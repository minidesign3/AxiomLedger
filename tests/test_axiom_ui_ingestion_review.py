"""Tests for the full-page file reviewer split (D20).

All Streamlit calls go through ``ui_recorder.FakeSt``; classification helpers are
deterministic fakes driven by the row description text.
"""

from __future__ import annotations

import pandas as pd
from ui_recorder import FakeSt, run_render

import axiom_ui_ingestion_review as review
import axiom_ui_ingestion_review_sheet as sheet

ENTITY = "BT Property Holdings Ltd"
YEAR = "2025"
OPEN_KEY = f"ing_open_file_id_{ENTITY}_{YEAR}"


def make_df(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["Date", "Description", "Amount", "Account Code", "Property"],
    )


def mixed_file() -> dict:
    return {
        "id": "f1",
        "name": "bank.csv",
        "bucket": "csv",
        "df": make_df(
            [
                ["2025-01-01", "RED Rent", 100.0, "Uncategorized", "Unassigned"],
                ["2025-01-02", "YELLOW Power", 20.0, "Uncategorized", "Unassigned"],
                ["2025-01-03", "GREEN Rates", 30.0, "Rates", "Property A"],
            ]
        ),
    }


def green_file() -> dict:
    return {
        "id": "f2",
        "name": "stmt.pdf",
        "bucket": "pdf",
        "df": make_df(
            [
                ["2025-02-01", "GREEN Insurance", 10.0, "Insurance", "Property A"],
                ["2025-02-02", "GREEN Rent", 90.0, "Rent Income", "Property B"],
            ]
        ),
    }


def screenshot_file() -> dict:
    rec = {
        "id": "f3",
        "name": "receipt.png",
        "bucket": "screenshots",
        "raw_bytes": b"png-bytes",
        "df": make_df([["2025-03-01", "GREEN Receipt", 0.0, "Uncategorized", "Unassigned"]]),
    }
    return rec


def red_counts(df: pd.DataFrame, entity: str | None = None) -> dict[str, int]:
    desc = [str(x) for x in df.get("Description", [])]
    red = sum(d.startswith("RED") for d in desc)
    yellow = sum(d.startswith("YELLOW") for d in desc)
    green = sum(d.startswith("GREEN") for d in desc)
    return {
        "red": red,
        "yellow": yellow,
        "green": green,
        "total": int(len(df)),
        "red_total": red + yellow,
    }


def patch_review(monkeypatch, fake: FakeSt, calls: dict) -> None:
    def traffic_light(property_val: str = "", account_code: str = "", description: str = "", entity: str = ""):
        text = str(description)
        if text.startswith("RED"):
            return ("red", "🔴 Red")
        if text.startswith("YELLOW"):
            return ("yellow", "🟡 Yellow")
        return ("green", "🟢 Sure")

    deps = {
        "st": fake,
        "get_file_red_counts": red_counts,
        "get_entity_properties": lambda e: ["Property A", "Property B"],
        "get_account_code_options": lambda e: ["Insurance", "Rates", "Rent Income", "Uncategorized"],
        "render_property_editor_widget": lambda **k: fake.markdown("PROP_EDITOR"),
        "apply_rule_categorization": lambda df, **k: (
            calls.setdefault("rules", []).append(k) or df.assign(Description=df["Description"] + " RULED")
        ),
        "apply_ai_fallback_categorization": lambda df, **k: (df, []),
        "read_screenshot_ocr_markdown": lambda rec: (None, ""),
        "run_ocr_on_screenshot_record": lambda *a, **k: (True, "ocr ok", None),
        "save_memorized_rule": lambda **k: calls.setdefault("rules_saved", []).append(k),
        "sync_entity_ledger_from_files": lambda *a, **k: calls.setdefault("sync", []).append(a),
        "is_row_red_or_unsure": lambda p, c, description="", entity="": str(description).startswith(
            ("RED", "YELLOW")
        ),
        "is_property_unassigned": lambda p, c="": str(p).strip() in ("", "Unassigned"),
        "is_uncategorized": lambda c: str(c).strip() in ("", "Uncategorized"),
        "is_portfolio_wide_code": lambda c, e: str(c) in ("Insurance", "Rates"),
        "evaluate_row_traffic_light": traffic_light,
        "format_tax_year_period": lambda y, e: f"{y} period",
        "STANDARD_COLUMNS": ["Date", "Description", "Amount", "Account Code", "Property"],
        "UNASSIGNED_PROPERTY": "Unassigned",
    }
    for module in (review, sheet):
        for name, value in deps.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)


def texts(log: list, event: str) -> list[str]:
    return [e[1][0] for e in log if e[0] == event and e[1] and isinstance(e[1][0], str)]


def buttons(log: list) -> list[str]:
    return [e[1][0] for e in log if e[0] == "button" and e[1]]


