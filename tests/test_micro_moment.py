"""micro_moment mode v2 (MICRO_MOMENT_V2_SPEC.md): no network — the outliner and
the LLM writer/banner calls are monkeypatched. Verifies (a) write_script() routes
mode="micro_moment" here and raises a clear error when target_moment is missing,
(b) the v2 micro word-band validator accepts 120-200w and rejects out-of-band
drafts, (c) the moment-window selector picks a mini-arc (lead-in + moment +
consequence) around the described moment, with the moment landing mid-window
(not last), (d) the hook mirrors the title (statement, names a character in
sentence 1), (e) the writer declares one of 3 ending styles and the validator
accepts all three, (f) panel dialog/OCR is surfaced to the writer as context
only — the writer must NEVER quote a character's bubble in the narration."""
import pytest

import stages.stage_3.micro_moment as mm
import stages.stage_3.write_script as ws
from stages.stage_3.schema import Beat


def _beat(bid, name, summary, page, chars=("Punisher",)):
    return Beat(id=bid, function="SETUP", name=name, summary=summary,
                page_refs=[page], characters_active=list(chars))


# 6-beat fixture outline; the moment ("throw up / vomit brawl") lands squarely on
# beat id 4. With v2's mini-arc window (lead-in <=3, follow-up <=2) this short
# outline is entirely within the window — nothing gets truncated.
_FIXTURE_BEATS = [
    _beat(1, "Frank arrives", "Punisher tracks a gang to the docks at night.", 3),
    _beat(2, "First blows", "Punisher trades gunfire with the crew.", 6),
    _beat(3, "Juggernaut appears", "Juggernaut smashes in to protect the crew.", 9),
    _beat(4, "Punisher makes Juggernaut vomit",
          "Punisher forces Juggernaut to throw up during their brutal brawl.", 12),
    _beat(5, "Juggernaut reels", "Juggernaut staggers, sickened and humiliated.", 14),
    _beat(6, "Frank walks off", "Punisher walks away from the beaten giant.", 16),
]

_TARGET = "Punisher forces Juggernaut to throw up during their brawl, around page 12"


# A longer 9-beat outline used specifically to exercise the lead-in/follow-up
# CAPS (2-3 lead-in, 1-2 follow-up) — the moment sits in the middle of the full
# outline here, so the window genuinely trims beats off both ends.
_LONG_FIXTURE_BEATS = [
    _beat(1, "Frank tracks the gang", "Punisher tails a smuggling crew across the city for days.", 2),
    _beat(2, "Frank stakes out the docks", "Punisher watches the crew unload crates at the docks at night.", 4),
    _beat(3, "Juggernaut is hired muscle", "The crew brings in Juggernaut as their hired protection.", 7),
    _beat(4, "Frank preps a chemical spike", "Punisher rigs a hidden chemical spike meant for something huge.", 10),
    _beat(5, "Punisher makes Juggernaut vomit",
          "Punisher drives the spike home and Juggernaut violently throws up mid-brawl.", 12),
    _beat(6, "Juggernaut reels", "Juggernaut staggers, sickened and humiliated in front of the crew.", 14),
    _beat(7, "Frank walks off", "Punisher turns and walks away from the beaten giant.", 16),
    _beat(8, "Crew scatters", "The panicking crew scatters into the night.", 18),
    _beat(9, "Frank vanishes", "Punisher disappears into the dark before police arrive.", 20),
]
_LONG_TARGET = "Punisher forces Juggernaut to throw up during their brawl, around page 12"


_HOOK = "The day the Punisher finally made Juggernaut lose his lunch entirely."
# 5 distinct paratactic-ish sentences; the 6th (last) scene varies per ending style.
_BASE_TEXTS = [
    "Frank Castle tracks a violent gang to the flooded docks at night, and he waits "
    "patiently in the shadows for his moment.",
    "Gunfire erupts across the dockside as Frank trades shot for shot with the crew, "
    "but the fight only draws something far bigger out of hiding.",
    "Juggernaut smashes through the warehouse wall to shield his panicking crew, and "
    "the impact levels half the loading dock in one single blow.",
    "Frank drives a hidden chemical spike deep into the towering giant's throat, and "
    "Juggernaut instantly doubles over, gagging and violently throwing up in front of everyone.",
    "Juggernaut staggers backward, sickened and humiliated, while the terrified crew "
    "he came to protect watches in stunned silence.",
]


def _split_beats(text):
    """2 verbatim clause fragments for one scene's visual_beats (word-boundary split),
    matching the WRITER-PICKS-PANEL contract well enough to clear the hard gate."""
    words = text.split()
    mid = len(words) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _make_writer_fixture(last_scene_text, ending_style):
    """A fixture for mm.call_with_chain that emits _BASE_TEXTS + one ending-style-
    specific last scene, matching the 6-beat _FIXTURE_BEATS window 1:1. Each scene
    carries verbatim visual_beats so it clears write_micro_moment's hard gate."""
    def _fixture(*, system, user, models=None, max_tokens=1600, progress=None,
                label="llm", validator=None):
        texts = _BASE_TEXTS + [last_scene_text]
        scenes = [{"text": t, "visual_beats": _split_beats(t), "connective": None,
                   "beat_id": i + 1} for i, t in enumerate(texts)]
        import json
        raw = json.dumps({"hook": _HOOK, "ending_style": ending_style, "scenes": scenes})
        if validator is not None:
            assert validator(raw), "fixture must pass the module's coarse validator"
        return raw, "fake-micro-model"
    return _fixture


# Default fixture used by most tests: "thesis" ending (a 1-sentence meaning line).
_fake_writer_call = _make_writer_fixture(
    "The message is simple: even the unstoppable can be broken, and Frank never says a word.",
    "thesis",
)


def _raising_call(*args, **kwargs):
    raise RuntimeError("no network in tests")


# ── (c) moment-window selection ──────────────────────────────────────────────
def test_select_moment_window_picks_beats_around_the_moment():
    window = mm._select_moment_window(_FIXTURE_BEATS, _TARGET)
    # Short 6-beat outline: the ~6-7 cap isn't binding, so lead-in (up to 3) +
    # moment + follow-up (up to 2) covers the whole outline.
    assert [b.id for b in window] == [1, 2, 3, 4, 5, 6]
    assert len(window) <= mm._MICRO_WINDOW_MAX_BEATS


