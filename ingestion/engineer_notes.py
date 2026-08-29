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

# Who's allowed to submit an Engineer Note, and their role at the time
# they do (Aug 2026, real requirement from Jared): these notes carry
# real weight — shown before the answer, treated with real authority —
# so submission is deliberately restricted to a small, known list rather
# than open to anyone using the app. The Port Engineer is the real
# authority on these notes and approves/writes most of them; they may
# delegate to a Chief Engineer. To add someone (e.g. a newly delegated
# Chief Engineer), just add a line here and redeploy — no admin UI built
# for this yet, deliberately, since the list is small and changes rarely
# at this stage (see BACKLOG.md for the deferred, bigger version of this
# — a real submission+approval workflow — once usage grows beyond Dave
# and Jared).
#
# Keys must exactly match the normalized (title-cased) value of the
# "Who's asking?" name field for that person, or the check in app.py
# won't recognize them.
AUTHORIZED_NOTE_AUTHORS = {
    "Jared": "Port Engineer",
    "Dave": "Developer/PM",
}


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
        cur.execute("""
            ALTER TABLE engineer_notes
                ADD COLUMN IF NOT EXISTS author_role TEXT;
        """)
    conn.commit()


def add_note(conn, category: str, position: str | None, author: str,
             note_text: str, author_role: str | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO engineer_notes (category, position, author, note_text, author_role)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (category, position, author, note_text, author_role),
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
                SELECT id, category, position, author, author_role, note_text, created_at
                FROM engineer_notes
                ORDER BY created_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_notes_by_ids(conn, note_ids: list[int]) -> list[dict]:
    """Fetches the exact, verbatim stored text for specific notes by ID
    (Aug 2026) — used to display the notes Claude actually referenced in
    forming an answer, pulling straight from the database rather than
    trusting Claude's own paraphrase in its response text. This is what
    makes attribution and note content 100% exact every time, not
    dependent on the model re-stating them accurately.

    created_at is converted to a plain display string here (not left as
    a datetime object) — this dict gets stored as JSONB in the messages
    table (see db.py), and Python's json.dumps() can't serialize a raw
    datetime; formatting it once here avoids that failure and also means
    callers (app.py) never need datetime-vs-string branching logic
    depending on whether a note came from a live answer or a reloaded
    past conversation."""
    if not note_ids:
        return []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, category, position, author, author_role, note_text, created_at "
            "FROM engineer_notes WHERE id = ANY(%s)",
            (note_ids,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].strftime("%b %d, %Y")
    return rows


def format_notes_for_prompt(notes: list[dict]) -> str:
    """Formats notes for inclusion in the Claude prompt. Deliberately
    labeled and worded to make clear these are real-world engineer input,
    not manufacturer data — this label is what the SYSTEM_PROMPT rule in
    answer_query.py refers back to when instructing Claude how to use
    and attribute these.

    Includes each note's ID (Aug 2026) — this is what lets Claude report
    back exactly which notes it used (via the FIELD_NOTE_IDS section of
    its structured response — see answer_query.py), rather than us having
    to guess by re-parsing its free-text answer. The application then
    looks up those exact IDs directly (get_notes_by_ids above) to display
    the real, verbatim note content — not Claude's own rendering of it."""
    if not notes:
        return ""
    lines = ["Engineer Notes — real-world field experience added by the crew, "
             "NOT manufacturer data. Each has a NOTE_ID — if you use one in "
             "forming your answer, report its ID in the FIELD_NOTE_IDS section "
             "of your response (see system prompt) rather than just narrating "
             "it in the answer text itself:"]
    for n in notes:
        parts = [n["category"]]
        if n.get("position"):
            parts.append(n["position"])
        date_str = n["created_at"].strftime("%b %d, %Y") if n.get("created_at") else ""
        author_display = n["author"]
        if n.get("author_role"):
            author_display += f' ({n["author_role"]})'
        lines.append(f'- [NOTE_ID:{n["id"]}] [{" ".join(parts)}] {author_display}, '
                      f'{date_str}: {n["note_text"]}')
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
