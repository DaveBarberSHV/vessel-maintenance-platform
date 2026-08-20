# Vessel Maintenance Platform (Prototype)

An AI knowledge system for vessel engineering staff to get answers to
equipment questions from a vessel-specific document library (TMs,
generator/HVAC/fire/electrical/steering/hydraulics manuals, OEM service
bulletins, SMS procedures) — with precise citations back to the source
document, revision, and page.

## Status

Prototype phase. Single vessel combo (110' tug + 450' LNG bunkering barge,
Master Boat Builders Hull 469 / Order 7396A1). ~20–30 TMs expected; three
loaded so far as test corpus.

## Architecture (decided so far)

- **Document flow:** TMs live in a shared document store (Google Drive
  assumed; no live connector yet — manual handling for prototype).
- **Ingestion pipeline** (`ingestion/parse_and_chunk.py`): parse PDF → chunk
  by page → tag with metadata (vessel, equipment model, document type,
  revision, page number) → generate embeddings → store with metadata.
- **Query-time flow:** engineer asks a question in the app → backend runs a
  vector search against indexed chunks → backend sends question + retrieved
  chunks to the Claude API → Claude drafts a concise, cited answer → app
  displays it. The engineer's browser only ever talks to the app/backend,
  never directly to Claude or the vector store.
- **Vector store:** embedded Chroma inside the backend service (no separate
  hosting) — sufficient at current scale (tens of thousands of chunks).
- **Citation granularity:** document + revision + page number (see
  `BACKLOG.md` for the path to section-level citations later).

## Repo layout

```
ingestion/          parse -> chunk -> tag pipeline
docs/               project brief, design notes
BACKLOG.md           deferred items and why
```

## Open items

See `BACKLOG.md` for deferred engineering decisions, and the project brief
in `docs/` for people/roles and full scope.
