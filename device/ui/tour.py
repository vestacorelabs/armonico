"""tour.py - the opening tour and the one-time tips of the Armonico Pi screen.

The tour: after the setup finishes for the first time, the character chosen in the settings goes over
the device in 7 short steps, as in the book: it reaches out with a pen, loops around a part, and says a
line about it in a bubble while the rest of the screen is dimmed. The steps go through the screens: the
buttons and ☰ on the rest screen, ▶ in Songs, the lit key of a lesson, the keys of free play, the card
in Settings where the character is changed, and the figure itself, which ends the tour when touched.
✓ or a touch on the bubble goes on, ☰ goes a step back. The first tour has no Skip; the one started
again from the settings has. It waits while an alarm, a song or a lesson has the device, and goes on
from the same step when the rest screen is free again.

The tips: the first time Songs, Free play and a lesson open, the small character says one line in
a corner, for 8 seconds or until a touch. Each one once.

State, in the device's settings (settings.json, the same dict as everything else on this screen):
  tour_done   False while the tour is due; True once done or skipped
  tour_first  True for the tour after the first setup: no Skip
  tips_seen   the tips already shown: "songs", "free", "lesson"
A device set up before these existed has none of them: a missing key counts as done and seen, so
an update does not start the tour on a device in use. The first finish of the setup writes them
as due (a new install, and a full reset, which deletes the settings file). Settings › System ›
Opening tour writes them as due again.

Load: the step's whole picture (its screen, the dimming and the bubble) is drawn once per step into a
picture kept for the screen's size, and each frame copies it and draws over it the typed line, the
pen's loop, the arm and the character, as the rest screen's still layer does. No thread, nothing timed
apart from the frame loop.
"""
import math
import time

from .canvas import W, H
from .characters import LOW, Reaction
from .texts import T
from .widgets import BAR

TOUR_STEPS = 7
# each step: the screen it shows and the part the pen loops around
STEPS = (("rest", "bar"), ("rest", "menu"), ("songs", "play"), ("lesson", "lit"), ("free", "keys"),
         ("settings", "look"), ("rest", "me"))
TIPS = ("songs", "free", "lesson")
TIP_SECS = 8.0

BUBBLE = (255, 255, 255)
BUBBLE_INK = (29, 36, 51)
BUBBLE_SOFT = (61, 69, 87)
GOLD_BTN = (232, 176, 74)
PEN_MARK = (232, 182, 79)
DIM = (3, 6, 14, 140)
INK = (38, 45, 69)
PEN_BODY, PEN_CAP, PEN_TIP = (14, 42, 71), (232, 182, 79), (243, 244, 247)

CHAR_S = 0.85                     # the figure in the tour, of its size on the rest screen
CHAR_HALF_W, CHAR_TOP = 85, 128   # half its width and its top above its center (the chimney's notes too)
CORNER_S = 0.5                    # the figure in the corner, over a screen full of keys
TIP_S = 0.42
REACH, TRACE, BACK = 0.55, 1.0, 0.45   # seconds: the hand reaches out, the pen loops, the hand comes back
TYPE_CPS = 40                     # letters a second as the bubble's line is typed out
SIZES = (17, 16, 15, 14)          # the line's size: the largest that fits one row


def ease(u):
    return 2 * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 2 / 2


def loop_pts(r):
    """A rounded loop around a part, kept inside the screen: from its middle on the far side, all the way
    round and a little past the start, with a slight wobble of a hand."""
    x0, y0 = max(4.0, r[0] - 7), max(4.0, r[1] - 7)
    x1, y1 = min(W - 4.0, r[0] + r[2] + 7), min(H - 4.0, r[1] + r[3] + 7)
    R = min(16.0, (y1 - y0) / 2)
    pts = [(x1, (y0 + y1) / 2)]
    for cx, cy, a0 in ((x1 - R, y1 - R, 0), (x0 + R, y1 - R, math.pi / 2), (x0 + R, y0 + R, math.pi),
                       (x1 - R, y0 + R, math.pi * 1.5)):
        pts += [(cx + math.cos(a0 + i / 6 * math.pi / 2) * R, cy + math.sin(a0 + i / 6 * math.pi / 2) * R) for i in range(7)]
    pts.append((x1, (y0 + y1) / 2 + 6))
    return [(x + math.sin(i * 1.7) * 0.8, y + math.cos(i * 1.3) * 0.8) for i, (x, y) in enumerate(pts)]


