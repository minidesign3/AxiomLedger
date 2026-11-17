"""
Cache + invalidation tests for the memory-store reference-data cache.

`_load_memory_store_from_storage` (in `utils.py`) is wrapped in `st.cache_data`
with no arguments, so it holds a single process-wide cache entry. These tests
monkeypatch the underlying storage locations in `axiom_db` (where the actual
SQLite connection/paths live) and `utils.MEMORY_STORE_PATH` (the JSON fallback)
to an isolated temp directory, and explicitly clear the cache before/after each
test — so no real `data/axiom_ledger.db` or `.axiom_memory.json` is ever read,
written, or polluted across test runs, per `AGENTS.md`.
"""

from __future__ import annotations

import json
import threading

import pytest

import axiom_db
import axiom_memory
import utils


@pytest.fixture()
def temp_memory_store(tmp_path, monkeypatch):
    """Isolate both the SQLite store (axiom_db globals) and the JSON fallback (utils.MEMORY_STORE_PATH)."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", data_dir / "vault")
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    monkeypatch.setattr(utils, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")
    monkeypatch.setattr(axiom_memory, "MEMORY_STORE_PATH", tmp_path / ".axiom_memory.json")

    utils._load_memory_store_from_storage.clear()
    yield utils
    utils._load_memory_store_from_storage.clear()


def _read_raw_kv_value() -> dict[str, object] | None:
    with axiom_db._get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT value_json FROM kv_settings WHERE key = 'axiom_memory_store'"
        ).fetchone()
    return json.loads(row["value_json"]) if row and row["value_json"] else None


class TestColdLoad:
    def test_first_load_bootstraps_defaults_and_persists(self, temp_memory_store):
        store = temp_memory_store._load_memory_store()
        assert store["entity_options"] == list(utils.ENTITY_OPTIONS)
        assert "memorized_rules" in store
        # Persisted to SQLite...
        assert _read_raw_kv_value() is not None
        # ...and to the JSON fallback file.
        assert temp_memory_store.MEMORY_STORE_PATH.exists()


class TestCachingBehavior:
    def test_second_call_is_a_cache_hit_and_ignores_external_db_change(self, temp_memory_store):
        first = temp_memory_store._load_memory_store()

        # Mutate the underlying SQLite row directly, bypassing _save_memory_store
        # (simulating "data changed on disk without going through the cache").
        tampered = dict(first)
        tampered["google_genai_api_key"] = "SHOULD_NOT_APPEAR_YET"
        with axiom_db._get_sqlite_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_memory_store', ?)",
                (json.dumps(tampered),),
            )

        second = temp_memory_store._load_memory_store()
        # Still cached: the externally-tampered value must NOT be visible yet.
        assert second.get("google_genai_api_key") != "SHOULD_NOT_APPEAR_YET"

    def test_each_call_returns_an_independent_copy(self, temp_memory_store):
        a = temp_memory_store._load_memory_store()
        b = temp_memory_store._load_memory_store()
        a["entity_options"].append("Mutated Only Locally")
        assert "Mutated Only Locally" not in b["entity_options"]


class TestWritePathInvalidation:
    def test_save_memory_store_invalidates_cache(self, temp_memory_store, monkeypatch):
        store = temp_memory_store._load_memory_store()
        store["google_genai_api_key"] = "fake-key-for-test"
        temp_memory_store._save_memory_store(store)

        reloaded = temp_memory_store._load_memory_store()
        assert reloaded["google_genai_api_key"] == "fake-key-for-test"

    def test_add_entity_property_is_visible_on_next_load(self, temp_memory_store):
        ok, _msg = temp_memory_store.add_entity_property("Test Street 99", entity="BT Property Holdings Ltd")
        assert ok
        store = temp_memory_store._load_memory_store()
        assert "Test Street 99" in store["entity_properties"]["BT Property Holdings Ltd"]
