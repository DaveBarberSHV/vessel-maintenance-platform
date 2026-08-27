# Vessel Maintenance Platform (Prototype)

An AI knowledge system for vessel engineering staff to get answers to
equipment questions from a vessel-specific document library (TMs,
generator/HVAC/fire/electrical/steering/hydraulics manuals, OEM service
bulletins, SMS procedures) — with precise citations back to the source
document, revision, and page.

**New to this project (including a fresh Claude session)? Read
`docs/working_with_dave.md` first** for how to collaborate effectively,
then `docs/architecture.md` for the current technical state.

## Status

Live and in real use. Single vessel combo (110' tug + 450' LNG bunkering
barge, Master Boat Builders Hull 469 / Order 7396A1). Deployed to
Streamlit Community Cloud, used daily by Dave and Jared, with real
crew rollout planned. ~30 TMs ingested so far, growing regularly as
Jared uploads more. See `docs/architecture.md` for the current, actively
maintained architecture diagram — this section intentionally stays brief
since that's the source of truth for technical state.

## Architecture (see `docs/architecture.md` for full current detail)

- **Ingestion pipeline** (`ingestion/scan_folder.py`): parse PDF → chunk
  by page → tag with metadata → generate embeddings (Voyage AI) → store
  in Supabase Postgres (`pgvector`). Also renders page images to Supabase
  Storage, and extracts a structured vessel equipment registry from
  reference documents.
- **Query-time flow:** engineer asks a question in the Streamlit app →
  vector search against indexed chunks + the vessel equipment registry →
  Claude synthesizes a concise, cited answer, with page images available
  on request.
- **Vector store:** Supabase Postgres + `pgvector` (migrated from local
  Chroma — see `BACKLOG.md` for why).
- **Citation granularity:** document + revision + page number (confirmed
  sufficient by real user feedback — section-level citations deliberately
  not pursued, see `BACKLOG.md`).

## Repo layout

```
ingestion/          parse -> chunk -> tag -> embed -> store pipeline
docs/               architecture, working notes, project brief
app.py              Streamlit front end
db.py               chat history persistence (Supabase Postgres)
BACKLOG.md          deferred items, real bugs found/fixed, and why
```

## Open items

See `BACKLOG.md` for deferred engineering decisions and the real story
behind bugs found and fixed, `docs/architecture.md` for the current
(actively maintained) architecture diagram, `docs/tm_upload_checklist.md`
for what makes a good file to add to the TM library, and
`docs/working_with_dave.md` for how to collaborate effectively on this
project.
