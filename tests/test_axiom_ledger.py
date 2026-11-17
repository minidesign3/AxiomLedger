"""Ledger IO tests (phase D9).

Every test runs against a throwaway SQLite database / vault root in ``tmp_path``
and a stubbed memory store, so no repository data files are read or written.
"""

from __future__ import annotations

import io
import threading
import zipfile

import pandas as pd
import pytest
import streamlit as st

import axiom_dataframe
import axiom_db
import axiom_ledger
import axiom_vault
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

_SESSION_KEYS = (
    "entity_year_ledgers",
    "entity_ledgers",
    "_sqlite_loaded_ledgers",
    "entity_uploaded_files",
)
_MISSING = object()


def _df(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


@pytest.fixture()
def ledger_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    vault_root = data_dir / "vault"
    conn_cache: dict = {}
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", vault_root)
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", conn_cache)
    monkeypatch.setattr(axiom_vault, "VAULT_ROOT_DIR", vault_root)
    monkeypatch.setattr(axiom_ledger, "VAULT_ROOT_DIR", vault_root)

    monkeypatch.setattr(axiom_ledger, "init_workspace_state", lambda: None)
    monkeypatch.setattr(axiom_ledger, "get_active_entity", lambda entity=None: entity or "Alpha")
    monkeypatch.setattr(axiom_ledger, "get_active_tax_year", lambda year=None: year or "2026")
    monkeypatch.setattr(axiom_ledger, "apply_rule_categorization", lambda df, entity=None: df)
    # Property detection would otherwise read the real .axiom_memory.json.
    monkeypatch.setattr(axiom_dataframe, "detect_property_from_text", lambda text: UNASSIGNED_PROPERTY)

    saved_state = {key: st.session_state.get(key, _MISSING) for key in _SESSION_KEYS}
    st.session_state["entity_year_ledgers"] = {}
    st.session_state["entity_ledgers"] = {}
    st.session_state["_sqlite_loaded_ledgers"] = set()
    st.session_state["entity_uploaded_files"] = {}

    try:
        yield {"data_dir": data_dir, "vault_root": vault_root}
    finally:
        for conn in list(conn_cache.values()):
            try:
                conn.close()
            except Exception:
                pass
        for key, original in saved_state.items():
            if original is _MISSING:
                st.session_state.pop(key, None)
            else:
                st.session_state[key] = original


# --------------------------------------------------------------------------- #
# set / get / append
# --------------------------------------------------------------------------- #
def test_set_then_get_roundtrips_through_sqlite(ledger_env):
    df = _df([["2026-01-01", -12.5, "Countdown", UNASSIGNED_PROPERTY, "Materials"]])

    saved = axiom_ledger.set_entity_ledger(df, "Alpha", "2026")
    assert saved.loc[0, "Amount"] == -12.5

    # Drop every in-memory cache to prove the read comes back from SQLite.
    st.session_state["entity_year_ledgers"] = {}
    st.session_state["entity_ledgers"] = {}
    st.session_state["_sqlite_loaded_ledgers"] = set()

    loaded = axiom_ledger.get_entity_ledger("Alpha", "2026")
    assert list(loaded.columns) == STANDARD_COLUMNS
    assert loaded["Description"].tolist() == ["Countdown"]


def test_ledgers_are_isolated_by_entity_and_year(ledger_env):
    axiom_ledger.set_entity_ledger(_df([["2026-01-01", 1.0, "A", "P", "C"]]), "Alpha", "2026")
    axiom_ledger.set_entity_ledger(_df([["2026-01-01", 2.0, "B", "P", "C"]]), "Beta", "2026")
    axiom_ledger.set_entity_ledger(_df([["2025-01-01", 3.0, "C", "P", "C"]]), "Alpha", "2025")

    st.session_state["entity_year_ledgers"] = {}
    st.session_state["entity_ledgers"] = {}
    st.session_state["_sqlite_loaded_ledgers"] = set()

    alpha = axiom_ledger.get_entity_ledger("Alpha", "2026")
    beta = axiom_ledger.get_entity_ledger("Beta", "2026")
    alpha_2025 = axiom_ledger.get_entity_ledger("Alpha", "2025")

    assert alpha["Description"].tolist() == ["A"]
    assert beta["Description"].tolist() == ["B"]
    assert alpha_2025["Description"].tolist() == ["C"]


def test_set_entity_ledger_reorders_and_cleans_amounts(ledger_env):
    messy = pd.DataFrame(
        [{"Description": "Power", "Amount": "$1,200.50", "Account Code": "Utilities"}]
    )
    saved = axiom_ledger.set_entity_ledger(messy, "Alpha", "2026")
    assert list(saved.columns) == STANDARD_COLUMNS
    assert saved.loc[0, "Amount"] == 1200.5
    assert pd.isna(saved.loc[0, "Date"])


def test_append_entity_ledger_concatenates(ledger_env):
    axiom_ledger.set_entity_ledger(_df([["2026-01-01", 1.0, "A", "P", "C"]]), "Alpha", "2026")
    combined = axiom_ledger.append_entity_ledger(
        _df([["2026-01-02", 2.0, "B", "P", "C"]]), "Alpha", "2026"
    )
    assert combined["Description"].tolist() == ["A", "B"]

    st.session_state["entity_year_ledgers"] = {}
    st.session_state["_sqlite_loaded_ledgers"] = set()
    assert axiom_ledger.get_entity_ledger("Alpha", "2026")["Description"].tolist() == ["A", "B"]


# --------------------------------------------------------------------------- #
# is_property_unassigned
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("prop", "code", "portfolio_wide", "expected"),
    [
        (None, "Materials", False, True),
        ("", "Materials", False, True),
        ("  ", "Materials", False, True),
        ("Unassigned", "Materials", False, True),
        ("Select Property", "Materials", False, True),
        ("9 Damian", "Materials", False, False),
        (None, "Legal and Accounting", True, False),
    ],
)
def test_is_property_unassigned(ledger_env, monkeypatch, prop, code, portfolio_wide, expected):
    monkeypatch.setattr(axiom_ledger, "is_portfolio_wide_code", lambda c, entity=None: portfolio_wide)
    assert axiom_ledger.is_property_unassigned(prop, code) is expected


