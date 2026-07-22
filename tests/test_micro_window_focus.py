"""Focus-filter for micro_moment's `_select_moment_window` (team-book fix, v2 —
two-tier): positional window selection alone can pull in beats about an
entirely different character/subplot from the one the title/target_moment
describes. Real case: a Red Hulk/Ghost Rider moment's window included Flash
Thompson/Alejandra/X-23 beats AND Blackheart-only beats with zero Red Hulk —
~50% of the Short drifted off-topic. v1 filtered on title+target_moment
tokens together; that failed live because target_moment usually NAMES the
villain, and an OMNIPRESENT villain (in nearly every beat's characters_active)
made the filter a no-op. v2 filters on TITLE WORDS ONLY first (tier 1, floor
of 4 beats — immune to a villain the title itself doesn't mention), only
falling back to the old title+target_moment vocabulary (tier 2, floor of 3)
when tier 1 can't clear 4 beats even after widening, and finally shipping the
raw unfiltered window (tier 3) if tier 2 still can't clear 3. These tests
replicate the team-book shape and the omnipresent-villain shape with synthetic
beats (no projects/ fixtures)."""
import stages.stage_3.micro_moment as mm
from stages.stage_3.schema import Beat


def _beat(bid, function, name, summary, chars):
    return Beat(id=bid, function=function, name=name, summary=summary,
                characters_active=list(chars))


_TITLE = "Red Hulk Becomes the Spirit of Vengeance"
_TARGET = ("Red Hulk becomes the new Ghost Rider after Blackheart brands him "
           "with the Spirit of Vengeance curse")


# 6-beat replica of the real red-hulk-ghost-rider window (ids 9-14): beats 10
# and 13 belong to a parallel Flash Thompson/X-23/Alejandra subplot with zero
# overlap with the title/target's Red Hulk/Ghost Rider/Blackheart focus.
def _team_book_beats():
    return [
        _beat(9, "SETUP", "Red Hulk arrives",
              "Red Hulk confronts Blackheart in the ruined city.",
              ["Red Hulk", "Blackheart"]),
        _beat(10, "COMPLICATION", "Side mission",
              "Flash Thompson and Alejandra search for a way to escape.",
              ["Flash Thompson", "Alejandra"]),
        _beat(11, "ESCALATION", "Rising threat",
              "Red Hulk is overwhelmed as Blackheart's hellfire spreads.",
              ["Red Hulk", "Blackheart"]),
        _beat(12, "CLIMAX", "Ghost Rider curse",
              "Blackheart brands Red Hulk with the Spirit of Vengeance, "
              "turning him into the new Ghost Rider.",
              ["Red Hulk", "Blackheart"]),
        _beat(13, "MIDPOINT", "Meanwhile",
              "Flash Thompson and X-23 and Alejandra regroup elsewhere.",
              ["Flash Thompson", "X-23", "Alejandra"]),
        _beat(14, "LANDING", "Aftermath",
              "Red Hulk now wields the Ghost Rider curse as Blackheart flees.",
              ["Red Hulk", "Blackheart"]),
    ]


def test_focus_filter_drops_offtopic_teambook_beats():
    window = mm._select_moment_window(_team_book_beats(), _TARGET, title=_TITLE)
    assert [b.id for b in window] == [9, 11, 12, 14]


def test_focus_filter_logs_dropped_ids():
    logged = []
    mm._select_moment_window(_team_book_beats(), _TARGET, title=_TITLE, log=logged.append)
    assert any("dropped beat(s) [10, 13]" in m for m in logged), logged


def test_focus_filter_noop_when_all_beats_on_focus():
    """Solo book: every beat's characters_active already carries the title's
    character — filtering must drop nothing, window stays exactly as the
    positional slice produced it."""
    beats = [
        _beat(1, "SETUP", "a", "Batman tracks a lead across Gotham.", ["Batman"]),
        _beat(2, "COMPLICATION", "b", "Batman confronts the suspect.", ["Batman"]),
        _beat(3, "CLIMAX", "c", "Batman finally corners the culprit.", ["Batman"]),
        _beat(4, "LANDING", "d", "Batman walks away into the night.", ["Batman"]),
    ]
    window = mm._select_moment_window(beats, "Batman corners the culprit", title="Batman")
    assert [b.id for b in window] == [1, 2, 3, 4]


