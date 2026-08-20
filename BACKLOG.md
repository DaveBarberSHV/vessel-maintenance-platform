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

## Add new items below this line as they come up
