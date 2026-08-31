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

from retrieval import query_chunks, extract_code_like_terms, keyword_search_chunks


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
rather than guessing or filling gaps with general knowledge — but be precise \
about WHAT you're saying: say that the retrieved excerpts don't cover this, \
never that a document "hasn't been provided," "isn't in the system," or \
"isn't available." You only ever see what was retrieved for this specific \
question — never the full contents of the library — so you have no way to \
know whether something exists elsewhere in it. If someone references a \
specific document, page, or code by name and it's not in your excerpts, say \
plainly that it wasn't in what was retrieved for this question, and suggest \
they try rephrasing — never tell them to upload or provide something that \
may already exist in the system; that's a real, misleading claim you're not \
in a position to make.
- Every claim in your answer must be traceable to one of the excerpts.
- Be concise and procedural — the reader is a working engineer, not someone \
who wants prose. Use numbered steps when the excerpt describes a procedure.
- Do not include a "Sources" list in your answer — the application displays \
sources separately, generated directly from the actual retrieved excerpts \
rather than from your own summary of them.
- If a "Vessel equipment currently installed" list is provided, use it to \
determine which model/variant actually applies when a manual covers multiple \
options — the vessel only has one of them installed, so there's no need to \
ask the user which one unless the registry itself doesn't resolve it (e.g. \
the equipment isn't in the list at all, or the manual's variants don't map \
cleanly to what's listed).
- If a "Document Library" list is provided, it tells you what documents \
EXIST in the system — use it ONLY to answer questions about existence \
("is there a schematic for X," "are there more drawings for Y," "what \
documents do you have about Z"). Never use it to answer what a document's \
content says — you have no actual content from a document just because \
its title is in this list. If something relevant exists in the library but \
you don't have retrieved excerpts from it for this specific question, say \
plainly that it exists but wasn't retrieved for this question, and suggest \
asking about it more specifically — never describe or infer its content \
from the title alone.
- If an "Engineer Notes" section is provided, treat it as real crew \
experience, NOT manufacturer data. Its exact, verbatim text is ALWAYS \
shown to the reader separately, before your answer — you do not need to, \
and must NOT, reproduce, restate, quote, or summarize a note's content \
anywhere in your ANSWER section, even under a heading like "field note" \
or "important note." If a note is relevant, state the CONCRETE SUBSTANCE \
of why in one or two sentences — e.g. what the note says that conflicts \
with the manual, in your own brief words — not just that a note exists \
("be aware of a practical limitation" is too vague; "the note indicates \
this can only be done fully disassembled, which conflicts with the \
manual's routine in-service procedure" is the right level of detail). \
If a note conflicts with the manual, also state a concrete next step for \
resolving it — confirm with whoever wrote the note (their name/role is \
shown in the note itself, right above your answer) before proceeding — \
not just that a conflict exists. Never reproduce the note's exact \
wording verbatim, and never mention the term "NOTE_ID" or a note's \
numeric ID anywhere in your ANSWER section — that ID is an internal \
reference only used in the FIELD_NOTE_IDS section below; it means \
nothing to the reader and must never appear in visible text.

Response format — structure your ENTIRE response using exactly these \
three sections, in this exact order, with these exact headers, even when \
a section has nothing to report for this question:

###FIELD_NOTE_IDS###
If you used one or more notes from "Engineer Notes" above in forming \
your answer, list their NOTE_ID numbers here, comma-separated (e.g. \
"5, 12"). Otherwise write NONE. Nothing else on this line.

###SAFETY_INFO###
If the excerpts contain a WARNING, CAUTION, NOTICE, or similar \
safety-relevant statement that's relevant to this specific question, \
reproduce it here close to verbatim from the excerpt — don't paraphrase \
safety-critical wording. If nothing applies, write NONE.

###ANSWER###
Your actual answer, following all the rules above. Do not repeat the \
field note content or the safety information here in any form — they're \
shown separately — beyond a short reference if relevant (e.g. flagging \
a conflict with the manual, per the Engineer Notes rule above).

