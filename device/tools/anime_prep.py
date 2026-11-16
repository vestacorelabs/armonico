#!/usr/bin/env python3
"""anime_prep.py - the anime theme's pictures, made once from the Gemini kit, on a computer and not on the Pi.

    python3 device/tools/anime_prep.py <armonico_anime_kit folder>

Reads the approved pictures of the kit (1_approved and 2_partial) and writes the files the device loads as
they are, into device/ui/data/anime:

  * bg_02 .. bg_08 (no bg_07: the night is bg_08 with the Moon drawn in code; no bg_01: its lens flare), the
    rooftops for the hours, without the TV antenna cut by the right edge and without drawn stars (sky_clean)
  * room_07_day/evening/night, room_08 .. room_11, the room with the girl
  * close_face_*, close_hands_*, the close-ups of pose 7, cut from the full-size pictures so they stay sharp
  * face_08 .. face_11, the faces for the cut-ins
  * fx_lines, fx_golden, eyecatch
  * g08_* and g08face_*, pose 8 drawn again frame by frame (blinks, looks, the happy reaction with its sparkles),
    from the edits in 1_approved/girl08, pasted onto 08 where they changed and moved onto room_08's lines
  * cat_0..3, curtain_0..2 and the eight manpu symbols, with the green (#00FF00) keyed out to transparency
  * logo_light.png, the Armonico logo for a light background, from the SVG (when cairosvg is installed)

Everything is cut and scaled here, so the device only loads files: no keying, no cropping and no big
scaling on the Pi. Needs Pillow, numpy and OpenCV (pip install pillow numpy opencv-python-headless).

The pictures of one place are brought onto the same lines first (register): Gemini drew some of them a little
taller or moved by a few pixels (05, 06 and 08 by about 1 percent), and two pictures that blend on the screen
must have every roof and wire in the same place, or the blend shows them twice. The rooftops are matched to
02 (the clean plate), the room to pose 7 by day, on the room only (the girl moves).
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "ui" / "data" / "anime"
LOGO_SVG = Path(__file__).resolve().parent.parent / "ui" / "data" / "logos" / "armonico-logo-light.svg"
WIDE = (1280, 720)          # the full-screen pictures: room for the camera to move on the 800x480 screen
JPEG_Q = 86

# The pictures with light in windows have them all dark here (made again in Gemini): the windows light one by one
# in code (ui/anime.py windows), from 08_night_windows_on
BACKGROUNDS = {"bg_02": "02_clean_plate", "bg_03": "03_noon", "bg_04": "04_golden_hour",
               "bg_05": "05_sunset_windows_off", "bg_06": "06_blue_hour_windows_off", "bg_08": "08_night_windows_off"}
WINDOWS_ON = "08_night_windows_on"
ROOMS = {"room_07_day": "character/07_accompany", "room_07_evening": "character/07_accompany_evening",
         "room_07_night": "character/07_accompany_night", "room_08": "character/08_listening",
         "room_09": "partial/09_surprised", "room_10": "partial/10_happy", "room_11": "character/11_oops"}
# boxes as fractions of the picture (x0, y0, x1, y1), 16:9
CLOSE_FACE = (0.40, 0.06, 0.72, 0.38)
CLOSE_HANDS = (0.20, 0.56, 0.52, 0.88)
FACE = {"room_08": (0.47, 0.11, 0.71, 0.39), "room_09": (0.47, 0.09, 0.71, 0.37),
        "room_10": (0.47, 0.11, 0.71, 0.39), "room_11": (0.47, 0.11, 0.71, 0.39)}
FACE_SIZE = (384, 240)
# Pose 8 drawn again in Gemini, frame by frame, for real animation (1_approved/girl08 in the kit). Two kinds:
# whole pictures edited from 08 (the happy reaction, where the head moves), and edits of a square crop of the face
# (the eyes, and the sparkles over the happy peak), of which only the part that changed is pasted back.
# the reactions as drawn again (14.11.2026): a wrong key is a small encouraging nod, a part learned is joy with the
# hands together, thinking is a finger on the chin; each with its in-between
GIRL08_FULL = {"in": "happy_in", "peak": "happy_peak", "oops_in": "oops2_in", "oops_peak": "oops2_peak",
               "proud_in": "proud_in", "proud": "proud_peak", "think_in": "think2_in", "think": "think2_peak"}
GIRL08_EYES = {"relaxed": "eyes_relaxed", "wide": "eyes_wide", "down": "eyes_down", "side": "eyes_side",
               "half": "blink_half", "closed": "blink_closed"}
GIRL08_SPARKS = {"s1": "spark_1", "s2": "spark_2", "s3": "spark_3"}
# symbols Gemini drew over a whole reaction picture: (the drawing it is over, the edit, where the symbol may be)
SYM_AREAS = [(0.60, 0.0, 0.84, 0.40), (0.42, 0.18, 0.52, 0.42)]   # above-right of her head; left of her face
# (the drawing it is over, the edit, how many of its marks from the left: None for all, the areas taken).
# Every stage of a symbol comes from one drawing, shown part by part: Gemini draws it differently every time.
# The surprise: the lines beside her face flash first, then the "!" joins them. The dots: one, two, three.
GIRL08_SYMBOLS = {}   # the "!" and the dots were drawn over the reactions that were replaced: none over the new ones
FACE_CROP08 = (0.4596, 0.0396, 0.7391, 0.5403)        # the square crop of her face that the edits were made on
FACE_G08 = (0.46, 0.07, 0.76, 0.406)                  # the face for the corner cut-in, room for the sparkles
MANPU = ("drop", "sparkle", "question", "bang", "anger", "bubble", "surprise", "dots")   # the sheet, row by row


def _edges(gray, w, h, skip=None):
    a = cv2.resize(gray, (w // 2, h // 2))
    a = cv2.Canny(cv2.GaussianBlur(a, (5, 5), 0), 40, 120)
    a = cv2.GaussianBlur(a.astype(np.float32), (9, 9), 0)
    if skip:                                     # a band left out of the match, as fractions (x0, x1, y0, y1)
        x0, x1, y0, y1 = skip
        a[int(y0 * a.shape[0]):int(y1 * a.shape[0]), int(x0 * a.shape[1]):int(x1 * a.shape[1])] = 0
    return a


def register_m(im, ref, skip=None):
    """The affine move that brings im onto ref's lines (OpenCV ECC), with im at ref's size; None if not found."""
    w, h = ref.size
    src = np.asarray(im.convert("RGB").resize((w, h), Image.LANCZOS))
    r = _edges(cv2.cvtColor(np.asarray(ref.convert("RGB")), cv2.COLOR_RGB2GRAY), w, h, skip)
    a = _edges(cv2.cvtColor(src, cv2.COLOR_RGB2GRAY), w, h, skip)
    m = np.eye(2, 3, dtype=np.float32)
    try:
        cv2.findTransformECC(r, a, m, cv2.MOTION_AFFINE,
                             (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    except cv2.error as e:
        print(f"Not registered ({e}): kept as it is")
        return src, None
    m[:, 2] *= 2                                  # found at half size
    return src, m


def warp(src, m):
    if m is None:
        return Image.fromarray(src)
    h, w = src.shape[:2]
    return Image.fromarray(cv2.warpAffine(src, m, (w, h), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                          borderMode=cv2.BORDER_REPLICATE))


def register(im, ref, skip=None):
    """im moved, scaled and sheared onto ref's lines, at ref's size."""
    return warp(*register_m(im, ref, skip))


def paste_changed(base, edit, keep):
    """Pastes into base (an RGB crop) only what changed in edit inside keep (x0, y0, x1, y1, fractions of the crop),
    with a soft edge: the rest of base stays exactly as it was. edit is first moved onto base."""
    b = np.asarray(base)
    side = b.shape[1]
    g = np.asarray(edit.convert("RGB").resize((side, b.shape[0]), Image.LANCZOS))
    x0, y0, x1, y1 = (int(v * side) for v in keep)

    def edges(x):
        y = cv2.cvtColor(x, cv2.COLOR_RGB2GRAY)
        y = cv2.GaussianBlur(cv2.Canny(cv2.GaussianBlur(y, (5, 5), 0), 40, 120).astype(np.float32), (7, 7), 0)
        y[y0:y1, x0:x1] = 0                       # what changed is not used to match
        return y
    m = np.eye(2, 3, dtype=np.float32)
    try:
        cv2.findTransformECC(edges(b), edges(g), m, cv2.MOTION_AFFINE, (3, 300, 1e-6), None, 5)
        g = cv2.warpAffine(g, m, (side, b.shape[0]), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_REPLICATE)
    except cv2.error:
        pass
    d = cv2.absdiff(cv2.GaussianBlur(b, (7, 7), 0), cv2.GaussianBlur(g, (7, 7), 0)).max(axis=2)
    mask = np.zeros(d.shape, np.uint8)
    mask[y0:y1, x0:x1] = ((d[y0:y1, x0:x1] > 16) * 255).astype(np.uint8)
    k = max(3, side // 48) | 1
    mask = cv2.GaussianBlur(cv2.dilate(mask, np.ones((k, k), np.uint8)), (k * 2 + 1, k * 2 + 1), 0)
    a = (mask.astype(np.float32) / 255)[..., None]
    return Image.fromarray((g * a + b * (1 - a)).astype(np.uint8))


def paste_symbol(base, edit, areas, marks=None):
    """A symbol Gemini drew on a whole picture: only what changed inside areas (fractions) is pasted on base;
    with marks, only that many of its separate marks, counted from the left."""
    b = np.asarray(base)
    h, w = b.shape[:2]
    g = np.asarray(register(edit, base, skip=(0.42, 0.84, 0, 0.45)))
    d = cv2.absdiff(cv2.GaussianBlur(b, (7, 7), 0), cv2.GaussianBlur(g, (7, 7), 0)).max(axis=2)
    keep = np.zeros(d.shape, bool)
    for x0, y0, x1, y1 in areas:
        keep[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = True
    mask = ((d > 16) & keep).astype(np.uint8) * 255
    if marks:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((9, 9), np.uint8)))
        top = max([stats[i, cv2.CC_STAT_AREA] for i in range(1, n)] or [0])
        big = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.4 * top]   # the marks, not small retouches
        big.sort(key=lambda i: stats[i, cv2.CC_STAT_LEFT])
        mask = (np.isin(labels, big[:marks]) & (mask > 0)).astype(np.uint8) * 255
    k = max(3, w // 300) | 1
    mask = cv2.GaussianBlur(cv2.dilate(mask, np.ones((k, k), np.uint8)), (k * 2 + 1, k * 2 + 1), 0)
    a = (mask.astype(np.float32) / 255)[..., None]
    return Image.fromarray((g * a + b * (1 - a)).astype(np.uint8))


def girl08(kit, room):
    """The drawings of pose 8 for real animation: g08_<name> (whole picture, like room_08) and g08face_<name>
    (the face for the cut-in), all on room_08's lines exactly. Skipped when the kit has no girl08 folder."""
    src = kit / "1_approved" / "girl08"
    if not src.is_dir():
        print("No 1_approved/girl08 in the kit: pose 8 stays one drawing")
        return
    base = Image.open(find(kit, ROOMS["room_08"])).convert("RGB")
    w, h = base.size
    _, m08 = register_m(base, room, skip=(0.45, 0.8, 0, 1))            # the same move as room_08 gets
    cb = tuple(round(v * s) for v, s in zip(FACE_CROP08, (w, h, w, h)))
    drawings = {"base": base}
    for name, f in GIRL08_FULL.items():
        drawings[name] = register(Image.open(src / f"{f}.jpg"), base, skip=(0.45, 0.8, 0, 1))
    for name, f in GIRL08_EYES.items():
        im = base.copy()
        im.paste(paste_changed(base.crop(cb), Image.open(src / f"{f}.jpg"), (0.17, 0.28, 0.61, 0.5)), cb[:2])
        drawings[name] = im
    for name, f in GIRL08_SPARKS.items():
        im = drawings["peak"].copy()
        im.paste(paste_changed(im.crop(cb), Image.open(src / f"{f}.jpg"), (0.72, 0.0, 1.0, 0.75)), cb[:2])
        drawings[name] = im
    for name, (over, f, marks, areas) in GIRL08_SYMBOLS.items():
        if over in drawings and (src / f"{f}.jpg").exists():
            drawings[name] = paste_symbol(drawings[over], Image.open(src / f"{f}.jpg"), areas, marks)
    for name, im in drawings.items():
        im = warp(np.asarray(im), m08)
        save_jpg(cover(im, WIDE), f"g08_{name}")
        save_jpg(box(im, FACE_G08, FACE_SIZE), f"g08face_{name}")


