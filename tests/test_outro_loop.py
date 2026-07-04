"""LOOP-CLOSE outro: _match_panels must land the outro on the SAME panel the video
opened on (unit 0), NOT its own content match — so the Short's last narrated frame
matches frame 1 and the auto-replay reads as a seamless loop. build_shots then forces
zoom_out on the outro shot (ends z=1.0 centered = the cold-open zoom_in's start frame)."""
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
