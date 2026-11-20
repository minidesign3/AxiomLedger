"""
axiom_dedupe
============

Duplicate protection for imports.

* **Same file, different name** — uploads were deduplicated by file name only, so
  the same bank export saved under two names was imported twice.
  :func:`find_same_content_record` compares SHA-256 of the bytes against every
  already-imported file in the vault; :func:`duplicate_content_groups` finds
  existing duplicates.
* **Screenshot rows that repeat bank rows** — a screenshot of an account whose
  CSV is also imported repeats those transactions.
  :func:`screenshot_bank_overlap_mask` flags screenshot rows whose date and
  absolute amount match a bank-CSV row.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_bytes(rec: dict[str, object]) -> bytes | None:
    raw = rec.get("raw_bytes")
    if isinstance(raw, (bytes, bytearray)) and raw:
        return bytes(raw)
    vpath = str(rec.get("vault_path", "") or "")
    if not vpath:
        return None
    path = Path(vpath)
    if not path.is_absolute():
        path = _ROOT / path
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def record_hash(rec: dict[str, object]) -> str | None:
    """Content hash of a file record (cached on the record as ``content_hash``)."""
    cached = rec.get("content_hash")
    if isinstance(cached, str) and cached:
        return cached
    data = _record_bytes(rec)
    if data is None:
        return None
    rec["content_hash"] = content_hash(data)
    return rec["content_hash"]


def find_same_content_record(data: bytes, records: list[dict[str, object]]) -> dict[str, object] | None:
    """Return an existing record whose file bytes are identical to ``data``."""
    target = content_hash(data)
    for rec in records:
        if record_hash(rec) == target:
            return rec
    return None


def duplicate_content_groups(records: list[dict[str, object]]) -> list[list[str]]:
    """Names of already-imported files that share identical contents (2+ per group)."""
    groups: dict[str, list[str]] = {}
    for rec in records:
        digest = record_hash(rec)
        if digest:
            groups.setdefault(digest, []).append(str(rec.get("name", "")))
    return [names for names in groups.values() if len(names) > 1]


def screenshot_bank_overlap_mask(rows: pd.DataFrame) -> pd.Series:
    """True for screenshot rows whose (date, |amount|) also appears in a bank-CSV row.

    Expects columns ``_date`` (parsed date), ``Amount`` (float) and ``_bucket``.
    Zero-amount rows never count as overlaps.
    """
    if rows.empty:
        return pd.Series(False, index=rows.index)
    amount = pd.to_numeric(rows["Amount"], errors="coerce").fillna(0.0).abs().round(2)
    keys = list(zip(rows["_date"], amount, strict=True))
    bank_keys = {k for k, b in zip(keys, rows["_bucket"], strict=True) if b == "csv" and k[0] is not None}
    return pd.Series(
        [b == "screenshots" and k[1] != 0.0 and k[0] is not None and k in bank_keys
         for k, b in zip(keys, rows["_bucket"], strict=True)],
        index=rows.index,
    )
