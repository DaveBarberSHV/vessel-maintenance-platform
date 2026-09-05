"""
Persistence layer for chat history — Supabase (hosted Postgres).

See docs/architecture.md for why Supabase specifically (over a simpler
local-file option): chat history needs to survive restarts/redeploys, and
Supabase's pgvector extension leaves room for a planned v2 feature
(searchable maintenance/troubleshooting field notes) without needing new
infrastructure later.

Connection: a single environment variable / Streamlit secret,
SUPABASE_DB_URL, holding the full Postgres connection string from
Supabase's "Direct Connection" tab. Never hardcoded, never logged.

Schema: one flat `messages` table. Each row is one chat message (user
question or assistant answer), tagged with a conversation_id (groups
messages from one sitting together) and a user_name (Dave or Jared, for
now — see app.py's simple name-selector "auth"). Assistant messages also
store their citation chunks as JSON, so history can show the same
sources UI it showed live.
"""

import json
import os
import uuid
from datetime import datetime

import psycopg2
import psycopg2.extras


def get_db_url() -> str:
    key = os.environ.get("SUPABASE_DB_URL")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("SUPABASE_DB_URL")
        except Exception:
            pass
    if not key:
        raise ValueError(
            "No SUPABASE_DB_URL available. Set it in .streamlit/secrets.toml "
            "(local) or your Streamlit Cloud app's secrets (deployed)."
        )
    return key


def get_connection():
    """New connection per call, deliberately — callers (app.py) are
    expected to cache this via st.cache_resource so it's created once per
    session, not once per script rerun.

    autocommit=True (Aug 2026, after a real incident): without this, every
    statement — including plain reads like list_conversations() — silently
    opens a transaction that only closes when something later calls
    .commit(). Since this connection is long-lived (cached for the whole
    browser session, reused across many reads and writes), a session that
    ends abruptly right after a read — the app crashing, or just being
    force-stopped during local testing — leaves that transaction stranded
    on the server indefinitely. A real one sat "idle in transaction" for
    almost 6 hours and silently blocked every new session's schema-check
    step from completing, which looked exactly like unexplained hanging/
    slowness with no error message. See BACKLOG.md.

    sslmode="require" (Sept 2026, real incident) — the server supports SSL
    but doesn't enforce it by default, and a real check found our own
    connection was silently using plain, unencrypted traffic despite that
    support existing. Explicitly requiring SSL here means this connection
    refuses to proceed without it, regardless of whatever the platform's
    own "Enforce SSL" setting happens to be — this shouldn't depend on a
    dashboard toggle staying correctly configured forever."""
    conn = psycopg2.connect(get_db_url(), sslmode="require")
    conn.autocommit = True
    return conn


def ensure_schema(conn):
    """CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS — safe to call
    every time the app starts. No migration system needed yet at this
    scale; revisit if the schema needs a real change after real data
    exists."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                conversation_id UUID NOT NULL,
                user_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                chunks JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages (conversation_id, created_at);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages (user_name, created_at DESC);
        """)
        cur.execute("""
            ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS feedback TEXT
                CHECK (feedback IN ('up', 'down'));
        """)
        cur.execute("""
            ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS safety_info TEXT;
        """)
        cur.execute("""
            ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS field_notes_used JSONB;
        """)
        cur.execute("""
            ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS show_document_images JSONB;
        """)
        # RLS enabled directly here, not left as a separate manual step
        # (Sept 2026, real incident) — see auth.py's ensure_users_schema()
        # docstring for the full explanation. Safe to call repeatedly.
        cur.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY;")
    conn.commit()


