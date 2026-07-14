"""Guards for the 6 research-backed retention rules added to Stage 3 RECAP narration
(Jenny Hoyos / Creator Science / George Blackman / MrBallen). All the new validator
lints are SOFT — they must feed the retry loop a directive but never be critical, or a
single stylistic miss would crash the pipeline / lose best-draft to a clean-but-worse take.

Also locks the hook-band de-magic-numbering: the TEASER intro band is now its own named
constant, distinct (on purpose) from the body cold-open hook band."""
from stages.stage_3.write_script import (
    _TARGET_WORDS_MIN, _TARGET_WORDS_MAX,
    _HOOK_MIN_WORDS, _HOOK_MAX_WORDS, _INTRO_MIN_WORDS, _INTRO_MAX_WORDS,
    _YOU_QUOTA, _BEAT_COMMENT_CAP, _classify_hook, _ALLOWED_HOOK_ARCHETYPES,
    _is_critical_error, _validate,
    _lint_you_quota, _lint_weak_sequence_openers, _lint_beat_comments, _lint_loop_mirror,
)


def _sc(text: str) -> dict:
    return {"text": text}


# ── hook band: single source of truth, teaser distinct from body hook ──────────────
def test_intro_band_is_named_and_short_enough_to_protect_the_length_band():
    # Teaser (prepended ON TOP of the body budget) must stay short — its ceiling is well
    # under the body cold-open hook's 26 so a teaser + a full body cannot blow ~60s.
    assert (_INTRO_MIN_WORDS, _INTRO_MAX_WORDS) == (10, 20)
    assert (_HOOK_MIN_WORDS, _HOOK_MAX_WORDS) == (14, 26)
    assert _INTRO_MAX_WORDS < _HOOK_MAX_WORDS
    lo_sec = (_TARGET_WORDS_MIN + _INTRO_MIN_WORDS) / 3.4
    hi_sec = (_TARGET_WORDS_MAX + _INTRO_MAX_WORDS) / 3.4
    assert 40 <= lo_sec and hi_sec <= 60, (lo_sec, hi_sec)


def test_foreshadow_promise_two_sentence_teaser_still_classifies_and_fits_band():
    # Rule 1: hook sentence + a short promise sentence, all in one teaser line. The
    # promise must NOT change the archetype (classify reads the first 12 words) and the
    # whole thing must fit the teaser band.
    teaser = "Nightwing thought he had the perfect life. The choice costs him everything."
    assert _classify_hook(teaser) == "character_action"
    assert _classify_hook(teaser) in _ALLOWED_HOOK_ARCHETYPES
    assert _INTRO_MIN_WORDS <= len(teaser.split()) <= _INTRO_MAX_WORDS


# ── rule 2: flat-sequence transition overuse ───────────────────────────────────────
def test_rule2_flags_flat_sequence_lean():
    body = ([_sc("Nightwing thought life was perfect until it broke apart.")]
            + [_sc("Then he ran fast."), _sc("Then she fell hard."),
               _sc("Next he called out."), _sc("Later they regrouped calmly."),
               _sc("And then he wept.")]
            + [_sc("So he understood the whole cost at last.")]
            + [_sc("The comic is Test Comic.")])
    issues = _lint_weak_sequence_openers(body)
    assert issues and "rule2 connective" in issues[0]
    assert not _is_critical_error(issues[0])


def test_rule2_ok_when_transitions_mean_something():
    body = ([_sc("Nightwing thought life was perfect until it broke apart.")]
            + [_sc("But the truth was far worse than that."),
               _sc("So he hunted the man responsible."),
               _sc("He cornered him in the dark."),
               _sc("Then the mask finally came off.")]
            + [_sc("He walked away broken by what he found.")]
            + [_sc("The comic is Test Comic.")])
    assert _lint_weak_sequence_openers(body) == []


