"""
Vessel Maintenance TM Assistant — Streamlit chat front end.

This is a thin UI layer only. All the real logic (retrieval, prompt
construction, calling Claude) lives in ingestion/answer_query.py and
ingestion/retrieval.py, unchanged — this app just imports and calls it.
Chat persistence lives in db.py (Supabase/Postgres). See
docs/architecture.md for the full plan this fits into.

Run locally:
    Create .streamlit/secrets.toml with VOYAGE_API_KEY, ANTHROPIC_API_KEY,
    and SUPABASE_DB_URL (see docs/architecture.md), or set them as plain
    environment variables instead — both work.
    pip install -r requirements.txt
    streamlit run app.py

"Auth" is intentionally minimal for now: a free-text name field in the
sidebar, not real accounts — appropriate for a couple of known users; see
docs/architecture.md for when this would need to become more.
"""

import os
import sys
from pathlib import Path

import streamlit as st

# ingestion/ isn't a proper installed package — it's a folder of scripts,
# same as the CLI tools use it. Resolve the path from this file's own
# location so it works regardless of what directory `streamlit run` is
# launched from.
sys.path.insert(0, str(Path(__file__).parent / "ingestion"))

for key_name in ("VOYAGE_API_KEY", "ANTHROPIC_API_KEY", "SUPABASE_DB_URL"):
    if key_name not in os.environ and key_name in st.secrets:
        os.environ[key_name] = st.secrets[key_name]

from answer_query import get_answer, format_sources  # noqa: E402
import db  # noqa: E402
import auth  # noqa: E402
import engineer_notes  # noqa: E402
from document_inventory import get_document_library  # noqa: E402

st.set_page_config(page_title="Fathom - Polaris", page_icon="⚓")

# Hide Streamlit's default developer-facing chrome (Aug 2026) — the
# hamburger menu (with its "Deploy" option) and the "Made with Streamlit"
# footer. These are aimed at Streamlit developers, not end users, and
# make an app look like an obvious dev/demo project rather than a real
# product — worth hiding now given the longer-term goal of this looking
# professional to an actual customer, not just Dave and Jared.
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stDeployButton {display: none;}

/* Removed (Aug 2026, real bug): [data-testid="stToolbar"] { visibility:
hidden; } — this broke the app on mobile. On a phone's narrow viewport,
Streamlit starts the sidebar COLLAPSED and relies entirely on a toggle
control inside this same toolbar to open it; hiding the whole toolbar
hid that control too. Never visible as a problem on desktop, since the
sidebar just starts open there — only surfaced once real people tried
this on their phones. Trades back a couple of small dev-facing icons
(GitHub/star/edit) for the app actually being usable on mobile, which
matters far more. A more surgical fix (hide just those icons, leave the
sidebar toggle alone) is a real possibility, but needs live browser
inspection to get right rather than another guess at selectors — not
worth risking a second version of this same bug. */

/* Brass/amber accent applied directly (Aug 2026) — Streamlit's built-in
theme primaryColor doesn't reach plain bordered buttons/borders the way
it does sliders/checkboxes, so this makes the accent actually visible
on the elements a user notices most: buttons and the active chat input
border. */
button, [data-testid="stSidebar"] button {
    border-color: #C08A28 !important;
}
button:hover, [data-testid="stSidebar"] button:hover {
    border-color: #E0A83D !important;
    color: #E0A83D !important;
}
[data-testid="stChatInput"] {
    border-color: #C08A28 !important;
}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_db_connection():
    """Cached so one connection is reused across reruns within the app's
    process, rather than opening a new one on every interaction.
    Simplification worth knowing: this is a single shared connection, fine
    for a couple of users, not a real connection pool — revisit if this
    ever needs to handle real concurrent load."""
    conn = db.get_connection()
    db.ensure_schema(conn)
    engineer_notes.ensure_notes_schema(conn)
    auth.ensure_users_schema(conn)
    return conn


