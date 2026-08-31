# Administrator's Guide — Adding New Technical Manuals

This is a working guide for onboarding new documents into Fathom –
Polaris, written for Dave (and anyone who takes over this role later).
It reflects the actual process as it exists today — not an idealized
version of it — including the real gaps we haven't automated yet.

Deliberately kept as a plain document, not a new tool, per Dave's own
call (Aug 2026): the range of document types the system will need to
handle is still being discovered — DEF documentation was the most
recent example — and it's easier to adjust a written process than to
rebuild automation every time something new comes up. Revisit this
choice once the workflow has settled down and the same few steps are
clearly being repeated the same way every time.

---

## 1. The real workflow today

1. **Jared uploads a new document** to the shared Drive folder, usually
   named as best he can manage — he's not trying to hit the exact
   convention below, and that's fine; he's the source of new material,
   not the librarian.
2. **Dave notices the new file** (currently: by periodically checking
   the Drive folder — there's no notification system) and **renames it**
   to match the real naming convention (Section 3).
3. **Dave runs `scan_folder.py`** (Section 5) against the whole
   Drivetrain TMs folder. New/changed files get processed automatically;
   everything else is skipped, fast.
4. **Dave reads the output** (Section 6) and follows up on anything
   flagged — a naming mismatch, a scanned document with no text layer,
   etc.

**The real gap in this process, worth naming directly:** once a file is
renamed, there's no record anywhere of what Jared originally called it.
If Dave needs to reconcile "did I already handle the file Jared sent
last Tuesday?" against his own memory, there's currently no way to trace
back to it. See Section 4 for the recommended (still fully manual) fix.

---

## 2. Where documents live

All Drivetrain documents live under one Drive folder, in loosely
organized subfolders by equipment (`Main Engines`, `Azimuth Thruster`,
`Shafting and Bearings`, etc.). **Subfolder structure is purely for
human navigation** — `scan_folder.py` scans recursively and doesn't care
where in the folder tree a file sits. Feel free to organize subfolders
however makes sense to you; it has zero effect on ingestion.

---

## 3. The naming convention

Every filename must match this exact pattern, or it gets skipped
entirely (reported, never guessed at):

```
System_Manufacturer_Model_DocType_RevX.pdf
```

**Precise rules** (from the actual code, not approximated):
- Exactly five parts, separated by underscores.
- Each of `System`, `Manufacturer`, `Model`, and `DocType` must be
  letters/numbers only — no spaces, hyphens, or other punctuation.
- `RevX` must literally start with `Rev`, followed by any letters/numbers
  (e.g. `Rev2`, `RevA`, `Rev07082025`, `RevUnknown` — all valid).
- Matching is case-insensitive, but stay consistent for your own
  sanity when scanning file lists later.

**Real examples already in the system:**
```
MainEngines_CAT_3512E_OMM_Rev11012021.pdf
AzimuthThruster_MBB_ShaftArrangementM1_DWG_Rev2.pdf
PropulsionControl_Berg_MPC800A_WiringDiagram_Rev62481C.pdf
MainEngines_CAT_3512E_DEFTier4AftertreatmentGuide_RevLEBM00236.pdf
```

**Known `DocType` values with friendly display labels today:**

| DocType (in filename) | Displayed as |
|---|---|
| `OMM` | O&M Manual |
| `DWG` | General Arrangement Drawing |
| `PARTSLIST` | Parts List |
| `SERVICEBULLETIN` | Service Bulletin |
| `WIRINGDIAGRAM` | Wiring Diagram |
| `REFDATA` | Reference Data |
| `EQUIPMENTLIST` | Equipment List — **special**: also triggers automatic extraction into the vessel equipment registry, in addition to normal ingestion. Use this exact DocType only for a genuine equipment list document. |

**You can invent a new `DocType` freely** — the system doesn't reject
unrecognized ones, it just displays them in their raw, uppercased form
instead of a friendly label (this is exactly what happened with the DEF
documents today: `DEFTIER4TRNG`, `DEFCOOLANTDOSINGCALCULATIONS`, etc. are
new, real DocTypes, not typos). If a DocType is going to be used
repeatedly, it's worth asking Claude to add it to the friendly-label
table above — a small, quick code change, not something worth doing for
a one-off document.

### Deciding what `System` to use — the DEF example

DEF (Diesel Exhaust Fluid) documents are correctly filed under
`MainEngines`, not a new system of their own — DEF is part of the
engine's aftertreatment system, not independent shipboard
infrastructure, and this keeps a general "main engine" question able to
find DEF content naturally. The general rule: **if a document is about a
subsystem that only exists because of a larger system, file it under
that larger system, and let `DocType` carry the distinction** (e.g.
`DEFTier4Trng`, `DEFTankSpec`) rather than inventing a new top-level
`System`.

This will come up again for the first non-Drivetrain systems (HVAC,
electrical, etc.) — see Section 7.

---

## 4. Recommended practice: track original filenames

Not built into the system — a plain habit, using whatever tool is
convenient (a spreadsheet, a note, even a plain text file). Each time a
file gets renamed, record:

```
Original filename (from Jared) | Renamed to | Date
```

This is the direct fix for Section 1's real gap — a simple, searchable
record of "what did Jared actually call this" without needing to
remember or guess later. Worth starting now, before the number of
renamed files grows large enough that reconstructing this from memory
becomes genuinely hard.

---

## 5. Running an ingestion

From the `ingestion/` folder, with all four credentials exported:

```bash
export VOYAGE_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export SUPABASE_DB_URL="..."
export SUPABASE_SERVICE_KEY="..."   # needed for page images specifically
cd ~/vessel-maintenance-platform/ingestion
python3.14 scan_folder.py "/path/to/Drivetrain TMs folder"
```

Safe to re-run at any time — files that haven't changed are detected by
a content hash and skipped instantly. Only new or genuinely modified
files get (re)processed.

---

## 6. Reading the output

- **`X unchanged — skipped`** — normal, expected on every run. Nothing
  to do.
- **`X new or changed — will process`** — these are the files actually
  being ingested this run.
- **`did NOT match naming convention — skipped, not guessed at`** — the
  filename doesn't match Section 3's pattern exactly. Fix the name and
  re-run; the file will then be picked up as new.
- **`processed with a warning`** — usually "no extractable text found in
  first pages," meaning the document (or at least its early pages) is
  likely scanned/image-based. This isn't necessarily a problem —
  **vision extraction** (see below) often handles this automatically —
  but it's worth a glance at what actually got indexed for that file if
  the content matters.
- **`N page(s) with no text layer — trying vision extraction...`** —
  the system is using Claude's vision capability to read pages that have
  no real text layer (drawings, scanned pages, dense screenshots).
  Followed by a count of how many succeeded. This can take real,
  noticeably longer processing time for a large scanned file — the
  wiring diagram (19 pages needing vision extraction) is the slowest
  real document ingested so far.
- **`split a dense table into N focused sub-chunk(s)`** — a page had a
  dense table (many rows of similar data, like a diagnostic code log)
  that got automatically broken into smaller, more specific pieces for
  better searchability. Nothing to act on, just confirms the mechanism
  fired.

---

## 7. When new systems beyond Drivetrain start arriving

A few things worth deciding deliberately when the first non-Drivetrain
document set shows up (HVAC, electrical, fire suppression, etc.), rather
than guessing in the moment:

- **`System` in the filename should be the new system's name directly**
  (e.g. `HVAC_...`, `Electrical_...`), following the same pattern
  already established.
- **The equipment dropdown (both the registry and Engineer Notes) will
  need real attention once a second system's equipment list is
  ingested** — right now it's a flat list, fine for ~10 drivetrain
  items, but genuinely won't scale cleanly beyond that without adding a
  `system` field to the registry. This is already a scoped, deferred
  backlog item — see `BACKLOG.md`, "Anticipated scaling issue." Worth
  revisiting the moment a second system's equipment list document
  actually exists, not before.
- **Expect the same DEF-style judgment calls to recur** — a new
  subsystem within a larger system (is it its own `System`, or a
  `DocType` under the parent system?) is a real decision each time, not
  something to standardize away in advance. Section 3's DEF example is
  the reference case to reason from.

---

## 8. Troubleshooting

- **A file was already ingested, but ingestion logic has since improved
  and you want it reprocessed with the new logic** (e.g. after a real
  fix like today's dense-table threshold change): use
  `reprocess_file.py` to clear just that specific file's tracked chunks,
  then re-run `scan_folder.py` normally — it will be treated as brand
  new.
  ```bash
  python3.14 reprocess_file.py "ExactFileName.pdf"
  ```
- **Want to see exactly what's actually stored for a specific
  document/page**, independent of whether search happens to find it:
  ```bash
  python3.14 inspect_page.py "title substring" page_number
  ```
- **Want to see what a real question would actually retrieve**, without
  spending an API call on a full answer:
  ```bash
  python3.14 answer_query.py --dry-run "your question here"
  ```

See `docs/architecture.md`'s "Diagnostic tooling" section for the full
list of tools like these and what each is for.
