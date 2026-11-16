"""anime.py - the anime theme of the Armonico Pi device: limited animation over pictures made in Gemini.

The pictures (device/ui/data/anime, made once by device/tools/anime_prep.py) are stills. What moves is done
here, the way limited anime is made: a camera that pans slowly across a still, held frames, hard cuts, an
impact frame, manpu symbols as a layer, and emotional backgrounds (focus lines, golden streaks, black).
The camera moves on twos and threes (12 steps a second), not on every frame: it is the look, and it keeps
the Pi's load low.

  * The rooftops follow the real hour: the Sun's height over the city chosen (sky.py) picks the picture, and
    two pictures blend slowly while the Sun moves between them. Over them the sky of the city chosen, as in the
    sky theme: its stars where they really are (anime sparkles, twinkling), the Sun and the Moon in its phase.
  * The room: pose 7 has its own morning, evening and night pictures; poses 8 to 11 get the evening or night
    color and a lamp's light here.
  * A lesson is an episode (the song is the season, the part is the episode): before it, "previously", a
    cold open and the opening; in the breath before the last run with the band, the eyecatch; after it, the
    credits with the lesson's numbers, a short scene after them and "next time". The full episode is shown
    the first time a part is learned; again, the lesson starts at once.
  * The girl has no voice: each feeling is a short motif on the device's own piano (armonico_pi.py fx_note),
    heard only, never sent to the lesson.

Nothing here loads or scales a picture on every frame: each picture is scaled once to the screen and kept.
"""
import json
import math
import os
import random
import time
import zlib
from collections import OrderedDict
from pathlib import Path

from .canvas import W, H
from .topics import TOPICS

DATA = Path(__file__).resolve().parent / "data" / "anime"
EPISODES_FILE = "anime_episodes.json"      # in DATA_DIR: the episodes already shown in full
STEP = 1 / 12                              # drawings change on twos: the curtain's and the cat's frames
CAMERA_STEP = 1 / 30                       # the camera and the lights move every frame: on twos they looked jerky
KEEP = 32                                  # pictures and layers kept scaled to the screen (a picture is about 2 MB at 800x480)

# The rooftops by the Sun's height: (altitude in degrees, picture), morning and evening apart. Between two
# pictures the screen blends them, eased, over 5 to 12 degrees of the Sun (25 to 70 minutes), so the light
# changes as the real one does. bg_01 (the sunrise) had a lens flare drawn in it, a trail of blue and orange
# rings over the house: it is not used. The pink of bg_05 is the dawn too. The golden pictures come only near
# the horizon: by height alone (bg_02 below 28 degrees) a November afternoon looked like the evening.
MORNING = [(-90, "bg_08"), (-14, "bg_08"), (-7, "bg_06"), (-2, "bg_05"), (6, "bg_02"), (12, "bg_02"),
           (24, "bg_03"), (90, "bg_03")]
EVENING = [(-90, "bg_08"), (-14, "bg_08"), (-7, "bg_06"), (-1, "bg_05"), (5, "bg_04"), (10, "bg_04"),
           (20, "bg_03"), (90, "bg_03")]
# The sky over the rooftops, as the sky theme has it (sky.py) but drawn the anime way: the stars of the city
# chosen at their real place, the Sun and the Moon. Looking south, 180 degrees across the picture. Height:
# the horizon is behind the roofs (HORIZON of the picture's height); above CLEAR the sky is open everywhere.
HORIZON, ZENITH_AT, CLEAR = 0.34, 0.01, 0.29
ZENITH_ALT = 62.0                          # the height at the picture's top: as the sky theme (sky.py)
# The share of the city's windows lit, by the hour (local): (hour, share). Each window keeps its own rhythm and habit
# (Anime.window_level), so the share is reached one window at a time.
WINDOW_SHARE = [(0, 0.3), (1.5, 0.16), (4, 0.08), (5.5, 0.2), (6.5, 0.32), (8, 0.15), (16, 0.2), (17.5, 0.55),
                (20, 0.78), (22, 0.66), (24, 0.3)]
WINDOW_FADE = 40.0                         # seconds a window takes to light or to go dark
LAMP = (0.80, 0.42)                        # where the room's lamp light comes from, as fractions of the picture
LAYER_TINT = {"evening": (255, 206, 186, 255), "night": (96, 108, 160, 255)}   # the curtain and the cat after day
HEAD = {8: (0.60, 0.22), 9: (0.59, 0.20), 10: (0.60, 0.22), 11: (0.60, 0.22)}   # the girl's face in poses 8 to 11
# Where the corner faces were cut from the room's picture (tools/anime_prep.py FACE and FACE_G08): their evening
# and night are that part of the room relit
FACE_BOX = {"g08face": (0.46, 0.07, 0.76, 0.406), "face_08": (0.47, 0.11, 0.71, 0.39),
            "face_09": (0.47, 0.09, 0.71, 0.37), "face_10": (0.47, 0.11, 0.71, 0.39), "face_11": (0.47, 0.11, 0.71, 0.39)}
HAPPY_GAP = 30.0            # seconds between two of her laughs in a lesson (a part learned comes in any time)
CUTIN_IN, CUTIN_OUT = 0.28, 0.22           # seconds the corner face slides and fades in, and out

# The girl's feelings: (seconds from the start, MIDI note), on the device's piano
MOTIFS = {"happy": [(0, 76), (0.09, 79), (0.18, 84)],
          "sparkle": [(0, 96), (0.07, 100), (0.14, 103)],
          "surprised": [(0, 84), (0.06, 91)],
          "oops": [(0, 77), (0.17, 76)],
          "think": [(0, 74), (0.2, 79)],
          "dots": [(0, 72), (0.24, 72), (0.48, 72)],
          "sting": [(0, 72), (0, 79), (0, 84), (0.26, 88)],
          "start": [(0, 60), (0, 67), (0.13, 72), (0.26, 76)]}
FX_HOLD = 0.32

TEXT = {
    "prev": ("Previously", "בפרקים הקודמים"),
    "parts": ("Parts 1 to {n}", "חלקים 1 עד {n}"),
    "part1": ("Part 1", "חלק 1"),
    "episode": ("Episode {n}", "פרק {n}"),
    "season": ("Season: {s}", "עונה: {s}"),
    "partOf": ("Part {k} of {n}", "חלק {k} מתוך {n}"),
    "next": ("Next time", "בפרק הבא"),
    "nextEp": ("Episode {n}: part {n} of {t}", "פרק {n}: חלק {n} מתוך {t}"),
    "end": ("The season is over", "סוף העונה"),
    "endSub": ("Every part of {s} is learned", "כל החלקים של {s} נלמדו"),
    "cSeason": ("Season", "עונה"), "cEpisode": ("Episode", "פרק"), "cLearned": ("Parts learned", "חלקים שנלמדו"),
    "cScore": ("Last run", "הריצה האחרונה"), "cTime": ("Time", "זמן"), "cMiss": ("Wrong keys", "קלידים שגויים"),
    "cFixed": ("Parts practiced again", "חלקים שתורגלו שוב"), "cWarm": ("Warm-up", "חימום"),
    "cDuet": ("Duet", "דואט"), "cDuetV": ("with the band", "עם הליווי"),
    "cKnown": ("Parts you know", "חלקים שאתה כבר יודע"), "cKnownV": ("{k} of {n}", "{k} מתוך {n}"),
    "cKnownH": ("of the whole song", "מכל השיר"),
    "cToday": ("This lesson", "בשיעור הזה"), "cTodayOne": ("Part {k}, new", "חלק {k}, חדש"),
    "cTodayMany": ("Parts {a} to {b}, new", "חלקים {a} עד {b}, חדשים"),
    "cTodayReview": ("Practice of what you know", "תרגול של מה שכבר למדת"),
    "cTodayH": ("tomorrow they come back to be sure they hold", "מחר הם חוזרים כדי לוודא שהם נשמרים"),
    "cScoreH": ("of the song played right, all of it at once", "מהשיר נוגן נכון, כולו ברצף"),
    "cMissH": ("keys pressed that were not the next note", "קלידים שנלחצו ולא היו התו הבא"),
    "cFixedH": ("parts that began to fade, played again", "חלקים שהתחילו להישכח ונוגנו שוב"),
    "cWarmH": ("parts played first, to keep them", "חלקים שנוגנו בהתחלה כדי לשמור עליהם"),
    "cEnd": ("End of the episode", "סוף הפרק"),
    "duet": ("Duet", "דואט"),
    "thinkHi": ("That went well", "זה הלך טוב"), "thinkMid": ("Once more tomorrow", "עוד פעם מחר"),
    "thinkLo": ("Slowly, it's coming", "לאט לאט, זה מגיע"),
}


def tr(lang, key, **kw):
    pair = TEXT.get(key, (key, key))
    s = pair[1] if lang == "he" else pair[0]
    return s.format(**kw) if kw else s


def _hex(s):
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


