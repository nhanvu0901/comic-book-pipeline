"""write_explore_answer: no network — the LLM writer call and the outro/loop-
tease LLM helpers (reused from write_script.py) are monkeypatched. Verifies the
full contract from EXPLORE_ANSWER_DESIGN.md: 1:1 beat anchoring, deterministic
hook/banner, entity-named scenes, word band, and that write_script() (the
dispatch point) actually routes mode="explore_answer" here."""
import json
import shutil

import pytest

import stages.stage_3.explore_answer as ea
import stages.stage_3.write_script as ws
from config import PROJECTS_ROOT

_PROJECT = "_test_explore_answer_project"

_ANSWER_CONTEXT = {
    "items": [
        {"entity": "Wolverine", "how_or_why": "His healing factor closed every wound instantly.",
         "source_comic": "X-Men Legacy #1", "drawable_moment": "Wolverine walking through fire", "chapter_index": 1},
        {"entity": "Deadpool", "how_or_why": "His body regenerated faster than the stare could work.",
         "source_comic": "Deadpool #2", "drawable_moment": "Deadpool shrugging", "chapter_index": 2},
        {"entity": "Thanos", "how_or_why": "He simply endured the guilt without flinching.",
         "source_comic": "Thanos #3", "drawable_moment": "Thanos staring back", "chapter_index": 3},
    ]
}


def _page(page_number, chapter, page_in_chapter):
    return {
        "page_number": page_number,
        "source_image": f"ch{chapter:02d}_page{page_in_chapter:02d}.png",
        "is_story_page": True,
    }


def _fake_writer_call(*, system, user, models=None, max_tokens=2000, progress=None,
                      label="llm", validator=None):
    raw = json.dumps({"scenes": [
        {"text": "Wolverine healed through it in X-Men Legacy #1.", "connective": None, "beat_id": 1},
        {"text": "Deadpool outran it entirely in Deadpool #2.", "connective": None, "beat_id": 2},
        {"text": "Thanos just endured it in Thanos #3.", "connective": None, "beat_id": 3},
    ]})
    if validator is not None:
        assert validator(raw), "fixture scenes must pass the module's own coarse validator"
    return raw, "fake-writer-model"


def _raising_call(*args, **kwargs):
    raise RuntimeError("no network in tests")


@pytest.fixture
def project_dir():
    root = PROJECTS_ROOT / _PROJECT
    root.mkdir(parents=True, exist_ok=True)
    (root / "answer_context.json").write_text(json.dumps(_ANSWER_CONTEXT))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_write_explore_answer_end_to_end(monkeypatch, project_dir):
    # Small band so the short fixture scenes above land "in budget" without
    # needing 200 words of fixture prose.
    monkeypatch.setattr(ea, "_TARGET_WORDS_MIN", 10)
    monkeypatch.setattr(ea, "_TARGET_WORDS_MAX", 60)
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    # generate_outro / generate_loop_tease live in write_script.py and call ITS
    # call_with_chain binding — patch that one too so no real LLM/network call
    # happens; both helpers already fall back to "" on any exception, which
    # keeps the outro on the deterministic factual-credit line regardless of
    # the 50/50 coin flip, making the assertion below deterministic.
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Who has survived Ghost Rider's Penance Stare?", "is_arc": True}
    story_pages = [
        _page(1, 1, 1), _page(2, 1, 2),
        _page(3, 2, 1), _page(4, 2, 2),
        _page(5, 3, 1), _page(6, 3, 2),
    ]
    debug_dump = {"project": _PROJECT}

    nar = ws.write_script(comic_context, story_pages, "explore_answer", debug_dump=debug_dump)

    # dispatch actually routed to explore_answer (not the narrate-mode path)
    assert nar.mode == "explore_answer"
    assert nar.banner_title == comic_context["title"]
    assert nar.title == comic_context["title"]

    assert len(nar.scenes) == 5  # hook + 3 item scenes + outro
    assert nar.scenes[0].is_intro
    assert nar.scenes[-1].is_outro
    assert "Full sources" in nar.scenes[-1].text  # deterministic factual credit (LLM calls raise above)

    for scene, entity in zip(nar.scenes[1:4], ("Wolverine", "Deadpool", "Thanos")):
        assert entity.lower() in scene.text.lower()
        assert not scene.is_intro and not scene.is_outro
        assert scene.beat_id != 0

    body_words = sum(s.word_count for s in nar.scenes[1:4])
    assert 10 <= body_words <= 60


def test_missing_answer_context_raises(monkeypatch):
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)
    with pytest.raises(FileNotFoundError):
        ws.write_script({"title": "Q?"}, [], "explore_answer",
                        debug_dump={"project": "_no_such_project_xyz"})


def test_missing_project_name_raises(monkeypatch):
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    with pytest.raises(RuntimeError):
        ws.write_script({"title": "Q?"}, [], "explore_answer", debug_dump={})


if __name__ == "__main__":
    import types
    mp = pytest.MonkeyPatch()
    try:
        d = PROJECTS_ROOT / _PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "answer_context.json").write_text(json.dumps(_ANSWER_CONTEXT))
        test_write_explore_answer_end_to_end(mp, d)
        test_missing_answer_context_raises(mp)
        test_missing_project_name_raises(mp)
        print("ok")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        mp.undo()
