"""
Retrieval layer: store TM chunks, embed and query them, return
citation-ready results.

Two embedding engines are supported, selected with --engine:
  voyage  (default) — real semantic embeddings via Voyage AI. Requires
           VOYAGE_API_KEY. This is the production path. Storage: Supabase
           Postgres + pgvector (migrated Aug 2026 from a local Chroma
           database that had to be committed to git for the deployed app
           to reach it — that stopgap strained badly as the library grew
           past 20 documents; see BACKLOG.md for the full story).
  tfidf  — classic keyword-overlap similarity, no API key or network
           needed. Kept around for offline testing only; known to miss
           matches that use different wording than the source ("won't
           start" vs "fails to crank") — see BACKLOG.md. Storage: local
           Chroma, unchanged — this engine is never deployed, so it never
           had the "reachable by a hosted app" problem the voyage engine
           had.

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
import psycopg2
import psycopg2.extras

CHUNKS_PATH = Path(__file__).parent / "chunks.jsonl"
DB_ROOT = Path(__file__).parent / "chroma_db"  # tfidf engine only now — see module docstring
COLLECTION_NAME = "vessel_tms"
VOYAGE_MODEL = "voyage-3"  # verify still current at https://docs.voyageai.com/docs/embeddings
EMBEDDING_DIM = 1024  # voyage-3's real output dimension — verified against actual stored data, Aug 2026
PG_TABLE = "tm_chunks"


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
    a real failure: a single unbatched call submitted an entire document's
    chunk list at once. See BACKLOG.md.

    Batches by a hard CHARACTER limit (MAX_CHARS_PER_BATCH), not an
    estimated token count — two earlier attempts at chars-per-token
    heuristics (4, then 3) both still let real batches exceed Voyage's
    320,000-token limit for this library's dense table content (measured
    real ratio: ~1.8 chars/token for a parts-list document, far denser
    than normal prose). MAX_CHARS_PER_BATCH is set directly from that
    worst real ratio observed, with margin — exact, not estimated.
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

        dim = len(computed[0]) if computed else EMBEDDING_DIM
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
    """tfidf engine only now — voyage moved to Postgres. See module docstring."""
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


def get_pg_url() -> str:
    key = os.environ.get("SUPABASE_DB_URL")
    if not key:
        raise ValueError(
            "No SUPABASE_DB_URL available. Set the SUPABASE_DB_URL "
            "environment variable — same value the Streamlit app uses "
            "(from its .streamlit/secrets.toml), the Supabase 'Session "
            "pooler' connection string. See docs/architecture.md."
        )
    return key


def get_pg_connection():
    return psycopg2.connect(get_pg_url())


def ensure_pg_schema(conn):
    """CREATE EXTENSION / CREATE TABLE IF NOT EXISTS — safe to call every
    time. Mirrors the pattern already used for chat history in db.py."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {PG_TABLE} (
                chunk_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                embedding vector({EMBEDDING_DIM}) NOT NULL,
                document_title TEXT NOT NULL,
                revision TEXT NOT NULL,
                page_number INT NOT NULL,
                total_pages INT,
                equipment_model TEXT NOT NULL,
                document_type TEXT NOT NULL,
                source_file TEXT NOT NULL
            );
        """)
        # Added Aug 2026 for the page-images feature — see page_images.py.
        # Nullable: most chunks won't have one (only pages selected by
        # should_render_page() do), and existing chunks from before this
        # feature don't have it at all yet.
        cur.execute(f"""
            ALTER TABLE {PG_TABLE}
                ADD COLUMN IF NOT EXISTS page_image_url TEXT;
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{PG_TABLE}_source_file
                ON {PG_TABLE} (source_file);
        """)
        # No approximate-nearest-neighbor index (HNSW/IVFFlat) yet —
        # deliberately: at this library's scale (thousands, not millions,
        # of chunks) a plain sequential scan ordered by exact distance is
        # simple, correct, and fast enough. Revisit only if query latency
        # actually becomes a real problem — premature indexing here would
        # trade simplicity for a speed nobody's asked for yet.
    conn.commit()


def _vec_literal(embedding: list) -> str:
    """pgvector's text input format: '[0.1,0.2,...]'. Formatting this
    ourselves (rather than adding the separate pgvector-python package
    just for its adapter) keeps this module's dependency list smaller."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


