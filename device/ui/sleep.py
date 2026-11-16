"""sleep.py - the screen while it sleeps on the rest view: the approved demo's option C.

Black, and everything on it dim (about a quarter of full white at most), so nothing lights the room:
a medium clock, under it one line with the next alarm (only when the device's own alarm rings within
24 hours) and the weather now in the chosen city, the Moon in its real phase in a corner, a few
stars twinkling slowly, and the chosen character asleep on a dark hill, breathing, small z letters
drifting up from it. In Hebrew the clock and the line are on the right and the figure on the left.

Low load on the Pi 4: the screen is drawn twice a second (App.run). What stays the same for a minute
(the black, the faint stars, the Moon, the hill, the clock and the line) is drawn once into a layer,
made again only when the minute, the Moon, the weather, the alarm, the language or the screen
changes. The figure is drawn once in 5 breathing poses, kept as small pictures. Each frame copies
the layer, lights the twinkling stars (a few small fills), copies one pose and writes 3 cached z's.
The whole picture drifts by a few pixels over minutes against image retention (App.follow_shift).

The weather comes from Open-Meteo's forecast API (free, no key): current=temperature_2m,weather_code
for the city's latitude and longitude, asked in a background thread at most every 30 minutes and
only online, kept in memory and in DATA_DIR/device/weather.json. Without an answer younger than 3
hours the weather part is left out, with no error on the screen.
"""
import json
import math
import os
import random
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from .canvas import W
from . import sky as sky_mod
from .characters import LOW

DIM = 0.26                      # the brightest thing on the sleep screen, as a part of full white
CLOCK = (70, 228, 84)           # x of its start, baseline, size (plan units, left to right; Hebrew mirrors)
LINE = (72, 272, 20)            # the alarm and weather line under it
MOON = (117, 73, 21)            # center and radius
FIGURE = (W - 185, 0.9)         # the figure's x and size; it stands on the hill
HILL = (W / 2, 550, 500, 200)   # center, half width (at least), half height of the hill's ellipse
Z_EVERY = 6.0                   # seconds a z takes to drift up and fade
BREATH = 5.0                    # seconds of one breath
POSES = 12                      # breathing poses drawn ahead (steps of 0.3% in size: a smooth breath)
STARS, TWINKLE = 14, 8          # still faint stars, and the ones that twinkle (per 800 plan units across)

WEATHER_EVERY = 1800            # seconds between two questions to Open-Meteo, at most
WEATHER_KEEP = 3 * 3600         # an answer older than this is not shown

# WMO weather interpretation codes, as Open-Meteo gives them in weather_code, to a few short words
WMO = {0: "clear", 1: "clear", 2: "partly", 3: "cloudy", 45: "fog", 48: "fog",
       51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
       61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
       71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
       80: "showers", 81: "showers", 82: "showers", 95: "storm", 96: "storm", 99: "storm"}


def condition(code):
    """The texts.py key of a WMO code ("w.rain"), or "" for a code not known."""
    try:
        k = WMO.get(int(code))
    except (TypeError, ValueError):
        k = None
    return "w." + k if k else ""


def dim(c, k=DIM):
    return tuple(int(round(v * k)) for v in c[:3])


# ---------------------------------------------------------------- the Moon's phase, once an hour

_MOON = [None, None]


