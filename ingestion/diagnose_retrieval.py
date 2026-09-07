#!/usr/bin/env python3.14
"""Diagnoses retrieval for a specific question — shows exactly which chunks
are being returned and their similarity scores, so we can tell whether a
missed answer is a ranking problem, a top_k problem, or a chunking problem.

Usage (from ingestion/ directory, all env vars exported):
    python3.14 diagnose_retrieval.py "What type and grade of engine oil does the CAT 3512E use?"

Optional: --top_k N (default 10, to see more than the normal 5)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from retrieval import query_chunks, keyword_search_chunks
from answer_query import expand_units

question = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
top_k = 10
for i, a in enumerate(sys.argv):
    if a == "--top_k" and i+1 < len(sys.argv):
        top_k = int(sys.argv[i+1])

if not question:
    sys.exit("Usage: python3.14 diagnose_retrieval.py \"your question here\"")

print(f"\nQuestion: {question}")
print(f"Top_k: {top_k}")
print(f"\n{'='*70}")

search_query = expand_units(question)
if search_query != question:
    print(f"Expanded query: {search_query}")

chunks = query_chunks(search_query, engine="voyage", top_k=top_k)

print(f"\n📊 SEMANTIC SEARCH — top {top_k} chunks:\n")
for i, c in enumerate(chunks, 1):
    m = c["metadata"]
    print(f"  {i}. [{c['distance']:.4f}] {m['document_title']}, {m['revision']}, p.{m['page_number']}")
    print(f"     {c['text'][:200].replace(chr(10), ' ')}...")
    print()

# Also search by keyword
print(f"\n🔍 KEYWORD SEARCH for 'oil':\n")
kw_chunks = keyword_search_chunks(["oil", "lubricant", "viscosity"], limit_per_term=5)
seen = set()
for c in kw_chunks:
    m = c["metadata"]
    key = (m["document_title"], m["page_number"])
    if key in seen:
        continue
    seen.add(key)
    print(f"  [{c.get('distance','?')}] {m['document_title']}, p.{m['page_number']}")
    print(f"  {c['text'][:200].replace(chr(10), ' ')}...")
    print()
