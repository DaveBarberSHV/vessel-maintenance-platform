"""
Retrieval layer: store TM chunks in Chroma, embed and query them, return
citation-ready results.

Two embedding engines are supported, selected with --engine:
  voyage  (default) — real semantic embeddings via Voyage AI. Requires
           VOYAGE_API_KEY. This is the production path — proven in a live
           side-by-side test (see BACKLOG.md) to correctly find content
           that keyword-based search misses.
  tfidf  — classic keyword-overlap similarity, no API key or network
           needed. Kept around for offline testing only; known to miss
           matches that use different wording than the source ("won't
           start" vs "fails to crank") — see BACKLOG.md.

Each engine gets its own Chroma collection under chroma_db/<engine>/, so
switching engines never mixes up or overwrites the other's data.

Usage:
    python retrieval.py build                      # builds the voyage index (default)
    python retrieval.py build --engine tfidf        # builds the tfidf index instead
    python retrieval.py query "some question"       # queries voyage (default)
    python retrieval.py query --engine tfidf "..."  # queries tfidf instead
"""

import json
import os
import sys
import time
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

CHUNKS_PATH = Path(__file__).parent / "chunks.jsonl"
DB_ROOT = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "vessel_tms"
VOYAGE_MODEL = "voyage-3"  # verify still current at https://docs.voyageai.com/docs/embeddings


class TfidfEmbedder(EmbeddingFunction):
    """Placeholder embedding function — keyword overlap only, no network
    needed. See module docstring for when this is (and isn't) appropriate."""

    def __init__(self, vectorizer):
        self.vectorizer = vectorizer

    def __call__(self, input: Documents) -> Embeddings:
        return self.vectorizer.transform(input).toarray().tolist()


