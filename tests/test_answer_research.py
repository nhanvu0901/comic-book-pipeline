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
    """Stub SDK + Comic Vine + project dirs (no network, no real writes).

    build_contexts now cross-checks each item via verify_issue (Comic Vine) and
    auto-resolves empty reader_urls via resolve_reader_url (batcave) — both would
    hit the network, so stub them here. verify_issue -> a benign 'verified'; the
    resolver isn't stubbed because every _ITEMS fixture already has a reader_url
    (so it's never called) — tests that need it stub it themselves."""
    monkeypatch.setattr(mod, "sdk_available", lambda: True)
    monkeypatch.setattr(mod, "sdk_complete_web", lambda *a, **k: _fixture_json())
    monkeypatch.setattr(mod, "get_project_dirs", lambda name: {"root": tmp_path})
    monkeypatch.setattr(mod, "verify_issue",
                        lambda *a, **k: {"ok": True, "note": "verified"})
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
                                  "verification_note", "surprise_level",
                                  "verified", "verify_note"}
        assert it["verified"] is True and it["verify_note"] == "verified"


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
    # Auto-resolve can't find it either -> the empty URL survives -> fail loud.
    monkeypatch.setattr(mod, "resolve_reader_url", lambda *a, **k: "")
    res = mod.research_answer(QUESTION, log=lambda _m: None)
    with pytest.raises(ValueError, match="Deadpool"):
        mod.build_contexts(QUESTION, res, "gr_penance", log=lambda _m: None)


def test_auto_resolve_fills_empty_reader_url(wired, monkeypatch):
    """When the SDK left reader_url empty, resolve_reader_url fills it before the
    fail-loud check — so a resolvable item does NOT raise."""
    items = [dict(it) for it in _ITEMS]
    items[1]["reader_url"] = ""  # Deadpool empty, but resolvable
    monkeypatch.setattr(mod, "sdk_complete_web", lambda *a, **k: _fixture_json(items))
    monkeypatch.setattr(mod, "resolve_reader_url",
                        lambda *a, **k: "https://batcave.biz/reader/999/888")
    res = mod.research_answer(QUESTION, log=lambda _m: None)
    _, c_path = mod.build_contexts(QUESTION, res, "gr_penance", log=lambda _m: None)
    c = json.loads(c_path.read_text())
    assert c["reader_urls"][1] == "https://batcave.biz/reader/999/888"


def test_resolve_reader_url_parses_series_year_issue(monkeypatch):
    """resolve_reader_url parses 'Series (YEAR) #N', picks the year-matching series,
    and returns the chapter whose number == N (discover_issues + search mocked)."""
    captured = {}

    def fake_search(query, *, log=print):
        captured["query"] = query
        return [
            ("29797", "thunderbolts-2006", "https://batcave.biz/29797-thunderbolts-2006.html"),
            ("29798", "thunderbolts-2013", "https://batcave.biz/29798-thunderbolts-2013.html"),
        ]

    def fake_discover(series_url, headless=None):
        captured["series_url"] = series_url
        # posi (`number`) is OFF BY ONE from the issue # here (a front special),
        # exactly like the real batcave Thunderbolts (2013): the match must key on
        # the '#N' in the title, so posi 29 ('Issue #28') must NOT win.
        return [
            {"number": 29.0, "title": "Thunderbolts (2013) Issue #28",
             "url": "https://batcave.biz/reader/29798/209111"},
            {"number": 30.0, "title": "Thunderbolts (2013) Issue #29",
             "url": "https://batcave.biz/reader/29798/209112"},
            {"number": 31.0, "title": "Thunderbolts (2013) Issue #30",
             "url": "https://batcave.biz/reader/29798/209113"},
        ]

    monkeypatch.setattr(mod, "_batcave_search", fake_search)
    monkeypatch.setattr(mod, "discover_issues", fake_discover)

    url = mod.resolve_reader_url("Thunderbolts (2013) #29", "2014", "The Punisher",
                                 log=lambda _m: None)
    assert captured["query"] == "Thunderbolts"          # parsed series name
    assert captured["series_url"].endswith("thunderbolts-2013.html")  # year_hint picked 2013
    assert url == "https://batcave.biz/reader/29798/209112"           # matched title #29, not posi


def test_resolve_reader_url_oneshot_takes_single_chapter(monkeypatch):
    """A one-shot (no '#N') resolves to the series' single chapter."""
    monkeypatch.setattr(mod, "_batcave_search", lambda q, *, log=print: [
        ("500", "some-one-shot-2020", "https://batcave.biz/500-some-one-shot-2020.html")])
    monkeypatch.setattr(mod, "discover_issues", lambda url, headless=None: [
        {"number": 1.0, "url": "https://batcave.biz/reader/500/777"}])
    url = mod.resolve_reader_url("Some One-Shot (2020)", "2020", log=lambda _m: None)
    assert url == "https://batcave.biz/reader/500/777"


