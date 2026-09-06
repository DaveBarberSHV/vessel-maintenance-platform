# Vessel Maintenance Platform — Project Brief

*Last updated: September 2026, three weeks into active development.*

---

## Purpose

An AI knowledge system — not a document search tool — for vessel
engineering staff to get answers to equipment questions from a
vessel-specific document library: TMs, generator/HVAC/fire/electrical/
steering/hydraulics manuals, OEM service bulletins, drawings, and SMS
procedures.

The system identifies the installed equipment model from vessel-specific
documentation, searches only documents authorized for that vessel, pulls
relevant TM sections and applicable procedures, and returns a concise,
ordered answer with precise citations (document title, revision, page
number).

**Example target interaction:**
> Engineer asks: "The #2 generator is showing high coolant temperature
> under heavy load. What should I check?"

The system identifies the installed generator model, retrieves the
relevant TM sections and PMS procedures, and returns a cited
troubleshooting answer — e.g. "Generator TM, Rev. 7, p. 143" — not a
list of search results to dig through.

---

## Current scope (v1 — prototype)

- **Single vessel: the tug *Polaris* only.** The LNG bunkering barge
  is a fully separate vessel and is explicitly out of scope for v1.
- **Document library:** ~135 PDFs currently ingested (~50% of the
  estimated full Polaris library), heading toward ~270 PDFs. Documents
  organized by system using a consistent naming convention
  (`System_Manufacturer_Model_DocType_Rev.pdf`).
- **Maintenance history tracking** is explicitly deferred to v2 —
  requires a structured database and is a separate problem from Q&A
  retrieval.
- **Offline access** to raw TMs when disconnected is deferred — not a
  v1 focus. Starlink connectivity (~90% reliable) is accepted for the
  prototype.
- **Multi-vessel architecture** is deferred — the current system is
  single-vessel by design. Generalizing to a larger fleet is a real
  future goal but a significant architectural step, particularly around
  data isolation and security.

---

## Features built (as of September 2026)

**Core Q&A system:**
- Semantic search across the full document library (Voyage AI embeddings,
  Supabase/pgvector)
- Claude API generates concise, cited answers from retrieved chunks
- Citations at document + revision + page level (section-level precision
  was explicitly dropped in favor of page citations — cleaner and more
  useful in practice)
- Equipment registry — system identifies installed models so answers are
  specific to the *Polaris*, not generic
- Clarifying question when needed — asks once, never loops

**Document handling and ingestion:**
- Documents are stored in a shared Google Drive folder organized by
  system (e.g. `Main Engines/`, `Hull/`, `Electrical/`). Jared sources
  and uploads documents; Dave runs the ingestion pipeline locally.
- A rename tooling pipeline (`propose_renames.py` → review CSV →
  `apply_renames.py`) standardizes filenames to the naming convention
  before ingestion. The convention encodes system, manufacturer, model,
  document type, and revision directly in the filename — this is what
  allows the system to know which document is which without reading it.
- Duplicate detection (`find_duplicate_files.py`) catches identical
  files before ingestion, preventing double-indexing.
- The ingestion pipeline (`scan_folder.py`) processes new or changed
  files only — safe to re-run at any time. It parses PDFs into chunks,
  generates embeddings, and stores everything in Supabase with metadata
  tags (vessel, equipment model, document type, revision).
- Vision extraction for scanned/image-only pages — drawings and
  scanned reference docs are transcribed via Claude's vision API during
  ingestion, making them fully searchable. Large-format drawings
  exceeding the 8000px vision limit remain a partial gap (see Backlog).
- Page images rendered and stored in Supabase Storage during ingestion —
  engineers see the actual page alongside a citation, not just extracted
  text.
- Auto-surfacing on explicit request — a `SHOW_DOCUMENT` structured
  response detects genuine "show me" language and displays the specific
  page image prominently above the answer.

**Document library panel (in progress):**
- A 📂 Document Library sidebar panel lists all ingested documents
  grouped by system. Engineers can browse and click any document to view
  it directly — bypasses retrieval entirely for navigation.
- Retrieval boost for drawing requests is the immediate next build:
  when a click comes from the library panel, document chunks are fetched
  directly by title rather than relying on semantic search (which
  reliably fails for drawings).

