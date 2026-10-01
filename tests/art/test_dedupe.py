"""Tests for art_pipeline.dedupe — near-duplicate detection + surgical rewrite.

The similarity is the real utils.lexical_sim.seq_ratio (word sequences: no model, no
network), so it is not stubbed here; only the LLM rewrite is replaced."""
import art_pipeline.dedupe as dedupe
from art_pipeline.config import ART_LF_DEDUP_THRESHOLD

_CATHEDRAL = "The cathedral dominates the skyline of the old city."
_CATHEDRAL_REPEAT = "The cathedral dominates the skyline of the city."  # one word dropped


def test_find_near_duplicates_flags_later_scene():
    scenes = [
        {"scene_id": 1, "text": _CATHEDRAL},
        {"scene_id": 2, "text": "A river winds through the foreground."},
        {"scene_id": 3, "text": _CATHEDRAL_REPEAT},  # near-verbatim repeat of #1
    ]
    dups = dedupe.find_near_duplicates(scenes, threshold=ART_LF_DEDUP_THRESHOLD)
    # only the LATER scene is flagged, paired with its strongest earlier match
    assert len(dups) == 1
    later, earlier, sim = dups[0]
    assert (later, earlier) == (2, 0)   # 0-based indices
    assert sim >= ART_LF_DEDUP_THRESHOLD


def test_find_near_duplicates_none_when_distinct():
    scenes = [
        {"scene_id": 1, "text": _CATHEDRAL},
        {"scene_id": 2, "text": "A river winds through the foreground."},
        {"scene_id": 3, "text": "Dark clouds gather above the bridge."},
    ]
    assert dedupe.find_near_duplicates(scenes, threshold=ART_LF_DEDUP_THRESHOLD) == []


def test_find_near_duplicates_ignores_sentences_that_only_share_a_template():
    scenes = [
        {"scene_id": 1, "text": "The cathedral dominates the skyline."},
        {"scene_id": 2, "text": "The cathedral looms over the horizon."},
    ]
    assert dedupe.find_near_duplicates(scenes, threshold=ART_LF_DEDUP_THRESHOLD) == []


def test_find_near_duplicates_pairs_each_later_scene_with_its_strongest_earlier_match():
    scenes = [
        {"scene_id": 1, "text": _CATHEDRAL_REPEAT},
        {"scene_id": 2, "text": _CATHEDRAL},          # near-verbatim repeat of #1
        {"scene_id": 3, "text": _CATHEDRAL_REPEAT},   # identical to #1 (strongest), close to #2
    ]
    dups = dedupe.find_near_duplicates(scenes, threshold=ART_LF_DEDUP_THRESHOLD)
    assert [(later, earlier) for later, earlier, _ in dups] == [(1, 0), (2, 0)]
    assert dups[1][2] == 1.0
    # the first occurrence is never the reported side of a pair
    assert all(later != 0 for later, _earlier, _sim in dups)


def test_find_near_duplicates_blank_scenes_are_not_duplicates():
    scenes = [{"scene_id": 1, "text": ""}, {"scene_id": 2, "text": "   "}]
    assert dedupe.find_near_duplicates(scenes, threshold=ART_LF_DEDUP_THRESHOLD) == []


from stages.stage_3.schema import Scene


def _scene(sid, text):
    wc = len(text.split())
    return Scene(scene_id=sid, text=text, page_ref=1, panel_ref=0, word_count=wc,
                 target_seconds=round(wc / 2.88, 2), connective=False, beat_id=sid,
                 is_intro=False, is_outro=False)


def test_dedupe_scenes_rewrites_later_duplicate(monkeypatch):
    # rewrite returns a brand-new, distinct sentence
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "A wholly different observation here.")
    scenes = [_scene(1, _CATHEDRAL), _scene(2, _CATHEDRAL_REPEAT)]
    roles = {1: "cold_open", 2: "twist"}
    report = dedupe.dedupe_scenes(scenes, {}, roles, log=lambda m: None)
    assert scenes[0].text == _CATHEDRAL                    # the first occurrence is untouched
    assert scenes[1].text == "A wholly different observation here."
    assert scenes[1].word_count == 5
    assert len(scenes) == 2          # count preserved
    assert report["rewrites"] == 1
    assert report["max_similarity_after"] < ART_LF_DEDUP_THRESHOLD


def test_dedupe_scenes_keeps_best_when_rewrite_keeps_duplicating(monkeypatch):
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
    assert report["rewrites"] == 0


def test_dedupe_scenes_empty_rewrite_keeps_original(monkeypatch):
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "   ")  # whitespace only
    scenes = [_scene(1, "The cathedral dominates the skyline."),
              _scene(2, "The cathedral dominates the skyline.")]
    report = dedupe.dedupe_scenes(scenes, {}, {1: "cold_open", 2: "twist"}, log=lambda m: None)
    assert scenes[1].text == "The cathedral dominates the skyline."  # unchanged
    assert report["rewrites"] == 0
