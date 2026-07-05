"""Stage 5 landscape framing (MOTION CORE 2026-07-04).

Competitors FILL the 9:16 frame; letterboxing (contain+blur) shrinks the subject — the
measured mid-video "tiny subject" defect. So a MODERATE landscape now COVER-crops to fill
the frame (dropping the far side edges), and only an EXTREME strip (aspect >=
LANDSCAPE_COVER_MAX_ASPECT, where a centered cover-crop would show a meaningless sliver)
still falls back to contain+blur to keep the whole panel visible."""
from PIL import Image
from stages.stage_5.shots import (
    _prepare_panel_frame, OUTPUT_W, OUTPUT_H, LANDSCAPE_COVER_MAX_ASPECT,
)


def _make_wide(path, w, h, edge=100):
    # Far-left band RED, far-right band GREEN, middle BLUE — so we can tell whether the side
    # edges survived (contain+blur) or were cropped away (cover-fill). The band is wide enough
    # that it stays solid (not antialiased away) even after a heavy contain downscale.
    im = Image.new("RGB", (w, h), (0, 0, 255))
    im.paste(Image.new("RGB", (edge, h), (255, 0, 0)), (0, 0))           # left edge
    im.paste(Image.new("RGB", (edge, h), (0, 255, 0)), (w - edge, 0))    # right edge
    im.save(path)


def test_moderate_landscape_cover_fills(tmp_path):
    # 1600x900 (aspect 1.78, below the 2.2 extreme line) -> COVER-crop fills the frame:
    # the blue middle fills edge-to-edge and the red/green side edges are cropped away.
    assert 1600 / 900 < LANDSCAPE_COVER_MAX_ASPECT
    src, out = tmp_path / "wide.png", tmp_path / "frame.png"
    _make_wide(src, 1600, 900)
    _prepare_panel_frame(src, out)
    with Image.open(out) as f:
        assert f.size == (OUTPUT_W, OUTPUT_H)
        ycenter = OUTPUT_H // 2
        center = f.getpixel((OUTPUT_W // 2, ycenter))
        left = f.getpixel((2, ycenter))
    # Cover-fill: every output pixel is the panel's CENTER (blue); the side edges are gone.
    assert center[2] > 150 and center[0] < 100, f"center not blue (not cover-filled): {center}"
    assert left[2] > 150 and left[0] < 100, f"left edge kept the red side (not cover-cropped): {left}"


def test_extreme_landscape_contain_blur_keeps_edges(tmp_path):
    # 3500x1000 (aspect 3.5, an extreme strip) -> contain+blur keeps the WHOLE panel, so a
    # centered cover-crop's meaningless sliver is avoided: the red left + green right survive.
    assert 3500 / 1000 >= LANDSCAPE_COVER_MAX_ASPECT
    src, out = tmp_path / "strip.png", tmp_path / "frame2.png"
    _make_wide(src, 3500, 1000)
    _prepare_panel_frame(src, out)
    with Image.open(out) as f:
        ycenter = OUTPUT_H // 2
        left = f.getpixel((2, ycenter))
        right = f.getpixel((OUTPUT_W - 3, ycenter))
    assert left[0] > 150 and left[1] < 100, f"left edge lost (red gone): {left}"
    assert right[1] > 150 and right[0] < 100, f"right edge lost (green gone): {right}"


def test_portrait_splash_still_cover_fills(tmp_path):
    # A tall splash (1983x3047, like the p14 transform) must KEEP cover-scale (fill the
    # frame edge-to-edge), not get letterboxed. Cover fills width 1080 with no bars.
    src = tmp_path / "tall.png"
    out = tmp_path / "frame3.png"
    Image.new("RGB", (1983, 3047), (10, 20, 30)).save(src)
    _prepare_panel_frame(src, out)
    with Image.open(out) as f:
        # Top-left corner is real panel content (filled), not a blurred-bar artifact:
        # cover-fill means every pixel comes from the panel color ~ (10,20,30).
        px = f.getpixel((5, 5))
    assert abs(px[0] - 10) < 40 and abs(px[1] - 20) < 40, f"portrait splash not cover-filled: {px}"
