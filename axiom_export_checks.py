"""
axiom_export_checks
===================

Builds the rows that go to the accountant and the export-readiness checklist.

:func:`build_export` (pure) takes the ledger + file records and returns an
:class:`ExportResult`:

* ``rows`` — one clean row per transaction: Date in one format, Amount,
  Description, Property, Account Code, ``Type`` (``P&L`` or ``Not P&L`` for
  transfers / drawings / principal / tax payments …), ``Source File``,
  ``Source Type``.
* ``excluded`` — rows left out, with the reason ($0 placeholder rows,
  screenshot rows that repeat a bank-CSV row).
* ``checks`` — :class:`Check` items. ``blocker`` = the numbers would be wrong
  or incomplete; ``warning`` = worth a look; ``info`` = what was done.

:func:`prepare_export` is the app wrapper (loads ledger, files, codes …).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from axiom_core import STANDARD_COLUMNS, UNASSIGNED_PROPERTY
from axiom_dates import (
    ISSUE_BLANK,
    ISSUE_OUTSIDE_FY,
    ISSUE_UNPARSED,
    ISSUE_YEAR_INFERRED,
    normalize_date,
    parse_any_date,
)
from axiom_dedupe import duplicate_content_groups, screenshot_bank_overlap_mask
from axiom_ledger_sync import LEDGER_ONLY, ledger_with_sources
from axiom_pm_recon import ReconResult, own_account_rows, reconcile
from axiom_pm_rows import detect_pm_double_count

EXPORT_COLUMNS = [*STANDARD_COLUMNS, "Type", "Source File", "Source Type"]
_NON_PL_RE = re.compile(
    r"transfer|drawing|principal|\bird\b|tax payment|\bbond\b|contribution|settlement|capital|drawdown|loan advance",
    re.IGNORECASE,
)
_UNCODED = {"", "uncategorized", "unassigned", "nan", "none"}
PAYOUT_REASON = "Property-manager owner payout (rent comes from the PM statement) — see Rent Reconciliation"


@dataclass
class Check:
    level: str  # 'blocker' | 'warning' | 'info'
    name: str
    count: int
    detail: str


@dataclass
class ExportResult:
    rows: pd.DataFrame
    excluded: pd.DataFrame
    checks: list[Check] = field(default_factory=list)
    recon: ReconResult | None = None

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.level == "blocker"]

    @property
    def ready(self) -> bool:
        return not self.blockers


def _money(value: float) -> str:
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def is_non_pl_code(code: object) -> bool:
    return bool(_NON_PL_RE.search(str(code or "")))


def _prepare_rows(rows: pd.DataFrame, jurisdiction: str, fy) -> pd.DataFrame:
    rows = rows.copy()
    parsed = [normalize_date(v, jurisdiction, fy) for v in rows["Date"]]
    rows["Date"] = [p[0] for p in parsed]
    rows["_issue"] = [p[1] for p in parsed]
    rows["_date"] = [parse_any_date(v, jurisdiction, fy)[0] for v in rows["Date"]]
    rows["Amount"] = pd.to_numeric(rows["Amount"], errors="coerce").fillna(0.0).round(2)
    rows["Account Code"] = rows["Account Code"].fillna("").astype(str).str.strip()
    rows.loc[rows["Account Code"].str.lower().isin(_UNCODED), "Account Code"] = "Uncategorized"
    rows["Property"] = rows["Property"].fillna("").astype(str).str.strip().replace("", UNASSIGNED_PROPERTY)
    rows["Description"] = rows["Description"].fillna("").astype(str).str.replace(r"[\r\n]+", " ", regex=True).str.strip()
    rows["Type"] = ["Not P&L" if is_non_pl_code(c) else "P&L" for c in rows["Account Code"]]
    return rows


def _split_excluded(
    rows: pd.DataFrame, include_overlaps: bool, payout_mask: pd.Series | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    reasons = pd.Series("", index=rows.index)
    reasons[rows["Amount"] == 0.0] = "$0 placeholder / no amount"
    if payout_mask is not None:
        reasons[payout_mask & (reasons == "")] = PAYOUT_REASON
    overlap = screenshot_bank_overlap_mask(rows)
    if not include_overlaps:
        reasons[overlap & (reasons == "")] = "Screenshot row repeats a bank-CSV row (same date & amount)"
    keep = reasons == ""
    excluded = rows[~keep].assign(**{"Excluded Because": reasons[~keep]})
    return rows[keep], excluded


def _row_checks(rows: pd.DataFrame, is_unassigned: Callable[[str, str], bool]) -> list[Check]:
    checks: list[Check] = []
    uncoded = rows["Account Code"] == "Uncategorized"
    if uncoded.any():
        checks.append(Check("blocker", "Uncoded rows", int(uncoded.sum()),
                            f"{int(uncoded.sum())} row(s) have no Account Code (net {_money(rows.loc[uncoded, 'Amount'].sum())})."))
    no_prop = pd.Series([is_unassigned(p, c) for p, c in zip(rows["Property"], rows["Account Code"], strict=True)], index=rows.index)
    if no_prop.any():
        checks.append(Check("blocker", "No property", int(no_prop.sum()), f"{int(no_prop.sum())} row(s) have no Property."))
    for issue, level, label in (
        (ISSUE_BLANK, "blocker", "rows have no date"),
        (ISSUE_UNPARSED, "blocker", "rows have a date that can't be read"),
        (ISSUE_OUTSIDE_FY, "blocker", "rows are dated outside the financial year"),
        (ISSUE_YEAR_INFERRED, "warning", "rows had no year in the date (year was inferred)"),
    ):
        n = int((rows["_issue"] == issue).sum())
        if n:
            checks.append(Check(level, f"Dates: {issue.replace('_', ' ')}", n, f"{n} {label}."))
    return checks


def _code_checks(rows: pd.DataFrame, codes: list[str], properties: list[str]) -> list[Check]:
    checks: list[Check] = []
    known = {c.lower() for c in codes} | {"uncategorized"}
    off_list = sorted({c for c in rows["Account Code"] if c.lower() not in known})
    if off_list:
        n = int(rows["Account Code"].isin(off_list).sum())
        checks.append(Check("warning", "Codes not on the accountant's list", n, "Not on the entity code list: " + ", ".join(off_list)))
    props_known = {p.lower() for p in properties} | {UNASSIGNED_PROPERTY.lower()}
    split = sorted({p for p in rows["Property"] if " / " in p and p.lower() not in props_known})
    if split:
        n = int(rows["Property"].isin(split).sum())
        checks.append(Check("warning", "Split-property rows", n, "Needs dividing between properties: " + ", ".join(split)))
    transfers = rows[rows["Account Code"].str.contains("transfer", case=False)]
    if not transfers.empty and abs(transfers["Amount"].sum()) >= 1.0:
        checks.append(Check("warning", "Transfers don't net to zero", len(transfers),
                            f"Transfer rows total {_money(transfers['Amount'].sum())} — a statement may be missing "
                            "or a transfer is miscoded."))
    pl = rows[(rows["Type"] == "P&L") & (rows["Account Code"] != "Uncategorized")]
    for code, grp in pl.groupby("Account Code"):
        pos, neg = grp.loc[grp.Amount > 0, "Amount"].sum(), grp.loc[grp.Amount < 0, "Amount"].sum()
        if pos >= 100 and -neg >= 100 and min(pos, -neg) >= 0.05 * (pos - neg):
            checks.append(Check("warning", f"Mixed signs: {code}", len(grp),
                                f"'{code}' has money in {_money(pos)} AND money out {_money(neg)} — check signs/coding."))
    return checks


def _file_checks(records: list[dict[str, object]], aligned: bool, rows: pd.DataFrame) -> list[Check]:
    checks: list[Check] = []
    if not aligned:
        checks.append(Check("blocker", "Ledger out of sync", 1,
                            "The master ledger has fewer rows than the imported files — re-apply rules to re-sync."))
    for names in duplicate_content_groups(records):
        checks.append(Check("blocker", "Same file imported twice", len(names),
                            "Identical contents: " + " = ".join(names) + " — delete all but one."))
    for rec in records:
        note = str(rec.get("note") or "")
        if note.startswith("⚠️"):
            checks.append(Check("blocker", f"File warning: {rec.get('name')}", 1, note))
    double = detect_pm_double_count(rows)
    if double:
        checks.append(Check("blocker", "Rent counted twice", 1, double))
    n_ledger_only = int((rows["Source File"] == LEDGER_ONLY).sum())
    if n_ledger_only:
        checks.append(Check("warning", "Rows not linked to a file", n_ledger_only,
                            f"{n_ledger_only} row(s) exist only in the master ledger (not tied to an uploaded file)."))
    return checks


def _recon_checks(recon: ReconResult | None, kept: pd.DataFrame, entity: str) -> list[Check]:
    checks: list[Check] = []
    if recon is not None:
        for m in recon.managers:
            if not m.ok:
                checks.append(Check("blocker", f"Rent check: {m.manager}", len(m.deposits),
                                    f"{m.manager} statement says it paid you {_money(m.statement_total)}; the bank shows "
                                    f"{_money(m.bank_total)} from it (difference {_money(m.difference)}). "
                                    "See the Rent Reconciliation sheet."))
        for m, total in recon.bank_only.items():
            checks.append(Check("warning", f"Payouts with no statement: {m}", 1,
                                f"The bank received {_money(total)} from {m} but no statement from {m} is uploaded."))
        if not recon.unmatched.empty:
            checks.append(Check("warning", "Unmatched manager-like deposits", len(recon.unmatched),
                                f"{len(recon.unmatched)} bank deposit(s) ({_money(recon.unmatched['Amount'].sum())}) look like a "
                                "property manager but match no manager's payee keywords."))
    if entity:
        own = own_account_rows(kept, entity)
        if own.any():
            codes = ", ".join(sorted(set(kept.loc[own, "Account Code"])))
            checks.append(Check("warning", "Own-account transfers coded as income/expense", int(own.sum()),
                                f"{int(own.sum())} bank row(s) ({_money(kept.loc[own, 'Amount'].sum())}) name {entity} itself "
                                f"but are coded {codes} — usually a transfer between your own accounts."))
    return checks


@dataclass
class ExportConfig:
    """Reference data + options for :func:`build_export` (all optional)."""

    jurisdiction: str = "NZ"
    fy: object = None
    codes: list[str] | None = None
    properties: list[str] | None = None
    is_unassigned: Callable[[str, str], bool] | None = None
    include_overlaps: bool = False
    statements: dict[str, dict] | None = None
    payee_map: dict[str, list[str]] | None = None
    entity: str = ""


def build_export(ledger: pd.DataFrame, records: list[dict[str, object]], **options) -> ExportResult:
    """Clean export rows + excluded rows + readiness checks (+ rent reconciliation) (pure).

    ``options`` are :class:`ExportConfig` fields (unknown names raise ``TypeError``).
    """
    cfg = ExportConfig(**options)
    unassigned = cfg.is_unassigned or (lambda p, c: p in ("", UNASSIGNED_PROPERTY))
    sourced, aligned = ledger_with_sources(ledger, records)
    rows = _prepare_rows(sourced, cfg.jurisdiction, cfg.fy)
    recon = reconcile(rows, cfg.statements, cfg.payee_map) if cfg.statements else None
    kept, excluded = _split_excluded(rows, cfg.include_overlaps, recon.matched_mask if recon else None)
    checks = _file_checks(records, aligned, kept) + _recon_checks(recon, kept, cfg.entity) + _row_checks(kept, unassigned)
    checks += _code_checks(kept, list(cfg.codes or []), list(cfg.properties or []))
    for reason, grp in excluded.groupby("Excluded Because"):
        checks.append(Check("info", "Left out of export", len(grp), f"{len(grp)} row(s): {reason}."))
    return ExportResult(rows=kept[EXPORT_COLUMNS].reset_index(drop=True),
                        excluded=excluded[[*EXPORT_COLUMNS, "Excluded Because"]].reset_index(drop=True),
                        checks=checks, recon=recon)


def prepare_export(entity: str, tax_year: str, include_overlaps: bool = False) -> ExportResult:
    """App wrapper: load ledger, files, codes, properties and jurisdiction for ``build_export``."""
    from axiom_account_codes import get_entity_account_codes
    from axiom_dates import fy_bounds
    from axiom_dedupe import _record_bytes
    from axiom_ingestion import get_entity_uploaded_files
    from axiom_ledger import get_entity_ledger, is_property_unassigned
    from axiom_pm_payouts import statement_payouts
    from axiom_pm_recon import get_saved_payee_map
    from axiom_properties import get_entity_properties
    from axiom_state import get_entity_jurisdiction

    jurisdiction = get_entity_jurisdiction(entity)
    # Files first: loading them may repair records and rebuild the ledger.
    records = get_entity_uploaded_files(entity, tax_year)
    return build_export(
        get_entity_ledger(entity, tax_year),
        records,
        jurisdiction=jurisdiction,
        fy=fy_bounds(tax_year, jurisdiction),
        codes=get_entity_account_codes(entity),
        properties=get_entity_properties(entity),
        is_unassigned=lambda p, c: is_property_unassigned(p, c),
        include_overlaps=include_overlaps,
        statements=statement_payouts(records, _record_bytes),
        payee_map=get_saved_payee_map(entity),
        entity=entity,
    )
