"""
Apply a reviewed rename_proposals.csv — actually renames files on disk.
Run ONLY after reviewing the CSV from propose_renames.py and confirming
every proposed name is correct.

SAFETY FEATURES:
- Dry-run by default — prints what would happen, touches nothing
- Skips rows marked REVIEW, SKIP, or with no proposed name
- Never overwrites an existing file
- Writes a permanent mapping log (rename_log.csv) afterward

Usage:
    # Dry run first — review output carefully
    python3.14 apply_renames.py rename_proposals.csv "/path/to/folder"

    # Apply for real once dry-run looks correct
    python3.14 apply_renames.py rename_proposals.csv "/path/to/folder" --apply
"""

import csv
import sys
from pathlib import Path


def run(csv_path: str, folder_path: str, apply: bool = False):
    folder = Path(folder_path)
    csv_file = Path(csv_path)

    if not folder.exists():
        sys.exit(f"Folder not found: {folder_path}")
    if not csv_file.exists():
        sys.exit(f"CSV not found: {csv_path}")

    mode = "APPLYING" if apply else "DRY RUN (pass --apply to actually rename)"
    print(f"=== {mode} ===")
    print(f"Folder: {folder_path}")
    print(f"CSV: {csv_path}")
    print()

    rows = list(csv.DictReader(csv_file.open()))
    to_rename = [
        r for r in rows
        if r.get("action") == "RENAME"
        and r.get("proposed_filename")
        and r.get("proposed_filename") != "— MANUAL RENAME NEEDED —"
    ]
    skipped = [r for r in rows if r.get("action") in ("SKIP (already valid)", "REVIEW")]

    print(f"{len(to_rename)} rename(s) to apply")
    print(f"{len(skipped)} file(s) skipped (already valid or marked REVIEW)")
    print()

    applied = []
    errors = []

    for row in to_rename:
        original = row["original_filename"]
        proposed = row["proposed_filename"]

        # Search recursively — files may be in subfolders
        matches = list(folder.rglob(original))
        if not matches:
            errors.append(f"NOT FOUND on disk: {original}")
            print(f"  NOT FOUND: {original}")
            continue

        src = matches[0]  # take the first match
        dst = src.parent / proposed

        if dst.exists():
            errors.append(f"WOULD OVERWRITE: {dst} — skipped")
            print(f"  SKIP (target exists): {proposed}")
            continue

        print(f"  {'RENAME' if apply else 'WOULD RENAME'}: {original}")
        print(f"    → {proposed}")

        if apply:
            src.rename(dst)
            applied.append({"original": original, "renamed_to": proposed,
                             "folder": str(src.parent)})

    print()
    if apply:
        print(f"Applied {len(applied)} rename(s)")
        if errors:
            print(f"{len(errors)} error(s):")
            for e in errors:
                print(f"  {e}")

        # Write the permanent mapping log
        log_path = Path("rename_log.csv")
        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["original", "renamed_to", "folder"])
            writer.writeheader()
            writer.writerows(applied)
        print(f"Permanent rename log written to: {log_path.absolute()}")
        print()
        print("Next step: run scan_folder.py to ingest the renamed files.")
    else:
        print("Dry run complete — no files were changed.")
        print("Run with --apply to actually rename.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(
            "Usage: python3.14 apply_renames.py rename_proposals.csv '/path/to/folder' [--apply]"
        )
    apply = "--apply" in sys.argv
    run(sys.argv[1], sys.argv[2], apply=apply)
