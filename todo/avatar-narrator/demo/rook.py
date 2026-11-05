"""Placeholder mascot 'Rook' (an original crow) drawn in code, three whole-pose images.

Each pose is one finished picture (like AnimeRewind's pose illustrations); the animator only
moves the whole picture. Drawn at 4x and downsampled for clean edges, with a white sticker
outline and a soft drop shadow baked in.
"""
from PIL import Image, ImageDraw, ImageFilter

S = 4                      # supersampling factor
W = H = 520                # final sprite size (px) at "2x" master scale used by the animator
INK = (18, 18, 26, 255)
BODY = (34, 37, 56, 255)
BELLY = (58, 64, 92, 255)
BEAK = (246, 168, 40, 255)
BEAK_DARK = (198, 112, 18, 255)
SCARF = (214, 38, 61, 255)
SCARF_DARK = (150, 22, 40, 255)
GOLD = (236, 190, 70, 255)
WHITE = (255, 255, 255, 255)


def _p(*xy):
    return [v * S for v in xy]


def _ellipse(d, cx, cy, rx, ry, fill, outline=INK, width=6):
    d.ellipse(_p(cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=outline, width=width * S)


def _poly(d, pts, fill, outline=INK, width=6):
    d.polygon([(x * S, y * S) for x, y in pts], fill=fill)
    if outline:
        d.line([(x * S, y * S) for x, y in pts + [pts[0]]], fill=outline, width=width * S, joint="curve")


def _wing(d, pts):
    _poly(d, pts, BODY)


def draw(pose: str) -> Image.Image:
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = 250

    # feet
    _poly(d, [(205, 470), (225, 470), (215, 495), (192, 498)], BEAK)
    _poly(d, [(275, 470), (295, 470), (308, 498), (285, 495)], BEAK)

    # back wing (left side, at rest)
    if pose != "think":
        _wing(d, [(125, 300), (95, 360), (110, 430), (150, 400)])

    # body + belly
    _ellipse(d, cx, 360, 145, 125, BODY)
    _ellipse(d, cx, 385, 88, 82, BELLY, outline=None)

    # front wing by pose
    if pose == "point":       # right wing stretched out, pointing up-right at the panel
        _wing(d, [(355, 330), (470, 250), (488, 262), (478, 276), (495, 288), (470, 300), (380, 372)])
    elif pose == "shock":     # both wings thrown up
        _wing(d, [(360, 320), (430, 230), (452, 238), (440, 262), (395, 360)])
        _wing(d, [(140, 320), (70, 230), (48, 238), (60, 262), (105, 360)])
    else:                     # think: right wing at rest, left wing raised to the chin
        _wing(d, [(372, 300), (402, 360), (388, 430), (350, 400)])

    # head
    _ellipse(d, cx, 175, 132, 120, BODY)
    # tuft
    _poly(d, [(215, 70), (205, 20), (238, 62)], BODY)
    _poly(d, [(240, 60), (250, 8), (265, 60)], BODY)
    _poly(d, [(265, 64), (292, 24), (288, 72)], BODY)

    # scarf with a tail (over the chin, like a collar)
    d.rounded_rectangle(_p(158, 262, 342, 300), radius=20 * S, fill=SCARF, outline=INK, width=6 * S)
    _poly(d, [(300, 280), (345, 282), (360, 360), (320, 352)], SCARF)
    _poly(d, [(318, 300), (340, 302), (348, 340), (326, 336)], SCARF_DARK, outline=None)

    # eyes
    lx, rx, ey = 200, 300, 170
    if pose == "shock":
        for x in (lx, rx):
            _ellipse(d, x, ey, 46, 54, WHITE)
            _ellipse(d, x, ey + 4, 12, 12, INK, outline=None)
        d.arc(_p(lx - 42, ey - 92, lx + 30, ey - 58), 200, 330, fill=INK, width=8 * S)
        d.arc(_p(rx - 30, ey - 92, rx + 42, ey - 58), 210, 340, fill=INK, width=8 * S)
    elif pose == "point":     # happy squint: upside-down arcs
        for x in (lx, rx):
            d.arc(_p(x - 34, ey - 18, x + 34, ey + 34), 200, 340, fill=WHITE, width=16 * S)
            d.arc(_p(x - 34, ey - 18, x + 34, ey + 34), 200, 340, fill=INK, width=5 * S)
    else:                     # think: eyes open, pupils glancing up-right, lids half down
        for x in (lx, rx):
            _ellipse(d, x, ey, 40, 44, WHITE)
            _ellipse(d, x + 12, ey - 12, 15, 15, INK, outline=None)
            d.chord(_p(x - 40, ey - 44, x + 40, ey + 44), 180, 360, fill=BODY, outline=INK, width=5 * S)

    # monocle on the right eye (signature detail, every pose)
    d.ellipse(_p(rx - 58, ey - 60, rx + 58, ey + 60), outline=GOLD, width=10 * S)
    d.ellipse(_p(rx - 58, ey - 60, rx + 58, ey + 60), outline=INK, width=3 * S)
    d.line(_p(rx + 40, ey + 46, rx + 70, ey + 130, rx + 52, ey + 168), fill=GOLD, width=5 * S, joint="curve")

    # beak
    if pose == "shock":
        _poly(d, [(215, 222), (285, 222), (250, 252)], BEAK)
        _poly(d, [(222, 262), (278, 262), (250, 300)], BEAK_DARK)
        _ellipse(d, 250, 258, 22, 14, (90, 20, 30, 255), outline=INK, width=4)
    elif pose == "point":
        _poly(d, [(212, 222), (290, 222), (262, 262), (250, 270), (238, 262)], BEAK)
        d.arc(_p(222, 236, 278, 270), 20, 160, fill=INK, width=5 * S)   # grin line
    else:
        _poly(d, [(220, 224), (282, 224), (250, 262)], BEAK)

    if pose == "think":       # left wing up at the chin, drawn over the head
        _wing(d, [(130, 330), (150, 262), (205, 250), (222, 268), (180, 292), (160, 350)])
        # thought dots
        for i, (x, y, r) in enumerate(((392, 92, 11), (420, 64, 14), (455, 40, 18))):
            _ellipse(d, x, y, r, r, WHITE)

    # downsample, then sticker outline + soft shadow
    img = img.resize((W, H), Image.LANCZOS)
    alpha = img.getchannel("A")
    outline = alpha.filter(ImageFilter.MaxFilter(17)).filter(ImageFilter.GaussianBlur(0.8))
    sticker = Image.new("RGBA", img.size, (255, 255, 255, 0))
    sticker.putalpha(outline)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(outline.point(lambda v: int(v * 0.45)).filter(ImageFilter.GaussianBlur(9)))
    out = Image.new("RGBA", (W + 24, H + 24), (0, 0, 0, 0))
    out.alpha_composite(shadow, (16, 18))
    out.alpha_composite(sticker, (8, 8))
    out.alpha_composite(img, (8, 8))
    return out


if __name__ == "__main__":
    sheet = Image.new("RGBA", (3 * (W + 24), H + 24), (240, 240, 235, 255))
    for i, pose in enumerate(("point", "shock", "think")):
        im = draw(pose)
        im.save(f"rook_{pose}.png")
        sheet.alpha_composite(im, (i * (W + 24), 0))
    sheet.convert("RGB").save("rook_sheet.jpg", quality=92)
    print("ok")
