# tests/test_corner_logo.py
from pathlib import Path
from PIL import Image
from stages.stage_5.shots import _prepare_corner_logo


def test_prepare_corner_logo_scales_and_sets_alpha(tmp_path):
    src = tmp_path / "logo.png"
    Image.new("RGB", (600, 600), (200, 0, 0)).save(src)
    out = tmp_path / "corner.png"
    res = _prepare_corner_logo(src, out, width=108, alpha=0.55)
    assert res == out and out.exists()
    with Image.open(out) as im:
        assert im.mode == "RGBA"
        assert im.size[0] == 108            # scaled to target width
        # alpha applied: max alpha ≈ 0.55 * 255 ≈ 140
        a = im.split()[-1].getextrema()
        assert a[1] <= 145


def test_prepare_corner_logo_missing_source_returns_none(tmp_path):
    assert _prepare_corner_logo(tmp_path / "nope.png", tmp_path / "o.png", width=108, alpha=0.55) is None
