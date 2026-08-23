"""
Vessel Maintenance TM Assistant — Streamlit chat front end.

This is a thin UI layer only. All the real logic (retrieval, prompt
construction, calling Claude) lives in ingestion/answer_query.py and
ingestion/retrieval.py, unchanged — this app just imports and calls it.
See docs/architecture.md for the full plan this fits into.

Run locally:
    export VOYAGE_API_KEY="..."
    export ANTHROPIC_API_KEY="..."
    pip install -r requirements.txt
    streamlit run app.py

API keys: checks Streamlit secrets first (for when this is deployed to
Streamlit Community Cloud later), falls back to plain environment
variables (so it runs immediately today with the same `export` commands
already used for the CLI tools — no extra setup needed for local testing).
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

for key_name in ("VOYAGE_API_KEY", "ANTHROPIC_API_KEY"):
    if key_name not in os.environ and key_name in st.secrets:
        os.environ[key_name] = st.secrets[key_name]

from answer_query import get_answer, format_sources  # noqa: E402  (must come after sys.path fix above)

st.set_page_config(page_title="Vessel Maintenance TM Assistant", page_icon="⚓")
st.title("⚓ Vessel Maintenance TM Assistant")
st.caption(
    "Ask a question about the drivetrain TMs. Answers are generated only "
    "from the actual manual text — every answer includes the source "
    "excerpt it came from, so you can verify it yourself."
)

# v1: history lives only in this browser session (resets on refresh).
# Persistent, cross-session history via Supabase is step 4 of the planned
# build order — see docs/architecture.md.
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and message.get("chunks"):
            st.caption(format_sources(message["chunks"]).replace("\n", "  \n"))

question = st.chat_input("Ask a question about the drivetrain TMs...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

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
        if chunks:
            st.caption(format_sources(chunks).replace("\n", "  \n"))

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer_text,
        "chunks": chunks,
    })