def test_select_moment_window_uses_page_hint_when_text_is_weak():
    # A moment spec with almost no shared vocabulary still locks onto the right beat
    # via the explicit page number (page 14 -> beat id 5).
    window = mm._select_moment_window(_FIXTURE_BEATS, "that grim payoff on page 14")
    assert 5 in [b.id for b in window]


def test_select_moment_window_empty():
    assert mm._select_moment_window([], _TARGET) == []


def test_select_moment_window_places_moment_mid_not_last():
    """v2 spec: the moment lands ~40-70% into the window, never as the final
    beat — the v1 bug let a late-story moment become the LAST scene, cutting the
    Short off right on the payoff with no consequence beat to land it."""
    window = mm._select_moment_window(_LONG_FIXTURE_BEATS, _LONG_TARGET)
    ids = [b.id for b in window]
    # lead-in capped at 3 (drops beat 1), follow-up capped at 2 (drops beats 8, 9)
    assert ids == [2, 3, 4, 5, 6, 7]
    pos = ids.index(5)  # the moment beat
    assert pos != len(ids) - 1, "moment must not be the last beat in the window"
    frac = (pos + 1) / len(ids)
    assert 0.4 <= frac <= 0.7, f"moment landed at {frac:.0%} of the window"
    assert len(window) <= mm._MICRO_WINDOW_MAX_BEATS


# ── (b) micro word-band validator (v2 band: 120-200) ─────────────────────────
def _scenes(*word_counts):
    return [{"text": "Punisher " + "word " * (n - 1)} for n in word_counts]


def test_micro_band_accepts_120_to_200():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3, 4, 5)]
    hook = "The day the Punisher finally made the giant lose his lunch."  # 11w
    issues = mm._validate_micro_scenes(hook, _scenes(26, 26, 26, 26, 26), beats, "thesis")  # 11+130=141
    assert not any("micro band" in i for i in issues), issues


def test_micro_band_rejects_over_ceiling():
    beats = [Beat(id=i, function="SETUP", name="x") for i in range(1, 10)]
    hook = "The day the Punisher finally made the giant lose his lunch."  # 11w
    # 9 scenes × 38w + 11w hook = 353w > the 320w ceiling (raised 2026-07-20).
    issues = mm._validate_micro_scenes(hook, _scenes(*([38] * 9)), beats, "thesis")
    assert any("micro band" in i for i in issues), issues


def test_micro_band_accepts_whole_story_length_over_58s():
    """Ceiling nới (2026-07-20): a whole-moment micro that runs well past the old ~59s
    cap (here ~250w ≈ 74s) must NOT trip the band lint — longer is fine when the story
    needs it (Master). The old 200w ceiling would have flagged this."""
    beats = [Beat(id=i, function="SETUP", name="x") for i in range(1, 8)]
    hook = "The day the Punisher finally made the giant lose his lunch."  # 11w
    issues = mm._validate_micro_scenes(hook, _scenes(*([34] * 7)), beats, "thesis")  # 11+238=249
    assert not any("micro band" in i for i in issues), issues


def test_micro_band_rejects_under_floor():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2)]
    hook = "The day the Punisher humbled the unstoppable giant."  # 8w-ish, short
    issues = mm._validate_micro_scenes(hook, _scenes(20, 20), beats, "thesis")  # well under 120
    assert any("micro band" in i for i in issues), issues


def test_micro_hook_must_not_be_a_question():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    q = "Why did the Punisher make Juggernaut throw up in their brawl here?"
    issues = mm._validate_micro_scenes(q, _scenes(36, 36, 36), beats, "thesis")
    assert any("question" in i for i in issues), issues


# ── (d) hook mirrors the title: statement + names a character in sentence 1 ──
def test_micro_hook_must_name_a_character_in_first_sentence():
    beats = [Beat(id=i, function="SETUP", name="x", characters_active=["Juggernaut"])
             for i in (1, 2, 3)]
    hook_no_name = "The day someone finally paid for every crime committed on these docks."
    issues = mm._validate_micro_scenes(hook_no_name, _scenes(36, 36, 36), beats, "thesis")
    assert any("name a character" in i for i in issues), issues

    hook_with_name = "The day Juggernaut finally paid for every crime committed on these docks."
    issues2 = mm._validate_micro_scenes(hook_with_name, _scenes(36, 36, 36), beats, "thesis")
    assert not any("name a character" in i for i in issues2), issues2


def test_micro_hook_name_check_skipped_when_beats_carry_no_characters():
    # Beats with no characters_active (e.g. an incomplete outline) must not
    # produce a false-positive "name a character" lint — nothing to match against.
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    hook = "The day someone finally paid for every crime committed on these docks."
    issues = mm._validate_micro_scenes(hook, _scenes(36, 36, 36), beats, "thesis")
    assert not any("name a character" in i for i in issues), issues


# ── (e) ending_style: writer declares one of 3, validator accepts all 3 ──────
def test_validate_micro_scenes_accepts_all_three_ending_styles():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    hook = "The day the Punisher finally made the giant lose his lunch."
    for style in ("thesis", "hardcut", "question"):
        scenes = _scenes(36, 36, 36)
        if style == "question":
            scenes[-1]["text"] = scenes[-1]["text"].rstrip() + "?"
        issues = mm._validate_micro_scenes(hook, scenes, beats, style)
        assert not any("ending_style" in i for i in issues), (style, issues)


def test_validate_micro_scenes_flags_missing_or_unknown_ending_style():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    hook = "The day the Punisher finally made the giant lose his lunch."
    issues_missing = mm._validate_micro_scenes(hook, _scenes(36, 36, 36), beats, None)
    assert any("ending_style" in i for i in issues_missing), issues_missing
    issues_bad = mm._validate_micro_scenes(hook, _scenes(36, 36, 36), beats, "cliffhanger")
    assert any("ending_style" in i for i in issues_bad), issues_bad


