"""Integration tests for the FastAPI app.

Uses FastAPI's TestClient (starlette + httpx under the hood) — no real
server process. Covers the endpoints, the ten op kinds via POST /state,
structured error responses, and Popov baseline regression.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# --- GET /config ---


# --- static-asset cache headers ---


def test_static_index_sets_no_cache(client: TestClient):
    resp = client.get("/")
    # StaticFiles with html=True resolves "/" to index.html.
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache, must-revalidate"


def test_static_js_sets_no_cache(client: TestClient):
    resp = client.get("/app.js")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache, must-revalidate"


def test_static_css_sets_no_cache(client: TestClient):
    resp = client.get("/style.css")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache, must-revalidate"


def test_api_json_does_not_get_no_cache(client: TestClient):
    """/config returns JSON and must not receive the static-asset
    cache-control header -- API responses aren't browser-cached anyway,
    and pinning headers on them would be a surprise for future callers."""
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") != "no-cache, must-revalidate"


def test_config_llm_disabled_by_default(client: TestClient, monkeypatch):
    monkeypatch.delenv("ABDA_ENABLE_LLM", raising=False)
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json() == {"llm_enabled": False}


@pytest.mark.parametrize("truthy", ["1", "true", "True", "yes", "on"])
def test_config_llm_enabled_when_env_truthy(client: TestClient, monkeypatch, truthy):
    monkeypatch.setenv("ABDA_ENABLE_LLM", truthy)
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json() == {"llm_enabled": True}


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", ""])
def test_config_llm_disabled_when_env_falsy(client: TestClient, monkeypatch, falsy):
    monkeypatch.setenv("ABDA_ENABLE_LLM", falsy)
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json() == {"llm_enabled": False}


# --- GET /scenarios ---


def test_list_scenarios_returns_curated_first(client: TestClient):
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    ids = [s["id"] for s in body["scenarios"]]
    # Curated prefix: Popov, Prescribed Burn, PPI, NBA, Fried Chicken V1, V2.
    # Additional user-saved scenarios may trail; verify the curated
    # prefix order stays stable.
    curated = [
        "popov_v_hayashi",
        "fire_prevention",
        "medical_ppi",
        "nba_rebuild",
        "fried_chicken_v1",
        "fried_chicken",
    ]
    assert ids[: len(curated)] == curated
    for item in body["scenarios"]:
        assert item["title"]
        assert isinstance(item["description"], str)


# --- GET /scenarios/{id} ---


@pytest.mark.parametrize(
    "scenario_id",
    ["fire_prevention", "fried_chicken", "fried_chicken_v1", "medical_ppi", "nba_rebuild", "popov_v_hayashi"],
)
def test_get_each_scenario_returns_bundled_state(client: TestClient, scenario_id: str):
    resp = client.get(f"/scenarios/{scenario_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "scenario" in body
    assert "af" in body
    assert body["scenario"]["title"]
    assert "arguments" in body["af"]
    assert "attacks" in body["af"]
    assert "labels_by_proposition" in body["af"]


def test_get_unknown_scenario_returns_404_with_structured_error(client: TestClient):
    resp = client.get("/scenarios/does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "errors" in body
    assert body["errors"]
    err = body["errors"][0]
    assert err["code"] == "scenario_not_found"
    assert "does_not_exist" in err["message"]


def test_popov_matches_gold_snapshot(client: TestClient):
    """POST /state with empty ops on Popov must match the committed
    AF fixture (determinism regression check)."""
    gold = json.loads((FIXTURES / "popov_af.json").read_text())
    resp = client.post(
        "/state", json={"scenario_id": "popov_v_hayashi", "diff_ops": []}
    )
    assert resp.status_code == 200
    assert resp.json()["af"] == gold


# --- Scenario three-regime baseline + toggle regression guards ---


def _labels(resp) -> dict[str, str]:
    return resp.json()["af"]["labels_by_proposition"]


def test_medical_baseline_three_regimes(client: TestClient):
    """Medical baseline must simultaneously show one non-trivial accepted,
    one non-trivial rejected, and one non-trivial undecided key conclusion,
    per the scenario redesign constraints."""
    resp = client.get("/scenarios/medical_ppi")
    labels = _labels(resp)
    assert labels["be_indication"] == "accepted"      # regime 1
    assert labels["deprescribe_now"] == "rejected"    # regime 2
    assert labels["continue_ppi"] == "undecided"      # regime 3


def test_medical_pantoprazole_toggle_flips_cardiac_and_continue(client: TestClient):
    """Toggling ppi_is_panto undercuts the middle link of the cardiac chain,
    which flips cardiac_risk accepted→rejected and continue_ppi undec→accepted."""
    resp = client.post(
        "/state",
        json={
            "scenario_id": "medical_ppi",
            "diff_ops": [{"op": "toggle-assumption", "id": "ppi_is_panto"}],
        },
    )
    assert resp.status_code == 200
    labels = _labels(resp)
    assert labels["cardiac_risk"] == "rejected"
    assert labels["continue_ppi"] == "accepted"


def test_nba_baseline_three_regimes(client: TestClient):
    resp = client.get("/scenarios/nba_rebuild")
    labels = _labels(resp)
    assert labels["preserve_flex"] == "accepted"      # regime 1
    assert labels["tank"] == "rejected"               # regime 2
    assert labels["lottery_math"] == "undecided"      # regime 3


def test_nba_expansion_toggle_flips_tank_to_undec(client: TestClient):
    """Toggling expansion_pending activates the talent-dilution route into
    pick_premium, equalising strength with the development line and flipping
    tank from rejected to undecided."""
    resp = client.post(
        "/state",
        json={
            "scenario_id": "nba_rebuild",
            "diff_ops": [{"op": "toggle-assumption", "id": "expansion_pending"}],
        },
    )
    assert resp.status_code == 200
    labels = _labels(resp)
    assert labels["tank"] == "undecided"
    assert labels["pick_premium"] == "accepted"
    assert labels["talent_dilution"] == "accepted"


def test_forestry_baseline_three_regimes(client: TestClient):
    resp = client.get("/scenarios/fire_prevention")
    labels = _labels(resp)
    assert labels["legal_today"] == "accepted"        # regime 1
    assert labels["short_interval"] == "rejected"     # regime 2
    assert labels["conduct_burn"] == "undecided"      # regime 3


# --- POST /chat ---


class _FakeLLMClient:
    """Mimics the LLMClient protocol with canned responses for tests.

    ``complete_responses`` are consumed by ``complete()`` (chat); separate
    ``tool_responses`` are consumed by ``tool_call()`` (propose/review).
    Keeps the two streams independent so a test can stage both without
    worrying about call order.
    """

    def __init__(self, responses=None, *, tool_responses=None):
        from app.llm.client import LLMResponse  # noqa: F401 — imported for type reference

        self._responses = list(responses or [])
        self._tool_responses = list(tool_responses or [])
        self.calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def complete(self, *, system, messages, max_tokens, cache=True):
        self.calls.append({"system": system, "messages": list(messages), "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of canned responses")
        return self._responses.pop(0)

    def tool_call(self, *, system, messages, tool, max_tokens, cache=True):
        self.tool_calls.append({
            "system": system,
            "messages": list(messages),
            "tool": tool,
            "max_tokens": max_tokens,
        })
        if not self._tool_responses:
            raise AssertionError("FakeLLMClient ran out of canned tool responses")
        return self._tool_responses.pop(0)


def _llm_response(text: str, **overrides):
    from app.llm.client import LLMResponse

    base = {
        "text": text,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "latency_ms": 1234,
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return LLMResponse(**base)


def test_chat_disabled_returns_503(client: TestClient, monkeypatch):
    from app.api import main as main_module

    monkeypatch.setattr(main_module, "ENABLE_LLM", False)
    resp = client.post(
        "/chat",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 503
    assert "ABDA_ENABLE_LLM=1" in resp.json()["detail"]


def test_chat_happy_path_returns_message_and_usage(client: TestClient, monkeypatch):
    from app.api import main as main_module

    fake = _FakeLLMClient([_llm_response("The ball is undecided because both parties have equal claims.")])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/chat",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "messages": [{"role": "user", "content": "Why is the ball undecided?"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"].startswith("The ball is undecided")
    assert body["model"] == "claude-sonnet-4-6"
    assert body["retried"] is False
    assert body["stop_reason"] == "end_turn"
    assert body["usage"]["output_tokens"] == 50
    assert len(fake.calls) == 1
    # System prompt should contain the scenario title and some state.
    assert "Popov" in fake.calls[0]["system"] or "popov" in fake.calls[0]["system"]


def test_chat_retries_on_validator_flag(client: TestClient, monkeypatch):
    from app.api import main as main_module

    # First response invents a bogus corpus filename; second is clean.
    bad = "See [totally_made_up.txt] for details."
    good = "The scenario is balanced between Popov and Hayashi."
    fake = _FakeLLMClient([_llm_response(bad), _llm_response(good)])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/chat",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "messages": [{"role": "user", "content": "Explain."}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retried"] is True
    assert body["message"] == good
    # Two LLM calls total, and the second had the corrective feedback appended.
    assert len(fake.calls) == 2
    retry_last_user = fake.calls[1]["messages"][-1]
    assert retry_last_user["role"] == "user"
    assert "totally_made_up.txt" in retry_last_user["content"]
    # The retry must be framed as an automated validator check, not user
    # feedback, and must tell the model not to apologize or meta-comment
    # in its output. Without these the model leaks the retry mechanism
    # into the user-facing response ("You're right to flag that...").
    retry_text = retry_last_user["content"]
    assert "AUTOMATED VALIDATOR" in retry_text
    assert "not user feedback" in retry_text.lower()
    assert "do not apologize" in retry_text.lower()
    # Combined token usage.
    assert body["usage"]["output_tokens"] == 100


def test_chat_rejects_non_user_last_message(client: TestClient, monkeypatch):
    from app.api import main as main_module

    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", _FakeLLMClient([]))

    resp = client.post(
        "/chat",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        },
    )
    assert resp.status_code == 400
    assert "user message" in resp.json()["detail"]


def test_chat_uses_diff_ops_when_building_state(client: TestClient, monkeypatch):
    """After a toggle-assumption op, the state block should reflect the new state."""
    from app.api import main as main_module

    fake = _FakeLLMClient([_llm_response("ok")])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/chat",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [{"op": "toggle-assumption", "id": "equity_compromise_open"}],
            "messages": [{"role": "user", "content": "What changed?"}],
        },
    )
    assert resp.status_code == 200
    system_prompt = fake.calls[0]["system"]
    # The diff_ops summary should appear in the state block. Heading is
    # present-tense -- "Modifications from baseline scenario" -- not a
    # session timeline, to avoid the model narrating ops as user events.
    assert "Modifications from baseline scenario" in system_prompt
    assert "toggle-assumption" in system_prompt
    assert "equity_compromise_open" in system_prompt


# --- POST /propose ---


def _tool_response(tool_name: str, tool_input: dict, **overrides):
    from app.llm.client import ToolCallResponse

    base = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 800,
            "output_tokens": 100,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "latency_ms": 2100,
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return ToolCallResponse(**base)


def _review_response(issues: list[dict] | None = None):
    """Build a Reviewer tool response. Empty issues = clean review."""
    return _tool_response("review_edit", {"issues": list(issues or [])})


# --- POST /scenarios (save-as-new) ---


@pytest.fixture
def save_sandbox(tmp_path, monkeypatch):
    """Relocate EXAMPLES_ROOT to a tmp dir and seed it with a minimal Popov
    copy. Restores on teardown via monkeypatch.

    We don't want save tests writing into the real examples/ directory.
    """
    import shutil as _shutil
    from app.api import main as main_module

    real = main_module.EXAMPLES_ROOT
    sandbox = tmp_path / "examples"
    sandbox.mkdir()
    _shutil.copytree(real / "popov_v_hayashi", sandbox / "popov_v_hayashi")
    monkeypatch.setattr(main_module, "EXAMPLES_ROOT", sandbox)
    yield sandbox


def test_save_scenario_happy_path(client: TestClient, save_sandbox):
    resp = client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [],
            "save_as_id": "popov_user_copy",
            "title": "My Popov Copy",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "popov_user_copy"
    assert body["title"] == "My Popov Copy"
    # Bundled state fields present.
    assert "scenario" in body and "af" in body
    assert body["scenario"]["title"] == "My Popov Copy"
    # Saved directory is on disk.
    assert (save_sandbox / "popov_user_copy" / "scenario.yaml").is_file()
    assert (save_sandbox / "popov_user_copy" / "corpus").is_dir()


def test_save_scenario_with_diff_ops_applied(client: TestClient, save_sandbox):
    resp = client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [
                {"op": "toggle-assumption", "id": "equity_compromise_open"},
            ],
            "save_as_id": "popov_with_equity",
            "title": "Popov with equity on",
        },
    )
    assert resp.status_code == 201, resp.text
    # The saved scenario should show the equity_compromise_open assumption
    # as active (the toggle is present in the YAML).
    import yaml as _yaml
    saved = _yaml.safe_load(
        (save_sandbox / "popov_with_equity" / "scenario.yaml").read_text()
    )
    eq = saved["assumptions"]["equity_compromise_open"]
    # Baseline ships inactive; after the toggle it should be active=true.
    assert eq.get("active") is True


def test_save_scenario_collision_returns_409(client: TestClient, save_sandbox):
    """Second save with the same id and overwrite=false (default) fails."""
    payload = {
        "source_id": "popov_v_hayashi",
        "diff_ops": [],
        "save_as_id": "popov_dup",
        "title": "First",
    }
    first = client.post("/scenarios", json=payload)
    assert first.status_code == 201

    second = client.post("/scenarios", json=dict(payload, title="Second"))
    assert second.status_code == 409
    assert second.json()["errors"][0]["code"] == "scenario_id_collision"


def test_save_scenario_collision_with_overwrite_succeeds(
    client: TestClient, save_sandbox
):
    payload = {
        "source_id": "popov_v_hayashi",
        "diff_ops": [],
        "save_as_id": "popov_over",
        "title": "First",
    }
    assert client.post("/scenarios", json=payload).status_code == 201
    second = client.post(
        "/scenarios",
        json=dict(payload, title="Second", overwrite=True),
    )
    assert second.status_code == 201, second.text
    assert second.json()["title"] == "Second"


def test_save_scenario_invalid_id_returns_400(client: TestClient, save_sandbox):
    resp = client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [],
            "save_as_id": "has spaces",
            "title": "x",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "invalid_scenario_id"


def test_save_scenario_overwrite_source_succeeds(client: TestClient, save_sandbox):
    """Saving with save_as_id == source_id and overwrite=true updates the
    source scenario in place. expected_labels.yaml is preserved so the
    regression snapshot isn't silently wiped.
    """
    source = save_sandbox / "popov_v_hayashi"
    original_snapshot = (source / "expected_labels.yaml").read_bytes()

    resp = client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [
                {"op": "toggle-assumption", "id": "equity_compromise_open"},
            ],
            "save_as_id": "popov_v_hayashi",
            "title": "Popov (equity on, overwritten in place)",
            "overwrite": True,
        },
    )
    assert resp.status_code == 201, resp.text
    # Scenario yaml now reflects the edit; snapshot is untouched.
    import yaml as _yaml
    saved = _yaml.safe_load((source / "scenario.yaml").read_text())
    assert saved["assumptions"]["equity_compromise_open"].get("active") is True
    assert (source / "expected_labels.yaml").read_bytes() == original_snapshot


def test_save_scenario_overwrite_source_requires_overwrite_flag(
    client: TestClient, save_sandbox
):
    """Forgetting overwrite=true on a same-source save still produces a
    collision, same as any other same-id save.
    """
    resp = client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [],
            "save_as_id": "popov_v_hayashi",
            "title": "no overwrite flag",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["errors"][0]["code"] == "scenario_id_collision"


def test_save_scenario_unknown_source_returns_404(client: TestClient, save_sandbox):
    resp = client.post(
        "/scenarios",
        json={
            "source_id": "does_not_exist",
            "diff_ops": [],
            "save_as_id": "anywhere",
            "title": "x",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["errors"][0]["code"] == "scenario_not_found"


def test_save_scenario_shows_in_listing(client: TestClient, save_sandbox):
    client.post(
        "/scenarios",
        json={
            "source_id": "popov_v_hayashi",
            "diff_ops": [],
            "save_as_id": "saved_shows_up",
            "title": "Shows up in switcher",
        },
    )
    listing = client.get("/scenarios").json()
    ids = {s["id"] for s in listing["scenarios"]}
    assert "saved_shows_up" in ids


def test_propose_disabled_returns_503(client: TestClient, monkeypatch):
    from app.api import main as main_module

    monkeypatch.setattr(main_module, "ENABLE_LLM", False)
    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "add a rule that says X",
        },
    )
    assert resp.status_code == 503
    assert "ABDA_ENABLE_LLM=1" in resp.json()["detail"]


def test_propose_add_rule_happy_path(client: TestClient, monkeypatch):
    """One clean Proposer call + one Reviewer call with no issues."""
    from app.api import main as main_module

    tool_input = {
        "id": "pp_ball_ret",  # 11 chars, well under ceiling
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_legit_claim",
            "category": "test",
            "block": 1,
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", tool_input),
        _review_response([]),  # clean review
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a rule that says the pre-possessory interest supports the legitimate claim.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["op"]["op"] == "add-rule"
    assert body["op"]["id"] == "pp_ball_ret"
    assert body["op"]["rule"]["premises"] == ["popov_preposs_interest"]
    assert body["proposer_attempts"] == 1
    assert body["reviewed"] is True
    assert body["review_issues"] == []

    # Proposer then Reviewer: two tool calls.
    assert len(fake.tool_calls) == 2
    assert fake.tool_calls[0]["tool"]["name"] == "propose_add_rule"
    assert fake.tool_calls[1]["tool"]["name"] == "review_edit"


def test_propose_validator_retries_on_id_collision(client: TestClient, monkeypatch):
    """Validator flags a blocking issue (id_collision); Proposer retries.

    `unknown_premise` is advisory -- it does NOT trigger a retry. Use a
    blocking issue like `id_collision` to exercise the retry path.
    """
    from app.api import main as main_module

    first = {
        "id": "mc1",  # collides with existing rule id
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_qual_right",
        },
    }
    second = {
        "id": "qr_support",  # fresh id
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_qual_right",
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", first),
        _tool_response("propose_add_rule", second),
        _review_response([]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a supporting rule for qualified right.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposer_attempts"] == 2
    assert body["op"]["id"] == "qr_support"
    assert body["review_issues"] == []

    # Retry message must reference the Validator's issue (not the previous op).
    retry_user_msg = fake.tool_calls[1]["messages"][0]["content"]
    assert "mc1" in retry_user_msg
    assert "```json" not in retry_user_msg  # previous op JSON is NOT included


def test_propose_unknown_premise_is_advisory_not_blocking(client: TestClient, monkeypatch):
    """A rule with a phantom premise now goes through (200) with a warning.

    The rule is structurally valid; it just won't fire until the missing
    premise exists. User sees the warning and decides whether to Apply.
    """
    from app.api import main as main_module

    op = {
        "id": "store_rule",
        "rule": {
            "type": "defeasible",
            "premises": ["store_open"],  # unknown; advisory
            "conclusion": "popov_legit_claim",
        },
        "new_premise_notes": [
            {"id": "store_open", "description": "the store is currently open"},
        ],
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", op),
        _review_response([]),  # Reviewer has no semantic concerns
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a rule about the store being open.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposer_attempts"] == 1   # no retry
    assert body["op"]["id"] == "store_rule"
    # Advisory surfaces as a severity=warning review issue with NL text.
    assert len(body["review_issues"]) == 1
    warning = body["review_issues"][0]
    assert warning["severity"] == "warning"
    assert "the store is currently open" in warning["message"]
    assert "will not fire" in warning["message"]
    # The raw id should NOT appear in the user-facing message.
    assert "store_open" not in warning["message"]


def test_propose_unknown_premise_without_notes_still_advisory(client: TestClient, monkeypatch):
    """If the Proposer forgot to annotate new_premise_notes, the warning
    falls back to the raw Validator message -- still non-blocking."""
    from app.api import main as main_module

    op = {
        "id": "store_rule",
        "rule": {
            "type": "defeasible",
            "premises": ["store_open"],  # unknown; no new_premise_notes
            "conclusion": "popov_legit_claim",
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", op),
        _review_response([]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a rule.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposer_attempts"] == 1
    assert len(body["review_issues"]) == 1
    assert body["review_issues"][0]["severity"] == "warning"


def test_propose_retry_exhausted_on_persistent_id_collision(client: TestClient, monkeypatch):
    """If a blocking issue (e.g. id_collision) persists across all 3 attempts,
    return 422 proposer_retry_exhausted. Advisory issues alone no longer
    cause exhaustion (see test_propose_unknown_premise_is_advisory_*)."""
    from app.api import main as main_module

    bad = {
        "id": "mc1",  # always collides
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", bad),
        _tool_response("propose_add_rule", bad),
        _tool_response("propose_add_rule", bad),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a rule.",
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "proposer_retry_exhausted"
    assert any(i["code"] == "id_collision" for i in detail["issues"])


def test_propose_modify_rule_coerces_id(client: TestClient, monkeypatch):
    """Proposer emits wrong id for modify-rule; service coerces to existing_id."""
    from app.api import main as main_module

    tool_input = {
        "id": "unrelated",  # Proposer confused
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest", "mob_attacked_popov"],
            "conclusion": "popov_qual_right",
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_modify_rule", tool_input),
        _review_response([]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "modify-rule",
            "existing_id": "mc1",
            "instruction": "Make the rule require both the efforts and the mob attack.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["op"]["id"] == "mc1"


def test_propose_modify_rule_requires_existing_id(client: TestClient, monkeypatch):
    from app.api import main as main_module

    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", _FakeLLMClient())

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "modify-rule",
            "instruction": "Strengthen the main rule.",
        },
    )
    assert resp.status_code == 400
    assert "existing_id" in resp.json()["detail"]


def test_propose_add_fact_skips_reviewer(client: TestClient, monkeypatch):
    """Trivial edits (add-fact, add-assumption) skip the LLM Reviewer entirely."""
    from app.api import main as main_module

    tool_input = {
        "id": "new_fact",
        "fact": {"description": "the weather was clear", "category": "evidence"},
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_fact", tool_input),
        # No _review_response -- if the Reviewer were called, FakeLLMClient
        # would raise "ran out of canned tool responses".
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-fact",
            "instruction": "Add a fact that the weather was clear.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reviewed"] is False
    assert body["review_issues"] == []
    assert len(fake.tool_calls) == 1
    assert fake.tool_calls[0]["tool"]["name"] == "propose_add_fact"


def test_propose_reviewer_issues_surface_with_severity(client: TestClient, monkeypatch):
    """Reviewer issues are passed through with severity tags; never block."""
    from app.api import main as main_module

    tool_input = {
        "id": "br_test",
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_legit_claim",
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", tool_input),
        _review_response([
            {"severity": "warning", "message": "the NL description overstates the rule's force"},
            {"severity": "note", "message": "consider whether a bridging rule is really the cleanest encoding"},
        ]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a bridging rule.",
        },
    )
    assert resp.status_code == 200  # never blocked
    body = resp.json()
    assert body["reviewed"] is True
    assert len(body["review_issues"]) == 2
    assert body["review_issues"][0]["severity"] == "warning"
    assert "overstates" in body["review_issues"][0]["message"]


def test_propose_output_is_a_valid_diff_op(client: TestClient, monkeypatch):
    """End-to-end: Proposer output, once applied via POST /state, adds a rule."""
    from app.api import main as main_module

    tool_input = {
        "id": "pp_new_qr",  # 10 chars
        "rule": {
            "type": "defeasible",
            "premises": ["popov_preposs_interest"],
            "conclusion": "popov_qual_right",
        },
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", tool_input),
        _review_response([]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    propose = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add a rule from pre-possessory interest to qualified right.",
        },
    )
    assert propose.status_code == 200
    op = propose.json()["op"]

    applied = client.post(
        "/state",
        json={"scenario_id": "popov_v_hayashi", "diff_ops": [op]},
    )
    assert applied.status_code == 200, applied.text
    assert "pp_new_qr" in applied.json()["scenario"]["rules"]


def test_propose_reviewer_sees_the_proposed_edit(client: TestClient, monkeypatch):
    """The Reviewer's system prompt must carry the Proposer's output."""
    from app.api import main as main_module

    tool_input = {
        "id": "tag_xyz",
        "rule": {"type": "defeasible", "premises": ["popov_preposs_interest"], "conclusion": "x"},
    }
    fake = _FakeLLMClient(tool_responses=[
        _tool_response("propose_add_rule", tool_input),
        _review_response([]),
    ])
    monkeypatch.setattr(main_module, "ENABLE_LLM", True)
    monkeypatch.setattr(main_module, "_llm_client", fake)

    resp = client.post(
        "/propose",
        json={
            "scenario_id": "popov_v_hayashi",
            "diff_ops": [],
            "task": "add-rule",
            "instruction": "Add any rule.",
        },
    )
    assert resp.status_code == 200
    reviewer_system = fake.tool_calls[1]["system"]
    assert "tag_xyz" in reviewer_system
    assert "Add any rule." in reviewer_system
    assert "<current_state>" in reviewer_system


# --- Original forestry smp-permit regression guard ---


def test_forestry_smp_permit_off_flips_legal_today(client: TestClient):
    """Toggling the smp_permit assumption off removes the permit-based
    support for legal_today, leaving only the prudential attacker active;
    legal_today flips accepted → rejected."""
    resp = client.post(
        "/state",
        json={
            "scenario_id": "fire_prevention",
            "diff_ops": [{"op": "toggle-assumption", "id": "smp_permit"}],
        },
    )
    assert resp.status_code == 200
    labels = _labels(resp)
    assert labels["legal_today"] == "rejected"


# --- POST /state: each of the 10 op kinds ---


def _post_ops(client: TestClient, scenario_id: str, ops: list[dict]):
    return client.post("/state", json={"scenario_id": scenario_id, "diff_ops": ops})


def test_op_toggle_assumption(client: TestClient):
    # Popov has cs1_valid as an assumption (active by default).
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "toggle-assumption", "id": "cs1_valid"},
    ])
    assert resp.status_code == 200
    scen = resp.json()["scenario"]
    assert scen["assumptions"]["cs1_valid"]["active"] is False


def test_op_toggle_rule(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "toggle-rule", "id": "r1"},
    ])
    assert resp.status_code == 200
    assert resp.json()["scenario"]["rules"]["r1"]["active"] is False


def test_op_modify_rule(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {
            "op": "modify-rule",
            "id": "r1",
            "rule": {
                "type": "defeasible",
                "premises": ["r1_valid", "ball_hit_stands"],
                "conclusion": "hayashi_no_return",
                "block": 2,
            },
        },
    ])
    assert resp.status_code == 200
    r1 = resp.json()["scenario"]["rules"]["r1"]
    assert r1["premises"] == ["r1_valid", "ball_hit_stands"]
    assert r1["block"] == 2


def test_op_add_rule(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {
            "op": "add-rule",
            "id": "r_brand_new",
            "rule": {
                "type": "defeasible",
                "premises": ["ball_hit_stands"],
                "conclusion": "hayashi_no_return",
            },
        },
    ])
    assert resp.status_code == 200
    assert "r_brand_new" in resp.json()["scenario"]["rules"]


def test_op_remove_rule(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "remove-rule", "id": "cs3"},
    ])
    assert resp.status_code == 200
    assert "cs3" not in resp.json()["scenario"]["rules"]


def test_op_set_block(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "set-block", "target": "rule", "id": "r1", "block": 5},
    ])
    assert resp.status_code == 200
    assert resp.json()["scenario"]["rules"]["r1"]["block"] == 5


def test_op_add_fact(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {
            "op": "add-fact",
            "id": "new_evidence",
            "fact": {"description": "a new piece of evidence"},
        },
    ])
    assert resp.status_code == 200
    assert "new_evidence" in resp.json()["scenario"]["facts"]


def test_op_remove_fact(client: TestClient):
    # popov_v_hayashi has r2_fan_expectations — used only by rule cs1_r2.
    # Removing it alone would leave cs1_r2's premise dangling, so remove
    # both the fact and the rule in one op batch to preserve integrity.
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "remove-rule", "id": "cs1_r2"},
        {"op": "remove-fact", "id": "r2_fan_expectations"},
    ])
    assert resp.status_code == 200
    assert "r2_fan_expectations" not in resp.json()["scenario"]["facts"]


def test_op_add_assumption(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {
            "op": "add-assumption",
            "id": "new_assumption",
            "assumption": {"description": "a new defeasible starting point"},
        },
    ])
    assert resp.status_code == 200
    assert "new_assumption" in resp.json()["scenario"]["assumptions"]


def test_op_remove_assumption(client: TestClient):
    # cs10_valid is only referenced by rule cs10. Remove both to keep
    # references resolving.
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "remove-rule", "id": "cs10"},
        {"op": "remove-assumption", "id": "cs10_valid"},
    ])
    assert resp.status_code == 200
    assert "cs10_valid" not in resp.json()["scenario"]["assumptions"]


# --- POST /state error shapes ---


def test_post_state_rejects_unknown_op_kind(client: TestClient):
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "teleport-rule", "id": "r1"},
    ])
    assert resp.status_code == 422  # Pydantic request-validation rejection
    body = resp.json()
    assert "errors" in body


def test_post_state_rejects_malformed_op_payload(client: TestClient):
    # Missing the "fact" payload key entirely.
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "add-fact", "id": "xyz"},
    ])
    assert resp.status_code == 422
    assert "errors" in resp.json()


def test_post_state_rejects_bad_identifier_in_payload(client: TestClient):
    # Pydantic accepts the request (id is a str), but diff_ops'
    # JSON-schema check rejects "1illegal" as an invalid identifier.
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "add-fact", "id": "1illegal", "fact": {"description": "x"}},
    ])
    assert resp.status_code == 400
    body = resp.json()
    assert body["errors"][0]["code"] == "op_invalid"


def test_post_state_rejects_op_with_dangling_reference(client: TestClient):
    # Removing a fact that other rules depend on leaves dangling references;
    # the post-apply integrity validator must reject.
    resp = _post_ops(client, "popov_v_hayashi", [
        {"op": "remove-fact", "id": "r2_fan_expectations"},
    ])
    assert resp.status_code == 400
    body = resp.json()
    assert body["errors"][0]["code"] == "op_invalid"


def test_post_state_rejects_unknown_scenario(client: TestClient):
    resp = _post_ops(client, "not_a_real_scenario", [])
    assert resp.status_code == 404
    assert resp.json()["errors"][0]["code"] == "scenario_not_found"


def test_post_state_empty_ops_equivalent_to_get_scenario(client: TestClient):
    g = client.get("/scenarios/medical_ppi").json()
    p = client.post(
        "/state", json={"scenario_id": "medical_ppi", "diff_ops": []}
    ).json()
    assert g == p


def test_self_referential_rule_is_admitted_with_bounded_args(client: TestClient):
    """Adding a rule whose conclusion feeds its own premise chain is
    legitimate per Caminada et al. (2015) Def 7 footnote 6: the rule is
    allowed to fire but cannot appear twice on any root-to-leaf
    derivation path. The API must return 200 with a finite AF (not hang
    or 500)."""
    resp = _post_ops(
        client,
        "medical_ppi",
        [
            {
                "op": "add-rule",
                "id": "r_loop",
                "rule": {
                    "type": "defeasible",
                    "premises": ["continue_ppi"],
                    "conclusion": "continue_ppi",
                },
            }
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "r_loop" in body["scenario"]["rules"]
    # AF remains finite.
    assert isinstance(body["af"]["arguments"], list)
