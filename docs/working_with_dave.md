# Working With Dave — Collaboration Notes

This file exists for one reason: if a conversation with Claude ends
(length limit, new session, anything) and a fresh chat picks this project
back up, it should read this file first — before touching any code — to
calibrate *how* to work with Dave, not just *what* the project is. The
technical state of the project itself is well-documented elsewhere
(`docs/architecture.md`, `BACKLOG.md`) — this file is specifically about
communication style, since that part doesn't show up anywhere else.

## Dave's background

Self-taught into this project from close to zero prior coding/git
experience, over about 8 days of part-time work (as of Aug 2026). Now
genuinely comfortable with the basic git loop (`status`, `diff`, `add`,
`commit`, `push`) and running Python scripts with real arguments/exports
— but still very much appreciates **exact, copy-pasteable commands**
rather than general instructions like "update the file" or "install the
dependency." Don't assume prior familiarity with a tool just because it
came up once before — a quick reminder of the exact syntax is always
welcome, not condescending.

## How to hand off files

Claude cannot write directly to Dave's machine — every code change goes
through: Claude creates/edits the file → `present_files` shares it →
Dave clicks it, confirms it landed in Downloads (`ls -la ~/Downloads | grep <name>`) →
Dave moves it into place with `mv`. **Do this one file at a time when
multiple files changed**, not all at once — confirm each one landed
before sharing the next. This has caused real, wasted round-trips before
when skipped (a file got missed in the shuffle and Dave spent a while
debugging a "bug" that was actually just an unmoved file).

## Before committing anything

Always have Dave run `git diff` (or `git diff <file>`) and paste the output
back **before** committing — review it together, then commit. Don't skip
this even for small changes; it's caught real mistakes (accidental content
loss during an edit, a stale value that shouldn't have been there).

## Real environment quirks worth knowing immediately

- **Mac, zsh.** Plain `python`, `pip` don't exist as commands — always use
  `python3.14` and `python3.14 -m pip install ...` (he installed Python
  3.14 directly from python.org, not a system default).
- **Editing files with real secrets** (`.streamlit/secrets.toml`): use the
  heredoc pattern (`cat > file << 'EOF' ... EOF`), not `nano` — nano has
  gotten him stuck (unfamiliar save/exit keys) more than once.
- **Never have him paste a real API key/secret value into chat.** This
  has happened accidentally more than once this project (a Voyage key, a
  Supabase `service_role` key) — always use `export VAR="..."` run
  directly in his terminal, values never typed into the conversation.
  If it happens anyway, flag it clearly and get it rotated.
- **Google Drive path** for TMs has a long, easy-to-mistype
  `CloudStorage/GoogleDrive-...` prefix — when giving a path-based
  command, use the full real path from a recent successful command rather
  than reconstructing it from memory, or double check the exact folder
  structure with `ls` first if unsure (the folder structure itself has
  changed at least once — subfolders were added by system).

## General working pattern that's worked well

1. Build/fix something, test it as thoroughly as possible in sandbox
   before ever handing it to Dave (mocked tests, real data when
   available, real files when possible).
2. Hand off with exact commands, one file at a time if multiple changed.
3. Have Dave run the real thing and paste real output — don't assume
   success, verify it.
4. When something breaks, diagnose with real evidence (query the actual
   database, check actual logs) rather than guessing serially through
   theories — this project has hit a few multi-theory debugging sessions
   (a redeploy issue, a stuck Postgres transaction) that got resolved
   faster once real diagnostic tooling was built rather than guessed at.
5. Keep `BACKLOG.md` and `docs/architecture.md` current as things land —
   this has been done consistently and is exactly why a fresh session can
   pick up context quickly.

## Tone

Warm, patient, plain language over jargon. Dave is proud of what's been
built here (rightfully) — celebrate real wins when they land, be honest
and calm about real problems, and don't over-apologize when something
(often on Claude's end — a missed file, a bug) needs fixing. Own it,
fix it, move on.
