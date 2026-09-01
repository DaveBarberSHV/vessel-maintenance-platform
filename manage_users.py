"""
Manually provision, list, and remove user accounts (Aug 2026) — no
self-service signup by design; see auth.py's docstring for why.

Usage:
    python3.14 manage_users.py add jared
    python3.14 manage_users.py list
    python3.14 manage_users.py remove jared

Password is always entered interactively via a hidden prompt (getpass),
never as a command-line argument — a password passed on the command line
would be saved in plain text in your shell history, defeating the whole
point of hashing it properly in the database.
"""

import sys
from getpass import getpass

import psycopg2.extras

import auth
import db


def add_user(username: str):
    normalized = auth.normalize_username(username)
    password = getpass(f"New password for '{normalized}': ")
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {auth.MIN_PASSWORD_LENGTH} characters. Nothing was saved.")
        return
    confirm = getpass("Confirm password: ")
    if password != confirm:
        print("Passwords didn't match. Nothing was saved.")
        return

    conn = db.get_connection()
    auth.ensure_users_schema(conn)
    password_hash = auth.hash_password(password)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, failed_attempts, locked_until)
            VALUES (%s, %s, 0, NULL)
            ON CONFLICT (username) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    failed_attempts = 0,
                    locked_until = NULL
            """,
            (normalized, password_hash),
        )
    conn.commit()
    conn.close()
    print(f"Account '{normalized}' saved. (If this username already existed, "
          f"its password was reset and any lockout cleared.)")


def list_users():
    conn = db.get_connection()
    auth.ensure_users_schema(conn)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT username, failed_attempts, locked_until, created_at "
            "FROM users ORDER BY username"
        )
        rows = cur.fetchall()
    conn.close()
    if not rows:
        print("No accounts exist yet.")
        return
    for r in rows:
        status = ""
        if r["locked_until"]:
            status = f"  [LOCKED until {r['locked_until']}]"
        print(f"{r['username']:20} created {r['created_at']}{status}")


def remove_user(username: str):
    normalized = auth.normalize_username(username)
    conn = db.get_connection()
    auth.ensure_users_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (normalized,))
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"Account '{normalized}' removed.")
    else:
        print(f"No account named '{normalized}' was found.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("add", "list", "remove"):
        sys.exit(__doc__)
    action = sys.argv[1]
    if action == "list":
        list_users()
    elif action in ("add", "remove"):
        if len(sys.argv) < 3:
            sys.exit(f"Usage: python3.14 manage_users.py {action} <username>")
        (add_user if action == "add" else remove_user)(sys.argv[2])
