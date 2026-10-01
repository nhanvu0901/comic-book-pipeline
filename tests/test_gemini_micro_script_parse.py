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


# ── the story does not say the hook again ────────────────────────────────────
# Seen 2026-10-01 probing the rewritten template with agy (Gemini 3.1 Pro): in 2 of 3 replies
# the story paragraph opened with the chosen hook again, once word for word and once with a
# few words changed. The parser only dropped a paragraph that EQUALS the hook, so the hook was
# narrated twice (75 spoken words where the reply's own count said 60). These are those two
# replies, verbatim.

PROBE_A_HOOK = "Trapped in Hell, Steve Rogers battles the Red Skull and an army of undead Nazis."
PROBE_A_REPLY = """HOOK OPTIONS
1. Steve Rogers trades his soul to the devil.
2. Trapped in Hell, Steve Rogers battles the Red Skull and an army of undead Nazis.
3. Why does Captain America surrender his soul to Mephisto?
CHOSEN HOOK: 2

FINAL SCRIPT
Trapped in Hell, Steve Rogers battles the Red Skull and an army of undead Nazis. He fights using the Antidivine, an angel-slaying sword that he transforms into a circular shield.

But realizing they can't defeat Mephisto, Steve makes a deal with the devil. Mephisto promises to end dictatorships and ensure democracies flourish in exchange for Steve's soul, which Steve accepts.

SPOKEN WORD COUNT: 60

FACT TRACE
S1 | Trapped in Hell, Steve Rogers battles the Red Skull and an army of undead Nazis. | B2
S2 | He fights using the Antidivine, an angel-slaying sword that he transforms into a circular shield. | B1
S3 | But realizing they can't defeat Mephisto, Steve makes a deal with the devil. | B3
S4 | Mephisto promises to end dictatorships and ensure democracies flourish in exchange for Steve's soul, which Steve accepts. | B4
OPENING CHAIN: Steve battles the Red Skull in Hell -> shapes the angel-slaying sword into a shield, B1 -> makes a deal with the devil, B3
STORY FRAME CHECK: B2, used in S1; no generic trait was substituted.
AFTERMATH CHECK: B3, B4, used in S3 and S4 to tell how the encounter ends.
OPEN LOOPS: none
REACTIONS: none
UNSUPPORTED FACTS: none
"""
PROBE_B_HOOK = ("Dr. Ambrose Keel's engine keeps rewriting the street grid of Lowtown, so a "
                "rescue team cannot reach the hostages.")
PROBE_B_REPLY = """HOOK OPTIONS
1. Wren Calloway tears the minute hand off a clock tower and drives it straight into an engine.
2. Dr. Ambrose Keel's engine keeps rewriting the street grid of Lowtown, so a rescue team cannot reach the hostages.
3. How do you stop a machine that keeps changing the city streets around you?

CHOSEN HOOK: 2

FINAL SCRIPT
Dr. Ambrose Keel's engine keeps rewriting the street grid of Lowtown, so Wren Calloway's rescue team cannot reach the hostages. Calloway tears the minute hand off the Halvorsen clock tower and drives it straight into the engine. With the hand lodged in its gears, the machine seizes, and every street snaps back to its original layout.

SPOKEN WORD COUNT: 56

FACT TRACE
S1 | Dr. Ambrose Keel's engine keeps rewriting the street grid of Lowtown, so Wren Calloway's rescue team cannot reach the hostages. | B2
S2 | Calloway tears the minute hand off the Halvorsen clock tower and drives it straight into the engine. | B1
S3 | With the hand lodged in its gears, the machine seizes, and every street snaps back to its original layout. | B3
OPENING CHAIN: Keel's engine rewrites the grid so the team cannot reach hostages -> Calloway drives the clock hand into the engine, B1 -> the machine seizes and the streets snap back
STORY FRAME CHECK: B2 used in S1
AFTERMATH CHECK: UNKNOWN — main moment expanded instead
OPEN LOOPS: none
REACTIONS: none
UNSUPPORTED FACTS: none
"""
# The same hook with a few words changed: what a writer prints when it "restates" it.
HOOK_REWORDED = ("Captain America fought the Red Skull in the middle of Hell with a weapon that "
                 "makes evil explode.")


