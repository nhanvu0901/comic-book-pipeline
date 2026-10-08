import pytest
from stages.stage_4.beat_timing import (
    calculate_beat_durations,
    set_keep_awake,
    BeatWindow,
)


def test_set_keep_awake_runs_without_error():
    # Should safely succeed on any OS
    set_keep_awake(True)
    set_keep_awake(False)


def test_calculate_beat_durations_proportional_to_words():
    scenes = [
        {
            "scene_id": 1,
            "text": "First part of the story. Second part of the story.",
            "visual_beats": [
                {"beat_id": "1:1", "text": "First part of the story."},
                {"beat_id": "1:2", "text": "Second part of the story."},
            ],
        }
    ]
    # Sentence duration = 4.0s for 10 words (5 words each beat)
    sentence_timings = {1: {"duration": 4.0, "start": 0.0, "end": 4.0}}

    windows = calculate_beat_durations(scenes, sentence_timings)
    assert len(windows) == 2
    assert pytest.approx(windows[0].duration, rel=1e-2) == 2.0
    assert pytest.approx(windows[1].duration, rel=1e-2) == 2.0
    assert pytest.approx(windows[0].start, rel=1e-2) == 0.0
    assert pytest.approx(windows[1].start, rel=1e-2) == 2.0


def test_whip_deduction_and_minimum_duration():
    scenes = [
        {
            "scene_id": 1,
            "text": "Scene one beat text here.",
            "visual_beats": [{"beat_id": "1:1", "text": "Scene one beat text here."}],
        },
        {
            "scene_id": 2,
            "text": "Scene two beat text here.",
            "visual_beats": [{"beat_id": "2:1", "text": "Scene two beat text here."}],
        },
    ]
    sentence_timings = {
        1: {"duration": 2.0, "start": 0.0, "end": 2.0},
        2: {"duration": 2.0, "start": 2.0, "end": 4.0},
    }
    # Whip at boundary between scene 1 and 2: whip_secs = 0.24 (deduct 0.12 each side)
    whips = {0: 0.24}
    windows = calculate_beat_durations(scenes, sentence_timings, whips=whips)
    assert len(windows) == 2
    assert pytest.approx(windows[0].duration, rel=1e-2) == 1.88
    assert pytest.approx(windows[1].duration, rel=1e-2) == 1.88
    # Duration floor never falls below 0.4s
    assert all(w.duration >= 0.4 for w in windows)


def test_short_beat_never_below_min_floor():
    scenes = [
        {
            "scene_id": 1,
            "text": "Short. Rest of the longer sentence.",
            "visual_beats": [
                {"beat_id": "1:1", "text": "Short."},
                {"beat_id": "1:2", "text": "Rest of the longer sentence."},
            ],
        }
    ]
    sentence_timings = {1: {"duration": 1.0, "start": 0.0, "end": 1.0}}
    windows = calculate_beat_durations(scenes, sentence_timings)
    assert all(w.duration >= 0.4 for w in windows)
