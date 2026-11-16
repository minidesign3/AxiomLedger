#!/usr/bin/env bash
set -euo pipefail

INPUT="$(cat)"

python3 - "$INPUT" <<'PY'
import json
import re
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    payload = json.loads(raw) if raw.strip() else {}
except json.JSONDecodeError:
    payload = {}

tool_input = payload.get("tool_input") or {}
cmd = tool_input.get("command") or ""

if not cmd:
    sys.exit(0)

# Allow safe references to sanitized fixtures and .env.example by stripping them
# before checking for sensitive path patterns.
scrubbed = re.sub(r'''(?:^|[\s'"=])(?:\./)?fixtures/sanitized_[^\s'";|&>]*''', " ", cmd)
scrubbed = re.sub(r'''(?:^|[\s'"=])(?:\./)?\.env\.example(?![A-Za-z0-9_.-])''', " ", scrubbed)

# Block sqlite3 or strings unless exclusively operating on fixtures/sanitized_*
if re.search(r"(?<![A-Za-z0-9_])(sqlite3|strings)(?![A-Za-z0-9_])", scrubbed):
    print(
        "BLOCKED: Running 'sqlite3' or 'strings' outside fixtures/sanitized_* is prohibited by data-privacy policy.",
        file=sys.stderr,
    )
    sys.exit(2)

# Restricted path and extension patterns
patterns = [
    (r'''(?:^|[\s/'"=])\.env(?:\.[A-Za-z0-9_.-]+)?(?=$|[\s'";|&>/])''', ".env*"),
    (r"\.(?:db|db-wal|db-shm|sqlite|sqlite3)(?![A-Za-z0-9_])", "SQLite/PocketBase database"),
    (r'''(?:^|[\s/'"=])(?:pb_data|data|exports|uploads|statements|logs)(?:/|$|[\s'";|&>])''', "sensitive data directory"),
    (r"\.(?:csv|tsv|pdf|xlsx|xls|parquet|pem|key)(?![A-Za-z0-9_])", "sensitive data/key file"),
    (r'''(?:^|[\s/'"=])secrets(?:/|$|[\s'";|&>])''', "secrets directory"),
]

for pattern, label in patterns:
    if re.search(pattern, scrubbed, re.IGNORECASE):
        print(
            f"BLOCKED: Bash command references restricted {label} outside fixtures/sanitized_*. "
            "Use schemas, code, or fixtures/sanitized_* only.",
            file=sys.stderr,
        )
        sys.exit(2)

sys.exit(0)
PY