def along(pts, u):
    """The first u (0 to 1) of a path, by length."""
    L = [0.0]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        L.append(L[-1] + math.hypot(bx - ax, by - ay))
    want, out = u * L[-1], [pts[0]]
    for i in range(1, len(pts)):
        if L[i] <= want:
            out.append(pts[i])
            continue
        k = (want - L[i - 1]) / max(1e-9, L[i] - L[i - 1])
        out.append((pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k))
        break
    return out


class TourViews:
    # ------------------------------------------------------------------ state
    def init_tour_state(self):
        self.tour_step = 0
        self.tour_at = 0.0               # when the step began: the hand and the typing follow it
        self.tip = None                   # (name, since) while a tip shows
        self._tour_pics = {}

    def tour_due(self):
        return self.st.get("setup_done") and self.st.get("tour_done", True) is False

    def tour_must(self):
        """The first tour after the setup has no Skip: it is short, and it is how the device is met."""
        return bool(self.st.get("tour_first"))

    def device_busy(self):
        s = self.screen
        return bool(s.lesson_on() or getattr(s, "alarm", False) or getattr(s, "song", False))

    def tour_on(self):
        """The tour shows now: due, on the rest screen, and nothing else has the device."""
        return bool(self.tour_due() and self.view == "rest" and not self.device_busy()
                    and time.time() >= self.screen.summary_until and time.time() >= self.off_until)

    def tour_first_setup(self):
        """The setup finished for the first time on this device: the tour and the tips are due."""
        self.tour_step, self.tour_at = 0, self.t()
        self.save(tour_done=False, tour_first=True, tips_seen=[])

    def tour_again(self):
        """Settings › System › Opening tour: the tour from its first step, and the tips again. This one can be skipped."""
        self.tour_step, self.tip, self.tour_at = 0, None, self.t()
        self.save(tour_done=False, tour_first=False, tips_seen=[])
        self.go("rest")

    def tour_go(self, d):
        k = self.tour_step + d
        if k < 0:
            return
        if k >= TOUR_STEPS:
            self.tour_end()
            return
        self.tour_step, self.tour_at = k, self.t()
        self.layer_dirty()

    def tour_next(self):
        self.tour_go(1)

    def tour_end(self):
        self.tour_step = 0
        self.save(tour_done=True, tour_first=False)
        self.layer_dirty()

    # ------------------------------------------------------------------ input while the tour shows
    def tour_button(self, name, hold):
        """✓ goes on, ☰ goes back a step; the other buttons do nothing under the tour."""
        if not hold:
            if name == "ok":
                self.tour_next()
            elif name == "menu":
                self.tour_go(-1)

    def tour_tap(self, x, y):
        """A touch, in plan units of the physical screen: on the bubble it goes on (Skip skips); on the
        figure in the last step it laughs and the tour is over; anywhere else the figure glances there and
        nothing under the tour is pressed."""
        lay = getattr(self, "_tour_lay", None)
        if lay:
            for (rx, ry, rw, rh), fn in lay["hits"]:
                if rx <= x <= rx + rw and ry <= y <= ry + rh:
                    fn()
                    return
            if STEPS[lay["step"]][1] == "me":
                bx, by, bw, bh = lay["cbox"]
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    t = self.t()
                    self.char.react = Reaction("happy", t + 1.3)
                    self.char.move, self.char.move_t = "jump", t
                    self.tour_end()
                    return
        self.char.touch(x, y, self.t())

    # ------------------------------------------------------------------ the screens under the steps
    def tour_scene(self, step, t):
        """Draws the screen of a step as it really looks (the rest screen, Songs, free play, Settings), or a
        lesson as it looks in its first round, and returns the part to loop around, in physical plan units."""
        cv, he = self.cv, self.lang == "he"
        scene, part = STEPS[step]

        def phys(x, y, w, h):
            return (W - x - w, y, w, h) if he else (x, y, w, h)
        if scene == "rest":
            self.draw_rest_layer(t)                  # the rest screen as it is, without the figure (rest_moving)
            bars = self.bar_rects or []
            if part == "bar" and bars:
                x0, y0 = bars[0][0], bars[0][1]
                return (x0, y0, bars[-1][0] + bars[-1][2] - x0, bars[0][3])
            if part == "menu" and bars:
                return bars[0]                       # ☰: the leftmost button, in both languages
            return None
        self.background(t)
        if scene == "songs":
            self.view_songs(t)
            if self.screen.songs():
                return phys(W - 326, 66 + 4, 52, 50)     # ▶ on the first card
            return phys(24, 66, W - 48, 62)              # no songs: Search online
        if scene == "free":
            self.view_free(t)
            rects = getattr(self, "key_rects", None) or []
            if not rects:
                return None
            x0 = min(r[1] for r in rects)
            x1 = max(r[1] + r[3] for r in rects)
            y0 = min(r[2] for r in rects)
            return (x0, y0, x1 - x0, max(r[2] + r[4] for r in rects) - y0)
        if scene == "lesson":
            # the first round of a lesson: the next key lit, the finger's number over it
            self.hills(0.45)
            card = self.song_card_data() if hasattr(self, "song_card_data") else None
            from .views_main import song_title
            name = song_title(card["name"]) if card else "Ode to Joy"
            self.header(self.tr("lesson") + " · " + name, self.tr("st.numbers"))
            rects = self.keys(172, 4, finger="3")
            self.bar([("■", self.tr("leave"), lambda: None)])
            r = next((r for r in rects if r[0] == 4), None)
            return (r[1] - 4, r[2] - 46, r[3] + 8, r[4] + 50) if r else None
        if scene == "settings":
            page, sel = getattr(self, "page", "top"), getattr(self, "sel", 0)
            self.page, self.sel = "top", 0
            try:
                self.view_settings(t)
            finally:
                self.page, self.sel = page, sel
            cols, rows, w, h, gap = self.grid_geom()
            return phys(24, 58, w, h)
        return None

    # ------------------------------------------------------------------ layout
    def tour_text(self, step):
        kind, scene = self.kind, STEPS[step][0]
        n = step + 1
        if scene == "songs" and not self.screen.songs():
            return self.tr("tour.3none")
        key = f"tour.{n}.{kind}" if f"tour.{n}.{kind}" in T else f"tour.{n}"
        return self.tr(key)

    def tour_place(self, step):
        """Where the figure stands in a step, and its size: on the reading side (right in Hebrew, left in
        English); in a corner, small, over a screen full of keys; across from Settings' first card."""
        he, part = self.lang == "he", STEPS[step][1]
        right = he
        if part in ("lit", "keys"):
            return (W - 62 if right else 62), 80, CORNER_S, right
        if part == "look":
            right = not he
            return (W - 120 if right else 120), 330, CHAR_S, right
        return (W - 120 if right else 120), 300, CHAR_S, right

    def tour_layout(self, step, spot):
        """The figure, the bubble beside it on the inner side, one line in the bubble, and its buttons, in
        physical plan units. The bubble keeps clear of the part: above it, or under it when the part is high."""
        cv, he = self.cv, self.lang == "he"
        cx, cy, s, right = self.tour_place(step)
        half = CHAR_HALF_W * s
        cbox = (cx - half, cy - CHAR_TOP * s, 2 * half, (CHAR_TOP + LOW.get(self.kind, 58)) * s)
        gap, pad, lh = 22, 18, 25
        room = (cx - half - gap - 12) if right else (W - 12 - (cx + half + gap))
        text = self.tour_text(step)
        size = next((z for z in SIZES if cv.measure(text, z, True) <= room - 2 * pad), SIZES[-1])
        tw = cv.measure(text, size, True)
        last = step == TOUR_STEPS - 1
        nxt = self.tr("tour.done") if last else self.tr("tour.next")
        nw = cv.measure(nxt, 15, True) + 32
        skip = None if self.tour_must() or last else self.tr("tour.skip")
        sw = cv.measure(skip, 14, True) + 16 if skip else 0
        count = self.tr("tour.of", n=step + 1, k=TOUR_STEPS)
        bw = min(room, max(tw, nw + sw + 8 + cv.measure(count, 13) + 24) + 2 * pad)
        bh = pad + lh + 12 + 34 + 14
        bx = cx - half - gap - bw if right else cx + half + gap
        by = cy - bh / 2 - 10
        if step in (3, 4):
            by = 14                                  # over the keys: in the free top of the screen, beside the figure
        elif spot:
            by = min(by, spot[1] - 18 - bh) if spot[1] > 200 else max(by, spot[1] + spot[3] + 18)
        by = max(12, min(H - BAR - bh - 4, by))
        row = by + bh - 14 - 34
        if he:
            nbox = (bx + bw - pad - nw, row, nw, 34)
            sbox = (nbox[0] - 8 - sw, row, sw, 34) if skip else None
        else:
            nbox = (bx + pad, row, nw, 34)
            sbox = (nbox[0] + nw + 8, row, sw, 34) if skip else None
        ring = cbox if STEPS[step][1] == "me" else None
        return {"step": step, "spot": spot, "ring": ring, "char": (cx, cy), "s": s, "right": right, "cbox": cbox,
                "bubble": (bx, by, bw, bh), "lines": [text], "size": size, "lh": lh, "pad": pad,
                "next": (nbox, nxt), "skip": (sbox, skip) if skip else None, "count": count, "he": he}

    # ------------------------------------------------------------------ drawing
    def tour_draw(self, t):
        """One frame of the tour, or False when it does not show. Called by draw() first. The screen of the
        step, the dimming and the bubble are drawn once a step into a picture; each frame copies it, then
        types the line, moves the hand with the pen and draws the figure."""
        if not self.tour_on():
            return False
        cv = self.cv
        sw, sh = cv.surf.get_size()
        key = (self.tour_step, self.lang, self.kind, self.theme, int(self.now_ms() // 60000),
               bool(self.screen.songs()), self.online(), id(cv), self.tour_must())
        pic = self._tour_pics.get((sw, sh))
        if pic is None or pic["key"] != key:
            self.hits, self.bar_items = [], []
            spot = self.tour_scene(self.tour_step, t)
            lay = self.tour_layout(self.tour_step, spot)
            self.tour_paint(lay)
            pic = self._tour_pics[(sw, sh)] = {"key": key, "surf": cv.surf.copy(), "lay": lay}
        else:
            cv.surf.blit(pic["surf"], (0, 0))
        if getattr(self, "_tour_seen", None) != self.tour_step:
            self._tour_seen, self.tour_at = self.tour_step, t     # a new step: the hand and the typing start over
        lay = pic["lay"]
        nbox, bub = lay["next"][0], lay["bubble"]
        lay["hits"] = ([(lay["skip"][0], self.tour_end)] if lay["skip"] else []) + [(nbox, self.tour_next), (bub, self.tour_next)]
        self._tour_lay = lay
        self.hits, self.bar_items = [], []
        self.covered = False
        el = max(0.0, t - self.tour_at)
        tip = self.tour_pen(lay, el)
        cx, cy = lay["char"]
        if t > self.char.look_hold:                  # the eyes on the pen, or the bubble, unless a touch drew them away
            bx, by, bw, bh = bub
            lx, ly = tip if tip else (bx + bw / 2, by + bh / 2)
            self.char.look_t = [max(-1.0, min(1.0, (lx - cx) / 260)), max(-1.0, min(1.0, (ly - cy) / 160))]
            self.char.look_hold = t + 0.2
        with cv.physical():
            n = min(len(lay["lines"][0]), int(el * TYPE_CPS) + 1)
            bx, by, bw, bh = bub
            line = lay["lines"][0][:n]
            if lay["he"]:
                cv.text(line, bx + bw - lay["pad"], by + lay["pad"] + 16, lay["size"], BUBBLE_INK, True, "right")
            else:
                cv.text(line, bx + lay["pad"], by + lay["pad"] + 16, lay["size"], BUBBLE_INK, True)
        self.char.draw(cv, self.kind, cx, cy, t, lay["s"])
        if tip:
            self.tour_arm(lay, tip)
        self.draw_toast()
        self.draw_confirm()
        self.finish_frame()
        return True

    def tour_pen(self, lay, el):
        """The pen's mark so far, drawn; returns where its tip is now, or None once the hand is back."""
        target = lay["spot"] or lay["ring"]
        if not target:
            return None
        pts = loop_pts(target)
        cx, cy = lay["char"]
        half = CHAR_HALF_W * lay["s"]
        rest = (cx - half - 14, cy - 14) if lay["right"] else (cx + half + 14, cy - 14)
        u = (el - REACH) / TRACE
        if u > 0:
            with self.cv.physical():
                self.cv.polyline(along(pts, min(1.0, u)), PEN_MARK, 4)
        if el < REACH:
            k = ease(el / REACH)
            return (rest[0] + (pts[0][0] - rest[0]) * k, rest[1] + (pts[0][1] - rest[1]) * k)
        if u < 1:
            return along(pts, u)[-1]
        back = el - REACH - TRACE - 0.25
        if back < BACK:
            k = ease(max(0.0, back) / BACK)
            e = pts[-1]
            return (e[0] + (rest[0] - e[0]) * k, e[1] + (rest[1] - e[1]) * k)
        return None

    def tour_arm(self, lay, tip):
        """The figure's arm from its side to the pen it holds, the tip at the point given."""
        cv, s = self.cv, lay["s"]
        cx, cy = lay["char"]
        half = CHAR_HALF_W * s
        sh = (cx - half + 6, cy + 8 * s) if lay["right"] else (cx + half - 6, cy + 8 * s)
        ang = math.atan2(tip[1] - sh[1], tip[0] - sh[0])
        hand = (tip[0] - math.cos(ang) * 40 * s, tip[1] - math.sin(ang) * 40 * s)
        mid = ((sh[0] + hand[0]) / 2, (sh[1] + hand[1]) / 2 + 18 * s)
        arm = [((1 - u) ** 2 * sh[0] + 2 * (1 - u) * u * mid[0] + u * u * hand[0],
                (1 - u) ** 2 * sh[1] + 2 * (1 - u) * u * mid[1] + u * u * hand[1]) for u in (i / 12 for i in range(13))]
        with cv.physical():
            cv.polyline(arm, INK, 5 * s)
            ps = s * 0.85
            c, n = math.cos(ang), math.sin(ang)

            def at(x, y):                            # a point of the pen, its tip at (0, 0), lying along the arm
                return (tip[0] + (x * c - y * n) * ps, tip[1] + (x * n + y * c) * ps)
            cv.polygon([at(-60, -4), at(-14, -4), at(-14, 4), at(-60, 4)], PEN_BODY)
            cv.polygon([at(-60, -4), at(-48, -4), at(-48, 4), at(-60, 4)], PEN_CAP)
            cv.polygon([at(-56, -7.5), at(-41, -7.5), at(-41, -5.1), at(-56, -5.1)], PEN_CAP)
            cv.polygon([at(0, 0), at(-14, -4), at(-14, 4)], PEN_TIP)
            cv.circle(*at(0, 0), 1.6 * ps, INK)
            cv.circle(hand[0], hand[1], 6.5 * s, (255, 255, 255))
            cv.circle(hand[0], hand[1], 6.5 * s, INK, width=2 * s)

    def tour_paint(self, lay):
        """The dimming with its hole around the part explained, the bubble and its buttons (the line is typed
        over it on every frame)."""
        cv = self.cv
        x0, y0, x1, y1 = cv.vx, cv.vy, cv.vx + cv.vw, cv.vy + cv.vh
        with cv.physical():
            spot = lay["spot"]
            if spot:
                m = 5
                sx, sy, sw, sh = spot[0] - m, spot[1] - m, spot[2] + 2 * m, spot[3] + 2 * m
                cv.rect(x0, y0, x1 - x0, sy - y0, DIM)
                cv.rect(x0, sy + sh, x1 - x0, y1 - sy - sh, DIM)
                cv.rect(x0, sy, sx - x0, sh, DIM)
                cv.rect(sx + sw, sy, x1 - sx - sw, sh, DIM)
            else:
                cv.rect(x0, y0, x1 - x0, y1 - y0, DIM)
            bx, by, bw, bh = lay["bubble"]
            cx, cy = lay["char"]
            pad = lay["pad"]
            # the tail points at the figure, from the bubble's side that faces it
            ty = max(by + 18, min(by + bh - 18, cy - 20))
            if lay["right"]:
                cv.polygon([(bx + bw - 1, ty - 11), (bx + bw + 14, ty), (bx + bw - 1, ty + 11)], BUBBLE)
            else:
                cv.polygon([(bx + 1, ty - 11), (bx - 14, ty), (bx + 1, ty + 11)], BUBBLE)
            cv.rect(bx, by, bw, bh, BUBBLE, r=14)
            (nx, ny, nw, nh), nxt = lay["next"]
            cv.rect(nx, ny, nw, nh, GOLD_BTN, r=9)
            cv.text(nxt, nx + nw / 2, ny + 23, 15, BUBBLE_INK, True, "center")
            if lay["skip"]:
                (sx, sy, sw, sh), skip = lay["skip"]
                cv.text(skip, sx + sw / 2, sy + 23, 14, BUBBLE_SOFT, True, "center")
                tw = cv.measure(skip, 14, True)
                cv.rect(sx + sw / 2 - tw / 2, sy + 26, tw, 1.5, BUBBLE_SOFT)
            if lay["he"]:
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
