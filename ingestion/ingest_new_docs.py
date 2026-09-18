#!/usr/bin/env python3.14
"""Single-command wrapper for the full document ingestion workflow (Sept 2026).

Runs the complete pipeline in order:
  0. List what's currently in the inbox
  1. propose_renames.py  — scan the INBOX for new files, propose names
                           (with vision fallback for unrecognised filenames)
  2. PAUSE               — open the CSV for human review
  3. apply_renames.py    — apply approved renames (dry run first, then --apply),
                           renaming IN PLACE within the inbox
  4. find_duplicate_files.py — check for duplicates across the WHOLE library
                           (not just the inbox — catches an inbox file that
                           duplicates something already filed elsewhere)
  5. scan_folder.py      — ingest new/changed files, scoped to the inbox
  6. File each successfully-renamed-and-ingested document into
     "Vessel Library/<System>/", based on the System column in the reviewed
     CSV — creating the system subfolder if it doesn't exist yet. Anything
     marked SKIP, still marked REVIEW, or that didn't actually make it into
     manifest.json after ingest is deliberately left in the inbox rather than
     filed, with a clear note printed at the end.

Real workflow change (Sept 2026, Dave's own redesign with Jared, thought
through separately first): the goal is that Jared drops a file in the
inbox, Dave runs this one command, and the file gets renamed, ingested,
and filed into the correct Vessel Library subfolder automatically — no
separate manual filing step. Confirmed directly (Sept 2026) that neither
"New Documents - Inbox" nor "Vessel Library" exist yet in the real Drive
folder — this is a new structure being introduced, not a rename of an
existing one, so both get created as needed rather than assumed to exist.

All env vars (VOYAGE_API_KEY, ANTHROPIC_API_KEY, SUPABASE_DB_URL,
SUPABASE_SERVICE_KEY, SUPABASE_URL) must be exported before running.

Usage (from the ingestion/ directory):
    python3.14 ingest_new_docs.py "/path/to/Vessel Maintenance System Documents"
    python3.14 ingest_new_docs.py "/path/to/..." --inbox "/path/to/some/other/inbox"
"""
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PYTHON = sys.executable
# Real bug found live (Sept 2026) while testing the inbox workflow for the
# first time: this used to point at the repo root
# (~/vessel-maintenance-platform/rename_proposals.csv), which only agreed
# with where propose_renames.py actually writes the CSV (cwd-relative) by
# coincidence of which directory the wrapper happened to be run from. A
# real, 12-day-stale CSV from an unrelated earlier batch was already
# sitting at the old repo-root location — reviewing or reading that
# instead of a fresh proposal would have silently acted on the wrong
# file. Now anchored to this script's own directory, matching
# propose_renames.py's now-identically-fixed output path and
# scan_folder.py's MANIFEST_PATH pattern — correct regardless of cwd.
CSV_PATH = HERE / "rename_proposals.csv"
# Always resolved relative to this script's own location (matches
# scan_folder.py's identical MANIFEST_PATH), so it's correct regardless of
# the current working directory.
MANIFEST_PATH = HERE / "manifest.json"

INBOX_DIR_NAME = "New Documents - Inbox"
VESSEL_LIBRARY_DIR_NAME = "Vessel Library"


def parse_args(argv: list[str]) -> tuple[str, str]:
    args = list(argv)
    inbox_override = None
    if "--inbox" in args:
        idx = args.index("--inbox")
        if idx + 1 >= len(args):
            sys.exit("--inbox requires a path argument.")
        inbox_override = args[idx + 1]
        del args[idx:idx + 2]

    if not args:
        sys.exit(
            "Usage: python3.14 ingest_new_docs.py "
            "'/path/to/Vessel Maintenance System Documents' [--inbox '/path/to/inbox']"
        )
    drive_path = args[0]
    inbox_path = inbox_override or str(Path(drive_path) / INBOX_DIR_NAME)
    return drive_path, inbox_path


DRIVE_PATH, INBOX_PATH = parse_args(sys.argv[1:])
VESSEL_LIBRARY_PATH = Path(DRIVE_PATH) / VESSEL_LIBRARY_DIR_NAME


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


