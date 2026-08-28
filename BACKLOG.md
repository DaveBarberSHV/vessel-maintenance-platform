# Backlog — "Fix Later" List

Things we've deliberately deferred so v1 doesn't stall. Each entry: what it is,
why it's deferred, and what would trigger picking it up.

---

## Deliberately deferred — real password/access gate before wider rollout

**What:** The app currently has no password or access control at all — a
free-text name field only (see `docs/architecture.md`). Anyone with the
URL can use it.

**Deliberately deferred (Aug 2026, Dave's explicit call):** at least a
week, tied to actual rollout readiness rather than a fixed date — Dave
wants more TMs ingested and more real testing done first, before opening
access to the wider crew (mechanics, port engineer, captain). Since only
Dave and Jared use it currently, the risk is low enough to sequence this
against real need rather than build it preemptively.

**Trigger to revisit:** whenever the wider-crew rollout actually becomes
imminent — build this shortly before then, not automatically "in a week"
if rollout itself has also slipped.

**Distinct from, and not to be conflated with, the separate exposed
`service_role` key issue below** — that's a credential-exposure risk
that exists regardless of app rollout timing, not an access-control
decision tied to how many people use the app.

---

## 🔺 PRIORITY (elevated Aug 2026) — Engineer Notes: field/tribal knowledge, tied to equipment

**Name confirmed (Aug 2026, Dave + Jared): "Engineer Notes."**

**What:** Capturing real-world, experience-based knowledge from engineers —
adjustments, quirks, and lessons learned that aren't in any manufacturer
document — tied to specific pieces of equipment, clearly separated from
official TM content. First raised as a passing idea when Supabase was
chosen (a factor in picking `pgvector` over a simpler option); sharpened
into something much more concrete and higher-priority by Jared (Aug 2026)
after seeing how the vessel equipment registry works.

**Jared's real example, which should directly shape the design, not just
motivate it:**
> "As far as the notes from real world experience. It would be important
> that the system keeps track of who is inputting the notes and
> differentiates it from manufacturer info. You can get factual reference
> data from the TMs. But then it should mention that USERSOANSO added a
> note. For example: Aug 27 — Jared A. noted that clutches should be
> filled +5% higher than manual specs due to additional pipe lengths
> installed, per CAT tech. We don't fill clutches to the recommended 75%,
> we go to 80%."

**Why this got elevated, not just captured (Aug 2026, Dave):** the real
value here is bigger than "notes" — it's addressing a genuinely expensive,
common industry problem: when a vessel loses a chief engineer, it loses
all the vessel-specific knowledge that engineer had, and management often
ends up re-troubleshooting problems that were already solved once. Dave's
view: this value proposition matters even more to the actual paying
customer (the vessel owner) than to day-to-day crew — an owner cares
directly about not losing institutional knowledge across crew turnover,
which is a genuine cost problem, not just a convenience.

**Design directly informed by Jared's example, not yet built:**
- **Attribution is a hard requirement, not optional** — who and when need
  equal visual weight to the note content itself, always. Reuses
  `user_name`, already flowing through the app for chat history — no new
  identity infrastructure needed.
- **Never blend into the "official" answer.** A note must appear as a
  clearly separate, visually distinct block — e.g. "Engineer note — Jared
  A., Aug 27: clutches filled +5% higher than manual spec due to
  additional pipe lengths, per CAT tech" — never silently merged into
  manufacturer spec text as if it were the same kind of fact.
- **Likely doesn't need new retrieval infrastructure for a first version.**
  Notes are naturally per-piece-of-equipment — the same `(category,
  position)` identity already built for `vessel_equipment`. The registry's
  proven pattern (always inject current state into every prompt,
  unconditionally, rather than relying on semantic search to happen to
  find it) likely applies directly here too, at least for a first version
  — no need to stand up separate semantic search over notes just to get
  real value.

**Entry-point / UX design (Aug 2026, discussed, not yet built):**
- **Two entry points, one primary and one secondary — not just a single
  standalone button.** A disconnected "+ Engineer Note" button as the
  *only* path risks the common failure pattern for this kind of feature:
  used once during a demo, then forgotten, since it competes with
  whatever the person actually came to do — and this app's own users are
  explicitly busy and impatient with extra steps (see Jared's own stated
  reasoning elsewhere in this file).
  - **Primary: inline, attached to an actual answer.** A small "📝 Add a
    note" action alongside the existing 👍/👎/Copy row. Since the
    equipment is already known from that answer's citations, the
    equipment field can be **pre-filled** ("Add a note about: Marine
    Clutch/Steering") with just a confirm/change option, not a cold
    dropdown — captures knowledge exactly when someone naturally thinks
    "actually, we do it differently," right after getting an answer.
  - **Secondary: a standalone "+ Engineer Note" button** (Dave's original
    idea, still right for this path) for the proactive case — someone who
    wants to log something without having asked a question first. Here,
    force a real equipment selection from the same category/position
    options already in the registry — not free text, to avoid notes
    landing under inconsistent naming (e.g. "clutch" in one place,
    "Clutch" in another).
- **Needs a "General / Other" option from the start**, not added later —
  not every note will cleanly map to one of the ~10 current registry
  entries, especially once HVAC/electrical/etc. get added, or for
  something genuinely vessel-wide rather than tied to one system.
- **Deliberately deferred, not a blocker:** editing or removing a note
  after the fact if it turns out wrong — a real v2 concern, not solved now.

**Related product/process idea, distinct from the engineering feature
itself — capture separately, don't lose it:** Dave's plan to build a
**formal onboarding process** for capturing vessel-specific knowledge
per system when a vessel/customer first comes onboard — rather than
relying purely on notes accumulating organically over time from ongoing
use. This is a business/process idea (how the knowledge gets captured
in the first place) as much as a technical one (where it's stored and
how it's surfaced) — worth designing deliberately together when this
gets built, not something the engineering side should assume or invent
alone.

---

## ✅ RESOLVED — Vessel equipment registry: know which model applies without asking

**What:** Real problem, raised by Dave (Aug 2026): Jared sent a one-page
document listing the actual drivetrain equipment installed on the vessel
(model, serial, key specs) — separate from any single TM, since TMs cover
a manufacturer's full model range but the vessel only has one variant of
each actually installed. A question like "what torque for the driveline
bearing bolts?" was genuinely ambiguous without knowing which bearing
housing was meant, even though a real engineer on the vessel would just
know.

**Built (Aug 2026):**
- `ingestion/extract_equipment_list.py` — reads an equipment list document
  and asks Claude to structure it into JSON (category, position,
  manufacturer, model, serial, flexible specs) rather than hand-writing
  parsing logic, since layouts will vary vessel to vessel and even
  between revisions of the same vessel's document.
- New `vessel_equipment` Postgres table, natural key `(category,
  position)` — chosen specifically because equipment gets replaced (the
  vessel is in the shipyard now); re-running extraction against an
  updated document updates existing entries rather than duplicating or
  leaving stale ones.
- `answer_query.py` now fetches the full current registry on **every**
  question, unconditionally — not dependent on retrieval happening to
  find the equipment list document via search, since a question rarely
  names the model explicitly. Degrades silently to no equipment context
  if the table doesn't exist or the connection fails — verified directly
  that this can never be the reason a question fails.
- `scan_folder.py` — new `EquipmentList` doc type (naming convention:
  `[System]_[Manufacturer/Vessel]_[Model]_EquipmentList_Rev[X].pdf`).
  Any file with this doctype gets normal chunking (so it stays a regular
  searchable TM too) **and** automatic registry extraction, in one pass —
  no separate manual step needed going forward.

**Verified end-to-end, real data throughout:**
- Manual extraction against the real document: all 10 entries correct,
  including correctly leaving manufacturer/model blank where the source
  document didn't state one (e.g. the wheels — a spec, not a model
  number) rather than guessing.
- Real question, real before/after: "what torque for the driveline
  bearing housing bolts?" went from *"you need to clarify which
  assembly"* (two real, different bearing housings existed in the
  library) to a direct, confident, correctly-sourced answer once the
  registry was wired in.
- Full automation confirmed via a real `scan_folder.py` run against the
  actual 22-document library: extraction fired automatically, produced
  the same 10 correct entries as the manual run, alongside normal
  chunking and page-image rendering for the same file in one pass.

**Related, now-prioritized idea:** "Engineer Notes" — a field-notes/tribal-
knowledge feature for engineers to record real-world findings per piece
of equipment, attaching naturally to this registry's `(category,
position)` identity — see the dedicated priority entry near the top of
this file for Jared's real example and the design it now points to.

---

## ⚠️ Known operational gotcha — deploys silently need a manual reboot

**What:** Twice now (Aug 2026) — once after a large data push (the
pgvector migration), once after the page-images feature push — Streamlit
Cloud's automatic redeploy did not reliably bring the app fully up to
date on its own. The app *looked* fine (loaded, answered questions
normally) but was silently broken in a real way: the first time, serving
stale data; the second time, silently failing to save any chat messages
at all (`save_message()` failures are deliberately swallowed — see
`app.py` — so this produced zero visible error, just quietly lost data).
Confirmed both times by checking real database state directly, not by
how the app looked.

**Real cost this time:** a genuine, unrecoverable small data loss — two
real Q&A exchanges asked during the broken window were never saved,
since they were never written to the database at all.

**Not yet root-caused** — could be something to do with cached resources
(`@st.cache_resource` connections) surviving a redeploy that should have
replaced them, could be a Streamlit Cloud platform quirk. Not investigated
deeply, since the reliable workaround is simple.

**Standing practice going forward:** manually reboot the deployed app
after every push that touches `app.py`, `db.py`, or dependencies —
don't rely on the automatic redeploy alone, and verify with a real
follow-up question rather than just glancing at whether the app loads.

---

## 🔺 OPEN — Exposed service_role key needs rotation (Aug 2026)

**What:** The Supabase `service_role` secret key was accidentally pasted
into chat while troubleshooting a connection-string mix-up (Aug 2026).
This key has full, unrestricted access to the project — more sensitive
than the Voyage key exposure earlier in the project.

**Status:** rotation not yet completed. Supabase's UI for this changed
recently — the classic "regenerate" button wasn't found on the API Keys
page; the "JWT Keys" → "Legacy JWT Secret" page appears to be the right
place (rotating the underlying JWT secret regenerates both `anon` and
`service_role` legacy keys), but this wasn't confirmed working yet.

**Why not fixed immediately:** didn't want to block the actual latency
investigation on a UI hunt; the exposure risk is real but the key hasn't
shown signs of misuse so far.

**Next step:** find and complete the rotation, or contact Supabase
support if the UI path isn't clear. Once rotated, `SUPABASE_SERVICE_KEY`
in both `.streamlit/secrets.toml` and Streamlit Cloud's deployed secrets
needs updating to the new value.

---

## ✅ RESOLVED (mostly) — Page images: view the actual diagram/table for a citation

**What:** Real problem, raised by Dave (Aug 2026): a 1415-page parts manual
documents each part as a table + exploded-view diagram, sometimes split
across "1 of 2 / 2 of 2" pages. Text extraction alone loses the diagram —
which callout number is which part, how pieces connect — even though the
table text extracts fine. Other TMs have diagrams embedded directly on
text-bearing pages too.

**Built (Aug 2026):** `ingestion/page_images.py` renders selected pages to
PNG (via `pdfplumber`'s page rasterization — confirmed good quality: fully
readable text, crisp diagrams, ~70KB/page) and uploads them to Supabase
Storage. A new `page_image_url` column on `tm_chunks` links each chunk to
its page's image, when one exists. The app shows a "🖼️ View page image(s)"
expander under any answer citing a rendered page; the CLI prints the same
URLs for testing.

**Why images are rendered at ingestion time, not on-demand:** the deployed
app never has access to the original PDF files — only extracted text
(in Postgres since the pgvector migration). Images have to be produced
once, wherever the real PDF bytes are (Dave's machine), and uploaded
somewhere the deployed app can reach.

**Which pages get rendered — not blindly every page:** a page is selected
if it has real text, OR either adjacent page does. This specifically
protects the "picture continues on the next page with little/no text of
its own" pattern (Dave's exact example) from being skipped by a naive
"only pages with text" rule, while still skipping genuinely blank pages.

**Deliberately deferred, discussed and parked (Aug 2026):** "can I see the
whole TM for this system?" — a real, different feature (viewing an entire
document, not one page). Likely easy path later: upload the original PDF
itself (once per document, not per page) and link to it directly, letting
the browser's own PDF viewer handle navigation. Not attempted now — solving
it today risked either re-rendering every page anyway (undoing the
storage savings from being selective) or building a second, different
mechanism alongside this one.

**Real gotcha worth recording — Supabase's newer API key format doesn't
work with Storage:** Supabase's current "Secret keys" (format `sb_secret_...`)
failed against the Storage API with a cryptic `"Invalid Compact JWS"` /
403 error. The fix: use the **legacy `service_role` key** instead (format
`eyJ...`, a classic JWT) — found under the "Legacy anon, service_role API
keys" tab on the same Settings → API page. `SUPABASE_SERVICE_KEY` in
secrets/env needs to be this legacy-format key, not the new one, until/
unless Supabase's Storage API adds support for the newer format.

**Verified working end-to-end (Aug 2026):** real page rendered, real
upload to Supabase Storage, real citation with a working public URL,
image displayed correctly both via direct URL and inline in the deployed
Streamlit app (confirmed against the SKF bearing housing reference doc —
the rendered page matched the answer's content exactly).

**Backfill — done (Aug 2026):** ran `backfill_page_images.py` against the
real library — ~2,228 images rendered and uploaded across 13 documents,
including the 1,415-page parts manual. Also confirmed (Aug 2026): the SKF
bearing housing document, which had been sitting under a leftover test
filename since the original page-images testing, self-corrected
automatically the next time a real `scan_folder.py` run encountered it —
rename detection cleanly restored its real name and regenerated its
images, exactly as predicted at the time and with no manual fix needed.

**Still open:**
- **No storage-size/cost monitoring set up yet** for the images bucket —
  worth a periodic glance at Supabase's dashboard as the library grows,
  same as the existing recommendation for Voyage/Anthropic usage.

---

## ✅ RESOLVED — Real "unexplained slowness" traced to a stranded idle-in-transaction connection

**What:** During page-images testing (Aug 2026), the app started hanging
indefinitely — a Postgres statement timeout on chat history, then general
unresponsiveness with no clear error, sometimes clearing up after a wait,
sometimes not.

**What it wasn't, ruled out with real evidence rather than assumption:**
- Wrong connection string — a real issue hit along the way (accidentally
  set `SUPABASE_DB_URL` to a JWT key at one point; separately, the Direct
  Connection hostname resurfaced instead of the Session pooler one), but
  fixing that alone didn't resolve the hang.
- Supabase free-tier "cold" pause — dashboard showed normal CPU/RAM/disk,
  9/60 connections in use, no paused/restoring status.
- Connection pool exhaustion — same dashboard check ruled this out.
- The `tm_chunks` table needing a real search index — a purpose-built
  diagnostic (`diagnose_latency.py`, timing connection/embedding/search
  steps separately, twice) showed the vector search itself was fast
  (0.25s) once actually connected — the original reasoning that a plain
  scan is fine at this library's scale held up.

**What it actually was:** `db.py`'s cached, long-lived database connection
(reused for the whole browser session) never set `autocommit`. Every
statement — including a plain read like `list_conversations()` — silently
opened a transaction that only closes when something later calls
`.commit()`. A session that got killed abruptly right after a read (this
project had several today, from testing) left that transaction stranded
on the server — a real one sat "idle in transaction" for almost 6 hours.
Confirmed directly with a second diagnostic (`diagnose_locks.py`, querying
Postgres's own `pg_stat_activity`) — found the exact stuck connection,
running the exact query, for the exact duration that matched the symptom.

**Fixed:** `db.get_connection()` now sets `conn.autocommit = True`. Every
statement commits immediately; no connection can be left holding an open
transaction regardless of how the session ends. The one stuck connection
was cleared manually (`pg_terminate_backend`) to unblock testing
immediately; the code fix prevents new ones from accumulating.

**Worth remembering:** this specific failure mode — indefinite hang, no
error message, intermittent — is a classic sign of a database-level lock
conflict, not an application bug in the usual sense. `pg_stat_activity` is
the right first place to look next time something like this happens
again, before assuming it's a connection string, cold start, or missing
index.

---

## ✅ RESOLVED — Deployed! Voyage embedding batching broke on real large documents

**What:** First deploy to Streamlit Community Cloud (Aug 2026) succeeded.
Immediately after, ingesting a real 21-document library update (Jared's
new batch, including a 1415-page parts manual and the real Azimuth
Thruster O&M manual) surfaced a genuine production bug: `scan_folder.py`
submitted an entire document's chunk list to Voyage as one unbatched API
call. Fine at the smaller scale tested so far, but broke on two real
documents at once — a 347K-token manual, and a 4,095-chunk dense parts
list — both exceeding Voyage's real per-request limits (1000 items,
~320,000 tokens).

**Fix took three real iterations, each based on actual measured data, not
assumption:**
1. First attempt: batch by item count (≤1000) + an estimated token count
   (~4 chars/token, a common English-prose heuristic) — fixed the
   item-count failure, but the token estimate was still too generous for
   this library's dense table content, which tokenizes far less
   efficiently per character than prose.
2. Second attempt: tightened to ~3 chars/token — still failed on the
   parts-list document (333,900 real tokens vs. an intended 200,000
   estimated ceiling).
3. **Final fix:** dropped token *estimation* entirely in favor of a hard,
   exact **character** limit (250,000 chars/batch), calibrated directly
   from the worst real density actually measured (~1.8 chars/token) with
   margin — no more guessing a ratio, since character count needs no
   estimation at all.

Also added: any single chunk larger than the batch limit is now flagged
and safely excluded (with a clear console warning naming the page) rather
than crashing the whole file's ingestion — see `VoyageEmbedder` in
`retrieval.py`.

**Verified:** full 21-document re-ingestion succeeded cleanly after the
final fix, including the two previously-failing documents. Confirmed via
a real question ("What are the tag out procedures for the z-drive shaft
lock?") that the Azimuth Thruster manual's content — the whole reason it
was uploaded — is genuinely searchable, not silently dropped.

---

## ✅ RESOLVED — Page citations read like the document's own page numbers, but aren't

**What:** Real confusion during testing (Aug 2026): a citation read "p.
672" for a 1415-page manual. The document's own printed page number, in
the margin, was "636" — a 36-page gap caused by front matter/cover pages
before the content starts. The citation's page number is the PDF file's
physical page position, not the document's internal printed numbering,
and nothing made that distinction clear.

**Fix:** Citations now read "p. 672 of 1415" (total page count added to
chunk metadata) whenever that's available, making clear it's a position
within the file — not the printed page number — without the much bigger
effort of extracting and matching each document's actual printed page
numbers (deferred; margins aren't consistently formatted across
manufacturers, so that would be real, separate engineering).

**Note:** the library's 21 already-ingested documents don't have this
field yet and will keep showing the plain "p. X" format until they're
next re-ingested (a rename, content update, or a deliberate full
rebuild via `python retrieval.py build`) — deliberately not forced
immediately to avoid an unplanned full re-embedding of the whole library.

---

## ✅ RESOLVED — Ask a clarifying question instead of "not enough information" when a term is ambiguous

**What:** Real example (Aug 2026, Jared's first live test): asked "We have
a bearing running at 220 degrees F. What is going to happen?" The manual
covers several distinct bearing types with different fault thresholds each
— the system correctly avoided guessing, but its "the excerpts don't
contain enough information" response wasn't as helpful as it could have
been. Dave's request: when the retrieved excerpts suggest the question
could mean one of several specific things, ask the user to clarify which
one, rather than just reporting insufficient information.

**Constraint from Dave (Aug 2026):** at most **one** clarifying question per
issue — never loop. Revisited explicitly once more before building (Aug
2026, after the vessel equipment registry landed): kept at one rather than
raised, since the registry had already resolved a large share of what used
to need clarification (which model/variant applies), narrowing what's left
to genuinely open-ended cases less likely to benefit from a second round
anyway — and Jared's original reasoning (busy users, terse writers, don't
want a back-and-forth) still held.

**Built (Aug 2026), following almost exactly the approach outlined below:**
- `SYSTEM_PROMPT` instructs Claude to ask one clarifying question grounded
  in the actual distinct options visible in the retrieved excerpts (not a
  generic "could you clarify?"), and — critically — to never ask a second
  one, falling back to the most likely answer with a stated assumption even
  when a follow-up reply doesn't fully resolve things or retrieval for that
  follow-up turns out noisy.
- `get_answer()`/`build_prompt()` accept an optional `previous_exchange`
  (immediately-prior question + answer only, not full history — deliberately
  scoped to just what's needed for Claude to tell "did I already ask here").
  `app.py` constructs this from session state and passes it automatically.
- Retrieval for a follow-up reply combines it with the *original* question
  before searching — a short reply alone ("the driveline one") often isn't
  enough signal on its own for good retrieval.
- CLI debug support (`--previous-question`/`--previous-answer`) added
  specifically to reproduce and inspect real conversation scenarios without
  needing the full app.

**Verified with real evidence, several angles:**
- A dry-run reproduction of a real live exchange confirmed the previous-
  exchange context genuinely reaches the prompt correctly (not just written
  but actually wired end to end).
- That same dry-run surfaced a real, separate finding: a follow-up term
  that doesn't exist anywhere in the current library can pull in noisy,
  irrelevant retrieval (parts-list tables sharing a generic word like
  "bearing") — this specifically motivated strengthening the fallback
  instruction so the model never restarts the conversation even when
  follow-up retrieval is poor, only that.
- Live app testing (Aug 2026) across the real motivating example: the
  system answered directly and correctly for content it had (the CAT
  engine bearing limit, with correct °F/°C reasoning and an honest note
  that the limit doesn't extend to other equipment), and — when a
  follow-up used a term matching nothing in the library — stayed honest
  ("the excerpts don't reference a PTI bearing") rather than guessing or
  looping.

**One nuance not directly exercised in live testing, worth knowing rather
than re-litigating:** the exact "asked once, user replies, still ambiguous,
falls back with a stated assumption instead of asking again" sequence
wasn't caught live in the two real test runs (the first turn each time
either resolved directly or found nothing at all, rather than asking a
question that then got an ambiguous reply). The mechanism is verified
correct via the dry-run prompt inspection and the strengthened fallback
instruction; the precise three-step live sequence just didn't happen to
occur in these particular real questions. Not blocking — flagged here so
it's a known gap, not a forgotten one, if it's ever worth a dedicated test.

---

## ✅ RESOLVED — Fahrenheit questions missed Celsius-only manual content

**What:** Same real example as above. The correct chunk (MPC800A fault
table, p. 34) exists and uses the exact term "PTI bearing" with thresholds
in Celsius only (>70°C, >90°C) — a question using "bearing" (not "PTI
bearing") in Fahrenheit missed it entirely. A near-identical question
phrased in Celsius with "PTI bearing" found it correctly, confirming this
wasn't a fluke.

**Fix (Aug 2026):** `expand_temperature_units()` in `answer_query.py`
detects Fahrenheit mentions in a question and appends the Celsius
equivalent — used only for the search step, never shown to the user or
substituted for what Claude actually answers. Also raised `top_k` from 3
to 5 (retrieves more candidate chunks), giving imperfectly-phrased
questions more room to still include the right one.

**Verified:** Conversion logic tested against real and edge-case inputs —
correctly converts "220 degrees F" → "104.4°C" (safely above the 90°C
shutdown threshold, so should now surface the right fault-table row),
correctly ignores Celsius-only questions, correctly avoids false positives
like part numbers (e.g. "ABC-220F"). Full retrieval-ranking verification
needs a live Voyage call this sandbox can't make — Dave to confirm with
the real original question against the real index.

**Still open:** the "bearing" vs "PTI bearing" terminology gap itself
(generic term vs. the manual's specific term) isn't fully solved by this —
`top_k=5` helps by casting a wider net, but a generic-term question could
still miss a specific-term chunk in some cases. The clarifying-question
feature above is one path to make this less costly when it happens: even
if the exact right chunk doesn't surface, surfacing a chunk that reveals
"there are several distinct bearing types" is itself useful if Claude asks
the user to disambiguate.

**Generalized (Aug 2026):** the fix was broadened from a temperature-only
function to `expand_units()`, a small table of `(pattern, conversion)`
entries, and pressure (psi → bar and MPa together, since manufacturers
aren't consistent about which metric unit they use — GEWES states the same
spec in both) was added as the second unit type. Verified: "300 psi" →
correctly expands to "20.7bar, 2.1MPa" for search purposes.

**Requested next (Aug 2026, Dave):** **torque** (crew's wrenches likely
ft-lb, manuals — e.g. GEWES's flange-bolting table — in Nm) and **length**
(likely inches vs. mm) — both expected to come up regularly, same pattern
as temperature and pressure. Straightforward to add to the existing
`UNIT_CONVERSIONS` table — each is one more entry, not a rewrite. Hold for
the batch with Dave's meeting notes.

**Refinement noted, not yet investigated (Aug 2026):** live-tested a
follow-up question — "What happens if grease gun pressure hits 300 psi?"
— specifically *because* 300 psi (≈2.07 MPa) sits almost exactly on the
real, correct answer: the Service Bulletin's documented 2 MPa seal-damage
threshold (confirmed accurate — see the Service Bulletin p.3 text, not a
fabrication). Despite `expand_units()` correctly including "2.1MPa" in the
search, retrieval still didn't surface that specific chunk — it pulled a
related-but-different number instead (the shaft's internal relief valve
spec, 0.5–1.0 MPa, from a different document) and correctly said "not
enough information" rather than guessing. Safe outcome, but shows unit
conversion alone doesn't guarantee retrieval consistency across different
phrasings of a question about the same real fact. Dave: hold for later,
don't forget. Worth a fresh, focused look — possibly related to `top_k`
tuning, possibly to how multiple close-but-distinct numeric specs compete
for the same ranking slots.

---

## ✅ RESOLVED — Chroma index committed to git as a deployment stopgap (Aug 2026)

**What:** `ingestion/chroma_db/` (previously `.gitignore`'d as derived data)
was committed to git specifically so Streamlit Community Cloud's deployed
app has something to query — a hosted app has no access to Dave's local
machine's filesystem otherwise.

**Why this was a stopgap, not the real fix:** git isn't built for binary
data like this, and it goes stale immediately — running `scan_folder.py`
locally again updates Dave's local index but does nothing for the
deployed one until someone remembers to re-commit and push it. This was
a deliberate trade for getting a real, working demo in front of Jared
quickly, not a decision to keep long-term.

**Confirmed the strain was real, not theoretical:** after ingesting 7 new
documents in one batch (21 documents, ~5,300 chunks), the committed
database file grew to 58.92 MB — over GitHub's recommended 50 MB
guideline — and a redeploy failed to pick up new data automatically,
needing a manual "Reboot app." Both real, confirmed symptoms, not
hypothetical risk.

**Resolved (Aug 2026):** migrated vector storage from local Chroma to
Supabase's `pgvector` extension. `retrieval.py` and `scan_folder.py`
rewritten to read/write a `tm_chunks` Postgres table instead of a local
Chroma collection — `answer_query.py` and `app.py` needed zero changes,
since both already went through the `query_chunks()`/`get_answer()`
abstraction layer built earlier. A one-time migration script
(`migrate_chroma_to_postgres.py`) copied all 5,386 existing chunks and
their already-computed embeddings directly from the committed Chroma
database into Postgres — no Voyage re-embedding, no cost.

**Verified at every layer:** unit tests on the new SQL/batching logic,
a real end-to-end test against the actual production Chroma data (5,386
in, 5,386 migrated), the rename-detection path re-tested against the new
backend, a real CLI query, a local Streamlit test, and finally the actual
**deployed production app** — all confirmed working, including a repeat
of the exact original "z-drive shaft lock" test question. The next code
push after this migration was 5.23 KiB, versus the 49+ MiB push that
originally surfaced this problem.

**Remaining cleanup, not urgent:** `ingestion/chroma_db/voyage/` is no
longer used for anything and was removed from git tracking going
forward (back in `.gitignore`), but the ~59 MB already sitting in the
repo's git *history* doesn't shrink automatically — that would need a
more involved history rewrite, deliberately not done casually. Worth
revisiting later if repo size ever becomes a real problem again; not
urgent now that new pushes are small.

---

## ✅ RESOLVED — User selector hardcoded to two names, blocks the real user base

**What:** `app.py`'s "Who's asking?" sidebar selector only offered `Dave`
and `Jared` — anyone else literally could not use the app, since there was
no way to select or enter a different name.

**Why this was urgent (Aug 2026):** Jared's answer to "who will actually
use this" was much broader than assumed when this was built: Jared
himself, his mechanics and engineers, the port engineer during in-port
maintenance, the ship's captain, and possibly others — see
`docs/monday_discussion_guide.md`. None of them could get past the name
selector.

**Fixed (Aug 2026):** Replaced the fixed dropdown with free-text name
entry, with light normalization (trim + title-case) so the same person
typing their name differently on different visits still groups correctly
under "Past conversations." Live-tested: a brand-new name ("Port
Engineer") correctly got a fresh conversation with no history, and
re-typing an existing name ("Dave") correctly pulled back real prior
history — confirming both the new-user and returning-user paths work.

---



**What:** The 📋 Copy expander (added for step 6 of the frontend build)
uses Streamlit's built-in `st.code()` copy icon. Confirmed working on
desktop; confirmed working but "clunky" on iPhone (Aug 2026) — exact UX
issue not pinned down (icon visibility/tap target size most likely, given
the icon is designed for hover-based desktop interaction).

**Elevated to priority (Aug 2026):** originally deferred pending Jared's
real answer on desk vs. mobile usage. That answer came back: roughly
**half his real usage will be on a mobile phone**, in an engineering space
or on deck — not an edge case. Worth real attention, not a someday item.

**Why not fixed immediately anyway:** the underlying browser clipboard API
works fine even when the custom icon doesn't behave nicely —
long-press-to-select still works as a fallback on any phone regardless, so
this isn't fully blocking. But given the confirmed usage split, it should
be picked up soon rather than left indefinitely.

**Path to fixing it:** A small custom HTML/JS copy button (via
`st.components.v1.html`) sized and styled for touch, instead of the
built-in `st.code()` icon.

---

## ✅ RESOLVED — First real (non-sandbox) ingestion run: GEWES bilingual cardan shaft manual

**What it was:** All prior testing (TF-IDF vs. Voyage comparison, checkbox-table
fix, etc.) ran inside Claude's chat sandbox — ephemeral, never persisted to a
real database anywhere. This was the first time `scan_folder.py` ran against
Dave's own machine with a real local Chroma index and a live Voyage key.

**What got tested (Aug 2026):** Added `CardanShafts_Gewes_All_OMM_Rev3.pdf` —
a 20-page bilingual German/English manual — via `scan_folder.py`, pointed at a
folder containing only that one file.

**Results:**
- All 20 pages had a real text layer; 20/20 chunks embedded (0 metadata-only).
- Bilingual German/English prose extracts cleanly in correct reading order —
  pdfplumber's default position-based sort naturally interleaves
  German-sentence/English-sentence pairs since both columns sit at matching
  vertical positions on the page. No special-casing needed for bilingual docs.
- Revision confirmed from the actual cover page ("Edition 03/2012, 13.03.2012"),
  not inferred from the filename.
- `answer_query.py` correctly answered a grease-spec question (citing p.10)
  and a flange-bolt-torque question — the latter pulled the **entire 16-row
  torque table** and matched the correct torque to the correct flange size,
  citing both source pages.
- This is the first live proof that `scan_folder.py`'s `collection.add()` path
  (previously untested outside the hash-logic unit test — see the still-open
  entry below) works correctly end-to-end against a real Voyage-backed Chroma
  index.

**Important side-effect worth knowing:** the original three TMs (MPC 800A,
MCH6, azimuth thruster) have only ever been tested inside the ephemeral chat
sandbox — they were never run through `scan_folder.py` and are **not yet in
any real, persistent database**. Dave's local Chroma index currently contains
only the GEWES manual's 20 chunks. Folding the original three in for real is
still an open task (see discussion in project chat, Aug 2026) — they need the
same naming-convention rename + `scan_folder.py` treatment as any new TM,
not a special case.

**Also surfaced and fixed in this pass:** `ingestion/requirements.txt` only
listed `pdfplumber` — `chromadb`, `voyageai`, and `anthropic` were all missing
(their imports are inline inside functions in `retrieval.py` and
`answer_query.py`, so `ModuleNotFoundError` only surfaced at the moment each
was actually called, not at import time). Fixed by listing all four.

---

## Dense spec table result — flange-bolt torque table did NOT get lost

**What:** The GEWES manual's Tab. 1 (flange bolting torque, 16 rows × 8
columns, page 16) is structurally the same kind of dense numeric table that
originally motivated the priority fix below. Live query: "What is the
tightening torque for the flange bolts?"

**Result:** Retrieval + Claude correctly returned the **full table**, matched
each torque value to the correct flange diameter, and cited both source
pages accurately — even before the table-aware chunking fix existed. Useful
signal that uniform numeric tables were less at-risk than the MPC 800A case,
which mixes many different kinds of values (temperatures, frequencies,
voltages, standard numbers) in one chunk. See the resolved entry below for
the conclusive fix and live confirmation against the actual MPC 800A case.

---

## `scan_folder.py` doesn't detect renamed files — creates silent duplicates

**What happened (Aug 2026):** The GEWES cardan shaft manual was renamed from
`CardanShafts_Gewes_All_OMM_Rev3.pdf` to `Shafting_Gewes_CardanShafts_OMM_Rev3.pdf`
as part of a broader naming-convention cleanup (adding `DWG`/`RefData` doc
types). The file's *content* didn't change — only its name. Re-running
`scan_folder.py` did not recognize this as the same document.

**Why:** `manifest.json` (and therefore all change-detection) is keyed by
exact filename, not by file content hash alone. A rename looks identical to
"a brand new file that happens to have similar content" — there's no logic
that compares content across different filenames to catch this case. Result:
the old chunks stayed in Chroma/`chunks.jsonl` untouched (never flagged as
stale, since their filename key was simply absent from the new scan), while
a second, fully duplicate set of chunks was added under the new name and a
new chunk-ID scheme. 20 chunks became 40 for the same physical document.

**How it was caught:** Reading `scan_folder.py`'s own summary output
carefully — it reported "0 unchanged, 14 new or changed" when 1 of those 14
should have been recognized as already-indexed. Caught before running any
real queries against the corrupted index.

**Fixed for this instance:** One-off cleanup removed the 20 stale chunks
under the old filename from Chroma, `chunks.jsonl`, and `manifest.json`,
leaving the correctly-tagged new-filename version intact.

**Path to fixing properly:** `scan_folder.py` needs rename detection —
e.g. hash every file first, and if a hash exists in the manifest under a
*different* filename than the one currently being scanned, treat it as a
rename (update the filename key, keep or refresh the chunk IDs) rather than
silently leaving the old entry orphaned while adding a new one. Until fixed,
**renaming a file in the TM library requires a manual cleanup pass** —
worth a note in `docs/tm_upload_checklist.md` warning against renaming
already-ingested files without also running a cleanup step.

---

## ✅ RESOLVED (won't fix) — Citation precision: page-level → section-level

**What:** Citations currently resolve to `document + revision + page number`
(e.g. "MCH6 O&M Manual, p. 40"). The original target was section-level
(e.g. "§8.4, pp. 142–144").

**Why originally deferred:** A generic regex heading-detector was tried
against the real manuals and proved unreliable — it matched
table-of-contents entries and stray numbered sentences as if they were
section headings, which would have produced *wrong* citations that look
precise. Page-level citation is 100% reliable; false section precision is
worse than no section precision. Deferral was explicitly conditioned on
"revisit once there's a real user (Jared) generating query volume."

**Resolved (Aug 2026):** that real-user check happened. Jared's answer,
after real usage: page-level citations are completely fine; section-level
"is not necessary and may never be." No further engineering planned here —
closing this out rather than leaving it open indefinitely.

---

## Azimuth thruster drawing has no text layer

**What:** `70958__THRUSTER_ARAZIMUTH_Rev_I.pdf` is a single-page vector CAD
drawing (general arrangement + BOM table). No extractable text — text
extraction returns nothing. As of Aug 2026, 4 more scanned reference
documents (image-only balancing reports) share this same limitation.

**Why deferred:** OCR/vision-based extraction of dimensioned engineering
drawings is a materially bigger lift than manual text parsing, and v1 scope
is Q&A over manuals, not drawings.

**Current handling:** Indexed by metadata only (drawing number, equipment,
revision) with no searchable text chunk. If a query might need it, the
answer should surface it as a visual reference (e.g. "see Drawing 70958 Rev
I") rather than silently omitting it.

**Jared's real-usage guidance (Aug 2026):** confirmed this will matter
eventually — OCR + metadata would help. If there's no good OCR path,
Jared's suggested fallback: let the user view the actual source
document/page directly rather than requiring it to be searchable —
specifically for drawings with piping or electrical wiring diagrams, where
the visual structure itself carries information that text extraction can't
capture even if OCR technically succeeds on the words present. Urgency not
yet determined — revisit once there's a concrete case blocking someone.

**Path to fixing it — two options, not yet chosen between:**
1. Vision-based extraction of BOM tables/dimensions from drawing sheets, or
   at minimum image-embedding-based retrieval so a query can at least
   *surface* the right drawing even without pulling text from it.
2. Jared's simpler fallback: skip OCR, just make it easy to view the actual
   source page/document directly. This connects to an idea raised earlier
   (Aug 2026, when the citation UI was simplified per Dave's feedback) —
   linking citations directly to a source PDF page (e.g. a
   `#page=N`-anchored link to a hosted copy of the file) rather than
   showing extracted text at all. Worth discussing together which path (or
   both, for different document types) makes sense before building either.

