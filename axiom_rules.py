"""
axiom_rules
===========

Memorized coding rules extracted from the monolithic ``utils.py`` god-module:
the per-entity rule-table CRUD (get / save / delete / replace) and the
deterministic matching engine (property detection -> memorized rules ->
jurisdiction keyword dictionary) used by ingestion and the traffic lights.

Dependency handling follows the proven ``axiom_categorization`` /
``axiom_loan_crud`` pattern. Pure helpers and already-extracted modules are
imported at module level (``axiom_core``, ``axiom_memory``, ``axiom_state``,
``axiom_loan_crud``). Since phase D6 the rule engine imports the account-code /
property-detection helpers directly from ``axiom_account_codes`` and
``axiom_properties`` instead of reaching back into ``utils``. The one helper
still owned by ``utils.py`` -- ``is_property_unassigned`` (D9) -- is reached
through a *function-body scoped* ``import utils``, never a module-level one.
That is order-independent: by the time it is called, both modules have
finished importing.

Everything else in this module was moved byte-for-byte. ``utils.py`` imports
and re-exports these symbols, so existing ``from utils import ...`` call sites
keep working unchanged.
"""

from __future__ import annotations

import re

import pandas as pd

from axiom_account_codes import (
    detect_property_from_text,
    get_entity_account_codes,
    get_entity_rules,
)
from axiom_core import (
    DEFAULT_PROPERTIES,
    UNASSIGNED_PROPERTY,
    UNCATEGORIZED_MARKERS,
    is_uncategorized,
)
from axiom_loan_crud import match_loan_from_text
from axiom_memory import _load_memory_store, _save_memory_store
from axiom_properties import is_portfolio_wide_code
from axiom_state import get_active_entity


def get_memorized_rules(entity: str | None = None) -> list[dict[str, str]]:
    """
    Return the list of memorized rules for the entity.
    Each item is: {'pattern': str, 'property': str, 'account_code': str, 'source': str}
    """
    resolved = get_active_entity(entity)
    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    if not isinstance(rules_map, dict):
        rules_map = {}
        store["memorized_rules"] = rules_map
    if resolved not in rules_map or not isinstance(rules_map[resolved], list):
        rules_map[resolved] = []
    return list(rules_map[resolved])