def upsert_chunks(conn, chunks: list[dict], embeddings: list[list[float]]):
    """Bulk insert-or-update by chunk_id — safe to call repeatedly (e.g. a
    full rebuild, or scan_folder.py re-adding a changed file's chunks)."""
    rows = [
        (
            c["chunk_id"], c["text"], _vec_literal(emb),
            c["document_title"], c["revision"], c["page_number"],
            c.get("total_pages"), c["equipment_model"], c["document_type"],
            c["source_file"], c.get("page_image_url"),
        )
        for c, emb in zip(chunks, embeddings)
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO {PG_TABLE}
                (chunk_id, text, embedding, document_title, revision,
                 page_number, total_pages, equipment_model, document_type,
                 source_file, page_image_url)
            VALUES %s
            ON CONFLICT (chunk_id) DO UPDATE SET
                text = EXCLUDED.text,
                embedding = EXCLUDED.embedding,
                document_title = EXCLUDED.document_title,
                revision = EXCLUDED.revision,
                page_number = EXCLUDED.page_number,
                total_pages = EXCLUDED.total_pages,
                equipment_model = EXCLUDED.equipment_model,
                document_type = EXCLUDED.document_type,
                source_file = EXCLUDED.source_file,
                page_image_url = EXCLUDED.page_image_url
            """,
            rows,
            template="(%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s)",
        )
    conn.commit()


def delete_chunks(conn, chunk_ids: list[str]):
    if not chunk_ids:
        return
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {PG_TABLE} WHERE chunk_id = ANY(%s)", (chunk_ids,))
    conn.commit()


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

    if engine == "voyage":
        api_key = get_voyage_key()
        embedder = VoyageEmbedder(api_key, input_type="document")

        # Compute embeddings ourselves (rather than inside the insert
        # call) so any chunk VoyageEmbedder flags as too large to embed at
        # all (oversized_indices — see its docstring) can be filtered out
        # before storing anything.
        texts = [c["text"] for c in text_chunks]
        embeddings = embedder(texts)
        oversized = set(embedder.oversized_indices)
        keep = [i for i in range(len(text_chunks)) if i not in oversized]

        conn = get_pg_connection()
        ensure_pg_schema(conn)
        upsert_chunks(conn, [text_chunks[i] for i in keep], [embeddings[i] for i in keep])
        conn.close()

        if oversized:
            print(f"WARNING: {len(oversized)} chunk(s) across the library were too "
                  f"large to embed and were NOT added to the index — see per-file "
                  f"output above for which pages.")
        print(f"Embedded {len(keep)} chunks into Postgres.")

    elif engine == "tfidf":
        from sklearn.feature_extraction.text import TfidfVectorizer
        import pickle
        path = db_path(engine)
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(path))
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
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
                "total_pages": c.get("total_pages"),
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


def query_chunks(question: str, engine: str = "voyage", top_k: int = 5) -> list[dict]:
    """Core retrieval function: returns a list of {text, metadata, distance}
    dicts for the top_k most relevant chunks. Used by both the CLI query
    command below and by answer_query.py to build a Claude prompt."""
    if engine == "voyage":
        api_key = get_voyage_key()
        embedder = VoyageEmbedder(api_key, input_type="query")  # 'query' not 'document' at search time
        query_embedding = embedder([question])[0]

        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            vec = _vec_literal(query_embedding)
            cur.execute(
                f"""
                SELECT text, document_title, revision, page_number, total_pages,
                       equipment_model, document_type, source_file, page_image_url,
                       embedding <=> %s::vector AS distance
                FROM {PG_TABLE}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec, vec, top_k),
            )
            rows = cur.fetchall()
        conn.close()

        return [
            {
                "text": r["text"],
                "metadata": {
                    "document_title": r["document_title"],
                    "revision": r["revision"],
                    "page_number": r["page_number"],
                    "total_pages": r["total_pages"],
                    "equipment_model": r["equipment_model"],
                    "document_type": r["document_type"],
                    "source_file": r["source_file"],
                    "page_image_url": r["page_image_url"],
                },
                "distance": r["distance"],
            }
            for r in rows
        ]

    elif engine == "tfidf":
        path = db_path(engine)
        import pickle
        with open(path / "vectorizer.pkl", "rb") as f:
            vectorizer = pickle.load(f)
        embedder = TfidfEmbedder(vectorizer)
        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_collection(COLLECTION_NAME, embedding_function=embedder)
        results = collection.query(query_texts=[question], n_results=top_k)
        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0], results["metadatas"][0], results["distances"][0]
            )
        ]

    else:
        # Same reasoning as get_voyage_key() above — this function is
        # imported directly by the front end, so no sys.exit() here.
        raise ValueError(f"Unknown engine '{engine}'. Use 'voyage' or 'tfidf'.")


def query(question: str, engine: str = "voyage", top_k: int = 5):
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
        # get_voyage_key() / get_pg_url() / query_chunks() now raise
        # instead of sys.exit() (needed so they're safely importable by
        # the Streamlit front end) — this preserves the original clean
        # one-line CLI error behavior.
        sys.exit(str(e))
