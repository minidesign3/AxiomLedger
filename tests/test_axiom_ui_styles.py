"""Tests for axiom_ui_styles (D16): external stylesheet loading and injection."""

from __future__ import annotations

import axiom_ui_styles


def test_stylesheet_file_exists_and_is_plain_css():
    css = axiom_ui_styles.STYLES_CSS_PATH.read_text(encoding="utf-8")
    assert ".stApp {" in css
    assert "{{" not in css and "}}" not in css
    assert css.count("{") == css.count("}")


def test_authenticated_css_keeps_sidebar_visible():
    css = axiom_ui_styles.build_custom_css(authenticated=True)
    assert css.startswith("<style>") and css.endswith("</style>")
    assert 'display: none !important' not in css.split("[data-testid=\"stAlert\"]")[-1]
    assert '[data-testid="collapsedControl"]' not in css


def test_unauthenticated_css_hides_sidebar():
    css = axiom_ui_styles.build_custom_css(authenticated=False)
    assert '[data-testid="collapsedControl"]' in css
    assert "display: none !important" in css


def test_apply_custom_styles_injects_html(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        axiom_ui_styles.st, "markdown", lambda body, **kw: calls.append((body, kw))
    )
    axiom_ui_styles.apply_custom_styles(authenticated=False)
    assert len(calls) == 1
    body, kwargs = calls[0]
    assert kwargs == {"unsafe_allow_html": True}
    assert body == axiom_ui_styles.build_custom_css(authenticated=False)


def test_reexported_from_utils():
    import utils

    assert utils.apply_custom_styles is axiom_ui_styles.apply_custom_styles