def test_focus_filter_keeps_beats_with_empty_characters_active():
    """Unknown cast (empty characters_active) must NOT be treated as off-focus —
    that would strip legitimate bridge/LANDING beats that carry no character tag."""
    beats = [
        _beat(1, "SETUP", "a", "Batman tracks a lead across Gotham.", ["Batman"]),
        _beat(2, "COMPLICATION", "b", "A quiet transition beat.", []),
        _beat(3, "CLIMAX", "c", "Batman finally corners the culprit.", ["Batman"]),
        _beat(4, "LANDING", "d", "Batman walks away into the night.", ["Batman"]),
    ]
    window = mm._select_moment_window(beats, "Batman corners the culprit", title="Batman")
    assert [b.id for b in window] == [1, 2, 3, 4]


def test_focus_filter_always_keeps_the_peak_even_if_offtopic_chars():
    """The peak beat itself is never dropped, even if its characters_active
    happens to miss the focus vocabulary — it's the described moment itself.
    4 other on-focus beats already clear the >=3 floor on their own, so
    (without rule a) the peak WOULD be dropped; this proves rule (a) is what
    keeps it, not the <3 fallback."""
    beats = [
        _beat(1, "SETUP", "a", "Batman tracks a lead across Gotham.", ["Batman"]),
        _beat(2, "COMPLICATION", "b", "Batman closes in on the culprit.", ["Batman"]),
        _beat(3, "CLIMAX", "Batman corners the culprit",
              "Batman corners the culprit at last.", ["Some Rando"]),
        _beat(4, "ESCALATION", "d", "Batman handcuffs the culprit.", ["Batman"]),
        _beat(5, "LANDING", "e", "Batman walks away into the night.", ["Batman"]),
    ]
    window = mm._select_moment_window(beats, "Batman corners the culprit", title="Batman")
    assert [b.id for b in window] == [1, 2, 3, 4, 5]


def test_focus_filter_falls_back_to_unfiltered_window_when_still_under_3():
    """10 beats, all off-focus ("Random Guy") except the peak (Batman) — even
    after widening lead<=5/follow<=4 the filtered set never reaches 3 (only the
    peak ever passes), so the function must ship the ORIGINAL, UNFILTERED
    positional window rather than a too-short/broken one."""
    beats = [_beat(i, "SETUP", f"beat{i}", f"Random Guy does something on day {i}.",
                   ["Random Guy"]) for i in range(1, 11)]
    # peak at id 5 (0-indexed position 4): distinctive text matching title/target
    beats[4] = _beat(5, "CLIMAX", "Batman corners the culprit",
                     "Batman corners the culprit at last.", ["Batman"])

    window = mm._select_moment_window(beats, "Batman corners the culprit", title="Batman")
    # peak index=4, lead=min(4,3)=3 -> start=1, follow=min(10-4-1,2)=2 -> end=7
    assert [b.id for b in window] == [2, 3, 4, 5, 6, 7]


def test_focus_filter_fallback_logs_warning():
    logged = []
    beats = [_beat(i, "SETUP", f"beat{i}", f"Random Guy does something on day {i}.",
                   ["Random Guy"]) for i in range(1, 11)]
    beats[4] = _beat(5, "CLIMAX", "Batman corners the culprit",
                     "Batman corners the culprit at last.", ["Batman"])
    mm._select_moment_window(beats, "Batman corners the culprit", title="Batman",
                             log=logged.append)
    assert any("still has only" in m for m in logged), logged


