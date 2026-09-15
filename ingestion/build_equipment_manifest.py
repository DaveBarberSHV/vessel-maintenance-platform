"""
Extracts every piece of equipment mentioned on the vessel's machinery and
general arrangement drawings, cross-references it against the vessel
equipment registry and the document library, and reports equipment that
appears on a drawing but has no corresponding manual ingested — a real
gap-finding tool, not a data-entry one.

Why this reads from tm_chunks, not the original PDFs (unlike
extract_equipment_list.py): these drawings are already ingested, mostly
via vision extraction (see vision_extraction.py) since a drawing's real
content — an equipment schedule table, deck-plan tags — is graphical, not
native text. Re-reading the same content from tm_chunks avoids a second,
redundant vision pass and works from any machine with just DB access, no
local PDF files required.

Which documents count as "machinery/general arrangement drawings" for
this tool — deliberately NOT document_type == 'General Arrangement
Drawing' alone. Confirmed directly against the real library (Sept 2026):
that doctype is used as a broad catch-all across this project for hull
structural drawings, stability calculations, and tonnage estimates too —
none of which show installed equipment, and including them would both
waste real API cost and dilute the extraction with irrelevant content
(the same dilution risk documented elsewhere in this project). Scoped
instead to the actual "GeneralArrangement" system category (the A01-A17
series — outboard profile, GA, machinery arrangement, safety/fire
equipment plans, navigation light arrangement...) plus any
"MachineryArrangement" / "EquipmentArrgt"-named drawing regardless of
which system folder it's filed under (e.g. Electrical's own equipment
arrangement drawing). Confirmed against the real library: matches exactly
13 real document/revision combinations, all genuinely equipment-adjacent —
adjust DRAWING_TITLE_PATTERN below if the library's naming conventions
change.

Both revisions of a drawing that has more than one (e.g. A03 has Rev1 and
RevP) are deliberately both processed rather than picking "the latest" —
simpler and safer than guessing a preliminary-vs-final ranking from
revision strings alone, and any duplicate equipment this produces
collapses naturally in the dedup step below.

Usage:
    export ANTHROPIC_API_KEY="..."
    export SUPABASE_DB_URL="..."
    python build_equipment_manifest.py [--out gap_report.md]
"""

import json
import re
import sys
from collections import defaultdict

import psycopg2.extras

sys.path.insert(0, ".")
from retrieval import get_pg_connection
from extract_equipment_list import get_equipment_list
from document_inventory import get_document_inventory

DRAWING_TITLE_PATTERN = re.compile(
    r"^GeneralArrangement - |MachineryArrangement|EquipmentArrgt",
    re.IGNORECASE,
)

# Same rules-first philosophy as extract_equipment_list.py's
# EXTRACTION_SYSTEM_PROMPT (only extract what's actually written, never
# guess) — but scoped to Dave's exact four fields for this tool, and
# written for drawing content specifically: an equipment schedule table
# (piece number / description / make-model) on one page, cross-referenced
# by piece number against tag callouts on a deck-plan/arrangement page
# elsewhere in the SAME document. Both are given together (the full
# document's text, all pages) specifically so Claude can do that
# cross-referencing itself — the same reasoning extract_equipment_list.py
# gives for using Claude over hand-written parsing: layouts vary, and
# understanding a piece number's real physical location by connecting two
# different pages is exactly the kind of thing worth asking Claude to do
# rather than encoding by hand.
EXTRACTION_SYSTEM_PROMPT = """You extract equipment mentioned on vessel machinery/general arrangement drawings.

The text you're given is the full transcribed content of one drawing (all pages), often including an equipment schedule table (columns like PIECE No. / QTY / DESCRIPTION / RATING / MAKE-MODEL / REMARKS) and separate deck-plan or arrangement pages that tag the same equipment by piece number at its physical location.

Return a JSON array of equipment entries. Each entry:
{
  "item_designation": "the equipment's piece/item number as shown (e.g. '500-01'), or its name/description if no number is given (e.g. 'MAIN ENGINE') — whatever the drawing itself uses to identify this specific item",
  "manufacturer": "as stated in a MAKE/MODEL column or similar — do not guess or infer one that isn't written, even if you recognize the part",
  "model": "as stated — do not guess",
  "system_location": "the system, compartment, or physical location this item belongs to, inferred from the drawing's own labeling (its DESCRIPTION/category column, or a deck-plan tag's location context if you can connect a piece number across pages) — only from what's actually shown, never invented"
}

Rules:
- Only extract what's actually written in the text. Never fill in a manufacturer, model, or location that isn't present, even if you recognize the equipment.
- One entry per distinct real piece of equipment — if the same item (by piece number) is mentioned on both the equipment schedule and a separate arrangement page, merge it into ONE entry rather than listing it twice.
- Skip pure structural/hull/tank labeling that isn't actual mechanical/electrical equipment (e.g. a fuel tank compartment label alone, with no piece number or make/model, is not equipment — but a piece of equipment installed in or near a tank, if identified by a real piece number or description, is).
- Return ONLY the JSON array, no other text, no markdown code fences."""


