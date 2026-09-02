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
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = response.content[0].text.strip()
    # Defensive: strip markdown fences if the model adds them despite instructions not to
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


def ensure_equipment_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vessel_equipment (
                id BIGSERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                position TEXT,
                manufacturer TEXT,
                model TEXT,
                serial_number TEXT,
                specs JSONB,
                notes TEXT,
                source_document TEXT,
                source_page INT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (category, position)
            );
        """)
        # RLS enabled directly here, not left as a separate manual step
        # (Sept 2026, real incident) — see auth.py's ensure_users_schema()
        # docstring for the full explanation. Safe to call repeatedly.
        cur.execute("ALTER TABLE vessel_equipment ENABLE ROW LEVEL SECURITY;")
    conn.commit()


def upsert_equipment(conn, entries: list[dict], source_document: str):
    rows = [
        (
            e["category"], e.get("position"), e.get("manufacturer"), e.get("model"),
            e.get("serial_number"), json.dumps(e.get("specs") or {}), e.get("notes"),
            source_document,
        )
        for e in entries
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO vessel_equipment
                (category, position, manufacturer, model, serial_number, specs, notes, source_document)
            VALUES %s
            ON CONFLICT (category, position) DO UPDATE SET
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
            # exactly (system, category, position, manufacturer, model,
            # serial_number, specs, notes, source_document). Real bug
            # fixed here (Sept 2026, found via a real live ingestion
            # run): this template still had only 8 placeholders after
            # `system` was added as a new first column — the column
            # list and the row tuple were both updated correctly, but
            # this template wasn't, silently misaligning every row's
            # values against the wrong columns and producing a
            # confusing, unrelated-looking "no unique constraint
            # matching ON CONFLICT" error rather than an obvious count
            # mismatch.
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
                SELECT category, position, manufacturer, model, serial_number, specs, notes
                FROM vessel_equipment
                ORDER BY category, position
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def format_equipment_list(entries: list[dict]) -> str:
    """Formats the registry as compact text for a prompt."""
    if not entries:
        return ""
    lines = ["Vessel equipment currently installed (use this to know which "
             "model/variant applies when a TM covers multiple options):"]
    for e in entries:
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
