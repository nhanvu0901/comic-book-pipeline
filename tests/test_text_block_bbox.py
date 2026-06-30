"""_vlm_text_blocks_with_magi_bboxes: VLM clean text + Magi bbox, paired per panel in
reading order; leftover Magi boxes become bbox-only mask entries. Guards the inpaint
mask against empty bboxes (which caused mirrored panels to show text backwards)."""
from stages.stage_2.pipeline import _vlm_text_blocks_with_magi_bboxes


def _box(x, y, w=50, h=20):
    return {"x": x, "y": y, "w": w, "h": h}


def _magi(*boxes):
    """Magi text entries are {"bbox": {...}, "ocr": ...}; we only need bbox here."""
    return [{"bbox": b, "ocr": "garbled"} for b in boxes]


def test_vlm_blocks_get_magi_bbox_in_reading_order():
    vlm = [
        {"panel_index": 0, "text": "TOP BUBBLE", "type": "speech"},
        {"panel_index": 0, "text": "BOTTOM BUBBLE", "type": "speech"},
    ]
    magi = _magi(_box(10, 200), _box(10, 10))          # out of order; box1 is higher (y=10)
    panel_texts = {0: [0, 1]}
    out = _vlm_text_blocks_with_magi_bboxes(vlm, panel_texts, magi)
    assert [t.text for t in out] == ["TOP BUBBLE", "BOTTOM BUBBLE"]
    # reading order → first VLM bubble paired with the TOP (y=10) box
    assert out[0].bbox == _box(10, 10)
    assert out[1].bbox == _box(10, 200)


def test_leftover_magi_box_becomes_bbox_only_entry():
    vlm = [{"panel_index": 1, "text": "ONE", "type": "speech"}]
    magi = _magi(_box(0, 0), _box(0, 100))             # two detected regions, one VLM bubble
    panel_texts = {1: [0, 1]}
    out = _vlm_text_blocks_with_magi_bboxes(vlm, panel_texts, magi)
    assert len(out) == 2
    assert out[0].text == "ONE" and out[0].bbox.get("w")
    leftover = out[1]
    assert leftover.text == "" and leftover.bbox.get("w")   # inert for embed, live for mask


def test_no_magi_boxes_leaves_empty_bbox_gracefully():
    vlm = [{"panel_index": 0, "text": "X", "type": "speech"}]
    out = _vlm_text_blocks_with_magi_bboxes(vlm, {}, [])
    assert len(out) == 1 and out[0].bbox == {}        # no crash, just no bbox (fallback)


def test_blank_vlm_text_skipped():
    vlm = [{"panel_index": 0, "text": "   ", "type": "speech"},
           {"panel_index": 0, "text": "REAL", "type": "speech"}]
    out = _vlm_text_blocks_with_magi_bboxes(vlm, {0: [0]}, _magi(_box(5, 5)))
    assert [t.text for t in out] == ["REAL"]
    assert out[0].bbox == _box(5, 5)
