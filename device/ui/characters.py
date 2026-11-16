"""characters.py - the little figure on the rest screen of the Armonico Pi device.

Three kinds, drawn the same way as the approved demo: 'house' (a house with a chimney that puffs
notes, the default), 'keys' (the logo house, its wall made of piano keys) and 'grand' (the grand from
the clip, seen 3/4, with a bow tie: its lid opens all the way now and then, and its own keys light as it
plays the echo game and as keys are played). The metronome ('metro') was taken out: its code stays below as comments, and a
setting that still names it is shown as the house.

The eyes are alive: the figure glances around on its own now and then, and after a touch it looks
toward where the screen was touched for a few seconds. A touch on the figure moves it, each time in
another way (MOVES), and the screen has it say a line (talk.py); quick touches make it laugh (^ ^ eyes),
and the fifth quick one spins it round, dizzy after (spirals, a wobble). Its face follows what it says
(MOODS): smug, angry, offended, surprised, proud or sleepy. A key press makes it glance at that key.

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

KINDS = ("house", "keys", "grand")   # , "metro": taken out, kept as comments
# lowest point of each kind below its center (plus 2 of bob), so the screen keeps it clear of what is under it
LOW = {"house": 46, "keys": 52, "grand": 58}   # , "metro": 52

OWL_TEXT = {"en": "Still not asleep?", "he": "עוד לא ישנת?"}

HAPPY_FOR, DIZZY_FOR, TAP_WINDOW, DIZZY_TAPS = 1.3, 2.6, 2.5, 5
QUICK = 0.7          # seconds: a touch this soon after the last one is a quick one, and makes it laugh
SPIN_FOR = 0.95      # seconds of the whole turn at the fifth quick touch
LOOK_HOLD, KEY_HOLD, OCT_HOLD = 2.8, 1.0, 1.5

# the faces it makes besides laughing and being dizzy: set by what it says (talk.py) and by the echo game
MOODS = ("smug", "angry", "offended", "surprised", "proud", "sleepy")

# every touch on the figure moves it in another way: each move is f(u) for u from 0 to 1 over its time,
# giving any of dx, dy (plan units), rot (radians), sx, sy (scale)
MOVES = {
    "hop": (0.42, lambda u: {"dy": -16 * math.sin(math.pi * u)}),
    "squash": (0.5, lambda u: {"sx": 1 + 0.14 * math.sin(math.pi * u), "sy": 1 - 0.12 * math.sin(math.pi * u)}),
    "lean_l": (1.1, lambda u: {"rot": -0.16 * math.sin(math.pi * u), "dx": -6 * math.sin(math.pi * u)}),
    "lean_r": (1.1, lambda u: {"rot": 0.16 * math.sin(math.pi * u), "dx": 6 * math.sin(math.pi * u)}),
    "bow": (0.9, lambda u: {"sy": 1 - 0.18 * math.sin(math.pi * u), "dy": 8 * math.sin(math.pi * u)}),
    "jump": (0.7, lambda u: {"dy": -34 * math.sin(math.pi * u), "sx": 1 - 0.06 * math.sin(math.pi * u),
                             "sy": 1 + 0.08 * math.sin(math.pi * u)}),
    "wobble": (0.8, lambda u: {"rot": math.sin(u * math.pi * 4) * 0.09 * (1 - u)}),
    "shrink": (0.6, lambda u: {"sx": 1 - 0.18 * math.sin(math.pi * u), "sy": 1 - 0.18 * math.sin(math.pi * u)}),
    "turn": (0.6, lambda u: {"sx": math.cos(u * math.pi * 2)}),
    "stretch": (0.7, lambda u: {"sx": 1 - 0.08 * math.sin(math.pi * u), "sy": 1 + 0.16 * math.sin(math.pi * u),
                                "dy": -6 * math.sin(math.pi * u)}),
    "slide": (1.0, lambda u: {"dx": math.sin(u * math.pi * 2) * 14}),
}

# where each kind's eyes and mouth are, for the faces: the eyes (x, y, w), how far the pupils travel,
# half the eye's height, how much lower the brows sit, the mouth (x, y, r) or None (the grand's keys are its grin)
FACE = {
    "house": {"eyes": ((-24, 2, 22), (24, 2, 22)), "k": 7, "eh": 14, "bo": 0, "mouth": (0, 25, 8), "ink": INK},
    "keys": {"eyes": ((-14, 24, 12), (14, 24, 12)), "k": 4, "eh": 5, "bo": 5, "mouth": (0, 37, 6), "ink": INK},
    "grand": {"eyes": ((-18, 0, 16), (18, 0, 16)), "k": 4.5, "eh": 10, "bo": 0, "mouth": None, "ink": PURE},
}
LIDS = {"sleepy": 0.6, "smug": 0.38}     # how much of the eye the lid covers

# ---- the grand, as in the clip (piano3q.js, grand3q): a real grand seen 3/4 from above and the front right.
# u across the keyboard, v from the keys to the tail, h up from the floor; P(u, v, h) gives plan units.
# Without the shadow under it and without the lyre and its 3 pedals (they read odd on the small screen).
GQ = {"ux": 190, "vx": 70, "vy": 40, "hy": 190}
GSC = 0.54                                   # the clip's size times this: about the size it has in the clip on this screen
GX, GY = -7.0, 56.0                          # where the floor under the keys is, from the figure's center


def _bez(p0, p1, p2, p3, n):
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


GCASE = [(-.75, 0), (.75, 0), (.75, .5)] + _bez((.75, .5), (.75, .95), (.28, 1.0), (.08, 1.42), 8) \
    + _bez((.08, 1.42), (-.08, 1.8), (-.32, 2.0), (-.58, 1.98), 8) + [(-.72, 1.9), (-.75, 1.75)]


def _inset(pts, d):
    """The outline moved in by d all round (the same width everywhere)."""
    n, out = len(pts), []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        n1, n2 = (b[1] - a[1], a[0] - b[0]), (c[1] - b[1], b[0] - c[0])
        l1, l2 = math.hypot(*n1) or 1, math.hypot(*n2) or 1
        nx, ny = n1[0] / l1 + n2[0] / l2, n1[1] / l1 + n2[1] / l2
        ln = math.hypot(nx, ny) or 1
        nx, ny = nx / ln, ny / ln
        cosh = max(0.5, nx * n1[0] / l1 + ny * n1[1] / l1)
        out.append((b[0] - nx * d / cosh, b[1] - ny * d / cosh))
    return out


GCASE_IN1, GCASE_IN2, GCASE_LID = _inset(GCASE, .05), _inset(GCASE, .12), _inset(GCASE, .06)
GCOL = {"top": (30, 35, 54), "inside": (214, 172, 118), "plate": (226, 180, 84), "front": (48, 57, 88),
        "leg": (34, 40, 62), "legtop": (46, 54, 82), "dark": (24, 28, 44), "desk": (30, 35, 54),
        "deskedge": (58, 68, 100), "lid": (52, 62, 94), "lidin": (62, 74, 110), "cheek": (44, 52, 80),
        "cheektop": (64, 76, 112), "cheekside": (36, 42, 66), "keyfront": (200, 204, 214), "keygold": (196, 148, 58)}
GWHITE = (0, 2, 4, 5, 7, 9, 11, 12)          # the 13 keys: which are white, left to right (pc 0..12)
GLIT = {"play": GOLD, "you": SKYBLUE, "wrong": BOW}


def clamp(v, a, b):
    return max(a, min(b, v))


def ease(u):
    return 2 * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 2 / 2


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
        self.react = None               # laughing ("happy") or dizzy, for a moment
        self.mood = None                # one of MOODS, until mood_until
        self.mood_until = 0.0
        self.move, self.move_t = None, -9.0
        self.bag, self.last_move = [], ""
        self.spin_t = -9.0
        self.hop_t = -9.0
        self.taps = []
        self.last_t = 0.0
        self.cool_until = 0.0           # Easter egg: sunglasses while t < cool_until
        self.owl = None                 # playing at 3 a.m.: the time the bubble came up
        self.last = (400.0, 300.0, 1.0)  # (x, y, s) of the last draw, for touches and the owl bubble
        self.nap = False                # drawn for the sleep screen: eyes shut, nothing moving (draw_sleeping)
        self.lit = []                   # keys lit on the grand: (pc, from, until, how), how in GLIT
        self.lid_shut = False           # the screen asks the grand to keep its lid down (the echo game's dots)

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
        if self.mood and t > self.mood_until:
            self.mood = None

    def reaction(self, t):
        """'happy', 'dizzy' or None at time t."""
        return self.react.kind if self.react and t <= self.react.until else None

    def face(self, t):
        """The face now: a reaction first (laughing, dizzy), then a mood, else None."""
        R = self.reaction(t)
        if R:
            return R
        return self.mood if self.mood and t <= self.mood_until else None

    def set_mood(self, mood, t, secs=4.0):
        self.mood, self.mood_until = (mood if mood in MOODS else None), t + secs

    def busy(self, t):
        """Spinning, or dizzy after it: a touch then only keeps it going."""
        return self.reaction(t) == "dizzy"

    def box_hit(self, x, y):
        """Is (x, y) on the figure, as it was last drawn?"""
        cx, cy, s = self.last
        dx, dy = x - cx, y - cy
        return abs(dx) < 85 * s and -95 * s < dy < 65 * s

    def next_move(self):
        """The moves come from a shuffled bag, so two touches in a row never move the same way."""
        if not self.bag:
            self.bag = list(MOVES)
            self.rnd.shuffle(self.bag)
            if self.bag[0] == self.last_move:
                self.bag.append(self.bag.pop(0))
        self.last_move = self.bag.pop(0)
        return self.last_move

    def touch(self, x, y, t):
        """A touch anywhere: a glance there. On the figure: "talk" for a touch on its own (a move, and the
        screen says a line), "laugh" for a quick one after another, "spin" at the fifth quick one (a whole
        turn, then dizzy). While it is dizzy a touch only keeps it dizzy: "dizzy". Off the figure: False."""
        self.owl = None
        cx, cy, s = self.last
        dx, dy = x - cx, y - cy
        self.look_t = [clamp(dx / 260, -1, 1), clamp(dy / 160, -1, 1)]
        self.look_hold = t + LOOK_HOLD
        if not self.box_hit(x, y):
            return False
        self.hop_t = t
        self.look_t = [0.0, 0.0]
        if self.busy(t):
            self.react.until = t + DIZZY_FOR
            return "dizzy"
        quick = bool(self.taps) and t - self.taps[-1] < QUICK
        self.taps = [q for q in self.taps if t - q < TAP_WINDOW] + [t]
        if len(self.taps) >= DIZZY_TAPS:
            self.taps = []
            self.react = Reaction("dizzy", t + SPIN_FOR + DIZZY_FOR)
            self.spin_t, self.move = t, None
            return "spin"
        if quick:
            self.react = Reaction("happy", t + HAPPY_FOR)
            self.move, self.move_t = "hop", t
            return "laugh"
        self.move, self.move_t = self.next_move(), t
        return "talk"

    def key(self, pc, t):
        """One of the keys (0..12, left to right, 12 the high C) was pressed: a glance down toward it,
        and on the grand that key goes down in light."""
        self.look_t = [min(1.0, (pc - 5.5) / 6), 0.6]
        self.look_hold = t + KEY_HOLD
        self.light(pc, t, 0.35, "you")

    def light(self, pc, at, hold, how="play"):
        """A key of the grand lit from at for hold seconds: "play" (gold, it plays it itself),
        "you" (sky blue, a key played) or "wrong" (red)."""
        self.lit = [q for q in self.lit if q[2] > at - 1][-12:] + [(int(pc) % 13 if 0 <= pc <= 12 else int(pc) % 12, at, at + hold, how)]

    def keys_hit(self, x, y):
        """Is (x, y) on the grand's keyboard, as it was last drawn? The echo game starts from there."""
        cx, cy, s = self.last
        P = self._gp(0.0)
        x0, y0 = P(-.78, -.3, .72)
        x1, y1 = P(.78, 0, .72)
        return min(x0, x1) - 6 < (x - cx) / s < max(x0, x1) + 6 and min(y0, y1) - 14 < (y - cy) / s < max(y0, y1) + 10

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
    def pose(self, t, F):
        """How it stands at t: the move of the last touch, the spin, and what the face does to the body."""
        o = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0}
        if self.move:
            secs, f = MOVES[self.move]
            u = (t - self.move_t) / secs
            if u >= 1 or u < 0:
                self.move = None
            else:
                o.update(f(u))
        su = (t - self.spin_t) / SPIN_FOR
        if 0 <= su < 1:
            o["rot"] += ease(su) * math.pi * 2
            o["dy"] += -18 * math.sin(math.pi * su)
        if F == "happy":
            q = 1 + 0.05 * math.sin(t * 22)
            o["sx"], o["sy"] = o["sx"] / q, o["sy"] * q
        elif F == "dizzy":
            o["rot"] += math.sin(t * 9) * 0.07
        elif F == "angry":
            o["dx"] += math.sin(t * 40) * 1.2
        elif F == "proud":
            o["rot"] -= 0.05
        elif F == "offended":
            o["rot"] += 0.07
        elif F == "sleepy":
            o["sy"] *= 1 - 0.02 * (1 + math.sin(t * 1.4))
        return o

    def draw(self, cv, kind, x, y, t, s=1.0):
        """The figure centered at plan (x, y), s times its plan size. It turns and stretches around its feet."""
        kind = kind if kind in KINDS else "house"
        self.last = (x, y, s)
        F = None if self.nap else self.face(t)
        if kind == "grand" and F == "proud":
            F = "happy"                              # the grand has no proud face: it laughs instead
        o = self.pose(t, F)
        low = LOW[kind]
        with cv.physical(), cv.at(x + o["dx"] * s, y + (low + o["dy"]) * s, s, o["rot"]):
            cv.scale_by(o["sx"], o["sy"])
            cv.translate(0, -low)
            getattr(self, "_" + kind)(cv, t, F)

    @staticmethod
    def _seg(cv, cx, cy, r, cut, color):
        """The part of a circle below the line y = cut (an eye under a lid)."""
        if cut <= cy - r:
            cv.circle(cx, cy, r, color)
            return
        if cut >= cy + r:
            return
        a = math.asin(clamp((cut - cy) / r, -1, 1))
        n = 14
        pts = [(cx + r * math.cos(a + (math.pi - 2 * a) * i / n), cy + r * math.sin(a + (math.pi - 2 * a) * i / n))
               for i in range(n + 1)]
        cv.polygon(pts, color)

    def _eyes(self, cv, t, F, kind):
        """Both eyes, as the face is now: shut (blinking, napping, the owl asleep), laughing ^ ^, dizzy
        spirals, sunglasses, or open with the pupils looking where they look, under a lid and brows
        for the faces that have them."""
        fc = FACE[kind]
        ink, k, eh = fc["ink"], fc["k"], fc["eh"]
        blink = (t % 4.2) < 0.13
        for x, y, w in fc["eyes"]:
            left = x < 0
            if self.nap or ((blink or self.asleep(t)) and F not in ("surprised", "happy", "dizzy")
                            and self.cool_until <= t):
                self._shut(cv, x, y, w, kind)
                continue
            if F in ("happy", "proud"):
                cv.arc(x, y + w * 0.35, w * 0.55, math.pi * 1.15, math.pi * 1.85, ink, 3.5)
                continue
            if F == "dizzy":
                spin = 1 if left else -1
                p, a = [], 0.0
                while a < 12.6:
                    r2 = a / 12.6 * w * 0.6
                    an = a + t * 8 * spin
                    p.append((x + math.cos(an) * r2, y + math.sin(an) * r2))
                    a += 0.3
                # the turns are close: a hair thinner than 2.4, as the polygon edges are smoothed outward
                cv.polyline(p, ink, max(1.0, 2.4 - 0.9 / max(cv.k(), 1e-6)))
                continue
            if self.cool_until > t:
                gw, gh = w * 1.25, w * 0.75
                cv.rect(x - gw / 2, y - gh / 2, gw, gh, SHADES, r=gh / 2.5)
                cv.rect(x - gw / 4, y - gh / 4, gw / 5, 2, (255, 255, 255, 89))
                if left:                                 # the bridge between the two glasses
                    cv.line(x + gw / 2, y - 2, -x - gw / 2, y - 2, SHADES, 3)
                continue
            lx, ly, grow = self.look[0], self.look[1], 1.0
            if F == "offended":
                lx, ly = -1.0, -0.7                      # looking away, up
            elif F == "smug":
                lx, ly = 0.9, 0.35                       # a look to the side
            elif F == "surprised":
                lx, ly, grow = lx * 0.3, ly * 0.3, 1.3
            cut = y - eh + eh * 2 * LIDS.get(F, 0.0) if F in LIDS else None
            px, py = x + lx * k, y + ly * k * 0.8
            if kind == "house":
                h, w2 = 28 * grow, 11 * grow
                top = py - h / 2 if cut is None else max(py - h / 2, cut)
                if py + h / 2 - top > 1:
                    cv.rect(px - w2 / 2, top, w2, py + h / 2 - top, INK, r=min(5, (py + h / 2 - top) / 2))
            else:
                if kind == "grand":
                    rw = 10 * (1.15 if F == "surprised" else 1.0)
                    self._seg(cv, x, y, rw, -1e9 if cut is None else cut, PURE)
                self._seg(cv, px, py, 5 * grow, -1e9 if cut is None else cut, INK)
            by, bw = y - eh - 7 + fc["bo"], w * 0.5
            if F == "angry":
                cv.line(x - bw, by + (-4 if left else 4), x + bw, by + (4 if left else -4), ink, 3.5)
            elif F == "offended":
                cv.line(x - bw, by + (3 if left else -3), x + bw, by + (-3 if left else 3), ink, 3)
            elif F == "surprised":
                cv.arc(x, by + 6, bw * 1.1, math.pi * 1.2, math.pi * 1.8, ink, 3)

    @staticmethod
    def _shut(cv, x, y, w, kind):
        """An eye shut: a short line."""
        if kind == "grand":
            cv.rect(x - 7, y - 1, 14, 3, INK)
        else:
            cv.rect(x - w / 2 - 1, y - 1, w + 2, 3, INK)

    def _mouth(self, cv, t, F, kind, smile):
        """The mouth for the face; smile() draws the kind's own calm one. The grand has none."""
        m = FACE[kind]["mouth"]
        if m is None:
            return
        x, y, r = m
        if F == "happy":
            cv.polygon([(x + (r + 1) * math.cos(math.pi * i / 16), y + 1 + (r + 1) * math.sin(math.pi * i / 16))
                        for i in range(17)], INK)
        elif F == "proud":
            cv.arc(x, y - 2, r * 1.3, 0.2, math.pi - 0.2, INK, 3.5)
        elif F == "smug":
            cv.polyline([(x - r * 0.7 + (r * 1.6) * i / 8, y + 3 + 2 * math.sin(math.pi * i / 8) - 3.5 * (i / 8) ** 2)
                         for i in range(9)], INK, 3)
        elif F == "angry":
            cv.arc(x, y + r * 1.1, r * 0.9, math.pi * 1.2, math.pi * 1.8, INK, 3)
        elif F == "offended":
            cv.arc(x - r * 0.4, y + r * 0.9, r * 0.6, math.pi * 1.15, math.pi * 1.85, INK, 3)
        elif F == "surprised":
            cv.ellipse(x, y + 2, r * 0.55, r * 0.75, INK)
        elif F == "sleepy":
            cv.ellipse(x, y + 2, r * 0.4, r * 0.45 + max(0.0, math.sin(t * 1.4)) * r * 0.25, INK)
        elif F == "dizzy":
            cv.polyline([(x - r, y + 1), (x - r / 2, y - 3), (x, y + 1), (x + r / 2, y + 5), (x + r, y + 1)], INK, 3)
        else:
            smile()

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
        # a chimney puffing little notes, faster when it laughs, grey smoke when it is angry; a calm smile
        cv.rect(26, -76, 13, 22, GOLD, r=3)
        sp = 0.9 if R == "happy" else 1.2 if R == "angry" else 0.15 if R == "sleepy" else 0.32
        for k in range(0 if self.nap else 3):
            p = (t * sp + k / 3) % 1
            a = (1 - p) * (p / 0.12 if p < 0.12 else 1)
            if R == "angry":
                a = round(clamp(a * 0.7, 0, 1) * 10) / 10
                if a > 0:
                    cv.circle(33 + math.sin(p * 7 + k) * 6 + p * 10, -82 - p * 40, 5 + p * 7, (120, 128, 145, int(255 * a)))
            else:
                self._note(cv, 33 + math.sin(p * 7 + k) * 7 + p * 14, -80 - p * 46, a, 16 + p * 8)
        cv.polyline([(-74, -30), (0, -84), (74, -30)], GOLD, 7)
        cv.polyline([(-56, -20), (0, -66), (56, -20)], SKYBLUE, 6)
        cv.rect(-52, -20, 104, 66, WHITE, r=12)
        self._eyes(cv, t, R, "house")
        self._mouth(cv, t, R, "house", lambda: cv.arc(0, 25, 8, 0.25, math.pi - 0.25, INK, 3))

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
        cv.save()
        cv.translate(0, dip)
        self._eyes(cv, t, R, "keys")
        self._mouth(cv, t, R, "keys", lambda: cv.arc(0, 37, 6, 0.25, math.pi - 0.25, INK, 3))
        cv.restore()

    @staticmethod
    def _gp(hop):
        """P(u, v, h) of the grand, in the figure's plan units."""
        sc, cx, cy = GSC, GX, GY - hop
        return lambda u, v, h: (cx + sc * (GQ["ux"] * u + GQ["vx"] * v), cy - sc * (GQ["vy"] * v + GQ["hy"] * h))

    def _grand_lift(self, t, R):
        """The lid: closed, or open all the way, never stuck half way (half open it reads broken). Now and
        then it opens for a moment; it stays down while there is a face, a nap or the echo game."""
        if self.nap or R or self.lid_shut:
            return 0.0
        c = t % 9.0
        if c < 6.4 or c > 8.4:
            return 0.0
        return ease(min(1.0, (c - 6.4) / 0.35, (8.4 - c) / 0.35))

    def _grand(self, cv, t, R):
        # the grand from the clip: the eyes on the wall above the keys, the bow tie on the key slip, arms resting low
        P = self._gp(0.0)
        sc, H0, H1, KH = GSC, .64, .98, .72
        poly = lambda pts, col: cv.polygon(pts, col)
        # the tail leg first, it is the farthest
        self._gleg(cv, P, -.32, 1.72)
        # the side wall: one smooth shade round the whole wing, each piece a little longer so no seams show
        n = len(GCASE)
        for i in range(n):
            p, q = GCASE[i], GCASE[(i + 1) % n]
            dx, dy = q[0] - p[0], q[1] - p[1]
            ln = math.hypot(dx, dy) or 1
            f = clamp(.5 - (dx * .9 - dy * .4) / ln * .6, 0, 1)
            col = (round(40 + 18 * f), round(47 + 21 * f), round(72 + 28 * f))
            e = .012 / ln
            p2, q2 = (p[0] - dx * e, p[1] - dy * e), (q[0] + dx * e, q[1] + dy * e)
            poly([P(p2[0], p2[1], H1), P(q2[0], q2[1], H1), P(q2[0], q2[1], H0), P(p2[0], p2[1], H0)], col)
        lift = self._grand_lift(t, R)
        th = lift * .92
        if lift > .02:
            poly([P(u, v, H1) for u, v in GCASE], (40, 47, 72))
            poly([P(u, v, H1 - .01) for u, v in GCASE_IN1], GCOL["inside"])
            poly([P(u, v, H1 - .005) for u, v in GCASE_IN2], GCOL["plate"])
            for i in range(9):
                u = -.6 + i * .15
                a, b = P(u, .32, H1), P(u, 1.75 - (u + .6) * .55, H1)
                cv.line(a[0], a[1], b[0], b[1], (150, 118, 80), 1.2)

            def lid(p):
                d = p[0] + .75
                return P(-.75 + d * math.cos(th), p[1], H1 + .01 + d * math.sin(th))
            sb, st = P(.52, .95, H1), lid((.52, .95))
            poly([lid(p) for p in GCASE], GCOL["lid"])
            poly([lid(p) for p in GCASE_LID], GCOL["lidin"])
            cv.line(sb[0], sb[1], st[0], st[1], GCOL["dark"], 2.6)
        else:
            poly([P(u, v, H1) for u, v in GCASE], GCOL["top"])
            cv.polyline([P(u, v, H1 + .001) for u, v in GCASE_LID + GCASE_LID[:1]], (52, 59, 82), 1.3)
        # the music desk: two rails, a panel leaning back with a carved border, and the ledge
        for u in (-.42, .42):
            poly([P(u - .02, .24, H1), P(u + .02, .24, H1), P(u + .02, .44, H1), P(u - .02, .44, H1)], (22, 26, 40))
        DK = lambda u, k: P(u, .3 + .13 * k, H1 + .03 + .47 * k)
        poly([DK(-.52, 0), DK(.52, 0), DK(.52, 1), DK(-.52, 1)], GCOL["desk"])
        poly([DK(-.52, .96), DK(.52, .96), DK(.52, 1), DK(-.52, 1)], GCOL["deskedge"])
        cv.polyline([DK(-.47, .1), DK(.47, .1), DK(.47, .9), DK(-.47, .9), DK(-.47, .1)], (70, 80, 108), 1.0)
        poly([P(-.53, .27, H1 + .02), P(.53, .27, H1 + .02), P(.53, .3, H1 + .045), P(-.53, .3, H1 + .045)], GCOL["lid"])
        poly([P(-.53, .27, H1 - .005), P(.53, .27, H1 - .005), P(.53, .27, H1 + .02), P(-.53, .27, H1 + .02)], GCOL["dark"])
        poly([P(-.2, .31, H1 + .02), P(.2, .31, H1 + .02), P(.2, .37, H1 + .12), P(-.2, .37, H1 + .12)], (246, 242, 232))
        for i in range(4):
            f = .04 + i * .022
            a, b = P(-.18, .31 + f * .6, H1 + .02 + f), P(.18, .31 + f * .6, H1 + .02 + f)
            cv.line(a[0], a[1], b[0], b[1], (110, 114, 130), 0.7)
        # the front wall above the keys: the face lives here
        poly([P(-.75, 0, KH + .02), P(.75, 0, KH + .02), P(.75, 0, H1), P(-.75, 0, H1)], GCOL["front"])
        a, b = P(-.73, 0, H1 - .015), P(.73, 0, H1 - .015)
        cv.line(a[0], a[1], b[0], b[1], (70, 79, 108), 1.3)
        self._grand_eyes(cv, t, R, P)
        # the keys stick out in front: the key slip, the white keys with their front edge, the black keys
        poly([P(-.78, -.3, .62), P(.78, -.3, .62), P(.78, -.3, KH - .015), P(-.78, -.3, KH - .015)], GCOL["leg"])
        lit = {}
        for pc, a0, a1, how in ([] if self.nap else self.lit):
            if a0 <= t < a1:
                lit[pc] = GLIT.get(how, GOLD)
        N, kw = 15, 1.42 / 15
        wl = {}
        O = 4                                        # the 13 keys sit in the middle: C is the fifth white key
        for pc, w in zip(GWHITE, range(8)):
            wl[O + w] = lit.get(pc)
        for i in range(N):
            u0 = -.71 + i * kw + .004
            u1 = u0 + kw - .008
            on = wl.get(i)
            dip = .006 if on else 0
            poly([P(u0, -.29, KH - dip), P(u1, -.29, KH - dip), P(u1, -.01, KH - dip), P(u0, -.01, KH - dip)], on or WHITE)
            poly([P(u0, -.29, KH - .015), P(u1, -.29, KH - .015), P(u1, -.29, KH - dip), P(u0, -.29, KH - dip)],
                 GCOL["keygold"] if on else GCOL["keyfront"])
        black = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}     # pc of a black key: after which white key (i in 15)
        blit = {O + b: lit.get(pc) for pc, b in black.items()}
        for i in range(N - 1):
            if (i - O) % 7 not in (0, 1, 3, 4, 5):
                continue
            u = -.71 + (i + 1) * kw
            poly([P(u - kw * .3, -.14, KH + .022), P(u + kw * .3, -.14, KH + .022), P(u + kw * .3, -.01, KH + .022),
                  P(u - kw * .3, -.01, KH + .022)], blit.get(i) or GCOL["dark"])
        # the cheek blocks at both ends of the keyboard
        for a, b in ((-.8, -.71), (.71, .8)):
            poly([P(a, -.3, .62), P(b, -.3, .62), P(b, -.3, KH + .05), P(a, -.3, KH + .05)], GCOL["cheek"])
            poly([P(a, -.3, KH + .05), P(b, -.3, KH + .05), P(b, 0, KH + .05), P(a, 0, KH + .05)], GCOL["cheektop"])
            poly([P(b, -.3, .62), P(b, 0, .62), P(b, 0, KH + .05), P(b, -.3, KH + .05)], GCOL["cheekside"])
        # the bow tie on the key slip, bigger when it is proud or laughing
        bx, by = P(0, -.3, .66)
        bw = (1.5 if R == "proud" else 1.2 if R == "happy" else 1.0) * sc
        cv.polygon([(bx, by), (bx - 15 * bw, by - 8 * sc), (bx - 15 * bw, by + 8 * sc)], BOW)
        cv.polygon([(bx, by), (bx + 15 * bw, by - 8 * sc), (bx + 15 * bw, by + 8 * sc)], BOW)
        cv.circle(bx, by, 4 * sc, BOW)
        # the two front legs, then the arms from the cheek blocks, always resting low
        self._gleg(cv, P, -.68, -.06)
        self._gleg(cv, P, .68, -.06)
        sw = math.sin(t * 5) * .6 if R == "happy" else 0.0
        for d in (-1, 1):
            x0, y0 = P(.8 * d, -.16, .7)
            x1, y1 = x0 + d * (40 - d * sw * 12) * sc, y0 + (60 - d * sw * 8) * sc
            mx, my = (x0 + x1) / 2 + d * 8 * sc, (y0 + y1) / 2
            cv.polyline([(x0 + (1 - u) ** 2 * 0 + 2 * (1 - u) * u * (mx - x0) + u * u * (x1 - x0),
                          y0 + 2 * (1 - u) * u * (my - y0) + u * u * (y1 - y0)) for u in (i / 8 for i in range(9))], INK, 6 * sc)
            cv.circle(x1, y1, 6.6 * sc, INK)
        if R == "proud" and not self.nap:          # a few small gold sparkles round it
            for i in range(3):
                a = (math.sin(t * 3 + i * 2.1) + 1) / 2
                x, y = ((-80, -60), (78, -66), (64, -96))[i]
                r = 3 + a * 3.5
                cv.polygon([(x + math.cos(j * math.pi / 4) * (r if j % 2 == 0 else r * .3),
                             y + math.sin(j * math.pi / 4) * (r if j % 2 == 0 else r * .3)) for j in range(8)],
                           GOLD + (int(255 * (.35 + a * .6)),))
        if R == "surprised" and not self.nap:
            self._text(cv, "!", 74, -70 + math.sin(t * 8) * 2, 20, PURE)
        if not self.nap:
            self._grand_signs(cv, t, R)

    @staticmethod
    def _grand_signs(cv, t, R):
        """Each face's own sign beside the grand, as in the manga: notes rising when it laughs, the red anger
        mark, a puff when it is offended, a small z now and then when it is sleepy."""
        if R == "happy":
            for i in range(2):
                p = (t * .7 + i / 2) % 1
                x, y = 89 + 15 * i + math.sin(p * 6) * 4, -66 - 52 * p
                col = GOLD + (int(255 * (1 - p)),)
                cv.ellipse(x, y, 4.2, 3.2, col)
                cv.line(x + 3.6, y - 1, x + 3.6, y - 13, col, 1.6)
                if i:
                    cv.line(x + 3.6, y - 13, x + 9, y - 10, col, 1.6)
        elif R == "angry":
            x, y, r = 89, -82, 9.0
            for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                a0 = math.atan2(-dy, -dx)
                cv.polyline([(x + dx * r + math.cos(a0 - .9 + 1.8 * i / 6) * r * .8,
                              y + dy * r + math.sin(a0 - .9 + 1.8 * i / 6) * r * .8) for i in range(7)], BOW, 2.4)
        elif R == "offended":
            p = (t * .8) % 1
            for dx, dy, r in ((0, 0, 5.2), (7, -3, 3.7), (12, -7, 2.6)):
                cv.circle(-96 - 15 * p - dx, -36 + dy, r, (220, 226, 240, int(255 * (.8 - p * .8))))
        elif R == "sleepy":
            p = (t * .35) % 1
            if p < .6:
                Character._text(cv, "z", 96 + 15 * p, -70 - 30 * p, 15, (255, 255, 255, int(255 * min(1, (.6 - p) * 1.6))))

    @staticmethod
    def _gleg(cv, P, u, v):
        sc = GSC
        a, b = P(u, v, .64), P(u, v, .02)
        w = 15 * sc
        cv.polygon([(a[0] - w, a[1]), (a[0] + w, a[1]), (b[0] + w * .6, b[1]), (b[0] - w * .6, b[1])], GCOL["leg"])
        cv.rect(a[0] - w * 1.25, a[1] - 4 * sc, w * 2.5, 12 * sc, GCOL["legtop"], r=4 * sc)
        cv.ellipse(b[0], b[1] + 3 * sc, w * .9, 7 * sc, GOLD)

    def _grand_eyes(self, cv, t, F, P):
        """The grand's eyes on its front wall. Each face its own: the eyes (shape, size, where they look,
        lids) and the brows on the lid's edge above them. Shut when blinking, napping or the owl sleeps;
        ^ ^ laughing; spirals dizzy; sunglasses for the Easter egg."""
        fx, fy = P(0, 0, .865)
        k = GSC * 1.2 * 20 / 9.5
        wall = GCOL["front"]
        blink = (t % 4.2) < 0.13
        shut = self.nap or self.asleep(t) or (blink and F in (None, "sleepy"))
        for d in (-1, 1):
            ex, ey = fx + d * 18 * k, fy
            if self.cool_until > t and not self.nap:
                gw, gh = 22 * k, 13 * k
                cv.rect(ex - gw / 2, ey - gh / 2, gw, gh, SHADES, r=gh / 2.5)
                cv.rect(ex - gw / 4, ey - gh / 4, gw / 5, 2, (255, 255, 255, 89))
                if d < 0:
                    cv.line(ex + gw / 2, ey - 2, ex + 36 * k - gw / 2, ey - 2, SHADES, 3)
                continue
            if shut:
                cv.polyline([(ex - 7 * k + 14 * k * i / 8, ey + 4 * k * math.sin(math.pi * i / 8) * .5) for i in range(9)], PURE, 3.2 * k)
                continue
            if F == "happy":
                j = math.sin(t * 22) * 1.2 * k
                cv.polyline([(ex - 7.5 * k + 15 * k * i / 8, ey + 3 * k + j - 5.5 * k * math.sin(math.pi * i / 8)) for i in range(9)], PURE, 3.6 * k)
                continue
            if F == "dizzy":
                pts, a = [], 0.0
                while a < 12.6:
                    r2 = a / 12.6 * 9 * k
                    an = a + t * 8 * (1 if d < 0 else -1)
                    pts.append((ex + math.cos(an) * r2, ey + math.sin(an) * r2))
                    a += 0.3
                cv.polyline(pts, PURE, max(1.0, 2.4 - 0.9 / max(cv.k(), 1e-6)))
                continue
            R, pr = 9.5 * k, 4.8 * k
            lx, ly = self.look
            top = low = tilt = 0.0
            brow = None                              # (y offset, inner raise, outer raise, width)
            if F == "proud":                         # calm, half shut from above, looking up over everyone
                top, lx, ly = .42, 0.0, -.95
            elif F == "smug":                        # both eyes half shut, a side look, one brow up
                top, lx, ly = .5, .95, .2
                if d > 0:
                    brow = (-R - 7 * k, 1, 6, 2.6)
            elif F == "surprised":                   # big round eyes, small pupils in the middle, brows high
                R, pr, lx, ly = R * 1.32, pr * .62, 0.0, 0.0
                brow = (-R - 7 * k, None, None, 2.6)
            elif F == "offended":                    # will not look at all: shut flat, nose in the air, brows up inside
                cv.line(ex - 7.5 * k, ey + d * 1.5 * k, ex + 7.5 * k, ey - d * 1.5 * k, PURE, 3.4 * k)
                cv.line(ex - d * R * .85, ey - R - 7 * k, ex + d * R * .85, ey - R - 1 * k, PURE, 2.6 * k * .8)
                continue
            elif F == "angry":                       # narrowed at a slant, small pupils ahead, steep brows in a V
                top, tilt, lx, ly, pr = .42, d, 0.0, .2, pr * .8
                brow = (-R - 3 * k, -5, 3, 3.4)
            elif F == "sleepy":                      # heavy lids from above and below, looking down, drooping
                top, low, lx, ly = .55 + math.sin(t * 1.4) * .12, .15, 0.0, .7
            cv.circle(ex, ey, R, PURE)
            m = R - pr - 1.5 * k
            px, py = ex + lx * m * .9, ey + ly * m * .75
            cv.circle(px, py, pr, INK)
            if F != "surprised":
                cv.circle(px + 2 * k, py - 2 * k, 1.5 * k, PURE)
            if top:                                  # the lid from above, at a slant when angry
                y0 = ey - R + 2 * R * top
                sl = tilt * R * .35
                ya, yb = y0 - sl * d * (-1 if d > 0 else 1), y0 + sl * d * (-1 if d > 0 else 1)
                lidpts = []
                for i in range(13):
                    x = ex - R + 2 * R * i / 12
                    ly2 = ya + (yb - ya) * i / 12
                    yc = ey - math.sqrt(max(0.0, R * R - (x - ex) ** 2))
                    lidpts.append((x, max(yc, min(ly2, ey + math.sqrt(max(0.0, R * R - (x - ex) ** 2))))))
                arc_top = [(ex + R * math.cos(math.pi + math.pi * i / 12), ey + R * math.sin(math.pi + math.pi * i / 12)) for i in range(13)]
                cv.polygon(arc_top + lidpts[::-1], wall)
                cv.line(lidpts[0][0], ya, lidpts[-1][0], yb, (30, 35, 54), 1.6 * k * .6)
            if low:
                yl = ey + R - 2 * R * low
                bot = [(ex + R * math.cos(math.pi * i / 12), ey + R * math.sin(math.pi * i / 12)) for i in range(13)]
                cv.polygon([(x, max(y, yl)) for x, y in bot], wall)
            if brow:
                by, inner, outer, w = brow
                by += ey
                hw = R * .85
                if inner is None:
                    cv.polyline([(ex - hw + 2 * hw * i / 8, by + 2 * k - 6 * k * math.sin(math.pi * i / 8)) for i in range(9)], PURE, w * k * .8)
                else:
                    cv.line(ex - d * hw, by - inner * k, ex + d * hw, by - outer * k, PURE, w * k * .8)

    # the metronome, taken out (KINDS): kept as comments
    # def _metro(self, cv, t, R):
    #     # when nobody touched it lately, its eyes follow its own swinging arm, tick, tock
    #     cv.polygon([(-46, 52), (-16, -70), (16, -70), (46, 52)], GOLD)
    #     cv.polygon([(-28, 40), (-10, -48), (10, -48), (28, 40)], WHITE)
    #     sp = 9 if R == "dizzy" else 2.4
    #     a = math.sin(t * sp) * 0.42
    #     lx = ly = None
    #     if not R and t > self.look_hold:
    #         lx, ly = math.sin(t * sp) * 0.9, -0.6
    #     cv.save()
    #     cv.translate(0, 34)
    #     cv.rotate(a)
    #     cv.line(0, 0, 0, -86, INK, 4)
    #     cv.rect(-8, -66, 16, 12, SKYBLUE, r=3)
    #     cv.restore()
    #     self._pair(cv, t, R, [(-13, 1, 14), (13, 1, 14)], 3, self._pill(cv, 18, 8), self._bar(cv), lx=lx, ly=ly)
    #     cv.arc(0, 20, 7, 0.3, math.pi - 0.3, INK, 3)

    def draw_sleeping(self, cv, kind, x, y, s=1.0, breath=1.0):
        """The figure asleep, for the sleep screen: the eyes shut as short lines, no notes from the chimney. breath (1.0 and a little over) stretches it upward from its
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