Clarifying questions — ask at most ONE per issue, never loop:
- If, after considering the vessel equipment list above, the excerpts still \
describe genuinely different things that the question could reasonably mean \
(e.g. a generic term like "the pump" matches excerpts for two unrelated \
pumps), and you have NOT already asked about this in the "Previous exchange" \
below, ask ONE clarifying question — naming the specific real options found \
in the excerpts, not a generic "could you clarify?"
- If a "Previous exchange" section below shows you already asked a \
clarifying question last turn, do NOT ask again under any circumstances — \
this holds even if the newly retrieved excerpts for this follow-up are \
noisy, unhelpful, or don't obviously address the reply (retrieval isn't \
perfect, especially for a short reply). In that case, fall back to what \
you already know from the previous exchange itself plus whatever's \
genuinely useful in the new excerpts, clearly state what you're assuming \
or what's still missing, and answer with that — never respond as if the \
conversation is starting over.
"""


def parse_structured_response(raw_text: str) -> dict:
    """Splits Claude's structured response (see SYSTEM_PROMPT's Response
    Format section) into its three parts. Falls back gracefully to
    treating the WHOLE response as the answer — no field notes, no
    safety info — if the expected markers aren't found or don't parse
    cleanly. This must never be the reason an answer fails to display,
    even on the rare response where Claude doesn't follow the format
    exactly."""
    result = {"field_note_ids": [], "safety_info": "", "answer": raw_text}
    markers = ("###FIELD_NOTE_IDS###", "###SAFETY_INFO###", "###ANSWER###")
    if not all(m in raw_text for m in markers):
        return result
    try:
        _, rest = raw_text.split(markers[0], 1)
        ids_part, rest = rest.split(markers[1], 1)
        safety_part, answer_part = rest.split(markers[2], 1)

        ids_part = ids_part.strip()
        if ids_part and ids_part.upper() != "NONE":
            result["field_note_ids"] = [
                int(x.strip()) for x in ids_part.split(",") if x.strip().isdigit()
            ]

        safety_part = safety_part.strip()
        if safety_part and safety_part.upper() != "NONE":
            result["safety_info"] = safety_part

        result["answer"] = answer_part.strip()
    except Exception:
        return {"field_note_ids": [], "safety_info": "", "answer": raw_text}
    return result


def add_exact_code_matches(question: str, chunks: list[dict]) -> list[dict]:
    """If the question contains something that looks like a specific
    code/identifier (see retrieval.extract_code_like_terms), searches
    for it literally and merges any real matches into the semantic
    results — ensuring an exact match is never missing just because it
    ranked poorly semantically. Real motivating case (Aug 2026, see
    BACKLOG.md's DEF alarm entry): even a correctly, tightly split chunk
    for a specific fault code didn't reliably rank in a usable top-15 —
    confirming this needed a different search method, not just smaller
    chunks. De-dupes against chunks already present from semantic
    search, using the same fingerprint keyword_search_chunks() uses
    internally, so a term that already ranked well doesn't get added
    twice."""
    terms = extract_code_like_terms(question)
    if not terms:
        return chunks
    keyword_matches = keyword_search_chunks(terms)
    if not keyword_matches:
        return chunks

    existing_fingerprints = {
        (c["metadata"]["document_title"], c["metadata"]["page_number"], c["text"][:80])
        for c in chunks
    }
    new_matches = [
        m for m in keyword_matches
        if (m["metadata"]["document_title"], m["metadata"]["page_number"], m["text"][:80])
        not in existing_fingerprints
    ]
    return chunks + new_matches


def build_prompt(question: str, chunks: list[dict], equipment_context: str = "",
                  previous_exchange: dict | None = None, notes_context: str = "",
                  inventory_context: str = "") -> str:
    excerpt_blocks = []
    for i, c in enumerate(chunks):
        citation = f'{c["metadata"]["document_title"]}, {c["metadata"]["revision"]}, p. {c["metadata"]["page_number"]}'
        excerpt_blocks.append(f"--- Excerpt {i+1} ({citation}) ---\n{c['text']}")
    excerpts = "\n\n".join(excerpt_blocks)
    equipment_block = f"\n{equipment_context}\n" if equipment_context else ""
    notes_block = f"\n{notes_context}\n" if notes_context else ""
    inventory_block = f"\n{inventory_context}\n" if inventory_context else ""

    history_block = ""
    if previous_exchange:
        history_block = f"""
Previous exchange in this conversation (check: did you already ask a \
clarifying question here? If so, do not ask another — see system prompt rules):
User asked: "{previous_exchange['question']}"
You answered: "{previous_exchange['answer']}"
"""

    return f"""Question: {question}
{equipment_block}{notes_block}{inventory_block}{history_block}
Manual excerpts retrieved for this question:

{excerpts}

Answer the question using only the excerpts above."""


def get_answer(question: str, engine: str = "voyage", top_k: int = 5,
               api_key: str | None = None, previous_exchange: dict | None = None) -> dict:
    """The importable core of this module — used by both the CLI below and
    the Streamlit front end. Returns a dict rather than printing, and
    raises a normal exception rather than sys.exit()-ing, since this now
    needs to run safely inside a long-lived app process, not just as a
    one-shot script.

    top_k default raised from 3 to 5 (Aug 2026) — a real missed-retrieval
    case (see BACKLOG.md) suggested the right chunk can rank just outside
    the top 3 for an imperfectly-phrased question; a slightly wider net
    costs a little more context but meaningfully reduces that risk.

    Vessel equipment context (Aug 2026, see extract_equipment_list.py) is
    fetched fresh on every call and always included when available — not
    dependent on retrieval happening to find the equipment list document,
    since a question rarely names the model explicitly (the asker assumes
    the system already knows what's installed, same as a real engineer
    would). Degrades silently to no equipment context if the registry is
    empty or unreachable — this must never be the reason a question fails.

    previous_exchange (Aug 2026, see BACKLOG.md's clarifying-question
    entry): optional {"question": ..., "answer": ...} dict for the
    immediately-prior turn only — not the whole conversation history,
    deliberately, to keep this scoped to its one job (letting Claude tell
    whether it already asked a clarifying question) rather than turning
    into a general multi-turn memory feature. The caller (app.py) decides
    whether to pass this; the CLI below does not by default, so plain CLI
    testing remains single-shot/stateless unless a caller passes one in.

    Returns:
        {
            "answer": str,        # Claude's synthesized response text —
                                   # just the ANSWER section, with field
                                   # notes / safety info split out
            "chunks": list[dict], # raw retrieved chunks (metadata + excerpt
                                   # text) used to build the prompt — the
                                   # front end shows these inline next to
                                   # citations, not just a page number, per
                                   # docs/architecture.md
            "prompt": str,        # the actual prompt sent (useful for a
                                   # debug/dry-run view later)
            "safety_info": str,   # extracted WARNING/CAUTION text relevant
                                   # to this answer, or "" if none (Aug 2026)
            "field_notes_used": list[dict],  # full, verbatim Engineer Notes
                                   # actually referenced, fetched directly
                                   # from the database — not Claude's own
                                   # paraphrase (Aug 2026)
        }

    Raises:
        ValueError if no Anthropic API key is available.
    """
    # Search query includes the previous question too, when there is one
    # (Aug 2026) — a reply to a clarifying question is often short ("the
    # azimuth one", "port side"), and a couple of words alone often isn't
    # enough signal for good retrieval. Combining with the original
    # question gives the embedding real context to work with, without
    # changing what's shown to Claude as "the question" in the prompt
    # itself (build_prompt still receives the bare current question).
    search_text = question
    if previous_exchange and previous_exchange.get("question"):
        search_text = f"{previous_exchange['question']} {question}"
    search_query = expand_units(search_text)
    chunks = query_chunks(search_query, engine=engine, top_k=top_k)
    chunks = add_exact_code_matches(question, chunks)

    equipment_context = ""
    try:
        from retrieval import get_pg_connection
        from extract_equipment_list import get_equipment_list, format_equipment_list
        eq_conn = get_pg_connection()
        equipment_context = format_equipment_list(get_equipment_list(eq_conn))
        eq_conn.close()
    except Exception:
        pass  # equipment context is an enhancement, never a reason a question fails

    notes_context = ""
    try:
        from retrieval import get_pg_connection
        from engineer_notes import get_all_notes, format_notes_for_prompt
        notes_conn = get_pg_connection()
        notes_context = format_notes_for_prompt(get_all_notes(notes_conn))
        notes_conn.close()
    except Exception:
        pass  # same reasoning as equipment_context — never a reason a question fails

    inventory_context = ""
    try:
        from retrieval import get_pg_connection
        from document_inventory import get_document_inventory, format_document_inventory
        inv_conn = get_pg_connection()
        inventory_context = format_document_inventory(get_document_inventory(inv_conn))
        inv_conn.close()
    except Exception:
        pass  # same reasoning as equipment_context — never a reason a question fails

    prompt = build_prompt(question, chunks, equipment_context, previous_exchange,
                           notes_context, inventory_context)

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
    raw_text = response.content[0].text
    parsed = parse_structured_response(raw_text)

    field_notes_used = []
    if parsed["field_note_ids"]:
        try:
            from retrieval import get_pg_connection
            from engineer_notes import get_notes_by_ids
            fn_conn = get_pg_connection()
            field_notes_used = get_notes_by_ids(fn_conn, parsed["field_note_ids"])
            fn_conn.close()
        except Exception:
            pass  # if the exact notes can't be fetched, just don't show them — never break the answer

    return {
        "answer": parsed["answer"],
        "chunks": chunks,
        "prompt": prompt,
        "safety_info": parsed["safety_info"],
        "field_notes_used": field_notes_used,
    }


def format_sources(chunks: list[dict]) -> str:
    """Clean, code-generated citation list — document + revision + page
    only, no raw excerpt text. Built directly from retrieval metadata
    (not from Claude's own summary of it), so it's independently accurate
    rather than dependent on the model reliably reformatting it every time.
    Used by both the CLI and the Streamlit front end so citations look and
    behave identically in both places.

    Page format: "p. X of Y" when total_pages is known (Aug 2026 — added
    after real confusion: "p. 672" reads like the number printed in the
    document's own margin, but it's actually the PDF file's physical page
    position, which can drift from the document's internal printed page
    numbers whenever there's a cover page, TOC, or front matter — happened
    for real on a 1415-page manual, a 36-page gap). Falls back to plain
    "p. X" when total_pages isn't in a chunk's metadata — documents
    ingested before this change don't have it yet, and re-ingesting the
    whole library just for this wasn't worth doing immediately; they'll
    pick up the fuller format automatically whenever they're next
    re-ingested (e.g. a rename or content update)."""
    lines = []
    seen = set()
    for c in chunks:
        m = c["metadata"]
        key = (m["document_title"], m["revision"], m["page_number"])
        if key in seen:
            continue
        seen.add(key)
        total = m.get("total_pages")
        page_label = f'p. {m["page_number"]} of {total}' if total else f'p. {m["page_number"]}'
        lines.append(f'- {m["document_title"]}, {m["revision"]}, {page_label}')
    return "Sources:\n" + "\n".join(lines) if lines else ""


def answer(question: str, engine: str = "voyage", dry_run: bool = False, top_k: int = 5,
           previous_exchange: dict | None = None):
    """CLI-facing wrapper — keeps the exact command-line behavior/UX
    unchanged (dry-run printing, sys.exit on a missing key) while
    delegating the real work to get_answer(). previous_exchange support
    added Aug 2026 specifically for debugging the clarifying-question
    feature from the CLI, reproducing exactly what app.py would send."""
    if dry_run:
        search_text = question
        if previous_exchange and previous_exchange.get("question"):
            search_text = f"{previous_exchange['question']} {question}"
        search_query = expand_units(search_text)
        chunks = query_chunks(search_query, engine=engine, top_k=top_k)
        chunks = add_exact_code_matches(question, chunks)
        equipment_context = ""
        try:
            from retrieval import get_pg_connection
            from extract_equipment_list import get_equipment_list, format_equipment_list
            eq_conn = get_pg_connection()
            equipment_context = format_equipment_list(get_equipment_list(eq_conn))
            eq_conn.close()
        except Exception:
            pass
        notes_context = ""
        try:
            from retrieval import get_pg_connection
            from engineer_notes import get_all_notes, format_notes_for_prompt
            notes_conn = get_pg_connection()
            notes_context = format_notes_for_prompt(get_all_notes(notes_conn))
            notes_conn.close()
        except Exception:
            pass
        inventory_context = ""
        try:
            from retrieval import get_pg_connection
            from document_inventory import get_document_inventory, format_document_inventory
            inv_conn = get_pg_connection()
            inventory_context = format_document_inventory(get_document_inventory(inv_conn))
            inv_conn.close()
        except Exception:
            pass
        prompt = build_prompt(question, chunks, equipment_context, previous_exchange,
                               notes_context, inventory_context)
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        if search_query != question:
            print(f"\n=== SEARCH QUERY (expanded/combined) ===\n{search_query}")
        print("\n=== RETRIEVED CHUNKS ===")
        for i, c in enumerate(chunks):
            m = c["metadata"]
            print(f"[{i+1}] {m['document_title']}, p. {m['page_number']} (distance={c['distance']:.3f})")
        print("\n=== USER PROMPT (what would be sent) ===")
        print(prompt)
        return

    try:
        result = get_answer(question, engine=engine, top_k=top_k, previous_exchange=previous_exchange)
    except ValueError as e:
        sys.exit(str(e))

    print(result["answer"])
    if result.get("safety_info"):
        print(f"\n⚠️ Safety Information:\n{result['safety_info']}")
    if result.get("field_notes_used"):
        print("\nField Notes used:")
        for n in result["field_notes_used"]:
            author = n["author"] + (f' ({n["author_role"]})' if n.get("author_role") else "")
            print(f'- [{n["category"]}{" " + n["position"] if n.get("position") else ""}] '
                  f'{author}, {n.get("created_at", "")}: {n["note_text"]}')
    sources = format_sources(result["chunks"])
    if sources:
        print(f"\n{sources}")

    # Page images (Aug 2026) — printed separately from format_sources()
    # since a URL isn't part of a citation line itself, just useful CLI
    # debug output. See page_images.py.
    seen_images = set()
    image_lines = []
    for c in result["chunks"]:
        url = c["metadata"].get("page_image_url")
        if url and url not in seen_images:
            seen_images.add(url)
            image_lines.append(f'- {c["metadata"]["document_title"]}, '
                                f'p. {c["metadata"]["page_number"]}: {url}')
    if image_lines:
        print("\nPage images:")
        print("\n".join(image_lines))


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
    previous_exchange = None
    if "--previous-question" in args:
        idx = args.index("--previous-question")
        prev_q = args[idx + 1]
        del args[idx:idx + 2]
        prev_a = ""
        if "--previous-answer" in args:
            idx = args.index("--previous-answer")
            prev_a = args[idx + 1]
            del args[idx:idx + 2]
        previous_exchange = {"question": prev_q, "answer": prev_a}
    if not args:
        sys.exit('Usage: python answer_query.py [--engine voyage|tfidf] [--dry-run] '
                  '[--previous-question "..." --previous-answer "..."] "your question"')
    answer(args[0], engine=engine, dry_run=dry_run, previous_exchange=previous_exchange)