def _rgba(r, g, b, a):
    return (r, g, b, int(round(255 * a)))


def palette(sun_alt):
    """The colors of the screens over the anime pictures: dark letters on the day's pale pictures, light ones
    from the blue hour on. Panels are see-through, so the picture stays behind every screen."""
    day = sun_alt is None or sun_alt > 1
    if day:
        return {"ink": _hex("#2a2238"), "dim": _rgba(42, 34, 56, 0.8), "panel": _rgba(255, 250, 244, 0.74),
                "sel": _hex("#d0682c"), "gold": _hex("#b8661a"), "bar": _rgba(255, 248, 240, 0.6),
                "key": _rgba(255, 255, 255, 0.96), "black": _hex("#2d2a3a"), "onkey": _hex("#4a4458"),
                "lit": _hex("#e58a3c"), "ok": _hex("#2e8b62"), "line": None}
    return {"ink": (255, 255, 255), "dim": _rgba(255, 255, 255, 0.84), "panel": _rgba(20, 22, 46, 0.64),
            "sel": _hex("#ffc27a"), "gold": _hex("#ffcf8a"), "bar": _rgba(14, 16, 36, 0.5),
            "key": _rgba(255, 255, 255, 0.95), "black": _hex("#26304f"), "onkey": _hex("#3b4766"),
            "lit": _hex("#ffcf8a"), "ok": _hex("#a6f0c6"), "line": None}


# ---------------------------------------------------------------- the hour

def sun_now(app):
    """(altitude, morning) of the Sun over the city chosen; without a city, from the clock (6 to 18 as day)."""
    st = getattr(app, "sky_st", None)
    if st is not None:
        return st.sun.altitude, st.sun.azimuth < 180
    d = app.local()
    h = d.hour + d.minute / 60
    return 60 * math.sin(math.pi * (h - 6) / 12), h < 12


def sky_mix(alt, morning):
    """[(picture, weight)]: one picture, or two blending."""
    keys = MORNING if morning else EVENING
    for (a0, p0), (a1, p1) in zip(keys, keys[1:]):
        if a0 <= alt <= a1:
            if p0 == p1:
                return [(p0, 1.0)]
            w = ease((alt - a0) / (a1 - a0))  # eased: no sudden start or stop of the change
            return [(p0, 1 - w), (p1, w)]
    return [(keys[-1][1] if alt > 0 else keys[0][1], 1.0)]


def period(alt, morning):
    """day, evening or night: the room's pictures and colors."""
    if alt < -6 or (morning and alt < -1):
        return "night"
    if not morning and alt < 10:
        return "evening"
    return "day"


# ---------------------------------------------------------------- the pictures

class Pics:
    """The pictures, each scaled once to the screen (and tinted once for an hour) and kept."""

    def __init__(self, pg):
        self.pg = pg
        self.small = {}                   # the layers (cat, curtain, manpu, logo): small, kept as loaded
        self.sized = OrderedDict()

    def path(self, name):
        for ext in (".jpg", ".png"):
            p = DATA / (name + ext)
            if p.exists():
                return p
        return None

    def load(self, name, alpha=False):
        p = self.path(name)
        if p is None:
            return None
        try:
            img = self.pg.image.load(str(p))
        except Exception:
            return None
        try:
            return img.convert_alpha() if alpha else img.convert()
        except Exception:                 # no display mode yet (the tests set one)
            return img

    def layer(self, name):
        if name not in self.small:
            self.small[name] = self.load(name, alpha=True)
        return self.small[name]

    def sized_layer(self, name, w, h, when=None, box=None):
        """A layer scaled to w x h real pixels, kept; in the evening or at night in the room's color. box: the
        part of the room the layer was cut from (a face): then it is relit as the room is, not tinted."""
        key = ("L", name, int(w), int(h), when, box)
        img = self.sized.get(key)
        if img is None:
            src = self.layer(name)
            if src is None:
                return None
            img = self.pg.transform.smoothscale(src, (max(1, int(w)), max(1, int(h))))
            lit = self.relit(img, when, box) if box and when in LAYER_TINT else None
            if lit is not None:
                img = lit
            elif when in LAYER_TINT:
                img.fill(LAYER_TINT[when], special_flags=self.pg.BLEND_RGBA_MULT)
            self._keep(key, img)
        else:
            self.sized.move_to_end(key)
        return img

    def cover(self, name, sw, sh, zoom=1.0, tint=None):
        """The picture scaled to cover sw x sh, zoom times more (room for the camera), tinted for an hour."""
        key = (name, sw, sh, round(zoom, 3), tint)
        img = self.sized.get(key)
        if img is not None:
            self.sized.move_to_end(key)
            return img
        src = self.load(name)
        if src is None:
            return None
        iw, ih = src.get_size()
        k = max(sw / iw, sh / ih) * zoom
        img = self.pg.transform.smoothscale(src, (max(sw, int(math.ceil(iw * k))), max(sh, int(math.ceil(ih * k)))))
        if tint:
            img = self.tinted(img, tint)
        self._keep(key, img)
        return img

    def _keep(self, key, img):
        self.sized[key] = img
        while len(self.sized) > KEEP:
            self.sized.popitem(last=False)

    def field(self, when):
        """The evening's or the night's light, learned from pose 7 (tools/relight_fields.py), or None."""
        if not hasattr(self, "_fields"):
            self._fields = {}
            try:
                import numpy as np
                with np.load(DATA / "relight.npz") as f:
                    self._fields = {k: f[k].astype(np.float32) for k in f.files}
            except Exception:
                pass                       # no file or no numpy: the old tint
        g, o = self._fields.get(f"{when}_gain"), self._fields.get(f"{when}_off")
        return (g, o) if g is not None and o is not None else None

    def relit(self, img, when, box=(0.0, 0.0, 1.0, 1.0)):
        """A day picture of the room as it looks in the evening or at night: each part of it changed the way the
        same part of pose 7 changes between its day and its night (the window goes dark, the lamp lights the bed).
        box: the part of the room the picture is (a face cut from it). None when there is no field."""
        f = self.field(when)
        if f is None:
            return None
        try:
            import numpy as np
            pg = self.pg
            w, h = img.get_size()

            def axis(n, a0, a1, cells):
                g = np.clip((a0 + (np.arange(n) + 0.5) / n * (a1 - a0)) * cells - 0.5, 0, cells - 1)
                i0 = np.floor(g).astype(int)
                return i0, np.minimum(i0 + 1, cells - 1), (g - i0).astype(np.float32)

            def up(F):
                y0, y1, fy = axis(h, box[1], box[3], F.shape[0])
                x0, x1, fx = axis(w, box[0], box[2], F.shape[1])
                rows = F[y0] * (1 - fy)[:, None, None] + F[y1] * fy[:, None, None]
                return rows[:, x0] * (1 - fx)[None, :, None] + rows[:, x1] * fx[None, :, None]

            arr = pg.surfarray.array3d(img).transpose(1, 0, 2).astype(np.float32) / 255.0
            out = np.clip(arr * up(f[0]) + up(f[1]), 0.0, 1.0)
            res = img.copy()
            pg.surfarray.blit_array(res, (out * 255 + 0.5).astype(np.uint8).transpose(1, 0, 2))
            return res
        except Exception:
            return None

    def tinted(self, img, when):
        """Poses 8 to 11 in the evening or at night: relit as pose 7 is (relit), or, without the learned light,
        the room's color and the lamp's warm light on it."""
        pg = self.pg
        lit = self.relit(img, when)
        if lit is not None:
            return lit
        img = img.copy()
        if when == "evening":
            img.fill((255, 214, 192), special_flags=pg.BLEND_RGB_MULT)
            glow = (70, 38, 10)
        else:
            img.fill((112, 122, 178), special_flags=pg.BLEND_RGB_MULT)
            glow = (120, 70, 24)
        try:
            import numpy as np
            w, h = img.get_size()
            ys, xs = np.ogrid[0:h, 0:w]
            d = np.sqrt((xs - LAMP[0] * w) ** 2 + (ys - LAMP[1] * h) ** 2) / (0.6 * w)
            f = np.clip(1 - d, 0, 1) ** 2
            arr = np.dstack([f * c for c in glow]).astype(np.uint8).transpose(1, 0, 2)
            img.blit(pg.surfarray.make_surface(arr), (0, 0), special_flags=pg.BLEND_RGB_ADD)
        except Exception:
            pass                          # no numpy: the color alone
        return img


def blit_cover(cv, img, fx=0.5, fy=0.5, alpha=None):
    """A covering picture on the whole screen, the part shown chosen by fx, fy (0 left/top to 1 right/bottom)."""
    if img is None:
        return
    sw, sh = cv.surf.get_size()
    iw, ih = img.get_size()
    x, y = -int(round((iw - sw) * fx)), -int(round((ih - sh) * fy))
    if alpha is not None:
        img.set_alpha(int(alpha * 255))
    cv.surf.blit(img, (x, y))
    if alpha is not None:
        img.set_alpha(None)
    return x, y, iw, ih