def test_validate_micro_scenes_question_ending_requires_question_mark_no_subscribe():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    hook = "The day the Punisher finally made the giant lose his lunch."
    scenes_no_q = _scenes(36, 36, 36)  # last scene has no "?"
    issues = mm._validate_micro_scenes(hook, scenes_no_q, beats, "question")
    assert any("question" in i and "last scene" in i for i in issues), issues

    scenes_subscribe = _scenes(36, 36, 35)
    scenes_subscribe[-1]["text"] = scenes_subscribe[-1]["text"].rstrip() + " please subscribe?"
    issues2 = mm._validate_micro_scenes(hook, scenes_subscribe, beats, "question")
    assert any("subscribe" in i for i in issues2), issues2

    scenes_ok = _scenes(36, 36, 35)
    scenes_ok[-1]["text"] = scenes_ok[-1]["text"].rstrip() + "?"
    issues3 = mm._validate_micro_scenes(hook, scenes_ok, beats, "question")
    assert not any("last scene" in i or "subscribe" in i for i in issues3), issues3


@pytest.mark.parametrize("style,last_line", [
    ("thesis", "The message is simple: even the unstoppable can be broken, and Frank "
               "never says a word."),
    ("hardcut", "Frank turns and walks away from the beaten giant without a single word."),
    ("question", "So if the Punisher can drop Juggernaut cold, who else on his list "
                 "should be worried?"),
])
def test_write_micro_moment_parses_all_three_ending_styles(monkeypatch, style, last_line):
    """End-to-end: the writer JSON's "ending_style" field survives onto the final
    Narration for each of the 3 declared shapes (thesis/hardcut/question)."""
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _make_writer_fixture(last_line, style))
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Punisher vs Juggernaut", "target_moment": _TARGET,
                     "plot_summary": "Frank Castle fights the Juggernaut on the docks."}
    story_pages = [{"page_number": p, "is_story_page": True} for p in range(2, 17)]

    nar = ws.write_script(comic_context, story_pages, "micro_moment", debug_dump={})
    assert nar.ending_style == style


# ── (f) panel dialog/OCR surfaced as context (never quoted in narration) ─────
def test_window_dialog_block_includes_verbatim_ocr_quote():
    window = [Beat(id=5, function="CLIMAX", name="Vomit moment", page_refs=[12])]
    story_pages = [
        {"page_number": 12, "panels": [
            {"index": 0, "dialog": [
                {"type": "speech", "speaker": "Juggernaut",
                 "text": "a paraphrased guess", "ocr": "I'M GONNA BE SICK!"},
            ]},
        ]},
    ]
    block = mm._window_dialog_block(window, story_pages)
    assert "PANEL DIALOG" in block
    assert "I'M GONNA BE SICK!" in block
    assert "a paraphrased guess" not in block  # OCR wins over the VLM's `text`


def test_window_dialog_block_empty_when_no_dialog_or_no_pages():
    window = [Beat(id=1, function="SETUP", name="x", page_refs=[2])]
    assert mm._window_dialog_block(window, [{"page_number": 2, "panels": [
        {"index": 0, "dialog": []}]}]) == ""
    assert mm._window_dialog_block(window, None) == ""


def test_micro_write_system_forbids_quoting_dialogue():
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "never speak the bubbles" in low
    assert "no quotation marks around anything a character" in low
    assert "narrator-voice" in low
    # the old "quote the payoff verbatim" instruction must be GONE, not just softened
    assert "quote the payoff" not in low
    assert "quote-fidelity" not in low


# ── RULE 1 (retention): hook is a CONCRETE twist, abstract/poetic hooks banned ──
def test_micro_write_system_hook_is_concrete_twist_bans_abstract():
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "concrete twist" in low                 # winning shape is a concrete reversal
    assert "concrete, never abstract" in low       # the abstract ban is explicit
    assert "she planned every second" in low       # a named banned abstract example
    # reconciled with the old "don't force a paradox" note — the story's REAL reversal is wanted,
    # only a disconnected invented riddle is banned
    assert "invented riddle is banned" in low


# ── RULE 2 (retention): last line is a SHORT, quotable loop back to the hook ────
def test_micro_write_system_outro_is_quotable_loop():
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "the last line is the loop" in low
    assert "quotable" in low
    assert "close the hook" in low


# ── STORY-FIRST input builder (2026-07-16): story sources IN, panel prose OUT ──
def test_micro_write_system_is_story_first_not_art_description():
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "tell the story, not the pictures" in low
    # the writer is explicitly told NOT to pick panels / describe art
    assert "you do not pick pages or panels" in low
    assert "story sources" in low


def test_call_micro_writer_surfaces_story_meaning_and_moments(monkeypatch):
    """story_meaning (marked THEME ONLY) and notable_moments (clean story beats) must reach
    the writer prompt when Stage 1 supplied them — these are the story-language sources the
    writer draws its wording from instead of VLM panel prose."""
    captured = {}

    def _capture(*, system, user, models=None, max_tokens=1600, progress=None,
                 label="llm", validator=None):
        captured["user"] = user
        return _fake_writer_call(system=system, user=user, models=models,
                                 max_tokens=max_tokens, progress=progress,
                                 label=label, validator=validator)

    monkeypatch.setattr(mm, "call_with_chain", _capture)
    cc = {"title": "t", "plot_summary": "p",
          "story_meaning": "doubt is the real weapon here",
          "notable_moments": ["young bruce watches the show", "bruce sits in arkham"]}
    mm._call_micro_writer(_FIXTURE_BEATS, cc, _TARGET, model=None, progress=None,
                          debug_dump={}, story_pages=None)
    u = captured["user"]
    assert "STORY MEANING" in u and "THEME ONLY" in u and "doubt is the real weapon here" in u
    assert "KEY STORY MOMENTS" in u and "young bruce watches the show" in u


