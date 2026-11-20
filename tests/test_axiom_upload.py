"""Upload pipeline tests (phase D15b): no UI, no network, no real vault."""

from __future__ import annotations

import pandas as pd
import pytest

import axiom_upload
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY

ENTITY = "Alpha Trust"
YEAR = "2026"


def _frame(rows):
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


class _Upload:
    def __init__(self, name: str, data: bytes, mime: str = ""):
        self.name = name
        self._data = data
        self.type = mime

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def env(monkeypatch, tmp_path):
    state = {"google_genai_api_key": ""}
    files: list[dict[str, object]] = []
    calls = {"sync": 0, "md": [], "saved": [], "deleted": []}

    monkeypatch.setattr(axiom_upload.st, "session_state", state)
    monkeypatch.setattr(axiom_upload, "get_active_entity", lambda e=None: e or ENTITY)
    monkeypatch.setattr(axiom_upload, "get_active_tax_year", lambda y=None: y or YEAR)
    monkeypatch.setattr(axiom_upload, "get_entity_uploaded_files", lambda *a, **k: files)
    monkeypatch.setattr(axiom_upload, "apply_rule_categorization", lambda df, **kw: df)
    monkeypatch.setattr(axiom_upload, "normalize_financial_dataframe", lambda df, **kw: df)
    monkeypatch.setattr(
        axiom_upload,
        "_save_raw_file_to_vault",
        lambda entity, year, bucket, name, data: f"data/vault/{entity}/{year}/{bucket}/{name}",
    )
    monkeypatch.setattr(
        axiom_upload,
        "_save_file_record_to_sqlite",
        lambda entity, year, rec, **kw: calls["saved"].append(rec),
    )
    monkeypatch.setattr(
        axiom_upload,
        "_delete_file_record_from_sqlite",
        lambda file_id, **kw: calls["deleted"].append(file_id),
    )
    monkeypatch.setattr(
        axiom_upload,
        "sync_entity_ledger_from_files",
        lambda *a, **k: calls.__setitem__("sync", calls["sync"] + 1),
    )
    monkeypatch.setattr(axiom_upload, "save_screenshot_ocr_markdown", _fake_md(calls))
    monkeypatch.setattr(axiom_upload, "extract_pdf_tables", lambda b: _frame([]))
    monkeypatch.setattr(axiom_upload, "extract_other_document_tables", lambda b, name="": _frame([]))
    return {"state": state, "files": files, "calls": calls, "tmp": tmp_path}


def _fake_md(calls):
    def fake_markdown(rec, entity, year, ocr_meta=None):
        calls["md"].append(rec.get("name"))
        return f"data/vault/{entity}/{year}/screenshots/{rec.get('name')}.ocr.md"

    return fake_markdown


# --------------------------------------------------------------------------- #
# ingest_uploaded_file_object
# --------------------------------------------------------------------------- #
def test_csv_upload_is_parsed_and_synced(env):
    upload = _Upload("asb.csv", b"Date,Amount,Description\n2026-01-01,-9.99,Coffee\n")

    ok, msg, rec = axiom_upload.ingest_uploaded_file_object(upload)

    assert ok is True
    assert "1 rows" in msg
    assert rec["bucket"] == "csv"
    assert rec["df"].iloc[0]["Description"] == "Coffee"
    assert rec["note"] == ""
    assert env["files"] == [rec]
    assert env["calls"]["sync"] == 1
    assert env["calls"]["saved"][-1] is rec


def test_same_contents_under_new_name_is_not_imported(env, monkeypatch):
    data = b"Date,Amount,Description\n2026-01-01,-9.99,Coffee\n"
    existing = {"id": "f1", "name": "bank-export.csv", "bucket": "csv", "df": _frame([]), "raw_bytes": data}
    env["files"].append(existing)
    toasts: list[str] = []
    monkeypatch.setattr(axiom_upload.st, "toast", lambda msg: toasts.append(msg), raising=False)

    ok, msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("bank-loan-copy.csv", data))

    assert ok is True and rec is existing
    assert "same contents" in msg and "bank-export.csv" in msg
    assert env["calls"]["saved"] == [] and env["calls"]["sync"] == 0
    assert toasts and toasts[0].startswith("⚠️")


def test_duplicate_upload_is_not_saved_twice(env):
    upload = _Upload("asb.csv", b"Date,Amount,Description\n2026-01-01,-9.99,Coffee\n")
    existing = {"id": "f1", "name": "asb.csv", "signature": "", "bucket": "csv", "df": _frame([])}
    env["files"].append(existing)

    ok, msg, rec = axiom_upload.ingest_uploaded_file_object(upload)

    assert ok is True
    assert rec is existing
    assert "already saved" in msg
    assert env["calls"]["sync"] == 0


