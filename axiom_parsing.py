"""
axiom_parsing
=============

Raw document readers: PDF table extraction (pdfplumber), pure-Python XLSX
reading for environments without ``openpyxl``, Excel/ODS/ODT/TSV table
extraction and the bank-CSV preamble sniffer for exports (such as ASB) that
prepend metadata lines above the real multi-column header.

Extracted from ``utils.py`` (phase D11) with byte-identical bodies except
``extract_other_document_tables``, which imports ``axiom_workbook`` inside the
function body to keep the import graph acyclic (``axiom_workbook`` imports
``parse_xlsx_without_openpyxl`` from here). ``utils.py`` re-exports everything.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

from axiom_dataframe import _dedupe_column_names, normalize_financial_dataframe


def extract_pdf_tables(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Use pdfplumber to extract all tables from a PDF document and return a combined raw DataFrame.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required for PDF table extraction. Install it via `pip install pdfplumber`."
        ) from exc

    extracted_frames: list[pd.DataFrame] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                cleaned_rows = [
                    [("" if cell is None else str(cell).strip()) for cell in row]
                    for row in table
                    if row and any(cell is not None and str(cell).strip() != "" for cell in row)
                ]
                if not cleaned_rows:
                    continue

                header_row = cleaned_rows[0]
                header_tokens = " ".join(header_row).lower()
                looks_like_header = any(
                    kw in header_tokens
                    for kw in ("date", "amount", "debit", "credit", "description", "account", "code", "particulars", "memo", "balance")
                )

                if looks_like_header and len(cleaned_rows) > 1:
                    deduped_cols = _dedupe_column_names(list(header_row))
                    frame = pd.DataFrame(cleaned_rows[1:], columns=deduped_cols)
                else:
                    frame = pd.DataFrame(cleaned_rows)

                extracted_frames.append(frame)

    if not extracted_frames:
        return pd.DataFrame()

    return pd.concat(extracted_frames, ignore_index=True)

def parse_xlsx_without_openpyxl(file_bytes: bytes) -> dict[str, pd.DataFrame]:
    """
    Pure-Python `.xlsx` workbook parser using `zipfile` + `xml.etree.ElementTree`.
    Works even when `openpyxl` is not installed in the environment, and reads ALL sheets
    accurately (including `Codes.xlsx`).
    """
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

    def _col_letters_to_idx(col_str: str) -> int:
        idx = 0
        for ch in col_str:
            if ch.isalpha():
                idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
        return max(0, idx - 1)

    sheets_dict: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall("main:si", ns):
                texts = [t.text or "" for t in si.iter(f"{{{ns['main']}}}t")]
                shared_strings.append("".join(texts))

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map: dict[str, str] = {}
        for rel in rels_root:
            r_id = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if r_id and target:
                clean_target = target.lstrip("/")
                if not clean_target.startswith("xl/"):
                    clean_target = f"xl/{clean_target}"
                rel_map[r_id] = clean_target

        for sh_idx, sh_el in enumerate(wb_root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"), start=1):
            sh_name = sh_el.attrib.get("name", f"Sheet{sh_idx}")
            r_id = ""
            for attr_k, attr_v in sh_el.attrib.items():
                if attr_k.endswith("}id") or attr_k == "r:id":
                    r_id = attr_v
                    break
            xml_path = rel_map.get(r_id, f"xl/worksheets/sheet{sh_idx}.xml")
            if xml_path not in zf.namelist():
                continue
            s_root = ET.fromstring(zf.read(xml_path))
            rows_xml = s_root.findall(".//main:row", ns)
            if not rows_xml:
                sheets_dict[sh_name] = pd.DataFrame()
                continue

            parsed_rows: list[dict[int, str]] = []
            max_col_idx = 0
            for r_el in rows_xml:
                row_map: dict[int, str] = {}
                for c_el in r_el.findall("main:c", ns):
                    ref = c_el.attrib.get("r", "")
                    col_letters = "".join(ch for ch in ref if ch.isalpha())
                    c_idx = _col_letters_to_idx(col_letters)
                    c_type = c_el.attrib.get("t", "")
                    v_el = c_el.find("main:v", ns)
                    is_el = c_el.find("main:is", ns)
                    val_str = ""
                    if v_el is not None and v_el.text is not None:
                        if c_type == "s":
                            try:
                                val_str = shared_strings[int(v_el.text)]
                            except Exception:
                                val_str = v_el.text
                        else:
                            val_str = v_el.text
                    elif is_el is not None:
                        val_str = "".join(
                            t.text or "" for t in is_el.iter(f"{{{ns['main']}}}t")
                        )
                    if val_str != "":
                        row_map[c_idx] = val_str.strip()
                        if c_idx > max_col_idx:
                            max_col_idx = c_idx
                if row_map:
                    parsed_rows.append(row_map)

            if not parsed_rows:
                sheets_dict[sh_name] = pd.DataFrame()
                continue

            grid = [
                [r_map.get(c_i, "") for c_i in range(max_col_idx + 1)]
                for r_map in parsed_rows
            ]
            sheets_dict[sh_name] = pd.DataFrame(grid)

    return sheets_dict

def extract_other_document_tables(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Extract financial tables from 'Other' formats:
    - Excel (.xlsx, .xls) — with pure-Python XML fallback if `openpyxl` is unavailable
    - OpenDocument Spreadsheet (.ods)
    - OpenDocument Text (.odt)
    - TSV / Text (.tsv, .txt)
    """
    ext = Path(filename or "").suffix.lower()
    if ext in (".xlsx", ".xls"):
        # First try smart multi-sheet coded workbook parser (handles Codes.xlsx & bank sheets cleanly)
        try:
            # Local import: axiom_workbook imports this module's parsers at module level.
            from axiom_workbook import parse_coded_workbook_sheets

            parsed_wb = parse_coded_workbook_sheets(file_bytes, filename=filename)
            bank_df = parsed_wb.get("combined_bank_df")
            if isinstance(bank_df, pd.DataFrame) and not bank_df.empty:
                return bank_df
        except Exception:
            pass
        try:
            sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
        except Exception:
            sheets = parse_xlsx_without_openpyxl(file_bytes) if ext == ".xlsx" else {}
        frames = []
        for df in sheets.values():
            if isinstance(df, pd.DataFrame) and not df.empty:
                norm_s = normalize_financial_dataframe(df)
                if not norm_s.empty:
                    frames.append(norm_s)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ext == ".ods":
        sheets = pd.read_excel(io.BytesIO(file_bytes), engine="odf", sheet_name=None)
        frames = [df for df in sheets.values() if isinstance(df, pd.DataFrame) and not df.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ext == ".odt":
        from odf.opendocument import load as odf_load
        from odf.table import Table, TableCell, TableRow
        from odf.teletype import extractText

        doc = odf_load(io.BytesIO(file_bytes))
        extracted_frames: list[pd.DataFrame] = []
        for tbl in doc.getElementsByType(Table):
            rows_data: list[list[str]] = []
            for tr in tbl.getElementsByType(TableRow):
                row_cells: list[str] = []
                for tc in tr.getElementsByType(TableCell):
                    cell_text = extractText(tc).strip()
                    repeat = int(tc.getAttribute("numbercolumnsrepeated") or 1)
                    for _ in range(min(repeat, 12)):
                        row_cells.append(cell_text)
                if any(c != "" for c in row_cells):
                    rows_data.append(row_cells)
            if rows_data:
                header_tokens = " ".join(rows_data[0]).lower()
                if any(k in header_tokens for k in ("date", "amount", "debit", "credit", "description", "payee")) and len(rows_data) > 1:
                    cols = _dedupe_column_names(rows_data[0])
                    extracted_frames.append(pd.DataFrame(rows_data[1:], columns=cols))
                else:
                    extracted_frames.append(pd.DataFrame(rows_data))
        return pd.concat(extracted_frames, ignore_index=True) if extracted_frames else pd.DataFrame()
    # Fallback CSV/TSV
    try:
        return pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
    except Exception:
        return pd.read_csv(io.BytesIO(file_bytes))

def _read_bank_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    """
    Robustly parse bank CSV bytes, including NZ/US bank exports (such as ASB CSVs)
    that prepend 1-column metadata preamble lines above the true multi-column table header.
    """
    import csv as _csv

    text = ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = file_bytes.decode("utf-8", errors="replace")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    header_keywords = {
        "date",
        "amount",
        "debit",
        "credit",
        "payee",
        "description",
        "memo",
        "particulars",
        "tran type",
        "details",
        "reference",
        "other party",
        "unique id",
    }

    best_header_idx = 0
    for idx, line in enumerate(lines[:30]):
        try:
            parsed_row = next(_csv.reader([line]))
        except Exception:
            continue
        cleaned_cells = [str(c).strip().lower() for c in parsed_row if str(c).strip()]
        if len(cleaned_cells) >= 3:
            hits = sum(1 for c in cleaned_cells if c in header_keywords or any(k in c for k in ("date", "amount", "debit", "credit")))
            if hits >= 2:
                best_header_idx = idx
                break

    sliced_text = "\n".join(lines[best_header_idx:])
    try:
        df = pd.read_csv(io.StringIO(sliced_text))
        if len(df.columns) >= 2:
            return df
    except Exception:
        pass

    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
        if len(df.columns) >= 2:
            return df
    except Exception:
        pass

    # Fallback: jagged row reader via csv.reader padded to max width
    all_rows: list[list[str]] = []
    for line in lines[best_header_idx:]:
        try:
            row = next(_csv.reader([line]))
            if any(str(cell).strip() for cell in row):
                all_rows.append([str(cell).strip() for cell in row])
        except Exception:
            continue
    if not all_rows:
        return pd.DataFrame()
    max_cols = max(len(r) for r in all_rows)
    padded_rows = [r + [""] * (max_cols - len(r)) for r in all_rows]
    if len(padded_rows) >= 2:
        cols = _dedupe_column_names(padded_rows[0])
        return pd.DataFrame(padded_rows[1:], columns=cols)
    return pd.DataFrame(padded_rows)
