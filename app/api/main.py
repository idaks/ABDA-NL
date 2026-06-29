"""FastAPI backend for ABDA-NL.

Endpoints:
  GET  /scenarios            -- list scenario ids / titles / descriptions
  GET  /scenarios/{id}       -- baseline bundled state (zero ops applied)
  POST /state                -- apply diff_ops against a baseline, return bundled state
  POST /chat                 -- corpus-grounded chat (LLM mode only)
  POST /propose              -- natural-language rule authoring (LLM mode only)
  POST /scenarios            -- save a modified scenario

State bundle shape (returned by both GET /scenarios/{id} and POST /state):
  {
    "scenario": {...mirror of scenario.yaml shape...},
    "af": {arguments, attacks, labels_by_proposition}
  }
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

from app.abda_bridge import (
    ArgumentationGraph,
    build_arguments,
    build_attacks,
    init_engine,
)
from app.api.errors import ScenarioNotFoundError, register_exception_handlers
from app.api.models import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ConfigResponse,
    ProposeRequest,
    ProposeResponse,
    SaveScenarioRequest,
    SaveScenarioResponse,
    ScenarioListItem,
    ScenarioListResponse,
    StateRequest,
    StateResponse,
)


def _read_llm_flag() -> bool:
    return os.getenv("ABDA_ENABLE_LLM", "0").strip().lower() in ("1", "true", "yes", "on")


ENABLE_LLM = _read_llm_flag()


def _preflight_llm_config(enable_llm: bool) -> None:
    """Validate that the active backend's prerequisites are satisfied."""
    if not enable_llm:
        return
    from app.llm import resolve_backend

    backend = resolve_backend()
    if backend == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ABDA_ENABLE_LLM=1 and ABDA_LLM_BACKEND=claude but "
            "ANTHROPIC_API_KEY is not set. Export the key, switch to "
            "a local backend (ABDA_LLM_BACKEND=ollama), or disable "
            "LLM features (ABDA_ENABLE_LLM=0). See README.md."
        )


# Fail fast at import time so the error is loud and local rather than
# surfacing later as a failing chat request.
_preflight_llm_config(ENABLE_LLM)
from app.scenario.diff_ops import apply as apply_ops
from app.scenario.loader import load_scenario, scenario_to_rule_collection
from app.scenario.save import save_scenario
from app.scenario.serialize import scenario_to_dict, serialize_af

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent / "examples"
STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"

# Curated scenario ordering for the dropdown. Unknown ids sort to the end
# in alphabetical order.
SCENARIO_ORDER = [
    "popov_v_hayashi",
    "fire_prevention",
    "medical_ppi",
    "nba_rebuild",
    "fried_chicken_v1",
    "fried_chicken_v2",
]


def _scenario_sort_key(scenario_id: str) -> tuple[int, str]:
    try:
        return (SCENARIO_ORDER.index(scenario_id), scenario_id)
    except ValueError:
        return (len(SCENARIO_ORDER), scenario_id)


app = FastAPI(
    title="ABDA-NL",
    description="Natural-language scenario explorer for argument-based reasoning.",
    version="1.0.0",
)

register_exception_handlers(app)


# Static assets get `Cache-Control: no-cache, must-revalidate` so the
# browser revalidates on each load (unchanged files still return 304).
_STATIC_SUFFIXES = (".html", ".js", ".css", ".map", ".ico", ".svg", ".png")


def _is_static_path(path: str) -> bool:
    return (
        path == "/"
        or path.endswith(_STATIC_SUFFIXES)
    )


@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    if _is_static_path(request.url.path):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.on_event("startup")
def _on_startup() -> None:
    init_engine()


def _load_baseline(scenario_id: str):
    scenario_dir = EXAMPLES_ROOT / scenario_id
    scenario_path = scenario_dir / "scenario.yaml"
    if not scenario_path.is_file():
        raise ScenarioNotFoundError(f"scenario not found: {scenario_id!r}")
    return load_scenario(scenario_path)


