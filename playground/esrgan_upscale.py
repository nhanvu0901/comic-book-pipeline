"""
Play with Real-ESRGAN on any image(s) — upscale + optional before/after strip.

Usage:
    python scripts/esrgan_upscale.py panel.png                      # x4, anime model
    python scripts/esrgan_upscale.py page.jpg --scale 2             # x2
    python scripts/esrgan_upscale.py a.png b.png --compare          # + side-by-side jpg
    python scripts/esrgan_upscale.py panel.png --model realesrgan-x4plus   # photo model

Models available in <repo>/tools/realesrgan/models:
    realesrgan-x4plus-anime (default — comic/line art), realesrgan-x4plus (photo),
    realesr-animevideov3-x2 / -x3 / -x4 (video-tuned, softer)
Output lands in playground/esrgan_out/: <name>_up<scale>.png (+ <name>_compare.jpg).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "tools/realesrgan/realesrgan-ncnn-vulkan"
MODELS = BIN.parent / "models"

# ── Master chỉnh chỗ này: ảnh mặc định khi chạy không truyền argument ──
DEFAULT_IMAGE = Path('/Users/nhan/Documents/Mac home project/comic-book-pipeline/projects/damian-wayne-death/raw_comic/ch01_page_22.jpg')
OUT_DIR = Path(__file__).resolve().parent / "esrgan_out"   # mọi output gom về đây


def upscale(src: Path, scale: int, model: str) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{src.stem}_up{scale}.png"
    subprocess.run([str(BIN), "-i", str(src), "-o", str(out),
                    "-n", model, "-m", str(MODELS), "-s", str(scale)],
                   check=True, capture_output=True, timeout=120)
    return out


def compare_strip(src: Path, up: Path) -> Path:
    # both scaled to the same display height so the eye compares sharpness, not size
    from PIL import Image
    a, b = Image.open(src), Image.open(up)
    h = 900
    a2 = a.resize((int(a.width * h / a.height), h), Image.LANCZOS)
    b2 = b.resize((int(b.width * h / b.height), h), Image.LANCZOS)
    strip = Image.new("RGB", (a2.width + b2.width + 16, h), "white")
    strip.paste(a2, (0, 0))
    strip.paste(b2, (a2.width + 16, 0))
    out = OUT_DIR / f"{src.stem}_compare.jpg"
    strip.save(out, quality=92)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("images", nargs="*", type=Path, default=[DEFAULT_IMAGE],
                   help=f"mặc định: {DEFAULT_IMAGE.name}")
    p.add_argument("--scale", type=int, default=4, choices=(2, 3, 4))
    p.add_argument("--model", default="realesrgan-x4plus-anime")
    p.add_argument("--compare", action="store_true", help="also write before/after strip")
    args = p.parse_args()
    if not BIN.exists():
        sys.exit(f"binary not found: {BIN}")
    for src in args.images:
        up = upscale(src, args.scale, args.model)
        print(f"{src.name} {Image_size(src)} -> {up.name} {Image_size(up)}")
        if args.compare:
            print(f"  compare: {compare_strip(src, up).name}")
    return 0


def Image_size(p: Path) -> str:
    from PIL import Image
    with Image.open(p) as im:
        return f"{im.width}x{im.height}"


if __name__ == "__main__":
    raise SystemExit(main())