# --- villain-omnipresent (the live red-hulk-ghost-rider bug this iteration fixes) ---
# Blackheart sits in EVERY beat's characters_active below (exactly the live shape:
# a team-book villain who is on-page constantly), which made the v1 single-tier
# title+target_moment filter a no-op (every beat shares the villain token, so
# nothing gets dropped). Tier 1 (title words ONLY) doesn't carry the villain's
# name at all, so it isn't fooled by the villain's omnipresence.
def _villain_omnipresent_beats(extra_hero_beat: bool):
    """8 (or 10) beats: Blackheart in every one; Red Hulk in only 3 of them
    (ids 3, 5, 7) plus, when `extra_hero_beat`, a 4th (id 8) that only enters
    the window after tier 1's own widen step — enough to clear tier 1's floor
    of 4 on title words alone. Without it, Red Hulk never reaches 4 anywhere
    in the beat list, so tier 1 must give up and tier 2 takes over."""
    beats = [
        _beat(1, "SETUP", "Scheme", "Blackheart schemes in the shadows.", ["Blackheart"]),
        _beat(2, "SETUP", "Hellfire", "Blackheart's hellfire spreads across the city.", ["Blackheart"]),
        _beat(3, "COMPLICATION", "First clash", "Red Hulk confronts Blackheart at the gates.",
              ["Red Hulk", "Blackheart"]),
        _beat(4, "SIDEBAR", "Cosmic drift", "Silver Surfer drifts through a distant nebula.",
              ["Silver Surfer"]) if not extra_hero_beat else
        _beat(4, "ESCALATION", "Taunt", "Blackheart taunts his enemies from the throne.", ["Blackheart"]),
        _beat(5, "CLIMAX", "Ghost Rider curse",
              "Red Hulk becomes the Spirit of Vengeance as Blackheart brands him.",
              ["Red Hulk", "Blackheart"]),
        _beat(6, "ESCALATION", "Laughing villain", "Blackheart laughs as flames rise.", ["Blackheart"]),
        _beat(7, "ESCALATION", "Curse wielded", "Red Hulk wields the curse against Blackheart.",
              ["Red Hulk", "Blackheart"]),
    ]
    if extra_hero_beat:
        beats.append(_beat(8, "LANDING", "Aftermath", "Red Hulk stands with Blackheart nearby.",
                            ["Red Hulk", "Blackheart"]))
    else:
        beats += [
            _beat(8, "MIDPOINT", "Retreat", "Blackheart retreats into the void.", ["Blackheart"]),
            _beat(9, "MIDPOINT", "Plotting", "Blackheart plots his next move.", ["Blackheart"]),
            _beat(10, "LANDING", "Vanish", "Blackheart vanishes into the dark.", ["Blackheart"]),
        ]
    return beats


def test_villain_omnipresent_tier1_alone_drops_villain_only_beats():
    """Red Hulk reaches 4 beats once tier 1 widens one step (picks up id 8) — tier 1
    succeeds on TITLE WORDS ALONE despite Blackheart sitting in every single beat,
    dropping the 3 Blackheart-only beats (2, 4, 6) from the window."""
    logged = []
    window = mm._select_moment_window(_villain_omnipresent_beats(extra_hero_beat=True),
                                      _TARGET, title=_TITLE, log=logged.append)
    assert [b.id for b in window] == [3, 5, 7, 8]
    assert any("focus-filter(title) dropped beat(s) [2, 4, 6]" in m for m in logged), logged


def test_villain_omnipresent_falls_back_to_tier2_when_hero_only_in_3_beats():
    """Red Hulk never reaches 4 beats anywhere in this list (only ids 3, 5, 7) — tier 1
    gives up after widening, and tier 2 (title+target_moment vocabulary, which DOES
    carry "Blackheart" because target_moment names him) takes over at its floor of 3.
    Tier 2 is not immune to the omnipresent villain (it keeps the Blackheart-only
    beats 2 and 6), but it still correctly drops the genuinely unrelated Silver
    Surfer beat (4), proving the fallback isn't a no-op either."""
    logged = []
    window = mm._select_moment_window(_villain_omnipresent_beats(extra_hero_beat=False),
                                      _TARGET, title=_TITLE, log=logged.append)
    assert [b.id for b in window] == [2, 3, 5, 6, 7]
    assert any("focus-filter(title+moment) dropped beat(s) [4]" in m for m in logged), logged


