"""Tests for stages/stage_3/screen_qa.py — the screen_qa narration writer.

The video keeps the Q&A structure of explore_answer (spoken hook -> TWO scenes per item,
CONTEXT then MOMENT -> spoken outro) but is grounded in screen canon: every new
film/series is cited by TITLE + YEAR instead of a comic issue number, and every visual beat
is a {text, query} dict (query = clip-search hint) instead of a panel pick.
"""
import json
import re

import pytest

from stages.stage_3 import screen_qa as sq
from stages.stage_3.schema import Narration
from stages.stage_5.shots import _vb_text

Q_EXPLAIN = "How did the crew escape the sinking station?"
Q_LIST = "Which heroes sacrificed themselves to save the city?"


def _items(same_title=False):
    return [
        {"entity": "Alice Park", "event": "seals the breached airlock",
         "adaptation_title": "Deep Station", "year": 2011,
         "summary": "Alice welds the airlock shut while the crew runs for the pods.",
         "visual_query": "Deep Station Alice airlock weld",
         "source_urls": ["https://wiki.example.org/a"]},
        {"entity": "Bob Chen", "event": "reroutes the reactor coolant",
         "adaptation_title": "Deep Station" if same_title else "Cold Orbit", "year": 2011 if same_title else 2016,
         "summary": "Bob pumps the coolant into the flooded deck so the reactor stays stable.",
         "visual_query": "Cold Orbit Bob reactor coolant",
         "source_urls": ["https://wiki.example.org/b"]},
    ]


def _write_context(root, question=Q_EXPLAIN, items=None, answer_summary="They sealed the breach and saved the reactor."):
    ctx = {"question": question, "items": items or _items()}
    if answer_summary:
        ctx["answer_summary"] = answer_summary
    root.mkdir(parents=True, exist_ok=True)
    (root / "screen_context.json").write_text(json.dumps(ctx))


def _good_llm(items=None, **over):
    """A writer response that satisfies every validator: 2 scenes per item, title+year cited."""
    items = items or _items()
    scenes = []
    for it in items:
        cite = f"In {it['adaptation_title']} ({it['year']}), "
        a = f"{cite}{it['entity']} faces a growing crisis."
        b = f"{it['entity']} {it['event']} and that is why everyone lives."
        for text, q in ((a, it["visual_query"]), (b, it["visual_query"] + " close up")):
            words = text.split()
            mid = len(words) // 2
            scenes.append({"text": text, "visual_beats": [
                {"text": " ".join(words[:mid]), "query": q},
                {"text": " ".join(words[mid:]), "query": q + " reaction"},
            ]})
    payload = {
        "title": "How The Crew Survived The Station",
        "hook": "The station was sinking and only two people could stop it.",
        "outro": "Two quiet acts of nerve saved everyone aboard.",
        "scenes": scenes,
    }
    payload.update(over)
    return payload


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    monkeypatch.setattr(sq, "get_project_dirs", lambda p: {"root": root})
    monkeypatch.setattr(sq, "_qa_budget", lambda n: (5, 10_000, 42, 2))   # real band tested separately
    return root


def _run(proj, monkeypatch, llm, items=None, question=Q_EXPLAIN, **ctx):
    _write_context(proj, question=question, items=items, **ctx)
    prompts = []
    queue = list(llm) if isinstance(llm, list) else [llm]

    def fake(system, user, **kw):
        prompts.append((system, user))
        out = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(out, Exception):
            raise out
        return (out if isinstance(out, str) else json.dumps(out)), "test-model"

    monkeypatch.setattr(sq, "_call_llm_chain", fake)
    nar = sq.write_screen_qa("proj", progress=lambda m: None)
    return nar, prompts


def _tokens(s):
    return re.findall(r"[a-z0-9]+", s.lower())


# ─── structure ──────────────────────────────────────────────────────────────

