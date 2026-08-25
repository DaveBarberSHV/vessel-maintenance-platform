"""
Query-time answer flow: engineer's question -> retrieval finds relevant TM
chunks -> Claude synthesizes a concise, cited answer from those chunks only.

This is the piece that turns "here are 3 pages that might be relevant" into
an actual answer an engineer can act on.

Requires an Anthropic API key, set as an environment variable — never typed
into code, never pasted into a chat with Claude:

    export ANTHROPIC_API_KEY="your-key-here"

Get a key at: https://console.anthropic.com

Usage:
    python answer_query.py "How do I replace the oil filter on the clutch?"
    python answer_query.py --engine tfidf "..."   # use TF-IDF instead of the Voyage default
    python answer_query.py --dry-run "..."        # builds the prompt, doesn't call the API
"""

import os
import re
import sys

from retrieval import query_chunks


# Real case that motivated this (Aug 2026, Jared's first live test): "We
# have a bearing running at 220 degrees F. What is going to happen?" missed
# the correct fault-table chunk entirely — it exists, but is written only
# in Celsius (">70°C", ">90°C"). A near-identical question phrased in
# Celsius found it correctly. See BACKLOG.md. The underlying problem isn't
# specific to temperature — crew uses English/Imperial units (°F, psi),
# manuals often use metric (°C, bar, MPa) — so this is a small, generic
# table rather than a one-off temperature function, to make adding the
# next unit pair (e.g. torque, length) a one-line addition, not a rewrite.
#
# Each entry: a name (for clarity only), a regex matching "<number> <unit>"
# (deliberately requiring an explicit unit marker, not a bare number, to
# avoid misfiring on unrelated numbers like part numbers), and one or more
# (label, conversion function) pairs — some units get converted to more
# than one target, since manufacturers aren't consistent about which
# metric unit they use (e.g. GEWES's own manual states relief-valve
# pressure in both bar AND MPa together: "0.5 to 1.0 MPa (5 to 10 bar)").
UNIT_CONVERSIONS = [
    {
        "name": "fahrenheit",
        "source_label": "°F",
        "pattern": re.compile(
            r"(-?\d+(?:\.\d+)?)\s*(?:°\s*F\b|degrees?\s*F\b|deg\.?\s*F\b)",
            re.IGNORECASE,
        ),
        "targets": [
            ("°C", lambda f: (f - 32) * 5 / 9),
        ],
    },
    {
        "name": "psi",
        "source_label": "psi",
        "pattern": re.compile(r"(-?\d+(?:\.\d+)?)\s*psi\b", re.IGNORECASE),
        "targets": [
            ("bar", lambda psi: psi * 0.0689476),
            ("MPa", lambda psi: psi * 0.00689476),
        ],
    },
]


def expand_units(text: str) -> str:
    """If the text mentions a value in a unit crew commonly use that
    differs from what the manuals use, append the metric equivalent(s) —
    used only to build a better search query, never shown to the user or
    to Claude as a replacement for what they actually asked. A search in
    the "wrong" unit system can have little lexical or semantic overlap
    with metric-only manual content, even though the conversion itself is
    trivial."""
    additions = []
    for spec in UNIT_CONVERSIONS:
        for match_str in spec["pattern"].findall(text):
            value = float(match_str)
            converted = ", ".join(
                f"{target(value):.1f}{unit}" for unit, target in spec["targets"]
            )
            additions.append(f"{match_str}{spec['source_label']} ({converted})")
    if not additions:
        return text
    return text + " [" + ", ".join(additions) + "]"


# Kept as a thin alias — expand_temperature_units was the original,
# narrower name before this was generalized to expand_units() above.
expand_temperature_units = expand_units


SYSTEM_PROMPT = """You are a technical assistant for a ship's engineering department. \
You answer equipment questions using ONLY the manual excerpts provided below — \
never your own general knowledge of similar equipment, since exact procedures, \
part numbers, and specs vary by manufacturer and model.

Rules:
- If the excerpts don't contain enough information to answer, say so plainly \
rather than guessing or filling gaps with general knowledge.
- Every claim in your answer must be traceable to one of the excerpts.
- Be concise and procedural — the reader is a working engineer, not someone \
who wants prose. Use numbered steps when the excerpt describes a procedure.
- Do not include a "Sources" list in your answer — the application displays \
sources separately, generated directly from the actual retrieved excerpts \
rather than from your own summary of them.
"""


