"""
axiom_rule_patterns
===================

Pattern hygiene for memorized coding rules.

Bank lines often carry a per-transaction reference that changes every month
(e.g. ``BCA Ltd Westp | A/P Quest Hotel Apt 1604 Body Corp | A/P | 2025041101``).
Memorizing the raw line verbatim created one near-identical rule per statement
month. ``normalize_rule_pattern`` strips those volatile date/reference tokens so
the stable payee text becomes the rule, and ``collapse_duplicate_rules`` merges
rules that only differed by such tokens.

Pure functions only -- no store / Streamlit access.
"""

from __future__ import annotations

import re

# Date-like / per-transaction reference tokens:
#  - 8+ digit runs starting with 19/20 (YYYYMMDD... bank refs, e.g. 2025041101)
#  - ISO dates (2025-04-11) and d/m/y dates (11/04/2025, 11-04-25)
_VOLATILE_TOKEN_RE = re.compile(
    r"(?<![\w/-])(?:"
    r"(?:19|20)\d{6,}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r")(?![\w/-])"
)
_SEGMENT_SPLIT_RE = re.compile(r"\s*\|\s*")


def normalize_rule_pattern(pattern: object) -> str:
    """
    Remove volatile date / reference tokens from a rule pattern and drop any
    ``|`` segments left empty. Returns the stripped original if normalization
    would leave nothing (so a pattern is never emptied).
    """
    raw = str(pattern or "").strip()
    if not raw:
        return ""
    segments = []
    for seg in _SEGMENT_SPLIT_RE.split(raw):
        cleaned = re.sub(r"\s{2,}", " ", _VOLATILE_TOKEN_RE.sub("", seg)).strip()
        if cleaned:
            segments.append(cleaned)
    result = " | ".join(segments)
    return result or raw


def collapse_duplicate_rules(rules: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """
    Normalize every rule's pattern and keep only the first rule per
    case-insensitive ``(pattern, property, account_code)``. Order of the first
    occurrence is preserved. Returns ``(collapsed_rules, removed_count)``.
    """
    seen: set[tuple[str, str, str]] = set()
    collapsed: list[dict[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        new_rule = dict(rule)
        new_rule["pattern"] = normalize_rule_pattern(rule.get("pattern", ""))
        key = (
            new_rule["pattern"].lower(),
            str(new_rule.get("property", "")).strip().lower(),
            str(new_rule.get("account_code", "")).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        collapsed.append(new_rule)
    return collapsed, len(rules) - len(collapsed)
