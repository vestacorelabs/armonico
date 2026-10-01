"""tour.py - the opening tour and the one-time tips of the Armonico Pi screen.

The tour: after the setup finishes for the first time, the character chosen in the settings shows
the rest screen in 8 short steps, in a speech bubble beside it, with the rest of the screen dimmed
and the part it talks about outlined. ✓ or a touch on the bubble goes on, ☰ skips. It waits while
an alarm, a song or a lesson has the device, and goes on from the same step when the rest screen
is free again.

The tips: the first time Songs, Free play and a lesson open, the small character says one line in
a corner, for 8 seconds or until a touch. Each one once.

State, in the device's settings (settings.json, the same dict as everything else on this screen):
  tour_done   False while the tour is due; True once done or skipped
  tips_seen   the tips already shown: "songs", "free", "lesson"
A device set up before these existed has neither key: a missing key counts as done and seen, so
an update does not start the tour on a device in use. The first finish of the setup writes them
as due (a new install, and a full reset, which deletes the settings file). Settings › System ›
Opening tour writes them as due again.

Load: the tour's whole picture (the rest screen, the dimming, the outline and the bubble) is drawn
once per step into a picture kept for the screen's size, and each frame copies it and draws the
character over it, as the rest screen's still layer does. No thread, nothing timed apart from the
frame loop.
"""
import time

from .canvas import W, H
from .characters import LOW

TOUR_STEPS = 8
TIPS = ("songs", "free", "lesson")
TIP_SECS = 8.0

BUBBLE = (255, 255, 255)
BUBBLE_INK = (29, 36, 51)
BUBBLE_SOFT = (61, 69, 87)
GOLD_BTN = (232, 176, 74)
SPOT_LINE = (232, 182, 79)
DIM = (3, 6, 14, 140)

CHAR_S = 0.85                     # the figure in the tour, of its size on the rest screen
CHAR_HALF_W, CHAR_TOP = 85, 128   # half its width and its top above its center (the chimney's notes too)
TIP_S = 0.42


