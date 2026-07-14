"""Stage 5 panel AI upscale (Real-ESRGAN, config.PANEL_UPSCALE).

Panel crops (237-500px) blown up by _prepare_panel_frame's LANCZOS to fill 1080x1920
read soft; Real-ESRGAN sharpens the crop first (render_shot calls it right before
framing). subprocess.run is mocked throughout — no real binary is invoked."""
import os
import time
from pathlib import Path

from PIL import Image

import stages.stage_5.shots as S


def _make_panel(path: Path, w: int, h: int) -> None:
    Image.new("RGB", (w, h), (10, 20, 30)).save(path)


def test_small_magnification_skips_upscale():
    assert not S._needs_upscale(1080, 1920)   # already frame-sized
    assert not S._needs_upscale(900, 1600)    # cover ~1.2x, under the 1.3 gate


def test_large_magnification_needs_upscale():
    assert S._needs_upscale(237, 304)         # measured example from the task (~4.5-6.3x)


def test_upscale_runs_and_returns_up4(tmp_path, monkeypatch):
    panel = tmp_path / "panel_001.png"
    _make_panel(panel, 200, 300)

    def fake_run(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1])
        _make_panel(out, 800, 1200)   # pretend Real-ESRGAN produced the 4x image
    monkeypatch.setattr(S.subprocess, "run", fake_run)

    result = S._ai_upscale_panel(panel)
    assert result == panel.with_name("panel_001_up4.png")
    assert result.exists()


def test_binary_failure_falls_back_to_original(tmp_path, monkeypatch):
    panel = tmp_path / "panel_002.png"
    _make_panel(panel, 200, 300)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no such binary")
    monkeypatch.setattr(S.subprocess, "run", fake_run)

    result = S._ai_upscale_panel(panel)
    assert result == panel   # fallback, no raise


def test_cache_hit_skips_subprocess(tmp_path, monkeypatch):
    panel = tmp_path / "panel_003.png"
    _make_panel(panel, 200, 300)
    up = panel.with_name("panel_003_up4.png")
    _make_panel(up, 800, 1200)
    os.utime(up, (time.time() + 1, time.time() + 1))   # force up4 newer than the input

    def fake_run(cmd, **kwargs):
        raise AssertionError("subprocess.run should not be called on a cache hit")
    monkeypatch.setattr(S.subprocess, "run", fake_run)

    result = S._ai_upscale_panel(panel)
    assert result == up
