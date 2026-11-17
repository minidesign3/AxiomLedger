"""
axiom_traffic
=============

Three-tier traffic-light certainty engine (green / yellow / red) for ledger
rows, plus the status-badge helpers used by the review table.

Split out of the D9 ledger slice so every module stays under the 500-line cap;
the bodies are byte-identical and ``utils.py`` re-exports them.
"""

from __future__ import annotations

import pandas as pd

from axiom_core import is_uncategorized
from axiom_ledger import is_property_unassigned
from axiom_rules import match_memorized_or_rule
from axiom_state import get_active_entity


def evaluate_row_traffic_light(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> tuple[str, str]:
    """
    Evaluate the 3-tier Traffic Light certainty level for a single transaction row:
    Returns `(tier, badge_text)` where `tier` is one of `'green'`, `'yellow'`, `'red'`:
      - `'green'`  (🟢 100% Sure):
          Both Item (Account Code) and Property are assigned, AND the row matches a Saved/Memorized Rule
          (or aligned specific vendor + house name on the CSV line, or was user-coded).
      - `'yellow'` (🟡 Needs Double-Check / Partially Sure):
          1) Both Item and Property are filled, but it came from a generic Keyword Guess / AI Guess
             rather than a confirmed Memorized Rule (`🟡 Check Guess (Not Memorized)`), OR
          2) One of Item or Property was found (e.g. House '9 Damian' found on the line, or Item 'Insurance'
             found on the line), but the other half still needs to be picked (`🟡 Pick Property` / `🟡 Pick Item`).
      - `'red'`    (🔴 Unsure / Missing Both):
          Neither the Item nor the Property could be determined (`🔴 Unsure — Pick Item & Property`).
    """
    resolved_entity = get_active_entity(entity)
    missing_code = is_uncategorized(account_code)
    missing_prop = is_property_unassigned(property_val, account_code)

    if missing_code and missing_prop:
        return "red", "🔴 Unsure — Needs Item & Property"

    if missing_code and not missing_prop:
        return "yellow", f"🟡 House Found ({property_val}) — Pick Item"

    if not missing_code and missing_prop:
        return "yellow", f"🟡 Item Found ({account_code}) — Pick Property"

    # Both Item and Property are present! Check whether this row is backed by a 100% certain Memorized Rule
    desc_str = "" if pd.isna(description) else str(description).strip()
    if desc_str:
        _, _, match_src = match_memorized_or_rule(desc_str, entity=resolved_entity)
        if match_src in ("Memorized", "Loan Match", "Aligned Vendor+House"):
            return "green", "🟢 100% Sure (Matched Rule)"
        if match_src == "Keyword Rule":
            return "yellow", "🟡 Check Guess (Keyword Match)"
        if match_src == "Memorized Partial":
            # Rule matched the item, and property is now filled
            return "green", "🟢 100% Sure (Rule + House)"
        # If match_src == "Unknown" yet both code & property are populated, it was either pre-coded in the file or AI-guessed
        return "green", "🟢 100% Coded"

    return "green", "🟢 100% Coded"

def compute_traffic_light_columns(
    df: pd.DataFrame,
    entity: str | None = None,
) -> tuple[list[str], list[str]]:
    """Compute `(tiers, badges)` for a whole ledger in one pass.

    Behaviour-identical to calling :func:`evaluate_row_traffic_light` row-by-row,
    but resolves the active entity once (instead of per row) and memoizes the
    expensive memorized/rule lookup per *unique* description — so a ledger with
    many repeated payees no longer re-runs the matcher for every row. Cheap
    red/yellow rows skip the matcher entirely.
    """
    tiers: list[str] = []
    badges: list[str] = []
    if not isinstance(df, pd.DataFrame) or df.empty:
        return tiers, badges

    resolved_entity = get_active_entity(entity)
    props = df["Property"] if "Property" in df.columns else None
    codes = df["Account Code"] if "Account Code" in df.columns else None
    descs = df["Description"] if "Description" in df.columns else None
    match_cache: dict[str, str] = {}

    for i in range(len(df)):
        property_val = props.iloc[i] if props is not None else ""
        account_code = codes.iloc[i] if codes is not None else ""
        description = descs.iloc[i] if descs is not None else ""

        missing_code = is_uncategorized(account_code)
        missing_prop = is_property_unassigned(property_val, account_code)

        if missing_code and missing_prop:
            tiers.append("red")
            badges.append("🔴 Unsure — Needs Item & Property")
            continue
        if missing_code and not missing_prop:
            tiers.append("yellow")
            badges.append(f"🟡 House Found ({property_val}) — Pick Item")
            continue
        if not missing_code and missing_prop:
            tiers.append("yellow")
            badges.append(f"🟡 Item Found ({account_code}) — Pick Property")
            continue

        desc_str = "" if pd.isna(description) else str(description).strip()
        if not desc_str:
            tiers.append("green")
            badges.append("🟢 100% Coded")
            continue

        if desc_str not in match_cache:
            _, _, match_cache[desc_str] = match_memorized_or_rule(desc_str, entity=resolved_entity)
        match_src = match_cache[desc_str]

        if match_src in ("Memorized", "Loan Match", "Aligned Vendor+House"):
            tiers.append("green")
            badges.append("🟢 100% Sure (Matched Rule)")
        elif match_src == "Keyword Rule":
            tiers.append("yellow")
            badges.append("🟡 Check Guess (Keyword Match)")
        elif match_src == "Memorized Partial":
            tiers.append("green")
            badges.append("🟢 100% Sure (Rule + House)")
        else:
            tiers.append("green")
            badges.append("🟢 100% Coded")

    return tiers, badges

def get_row_status_badge(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> str:
    """
    Grade a single transaction row into the 3-tier Traffic Light system:
    - '🟢 100% Sure ...' : High-confidence matched rule or confirmed Item + Property
    - '🟡 ...'           : Partially matched (Item found OR House found, or unconfirmed keyword guess)
    - '🔴 Unsure ...'    : Neither Item nor Property is known
    """
    _, badge = evaluate_row_traffic_light(
        property_val=property_val,
        account_code=account_code,
        description=description,
        entity=entity,
    )
    return badge

def is_row_red_or_unsure(
    property_val: object,
    account_code: object,
    description: object = "",
    entity: str | None = None,
) -> bool:
    """Return True if a row is 🔴 Red or 🟡 Yellow (i.e. not 🟢 100% Green)."""
    if is_uncategorized(account_code) or is_property_unassigned(property_val, account_code):
        return True
    if description:
        tier, _ = evaluate_row_traffic_light(property_val, account_code, description=description, entity=entity)
        return tier != "green"
    return False