# --------------------------------------------------------------------------- #
# export_entity_vault_zip
# --------------------------------------------------------------------------- #
def test_export_entity_vault_zip_contains_master_csv_and_originals(ledger_env):
    bucket_dir = ledger_env["vault_root"] / "Alpha" / "2026" / "csv"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "statement.csv").write_text("Date,Amount\n2026-01-01,1\n", encoding="utf-8")

    axiom_ledger.set_entity_ledger(_df([["2026-01-01", 1.0, "Rent", "P", "C"]]), "Alpha", "2026")
    payload = axiom_ledger.export_entity_vault_zip("Alpha", "2026")

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
        master = zf.read("Alpha_2026_Master_Coded_Ledger.csv").decode()
        original = zf.read("Original_Files/csv/statement.csv").decode()

    assert "Original_Files/csv/statement.csv" in names
    assert master.splitlines()[0].split(",") == STANDARD_COLUMNS
    assert "Rent" in master
    assert original.startswith("Date,Amount")


# --------------------------------------------------------------------------- #
# _load_uploaded_files_from_sqlite
# --------------------------------------------------------------------------- #
def _record(name: str, fid: str, rows: list[list[object]], **extra) -> dict:
    record = {
        "id": fid,
        "name": name,
        "signature": f"{name}::1",
        "bucket": "csv",
        "vault_path": "",
        "mime_type": "",
        "uploaded_at": "2026-01-01 00:00:00",
        "needs_ocr": False,
        "note": "",
        "df": _df(rows),
    }
    record.update(extra)
    return record


