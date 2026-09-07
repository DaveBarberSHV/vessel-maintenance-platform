#!/usr/bin/env python3.14
"""Queues all DWG (drawing) files in the library for reprocessing on the
next scan_folder.py run (Sept 2026 — tiled vision extraction).

Real motivating case: large-format engineering drawings (A1, A0 format)
were originally ingested with single-image vision extraction, which clamps
DPI below the threshold needed to read small text — equipment labels,
manufacturer names, valve tags, and part numbers were illegible.
Tiled extraction (see page_images.tile_page_for_vision()) now renders each
drawing region at full 300 DPI, but existing DWG chunks need to be cleared
so scan_folder.py treats them as new and re-runs vision extraction.

Usage (from the ingestion/ directory):
    export SUPABASE_DB_URL="..."
    export SUPABASE_SERVICE_KEY="..."
    export SUPABASE_URL="..."
    export ANTHROPIC_API_KEY="..."
    export VOYAGE_API_KEY="..."

    python3.14 reprocess_all_dwg.py
    # Then run the full ingest:
    python3.14 scan_folder.py "/path/to/Drive/Vessel Maintenance System Documents"
"""
import subprocess
import sys
from pathlib import Path

# Find reprocess_file.py in the same directory as this script
reprocess = Path(__file__).parent / "reprocess_file.py"
manifest = Path(__file__).parent / "manifest.json"

if not manifest.exists():
    sys.exit("manifest.json not found — run from the ingestion/ directory.")

import json
data = json.loads(manifest.read_text())

dwg_files = [f for f in data.keys() if "_DWG_" in f]

if not dwg_files:
    print("No DWG files found in manifest.json.")
    sys.exit(0)

print(f"Found {len(dwg_files)} DWG file(s) to queue for reprocessing:")
for f in sorted(dwg_files):
    print(f"  {f}")

print(f"\nQueuing all {len(dwg_files)} files...")
result = subprocess.run(
    [sys.executable, str(reprocess)] + dwg_files,
    capture_output=False
)

if result.returncode == 0:
    print(f"\nDone — {len(dwg_files)} DWG files queued.")
    print("Now run scan_folder.py to re-ingest them with tiled vision extraction.")
else:
    print(f"\nreprocess_file.py exited with code {result.returncode}")
    sys.exit(result.returncode)
