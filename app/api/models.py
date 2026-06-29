"""Pydantic request/response models for the ABDA-NL HTTP API.

Built for Pydantic v1 (the version that ships with the apt FastAPI 0.101).

Op shapes are a discriminated union on `op`; Pydantic rejects unknown
op kinds at the HTTP boundary. Payload dicts (`fact`, `assumption`,
`rule`) are passed through as free-form dicts and validated downstream
by `app.scenario.diff_ops` against the scenario JSON schema -- one
source of truth for payload shape.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import Annotated


# --- Op models (one per kind; discriminated on "op") ---


class _OpBase(BaseModel):
    class Config:
        extra = "forbid"


class ToggleAssumptionOp(_OpBase):
    op: Literal["toggle-assumption"]
    id: str


class ToggleRuleOp(_OpBase):
    op: Literal["toggle-rule"]
    id: str


class _NewPremiseNote(BaseModel):
    """NL description for a premise not yet in the scenario."""
    id: str
    description: str

    class Config:
        extra = "forbid"


class ModifyRuleOp(_OpBase):
    op: Literal["modify-rule"]
    id: str
    rule: dict
    new_premise_notes: Optional[List[_NewPremiseNote]] = None


class AddRuleOp(_OpBase):
    op: Literal["add-rule"]
    id: str
    rule: dict
    new_premise_notes: Optional[List[_NewPremiseNote]] = None


class RemoveRuleOp(_OpBase):
    op: Literal["remove-rule"]
    id: str


class SetBlockOp(_OpBase):
    op: Literal["set-block"]
    target: Literal["rule", "assumption"]
    id: str
    block: int


class AddFactOp(_OpBase):
    op: Literal["add-fact"]
    id: str
    fact: dict


class RemoveFactOp(_OpBase):
    op: Literal["remove-fact"]
    id: str


class AddAssumptionOp(_OpBase):
    op: Literal["add-assumption"]
    id: str
    assumption: dict


class RemoveAssumptionOp(_OpBase):
    op: Literal["remove-assumption"]
    id: str


DiffOp = Annotated[
    Union[
        ToggleAssumptionOp,
        ToggleRuleOp,
        ModifyRuleOp,
        AddRuleOp,
        RemoveRuleOp,
        SetBlockOp,
        AddFactOp,
        RemoveFactOp,
        AddAssumptionOp,
        RemoveAssumptionOp,
    ],
    Field(discriminator="op"),
]


# --- Request / response envelopes ---


class StateRequest(BaseModel):
    scenario_id: str
    diff_ops: List[DiffOp] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class StateResponse(BaseModel):
    """Bundled state. Returned by both `GET /scenarios/{id}`
    (baseline, zero ops) and `POST /state` (after applying ops).
    """
    scenario: dict
    af: dict

    class Config:
        extra = "forbid"


class ScenarioListItem(BaseModel):
    id: str
    title: str
    description: str = ""


class ScenarioListResponse(BaseModel):
    scenarios: List[ScenarioListItem]


class ConfigResponse(BaseModel):
    llm_enabled: bool


# --- Chat ---


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

    class Config:
        extra = "forbid"


class ChatRequest(BaseModel):
    scenario_id: str
    diff_ops: List[DiffOp] = Field(default_factory=list)
    messages: List[ChatMessage]

    class Config:
        extra = "forbid"


class ChatUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class ChatResponse(BaseModel):
    message: str
    stop_reason: str
    model: str
    usage: ChatUsage
    latency_ms: int
    retried: bool = False

    class Config:
        extra = "forbid"


# --- Propose ---


class ProposeRequest(BaseModel):
    scenario_id: str
    diff_ops: List[DiffOp] = Field(default_factory=list)
    task: Literal["add-rule", "modify-rule", "add-fact", "add-assumption"]
    instruction: str
    existing_id: Optional[str] = None  # required when task == "modify-rule"

    class Config:
        extra = "forbid"


class ReviewIssueModel(BaseModel):
    severity: Literal["blocker", "warning", "note"]
    message: str

    class Config:
        extra = "forbid"


# --- Save as new scenario ---


class SaveScenarioRequest(BaseModel):
    source_id: str
    diff_ops: List[DiffOp] = Field(default_factory=list)
    save_as_id: str
    title: str
    overwrite: bool = False

    class Config:
        extra = "forbid"


class SaveScenarioResponse(BaseModel):
    """Response after a successful save. Bundled state so the UI can
    pivot to the saved scenario without a follow-up fetch.
    """
    id: str
    title: str
    scenario: dict
    af: dict

    class Config:
        extra = "forbid"


class ProposeResponse(BaseModel):
    op: dict  # a ready-to-apply diff_op
    stop_reason: str
    model: str
    usage: ChatUsage
    latency_ms: int
    # Number of Proposer attempts it took to pass deterministic
    # validation.  1 = no retry; 2-3 = Validator flagged early
    # attempt(s) and the Proposer corrected on retry.
    proposer_attempts: int = 1
    # Whether the LLM Reviewer was run on this op. False for trivial
    # edits (add-fact / add-assumption) where the Reviewer is skipped.
    reviewed: bool = False
    # Advisory semantic issues from the Reviewer. Never blocks; the UI
    # surfaces them alongside the op and the user decides whether to
    # Apply, Refine, or Cancel.
    review_issues: List[ReviewIssueModel] = Field(default_factory=list)

    class Config:
        extra = "forbid"


# --- Error envelope ---


class ErrorDetail(BaseModel):
    code: str
    path: str = "<root>"
    message: str


class ErrorResponse(BaseModel):
    errors: List[ErrorDetail]
