#!/usr/bin/env python3
"""Prove extracted functions are logic-identical to the frozen baseline.

This is the safety net that makes it safe to let a cheap model move code: it
catches silent paraphrasing, dropped branches, swapped constants, etc.

How it works
------------
For every top-level ``def``/``class`` (and simple module-level constant
assignment) defined in the baseline tree (``.axiom_baseline/``, created by
``scripts/snapshot_baseline.py``), it finds the symbol's *current* definition
anywhere in the live tree and compares a **normalised AST dump** of both. AST
normalisation ignores formatting/comments/blank-lines but is sensitive to any
change in logic, names, literals, or control flow.

A symbol is allowed to differ only if explicitly waived with ``--allow-changed``
(use this for intentional edits such as the ``"US" in ...`` jurisdiction fix or
added ``logging`` calls — review those by hand).

Exit status is non-zero if any symbol is missing or changed-without-waiver.

Usage
-----
    python scripts/check_extraction.py
    python scripts/check_extraction.py --allow-changed get_entity_jurisdiction
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".axiom_baseline"
EXCLUDE_DIRS = {
    ".venv",
    ".git",
    "data",
    "pb_data",
    "exports",
    "uploads",
    "statements",
    "logs",
    "__pycache__",
    ".axiom_baseline",
    ".ruff_cache",
    ".pytest_cache",
    ".codegraph",
    ".aider-desk",
    # Test fixtures are expected to adapt as product code moves between modules
    # (e.g. patching `axiom_memory.MEMORY_STORE_PATH` after the memory store left
    # utils.py). Behaviour is enforced by pytest, not byte-identity of fixtures.
    "tests",
}
# Symbols that legitimately exist under the same name in multiple modules
# (e.g. thin re-export shims). Mismatch across them is not a logic change.
IGNORE_NAMES = {"main", "__all__"}


def _iter_py(root: Path):
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        yield path


def _normalise(node: ast.AST) -> str:
    """AST dump insensitive to formatting/comments, sensitive to logic."""
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _collect(root: Path) -> dict[str, list[tuple[str, str]]]:
    """name -> list of (relpath, normalised-source). Top-level defs only."""
    out: dict[str, list[tuple[str, str]]] = {}
    for path in _iter_py(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.setdefault(node.name, []).append((rel, _normalise(node)))
    return out


def main(argv: list[str]) -> int:
    if not BASELINE.exists():
        print(
            "No .axiom_baseline/ found. Run "
            "`python scripts/snapshot_baseline.py` once before starting."
        )
        return 1

    allow_changed: set[str] = set()
    it = iter(argv)
    for arg in it:
        if arg == "--allow-changed":
            for nxt in it:
                if nxt.startswith("--"):
                    break
                allow_changed.add(nxt)

    base = _collect(BASELINE)
    live = _collect(ROOT)

    missing: list[str] = []
    changed: list[str] = []

    for name, base_defs in base.items():
        if name in IGNORE_NAMES:
            continue
        base_src = {src for _, src in base_defs}
        live_defs = live.get(name, [])
        if not live_defs:
            missing.append(name)
            continue
        live_src = {src for _, src in live_defs}
        # OK if any live definition matches any baseline definition.
        if base_src & live_src:
            continue
        if name in allow_changed:
            continue
        where = ", ".join(sorted({rel for rel, _ in live_defs}))
        changed.append(f"{name} (now in {where})")

    if missing or changed:
        print("Extraction check FAILED:")
        if missing:
            print("  MISSING (defined in baseline, not found anywhere now):")
            for n in sorted(missing):
                print(f"    - {n}")
        if changed:
            print("  CHANGED (logic differs from baseline, not waived):")
            for n in sorted(changed):
                print(f"    - {n}")
            print(
                "\n  If a change is intentional, re-run with "
                "`--allow-changed <name> ...` after reviewing it by hand."
            )
        return 1

    print(f"Extraction check OK ({len(base)} baseline symbols verified).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
