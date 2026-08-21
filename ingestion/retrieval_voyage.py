"""
Real semantic embeddings via Voyage AI, replacing the TF-IDF placeholder.

Why Voyage AI: Anthropic doesn't run its own embedding model, and Voyage AI
is Anthropic's recommended embedding partner. Check current model names and
pricing at https://docs.voyageai.com before relying on the defaults below —
model lineups and prices change, and this was written from documentation
that may not reflect what's current when you read this.

Requires a Voyage API key, set as an environment variable — never typed
into code, never pasted into a chat with Claude:

    export VOYAGE_API_KEY="your-key-here"

Get a key at: https://dash.voyageai.com

This can genuinely be tested locally (unlike from Claude's sandbox, which
can't reach voyageai.com) — your own machine has normal network access.

Usage:
    python retrieval_voyage.py build     # re-embed chunks.jsonl with real embeddings
    python retrieval_voyage.py query "some question"
"""

import json
import os
import sys
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

CHUNKS_PATH = Path(__file__).parent / "chunks.jsonl"
DB_PATH = Path(__file__).parent / "chroma_db_voyage"  # separate from the TF-IDF DB so both can be compared side by side
COLLECTION_NAME = "vessel_tms"
EMBED_MODEL = "voyage-3"  # verify this is still current at https://docs.voyageai.com/docs/embeddings


class VoyageEmbedder(EmbeddingFunction):
    """Real embedding function. Same __call__ interface as the TF-IDF
    placeholder in retrieval.py, so this is a genuine drop-in swap — nothing
    else in the pipeline needs to change to use it.

    Voyage distinguishes between embedding a document (input_type='document')
    at ingestion time and embedding a query (input_type='query') at search
    time — the two are optimized slightly differently. This class defaults
    to 'document' for building the collection; query() below overrides it.
    """

    def __init__(self, api_key: str, input_type: str = "document"):
        import voyageai
        self.client = voyageai.Client(api_key=api_key)
        self.input_type = input_type

    def __call__(self, input: Documents) -> Embeddings:
        result = self.client.embed(input, model=EMBED_MODEL, input_type=self.input_type)
        return result.embeddings


def get_api_key() -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        sys.exit(
            "No VOYAGE_API_KEY environment variable found.\n"
            "Set it first: export VOYAGE_API_KEY=\"your-key-here\"\n"
            "Get a key at https://dash.voyageai.com"
        )
    return key


def load_chunks():
    if not CHUNKS_PATH.exists():
        sys.exit(f"No chunks found at {CHUNKS_PATH} — copy chunks.jsonl into this folder first.")
    with open(CHUNKS_PATH) as f:
        return [json.loads(line) for line in f]


def build_collection():
    chunks = load_chunks()
    text_chunks = [c for c in chunks if c["has_text_layer"] and c["text"].strip()]
    skipped = len(chunks) - len(text_chunks)

    api_key = get_api_key()
    embedder = VoyageEmbedder(api_key, input_type="document")

    client = chromadb.PersistentClient(path=str(DB_PATH))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME, embedding_function=embedder)

    # Embed in batches — Voyage (like most embedding APIs) has a per-request
    # size limit, and batching also means one bad chunk doesn't kill the
    # whole run.
    batch_size = 20
    for i in range(0, len(text_chunks), batch_size):
        batch = text_chunks[i:i + batch_size]
        collection.add(
            ids=[c["chunk_id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[{
                "document_title": c["document_title"],
                "revision": c["revision"],
                "page_number": c["page_number"],
                "equipment_model": c["equipment_model"],
                "document_type": c["document_type"],
                "source_file": c["source_file"],
            } for c in batch],
        )
        print(f"Embedded {min(i + batch_size, len(text_chunks))}/{len(text_chunks)}...")

    print(f"\nIndexed {len(text_chunks)} chunks with real embeddings "
          f"({skipped} skipped — no text layer).")


def query_chunks(question: str, top_k: int = 3) -> list[dict]:
    api_key = get_api_key()
    # input_type='query' here, not 'document' — this is the one place
    # querying differs from building, per Voyage's recommended usage.
    embedder = VoyageEmbedder(api_key, input_type="query")

    client = chromadb.PersistentClient(path=str(DB_PATH))
    collection = client.get_collection(COLLECTION_NAME, embedding_function=embedder)
    results = collection.query(query_texts=[question], n_results=top_k)

    return [
        {"text": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def query(question: str, top_k: int = 3):
    chunks = query_chunks(question, top_k=top_k)
    print(f'\nQuery: "{question}"\n')
    for i, c in enumerate(chunks):
        meta = c["metadata"]
        citation = f'{meta["document_title"]}, {meta["revision"]}, p. {meta["page_number"]}'
        print(f"[{i+1}] {citation}  (distance={c['distance']:.3f})")
        print(f"    {c['text'][:180].replace(chr(10), ' ')}...")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python retrieval_voyage.py [build|query \"question\"]")
    if sys.argv[1] == "build":
        build_collection()
    elif sys.argv[1] == "query":
        if len(sys.argv) < 3:
            sys.exit('Usage: python retrieval_voyage.py query "your question"')
        query(sys.argv[2])
    else:
        sys.exit("Unknown command. Use 'build' or 'query'.")
