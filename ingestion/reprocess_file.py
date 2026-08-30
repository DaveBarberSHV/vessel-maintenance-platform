"""
Force specific files to be reprocessed on the next scan_folder.py run
(Aug 2026) — a real, recurring need whenever ingestion logic changes in a
way that would benefit already-ingested files (e.g. vision extraction:
existing DWG/wiring diagram files were originally skipped as
metadata-only and won't automatically get revisited just because that
code now exists, since scan_folder.py's hash-based skip logic sees their
content as unchanged).

This deliberately does NOT touch scan_folder.py's core skip logic itself
— that fast-skip behavior is correct and worth keeping for the common
case. This is a narrow, explicit escape hatch: "I know these specific
files need a fresh look," not a general "reprocess everything" switch,
which would be slow and needlessly re-embed files that don't need it.

What this does, mirroring the exact same cleanup scan_folder.py already
performs internally when a file is updated or renamed:
  1. Removes the file's existing chunks from Postgres (delete_chunks)
  2. Removes the file's existing chunks from the local chunks.jsonl record
  3. Removes the file's entry from manifest.json entirely

After running this, a normal `python scan_folder.py /path/to/folder` will
see the file as brand new and fully reprocess it — including running it
through vision extraction if it qualifies (no text layer) and the new
code path is enabled.

Usage:
    python reprocess_file.py "ExactFileName.pdf" ["AnotherFile.pdf" ...]

Safe to run without a live Postgres connection configured — chunk
removal from the database is attempted but the local files still get
cleaned up either way, since the follow-up scan_folder.py run will
correctly detect "new" content and can re-establish everything.
"""

import sys
from pathlib import Path

from scan_folder import load_manifest, save_manifest, load_chunks, save_chunks


def reprocess_file(filenames: list[str]) -> None:
    manifest = load_manifest()
    chunks = load_chunks()

    for filename in filenames:
        if filename not in manifest:
            print(f"'{filename}' not found in manifest.json — nothing to clear. "
                  f"(Check the exact filename matches what's in your Drive folder.)")
            continue

        chunk_ids = manifest[filename]["chunk_ids"]
        chunks = [c for c in chunks if c["chunk_id"] not in chunk_ids]

        try:
            from retrieval import get_pg_connection, delete_chunks
            conn = get_pg_connection()
            delete_chunks(conn, chunk_ids)
            conn.close()
            print(f"'{filename}': removed {len(chunk_ids)} chunk(s) from Postgres.")
        except Exception as e:
            print(f"'{filename}': could not remove chunks from Postgres right now "
                  f"({type(e).__name__}: {e}) — local records still cleared below; "
                  f"the next scan_folder.py run will still correctly re-add fresh "
                  f"chunks, just alongside any stale old ones until this is retried "
                  f"with a working connection.")

        del manifest[filename]
        print(f"'{filename}': cleared from manifest — will be treated as new on "
              f"the next scan_folder.py run.")

    save_manifest(manifest)
    save_chunks(chunks)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python reprocess_file.py "ExactFileName.pdf" ["AnotherFile.pdf" ...]')
    reprocess_file(sys.argv[1:])
