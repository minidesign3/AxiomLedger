"""
axiom_ai_tools
==============

Read-only tools Ask Axiom's Gemini model can call (function calling). Each
tool runs locally on the app's own data — the same clean rows, checks and rent
reconciliation the Accountant Pack uses (``axiom_export_checks``) — and only
its (masked, size-capped) JSON result is sent to Gemini. No tool can change
data.

:class:`AIContext` is built once per question; :func:`run_tool` dispatches,
applies masking (``axiom_ai_redact``) and caps result size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from axiom_ai_redact import hide_incoming_description, mask_value
from axiom_dates import parse_any_date
from axiom_export_checks import ExportResult

MAX_ROWS = 200
MAX_RESULT_CHARS = 60_000
_GROUPS = {"account_code": "Account Code", "property": "Property", "month": "_month",
           "source_file": "Source File", "source_type": "Source Type", "type": "Type"}


@dataclass
class AIContext:
    entity: str
    tax_year: str
    period: str
    jurisdiction: str
    result: ExportResult
    records: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)
    hide_incoming: bool = True


def _money(v: float) -> float:
    return round(float(v), 2)


def _all_rows(ctx: AIContext, include_excluded: bool = False) -> pd.DataFrame:
    rows = ctx.result.rows.assign(**{"Excluded Because": ""})
    if include_excluded and not ctx.result.excluded.empty:
        rows = pd.concat([rows, ctx.result.excluded], ignore_index=True)
    dates = [parse_any_date(d, ctx.jurisdiction)[0] for d in rows["Date"]]
    rows = rows.assign(_date=dates, _month=[d.strftime("%Y-%m") if d else "" for d in dates])
    if ctx.hide_incoming:
        rows["Description"] = [hide_incoming_description(d, a, s, ctx.entity)
                               for d, a, s in zip(rows["Description"], rows["Amount"], rows["Source Type"], strict=True)]
    return rows


def _filter(rows: pd.DataFrame, args: dict, jurisdiction: str) -> pd.DataFrame:
    out = rows
    text = str(args.get("text") or "").strip().lower()
    if text:
        out = out[out["Description"].str.lower().str.contains(text, regex=False)]
    for key, col in (("property", "Property"), ("account_code", "Account Code"), ("source_file", "Source File"),
                     ("type", "Type")):
        val = str(args.get(key) or "").strip().lower()
        if val:
            out = out[out[col].astype(str).str.lower().str.contains(val, regex=False)]
    if args.get("uncoded_only"):
        out = out[out["Account Code"] == "Uncategorized"]
    for key, op in (("date_from", "ge"), ("date_to", "le")):
        bound, _ = parse_any_date(args.get(key), jurisdiction) if args.get(key) else (None, "")
        if bound:
            out = out[[d is not None and (d >= bound if op == "ge" else d <= bound) for d in out["_date"]]]
    if args.get("min_amount") is not None:
        out = out[out["Amount"] >= float(args["min_amount"])]
    if args.get("max_amount") is not None:
        out = out[out["Amount"] <= float(args["max_amount"])]
    return out


def tool_get_overview(ctx: AIContext, _args: dict) -> dict:
    res = ctx.result
    by_bucket: dict[str, int] = {}
    for rec in ctx.records:
        by_bucket[str(rec.get("bucket"))] = by_bucket.get(str(rec.get("bucket")), 0) + 1
    return {
        "entity": ctx.entity, "tax_year": ctx.tax_year, "period": ctx.period, "jurisdiction": ctx.jurisdiction,
        "files_by_type": by_bucket, "rows_for_accountant": len(res.rows), "rows_excluded": len(res.excluded),
        "ready_to_send": res.ready, "blockers": len(res.blockers),
        "warnings": sum(c.level == "warning" for c in res.checks),
        "net_total_of_exported_rows": _money(res.rows["Amount"].sum()) if not res.rows.empty else 0.0,
        "memorized_rules": len(ctx.rules), "account_codes": len(ctx.codes), "properties": ctx.properties,
    }


def tool_get_readiness_checks(ctx: AIContext, _args: dict) -> dict:
    return {"ready_to_send": ctx.result.ready,
            "checks": [{"level": c.level, "name": c.name, "rows": c.count, "detail": c.detail} for c in ctx.result.checks]}


def tool_get_rent_reconciliation(ctx: AIContext, args: dict) -> dict:
    recon = ctx.result.recon
    if recon is None:
        return {"managers": [], "note": "No property-manager statements with owner payouts are uploaded."}
    want = str(args.get("manager") or "").strip().lower()
    managers = []
    for m in recon.managers:
        if want and want not in m.manager.lower():
            continue
        managers.append({
            "manager": m.manager, "basis": m.basis, "statement_paid_to_owner": m.statement_total,
            "bank_received": m.bank_total, "difference_bank_minus_statement": m.difference, "matches": m.ok,
            "bank_payee_keywords": m.keywords, "statement_files": m.files,
            "monthly": m.monthly.to_dict("records") if m.monthly is not None else None,
            "bank_deposits": m.deposits[["Date", "Amount", "Description", "Source File"]].head(MAX_ROWS).to_dict("records"),
        })
    unmatched = recon.unmatched[["Date", "Amount", "Description", "Source File"]].to_dict("records")
    return {"managers": managers, "unmatched_manager_like_deposits": unmatched, "payouts_with_no_statement": recon.bank_only}


def tool_list_files(ctx: AIContext, _args: dict) -> dict:
    files = []
    for rec in ctx.records:
        df = rec.get("df")
        n = len(df) if isinstance(df, pd.DataFrame) else 0
        uncoded = int((df["Account Code"].fillna("").isin(["", "Uncategorized"])).sum()) if n else 0
        files.append({"name": rec.get("name"), "type": rec.get("bucket"), "rows": n, "uncoded_rows": uncoded,
                      "note": str(rec.get("note") or "")})
    return {"files": files}


def tool_search_transactions(ctx: AIContext, args: dict) -> dict:
    rows = _filter(_all_rows(ctx, bool(args.get("include_excluded"))), args, ctx.jurisdiction)
    limit = max(1, min(int(args.get("limit") or 50), MAX_ROWS))
    cols = ["Date", "Amount", "Description", "Property", "Account Code", "Type", "Source File", "Excluded Because"]
    return {"matched_rows": len(rows), "matched_total": _money(rows["Amount"].sum()) if len(rows) else 0.0,
            "returned_rows": min(limit, len(rows)), "rows": rows[cols].head(limit).to_dict("records")}


def tool_summarize(ctx: AIContext, args: dict) -> dict:
    group_by = [g for g in (args.get("group_by") or ["account_code"]) if g in _GROUPS][:2] or ["account_code"]
    rows = _filter(_all_rows(ctx), args, ctx.jurisdiction)
    cols = [_GROUPS[g] for g in group_by]
    grp = rows.groupby(cols)["Amount"].agg(["count", "sum"]).round(2).reset_index()
    grp = grp.rename(columns={"count": "rows", "sum": "total", "_month": "month"})
    return {"group_by": group_by, "groups": grp.head(MAX_ROWS).to_dict("records"), "total": _money(rows["Amount"].sum())}


def tool_get_property_summary(ctx: AIContext, args: dict) -> dict:
    from axiom_pack_sheets import property_blocks

    want = str(args.get("property") or "").strip().lower()
    out = []
    for prop, table, net, rent in property_blocks(ctx.result.rows):
        if want and want not in prop.lower():
            continue
        out.append({"property": prop, "net": net, "by_account_code": table.to_dict("records"),
                    "rent_by_manager_and_period": rent.to_dict("records")})
    return {"properties": out}


def tool_list_rules(ctx: AIContext, args: dict) -> dict:
    text = str(args.get("text") or "").strip().lower()
    rules = [r for r in ctx.rules if not text or text in str(r.get("pattern", "")).lower()
             or text in str(r.get("account_code", "")).lower()]
    keep = [{"pattern": r.get("pattern"), "account_code": r.get("account_code"), "property": r.get("property")}
            for r in rules[:MAX_ROWS]]
    return {"matched_rules": len(rules), "rules": keep}


def tool_list_codes_and_properties(ctx: AIContext, _args: dict) -> dict:
    return {"account_codes": ctx.codes, "properties": ctx.properties}


_FILTER_PROPS = {
    "text": {"type": "STRING", "description": "Text the description must contain (case-insensitive)."},
    "property": {"type": "STRING", "description": "Property name (partial match)."},
    "account_code": {"type": "STRING", "description": "Account code (partial match), e.g. 'Rates' or 'Uncategorized'."},
    "source_file": {"type": "STRING", "description": "Source file name (partial match)."},
    "type": {"type": "STRING", "description": "'P&L' or 'Not P&L'."},
    "date_from": {"type": "STRING", "description": "Start date dd/mm/yyyy (inclusive)."},
    "date_to": {"type": "STRING", "description": "End date dd/mm/yyyy (inclusive)."},
    "min_amount": {"type": "NUMBER", "description": "Minimum amount (negative = money out)."},
    "max_amount": {"type": "NUMBER", "description": "Maximum amount."},
    "uncoded_only": {"type": "BOOLEAN", "description": "Only rows with no account code."},
}

TOOLS: dict[str, tuple] = {
    "get_overview": (tool_get_overview, "Entity, financial year, file counts, readiness status and headline totals.", {}),
    "get_readiness_checks": (tool_get_readiness_checks,
                             "The export readiness checklist: blockers, warnings and info items.", {}),
    "get_rent_reconciliation": (tool_get_rent_reconciliation,
                                "Property-manager statement owner payouts vs bank deposits, per manager, with monthly "
                                "running totals and the matched bank deposits.",
                                {"manager": {"type": "STRING", "description": "Optional manager name filter."}}),
    "list_files": (tool_list_files, "Every imported file with row counts, uncoded rows and its status note.", {}),
    "search_transactions": (tool_search_transactions,
                            "Search transactions (rows sent to the accountant; optionally excluded rows too).",
                            {**_FILTER_PROPS,
                             "include_excluded": {"type": "BOOLEAN", "description": "Also search rows left out of the "
                                                  "export (PM payouts, duplicate screenshots, $0 rows)."},
                             "limit": {"type": "INTEGER", "description": f"Max rows to return (≤{MAX_ROWS})."}}),
    "summarize": (tool_summarize, "Totals grouped by account_code, property, month, source_file, source_type or type.",
                  {**_FILTER_PROPS, "group_by": {"type": "ARRAY", "items": {"type": "STRING"},
                                                 "description": "Up to 2 of: " + ", ".join(_GROUPS)}}),
    "get_property_summary": (tool_get_property_summary,
                             "Per-property income/expenses by code (via manager vs bank), net, and rent by period.",
                             {"property": {"type": "STRING", "description": "Optional property filter."}}),
    "list_rules": (tool_list_rules, "Memorized coding rules (pattern → account code / property).",
                   {"text": {"type": "STRING", "description": "Optional filter text."}}),
    "list_codes_and_properties": (tool_list_codes_and_properties,
                                  "The entity's account code list (accountant's codes) and property list.", {}),
}


def function_declarations() -> list[dict]:
    """Gemini function declarations (JSON-schema style dicts accepted by google-genai)."""
    decls = []
    for name, (_fn, desc, props) in TOOLS.items():
        decl: dict = {"name": name, "description": desc}
        if props:
            decl["parameters"] = {"type": "OBJECT", "properties": props}
        decls.append(decl)
    return decls


def run_tool(ctx: AIContext, name: str, args: dict | None) -> dict:
    """Execute a tool locally; returns the masked, size-capped JSON-safe result."""
    if name not in TOOLS:
        return {"error": f"Unknown tool '{name}'."}
    try:
        result = TOOLS[name][0](ctx, dict(args or {}))
    except Exception as exc:  # report to the model instead of crashing the chat
        result = {"error": f"{type(exc).__name__}: {exc}"}
    safe = json.loads(json.dumps(mask_value(result), default=str))
    text = json.dumps(safe)
    if len(text) > MAX_RESULT_CHARS:
        return {"truncated": True, "note": "Result too large — narrow the filters or lower the limit.",
                "preview": text[:MAX_RESULT_CHARS]}
    return safe
