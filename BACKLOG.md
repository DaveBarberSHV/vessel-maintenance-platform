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

## 🔺 PRIORITY — Dense spec tables get lost in page-level chunks

**Elevated (Aug 2026):** Dave confirms many TMs in the full drivetrain
library have tables more complex/dense than this prototype's examples.
This is no longer a corner case worth fixing eventually — it's likely
closer to core reliability, and should be the top priority once the
current session's testing wraps up. Recommended sequencing: fix this
before building any front end (see `docs/architecture.md`), since a
polished interface over an unreliable table-reading system would be worse
than no interface at all.

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

## ✅ RESOLVED — Placeholder embeddings replaced with real embeddings (Voyage AI)

**What it was:** `ingestion/retrieval.py` used TF-IDF (keyword overlap) as a
stand-in for real semantic embeddings, chosen because Claude's sandbox
can't reach an embedding model host over the network.

**Concrete failure case that motivated the fix (Aug 2026):** Live-tested
"My propulsion equipment has shut down. What could be causing it?" — TF-IDF
retrieved three generic PPE/safety boilerplate pages (matched on the
repeated literal phrase "propulsion equipment") instead of the two
genuinely relevant pages: MCH6 p.15's shutdown-condition list (high oil
temp, low lube pressure) and MPC 800A p.34's fault-handling table.

**Resolution (Aug 2026):** Built `ingestion/retrieval_voyage.py` using
Voyage AI, tested live on Dave's own machine (which has normal network
access, unlike Claude's sandbox). Ran the identical query through both
engines side by side:
- **TF-IDF:** missed both relevant pages, correctly said it didn't have
  enough information rather than guessing.
- **Voyage:** correctly retrieved both pages. Citation to MCH6 p.15 was
  verified against the actual manual text — verbatim accurate, not a lucky
  guess.

Same `__call__` interface as the TF-IDF version, so this was a genuine
drop-in swap, not a redesign. `answer_query.py --engine voyage|tfidf` lets
either be selected for comparison going forward.

**Still open from this:** batch-pacing scaling for large corpora (separate
entry below).

**Update (Aug 2026):** `retrieval.py` and `retrieval_voyage.py` have been
consolidated into a single `retrieval.py` — Voyage is now the default
engine (`--engine voyage`, or no flag at all), TF-IDF remains available for
offline testing (`--engine tfidf`). Done ahead of Jared's ~20 additional
TMs so there's one clear pipeline to run, not two to keep in sync.
**Note:** the Chroma database folder layout changed as part of this — old
`chroma_db/` and `chroma_db_voyage/` folders are stale and can be deleted;
new layout is `chroma_db/voyage/` and `chroma_db/tfidf/`. Anyone pulling
this update needs to re-run `python retrieval.py build` once (defaults to
Voyage) before querying again.

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

## ✅ RESOLVED — Checkbox/marker tables lose their meaning in text extraction

**What it was:** Tables like the MCH6 manual's maintenance schedule mark
which interval column a job applies to with a small filled dot — a drawn
image, not a text character. Plain text extraction dropped it entirely, so
"list the 100-hour checks" got the right answer once by what may have been
reasonable-sounding inference rather than verified fact — risky for a
maintenance checklist, since a wrong answer would look exactly as
confident as a right one.

**Resolution (Aug 2026):** Built `ingestion/table_extraction.py`. Confirmed
the dots are small embedded images (~9×10pt) at consistent x-positions per
column; cross-referenced their positions against pdfplumber's table cell
boundaries to determine exactly which cell each marker sits in — turning
an ambiguous blank cell into a verified fact taken from the document's own
structure. Verified against the real MCH6 PDF (uploaded directly to this
chat, not the platform-preprocessed copy — see note below) and confirmed
byte-for-byte correct against the actual page image for both the
Electric/Hydraulic and Hybrid configuration tables on page 32.

Wired into `parse_and_chunk.py`: any page with tables now gets a rendered
markdown version of the table appended to its chunk text, with marker
cells filled in as "X" rather than left blank.

**Important process note this surfaced:** table structure recovery only
works on genuine PDF files, not the platform's pre-processed preview
format (page images + extracted text) that Project-attached files get
converted into. Going forward, TMs should be uploaded directly to chat
(not just added to Project files) when ingestion needs to run against
them, so real PDF bytes are available. This also incidentally resolved
the MCH6 "revision unknown" flag that's been in every citation this
session — the real PDF's title page has "Document Version 1, May 24 2021"
clearly stated.

**Still open:** the related dense-table entry above (tables *without*
marker cells, where a spec gets buried among unrelated ones in one big
chunk) is a related but distinct problem — same underlying tool, different
fix needed (splitting dense chunks, not recovering marker cells).

---

## Voyage embedding batch pacing won't scale to the full TM library

**What:** `retrieval_voyage.py` currently pauses 15 seconds between every
batch of 20 chunks, added as a quick fix when the first embedding run hit
Voyage's reduced rate limit (before a payment method was added). First real
run succeeded: 112 chunks in ~6 batches, ~1.5 minutes overhead.

**Why deferred:** Worked fine for a 112-chunk corpus. Won't scale
gracefully once Jared's full TM library (dozens of manuals, potentially
thousands of pages) needs (re-)embedding — flat unconditional pauses add up
fast and aren't smart about it.

**Path to fixing it:** Two real options, worth choosing deliberately rather
than just tuning the sleep number: (1) increase batch size toward Voyage's
actual per-request limits so fewer requests are needed overall, and/or (2)
only back off when an actual rate-limit error comes back, rather than
pausing unconditionally on every batch regardless of whether it's needed —
now that a payment method is on file, the account may not even hit the
reduced limit anymore, making the current pause pure overhead.

---

## Incremental scanner's Chroma add/delete path needs a live test

**What:** `ingestion/scan_folder.py` was built to solve a real workflow
problem — as Jared adds TMs to Drive day by day, re-embedding the entire
library on every addition would waste time and Voyage API cost. It hashes
each file and skips anything unchanged, only processing new or changed
files.

**What's verified vs. not:** the hash-comparison logic (unchanged files
correctly skipped, changed files — e.g. a revision swap — correctly
detected) was tested directly and works. The actual Chroma
`collection.add()` / `collection.delete()` calls for new/updated chunks
could NOT be tested from Claude's sandbox (no network access to Voyage),
so that path is implemented but not yet proven against the real index.

**Path to fixing it:** first time Dave runs `scan_folder.py` for real
(when Jared's TMs land), watch the output closely — confirm the reported
counts (new/changed/unchanged) match expectations, and spot-check that a
query against a newly-added TM actually returns results. If anything looks
off, this is the first place to check.

---


