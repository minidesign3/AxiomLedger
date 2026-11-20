"""
Tests for the LLM connectivity verification service (``axiom_llm.py``).

Both ``verify_gemini_connection`` and ``verify_local_llm_connection`` perform
real network I/O in production. Every test here monkeypatches the underlying
client (``genai.Client``) or transport (``urllib.request.urlopen``) to a fake —
**no real network or API call is ever made**. ``verify_gemini_connection``'s
persistence path (``utils._save_memory_store``) is exercised against an
isolated temp SQLite DB, never the real one (AGENTS.md).
"""

from __future__ import annotations

import io
import threading
import urllib.error

import pytest

import axiom_db
import axiom_llm as al
import axiom_memory
import utils


@pytest.fixture()
def temp_llm_store(tmp_path, monkeypatch):
    """Isolate the SQLite store + JSON fallback used by verify_gemini_connection's _save_memory_store call."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", data_dir / "vault")
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    monkeypatch.setattr(utils, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")
    monkeypatch.setattr(axiom_memory, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")
    utils._load_memory_store_from_storage.clear()
    yield
    utils._load_memory_store_from_storage.clear()


class TestVerifyGeminiConnection:
    def test_no_api_key_returns_early_without_network(self, temp_llm_store, monkeypatch):
        monkeypatch.setitem(utils.st.session_state, "google_genai_api_key", "")
        status = al.verify_gemini_connection(api_key="")
        assert status["connected"] is False
        assert status["label"] == "NO API KEY"

    def test_successful_connection(self, temp_llm_store, monkeypatch):
        class _FakeModelsApi:
            def list(self):
                return iter(["model-a", "model-b"])

        class _FakeClient:
            def __init__(self, api_key):
                self.api_key = api_key
                self.models = _FakeModelsApi()

        monkeypatch.setattr(al.genai, "Client", _FakeClient)
        status = al.verify_gemini_connection(api_key="fake-valid-key")
        assert status["connected"] is True
        assert "CONNECTED" in status["label"]
        # Persisted back into session state and the (isolated) memory store.
        assert utils.st.session_state["google_genai_api_key"] == "fake-valid-key"
        stored = utils._load_memory_store()
        # The key is never persisted in the plain-text store any more — it goes to the
        # private secrets file (axiom_secrets; isolated per test by conftest).
        assert stored.get("google_genai_api_key") == ""
        import axiom_secrets

        assert axiom_secrets.load_gemini_key() == "fake-valid-key"

    def test_auth_error_is_caught_and_reported(self, temp_llm_store, monkeypatch):
        class _FakeClient:
            def __init__(self, api_key):
                raise ValueError("invalid API key: 401 Unauthorized")

        monkeypatch.setattr(al.genai, "Client", _FakeClient)
        status = al.verify_gemini_connection(api_key="bad-key")
        assert status["connected"] is False
        assert status["label"] == "AUTH / CONNECTION ERROR"
        assert "invalid API key" in status["detail"]

    def test_falls_back_to_session_state_key_when_arg_is_none(self, temp_llm_store, monkeypatch):
        monkeypatch.setitem(utils.st.session_state, "google_genai_api_key", "from-session")

        class _FakeModelsApi:
            def list(self):
                return iter([])

        class _FakeClient:
            def __init__(self, api_key):
                self.received_key = api_key
                self.models = _FakeModelsApi()

        monkeypatch.setattr(al.genai, "Client", _FakeClient)
        status = al.verify_gemini_connection(api_key=None)
        assert status["connected"] is True


class TestVerifyLocalLlmConnection:
    def test_no_url_returns_early_without_network(self, monkeypatch):
        monkeypatch.setitem(utils.st.session_state, "local_llm_url", "")
        status = al.verify_local_llm_connection(local_llm_url="")
        assert status["connected"] is False
        assert status["label"] == "NO ENDPOINT URL"

    def test_successful_connection_reports_model_count(self, monkeypatch):
        class _FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        payload = b'{"data": [{"id": "qwen2.5:9b"}, {"id": "llama3"}]}'

        def fake_urlopen(req, timeout=2.0):
            return _FakeResponse(payload)

        monkeypatch.setattr(al.urllib.request, "urlopen", fake_urlopen)
        status = al.verify_local_llm_connection(local_llm_url="http://localhost:11434/v1")
        assert status["connected"] is True
        assert "2 model(s)" in status["detail"]

    def test_unreachable_endpoint_is_caught_and_reported(self, monkeypatch):
        def fake_urlopen(req, timeout=2.0):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(al.urllib.request, "urlopen", fake_urlopen)
        status = al.verify_local_llm_connection(local_llm_url="http://localhost:19999/v1")
        assert status["connected"] is False
        assert status["label"] == "OFFLINE / UNREACHABLE"
        assert "unreachable" in status["detail"]

    def test_default_url_used_when_none_and_session_empty(self, monkeypatch):
        monkeypatch.setitem(utils.st.session_state, "local_llm_url", al.DEFAULT_LOCAL_LLM_URL)

        def fake_urlopen(req, timeout=2.0):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(al.urllib.request, "urlopen", fake_urlopen)
        status = al.verify_local_llm_connection(local_llm_url=None)
        assert al.DEFAULT_LOCAL_LLM_URL in status["detail"]
