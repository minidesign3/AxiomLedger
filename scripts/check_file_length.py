#!/usr/bin/env python3
"""Fail if any tracked Python file exceeds the line-length cap.

Guardrail against the ``utils.py`` God-module ever happening again.

Rules
-----
* Soft alert: ``WARN_LINES`` (400) physical lines per ``*.py`` file. Files
  over this print a non-fatal warning so growth gets noticed early, before a
  file reaches the hard cap.
* Default hard cap: ``MAX_LINES`` (500) physical lines per ``*.py`` file.
* A grandfather allow-list (``.axiom_file_length_allow.txt``) lets specific
  files temporarily exceed the cap with an explicit, **shrink-only** ceiling:
  each line is ``<relpath> <max_lines>``. A file listed there must be
  ``<= min(its_ceiling)`` and the ceiling may only ever be *lowered*. Run
  ``--tighten`` to rewrite every ceiling down to the file's current size (it
  refuses to raise any ceiling), so the allowance ratchets toward the cap and
  can never silently grow back.

Usage
-----
    python scripts/check_file_length.py          # check (CI / pre-commit)
    python scripts/check_file_length.py --tighten # ratchet ceilings down
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / ".axiom_file_length_allow.txt"
WARN_LINES = 400
MAX_LINES = 500

# Directories never scanned (vendored / data / caches / protected per AGENTS.md).
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
}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def _load_allow() -> dict[str, int]:
    allow: dict[str, int] = {}
    if not ALLOW_FILE.exists():
        return allow
    for raw in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        allow[parts[0]] = int(parts[1])
    return allow


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _write_allow(allow: dict[str, int]) -> None:
    header = (
        "# Grandfather allow-list for scripts/check_file_length.py\n"
        "# Format: <relpath> <max_lines>.  Ceilings are SHRINK-ONLY.\n"
        "# Goal: every entry reaches <= 500 lines, then is deleted.\n"
    )
    body = "".join(f"{rel} {cap}\n" for rel, cap in sorted(allow.items()))
    ALLOW_FILE.write_text(header + body, encoding="utf-8")


def main(argv: list[str]) -> int:
    tighten = "--tighten" in argv
    allow = _load_allow()
    failures: list[str] = []
    warnings: list[str] = []
    new_allow = dict(allow)

    for path in _iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        n = _count_lines(path)
        ceiling = allow.get(rel)

        if ceiling is not None:
            if n > ceiling:
                failures.append(
                    f"{rel}: {n} lines > allow-list ceiling {ceiling} "
                    f"(may only shrink toward {MAX_LINES})"
                )
            # Ratchet: never let the recorded ceiling exceed current size.
            new_allow[rel] = min(ceiling, n)
            if n <= MAX_LINES:
                # Earned its way under the cap: drop the exemption.
                new_allow.pop(rel, None)
        elif n > MAX_LINES:
            failures.append(
                f"{rel}: {n} lines > hard cap {MAX_LINES} "
                f"(split it, or add a shrink-only allow-list entry)"
            )
        elif n > WARN_LINES:
            warnings.append(f"{rel}: {n} lines (> {WARN_LINES}, approaching the {MAX_LINES} cap)")

    if tighten:
        _write_allow(new_allow)
        print(f"Allow-list ratcheted ({len(new_allow)} entries remain).")
        return 0

    if warnings:
        print(f"File-length ALERT ({WARN_LINES}-{MAX_LINES} lines — plan to split soon):")
        for w in sorted(warnings, reverse=True):
            print(f"  ::warning:: {w}")

    if failures:
        print("File-length guard FAILED:")
        for f in failures:
            print(f"  - {f}")
        print(f"\nCap is {MAX_LINES} lines/file. See CONTRIBUTING.md.")
        return 1

    print(
        f"File-length guard OK (warn {WARN_LINES}, cap {MAX_LINES}, "
        f"{len(warnings)} in alert zone, {len(allow)} grandfathered)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
