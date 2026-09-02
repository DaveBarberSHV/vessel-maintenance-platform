"""
One-off diagnostic (Sept 2026, real bug investigation) — captures the raw
extraction response text and prints exactly what's around a real,
reported failure position, rather than guessing at what's malformed.

Usage:
    export ANTHROPIC_API_KEY="..."
    python3.14 diagnose_equipment_json.py "/path/to/EquipmentList.pdf"
"""

import sys
from pathlib import Path

sys.path.insert(0, ".")
import extract_equipment_list as eq


def diagnose(pdf_path: Path):
    import os
    import anthropic
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=eq.EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    print(f"stop_reason: {response.stop_reason}")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    print(f"Total response length: {len(raw)} characters")
    print()

    import json
    try:
        json.loads(raw)
        print("Parsed successfully -- no error this time.")
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError: {e}")
        print()
        start = max(0, e.pos - 300)
        end = min(len(raw), e.pos + 100)
        print(f"--- Raw content from character {start} to {end} (error at {e.pos}) ---")
        print(raw[start:end])
        print("--- end excerpt ---")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python3.14 diagnose_equipment_json.py \"/path/to/file.pdf\"")
    diagnose(Path(sys.argv[1]))
