# TM Upload Checklist

Quick rules for adding a new TM to the Drive library. Following these
means `scan_folder.py` can process it automatically — skipping any of
these usually means the file gets rejected (with a clear reason) rather
than silently causing a problem later.

## Before you add a file

- [ ] **It's a real PDF, not a scan of a scan, not a photo of a page.** If
  you can select/highlight text in the PDF when you open it, that's a
  good sign. If the whole thing is one big image per page, the system
  will still store it, but won't be able to search its text — worth
  knowing in advance rather than being surprised later (see "Not fully
  searchable" below).
- [ ] **It's not password-protected.** Remove any password before adding
  it — the system can't open a locked file.
- [ ] **It actually opens and displays correctly** on your own computer
  first. If it looks broken to you, it'll look broken to the system too.
- [ ] **The filename follows the naming convention exactly:**
  ```
  [System]_[Manufacturer]_[Model]_[DocType]_Rev[X].pdf
  ```
  Example: `Clutch_BergPropulsion_MCH6_OMM_RevA.pdf`

  - No spaces anywhere in the filename.
  - Manufacturer and Model should be one "word" each — if the real name
    has multiple words or a space (e.g. "Twin Disc"), squash it together
    instead: `TwinDisc`, not `Twin_Disc` or `Twin Disc`.
  - DocType should be one of: `OMM`, `GADrawing`, `PartsList`,
    `ServiceBulletin`, `WiringDiagram` (ask if you need a new one added).
  - If there's genuinely no revision marked anywhere in the document,
    use `RevUnknown` rather than leaving it out.

## What happens automatically when you run the scan

The system checks every file for you and reports anything that needs
attention — you don't have to manually verify these, just be aware of
what triggers each message:

| What it catches | What you'll see |
|---|---|
| Empty or 0-byte file (failed upload) | Reported as invalid, skipped |
| Corrupted / not actually a PDF | Reported as invalid, skipped |
| Filename doesn't match the convention | Reported separately, skipped until renamed |
| Scanned document with no searchable text | Processed, but flagged as a warning — stored for reference, not full-text searchable |
| Something goes wrong partway through one file | That one file is skipped; everything else in the batch still processes normally |

None of these stop the rest of the batch from processing — one bad file
never blocks the good ones.

## If a file gets flagged

- **Naming issue** → just rename it and run the scan again next time.
- **Corrupted/empty** → re-export or re-download the file and try again.
- **No text layer (scanned document)** → this one's a judgment call: still
  worth adding for reference (drawings, old scanned manuals with no
  digital original), but know it won't be found by a text search the way
  a normal manual would be.
