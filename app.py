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

"Auth" is intentionally minimal for now: a name selector (Dave/Jared) in
the sidebar, not real accounts — appropriate for two known users; see
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

st.set_page_config(page_title="Fathom - Polaris", page_icon="⚓")

# Hide Streamlit's default developer-facing chrome (Aug 2026) — the
# top-right toolbar (Share/star/GitHub/edit-pencil icons), the hamburger
# menu (with its "Deploy" option), and the "Made with Streamlit" footer.
# These are aimed at Streamlit developers, not end users, and make an
# app look like an obvious dev/demo project rather than a real product —
# worth hiding now given the longer-term goal of this looking
# professional to an actual customer, not just Dave and Jared. Uses
# Streamlit's documented data-testid selectors where available (more
# stable across versions than relying on generic tag names alone).
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
.stDeployButton {display: none;}

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
    return conn


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

# --- Simple user identification (not real auth — see docs/architecture.md) ---
if "user_name" not in st.session_state:
    st.session_state.user_name = None

with st.sidebar:
    st.subheader("Who's asking?")
    name_input = st.text_input(
        "Name", value=st.session_state.user_name or "",
        placeholder="Type your name...",
        label_visibility="collapsed",
    )
    # Free-text (Aug 2026) — was a fixed Dave/Jared dropdown, which blocked
    # everyone else on the crew from using the app at all. Light
    # normalization (trim + title-case) so "jared" / "Jared" / "JARED"
    # from different visits still group together under one consistent
    # name for "Past conversations" and feedback — matching is an exact
    # string comparison at the database level, not case-insensitive.
    st.session_state.user_name = name_input.strip().title() or None

    if st.session_state.user_name and db_available:
        st.divider()
        if st.button("+ New conversation", use_container_width=True):
            st.session_state.conversation_id = db.new_conversation_id()
            st.session_state.messages = []
            st.rerun()

        st.caption("Past conversations")
        try:
            past = db.list_conversations(conn, st.session_state.user_name)
        except Exception:
            past = []
        for convo in past:
            label = convo["first_message"][:40] + ("..." if len(convo["first_message"]) > 40 else "")
            if st.button(label, key=f"convo_{convo['conversation_id']}", use_container_width=True):
                st.session_state.conversation_id = str(convo["conversation_id"])
                st.session_state.messages = db.load_conversation(conn, str(convo["conversation_id"]))
                st.rerun()

if not st.session_state.user_name:
    st.info("👋 Select your name in the sidebar to get started.")
    st.stop()

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = db.new_conversation_id() if db_available else "local-only"
if "messages" not in st.session_state:
    st.session_state.messages = []


def render_assistant_extras(message: dict, key_prefix: str):
    """Sources caption, page images, 👍/👎 feedback, and copy options —
    shared by both historical messages (loaded from the sidebar) and the
    live answer just generated, so they always look and behave
    identically. key_prefix must be unique per message (message id if
    saved, or the live index) since Streamlit widgets need stable,
    unique keys."""
    chunks = message.get("chunks")
    if chunks:
        st.caption(format_sources(chunks).replace("\n", "  \n"))

    # Page images (Aug 2026) — lets the user see the actual source page,
    # including diagrams/exploded views that plain text can't fully
    # convey. Only appears for pages that were selected for rendering at
    # ingestion time — see page_images.py. De-duplicated the same way
    # format_sources() dedupes citations (a dense-table sub-chunk shares
    # its page's image with the main page chunk).
    image_entries = []
    seen_images = set()
    for c in (chunks or []):
        m = c["metadata"]
        url = m.get("page_image_url")
        if url and url not in seen_images:
            seen_images.add(url)
            image_entries.append((m["document_title"], m["page_number"], url))
    if image_entries:
        label = f"🖼️ View page image{'s' if len(image_entries) > 1 else ''} ({len(image_entries)})"
        with st.expander(label):
            for doc_title, page_num, url in image_entries:
                st.caption(f"{doc_title}, p. {page_num}")
                st.image(url)

    message_id = message.get("id")
    if db_available and message_id is not None:
        current = message.get("feedback")
        col1, col2, _ = st.columns([1, 1, 10])
        with col1:
            if st.button("👍" if current != "up" else "✅👍", key=f"{key_prefix}_up"):
                new_value = None if current == "up" else "up"
                try:
                    db.set_feedback(conn, message_id, new_value)
                    message["feedback"] = new_value
                    st.rerun()
                except Exception:
                    pass
        with col2:
            if st.button("👎" if current != "down" else "✅👎", key=f"{key_prefix}_down"):
                new_value = None if current == "down" else "down"
                try:
                    db.set_feedback(conn, message_id, new_value)
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


for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_assistant_extras(message, key_prefix=f"hist_{message.get('id', i)}")

question = st.chat_input("Ask me an engineering question about Polaris's systems...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    if db_available:
        try:
            db.save_message(conn, st.session_state.conversation_id,
                             st.session_state.user_name, "user", question)
        except Exception:
            pass  # chat still works in-session even if a save fails

    with st.chat_message("assistant"):
        with st.spinner("Searching the TMs..."):
            try:
                result = get_answer(question)
                answer_text = result["answer"]
                chunks = result["chunks"]
            except ValueError as e:
                # get_voyage_key()/get_answer() raise cleanly on a missing
                # key rather than crashing the app — see BACKLOG.md and the
                # retrieval.py/answer_query.py refactor for why this matters
                # here specifically (a sys.exit() would have killed the
                # whole running app for every user, not just this request).
                answer_text = f"⚠️ Configuration problem: {e}"
                chunks = []
            except Exception as e:
                answer_text = f"⚠️ Something went wrong answering that: {e}"
                chunks = []

        st.markdown(answer_text)

        new_message = {"role": "assistant", "content": answer_text, "chunks": chunks}
        if db_available:
            try:
                new_message["id"] = db.save_message(
                    conn, st.session_state.conversation_id,
                    st.session_state.user_name, "assistant",
                    answer_text, chunks=chunks)
            except Exception:
                pass

        st.session_state.messages.append(new_message)
        render_assistant_extras(new_message, key_prefix=f"live_{new_message.get('id', len(st.session_state.messages))}")
