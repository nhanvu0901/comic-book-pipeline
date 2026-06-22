"""Stage 5: a landscape panel (clearly wider than tall) must render via contain+blur
— the WHOLE panel stays visible — instead of cover-crop chopping its sides into a
meaningless center sliver (the magik 0:53 'disembodied hand' bug: p12 panel0 was
1988x1085, cover 1.77x < 2.5x guard, so cover-crop kept only ~31% of the width)."""
from PIL import Image
from stages.stage_5.shots import _prepare_panel_frame, OUTPUT_W, OUTPUT_H


def _make_landscape(path):
    # 1600x900 (aspect 1.78:1) -> cover 2.13x (< 2.5 guard) so the OLD code cover-crops.
    # Far-left column RED, far-right column GREEN, middle BLUE.
    im = Image.new("RGB", (1600, 900), (0, 0, 255))
    for y in range(900):
        for x in range(8):
            im.putpixel((x, y), (255, 0, 0))            # left edge
            im.putpixel((1599 - x, y), (0, 255, 0))     # right edge
    im.save(path)


def test_landscape_panel_keeps_both_side_edges(tmp_path):
    src = tmp_path / "wide.png"
    out = tmp_path / "frame.png"
    _make_landscape(src)
    _prepare_panel_frame(src, out)

    with Image.open(out) as f:
        assert f.size == (OUTPUT_W, OUTPUT_H)
        ycenter = OUTPUT_H // 2
        left = f.getpixel((2, ycenter))
        right = f.getpixel((OUTPUT_W - 3, ycenter))

    # Contain-fit centers the whole panel full-width -> the red left edge and green
    # right edge both survive at the frame's left/right. Cover-crop would drop them.
    assert left[0] > 150 and left[1] < 100, f"left edge lost (red gone): {left}"
    assert right[1] > 150 and right[0] < 100, f"right edge lost (green gone): {right}"


def test_portrait_splash_still_cover_fills(tmp_path):
    # A tall splash (1983x3047, like the p14 transform) must KEEP cover-scale (fill the
    # frame edge-to-edge), not get letterboxed. Cover fills width 1080 with no bars.
    src = tmp_path / "tall.png"
    out = tmp_path / "frame2.png"
    Image.new("RGB", (1983, 3047), (10, 20, 30)).save(src)
    _prepare_panel_frame(src, out)
    with Image.open(out) as f:
        # Top-left corner is real panel content (filled), not a blurred-bar artifact:
        # cover-fill means every pixel comes from the panel color ~ (10,20,30).
        px = f.getpixel((5, 5))
    assert abs(px[0] - 10) < 40 and abs(px[1] - 20) < 40, f"portrait splash not cover-filled: {px}"