def _compute_state_bundle(scenario) -> dict[str, Any]:
    rc = scenario_to_rule_collection(scenario)
    arguments = build_arguments(rc.get_all_rules())
    attacks = build_attacks(arguments)
    graph = ArgumentationGraph(arguments, attacks)
    labelling = graph.get_grounded_labelling()
    return {
        "scenario": scenario_to_dict(scenario),
        "af": serialize_af(scenario, arguments, attacks, labelling),
    }


@app.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(llm_enabled=_read_llm_flag())


@app.get("/scenarios", response_model=ScenarioListResponse)
def list_scenarios() -> ScenarioListResponse:
    items: list[ScenarioListItem] = []
    if EXAMPLES_ROOT.is_dir():
        children = [c for c in EXAMPLES_ROOT.iterdir() if c.is_dir()]
        children.sort(key=lambda c: _scenario_sort_key(c.name))
        for child in children:
            scenario_path = child / "scenario.yaml"
            if not scenario_path.is_file():
                continue
            try:
                scenario = load_scenario(scenario_path)
            except Exception as exc:  # noqa: BLE001
                # Broken scenarios are skipped from the listing, but we
                # log the reason so it's visible in server output. The
                # scenario still surfaces (with its full error shape) on
                # explicit GET /scenarios/{id}.
                log.warning("skipping broken scenario %s: %s", child.name, exc)
                continue
            items.append(
                ScenarioListItem(
                    id=child.name,
                    title=scenario.title,
                    description=scenario.description,
                )
            )
    return ScenarioListResponse(scenarios=items)


@app.get("/scenarios/{scenario_id}", response_model=StateResponse)
def get_scenario(scenario_id: str) -> StateResponse:
    scenario = _load_baseline(scenario_id)
    return StateResponse(**_compute_state_bundle(scenario))


@app.post("/state", response_model=StateResponse)
def post_state(request: StateRequest) -> StateResponse:
    baseline = _load_baseline(request.scenario_id)
    ops = [op.dict() for op in request.diff_ops]
    effective = apply_ops(baseline, ops)
    return StateResponse(**_compute_state_bundle(effective))


# Lazy-imported on first request so non-LLM mode never loads the
# anthropic SDK or requires an API key.
_llm_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        if not ENABLE_LLM:
            raise RuntimeError("chat is disabled; restart with ABDA_ENABLE_LLM=1")
        from app.llm import make_llm_client

        _llm_client = make_llm_client()
    return _llm_client


