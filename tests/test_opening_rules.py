"""Event-first openings and the citation placement rule (2026-11-12 script gap).

Measured: our first concrete story event came 7-10 s in, behind a question / "what if" hook and a
sentence citing the comic and issue; competitors state a concrete event in sentence one and never
cite in sentence two. Every narration writer now carries the shared OPENING RULES, no prompt still
demands the citation in the sentence right after the hook, and the validators accept a script that
names its source after the first event."""
import json
import re

import pytest

from stages.stage_3 import (explore_answer as ea, gemini_prompt as gp, micro_moment as mm,
                            provided_narration as pn, screen_qa as sq, write_script as ws)
from stages.stage_3 import pipeline as pl
from stages.stage_3.opening_rules import (CITATION_PLACEMENT_RULES, EVENT_FIRST_RULES,
                                          OPENING_RULES, OPENING_RULES_PLACEHOLDER,
                                          fill_opening_rules, with_opening_rules)
from stages.stage_3.spoken_rules import SPOKEN_TEXT_RULES

# Every inline system prompt that writes the OPENING of a spoken script.
EVENT_FIRST_PROMPTS = {
    "write_script._INTRO_SYSTEM": ws._INTRO_SYSTEM,
    "write_script._WRITE_SYSTEM": ws._WRITE_SYSTEM,
    "explore_answer._EXPLORE_WRITE_SYSTEM_LIST": ea._EXPLORE_WRITE_SYSTEM_LIST,
    "explore_answer._EXPLORE_WRITE_SYSTEM_EXPLAIN": ea._EXPLORE_WRITE_SYSTEM_EXPLAIN,
    "micro_moment._MICRO_WRITE_SYSTEM": mm._MICRO_WRITE_SYSTEM,
    "screen_qa._SYSTEM_LIST": sq._SYSTEM_LIST,
    "screen_qa._SYSTEM_EXPLAIN": sq._SYSTEM_EXPLAIN,
    "provided_narration._FRAME_SYSTEM": pn._FRAME_SYSTEM,
}
# Of those, the writers that name their source (comic + issue, film + year) in the opening.
CITING_PROMPTS = (
    "explore_answer._EXPLORE_WRITE_SYSTEM_LIST",
    "explore_answer._EXPLORE_WRITE_SYSTEM_EXPLAIN",
    "screen_qa._SYSTEM_LIST",
    "screen_qa._SYSTEM_EXPLAIN",
)
TEMPLATES = {
    "micro": gp.MICRO_TEMPLATE_PATH,
    "qa": gp.QA_TEMPLATE_PATH,
    "topic": gp.TOPIC_QA_TEMPLATE_PATH,
    "comic_vs_show": gp.COMIC_VS_SHOW_TEMPLATE_PATH,
}
# Wording of the old rule ("the sentence right after the hook must cite the comic").
OLD_CITATION_WORDING = re.compile(
    r"REQUIRED AFTER THE HOOK"
    r"|(?:immediately|right) after (?:the )?(?:chosen )?hook\W[^.\n]{0,60}(?:identif|names?\b|mention)"
    r"|next spoken sentence will identify"
    r"|after the hook MUST (?:name|naturally identify)"
    r"|HOOK → COMIC \+ ISSUE",
    re.IGNORECASE,
)
# The question-by-default hook options the old prompts offered.
OLD_QUESTION_OPTIONS = (
    "Ask a specific question that this moment actually answers",
    "a specific question the moment answers",
    "One candidate may be a question",
    "a direct question about that same concrete event",
    "A direct question, a factual statement",
    "A direct question about a SPECIFIC event",
    "a direct question, a plain fact",
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _filled(key: str) -> str:
    text = TEMPLATES[key].read_text(encoding="utf-8")
    return fill_opening_rules(gp.fill_spoken_rules(text))


# ── the shared block ─────────────────────────────────────────────────────────


def test_rules_are_general_and_carry_the_measured_limits():
    flat = _flat(OPENING_RULES)
    assert "first ten words" in flat and "three seconds" in flat
    assert "only when the source itself is a hypothetical" in flat
    assert "only after the first story event" in flat
    assert "within roughly the first third" in flat
    assert "does not have to be the sentence right after the hook" in flat
    assert not re.search(r"#\d", OPENING_RULES)


def test_rules_do_not_conflict_with_the_spoken_rules():
    # The examples obey the spoken-text rules: no digits, no symbols, issue numbers as words.
    examples = re.findall(r'"([^"]+)"', CITATION_PLACEMENT_RULES)
    assert examples
    for ex in examples:
        assert not re.search(r"\d|#", ex), ex
    assert SPOKEN_TEXT_RULES not in OPENING_RULES


def test_with_opening_rules_needs_its_marker_and_can_skip_the_citation_block():
    full = with_opening_rules("abc\nReturn ONLY JSON.")
    assert OPENING_RULES in full and full.endswith("Return ONLY JSON.")
    event_only = with_opening_rules("abc\nReturn ONLY JSON.", citation=False)
    assert EVENT_FIRST_RULES in event_only and CITATION_PLACEMENT_RULES not in event_only
    with pytest.raises(ValueError):
        with_opening_rules("no marker here")


def test_fill_opening_rules_placeholder_append_and_idempotent():
    assert fill_opening_rules(f"x\n{OPENING_RULES_PLACEHOLDER}\ny") == f"x\n{OPENING_RULES}\ny"
    appended = fill_opening_rules("THE QUESTION: x")
    assert appended.count(OPENING_RULES) == 1
    assert fill_opening_rules(appended).count(OPENING_RULES) == 1


# ── every writer carries it ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(EVENT_FIRST_PROMPTS))
def test_inline_writer_carries_the_event_first_rule(name):
    prompt = EVENT_FIRST_PROMPTS[name]
    assert EVENT_FIRST_RULES in prompt
    assert SPOKEN_TEXT_RULES in prompt                     # the shared spoken block is intact


@pytest.mark.parametrize("name", CITING_PROMPTS)
def test_citing_inline_writer_carries_the_citation_rule(name):
    assert CITATION_PLACEMENT_RULES in EVENT_FIRST_PROMPTS[name]


@pytest.mark.parametrize("name", sorted(EVENT_FIRST_PROMPTS))
def test_inline_writer_has_no_old_citation_or_question_default(name):
    prompt = _flat(EVENT_FIRST_PROMPTS[name])
    assert not OLD_CITATION_WORDING.search(prompt), OLD_CITATION_WORDING.search(prompt)
    for old in OLD_QUESTION_OPTIONS:
        assert old not in prompt, old


@pytest.mark.parametrize("key", ["micro", "qa", "comic_vs_show"])
def test_rendered_templates_get_the_rules_through_the_placeholder(key):
    text = TEMPLATES[key].read_text(encoding="utf-8")
    assert text.count(OPENING_RULES_PLACEHOLDER) == 1
    filled = _filled(key)
    assert OPENING_RULES in filled and OPENING_RULES_PLACEHOLDER not in filled
    assert SPOKEN_TEXT_RULES in filled


def test_topic_template_is_pasted_by_hand_and_must_match_the_constant():
    assert OPENING_RULES in TEMPLATES["topic"].read_text(encoding="utf-8")


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_template_has_no_old_citation_or_question_default(key):
    flat = _flat(_filled(key))
    assert not OLD_CITATION_WORDING.search(flat), OLD_CITATION_WORDING.search(flat)
    for old in OLD_QUESTION_OPTIONS:
        assert old not in flat, old


@pytest.mark.parametrize("key", ["micro", "qa", "topic"])
def test_comic_templates_name_the_issue_after_the_first_event(key):
    flat = _flat(TEMPLATES[key].read_text(encoding="utf-8"))
    assert "COMIC ISSUE IDENTIFICATION — AFTER THE FIRST EVENT" in flat
    assert "It does not have to be the sentence right after the hook." in flat
    assert "That's Ultimate Wolverine issue fifteen." in flat
    # the self-check and the audit line follow the new placement
    assert re.search(r"16\. Is the first concrete story event spoken in the hook|"
                     r"16\. The first concrete story event is spoken in the hook", flat)
    assert re.search(r"ISSUE MENTION CHECK: <quote the short clause or sentence that names", flat)


def test_comic_vs_show_cites_in_act_two_as_a_short_clause_after_the_first_event():
    flat = _flat(TEMPLATES["comic_vs_show"].read_text(encoding="utf-8"))
    assert "state one concrete event as a fact within the first ten words" in flat
    assert "It comes after the first event, within roughly the first third" in flat


def test_generated_micro_prompt_reaches_gemini_with_the_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "rules_probe"
    root.mkdir()
    (root / "comic_context.json").write_text(json.dumps({
        "title": "Probe #1", "pipeline_mode": "micro_moment",
        "target_moment": "A hero does an odd thing."}), encoding="utf-8")
    prompt, _path = gp.generate_gemini_writer_prompt("rules_probe")
    assert OPENING_RULES in prompt and OPENING_RULES_PLACEHOLDER not in prompt


# ── validators accept a citation that comes after the first event ────────────


def test_explore_validator_accepts_a_late_short_citation():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Wolverine", page_refs=[1]),
             ea.Beat(id=2, function="LANDING", name="Storm", page_refs=[2])]
    scenes = [
        {"text": "Wolverine walks into the burning lab to pull out the last hostage alive. "
                 "That's Wolverine issue twelve."},
        {"text": "He carries her out while his skin keeps healing faster than the fire burns it."},
        {"text": "Storm pulls the rain down on the whole block, all in X-Men issue three."},
        {"text": "The fire dies in seconds, and the team finds every hostage still breathing."},
    ]
    issues = ea._validate_explore_scenes(scenes, beats, "list")
    assert not any(re.search(r"cite|issue \w+|comic", i, re.I) for i in issues), issues
    assert not any("never names its entity" in i for i in issues), issues


