import json
import urllib.error

import pytest

from stages.research_scout import planner
from stages.research_scout.planner import (
    PlanField,
    ResearchPlan,
    assemble_prompt,
    compile_schema,
    make_plan,
    validate_plan,
)


def _plan(**overrides) -> ResearchPlan:
    base = dict(
        unit="one character",
        cardinality="exhaustive",
        ranking="",
        extra_fields=[],
        research_prompt="List every character who resisted the Anti-Life Equation.",
    )
    base.update(overrides)
    return ResearchPlan(**base)


# ─── validate_plan ──────────────────────────────────────────────────────────


def test_validate_plan_accepts_a_well_formed_plan():
    assert validate_plan(_plan()) is None


def test_validate_plan_rejects_unknown_cardinality():
    assert validate_plan(_plan(cardinality="bogus")) is not None


def test_validate_plan_rejects_too_many_extra_fields():
    fields = [PlanField(name=f"field_{i}", type="string", description="") for i in range(7)]
    assert validate_plan(_plan(extra_fields=fields)) is not None


def test_validate_plan_rejects_bad_field_name_pattern():
    fields = [PlanField(name="Bad-Name", type="string", description="")]
    assert validate_plan(_plan(extra_fields=fields)) is not None


@pytest.mark.parametrize("reserved", [
    "title", "summary", "character_or_thing", "series_issue_year",
    "what_visibly_happens", "evidence_urls", "rank_reason", "id", "flags",
    "notes", "candidates",
])
def test_validate_plan_rejects_reserved_field_names(reserved):
    fields = [PlanField(name=reserved, type="string", description="")]
    assert validate_plan(_plan(extra_fields=fields)) is not None


def test_validate_plan_rejects_duplicate_field_names():
    fields = [
        PlanField(name="issue_number", type="string", description=""),
        PlanField(name="issue_number", type="string_array", description=""),
    ]
    assert validate_plan(_plan(extra_fields=fields)) is not None


def test_validate_plan_rejects_unknown_field_type():
    fields = [PlanField(name="issue_number", type="integer", description="")]
    assert validate_plan(_plan(extra_fields=fields)) is not None


def test_validate_plan_rejects_blank_research_prompt():
    assert validate_plan(_plan(research_prompt="")) is not None


def test_validate_plan_rejects_overlong_research_prompt():
    assert validate_plan(_plan(research_prompt="x" * 4001)) is not None


def test_validate_plan_rejects_blank_unit():
    assert validate_plan(_plan(unit="")) is not None


# ─── compile_schema ─────────────────────────────────────────────────────────


def test_compile_schema_is_strict_at_both_object_levels():
    plan = _plan(extra_fields=[PlanField(name="issue_number", type="string", description="")])
    schema = compile_schema(plan)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])

    items = schema["properties"]["candidates"]["items"]
    assert items["additionalProperties"] is False
    assert set(items["required"]) == set(items["properties"])


def test_compile_schema_maps_string_array_extra_field():
    plan = _plan(extra_fields=[PlanField(name="aliases", type="string_array", description="")])
    schema = compile_schema(plan)
    item_props = schema["properties"]["candidates"]["items"]["properties"]
    assert item_props["aliases"] == {"type": "array", "items": {"type": "string"}}


def test_compile_schema_adds_rank_reason_only_when_ranking_set():
    with_ranking = compile_schema(_plan(ranking="most brutal"))
    without_ranking = compile_schema(_plan(ranking=""))

    with_props = with_ranking["properties"]["candidates"]["items"]["properties"]
    without_props = without_ranking["properties"]["candidates"]["items"]["properties"]

    assert "rank_reason" in with_props
    assert "rank_reason" not in without_props
    assert "rank_reason" in with_ranking["properties"]["candidates"]["items"]["required"]


