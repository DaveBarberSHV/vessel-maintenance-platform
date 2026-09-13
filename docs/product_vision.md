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

Several real ideas from Dave, worth preserving precisely even though
none is being built yet — see their dedicated `BACKLOG.md` entries for
full detail:
- **Full-manual download**, with a bandwidth-aware warning before a
  large file downloads — a direct, concrete expression of "their
  library" meaning real, complete access.
- **Email-based document ingestion** — the real, longer-term vision for
  making it genuinely easy for a vessel owner to keep their library
  current, since most new documents will arrive by email in practice.
- **Vessel knowledge graph** — cross-referencing interconnected shipboard
  systems (which electrical circuits and valves tie to which piece of
  equipment) so Fathom can reason across documents, not just retrieve
  from one at a time. Real test evidence exists already (see
  `BACKLOG.md`) — semantic search alone doesn't reliably connect a
  narrative safety question to the drawing that answers it, which is
  exactly the gap this would close.
- **General knowledge documents** — authoritative content that applies
  broadly across commercial vessels (OEM generic engine/propulsion
  guidance, general maintenance practice) rather than being specific to
  Polaris, to fill genuine gaps vessel-specific TMs don't cover.
- **Regulatory and standards library** — USCG NVICs and Marine Safety
  Manuals, ABS Rules, SNAME publications, ASTM/SAE standards, NFPA 301,
  and similar. Real, authoritative value, but a genuinely different risk
  profile than everything else in this library: licensing/redistribution
  rights vary a lot by source (USCG material is generally public domain;
  ASTM/SAE standards are commercial and often prohibit redistribution —
  needs real verification before any ingestion decision, not an
  assumption), and regulatory content needs an ongoing currency/update
  process TMs don't, since a vessel's TMs don't change but the
  regulations referencing them do.
- **TSMS / Emergency Response Plan documents** — a related but distinct
  and much lower-risk idea: McAllister almost certainly already has a
  Towing Safety Management System and Emergency Response Plan as a
  Subchapter M regulatory requirement — real, owned, vessel-specific
  documents, not public standards, so none of the licensing question
  above applies. Jared is checking on availability (Sept 2026) — if
  these exist and can be obtained, they should be ingested the same way
  any other real document is, no new architecture needed.

**Why none of these are being pursued right now (Sept 2026):** a demo to
McAllister's VP of Engineering is roughly three weeks out, intended to
secure commitment to a formal pilot — real feedback, real usage metrics,
and eventually a paid license leading to broader fleet rollout. The one
thing that demo needs is a system that answers real questions reliably;
introducing new ingestion capability this close to that meeting risks
exactly the kind of instability found and fixed repeatedly this same
week (see `BACKLOG.md`'s Sept 2026 entries) for far less benefit than
continuing to harden what already exists. The right way to use these
ideas *before* the demo is as a roadmap story — a well-thought-out
"here's what's next if you commit" — not as something to rush into the
product itself.
