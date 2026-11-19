"""
axiom_nav
=========

Single source of truth for app navigation (plan.md §11). ``app.py`` is the
router: it builds the ``st.Page`` list here and runs the selected page. Every
``st.switch_page`` target must use the constants below instead of raw paths.

Page map (sidebar order):
  Workspace — Inbox (upload & file board), Review & Ledger, Loans
  System    — Background Jobs (only when ``AXIOM_DEBUG=1``; demo-only workers)
"""

from __future__ import annotations

import os

import streamlit as st

INBOX_PAGE = "views/inbox.py"
LEDGER_PAGE = "views/ledger.py"
LOANS_PAGE = "views/loans.py"
JOBS_PAGE = "views/jobs.py"

_TRUTHY = {"1", "true", "yes", "on"}


def debug_pages_enabled() -> bool:
    """Demo/diagnostic pages are only registered when ``AXIOM_DEBUG`` is truthy."""
    return os.environ.get("AXIOM_DEBUG", "").strip().lower() in _TRUTHY


def build_navigation_pages() -> dict[str, list[st.Page]]:
    """Return the grouped ``st.Page`` mapping passed to ``st.navigation``."""
    sections: dict[str, list[st.Page]] = {
        "Workspace": [
            st.Page(INBOX_PAGE, title="Inbox", icon=":material/inbox:", url_path="inbox", default=True),
            st.Page(LEDGER_PAGE, title="Review & Ledger", icon=":material/fact_check:", url_path="ledger"),
            st.Page(LOANS_PAGE, title="Loans", icon=":material/account_balance:", url_path="loans"),
        ],
    }
    if debug_pages_enabled():
        sections["System"] = [
            st.Page(JOBS_PAGE, title="Background Jobs", icon=":material/memory:", url_path="jobs"),
        ]
    return sections


def page_identity(page: object) -> str:
    """Stable identifier of the selected page (used to close Settings on navigation)."""
    url_path = getattr(page, "url_path", "") or ""
    title = getattr(page, "title", "") or ""
    return f"{url_path}|{title}"
