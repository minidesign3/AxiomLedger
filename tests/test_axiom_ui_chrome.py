"""Tests for the workspace chrome split (D22).

The top header and left sidebar are driven with ``ui_recorder.FakeSt`` and
deterministic dependency fakes; no real authentication or ledger data involved.
"""

from __future__ import annotations

from ui_recorder import FakeSt, RerunSignal, run_render

import axiom_ui_chrome as chrome

ENTITY = "BT Property Holdings Ltd"
YEAR = "2025"


def names(log, kind=None):
    return [entry[0] for entry in log if kind is None or entry[0] == kind]


def texts(log, kind):
    return [entry[1][0] for entry in log if entry[0] == kind and entry[1]]


def buttons(log):
    return [entry[2]["key"] for entry in log if entry[0] == "button" and "key" in entry[2]]


class SidebarSt:
    """Fake ``st`` whose ``sidebar`` is a context manager over the recorder."""

    def __init__(self, fake):
        self._fake = fake

    def __getattr__(self, name):
        return getattr(self._fake, name)

    @property
    def sidebar(self):
        class _Sidebar:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Sidebar()


def patch_chrome(monkeypatch, fake, calls=None, results=None, *, gem_ok=True, loc_ok=True):
    calls = calls if calls is not None else []
    results = results or {}

    def event(name):
        def fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return results.get(name)

        return fn

    monkeypatch.setattr(chrome, "st", SidebarSt(fake))
    monkeypatch.setattr(chrome, "init_workspace_state", event("init"))
    monkeypatch.setattr(chrome, "get_active_entity", lambda: fake.session_state.get("active_entity", ENTITY))
    monkeypatch.setattr(chrome, "get_active_tax_year", lambda: fake.session_state.get("active_tax_year", YEAR))
    monkeypatch.setattr(chrome, "get_entity_jurisdiction", lambda e: "US" if "Trust" in e else "NZ")
    monkeypatch.setattr(chrome, "get_entity_options", lambda: [ENTITY, "Marine Trust"])
    monkeypatch.setattr(chrome, "get_tax_year_options", lambda: ["2024", "2025", "2026"])
    monkeypatch.setattr(chrome, "get_entity_ledger", lambda e, y: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(chrome, "format_tax_year_period", lambda y, e: f"{y} period")
    monkeypatch.setattr(chrome, "add_new_tax_year", event("add_year"))
    monkeypatch.setattr(chrome, "_save_memory_store", event("save_memory"))
    monkeypatch.setattr(chrome, "render_right_settings_drawer", event("settings"))
    monkeypatch.setattr(chrome, "render_global_status_indicator", event("status"))
    monkeypatch.setattr(
        chrome,
        "_render_connection_badge",
        lambda label, status, is_selected=False: f"BADGE {label} {bool(status)} {is_selected}",
    )
    monkeypatch.setattr(chrome, "sign_out_current_session", event("sign_out"))
    return calls


def run_header(fake, *, status=False):
    return run_render(chrome.render_workspace_header, fake, status)


def test_header_renders_banner_badges_and_year_pills(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    patch_chrome(monkeypatch, fake)
    log = run_header(fake)

    markdown = "\n".join(texts(log, "markdown"))
    assert "ORGANIZATION WORKSPACE" in markdown
    assert "2025 PERIOD" in markdown
    assert any(text.startswith("BADGE GEMINI") for text in markdown.splitlines())
    assert any(text.startswith("BADGE LOCAL AI") for text in markdown.splitlines())
    assert "_top_toolbar_settings_btn" in buttons(log)
    assert "_fy_pill_btn_2024" in buttons(log)
    assert "button" in names(log)


def test_header_status_indicator_column(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    calls = patch_chrome(monkeypatch, fake)
    run_header(fake, status=True)
    assert ("status", (), {}) in calls


def test_header_drawer_open_renders_only_settings(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "show_settings_drawer": True})
    calls = patch_chrome(monkeypatch, fake)

    run_header(fake)

    assert calls == [("init", (), {}), ("settings", (), {})]


def test_header_click_year_switches_year(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_fy_pill_btn_2026"})
    patch_chrome(monkeypatch, fake)

    try:
        run_header(fake)
    except RerunSignal:
        pass

    assert fake.session_state["active_tax_year"] == "2026"


def test_header_add_year_failure_shows_error(monkeypatch):
    fake = FakeSt(
        session={"active_entity": ENTITY, "active_tax_year": YEAR},
        clicks={"_btn_confirm_add_year"},
        values={"_popover_new_tax_year_input": "2020"},
    )
    patch_chrome(monkeypatch, fake, results={"add_year": (False, "Year must be after 2024")})
    log = run_header(fake)

    assert "Year must be after 2024" in texts(log, "error")


def test_sidebar_lists_orgs_and_switches_workspace(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_sidebar_org_btn_1_Marine Trust"})
    calls = patch_chrome(monkeypatch, fake)

    try:
        run_render(chrome.render_sidebar_session, fake, object())
    except RerunSignal:
        pass

    labels = texts(fake.log, "button")
    assert any(f"{ENTITY} [NZ IRD] (2)" in str(label) for label in labels)
    assert fake.session_state["active_entity"] == "Marine Trust"
    assert ("save_memory", (), {}) in calls


def test_sidebar_signout_calls_auth(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_sidebar_explicit_signout_btn"})
    calls = patch_chrome(monkeypatch, fake)
    auth = object()

    run_render(chrome.render_sidebar_session, fake, auth)

    assert ("sign_out", (auth,), {}) in calls
    assert "switch_page" in names(fake.log)


def test_utils_reexports_chrome_api():
    import utils

    assert utils.render_workspace_header is chrome.render_workspace_header
    assert utils.render_sidebar_workspace_controls is chrome.render_sidebar_workspace_controls
    assert utils.render_sidebar_session is chrome.render_sidebar_session
