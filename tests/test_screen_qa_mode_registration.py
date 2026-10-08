import pytest

import config
from stages.stage_3.modes import MODES_BY_KEY


def test_screen_qa_mode_registered():
    assert hasattr(config, "MODE_SCREEN_QA")
    assert config.MODE_SCREEN_QA == "screen_qa"
    assert "screen_qa" in MODES_BY_KEY
    mode_obj = MODES_BY_KEY["screen_qa"]
    assert mode_obj.key == "screen_qa"
    assert "Screen" in mode_obj.label
