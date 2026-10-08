import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stages.research_scout import router_rules
from stages.research_scout.router_schema import PrimaryMedium, VisualSource, RoutedItem, QuestionRouteResponse
from stages.research_scout.router_rules import (
    resolve_media_route,
    route_question_to_pipeline,
    decide_route,
)


def _item(medium="film", source="youtube", **extra):
    return RoutedItem(event=extra.pop("event", "an event"), primary_medium=PrimaryMedium(medium),
                      visual_source=VisualSource(source), confidence=0.9,
                      reason=extra.pop("reason", "r"), **extra)


def _llm(*items):
    """A fake OpenAI-style client whose one answer is `items` (RoutedItem models)."""
    payload = QuestionRouteResponse(question="q", items=list(items)).model_dump(mode="json")
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))])
    return client


def _prompt_sent(client) -> str:
    msgs = client.chat.completions.create.call_args.kwargs["messages"]
    return "\n".join(m["content"] for m in msgs)


# ── the deterministic rule ─────────────────────────────────────────────────────

def test_resolve_media_route_rules():
    items_comic = [_item("comic", "comic", event="Death of Gwen Stacy in comics")]
    assert resolve_media_route(items_comic, batcave_available=True, clips_found=True) == "comic_qa"
    assert resolve_media_route(items_comic, batcave_available=False, clips_found=True) == "comic_qa"

    items_mixed = [_item("mixed", "both", event="Civil War conflict")]
    assert resolve_media_route(items_mixed, batcave_available=True, clips_found=True) == "comic_qa"
    assert resolve_media_route(items_mixed, batcave_available=False, clips_found=True) == "comic_qa"

    items_screen = [_item("film", "youtube", event="Time heist in Endgame")]
    assert resolve_media_route(items_screen, batcave_available=False, clips_found=True) == "screen_qa"
    assert resolve_media_route(items_screen, batcave_available=True, clips_found=True) == "comic_qa"
    assert resolve_media_route(items_screen, batcave_available=False, clips_found=False) == "comic_qa"
    assert resolve_media_route([], batcave_available=False, clips_found=True) == "comic_qa"


@pytest.mark.parametrize("source", ["comic", "both"])
def test_a_screen_item_that_still_wants_comic_visuals_is_not_screen_only(source):
    items = [_item("film", source)]
    assert resolve_media_route(items, batcave_available=False, clips_found=True) == "comic_qa"


# ── M9: the comic counterpart is looked up from the LLM's TYPED output ─────────
# Everything below the LLM is real code: only the network edges (batcave search/chapter
# list/page ping, You.com clips) are mocked, so the typed ref really flows into the verifier.

