"""
axiom_ui_export_pack
====================

"Send to Accountant" panel (Tab 04): runs the export-readiness checks, lists
blockers / warnings / info, and offers the one-file Accountant Pack (.xlsx).
Download is enabled when there are no blockers, or once the user ticks that
they've reviewed them (they're listed on the pack's Checks sheet either way).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from axiom_accountant_pack import build_accountant_pack, pack_filename
from axiom_pack_sheets import recon_summary
from axiom_pm_recon import save_payee_map

_ICONS = {"blocker": "🔴", "warning": "🟡", "info": "ℹ️"}


def _render_checks(checks) -> None:
    for level in ("blocker", "warning", "info"):
        items = [c for c in checks if c.level == level]
        for c in items:
            line = f"{_ICONS[level]} **{c.name}** ({c.count}) — {c.detail}"
            if level == "blocker":
                st.error(line)
            elif level == "warning":
                st.warning(line)
            else:
                st.caption(line)


def _render_rent_check(entity: str, result) -> None:
    """PM statement payouts vs bank deposits, plus the editable payee keyword table."""
    recon = result.recon
    if recon is None or not recon.managers:
        st.caption("Rent check: no property-manager statements with owner payouts uploaded yet.")
        return
    all_ok = all(m.ok for m in recon.managers)
    title = "✅ Rent check — every manager's payouts match the bank" if all_ok else "⚠️ Rent check — differences to review"
    with st.expander(title, expanded=not all_ok):
        summary = recon_summary(recon)
        st.dataframe(
            summary[["Manager", "Basis", "Statement paid to you", "Bank received", "Difference", "Status"]],
            use_container_width=True,
            hide_index=True,
        )
        for m in recon.managers:
            if m.monthly is not None and not m.ok:
                st.caption(f"{m.manager} — month by month (running totals show where a gap starts):")
                st.dataframe(m.monthly, use_container_width=True, hide_index=True)
        st.caption(
            "Bank deposits are matched to a manager when their description contains one of these keywords. "
            "Edit if a manager pays under another name (comma-separated)."
        )
        edit_df = pd.DataFrame({"Manager": [m.manager for m in recon.managers],
                                "Bank payee keywords": [", ".join(m.keywords) for m in recon.managers]})
        edited = st.data_editor(edit_df, hide_index=True, num_rows="dynamic", use_container_width=True,
                                key=f"_payee_map_{entity}")
        if st.button("💾 Save payee keywords", key=f"_payee_map_save_{entity}"):
            save_payee_map(entity, {str(r["Manager"]).strip(): str(r["Bank payee keywords"] or "").split(",")
                                    for _, r in edited.iterrows() if str(r["Manager"] or "").strip()})
            st.rerun()


def render_accountant_export_panel(entity: str, tax_year: str) -> None:
    """Readiness checklist + Accountant Pack download for ``(entity, tax_year)``."""
    st.markdown('<div class="axiom-eyebrow">SEND TO ACCOUNTANT // ONE-FILE ACCOUNTANT PACK</div>', unsafe_allow_html=True)
    include_overlaps = st.checkbox(
        "Include screenshot rows that repeat a bank-CSV row",
        value=False,
        key=f"_pack_overlaps_{entity}_{tax_year}",
        help="Off (recommended): a screenshot of an account whose CSV is also imported would double-count.",
    )
    try:
        pack_bytes, result = build_accountant_pack(entity, tax_year, include_overlaps=include_overlaps)
    except Exception as exc:
        st.error(f"Could not build the Accountant Pack: {exc}")
        return

    blockers = result.blockers
    if blockers:
        st.error(f"❌ Not ready: {len(blockers)} blocking issue(s). Fix them, or review and export anyway.")
    else:
        st.success(f"✅ Ready to send — {len(result.rows)} transactions, no blocking issues.")
    with st.expander(f"Readiness checklist ({len(result.checks)} item(s))", expanded=bool(blockers)):
        if result.checks:
            _render_checks(result.checks)
        else:
            st.caption("All checks passed.")

    _render_rent_check(entity, result)

    acknowledged = True
    if blockers:
        acknowledged = st.checkbox(
            f"I've reviewed the {len(blockers)} blocking issue(s) and want to export anyway "
            "(they will be listed on the Checks sheet)",
            value=False,
            key=f"_pack_ack_{entity}_{tax_year}",
        )
    st.download_button(
        label="⬇ Download Accountant Pack (.xlsx)",
        data=pack_bytes,
        file_name=pack_filename(entity, tax_year),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        disabled=not acknowledged,
        use_container_width=True,
        help="Summary per property, all transactions with source file, checks, and excluded rows.",
    )