---

## ✅ RESOLVED — Dense spec tables get lost in page-level chunks

**Elevated (Aug 2026):** Dave confirmed many TMs in the full drivetrain
library have tables more complex/dense than this prototype's examples —
no longer a corner case, made top priority ahead of front-end work.

**What:** Tested query "what is the operating temperature range for the
central unit" against the real MPC 800A manual. The correct answer (0–50°C)
is on page 10 — but retrieval missed it, surfacing descriptive pages about
the Central Unit instead.

**Why:** Page 10 is one giant technical data table (main data + environmental
tests + EMC tests all together) collapsed into a single text chunk. The word
"temperature" is one hit among dozens of other equally-rare terms (kHz, dB,
VDC, IEC standard numbers) in that same chunk, so it doesn't stand out enough
to rank highly — with either the TF-IDF placeholder or a real embedding
model, since the problem is chunk granularity, not the embedding method.

**Fix (Aug 2026):** Table-aware chunking added to `parse_and_chunk.py`.
Tables with more than 8 data rows (`TABLE_ROW_THRESHOLD`) now also get
split into standalone sub-chunks of 6 rows each (`TABLE_ROW_GROUP_SIZE`),
each with the header row repeated for column-label context and a note
identifying which page/table/row-range it came from — in addition to the
existing full-page chunk, not replacing it. Smaller tables are left as-is.

