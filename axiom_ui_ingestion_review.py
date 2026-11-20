"""
axiom_ui_ingestion_review
=========================

Full-page file review mode: header, red-alert breakdown, editable transaction
grid and save/re-run actions (phase D20). The sheet itself lives in
``axiom_ui_ingestion_review_sheet`` so both modules stay under the 500-line cap.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from axiom_account_codes import get_account_code_options
from axiom_categorization import apply_rule_categorization
from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY
from axiom_ingestion import get_file_red_counts, sync_entity_ledger_from_files
from axiom_ledger import is_property_unassigned
from axiom_ocr import read_screenshot_ocr_markdown
from axiom_properties import get_entity_properties, is_portfolio_wide_code
from axiom_rules import save_memorized_rule
from axiom_state import format_tax_year_period
from axiom_traffic import is_row_red_or_unsure
from axiom_ui_components import render_property_editor_widget
from axiom_ui_ingestion_common import ReviewContext
from axiom_ui_ingestion_review_sheet import _render_review_sheet
from axiom_upload import run_ocr_on_screenshot_record


def _render_full_page_file_reviewer(
    file_rec: dict[str, object],
    active_entity: str,
    active_tax_year: str,
    key_prefix: str = "ing",
) -> None:
    """
    Full-Page File Reviewer when a user clicks on an individual CSV, PDF, Screenshot, or Other file.
    Includes:
    - Top '← Back to 4-Column File Board' button
    - Summary metrics (🟢 Coded vs 🔴 Needs Property vs 🔴 Unknown Code)
    - One-click 'Set Default Property for This File'
    - Batch 'Group Identical Problem Payees (Fix Once -> Apply to All)'
    - Filter Toggle: [ 🔴 Red / Needs Attention Only ] | [ All Rows ]
    - Excel-style interactive table with Property + Account Code dropdowns and Red status indicators
    - Automatic memorization into .axiom_memory.json
    """
    ctx = _build_review_context(file_rec, active_entity, active_tax_year, key_prefix)
    _render_review_header(ctx)

    counts = get_file_red_counts(ctx.df, entity=ctx.active_entity)
    _render_review_metrics(counts)

    clean_prop_options = _dedupe_property_options(ctx.active_entity)
    account_code_options = get_account_code_options(ctx.active_entity)

    _render_quick_fix_bar(ctx, clean_prop_options, account_code_options)
    _render_review_sheet(ctx, counts, clean_prop_options, account_code_options)


def _build_review_context(
    file_rec: dict[str, object],
    active_entity: str,
    active_tax_year: str,
    key_prefix: str,
) -> ReviewContext:
    """Normalise the file frame and resolve the vault bytes served by the nav bar."""
    file_id = str(file_rec.get("id", "file"))
    file_name = str(file_rec.get("name", "Uploaded File"))
    bucket = str(file_rec.get("bucket", "csv")).upper()
    df: pd.DataFrame = file_rec.get("df", pd.DataFrame(columns=STANDARD_COLUMNS)).copy()
    df = df.reindex(columns=STANDARD_COLUMNS).copy()
    df["Property"] = df["Property"].fillna(UNASSIGNED_PROPERTY).astype(str).str.strip().replace("", UNASSIGNED_PROPERTY)
    df["Account Code"] = df["Account Code"].fillna("Uncategorized").astype(str).str.strip().replace("", "Uncategorized")

    state_open_key = f"{key_prefix}_open_file_id_{active_entity}_{active_tax_year}"
    vault_path_str = str(file_rec.get("vault_path", ""))
    vault_file_bytes: bytes | None = None
    if vault_path_str:
        vp = Path(vault_path_str)
        if not vp.is_absolute():
            vp = Path(__file__).resolve().parent / vp
        if vp.exists() and vp.is_file():
            try:
                vault_file_bytes = vp.read_bytes()
            except Exception:
                vault_file_bytes = None
    if vault_file_bytes is None and isinstance(file_rec.get("raw_bytes"), (bytes, bytearray)):
        vault_file_bytes = bytes(file_rec["raw_bytes"])

    return ReviewContext(
        file_rec=file_rec,
        file_id=file_id,
        file_name=file_name,
        bucket=bucket,
        df=df,
        state_open_key=state_open_key,
        active_entity=active_entity,
        active_tax_year=active_tax_year,
        key_prefix=key_prefix,
        vault_path_str=vault_path_str,
        vault_file_bytes=vault_file_bytes,
    )


def _render_review_header(ctx: ReviewContext) -> None:
    """Nav bar (back / original file / re-run rules), divider, screenshot OCR panel."""
    file_id = ctx.file_id
    file_name = ctx.file_name
    bucket = ctx.bucket
    state_open_key = ctx.state_open_key
    active_entity = ctx.active_entity
    active_tax_year = ctx.active_tax_year
    key_prefix = ctx.key_prefix
    df = ctx.df
    file_rec = ctx.file_rec
    vault_path_str = ctx.vault_path_str
    vault_file_bytes = ctx.vault_file_bytes

    # Top Navigation Bar with Back Button + Download Original Vault File + Re-Run Rules
    nav_c1, nav_c2, nav_c3, nav_c4 = st.columns([1.15, 2.35, 1.15, 1.15])
    with nav_c1:
        if st.button(
            "← Back to All Files",
            key=f"{key_prefix}_back_btn_{file_id}",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state[state_open_key] = None
            st.rerun()
    with nav_c2:
        period_str = format_tax_year_period(active_tax_year, active_entity)
        vault_badge = f" · 💾 Saved in Vault ({vault_path_str})" if vault_path_str else " · 💾 Saved in SQLite"
        st.markdown(
            f"""
            <div style="padding-top: 0.1rem;">
                <span class="axiom-eyebrow">{bucket} FILE REVIEW // {active_entity.upper()} ({period_str})</span>
                <div style="font-size: 1.12rem; font-weight: 600; color: #F3F4F6;">📄 {file_name}</div>
                <div style="font-size: 0.72rem; color: #8E95A5;">{vault_badge}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with nav_c3:
        if vault_file_bytes is not None:
            st.download_button(
                label="⬇ Original File",
                data=vault_file_bytes,
                file_name=file_name,
                mime=str(file_rec.get("mime_type") or "application/octet-stream"),
                key=f"{key_prefix}_dl_orig_{file_id}",
                use_container_width=True,
            )
    with nav_c4:
        if st.button(
            "Re-Run Memorized Rules",
            key=f"{key_prefix}_rerun_mem_{file_id}",
            use_container_width=True,
        ):
            updated_df = apply_rule_categorization(df, overwrite_existing=False, entity=active_entity)
            file_rec["df"] = updated_df
            sync_entity_ledger_from_files(active_entity, active_tax_year)
            st.rerun()

    st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

    # PDF reconciliation result (rows vs the statement's own printed totals)
    pdf_note = str(file_rec.get("note") or "")
    if file_rec.get("bucket") == "pdf" and pdf_note:
        if pdf_note.startswith("⚠️"):
            st.error(pdf_note)
        elif pdf_note.startswith("✅"):
            st.success(pdf_note)
        else:
            st.caption(pdf_note)

    # If this is a Screenshot, show image preview, OCR button & saved .ocr.md sidecar right at top
    if file_rec.get("bucket") == "screenshots" and file_rec.get("raw_bytes"):
        img_col, ocr_col = st.columns([1.2, 2.8])
        with img_col:
            st.image(file_rec["raw_bytes"], caption=file_name, use_container_width=True)
        with ocr_col:
            has_key = bool(st.session_state.get("google_genai_api_key", "").strip())
            st.markdown('<div class="axiom-eyebrow">SCREENSHOT OCR EXTRACTION (GEMINI 3.8 FLASH)</div>', unsafe_allow_html=True)
            if file_rec.get("note"):
                st.caption(str(file_rec.get("note")))
            md_rel_path, md_content = read_screenshot_ocr_markdown(file_rec)
            oc_b1, oc_b2 = st.columns([1.6, 1.2])
            with oc_b1:
                if st.button(
                    f"🔍 {'Re-Run' if md_content else 'Run'} Gemini 3.8 Flash OCR",
                    key=f"{key_prefix}_run_shot_ocr_{file_id}",
                    disabled=not has_key,
                    use_container_width=True,
                ):
                    with st.spinner("Running Gemini 3.8 Flash OCR and saving .ocr.md..."):
                        ok_ocr, msg_ocr, _ = run_ocr_on_screenshot_record(
                            file_rec,
                            entity=active_entity,
                            tax_year=active_tax_year,
                        )
                    if ok_ocr:
                        st.success(msg_ocr)
                        st.rerun()
                    else:
                        st.error(msg_ocr)
            with oc_b2:
                if md_content:
                    st.download_button(
                        label="⬇ Download .ocr.md",
                        data=md_content.encode("utf-8"),
                        file_name=f"{file_name}.ocr.md",
                        mime="text/markdown",
                        key=f"{key_prefix}_dl_ocr_md_{file_id}",
                        use_container_width=True,
                    )
            if md_content:
                with st.expander(f"📝 Saved OCR Markdown ({md_rel_path or f'{file_name}.ocr.md'})", expanded=False):
                    st.markdown(md_content)


