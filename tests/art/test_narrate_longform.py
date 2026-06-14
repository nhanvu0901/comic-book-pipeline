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
# target 150 → 85-150% band = 127-225 words; the 14×10-word fixture (~140) fits
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
        scenes.append(_scene(i, f"Scene number {i} says something concrete here about the painting.",
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


def test_cta_in_outro_rejected():
    raw = _raw_chapter(rehook_last=False)
    import json as _json
    data = _json.loads(raw)
    data["scenes"][0]["is_intro"] = False
    data["scenes"][-1]["is_outro"] = True
    data["scenes"][-1]["text"] = "Subscribe for more stories about View of Toledo art."
    with pytest.raises(ValueError, match="call-to-action"):
        build_chapter_scenes(_json.dumps(data), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False, is_last=True)


def test_generic_hook_in_first_chapter_rejected():
    raw = _raw_chapter(rehook_last=False)
    import json as _json
    data = _json.loads(raw)
    data["scenes"][0]["is_intro"] = True
    data["scenes"][0]["text"] = "Ever wonder how a famous painting gets made over years?"
    with pytest.raises(ValueError, match="generic"):
        build_chapter_scenes(_json.dumps(data), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False, is_first=True)


def _mk(scene_id, chapter_id, kind, *, page=1, panel=-1, subject=""):
    scene = {"scene_id": scene_id, "page_ref": page, "panel_ref": panel,
             "is_intro": False, "is_outro": False}
    decl = {"scene_id": scene_id, "chapter_id": chapter_id, "kind": kind,
            "panel_ref": panel, "subject": subject}
    return scene, decl


def test_chapter_words_far_off_target_rejected():
    # 14 scenes × 10 words = 140 < 85% of a 230-word target (195.5) → reject;
    # 230/17 ≈ 13.5 so 14 scenes pass the scene-count floor and the WORD band fires
    big_chapter = dict(CHAPTER, target_words=230)
    with pytest.raises(ValueError, match="words vs target"):
        build_chapter_scenes(_raw_chapter(rehook_last=False), PAGES, CTX,
                             big_chapter, scene_id_offset=0,
                             rehook_required=False, log=lambda m: None)


def test_cross_chapter_duplicate_subject_rejected():
    pairs = [_mk(1, 1, "related", subject="portrait of El Greco"),
             _mk(2, 2, "related", subject="Portrait of  el greco")]
    scenes = [p[0] for p in pairs]; decls = [p[1] for p in pairs]
    with pytest.raises(ValueError, match="subject"):
        validate_cross_chapter(scenes, decls)


def _region_video(n, reuse_at=None, reuse_of=None):
    """n ordered region scenes, each its own panel; scene at 0-based position
    `reuse_at` optionally reuses the panel of the scene at `reuse_of`."""
    pairs = []
    for i in range(n):
        panel = reuse_of if reuse_at is not None and i == reuse_at else i
        pairs.append(_mk(i + 1, 1 + i // 7, "painting_region", panel=panel))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def test_cross_chapter_region_reuse_within_window_rejected():
    # scenes 5 and 8 share a region — only 3 apart (< ART_LF_REGION_REUSE_WINDOW=6)
    scenes, decls = _region_video(10, reuse_at=7, reuse_of=4)
    with pytest.raises(ValueError, match="scenes apart"):
        validate_cross_chapter(scenes, decls)


def test_cross_chapter_region_reuse_outside_window_ok():
    # scenes 1 and 9 share a region — 8 apart (>= window 6); spans chapters too
    scenes, decls = _region_video(10, reuse_at=8, reuse_of=0)
    validate_cross_chapter(scenes, decls)  # must not raise


def test_mid_chapter_double_painting_full_rejected():
    import json as _json
    data = _json.loads(_raw_chapter(rehook_last=False))
    for idx in (1, 10):  # 9 scenes apart — clears the reuse window, so the
        data["scenes"][idx]["visual"] = {"kind": "painting_full"}  # full-cap rule
        data["scenes"][idx]["panel_ref"] = -1                      # is what fires
    with pytest.raises(ValueError, match="painting_full"):
        build_chapter_scenes(_json.dumps(data), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False)


def test_repair_reaims_repeated_regions():
    # LLM aims EVERY region scene at panel 0 — the deterministic repair pass
    # must spread them (LRU on the same page) so validation passes
    import json as _json
    data = _json.loads(_raw_chapter(rehook_last=False))
    for s in data["scenes"]:
        if s["visual"]["kind"] == "painting_region":
            s["visual"]["panel_ref"] = 0
            s["panel_ref"] = 0
    scenes, decls = build_chapter_scenes(
        _json.dumps(data), PAGES, CTX, CHAPTER,
        scene_id_offset=0, rehook_required=False, log=lambda m: None)
    region = [(sc, d) for sc, d in zip(scenes, decls)
              if d["kind"] == "painting_region"]
    # every repeat of a panel keeps >= window(6) scenes between appearances
    # (a reuse exactly 6 apart is legal — repair only fixes violations)
    positions: dict[int, list[int]] = {}
    for idx, (sc, d) in enumerate(zip(scenes, decls)):
        if d["kind"] == "painting_region":
            positions.setdefault(d["panel_ref"], []).append(idx)
    for panel, idxs in positions.items():
        for a, b in zip(idxs, idxs[1:]):
            assert b - a >= 6, (panel, idxs)
    # repair actually spread the all-panel-0 input across many regions
    assert len(positions) >= 6
    # Scene objects stay in sync with the repaired decls
    assert all(sc.panel_ref == d["panel_ref"] for sc, d in region)


def test_repair_respects_history():
    # the previous chapter just showed panel 3 — a chapter opening on panel 3
    # must be re-aimed away from it
    import json as _json
    data = _json.loads(_raw_chapter(rehook_last=False))
    data["scenes"][0]["visual"]["panel_ref"] = 3
    data["scenes"][0]["panel_ref"] = 3
    scenes, decls = build_chapter_scenes(
        _json.dumps(data), PAGES, CTX, CHAPTER,
        scene_id_offset=0, rehook_required=False,
        history=[("r", 1, 3)], log=lambda m: None)
    assert decls[0]["panel_ref"] != 3
    assert scenes[0].panel_ref == decls[0]["panel_ref"]


def test_consecutive_duplicate_related_not_repaired():
    # two adjacent related scenes with the SAME subject must still be rejected
    # (repair only touches painting_region — subjects belong to the writer)
    import json as _json
    data = _json.loads(_raw_chapter(rehook_last=False))
    data["scenes"][3]["visual"] = {"kind": "related", "subject": "subject 2"}
    data["scenes"][3]["panel_ref"] = -1  # scene index 2 is already related "subject 2"
    with pytest.raises(ValueError, match="subject"):
        build_chapter_scenes(_json.dumps(data), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False,
                             log=lambda m: None)


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


def test_subject_used_in_earlier_chapter_rejected():
    import json as _json
    data = _json.loads(_raw_chapter(rehook_last=False))
    # make scene index 2 (a related scene in the fixture) reuse an earlier
    # chapter's subject, with different case/whitespace
    data["scenes"][2]["visual"]["subject"] = "Portrait of  EL GRECO"
    with pytest.raises(ValueError, match="already used"):
        build_chapter_scenes(_json.dumps(data), PAGES, CTX, CHAPTER,
                             scene_id_offset=0, rehook_required=False,
                             used_subjects={"portrait of el greco"})


def test_scene_floor_scales_with_word_target():
    # 320-word target needs ceil(320/17) = 19 scenes; the 14-scene fixture is rejected
    big_chapter = dict(CHAPTER, target_words=320)
    with pytest.raises(ValueError, match="needs 19-22 scenes"):
        build_chapter_scenes(_raw_chapter(rehook_last=False), PAGES, CTX,
                             big_chapter, scene_id_offset=0, rehook_required=False)


def test_new_narration_deletes_stale_hunt_manifest(tmp_path, monkeypatch):
    import json as _json
    from art_pipeline import narrate_longform as nl
    root = tmp_path / "proj"; (root / "preprocessed").mkdir(parents=True)
    monkeypatch.setattr(nl, "get_art_project_path", lambda name: root)
    (root / "outline.json").write_text(_json.dumps({
        "mode": "painting_story", "through_line": "Why?",
        "chapters": [{"chapter_id": 1, "title": "C", "role": "cold_open",
                      "facts": ["f"], "target_words": 170, "artwork_ids": [1]}]}))
    (root / "art_context.json").write_text(_json.dumps(
        {"title": "View of Toledo",
         "summary": {"characters": [{"name": "El Greco"}]}}))
    (root / "preprocessed" / "page_001.json").write_text(_json.dumps(PAGES[0]))
    (root / "hunt_manifest.json").write_text("[]")

    raw = _raw_chapter(rehook_last=False)
    data = _json.loads(raw)
    data["scenes"][0]["is_intro"] = True
    data["scenes"][0]["text"] = "View of Toledo hides a deliberate lie about the city skyline."
    data["scenes"][-1]["is_outro"] = True
    data["scenes"][-1]["text"] = "View of Toledo still hangs in The Met, storm intact."
    monkeypatch.setattr(nl, "call_with_chain",
                        lambda **k: (_json.dumps(data), "test-model"))
    monkeypatch.setattr(nl, "ART_LF_TOTAL_WORDS_FLOOR", 1)
    nl.write_longform_narration("proj", log=lambda *_: None)
    assert not (root / "hunt_manifest.json").exists()


def test_dedupe_called_after_draw_loop(monkeypatch, tmp_path):
    """write_longform_narration runs the dedupe guard on the assembled scenes
    and writes repetition_report.json."""
    import art_pipeline.narrate_longform as nlf
    from stages.stage_3.schema import Scene

    captured = {}

    def fake_dedupe(scenes, ctx, roles, **kw):
        captured["n"] = len(scenes)
        return {"rewrites": 0, "unresolved": 0, "max_similarity_after": 0.0}

    monkeypatch.setattr(nlf, "dedupe_scenes", fake_dedupe)
    # Drive the helper directly with a stub all_scenes via a thin wrapper:
    rep = nlf._run_dedupe(
        [Scene(scene_id=1, text="x y z", page_ref=1, panel_ref=0, word_count=3,
               target_seconds=1.0, connective=False, beat_id=1,
               is_intro=False, is_outro=False)],
        {"title": "T"},
        [{"chapter_id": 1, "role": "cold_open", "scene_ids": [1]}],
        tmp_path, log=lambda m: None)
    assert captured["n"] == 1
    assert (tmp_path / "repetition_report.json").exists()
    assert rep["rewrites"] == 0