def test_load_uploaded_files_restores_records_and_rows(ledger_env):
    axiom_db._save_file_record_to_sqlite(
        "Alpha",
        "2026",
        _record("a.csv", "file_1", [["2026-01-01", -5.0, "Shop", "9 Damian", "Materials"]]),
    )

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert len(loaded) == 1
    rec = loaded[0]
    assert rec["id"] == "file_1"
    assert rec["name"] == "a.csv"
    assert rec["needs_ocr"] is False
    assert rec["df"]["Description"].tolist() == ["Shop"]
    assert rec["df"]["Property"].tolist() == ["9 Damian"]


def test_load_uploaded_files_reads_screenshot_bytes(ledger_env, tmp_path):
    shot = tmp_path / "vault_shot.png"
    shot.write_bytes(b"\x89PNG fake")
    axiom_db._save_file_record_to_sqlite(
        "Alpha",
        "2026",
        _record("shot.png", "file_2", [], bucket="screenshots", vault_path=str(shot)),
    )

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert loaded[0]["raw_bytes"] == b"\x89PNG fake"
    assert loaded[0]["df"].empty


def test_load_uploaded_files_repairs_zero_row_csv_with_parse_warning(ledger_env, tmp_path):
    csv_path = tmp_path / "preamble.csv"
    csv_path.write_text("ASB Bank\nDate,Amount,Payee\n2026-01-01,-9.00,Shop\n", encoding="utf-8")
    axiom_db._save_file_record_to_sqlite(
        "Alpha",
        "2026",
        _record("preamble.csv", "file_3", [], note="CSV parse warning", vault_path=str(csv_path)),
    )

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert loaded[0]["note"] == ""
    assert loaded[0]["df"]["Description"].tolist() == ["Shop"]
    assert loaded[0]["df"]["Amount"].tolist() == [-9.0]
    # Repaired rows are pushed into the master ledger table.
    assert axiom_db._load_master_ledger_from_sqlite("Alpha", "2026")["Description"].tolist() == ["Shop"]


def test_load_uploaded_files_recovers_orphaned_vault_csv(ledger_env):
    bucket_dir = ledger_env["vault_root"] / "Alpha" / "2026" / "csv"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "orphan.csv").write_text(
        "Date,Amount,Payee\n2026-02-01,-4.50,Cafe\n", encoding="utf-8"
    )

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert [r["name"] for r in loaded] == ["orphan.csv"]
    assert loaded[0]["bucket"] == "csv"
    assert loaded[0]["vault_path"].endswith("orphan.csv")
    assert loaded[0]["df"]["Amount"].tolist() == [-4.5]
    assert axiom_db._load_master_ledger_from_sqlite("Alpha", "2026")["Description"].tolist() == ["Cafe"]


def test_load_uploaded_files_recovers_orphaned_screenshot_as_needs_ocr(ledger_env):
    bucket_dir = ledger_env["vault_root"] / "Alpha" / "2026" / "screenshots"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "receipt_image.png").write_bytes(b"\x89PNG")

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert len(loaded) == 1
    rec = loaded[0]
    assert rec["needs_ocr"] is True
    assert rec["mime_type"] == "image/png"
    assert rec["df"]["Description"].tolist() == ["receipt image"]
    assert rec["df"]["Account Code"].tolist() == ["Uncategorized"]


def test_load_uploaded_files_skips_ocr_sidecar_files(ledger_env):
    bucket_dir = ledger_env["vault_root"] / "Alpha" / "2026" / "screenshots"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "receipt_image.png").write_bytes(b"\x89PNG")
    (bucket_dir / "receipt_image.png.ocr.md").write_text("# ocr", encoding="utf-8")

    loaded = axiom_ledger._load_uploaded_files_from_sqlite("Alpha", "2026")

    assert [r["name"] for r in loaded] == ["receipt_image.png"]