class TourViews:
    # ------------------------------------------------------------------ state
    def init_tour_state(self):
        self.tour_step = 0
        self.tip = None                   # (name, since) while a tip shows
        self._tour_pics = {}

    def tour_due(self):
        return self.st.get("setup_done") and self.st.get("tour_done", True) is False

    def device_busy(self):
        s = self.screen
        return bool(s.lesson_on() or getattr(s, "alarm", False) or getattr(s, "song", False))

    def tour_on(self):
        """The tour shows now: due, on the rest screen, and nothing else has the device."""
        return bool(self.tour_due() and self.view == "rest" and not self.device_busy()
                    and time.time() >= self.screen.summary_until and time.time() >= self.off_until)

    def tour_first_setup(self):
        """The setup finished for the first time on this device: the tour and the tips are due."""
        self.tour_step = 0
        self.save(tour_done=False, tips_seen=[])

    def tour_again(self):
        """Settings › System › Opening tour: the tour from its first step, and the tips again."""
        self.tour_step, self.tip = 0, None
        self.save(tour_done=False, tips_seen=[])
        self.go("rest")

    def tour_next(self):
        if self.tour_step >= TOUR_STEPS - 1:
            self.tour_end()
        else:
            self.tour_step += 1
        self.layer_dirty()

    def tour_end(self):
        self.tour_step = 0
        self.save(tour_done=True)
        self.layer_dirty()

    # ------------------------------------------------------------------ input while the tour shows
    def tour_button(self, name, hold):
        """✓ goes on, ☰ skips; the other buttons do nothing under the tour."""
        if not hold:
            if name == "ok":
                self.tour_next()
            elif name == "menu":
                self.tour_end()

    def tour_tap(self, x, y):
        """A touch, in plan units of the physical screen: on the bubble it goes on (Skip skips),
        anywhere else the figure glances there and nothing under the tour is pressed."""
        lay = getattr(self, "_tour_lay", None)
        if lay:
            for (rx, ry, rw, rh), fn in lay["hits"]:
                if rx <= x <= rx + rw and ry <= y <= ry + rh:
                    fn()
                    return
        self.char.touch(x, y, self.t())

    # ------------------------------------------------------------------ layout
    def tour_spot(self, step):
        """The part outlined in a step, from where the rest screen really drew it (physical plan units)."""
        bars = getattr(self, "bar_rects", None) or []
        if step in (1, 5) and bars:                  # the 4 buttons: what they say, and what holding them does
            x0, y0 = bars[0][0], bars[0][1]
            x1 = bars[-1][0] + bars[-1][2]
            return (x0, y0, x1 - x0, bars[0][3])
        if step == 2:
            return getattr(self, "song_rect", None)
        if step == 6 and bars:
            return bars[0]                           # ☰: the leftmost button, in both languages
        return None

    def tour_text(self, step, card=True):
        name = self.tr("tour.n." + self.kind)
        if step == 2 and not card:
            return self.tr("tour.3new")
        return self.tr("tour.%d" % (step + 1), name=name)

    def tour_layout(self, step, card=True):
        """Where the figure and the bubble go for a step, in physical plan units: the figure on the
        reading side (left in English, right in Hebrew), the bubble beside it, both above the part
        outlined. Returns the figure's center and box, the bubble's box, its lines and buttons."""
        cv, he = self.cv, self.lang == "he"
        spot = self.tour_spot(step)
        cy = 300 if spot else 230
        cx = W - 120 if he else 120
        half = CHAR_HALF_W * CHAR_S
        cbox = (cx - half, cy - CHAR_TOP * CHAR_S, 2 * half, (CHAR_TOP + LOW.get(self.kind, 58)) * CHAR_S)
        gap, pad, size, lh = 22, 18, 17, 25
        bw = 460
        bx = cx - half - gap - bw if he else cx + half + gap
        lines = cv.wrap(self.tour_text(step, card), bw - 2 * pad, size)
        bh = pad + len(lines) * lh + 12 + 34 + 14
        by = cy - bh / 2 - 10
        if spot:
            by = min(by, spot[1] - 14 - bh)
        by = max(12, by)
        last = step == TOUR_STEPS - 1
        nxt = self.tr("tour.done") if last else self.tr("tour.next")
        nw = cv.measure(nxt, 15, True) + 32
        row = by + bh - 14 - 34
        skip = self.tr("tour.skip")
        sw = cv.measure(skip, 14, True) + 16
        if he:
            nbox = (bx + bw - pad - nw, row, nw, 34)
            sbox = (nbox[0] - 8 - sw, row, sw, 34)
        else:
            nbox = (bx + pad, row, nw, 34)
            sbox = (nbox[0] + nw + 8, row, sw, 34)
        return {"step": step, "spot": spot, "char": (cx, cy), "cbox": cbox, "bubble": (bx, by, bw, bh),
                "lines": lines, "size": size, "lh": lh, "pad": pad, "next": (nbox, nxt), "skip": (sbox, skip),
                "count": self.tr("tour.of", n=step + 1, k=TOUR_STEPS), "he": he}

    # ------------------------------------------------------------------ drawing
    def tour_draw(self, t):
        """One frame of the tour, or False when it does not show. Called by draw() first."""
        if not self.tour_on():
            return False
        cv = self.cv
        sw, sh = cv.surf.get_size()
        card = self.song_card_data()
        key = (self.tour_step, self.lang, self.kind, self.theme, int(self.now_ms() // 60000), bool(card),
               self.online(), id(cv))
        pic = self._tour_pics.get((sw, sh))
        if pic is None or pic["key"] != key:
            self.hits, self.bar_items = [], []
            self.draw_rest_layer(t)                  # the rest screen as it is, without the figure (rest_moving)
            lay = self.tour_layout(self.tour_step, bool(card))
            self.tour_paint(lay)
            pic = self._tour_pics[(sw, sh)] = {"key": key, "surf": cv.surf.copy(), "lay": lay}
        else:
            cv.surf.blit(pic["surf"], (0, 0))
        lay = pic["lay"]
        nbox, sbox, bub = lay["next"][0], lay["skip"][0], lay["bubble"]
        lay["hits"] = [(sbox, self.tour_end), (nbox, self.tour_next), (bub, self.tour_next)]
        self._tour_lay = lay
        self.hits, self.bar_items = [], []
        self.covered = False
        cx, cy = lay["char"]
        if t > self.char.look_hold:                  # the eyes on the bubble, unless a touch drew them away
            bx, by, bw, bh = bub
            self.char.look_t = [max(-1.0, min(1.0, (bx + bw / 2 - cx) / 260)), max(-1.0, min(1.0, (by + bh / 2 - cy) / 160))]
            self.char.look_hold = t + 0.5
        self.char.draw(cv, self.kind, cx, cy, t, CHAR_S)
        self.draw_toast()
        self.draw_confirm()
        self.finish_frame()
        return True

    def tour_paint(self, lay):
        """The dimming with its hole around the part explained, the outline and the bubble."""
        cv = self.cv
        x0, y0, x1, y1 = cv.vx, cv.vy, cv.vx + cv.vw, cv.vy + cv.vh
        with cv.physical():
            spot = lay["spot"]
            if spot:
                m = 4
                sx, sy, sw, sh = spot[0] - m, spot[1] - m, spot[2] + 2 * m, spot[3] + 2 * m
                cv.rect(x0, y0, x1 - x0, sy - y0, DIM)
                cv.rect(x0, sy + sh, x1 - x0, y1 - sy - sh, DIM)
                cv.rect(x0, sy, sx - x0, sh, DIM)
                cv.rect(sx + sw, sy, x1 - sx - sw, sh, DIM)
                cv.rect(sx, sy, sw, sh, SPOT_LINE, r=12, width=3)
            else:
                cv.rect(x0, y0, x1 - x0, y1 - y0, DIM)
            bx, by, bw, bh = lay["bubble"]
            cx, cy = lay["char"]
            he, pad = lay["he"], lay["pad"]
            # the tail points at the figure, from the bubble's side that faces it
            ty = max(by + 18, min(by + bh - 18, cy - 20))
            if he:
                cv.polygon([(bx + bw - 1, ty - 11), (bx + bw + 14, ty), (bx + bw - 1, ty + 11)], BUBBLE)
            else:
                cv.polygon([(bx + 1, ty - 11), (bx - 14, ty), (bx + 1, ty + 11)], BUBBLE)
            cv.rect(bx, by, bw, bh, BUBBLE, r=14)
            for i, line in enumerate(lay["lines"]):
                y = by + pad + 16 + i * lay["lh"]
                if he:
                    cv.text(line, bx + bw - pad, y, lay["size"], BUBBLE_INK, False, "right")
                else:
                    cv.text(line, bx + pad, y, lay["size"], BUBBLE_INK)
            (nx, ny, nw, nh), nxt = lay["next"]
            cv.rect(nx, ny, nw, nh, GOLD_BTN, r=9)
            cv.text(nxt, nx + nw / 2, ny + 23, 15, BUBBLE_INK, True, "center")
            (sx, sy, sw, sh), skip = lay["skip"]
            if lay["step"] < TOUR_STEPS - 1:
                cv.text(skip, sx + sw / 2, sy + 23, 14, BUBBLE_SOFT, True, "center")
                tw = cv.measure(skip, 14, True)
                cv.rect(sx + sw / 2 - tw / 2, sy + 26, tw, 1.5, BUBBLE_SOFT)
            if he:
                cv.text(lay["count"], bx + pad, ny + 23, 13, BUBBLE_SOFT)
            else:
                cv.text(lay["count"], bx + bw - pad, ny + 23, 13, BUBBLE_SOFT, False, "right")

    # ------------------------------------------------------------------ the tips
    def tip_scene(self):
        """Which tip this screen could show, or None."""
        s = self.screen
        if s.lesson_on():
            return "lesson" if time.time() >= s.summary_until else None
        if time.time() < s.summary_until:
            return None
        if self.view == "songs":
            return "songs" if s.songs() else None    # ▶ is on the songs' cards: none, no tip yet
        if self.view == "free":
            return None if self.touch_off() else "free"   # touch off: sliding plays nothing
        return None

    def tip_draw(self, t):
        """A tip's frame, called by draw() after the screen. A tip starts on the first look at its
        screen and is written seen at once, so it never comes back, after a restart either."""
        scene = self.tip_scene()
        s = self.screen
        if self.tip and (self.tip[0] != scene or t - self.tip[1] > TIP_SECS or getattr(s, "alarm", False)):
            self.tip = None
        if self.tip is None:
            seen = self.st.get("tips_seen")
            if (scene is None or not isinstance(seen, list) or scene in seen or self.tour_due() or self.confirm
                    or getattr(s, "alarm", False) or getattr(s, "song", False)):
                return
            self.tip = (scene, t)
            self.save(tips_seen=list(seen) + [scene])
        self.tip_paint(t)

    def tip_layout(self, name):
        """The small figure and its one line, above the buttons. In Songs and Free play on the reading
        side; in a lesson on the other side, so the lesson's own line (on the reading side) stays seen."""
        cv = self.cv
        start = name != "lesson"
        left = (self.lang != "he") == start
        text = self.tr("tip." + name)
        size = 15
        tw = cv.measure(text, size)
        half = CHAR_HALF_W * TIP_S
        cy = H - 58 - 8 - LOW.get(self.kind, 58) * TIP_S
        cx = 24 + half if left else W - 24 - half
        bw, bh = tw + 28, 36
        bx = cx + half + 10 if left else cx - half - 10 - bw
        by = cy - bh / 2 - 6
        cbox = (cx - half, cy - CHAR_TOP * TIP_S, 2 * half, (CHAR_TOP + LOW.get(self.kind, 58)) * TIP_S)
        return {"char": (cx, cy), "cbox": cbox, "bubble": (bx, by, bw, bh), "text": text, "size": size, "left": left}

    def tip_paint(self, t):
        cv = self.cv
        lay = self.tip_layout(self.tip[0])
        self._tip_lay = lay
        bx, by, bw, bh = lay["bubble"]
        cx, cy = lay["char"]
        with cv.physical():
            if lay["left"]:
                cv.polygon([(bx + 1, by + bh / 2 - 7), (bx - 9, by + bh / 2 + 2), (bx + 1, by + bh / 2 + 7)], BUBBLE)
            else:
                cv.polygon([(bx + bw - 1, by + bh / 2 - 7), (bx + bw + 9, by + bh / 2 + 2), (bx + bw - 1, by + bh / 2 + 7)], BUBBLE)
            cv.rect(bx, by, bw, bh, BUBBLE, r=12)
            cv.text(lay["text"], bx + bw / 2, by + 24, lay["size"], BUBBLE_INK, False, "center")
        if t > self.char.look_hold:
            self.char.look_t = [1.0 if lay["left"] else -1.0, -0.2]
            self.char.look_hold = t + 0.5
        self.char.draw(cv, self.kind, cx, cy, t, TIP_S)

    def tip_tap(self, x, y):
        """A touch while a tip shows: the tip goes. On the tip itself the touch ends there; anywhere
        else it does what it always does. Returns True when the touch was the tip's."""
        if not self.tip:
            return False
        lay = getattr(self, "_tip_lay", None)
        self.tip = None
        if not lay:
            return False
        for bx, by, bw, bh in (lay["bubble"], lay["cbox"]):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return True
        return False
