"""
axiom_ai_redact
===============

Masking applied to everything Ask Axiom sends to Gemini (tool results and
conversation text). Amounts, dates, payees, codes and properties are kept —
the assistant needs them — but identifiers are masked:

* NZ bank account numbers (``12-3456-7890123-00``) and long digit runs
  (7+ digits: account / card / loan / reference numbers) → ``[#…123]``
  (last 3 digits kept so you can still tell accounts apart)
* IRD numbers (``123-456-789``), email addresses, phone numbers
* Optional: descriptions of incoming bank payments that are not from a
  property manager, a bank/loan/tax source or your own accounts (these are
  usually tenants paying directly) → ``[incoming payment — details hidden]``

Pure functions only.
"""

from __future__ import annotations

import re
from typing import Any

_NZ_ACCOUNT_RE = re.compile(r"\b\d{2}[- ]\d{4}[- ]\d{7}[- ]\d{2,3}\b")
_IRD_RE = re.compile(r"\b\d{2,3}-\d{3}-\d{3}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\d$.,])(?:\+64[ -]?|\(0\d\)[ -]?|0)\d{1,2}[ -]\d{3}[ -]?\d{3,4}(?![\d.,])")
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]){3}\d{1,4}\b")
_LONG_DIGITS_RE = re.compile(r"(?<![\d.,$])\d{7,}(?![\d.,])")

HIDDEN_INCOMING = "[incoming payment — details hidden]"
_KEEP_INCOMING_RE = re.compile(
    r"loan|advance|drawdown|interest|transfer|ird|inland revenue|refund|insurance|settlement|bank|"
    r"property management|realty|rentals|ray ?white|barfoot|b ?& ?t|charlton|scpm|csi|lawyer|solicitor|trust account",
    re.IGNORECASE,
)


def _tail(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"[#…{digits[-3:]}]"


def mask_text(text: str) -> str:
    """Mask identifiers inside a string."""
    if not text:
        return text
    out = _EMAIL_RE.sub("[email]", text)
    out = _NZ_ACCOUNT_RE.sub(_tail, out)
    out = _CARD_RE.sub(_tail, out)
    out = _IRD_RE.sub("[IRD no.]", out)
    out = _PHONE_RE.sub("[phone]", out)
    return _LONG_DIGITS_RE.sub(_tail, out)


def mask_value(value: Any) -> Any:
    """Recursively mask strings inside dicts / lists / tuples."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_value(v) for v in value]
    return value


def hide_incoming_description(description: str, amount: float, source_type: str, entity: str = "") -> str:
    """Hide likely tenant names on incoming bank payments (privacy option)."""
    if source_type != "Bank CSV" or not amount or amount <= 0:
        return description
    text = str(description or "")
    stem = " ".join(re.findall(r"[a-z0-9&]+", entity.lower())[:2])
    if _KEEP_INCOMING_RE.search(text) or (stem and stem in text.lower()):
        return text
    return HIDDEN_INCOMING
