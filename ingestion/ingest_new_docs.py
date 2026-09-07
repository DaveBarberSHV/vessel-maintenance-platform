#!/usr/bin/env python3.14
"""Single-command wrapper for the full document ingestion workflow (Sept 2026).

Runs the complete pipeline in order:
  1. propose_renames.py  — scan for new files, propose names (with vision
                           fallback for unrecognised filenames)
  2. PAUSE               — open the CSV for human review
  3. apply_renames.py    — apply approved renames (dry run first, then --apply)
  4. find_duplicate_files.py — check for duplicates before ingesting
  5. scan_folder.py      — ingest new/changed files into the database

All env vars (VOYAGE_API_KEY, ANTHROPIC_API_KEY, SUPABASE_DB_URL,
SUPABASE_SERVICE_KEY, SUPABASE_URL) must be exported before running.

Usage (from the ingestion/ directory):
    python3.14 ingest_new_docs.py "/path/to/Vessel Maintenance System Documents"
"""
import os
import subprocess
import sys
from pathlib import Path

DRIVE_PATH = sys.argv[1] if len(sys.argv) > 1 else None
if not DRIVE_PATH:
    sys.exit(
        "Usage: python3.14 ingest_new_docs.py "
        "'/path/to/Vessel Maintenance System Documents'"
    )

HERE = Path(__file__).parent
PYTHON = sys.executable
CSV_PATH = Path.home() / "vessel-maintenance-platform" / "rename_proposals.csv"


def run(cmd, **kwargs):
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(f"Step failed — stopping. Fix the issue above and re-run.")
    return result


def pause(message: str):
    print()
    print("=" * 70)
    print(message)
    print("=" * 70)
    input("Press Enter when ready to continue...")
    print()


# ── Step 1: Propose renames ──────────────────────────────────────────────────
print("\n🔍 STEP 1 — Scanning for new files and proposing renames...\n")
run([PYTHON, str(HERE / "propose_renames.py"), DRIVE_PATH])

# ── Step 2: Human review ─────────────────────────────────────────────────────
# Open the CSV automatically if possible
try:
    subprocess.Popen(["open", "-e", str(CSV_PATH)])
except Exception:
    pass

pause(
    f"REVIEW REQUIRED\n\n"
    f"The CSV has been opened in TextEdit (or find it at):\n"
    f"  {CSV_PATH}\n\n"
    f"Check every row:\n"
    f"  • RENAME rows with 'vision-extracted' notes — verify the proposed name\n"
    f"  • Any rows still marked REVIEW — fill in the proposed_filename manually\n"
    f"  • Change action to RENAME when you're happy with a proposed name\n\n"
    f"Save the CSV, then press Enter to continue."
)

# ── Step 3: Dry run ──────────────────────────────────────────────────────────
print("📋 STEP 3 — Dry run (no files changed yet)...\n")
run([PYTHON, str(HERE / "apply_renames.py"), str(CSV_PATH), DRIVE_PATH])

pause(
    "DRY RUN COMPLETE\n\n"
    "Review the output above. If all renames look correct, press Enter\n"
    "to apply them. If anything looks wrong, Ctrl-C now and fix the CSV."
)

# ── Step 4: Apply renames ────────────────────────────────────────────────────
print("✏️  STEP 4 — Applying renames...\n")
run([PYTHON, str(HERE / "apply_renames.py"), str(CSV_PATH), DRIVE_PATH, "--apply"])

# ── Step 5: Duplicate check ──────────────────────────────────────────────────
print("\n🔎 STEP 5 — Checking for duplicates...\n")
run([PYTHON, str(HERE / "find_duplicate_files.py"), DRIVE_PATH])

pause(
    "DUPLICATE CHECK COMPLETE\n\n"
    "If any duplicates were found above, resolve them now (delete the\n"
    "extra copy from Drive) before continuing.\n\n"
    "If no duplicates, press Enter to start the ingest."
)

# ── Step 6: Ingest ───────────────────────────────────────────────────────────
print("🚀 STEP 6 — Ingesting new/changed files...\n")
run([PYTHON, str(HERE / "scan_folder.py"), DRIVE_PATH])

print("\n✅ All done. Review the ingest output above for any warnings.")
print("   Commit rename_log.csv to git to preserve the rename history:")
print("   cd ~/vessel-maintenance-platform && git add rename_log.csv && git commit -m 'Update rename log'")
