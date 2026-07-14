"""build_panel_sheet: one JPEG contact sheet of every panel a render will use,
built from a plain shot list (dicts here — same shape as shots.json entries)."""
from PIL import Image

from stages.stage_5.panel_sheet import build_panel_sheet


def test_build_panel_sheet_writes_valid_jpeg(tmp_path):
    page1 = tmp_path / "project_page_01.jpg"
    page2 = tmp_path / "project_page_02.jpg"
    Image.new("RGB", (800, 1200), (255, 0, 0)).save(page1)
    Image.new("RGB", (800, 1200), (0, 255, 0)).save(page2)

    shots = [
        {"scene_id": 1, "source_image": str(page1),
         "panel_bbox": {"x": 10, "y": 10, "w": 300, "h": 400}, "duration_seconds": 2.3},
        {"scene_id": 1, "source_image": str(page2),
         "panel_bbox": {"x": 0, "y": 0, "w": 400, "h": 600}, "duration_seconds": 1.8},
        {"scene_id": 2, "source_image": str(page2),
         "panel_bbox": {}, "duration_seconds": 3.0},  # empty bbox -> whole page
    ]

    out_path = tmp_path / "panel_sheet.jpg"
    result = build_panel_sheet(shots, out_path, cols=4, thumb_h=320)

    assert result == out_path
    assert out_path.exists()
    with Image.open(out_path) as img:
        img.verify()
    assert out_path.stat().st_size > 0