def find(kit, rel):
    """A kit picture by its name without the extension, in 1_approved or 2_partial."""
    folder, name = rel.split("/") if "/" in rel else ("backgrounds", rel)
    roots = [kit / "2_partial"] if folder == "partial" else [kit / "1_approved" / folder]
    for root in roots:
        for ext in (".jpg", ".png", ".jpeg"):
            p = root / (name + ext)
            if p.exists():
                return p
    raise SystemExit(f"Missing in the kit: {rel}")


def cover(im, size):
    """Scaled to cover size, the middle kept."""
    w, h = im.size
    k = max(size[0] / w, size[1] / h)
    im = im.resize((max(size[0], round(w * k)), max(size[1], round(h * k))), Image.LANCZOS)
    x, y = (im.width - size[0]) // 2, (im.height - size[1]) // 2
    return im.crop((x, y, x + size[0], y + size[1]))

def sky_clean(im, name):
    """The rooftops' sky without what does not belong to it on the screen (at WIDE, 1280x720): the TV antenna at the
    right edge, which the screen cut in half over the date, and the stars drawn in the night pictures, which stayed
    in one place: the city's real stars are drawn in code (ui/anime.py sky_over). Both are painted over from the
    sky around them."""
    a = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
    roi = a[40:228, 1140:]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    m = ((cv2.medianBlur(g, 41).astype(int) - g) > 14).astype(np.uint8) * 255
    a[40:228, 1140:] = cv2.inpaint(roi, cv2.dilate(m, np.ones((5, 5), np.uint8)), 6, cv2.INPAINT_TELEA)
    if name in ("bg_06", "bg_08"):
        g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        m = ((g.astype(int) - cv2.medianBlur(g, 11)) > 22).astype(np.uint8)
        m[228:] = 0
        k, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        dots = np.zeros_like(m)
        for i in range(1, k):
            if st[i, 4] <= 25 and st[i, 2] <= 7 and st[i, 3] <= 7:
                dots[lab == i] = 255
        a = cv2.inpaint(a, cv2.dilate(dots, np.ones((5, 5), np.uint8)), 4, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(a, cv2.COLOR_BGR2RGB))

