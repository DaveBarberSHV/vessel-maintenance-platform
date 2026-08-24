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

v2 note (see docs/architecture.md): when maintenance/troubleshooting field
notes get added later, they'll likely live in their own table, linked to
equipment/vessel identifiers — not bolted onto this one. Nothing here
needs to change to support that.
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
    session, not once per script rerun."""
    return psycopg2.connect(get_db_url())


def ensure_schema(conn):
    """CREATE TABLE IF NOT EXISTS — safe to call every time the app
    starts. No migration system needed yet at this scale; revisit if the
    schema needs a real change after real data exists."""
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
    conn.commit()


def save_message(conn, conversation_id: str, user_name: str, role: str,
                  content: str, chunks: list | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages (conversation_id, user_name, role, content, chunks)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (conversation_id, user_name, role, content,
             json.dumps(chunks) if chunks is not None else None),
        )
    conn.commit()


def load_conversation(conn, conversation_id: str) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT role, content, chunks, created_at
            FROM messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "chunks": r["chunks"] if r["chunks"] is not None else None,
        }
        for r in rows
    ]


def list_conversations(conn, user_name: str, limit: int = 20) -> list[dict]:
    """One row per conversation: its id, the first question asked in it
    (for display), and when it started. Used to populate the sidebar
    history list."""
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


def new_conversation_id() -> str:
    return str(uuid.uuid4())
