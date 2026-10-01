"""canvas.py - one drawing surface for every screen of the Armonico Pi device.

The screens are laid out on an 800x480 plan, and the canvas draws that plan straight at the
screen's own resolution: from a 3.5" 480x320 panel to a 24" 1920x1080 monitor. Nothing is drawn
small and stretched afterwards, so text and lines stay sharp at every size. A screen that is
taller or wider than 5:3 gets the plan centered, and backgrounds use the full area (vx, vy, vw, vh).

Every shape goes through one affine transform, the same idea as a browser canvas: translate,
scale and rotate work for the characters, and Hebrew mirrors the whole layout right to left.
Inside `with cv.physical():` nothing is mirrored: the buttons under the screen, the keys and the
sky are where they are in the real world, in both languages.

Hebrew text: pygame draws letters left to right, so a line is put into display order first
(python-bidi), and Hebrew and Latin each use the Heebo file that has their letters.
"""
import itertools
import math
import re
import sys
import unicodedata
from contextlib import contextmanager
from pathlib import Path

W, H = 800, 480
CACHE_MAX = 1500        # rendered lines kept (a line is 5-30 KB: at most some 25-40 MB); over it the oldest go
CACHE_DROP = 100        # ... this many at a time
# Characters always drawn with DejaVu (Heebo has none, or a different look). Any other character that
# Heebo lacks goes to DejaVu too when DejaVu has it (Fonts.route); app.py adds to this set.
SYMBOLS = set("○✓✔✕▲▼☰♪▶◀◂▸‖→←↑↓■⚠⬆⬑●◐◓◑◒·…⇧⌫›")
DEJAVU_DIR = "/usr/share/fonts/truetype/dejavu"
DEJAVU_FILES = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf")      # Bold first; the plain file if Bold is absent
_warned = set()


def dejavu_path():
    for n in DEJAVU_FILES:
        if (Path(DEJAVU_DIR) / n).exists():
            return str(Path(DEJAVU_DIR) / n)
    return None


def _joiner(ch):
    """Variation selectors, zero-width joiners and keycap marks: only ever parts of an emoji."""
    o = ord(ch)
    return 0xFE00 <= o <= 0xFE0F or o in (0x200D, 0x20E3) or 0xE0000 <= o <= 0xE01EF


def _dropped(ch):
    """Invisible characters (controls, format marks, combining marks no font has): left out quietly. Emoji are
    not here: they are drawn by DejaVu where it has them, as before, and are a "?" where no font does."""
    return unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Me", "Mn", "Zl", "Zp")


HEB = re.compile(r"[֐-׿]")

try:                                            # python3-bidi on Raspberry Pi OS, python-bidi from pip
    from bidi.algorithm import get_display
except Exception:                               # pragma: no cover - without it Hebrew comes out reversed
    def get_display(s, base_dir=None):
        return s[::-1] if HEB.search(s) else s


