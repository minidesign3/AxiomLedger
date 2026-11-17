"""
axiom_ui_ingestion
==================

Entity ingestion pipeline (phase D20): the STEP 1 board shell, quick multi-file
upload and the STEP 2 loans block. The 4-column board is rendered by
``axiom_ui_ingestion_board``; the dropzone/reviewer live in their own modules.
"""

from __future__ import annotations

import streamlit as st

from axiom_core import _safe_vault_segment, classify_upload_bucket
from axiom_ingestion import get_entity_uploaded_files
from axiom_ledger import export_entity_vault_zip
from axiom_loan_crud import is_loan_target_entity
from axiom_properties import get_entity_properties
from axiom_rules import get_memorized_rules
from axiom_ui_components import render_property_editor_widget
from axiom_ui_ingestion_board import _render_ingestion_board
from axiom_ui_ingestion_review import _render_full_page_file_reviewer
from axiom_ui_loans import render_property_loans_block
from axiom_ui_setup_matrix import render_item_property_setup_matrix
from axiom_upload import ingest_uploaded_file_object


def render_entity_ingestion_pipeline(
    active_entity: str,
    active_tax_year: str,
    key_prefix: str = "ing",
    include_loans_block: bool = True,
) -> None:
    """
    Front Page / Data Ingestion Board:
    - Block 1 (Step 1): Automatic 4-Column File Table (CSV | PDF | Screenshots | Other) — click any file to open full-page review & fix red alerts.
    - Block 2 (Step 2, for BT Property Holdings Ltd): Property Loans & Bank Registry — each property and what each loan is.
    """
    state_open_key = f"{key_prefix}_open_file_id_{active_entity}_{active_tax_year}"
    files_list = get_entity_uploaded_files(active_entity, active_tax_year)

    # Check if a specific file is currently opened in Full-Page Review Mode
    open_file_id = st.session_state.get(state_open_key)
    if open_file_id:
        matched_file = next((f for f in files_list if f.get("id") == open_file_id), None)
        if matched_file is not None:
            _render_full_page_file_reviewer(
                matched_file,
                active_entity=active_entity,
                active_tax_year=active_tax_year,
                key_prefix=key_prefix,
            )
            return
        else:
            st.session_state[state_open_key] = None

    # =========================================================================
    # BLOCK 1 (STEP 1): AUTOMATIC 4-COLUMN FILE TABLE
    # =========================================================================
    with st.container(border=True):
        hdr1, hdr2, hdr3, hdr4 = st.columns([2.6, 0.95, 1.15, 0.95])
        with hdr1:
            st.markdown(
                '<div class="axiom-eyebrow">STEP 1 // AUTOMATIC 4-COLUMN FILE TABLE &mdash; CLICK ANY FILE TO OPEN FULL-PAGE REVIEW &amp; FIX RED ALERTS</div>',
                unsafe_allow_html=True,
            )
        with hdr2:
            with st.popover(f"🏠 Properties ({len(get_entity_properties(active_entity))})", use_container_width=True):
                st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
                render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_board_prop")
        with hdr3:
            show_setup_key = f"_{key_prefix}_show_item_prop_setup_{active_entity}"
            is_setup_open = bool(st.session_state.get(show_setup_key, False))
            if st.button(
                "✕ Close Setup" if is_setup_open else f"⚙️ Item + Property ({len(get_memorized_rules(active_entity))})",
                key=f"{key_prefix}_btn_toggle_item_prop_{active_entity}",
                type="primary" if is_setup_open else "secondary",
                use_container_width=True,
            ):
                st.session_state[show_setup_key] = not is_setup_open
                st.rerun()
        with hdr4:
            zip_bytes = export_entity_vault_zip(active_entity, active_tax_year)
            safe_ent_slug = _safe_vault_segment(active_entity).replace(" ", "_")
            st.download_button(
                label=f"📦 Vault Zip ({len(files_list)})",
                data=zip_bytes,
                file_name=f"AxiomVault_{safe_ent_slug}_{active_tax_year}.zip",
                mime="application/zip",
                key=f"{key_prefix}_dl_vault_zip_{active_entity}_{active_tax_year}",
                use_container_width=True,
            )

        st.markdown("<div style='height: 0.85rem;'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown(
                """
                <div style="border-bottom: 1px solid #2A2D34; padding-bottom: 0.5rem; margin-bottom: 0.65rem;">
                    <div class="axiom-eyebrow" style="margin-bottom: 0.1rem;">＋ UPLOAD MULTIPLE FILES (ADD AS MANY BATCHES AS YOU NEED)</div>
                    <div style="font-size: 0.82rem; color: #A0A5B1;">1) Select or drag-and-drop CSV, PDF, PNG/JPG Screenshots, Excel &amp; ODT files &rarr; 2) Click <b>⬆️ Upload &amp; Sort Selected Files</b> below</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            quick_nonce_key = f"_{key_prefix}_quick_up_nonce_{active_entity}_{active_tax_year}"
            quick_nonce = int(st.session_state.get(quick_nonce_key, 0))
            last_quick_msg_key = f"_{key_prefix}_quick_up_msg_{active_entity}_{active_tax_year}"
            if st.session_state.get(last_quick_msg_key):
                st.success(str(st.session_state.pop(last_quick_msg_key)))

            quick_batch = st.file_uploader(
                "Drop CSV, PDF, PNG/JPG Screenshots, Excel/ODT files",
                type=["csv", "pdf", "png", "jpg", "jpeg", "webp", "xlsx", "xls", "ods", "odt", "tsv"],
                accept_multiple_files=True,
                key=f"{key_prefix}_quick_multi_up_{active_entity}_{active_tax_year}_{quick_nonce}",
                label_visibility="collapsed",
            )
            if quick_batch:
                q_shots = [uf for uf in quick_batch if classify_upload_bucket(getattr(uf, "name", "")) == "screenshots"]
                has_gemini_q = bool(st.session_state.get("google_genai_api_key", "").strip())
                st.markdown(
                    f"""
                    <div style="
                        background-color: #1A2332;
                        border: 1px solid #3B82F6;
                        border-radius: 4px;
                        padding: 0.5rem 0.75rem;
                        margin-top: 0.35rem;
                        margin-bottom: 0.5rem;
                        font-size: 0.83rem;
                        color: #E5E7EB;
                    ">
                        📂 <b>{len(quick_batch)} file(s) staged for upload</b> — click <b>⬆️ Upload &amp; Sort Selected Files</b> below to save them into the 4 columns (you can keep clicking <i>Browse files</i> to add more before uploading).
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if q_shots and has_gemini_q:
                    qb_c1, qb_c2, qb_c3 = st.columns([2.1, 2.1, 0.9])
                else:
                    qb_c1, qb_c2, qb_c3 = st.columns([3.2, 0.01, 0.9])
                with qb_c1:
                    click_quick_upload = st.button(
                        f"⬆️ Upload & Sort {len(quick_batch)} Selected File(s) Now",
                        key=f"{key_prefix}_btn_quick_upload_{active_entity}_{active_tax_year}_{quick_nonce}",
                        type="primary",
                        use_container_width=True,
                    )
                with qb_c2:
                    click_quick_ocr = False
                    if q_shots and has_gemini_q:
                        click_quick_ocr = st.button(
                            f"🔍 Upload & Run AI OCR on {len(q_shots)} Screenshot(s)",
                            key=f"{key_prefix}_btn_quick_upload_ocr_{active_entity}_{active_tax_year}_{quick_nonce}",
                            type="secondary",
                            use_container_width=True,
                        )
                with qb_c3:
                    if st.button(
                        "✕ Clear",
                        key=f"{key_prefix}_btn_quick_clear_{active_entity}_{active_tax_year}_{quick_nonce}",
                        use_container_width=True,
                    ):
                        st.session_state[quick_nonce_key] = quick_nonce + 1
                        st.rerun()

                if click_quick_upload or click_quick_ocr:
                    q_prog = st.progress(0.0, text="Saving uploaded files to Vault & SQLite...")
                    newly_added_q = 0
                    tot_q = len(quick_batch)
                    for idx_q, up_file in enumerate(quick_batch, start=1):
                        q_prog.progress(
                            idx_q / tot_q,
                            text=f"Uploading ({idx_q}/{tot_q}): {up_file.name}...",
                        )
                        ok, _, _ = ingest_uploaded_file_object(
                            up_file,
                            entity=active_entity,
                            tax_year=active_tax_year,
                            run_ocr_immediately=True,
                        )
                        if ok:
                            newly_added_q += 1
                    q_prog.empty()
                    st.session_state[quick_nonce_key] = quick_nonce + 1
                    st.session_state[last_quick_msg_key] = (
                        f"✅ Uploaded & sorted {newly_added_q} file(s) into the 4 columns below! Ready to add more files anytime."
                    )
                    st.toast(f"Uploaded & sorted {newly_added_q} file(s) into columns.")
                    st.rerun()

        if st.session_state.get(f"_{key_prefix}_show_item_prop_setup_{active_entity}", False):
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)
            render_item_property_setup_matrix(
                entity=active_entity,
                tax_year=active_tax_year,
                key_prefix=f"{key_prefix}_inline_matrix",
            )
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

        # =====================================================================
        # 4-COLUMN BOARD: CSV | PDF | SCREENSHOTS | OTHER (EXCEL / ODT / ODS)
        # =====================================================================
        _render_ingestion_board(active_entity, active_tax_year, key_prefix, state_open_key, files_list)
    # =========================================================================
    # BLOCK 2 (STEP 2): PROPERTY LOANS & BANK REGISTRY (FOR BT PROPERTY HOLDINGS LTD)
    # =========================================================================
    if include_loans_block and is_loan_target_entity(active_entity):
        st.markdown("<div style='height: 0.45rem;'></div>", unsafe_allow_html=True)
        _front_loans_prefix = f"{key_prefix}_front_loans"
        _front_loans_expanded = bool(
            st.session_state.get(f"_{_front_loans_prefix}_show_loan_import")
            or st.session_state.get(f"_{_front_loans_prefix}_loan_ocr_result")
        )
        with st.expander(
            "STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
            expanded=_front_loans_expanded,
        ):
            render_property_loans_block(
                key_prefix=_front_loans_prefix,
                eyebrow_title="STEP 2 // PROPERTY LOANS & BANK REGISTRY — PROPERTIES & WHAT EACH LOAN IS (BT PROPERTY HOLDINGS LTD)",
                show_page_title=False,
            )
