"""Tests for the ``st.navigation`` page registry (plan.md §11)."""

from __future__ import annotations

from pathlib import Path

import pytest

import axiom_nav as nav

ROOT = Path(__file__).resolve().parent.parent


class FakePage:
    def __init__(self, page, *, title, icon=None, url_path=None, default=False):
        self.page = page
        self.title = title
        self.icon = icon
        self.url_path = url_path
        self.default = default


@pytest.fixture
def fake_pages(monkeypatch):
    monkeypatch.setattr(nav.st, "Page", FakePage)


def test_workspace_pages_are_named_and_ordered(fake_pages, monkeypatch):
    monkeypatch.delenv("AXIOM_DEBUG", raising=False)
    sections = nav.build_navigation_pages()

    assert list(sections) == ["Workspace"]
    titles = [p.title for p in sections["Workspace"]]
    assert titles == ["Inbox", "Review & Ledger", "Loans", "Ask Axiom"]
    assert [p.default for p in sections["Workspace"]] == [True, False, False, False]


def test_loans_page_hidden_for_entities_without_loans(fake_pages, monkeypatch):
    monkeypatch.delenv("AXIOM_DEBUG", raising=False)
    sections = nav.build_navigation_pages(show_loans=False)

    pages = sections["Workspace"]
    assert [p.title for p in pages] == ["Inbox", "Review & Ledger", "Ask Axiom"]
    assert nav.LOANS_PAGE not in {p.page for p in pages}


@pytest.mark.parametrize("flag", ["1", "true", "YES"])
def test_debug_flag_adds_background_jobs(fake_pages, monkeypatch, flag):
    monkeypatch.setenv("AXIOM_DEBUG", flag)
    sections = nav.build_navigation_pages()
    assert [p.title for p in sections["System"]] == ["Background Jobs"]


def test_every_registered_page_file_exists(fake_pages, monkeypatch):
    monkeypatch.setenv("AXIOM_DEBUG", "1")
    for pages in nav.build_navigation_pages().values():
        for page in pages:
            assert (ROOT / page.page).is_file(), page.page


def test_legacy_pages_dir_removed():
    # A ``pages/`` directory would make Streamlit auto-discover pages alongside st.navigation.
    assert not (ROOT / "pages").exists()


def test_page_identity_distinguishes_pages():
    a = FakePage("x", title="Inbox", url_path="inbox")
    b = FakePage("y", title="Review & Ledger", url_path="ledger")
    assert nav.page_identity(a) != nav.page_identity(b)
    assert nav.page_identity(object()) == "|"