**Engineer knowledge:**
- Engineer Notes — real crew experience attached to equipment, clearly
  separated from manufacturer data, with role-based submission authority.
  Notes appear before the answer, since they change how you approach a
  task.
- Safety Information — WARNING/CAUTION callouts extracted from TMs and
  surfaced in a collapsed section after the answer.

**Infrastructure:**
- Deployed on Streamlit Cloud, Supabase Postgres backend.
- Authentication (username/password), MFA on service accounts.
- Persistent conversation history with 👍/👎 feedback.
- Security-by-design: RLS enabled on all database tables, four-question
  security review required before any new table or integration.

---

## Deferred / future scope (v2 and beyond)

- Maintenance history tracking (structured database — separate problem)
- Offline access to raw TMs when disconnected
- Multi-vessel architecture (real security boundary question, not just
  a feature flag)
- Full-manual PDF download (Storage infrastructure needed)
- Email-based document ingestion (longer-term vision for keeping the
  library current)
- Large-format drawing resolution improvement (tiling beyond 8000px)
- Security audit log (30-day commitment from Sept 2026)

---

## Development timeline — September through Jared's next rotation

The goal: a system reliable enough to put in front of Jared's engineers
for real testing, within approximately one month.

**Week 1 (Sept 6–13) — Retrieval and library usability**

The two most important technical gaps before wider rollout:

1. **Retrieval boost for drawing requests** — when a library panel click
   fires a "Show me the..." question, fetch that document's chunks
   directly by title rather than running semantic search. Deterministic,
   not language-dependent. This is the fix for the core failure seen in
   live testing: clicking a shaft arrangement drawing retrieved O&M
   manual pages instead.

2. **Document library filter** — a simple text filter box within the
   library panel that narrows the 100+ document list as you type.
   Essential before the library doubles to ~270 documents. Without this,
   browsing is impractical under real working conditions.

3. **Library label cleanup** — strip the redundant system prefix from
   button labels (already in the group header). Small but meaningful for
   readability.

**Week 2 (Sept 14–20) — Answer quality and document completion**

1. **Structured answer quality testing** — not UI testing, but testing
   whether answers are actually correct and well-cited across the real
   document library. Dave and Jared both have the engineering background
   to judge this. This is the most important pre-rollout activity —
   it will reveal whether the Q&A core is trustworthy enough for
   engineers to rely on operationally.

2. **Remaining document ingestion** — complete the second 50% of the
   Polaris library as Jared continues sourcing documents. Target: as
   close to 100% coverage as practical before Jared comes off rotation.

3. **Page image verification** — confirm all ingested documents have
   page images in Supabase Storage (particularly the newly ingested
   batch from September).

**Week 3 (Sept 21–27) — Polish and rollout preparation**

1. **Small open items:** SDS friendly label, empty CAT Coolant DEAC SDS
   file, Alfa Laval Drive duplicate cleanup, Jared's role label fix,
   test data cleanup from the database.

2. **Rules of behavior / onboarding** — a simple one-page orientation
   for new users explaining what the system is, what it's good at, what
   it won't do, and how to give useful feedback. Engineers who understand
   the system's constraints give better feedback.

3. **Security audit log** — the 30-day commitment from Sept 2026 falls
   within this window.

**Week 4 (Sept 28 – Oct ~6) — Controlled rollout to Jared's team**

Jared comes off rotation with approximately three weeks away from the
vessel. This is the window to introduce the system to his engineers for
real testing under his supervision.

- Start with 2–3 engineers, not the full team — enough to get real
  feedback without overwhelming the support loop.
- Give them specific test scenarios (not just "try it") — real
  troubleshooting questions from actual recent work on the *Polaris*.
- Collect 👍/👎 feedback and any questions that returned poor or
  missing answers — review these together as a structured session.
- Decide based on that feedback whether the system is ready for broader
  deployment or needs another development cycle first.

---

## What "ready for wider rollout" means technically

Not perfection — the system will miss some answers and engineers need to
know that. Ready means:

1. Drawing retrieval works reliably from the library panel.
2. Q&A answers for common troubleshooting questions are correct and
   well-cited (verified by Dave and Jared before rollout).
