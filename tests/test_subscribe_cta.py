"""The default spoken CTA is part of the final narration scene, never an earlier beat."""
from copy import deepcopy
import json

from stages.stage_3.ending import ensure_subscribe_cta


def test_cta_extends_only_final_scene_and_its_last_visual_fragment():
    narration = {"mode": "explore_answer", "words_per_second": 3.4,
                 "total_word_count": 5, "estimated_duration_seconds": 1.47,
                 "scenes": [
                     {"scene_id": 1, "text": "First beat.", "word_count": 2,
                      "target_seconds": 0.59, "visual_beats": ["First beat."]},
                     {"scene_id": 2, "text": "The final beat ends.", "word_count": 4,
                      "target_seconds": 1.18, "is_outro": True,
                      "visual_beats": [{"text": "The final beat ends.", "query": "final scene"}]},
                 ]}
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


def test_existing_subscribe_call_is_not_duplicated():
    narration = {"scenes": [{"text": "Subscribe for more stories.",
                               "visual_beats": ["Subscribe for more stories."]}]}
    assert ensure_subscribe_cta(narration) is False
    assert narration["scenes"][0]["text"] == "Subscribe for more stories."


def test_story_mention_of_subscriber_does_not_count_as_a_call_to_action():
    narration = {"scenes": [{"text": "The hero found the subscriber list."}]}
    assert ensure_subscribe_cta(narration) is True
    assert narration["scenes"][0]["text"].endswith("Make sure to subscribe for more content.")


def test_saved_narration_feeds_the_full_final_line_to_tts(tmp_path, monkeypatch):
    from stages.stage_3 import pipeline
    from stages.stage_3.schema import Narration, Scene
    from stages.stage_4.pipeline import _build_emotional_transcript

    monkeypatch.setattr(pipeline, "get_project_dirs", lambda project: {"root": tmp_path})
    narration = Narration(mode="micro_moment", title="Test", hook="Opening.", scenes=[
        Scene(scene_id=1, text="Opening.", page_ref=1, panel_ref=0,
              word_count=1, target_seconds=0.3, is_intro=True),
        Scene(scene_id=2, text="Final payoff.", page_ref=2, panel_ref=0,
              word_count=2, target_seconds=0.6, is_outro=True,
              visual_beats=["Final payoff."]),
    ], total_word_count=3, estimated_duration_seconds=0.9)

    pipeline.save_narration(narration, "sample")
    saved = json.loads((tmp_path / "narration.json").read_text())
    final = saved["scenes"][-1]
    assert final["visual_beats"] == ["Final payoff. Make sure to subscribe for more content."]
    spoken = _build_emotional_transcript(saved["scenes"], "contemplative", lambda _: None)
    assert "Final payoff. Make sure to subscribe for more content." in spoken


def test_screen_qa_saver_adds_cta_to_final_query_beat(tmp_path, monkeypatch):
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
    saved = json.loads(path.read_text())
    assert saved["scenes"][-1]["text"].endswith("Make sure to subscribe for more content.")
    assert saved["scenes"][-1]["visual_beats"][-1]["query"] == "final movie scene"
