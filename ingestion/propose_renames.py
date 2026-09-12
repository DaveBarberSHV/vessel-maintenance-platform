"""
Batch rename proposal for shipyard DWG files — produces a CSV for
human review BEFORE anything changes on disk.

Reads a folder, identifies files that don't match Fathom's naming
convention, proposes a convention-compliant name for each one, flags
any that appear to duplicate an already-ingested document, and writes
everything to a CSV. Nothing on disk is touched until the separate
apply_renames.py script is run against a reviewed, approved CSV.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 propose_renames.py "/path/to/Vessel Maintenance System Documents"

Output:
    rename_proposals.csv  — in the current working directory
"""

import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scan_folder import FILENAME_PATTERN

# ---------------------------------------------------------------------------
# System mapping — yard drawing letter prefix → Fathom System name
# ---------------------------------------------------------------------------
YARD_SYSTEM_MAP = {
    "A":  "GeneralArrangement",   # General arrangement, safety, nav plans
    "C":  "Stability",             # Hydrostatics, stability, tonnage, capacity
    "E":  "Electrical",            # Electrical one-lines, lighting, load analysis
    "M":  "Drivetrain",            # Machinery (shaft, Z-drive) — already in Drivetrain
    "P":  "Piping",                # All piping/system schematics
    "S":  "Hull",                  # Structural — matches Hull_MBB_S43... pattern
    "SK": "Hull",                  # Hull sketches (e.g. SK-02P Aft Staple)
    "T":  "Tonnage",               # Tonnage calculations
}

# DocType — determined by the description content, not just the letter prefix
# Applied in order; first match wins.
DESCRIPTION_DOCTYPE_RULES = [
    (re.compile(r'parts\s*(book|list|manual)', re.I), "PARTSLIST"),
    (re.compile(r'(datasheet|technical\s+data)', re.I), "REFDATA"),
    (re.compile(r'(calc|calculation|calcs)', re.I), "CALCS"),
    (re.compile(r'stability\s+booklet', re.I), "OMM"),
    (re.compile(r'(one.?line|single.?line|load\s+analysis|electric\s+plant)', re.I), "REFDATA"),
    (re.compile(r'schematic', re.I), "DWG"),
    (re.compile(r'(arrangement|plan|section|drawing|detail|install)', re.I), "DWG"),
]

# Vendor docs that don't match the 20-005 yard pattern — handled individually.
# Format: original_stem_pattern → (System, Manufacturer, Model, DocType, Rev)
VENDOR_MAP = [
    (re.compile(r'QUINCY.*QGS.*15.*Parts', re.I),
     ("CompressedAir", "Quincy", "QGS15", "PARTSLIST", "Rev1")),
    (re.compile(r'QUINCY.*QGS.*10.*20.*Datasheet', re.I),
     ("CompressedAir", "Quincy", "QGS1020S", "REFDATA", "Rev2")),
    (re.compile(r'Quincy.*ER.*Compressor', re.I),
     ("CompressedAir", "Quincy", "D310HP", "OMM", "Rev4")),
    (re.compile(r'Alfa\s*Laval.*304', re.I),
     ("FuelOil", "AlfaLaval", "MMB304S", "PARTSLIST", "Rev1")),
    (re.compile(r'Belimo|Ventilation.*Louver', re.I),
     ("HVAC", "Belimo", "EF230AS2", "REFDATA", "Rev1")),
]


def description_to_model(drawing_code: str, description: str) -> str:
    """Convert drawing code + description to a clean alphanumeric model
    field. Matches the existing pattern: S43CraneFoundation,
    S59TugBargeBoardingLadders."""
    desc = re.sub(r'\s+SHEET\s+\d+.*$', '', description, flags=re.IGNORECASE)
    desc = re.sub(r'\s+SHT\s+\d+.*$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\(shts[^)]*\)', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\([^)]*\)', '', desc)
    words = re.split(r'[^A-Za-z0-9]+', desc.strip())
    return drawing_code + ''.join(w.capitalize() for w in words if w)


def sub_to_rev(sub: str) -> str:
    """Convert yard sub-number to a Fathom Rev string.
    00 → Rev0, 01 → Rev1, 06 → Rev6, 0P → RevP (preliminary)."""
    sub = sub.upper()
    if sub == "0P" or sub.endswith("P"):
        return "RevP"
    # Strip leading zero for readability: 01 → Rev1, 06 → Rev6
    return "Rev" + str(int(sub)) if sub.isdigit() else f"Rev{sub}"


def get_doctype(description: str) -> str:
    for pattern, doctype in DESCRIPTION_DOCTYPE_RULES:
        if pattern.search(description):
            return doctype
    return "DWG"