def test_call_micro_writer_omits_sources_when_absent(monkeypatch):
    """Old projects (no story_meaning / notable_moments) degrade to plot-only — the sources
    blocks must NOT appear, and nothing must crash."""
    captured = {}

    def _capture(*, system, user, models=None, max_tokens=1600, progress=None,
                 label="llm", validator=None):
        captured["user"] = user
        return _fake_writer_call(system=system, user=user, models=models,
                                 max_tokens=max_tokens, progress=progress,
                                 label=label, validator=validator)

    monkeypatch.setattr(mm, "call_with_chain", _capture)
    mm._call_micro_writer(_FIXTURE_BEATS, {"title": "x", "plot_summary": "y"}, _TARGET,
                          model=None, progress=None, debug_dump={}, story_pages=None)
    assert "STORY MEANING" not in captured["user"]
    assert "KEY STORY MOMENTS" not in captured["user"]


# ── vector-PIN + GROUND-CHECK (2026-07-16) ───────────────────────────────────
def _crc_embed(texts):
    """Deterministic bag-of-words embedding (crc32-bucketed, 4096-dim, unit-normed) — a
    fragment sharing content words with a panel's embed_text scores high; disjoint text
    scores ~0. Stands in for the real embedding backend so the pin argmax is predictable
    without a network/model."""
    import re as _re
    import zlib
    import numpy as np
    out = []
    for t in texts:
        v = np.zeros(4096, dtype="float32")
        for tok in _re.findall(r"[a-z]+", (t or "").lower()):
            v[zlib.crc32(tok.encode()) % 4096] += 1.0
        n = float(np.linalg.norm(v))
        out.append(v / n if n else v)
    return out


def test_pin_beats_by_vector_argmax_and_ground_check(monkeypatch):
    import stages._embedding as emb
    monkeypatch.setattr(emb, "backend_name", lambda: "openai")
    monkeypatch.setattr(emb, "embed_batch", _crc_embed)

    window = [Beat(id=1, function="SETUP", name="a", page_refs=[10]),
              Beat(id=2, function="SETUP", name="b", page_refs=[11]),
              Beat(id=3, function="SETUP", name="c", page_refs=[12])]
    story_pages = [
        {"page_number": 10, "is_story_page": True, "panels": [
            {"index": 0, "description": "quiet empty rooftop"},
            {"index": 1, "description": "hero punches villain fist"}]},
        {"page_number": 11, "is_story_page": True, "panels": [
            {"index": 0, "description": "villain grows huge monster"}]},
        {"page_number": 12, "is_story_page": True, "panels": [
            {"index": 0, "description": "crowd flees burning street"}]},
    ]
    scenes = [
        {"text": "hero punches villain fist", "visual_beats": ["hero punches villain fist"]},
        {"text": "penguin waddles across frozen antarctica",   # matches NOTHING on any page
         "visual_beats": ["penguin waddles across frozen antarctica"]},
        {"text": "villain grows huge monster", "visual_beats": ["villain grows huge monster"]},
    ]
    best = mm._pin_beats_by_vector(scenes, window, story_pages, floor=0.3, log=lambda _m: None)

    # scene 1 fragment → argmax is page 10 panel 1 (exact content overlap), pinned as a dict
    vb0 = scenes[0]["visual_beats"][0]
    assert isinstance(vb0, dict) and vb0["page"] == 10 and vb0["panel"] == 1
    # scene 2 fragment matches no window panel → left UNPINNED (Stage 5 matcher handles it)
    vb1 = scenes[1]["visual_beats"][0]
    assert isinstance(vb1, dict) and vb1["page"] is None and vb1["panel"] is None

    # ground-check flags the ungrounded MIDDLE scene, never the grounded scene 1, never last
    issues = mm._ground_issues(scenes, window, best, 0.3)
    assert any("scene 2 describes something not drawn" in i for i in issues), issues
    assert not any("scene 1 describes" in i for i in issues)
    assert not any("scene 3 describes" in i for i in issues)  # last scene is exempt (thematic landing)


def test_pin_beats_by_vector_graceful_without_backend(monkeypatch):
    """No embedding backend → no pins, no ground scores, beats left untouched (Stage 5 matcher
    then handles every beat, identical to recap/Q&A)."""
    import stages._embedding as emb
    monkeypatch.setattr(emb, "backend_name", lambda: "none")
    scenes = [{"text": "hero wins", "visual_beats": ["hero wins"]}]
    window = [Beat(id=1, function="SETUP", name="a", page_refs=[10])]
    story_pages = [{"page_number": 10, "is_story_page": True,
                    "panels": [{"index": 0, "description": "hero wins"}]}]
    best = mm._pin_beats_by_vector(scenes, window, story_pages, floor=0.3, log=lambda _m: None)
    assert best == []
    assert scenes[0]["visual_beats"] == ["hero wins"]            # untouched (still a string)
    assert mm._ground_issues(scenes, window, best, 0.3) == []    # no scores → no issues


def test_pin_beats_by_vector_skips_on_no_embed_env(monkeypatch):
    """--no-embed (STAGE3_NO_EMBED=1) must short-circuit BEFORE any network call — even
    with a live backend configured, embed_batch must never be invoked."""
    import stages._embedding as emb
    monkeypatch.setattr(emb, "backend_name", lambda: "openai")  # backend IS configured
    def _boom(_texts):
        raise AssertionError("embed_batch called despite STAGE3_NO_EMBED=1")
    monkeypatch.setattr(emb, "embed_batch", _boom)
    monkeypatch.setenv("STAGE3_NO_EMBED", "1")

    scenes = [{"text": "hero wins", "visual_beats": ["hero wins"]}]
    window = [Beat(id=1, function="SETUP", name="a", page_refs=[10])]
    story_pages = [{"page_number": 10, "is_story_page": True,
                    "panels": [{"index": 0, "description": "hero wins"}]}]
    best = mm._pin_beats_by_vector(scenes, window, story_pages, floor=0.3, log=lambda _m: None)
    assert best == []
    assert scenes[0]["visual_beats"] == ["hero wins"]  # untouched, no network attempted


