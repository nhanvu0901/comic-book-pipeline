"""Guards for the 2026-07-27 narration/review fixes.

1. generate_outro's fallback must still be a character-DEFINING line, never the channel
   credit — a single transient LLM error once turned a closer into "The comic is X.",
   which is the one ending this register must not have.
2. build_candidates only scopes a row's candidate pool to one issue for Q&A (where each
   countdown item cites an issue). A recap spanning issues is ONE story and must be picked
   from the whole project pool.
"""
import json

import pytest

import config
import stages.review_gate as rg
import stages.stage_5.shots as shots
from stages.stage_3.write_script import _defining_line_fallback


# ─── outro fallback ──────────────────────────────────────────────────────────

CTX = {"title": "Gambit", "characters": ["Remy LeBeau"]}


def test_fallback_closer_names_the_subject_and_is_not_the_credit():
    body = [
        {"text": "He crossed into other realities looking for a fix."},
        {"text": "He popped like a balloon and took his dead world with him."},
    ]
    line = _defining_line_fallback(body, CTX)

    assert line, "a defining line must be produced when a subject and body exist"
    assert "comic is" not in line.lower(), "the credit is the one ending this register bans"
    assert line.endswith(".")
    assert 4 <= len(line.split()) <= 14, f"outro shape broken: {line!r}"


def test_fallback_ignores_intro_and_outro_scenes():
    body = [
        {"text": "Who is he?", "is_intro": True},
        {"text": "He burned his whole planet into a star.", },
        {"text": "The comic is Gambit.", "is_outro": True},
    ]
    line = _defining_line_fallback(body, CTX)
    assert "burned" in line or "star" in line, f"should build off the last BODY beat: {line!r}"


@pytest.mark.parametrize("body", [None, [], [{"text": "", "is_intro": True}]])
def test_fallback_degrades_to_empty_without_body(body):
    """Caller keeps the factual credit rather than shipping a malformed closer."""
    assert _defining_line_fallback(body, CTX) == ""


# ─── candidate-pool scoping ──────────────────────────────────────────────────

def _page(n: int, issue: str) -> dict:
    return {
        "page_number": n, "issue_label": issue, "is_story_page": True,
        "source_image": f"/tmp/p{n}.jpg",
        "image_dimensions": {"width": 100, "height": 200},
        "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
                    "description": f"panel on page {n}", "characters": [], "dialog": []}],
        "text_blocks": [], "page_summary": "",
    }


def _project(tmp_path, slug: str, *, qa: bool, bookends: bool = False,
             qa_format: str = "list", same_anchor: bool = False) -> None:
    root = tmp_path / slug
    (root / "preprocessed").mkdir(parents=True)
    for n, issue in ((1, "#1"), (2, "#1"), (3, "#2"), (4, "#2")):
        (root / "preprocessed" / f"page_{n:03d}_x.json").write_text(json.dumps(_page(n, issue)))
    # one body scene per issue, no bookends (those always get the full pool by design)
    scenes = [
        {"scene_id": 2, "text": "first", "page_ref": 1, "panel_ref": 0},
        {"scene_id": 3, "text": "second", "page_ref": 1 if same_anchor else 3,
         "panel_ref": 0},
    ]
    if bookends:
        # A FRAGMENTED hook/closer — bookend_row_keys hands these back as unit "fragment",
        # not "intro"/"outro". page_ref 1 is the Q&A placeholder anchor (it lands in issue
        # #1), which is exactly what used to silo the whole cold open to that one issue.
        scenes.insert(0, {"scene_id": 1, "is_intro": True, "text": "hook a, and hook b",
                          "page_ref": 1, "panel_ref": -1,
                          "visual_beats": ["hook a,", "and hook b"]})
        scenes.append({"scene_id": 4, "is_outro": True, "text": "close a, and close b",
                       "page_ref": 1, "panel_ref": -1,
                       "visual_beats": ["close a,", "and close b"]})
    (root / "narration.json").write_text(json.dumps({"scenes": scenes}))
    ctx = {"plot_source": "answer_research"} if qa else {"title": "Arc"}
    (root / "comic_context.json").write_text(json.dumps(ctx))
    if qa:
        (root / "answer_context.json").write_text(json.dumps({"format": qa_format, "items": [
            {"source_comic": "A", "source_year": "2020", "reader_url": "u",
             "drawable_moment": "m", "verification_note": ""},
            {"source_comic": "B", "source_year": "2021", "reader_url": "u",
             "drawable_moment": "m", "verification_note": ""},
        ]}))


