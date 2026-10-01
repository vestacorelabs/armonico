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
    "en": [("Getting ready", "a copy of the installer is kept outside the program"),
           ("Packages and version", "looks for a newer version and gets the packages. No network: from the kit kept on the device"),
           ("Deleting", "program, data, backups and system packages"),
           ("Installing again", "the same way as a first install"),
           ("Starting again", "the setup opens by itself")],
    "he": [("הכנה", "עותק של ההתקנה נשמר מחוץ לתוכנה"),
           ("חבילות וגרסה", "בודק גרסה חדשה ומכין חבילות. בלי רשת: מהערכה השמורה במכשיר"),
           ("מחיקה", "תוכנה, נתונים, גיבויים וחבילות מערכת"),
           ("התקנה מחדש", "כמו בהתקנה ראשונה"),
           ("הפעלה מחדש", "אשף ההגדרה ייפתח מעצמו")],
}
TEXT = {
    "en": {"title": "Full reset", "keep": "The device is deleted and installed again. Do not turn it off or unplug it",
           "done": "Done. Starting again…", "failed": "The reset stopped", "how": "The log, and the install by hand:",
           "nonet": "No network and no kit on the device. Nothing was deleted", "cancelled": "Canceled. Nothing was deleted",
           "back": "Back to the device in a moment", "now": "What is happening now", "step": "Step {n} of {m}",
           "foot": "If the power is cut, the reset goes on at the next start"},
    "he": {"title": "איפוס מלא", "keep": "המכשיר נמחק ומותקן מחדש. לא לכבות ולא לנתק מחשמל",
           "done": "הסתיים. מופעל מחדש…", "failed": "האיפוס נעצר", "how": "הלוג, וההתקנה הידנית:",
           "nonet": "אין רשת ואין ערכה במכשיר. שום דבר לא נמחק", "cancelled": "בוטל. שום דבר לא נמחק",
           "back": "חוזרים למכשיר בעוד רגע", "now": "מה קורה עכשיו", "step": "שלב {n} מתוך {m}",
           "foot": "אם החשמל נותק, האיפוס ימשיך בהדלקה הבאה"},
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
        self.logo = self.vcl = None
        self.lines = []                              # the last lines the installer wrote, for the live log
        for attr, name in (("logo", "logo.png"), ("vcl", "vcl.png")):
            try:
                setattr(self, attr, pg.image.load(str(self.dir / name)).convert_alpha())
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

    def logo_at(self, img, cy, h):
        """A logo of height h centered on the screen, its proportions kept."""
        if img is None:
            return
        w = max(1, int(img.get_width() * h / img.get_height()))
        self.surf.blit(self.pg.transform.smoothscale(img, (w, h)), ((W - w) // 2, cy))

    def ring(self, cx, cy, r, frac, color):
        """The progress ring: a track, and the done part of it drawn clockwise from the top."""
        pg = self.pg
        pg.draw.circle(self.surf, PANEL, (cx, cy), r, 10)
        n = int(120 * max(0.0, min(1.0, frac)))
        for i in range(n):
            a = -math.pi / 2 + 2 * math.pi * i / 120
            pg.draw.circle(self.surf, color, (int(cx + (r - 5) * math.cos(a)), int(cy + (r - 5) * math.sin(a))), 5)

    def draw(self, st, t):
        lang = "he" if st.get("lang") == "he" else "en"
        he = lang == "he"
        tx, steps = TEXT[lang], STEPS[lang]
        step, state = int(st.get("step", 0)), st.get("state", "running")
        s = self.surf
        s.fill(NIGHT)
        stopped = state in ("failed", "nonet", "cancelled")
        # the header, centered: the Armonico logo, the title, and the line that says what is going on
        self.logo_at(self.logo, 6, 26)
        self.text(tx["title"], W // 2, 58, 22, INK, True, "center", he)
        warn = tx.get(state) if stopped else tx["done"] if state == "done" else tx["keep"]
        self.text(warn, W // 2, 78, 14, RED if stopped else MINT if state == "done" else GOLD, True, "center", he)
        # the ring on the side away from the reading side, with the percent and the step in it
        done = len(steps) if state == "done" else step + (0.5 if state == "running" else 0)
        frac = min(1.0, done / len(steps))
        cx = 130 if he else W - 130
        self.ring(cx, 190, 80, frac, RED if stopped else GOLD)
        self.text(f"{int(frac * 100)}%", cx, 198, 36, INK, True, "center")
        self.text(tx["step"].format(n=min(step + 1, len(steps)), m=len(steps)) if state != "done" else "", cx, 222, 13, DIM,
                  False, "center", he)
        # the steps, on the reading side
        right = W - 20 if he else 20
        al = "right" if he else "left"
        y0 = 92
        for i, (name, sub) in enumerate(steps):
            y = y0 + i * 46
            kind = ("failed" if stopped else "running") if i == step and state != "done" else \
                "done" if i < step or state == "done" else "waiting"
            if i == step and state != "done":
                self.pg.draw.rect(s, PANEL, (260 if he else 16, y - 4, 524, 44), border_radius=12)
            mx = right - 18 if he else right + 18
            self.mark(kind, mx, y + 18, t)
            nx = right - 46 if he else right + 46
            self.text(name, nx, y + 16, 17, INK if kind != "waiting" else DIM, True, al, he)
            self.text(sub, nx, y + 34, 12, DIM, False, al, he)
        # the live log: the last lines the installer wrote, each with a dot (done green, now gold)
        line = "" if state == "nonet" else str(st.get("line", ""))[:100]
        if line and (not self.lines or self.lines[-1] != line):
            self.lines = (self.lines + [line])[-4:]
        if state in ("nonet", "cancelled"):
            self.text(tx["back"], W // 2, 380, 15, INK, True, "center", he)
        elif state == "failed":
            self.text(tx["how"], W // 2, 356, 13, INK, True, "center", he)
            self.text("/var/lib/armonico-reset/reset.log", W // 2, 378, 12, INK, False, "center")
            self.text("cd /var/lib/armonico-reset/src && sudo ./install.sh --device", W // 2, 398, 12, INK, False, "center")
        else:
            self.text(tx["now"], right, 346, 12, DIM, False, al, he)
            self.pg.draw.rect(s, (10, 16, 29), (24, 352, W - 48, 82), border_radius=12)
            for i, ln in enumerate(self.lines):
                last = i == len(self.lines) - 1
                col = (MINT if state == "done" else GOLD) if last else MINT
                yy = 372 + i * 19
                self.pg.draw.circle(s, col, (40, yy - 4), 4)
                self.text(ln, 54, yy, 13, INK if last else DIM, False, "left")
        # the footer, centered: where the install comes from, the power-cut note, and the Vesta Core Labs logo
        src = st.get("source", "")
        foot = (src + ". " if src and not stopped else "") + tx["foot"]
        self.text(foot, W // 2, 444, 12, DIM, False, "center", he)
        self.logo_at(self.vcl, 452, 22)


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