CIVIL_WAR_HITS = [("777", "civil-war-2006", "https://batcave.biz/777-civil-war-2006.html")]
CIVIL_WAR_CHAPTERS = [
    {"title": "Civil War (2006) #1", "url": "https://batcave.biz/reader/777/9001",
     "chapter_id": 9001, "number": 1.0, "date": "01.01.2007"},
    {"title": "Civil War (2006) #2", "url": "https://batcave.biz/reader/777/9002",
     "chapter_id": 9002, "number": 2.0, "date": "01.02.2007"},
]
CLIPS = [{"title": "clip", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]


class _Edges:
    """Patch the network edges; remember how they were used."""

    def __init__(self, hits=CIVIL_WAR_HITS, chapters=CIVIL_WAR_CHAPTERS, pages=("p1.jpg",), clips=CLIPS):
        self.search = MagicMock(return_value=list(hits))
        self.chapters = MagicMock(return_value=list(chapters))
        self.ping = MagicMock(return_value=list(pages))
        self.clips = MagicMock(return_value=list(clips)) if not isinstance(clips, Exception) \
            else MagicMock(side_effect=clips)
        self._patches = [
            patch("stages.stage_1.batcave_verifier._batcave_search", self.search),
            patch("stages.stage_1.batcave_verifier.discover_issues", self.chapters),
            patch("utils.comic_scraper.readcomiconline._ajax_chapter_images", self.ping),
            patch.object(router_rules, "youtube_search", self.clips),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def test_llm_says_film_only_but_batcave_has_civil_war_1_2006_routes_comic_qa():
    film_only = _llm(_item("film", "youtube", event="Captain America and Iron Man split over the Accords",
                           adaptation_title="Captain America: Civil War (2016)",
                           comic_series="Civil War", comic_issue=1, comic_year=2006))
    with _Edges() as edges:
        decision = decide_route("Why did Captain America and Iron Man fight?",
                                search_results=[], client=film_only, log=lambda m: None)

    assert decision.route == "comic_qa"
    assert [(c.series, c.issue, c.year, c.found, c.level) for c in decision.batcave_checks] == \
        [("Civil War", 1, 2006, True, "issue")]
    # the page ping is on (ping_pages=True) and used the real chapter ids
    edges.ping.assert_called_once()
    assert edges.ping.call_args.args[1:] == ("777", 9001)


def test_same_film_only_answer_goes_to_screen_qa_when_batcave_has_no_such_comic():
    film_only = _llm(_item("film", "youtube", comic_series="Civil War", comic_issue=1, comic_year=2006))
    with _Edges(hits=[]):
        decision = decide_route("q", search_results=[], client=film_only, log=lambda m: None)
    assert decision.route == "screen_qa"
    assert decision.batcave_checks[0].found is False


def test_a_comic_whose_pages_do_not_answer_does_not_count_as_available():
    film_only = _llm(_item("film", "youtube", comic_series="Civil War", comic_issue=1, comic_year=2006))
    with _Edges(pages=()):
        assert decide_route("q", search_results=[], client=film_only, log=lambda m: None).route == "screen_qa"


def test_unknown_issue_verifies_series_and_year_only_and_says_so():
    film_only = _llm(_item("film", "youtube", comic_series="Civil War", comic_year=2006))
    logs: list[str] = []
    with _Edges() as edges:
        decision = decide_route("q", search_results=[], client=film_only, log=logs.append)

    assert decision.route == "comic_qa"
    (check,) = decision.batcave_checks
    assert check.level == "series" and check.issue is None and check.found is True
    assert "issue number unknown" in check.note.lower()
    edges.ping.assert_not_called()                      # nothing to ping without an issue
    assert any("issue number unknown" in line.lower() for line in logs)
    assert "issue number unknown" in json.dumps(decision.to_dict()).lower()


def test_no_comic_reference_means_nothing_is_looked_up_and_no_issue_1_is_invented():
    # A title with a year, an event name — none of it is a typed comic ref, so none of it is probed.
    film_only = _llm(_item("film", "youtube", event="Time heist", adaptation_title="Avengers: Endgame (2019)"))
    with _Edges() as edges:
        decision = decide_route("How did the Avengers travel back in time in Endgame?",
                                search_results=[], client=film_only, log=lambda m: None)
    assert decision.route == "screen_qa"
    assert decision.batcave_checks == []
    edges.search.assert_not_called()
    edges.chapters.assert_not_called()


def test_an_inconclusive_batcave_lookup_keeps_the_safe_comic_route_and_skips_the_clip_search():
    # Seen on the Windows server: a transient batcave failure was read as "no such comic" and a
    # Civil War question went to screen_qa. A lookup that could not finish must never say "absent".
    film_only = _llm(_item("film", "youtube", comic_series="Civil War", comic_issue=1, comic_year=2006))
    logs: list[str] = []
    with _Edges() as edges:
        edges.search.side_effect = RuntimeError("batcave search 'Civil War' answered status=503")
        decision = decide_route("q", search_results=[], client=film_only, log=logs.append)

    assert decision.route == "comic_qa"
    assert decision.batcave_checks[0].inconclusive is True
    assert decision.clips_found is None
    edges.clips.assert_not_called()
    assert any("inconclusive" in r.lower() for r in decision.reasons)
    assert "inconclusive" in json.dumps(decision.to_dict()).lower()


def test_one_inconclusive_ref_does_not_hide_a_found_one():
    film_only = _llm(_item("film", "youtube", comic_series="Civil War", comic_year=2006),
                     _item("film", "youtube", comic_series="Civil War Aftermath"))
    with _Edges() as edges:
        edges.search.side_effect = [RuntimeError("503"), list(CIVIL_WAR_HITS)]
        decision = decide_route("q", search_results=[], client=film_only, log=lambda m: None)
    assert decision.route == "comic_qa"
    assert [c.found for c in decision.batcave_checks] == [False, True]


def test_definite_absence_on_every_ref_still_allows_screen_qa():
    film_only = _llm(_item("film", "youtube", comic_series="Nothing Like It", comic_year=1999))
    with _Edges(hits=[]):
        decision = decide_route("q", search_results=[], client=film_only, log=lambda m: None)
    assert decision.route == "screen_qa"
    assert decision.batcave_checks[0].inconclusive is False


def test_no_clips_found_keeps_the_safe_comic_route():
    film_only = _llm(_item("film", "youtube"))
    with _Edges(clips=[]):
        assert decide_route("q", search_results=[], client=film_only, log=lambda m: None).route == "comic_qa"


def test_clip_search_failure_keeps_the_safe_comic_route():
    film_only = _llm(_item("film", "youtube"))
    with _Edges(clips=RuntimeError("You.com down")):
        decision = decide_route("q", search_results=[], client=film_only, log=lambda m: None)
    assert decision.route == "comic_qa" and decision.clips_found is False


def test_comic_or_mixed_items_decide_without_touching_batcave_or_youtube():
    mixed = _llm(_item("film", "youtube"), _item("mixed", "both"))
    with _Edges() as edges:
        decision = decide_route("q", search_results=[], client=mixed, log=lambda m: None)
    assert decision.route == "comic_qa"
    edges.search.assert_not_called()
    edges.clips.assert_not_called()


def test_a_failing_llm_defaults_to_comic_qa():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("openrouter 500")
    decision = decide_route("q", search_results=[], client=client, log=lambda m: None)
    assert decision.route == "comic_qa" and decision.response is None


def test_an_answer_with_no_items_is_not_evidence_for_screen_qa():
    decision = decide_route("q", search_results=[], client=_llm(), log=lambda m: None)
    assert decision.route == "comic_qa"


def test_route_question_to_pipeline_keeps_its_tuple_contract():
    film_only = _llm(_item("film", "youtube"))
    with _Edges():
        route, resp = route_question_to_pipeline("q", search_results=[], client=film_only,
                                                 log=lambda m: None)
    assert route == "screen_qa"
    assert isinstance(resp, QuestionRouteResponse) and len(resp.items) == 1


# ── M12: the web-search evidence step ──────────────────────────────────────────

def test_without_a_you_com_key_routing_still_runs_with_empty_evidence(monkeypatch):
    # YDC_API_KEY unset: the old code read a config attribute that does not exist -> AttributeError.
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    client = _llm(_item("comic", "comic"))
    decision = decide_route("How did Gwen Stacy die?", client=client, log=lambda m: None)
    assert decision.route == "comic_qa"
    client.chat.completions.create.assert_called_once()
    assert "Search Results:" in _prompt_sent(client)


def test_you_com_web_results_reach_the_router_prompt(monkeypatch):
    payload = {"results": {"web": [
        {"url": "https://example.test/gwen", "title": "Gwen Stacy dies", "snippets": ["neck snap in #121"]},
    ]}}

    class FakeYou:
        def __init__(self, *a, **k):
            pass

        def search(self, query, profile):
            return MagicMock(ok=True, payload=payload)

    monkeypatch.setattr(router_rules, "YouComClient", FakeYou)
    client = _llm(_item("comic", "comic"))
    decide_route("How did Gwen Stacy die?", client=client, log=lambda m: None)
    prompt = _prompt_sent(client)
    assert "https://example.test/gwen" in prompt and "neck snap in #121" in prompt


def test_a_raising_search_client_does_not_stop_routing(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no network")

    monkeypatch.setattr(router_rules, "YouComClient", Boom)
    client = _llm(_item("comic", "comic"))
    assert decide_route("q", client=client, log=lambda m: None).route == "comic_qa"


def test_a_search_error_response_is_treated_as_no_evidence(monkeypatch):
    class Failing:
        def __init__(self, *a, **k):
            pass

        def search(self, query, profile):
            return MagicMock(ok=False, payload={}, error="HTTP 429")

    monkeypatch.setattr(router_rules, "YouComClient", Failing)
    client = _llm(_item("comic", "comic"))
    assert decide_route("q", client=client, log=lambda m: None).route == "comic_qa"


# ── recorded LLM outputs from the spike -> our rule (NOT built from ground truth) ──

SPIKE_RESULTS = Path("/tmp/source_router/followup_results.json")


@pytest.mark.skipif(not SPIKE_RESULTS.exists(), reason="spike recordings not present")
def test_recorded_spike_outputs_never_send_a_comic_question_to_screen_qa_except_the_known_miss():
    """Replay the real Gemini outputs of the neutral set through resolve_media_route.
    With no typed comic refs recorded, only the known miss (the LLM saw just the film for
    'Captain America vs Iron Man') may reach screen_qa from a non-screen-only question."""
    rows = json.loads(SPIKE_RESULTS.read_text())
    wrong_screen = []
    for row in rows:
        info = row["info"]
        items = [RoutedItem.model_validate(i) for i in row["run1"]["items"]]
        route = resolve_media_route(items, batcave_available=False, clips_found=True)
        if route == "screen_qa" and info["ground_truth_category"] != "screen_only":
            wrong_screen.append(info["id"])
    assert wrong_screen == ["A05_CAP_IRONMAN_FIGHT"]


# ── opt-in accuracy run (live You.com + OpenRouter + batcave) ──────────────────

NEUTRAL_SET = Path("/tmp/source_router/followup_definitions.json")


@pytest.mark.integration
@pytest.mark.skipif(not NEUTRAL_SET.exists(), reason="/tmp/source_router/followup_definitions.json not found")
def test_live_router_accuracy_on_the_neutral_question_set(capsys):
    """Opt-in: `pytest -m integration tests/test_router_rules.py -k live_router`.
    Runs every neutral/bank question through the whole router and prints the table.
    Safety is strict (a comic-canon question must never reach screen_qa); accuracy must
    stay at the spike's 15/18."""
    data = json.loads(NEUTRAL_SET.read_text())
    questions = data.get("set_a", []) + data.get("set_b", [])
    rows, correct, unsafe = [], 0, []
    for q in questions:
        want = "screen_qa" if q["ground_truth_category"] == "screen_only" else "comic_qa"
        route, resp = route_question_to_pipeline(q["question"])
        ok = route == want
        correct += ok
        if route == "screen_qa" and want == "comic_qa":
            unsafe.append(q["id"])
        refs = [(i.comic_series, i.comic_issue, i.comic_year) for i in (resp.items if resp else [])
                if i.comic_series]
        rows.append(f"{'OK ' if ok else 'BAD'} {q['id']:<28} want={want:<9} got={route:<9} refs={refs}")
    with capsys.disabled():
        print("\n" + "\n".join(rows) + f"\nACCURACY {correct}/{len(questions)}")
    assert not unsafe, f"comic-canon questions routed to screen_qa: {unsafe}"
    assert correct >= 15
