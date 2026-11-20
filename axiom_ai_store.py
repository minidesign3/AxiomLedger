"""
axiom_ai_store
==============

Ask Axiom conversations, stored locally in the app's SQLite database
(``ai_conversations`` / ``ai_messages``). Each assistant message keeps the
audit trail of what was sent to Gemini for it (``sent_json``: tool calls with
their masked results).
"""

from __future__ import annotations

import json
import time
import uuid

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_conversations (
        conv_id TEXT PRIMARY KEY, entity TEXT NOT NULL, tax_year TEXT NOT NULL,
        title TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS ai_messages (
        msg_id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, role TEXT NOT NULL,
        text TEXT NOT NULL, sent_json TEXT NOT NULL DEFAULT '[]', model TEXT NOT NULL DEFAULT '', created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_ai_messages_conv ON ai_messages(conv_id, msg_id)",
)


def _conn():
    from axiom_db import _DB_LOCK, _get_sqlite_conn

    return _DB_LOCK, _get_sqlite_conn()


def ensure_schema() -> None:
    lock, conn = _conn()
    with lock, conn:
        for stmt in _SCHEMA:
            conn.execute(stmt)


def create_conversation(entity: str, tax_year: str, title: str = "New chat", model: str = "") -> str:
    ensure_schema()
    conv_id = uuid.uuid4().hex
    now = time.time()
    lock, conn = _conn()
    with lock, conn:
        conn.execute(
            "INSERT INTO ai_conversations (conv_id, entity, tax_year, title, model, created, updated) VALUES (?,?,?,?,?,?,?)",
            (conv_id, entity, tax_year, title[:80], model, now, now),
        )
    return conv_id


def list_conversations(entity: str, tax_year: str) -> list[dict]:
    ensure_schema()
    lock, conn = _conn()
    with lock:
        rows = conn.execute(
            "SELECT conv_id, title, model, updated FROM ai_conversations WHERE entity=? AND tax_year=? ORDER BY updated DESC",
            (entity, tax_year),
        ).fetchall()
    return [{"conv_id": r[0], "title": r[1], "model": r[2], "updated": r[3]} for r in rows]


def add_message(conv_id: str, role: str, text: str, sent: list[dict] | None = None, model: str = "") -> None:
    ensure_schema()
    lock, conn = _conn()
    with lock, conn:
        conn.execute(
            "INSERT INTO ai_messages (conv_id, role, text, sent_json, model, created) VALUES (?,?,?,?,?,?)",
            (conv_id, role, text, json.dumps(sent or [], default=str), model, time.time()),
        )
        conn.execute("UPDATE ai_conversations SET updated=? WHERE conv_id=?", (time.time(), conv_id))


def get_messages(conv_id: str) -> list[dict]:
    ensure_schema()
    lock, conn = _conn()
    with lock:
        rows = conn.execute(
            "SELECT role, text, sent_json, model FROM ai_messages WHERE conv_id=? ORDER BY msg_id", (conv_id,)
        ).fetchall()
    return [{"role": r[0], "text": r[1], "sent": json.loads(r[2] or "[]"), "model": r[3]} for r in rows]


def rename_conversation(conv_id: str, title: str) -> None:
    lock, conn = _conn()
    with lock, conn:
        conn.execute("UPDATE ai_conversations SET title=? WHERE conv_id=?", (title.strip()[:80] or "Chat", conv_id))


def delete_conversation(conv_id: str) -> None:
    lock, conn = _conn()
    with lock, conn:
        conn.execute("DELETE FROM ai_messages WHERE conv_id=?", (conv_id,))
        conn.execute("DELETE FROM ai_conversations WHERE conv_id=?", (conv_id,))
