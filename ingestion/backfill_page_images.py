"""
One-time backfill: renders and uploads page images for documents that
were already ingested BEFORE the page-images feature existed (Aug 2026)
— everything in the library except the one test document.

Deliberately does NOT touch chunk text or embeddings at all — this is a
pure additive UPDATE, setting page_image_url on existing rows. No
re-embedding, no Voyage cost, no risk to already-working retrieval.

Safe to re-run / resume: skips any document that already has at least
one chunk with a page_image_url set, so an interrupted run can just be
re-run without redoing already-backfilled documents.

Usage:
    export SUPABASE_DB_URL="..."
    export SUPABASE_URL="..."
    export SUPABASE_SERVICE_KEY="..."
    python backfill_page_images.py "/path/to/your/Drivetrain TMs folder"
"""

import sys
from pathlib import Path

import pdfplumber
import psycopg2.extras

sys.path.insert(0, ".")
import page_images
from retrieval import get_pg_connection


def backfill(folder: Path):
    page_images.ensure_storage_bucket()
    conn = get_pg_connection()
    conn.autocommit = True

    pdf_files = sorted(folder.rglob("*.pdf"))  # recursive, matches scan_folder.py's own search
    print(f"Found {len(pdf_files)} PDF file(s) under {folder}\n")

    for path in pdf_files:
        source_file = path.name

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT DISTINCT page_number FROM tm_chunks WHERE source_file = %s",
                (source_file,),
            )
            pages_with_text = {r["page_number"] for r in cur.fetchall()}

        if not pages_with_text:
            print(f"Skipping {source_file}: not found in the index under this exact "
                  f"filename (not ingested yet, or ingested under a different name)\n")
            continue

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM tm_chunks WHERE source_file = %s AND page_image_url IS NOT NULL",
                (source_file,),
            )
            already_has = cur.fetchone()[0]
        if already_has > 0:
            print(f"Skipping {source_file}: already has {already_has} chunk(s) with images\n")
            continue

        try:
            with pdfplumber.open(path) as pdf:
                total_pages = len(pdf.pages)
        except Exception as e:
            print(f"Skipping {source_file}: could not open as a genuine PDF ({e})\n")
            continue

        print(f"Processing {source_file} ({total_pages} pages, "
              f"{len(pages_with_text)} already have extracted text)...")
        urls = page_images.render_and_upload_selected_pages(
            path, source_file, pages_with_text, total_pages)

        if urls:
            with conn.cursor() as cur:
                for page_number, url in urls.items():
                    cur.execute(
                        "UPDATE tm_chunks SET page_image_url = %s "
                        "WHERE source_file = %s AND page_number = %s",
                        (url, source_file, page_number),
                    )
            print(f"  {len(urls)} page image(s) uploaded and linked.\n")
        else:
            print(f"  No pages selected for rendering (or all failed) — see above for details.\n")

    conn.close()
    print("Backfill complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python backfill_page_images.py \"/path/to/your/TM folder\"")
    backfill(Path(sys.argv[1]))
