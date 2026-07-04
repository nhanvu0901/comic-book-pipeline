"""Twist-landing headroom (learned from the doom-rocket-raccoon hand-fix, 2026-07-03):
the two closing story lines carry the story's biggest idea — the twist's implication
and the thematic mirror — and Master's approved versions run 22-24 words. The 18-word
line cap must NOT reject those two lines (they'd be rewritten flat by the retry loop),
while every other body line keeps the strict cap."""
from stages.stage_3.write_script import _validate


def _parsed(n_story: int, finale_words: int, mid_words: int = 10) -> dict:
    hook = "When a very long hook line opens the video it may run to many words fine..."
    scenes = [{"text": hook, "connective": None, "page_ref": 1, "beat_id": 1}]
    for b in range(2, n_story + 1):
        words = finale_words if b >= n_story - 1 else mid_words
        scenes.append({
            "text": " ".join(["word"] * words) + ".",
            "connective": None, "page_ref": 1, "beat_id": b,
        })
    scenes.append({"text": "The comic is Test Comic.", "connective": None,
                   "page_ref": 1, "beat_id": n_story})
    return {"scenes": scenes, "_coverage_gaps": [], "_anchor_pool_count": n_story}


def test_finale_two_lines_get_headroom():
    # last two STORY scenes at 24w (the approved twist-unpack + mirror register)
    parsed = _parsed(n_story=12, finale_words=24)
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 13)))
    cap_errs = [e for e in errors if "over the line cap" in e]
    assert not cap_errs, f"finale lines must be allowed 24w, got: {cap_errs}"


def test_mid_scene_still_capped_at_18():
    # a MIDDLE scene at 24w must still be rejected — headroom is finale-only
    parsed = _parsed(n_story=12, finale_words=10, mid_words=24)
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 13)))
    cap_errs = [e for e in errors if "over the line cap" in e]
    assert cap_errs, "mid-script 24w line must still hit the cap"
