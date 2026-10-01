#!/usr/bin/env python3
"""reset_screen.py - the screen of the full reset (device/full_reset.sh).

While the full reset runs, the program and its screens are deleted and installed again, so this
small screen shows the steps instead. It runs as root with the system's own Python and pygame,
from a folder in /var/lib/armonico-reset that holds a copy of this file, the fonts and the logo. The purge takes
pygame's package away while it runs; what it has loaded stays in memory, and if it stops anyway,
full_reset.sh starts it again after the install. It draws what full_reset.sh writes into status.json:

  {"lang": "he", "step": 2, "state": "running" | "done" | "failed" | "nonet" | "cancelled",
   "line": "...", "source": "..."}

nonet and cancelled come before anything is deleted: the device goes back to its own screen.

    python3 reset_screen.py FOLDER          on the screen, until full_reset.sh stops it
    python3 reset_screen.py FOLDER --png F  one frame into an image, for a look without a screen
"""
import json
import math
import os
import sys
import time
from pathlib import Path

W, H = 800, 480
NIGHT, PANEL, INK, DIM, GOLD, MINT, RED = (13, 20, 36), (22, 31, 51), (233, 237, 245), (141, 151, 171), \
    (232, 182, 79), (142, 227, 184), (240, 120, 110)

STEPS = {
    "en": [("Getting ready", "a copy of the installer, kept outside the program"),
           ("The latest version", "the newer of GitHub and the device, and the packages"),
           ("Deleting everything", "program, data, backups, system packages, MQTT"),
           ("Installing again", "the same way as a first install"),
           ("Starting again", "the setup opens by itself")],
    "he": [("הכנה", "עותק של ההתקנה, מחוץ לתוכנה"),
           ("הגרסה האחרונה", "החדשה מבין GitHub והמכשיר, וחבילות המערכת"),
           ("מחיקת הכל", "תוכנה, נתונים, גיבויים, חבילות המערכת, MQTT"),
           ("התקנה מחדש", "כמו בהתקנה ראשונה"),
           ("הפעלה מחדש", "אשף ההגדרה נפתח לבד")],
}
TEXT = {
    "en": {"title": "Full reset", "keep": "Do not turn the device off", "done": "Done. Starting again…",
           "failed": "The reset stopped", "how": "The log, and the install by hand:",
           "nonet": "No network. Nothing was deleted", "cancelled": "Canceled. Nothing was deleted",
           "back": "Back to the device in a moment"},
    "he": {"title": "איפוס מלא", "keep": "לא לכבות את המכשיר", "done": "הסתיים. מופעל מחדש…",
           "failed": "האיפוס נעצר", "how": "הלוג, וההתקנה הידנית:",
           "nonet": "אין רשת. שום דבר לא נמחק", "cancelled": "בוטל. שום דבר לא נמחק",
           "back": "חוזרים למכשיר בעוד רגע"},
}


def bidi(s, he):
    """Hebrew in display order (python-bidi, copied next to this file before the program went)."""
    if not he:
        return s
    try:
        from bidi.algorithm import get_display
        return get_display(s, base_dir="R")
    except Exception:
        return s


