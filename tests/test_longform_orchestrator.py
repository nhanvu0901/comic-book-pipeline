"""
Unit tests for the long-form orchestrator — NO render, NO network.

Everything the orchestrator reaches for is stubbed: decompose.py / stitch.py
(may not exist on disk yet — the sibling crew builds them) via sys.modules, and
the Stage 3/4/5 pipeline functions + the voice picker likewise. The orchestrator
does all of these imports INSIDE run_longform, and CPython's import machinery
short-circuits on sys.modules, so a stub registered before the call is what gets
imported — no real (heavy) stage module is ever loaded.

Asserted: phase order, SAME voice_id passed to every segment, a failing segment
is skipped (not aborted) and reported, and stitch receives the surviving segment
mp4s in order.
"""
from __future__ import annotations

import sys
import types

import pytest


class Recorder:
    """Configurable fakes for every dependency run_longform imports, sharing one
    ordered event log so tests can assert the exact call sequence."""

    def __init__(self, segments, *, fail_write=(), fail_synth=None, voice=("voice-CARL-123", "Carl")):
        self.segments = list(segments)
        self.fail_write = set(fail_write)
        self.fail_synth = dict(fail_synth or {})   # slug -> exception instance to raise
        self.voice = voice
        self.events: list[tuple] = []
        self.voice_ids: list = []
        self.skip_reviews: list = []
        self.stitch_call = None
        self.decompose_call = None

    # ── Stage 2 (QA segments only) ──
    def preprocess_project(self, project_name, **kw):
        self.events.append(("preprocess", project_name))
        return []

    # ── Stage 3 ──
    def write_script(self, project_name, mode, hook_hint="", **kw):
        self.events.append(("write_script", project_name, mode))
        if project_name in self.fail_write:
            raise RuntimeError(f"boom write {project_name}")
        return f"NAR::{project_name}"

    def save_narration(self, narration, project_name, **kw):
        self.events.append(("save_narration", project_name, narration))
        return None

    # ── Stage 4 ──
    def synthesize_project(self, project_name, *, post_atempo=1.35, force=False,
                           voice_id=None, skip_review=False, **kw):
        self.events.append(("synthesize", project_name))
        self.voice_ids.append(voice_id)
        self.skip_reviews.append(skip_review)
        assert force is True, "segments must force-regenerate audio (fresh narration each run)"
        if project_name in self.fail_synth:
            raise self.fail_synth[project_name]
        return None

    # ── Stage 5 ──
    def assemble_project(self, project_name, *, force=False, **kw):
        self.events.append(("assemble", project_name))
        assert force is True, "segments must force-rebuild the video"
        return None

    # ── voice picker ──
    def select_voice(self, narration, comic_context=None, *, log=print):
        return self.voice

    # ── decompose / stitch ──
    def decompose_recap(self, saga_project, *, log=print):
        self.decompose_call = ("recap", saga_project)
        return list(self.segments)

    def decompose_qa(self, question, project, *, max_items, log=print):
        self.decompose_call = ("qa", question, project, max_items)
        return list(self.segments)

    def stitch_segments(self, segment_mp4s, out_path, *, dissolve=0.4, log=print):
        self.stitch_call = (list(segment_mp4s), out_path)
        return out_path


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Return a factory: build a Recorder, wire all stubs into sys.modules and
    patch get_project_dirs to a tmp root, then hand back (orchestrator, rec)."""
    def _make(**kwargs):
        rec = Recorder(**kwargs)

        def _mod(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            monkeypatch.setitem(sys.modules, name, m)
            return m

        _mod("stages.longform.decompose",
             decompose_recap=rec.decompose_recap, decompose_qa=rec.decompose_qa)
        _mod("stages.longform.stitch", stitch_segments=rec.stitch_segments)
        _mod("stages.stage_2.pipeline", preprocess_project=rec.preprocess_project)
        _mod("stages.stage_3.pipeline",
             write_script=rec.write_script, save_narration=rec.save_narration)
        _mod("stages.stage_4.pipeline", synthesize_project=rec.synthesize_project)
        _mod("stages.stage_5.pipeline", assemble_project=rec.assemble_project)
        _mod("stages.stage_4.resemble_tts", select_voice=rec.select_voice)

        import stages.longform.orchestrator as orch
        monkeypatch.setattr(orch, "get_project_dirs",
                            lambda slug: {"root": tmp_path / slug})
        return orch, rec, tmp_path
    return _make


def test_recap_happy_path_order_and_voice(wired):
    orch, rec, tmp = wired(segments=["saga__seg01", "saga__seg02", "saga__seg03"])

    out = orch.run_longform("recap", source="saga", project="saga_long", log=lambda _m: None)

    # decompose got the saga slug
    assert rec.decompose_call == ("recap", "saga")

    # exact per-segment order, segments back-to-back
    names = [e for e in rec.events]
    assert names == [
        ("write_script", "saga__seg01", "recap_summary"),
        ("save_narration", "saga__seg01", "NAR::saga__seg01"),
        ("synthesize", "saga__seg01"),
        ("assemble", "saga__seg01"),
        ("write_script", "saga__seg02", "recap_summary"),
        ("save_narration", "saga__seg02", "NAR::saga__seg02"),
        ("synthesize", "saga__seg02"),
        ("assemble", "saga__seg02"),
        ("write_script", "saga__seg03", "recap_summary"),
        ("save_narration", "saga__seg03", "NAR::saga__seg03"),
        ("synthesize", "saga__seg03"),
        ("assemble", "saga__seg03"),
    ]

    # SAME voice_id passed to every segment, and it's the picked one
    assert rec.voice_ids == ["voice-CARL-123"] * 3
    # recap → review gate skipped
    assert rec.skip_reviews == [True, True, True]

    # stitch got all three segment mp4s IN ORDER, and returned path is the project final
    mp4s, out_path = rec.stitch_call
    assert mp4s == [tmp / "saga__seg01" / "final.mp4",
                    tmp / "saga__seg02" / "final.mp4",
                    tmp / "saga__seg03" / "final.mp4"]
    assert out_path == tmp / "saga_long" / "final.mp4"
    assert out == str(out_path)


def test_failing_segment_is_skipped_not_aborted(wired):
    # seg02 blows up in write_script — must be skipped, run continues, stitch gets 01+03
    orch, rec, tmp = wired(
        segments=["saga__seg01", "saga__seg02", "saga__seg03"],
        fail_write={"saga__seg02"})

    out = orch.run_longform("recap", source="saga", project="saga_long", log=lambda _m: None)

    # seg02 never reached synthesize/assemble
    assert ("synthesize", "saga__seg02") not in rec.events
    assert ("assemble", "saga__seg02") not in rec.events
    # seg01 and seg03 fully ran
    assert ("assemble", "saga__seg01") in rec.events
    assert ("assemble", "saga__seg03") in rec.events

    mp4s, out_path = rec.stitch_call
    assert mp4s == [tmp / "saga__seg01" / "final.mp4",
                    tmp / "saga__seg03" / "final.mp4"]
    assert out == str(out_path)


def test_qa_review_gate_systemexit_skips_segment(wired):
    # Q&A review gate raises SystemExit for one segment → skip + continue, honor gate.
    orch, rec, tmp = wired(
        segments=["qa__seg01", "qa__seg02"],
        fail_synth={"qa__seg01": SystemExit("not reviewed")})

    out = orch.run_longform("qa", source="Who survived it?", project="qa_long",
                            target_minutes=2.0, log=lambda _m: None)

    # decompose_qa got the question + a derived max_items
    assert rec.decompose_call[0] == "qa"
    assert rec.decompose_call[1] == "Who survived it?"
    assert rec.decompose_call[3] >= 12  # max_items floor

    # qa → review gate honored (skip_review False) for every segment reached
    assert all(sr is False for sr in rec.skip_reviews)
    # seg01 gate-blocked → skipped; seg02 shipped
    assert ("assemble", "qa__seg01") not in rec.events
    assert ("assemble", "qa__seg02") in rec.events

    mp4s, out_path = rec.stitch_call
    assert mp4s == [tmp / "qa__seg02" / "final.mp4"]
    assert out == str(out_path)


def test_no_segments_shipped_raises(wired):
    orch, rec, tmp = wired(segments=["a", "b"], fail_write={"a", "b"})
    with pytest.raises(RuntimeError, match="no segments shipped"):
        orch.run_longform("recap", source="saga", project="p", log=lambda _m: None)


def test_stop_after_decompose_short_circuits(wired):
    orch, rec, tmp = wired(segments=["a", "b"])
    out = orch.run_longform("recap", source="saga", project="p",
                            stop_after="decompose", log=lambda _m: None)
    assert out == ""
    assert rec.events == []          # no stage work
    assert rec.stitch_call is None   # no stitch


def test_bad_mode_rejected(wired):
    orch, rec, tmp = wired(segments=["a"])
    with pytest.raises(ValueError):
        orch.run_longform("bogus", source="x", project="p", log=lambda _m: None)
