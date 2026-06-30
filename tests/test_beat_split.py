"""Stage-3 LLM visual-beat splitter (stages/stage_3/beat_split.py). The LLM call is
mocked so these assert the SAFETY contract deterministically: verbatim validation,
per-scene fallback to one beat, never-raises, and disabled/short/intro-outro handling."""
import json
import pytest

import stages.stage_3.beat_split as bs
import stages.stage_3._llm as llm


def _scene(text, **kw):
    s = {"text": text}
    s.update(kw)
    return s


def test_verbatim_ok():
    assert bs._verbatim_ok("He drew the gun and fired.",
                           ["He drew the gun", "and fired"])
    # reworded → invalid
    assert not bs._verbatim_ok("He drew the gun and fired.",
                               ["He pulled the gun", "and fired"])
    # dropped a word → invalid
    assert not bs._verbatim_ok("He drew the gun and fired.", ["He drew the gun"])
    assert not bs._verbatim_ok("x", [])


def _mock_llm(monkeypatch, payload_or_exc):
    monkeypatch.setattr(bs, "ENABLE_LLM_BEAT_SPLIT", True)
    monkeypatch.setattr(llm, "_client", lambda: object())

    def fake(client, model, system, user, max_tokens):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return payload_or_exc
    monkeypatch.setattr(llm, "_call_with_deadline", fake)


def test_valid_beats_applied(monkeypatch):
    scenes = [_scene("The Grim Knight dragged Gordon through the sewers, a gun to his head.")]
    payload = json.dumps({"1": ["The Grim Knight dragged Gordon through the sewers",
                                "a gun to his head"]})
    _mock_llm(monkeypatch, payload)
    bs.split_visual_beats(scenes, progress=None)
    assert scenes[0]["visual_beats"] == ["The Grim Knight dragged Gordon through the sewers",
                                         "a gun to his head"]


def test_non_verbatim_falls_back_to_one_beat(monkeypatch):
    text = "The Grim Knight dragged Gordon through the sewers, a gun to his head."
    scenes = [_scene(text)]
    # reworded beats → must be rejected → single-beat fallback
    _mock_llm(monkeypatch, json.dumps({"1": ["The Grim Knight HAULED Gordon", "a gun to his head"]}))
    bs.split_visual_beats(scenes, progress=None)
    assert scenes[0]["visual_beats"] == [text]


def test_llm_exception_never_raises(monkeypatch):
    text = "He felt not trauma but power as he stood over the body of the man."
    scenes = [_scene(text)]
    _mock_llm(monkeypatch, RuntimeError("rate limited"))
    bs.split_visual_beats(scenes, progress=None)   # must not raise
    assert scenes[0]["visual_beats"] == [text]


def test_disabled_keeps_one_beat(monkeypatch):
    monkeypatch.setattr(bs, "ENABLE_LLM_BEAT_SPLIT", False)
    scenes = [_scene("Some long enough sentence that would otherwise split here today.")]
    bs.split_visual_beats(scenes, progress=None)
    assert scenes[0]["visual_beats"] == ["Some long enough sentence that would otherwise split here today."]


def test_intro_outro_and_short_not_split(monkeypatch):
    scenes = [
        _scene("Ever wonder what if Batman killed?", is_intro=True),
        _scene("He felt power.", ),                       # < 6 words
        _scene("The comic is X.", is_outro=True),
    ]
    # even if the LLM returned splits, intro/outro/short are not candidates
    _mock_llm(monkeypatch, json.dumps({"1": ["a", "b"]}))
    bs.split_visual_beats(scenes, progress=None)
    assert scenes[0]["visual_beats"] == ["Ever wonder what if Batman killed?"]
    assert scenes[1]["visual_beats"] == ["He felt power."]
    assert scenes[2]["visual_beats"] == ["The comic is X."]