def moon_phase(ms):
    """The Moon's phase angle (0 new, 90 first quarter, 180 full, 270 last quarter) from
    astronomy-engine, worked out once per hour. None when it cannot be."""
    hour = int(ms // 3600000)
    if _MOON[0] != hour:
        try:
            A = sky_mod._astro()
            _MOON[:] = [hour, A.MoonPhase(sky_mod._time(A, hour * 3600000 + 1800000))]
        except Exception:
            _MOON[:] = [hour, None]
    return _MOON[1]


# ---------------------------------------------------------------- the weather

def _fetch(url, timeout):
    return sky_mod._fetch(url, timeout)


class Weather:
    URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
           "&current=temperature_2m,weather_code&timezone=auto")

    def __init__(self, data_dir=None, fetch=None):
        self.dir = Path(data_dir) if data_dir else None
        self.fetch = fetch or _fetch
        self.data = None                 # {"lat", "lon", "temp", "code", "at"}
        self._thread, self._asked = None, (None, 0.0)
        try:
            d = json.loads((self.dir / "weather.json").read_text(encoding="utf-8")) if self.dir else None
            if d and {"lat", "lon", "temp", "code", "at"} <= set(d):
                self.data = d
        except Exception:
            pass

    @staticmethod
    def _place(city):
        return round(float(city["lat"]), 2), round(float(city["lon"]), 2)

    def start(self, city, online):
        """Ask for the weather now in a thread, unless it was asked for this place in the last 30
        minutes (an answer or not) or there is no network."""
        if not online or (self._thread and self._thread.is_alive()):
            return
        try:
            place = self._place(city)
        except Exception:
            return
        if self._asked[0] == place and time.time() - self._asked[1] < WEATHER_EVERY:
            return
        if self.data and (self.data["lat"], self.data["lon"]) == place and \
                time.time() - self.data["at"] < WEATHER_EVERY:
            return                       # the answer kept on disk is new enough
        self._asked = (place, time.time())
        self._thread = threading.Thread(target=self._run, args=(place,), daemon=True)
        self._thread.start()

    def wait(self, timeout=5):
        if self._thread:
            self._thread.join(timeout)

    def _run(self, place):
        try:
            j = json.loads(self.fetch(self.URL.format(lat=place[0], lon=place[1]), 6))
            cur = j["current"]
            d = {"lat": place[0], "lon": place[1], "temp": float(cur["temperature_2m"]),
                 "code": int(cur["weather_code"]), "at": time.time()}
        except Exception:
            return                       # offline or a bad answer: the last one stays, or nothing
        self.data = d
        if self.dir:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                tmp = self.dir / "weather.part"
                tmp.write_text(json.dumps(d), encoding="utf-8")
                os.replace(tmp, self.dir / "weather.json")
            except OSError:
                pass

    def now(self, city):
        """(temperature rounded, texts key of the condition) for the city, or None."""
        d = self.data
        try:
            if not d or (d["lat"], d["lon"]) != self._place(city) or time.time() - d["at"] > WEATHER_KEEP:
                return None
        except Exception:
            return None
        key = condition(d["code"])
        return (int(round(d["temp"])), key) if key else None


# ---------------------------------------------------------------- drawing

@contextmanager
def _into(cv, surf, x0, y0):
    """Draw on a small picture whose top left corner is the screen pixel (x0, y0)."""
    old = cv.surf, cv.ox, cv.oy, cv.rec
    cv.surf, cv.ox, cv.oy, cv.rec = surf, cv.ox - x0, cv.oy - y0, None
    try:
        yield
    finally:
        cv.surf, cv.ox, cv.oy, cv.rec = old


def _alarm_icon(cv, x, y, r, col):
    """A small alarm clock: a ring, two hands and two bells on top (the fonts have no ⏰)."""
    cv.circle(x, y, r, col, width=max(1.4, r * 0.24))
    cv.line(x, y, x, y - r * 0.55, col, max(1.2, r * 0.2))
    cv.line(x, y, x + r * 0.45, y, col, max(1.2, r * 0.2))
    for s in (-1, 1):
        cv.circle(x + s * r * 0.82, y - r * 0.92, r * 0.36, col)