def stepped(t):
    """The camera's time, every frame (CAMERA_STEP). On twos (1/12 s) every move came out in steps."""
    return math.floor(t / CAMERA_STEP) * CAMERA_STEP


def pan(u):
    """Every camera move of the scenes the same: a slow, even drift over the same short way (0.35 to 0.65 of the
    picture), at the same pace from start to end. Before, each shot moved its own way and most of the picture,
    eased in and out, and over a short shot that read as the picture jumping."""
    return 0.35 + 0.3 * max(0.0, min(1.0, u))


def ease(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------- the girl, drawn frame by frame

# Pose 8 has its own drawings (g08_* from anime_prep.py): looks, blinks and the happy reaction, each drawn from
# the same picture, so only what moves changes. Timing is counted in frames at 24 fps, as anime is timed.
# A real blink closes in about 75 ms, stays shut 50 to 100 ms and opens over about 150 ms; the drawings are blended
# into each other (Girl.frame), so a blink reads as a lid moving, not as three pictures in a row.
BLINKS = {"normal": [("half", 1), ("closed", 2), ("half", 3)],      # closes fast, opens slower
          "quick": [("half", 1), ("closed", 1), ("half", 2)],
          "long": [("half", 2), ("closed", 6), ("half", 4)],         # after a laugh, or a change of thought
          "soft": [("half", 4)]}                                     # a half blink that does not close
HAPPY = [("in", 2), ("peak", 2), ("s1", 2), ("s2", 4), ("s3", 3), ("s2", 3), ("s3", 3), ("s2", 3), ("s3", 3),
         ("s1", 2), ("peak", 3), ("in", 3)]        # the laugh lands fast, the sparkles twinkle a while (1.4 s)
HAPPY_PEAK = 2 / 24                        # where the laugh lands: the motif plays here
FADE, FADE_LID = 0.12, 0.035               # seconds a drawing blends into the next: a look or a reaction, a lid
# a small nod with an encouraging smile, "almost, again", held long enough to be seen, then a soft smile
OOPS = [("oops_in", 3), ("oops_peak", 30), ("oops_in", 4)]
OOPS_PEAK = 3 / 24
# a part learned: joy, her hands clasped in front of her (in-between, held, back)
SURPRISE = [("proud_in", 3), ("proud", 34), ("proud_in", 4)]
THINK = [("think_in", 3), ("think", 36)]   # the finger goes to her chin, her eyes up to the side; held
SURPRISE_PEAK = 3 / 24                     # the motif as her hands meet
# (look, how often, how long it is held in seconds). Mostly her plain look, now and then a soft smile or a look
# down at the keys; the wide eyes and the look aside came for no reason and read as reactions to nothing
LOOKS = [("base", 8, (5.0, 11.0)), ("relaxed", 3, (3.0, 6.0)), ("down", 1, (1.5, 3.0))]


class Girl:
    """Which drawing of pose 8 shows now: she holds a look, blinks at uneven times in four kinds, now and then
    twice, changes her look mostly through a blink and sometimes on a plain cut, and plays the happy reaction.
    No two blinks are timed the same: each hold moves by a frame."""

    def __init__(self, seed=None):
        self.rnd = random.Random(seed)
        self.look, self.look_until = "base", 0.0
        self.seq, self.seq_at = [], 0.0
        self.next_blink = 0.0
        self.shown, self.prev, self.changed = "base", "base", 0.0

    def gap(self):
        """Seconds to the next blink: mostly 2 to 5, now and then a quick one or a long stare (a log-normal, as
        people blink), never two the same."""
        return max(1.2, min(9.0, self.rnd.lognormvariate(math.log(3.4), 0.45)))

    def frame(self, now):
        """(the drawing before, the drawing now, how far the change is from 0 to 1): a change is blended over a
        moment instead of cut, quicker for the lids (FADE_LID) than for a look or a reaction (FADE)."""
        cur = self.drawing(now)
        if cur != self.shown:
            self.prev, self.shown, self.changed = self.shown, cur, now
        lid = {"half", "closed"} & {cur, self.prev}
        u = (now - self.changed) / (FADE_LID if lid else FADE)
        return self.prev, cur, max(0.0, min(1.0, u))

    def _play(self, seq, now, jitter=True):
        self.seq = [(d, (n + (self.rnd.choice((0, 0, 1)) if jitter else 0)) / 24) for d, n in seq]
        self.seq_at = now

    def busy(self, now):
        return bool(self.seq) and now - self.seq_at < sum(d for _, d in self.seq)

    def happy(self, now):
        """The laugh: in-between, peak, the sparkles in and twinkling, back; then a long blink and calm eyes.
        Returns how long it lasts."""
        self._play(HAPPY + [("base", 3)] + BLINKS["normal"], now, jitter=False)
        self.look, self.look_until = "relaxed", now + sum(d for _, d in self.seq) + 2.0
        self.next_blink = self.look_until
        return sum(d for _, d in self.seq)

    def oops(self, now):
        """The small nod, "almost, again": in-between, held, back; then a quick blink and a soft smile."""
        self._play(OOPS + [("relaxed", 3)] + BLINKS["quick"], now, jitter=False)
        self.look, self.look_until = "relaxed", now + sum(d for _, d in self.seq) + 2.0
        self.next_blink = self.look_until
        return sum(d for _, d in self.seq)

    def surprise(self, now):
        self._play(SURPRISE + [("base", 3)] + BLINKS["quick"], now, jitter=False)
        self.look, self.look_until = "wide", now + sum(d for _, d in self.seq) + 1.8
        self.next_blink = self.look_until
        return sum(d for _, d in self.seq)

    def think(self, now, hold=2.0):
        """Thinking: the finger to her chin, her eyes up to the side; held."""
        self._play(THINK, now, jitter=False)
        self.look, self.look_until = "think", now + sum(d for _, d in self.seq) + hold
        self.next_blink = self.look_until
        return sum(d for _, d in self.seq) + hold

    def drawing(self, now):
        if self.seq:
            t = now - self.seq_at
            for d, dur in self.seq:
                if t < dur:
                    return d
                t -= dur
            self.seq = []
        if now >= self.look_until:
            looks = [x for x in LOOKS if x[0] != self.look]
            look, _, (lo, hi) = self.rnd.choices(looks, [w for _, w, _ in looks])[0]
            self.look, self.look_until = look, now + self.rnd.uniform(lo, hi)
            if look != "side" and self.rnd.random() < 0.7:          # most looks change through a blink
                self._play(BLINKS["normal"], now)
                self.next_blink = now + self.gap()
                return self.drawing(now)
        if now >= self.next_blink:
            if self.next_blink:
                kind = self.rnd.choices(["normal", "quick", "long", "soft"], [5, 2, 1, 2])[0]
                seq = list(BLINKS[kind])
                if kind == "normal" and self.rnd.random() < 0.25:   # now and then a double blink
                    seq += [(self.look, 5)] + BLINKS["quick"]
                self._play(seq, now)
            self.next_blink = now + self.gap()
            if self.seq:
                return self.drawing(now)
        return self.look


# ---------------------------------------------------------------- the theme

class Anime:
    def __init__(self, app):
        self.app = app
        self.pics = Pics(app.pg)
        self.fx_due = []                  # (when, "fxon 72") still to send
        self.scene = None                 # a full-screen sequence playing: {"shots", "at", "then"}
        self.cutin = None                 # the face in the corner: {"face", "manpu", "at", "until"}
        self.seen = {"mode": None, "pos": None, "live": 0, "phase": "", "counting": False}
        self.eyecatch = None              # None, "on" or "done" in this lesson
        self.last_oops = 0.0
        self.last_happy = -1e9
        self.ending = None                # the episode's ending: started when the lesson's summary arrives
        self.girl = Girl()

    # ---- the look of a screen
    def look_key(self, scene):
        """What the kept background depends on, apart from the minute and the Sun (App.draw_backdrop)."""
        if scene in ("lesson", "summary"):
            return ("room", self.room_pose(), self.duet())
        return ("roofs",)

    def background(self, cv, scene, live=None):
        """The theme's background for a screen: the room in a lesson, the rooftops anywhere else."""
        alt, morning = sun_now(self.app)
        if scene in ("lesson", "summary"):
            when = period(alt, morning)
            pose = self.room_pose()
            img = self.room(pose, when)
            sw, sh = cv.surf.get_size()
            blit_cover(cv, img, 0.6, 0.35)
            veil = (8, 10, 24, 96 if self.duet() else 132) if when != "day" else (255, 248, 240, 70 if self.duet() else 104)
            with cv.physical():
                cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, veil)
            return
        self.roofs(cv, alt, morning, live)
        if scene == "rest":
            self.top_shade(cv, alt)
        else:
            dark = alt <= 1
            with cv.physical():
                cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (8, 10, 24, 120) if dark else (255, 250, 244, 96))

    def room(self, pose, when):
        sw, sh = self.app.cv.surf.get_size()
        if pose == 7:
            return self.pics.cover(f"room_07_{when}", sw, sh)
        return self.pics.cover(f"room_{pose:02d}", sw, sh, tint=None if when == "day" else when)

    def roofs(self, cv, alt, morning, live=None, fx=0.5, zoom=1.0):
        sw, sh = cv.surf.get_size()
        mix = sky_mix(alt, morning)
        box = None
        for i, (name, w) in enumerate(mix):
            img = self.pics.cover(name, sw, sh, zoom)
            box = blit_cover(cv, img, fx, 0.5, None if i == 0 else w) or box
        if box:
            self.windows(cv, box, alt)
            self.sky_over(cv, box, alt, live)

    # ---- the windows of the city: each lights and goes dark in its own time
    def _windows_data(self):
        """windows.json and the lit windows' pack (tools/anime_prep.py windows), loaded once; None without them."""
        if not hasattr(self, "_win"):
            self._win = None
            try:
                data = json.loads((DATA / "windows.json").read_text(encoding="utf-8"))
                pack = self.pics.layer("windows_lit")
                if pack is not None and data.get("windows"):
                    self._win = (data, pack)
            except Exception:
                pass
        return self._win

    @staticmethod
    def lit_share(hour):
        """How many of the windows are lit at an hour of the night: most in the evening, fewer after midnight, the
        fewest before dawn, some again as people wake."""
        pts = WINDOW_SHARE
        h = hour % 24
        for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
            if h0 <= h <= h1:
                return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
        return pts[0][1]

    def window_level(self, i, secs):
        """0 (dark) to 1 (lit) for window i at secs (local seconds): every window has its own rhythm (a time of 25
        to 75 minutes between two chances to change) and its own habit (lit more or less than the others); when it
        changes it fades over WINDOW_FADE seconds, so the city lights one window at a time, never all at once."""
        h = zlib.crc32(f"win{i}".encode())
        period = 1500 + (h % 3000)
        phase = (h >> 12) % period
        habit = ((h >> 20) % 300) / 1000 - 0.15

        def lit(slot):
            u = zlib.crc32(f"{i}:{slot}".encode()) / 0xFFFFFFFF
            hour = ((slot * period - phase) % 86400) / 3600
            return 1.0 if u < self.lit_share(hour) + habit else 0.0
        slot = int((secs + phase) // period)
        since = (secs + phase) - slot * period
        a, b = lit(slot - 1), lit(slot)
        return b if a == b else a + (b - a) * min(1.0, since / WINDOW_FADE)

    def windows(self, cv, box, alt):
        """The lit windows over the rooftops from the blue hour to the morning (the pictures have every window dark)."""
        vis = max(0.0, min(1.0, (4 - alt) / 8))
        if vis <= 0:
            return
        got = self._windows_data()
        if got is None:
            return
        data, pack = got
        x0, y0, iw, ih = box
        k = iw / data["size"][0]
        key = ("winpack", round(k, 4))
        img = self.pics.sized.get(key)
        if img is None:
            pw, ph = pack.get_size()
            img = self.app.pg.transform.smoothscale(pack, (max(1, round(pw * k)), max(1, round(ph * k))))
            self.pics._keep(key, img)
        try:
            d = self.app.local()
            secs = d.timestamp() + (d.utcoffset().total_seconds() if d.utcoffset() else 0)
        except Exception:
            secs = time.time()
        Rect = self.app.pg.Rect
        for i, (x, y, w, h, px, py, _n) in enumerate(data["windows"]):
            a = vis * self.window_level(i, secs)
            if a <= 0.02:
                continue
            img.set_alpha(int(255 * a))
            cv.surf.blit(img, (int(x0 + x * k), int(y0 + y * k)),
                         Rect(int(px * k), int(py * k), max(1, round(w * k)), max(1, round(h * k))))
        img.set_alpha(None)

    # ---- the sky over the rooftops: the city's stars, the Sun and the Moon, drawn the anime way
    def proj(self, cv, st, az, alt, box):
        """Where a body at (azimuth, altitude) is over the rooftops, in screen pixels, matching the sky theme
        (sky.Sky.proj): across, the very same place on the screen (looking south, north south of the equator,
        180 degrees from edge to edge); up, the same share of the sky's height (0 to 62 degrees), here from the
        horizon behind the roofs (HORIZON of the picture's height) to the top of the picture."""
        from . import sky as sky_mod
        f = 180 if st.lat >= 0 else 0
        a = ((az - f + 540) % 360) - 180
        x = cv.ox + (sky_mod.W / 2 + a / 180 * sky_mod.W) * cv.scale
        x0, y0, iw, ih = box
        hy, top = y0 + ih * HORIZON, y0 + ih * ZENITH_AT
        return x, hy - (hy - top) * max(-10.0, min(90.0, alt)) / ZENITH_ALT

    def sky_over(self, cv, box, sun_alt, live):
        """The stars bright enough for the twilight (the sky theme's limits), then the Sun and the Moon. The stars
        go into live (twinkled on every frame by stars_live) when the rooftops are a still layer."""
        st = getattr(self.app, "sky_st", None)
        if st is None:
            return
        x0, y0, iw, ih = box
        sw, _ = cv.surf.get_size()
        clear = y0 + ih * CLEAR
        dark = max(0.0, min(1.0, (-3 - sun_alt) / 9))         # the stars come out as the blue hour deepens
        if dark > 0:
            lim = 4 if sun_alt < -18 else 3 if sun_alt < -12 else 2 if sun_alt < -8 else 0.8
            k = max(1.0, cv.scale)
            stars = []
            for az, sa, mag, _name, i in st.stars:
                if mag > lim:
                    continue
                px, py = self.proj(cv, st, az, sa, box)
                if px < 0 or px > sw or py > clear:
                    continue
                low = min(1.0, (clear - py) / (0.05 * ih))     # fainter just over the roofs, in the city's haze
                b = max(0.15, min(1.0, (lim - mag + 0.6) / 3)) * dark * low
                stars.append((px, py, mag, b, i, k))
            for j, (_pname, paz, pa) in enumerate(getattr(st, "planets", ()) or ()):
                px, py = self.proj(cv, st, paz, pa, box)     # the planets the sky theme shows, as its brightest sparks
                if 0 <= px <= sw and py <= clear:
                    low = min(1.0, (clear - py) / (0.05 * ih))
                    stars.append((px, py, -1.0, dark * low, 9000 + j, k))
            if live is not None:
                live.extend(stars)
            else:
                self.stars_live(cv, stars, self.app.t())
        self.sun_draw(cv, st, box, clear)
        self.moon_over(cv, st, box, clear)

    def _sprite(self, kind, r, rgb):
        """A soft round glow or a four-pointed sparkle, r pixels from its middle, made once and kept."""
        key = ("spr", kind, int(r), rgb)
        img = self.pics.small.get(key)
        if img is not None:
            return img
        pg = self.app.pg
        n = int(r) * 2 + 1
        img = pg.Surface((n, n), pg.SRCALPHA)
        try:
            import numpy as np
            ys, xs = np.mgrid[0:n, 0:n] - int(r)
            d = np.sqrt(xs ** 2 + ys ** 2) / max(1.0, r)
            if kind == "spark":                  # a thin cross of light that fades to its tips, round in the middle
                a = np.maximum(np.clip(1 - np.abs(xs) / 1.2, 0, 1) * np.clip(1 - d, 0, 1) ** 1.6,
                               np.clip(1 - np.abs(ys) / 1.2, 0, 1) * np.clip(1 - d, 0, 1) ** 1.6)
                a = np.maximum(a, np.clip(1 - d * 4, 0, 1))
            else:
                a = np.clip(1 - d, 0, 1) ** 2
            arr = pg.surfarray.pixels_alpha(img)
            img.fill(rgb + (0,))
            arr[:] = (a.T * 255).astype(np.uint8)
            del arr
        except Exception:
            pg.draw.circle(img, rgb + (200,), (int(r), int(r)), max(1, int(r // 2)))
        self.pics.small[key] = img
        return img

    def stars_live(self, cv, stars, t):
        """The stars over the still layer, each twinkling in its own time: the bright ones as anime sparkles (a soft
        cross of light), the faint ones as small round dots, a little blue or warm by their color."""
        for px, py, mag, b, i, k in stars:
            tw = 0.7 + 0.3 * math.sin(t * (1.3 + (i % 7) * 0.17) + i * 1.7)
            a = int(255 * max(0.0, min(1.0, b * tw)))
            if a < 8:
                continue
            rgb = (210, 228, 255) if i % 3 == 0 else (255, 244, 222) if i % 3 == 1 else (255, 255, 250)
            if mag < 1.6:
                img = self._sprite("spark", round((7 - mag * 1.6) * k * (0.85 + 0.15 * tw)), rgb)
            else:
                img = self._sprite("dot", round(max(1.5, 2.8 - mag * 0.35) * k), rgb)
            img.set_alpha(a)
            w, h = img.get_size()
            cv.surf.blit(img, (int(px - w // 2), int(py - h // 2)))
            img.set_alpha(None)

    def sun_draw(self, cv, st, box, clear):
        """The Sun where it really is: a wide warm glow and a white core, the way anime paints it. Under the roofs'
        line it is hidden and only its glow shows in the sky over them, warmer and redder as it nears the horizon."""
        alt = st.sun.altitude
        if alt < -4:
            return
        x, y = self.proj(cv, st, st.sun.azimuth, alt, box)
        sw, sh = cv.surf.get_size()
        if x < -200 or x > sw + 200:
            return
        k = max(1.0, cv.scale)
        low = max(0.0, min(1.0, alt / 12))
        warm = (255, int(196 + 40 * low), int(130 + 90 * low))
        fade = max(0.0, min(1.0, (alt + 4) / 4))
        cv.surf.set_clip(cv.pg.Rect(0, 0, sw, int(clear + box[3] * 0.04)))
        try:
            for r, a in ((150, 0.42), (70, 0.55)):
                g = self._sprite("dot", int(r * k), warm)
                g.set_alpha(int(255 * a * fade))
                cv.surf.blit(g, (int(x - g.get_width() // 2), int(y - g.get_height() // 2)))
                g.set_alpha(None)
            if y < clear:
                core = self._sprite("dot", int(20 * k), (255, 252, 240))
                core.set_alpha(int(255 * fade))
                cv.surf.blit(core, (int(x - core.get_width() // 2), int(y - core.get_height() // 2)))
                core.set_alpha(None)
                cv.pg.draw.circle(cv.surf, (255, 253, 245), (int(x), int(y)), int(9 * k))
        finally:
            cv.surf.set_clip(None)

    def moon_over(self, cv, st, box, clear):
        """The Moon in its real phase and place, with a soft blue glow; pale by day, when it is up."""
        from . import sky as sky_mod
        alt = st.moon.altitude
        if alt <= 0:
            return
        x, y = self.proj(cv, st, st.moon.azimuth, alt, box)
        sw, _ = cv.surf.get_size()
        if y > clear or x < 0 or x > sw:
            return
        sun = st.sun.altitude
        faint = 0.45 if sun > 6 else 0.45 + 0.55 * min(1.0, (6 - sun) / 10)
        k = max(1.0, cv.scale)
        g = self._sprite("dot", int(46 * k), (200, 215, 255))
        g.set_alpha(int(255 * 0.35 * faint * (st.illum if hasattr(st, "illum") else 1.0)))
        cv.surf.blit(g, (int(x - g.get_width() // 2), int(y - g.get_height() // 2)))
        g.set_alpha(None)
        with cv.physical():
            sky_mod.moon_draw(cv, (x - cv.ox) / cv.scale, (y - cv.oy) / cv.scale, 13, st.phase, sun,
                              faint=faint, north=st.lat < 0)


    def top_shade(self, cv, alt):
        """A soft band behind the clock and the lines on the rest screen, light by day and dark at night."""
        sw, sh = cv.surf.get_size()
        dark = alt <= 1
        key = ("shade", sw, sh, dark)
        img = self.pics.sized.get(key)
        if img is None:
            pg = cv.pg
            h = int(sh * 0.42)
            img = pg.Surface((sw, h), pg.SRCALPHA)
            col = (10, 12, 30) if dark else (255, 250, 244)
            top = 150 if dark else 170
            for y in range(h):
                img.fill(col + (int(top * (1 - y / h) ** 1.6),), pg.Rect(0, y, sw, 1))
            self.pics._keep(key, img)
        cv.surf.blit(img, (0, 0))

    # ---- the lesson as it goes
    def room_pose(self):
        c = self.cutin
        if c and time.monotonic() < c["until"] and c.get("pose"):
            return c["pose"]
        st = self.app.screen.engine.state if self.app.screen.lesson_on() else {}
        return 7 if (st.get("mode") == "demo" or self.duet()) else 8

    def duet(self):
        st = self.app.screen.engine.state
        return bool(self.app.screen.lesson_on() and str(st.get("phase", "")).endswith("_band"))

    def follow_lesson(self):
        """From App.tick in a lesson: what the engine did since the last look, into the girl's reactions."""
        s = self.app.screen
        st = s.engine.state
        seen = self.seen
        now = time.monotonic()
        mode, pos, phase, counting = st.get("mode"), st.get("pos", 0), str(st.get("phase") or ""), bool(st.get("counting"))
        if seen["mode"] is None:                      # the lesson's first look
            seen.update(mode=mode, pos=pos, phase=phase, counting=counting)
            self.eyecatch = None
        # she comes in for a part learned, and for a round right at most once in HAPPY_GAP seconds: on every
        # round (5 to a part) she was in the corner all the lesson long
        if isinstance(pos, int) and isinstance(seen["pos"], int) and pos > seen["pos"]:
            self.react_surprise()
            self.last_happy = now
        elif mode == "ok" and seen["mode"] != "ok" and now - self.last_happy > HAPPY_GAP:
            self.last_happy = now
            self.react_happy()
        # a wrong key: one that is neither the note waited for nor the one just played
        live = st.get("live") or []
        new = [n for i, n in live if i > seen["live"]]
        if live:
            seen["live"] = max(i for i, _ in live)
        midi = st.get("midi") or []
        at = st.get("expect", -1)
        if new and mode == "wait" and isinstance(at, int) and 0 <= at < len(midi):
            ok = {midi[j] % 12 for j in (at - 1, at) if 0 <= j < len(midi) and isinstance(midi[j], int)}
            if any(n % 12 not in ok for n in new) and now - self.last_oops > 15:
                self.last_oops = now
                self.react_oops()                   # no sound in the middle of playing: it would cover the keys
        if phase.endswith("_band") and not seen["phase"].endswith("_band"):
            self.react(None, None, None, 1.8, face="hands", label="duet")   # silent: the count-in is sounding
        # the eyecatch: the breath before the last run with the band, and its count-in; no band, the count-in
        # of the last run
        if self.eyecatch is None and (phase == "final_breath" or (phase == "final" and counting and not st.get("band"))):
            self.eyecatch, self.eyecatch_at = "on", self.app.t()
            self.sound("sting")
        elif self.eyecatch == "on" and not (phase == "final_breath" or counting or self.app.t() - self.eyecatch_at < 1.2):
            self.eyecatch = "done"
        seen.update(mode=mode, pos=pos, phase=phase, counting=counting)

    def lesson_ended(self):
        self.seen = {"mode": None, "pos": None, "live": 0, "phase": "", "counting": False}
        self.cutin = None

    def drawn(self):
        """Pose 8 drawn frame by frame is in the data (anime_prep.py with the kit's girl08)."""
        return self.pics.path("g08face_base") is not None

    def react_happy(self, quiet=False):
        """A round played right: the laugh drawn frame by frame, the motif on the laugh, in the corner.
        quiet: no motif (the game, where the song is playing)."""
        if not self.drawn():
            return self.react(10, "sparkle", None if quiet else "happy", 1.4)
        now = time.monotonic()
        secs = self.girl.happy(now)
        self.cutin = {"pose": None, "face": "girl", "manpu": None, "at": now, "until": now + secs + 0.6,
                      "impact": False, "label": None}
        if not quiet:
            self.sound("happy", HAPPY_PEAK)
        self.app.layer_dirty()

    def react_oops(self):
        """A wrong key: the oops drawn frame by frame, in the corner, silent (she does not cover the keys' sound)."""
        if not self.pics.path("g08face_oops_peak"):
            return self.react(11, "drop", None, 1.3)
        now = time.monotonic()
        secs = self.girl.oops(now)
        self.cutin = {"pose": None, "face": "girl", "manpu": None, "at": now, "until": now + secs + 0.4,
                      "impact": False, "label": None}
        self.app.layer_dirty()

    def react_surprise(self):
        """A part learned: the surprise drawn frame by frame, the motif as her eyes open."""
        if not self.pics.path("g08face_proud"):
            return self.react(9, "surprise", "surprised", 1.6, impact=True)
        now = time.monotonic()
        secs = self.girl.surprise(now)
        self.cutin = {"pose": None, "face": "girl", "manpu": None, "at": now, "until": now + secs + 0.4,
                      "impact": False, "label": None}
        self.sound("surprised", SURPRISE_PEAK)
        self.app.layer_dirty()

    def react(self, pose, manpu, motif, secs, impact=False, face=None, label=None):
        now = time.monotonic()
        self.cutin = {"pose": pose, "face": face or (f"face_{pose:02d}" if pose else None), "manpu": manpu,
                      "at": now, "until": now + secs, "impact": impact, "label": label}
        if motif:
            self.sound(motif)
        self.app.layer_dirty()

    def eyecatch_on(self):
        return self.eyecatch == "on" and self.app.screen.lesson_on()

    PANEL_H = 220                                 # the manga panel over a lesson (tools/manga_panel.py)

    def has_panel(self):
        return self.pics.path("manga_panel") is not None

    def draw_panel(self, cv):
        """The manga panel at the top of a lesson: a printed page that does not move (approved as demo 3).
        Drawn on the screen as it is, not mirrored in Hebrew: a drawing keeps its sides."""
        sw = cv.surf.get_size()[0]
        img = self.pics.sized_layer("manga_panel", sw, self.PANEL_H * cv.scale)
        if img is None:
            return False
        with cv.physical():
            px, py = cv.P(0, 0)
            cv.surf.blit(img, (int(px), int(py)))
        return True

    def draw_cutin(self, cv, left=False, panel=False):
        """The girl's face in the corner, sliding and fading in, with a symbol popping beside it.
        left: in the corner of the time (the game keeps the song, the tempo and the lives in the other one); on the
        screen that is the left in English and the right in Hebrew."""
        c = self.cutin
        if not c:
            return
        now = time.monotonic()
        if now >= c["until"]:
            self.cutin = None
            self.app.layer_dirty()
            return
        lang = self.app.lang
        age, rest = now - c["at"], c["until"] - now
        # in and out smoothly: a slide that slows as it lands, and a fade with it (on two steps it jumped)
        come = ease(age / CUTIN_IN) if age < CUTIN_IN else 1.0
        go = ease(rest / CUTIN_OUT) if rest < CUTIN_OUT else 1.0
        show = min(come, go)
        slide = 70 * (1 - show)
        w, h, y = 200, 125, 8
        # the corner of the time, in a lesson as in the game: the left in English, the right in Hebrew, where the
        # plan mirrors it (as the face has always been placed)
        left = not cv.rtl
        if panel:
            # over the manga panel: the colored frame, tilted, sits on the panel's cut and breaks out of it, over
            # her face in the page; the page itself does not move (demo 3)
            w, h, y = 240, 150, 130
        # held, it breathes: a slow rise and fall of a pixel or two, so a reaction held long is never a still
        y += 1.5 * math.sin(age * 2.4)
        x = (W - 30 - w + slide) if panel else (8 - slide if left else W - 8 - w + slide)
        pw, ph = int(w * cv.scale), int(h * cv.scale)
        when = period(*sun_now(self.app))
        pg = self.app.pg
        prev, a = None, 1.0
        if c["face"] == "hands":
            name, box = f"close_hands_{when}", None
        elif c["face"] == "girl":
            p0, p1, a = self.girl.frame(now)              # the drawing of this moment, blended from the one before
            name, prev, box = "g08face_" + p1, "g08face_" + p0, FACE_BOX["g08face"]
        else:
            name, box = c["face"], FACE_BOX.get(c["face"] or "")
        lit = when if box else None                        # her face relit for the hour, as the room is
        with cv.physical():
            px, py = cv.P(x, y)
            if c.get("impact") and age < 2 * STEP:
                img = self.pics.sized_layer("fx_lines", pw, ph)
            elif c["face"] == "hands":
                # her hands on the keys: the picture a little larger than the frame, the frame moving slowly over it,
                # so the still drawing is alive while it is held
                big = self.pics.sized_layer(name, pw * 1.14, ph * 1.14)
                img = None
                if big is not None:
                    bw, bh = big.get_size()
                    ox = int((bw - pw) * (0.5 + 0.5 * math.sin(age * 0.7)))
                    oy = int((bh - ph) * (0.5 + 0.4 * math.cos(age * 0.5)))
                    img = big.subsurface((max(0, min(ox, bw - pw)), max(0, min(oy, bh - ph)), pw, ph))
            else:
                img = self.pics.sized_layer(name, pw, ph, lit, box) if name else None
                if img is not None and prev and prev != name and a < 1.0:
                    under = self.pics.sized_layer(prev, pw, ph, lit, box)
                    if under is not None:
                        mix = under.copy()
                        img.set_alpha(int(255 * a))
                        mix.blit(img, (0, 0))
                        img.set_alpha(None)
                        img = mix
            b = int((7 if panel else 4) * cv.scale)
            card = pg.Surface((pw + 2 * b, ph + 2 * b), pg.SRCALPHA)
            if panel:
                # the frame drawn on a surface of its own, then turned 3 degrees as one piece: ink, white, picture
                card.fill((29, 26, 34))
                card.fill((255, 255, 255), (int(3 * cv.scale), int(3 * cv.scale), pw + 2 * b - int(6 * cv.scale),
                                            ph + 2 * b - int(6 * cv.scale)))
            else:
                card.fill((255, 255, 255))
            if img is not None:
                card.blit(img, (b, b))
            if panel:
                card = pg.transform.rotozoom(card, 3, 0.94 + 0.06 * show)
            if show < 1.0:
                card.set_alpha(int(255 * show))
            cx, cy = px + pw / 2, py + ph / 2
            cv.surf.blit(card, (int(cx - card.get_width() / 2), int(cy - card.get_height() / 2)))
            if panel:
                return
            if c.get("label"):
                label = tr(lang, c["label"])
                lw = cv.measure(label, 14, True) + 20
                cv.rect(x + w - lw - 6, y + h - 30, lw, 24, (40, 30, 52, 220), r=4)
                cv.text(label, x + w - lw / 2 - 6, y + h - 13, 14, (255, 255, 255), True, "center")
            if c.get("manpu") and age >= STEP:
                big = 1.0 if age >= 3 * STEP else 0.8
                mw, mh = 54 * big, 54 * big
                mx, my = x - mw * 0.55, y + 4
                m = self.pics.sized_layer("manpu_" + c["manpu"], mw * cv.scale, mh * cv.scale)
                if m is not None:
                    ix, iy = cv.P(mx, my)
                    cv.surf.blit(m, (int(ix), int(iy)))

    def draw_eyecatch(self, cv, t):
        """The eyecatch: the still card with the real logo on its empty left side, and lights rising."""
        sw, sh = cv.surf.get_size()
        blit_cover(cv, self.pics.cover("eyecatch", sw, sh), 0.5, 0.5)
        self.logo(cv, 60, 170, 330, 93)
        self.lights(cv, t - getattr(self, "eyecatch_at", t), 18, (255, 236, 200))

    def logo(self, cv, x, y, w, h):
        img = self.pics.layer("logo_light")
        with cv.physical():
            if img is None:
                cv.text("Armonico", x + w / 2, y + h * 0.6, 40, (20, 36, 64), True, "center")
                return
            iw, ih = img.get_size()
            k = min(w / iw, h / ih)
            m = self.pics.sized_layer("logo_light", iw * k * cv.scale, ih * k * cv.scale)
            px, py = cv.P(x + (w - iw * k) / 2, y + (h - ih * k) / 2)
            cv.surf.blit(m, (int(px), int(py)))

    def lights(self, cv, t, n, rgb, top=0.0):
        """Soft points of light drifting up, on twos."""
        t = stepped(max(0.0, t))
        rnd = random.Random(31)
        with cv.physical():
            for i in range(n):
                x0, sp, r, ph = rnd.uniform(0, W), rnd.uniform(14, 34), rnd.uniform(2.5, 6), rnd.uniform(0, 6.28)
                y = H - ((t * sp + rnd.uniform(0, H)) % (H * (1 - top)))
                x = x0 + 10 * math.sin(t * 0.8 + ph)
                a = 0.35 + 0.3 * math.sin(t * 1.7 + ph)
                cv.circle(x, y, r * 2.2, rgb + (int(255 * a * 0.25),))
                cv.circle(x, y, r, rgb + (int(255 * a),))

    # ---- the sound of a feeling
    def sound(self, name, delay=0.0):
        t = time.monotonic() + delay
        for dt, note in MOTIFS.get(name, []):
            self.fx_due += [(t + dt, f"fxon {note}"), (t + dt + FX_HOLD, f"fxoff {note}")]
        self.fx_due.sort()

    def tick(self):
        if self.fx_due:
            now = time.monotonic()
            while self.fx_due and self.fx_due[0][0] <= now:
                _, msg = self.fx_due.pop(0)
                self.app.screen.pub(TOPICS + "/device/note", msg)
        s = self.app.screen
        if s.lesson_on():
            self.follow_lesson()
        elif self.seen["mode"] is not None:
            self.lesson_ended()

    # ---- the episodes
    def episode_of(self, song):
        """(learned, total) of a song, from the engine's overview."""
        for x in self.app.screen.songs() or []:
            if x.get("name") == song:
                return int(x.get("learned", 0) or 0), int(x.get("total", 0) or 0)
        return 0, 0

    def _seen_file(self):
        return Path(os.environ.get("DATA_DIR", "/var/lib/armonico")) / EPISODES_FILE

    def shown(self, song, ep):
        try:
            data = json.loads(self._seen_file().read_text())
        except (OSError, ValueError):
            return False
        return ep in (data.get(song) or [])

    def went_back(self, song, ep):
        """A later episode was shown before: the progress was reset since, so this one plays again."""
        try:
            return max(json.loads(self._seen_file().read_text()).get(song) or [0]) > ep
        except (OSError, ValueError, TypeError):
            return False

    def mark_shown(self, song, ep):
        f = self._seen_file()
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            data = {}
        eps = set(data.get(song) or [])
        if eps and max(eps) > ep:
            eps = set()                   # the song's progress was reset: its episodes are new again
        eps.add(ep)
        data[song] = sorted(eps)
        try:
            f.write_text(json.dumps(data))
        except OSError:
            pass

    def preroll(self, song):
        """Before a lesson: "previously", the cold open and the opening, the first time this part is learned.
        True when it started (the lesson starts when it ends or is skipped)."""
        if getattr(self, "bypass", False) or self.scene is not None:
            return False
        learned, total = self.episode_of(song)
        if not total or learned >= total:
            return False                  # a song learned to its end: a review, no new episode
        ep = learned + 1
        if self.shown(song, ep) and not self.went_back(song, ep):
            return False
        self.mark_shown(song, ep)
        self.ep_info = {"song": song, "ep": ep, "learned": learned, "total": total}
        shots = []
        if learned:
            shots.append({"kind": "prev", "dur": 3.4})
        shots += [{"kind": "cold_face", "dur": 1.5, "motif": "dots", "manpu": "dots"},
                  {"kind": "impact", "dur": 0.17},
                  {"kind": "cold_hands", "dur": 1.4, "motif": "start"},
                  {"kind": "op_roofs", "dur": 1.9},
                  {"kind": "op_title", "dur": 2.6, "motif": "sparkle"}]
        self.play(shots, then=lambda: self.start(song))
        return True

    def start(self, song):
        self.bypass = True
        try:
            self.app.screen.start_lesson(song)
        finally:
            self.bypass = False

    def play(self, shots, then=None):
        at = self.app.t()
        for sh in shots:
            sh["at"] = at
            at += sh["dur"]
        self.scene = {"shots": shots, "end": at, "then": then, "fired": set()}
        self.app.layer_dirty()

    def scene_on(self):
        return self.scene is not None

    def skip(self):
        sc, self.scene = self.scene, None
        self.fx_due = [x for x in self.fx_due if x[1].startswith("fxoff")]
        self.app.layer_dirty()
        if sc and sc.get("then"):
            sc["then"]()

    def draw_scene(self, cv, t):
        sc = self.scene
        if t >= sc["end"]:
            self.skip()
            return False
        shot = next(s for s in sc["shots"] if s["at"] <= t < s["at"] + s["dur"]) if any(
            s["at"] <= t < s["at"] + s["dur"] for s in sc["shots"]) else sc["shots"][-1]
        i = sc["shots"].index(shot)
        if i not in sc["fired"]:
            sc["fired"].add(i)
            if shot.get("motif"):
                self.sound(shot["motif"])
        u = (t - shot["at"]) / shot["dur"]
        getattr(self, "shot_" + shot["kind"])(cv, t, stepped(t - shot["at"]), u, shot)
        return True

    # each shot: (cv, t, local time on twos, 0..1 through the shot, the shot)
    def _when(self):
        alt, morning = sun_now(self.app)
        return period(alt, morning)

    def caption(self, cv, text, x, y, size, align="left", color=(255, 255, 255), edge=(30, 22, 40)):
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1.5, -1.5), (1.5, 1.5), (-1.5, 1.5), (1.5, -1.5)):
            cv.text(text, x + dx, y + dy, size, edge, True, align)
        cv.text(text, x, y, size, color, True, align)

    def shot_prev(self, cv, t, lt, u, shot):
        sw, sh = cv.surf.get_size()
        img = self.pics.cover(f"room_07_{self._when()}", sw, sh, 1.18)
        blit_cover(cv, img, pan(u), 0.4)
        with cv.physical():
            cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (20, 14, 30, 70))
        lang, info = self.app.lang, self.ep_info
        self.caption(cv, tr(lang, "prev") + "…", 40, 92, 34)
        n = info["learned"]
        sub = tr(lang, "parts", n=n) if n > 1 else tr(lang, "part1")
        self.caption(cv, sub, 42, 128, 18)
        for k in range(min(info["total"], 16)):
            cv.rect(42 + k * 26, 146, 20, 7, (255, 207, 138) if k < n else (255, 255, 255, 90), r=3)

    def shot_cold_face(self, cv, t, lt, u, shot):
        sw, sh = cv.surf.get_size()
        blit_cover(cv, self.pics.cover(f"close_face_{self._when()}", sw, sh, 1.08), 0.5, 0.6 - 0.2 * u)
        if lt >= 0.5:
            big = 1.0 if lt >= 0.5 + 2 * STEP else 0.8
            m = self.pics.sized_layer("manpu_dots", 150 * big * cv.scale, 40 * big * cv.scale)
            with cv.physical():
                px, py = cv.P(W - 250, 70)
                cv.surf.blit(m, (int(px), int(py))) if m is not None else None

    def shot_impact(self, cv, t, lt, u, shot):
        sw, sh = cv.surf.get_size()
        blit_cover(cv, self.pics.cover("fx_lines", sw, sh), 0.5, 0.5)
        if lt < STEP:
            with cv.physical():
                cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (255, 255, 255, 200))

    def shot_cold_hands(self, cv, t, lt, u, shot):
        sw, sh = cv.surf.get_size()
        blit_cover(cv, self.pics.cover(f"close_hands_{self._when()}", sw, sh, 1.1), pan(u), 0.5)

    def shot_op_roofs(self, cv, t, lt, u, shot):
        alt, morning = sun_now(self.app)
        self.roofs(cv, alt, morning, fx=pan(u), zoom=1.2)
        # the curtain at the edge of the balcony door, swaying on threes, and the cat walking along the rail
        frame = int(lt / (3 * STEP)) % 3
        when = period(alt, morning)
        c = self.pics.layer(f"curtain_{frame}")
        if c is not None:
            iw, ih = c.get_size()
            hh = H * 0.96
            m = self.pics.sized_layer(f"curtain_{frame}", iw * hh / ih * cv.scale, hh * cv.scale, when)
            with cv.physical():
                px, py = cv.P(-iw * hh / ih * 0.55, -8)
                cv.surf.blit(m, (int(px), int(py)))
        k = int(lt / (2 * STEP)) % 2
        cat = self.pics.layer(f"cat_{k}")
        if cat is not None:
            iw, ih = cat.get_size()
            ch = 58
            m = self.pics.sized_layer(f"cat_{k}", iw * ch / ih * cv.scale, ch * cv.scale)
            with cv.physical():
                px, py = cv.P(W * 1.05 - (W * 0.7) * ease(u), H - 112)
                cv.surf.blit(m, (int(px), int(py)))

    def shot_op_title(self, cv, t, lt, u, shot):
        sw, sh = cv.surf.get_size()
        blit_cover(cv, self.pics.cover("fx_golden", sw, sh, 1.1), 0.5, pan(u))
        self.lights(cv, lt, 22, (255, 240, 205))
        self.logo(cv, W / 2 - 170, 70, 340, 96)
        info, lang = self.ep_info, self.app.lang
        name = info["song"].replace("_", " ").strip().capitalize()
        if lt >= 3 * STEP:                                    # the title comes in on a cut, not a fade
            self.caption(cv, tr(lang, "episode", n=info["ep"]), W / 2, 248, 46, "center", (255, 255, 255), (120, 60, 20))
            self.caption(cv, tr(lang, "season", s=name), W / 2, 292, 20, "center", (255, 255, 255), (120, 60, 20))
            self.caption(cv, tr(lang, "partOf", k=info["ep"], n=info["total"]), W / 2, 322, 16, "center",
                         (255, 255, 255), (120, 60, 20))

    # ---- the ending: credits, the scene after them, next time
    def start_ending(self, sm):
        """When the lesson's summary arrives: the credits with its numbers, then a short scene, then next time.
        Returns its length in seconds."""
        self.ending = {"sm": dict(sm or {})}
        learned, total = int(sm.get("learned", 0) or 0), int(sm.get("total", 0) or 0)
        shots = [{"kind": "credits", "dur": 12.0}, {"kind": "tag", "dur": 3.0}]   # slow enough to read
        if total:
            shots.append({"kind": "next", "dur": 3.0})
        score = sm.get("score")
        good = sm.get("complete") or (score is not None and score >= 85)
        low = score is not None and score < 60
        shots[1]["mood"] = "hi" if good else "lo" if low else "mid"
        shots[1]["motif"] = ((None if self.drawn() else "happy") if good   # drawn: the motif lands on the reaction
                             else (None if self.pics.path("g08_oops_peak") else "oops") if low else "think")
        self.ending.update(learned=learned, total=total, complete=bool(sm.get("complete")))
        self.play(shots, then=None)
        return sum(s["dur"] for s in shots)

    def credit_lines(self):
        """The lesson in numbers, each with a line that says what it means: (label, value, explanation)."""
        sm, lang = self.ending["sm"], self.app.lang
        rows = []
        learned, total = int(sm.get("learned", 0) or 0), int(sm.get("total", 0) or 0)
        new = int(sm.get("new", 0) or 0)
        if total:
            rows.append((tr(lang, "cKnown"), tr(lang, "cKnownV", k=learned, n=total), tr(lang, "cKnownH")))
            if new and learned:
                a, b = learned - new + 1, learned
                today = tr(lang, "cTodayOne", k=b) if a >= b else tr(lang, "cTodayMany", a=a, b=b)
                rows.append((tr(lang, "cToday"), today, tr(lang, "cTodayH")))
            else:
                rows.append((tr(lang, "cToday"), tr(lang, "cTodayReview"), ""))
        if sm.get("score") is not None:
            rows.append((tr(lang, "cScore"), f"{sm['score']}%", tr(lang, "cScoreH")))
        secs = int(sm.get("secs", 0) or 0)
        if secs:
            rows.append((tr(lang, "cTime"), f"{secs // 60}:{secs % 60:02d}", ""))
        miss = sum((sm.get("mistakes") or {}).values() or [0]) if isinstance(sm.get("mistakes"), dict) else 0
        rows.append((tr(lang, "cMiss"), str(miss), tr(lang, "cMissH")))
        if sm.get("fixed"):
            rows.append((tr(lang, "cFixed"), ", ".join(str(x) for x in sm["fixed"]), tr(lang, "cFixedH")))
        if sm.get("warmup"):
            rows.append((tr(lang, "cWarm"), str(sm["warmup"]), tr(lang, "cWarmH")))
        if sm.get("band"):
            rows.append((tr(lang, "cDuet"), tr(lang, "cDuetV"), ""))
        return rows

    def shot_credits(self, cv, t, lt, u, shot):
        """The credits: the song's name, then each number in the middle of the screen with what it means under it,
        rolling up slowly over the rooftops."""
        alt, morning = sun_now(self.app)
        self.roofs(cv, alt, morning, fx=pan(1 - u), zoom=1.15)
        with cv.physical():
            cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (10, 8, 20, 150))
        lang, sm = self.app.lang, self.ending["sm"]
        name = str(sm.get("song", "")).replace("_", " ").strip().capitalize()
        rows = self.credit_lines()
        head, pitch = 150, 96
        total = head + len(rows) * pitch
        y0 = H + 10 - (H + total) * (lt / shot["dur"])
        cx = W / 2
        gold, white, soft = (255, 214, 150), (255, 255, 255), (226, 222, 240)
        if -60 < y0 < H + 60:
            self.caption(cv, tr(lang, "cEnd"), cx, y0, 16, "center", gold)
        if -60 < y0 + 52 < H + 60:
            self.caption(cv, name, cx, y0 + 52, 36, "center", white)
        with cv.physical():
            if -10 < y0 + 84 < H + 10:
                cv.rect(cx - 60, y0 + 84, 120, 2, gold + (200,))
        for i, (k, v, hint) in enumerate(rows):
            y = y0 + head + i * pitch
            if -80 < y < H + 80:
                self.caption(cv, k, cx, y, 15, "center", gold)
                self.caption(cv, v, cx, y + 32, 26, "center", white)
                if hint:
                    self.caption(cv, hint, cx, y + 56, 13, "center", soft)

    def girl_cover(self, cv, when, zoom, fx, fy, prefix="g08_"):
        """Pose 8 drawn frame by frame over the whole screen, each drawing blended into the next (Girl.frame).
        Returns blit_cover's box."""
        sw, sh = cv.surf.get_size()
        tint = None if when == "day" else when
        p0, p1, a = self.girl.frame(time.monotonic())
        box = blit_cover(cv, self.pics.cover(prefix + (p0 if a < 1.0 else p1), sw, sh, zoom, tint=tint), fx, fy)
        if a < 1.0 and p0 != p1:
            blit_cover(cv, self.pics.cover(prefix + p1, sw, sh, zoom, tint=tint), fx, fy, a)
        return box

    def live_room(self, cv, scene):
        """In a lesson in pose 8, the girl is alive over the kept room: her looks and blinks, and her reactions,
        drawn frame by frame and blended (the room under her was one still picture). Drawn only while she is not on
        her plain look, so most frames add nothing; the veil of the room goes over her as over it."""
        if scene not in ("lesson", "summary") or self.room_pose() != 8 or not self.drawn():
            return
        p0, p1, a = self.girl.frame(time.monotonic())
        if p1 == "base" and (a >= 1.0 or p0 == "base"):
            return
        when = period(*sun_now(self.app))
        self.girl_cover(cv, when, 1.0, 0.6, 0.35)
        veil = (8, 10, 24, 96 if self.duet() else 132) if when != "day" else (255, 248, 240, 70 if self.duet() else 104)
        with cv.physical():
            cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, veil)

    def shot_tag(self, cv, t, lt, u, shot):
        mood = shot.get("mood", "mid")
        pose = {"hi": 10, "mid": 8, "lo": 11}[mood]
        sw, sh = cv.surf.get_size()
        when = self._when()
        if mood == "mid" and self.pics.path("g08_think"):
            if not shot.get("started"):
                shot["started"] = True
                self.girl.think(time.monotonic(), hold=shot["dur"])
            self.girl_cover(cv, when, 1.12, 0.7, 0.2 + 0.15 * u)
            return
        if mood == "lo" and self.pics.path("g08_oops_peak"):
            if not shot.get("started"):
                shot["started"] = True
                self.girl.oops(time.monotonic())
                self.sound("oops", OOPS_PEAK)
            self.girl_cover(cv, when, 1.12, 0.7, 0.2 + 0.15 * u)
            return
        if mood == "hi" and self.drawn():
            # the laugh drawn frame by frame, its sparkles drawn in the frames: nothing laid over her
            if not shot.get("started"):
                shot["started"] = True
                self.girl.happy(time.monotonic())
                self.sound("happy", HAPPY_PEAK)
            self.girl_cover(cv, when, 1.12, 0.7, 0.2 + 0.15 * u)
            return
        img = self.pics.cover(f"room_{pose:02d}", sw, sh, 1.12, tint=None if when == "day" else when)
        x0, y0, iw, ih = blit_cover(cv, img, 0.7, 0.2 + 0.15 * u)
        lang = self.app.lang
        manpu = {"hi": "sparkle", "mid": "bubble", "lo": "drop"}[mood]
        # the face, in plan units: the symbol goes beside the head, never over it
        fx, fy = HEAD[pose]
        hx, hy = (x0 + fx * iw - cv.ox) / cv.scale, (y0 + fy * ih - cv.oy) / cv.scale
        if lt >= 0.4:
            with cv.physical():
                if manpu == "bubble":
                    bx, by = hx - 330, max(16, hy - 120)
                    m = self.pics.sized_layer("manpu_bubble", 250 * cv.scale, 200 * cv.scale)
                    px, py = cv.P(bx, by)
                    if m is not None:
                        cv.surf.blit(m, (int(px), int(py)))
                    line = tr(lang, {"hi": "thinkHi", "mid": "thinkMid", "lo": "thinkLo"}[mood])
                    cv.text(cv.fit(line, 180, 17, True), bx + 125, by + 104, 17, (40, 30, 52), True, "center")
                else:
                    size = 80 if lt >= 0.4 + 2 * STEP else 64
                    m = self.pics.sized_layer("manpu_" + manpu, size * cv.scale, size * cv.scale)
                    px, py = cv.P(hx + 70 if manpu == "drop" else hx - 150, hy - 95)
                    if m is not None:
                        cv.surf.blit(m, (int(px), int(py)))

    def shot_next(self, cv, t, lt, u, shot):
        e, lang = self.ending, self.app.lang
        sw, sh = cv.surf.get_size()
        alt, morning = sun_now(self.app)
        self.roofs(cv, alt, morning, fx=pan(u), zoom=1.1)
        with cv.physical():
            cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (10, 8, 20, 90))
        name = str(e["sm"].get("song", "")).replace("_", " ").strip().capitalize()
        if e["complete"] or e["learned"] >= e["total"]:
            self.caption(cv, tr(lang, "end"), W / 2, 220, 40, "center")
            self.caption(cv, tr(lang, "endSub", s=name), W / 2, 262, 18, "center")
            return
        self.caption(cv, tr(lang, "next") + "…", 40, 92, 34)
        if lt >= 3 * STEP:
            self.caption(cv, tr(lang, "nextEp", n=e["learned"] + 1, t=e["total"]), 42, 130, 20)

