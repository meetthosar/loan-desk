"""Check the LLM judge against your own labels before trusting it.

Label cases by hand in evals/labels.yaml (`- {id: emi-001, faithful: true}`), then compare
with the judge's verdicts in an eval report. No model calls: it reads the report.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def calibrate(report_json: Path, labels_yaml: Path) -> dict:
    report = json.loads(report_json.read_text())
    judged = {c["id"]: c["scores"].get("faithful") for c in report["cases"]}
    labels = {row["id"]: bool(row["faithful"]) for row in yaml.safe_load(labels_yaml.read_text())
              or []}
    pairs = [(labels[i], judged[i]) for i in labels if judged.get(i) is not None]
    if not pairs:
        return {"labelled_and_judged": 0}
    tp = sum(1 for h, j in pairs if not h and not j)  # both say unfaithful
    fp = sum(1 for h, j in pairs if h and not j)  # judge flags a reply you passed
    fn = sum(1 for h, j in pairs if not h and j)  # judge misses one you failed
    return {
        "labelled_and_judged": len(pairs),
        "agreement": round(sum(h == j for h, j in pairs) / len(pairs), 4),
        "unfaithful_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "unfaithful_recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "disagreements": [i for i in labels if judged.get(i) is not None
                          and labels[i] != judged[i]],
    }