def with_connection_retry(fn, *args, **kwargs):
    """Calls fn(conn, *args, **kwargs) using the shared cached connection;
    if that fails, retries ONCE with a freshly re-established connection
    before giving up. Added Aug 2026 after a real failure ("connection
    already closed") surfaced when adding an Engineer Note: a cached
    connection left idle for a while (e.g. time spent typing) can be
    silently dropped by Supabase's session pooler in the background, and
    the failure only shows up the next time the connection is actually
    used. This risk applies to every operation using the shared cached
    connection, not just the one feature that happened to surface it —
    hence one reusable wrapper used everywhere, not a fix in just one
    place."""
    global conn
    try:
        return fn(conn, *args, **kwargs)
    except Exception:
        get_db_connection.clear()
        conn = get_db_connection()
        return fn(conn, *args, **kwargs)


try:
    conn = get_db_connection()
    db_available = True
except Exception as e:
    conn = None
    db_available = False
    db_error = str(e)

st.title("⚓ Fathom - Polaris")
st.caption(
    "Answers are generated from Polaris's technical data with references listed."
)

if not db_available:
    st.warning(
        f"⚠️ Chat history isn't available right now ({db_error}). "
        "You can still ask questions — answers just won't be saved."
    )

# --- Real authentication (Aug 2026) — replaces the original free-text
# "Who's asking?" field. See auth.py for the full design rationale: the
# free-text version meant Engineer Notes' authorization
# (AUTHORIZED_NOTE_AUTHORS) was only ever checking a typed name, not a
# verified identity — anyone with the app URL could type "Jared" and
# submit a note carrying his real operational authority. No self-service
# signup, no password-reset flow — accounts are provisioned manually via
# manage_users.py, matching how AUTHORIZED_NOTE_AUTHORS is already a
# small, manually-maintained list.
if "user_name" not in st.session_state:
    st.session_state.user_name = None

if not st.session_state.user_name:
    st.info("👋 Please log in to continue.")
    with st.form("login_form"):
        username_input = st.text_input("Username")
        password_input = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log In")
        if submitted:
            if not db_available:
                st.error(f"Login isn't available right now ({db_error}).")
            elif not username_input.strip() or not password_input:
                st.warning("Please enter both a username and password.")
            else:
                try:
                    success, message = with_connection_retry(
                        auth.verify_credentials, username_input.strip(), password_input)
                    if success:
                        st.session_state.user_name = auth.normalize_username(username_input)
                        st.rerun()
                    else:
                        st.error(message)
                except Exception as e:
                    st.error(f"Couldn't verify login right now ({e}).")
    st.stop()

def render_field_notes(message: dict):
    """Engineer Notes referenced in forming this answer, rendered BEFORE
    the answer itself (Aug 2026, real request from Jared after a call
    with him and the Port Engineer): these notes carry real operational
    authority, so they need to be seen before the procedure, not
    discovered afterward. Expanded by default ("should appear in full"
    per Jared) but still collapsible if someone wants to hide it —
    deliberately different from Safety Information below, which stays
    collapsed by default so it doesn't slow someone down just getting
    their answer.

    Label uses 📝, matching the "+ Engineer Note" button (Aug 2026, real
    fix) — deliberately NOT ⚠️, which is reserved for Safety Information;
    reusing the warning triangle here blurred together two genuinely
    different kinds of information (real crew experience vs. a
    manufacturer safety callout)."""
    notes = message.get("field_notes_used")
    if not notes:
        return
    label = f"📝 Engineer Note{'s' if len(notes) > 1 else ''} ({len(notes)}) — tap to hide"
    with st.expander(label, expanded=True):
        for i, n in enumerate(notes):
            parts = [n["category"]]
            if n.get("position"):
                parts.append(n["position"])
            author_display = n["author"]
            if n.get("author_role"):
                author_display += f' ({n["author_role"]})'
            st.markdown(f'**{" ".join(parts)}** — {author_display}, {n.get("created_at", "")}')
            st.markdown(n["note_text"])
            if i < len(notes) - 1:
                st.divider()


