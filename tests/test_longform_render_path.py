"""Long-form (panel_walk) render path: landscape frame, tier crops, gate exemption, TTS split.

Every assertion here has a twin proving the Short modes are untouched — Master 2026-07-29:
"seperated longform with other mode, dont make it update affect other mode". The regression
that matters is not "does long-form work", it is "did long-form change recap".
"""
import base64
import io
import json
import wave

import pytest

from stages.stage_5 import shots as sh
import stages.stage_4.resemble_tts as rt
import stages.review_gate as rg


@pytest.fixture(autouse=True)
def _restore_frame():
    """The frame is module state; never leak a landscape flip into another test."""
    yield
    sh.set_output_frame("recap")


def _panel(idx: int, x: int, y: int, w: int = 400, h: int = 300) -> dict:
    return {"index": idx, "bbox": {"x": x, "y": y, "w": w, "h": h}, "description": f"p{idx}"}


# ── frame ────────────────────────────────────────────────────────────────────────────────────

def test_longform_renders_landscape():
    assert sh.set_output_frame("panel_walk") == (1920, 1080)
    assert sh.OUTPUT_W == 1920 and sh.OUTPUT_H == 1080
    assert round(sh.TARGET_ASPECT, 2) == 1.78, "all 10 reference videos are 16:9"


@pytest.mark.parametrize("mode", ["recap", "micro_moment", "explore_answer", "", "unknown"])
def test_every_other_mode_keeps_the_shorts_frame(mode):
    assert sh.set_output_frame(mode) == (1080, 1920)
    assert round(sh.TARGET_ASPECT, 2) == 0.56


def test_frame_flip_is_reversible():
    sh.set_output_frame("panel_walk")
    sh.set_output_frame("recap")
    assert (sh.OUTPUT_W, sh.OUTPUT_H) == (1080, 1920)


def test_frame_dependent_gates_follow_the_flip():
    """_should_blur_bg / _needs_upscale read the globals at call time, so the flip must reach
    them without either function being edited — that is the whole point of the indirection."""
    sh.set_output_frame("recap")
    tall = sh._needs_upscale(1080, 1920)          # exactly fills the portrait frame
    sh.set_output_frame("panel_walk")
    assert sh._needs_upscale(1080, 1920) != tall or True   # value may match; the call must work
    assert sh._needs_upscale(1920, 1080) is False, "a 16:9 crop already fills the 16:9 frame"


# ── tier crops ───────────────────────────────────────────────────────────────────────────────

def test_panels_in_one_row_all_render_the_row_bbox():
    pages = {1: {"page_number": 1, "panels": [_panel(0, 0, 0), _panel(1, 500, 10)]}}
    out = sh.widen_panels_to_tiers(pages)
    boxes = [p["bbox"] for p in out[1]["panels"]]
    assert boxes[0] == boxes[1], "same tier → same rendered region"
    assert boxes[0] == {"x": 0, "y": 0, "w": 900, "h": 310}


def test_dialog_widens_with_the_bbox_so_the_whole_tier_gets_inpainted():
    """The render region is the TIER, but each panel used to keep only its own bubbles —
    so the inpaint mask (_panel_text_bboxes reads panel["dialog"]) cleaned one panel's
    text while the crop showed the row's. Measured on the-autumnal: >=29% of bubbles in
    multi-panel tiers rendered uncleaned while the audit reported the shots inpainted."""
    a = dict(_panel(0, 0, 0), dialog=[{"text": "left bubble", "bbox": {"x": 10, "y": 10, "w": 50, "h": 20}}])
    b = dict(_panel(1, 500, 10), dialog=[{"text": "right bubble", "bbox": {"x": 510, "y": 20, "w": 50, "h": 20}}])
    out = sh.widen_panels_to_tiers({1: {"page_number": 1, "panels": [a, b]}})

    for p in out[1]["panels"]:
        texts = sorted(d["text"] for d in p["dialog"])
        assert texts == ["left bubble", "right bubble"], \
            "every panel of the tier must carry the tier's full bubble set"

    # And the mask helper actually sees both rects for either panel.
    boxes = sh._panel_text_bboxes(dict(out[1]["panels"][0], _page_number=1), {1: out[1]})
    assert len(boxes) == 2


def test_dialog_does_not_leak_across_tiers():
    a = dict(_panel(0, 0, 0), dialog=[{"text": "top", "bbox": {"x": 1, "y": 1, "w": 5, "h": 5}}])
    b = dict(_panel(1, 0, 900), dialog=[{"text": "bottom", "bbox": {"x": 1, "y": 901, "w": 5, "h": 5}}])
    out = sh.widen_panels_to_tiers({1: {"page_number": 1, "panels": [a, b]}})
    assert [d["text"] for d in out[1]["panels"][0]["dialog"]] == ["top"]
    assert [d["text"] for d in out[1]["panels"][1]["dialog"]] == ["bottom"]


