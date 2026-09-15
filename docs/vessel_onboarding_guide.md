# Fathom — Vessel Onboarding Implementation Guide

*This guide defines the complete process for onboarding a new vessel
into Fathom. Follow it in order. Do not skip steps — partial coverage
is worse than no system because it creates false expectations and
erodes engineer trust. Every step here exists because something went
wrong when it was skipped on the Polaris.*

---

## Phase 0 — Pre-onboarding preparation

### 0.1 Confirm vessel scope
- [ ] Confirm vessel name, type, and flag state
- [ ] Confirm tug/barge combo or single vessel (barge = separate onboarding)
- [ ] Confirm primary point of contact (Chief Engineer + Port Engineer)
- [ ] Confirm shared Drive folder has been created and shared with Chief Engineer
- [ ] Confirm Streamlit app has been deployed for this vessel
- [ ] Set app visibility to Private in Streamlit Cloud — go to
      share.streamlit.io → app Settings → General → App visibility →
      Private. This prevents the app from appearing on Streamlit's
      public Explore page. Default is Public which exposes the app
      name and screenshot to anyone browsing Streamlit's community.
- [ ] Confirm all env vars are set (SUPABASE_DB_URL, ANTHROPIC_API_KEY,
      VOYAGE_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL)

### 0.2 Create initial users
- [ ] Add Chief Engineer account via `manage_users.py`
- [ ] Add Port Engineer account via `manage_users.py`
- [ ] Confirm both can log in before proceeding

---

## Phase 1 — Build the equipment manifest (CRITICAL — do not skip)

This is the foundational step that was missing from the Polaris onboarding
and caused ongoing frustration with partial coverage. The manifest tells
you exactly what equipment is on the vessel, what manuals you need, and
what percentage of the library is complete. Without it you're flying blind.

### 1.1 Ingest the shipyard drawing package first
Before building the manifest, you need the vessel's general arrangement
and machinery arrangement drawings ingested. These are the authoritative
source of truth for what's on the vessel.

- [ ] Obtain from shipyard or owner: General Arrangement (A-series),
      Machinery Arrangement (A03), Piping schematics (P-series),
      Electrical drawings (E-series)
- [ ] Run `ingest_new_docs.py` on the drawing package
- [ ] Verify all drawings ingested cleanly (no vision extraction failures)

**Key drawings for the manifest:**
- `GeneralArrangement_*_A03MachineryArrangement_DWG_*` — primary equipment list
- `GeneralArrangement_*_A02GeneralArrangementsGa_DWG_*` — overall layout
- `Piping_*_P02FuelOilTransferPipingSchematic_DWG_*` — fuel system equipment
- `Electrical_*_E12ElectricalEquipmentArrgt_DWG_*` — electrical equipment

### 1.2 Run the equipment manifest extraction tool
```bash
cd ingestion
python3.14 build_equipment_manifest.py
```

This tool:
1. Queries the ingested drawing chunks for equipment references
2. Extracts manufacturer, model, item designation, and system/location
3. Cross-references against what's already ingested
4. Outputs a gap report: equipment on vessel with no manual in Fathom

- [ ] Review the manifest output
- [ ] Save the manifest as `docs/equipment_manifest_[vesselname].csv`
- [ ] Review the gap report — this is your document sourcing checklist

### 1.3 Review manifest with Chief Engineer
- [ ] Walk through the manifest with the Chief Engineer
- [ ] Mark items where he has the manual somewhere (vessel binder,
      personal laptop, OEM portal)
- [ ] Mark items where the manual needs to be sourced from OEM
- [ ] Mark items where no manual exists (note for future)
- [ ] Establish target coverage percentage before rollout
      (recommend: ≥80% of major systems before engineers use the system)

---

## Phase 2 — Document sourcing and ingestion

### 2.1 Collect documents from the vessel
The Chief Engineer will have documents in several places:
- [ ] Engine room binders (physical — scan or photograph)
- [ ] Personal laptop or shared drive
- [ ] OEM portals (CAT, Berg, etc. — login required)
- [ ] Shipyard documentation package (if not already obtained)

**Priority order for sourcing:**
1. Main propulsion (engines, thrusters, gearboxes) — highest value
2. Critical safety systems (fire suppression, steering, bilge)
3. Electrical (generators, switchboard, controls)
4. Auxiliary systems (HVAC, compressed air, water makers)
5. Deck equipment (capstan, crane, anchor windlass)

