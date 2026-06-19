"""Set-of-marks overlay (2026-06-19): Stage 2 draws numbered Magi panel boxes on the
page before sending it to the VLM, so the VLM's per-panel `index` is anchored to Magi's
exact bboxes (fixes description↔bbox misalignment — the 'reduced to ash' bug)."""
import base64
import io

from PIL import Image

from stages.stage_2.vlm_extract import _encode_image, _encode_image_with_panels


def _make_image(tmp_path, w=600, h=900):
    p = tmp_path / "page.jpg"
    Image.new("RGB", (w, h), (200, 200, 200)).save(p, format="JPEG")
    return p


def _decode_size(b64):
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        return im.size


def test_overlay_returns_valid_jpeg_of_same_size(tmp_path):
    p = _make_image(tmp_path)
    panels = [{"bbox": {"x": 10, "y": 10, "w": 200, "h": 300}},
              {"bbox": {"x": 10, "y": 400, "w": 200, "h": 300}}]
    b64 = _encode_image_with_panels(p, panels)
    assert _decode_size(b64) == (600, 900)        # same canvas, marks drawn on top


def test_overlay_changes_pixels(tmp_path):
    # Drawing numbered magenta boxes must change the bytes vs the plain encode.
    p = _make_image(tmp_path)
    panels = [{"bbox": {"x": 10, "y": 10, "w": 200, "h": 300}}]
    assert _encode_image_with_panels(p, panels) != _encode_image(p)


def test_no_panels_falls_back_to_plain(tmp_path):
    p = _make_image(tmp_path)
    assert _encode_image_with_panels(p, []) == _encode_image(p)


def test_bad_bbox_does_not_crash(tmp_path):
    p = _make_image(tmp_path)
    panels = [{"bbox": {"x": 0, "y": 0, "w": 0, "h": 0}},   # zero-area → skipped
              {"bbox": {"x": 5, "y": 5, "w": 100, "h": 100}}]
    assert _decode_size(_encode_image_with_panels(p, panels)) == (600, 900)
