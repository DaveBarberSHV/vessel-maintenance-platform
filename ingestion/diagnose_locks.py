"""
One-off diagnostic: asks Postgres directly what every connection to this
database is currently doing — specifically looking for a connection stuck
"idle in transaction" (e.g. from an earlier session killed with Ctrl+C
before it could commit/close cleanly) that might be holding a lock and
silently blocking new connections from completing their own setup steps.

Usage:
    export SUPABASE_DB_URL="..."
    python3.14 diagnose_locks.py
"""

from retrieval import get_pg_connection

conn = get_pg_connection()
conn.autocommit = True  # this diagnostic must never itself become a stuck transaction

with conn.cursor() as cur:
    cur.execute("""
        SELECT pid, state, wait_event_type, wait_event,
               now() - query_start AS running_for,
               now() - state_change AS in_this_state_for,
               left(query, 150) AS query
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid != pg_backend_pid()
        ORDER BY query_start ASC NULLS LAST
    """)
    rows = cur.fetchall()

print(f"=== {len(rows)} other connection(s) to this database ===\n")
if not rows:
    print("No other connections found — the hang isn't caused by a blocking "
          "connection at the database level. Something else is going on.")
else:
    stuck_found = False
    for pid, state, wait_type, wait_event, running_for, in_state_for, query in rows:
        print(f"PID {pid} | state: {state} | wait: {wait_type}/{wait_event}")
        print(f"  running for: {running_for} | in this state for: {in_state_for}")
        print(f"  query: {query}")
        print()
        if state == "idle in transaction":
            stuck_found = True

    if stuck_found:
        print("^^^ FOUND IT: at least one connection is 'idle in transaction' — "
              "this is very likely holding a lock and blocking new connections "
              "from completing setup. See instructions for how to clear it.")
    else:
        print("No 'idle in transaction' connections found. If the app is still "
              "hanging, the cause is something other than a stuck transaction.")

conn.close()
