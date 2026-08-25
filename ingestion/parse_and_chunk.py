"""
Prototype ingestion pipeline: parse -> chunk (by page/section) -> tag with metadata.

Design note on parsing:
  Source files should be ordinary PDFs. For genuine PDFs, this script uses
  pdfplumber for both plain text AND structured table extraction — the
  latter recovers marker/checkbox cells (see table_extraction.py) that
  plain text extraction alone would leave ambiguous, which turned out to
  matter a lot in practice (see BACKLOG.md's checkbox-table entry). A
  handful of files in this project were stored by the platform in a
  pre-processed container (per-page JPEG + extracted text + manifest.json)
  rather than as raw PDF bytes — that path is kept as a fallback since it
  doesn't support table structure recovery, only plain text.
"""

import json
import re
import subprocess
import zipfile
from pathlib import Path
from dataclasses import dataclass, asdict

import pdfplumber

from table_extraction import extract_structured_tables, render_as_markdown


@dataclass
class Chunk:
    chunk_id: str
    text: str
    vessel: str
    order_no: str
    equipment_model: str
    document_type: str
    document_title: str
    revision: str
    source_file: str
    page_number: int
    section: str | None
    has_text_layer: bool
    total_pages: int  # for citations like "p. 672 of 1415" — the PDF's
    # own physical page position, not the document's printed page number
    # (which can differ due to cover pages/front matter — see BACKLOG.md)