3. The document library is navigable at the current document volume.
4. Engineers have a clear mental model of what the system does and
   doesn't do (onboarding doc).
5. Feedback mechanism is working so bad answers are captured and
   improvable.

---

## Open items for the Jared conversation (September 2026)

- Confirm tug-only scope (barge explicitly out of v1) — **agreed**
- Confirm maintenance history tracking stays in v2 — **agreed**
- Confirm offline access stays deferred — **agreed**
- Assess document library progress: ~50% ingested, path to completion
- Align on the one-month rollout timeline above
- Discuss what "good enough" looks like for the initial engineer testing
  group — what failure modes are acceptable vs. disqualifying
- **Google Drive document process** — the current workflow (Jared
  uploads originals to Drive, Dave runs the rename and ingestion pipeline
  locally) works for the prototype phase but has friction: Jared can't
  add a document and have it appear in the system without Dave's
  involvement. Discuss: is this acceptable for the next month, or do we
  need to streamline it? Longer-term, an automated Drive-to-backend
  integration (triggered on new file upload) would remove Dave from the
  loop entirely — but this requires backend development and is not yet
  scoped. Also worth aligning on whether Jared or his team should have
  any direct role in the rename/naming convention step, since getting
  the filename right is what determines how well the system can find and
  cite a document.

- **Single vessel: the tug *Polaris* only.** The LNG bunkering barge
  is a fully separate vessel and is explicitly out of scope for v1.
- **Document library:** ~135 PDFs currently ingested (~50% of the
  estimated full Polaris library), heading toward ~270 PDFs. Documents
  organized by system using a consistent naming convention
  (`System_Manufacturer_Model_DocType_Rev.pdf`).
- **Maintenance history tracking** is explicitly deferred to v2 —
  requires a structured database and is a separate problem from Q&A
  retrieval.
- **Offline access** to raw TMs when disconnected is deferred — not a
  v1 focus. Starlink connectivity (~90% reliable) is accepted for the
  prototype.
- **Multi-vessel architecture** is deferred — the current system is
  single-vessel by design. Generalizing to Jared's company's 50+ vessel
  fleet is a real future goal but a significant architectural step,
  particularly around data isolation and security.

---

## Features built (as of September 2026)

**Core Q&A system:**
- Semantic search across the full document library (Voyage AI embeddings,
  Supabase/pgvector)
- Claude API generates concise, cited answers from retrieved chunks
- Citations at document + revision + page level (section-level precision
  was explicitly dropped in favor of page citations — cleaner and more
  useful in practice)
- Equipment registry — system identifies installed models so answers are
  specific to the *Polaris*, not generic
- Clarifying question when needed — asks once, never loops

**Document handling:**
- Vision extraction for scanned/image-only pages — drawings and
  scanned reference docs are transcribed via Claude's vision API during
  ingestion, making them fully searchable. Large-format drawings
  exceeding the 8000px vision limit remain a partial gap (see Backlog).
- Page images surfaced alongside citations — engineers see the actual
  page, not just extracted text
- Auto-surfacing on explicit request — a `SHOW_DOCUMENT` structured
  response detects genuine "show me" language and displays the specific
  page image prominently above the answer

**Document library panel (in progress):**
- A 📂 Document Library sidebar panel lists all ingested documents
  grouped by system. Engineers can browse and click any document to view
  it directly — bypasses retrieval entirely for navigation.
- Retrieval boost for drawing requests is the immediate next build:
  when a click comes from the library panel, document chunks are fetched
  directly by title rather than relying on semantic search (which
  reliably fails for drawings).

**Engineer knowledge:**
- Engineer Notes — real crew experience attached to equipment, clearly
  separated from manufacturer data, with role-based submission authority.
  Notes appear before the answer, since they change how you approach a
  task.
- Safety Information — WARNING/CAUTION callouts extracted from TMs and
  surfaced in a collapsed section after the answer

**Infrastructure:**
- Deployed on Streamlit Cloud, Supabase Postgres backend
- Authentication (username/password), MFA on service accounts
- Persistent conversation history with 👍/👎 feedback
- Rename tooling, duplicate detection, and ingestion pipeline for
  document management
- Security-by-design: RLS enabled on all database tables, four-question
  security review required before any new table or integration

