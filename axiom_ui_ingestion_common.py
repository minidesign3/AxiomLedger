"""
axiom_ui_ingestion_common
=========================

Shared context for the full-page file reviewer (phase D20). Holds the values that
the pre-refactor reviewer kept as locals, so the section renderers stay
byte-identical body moves.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ReviewContext:
    """Everything the reviewer sections need, resolved once per render."""

    file_rec: dict
    file_id: str
    file_name: str
    bucket: str
    df: pd.DataFrame
    state_open_key: str
    active_entity: str
    active_tax_year: str
    key_prefix: str
    vault_path_str: str
    vault_file_bytes: bytes | None
