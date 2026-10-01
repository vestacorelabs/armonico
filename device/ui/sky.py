"""sky.py - the real sky over the chosen city, and the classic night look, for the device screens.

The sky theme draws what is up there right now: the color of the sky by the Sun's altitude through
the twilight bands, the stars that are bright enough to be seen, the planets with their names, the
Moon with its real lit side, and meteors from a shower when one is active. The positions come from
astronomy-engine, worked out once a minute per city (the events once an hour: eclipse searches are
slow on a Pi). The 519 stars are turned into altitude and azimuth with one sidereal time and plain
trigonometry, which costs a few milliseconds.

The view looks at the horizon in the direction the Sun goes through at noon: south in the northern
hemisphere, north in the southern. Azimuth runs across the screen around that direction, altitude
0..62 degrees runs from the horizon line (SKYH) up to the top.

Also here, for the screens that show them: the color palettes of every theme, the events of the
coming week as text in English and Hebrew, the Hebrew date (pyluach offline, Hebcal when online),
the city search (Open-Meteo, or the built-in cities offline) and NASA's picture of the day.
"""
import json
import math
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:                               # pragma: no cover - Python 3.9+ always has it
    ZoneInfo = None

W, H = 800, 480
SKYH = 372                                      # the horizon line, in plan units
DAY_MS = 86400000
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
J2000_UNIX = 946728000                          # 2000-01-01 12:00 UTC, astronomy-engine's day 0

# name, solar longitude at the peak, radiant RA and Dec (degrees), ZHR at the peak, days active either side
SHOWERS = [["Quadrantids", 283.15, 230, 49, 80, 4], ["Lyrids", 32.32, 271, 34, 18, 3],
           ["Eta Aquariids", 45.5, 338, -1, 50, 5], ["Perseids", 140.0, 48, 58, 100, 5],
           ["Draconids", 195.4, 262, 54, 5, 1], ["Orionids", 208, 95, 16, 20, 4],
           ["Leonids", 235.27, 152, 22, 15, 3], ["Geminids", 262.2, 112, 33, 150, 4],
           ["Ursids", 270.7, 217, 76, 10, 2]]
PLANETS = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn"]
PLANET_HE = {"Mercury": "כוכב חמה", "Venus": "נוגה", "Mars": "מאדים", "Jupiter": "צדק", "Saturn": "שבתאי"}
SHOWER_HE = {"Quadrantids": "הקואדרנטידים", "Lyrids": "הלירידים", "Eta Aquariids": "האקוורידים-אטא",
             "Perseids": "הפרסאידים", "Draconids": "הדרקונידים", "Orionids": "האוריונידים",
             "Leonids": "הליאונידים", "Geminids": "הג׳מינידים", "Ursids": "האורסידים"}
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]
MONTHS_HE = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
QUARTERS_EN = ["New moon", "First quarter", "Full moon", "Last quarter"]
QUARTERS_HE = ["ירח חדש", "רבע ראשון", "ירח מלא", "רבע אחרון"]
LUNAR_HE = {"penumbral": "ליקוי ירח חצי-צל", "partial": "ליקוי ירח חלקי", "total": "ליקוי ירח מלא"}
SOLAR_HE = {"partial": "ליקוי חמה חלקי", "annular": "ליקוי חמה טבעתי", "total": "ליקוי חמה מלא"}

# zenith and horizon color by the Sun's altitude: night, astronomical, nautical, civil twilight, day
SKY_KEYS = [(-90, (6, 9, 26), (14, 20, 48)), (-18, (8, 12, 34), (20, 26, 62)), (-12, (18, 26, 78), (52, 52, 112)),
            (-6, (44, 60, 140), (232, 140, 118)), (0, (70, 110, 196), (255, 186, 128)),
            (6, (64, 132, 220), (176, 214, 246)), (90, (52, 120, 214), (170, 210, 244))]


def _tz(name):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return timezone.utc


def local_dt(ms, tz):
    """A moment (ms since 1970) as a datetime in the city's time zone."""
    return datetime.fromtimestamp(ms / 1000, _tz(tz))


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def sky_colors(alt):
    """(zenith, horizon) colors for a Sun altitude in degrees."""
    for (a0, z0, h0), (a1, z1, h1) in zip(SKY_KEYS, SKY_KEYS[1:]):
        if alt <= a1:
            k = (alt - a0) / (a1 - a0)
            return mix(z0, z1, k), mix(h0, h1, k)
    return SKY_KEYS[-1][1], SKY_KEYS[-1][2]


# ---------------------------------------------------------------- astronomy

class Pos:
    """Where a body is: azimuth (0 north, 90 east) and altitude, in degrees."""
    __slots__ = ("azimuth", "altitude")

    def __init__(self, azimuth, altitude):
        self.azimuth, self.altitude = azimuth, altitude

    def __repr__(self):
        return f"Pos(az={self.azimuth:.1f}, alt={self.altitude:.1f})"


