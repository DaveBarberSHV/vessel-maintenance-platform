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
from collections import defaultdict

from retrieval import (query_chunks, extract_code_like_terms, keyword_search_chunks,
                        fetch_chunks_by_title, search_dwg_titles_by_keywords)


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
    {
        "name": "torque_lbft",
        "source_label": "lb-ft",
        "pattern": re.compile(
            r"(-?\d+(?:\.\d+)?)\s*(?:lb-?ft\b|ft-?lbs?\b|lbf-?ft\b|foot-?pounds?\b)",
            re.IGNORECASE,
        ),
        "targets": [
            ("N·m", lambda lbft: lbft * 1.35582),
        ],
    },
    {
        "name": "inches",
        "source_label": "in",
        "pattern": re.compile(r'(-?\d+(?:\.\d+)?)\s*(?:inches\b|inch\b|")', re.IGNORECASE),
        "targets": [
            ("mm", lambda inch: inch * 25.4),
        ],
    },
    {
        "name": "feet",
        "source_label": "ft",
        # Negative lookahead avoids double-matching "ft" inside torque
        # notation like "50 ft-lbs" — that's the torque_lbft entry
        # above's job, not this one's.
        "pattern": re.compile(
            r"(-?\d+(?:\.\d+)?)\s*(?:feet\b|foot\b|ft\b(?!-?lbs?\b))",
            re.IGNORECASE,
        ),
        "targets": [
            ("m", lambda ft: ft * 0.3048),
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
- When stating a value that has a unit (temperature, pressure, torque, \
length, etc.), always lead with the units the crew actually operates in — \
°F for temperature, psi for pressure, lb-ft for torque, inches/feet for \
length — even when the source excerpt states it natively in metric (common \
in German/European manuals). Include the metric value in parentheses as a \
secondary reference if useful, but never lead with metric or state metric \
alone. This applies regardless of which unit the excerpt itself happens to \
use — don't just mirror the source's units. If you're not confident in a \
conversion, state the value exactly as given in the excerpt rather than \
guess at converting it.
- Be concise and procedural — the reader is a working engineer, not someone \
who wants prose. Use numbered steps when the excerpt describes a procedure.
- Do not include a page-level "Sources" list in your answer — the \
application displays that separately, generated directly from the actual \
retrieved excerpts rather than from your own summary of them. (This is \
distinct from the "📁 Full manuals" block described under ###ANSWER### \
below, which you DO add yourself — that's a different, document-level \
reference, not a page citation list.)
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
- If a "Recent conversation history" section is provided, use it to \
interpret a short follow-up question that only makes sense in light of \
what was just discussed — e.g. "what about the starboard engine?" \
(same topic, different equipment instance), "how often?" (same topic, \
asking for an interval this time), "does that apply to ours too?" \
(referring back to something just named). Resolve what's actually being \
asked and answer the full underlying question directly — never answer \
the literal fragment on its own as if it had no context, and never ask \
the user to repeat information already established earlier in the \
conversation. If the history doesn't actually clarify the current \
question (a genuinely new, unrelated topic), treat it as a fresh \
question instead of forcing a connection that isn't there.

Response format — structure your ENTIRE response using exactly these \
four sections, in this exact order, with these exact headers, even when \
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

###SHOW_DOCUMENT###
Only report something here if the question ITSELF explicitly asks to be \
SHOWN something — genuine showing-language like "show me," "can I see," \
"picture of," "what does X look like," "let me see the drawing." A \
question that merely CITES or is ANSWERED USING a drawing/image does \
NOT qualify — e.g. "what's the GPM rating on the pump" is answered using \
a schematic but never asked to see it, so this must be NONE even though \
a relevant excerpt exists. If genuine showing-language is present AND \
one or more of the excerpts above is the specific thing being asked to \
be shown, list those excerpt numbers here, comma-separated (e.g. "2" or \
"1, 3"). Otherwise, or if you're not confident the question is really \
asking to be shown something specifically, write NONE — a missed \
detection here just means the image stays in the normal Sources list \
instead of appearing prominently, which is a far smaller cost than \
showing a large image on a question that didn't really ask for one.

###EXCERPTS_USED###
Real fix (Sept 2026) for a real, reported problem: retrieval can bring \
back excerpts from the wrong piece of equipment (e.g. a generic fuel- \
filter question pulls in the main engine's manual alongside the correct \
generator manual, because the two describe similarly-worded procedures) \
— you correctly ignore the wrong ones when writing the answer, but the \
app's Sources list previously showed every retrieved excerpt regardless, \
looking alarming even when the answer itself was accurate. List the \
excerpt numbers you ACTUALLY drew on to construct the answer below, \
comma-separated (e.g. "4, 5") — only ones whose content you genuinely \
used, never every excerpt you were given, and never ones you considered \
but judged irrelevant (like a different piece of equipment's manual). If \
you didn't rely on any specific excerpt at all (e.g. the question was \
answered entirely from Engineer Notes or the vessel equipment list), \
write NONE. This is a real, code-driven filter (see get_answer() below) \
that decides what the reader sees in "View Sources" — under-reporting a \
real excerpt you used means a real citation goes missing, so only omit \
one if you're genuinely confident you didn't use it.

###ANSWER###
Your actual answer, following all the rules above. Do not repeat the \
field note content or the safety information here in any form — they're \
shown separately — beyond a short reference if relevant (e.g. flagging \
a conflict with the manual, per the Engineer Notes rule above).

After the answer itself, if you actually cited one or more specific \
documents to answer this question (not just excerpts you were given but \
didn't rely on), add a brief block at the very end pointing to each \
cited document's complete source, so the reader knows where to find the \
full manual, not just the cited page:

📁 Full manuals:
The complete manual is located [System]/[source_file] on the vessel's \
Engineering laptop computer with Fathom.

One line per unique document actually cited — never one line per \
excerpt, since several excerpts can come from the same document. \
[System] is the first segment of that document's title before " - " \
(e.g. "MainEngines" from "MainEngines - CAT 3512E O&M Manual"). \
[source_file] is that excerpt's own source_file value exactly as given \
in the excerpt header above — never invent, reformat, abbreviate, or \
guess a filename that wasn't provided; if an excerpt you cited has no \
source_file value, omit that one line rather than guessing. Omit this \
entire block if the question was answered from Engineer Notes or the \
vessel equipment list alone, with no specific document excerpt actually \
relied upon.

Clarifying questions — ask at most ONE per issue, never loop:
- If, after considering the vessel equipment list above, the excerpts still \
describe genuinely different things that the question could reasonably mean \
(e.g. a generic term like "the pump" matches excerpts for two unrelated \
pumps), and the MOST RECENT turn in "Recent conversation history" below (if \
any turns are present at all) does NOT show you already asked about this, \
ask ONE clarifying question — naming the specific real options found in the \
excerpts, not a generic "could you clarify?"
- If the MOST RECENT turn in "Recent conversation history" below shows you \
already asked a clarifying question last turn, do NOT ask again under any \
circumstances — this holds even if the newly retrieved excerpts for this \
follow-up are noisy, unhelpful, or don't obviously address the reply \
(retrieval isn't perfect, especially for a short reply). In that case, fall \
back to what you already know from recent conversation plus whatever's \
genuinely useful in the new excerpts, clearly state what you're assuming or \
what's still missing, and answer with that — never respond as if the \
conversation is starting over. Only the single most recent turn counts for \
this specific check — an earlier turn further back asking about a \
different, already-resolved issue does not block a new clarifying question \
now.
"""


def parse_structured_response(raw_text: str) -> dict:
    """Splits Claude's structured response (see SYSTEM_PROMPT's Response
    Format section) into its parts. Falls back gracefully to treating the
    WHOLE response as the answer — no field notes, no safety info, no
    show-document reference, no excerpts_used — if the expected markers
    aren't found or don't parse cleanly. This must never be the reason
    an answer fails to display, even on the rare response where Claude
    doesn't follow the format exactly.

    show_document_excerpts added Sept 2026 — see architecture.md's
    "Auto-surfacing a document" entry. Deliberately parsed the same
    reliable way as field_note_ids: Claude reports which excerpt
    NUMBER(S) it means, and get_answer() below resolves those numbers
    into real image URLs from the chunks' own metadata — the same
    established pattern used for citations generally (code resolves
    real data, Claude never has to know or report a URL itself).

    excerpts_used added Sept 2026 — real bug found live: retrieval can
    bring back excerpts from the wrong piece of equipment (similarly-
    worded procedures across two different manuals), and while Claude
    correctly ignored the wrong ones when writing the answer, the Sources
    list previously showed every retrieved excerpt regardless — looking
    alarming even when the answer itself was accurate. Deliberately
    distinguishes "Claude reported NONE" (a real, legitimate empty list —
    e.g. the answer came entirely from Engineer Notes) from "the markers
    were missing/malformed" (None — genuinely unknown) so get_answer()
    can tell "show zero sources, that's correct" apart from "something
    went wrong parsing this, don't hide real sources over it." Only ever
    narrows Sources down from what was actually retrieved — never adds
    anything Claude wasn't actually given."""
    result = {
        "field_note_ids": [], "safety_info": "", "show_document_excerpts": [],
        "excerpts_used": None, "answer": raw_text,
    }
    markers = ("###FIELD_NOTE_IDS###", "###SAFETY_INFO###", "###SHOW_DOCUMENT###",
               "###EXCERPTS_USED###", "###ANSWER###")
    if not all(m in raw_text for m in markers):
        return result
    try:
        _, rest = raw_text.split(markers[0], 1)
        ids_part, rest = rest.split(markers[1], 1)
        safety_part, rest = rest.split(markers[2], 1)
        show_part, rest = rest.split(markers[3], 1)
        excerpts_part, answer_part = rest.split(markers[4], 1)

        ids_part = ids_part.strip()
        if ids_part and ids_part.upper() != "NONE":
            result["field_note_ids"] = [
                int(x.strip()) for x in ids_part.split(",") if x.strip().isdigit()
            ]

        safety_part = safety_part.strip()
        if safety_part and safety_part.upper() != "NONE":
            result["safety_info"] = safety_part

        show_part = show_part.strip()
        if show_part and show_part.upper() != "NONE":
            result["show_document_excerpts"] = [
                int(x.strip()) for x in show_part.split(",") if x.strip().isdigit()
            ]

        excerpts_part = excerpts_part.strip()
        if excerpts_part.upper() == "NONE":
            result["excerpts_used"] = []
        else:
            result["excerpts_used"] = [
                int(x.strip()) for x in excerpts_part.split(",") if x.strip().isdigit()
            ]

        result["answer"] = answer_part.strip()
    except Exception:
        return {
            "field_note_ids": [], "safety_info": "", "show_document_excerpts": [],
            "excerpts_used": None, "answer": raw_text,
        }
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


def finalize_chunks(chunks: list[dict]) -> list[dict]:
    """Deduplicates and sorts the final merged chunk list before it goes
    into the prompt — real bug found live (Sept 2026, see BACKLOG.md):
    two separate issues were compounding into visibly wrong-looking
    Sources lists.

    1. Deduplication previously only happened in format_sources(), for
       DISPLAY — Claude's actual prompt saw every raw chunk, duplicates
       included. A real, separate ingestion bug (see BACKLOG.md) wrote
       the same page's text into multiple chunk_ids, so a single
       question's top_k=10 semantic results could contain 4+ literal
       copies of one page — wasting real prompt tokens on redundant
       content and crowding out other chunks that would otherwise have
       ranked in the window.

       Fingerprint is (document_title, page_number, text[:80]) — NOT
       just (document_title, page_number) — matching the exact
       convention add_exact_code_matches() and add_isolation_dwg_matches()
       already use for their own de-dup, and for a real reason found
       live: a single page can legitimately hold multiple genuinely
       different chunks (split_dense_tables() deliberately splits a
       dense table into several row-range sub-chunks — a real case had
       three distinct, real chunks all from one page: the main prose,
       rows 1-6 of a spec table, and rows 7-9, the latter containing the
       exact pressure value a real question needed). Deduping by page
       number alone would have silently discarded two of those three,
       losing real content rather than removing a real duplicate. Only
       chunks with genuinely identical text collapse together; distinct
       sub-chunks from the same page all survive.
    2. Sorting was entirely absent — query_chunks() returns semantic
       results in distance order, but add_exact_code_matches() and
       add_isolation_dwg_matches() append their matches at the end
       regardless of relevance. A real reported case: the one clearly
       correct source for a question ended up listed dead last,
       underneath several barely-related semantic near-misses, because
       nothing ever re-sorted the merged list. keyword_search_chunks()
       and search_dwg_titles_by_keywords() both use -1.0 as a deliberate
       "exact/deterministic match" sentinel, which sorts before any real
       semantic distance (always >= 0) — so this naturally promotes
       boosted matches to the top, where they belong."""
    seen = set()
    deduped = []
    for c in chunks:
        m = c["metadata"]
        key = (m["document_title"], m["page_number"], c["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    ordered = sorted(deduped, key=lambda c: c["distance"])
    return _reorder_page_sequences(ordered)


def _reorder_page_sequences(ordered: list[dict], max_gap: int = 1) -> list[dict]:
    """Real bug found live (Sept 2026, see BACKLOG.md): a real multi-page
    procedure (the CAT 3512E fuel filter change, pages 173-177) retrieved
    completely intact — every real page was in the top_k window — but in
    scrambled order (175, 176, 173, 174, 177), because each page's chunk is
    scored independently by its own embedding distance, with no awareness
    that consecutive pages of the same document are often one continuous
    procedure. Claude then wrote the answer in that same scrambled order
    (leading with the Secondary Filter procedure, Primary Filter trailing
    in almost last), even though the content itself was accurate.

    Fix: after the existing distance sort, find runs of STRICTLY
    consecutive pages (page N, N+1, N+2, ... — max_gap=1, deliberately the
    narrowest possible definition of "a sequence," not a guessed-at
    leniency) within the same document, and re-sort just those runs by
    page number — anchored at the position of the run's best-ranked
    (lowest-distance) member, so the run's overall placement relative to
    everything else is unaffected, only the reading order *within* it.
    Chunks not part of any run keep their original relevance-ranked
    position untouched.

    Known, disclosed trade-off, not solved by this fix: page adjacency
    alone can't tell "still the same procedure, continued" from "the
    manual's next, unrelated procedure happens to start on the very next
    page" (confirmed real case: page 171, "Filter Screen (DEF) -
    Inspect/Clean," is strictly adjacent to page 172 and gets pulled into
    the same run even though it's a different system entirely). That's a
    real precision problem, but a separate one — see the standalone
    BACKLOG.md entry for pages ranking above the real answer due to
    vocabulary overlap. This fix only reorders what's already being
    retrieved; it doesn't change what qualifies for retrieval."""
    doc_pages = defaultdict(set)
    for c in ordered:
        m = c["metadata"]
        doc_pages[m["document_title"]].add(m["page_number"])

    # (document_title, page_number) -> run key (the run's own first page),
    # only recorded for pages that are part of a real run of 2+ consecutive
    # pages — an isolated page is left out of this map entirely.
    run_of_page = {}
    for doc_title, pages in doc_pages.items():
        pages_sorted = sorted(pages)
        run = [pages_sorted[0]]
        for p in pages_sorted[1:]:
            if p - run[-1] <= max_gap:
                run.append(p)
            else:
                if len(run) > 1:
                    for rp in run:
                        run_of_page[(doc_title, rp)] = (doc_title, run[0])
                run = [p]
        if len(run) > 1:
            for rp in run:
                run_of_page[(doc_title, rp)] = (doc_title, run[0])

    orig_index = {id(c): i for i, c in enumerate(ordered)}
    result = []
    emitted_runs = set()
    for c in ordered:
        m = c["metadata"]
        run_key = run_of_page.get((m["document_title"], m["page_number"]))
        if run_key is None:
            result.append(c)
            continue
        if run_key in emitted_runs:
            continue
        emitted_runs.add(run_key)
        run_chunks = [
            cc for cc in ordered
            if run_of_page.get((cc["metadata"]["document_title"], cc["metadata"]["page_number"])) == run_key
        ]
        run_chunks.sort(key=lambda cc: (cc["metadata"]["page_number"], orig_index[id(cc)]))
        result.extend(run_chunks)
    return result


# Retrieval boost for isolation/safety questions (Sept 2026, see
# BACKLOG.md's vessel knowledge graph entry). Confirmed directly: a real
# question about isolating a specific pump never surfaced its own
# piping schematic anywhere in the top 30 semantic results, regardless
# of top_k — semantic search doesn't reliably connect narrative
# lockout/tagout language to drawing content, a structural gap, not a
# ranking one. This is the smaller, no-graph-required fix recommended
# alongside that finding: when a question uses isolation language AND
# names something specific enough to match a drawing's title, pull that
# drawing's chunks directly rather than hoping semantic search finds it.
ISOLATION_LANGUAGE_PATTERN = re.compile(
    r"\b(isolat\w*|lock\s*out|lockout|tag\s*out|tagout|LOTO|"
    r"shut\s+(off|down)|de-?energi\w*|depressuriz\w*|disconnect\w*)\b",
    re.IGNORECASE,
)

# Common words excluded from the keyword match so "the," "engine," or
# "system" alone don't fire a match against every drawing in the
# library — deliberately short list, not a full stopword corpus: the
# goal is filtering out words too generic to mean anything, not
# filtering aggressively. A missed real keyword just means this
# question falls through to semantic search alone, same as before this
# fix existed; a false positive here only costs one extra, harmless
# drawing lookup — same risk tradeoff as add_exact_code_matches above.
GENERIC_TERMS_EXCLUDED = {
    "what", "which", "would", "should", "could", "need", "needs", "needed",
    "work", "working", "system", "systems", "engine", "engines", "pump",
    "pumps", "valve", "valves", "with", "this", "that", "these", "those",
    "isolate", "isolated", "isolation", "before", "while", "there", "their",
}


def find_matching_drawing_chunks(question: str) -> list[dict]:
    """If the question uses isolation/lockout/safety language, extracts
    meaningful keywords (4+ letters, not in the generic-terms list) and
    searches drawing-type document titles directly for them. Returns []
    if the isolation-language trigger isn't present, or no keyword
    matches a real drawing title — falls through to normal retrieval
    unchanged either way, same as find_matching_document_title()
    above."""
    if not ISOLATION_LANGUAGE_PATTERN.search(question):
        return []
    words = re.findall(r"[A-Za-z]{4,}", question.lower())
    keywords = [w for w in words if w not in GENERIC_TERMS_EXCLUDED]
    if not keywords:
        return []
    return search_dwg_titles_by_keywords(keywords)


def add_isolation_dwg_matches(question: str, chunks: list[dict]) -> list[dict]:
    """Merges any drawing chunks found by find_matching_drawing_chunks()
    into the semantic results, de-duped the same way
    add_exact_code_matches() is — a drawing that already ranked well
    semantically doesn't get added twice."""
    drawing_matches = find_matching_drawing_chunks(question)
    if not drawing_matches:
        return chunks

    existing_fingerprints = {
        (c["metadata"]["document_title"], c["metadata"]["page_number"], c["text"][:80])
        for c in chunks
    }
    new_matches = [
        m for m in drawing_matches
        if (m["metadata"]["document_title"], m["metadata"]["page_number"], m["text"][:80])
        not in existing_fingerprints
    ]
    return chunks + new_matches


def build_prompt(question: str, chunks: list[dict], equipment_context: str = "",
                  conversation_history: list[dict] | None = None, notes_context: str = "",
                  inventory_context: str = "") -> str:
    excerpt_blocks = []
    for i, c in enumerate(chunks):
        citation = f'{c["metadata"]["document_title"]}, {c["metadata"]["revision"]}, p. {c["metadata"]["page_number"]}'
        # source_file exposed here (Sept 2026, real request from Dave, for
        # the "Full manuals" reference below) so Claude has the actual
        # real filename to work with — never asked to guess or reconstruct
        # it from document_title, which is a differently-formatted display
        # string (e.g. "O&M Manual" vs the real file's "OMM" abbreviation),
        # not a deterministic transform of the real filename.
        source_file = c["metadata"].get("source_file", "unknown")
        excerpt_blocks.append(f"--- Excerpt {i+1} ({citation}, source_file: {source_file}) ---\n{c['text']}")
    excerpts = "\n\n".join(excerpt_blocks)
    equipment_block = f"\n{equipment_context}\n" if equipment_context else ""
    notes_block = f"\n{notes_context}\n" if notes_context else ""
    inventory_block = f"\n{inventory_context}\n" if inventory_context else ""

    # Real feature, generalized from a single-turn mechanism (Sept 2026,
    # see BACKLOG.md): originally just one prior {question, answer} dict,
    # scoped narrowly to the clarifying-question "did I already ask" check.
    # Broadened to the last few turns so genuinely ambiguous follow-ups
    # ("what about the starboard engine?", "how often?") can be resolved
    # in context too — an engineer shouldn't have to restate the whole
    # topic just to ask a natural follow-up. Deliberately still capped
    # (last 3 turns, not the whole conversation) — plenty for both jobs
    # this serves, without unboundedly growing the prompt on a long chat.
    # Oldest first, so "the most recent turn" (used by the
    # clarifying-question check in SYSTEM_PROMPT) is unambiguously the
    # last one shown, not the first.
    history_block = ""
    if conversation_history:
        recent_turns = conversation_history[-3:]
        turn_blocks = [
            f'Turn {i} of {len(recent_turns)}:\nUser asked: "{turn["question"]}"\n'
            f'You answered: "{turn["answer"]}"'
            for i, turn in enumerate(recent_turns, 1)
        ]
        history_block = f"""
Recent conversation history, oldest first (use this to interpret a \
follow-up question that only makes sense in context — see system prompt \
rules; the LAST turn below is specifically what the "did you already ask \
a clarifying question" check refers to):

{(chr(10) * 2).join(turn_blocks)}
"""

    return f"""Question: {question}
{equipment_block}{notes_block}{inventory_block}{history_block}
Manual excerpts retrieved for this question:

{excerpts}

Answer the question using only the excerpts above."""



# Retrieval boost for drawing/document requests (Sept 2026, see BACKLOG.md).
# When a question contains genuine "show me" language AND the question text
# contains a known document title from the inventory, we bypass semantic
# search entirely and fetch that document's chunks directly by title.
#
# Real motivating case: clicking "AzimuthThruster - MBB ShaftArrangementM1
# General Arrangement Drawing" from the library panel fired a "Show me the..."
# question but semantic search returned BergPropulsion MTA O&M Manual pages
# instead of the drawing. Semantic search reliably fails for this case because
# shaft component content from manuals is semantically similar to shaft-related
# drawing descriptions — the drawing itself scores lower.
#
# This is deterministic, not language-dependent: the library panel generates
# questions in the exact format "Show me the {document_title}", so the match
# is essentially guaranteed for panel clicks. Natural-language "show me"
# requests that don't match a known title fall through to normal retrieval
# unchanged.
SHOW_LANGUAGE_PATTERN = re.compile(
    r"\b(show\s+me|can\s+I\s+see|let\s+me\s+see|display|view|picture\s+of|"
    r"what\s+does\s+.+\s+look\s+like|pull\s+up)\b",
    re.IGNORECASE,
)


def find_matching_document_title(question: str) -> str | None:
    """If the question contains showing-language AND a known document title
    from the inventory, returns the matched title so the caller can fetch
    its chunks directly. Returns None if no match — falls through to normal
    semantic search. Case-insensitive title match, requiring the full title
    to appear in the question (the library panel generates questions in the
    exact format 'Show me the {document_title}', so this is reliable for
    panel clicks; natural-language partial matches are deliberately not
    attempted to avoid false positives on common words like 'Hull').

    Fetches the inventory fresh on each call — cheap since it's a DISTINCT
    query on an indexed column, and this keeps the function stateless."""
    if not SHOW_LANGUAGE_PATTERN.search(question):
        return None
    try:
        from retrieval import get_pg_connection
        from document_inventory import get_document_inventory
        conn = get_pg_connection()
        inventory = get_document_inventory(conn)
        conn.close()
    except Exception:
        return None

    question_lower = question.lower()
    for doc in inventory:
        title = doc.get("document_title", "")
        if title and title.lower() in question_lower:
            return title
    return None


def build_search_text(question: str, conversation_history: list[dict] | None) -> str:
    """Combines the current question with recent conversation for
    retrieval purposes only — shared by get_answer() and answer()'s
    --dry-run path so the two can't silently drift apart.

    Only the last 2 turns' QUESTIONS are used, deliberately not their
    answers — see get_answer()'s docstring for the full reasoning (a
    prior answer's prose is often long and would dilute the embedding
    query away from what's actually being asked now, the same real
    dilution problem documented elsewhere in this file)."""
    if not conversation_history:
        return question
    prior_questions = [h["question"] for h in conversation_history[-2:] if h.get("question")]
    if not prior_questions:
        return question
    return " ".join(prior_questions + [question])


def get_answer(question: str, engine: str = "voyage", top_k: int = 10,
               api_key: str | None = None, conversation_history: list[dict] | None = None) -> dict:
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

    conversation_history (Aug 2026, see BACKLOG.md's clarifying-question
    entry; generalized Sept 2026 to a real multi-turn follow-up feature):
    optional list of {"question": ..., "answer": ...} dicts, oldest first.
    Originally a single immediately-prior-turn dict scoped only to the
    clarifying-question "did I already ask" check; broadened to carry the
    last few turns so genuinely ambiguous follow-ups ("what about the
    starboard engine?", "how often?") can be resolved without the engineer
    repeating the whole topic. Used two ways, deliberately different in
    scope:
      - Search/retrieval (below): only the last 2 turns' QUESTIONS (not
        their answers) are combined with the current question. Answers are
        left out of the search text on purpose — a full prior answer is
        often a long procedure or spec table, and folding that much prose
        into the embedding query risks diluting it away from what the
        current question is actually asking, the same real dilution
        problem documented elsewhere in this file and in BACKLOG.md. The
        prior questions' own wording (e.g. "the port engine," "the fuel
        filter") is what actually carries the missing referent.
      - Prompt context (build_prompt): the last 3 full turns (question +
        answer) are shown to Claude, since interpreting a follow-up like
        "how often?" genuinely needs to know what the previous ANSWER
        said, not just what was asked.
    The caller (app.py) decides whether to pass this; the CLI below does
    not by default, so plain CLI testing remains single-shot/stateless
    unless a caller passes one in.

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
            "show_document_images": list[dict],  # {"url", "document_title",
                                   # "page_number"} for any excerpt Claude
                                   # identified as the SPECIFIC thing a
                                   # question explicitly asked to be shown
                                   # (Sept 2026) — the app renders these
                                   # prominently, not tucked in Sources
        }

    Raises:
        ValueError if no Anthropic API key is available.
    """
    # Search query includes recent prior questions too, when there are any
    # (Aug 2026, broadened Sept 2026 from 1 turn to 2 — see
    # build_search_text()'s docstring) — a short follow-up ("the azimuth
    # one," "how often?") often isn't enough signal alone for good
    # retrieval. Combining with recent questions gives the embedding real
    # context to work with, without changing what's shown to Claude as
    # "the question" in the prompt itself (build_prompt still receives the
    # bare current question).
    search_query = expand_units(build_search_text(question, conversation_history))

    # Retrieval boost (Sept 2026) — if the question contains showing-language
    # AND matches a known document title exactly, fetch that document's chunks
    # directly rather than relying on semantic search, which reliably fails for
    # drawings. Falls through to normal retrieval if no title match is found.
    matched_title = find_matching_document_title(question)
    if matched_title:
        chunks = fetch_chunks_by_title(matched_title, top_k=8)
    else:
        chunks = query_chunks(search_query, engine=engine, top_k=top_k)
        chunks = add_exact_code_matches(question, chunks)
        chunks = add_isolation_dwg_matches(question, chunks)
    chunks = finalize_chunks(chunks)

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

    prompt = build_prompt(question, chunks, equipment_context, conversation_history,
                           notes_context, inventory_context)

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "No ANTHROPIC_API_KEY available. Set the ANTHROPIC_API_KEY "
            "environment variable, or pass api_key= explicitly. "
            "Get a key at https://console.anthropic.com"
        )

    import anthropic
    # Explicit timeout (Sept 2026, real bug found live in ingestion —
    # see vision_extraction.py): without one, a stalled connection hangs
    # forever with nothing to catch it. This is the live app's answer
    # path, so an unbounded hang here means a user's chat freezes with
    # no error at all — worse than the same bug in an offline script.
    client = anthropic.Anthropic(api_key=key, timeout=120.0)
    # Raised from 1000 (Sept 2026, real bug found live via the "Full
    # manuals" feature above) — a real, long multi-step procedural answer
    # (the fuel filter change, Primary + Secondary + Duplex procedures)
    # hit 1000 exactly, confirmed via stop_reason == "max_tokens", cutting
    # off mid-procedure before ever reaching the FIELD_NOTE_IDS/SAFETY_INFO
    # sections were even fully written, let alone the new footer. Raised
    # generously, not just to the exact size that would have covered this
    # one case, matching the same reasoning already applied to
    # extract_equipment_list.py's max_tokens bump.
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
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

    # show_document_images (Sept 2026) — resolves Claude's reported
    # excerpt numbers into real image data from the chunks' own
    # metadata, the same reliable pattern as format_sources(): code
    # reads real data, never trusts a model to know or report a URL
    # itself. 1-indexed to match how excerpts are numbered in
    # build_prompt(); an out-of-range or missing-image excerpt is
    # silently skipped rather than raising, since a wrong/missing image
    # here should never be the reason an answer fails to display.
    show_document_images = []
    seen_urls = set()
    for excerpt_num in parsed["show_document_excerpts"]:
        idx = excerpt_num - 1
        if 0 <= idx < len(chunks):
            m = chunks[idx]["metadata"]
            url = m.get("page_image_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                show_document_images.append({
                    "url": url,
                    "document_title": m["document_title"],
                    "page_number": m["page_number"],
                })

    # Sources filtering (Sept 2026, real bug found live) — narrows the
    # chunks shown in "View Sources" down to only the ones Claude actually
    # says it drew on, via ###EXCERPTS_USED### above. Real motivating
    # case: a generator-engine question retrieved 7 main-engine excerpts
    # alongside 3 correct generator excerpts; Claude correctly used only
    # the 3 real ones when writing the answer, but Sources previously
    # showed all 10, looking alarming even though the answer itself was
    # accurate.
    #
    # excerpts_used is None (not []) specifically when the markers were
    # missing/malformed — genuinely unknown, not "Claude said zero" — and
    # in that case this must fall back to the full original chunks rather
    # than risk hiding real sources over a parsing hiccup. A non-empty
    # excerpts_used that resolves to zero valid indices (Claude reported
    # numbers, but none were valid — should be rare) gets the same safe
    # fallback, for the same reason. Only a genuinely empty [] (Claude
    # explicitly wrote NONE) is trusted to mean zero sources.
    if parsed["excerpts_used"] is None:
        source_chunks = chunks
    else:
        valid_indices = {n - 1 for n in parsed["excerpts_used"] if 0 <= n - 1 < len(chunks)}
        source_chunks = [c for i, c in enumerate(chunks) if i in valid_indices]
        if not source_chunks and parsed["excerpts_used"]:
            source_chunks = chunks

    return {
        "answer": parsed["answer"],
        "chunks": source_chunks,
        "prompt": prompt,
        "safety_info": parsed["safety_info"],
        "field_notes_used": field_notes_used,
        "show_document_images": show_document_images,
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
           conversation_history: list[dict] | None = None):
    """CLI-facing wrapper — keeps the exact command-line behavior/UX
    unchanged (dry-run printing, sys.exit on a missing key) while
    delegating the real work to get_answer(). conversation_history support
    added Aug 2026 (as a single-turn previous_exchange, generalized Sept
    2026 to multiple turns) specifically for debugging the clarifying-
    question and follow-up-question features from the CLI, reproducing
    exactly what app.py would send."""
    if dry_run:
        search_query = expand_units(build_search_text(question, conversation_history))
        chunks = query_chunks(search_query, engine=engine, top_k=top_k)
        chunks = add_exact_code_matches(question, chunks)
        chunks = add_isolation_dwg_matches(question, chunks)
        chunks = finalize_chunks(chunks)
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
        prompt = build_prompt(question, chunks, equipment_context, conversation_history,
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
        result = get_answer(question, engine=engine, top_k=top_k, conversation_history=conversation_history)
    except ValueError as e:
        sys.exit(str(e))

    print(result["answer"])
    if result.get("show_document_images"):
        print("\n📄 Shown prominently (question explicitly asked to see this):")
        for img in result["show_document_images"]:
            print(f'- {img["document_title"]}, p. {img["page_number"]}: {img["url"]}')
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
    # CLI still only simulates a single prior turn — plenty for debugging
    # both the clarifying-question check and follow-up interpretation,
    # without needing a multi-flag interface just for manual testing.
    # Wrapped in a 1-item list since answer()/get_answer() now take the
    # generalized multi-turn conversation_history.
    conversation_history = None
    if "--previous-question" in args:
        idx = args.index("--previous-question")
        prev_q = args[idx + 1]
        del args[idx:idx + 2]
        prev_a = ""
        if "--previous-answer" in args:
            idx = args.index("--previous-answer")
            prev_a = args[idx + 1]
            del args[idx:idx + 2]
        conversation_history = [{"question": prev_q, "answer": prev_a}]
    if not args:
        sys.exit('Usage: python answer_query.py [--engine voyage|tfidf] [--dry-run] '
                  '[--previous-question "..." --previous-answer "..."] "your question"')
    answer(args[0], engine=engine, dry_run=dry_run, conversation_history=conversation_history)
