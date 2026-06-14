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