def save_message(conn, conversation_id: str, user_name: str, role: str,
                  content: str, chunks: list | None = None,
                  safety_info: str | None = None,
                  field_notes_used: list | None = None,
                  show_document_images: list | None = None) -> int:
    """Returns the new row's id — the caller (app.py) needs this to later
    attach feedback to the specific message a thumbs-up/down was clicked
    on."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages
                (conversation_id, user_name, role, content, chunks, safety_info,
                 field_notes_used, show_document_images)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, user_name, role, content,
             json.dumps(chunks) if chunks is not None else None,
             safety_info,
             json.dumps(field_notes_used) if field_notes_used is not None else None,
             json.dumps(show_document_images) if show_document_images is not None else None),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def set_feedback(conn, message_id: int, feedback: str | None):
    """feedback is 'up', 'down', or None (clearing previously-given
    feedback — e.g. clicking the same button again to un-set it)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE messages SET feedback = %s WHERE id = %s",
            (feedback, message_id),
        )
    conn.commit()


def load_conversation(conn, conversation_id: str) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, role, content, chunks, feedback, safety_info,
                   field_notes_used, show_document_images, created_at
            FROM messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "chunks": r["chunks"] if r["chunks"] is not None else None,
            "feedback": r["feedback"],
            "safety_info": r["safety_info"],
            "field_notes_used": r["field_notes_used"] if r["field_notes_used"] is not None else None,
            "show_document_images": r["show_document_images"] if r["show_document_images"] is not None else None,
        }
        for r in rows
    ]


def list_conversations(conn, user_name: str, limit: int = 500) -> list[dict]:
    """One row per conversation: its id, the first question asked in it
    (for display), and when it started. Used to populate the sidebar
    history list.

    limit raised from 20 to 500 (Aug 2026) — the old default of 20 was a
    silent, hard cutoff: past that many conversations, older ones simply
    stopped appearing in the sidebar at all, with no indication anything
    was missing. There's no real storage-scale reason to cap this tightly
    — Postgres handles many thousands of rows trivially — so this is now
    a generous practical safety net rather than a real limit anyone is
    expected to hit. See group_conversations_by_recency() below, which
    is the real fix for a long list: grouping for display, not deleting
    or hiding anything."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (conversation_id)
                conversation_id, content AS first_message, created_at
            FROM messages
            WHERE user_name = %s AND role = 'user'
            ORDER BY conversation_id, created_at ASC
            """,
            (user_name,),
        )
        rows = cur.fetchall()
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows[:limit]


def group_conversations_by_recency(conversations: list[dict]) -> dict[str, list[dict]]:
    """Groups a flat list of conversations (each with a 'created_at') into
    recency buckets for sidebar display — Today, Yesterday, This Week,
    This Month, Older (Aug 2026). Replaces the old flat list capped at 20
    — nothing is ever deleted; this only changes how the existing list is
    grouped for display, since there's no real storage-scale reason to
    delete anything, and the value of old troubleshooting history only
    grows over time, not shrinks.

    Only groups with at least one conversation should be shown by the
    caller (app.py) — an empty "This Month" header with nothing under it
    would just be visual noise.

    Bucketing uses UTC dates throughout, matching how created_at is
    stored (see get_connection()'s docstring — timestamps default to
    Postgres's now(), which is UTC) — not each individual user's local
    time zone, since there's no per-user timezone setting anywhere else
    in this app. A conversation started right at a day boundary might
    occasionally land in a slightly unexpected bucket; a reasonable
    simplification for a sidebar grouping label, not worth building real
    per-user timezone-awareness for."""
    from datetime import datetime, timezone, timedelta

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=7)
    month_start = today - timedelta(days=30)

    groups = {"Today": [], "Yesterday": [], "This Week": [], "This Month": [], "Older": []}
    for c in conversations:
        c_date = c["created_at"].date()
        if c_date == today:
            groups["Today"].append(c)
        elif c_date == yesterday:
            groups["Yesterday"].append(c)
        elif c_date >= week_start:
            groups["This Week"].append(c)
        elif c_date >= month_start:
            groups["This Month"].append(c)
        else:
            groups["Older"].append(c)
    return groups


def new_conversation_id() -> str:
    return str(uuid.uuid4())
