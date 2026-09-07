#!/usr/bin/env python3.14
"""Lists all Engineer Notes in the system for review.
Run from the vessel-maintenance-platform root directory with
SUPABASE_DB_URL exported.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 list_engineer_notes.py
"""
import os
import sys
import psycopg2
import psycopg2.extras

db_url = os.environ.get("SUPABASE_DB_URL")
if not db_url:
    sys.exit("SUPABASE_DB_URL not set — export it first.")

conn = psycopg2.connect(db_url)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("""
        SELECT id, author, author_role, category,
               position, note_text, created_at
        FROM engineer_notes
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
conn.close()

if not rows:
    print("No Engineer Notes found.")
    sys.exit(0)

print(f"Found {len(rows)} Engineer Note(s):\n")
print("=" * 80)
for row in rows:
    print(f"ID:        {row['id']}")
    print(f"Date:      {row['created_at']}")
    print(f"Author:    {row['author']}" + (f" ({row['author_role']})" if row['author_role'] else ""))
    print(f"Category:  {row['category']}")
    if row.get('position'):
        print(f"Position:  {row['position']}")
    print(f"Note:\n{row['note_text']}")
    print("=" * 80)
