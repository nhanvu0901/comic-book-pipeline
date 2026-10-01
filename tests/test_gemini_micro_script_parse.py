"""Reading a micro_moment script the way Gemini prints it: hook options, FINAL SCRIPT, then
the audit lines. A fake project on disk only, no network.

Seen 2026-10-01: the writer's paste spoke the hook twice (6 scenes, 105 words where its own
SPOKEN WORD COUNT said 87). It prints the chosen hook as the first line of FINAL SCRIPT, and
the recap/micro path kept that line as both the hook and the first body paragraph; the Q&A
path already dropped the repeat. The parser does not judge what a sentence says (the
template keeps teasers out of the script); only the hook repeat and the audit tail are its job.
"""
import json
import re

import pytest

import stages.stage_3.gemini_prompt as gp
import stages.stage_3.pipeline as pl

HOOK = ("Captain America battled the Red Skull in the middle of Hell with a weapon that "
        "makes evil explode.")
BODY = ("Steve Rogers fights to topple Mephisto's rule over the underworld, while the Skull "
        "wants to slay his hated foe once and for all. During the fight, Steve hurls the "
        "Antidivine. The weapon looks like a shield, but it hits with enough force to make "
        "evil literally explode. The underworld showdown ends with a shocking twist centered "
        "on Steve, offering what looks like an overly easy solution to his problem.")

REAL_PASTE = f"""HOOK OPTIONS

1. {HOOK}
2. Captain America fought for control of Hell using a shield that makes evil literally explode.
3. Steve Rogers brought a brand new shield to Hell that hits hard enough to make evil explode.
CHOSEN HOOK: 1

FINAL SCRIPT
{HOOK}

{BODY}

SPOKEN WORD COUNT: 87

FACT TRACE
S1 | Captain America battled the Red Skull | B1, B2
S2 | Steve Rogers fights to topple | B1
S3 | During the fight, Steve hurls | B2
S4 | The weapon looks like a | B2
S5 | The underworld showdown ends with | B3
OPENING CHAIN: Captain America battled the Red Skull in Hell with an exploding weapon -> Steve fights to topple Mephisto's rule while the Skull wants to slay him, B1 -> Steve hurls the Antidivine, B2
STORY FRAME CHECK: B1 (Steve fighting to topple Mephisto while Red Skull wants to slay him) used in S2. Confirmed no generic trait was substituted.
REACTIONS: none
UNSUPPORTED FACTS: none
"""

AUDIT_WORDS = ("SPOKEN WORD COUNT", "FACT TRACE", "OPENING CHAIN", "STORY FRAME CHECK",
               "AFTERMATH CHECK", "OPEN LOOPS", "REACTIONS", "UNSUPPORTED FACTS", "HOOK OPTIONS",
               "CHOSEN HOOK", "S1 |")


def _micro_project(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "micro_paste"
    root.mkdir()
    (root / "comic_context.json").write_text(
        json.dumps({"title": "A comic moment", "pipeline_mode": "micro_moment"}),
        encoding="utf-8",
    )
    return "micro_paste"


def _spoken(hook, paras, outro) -> str:
    return " ".join([hook, *paras, outro])


# ── the hook is spoken once ──────────────────────────────────────────────────


def test_pasted_micro_script_speaks_its_hook_once(tmp_path, monkeypatch):
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, REAL_PASTE, log=lambda *_: None)

    texts = [scene["text"] for scene in narration["scenes"]]
    assert narration["hook"] == HOOK
    assert texts.count(HOOK) == 1
    assert [scene["is_intro"] for scene in narration["scenes"]] == [True] + [False] * 4
    assert len(texts) == 5                         # the hook plus the four story sentences
    assert narration["total_word_count"] == 87     # what the writer's own count said
    spoken = " ".join(texts)
    for audit in AUDIT_WORDS:
        assert audit not in spoken, audit


def test_a_closing_line_the_script_repeats_is_spoken_once(tmp_path, monkeypatch):
    outro = "That is how it ends."
    script = f"Hook: {HOOK}\n\n{HOOK}\n\n{BODY}\n\nOutro: {outro}\n\n{outro}"
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, script, log=lambda *_: None)

    texts = [scene["text"] for scene in narration["scenes"]]
    assert texts.count(HOOK) == 1 and texts.count(outro) == 1
    assert [scene["is_outro"] for scene in narration["scenes"]][-1] is True


def test_a_different_first_paragraph_is_not_taken_for_the_hook():
    hook, paras, outro = gp._split_free_script(f"CHOSEN HOOK: {HOOK}\n\nHe fights.\n\n{BODY}")

    assert hook == HOOK
    assert paras == ["He fights.", BODY]
    assert outro == ""


