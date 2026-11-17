"""Tests for the ingestion board split (D20).

The board and pipeline are driven with ``ui_recorder.FakeSt`` and deterministic
data fakes; no real uploads, vault files or Streamlit runtime are involved.
"""

from __future__ import annotations

import pandas as pd
from ui_recorder import FakeSt, run_render

import axiom_ui_ingestion as pipeline
import axiom_ui_ingestion_board as board

ENTITY = "BT Property Holdings Ltd"
YEAR = "2025"
OPEN_KEY = f"ing_open_file_id_{ENTITY}_{YEAR}"


class Upload:
    def __init__(self, name: str) -> None:
        self.name = name


def make_df(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["Date", "Description", "Amount", "Account Code", "Property"],
    )


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


def sample_files() -> list[dict]:
    return [
        {
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
        },
        {
            "id": "f2",
            "name": "stmt.pdf",
            "bucket": "pdf",
            "df": make_df([["2025-02-01", "GREEN Insurance", 10.0, "Insurance", "Property A"]]),
        },
        {
            "id": "f3",
            "name": "receipt.png",
            "bucket": "screenshots",
            "needs_ocr": True,
            "df": make_df([["2025-03-01", "GREEN Receipt", 0.0, "Uncategorized", "Unassigned"]]),
        },
        {
            "id": "f4",
            "name": "loan-summary.png",
            "bucket": "screenshots",
            "note": "Account/Loan summary shot",
            "df": make_df([["2025-03-02", "GREEN Summary", 0.0, "Uncategorized", "Unassigned"]]),
        },
        {
            "id": "f5",
            "name": "gst.xlsx",
            "bucket": "other",
            "df": make_df(
                [
                    ["2025-04-01", "YELLOW GST", 5.0, "Uncategorized", "Unassigned"],
                    ["2025-04-02", "YELLOW GST 2", 6.0, "Uncategorized", "Unassigned"],
                ]
            ),
        },
        {
            "id": "f6",
            "name": "empty.csv",
            "bucket": "other",
            "df": make_df([]),
        },
    ]


def patch_board(monkeypatch, fake: FakeSt, calls: dict) -> None:
    monkeypatch.setattr(board, "st", fake)
    monkeypatch.setattr(board, "get_file_red_counts", red_counts)
    monkeypatch.setattr(
        board,
        "_delete_file_record_from_sqlite",
        lambda *a, **k: calls.setdefault("delete", []).append((a, k)),
    )
    monkeypatch.setattr(
        board,
        "sync_entity_ledger_from_files",
        lambda *a, **k: calls.setdefault("sync", []).append((a, k)),
    )
    monkeypatch.setattr(
        board,
        "ingest_uploaded_file_object",
        lambda *a, **k: (calls.setdefault("ingest", []).append((a, k)) or (True, "ok", None)),
    )
    monkeypatch.setattr(
        board,
        "run_ocr_on_screenshot_record",
        lambda *a, **k: (calls.setdefault("ocr", []).append((a, k)) or (True, "ok", None)),
    )


def texts(log: list, event: str) -> list[str]:
    return [e[1][0] for e in log if e[0] == event and e[1] and isinstance(e[1][0], str)]


def keys_for(log: list, event: str) -> list[str]:
    return [e[2].get("key") for e in log if e[0] == event and len(e) > 2]


def test_board_empty_columns_and_quick_add(monkeypatch) -> None:
    fake = FakeSt()
    patch_board(monkeypatch, fake, {})
    files: list[dict] = []

    log = run_render(board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, files)

    headers = [t for t in texts(log, "markdown") if "axiom-eyebrow" in t]
    assert len(headers) == 4
    for label, header in zip(
        ("01 // CSV FILES (0)", "02 // PDF STATEMENTS (0)", "03 // SCREENSHOTS (0)",
         "04 // OTHER (EXCEL / ODT) (0)"),
        headers,
        strict=True,
    ):
        assert label in header
    assert sum("No files in this column yet" in t for t in texts(log, "markdown")) == 4
    assert keys_for(log, "file_uploader") == [
        f"ing_col_up_{bucket}_{ENTITY}_{YEAR}_0"
        for bucket in ("csv", "pdf", "screenshots", "other")
    ]
    assert not keys_for(log, "button")


def test_board_cards_cover_all_status_branches(monkeypatch) -> None:
    fake = FakeSt()
    patch_board(monkeypatch, fake, {})
    files = sample_files()

    log = run_render(board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, files)

    markdown = texts(log, "markdown")
    assert any("🔴 1 Red · 🟡 1 Yellow · 🟢 1 Sure" in t for t in markdown)
    assert any("🟢 All 1 Rows 100% Sure" in t for t in markdown)
    assert any("🕓 Saved in Vault" in t for t in markdown)
    assert any("🟡 2 Yellow (Double-Check) · 🟢 0 Sure" in t for t in markdown)
    assert any("0 rows — Click to inspect" in t for t in markdown)
    assert any("Account/Loan summary shot" in t for t in markdown)

    labels = [e[1][0] for e in log if e[0] == "button"]
    assert "Open & Code (1 Red)" in labels
    assert "Open Full Page" in labels


def test_board_open_sets_session_and_reruns(monkeypatch) -> None:
    fake = FakeSt(clicks={"ing_open_btn_f1"})
    patch_board(monkeypatch, fake, {})

    log = run_render(
        board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, sample_files()
    )

    assert fake.session_state[OPEN_KEY] == "f1"
    assert ("rerun",) in log