# --- duplicate beat id (2026-07-16 regression: bridge-retry outline let two
# DIFFERENT beats share one numeric id) ---
def test_duplicate_id_does_not_fake_a_tier1_pass():
    """A bridge-retry outline bug let a beat's id collide with another beat's id
    (real case: red-hulk-ghost-rider's bridge response tagged 3 distinct new
    beats id=12). If tier 1 counted RAW list length, a duplicate could pad the
    count to the floor of 4 without adding real on-focus coverage, and the
    caller (`_anchor_scenes_to_beats`, keyed back then by `.id`) would silently
    collapse two beats onto one scene — the "narration only 67 words" bug.
    Same corpus as test_villain_omnipresent_falls_back_to_tier2_when_hero_only_in_3_beats
    (Red Hulk on-focus in only 3 physical beats: ids 3, 5, 7 — tier 1 must give
    up), but the "Ghost Rider curse" beat is now duplicated (both id=5): tier 1's
    raw filtered length reads 4, yet only 3 ids are DISTINCT (3, 5, 7), so tier 1
    must still give up and tier 2 (title+target vocabulary) must take over —
    exactly like the un-duplicated case, proven by tier 2's own log line firing
    (tier 1's "dropped beat(s)" success line must NOT appear)."""
    beats = _villain_omnipresent_beats(extra_hero_beat=False)
    dup = _beat(5, "CLIMAX", "Ghost Rider curse (dup id)",
                "Red Hulk becomes the Spirit of Vengeance as Blackheart brands him.",
                ["Red Hulk", "Blackheart"])
    beats.insert(5, dup)  # id=5 now appears twice — the bridge-outline defect
    logged = []
    window = mm._select_moment_window(beats, _TARGET, title=_TITLE, log=logged.append)
    assert not any("focus-filter(title) dropped" in m for m in logged), (
        f"tier 1 must NOT accept a duplicate-padded count as a real 4-beat pass: {logged}")
    assert any("focus-filter(title+moment) dropped" in m for m in logged), (
        f"expected the tier-2 fallback to fire instead: {logged}")
    assert window, "must still ship a non-empty window"


# ── LLM-segment context-aware path (_segment_moment_window, 2026-07-20) ──────────
# Primary path: reads the WHOLE outline and groups beats into focus/context/payoff/drop
# so the far SETUP a positional window misses is kept (the immortal-hulk bug: setup pages
# before the moment were dropped, so narration jumped into the payoff and confused viewers).
import json


def _seg_call(segment_obj):
    """A call_with_chain stand-in that returns a canned segment JSON. The real segmenter
    passes NO validator (does its own id validation), matching this signature."""
    def _fn(*, system, user, models=None, max_tokens=700, progress=None,
            label="llm", validator=None):
        return json.dumps(segment_obj), "fake-seg-model"
    return _fn


# 6 beats: 1,2 = setup(context), 3 = an unrelated subplot, 4 = focus, 5 = payoff,
# 6 = a second unrelated subplot beat.
def _seg_beats():
    return [
        _beat(1, "SETUP", "Robbery", "Bruce Banner robs a gas station at gunpoint.", ["Bruce Banner"]),
        _beat(2, "SETUP", "Shot dead", "A robber shoots Bruce Banner in the head.", ["Bruce Banner"]),
        _beat(3, "COMPLICATION", "Elsewhere", "Two detectives argue about an unrelated case.",
              ["Detective A", "Detective B"]),
        _beat(4, "CLIMAX", "Hulk wakes", "The Hulk rises from the slab in the morgue.", ["Hulk"]),
        _beat(5, "LANDING", "Aftermath", "The Hulk walks out into the night.", ["Hulk"]),
        _beat(6, "COMPLICATION", "Subplot coda", "The detectives file their unrelated report.",
              ["Detective A", "Detective B"]),
    ]


