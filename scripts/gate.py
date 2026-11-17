#!/usr/bin/env python3
"""One-command acceptance gate for every decomposition phase.

Run this after each extraction. It is the single source of "is this phase
done?" — if it prints ``GATE PASSED`` the slice is safe to ship; otherwise it
tells you exactly what broke. Designed so a cheap model can iterate against it
without human help until it is green.

Checks, in order:
  1. py_compile every source file.
  2. ruff on the whole repo (config in pyproject.toml).
  3. File-length guard (scripts/check_file_length.py).
  4. Extraction byte-identity vs baseline (scripts/check_extraction.py),
     forwarding any --allow-changed names.
  5. Import-order identity: for each --module, every public symbol it defines
     must be the SAME object as utils.<name>, whether utils OR the module is
     imported first (catches circular-import / shim mistakes).
  6. pytest -q.

Usage
-----
    python scripts/gate.py --module axiom_state
    python scripts/gate.py --module axiom_state --allow-changed get_entity_jurisdiction
    python scripts/gate.py            # no module: runs 1-4 and 6
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(label: str, cmd: list[str]) -> bool:
    print(f"\n=== {label} ===")
    proc = subprocess.run(cmd, cwd=ROOT)
    ok = proc.returncode == 0
    print(f"--- {label}: {'OK' if ok else 'FAIL'} ---")
    return ok


def _parse(argv: list[str]) -> tuple[list[str], list[str]]:
    modules: list[str] = []
    allow: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--module":
            modules.append(next(it))
        elif arg == "--allow-changed":
            for nxt in it:
                if nxt.startswith("--"):
                    # push-back not supported; require --allow-changed last
                    break
                allow.append(nxt)
    return modules, allow


def _identity_snippet(module: str) -> str:
    return (
        "import importlib, sys\n"
        f"mod = importlib.import_module({module!r})\n"
        "import utils\n"
        "bad = []\n"
        "for name in dir(mod):\n"
        "    if name.startswith('__'):\n"
        "        continue\n"
        "    if not hasattr(utils, name):\n"
        "        continue\n"
        "    a = getattr(mod, name)\n"
        "    b = getattr(utils, name)\n"
        "    if callable(a) and a is not b:\n"
        "        bad.append(name)\n"
        "if bad:\n"
        "    print('NOT identical via utils re-export:', bad); sys.exit(1)\n"
        "print('identity OK')\n"
    )


def main(argv: list[str]) -> int:
    modules, allow = _parse(argv)
    results: list[tuple[str, bool]] = []

    py_files = [
        p.as_posix()
        for p in sorted(ROOT.rglob("*.py"))
        if not any(
            part in {".venv", ".git", "__pycache__", ".axiom_baseline"}
            for part in p.relative_to(ROOT).parts
        )
    ]
    results.append(("py_compile", _run("py_compile", [PY, "-m", "py_compile", *py_files])))
    results.append(("ruff", _run("ruff", [PY, "-m", "ruff", "check", "."])))
    results.append(
        ("file-length", _run("file-length", [PY, "scripts/check_file_length.py"]))
    )

    extract_cmd = [PY, "scripts/check_extraction.py"]
    if allow:
        extract_cmd += ["--allow-changed", *allow]
    if (ROOT / ".axiom_baseline").exists():
        results.append(("extraction", _run("extraction", extract_cmd)))
    else:
        print("\n=== extraction === (skipped: no .axiom_baseline/) ===")

    for module in modules:
        # Order A: module first, then utils.
        ok_a = _run(
            f"identity[{module}] module-first",
            [PY, "-c", _identity_snippet(module)],
        )
        # Order B: utils first, then module (import utils up front).
        snippet_b = "import utils\n" + _identity_snippet(module)
        ok_b = _run(
            f"identity[{module}] utils-first",
            [PY, "-c", snippet_b],
        )
        results.append((f"identity[{module}]", ok_a and ok_b))

    results.append(("pytest", _run("pytest", [PY, "-m", "pytest", "-q"])))

    print("\n================ GATE SUMMARY ================")
    all_ok = True
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        all_ok = all_ok and ok
    print("=============================================")
    print("GATE PASSED" if all_ok else "GATE FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
