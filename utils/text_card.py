"""Text card generator for video slides and fallbacks."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def generate_text_card_image(
    title: str,
    subtitle: str,
    out_path: Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Generate a clean 9:16 vertical text card image (1080x1920) on dark gradient/border."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Base dark background #0B0F19
    img = Image.new("RGB", (width, height), color=(11, 15, 25))
    draw = ImageDraw.Draw(img)

    # Subtle decorative border
    margin = 60
    draw.rectangle(
        [(margin, margin), (width - margin, height - margin)],
        outline=(30, 41, 59),
        width=4,
    )
    # Accent top border bar
    draw.rectangle(
        [(margin, margin), (width - margin, margin + 12)],
        fill=(220, 38, 38),  # Crimson accent
    )

    # Try system fonts, fallback to default
    try:
        title_font = ImageFont.truetype("Arial", 52)
        sub_font = ImageFont.truetype("Arial", 36)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    # Draw title
    wrapped_title = textwrap.fill(title.upper(), width=28)
    draw.text(
        (width // 2, height // 2 - 120),
        wrapped_title,
        font=title_font,
        fill=(255, 255, 255),
        anchor="mm",
        align="center",
    )

    # Draw subtitle/caption
    if subtitle:
        wrapped_sub = textwrap.fill(subtitle, width=38)
        draw.text(
            (width // 2, height // 2 + 80),
            wrapped_sub,
            font=sub_font,
            fill=(148, 163, 184),
            anchor="mm",
            align="center",
        )

    img.save(out_path, "PNG")
    return out_path


def generate_text_card_video(
    title: str,
    subtitle: str,
    duration: float,
    out_video_path: Path,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Generate an exact H.264 video clip from a text card (1080x1920 30fps yuv420p)."""
    out_video_path.parent.mkdir(parents=True, exist_ok=True)
    temp_img = out_video_path.parent / f"_temp_card_{out_video_path.stem}.png"
    generate_text_card_image(title, subtitle, temp_img, width=width, height=height)

    frames = max(1, round(duration * fps))

    cmd = [
        FFMPEG, "-y",
        "-loop", "1",
        "-i", str(temp_img),
        "-t", f"{duration:.3f}",
        "-vf", f"fps={fps}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_video_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if temp_img.exists():
            temp_img.unlink()

    return out_video_path