def test_segment_keeps_context_and_drops_subplot(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)
    monkeypatch.setattr(mm, "call_with_chain",
                        _seg_call({"focus": [4], "context": [1, 2], "payoff": [5], "drop": [3, 6]}))
    window = mm._segment_moment_window(_seg_beats(), "The Hulk rises in the morgue",
                                       title="Immortal Hulk", model="x")
    # context (1,2) KEPT, subplot (3,6) DROPPED, focus+payoff kept, ORIGINAL causal order
    assert [b.id for b in window] == [1, 2, 4, 5]


# Real bug (2026-07-20, Immortal Hulk): the segmenter kept a beat where a reporter and a
# detective piece together clues to conclude the gamma mutation — two SIDE characters
# recapping what the focus already showed, no new stakes for the main thread. The prompt now
# tells the model to DROP that shape even though it is "related"; this test locks the
# mechanism (essential setup still kept, side-character recap still droppable) against that beat.
def _seg_beats_with_recap():
    beats = _seg_beats()
    beats.append(_beat(7, "COMPLICATION", "Reporter connects the dots",
                        "A reporter and a detective piece together clues and conclude the "
                        "gamma mutation reverted, restating what the focus already showed.",
                        ["Reporter", "Detective"]))
    return beats


def test_segment_essential_setup_kept_recap_beat_dropped(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)
    monkeypatch.setattr(
        mm, "call_with_chain",
        _seg_call({"focus": [4], "context": [1, 2], "payoff": [5], "drop": [3, 6, 7]}))
    window = mm._segment_moment_window(_seg_beats_with_recap(), "The Hulk rises in the morgue",
                                       title="Immortal Hulk", model="x")
    # essential setup (1,2 — robbery + shooting of the MAIN character) still KEPT;
    # side-character recap beat (7) DROPPED same as the unrelated subplot (3,6).
    assert [b.id for b in window] == [1, 2, 4, 5]


def test_segment_prompt_requires_essential_context_and_drops_side_recap():
    # Locks the tightened wording in place so this doesn't drift back to the old
    # "err on keeping context" / "most beats are kept" looseness that let a
    # side-character recap beat slip into payoff.
    prompt = mm._FOCUS_SEGMENT_SYSTEM
    assert "ESSENTIAL" in prompt
    assert "side" in prompt.lower() and "recap" in prompt.lower()
    assert "err on keeping context" not in prompt.lower()


def test_segment_focus_never_dropped_even_if_model_contradicts(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)
    # model absurdly lists the focus beat in BOTH focus and drop — focus must win.
    monkeypatch.setattr(mm, "call_with_chain",
                        _seg_call({"focus": [4], "context": [1, 2], "payoff": [5], "drop": [3, 4]}))
    window = mm._segment_moment_window(_seg_beats(), "morgue", title="Hulk", model="x")
    assert 4 in [b.id for b in window]


def test_segment_falls_back_to_none_on_llm_failure(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)

    def _boom(*, system, user, models=None, max_tokens=700, progress=None,
              label="llm", validator=None):
        raise RuntimeError("SDK unavailable in test")

    monkeypatch.setattr(mm, "call_with_chain", _boom)
    logged = []
    assert mm._segment_moment_window(_seg_beats(), "morgue", title="Hulk",
                                     model="x", log=logged.append) is None
    assert any("heuristic fallback" in m for m in logged), logged


def test_segment_falls_back_when_no_valid_focus(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)
    # focus ids are all OUT of range (99) → no valid focus → fall back.
    monkeypatch.setattr(mm, "call_with_chain", _seg_call({"focus": [99], "context": [1]}))
    assert mm._segment_moment_window(_seg_beats(), "morgue", title="Hulk", model="x") is None


def test_segment_knob_off_skips_llm(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "0")

    def _must_not_call(*a, **k):
        raise AssertionError("FOCUS_FILTER_LLM=0 must not call the LLM")

    monkeypatch.setattr(mm, "call_with_chain", _must_not_call)
    assert mm._segment_moment_window(_seg_beats(), "morgue", title="Hulk", model="x") is None