def get_ingested_files(folder: Path) -> set[str]:
    """Query the manifest for already-ingested filenames. Falls back to
    checking the database if the manifest isn't available locally."""
    manifest_path = folder / ".." / "manifest.json"
    # Try manifest first (faster, no DB needed)
    candidates = [
        Path.home() / "vessel-maintenance-platform" / "manifest.json",
        folder.parent / "manifest.json",
        Path("manifest.json"),
    ]
    import json
    for p in candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return set(data.keys())
            except Exception:
                pass

    # Fall back to DB
    try:
        from retrieval import get_pg_connection
        import psycopg2.extras
        conn = get_pg_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT source_file FROM tm_chunks")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"  Warning: could not load ingested file list ({e}) — "
              f"duplicate detection disabled.")
        return set()


# Yard drawing pattern: "20-005 A01-01 Description text"
# Also handles SK codes: "20-005 SK-02P Aft Staple..."
YARD_DRAWING_PATTERN = re.compile(
    r'^20-005\s+'
    r'([A-Za-z]+)'            # group 1: letter code(s) — A, S, P, SK, etc.
    r'(\d+)'                  # group 2: numeric part of code
    r'-'
    r'([0-9]+[PpRr]?|[0-9]*[Pp])'  # group 3: sub-number/revision
    r'\s+'
    r'(.+?)(?:\s*\([^)]*\))*(?:_\d+)?\.pdf$',  # group 4: description
    re.IGNORECASE,
)

# Fallback for malformed yard drawings without hyphen: "20-005 E12 Desc R1"
YARD_NO_HYPHEN_PATTERN = re.compile(
    r'^20-005\s+([A-Za-z]+)(\d+)\s+(.+?)\s+R(\d+)\.pdf$',
    re.IGNORECASE,
)


def propose_name(filename: str) -> dict:
    """Return a dict with keys: original, proposed, system, notes.
    proposed is None if we can't reliably map it."""
    stem = filename[:-4] if filename.lower().endswith('.pdf') else filename
    result = {"original": filename, "proposed": None, "system": "", "notes": ""}

    # Already matches convention — no change needed
    if FILENAME_PATTERN.match(filename):
        result["notes"] = "ALREADY_VALID — no rename needed"
        result["proposed"] = filename
        return result

    # Vendor docs
    for pattern, (sys, mfr, model, doctype, rev) in VENDOR_MAP:
        if pattern.search(stem):
            result["proposed"] = f"{sys}_{mfr}_{model}_{doctype}_{rev}.pdf"
            result["system"] = sys
            result["notes"] = "vendor doc"
            return result

    # Yard drawing with hyphen
    m = YARD_DRAWING_PATTERN.match(filename)
    if m:
        letter, num, sub, desc = m.groups()
        letter = letter.upper()
        drawing_code = letter + num  # e.g. "S43", "A01", "SK02"

        system = YARD_SYSTEM_MAP.get(letter, YARD_SYSTEM_MAP.get(letter[:1], ""))
        if not system:
            result["notes"] = f"UNKNOWN_SYSTEM — letter prefix '{letter}' not in YARD_SYSTEM_MAP"
            return result

        model = description_to_model(drawing_code, desc.strip())
        doctype = get_doctype(desc)
        rev = sub_to_rev(sub)

        result["proposed"] = f"{system}_MBB_{model}_{doctype}_{rev}.pdf"
        result["system"] = system
        return result

    # Yard drawing without hyphen (e.g. "20-005 E12 Electrical Equipment Arrgt R1")
    m2 = YARD_NO_HYPHEN_PATTERN.match(filename)
    if m2:
        letter, num, desc, rev_num = m2.groups()
        letter = letter.upper()
        drawing_code = letter + num
        system = YARD_SYSTEM_MAP.get(letter, YARD_SYSTEM_MAP.get(letter[:1], ""))
        if not system:
            result["notes"] = f"UNKNOWN_SYSTEM — letter prefix '{letter}' not mapped"
            return result
        model = description_to_model(drawing_code, desc.strip())
        doctype = get_doctype(desc)
        result["proposed"] = f"{system}_MBB_{model}_DWG_Rev{rev_num}.pdf"
        result["system"] = system
        return result

    # Two-letter prefix or hyphen-separated code: "20-005 SK-02P ..." and "20-005 E-12 ..."
    m3 = re.match(
        r'''(?x)^20-005\s+([A-Za-z]{1,2})-([0-9]+)([PpRr]?)\s+
        (.+?)(?:\s+R([0-9]+))?(?:\s*\([^)]*\))*(?:_[0-9]+)?\.pdf$''',
        filename, re.IGNORECASE,
    )
    if m3:
        letter, num, suffix, desc, explicit_rev = m3.groups()
        letter = letter.upper()
        drawing_code = letter + num
        system = YARD_SYSTEM_MAP.get(letter, YARD_SYSTEM_MAP.get(letter[:1], ""))
        if not system:
            result["notes"] = f"UNKNOWN_SYSTEM — letter prefix '{letter}' not mapped"
            return result
        model = description_to_model(drawing_code, desc.strip())
        doctype = get_doctype(desc)
        if explicit_rev:
            rev = f"Rev{explicit_rev}"
        else:
            sub = num + suffix.upper() if suffix else num
            rev = sub_to_rev(sub)
        result["proposed"] = f"{system}_MBB_{model}_{doctype}_{rev}.pdf"
        result["system"] = system
        return result

    # Free-text yard doc with no drawing code (e.g. "20-005 Compressed Air Calculations")
    m4 = re.match(
        r'''(?x)^20-005\s+([A-Za-z].+?)(?:\s+[0-9]{1,2}[_/][0-9]{2}[_/][0-9]{2,4})?\.pdf$''',
        filename, re.IGNORECASE,
    )
    if m4:
        desc = m4.group(1).strip()
        desc_lower = desc.lower()
        if "compress" in desc_lower or "air" in desc_lower:
            system = "CompressedAir"
        elif "electrical" in desc_lower or "electric" in desc_lower:
            system = "Electrical"
        elif "stability" in desc_lower or "tonnage" in desc_lower:
            system = "Stability"
        else:
            system = "GeneralArrangement"
        words = re.split(r'[^A-Za-z0-9]+', desc.strip())
        model = ''.join(w.capitalize() for w in words if w)
        doctype = get_doctype(desc)
        result["proposed"] = f"{system}_MBB_{model}_{doctype}_Rev0.pdf"
        result["system"] = system
        result["notes"] = "inferred from description — verify Rev"
        return result

    result["notes"] = "COULD_NOT_PARSE — trying vision extraction..."
    return result