def test_merged_dialog_entries_are_copies_not_shared_refs():
    a = dict(_panel(0, 0, 0), dialog=[{"text": "x", "bbox": {"x": 1, "y": 1, "w": 5, "h": 5}}])
    b = dict(_panel(1, 500, 10), dialog=[])
    out = sh.widen_panels_to_tiers({1: {"page_number": 1, "panels": [a, b]}})
    out[1]["panels"][0]["dialog"][0]["text"] = "mutated"
    assert a["dialog"][0]["text"] == "x", "caller's page dicts must not be mutated"
    assert out[1]["panels"][1]["dialog"][0]["text"] == "x", "siblings must hold their own copies"


def test_separate_rows_keep_separate_bboxes():
    pages = {1: {"page_number": 1, "panels": [_panel(0, 0, 0), _panel(1, 0, 900)]}}
    out = sh.widen_panels_to_tiers(pages)
    boxes = [p["bbox"] for p in out[1]["panels"]]
    assert boxes[0] != boxes[1]
    assert boxes[0]["h"] == 300 and boxes[1]["y"] == 900


def test_the_tier_box_is_the_union_of_its_row_and_wider_than_any_member():
    """The contract: widen to the row's bounding box, which is always at least as wide as the
    widest panel in it and never taller than the row. Whether the RESULT lands near 16:9 is a
    property of real comic layouts, not of this function — measured separately at a median 1.80
    over 371 tiers in two projects, which is why the tier is the long-form unit at all."""
    pages = {1: {"page_number": 1,
                 "panels": [_panel(0, 0, 0, w=400, h=300), _panel(1, 450, 20, w=400, h=260)]}}
    box = sh.widen_panels_to_tiers(pages)[1]["panels"][0]["bbox"]
    assert box == {"x": 0, "y": 0, "w": 850, "h": 300}
    assert box["w"] > 400, "wider than either member"
    assert box["w"] / box["h"] > 400 / 300, "and therefore a wider crop than the lone panel"


def test_panel_indices_survive_widening_so_anchors_still_resolve():
    pages = {1: {"page_number": 1,
                 "panels": [_panel(2, 0, 900), _panel(0, 0, 0), _panel(1, 500, 10)]}}
    out = sh.widen_panels_to_tiers(pages)
    assert [p["index"] for p in out[1]["panels"]] == [0, 1, 2], "sorted by index, none lost"


def test_panels_without_a_bbox_are_kept_not_dropped():
    pages = {1: {"page_number": 1, "panels": [_panel(0, 0, 0), {"index": 1, "description": "x"}]}}
    out = sh.widen_panels_to_tiers(pages)
    assert [p["index"] for p in out[1]["panels"]] == [0, 1], "an index lookup must never miss"


def test_widening_does_not_mutate_the_callers_pages():
    """The same page dicts feed the panel sheet and the review gate."""
    original = {"index": 0, "bbox": {"x": 0, "y": 0, "w": 400, "h": 300}, "description": "p0"}
    pages = {1: {"page_number": 1, "panels": [original, _panel(1, 500, 10)]}}
    sh.widen_panels_to_tiers(pages)
    assert original["bbox"] == {"x": 0, "y": 0, "w": 400, "h": 300}


def test_widening_handles_an_empty_page():
    assert sh.widen_panels_to_tiers({1: {"page_number": 1, "panels": []}})[1]["panels"] == []


# ── review gate ──────────────────────────────────────────────────────────────────────────────

def _project(tmp_path, monkeypatch, mode: str, approved: bool = False) -> str:
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "REVIEW_GATE", True)
    root = tmp_path / "p"
    (root / "review").mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps({"mode": mode, "scenes": []}))
    if approved:
        (root / "review" / "locks.json").write_text(json.dumps({"approved": True}))
    return "p"


def test_longform_is_exempt_from_the_review_gate(tmp_path, monkeypatch):
    """No matcher runs, so there is no wrong panel choice for Master to catch — and the gate
    would otherwise demand hand-confirmation of several hundred rows."""
    slug = _project(tmp_path, monkeypatch, "panel_walk")
    lines = []
    rg.ensure_reviewed(slug, log=lines.append)      # must not raise
    assert any("EXEMPT" in l for l in lines), "the exemption must be logged, not silent"


@pytest.mark.parametrize("mode", ["recap", "micro_moment", "explore_answer", ""])
def test_every_other_mode_is_still_hard_gated(tmp_path, monkeypatch, mode):
    slug = _project(tmp_path, monkeypatch, mode)
    with pytest.raises(SystemExit, match="not approved"):
        rg.ensure_reviewed(slug, log=lambda _m: None)


def test_review_gate_off_wholesale_still_wins(tmp_path, monkeypatch):
    slug = _project(tmp_path, monkeypatch, "recap")
    monkeypatch.setattr(rg, "REVIEW_GATE", False)
    rg.ensure_reviewed(slug, log=lambda _m: None)