def windows(lit, dark):
    """Every lit window of the night, apart, from the night with every window lit and the same night with none (both
    at WIDE, on the rooftops' lines): windows_lit.png, the lit windows packed in rows, and windows.json, where each
    one goes on the picture ([x, y, w, h, x in the pack, y in the pack, its lit pixels]). A window's railing and
    curtain stay from the picture under it: only its light is kept, by how much brighter it is lit."""
    import json
    lit = cv2.cvtColor(np.array(lit), cv2.COLOR_RGB2BGR).astype(np.float32)
    dark = cv2.cvtColor(np.array(dark), cv2.COLOR_RGB2BGR).astype(np.float32)

    def lum(a):
        return a[..., 2] * 0.3 + a[..., 1] * 0.59 + a[..., 0] * 0.11
    d = cv2.GaussianBlur(lum(lit) - lum(dark), (3, 3), 0)
    m = ((d > 22) & ((lit[..., 2] - lit[..., 0]) > 10)).astype(np.uint8)
    m[:235] = 0                                   # the sky: no windows
    k, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(m, np.ones((5, 5), np.uint8)), 8)
    alpha = np.clip((d - 10) / 45, 0, 1)
    wins, tiles = [], []
    for i in range(1, k):
        x, y, w, h, _a = st[i]
        lit_px = int(m[lab == i].sum())
        if lit_px < 4:
            continue
        x0, y0, x1, y1 = max(0, x - 3), max(0, y - 3), min(WIDE[0], x + w + 3), min(WIDE[1], y + h + 3)
        own = cv2.GaussianBlur(cv2.dilate((lab[y0:y1, x0:x1] == i).astype(np.uint8), np.ones((5, 5), np.uint8))
                               .astype(np.float32), (5, 5), 0)
        tiles.append(np.dstack([lit[y0:y1, x0:x1], alpha[y0:y1, x0:x1, None] * own[..., None] * 255]).astype(np.uint8))
        wins.append([int(x0), int(y0), int(x1 - x0), int(y1 - y0), lit_px])
    width, ax, ay, row, place = 1024, 0, 0, 0, []
    for t in tiles:
        h, w = t.shape[:2]
        if ax + w > width:
            ax, ay, row = 0, ay + row + 1, 0
        place.append((ax, ay))
        ax, row = ax + w + 1, max(row, h)
    atlas = np.zeros((ay + row + 1, width, 4), np.uint8)
    for t, (px, py) in zip(tiles, place):
        atlas[py:py + t.shape[0], px:px + t.shape[1]] = t
    cv2.imwrite(str(OUT / "windows_lit.png"), atlas)
    rows = [[x, y, w, h, int(px), int(py), n] for (x, y, w, h, n), (px, py) in zip(wins, place)]
    (OUT / "windows.json").write_text(json.dumps({"size": list(WIDE), "windows": rows}, separators=(",", ":")))




