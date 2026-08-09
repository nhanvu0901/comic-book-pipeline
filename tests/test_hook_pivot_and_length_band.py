"""Guards for the 2026-07-03 evidence-based Stage 3 retune (competitor-Short mining):
  1. the "thought/believed X ... until [dark turn]" contrast pivot — the shape the
     3 biggest hits all used — must classify into an ALLOWED hook archetype so
     generate_intro accepts it (no code gate change needed, but lock it down).
  2. the length band + measured wps must land the FINISHED audio in the 60-75s
     viral cluster (was mis-calibrated to the unused 1.1-atempo 2.88 wps).
"""
from stages.stage_3.write_script import (
    _classify_hook, _ALLOWED_HOOK_ARCHETYPES,
    _TARGET_WORDS_MIN, _TARGET_WORDS_MAX, _WORDS_PER_SEC,
    _RECAP_WORDS_PER_SEC, _wps_for,
)


def test_thought_until_pivot_lands_in_allowed_archetype():
    # Name-first pivot → character_action; "When ..." pivot → temporal-when. Both are
    # already in the allow-list, so the intro validator accepts the new preferred shape
    # WITHOUT desyncing _classify_hook from research/scripts/benchmark_builder.
    name_first = "Nightwing thought he had the perfect life, until it all fell apart."
    when_first = "When Superman came home, Lois thought he'd been gone hours, until she learned the truth."
    assert _classify_hook(name_first) == "character_action"
    assert _classify_hook(when_first) == "temporal-when"
    assert _classify_hook(name_first) in _ALLOWED_HOOK_ARCHETYPES
    assert _classify_hook(when_first) in _ALLOWED_HOOK_ARCHETYPES


def test_length_band_targets_the_competitor_53_61s_video():
    # VARIANT-PROFILE REGISTER. The six biggest hits of the format run 53-61s of audio.
    # The band tracks their DURATION, never their word count: the words are whatever our
    # render pace needs to fill those seconds. So when atempo moved 1.35 -> 1.10 (Master
    # 2026-08-01) the band moved with it: 1.35 -> 1.10 shrank it to 168-182, then Shorts went
    # to 1.30 and it came back to 199-214. Seconds are the target; words follow the tempo.
    assert (_TARGET_WORDS_MIN, _TARGET_WORDS_MAX) == (199, 214)
    lo_total = _TARGET_WORDS_MIN + 4    # shortest teaser: "Who is Deadpool 2099?"
    hi_total = _TARGET_WORDS_MAX + 9    # longest allowed teaser
    assert 53 <= lo_total / _RECAP_WORDS_PER_SEC <= 56, lo_total / _RECAP_WORDS_PER_SEC
    assert 58 <= hi_total / _RECAP_WORDS_PER_SEC <= 64, hi_total / _RECAP_WORDS_PER_SEC


def test_recap_rate_is_separate_so_qa_keeps_the_shared_estimate():
    """Retuning recap must not move Q&A: explore_answer imports _WORDS_PER_SEC to size its
    own band. Both rates track config.POST_ATEMPO — 3.40 / 3.83 at atempo 1.30 — but they
    stay SEPARATE numbers, because Q&A and recap measure different paces at the same tempo."""
    assert _WORDS_PER_SEC == 3.40
    assert _RECAP_WORDS_PER_SEC != _WORDS_PER_SEC
    assert _wps_for("explore_answer") == _WORDS_PER_SEC
    assert _wps_for("micro_moment") == _WORDS_PER_SEC
    assert _wps_for("recap_summary") == _RECAP_WORDS_PER_SEC
    assert _wps_for("panel_walk") == _RECAP_WORDS_PER_SEC
