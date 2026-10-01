"""Tests for Stage 2 Gemini prompt generation in micro_moment mode and script anchoring."""
import json
import re
from pathlib import Path
import pytest

import stages.stage_3.gemini_prompt as gp


def _flat(text: str) -> str:
    """The template hard-wraps its sentences; compare them without the line breaks."""
    return " ".join(text.split())


def test_micro_moment_prompt_generation(tmp_path, monkeypatch):
    """Ensure micro_moment mode generates the grounded micro-moment prompt, NOT the Q&A prompt."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "batman_test"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "Batman #5 (2026)",
        "series": "Batman",
        "issue": "5",
        "year": "2026",
        "pipeline_mode": "micro_moment",
        "characters": ["Batman / Bruce Wayne", "Annika Zeller"],
        "target_moment": "Batman is trapped in a collapsing vault and Annika reveals her true identity.",
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    candidate = {
        "character": "Batman / Bruce Wayne",
        "series_issue_year": "Batman #5 (2026)",
        "what_visibly_happens": "Batman is trapped in a collapsing vault and Annika reveals her true identity.",
        "summary": "Dramatic confrontation in the underground vault.",
        "claim_citation": {
            "title": "CBR Review",
            "url": "https://www.cbr.com/batman-5-review/",
            "quote": "The twist ending redefines Annika's motives completely."
        },
        "verbatim_sentence": "The twist ending redefines Annika's motives completely.",
        "source_url": "https://www.cbr.com/batman-5-review/",
        "evidence_urls": ["https://www.cbr.com/batman-5-review/"],
        "verdict": "CONFIRMED"
    }
    (proj_dir / "scout_candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")

    prompt_text, file_path = gp.generate_gemini_writer_prompt("batman_test")

    assert file_path.exists()
    assert "GRIMFRAME MICRO-MOMENT WRITER (GROUNDED)" in prompt_text
    assert "THE QUESTION:" not in prompt_text
    assert "Batman / Bruce Wayne" in prompt_text
    assert "Batman is trapped in a collapsing vault" in prompt_text
    assert "The twist ending redefines Annika's motives completely." in prompt_text
    assert "CONFIRMED" in prompt_text
    assert "HOOK AND FIRST BEAT" in prompt_text
    assert "A question is allowed" in " ".join(prompt_text.split())
    assert "OPENING CHAIN" in prompt_text
    assert "same concrete person-and-event promise" in prompt_text
    assert "STORY FRAME if verified" in prompt_text


def test_micro_prompt_requires_verified_story_frame_without_padding():
    """A stunt alone must not be mistaken for the situation that led to it."""
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    phase1, phase2 = prompt.split("# PHASE 2 — WRITE THE SHORT", 1)
    assert "STORY FRAME" in phase1
    assert "what immediate conflict or goal is active" in " ".join(phase1.split())
    assert "STORY FRAME CHECK" in phase2
    assert "replace repetition" in phase2
    assert "adding a verified story frame does not create a longer word target" in " ".join(phase2.split()).lower()
    assert "no beat quota" in phase1.lower()


# generate_gemini_writer_prompt string-replaces these two; an edit that breaks either one
# silently stops the moment and the scout JSON from reaching Gemini.
_MICRO_PLACEHOLDERS = (
    "<<one line: character — what happens — series, volume, #issue (year)>>",
    "<<paste the object here>>",
)
# Phrases the tests above, the importer and the writer's own contract rely on.
_MICRO_PINNED_PHRASES = (
    "GRIMFRAME MICRO-MOMENT WRITER (GROUNDED)",
    "HOOK AND FIRST BEAT",
    "A question is allowed",
    "same concrete person-and-event promise",
    "STORY FRAME if verified",
    "# PHASE 2 — WRITE THE SHORT",
    "STORY FRAME CHECK",
    "replace repetition",
    "no beat quota",
    "adding a verified story frame does not create a longer word target",
)


def test_micro_template_keeps_its_placeholders_and_pinned_phrases():
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder in _MICRO_PLACEHOLDERS:
        assert prompt.count(placeholder) == 1, placeholder
    flat = _flat(prompt)
    for phrase in _MICRO_PINNED_PHRASES:
        assert phrase in flat, phrase


def test_micro_template_never_ends_on_a_teaser_and_tells_how_it_ends():
    """A reviewer's reaction to a twist the review withheld ("a shocking twist I never saw
    coming") was rewritten by the scout as an event and became the script's last line. The
    template now treats such lines as research questions, asks for what happens next and
    what set the moment up, and bans any sentence that hints at an outcome it never states."""
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    phase1, phase2 = prompt.split("# PHASE 2 — WRITE THE SHORT", 1)
    flat1, flat2 = _flat(phase1), _flat(phase2)

    # PHASE 1: the scout's new fields are leads, teasers are not beats, two new questions.
    for field in ("scout_check", "aftermath", "context_behind", "detail_citations", "unrevealed"):
        assert f"`{field}`" in flat1, field
    assert "## Reactions and teasers are not beats" in phase1
    assert "never grounds for implying an outcome" in flat1
    assert "## What happens next, and what set it up" in phase1
    assert "never manufacture an outcome or a backstory" in flat1
    for sheet_line in ("B3 | AFTERMATH:", "B4 | CONTEXT:", "AFTERMATH |", "CONTEXT BEHIND |",
                       "OPEN QUESTIONS |"):
        assert sheet_line in phase1, sheet_line

    # PHASE 2: carry the story through the aftermath, no teasers, check both in the audit.
    assert "NO TEASERS" in phase2
    assert "Nothing listed under OPEN QUESTIONS may appear in any wording" in flat2
    assert "If the only way to end is a tease, end one sentence earlier" in flat2
    assert "carry the story through them" in flat2
    assert "CONTEXT BEHIND: use it only when a sourced fact makes the moment clearer" in flat2
    assert "in story order: setup, the act, what it leads to, how it ends" in flat2
    assert "The final sentence states a concrete verified outcome" in flat2
    assert "Verified AFTERMATH beats are distinct events" in flat2
    assert "If not, it is a teaser. Delete it, and do not merely list it." in flat2


def test_micro_template_no_longer_ends_on_a_withheld_payoff_by_default():
    """The old landing rule ("strongest verified detail", "do not tease a payoff") let the
    teaser through as that detail. The detail is now only the fallback when no aftermath
    is sourced."""
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    phase2 = _flat(prompt.split("# PHASE 2 — WRITE THE SHORT", 1)[1])
    assert "Do not tease a payoff that the sheet lacks." not in phase2
    assert "the verified outcome when AFTERMATH exists" in phase2
    assert "otherwise the strongest verified result or detail of the moment" in phase2
    assert "When it has none, spend the words on the main moment instead" in phase2


def test_micro_output_block_audits_aftermath_and_open_loops():
    prompt = gp.MICRO_TEMPLATE_PATH.read_text(encoding="utf-8")
    lines = prompt.split("OUTPUT EXACTLY", 1)[1].splitlines()
    after_script = lines[lines.index("FINAL SCRIPT") + 1:]
    labels = []
    for line in after_script:
        label = re.match(r"^([A-Z][A-Z ]+[A-Z])\s*(?::|$)", line)
        if label:
            labels.append(label.group(1))

    assert labels[0] == "SPOKEN WORD COUNT"          # still the first block after the script
    order = [labels.index(name) for name in (
        "STORY FRAME CHECK", "AFTERMATH CHECK", "OPEN LOOPS", "REACTIONS", "UNSUPPORTED FACTS")]
    assert order == sorted(order)
    assert any(line.strip() == "OPEN LOOPS: none" for line in after_script)


def test_qa_template_ends_every_item_on_what_happened():
    """The same rule for Q&A: an item paragraph may not end on a hint it never explains."""
    qa = gp.QA_TEMPLATE_PATH.read_text(encoding="utf-8")
    paragraph = next(p for p in qa.split("\n\n") if "announces that a twist is coming" in p)
    assert ("End every item paragraph on what actually happened, never on a hint of "
            "something the paragraph does not explain.") in _flat(paragraph)


def test_micro_moment_script_can_be_approved_before_pages_exist(tmp_path, monkeypatch):
    """Stage 2 must save a draft before Download and Preprocess create any page files."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    import stages.stage_3.pipeline as pl
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    project = tmp_path / "micro_before_download"
    project.mkdir()
    (project / "comic_context.json").write_text(
        json.dumps({"title": "A comic moment", "pipeline_mode": "micro_moment"}),
        encoding="utf-8",
    )

    narration = gp.parse_and_save_script(
        "micro_before_download",
        "A hero stops the machine.\n\nThe machine starts to fall, and the hero holds it long enough for everyone to leave. The hero then lets it go.",
        log=lambda _message: None,
    )

    assert (project / "narration.json").exists()
    assert narration["scenes"]
    assert {scene["page_ref"] for scene in narration["scenes"]} == {1}

    prep = project / "preprocessed"
    prep.mkdir()
    for number in (2, 3, 4):
        (prep / f"page_{number:03d}.json").write_text(json.dumps({
            "page_number": number, "is_story_page": True,
        }), encoding="utf-8")

    assert gp.reanchor_narration_to_pages("micro_before_download", log=lambda _message: None)
    updated = json.loads((project / "narration.json").read_text(encoding="utf-8"))
    assert updated["scenes"][0]["page_ref"] == 2
    assert all(scene["page_ref"] > 1 for scene in updated["scenes"])
    assert [beat["page_refs"] for beat in updated["beats"]] == [
        [scene["page_ref"]] for scene in updated["scenes"]
    ]


