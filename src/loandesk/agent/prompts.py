from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache
def load_prompt(prompts_dir: Path, name: str) -> str:
    path = prompts_dir / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt {path} not found. Check LOANDESK_PROMPT_VERSION.")
    return path.read_text().strip()


HANDOFF_REPLY = {
    "en": "I'm connecting you to a member of our team. Your reference number is {ticket}. "
          "They will reply here during working hours (Mon–Sat, 9:00–18:00).",
    "hinglish": "Main aapko hamari team se connect kar raha hoon. Aapka reference number "
                "{ticket} hai. Team working hours (Mon–Sat, 9:00–18:00) mein yahin reply karegi.",
    "hi": "मैं आपको हमारी टीम से जोड़ रहा हूँ। आपका रेफ़रेंस नंबर {ticket} है। टीम कामकाजी समय "
          "(सोम–शनि, 9:00–18:00) में यहीं जवाब देगी।",
}

FALLBACK_REPLY = {
    "en": "Sorry, I can't answer that safely right now. ",
    "hinglish": "Maaf kijiye, main abhi iska sahi jawab nahi de sakta. ",
    "hi": "क्षमा करें, मैं अभी इसका सही जवाब नहीं दे सकता। ",
}