class Fonts:
    """Heebo in two weights for words, DejaVu for the symbols Heebo does not have."""

    def __init__(self, pg, font_dir):
        self.pg, self.dir, self.cache = pg, Path(font_dir), {}
        self._route = {}
        self._dv = dejavu_path()
        self._check()

    def _check(self):
        """One line on stderr (the journal) per missing set: without these files pygame uses its own
        font and Hebrew, symbols and other scripts come out as squares."""
        if not (self.dir / "Heebo-Hebrew-Medium.ttf").exists() and "heebo" not in _warned:
            _warned.add("heebo")
            print(f"armonico: font file {self.dir / 'Heebo-Hebrew-Medium.ttf'} is missing: Hebrew and text will show "
                  f"as squares (run 'armonico_pi.py prepare')", file=sys.stderr, flush=True)
        if not self._dv and "dejavu" not in _warned:
            _warned.add("dejavu")
            print(f"armonico: DejaVuSans-Bold.ttf not found in {DEJAVU_DIR}: symbols, arrows and Cyrillic/Greek names "
                  f"will be left out or shown as '?' (apt install fonts-dejavu-core)", file=sys.stderr, flush=True)

    def _cmap(self, path):
        """The set of code points a TrueType file has a glyph for, read once (None: unreadable). pygame's
        own metrics() answers with the "missing" box for an absent glyph, so it cannot tell."""
        if path not in self.cache:
            try:
                from fontTools.ttLib import TTFont
                self.cache[path] = frozenset(TTFont(path, lazy=True).getBestCmap())
            except Exception:
                self.cache[path] = None
        return self.cache[path]

    def _has(self, ch, symbol):
        path = self._dv if symbol else self._file(False, False)
        if path is None:                            # no file: pygame's own font, whose answer is all there is
            return not symbol and ord(ch) < 128
        cm = self._cmap(path)
        if cm is not None:
            return ord(ch) in cm
        try:
            m = self.get(16, False, False, symbol).metrics(ch)
        except Exception:
            return False
        return bool(m) and m[0] is not None

    def route(self, ch):
        """Which font draws this character: "sym" (DejaVu), "lat" (Heebo), "heb", None (left out), or
        ("lat", "?") for a character no font has. The one place where this is decided; kept per character."""
        r = self._route.get(ch, 0)
        if r != 0:
            return r
        if ch in SYMBOLS and self._has(ch, True):
            r = "sym"
        elif HEB.match(ch):
            r = "heb"
        elif ch in " \u00a0":
            r = "lat"
        elif _joiner(ch):
            r = None
        elif self._has(ch, False):
            r = "lat"
        elif _dropped(ch):
            r = None
        elif self._has(ch, True):
            r = "sym"
        else:
            r = ("lat", "?")
        self._route[ch] = r
        return r

    def _file(self, bold, hebrew):
        names = (["Heebo-Hebrew-Bold.ttf"] if hebrew and bold else ["Heebo-Hebrew-Medium.ttf"] if hebrew else []) + \
                (["Heebo-Bold.ttf", "Heebo-ExtraBold.ttf"] if bold else ["Heebo-Medium.ttf", "Heebo-Regular.ttf"])
        for n in names:
            if (self.dir / n).exists():
                return str(self.dir / n)
        return None

    def get(self, px, bold=False, hebrew=False, symbol=False):
        px = max(6, int(round(px)))
        key = (px, bold, hebrew, symbol)
        if key not in self.cache:
            if symbol:
                path = self._dv
            else:
                path = self._file(bold, hebrew)
            self.cache[key] = self.pg.font.Font(path, px)
        return self.cache[key]