def render_safety_info(message: dict):
    """Safety Information extracted from the source manual (Aug 2026,
    real request from Jared): TMs put WARNING/CAUTION callouts before a
    procedure; we don't want to slow someone down getting their answer,
    so this stays collapsed by default, right after the answer — opt-in,
    not always visible, unlike Field Notes above."""
    safety_info = message.get("safety_info")
    if not safety_info:
        return
    with st.expander("⚠️ Show Safety Information"):
        st.markdown(safety_info)


def render_show_document_images(message: dict):
    """Auto-surfaces the specific drawing/page a question explicitly
    asked to be SHOWN (Sept 2026, real request from Dave: "Can you show
    me the drawing of the Shaft Arrangement?" should return the drawing,
    not bury it in the collapsed View Sources section below).

    Deliberately renders directly, always-visible, right after the
    answer text — not in an expander like Safety Info or Sources —
    since the whole point is that this IS what the question asked for,
    not supplementary material someone has to opt into seeing. Only
    fires when Claude's own SHOW_DOCUMENT section identified genuine
    showing-language in the question itself (see answer_query.py's
    SYSTEM_PROMPT) — a normal question that's merely answered using a
    drawing still only shows that drawing in the normal Sources
    section below, unchanged."""
    images = message.get("show_document_images")
    if not images:
        return
    for img in images:
        total = img.get("total_pages")
        page_label = f'p. {img["page_number"]} of {total}' if total else f'p. {img["page_number"]}'
        st.markdown(f'**{img["document_title"]}, {page_label}**')
        st.image(img["url"])


def render_sources(chunks: list[dict] | None):
    """Combined citation list + page images (Aug 2026) — previously two
    separate things (a plain-text "Sources:" caption, and a separate
    "View page images" expander); merged into one "View Sources"
    treatment per Jared's request, to save space in the main answer.

    Always lists EVERY citation by name/revision/page, with an image
    shown inline underneath whenever one exists — deliberately never
    conditional on having an image, since not every citation has one
    (scanned documents with no text layer, or ingestion that ran without
    Storage credentials configured). The plain citation list has been a
    trust-building feature since day one of this project; this redesign
    must not cause any source to silently disappear just because it
    lacks an image."""
    if not chunks:
        return
    seen = set()
    entries = []
    for c in chunks:
        m = c["metadata"]
        key = (m["document_title"], m["revision"], m["page_number"])
        if key in seen:
            continue
        seen.add(key)
        entries.append(m)
    with st.expander(f"📚 View Sources ({len(entries)})"):
        for i, m in enumerate(entries):
            total = m.get("total_pages")
            page_label = f'p. {m["page_number"]} of {total}' if total else f'p. {m["page_number"]}'
            st.markdown(f'**{m["document_title"]}, {m["revision"]}, {page_label}**')
            url = m.get("page_image_url")
            if url:
                st.image(url)
            if i < len(entries) - 1:
                st.divider()


def render_assistant_message(message: dict, key_prefix: str):
    """Renders a complete assistant message in the agreed order (Aug
    2026, updated Sept 2026): Field Notes (before the answer, if any),
    the answer itself, any auto-surfaced "show me" image (Sept 2026 —
    directly after the answer, always visible, since it IS the thing
    asked for), Safety Information (collapsed, after), View Sources
    (collapsed, combining citations + images), then feedback and copy.
    Shared by both historical messages (loaded from the sidebar) and the
    live answer just generated, so they always look and behave
    identically. key_prefix must be unique per message (message id if
    saved, or the live index) since Streamlit widgets need stable,
    unique keys."""
    render_field_notes(message)
    st.markdown(message["content"])
    render_show_document_images(message)
    render_safety_info(message)
    render_sources(message.get("chunks"))

    chunks = message.get("chunks")
    message_id = message.get("id")
    if db_available and message_id is not None:
        current = message.get("feedback")
        col1, col2, _ = st.columns([1, 1, 10])
        with col1:
            if st.button("👍" if current != "up" else "✅👍", key=f"{key_prefix}_up"):
                new_value = None if current == "up" else "up"
                try:
                    with_connection_retry(db.set_feedback, message_id, new_value)
                    message["feedback"] = new_value
                    st.rerun()
                except Exception:
                    pass
        with col2:
            if st.button("👎" if current != "down" else "✅👎", key=f"{key_prefix}_down"):
                new_value = None if current == "down" else "down"
                try:
                    with_connection_retry(db.set_feedback, message_id, new_value)
                    message["feedback"] = new_value
                    st.rerun()
                except Exception:
                    pass

    with st.expander("📋 Copy"):
        st.caption("Answer only")
        st.code(message["content"], language=None)
        if chunks:
            st.caption("Answer with sources")
            st.code(message["content"] + "\n\n" + format_sources(chunks), language=None)


