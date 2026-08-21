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
    python answer_query.py --engine voyage "..."   # use real embeddings instead of TF-IDF
    python answer_query.py --dry-run "..."         # builds the prompt, doesn't call the API
"""

import os
import sys


SYSTEM_PROMPT = """You are a technical assistant for a ship's engineering department. \
You answer equipment questions using ONLY the manual excerpts provided below — \
never your own general knowledge of similar equipment, since exact procedures, \
part numbers, and specs vary by manufacturer and model.

Rules:
- If the excerpts don't contain enough information to answer, say so plainly \
rather than guessing or filling gaps with general knowledge.
- Every claim in your answer must be traceable to one of the excerpts. \
End with a "Sources:" line listing exactly which excerpts you used, in the \
format: [Document Title], [Revision], p. [Page Number].
- Be concise and procedural — the reader is a working engineer, not someone \
who wants prose. Use numbered steps when the excerpt describes a procedure.
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


def answer(question: str, engine: str = "tfidf", dry_run: bool = False, top_k: int = 3):
    if engine == "voyage":
        from retrieval_voyage import query_chunks
    else:
        from retrieval import query_chunks
    chunks = query_chunks(question, top_k=top_k)
    prompt = build_prompt(question, chunks)

    if dry_run:
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER PROMPT (what would be sent) ===")
        print(prompt)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "No ANTHROPIC_API_KEY environment variable found.\n"
            "Set it first: export ANTHROPIC_API_KEY=\"your-key-here\"\n"
            "Get a key at https://console.anthropic.com"
        )

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    print(response.content[0].text)


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")
    engine = "tfidf"
    if "--engine" in args:
        idx = args.index("--engine")
        engine = args[idx + 1]
        del args[idx:idx + 2]
    if not args:
        sys.exit('Usage: python answer_query.py [--engine tfidf|voyage] [--dry-run] "your question"')
    answer(args[0], engine=engine, dry_run=dry_run)