def test_reviewer_renders_metrics_quick_fixes_and_sheet(monkeypatch) -> None:
    fake = FakeSt()
    calls: dict = {}
    patch_review(monkeypatch, fake, calls)

    log = run_render(review._render_full_page_file_reviewer, fake, mixed_file(), ENTITY, YEAR, "ing")

    assert ("metric", ["TOTAL ROWS IN FILE", "3"], {}) in log
    assert ("metric", ["🟢 GREEN (100% SURE)", "1"], {}) in log
    assert ("metric", ["🟡 YELLOW (DOUBLE-CHECK)", "1"], {}) in log
    assert ("metric", ["🔴 RED (UNSURE / MISSING)", "1"], {}) in log

    radio = next(e for e in log if e[0] == "radio")
    assert radio[1][1] == [
        "All Rows (3)",
        "🔴+🟡 Needs Check (2)",
        "🔴 Red Only (1)",
        "🟡 Yellow Only (1)",
        "🟢 Green 100% Sure (1)",
    ]

    editor = next(e for e in log if e[0] == "data_editor")
    _, cols, rows = editor[1][0]
    assert "Status" in cols
    status_idx = cols.index("Status")
    assert [r[status_idx] for r in rows] == ["🔴 Red", "🟡 Yellow", "🟢 Sure"]
    assert "💾 Save & Confirm Checked Rows (Memorize as 🟢 100% Sure)" in buttons(log)
    assert "Apply to Unassigned" in buttons(log)


def test_reviewer_red_filter_narrows_sheet(monkeypatch) -> None:
    fake = FakeSt(values={"ing_filter_radio_f1": "🔴 Red Only (1)"})
    patch_review(monkeypatch, fake, {})

    log = run_render(review._render_full_page_file_reviewer, fake, mixed_file(), ENTITY, YEAR, "ing")

    editor = next(e for e in log if e[0] == "data_editor")
    _, cols, rows = editor[1][0]
    assert [r[cols.index("Description")] for r in rows] == ["RED Rent"]
    assert editor[2]["num_rows"] == "fixed"


def test_reviewer_green_file_shows_batch_success(monkeypatch) -> None:
    fake = FakeSt()
    patch_review(monkeypatch, fake, {})

    log = run_render(review._render_full_page_file_reviewer, fake, green_file(), ENTITY, YEAR, "ing")

    assert "✅ Zero Red/Yellow rows remaining in this file! Every row is 🟢 100% Sure." in texts(
        log, "success"
    )
    assert not any("Batch Code Repeated" in b for b in buttons(log))


def test_reviewer_default_property_quick_fix(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(
        clicks={"ing_apply_def_prop_f1"},
        values={"ing_def_prop_sel_f1": "Property A"},
    )
    patch_review(monkeypatch, fake, calls)
    rec = mixed_file()

    log = run_render(review._render_full_page_file_reviewer, fake, rec, ENTITY, YEAR, "ing")

    assert list(rec["df"]["Property"]) == ["Property A", "Property A", "Property A"]
    assert "Assigned 'Property A' to 2 unassigned row(s)." in texts(log, "success")
    assert calls["sync"] == [(ENTITY, YEAR)]
    assert ("rerun",) in log


def test_reviewer_rerun_rules_updates_file(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(clicks={"ing_rerun_mem_f1"})
    patch_review(monkeypatch, fake, calls)
    rec = mixed_file()

    log = run_render(review._render_full_page_file_reviewer, fake, rec, ENTITY, YEAR, "ing")

    assert calls["rules"] == [{"overwrite_existing": False, "entity": ENTITY}]
    assert all(d.endswith(" RULED") for d in rec["df"]["Description"])
    assert calls["sync"] == [(ENTITY, YEAR)]
    assert ("rerun",) in log


def test_reviewer_back_button_clears_open_file(monkeypatch) -> None:
    fake = FakeSt(session={OPEN_KEY: "f1"}, clicks={"ing_back_btn_f1"})
    patch_review(monkeypatch, fake, {})
    rec = mixed_file()

    log = run_render(review._render_full_page_file_reviewer, fake, rec, ENTITY, YEAR, "ing")

    assert fake.session_state[OPEN_KEY] is None
    assert ("rerun",) in log


def test_reviewer_screenshot_panel(monkeypatch) -> None:
    fake = FakeSt(session={"google_genai_api_key": "test-key"})
    calls: dict = {}
    patch_review(monkeypatch, fake, calls)
    monkeypatch.setattr(
        review, "read_screenshot_ocr_markdown", lambda rec: ("shots/receipt.ocr.md", "# OCR text")
    )
    rec = screenshot_file()

    log = run_render(review._render_full_page_file_reviewer, fake, rec, ENTITY, YEAR, "ing")

    image = next(e for e in log if e[0] == "image")
    assert image[2] == {"caption": "receipt.png", "use_container_width": True}
    assert any("SCREENSHOT OCR EXTRACTION (GEMINI 3.8 FLASH)" in t for t in texts(log, "markdown"))
    assert "⬇ Download .ocr.md" in [
        e[1][0] for e in log if e[0] == "download_button"
    ]
    assert "# OCR text" in texts(log, "markdown")