# Initialize session state before the sidebar runs — the Document Library
# panel (inside the sidebar) can fire a question into the chat, which
# requires messages and conversation_id to already exist.
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = db.new_conversation_id() if db_available else "local-only"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.write(f"Logged in as **{st.session_state.user_name}**")
    if st.button("Log out"):
        st.session_state.user_name = None
        st.session_state.messages = []
        if "conversation_id" in st.session_state:
            del st.session_state["conversation_id"]
        st.rerun()

    if db_available:
        st.divider()
        if st.button("+ New conversation", use_container_width=True):
            st.session_state.conversation_id = db.new_conversation_id()
            st.session_state.messages = []
            st.rerun()

        # Engineer Notes (Aug 2026) — standalone entry point. See
        # BACKLOG.md for the full design (Jared's real motivating example,
        # why attribution is non-negotiable, why this reuses the
        # equipment registry's identity rather than free text). A second,
        # inline entry point attached to a specific answer (pre-filled
        # from that answer's own equipment context) is deliberately not
        # built — deferred unless real usage shows genuine friction with
        # the standalone button, see BACKLOG.md.
        #
        # Restricted to a known list of authors (Aug 2026, real
        # requirement from Jared): these notes carry real weight — shown
        # before the answer, treated with real authority — so the button
        # itself is only shown to people on that list at all, rather than
        # shown to everyone and blocked with an error. See
        # engineer_notes.AUTHORIZED_NOTE_AUTHORS to add someone (e.g. a
        # newly delegated Chief Engineer). Now backed by real login (Aug
        # 2026) rather than a typed name, so this check is a genuine
        # authorization check, not just a display filter.
        author_role = engineer_notes.AUTHORIZED_NOTE_AUTHORS.get(st.session_state.user_name)
        if author_role:
            with st.popover("📝 + Engineer Note", use_container_width=True):
                st.caption("Real-world experience — kept clearly separate from manufacturer data.")
                try:
                    options = with_connection_retry(engineer_notes.get_equipment_options)
                except Exception:
                    options = [("General", engineer_notes.GENERAL_CATEGORY, None)]
                # Labels include system (Sept 2026, real trigger — see
                # BACKLOG.md's equipment dropdown scaling entry) so the
                # list stays scannable as systems beyond drivetrain get
                # added — Streamlit's selectbox already supports typing
                # to filter, so "Drivetrain — Main Engine — Port" lets
                # someone type "Drivetrain" or "Fire" to jump straight
                # to the right system's items.
                labels = [
                    f"{sys} — {cat} — {pos}" if pos else f"{sys} — {cat}"
                    for sys, cat, pos in options
                ]
                # st.form (Aug 2026, real bug fix): a real incident produced
                # 4 duplicate copies of the same note, submitted seconds
                # apart — almost certainly "Add Note" clicked more than
                # once while the form still showed the same filled-in
                # text. A form only submits once, explicitly, on its own
                # submit button (not on every stray widget interaction),
                # and clear_on_submit=True resets the fields afterward —
                # both make an accidental resubmission much harder.
                with st.form("engineer_note_form", clear_on_submit=True):
                    selected_label = st.selectbox("Equipment", labels, key="note_equipment_select")
                    note_text = st.text_area(
                        "Note", placeholder="What did you notice or adjust, and why?",
                        key="note_text_input",
                    )
                    submitted = st.form_submit_button("Add Note")
                    if submitted:
                        if note_text.strip():
                            _system, category, position = options[labels.index(selected_label)]
                            try:
                                with_connection_retry(
                                    engineer_notes.add_note, category, position,
                                    st.session_state.user_name, note_text.strip(),
                                    author_role=author_role)
                                st.success("Note added — it'll be used in future answers about this equipment.")
                            except Exception as e:
                                st.error(f"Couldn't save the note right now ({e}).")
                        else:
                            st.warning("Please enter a note before submitting.")

        # Document library panel (Sept 2026) — lets engineers browse what's
        # in the system by system rather than relying on retrieval to find
        # the right document. Real motivating gap: "show me the shaft
        # arrangement drawing" didn't reliably retrieve the drawing via
        # semantic search (shaft component content from manuals scored
        # higher). Clicking a document fires a pre-formed "show me" question
        # into the chat, which triggers the existing SHOW_DOCUMENT feature.
        st.divider()
        with st.expander("📂 Document Library", expanded=False):
            try:
                library = with_connection_retry(get_document_library)
            except Exception:
                library = {}
            if not library:
                st.caption("Library not available right now.")
            else:
                filter_text = st.text_input(
                    "Filter documents",
                    placeholder="Type to filter...",
                    key="lib_filter",
                    label_visibility="collapsed",
                )
                filter_lower = filter_text.strip().lower()

                def _short_label(doc, system):
                    """Strip system prefix — already in group header. Doc type
                    is also already embedded in the title so we omit it from
                    the label to avoid duplication. Only revision is appended
                    since that's the one piece of useful context not in the
                    title itself."""
                    prefix = f"{system} - "
                    title = doc["document_title"]
                    short = title[len(prefix):] if title.startswith(prefix) else title
                    rev = f" · {doc['revision']}" if doc.get("revision") else ""
                    return f"{short}{rev}"

                any_shown = False
                for system, docs in library.items():
                    visible_docs = [
                        d for d in docs
                        if not filter_lower
                        or filter_lower in d["document_title"].lower()
                        or filter_lower in _short_label(d, system).lower()
                        or filter_lower in (d.get("document_type") or "").lower()
                        or filter_lower in (d.get("revision") or "").lower()
                    ]
                    if not visible_docs:
                        continue
                    any_shown = True
                    st.markdown(f"**{system}**")
                    for doc in visible_docs:
                        label = _short_label(doc, system)
                        safe_key = f"lib_{doc['document_title']}_{doc.get('document_type','')}_{doc.get('revision','')}".replace(" ", "_")
                        if st.button(label, key=safe_key, use_container_width=True):
                            # Fire a "show me" question for this document —
                            # triggers the SHOW_DOCUMENT feature in answer_query.py
                            # so the page image surfaces prominently above the answer.
                            show_q = f"Show me the {doc['document_title']}"
                            st.session_state.messages.append({"role": "user", "content": show_q})
                            with st.chat_message("user"):
                                st.markdown(show_q)
                            if db_available:
                                try:
                                    with_connection_retry(
                                        db.save_message,
                                        st.session_state.conversation_id,
                                        st.session_state.user_name, "user", show_q)
                                except Exception:
                                    pass
                            with st.chat_message("assistant"):
                                with st.spinner("Searching the TMs..."):
                                    try:
                                        result = get_answer(show_q)
                                        answer_text = result["answer"]
                                        chunks = result["chunks"]
                                        safety_info = result.get("safety_info", "")
                                        field_notes_used = result.get("field_notes_used", [])
                                        show_document_images = result.get("show_document_images", [])
                                    except Exception as e:
                                        answer_text = f"⚠️ Something went wrong: {e}"
                                        chunks, safety_info, field_notes_used, show_document_images = [], "", [], []
                            new_message = {
                                "role": "assistant", "content": answer_text, "chunks": chunks,
                                "safety_info": safety_info, "field_notes_used": field_notes_used,
                                "show_document_images": show_document_images,
                            }
                            if db_available:
                                try:
                                    new_message["id"] = with_connection_retry(
                                        db.save_message,
                                        st.session_state.conversation_id,
                                        st.session_state.user_name, "assistant",
                                        answer_text, chunks=chunks, safety_info=safety_info,
                                        field_notes_used=field_notes_used,
                                        show_document_images=show_document_images)
                                except Exception:
                                    pass
                            st.session_state.messages.append(new_message)
                            render_assistant_message(
                                new_message,
                                key_prefix=f"lib_{new_message.get('id', len(st.session_state.messages))}")
                            st.rerun()

        # Grouped by recency (Aug 2026) — replaces a single flat list
        # that was hard-capped at 20 conversations, past which older
        # ones silently vanished from the sidebar with no indication
        # anything was missing. Nothing is deleted; db.list_conversations
        # now fetches a much larger practical limit, and
        # group_conversations_by_recency() organizes the full list into
        # Today/Yesterday/This Week/This Month/Older sections — the same
        # pattern used by most chat apps for exactly this problem. Only
        # non-empty groups are shown, so an empty "This Month" header
        # never appears just because nothing happens to be in it.
        try:
            past = with_connection_retry(db.list_conversations, st.session_state.user_name)
        except Exception:
            past = []
        grouped = db.group_conversations_by_recency(past)
        for group_name, convos in grouped.items():
            if not convos:
                continue
            st.caption(group_name)
            for convo in convos:
                label = convo["first_message"][:40] + ("..." if len(convo["first_message"]) > 40 else "")
                if st.button(label, key=f"convo_{convo['conversation_id']}", use_container_width=True):
                    st.session_state.conversation_id = str(convo["conversation_id"])
                    st.session_state.messages = with_connection_retry(
                        db.load_conversation, str(convo["conversation_id"]))
                    st.rerun()