def extract_pages(path: Path):
    """Return list of (page_number, text, tables) tuples. Tries platform
    container format first (plain text only, tables=[] since structure
    recovery needs real PDF bytes), falls back to pdfplumber for genuine
    PDFs (plain text + structured table recovery)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "manifest.json" in names:
                manifest = json.loads(z.read("manifest.json"))
                pages = []
                for p in manifest["pages"]:
                    txt_path = p.get("text", {}).get("path")
                    text = z.read(txt_path).decode("utf-8", errors="replace") if txt_path else ""
                    pages.append((p["page_number"], text, []))
                return pages
    except zipfile.BadZipFile:
        pass

    # Fallback: genuine PDF -> pdfplumber, one page at a time. Text and
    # table structure both come from the same library here, deliberately —
    # pdftotext alone can't recover marker/checkbox cells.
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            tables = extract_structured_tables(page)
            if tables:
                table_blocks = "\n\n".join(render_as_markdown(t) for t in tables)
                text = f"{text}\n\n{table_blocks}"

            pages.append((i, text, tables))
    return pages


SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+([A-Z][A-Za-z0-9 /\-]{2,60})", re.MULTILINE)


def detect_section(text: str) -> str | None:
    """Heuristic: find the last numbered heading on the page (e.g. '4.5.1. Additional Functions')."""
    matches = SECTION_RE.findall(text)
    if not matches:
        return None
    num, title = matches[-1]
    return f"{num} {title.strip()}"


# Table-aware chunking (see BACKLOG.md, "Dense spec tables get lost in
# page-level chunks" — the original motivating case: a page-level chunk
# mixing many kinds of values (temperatures, voltages, standard numbers)
# buried the one fact a query needed among dozens of equally-rare terms).
# Tables at or below ROW_THRESHOLD data rows are left as part of the
# page-level chunk only — they're not dense enough to cause the problem.
# Tables above it also get split into smaller standalone chunks, each with
# the header row repeated for column-label context, so a query can match a
# single row (or small group of rows) directly instead of competing with
# every other row on the page.
TABLE_ROW_THRESHOLD = 8
TABLE_ROW_GROUP_SIZE = 6


def split_dense_tables(tables: list, page_number: int) -> list[str]:
    """Return standalone chunk texts for any table on this page dense
    enough to need splitting. Small tables are skipped here since they're
    already fully represented in the page-level chunk."""
    extra_chunks = []
    for table_idx, rows in enumerate(tables, start=1):
        if not rows:
            continue
        header, data_rows = rows[0], rows[1:]
        if len(data_rows) <= TABLE_ROW_THRESHOLD:
            continue
        for start in range(0, len(data_rows), TABLE_ROW_GROUP_SIZE):
            group = data_rows[start:start + TABLE_ROW_GROUP_SIZE]
            row_range = f"rows {start + 1}-{start + len(group)} of {len(data_rows)}"
            md = render_as_markdown([header] + group)
            extra_chunks.append(f"(Table {table_idx} on page {page_number}, {row_range})\n\n{md}")
    return extra_chunks


def chunk_document(path: Path, metadata: dict) -> list[Chunk]:
    # chunk_id is derived from the actual filename (guaranteed unique under
    # the naming convention) + page number — NOT from document_type +
    # equipment_model, which can collide across multiple files that share
    # the same doctype/model (e.g. several RefData reports for one part).
    # See BACKLOG.md for the case that surfaced this.
    file_stem = re.sub(r"[^A-Za-z0-9]+", "_", path.stem)
    pages = extract_pages(path)
    total_pages = len(pages)
    chunks = []
    for page_number, text, tables in pages:
        section = detect_section(text)
        chunks.append(Chunk(
            chunk_id=f"{file_stem}-p{page_number}",
            text=text.strip(),
            vessel=metadata["vessel"],
            order_no=metadata["order_no"],
            equipment_model=metadata["equipment_model"],
            document_type=metadata["document_type"],
            document_title=metadata["document_title"],
            revision=metadata["revision"],
            source_file=path.name,
            page_number=page_number,
            section=section,
            has_text_layer=bool(text.strip()),
            total_pages=total_pages,
        ))
        for table_idx, extra_text in enumerate(split_dense_tables(tables, page_number), start=1):
            chunks.append(Chunk(
                chunk_id=f"{file_stem}-p{page_number}-densetable{table_idx}",
                text=extra_text,
                vessel=metadata["vessel"],
                order_no=metadata["order_no"],
                equipment_model=metadata["equipment_model"],
                document_type=metadata["document_type"],
                document_title=metadata["document_title"],
                revision=metadata["revision"],
                source_file=path.name,
                page_number=page_number,
                section=section,
                has_text_layer=True,
                total_pages=total_pages,
            ))
    return chunks


DOCS = [
    {
        "path": Path("/mnt/project/BergPropulsion_MPC_OMM_7396A_RevA.pdf"),
        "metadata": {
            "vessel": "Master Boat Builders Hull 469 (tug+barge combo)",
            "order_no": "7396A1",
            "equipment_model": "MPC 800A Marine Propulsion Control",
            "document_type": "O&M Manual",
            "document_title": "Operation and Maintenance Manual - MPC 800A",
            "revision": "Rev A",
        },
    },
    {
        "path": Path("/mnt/user-data/uploads/7396A1_-_OP_AND_MAINT_MANUAL-MCH6.pdf"),
        "metadata": {
            "vessel": "Master Boat Builders Hull 469 (tug+barge combo)",
            "order_no": "7396A1",
            "equipment_model": "MCH6 Marine Clutch Highspeed",
            "document_type": "O&M Manual",
            "document_title": "Operation and Maintenance Manual - MCH Marine Clutch Highspeed 6",
            "revision": "Document Version 1 (May 24, 2021) - confirmed from title page of genuine PDF upload",
        },
    },
    {
        "path": Path("/mnt/project/70958__THRUSTER_ARAZIMUTH_Rev_I.pdf"),
        "metadata": {
            "vessel": "Master Boat Builders Hull 469 (tug+barge combo)",
            "order_no": "7396A1",
            "equipment_model": "MTA 524-FZ-03 Azimuth Thruster",
            "document_type": "General Arrangement Drawing",
            "document_title": "Thruster AR-Azimuth, Drawing 70958",
            "revision": "Rev I",
        },
    },
    {
        "path": Path("/path/to/your/local/TM-folder/CardanShafts_Gewes_All_OMM_Rev3.pdf"),
        "metadata": {
            "vessel": "Master Boat Builders Hull 469 (tug+barge combo)",
            "order_no": "7396A1",
            "equipment_model": "GEWES Cardan Shafts (All models)",
            "document_type": "O&M Manual",
            "document_title": "Einbau-Wartungs- und Lagervorschrift fur Gelenkwellen / "
                               "Recommendations for Installation, Maintenance and Storage of Cardan Shafts (bilingual DE/EN)",
            "revision": "Edition 03/2012 (13.03.2012) - confirmed from cover page, matches filename",
        },
    },
]


if __name__ == "__main__":
    all_chunks = []
    for doc in DOCS:
        chunks = chunk_document(doc["path"], doc["metadata"])
        all_chunks.extend(chunks)
        text_pages = sum(1 for c in chunks if c.has_text_layer)
        print(f"{doc['path'].name}: {len(chunks)} pages -> {text_pages} with extractable text, "
              f"{len(chunks) - text_pages} requiring OCR/vision")

    out_path = Path(__file__).parent / "chunks.jsonl"
    with open(out_path, "w") as f:
        for c in all_chunks:
            f.write(json.dumps(asdict(c)) + "\n")
    print(f"\nWrote {len(all_chunks)} chunks to {out_path}")
