#!/usr/bin/env python3.14
"""Deletes specific Engineer Notes by ID.
Use list_engineer_notes.py first to confirm which IDs to remove.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 delete_engineer_notes.py 1 8 9 10

Add --apply to actually delete (dry run by default).
"""
import os
import sys
import psycopg2
import psycopg2.extras

apply = "--apply" in sys.argv
ids = [int(a) for a in sys.argv[1:] if a.isdigit()]

if not ids:
    sys.exit("Usage: python3.14 delete_engineer_notes.py ID1 ID2 ... [--apply]")

db_url = os.environ.get("SUPABASE_DB_URL")
if not db_url:
    sys.exit("SUPABASE_DB_URL not set — export it first.")

conn = psycopg2.connect(db_url)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT id, author, category, note_text FROM engineer_notes WHERE id = ANY(%s)", (ids,))
    rows = [dict(r) for r in cur.fetchall()]
conn.close()

if not rows:
    sys.exit("No notes found with those IDs.")

print(f"Notes to delete:\n")
for row in rows:
    print(f"  ID {row['id']} — {row['author']} / {row['category']}")
    print(f"  {row['note_text'][:80]}")
    print()

if not apply:
    print("DRY RUN — nothing deleted.")
    print(f"Run with --apply to delete these {len(rows)} note(s):")
    print(f"  python3.14 delete_engineer_notes.py {' '.join(str(i) for i in ids)} --apply")
    sys.exit(0)

confirm = input(f"Permanently delete {len(rows)} note(s)? Type YES to confirm: ")
if confirm.strip() != "YES":
    print("Aborted.")
    sys.exit(0)

conn = psycopg2.connect(db_url)
with conn.cursor() as cur:
    cur.execute("DELETE FROM engineer_notes WHERE id = ANY(%s)", (ids,))
    deleted = cur.rowcount
conn.commit()
conn.close()

print(f"Deleted {deleted} Engineer Note(s).")
