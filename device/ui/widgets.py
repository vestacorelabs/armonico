"""widgets.py - the pieces every Armonico Pi screen is made of, drawn on the Canvas.

The 4 buttons under the screen, the header, a panel cell, the list of cards, the keys,
the job with its steps, the confirm box and the short notes. Sizes and places are the demo's,
in plan units (800x480). Hits are kept in screen-plan coordinates (already mirrored in Hebrew),
the same as the demo's hits, so a touch is looked up without mirroring it again.
"""
import math
import sys
import threading
import time

from .canvas import W, H
from .texts import short_forms

BAR = 58
RING_INSET = 64                # the touch test's corner rings reach 64 px in: the bar keeps clear of them
SPIN_MARK = "spin"         # what spinner() returns; icon() draws it
RED = (226, 118, 111)
PINK = (243, 154, 168)
WHITES = (0, 2, 4, 5, 7, 9, 11)
BLACKS = ((1, 0), (3, 1), (6, 3), (8, 4), (10, 5))
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()


def key_name(labels, key, mode):
    """The label of one of the device's 13 keys (0 to 12) from the engine's labels for C to B. The high C is 8 in
    the numbers, the white keys being 1 to 8 from the left; in C D E and Do Re Mi it has the low C's name."""
    if not labels or key is None:
        return ""
    if key == 12 and mode == "num":
        return "8"
    k = key % 12
    return str(labels[k]) if k < len(labels) and labels[k] else ""


def alpha(c, a):
    """A color at a fraction of its own opacity."""
    base = c[3] if len(c) == 4 else 255
    return tuple(c[:3]) + (int(base * a),)


class SwitchVal(str):
    """A settings card value that is drawn with a switch: its text (On, Off) and whether it is on."""
    on = False


class Job:
    """Something that takes a while, shown step by step: each step waits, runs, is done or failed.
    The work runs in a thread and moves the steps with .step(i) and .finish()/.fail()."""

    def __init__(self, title, steps, back):
        self.title, self.labels, self.back = title, list(steps), back
        self.state = [0] * len(self.labels)          # 0 waiting, 1 running, 2 done, 3 failed
        self.done, self.ok, self.error = False, False, ""
        self.t0 = time.monotonic()
        self.after = None                            # run on the screen's thread when it ends well

    def step(self, i):
        i = max(0, min(i, len(self.labels) - 1))
        for k in range(len(self.labels)):
            self.state[k] = 2 if k < i else 1 if k == i else (self.state[k] if self.state[k] == 3 else 0)

    def finish(self):
        self.state = [2] * len(self.labels)
        self.done, self.ok = True, True

    def fail(self, error=""):
        k = next((i for i, s in enumerate(self.state) if s == 1), 0)
        self.state[k] = 3
        self.done, self.ok, self.error = True, False, str(error)

    def run(self, work):
        def go():
            try:
                work(self)
                if not self.done:
                    self.finish()
            except Exception as e:                   # a job never takes the screen down
                self.fail(e)
        threading.Thread(target=go, daemon=True).start()
        return self

    @property
    def progress(self):
        return sum(1 for s in self.state if s == 2) / max(1, len(self.labels))