def file_into_vessel_library(csv_path: Path, manifest_path: Path,
                              inbox_path: Path, vessel_library_path: Path) -> dict:
    """Reads the reviewed CSV and the fresh post-ingest manifest.json, and
    moves each successfully-renamed-and-ingested file from the inbox into
    Vessel Library/<System>/, creating that subfolder if needed.

    Deliberately conservative about what counts as "successful," since
    this is a real, hard-to-reverse move on Dave's actual Drive folder,
    not a local scratch file:
    - SKIP rows (either the automatic "SKIP (already valid)" or a manual
      "SKIP" Dave wrote in during review) are left in the inbox untouched.
    - REVIEW rows were never renamed at all (apply_renames.py already
      skips them) — left in the inbox.
    - A RENAME row whose proposed filename doesn't actually appear in
      manifest.json after scan_folder.py ran is NOT moved — that means
      ingestion didn't record success for it (e.g. a validation failure),
      and filing it away would hide a real problem rather than surface it.
    - Never overwrites an existing file at the destination.

    Takes all paths as explicit parameters (rather than reading module
    globals directly) specifically so this can be unit-tested in an
    isolated sandbox before ever running near Dave's real Drive folder —
    this function performs real, hard-to-reverse file moves.

    Returns a dict of {"filed": [...], "left_in_inbox": [...],
    "ingest_failed": [...]} for both printing and testing."""
    if not csv_path.exists():
        print(f"  No CSV found at {csv_path} — skipping the filing step.")
        return {"filed": [], "left_in_inbox": [], "ingest_failed": []}

    with open(csv_path, newline="") as f:
        csv_rows = list(csv.DictReader(f))

    manifest = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)

    inbox_folder = Path(inbox_path)
    filed, left_in_inbox, ingest_failed = [], [], []

    for row in csv_rows:
        action = (row.get("action") or "").strip()
        proposed = row.get("proposed_filename") or ""
        system = (row.get("system") or "").strip()
        original = row.get("original_filename") or proposed

        if action.upper().startswith("SKIP"):
            left_in_inbox.append((proposed or original, "marked SKIP in the CSV"))
            continue
        if action == "REVIEW":
            left_in_inbox.append((original, "still marked REVIEW — never renamed"))
            continue
        if action != "RENAME" or not proposed or proposed == "— MANUAL RENAME NEEDED —":
            continue  # not a real rename candidate — nothing to file

        src = inbox_folder / proposed
        if not src.exists():
            # Already filed by an earlier run of this script, or genuinely
            # not there — either way, don't fabricate a move that isn't real.
            continue

        if proposed not in manifest:
            ingest_failed.append(proposed)
            continue

        if not system:
            left_in_inbox.append((proposed, "no System value in the CSV — needs manual filing"))
            continue

        dest_folder = vessel_library_path / system
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest = dest_folder / proposed
        if dest.exists():
            left_in_inbox.append((proposed, f"a file already exists at {dest} — not overwritten"))
            continue

        shutil.move(str(src), str(dest))
        filed.append((proposed, str(dest_folder)))

    print(f"  {len(filed)} file(s) filed into {vessel_library_path}:")
    for name, folder in filed:
        print(f"    {name} → {folder}")

    if ingest_failed:
        print(f"\n  {len(ingest_failed)} file(s) renamed but NOT found in manifest.json after ingest")
        print("  (ingestion may have failed for these) — left in inbox, needs a look:")
        for name in ingest_failed:
            print(f"    {name}")

    if left_in_inbox:
        print(f"\n  {len(left_in_inbox)} file(s) left in inbox:")
        for name, reason in left_in_inbox:
            print(f"    {name} — {reason}")

    return {"filed": filed, "left_in_inbox": left_in_inbox, "ingest_failed": ingest_failed}


