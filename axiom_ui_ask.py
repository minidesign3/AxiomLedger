"""
axiom_ui_ask
============

Ask Axiom page: a Gemini-style assistant over the app's data.

Left column: new chat, model picker (live list of the Gemini models your key
can use), privacy option, saved conversations. Right column: the chat, with a
"What was sent to Gemini" audit expander under every answer.

The chat stays disabled until the user confirms the API key belongs to a
billing-enabled (paid) Google project — free-tier prompts may be used by
Google to improve products and read by human reviewers.
"""

from __future__ import annotations

import streamlit as st

from axiom_ai_store import (
    add_message,
    create_conversation,
    delete_conversation,
    get_messages,
    list_conversations,
    rename_conversation,
)

STARTERS = (
    "What should I fix next before sending this to my accountant?",
    "Why doesn't each property manager's rent reconcile with the bank?",
    "Which rows look miscoded or suspicious?",
    "Summarise income and expenses for each property.",
)
_CONV_KEY = "_ai_conv_id"


def build_ai_context(entity: str, tax_year: str, hide_incoming: bool):
    """Assemble the read-only data context the tools work on (same data as the Accountant Pack)."""
    from axiom_account_codes import get_entity_account_codes
    from axiom_ai_tools import AIContext
    from axiom_export_checks import prepare_export
    from axiom_ingestion import get_entity_uploaded_files
    from axiom_properties import get_entity_properties
    from axiom_rules import get_memorized_rules
    from axiom_state import format_tax_year_period, get_entity_jurisdiction

    result = prepare_export(entity, tax_year)
    return AIContext(
        entity=entity, tax_year=tax_year, period=format_tax_year_period(tax_year, entity),
        jurisdiction=get_entity_jurisdiction(entity), result=result,
        records=get_entity_uploaded_files(entity, tax_year), rules=get_memorized_rules(entity),
        codes=get_entity_account_codes(entity), properties=get_entity_properties(entity),
        hide_incoming=hide_incoming,
    )


def _store_flag(name: str, value: object | None = None) -> object:
    from axiom_memory import _load_memory_store, _save_memory_store

    store = _load_memory_store()
    if value is not None:
        store[name] = value
        _save_memory_store(store)
    return store.get(name)


def _client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _models(api_key: str, refresh: bool = False) -> list[str]:
    from axiom_ai_chat import list_chat_models

    if refresh or not st.session_state.get("_ai_models"):
        try:
            st.session_state["_ai_models"] = list_chat_models(_client(api_key))
            st.session_state["_ai_models_error"] = ""
        except Exception as exc:
            st.session_state["_ai_models"] = []
            st.session_state["_ai_models_error"] = str(exc).splitlines()[0][:200]
    return st.session_state["_ai_models"]


def _render_paid_gate() -> bool:
    if _store_flag("gemini_paid_ack"):
        return True
    st.warning(
        "**Before using Ask Axiom with financial data:** on Google's *free* tier, prompts may be used to improve "
        "Google products and may be read by human reviewers. On a *paid* (billing-enabled) project they aren't used "
        "for training and are only kept briefly for abuse monitoring. Only use a key from a billing-enabled project."
    )
    if st.checkbox("My Gemini API key belongs to a billing-enabled (paid) Google AI Studio / Cloud project"):
        _store_flag("gemini_paid_ack", True)
        st.rerun()
    return False