def _pools_seen(tmp_path, monkeypatch, slug: str, *, qa: bool, bookends: bool = False) -> dict:
    """{beat_key: page numbers its candidate row lists} — read back from the real
    candidates.json, i.e. the pools Master actually reviews through."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    _project(tmp_path, slug, qa=qa, bookends=bookends)
    rg.build_candidates(slug)
    beats = json.loads(
        (tmp_path / slug / "review" / "candidates.json").read_text())["beats"]
    return {b["beat_key"]: {c["page"] for c in b["candidates"]} for b in beats}


def test_recap_spanning_issues_uses_one_whole_project_pool(tmp_path, monkeypatch):
    pools = _pools_seen(tmp_path, monkeypatch, "arc_recap", qa=False)
    assert pools == {"2": {1, 2, 3, 4}, "3": {1, 2, 3, 4}}, (
        f"a recap must not be split per issue, every row sees every page, got {pools}")


def test_qa_still_scopes_each_row_to_its_cited_issue(tmp_path, monkeypatch):
    pools = _pools_seen(tmp_path, monkeypatch, "qa_arc", qa=True)
    assert pools == {"2": {1, 2}, "3": {3, 4}}, (
        f"Q&A keeps one pool per cited issue, got {pools}")


def test_direct_qa_body_uses_both_comics_despite_placeholder_page_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    _project(tmp_path, "direct_qa", qa=True, bookends=True,
             qa_format="direct", same_anchor=True)

    beats = json.loads(rg.build_candidates("direct_qa").read_text())["beats"]
    by_key = {b["beat_key"]: b for b in beats}
    for key in ("2", "3", "1:0", "1:1", "4:0", "4:1"):
        assert {c["page"] for c in by_key[key]["candidates"]} == {1, 2, 3, 4}
    assert by_key["2"]["page_ref"] == by_key["3"]["page_ref"] == 1
    assert by_key["2"]["source"]["title"] == "A"
    assert {c["panel_source"]["title"] for c in by_key["2"]["candidates"]} == {"A", "B"}


def test_list_qa_placeholder_anchor_stays_issue_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    _project(tmp_path, "list_placeholder", qa=True, same_anchor=True)
    beats = json.loads(rg.build_candidates("list_placeholder").read_text())["beats"]
    pools = {b["beat_key"]: {c["page"] for c in b["candidates"]} for b in beats}
    assert pools == {"2": {1, 2}, "3": {1, 2}}


def test_stage5_accepts_direct_qa_lock_from_another_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    root = tmp_path / "direct_lock"
    (root / "review").mkdir(parents=True)
    (root / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (root / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {
        "2:0": {"panels": [{"page": 3, "panel": 0}], "source": "batcave"},
    }}))
    narration = {"scenes": [{"scene_id": 2, "text": "one beat", "page_ref": 1,
                              "panel_ref": 0, "visual_beats": ["one beat"]}]}
    pages = {n: _page(n, issue) for n, issue in ((1, "#1"), (3, "#2"))}
    built = shots.build_shots(
        narration, scene_timings=[{"scene_id": 2, "start": 0.0, "end": 3.0}],
        caption_chunks=[{"text": "one beat", "start": 0.0, "end": 3.0}],
        pages_by_number=pages, project="direct_lock")
    assert built and all(shot.source_image == "/tmp/p3.jpg" for shot in built)


# ─── fragmented bookends keep the whole-project pool ─────────────────────────
# The "bookends always get the full pool" rule was written against unit "intro"/"outro"
# only. A hook or closer split into 2+ visual beats comes back from bookend_row_keys as
# unit "fragment" (that split is what gives each fragment its own text box), so it fell
# through to per-issue scoping — and in Q&A that pinned every cold-open fragment to
# whichever issue the intro's PLACEHOLDER page_ref happened to land in. The grouping now
# keys on the "bookend" marker the row already carries.

def test_fragmented_bookend_keeps_the_whole_project_pool(tmp_path, monkeypatch):
    pools = _pools_seen(tmp_path, monkeypatch, "qa_frag_bookend", qa=True, bookends=True)
    everything = {1, 2, 3, 4}
    for bk in ("1:0", "1:1", "4:0", "4:1"):
        assert pools[bk] == everything, (
            f"bookend fragment {bk} saw only pages {pools[bk]}")
    assert pools["2"] == {1, 2} and pools["3"] == {3, 4}, (
        f"Q&A body rows must stay scoped to their cited issue, got {pools}")
