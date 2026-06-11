import pytest

from art_pipeline.assemble import plan_shots


def _page(n, w=2000, h=1500, n_panels=4, related=False):
    return {"page_number": n, "source_image": f"/tmp/p{n}.jpg",
            "image_dimensions": {"width": w, "height": h},
            "preprocessing_method": "web-related" if related else "vlm-regions",
            "panels": [{"index": i,
                        "bbox": {"x": 50 * i, "y": 40 * i, "w": 400, "h": 300}}
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
    assert shots[1].panel_bbox == {"x": 0, "y": 0, "w": 400, "h": 300}     # region crop = the zoom
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
    assert scene2[0].panel_bbox == {"x": 0, "y": 0, "w": 400, "h": 300}
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