class Widgets:
    """Drawing helpers, mixed into App: they use self.cv, self.P (the palette), self.hits."""

    # ---- hits
    def hit(self, x, y, w, h, fn):
        """A touch area in layout units; in Hebrew it is stored mirrored, as it shows."""
        if fn is None:
            return
        if self.cv.mirrored:
            x = W - x - w
        self.hits.append((x, y, w, h, fn))

    # ---- scrolling: a page of cards or rows that is longer than the screen
    def grid_geom(self):
        """The card grid of the screen's size class: (columns, rows, card width, card height, gap). The grid
        always ends above the bottom buttons: 5" is 2 x 4, with the cards 64 high as before."""
        from .screensize import layout
        cols, rows, _ = layout(getattr(self, "screen_inch", 5.0))
        zoom = self.zoom_pct() if hasattr(self, "zoom_pct") else 100
        if cols == 3 and zoom >= 140:
            cols = 2                                 # 7" and 10" at 140% and more: three columns cut the descriptions
        gap = {1: 8, 2: 8, 3: 7}[cols] if rows < 5 else 6
        avail = H - BAR - 6 - 58
        h = {(1, 4): 80, (2, 3): 100, (2, 4): 76, (3, 4): 76, (3, 5): 64}.get((cols, rows), 64)
        h = min(h, (avail - (rows - 1) * gap) / rows)
        return cols, rows, (752 - (cols - 1) * 12) / cols, h, gap

    def first_row(self, key, row, rows, total_rows):
        """The top row shown of a page of total_rows rows, kept in self.scroll[key]: it moves only when the
        selected row leaves the window, and never past the last full page."""
        sc = self.__dict__.setdefault("scroll", {})
        first = sc.get(key, 0)
        if row < first:
            first = row
        elif row > first + rows - 1:
            first = row - rows + 1
        first = max(0, min(first, max(0, total_rows - rows)))
        sc[key] = first
        return first

    GLIDE = 0.35          # of the way left to the new row, each frame: about 150 ms at 30 frames a second

    def glide(self, key, first):
        """Where a scrolled page is drawn, in rows (a fraction while it moves): it glides to `first` instead of
        jumping there, so ▼▲, the wheel and a drag move the cards smoothly. While it glides the screen is
        drawn at the full rate."""
        g = self.__dict__.setdefault("glide_at", {})
        pos = g.get(key, first)
        d = first - pos
        if abs(d) < 0.02 or abs(d) > 6:
            pos = first                              # there, or a jump too far to glide (a page opened on its end)
        else:
            pos += d * self.GLIDE
            if hasattr(self, "bump"):
                self.bump()
        g[key] = pos
        return pos

    def page_counter(self, first, shown, total):
        """"1-8 of 16" for the top of a page that has more than it shows; empty when all of it is shown."""
        if total <= shown:
            return ""
        return self.tr("pg.of", a=first + 1, b=min(total, first + shown), n=total)

    def scroll_bar(self, y, h, first, shown, total, x=788):
        """A thin bar on the outer side showing where the window is in the whole (nothing if all is shown)."""
        if total <= shown:
            return
        P, cv = self.P, self.cv
        cv.rect(x, y, 4, h, alpha(P["dim"], 0.25), r=2)
        th = max(18, h * shown / total)
        cv.rect(x, y + (h - th) * first / max(1, total - shown), 4, th, P["gold"], r=2)

    def list_marks(self, first, per, total, top, step, move):
        """The marks of a list of rows (counter at the top, position bar, drag to scroll): move(rows) scrolls it."""
        if total <= per:
            return
        self.cv.text(self.page_counter(first, per, total), W - 22, 47, 11, self.P["dim"], align="right")
        self.scroll_bar(top, per * step - 6, first, per, total)
        self.scroll_zone(24, top, W - 48, per * step, step, move)

    def scroll_zone(self, x, y, w, h, pitch, move):
        """A touch that starts inside this area and is dragged up or down scrolls it: move(rows) is called with
        the rows to move (positive = later rows); a short touch is a press as ever. Set each frame."""
        if self.cv.mirrored:
            x = W - x - w
        self.zone = (x, y, w, h, pitch, move)

    # ---- text that has to fit its place
    def short(self, s, max_w, size, bold=False):
        """A text in a button or a card that does not fit is said shorter, never cut or scrolled: its
        own shorter wording from texts.SHORT first, then as many whole words as fit (a name, an
        address). Only one word too long for the place loses letters, from its end."""
        cv, s = self.cv, str(s)
        if not s or cv.measure(s, size, bold) <= max_w:
            return s
        for alt in short_forms(self.lang, s):
            if cv.measure(alt, size, bold) <= max_w:
                return alt
        missed = getattr(self, "short_missed", None)
        if missed is not None:
            missed.add((self.lang, s, max_w, size, bold))
        # whole words, and "…" where the rest was left out, so a cut never reads as a finished phrase
        words, out = s.split(" "), ""
        for wd in words:
            t = f"{out} {wd}" if out else wd
            if cv.measure(t.rstrip(" ,·:;-") + "…", size, bold) > max_w:
                break
            out = t
        if out:
            return out.rstrip(" ,·:;-") + "…"
        while len(s) > 1 and cv.measure(s + "…", size, bold) > max_w:
            s = s[:-1]
        return s + "…"

    def fit_text(self, s, max_w, size, bold=False, min_scale=0.8):
        """(text, size) for a place of max_w: the whole text when it fits, else the whole text a little smaller
        (down to min_scale of the size), else a shorter wording or whole words (short()) at that smallest size."""
        cv, s = self.cv, str(s)
        if not s or cv.measure(s, size, bold) <= max_w:
            return s, size
        k = 0.95
        while k >= min_scale - 1e-6:
            if cv.measure(s, size * k, bold) <= max_w:
                return s, size * k
            k -= 0.05
        for alt in short_forms(self.lang, s):
            if cv.measure(alt, size, bold) <= max_w:
                return alt, size
        small = size * min_scale
        return self.short(s, max_w, small, bold), small

    # ---- frame pieces
    def alarm_icon(self, cx, cy, color, s=1.0):
        """An alarm clock drawn from lines and circles (so it mirrors with the plan): a round face with
        its hands, a bell on each side of the top and two feet. About 24 plan units wide."""
        cv = self.cv
        cv.circle(cx, cy + 1.5 * s, 7.5 * s, color, width=1.8)
        cv.line(cx, cy + 1.5 * s, cx, cy - 2.4 * s, color, 1.6)
        cv.line(cx, cy + 1.5 * s, cx + 2.8 * s, cy + 3.2 * s, color, 1.6)
        cv.circle(cx - 6.8 * s, cy - 6.4 * s, 2.7 * s, color)
        cv.circle(cx + 6.8 * s, cy - 6.4 * s, 2.7 * s, color)
        cv.line(cx - 5 * s, cy + 8 * s, cx - 7.4 * s, cy + 11 * s, color, 1.8)
        cv.line(cx + 5 * s, cy + 8 * s, cx + 7.4 * s, cy + 11 * s, color, 1.8)

    # Where each full screen lives under Settings: view -> (the settings page it sits under, its own title).
    # The path over a title is built from this and SUB_PAGES alone, so every screen shows the same chain.
    SCREEN_HOME = {"check": ("test", "chk"), "diag": ("test", "diag"), "upd": ("sys", "upd"), "test": ("test", "test"),
                   "wifi": ("net", "wf.title"), "bt": ("outs", "bt.title"), "net": ("net", "network"),
                   "place": ("place", "place"), "bk": ("bkp", None)}

    def page_trail(self, page):
        """The titles from Settings down to a settings page, that page included:
        ["Settings", "Network and links", "Broker"]. The top page is just ["Settings"]."""
        from .views_settings import SUB_PAGES
        pages = [page]
        while pages[0] in SUB_PAGES:
            pages.insert(0, SUB_PAGES[pages[0]][0])
        return [self.tr("settings")] + [self.tr("g." + p) for p in pages if p != "top"]

    def opened_from(self, view):
        """Where a screen was opened from, when it keeps that: "settings", "rest", "setup"."""
        if view == "check":
            return (getattr(self, "chk", None) or {}).get("back")
        if view == "wifi":
            return (getattr(self, "wf", None) or {}).get("from")
        return {"diag": getattr(self, "diag_from", None), "upd": getattr(self, "upd_from", None),
                "place": getattr(self, "place_return", None), "net": getattr(self, "net_from", None)}.get(view)

    def trail_of(self, view):
        """The chain of titles down to a screen, its own title last; empty for the screens that sit
        outside Settings (the rest screen, songs, free play, the setup wizard, one opened from the rest screen)."""
        if view == "settings":
            return self.page_trail(self.page)
        if view == "about":
            return [self.tr("settings"), self.tr("about")]
        if view == "edit":
            return self.trail_of((self.edit or {}).get("back") or "settings")     # a field belongs to the screen it edits
        home = self.SCREEN_HOME.get(view)
        origin = (self.__dict__.get("_bt") or {}).get("origin") if view == "bt" else None
        if origin == "quick":
            home = ("quick", "bt.title")             # opened from the quick check: its path is that list
        elif origin == "net":
            home = ("net", "bt.title")
        if view == "check":
            # the check's own name in the path: a quick check of one area sits under that list, and each way in has
            # its title (the path said "Full check" over the automatic checks and every quick check)
            c = getattr(self, "chk", None) or {}
            if c.get("topic"):
                home = ("quick", "qa." + c["topic"])
            elif c.get("mode") in ("auto", "guided"):
                home = ("test", "ci." + c["mode"] + ".t")
        if home and self.opened_from(view) in (None, "settings"):
            return self.page_trail(home[0]) + ([self.tr(home[1])] if home[1] else [])
        return []

    def screen_path(self, title=""):
        """The small line over the title: the chain above this screen, "Settings • System". The screen's own
        title is left off when the title shown is that one."""
        trail = self.trail_of(self.view)
        if trail and (trail[-1] == title or (self.view == "settings" and getattr(self, "page", "") == "prof")):
            trail = trail[:-1]                       # a profile's page has the profile's name for its title
        return " • ".join(trail)

    def fit_chain(self, path, room):
        """A path that is too wide drops its middle levels first ("Settings • … • Wi-Fi"), then is cut."""
        cv = self.cv
        parts = path.split(" • ")
        s = " • ".join(parts)
        if cv.measure(s, 12) > room and len(parts) > 2:
            s = " • ".join([parts[0], "…", parts[-1]])
        return cv.fit(s, room, 12)

    TEMP_FILE = "/sys/class/thermal/thermal_zone0/temp"

    def cpu_temp(self):
        """The CPU's temperature in degrees, read again every 5 seconds at most; None where there is none."""
        at, v = getattr(self, "_temp_seen", (-99.0, None))
        if time.monotonic() - at > 5:
            try:
                with open(self.TEMP_FILE) as f:
                    v = int(f.read().strip()) / 1000.0
            except (OSError, ValueError):
                v = None
            self._temp_seen = (time.monotonic(), v)
        return v

    def temp_chip(self, end):
        """The CPU temperature as a small chip whose far edge is at plan x `end`: green to 60, gold to 70, red from
        70 (the Pi slows down from 80). Returns the width it took, 0 when there is no reading."""
        t = self.cpu_temp()
        if t is None:
            return 0
        P, cv = self.P, self.cv
        s = self.tr("chk.tempCpu", t=int(round(t)))
        col = P["ok"] if t < 60 else P["gold"] if t < 70 else RED
        w = cv.measure(s, 13, True) + 20
        cv.rect(end - w, 6, w, 22, P["panel"], r=11)               # clear of the page counter under it (y 47)
        cv.rect(end - w, 6, w, 22, alpha(col, 0.5), r=11, width=1)
        cv.text(s, end - w / 2, 22, 12, col, True, "center")
        return w

    def header(self, title, right="", inset=0, path=None, temp=False):
        """inset: the texts kept that much further from both sides (the hardware test's corner rings).
        path: a small line above the title (Settings • System); the title then sits lower, in the same bar. Left
        out it is built from where the screen sits (screen_path); "" shows none, and none is shown clear of the
        corner rings (inset).
        An alarm that is on shows as a small clock and its time on the far side, before the right text."""
        P, cv = self.P, self.cv
        if P.get("bar"):
            cv.rect(cv.vx, cv.vy, cv.vw, 52 - cv.vy, P["bar"])
        chip = self.alarm_chip() if not inset and hasattr(self, "alarm_chip") else ""
        room = (520 if right else 740) - 2 * inset
        if path is None:
            path = "" if inset else self.screen_path(title)
        end = W - 22 - inset
        if temp or (not inset and self.trail_of(self.view)):
            # the CPU temperature on the far side of every test screen (a check that heats the Pi shows it) and of every
            # screen under Settings
            tw = self.temp_chip(end)
            if tw:
                end -= tw + 12
                room -= tw + 12
        if right:
            # the text on the far side gets what the title leaves (at least 230): a hint was cut to "hold ☰ or touch…"
            rw = max(230, min(420, 740 - 2 * inset - min(cv.measure(title, 20, True) if title else 0, 520 - 2 * inset) - 40))
            rw = min(rw, end - 22 - inset - 120)
            room = min(room, 740 - 2 * inset - rw - 10)
            cv.text(cv.fit(right, rw, 14), end, 33, 14, P["dim"], align="right")
            end -= min(rw, cv.measure(cv.fit(right, rw, 14), 14)) + 18
        if chip:
            tw = cv.measure(chip, 15, True)
            cv.text(chip, end, 32, 15, P["gold"], True, "right")
            self.alarm_icon(end - tw - 16, 26, P["gold"])
            room -= tw + 56
        if path:
            zoom = cv.zoom
            cv.zoom = min(zoom, 1.0)                 # the small line over the title stays small: it touched the title at 150%
            try:
                cv.text(self.fit_chain(path, room), 22 + inset, 14, 12, P["dim"])
            finally:
                cv.zoom = zoom
        cv.text(cv.fit(title, room, 20, True) if title else "", 22 + inset,
                (43 + 4 * max(0.0, min(1.0, (zoom - 1) / 0.5)) if path else 34), 20, P["ink"], True)    # the title sits lower as it grows

    def bar(self, items, inset=0):
        """The buttons under the screen: [(icon, label, action)], ☰ ▼ ▲ ✓ in that order. They are
        physical, so they never mirror. `inset` pulls the first and last button in from the screen's sides:
        the touch test's corner rings sit there and must not lie on a button. A ☰ without an action goes to the next screen, and says which."""
        P, cv = self.P, self.cv
        items = [list(it) for it in items]
        for it in items:
            if it[0] == "☰" and it[2] is None and not self.confirm:
                nxt = self.next_view()
                it[1], it[2] = self.tr(nxt), (lambda n=nxt: self.go(n))
        self.bar_items = items
        n = len(items)
        x0 = 10 + inset
        w = (W - 2 * x0 - (n - 1) * 8) / n
        self.bar_rects = [(x0 + i * (w + 8), H - BAR + 8, w, 42) for i in range(n)]   # physical, for the tour
        if self.bar_hidden():                        # hidden by the setting: the physical buttons still work (bar_items)
            return
        with cv.physical():
            if P.get("bar"):
                cv.rect(cv.vx, H - BAR, cv.vw, BAR + cv.vy + cv.vh - H, P["bar"])
            for i, (ic, label, fn) in enumerate(items):
                x = x0 + i * (w + 8)
                self.hit(x, H - BAR + 8, w, 42, fn and (lambda f=fn: self.press_bar(f)))
                cv.rect(x, H - BAR + 8, w, 42, P["panel"], r=12)
                label, ls = self.fit_text(label, w - 44, 15, True) if label else ("", 15)
                tw = cv.measure(label, ls, True) if label else 0
                sx = x + w / 2 - (tw + (28 if label else 0)) / 2
                cv.text(ic, sx + 9 if label else x + w / 2, H - BAR + 36, 18, P["gold"], True, "center")
                if label:
                    cv.text(label, sx + 28, H - BAR + 35, ls, P["ink"], True)

    def draw_qr(self, x, y, size, text):
        """A QR code of text on a white square, in physical plan units (x, y, size): never mirrored, so it reads
        the same in Hebrew. Runs of dark modules are one rectangle each. False when the text does not fit a code."""
        from . import qrmini
        try:
            rows = qrmini.modules(text)
        except ValueError:
            return False
        n = len(rows)
        k = size / (n + 8)                           # 4 modules of white around it
        cv = self.cv
        with cv.physical():
            cv.rect(x, y, size, size, (255, 255, 255), r=10)
            for r, row in enumerate(rows):
                c = 0
                while c < n:
                    if row[c]:
                        e = c
                        while e < n and row[e]:
                            e += 1
                        cv.rect(x + (c + 4) * k, y + (r + 4) * k, (e - c) * k + 0.5, k + 0.5, (13, 20, 36))
                        c = e
                    else:
                        c += 1
        return True

    def switch(self, cx, cy, on, color_on, color_off):
        """A phone-style switch, 44 x 24 screen pixels centered on (cx, cy): a track, and a knob at its right end when on."""
        cv = self.cv
        tw, th = 44, 24
        cv.rect(cx - tw / 2, cy - th / 2, tw, th, color_on if on else color_off, r=th / 2)
        kx = cx + tw / 2 - th / 2 if on else cx - tw / 2 + th / 2
        cv.circle(kx, cy, th / 2 - 3, (255, 255, 255, 255))

    def cell(self, x, y, w, h, label, value, desc, framed, selectable, fn):
        """A settings card: a small label, the value, and the action line (gold when framed)."""
        P, cv = self.P, self.cv
        if selectable:
            self.hit(x, y, w, h, fn)
        cv.rect(x, y, w, h, P["panel"], r=12)
        if framed:
            cv.rect(x, y, w, h, P["sel"], r=12, width=2.5)
        k = h / 64
        # The card does not grow with the interface size: its 3 lines get only the growth that fits between
        # their baselines (up to 1.25 of the card's own height), so a large setting never puts one line over another.
        zoom = cv.zoom
        cv.zoom = min(zoom, max(1.0, 1.25 * k))
        try:
            # a line too long for the card is said shorter (short()): no scrolling, nothing hidden
            lab, ls = self.fit_text(label, w - 32, 12, True)
            cv.text(lab, x + 16, y + 18 * k, ls, P["dim"], True)
            sw = isinstance(value, SwitchVal)
            val, vs = self.fit_text(value, w - 32 - (60 if sw else 0), 16, True)
            cv.text(val, x + 16, y + 40 * k, vs, P["ink"], True)
            if desc:
                d, ds = self.fit_text(desc, w - 32 - (cv.measure("✓ ", 11, True) if framed else 0), 11, framed)
                cv.text(("✓ " + d) if framed else d, x + 16, y + 56 * k, ds, P["gold"] if framed else P["dim"], framed)
        finally:
            cv.zoom = zoom
        if isinstance(value, SwitchVal):
            # at the far end of the card from where the text starts (left in Hebrew, right in English), drawn in screen
            # coordinates so the knob is at the right end for on, as on a phone
            mir = cv.mirrored                      # asked outside physical(), which reads as not mirrored
            with cv.physical():
                px = (W - (x + w) + 16 + 22) if mir else (x + w - 16 - 22)
                self.switch(px, y + h / 2, value.on, P["gold"], (128, 128, 128, 110))
            toggle = getattr(value, "toggle", None)
            if toggle is not None and selectable:
                self.hit(x + w - 76, y, 76, h, toggle)   # the switch itself turns it off or on; the card opens its page

    def paras(self, lines, x, y, w, size=15, gap=21, color=None):
        """Wrapped lines; returns the y under the last one."""
        for s in lines:
            for line in self.cv.wrap(s, w, size):
                self.cv.text(line, x, y, size, color or self.P["ink"])
                y += gap
        return y

    def spinner(self, t):
        """A mark for "working": icon() draws it as the spinner below, at the time it carries."""
        return (SPIN_MARK, t)

    def icon(self, ic, x, y, size, color):
        """A status mark (✓ ⚠ ✕ ·) as text with its baseline at y, or the spinner centered on the same line."""
        if isinstance(ic, tuple) and ic and ic[0] == SPIN_MARK:
            self.spin(x, y - size * 0.36, size * 0.48, color, ic[1])
        else:
            self.cv.text(ic, x, y, size, color, True, "center")

    def spin(self, cx, cy, r, color, t):
        """8 dots on a ring, one bright and a fading tail behind it, turning smoothly once a second."""
        head = (t * 8.0) % 8
        for i in range(8):
            behind = (head - i) % 8                  # 0 is the bright dot, 7 the faintest
            a = max(0.12, 1.0 - behind / 7.0)
            ang = i * math.pi / 4 - math.pi / 2
            self.cv.circle(cx + math.cos(ang) * r, cy + math.sin(ang) * r, r * (0.13 + 0.09 * a),
                           tuple(color[:3]) + (int(255 * a),))

    def key_total(self):
        """13 keys, C to C, the 13th the high C."""
        fn = getattr(sys.modules.get(type(self.screen).__module__), "key_total", None)
        return fn() if fn else 13

    def lit_key(self, note):
        """The key that lights for a note of a lesson: key 12 (the high C) when it is the C one octave
        above the keys' first C, else its pitch class: the keys stay in one octave."""
        if note % 12 == 0 and note == 12 * (self.screen.octave + 1) + 12:
            return 12
        return note % 12

    # ---- the 13 keys (physical: never mirrored); a key is its index 0 to 12, 12 being the high C
    def keys(self, y, lit=None, ghost=None, labels=None, colours=None, label_mode="num", colors_mode="notes",
             stage="free", finger="", flash=None, numbers=True):
        P, cv = self.P, self.cv
        kw, kh, gap = 78, 172, 6
        whites = WHITES + (12,)
        x0 = (W - len(whites) * kw - (len(whites) - 1) * gap) / 2
        now = time.time()
        flash = flash or {}

        def key_colour(pc):
            pc %= 12
            if colours and pc < len(colours) and isinstance(colours[pc], (list, tuple)) and len(colours[pc]) >= 3:
                return tuple(max(0, min(255, int(v))) for v in colours[pc][:3])
            return P["lit"]

        def target_colour(pc):
            pc %= 12
            return P["lit"] if stage == "keys" or colors_mode == "simple" or stage == "free" else key_colour(pc)

        stickers = stage == "numbers" and label_mode in ("num", "abc", "do") and labels
        rects = []
        with cv.physical():
            for i, pc in enumerate(whites):
                x = x0 + i * (kw + gap)
                hot = now - flash.get(pc, 0) < 0.18
                col = P["ok"] if hot else target_colour(pc) if pc == lit else P["key"]
                cv.rect(x, y, kw, kh, col, r=12)
                if P.get("line"):
                    cv.rect(x, y, kw, kh, P["line"], r=12, width=1.5)
                if stickers:
                    lab = key_name(labels, pc, label_mode)
                    if lab:
                        cv.circle(x + kw / 2, y + kh - 30, 17, (255, 255, 255) if pc == lit else key_colour(pc))
                        dark = pc == lit or (colors_mode == "notes" and pc in (2, 3, 4))
                        size = 12 if len(lab) >= 4 else 14 if len(lab) >= 3 else 17
                        cv.text(lab, x + kw / 2, y + kh - 24, size, (29, 29, 29) if dark else (255, 255, 255), True, "center")
                elif numbers and labels is None:
                    cv.text(str(i + 1), x + kw / 2, y + kh - 18, 17, P["onkey"], True, "center")
                rects.append((pc, x, y, kw, kh))
            for pc, a in BLACKS:
                x = x0 + (a + 1) * (kw + gap) - 29
                hot = now - flash.get(pc, 0) < 0.18
                cv.rect(x, y, 52, 104, P["ok"] if hot else target_colour(pc) if pc == lit else P["black"], r=9)
                if stickers:
                    # the black keys' labels (1# ... 6#, C# ...) as the white keys show theirs: they had none.
                    # Above the pink ring's place, so both show
                    lab = labels[pc] if pc < len(labels) else ""
                    if lab:
                        bg = (255, 255, 255) if pc == lit else key_colour(pc)
                        cv.circle(x + 26, y + 50, 15, bg)
                        dark = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] > 150
                        size = 10 if len(lab) >= 4 else 12 if len(lab) >= 3 else 14
                        cv.text(lab, x + 26, y + 50 + size * 0.36, size, (29, 29, 29) if dark else (255, 255, 255), True, "center")
                rects.insert(0, (pc, x, y, 52, 104))
            if ghost is not None and 0 <= ghost < 13:
                if ghost in whites:
                    cx, cy = x0 + whites.index(ghost) * (kw + gap) + kw / 2, y + kh - 62
                else:
                    a = dict(BLACKS)[ghost]
                    cx, cy = x0 + (a + 1) * (kw + gap) - 3, y + 104 - 22
                cv.circle(cx, cy, 14, PINK, width=3)
            # the gold mark over the next key follows the labels chosen: the finger in 1 2 3,
            # the note's name in C D E and Do Re Mi, nothing with no labels
            mark = finger if label_mode == "num" else \
                (key_name(labels, lit, label_mode) if label_mode in ("abc", "do") and labels and lit is not None else "")
            if finger and mark and lit is not None:
                if lit in whites:
                    cx = x0 + whites.index(lit) * (kw + gap) + kw / 2
                else:
                    cx = x0 + (dict(BLACKS)[lit] + 1) * (kw + gap) - 3
                size = 18 if len(mark) <= 2 else 14 if len(mark) == 3 else 12
                cv.circle(cx, y - 22, 16 if len(mark) <= 2 else 19, P["gold"])
                cv.text(mark, cx, y - 22 + size * 0.38, size, (59, 42, 5), True, "center")
        return rects

    # ---- overlays
    def draw_toast(self):
        msg, until = self.screen.toast
        left = until - time.time()
        if not msg or left <= 0:
            return
        P, cv = self.P, self.cv
        light = tuple(P["panel"]) in ((255, 255, 255), (255, 255, 255, 255))
        a = min(1.0, left * 3)
        bg = (255, 255, 255) if light else (29, 39, 64)
        fg = P["ink"] if light else (255, 255, 255)
        msg = cv.fit(msg, 700, 15, True)
        w = cv.measure(msg, 15, True) + 40
        with cv.physical():
            cv.rect(W / 2 - w / 2, H - BAR - 56, w, 38, alpha(bg, a), r=19)
            cv.text(msg, W / 2, H - BAR - 31, 15, alpha(fg, a), True, "center")

    def draw_soak(self):
        """While armonico-soak.py runs (piano/device/soak, retained): a strip at the top of every screen, so a
        screen that changes by itself is known to be the test. It goes away by itself when the test stops sending
        its beat (it was killed, or the Pi restarted)."""
        d = getattr(self, "soak", None)
        if not isinstance(d, dict):
            return
        try:
            t0, total = float(d.get("t0", 0)), float(d.get("total", 0))
        except (TypeError, ValueError):
            return
        now = time.time()
        try:
            beat = float(d.get("beat", t0))
        except (TypeError, ValueError):
            beat = t0
        # the test sends a beat every minute: none for 3 minutes means it was killed or the Pi restarted, and
        # "finished" or "stopped" stay up for 2 minutes at most if the test never cleared them
        if now - beat > (120 if d.get("state") in ("end", "stopped") else 180):
            return

        def hm(sec):
            sec = int(max(0, sec))
            return f"{sec // 3600}:{sec % 3600 // 60:02d}"
        state = d.get("state")
        if state == "end":
            msg = self.L("Load test finished", "בדיקת העומס הסתיימה")
        elif state == "stopped":
            msg = self.L(f"Load test stopped · {d.get('reason_en', '')}", f"בדיקת העומס נעצרה · {d.get('reason', '')}")
        elif state == "start":
            msg = self.L(f"Load test starting · {hm(total)}", f"בדיקת עומס מתחילה · {hm(total)}")
        else:
            msg = self.L(f"Load test · {hm(now - t0)} of {hm(total)} · {d.get('phase_en', '')}",
                         f"בדיקת עומס · {hm(now - t0)} מתוך {hm(total)} · {d.get('phase', '')}")
        cv = self.cv
        msg = cv.fit(msg, 560, 14, True)
        w = cv.measure(msg, 14, True) + 36
        with cv.physical():
            cv.rect(W / 2 - w / 2, 6, w, 30, self.P["gold"], r=15)
            cv.text(msg, W / 2, 26, 14, (59, 42, 5), True, "center")

    def draw_saving(self):
        """While a recording that was stopped is put together into its file: a small box at the top of every screen
        with how far it is, a bar and the stage, until the file is ready (Recorder.save_progress)."""
        rec = getattr(self, "srec", None)
        got = rec.save_progress() if rec is not None and hasattr(rec, "save_progress") else None
        if got is None:
            return
        frac, stage = got
        P, cv = self.P, self.cv
        light = tuple(P["panel"]) in ((255, 255, 255), (255, 255, 255, 255))
        bg = (255, 255, 255) if light else (34, 48, 79)
        fg = P["ink"] if light else (255, 255, 255)
        x, y, w, h = W / 2 - 150, 10, 300, 70
        cv.rect(x, y, w, h, bg, r=14)
        cv.text(self.tr("rec.savingT"), x + 16, y + 24, 14, fg, True)
        cv.text(f"{int(frac * 100)}%", x + w - 16, y + 24, 14, P["gold"], True, "right")
        cv.rect(x + 16, y + 34, w - 32, 8, alpha(P["dim"], 0.3), r=4)
        cv.rect(x + 16, y + 34, max(8, (w - 32) * frac), 8, P["gold"], r=4)
        cv.text(self.tr("rec.stage." + stage), x + 16, y + 60, 12, P["dim"])

    def draw_confirm(self):
        c = self.confirm
        if not c:
            return False
        P, cv = self.P, self.cv
        self.hits = []
        cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (0, 0, 0, 115))
        cv.rect(cv.vx, H - BAR, cv.vw, BAR + cv.vy + cv.vh - H, (11, 15, 26))
        light = tuple(P["panel"]) in ((255, 255, 255), (255, 255, 255, 255))
        # the box grows with the question (it showed 3 lines and cut the rest), centered above the buttons
        lines = cv.wrap(c["text"], 440, 17, True)
        pitch = 24 * max(1.0, cv.zoom)
        bh = min(H - BAR - 20, max(130, 64 + len(lines) * pitch))
        by = max(10, (H - BAR - bh) / 2)
        lines = lines[:max(1, int((bh - 50) // pitch))]
        cv.rect(150, by, 500, bh, (255, 255, 255) if light else (29, 39, 64), r=16)
        for i, line in enumerate(lines):
            cv.text(line, 180, by + 42 + i * pitch, 17, P["ink"] if light else (255, 255, 255), True)
        self.bar([("☰", self.tr("cancel"), lambda: self.answer(False)), ("▼", "", None), ("▲", "", None),
                  ("✓", self.tr("yes"), lambda: self.answer(True))])
        return True

    def draw_job(self, job, t):
        P, cv = self.P, self.cv
        n = len(job.labels)
        right = self.tr("jobDone") if job.done else f"{min(n, sum(1 for s in job.state if s >= 2) + 1)} / {n}"
        self.header(job.title, right)
        cv.rect(24, 64, W - 48, 8, P["panel"], r=4)
        cv.rect(24, 64, max(8, (W - 48) * job.progress), 8, P["ok"], r=4)
        pitch = 44 if n <= 7 else 38
        for i, label in enumerate(job.labels):
            y = 104 + i * pitch
            s = job.state[i]
            cv.rect(24, y - 26, W - 48, pitch - 6, P["panel"], r=10)
            ic = "✓" if s == 2 else "✕" if s == 3 else self.spinner(t) if s == 1 else "·"
            col = P["ok"] if s == 2 else RED if s == 3 else P["gold"] if s == 1 else P["dim"]
            self.icon(ic, 44, y, 18, col)
            cv.text(cv.fit(label, W - 120, 15, s == 1), 66, y, 15, P["ink"] if s else P["dim"], s == 1)
        if job.done:
            msg = self.tr("jobOk") if job.ok else self.tr("jobFail", e=job.error or "?")
            cv.text(cv.fit(msg, W - 48, 15, True), 24, H - BAR - 16, 15, P["ok"] if job.ok else RED, True)
