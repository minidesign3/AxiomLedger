#!/usr/bin/env python3
"""Freeze a one-time baseline of the source tree for extraction checks.

Run this ONCE, before starting the decomposition (phase D1). It copies every
tracked ``*.py`` file into ``.axiom_baseline/`` so that
``scripts/check_extraction.py`` can prove that functions moved out of
``utils.py`` were copied *byte-for-byte* (logic-identical), not paraphrased.

The baseline is the original definition of truth and must NOT be refreshed
between phases (that would defeat the check). Delete ``.axiom_baseline/`` only
when the whole decomposition is finished and verified.

Usage
-----
    python scripts/snapshot_baseline.py           # create (refuses to clobber)
    python scripts/snapshot_baseline.py --force    # recreate from scratch
"""

from __future__ import annotations

import shutil
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
}


def main(argv: list[str]) -> int:
    force = "--force" in argv
    if BASELINE.exists():
        if not force:
            print(
                f"{BASELINE.name}/ already exists. The baseline is frozen on "
                "purpose; pass --force only if you are (re)starting the whole "
                "decomposition from the original tree."
            )
            return 1
        shutil.rmtree(BASELINE)

    count = 0
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        dest = BASELINE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        count += 1

    (BASELINE / ".gitignore").write_text("*\n", encoding="utf-8")
    print(f"Baseline frozen: {count} files copied into {BASELINE.name}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
