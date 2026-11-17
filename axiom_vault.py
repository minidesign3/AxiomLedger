"""
axiom_vault
===========

Local File Vault paths and raw-upload read/write, extracted from the monolithic
``utils.py`` god-module.

Owns ``data/vault/<Entity>/<TaxYear>/<Bucket>/`` path construction, the two
raw-byte write paths (Streamlit-free ``_save_raw_file_to_vault`` used by the
ingestion pipeline, and the public ``save_raw_file_to_vault``), and the raw-byte
read used by reviewers/exports. Every function here depends only on
``axiom_core``/``axiom_db``, so there is no ``import utils`` cycle to manage.

``export_entity_vault_zip`` stays in ``utils.py`` for now: it also needs
``get_entity_ledger`` and ``export_xero_master_csv``, which move in D9/D10.

``utils.py`` imports and re-exports these symbols, so all existing
``from utils import ...`` call sites keep working unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from axiom_core import _safe_vault_segment
from axiom_db import VAULT_ROOT_DIR, _ensure_sqlite_schema


def get_vault_dir(entity: str, tax_year: str, bucket: str) -> Path:
    """Return the local File Vault directory path `data/vault/<Entity>/<TaxYear>/<Bucket>`."""
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket)
    return VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir


def save_raw_file_to_vault(
    entity: str,
    tax_year: str,
    bucket: str,
    filename: str,
    file_bytes: bytes,
) -> Path:
    """
    Save the original uploaded file (CSV, PDF, Screenshot, Excel/ODT) into the
    human-readable local File Vault: `data/vault/<Entity>/<TaxYear>/<Bucket>/<Filename>`.
    """
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket)
    safe_fname = _safe_vault_segment(filename)

    target_folder = VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir
    target_folder.mkdir(parents=True, exist_ok=True)
    target_path = target_folder / safe_fname
    target_path.write_bytes(file_bytes)
    return target_path


def get_vault_file_bytes(file_rec: dict[str, object]) -> bytes | None:
    """Read and return the original raw file bytes from `data/vault/` (or in-memory cache)."""
    vpath = str(file_rec.get("vault_path") or "").strip()
    if vpath and Path(vpath).exists():
        try:
            return Path(vpath).read_bytes()
        except Exception:
            pass
    raw = file_rec.get("raw_bytes")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return None


def _save_raw_file_to_vault(
    entity: str,
    tax_year: str,
    bucket: str,
    filename: str,
    file_bytes: bytes,
) -> str:
    """
    Save an uploaded file's raw bytes into the organized local File Vault on disk:
    `data/vault/<Entity>/<Year>/<Bucket>/<Filename>`
    Returns the relative vault path string.
    """
    _ensure_sqlite_schema()
    ent_dir = _safe_vault_segment(entity)
    yr_dir = _safe_vault_segment(tax_year)
    bkt_dir = _safe_vault_segment(bucket.lower())
    target_dir = VAULT_ROOT_DIR / ent_dir / yr_dir / bkt_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    clean_fname = Path(filename or "Uploaded_File").name
    clean_fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean_fname).strip() or "Uploaded_File"
    dest_path = target_dir / clean_fname
    try:
        dest_path.write_bytes(file_bytes)
    except Exception:
        pass
    try:
        return str(dest_path.relative_to(Path(__file__).resolve().parent))
    except Exception:
        return str(dest_path)