---

## Deferred / future scope (v2 and beyond)

- Maintenance history tracking (structured database — separate problem)
- Offline access to raw TMs when disconnected
- Multi-vessel architecture (real security boundary question, not just
  a feature flag)
- Full-manual PDF download (Storage infrastructure needed)
- Email-based document ingestion (longer-term vision for keeping the
  library current)
- Large-format drawing resolution improvement (tiling beyond 8000px)
- Security audit log (30-day commitment from Sept 2026)

---

## Development timeline — September through Jared's next rotation

The goal: a system reliable enough to put in front of Jared's engineers
for real testing, within approximately one month.

**Week 1 (Sept 6–13) — Retrieval and library usability**

The two most important technical gaps before wider rollout:

1. **Retrieval boost for drawing requests** — when a library panel click
   fires a "Show me the..." question, fetch that document's chunks
   directly by title rather than running semantic search. Deterministic,
   not language-dependent. This is the fix for the core failure seen in
   live testing: clicking a shaft arrangement drawing retrieved O&M
   manual pages instead.

2. **Document library filter** — a simple text filter box within the
   library panel that narrows the 100+ document list as you type.
   Essential before the library doubles to ~270 documents. Without this,
   browsing is impractical under real working conditions.

3. **Library label cleanup** — strip the redundant system prefix from
   button labels (already in the group header). Small but meaningful for
   readability.

**Week 2 (Sept 14–20) — Answer quality and document completion**

1. **Structured answer quality testing** — not UI testing, but testing
   whether answers are actually correct and well-cited across the real
   document library. Dave and Jared both have the engineering background
   to judge this. This is the most important pre-rollout activity —
   it will reveal whether the Q&A core is trustworthy enough for
   engineers to rely on operationally.

2. **Remaining document ingestion** — complete the second 50% of the
   Polaris library as Jared continues sourcing documents. Target: as
   close to 100% coverage as practical before Jared comes off rotation.

3. **Page image verification** — confirm all ingested documents have
   page images in Supabase Storage (particularly the newly ingested
   batch from September).

**Week 3 (Sept 21–27) — Polish and rollout preparation**

1. **Small open items:** SDS friendly label, empty CAT Coolant DEAC SDS
   file, Alfa Laval Drive duplicate cleanup, Jared's role label fix,
   test data cleanup from the database.

2. **Rules of behavior / onboarding** — a simple one-page orientation
   for new users explaining what the system is, what it's good at, what
   it won't do, and how to give useful feedback. Engineers who understand
   the system's constraints give better feedback.

3. **Security audit log** — the 30-day commitment from Sept 2026 falls
   within this window.

**Week 4 (Sept 28 – Oct ~6) — Controlled rollout to Jared's team**

Jared comes off rotation with approximately three weeks away from the
vessel. This is the window to introduce the system to his engineers for
real testing under his supervision.

- Start with 2–3 engineers, not the full team — enough to get real
  feedback without overwhelming the support loop
- Give them specific test scenarios (not just "try it") — real
  troubleshooting questions from actual recent work on the *Polaris*
- Collect 👍/👎 feedback and any questions that returned poor or
  missing answers — review these together as a structured session
- Decide based on that feedback whether the system is ready for broader
  deployment or needs another development cycle first

---

## What "ready for wider rollout" means technically

Not perfection — the system will miss some answers and engineers need to
know that. Ready means:

1. Drawing retrieval works reliably from the library panel
2. Q&A answers for common troubleshooting questions are correct and
   well-cited (verified by Dave and Jared before rollout)
3. The document library is navigable at the current document volume
4. Engineers have a clear mental model of what the system does and
   doesn't do (onboarding doc)
5. Feedback mechanism is working so bad answers are captured and
   improvable

---

## Open items for the Jared conversation (September 2026)

- Confirm tug-only scope (barge explicitly out of v1) — **agreed**
- Confirm maintenance history tracking stays in v2 — **agreed**
- Confirm offline access stays deferred — **agreed**
- Assess document library progress: ~50% ingested, path to completion
- Align on the one-month rollout timeline above
- Discuss what "good enough" looks like for the initial engineer testing
  group — what failure modes are acceptable vs. disqualifying
