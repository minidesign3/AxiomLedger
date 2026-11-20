"""
axiom_secrets
=============

Gemini API key storage that keeps the key OUT of the app database and
``.axiom_memory.json`` (both live inside the project folder and were storing
it in plain text).

Resolution order:
1. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` environment variables
2. ``~/.config/axiomledger/gemini_api_key`` (directory 0700, file 0600 —
   readable only by your OS user), override with ``AXIOM_SECRETS_DIR``.

:func:`migrate_key_out_of_store` moves a key found in the memory store into the
secrets file and blanks it in the store.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_FILE_NAME = "gemini_api_key"


def secrets_dir() -> Path:
    override = os.environ.get("AXIOM_SECRETS_DIR", "").strip()
    return Path(override) if override else Path.home() / ".config" / "axiomledger"


def _key_path() -> Path:
    return secrets_dir() / _FILE_NAME


def load_gemini_key() -> str:
    for var in _ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        return _key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_gemini_key(key: str) -> None:
    """Write (or with an empty key, delete) the private key file."""
    path = _key_path()
    key = (key or "").strip()
    if not key:
        try:
            path.unlink()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    os.chmod(path, 0o600)


def migrate_key_out_of_store(store: dict[str, object]) -> bool:
    """Move ``store['google_genai_api_key']`` to the secrets file; True if the store changed."""
    legacy = str(store.get("google_genai_api_key") or "").strip()
    if not legacy:
        return False
    if not load_gemini_key():
        save_gemini_key(legacy)
    store["google_genai_api_key"] = ""
    return True
