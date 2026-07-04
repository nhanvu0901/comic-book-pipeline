"""Tests for stages/stage_1/answer_research.py (explore_answer / Q&A mode, piece #1).

No network: the SDK web call and get_project_dirs are monkeypatched. See
EXPLORE_ANSWER_DESIGN.md for the schema contract these tests pin down.
"""
import json

import pytest

import stages.stage_1.answer_research as mod

QUESTION = "Who has survived Ghost Rider's Penance Stare?"

# Three items, surprise ascending (shock last). Wrapped in a ```json fence so the
# tests also exercise _extract_json's tolerance of markdown fences.
_ITEMS = [
    {"entity": "Ghost Rider", "how_or_why": "Danny Ketch turns the Stare on himself "
     "and feels nothing, carrying no innocent blood.",
     "source_comic": '"Ghost Rider" (1990) #12', "source_year": "1991",
     "drawable_moment": "flaming skull staring into a mirror",
     "verification_note": "marvel.fandom.com + Comic Vine",
     "surprise_level": "low", "reader_url": "https://batcave.biz/reader/111/222"},
    {"entity": "Deadpool", "how_or_why": "His scrambled mind offers no coherent guilt "
     "to burn, so the Stare does nothing.",
     "source_comic": '"Deadpool" #33', "source_year": "2014",
     "drawable_moment": "Deadpool grinning as hellfire washes over him",
     "verification_note": "CBR feats list + marvel.fandom.com",
     "surprise_level": "medium", "reader_url": "https://batcave.biz/reader/333/444"},
    {"entity": "Man-Thing", "how_or_why": "With no soul to judge, the swamp creature "
     "is simply unaffected.",
     "source_comic": '"Marvel Comics Presents" #1', "source_year": "1990",
     "drawable_moment": "Man-Thing looming unmoved before Ghost Rider",
     "verification_note": "WEAK: single Reddit thread",
     "surprise_level": "high", "reader_url": "https://batcave.biz/reader/555/666"},
]


def _fixture_json(items=None):
    return "```json\n" + json.dumps({
        "answer_summary": "Several heroes shrugged it off — the last one shouldn't have.",
        "items": items if items is not None else _ITEMS,
    }) + "\n```"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Stub the SDK + redirect project dirs into tmp_path (no network, no real writes)."""
    monkeypatch.setattr(mod, "sdk_available", lambda: True)
    monkeypatch.setattr(mod, "sdk_complete_web", lambda *a, **k: _fixture_json())
    monkeypatch.setattr(mod, "get_project_dirs", lambda name: {"root": tmp_path})
    return tmp_path


def _research(monkeypatch, items=None):
    monkeypatch.setattr(mod, "sdk_available", lambda: True)
    monkeypatch.setattr(mod, "sdk_complete_web", lambda *a, **k: _fixture_json(items))
    return mod.research_answer(QUESTION, log=lambda _m: None)


def test_json_extraction_tolerates_markdown_fences():
    obj = mod._extract_json(_fixture_json())
    assert obj is not None and len(obj["items"]) == 3


def test_research_orders_by_surprise_ascending(monkeypatch):
    # Feed items shuffled; research_answer must re-order shock LAST.
    shuffled = [_ITEMS[2], _ITEMS[0], _ITEMS[1]]
    res = _research(monkeypatch, shuffled)
    assert [i["surprise_level"] for i in res["items"]] == ["low", "medium", "high"]
    assert res["items"][-1]["entity"] == "Man-Thing"
    assert res["source_engine"] == "claude-sdk-web"


def test_research_fails_loud_when_too_few_items(monkeypatch):
    with pytest.raises(ValueError, match="need >= 3"):
        _research(monkeypatch, _ITEMS[:2])


def test_answer_context_schema_exact_and_presentation_order(wired, monkeypatch):
    res = mod.research_answer(QUESTION, log=lambda _m: None)
    a_path, _ = mod.build_contexts(QUESTION, res, "gr_penance",
                                   researched_at="2026-07-04", log=lambda _m: None)
    a = json.loads(a_path.read_text())

    assert set(a.keys()) == {"question", "answer_summary", "researched_at",
                             "source_engine", "items"}
    assert a["question"] == QUESTION
    assert a["researched_at"] == "2026-07-04"
    assert a["source_engine"] == "claude-sdk-web"
    # presentation order: rank 1 first, shock last
    assert [it["rank"] for it in a["items"]] == [1, 2, 3]
    assert [it["entity"] for it in a["items"]] == ["Ghost Rider", "Deadpool", "Man-Thing"]
    for it in a["items"]:
        assert set(it.keys()) == {"rank", "entity", "how_or_why", "source_comic",
                                  "source_year", "reader_url", "drawable_moment",
                                  "verification_note", "surprise_level"}


def test_comic_context_saga_shape(wired):
    res = mod.research_answer(QUESTION, log=lambda _m: None)
    _, c_path = mod.build_contexts(QUESTION, res, "gr_penance", log=lambda _m: None)
    c = json.loads(c_path.read_text())

    assert c["is_arc"] is True
    assert c["issue_count"] == 3
    assert c["plot_source"] == "answer_research"
    assert c["title"] == QUESTION and c["series"] == QUESTION
    # NO cold-read summary, NO identity-hook user_prompt (design map item 2)
    assert "summary" not in c
    assert "user_prompt" not in c
    # issues[] carry per-item how/why plots, in order
    assert isinstance(c["issues"], list) and len(c["issues"]) == 3
    assert [i["chapter_index"] for i in c["issues"]] == [1, 2, 3]
    assert c["issues"][0]["plot_summary"] == _ITEMS[0]["how_or_why"]
    assert [i["label"] for i in c["issues"]] == [it["source_comic"] for it in _ITEMS]
    # reader_urls order == items order
    assert c["reader_urls"] == [it["reader_url"] for it in _ITEMS]
    assert c["characters"] == [it["entity"] for it in _ITEMS]


def test_empty_reader_url_fails_loud_naming_item(wired, monkeypatch):
    items = [dict(it) for it in _ITEMS]
    items[1]["reader_url"] = ""  # Deadpool has no downloadable source
    monkeypatch.setattr(mod, "sdk_complete_web", lambda *a, **k: _fixture_json(items))
    res = mod.research_answer(QUESTION, log=lambda _m: None)
    with pytest.raises(ValueError, match="Deadpool"):
        mod.build_contexts(QUESTION, res, "gr_penance", log=lambda _m: None)
