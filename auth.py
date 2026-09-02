"""
Real, individual-account authentication (Aug 2026) — replaces the
original free-text "Who's asking?" name field.

Why this matters beyond just "keeping strangers out": the free-text
field meant Engineer Notes' whole authorization model — restricting
submission to specific authorized people (see ingestion/engineer_notes.py
AUTHORIZED_NOTE_AUTHORS) — was only ever checking a name someone typed,
not who they actually were. Anyone with the app URL could type "Jared"
and submit a note carrying his name and real operational authority. Real
login closes that gap: authorization now checks a verified identity, not
typed text.

Deliberately scoped for where this project actually is (two known
users, Dave and Jared) rather than building more than's needed:
- No self-service signup — accounts are provisioned manually via
  manage_users.py, the same way AUTHORIZED_NOTE_AUTHORS is already a
  small, manually-maintained list.
- No password-reset flow — real complexity (email sending, reset
  tokens) not worth building for two known people; a forgotten password
  just means asking Dave to reset it via manage_users.py.
- Session persistence relies entirely on Streamlit's own session state
  (matching how the free-text field already worked) — no separate
  cookie/token system.

username values are stored and compared in their normalized (stripped,
title-cased) form throughout — "Jared", not "jared" or "JARED" — so a
verified login produces exactly the same st.session_state.user_name
value the free-text field used to, meaning every existing piece of code
that keys off user_name (chat history, Engineer Notes authorization,
feedback) continues to work completely unchanged, and all prior data
(existing conversations, existing notes) stays correctly attributed
with zero migration needed.
"""

import bcrypt
import psycopg2.extras

# Real, basic brute-force protection: after this many consecutive failed
# attempts, further attempts are rejected outright (without even
# checking the password) for LOCKOUT_MINUTES — a standard, expected
# control for any real login, not something to skip just because there
# are only two accounts today.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

MIN_PASSWORD_LENGTH = 8


def ensure_users_schema(conn):
    """RLS enabled directly here, not left as a separate manual step (Sept
    2026, real incident) — a real Supabase security alert found this
    table (and 4 others) publicly readable/writable through Supabase's
    auto-generated public REST API, since RLS defaults to disabled on any
    new Postgres table. Baking ENABLE ROW LEVEL SECURITY into the same
    function that creates the table means every future table created the
    same way is protected automatically, the moment it's created — not
    dependent on anyone remembering a separate step. Safe to call
    repeatedly: enabling RLS on an already-enabled table is a harmless
    no-op, same as CREATE TABLE IF NOT EXISTS. This does NOT affect our
    own app's direct database connection, which authenticates as the
    table owner and bypasses RLS by default — only the separate,
    publicly-reachable REST API path is blocked."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                failed_attempts INT NOT NULL DEFAULT 0,
                locked_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY;")
    conn.commit()


def hash_password(password: str) -> str:
    """bcrypt with its own random salt per call — the standard, correct
    way to store a password. Never store or log a plain password
    anywhere, including in this codebase or in chat with Claude."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def normalize_username(username: str) -> str:
    """Same normalization the old free-text field already applied —
    preserves exact continuity with every existing record (past
    conversations, Engineer Notes, AUTHORIZED_NOTE_AUTHORS) that already
    uses "Jared" / "Dave" in this exact form."""
    return username.strip().title()


def verify_credentials(conn, username: str, password: str) -> tuple[bool, str]:
    """Returns (success, message). message is a real, safe-to-display
    string explaining a failure (locked out, wrong credentials, no
    account) — deliberately generic between "wrong password" and "no
    such account" (both just say "Incorrect username or password"), a
    standard practice so a failed attempt can't be used to discover
    which usernames are real accounts."""
    normalized = normalize_username(username)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT password_hash, failed_attempts, locked_until FROM users "
            "WHERE username = %s",
            (normalized,),
        )
        row = cur.fetchone()

        if row and row["locked_until"]:
            cur.execute("SELECT now() < %s AS still_locked", (row["locked_until"],))
            if cur.fetchone()["still_locked"]:
                return False, (
                    f"Too many failed attempts. Try again in a few minutes, "
                    f"or ask an administrator to reset the account."
                )

        if not row:
            return False, "Incorrect username or password."

        password_ok = bcrypt.checkpw(
            password.encode("utf-8"), row["password_hash"].encode("utf-8")
        )

        if password_ok:
            cur.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL "
                "WHERE username = %s",
                (normalized,),
            )
            conn.commit()
            return True, ""

        new_attempts = row["failed_attempts"] + 1
        if new_attempts >= MAX_FAILED_ATTEMPTS:
            cur.execute(
                "UPDATE users SET failed_attempts = %s, "
                "locked_until = now() + interval %s WHERE username = %s",
                (new_attempts, f"{LOCKOUT_MINUTES} minutes", normalized),
            )
            conn.commit()
            return False, (
                f"Too many failed attempts. Account locked for "
                f"{LOCKOUT_MINUTES} minutes."
            )
        else:
            cur.execute(
                "UPDATE users SET failed_attempts = %s WHERE username = %s",
                (new_attempts, normalized),
            )
            conn.commit()
            return False, "Incorrect username or password."