class Canvas:
    def __init__(self, pg, surface, fonts):
        self.pg, self.fonts = pg, fonts
        try:
            # pygame does not load gfxdraw by itself, and every panel is drawn with it: without this
            # line the first panel on the rest screen ended the program when the sky theme was not on
            import pygame.gfxdraw  # noqa: F401
        except ImportError:
            pass
        self.zoom = 1.0                 # the interface size setting (0.8 to 1.5): the text grows, the plan stays
        self.rtl = False                # Hebrew: the plan is mirrored, except inside physical()
        self._fix = 0
        self.m = [1, 0, 0, 1, 0, 0]      # the current transform: x' = a x + c y + e, y' = b x + d y + f
        self.stack = []
        self._cache = {}
        self.rec = None                 # a list while a still layer is drawn: the pixel boxes of what lies on top
        self.sig_on = False             # on: every line, shape and picture drawn is folded into self.sig (see app.run:
        self.sig = 0                    # a frame whose sig equals the last one's is the same picture, bar the sky's twinkle)
        self._last_key = None
        self.set_surface(surface)

    # ---- the plan on the real screen
    def set_surface(self, surface):
        self.surf = surface
        sw, sh = surface.get_size()
        self.scale = min(sw / W, sh / H)
        self.ox, self.oy = (sw - W * self.scale) / 2, (sh - H * self.scale) / 2
        # the full screen in plan units, for backgrounds that must cover it all
        self.vx, self.vy = -self.ox / self.scale, -self.oy / self.scale
        self.vw, self.vh = sw / self.scale, sh / self.scale

    def to_plan(self, px, py):
        """A touch or click on the real screen, in plan units (before mirroring)."""
        return (px - self.ox) / self.scale, (py - self.oy) / self.scale

    @property
    def mirrored(self):
        return self.rtl and not self._fix

    @contextmanager
    def physical(self):
        self._fix += 1
        try:
            yield
        finally:
            self._fix -= 1

    def mx(self, x):
        """A plan x as it lands on the screen: mirrored in Hebrew."""
        return W - x if self.mirrored else x

    # ---- transform
    def save(self):
        self.stack.append(list(self.m))

    def restore(self):
        self.m = self.stack.pop()

    def _mul(self, a2, b2, c2, d2, e2, f2):
        a, b, c, d, e, f = self.m
        self.m = [a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f]

    def translate(self, x, y):
        self._mul(1, 0, 0, 1, x, y)

    def scale_by(self, sx, sy=None):
        self._mul(sx, 0, 0, sx if sy is None else sy, 0, 0)

    def rotate(self, ang):
        c, s = math.cos(ang), math.sin(ang)
        self._mul(c, s, -s, c, 0, 0)

    @contextmanager
    def at(self, x, y, s=1.0, rot=0.0):
        """Draw a figure around (x, y): the characters use this, with their own size and tilt."""
        self.save()
        self.translate(x, y)
        if rot:
            self.rotate(rot)
        if s != 1.0:
            self.scale_by(s)
        try:
            yield
        finally:
            self.restore()

    def P(self, x, y):
        """A point through the transform, then the mirror, then onto the real pixels."""
        a, b, c, d, e, f = self.m
        X, Y = a * x + c * y + e, b * x + d * y + f
        if self.mirrored:
            X = W - X
        return (self.ox + X * self.scale, self.oy + Y * self.scale)

    def k(self):
        """How many real pixels one plan unit is, at the current transform."""
        a, b = self.m[0], self.m[1]
        return math.hypot(a, b) * self.scale

    # ---- color with alpha
    def _poly(self, pts, color, width=0):
        self._polys([pts], color, width)

    def _polys(self, shapes, color, width=0):
        """Opaque colors draw straight on the screen. With alpha, all the shapes are drawn opaque on
        a copy of the screen under them, and the copy is blended back once at the color's alpha:
        outside the shapes the copy equals the screen, so only the shapes change. A plain copy blends
        with one alpha for the whole layer, which SDL does several times faster than a layer with an
        alpha per pixel (on a Pi 4 that layer was most of the time of a frame, and the screens crawled
        on the 24"). The pieces of one line or arc share the layer, so their joints are not darker."""
        shapes = [p for p in shapes if len(p) >= 2]
        if not shapes or color is None:
            return
        if self.sig_on:
            a, z = shapes[0], shapes[-1]
            self.sig = hash((self.sig, tuple(color), width, len(shapes), len(a), a[0], a[len(a) // 2], z[-1]))
        gfx = self.pg.gfxdraw
        pad = int(width) + 2
        if len(color) == 4 and color[3] < 255:
            if color[3] <= 0:
                return
            sw, sh = self.surf.get_size()
            x0 = max(0, int(min(x for p in shapes for x, _ in p)) - pad)
            y0 = max(0, int(min(y for p in shapes for _, y in p)) - pad)
            x1 = min(sw, int(max(x for p in shapes for x, _ in p)) + pad)
            y1 = min(sh, int(max(y for p in shapes for _, y in p)) + pad)
            if x1 <= x0 or y1 <= y0:
                return
            tgt = self.surf.subsurface((x0, y0, x1 - x0, y1 - y0)).copy()
            col, off = tuple(color[:3]), (x0, y0)
        else:
            tgt, col, off = self.surf, color[:3], (0, 0)
        if self.rec is not None:
            self.rec.append((min(x for p in shapes for x, _ in p) - pad, min(y for p in shapes for _, y in p) - pad,
                             max(x for p in shapes for x, _ in p) + pad, max(y for p in shapes for _, y in p) + pad))
        for pts in shapes:
            ipts = [(int(round(x - off[0])), int(round(y - off[1]))) for x, y in pts]
            if width == 0:
                if len(ipts) >= 3:
                    gfx.filled_polygon(tgt, ipts, col)
                    gfx.aapolygon(tgt, ipts, col)
            else:
                self.pg.draw.lines(tgt, col, False, ipts, max(1, int(round(width))))
        if tgt is not self.surf:
            tgt.set_alpha(color[3])
            self.surf.blit(tgt, off)

    # ---- shapes, all in plan units
    def fill(self, color):
        """The whole real screen, past the plan's edges too."""
        self.surf.fill(color[:3])

    def rect(self, x, y, w, h, color, r=0, width=0):
        if r <= 0:
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        else:
            r = min(r, w / 2, h / 2)
            pts = []
            for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
                for i in range(7):
                    a = math.radians(a0 + i * 15)
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        if width:
            pts.append(pts[0])
            self._poly([self.P(*p) for p in pts], color, width * self.k())
        else:
            self._poly([self.P(*p) for p in pts], color)

    def ellipse(self, cx, cy, rx, ry, color, rot=0.0, width=0):
        self._poly(self._ellipse_pts(cx, cy, rx, ry, rot, bool(width)), color, width * self.k() if width else 0)

    def circle(self, cx, cy, r, color, width=0):
        self.ellipse(cx, cy, r, r, color, 0.0, width)

    def polygon(self, pts, color):
        self._poly([self.P(*p) for p in pts], color)

    def _ellipse_pts(self, cx, cy, rx, ry, rot=0.0, closed=False):
        n = max(16, min(90, int((rx + ry) * self.k() / 2)))
        c, s = math.cos(rot), math.sin(rot)
        pts = []
        for i in range(n + (1 if closed else 0)):
            t = 2 * math.pi * i / n
            ex, ey = rx * math.cos(t), ry * math.sin(t)
            pts.append(self.P(cx + ex * c - ey * s, cy + ex * s + ey * c))
        return pts

    def _line_shapes(self, x1, y1, x2, y2, width, start=True):
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / L * width / 2, dx / L * width / 2
        out = [[self.P(x1 + nx, y1 + ny), self.P(x2 + nx, y2 + ny), self.P(x2 - nx, y2 - ny), self.P(x1 - nx, y1 - ny)]]
        if start:
            out.append(self._ellipse_pts(x1, y1, width / 2, width / 2))
        out.append(self._ellipse_pts(x2, y2, width / 2, width / 2))
        return out

    def line(self, x1, y1, x2, y2, color, width=1.0):
        """A thick line with round ends, as a polygon so it tilts and mirrors with everything else."""
        self._polys(self._line_shapes(x1, y1, x2, y2, width), color)

    def polyline(self, pts, color, width=1.0):
        shapes = []
        for i, ((a, b), (c, d)) in enumerate(zip(pts, pts[1:])):
            shapes += self._line_shapes(a, b, c, d, width, start=i == 0)
        self._polys(shapes, color)

    def arc(self, cx, cy, r, a0, a1, color, width=2.0, ry=None):
        """An arc from angle a0 to a1 (radians, clockwise on screen, 0 to the right)."""
        n = max(6, int(abs(a1 - a0) * r * self.k() / 6))
        ry = r if ry is None else ry
        pts = [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        self.polyline(pts, color, width)


    def glow(self, cx, cy, r, color, alpha_in=0.9):
        """A soft round light: a few rings, faint outside, strong in the middle."""
        for i in range(10, 0, -1):
            a = int(255 * alpha_in * (1 - i / 10) ** 2 * 0.5)
            self.circle(cx, cy, r * i / 10, color[:3] + (a,))

    def image(self, img, x, y, w, h):
        """A picture into a plan box, scaled once, in real pixels."""
        (x0, y0), (x1, y1) = self.P(x, y), self.P(x + w, y + h)
        x0, x1 = sorted((x0, x1))
        size = (max(1, int(x1 - x0)), max(1, int(y1 - y0)))
        iw, ih = img.get_size()
        if self.sig_on:
            self.sig = hash((self.sig, id(img), size, int(x0), int(y0)))
        if abs(iw - size[0]) <= 2 and abs(ih - size[1]) <= 2:
            # a video frame already at the screen's size: copied, not scaled
            self.surf.blit(img, (int(x0 + (size[0] - iw) / 2), int(y0 + (size[1] - ih) / 2)))
            return
        # a still picture is drawn every frame: it is scaled once and kept until it or the box changes
        kept = getattr(self, "_img_kept", None)
        if kept is None or kept[0] is not img or kept[1] != size:
            kept = self._img_kept = (img, size, self.pg.transform.smoothscale(img, size))
        self.surf.blit(kept[2], (int(x0), int(y0)))

    # ---- text
    def _runs(self, s):
        out, cur, kind = [], "", None
        for ch in s:
            k = self.fonts.route(ch)
            if isinstance(k, tuple):                # in no font: a quiet "?"
                k, ch = k[0], "?"
            if k is None:                           # emoji parts, joiners: left out
                continue
            if k != kind and cur:
                out.append((cur, kind))
                cur = ""
            cur, kind = cur + ch, k
        if cur:
            out.append((cur, kind))
        # spaces and punctuation between two Hebrew runs stay Hebrew, so words keep their spacing
        return out

    def render(self, s, size, color, bold=False):
        """One line of text as an image at the real pixel size, in display order. Lines repeat
        from frame to frame, so each one is drawn once and kept (a few hundred at most)."""
        # the small lines (13 and under: the gray explanations, labels and notes) one size up: they were
        # hard to read on the 5". Measured the same way, so everything that fits text to a place still fits.
        if size <= 13:
            size += 1
        if self.zoom != 1.0:
            # the text of the screens grows with the setting; the big titles and clocks only by half of it
            size = size * (self.zoom if size <= 22 else 1 + (self.zoom - 1) / 2)
        key = (str(s), size, tuple(color), bold, self.scale)
        self._last_key = key
        img = self._cache.get(key)
        if img is None:
            if len(self._cache) >= CACHE_MAX:
                # the oldest lines go (the dict keeps insertion order), never the whole cache at once: all the
                # lines of a screen drawn again in one frame was a hitch of 100 ms and more on the Pi
                for old in list(itertools.islice(self._cache, CACHE_DROP)):
                    del self._cache[old]
            img = self._cache[key] = self._render(str(s), size, color, bold)
        return img

    def _render(self, s, size, color, bold):
        if HEB.search(s):
            s = get_display(s, base_dir="R")
        px = size * self.scale
        imgs = []                                   # (image, gap before it): spaces at a run's edges are kept as gaps
        gap = 0
        for t, kind in self._runs(s) or [("", "lat")]:
            f = self.fonts.get(px * (0.85 if kind == "sym" else 1), bold, kind == "heb", kind == "sym")
            body = t.strip(" ")
            sp = f.size(" ")[0]
            gap += sp * (len(t) - len(t.lstrip(" ")))
            if body:
                imgs.append((f.render(body, True, color[:3]), gap))
                gap = sp * (len(t) - len(t.rstrip(" ")))
        if not imgs:
            imgs.append((self.fonts.get(px, bold, False, False).render("", True, color[:3]), 0))
        hgt = max(i.get_height() for i, _ in imgs)
        out = self.pg.Surface((max(1, int(sum(i.get_width() + g for i, g in imgs))), hgt), self.pg.SRCALPHA)
        x = 0
        for i, g in imgs:
            x += g
            out.blit(i, (int(x), (hgt - i.get_height()) // 2))
            x += i.get_width()
        if len(color) == 4 and color[3] < 255:
            out.set_alpha(color[3])
        return out

    def measure(self, s, size, bold=False):
        """The width of a line, in plan units."""
        return self.render(s, size, (0, 0, 0), bold).get_width() / self.scale

    def text(self, s, x, y, size, color, bold=False, align="left"):
        """A line whose baseline sits at plan y. In Hebrew, x and the alignment mirror."""
        img = self.render(s, size, color, bold)
        w = img.get_width() / self.scale
        al = align
        if self.mirrored:
            x = W - x
            al = {"left": "right", "right": "left"}.get(al, al)
        left = x if al == "left" else x - w if al == "right" else x - w / 2
        # the transform's translation applies (characters' speech bubbles); rotation does not
        a, b, c, d, e, f = self.m
        left, y2 = left + (e if not self.mirrored else -e), y + f
        px, py = self.ox + left * self.scale, self.oy + y2 * self.scale - img.get_height() * 0.78
        if self.sig_on:
            self.sig = hash((self.sig, self._last_key, int(px), int(py)))
        self.surf.blit(img, (int(px), int(py)))
        if self.rec is not None:
            self.rec.append((px - 2, py - 2, px + img.get_width() + 2, py + img.get_height() + 2))
        return w

    def ticker(self, s, x, y, max_w, size, color, t, bold=False):
        """One line that does not fit its place scrolls slowly inside it, whole, instead of being cut:
        it waits at its start, moves to its end, waits, and starts over. A line that fits is plain text.
        In Hebrew the window mirrors like any text and the line starts from its right end."""
        if self.measure(s, size, bold) <= max_w:
            return self.text(s, x, y, size, color, bold)
        img = self.render(s, size, color, bold)
        iw, ih = img.get_width(), img.get_height()
        ww = int(max_w * self.scale)
        over = iw - ww
        speed = 40 * self.scale                       # pixels a second
        run = over / speed
        cycle = 1.5 + run + 1.5
        ph = t % cycle
        o = 0 if ph < 1.5 else over if ph > 1.5 + run else (ph - 1.5) * speed
        if self.mirrored:
            left = W - x - max_w
            src_x = over - o                          # Hebrew is read from the right: start at the right end
        else:
            left = x
            src_x = o
        a, b, c, d, e, f = self.m
        px = self.ox + (left + (e if not self.mirrored else -e)) * self.scale
        py = self.oy + (y + f) * self.scale - ih * 0.78
        if self.sig_on:
            self.sig = hash((self.sig, self._last_key, int(px), int(py), int(src_x), ww))
        self.surf.blit(img, (int(px), int(py)), self.pg.Rect(int(src_x), 0, ww, ih))
        return max_w

    def wrap(self, s, max_w, size, bold=False):
        lines, line = [], ""
        for word in str(s).split(" "):
            t = f"{line} {word}" if line else word
            if line and self.measure(t, size, bold) > max_w:
                lines.append(line)
                line = word
            else:
                line = t
        if line:
            lines.append(line)
        return lines

    def fit(self, s, max_w, size, bold=False, end=False):
        """A long text keeps its start and loses its end, marked with …. end=True is for a path or the
        text being typed, where the end is what matters: the start is cut instead."""
        s = str(s)
        if self.measure(s, size, bold) <= max_w:
            return s
        if end:
            while len(s) > 4 and self.measure("…" + s, size, bold) > max_w:
                s = s[1:]
            return "…" + s
        while len(s) > 4 and self.measure(s + "…", size, bold) > max_w:
            s = s[:-1]
        return s.rstrip() + "…"