def test_call_micro_writer_surfaces_dialog_block_in_user_prompt(monkeypatch):
    """The dialog found on the window's pages must actually reach the LLM call's
    user message (not just be computed and discarded)."""
    captured = {}

    def _capture(*, system, user, models=None, max_tokens=1600, progress=None,
                label="llm", validator=None):
        captured["user"] = user
        captured["system"] = system
        return _fake_writer_call(system=system, user=user, models=models,
                                 max_tokens=max_tokens, progress=progress,
                                 label=label, validator=validator)

    monkeypatch.setattr(mm, "call_with_chain", _capture)
    story_pages = [
        {"page_number": 12, "panels": [
            {"index": 0, "dialog": [
                {"type": "speech", "speaker": "Juggernaut", "text": "guess",
                 "ocr": "I'M GONNA BE SICK!"},
            ]},
        ]},
    ]
    mm._call_micro_writer(_FIXTURE_BEATS, {"title": "x", "plot_summary": "y"}, _TARGET,
                          model=None, progress=None, debug_dump={}, story_pages=story_pages)
    assert "PANEL DIALOG" in captured["user"]
    assert "I'M GONNA BE SICK!" in captured["user"]


def test_call_micro_writer_omits_dialog_block_when_none_available(monkeypatch):
    captured = {}

    def _capture(*, system, user, models=None, max_tokens=1600, progress=None,
                label="llm", validator=None):
        captured["user"] = user
        return _fake_writer_call(system=system, user=user, models=models,
                                 max_tokens=max_tokens, progress=progress,
                                 label=label, validator=validator)

    monkeypatch.setattr(mm, "call_with_chain", _capture)
    mm._call_micro_writer(_FIXTURE_BEATS, {"title": "x", "plot_summary": "y"}, _TARGET,
                          model=None, progress=None, debug_dump={}, story_pages=None)
    assert "PANEL DIALOG" not in captured["user"]


# ── (a) dispatch routing + missing target_moment ─────────────────────────────
def test_missing_target_moment_raises(monkeypatch):
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "m"))
    monkeypatch.setattr(mm, "call_with_chain", _fake_writer_call)
    with pytest.raises(ValueError, match="target_moment"):
        ws.write_script({"title": "Punisher vs Juggernaut"}, [], "micro_moment")


def test_write_micro_moment_end_to_end(monkeypatch):
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _fake_writer_call)
    # banner LLM lives in write_script.py; stub it to fall back to the title.
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Punisher vs Juggernaut", "target_moment": _TARGET,
                     "plot_summary": "Frank Castle fights the Juggernaut on the docks."}
    story_pages = [{"page_number": p, "is_story_page": True} for p in range(3, 17)]

    nar = ws.write_script(comic_context, story_pages, "micro_moment", debug_dump={})

    # dispatch actually routed here (not the recap path)
    assert nar.mode == "micro_moment"
    # hook (is_intro) + one scene per windowed beat (the whole 6-beat outline)
    assert len(nar.scenes) == 7
    assert nar.scenes[0].is_intro
    assert "?" not in nar.scenes[0].text  # hook is a statement, not a question
    # body scenes are beat-anchored to the moment window (beats 1..6)
    assert [s.beat_id for s in nar.scenes[1:]] == [1, 2, 3, 4, 5, 6]
    for s in nar.scenes[1:]:
        assert not s.is_intro
        assert s.page_ref in {3, 6, 9, 12, 14, 16}
    # banner falls back to the working title (no question banner for this mode)
    assert nar.banner_title == "Punisher vs Juggernaut"
    # total lands ~35-60s (120-200 words)
    assert mm._MICRO_WORDS_MIN <= nar.total_word_count <= mm._MICRO_WORDS_MAX
    assert nar.ending_style == "thesis"


# ── visual_beats plumbing (immortal-hulk-13 held-panel bug) ──────────────────
def test_write_micro_moment_keeps_writer_visual_beats(monkeypatch):
    """When the writer response carries visual_beats per scene, they must survive
    _anchor_scenes_to_beats + _to_narration unchanged onto the final Narration —
    otherwise Stage 5 (_build_shots_per_chunk) holds ONE panel for the whole scene."""
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _fake_writer_call)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Punisher vs Juggernaut", "target_moment": _TARGET,
                     "plot_summary": "Frank Castle fights the Juggernaut on the docks."}
    story_pages = [{"page_number": p, "is_story_page": True} for p in range(3, 17)]

    nar = ws.write_script(comic_context, story_pages, "micro_moment", debug_dump={})

    assert len(nar.scenes) == 7
    assert nar.scenes[0].visual_beats == []  # hook/intro carries no beats
    for s in nar.scenes[1:]:
        assert len(s.visual_beats) == 2
        # beats concatenate back to the exact scene text (verbatim contract)
        assert " ".join(s.visual_beats).split() == s.text.split()


def test_validate_micro_scenes_lints_long_scene_missing_beats():
    """A body scene over the lint floor with no visual_beats gets a SOFT issue
    (never raises) naming visual_beats; a short scene or one WITH beats does not."""
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2)]
    hook = "The day the Punisher humbled the unstoppable giant entirely tonight."
    long_text = "Punisher " + "word " * 14  # 15w, one event, no visual_beats
    scenes_missing = [{"text": long_text}, {"text": "Punisher word"}]  # 2nd is short, exempt
    issues = mm._validate_micro_scenes(hook, scenes_missing, beats, "thesis")
    assert any("visual_beats" in i and "scene 1" in i for i in issues), issues
    assert not any("scene 2" in i and "visual_beats" in i for i in issues), issues

    # Same long scene WITH verbatim visual_beats — no lint fires for it.
    words = long_text.split()
    mid = len(words) // 2
    scenes_with_beats = [
        {"text": long_text, "visual_beats": [" ".join(words[:mid]), " ".join(words[mid:])]},
        {"text": "Punisher word"},
    ]
    issues2 = mm._validate_micro_scenes(hook, scenes_with_beats, beats, "thesis")
    assert not any("visual_beats" in i for i in issues2), issues2


def _no_beats_writer_call(*, system, user, models=None, max_tokens=1600, progress=None,
                          label="llm", validator=None):
    """Same 6 scenes/hook as _fake_writer_call but with NO visual_beats at all —
    the writer regression that produced the immortal-hulk-13 / batman-killer-croc
    run-1 held-panel bug. Ignores the `issues` kwarg on retry (always broken), so
    write_micro_moment's bounded retry loop genuinely exhausts."""
    texts = _BASE_TEXTS + [
        "The message is simple: even the unstoppable can be broken, and Frank never says a word."
    ]
    scenes = [{"text": t, "connective": None, "beat_id": i + 1} for i, t in enumerate(texts)]
    import json
    raw = json.dumps({"hook": _HOOK, "ending_style": "thesis", "scenes": scenes})
    if validator is not None:
        assert validator(raw), "fixture must pass the module's coarse validator"
    return raw, "fake-micro-model"