def fetch_drawing_chunks(conn) -> dict[tuple[str, str, str], list[dict]]:
    """Groups all chunk text from matched machinery/GA drawings by
    (document_title, source_file, revision), ordered by page number, so
    each document-revision's full content can be sent to Claude as one
    coherent unit."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT document_title, source_file, revision, page_number, text
            FROM tm_chunks
            ORDER BY document_title, page_number
        """)
        rows = [dict(r) for r in cur.fetchall()]

    by_doc = defaultdict(list)
    for r in rows:
        if DRAWING_TITLE_PATTERN.search(r["document_title"]):
            key = (r["document_title"], r["source_file"], r["revision"])
            by_doc[key].append(r)
    return dict(by_doc)


def extract_equipment_from_drawing(chunks: list[dict], api_key: str | None = None) -> list[dict]:
    """Sends one document-revision's full chunk text (all pages,
    concatenated in page order) to Claude for extraction. Same retry-on-
    malformed-JSON and max_tokens-truncation handling as
    extract_equipment_list.py's extract_equipment() — a real, proven
    pattern for this exact kind of Claude-JSON-extraction call, not
    re-invented here."""
    import os
    import anthropic

    text = "\n\n".join(
        f"--- Page {c['page_number']} ---\n{c['text']}" for c in chunks
    )

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("No ANTHROPIC_API_KEY available.")

    client = anthropic.Anthropic(api_key=key, timeout=120.0)

    MAX_ATTEMPTS = 3
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        if response.stop_reason == "max_tokens":
            raise ValueError(
                "Claude's response was cut off at max_tokens (8000) before "
                "finishing — this drawing has more equipment than this "
                "limit currently allows. Raise max_tokens rather than "
                "guess at the JSON parsing error this would otherwise "
                "produce."
            )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = e
            print(f"    Attempt {attempt}/{MAX_ATTEMPTS}: malformed JSON ({e})"
                  + (" — retrying." if attempt < MAX_ATTEMPTS else " — out of retries."))

    raise ValueError(
        f"Equipment extraction produced malformed JSON {MAX_ATTEMPTS} times "
        f"in a row (last error: {last_error})."
    )


