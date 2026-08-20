"""
Prototype retrieval layer: store chunks in Chroma, query with a placeholder
embedding function, return citation-ready results.

IMPORTANT — placeholder embeddings:
  This uses TF-IDF (classic keyword-overlap similarity) as a stand-in for a
  real embedding model. It's enough to prove out the storage/query/ranking
  plumbing without needing network access to a model host. It will miss
  matches that use different words for the same idea (e.g. "won't start" vs
  "fails to crank"), which real embeddings handle. Swapping in a real
  embedding model (e.g. Voyage AI, which Anthropic partners with, or a local
  sentence-transformers model) is a drop-in change: replace TfidfEmbedder
  with a class that implements the same __call__ interface. See BACKLOG.md.

Usage:
    python retrieval.py build     # parse chunks.jsonl, build the Chroma collection
    python retrieval.py query "some question"   # search it
"""

import json
import sys
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
from sklearn.feature_extraction.text import TfidfVectorizer

CHUNKS_PATH = Path(__file__).parent / "chunks.jsonl"
DB_PATH = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "vessel_tms"


class TfidfEmbedder(EmbeddingFunction):
    """Placeholder embedding function. Fits a TF-IDF vectorizer on the
    corpus at build time and reuses it at query time. Real embedding models
    (Voyage AI, sentence-transformers, etc.) implement this same __call__
    interface, taking a list of strings and returning a list of vectors."""

    def __init__(self, vectorizer: TfidfVectorizer):
        self.vectorizer = vectorizer

    def __call__(self, input: Documents) -> Embeddings:
        return self.vectorizer.transform(input).toarray().tolist()


def load_chunks():
    if not CHUNKS_PATH.exists():
        sys.exit(f"No chunks found at {CHUNKS_PATH} — run ingestion/parse_and_chunk.py first "
                  f"and copy its chunks.jsonl output here.")
    with open(CHUNKS_PATH) as f:
        return [json.loads(line) for line in f]


def build_collection():
    chunks = load_chunks()
    # Only chunks with real text can be embedded/searched. Metadata-only
    # chunks (e.g. the thruster drawing page) are skipped here — see
    # BACKLOG.md "Azimuth thruster drawing has no text layer".
    text_chunks = [c for c in chunks if c["has_text_layer"] and c["text"].strip()]
    skipped = len(chunks) - len(text_chunks)

    vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
    vectorizer.fit([c["text"] for c in text_chunks])
    embedder = TfidfEmbedder(vectorizer)

    client = chromadb.PersistentClient(path=str(DB_PATH))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME, embedding_function=embedder)

    collection.add(
        ids=[c["chunk_id"] for c in text_chunks],
        documents=[c["text"] for c in text_chunks],
        metadatas=[{
            "document_title": c["document_title"],
            "revision": c["revision"],
            "page_number": c["page_number"],
            "equipment_model": c["equipment_model"],
            "document_type": c["document_type"],
            "source_file": c["source_file"],
        } for c in text_chunks],
    )

    # Persist the fitted vectorizer alongside the DB so query-time can reuse it
    import pickle
    with open(DB_PATH / "vectorizer.pkl", "wb") as f:
        pickle.dump(vectorizer, f)

    print(f"Indexed {len(text_chunks)} chunks ({skipped} skipped — no text layer).")


def query(question: str, top_k: int = 3):
    import pickle
    with open(DB_PATH / "vectorizer.pkl", "rb") as f:
        vectorizer = pickle.load(f)
    embedder = TfidfEmbedder(vectorizer)

    client = chromadb.PersistentClient(path=str(DB_PATH))
    collection = client.get_collection(COLLECTION_NAME, embedding_function=embedder)

    results = collection.query(query_texts=[question], n_results=top_k)

    print(f'\nQuery: "{question}"\n')
    for i, (doc, meta, dist) in enumerate(zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    )):
        citation = f'{meta["document_title"]}, {meta["revision"]}, p. {meta["page_number"]}'
        print(f"[{i+1}] {citation}  (distance={dist:.3f})")
        print(f"    {doc[:180].replace(chr(10), ' ')}...")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python retrieval.py [build|query \"question\"]")
    if sys.argv[1] == "build":
        build_collection()
    elif sys.argv[1] == "query":
        if len(sys.argv) < 3:
            sys.exit('Usage: python retrieval.py query "your question"')
        query(sys.argv[2])
    else:
        sys.exit("Unknown command. Use 'build' or 'query'.")
