"""Redact personal data before any text reaches a model or a log (DPDP; OWASP LLM02).

Phone numbers and emails become placeholders such as [PHONE_1]. The real values stay
in a per-conversation vault so that update_contact can still use them; the model only
ever sees the placeholder. Aadhaar, PAN and bank account numbers are dropped outright:
no tool needs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PATTERNS: list[tuple[str, re.Pattern, bool]] = [
    # (label, pattern, keep the value in the vault?)
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), True),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"), False),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), False),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), True),
    ("ACCOUNT", re.compile(r"(?<!\d)\d{9,18}(?!\d)"), False),
]
PLACEHOLDER = re.compile(r"\[(EMAIL|PHONE|AADHAAR|PAN|ACCOUNT)_(\d+)\]")


@dataclass
class Vault:
    values: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def put(self, label: str, value: str, keep: bool) -> str:
        for key, existing in self.values.items():
            if existing == value:
                return key
        n = self.counters.get(label, 0) + 1
        self.counters[label] = n
        key = f"[{label}_{n}]"
        if keep:
            self.values[key] = value
        return key

    def resolve(self, text: str) -> str:
        """Swap placeholders back to real values. Used only inside tool execution."""
        return PLACEHOLDER.sub(lambda m: self.values.get(m.group(0), m.group(0)), text)

    def mask(self, text: str) -> str:
        """Swap placeholders for a masked form, for text shown to the borrower."""
        def _mask(m: re.Match) -> str:
            value = self.values.get(m.group(0))
            if value is None:
                return "[removed]"
            if m.group(1) == "PHONE":
                return "number ending " + re.sub(r"\D", "", value)[-4:]
            user, _, domain = value.partition("@")
            return f"{user[:1]}***@{domain}"
        return PLACEHOLDER.sub(_mask, text)

    def to_dict(self) -> dict:
        return {"values": self.values, "counters": self.counters}

    @classmethod
    def from_dict(cls, data: dict | None) -> Vault:
        data = data or {}
        return cls(values=dict(data.get("values", {})), counters=dict(data.get("counters", {})))


def redact(text: str, vault: Vault) -> tuple[str, list[str]]:
    found: list[str] = []
    for label, pattern, keep in PATTERNS:
        def _sub(m: re.Match, label=label, keep=keep) -> str:
            found.append(label)
            return vault.put(label, m.group(0), keep)
        text = pattern.sub(_sub, text)
    return text, found


def contains_pii(text: str) -> list[str]:
    return [label for label, pattern, _ in PATTERNS if label != "EMAIL" and pattern.search(text)]
