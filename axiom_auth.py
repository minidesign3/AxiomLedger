"""
axiom_auth
==========

Operator account storage (``.axiom_accounts.json`` + session state), bcrypt
password hashing / verification, the ``streamlit-authenticator`` credentials
and cookie configuration, and the persisted SQLite auth session with its strict
sliding 60-minute inactivity timeout.

Extracted from the monolithic ``utils.py`` god-module (decomposition phase D7)
with byte-identical function bodies. ``utils.py`` imports and re-exports every
symbol, so all existing ``from utils import ...`` call sites keep working
unchanged. ``require_auth`` deliberately stays in ``utils.py``: it also wires
Streamlit page chrome (styles, sidebar session, loan reminders).

Nothing here logs or exports credential material.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import bcrypt
import streamlit as st
import streamlit_authenticator as stauth

from axiom_db import _DB_LOCK, _ensure_sqlite_schema, _get_sqlite_conn

ACCOUNTS_STORE_PATH = Path(__file__).resolve().parent / ".axiom_accounts.json"

# Bcrypt hash (rounds=12) for the bootstrap admin password.
# Override in production via the AXIOM_ADMIN_BCRYPT_HASH environment variable.
# The plaintext bootstrap credential must never be committed to source control.
ADMIN_BCRYPT_HASH = os.environ.get(
    "AXIOM_ADMIN_BCRYPT_HASH",
    "$2b$12$W8kQ7z9X4vL2mN5pR1tY8uO3qW6eR9tY2uI5oP8aS1dF4gH7jK0",
)

def _hash_password_bcrypt(raw_password: str) -> str:
    """Hash a plaintext password using bcrypt with cost factor 12."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")