def test_micro_moment_reanchor_narration(tmp_path, monkeypatch):
    """Ensure reanchor_narration_to_pages spreads beats across preprocessed story pages."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    import stages.stage_3.pipeline as pl
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "micro_reanchor_test"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "Batman #5",
        "pipeline_mode": "micro_moment",
        "characters": ["Batman"],
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx), encoding="utf-8")

    # Initial script parsed before downloading pages (all default to page 1)
    narration_data = {
        "mode": "micro_moment",
        "total_word_count": 120,
        "scenes": [
            {"scene_id": 1, "text": "Hook line.", "page_ref": 1, "is_intro": True},
            {"scene_id": 2, "text": "First body beat.", "page_ref": 1},
            {"scene_id": 3, "text": "Second body beat.", "page_ref": 1},
            {"scene_id": 4, "text": "Third body beat.", "page_ref": 1},
            {"scene_id": 5, "text": "Outro punchline.", "page_ref": 1, "is_outro": True},
        ],
        "beats": [
            {"id": 1, "voiceover": "Hook line.", "page_refs": [1]},
            {"id": 2, "voiceover": "First body beat.", "page_refs": [1]},
            {"id": 3, "voiceover": "Second body beat.", "page_refs": [1]},
            {"id": 4, "voiceover": "Third body beat.", "page_refs": [1]},
            {"id": 5, "voiceover": "Outro punchline.", "page_refs": [1]},
        ]
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration_data, indent=2), encoding="utf-8")

    # Mock preprocessed/page_*.json with pages 1..10 (page 1 is cover, 2..9 are story, 10 is ad)
    prep_dir = proj_dir / "preprocessed"
    prep_dir.mkdir()
    pages = [
        {"page_number": 1, "page_type": "cover", "is_story_page": False},
        {"page_number": 2, "page_type": "story", "is_story_page": True},
        {"page_number": 3, "page_type": "story", "is_story_page": True},
        {"page_number": 4, "page_type": "story", "is_story_page": True},
        {"page_number": 5, "page_type": "story", "is_story_page": True},
        {"page_number": 6, "page_type": "story", "is_story_page": True},
        {"page_number": 7, "page_type": "story", "is_story_page": True},
        {"page_number": 8, "page_type": "story", "is_story_page": True},
        {"page_number": 9, "page_type": "story", "is_story_page": True},
        {"page_number": 10, "page_type": "ad", "is_story_page": False},
    ]
    for p in pages:
        (prep_dir / f"page_{p['page_number']:03d}.json").write_text(json.dumps(p), encoding="utf-8")

    # Run reanchor
    ok = gp.reanchor_narration_to_pages("micro_reanchor_test")
    assert ok is True

    updated_nar = json.loads((proj_dir / "narration.json").read_text(encoding="utf-8"))
    scenes = updated_nar["scenes"]
    beats = updated_nar["beats"]

    # Intro should anchor to first story page (2)
    assert scenes[0]["page_ref"] == 2
    assert beats[0]["page_refs"] == [2]

    # Outro should anchor to last story page (9)
    assert scenes[-1]["page_ref"] == 9
    assert beats[-1]["page_refs"] == [9]

    # Body scenes should be spread across story pages, not stuck on 1
    for s in scenes:
        assert s["page_ref"] >= 2
        assert s["page_ref"] <= 9
    for b in beats:
        assert b["page_refs"][0] >= 2
        assert b["page_refs"][0] <= 9


# ── the scout's verification and aftermath leads reach the writer ────────────
# The writer prompt reads these (INPUT section) and project_factory stores them under exactly
# these names, but the places that rebuild a candidate used to copy only nine fixed keys.

BASE_CANDIDATE = {
    "character": "Steve Rogers",
    "series_issue_year": "Captain America #1 (2026)",
    "what_visibly_happens": "Steve hurls a shield that makes evil explode.",
    "summary": "Steve fights to topple a ruler of Hell.",
    "claim_citation": {"title": "Review", "url": "https://example.com/review",
                       "quote": "Steve hurls the shield."},
    "verbatim_sentence": "Steve hurls the shield.",
    "source_url": "https://example.com/review",
    "evidence_urls": ["https://example.com/review"],
    "verdict": "CONFIRMED",
}
SCOUT_EXTRAS = {
    "turning_point": "Steve hurls the shield at the Skull.",
    "why_it_lands": "A weapon that makes evil explode changes the fight.",
    "aftermath": "The fight ends when the Skull's power collapses.",
    "context_behind": "Steve went into Hell to topple its ruler.",
    "unrevealed": ["who is at the center of the final twist"],
    "detail_citations": [{"supports": "aftermath", "url": "https://example.com/recap",
                          "quote": "The Skull's power collapses."}],
    "reason": "The review describes the throw and what it does.",
    "scout_check": "Quote found on the cited page; issue number matches.",
}


def _scout_json_in(prompt_text: str) -> dict:
    marker = "SCOUT JSON — the single confirm object from stages/youcom_scout:\n\n"
    body = prompt_text.split(marker, 1)[1].split("\n\nThe scout is a lead", 1)[0]
    return json.loads(body)


def _micro_project(tmp_path, monkeypatch, *, context=None):
    import config
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(config, "RESEARCH_SESSIONS_ROOT", tmp_path / "no_sessions")
    root = tmp_path / "scout_extras"
    root.mkdir()
    ctx = {"title": "Captain America #1 (2026)", "pipeline_mode": "micro_moment",
           "target_moment": "Steve hurls a shield that makes evil explode.",
           "plot_summary": "Steve fights to topple a ruler of Hell.", **(context or {})}
    (root / "comic_context.json").write_text(json.dumps(ctx), encoding="utf-8")
    return root


def _research_session(tmp_path, monkeypatch, root, *, candidate, gate):
    """A scout session that created this project; the candidate is found through it."""
    import config
    sessions = tmp_path / "research_sessions"
    monkeypatch.setattr(config, "RESEARCH_SESSIONS_ROOT", sessions)
    (sessions / "s1" / "general").mkdir(parents=True)
    (sessions / "s1" / "specific").mkdir(parents=True)
    (sessions / "s1" / "session.json").write_text(json.dumps({
        "id": "s1", "created_project": root.name,
        "selected_specific_candidate_ids": [candidate["id"]]}), encoding="utf-8")
    (sessions / "s1" / "general" / "candidates.v1.json").write_text(
        json.dumps({"candidates": [candidate]}), encoding="utf-8")
    (sessions / "s1" / "specific" / "evidence_gate.v1.json").write_text(
        json.dumps({"gates": [gate]}), encoding="utf-8")


def test_scout_extras_reach_the_prompt_from_the_stored_candidate(tmp_path, monkeypatch):
    root = _micro_project(tmp_path, monkeypatch)
    (root / "scout_candidate.json").write_text(
        json.dumps({**BASE_CANDIDATE, **SCOUT_EXTRAS}), encoding="utf-8")

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    assert _scout_json_in(prompt) == {**BASE_CANDIDATE, **SCOUT_EXTRAS}
    assert "<<" not in prompt                       # both placeholders were filled


def test_scout_extras_reach_the_prompt_from_the_candidate_in_comic_context(tmp_path, monkeypatch):
    root = _micro_project(tmp_path, monkeypatch,
                          context={"scout_candidate": {**BASE_CANDIDATE, **SCOUT_EXTRAS}})

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    assert _scout_json_in(prompt) == {**BASE_CANDIDATE, **SCOUT_EXTRAS}


def test_scout_extras_survive_rebuilding_the_candidate_from_its_session(tmp_path, monkeypatch):
    """No stored candidate: it is rebuilt from the scout session's candidate and gate. The
    gate's `reason` explains the verdict, so it wins over a `reason` on the candidate."""
    root = _micro_project(tmp_path, monkeypatch)
    candidate = {"id": "c1", "character": "Steve Rogers",
                 "series_issue_year": "Captain America #1 (2026)",
                 "what_visibly_happens": "Steve hurls a shield that makes evil explode.",
                 "summary": "Steve fights to topple a ruler of Hell.",
                 **{key: value for key, value in SCOUT_EXTRAS.items()
                    if key not in ("reason", "scout_check")},
                 "reason": "the candidate's own reasoning"}
    gate = {"verdict": "confirmed", "reason": SCOUT_EXTRAS["reason"],
            "scout_check": SCOUT_EXTRAS["scout_check"]}
    _research_session(tmp_path, monkeypatch, root, candidate=candidate, gate=gate)

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    rebuilt = _scout_json_in(prompt)
    assert {key: rebuilt[key] for key in SCOUT_EXTRAS} == SCOUT_EXTRAS
    assert rebuilt["verdict"] == "CONFIRMED"
    stored = json.loads((root / "scout_candidate.json").read_text(encoding="utf-8"))
    assert {key: stored[key] for key in SCOUT_EXTRAS} == SCOUT_EXTRAS     # kept for the next run


