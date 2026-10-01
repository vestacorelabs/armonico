"""characters.py - the little figure on the rest screen of the Armonico Pi device.

Four kinds, drawn the same way as the approved demo: 'house' (a house with a chimney that puffs
notes), 'keys' (the logo house, its wall made of piano keys), 'grand' (a small grand piano with a
bow tie) and 'metro' (a metronome with a swinging arm).

The eyes are alive: the figure glances around on its own now and then, and after a touch it looks
toward where the screen was touched for a few seconds. Every touch makes it hop. A touch on the
figure itself makes it laugh (^ ^ eyes); 5 quick touches make it dizzy (spinning eyes, a wobble).
A key press makes it glance at that key.

Time is always given by the caller, in seconds, so the same calls give the same picture (tests).
Touches are in plan units of the physical screen (cv.to_plan), since the figure is drawn inside
cv.physical() and never mirrors.
"""
import math
import random

INK = (38, 45, 69)
GOLD = (232, 182, 79)
SKYBLUE = (143, 205, 246)
WHITE = (243, 244, 247)
PURE = (255, 255, 255)
GRAND_BODY = (47, 56, 86)
BOW = (217, 72, 95)
SHADES = (17, 21, 34)

KINDS = ("house", "keys", "grand", "metro")
# lowest point of each kind below its center (plus 2 of bob), so the screen keeps it clear of what is under it
LOW = {"house": 46, "keys": 52, "grand": 58, "metro": 52}

OWL_TEXT = {"en": "Still not asleep?", "he": "עוד לא ישנת?"}

HAPPY_FOR, DIZZY_FOR, TAP_WINDOW, DIZZY_TAPS = 1.3, 2.6, 2.5, 5
LOOK_HOLD, KEY_HOLD, OCT_HOLD = 2.8, 1.0, 1.5


def clamp(v, a, b):
    return max(a, min(b, v))


class Reaction:
    def __init__(self, kind, until):
        self.kind, self.until = kind, until


