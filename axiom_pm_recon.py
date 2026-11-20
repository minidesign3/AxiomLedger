"""
axiom_pm_recon
==============

Rent check — the accountant's tick-off: do the owner payouts each property
manager says it made line up with the deposits that reached the bank?

* :func:`effective_payee_map` — manager → bank payee keywords (defaults for the
  known managers, overridable per entity in the memory store).
* :func:`match_pm_deposits` — tags bank-CSV deposits with the manager they came
  from (longest keyword wins, so "ray white karaka" beats "ray white").
* :func:`reconcile` — per manager: statement total vs bank total, difference,
  status, and for monthly statements a month-by-month table with running
  totals (managers pay on their own cycle, so single months swing; the running
  difference shows where a gap starts and whether it catches up).
* :func:`own_account_rows` — rows that look like the entity moving money
  between its own accounts but are coded as income/expense.

Matched deposits are kept OUT of the accountant's transactions/P&L (rent comes
from the PM statements, gross per house); they appear only on the Rent
Reconciliation sheet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from axiom_pm_payouts import MONTHLY

TOLERANCE = 1.00
_KNOWN_ALIASES = {
    "charlton": ["charlton"],
    "barfoot": ["b&t city", "b & t city", "barfoot"],
    "karaka": ["ray white karaka", "csi property"],
    "ray white": ["scpml", "scpm ltd", "ray white", "raywhite", "leading edge", "compass pm"],
}
PM_LIKE_RE = re.compile(
    r"property management|property mgmt|realty|rentals|real estate|harcourts|ray ?white|barfoot|b ?& ?t city|"
    r"charlton|scpm|csi property|quinovic|bayleys|lj hooker|leading edge|compass pm",
    re.IGNORECASE,
)


@dataclass
class ManagerRecon:
    manager: str
    basis: str
    statement_total: float
    bank_total: float
    keywords: list[str]
    files: list[str]
    monthly: pd.DataFrame | None = None
    deposits: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def difference(self) -> float:
        return round(self.bank_total - self.statement_total, 2)

    @property
    def ok(self) -> bool:
        return abs(self.difference) <= TOLERANCE


@dataclass
class ReconResult:
    managers: list[ManagerRecon]
    matched_mask: pd.Series
    unmatched: pd.DataFrame
    bank_only: dict[str, float] = field(default_factory=dict)  # payouts in bank from managers with no statement


def default_keywords(manager: str) -> list[str]:
    low = manager.lower()
    if "karaka" in low:
        return list(_KNOWN_ALIASES["karaka"])
    for key, words in _KNOWN_ALIASES.items():
        if key in low:
            return list(words)
    return [low]


def effective_payee_map(managers: list[str], saved: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    saved = saved or {}
    out = {m: [k.strip().lower() for k in saved.get(m, default_keywords(m)) if k.strip()] for m in managers}
    for m, kws in saved.items():  # managers configured without a statement uploaded
        out.setdefault(m, [k.strip().lower() for k in kws if k.strip()])
    return out


def match_pm_deposits(rows: pd.DataFrame, payee_map: dict[str, list[str]]) -> pd.Series:
    """Manager name for bank-CSV deposits (Amount > 0) matching a payee keyword, else ''."""
    pairs = sorted(((kw, m) for m, kws in payee_map.items() for kw in kws if kw), key=lambda p: -len(p[0]))
    desc = rows["Description"].fillna("").astype(str).str.lower()
    is_deposit = (rows["_bucket"] == "csv") & (pd.to_numeric(rows["Amount"], errors="coerce").fillna(0) > 0)
    out = []
    for text, dep in zip(desc, is_deposit, strict=True):
        out.append(next((m for kw, m in pairs if kw in text), "") if dep else "")
    return pd.Series(out, index=rows.index)


def _monthly_table(payouts: list[dict], deposits: pd.DataFrame) -> pd.DataFrame:
    stmt = pd.DataFrame(payouts).groupby("month")["amount"].sum() if payouts else pd.Series(dtype=float)
    months = [d.strftime("%Y-%m") if d is not None else "" for d in deposits["_date"]]
    bank = deposits.assign(month=months).groupby("month")["Amount"].sum() if not deposits.empty else pd.Series(dtype=float)
    table = pd.DataFrame({"Statement paid to you": stmt, "Bank received": bank}).fillna(0.0).sort_index()
    table["Running statement"] = table["Statement paid to you"].cumsum()
    table["Running bank"] = table["Bank received"].cumsum()
    table["Running difference"] = table["Running bank"] - table["Running statement"]
    return table.round(2).reset_index(names="Month")


def reconcile(rows: pd.DataFrame, statements: dict[str, dict], saved_map: dict[str, list[str]] | None = None) -> ReconResult:
    """Reconcile prepared export rows (needs ``_bucket``, ``_date``) against statement payouts."""
    payee_map = effective_payee_map(list(statements), saved_map)
    managers = match_pm_deposits(rows, payee_map)
    result: list[ManagerRecon] = []
    for name, info in statements.items():
        deps = rows[managers == name]
        recon = ManagerRecon(
            manager=name,
            basis=info["basis"],
            statement_total=round(sum(p["amount"] for p in info["payouts"]), 2),
            bank_total=round(float(deps["Amount"].sum()), 2),
            keywords=payee_map.get(name, []),
            files=list(info.get("files", [])),
            deposits=deps,
        )
        if info["basis"] == MONTHLY:
            recon.monthly = _monthly_table(info["payouts"], deps)
        result.append(recon)
    bank_only = {m: round(float(rows.loc[managers == m, "Amount"].sum()), 2)
                 for m in payee_map if m not in statements and (managers == m).any()}
    deposit = (rows["_bucket"] == "csv") & (pd.to_numeric(rows["Amount"], errors="coerce").fillna(0) > 0)
    unmatched = rows[deposit & (managers == "") & rows["Description"].fillna("").str.contains(PM_LIKE_RE)]
    return ReconResult(managers=result, matched_mask=managers != "", unmatched=unmatched, bank_only=bank_only)


def get_saved_payee_map(entity: str) -> dict[str, list[str]]:
    """User-edited manager → bank payee keywords for ``entity`` (memory store)."""
    from axiom_memory import _load_memory_store

    saved = (_load_memory_store().get("pm_payee_map") or {}).get(entity) or {}
    return {str(k): [str(x) for x in v] for k, v in saved.items() if isinstance(v, list)}


def save_payee_map(entity: str, mapping: dict[str, list[str]]) -> None:
    from axiom_memory import _load_memory_store, _save_memory_store

    store = _load_memory_store()
    store.setdefault("pm_payee_map", {})[entity] = {
        m: [k.strip().lower() for k in kws if k and k.strip()] for m, kws in mapping.items() if m.strip()
    }
    _save_memory_store(store)


def own_account_rows(rows: pd.DataFrame, entity: str) -> pd.Series:
    """P&L rows whose text names the entity itself (likely own-account transfers)."""
    words = re.findall(r"[a-z0-9&]+", entity.lower())
    stem = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "")
    if len(stem) < 4:
        return pd.Series(False, index=rows.index)
    desc = rows["Description"].fillna("").astype(str).str.lower()
    return desc.str.contains(re.escape(stem)) & (rows["Type"] == "P&L") & (rows["_bucket"] == "csv")