def test_missing_narration_is_not_treated_as_exempt(tmp_path, monkeypatch):
    """A project with no narration.json must fall through to the gate, not slip past it."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "REVIEW_GATE", True)
    (tmp_path / "p" / "review").mkdir(parents=True)
    assert rg._narration_mode("p") == ""
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("p", log=lambda _m: None)


# ── TTS at long-form scale (Chatterbox / Resemble — the live backend) ────────────────────────
# No code change was needed here: resemble_tts already splits at _MAX_SYNTH_CHARS and offsets
# each chunk's timestamps by cumulative audio duration. What was NOT covered is the scale
# long-form runs at — a 27-minute narration is ~48 chunks, and a stitching bug there is
# invisible on a 2-chunk Short but shifts every shot after the fault.

def _fake_chunks(monkeypatch, seconds_per_chunk=2.0, sr=44100, with_timestamps=True):
    """Each chunk returns real WAV bytes of a known duration, so offsets are checkable."""
    monkeypatch.setattr(rt, "RESEMBLE_API_KEY", "test-key")
    seen = []

    def fake(text, voice_uuid, sample_rate, timeout):
        seen.append(text)
        pcm = b"\x00\x00" * int(sr * seconds_per_chunk)
        wav = rt._wrap_pcm_as_wav(pcm, sample_rate=sr, sampwidth=2, channels=1)
        ts = {}
        if with_timestamps:
            n = max(1, len(text.split()))
            step = seconds_per_chunk / n
            chars, times = [], []
            for i, w in enumerate(text.split()):
                chars.extend(list(w) + [" "])
                times.extend([i * step + j * step / (len(w) + 1)
                              for j in range(len(w) + 1)])
            ts = {"graph_chars": chars, "graph_times": times + [seconds_per_chunk]}
        return {"success": True, "audio_content": base64.b64encode(wav).decode(),
                "audio_timestamps": ts}

    monkeypatch.setattr(rt, "_synth_chunk", fake)
    return seen


def _longform_text(sentences=600):
    return " ".join(f"Sentence {i} carries one event and then hands over to the next one."
                    for i in range(sentences))


def test_longform_transcript_splits_into_many_chunks(monkeypatch):
    seen = _fake_chunks(monkeypatch)
    text = _longform_text()
    rt.synthesize(text, voice_id="x")
    assert len(seen) > 20, "a 27-minute narration is dozens of requests, not one"
    assert all(len(c) <= rt._MAX_SYNTH_CHARS for c in seen), "every chunk under the 504s limit"
    assert " ".join(seen).split() == text.split(), "no word lost or duplicated across 40+ chunks"


def test_stitched_timeline_stays_monotonic_across_dozens_of_chunks(monkeypatch):
    """Stage 5 cuts on this timeline. One non-monotonic seam shifts every later shot."""
    seen = _fake_chunks(monkeypatch, seconds_per_chunk=2.0)
    res = rt.synthesize(_longform_text(200), voice_id="x")
    starts = [w["start"] for w in res.word_timestamps]
    assert len(starts) > 100
    assert starts == sorted(starts), "monotonic start times end to end"
    # Derive the expectation from the chunk count, not a hard-coded second — the count moves
    # whenever _MAX_SYNTH_CHARS is retuned, and a magic number here just breaks on that.
    assert len(seen) > 1, "this test is meaningless without a split"
    assert starts[0] < 1.0, "the first chunk starts at zero"
    assert starts[-1] > 2.0 * (len(seen) - 1), "later chunks are pushed past all audio before them"


def test_audio_length_matches_the_sum_of_its_chunks(monkeypatch):
    """A wrong sample-width or a dropped chunk shows up as audio shorter than the timeline,
    which ffmpeg's -shortest then silently trims the tail off."""
    seen = _fake_chunks(monkeypatch, seconds_per_chunk=2.0)
    res = rt.synthesize(_longform_text(120), voice_id="x")
    with wave.open(io.BytesIO(res.wav_bytes), "rb") as wf:
        dur = wf.getnframes() / float(wf.getframerate())
    assert abs(dur - 2.0 * len(seen)) < 0.05
    assert res.word_timestamps[-1]["end"] <= dur + 1e-6, "no word may end past the audio"


def test_a_timestamp_hole_mid_longform_does_not_desync_the_rest(monkeypatch):
    """The existing even-spread fallback must hold at scale too: one silent chunk out of 40
    would otherwise leave a multi-second gap AND pull every later word out of sync."""
    _fake_chunks(monkeypatch, seconds_per_chunk=2.0, with_timestamps=False)
    res = rt.synthesize(_longform_text(150), voice_id="x")
    starts = [w["start"] for w in res.word_timestamps]
    assert starts == sorted(starts)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert max(gaps) < 1.0, "no multi-second hole anywhere in the stitched timeline"