class SkyState:
    """One minute of sky over one city. Times are ms since 1970 (UTC).

    sun, moon   Pos
    phase       Moon phase angle, 0 new, 90 first quarter, 180 full, 270 last quarter
    illum       the lit fraction of the Moon, 0..1
    stars       [(azimuth, altitude, magnitude, name, index in the catalog)], above the horizon
    planets     [(name, azimuth, altitude)], above the horizon
    rise, set   the Sun's next rise and set after 20 hours ago (None near the poles)
    ev          [(ms, kind, data)] sorted: kind is moon, lunar, solar or meteor
    shower      the active meteor shower: dict(n, zhr, az, alt), or None
    vis         names of the planets more than 5 degrees up
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _astro():
    import astronomy
    return astronomy


def _time(A, ms):
    return A.Time((ms / 1000 - J2000_UNIX) / 86400)


def _ms(t):
    return (t.ut * 86400 + J2000_UNIX) * 1000


def _hz(A, body, t, obs):
    e = A.Equator(body, t, obs, True, True)
    h = A.Horizon(t, obs, e.ra, e.dec, A.Refraction.Normal)
    return Pos(h.azimuth, h.altitude)


def _altaz(lst_deg, lat, ra_deg, dec_deg):
    """Equatorial to horizontal with plain trigonometry: cheap enough for every star each minute."""
    ha = math.radians(lst_deg - ra_deg)
    phi, dec = math.radians(lat), math.radians(dec_deg)
    sa = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sa))))
    az = math.degrees(math.atan2(-math.cos(dec) * math.sin(ha),
                                 math.sin(dec) * math.cos(phi) - math.cos(dec) * math.sin(phi) * math.cos(ha)))
    return az % 360, alt


def _load_json(name, data_dir=None):
    for d in (Path(data_dir) if data_dir else None, DATA):
        if d is not None and (d / name).exists():
            with open(d / name, encoding="utf-8") as f:
                return json.load(f)
    return []


class Sky:
    """The sky of one city at one minute, and its drawing."""

    def __init__(self, data_dir=None, seed=None):
        self.stars = _load_json("stars.json", data_dir)          # [ra deg, dec deg, magnitude, name]
        rnd = random.Random(seed)
        self.twk = [rnd.random() * 6.3 for _ in self.stars]       # each star twinkles in its own phase
        self.meteors = []
        self.rnd = rnd
        self._key, self._st = None, None
        self._ev_key, self._ev = None, None
        self._last_t = None
        self.lock = threading.Lock()

    # ---- the computation
    def compute(self, ms, city):
        """The sky over a city dict (name, lat, lon, tz) at ms. Cached per minute per city."""
        lat, lon = float(city["lat"]), float(city["lon"])
        key = (int(ms // 60000), city.get("name"), lat, lon)
        with self.lock:
            if key == self._key:
                return self._st
        A = _astro()
        t = _time(A, ms)
        obs = A.Observer(lat, lon, 0)
        sun, moon = _hz(A, A.Body.Sun, t, obs), _hz(A, A.Body.Moon, t, obs)
        phase = A.MoonPhase(t)
        illum = A.Illumination(A.Body.Moon, t).phase_fraction
        lst = A.SiderealTime(t) * 15 + lon
        stars = []
        for i, s in enumerate(self.stars):
            az, alt = _altaz(lst, lat, s[0] % 360, s[1])
            if alt > 0:
                stars.append((az, alt, s[2], s[3], i))
        planets = []
        for n in PLANETS:
            p = _hz(A, getattr(A.Body, n), t, obs)
            if p.altitude > 0:
                planets.append((n, p.azimuth, p.altitude))
        t20 = t.AddDays(-20 / 24)
        rise = A.SearchRiseSet(A.Body.Sun, obs, A.Direction.Rise, t20, 1.5)
        sset = A.SearchRiseSet(A.Body.Sun, obs, A.Direction.Set, t20, 1.5)
        ev, showers = self._events(A, ms, t, obs, key)
        shower = None
        for n, zhr, width, dd, ra, dec in showers:
            if abs(dd) <= width:
                az, alt = _altaz(lst, lat, ra, dec)
                if not shower or zhr > shower["zhr"]:
                    shower = {"n": n, "zhr": zhr * max(0.0, 1 - abs(dd) / width), "az": az, "alt": alt}
        st = SkyState(ms=ms, lat=lat, lon=lon, tz=city.get("tz") or "UTC", sun=sun, moon=moon, phase=phase,
                      illum=illum, stars=stars, planets=planets,
                      rise=_ms(rise) if rise else None, set=_ms(sset) if sset else None,
                      ev=ev, shower=shower, vis=[p[0] for p in planets if p[2] > 5])
        with self.lock:
            self._key, self._st = key, st
        return st

    def _events(self, A, ms, t, obs, key):
        """The moon quarter, eclipses and shower peaks. They change slowly and the eclipse
        searches are the slowest part, so they are kept for an hour per city."""
        ekey = (int(ms // 3600000),) + key[1:]
        if ekey == self._ev_key:
            ev, showers = self._ev
        else:
            ev, showers = [], []
            q = A.SearchMoonQuarter(t)
            ev.append((_ms(q.time), "moon", {"q": q.quarter}))
            le = A.SearchLunarEclipse(t)
            if _ms(le.peak) - ms < 200 * DAY_MS:
                up = _hz(A, A.Body.Moon, le.peak, obs).altitude > 0
                ev.append((_ms(le.peak), "lunar", {"kind": le.kind.name.lower(), "up": up}))
            se = A.SearchLocalSolarEclipse(t, obs)
            if _ms(se.peak.time) - ms < 400 * DAY_MS:
                ev.append((_ms(se.peak.time), "solar", {"kind": se.kind.name.lower(), "p": round(se.obscuration * 100)}))
            start = t.AddDays(-20)
            for n, lam, ra, dec, zhr, width in SHOWERS:
                pk = A.SearchSunLongitude(lam, start, 40)
                if not pk:
                    continue
                pms = _ms(pk)
                dd = (pms - ms) / DAY_MS
                if -1 < dd < 25:
                    ev.append((pms, "meteor", {"n": n, "zhr": zhr}))
                showers.append((n, zhr, width, pms, ra, dec))
            ev.sort(key=lambda e: e[0])
            self._ev_key, self._ev = ekey, (ev, showers)
        # the distance to each peak moves with the minute, the peaks themselves do not
        return list(ev), [(n, zhr, w, (pms - ms) / DAY_MS, ra, dec) for n, zhr, w, pms, ra, dec in showers]

    # ---- drawing
    def draw(self, cv, st, t, faint=1.0, face=None, lang=None, live=None):
        """The whole sky behind a screen. faint dims the stars, planets, Sun and Moon (1 on the
        rest screen, 0.22 behind the other screens). Returns the (zenith, horizon) colors.
        live: a list, when the sky goes into a still layer (the rest screen, drawn again only when
        something in it changes): the stars and meteors are left out of it, the stars go into the
        list, and draw_live puts them on every frame, twinkling, over the layer."""
        he = cv.rtl if lang is None else lang == "he"
        self.live_face = face
        with cv.physical():
            return self._draw(cv, st, t, faint, face, he, live)

    def draw_live(self, cv, st, t, stars, faint=1.0, rects=()):
        """The moving part of the sky over its still layer: the stars twinkling and the meteors.
        A star or meteor under something drawn on top of the sky (a hill, a line of text, the Moon)
        is not drawn, as before, when the stars came first and the rest covered them."""
        gfx = cv.pg.gfxdraw
        for px, py, r, b, i in stars:
            _dot(gfx, cv.surf, px, py, r, (255, 250, 238), b * (0.82 + 0.18 * math.sin(t * 2 + self.twk[i])) * faint)
        with cv.physical():
            self._meteors(cv, st, t, faint, self.live_face, rects)

    def proj(self, st, az, alt, face=None):
        f = face if face is not None else (180 if st.lat >= 0 else 0)
        a = ((az - f + 540) % 360) - 180
        return W / 2 + a / 180 * W, SKYH - alt / 62 * SKYH

    def _draw(self, cv, st, t, faint, face, he, live=None):
        import pygame.gfxdraw  # noqa: F401 - cv.pg.gfxdraw needs the submodule loaded
        pg = cv.pg
        sun = st.sun
        zen, hor = sky_colors(sun.altitude)
        _sky_gradient(cv, zen, hor)
        # glow on the horizon where the Sun is, in twilight
        if -14 < sun.altitude < 12:
            x, _ = self.proj(st, sun.azimuth, 0, face)
            k = 1 - abs(sun.altitude + 1) / 13
            _radial(cv, x, SKYH, 10, 360, (255, 170, 110), 0.55 * k, (255, 170, 110), 0.0)
        alt = sun.altitude
        lim = 4 if alt < -18 else 3 if alt < -12 else 2 if alt < -8 else 0.8 if alt < -4 else -9
        x0, x1 = cv.vx, cv.vx + cv.vw
        # the stars bright enough for this twilight, each twinkling a little
        gfx = pg.gfxdraw
        kpx = cv.k()
        surf = cv.surf
        for az, sa, mag, _name, i in st.stars:
            if mag > lim:
                continue
            x, y = self.proj(st, az, sa, face)
            if x < x0 or x > x1:
                continue
            b = max(0.15, min(1.0, (lim - mag + 0.6) / 3))
            r = max(0.7, 2.4 - mag * 0.45) * kpx
            px, py = cv.P(x, y)
            if live is not None:
                live.append((px, py, r, b, i))
                continue
            tw = 0.82 + 0.18 * math.sin(t * 2 + self.twk[i])
            a = b * tw * faint
            _dot(gfx, surf, px, py, r, (255, 250, 238), a)
        if lim > 0:
            for n, az, pa in st.planets:
                x, y = self.proj(st, az, pa, face)
                if x < x0 or x > x1:
                    continue
                px, py = cv.P(x, y)
                _dot(gfx, surf, px, py, 3 * kpx, (255, 246, 216), faint)
                if faint >= 1: cv.text(PLANET_HE[n] if he else n, x + 7, y + 4, 11, (255, 246, 216, int(255 * 0.75 * faint)))
        if live is None:
            self._meteors(cv, st, t, faint, face)
        self._lights(cv, st, sun, faint, face)
        return zen, hor

    def _meteors(self, cv, st, t, faint, face, rects=()):
        """Meteors from the radiant of the active shower, at its real rate (sped up 20x to be seen)."""
        lim_ok = st.sun.altitude < -8             # dark enough for the faint stars (lim >= 2 in _draw)
        dt = 1 / 60 if self._last_t is None else max(0.0, min(0.1, t - self._last_t))
        self._last_t = t
        sh = st.shower
        if sh and lim_ok and sh["alt"] > 0:
            rate = sh["zhr"] * math.sin(math.radians(sh["alt"])) / 3600 * 20
            if self.rnd.random() < rate * dt:
                rx, ry = self.proj(st, sh["az"], sh["alt"], face)
                ang, d0 = self.rnd.random() * 6.3, 40 + self.rnd.random() * 120
                self.meteors.append([rx + math.cos(ang) * d0, ry + math.sin(ang) * d0, math.cos(ang), math.sin(ang), 0.0])
        alive = []
        for m in self.meteors:
            m[4] += 1.8 * dt                      # 0.03 a frame at 60 frames a second
            if m[4] < 1:
                alive.append(m)
                mx_, my_, dx, dy, life = m
                hx, hy = cv.P(mx_ + dx * life * 160, my_ + dy * life * 160)
                if any(x0 <= hx <= x1 and y0 <= hy <= y1 for x0, y0, x1, y1 in rects):
                    continue                      # its head is behind a hill or a line of text
                _shape(cv, _line_pts(mx_ + dx * (life * 160 - 40), my_ + dy * (life * 160 - 40), mx_ + dx * life * 160,
                                     my_ + dy * life * 160, 1.6), (255, 255, 255, int(255 * 0.9 * (1 - life) * faint)))
        self.meteors = alive

    def _lights(self, cv, st, sun, faint, face):
        """The Sun and the Moon, over the stars."""
        # the Sun
        if sun.altitude > -1:
            x, y = self.proj(st, sun.azimuth, sun.altitude, face)
            if -40 < x < W + 40:
                _radial(cv, x, y, 4, 70, (255, 244, 200), 0.95 * faint, (255, 220, 150), 0.0)
                _shape(cv, _circle_pts(x, y, 16), (255, 244, 200, int(255 * faint)))
        # the Moon, lit side and phase as they are
        if st.moon.altitude > -1:
            x, y = self.proj(st, st.moon.azimuth, st.moon.altitude, face)
            if -30 < x < W + 30:
                f = face if face is not None else (180 if st.lat >= 0 else 0)
                north = math.cos(math.radians(f)) > 0          # looking north: the lit side swaps
                moon_draw(cv, x, y, 15, st.phase, sun.altitude, faint, north)

    # the shared helpers, also reachable from a Sky
    hills = staticmethod(lambda cv, hor, strength: hills(cv, hor, strength))
    classic = staticmethod(lambda cv, t, dark: classic(cv, t, dark))
    palette = staticmethod(lambda theme, dark, sun_alt: palette(theme, dark, sun_alt))
    events_week = staticmethod(lambda st, ms: events_week(st, ms))
    event_text = staticmethod(lambda ev, lang, tz: event_text(ev, lang, tz))
    hebrew_date = staticmethod(lambda ms, tz, after_sunset, online=False: hebrew_date(ms, tz, after_sunset, online))


# ---------------------------------------------------------------- drawing helpers

_LAYERS = {}


BIG = ("grad", "classic")    # whole-screen pictures: 8 MB each at 1920x1080


def _cache(key, make):
    """Pictures kept between frames. The whole-screen ones are few: the sky's gradient changes color
    every few minutes, and keeping 40 of them filled some 300 MB of memory on the 24" screen within
    a day. Each kind keeps its last 2 (one per screen when both are lit)."""
    if key not in _LAYERS:
        if key[0] in BIG:
            same = [k for k in _LAYERS if k[0] == key[0]]
            for k in same[:-1]:                       # dicts keep their order: the oldest first
                del _LAYERS[k]
        elif len(_LAYERS) > 40:
            for k in [k for k in _LAYERS if k[0] not in BIG]:
                del _LAYERS[k]
        _LAYERS[key] = make()
    return _LAYERS[key]


def _shape(cv, pts, color):
    """A filled shape in plan units with true translucency: drawn opaque on a small layer, then
    blended once with the layer's alpha (gfxdraw on a transparent layer would square the alpha)."""
    pg = cv.pg
    a = color[3] if len(color) == 4 else 255
    if a <= 0 or len(pts) < 3:
        return
    sp = [cv.P(*p) for p in pts]
    if getattr(cv, "rec", None) is not None and sp:
        cv.rec.append((min(p[0] for p in sp) - 2, min(p[1] for p in sp) - 2, max(p[0] for p in sp) + 2, max(p[1] for p in sp) + 2))
    if a >= 255:
        cv._poly(sp, color[:3])
        return
    x0, y0 = int(min(p[0] for p in sp)) - 2, int(min(p[1] for p in sp)) - 2
    x1, y1 = int(max(p[0] for p in sp)) + 2, int(max(p[1] for p in sp)) + 2
    if x1 <= x0 or y1 <= y0:
        return
    lay = pg.Surface((x1 - x0, y1 - y0), pg.SRCALPHA)
    lay.fill(color[:3] + (0,))
    ip = [(int(round(x - x0)), int(round(y - y0))) for x, y in sp]
    pg.gfxdraw.filled_polygon(lay, ip, color[:3])
    pg.gfxdraw.aapolygon(lay, ip, color[:3])
    lay.set_alpha(int(a))
    cv.surf.blit(lay, (x0, y0))