# ── Step -1: Credential pre-flight check ─────────────────────────────────────
# Real incident (Sept 2026): SUPABASE_URL/SUPABASE_SERVICE_KEY were missing
# for a whole real ingestion run, and the resulting "no page images"
# warning (buried mid-scan_folder.py output) went unnoticed until someone
# tried to view a source page days later. Checked here too, at the very
# start, before any of the 7 steps below run, specifically so this is the
# first thing seen — not a hard requirement (ingestion still fully works
# without it, same reasoning as scan_folder.py itself), just impossible to
# miss now.
_missing_for_images = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(v)]
if _missing_for_images:
    print("\n" + "=" * 70)
    print(f"⚠️  {' and '.join(_missing_for_images)} not set — page images will")
    print("    NOT be created for anything ingested this run. Text/embeddings")
    print("    are unaffected, but 'View Sources' will show no viewable image")
    print("    for these documents until backfill_page_images.py is run later.")
    print("=" * 70)
    pause("Continue anyway, or Ctrl-C to fix credentials first.")

# ── Step 0: List inbox contents ──────────────────────────────────────────────
inbox_folder = Path(INBOX_PATH)
if not inbox_folder.exists():
    sys.exit(
        f"Inbox folder not found: {INBOX_PATH}\n"
        f"Create it in Drive first (default name: '{INBOX_DIR_NAME}', as a "
        f"sibling of '{VESSEL_LIBRARY_DIR_NAME}' inside the main Drive "
        f"folder), or pass --inbox with the real path."
    )

inbox_pdfs = sorted(inbox_folder.rglob("*.pdf"))
print(f"\n📥 STEP 0 — Inbox: {INBOX_PATH}\n")
if not inbox_pdfs:
    sys.exit("No PDF files found in the inbox — nothing to do.")
print(f"  {len(inbox_pdfs)} file(s) found:")
for p in inbox_pdfs:
    print(f"    {p.name}")

# ── Step 1: Propose renames (scoped to the inbox only) ───────────────────────
print("\n🔍 STEP 1 — Scanning the inbox and proposing renames...\n")
run([PYTHON, str(HERE / "propose_renames.py"), INBOX_PATH])

# ── Step 2: Human review ─────────────────────────────────────────────────────
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
    f"  • Change action to RENAME when you're happy with a proposed name\n"
    f"  • Change action to SKIP for anything you don't want ingested this\n"
    f"    round — it will be left in the inbox untouched\n\n"
    f"Save the CSV, then press Enter to continue."
)

# ── Step 3: Dry run ──────────────────────────────────────────────────────────
print("📋 STEP 3 — Dry run (no files changed yet)...\n")
run([PYTHON, str(HERE / "apply_renames.py"), str(CSV_PATH), INBOX_PATH])

pause(
    "DRY RUN COMPLETE\n\n"
    "Review the output above. If all renames look correct, press Enter\n"
    "to apply them. If anything looks wrong, Ctrl-C now and fix the CSV."
)

# ── Step 4: Apply renames (in place, within the inbox) ───────────────────────
print("✏️  STEP 4 — Applying renames...\n")
run([PYTHON, str(HERE / "apply_renames.py"), str(CSV_PATH), INBOX_PATH, "--apply"])

# ── Step 5: Duplicate check (whole library — catches inbox-vs-filed dupes) ──
print("\n🔎 STEP 5 — Checking for duplicates across the whole library...\n")
run([PYTHON, str(HERE / "find_duplicate_files.py"), DRIVE_PATH])

pause(
    "DUPLICATE CHECK COMPLETE\n\n"
    "If any duplicates were found above, resolve them now (delete the\n"
    "extra copy from Drive) before continuing.\n\n"
    "If no duplicates, press Enter to start the ingest."
)

# ── Step 6: Ingest (scoped to the inbox — files are still there, renamed) ───
print("🚀 STEP 6 — Ingesting new/changed files from the inbox...\n")
run([PYTHON, str(HERE / "scan_folder.py"), INBOX_PATH])

# ── Step 7: File successfully-ingested documents into Vessel Library ────────
print("\n📁 STEP 7 — Filing ingested documents into Vessel Library...\n")
file_into_vessel_library(CSV_PATH, MANIFEST_PATH, Path(INBOX_PATH), VESSEL_LIBRARY_PATH)

print("\n✅ All done. Review the output above for any warnings.")
print("   Commit rename_log.csv to git to preserve the rename history:")
print("   cd ~/vessel-maintenance-platform && git add rename_log.csv && git commit -m 'Update rename log'")
