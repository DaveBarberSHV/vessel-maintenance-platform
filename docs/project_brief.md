# Vessel Maintenance Platform — Project Brief

*Last updated: September 2026, three weeks into active development.*

---

## Purpose

An AI knowledge system — not a document search tool — for vessel
engineering staff to get answers to equipment questions from a
vessel-specific document library: TMs, generator/HVAC/fire/electrical/
steering/hydraulics manuals, OEM service bulletins, drawings, and SMS
procedures.

The system identifies the installed equipment model, searches only
documents authorized for that vessel, and returns a concise, ordered
answer with precise citations (document title, revision, page number).

**Example target interaction:**
> Engineer asks: "The #2 generator is showing high coolant temperature
> under heavy load. What should I check?"

The system identifies the installed generator model, retrieves the
relevant TM sections and PMS procedures, and returns a cited answer —
e.g. "Generator TM, Rev. 7, p. 143" — not a list of search results.

---

## Scope (v1 — prototype)

- **Single vessel: the tug *Polaris* only.** The LNG bunkering barge is
  a fully separate vessel, explicitly out of scope for v1.
- **Document library:** ~135 PDFs ingested (~50% of the estimated full
  Polaris library), heading toward ~270 PDFs.
- **Maintenance history tracking** deferred to v2 — requires a
  structured database, separate from Q&A retrieval.
- **Offline access** deferred — Starlink (~90% reliable) accepted for
  the prototype.
- **Multi-vessel architecture** deferred — significant security
  architecture work required before generalizing beyond one vessel.

---

## Features built (as of September 2026)

**Core Q&A:**
- Semantic search (Voyage AI embeddings, Supabase/pgvector) + Claude API
  generates cited answers from retrieved document chunks
- Citations at document + revision + page level
- Equipment registry — answers are specific to the *Polaris*, not generic
- Clarifying question when needed — asks once, never loops

**Document ingestion pipeline:**
- Documents live in a shared Google Drive folder organized by system.
  Jared sources and uploads originals; Dave runs the pipeline locally.
- Rename tooling (`propose_renames.py` → review CSV → `apply_renames.py`)
  standardizes filenames to the naming convention before ingestion
  (`System_Manufacturer_Model_DocType_Rev.pdf`). The filename is what
  tells the system which document is which.
- Duplicate detection (`find_duplicate_files.py`) prevents
  double-indexing before each ingest run.
- Ingestion (`scan_folder.py`) processes new or changed files only —
  safe to re-run at any time. Parses PDFs into chunks, generates
  embeddings, stores in Supabase with metadata tags.
- Vision extraction for scanned/image-only pages — drawings and scanned
  docs are transcribed via Claude's vision API, making them searchable.
  Large-format drawings exceeding the 8000px limit remain a partial gap.
- Page images stored in Supabase Storage — engineers see the actual page
  alongside a citation.

**Document library panel (in progress):**
- 📂 Document Library sidebar panel lists all documents grouped by
  system. Click any document to view it directly — no retrieval needed.
- Immediate next build: retrieval boost so panel clicks fetch chunks
  directly by title, bypassing semantic search (which fails for drawings).

**Engineer knowledge:**
- Engineer Notes — real crew experience, clearly separated from
  manufacturer data, with role-based submission authority. Shown before
  the answer.
- Safety Information — WARNING/CAUTION callouts surfaced in a collapsed
  section after the answer.

**Infrastructure:**
- Streamlit Cloud + Supabase Postgres. Authentication, MFA on service
  accounts, persistent conversation history, 👍/👎 feedback.
- Security-by-design: RLS on all database tables, four-question security
  review before any new table or integration.

---

## Deferred to v2

- Maintenance history tracking
- Offline TM access
- Multi-vessel / multi-owner architecture
- Full-manual PDF download
- Email-based document ingestion
- Large-format drawing resolution (tiling beyond 8000px)
- Security audit log (30-day commitment from Sept 2026)

---

## Development timeline — through Jared's next rotation

**Week 1 (Sept 6–13) — Retrieval and library usability**
1. Retrieval boost for drawing requests — fetch chunks directly by title
   on library panel clicks, not via semantic search
2. Document library filter — text filter box to narrow 100+ documents
   as you type; essential before the library doubles to ~270
3. Library label cleanup — strip redundant system prefix from buttons

**Week 2 (Sept 14–20) — Answer quality and document completion**
1. Structured Q&A testing — verify answers are correct and well-cited
   across the real library; most important pre-rollout activity
2. Remaining document ingestion — target as close to 100% Polaris
   coverage as practical before Jared comes off rotation
3. Page image verification — confirm all ingested docs have Storage images

**Week 3 (Sept 21–27) — Polish and rollout preparation**
1. Small open items: SDS label, empty CAT Coolant DEAC SDS file, Alfa
   Laval Drive duplicate, Jared's role label, test data cleanup
2. User onboarding doc — one page on what the system does, what it
   won't do, and how to give useful feedback
3. Security audit log

**Week 4 (Sept 28 – Oct ~6) — Controlled rollout to Jared's team**
- 2–3 engineers to start, with specific test scenarios from real recent
  work on the *Polaris* — not just "try it"
- Collect and review 👍/👎 feedback as a structured session
- Decide whether ready for broader deployment or another dev cycle

**Ready for rollout means:**
drawing retrieval works from the panel; Q&A answers verified correct;
library is navigable; engineers have a clear mental model; feedback loop
is working.

---

## Open items for the Jared conversation (September 2026)

- Tug-only scope, barge out of v1 — **agreed**
- Maintenance history tracking stays v2 — **agreed**
- Offline access stays deferred — **agreed**
- Document library progress: ~50% ingested, path to 100% coverage
- Align on one-month rollout timeline above
- What "good enough" looks like for the initial test group — what
  failure modes are acceptable vs. disqualifying
- **Google Drive ingestion process** — current workflow (Jared uploads,
  Dave renames and ingests) works but has friction: a new document
  can't appear in the system without Dave's involvement. Discuss whether
  this is acceptable for the next month, and whether Jared or his team
  should have any role in the naming step. Longer-term: automated
  Drive-to-backend integration is the right answer but not yet scoped.
