from stages.stage_3.write_script import _classify_hook, _ALLOWED_HOOK_ARCHETYPES


def test_character_action_is_classified():
    # An action-first opener (proper noun + active verb) classifies as character_action.
    assert _classify_hook("Peter woke in a body that was no longer his.") == "character_action"


def test_character_action_multiword_name_and_any_verb():
    # Regression: the natural action-first openers the LLM actually writes — a
    # MULTI-WORD name + a verb outside the old fixed list ("became"/"vowed") — must
    # classify as character_action (the old narrow regex left these as
    # other_character → intro validator rejected → generic fallback hook).
    assert _classify_hook("Bruce Banner became Galactus's Herald to save worlds, not destroy them.") == "character_action"
    assert _classify_hook("Banner vowed to save every world his master marked for death.") == "character_action"
    assert _classify_hook("Magik stood over the boy she once called her hero.") == "character_action"


def test_classifier_matches_benchmark_builder():
    # The two classifiers MUST stay identical (Stage 3 gate vs benchmark builder).
    import importlib.util
    from pathlib import Path
    bb_path = Path(__file__).resolve().parent.parent / "research" / "scripts" / "benchmark_builder.py"
    spec = importlib.util.spec_from_file_location("benchmark_builder", bb_path)
    bb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bb)
    for line in (
        "Bruce Banner became Galactus's Herald to save worlds, not destroy them.",
        "Peter woke in a body that was no longer his.",
        "When Illyana returned from Limbo, she rejected the X-Men.",
        "What if surviving hell meant becoming the monster you fled?",
        "In a broken reality, Magik turned her back on the X-Men.",
    ):
        assert _classify_hook(line) == bb.classify_hook(" ".join(line.split()[:12])), line


def test_character_action_is_now_allowed():
    # The growth change: character_action must be in the allow-list so generate_intro accepts it.
    assert "character_action" in _ALLOWED_HOOK_ARCHETYPES


def test_existing_archetypes_still_allowed():
    for a in ("interrogative", "temporal-when", "temporal-other", "scenic"):
        assert a in _ALLOWED_HOOK_ARCHETYPES
