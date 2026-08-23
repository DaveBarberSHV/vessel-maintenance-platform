"""
Incremental folder ingestion — scans a folder of TMs and processes only
what's new or changed since the last run.

Why this matters: without this, adding one new TM to a 20-file library
would mean re-parsing and re-embedding all 20 every time — wasted time,
and wasted Voyage API cost, for files that didn't change. This script
keeps a manifest (manifest.json) recording a hash of each file it has
already processed, so:
  - Unchanged files: skipped entirely, instantly.
  - New files: parsed, chunked, and embedded — only these hit the Voyage API.
  - Changed files (e.g. a TM replaced with a new revision): the OLD chunks
    for that file are removed (from chunks.jsonl and from the Chroma
    index) before the new version is processed, so nothing stale lingers.

Relies on the naming convention (see docs/naming_convention discussion):
    [System]_[Manufacturer]_[Model]_[DocType]_Rev[X].pdf
Example:
    Clutch_BergPropulsion_MCH6_OMM_RevA.pdf

Files that don't match this pattern are reported and skipped, rather than
guessed at — a wrong metadata guess is worse than a file that needs a
quick rename.

Usage:
    python scan_folder.py /path/to/your/Drive/Drivetrain-TMs
    python scan_folder.py /path/to/folder --engine tfidf   # for offline testing
"""

import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pdfplumber

from parse_and_chunk import chunk_document
from retrieval import TfidfEmbedder, VoyageEmbedder, db_path, get_voyage_key, COLLECTION_NAME

import chromadb

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

    # Set up Chroma collection for incremental add (and delete, for updates)
    path_db = db_path(engine)
    path_db.mkdir(parents=True, exist_ok=True)
    embedder = get_embedder(engine)
    client = chromadb.PersistentClient(path=str(path_db))
    collection = client.get_or_create_collection(COLLECTION_NAME, embedding_function=embedder)

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
                    collection.delete(ids=old_chunk_ids)
                except Exception:
                    pass  # ids may not all exist if index was rebuilt separately; safe to ignore
                reason = "renamed from" if renamed_from else "superseded revision of"
                print(f"  Removed {len(old_chunk_ids)} old chunks ({reason} '{old_key}')")
                if renamed_from:
                    del manifest[old_key]

            file_chunks = chunk_document(path, metadata)
            text_chunks = [c for c in file_chunks if c.has_text_layer and c.text.strip()]

            if text_chunks:
                collection.add(
                    ids=[c.chunk_id for c in text_chunks],
                    documents=[c.text for c in text_chunks],
                    metadatas=[{
                        "document_title": c.document_title,
                        "revision": c.revision,
                        "page_number": c.page_number,
                        "equipment_model": c.equipment_model,
                        "document_type": c.document_type,
                        "source_file": c.source_file,
                    } for c in text_chunks],
                )

            new_chunks = [asdict(c) for c in file_chunks]
            chunks.extend(new_chunks)
            new_chunk_count += len(file_chunks)

            manifest[path.name] = {
                "hash": current_hash,
                "chunk_ids": [c.chunk_id for c in file_chunks],
            }
            print(f"  {len(file_chunks)} chunks ({len(text_chunks)} embedded, "
                  f"{len(file_chunks) - len(text_chunks)} metadata-only — no text layer)")

        except Exception as e:
            # One bad file should never take down the whole batch. Report
            # it clearly and keep going — better to process 19 of 20 TMs
            # and flag the 1 problem than to fail silently or abort everything.
            print(f"  FAILED: {type(e).__name__}: {e}")
            failed_during_processing.append((path.name, str(e)))
            continue

    save_chunks(chunks)
    save_manifest(manifest)
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
    scan_folder(Path(args[0]), engine=engine)
