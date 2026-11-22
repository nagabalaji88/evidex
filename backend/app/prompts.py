"""Prompt files live in backend/prompts/ and are versioned with the code (spec section 3).
Placeholders are {name}; they are replaced literally, so JSON braces in a prompt are safe."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render_prompt(name: str, **values: str) -> str:
    text = load_prompt(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
