"""Tests for the raw document readers (phase D11).

Fixtures are synthetic: an in-memory XLSX built from the minimum OOXML parts
``parse_xlsx_without_openpyxl`` needs, plus CSV/TSV byte payloads. No real
statement files are read.
"""

from __future__ import annotations

import io
import sys
import zipfile

import pandas as pd
import pytest

import axiom_parsing

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _cell(ref: str, value: str, inline: bool = True) -> str:
    if inline:
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    return f'<c r="{ref}"><v>{value}</v></c>'


def _sheet_xml(rows: list[str]) -> bytes:
    body = "".join(f'<row r="{i}">{cells}</row>' for i, cells in enumerate(rows, start=1))
    return (
        f'<worksheet xmlns="{_XLSX_NS}"><sheetData>{body}</sheetData></worksheet>'
    ).encode()


def _build_xlsx(sheets: dict[str, list[str]]) -> bytes:
    """Build a minimal workbook: one worksheet XML string per sheet name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        sheet_entries = "".join(
            f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(sheets, start=1)
        )
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{_XLSX_NS}" xmlns:r="{_REL_NS}">'
            f"<sheets>{sheet_entries}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="{_REL_NS}/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{_REL_NS.replace("/relationship", "/relationships")}">'
            f"{rels}</Relationships>",
        )
        for i, rows in enumerate(sheets.values(), start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# parse_xlsx_without_openpyxl
# --------------------------------------------------------------------------- #
def test_parse_xlsx_reads_all_sheets_and_cell_types():
    payload = _build_xlsx(
        {
            "Bank": [
                _cell("A1", "Date") + _cell("B1", "Amount"),
                _cell("A2", "2026-01-01") + _cell("B2", "12.5", inline=False),
            ],
            "Codes": [_cell("D1", "Insurance")],
        }
    )

    sheets = axiom_parsing.parse_xlsx_without_openpyxl(payload)

    assert list(sheets) == ["Bank", "Codes"]
    assert sheets["Bank"].iloc[0].tolist() == ["Date", "Amount"]
    assert sheets["Bank"].iloc[1].tolist() == ["2026-01-01", "12.5"]
    assert sheets["Codes"].iloc[0, 3] == "Insurance"


def test_parse_xlsx_returns_empty_frame_for_header_only_sheet():
    payload = _build_xlsx({"Empty": []})
    sheets = axiom_parsing.parse_xlsx_without_openpyxl(payload)
    assert sheets["Empty"].empty


def test_parse_xlsx_keeps_sparse_columns_aligned():
    payload = _build_xlsx({"Sheet1": [_cell("A1", "Date") + _cell("D1", "Total")]})
    sheets = axiom_parsing.parse_xlsx_without_openpyxl(payload)
    assert sheets["Sheet1"].iloc[0].tolist() == ["Date", "", "", "Total"]


# --------------------------------------------------------------------------- #
# _read_bank_csv_bytes
# --------------------------------------------------------------------------- #
def test_read_bank_csv_skips_single_column_preamble():
    payload = (
        b"ASB Bank Limited\n"
        b"Account: 12-1234-5678900-00\n"
        b"Date,Amount,Payee,Particulars\n"
        b"01/02/2026,-10.00,Countdown,Groceries\n"
        b"02/02/2026,250.00,Raywhite,Rent\n"
    )
    df = axiom_parsing._read_bank_csv_bytes(payload)
    assert list(df.columns) == ["Date", "Amount", "Payee", "Particulars"]
    assert len(df) == 2
    assert df.iloc[0]["Payee"] == "Countdown"


def test_read_bank_csv_handles_cp1252_bytes():
    payload = "Date,Amount,Description\n01/02/2026,-10.00,Caf\xe9\n".encode("cp1252")
    df = axiom_parsing._read_bank_csv_bytes(payload)
    assert df.iloc[0]["Description"] == "Café"


def test_read_bank_csv_returns_empty_frame_for_blank_payload():
    assert axiom_parsing._read_bank_csv_bytes(b"  \n\n").empty
    assert axiom_parsing._read_bank_csv_bytes(b"").empty


def test_read_bank_csv_reads_quoted_commas():
    payload = b'Date,Amount,Description\n01/02/2026,"-1,234.56","Power, water"\n'
    df = axiom_parsing._read_bank_csv_bytes(payload)
    assert df.iloc[0]["Description"] == "Power, water"


# --------------------------------------------------------------------------- #
# extract_other_document_tables
# --------------------------------------------------------------------------- #
def test_extract_other_document_tables_delimiter_sniffing_fallback(monkeypatch):
    captured: list[pd.DataFrame] = []

    def _identity(df: pd.DataFrame) -> pd.DataFrame:
        captured.append(df)
        return df

    monkeypatch.setattr(axiom_parsing, "normalize_financial_dataframe", _identity)
    df = axiom_parsing.extract_other_document_tables(b"Date\tAmount\n2026-01-01\t5\n", "notes.txt")
    assert list(df.columns) == ["Date", "Amount"]
    assert df.iloc[0, 0] == "2026-01-01"
    assert int(df.iloc[0, 1]) == 5


def test_extract_other_document_tables_xlsx_delegates_to_coded_workbook_parser(monkeypatch):
    payload = _build_xlsx(
        {"Bank": [_cell("A1", "Date") + _cell("B1", "Amount"), _cell("A2", "2026-01-01") + _cell("B2", "5", inline=False)]}
    )
    monkeypatch.setattr(pd, "read_excel", _raise_value_error)
    import axiom_workbook

    monkeypatch.setattr(axiom_workbook, "detect_property_from_text", lambda text, entity=None: "Unassigned")
    monkeypatch.setattr(axiom_workbook, "get_active_entity", lambda entity=None: "Test Entity")

    df = axiom_parsing.extract_other_document_tables(payload, "statement.xlsx")

    assert df.iloc[0, 0] == "2026-01-01"
    assert df.iloc[0, 1] == 5.0


def test_extract_other_document_tables_xlsx_falls_back_to_pure_parser(monkeypatch):
    payload = _build_xlsx(
        {"Bank": [_cell("A1", "Date") + _cell("B1", "Amount"), _cell("A2", "2026-01-01") + _cell("B2", "5", inline=False)]}
    )
    monkeypatch.setattr(axiom_parsing, "normalize_financial_dataframe", lambda df: df)
    monkeypatch.setattr(pd, "read_excel", _raise_value_error)
    import axiom_workbook

    monkeypatch.setattr(
        axiom_workbook,
        "parse_coded_workbook_sheets",
        lambda *a, **k: {"combined_bank_df": pd.DataFrame()},
    )

    df = axiom_parsing.extract_other_document_tables(payload, "statement.xlsx")

    assert df.iloc[0].tolist() == ["Date", "Amount"]


def _raise_value_error(*args, **kwargs):
    raise ValueError("openpyxl cannot read this minimal fixture")


def test_extract_pdf_tables_requires_pdfplumber(monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    with pytest.raises(RuntimeError, match="pdfplumber"):
        axiom_parsing.extract_pdf_tables(b"%PDF-1.4")