### 2.2 Source missing manuals from OEMs
For each gap in the manifest, search in this order:
1. Manufacturer website — many manuals are freely available as PDFs
2. Request directly from OEM technical support
3. Check if a text-layer PDF exists before accepting a scanned version
   (scanned PDFs require vision extraction — significantly slower and
   more expensive to ingest)

**Critical rule — always get text-layer PDFs when available:**
A 284-page scanned manual costs hours of vision extraction and
significant API credits. The same manual as a text-layer PDF ingests
in minutes. Always search for the manufacturer's official PDF first.

### 2.3 Ingest documents in batches by system
```bash
cd ingestion
python3.14 ingest_new_docs.py "/path/to/Drive/Vessel Documents"
```

- [ ] Main engines batch — ingest and verify
- [ ] Propulsion/drivetrain batch — ingest and verify
- [ ] Electrical batch — ingest and verify
- [ ] Piping/fuel/hydraulics batch — ingest and verify
- [ ] Hull/stability batch — ingest and verify
- [ ] Auxiliary systems batch — ingest and verify

**After each batch:**
- [ ] Review rename proposals with Chief Engineer before applying
      (he knows better than the title block which system a document belongs to)
- [ ] Check for duplicates: `python3.14 find_duplicate_files.py "/path/to/Drive/..."`
- [ ] Review scan output for warnings and failures
- [ ] Commit rename_log.csv to git

### 2.4 Re-run manifest gap report
After ingestion is complete, re-run the manifest tool to get updated
coverage percentage.

- [ ] Run `python3.14 build_equipment_manifest.py`
- [ ] Review updated gap report
- [ ] Document remaining gaps and reason (no manual exists, not yet sourced, etc.)
- [ ] Confirm coverage meets the ≥80% threshold before proceeding

---

## Phase 3 — Engineer Notes capture (on-site session)

This is the highest-value step that can't be done remotely. Schedule
2-3 days on the vessel with the Chief Engineer and ideally the Port
Engineer.

### 3.1 Pre-session preparation
- [ ] Print or have available the equipment manifest
- [ ] Prepare a list of open questions from the QA test pass
- [ ] Have the app loaded and working on a laptop

### 3.2 Walk the vessel systematically
Follow the same process as Navy officer qualification — physically walk
every space and trace every system:

- [ ] Engine room — main engines, generators, fuel system, cooling,
      exhaust, bilge
- [ ] Steering gear room — hydraulics, rudder actuators
- [ ] Pump room / void spaces — bilge, ballast, fuel transfer
- [ ] Electrical spaces — switchboard, panels, distribution
- [ ] Deck equipment — capstan, winches, anchor windlass, crane
- [ ] Pilothouse — navigation, controls, communications

For each system, capture Engineer Notes on:
- Known quirks not in any manual
- Non-standard configurations (e.g. "clutch filled to 80% not 75%")
- Workarounds for known issues
- Things that have failed before and how they were fixed
- Anything the Chief would tell a new engineer on their first day

### 3.3 Capture Engineer Notes in the app
- [ ] Log into Fathom during the walk-through
- [ ] Enter Engineer Notes in real time as the Chief talks
- [ ] Attribute notes correctly (Chief Engineer, Port Engineer, etc.)
- [ ] Aim for minimum 15 Engineer Notes before leaving the vessel

### 3.4 Run Q&A test session with Chief Engineer
Use the standard Q&A test question sheet (`docs/qa_test_questions.md`)
and add vessel-specific questions based on the manifest.

- [ ] Work through Section 1-6 questions with Chief Engineer
- [ ] Note any wrong answers (what the correct answer should be)
- [ ] Note any "not in system" responses — are these document gaps
      or retrieval failures?
- [ ] Note any questions where the answer is correct but incomplete
- [ ] Target: ≥80% Good or Partial before wider rollout

---

## Phase 4 — Pre-rollout verification

### 4.1 Technical verification
- [ ] App loads cleanly on Chief Engineer's device (laptop and phone)
- [ ] Document Library panel shows all systems correctly
- [ ] Tap-to-zoom works on drawings (test on phone)
- [ ] Conversational follow-up questions work naturally
- [ ] Engineer Notes appear correctly before answers
- [ ] Run `python3.14 review_feedback.py` — no unexplained thumbs-down

