"""Prompt template loader.

Prompts live under `app/prompts/<name>.md` and use standard Python
`str.format`-style `{var}` placeholders. Literal braces in prompt
bodies should be doubled (`{{` / `}}`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=64)
def _read_template(name: str) -> str:
    path = PROMPTS_ROOT / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"prompt template not found: {path} "
            f"(expected under {PROMPTS_ROOT})"
        )
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, **variables: str) -> str:
    """Load `app/prompts/<name>.md` and substitute `{var}`
    placeholders."""
    template = _read_template(name)
    if not variables:
        return template
    return template.format(**variables)
