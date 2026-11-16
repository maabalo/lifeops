"""
LifeOps extraction: turn document text into a PROPOSAL (never a record) with evidence and confidence.

The contract (schema version 1) is the same for every extractor, so Claude can replace the mock in M5:

    {"doc_type": Field, "title": Field, "supplier": Field, "amount": Field, "currency": Field,
     "reference": Field, "due_date": Field, "expiry_date": Field}
    Field = {"value": str | None, "confidence": "high" | "medium" | "low",
             "evidence": {"page": int, "snippet": str} | None,
             "date_kind": "explicit" | "ambiguous" | None, "options": [str]}   # dates only

Rules (from the spec): unknown fields are null, never guessed; ambiguous dates such as 03/04/2027 are not
resolved: both readings are offered and the user picks. Document text is DATA: the mock only pattern-matches
it, and the Claude adapter (M5) will get it inside delimiters, with no tools, and its answer is validated here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

SCHEMA_VERSION = 1
FIELDS = ("doc_type", "title", "supplier", "amount", "currency", "reference", "due_date", "expiry_date")
DOC_TYPES = ("bill", "warranty", "renewal", "receipt", "other")
SNIPPET_CHARS = 120

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
            ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


@dataclass
class Found:
    value: str | None = None
    confidence: str = "low"
    page: int | None = None
    snippet: str = ""
    date_kind: str | None = None
    options: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {
            "value": self.value,
            "confidence": self.confidence,
            "evidence": {"page": self.page, "snippet": self.snippet} if self.snippet else None,
        }
        if self.date_kind is not None or self.options:
            out["date_kind"] = self.date_kind
            out["options"] = self.options
        return out


class Extractor(Protocol):
    name: str

    def extract(self, pages: list[str]) -> dict: ...


# ---- the mock: deterministic rules ---------------------------------------------------------------
DUE_WORDS = re.compile(r"\b(due|pay\s+by|payment\s+due|deadline|on\s+or\s+before)\b", re.I)
EXPIRY_WORDS = re.compile(r"\b(expir\w*|ends?|valid\s+until|warranty\s+until|renew\w*\s+by|until)\b", re.I)
AMOUNT_RE = re.compile(
    r"(?P<cur>₱|PHP|Php|P(?=\s?\d)|USD|US\$|\$)\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<num2>\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})\s?(?P<cur2>PHP|USD)"
)
REFERENCE_RE = re.compile(
    r"\b(?:account|acct|ref(?:erence)?|invoice|policy|serial|contract|plate)\s*(?:no\.?|number|#)?\s*[:#]?\s*"
    r"(?P<ref>(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,30})",  # must contain a digit: never a plain word
    re.I,
)
SUPPLIER_HINT = re.compile(
    r"\b([A-Z][A-Za-z&.]+(?:\s+[A-Z][A-Za-z&.]+){0,3})\s+"
    r"(?:Inc\.?|Corp\.?|Corporation|Co\.?|Company|Electric|Water|Telecom|Insurance|Services?)\b"
)
TYPE_WORDS = [
    ("warranty", re.compile(r"\bwarrant(y|ies)\b|\bguarantee\b", re.I)),
    ("bill", re.compile(r"\b(bill|statement of account|amount due|invoice)\b", re.I)),
    ("renewal", re.compile(r"\b(renew\w*|registration|license|licence|subscription|membership)\b", re.I)),
    ("receipt", re.compile(r"\b(receipt|paid|thank you for your payment)\b", re.I)),
]


def snippet(text: str, start: int, end: int) -> str:
    """The words around a match, trimmed to SNIPPET_CHARS, on one line."""
    a = max(0, start - 30)  # a little context before, so the match itself always fits in the snippet
    b = min(len(text), end + 50)
    return " ".join(text[a:b].split())[:SNIPPET_CHARS]


def iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, int, Found]]:
    """Every date in the text with where it is. Written-out months are explicit; 03/04/2027 is ambiguous."""
    found = []
    patterns = [
        (rf"\b(?P<d>\d{{1,2}})\s+(?P<m>{MONTH_RE})\.?,?\s+(?P<y>\d{{4}})\b", "dmy"),
        (rf"\b(?P<m>{MONTH_RE})\.?\s+(?P<d>\d{{1,2}}),?\s+(?P<y>\d{{4}})\b", "mdy"),
        (r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b", "iso"),
        (r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\b", "slash"),
    ]
    for pattern, style in patterns:
        for m in re.finditer(pattern, text, re.I):
            y = int(m["y"])
            if style in ("dmy", "mdy"):
                value = iso(y, MONTHS[m["m"].lower().rstrip(".")], int(m["d"]))
                item = Found(value=value, confidence="high", date_kind="explicit")
            elif style == "iso":
                value = iso(y, int(m["m"]), int(m["d"]))
                item = Found(value=value, confidence="high", date_kind="explicit")
            else:
                a, b = int(m["a"]), int(m["b"])
                day_first, month_first = iso(y, b, a), iso(y, a, b)
                options = sorted({o for o in (day_first, month_first) if o})
                if len(options) == 1:
                    item = Found(value=options[0], confidence="medium", date_kind="explicit")
                else:
                    item = Found(value=None, confidence="low", date_kind="ambiguous", options=options)
            if item.value or item.options:
                found.append((m.start(), m.end(), item))
    found.sort(key=lambda x: x[0])
    return found


def pick_date(pages: list[str], cue: re.Pattern) -> Found:
    """The first date that has a cue word (due / expires…) shortly before it on the same page."""
    for number, text in enumerate(pages, start=1):
        for start, end, item in find_dates(text):
            window = text[max(0, start - 60) : start]
            if cue.search(window):
                item.page, item.snippet = number, snippet(text, start, end)
                return item
    return Found()


def first_match(pages: list[str], pattern: re.Pattern, group: str) -> tuple[str, int, str] | None:
    for number, text in enumerate(pages, start=1):
        m = pattern.search(text)
        if m:
            return m.group(group), number, snippet(text, m.start(), m.end())
    return None


class MockExtractor:
    """Free, offline and deterministic: the same text always gives the same proposal (used in tests too)."""

    name = "mock-v1"

    def extract(self, pages: list[str]) -> dict:
        result = {name: Found() for name in FIELDS}
        text_pages = [p for p in pages]
        result["due_date"] = pick_date(text_pages, DUE_WORDS)
        result["expiry_date"] = pick_date(text_pages, EXPIRY_WORDS)
        for number, text in enumerate(text_pages, start=1):
            for doc_type, words in TYPE_WORDS:
                m = words.search(text)
                if m:
                    result["doc_type"] = Found(doc_type, "medium", number, snippet(text, m.start(), m.end()))
                    break
            if result["doc_type"].value:
                break
        for number, text in enumerate(text_pages, start=1):
            m = AMOUNT_RE.search(text)
            if m:
                raw = (m["num"] or m["num2"]).replace(",", "")
                cur = (m["cur"] or m["cur2"]).upper().replace("₱", "PHP").replace("US$", "USD").replace("$", "USD")
                cur = "PHP" if cur == "P" else cur
                where = snippet(text, m.start(), m.end())
                result["amount"] = Found(f"{float(raw):.2f}", "medium", number, where)
                result["currency"] = Found(cur, "medium", number, where)
                break
        ref = first_match(text_pages, REFERENCE_RE, "ref")
        if ref:
            result["reference"] = Found(ref[0], "medium", ref[1], ref[2])
        supplier = first_match(text_pages, SUPPLIER_HINT, 0)
        if supplier:
            result["supplier"] = Found(supplier[0].strip(), "low", supplier[1], supplier[2])
        result["title"] = self.title(result, text_pages)
        return {"schema": SCHEMA_VERSION, "fields": {k: v.as_dict() for k, v in result.items()}}

    @staticmethod
    def title(result: dict, pages: list[str]) -> Found:
        """A short suggested title: the type and supplier, or the first line of the text."""
        doc = result["doc_type"].value
        sup = result["supplier"].value
        if doc and sup:
            return Found(f"{sup} {doc}", "low")
        for text in pages:
            line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            if line:
                return Found(line[:80], "low")
        return Found()


def claude_available() -> bool:
    """Claude can be chosen only when the server has an API key (kept in the environment, never in code)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get_extractor(name: str = "mock") -> Extractor:
    """The configured extractor (only the mock until M5)."""
    if name == "mock":
        return MockExtractor()
    raise ValueError(f"Unknown extractor {name!r}")


