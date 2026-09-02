"""
Audits every chunk already in the database for the exact real bug found
Sept 2026 — garbled/reversed native text (e.g. a rotated AutoCAD
title-block stamp) that happened to be long enough to pass as "real
text," silently skipping vision extraction entirely. Uses the same
is_real_language() check scan_folder.py now applies going forward, but
run here retroactively against everything already ingested before the
fix existed.

A real match here means: native text (not already vision-transcribed —
those are correctly excluded, since they already went through vision)
that fails the real-language check despite being long enough to have
been accepted. That's precisely the signature of this bug — genuinely
different from a page that's just short and terse, which the length
threshold alone already handles correctly.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 audit_garbled_text.py
"""

import sys

sys.path.insert(0, ".")
from scan_folder import is_real_language, VISION_CANDIDATE_CHAR_THRESHOLD
from retrieval import get_pg_connection
import psycopg2.extras


def audit():
    conn = get_pg_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT source_file, document_title, page_number, text
            FROM tm_chunks
            WHERE LENGTH(TRIM(text)) >= %s
            ORDER BY source_file, page_number
        """, (VISION_CANDIDATE_CHAR_THRESHOLD,))
        rows = cur.fetchall()
    conn.close()

    print(f"Checked {len(rows)} chunk(s) at or above the {VISION_CANDIDATE_CHAR_THRESHOLD}-character threshold.\n")

    affected_files = set()
    for row in rows:
        # Already vision-transcribed pages are fine by construction —
        # only checking native text that was accepted as-is.
        if row["text"].strip().startswith("[AI-transcribed"):
            continue
        if not is_real_language(row["text"]):
            print(f"POSSIBLE MATCH: {row['source_file']}, p. {row['page_number']} "
                  f"({row['document_title']})")
            print(f"  First 150 chars: {row['text'][:150]!r}")
            print()
            affected_files.add(row["source_file"])

    print("=" * 70)
    if affected_files:
        print(f"{len(affected_files)} file(s) with at least one likely-affected page:")
        for f in sorted(affected_files):
            print(f"  - {f}")
        print()
        print("To fix each one, run:")
        for f in sorted(affected_files):
            print(f'  python3.14 reprocess_file.py "{f}"')
        print("Then re-run scan_folder.py normally to reprocess them with the fix in place.")
    else:
        print("No matches found — no other pages currently show this same signature.")


if __name__ == "__main__":
    audit()