def normalize(s: str | None) -> str:
    """Uppercase, alphanumeric-only — same pragmatic substring-matching
    approach already used elsewhere in this codebase (e.g.
    answer_query.py's find_matching_document_title()) rather than a
    fuzzy-matching library, since exact punctuation/spacing varies a lot
    between a drawing's own text and a document title or registry
    entry ('Cat. 3512E' vs 'CATERPILLAR 3512E' vs 'CAT 3512E')."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# Below this length, a normalized model string is too short/generic to
# trust for a substring match (e.g. a bare "10" or "A") — real risk of a
# false-positive "covered" match on an unrelated document. Treated as
# "not enough info to check" rather than either a confident match or a
# confident gap.
MIN_MATCH_LENGTH = 3


def find_registry_match(item: dict, registry: list[dict]) -> dict | None:
    model_norm = normalize(item.get("model"))
    if len(model_norm) < MIN_MATCH_LENGTH:
        return None
    for entry in registry:
        entry_model_norm = normalize(entry.get("model"))
        if entry_model_norm and (model_norm in entry_model_norm or entry_model_norm in model_norm):
            return entry
    return None


def find_document_match(item: dict, inventory: list[dict]) -> dict | None:
    model_norm = normalize(item.get("model"))
    if len(model_norm) < MIN_MATCH_LENGTH:
        return None
    for doc in inventory:
        haystack = normalize(doc.get("document_title")) + normalize(doc.get("equipment_model"))
        if model_norm in haystack:
            return doc
    return None


def dedupe_items(items: list[dict]) -> list[dict]:
    """Collapses equipment extracted redundantly across multiple
    document-revisions (e.g. A03's Rev1 and RevP both listing the same
    main engines) into one entry, keyed on (manufacturer, model) when
    both are present, falling back to (item_designation, source_document)
    when they're not — real equipment with no manufacturer/model on the
    drawing is still worth keeping, just can't be deduped across
    documents by identity the same way."""
    seen = {}
    for item in items:
        model_norm = normalize(item.get("model"))
        if model_norm and len(model_norm) >= MIN_MATCH_LENGTH:
            key = ("model", normalize(item.get("manufacturer")), model_norm)
        else:
            key = ("designation", item.get("source_document"), item.get("item_designation"))
        if key not in seen:
            seen[key] = item
        else:
            # Keep the first, but note every document this item showed up on.
            existing_docs = seen[key].setdefault("also_seen_on", [])
            if item.get("source_document") not in existing_docs and item.get("source_document") != seen[key].get("source_document"):
                existing_docs.append(item.get("source_document"))
    return list(seen.values())


def build_gap_report(items: list[dict], registry: list[dict], inventory: list[dict]) -> dict:
    covered, gaps, unknown = [], [], []
    for item in items:
        model_norm = normalize(item.get("model"))
        if len(model_norm) < MIN_MATCH_LENGTH:
            unknown.append(item)
            continue
        doc_match = find_document_match(item, inventory)
        reg_match = find_registry_match(item, registry)
        if doc_match:
            covered.append({**item, "matched_document": doc_match["document_title"]})
        else:
            gaps.append({**item, "in_registry": bool(reg_match)})
    return {"covered": covered, "gaps": gaps, "unknown": unknown}


def format_report(report: dict) -> str:
    lines = ["# Equipment Manifest — Gap Report", ""]

    lines.append(f"## Gaps — equipment on a drawing with no ingested manual ({len(report['gaps'])})")
    lines.append("")
    if not report["gaps"]:
        lines.append("None found — every equipment item with a stated model matched an ingested document.")
    for g in sorted(report["gaps"], key=lambda x: (x.get("manufacturer") or "", x.get("model") or "")):
        reg_note = " (in vessel_equipment registry)" if g.get("in_registry") else " (NOT in vessel_equipment registry either)"
        lines.append(f"- **{g.get('manufacturer') or '?'} {g.get('model') or '?'}** "
                     f"— {g.get('item_designation', '?')}, {g.get('system_location', 'location unknown')}"
                     f"{reg_note}")
        lines.append(f"  - Source: {g.get('source_document')}")
        if g.get("also_seen_on"):
            lines.append(f"  - Also seen on: {', '.join(g['also_seen_on'])}")
    lines.append("")

    lines.append(f"## Covered — matched to an ingested document ({len(report['covered'])})")
    lines.append("")
    for c in sorted(report["covered"], key=lambda x: (x.get("manufacturer") or "", x.get("model") or "")):
        lines.append(f"- {c.get('manufacturer') or ''} {c.get('model') or ''} "
                     f"({c.get('item_designation', '?')}) → {c['matched_document']}")
    lines.append("")

    lines.append(f"## No manufacturer/model on the drawing — not checkable ({len(report['unknown'])})")
    lines.append("")
    lines.append("Real equipment the drawing identifies (a piece number, description, or "
                  "location) but without a manufacturer/model stated on the drawing itself "
                  "— not a confirmed gap, just not enough information on THIS drawing to "
                  "check against the library. May already be covered by a document that "
                  "identifies it differently.")
    lines.append("")
    for u in sorted(report["unknown"], key=lambda x: x.get("item_designation") or ""):
        lines.append(f"- {u.get('item_designation', '?')} — {u.get('system_location', 'location unknown')} "
                     f"(source: {u.get('source_document')})")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    out_path = "equipment_manifest_gap_report.md"
    if "--out" in args:
        idx = args.index("--out")
        out_path = args[idx + 1]

    conn = get_pg_connection()
    by_doc = fetch_drawing_chunks(conn)
    print(f"Found {len(by_doc)} machinery/general arrangement drawing document-revision(s) to scan.")

    all_items = []
    for (title, source_file, revision), chunks in by_doc.items():
        print(f"  Extracting from {title} ({revision}, {len(chunks)} chunk(s))...")
        try:
            items = extract_equipment_from_drawing(chunks)
        except ValueError as e:
            print(f"    Skipped — {e}")
            continue
        for item in items:
            item["source_document"] = title
            item["source_revision"] = revision
        all_items.extend(items)
        print(f"    {len(items)} equipment item(s) found.")

    print(f"\n{len(all_items)} raw equipment mentions across all drawings — deduping...")
    deduped = dedupe_items(all_items)
    print(f"{len(deduped)} distinct equipment item(s) after dedup.")

    registry = get_equipment_list(conn)
    inventory = get_document_inventory(conn)
    conn.close()

    report = build_gap_report(deduped, registry, inventory)
    text = format_report(report)
    print(f"\n{'=' * 70}\n")
    print(text)

    with open(out_path, "w") as f:
        f.write(text)
    print(f"\nFull report written to {out_path}")


if __name__ == "__main__":
    main()