# ---- validation: whatever an extractor returns is checked before it is stored ----------------------
def validate_proposal(proposal: dict) -> dict:
    """Keep only known fields with the right shapes; anything odd becomes null (never a silent repair)."""
    if not isinstance(proposal, dict) or proposal.get("schema") != SCHEMA_VERSION:
        raise ValueError("Unexpected proposal format")
    fields_in = proposal.get("fields") or {}
    clean = {}
    for name in FIELDS:
        f = fields_in.get(name) or {}
        value = f.get("value")
        if value is not None and not isinstance(value, str):
            value = None
        if value is not None:
            value = value.strip()[:200] or None
        if name in ("due_date", "expiry_date") and value is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            value = None
        if name == "doc_type" and value not in (*DOC_TYPES, None):
            value = None
        if name == "amount" and value is not None and not re.fullmatch(r"\d{1,12}(\.\d{1,2})?", value):
            value = None
        if name == "currency" and value is not None and not re.fullmatch(r"[A-Z]{3}", value):
            value = None
        evidence = f.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("snippet"), str):
            page = evidence.get("page") if isinstance(evidence.get("page"), int) else None
            evidence = {"page": page, "snippet": evidence["snippet"][:SNIPPET_CHARS]}
        else:
            evidence = None
        item = {
            "value": value,
            "confidence": f.get("confidence") if f.get("confidence") in ("high", "medium", "low") else "low",
            "evidence": evidence,
        }
        if name in ("due_date", "expiry_date"):
            options = [o for o in (f.get("options") or []) if isinstance(o, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", o)]
            item["date_kind"] = f.get("date_kind") if f.get("date_kind") in ("explicit", "ambiguous") else None
            item["options"] = options[:2]
        clean[name] = item
    return {"schema": SCHEMA_VERSION, "fields": clean}
