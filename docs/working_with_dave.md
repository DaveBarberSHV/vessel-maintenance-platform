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

## File access — depends which Claude interface this is

**Updated Sept 2026:** Dave now primarily works in **Claude Code** (the CLI,
running directly in his own terminal), not Claude Chat. This is a real,
important difference, not a cosmetic one: Claude Code has direct Read/Edit/
Write access to files on Dave's actual machine, and a real Bash tool that
executes commands directly in his real environment (same filesystem, same
git repo, same running processes) — there is no copy/paste or file hand-off
step at all. Edit or write the file directly, then follow the "before
committing anything" rule below.

The paragraph that used to be here (Claude creates a file → `present_files`
→ Dave downloads it → Dave moves it into place with `mv`) described the
**old Claude Chat workflow**, where Claude had no execution access at all.
That workflow no longer applies to Claude Code sessions. If a fresh session
somehow turns out to be Claude Chat again (no Bash/file tools available),
fall back to that hand-off pattern — but Claude Code is the real, current
default as of Sept 2026.

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

## Credential handling — critical rule

**Never ask Dave to paste Terminal output that might contain credentials.**
This has caused real pain — the `SUPABASE_DB_URL` was accidentally exposed
in chat multiple times because Terminal output grabbed more than intended,
the font is tiny, and it's easy to miss a credential buried in a long
command response.

**The rules:**
- Never ask Dave to `echo $SUPABASE_DB_URL` or any other credential
- Never ask Dave to paste `cat ~/.streamlit/secrets.toml`
- If a credential appears in chat, flag it immediately and prompt a
  rotation — don't let it slide even for one more message
- Verification commands that don't expose credentials are fine:
  `python3.14 list_engineer_notes.py` — shows data, not the key itself

**The reason this keeps happening:** Dave needs to copy/paste Terminal
output to share results, the Terminal font is very small, multi-line
output is hard to review quickly, and credentials get buried in export
commands that look like innocuous setup steps. Claude needs to be the
guard here, not Dave.

**Real incident, Claude Code specifically (Sept 14, 2026):** the old
`export SUPABASE_DB_URL="..."` placeholder pattern above was written for
Claude Chat, where Dave had to type the real command back to Claude for
Claude to "see" it ran — which meant the real value passed through the
chat transcript every time. Under Claude Code this is both unnecessary and
actively dangerous: Claude's Bash tool runs directly in Dave's real
environment, so once credentials are in the process environment, Claude
can already use them in every command with no export step from Dave at
all. Asking Dave to type an `export VAR="real-value"` line through
Claude Code (even via the `!` prefix) puts the real value in the
conversation transcript exactly like pasting it — confirmed the hard way
this session, when all three of `SUPABASE_DB_URL`, `ANTHROPIC_API_KEY`,
and `VOYAGE_API_KEY` ended up in chat this way.

**The fix, going forward — a one-time setup, never repeated:**
1. Dave creates a dedicated file (`~/.fathom_env`, outside the repo)
   containing the real `export VAR="..."` lines — typed directly in
   an ordinary Terminal window that has no connection to Claude Code at
   all, never through the `!` prefix or any command Claude runs.
2. `chmod 600 ~/.fathom_env` so only Dave can read it.
3. Before starting (or resuming) a Claude Code session for this project,
   Dave runs `source ~/.fathom_env && claude` (or `claude --resume`) from
   that same terminal — the new session's process inherits the
   credentials automatically.
4. From then on, Claude verifies credentials are present with
   `printenv VAR_NAME > /dev/null && echo set` (reports set/missing only,
   never the value) — Claude should never again construct a command that
   requires Dave to type a real secret value anywhere Claude can see it,
   including through `!`.

**Real recurring problem, fixed for good (Sept 2026):** `~/.fathom_env`
originally held its own separate, hardcoded copy of each `export VAR="..."`
line — a second credential store alongside the one Dave already
maintains at `~/.streamlit/secrets.toml` (a *global*, home-directory
Streamlit secrets file, distinct from this repo's own
`.streamlit/secrets.toml`). The two drifted out of sync more than once —
most concretely, `~/.fathom_env` was missing `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY` entirely, which silently disabled page-image
uploads during a real ingestion run without any obvious error at the
time. `~/.fathom_env` now contains no hardcoded values at all — just a
small loader that reads `~/.streamlit/secrets.toml` fresh every time it's
sourced (see the file itself for the exact loader). Dave only has one
real place to update a key going forward (`~/.streamlit/secrets.toml`,
which he already reliably keeps current, e.g. rotating the Anthropic key
there) — `~/.fathom_env` can't go stale on its own since it has nothing
of its own to go stale.

**Why this couldn't just be typed "right here, right now" mid-session:**
environment variables are inherited by a process only at the moment it
starts — exporting them in a different terminal window, or in the same
window after Claude Code already launched, doesn't reach an
already-running session. The session has to be (re)started *after*
`~/.fathom_env` is sourced for this to work.

**Update, Sept 22 2026 — step 3 is now automatic.** `~/.zshrc` sources
`~/.fathom_env` itself on every new shell, so Dave no longer has to
remember to run `source ~/.fathom_env` before starting Claude Code —
every new terminal and every new Claude Code session already has the
credentials. This does **not** reintroduce the drift problem from the
section above: `~/.zshrc` contains only the one `source ~/.fathom_env`
line, no hardcoded values — `~/.streamlit/secrets.toml` stays the only
file a real value is ever typed into.

**To be clear, this whole section is about Claude Code, not Claude
Chat.** The "old pattern" referenced above was written for Claude Chat
(no direct shell access, so Dave had to paste terminal output back for
Claude to "see" a command ran). Claude Code is the opposite case — it
runs commands directly in Dave's real environment, which is exactly why
the transcript-leak risk is real here and the guard stays in place even
though loading is now automatic. This project's entire development
workflow (all edits, commits, and anything touching the repo) happens
through Claude Code, so this section governs the normal case, not an
edge case.

**What this section doesn't cover — the live app.** Everything above is
about credentials in Dave's local shell / Claude Code sessions. The
deployed app at polaris.fathomvessel.com reads its 3 keys
(`VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `SUPABASE_DB_URL`) from Google
Secret Manager, set up by `deploy/setup_cloud_run.sh` — a completely
separate store from `~/.streamlit/secrets.toml`. Rotating a key locally
does **not** update the live app; re-run `deploy/setup_cloud_run.sh`
(after updating `~/.streamlit/secrets.toml` and sourcing
`~/.fathom_env`) to push the new value there too and redeploy.

GitHub Actions secrets (`WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`) are a
separate, unrelated pair — they authenticate the CI/CD deploy pipeline
to GCP and aren't part of the app's credential story at all. Don't tell
Dave to update a GitHub Actions secret when an API key rotates.

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