def _circle_pts(cx, cy, rx, ry=None, n=40):
    ry = rx if ry is None else ry
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _line_pts(x1, y1, x2, y2, width):
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1e-6
    nx, ny = -dy / L * width / 2, dx / L * width / 2
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


def _dot(gfx, surf, px, py, r, rgb, a):
    """A small round light straight on the screen, blended by gfxdraw: no layer per star."""
    a = max(0.0, min(1.0, a))
    if a <= 0:
        return
    if r < 1.0:
        gfx.pixel(surf, int(px), int(py), rgb + (int(255 * a * max(0.35, r)),))
        return
    ri = int(round(r))
    col = rgb + (int(255 * a),)
    gfx.filled_circle(surf, int(round(px)), int(round(py)), ri, col)


_NOISE = {}


def _dither(np, w, h):
    """The same noise every time (0 to 1, one value a pixel), so the sky does not shimmer."""
    if (w, h) not in _NOISE:
        if len(_NOISE) >= 2:                  # both screens draw at two sizes: keep one noise for each
            _NOISE.clear()
        _NOISE[(w, h)] = np.random.default_rng(7).random((w, h), dtype=np.float32)
    return _NOISE[(w, h)]


def _sky_gradient(cv, zen, hor):
    """Zenith color above the plan, the gradient down to the horizon, horizon color below it.
    Kept as one image per color pair: the colors change once a minute at most."""
    pg = cv.pg
    sw, sh = cv.surf.get_size()

    def make():
        y0 = cv.oy
        y1 = cv.oy + SKYH * cv.scale
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None:
            # A dark sky has only some 30 levels from top to bottom. Plain rows show them as bands,
            # and as the red, green and blue levels step on different rows, as colored stripes on a
            # big screen. A fixed fine noise under each row spreads the steps out (dithering).
            k = np.clip((np.arange(sh, dtype=np.float32) - y0) / max(1.0, y1 - y0), 0.0, 1.0)
            z = np.array(zen[:3], np.float32)
            rows = z[None, :] + (np.array(hor[:3], np.float32) - z)[None, :] * k[:, None]      # sh x 3
            arr = np.floor(rows[None, :, :] + _dither(np, sw, sh)[:, :, None])
            return pg.surfarray.make_surface(np.clip(arr, 0, 255).astype(np.uint8))
        col = pg.Surface((1, sh))
        for py in range(sh):
            k = (py - y0) / max(1.0, y1 - y0)
            col.set_at((0, py), mix(zen, hor, k))
        return pg.transform.scale(col, (sw, sh))

    cv.surf.blit(_cache(("grad", zen, hor, sw, sh, cv.oy, cv.scale), make), (0, 0))