def test_zero_row_csv_with_parse_warning_is_reingested(env):
    upload = _Upload("asb.csv", b"Date,Amount,Description\n2026-01-01,-9.99,Coffee\n")
    bad = {
        "id": "f0",
        "name": "asb.csv",
        "bucket": "csv",
        "df": pd.DataFrame(columns=STANDARD_COLUMNS),
        "note": "Saved to Vault (parse warning: boom)",
    }
    env["files"].append(bad)

    ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(upload)

    assert ok is True
    assert env["calls"]["deleted"] == ["f0"]
    assert rec is not bad
    assert bad not in env["files"]


def test_duplicate_screenshot_runs_ocr_when_pending(env, monkeypatch):
    ran = []
    existing = {"id": "f2", "name": "shot.png", "bucket": "screenshots", "needs_ocr": True}
    env["files"].append(existing)
    monkeypatch.setattr(
        axiom_upload, "run_ocr_on_screenshot_record", lambda rec, **kw: ran.append(rec) or (True, "ok", 1)
    )

    ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("shot.png", b"png", "image/png"))

    assert ok is True and rec is existing and ran == [existing]


def test_screenshot_without_api_key_is_queued_for_ocr(env):
    upload = _Upload("receipt.png", b"png-bytes", "image/png")

    ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(upload)

    assert ok is True
    assert rec["needs_ocr"] is True
    assert rec["raw_bytes"] == b"png-bytes"
    assert len(rec["df"]) == 1
    assert rec["df"].iloc[0]["Account Code"] == "Uncategorized"
    assert "open file" in rec["note"]
    assert env["calls"]["md"] == []


def test_screenshot_ocr_on_upload(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"
    ocr_df = _frame(
        [
            {"Date": "2026-01-05", "Amount": -12.0, "Description": "Fuel",
             "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"},
        ]
    )
    monkeypatch.setattr(
        axiom_upload,
        "extract_screenshot_structured_gemini",
        lambda **kw: {"df": ocr_df, "notes": ""},
    )

    ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("fuel.png", b"png", "image/png"))

    assert ok is True
    assert rec["needs_ocr"] is False
    assert "Auto-OCR" in rec["note"]
    assert rec["ocr_md_path"].endswith("fuel.png.ocr.md")
    assert env["calls"]["md"] == ["fuel.png"]


def test_screenshot_ocr_summary_only(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"
    monkeypatch.setattr(
        axiom_upload,
        "extract_screenshot_structured_gemini",
        lambda **kw: {"df": pd.DataFrame(), "notes": "ASB loan summary 5.19%"},
    )

    _ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("loan.png", b"png", "image/png"))

    assert rec["note"] == "ASB loan summary 5.19% · Saved .ocr.md"


def test_screenshot_ocr_failure_marks_needs_ocr(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"

    def boom(**kw):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(axiom_upload, "extract_screenshot_structured_gemini", boom)

    _ok, _msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("x.png", b"png", "image/png"))

    assert rec["needs_ocr"] is True
    assert rec["note"].startswith("OCR pending (model unavailable")


def test_parse_failure_is_recorded_as_warning(env, monkeypatch):
    def boom(_bytes):
        raise ValueError("bad csv")

    monkeypatch.setattr(axiom_upload, "_read_bank_csv_bytes", boom)

    ok, msg, rec = axiom_upload.ingest_uploaded_file_object(_Upload("x.csv", b"junk"))

    assert ok is True
    assert "parse warning: bad csv" in rec["note"]
    assert rec["df"].empty
    assert "0 rows" in msg


# --------------------------------------------------------------------------- #
# run_ocr_on_screenshot_record
# --------------------------------------------------------------------------- #
def test_run_ocr_requires_api_key(env):
    ok, msg, count = axiom_upload.run_ocr_on_screenshot_record({"name": "a.png"})
    assert (ok, count) == (False, 0)
    assert "No Gemini API Key" in msg


def test_run_ocr_requires_image_bytes(env):
    env["state"]["google_genai_api_key"] = "key"
    ok, msg, count = axiom_upload.run_ocr_on_screenshot_record({"name": "a.png", "vault_path": "missing.png"})
    assert (ok, count) == (False, 0)
    assert "Could not load image bytes" in msg


