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


def test_length_band_targets_45_57s_at_measured_wps():
    # Retuned from the empirical ~3.4 wps (Resemble Carl + shipped --atempo 1.35),
    # NOT the stale 2.88. Body band + a ~14-word teaser intro must land the FINAL
    # audio inside the ~45-57s attention budget (B4 retune, 2026-07-12).
    assert (_TARGET_WORDS_MIN, _TARGET_WORDS_MAX) == (140, 180)
    assert _WORDS_PER_SEC == 3.4
    intro_words = 14  # typical teaser prepended on top of the body
    lo_sec = (_TARGET_WORDS_MIN + intro_words) / _WORDS_PER_SEC
    hi_sec = (_TARGET_WORDS_MAX + intro_words) / _WORDS_PER_SEC
    assert 43 <= lo_sec <= 48, lo_sec   # ~45s floor
    assert 54 <= hi_sec <= 60, hi_sec   # ~57s ceiling