def _radial(cv, cx, cy, r0, r1, c0, a0, c1, a1):
    """A radial gradient like a browser's createRadialGradient(cx, cy, r0, cx, cy, r1):
    color c0 at r0 (and inside it) to c1 at r1. Drawn small once, scaled, then blitted."""
    pg = cv.pg
    R = max(1, int(round(r1 * cv.k())))
    top = max(a0, a1)
    if top <= 0.003:
        return

    def make():
        n = 96
        s = pg.Surface((2 * n, 2 * n), pg.SRCALPHA)
        for i in range(n, 0, -1):
            rad = r1 * i / n
            k = (rad - r0) / max(1e-6, r1 - r0)
            alpha = (a0 + (a1 - a0) * max(0.0, min(1.0, k))) / top
            s_col = mix(c0, c1, k)
            pg.draw.circle(s, s_col + (int(255 * alpha),), (n, n), i)
        return pg.transform.smoothscale(s, (2 * R, 2 * R))

    img = _cache(("rad", r0, r1, c0, c1, round(a0 / top, 3), round(a1 / top, 3), R), make)
    img.set_alpha(int(255 * min(1.0, top)))
    px, py = cv.P(cx, cy)
    cv.surf.blit(img, (int(px - R), int(py - R)))


def moon_draw(cv, x, y, r, phase, sun_alt, faint=1.0, north=False):
    """The Moon: a faint halo, the dark disc, and the lit part bounded by the terminator, an ellipse.
    Waxing, the right side is lit as seen facing south; facing north it is the left."""
    _shape(cv, _circle_pts(x, y, r + 7), (255, 250, 230, int(255 * 0.12 * faint)))
    _shape(cv, _circle_pts(x, y, r), (60, 64, 80, int(255 * (0.08 if sun_alt > -4 else 0.55) * faint)))
    waxing = phase < 180
    k = math.cos(math.radians(phase))
    rx = max(0.01, abs(k) * r)
    crescent = k > 0
    side = 1 if waxing else -1
    if north:
        side = -side
    term = side if crescent else -side
    n = 24
    pts = [(x + side * r * math.cos(v), y + r * math.sin(v)) for v in (-math.pi / 2 + math.pi * i / n for i in range(n + 1))]
    pts += [(x + term * rx * math.cos(v), y + r * math.sin(v)) for v in (math.pi / 2 - math.pi * i / n for i in range(n + 1))]
    _shape(cv, pts, (244, 240, 224, int(255 * faint)))