### 4.2 Data hygiene
- [ ] Run `python3.14 list_engineer_notes.py` — review all notes with
      Chief Engineer, remove any test/incorrect entries
- [ ] Run `python3.14 clear_test_data.py --apply` to clear development
      conversation history before engineers use the system
- [ ] Confirm all users are set up correctly

### 4.3 Coverage documentation
- [ ] Document final coverage percentage in the vessel record
- [ ] Document known gaps and plan to address them
- [ ] Set a target date for next document sourcing pass

---

## Phase 5 — Controlled rollout

### 5.1 Initial group (weeks 1-2)
- [ ] Chief Engineer and Port Engineer only initially
- [ ] Give specific test scenarios — not just "try it"
- [ ] Use real recent troubleshooting questions from actual work
- [ ] Collect 👍/👎 feedback — remind them thumbs-down is valuable data

### 5.2 Wider crew (weeks 3-4, if Phase 5.1 successful)
- [ ] Brief the engineering crew — what it is, what it isn't
- [ ] Provide the one-page user guide (see Section 6)
- [ ] Set expectations: system is good but not complete, always verify
      safety-critical information against the source document

### 5.3 30-day review
- [ ] Run `python3.14 review_feedback.py` — review all thumbs-down
- [ ] Review with Chief Engineer: what worked, what didn't
- [ ] Address any retrieval failures identified
- [ ] Source any missing manuals that caused frustration
- [ ] Decide: ready for subscription, or needs another development cycle

---

## Phase 6 — Ongoing maintenance

### 6.1 Document updates
When new equipment is installed or manuals are updated:
- [ ] Chief Engineer uploads new PDF to Drive
- [ ] Dave runs `ingest_new_docs.py` (until admin panel is built)
- [ ] Verify new document appears in Document Library

### 6.2 Engineer Notes review
- [ ] Monthly: review Engineer Notes with Chief Engineer for accuracy
- [ ] Remove outdated notes (equipment replaced, procedures changed)
- [ ] Add notes from any significant maintenance events

### 6.3 Coverage monitoring
- [ ] Quarterly: re-run manifest gap report
- [ ] Source any manuals added to the gap list
- [ ] Update coverage percentage in vessel record

---

## Appendix A — Document coverage targets by system

| System | Target docs | Notes |
|---|---|---|
| Main Engines | OMM, Parts List, Service Bulletins | Highest priority |
| Propulsion/Drivetrain | OMM, Drawings | Critical for operations |
| Electrical | Equipment arrangement, Switchboard OMM | Safety critical |
| Fuel Oil | Transfer schematic, Service schematic | Operations critical |
| Steering | Hydraulic OMM, Drawings | Safety critical |
| Fire Suppression | OMM, Activation procedures | Safety critical |
| Compressed Air | OMM, Schematic | Important |
| Bilge/Ballast | Schematic, Pump OMMs | Important |
| HVAC | OMM for major equipment | Moderate |
| Deck Equipment | OMM for capstan, winches, crane | Moderate |
| Stability | Stability booklet, Tank tables | Regulatory |

---

## Appendix B — Common sourcing resources

- **CAT manuals:** sis.cat.com (requires dealer login) or cat.com/support
- **Berg Propulsion:** bergpropulsion.com/support
- **USCG regulations:** ecfr.gov (Title 46 — Shipping)
- **ABS rules:** eagle.org/rules-and-guides
- **Manufacturer generic:** search "[manufacturer] [model] operation maintenance manual PDF"

---

## Appendix C — Time estimates per phase

| Phase | Estimated time |
|---|---|
| Phase 0 — Preparation | 2-3 hours |
| Phase 1 — Equipment manifest | 2-4 hours |
| Phase 2 — Document sourcing and ingestion | 2-4 weeks (documents arrive over time) |
| Phase 3 — On-site session | 2-3 days on vessel |
| Phase 4 — Pre-rollout verification | 2-4 hours |
| Phase 5 — Controlled rollout | 4-6 weeks |
| Phase 6 — Ongoing | 1-2 hours/month |

*The on-site session (Phase 3) is the most important investment and
the one most likely to be cut short. Budget for it properly — the
Engineer Notes captured during that session are often the highest-value
content in the entire system.*
