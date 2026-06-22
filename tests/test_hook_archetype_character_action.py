from stages.stage_3.write_script import _classify_hook, _ALLOWED_HOOK_ARCHETYPES


def test_character_action_is_classified():
    # An action-first opener (proper noun + active verb) classifies as character_action.
    assert _classify_hook("Peter woke in a body that was no longer his.") == "character_action"


def test_character_action_is_now_allowed():
    # The growth change: character_action must be in the allow-list so generate_intro accepts it.
    assert "character_action" in _ALLOWED_HOOK_ARCHETYPES


def test_existing_archetypes_still_allowed():
    for a in ("interrogative", "temporal-when", "temporal-other", "scenic"):
        assert a in _ALLOWED_HOOK_ARCHETYPES