def test_run_ocr_reads_vault_bytes_and_updates_record(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"
    shot = env["tmp"] / "shot.png"
    shot.write_bytes(b"png-bytes")
    rec = {"id": "f3", "name": "shot.png", "vault_path": str(shot), "df": _frame([])}
    ocr_df = _frame(
        [
            {"Date": "2026-01-05", "Amount": -3.5, "Description": "Parking",
             "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"},
        ]
    )
    monkeypatch.setattr(
        axiom_upload, "extract_screenshot_structured_gemini", lambda **kw: {"df": ocr_df, "notes": ""}
    )

    ok, msg, count = axiom_upload.run_ocr_on_screenshot_record(rec)

    assert ok is True and count == 1
    assert rec["raw_bytes"] == b"png-bytes"
    assert rec["needs_ocr"] is False
    assert "OCR complete (1 rows)" in rec["note"]
    assert env["calls"]["sync"] == 1
    assert "1 transaction(s)" in msg


def test_run_ocr_summary_only(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"
    rec = {"id": "f4", "name": "loan.png", "raw_bytes": b"png", "df": _frame([])}
    monkeypatch.setattr(
        axiom_upload,
        "extract_screenshot_structured_gemini",
        lambda **kw: {"df": None, "notes": "Loan summary"},
    )

    ok, msg, count = axiom_upload.run_ocr_on_screenshot_record(rec)

    assert ok is True and count == 0
    assert rec["note"] == "Loan summary · Saved .ocr.md"
    assert "saved .ocr.md summary" in msg


def test_run_ocr_failure(env, monkeypatch):
    env["state"]["google_genai_api_key"] = "key"
    rec = {"id": "f5", "name": "bad.png", "raw_bytes": b"png", "df": _frame([])}

    def boom(**kw):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(axiom_upload, "extract_screenshot_structured_gemini", boom)

    ok, msg, count = axiom_upload.run_ocr_on_screenshot_record(rec)

    assert (ok, count) == (False, 0)
    assert rec["needs_ocr"] is True
    assert rec["note"] == "OCR error: quota exceeded"
    assert "OCR failed on 'bad.png'" in msg


# --------------------------------------------------------------------------- #
# import_codes_workbook_into_entity
# --------------------------------------------------------------------------- #
def test_import_codes_workbook(env, monkeypatch):
    parsed = {
        "account_codes": ["Rates", "Insurance"],
        "properties": ["33 Blampied"],
        "payee_code_rules": [{"pattern": "Countdown", "property": "33 Blampied", "account_code": "Groceries"}],
        "combined_bank_df": _frame(
            [{"Date": "2026-01-01", "Amount": 1.0, "Description": "x",
              "Property": UNASSIGNED_PROPERTY, "Account Code": "Uncategorized"}]
        ),
    }
    monkeypatch.setattr(axiom_upload, "parse_coded_workbook_sheets", lambda *a, **k: parsed)
    monkeypatch.setattr(axiom_upload, "add_entity_account_code", lambda code, entity=None: (True, "ok"))
    monkeypatch.setattr(axiom_upload, "add_entity_property", lambda prop, entity=None: (True, "ok"))
    rules = []
    monkeypatch.setattr(
        axiom_upload, "save_memorized_rule", lambda **kw: (rules.append(kw) or True, "ok")
    )
    appended = []
    monkeypatch.setattr(
        axiom_upload,
        "append_entity_ledger",
        lambda df, entity=None, tax_year=None: appended.append(df),
    )
    env["files"].append({"id": "f1", "name": "a.csv", "df": _frame([{"Date": "2026-01-01"}])})

    result = axiom_upload.import_codes_workbook_into_entity(b"wb", import_bank_transactions=True)

    assert result["added_codes"] == 2
    assert result["added_properties"] == 1
    assert result["saved_rules"] == 1
    assert result["imported_tx_count"] == 1
    assert rules[0]["source"] == "Imported (Codes.xlsx)"
    assert env["calls"]["sync"] == 1
    assert len(appended) == 1


def test_import_codes_workbook_without_files_reapplies_to_ledger(env, monkeypatch):
    parsed = {
        "account_codes": [],
        "properties": [],
        "payee_code_rules": [],
        "combined_bank_df": pd.DataFrame(),
    }
    monkeypatch.setattr(axiom_upload, "parse_coded_workbook_sheets", lambda *a, **k: parsed)
    monkeypatch.setattr(
        axiom_upload,
        "get_entity_ledger",
        lambda entity=None, tax_year=None: _frame([{"Date": "2026-01-01"}]),
    )
    applied = []
    monkeypatch.setattr(
        axiom_upload,
        "set_entity_ledger",
        lambda df, entity=None, year=None: applied.append(df),
    )

    result = axiom_upload.import_codes_workbook_into_entity(b"wb")

    assert result["imported_tx_count"] == 0
    assert env["calls"]["sync"] == 0
    assert len(applied) == 1