def hills(cv, hor, strength):
    """Two hills in front of the sky, colored from the horizon: a wavy one behind, a round one in front."""
    back, front = mix(hor, (18, 24, 52), 0.6), mix(hor, (10, 14, 34), 0.78)
    sw, sh = cv.surf.get_size()
    with cv.physical():
        vx, vy, vw, vh = cv.vx, cv.vy, cv.vw, cv.vh
        bpts = [(vx, vy + vh)]
        x = vx
        while x <= vx + vw + 20:
            bpts.append((x, SKYH - 24 + math.sin(x / 120 + 1) * 14))
            x += 20
        bpts.append((vx + vw, vy + vh))
        fpts = [(vx, vy + vh), (vx, H)]
        for i in range(1, 33):                       # the quadratic curve to the right edge
            u = i / 32
            cx_, cy_ = W / 2, SKYH - 60
            fpts.append(((1 - u) ** 2 * vx + 2 * (1 - u) * u * cx_ + u * u * (vx + vw),
                         (1 - u) ** 2 * H + 2 * (1 - u) * u * cy_ + u * u * H))
        fpts.append((vx + vw, vy + vh))
        a = int(255 * max(0.0, min(1.0, strength)))
        if a <= 0:
            return
        for pts, col in ((bpts, back), (fpts, front)):
            # a faint hill is the color with alpha: canvas draws it on a copy of what is under it and
            # blends that back with one alpha (a whole-screen layer with an alpha per pixel, twice a
            # frame, was most of the time of a frame on a 24" screen)
            cv.polygon(pts, col if a >= 255 else tuple(col[:3]) + (a,))