def test_segment_skipped_under_no_embed(monkeypatch):
    monkeypatch.setenv("FOCUS_FILTER_LLM", "1")
    monkeypatch.setenv("STAGE3_NO_EMBED", "1")

    def _must_not_call(*a, **k):
        raise AssertionError("--no-embed must not call the LLM segmenter")

    monkeypatch.setattr(mm, "call_with_chain", _must_not_call)
    assert mm._segment_moment_window(_seg_beats(), "morgue", title="Hulk", model="x") is None


# ── SETUP-REACH fallback loosening (heuristic path, offline) ──────────────────────
def _hulk_setup_beats(subplot_before_window: bool):
    """8 Hulk beats with the moment (id 6) deep enough that the positional lead cap
    (<=3) leaves the early setup (ids 1,2 = the robbery + shooting) OUT of the window.
    When `subplot_before_window`, id 3 becomes an unrelated Wasp beat that sits at the
    window's front edge — the backward reach must STOP there instead of crossing it."""
    chars3 = ["Wasp"] if subplot_before_window else ["Hulk"]
    return [
        _beat(1, "SETUP", "Robbery", "Bruce robs a gas station.", ["Hulk"]),
        _beat(2, "SETUP", "Shot", "Bruce is shot dead.", ["Hulk"]),
        _beat(3, "SETUP", "Bridge", "A quiet transition at the scene.", chars3),
        _beat(4, "COMPLICATION", "Morgue night", "Night falls over the morgue.", ["Hulk"]),
        _beat(5, "COMPLICATION", "Morgue quiet", "The morgue sits silent and cold.", ["Hulk"]),
        _beat(6, "CLIMAX", "Hulk rises in the morgue",
              "The Hulk rises in the morgue and tears free.", ["Hulk"]),
        _beat(7, "LANDING", "Walks out", "The Hulk walks out into the night.", ["Hulk"]),
        _beat(8, "LANDING", "Vanishes", "The Hulk vanishes into the dark.", ["Hulk"]),
    ]


def test_setup_reach_prepends_far_setup_beyond_lead_cap():
    """Offline heuristic: the reach pulls the robbery+shooting (ids 1,2) that the
    positional lead cap dropped, so the fallback still tells who/why — no crash."""
    logged = []
    window = mm._select_moment_window(_hulk_setup_beats(subplot_before_window=False),
                                      "The Hulk rises in the morgue", title="Hulk",
                                      log=logged.append)
    assert [b.id for b in window] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert any("setup-reach prepended beat(s) [1, 2]" in m for m in logged), logged


def test_setup_reach_stops_at_subplot_boundary():
    """The backward reach halts at the first off-subject beat (id 3 = a Wasp beat),
    never crossing a subplot to grab setup behind it — conservative by design."""
    logged = []
    window = mm._select_moment_window(_hulk_setup_beats(subplot_before_window=True),
                                      "The Hulk rises in the morgue", title="Hulk",
                                      log=logged.append)
    # id 3 (Wasp) is filtered out of the positional window; reach stops there → no prepend
    assert [b.id for b in window] == [4, 5, 6, 7, 8]
    assert not any("setup-reach prepended" in m for m in logged), logged


if __name__ == "__main__":
    test_focus_filter_drops_offtopic_teambook_beats()
    test_focus_filter_logs_dropped_ids()
    test_focus_filter_noop_when_all_beats_on_focus()
    test_focus_filter_keeps_beats_with_empty_characters_active()
    test_focus_filter_always_keeps_the_peak_even_if_offtopic_chars()
    test_focus_filter_falls_back_to_unfiltered_window_when_still_under_3()
    test_focus_filter_fallback_logs_warning()
    test_villain_omnipresent_tier1_alone_drops_villain_only_beats()
    test_villain_omnipresent_falls_back_to_tier2_when_hero_only_in_3_beats()
    test_duplicate_id_does_not_fake_a_tier1_pass()
    print("ok")
