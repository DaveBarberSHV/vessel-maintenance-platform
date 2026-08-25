# Monday Discussion Guide — Jared Check-in

**Session happened Aug 2026 — real answers below in Section 4.** Original
purpose: step back from building and re-ground on end-user needs before
adding more scope. This is now a record of what was actually learned, not
just a pre-meeting plan — kept as history so these answers don't get lost
or need re-asking later.

## 1. Show him something real first

Before asking questions, demonstrate the system working against his own
equipment — this grounds the rest of the conversation in something
concrete rather than abstract plans.

- Run 2-3 real questions live against the TMs he's already provided —
  ideally including at least one that hits one of his own 8 TMs directly
  (e.g. the SKF bearing housing doc, or the GEWES cardan shaft manual),
  so he sees his own recent contribution already working.
- Worth showing one *good* answer and being honest about one *limitation*
  — e.g. the page-level citation choice, the "I don't have enough
  information" behavior when something isn't in the TMs, or the fact that
  5 of the 14 files currently loaded (scanned reports, one drawing) aren't
  full-text searchable yet, only findable by metadata. This sets realistic
  expectations rather than overselling.

## 2. Questions to actually ask him

These are things we've been assuming or hasn't come up yet — worth
getting his real answer rather than continuing to guess:

- **What do his real day-to-day questions actually look like?** The
  brief's example (generator overheating) was a good starting scenario,
  but is it representative? Ask him to describe 3-4 recent real situations
  where he needed to look something up.
- **Who would actually use this day to day** — just him, or his other
  engineers too? This affects how forgiving the interface needs to be,
  and how urgent a real front end is versus staying command-line for now.
- **What does "good enough" mean to him for an answer?** We've built in
  honesty (saying "I don't know" rather than guessing) — does that match
  what he'd want, or would he rather get a best-guess with a caveat?
- **What's the realistic system priority order beyond drivetrain?** The
  brief mentions generator/HVAC/fire/electrical/steering/hydraulics —
  worth asking him to actually rank these, rather than assuming order.
- **How does he imagine using this in a real moment** — at a desk with a
  laptop, on a phone in the engine room, printed out? This matters a lot
  for what "the product" eventually needs to be.
- **What would make him NOT trust an answer?** Useful to know his
  personal bar for skepticism, since he's the one who'll actually be
  relying on this in a real situation.
- **How does he want scanned/image-only reference material handled?** His
  batch included 4 single-page balancing reports that are scans with no
  text layer — they're indexed by filename/metadata only right now, not
  searchable by content. Worth finding out how often this kind of
  reference material matters to him day-to-day, since it directly affects
  how urgent OCR support is (see `BACKLOG.md`).

## 3. Things worth being transparent with him about

- **Page-level citations, not section-level** — deliberate choice for
  accuracy over precision (see `BACKLOG.md`). Worth explaining briefly so
  it doesn't look like an oversight.
- **Not everything he sent is fully searchable yet** — of his 8 TMs, the
  4 balancing-report scans have no extractable text (indexed by filename
  only), and the original azimuth thruster GA drawing is the same way.
  Worth noting that 2 of the 3 drawing-type files *do* have searchable
  text and work normally — this isn't a blanket "drawings don't work"
  limitation, just true for scans and vector-only drawings specifically.
- **This is still just the two of you testing** — no real front end yet,
  everything runs from a command line. Worth being upfront about how early
  stage this still is if there's any risk he's expecting something more
  polished.
- **The naming convention picked up two new document types** (`DWG`,
  replacing the earlier `GADrawing`, and `RefData` as a catch-all for
  reports/inspections/reference material) based on how his actual batch of
  files needed to be categorized — worth letting him know so future
  uploads use the current convention from the start.

## 4. Real answers from the session (Aug 2026)

**What real day-to-day questions look like:** tested several real
scenarios; the standout was Jared asking for the full clutch pressure test
reference (test port locations, standard settings, hybrid config, and
alarm thresholds) — the system correctly synthesized this from three
separate pages/tables in the MCH6 manual into one clean, usable reference.
Jared was genuinely pleased with this result — good concrete proof of
value, not just a working demo.

**Who uses this day to day — much broader than assumed:** not just Dave
and Jared. Real answer: **Jared** (Chief Engineer), **his mechanics and
engineers**, the **port engineer** while the vessel is in port for
maintenance, the **ship's captain**, and possibly others. Jared sees this
as a tool for questions about operation/maintenance across essentially
*all* engineering-department-responsible systems on the vessel — not a
narrow drivetrain tool. **Action needed:** the app's user selector is
currently hardcoded to just "Dave" and "Jared" — this needs to change
before the wider crew can actually use it. See `BACKLOG.md`.

**What "good enough" means — confirmed, no change needed:** "Honest
answers admitting 'I don't know' is the best and only option, and guessing
is not acceptable, even with a caveat." This matches the system's existing
design exactly (`SYSTEM_PROMPT` already instructs Claude to say so plainly
rather than guess or hedge) — good confirmation the core design choice is
right, not something to revisit.

**System priority order beyond drivetrain:** electrical, generators, HVAC,
then other systems in no particular order yet.

**How he imagines using this in a real moment:** roughly half the time,
mobile phone — in an engineering space or on deck, away from his office.
The other half, at a desk with a computer/browser. **This matters**: the
"clunky on mobile" copy-button issue noted in `BACKLOG.md` isn't an edge
case at this usage split — worth real priority, not a someday item.

**Scanned/image-only documents:** confirmed these will matter eventually.
OCR + metadata would help. If there's no good OCR path, Jared suggested a
fallback: let the user view the actual source document/page directly
rather than making it searchable — specifically called out drawings (DWG)
with piping or electrical wiring diagrams as the case where seeing the
actual image matters most, since the visual structure carries information
text extraction can't. Urgency not yet determined. (This connects to the
earlier idea, mentioned when simplifying the citation UI, of linking
citations directly to a source PDF page — worth reviewing together, see
`BACKLOG.md`.)

**Citation precision — resolved, closing the open backlog item:**
page-level citations are completely fine; section-level is not necessary
and "may never be." No further work needed here — see `BACKLOG.md`.

**New document types:** may be needed once more documents are collected,
but not yet known which. Dave and Jared will discuss before adding any —
no action needed now.

**Front end:** "looks ok to proceed as is" — deployment (getting this
in front of Jared and the crew while he's onboard the next few weeks) is
now the top priority, ahead of new features.

**Ongoing TM ingestion:** Jared will keep uploading new TMs/documents to
the shared Drive folder, doing his best to follow the naming convention.
Dave will run ingestion as regularly as he can. This is the established
workflow (`docs/tm_upload_checklist.md`, `scan_folder.py`) continuing to
operate as designed — no process change needed.