class Screen:
    def __init__(self, pg, folder, surf):
        self.pg, self.dir, self.surf = pg, Path(folder), surf
        self.fonts = {}
        self.logo = None
        try:
            self.logo = pg.image.load(str(self.dir / "logo.png")).convert_alpha()
        except Exception:
            pass

    def font(self, size, bold=False, hebrew=False, symbol=False):
        key = (size, bold, hebrew, symbol)
        if key not in self.fonts:
            names = (["DejaVuSans-Bold.ttf"] if symbol else []) + \
                    (["Heebo-Hebrew-Bold.ttf" if bold else "Heebo-Hebrew-Medium.ttf"] if hebrew else []) + \
                    ["Heebo-Bold.ttf" if bold else "Heebo-Medium.ttf", "Heebo-Regular.ttf"]
            path = next((str(self.dir / n) for n in names if (self.dir / n).exists()), None)
            if symbol and path is None and Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists():
                path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            self.fonts[key] = self.pg.font.Font(path, size)
        return self.fonts[key]

    def text(self, s, x, y, size, color, bold=False, align="left", he=False):
        heb = he and any("֐" <= ch <= "׿" for ch in s)
        line = bidi(s, heb)
        # Hebrew letters from the Hebrew file, everything else (digits, commas, Latin) from the Latin one:
        # the Hebrew file has no punctuation, and a comma came out as a box
        runs, cur, kind = [], "", None
        for ch in line:
            k = "heb" if "֐" <= ch <= "׿" else ("sp" if ch == " " else "lat")
            if k == "sp":
                k = kind or "lat"
            if k != kind and cur:
                runs.append((cur, kind))
                cur = ""
            cur, kind = cur + ch, k
        if cur:
            runs.append((cur, kind))
        imgs = [self.font(size, bold, k == "heb").render(r, True, color) for r, k in runs] or \
            [self.font(size, bold).render("", True, color)]
        img = self.pg.Surface((sum(i.get_width() for i in imgs), max(i.get_height() for i in imgs)), self.pg.SRCALPHA)
        xx = 0
        for i in imgs:
            img.blit(i, (xx, 0))
            xx += i.get_width()
        if align == "right":
            x -= img.get_width()
        elif align == "center":
            x -= img.get_width() // 2
        self.surf.blit(img, (int(x), int(y - img.get_height() * 0.78)))
        return img.get_width()

    def mark(self, kind, cx, cy, t):
        pg = self.pg
        if kind == "done":
            pg.draw.circle(self.surf, MINT, (cx, cy), 11)
            pg.draw.lines(self.surf, NIGHT, False, [(cx - 5, cy), (cx - 1, cy + 4), (cx + 6, cy - 4)], 3)
        elif kind == "failed":
            pg.draw.circle(self.surf, RED, (cx, cy), 11)
            pg.draw.line(self.surf, NIGHT, (cx - 4, cy - 4), (cx + 4, cy + 4), 3)
            pg.draw.line(self.surf, NIGHT, (cx + 4, cy - 4), (cx - 4, cy + 4), 3)
        elif kind == "running":
            for i in range(8):                      # the device's 8-dot ring
                a = t * 5 + i * math.pi / 4
                r = 2.2 + 1.6 * ((i / 7) ** 1.5)
                pg.draw.circle(self.surf, GOLD, (int(cx + 9 * math.cos(a)), int(cy + 9 * math.sin(a))), int(round(r)))
        else:
            pg.draw.circle(self.surf, (60, 70, 92), (cx, cy), 5)

    def draw(self, st, t):
        lang = "he" if st.get("lang") == "he" else "en"
        he = lang == "he"
        tx, steps = TEXT[lang], STEPS[lang]
        step, state = int(st.get("step", 0)), st.get("state", "running")
        s = self.surf
        s.fill(NIGHT)
        # the logo and the title, on the reading side
        if self.logo is not None:
            img = self.pg.transform.smoothscale(self.logo, (44, 44))
            s.blit(img, (W - 24 - 44, 18) if he else (24, 18))
        ax = W - 24 - 56 if he else 24 + 56
        al = "right" if he else "left"
        self.text(tx["title"], ax, 50, 26, INK, True, al, he)
        stopped = state in ("failed", "nonet", "cancelled")
        warn = tx.get(state) if stopped else tx["done"] if state == "done" else tx["keep"]
        self.text(warn, (24 if he else W - 24), 50, 15, RED if stopped else MINT if state == "done" else GOLD,
                  True, "left" if he else "right", he)
        # the steps
        y0 = 92
        for i, (name, sub) in enumerate(steps):
            y = y0 + i * 52
            kind = ("failed" if stopped else "running") if i == step and state != "done" else \
                "done" if i < step or state == "done" else "waiting"
            if i == step and state != "done":
                self.pg.draw.rect(s, PANEL, (16, y - 4, W - 32, 48), border_radius=12)
            mx = W - 44 if he else 44
            self.mark(kind, mx, y + 20, t)
            nx = W - 70 if he else 70
            self.text(name, nx, y + 20, 18, INK if kind != "waiting" else DIM, True, al, he)
            self.text(sub, nx, y + 39, 13, DIM, False, al, he)
        # the bar and the line the installer is on
        done = len(steps) if state == "done" else step + (0.5 if state == "running" else 0)
        bar = (24, 362, W - 48, 8)
        self.pg.draw.rect(s, PANEL, bar, border_radius=4)
        fill = int(bar[2] * min(1.0, done / len(steps)))
        if fill:
            fx = bar[0] + bar[2] - fill if he else bar[0]
            self.pg.draw.rect(s, RED if stopped else GOLD, (fx, bar[1], fill, bar[3]), border_radius=4)
        # no network says it all in the title, in the screen's language
        line = "" if state == "nonet" else str(st.get("line", ""))[:110]
        if line:
            self.text(line, W // 2, 398, 13, DIM, False, "center")
        if state == "failed":
            self.text(tx["how"], W // 2, 420, 13, INK, True, "center", he)
            self.text("/var/lib/armonico-reset/reset.log", W // 2, 441, 12, INK, False, "center")
            self.text("cd /var/lib/armonico-reset/src && sudo ./install.sh --device", W // 2, 460, 12, INK, False, "center")
        if state in ("nonet", "cancelled"):
            self.text(tx["back"], W // 2, 430, 15, INK, True, "center", he)
        src = st.get("source", "")
        if src and not stopped:
            self.text(src, W // 2, 440, 12, DIM, False, "center")


def read_status(folder):
    try:
        return json.loads((Path(folder) / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    folder = Path(sys.argv[1])
    sys.path.insert(0, str(folder / "lib"))          # python-bidi, copied before the program went
    png = sys.argv[3] if len(sys.argv) == 4 and sys.argv[2] == "--png" else None
    if png:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    else:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        os.environ.setdefault("SDL_RENDER_VSYNC", "1")
    import pygame as pg
    pg.display.init()
    pg.font.init()
    if png:
        surf = pg.display.set_mode((W, H))
    else:
        # the device's own 5" first: on a Pi 4 the HDMI ports come before the DSI in SDL's list
        sizes = []
        try:
            sizes = [tuple(x) for x in pg.display.get_desktop_sizes()]
        except Exception:
            pass
        # idx = next((i for i, sz in enumerate(sizes) if sz in ((800, 480), (480, 800))), 0)    # HDMI off (DSI only)
        idx = next((i for i, sz in enumerate(sizes) if sz in ((800, 480), (480, 800))), max(0, len(sizes) - 1))
        try:
            surf = pg.display.set_mode((W, H), pg.FULLSCREEN, display=idx)
        except Exception:
            surf = pg.display.set_mode((W, H), pg.FULLSCREEN)
        pg.mouse.set_visible(False)
    scr = Screen(pg, folder, surf)
    t0 = time.monotonic()
    if png:
        scr.draw(read_status(folder), 0.3)
        pg.image.save(surf, png)
        return
    clock = pg.time.Clock()
    st = {}
    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                return
        # the last status stays on the screen when the file is gone (the reset deletes its folder last)
        st = read_status(folder) or st
        scr.draw(st, time.monotonic() - t0)
        pg.display.flip()
        if st.get("quit"):
            return
        clock.tick(20)


if __name__ == "__main__":
    main()