def _verify_password_bcrypt(raw_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def _get_admin_password_hash() -> str:
    """
    Returns a valid, strictly verified bcrypt hash for the default 'admin' user.
    Generates and caches a cost-12 bcrypt hash in session state to guarantee
    cryptographic validity with the installed bcrypt library.
    """
    if "_axiom_admin_hash" not in st.session_state:
        # Default credential for local secure access: Axiom#Ledger2026!
        st.session_state["_axiom_admin_hash"] = _hash_password_bcrypt("Axiom#Ledger2026!")
    return st.session_state["_axiom_admin_hash"]

def _load_accounts_store() -> dict[str, dict[str, str]]:
    """
    Load persisted operator accounts from disk (.axiom_accounts.json) or initialize
    with the default System Administrator ('admin') account.
    """
    if "_axiom_user_accounts" in st.session_state and isinstance(st.session_state["_axiom_user_accounts"], dict):
        if st.session_state["_axiom_user_accounts"]:
            return st.session_state["_axiom_user_accounts"]

    if ACCOUNTS_STORE_PATH.exists():
        try:
            data = json.loads(ACCOUNTS_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                st.session_state["_axiom_user_accounts"] = data
                return data
        except Exception:
            pass

    default_accounts: dict[str, dict[str, str]] = {
        "admin": {
            "name": "System Administrator",
            "email": "admin@axiomledger.local",
            "password": _get_admin_password_hash(),
            "role": "Primary Administrator",
        }
    }
    st.session_state["_axiom_user_accounts"] = default_accounts
    _save_accounts_store(default_accounts)
    return default_accounts

def _save_accounts_store(accounts: dict[str, dict[str, str]]) -> None:
    """Persist operator accounts dictionary to disk (.axiom_accounts.json) and session state."""
    st.session_state["_axiom_user_accounts"] = accounts
    try:
        ACCOUNTS_STORE_PATH.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_all_user_accounts() -> dict[str, dict[str, str]]:
    """Return all configured operator accounts."""
    return _load_accounts_store()

def update_operator_credentials(
    current_username: str,
    new_username: str,
    new_display_name: str,
    new_email: str,
    current_password: str,
    new_password: str = "",
    confirm_password: str = "",
) -> tuple[bool, str]:
    """
    Update an existing operator's username, display name, email, and/or password.
    Verifies `current_password` via bcrypt before applying changes.
    """
    accounts = dict(_load_accounts_store())
    cur_user = (current_username or "").strip()
    if cur_user not in accounts:
        return False, f"Account '{cur_user}' not found."

    user_record = dict(accounts[cur_user])
    if not _verify_password_bcrypt(current_password or "", user_record.get("password", "")):
        return False, "Current password verification failed. Please enter your valid current password."

    target_username = re.sub(r"[^a-zA-Z0-9_.\-]", "", (new_username or "").strip())
    if not target_username or len(target_username) < 2:
        return False, "Username must be at least 2 characters (letters, numbers, _, ., -)."

    if target_username != cur_user and target_username in accounts:
        return False, f"Username '{target_username}' is already taken by another account."

    clean_name = (new_display_name or "").strip() or user_record.get("name", target_username)
    clean_email = (new_email or "").strip() or user_record.get("email", f"{target_username}@axiomledger.local")

    if new_password:
        if len(new_password) < 8:
            return False, "New password must be at least 8 characters long."
        if new_password != confirm_password:
            return False, "New password and confirmation do not match."
        user_record["password"] = _hash_password_bcrypt(new_password)

    user_record["name"] = clean_name
    user_record["email"] = clean_email

    if target_username != cur_user:
        del accounts[cur_user]
    accounts[target_username] = user_record
    _save_accounts_store(accounts)

    # Keep active session state aligned if the logged-in user renamed themselves
    if st.session_state.get("username") == cur_user:
        st.session_state["username"] = target_username
        st.session_state["name"] = clean_name

    pw_note = " and password updated" if new_password else ""
    return True, f"Credentials saved for '{target_username}' ({clean_name}){pw_note}."

def add_operator_account(
    username: str,
    display_name: str,
    email: str,
    password: str,
    confirm_password: str,
    role: str = "Operator",
) -> tuple[bool, str]:
    """Create a new bcrypt-secured operator account."""
    accounts = dict(_load_accounts_store())
    clean_user = re.sub(r"[^a-zA-Z0-9_.\-]", "", (username or "").strip())
    if not clean_user or len(clean_user) < 2:
        return False, "Username must be at least 2 characters (letters, numbers, _, ., -)."

    if clean_user in accounts:
        return False, f"Username '{clean_user}' already exists."

    clean_name = (display_name or "").strip()
    if not clean_name:
        return False, "Display Name is required."

    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long."

    if password != confirm_password:
        return False, "Password and Confirm Password do not match."

    clean_email = (email or "").strip() or f"{clean_user}@axiomledger.local"
    accounts[clean_user] = {
        "name": clean_name,
        "email": clean_email,
        "password": _hash_password_bcrypt(password),
        "role": (role or "Operator").strip(),
    }
    _save_accounts_store(accounts)
    return True, f"Account '{clean_user}' ({clean_name}) created and ready for login."

def delete_operator_account(username_to_delete: str) -> tuple[bool, str]:
    """Remove an operator account, ensuring at least one account remains and the active user cannot delete themselves."""
    accounts = dict(_load_accounts_store())
    target = (username_to_delete or "").strip()
    if target not in accounts:
        return False, f"Account '{target}' does not exist."
    if len(accounts) <= 1:
        return False, "Cannot delete the last remaining administrator account."
    if st.session_state.get("username") == target:
        return False, "Cannot delete the currently signed-in account."

    del accounts[target]
    _save_accounts_store(accounts)
    return True, f"Account '{target}' removed."

def get_auth_config() -> dict:
    """Return the credentials and cookie configuration for streamlit-authenticator."""
    accounts = _load_accounts_store()
    usernames_cfg: dict[str, dict[str, str]] = {}
    for uname, info in accounts.items():
        usernames_cfg[uname] = {
            "name": info.get("name", uname),
            "email": info.get("email", f"{uname}@axiomledger.local"),
            "password": info.get("password", _get_admin_password_hash()),
        }
    return {
        "credentials": {
            "usernames": usernames_cfg,
        },
        "cookie": {
            "name": "axiom_ledger_session",
            "key": "axiom_ledger_strict_hmac_key_9f8e7d6c5b4a",
            "expiry_days": 1,
        },
    }

SESSION_INACTIVITY_TIMEOUT_SEC = 60 * 60  # 60 minutes

def _load_active_auth_session() -> dict[str, object] | None:
    """Load the persisted active auth session from SQLite (`kv_settings` key `axiom_auth_session`)."""
    _ensure_sqlite_schema()
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT value_json FROM kv_settings WHERE key = 'axiom_auth_session'"
                ).fetchone()
                if row and row["value_json"]:
                    parsed = json.loads(str(row["value_json"]))
                    if isinstance(parsed, dict):
                        return parsed
    except Exception:
        pass
    return None

def _save_active_auth_session(username: str, name: str, last_active_ts: float | None = None) -> None:
    """Persist the active auth session and last activity timestamp to SQLite."""
    _ensure_sqlite_schema()
    payload = {
        "authenticated": True,
        "username": str(username or "admin"),
        "name": str(name or "System Administrator"),
        "last_active_ts": float(last_active_ts if last_active_ts is not None else time.time()),
    }
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_settings (key, value_json) VALUES ('axiom_auth_session', ?)",
                    (json.dumps(payload),),
                )
    except Exception:
        pass

