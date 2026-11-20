"""
axiom_ledger_sync
=================

Keeps the master ledger and the per-file records in step, and tags every ledger
row with the file it came from.

The master ledger is rebuilt as the concatenation of every file record's rows
(``sync_entity_ledger_from_files``). Edits made on the Master sheet were only
written to the ledger, so the next file-level save silently discarded them.
:func:`push_ledger_to_files` writes ledger rows back into their file records
(the first N ledger rows map positionally onto the N file rows), so both stay
identical. Extra ledger rows (e.g. appended from a workbook import) are kept
in the ledger and reported as "Master ledger only".
"""

from __future__ import annotations

import pandas as pd

from axiom_core import STANDARD_COLUMNS
from axiom_pm_rows import is_pm_row

LEDGER_ONLY = "Master ledger only"
_BUCKET_LABELS = {"csv": "Bank CSV", "pdf": "PDF", "screenshots": "Screenshot", "other": "Spreadsheet/Doc"}


def _file_frames(records: list[dict[str, object]]) -> list[tuple[dict[str, object], pd.DataFrame]]:
    out = []
    for rec in records:
        df = rec.get("df")
        if isinstance(df, pd.DataFrame) and not df.empty:
            out.append((rec, df))
    return out


def file_row_count(records: list[dict[str, object]]) -> int:
    return sum(len(df) for _, df in _file_frames(records))


def ledger_with_sources(ledger: pd.DataFrame, records: list[dict[str, object]]) -> tuple[pd.DataFrame, bool]:
    """Ledger rows + ``Source File`` / ``Source Type`` / ``_bucket``; bool = aligned with files."""
    base = ledger.reindex(columns=STANDARD_COLUMNS).reset_index(drop=True).copy()
    names: list[str] = []
    buckets: list[str] = []
    for rec, df in _file_frames(records):
        names.extend([str(rec.get("name", ""))] * len(df))
        buckets.extend([str(rec.get("bucket", ""))] * len(df))
    aligned = len(base) >= len(names)
    if not aligned:
        names, buckets = [], []
    extra = len(base) - len(names)
    base["Source File"] = names + [LEDGER_ONLY] * extra
    base["_bucket"] = buckets + ["ledger"] * extra
    types = []
    for bucket, desc in zip(base["_bucket"], base["Description"], strict=True):
        types.append("PM Statement" if bucket == "pdf" and is_pm_row(desc) else _BUCKET_LABELS.get(bucket, "Ledger"))
    base["Source Type"] = types
    return base, aligned


def push_ledger_to_files(ledger: pd.DataFrame, records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Copy ledger rows back into their file records; returns the records that changed.

    Returns ``[]`` (no write-back) when the ledger is shorter than the files,
    i.e. the two can't be aligned safely.
    """
    base = ledger.reindex(columns=STANDARD_COLUMNS).reset_index(drop=True)
    if len(base) < file_row_count(records):
        return []
    changed: list[dict[str, object]] = []
    pos = 0
    for rec, df in _file_frames(records):
        block = base.iloc[pos : pos + len(df)].reset_index(drop=True)
        pos += len(df)
        current = df.reindex(columns=STANDARD_COLUMNS).reset_index(drop=True)
        if not block.astype(str).equals(current.astype(str)):
            rec["df"] = block.copy()
            changed.append(rec)
    return changed


def save_ledger_everywhere(ledger: pd.DataFrame, entity: str, tax_year: str) -> int:
    """Write the ledger AND push the same rows into the file records (persisted)."""
    from axiom_db import _save_file_record_to_sqlite
    from axiom_ingestion import get_entity_uploaded_files
    from axiom_ledger import set_entity_ledger

    records = get_entity_uploaded_files(entity, tax_year)
    changed = push_ledger_to_files(ledger, records)
    for rec in changed:
        _save_file_record_to_sqlite(entity, tax_year, rec)
    set_entity_ledger(ledger, entity, tax_year)
    return len(changed)