def _render_review_metrics(counts: dict) -> None:
    """Four traffic-light counters for the file."""
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.metric("TOTAL ROWS IN FILE", f"{counts['total']:,}")
    with mc2:
        st.metric("🟢 GREEN (100% SURE)", f"{counts['green']:,}")
    with mc3:
        st.metric("🟡 YELLOW (DOUBLE-CHECK)", f"{counts['yellow']:,}")
    with mc4:
        st.metric("🔴 RED (UNSURE / MISSING)", f"{counts['red']:,}")


def _dedupe_property_options(active_entity: str) -> list[str]:
    """Property dropdown options (Unassigned first, duplicates removed, order kept)."""
    property_options = [UNASSIGNED_PROPERTY] + get_entity_properties(active_entity)
    # Deduplicate while preserving order
    seen_p: set[str] = set()
    clean_prop_options: list[str] = []
    for p in property_options:
        if p not in seen_p:
            seen_p.add(p)
            clean_prop_options.append(p)

    return clean_prop_options


def _render_quick_fix_bar(
    ctx: ReviewContext,
    clean_prop_options: list[str],
    account_code_options: list[str],
) -> None:
    """Quick Fix 1 (file default property) and Quick Fix 2 (batch repeated payees)."""
    fix_col1, fix_col2 = st.columns([1.4, 2.2])
    with fix_col1:
        _render_default_property_fix(ctx)
    with fix_col2:
        _render_batch_payee_fix(ctx, clean_prop_options, account_code_options)


