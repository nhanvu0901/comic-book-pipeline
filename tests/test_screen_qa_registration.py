"""screen_qa must be registered WITHOUT touching the stable comic path.

Two regressions the first registration attempt introduced, both reachable from a plain comic run:
  * adding a NarrationMode to stages/stage_3/modes.MODES changes describe_catalog(), i.e. the
    Stage-3 "propose modes" system prompt for EVERY comic (and lets the LLM propose screen_qa);
  * adding a PipelineMode member made ui/screens/s1_identify raise KeyError(MODE_LABELS[mode])
    while building the Stage-1 mode dropdown, because that screen iterates the whole enum.
"""
from config import PipelineMode
from stages.stage_3.modes import MODES, MODES_BY_KEY, describe_catalog


def test_screen_qa_is_not_in_the_comic_mode_catalog():
    assert "screen_qa" not in MODES_BY_KEY
    assert all(m.key != "screen_qa" for m in MODES)
    assert "screen_qa" not in describe_catalog()


def test_comic_propose_prompt_catalog_is_unchanged():
    # The catalog is baked into propose_modes._CATALOG at import; it must list only comic angles.
    from stages.stage_3 import propose_modes
    assert "screen_qa" not in propose_modes._CATALOG
    assert "- explore_answer:" in propose_modes._CATALOG
    assert "- micro_moment:" in propose_modes._CATALOG


def test_every_pipeline_mode_has_a_stage1_dropdown_label():
    from ui.screens.s1_identify import MODE_LABELS
    missing = [m for m in PipelineMode if m not in MODE_LABELS]
    assert not missing, f"s1_identify would KeyError on: {missing}"


def test_screen_qa_mode_key_lives_in_the_new_module():
    from stages.stage_3.screen_qa import SCREEN_QA_MODE
    assert SCREEN_QA_MODE == "screen_qa"
    assert SCREEN_QA_MODE not in {m.value for m in PipelineMode}
