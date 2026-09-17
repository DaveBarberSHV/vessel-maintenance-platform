"""
Generates a multi-tab Excel spreadsheet summarizing the current state of
the vessel's document library — what's ingested, what equipment is known
to be missing a manual, and rough per-system completeness — so Dave can
send Jared a single, current picture after every ingest run.

Real investigation before building this (Sept 2026): the "Drawings" tab
was originally meant to also list known shipyard drawing numbers not yet
ingested, inferred by looking for gaps in each drawing series' own
numbering (A01, A02, A03...). Tested this directly against the real
library before shipping it: the real numbering has large, apparently
intentional gaps that have nothing to do with missing documents (e.g. the
Hull "S" series spans 01-71 with only 22 real drawings — a naive gap-fill
would report 49 "missing" drawing numbers, almost certainly false; the
Hull "S1101"/"S1202" level-reference codes aren't even a dense sequence at
all). Shipping that would actively mislead Dave into chasing shipyard
drawings that may never have existed. Deliberately NOT built — the
Drawings tab only lists what's actually ingested. Detecting real missing
drawings needs comparison against the shipyard's own real drawing
index/transmittal log, not a numbering-sequence guess.

Usage (from ingestion/, all env vars exported):
    python3.14 generate_library_status.py
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, ".")
from retrieval import get_pg_connection

# Matches scan_folder.py's own hardcoded VESSEL constant — this project
# is deliberately single-vessel for now (see BACKLOG.md's fleet-scaling
# entry); update both together if that ever changes.
VESSEL_NAME = "polaris"

# document_type values confirmed directly against the real library
# (Sept 2026) — see docs/vessel_onboarding_guide.md's Appendix A for the
# real doc-type-per-system targets this categorization is meant to match.
DRAWING_TYPES = {"General Arrangement Drawing", "Wiring Diagram"}
TM_TYPES = {"O&M Manual", "Parts List", "Service Bulletin"}
# Everything else (CALCS, Equipment List, Reference Data, SERVICEREPORT,
# and the various DEF* training/reference doctypes) -> Reference Docs.

GAP_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
HEADER_FILL = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def get_system(document_title: str) -> str:
    """document_title format is always "System - ..." (the naming
    convention guarantees this — see document_inventory.py's identical
    convention)."""
    return document_title.split(" - ")[0].strip()


def fetch_documents(conn) -> list[dict]:
    """One row per distinct document (title + revision), with its real
    chunk count — the actual, current state of tm_chunks, not a cached
    or assumed picture."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT document_title, document_type, revision, source_file, count(*) AS chunk_count
            FROM tm_chunks
            GROUP BY document_title, document_type, revision, source_file
            ORDER BY document_title
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def find_equipment_manifest_csv() -> str | None:
    """Prefers a reviewed, promoted copy in docs/ (per
    docs/vessel_onboarding_guide.md's naming convention) over the scratch
    default build_equipment_manifest.py writes to ingestion/. Returns
    None if neither exists — this script needs to run cleanly right after
    a fresh ingest, before anyone's necessarily run the manifest tool yet,
    so a missing manifest must degrade gracefully, not crash."""
    candidates = [
        f"../docs/equipment_manifest_{VESSEL_NAME}.csv",
        "equipment_manifest_gap_report.csv",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def load_equipment_manifest() -> list[dict]:
    path = find_equipment_manifest_csv()
    if not path:
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    # Real bug caught before shipping (Sept 2026): using the SOURCE
    # DRAWING's own system prefix (e.g. "GeneralArrangement") bucketed
    # every single piece of equipment under one system, since every
    # manifest entry currently comes from GeneralArrangement-prefixed
    # arrangement drawings — completely defeating the point of a
    # per-system breakdown. system_location is the equipment's own real
    # system as the drawing itself describes it (e.g. "Main Engine /
    # Engine Room", "Fresh Water System") — take the segment before " / "
    # as the system, the rest as location detail. This is real, direct
    # signal from the source, not a guess — but it's the drawing's own
    # free-text wording, not the same canonical System names tm_chunks
    # documents use (see build_summary_tab's explanatory note).
    for r in rows:
        location = (r.get("system_location") or "").strip()
        r["system"] = location.split(" / ")[0].strip() if location else "Unknown"
    return rows


def style_header_row(ws, ncols: int):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"


def autofit_columns(ws, widths: list[int]):
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def build_equipment_tab(wb: Workbook, manifest_rows: list[dict]):
    ws = wb.create_sheet("Equipment & TMs")
    headers = ["System", "Equipment", "Manufacturer", "Model", "Manual in Fathom (Y/N)",
               "Document Title", "Gap Notes"]
    ws.append(headers)
    style_header_row(ws, len(headers))

    if not manifest_rows:
        ws.append(["No equipment manifest found — run build_equipment_manifest.py first, "
                   "then re-run this script.", "", "", "", "", "", ""])
        autofit_columns(ws, [50, 15, 15, 15, 20, 40, 50])
        return

    for r in sorted(manifest_rows, key=lambda r: (r["system"], r.get("manufacturer") or "", r.get("model") or "")):
        status = r.get("status")
        in_fathom = "Y" if status == "covered" else "N"
        doc_title = r.get("matched_document", "") if status == "covered" else ""
        if status == "gap":
            gap_notes = ("In vessel_equipment registry, but no manual ingested" if r.get("in_registry") == "True"
                         else "Not in vessel_equipment registry either — no manual ingested")
        elif status == "unknown":
            gap_notes = "No manufacturer/model stated on the source drawing — not checkable, not a confirmed gap"
        else:
            gap_notes = ""
        row = [r["system"], r.get("item_designation", ""), r.get("manufacturer", ""),
               r.get("model", ""), in_fathom, doc_title, gap_notes]
        ws.append(row)
        if status in ("gap", "unknown"):
            for col in range(1, len(headers) + 1):
                ws.cell(row=ws.max_row, column=col).fill = GAP_FILL

    autofit_columns(ws, [20, 30, 20, 20, 20, 45, 55])


def build_drawings_tab(wb: Workbook, documents: list[dict]):
    ws = wb.create_sheet("Drawings")
    headers = ["System", "Drawing Number", "Title", "In Fathom (Y/N)", "Gap Notes"]
    ws.append(headers)
    style_header_row(ws, len(headers))

    drawings = [d for d in documents if d["document_type"] in DRAWING_TYPES]
    for d in sorted(drawings, key=lambda d: (get_system(d["document_title"]), d["document_title"])):
        # Drawing number extraction is display-only here (not used for any
        # gap inference — see the module docstring for why that was tried
        # and deliberately not shipped). Falls back to blank for
        # vendor-numbered drawings (Berg, Logan, South Coast Electric,
        # etc.) that don't follow the shipyard's own A/S/P/E/C/T-series
        # numbering.
        import re
        m = re.search(r"_MBB_([A-Z]{1,2}\d+)", d["source_file"])
        drawing_number = m.group(1) if m else ""
        ws.append([get_system(d["document_title"]), drawing_number, d["document_title"], "Y", ""])

    if not drawings:
        ws.append(["No drawings ingested yet.", "", "", "", ""])

    autofit_columns(ws, [20, 16, 60, 16, 40])


def build_reference_tab(wb: Workbook, documents: list[dict]):
    ws = wb.create_sheet("Reference Docs")
    headers = ["System", "Document Type", "Title", "In Fathom (Y/N)", "Gap Notes"]
    ws.append(headers)
    style_header_row(ws, len(headers))

    reference_docs = [d for d in documents
                       if d["document_type"] not in DRAWING_TYPES and d["document_type"] not in TM_TYPES]
    for d in sorted(reference_docs, key=lambda d: (get_system(d["document_title"]), d["document_title"])):
        ws.append([get_system(d["document_title"]), d["document_type"], d["document_title"], "Y", ""])

    if not reference_docs:
        ws.append(["No reference documents ingested yet.", "", "", "", ""])

    autofit_columns(ws, [20, 25, 60, 16, 40])


def build_summary_tab(wb: Workbook, documents: list[dict], manifest_rows: list[dict], total_chunks: int):
    ws = wb.create_sheet("Summary", 0)  # index 0 -> first tab, the real landing page

    ws.append([f"Last Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"])
    ws.append([f"Total chunks in tm_chunks: {total_chunks}"])
    ws.append([])
    ws.append(["\"Total Expected\"/\"% Complete\" below are derived only from equipment "
               "the manifest tool could actually check (a named manufacturer/model on a "
               "drawing) — not an externally-verified target. See "
               "ingestion/build_equipment_manifest.py."])
    ws.append(["\"System\" below is the equipment's own real-world system as the source "
               "drawing describes it (e.g. \"Main Engine\", \"Fresh Water System\") — NOT "
               "the same System names used to organize the document library in the "
               "Drawings/Reference Docs tabs (e.g. \"MainEngines\", \"Piping\"). The two "
               "haven't been reconciled; don't expect the names to match row-for-row."])
    ws.append([])

    header_row = ws.max_row + 1
    headers = ["System", "Total Expected", "In Fathom", "% Complete", "Priority Gaps"]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT

    by_system = defaultdict(lambda: {"covered": 0, "gap": 0, "gap_items": []})
    for r in manifest_rows:
        status = r.get("status")
        if status not in ("covered", "gap"):
            continue
        by_system[r["system"]][status] += 1
        if status == "gap":
            label = f"{r.get('manufacturer') or ''} {r.get('model') or ''}".strip() or r.get("item_designation", "?")
            by_system[r["system"]]["gap_items"].append(label)

    # Deliberately NOT merged with tm_chunks' own System list (real bug
    # caught before shipping this: every manifest entry currently comes
    # from GeneralArrangement-prefixed drawings, so a naive merge either
    # bucketed everything under "GeneralArrangement" or produced a wall
    # of unrelated N/A rows for document-library systems with zero
    # checkable equipment). This table is scoped to systems the manifest
    # actually has equipment data for.
    for system in sorted(by_system.keys()):
        stats = by_system[system]
        total = stats["covered"] + stats["gap"]
        pct = f"{round(100 * stats['covered'] / total)}%" if total else "N/A"
        priority = "; ".join(stats["gap_items"][:3])
        if len(stats["gap_items"]) > 3:
            priority += f" (+{len(stats['gap_items']) - 3} more)"
        row = [system, total, stats["covered"], pct, priority]
        ws.append(row)
        if total and stats["gap"] > 0:
            for col in range(1, len(headers) + 1):
                ws.cell(row=ws.max_row, column=col).fill = GAP_FILL

    autofit_columns(ws, [20, 16, 12, 12, 60])
    for row in ws.iter_rows(min_row=1, max_row=5, max_col=1):
        for cell in row:
            cell.alignment = WRAP


def main():
    conn = get_pg_connection()
    documents = fetch_documents(conn)
    total_chunks = sum(d["chunk_count"] for d in documents)
    conn.close()

    manifest_rows = load_equipment_manifest()
    if not manifest_rows:
        print("No equipment manifest CSV found (checked docs/equipment_manifest_"
              f"{VESSEL_NAME}.csv and ingestion/equipment_manifest_gap_report.csv) — "
              "the Equipment & TMs tab and Summary tab will note this rather than "
              "showing equipment data. Run build_equipment_manifest.py first for a "
              "complete picture.")

    wb = Workbook()
    wb.remove(wb.active)  # drop the default blank sheet; build_summary_tab creates its own as sheet 0
    build_summary_tab(wb, documents, manifest_rows, total_chunks)
    build_equipment_tab(wb, manifest_rows)
    build_drawings_tab(wb, documents)
    build_reference_tab(wb, documents)

    out_path = f"../docs/library_status_{VESSEL_NAME}.xlsx"
    wb.save(out_path)
    print(f"\n{len(documents)} distinct documents, {total_chunks} total chunks.")
    print(f"Library status spreadsheet written to {out_path}")


if __name__ == "__main__":
    main()
