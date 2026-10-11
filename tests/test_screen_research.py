"""Tests for stages/stage_1/screen_research.py (screen_qa Stage 1).

Contract (shared with video-qa/p3-visual): screen_context.json =
{question, items:[{entity, event, adaptation_title, year, summary, visual_query, source_urls}]}
plus an OPTIONAL top-level answer_summary (ignored by readers that don't know it).
"""
import json

import pytest

from stages.stage_1 import screen_research as sr

Q = "How did the heroes survive the final battle?"
GOOD_URL = "https://wiki.example.org/wiki/Final_Battle"
OTHER_URL = "https://news.example.com/final-battle-explained"


class FakeSearch:
    """Stands in for research_scout.youcom.RawCall."""

    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {}
        self.error = error

    @property
    def ok(self):
        return self.error is None


def _payload(*urls):
    return {"results": {"web": [
        {"url": u, "title": f"title of {u}", "description": "desc " + u,
         "snippets": ["snippet one", "snippet two"],
         "contents": {"markdown": "x" * 20000},
         "thumbnail_url": "https://img.example.com/t.png",
         "favicon_url": "https://img.example.com/f.png"}
        for u in urls
    ]}}


def _item(**over):
    base = {
        "entity": "Alice",
        "event": "Seals the portal",
        "adaptation_title": "Some Film",
        "year": 2019,
        "summary": "Alice closes the portal from the inside.",
        "visual_query": "Some Film Alice portal",
        "source_urls": [GOOD_URL],
    }
    base.update(over)
    return base


def _stub(monkeypatch, search, llm_json):
    monkeypatch.setattr(sr.YouComClient, "search", lambda self, q, profile=None: search)
    monkeypatch.setattr(sr, "_call_llm", lambda system, user, log=print: json.dumps(llm_json))


# ─── evidence ───────────────────────────────────────────────────────────────

def test_compact_evidence_keeps_urls_and_bounds_size():
    evidence = sr._compact_evidence(_payload(GOOD_URL, OTHER_URL))
    assert [e["url"] for e in evidence] == [GOOD_URL, OTHER_URL]
    assert all(set(e) <= {"url", "title", "description", "snippets", "content"} for e in evidence)
    assert sum(len(json.dumps(e)) for e in evidence) <= sr._EVIDENCE_MAX_CHARS
    assert "thumbnail_url" not in json.dumps(evidence)


def test_compact_evidence_tolerates_garbage():
    assert sr._compact_evidence({}) == []
    assert sr._compact_evidence({"results": "nope"}) == []
    assert sr._compact_evidence({"results": {"web": [None, {"title": "no url"}]}}) == []


# ─── grounding ──────────────────────────────────────────────────────────────

def test_research_refuses_to_answer_without_web_evidence(monkeypatch):
    _stub(monkeypatch, FakeSearch(error="YDC_API_KEY is not set"), {"items": [_item()]})
    monkeypatch.setattr(sr, "_call_llm", lambda *a, **k: pytest.fail("LLM must not run without evidence"))
    with pytest.raises(RuntimeError, match="no web evidence"):
        sr.research_screen_canon(Q)


def test_research_refuses_when_search_returns_no_results(monkeypatch):
    _stub(monkeypatch, FakeSearch(payload={"results": {"web": []}}), {"items": [_item()]})
    monkeypatch.setattr(sr, "_call_llm", lambda *a, **k: pytest.fail("LLM must not run without evidence"))
    with pytest.raises(RuntimeError, match="no web evidence"):
        sr.research_screen_canon(Q)


def test_items_keep_only_urls_that_were_in_the_evidence(monkeypatch):
    llm = {"question": Q, "items": [
        _item(source_urls=[GOOD_URL, "https://made-up.example.net/x"]),
    ]}
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)), llm)
    res = sr.research_screen_canon(Q)
    assert res["items"][0]["source_urls"] == [GOOD_URL]


def test_item_without_any_verifiable_url_is_dropped(monkeypatch):
    llm = {"items": [_item(source_urls=["https://made-up.example.net/x"]),
                     _item(entity="Bob", source_urls=[OTHER_URL])]}
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL, OTHER_URL)), llm)
    res = sr.research_screen_canon(Q)
    assert [i["entity"] for i in res["items"]] == ["Bob"]