class SleepScreen:
    def __init__(self):
        self.layers = {}                  # id(canvas) -> the still layer and what it was drawn for
        self.poses = {}                   # (id(canvas), size, lang, kind) -> breathing poses
        self.weather = None
        self.boxes = []                   # what the last layer holds, in plan units (name, x0, y0, x1, y1), for tests

    # ---- what the line says
    def next_alarm(self, app):
        """The alarm's time ("07:00") when the device's own alarm rings within 24 hours, else ""."""
        try:
            a = app.alarm()
            if not a["on"] or not a["song"] or not app.alarm_rings_here(a):
                return ""
            from .views_extra import ALARM_DAYS
            local = app.local()
            h, m = (int(x) for x in a["time"].split(":"))
            today = local.replace(hour=h, minute=m, second=0, microsecond=0)
            for add in (0, 1):
                when = today + timedelta(days=add)
                if local < when <= local + timedelta(hours=24) and when.weekday() in ALARM_DAYS[a["days"]]:
                    return a["time"]
        except Exception:
            pass
        return ""

    def weather_now(self, app):
        if self.weather is None:
            self.weather = Weather(Path(os.environ.get("DATA_DIR", "/var/lib/armonico")) / "device")
        if getattr(app, "background_on", True):
            self.weather.start(app.city, app.online())
        return self.weather.now(app.city)

    # ---- the frame
    def draw(self, app):
        cv = app.cv
        sw, sh = cv.surf.get_size()
        ms = app.now_ms()
        phase = moon_phase(ms)
        alarm, wx = self.next_alarm(app), self.weather_now(app)
        key = (sw, sh, cv.scale, app.lang, app.hm(ms), None if phase is None else round(phase / 3),
               alarm, wx, app.city.get("lat", 0) < 0)
        L = self.layers.get(id(cv))
        if L is None or L["key"] != key:
            surf = L["surf"] if L is not None and L["surf"].get_size() == (sw, sh) else app.pg.Surface((sw, sh), 0, cv.surf)
            L = self.layers[id(cv)] = {"key": key, "surf": surf}
            self._layer(app, L, ms, phase, alarm, wx)
        cv.surf.blit(L["surf"], (0, 0))
        t = time.monotonic()
        for rect, base, p in L["twinkle"]:
            v = base * (0.55 + 0.45 * math.sin(t * 2 * math.pi / 7 + p))
            cv.surf.fill((int(v), int(v), int(min(255, v * 1.1))), rect)
        self._figure(app, L, t)

    def _layer(self, app, L, ms, phase, alarm, wx):
        """The still layer: black, the faint stars, the Moon, the hill, the clock and its line."""
        cv, he = app.cv, app.lang == "he"
        surf = L["surf"]
        old, cv.surf, rec = cv.surf, surf, cv.rec
        cv.rec = None
        boxes = []
        try:
            surf.fill((0, 0, 0))
            cx, cy, rx, ry = HILL
            rx = max(rx, cv.vw / 2 + 120)
            with cv.physical():
                cv.ellipse(cx, cy, rx, ry, (9, 13, 24))
            # the clock, and the line under it
            x, y, size = CLOCK
            clock = app.hm(ms)
            w = cv.measure(clock, size)
            cv.text(clock, x, y, size, dim((255, 255, 255)))
            boxes.append(("clock", x, y - size * 0.75, x + w, y + size * 0.1))
            boxes += self._line(app, alarm, wx, he)
            # the Moon, its lit side as it is (south up in the north, north up in the south)
            mx, my, mr = MOON
            if phase is not None:
                with cv.physical():
                    sky_mod.moon_draw(cv, W - mx if he else mx, my, mr, phase, -90, DIM, app.city.get("lat", 0) < 0)
            boxes.append(("moon", mx - mr - 8, my - mr - 8, mx + mr + 8, my + mr + 8))
            fx, s = FIGURE
            top = self.ground(fx) - (LOW.get(app.kind, 50) + 95) * s
            boxes.append(("figure", fx - 90 * s, top - 40, fx + 90 * s, self.ground(fx)))
            # stars: still faint ones and twinkling ones, never over the clock, the line, the Moon or the figure
            L["twinkle"] = self._stars(cv, boxes)
        finally:
            cv.surf, cv.rec = old, rec
        self.boxes = boxes

    def _line(self, app, alarm, wx, he):
        """⏰ 07:00 · 18° clear, from the line's start (the right end in Hebrew): each piece apart,
        so the numbers keep their order in Hebrew."""
        cv = app.cv
        x0, y, size = LINE
        pieces = []
        if alarm:
            pieces += [("icon", None), (alarm, dim((232, 182, 79)))]
        if wx:
            if pieces:
                pieces.append(("·", dim((150, 160, 180))))
            pieces += [(f"{wx[0]}°", dim((201, 208, 222))), (app.tr(wx[1]), dim((201, 208, 222)))]
        if not pieces:
            return []
        gap, ir = 8, size * 0.36
        x = x0
        with cv.physical():
            for s, col in pieces:
                w = ir * 2.3 if s == "icon" else cv.measure(s, size)
                left = W - x - w if he else x
                if s == "icon":
                    _alarm_icon(cv, left + w / 2, y - size * 0.33, ir, dim((232, 182, 79)))
                else:
                    cv.text(s, left, y, size, col)
                x += w + gap
        return [("line", x0, y - size * 0.8, x - gap, y + size * 0.25)]

    @staticmethod
    def ground(x):
        cx, cy, rx, ry = HILL
        u = min(1.0, abs(x - cx) / rx)
        return cy - ry * math.sqrt(1 - u * u)

    def _stars(self, cv, boxes):
        """The still stars go into the layer; the twinkling ones come back as (rect, value, phase)."""
        rnd = random.Random(29)
        n = max(1, round(cv.vw / W * (STARS + TWINKLE)))
        k = max(1, round(cv.scale * 1.6))
        out, placed, tries = [], 0, 0
        he = cv.rtl
        while placed < n and tries < n * 30:
            tries += 1
            x, y = cv.vx + rnd.random() * cv.vw, 12 + rnd.random() * (self.ground(W / 2) - 60)
            lx = W - x if he else x                      # in the left to right plan, as the boxes are
            if any(x0 - 6 <= lx <= x1 + 6 and y0 - 6 <= y <= y1 + 6 for _n, x0, y0, x1, y1 in boxes):
                continue
            if y > self.ground(x) - 12:
                continue
            with cv.physical():
                px, py = cv.P(x, y)
            rect = cv.pg.Rect(int(px), int(py), k, k)
            v = 30 + rnd.random() * 34                   # faint: 30 to 64 of 255
            if placed % (STARS + TWINKLE) < TWINKLE:
                out.append((rect, v, rnd.random() * 6.3))
            else:
                cv.surf.fill((int(v), int(v), int(min(255, v * 1.1))), rect)
            placed += 1
        return out

    # ---- the figure and its z's
    def _poses(self, app):
        cv = app.cv
        fx, s = FIGURE
        kind, he = app.kind, app.lang == "he"
        key = (id(cv), cv.surf.get_size(), cv.scale, he, kind)
        P = self.poses.get(key)
        if P is not None:
            return P
        if len(self.poses) > 4:
            self.poses.clear()
        low = LOW.get(kind, 50) - 2
        ground = self.ground(fx)
        x = W - fx if he else fx
        y = ground - low * s                              # the figure's center: its feet on the hill
        with cv.physical():
            (x0, y0), (x1, y1) = cv.P(x - 95 * s, y - 120 * s), cv.P(x + 95 * s, ground + 6)
        x0, y0 = int(x0), int(y0)
        size = (max(1, int(x1) - x0), max(1, int(y1) - y0))
        frames = []
        for i in range(POSES):
            pic = app.pg.Surface(size, 0, cv.surf)
            pic.fill((0, 0, 0))
            with _into(cv, pic, x0, y0):
                app.char.draw_sleeping(cv, kind, x, y, s, 1 + 0.03 * i / (POSES - 1))
            pic.fill(dim((255, 255, 255)), special_flags=app.pg.BLEND_RGB_MULT)
            pic.set_colorkey((0, 0, 0))
            frames.append(pic)
        P = self.poses[key] = {"at": (x0, y0), "frames": frames, "head": (fx, y - 70 * s)}
        return P

    def _figure(self, app, L, t):
        cv = app.cv
        P = self._poses(app)
        b = (1 - math.cos(2 * math.pi * t / BREATH)) / 2
        cv.surf.blit(P["frames"][int(round(b * (POSES - 1)))], P["at"])
        hx, hy = P["head"]
        col = dim((143, 205, 246), 0.3)
        for i in range(3):
            p = (t / Z_EVERY + i / 3) % 1
            a = p / 0.25 * 0.6 if p < 0.25 else 0.6 * (1 - p) / 0.75   # in at a quarter, out at the top
            a = round(a / 0.6 * 5) / 5                    # 6 levels: the canvas keeps each one rendered
            if a <= 0:
                continue
            cv.text("z", hx - 70 + p * 26, hy - 10 - p * 46, 16 + 2 * i,
                    col + (int(255 * a),) if a < 1 else col, True, "center")