@app.post("/chat", response_model=ChatResponse)
def post_chat(request: ChatRequest) -> ChatResponse:
    from app.llm.chat_service import run_turn

    if not ENABLE_LLM:
        raise HTTPException(
            status_code=503,
            detail="chat is disabled; restart with ABDA_ENABLE_LLM=1",
        )

    scenario_dir = EXAMPLES_ROOT / request.scenario_id
    baseline = _load_baseline(request.scenario_id)
    ops = [op.dict() for op in request.diff_ops]
    scenario = apply_ops(baseline, ops)
    bundle = _compute_state_bundle(scenario)

    try:
        result = run_turn(
            scenario,
            bundle["af"],
            ops,
            [m.dict() for m in request.messages],
            scenario_dir=scenario_dir,
            client=_get_llm_client(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "chat_turn scenario=%s msgs=%d validator_flags=%d retried=%s stop=%s",
        request.scenario_id,
        len(request.messages),
        len(result.validator_flags),
        result.retried,
        result.stop_reason,
    )

    return ChatResponse(
        message=result.text,
        stop_reason=result.stop_reason,
        model=result.model,
        usage=ChatUsage(**result.usage),
        latency_ms=result.latency_ms,
        retried=result.retried,
    )


@app.post("/propose", response_model=ProposeResponse)
def post_propose(request: ProposeRequest) -> ProposeResponse:
    from app.llm.edit_service import ProposerRetryExhausted, run_propose

    if not ENABLE_LLM:
        raise HTTPException(
            status_code=503,
            detail="edit flows are disabled; restart with ABDA_ENABLE_LLM=1",
        )
    if request.task == "modify-rule" and not request.existing_id:
        raise HTTPException(
            status_code=400,
            detail="modify-rule requires `existing_id`",
        )

    scenario_dir = EXAMPLES_ROOT / request.scenario_id
    baseline = _load_baseline(request.scenario_id)
    ops = [op.dict() for op in request.diff_ops]
    scenario = apply_ops(baseline, ops)
    bundle = _compute_state_bundle(scenario)

    try:
        result = run_propose(
            scenario,
            bundle["af"],
            ops,
            task=request.task,
            instruction=request.instruction,
            existing_id=request.existing_id,
            scenario_dir=scenario_dir,
            client=_get_llm_client(),
        )
    except ProposerRetryExhausted as e:
        # Couldn't produce a valid ASPIC- statement after retries;
        # ask the user to rephrase.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "proposer_retry_exhausted",
                "message": str(e),
                "issues": [i.to_dict() for i in e.last_issues],
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "propose_turn scenario=%s task=%s op_id=%s attempts=%d reviewed=%s review_issues=%d",
        request.scenario_id,
        request.task,
        result.op.get("id"),
        result.proposer_attempts,
        result.reviewed,
        len(result.review_issues),
    )

    return ProposeResponse(
        op=result.op,
        stop_reason=result.stop_reason,
        model=result.model,
        usage=ChatUsage(**result.usage),
        latency_ms=result.latency_ms,
        proposer_attempts=result.proposer_attempts,
        reviewed=result.reviewed,
        review_issues=[i.to_dict() for i in result.review_issues],
    )


@app.post("/scenarios", response_model=SaveScenarioResponse, status_code=201)
def post_save_scenario(request: SaveScenarioRequest) -> SaveScenarioResponse:
    """Save the current (baseline + diff_ops) state as a new scenario.

    Writes `examples/<save_as_id>/` with a diff-applied
    `scenario.yaml` plus a copy of the baseline's corpus
    artefacts. Post-write, the server reloads and rebuilds the
    scenario to catch any inconsistency; on verification failure the
    temp dir is cleaned up and a 500 is returned.

    Response carries the fresh bundled state so the UI can pivot to
    the saved scenario without a second fetch.

    Error codes:
      400 invalid_scenario_id -- save_as_id fails the identifier pattern
      404 scenario_not_found -- source_id doesn't exist under examples/
      409 scenario_id_collision -- target exists and overwrite=false
      500 save_verification_failed -- post-write rebuild failed (rare)
    """
    baseline = _load_baseline(request.source_id)
    ops = [op.dict() for op in request.diff_ops]
    effective = apply_ops(baseline, ops)

    target = save_scenario(
        effective=effective,
        title=request.title,
        save_as_id=request.save_as_id,
        baseline_dir=EXAMPLES_ROOT / request.source_id,
        examples_root=EXAMPLES_ROOT,
        overwrite=request.overwrite,
    )
    log.info(
        "scenario_saved source=%s saved_as=%s overwrite=%s",
        request.source_id,
        request.save_as_id,
        request.overwrite,
    )

    # Reload from disk and return the fresh bundle so the UI can pivot.
    saved = load_scenario(target / "scenario.yaml")
    bundle = _compute_state_bundle(saved)
    return SaveScenarioResponse(
        id=request.save_as_id,
        title=saved.title,
        scenario=bundle["scenario"],
        af=bundle["af"],
    )


# The frontend mount is a catch-all at "/" and must stay last — any
# endpoint registered after it becomes silently unreachable.
if STATIC_ROOT.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_ROOT), html=True), name="static")
