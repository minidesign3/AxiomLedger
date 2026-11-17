"""Ingestion orchestration tests (phase D15a): no UI, no real vault, no real DB."""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_dataframe
import axiom_ingestion
import axiom_vault
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

ENTITY = "Alpha Trust"
YEAR = "2026"
CSV_PREAMBLE = "ASB Bank Export\nAccount: 12-3456-0123456-00\n"


def _frame(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(axiom_vault, "VAULT_ROOT_DIR", vault_root)
    monkeypatch.setattr(axiom_ingestion, "init_workspace_state", lambda: None)
    monkeypatch.setattr(axiom_ingestion, "get_active_entity", lambda e=None: e or ENTITY)
    monkeypatch.setattr(axiom_ingestion, "get_active_tax_year", lambda y=None: y or YEAR)
    monkeypatch.setattr(axiom_ingestion, "apply_rule_categorization", lambda df, **kw: df)
    monkeypatch.setattr(axiom_ingestion, "_save_file_record_to_sqlite", lambda *a, **k: None)
    monkeypatch.setattr(axiom_dataframe, "detect_property_from_text", lambda text, entity=None: "")

    saved = {}
    monkeypatch.setattr(
        axiom_ingestion,
        "_save_master_ledger_to_sqlite",
        lambda entity, year, df: saved.__setitem__("master", (entity, year, df)),
    )
    ledgers = {}
    monkeypatch.setattr(
        axiom_ingestion,
        "set_entity_ledger",
        lambda df, entity=None, year=None: (
            ledgers.__setitem__("last", df.copy()) or ledgers["last"]
        ),
    )
    return {"vault": vault_root, "saved": saved, "ledgers": ledgers}


def _vault_dir(env, bucket):
    path = env["vault"] / ENTITY / YEAR / bucket
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# reconciliation
# --------------------------------------------------------------------------- #
def test_repairs_zero_row_csv_record(env):
    csv_dir = _vault_dir(env, "csv")
    bank = csv_dir / "asb.csv"
    bank.write_text(CSV_PREAMBLE + "Date,Amount,Payee,Particulars\n2026-01-02,-12.50,Countdown,Groceries\n")

    rec = {
        "id": "file_1",
        "name": "asb.csv",
        "bucket": "csv",
        "vault_path": str(bank),
        "df": pd.DataFrame(columns=STANDARD_COLUMNS),
        "note": "Saved to Vault (parse warning: boom)",
    }

    out = axiom_ingestion._reconcile_vault_and_broken_csvs(ENTITY, YEAR, [rec])

    assert len(out) == 1
    assert len(out[0]["df"]) == 1
    assert "Countdown" in out[0]["df"].iloc[0]["Description"]
    assert out[0]["note"] == ""
    assert env["saved"]["master"][0] == ENTITY
    assert len(env["saved"]["master"][2]) == 1


def test_recovers_orphaned_files_and_skips_noise(env):
    _vault_dir(env, "csv").joinpath("orphan.csv").write_text(
        "Date,Amount,Description\n2026-02-01,-5.00,Orphan Row\n"
    )
    _vault_dir(env, "screenshots").joinpath("shot.png").write_bytes(b"png")
    _vault_dir(env, "screenshots").joinpath("shot.png.ocr.md").write_text("# ocr")
    _vault_dir(env, "pdf").joinpath(".DS_Store").write_bytes(b"x")
    _vault_dir(env, "other").joinpath("known.csv").write_text("a,b\n1,2\n")

    existing = {
        "id": "file_9",
        "name": "known.csv",
        "bucket": "other",
        "df": pd.DataFrame(columns=STANDARD_COLUMNS),
    }

    out = axiom_ingestion._reconcile_vault_and_broken_csvs(ENTITY, YEAR, [existing])

    names = [r["name"] for r in out]
    assert names == ["known.csv", "orphan.csv", "shot.png"]

    shot = next(r for r in out if r["name"] == "shot.png")
    assert shot["needs_ocr"] is True
    assert shot["raw_bytes"] == b"png"
    assert len(shot["df"]) == 1
    assert "run ai ocr" in shot["note"].lower()

    orphan = next(r for r in out if r["name"] == "orphan.csv")
    assert orphan["df"].iloc[0]["Description"] == "Orphan Row"
    assert "master" in env["saved"]


def test_no_changes_leaves_master_ledger_alone(env):
    _vault_dir(env, "csv")  # empty bucket
    axiom_ingestion._reconcile_vault_and_broken_csvs(ENTITY, YEAR, [])
    assert "master" not in env["saved"]


# --------------------------------------------------------------------------- #
# uploaded file list
# --------------------------------------------------------------------------- #
def test_get_entity_uploaded_files_caches_per_session(env, monkeypatch):
    calls = []

    def fake_load(entity, year):
        calls.append((entity, year))
        return [{"id": "f1", "name": "a.csv", "bucket": "csv", "df": _frame([])}]

    monkeypatch.setattr(axiom_ingestion, "_load_uploaded_files_from_sqlite", fake_load)
    monkeypatch.setattr(
        axiom_ingestion,
        "_reconcile_vault_and_broken_csvs",
        lambda entity, year, recs: recs,
    )
    state = {"_sqlite_loaded_files": set()}
    monkeypatch.setattr(axiom_ingestion.st, "session_state", state)

    files = axiom_ingestion.get_entity_uploaded_files()
    axiom_ingestion.get_entity_uploaded_files()

    assert len(files) == 1
    assert calls == [(ENTITY, YEAR)]

    axiom_ingestion.get_entity_uploaded_files(tax_year="2025")
    assert len(calls) == 2


def test_sync_entity_ledger_concatenates_files(env, monkeypatch):
    monkeypatch.setattr(axiom_ingestion, "get_entity_uploaded_files", lambda *a, **k: [
        {"df": _frame([{"Date": "2026-01-01", "Amount": 1.0, "Description": "a",
                        "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"}])},
        {"df": _frame([{"Date": "2026-01-02", "Amount": 2.0, "Description": "b",
                        "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"}])},
    ])

    out = axiom_ingestion.sync_entity_ledger_from_files()

    assert list(out["Description"]) == ["a", "b"]
    assert env["ledgers"]["last"].equals(out)


def test_sync_entity_ledger_with_no_files_is_empty(env, monkeypatch):
    monkeypatch.setattr(axiom_ingestion, "get_entity_uploaded_files", lambda *a, **k: [])
    out = axiom_ingestion.sync_entity_ledger_from_files()
    assert out.empty
    assert list(out.columns) == STANDARD_COLUMNS


# --------------------------------------------------------------------------- #
# memory training
# --------------------------------------------------------------------------- #
def test_train_memory_from_coded_dataframe(env, monkeypatch):
    monkeypatch.setattr(axiom_ingestion, "normalize_financial_dataframe", lambda df, **kw: df)
    saved_rules = []
    props = ["General / Portfolio"]

    def fake_save(**kw):
        saved_rules.append(kw)
        if "33 Blampied" not in props:
            props.append("33 Blampied")
        return True, "ok"

    monkeypatch.setattr(axiom_ingestion, "save_memorized_rule", fake_save)
    monkeypatch.setattr(axiom_ingestion, "get_entity_properties", lambda entity=None: list(props))

    df = _frame(
        [
            {"Date": "2026-01-01", "Amount": 1.0, "Description": "Countdown",
             "Property": "33 Blampied", "Account Code": "Groceries"},
            {"Date": "2026-01-02", "Amount": 2.0, "Description": "Mystery",
             "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"},
            {"Date": "2026-01-03", "Amount": 3.0, "Description": "", "Property": "33 Blampied",
             "Account Code": "Rates"},
        ]
    )

    learned, new_props = axiom_ingestion.train_memory_from_coded_dataframe(df)

    assert learned == 1
    assert new_props == 1
    assert saved_rules[0]["pattern"] == "Countdown"
    assert saved_rules[0]["source"] == "2026 Coded Accounts"


def test_train_memory_with_empty_frame_returns_zeros(env):
    assert axiom_ingestion.train_memory_from_coded_dataframe(pd.DataFrame()) == (0, 0)


# --------------------------------------------------------------------------- #
# red / unsure counts
# --------------------------------------------------------------------------- #
def test_get_file_red_counts(env, monkeypatch):
    tiers = {"good": "green", "maybe": "yellow", "nope": "red"}
    monkeypatch.setattr(
        axiom_ingestion,
        "evaluate_row_traffic_light",
        lambda property_val, account_code, description="", entity=None: (
            tiers[str(description)],
            "",
        ),
    )
    df = _frame(
        [
            {"Date": "2026-01-01", "Amount": 1.0, "Description": "good",
             "Property": "33 Blampied", "Account Code": "Rates"},
            {"Date": "2026-01-02", "Amount": 2.0, "Description": "maybe",
             "Property": "33 Blampied", "Account Code": "Uncategorized"},
            {"Date": "2026-01-03", "Amount": 3.0, "Description": "nope",
             "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"},
        ]
    )

    counts = axiom_ingestion.get_file_red_counts(df)

    assert counts == {
        "total": 3,
        "green": 1,
        "yellow": 1,
        "red": 1,
        "certain": 1,
        "needs_property": 0,
        "unknown_code": 2,
        "red_total": 2,
    }


def test_get_file_red_counts_empty(env):
    assert axiom_ingestion.get_file_red_counts(pd.DataFrame())["total"] == 0
