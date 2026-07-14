"""Q&A subject-panel ranking (stages/subject_panels.py) + the Stage 5 multi-panel
subject INTRO it feeds.

A Q&A Short answers ONE question about ONE famous character but its answer ITEMS name
DIFFERENT characters — the intro/outro must feature the QUESTION'S subject. This covers:
subject derivation from the question, panel scoring, the manual-override guard (a hand-
written file is never overwritten), Stage 5's file→pool resolution with no-reuse, and the
multi-panel intro built from the top-N ranked subject panels (with the pre-file fallback)."""
import json

import config
from stages import subject_panels as sp
from stages.stage_5 import shots


# ── subject derivation ───────────────────────────────────────────────────────

def test_derive_subject_single_name():
    ac = {"question": "Who has actually stopped the unstoppable Juggernaut?",
          "items": [{"how_or_why": "Colossus dropped the Juggernaut off a cliff."}]}
    assert sp.derive_subject(ac) == "Juggernaut"


def test_derive_subject_possessive_splits_and_leader_wins_tie():
    """"Ghost Rider's Penance Stare" → two candidates; both appear in every item, so the
    tie breaks toward the phrase that LEADS the question (the possessor character)."""
    ac = {"question": "Who has survived Ghost Rider's Penance Stare?", "items": [
        {"how_or_why": "Ghost Rider hit him with the Penance Stare."},
        {"how_or_why": "Ghost Rider unleashed the Penance Stare again."},
    ]}
    assert sp.derive_subject(ac) == "Ghost Rider"


def test_derive_subject_strips_title_word():
    ac = {"question": "Who has actually beaten the real Doctor Doom?",
          "items": [{"how_or_why": "Doom lost the fight."}]}
    assert sp.derive_subject(ac) == "Doom"          # "Doctor" is a stop-word


def test_derive_subject_none_when_no_capitalized_name():
    assert sp.derive_subject({"question": "why is he so angry", "items": []}) == ""
    assert sp.derive_subject({}) == ""


# ── panel scoring ─────────────────────────────────────────────────────────────

def test_score_panels_weights_and_story_filter():
    pages = [
        {"page_number": 5, "is_story_page": True, "panels": [
            {"bbox": {"w": 100, "h": 100}, "characters": ["Doom"],           # 3
             "description": "Doom on a throne", "dialog": [{"ocr": "I am Doom"}]},  # +2 +1 = 6
            {"bbox": {"w": 900, "h": 900}, "characters": ["a knight"],
             "description": "Doom looms over the city", "dialog": []},        # 2 (big)
            {"bbox": {"w": 50, "h": 50}, "characters": ["a crowd"], "description": "an empty plaza"},  # 0
        ]},
        {"page_number": 6, "is_story_page": False, "panels": [
            {"bbox": {"w": 999, "h": 999}, "characters": ["Doom"], "description": "cover art"}]},
    ]
    ranked = sp.score_panels("Doom", pages)
    assert [(r["page"], r["panel"]) for r in ranked] == [(5, 0), (5, 1)]   # score first, cover skipped
    assert ranked[0]["score"] == 6.0 and ranked[1]["score"] == 2.0
    assert all("_area" not in r for r in ranked)                            # scratch key stripped


def test_score_panels_ties_break_on_area():
    pages = [{"page_number": 1, "is_story_page": True, "panels": [
        {"bbox": {"w": 100, "h": 100}, "description": "Doom small"},
        {"bbox": {"w": 800, "h": 800}, "description": "Doom big"},
    ]}]
    ranked = sp.score_panels("Doom", pages)
    assert [r["panel"] for r in ranked] == [1, 0]        # equal score → bigger panel first


# ── build_subject_panels: write + manual override ─────────────────────────────

def _answer_ctx():
    return {"question": "Who has stopped the unstoppable Juggernaut?",
            "items": [{"how_or_why": "Colossus beat the Juggernaut."}]}


def _pages():
    return [{"page_number": 3, "is_story_page": True, "panels": [
        {"bbox": {"w": 500, "h": 500}, "characters": ["Juggernaut"], "description": "Juggernaut charges"}]}]


def test_build_subject_panels_writes_ranked_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    out = sp.build_subject_panels("qa", _answer_ctx(), _pages(), log=lambda *_: None)
    data = json.loads(out.read_text())
    assert data["subject"] == "Juggernaut"
    assert data["panels"] == [{"page": 3, "panel": 0, "score": 5.0}]


def test_build_subject_panels_respects_manual_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    hand = {"manual": True, "subject": "Juggernaut", "panels": [{"page": 99, "panel": 7, "score": 1.0}]}
    (tmp_path / "qa" / "subject_panels.json").write_text(json.dumps(hand))
    out = sp.build_subject_panels("qa", _answer_ctx(), _pages(), log=lambda *_: None)
    assert json.loads(out.read_text()) == hand            # untouched — Master's picks win


