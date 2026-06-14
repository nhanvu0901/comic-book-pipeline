import pytest

from art_pipeline.assemble import _expand_extreme_bbox, _frame_bbox, plan_shots


def _page(n, w=2000, h=1500, n_panels=4, related=False):
    return {"page_number": n, "source_image": f"/tmp/p{n}.jpg",
            "image_dimensions": {"width": w, "height": h},
            "preprocessing_method": "web-related" if related else "vlm-regions",
            # spread panels across the canvas so context-framed crops stay
            # distinct (real VLM regions aren't clustered at the origin)
            "panels": [{"index": i,
                        "bbox": {"x": 450 * i, "y": 300 * i, "w": 400, "h": 300}}
                       for i in range(n_panels)]}


def _fixture():
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "a", "page_ref": 1, "panel_ref": 0},
        {"scene_id": 3, "text": "b", "page_ref": 2, "panel_ref": 0},
        {"scene_id": 4, "text": "out", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    plan = [
        {"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "motion": "static", "subject": "", "fallback": ""},
        {"scene_id": 2, "kind": "painting_region", "panel_ref": 0, "motion": "zoom_in", "subject": "", "fallback": ""},
        {"scene_id": 3, "kind": "related", "panel_ref": -1, "motion": "pan_right", "subject": "x", "fallback": "", "page_ref": 2},
        {"scene_id": 4, "kind": "painting_full", "panel_ref": -1, "motion": "zoom_out", "subject": "", "fallback": ""}]
    pages = {1: _page(1), 2: _page(2, related=True, n_panels=1)}
    timings = [{"scene_id": 1, "start": 0.0, "end": 3.0},
               {"scene_id": 2, "start": 3.0, "end": 6.0},
               {"scene_id": 3, "start": 6.0, "end": 9.0},
               {"scene_id": 4, "start": 9.0, "end": 12.0}]
    return narration, plan, pages, timings


def test_motion_and_bbox_per_kind():
    narration, plan, pages, timings = _fixture()
    shots = plan_shots(narration, plan, pages, timings, audio_duration=12.0)
    assert [s.motion for s in shots] == ["static", "zoom_in", "pan_right", "zoom_out"]
    assert shots[0].panel_bbox == {"x": 0, "y": 0, "w": 2000, "h": 1500}   # full painting
    # region crop = the zoom, now CONTEXT-FRAMED (padded + upscale-capped)
    assert shots[1].panel_bbox == _frame_bbox({"x": 0, "y": 0, "w": 400, "h": 300}, pages[1])
    assert shots[1].panel_bbox["w"] > 400                                  # got context
    assert shots[2].source_image == "/tmp/p2.jpg"                          # related page image
    assert all(s.text_bboxes == [] for s in shots)


def test_long_scene_splits_into_two_shots():
    narration, plan, pages, timings = _fixture()
    timings[1] = {"scene_id": 2, "start": 3.0, "end": 9.5}    # 6.5s >= ART_SHOT_SPLIT_SEC
    for t in timings[2:]:
        t["start"] += 3.5; t["end"] += 3.5
    shots = plan_shots(narration, plan, pages, timings, audio_duration=15.5)
    scene2 = [s for s in shots if s.scene_id == 2]
    assert len(scene2) == 2
    assert scene2[0].panel_bbox == _frame_bbox({"x": 0, "y": 0, "w": 400, "h": 300}, pages[1])
    # secondary = an UNUSED region (not region 0 again)
    assert scene2[1].panel_bbox != scene2[0].panel_bbox
    assert abs(scene2[0].duration_seconds - 6.5 * 0.6) < 0.01


def test_static_longer_than_4s_upgraded_to_motion():
    narration, plan, pages, timings = _fixture()
    timings[0] = {"scene_id": 1, "start": 0.0, "end": 4.6}    # static intro 4.6s < split
    for t in timings[1:]:
        t["start"] += 1.6; t["end"] += 1.6
    shots = plan_shots(narration, plan, pages, timings, audio_duration=13.6)
    assert shots[0].motion == "zoom_out"    # ART_MAX_STATIC_SEC guard


def test_even_split_fallback_and_audio_pad():
    narration, plan, pages, _ = _fixture()
    shots = plan_shots(narration, plan, pages, [], audio_duration=10.0)
    assert len(shots) == 4
    total = sum(s.duration_seconds for s in shots)
    assert total >= 10.0   # padded to cover audio (-shortest guard)


def test_split_secondary_motion_opposes_primary():
    narration, plan, pages, timings = _fixture()
    plan[1]["motion"] = "zoom_out"                            # primary = zoom_out
    timings[1] = {"scene_id": 2, "start": 3.0, "end": 9.5}    # 6.5s >= split
    for t in timings[2:]:
        t["start"] += 3.5; t["end"] += 3.5
    shots = plan_shots(narration, plan, pages, timings, audio_duration=15.5)
    scene2 = [s for s in shots if s.scene_id == 2]
    assert len(scene2) == 2
    assert scene2[0].motion == "zoom_out"
    assert scene2[1].motion == "zoom_in"    # opposes primary, not shot_id parity


def test_related_split_secondary_uses_painting_page():
    narration, plan, pages, timings = _fixture()
    timings[2] = {"scene_id": 3, "start": 6.0, "end": 12.0}   # related scene 6.0s >= split
    for t in timings[3:]:
        t["start"] += 3.0; t["end"] += 3.0
    shots = plan_shots(narration, plan, pages, timings, audio_duration=15.0)
    scene3 = [s for s in shots if s.scene_id == 3]
    assert len(scene3) == 2
    assert scene3[0].source_image == "/tmp/p2.jpg"            # web image, pan only
    assert scene3[0].motion == "pan_right"
    # secondary lives on the PAINTING page — the web image is never zoomed
    assert scene3[1].source_image == "/tmp/p1.jpg"
    assert scene3[1].motion in ("zoom_in", "zoom_out")


def test_scene_durations_absorb_inter_scene_gaps():
    narration, plan, pages, _ = _fixture()
    # 0.5s silence after each scene — visual must hold until the NEXT scene
    # starts (and the last scene runs to the end of the audio), otherwise every
    # later shot appears earlier than its audio (progressive A/V drift).
    timings = [{"scene_id": 1, "start": 0.0, "end": 2.5},
               {"scene_id": 2, "start": 3.0, "end": 5.5},
               {"scene_id": 3, "start": 6.0, "end": 8.5},
               {"scene_id": 4, "start": 9.0, "end": 11.5}]
    shots = plan_shots(narration, plan, pages, timings, audio_duration=12.0)
    assert len(shots) == 4
    for s in shots:
        assert abs(s.duration_seconds - 3.0) < 0.25   # each scene absorbs its gap
    total = sum(s.duration_seconds for s in shots)
    assert total >= 12.0          # covers the whole audio
    assert total <= 12.0 + 0.25   # no abnormal end-pad — drift is gone


def test_scene_durations_key_mismatch_falls_back_even():
    narration, plan, pages, timings = _fixture()
    timings[3]["scene_id"] = 99   # right count, wrong key — must not KeyError
    shots = plan_shots(narration, plan, pages, timings, audio_duration=12.0)
    assert len(shots) == 4
    for s in shots:
        assert abs(s.duration_seconds - 12.0 / 4) < 0.25   # even split fallback


def test_consecutive_identical_fulls_get_distinct_frames():
    # Hunt fallback exhaustion: scenes 3-5 all painting_full of the same page
    # (seen on circus-sideshow scenes 11-13 → 3 identical frames ≈15.6s).
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "a", "page_ref": 1, "panel_ref": 0},
        {"scene_id": 3, "text": "b", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 4, "text": "c", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 5, "text": "out", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    plan = [
        {"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "motion": "static", "subject": "", "fallback": ""},
        {"scene_id": 2, "kind": "painting_region", "panel_ref": 0, "motion": "zoom_in", "subject": "", "fallback": ""},
        {"scene_id": 3, "kind": "painting_full", "panel_ref": -1, "motion": "zoom_out", "subject": "", "fallback": ""},
        {"scene_id": 4, "kind": "painting_full", "panel_ref": -1, "motion": "zoom_out", "subject": "", "fallback": ""},
        {"scene_id": 5, "kind": "painting_full", "panel_ref": -1, "motion": "zoom_out", "subject": "", "fallback": ""}]
    pages = {1: _page(1)}
    timings = [{"scene_id": i, "start": 3.0 * (i - 1), "end": 3.0 * i}
               for i in range(1, 6)]
    shots = plan_shots(narration, plan, pages, timings, audio_duration=15.0)
    # no consecutive pair may show the identical frame
    for a, b in zip(shots, shots[1:]):
        assert (a.source_image, a.panel_bbox) != (b.source_image, b.panel_bbox)
    # the re-aimed shot opposes the motion of the shot before it
    assert shots[3].panel_bbox != shots[2].panel_bbox
    assert shots[3].motion != shots[2].motion


def test_extreme_wide_region_expanded():
    # Real case: intro region "gas lamp string" 3920x262 (aspect 15:1) →
    # cover-scale 7.3x triggered Stage 5's blur-bg contain → a thin sharp
    # sliver over blur, "not clear". Height must grow around the center.
    page = {"page_number": 1, "source_image": "/tmp/p1.jpg",
            "image_dimensions": {"width": 2000, "height": 1500},
            "preprocessing_method": "vlm-regions",
            "panels": [{"index": 0, "bbox": {"x": 0, "y": 600, "w": 2000, "h": 130}}]}
    narration = {"scenes": [{"scene_id": 1, "text": "a", "page_ref": 1, "panel_ref": 0}]}
    plan = [{"scene_id": 1, "kind": "painting_region", "panel_ref": 0,
             "motion": "zoom_in", "subject": "", "fallback": ""}]
    shots = plan_shots(narration, plan, {1: page}, [], audio_duration=3.0)
    b = shots[0].panel_bbox
    # context-framing already grows the thin strip's height (no more sliver),
    # then the aspect guard keeps it within bounds — both clamped to the canvas.
    assert b["h"] >= 130                                 # grown well past the 130px strip
    assert b["y"] <= 665 <= b["y"] + b["h"]              # the strip stays visible
    assert b["y"] >= 0 and b["y"] + b["h"] <= 1500       # clamped inside image
    assert b["w"] / b["h"] <= 2.5 + 0.01                 # aspect within bounds


def test_normal_region_bbox_unchanged():
    page = _page(1)
    bbox = {"x": 100, "y": 200, "w": 400, "h": 300}      # aspect 1.33 — fine
    assert _expand_extreme_bbox(bbox, page) == bbox


def test_extreme_tall_region_expanded():
    page = _page(1)   # 2000x1500
    out = _expand_extreme_bbox({"x": 900, "y": 0, "w": 100, "h": 1500}, page)
    assert out["w"] >= 1500 * 0.4                        # grown to h * MIN_ASPECT
    assert out["x"] >= 0 and out["x"] + out["w"] <= 2000  # clamped inside image
    assert out["h"] == 1500


def test_aspect_bounds_follow_output_frame(monkeypatch):
    import stages.stage_5.shots as shots
    from art_pipeline.assemble import _aspect_bounds
    lo, hi = _aspect_bounds()                       # default 9:16
    assert lo == pytest.approx(0.4, abs=0.01)
    assert hi == pytest.approx(2.5, abs=0.01)
    monkeypatch.setattr(shots, "OUTPUT_W", 1920)
    monkeypatch.setattr(shots, "OUTPUT_H", 1080)
    lo2, hi2 = _aspect_bounds()                     # 16:9 → scaled bounds
    assert lo2 == pytest.approx(0.7111 * (16 / 9), abs=0.02)
    assert hi2 == pytest.approx(4.4444 * (16 / 9), abs=0.05)


def test_build_srt_chunks_and_format():
    from art_pipeline.assemble import build_srt
    words = [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i in range(10)]
    srt = build_srt(words, max_words=7)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 2                          # 7 + 3 words
    assert blocks[0].splitlines()[0] == "1"
    assert "-->" in blocks[0].splitlines()[1]
    assert blocks[0].splitlines()[1].startswith("00:00:00,000")
    assert blocks[1].splitlines()[0] == "2"


def test_build_srt_empty():
    from art_pipeline.assemble import build_srt
    assert build_srt([]) == ""


def test_build_srt_skips_empty_cues():
    from art_pipeline.assemble import build_srt
    # middle chunk is all empty-word entries → no blank cue, numbering continuous
    words = ([{"word": f"a{i}", "start": i * 0.5, "end": i * 0.5 + 0.4}
              for i in range(3)]
             + [{"word": "", "start": 1.5 + i * 0.5, "end": 1.9 + i * 0.5}
                for i in range(3)]
             + [{"word": f"b{i}", "start": 3.0 + i * 0.5, "end": 3.4 + i * 0.5}
                for i in range(3)])
    srt = build_srt(words, max_words=3)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 2                       # all-empty chunk dropped
    assert blocks[0].splitlines()[0] == "1"
    assert blocks[1].splitlines()[0] == "2"       # numbering stays 1,2,... no gap
    assert blocks[0].splitlines()[2] == "a0 a1 a2"
    assert blocks[1].splitlines()[2] == "b0 b1 b2"
    # all-empty input → empty string, not a stray newline
    assert build_srt([{"word": "", "start": 0.0, "end": 0.4}]) == ""


def test_contextualize_bbox_caps_upscale_and_adds_context(monkeypatch):
    import stages.stage_5.shots as shots
    from art_pipeline.assemble import _contextualize_bbox
    monkeypatch.setattr(shots, "OUTPUT_W", 1920)
    monkeypatch.setattr(shots, "OUTPUT_H", 1080)
    page = {"image_dimensions": {"width": 3496, "height": 3934}}
    # Toledo panel 4: 524x1180 (4.5% area → 3.66x upscale before fix)
    out = _contextualize_bbox({"x": 1000, "y": 1000, "w": 524, "h": 1180}, page)
    # grew on the binding axis; upscale now capped ~1.4 (=1920/min width 1371)
    upscale = max(1920 / out["w"], 1080 / out["h"])
    assert upscale <= 1.45
    assert out["w"] > 524                     # added horizontal context
    # stays inside the canvas
    assert out["x"] >= 0 and out["x"] + out["w"] <= 3496
    assert out["y"] >= 0 and out["y"] + out["h"] <= 3934


def test_contextualize_bbox_large_region_untouched(monkeypatch):
    import stages.stage_5.shots as shots
    from art_pipeline.assemble import _contextualize_bbox
    monkeypatch.setattr(shots, "OUTPUT_W", 1920)
    monkeypatch.setattr(shots, "OUTPUT_H", 1080)
    page = {"image_dimensions": {"width": 3496, "height": 3934}}
    # a region already larger than frame/upscale gets only the margin pad, clamped
    out = _contextualize_bbox({"x": 0, "y": 0, "w": 3496, "h": 1574}, page)
    assert out["w"] <= 3496 and out["h"] <= 3934   # clamped to canvas


def test_contextualize_bbox_degenerate_passthrough():
    from art_pipeline.assemble import _contextualize_bbox
    page = {"image_dimensions": {"width": 100, "height": 100}}
    assert _contextualize_bbox({"x": 0, "y": 0, "w": 0, "h": 0}, page) == \
        {"x": 0, "y": 0, "w": 0, "h": 0}
