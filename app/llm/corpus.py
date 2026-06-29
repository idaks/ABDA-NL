"""Corpus-block builder for the chat system prompt.

Resolution order:

1. If `corpus_summary.yaml` exists in the scenario directory, render
   its hand-curated passages.
2. Else, if the raw corpus fits within the token budget, concatenate
   every file's text verbatim.
3. Else, raise — the scenario author needs to create
   `corpus_summary.yaml`.

PDFs in the raw-corpus path are extracted via `pdftotext`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

DEFAULT_BUDGET_TOKENS = 6000
_CHARS_PER_TOKEN = 4


class CorpusLoadError(Exception):
    """Raised when the corpus cannot be assembled for the chat
    prompt."""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _read_corpus_file(path: Path) -> str:
    if not path.is_file():
        raise CorpusLoadError(f"missing corpus file: {path}")
    if path.suffix.lower() == ".pdf":
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise CorpusLoadError(
                "pdftotext not found; install poppler-utils or add a .txt variant"
            )
        except subprocess.CalledProcessError as e:
            raise CorpusLoadError(f"pdftotext failed on {path}: {e.stderr}")
        return result.stdout
    return path.read_text(encoding="utf-8", errors="replace")


def _render_yaml_corpus(data: dict, scenario_title: str) -> str:
    parts = [f"# Corpus Snippets — {scenario_title}\n"]
    for entry in data.get("sources") or []:
        fname = entry.get("filename")
        if not fname:
            continue
        parts.append(f"## {fname}\n")
        for passage in entry.get("passages") or []:
            # Escape internal double-quotes by using the passage verbatim in a
            # blockquote — the chat prompt doesn't need strict quote delimiters.
            parts.append(f"> {passage}\n")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _render_concat_corpus(scenario_title: str, raw_texts: dict[str, str]) -> str:
    parts = [
        f"# Corpus (full text) — {scenario_title}",
        "<!-- Raw corpus fits within the token budget; verbatim passthrough. -->",
        "",
    ]
    for fname, text in raw_texts.items():
        parts.append(f"## {fname}\n")
        parts.append("<source>")
        parts.append(text.rstrip())
        parts.append("</source>")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_corpus_block(
    scenario_dir: Path,
    corpus_files: list[str],
    scenario_title: str,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> str:
    """Return the corpus section of the chat system prompt.

    Uses a hand-curated `corpus_summary.yaml` if present; otherwise
    falls back to runtime concatenation of the raw corpus. Raises
    `CorpusLoadError` if no yaml exists and the raw corpus exceeds the
    budget.
    """
    yaml_path = scenario_dir / "corpus_summary.yaml"
    if yaml_path.is_file():
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise CorpusLoadError(f"invalid YAML in {yaml_path.name}: {e}") from e
        return _render_yaml_corpus(data, scenario_title)

    raw_texts = {}
    for fname in corpus_files:
        raw_texts[fname] = _read_corpus_file(scenario_dir / "corpus" / fname)

    total_tokens = sum(_estimate_tokens(t) for t in raw_texts.values())
    if total_tokens > budget_tokens:
        raise CorpusLoadError(
            f"raw corpus is ~{total_tokens} tokens, exceeding the "
            f"{budget_tokens}-token budget; create corpus_summary.yaml to curate"
        )
    return _render_concat_corpus(scenario_title, raw_texts)