def test_write_micro_moment_raises_on_missing_visual_beats(monkeypatch):
    """write_micro_moment must HARD-FAIL (never ship) when the writer keeps
    returning long body scenes with no visual_beats through every retry —
    Stage 5 would hold ONE panel per scene otherwise (the immortal-hulk-13 /
    batman-killer-croc run-1 flat-video bug this hardening exists to catch)."""
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _no_beats_writer_call)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Punisher vs Juggernaut", "target_moment": _TARGET,
                     "plot_summary": "Frank Castle fights the Juggernaut on the docks."}
    story_pages = [{"page_number": p, "is_story_page": True} for p in range(3, 17)]

    with pytest.raises(RuntimeError, match="no visual_beats"):
        ws.write_script(comic_context, story_pages, "micro_moment", debug_dump={})


def test_write_micro_moment_ships_with_soft_only_issues(monkeypatch):
    """A soft-only issue (an overlong hook) must NOT block shipping even after every
    retry is exhausted — only the HARD structural markers in
    _MICRO_HARD_ISSUE_MARKERS do that; this locks the hard/soft boundary."""
    long_hook = ("The day the Punisher finally, after weeks of stalking this crew through "
                 "every flooded alley on the docks at night, made Juggernaut lose his lunch.")
    assert len(long_hook.split()) > mm._MICRO_HOOK_MAX_WORDS

    def _long_hook_writer(*, system, user, models=None, max_tokens=1600, progress=None,
                          label="llm", validator=None):
        texts = _BASE_TEXTS + [
            "The message is simple: even the unstoppable can be broken, and Frank never says a word."
        ]
        scenes = [{"text": t, "visual_beats": _split_beats(t), "connective": None,
                   "beat_id": i + 1} for i, t in enumerate(texts)]
        import json
        raw = json.dumps({"hook": long_hook, "ending_style": "thesis", "scenes": scenes})
        if validator is not None:
            assert validator(raw), "fixture must pass the module's coarse validator"
        return raw, "fake-micro-model"

    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_FIXTURE_BEATS, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _long_hook_writer)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    logged: list[str] = []
    comic_context = {"title": "Punisher vs Juggernaut", "target_moment": _TARGET,
                     "plot_summary": "Frank Castle fights the Juggernaut on the docks."}
    story_pages = [{"page_number": p, "is_story_page": True} for p in range(3, 17)]

    nar = ws.write_script(comic_context, story_pages, "micro_moment",
                          progress=logged.append, debug_dump={})

    assert nar.mode == "micro_moment"  # shipped despite the unresolved soft hook-length issue
    assert any("shipping with unresolved issue" in m for m in logged), logged


# ── (g) QUOTE-FIDELITY lint (2026-07-16 tilt: real quote shortened to 'VENGEANCE.') ──
def test_quote_issues_flags_non_verbatim_quote():
    dialog_lines = ["WE AM SMASH FOR VENGEANCE"]
    scenes = [{"text": "and the only thing left to say is 'VENGEANCE, DESTROY THEM ALL.'"}]
    issues = mm._quote_issues(scenes, dialog_lines)
    assert any("quote:" in i and "scene 1" in i for i in issues), issues


def test_quote_issues_passes_verbatim_quote():
    dialog_lines = ["...THEY'VE ACTUALLY SURPRISED ME..."]
    scenes = [{"text": "and Blackheart admits: '...THEY'VE ACTUALLY SURPRISED ME...'"}]
    issues = mm._quote_issues(scenes, dialog_lines)
    assert issues == []


def test_quote_issues_noop_without_dialog_lines():
    # No dialog block at all -> nothing to verify against, stays silent even on a
    # fabricated-looking quote.
    scenes = [{"text": "and the demon screams 'I WILL NEVER SURRENDER TO YOU.'"}]
    assert mm._quote_issues(scenes, []) == []


def test_quote_issues_ignores_plain_possessives_and_contractions():
    dialog_lines = ["WE AM SMASH FOR VENGEANCE"]
    scenes = [{"text": "Blackheart's own hell turns against him, and it isn't over yet."}]
    assert mm._quote_issues(scenes, dialog_lines) == []


def test_quote_issues_double_quotes_also_checked():
    dialog_lines = ["I AM THE DANGER"]
    scenes = [{"text": 'He screams "I am the one who knocks" at the sky.'}]
    issues = mm._quote_issues(scenes, dialog_lines)
    assert any("quote:" in i for i in issues), issues


def test_validate_micro_scenes_surfaces_quote_issue_via_dialog_lines():
    beats = [Beat(id=i, function="SETUP", name="x") for i in (1, 2, 3)]
    hook = "The day the Punisher finally made the giant lose his lunch."
    scenes = _scenes(36, 36, 36)
    scenes[-1]["text"] += " and the crowd roars 'WE HAVE WON THIS FIGHT FOREVER.'"
    issues = mm._validate_micro_scenes(hook, scenes, beats, "thesis",
                                       dialog_lines=["THE FIGHT IS OVER NOW"])
    assert any("quote:" in i for i in issues), issues
    # Same call with no dialog_lines given (default None) must NOT lint quotes at all.
    issues2 = mm._validate_micro_scenes(hook, scenes, beats, "thesis")
    assert not any("quote:" in i for i in issues2), issues2


# ── (h) RESOLUTION beat gate (2026-07-16 tilt: arc stopped cold on the moment,
#     never reaching the villain's defeat 8 beats later in a 20-beat outline) ──
def _res_beat(bid, name, summary, pages, chars=()):
    return Beat(id=bid, function="SETUP", name=name, summary=summary,
                page_refs=list(pages), characters_active=list(chars))


