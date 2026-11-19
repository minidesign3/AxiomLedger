"""Tests for the header AI status chip + popover (plan.md §11–§12)."""

from __future__ import annotations

import pytest
from ui_recorder import FakeSt, run_render

import axiom_ui_status as status

OK = {"connected": True, "label": "CONNECTED (40ms)"}
DOWN = {"connected": False, "label": "OFFLINE / UNREACHABLE"}
FAILED = {"connected": False, "label": "AUTH / CONNECTION ERROR"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(OK, "ok"), (DOWN, "idle"), (FAILED, "error"), ({}, "idle")],
)
def test_provider_state(value, expected):
    assert status.provider_state(value) == expected


@pytest.mark.parametrize(
    ("model", "gem", "loc", "bg", "state", "label"),
    [
        (status.GEMINI_MODEL, OK, DOWN, "Online", "ok", "Gemini"),
        (status.LOCAL_MODEL, OK, DOWN, "Online", "idle", "Local AI"),
        (status.GEMINI_MODEL, FAILED, OK, "Online", "error", "Gemini"),
        (status.LOCAL_MODEL, OK, OK, "Error", "error", "Local AI"),
        ("Rules Engine Only", DOWN, DOWN, "Running", "ok", "Rules only"),
    ],
)
def test_summarize_status(model, gem, loc, bg, state, label):
    got_state, got_label, tooltip = status.summarize_status(model, gem, loc, bg)
    assert (got_state, got_label) == (state, label)
    assert tooltip.startswith(label) or "background job" in tooltip


def _patch(monkeypatch, fake, *, overall="Online", debug=False):
    monkeypatch.setattr(status, "st", fake)
    monkeypatch.setattr(
        status,
        "get_background_status_summary",
        lambda: {"overall": overall, "headline": f"{overall.upper()} HEADLINE"},
    )
    monkeypatch.setattr(status, "debug_pages_enabled", lambda: debug)


def _markdown(log):
    return "\n".join(entry[1][0] for entry in log if entry[0] == "markdown")


def test_chip_uses_state_container_and_provider_label(monkeypatch):
    fake = FakeSt(
        session={
            "categorization_model": status.LOCAL_MODEL,
            "gemini_connection_status": OK,
            "local_ai_connection_status": DOWN,
        },
        clicks={"_status_open_settings_btn"},
    )
    _patch(monkeypatch, fake)
    log = run_render(status.render_status_popover, fake)

    assert ("container", [], {"key": "ws_status_idle"}) in log
    popovers = [entry for entry in log if entry[0] == "popover"]
    assert popovers[0][1] == ["Local AI"]
    assert popovers[0][2]["use_container_width"] is False
    assert "not connected" in popovers[0][2]["help"]

    markdown = _markdown(log)
    assert "axiom-status-row axiom-status-ok" in markdown  # Gemini row
    assert "axiom-status-row axiom-status-idle" in markdown  # Local AI row
    assert markdown.count("axiom-status-active") == 1
    assert "Background jobs" not in markdown  # idle + not debug → hidden
    assert fake.session_state["show_settings_drawer"] is True


@pytest.mark.parametrize(("overall", "debug", "shown"), [("Error", False, True), ("Online", True, True), ("Running", False, True), ("Online", False, False)])
def test_background_row_visibility(monkeypatch, overall, debug, shown):
    fake = FakeSt(session={"categorization_model": status.GEMINI_MODEL, "gemini_connection_status": OK})
    _patch(monkeypatch, fake, overall=overall, debug=debug)
    log = run_render(status.render_status_popover, fake)
    assert ("Background jobs" in _markdown(log)) is shown


def test_background_error_turns_chip_red(monkeypatch):
    fake = FakeSt(session={"categorization_model": status.GEMINI_MODEL, "gemini_connection_status": OK})
    _patch(monkeypatch, fake, overall="Error")
    log = run_render(status.render_status_popover, fake)
    assert ("container", [], {"key": "ws_status_error"}) in log


def test_popover_can_skip_background(monkeypatch):
    fake = FakeSt(session={"categorization_model": status.GEMINI_MODEL})
    _patch(monkeypatch, fake, overall="Error", debug=True)
    log = run_render(status.render_status_popover, fake, show_background=False)
    assert "Background jobs" not in _markdown(log)
    assert ("container", [], {"key": "ws_status_idle"}) in log


def test_row_escapes_html():
    row = status._status_row("<b>x</b>", "ok", "a & b")
    assert "&lt;b&gt;" in row and "a &amp; b" in row
