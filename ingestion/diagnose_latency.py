"""
One-off diagnostic: times each real step in a query separately, so we can
tell "cold Supabase project waking up" apart from "the vector search
itself is slow" — two different problems needing different fixes.

Run twice in a row if the first run looks slow — a second run right after
tells us whether it was a one-time wake-up cost (second run fast) or a
persistent problem (second run still slow).

Usage:
    export VOYAGE_API_KEY="..."
    export SUPABASE_DB_URL="..."
    python3.14 diagnose_latency.py
"""

import time

import psycopg2.extras
from retrieval import PG_TABLE, VoyageEmbedder, _vec_literal, get_pg_connection, get_voyage_key


def timed(label, fn):
    t0 = time.time()
    result = fn()
    elapsed = time.time() - t0
    print(f"{label}: {elapsed:.2f}s")
    return result, elapsed


def _run(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def _vector_search(conn, vec, top_k=10):
    """top_k matches answer_query.get_answer()'s real default, so this times
    what production actually does — not a lighter query that looks faster."""
    v = _vec_literal(vec)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT text FROM {PG_TABLE} ORDER BY embedding <=> %s::vector LIMIT %s",
            (v, top_k),
        )
        return cur.fetchall()


print("=== Latency diagnostic ===\n")

conn, _ = timed("1. Open Postgres connection (first time this run)", get_pg_connection)

count_result, _ = timed(
    "2. Simple query — row count (baseline DB responsiveness, no vector math)",
    lambda: _run(conn, f"SELECT COUNT(*) FROM {PG_TABLE}"),
)

embedder = VoyageEmbedder(get_voyage_key(), input_type="query")
vec, _ = timed(
    "3. Voyage API call — embed the query text",
    lambda: embedder(["what is the operating temperature range?"])[0],
)

_, _ = timed(
    "4. Vector similarity search (the actual retrieval query, no index)",
    lambda: _vector_search(conn, vec),
)

print()
print("--- Repeating connection + search once more (steady-state check) ---")
conn2, _ = timed("5. Open a SECOND Postgres connection", get_pg_connection)
_, _ = timed("6. Vector search again (should be fast if step 1/4 was just a cold start)",
             lambda: _vector_search(conn2, vec))

conn.close()
conn2.close()

print("\n=== How to read this ===")
print("- If step 1 is slow but step 5 is fast: the Supabase project was cold, now it's warm.")
print("- If step 4 AND step 6 are both slow: the vector search itself needs a real index,")
print("  not just a warm connection — see BACKLOG.md.")
print("- If step 3 is the slow one: that's Voyage's API, not Supabase at all.")