def test_append_resolution_beat_noop_when_window_already_reaches_the_end():
    beats_all = [_res_beat(1, "a", "setup", [1]), _res_beat(2, "b", "defeat", [2])]
    window = list(beats_all)  # window's last id (2) == beats_all's last id (2)
    out = mm._append_resolution_beat(window, beats_all, {"plot_summary": "The villain is defeated."})
    assert out == window


def test_append_resolution_beat_noop_when_ending_has_no_resolution_language():
    beats_all = [_res_beat(i, f"b{i}", "the story continues elsewhere", [i]) for i in range(1, 11)]
    window = beats_all[3:6]  # ids 4,5,6 — well short of the last beat (id 10)
    cc = {"plot_summary": "Meanwhile, across town, a new mystery quietly begins to unfold."}
    out = mm._append_resolution_beat(window, beats_all, cc)
    assert out == window


def test_append_resolution_beat_appends_when_tail_describes_defeat():
    beats_all = [_res_beat(i, f"b{i}", f"event number {i} happens", [i], ["Red Hulk"])
                 for i in range(1, 19)]
    beats_all.append(_res_beat(19, "Curse lands", "Red Hulk becomes the Ghost Rider", [19],
                               ["Red Hulk"]))
    beats_all.append(_res_beat(20, "Defeat", "Blackheart's reign collapses and he is banished",
                               [30, 31], ["Red Hulk", "Blackheart"]))
    window = beats_all[8:12]  # peak around id 11, follow-up capped well short of id 20
    cc = {"plot_summary": "Red Hulk becomes the Spirit of Vengeance."}  # no ending language here
    logged = []
    out = mm._append_resolution_beat(window, beats_all, cc, log=logged.append)
    assert len(out) == len(window) + 1
    new_beat = out[-1]
    assert new_beat.function == "RESOLUTION"
    assert new_beat.page_refs == [19, 30, 31]  # union of the LAST TWO outline beats' pages
    assert "banished" in new_beat.summary.lower() or "collapses" in new_beat.summary.lower()
    assert any("appended RESOLUTION beat" in m for m in logged), logged


def test_append_resolution_beat_prefers_plot_summary_ending_when_it_has_resolution_language():
    beats_all = [_res_beat(i, f"b{i}", "irrelevant VLM-flavored beat text", [i])
                 for i in range(1, 6)]
    window = beats_all[:2]
    cc = {"plot_summary": "The hero fights on. In the end, the villain is finally defeated."}
    out = mm._append_resolution_beat(window, beats_all, cc)
    assert len(out) == len(window) + 1
    assert "defeated" in out[-1].summary.lower()


def test_resolution_label_skips_post_climax_epilogue_tease_in_story_arc():
    """Live bug found during the 2026-07-16 tilt: plot_summary stops at the moment
    itself (no defeat language anywhere), and the richer story_arc block's OWN
    final sentence is a coda tease for the NEXT story ('a demonic figure schemes...')
    — a blind 'last sentence' read would wrongly surface that tease as the
    'resolution'. Scanning BACKWARD must skip the tease and land on the real
    defeat sentence buried a few sentences earlier."""
    cc = {
        "plot_summary": "The hero is bonded with a power that judges the guilty.",
        "summary": {"story_arc": (
            "The villain tears through the city unchecked. "
            "The heroes regroup and strike back. "
            "A fiery motorcycle seals the breach; the villain's reign collapses. "
            "The team confirms the mission is over with command. "
            "A rival hero questions their methods, but they hold firm. "
            "In a final coda, a new villain schemes elsewhere, foreshadowing the next conflict."
        )},
    }
    tail = [Beat(id=1, function="SETUP", name="x", summary="irrelevant tail text")]
    label = mm._resolution_label(cc, tail)
    assert "collapses" in label.lower()
    assert "coda" not in label.lower() and "schemes" not in label.lower()


def test_resolution_label_ignores_negated_midstory_beat_language():
    """'cannot beat a god of Hell' (a mid-story COMPLICATION/failure line) must NOT
    be mistaken for resolution language — "beat"/"beats" bare are excluded from the
    keyword set precisely because of this ambiguity, and the bounded tail-window
    also keeps an early sentence like this out of scan range entirely."""
    cc = {"plot_summary": (
        "The villain feeds on rage. The hero throws everything at the villain and "
        "gets hurled through a wall for it; raw muscle alone cannot beat a god of "
        "Hell. In the final push, a new power is passed to the hero. Even the "
        "villain admits they are surprised."
    )}
    tail = [Beat(id=1, function="SETUP", name="x", summary="the story trails off here")]
    label = mm._resolution_label(cc, tail)
    assert label == "the story trails off here"  # falls all the way to the tail-beat fallback


def test_append_resolution_beat_respects_window_size_cap():
    # window already at the mini-arc cap — must not push it over cap+1.
    beats_all = [_res_beat(i, f"b{i}", "the villain is finally defeated", [i])
                 for i in range(1, 12)]
    window = beats_all[:mm._MICRO_WINDOW_MAX_BEATS + 1]  # already over cap
    cc = {"plot_summary": "The villain is finally defeated."}
    out = mm._append_resolution_beat(window, beats_all, cc)
    assert out == window  # no-op: already over the size budget


# ── (i) RESOLUTION beat feeds a GIST + anti-copy reference, not a bare source
#     sentence (2026-07-16 tilt #2: writer copied the source's own vague nouns —
#     "a symbiote tears itself free", "a flaming motorcycle seals the breach") ──
def test_wrap_resolution_reference_marks_do_not_copy():
    wrapped = mm._wrap_resolution_reference("A symbiote tears itself free; the villain's reign collapses.")
    assert "do not copy" in wrapped.lower()
    assert "named characters" in wrapped.lower()
    assert "A symbiote tears itself free" in wrapped  # reference still present, just marked


def test_append_resolution_beat_name_carries_gist_and_reference_not_bare_sentence():
    beats_all = [_res_beat(i, f"b{i}", "irrelevant text", [i]) for i in range(1, 6)]
    window = beats_all[:2]
    cc = {"plot_summary": "A symbiote tears itself free; the villain's reign finally collapses."}
    out = mm._append_resolution_beat(window, beats_all, cc)
    name = out[-1].name
    assert "do not copy" in name.lower()
    assert name != "A symbiote tears itself free; the villain's reign finally collapses."


