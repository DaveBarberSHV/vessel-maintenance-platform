"""
Directly inspects what's actually stored for a specific document/page in
tm_chunks — used here to check exactly what text is on record for page 8
of the CAT 3512E service report, to see whether "4367-1" and its
occurrence count are legible in what got stored, independent of whether
retrieval found it for any particular question.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 inspect_page.py "ServiceReport" 8
"""

import sys
import psycopg2.extras
from retrieval import get_pg_connection


def inspect_page(title_contains: str, page_number: int):
    conn = get_pg_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT document_title, revision, page_number, chunk_id, text
            FROM tm_chunks
            WHERE document_title ILIKE %s AND page_number = %s
            ORDER BY chunk_id
            """,
            (f"%{title_contains}%", page_number),
        )
        rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"No chunks found matching title '{title_contains}', page {page_number}.")
        return

    for r in rows:
        print("=" * 90)
        print(f"{r['document_title']}, {r['revision']}, p. {r['page_number']} "
              f"(chunk_id: {r['chunk_id']})")
        print()
        print(r["text"])
        print()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('Usage: python inspect_page.py "title substring" page_number')
    inspect_page(sys.argv[1], int(sys.argv[2]))
