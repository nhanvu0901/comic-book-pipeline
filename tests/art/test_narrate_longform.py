import pytest

from art_pipeline.narrate_longform import (
    _inject_chapter_flags, _is_forward_hook, _has_cta, build_chapter_scenes,
    validate_cross_chapter,
)

PAGES = [{
    "page_number": 1, "issue_label": "View of Toledo", "page_summary": "city",
    "image_dimensions": {"width": 4000, "height": 3000},
    # 16 panels so a 14-scene chapter can use all-distinct regions (variety rule 2)
    "panels": [{"index": i, "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
                "description": f"region {i}"} for i in range(16)],
}]
CTX = {"title": "View of Toledo", "summary": {"characters": [{"name": "El Greco"}]}}
# target 150 → 60-150% band = 90-225 words; the 14×7-word fixture (98) fits
CHAPTER = {"chapter_id": 2, "title": "The Painter", "role": "backfill",
           "facts": ["El Greco moved to Toledo"], "target_words": 150,
           "artwork_ids": [436575]}


def _scene(i, text, kind="painting_region", panel=0, subject=""):
    v = {"kind": kind}
    if kind == "painting_region":
        v["panel_ref"] = panel
    if kind == "related":
        v["subject"] = subject
    return {"text": text, "page_ref": 1, "panel_ref": panel if kind == "painting_region" else -1,
            "visual": v, "is_intro": False, "is_outro": False}


def _raw_chapter(n=14, rehook_last=True):
    scenes = []
    for i in range(n):
        kind = "related" if i % 3 == 2 else "painting_region"
        scenes.append(_scene(i, f"Scene number {i} says something concrete here.",
                             kind=kind, panel=i, subject=f"subject {i}"))
    if rehook_last:
        scenes[-1]["text"] = "But what the x-ray revealed next was stranger still."
    import json
    return json.dumps({"scenes": scenes})


def test_forward_hook_lexicon():
    assert _is_forward_hook("But what happened next was stranger still.")
    assert _is_forward_hook("Yet no one expected what the x-ray would show.")
    assert not _is_forward_hook("The painting now hangs in The Met.")


def test_cta_lexicon():
    assert _has_cta("Subscribe for more art stories!")
    assert _has_cta("like this video and comment below")
    assert not _has_cta("The storm still hangs over Toledo today.")


def test_chapter_scenes_built_with_offset():
    scenes, decls = build_chapter_scenes(
        _raw_chapter(), PAGES, CTX, CHAPTER, scene_id_offset=10,
        rehook_required=True)
    assert scenes[0].scene_id == 11
    assert all(d["chapter_id"] == 2 for d in decls)
    assert len(scenes) == len(decls) == 14


def test_chapter_too_few_scenes():
    with pytest.raises(ValueError, match="scenes"):
        build_chapter_scenes(_raw_chapter(n=5), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False)


def test_rehook_missing_rejected():
    with pytest.raises(ValueError, match="re-hook"):
        build_chapter_scenes(_raw_chapter(rehook_last=False), PAGES, CTX,
                             CHAPTER, scene_id_offset=0, rehook_required=True)


def _mk(scene_id, chapter_id, kind, *, page=1, panel=-1, subject=""):
    scene = {"scene_id": scene_id, "page_ref": page, "panel_ref": panel,
             "is_intro": False, "is_outro": False}
    decl = {"scene_id": scene_id, "chapter_id": chapter_id, "kind": kind,
            "panel_ref": panel, "subject": subject}
    return scene, decl


def test_cross_chapter_duplicate_subject_rejected():
    pairs = [_mk(1, 1, "related", subject="portrait of El Greco"),
             _mk(2, 2, "related", subject="Portrait of  el greco")]
    scenes = [p[0] for p in pairs]; decls = [p[1] for p in pairs]
    with pytest.raises(ValueError, match="subject"):
        validate_cross_chapter(scenes, decls)


def test_cross_chapter_adjacent_region_reuse_rejected():
    pairs = [_mk(1, 1, "painting_region", panel=3),
             _mk(2, 2, "painting_region", panel=3)]
    scenes = [p[0] for p in pairs]; decls = [p[1] for p in pairs]
    with pytest.raises(ValueError, match="adjacent"):
        validate_cross_chapter(scenes, decls)


def test_cross_chapter_nonadjacent_region_reuse_ok():
    pairs = [_mk(1, 1, "painting_region", panel=3),
             _mk(2, 3, "painting_region", panel=3)]
    scenes = [p[0] for p in pairs]; decls = [p[1] for p in pairs]
    validate_cross_chapter(scenes, decls)  # must not raise


def test_inject_chapter_flags_marks_rehook_chapter_tails():
    # 4 chapters x 2 scenes; chapters 2 and 3 are re-hook chapters
    narration = {"scenes": [{"scene_id": i} for i in range(1, 9)]}
    chapters_meta = [
        {"chapter_id": 1, "rehook": False, "scene_ids": [1, 2]},
        {"chapter_id": 2, "rehook": True, "scene_ids": [3, 4]},
        {"chapter_id": 3, "rehook": True, "scene_ids": [5, 6]},
        {"chapter_id": 4, "rehook": False, "scene_ids": [7, 8]},
    ]
    _inject_chapter_flags(narration, chapters_meta)
    by_id = {s["scene_id"]: s for s in narration["scenes"]}
    # every scene tagged with its chapter
    assert [by_id[i]["chapter_id"] for i in range(1, 9)] == [1, 1, 2, 2, 3, 3, 4, 4]
    # ONLY the LAST scene of each rehook chapter carries is_rehook=True
    rehook_ids = [s["scene_id"] for s in narration["scenes"] if s.get("is_rehook")]
    assert rehook_ids == [4, 6]
    # non-rehook scenes do not carry the key at all
    assert "is_rehook" not in by_id[2] and "is_rehook" not in by_id[3]
    assert "is_rehook" not in by_id[8]