# ---------------------------------------------------------------- the classic look

_CSTARS = [(random.Random(7 + i).random(), random.Random(1007 + i).random(), random.Random(2007 + i).random() * 6.3)
           for i in range(300)]


def classic(cv, t, dark, live=None):
    """The classic background: a plain night (or cream) color, a wide hill and blinking stars
    spread over the whole screen, however wide it is. live: a list, when the background goes into a
    still layer: the stars go into it instead, and classic_live blinks them on every frame."""
    import pygame.gfxdraw  # noqa: F401
    pg = cv.pg
    sw, sh = cv.surf.get_size()

    def make():
        lay = pg.Surface((sw, sh))
        old, cv.surf = cv.surf, lay
        rec, cv.rec = getattr(cv, "rec", None), None     # the hill is background: nothing is hidden by it
        try:
            with cv.physical():
                lay.fill((13, 20, 36) if dark else (245, 241, 232))
                _shape(cv, _circle_pts(W / 2, H * 0.55 + H / 2, W / 2 + 200, H / 2, 120),
                       (34, 49, 79, 140) if dark else (226, 216, 196, 230))
        finally:
            cv.surf, cv.rec = old, rec
        return lay

    cv.surf.blit(_cache(("classic", dark, sw, sh, cv.scale), make), (0, 0))
    gfx = pg.gfxdraw
    # the demo has 110 stars over 1120 units across; the same density over the whole screen
    n = min(len(_CSTARS), int(round(110 * cv.vw / 1120 * (380 - cv.vy) / 440)))
    with cv.physical():
        for u, v, p in _CSTARS[:n]:
            x, y = cv.vx + u * cv.vw, cv.vy + v * (380 - cv.vy)
            (x0, y0), (x1, y1) = cv.P(x, y), cv.P(x + 2, y + 2)
            rect = pg.Rect(int(x0), int(y0), max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0))))
            if live is not None:
                live.append((rect.centerx, rect.centery, rect, p))
                continue
            _cstar(cv, gfx, rect, p, t, dark)


def _cstar(cv, gfx, rect, p, t, dark):
    if dark:
        val = int(round(150 + 80 * math.sin(t * 1.6 + p)))
        cv.surf.fill((val, val, min(255, val + 20)), rect)
    else:
        gfx.box(cv.surf, rect, (200, 160, 90, int(255 * (0.25 + 0.2 * math.sin(t * 1.6 + p)))))


def classic_live(cv, stars, t, dark):
    """The classic stars blinking over the still layer (classic(live=...))."""
    import pygame.gfxdraw  # noqa: F401
    gfx = cv.pg.gfxdraw
    for _x, _y, rect, p in stars:
        _cstar(cv, gfx, rect, p, t, dark)


def is_dark(theme, st):
    """Whether a theme is in its dark colors now: auto follows the real sunset and sunrise."""
    if theme == "classicLight":
        return False
    if theme == "auto":
        return st.sun.altitude < -0.833
    return True


def _rgba(r, g, b, a):
    return (r, g, b, int(round(255 * a)))


def _hex(s):
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


def palette(theme, dark, sun_alt):
    """The colors of the screens over each theme. Keys: ink, dim, panel, sel, gold, bar, key,
    black, onkey, lit, ok, line. bar and line are None where the theme has none."""
    if theme == "sky":
        day = sun_alt is not None and sun_alt > 2
        return {"ink": _hex("#14213d") if day else (255, 255, 255),
                "dim": _rgba(20, 33, 61, 0.8) if day else _rgba(255, 255, 255, 0.85),
                "panel": _rgba(255, 255, 255, 0.45) if day else _rgba(255, 255, 255, 0.14),
                "sel": _hex("#b9781a") if day else _hex("#ffd27a"),
                "gold": _hex("#9a5f00") if day else _hex("#ffd27a"),
                "bar": _rgba(255, 255, 255, 0.35) if day else _rgba(10, 14, 34, 0.3),
                "key": _rgba(255, 255, 255, 0.95), "black": _hex("#26304f"), "onkey": _hex("#3b4766"),
                "lit": _hex("#ffd27a"), "ok": _hex("#2a8a5a") if day else _hex("#a6f0c6"), "line": None}
    if dark:
        return {"ink": _hex("#e9edf5"), "dim": _hex("#a3adc0"), "panel": _hex("#161f33"), "sel": _hex("#e8b64f"),
                "gold": _hex("#e8b64f"), "bar": None, "key": _hex("#f3f4f7"), "black": _hex("#1c2233"),
                "onkey": _hex("#6a7387"), "lit": _hex("#8fcdf6"), "ok": _hex("#8ee3b8"), "line": None}
    return {"ink": _hex("#1d2433"), "dim": _hex("#565e6d"), "panel": _hex("#ffffff"), "sel": _hex("#c8902e"),
            "gold": _hex("#b67f1f"), "bar": None, "key": _hex("#ffffff"), "black": _hex("#2a3148"),
            "onkey": _hex("#6a7387"), "lit": _hex("#8fcdf6"), "ok": _hex("#2f9a68"), "line": _hex("#e4dccb")}


