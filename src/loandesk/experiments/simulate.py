"""Simulated traffic for the dashboard: N one-turn conversations from the intent map's
example messages, sent from random synthetic borrowers. Logged with source="simulate"."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor

import yaml

from loandesk.agent.graph import new_conversation_id
from loandesk.config import ROOT
from loandesk.db.seed import N_BORROWERS, phone_for
from loandesk.wiring import App

MESSAGES = ROOT / "evals" / "messages.yaml"


def sample_messages(n: int, seed: int = 7) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    by_intent: dict[str, list[str]] = yaml.safe_load(MESSAGES.read_text())
    weights = {"emi_due_date": 0.35, "failed_payment": 0.2, "foreclosure_quote": 0.1,
               "contact_update": 0.05, "complaint": 0.1, "policy_question": 0.15,
               "out_of_scope": 0.05}
    intents = rng.choices(list(weights), weights=list(weights.values()), k=n)
    return [(phone_for(rng.randint(1, N_BORROWERS)), rng.choice(by_intent[i])) for i in intents]


def simulate(app: App, n: int, model: str | None, workers: int = 6, on_turn=None) -> None:
    models = {"router": model, "responder": model} if model else None

    def one(item):
        phone, text = item
        return app.agent.turn(phone, text, conversation_id=new_conversation_id("sim"),
                              models=models, source="simulate")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(one, sample_messages(n)):
            if on_turn:
                on_turn(result)
