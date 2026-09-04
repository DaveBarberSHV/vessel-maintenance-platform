"""
Find duplicate files across the whole library by exact file size —
a real, direct proxy for identical content, not a guess (confirmed
Sept 2026: two files with an identical byte count turned out to be
byte-for-byte the same drawing).

Real motivating case: the yard drawing batch and files already
individually renamed weeks earlier turned out to include several of
the same underlying drawings, just named differently enough (M1 vs
M01, E09 vs a pre-existing MainSwitchboard filing) that yesterday's
name-based duplicate check missed them. This surfaced as files
silently flip-flopping between two names on alternating scan_folder.py
runs — a confusing symptom with a simple root cause.

Usage:
    python3.14 find_duplicate_files.py "/path/to/Vessel Maintenance System Documents"
"""

import sys
from collections import defaultdict
from pathlib import Path


def run(folder_path: str):
    folder = Path(folder_path)
    if not folder.exists():
        sys.exit(f"Folder not found: {folder_path}")

    pdfs = list(folder.rglob("*.pdf"))
    print(f"Scanning {len(pdfs)} PDFs for exact size matches...")
    print()

    by_size = defaultdict(list)
    for p in pdfs:
        size = p.stat().st_size
        by_size[size].append(p)

    duplicates = {size: paths for size, paths in by_size.items() if len(paths) > 1}

    if not duplicates:
        print("No duplicate file sizes found — no likely duplicates.")
        return

    print(f"Found {len(duplicates)} group(s) of files with identical size "
          f"(strong evidence of duplicate content):")
    print()

    for size, paths in sorted(duplicates.items(), key=lambda x: -x[0]):
        print(f"  {size:,} bytes — {len(paths)} files:")
        for p in paths:
            print(f"    {p.relative_to(folder)}")
        print()

    print("Real next step for each group: confirm which is the file "
          "currently active in the database, keep it (or the better-named "
          "one), delete the other(s), then run reprocess_file.py on "
          "whichever name is stale before the next scan_folder.py run.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python3.14 find_duplicate_files.py "/path/to/folder"')
    run(sys.argv[1])
