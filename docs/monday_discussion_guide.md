# Monday Discussion Guide — Jared Check-in

Purpose: step back from building and re-ground on end-user needs before
adding more scope. We've been building against the original brief's
example scenario and the drivetrain TMs available so far — this is the
first real chance to validate that against what Jared actually needs.

## 1. Show him something real first

Before asking questions, demonstrate the system working against his own
equipment — this grounds the rest of the conversation in something
concrete rather than abstract plans.

- Run 2-3 real questions live (or show recent results) against the TMs
  he's already provided — ideally including whichever new TMs he added
  this week, so he sees his own recent contribution already working.
- Worth showing one *good* answer and being honest about one *limitation*
  (e.g. the page-level citation choice, or the "I don't have enough
  information" behavior when something isn't in the TMs) — this sets
  realistic expectations rather than overselling.

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

## 3. Things worth being transparent with him about

- **Page-level citations, not section-level** — deliberate choice for
  accuracy over precision (see `BACKLOG.md`). Worth explaining briefly so
  it doesn't look like an oversight.
- **Some documents (like drawings) aren't fully searchable yet** — the
  thruster GA drawing is indexed by metadata only, not full text.
- **This is still just the two of you testing** — no real front end yet,
  everything runs from a command line. Worth being upfront about how early
  stage this still is if there's any risk he's expecting something more
  polished.

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