def save_memorized_rule(
    pattern: str,
    property_name: str,
    account_code: str,
    entity: str | None = None,
    source: str = "User Coded",
) -> tuple[bool, str]:
    """
    Upsert a memorized rule mapping `pattern` -> (`property_name`, `account_code`)
    for the specified entity and persist to disk.
    """
    resolved = get_active_entity(entity)
    clean_pat = (pattern or "").strip()
    clean_prop = (property_name or "").strip() or UNASSIGNED_PROPERTY
    clean_code = (account_code or "").strip() or "Uncategorized"
    if not clean_pat:
        return False, "Description pattern cannot be empty."

    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    current_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))

    pat_lower = clean_pat.lower()
    prop_lower = clean_prop.lower()
    updated = False
    for item in current_rules:
        item_pat_l = str(item.get("pattern", "")).strip().lower()
        item_prop_l = str(item.get("property", "") or UNASSIGNED_PROPERTY).strip().lower()
        if item_pat_l == pat_lower and item_prop_l == prop_lower:
            item["pattern"] = clean_pat
            item["property"] = clean_prop
            item["account_code"] = clean_code
            item["source"] = source
            updated = True
            break

    if not updated:
        current_rules.insert(
            0,
            {
                "pattern": clean_pat,
                "property": clean_prop,
                "account_code": clean_code,
                "source": source,
            },
        )

        # Also auto-register the account_code in entity_account_codes if it was newly typed/edited
    if clean_code and clean_code.lower() not in UNCATEGORIZED_MARKERS:
        codes_map = store.setdefault("entity_account_codes", {})
        ent_codes = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
        if not any(c.lower() == clean_code.lower() for c in ent_codes):
            ent_codes.append(clean_code)
            ent_codes.sort(key=lambda s: s.lower())
            codes_map[resolved] = ent_codes

    # Also auto-register the property if it was newly typed/imported and isn't Unassigned
    if clean_prop and clean_prop.lower() not in UNCATEGORIZED_MARKERS:
        props_map = store.setdefault("entity_properties", {})
        ent_props = list(props_map.get(resolved, DEFAULT_PROPERTIES))
        if not any(p.lower() == clean_prop.lower() for p in ent_props):
            ent_props.append(clean_prop)
            props_map[resolved] = ent_props

    # Self-Learning Vendor Token Extraction:
    # If `clean_pat` is a compound CSV line (e.g. "6851Tower Insurance | Damian | Pol 8821" or "Tower + Damian")
    # and has a valid Account Code, also ensure the primary vendor stem (segment 0) is remembered
    # so future CSV lines with the same vendor + a DIFFERENT house (e.g. "6851Tower Insurance | 33 Blampied")
    # automatically line up the vendor's Account Code ("Insurance") with the new house ("33 Blampied")!
    if "|" in clean_pat and not is_uncategorized(clean_code):
        primary_seg = clean_pat.split("|")[0].strip()
        if len(primary_seg) >= 3:
            seg_has_prop = detect_property_from_text(primary_seg, entity=resolved) != UNASSIGNED_PROPERTY
            seg_lower = primary_seg.lower()
            already_has_seg = any(
                str(r.get("pattern", "")).strip().lower() == seg_lower for r in current_rules
            )
            if not already_has_seg:
                vendor_default_prop = (
                    "General / Portfolio"
                    if is_portfolio_wide_code(clean_code, resolved)
                    else (clean_prop if seg_has_prop else UNASSIGNED_PROPERTY)
                )
                current_rules.append(
                    {
                        "pattern": primary_seg,
                        "property": vendor_default_prop,
                        "account_code": clean_code,
                        "source": f"{source} (Vendor Auto-Rule)",
                    }
                )

    rules_map[resolved] = current_rules
    _save_memory_store(store)
    return True, f"Memorized: '{clean_pat}' → {clean_prop} | {clean_code}"


def delete_memorized_rule(pattern: str, entity: str | None = None) -> tuple[bool, str]:
    """Delete a memorized rule by pattern for the specified entity."""
    resolved = get_active_entity(entity)
    pat_lower = (pattern or "").strip().lower()
    store = _load_memory_store()
    rules_map = store.setdefault("memorized_rules", {})
    current_rules: list[dict[str, str]] = list(rules_map.get(resolved, []))
    filtered = [r for r in current_rules if str(r.get("pattern", "")).strip().lower() != pat_lower]
    rules_map[resolved] = filtered
    _save_memory_store(store)
    return True, f"Removed memorized rule '{pattern}'."


