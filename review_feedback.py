"""
Pulls every 👎 (and, for comparison, a quick count of 👍) from the real
chat history, with full context — the original question, the answer,
and any safety info / field notes shown alongside it — so a genuine
review can happen against real data, not just whatever's remembered.

Closes out a backlog item that's been open since early in this project:
accumulated feedback has never actually been reviewed. Run this from the
repo root (same place you'd run `streamlit run app.py` from).

Usage:
    export SUPABASE_DB_URL="your-real-connection-string"
    python3.14 review_feedback.py
"""

import db
import psycopg2.extras


def get_preceding_question(conn, conversation_id: str, before_timestamp) -> str:
    """Finds the user question immediately before a given assistant
    message in the same conversation — this is what the person actually
    asked, needed to make sense of why an answer got a thumbs-down."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT content FROM messages
            WHERE conversation_id = %s AND role = 'user' AND created_at < %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (conversation_id, before_timestamp),
        )
        row = cur.fetchone()
    return row["content"] if row else "(no preceding question found)"


def main():
    conn = db.get_connection()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM messages WHERE role = 'assistant' AND feedback = 'up'")
        up_count = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM messages WHERE role = 'assistant' AND feedback = 'down'")
        down_count = cur.fetchone()["n"]

    print(f"Total 👍: {up_count}    Total 👎: {down_count}\n")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT conversation_id, user_name, content, safety_info,
                   field_notes_used, created_at
            FROM messages
            WHERE role = 'assistant' AND feedback = 'down'
            ORDER BY created_at ASC
            """
        )
        downvotes = cur.fetchall()

    if not downvotes:
        print("No 👎 feedback found.")
        conn.close()
        return

    for i, dv in enumerate(downvotes, 1):
        question = get_preceding_question(conn, dv["conversation_id"], dv["created_at"])
        print("=" * 90)
        print(f"#{i} — {dv['created_at']} — {dv['user_name']}")
        print(f"\nQUESTION:\n{question}")
        print(f"\nANSWER (thumbs-down):\n{dv['content']}")
        if dv["safety_info"]:
            print(f"\nSAFETY INFO SHOWN:\n{dv['safety_info']}")
        if dv["field_notes_used"]:
            print(f"\nFIELD NOTES SHOWN: {dv['field_notes_used']}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
