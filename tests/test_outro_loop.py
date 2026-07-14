"""LOOP-CLOSE outro: _match_panels must land the outro on the SAME panel the video
opened on (unit 0), NOT its own content match — so the Short's last narrated frame
matches frame 1 and the auto-replay reads as a seamless loop. build_shots then forces
zoom_out on the outro shot (ends z=1.0 centered = the cold-open zoom_in's start frame)."""
import importlib

import stages._embedding as _embedding
import stages.stage_5.shots as shots


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    tag = str(panel.get("description", "")).strip().lower()
    return (10.0, 0.9) if tag and tag in (chunk_text or "").lower() else (0.5, 0.1)


def test_outro_reuses_first_unit_panel(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = {10: {
        "source_image": "p10.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "page_type": "story",
        "panels": [
            {"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
             "description": t, "characters": []}
            for i, t in enumerate(["alpha", "beta", "gamma"])
        ],
        "text_blocks": [],
    }}
    units = [
        ({"scene_id": 1, "is_intro": True, "text": "hook"}, "alpha here"),
        ({"scene_id": 2, "text": "body"}, "beta here"),
        # outro text content-matches "gamma" — the loop-close must IGNORE that and
        # reuse unit 0's panel instead.
        ({"scene_id": 3, "is_outro": True, "text": "outro"}, "gamma here"),
    ]
    out = shots._match_panels(units, pages, {})
    first_p, first_src = out[0]
    outro_p, outro_src = out[-1]
    assert outro_src == first_src
    assert outro_p["index"] == first_p["index"]
    assert outro_p["_page_number"] == first_p["_page_number"]


# ── SEAMLESS_LOOP: _close_loop clones the cold-open frame onto the final shot ──
def _shot(sid, dur, src, motion, *, is_intro=False, w=700, h=1200, cap="", tb=None):
    from stages.stage_5.schema import Shot
    return Shot(shot_id=sid, scene_id=sid, duration_seconds=dur,
                panel_bbox={"x": 0, "y": 0, "w": w, "h": h}, source_image=src, motion=motion,
                text_bboxes=list(tb or []), caption_text=cap, is_intro=is_intro)


def test_close_loop_clones_first_visual_onto_last():
    """SEAMLESS_LOOP: the LAST shot's VISUAL (panel/source/mirror) becomes the FIRST
    (cold-open) shot's so the video ends where it began; duration + caption are kept and
    motion is forced to zoom_out (ends z=1.0 = the intro zoom_in's first frame)."""
    first = _shot(0, 2.0, "open.png", "zoom_in", is_intro=True, w=700, h=1200,
                  tb=[{"x": 1, "y": 2, "w": 3, "h": 4}])
    mid = _shot(1, 3.0, "mid.png", "pan_right", w=800, h=900)
    last = _shot(2, 1.5, "close.png", "pan_up", w=400, h=500, cap="outro line")
    last.no_mirror = False
    first.no_mirror = True
    shots._close_loop([first, mid, last])
    assert last.source_image == "open.png"
    assert last.panel_bbox == {"x": 0, "y": 0, "w": 700, "h": 1200}
    assert last.no_mirror is True
    assert last.motion == "zoom_out"
    assert last.duration_seconds == 1.5 and last.caption_text == "outro line"  # untouched
    assert mid.source_image == "mid.png"                                       # middle untouched
    # a distinct dict was cloned (mutating the last shot must not touch the first)
    last.panel_bbox["w"] = 1
    assert first.panel_bbox["w"] == 700


def test_close_loop_noop_without_intro():
    """No cold-open (first shot not is_intro) → _close_loop must not clobber the final shot."""
    a = _shot(0, 2.0, "a.png", "zoom_in", is_intro=False)
    b = _shot(1, 2.0, "b.png", "pan_right")
    shots._close_loop([a, b])
    assert b.source_image == "b.png" and b.motion == "pan_right"


def test_close_loop_overwrites_subject_panel_outro():
    """Q&A locked builder (_build_shots_per_chunk_locked) points the outro scene at a
    subject panel distinct from the intro's (e.g. subject_seq[QA_INTRO_SUBJECT_PANELS],
    the "next unused" subject panel after the intro's own subject #1) — the two bookends
    were deliberately DIFFERENT panels before this feature. With SEAMLESS_LOOP on,
    build_shots runs _close_loop as its LAST step over the finished shot list, so the
    loop-close still wins: the outro's visual becomes the cold-open (intro subject #1)
    panel, not the subject-outro panel. Desired interplay, not a bug (see _close_loop
    docstring) — the last frame must match the first for the loop seam."""
    first = _shot(0, 2.0, "subject_panel_1.png", "zoom_in", is_intro=True)
    mid = _shot(1, 3.0, "body_panel.png", "pan_right")
    subject_outro = _shot(2, 1.5, "subject_panel_4.png", "pan_up", cap="outro line")
    shots._close_loop([first, mid, subject_outro])
    assert subject_outro.source_image == "subject_panel_1.png"
    assert subject_outro.source_image != "subject_panel_4.png"


# ── SEAMLESS_LOOP default flip (Master 2026-07-09: loop ON for recap + Q&A) ──
def test_seamless_loop_default_is_on():
    assert shots.SEAMLESS_LOOP is True


def test_seamless_loop_env_off_disables(monkeypatch):
    """SEAMLESS_LOOP=0 must still turn the loop off (env override retained)."""
    monkeypatch.setenv("SEAMLESS_LOOP", "0")
    importlib.reload(shots)
    try:
        assert shots.SEAMLESS_LOOP is False
    finally:
        monkeypatch.delenv("SEAMLESS_LOOP", raising=False)
        importlib.reload(shots)