for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_assistant_message(message, key_prefix=f"hist_{message.get('id', i)}")
        else:
            st.markdown(message["content"])

question = st.chat_input("Ask me an engineering question about Polaris's systems...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    if db_available:
        try:
            with_connection_retry(
                db.save_message, st.session_state.conversation_id,
                st.session_state.user_name, "user", question)
        except Exception:
            pass  # chat still works in-session even if a save fails

    with st.chat_message("assistant"):
        with st.spinner("Searching the TMs..."):
            try:
                result = get_answer(question)
                answer_text = result["answer"]
                chunks = result["chunks"]
                safety_info = result.get("safety_info", "")
                field_notes_used = result.get("field_notes_used", [])
                show_document_images = result.get("show_document_images", [])
            except ValueError as e:
                # get_voyage_key()/get_answer() raise cleanly on a missing
                # key rather than crashing the app — see BACKLOG.md and the
                # retrieval.py/answer_query.py refactor for why this matters
                # here specifically (a sys.exit() would have killed the
                # whole running app for every user, not just this request).
                answer_text = f"⚠️ Configuration problem: {e}"
                chunks = []
                safety_info = ""
                field_notes_used = []
                show_document_images = []
            except Exception as e:
                answer_text = f"⚠️ Something went wrong answering that: {e}"
                chunks = []
                safety_info = ""
                field_notes_used = []
                show_document_images = []

        new_message = {
            "role": "assistant", "content": answer_text, "chunks": chunks,
            "safety_info": safety_info, "field_notes_used": field_notes_used,
            "show_document_images": show_document_images,
        }
        if db_available:
            try:
                new_message["id"] = with_connection_retry(
                    db.save_message, st.session_state.conversation_id,
                    st.session_state.user_name, "assistant",
                    answer_text, chunks=chunks, safety_info=safety_info,
                    field_notes_used=field_notes_used,
                    show_document_images=show_document_images)
            except Exception:
                pass

        st.session_state.messages.append(new_message)
        render_assistant_message(new_message, key_prefix=f"live_{new_message.get('id', len(st.session_state.messages))}")
