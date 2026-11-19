"""Tests for the workspace chrome split (D22).

The top header and left sidebar are driven with ``ui_recorder.FakeSt`` and
deterministic dependency fakes; no real authentication or ledger data involved.
"""

from __future__ import annotations

from ui_recorder import FakeSt, run_render

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
    monkeypatch.setattr(chrome, "render_status_popover", event("status"))
    monkeypatch.setattr(chrome, "sign_out_current_session", event("sign_out"))
    return calls


def run_header(fake, *, status=False):
    return run_render(chrome.render_workspace_header, fake, status)


def run_sidebar(fake, auth=None, current_page=None):
    return run_render(chrome.render_sidebar_session, fake, auth or object(), current_page)


def test_header_is_one_line_banner_plus_status_popover(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    calls = patch_chrome(monkeypatch, fake)
    log = run_header(fake)

    markdown = "\n".join(texts(log, "markdown"))
    assert "NZ IRD" in markdown
    assert "TAX PERIOD 2025 PERIOD" in markdown
    assert f"{ENTITY} &mdash; FY {YEAR}" in markdown
    # Decluttered: no header Settings button, no year pills, no per-provider badges.
    assert buttons(log) == []
    assert ("status", (), {"show_background": False}) in calls


def test_header_status_popover_includes_background_when_requested(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    calls = patch_chrome(monkeypatch, fake)
    run_header(fake, status=True)
    assert ("status", (), {"show_background": True}) in calls


def test_header_drawer_open_renders_only_settings(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "show_settings_drawer": True})
    calls = patch_chrome(monkeypatch, fake)

    log = run_header(fake)

    assert calls == [("init", (), {}), ("settings", (), {})]
    assert ("stop",) in log


def test_sidebar_year_selector_syncs_and_callback_switches_year(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    patch_chrome(monkeypatch, fake)
    log = run_sidebar(fake)

    selects = [entry for entry in log if entry[0] == "selectbox"]
    assert selects and selects[0][1][1] == ["2024", "2025", "2026"]
    assert fake.session_state[chrome.YEAR_SELECT_KEY] == YEAR

    fake.session_state[chrome.YEAR_SELECT_KEY] = "2026"
    chrome._on_year_selected()
    assert fake.session_state["active_tax_year"] == "2026"


def test_sidebar_add_year_failure_shows_error(monkeypatch):
    fake = FakeSt(
        session={"active_entity": ENTITY, "active_tax_year": YEAR},
        clicks={"_btn_confirm_add_year"},
        values={"_popover_new_tax_year_input": "2020"},
    )
    patch_chrome(monkeypatch, fake, results={"add_year": (False, "Year must be after 2024")})
    log = run_sidebar(fake)

    assert "Year must be after 2024" in texts(log, "error")


def test_sidebar_lists_orgs_and_switches_workspace(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_sidebar_org_btn_1_Marine Trust"})
    calls = patch_chrome(monkeypatch, fake)

    run_sidebar(fake)

    org_buttons = [e for e in fake.log if e[0] == "button" and str(e[2].get("key", "")).startswith("_sidebar_org_btn_")]
    assert [e[1][0] for e in org_buttons] == [ENTITY, "Marine Trust"]
    assert org_buttons[0][2]["type"] == "primary"
    assert org_buttons[0][2]["icon"] == ":material/domain:"
    assert "2 rows in FY 2025" in org_buttons[0][2]["help"]
    assert "click to switch" in org_buttons[1][2]["help"]
    assert ("container", [], {"key": "sb_orgs"}) in fake.log
    assert fake.session_state["active_entity"] == "Marine Trust"
    assert ("save_memory", (), {}) in calls


def test_sidebar_counts_rows_only_for_active_org(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR})
    patch_chrome(monkeypatch, fake)
    counted = []
    monkeypatch.setattr(chrome, "get_entity_ledger", lambda e, y: counted.append(e) or [])

    run_sidebar(fake)

    assert counted == [ENTITY]


def test_sidebar_has_single_settings_toggle(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_sidebar_open_settings_btn"})
    patch_chrome(monkeypatch, fake)

    log = run_sidebar(fake)

    assert buttons(log).count("_sidebar_open_settings_btn") == 1
    assert fake.session_state["show_settings_drawer"] is True


def test_sidebar_page_change_closes_settings(monkeypatch):
    fake = FakeSt(
        session={
            "active_entity": ENTITY,
            "active_tax_year": YEAR,
            "show_settings_drawer": True,
            "_last_rendered_page": "inbox|Inbox",
        }
    )
    patch_chrome(monkeypatch, fake)

    run_sidebar(fake, current_page="inbox|Inbox")
    assert fake.session_state["show_settings_drawer"] is True

    run_sidebar(fake, current_page="ledger|Review & Ledger")
    assert fake.session_state["show_settings_drawer"] is False
    assert fake.session_state["_last_rendered_page"] == "ledger|Review & Ledger"


def test_sidebar_signout_calls_auth_and_reruns(monkeypatch):
    fake = FakeSt(session={"active_entity": ENTITY, "active_tax_year": YEAR}, clicks={"_sidebar_explicit_signout_btn"})
    calls = patch_chrome(monkeypatch, fake)
    auth = object()

    log = run_sidebar(fake, auth)

    assert ("sign_out", (auth,), {}) in calls
    assert ("rerun",) in log


def test_utils_reexports_chrome_api():
    import utils

    assert utils.render_workspace_header is chrome.render_workspace_header
    assert utils.render_sidebar_workspace_controls is chrome.render_sidebar_workspace_controls
    assert utils.render_sidebar_session is chrome.render_sidebar_session
