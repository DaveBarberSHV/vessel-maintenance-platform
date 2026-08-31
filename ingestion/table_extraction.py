"""
Structured table extraction — recovers marker/checkbox cells that plain
text extraction loses.

Problem this solves (see BACKLOG.md "Checkbox/marker tables lose their
meaning in text extraction"): tables like the MCH6 manual's maintenance
schedule use a small filled dot/image to mark which interval column a job
applies to. Plain text extraction (pdftotext, or extract_tables() alone)
sees the job names and column headers, but the dot itself is a drawn
image, not a text character — so the extracted text has no way to say
which column a job belongs to. An answer built from that flattened text
can only guess.

This module cross-references each table's cell boundaries (from
pdfplumber's find_tables()) against the position of small marker images on
the page, and reports exactly which cell each marker falls inside. That
turns "we can't tell which column this job applies to" into a verified
fact taken directly from the document's structure — not an LLM guess.

Usage:
    python table_extraction.py <path/to.pdf> <page_number>   # 1-indexed page, for manual testing
"""

import re
import sys

import pdfplumber

# Markers observed in the MCH6 manual are ~8.6-9.6pt square. A generous
# threshold catches similar small marker images without also catching
# larger embedded figures/photos, which are what we want to leave alone.
MAX_MARKER_SIZE = 20


def find_markers(page) -> list[dict]:
    """Return center points of small images on the page — candidate
    checkbox/dot markers, as opposed to larger embedded figures."""
    markers = []
    for img in page.images:
        width = img["x1"] - img["x0"]
        height = img["bottom"] - img["top"]
        if width <= MAX_MARKER_SIZE and height <= MAX_MARKER_SIZE:
            markers.append({
                "cx": (img["x0"] + img["x1"]) / 2,
                "cy": (img["top"] + img["bottom"]) / 2,
            })
    return markers


def extract_structured_tables(page) -> list[list[list[str]]]:
    """Returns tables as a list of rows of cell strings, with marker cells
    filled in as 'X' — recovering information plain extract_tables() would
    leave blank."""
    markers = find_markers(page)
    tables = page.find_tables()
    results = []

    for table in tables:
        text_rows = table.extract()  # text-only version, for cell labels
        structured_rows = []

        for row_idx, row in enumerate(table.rows):
            row_out = []
            for col_idx, cell_bbox in enumerate(row.cells):
                text = (text_rows[row_idx][col_idx] or "").strip()
                if cell_bbox is None:
                    row_out.append(text)
                    continue
                x0, top, x1, bottom = cell_bbox
                has_marker = any(
                    x0 <= m["cx"] <= x1 and top <= m["cy"] <= bottom
                    for m in markers
                )
                if has_marker and not text:
                    row_out.append("X")
                else:
                    row_out.append(text)
            structured_rows.append(row_out)
        results.append(structured_rows)

    return results


def render_as_markdown(rows: list[list[str]]) -> str:
    """Render a structured table as markdown — this is what should go
    into the chunk text instead of the flattened, ambiguous version."""
    if not rows:
        return ""
    header = rows[0]
    lines = ["| " + " | ".join(h.replace("\n", " ") for h in header) + " |"]
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(c.replace("\n", " ") for c in row) + " |")
    return "\n".join(lines)


def parse_markdown_tables(text: str) -> list[list[list[str]]]:
    """Finds and parses markdown-formatted tables (| cell | cell | ...)
    within a block of text, returning them in the same structured row
    shape extract_structured_tables() produces for native PDF tables —
    [[header_cells], [row1_cells], [row2_cells], ...] per table — so
    parse_and_chunk.split_dense_tables() can be reused unchanged on
    output from either source.

    Built for vision-transcribed pages (Aug 2026, see BACKLOG.md's DEF
    alarm entry): Claude's vision output naturally includes markdown
    tables for genuinely tabular content — its own choice when
    transcribing something structured, never explicitly requested in the
    vision prompt — confirmed directly by inspecting real production
    output. This parses that markdown back into structured rows rather
    than treating vision output as flat, undifferentiated prose, which
    was the real root cause of a dense multi-code alarm table becoming
    one large, hard-to-retrieve chunk: its single embedding had to
    represent eight unrelated alarm codes at once, diluting relevance to
    a question about any single one."""
    tables = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and line.endswith("|") and len(line) > 1:
            table_lines = []
            while i < len(lines):
                candidate = lines[i].strip()
                if candidate.startswith("|") and candidate.endswith("|") and len(candidate) > 1:
                    table_lines.append(candidate)
                    i += 1
                else:
                    break
            # A real markdown table needs at least a header row and a
            # separator row (|---|---|...|) right after it — anything
            # shorter is just a stray line that happens to contain pipe
            # characters, not an actual table.
            if len(table_lines) >= 2 and re.match(r"^\|[\s\-:|]+\|$", table_lines[1]):
                rows = []
                for tl in [table_lines[0]] + table_lines[2:]:
                    cells = [c.strip() for c in tl.strip("|").split("|")]
                    rows.append(cells)
                if rows:
                    tables.append(rows)
        else:
            i += 1
    return tables


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python table_extraction.py <path/to.pdf> <page_number>")
    path, page_num = sys.argv[1], int(sys.argv[2])
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_num - 1]
        tables = extract_structured_tables(page)
        print(f"Found {len(tables)} table(s) on page {page_num}\n")
        for i, rows in enumerate(tables):
            print(f"--- Table {i+1} ---")
            print(render_as_markdown(rows))
            print()
