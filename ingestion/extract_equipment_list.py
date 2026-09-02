"""
Extracts a structured vessel equipment registry from a reference document
(e.g. an equipment list PDF) and upserts it into Postgres.

Why this exists: TMs are often written per equipment *model*, but a real
question ("what's the grease spec for the bearings?") doesn't usually name
the model — the asker assumes the system already knows what's actually
installed on this vessel, the same way a real engineer would. This table
is the vessel's own "what's actually here" registry, kept separate from
the TM chunks themselves, and injected into every question's context (see
answer_query.py's build_prompt()) so retrieval doesn't have to happen to
stumble onto the equipment list document to know what model applies.

Why extraction uses Claude rather than hand-written parsing: equipment
list layouts vary — this document alone abbreviates "Port Main Engine"
as "PME", mixes columns per category, and isn't a clean table. A
different vessel's list could look completely different. Parsing logic
built for this exact layout would break immediately on the next one;
asking Claude to read and structure it is the same kind of understanding
it already applies to answering questions.

Design note on updates: equipment gets replaced (e.g. during shipyard
work) — see BACKLOG.md. The (category, position) pair is the natural
identity for each row, so re-running this against an updated document
UPDATES existing entries rather than creating duplicates or leaving
stale ones behind.

Usage:
    export ANTHROPIC_API_KEY="..."
    export SUPABASE_DB_URL="..."
    python extract_equipment_list.py "/path/to/EquipmentList.pdf" \
        --source-document "Drivetrain_Vessel_EquipmentList_RefData_Rev1.pdf"
"""

import json
import sys
from pathlib import Path

import pdfplumber
import psycopg2.extras

sys.path.insert(0, ".")
from retrieval import get_pg_connection

EXTRACTION_SYSTEM_PROMPT = """You extract structured equipment data from vessel reference documents.

Read the provided text and return a JSON array of equipment entries. Each entry:
{
  "system": "the broader vessel system this equipment belongs to, e.g. 'Drivetrain', 'Fire \
Suppression', 'HVAC', 'Electrical', 'Deck Equipment' — infer this from section headers, \
groupings, or context within the document itself. A single document may cover MULTIPLE \
systems (e.g. a vessel-wide equipment list covering drivetrain, fire pumps, air compressors, \
a crane, and water pumps together) — identify each item's own real system individually, \
never default every item in the document to one system just because that's what the \
document's own title/filename suggests.",
  "category": "short category name, e.g. 'Main Engine', 'Azimuth Drive', 'Wheel/Propeller', 'Marine Clutch/Steering', 'Driveline Bearing'",
  "position": "Port" or "Starboard" or null if not applicable (normalize abbreviations \
like PME/SME to Port/Starboard — PME = Port Main Engine, SME = Starboard Main Engine),
  "manufacturer": "if stated in the text — do not guess or infer one that isn't written there",
  "model": "the model designation as written",
  "serial_number": "if present, else null",
  "specs": {"any other relevant fields as key-value pairs, e.g. hp, rpm, quantity, pitch — \
whatever is actually present for that category, using short lowercase snake_case keys"},
  "notes": "any other free-text detail that doesn't fit the fields above, else null"
}

Rules:
- Only extract what's actually written. Never fill in a manufacturer, model, or spec that
  isn't present in the text, even if you recognize the part and know the real manufacturer.
- One entry per distinct piece of equipment (e.g. Port and Starboard engines are two entries).
- Return ONLY the JSON array, no other text, no markdown code fences."""


def extract_equipment(pdf_path: Path, api_key: str = None) -> list[dict]:
    import os
    import anthropic

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("No ANTHROPIC_API_KEY available.")

    client = anthropic.Anthropic(api_key=key)

    # Retry on malformed JSON (Sept 2026, real bug found via two live
    # ingestion runs) — the first failure looked like a max_tokens
    # truncation (a real, separate, now-fixed bug below), but a second
    # failure at nearly the same character position, with the SAME
    # file, on a run that reported stop_reason="end_turn" (a normal,
    # complete response, not a truncation), proved this is a genuine,
    # intermittent JSON-formatting slip by the model — not a fixed
    # structural problem with one specific piece of content. Confirmed
    # directly: re-running the identical extraction with zero changes
    # succeeded cleanly. A retry is the correct, evidence-based fix for
    # an intermittent failure — not further guessing at which character
    # might be "the" problem, since there isn't one fixed problem
    # character to find.
    MAX_ATTEMPTS = 3
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            # Raised from 4000 (Sept 2026, real bug found via a real, live
            # ingestion run) — the original limit was sized for the old
            # ~10-item drivetrain-only equipment list. The first real
            # vessel-wide list (30+ items: generators, JAK system, nine
            # separate pumps, fuel oil, potable water, the crane...) hit
            # this limit exactly, silently truncating the JSON mid-string
            # and producing a cryptic JSONDecodeError ("Unterminated
            # string...") rather than a clear "ran out of room" error.
            # Raised generously, not just to the exact size that would
            # have covered this one list, since equipment lists will
            # likely keep growing as more systems get documented.
            max_tokens=16000,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        if response.stop_reason == "max_tokens":
            raise ValueError(
                "Claude's response was cut off because it hit the max_tokens "
                "limit before finishing — the equipment list is larger than "
                "this limit currently allows. Raise max_tokens in "
                "extract_equipment() further (currently 16000) rather than "
                "guess at the JSON parsing error this would otherwise "
                "produce."
            )
        raw = response.content[0].text.strip()
        # Defensive: strip markdown fences if the model adds them despite instructions not to
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = e
            print(f"    Attempt {attempt}/{MAX_ATTEMPTS}: malformed JSON "
                  f"({e}) — retrying." if attempt < MAX_ATTEMPTS else
                  f"    Attempt {attempt}/{MAX_ATTEMPTS}: malformed JSON "
                  f"({e}) — out of retries.")

    raise ValueError(
        f"Equipment extraction produced malformed JSON {MAX_ATTEMPTS} times "
        f"in a row (last error: {last_error}) — this goes beyond the normal "
        f"occasional intermittent failure this retry logic already covers; "
        f"worth a real look rather than retrying again."
    )


