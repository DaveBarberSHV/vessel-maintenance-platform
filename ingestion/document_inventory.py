"""
Document inventory: answers "what documents/drawings exist" questions,
as distinct from "what does a document say" questions.

Real motivating case (Aug 2026, see BACKLOG.md's DEF alarm / azimuth
thruster entries): asking "are there any more drawings available for the
Azimuth Thruster?" returned zero results for the real Shaft Arrangement
drawing anywhere in the top 20 semantic search results — confirmed
directly via retrieval.query_chunks(). This isn't a chunking or ranking
problem; it's a fundamental mismatch. Semantic search finds content that
resembles the question's meaning, but no page of an actual drawing
contains anything resembling "here is a list of drawings that exist" —
so no amount of better chunking fixes a question whose intent doesn't
semantically resemble any real page's content.

Same fix pattern as the vessel equipment registry and Engineer Notes
(both proven working): rather than relying on retrieval to find this
information, generate it directly and inject it into every prompt
unconditionally. No new data entry required — this is built entirely
from what's already in tm_chunks.
"""

import psycopg2.extras


def get_document_inventory(conn) -> list[dict]:
    """Returns one entry per distinct document currently in the system —
    title, doc type, revision, and the equipment/system it's filed
    under. Used by answer_query.py — always included in every question's
    context, not dependent on retrieval happening to find it. Returns []
    rather than raising if something goes wrong (e.g. the table is
    briefly unreachable) — this must never be the reason a question
    fails, same resilience pattern as the equipment registry and
    Engineer Notes."""
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT document_title, document_type, revision, equipment_model
                FROM tm_chunks
                ORDER BY document_title
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def format_document_inventory(entries: list[dict]) -> str:
    """Formats the inventory as compact text for a prompt. Deliberately
    labeled to make the boundary crisp for Claude: this list answers
    "does X exist," never "what does X say" — see the matching
    SYSTEM_PROMPT rule in answer_query.py, which is what actually
    enforces that distinction in how this gets used."""
    if not entries:
        return ""
    lines = ["Document Library — every document currently in the system, by "
             "title and type (use ONLY to answer whether a document/drawing "
             "exists — never to answer what its content says; see system "
             "prompt rule):"]
    for e in entries:
        rev = f", {e['revision']}" if e.get("revision") else ""
        lines.append(f"- {e['document_title']} ({e['document_type']}{rev})")
    return "\n".join(lines)