def test_all_items_unverifiable_is_an_error(monkeypatch):
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)),
          {"items": [_item(source_urls=["https://made-up.example.net/x"])]})
    with pytest.raises(RuntimeError, match="at least 1"):
        sr.research_screen_canon(Q)


def test_url_match_ignores_trailing_slash_fragment_and_host_case(monkeypatch):
    llm = {"items": [_item(source_urls=["HTTPS://Wiki.Example.org/wiki/Final_Battle/#section"])]}
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)), llm)
    res = sr.research_screen_canon(Q)
    assert len(res["items"]) == 1


# ─── item hygiene ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_year", ["soon", "", None, 1850, 2999])
def test_item_with_implausible_year_is_dropped(bad_year):
    assert sr._clean_screen_items([_item(year=bad_year)]) == []


def test_year_string_is_coerced_to_int():
    assert sr._clean_screen_items([_item(year="2019")])[0]["year"] == 2019


def test_duplicate_items_are_collapsed():
    items = [_item(), _item(summary="same entity and event again")]
    assert len(sr._clean_screen_items(items)) == 1


def test_item_missing_title_or_entity_is_dropped():
    assert sr._clean_screen_items([_item(adaptation_title="")]) == []
    assert sr._clean_screen_items([_item(entity="")]) == []


def test_visual_query_defaults_from_title_entity_event():
    cleaned = sr._clean_screen_items([_item(visual_query="")])
    assert cleaned[0]["visual_query"] == "Some Film Alice Seals the portal"


def test_max_items_is_respected(monkeypatch):
    llm = {"items": [_item(entity=f"E{i}") for i in range(6)]}
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)), llm)
    assert len(sr.research_screen_canon(Q, max_items=3)["items"]) == 3


# ─── persistence + contract ────────────────────────────────────────────────

def test_save_screen_context_roundtrip_and_contract_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "get_project_dirs", lambda p: {"root": tmp_path / p})
    out = sr.save_screen_context("proj", {"question": Q, "items": [_item()]})
    assert out == tmp_path / "proj" / "screen_context.json"
    saved = json.loads(out.read_text())
    assert saved["question"] == Q
    assert "answer_summary" not in saved          # optional: absent unless researched
    for key in ("entity", "event", "adaptation_title", "year", "summary", "visual_query", "source_urls"):
        assert key in saved["items"][0]
    assert isinstance(saved["items"][0]["year"], int)


def test_answer_summary_is_persisted_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "get_project_dirs", lambda p: {"root": tmp_path / p})
    out = sr.save_screen_context("proj", {"question": Q, "answer_summary": " They used X. ", "items": [_item()]})
    assert json.loads(out.read_text())["answer_summary"] == "They used X."


def test_research_returns_answer_summary(monkeypatch):
    llm = {"answer_summary": "They closed the portal.", "items": [_item()]}
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)), llm)
    assert sr.research_screen_canon(Q)["answer_summary"] == "They closed the portal."


# ─── isolation from the comic Q&A research ─────────────────────────────────

def test_research_screen_never_touches_comic_answer_research(monkeypatch, tmp_path):
    import stages.stage_1.answer_research as ar

    def _boom(*a, **k):
        raise AssertionError("screen research must not call the comic/Batcave research path")

    for name in ("build_contexts", "research_answer", "resolve_reader_url", "repair_reader_urls"):
        if hasattr(ar, name):
            monkeypatch.setattr(ar, name, _boom)
    monkeypatch.setattr(sr, "get_project_dirs", lambda p: {"root": tmp_path / p})
    _stub(monkeypatch, FakeSearch(_payload(GOOD_URL)), {"items": [_item()]})

    path = sr.research_screen(Q, "proj")
    assert path.name == "screen_context.json"
    assert not (tmp_path / "proj" / "answer_context.json").exists()
    assert not (tmp_path / "proj" / "comic_context.json").exists()


def test_research_prompt_names_no_specific_comic_or_film():
    # CLAUDE.md: general mechanisms only. Few-shot examples lifted from one test question
    # bias the model toward that title for every other question.
    for banned in ("Endgame", "Scott Lang", "Tony Stark", "Ant-Man", "Avengers", "Spider", "Batman"):
        assert banned.lower() not in sr._SCREEN_RESEARCH_SYSTEM.lower(), banned
