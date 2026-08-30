"""
Incremental folder ingestion — scans a folder of TMs and processes only
what's new or changed since the last run.

Why this matters: without this, adding one new TM to a large library
would mean re-parsing and re-embedding all of them every time — wasted
time, and wasted Voyage API cost, for files that didn't change. This
script keeps a manifest (manifest.json) recording a hash of each file it
has already processed, so:
  - Unchanged files: skipped entirely, instantly.
  - New files: parsed, chunked, and embedded — only these hit the Voyage API.
  - Changed files (e.g. a TM replaced with a new revision): the OLD chunks
    for that file are removed (from chunks.jsonl and from the Postgres
    index) before the new version is processed, so nothing stale lingers.

Storage: Supabase Postgres + pgvector (migrated Aug 2026 from local
Chroma, which had to be committed to git for the deployed app to reach
it — see BACKLOG.md for why that stopped scaling).

Relies on the naming convention (see docs/naming_convention discussion):
    [System]_[Manufacturer]_[Model]_[DocType]_Rev[X].pdf
Example:
    Clutch_BergPropulsion_MCH6_OMM_RevA.pdf

Files that don't match this pattern are reported and skipped, rather than
guessed at — a wrong metadata guess is worse than a file that needs a
quick rename.

Usage:
    python scan_folder.py /path/to/your/Drive/Drivetrain-TMs
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pdfplumber

from parse_and_chunk import chunk_document
from retrieval import (VoyageEmbedder, get_voyage_key, get_pg_connection,
                        ensure_pg_schema, upsert_chunks, delete_chunks)

MANIFEST_PATH = Path(__file__).parent / "manifest.json"
CHUNKS_PATH = Path(__file__).parent / "chunks.jsonl"

# Single-vessel prototype — same for every file. If a second vessel is
# ever added to this library, this needs to become per-file (e.g. a
# subfolder per vessel), not a single constant. See BACKLOG.md.
VESSEL = "Master Boat Builders Hull 469 (tug+barge combo)"
ORDER_NO = "7396A1"

FILENAME_PATTERN = re.compile(
    r"^(?P<system>[A-Za-z0-9]+)_(?P<manufacturer>[A-Za-z0-9]+)_(?P<model>[A-Za-z0-9]+)_"
    r"(?P<doctype>[A-Za-z0-9]+)_(?P<revision>Rev[A-Za-z0-9]+)\.pdf$",
    re.IGNORECASE,
)

DOCTYPE_LABELS = {
    "OMM": "O&M Manual",
    "DWG": "General Arrangement Drawing",
    "PARTSLIST": "Parts List",
    "SERVICEBULLETIN": "Service Bulletin",
    "WIRINGDIAGRAM": "Wiring Diagram",
    "REFDATA": "Reference Data",
    "EQUIPMENTLIST": "Equipment List",  # triggers automatic vessel_equipment
    # extraction too, in addition to normal chunking — see extract_equipment_list.py
}


def validate_pdf(path: Path) -> tuple[bool, str | None]:
    """Sanity-check a file before processing. Returns (is_valid, issue).
    A file that fails this should be reported clearly and skipped —
    never guessed at or force-processed, since a corrupted or
    misidentified file poisoning the index is worse than one missing TM."""
    if path.stat().st_size == 0:
        return False, "File is empty (0 bytes)"

    if path.stat().st_size < 1024:
        return False, "File is suspiciously small (<1KB) — likely not a real PDF"

    try:
        with pdfplumber.open(path) as pdf:
            num_pages = len(pdf.pages)
            if num_pages == 0:
                return False, "PDF opens but has 0 pages"

            # Sample first few pages to check for a text layer at all.
            # Zero text across a whole manual usually means a scanned
            # document with no OCR — not corrupted, but won't be
            # searchable beyond metadata. Worth flagging, not blocking.
            sample_pages = pdf.pages[:min(3, num_pages)]
            total_text = sum(len((p.extract_text() or "").strip()) for p in sample_pages)
            if total_text == 0 and num_pages > 1:
                return True, ("WARNING: no extractable text found in first pages — "
                              "likely a scanned document with no text layer. "
                              "Will be indexed as metadata-only, not full-text searchable.")

    except Exception as e:
        # Covers corrupted files, password-protected PDFs, and anything
        # else that makes the file unreadable as a PDF at all.
        return False, f"Could not open as a valid PDF ({type(e).__name__}: {e})"

    return True, None


def parse_filename(filename: str) -> dict | None:
    """Extract metadata from a filename following the naming convention.
    Returns None if the filename doesn't match — such files are reported,
    not guessed at."""
    m = FILENAME_PATTERN.match(filename)
    if not m:
        return None
    doctype_raw = m.group("doctype").upper()
    return {
        "vessel": VESSEL,
        "order_no": ORDER_NO,
        "equipment_model": f"{m.group('manufacturer')} {m.group('model')}",
        "document_type": DOCTYPE_LABELS.get(doctype_raw, doctype_raw),
        "document_title": f"{m.group('system')} - {m.group('manufacturer')} {m.group('model')} "
                           f"{DOCTYPE_LABELS.get(doctype_raw, doctype_raw)}",
        "revision": m.group("revision"),
    }


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict):
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def load_chunks() -> list[dict]:
    if not CHUNKS_PATH.exists():
        return []
    with open(CHUNKS_PATH) as f:
        return [json.loads(line) for line in f]


def save_chunks(chunks: list[dict]):
    with open(CHUNKS_PATH, "w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")


def get_embedder(engine: str):
    if engine == "voyage":
        return VoyageEmbedder(get_voyage_key(), input_type="document")
    elif engine == "tfidf":
        sys.exit("tfidf engine requires a fitted vectorizer over the full corpus — "
                 "run 'python retrieval.py build --engine tfidf' for a full rebuild instead. "
                 "Incremental scanning is only supported for the voyage engine.")
    else:
        sys.exit(f"Unknown engine '{engine}'.")


def scan_folder(folder: Path, engine: str = "voyage"):
    manifest = load_manifest()
    chunks = load_chunks()
    chunks_by_file = {}
    for c in chunks:
        chunks_by_file.setdefault(c["source_file"], []).append(c)

    pdf_files = sorted(folder.rglob("*.pdf"))
    if not pdf_files:
        sys.exit(f"No PDF files found in {folder}")

    unmatched = []
    unchanged = []
    invalid = []       # (filename, issue) — hard failures, never processed
    warnings = []      # (filename, warning) — processed anyway, but flagged
    to_process = []  # (path, metadata, is_update, renamed_from, current_hash)

    # Reverse lookup: content hash -> filename it was last seen under. Used
    # to detect renames (same content, new filename) rather than treating
    # a renamed file as brand new and leaving its old entry orphaned — see
    # BACKLOG.md for the real duplication bug this caused before this fix.
    hash_to_prior_filename = {entry["hash"]: fname for fname, entry in manifest.items()}

    for path in pdf_files:
        metadata = parse_filename(path.name)
        if metadata is None:
            unmatched.append(path.name)
            continue

        is_valid, issue = validate_pdf(path)
        if not is_valid:
            invalid.append((path.name, issue))
            continue
        if issue:  # valid but with a warning (e.g. no text layer)
            warnings.append((path.name, issue))

        current_hash = file_hash(path)
        prior = manifest.get(path.name)

        if prior and prior["hash"] == current_hash:
            unchanged.append(path.name)
            continue

        renamed_from = None
        if prior is None:
            candidate = hash_to_prior_filename.get(current_hash)
            if candidate and candidate != path.name:
                renamed_from = candidate

        to_process.append((path, metadata, prior is not None, renamed_from, current_hash))

    renamed_count = sum(1 for _, _, _, rf, _ in to_process if rf)

    print(f"Scanned {len(pdf_files)} PDF(s) in {folder}")
    print(f"  {len(unchanged)} unchanged — skipped")
    print(f"  {len(to_process)} new or changed — will process"
          + (f" ({renamed_count} of these are renames of already-indexed files)" if renamed_count else ""))
    if unmatched:
        print(f"\n  {len(unmatched)} did NOT match naming convention — skipped, not guessed at:")
        for name in unmatched:
            print(f"    - {name}")
    if invalid:
        print(f"\n  {len(invalid)} FAILED validation — skipped, need attention:")
        for name, issue in invalid:
            print(f"    - {name}: {issue}")
    if warnings:
        print(f"\n  {len(warnings)} processed with a warning — check these:")
        for name, warning in warnings:
            print(f"    - {name}: {warning}")

    if not to_process:
        print("\nNothing to do.")
        return

    # Postgres connection for incremental add/delete — migrated Aug 2026
    # from local Chroma, which had to be committed to git for the deployed
    # app to reach it; that stopgap strained badly as the library grew.
    # See BACKLOG.md.
    embedder = get_embedder(engine)
    conn = get_pg_connection()
    ensure_pg_schema(conn)

    # Page images (Aug 2026) — optional: Storage credentials are separate
    # from the database connection above (SUPABASE_URL/SUPABASE_SERVICE_KEY,
    # not SUPABASE_DB_URL), and not everyone running this script will have
    # them set up yet. Rather than make this a hard requirement, it's
    # silently skipped if unavailable — text/embedding ingestion (the core
    # feature) keeps working exactly as before either way. See page_images.py.
    images_enabled = False
    try:
        import page_images
        page_images.get_supabase_storage_config()  # just checks the env vars are set, doesn't call the network
        page_images.ensure_storage_bucket()
        images_enabled = True
    except ValueError:
        print("Note: SUPABASE_URL/SUPABASE_SERVICE_KEY not set — skipping page image "
              "rendering this run (text/embedding ingestion is unaffected). See "
              "docs/architecture.md if you want to enable page images.\n")
    except Exception as e:
        print(f"Note: page image storage isn't reachable right now ({e}) — skipping "
              f"page image rendering this run (text/embedding ingestion is unaffected).\n")

    # Vessel equipment registry (Aug 2026) — optional: needs
    # ANTHROPIC_API_KEY in addition to what's already required above. Any
    # file with doctype EquipmentList gets its content extracted into the
    # vessel_equipment table automatically, in addition to normal chunking
    # (so it's also searchable as a regular TM chunk, same as everything
    # else). See extract_equipment_list.py and BACKLOG.md.
    equipment_enabled = False
    try:
        import extract_equipment_list
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY not set")
        extract_equipment_list.ensure_equipment_schema(conn)
        equipment_enabled = True
    except Exception as e:
        print(f"Note: vessel equipment extraction isn't available right now ({e}) — "
              f"skipping for any EquipmentList documents this run (normal chunking is "
              f"unaffected). See docs/architecture.md.\n")

    # Vision extraction for image-only pages (Aug 2026) — optional: needs
    # ANTHROPIC_API_KEY, same as equipment extraction above. Real
    # motivating case: wiring diagrams and dense drawings often have NO
    # text layer at all, so their content was previously invisible to
    # search entirely — an engineer asking about a labeled component on a
    # drawing got nothing back, even though the label is right there on
    # the page. See vision_extraction.py and BACKLOG.md for the full
    # two-tier reasoning (this only ever transcribes visible text, never
    # interprets arrows/symbols/relationships).
    vision_enabled = False
    try:
        import page_images  # noqa: F811 — render_page_image() doesn't need
        # Storage credentials, only the upload step does; importing here
        # independently of images_enabled above ensures this works even
        # if only ANTHROPIC_API_KEY is set and Storage isn't configured.
        import vision_extraction
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY not set")
        vision_enabled = True
    except Exception as e:
        print(f"Note: vision extraction for image-only pages isn't available right now "
              f"({e}) — those pages will remain metadata-only, not full-text searchable, "
              f"this run. See BACKLOG.md.\n")

    new_chunk_count = 0
    failed_during_processing = []

    for path, metadata, is_update, renamed_from, current_hash in to_process:
        if renamed_from:
            action = f"Renamed (was '{renamed_from}') -> "
        elif is_update:
            action = "Updating"
        else:
            action = "Adding"
        print(f"\n{action}: {path.name}")

        try:
            # Clean up old chunks first — either because this filename was
            # seen before with different content (is_update), or because
            # this content was seen before under a different filename
            # (renamed_from). Either way, the OLD chunk_ids need removing
            # from Chroma and chunks.jsonl, and the OLD manifest key needs
            # removing so it doesn't linger as a stale, orphaned entry.
            old_key = renamed_from if renamed_from else (path.name if is_update else None)
            if old_key and old_key in manifest:
                old_chunk_ids = manifest[old_key]["chunk_ids"]
                chunks = [c for c in chunks if c["chunk_id"] not in old_chunk_ids]
                try:
                    delete_chunks(conn, old_chunk_ids)
                except Exception:
                    pass  # ids may not all exist if index was rebuilt separately; safe to ignore
                reason = "renamed from" if renamed_from else "superseded revision of"
                print(f"  Removed {len(old_chunk_ids)} old chunks ({reason} '{old_key}')")
                if renamed_from:
                    del manifest[old_key]

            file_chunks = chunk_document(path, metadata)
            # Vision-candidate threshold, not just "zero text" (Aug 2026,
            # real gap found via a real production run): a page that's
            # almost entirely a drawing/image can still have a handful of
            # genuinely real, selectable text characters on it — a title
            # block, a drawing number, a small label — which made it
            # count as "has a text layer" under the original all-or-
            # nothing rule, silently skipping vision extraction and
            # leaving the actual dense drawing content never read at all.
            # Confirmed directly: two real DWG files with known dense
            # drawing content produced zero "trying vision extraction"
            # output during a real ingestion run. A character-count
            # threshold catches this — genuine manual text pages have far
            # more than this; a bare title block doesn't. Deliberately
            # generous (not just "more than a few words") since it's far
            # better to run one unnecessary vision call on a border-case
            # page than to silently under-detect a real drawing again.
            VISION_CANDIDATE_CHAR_THRESHOLD = 200
            text_chunks = [c for c in file_chunks if len(c.text.strip()) >= VISION_CANDIDATE_CHAR_THRESHOLD]
            no_text_chunks = [c for c in file_chunks if c not in text_chunks]

            # Vision extraction for pages with no text layer (Aug 2026) —
            # see setup note above. Each candidate page gets its image
            # rendered (same function page_images.py already uses, but at
            # a higher resolution — see render_page_image()'s docstring)
            # and sent to Claude's vision API; if real text comes back,
            # that page is promoted into text_chunks and flows through
            # the exact same embed/store pipeline as every other page —
            # no parallel system. A page vision genuinely can't read
            # anything on (blank separator pages, etc.) correctly stays
            # metadata-only, same as before.
            #
            # Grouped by page_number, not iterated per-chunk (Aug 2026,
            # real bug fix): a single physical page can have MORE than
            # one chunk — its main page-level chunk plus one or more
            # dense-table sub-chunks (see split_dense_tables() above), all
            # sharing the same page_number. Iterating per-chunk meant a
            # sparse page with, say, two dense-table sub-chunks triggered
            # THREE separate, redundant vision calls (and three identical
            # renders) for the exact same image — confirmed directly via
            # real production output showing the same page's "large-
            # format" resolution note printed multiple times for what was
            # actually one physical page. Grouping first means exactly
            # one render and one vision call per distinct page, with the
            # same transcribed result applied to every chunk on that page.
            pages_needing_vision = {}
            for c in no_text_chunks:
                pages_needing_vision.setdefault(c.page_number, []).append(c)

            vision_extracted_count = 0
            if vision_enabled and pages_needing_vision:
                print(f"  {len(pages_needing_vision)} page(s) with no text layer — "
                      f"trying vision extraction...")
                for page_number, chunks_for_page in pages_needing_vision.items():
                    try:
                        # Higher resolution than the citation-display
                        # default (Aug 2026, see page_images.py) — this
                        # image is only used transiently for the vision
                        # call, never stored, so there's no cost to
                        # rendering it sharper. Confirmed necessary by a
                        # real test: a dense rotation-direction table was
                        # genuinely unreadable at the default resolution.
                        image_bytes = page_images.render_page_image(
                            path, page_number, resolution=300)
                        transcribed = vision_extraction.extract_text_from_image(image_bytes)
                        if transcribed:
                            formatted = vision_extraction.format_vision_chunk_text(transcribed)
                            for c in chunks_for_page:
                                c.text = formatted
                                c.has_text_layer = True
                                text_chunks.append(c)
                            vision_extracted_count += 1
                    except Exception as e:
                        print(f"    WARNING: vision extraction failed for page "
                              f"{page_number} ({type(e).__name__}: {e}) — this page "
                              f"remains metadata-only.")
                if vision_extracted_count:
                    print(f"  {vision_extracted_count} of {len(pages_needing_vision)} "
                          f"page(s) successfully transcribed via vision.")

            if text_chunks:
                # Compute embeddings ourselves first (rather than inside
                # the upsert) so we can filter out any
                # chunk VoyageEmbedder flags as too large to embed at all
                # (see its MAX_SINGLE_CHUNK_CHARS) BEFORE storing anything
                # — added Aug 2026 after a real oversized chunk broke a
                # whole file's ingestion. See BACKLOG.md.
                texts = [c.text for c in text_chunks]
                embeddings = embedder(texts)
                oversized = set(getattr(embedder, "oversized_indices", []))

                keep = [i for i in range(len(text_chunks)) if i not in oversized]

                # Page images (Aug 2026) — see setup note above and
                # page_images.py for the selection logic and why this
                # happens here (real PDF bytes are only available at
                # ingestion time, on this machine).
                page_image_urls = {}
                if images_enabled:
                    pages_with_text = {c.page_number for c in text_chunks}
                    total_pages = file_chunks[0].total_pages if file_chunks else 0
                    page_image_urls = page_images.render_and_upload_selected_pages(
                        path, path.name, pages_with_text, total_pages)
                    if page_image_urls:
                        print(f"  {len(page_image_urls)} page image(s) rendered and uploaded.")

                keep_chunks = [
                    {
                        "chunk_id": text_chunks[i].chunk_id,
                        "text": text_chunks[i].text,
                        "document_title": text_chunks[i].document_title,
                        "revision": text_chunks[i].revision,
                        "page_number": text_chunks[i].page_number,
                        "total_pages": text_chunks[i].total_pages,
                        "equipment_model": text_chunks[i].equipment_model,
                        "document_type": text_chunks[i].document_type,
                        "source_file": text_chunks[i].source_file,
                        "page_image_url": page_image_urls.get(text_chunks[i].page_number),
                    }
                    for i in keep
                ]
                upsert_chunks(conn, keep_chunks, [embeddings[i] for i in keep])
                if oversized:
                    pages = sorted({text_chunks[i].page_number for i in oversized})
                    print(f"  WARNING: {len(oversized)} chunk(s) too large to embed "
                          f"(page(s) {pages}) — NOT added to the index. This usually "
                          f"means a page's text didn't split as expected; worth a "
                          f"look if that page's content matters for search.")

            new_chunks = [asdict(c) for c in file_chunks]
            chunks.extend(new_chunks)
            new_chunk_count += len(file_chunks)

            manifest[path.name] = {
                "hash": current_hash,
                "chunk_ids": [c.chunk_id for c in file_chunks],
            }
            print(f"  {len(file_chunks)} chunks ({len(text_chunks)} embedded, "
                  f"{len(file_chunks) - len(text_chunks)} metadata-only — no text layer)")

            # Vessel equipment registry (Aug 2026) — in addition to normal
            # chunking above, an EquipmentList document also gets its
            # content extracted into the vessel_equipment table. See setup
            # note near the top of this function and extract_equipment_list.py.
            if equipment_enabled and metadata["document_type"] == "Equipment List":
                try:
                    entries = extract_equipment_list.extract_equipment(path)
                    extract_equipment_list.upsert_equipment(conn, entries, path.name)
                    print(f"  {len(entries)} vessel equipment entries extracted and upserted.")
                except Exception as e:
                    print(f"  WARNING: equipment extraction failed for this file "
                          f"({type(e).__name__}: {e}) — normal chunking above still "
                          f"succeeded, only the structured equipment registry update failed.")

        except Exception as e:
            # One bad file should never take down the whole batch. Report
            # it clearly and keep going — better to process 19 of 20 TMs
            # and flag the 1 problem than to fail silently or abort everything.
            print(f"  FAILED: {type(e).__name__}: {e}")
            failed_during_processing.append((path.name, str(e)))
            continue

    save_chunks(chunks)
    save_manifest(manifest)
    conn.close()
    print(f"\nDone. {new_chunk_count} chunks added/updated across "
          f"{len(to_process) - len(failed_during_processing)} file(s). "
          f"{len(unchanged)} file(s) untouched.")

    if failed_during_processing:
        print(f"\n{len(failed_during_processing)} file(s) FAILED during processing "
              f"(not added to the index):")
        for name, err in failed_during_processing:
            print(f"  - {name}: {err}")

    total_issues = len(unmatched) + len(invalid) + len(failed_during_processing)
    if total_issues:
        print(f"\n{total_issues} file(s) need your attention — see above. "
              f"Nothing else in the library was affected.")


if __name__ == "__main__":
    args = sys.argv[1:]
    engine = "voyage"
    if "--engine" in args:
        idx = args.index("--engine")
        engine = args[idx + 1]
        del args[idx:idx + 2]
    if not args:
        sys.exit("Usage: python scan_folder.py /path/to/TM/folder [--engine voyage|tfidf]")
    try:
        scan_folder(Path(args[0]), engine=engine)
    except ValueError as e:
        # get_voyage_key() (via get_embedder()) now raises instead of
        # sys.exit() — see retrieval.py for why. This preserves the
        # original clean one-line CLI error behavior here too.
        sys.exit(str(e))
