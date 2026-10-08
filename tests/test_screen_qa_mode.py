"""Tests for Task 1: mode 'screen_qa' registration without disturbing existing modes."""
import argparse
import pytest
from config import PipelineMode
from stages.stage_3.modes import MODES, MODES_BY_KEY, describe_catalog


def test_pipeline_mode_screen_qa_registered():
    assert hasattr(PipelineMode, "SCREEN_QA")
    assert PipelineMode.SCREEN_QA.value == "screen_qa"


def test_narration_mode_screen_qa_registered():
    assert "screen_qa" in MODES_BY_KEY
    mode = MODES_BY_KEY["screen_qa"]
    assert mode.key == "screen_qa"
    assert "Screen" in mode.label
    assert "screen_context.json" in mode.description


def test_existing_modes_undisturbed():
    expected_modes = {
        "panel_walk", "lesson", "moral", "feat", "fun_fact",
        "recap_summary", "character_spotlight", "hot_take",
        "twist_reveal", "theme_analysis", "what_if", "tragedy",
        "power_ranking", "explore_answer", "micro_moment", "screen_qa",
    }
    actual_modes = {m.key for m in MODES}
    assert expected_modes.issubset(actual_modes)
    # Ensure recap, explore_answer, and micro_moment are intact
    assert "recap_summary" in MODES_BY_KEY
    assert "explore_answer" in MODES_BY_KEY
    assert "micro_moment" in MODES_BY_KEY


def test_describe_catalog_includes_screen_qa():
    catalog = describe_catalog()
    assert "- screen_qa:" in catalog
    assert "- explore_answer:" in catalog
    assert "- micro_moment:" in catalog


def test_stage_3_cli_accepts_screen_qa():
    # Verify that stages/stage_3/cli.py argument parser accepts screen_qa
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=lambda s: s.strip().replace("-", "_"),
        choices=sorted(MODES_BY_KEY.keys()),
    )
    args = parser.parse_args(["--mode", "screen_qa"])
    assert args.mode == "screen_qa"

    # Also accept hyphenated
    args_hyphen = parser.parse_args(["--mode", "screen-qa"])
    assert args_hyphen.mode == "screen_qa"
