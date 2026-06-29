# ABDA-NL Makefile

PY := python3
PORT ?= 8000
HOST ?= 127.0.0.1
LLM ?= 0
BACKEND ?= claude
MODEL ?=

.PHONY: help install run run-basic run-qwen run-llama run-phi prepare-scenario test clean

help:
	@echo "Targets:"
	@echo "  install           Install Python dependencies via pip"
	@echo "  run               Start the app with LLM features (Claude API, requires ANTHROPIC_API_KEY)"
	@echo "  run-basic         Start the app without LLM features (no API key needed)"
	@echo "  run-qwen          Start with local Ollama backend, Qwen 3 8B"
	@echo "  run-llama         Start with local Ollama backend, Llama 3.1 8B"
	@echo "  run-phi           Start with local Ollama backend, Phi-4-mini 3.8B"
	@echo "  prepare-scenario  Build corpus_summary.yaml for a scenario (S=examples/<name>/)"
	@echo "  test              Run the pytest suite"
	@echo "  clean             Remove Python bytecode caches"

install:
	$(PY) -m pip install -r requirements.txt

# Internal target used by the run-* variants.
_serve:
	ABDA_ENABLE_LLM=$(LLM) ABDA_LLM_BACKEND=$(BACKEND) ABDA_OLLAMA_MODEL=$(MODEL) \
	    $(PY) -m uvicorn app.api.main:app --host $(HOST) --port $(PORT) --reload

run: LLM := 1
run: BACKEND := claude
run: _serve

run-basic: LLM := 0
run-basic: _serve

# Local Ollama backends. Each requires Ollama running (default
# http://localhost:11434) and the named model pulled. No Claude API
# key needed.

run-qwen: LLM := 1
run-qwen: BACKEND := ollama
run-qwen: MODEL := qwen3:8b
run-qwen: _serve

run-llama: LLM := 1
run-llama: BACKEND := ollama
run-llama: MODEL := llama3.1:8b
run-llama: _serve

run-phi: LLM := 1
run-phi: BACKEND := ollama
run-phi: MODEL := phi4-mini:3.8b
run-phi: _serve

prepare-scenario:
	@if [ -z "$(S)" ]; then echo "Usage: make prepare-scenario S=examples/<scenario>/"; exit 2; fi
	$(PY) -m app.cli.prepare_scenario $(S)

test:
	$(PY) -m pytest tests/ -q

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