**Verified two ways:**
1. In sandbox testing against the real GEWES manual's 16-row flange-bolt
   torque table: correctly split into 3 labeled sub-chunks (rows 1-6, 7-12,
   13-16), while the smaller 6-row bilingual maintenance-interval table on
   an earlier page was correctly left untouched. Full 4-document test run
   showed no regressions.
2. **Live, conclusive fix confirmed on Dave's machine (Aug 2026):** full
   reindex of all 14 real TMs, then re-ran the *exact* original failing
   query. Result: `"0 – 50°C ... Sources: PropulsionControl - BergPropulsion
   MPC800A O&M Manual, RevA, p. 10"` — correct answer, correct citation.
   This is the first live proof this specific, long-standing priority item
   is actually fixed, not just theoretically addressed.

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

## ✅ RESOLVED — Voyage embedding batch pacing won't scale to the full TM library

**What:** `retrieval_voyage.py` originally paused 15 seconds between every
batch of 20 chunks, added as a quick fix when the first embedding run hit
Voyage's reduced rate limit (before a payment method was added). First
real run succeeded: 112 chunks in ~6 batches, ~1.5 minutes overhead.

**Why originally deferred:** Worked fine for a 112-chunk corpus. Wouldn't
scale gracefully once Jared's full TM library needed (re-)embedding — flat
unconditional pauses add up fast and aren't smart about it.

