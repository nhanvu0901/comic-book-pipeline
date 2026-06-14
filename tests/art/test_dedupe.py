"""Tests for art_pipeline.dedupe — near-duplicate detection + surgical rewrite."""
import art_pipeline.dedupe as dedupe


def _fake_sim(a, b):
    """Deterministic stand-in for the embedding model: 1.0 if identical text
    (case/space-insensitive), else a low constant."""
    na = " ".join(a.lower().split())
    nb = " ".join(b.lower().split())
    return 1.0 if na == nb else 0.1


def test_find_near_duplicates_flags_later_scene(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    scenes = [
        {"scene_id": 1, "text": "The cathedral dominates the skyline."},
        {"scene_id": 2, "text": "A river winds through the foreground."},
        {"scene_id": 3, "text": "The cathedral dominates the skyline."},  # dup of #1
    ]
    dups = dedupe.find_near_duplicates(scenes, threshold=0.86)
    # only the LATER scene is flagged, paired with its strongest earlier match
    assert len(dups) == 1
    later, earlier, sim = dups[0]
    assert (later, earlier) == (2, 0)   # 0-based indices
    assert sim == 1.0


def test_find_near_duplicates_none_when_distinct(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    scenes = [{"scene_id": 1, "text": "A"}, {"scene_id": 2, "text": "B"}]
    assert dedupe.find_near_duplicates(scenes, threshold=0.86) == []


from stages.stage_3.schema import Scene


def _scene(sid, text):
    wc = len(text.split())
    return Scene(scene_id=sid, text=text, page_ref=1, panel_ref=0, word_count=wc,
                 target_seconds=round(wc / 2.88, 2), connective=False, beat_id=sid,
                 is_intro=False, is_outro=False)


def test_dedupe_scenes_rewrites_later_duplicate(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    # rewrite returns a brand-new, distinct sentence
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "A wholly different observation here.")
    scenes = [_scene(1, "The cathedral dominates the skyline."),
              _scene(2, "The cathedral dominates the skyline.")]
    roles = {1: "cold_open", 2: "twist"}
    report = dedupe.dedupe_scenes(scenes, {}, roles, log=lambda m: None)
    assert scenes[1].text == "A wholly different observation here."
    assert scenes[1].word_count == 5
    assert len(scenes) == 2          # count preserved
    assert report["rewrites"] == 1
    assert report["max_similarity_after"] < 0.86


def test_dedupe_scenes_keeps_best_when_rewrite_keeps_duplicating(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    # rewrite stubbornly returns the SAME duplicate text every pass
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "The cathedral dominates the skyline.")
    scenes = [_scene(1, "The cathedral dominates the skyline."),
              _scene(2, "The cathedral dominates the skyline.")]
    warnings = []
    report = dedupe.dedupe_scenes(scenes, {}, {1: "cold_open", 2: "twist"},
                                  log=lambda m: warnings.append(m))
    assert len(scenes) == 2          # never drops a scene, never raises
    assert report["unresolved"] == 1
    assert any("still duplicated" in w for w in warnings)
