"""
axiom_ui_ingestion_board
========================

The 4-column ingestion board (CSV | PDF | Screenshots | Other) with per-bucket
file tables, red-count badges and the per-file action rows (phase D20).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_core import STANDARD_COLUMNS
from axiom_db import _delete_file_record_from_sqlite
from axiom_ingestion import get_file_red_counts, sync_entity_ledger_from_files
from axiom_upload import ingest_uploaded_file_object, run_ocr_on_screenshot_record


def _render_bucket_header(col_title: str, col_subtitle: str, bucket_files: list) -> None:
    """Column heading with the file count."""
    st.markdown(
        f"""
        <div style="border-bottom: 1px solid #2A2D34; padding-bottom: 0.5rem; margin-bottom: 0.65rem;">
            <div class="axiom-eyebrow" style="margin-bottom: 0.1rem;">{col_title} ({len(bucket_files)})</div>
            <div style="font-size: 0.82rem; color: #A0A5B1;">{col_subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_screenshot_ocr_bar(
    bucket_files: list,
    active_entity: str,
    active_tax_year: str,
    key_prefix: str,
) -> None:
    """Batch OCR action bar specifically for 03 // SCREENSHOTS."""
    pending_ocr_files = [
        f for f in bucket_files
        if f.get("needs_ocr")
        or (
            isinstance(f.get("df"), pd.DataFrame)
            and len(f["df"]) == 1
            and float(f["df"]["Amount"].iloc[0] or 0.0) == 0.0
            and not str(f.get("note", "")).startswith("Account/Loan summary")
        )
    ]
    has_gem_col = bool(st.session_state.get("google_genai_api_key", "").strip())
    ocr_btn_label = (
        f"🔍 Run AI OCR on Pending ({len(pending_ocr_files)})"
        if pending_ocr_files
        else f"🔄 Re-Run AI OCR on All ({len(bucket_files)})"
    )
    if st.button(
        ocr_btn_label,
        key=f"{key_prefix}_batch_shot_ocr_{active_entity}_{active_tax_year}",
        type="primary" if pending_ocr_files else "secondary",
        disabled=not has_gem_col,
        use_container_width=True,
        help="Extract transaction rows from uploaded PNG/JPG screenshots using Gemini Vision OCR.",
    ):
        targets_to_ocr = pending_ocr_files if pending_ocr_files else bucket_files
        shot_prog = st.progress(0.0, text="Starting Gemini OCR on screenshots...")
        extracted_files_cnt = 0
        for s_idx, s_rec in enumerate(targets_to_ocr, start=1):
            shot_prog.progress(
                s_idx / len(targets_to_ocr),
                text=f"OCR ({s_idx}/{len(targets_to_ocr)}): {s_rec.get('name')}...",
            )
            ok_s, _, _ = run_ocr_on_screenshot_record(
                s_rec,
                entity=active_entity,
                tax_year=active_tax_year,
            )
            if ok_s:
                extracted_files_cnt += 1
        shot_prog.empty()
        st.toast(f"Completed OCR on {extracted_files_cnt} screenshot(s).")
        st.rerun()


def _file_status_style(
    bucket_key: str,
    f_rec: dict,
    f_df: pd.DataFrame,
    c_stats: dict,
) -> tuple[str, str]:
    """Border colour and status line for one file card."""
    red_cnt = c_stats["red"]
    yellow_cnt = c_stats["yellow"]
    green_cnt = c_stats["green"]
    tot_cnt = c_stats["total"]

    if f_rec.get("needs_ocr"):
        border_color = "#3B82F6"
        status_line = "🕓 Saved in Vault · Click '🔍 Run AI OCR' or Open File"
    elif (
        bucket_key == "screenshots"
        and tot_cnt == 1
        and float(f_df["Amount"].iloc[0] or 0.0) == 0.0
    ):
        border_color = "#4B5563"
        status_line = str(
            f_rec.get("note")
            or "🖼️ Account/Loan Summary Screenshot · Click to view"
        )
    elif red_cnt > 0:
        border_color = "#8A4B50"
        status_line = f"🔴 {red_cnt} Red · 🟡 {yellow_cnt} Yellow · 🟢 {green_cnt} Sure"
    elif yellow_cnt > 0:
        border_color = "#B4833D"
        status_line = f"🟡 {yellow_cnt} Yellow (Double-Check) · 🟢 {green_cnt} Sure"
    elif tot_cnt > 0:
        border_color = "#3A5A48"
        status_line = f"🟢 All {tot_cnt} Rows 100% Sure"
    else:
        border_color = "#3A3F4B"
        status_line = str(f_rec.get("note") or "0 rows — Click to inspect")
    return border_color, status_line


def _render_empty_bucket_state() -> None:
    """Dashed placeholder for a bucket with no files."""
    st.markdown(
        """
        <div style="
            padding: 1.3rem 0.75rem;
            text-align: center;
            color: #6C727F;
            font-size: 0.8rem;
            border: 1px dashed #2A2D34;
            border-radius: 4px;
            margin-bottom: 0.65rem;
        ">
            No files in this column yet
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_file_card(f_rec: dict, bucket_key: str, active_entity: str) -> dict:
    """Status card for one file; returns its red/yellow/green counts."""
    f_name = str(f_rec.get("name"))
    f_df: pd.DataFrame = f_rec.get("df", pd.DataFrame(columns=STANDARD_COLUMNS))
    c_stats = get_file_red_counts(f_df, entity=active_entity)
    border_color, status_line = _file_status_style(bucket_key, f_rec, f_df, c_stats)

    st.markdown(
        f"""
        <div style="
            background-color: #16181C;
            border: 1px solid {border_color};
            border-left: 3px solid {border_color};
            border-radius: 4px;
            padding: 0.55rem 0.7rem;
            margin-bottom: 0.35rem;
        ">
            <div style="font-weight: 600; font-size: 0.86rem; color: #F3F4F6; word-break: break-all;">
                {f_name}
            </div>
            <div style="font-size: 0.74rem; color: #B8BCC6; margin-top: 0.2rem;">
                {status_line}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return c_stats


def _render_bucket_file_rows(
    bucket_files: list,
    bucket_key: str,
    active_entity: str,
    active_tax_year: str,
    key_prefix: str,
    state_open_key: str,
    files_list: list,
) -> None:
    """Empty state, or one status card plus open/delete actions per file."""
    if not bucket_files:
        _render_empty_bucket_state()
        return

    for f_rec in bucket_files:
        f_id = str(f_rec.get("id"))
        f_name = str(f_rec.get("name"))
        c_stats = _render_file_card(f_rec, bucket_key, active_entity)
        red_cnt = c_stats["red"]
        act_c1, act_c2 = st.columns([3.2, 1.0])
        with act_c1:
            btn_lbl = f"Open & Code ({red_cnt} Red)" if red_cnt > 0 else "Open Full Page"
            if st.button(
                btn_lbl,
                key=f"{key_prefix}_open_btn_{f_id}",
                type="primary" if red_cnt > 0 else "secondary",
                use_container_width=True,
            ):
                st.session_state[state_open_key] = f_id
                st.rerun()
        with act_c2:
            if st.button(
                "✕",
                key=f"{key_prefix}_del_file_{f_id}",
                help=f"Remove {f_name} from Vault & SQLite",
                use_container_width=True,
            ):
                _delete_file_record_from_sqlite(f_id, delete_vault_file=True)
                files_list[:] = [x for x in files_list if x.get("id") != f_id]
                sync_entity_ledger_from_files(active_entity, active_tax_year)
                st.rerun()


def _render_bucket_quick_add(
    bucket_key: str,
    col_subtitle: str,
    allowed_exts: list,
    active_entity: str,
    active_tax_year: str,
    key_prefix: str,
) -> None:
    """Direct single-column quick add with explicit Upload button & clean queue reset."""
    col_nonce_key = f"_{key_prefix}_col_up_nonce_{bucket_key}_{active_entity}_{active_tax_year}"
    col_nonce = int(st.session_state.get(col_nonce_key, 0))
    col_msg_key = f"_{key_prefix}_col_up_msg_{bucket_key}_{active_entity}_{active_tax_year}"
    if st.session_state.get(col_msg_key):
        st.success(str(st.session_state.pop(col_msg_key)))

    with st.expander(f"＋ Add to {bucket_key.upper()}", expanded=False):
        col_up = st.file_uploader(
            f"Select {col_subtitle}",
            type=allowed_exts,
            accept_multiple_files=True,
            key=f"{key_prefix}_col_up_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
        )
        if col_up:
            st.caption(f"📂 {len(col_up)} file(s) staged — click Upload below:")
            if st.button(
                f"⬆️ Upload {len(col_up)} File(s) to {bucket_key.upper()}",
                key=f"{key_prefix}_btn_col_up_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
                type="primary",
                use_container_width=True,
            ):
                c_prog = st.progress(0.0, text=f"Uploading to {bucket_key.upper()}...")
                added_c = 0
                for idx_c, uf in enumerate(col_up, start=1):
                    c_prog.progress(
                        idx_c / len(col_up),
                        text=f"Saving ({idx_c}/{len(col_up)}): {uf.name}...",
                    )
                    ok_c, _, _ = ingest_uploaded_file_object(
                        uf,
                        entity=active_entity,
                        tax_year=active_tax_year,
                        forced_bucket=bucket_key,
                        run_ocr_immediately=True,
                    )
                    if ok_c:
                        added_c += 1
                c_prog.empty()
                st.session_state[col_nonce_key] = col_nonce + 1
                st.session_state[col_msg_key] = f"✅ Added {added_c} file(s) to {bucket_key.upper()}."
                st.toast(f"Added {added_c} file(s) to {bucket_key.upper()}.")
                st.rerun()
            if (
                bucket_key == "screenshots"
                and bool(st.session_state.get("google_genai_api_key", "").strip())
                and st.button(
                    f"🔍 Upload & Run AI OCR ({len(col_up)})",
                    key=f"{key_prefix}_btn_col_up_ocr_{bucket_key}_{active_entity}_{active_tax_year}_{col_nonce}",
                    type="secondary",
                    use_container_width=True,
                )
            ):
                c_prog = st.progress(0.0, text="Uploading & running OCR on screenshots...")
                added_c = 0
                for idx_c, uf in enumerate(col_up, start=1):
                    c_prog.progress(
                        idx_c / len(col_up),
                        text=f"OCR ({idx_c}/{len(col_up)}): {uf.name}...",
                    )
                    ok_c, _, _ = ingest_uploaded_file_object(
                        uf,
                        entity=active_entity,
                        tax_year=active_tax_year,
                        forced_bucket=bucket_key,
                        run_ocr_immediately=True,
                    )
                    if ok_c:
                        added_c += 1
                c_prog.empty()
                st.session_state[col_nonce_key] = col_nonce + 1
                st.session_state[col_msg_key] = f"✅ Added & OCR'd {added_c} screenshot(s)."
                st.rerun()


def _render_ingestion_board(
    active_entity: str,
    active_tax_year: str,
    key_prefix: str,
    state_open_key: str,
    files_list: list,
) -> None:
    """4-column ingestion board: CSV | PDF | Screenshots | Other."""
    col_csv, col_pdf, col_shot, col_other = st.columns(4)

    bucket_configs = [
        (
            col_csv,
            "csv",
            "01 // CSV FILES",
            "Bank & Card Exports (.csv)",
            ["csv"],
        ),
        (
            col_pdf,
            "pdf",
            "02 // PDF STATEMENTS",
            "Invoices & Statements (.pdf)",
            ["pdf"],
        ),
        (
            col_shot,
            "screenshots",
            "03 // SCREENSHOTS",
            "Receipts & Captures (.png, .jpg)",
            ["png", "jpg", "jpeg", "webp"],
        ),
        (
            col_other,
            "other",
            "04 // OTHER (EXCEL / ODT)",
            "Excel & OpenSource (.xlsx, .ods, .odt)",
            ["xlsx", "xls", "ods", "odt", "tsv"],
        ),
    ]

    for col_obj, bucket_key, col_title, col_subtitle, allowed_exts in bucket_configs:
        with col_obj:
            bucket_files = [f for f in files_list if f.get("bucket") == bucket_key]
            with st.container(border=True):
                _render_bucket_header(col_title, col_subtitle, bucket_files)

                if bucket_key == "screenshots" and bucket_files:
                    _render_screenshot_ocr_bar(
                        bucket_files,
                        active_entity,
                        active_tax_year,
                        key_prefix,
                    )

                _render_bucket_file_rows(
                    bucket_files,
                    bucket_key,
                    active_entity,
                    active_tax_year,
                    key_prefix,
                    state_open_key,
                    files_list,
                )

                _render_bucket_quick_add(
                    bucket_key,
                    col_subtitle,
                    allowed_exts,
                    active_entity,
                    active_tax_year,
                    key_prefix,
                )