# ── the audit block ends the script wherever Gemini puts it ──────────────────


def _template_audit_lines() -> list[tuple[str, str]]:
    """(label, printed line) for every audit heading the micro template's OUTPUT block prints
    after FINAL SCRIPT, with its <placeholders> filled in. Read from the template itself, so
    a heading added there later is held to the same rule without touching this test."""
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    block = prompt.split("OUTPUT EXACTLY", 1)[1].splitlines()
    found = []
    for line in block[block.index("FINAL SCRIPT") + 1:]:
        label = re.match(r"^([A-Z][A-Z ]*[A-Z])\s*(?::|$)", line)
        if label:
            found.append((label.group(1), re.sub(r"<[^>]*>", "filler", line)))
    return found


TEMPLATE_AUDIT_LINES = _template_audit_lines()


def test_the_template_audit_headings_were_found():
    labels = {label for label, _line in TEMPLATE_AUDIT_LINES}
    assert {"SPOKEN WORD COUNT", "FACT TRACE", "OPENING CHAIN", "STORY FRAME CHECK",
            "AFTERMATH CHECK", "OPEN LOOPS", "REACTIONS", "UNSUPPORTED FACTS"} <= labels


@pytest.mark.parametrize("label,line", TEMPLATE_AUDIT_LINES,
                         ids=[label for label, _line in TEMPLATE_AUDIT_LINES])
def test_every_audit_heading_the_template_prints_ends_the_script(label, line):
    """Gemini sometimes reorders the block, so any heading can follow the story directly."""
    raw = f"FINAL SCRIPT\n{HOOK}\n\n{BODY}\n\n{line}\nLEAKED sentence after the heading.\n"

    hook, paras, outro = gp._split_free_script(raw)

    assert hook == HOOK
    assert paras == [BODY]
    assert outro == ""
    spoken = _spoken(hook, paras, outro)
    assert "filler" not in spoken and "LEAKED" not in spoken


AUDIT_LABELS_AFTER_THE_SCRIPT = ("OPENING CHAIN", "STORY FRAME CHECK", "AFTERMATH CHECK",
                                 "OPEN LOOPS", "REACTIONS", "UNSUPPORTED FACTS")


@pytest.mark.parametrize("shape", [
    "{label}: B1 used in S2",
    "**{label}:** none",
    "**{label}**: none",
    "### {label}",
    "- **{label}:** none",
    "{label}:",
    "{label}",
])
def test_audit_heading_ends_the_script_however_it_is_formatted(shape):
    """Upper, title and lower case; plain, bold, bulleted and markdown-heading shapes."""
    for label in AUDIT_LABELS_AFTER_THE_SCRIPT:
        for case in (str.upper, str.title, str.lower):
            line = shape.format(label=case(label))
            raw = f"FINAL SCRIPT\n{HOOK}\n\n{BODY}\n\n{line}\nLEAKED sentence after the heading.\n"

            result = gp._split_free_script(raw)

            assert result == (HOOK, [BODY], ""), line


@pytest.mark.parametrize("sentence", [
    "Reactions to the news were fierce.",
    "Open loops of cable hang from the ceiling.",
    "Opening chain after chain, the doors slam shut.",
    "Unsupported facts were the least of his worries.",
])
def test_story_sentences_that_start_like_a_heading_are_kept(sentence):
    hook, paras, outro = gp._split_free_script(f"FINAL SCRIPT\n{HOOK}\n\n{BODY}\n\n{sentence}")

    assert sentence in _spoken(hook, paras, outro)


def test_reordered_audit_block_does_not_reach_the_narration(tmp_path, monkeypatch):
    reordered = f"""FINAL SCRIPT
{HOOK}

{BODY}

OPENING CHAIN: the hook promise -> a new fact -> the next movement
STORY FRAME CHECK: B1 used in S2.
AFTERMATH CHECK: UNKNOWN — main moment expanded instead
OPEN LOOPS: none
REACTIONS: none
UNSUPPORTED FACTS: none

FACT TRACE
S1 | Captain America battled the Red Skull | B1, B2

SPOKEN WORD COUNT: 87
"""
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, reordered, log=lambda *_: None)

    assert len(narration["scenes"]) == 5
    assert narration["total_word_count"] == 87
    spoken = " ".join(scene["text"] for scene in narration["scenes"])
    for audit in AUDIT_WORDS:
        assert audit not in spoken, audit