class Character:
    def __init__(self, seed=None):
        self.rnd = random.Random(seed)
        self.look = [0.0, 0.0]          # where the eyes look now, -1..1 each way
        self.look_t = [0.0, 0.0]        # where they are heading
        self.look_hold = 0.0            # until then the eyes keep looking where they were sent
        self.next_glance = 0.0
        self.react = None
        self.hop_t = -9.0
        self.taps = []
        self.last_t = 0.0
        self.cool_until = 0.0           # Easter egg: sunglasses while t < cool_until
        self.owl = None                 # playing at 3 a.m.: the time the bubble came up
        self.last = (400.0, 300.0, 1.0)  # (x, y, s) of the last draw, for touches and the owl bubble
        self.nap = False                # drawn for the sleep screen: eyes shut, nothing moving (draw_sleeping)

    # ---- state
    def update(self, t):
        """Once per frame: a glance now and then, the eyes eased toward their target."""
        dt = clamp(t - self.last_t, 0.0, 0.1)
        self.last_t = t
        if t > self.look_hold and t > self.next_glance:
            r = self.rnd.random()
            if r < 0.4:
                self.look_t = [0.0, 0.0]
            else:
                self.look_t = [self.rnd.random() * 2 - 1, self.rnd.random() * 1.6 - 0.8]
            self.next_glance = t + 1.6 + self.rnd.random() * 3
        k = min(1.0, dt * 12)
        self.look[0] += (self.look_t[0] - self.look[0]) * k
        self.look[1] += (self.look_t[1] - self.look[1]) * k
        if self.react and t > self.react.until:
            self.react = None

    def reaction(self, t):
        """'happy', 'dizzy' or None at time t."""
        return self.react.kind if self.react and t <= self.react.until else None

    def box_hit(self, x, y):
        """Is (x, y) on the figure, as it was last drawn?"""
        cx, cy, s = self.last
        dx, dy = x - cx, y - cy
        return abs(dx) < 85 * s and -95 * s < dy < 65 * s

    def touch(self, x, y, t):
        """A touch anywhere: a glance there. On the figure only: a hop and a laugh, or after 5 quick
        ones, dizzy. Every further touch keeps the reaction going, so it ends a while after the last touch.
        (It used to hop at a touch anywhere on the screen.)"""
        self.owl = None
        cx, cy, s = self.last
        dx, dy = x - cx, y - cy
        self.look_t = [clamp(dx / 260, -1, 1), clamp(dy / 160, -1, 1)]
        self.look_hold = t + LOOK_HOLD
        if not self.box_hit(x, y):
            return False
        self.hop_t = t
        self.taps = [q for q in self.taps if t - q < TAP_WINDOW] + [t]
        self.look_t = [0.0, 0.0]
        R = self.reaction(t)
        if R == "dizzy":
            self.react.until = t + DIZZY_FOR
        elif len(self.taps) >= DIZZY_TAPS:
            self.react = Reaction("dizzy", t + DIZZY_FOR)
        else:
            self.react = Reaction("happy", t + HAPPY_FOR)
        return True

    def key(self, pc, t):
        """One of the keys (0..12, left to right, 12 the high C) was pressed: a glance down toward it."""
        self.look_t = [min(1.0, (pc - 5.5) / 6), 0.6]
        self.look_hold = t + KEY_HOLD

    def octave(self, d, t):
        """The octave moved: a glance down (lower) or up (higher)."""
        self.look_t = [0.0, 1.0 if d < 0 else -1.0]
        self.look_hold = t + OCT_HOLD

    def start_owl(self, t):
        self.owl = t

    def stop_owl(self):
        self.owl = None

    def asleep(self, t):
        """Eyes closed: 2 s into the owl bubble, and then until a touch."""
        return self.owl is not None and t - self.owl > 2

    # ---- drawing
    def draw(self, cv, kind, x, y, t, s=1.0):
        """The figure centered at plan (x, y), s times its plan size."""
        self.last = (x, y, s)
        R = self.reaction(t)
        ht = t - self.hop_t
        hop = -14 * math.sin(math.pi * ht / 0.4) if 0 <= ht < 0.4 else 0.0
        bob = 0.0                    # no idle up and down: the figure stands still until touched
        wob = math.sin(t * 9) * 0.07 if R == "dizzy" else 0.0
        sq = 1 + 0.05 * math.sin(t * 22) if R == "happy" else 1.0
        with cv.physical(), cv.at(x, y + (bob + hop) * s, s, wob):
            cv.scale_by(1 / sq, sq)
            getattr(self, "_" + (kind if kind in KINDS else "house"))(cv, t, R)

    def _pair(self, cv, t, R, pts, k, draw, shut, col=INK, lx=None, ly=None):
        """Every pair of eyes: k is how far the pupils may travel, draw(x, y, w) a normal eye."""
        lx = self.look[0] if lx is None else lx
        ly = self.look[1] if ly is None else ly
        blink = (t % 4.2) < 0.13
        for x, y, w in pts:
            if self.nap:
                shut(x, y, w)
            elif R == "happy":
                cv.arc(x, y + w * 0.35, w * 0.55, math.pi * 1.15, math.pi * 1.85, col, 3.5)
            elif R == "dizzy":
                spin = 1 if x < 0 else -1
                p, a = [], 0.0
                while a < 12.6:
                    r2 = a / 12.6 * w * 0.6
                    an = a + t * 8 * spin
                    p.append((x + math.cos(an) * r2, y + math.sin(an) * r2))
                    a += 0.3
                # the turns are close: a hair thinner than 2.4, as the polygon edges are smoothed outward
                cv.polyline(p, col, max(1.0, 2.4 - 0.9 / max(cv.k(), 1e-6)))
            elif self.cool_until > t:
                gw, gh = w * 1.25, w * 0.75
                cv.rect(x - gw / 2, y - gh / 2, gw, gh, SHADES, r=gh / 2.5)
                cv.rect(x - gw / 4, y - gh / 4, gw / 5, 2, (255, 255, 255, 89))
                if x < 0:                                # the bridge between the two glasses
                    cv.line(x + gw / 2, y - 2, -x - gw / 2, y - 2, SHADES, 3)
            elif blink or self.asleep(t):
                shut(x, y, w)
            else:
                draw(x + lx * k, y + ly * k * 0.8, w)

    @staticmethod
    def _bar(cv):
        return lambda x, y, w: cv.rect(x - w / 2 - 1, y - 1, w + 2, 3, INK)

    @staticmethod
    def _pill(cv, h, w):
        return lambda x, y, _w: cv.rect(x - w / 2, y - h / 2, w, h, INK, r=5)

    @staticmethod
    def _dot(cv, r):
        return lambda x, y, _w: cv.circle(x, y, r, INK)

    @staticmethod
    def _text(cv, s, x, y, size, color, bold=True, align="center"):
        """Text at a point of the figure. cv.text follows only the translation, so the point and
        the size go through the whole transform here (the figure may be scaled)."""
        a, b, c, d, e, f = cv.m
        X, Y, k = a * x + c * y + e, b * x + d * y + f, math.hypot(a, b)
        cv.save()
        cv.m = [1, 0, 0, 1, 0, 0]
        try:
            return cv.text(s, X, Y, size * k, color, bold, align)
        finally:
            cv.restore()

    def _note(self, cv, x, y, a, size=15, col=GOLD):
        """A ♪ with a soft dark edge. Size and alpha are rounded so the canvas text cache holds them."""
        a = round(clamp(a, 0, 1) * 10) / 10
        if a <= 0:
            return
        size = round(size)
        edge = INK + (int(255 * 0.3 * a),)
        for ox, oy in ((-1.2, 0), (1.2, 0), (0, -1.2), (0, 1.2)):
            self._text(cv, "♪", x + ox, y + oy, size, edge)
        self._text(cv, "♪", x, y, size, col + (int(255 * a),) if a < 1 else col)

    def _house(self, cv, t, R):
        # a chimney puffing little notes, faster when it laughs; a calm smile
        cv.rect(26, -76, 13, 22, GOLD, r=3)
        sp = 0.9 if R == "happy" else 0.32
        for k in range(0 if self.nap else 3):
            p = (t * sp + k / 3) % 1
            self._note(cv, 33 + math.sin(p * 7 + k) * 7 + p * 14, -80 - p * 46,
                       (1 - p) * (p / 0.12 if p < 0.12 else 1), 16 + p * 8)
        cv.polyline([(-74, -30), (0, -84), (74, -30)], GOLD, 7)
        cv.polyline([(-56, -20), (0, -66), (56, -20)], SKYBLUE, 6)
        cv.rect(-52, -20, 104, 66, WHITE, r=12)
        self._pair(cv, t, R, [(-24, 2, 22), (24, 2, 22)], 7, self._pill(cv, 28, 11), self._bar(cv))
        if R == "happy":
            cv.polygon([(9 * math.cos(math.pi * i / 16), 26 + 9 * math.sin(math.pi * i / 16)) for i in range(17)], INK)
        else:
            cv.arc(0, 25, 8, 0.25, math.pi - 0.25, INK, 3)

    def _keys(self, cv, t, R):
        # the logo house: its wall is 4 white keys with 3 black keys between them, the face on the two middle keys
        cv.polyline([(-74, -32), (0, -86), (74, -32)], GOLD, 7)
        cv.rect(-56, -26, 112, 78, INK, r=12)
        dip = max(0.0, math.sin(t * 14)) * 3 if R == "happy" else 0.0
        # the outer keys follow the wall's round corners on their outer side
        cv.rect(-55, -26, 26, 78, WHITE, r=11)
        cv.rect(-41, -26, 12, 78, WHITE)
        cv.rect(29, -26, 26, 78, WHITE, r=11)
        cv.rect(29, -26, 12, 78, WHITE)
        cv.rect(-27, -26 + dip, 26, 78 - dip, WHITE)
        cv.rect(1, -26 + dip, 26, 78 - dip, WHITE)
        for x in (-28, 0, 28):                           # black keys: square at the top, round at the bottom
            cv.rect(x - 7, -26, 14, 36, INK, r=3)
            cv.rect(x - 7, -26, 14, 6, INK)
        self._pair(cv, t, R, [(-14, 24 + dip, 12), (14, 24 + dip, 12)], 4, self._dot(cv, 5), self._bar(cv))
        if R == "happy":
            cv.arc(0, 36 + dip, 8, 0.1, math.pi - 0.1, INK, 3)
        else:
            cv.arc(0, 37, 6, 0.25, math.pi - 0.25, INK, 3)

    def _grand(self, cv, t, R):
        # googly white eyes and a red bow tie, like a pianist on stage; the lid lifts now and then
        lift = max(0.0, math.sin(t * 1.3)) * 6 + (8 if R == "happy" else 0)
        cv.polygon([(-62, -14), (54, -14), (30, -58 - lift)], INK)
        cv.line(10, -14, 22, -46 - lift * 0.7, INK, 3)
        cv.rect(-64, -16, 128, 52, GRAND_BODY, r=16)
        cv.rect(-56, 16, 112, 16, WHITE, r=4)
        for x in (-44, -30, -8, 6, 20, 42):
            cv.rect(x, 16, 7, 9, INK)
        cv.rect(-52, 36, 8, 22, INK)
        cv.rect(44, 36, 8, 22, INK)
        bw = 1.25 if R == "happy" else 1.0
        cv.polygon([(0, 44), (-13 * bw, 37), (-13 * bw, 51)], BOW)
        cv.polygon([(0, 44), (13 * bw, 37), (13 * bw, 51)], BOW)
        cv.circle(0, 44, 3.5, BOW)
        if R not in ("happy", "dizzy"):
            cv.circle(-18, 0, 10, PURE)
            cv.circle(18, 0, 10, PURE)
        self._pair(cv, t, R, [(-18, 0, 16), (18, 0, 16)], 4.5, self._dot(cv, 5),
                   lambda x, y, w: cv.rect(x - 7, y - 1, 14, 3, INK), PURE)

    def _metro(self, cv, t, R):
        # when nobody touched it lately, its eyes follow its own swinging arm, tick, tock
        cv.polygon([(-46, 52), (-16, -70), (16, -70), (46, 52)], GOLD)
        cv.polygon([(-28, 40), (-10, -48), (10, -48), (28, 40)], WHITE)
        sp = 9 if R == "dizzy" else 2.4
        a = math.sin(t * sp) * 0.42
        lx = ly = None
        if not R and t > self.look_hold:
            lx, ly = math.sin(t * sp) * 0.9, -0.6
        cv.save()
        cv.translate(0, 34)
        cv.rotate(a)
        cv.line(0, 0, 0, -86, INK, 4)
        cv.rect(-8, -66, 16, 12, SKYBLUE, r=3)
        cv.restore()
        self._pair(cv, t, R, [(-13, 1, 14), (13, 1, 14)], 3, self._pill(cv, 18, 8), self._bar(cv), lx=lx, ly=ly)
        cv.arc(0, 20, 7, 0.3, math.pi - 0.3, INK, 3)

    def draw_sleeping(self, cv, kind, x, y, s=1.0, breath=1.0):
        """The figure asleep, for the sleep screen: the eyes shut as short lines, the metronome's arm
        still, no notes from the chimney. breath (1.0 and a little over) stretches it upward from its
        feet, for a slow breathing. It never reacts and never looks around."""
        low = LOW.get(kind, 50) - 2
        self.nap = True
        try:
            with cv.physical(), cv.at(x, y + low * s, s):
                cv.scale_by(1, breath)
                cv.translate(0, -low)
                getattr(self, "_" + (kind if kind in KINDS else "house"))(cv, 0.0, None)
        finally:
            self.nap = False

    # ---- the owl Easter egg
    def draw_owl(self, cv, lang, t):
        """Playing at 3 a.m.: "still not asleep?" for 4 s, then Z Z Z rising until a touch."""
        if self.owl is None:
            return
        cx, cy, s = self.last
        k = t - self.owl
        with cv.physical():
            if k < 4:
                label = OWL_TEXT["he" if lang == "he" else "en"]
                w = cv.measure(label, 14 * s, True) + 30 * s
                x, y = cx + 60 * s, cy - 130 * s
                cv.rect(x, y, w, 34 * s, PURE, r=17 * s)
                cv.polygon([(x + 14 * s, y + 30 * s), (x + 4 * s, y + 46 * s), (x + 28 * s, y + 32 * s)], PURE)
                cv.text(label, x + w / 2, y + 22 * s, 14 * s, INK, True, "center")
            else:
                for i in range(3):
                    p = (t * 0.5 + i / 3) % 1
                    a = round((1 - p) * 10) / 10
                    if a <= 0:
                        continue
                    cv.text("Z", cx + (50 + p * 40) * s, cy - (70 + p * 60) * s, round((14 + p * 14) * s),
                            PURE + (int(255 * a),) if a < 1 else PURE, True)
