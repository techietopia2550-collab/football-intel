"""Scout Chat: ask questions about the 2015/16 data in plain language. Presentation only: every
answer, refusal and warning comes from `src.chat.conversation.ask`, which calls `src/analytics`."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from typing import Any

import streamlit as st

from src.app import design
from src.chat.conversation import UNAVAILABLE, ask

MAX_TURNS = 10
SIDEBAR_NOTE = (
    "Answers come only from the 2015/16 StatsBomb database. No predictions. No career history."
)
LIMITATIONS = """
- **One season, four leagues:** 2015/16 Premier League, La Liga, Ligue 1 and Serie A only.
- **900-minute threshold:** players under it have season totals only, no percentiles.
- **Percentiles are within position group**, pooled across the four leagues.
- **Similarity is a heuristic:** in testing about half of "Fair" (60%+) matches shared a role.
- **Market values** are Transfermarkt estimates, not fees or wages. No age data.
- **The assistant can be wrong.** Every answer lists its sources: check them on the Player,
  Team and Compare pages. Questions are sent to Groq's hosted model.
"""

ss = st.session_state
ss.setdefault("chat_turns", [])  # [{"question": str, "reply": dict}]

st.sidebar.caption(SIDEBAR_NOTE)
design.render('<h1 class="fi-h1">Scout Chat</h1>')
st.caption("Ask about players, teams and styles in the 2015/16 data.")
with st.expander("Limitations"):
    st.markdown(LIMITATIONS)


def _render_reply(reply: dict[str, Any]) -> None:
    if reply.get("error") == "llm_unavailable":
        st.error(UNAVAILABLE)
        return
    st.markdown(reply["answer"])
    if reply["sources"]:
        with st.expander("Sources"):
            for src in reply["sources"]:
                st.markdown(f"**{src['tool']}**  `{src['arguments']}`")
                st.json(src["fields"], expanded=False)
    notes = list(reply["warnings"])
    if reply["refused"] and reply.get("verdict"):
        notes.insert(0, f"Refused: {reply['verdict']}")
    if notes:
        with st.expander("Warnings"):
            for note in notes:
                st.markdown(f"- {note}")


for turn in ss["chat_turns"]:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        _render_reply(turn["reply"])

question = st.chat_input("e.g. What are Özil's strengths?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    history: list[dict[str, str]] = []
    for turn in ss["chat_turns"][-MAX_TURNS:]:
        if turn["reply"].get("error"):
            continue
        history += [
            {"role": "user", "content": turn["question"]},
            {"role": "assistant", "content": turn["reply"]["answer"]},
        ]
    with st.chat_message("assistant"):
        with st.spinner("Looking it up..."):
            try:
                reply = ask(question, history)
            except Exception:  # noqa: BLE001 - never leave the page in a broken state
                reply = {"answer": UNAVAILABLE, "sources": [], "warnings": [], "refused": True,
                         "verdict": None, "error": "llm_unavailable"}  # fmt: skip
        _render_reply(reply)
    ss["chat_turns"] = [*ss["chat_turns"], {"question": question, "reply": reply}][-MAX_TURNS:]
