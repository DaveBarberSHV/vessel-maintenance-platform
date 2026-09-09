# Fathom – Polaris: Claude Code Context

Read this at the start of every session. Then read `docs/architecture.md`
and `BACKLOG.md` for full technical state before touching any code.

---

## What this project is

A RAG-based vessel maintenance assistant for the M/V Polaris (110' tug).
Engineers ask questions in a Streamlit chat app and get cited answers with
page images from the vessel's own technical manuals. Live and in real use.

**Stack:** Google Drive → parse/chunk → Voyage AI embeddings → Supabase
Postgres + pgvector → Streamlit Cloud. Claude API generates answers.

**Repo:** https://github.com/DaveBarberSHV/vessel-maintenance-platform

---

## Working with Dave — critical rules

**Dave is not a developer by background.** He's learned git and Python
on this project. Always give exact, copy-pasteable commands. Never say
"update the file" — show the exact command.

**Python is `python3.14`** — not `python` or `python3`. Always use
`python3.14` for all Python commands.

**Always run `git diff` before committing** — show Dave the diff and
review it together. No exceptions, even for small changes.

**Credentials — the most important rule:**
- Never ask Dave to echo or display any credential in the terminal
- Never read `.streamlit/secrets.toml` and display its contents
- If a credential appears in output, flag it immediately and prompt rotation
- Credentials live in environment variables — check they're exported
  before running scripts that need them (`SUPABASE_DB_URL`,
  `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `SUPABASE_SERVICE_KEY`,
  `SUPABASE_URL`)

**Google Drive path** (exact, use this):
```
/Users/davebarber/Library/CloudStorage/GoogleDrive-dave.safeharbour@gmail.com/My Drive/Vessel Maintenance System Documents
```

---

## Current state (September 2026)

- **Library:** ~160 PDFs, ~20 systems, ~9,000+ chunks ingested
- **Deployed:** Streamlit Cloud, live and working
- **Jared** (Chief Engineer, M/V Polaris) is the primary user and
  test partner — approaching Port Engineer demo soon
- **Status:** prototype phase, single vessel, pre-fleet-scaling

---

## Top open items (from BACKLOG.md)

1. **Admin panel** — add/remove users, approve Engineer Notes, manage
   documents via web UI (required before fleet scaling)
2. **Multi-vessel architecture** — vessel_id isolation before vessel #2
3. **Chief Engineer approval step** in rename workflow
4. **Partial title match** retrieval boost (#6, #7, #12 thumbs-down pattern)
5. **Security audit log** — 30-day commitment from Sept 2026
6. **FILENAME_PATTERN** — allow hyphens in model names (e.g. 807B-828B)
7. **Page count gate** — warn before vision extraction on >50 image-only pages
8. **Ingestion checkpointing** — large docs should resume, not restart

---

## Key scripts (all run from repo root or ingestion/ directory)

```bash
# Full ingest pipeline (use this for new documents)
cd ingestion
python3.14 ingest_new_docs.py "/path/to/Drive/..."

# Direct scan (already-named files only)
python3.14 scan_folder.py "/path/to/Drive/..."

# Reprocess a specific file
python3.14 reprocess_file.py "ExactFileName.pdf"

# Reprocess all DWG files
python3.14 reprocess_all_dwg.py

# Review thumbs-down feedback
python3.14 review_feedback.py

# List Engineer Notes
python3.14 list_engineer_notes.py

# Delete specific Engineer Notes by ID
python3.14 delete_engineer_notes.py 1 2 3 --apply

# Clear all chat history (keeps Engineer Notes, chunks, registry)
python3.14 clear_test_data.py --apply

# Diagnose retrieval for a question
cd ingestion
python3.14 diagnose_retrieval.py "your question here"
```

---

## Architecture in one paragraph

Engineers access via browser → Streamlit app → backend queries Supabase
pgvector (vector search, top_k=10) + equipment registry + Engineer Notes
→ Claude API generates cited answer → rendered as: Engineer Notes (before
answer) → Answer → SHOW_DOCUMENT images (if "show me" language detected)
→ Safety Information (collapsed) → Sources with page images (capped at 10).
Ingestion: PDFs in Drive → scan_folder.py → pdfplumber extracts text →
vision extraction for image-only pages (tiled at 300 DPI for large-format
drawings) → Voyage AI embeddings → Supabase chunks + Storage page images.

---

## Security standing rules

- RLS enabled on all database tables — verify before any new table
- Four-question security review before any new table or integration
- See `docs/architecture.md` "Security-by-design" section for the full
  checklist and the real incidents that motivated it
