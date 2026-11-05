"""The shared SPOKEN_TEXT_RULES block (Chatterbox-friendly narration) is in every writer prompt,
no prompt teaches "#15"-style spoken examples, the .md templates cannot drift from the constant,
and the validators accept the spoken form of years / issue numbers."""
import re
from pathlib import Path

import pytest

from stages.panel_walk import narrate as pw
from stages.stage_3 import (explore_answer as ea, gemini_prompt as gp, micro_moment as mm,
                            propose_modes as pm, provided_narration as pn, screen_qa as sq,
                            story_verify as sv, write_script as ws)
from stages.stage_3.spoken_rules import SPOKEN_TEXT_RULES, with_spoken_rules, year_words

INLINE_PROMPTS = {
    "write_script._INTRO_SYSTEM": ws._INTRO_SYSTEM,
    "write_script._OUTRO_SYSTEM": ws._OUTRO_SYSTEM,
    "write_script._LOOP_TEASE_SYSTEM": ws._LOOP_TEASE_SYSTEM,
    "write_script._WRITE_SYSTEM": ws._WRITE_SYSTEM,
    "explore_answer._EXPLORE_WRITE_SYSTEM_LIST": ea._EXPLORE_WRITE_SYSTEM_LIST,
    "explore_answer._EXPLORE_WRITE_SYSTEM_EXPLAIN": ea._EXPLORE_WRITE_SYSTEM_EXPLAIN,
    "explore_answer._QA_OUTRO_SYSTEM": ea._QA_OUTRO_SYSTEM,
    "micro_moment._MICRO_WRITE_SYSTEM": mm._MICRO_WRITE_SYSTEM,
    "screen_qa._SYSTEM_LIST": sq._SYSTEM_LIST,
    "screen_qa._SYSTEM_EXPLAIN": sq._SYSTEM_EXPLAIN,
    "story_verify._REWRITE_SYSTEM": sv._REWRITE_SYSTEM,
    "provided_narration._FRAME_SYSTEM": pn._FRAME_SYSTEM,
    "propose_modes._SYSTEM": pm._SYSTEM,
    "panel_walk._SYSTEM": pw._SYSTEM,
    "panel_walk._COLD_OPEN_SYSTEM": pw._COLD_OPEN_SYSTEM,
    "panel_walk._OUTRO_SYSTEM": pw._OUTRO_SYSTEM,
}

PROMPTS_DIR = Path(gp.QA_TEMPLATE_PATH).parent
SPOKEN_MD = {
    "micro": gp.MICRO_TEMPLATE_PATH,
    "qa": gp.QA_TEMPLATE_PATH,
    "topic": gp.TOPIC_QA_TEMPLATE_PATH,
}


def test_block_is_tight_and_digit_free():
    assert SPOKEN_TEXT_RULES.count("\n") + 1 <= 25
    assert re.findall(r"#\d+", SPOKEN_TEXT_RULES) == ["#15"]   # only as the thing NOT to write
    assert "issue fifteen" in SPOKEN_TEXT_RULES and "F. B. I." in SPOKEN_TEXT_RULES


def test_with_spoken_rules_needs_its_marker():
    assert SPOKEN_TEXT_RULES in with_spoken_rules("abc\nReturn ONLY JSON.")
    with pytest.raises(ValueError):
        with_spoken_rules("no marker here")


@pytest.mark.parametrize("name", sorted(INLINE_PROMPTS))
def test_inline_prompt_carries_the_shared_block(name):
    assert SPOKEN_TEXT_RULES in INLINE_PROMPTS[name]


@pytest.mark.parametrize("name", sorted(INLINE_PROMPTS))
def test_inline_prompt_has_no_hash_number_example(name):
    assert not re.search(r"#\d", INLINE_PROMPTS[name].replace(SPOKEN_TEXT_RULES, "")), name


@pytest.mark.parametrize("key", sorted(SPOKEN_MD))
def test_md_template_has_no_hash_number_example(key):
    text = SPOKEN_MD[key].read_text(encoding="utf-8")
    assert not re.search(r"#\d", text.replace(SPOKEN_TEXT_RULES, ""))
    assert "#[Issue]" not in text


def test_micro_and_qa_templates_get_the_block_through_the_placeholder():
    for key in ("micro", "qa"):
        text = SPOKEN_MD[key].read_text(encoding="utf-8")
        assert text.count(gp.SPOKEN_RULES_PLACEHOLDER) == 1
        filled = gp.fill_spoken_rules(text)
        assert SPOKEN_TEXT_RULES in filled and gp.SPOKEN_RULES_PLACEHOLDER not in filled


def test_fallback_template_without_placeholder_still_gets_the_block():
    assert SPOKEN_TEXT_RULES in gp.fill_spoken_rules("THE QUESTION: x")


def test_topic_template_is_pasted_by_hand_and_must_match_the_constant():
    assert SPOKEN_TEXT_RULES in SPOKEN_MD["topic"].read_text(encoding="utf-8")


def test_md_examples_use_the_spoken_issue_form():
    for key in SPOKEN_MD:
        text = SPOKEN_MD[key].read_text(encoding="utf-8")
        assert "issue fifteen" in text, key
        assert "issue one twenty-five" in text, key


def test_old_conflicting_short_fragment_examples_are_gone():
    assert 'It doesn\'t."' not in ws._WRITE_SYSTEM
    assert '"Warda was furious." (3w)' not in ws._WRITE_SYSTEM


@pytest.mark.parametrize("year,words", [
    (2019, "twenty nineteen"), (1984, "nineteen eighty-four"), (2005, "two thousand five"),
    (2000, "two thousand"), (1905, "nineteen oh five"), (1900, "nineteen hundred"),
    (2010, "twenty ten"),
])
def test_year_words(year, words):
    assert year_words(year) == words


def test_screen_citation_accepts_a_spoken_year_and_still_digits():
    title = "Deep Station"
    assert sq._cites("In the twenty eleven film Deep Station, Alice acts.", title, 2011)
    assert sq._cites("In Deep Station, from twenty-eleven, Alice acts.", title, 2011)
    assert sq._cites("In Deep Station (2011), Alice acts.", title, 2011)
    assert not sq._cites("In Deep Station, Alice acts.", title, 2011)


def test_screen_validator_accepts_spoken_year_and_does_not_fail_on_hash():
    items = [{"entity": "Alice Park", "adaptation_title": "Deep Station", "year": 2011}]
    scenes = [{"text": "In the twenty eleven film Deep Station, Alice Park seals the airlock."},
              {"text": "Then the crew runs for the pods, but she stays behind #1."}]
    issues = sq._validate_screen_scenes(scenes, items, "list", band=(1, 999), scene_max=42)
    assert not any("cite" in i or "#" in i for i in issues)