def replace_memorized_rules(
    new_rules_rows: list[dict[str, object]],
    entity: str | None = None,
) -> tuple[bool, str]:
    """
    Save the full 3-column Item + Property mapping table for an entity:
    Each row contains:
    - `pattern`: What appears on the bank statement (e.g. Payee / Loan / Policy / Tenant text)
    - `account_code`: What is the Item? (e.g. Insurance, Loan Interest, Rent Income, Rates, Accountant)
    - `property`: Which Property? (e.g. 33 Blampied, 303 Weymouth, or General / Portfolio)
    Allows the same `pattern` to be saved across multiple properties (deduplicates by pattern + property + account_code).
    Automatically sets `General / Portfolio` for portfolio-wide items like Accountant, Bank Fee, etc.
    """
    resolved = get_active_entity(entity)
    cleaned_rules: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for r in new_rules_rows:
        pat = str(r.get("pattern", "") or "").strip()
        if not pat or pat.lower() in ("nan", "none"):
            continue

        code = str(r.get("account_code", "") or "").strip() or "Uncategorized"
        raw_prop = str(r.get("property", "") or "").strip()
        if raw_prop.startswith("Auto-detect") or raw_prop in ("", "nan", "None"):
            prop = UNASSIGNED_PROPERTY
        else:
            prop = raw_prop

        if is_portfolio_wide_code(code, resolved):
            prop = "General / Portfolio"

        dedup_key = (pat.lower(), prop.lower(), code.lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        src = str(r.get("source", "") or "Setup Table").strip() or "Setup Table"
        cleaned_rules.append(
            {
                "pattern": pat,
                "account_code": code,
                "property": prop,
                "source": src,
            }
        )

    store = _load_memory_store()
    codes_map = store.setdefault("entity_account_codes", {})
    ent_codes = list(codes_map.get(resolved, get_entity_account_codes(resolved)))
    codes_added = False
    for r_saved in cleaned_rules:
        c_val = str(r_saved.get("account_code", "")).strip()
        if c_val and c_val.lower() not in UNCATEGORIZED_MARKERS:
            if not any(existing_c.lower() == c_val.lower() for existing_c in ent_codes):
                ent_codes.append(c_val)
                codes_added = True
    if codes_added:
        ent_codes.sort(key=lambda s: s.lower())
        codes_map[resolved] = ent_codes

    rules_map = store.setdefault("memorized_rules", {})
    rules_map[resolved] = cleaned_rules
    _save_memory_store(store)
    return True, f"Saved {len(cleaned_rules)} Item + Property rule(s) for {resolved}."


def _pattern_matches_row_text(pattern_str: str, row_text_lower: str) -> tuple[bool, int]:
    """
    Evaluate whether a saved rule `pattern_str` matches `row_text_lower` (the full CSV row text).
    Supports:
    - Exact match (score 1000 + len)
    - Contiguous substring match (score 500 + len)
    - Compound multi-part rules separated by '|', '+', or '&' (e.g. 'Tower | Damian' or 'Tower + 9 Damian')
      where every part appears anywhere across the CSV row text (score 400 + sum of part lengths)
    - Leading payee segment match when rule pattern has bank suffix/prefix variations (score 300 + len)
    """
    pat_clean = (pattern_str or "").strip().lower()
    if not pat_clean or not row_text_lower:
        return False, 0

    if pat_clean == row_text_lower:
        return True, 1000 + len(pat_clean)

    if pat_clean in row_text_lower:
        return True, 500 + len(pat_clean)

    # Check compound pattern separated by '|', '+', or '&' (e.g. "Tower + Damian" or "6851Tower Insurance | 9 Damian")
    if any(sep in pat_clean for sep in ("|", "+", "&")):
        sub_parts = [p.strip() for p in re.split(r"[|+&]+", pat_clean) if p.strip()]
        if len(sub_parts) >= 2 and all(sp in row_text_lower for sp in sub_parts):
            return True, 400 + sum(len(sp) for sp in sub_parts)

    # Check if stripping leading 4-digit bank code from pattern (e.g. '6851Tower Insurance' -> 'tower insurance')
    # matches the CSV row text
    stripped_digits = re.sub(r"^\d{3,6}\s*", "", pat_clean).strip()
    if len(stripped_digits) >= 5 and stripped_digits != pat_clean and stripped_digits in row_text_lower:
        return True, 320 + len(stripped_digits)

    return False, 0


def match_memorized_or_rule(
    description: object,
    entity: str | None = None,
) -> tuple[str, str, str]:
    """
    Full-CSV-Line Decoupled Account Code & Property Matching Engine:
    1. Property Detection Across Full Line:
       Scans `description` (which includes Payee, Analysis Code, Reference, Particulars, Memo, and all
       other text columns from the CSV line) using `detect_property_from_text()`.
       If a house name (e.g. 'Damian', '26 Frances', '33 Blampied', '14 Raleigh', '303 Weymouth', '363 Queen')
       appears anywhere on the line, `explicit_prop` captures it immediately.
    2. Memorized Rules Matching:
       Checks User's Memorized Rules (exact match, substring match, compound 'Vendor + House' match, or
       vendor-without-bank-prefix match).
       - If both a Memorized Rule AND an explicit house in the CSV line or rule property resolve -> 'Memorized (100%)'
       - If a Memorized Rule matches the Item, and the rule is Portfolio-Wide -> 'Memorized (100%)'
       - If a Memorized Rule matches the Item, but Property is still unknown -> 'Memorized (Missing Property)'
    3. Built-in Jurisdiction & Vendor Keyword Dictionary:
       Scans the full CSV line against known vendor/item keywords (e.g. 'tower', 'insurance', 'auckland council',
       'watercare', 'rates', 'body corp', 'rent', 'interest', 'bunnings', 'mitre 10').
       - If a strong vendor keyword (like 'tower insurance', 'auckland council', 'watercare') lines up with an
         explicit house name on the same CSV line (like 'Damian') -> 'Aligned Vendor + House (100%)'
       - If only a generic keyword matched (or property wasn't on the line) -> 'Keyword Guess (Needs Check)'
    """
    import utils

    if pd.isna(description):
        return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"
    desc_text = str(description).strip()
    desc_lower = desc_text.lower()
    if not desc_lower:
        return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"

    resolved_entity = get_active_entity(entity)

    # Step 1: Detect explicit property / house name anywhere in the CSV line text
    explicit_prop = detect_property_from_text(desc_text, entity=resolved_entity)

    # Step 1b: Loan account/trigger match (highest priority — explicit account numbers)
    try:
        _loan = match_loan_from_text(desc_text, entity=resolved_entity)
    except Exception:
        _loan = None
    if _loan:
        _prop = str(_loan.get("property_name") or UNASSIGNED_PROPERTY)
        _int_code = str(_loan.get("interest_account_code") or "Loan Interest")
        _prin_code = str(_loan.get("principal_account_code") or "Principal Loan")
        try:
            _codes = get_entity_account_codes(resolved_entity)
            if "Loan Interest" not in _codes and "Interest" in _codes and _int_code == "Loan Interest":
                _int_code = "Interest"
        except Exception:
            pass
        if re.search(r"\b(principal|loan repayment|mortgage repayment|repayment|loan payment)\b", desc_lower):
            return _prop, _prin_code, "Memorized"
        if re.search(r"\b(interest|int charged|debit interest|loan int)\b", desc_lower):
            return _prop, _int_code, "Memorized"
        # Bare account-number transfer with no interest/principal wording
        return _prop, _int_code, "Memorized"

    # Step 2: Check Memorized Rules (scored by match specificity)
    mem_rules = get_memorized_rules(resolved_entity)
    best_rule: dict[str, str] | None = None
    best_score = 0
    matched_props_for_best_pat: set[str] = set()
    best_pat_lower = ""

    for rule in mem_rules:
        pat = str(rule.get("pattern", "")).strip()
        if not pat:
            continue
        matched, score = _pattern_matches_row_text(pat, desc_lower)
        if matched:
            # Bonus score if the rule's property also matches the explicit property detected on the CSV line
            r_prop = str(rule.get("property", "")).strip()
            if explicit_prop != UNASSIGNED_PROPERTY and r_prop.lower() == explicit_prop.lower():
                score += 150
            if score > best_score:
                best_score = score
                best_rule = rule
                best_pat_lower = pat.lower()

    if best_rule is not None:
        if best_pat_lower:
            for r_chk in mem_rules:
                if str(r_chk.get("pattern", "")).strip().lower() == best_pat_lower:
                    p_chk = str(r_chk.get("property", "")).strip()
                    if p_chk and p_chk != UNASSIGNED_PROPERTY and p_chk.lower() != "general / portfolio":
                        matched_props_for_best_pat.add(p_chk)

        rule_prop = str(best_rule.get("property", "")).strip() or UNASSIGNED_PROPERTY
        code = str(best_rule.get("account_code", "")).strip() or "Uncategorized"
        # Explicit house on the CSV line always takes precedence over a generic default property.
        # If the same vendor pattern is saved for multiple different properties (e.g. TOWER Insurance saved for
        # 9 Damian AND 303 Weymouth), require the house name on the CSV line to pick the right property
        # rather than blindly assigning every row to whichever property rule was saved last.
        if explicit_prop != UNASSIGNED_PROPERTY:
            final_prop = explicit_prop
        elif len(matched_props_for_best_pat) > 1:
            final_prop = UNASSIGNED_PROPERTY
        else:
            final_prop = rule_prop

        if final_prop == UNASSIGNED_PROPERTY and is_portfolio_wide_code(code, resolved_entity):
            final_prop = "General / Portfolio"

        if not is_uncategorized(code) and not utils.is_property_unassigned(final_prop, code):
            return final_prop, code, "Memorized"
        return final_prop, code, "Memorized Partial"

    # Step 3: Check entity's custom Account Codes appearing directly as words in the CSV line
    # AND check Jurisdiction Keyword Dictionary (longest/most specific keyword first)
    active_rules = dict(get_entity_rules(resolved_entity))
    # Ensure common NZ/US vendor shorthand keywords like 'tower' -> 'Insurance' are included
    extra_smart_keywords = {
        "tower": "Insurance",
        "aon": "Insurance",
        "stylecover": "Insurance",
        "lumley": "Insurance",
        "crombie lockwood": "Insurance",
        "bodycorp": "Bodycorp",
        "body corporate": "Bodycorp",
        "bca ltd": "Bodycorp",
        "watercare": "Watercare" if "Watercare" in get_entity_account_codes(resolved_entity) else "Water Rates",
        "auckland council": "Rates",
    }
    for ek, ev in extra_smart_keywords.items():
        if ek not in active_rules:
            active_rules[ek] = ev

    matched_code = "Uncategorized"
    matched_kw = ""
    # Sort keywords longest first so specific vendor names ('tower insurance', 'auckland council') match before generic words
    for keyword in sorted(active_rules.keys(), key=len, reverse=True):
        kw_low = keyword.lower().strip()
        if not kw_low:
            continue
        if len(kw_low) <= 4:
            if re.search(rf"(?<![a-z0-9]){re.escape(kw_low)}(?![a-z0-9])", desc_lower):
                matched_code = active_rules[keyword]
                matched_kw = kw_low
                break
        elif kw_low in desc_lower:
            matched_code = active_rules[keyword]
            matched_kw = kw_low
            break

    # Map jurisdiction generic category name to entity's exact Account Code if equivalent exists
    ent_codes = get_entity_account_codes(resolved_entity)
    if matched_code == "Rental Income" and "Rent Income" in ent_codes:
        matched_code = "Rent Income"
    elif matched_code == "Body Corporate Levies" and "Bodycorp" in ent_codes:
        matched_code = "Bodycorp"
    elif matched_code == "Water Rates" and "Watercare" in ent_codes:
        matched_code = "Watercare"

    detected_prop = explicit_prop
    if is_portfolio_wide_code(matched_code, resolved_entity) and detected_prop == UNASSIGNED_PROPERTY:
        detected_prop = "General / Portfolio"

    if matched_code != "Uncategorized" or detected_prop != UNASSIGNED_PROPERTY:
        # If both a house name AND an item keyword were found on the line, note whether it was a specific vendor or generic keyword
        high_conf_vendors = {
            "tower",
            "tower insurance",
            "ami insurance",
            "state insurance",
            "aa insurance",
            "vero",
            "initio",
            "aon",
            "stylecover",
            "auckland council",
            "wellington city council",
            "christchurch city council",
            "hamilton city council",
            "tauranga city council",
            "dunedin city council",
            "watercare",
            "bca ltd",
            "inland revenue",
            "xero",
        }
        if matched_code != "Uncategorized" and detected_prop != UNASSIGNED_PROPERTY and matched_kw in high_conf_vendors:
            return detected_prop, matched_code, "Aligned Vendor+House"
        return detected_prop, matched_code, "Keyword Rule"

    return UNASSIGNED_PROPERTY, "Uncategorized", "Unknown"
