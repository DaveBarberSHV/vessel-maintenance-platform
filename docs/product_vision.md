# Product Vision — Strategic Principles

This document is different from `architecture.md` (what's built) and
`BACKLOG.md` (specific deferred technical items). It's for the bigger,
slower-moving thinking that should inform *many* future decisions —
written down so it survives past any one conversation, and so future
backlog items and architecture choices can be checked against it rather
than reinvented each time.

---

## "Their Library" — the core principle (Sept 2026, Dave)

**The real problem this product solves, stated plainly:** today, a
vessel's technical library is scattered — thumb drives, individual
computers, folders, desk drawers. Owners aren't maintaining it well not
because they don't care, but because the volume, format, and pace
documents arrive in makes it genuinely hard: TMs at vessel acceptance,
manufacturer updates over the equipment's life, replacement systems
bringing entirely new documentation, and real engineer knowledge — a
problem recognized, a fix worked out — that mostly never gets written
down anywhere at all.

**Fathom's core identity, not just a feature list:** this product *is*
the vessel owner's library — not a search tool bolted onto their
documents, but the actual organized home for their TMs, drawings,
service data, and the accumulated real-world knowledge of their own
engineers (Engineer Notes), together, over time.

**The trust commitment this requires, stated explicitly:** a
manufacturer provides a TM to a vessel owner with the understanding that
the owner may use it operationally and give it to their own employees to
do their jobs. For an owner to feel comfortable putting that document
into Fathom, the same understanding has to hold: **these are the
owner's documents, used only to serve cited answers and source pages
back to them — never for any other purpose.** This isn't just a policy
statement for a legal page; it's the thing that gives an owner the
confidence to actually contribute their real documents in the first
place, which is what the whole product depends on.

**Real, direct implications of taking this seriously:**
- The full-manual download feature (see `BACKLOG.md`) exists because "a
  library" means real, complete access to what's in it — not just
  fragments surfaced in an answer.
- The multi-vessel architecture decision (see `BACKLOG.md`) is this same
  principle, technically: "their library" has to mean genuinely,
  structurally *theirs* — not just kept separate by convention once more
  than one owner's data lives in the same system. This is a real
  security architecture question, not just a business one — see the
  planned NIST controls review.

---

## Multi-vessel / multi-owner reality check (Sept 2026)

**Real, concrete context:** Jared's company — the real first target
customer — owns 50+ large vessels. Most real ship owners likely operate
more than one vessel. This surfaced while discussing the full-manual
download feature, and turned into a genuine architecture fork worth
deciding deliberately, not defaulting into. Full technical detail and
both real options are captured in `BACKLOG.md`, since it's fundamentally
a technical/security decision — this entry exists to record the
*business* reality that makes it a real decision worth having, not a
hypothetical.

---

## Future vision, explicitly not being built now

Two real ideas from Dave, worth preserving precisely even though neither
is being built yet — see their dedicated `BACKLOG.md` entries for full
detail:
- **Full-manual download**, with a bandwidth-aware warning before a
  large file downloads — a direct, concrete expression of "their
  library" meaning real, complete access.
- **Email-based document ingestion** — the real, longer-term vision for
  making it genuinely easy for a vessel owner to keep their library
  current, since most new documents will arrive by email in practice.