def test_board_delete_removes_file_and_syncs(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(clicks={"ing_del_file_f1"})
    patch_board(monkeypatch, fake, calls)
    files = sample_files()

    log = run_render(board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, files)

    assert [f["id"] for f in files] == ["f2", "f3", "f4", "f5", "f6"]
    assert calls["delete"] == [(("f1",), {"delete_vault_file": True})]
    assert calls["sync"] == [((ENTITY, YEAR), {})]
    assert ("rerun",) in log


def test_board_quick_add_upload_staged_files(monkeypatch) -> None:
    calls: dict = {}
    upload_key = f"ing_col_up_csv_{ENTITY}_{YEAR}_0"
    button_key = f"ing_btn_col_up_csv_{ENTITY}_{YEAR}_0"
    fake = FakeSt(clicks={button_key}, values={upload_key: [Upload("new.csv")]})
    patch_board(monkeypatch, fake, calls)
    files: list[dict] = []

    log = run_render(board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, files)

    assert any("📂 1 file(s) staged" in t for t in texts(log, "caption"))
    assert keys_for(log, "progress") == [None]
    assert any(e[0] == "progress.update" for e in log)
    assert calls["ingest"][0][1]["forced_bucket"] == "csv"
    assert calls["ingest"][0][1]["run_ocr_immediately"] is True
    assert fake.session_state[f"_ing_col_up_nonce_csv_{ENTITY}_{YEAR}"] == 1
    assert fake.session_state[f"_ing_col_up_msg_csv_{ENTITY}_{YEAR}"] == "✅ Added 1 file(s) to CSV."
    assert ("toast", ["Added 1 file(s) to CSV."], {}) in log
    assert any(e[0] == "rerun" for e in log)


def test_board_screenshot_ocr_bar_runs_pending(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(
        clicks={f"ing_batch_shot_ocr_{ENTITY}_{YEAR}"},
        session={"google_genai_api_key": "test-key"},
    )
    patch_board(monkeypatch, fake, calls)
    files = [f for f in sample_files() if f["bucket"] == "screenshots"]

    log = run_render(board._render_ingestion_board, fake, ENTITY, YEAR, "ing", OPEN_KEY, files)

    assert any("Run AI OCR on Pending (1)" in e[1][0] for e in log if e[0] == "button")
    assert len(calls["ocr"]) == 1
    assert any(e[0] == "toast" and "1 screenshot(s)" in e[1][0] for e in log)
    assert ("rerun",) in log


def _patch_pipeline(monkeypatch, fake: FakeSt, files: list[dict], calls: dict) -> None:
    patch_board(monkeypatch, fake, calls)
    monkeypatch.setattr(pipeline, "st", fake)
    monkeypatch.setattr(pipeline, "get_entity_uploaded_files", lambda e, y: files)
    monkeypatch.setattr(pipeline, "get_entity_properties", lambda e: ["Property A"])
    monkeypatch.setattr(
        pipeline, "render_property_editor_widget", lambda **k: fake.markdown("PROP_EDITOR")
    )
    monkeypatch.setattr(pipeline, "get_memorized_rules", lambda e: [])
    monkeypatch.setattr(pipeline, "export_entity_vault_zip", lambda e, y: b"zip")
    monkeypatch.setattr(pipeline, "_safe_vault_segment", lambda e: "Entity")
    monkeypatch.setattr(pipeline, "is_loan_target_entity", lambda e: calls.get("loan_target", False))
    monkeypatch.setattr(
        pipeline,
        "render_item_property_setup_matrix",
        lambda **k: calls.setdefault("matrix", []).append(k),
    )
    monkeypatch.setattr(
        pipeline,
        "render_property_loans_block",
        lambda **k: calls.setdefault("loans", []).append(k) or fake.markdown("LOANS"),
    )
    monkeypatch.setattr(pipeline, "classify_upload_bucket", lambda n: "screenshots")
    monkeypatch.setattr(pipeline, "ingest_uploaded_file_object", lambda *a, **k: (True, "", None))


def test_pipeline_renders_step1_and_step2_loans(monkeypatch) -> None:
    calls: dict = {"loan_target": True}
    fake = FakeSt()
    _patch_pipeline(monkeypatch, fake, [], calls)

    log = run_render(pipeline.render_entity_ingestion_pipeline, fake, ENTITY, YEAR, "ing", True)

    assert any("STEP 1 // AUTOMATIC 4-COLUMN FILE TABLE" in t for t in texts(log, "markdown"))
    assert calls["loans"][0]["key_prefix"] == "ing_front_loans"
    assert calls["loans"][0]["show_page_title"] is False
    assert "LOANS" in texts(log, "markdown")


def test_pipeline_open_file_delegates_to_reviewer(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(session={OPEN_KEY: "f1"})
    files = sample_files()
    _patch_pipeline(monkeypatch, fake, files, calls)
    seen: list = []

    def fake_reviewer(file_rec, **kwargs):
        seen.append((file_rec, kwargs))
        fake.markdown("REVIEWER")

    monkeypatch.setattr(pipeline, "_render_full_page_file_reviewer", fake_reviewer)

    log = run_render(pipeline.render_entity_ingestion_pipeline, fake, ENTITY, YEAR, "ing", False)

    assert seen[0][0]["id"] == "f1"
    assert seen[0][1] == {"active_entity": ENTITY, "active_tax_year": YEAR, "key_prefix": "ing"}
    assert "REVIEWER" in texts(log, "markdown")
    assert not any(e[0] == "columns" for e in log)


def test_pipeline_stale_open_id_clears_and_renders_board(monkeypatch) -> None:
    calls: dict = {}
    fake = FakeSt(session={OPEN_KEY: "gone"})
    _patch_pipeline(monkeypatch, fake, sample_files(), calls)

    log = run_render(pipeline.render_entity_ingestion_pipeline, fake, ENTITY, YEAR, "ing", False)

    assert fake.session_state[OPEN_KEY] is None
    assert any(e[0] == "columns" for e in log)
