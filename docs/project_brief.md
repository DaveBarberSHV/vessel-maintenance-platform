# Vessel Maintenance Platform — Project Brief

## Purpose
An AI knowledge system (not document search) for vessel engineering staff to
get answers to equipment questions from a vessel-specific document library —
TMs, generator/HVAC/fire/electrical/steering/hydraulics manuals, OEM service
bulletins, and SMS procedures.

**Example target interaction**
Engineer asks: "The #2 generator is showing high coolant temperature under
heavy load. What should I check?"
System should:
1. Identify the installed generator model from vessel-specific documentation
2. Search only documents authorized for that vessel
3. Pull relevant TM sections and applicable PMS procedures/service bulletins
4. Return a concise, ordered troubleshooting answer with precise citations
   (e.g. "Generator TM, Rev. 7, §8.4, pp. 142–144" and "Vessel PMS Procedure
   ENG-027, Rev. 3, p. 4")

*(Note: v1 citation granularity is document + revision + page number, not
section — see `../BACKLOG.md`.)*

## People and roles
- **You:** Founder/lead. Built and sold a company making troubleshooting
  apps for complex military equipment; managed a 50+ person dev team for 10
  years. Not a coder yourself — using Claude as the development team for
  this prototype. Also hold a USCG 100-Ton Master's license and served as a
  Surface Warfare Officer with shipboard engineering training — gives you
  the ability to judge whether the system's answers are actually correct,
  not just plausible.
- **Jared:** Chief Engineer on a 110' tug + 450' LNG bunkering barge combo,
  with three engineers on staff. His vessel combo is the prototype target.
  Role: define requirements, supply TMs/documents, test and use the
  prototype, provide ongoing user feedback from an actual engineering
  department perspective.

## Scope for prototype
- Single vessel combo (Jared's tug + barge) — not "any vessel" yet
- Starting document volume: ~20–30 TMs (smaller than the eventual target of
  ~200 PDFs / 30,000 pages)
- Maintenance history tracking (would require a structured database) is
  explicitly deferred — not a v1 focus

## Architecture decided so far
See `../README.md` for the current architecture summary.

## Deferred / future scope
- Maintenance history tracking (requires a structured database — separate
  problem from Q&A retrieval)
- Offline access to raw TMs when disconnected
- Generalizing beyond the single prototype vessel combo
- Section-level citation precision (see `../BACKLOG.md`)
- OCR/vision extraction for drawing-only documents (see `../BACKLOG.md`)

## Open items / next steps
- Confirm Team account steps for inviting Jared and sharing a Project with
  him (see support.claude.com)
- Decide long-term Drive-to-backend integration approach once ingestion
  pipeline code exists
- Begin testing ingestion/parsing logic against Jared's actual TMs once
  uploaded in the new Project chat — **done for the first 3 TMs**, see
  `../ingestion/parse_and_chunk.py` and its output