def test_screen_validator_accepts_a_citation_after_the_first_event():
    items = [{"entity": "Alice Park", "adaptation_title": "Deep Station", "year": 2011}]
    scenes = [{"text": "Alice Park seals the airlock with the crew still outside. "
                       "That's the twenty eleven film Deep Station."},
              {"text": "Then the crew runs for the pods, but she stays behind to hold the door."}]
    issues = sq._validate_screen_scenes(scenes, items, "list", band=(1, 999), scene_max=42)
    assert not any("cite" in i for i in issues), issues


def _micro_project(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "event_first"
    root.mkdir()
    (root / "comic_context.json").write_text(
        json.dumps({"title": "A comic moment", "pipeline_mode": "micro_moment"}), encoding="utf-8")
    return "event_first"


def test_pasted_event_first_script_with_a_late_citation_is_saved_as_written(tmp_path, monkeypatch):
    hook = "Doctor Doom scares off Galactus while trapped in another hero's body."
    body = ("A time traveling Doom wakes up inside that hero right as the giant reaches Earth. "
            "He learns her force field powers in minutes and knocks the herald out of the sky. "
            "That's Doomquest issue five. Then he fakes the one weapon the giant fears, and it works.")
    script = f"FINAL SCRIPT\n{hook}\n\n{body}"
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, script, log=lambda *_: None)

    texts = [s["text"] for s in narration["scenes"]]
    assert narration["hook"] == hook and texts[0] == hook
    assert " ".join(texts[1:]) == body                         # nothing moved, nothing appended
    assert "subscribe" not in " ".join(texts).lower()