def test_scout_extras_survive_the_fallback_rebuild(tmp_path, monkeypatch):
    """A scout_candidate in the context that has neither the moment nor the summary is not
    used as is; the fallback rebuilds from the context but keeps what the scout recorded."""
    partial = {"character": "Steve Rogers", **SCOUT_EXTRAS}
    root = _micro_project(tmp_path, monkeypatch, context={"scout_candidate": partial})

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    rebuilt = _scout_json_in(prompt)
    assert {key: rebuilt[key] for key in SCOUT_EXTRAS} == SCOUT_EXTRAS
    assert rebuilt["what_visibly_happens"] == "Steve hurls a shield that makes evil explode."


def test_absent_or_empty_scout_extras_stay_out_of_the_prompt(tmp_path, monkeypatch):
    """An older candidate, or a scout that found nothing for a field, adds no empty keys."""
    root = _micro_project(tmp_path, monkeypatch)
    candidate = {"id": "c1", "character": "Steve Rogers",
                 "series_issue_year": "Captain America #1 (2026)",
                 "what_visibly_happens": "Steve hurls a shield that makes evil explode.",
                 "summary": "Steve fights to topple a ruler of Hell.",
                 "aftermath": "", "unrevealed": [], "detail_citations": [],
                 "context_behind": "   ", "scout_check": None}
    _research_session(tmp_path, monkeypatch, root, candidate=candidate,
                      gate={"verdict": "CONFIRMED"})

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    rebuilt = _scout_json_in(prompt)
    assert set(rebuilt) == set(BASE_CANDIDATE)


def test_fallback_without_a_scout_candidate_adds_no_extras(tmp_path, monkeypatch):
    root = _micro_project(tmp_path, monkeypatch)

    prompt, _path = gp.generate_gemini_writer_prompt(root.name)

    assert set(_scout_json_in(prompt)) == set(BASE_CANDIDATE)
