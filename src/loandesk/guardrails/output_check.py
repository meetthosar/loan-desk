"""Checks on the reply before it is sent. Deterministic, so they cost nothing and never drift.

1. No fee-waiver promises (the agent has no authority to waive anything).
2. Every rupee amount in the reply appears in a tool result or a retrieved policy page.
3. No Aadhaar, PAN, phone or account numbers in the reply.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loandesk.guardrails.pii import contains_pii

WAIVER = re.compile(
    # "maaf kijiye" is just "sorry"; "maaf kar denge / maaf ho jayega" is a waiver promise.
    r"\b(waive[ds]?|waiving|waiver|write[- ]?off|written off|maaf (kar|ho)\w*|"
    r"(cancel|remove|reverse)(led|d)? (the |your |all )?(interest|charges?|penalt(y|ies)|fees?))\b",
    re.I,
)
NEGATION = re.compile(
    r"\b(can ?not|can't|cannot|unable|not able|no authority|not possible|don't|do not|won't|"
    r"will not|isn't|is not|nahi|nahin|not)\b",
    re.I,
)
AMOUNT = re.compile(r"(?:₹|\bRs\.?|\bINR)\s?(\d[\d,]*(?:\.\d{1,2})?)", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SENTENCE = re.compile(r"(?<=[.!?।])\s+|\n+")


@dataclass
class OutputVerdict:
    ok: bool
    flags: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in NUMBER.findall(text):
        try:
            out.add(round(float(raw.replace(",", "")), 2))
        except ValueError:
            pass
    return out


def check_reply(reply: str, tool_results: list[dict], policy_texts: list[str]) -> OutputVerdict:
    verdict = OutputVerdict(ok=True)

    for sentence in SENTENCE.split(reply):
        if WAIVER.search(sentence) and not NEGATION.search(sentence):
            verdict.flags.append("fee_waiver_promise")
            verdict.detail.append(sentence.strip())
            break

    grounded = _numbers(json.dumps(tool_results, default=str)) | _numbers(" ".join(policy_texts))
    for raw in AMOUNT.findall(reply):
        value = round(float(raw.replace(",", "")), 2)
        if not any(abs(value - g) <= 1.0 for g in grounded):
            verdict.flags.append("ungrounded_amount")
            verdict.detail.append(f"₹{raw}")

    leaked = contains_pii(reply)
    if leaked:
        verdict.flags.append("pii_in_reply")
        verdict.detail += leaked

    verdict.ok = not verdict.flags
    return verdict