# ── (j) AMBIGUOUS-SUBJECT lint: anonymous prop acting alone reads like a VLM read ──
def test_ambiguous_subject_issues_flags_anonymous_prop_actor():
    beats = [Beat(id=1, function="SETUP", name="x", characters_active=["Red Hulk"])]
    scenes = [{"text": "A symbiote tears itself free, and a flaming motorcycle seals the breach."}]
    issues = mm._ambiguous_subject_issues(scenes, beats)
    assert any("ambiguous:" in i and "scene 1" in i for i in issues), issues


def test_ambiguous_subject_issues_passes_when_named_character_present():
    beats = [Beat(id=1, function="SETUP", name="x", characters_active=["Venom", "Ghost Rider"])]
    scenes = [{"text": "Venom tears free of its host, and Ghost Rider's bike seals the breach."}]
    assert mm._ambiguous_subject_issues(scenes, beats) == []


def test_ambiguous_subject_issues_noop_without_known_character_names():
    beats = [Beat(id=1, function="SETUP", name="x")]  # no characters_active anywhere
    scenes = [{"text": "A symbiote tears itself free from the demon's grip."}]
    assert mm._ambiguous_subject_issues(scenes, beats) == []


def test_micro_write_system_requires_named_subject():
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "named subject only" in low
    assert "a symbiote" in low  # the exact failure example is called out


# ── (k) QUOTE SPEAKER: quote must be attributed to the correct speaker ───────
#     (2026-07-16 tilt #3: a Blackheart admission narrated as "a burning Hulk
#     who roars, '...'" — WRONG speaker, real name available in beat context)
def test_named_speaker_rejects_vague_placeholders():
    assert mm._named_speaker("a figure") is None
    assert mm._named_speaker("a man") is None
    assert mm._named_speaker("?") is None
    assert mm._named_speaker("") is None
    assert mm._named_speaker(None) is None


def test_named_speaker_keeps_real_names():
    assert mm._named_speaker("Blackheart") == "Blackheart"
    assert mm._named_speaker("Juggernaut") == "Juggernaut"


def test_window_dialog_block_shows_speaker_unclear_for_vague_placeholder():
    window = [Beat(id=5, function="CLIMAX", name="x", page_refs=[22])]
    story_pages = [
        {"page_number": 22, "panels": [
            {"index": 0, "dialog": [
                {"type": "speech", "speaker": "a figure", "ocr": "...SURPRISED ME..."},
            ]},
        ]},
    ]
    block = mm._window_dialog_block(window, story_pages)
    assert "speaker unclear" in block
    assert "a figure" not in block


def test_quote_speaker_issues_flags_misattributed_quote():
    known_names = {"blackheart", "red hulk"}
    dialog_entries = [(22, "Blackheart", "...THEY'VE ACTUALLY SURPRISED ME...")]
    scenes = [{"text": "Red Hulk transforms, and a burning Hulk who roars, "
                       "'...THEY'VE ACTUALLY SURPRISED ME...'"}]
    issues = mm._quote_speaker_issues(scenes, dialog_entries, known_names)
    assert any("quote-speaker:" in i and "scene 1" in i for i in issues), issues


def test_quote_speaker_issues_passes_when_correctly_attributed():
    known_names = {"blackheart", "red hulk"}
    dialog_entries = [(22, "Blackheart", "...THEY'VE ACTUALLY SURPRISED ME...")]
    scenes = [{"text": "Red Hulk transforms, and Blackheart admits, "
                       "'...THEY'VE ACTUALLY SURPRISED ME...'"}]
    assert mm._quote_speaker_issues(scenes, dialog_entries, known_names) == []


def test_quote_speaker_issues_noop_when_entry_speaker_is_vague():
    # entry's own speaker tag is a placeholder ("a figure") — nothing solid to
    # compare against, so this mechanical check must stay silent (the prompt's
    # QUOTE SPEAKER rule is the primary defense for this case).
    known_names = {"blackheart", "red hulk"}
    dialog_entries = [(22, "a figure", "...THEY'VE ACTUALLY SURPRISED ME...")]
    scenes = [{"text": "Red Hulk transforms, and a burning Hulk who roars, "
                       "'...THEY'VE ACTUALLY SURPRISED ME...'"}]
    assert mm._quote_speaker_issues(scenes, dialog_entries, known_names) == []


def test_validate_micro_scenes_surfaces_quote_speaker_issue_via_dialog_entries():
    beats = [Beat(id=i, function="SETUP", name="x",
                  characters_active=["Blackheart", "Red Hulk"]) for i in (1, 2, 3)]
    hook = "The day Red Hulk finally became the Spirit of Vengeance."
    scenes = _scenes(36, 36, 36)
    scenes[-1]["text"] += (" and a burning Hulk who roars, "
                           "'...THEY'VE ACTUALLY SURPRISED ME...'")
    issues = mm._validate_micro_scenes(
        hook, scenes, beats, "thesis",
        dialog_entries=[(22, "Blackheart", "...THEY'VE ACTUALLY SURPRISED ME...")])
    assert any("quote-speaker:" in i for i in issues), issues
    # default (no dialog_entries) must NOT lint speaker attribution at all.
    issues2 = mm._validate_micro_scenes(hook, scenes, beats, "thesis")
    assert not any("quote-speaker:" in i for i in issues2), issues2


def test_micro_write_system_tells_writer_to_describe_dialogue_indirectly():
    # QUOTE SPEAKER (attributing a quote to the right character) is moot now that
    # quoting dialogue at all is banned — the replacement rule instead tells the
    # writer to retell a dialogue-driven moment in indirect story language.
    low = mm._MICRO_WRITE_SYSTEM.lower()
    assert "describe it indirectly in story language" in low
    assert "quote speaker" not in low


if __name__ == "__main__":
    mp = pytest.MonkeyPatch()
    try:
        test_select_moment_window_picks_beats_around_the_moment()
        test_select_moment_window_places_moment_mid_not_last()
        test_micro_band_accepts_120_to_200()
        test_micro_band_rejects_over_ceiling()
        test_write_micro_moment_end_to_end(mp)
        print("ok")
    finally:
        mp.undo()
