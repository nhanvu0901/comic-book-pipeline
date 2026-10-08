"""Pillow text rendering for cards when FFmpeg lacks the drawtext filter."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(path: str | Path, size: int):
    try:
        return ImageFont.truetype(str(path), size=size)
    except (OSError, ValueError):
        return ImageFont.load_default(size=size)


def render_text_card(path: str | Path, *, width: int, height: int,
                     background: str,
                     lines: list[tuple[str, str, int, float]],
                     font_path: str | Path,
                     logo_path: str | Path | None = None,
                     logo_width: int = 360,
                     logo_center_y: int | None = None) -> Path:
    """Draw centered (text, color, size, y-fraction) lines onto a card PNG."""
    output = Path(path)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    if logo_path and Path(logo_path).is_file():
        with Image.open(logo_path) as source:
            logo = source.convert("RGBA")
        logo.thumbnail((logo_width, height))
        cy = logo_center_y if logo_center_y is not None else height // 2
        image.paste(logo, ((width - logo.width) // 2, cy - logo.height // 2), logo)
    for text, color, size, y_fraction in lines:
        font = _font(font_path, size)
        # Keep long names/titles legible and within the frame.
        while size > 12 and draw.textbbox((0, 0), text, font=font)[2] > width * 0.9:
            size = max(12, int(size * 0.92))
            font = _font(font_path, size)
        draw.text((width / 2, height * y_fraction), text, fill=color,
                  font=font, anchor="mm")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output