def _render_default_property_fix(ctx: ReviewContext) -> None:
    """Assign every still-unassigned row of this file to one property."""
    file_id = ctx.file_id
    key_prefix = ctx.key_prefix
    active_entity = ctx.active_entity
    active_tax_year = ctx.active_tax_year
    df = ctx.df
    file_rec = ctx.file_rec

    with st.container(border=True):
        st.markdown('<div class="axiom-eyebrow">QUICK FIX 1 // BANK FILE DEFAULT PROPERTY</div>', unsafe_allow_html=True)
        st.caption("If this entire bank CSV/statement belongs to one property, fill all unassigned rows in 1 click:")
        dp_c1, dp_c2 = st.columns([1.6, 1.1])
        with dp_c1:
            chosen_default_prop = st.selectbox(
                "Default Property for File",
                options=get_entity_properties(active_entity),
                key=f"{key_prefix}_def_prop_sel_{file_id}",
                label_visibility="collapsed",
            )
        with dp_c2:
            if st.button(
                "Apply to Unassigned",
                key=f"{key_prefix}_apply_def_prop_{file_id}",
                use_container_width=True,
            ):
                mask_unassigned = df.apply(
                    lambda r: is_property_unassigned(r.get("Property", ""), r.get("Account Code", "")),
                    axis=1,
                )
                df.loc[mask_unassigned, "Property"] = chosen_default_prop
                file_rec["df"] = df
                sync_entity_ledger_from_files(active_entity, active_tax_year)
                st.success(f"Assigned '{chosen_default_prop}' to {int(mask_unassigned.sum())} unassigned row(s).")
                st.rerun()

        with st.popover("✏️ Edit Property Names (Property 1, Property 2...)", use_container_width=True):
            st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
            render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_fp_prop_{file_id}")


