#!/usr/bin/env python3.14
"""Clears all conversation history and messages from the database,
leaving everything else intact (chunks, Engineer Notes, equipment
registry, feedback data on messages is also cleared as part of messages).

Use before a demo or when onboarding new users so they start with a
clean slate. Safe to run multiple times.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 clear_test_data.py

Add --apply to actually delete (dry run by default).
"""
import os
import sys
import psycopg2
import psycopg2.extras

apply = "--apply" in sys.argv

db_url = os.environ.get("SUPABASE_DB_URL")
if not db_url:
    sys.exit("SUPABASE_DB_URL not set — export it first.")

conn = psycopg2.connect(db_url)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT COUNT(*) as n FROM messages")
    msg_count = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(DISTINCT conversation_id) as n FROM messages")
    conv_count = cur.fetchone()["n"]

print(f"Found {conv_count} conversation(s) and {msg_count} message(s).")
print()

if not apply:
    print("DRY RUN — no data deleted.")
    print("Run with --apply to actually clear the data:")
    print("  python3.14 clear_test_data.py --apply")
    conn.close()
    sys.exit(0)

confirm = input(f"Delete all {conv_count} conversations and {msg_count} messages? Type YES to confirm: ")
if confirm.strip() != "YES":
    print("Aborted.")
    conn.close()
    sys.exit(0)

with conn.cursor() as cur:
    cur.execute("DELETE FROM messages")
    deleted_msgs = cur.rowcount
conn.commit()
conn.close()

print(f"Deleted {deleted_msgs} message(s) across {conv_count} conversation(s).")
print("Engineer Notes, document chunks, and equipment registry are untouched.")