def propose_name_via_vision(filename: str, folder: Path) -> dict | None:
    """Fallback for filenames that can't be parsed from the name alone.
    Sends the first page of the PDF to Claude's vision API and asks it
    to extract the key naming fields directly from the document's title
    block or cover page (Sept 2026).

    Real motivating case: vendor docs with generic names like
    'CA126651 - NB 469 - ICCP Minitek Installation Manual.pdf' or
    'CAT COOLANT DEAC.pdf' — the filename alone doesn't contain enough
    structured information to propose a reliable name, but the document's
    own title block almost always does.

    Returns a propose_name()-compatible dict on success, or None if
    vision extraction fails or the API key isn't available. The caller
    falls back to MANUAL RENAME NEEDED if None is returned."""
    import base64
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    pdf_path = next(folder.rglob(filename), None)
    if not pdf_path or not pdf_path.exists():
        return None

    try:
        import pdfplumber
        from io import BytesIO
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None
            page = pdf.pages[0]
            im = page.to_image(resolution=150)
            buf = BytesIO()
            im.save(buf, format="PNG")
            image_b64 = base64.standard_b64encode(buf.getvalue()).decode()
    except Exception:
        return None

    prompt = """You are helping rename a marine vessel technical document to follow
a strict naming convention: System_Manufacturer_Model_DocType_Rev.pdf

Valid Systems: GeneralArrangement, Hull, Electrical, MainEngines, Drivetrain,
AzimuthThruster, FuelOil, Piping, Stability, Hydraulics, Steering,
CompressedAir, FireSuppression, HVAC, Bilge, Genset, MainSwitchboard,
PropulsionControl, Clutch, Tonnage, GeneralKnowledge

Valid DocTypes: OMM (operation & maintenance manual), DWG (drawing/schematic),
PARTSLIST, REFDATA (reference data/datasheet), CALCS, SDS (safety data sheet)

Look at this document's title block or cover page and extract:
1. System (pick the closest match from the valid list)
2. Manufacturer (short name, no spaces, e.g. CAT, BergPropulsion, Cathelco)
3. Model (alphanumeric, no spaces, e.g. 3512E, ICCPMinitek, MMB304S)
4. DocType (pick from the valid list)
5. Rev (e.g. Rev1, Rev0, RevUnknown if not shown)

Respond ONLY with a JSON object like:
{"system": "MainEngines", "manufacturer": "CAT", "model": "CoolantDEAC",
 "doctype": "SDS", "rev": "Rev1", "confidence": "high"}

Set confidence to "high" if clearly readable, "low" if guessing.
If you cannot determine the fields reliably, respond with {"confidence": "none"}"""

    try:
        # Explicit timeout (Sept 2026, real bug found live) — see
        # vision_extraction.py's client construction for the full story.
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": image_b64}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        import json
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        text = re.sub(r'^```[a-z]*\n?', '', text).rstrip('`').strip()
        data = json.loads(text)
    except Exception:
        return None

    if data.get("confidence") == "none":
        return None

    sys = data.get("system", "")
    mfr = data.get("manufacturer", "")
    model = data.get("model", "")
    doctype = data.get("doctype", "DWG")
    rev = data.get("rev", "Rev1")
    confidence = data.get("confidence", "low")

    if not all([sys, mfr, model]):
        return None

    proposed = f"{sys}_{mfr}_{model}_{doctype}_{rev}.pdf"
    return {
        "original": filename,
        "proposed": proposed,
        "system": sys,
        "notes": f"vision-extracted (confidence: {confidence}) — please verify",
    }


