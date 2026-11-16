#!/usr/bin/env python3
"""
Deterministic, offline fixture sanitizer for AxiomLedger.
Uses Python 3 standard library only (zero network calls).

Reads a JSON, HTML, or CSV file and writes a scrubbed copy to:
  fixtures/sanitized_<original_filename>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


# Regex patterns for PII / financial / tax identifiers
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SSN_RE = re.compile(r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])")
EIN_RE = re.compile(r"(?<![0-9])[0-9]{2}-[0-9]{7}(?![0-9])")
PHONE_RE = re.compile(
    r"(?<![0-9])(?:\+?1[-. ]?)?(?:\([0-9]{3}\)|[0-9]{3})[-. ][0-9]{3}[-. ][0-9]{4}(?![0-9])"
)
STREET_RE = re.compile(
    r"(?<![A-Za-z0-9])[0-9]{1,6}[ ]+[A-Za-z0-9.' -]{2,40}[ ]+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place|Cir|Circle|Pkwy|Parkway)"
    r"\.?(?:[ ]*,?[ ]*(?:Apt|Suite|Ste|Unit|#)[ ]*[A-Za-z0-9-]+)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
ACCT_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:acct|account|loan|routing|iban|card)[ #:_-]*([0-9]{4,17})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Field-name / header classification
PERSON_KEYS = {
    "name", "full_name", "fullname", "first_name", "firstname", "last_name", "lastname",
    "patient", "patient_name", "client", "client_name", "borrower", "borrower_name",
    "user", "username", "owner", "contact_name", "therapist", "provider_name",
    "clinician", "subscriber", "beneficiary", "payee", "payer",
}
ENTITY_KEYS = {
    "entity", "entity_name", "company", "company_name", "organization", "org",
    "org_name", "business", "business_name", "lender", "bank", "bank_name",
    "institution", "merchant", "vendor", "employer", "clinic", "practice",
}
NOTE_KEYS = {
    "note", "notes", "medical_note", "medical_notes", "therapy_note", "therapy_notes",
    "clinical_note", "clinical_notes", "session_note", "session_notes", "progress_note",
    "diagnosis", "diagnoses", "treatment", "treatment_plan", "symptoms", "history",
    "narrative", "memo", "comments", "comment", "free_text", "observations",
}
EMAIL_KEYS = {"email", "email_address", "e_mail", "contact_email", "user_email"}
PHONE_KEYS = {"phone", "phone_number", "mobile", "cell", "telephone", "tel", "fax"}
ADDRESS_KEYS = {
    "address", "street", "street_address", "address_line_1", "address_line_2",
    "mailing_address", "billing_address", "home_address", "location",
}
TAX_ID_KEYS = {"ssn", "ein", "itin", "tin", "tax_id", "taxpayer_id", "social_security"}
ACCOUNT_KEYS = {
    "account", "account_number", "acct", "acct_no", "acct_num", "loan_number",
    "loan_id", "routing_number", "iban", "card_number", "policy_number",
}


class DeterministicMasker:
    """Maps sensitive values deterministically to privacy-safe tokens."""

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = {}
        self._counters: dict[str, int] = {}

    def _next_index(self, category: str, raw: str) -> int:
        norm = raw.strip().lower()
        cat_map = self._maps.setdefault(category, {})
        if norm in cat_map:
            return int(cat_map[norm])
        idx = self._counters.get(category, 0) + 1
        self._counters[category] = idx
        cat_map[norm] = str(idx)
        return idx

    def token_user(self, raw: str) -> str:
        idx = self._next_index("user", raw)
        return f"USER_{idx:03d}"

    def token_entity(self, raw: str) -> str:
        idx = self._next_index("entity", raw)
        # ENTITY_A .. ENTITY_Z, then ENTITY_27, ...
        if idx <= 26:
            return f"ENTITY_{chr(64 + idx)}"
        return f"ENTITY_{idx}"

    def token_email(self, raw: str) -> str:
        idx = self._next_index("email", raw)
        return f"user_{idx:03d}@example.com"

    def token_phone(self, raw: str) -> str:
        idx = self._next_index("phone", raw)
        return f"555-010-{idx:04d}"

    def token_address(self, raw: str) -> str:
        idx = self._next_index("address", raw)
        return f"{100 + idx} Redacted St"

    def token_tax_id(self, raw: str) -> str:
        idx = self._next_index("tax_id", raw)
        return f"TAX_ID_{idx:03d}"

    def token_account(self, raw: str) -> str:
        idx = self._next_index("account", raw)
        return f"ACCT_{idx:03d}"

    def token_int(self, category: str, raw: int) -> int:
        idx = self._next_index(category, str(raw))
        return 900000 + idx

    def scrub_inline_text(self, text: str) -> str:
        if not text:
            return text
        out = EMAIL_RE.sub(lambda m: self.token_email(m.group(0)), text)
        out = SSN_RE.sub(lambda m: self.token_tax_id(m.group(0)), out)
        out = EIN_RE.sub(lambda m: self.token_tax_id(m.group(0)), out)
        out = PHONE_RE.sub(lambda m: self.token_phone(m.group(0)), out)
        out = STREET_RE.sub(lambda m: self.token_address(m.group(0)), out)
        out = ACCT_NUM_RE.sub(lambda m: self.token_account(m.group(1)), out)
        return out

    def scrub_field(self, key: str, value: Any) -> Any:
        norm_key = key.strip().lower().replace("-", "_").replace(" ", "_")

        if value is None or isinstance(value, bool):
            return value

        if isinstance(value, int):
            if norm_key in TAX_ID_KEYS:
                return self.token_int("tax_id", value)
            if norm_key in ACCOUNT_KEYS:
                return self.token_int("account", value)
            return value

        if isinstance(value, float):
            return value

        if not isinstance(value, str):
            return value

        if not value.strip():
            return value

        if norm_key in NOTE_KEYS:
            return "REDACTED_NOTE"
        if norm_key in EMAIL_KEYS:
            return self.token_email(value)
        if norm_key in PHONE_KEYS:
            return self.token_phone(value)
        if norm_key in ADDRESS_KEYS:
            return self.token_address(value)
        if norm_key in TAX_ID_KEYS:
            return self.token_tax_id(value)
        if norm_key in ACCOUNT_KEYS:
            return self.token_account(value)
        if norm_key in ENTITY_KEYS:
            return self.token_entity(value)
        if norm_key in PERSON_KEYS:
            return self.token_user(value)

        return self.scrub_inline_text(value)


def sanitize_json_obj(obj: Any, masker: DeterministicMasker, parent_key: str = "") -> Any:
    if isinstance(obj, dict):
        return {
            k: sanitize_json_obj(v, masker, parent_key=str(k))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_json_obj(item, masker, parent_key=parent_key) for item in obj]
    if parent_key:
        return masker.scrub_field(parent_key, obj)
    if isinstance(obj, str):
        return masker.scrub_inline_text(obj)
    return obj


class SanitizingHTMLParser(HTMLParser):
    """Preserves exact HTML tags, attributes, classes, and structure while scrubbing text."""

    def __init__(self, masker: DeterministicMasker) -> None:
        super().__init__(convert_charrefs=False)
        self.masker = masker
        self.parts: list[str] = []
        self._in_raw_tag = False

    def _format_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        if not attrs:
            return ""
        rendered = []
        for name, val in attrs:
            if val is None:
                rendered.append(f" {name}")
            else:
                escaped = val.replace('"', "&quot;")
                rendered.append(f' {name}="{escaped}"')
        return "".join(rendered)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style"):
            self._in_raw_tag = True
        self.parts.append(f"<{tag}{self._format_attrs(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(f"<{tag}{self._format_attrs(attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._in_raw_tag = False
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._in_raw_tag:
            self.parts.append(data)
        else:
            self.parts.append(self.masker.scrub_inline_text(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{self.masker.scrub_inline_text(data)}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")


def sanitize_csv_text(raw_text: str, masker: DeterministicMasker) -> str:
    sample = raw_text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(raw_text), dialect=dialect)
    rows = list(reader)
    if not rows:
        return raw_text

    headers = rows[0]
    out_buf = io.StringIO()
    writer = csv.writer(out_buf, dialect=dialect, lineterminator=chr(10))
    writer.writerow(headers)

    for row in rows[1:]:
        scrubbed_row = []
        for idx, cell in enumerate(row):
            header = headers[idx] if idx < len(headers) else ""
            scrubbed_row.append(masker.scrub_field(header, cell))
        writer.writerow(scrubbed_row)

    return out_buf.getvalue()


def sanitize_file(input_path: Path, output_dir: Path | None = None) -> Path:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    repo_root = Path(__file__).resolve().parent.parent
    fixtures_dir = output_dir if output_dir is not None else (repo_root / "fixtures")
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    out_name = f"sanitized_{input_path.name}"
    out_path = fixtures_dir / out_name

    raw_text = input_path.read_text(encoding="utf-8")
    masker = DeterministicMasker()
    suffix = input_path.suffix.lower()

    if suffix == ".json":
        data = json.loads(raw_text)
        sanitized = sanitize_json_obj(data, masker)
        out_text = json.dumps(sanitized, indent=2, ensure_ascii=False) + chr(10)
    elif suffix in (".html", ".htm"):
        parser = SanitizingHTMLParser(masker)
        parser.feed(raw_text)
        parser.close()
        out_text = "".join(parser.parts)
    elif suffix == ".csv":
        out_text = sanitize_csv_text(raw_text, masker)
    else:
        raise ValueError(f"Unsupported file type '{suffix}'. Supported: .json, .html, .htm, .csv")

    out_path.write_text(out_text, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrub PII/financial/medical fields into fixtures/sanitized_<filename>."
    )
    parser.add_argument("input_file", help="Path to input .json, .html, or .csv file")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory (defaults to repo fixtures/)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.output_dir) if args.output_dir else None
    out_path = sanitize_file(Path(args.input_file), output_dir=out_dir)
    print(f"Wrote sanitized fixture: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