def _render_batch_payee_fix(
    ctx: ReviewContext,
    clean_prop_options: list[str],
    account_code_options: list[str],
) -> None:
    """Code every row sharing one repeated payee text, then memorise the rule."""
    file_id = ctx.file_id
    file_name = ctx.file_name
    key_prefix = ctx.key_prefix
    active_entity = ctx.active_entity
    active_tax_year = ctx.active_tax_year
    df = ctx.df
    file_rec = ctx.file_rec

    with st.container(border=True):
        st.markdown('<div class="axiom-eyebrow">QUICK FIX 2 // BATCH CODE REPEATED 🔴 RED / 🟡 YELLOW ROWS &amp; MEMORIZE</div>', unsafe_allow_html=True)
        red_mask = df.apply(
            lambda r: is_row_red_or_unsure(
                r.get("Property", ""),
                r.get("Account Code", ""),
                description=r.get("Description", ""),
                entity=active_entity,
            ),
            axis=1,
        )
        red_df = df[red_mask]
        if red_df.empty:
            st.success("✅ Zero Red/Yellow rows remaining in this file! Every row is 🟢 100% Sure.")
        else:
            payee_counts = red_df["Description"].value_counts()
            payee_labels = [f"{desc} ({cnt} row{'s' if cnt > 1 else ''})" for desc, cnt in payee_counts.items() if str(desc).strip()]
            payee_map = {f"{desc} ({cnt} row{'s' if cnt > 1 else ''})": str(desc) for desc, cnt in payee_counts.items() if str(desc).strip()}

            if payee_labels:
                b_c1, b_c2, b_c3, b_c4 = st.columns([1.8, 1.25, 1.25, 0.9])
                with b_c1:
                    selected_payee_label = st.selectbox(
                        "Repeated Bank Row Text",
                        options=payee_labels,
                        key=f"{key_prefix}_batch_payee_{file_id}",
                    )
                    target_desc = payee_map[selected_payee_label]
                    sample_row = red_df[red_df["Description"] == target_desc].iloc[0]
                    curr_p = str(sample_row.get("Property", UNASSIGNED_PROPERTY))
                    curr_c = str(sample_row.get("Account Code", "Uncategorized"))
                with b_c2:
                    batch_code = st.selectbox(
                        "1. What is the Item?",
                        options=account_code_options,
                        index=account_code_options.index(curr_c) if curr_c in account_code_options else (account_code_options.index("Insurance") if "Insurance" in account_code_options else 0),
                        key=f"{key_prefix}_batch_code_{file_id}",
                    )
                is_batch_port = is_portfolio_wide_code(batch_code, active_entity)
                with b_c3:
                    if is_batch_port:
                        batch_prop = st.selectbox(
                            "2. Which Property?",
                            options=["General / Portfolio"],
                            disabled=True,
                            key=f"{key_prefix}_batch_prop_port_{file_id}",
                        )
                    else:
                        batch_prop = st.selectbox(
                            "2. Which Property?",
                            options=clean_prop_options,
                            index=clean_prop_options.index(curr_p) if curr_p in clean_prop_options else (1 if len(clean_prop_options) > 1 else 0),
                            key=f"{key_prefix}_batch_prop_{file_id}",
                        )
                with b_c4:
                    st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
                    if st.button(
                        "Save & Memorize",
                        key=f"{key_prefix}_btn_batch_fix_{file_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        resolved_batch_prop = "General / Portfolio" if is_batch_port else batch_prop
                        match_rows = df["Description"].astype(str).str.strip().str.lower() == target_desc.strip().lower()
                        df.loc[match_rows, "Property"] = resolved_batch_prop
                        df.loc[match_rows, "Account Code"] = batch_code
                        save_memorized_rule(
                            pattern=target_desc,
                            property_name=resolved_batch_prop,
                            account_code=batch_code,
                            entity=active_entity,
                            source=f"Coded in {file_name}",
                        )
                        file_rec["df"] = df
                        sync_entity_ledger_from_files(active_entity, active_tax_year)
                        st.success(
                            f"Updated {int(match_rows.sum())} row(s) for '{target_desc}' → {batch_code} | {resolved_batch_prop} (🟢 Memorized)."
                        )
                        st.rerun()