**Resolved (Aug 2026):** exactly option (1) from the original path-to-fixing
notes below happened, forced by real necessity rather than chosen
proactively — batching was rebuilt around Voyage's real per-request limits
(hard character-based batches, not a conservative fixed 20) after two real
documents broke the old unbatched approach entirely. See the "Voyage
embedding batching broke on real large documents" entry above for the full
story. Batches are now sized close to Voyage's real limits, with a brief
1-second pause only between batches when more than one is needed — far
less overhead than the original 15-second fixed pause, and no unconditional
pausing for documents small enough to fit in one batch.

---

## ✅ RESOLVED — Incremental scanner's Chroma add/delete path needs a live test

**What:** `ingestion/scan_folder.py` was built to solve a real workflow
problem — as Jared adds TMs to Drive day by day, re-embedding the entire
library on every addition would waste time and Voyage API cost. It hashes
each file and skips anything unchanged, only processing new or changed
files.

**What was verified vs. not, originally:** the hash-comparison logic was
tested directly and worked. The actual Chroma `collection.add()` /
`collection.delete()` calls for new/updated chunks could NOT be tested
from Claude's sandbox (no network access to Voyage), so that path was
implemented but not yet proven against a real index.

**Resolved:** thoroughly live-tested many times over since — real
ingestion runs across a growing library (14 → 21 documents), real renames
(triggering the delete/re-add path), and real incremental additions in
batches, including two that surfaced and led to fixing real embedding
batch-limit bugs. The add/delete path has been exercised extensively
against production data at this point, not just once.

---


