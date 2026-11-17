"""Operator auth storage + session tests (phase D7).

Everything runs against ``tmp_path`` (accounts JSON + SQLite) and an explicit
session-state snapshot. Credential material in these tests is synthetic.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
import streamlit as st

import axiom_auth
import axiom_db

SESSION_KEYS = (
    "_axiom_user_accounts",
    "_axiom_admin_hash",
    "authentication_status",
    "name",
    "username",
    "_last_active_ts",
    "_session_expired_notice",
)
_MISSING = object()
_REAL_GET_ADMIN_HASH = axiom_auth._get_admin_password_hash


class _FakeCookieController:
    def __init__(self):
        self.deleted = 0

    def delete_cookie(self):
        self.deleted += 1


class _FakeAuthenticator:
    def __init__(self):
        self.cookie_controller = _FakeCookieController()

    def login(self, location: str = "main"):
        return None


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    accounts_path = tmp_path / ".axiom_accounts.json"
    data_dir = tmp_path / "data"
    conn_cache: dict = {}

    monkeypatch.setattr(axiom_auth, "ACCOUNTS_STORE_PATH", accounts_path)
    monkeypatch.setattr(axiom_auth, "_get_admin_password_hash", lambda: "stub-admin-hash")
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_auth.db")
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", conn_cache)

    saved = {key: st.session_state.get(key, _MISSING) for key in SESSION_KEYS}
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)

    try:
        yield {"accounts_path": accounts_path}
    finally:
        for conn in list(conn_cache.values()):
            try:
                conn.close()
            except Exception:
                pass
        for key, original in saved.items():
            if original is _MISSING:
                st.session_state.pop(key, None)
            else:
                st.session_state[key] = original


# --------------------------------------------------------------------------- #
# bcrypt helpers
# --------------------------------------------------------------------------- #
def test_bcrypt_hash_and_verify_roundtrip():
    hashed = axiom_auth._hash_password_bcrypt("CorrectHorse1")
    assert hashed != "CorrectHorse1"
    assert hashed.startswith("$2")
    assert axiom_auth._verify_password_bcrypt("CorrectHorse1", hashed) is True
    assert axiom_auth._verify_password_bcrypt("wrong", hashed) is False
    assert axiom_auth._verify_password_bcrypt("anything", "not-a-bcrypt-hash") is False


def test_get_admin_password_hash_caches_in_session(auth_env, monkeypatch):
    calls: list[str] = []

    def _hash(raw: str) -> str:
        calls.append(raw)
        return "hashed-admin"

    monkeypatch.setattr(axiom_auth, "_hash_password_bcrypt", _hash)
    monkeypatch.setattr(axiom_auth, "_get_admin_password_hash", _REAL_GET_ADMIN_HASH)
    assert axiom_auth._get_admin_password_hash() == "hashed-admin"
    assert axiom_auth._get_admin_password_hash() == "hashed-admin"
    assert calls == ["Axiom#Ledger2026!"]


# --------------------------------------------------------------------------- #
# accounts store
# --------------------------------------------------------------------------- #
def test_load_accounts_store_seeds_default_admin_and_persists(auth_env):
    accounts = axiom_auth._load_accounts_store()

    assert list(accounts) == ["admin"]
    assert accounts["admin"]["password"] == "stub-admin-hash"
    on_disk = json.loads(auth_env["accounts_path"].read_text(encoding="utf-8"))
    assert on_disk == accounts


def test_load_accounts_store_prefers_session_then_disk(auth_env):
    axiom_auth._save_accounts_store({"custom": {"name": "C", "email": "c@x", "password": "p"}})
    st.session_state.pop("_axiom_user_accounts", None)

    assert list(axiom_auth._load_accounts_store()) == ["custom"]


def test_load_accounts_store_ignores_corrupt_json(auth_env):
    auth_env["accounts_path"].write_text("{not json", encoding="utf-8")
    assert list(axiom_auth._load_accounts_store()) == ["admin"]


def test_get_all_user_accounts_returns_store(auth_env):
    assert axiom_auth.get_all_user_accounts() == axiom_auth._load_accounts_store()


# --------------------------------------------------------------------------- #
# add / update / delete operators
# --------------------------------------------------------------------------- #
def test_add_operator_account_validation_and_success(auth_env, monkeypatch):
    monkeypatch.setattr(axiom_auth, "_hash_password_bcrypt", lambda raw: f"hashed::{raw}")

    assert axiom_auth.add_operator_account("x", "Name", "e@x", "Password1", "Password1")[0] is False
    assert axiom_auth.add_operator_account("operator", "", "e@x", "Password1", "Password1")[0] is False
    assert axiom_auth.add_operator_account("operator", "Op", "e@x", "short", "short")[0] is False
    assert axiom_auth.add_operator_account("operator", "Op", "e@x", "Password1", "Password2")[0] is False
    assert axiom_auth.add_operator_account("admin", "Op", "e@x", "Password1", "Password1")[0] is False

    ok, message = axiom_auth.add_operator_account("op.user", "Op User", "", "Password1", "Password1")
    assert ok is True
    assert "op.user" in message

    accounts = axiom_auth._load_accounts_store()
    assert accounts["op.user"]["name"] == "Op User"
    assert accounts["op.user"]["email"] == "op.user@axiomledger.local"
    assert accounts["op.user"]["password"] == "hashed::Password1"


def test_update_operator_credentials_paths(auth_env, monkeypatch):
    monkeypatch.setattr(axiom_auth, "_hash_password_bcrypt", lambda raw: f"hashed::{raw}")
    axiom_auth._save_accounts_store(
        {
            "admin": {"name": "Admin", "email": "a@x", "password": "hashed::OldPass1", "role": "Primary"},
            "taken": {"name": "Taken", "email": "t@x", "password": "hashed::OldPass1", "role": "Operator"},
        }
    )

    assert axiom_auth.update_operator_credentials("nobody", "n", "N", "n@x", "OldPass1")[0] is False
    assert (
        axiom_auth.update_operator_credentials("admin", "newadmin", "N", "n@x", "WrongPass1")[0] is False
    )
    assert (
        axiom_auth.update_operator_credentials("admin", "x", "N", "n@x", "OldPass1")[0] is False
    )
    assert (
        axiom_auth.update_operator_credentials("admin", "taken", "N", "n@x", "OldPass1")[0] is False
    )
    assert (
        axiom_auth.update_operator_credentials(
            "admin", "admin", "A", "a@x", "OldPass1", new_password="short", confirm_password="short"
        )[0]
        is False
    )
    assert (
        axiom_auth.update_operator_credentials(
            "admin", "admin", "A", "a@x", "OldPass1", new_password="NewPass123", confirm_password="Nope"
        )[0]
        is False
    )


def test_update_operator_credentials_renames_and_rehashes(auth_env, monkeypatch):
    real_hash = axiom_auth._hash_password_bcrypt
    axiom_auth._save_accounts_store(
        {"admin": {"name": "Admin", "email": "a@x", "password": real_hash("OldPass1"), "role": "Primary"}}
    )
    st.session_state["username"] = "admin"

    ok, message = axiom_auth.update_operator_credentials(
        "admin",
        "owner",
        "Owner Name",
        "owner@x",
        "OldPass1",
        new_password="NewPass123",
        confirm_password="NewPass123",
    )

    assert ok is True
    assert "password updated" in message
    accounts = axiom_auth._load_accounts_store()
    assert "admin" not in accounts and "owner" in accounts
    assert accounts["owner"]["name"] == "Owner Name"
    assert axiom_auth._verify_password_bcrypt("NewPass123", accounts["owner"]["password"]) is True
    assert st.session_state["username"] == "owner"


def test_delete_operator_account_paths(auth_env):
    assert axiom_auth.delete_operator_account("missing")[0] is False
    assert axiom_auth.delete_operator_account("admin")[0] is False  # last remaining account

    axiom_auth._save_accounts_store(
        {
            "admin": {"name": "Admin", "email": "a@x", "password": "h", "role": "Primary"},
            "op": {"name": "Op", "email": "o@x", "password": "h", "role": "Operator"},
        }
    )
    st.session_state["username"] = "op"
    assert axiom_auth.delete_operator_account("op")[0] is False  # cannot delete self
    st.session_state["username"] = "admin"
    assert axiom_auth.delete_operator_account("op")[0] is True
    assert "op" not in axiom_auth._load_accounts_store()


def test_get_auth_config_shape(auth_env):
    config = axiom_auth.get_auth_config()
    assert set(config) == {"credentials", "cookie"}
    assert config["cookie"]["name"] == "axiom_ledger_session"
    assert config["credentials"]["usernames"]["admin"]["password"] == "stub-admin-hash"


# --------------------------------------------------------------------------- #
# persisted session + inactivity timeout
# --------------------------------------------------------------------------- #
def test_session_roundtrip_and_clear(auth_env):
    axiom_auth._save_active_auth_session("admin", "Admin", 1234.5)
    persisted = axiom_auth._load_active_auth_session()
    assert persisted["authenticated"] is True
    assert persisted["username"] == "admin"
    assert persisted["last_active_ts"] == 1234.5

    axiom_auth._clear_active_auth_session()
    assert axiom_auth._load_active_auth_session() is None


def test_sync_expires_stale_persisted_session(auth_env):
    stale = time.time() - axiom_auth.SESSION_INACTIVITY_TIMEOUT_SEC - 5
    axiom_auth._save_active_auth_session("admin", "Admin", stale)
    authenticator = _FakeAuthenticator()

    assert axiom_auth.sync_and_touch_auth_session(authenticator) is False
    assert st.session_state["authentication_status"] is None
    assert st.session_state["_session_expired_notice"] is True
    assert authenticator.cookie_controller.deleted == 1
    assert axiom_auth._load_active_auth_session() is None


def test_sync_expires_stale_in_memory_session(auth_env):
    st.session_state["authentication_status"] = True
    st.session_state["_last_active_ts"] = time.time() - axiom_auth.SESSION_INACTIVITY_TIMEOUT_SEC - 5
    st.session_state["username"] = "admin"

    assert axiom_auth.sync_and_touch_auth_session() is False
    assert st.session_state["authentication_status"] is None
    assert st.session_state["_session_expired_notice"] is True


def test_sync_restores_session_from_sqlite(auth_env):
    axiom_auth._save_active_auth_session("admin", "Admin")

    assert axiom_auth.sync_and_touch_auth_session() is True
    assert st.session_state["authentication_status"] is True
    assert st.session_state["username"] == "admin"
    assert st.session_state["name"] == "System Administrator"
    assert st.session_state["_last_active_ts"] > 0
    assert axiom_auth._load_active_auth_session()["authenticated"] is True


def test_sync_ignores_persisted_session_for_deleted_account(auth_env):
    axiom_auth._save_active_auth_session("ghost", "Ghost")
    st.session_state["authentication_status"] = None

    assert axiom_auth.sync_and_touch_auth_session() is False
    assert st.session_state.get("authentication_status") is None


def test_sync_uses_unrendered_cookie_login_fallback(auth_env, monkeypatch):
    class _LoginAuthenticator(_FakeAuthenticator):
        def login(self, location: str = "main"):
            st.session_state["authentication_status"] = True
            st.session_state["username"] = "cookie-user"
            st.session_state["name"] = "Cookie"

    assert axiom_auth.sync_and_touch_auth_session(_LoginAuthenticator()) is True
    assert st.session_state["username"] == "cookie-user"


def test_sign_out_clears_state_and_cookie(auth_env):
    axiom_auth._save_active_auth_session("admin", "Admin")
    st.session_state["authentication_status"] = True
    st.session_state["_last_active_ts"] = time.time()
    authenticator = _FakeAuthenticator()

    axiom_auth.sign_out_current_session(authenticator)

    assert st.session_state["authentication_status"] is None
    assert st.session_state["username"] is None
    assert "_last_active_ts" not in st.session_state
    assert authenticator.cookie_controller.deleted == 1
    assert axiom_auth._load_active_auth_session() is None


def test_get_authenticator_builds_with_auto_hash(auth_env, monkeypatch):
    captured: dict = {}

    class _FakeStauth:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(axiom_auth.stauth, "Authenticate", _FakeStauth)
    axiom_auth.get_authenticator()

    assert captured["cookie_name"] == "axiom_ledger_session"
    assert captured["auto_hash"] is False


def test_get_authenticator_falls_back_without_auto_hash_kwarg(auth_env, monkeypatch):
    captured: dict = {}

    class _LegacyStauth:
        def __init__(self, **kwargs):
            if "auto_hash" in kwargs:
                raise TypeError("unexpected keyword")
            captured.update(kwargs)

    monkeypatch.setattr(axiom_auth.stauth, "Authenticate", _LegacyStauth)
    axiom_auth.get_authenticator()
    assert "auto_hash" not in captured
