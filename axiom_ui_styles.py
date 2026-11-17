"""
axiom_ui_styles
===============

Global Streamlit stylesheet. The CSS itself lives in ``assets/styles.css`` (so it
can be edited and diffed as real CSS rather than as an escaped f-string);
``apply_custom_styles`` injects it into the page and appends the rule that hides
the sidebar chrome while the operator is not authenticated.

Extracted from ``utils.py`` (phase D16). The CSS text is the former f-string body
with ``{{``/``}}`` unescaped; ``apply_custom_styles`` is an intentional edit
(waived in ``scripts/check_extraction.py``) because it now reads the file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import streamlit as st

STYLES_CSS_PATH = Path(__file__).resolve().parent / "assets" / "styles.css"

_UNAUTHENTICATED_SIDEBAR_CSS = """
[data-testid="stSidebar"],
[data-testid="collapsedControl"],
[data-testid="stSidebarNav"] {
    display: none !important;
}
"""


@lru_cache(maxsize=1)
def _load_base_css() -> str:
    """Read the shared stylesheet once per process."""
    return STYLES_CSS_PATH.read_text(encoding="utf-8")


def build_custom_css(authenticated: bool = False) -> str:
    """Return the full ``<style>`` block for the given authentication state."""
    sidebar_visibility_css = "" if authenticated else _UNAUTHENTICATED_SIDEBAR_CSS
    return f"<style>\n{_load_base_css()}\n{sidebar_visibility_css}\n</style>"


def apply_custom_styles(authenticated: bool = False) -> None:
    """
    Enforce a high-end, technical dark charcoal/graphite aesthetic across all pages.
    Strictly eliminates neon, cyan, magenta, or glowing effects.
    Hides sidebar navigation completely when unauthenticated.
    """
    st.markdown(build_custom_css(authenticated), unsafe_allow_html=True)