def _render_sidebar(entity: str, tax_year: str, api_key: str) -> tuple[str, bool]:
    if st.button("＋ New chat", use_container_width=True, type="primary"):
        st.session_state[_CONV_KEY] = None
        st.rerun()
    models = _models(api_key)
    if st.session_state.get("_ai_models_error"):
        st.error(f"Couldn't list models: {st.session_state['_ai_models_error']}")
    saved_model = str(_store_flag("ai_chat_model") or "")
    index = models.index(saved_model) if saved_model in models else 0
    model = st.selectbox("Model", models, index=index if models else None, placeholder="No models available")
    if model and model != saved_model:
        _store_flag("ai_chat_model", model)
    if st.button("↻ Refresh model list", use_container_width=True):
        _models(api_key, refresh=True)
        st.rerun()
    hide = st.toggle("Hide incoming bank-payment details (tenant names)", value=True,
                     help="Incoming bank payments that aren't from a manager, bank, IRD or your own accounts are sent "
                          "as '[incoming payment — details hidden]'. Account/IRD/card numbers, emails and phones are "
                          "always masked.")
    st.markdown("**Chats**")
    for conv in list_conversations(entity, tax_year):
        current = conv["conv_id"] == st.session_state.get(_CONV_KEY)
        label = ("▸ " if current else "") + conv["title"][:40]
        if st.button(label, key=f"_ai_conv_{conv['conv_id']}", use_container_width=True):
            st.session_state[_CONV_KEY] = conv["conv_id"]
            st.rerun()
    return model or "", hide


def _render_messages(messages: list[dict]) -> None:
    for msg in messages:
        with st.chat_message("assistant" if msg["role"] == "assistant" else "user"):
            st.markdown(msg["text"])
            if msg["role"] == "assistant" and msg.get("sent"):
                with st.expander(f"🔎 What was sent to Gemini ({len(msg['sent'])} data lookup(s), masked)"):
                    for item in msg["sent"]:
                        st.caption(f"`{item['tool']}` {item.get('args') or ''}")
                        st.json(item.get("result"), expanded=False)


def _answer(entity: str, tax_year: str, api_key: str, model: str, hide: bool, question: str) -> None:
    from axiom_ai_chat import ask

    conv_id = st.session_state.get(_CONV_KEY) or create_conversation(entity, tax_year, question[:60], model)
    st.session_state[_CONV_KEY] = conv_id
    history = get_messages(conv_id)
    add_message(conv_id, "user", question)
    with st.spinner(f"Looking at your data with {model}…"):
        ctx = build_ai_context(entity, tax_year, hide)
        result = ask(_client(api_key), model, ctx, history, question)
    add_message(conv_id, "assistant", result.text, result.sent, model)
    st.rerun()


def _render_conversation_tools(conv_id: str) -> None:
    c1, c2 = st.columns([3, 1])
    with c1:
        new_title = st.text_input("Chat title", key=f"_ai_title_{conv_id}", label_visibility="collapsed",
                                  placeholder="Rename this chat…")
        if new_title:
            rename_conversation(conv_id, new_title)
    with c2:
        if st.button("🗑 Delete chat", key=f"_ai_del_{conv_id}", use_container_width=True):
            delete_conversation(conv_id)
            st.session_state[_CONV_KEY] = None
            st.rerun()


def render_ask_axiom(entity: str, tax_year: str) -> None:
    from axiom_secrets import load_gemini_key

    st.title("Ask Axiom")
    st.caption(f"Ask anything about {entity} · FY {tax_year}. The assistant can read your data; it can't change it.")
    api_key = str(st.session_state.get("google_genai_api_key") or load_gemini_key() or "").strip()
    if not api_key:
        st.info("Add your Google AI Studio (Gemini) API key in ⚙ Settings → AI to use Ask Axiom.")
        return
    if not _render_paid_gate():
        return
    left, right = st.columns([1, 3], gap="large")
    with left:
        model, hide = _render_sidebar(entity, tax_year, api_key)
    with right:
        conv_id = st.session_state.get(_CONV_KEY)
        messages = get_messages(conv_id) if conv_id else []
        if conv_id:
            _render_conversation_tools(conv_id)
        if not messages:
            st.markdown("**Try asking:**")
            for i, starter in enumerate(STARTERS):
                if st.button(starter, key=f"_ai_starter_{i}", disabled=not model):
                    _answer(entity, tax_year, api_key, model, hide, starter)
        _render_messages(messages)
        question = st.chat_input("Ask about your ledger, files, rent reconciliation…", disabled=not model)
        if question and model:
            _answer(entity, tax_year, api_key, model, hide, question)
