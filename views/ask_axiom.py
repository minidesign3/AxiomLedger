"""Ask Axiom — Gemini-powered assistant over the app's data (read-only)."""

from axiom_ui_ask import render_ask_axiom
from utils import get_active_entity, get_active_tax_year

render_ask_axiom(get_active_entity(), get_active_tax_year())
