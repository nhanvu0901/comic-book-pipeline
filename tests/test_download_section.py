import subprocess
from pathlib import Path
import pytest
import config

from stages import clip_fetch
from stages.stage_5 import clips


def test_ytdlp_section_args_contains_download_sections_and_keyframes(tmp_path):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    start = 10.0
    beat_dur = 4.0
    # Formula: end = start + beat_dur * CLIP_SPEED_MAX + margin (default margin=2.0)
    expected_end = start + beat_dur * config.CLIP_SPEED_MAX + 2.0  # 10 + 4*1.25 + 2 = 17.0

    args = clip_fetch.ytdlp_section_args(url, tmp_path, start=start, beat_duration=beat_dur)
    joined = " ".join(args)

    assert "--download-sections" in joined
    assert f"*{start:.2f}-{expected_end:.2f}" in joined
    assert "--force-keyframes-at-cuts" in joined
    assert "--js-runtimes node" in joined
    assert "--merge-output-format mp4" in joined


def test_render_clip_preview_geometry_and_frames(tmp_path):
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")

    # Generate a 5s synthetic test video (640x360, 25fps)
    src_mp4 = tmp_path / "src.mp4"
    subprocess.run([
        ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
        "-t", "5.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_mp4)
    ], check=True, capture_output=True)

    preview_mp4 = tmp_path / "preview.mp4"
    beat_dur = 2.0
    clips.render_clip_preview(src_mp4, start=1.0, beat_duration=beat_dur, out_path=preview_mp4)

    assert preview_mp4.exists()
    info = clips.probe_video(preview_mp4)
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["duration"] - beat_dur) < 0.1
