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


def _project(tmp_path, slug: str, *, qa: bool) -> None:
    root = tmp_path / slug
    (root / "preprocessed").mkdir(parents=True)
    for n, issue in ((1, "#1"), (2, "#1"), (3, "#2"), (4, "#2")):
        (root / "preprocessed" / f"page_{n:03d}_x.json").write_text(json.dumps(_page(n, issue)))
    # one body scene per issue, no bookends (those always get the full pool by design)
    (root / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "first", "page_ref": 1, "panel_ref": 0},
        {"scene_id": 3, "text": "second", "page_ref": 3, "panel_ref": 0},
    ]}))
    ctx = {"plot_source": "answer_research"} if qa else {"title": "Arc"}
    (root / "comic_context.json").write_text(json.dumps(ctx))
    if qa:
        (root / "answer_context.json").write_text(json.dumps({"items": [
            {"source_comic": "A", "source_year": "2020", "reader_url": "u",
             "drawable_moment": "m", "verification_note": ""},
            {"source_comic": "B", "source_year": "2021", "reader_url": "u",
             "drawable_moment": "m", "verification_note": ""},
        ]}))


def _pools_seen(tmp_path, monkeypatch, slug: str, *, qa: bool) -> list[set]:
    """Page-number sets handed to the matcher — one entry per candidate GROUP."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    pools: list[set] = []

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        pools.append(set(pages))
        for _ in units:
            candidates_out.append([])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    _project(tmp_path, slug, qa=qa)
    rg.build_candidates(slug, k=5)
    return pools


def test_recap_spanning_issues_uses_one_whole_project_pool(tmp_path, monkeypatch):
    pools = _pools_seen(tmp_path, monkeypatch, "arc_recap", qa=False)
    assert len(pools) == 1, f"a recap must not be split per issue, got {len(pools)} groups"
    assert pools[0] == {1, 2, 3, 4}, f"recap rows must see every page, got {pools[0]}"


def test_qa_still_scopes_each_row_to_its_cited_issue(tmp_path, monkeypatch):
    pools = _pools_seen(tmp_path, monkeypatch, "qa_arc", qa=True)
    assert len(pools) == 2, f"Q&A keeps one group per cited issue, got {len(pools)}"
    assert sorted(sorted(p) for p in pools) == [[1, 2], [3, 4]], pools