def test_a_story_that_opens_on_the_hook_again_speaks_it_once(tmp_path, monkeypatch):
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, PROBE_A_REPLY, log=lambda *_: None)

    texts = [scene["text"] for scene in narration["scenes"]]
    assert narration["hook"] == PROBE_A_HOOK
    assert texts == [
        PROBE_A_HOOK,
        "He fights using the Antidivine, an angel-slaying sword that he transforms into a circular shield.",
        "But realizing they can't defeat Mephisto, Steve makes a deal with the devil.",
        "Mephisto promises to end dictatorships and ensure democracies flourish in exchange for "
        "Steve's soul, which Steve accepts.",
    ]
    assert [scene["is_intro"] for scene in narration["scenes"]] == [True, False, False, False]
    assert narration["total_word_count"] == 60     # what the reply's own count said
    spoken = " ".join(texts)
    for audit in AUDIT_WORDS:
        assert audit not in spoken, audit


def test_a_story_that_opens_on_the_hook_reworded_speaks_it_once():
    """A rescue team became "Wren Calloway's rescue team": the same sentence, said again."""
    hook, paras, outro = gp._split_free_script(PROBE_B_REPLY)

    assert hook == PROBE_B_HOOK
    assert paras == ["Calloway tears the minute hand off the Halvorsen clock tower and drives it "
                     "straight into the engine. With the hand lodged in its gears, the machine "
                     "seizes, and every street snaps back to its original layout."]
    assert outro == ""


def test_a_hook_without_labels_is_not_said_again_by_the_story():
    """Only the part under FINAL SCRIPT was pasted: the short first paragraph is the hook."""
    hook, paras, outro = gp._split_free_script(f"FINAL SCRIPT\n{HOOK}\n\n{HOOK_REWORDED} {BODY}")

    assert (hook, paras, outro) == (HOOK, [BODY], "")


def test_the_hook_said_again_is_dropped_wherever_the_closing_line_is():
    outro = "That is how it ends."

    hook, paras, closing = gp._split_free_script(
        f"CHOSEN HOOK: {HOOK}\n\n{HOOK_REWORDED} {BODY}\n\n{outro}")

    assert (hook, paras, closing) == (HOOK, [BODY], outro)


def test_a_story_made_only_of_the_hook_again_leaves_just_the_hook():
    assert gp._split_free_script(f"CHOSEN HOOK: {HOOK}\n\n{HOOK_REWORDED}") == (HOOK, [], "")


def test_the_story_line_after_a_reworded_hook_line_is_not_taken_for_the_closing():
    """The hook said again goes first, so the one line of story left is not the last of two
    short paragraphs (which the parser reads as the closing line)."""
    assert gp._split_free_script(
        f"CHOSEN HOOK: {HOOK}\n\n{HOOK_REWORDED}\n\nHe fights.") == (HOOK, ["He fights."], "")


def test_a_hook_printed_alone_is_dropped_and_the_story_keeps_its_first_sentence():
    """The exact-paragraph dedupe, which the template's "hook alone on its own line" relies on:
    the hook line goes (case and punctuation do not matter), the sentence after it stays."""
    hook, paras, outro = gp._split_free_script(
        f"CHOSEN HOOK: {HOOK}\n\nFINAL SCRIPT\n{HOOK.rstrip('.').lower()}\n\n{BODY}")

    assert (hook, paras, outro) == (HOOK, [BODY], "")


@pytest.mark.parametrize("hook,first", [
    (HOOK, "Captain America carries the Antidivine into Hell."),
    (HOOK, "The Red Skull battled Captain America in Hell."),
    ("Steve Rogers trades his soul to the devil.", "Steve Rogers saves the soul of the devil."),
    ("Why does Captain America surrender his soul to Mephisto?",
     "Captain America surrenders his soul to Mephisto."),
    ("Steve Rogers trades his soul to the devil.",
     "Steve Rogers fights the Red Skull in Hell with the Antidivine."),
])
def test_a_first_sentence_that_only_shares_words_with_the_hook_is_kept(hook, first):
    """The answer to a question hook, a sentence on the same subject, the same names in
    another act: each is new information, not the hook said again."""
    story = f"{first} Then the fight turns. The Skull falls."

    assert gp._split_free_script(f"CHOSEN HOOK: {hook}\n\n{story}") == (hook, [story], "")


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


