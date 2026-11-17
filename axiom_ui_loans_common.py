"""
axiom_ui_loans_common
=====================

Shared pieces of the Property Loans & Bank Registry block (phase D19): the
``LoanBlockContext`` passed to every render helper, plus the bank / lender and
loan-option lists. The option lists were module-level constants in ``utils.py``
and are re-exported there so existing imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NZ_BANK_OPTIONS = [
    "ANZ",
    "ASB",
    "BNZ",
    "Westpac",
    "Kiwibank",
    "TSB",
    "SBS Bank",
    "Heartland Bank",
    "Co-operative Bank",
    "Other / Non-Bank Lender",
]
LOAN_INTEREST_TYPE_OPTIONS = [
    "Fixed",
    "Floating / Variable",
    "Revolving Credit / Flexi",
    "Offset",
]
LOAN_REPAYMENT_TYPE_OPTIONS = [
    "Interest Only",
    "Principal & Interest",
    "Revolving / Line of Credit",
]
LOAN_FREQUENCY_OPTIONS = [
    "Monthly",
    "Fortnightly",
    "Weekly",
    "Quarterly",
    "At Maturity",
]


@dataclass(frozen=True)
class LoanBlockContext:
    """Pre-computed state shared by the loans render helpers."""

    key_prefix: str
    target: str
    loans: list[dict[str, Any]]
    props: list[str]
    cfg: dict[str, Any]
    show_import_key: str
    ocr_result_key: str
    ocr_image_key: str
    ocr_target_key: str
    import_location_key: str
    expanded_prop_key: str