def run(folder_path: str):
    folder = Path(folder_path)
    if not folder.exists():
        sys.exit(f"Folder not found: {folder_path}")

    print(f"Scanning {folder_path}...")
    pdfs = sorted(p.name for p in folder.rglob("*.pdf"))
    unmatched = [f for f in pdfs if not FILENAME_PATTERN.match(f)]
    print(f"  {len(pdfs)} total PDFs, {len(unmatched)} don't match naming convention")

    print("  Loading already-ingested file list for duplicate detection...")
    ingested = get_ingested_files(folder)
    print(f"  {len(ingested)} files currently in the system")

    rows = []
    needs_manual = []
    duplicates = []

    for filename in unmatched:
        result = propose_name(filename)
        proposed = result["proposed"]
        notes = result["notes"]

        # Vision fallback — when filename parsing fails, try reading the
        # document's own title block via Claude's vision API (Sept 2026).
        if "COULD_NOT_PARSE" in notes and os.environ.get("ANTHROPIC_API_KEY"):
            print(f"  Trying vision extraction for: {filename}")
            vision_result = propose_name_via_vision(filename, folder)
            if vision_result:
                proposed = vision_result["proposed"]
                result["proposed"] = proposed
                result["system"] = vision_result["system"]
                notes = vision_result["notes"]
            else:
                notes = "COULD_NOT_PARSE — manual rename needed"

        # Duplicate detection: does the proposed name match something
        # already ingested, or does the drawing code appear in an already-
        # ingested file's name?
        if proposed and proposed != filename:
            if proposed in ingested:
                notes = f"DUPLICATE — '{proposed}' already ingested"
                duplicates.append(filename)
            else:
                # Check by drawing code (e.g. "S43" appears in
                # "Hull_MBB_S43CraneFoundation_DWG_Rev0.pdf")
                m = YARD_DRAWING_PATTERN.match(filename)
                if m:
                    code = m.group(1).upper() + m.group(2)
                    matching = [f for f in ingested if code in f]
                    if matching:
                        notes = f"POSSIBLE_DUPLICATE — drawing code {code} found in: {matching[0]}"
                        duplicates.append(filename)

        if "COULD_NOT_PARSE" in notes or "UNKNOWN_SYSTEM" in notes:
            needs_manual.append(filename)

        rows.append({
            "original_filename": filename,
            "proposed_filename": proposed or "— MANUAL RENAME NEEDED —",
            "system": result["system"],
            "action": "SKIP (already valid)" if notes == "ALREADY_VALID — no rename needed"
                      else "REVIEW" if ("DUPLICATE" in notes or "MANUAL" in notes)
                      else "RENAME",
            "notes": notes,
        })

    output_path = Path("rename_proposals.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "original_filename", "proposed_filename", "system", "action", "notes"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"=== Summary ===")
    actions = {}
    for r in rows:
        actions[r["action"]] = actions.get(r["action"], 0) + 1
    for action, count in sorted(actions.items()):
        print(f"  {action}: {count}")
    print()
    if duplicates:
        print(f"  {len(duplicates)} possible duplicate(s) — marked REVIEW in CSV")
    if needs_manual:
        print(f"  {len(needs_manual)} file(s) need manual renaming — marked REVIEW in CSV")
    print()
    print(f"CSV written to: {output_path.absolute()}")
    print()
    print("Next step: review rename_proposals.csv, edit any 'proposed_filename'")
    print("values you want to change, then run:")
    print("  python3.14 apply_renames.py rename_proposals.csv '/path/to/folder'")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(
            "Usage: python3.14 propose_renames.py '/path/to/Vessel Maintenance System Documents'"
        )
    run(sys.argv[1])