def ensure_equipment_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vessel_equipment (
                id BIGSERIAL PRIMARY KEY,
                system TEXT NOT NULL DEFAULT 'Drivetrain',
                category TEXT NOT NULL,
                position TEXT,
                manufacturer TEXT,
                model TEXT,
                serial_number TEXT,
                specs JSONB,
                notes TEXT,
                source_document TEXT,
                source_page INT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        # Real migration for anyone re-running this against an existing
        # database created before this column existed (Sept 2026) —
        # CREATE TABLE IF NOT EXISTS alone won't add a column to an
        # already-existing table.
        cur.execute("""
            ALTER TABLE vessel_equipment
                ADD COLUMN IF NOT EXISTS system TEXT NOT NULL DEFAULT 'Drivetrain';
        """)
        # Replace the old (category, position)-only uniqueness with the
        # real, three-part identity — safe to run repeatedly.
        cur.execute("""
            ALTER TABLE vessel_equipment DROP CONSTRAINT IF EXISTS vessel_equipment_category_position_key;
        """)
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'vessel_equipment_system_category_position_key'
                ) THEN
                    ALTER TABLE vessel_equipment
                        ADD CONSTRAINT vessel_equipment_system_category_position_key
                        UNIQUE (system, category, position);
                END IF;
            END $$;
        """)
        # RLS enabled directly here, not left as a separate manual step
        # (Sept 2026, real incident) — see auth.py's ensure_users_schema()
        # docstring for the full explanation. Safe to call repeatedly.
        cur.execute("ALTER TABLE vessel_equipment ENABLE ROW LEVEL SECURITY;")
    conn.commit()


def upsert_equipment(conn, entries: list[dict], source_document: str):
    rows = [
        (
            e.get("system") or "Drivetrain", e["category"], e.get("position"),
            e.get("manufacturer"), e.get("model"), e.get("serial_number"),
            json.dumps(e.get("specs") or {}), e.get("notes"), source_document,
        )
        for e in entries
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO vessel_equipment
                (system, category, position, manufacturer, model, serial_number, specs, notes, source_document)
            VALUES %s
            ON CONFLICT (system, category, position) DO UPDATE SET
                manufacturer = EXCLUDED.manufacturer,
                model = EXCLUDED.model,
                serial_number = EXCLUDED.serial_number,
                specs = EXCLUDED.specs,
                notes = EXCLUDED.notes,
                source_document = EXCLUDED.source_document,
                updated_at = now()
            """,
            rows,
            # 9 placeholders, matching the 9-value row tuple above
            # exactly. Real bug fixed here (Sept 2026, found via a real
            # live ingestion run): this template only had 8 placeholders
            # after `system` was added as a new first column — silently
            # misaligning every row's values against the wrong columns
            # and producing a confusing, unrelated-looking "no unique
            # constraint matching ON CONFLICT" error.
            template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
        )
    conn.commit()


def get_equipment_list(conn) -> list[dict]:
    """Used by answer_query.py — returns the full current registry, always
    included in every question's context (not dependent on retrieval
    happening to find it). Returns [] rather than raising if the table
    doesn't exist yet (e.g. extraction has never been run) — the app
    should degrade to "no equipment context" gracefully, not crash every
    question just because this feature hasn't been set up yet."""
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT system, category, position, manufacturer, model, serial_number, specs, notes
                FROM vessel_equipment
                ORDER BY system, category, position
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def format_equipment_list(entries: list[dict]) -> str:
    """Formats the registry as compact text for a prompt. Grouped by
    system (Sept 2026, real trigger — see BACKLOG.md's equipment
    dropdown scaling entry) rather than one flat list, so the injected
    context stays scannable as more systems beyond drivetrain get
    ingested."""
    if not entries:
        return ""
    lines = ["Vessel equipment currently installed (use this to know which "
             "model/variant applies when a TM covers multiple options):"]
    last_system = None
    for e in entries:
        if e["system"] != last_system:
            lines.append(f"\n{e['system']}:")
            last_system = e["system"]
        parts = [e["category"]]
        if e.get("position"):
            parts.append(e["position"])
        detail = f"{e.get('manufacturer') or ''} {e.get('model') or ''}".strip()
        if detail:
            parts.append(f"— {detail}")
        if e.get("serial_number"):
            parts.append(f"(S/N {e['serial_number']})")
        lines.append("- " + " ".join(parts))
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit('Usage: python extract_equipment_list.py "/path/to/file.pdf" '
                  '[--source-document "name.pdf"]')
    pdf_path = Path(args[0])
    source_document = pdf_path.name
    if "--source-document" in args:
        idx = args.index("--source-document")
        source_document = args[idx + 1]

    entries = extract_equipment(pdf_path)
    print(f"Extracted {len(entries)} equipment entries:\n")
    for e in entries:
        print(f"  {e['category']} ({e.get('position', 'N/A')}): "
              f"{e.get('manufacturer', '')} {e.get('model', '')}".strip())

    conn = get_pg_connection()
    conn.autocommit = True
    ensure_equipment_schema(conn)
    upsert_equipment(conn, entries, source_document)
    conn.close()
    print(f"\nUpserted into vessel_equipment (source: {source_document}).")
