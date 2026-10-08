"""Wrapped-text card for screen_qa — the last level of its never-crash visual chain.

utils/text_card.render_text_card draws ONE line per entry and shrinks it until it fits the width,
which is right for a short title but turns a 20-word narration line into unreadable 12px type.
This module adds the missing piece (word-wrapped body, auto-sized to the frame) as a NEW function;
render_text_card itself is untouched and still serves the outro-card fallback.

Fonts come from the repo's fonts/ directory (the same Anton the outro card uses) — never a system
font by name, so a card renders identically on the Mac and the Windows server.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_FONT = Path(__file__).resolve().parent.parent / "fonts" / "Anton-Regular.ttf"


def _font(path: str | Path | None, size: int):
    try:
        return ImageFont.truetype(str(path or REPO_FONT), size=size)
    except (OSError, ValueError):
        return ImageFont.load_default(size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    """Greedy word wrap; a single word wider than max_width gets a line of its own."""
    words = str(text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and draw.textbbox((0, 0), trial, font=font)[2] > max_width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _fit_body(draw, text: str, font_path, max_width: float, max_height: float,
              sizes=(96, 84, 72, 64, 56, 48, 42, 36)) -> tuple[list[str], int, int]:
    """Largest size whose wrapped block fits max_height. If even the smallest overflows, the
    text is cut with an ellipsis instead of overrunning the frame."""
    line_gap = 0.22
    for size in sizes:
        font = _font(font_path, size)
        lines = wrap_text(draw, text, font, max_width)
        block = len(lines) * size * (1 + line_gap)
        if lines and block <= max_height:
            return lines, size, int(size * (1 + line_gap))
    size = sizes[-1]
    font = _font(font_path, size)
    lines = wrap_text(draw, text, font, max_width)
    step = int(size * (1 + line_gap))
    keep = max(1, int(max_height // step))
    if len(lines) > keep:
        lines = lines[:keep]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return lines, size, step


def render_screen_card(
    path: str | Path, *, width: int, height: int,
    headline: str = "", body: str = "", footer: str = "",
    background: str = "#0e1117", accent: str = "#58a6ff", body_color: str = "#ffffff",
    footer_color: str = "#8b949e", font_path: str | Path | None = None,
    logo_path: str | Path | None = None, logo_width: int = 240,
) -> Path:
    """Draw a card PNG: accent-ruled headline on top, the wrapped body centred, footer at the
    bottom. Never raises on an empty/odd string (an empty body just leaves the card plain)."""
    out = Path(path)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.08)
    max_w = width - 2 * margin

    top = int(height * 0.16)
    if logo_path and Path(logo_path).is_file():
        try:
            with Image.open(logo_path) as src:
                logo = src.convert("RGBA")
            logo.thumbnail((logo_width, int(height * 0.12)))
            image.paste(logo, ((width - logo.width) // 2, top - logo.height // 2), logo)
            top += logo.height
        except Exception:
            pass

    if headline:
        h_lines, h_size, h_step = _fit_body(draw, headline.upper(), font_path, max_w,
                                            height * 0.14, sizes=(56, 48, 42, 36, 30))
        font = _font(font_path, h_size)
        y = top + int(height * 0.04)
        for ln in h_lines:
            draw.text((width / 2, y), ln, fill=accent, font=font, anchor="mt")
            y += h_step
        draw.rectangle((margin, y + 12, width - margin, y + 18), fill=accent)

    if body:
        lines, size, step = _fit_body(draw, body, font_path, max_w, height * 0.50)
        font = _font(font_path, size)
        block = len(lines) * step
        y = (height - block) / 2 + step * 0.1
        for ln in lines:
            draw.text((width / 2, y), ln, fill=body_color, font=font, anchor="mt")
            y += step

    if footer:
        font = _font(font_path, 34)
        draw.text((width / 2, height - int(height * 0.08)), footer.upper(),
                  fill=footer_color, font=font, anchor="mm")

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out
