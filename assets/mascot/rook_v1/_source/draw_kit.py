"""Generator for the placeholder Rook kit (rook_v1).

    python3 assets/mascot/rook_v1/_source/draw_kit.py [--out DIR] [--no-sheets]

Rewrites kit.json, bodies/, faces/, accessories/ and the two review sheets. Drawing helpers are
adapted from todo/avatar-narrator/demo/rook.py (4x supersampling, ink outline, flat colours).
The sticker outline / drop shadow are NOT baked into parts; the contact sheets add a simple one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

S = 4                      # supersampling
K = 1.16                   # design units (rook.py 520px space) -> canvas px
CANVAS = 640
INK = (18, 18, 26, 255)
BODY = (34, 37, 56, 255)
BELLY = (58, 64, 92, 255)
BEAK = (246, 168, 40, 255)
BEAK_DARK = (198, 112, 18, 255)
SCARF = (214, 38, 61, 255)
SCARF_DARK = (150, 22, 40, 255)
GOLD = (236, 190, 70, 255)
WHITE = (255, 255, 255, 255)
BROW = (138, 146, 186, 255)
MOUTH = (90, 20, 30, 255)
HAT = (78, 72, 96, 255)
PAGE = (250, 246, 236, 255)
COVER = (204, 150, 50, 255)

# body pen origin: design (250, 170) (centre between the eyes) -> canvas (320, 237)
OX, OY = 320 - 250 * K, 237 - 170 * K
ANCHOR = (320, 237)
SHRUG_DY = 10          # canvas px the head drops in `shrug`


class Pen:
    """Draw in design units; origin (ox, oy) is where design (0, 0) lands in the output."""

    def __init__(self, w, h, ox, oy):
        self.w, self.h, self.ox, self.oy, self.dy = w, h, ox, oy, 0.0
        self.img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def P(self, x, y):
        return ((self.ox + x * K) * S, (self.oy + (y + self.dy) * K) * S)

    def wd(self, v):
        return max(1, round(v * K * S))

    def _box(self, x0, y0, x1, y1):
        a, b = self.P(x0, y0), self.P(x1, y1)
        return [a[0], a[1], b[0], b[1]]

    def ellipse(self, cx, cy, rx, ry, fill, outline=INK, width=6):
        self.d.ellipse(self._box(cx - rx, cy - ry, cx + rx, cy + ry), fill=fill,
                       outline=outline, width=self.wd(width) if outline else 0)

    def poly(self, pts, fill, outline=INK, width=6):
        P = [self.P(*p) for p in pts]
        self.d.polygon(P, fill=fill)
        if outline:
            self.d.line(P + [P[0]], fill=outline, width=self.wd(width), joint="curve")

    def line(self, pts, fill, width):
        self.d.line([self.P(*p) for p in pts], fill=fill, width=self.wd(width), joint="curve")

    def stroke(self, pts, fill, width):
        """Polyline with round caps."""
        self.line(pts, fill, width)
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            self.ellipse(x, y, r, r, fill, outline=None)

    def arc(self, box, a0, a1, fill, width):
        self.d.arc(self._box(*box), a0, a1, fill=fill, width=self.wd(width))

    def rrect(self, box, radius, fill, outline=INK, width=6):
        self.d.rounded_rectangle(self._box(*box), radius=radius * K * S, fill=fill,
                                 outline=outline, width=self.wd(width))

    def _mask_ellipse(self, cx, cy, rx, ry):
        m = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(m).ellipse(self._box(cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
        return m

    def _mask_below(self, cx, y0, slope):
        """Half-plane under the line through (cx, y0) with dy/dx = slope."""
        m = Image.new("L", self.img.size, 0)
        far = 400
        ImageDraw.Draw(m).polygon([self.P(cx - far, y0 - far * slope), self.P(cx + far, y0 + far * slope),
                                   self.P(cx + far, y0 + far * 3), self.P(cx - far, y0 + far * 3)],
                                  fill=255)
        return m

    def eye(self, cx, cy, rx, ry, pupil=None, lid=0.0, slope=0.0, w=6):
        """White eye with ink outline; `lid` = fraction of the height hidden under an ink lid line
        tilted by `slope` (dy/dx)."""
        y0 = cy - ry + lid * 2 * ry
        norm = (1 + slope * slope) ** 0.5
        h = w / 2
        ink = ImageChops.multiply(self._mask_ellipse(cx, cy, rx + h, ry + h),
                                  self._mask_below(cx, y0 - (h * norm if lid else h), slope))
        white = ImageChops.multiply(self._mask_ellipse(cx, cy, rx - h, ry - h),
                                    self._mask_below(cx, y0 + (h * norm if lid else 0), slope))
        self.img.paste(INK, mask=ink)
        self.img.paste(WHITE, mask=white)
        if pupil:
            px, py, pr = pupil
            pm = ImageChops.multiply(self._mask_ellipse(cx + px, cy + py, pr, pr), white)
            self.img.paste(INK, mask=pm)

    def done(self):
        return self.img.resize((self.w, self.h), Image.LANCZOS)


# ------------------------------------------------------------------ bodies
REST_L = [(125, 300), (95, 360), (110, 430), (150, 400)]
REST_R = [(372, 300), (402, 360), (388, 430), (350, 400)]


def mirror(pts):
    return [(500 - x, y) for x, y in pts]


def wing(p, pts):
    p.poly(pts, BODY)


def scarf(p):
    p.rrect((158, 262, 342, 300), 20, SCARF)
    p.poly([(300, 280), (345, 282), (360, 360), (320, 352)], SCARF)
    p.poly([(318, 300), (340, 302), (348, 340), (326, 336)], SCARF_DARK, outline=None)


def head(p, dy=0.0):
    p.dy = dy
    p.ellipse(250, 175, 132, 120, BODY)
    p.poly([(215, 70), (205, 20), (238, 62)], BODY)
    p.poly([(240, 60), (250, 8), (265, 60)], BODY)
    p.poly([(265, 64), (292, 24), (288, 72)], BODY)


def quad_pt(q, u, v):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q        # tl, tr, br, bl
    tx, ty = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
    bx, by = x3 + (x2 - x3) * u, y3 + (y2 - y3) * u
    return (tx + (bx - tx) * v, ty + (by - ty) * v)


def page(p, q, cells):
    p.poly(q, PAGE, width=4)
    inner = [quad_pt(q, .1, .1), quad_pt(q, .9, .1), quad_pt(q, .9, .9), quad_pt(q, .1, .9)]
    cols = [(255, 214, 120, 255), (150, 190, 230, 255), (232, 120, 130, 255)]
    for i, (u0, v0, u1, v1) in enumerate(cells):
        p.poly([quad_pt(inner, u0, v0), quad_pt(inner, u1, v0), quad_pt(inner, u1, v1),
                quad_pt(inner, u0, v1)], cols[i % 3], width=3)


def draw_body(pose):
    p = Pen(CANVAS, CANVAS, OX, OY)
    front = None
    p.poly([(205, 470), (225, 470), (215, 495), (192, 498)], BEAK)      # feet
    p.poly([(275, 470), (295, 470), (308, 498), (285, 495)], BEAK)
    if pose in ("stand", "point"):
        wing(p, REST_L)
    p.ellipse(250, 360, 145, 125, BODY)                                  # body + belly
    p.ellipse(250, 385, 88, 82, BELLY, outline=None)

    if pose == "stand":
        wing(p, REST_R)
    elif pose == "point":
        wing(p, [(355, 330), (470, 250), (488, 262), (478, 276), (495, 288), (470, 300), (380, 372)])
    elif pose == "arms_up":
        w = [(142, 325), (76, 205), (50, 198), (58, 225), (40, 235), (66, 258), (108, 365)]
        wing(p, w)
        wing(p, mirror(w))
    elif pose == "chin":
        wing(p, REST_R)
    elif pose == "shrug":
        w = [(138, 312), (92, 322), (60, 318), (34, 300), (48, 288), (30, 272), (56, 270), (50, 250),
             (84, 278), (134, 282)]
        wing(p, w)
        wing(p, mirror(w))

    head(p, SHRUG_DY / K if pose == "shrug" else 0.0)
    scarf(p)
    p.dy = 0
    if pose == "book":
        p.poly([(152, 334), (250, 347), (348, 334), (348, 436), (250, 449), (152, 436)], COVER)
        page(p, [(160, 342), (246, 354), (246, 440), (160, 428)],
             [(0, 0, 1, .4), (0, .5, .45, 1), (.55, .5, 1, 1)])
        page(p, [(254, 354), (340, 342), (340, 428), (254, 440)],
             [(0, 0, .45, .5), (.55, 0, 1, .5), (0, .6, 1, 1)])
        hand = [(116, 300), (150, 318), (180, 372), (186, 420), (152, 440), (122, 402), (98, 350)]
        wing(p, hand)
        wing(p, mirror(hand))
    elif pose == "arms_crossed":
        a = [(118, 312), (152, 304), (326, 384), (336, 408), (322, 414), (330, 432), (296, 428), (146, 362)]
        wing(p, a)
        wing(p, mirror(a))
    elif pose == "chin":
        f = Pen(CANVAS, CANVAS, OX, OY)
        wing(f, [(126, 340), (132, 296), (170, 262), (214, 244), (236, 256), (230, 276), (240, 284),
                 (210, 300), (170, 352)])
        front = f.done()
    return p.done(), front


# ------------------------------------------------------------------ faces (design coords relative to the anchor)
FW, FH, FAX, FAY = 360, 300, 180, 130
LX, RX = -50, 50


def beak_tri(p):
    p.poly([(-30, 54), (32, 54), (0, 92)], BEAK)


def brows_pair(p, left, right, w=8):
    p.stroke(left, BROW, w)
    p.stroke(right, BROW, w)


def draw_face(name):
    p = Pen(FW, FH, FAX, FAY)
    if name == "happy":
        for x in (LX, RX):
            p.arc((x - 34, -18, x + 34, 34), 200, 340, INK, 19)
            p.arc((x - 34, -18, x + 34, 34), 200, 340, WHITE, 11)
        p.poly([(-38, 52), (40, 52), (12, 92), (0, 100), (-12, 92)], BEAK)
        p.arc((-28, 66, 28, 100), 20, 160, INK, 5)
    elif name == "shock":
        for x in (LX, RX):
            p.eye(x, 0, 44, 50, pupil=(0, 4, 12))
        p.arc((LX - 40, -82, LX + 28, -56), 200, 330, BROW, 8)
        p.arc((RX - 28, -82, RX + 40, -56), 210, 340, BROW, 8)
        p.poly([(-35, 52), (35, 52), (0, 82)], BEAK)
        p.poly([(-28, 92), (28, 92), (0, 130)], BEAK_DARK)
        p.ellipse(0, 88, 22, 14, MOUTH, width=4)
    elif name == "suspicious":
        p.eye(LX, 0, 40, 40, pupil=(-12, 8, 13), lid=0.52, slope=0.22)
        p.eye(RX, 0, 40, 40, pupil=(-12, 6, 13), lid=0.22, slope=-0.05)
        p.stroke([(-90, -58), (-30, -38)], BROW, 9)
        p.stroke([(30, -58), (60, -70), (90, -60)], BROW, 8)
        p.poly([(-34, 54), (34, 54), (28, 76), (-28, 76)], BEAK)
        p.line([(-30, 66), (30, 66)], INK, 4)
    elif name == "sleepy":
        p.eye(LX, 0, 40, 40, pupil=(0, 14, 14), lid=0.55, slope=-0.28)
        p.eye(RX, 0, 40, 40, pupil=(0, 14, 14), lid=0.55, slope=0.28)
        brows_pair(p, [(-94, -38), (-30, -52)], [(30, -52), (94, -38)], 7)
        beak_tri(p)
    elif name == "sly":
        p.eye(LX, 0, 40, 40, pupil=(16, 6, 13), lid=0.42, slope=0.1)
        p.eye(RX, 0, 40, 40, pupil=(16, 6, 13), lid=0.42, slope=-0.1)
        brows_pair(p, [(-94, -58), (-28, -72)], [(28, -50), (94, -60)], 8)
        p.poly([(-34, 54), (38, 54), (8, 94), (-8, 94)], BEAK)
        p.line([(-12, 76), (4, 80), (18, 72), (26, 64)], INK, 4)
    elif name == "angry":
        p.eye(LX, 0, 38, 38, pupil=(0, 4, 10), lid=0.38, slope=0.55)
        p.eye(RX, 0, 38, 38, pupil=(0, 4, 10), lid=0.38, slope=-0.55)
        brows_pair(p, [(-88, -58), (-22, -32)], [(22, -32), (88, -58)], 10)
        p.poly([(-34, 56), (34, 56), (28, 84), (-28, 84)], BEAK)
        p.line([(-30, 70), (-20, 63), (-10, 70), (0, 63), (10, 70), (20, 63), (30, 70)], INK, 4)
    elif name == "sad":
        p.eye(LX, 0, 38, 42, pupil=(6, -4, 15), lid=0.3, slope=-0.4)
        p.eye(RX, 0, 38, 42, pupil=(-6, -4, 15), lid=0.3, slope=0.4)
        brows_pair(p, [(-96, -48), (-30, -76)], [(30, -76), (96, -48)], 8)
        p.poly([(-20, 56), (22, 56), (0, 84)], BEAK)
    elif name == "shout":
        for sgn, x in ((1, LX), (-1, RX)):
            pts = [(x - sgn * 30, -30), (x + sgn * 18, 0), (x - sgn * 30, 30)]
            p.stroke(pts, INK, 17)
            p.stroke(pts, WHITE, 9)
        p.poly([(-42, 50), (42, 50), (32, 78), (-32, 78)], BEAK)
        p.poly([(-30, 76), (30, 76), (24, 112), (-24, 112)], MOUTH, width=4)
        p.poly([(-24, 110), (24, 110), (0, 128)], BEAK_DARK)
    else:
        raise KeyError(name)
    return p.done()


def draw_monocle():
    # pen origin = the face anchor; the ring is centred on the right eye, 50 design units right of it
    p = Pen(200, 300, 30, 90)
    rx = 50
    p.line([(rx + 40, 46), (rx + 70, 130), (rx + 52, 168)], GOLD, 5)
    p.d.ellipse(p._box(rx - 58, -60, rx + 58, 60), outline=GOLD, width=p.wd(10))
    p.d.ellipse(p._box(rx - 58, -60, rx + 58, 60), outline=INK, width=p.wd(3))
    return p.done()


def draw_hat():
    p = Pen(360, 230, 180, 210)
    # design coords relative to the eye centre; the head top is at y=-115, the brim sits on it
    p.poly([(-46, -82), (-40, -168), (-18, -176), (0, -168), (18, -176), (40, -168), (46, -82)], HAT)
    p.ellipse(0, -84, 104, 17, HAT)
    p.poly([(-46, -100), (46, -100), (47, -84), (-47, -84)], SCARF, width=4)
    return p.done()


# ------------------------------------------------------------------ kit.json + sheets
BODIES = [("stand", "Đứng"), ("point", "Chỉ tay"), ("arms_up", "Giơ hai tay"), ("chin", "Chống cằm"),
          ("book", "Cầm truyện"), ("shrug", "Nhún vai"), ("arms_crossed", "Khoanh tay")]
FACES = [("happy", "Cười híp"), ("shock", "Giật mình"), ("suspicious", "Nghi ngờ"), ("sleepy", "Buồn ngủ"),
         ("sly", "Cười khẩy"), ("angry", "Tức giận"), ("sad", "Buồn"), ("shout", "Hét")]
PRESETS = [("neutral", "stand", "happy", "Bình thường"), ("point", "point", "happy", "Chỉ vào tranh"),
           ("shock", "arms_up", "shock", "Giật mình"), ("think", "chin", "suspicious", "Suy nghĩ"),
           ("sly", "arms_crossed", "sly", "Cười khẩy"), ("explain", "book", "happy", "Giải thích"),
           ("shrug", "shrug", "sad", "Nhún vai"), ("angry", "arms_crossed", "angry", "Tức giận"),
           ("sleepy", "stand", "sleepy", "Buồn ngủ"), ("shout", "arms_up", "shout", "Hét")]


def sticker(im, pad=26):
    big = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    big.alpha_composite(im, (pad, pad))
    a = big.getchannel("A").filter(ImageFilter.GaussianBlur(5)).point(lambda v: 255 if v > 6 else 0)
    a = a.filter(ImageFilter.GaussianBlur(1))
    out = Image.new("RGBA", big.size, (255, 255, 255, 0))
    out.putalpha(a)
    out.alpha_composite(big)
    return out


def compose(parts, body, face, accs):
    b = parts["bodies"][body]
    im = b["img"].copy()
    hx, hy = b["anchor"]
    im.alpha_composite(parts["faces"][face], (hx - FAX, hy - FAY))
    for a in accs:
        am = parts["acc"][a]
        im.alpha_composite(am["img"], (hx - am["anchor"][0], hy - am["anchor"][1]))
    if b["front"] is not None:
        im.alpha_composite(b["front"])
    return im


def font(size):
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def sheets(out, parts):
    f = font(18)
    cell = 250
    sheet = Image.new("RGBA", (90 + len(FACES) * cell, 40 + len(BODIES) * cell), (238, 238, 232, 255))
    dr = ImageDraw.Draw(sheet)
    for j, (fn, _) in enumerate(FACES):
        dr.text((90 + j * cell + 10, 10), fn, fill=(40, 40, 40, 255), font=f)
    for i, (bn, _) in enumerate(BODIES):
        dr.text((8, 40 + i * cell + 10), bn, fill=(40, 40, 40, 255), font=f)
        for j, (fn, _) in enumerate(FACES):
            im = sticker(compose(parts, bn, fn, ["monocle"])).resize((cell, cell), Image.LANCZOS)
            sheet.alpha_composite(im, (90 + j * cell, 40 + i * cell))
    sheet.convert("RGB").save(out / "contact_sheet.jpg", quality=90)

    cell, cols = 360, 5
    sheet = Image.new("RGBA", (cols * cell, 2 * (cell + 30)), (238, 238, 232, 255))
    dr = ImageDraw.Draw(sheet)
    for i, (pn, bn, fn, _) in enumerate(PRESETS):
        im = sticker(compose(parts, bn, fn, ["monocle"])).resize((cell, cell), Image.LANCZOS)
        x, y = (i % cols) * cell, (i // cols) * (cell + 30)
        sheet.alpha_composite(im, (x, y + 30))
        dr.text((x + 10, y + 6), f"{pn}  ({bn} + {fn})", fill=(40, 40, 40, 255), font=f)
    sheet.convert("RGB").save(out / "presets_sheet.jpg", quality=90)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--no-sheets", action="store_true")
    args = ap.parse_args()
    out = args.out
    for d in ("bodies", "faces", "accessories"):
        (out / d).mkdir(parents=True, exist_ok=True)

    parts = {"bodies": {}, "faces": {}, "acc": {}}
    kit = {"name": "rook_v1", "label": "Rook — quạ đeo kính một tròng", "version": 1,
           "canvas": [CANVAS, CANVAS], "bodies": {}, "faces": {}, "accessories": {}, "presets": {}}

    for bn, lab in BODIES:
        img, front = draw_body(bn)
        img.save(out / "bodies" / f"{bn}.png")
        if front is not None:
            front.save(out / "bodies" / f"{bn}_front.png")
        bbox = img.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
        anchor = (ANCHOR[0], ANCHOR[1] + (SHRUG_DY if bn == "shrug" else 0))
        parts["bodies"][bn] = {"img": img, "front": front, "anchor": anchor}
        kit["bodies"][bn] = {"file": f"bodies/{bn}.png",
                             "front": f"bodies/{bn}_front.png" if front is not None else None,
                             "head_anchor": list(anchor), "feet_y": bbox[3] - 1, "center_x": CANVAS // 2,
                             "label": lab}
    for fn, lab in FACES:
        img = draw_face(fn)
        img.save(out / "faces" / f"{fn}.png")
        parts["faces"][fn] = img
        kit["faces"][fn] = {"file": f"faces/{fn}.png", "anchor": [FAX, FAY], "label": lab}
    for an, lab, default, img, anchor in (
            ("monocle", "Kính một tròng", True, draw_monocle(), (30, 90)),
            ("hat", "Mũ phớt", False, draw_hat(), (180, 210))):
        img.save(out / "accessories" / f"{an}.png")
        parts["acc"][an] = {"img": img, "anchor": anchor}
        kit["accessories"][an] = {"file": f"accessories/{an}.png", "anchor": list(anchor), "label": lab,
                                  "default": default}
    for pn, bn, fn, lab in PRESETS:
        kit["presets"][pn] = {"body": bn, "face": fn, "label": lab}
    kit["default"] = {"body": "stand", "face": "happy"}
    (out / "kit.json").write_text(json.dumps(kit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_sheets:
        sheets(out, parts)
    print("ok", out)


if __name__ == "__main__":
    main()