# ---------------------------------------------------------------- events as text

def events_week(st, ms):
    """Only what happens from a day ago to a week ahead: eclipses months away are left out."""
    return [e for e in st.ev if ms - DAY_MS < e[0] < ms + 7 * DAY_MS]


def after_sunset(st, ms):
    """True after today's sunset, when the Hebrew date has already moved to the next day."""
    if not st.set or ms <= st.set:
        return False
    return local_dt(st.set, st.tz).date() == local_dt(ms, st.tz).date()


def event_text(ev, lang, tz):
    """An event as (text, date), in English or Hebrew."""
    when, kind, o = ev
    he = lang == "he"
    d = local_dt(when, tz)
    date_text = f"{d.day} ב{MONTHS_HE[d.month - 1]}" if he else f"{d.day} {MONTHS_EN[d.month - 1]}"
    text = ""
    if kind == "moon":
        text = (QUARTERS_HE if he else QUARTERS_EN)[o["q"]]
    elif kind == "lunar":
        if he:
            text = f"{LUNAR_HE[o['kind']]}, {'נראה מכאן' if o['up'] else 'מתחת לאופק כאן'}"
        else:
            text = f"{o['kind'].capitalize()} lunar eclipse, {'visible here' if o['up'] else 'below the horizon here'}"
    elif kind == "solar":
        if he:
            text = f"{SOLAR_HE[o['kind']]}, {o['p']}% מהשמש מוסתרים"
        else:
            text = f"{o['kind'].capitalize()} solar eclipse, {o['p']}% of the Sun covered"
    elif kind == "meteor":
        if he:
            text = f"שיא מטר {SHOWER_HE[o['n']]}, עד {o['zhr']} מטאורים בשעה"
        else:
            text = f"{o['n']} at their peak, up to {o['zhr']} an hour"
    return text, date_text


# ---------------------------------------------------------------- the Hebrew date

_HEB = {}
_HEB_ASKED = set()


def _fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "Armonico-Pi"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def hebrew_date(ms, tz, after_sunset, online=False, fetch=None):
    """The Hebrew date in Hebrew letters, like "י״ג בחשון תשפ״ז". After sunset it is the next day's.
    Worked out offline with pyluach; when online, Hebcal's wording replaces it once a day, from a
    background thread, so drawing never waits for the network."""
    d = local_dt(ms, tz).date()
    key = (d.isoformat(), bool(after_sunset))
    if key not in _HEB:
        _HEB[key] = _pyluach_text(d + timedelta(days=1 if after_sunset else 0))
    if online and key not in _HEB_ASKED:
        _HEB_ASKED.add(key)
        url = (f"https://www.hebcal.com/converter?cfg=json&gy={d.year}&gm={d.month}&gd={d.day}&g2h=1"
               + ("&gs=on" if after_sunset else ""))

        def ask():
            try:
                p = json.loads((fetch or _fetch)(url, 5)).get("heDateParts")
                if p:
                    _HEB[key] = f"{p['d']} ב{p['m']} {p['y']}"
            except Exception:
                pass                                  # the offline date stays

        th = threading.Thread(target=ask, daemon=True)
        th.start()
        if fetch is not None:                         # tests pass their own fetch and want the result
            th.join(5)
    return _HEB[key]


def _pyluach_text(d):
    try:
        from pyluach import dates
    except Exception:                                 # pragma: no cover - pyluach missing
        return ""
    h = dates.HebrewDate.from_pydate(d)
    return f"{h.hebrew_day()} ב{h.month_name(hebrew=True)} {h.hebrew_year()}"


# ---------------------------------------------------------------- city search

class CityFinder:
    """Search for a city by name: Open-Meteo's geocoding when online, the built-in cities offline
    (by the start of the English or the Hebrew name). The network part runs in a thread; until it
    answers, the built-in matches are shown."""

    URL = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=4&language={lang}&format=json"

    def __init__(self, data_dir=None, fetch=None):
        self.builtin = _load_json("cities.json", data_dir)
        self.fetch = fetch or _fetch
        self.query, self.results, self.busy, self.offline = "", [], False, False
        self._thread = None

    def local(self, query, lang="en"):
        he = bool(re.search(r"[֐-׿]", query)) or lang == "he"
        k = query.lower()
        out = [c for c in self.builtin if c["en"].lower().startswith(k) or c["he"].startswith(query)][:4]
        return [{"name": c["he"] if he else c["en"], "country": c["che"] if he else c["cen"], "admin": "",
                 "lat": c["lat"], "lon": c["lon"], "tz": c["tz"]} for c in out]

    def search(self, query, lang="en", online=True):
        """Start a search and return what is known now; .results fills in when the answer comes."""
        self.query = query
        if len(query) < 2:
            self.results, self.busy = [], False
            return self.results
        if not online:
            self.offline, self.busy = True, False
            self.results = self.local(query, lang)
            return self.results
        self.busy = True
        if not self.results:
            self.results = self.local(query, lang)
        th = threading.Thread(target=self._ask, args=(query, lang), daemon=True)
        self._thread = th
        th.start()
        return self.results

    def _ask(self, q, lang):
        he = bool(re.search(r"[֐-׿]", q)) or lang == "he"
        try:
            raw = self.fetch(self.URL.format(q=urllib.parse.quote(q), lang="he" if he else "en"), 4)
            j = json.loads(raw)
            res = [{"name": x.get("name", ""), "country": x.get("country") or "", "admin": x.get("admin1") or "",
                    "lat": x.get("latitude"), "lon": x.get("longitude"), "tz": x.get("timezone") or "UTC"}
                   for x in (j.get("results") or [])]
            off = False
        except Exception:
            res, off = self.local(q, lang), True
        if q == self.query:                          # an older answer does not replace a newer query
            self.results, self.offline, self.busy = res, off, False

    def wait(self, timeout=5):
        if self._thread:
            self._thread.join(timeout)


