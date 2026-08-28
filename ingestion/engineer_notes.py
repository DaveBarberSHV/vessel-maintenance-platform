"""
Engineer Notes: real-world, experience-based knowledge from engineers,
tied to specific equipment, clearly separated from official manufacturer
TM content. See BACKLOG.md for the full design discussion and Jared's
real motivating example.

Design decisions, deliberately following the equipment registry's proven
pattern rather than building new infrastructure:
- Tied to the same (category, position) identity as vessel_equipment —
  reuses the same dropdown of real installed equipment, plus a
  "General / Other" option for notes that don't map to one specific item.
- Notes are fetched fresh and injected into EVERY question's prompt,
  unconditionally — the same reasoning as the equipment registry: a
  question rarely names "is there a note about this?" explicitly, so
  retrieval can't be relied on to surface a relevant note. No separate
  semantic search infrastructure needed for a first version.
- Attribution (who + when) is a hard requirement, never optional —
  Jared's own stated reasoning: a note must always read as clearly
  distinct from manufacturer data, never blended in as if it were the
  same kind of fact.
- No edit/delete in this version — a deliberate v1 scope cut, not an
  oversight. See BACKLOG.md.
"""

import psycopg2.extras

GENERAL_CATEGORY = "General / Other"


def ensure_notes_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS engineer_notes (
                id BIGSERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                position TEXT,
                author TEXT NOT NULL,
                note_text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
    conn.commit()


def add_note(conn, category: str, position: str | None, author: str, note_text: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO engineer_notes (category, position, author, note_text)
            VALUES (%s, %s, %s, %s)
            """,
            (category, position, author, note_text),
        )
    conn.commit()


def get_all_notes(conn) -> list[dict]:
    """Returns every note, most recent first. Used both to inject into
    every prompt (answer_query.py) and to display a running log in the
    app. Returns [] rather than raising if the table doesn't exist yet —
    same resilience pattern as the equipment registry: this must never be
    the reason a question or the app itself fails."""
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT category, position, author, note_text, created_at
                FROM engineer_notes
                ORDER BY created_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def format_notes_for_prompt(notes: list[dict]) -> str:
    """Formats notes for inclusion in the Claude prompt. Deliberately
    labeled and worded to make clear these are real-world engineer input,
    not manufacturer data — this label is what the SYSTEM_PROMPT rule in
    answer_query.py refers back to when instructing Claude how to use
    and attribute these."""
    if not notes:
        return ""
    lines = ["Engineer Notes — real-world field experience added by the crew, "
             "NOT manufacturer data (always attribute by author and date if used):"]
    for n in notes:
        parts = [n["category"]]
        if n.get("position"):
            parts.append(n["position"])
        date_str = n["created_at"].strftime("%b %d, %Y") if n.get("created_at") else ""
        lines.append(f'- [{" ".join(parts)}] {n["author"]}, {date_str}: {n["note_text"]}')
    return "\n".join(lines)


def get_equipment_options(conn) -> list[tuple[str, str | None]]:
    """Returns (category, position) pairs for the notes dropdown — reuses
    the real installed-equipment registry rather than a separate list,
    plus a General/Other option for anything that doesn't map cleanly to
    one specific item.

    Queries the database directly rather than calling
    extract_equipment_list.get_equipment_list() (Aug 2026, real bug fix):
    that function deliberately swallows its own errors internally so a
    broken equipment lookup never breaks a question — correct there, but
    it meant a real failure here (e.g. a stale cached connection) never
    became an actual exception, so app.py's with_connection_retry()
    wrapper — which only knows to retry on a caught exception — never
    got the chance to fetch a fresh connection and try again. The result
    was a dropdown silently stuck showing only "General / Other" instead
    of the real registry. This function is deliberately allowed to raise;
    the caller (app.py) already wraps this in a try/except of its own as
    the final fallback if a fresh connection genuinely doesn't help
    either."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT category, position FROM vessel_equipment ORDER BY category, position")
        options = [(r["category"], r["position"]) for r in cur.fetchall()]
    options.append((GENERAL_CATEGORY, None))
    return options