def test_narration_keeps_the_qa_structure(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    assert isinstance(nar, Narration)
    assert nar.mode == "screen_qa" == sq.SCREEN_QA_MODE
    scenes = nar.scenes
    assert len(scenes) == 1 + 2 * 2 + 1                       # hook + 2 per item + outro
    assert [s.scene_id for s in scenes] == list(range(1, 7))
    assert scenes[0].is_intro and scenes[0].text == nar.hook
    assert scenes[-1].is_outro and scenes[-1].text == "Two quiet acts of nerve saved everyone aboard."
    assert not any(s.is_intro or s.is_outro for s in scenes[1:-1])
    assert nar.title == nar.banner_title == "How The Crew Survived The Station"


def test_body_scenes_follow_item_order_two_per_item(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    assert [s.beat_id for s in nar.scenes] == [0, 1, 1, 2, 2, 2]   # outro anchors to the last item
    assert [b.name for b in nar.beats] == ["Alice Park", "Bob Chen"]
    assert nar.beats[0].function == "COLD_OPEN" and nar.beats[-1].function == "LANDING"
    assert "Alice" in nar.scenes[1].text and "Bob" in nar.scenes[3].text


def test_no_comic_page_refs(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    assert all(s.page_ref == 0 and s.panel_ref == -1 for s in nar.scenes)


def test_word_count_and_duration_are_totalled(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    assert nar.total_word_count == sum(len(s.text.split()) for s in nar.scenes)
    assert nar.estimated_duration_seconds == pytest.approx(nar.total_word_count / nar.words_per_second, abs=0.2)


# ─── citation: title + year, never a comic issue number ──────────────────────

def test_each_new_title_is_cited_with_its_year(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    first = " ".join(s.text for s in nar.scenes if s.beat_id == 1 and not s.is_intro)
    second = " ".join(s.text for s in nar.scenes if s.beat_id == 2 and not s.is_outro)
    assert "Deep Station" in first and "2011" in first
    assert "Cold Orbit" in second and "2016" in second
    assert not any("#" in s.text for s in nar.scenes)


def test_missing_citation_is_repaired_deterministically(proj, monkeypatch):
    bad = _good_llm()
    for s in bad["scenes"]:                                  # writer forgot every citation
        s["text"] = re.sub(r"In [A-Za-z ]+ \(\d{4}\), ", "", s["text"])
        s["visual_beats"] = [{"text": s["text"], "query": "clip"}]
    nar, _ = _run(proj, monkeypatch, [bad, bad])
    first = " ".join(s.text for s in nar.scenes if s.beat_id == 1)
    second = " ".join(s.text for s in nar.scenes if s.beat_id == 2)
    assert "Deep Station" in first and "2011" in first
    assert "Cold Orbit" in second and "2016" in second
    # the repaired scene keeps verbatim beats that cover its whole text
    for s in nar.scenes:
        assert _tokens(" ".join(b["text"] for b in s.visual_beats)) == _tokens(s.text)


def test_a_repeated_title_is_not_re_cited_by_the_repair(proj, monkeypatch):
    items = _items(same_title=True)
    llm = _good_llm(items)
    for s in llm["scenes"][2:]:                              # item 2 never repeats the film name
        s["text"] = re.sub(r"In [A-Za-z ]+ \(\d{4}\), ", "", s["text"])
        s["visual_beats"] = [{"text": s["text"], "query": "clip"}]
    nar, _ = _run(proj, monkeypatch, [llm, llm], items=items)
    second = " ".join(s.text for s in nar.scenes if s.beat_id == 2 and not s.is_outro)
    assert "Deep Station" not in second


# ─── visual beats: {text, query}, verbatim, anchored ─────────────────────────

def test_visual_beats_are_text_query_dicts_that_rebuild_the_scene(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    for s in nar.scenes:
        assert s.visual_beats, s.text
        for b in s.visual_beats:
            assert isinstance(b, dict) and b["text"].strip() and b["query"].strip()
            assert _vb_text(b) == b["text"]
        assert _tokens(" ".join(b["text"] for b in s.visual_beats)) == _tokens(s.text)


def test_non_verbatim_beats_are_rebuilt_from_the_scene_text(proj, monkeypatch):
    llm = _good_llm()
    llm["scenes"][0]["visual_beats"] = [{"text": "something the writer made up", "query": "q"}]
    nar, _ = _run(proj, monkeypatch, llm)
    s = nar.scenes[1]
    assert _tokens(" ".join(b["text"] for b in s.visual_beats)) == _tokens(s.text)
    assert all(b["query"].strip() for b in s.visual_beats)


def test_string_beats_are_upgraded_to_dicts(proj, monkeypatch):
    llm = _good_llm()
    for sc in llm["scenes"]:
        sc["visual_beats"] = [b["text"] for b in sc["visual_beats"]]
    nar, _ = _run(proj, monkeypatch, llm)
    assert all(isinstance(b, dict) and b["query"] for s in nar.scenes for b in s.visual_beats)


def test_every_query_is_anchored_to_the_film_title(proj, monkeypatch):
    llm = _good_llm()
    llm["scenes"][0]["visual_beats"][0]["query"] = "airlock weld sparks"        # title missing
    nar, _ = _run(proj, monkeypatch, llm)
    q = nar.scenes[1].visual_beats[0]["query"]
    assert "Deep Station" in q and "airlock weld sparks" in q


def test_hook_and_outro_have_queries_from_the_items(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    assert all(b["query"] for b in nar.scenes[0].visual_beats)
    assert "Deep Station" in nar.scenes[0].visual_beats[0]["query"]
    assert "Cold Orbit" in nar.scenes[-1].visual_beats[0]["query"]


def test_contract_with_the_stage4_beat_window_calculator(proj, monkeypatch):
    """p3-visual builds shots from calculate_beat_durations(): beats MUST be dicts, and the
    window ids ('<scene>:<1-based n>') are the keys clips are stored under."""
    from stages.stage_4.beat_timing import calculate_beat_durations
    nar, _ = _run(proj, monkeypatch, _good_llm())
    scenes = [s for s in json.loads(json.dumps(nar.to_dict()))["scenes"]]
    timings = {s["scene_id"]: {"start": i * 4.0, "end": i * 4.0 + 4.0, "duration": 4.0}
               for i, s in enumerate(scenes)}
    windows = calculate_beat_durations(scenes, timings, min_duration=0.4)
    assert len(windows) == sum(len(s["visual_beats"]) for s in scenes)
    expected_ids = [f"{s['scene_id']}:{k}" for s in scenes for k in range(1, len(s["visual_beats"]) + 1)]
    assert [w.beat_id for w in windows] == expected_ids
    assert abs(sum(w.duration for w in windows) - 4.0 * len(scenes)) < 0.01


# ─── hook / outro quality gates ──────────────────────────────────────────────

def test_generic_clickbait_hook_falls_back_to_the_question(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm(hook="Wait until you see what happens next."))
    assert "wait until you" not in nar.hook.lower()
    assert nar.hook.startswith("How did the crew escape the sinking station")


def test_empty_outro_gets_a_grounded_closing_line(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm(outro=""))
    assert nar.scenes[-1].is_outro and nar.scenes[-1].text.strip()


# ─── validation + retry ──────────────────────────────────────────────────────

def _scene(text):
    return {"text": text}


def test_validator_flags_wrong_scene_count():
    issues = sq._validate_screen_scenes([_scene("one two")], _items(), "list", band=(1, 100), scene_max=42)
    assert any("expected 4 scenes" in i for i in issues)


def test_validator_flags_overlong_scene_and_band():
    long = " ".join(["word"] * 50)
    scenes = [_scene(f"Alice Park {long}"), _scene("b"), _scene("Bob Chen c"), _scene("d")]
    issues = sq._validate_screen_scenes(scenes, _items(), "list", band=(200, 300), scene_max=42)
    assert any("max 42" in i for i in issues)
    assert any("outside band" in i for i in issues)


def test_validator_flags_context_scene_that_never_names_its_entity():
    scenes = [_scene("Someone does something in Deep Station (2011)."), _scene("x"),
              _scene("Bob Chen in Cold Orbit (2016)."), _scene("y")]
    issues = sq._validate_screen_scenes(scenes, _items(), "list", band=(1, 999), scene_max=42)
    assert any("never names its entity" in i and "Alice" in i for i in issues)


def test_validator_flags_missing_title_or_year_when_the_title_is_new():
    scenes = [_scene("Alice Park seals it in Deep Station."), _scene("Alice is done."),
              _scene("Bob Chen reroutes coolant in Cold Orbit (2016)."), _scene("Bob is done.")]
    issues = sq._validate_screen_scenes(scenes, _items(), "list", band=(1, 999), scene_max=42)
    assert any("2011" in i for i in issues)           # year missing for item 1
    assert not any("Cold Orbit" in i for i in issues)


def test_validator_accepts_title_without_colon_or_with_spoken_subtitle():
    items = _items()
    items[0]["adaptation_title"] = "Deep Station: Rising Tide"
    scenes = [_scene("In Deep Station Rising Tide (2011), Alice Park acts."), _scene("Alice is done."),
              _scene("Bob Chen in Cold Orbit (2016)."), _scene("Bob is done.")]
    assert not [i for i in sq._validate_screen_scenes(scenes, items, "list", band=(1, 999), scene_max=42)
                if "cite" in i]


def test_validator_flags_issue_numbers():
    scenes = [_scene("Alice Park in Deep Station (2011) #5."), _scene("a"),
              _scene("Bob Chen in Cold Orbit (2016)."), _scene("b")]
    assert any("#" in i for i in sq._validate_screen_scenes(scenes, _items(), "list", band=(1, 999), scene_max=42))


def test_explain_final_scene_must_state_the_answer_and_list_language_is_banned():
    ok = [_scene("Alice Park in Deep Station (2011)."), _scene("a"), _scene("Bob Chen in Cold Orbit (2016)."),
          _scene("That's why everyone lives, because the reactor held.")]
    assert not sq._validate_screen_scenes(ok, _items(), "explain", band=(1, 999), scene_max=42)
    bad = list(ok)
    bad[3] = _scene("Everyone lives.")
    assert any("ANSWER" in i for i in sq._validate_screen_scenes(bad, _items(), "explain", band=(1, 999), scene_max=42))
    bad[3] = _scene("The last one on this list is the reason.")
    assert any("list language" in i for i in sq._validate_screen_scenes(bad, _items(), "explain", band=(1, 999), scene_max=42))
    # a LIST question has no such requirements
    assert not sq._validate_screen_scenes([_scene(s["text"]) for s in bad[:3]] + [_scene("Everyone lives.")],
                                          _items(), "list", band=(1, 999), scene_max=42)


def test_real_budget_scales_with_item_count():
    lo2, hi2, cap, per = sq._qa_budget(2)
    lo5, hi5, _, _ = sq._qa_budget(5)
    assert per == 2 and cap == 42
    assert lo2 <= hi2 and lo5 <= hi5 and hi5 >= hi2


def test_one_retry_carries_the_validator_issues(proj, monkeypatch):
    short = _good_llm()
    short["scenes"] = short["scenes"][:3]                    # 3 scenes, 4 expected
    nar, prompts = _run(proj, monkeypatch, [short, _good_llm()])
    assert len(prompts) == 2
    assert "PREVIOUS DRAFT HAD ISSUES" in prompts[1][1] and "expected 4 scenes" in prompts[1][1]
    assert len(nar.scenes) == 6


def test_persistent_issues_ship_with_the_better_draft_not_a_crash(proj, monkeypatch):
    short = _good_llm()
    short["scenes"] = short["scenes"][:3]
    nar, prompts = _run(proj, monkeypatch, [short, short])
    assert len(prompts) == 2
    assert nar.scenes[0].is_intro and nar.scenes[-1].is_outro


def test_llm_failure_uses_the_deterministic_script_with_citations(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, RuntimeError("provider down"))
    assert nar.mode == "screen_qa"
    assert nar.scenes[0].is_intro and nar.scenes[-1].is_outro
    assert len(nar.scenes) == 1 + 2 * 2 + 1
    body = " ".join(s.text for s in nar.scenes)
    for needle in ("Deep Station", "2011", "Cold Orbit", "2016"):
        assert needle in body
    for s in nar.scenes:
        assert _tokens(" ".join(b["text"] for b in s.visual_beats)) == _tokens(s.text)


# ─── prompts ────────────────────────────────────────────────────────────────

def test_explain_question_gets_the_thesis_and_list_question_does_not(proj, monkeypatch):
    _, p_explain = _run(proj, monkeypatch, _good_llm(), question=Q_EXPLAIN)
    assert "ANSWER THESIS" in p_explain[0][1] and "sealed the breach" in p_explain[0][1]
    _, p_list = _run(proj, monkeypatch, _good_llm(), question=Q_LIST)
    assert "ANSWER THESIS" not in p_list[0][1]


def test_prompt_lists_items_in_order_with_title_and_year(proj, monkeypatch):
    _, prompts = _run(proj, monkeypatch, _good_llm())
    user = prompts[0][1]
    assert user.index("Alice Park") < user.index("Bob Chen")
    assert "Deep Station" in user and "2016" in user


@pytest.mark.parametrize("name", ["_SYSTEM_LIST", "_SYSTEM_EXPLAIN"])
def test_writer_prompts_name_no_specific_comic_or_film(name):
    prompt = getattr(sq, name)
    for banned in ("Endgame", "Scott Lang", "Tony Stark", "Ant-Man", "Avengers", "Spider", "Batman"):
        assert banned.lower() not in prompt.lower(), banned
    assert "#" in prompt                                     # states the no-issue-number rule


# ─── persistence ────────────────────────────────────────────────────────────

def test_save_screen_narration_roundtrip(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm())
    path = sq.save_screen_narration(nar, "proj", progress=lambda m: None)
    assert path == proj / "narration.json"
    saved = json.loads(path.read_text())
    assert saved["mode"] == "screen_qa"
    assert saved["scenes"][0]["is_intro"] and saved["scenes"][-1]["is_outro"]
    assert isinstance(saved["scenes"][1]["visual_beats"][0], dict)
    assert saved["hook"] == saved["scenes"][0]["text"]


def test_missing_or_empty_context_is_a_clear_error(proj, monkeypatch):
    with pytest.raises(FileNotFoundError, match="screen_context.json"):
        sq.write_screen_qa("proj")
    _write_context(proj, items=[])
    (proj / "screen_context.json").write_text(json.dumps({"question": Q_EXPLAIN, "items": []}))
    with pytest.raises(RuntimeError, match="no items"):
        sq.write_screen_qa("proj")


def test_words_per_second_matches_the_comic_short_pace():
    from stages.stage_3.write_script import _WORDS_PER_SEC
    assert sq._WORDS_PER_SEC == _WORDS_PER_SEC


@pytest.mark.parametrize("final", [
    "That is why they went through the Quantum Realm.",     # found live: spelled-out "that is why"
    "This is how it worked: the particles shrank them.",
    "That's why the plan worked.",
    "It worked because the particles shrank them.",
])
def test_explain_answer_marker_accepts_natural_phrasings(final):
    scenes = [_scene("Alice Park in Deep Station (2011)."), _scene("a"),
              _scene("Bob Chen in Cold Orbit (2016)."), _scene(final)]
    assert not sq._validate_screen_scenes(scenes, _items(), "explain", band=(1, 999), scene_max=42)


def test_contract_with_the_p3_visual_beat_planner(proj, monkeypatch):
    """Forward contract with video-qa/p3-visual (skipped until stages/stage_5/screen_beats.py is
    merged): its beat rows / timeline windows are planned straight from this narration."""
    planner = pytest.importorskip("stages.stage_5.screen_beats", reason="video-qa/p3-visual not merged")
    nar, _ = _run(proj, monkeypatch, _good_llm())
    narration = json.loads(json.dumps(nar.to_dict()))
    context = json.loads((proj / "screen_context.json").read_text())

    rows = planner.screen_beat_rows(narration, context)
    for scene in narration["scenes"]:
        mine = [r for r in rows if r.scene_id == scene["scene_id"]]
        assert mine, scene["text"]
        assert _tokens(" ".join(r.text for r in mine)) == _tokens(scene["text"])
        assert [r.query for r in mine] == [b["query"] for b in scene["visual_beats"]]   # explicit queries win
    assert rows[0].is_intro and rows[-1].is_outro
    assert len({r.key for r in rows}) == len(rows)                                      # clip keys are unique

    t, timings = 0.0, []
    for scene in narration["scenes"]:
        dur = scene["word_count"] / 3.4
        timings.append({"scene_id": scene["scene_id"], "start": t, "end": t + dur})
        t += dur
    windows = planner.plan_windows(narration, timings, audio_duration=t, screen_context=context)
    assert windows and sum(w.frames for w in windows) / 30 >= t           # video covers the audio
    covered = [k for w in windows for k in w.keys]
    assert covered == [r.key for r in rows]                               # every beat lands in a shot, in order


def test_writer_token_budget_covers_the_dict_beat_json(proj, monkeypatch):
    """Beats are {text, query} objects (~200 tokens per scene), far bigger than the comic Q&A's
    string beats. A flat 2600-token cap truncated a 5-item answer mid-object on a live run
    ('no usable JSON'), so the writer's budget must scale with the scene count."""
    five = [dict(_items()[i % 2], entity=f"Person {i}") for i in range(5)]
    _write_context(proj, items=five)
    seen = {}

    def fake(system, user, **kw):
        seen["max_tokens"] = kw.get("max_tokens")
        return json.dumps(_good_llm(five)), "test-model"

    monkeypatch.setattr(sq, "_call_llm_chain", fake)
    sq.write_screen_qa("proj")
    assert seen["max_tokens"] >= 450 * 10


# ─── fragment cuts must not strand a function word (a video cut mid-phrase) ─────────────────

HOOK_SENTENCE = ("In Deep Station, Alice accidentally spent five years trapped in the lower decks, "
                 "but for her, only five minutes passed.")


def test_fragments_do_not_end_on_a_dangling_function_word():
    frags = sq._split_fragments(HOOK_SENTENCE)
    assert len(frags) >= 2
    assert _tokens(" ".join(frags)) == _tokens(HOOK_SENTENCE)               # still verbatim
    for f in frags[:-1]:
        last = f.split()[-1]
        assert last[-1] in ",;:.!?—-" or last.lower() not in sq._DANGLING, f   # "trapped in | the ..." is the bug


def test_short_text_is_one_fragment():
    assert sq._split_fragments("Alice seals the door.") == ["Alice seals the door."]


def test_hook_beats_use_the_same_non_dangling_split(proj, monkeypatch):
    nar, _ = _run(proj, monkeypatch, _good_llm(hook=HOOK_SENTENCE))
    beats = [b["text"] for b in nar.scenes[0].visual_beats]
    assert len(beats) >= 2
    assert not any(b.split()[-1].lower() in sq._DANGLING and b.split()[-1][-1].isalpha() for b in beats[:-1]), beats