def test_resolve_reader_url_no_series_match_returns_empty(monkeypatch):
    """Unrelated search hits (name doesn't appear in any slug) -> "" (fail-loud upstream)."""
    monkeypatch.setattr(mod, "_batcave_search", lambda q, *, log=print: [
        ("1", "completely-different-comic", "https://batcave.biz/1-completely-different-comic.html")])
    monkeypatch.setattr(mod, "discover_issues",
                        lambda url, headless=None: pytest.fail("should not reach discover"))
    assert mod.resolve_reader_url("Thunderbolts (2013) #29", "2014", log=lambda _m: None) == ""


def test_resolve_reader_url_falls_through_slug_year_to_title_match(monkeypatch):
    """Real 2026-07-09 batcave-breach repro: 'Batman (2016) #16' with two 'batman'
    search hits — a legacy-ID slug with NO year at all ('561-batman.html', the real
    correct series) and an unrelated, wrongly-dated slug that DOES carry a year
    ('33758-batman-2025.html', a different Batman (2025) volume). Both tie on the
    'batman' name-token score, and the old code took the wrong one, saw its year
    baked into the slug didn't match, and refused outright.

    The fix must not trust the slug year alone: it should try both candidates,
    reading each one's own chapter TITLES (site's real label), and land on the
    2016 volume because THAT series' issue #16 chapter is titled '(2016-)'."""
    monkeypatch.setattr(mod, "_batcave_search", lambda q, *, log=print: [
        ("33758", "batman-2025", "https://batcave.biz/33758-batman-2025.html"),
        ("561", "batman", "https://batcave.biz/561-batman.html"),
    ])

    def fake_discover(series_url, headless=None):
        if series_url.endswith("33758-batman-2025.html"):
            # Wrong series: its own titles genuinely say 2025, not 2016.
            return [{"number": 16.0, "title": "Batman (2025-) #16",
                     "url": "https://batcave.biz/reader/33758/000"}]
        if series_url.endswith("561-batman.html"):
            # Right series: legacy slug has no year, but the chapter title does.
            return [
                {"number": 15.0, "title": "Batman (2016-) #15",
                 "url": "https://batcave.biz/reader/561/111"},
                {"number": 16.0, "title": "Batman (2016-) #16",
                 "url": "https://batcave.biz/reader/561/112"},
            ]
        pytest.fail(f"unexpected series_url {series_url!r}")

    monkeypatch.setattr(mod, "discover_issues", fake_discover)
    url = mod.resolve_reader_url("Batman (2016) #16", "2016", "Bane", log=lambda _m: None)
    assert url == "https://batcave.biz/reader/561/112"


def test_resolve_reader_url_refuses_when_no_candidate_title_matches_year(monkeypatch):
    """If NONE of the candidates' own chapter titles confirm the wanted year, the
    original refuse-and-hand-fill guard must still fire (it once correctly caught a
    genuinely wrong 2025 volume) — this must not regress into silently accepting
    the top mismatched pick."""
    monkeypatch.setattr(mod, "_batcave_search", lambda q, *, log=print: [
        ("33758", "batman-2025", "https://batcave.biz/33758-batman-2025.html"),
    ])
    monkeypatch.setattr(mod, "discover_issues", lambda url, headless=None: [
        {"number": 16.0, "title": "Batman (2025-) #16",
         "url": "https://batcave.biz/reader/33758/000"},
    ])
    logs = []
    url = mod.resolve_reader_url("Batman (2016) #16", "2016", log=logs.append)
    assert url == ""
    assert any("volume-year mismatch" in m for m in logs)


def test_parse_source_comic_shapes():
    assert mod._parse_source_comic('Thunderbolts (2013) #29') == ("Thunderbolts", "2013", "29")
    assert mod._parse_source_comic('"Ghost Rider" (1990) #12') == ("Ghost Rider", "1990", "12")
    assert mod._parse_source_comic('"Deadpool" #33') == ("Deadpool", "", "33")
    assert mod._parse_source_comic('Marvel Comics Presents (1988)') == ("Marvel Comics Presents", "1988", "")


def test_pick_series_ignores_generic_tokens():
    """'FF Vol. 2' must not lose to a slug that only matches 'vol'+'2' (real
    mis-resolve: Ant-Man's FF #16 → a Squirrel Girl chapter, 2026-07-06)."""
    from stages.stage_1.answer_research import _pick_series
    hits = [("6087", "the-unbeatable-squirrel-girl-vol-2-2015", "USG"),
            ("15010", "ff-2013", "FF")]
    assert _pick_series(hits, "FF Vol. 2", "2014") == "FF"
    # generic-only overlap alone can never clear the 0.5 threshold
    assert _pick_series([hits[0]], "FF Vol. 2", "2014") == ""
    # all-generic/numeric names still resolve via raw-token fallback
    assert _pick_series([("1", "2000-ad", "AD")], "2000 AD", "") == "AD"
