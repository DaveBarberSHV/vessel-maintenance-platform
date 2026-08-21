# Backlog — "Fix Later" List

Things we've deliberately deferred so v1 doesn't stall. Each entry: what it is,
why it's deferred, and what would trigger picking it up.

---

## Citation precision: page-level → section-level

**What:** Citations currently resolve to `document + revision + page number`
(e.g. "MCH6 O&M Manual, p. 40"). The original target was section-level
(e.g. "§8.4, pp. 142–144").

**Why deferred:** A generic regex heading-detector was tried against the real
manuals and proved unreliable — it matched table-of-contents entries and
stray numbered sentences as if they were section headings, which would have
produced *wrong* citations that look precise. Page-level citation is 100%
reliable; false section precision is worse than no section precision.

**Path to fixing it:** Parse each document's actual table of contents once
per document to build a verified page→section map, rather than pattern-match
headings blindly on every page. Revisit once there's a real user (Jared)
generating query volume to justify the extra engineering.

---

## Azimuth thruster drawing has no text layer

**What:** `70958__THRUSTER_ARAZIMUTH_Rev_I.pdf` is a single-page vector CAD
drawing (general arrangement + BOM table). No extractable text — text
extraction returns nothing.

**Why deferred:** OCR/vision-based extraction of dimensioned engineering
drawings is a materially bigger lift than manual text parsing, and v1 scope
is Q&A over manuals, not drawings.

**Current handling:** Indexed by metadata only (drawing number, equipment,
revision) with no searchable text chunk. If a query might need it, the
answer should surface it as a visual reference (e.g. "see Drawing 70958 Rev
I") rather than silently omitting it.

**Path to fixing it:** Vision-based extraction of BOM tables/dimensions from
drawing sheets, or at minimum image-embedding-based retrieval so a query can
at least *surface* the right drawing even without pulling text from it.

---

## Dense spec tables get lost in page-level chunks

**What:** Tested query "what is the operating temperature range for the
central unit" against the real MPC 800A manual. The correct answer (0–50°C)
is on page 10 — but retrieval missed it, surfacing descriptive pages about
the Central Unit instead.

**Why:** Page 10 is one giant technical data table (main data + environmental
tests + EMC tests all together) collapsed into a single text chunk. The word
"temperature" is one hit among dozens of other equally-rare terms (kHz, dB,
VDC, IEC standard numbers) in that same chunk, so it doesn't stand out enough
to rank highly — with either the TF-IDF placeholder or, likely, with a real
embedding model too, since the problem is chunk granularity, not just the
embedding method.

**Path to fixing it:** Table-aware chunking — detect table regions (e.g. via
pdfplumber's table extraction, which returns row/column structure) and split
dense multi-row tables into smaller chunks (per table, or per few rows)
rather than lumping a whole table-heavy page into one chunk.

---

## Placeholder embeddings (TF-IDF) need replacing before real use

**What:** `ingestion/retrieval.py` currently uses TF-IDF (keyword overlap) as
a stand-in for real semantic embeddings, chosen because this sandbox can't
reach an embedding model host over the network.

**Why deferred:** Good enough to prove out the store/query/rank plumbing;
not good enough for production — it'll miss matches that use different
words for the same idea (e.g. "won't start" vs "fails to crank").

**Path to fixing it:** Swap `TfidfEmbedder` for a real embedding model
(Voyage AI — Anthropic's embedding partner — or a local sentence-transformers
model) once the backend has real network/API access. Same `__call__`
interface, so it's a drop-in replacement, not a redesign.

---

## Google Drive connector not yet available

**What:** Wanted a live-connected Google Drive so Jared could drop TMs in
and have them flow into ingestion automatically.

**Why deferred:** Checked the connector directory (Aug 2026) — no Google
Drive connector currently available to link into this chat/project. Workflow
for now: shared Drive folder as the human hand-off point (Jared/Dave
organize files there), then manual upload into the ingestion pipeline when
ready to process a batch.

**Path to fixing it:** Check back periodically for a connector to appear, or
build real Google Drive API access (OAuth) into a proper backend once one
exists — that's a bigger, separate build from this chat-based connector
system.

---

## Checkbox/marker tables lose their meaning in text extraction

**What:** Tested "list the 100-hour checks for the marine clutch" live.
The answer was correct (2 of 3 jobs apply at 100 hours; the third applies
only at 500 hours) — but checking the actual page image revealed the
underlying table uses filled dots (●) to mark which interval column each
job belongs to. Plain text extraction drops those dots entirely, so the
extracted text lists job names and interval headers with no way to tell
which job belongs to which interval.

**Why this matters more than a typical extraction gap:** The answer being
right this time may have been Claude reasoning from a plausible pattern
(oil changes tend to come at a longer interval than inspections) rather
than reading a fact that was actually present in what it was given. Nothing
in the citation would reveal that difference — a wrong answer here would
look exactly as confident and well-cited as a right one. For a maintenance
checklist someone might actually follow onboard, that's a meaningfully
different risk than a merely-hard-to-find spec.

**Path to fixing it:** Table-aware extraction (see the related dense-table
entry above) needs to specifically preserve marker/checkbox state per cell,
not just cell text — e.g. via pdfplumber's table extraction, representing
each row as structured data ("job: X, applies_at: [100]") rather than
flattened prose. Until that's built, treat any answer describing which
items apply "at this interval" as needing a manual cross-check against the
actual table image, not just the manual text.

---

## Add new items below this line as they come up