# ── build_subject_panels: candidate-list fallback (2026-07-09 "Batcave" bug) ──────────

def _answer_ctx_location_subject():
    """The question's only leftover capitalized phrase is a LOCATION ("Lair") that no
    panel names — mirrors the real "Who Has Actually Broken Into the Batcave?" bug where
    derive_subject picked the place, not the recurring character (Batman)."""
    return {"question": "Who Has Actually Broken Into the Lair?", "items": [
        {"how_or_why": "Batman fought off the intruder inside the Lair."},
        {"how_or_why": "Batman chased the second intruder through the Lair."},
    ]}


def test_build_subject_panels_falls_back_to_item_character_when_question_subject_has_no_panels(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    pages = [{"page_number": 4, "is_story_page": True, "panels": [
        {"bbox": {"w": 500, "h": 500}, "characters": ["Batman"], "description": "Batman stands guard"}]}]
    logs: list[str] = []
    out = sp.build_subject_panels("qa", _answer_ctx_location_subject(), pages, log=logs.append)
    data = json.loads(out.read_text())
    assert data["subject"] == "Batman"                     # "Lair" (0 panels) → fallback to the
    assert data["panels"]                                  # character every item keeps naming
    assert any("subject='Lair' → 0 panels, fallback → 'Batman'" in m for m in logs)


def test_build_subject_panels_all_candidates_miss_keeps_empty_panels(tmp_path, monkeypatch):
    """When NO candidate (question phrase or item character) matches any panel, keep the
    original behavior: write the primary (derive_subject) candidate with empty panels
    instead of silently skipping — Stage 5 still sees a subject file, just with no shots."""
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    ac = {"question": "Who Has Actually Broken Into the Lair?",
          "items": [{"how_or_why": "Batman fought off the intruder inside the Lair."}]}
    pages = [{"page_number": 1, "is_story_page": True, "panels": [
        {"bbox": {"w": 10, "h": 10}, "characters": ["a stranger"], "description": "an empty hallway"}]}]
    out = sp.build_subject_panels("qa", ac, pages, log=lambda *_: None)
    data = json.loads(out.read_text())
    assert data["subject"] == "Lair"                       # primary candidate kept, no silent skip
    assert data["panels"] == []


# ── Stage 5: file → pool resolution with no-reuse ─────────────────────────────

def test_qa_subject_sequence_resolves_in_rank_order_and_excludes(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    (tmp_path / "qa" / "subject_panels.json").write_text(json.dumps({"subject": "Doom", "panels": [
        {"page": 5, "panel": 0, "score": 9.0},
        {"page": 8, "panel": 0, "score": 8.0},     # excluded: locked to a body beat
        {"page": 5, "panel": 1, "score": 7.0},
        {"page": 4, "panel": 9, "score": 6.0},     # dropped: not in the pool
    ]}))
    entry_by_key = {(5, 0): ({"id": "a"}, "p5"), (5, 1): ({"id": "b"}, "p5"), (8, 0): ({"id": "x"}, "p8")}
    seq = shots._qa_subject_sequence("qa", entry_by_key, exclude={(8, 0)})
    assert [pair[0]["id"] for pair in seq] == ["a", "b"]   # rank order, body-locked + off-pool gone


def test_qa_subject_sequence_empty_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    assert shots._qa_subject_sequence("qa", {(5, 0): ({}, "p5")}, exclude=set()) == []


def test_qa_subject_sequence_force_intro_bypasses_exclude(tmp_path, monkeypatch):
    """Money-shot intro-pin bug (Thor mjolnir-shattered, 2026-07-11): the confirmed money
    panel is ALSO locked to its own body beat, so plain no-reuse dropped it from the intro
    and a weaker panel led instead. `force_intro: true` (set by `_pin_money_intro`) must
    bypass the exclude — the ComicCut hook formula allows the payoff panel to spoiler the
    intro AND still play its body beat. A row with NO force_intro flag keeps the old
    no-reuse behavior untouched (regression guard)."""
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "qa").mkdir()
    (tmp_path / "qa" / "subject_panels.json").write_text(json.dumps({"subject": "Thor", "panels": [
        {"page": 10, "panel": 2, "score": 9.5, "money": True, "force_intro": True},  # (a) locked
        {"page": 8, "panel": 0, "score": 8.0},                                      # (b) locked, no flag
        {"page": 5, "panel": 1, "score": 7.0},                                      # not locked
    ]}))
    entry_by_key = {
        (10, 2): ({"id": "money"}, "p10"),
        (8, 0): ({"id": "x"}, "p8"),
        (5, 1): ({"id": "b"}, "p5"),
    }
    body_locked = {(10, 2), (8, 0)}   # both panels ALSO claimed by a body beat
    seq = shots._qa_subject_sequence("qa", entry_by_key, exclude=body_locked)
    # (a) force_intro survives the exclude, and leads (pinned first in the file);
    # (b) the plain locked panel (8,0) is still dropped — no-reuse unchanged for everyone else.
    assert [pair[0]["id"] for pair in seq] == ["money", "b"]


# ── Stage 5: multi-panel subject intro end to end ─────────────────────────────

import stages._embedding as _embedding             # noqa: E402
import stages._panel_index as _panel_index          # noqa: E402
import stages.review_gate as _review_gate            # noqa: E402


def _panel_y(y, desc="x"):
    return {"bbox": {"x": 0, "y": y, "w": 600, "h": 900}, "description": desc, "characters": []}


def _pg(panels, src, w=600, h=6000):
    return {"panels": panels, "source_image": src, "image_dimensions": {"width": w, "height": h}}


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    return 0.5, 0.5


def _setup(tmp_path, monkeypatch, subject_panels_json):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(_review_gate, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(sp, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "narration.json").write_text(json.dumps({"scenes": []}))
    # body scene 2 locks page-8 panel (kept OFF the subject list so no-reuse is visible).
    (proj / "review" / "locks.json").write_text(json.dumps(
        {"approved": True, "locks": {"2": {"panels": [{"page": 8, "panel": 0}], "source": "b"}}}))
    if subject_panels_json is not None:
        (proj / "subject_panels.json").write_text(json.dumps(subject_panels_json))
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(_panel_index, "load_vectors", lambda project: {})
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "_blend_image_content", lambda *a, **k: None)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    # intro/outro are fallback units → _match_panels runs for them; the intro is overridden by
    # subject panels and the outro by the subject sequence, so a null pair here is fine.
    monkeypatch.setattr(shots, "_match_panels", lambda units, *a, **k: [(None, "")] * len(units))
    return "qa"


_NARR = {"scenes": [
    {"scene_id": 1, "is_intro": True, "text": "hook"},
    {"scene_id": 2, "text": "body"},
    {"scene_id": 3, "is_outro": True, "text": "end"},
]}
_TIMINGS = [{"scene_id": 1, "start": 0.0, "end": 6.0},
            {"scene_id": 2, "start": 6.0, "end": 8.0},
            {"scene_id": 3, "start": 8.0, "end": 10.0}]
# 3 intro chunks (→ K=3), one body, one outro.
_CHUNKS = [{"text": "a", "start": 0.0, "end": 2.0}, {"text": "b", "start": 2.0, "end": 4.0},
           {"text": "c", "start": 4.0, "end": 6.0}, {"text": "body", "start": 6.0, "end": 8.0},
           {"text": "end", "start": 8.0, "end": 10.0}]
# 4 ranked subject panels on page 5 (top-3 → intro, 4th → outro); page 8 = body lock.
_PAGES = {5: _pg([_panel_y(0), _panel_y(900), _panel_y(1800), _panel_y(2700)], "p5.png"),
          8: _pg([_panel_y(0, "body")], "p8.png", h=900)}


def test_intro_builds_multi_panel_subject_sequence(tmp_path, monkeypatch):
    # This test targets the subject-outro WIRING in isolation. SEAMLESS_LOOP (default ON)
    # would run as build_shots' last step and overwrite the outro with the intro's cold-open
    # panel — the documented, desired loop behavior (see test_outro_loop.py's
    # test_close_loop_overwrites_subject_panel_outro), but not what THIS test is checking.
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    slug = _setup(tmp_path, monkeypatch, {"subject": "Doom", "panels": [
        {"page": 5, "panel": 0, "score": 9.0}, {"page": 5, "panel": 1, "score": 8.0},
        {"page": 5, "panel": 2, "score": 7.0}, {"page": 5, "panel": 3, "score": 6.0}]})
    built = shots.build_shots(_NARR, scene_timings=_TIMINGS, caption_chunks=_CHUNKS,
                              pages_by_number=_PAGES, project=slug)
    intro = [s for s in built if s.is_intro]
    assert len(intro) == 3                                        # top-3 subject panels
    assert [s.panel_bbox["y"] for s in intro] == [0, 900, 1800]   # rank order
    assert all(s.source_image == "p5.png" and s.motion == "zoom_in" for s in intro)
    assert built[-1].panel_bbox["y"] == 2700                      # outro = NEXT unused subject panel


def test_intro_single_shot_when_no_subject_file(tmp_path, monkeypatch):
    """No subject_panels.json → the sequence is empty and the intro falls back to a single
    held shot (legacy behavior), proving the feature never breaks a pre-file project."""
    slug = _setup(tmp_path, monkeypatch, None)
    built = shots.build_shots(_NARR, scene_timings=_TIMINGS, caption_chunks=_CHUNKS,
                              pages_by_number=_PAGES, project=slug)
    assert len([s for s in built if s.is_intro]) == 1            # one held intro, not multi-panel
