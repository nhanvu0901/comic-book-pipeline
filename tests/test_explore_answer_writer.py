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
    # Q&A outro is meaning-first only — never a "sources linked in the description"
    # credit. With the LLM outro/tease helpers failing (stubbed to raise above), it
    # falls back to the generic meaning line, NOT a credit.
    assert "Full sources" not in nar.scenes[-1].text
    assert "linked in the description" not in nar.scenes[-1].text
    assert nar.scenes[-1].text.strip()  # never empty

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


# ─── Archetype dispatch (Why/How → explain mode) ────────────────────────────

def test_question_archetype_detection():
    from stages.question_archetype import question_archetype as qa
    # explain family (Master's 2026-07-06 archetype)
    assert qa("Why the Phoenix Force chose a broken host to rebuild the entire multiverse") == "explain"
    assert qa("How Magneto built a mutant utopia on the ruins of a dead world") == "explain"
    assert qa("The day Joker finally went sane and realized the horror he created") == "explain"
    assert qa("The tragic reason Silver Surfer must keep his memories hidden") == "explain"
    assert qa("The time Superman realized his greatest power was a curse") == "explain"
    assert qa("What made Doctor Doom give up absolute godhood?") == "explain"
    # list family stays list
    assert qa("Who has survived Ghost Rider's Penance Stare?") == "list"
    assert qa("4 villains who broke a hero's body") == "list"
    assert qa("") == "list"


def test_validator_explain_requires_answer_in_final_scene():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Jean", page_refs=[1]),
             ea.Beat(id=2, function="LANDING", name="Scott", page_refs=[2])]
    # final scene narrates an event but never answers → flagged
    scenes = [{"text": "Jean hatches from the egg, broken and blank. " + "word " * 18},
              {"text": "Scott hears her whisper and walks back into the school. " + "word " * 18}]
    issues = ea._validate_explore_scenes(scenes, beats, "explain")
    assert any("never states the ANSWER" in i for i in issues)
    # same scenes pass in list mode (rule is explain-only)
    assert not any("ANSWER" in i for i in ea._validate_explore_scenes(scenes, beats, "list"))
    # causal marker in the final scene satisfies the guard
    scenes[1]["text"] = ("That's why it had to be Scott — only a broken host could let him go. "
                         + "word " * 12)
    assert not any("ANSWER" in i for i in ea._validate_explore_scenes(scenes, beats, "explain"))


def test_validator_explain_bans_list_language():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Jean", page_refs=[1])]
    scenes = [{"text": "Jean is the last one on this list, because reasons. " + "word " * 15}]
    issues = ea._validate_explore_scenes(scenes, beats, "explain")
    assert any("list language" in i for i in issues)
    assert not any("list language" in i for i in ea._validate_explore_scenes(scenes, beats, "list"))


def test_build_hook_by_archetype():
    ctx = {"answer_summary": "Because only the dead woman could let him go."}
    h_list = ea._build_hook("Who survived X", ctx, "list")
    h_explain = ea._build_hook("Why did the Phoenix choose a broken host", ctx, "explain")
    assert "list" in h_list  # countdown tease kept for list questions
    assert "list" not in h_explain  # no list language on explain hooks
    # explain hook must NOT spoil the thesis (the answer lands at the END)
    assert "dead woman" not in h_explain
    assert h_explain.startswith("Why did the Phoenix choose a broken host?")
