from art_pipeline import regions


def test_clamp_bbox_pct_clamps_and_rejects_degenerate():
    assert regions.clamp_bbox_pct({"x": -5, "y": 0, "w": 120, "h": 50}) == \
        {"x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0}
    assert regions.clamp_bbox_pct({"x": 99, "y": 99, "w": 0.5, "h": 0.5}) is None
    assert regions.clamp_bbox_pct({"x": "bad"}) is None


def test_pct_to_pixels():
    px = regions.pct_to_pixels({"x": 25, "y": 50, "w": 50, "h": 25}, 2000, 1000)
    assert px == {"x": 500, "y": 500, "w": 1000, "h": 250}


def test_bbox_iou_identical_and_disjoint():
    a = {"x": 10, "y": 10, "w": 30, "h": 30}
    assert regions.bbox_iou(a, dict(a)) == 1.0
    assert regions.bbox_iou(a, {"x": 60, "y": 60, "w": 10, "h": 10}) == 0.0


def test_filter_regions_dedups_keeping_higher_significance():
    r1 = {"bbox_pct": {"x": 10, "y": 10, "w": 30, "h": 30},
          "description": "a face", "significance": "the key figure",
          "dominant_emotion": "calm"}
    r2 = dict(r1, significance="")  # near-duplicate, weaker
    r2["bbox_pct"] = {"x": 12, "y": 12, "w": 30, "h": 30}
    tiny = {"bbox_pct": {"x": 0, "y": 0, "w": 1, "h": 1}, "description": "dust",
            "significance": "", "dominant_emotion": ""}
    out = regions.filter_regions([r2, r1, tiny])
    assert len(out) == 1 and out[0]["significance"] == "the key figure"


def test_grid_fallback_has_full_view_plus_quadrants_plus_center():
    out = regions.grid_fallback_regions()
    assert len(out) == 6
    assert out[0]["bbox_pct"] == {"x": 0, "y": 0, "w": 100, "h": 100}


def test_build_page_dict_matches_preprocessed_schema():
    regs = regions.grid_fallback_regions()
    page = regions.build_page_dict(
        page_number=1, image_path="/abs/a.jpg", width=2000, height=1000,
        regions=regs, page_summary="A wheat field under a stormy sky.",
        artwork_label="Wheat Field with Cypresses",
        model_used="grid-fallback", content_hash="abc123",
    )
    assert page["is_story_page"] is True and page["page_type"] == "story"
    assert page["page_number"] == 1
    assert len(page["panels"]) == 6
    p0 = page["panels"][0]
    assert p0["index"] == 0 and set(p0["bbox"]) == {"x", "y", "w", "h"}
    assert page["text_blocks"] == []
    assert page["issue_label"] == "Wheat Field with Cypresses"
