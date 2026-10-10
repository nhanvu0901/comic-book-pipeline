import json

import pytest

from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle
from tests import micro_detail_rules as rules


def test_general_micro_template_is_external_and_records_hash():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    rendered = bundle.render(
        "general",
        user_intent="new Hulk moment",
        angle="power failure",
        count="20",
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
    qa_general = qa.render(
        "general", user_intent="Hulk", angle="immunity", count="20", digest="none",
    )
    micro_general = micro.render(
        "general", user_intent="Hulk", angle="immunity", count="20", digest="none",
    )
    assert qa_general.version == "general_qa.v2"
    assert micro_general.version == "general_micro.v1"
    assert qa_general.text != micro_general.text


def test_specific_and_evidence_templates_accept_their_declared_values():
    bundle = PolicyBundle.load(ScoutMode.QA)
    # specific v2 is a research request, not a validator of evidence already in
    # hand, so it takes no raw_evidence.
    specific = bundle.render(
        "specific",
        user_intent="Hulk",
        angle="immunity",
        digest="none",
        candidate="candidate-1",
    )
    evidence = bundle.render(
        "evidence_gate",
        user_intent="Hulk",
        angle="immunity",
        digest="none",
        candidate="candidate-1",
        raw_evidence="source excerpt",
    )
    assert specific.version == "specific_qa.v2"
    assert evidence.version == "evidence_gate.v2"
    assert "candidate-1" in specific.text
    assert "source excerpt" in evidence.text


def test_policy_json_assets_are_valid_and_expose_required_gates():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    assert len(bundle.source_profiles["general_research"]["domains"]) == 8
    assert len(bundle.source_profiles["specific_web_search"]["domains"]) == 7
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


def test_evidence_gate_v2_keeps_v1s_placeholder_set():
    """Both fetched-source sections go inside the existing raw_evidence value,
    so _ALLOWED_PLACEHOLDERS needs no change and nothing that renders the gate
    has to learn a new keyword."""
    from stages.research_scout.policies import _PROMPTS_ROOT, _placeholders

    v1 = _placeholders((_PROMPTS_ROOT / "evidence_gate.v1.md").read_text(encoding="utf-8"))
    v2 = _placeholders((_PROMPTS_ROOT / "evidence_gate.v2.md").read_text(encoding="utf-8"))
    assert v2 == v1 == {"user_intent", "angle", "digest", "candidate", "raw_evidence"}


def test_evidence_gate_v2_tells_the_gate_what_an_unfetchable_source_means():
    """A cited URL nobody could open is unverified, not contradicted. Letting
    that produce `rejected` is the defect this version exists to close."""
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    rendered = bundle.render(
        "evidence_gate",
        user_intent="Hulk",
        angle="immunity",
        digest="none",
        candidate="candidate-1",
        raw_evidence="source excerpt",
    )
    assert "COULD NOT FETCH" in rendered.text
    assert "CITED SOURCES" in rendered.text
    assert "VERIFICATION RESEARCH" in rendered.text
    lowered = rendered.text.lower()
    assert "unverified, not contradicted" in lowered
    assert "already fetched" in lowered


def _render_general(mode):
    return PolicyBundle.load(mode).render(
        "general", user_intent="final twist of the fight", angle="a reveal",
        count="6", digest="none",
    ).text


def test_general_micro_template_carries_the_three_rules_in_order_and_qa_does_not():
    micro = _render_general(ScoutMode.MICRO)
    qa = _render_general(ScoutMode.QA)

    assert rules.missing_rules(micro) == []
    assert rules.in_order(micro)
    # str.format ate the doubled braces: the model sees one object per citation.
    assert '{"supports": "aftermath" or "context_behind"' in micro
    assert "{{" not in micro
    for name in rules.DETAIL_FIELDS:
        assert name not in qa
    assert "is not an event" not in qa
    # the fixed template asks for the same canonical identity / url shape as the planner path
    assert rules.missing_format_rules(micro) == []
    assert "Series Title #N" not in qa


def test_general_micro_template_keeps_the_pinned_breadth_wording():
    """The three rules were added below this paragraph; it must not be reflowed."""
    micro = _render_general(ScoutMode.MICRO)
    assert micro.startswith(
        "# General micro-moment research scout\n\n"
        "Find source-supported comic moments for this request. Seek about 6 distinct source pages\n"
        "when available; there is no candidate minimum. State the\n"
        "actual source and candidate counts in notes and never pad the list.\n"
    )


def test_specific_micro_template_flags_a_moment_that_rests_on_a_reaction_or_teaser():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    text = bundle.render(
        "specific", user_intent="Hulk", angle="x", digest="none", candidate="candidate-1",
    ).text
    flat = rules.normalized(text)

    # still the verification round it was
    assert "Verify ONE proposed" in text
    assert "NOT CONFIRMED" in text
    # the new check: a stated event, not a reviewer's reaction or a teaser
    assert "reaction or a teaser" in flat
    assert "a shocking twist I never saw coming" in flat
    assert "aftermath" in flat and "context_behind" in flat
    assert "withheld" in flat or "withholds" in flat
    # the QA verification template is untouched
    qa = PolicyBundle.load(ScoutMode.QA).render(
        "specific", user_intent="Hulk", angle="x", digest="none", candidate="candidate-1",
    ).text
    assert "reaction or a teaser" not in qa


def test_moment_scout_agent_doc_asks_for_the_same_things():
    """The agent scouts by hand what the workflow scouts by API; it gets the same
    ask and the same two rules, in its own words."""
    from pathlib import Path

    doc = Path(__file__).resolve().parent.parent / ".claude" / "agents" / "moment-scout.md"
    flat = rules.normalized(doc.read_text(encoding="utf-8"))

    assert "aftermath" in flat and "context" in flat and "unrevealed" in flat
    assert "not stated" in flat                      # the agent's "" for a missing source
    assert "a shocking twist I never saw coming" in flat
    assert "is not an event" in flat
    assert "final twist" in flat and "it is never evidence" in flat


def test_reddit_scouts_but_does_not_verify():
    """Reddit is where you learn what fans argue about, which is discovery's
    job. For confirming an issue number and year it is not a primary source,
    and at count=8 it crowded every other domain out of the verification
    search — 67 of 69 hits on one real candidate. A Reddit URL a candidate
    cites itself is still fetched, as a citation."""
    profiles = PolicyBundle.load(ScoutMode.QA).source_profiles
    assert "reddit.com" in profiles["general_research"]["domains"]
    assert "reddit.com" not in profiles["specific_web_search"]["domains"]