def build_prompt(question: str, chunks: list[dict]) -> str:
    excerpt_blocks = []
    for i, c in enumerate(chunks):
        citation = f'{c["metadata"]["document_title"]}, {c["metadata"]["revision"]}, p. {c["metadata"]["page_number"]}'
        excerpt_blocks.append(f"--- Excerpt {i+1} ({citation}) ---\n{c['text']}")
    excerpts = "\n\n".join(excerpt_blocks)
    return f"""Question: {question}

Manual excerpts retrieved for this question:

{excerpts}

Answer the question using only the excerpts above."""


def get_answer(question: str, engine: str = "voyage", top_k: int = 5,
               api_key: str | None = None) -> dict:
    """The importable core of this module — used by both the CLI below and
    the Streamlit front end. Returns a dict rather than printing, and
    raises a normal exception rather than sys.exit()-ing, since this now
    needs to run safely inside a long-lived app process, not just as a
    one-shot script.

    top_k default raised from 3 to 5 (Aug 2026) — a real missed-retrieval
    case (see BACKLOG.md) suggested the right chunk can rank just outside
    the top 3 for an imperfectly-phrased question; a slightly wider net
    costs a little more context but meaningfully reduces that risk.

    Returns:
        {
            "answer": str,        # Claude's synthesized response text
            "chunks": list[dict], # raw retrieved chunks (metadata + excerpt
                                   # text) used to build the prompt — the
                                   # front end shows these inline next to
                                   # citations, not just a page number, per
                                   # docs/architecture.md
            "prompt": str,        # the actual prompt sent (useful for a
                                   # debug/dry-run view later)
        }

    Raises:
        ValueError if no Anthropic API key is available.
    """
    search_query = expand_units(question)
    chunks = query_chunks(search_query, engine=engine, top_k=top_k)
    prompt = build_prompt(question, chunks)

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "No ANTHROPIC_API_KEY available. Set the ANTHROPIC_API_KEY "
            "environment variable, or pass api_key= explicitly. "
            "Get a key at https://console.anthropic.com"
        )

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return {
        "answer": response.content[0].text,
        "chunks": chunks,
        "prompt": prompt,
    }


def format_sources(chunks: list[dict]) -> str:
    """Clean, code-generated citation list — document + revision + page
    only, no raw excerpt text. Built directly from retrieval metadata
    (not from Claude's own summary of it), so it's independently accurate
    rather than dependent on the model reliably reformatting it every time.
    Used by both the CLI and the Streamlit front end so citations look and
    behave identically in both places."""
    lines = []
    seen = set()
    for c in chunks:
        m = c["metadata"]
        key = (m["document_title"], m["revision"], m["page_number"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'- {m["document_title"]}, {m["revision"]}, p. {m["page_number"]}')
    return "Sources:\n" + "\n".join(lines) if lines else ""


def answer(question: str, engine: str = "voyage", dry_run: bool = False, top_k: int = 5):
    """CLI-facing wrapper — keeps the exact command-line behavior/UX
    unchanged (dry-run printing, sys.exit on a missing key) while
    delegating the real work to get_answer()."""
    if dry_run:
        search_query = expand_units(question)
        chunks = query_chunks(search_query, engine=engine, top_k=top_k)
        prompt = build_prompt(question, chunks)
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        if search_query != question:
            print(f"\n=== SEARCH QUERY (expanded from question) ===\n{search_query}")
        print("\n=== USER PROMPT (what would be sent) ===")
        print(prompt)
        return

    try:
        result = get_answer(question, engine=engine, top_k=top_k)
    except ValueError as e:
        sys.exit(str(e))

    print(result["answer"])
    sources = format_sources(result["chunks"])
    if sources:
        print(f"\n{sources}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")
    engine = "voyage"
    if "--engine" in args:
        idx = args.index("--engine")
        engine = args[idx + 1]
        del args[idx:idx + 2]
    if not args:
        sys.exit('Usage: python answer_query.py [--engine voyage|tfidf] [--dry-run] "your question"')
    answer(args[0], engine=engine, dry_run=dry_run)
