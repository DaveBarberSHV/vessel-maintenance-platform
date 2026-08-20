"""
Prototype ingestion pipeline: parse -> chunk (by page/section) -> tag with metadata.

Design note on parsing:
  In production, source files will be ordinary PDFs and should be parsed with
  pdftotext / pdfplumber directly. The three files in this project happen to be
  stored by the platform in a pre-processed container (per-page JPEG + extracted
  text + manifest.json) rather than as raw PDF bytes, so this script reads that
  container when present and falls back to pdftotext for a normal PDF. Either
  path produces the same output structure, so the chunking/tagging logic below
  is what would actually ship in the backend.
"""

import json
import re
import subprocess
import zipfile
from pathlib import Path
from dataclasses import dataclass, asdict


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


def extract_pages(path: Path):
    """Return list of (page_number, text) tuples. Tries platform container
    format first, falls back to pdftotext for genuine PDFs."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "manifest.json" in names:
                manifest = json.loads(z.read("manifest.json"))
                pages = []
                for p in manifest["pages"]:
                    txt_path = p.get("text", {}).get("path")
                    text = z.read(txt_path).decode("utf-8", errors="replace") if txt_path else ""
                    pages.append((p["page_number"], text))
                return pages
    except zipfile.BadZipFile:
        pass

    # Fallback: genuine PDF -> pdftotext, one page at a time
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", info)
    num_pages = int(m.group(1)) if m else 1
    pages = []
    for i in range(1, num_pages + 1):
        out = subprocess.run(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(path), "-"],
            capture_output=True, text=True,
        ).stdout
        pages.append((i, out))
    return pages


SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+([A-Z][A-Za-z0-9 /\-]{2,60})", re.MULTILINE)


def detect_section(text: str) -> str | None:
    """Heuristic: find the last numbered heading on the page (e.g. '4.5.1. Additional Functions')."""
    matches = SECTION_RE.findall(text)
    if not matches:
        return None
    num, title = matches[-1]
    return f"{num} {title.strip()}"


def chunk_document(path: Path, metadata: dict) -> list[Chunk]:
    pages = extract_pages(path)
    chunks = []
    for page_number, text in pages:
        section = detect_section(text)
        chunks.append(Chunk(
            chunk_id=f"{metadata['document_type']}-{metadata['equipment_model']}-p{page_number}".replace(" ", "_"),
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
        "path": Path("/mnt/project/7396A1__OP_AND_MAINT_MANUALMCH6.pdf"),
        "metadata": {
            "vessel": "Master Boat Builders Hull 469 (tug+barge combo)",
            "order_no": "7396A1",
            "equipment_model": "MCH6 Marine Clutch Highspeed",
            "document_type": "O&M Manual",
            "document_title": "Operation and Maintenance Manual - MCH Marine Clutch Highspeed 6",
            "revision": "unknown - not marked in filename or body; flag for confirmation",
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
]


if __name__ == "__main__":
    all_chunks = []
    for doc in DOCS:
        chunks = chunk_document(doc["path"], doc["metadata"])
        all_chunks.extend(chunks)
        text_pages = sum(1 for c in chunks if c.has_text_layer)
        print(f"{doc['path'].name}: {len(chunks)} pages -> {text_pages} with extractable text, "
              f"{len(chunks) - text_pages} requiring OCR/vision")

    out_path = Path("/home/claude/ingestion/chunks.jsonl")
    with open(out_path, "w") as f:
        for c in all_chunks:
            f.write(json.dumps(asdict(c)) + "\n")
    print(f"\nWrote {len(all_chunks)} chunks to {out_path}")