def test_compile_schema_never_contains_min_or_max_items():
    plan = _plan(
        extra_fields=[PlanField(name="issue_number", type="string", description="")],
        ranking="best",
    )
    dumped = json.dumps(compile_schema(plan))
    assert "minItems" not in dumped
    assert "maxItems" not in dumped


# ─── assemble_prompt ────────────────────────────────────────────────────────


def test_assemble_prompt_contains_unit_sentence_and_digest_at_end():
    plan = _plan(unit="one issue")
    text = assemble_prompt(plan, "DIGEST-CONTENT")
    assert "One candidate per one issue — never merge entries." in text
    assert text.rstrip().endswith("DIGEST-CONTENT")


@pytest.mark.parametrize("cardinality,expected_snippet", [
    ("exhaustive", "Sweep EVERY retrieved source"),
    ("options", "propose distinct options"),
    ("pinpoint", "The set is closed and named in the task"),
])
def test_assemble_prompt_cardinality_block(cardinality, expected_snippet):
    text = assemble_prompt(_plan(cardinality=cardinality), "digest")
    assert expected_snippet in text


def test_assemble_prompt_includes_ranking_block_only_when_set():
    with_ranking = assemble_prompt(_plan(ranking="most brutal"), "digest")
    without_ranking = assemble_prompt(_plan(ranking=""), "digest")
    assert "most brutal" in with_ranking
    assert "rank_reason" in with_ranking
    assert "rank_reason" not in without_ranking


# ─── make_plan ──────────────────────────────────────────────────────────────


class _Response:
    status = 200

    def __init__(self, content):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": self._content}}]
        }).encode("utf-8")


def _valid_plan_json() -> str:
    return json.dumps({
        "unit": "one character",
        "cardinality": "exhaustive",
        "ranking": "",
        "extra_fields": [],
        "research_prompt": "List every character who resisted the Anti-Life Equation.",
    })


def test_make_plan_returns_none_when_api_key_unset(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "")
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(1))

    assert make_plan("question", [], "qa") is None
    assert calls == []


def test_make_plan_parses_valid_json_on_first_try(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response(_valid_plan_json())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    plan = make_plan("Who resisted the Anti-Life Equation?", [], "qa")

    assert isinstance(plan, ResearchPlan)
    assert plan.unit == "one character"
    assert len(requests) == 1
    assert requests[0]["provider"]["require_parameters"] is True
    assert requests[0]["response_format"]["type"] == "json_schema"


def test_make_plan_repairs_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    responses = ["not json", _valid_plan_json()]
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response(responses.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    plan = make_plan("Who resisted the Anti-Life Equation?", [], "qa")

    assert isinstance(plan, ResearchPlan)
    assert len(requests) == 2


def test_make_plan_returns_none_after_two_invalid_responses(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    responses = ["not json", "still not json"]
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response(responses.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    plan = make_plan("Who resisted the Anti-Life Equation?", [], "qa")

    assert plan is None
    assert len(requests) == 2


def test_make_plan_returns_none_on_transport_failure_with_one_request(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    calls = []

    def fail_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("temporary failure")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    plan = make_plan("Who resisted the Anti-Life Equation?", [], "qa")

    assert plan is None
    assert len(calls) == 1


def test_repair_prompt_names_the_rule_that_was_broken(monkeypatch):
    """A generic "that was invalid" makes the model guess which knob to change."""
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    reserved_field_plan = json.dumps({
        "unit": "one character",
        "cardinality": "exhaustive",
        "ranking": "",
        "extra_fields": [{"name": "summary", "type": "string", "description": "x"}],
        "research_prompt": "List every character who did it.",
    })
    responses = [reserved_field_plan, _valid_plan_json()]
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response(responses.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    plan = make_plan("Who resisted the Anti-Life Equation?", [], "qa")

    assert isinstance(plan, ResearchPlan)
    assert len(requests) == 2
    repair_text = requests[1]["messages"][1]["content"]
    assert "'summary' is reserved" in repair_text
