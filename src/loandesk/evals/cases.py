"""Eval case format (YAML). One case is one conversation of one or more borrower turns.

    - id: emi-001
      phone: "+919000000001"
      turns: ["When is my next EMI due?"]
      expect:
        intent: emi_due_date          # first turn's intent; omit to skip
        tools: [get_loan_account]     # must appear in this order (other calls allowed between)
        forbidden_tools: []           # must not appear at all
        handoff: false                # omit to skip
        must_include: []              # regexes the final reply must match
        must_not_include: []          # regexes the final reply must not match
        redacted: []                  # PII labels that must have been removed, e.g. [AADHAAR]
        faithful: true                # judge the final reply (default true)
      tags: [en]
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from loandesk.config import ROOT

CASES_DIR = ROOT / "evals" / "cases"
SUITES = {"seed": ["seed.yaml"], "all": ["seed.yaml", "extended.yaml"]}


class Expect(BaseModel):
    intent: str | None = None
    tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    handoff: bool | None = None
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    redacted: list[str] = Field(default_factory=list)
    faithful: bool = True


class Case(BaseModel):
    id: str
    phone: str
    turns: list[str]
    expect: Expect = Field(default_factory=Expect)
    tags: list[str] = Field(default_factory=list)


def load_cases(suite: str = "seed", directory: Path = CASES_DIR) -> list[Case]:
    files = SUITES.get(suite, [suite if suite.endswith(".yaml") else f"{suite}.yaml"])
    cases: list[Case] = []
    for name in files:
        cases += [Case.model_validate(c) for c in yaml.safe_load((directory / name).read_text())]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids")
    return cases
