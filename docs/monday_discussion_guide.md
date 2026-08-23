# Monday Discussion Guide — Jared Check-in

Purpose: step back from building and re-ground on end-user needs before
adding more scope. Since this guide was first drafted, the system has moved
from a 3-document prototype to a real, persistently-ingested 14-document
library — including all 8 TMs Jared provided in his first real batch. This
is the first check-in where there's a genuinely substantial, real result to
show him, not just a plan.

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

## 4. Decisions to bring back from this conversation

Not decisions to make live with him necessarily, but things this
conversation should give us enough information to decide afterward:

- Does the equipment priority order need to change?
- Does "page-level citation" need revisiting given how he actually reads
  answers?
- Is a real front end more urgent than we assumed, given who'd actually
  use this?
- Any new TM naming/organization issues from his actual usage of the
  Drive folder?
- How urgent is OCR/vision-based extraction for scanned reference material,
  given what he actually uses it for?