class VoyageEmbedder(EmbeddingFunction):
    """Real embedding function via Voyage AI (Anthropic's embedding
    partner). input_type differs between building (documents) and
    querying (query) per Voyage's recommended usage.

    Internally batches requests to respect Voyage's per-request limits
    (max 1000 items, max ~320,000 tokens per batch) — added Aug 2026 after
    a real failure: a single collection.add() call for one large document
    submitted its entire chunk list as one unbatched request. Fine for
    smaller documents, but broke on two real ones once the library grew
    past 14 documents: a 347K-token manual, and a 4,095-chunk dense parts
    list. See BACKLOG.md. Token counts are estimated (~4 chars/token, a
    common rough heuristic for English text — Voyage doesn't expose an
    offline tokenizer here), with a safety margin well under the real
    320,000 limit specifically because it's an estimate, not exact."""

    MAX_ITEMS_PER_BATCH = 1000  # Voyage's hard limit
    MAX_ESTIMATED_TOKENS_PER_BATCH = 280_000  # safety margin under the real 320,000 limit

    def __init__(self, api_key: str, input_type: str = "document"):
        import voyageai
        self.client = voyageai.Client(api_key=api_key)
        self.input_type = input_type

    def __call__(self, input: Documents) -> Embeddings:
        batches = list(self._batches(input))
        all_embeddings = []
        for i, batch in enumerate(batches):
            result = self.client.embed(batch, model=VOYAGE_MODEL, input_type=self.input_type)
            all_embeddings.extend(result.embeddings)
            if i < len(batches) - 1:
                time.sleep(1)  # brief, polite pause only when there's more than one batch
        return all_embeddings

    def _batches(self, texts):
        batch, batch_tokens = [], 0
        for text in texts:
            est_tokens = max(1, len(text) // 4)
            if batch and (len(batch) >= self.MAX_ITEMS_PER_BATCH
                          or batch_tokens + est_tokens > self.MAX_ESTIMATED_TOKENS_PER_BATCH):
                yield batch
                batch, batch_tokens = [], 0
            batch.append(text)
            batch_tokens += est_tokens
        if batch:
            yield batch


def db_path(engine: str) -> Path:
    return DB_ROOT / engine


def get_voyage_key() -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        # Raises rather than sys.exit()s — this function is called from
        # query_chunks(), which is imported directly by the Streamlit front
        # end (via answer_query.get_answer()). sys.exit() there would kill
        # the whole running app process for every user, not just report an
        # error for one request. CLI callers below catch this and preserve
        # the original clean-error behavior.
        raise ValueError(
            "No VOYAGE_API_KEY available. Set the VOYAGE_API_KEY "
            "environment variable. Get a key at https://dash.voyageai.com"
        )
    return key


def load_chunks():
    if not CHUNKS_PATH.exists():
        sys.exit(f"No chunks found at {CHUNKS_PATH} — run ingestion/parse_and_chunk.py first "
                  f"and copy its chunks.jsonl output here.")
    with open(CHUNKS_PATH) as f:
        return [json.loads(line) for line in f]


def build_collection(engine: str = "voyage"):
    chunks = load_chunks()
    # Only chunks with real text can be embedded/searched. Metadata-only
    # chunks (e.g. the thruster drawing page) are skipped here — see
    # BACKLOG.md "Azimuth thruster drawing has no text layer".
    text_chunks = [c for c in chunks if c["has_text_layer"] and c["text"].strip()]
    skipped = len(chunks) - len(text_chunks)

    path = db_path(engine)
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    if engine == "voyage":
        api_key = get_voyage_key()
        embedder = VoyageEmbedder(api_key, input_type="document")
        collection = client.create_collection(COLLECTION_NAME, embedding_function=embedder)

        # VoyageEmbedder now batches internally to respect Voyage's own
        # per-request limits (see its docstring) — no manual batching
        # needed here anymore. This also resolves the older "batch pacing
        # won't scale" backlog concern: batches are now sized close to
        # Voyage's real limits instead of a conservative fixed 20, so far
        # fewer requests are needed overall.
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
        print(f"Embedded {len(text_chunks)} chunks.")

    elif engine == "tfidf":
        from sklearn.feature_extraction.text import TfidfVectorizer
        import pickle
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
        vectorizer.fit([c["text"] for c in text_chunks])
        embedder = TfidfEmbedder(vectorizer)
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
        with open(path / "vectorizer.pkl", "wb") as f:
            pickle.dump(vectorizer, f)

    else:
        sys.exit(f"Unknown engine '{engine}'. Use 'voyage' or 'tfidf'.")

    print(f"\nIndexed {len(text_chunks)} chunks with {engine} embeddings "
          f"({skipped} skipped — no text layer).")


def query_chunks(question: str, engine: str = "voyage", top_k: int = 3) -> list[dict]:
    """Core retrieval function: returns a list of {text, metadata, distance}
    dicts for the top_k most relevant chunks. Used by both the CLI query
    command below and by answer_query.py to build a Claude prompt."""
    path = db_path(engine)

    if engine == "voyage":
        api_key = get_voyage_key()
        embedder = VoyageEmbedder(api_key, input_type="query")  # 'query' not 'document' at search time
    elif engine == "tfidf":
        import pickle
        with open(path / "vectorizer.pkl", "rb") as f:
            vectorizer = pickle.load(f)
        embedder = TfidfEmbedder(vectorizer)
    else:
        # Same reasoning as get_voyage_key() above — this function is
        # imported directly by the front end, so no sys.exit() here.
        raise ValueError(f"Unknown engine '{engine}'. Use 'voyage' or 'tfidf'.")

    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_collection(COLLECTION_NAME, embedding_function=embedder)
    results = collection.query(query_texts=[question], n_results=top_k)

    return [
        {"text": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def query(question: str, engine: str = "voyage", top_k: int = 3):
    """CLI-facing wrapper: prints results for human inspection."""
    chunks = query_chunks(question, engine=engine, top_k=top_k)
    print(f'\nQuery ({engine}): "{question}"\n')
    for i, c in enumerate(chunks):
        meta, dist = c["metadata"], c["distance"]
        citation = f'{meta["document_title"]}, {meta["revision"]}, p. {meta["page_number"]}'
        print(f"[{i+1}] {citation}  (distance={dist:.3f})")
        print(f"    {c['text'][:180].replace(chr(10), ' ')}...")
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    engine = "voyage"
    if "--engine" in args:
        idx = args.index("--engine")
        engine = args[idx + 1]
        del args[idx:idx + 2]

    if not args:
        sys.exit('Usage: python retrieval.py [build|query "question"] [--engine voyage|tfidf]')
    try:
        if args[0] == "build":
            build_collection(engine=engine)
        elif args[0] == "query":
            if len(args) < 2:
                sys.exit('Usage: python retrieval.py query "your question" [--engine voyage|tfidf]')
            query(args[1], engine=engine)
        else:
            sys.exit("Unknown command. Use 'build' or 'query'.")
    except ValueError as e:
        # get_voyage_key() / query_chunks() now raise instead of sys.exit()
        # (needed so they're safely importable by the Streamlit front end)
        # — this preserves the original clean one-line CLI error behavior.
        sys.exit(str(e))