# ---------------------------------------------------------------- NASA's picture of the day

class Apod:
    """NASA's Astronomy Picture of the Day, from APOD's RSS feed at science.nasa.gov (api.nasa.gov
    answers with the NASA logo since October 2026). The picture is asked for 1920 wide, downloaded
    in a thread and kept in data_dir, so the last one still shows offline.

    .image   a pygame Surface, or None
    .name    the English title
    .date    a datetime, or None
    .status  idle, loading, ok, offline, no-picture, unreachable, failed
    """

    FEED = "https://science.nasa.gov/feed/apod-basic/"
    TEXT = {"en": {"idle": "", "ok": "", "loading": "loading…", "offline": "no network: the animation stays",
                   "no-picture": "no picture in the feed", "unreachable": "NASA not reachable",
                   "failed": "the picture did not load"},
            "he": {"idle": "", "ok": "", "loading": "טוען…", "offline": "אין רשת: האנימציה נשארת",
                   "no-picture": "אין תמונה בפיד", "unreachable": "אין גישה לנאס״א",
                   "failed": "התמונה לא נטענה"}}

    def __init__(self, data_dir, fetch=None):
        self.dir = Path(data_dir)
        self.fetch = fetch or _fetch
        self.image, self.name, self.date, self.status = None, "", None, "idle"
        self._thread, self._checked = None, 0.0
        self._load_cache()

    @property
    def img_path(self):
        return self.dir / "apod.jpg"

    @property
    def meta_path(self):
        return self.dir / "apod.json"

    def _load_cache(self):
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            img = self._load_image(self.img_path)
            if img is not None:
                self.image, self.name = img, meta.get("name", "")
                self.date = datetime.fromisoformat(meta["date"]) if meta.get("date") else None
                self.status = "ok"
        except Exception:
            pass

    @staticmethod
    def _load_image(path):
        try:
            import pygame
            return pygame.image.load(str(path))
        except Exception:
            return None

    def start(self, online):
        """Check the feed (at most every 3 hours) without waiting for it. Offline, the cached
        picture stays, or the status says why there is none."""
        if not online:
            if self.image is None:
                self.status = "offline"
            return
        # a picture in hand is checked again every 3 hours; without one (the first boot, a network not
        # ready yet, a failed fetch) every 10 minutes, so the NASA screen does not wait 3 hours
        every = 3 * 3600 if self.image is not None else 600
        if (self._thread and self._thread.is_alive()) or time.time() - self._checked < every:
            return
        self._checked = time.time()
        if self.image is None:
            self.status = "loading"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wait(self, timeout=10):
        if self._thread:
            self._thread.join(timeout)

    @staticmethod
    def parse(xml_bytes):
        """The first item with a picture: (src at 1920 wide, title, datetime), or None."""
        root = ET.fromstring(xml_bytes)
        for it in root.iter("item"):
            src, content, title, pub = None, "", "", None
            for ch in it:
                tag = ch.tag.rsplit("}", 1)[-1]
                if tag == "hdurl" and (ch.text or "").strip():
                    src = ch.text.strip()
                elif tag == "encoded":
                    content = ch.text or ""
                elif tag == "title":
                    title = (ch.text or "").strip()
                elif tag == "pubDate":
                    pub = (ch.text or "").strip()
            if not src:
                m = re.search(r'<img[^>]+src="([^"]+)"', content, re.I)
                src = m and m.group(1)
            if not src or "nasa-logo" in src:
                continue                                  # a video day, or the logo
            # the picture server scales on request: the full width of a 24" screen, not more
            src = re.sub(r"([?&])w=\d+&h=\d+", r"\1w=1920", src)
            try:
                when = parsedate_to_datetime(pub) if pub else None
            except Exception:
                when = None
            return src, title, when
        return None

    def _run(self):
        try:
            feed = self.fetch(self.FEED, 8)
        except Exception:
            self.status = "ok" if self.image is not None else "unreachable"
            return
        try:
            item = self.parse(feed)
        except Exception:
            item = None
        if not item:
            self.status = "ok" if self.image is not None else "no-picture"
            return
        src, title, when = item
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if self.image is not None and meta.get("src") == src:
            self.status = "ok"
            return
        try:
            data = self.fetch(src, 20)
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.img_path.with_suffix(".part")
            tmp.write_bytes(data)
            img = self._load_image(tmp)
            if img is None:
                raise ValueError("not a picture")
            os.replace(tmp, self.img_path)
            self.meta_path.write_text(json.dumps({"src": src, "name": title,
                                                  "date": when.isoformat() if when else None}), encoding="utf-8")
            self.image, self.name, self.date, self.status = img, title, when, "ok"
        except Exception:
            self.status = "ok" if self.image is not None else "failed"
