"""Tests for Task 3 — story_map kwarg wired into write_script consumers."""
import inspect
from stages.stage_3 import write_script as ws


def test_consumers_accept_story_map_kwarg():
    for fn in (ws.outline_beats, ws.write_scenes, ws._retry_fix_with_wiki,
               ws._critique_beats_for_impact, ws._logic_clarity_critic):
        assert "story_map" in inspect.signature(fn).parameters, fn.__name__


def test_block_injected_is_noop_when_none():
    from stages.stage_3.story_architect import render_story_map_block
    assert render_story_map_block(None) == ""
