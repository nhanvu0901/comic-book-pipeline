"""The spoken CTA is opt-in (config.SPOKEN_SUBSCRIBE_CTA, default off). Off, the script ends on
its real ending; on, the CTA is part of the final narration scene, never an earlier beat."""
from copy import deepcopy
import json

import pytest

from stages.stage_3.ending import SUBSCRIBE_CTA, ensure_subscribe_cta, remove_subscribe_cta


@pytest.fixture
def cta_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "SPOKEN_SUBSCRIBE_CTA", True)


def _two_scene_narration():
    return {"mode": "explore_answer", "words_per_second": 3.4,
            "total_word_count": 6, "estimated_duration_seconds": 1.76,
            "scenes": [
                {"scene_id": 1, "text": "First beat.", "word_count": 2,
                 "target_seconds": 0.59, "visual_beats": ["First beat."]},
                {"scene_id": 2, "text": "The final beat ends.", "word_count": 4,
                 "target_seconds": 1.18, "is_outro": True,
                 "visual_beats": [{"text": "The final beat ends.", "query": "final scene"}]},
            ]}


def test_flag_defaults_to_off():
    """Read from the source, not the imported module: a local .env may opt in."""
    from pathlib import Path
    import config

    source = Path(config.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("SPOKEN_SUBSCRIBE_CTA", "0")' in source


def test_off_by_default_appends_nothing():
    narration = _two_scene_narration()
    before = deepcopy(narration)

    assert ensure_subscribe_cta(narration) is False
    assert narration == before
    assert "subscribe" not in narration["scenes"][-1]["text"].lower()


def test_off_removes_a_leftover_pipeline_cta_and_resyncs_counts():
    narration = _two_scene_narration()
    ensure_subscribe_cta(narration, enabled=True)
    assert narration["scenes"][-1]["text"].endswith(SUBSCRIBE_CTA)

    assert ensure_subscribe_cta(narration) is False
    last = narration["scenes"][-1]
    assert last["text"] == "The final beat ends."
    assert last["visual_beats"] == [{"text": "The final beat ends.", "query": "final scene"}]
    assert last["word_count"] == 4
    assert last["target_seconds"] == pytest.approx(1.18, abs=0.02)
    assert narration["total_word_count"] == 6


def test_off_keeps_an_author_written_subscribe_line():
    narration = {"scenes": [{"text": "He wins. Make sure to subscribe for more.",
                             "visual_beats": ["He wins.", "Make sure to subscribe for more."]}]}
    before = deepcopy(narration)
    assert remove_subscribe_cta(narration) is False
    assert narration == before


def test_off_never_empties_a_scene_that_is_only_the_cta():
    narration = {"scenes": [{"text": SUBSCRIBE_CTA, "visual_beats": [SUBSCRIBE_CTA]}]}
    assert remove_subscribe_cta(narration) is False
    assert narration["scenes"][0]["text"] == SUBSCRIBE_CTA


def test_cta_extends_only_final_scene_and_its_last_visual_fragment(cta_on):
    narration = _two_scene_narration()
    first = deepcopy(narration["scenes"][0])

    assert ensure_subscribe_cta(narration) is True
    last = narration["scenes"][-1]
    assert narration["scenes"][0] == first
    assert last["text"].endswith("Make sure to subscribe for more content.")
    assert last["visual_beats"][-1]["text"] == last["text"]
    assert last["visual_beats"][-1]["query"] == "final scene"
    assert last["word_count"] == len(last["text"].split())
    assert last["target_seconds"] > 1.18
    assert narration["total_word_count"] == sum(s["word_count"] for s in narration["scenes"])
    once = deepcopy(narration)
    assert ensure_subscribe_cta(narration) is False
    assert narration == once


def test_explicit_argument_overrides_the_flag(cta_on):
    narration = _two_scene_narration()
    assert ensure_subscribe_cta(narration, enabled=False) is False
    assert not narration["scenes"][-1]["text"].endswith(SUBSCRIBE_CTA)


def test_existing_subscribe_call_is_not_duplicated(cta_on):
    narration = {"scenes": [{"text": "Subscribe for more stories.",
                               "visual_beats": ["Subscribe for more stories."]}]}
    assert ensure_subscribe_cta(narration) is False
    assert narration["scenes"][0]["text"] == "Subscribe for more stories."


def test_story_mention_of_subscriber_does_not_count_as_a_call_to_action(cta_on):
    narration = {"scenes": [{"text": "The hero found the subscriber list."}]}
    assert ensure_subscribe_cta(narration) is True
    assert narration["scenes"][0]["text"].endswith("Make sure to subscribe for more content.")


def _save_micro(tmp_path, monkeypatch):
    from stages.stage_3 import pipeline
    from stages.stage_3.schema import Narration, Scene

    monkeypatch.setattr(pipeline, "get_project_dirs", lambda project: {"root": tmp_path})
    narration = Narration(mode="micro_moment", title="Test", hook="Opening.", scenes=[
        Scene(scene_id=1, text="Opening.", page_ref=1, panel_ref=0,
              word_count=1, target_seconds=0.3, is_intro=True),
        Scene(scene_id=2, text="Final payoff.", page_ref=2, panel_ref=0,
              word_count=2, target_seconds=0.6, is_outro=True,
              visual_beats=["Final payoff."]),
    ], total_word_count=3, estimated_duration_seconds=0.9)
    pipeline.save_narration(narration, "sample")
    return json.loads((tmp_path / "narration.json").read_text())


def test_saved_narration_ends_on_the_story_by_default(tmp_path, monkeypatch):
    from stages.stage_4.pipeline import _build_emotional_transcript

    saved = _save_micro(tmp_path, monkeypatch)
    final = saved["scenes"][-1]
    assert final["text"] == "Final payoff."
    assert final["visual_beats"] == ["Final payoff."]
    spoken = _build_emotional_transcript(saved["scenes"], "contemplative", lambda _: None)
    assert "subscribe" not in spoken.lower()


def test_saved_narration_feeds_the_full_final_line_to_tts(tmp_path, monkeypatch, cta_on):
    from stages.stage_4.pipeline import _build_emotional_transcript

    saved = _save_micro(tmp_path, monkeypatch)
    final = saved["scenes"][-1]
    assert final["visual_beats"] == ["Final payoff. Make sure to subscribe for more content."]
    spoken = _build_emotional_transcript(saved["scenes"], "contemplative", lambda _: None)
    assert "Final payoff. Make sure to subscribe for more content." in spoken


def _save_screen(tmp_path, monkeypatch):
    from stages.stage_3 import screen_qa
    from stages.stage_3.schema import Narration, Scene

    monkeypatch.setattr(screen_qa, "get_project_dirs", lambda project: {"root": tmp_path})
    narration = Narration(mode="screen_qa", title="Question", hook="How?", scenes=[
        Scene(scene_id=1, text="How?", page_ref=0, panel_ref=-1, is_intro=True),
        Scene(scene_id=2, text="That is why it worked.", page_ref=0, panel_ref=-1,
              is_outro=True, visual_beats=[{"text": "That is why it worked.",
                                           "query": "final movie scene"}]),
    ])
    path = screen_qa.save_screen_narration(narration, "sample")
    return json.loads(path.read_text())


def test_screen_qa_saver_ends_on_the_story_by_default(tmp_path, monkeypatch):
    saved = _save_screen(tmp_path, monkeypatch)
    assert saved["scenes"][-1]["text"] == "That is why it worked."
    assert saved["scenes"][-1]["visual_beats"][-1] == {"text": "That is why it worked.",
                                                       "query": "final movie scene"}


def test_screen_qa_saver_adds_cta_to_final_query_beat(tmp_path, monkeypatch, cta_on):
    saved = _save_screen(tmp_path, monkeypatch)
    assert saved["scenes"][-1]["text"].endswith("Make sure to subscribe for more content.")
    assert saved["scenes"][-1]["visual_beats"][-1]["query"] == "final movie scene"