# ── rule 4: "you" quota ─────────────────────────────────────────────────────────────
def test_rule4_flags_you_in_body():
    body = ([_sc("Nightwing thought life was perfect until it broke apart.")]
            + [_sc("He fought hard tonight."), _sc("You would never guess why."),
               _sc("He lost the fight.")]
            + [_sc("He walked away, forever changed by the loss.")]
            + [_sc("The comic is Test Comic.")])
    issues = _lint_you_quota(body)
    assert issues and "rule4 you-quota" in issues[0]
    assert not _is_critical_error(issues[0])


def test_rule4_allows_you_in_hook_and_final_only():
    body = ([_sc("Ever wonder what you would do in his place tonight?")]
            + [_sc("He fought hard here."), _sc("He lost the fight."),
               _sc("He ran for his life.")]
            + [_sc("It could just as easily have been you.")]
            + [_sc("The comic is Test Comic.")])
    assert _lint_you_quota(body) == []          # 1 in hook + 1 in final = quota met
    assert _YOU_QUOTA == 2


# ── rule 5: beat-comment cap ────────────────────────────────────────────────────────
def test_rule5_caps_beat_comments():
    body = ([_sc("Nightwing thought life was perfect until it broke apart.")]
            + [_sc("He tried to stop it. It doesnt."),
               _sc("She swore it would hold. It never does."),
               _sc("He begged for mercy. It wont.")]
            + [_sc("He walked away broken by the loss.")]
            + [_sc("The comic is Test Comic.")])
    issues = _lint_beat_comments(body)
    assert issues and "rule5 beat-comment" in issues[0]
    assert not _is_critical_error(issues[0])


def test_rule5_does_not_flag_plot_punches():
    # Short plot punches that open with a name / "The …" / a concrete event verb are NOT
    # narrator asides — they must never count toward the cap.
    body = ([_sc("Nightwing thought life was perfect until it broke apart.")]
            + [_sc("The Penance Stare had no effect."),
               _sc("It exploded loudly."),
               _sc("Reed fired the sonic gun."),
               _sc("He crushed the gun and left.")]
            + [_sc("He walked away broken by the loss.")]
            + [_sc("The comic is Test Comic.")])
    assert _lint_beat_comments(body) == []


# ── rule 6: loop-mirror landing ─────────────────────────────────────────────────────
def test_rule6_flags_landing_that_ignores_the_hook():
    body = ([_sc("Nightwing thought the perfect life was finally his.")]
            + [_sc("He fought hard here."), _sc("He lost the fight."),
               _sc("A stranger watched from far.")]
            + [_sc("The shadow simply vanished into nothing.")]
            + [_sc("The comic is Test Comic.")])
    issues = _lint_loop_mirror(body)
    assert issues and "rule6 loop-mirror" in issues[0]
    assert not _is_critical_error(issues[0])


def test_rule6_passes_when_landing_echoes_a_hook_word():
    body = ([_sc("Nightwing thought the perfect life was finally his.")]
            + [_sc("He fought hard here."), _sc("He lost the fight."),
               _sc("A stranger watched from far.")]
            + [_sc("The perfect life had been the trap all along.")]
            + [_sc("The comic is Test Comic.")])
    assert _lint_loop_mirror(body) == []


# ── integration: the lints ride _validate as soft (non-critical) errors ─────────────
def test_retention_lints_are_soft_in_validate():
    scenes = ([{"text": "You thought you knew this hero completely and totally today.",
                "connective": None, "page_ref": 1, "beat_id": 1}]
              + [{"text": "Then he ran.", "connective": "Then", "page_ref": 1, "beat_id": b}
                 for b in range(2, 8)]
              + [{"text": "And so you finally saw the whole truth of it.",
                  "connective": None, "page_ref": 1, "beat_id": 8}]
              + [{"text": "The comic is Test Comic.", "connective": None,
                  "page_ref": 1, "beat_id": 9}])
    parsed = {"scenes": scenes, "_coverage_gaps": [], "_anchor_pool_count": 9}
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 10)))
    retention = [e for e in errors if e.startswith(("rule2", "rule4", "rule5", "rule6"))]
    assert retention, f"expected retention lints to fire, got: {errors}"
    assert not any(_is_critical_error(e) for e in retention), retention
