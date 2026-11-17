"""
axiom_ui_ingestion_upload
=========================

Bulk upload dropzone used by the ingestion board (phase D20).
"""

from __future__ import annotations

import streamlit as st

from axiom_core import _safe_vault_segment, classify_upload_bucket
from axiom_ledger import export_entity_vault_zip
from axiom_properties import get_entity_properties
from axiom_rules import get_memorized_rules
from axiom_state import format_tax_year_period
from axiom_ui_components import render_property_editor_widget
from axiom_ui_setup_matrix import render_item_property_setup_matrix
from axiom_upload import ingest_uploaded_file_object


def _render_bulk_upload_dropzone(
    active_entity: str,
    active_tax_year: str,
    files_list: list[dict],
    key_prefix: str = "ing",
    eyebrow_label: str = "UPLOAD ALL FILES AT ONCE &mdash; AUTO-SAVED TO SQLITE &amp; DISK VAULT",
) -> None:
    period_label = format_tax_year_period(active_tax_year, active_entity)
    with st.container(border=True):
        top_u1, top_u2, top_u3, top_u4 = st.columns([2.2, 0.95, 1.05, 0.95])
        with top_u1:
            st.markdown(
                f"""
                <div>
                    <div class="axiom-eyebrow">{eyebrow_label}</div>
                    <h3 style="margin: 0; font-size: 1.08rem;">Upload Bank CSVs, PDF Statements, Screenshots &amp; Excel/ODT Files ({active_entity} &bull; {period_label})</h3>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with top_u2:
            with st.popover(f"🏠 Edit Properties ({len(get_entity_properties(active_entity))})", use_container_width=True):
                st.markdown(f'<div class="axiom-eyebrow">EDIT PROPERTIES // {active_entity.upper()}</div>', unsafe_allow_html=True)
                render_property_editor_widget(entity=active_entity, key_prefix=f"{key_prefix}_board_prop")
        with top_u3:
            show_setup_key = f"_{key_prefix}_show_item_prop_setup_{active_entity}"
            is_setup_open = bool(st.session_state.get(show_setup_key, False))
            if st.button(
                "✕ Close Item + Property Setup" if is_setup_open else f"⚙️ Item + Property Setup ({len(get_memorized_rules(active_entity))})",
                key=f"{key_prefix}_btn_toggle_item_prop_{active_entity}",
                type="primary" if is_setup_open else "secondary",
                use_container_width=True,
                help="Specify which Insurance, Loan Interest, Rent Income, or Rates belongs to which Property in clean columns.",
            ):
                st.session_state[show_setup_key] = not is_setup_open
                st.rerun()
        with top_u4:
            zip_bytes = export_entity_vault_zip(active_entity, active_tax_year)
            safe_ent_slug = _safe_vault_segment(active_entity).replace(" ", "_")
            st.download_button(
                label=f"📦 Vault Zip ({len(files_list)} files)",
                data=zip_bytes,
                file_name=f"AxiomVault_{safe_ent_slug}_{active_tax_year}.zip",
                mime="application/zip",
                key=f"{key_prefix}_dl_vault_zip_{active_entity}_{active_tax_year}",
                use_container_width=True,
                help="Download a ZIP bundle containing all original CSVs, PDFs, Screenshots, Excel/ODT files AND the coded Master CSV for your accountant.",
            )

        if st.session_state.get(f"_{key_prefix}_show_item_prop_setup_{active_entity}", False):
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)
            render_item_property_setup_matrix(
                entity=active_entity,
                tax_year=active_tax_year,
                key_prefix=f"{key_prefix}_inline_matrix",
            )
            st.markdown('<div class="axiom-divider" style="margin: 0.65rem 0;"></div>', unsafe_allow_html=True)

        master_nonce_key = f"_{key_prefix}_master_up_nonce_{active_entity}_{active_tax_year}"
        master_nonce = int(st.session_state.get(master_nonce_key, 0))
        last_master_msg_key = f"_{key_prefix}_master_up_msg_{active_entity}_{active_tax_year}"
        if st.session_state.get(last_master_msg_key):
            st.success(str(st.session_state.pop(last_master_msg_key)))

        uploaded_batch = st.file_uploader(
            "Select or drag-and-drop all your files here (CSV, PDF, PNG/JPG Screenshots, Excel .xlsx/.xls, OpenDocument .ods/.odt)",
            type=["csv", "pdf", "png", "jpg", "jpeg", "webp", "xlsx", "xls", "ods", "odt", "tsv"],
            accept_multiple_files=True,
            key=f"{key_prefix}_master_multi_uploader_{active_entity}_{active_tax_year}_{master_nonce}",
        )

        if uploaded_batch:
            has_shots = any(classify_upload_bucket(getattr(uf, "name", "")) == "screenshots" for uf in uploaded_batch)
            has_gemini = bool(st.session_state.get("google_genai_api_key", "").strip())
            ub_c1, ub_c2, ub_c3 = st.columns([2.0, 2.0, 1.0] if (has_shots and has_gemini) else [3.0, 0.01, 1.0])
            with ub_c1:
                do_fast_upload = st.button(
                    f"⬆️ Upload & Sort {len(uploaded_batch)} Selected File(s) Now",
                    key=f"{key_prefix}_btn_master_upload_{active_entity}_{active_tax_year}_{master_nonce}",
                    type="primary",
                    use_container_width=True,
                )
            with ub_c2:
                do_ocr_upload = False
                if has_shots and has_gemini:
                    do_ocr_upload = st.button(
                        f"🔍 Upload & Run AI OCR on Screenshots ({len(uploaded_batch)} file(s))",
                        key=f"{key_prefix}_btn_master_upload_ocr_{active_entity}_{active_tax_year}_{master_nonce}",
                        type="secondary",
                        use_container_width=True,
                    )
            with ub_c3:
                if st.button(
                    "✕ Clear Queue",
                    key=f"{key_prefix}_btn_master_clear_{active_entity}_{active_tax_year}_{master_nonce}",
                    use_container_width=True,
                ):
                    st.session_state[master_nonce_key] = master_nonce + 1
                    st.rerun()

            if do_fast_upload or do_ocr_upload:
                prog = st.progress(0.0, text="Uploading files to Vault & SQLite...")
                newly_added = 0
                total_u = len(uploaded_batch)
                for idx_u, up_file in enumerate(uploaded_batch, start=1):
                    prog.progress(
                        idx_u / total_u,
                        text=f"Processing ({idx_u}/{total_u}): {up_file.name}...",
                    )
                    ok, _, _ = ingest_uploaded_file_object(
                        up_file,
                        entity=active_entity,
                        tax_year=active_tax_year,
                        run_ocr_immediately=True,
                    )
                    if ok:
                        newly_added += 1
                prog.empty()
                st.session_state[master_nonce_key] = master_nonce + 1
                st.session_state[last_master_msg_key] = (
                    f"✅ Uploaded & sorted {newly_added} file(s) into the 4 columns below."
                )
                st.rerun()
