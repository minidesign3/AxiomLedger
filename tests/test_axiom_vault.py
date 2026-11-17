"""
Tests for ``axiom_vault.py``: vault directory construction and raw-upload
read/write.

Every test runs against an isolated **temporary** vault root (monkeypatched on
``axiom_db``) — never the real ``data/vault/`` (AGENTS.md: no real user files).
The module functions resolve ``VAULT_ROOT_DIR`` from their own globals, so the
patch has to land on ``axiom_vault.VAULT_ROOT_DIR`` as well as the ``axiom_db``
source constant; both are patched here.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

import pytest

import axiom_db
import axiom_vault
import utils


@pytest.fixture()
def temp_vault(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    vault_root = data_dir / "vault"
    monkeypatch.setattr(axiom_db, "DATA_DIR", data_dir)
    monkeypatch.setattr(axiom_db, "SQLITE_DB_PATH", data_dir / "axiom_ledger.db")
    monkeypatch.setattr(axiom_db, "VAULT_ROOT_DIR", vault_root)
    monkeypatch.setattr(axiom_db, "_SCHEMA_READY", threading.Event())
    monkeypatch.setattr(axiom_db, "_CONN_CACHE", {})
    monkeypatch.setattr(axiom_vault, "VAULT_ROOT_DIR", vault_root)
    yield vault_root


class TestGetVaultDir:
    def test_builds_entity_year_bucket_path(self, temp_vault):
        path = axiom_vault.get_vault_dir("BT Property Holdings Ltd", "2026", "Bank Statements")
        assert path == temp_vault / "BT Property Holdings Ltd" / "2026" / "Bank Statements"

    def test_does_not_create_directories(self, temp_vault):
        path = axiom_vault.get_vault_dir("Entity", "2026", "Bucket")
        assert not path.exists()

    def test_sanitises_path_separators(self, temp_vault):
        path = axiom_vault.get_vault_dir("A/B\\C", "2026", "x:y*z")
        assert path == temp_vault / "A_B_C" / "2026" / "x_y_z"

    def test_empty_segments_become_unnamed(self, temp_vault):
        path = axiom_vault.get_vault_dir("", "", "")
        assert path == temp_vault / "unnamed" / "unnamed" / "unnamed"


class TestSaveRawFileToVault:
    def test_writes_bytes_and_returns_path(self, temp_vault):
        dest = axiom_vault.save_raw_file_to_vault("Entity", "2026", "Bucket", "statement.csv", b"a,b\n1,2\n")
        assert dest == temp_vault / "Entity" / "2026" / "Bucket" / "statement.csv"
        assert dest.read_bytes() == b"a,b\n1,2\n"

    def test_creates_nested_folders(self, temp_vault):
        axiom_vault.save_raw_file_to_vault("New Entity", "2027", "Receipts", "r.pdf", b"%PDF-1.4")
        assert (temp_vault / "New Entity" / "2027" / "Receipts" / "r.pdf").is_file()

    def test_overwrites_existing_file(self, temp_vault):
        axiom_vault.save_raw_file_to_vault("E", "2026", "B", "f.csv", b"old")
        dest = axiom_vault.save_raw_file_to_vault("E", "2026", "B", "f.csv", b"new")
        assert dest.read_bytes() == b"new"

    def test_preserves_bucket_case(self, temp_vault):
        dest = axiom_vault.save_raw_file_to_vault("E", "2026", "Bank Statements", "f.csv", b"x")
        assert "Bank Statements" in str(dest)

    def test_sanitises_filename(self, temp_vault):
        dest = axiom_vault.save_raw_file_to_vault("E", "2026", "B", "bad:name?.csv", b"x")
        assert dest.name == "bad_name_.csv"


class TestSaveRawFileToVaultPrivate:
    def test_returns_absolute_path_when_vault_is_outside_repo(self, temp_vault):
        # relative_to(repo_root) fails for an out-of-tree vault, so the raw absolute
        # path is the documented fallback (the path must still be usable).
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "Bank Statements", "f.csv", b"x")
        assert Path(rel).is_absolute()
        assert Path(rel).is_file()
        assert Path(rel).read_bytes() == b"x"

    def test_returns_repo_relative_path_for_in_tree_vault(self, tmp_path, monkeypatch):
        repo_root = Path(axiom_vault.__file__).resolve().parent
        in_tree = Path(tempfile.mkdtemp(dir=repo_root, prefix=".vault_test_"))
        try:
            monkeypatch.setattr(axiom_vault, "VAULT_ROOT_DIR", in_tree)
            rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "b", "f.csv", b"x")
            assert not Path(rel).is_absolute()
            assert (repo_root / rel).is_file()
        finally:
            shutil.rmtree(in_tree, ignore_errors=True)

    def test_lowercases_bucket(self, temp_vault):
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "Bank Statements", "f.csv", b"x")
        assert Path(rel).parent.name == "bank statements"

    def test_strips_directories_from_filename(self, temp_vault):
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "b", "../../escape.csv", b"x")
        assert Path(rel).name == "escape.csv"
        assert Path(rel).parent.name == "b"

    def test_sanitises_filename_characters(self, temp_vault):
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "b", 'a<b>c:"d|e?f*g.csv', b"x")
        assert Path(rel).name == "a_b_c__d_e_f_g.csv"

    def test_falls_back_when_filename_empty(self, temp_vault):
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "b", "", b"x")
        assert Path(rel).name == "Uploaded_File"

    def test_swallows_write_errors(self, temp_vault, monkeypatch):
        def boom(self, data):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", boom)
        rel = axiom_vault._save_raw_file_to_vault("Entity", "2026", "b", "f.csv", b"x")
        assert Path(rel).name == "f.csv"


class TestGetVaultFileBytes:
    def test_reads_bytes_from_vault_path(self, temp_vault):
        dest = axiom_vault.save_raw_file_to_vault("E", "2026", "B", "f.csv", b"hello")
        assert axiom_vault.get_vault_file_bytes({"vault_path": str(dest)}) == b"hello"

    def test_falls_back_to_inline_raw_bytes(self, temp_vault):
        assert axiom_vault.get_vault_file_bytes({"raw_bytes": bytearray(b"inline")}) == b"inline"

    def test_missing_file_falls_back_to_raw_bytes(self, temp_vault):
        rec = {"vault_path": str(temp_vault / "nope.csv"), "raw_bytes": b"cached"}
        assert axiom_vault.get_vault_file_bytes(rec) == b"cached"

    def test_returns_none_when_nothing_available(self, temp_vault):
        assert axiom_vault.get_vault_file_bytes({}) is None

    def test_returns_none_for_non_bytes_raw(self, temp_vault):
        assert axiom_vault.get_vault_file_bytes({"raw_bytes": "not bytes"}) is None

    def test_ignores_blank_vault_path(self, temp_vault):
        assert axiom_vault.get_vault_file_bytes({"vault_path": "   ", "raw_bytes": b"x"}) == b"x"


def test_utils_reexports_same_objects():
    assert utils.get_vault_dir is axiom_vault.get_vault_dir
    assert utils.save_raw_file_to_vault is axiom_vault.save_raw_file_to_vault
    assert utils.get_vault_file_bytes is axiom_vault.get_vault_file_bytes
    assert utils._save_raw_file_to_vault is axiom_vault._save_raw_file_to_vault
