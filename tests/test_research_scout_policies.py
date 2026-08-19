import json

import pytest

from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle


def test_general_micro_template_is_external_and_records_hash():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    rendered = bundle.render(
        "general",
        user_intent="new Hulk moment",
        angle="power failure",
        digest="none",
    )
    assert "new Hulk moment" in rendered.text
    assert rendered.version == "general_micro.v1"
    assert len(rendered.sha256) == 64


def test_missing_required_placeholder_fails_loudly():
    bundle = PolicyBundle.load(ScoutMode.QA)
    with pytest.raises(ValueError, match="digest"):
        bundle.render("general", user_intent="Hulk", angle="immunity")


def test_render_rejects_unknown_template_and_extra_values():
    bundle = PolicyBundle.load(ScoutMode.QA)
    with pytest.raises(ValueError, match="unknown template"):
        bundle.render("missing", user_intent="Hulk", angle="immunity", digest="none")
    with pytest.raises(ValueError, match="unsupported placeholder"):
        bundle.render(
            "general",
            user_intent="Hulk",
            angle="immunity",
            digest="none",
            unexpected="value",
        )


def test_mode_selects_distinct_general_and_specific_templates():
    qa = PolicyBundle.load(ScoutMode.QA)
    micro = PolicyBundle.load(ScoutMode.MICRO)
    qa_general = qa.render("general", user_intent="Hulk", angle="immunity", digest="none")
    micro_general = micro.render("general", user_intent="Hulk", angle="immunity", digest="none")
    assert qa_general.version == "general_qa.v1"
    assert micro_general.version == "general_micro.v1"
    assert qa_general.text != micro_general.text


def test_specific_and_evidence_templates_accept_their_declared_values():
    bundle = PolicyBundle.load(ScoutMode.QA)
    specific = bundle.render(
        "specific",
        user_intent="Hulk",
        angle="immunity",
        digest="none",
        candidate="candidate-1",
        raw_evidence="source excerpt",
    )
    evidence = bundle.render(
        "evidence_gate",
        user_intent="Hulk",
        angle="immunity",
        digest="none",
        candidate="candidate-1",
        raw_evidence="source excerpt",
    )
    assert specific.version == "specific_qa.v1"
    assert evidence.version == "evidence_gate.v1"
    assert "candidate-1" in specific.text
    assert "source excerpt" in evidence.text


def test_policy_json_assets_are_valid_and_expose_required_gates():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    assert len(bundle.source_profiles["general_research"]["domains"]) == 8
    assert len(bundle.source_profiles["specific_web_search"]["domains"]) == 8
    assert bundle.source_profiles["general_research"]["domains"] is not bundle.source_profiles["specific_web_search"]["domains"]
    assert len(bundle.general_angles["qa"]) == 5
    assert len(bundle.general_angles["micro"]) == 5
    assert bundle.gates == {
        "qa_min_items": 3,
        "qa_max_items": 5,
        "micro_exact_issue_required": True,
        "search_query_max_words": 45,
        "search_query_max_chars": 360,
    }


def test_policy_bundle_does_not_mutate_loaded_json_assets():
    first = PolicyBundle.load(ScoutMode.QA)
    first.gates["qa_min_items"] = 99
    second = PolicyBundle.load(ScoutMode.QA)
    assert second.gates["qa_min_items"] == 3
    assert json.loads(json.dumps(second.gates)) == second.gates
