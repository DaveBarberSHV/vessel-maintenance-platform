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
    submitted its entire chunk list as one unbatched request. See
    BACKLOG.md.

    Batches by a hard CHARACTER limit (MAX_CHARS_PER_BATCH), not an
    estimated token count — two earlier attempts at chars-per-token
    heuristics (4, then 3) both still let real batches exceed Voyage's
    320,000-token limit for this library's dense table content (measured
    real ratio: ~1.8 chars/token for a parts-list document, far denser
    than normal prose). Rather than guess a third ratio, MAX_CHARS_PER_BATCH
    is set directly from that worst real ratio observed so far, with
    margin — exact, not estimated, so no further guessing is needed as
    long as future content isn't denser than what's already been seen.
    Any single chunk over this limit is flagged as oversized rather than
    attempted (see oversized_indices) — sending it alone wouldn't be safe
    either, at this same density."""

    MAX_ITEMS_PER_BATCH = 1000  # Voyage's hard limit
    MAX_CHARS_PER_BATCH = 250_000  # ~139,000 tokens even at the densest ratio seen so far (~1.8 chars/token) — see docstring

    def __init__(self, api_key: str, input_type: str = "document"):
        import voyageai
        self.client = voyageai.Client(api_key=api_key)
        self.input_type = input_type
        self.oversized_indices: list[int] = []  # populated during __call__, read by callers that need to know

    def __call__(self, input: Documents) -> Embeddings:
        self.oversized_indices = []
        embeddable, embeddable_indices = [], []
        for i, text in enumerate(input):
            if len(text) > self.MAX_CHARS_PER_BATCH:
                self.oversized_indices.append(i)
            else:
                embeddable.append(text)
                embeddable_indices.append(i)

        batches = list(self._batches(embeddable))
        computed = []
        for i, batch in enumerate(batches):
            result = self.client.embed(batch, model=VOYAGE_MODEL, input_type=self.input_type)
            computed.extend(result.embeddings)
            if i < len(batches) - 1:
                time.sleep(1)  # brief, polite pause only when there's more than one batch

        # Reassemble in original order. Oversized chunks get a zero-vector
        # placeholder — Chroma's collection.add() requires one embedding
        # per input item, so this can't just be omitted; callers should
        # filter oversized_indices out of ids/documents/metadatas before
        # calling add() to avoid actually storing a meaningless zero
        # vector. (scan_folder.py does this — see its use of this class.)
        dim = len(computed[0]) if computed else 1024
        all_embeddings = [[0.0] * dim] * len(input)
        for idx, emb in zip(embeddable_indices, computed):
            all_embeddings[idx] = emb
        return all_embeddings

    def _batches(self, texts):
        batch, batch_chars = [], 0
        for text in texts:
            if batch and (len(batch) >= self.MAX_ITEMS_PER_BATCH
                          or batch_chars + len(text) > self.MAX_CHARS_PER_BATCH):
                yield batch
                batch, batch_chars = [], 0
            batch.append(text)
            batch_chars += len(text)
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

        # Compute embeddings ourselves (rather than letting collection.add()
        # trigger it) so any chunk VoyageEmbedder flags as too large to
        # embed at all (oversized_indices — see its docstring) can be
        # filtered out before storing anything. Same fix as scan_folder.py;
        # see BACKLOG.md.
        texts = [c["text"] for c in text_chunks]
        embeddings = embedder(texts)
        oversized = set(embedder.oversized_indices)
        keep = [i for i in range(len(text_chunks)) if i not in oversized]

        collection.add(
            ids=[text_chunks[i]["chunk_id"] for i in keep],
            documents=[text_chunks[i]["text"] for i in keep],
            embeddings=[embeddings[i] for i in keep],
            metadatas=[{
                "document_title": text_chunks[i]["document_title"],
                "revision": text_chunks[i]["revision"],
                "page_number": text_chunks[i]["page_number"],
                "equipment_model": text_chunks[i]["equipment_model"],
                "document_type": text_chunks[i]["document_type"],
                "source_file": text_chunks[i]["source_file"],
            } for i in keep],
        )
        if oversized:
            print(f"WARNING: {len(oversized)} chunk(s) across the library were too "
                  f"large to embed and were NOT added to the index — see per-file "
                  f"output above for which pages.")
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
