"""The anime theme's manga panel over a lesson (ui/anime.py draw_panel), made once from the kit, like anime_prep.py.

    python3 -I armonico/device/tools/manga_panel.py armonico_anime_kit

Pose 8 (the girl listening) as a printed manga page that does not move: two frames with a white slanted gutter
between them, her hands on the keys on the left and her face on the right, in ink lines and screentone. The
bottom edge is cut on a slant, low on her side, and outlined in ink. Writes ui/data/anime/manga_panel.png
(800 x 220, transparent under the slant). Needs numpy, opencv-python and Pillow on the machine that runs it.
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "ui" / "data" / "anime" / "manga_panel.png"
W, H = 800, 220                   # 168 cut her head at the top: the taller page shows it whole (demo B)
LEFT_LOW = 192                    # the slant: the bottom edge at the left, rising from H on her side


def tone(h, w, period, r, angle=45):
    """A screentone sheet: round dots of radius r on a grid rotated by angle."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.deg2rad(angle)
    u = (x * np.cos(a) + y * np.sin(a)) / period
    v = (-x * np.sin(a) + y * np.cos(a)) / period
    du, dv = (u - np.round(u)) * period, (v - np.round(v)) * period
    return (du * du + dv * dv) <= r * r


def manga(path, size, box, levels, radii, period=5.0, line=-0.008):
    im = Image.open(path).convert("RGB")
    w0, h0 = im.size
    im = im.crop(tuple(int(v * (w0 if i % 2 == 0 else h0)) for i, v in enumerate(box)))
    g = cv2.cvtColor(np.array(im.resize(size, Image.LANCZOS)), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    g = cv2.bilateralFilter(g, 7, 0.12, 5)
    h, w = g.shape
    lines = (cv2.GaussianBlur(g, (0, 0), 0.9) - 0.985 * cv2.GaussianBlur(g, (0, 0), 1.6)) < line
    out = np.ones((h, w), bool)
    for lv, r in zip(levels, radii):
        out[g < lv] = ~tone(h, w, period, r)[g < lv]
    out[g < levels[3]] = False
    out[lines] = False
    return Image.fromarray((out * 255).astype(np.uint8)).convert("L")


def main(kit):
    src = Path(kit) / "1_approved" / "character" / "08_listening.jpg"
    # Each frame cut at its own shape, so nothing is stretched: her whole face from the hair to the chin on the
    # right (it was cut under the mouth), and her hands with the keys on the left
    hands = manga(src, (440, H), (0.20, 0.55, 0.70, 1.0), (0.58, 0.38, 0.22, 0.11), (0.9, 1.5, 2.3))
    face = manga(src, (380, H), (0.35, 0.0, 0.843, 0.52), (0.33, 0.24, 0.15, 0.08), (0.7, 1.3, 2.1))
    page = Image.new("L", (W, H), 255)
    page.paste(face, (W - 380, 0))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (432, 0), (392, H), (0, H)], fill=255)
    left = Image.new("L", (W, H), 255)
    left.paste(hands, (0, 0))
    page.paste(left, (0, 0), mask)
    d = ImageDraw.Draw(page)
    d.polygon([(432, -2), (450, -2), (410, H + 2), (392, H + 2)], fill=255)      # the gutter
    d.line([(432, 0), (392, H)], fill=0, width=3)
    d.line([(450, 0), (410, H)], fill=0, width=3)
    rgba = Image.merge("RGBA", (page, page, page, Image.new("L", (W, H), 255)))
    cut = Image.new("L", (W, H), 0)
    ImageDraw.Draw(cut).polygon([(0, 0), (W, 0), (W, H), (0, LEFT_LOW)], fill=255)
    rgba.putalpha(cut)
    ImageDraw.Draw(rgba).line([(0, LEFT_LOW - 2), (W, H - 2)], fill=(29, 26, 34, 255), width=4)   # the ink edge
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(OUT, optimize=True)
    print(f"Done: {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
