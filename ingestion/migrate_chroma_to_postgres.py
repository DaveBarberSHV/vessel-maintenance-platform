"""
One-time migration: copies all existing chunks + embeddings from the local
Chroma database directly into the new Supabase Postgres/pgvector table.

Why this exists: rather than re-embedding the whole library (real Voyage
API cost, real time), the vectors already computed and sitting in the
committed chroma_db/voyage/ database are read out directly and written
into Postgres as-is. No Voyage API calls happen here at all.

Run this ONCE, after retrieval.py and scan_folder.py have been updated to
use Postgres — see BACKLOG.md for why this migration is happening.

Usage:
    export SUPABASE_DB_URL="..."
    python migrate_chroma_to_postgres.py
"""

import sys
from pathlib import Path

import chromadb

sys.path.insert(0, str(Path(__file__).parent))
from retrieval import get_pg_connection, ensure_pg_schema, upsert_chunks

CHROMA_PATH = Path(__file__).parent / "chroma_db" / "voyage"
COLLECTION_NAME = "vessel_tms"


def migrate():
    if not CHROMA_PATH.exists():
        sys.exit(f"No local Chroma database found at {CHROMA_PATH} — nothing to migrate.")

    print(f"Reading existing chunks from {CHROMA_PATH} ...")
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_collection(COLLECTION_NAME)
    data = collection.get(include=["embeddings", "documents", "metadatas"])

    ids = data["ids"]
    documents = data["documents"]
    embeddings = data["embeddings"]
    metadatas = data["metadatas"]
    print(f"Found {len(ids)} chunks with embeddings in the local Chroma database.")

    chunks = []
    for chunk_id, text, meta in zip(ids, documents, metadatas):
        chunks.append({
            "chunk_id": chunk_id,
            "text": text,
            "document_title": meta["document_title"],
            "revision": meta["revision"],
            "page_number": meta["page_number"],
            "total_pages": meta.get("total_pages"),  # may be missing for older chunks — fine, nullable column
            "equipment_model": meta["equipment_model"],
            "document_type": meta["document_type"],
            "source_file": meta["source_file"],
        })

    print("Connecting to Supabase Postgres ...")
    conn = get_pg_connection()
    ensure_pg_schema(conn)

    print(f"Writing {len(chunks)} chunks to Postgres (no Voyage API calls — using the "
          f"embeddings already computed) ...")
    upsert_chunks(conn, chunks, embeddings)
    conn.close()

    print(f"\nDone. {len(chunks)} chunks migrated to Postgres.")
    print("Once you've confirmed this worked (see the verification query printed by "
          "the calling instructions), the local chroma_db/voyage/ files committed to "
          "git are no longer needed for the voyage engine and can eventually be "
          "removed from the repo — see BACKLOG.md.")


if __name__ == "__main__":
    migrate()