def box(im, b, size):
    w, h = im.size
    return im.crop((round(b[0] * w), round(b[1] * h), round(b[2] * w), round(b[3] * h))).resize(size, Image.LANCZOS)


def key_green(im):
    """RGBA with the green screen keyed out: alpha from how much greener than red and blue a pixel is,
    a soft edge, and the green taken out of the edge (despill)."""
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    over = g - np.maximum(r, b)
    alpha = np.clip(1.0 - (over - 40.0) / 60.0, 0.0, 1.0)
    alpha[(g > 150) & (over > 100)] = 0.0
    g2 = np.minimum(g, np.maximum(r, b) + 10)       # despill: no green halo on the edges
    out = np.dstack([r, g2, b, alpha * 255]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def trim(im, pad=4):
    bb = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bb:
        return im
    x0, y0, x1, y1 = bb
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))


def runs(mask, gap):
    """The spans along an axis where the mask has something, joined across gaps shorter than gap."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return out


def green_area(im):
    """The box of the green screen inside a screenshot (the cat came as a picture of the chat window)."""
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    g = (a[..., 1] > 180) & (a[..., 0] < 120) & (a[..., 2] < 120)
    ys, xs = np.flatnonzero(g.any(axis=1)), np.flatnonzero(g.any(axis=0))
    return im.crop((xs[0] + 36, ys[0] + 36, xs[-1] - 36, ys[-1] - 36))   # inside the rounded corners


def frames_by_columns(keyed, n, gap=6):
    a = np.asarray(keyed.getchannel("A")) > 24
    spans = runs(a.any(axis=0), gap=gap)
    spans = sorted(spans, key=lambda s: s[1] - s[0], reverse=True)[:n]
    return [trim(keyed.crop((x0, 0, x1, keyed.height))) for x0, x1 in sorted(spans)]


def save_jpg(im, name):
    im.convert("RGB").save(OUT / f"{name}.jpg", quality=JPEG_Q, optimize=True, progressive=True)


def save_png(im, name, height=None):
    if height and im.height > height:
        im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    im.save(OUT / f"{name}.png", optimize=True)


def main(kit):
    kit = Path(kit)
    OUT.mkdir(parents=True, exist_ok=True)
    roofs = Image.open(find(kit, BACKGROUNDS["bg_02"])).convert("RGB")
    for name, rel in BACKGROUNDS.items():
        im = Image.open(find(kit, rel)).convert("RGB")
        if name != "bg_02":
            im = register(im, roofs, skip=(0, 1, 0, 0.3))        # the sky (clouds, stars) is not matched
        save_jpg(sky_clean(cover(im, WIDE), name), name)
    on = cover(register(Image.open(find(kit, WINDOWS_ON)).convert("RGB"), roofs, skip=(0, 1, 0, 0.3)), WIDE)
    windows(on, Image.open(OUT / "bg_08.jpg").convert("RGB"))
    room = Image.open(find(kit, ROOMS["room_07_day"])).convert("RGB")
    for name, rel in ROOMS.items():
        im = Image.open(find(kit, rel)).convert("RGB")
        if name != "room_07_day":
            im = register(im, room, skip=(0.45, 0.8, 0, 1))      # the girl is left out: she moves
        save_jpg(cover(im, WIDE), name)
        if name.startswith("room_07_"):
            when = name.rsplit("_", 1)[1]
            save_jpg(box(im, CLOSE_FACE, (1066, 600)), f"close_face_{when}")
            save_jpg(box(im, CLOSE_HANDS, (1066, 600)), f"close_hands_{when}")
        if name in FACE:
            save_jpg(box(im, FACE[name], FACE_SIZE), "face_" + name.split("_")[1])
    girl08(kit, room)
    layers = kit / "1_approved" / "layers"
    save_jpg(cover(Image.open(layers / "bg_concentration_lines.jpg"), WIDE), "fx_lines")
    save_jpg(cover(Image.open(layers / "bg_golden_streaks.jpg"), WIDE), "fx_golden")
    save_jpg(cover(Image.open(layers / "eyecatch.jpg"), WIDE), "eyecatch")

    cat = key_green(green_area(Image.open(layers / "cat_4frames_green_screenshot.png")))
    for i, fr in enumerate(frames_by_columns(cat, 4)):
        save_png(fr, f"cat_{i}", height=140)
    curtain = key_green(Image.open(layers / "curtain_3frames_green.jpg"))
    for i, fr in enumerate(frames_by_columns(curtain, 3)):
        save_png(fr, f"curtain_{i}", height=420)
    sheet = key_green(Image.open(layers / "manpu_symbols_green.jpg"))
    cw, ch = sheet.width / 4, sheet.height / 2
    for i, name in enumerate(MANPU):
        x, y = (i % 4) * cw, (i // 4) * ch
        save_png(trim(sheet.crop((round(x), round(y), round(x + cw), round(y + ch)))), "manpu_" + name, height=160)

    try:
        import cairosvg
        cairosvg.svg2png(url=str(LOGO_SVG), write_to=str(OUT / "logo_light.png"), output_width=852)
    except Exception as e:                      # the eyecatch falls back to the icon and the name in text
        print(f"logo_light.png not made ({e})")
    print(f"Done: {len(list(OUT.iterdir()))} files in {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
