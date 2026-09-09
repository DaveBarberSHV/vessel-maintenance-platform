"""
Audits manifest.json against real tm_chunks contents in Postgres — the
standing version of a check that, until Sept 2026, only ever happened by
hand.

Real motivating case: reconciling library totals for CLAUDE.md and
docs/architecture.md surfaced 3 files that manifest.json recorded as
fully processed (with a chunk_id and everything) but that had zero
actual rows in tm_chunks — a silent partial failure where the manifest
got updated as if the Postgres write succeeded, but it hadn't. Separately,
a stale rename-undo left a file's real chunks sitting in tm_chunks with
no manifest entry at all — the reverse gap. Both were only found because
someone happened to compare the two by hand that day. This script makes
that comparison a one-command check instead.

Three things this looks for, all real cases that have actually happened:
  1. FULLY MISSING — manifest says a file is done, but none of its
     declared chunk_ids exist in tm_chunks at all. Nothing from the file
     is searchable. This is the unambiguous, always-worth-fixing case
     (confirmed real Sept 2026, see BACKLOG.md). Fix: reprocess_file.py,
     then scan_folder.py.
  2. PARTIAL — some, but not all, of a file's declared chunk_ids exist.
     Read this one with real judgment, not alarm — a small number
     missing relative to a large page count is usually just the
     expected "skipped — no text layer" behavior during indexing (a
     genuinely blank or logo-only page, already surfaced by
     scan_folder.py's own "indexed as metadata-only" warning at ingest
     time — nothing to fix). A large fraction missing, or a short
     document missing most of its content, is the real signal worth
     investigating with inspect_page.py.
  3. ORPHANED — chunks in tm_chunks whose source_file has no manifest
     entry at all. Not broken (still searchable), but untracked — a
     future scan_folder.py run won't recognize it as already done, and
     it won't get cleaned up automatically if the document is ever
     actually removed. Usually a leftover from a rename or a manual
     tm_chunks edit that didn't update manifest.json to match (confirmed
     real Sept 2026 — see BACKLOG.md's Electrical_Danfoss cleanup).

Read-only — reports findings, doesn't fix anything. Fixing is
reprocess_file.py's job (case 1/2) or a manual decision (case 3, since
"is this orphan actually stale" needs a human to confirm, as it did for
the Electrical_Danfoss case this script is named after).

Usage:
    python3.14 audit_manifest.py
"""

from retrieval import get_pg_connection
from scan_folder import load_manifest


def run():
    manifest = load_manifest()

    conn = get_pg_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, source_file FROM tm_chunks")
        rows = cur.fetchall()
    conn.close()

    chunk_ids_in_db = {chunk_id for chunk_id, _ in rows}
    source_files_in_db = {source_file for _, source_file in rows}

    fully_missing = []
    partial = []

    for filename, entry in manifest.items():
        declared = set(entry.get("chunk_ids", []))
        if not declared:
            continue
        missing = declared - chunk_ids_in_db
        if not missing:
            continue
        if missing == declared:
            fully_missing.append(filename)
        else:
            partial.append((filename, len(missing), len(declared)))

    orphaned = sorted(source_files_in_db - set(manifest.keys()))

    print(f"Manifest: {len(manifest)} file(s) | tm_chunks: {len(rows)} chunk(s) "
          f"across {len(source_files_in_db)} distinct source file(s)")
    print()

    if fully_missing:
        print(f"🔴 FULLY MISSING — {len(fully_missing)} file(s) marked done, "
              f"but nothing in tm_chunks at all:")
        for f in sorted(fully_missing):
            print(f"    {f}")
        print("  Fix: reprocess_file.py on these, then a normal scan_folder.py run.")
        print()

    if partial:
        print(f"🟡 PARTIAL — {len(partial)} file(s) missing some declared chunks:")
        for f, n_missing, n_declared in sorted(partial):
            pct = 100 * n_missing / n_declared
            print(f"    {f}  ({n_missing} of {n_declared} chunk(s) missing, {pct:.0f}%)")
        print("  Use judgment here: a small fraction missing from a large document")
        print("  is usually just the expected 'skipped — no text layer' behavior")
        print("  (a blank/logo-only page) — check scan_folder.py's own ingest-time")
        print("  warnings before assuming it's a bug. A high percentage, or a short")
        print("  document missing most of its content, is the real signal — confirm")
        print("  with inspect_page.py, then reprocess_file.py + scan_folder.py.")
        print()

    if orphaned:
        print(f"🟠 ORPHANED — {len(orphaned)} source file(s) in tm_chunks with no "
              f"manifest entry:")
        for f in orphaned:
            print(f"    {f}")
        print("  Not necessarily broken — confirm with a human before touching "
              "anything. If it's a stale leftover (e.g. an old name from a "
              "rename-undo), delete its chunks with retrieval.delete_chunks() "
              "and remove its page images from Storage. If it's legitimate, "
              "the safest fix is reprocessing it properly so manifest.json "
              "reflects it.")
        print()

    if not fully_missing and not partial and not orphaned:
        print("✅ Clean — every manifest file has all its declared chunks in "
              "tm_chunks, and every tm_chunks source file has a manifest entry.")


if __name__ == "__main__":
    run()
