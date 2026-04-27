"""
Pre-render caption chunks as transparent PNGs using Pillow.

Style: ALL CAPS bold, white fill, 4px black stroke. Centered text. PNG
is sized to fit the text plus a small padding. The assembler overlays
each PNG on the video at its (start, end) time using ffmpeg.
"""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .fonts import resolve_font_path


FONT_SIZE = 68
STROKE_WIDTH = 5
FILL = (255, 255, 255, 255)
STROKE = (0, 0, 0, 255)
MAX_CAPTION_WIDTH = 980      # px — stay inside 1080 frame with 50px margin
LINE_SPACING = 8             # extra px between wrapped lines


@dataclass
class RenderedCaption:
    text: str
    start: float
    end: float
    scene_id: int
    image_path: str
    width: int
    height: int


def render_caption_pngs(
    chunks: list[dict],
    out_dir: Path,
    *,
    font_path: str | None = None,
    font_size: int = FONT_SIZE,
) -> list[RenderedCaption]:
    """
    Render one PNG per chunk. Returns list aligned with the chunks input.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = font_path or resolve_font_path()
    font = ImageFont.truetype(fp, font_size)

    rendered: list[RenderedCaption] = []
    for i, c in enumerate(chunks):
        text = str(c.get("text", "")).strip().upper()
        if not text:
            continue

        lines = _wrap_text(text, font, MAX_CAPTION_WIDTH)
        png = _render_lines(lines, font, font_size)
        path = out_dir / f"cap_{i:03d}.png"
        png.save(path, "PNG")
        rendered.append(RenderedCaption(
            text=text,
            start=float(c.get("start", 0.0)),
            end=float(c.get("end", 0.0)),
            scene_id=int(c.get("scene_id", 0)),
            image_path=str(path),
            width=png.width,
            height=png.height,
        ))
    return rendered


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap to fit within max_width px."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for w in words:
        candidate = " ".join(current + [w])
        bb = font.getbbox(candidate)
        if (bb[2] - bb[0]) <= max_width:
            current.append(w)
        else:
            if current:
                lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _render_lines(lines: list[str], font: ImageFont.FreeTypeFont, font_size: int) -> Image.Image:
    # Measure
    widths, heights = [], []
    for ln in lines:
        bb = font.getbbox(ln)
        widths.append(bb[2] - bb[0])
        heights.append(bb[3] - bb[1])
    line_h = max(heights) if heights else font_size
    total_h = line_h * len(lines) + LINE_SPACING * (len(lines) - 1) + 2 * STROKE_WIDTH + 20
    total_w = max(widths) + 2 * STROKE_WIDTH + 20

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    y = STROKE_WIDTH + 10
    for ln in lines:
        bb = font.getbbox(ln)
        lw = bb[2] - bb[0]
        x = (total_w - lw) // 2
        d.text(
            (x, y),
            ln,
            font=font,
            fill=FILL,
            stroke_width=STROKE_WIDTH,
            stroke_fill=STROKE,
        )
        y += line_h + LINE_SPACING
    return img