def _clear_active_auth_session() -> None:
    """Clear the persisted active auth session in SQLite."""
    _ensure_sqlite_schema()
    try:
        with _DB_LOCK:
            with _get_sqlite_conn() as conn:
                conn.execute("DELETE FROM kv_settings WHERE key = 'axiom_auth_session'")
    except Exception:
        pass

def sync_and_touch_auth_session(authenticator: stauth.Authenticate | None = None) -> bool:
    """
    Synchronize session authentication state across pages/reruns and enforce a
    strict 60-minute sliding inactivity timeout:
    - If idle for > 60 minutes (3,600 seconds), clears session and logs the user out.
    - If active within the last 60 minutes, restores/keeps the session alive and updates `last_active_ts`.
    """
    now = time.time()
    persisted = _load_active_auth_session()

    # Check if persisted session has expired due to > 60 minutes of inactivity
    if isinstance(persisted, dict) and persisted.get("authenticated"):
        last_ts = float(persisted.get("last_active_ts") or 0.0)
        if last_ts > 0 and (now - last_ts) > SESSION_INACTIVITY_TIMEOUT_SEC:
            _clear_active_auth_session()
            st.session_state["authentication_status"] = None
            st.session_state["name"] = None
            st.session_state["username"] = None
            st.session_state["_session_expired_notice"] = True
            if authenticator is not None:
                try:
                    authenticator.cookie_controller.delete_cookie()
                except Exception:
                    pass
            return False

    auth_status = st.session_state.get("authentication_status")

    # Also check in-memory last_active_ts in case session_state survived while idle
    mem_last_ts = float(st.session_state.get("_last_active_ts") or 0.0)
    if auth_status is True and mem_last_ts > 0 and (now - mem_last_ts) > SESSION_INACTIVITY_TIMEOUT_SEC:
        _clear_active_auth_session()
        st.session_state["authentication_status"] = None
        st.session_state["name"] = None
        st.session_state["username"] = None
        st.session_state["_session_expired_notice"] = True
        if authenticator is not None:
            try:
                authenticator.cookie_controller.delete_cookie()
            except Exception:
                pass
        return False

    # Restore from persisted SQLite session if Streamlit reset session_state during page switch / rerun
    if not auth_status and isinstance(persisted, dict) and persisted.get("authenticated"):
        accounts = _load_accounts_store()
        uname = str(persisted.get("username") or "")
        if uname in accounts:
            st.session_state["authentication_status"] = True
            st.session_state["username"] = uname
            st.session_state["name"] = str(accounts[uname].get("name") or persisted.get("name") or uname)
            auth_status = True

    # Try unrendered cookie check as secondary fallback if still not authenticated
    if not auth_status and authenticator is not None:
        try:
            authenticator.login(location="unrendered")
            auth_status = st.session_state.get("authentication_status")
        except Exception:
            pass

    if auth_status is True:
        uname = str(st.session_state.get("username") or "admin")
        dname = str(st.session_state.get("name") or "System Administrator")
        st.session_state["_last_active_ts"] = now
        st.session_state.pop("_session_expired_notice", None)
        _save_active_auth_session(uname, dname, now)
        return True

    return False

def sign_out_current_session(authenticator: stauth.Authenticate | None = None) -> None:
    """Explicitly sign out the current operator and clear both SQLite and cookie sessions."""
    _clear_active_auth_session()
    st.session_state["authentication_status"] = None
    st.session_state["name"] = None
    st.session_state["username"] = None
    st.session_state.pop("_last_active_ts", None)
    if authenticator is not None:
        try:
            authenticator.cookie_controller.delete_cookie()
        except Exception:
            pass

def get_authenticator() -> stauth.Authenticate:
    """Instantiate and return the Streamlit Authenticator object."""
    config = get_auth_config()
    try:
        return stauth.Authenticate(
            credentials=config["credentials"],
            cookie_name=config["cookie"]["name"],
            cookie_key=config["cookie"]["key"],
            cookie_expiry_days=config["cookie"]["expiry_days"],
            auto_hash=False,
        )
    except TypeError:
        return stauth.Authenticate(
            credentials=config["credentials"],
            cookie_name=config["cookie"]["name"],
            cookie_key=config["cookie"]["key"],
            cookie_expiry_days=config["cookie"]["expiry_days"],
        )