# ── a looser reply than the template's exact shape still reads as hook + story ─
# Seen 2026-10-01 while loosening the template (the writer no longer has to print every audit line
# or the exact block shape): replies come in shapes the reader only half knew. A code fence closing
# straight after the story was read as the last words of the narration, a "Hook:" label under
# FINAL SCRIPT made the hook vanish, and a "Story:" label was spoken. The reader only needs a
# recognisable FINAL SCRIPT block, in whatever dress.

LOOSE_BODY = ("Dr. Cornelius is attempting to create new super-soldiers. To prevent this, Wolverine "
              "slashes open the reserves of liquid adamantium. The liquid encases him, leading to "
              "his death by suffocation.")
LOOSE_HOOK = "After losing his healing factor, Wolverine dies inside an adamantium cocoon."


@pytest.mark.parametrize("raw", [
    f"```\nFINAL SCRIPT\n{LOOSE_HOOK}\n\n{LOOSE_BODY}\n```\n",
    f"FINAL SCRIPT\n```\n{LOOSE_HOOK}\n\n{LOOSE_BODY}\n```\n\nSPOKEN WORD COUNT: 41\n",
    f"FINAL SCRIPT\n```text\n{LOOSE_HOOK}\n\n{LOOSE_BODY}\n```\n",
    f"FINAL SCRIPT\n~~~\n{LOOSE_HOOK}\n\n{LOOSE_BODY}\n~~~\n",
], ids=["fence closes the reply", "script fenced, audit after", "language-tagged fence", "tilde fence"])
def test_a_code_fence_around_the_script_is_not_narration(raw):
    assert gp._split_free_script(raw) == (LOOSE_HOOK, [LOOSE_BODY], "")


@pytest.mark.parametrize("raw", [
    f"FINAL SCRIPT\nHook: {LOOSE_HOOK}\n\n{LOOSE_BODY}\n",
    f"FINAL SCRIPT\n**Hook:** {LOOSE_HOOK}\n\n**Story:** {LOOSE_BODY}\n",
    f"FINAL SCRIPT\n\n### Hook\n{LOOSE_HOOK}\n\n### Story\n{LOOSE_BODY}\n",
    f"Hook: {LOOSE_HOOK}\n\nScript: {LOOSE_BODY}\n",
], ids=["Hook label", "Hook and Story labels", "headings", "no FINAL SCRIPT marker"])
def test_a_labelled_hook_and_story_read_as_hook_and_story(raw):
    """The hook line is the hook, and a label in front of the story is not spoken."""
    assert gp._split_free_script(raw) == (LOOSE_HOOK, [LOOSE_BODY], "")


def test_the_hook_survives_a_labelled_line_when_the_options_were_printed_above():
    raw = (f"HOOK OPTIONS\n1. {LOOSE_HOOK}\n2. Another opening for the same story.\nCHOSEN HOOK: 1\n\n"
           f"FINAL SCRIPT\nHook: {LOOSE_HOOK}\n\n{LOOSE_BODY}\n")
    assert gp._split_free_script(raw) == (LOOSE_HOOK, [LOOSE_BODY], "")


def test_a_script_with_no_audit_lines_at_all_becomes_narration(tmp_path, monkeypatch):
    """Nothing but the hook and the story: no word count, trace or checks. That is a complete reply."""
    name = _micro_project(tmp_path, monkeypatch)

    narration = gp.parse_and_save_script(name, f"```\nFINAL SCRIPT\n{LOOSE_HOOK}\n\n{LOOSE_BODY}\n```",
                                         log=lambda *_: None)

    texts = [scene["text"] for scene in narration["scenes"]]
    assert texts[0] == LOOSE_HOOK and narration["hook"] == LOOSE_HOOK
    assert len(texts) == 4                                  # the hook plus the three story sentences
    assert not any("`" in text for text in texts)
    assert [scene["is_intro"] for scene in narration["scenes"]] == [True, False, False, False]


@pytest.mark.parametrize("sentence", [
    "Story time was over before the sun came up.",
    "Body armor cracked under the blow.",
    "Script pages burned in the fire.",
])
def test_a_sentence_that_merely_starts_with_a_label_word_is_kept(sentence):
    """Only "Story:" / "Script:" with a colon or dash and text after it is a label."""
    assert gp._split_free_script(f"FINAL SCRIPT\n{LOOSE_HOOK}\n\n{sentence} Then he ran.") == (
        LOOSE_HOOK, [f"{sentence} Then he ran."], "")
