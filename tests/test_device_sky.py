"""Tests for the device sky: astronomy, events, Hebrew date, city search, APOD and drawing.
Nothing here touches the network: every fetch is a fake passed in."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "device" / "ui"

try:
    import astronomy  # noqa: F401
    HAVE_ASTRO = True
except Exception:
    HAVE_ASTRO = False
try:
    import pygame
    HAVE_PG = True
except Exception:
    HAVE_PG = False


def load(name):
    spec = importlib.util.spec_from_file_location(f"armonico_device_{name}_test", UI / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sky = load("sky")
JERUSALEM = {"name": "Jerusalem", "lat": 31.769, "lon": 35.2163, "tz": "Asia/Jerusalem"}


def utc_ms(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp() * 1000


def no_network(url, timeout):
    raise AssertionError("the network was touched: " + url)


@unittest.skipUnless(HAVE_ASTRO, "astronomy-engine is not installed")
class AstronomyTest(unittest.TestCase):
    def setUp(self):
        self.sky = sky.Sky(seed=1)

    def test_sun_high_at_noon_on_the_summer_solstice(self):
        # solar noon in Jerusalem is about 09:40 UTC; 90 - 31.77 + 23.44 = 81.7 degrees
        st = self.sky.compute(utc_ms(2026, 6, 21, 9, 40), JERUSALEM)
        self.assertAlmostEqual(st.sun.altitude, 81.7, delta=0.5)
        self.assertLess(abs(st.sun.azimuth - 180), 10)

    def test_sun_down_at_midnight(self):
        st = self.sky.compute(utc_ms(2026, 6, 21, 21, 40), JERUSALEM)
        self.assertLess(st.sun.altitude, -30)
        self.assertTrue(st.stars)
        self.assertTrue(all(s[1] > 0 for s in st.stars))

    def test_polaris_altitude_is_the_latitude(self):
        st = self.sky.compute(utc_ms(2026, 1, 10, 20, 0), JERUSALEM)
        polaris = [s for s in st.stars if s[3] == "Polaris"]
        self.assertEqual(len(polaris), 1)
        self.assertAlmostEqual(polaris[0][1], JERUSALEM["lat"], delta=1.2)

    def test_stars_match_astronomy_engine(self):
        import astronomy as A
        ms = utc_ms(2026, 10, 23, 19, 30)
        st = self.sky.compute(ms, JERUSALEM)
        t = sky._time(A, ms)
        obs = A.Observer(JERUSALEM["lat"], JERUSALEM["lon"], 0)
        for az, alt, _mag, name, i in st.stars[:40]:
            ra, dec = self.sky.stars[i][0] % 360, self.sky.stars[i][1]
            h = A.Horizon(t, obs, ra / 15, dec, A.Refraction.Airless)
            self.assertAlmostEqual(alt, h.altitude, delta=0.7, msg=name)
            if alt < 80:
                self.assertLess(abs((az - h.azimuth + 180) % 360 - 180), 1.5, name)

    def test_full_moon_is_lit(self):
        st = self.sky.compute(utc_ms(2026, 10, 26, 4, 0), JERUSALEM)
        self.assertGreater(st.illum, 0.97)
        self.assertAlmostEqual(st.phase, 180, delta=5)

    def test_sunrise_and_sunset(self):
        ms = utc_ms(2026, 10, 23, 9, 0)
        st = self.sky.compute(ms, JERUSALEM)
        rise = sky.local_dt(st.rise, "Asia/Jerusalem")
        sset = sky.local_dt(st.set, "Asia/Jerusalem")
        self.assertEqual((rise.hour, sset.hour), (6, 17))     # still summer time on 23.10.2026

    def test_cached_per_minute_and_city(self):
        ms = utc_ms(2026, 10, 23, 19, 30)
        a = self.sky.compute(ms, JERUSALEM)
        self.assertIs(self.sky.compute(ms + 20000, JERUSALEM), a)
        self.assertIsNot(self.sky.compute(ms + 61000, JERUSALEM), a)
        other = dict(JERUSALEM, name="Sydney", lat=-33.87, lon=151.21, tz="Australia/Sydney")
        self.assertNotEqual(self.sky.compute(ms + 61000, other).sun.altitude, a.sun.altitude)

    def test_events_include_moon_quarter_and_eclipses(self):
        ms = utc_ms(2026, 10, 23, 19, 30)
        st = self.sky.compute(ms, JERUSALEM)
        kinds = [e[1] for e in st.ev]
        self.assertIn("moon", kinds)
        self.assertEqual(st.ev, sorted(st.ev, key=lambda e: e[0]))
        lunar = [e for e in st.ev if e[1] == "lunar"]
        self.assertTrue(lunar)                                    # 20.2.2027, penumbral
        self.assertEqual(lunar[0][2]["kind"], "penumbral")

    def test_active_shower_in_august(self):
        st = self.sky.compute(utc_ms(2026, 8, 12, 23, 0), JERUSALEM)
        self.assertIsNotNone(st.shower)
        self.assertEqual(st.shower["n"], "Perseids")
        self.assertTrue(any(e[1] == "meteor" and e[2]["n"] == "Perseids" for e in st.ev))


class EventsTest(unittest.TestCase):
    def test_week_only(self):
        ms = utc_ms(2026, 10, 23, 12, 0)
        D = sky.DAY_MS
        st = sky.SkyState(ev=[(ms - 2 * D, "moon", {"q": 0}), (ms - D / 2, "moon", {"q": 1}),
                              (ms + 3 * D, "moon", {"q": 2}), (ms + 6.9 * D, "meteor", {"n": "Orionids", "zhr": 20}),
                              (ms + 8 * D, "moon", {"q": 3}), (ms + 120 * D, "lunar", {"kind": "total", "up": True})])
        got = sky.events_week(st, ms)
        self.assertEqual([e[0] for e in got], [ms - D / 2, ms + 3 * D, ms + 6.9 * D])

    def test_texts(self):
        ms = utc_ms(2026, 10, 26, 4, 12)
        tz = "Asia/Jerusalem"
        self.assertEqual(sky.event_text((ms, "moon", {"q": 2}), "en", tz), ("Full moon", "26 Oct"))
        self.assertEqual(sky.event_text((ms, "moon", {"q": 2}), "he", tz), ("ירח מלא", "26 באוקטובר"))
        self.assertEqual(sky.event_text((ms, "lunar", {"kind": "penumbral", "up": True}), "en", tz)[0],
                         "Penumbral lunar eclipse, visible here")
        self.assertEqual(sky.event_text((ms, "lunar", {"kind": "total", "up": False}), "he", tz)[0],
                         "ליקוי ירח מלא, מתחת לאופק כאן")
        self.assertEqual(sky.event_text((ms, "solar", {"kind": "partial", "p": 80}), "he", tz)[0],
                         "ליקוי חמה חלקי, 80% מהשמש מוסתרים")
        self.assertEqual(sky.event_text((ms, "solar", {"kind": "annular", "p": 91}), "en", tz)[0],
                         "Annular solar eclipse, 91% of the Sun covered")
        self.assertEqual(sky.event_text((ms, "meteor", {"n": "Geminids", "zhr": 150}), "he", tz)[0],
                         "שיא מטר הג׳מינידים, עד 150 מטאורים בשעה")
        self.assertEqual(sky.event_text((ms, "meteor", {"n": "Geminids", "zhr": 150}), "en", tz)[0],
                         "Geminids at their peak, up to 150 an hour")

    def test_date_in_the_city_time_zone(self):
        ms = utc_ms(2026, 10, 25, 23, 30)                         # already the 26th in Jerusalem
        self.assertEqual(sky.event_text((ms, "moon", {"q": 0}), "en", "Asia/Jerusalem")[1], "26 Oct")
        self.assertEqual(sky.event_text((ms, "moon", {"q": 0}), "en", "America/New_York")[1], "25 Oct")


@unittest.skipIf(importlib.util.find_spec("pyluach") is None, "pyluach is not installed")
class HebrewDateTest(unittest.TestCase):
    def setUp(self):
        sky._HEB.clear()
        sky._HEB_ASKED.clear()

    def test_offline(self):
        ms = utc_ms(2026, 10, 24, 9, 0)
        self.assertEqual(sky.hebrew_date(ms, "Asia/Jerusalem", False), "י״ג בחשון תשפ״ז")

    def test_after_sunset_is_the_next_day(self):
        ms = utc_ms(2026, 10, 24, 17, 0)
        self.assertEqual(sky.hebrew_date(ms, "Asia/Jerusalem", True), "י״ד בחשון תשפ״ז")

    def test_rosh_hashana(self):
        ms = utc_ms(2026, 9, 12, 9, 0)
        self.assertEqual(sky.hebrew_date(ms, "Asia/Jerusalem", False), "א׳ בתשרי תשפ״ז")

    def test_hebcal_refines(self):
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return json.dumps({"heDateParts": {"y": "תשפ״ז", "m": "חשון", "d": "י״ג"}}).encode()

        ms = utc_ms(2026, 10, 24, 9, 0)
        sky.hebrew_date(ms, "Asia/Jerusalem", False, online=True, fetch=fake)
        self.assertEqual(sky.hebrew_date(ms, "Asia/Jerusalem", False, online=True, fetch=fake), "י״ג בחשון תשפ״ז")
        self.assertEqual(len(calls), 1)                           # once a day, not every frame
        self.assertIn("gy=2026&gm=10&gd=24&g2h=1", calls[0])

    def test_hebcal_failure_keeps_offline_date(self):
        def broken(url, timeout):
            raise OSError("no network")

        ms = utc_ms(2026, 10, 24, 9, 0)
        self.assertEqual(sky.hebrew_date(ms, "Asia/Jerusalem", False, online=True, fetch=broken), "י״ג בחשון תשפ״ז")


class CityTest(unittest.TestCase):
    def test_builtin_count(self):
        f = sky.CityFinder(fetch=no_network)
        self.assertEqual(len(f.builtin), 78)  # 77 from the demo, plus London

    def test_offline_prefix_english_and_hebrew(self):
        f = sky.CityFinder(fetch=no_network)
        res = f.search("jer", "en", online=False)
        self.assertEqual(res[0]["name"], "Jerusalem")
        self.assertEqual(set(res[0]), {"name", "country", "admin", "lat", "lon", "tz"})
        self.assertTrue(f.offline)
        res = f.search("ירו", "en", online=False)
        self.assertEqual(res[0]["name"], "ירושלים")
        self.assertEqual(res[0]["country"], "ישראל")
        self.assertEqual(f.search("j", "en", online=False), [])

    def test_online_answer(self):
        seen = []

        def fake(url, timeout):
            seen.append((url, timeout))
            return json.dumps({"results": [{"name": "רחובות", "latitude": 31.89, "longitude": 34.81,
                                            "country": "ישראל", "admin1": "מחוז המרכז",
                                            "timezone": "Asia/Jerusalem"}]}).encode()

        f = sky.CityFinder(fetch=fake)
        f.search("רחו", "he", online=True)
        f.wait()
        self.assertFalse(f.busy)
        self.assertEqual(f.results, [{"name": "רחובות", "country": "ישראל", "admin": "מחוז המרכז",
                                      "lat": 31.89, "lon": 34.81, "tz": "Asia/Jerusalem"}])
        self.assertIn("language=he", seen[0][0])
        self.assertIn("count=4", seen[0][0])
        self.assertEqual(seen[0][1], 4)

    def test_online_failure_falls_back(self):
        def broken(url, timeout):
            raise OSError("timed out")

        f = sky.CityFinder(fetch=broken)
        f.search("Tok", "en", online=True)
        f.wait()
        self.assertTrue(f.offline)
        self.assertEqual(f.results[0]["name"], "Tokyo")


FEED = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:apod="https://science.nasa.gov/apod/">
<channel><title>APOD Basic</title>
<item><title>A Video Day</title><pubDate>Sat, 24 Oct 2026 04:05:00 +0000</pubDate>
<content:encoded><![CDATA[<p>a video</p>]]></content:encoded></item>
<item><title>Andromeda in Southern Spring</title><pubDate>Fri, 23 Oct 2026 04:05:00 +0000</pubDate>
<apod:hdurl>https://assets.science.nasa.gov/dynamicimage/apod/APOD_Lucy.jpg?w=3000&#038;h=2004&#038;fit=clip</apod:hdurl>
</item></channel></rss>"""


@unittest.skipUnless(HAVE_PG, "pygame is not installed")
class ApodTest(unittest.TestCase):
    def picture(self):
        s = pygame.Surface((8, 4))
        s.fill((10, 20, 30))
        tmp = Path(tempfile.mkdtemp()) / "p.png"
        pygame.image.save(s, str(tmp))
        return tmp.read_bytes()

    def test_parse_skips_video_days_and_asks_1920(self):
        src, title, when = sky.Apod.parse(FEED)
        self.assertEqual(src, "https://assets.science.nasa.gov/dynamicimage/apod/APOD_Lucy.jpg?w=1920&fit=clip")
        self.assertEqual(title, "Andromeda in Southern Spring")
        self.assertEqual((when.year, when.month, when.day), (2026, 10, 23))

    def test_download_and_cache_offline(self):
        d = tempfile.mkdtemp()
        pic, urls = self.picture(), []

        def fake(url, timeout):
            urls.append(url)
            return FEED if "feed" in url else pic

        a = sky.Apod(d, fetch=fake)
        a.start(True)
        a.wait()
        self.assertEqual(a.status, "ok")
        self.assertEqual(a.image.get_size(), (8, 4))
        self.assertEqual(a.name, "Andromeda in Southern Spring")
        self.assertEqual(urls[0], "https://science.nasa.gov/feed/apod-basic/")
        # a new start offline: the kept picture is there, and nothing is fetched
        b = sky.Apod(d, fetch=no_network)
        b.start(False)
        self.assertEqual(b.status, "ok")
        self.assertEqual(b.name, "Andromeda in Southern Spring")
        self.assertIsNotNone(b.image)
        self.assertEqual(b.date.day, 23)

    def test_offline_without_cache(self):
        a = sky.Apod(tempfile.mkdtemp(), fetch=no_network)
        a.start(False)
        self.assertEqual(a.status, "offline")
        self.assertIsNone(a.image)

    def test_unreachable(self):
        def broken(url, timeout):
            raise OSError("down")

        a = sky.Apod(tempfile.mkdtemp(), fetch=broken)
        a.start(True)
        a.wait()
        self.assertEqual(a.status, "unreachable")


class PaletteTest(unittest.TestCase):
    KEYS = {"ink", "dim", "panel", "sel", "gold", "bar", "key", "black", "onkey", "lit", "ok", "line"}

    def test_keys(self):
        for theme in ("classic", "classicLight", "auto", "sky"):
            for dark in (True, False):
                for alt in (-30, 30):
                    self.assertEqual(set(sky.palette(theme, dark, alt)), self.KEYS)

    def test_values(self):
        self.assertEqual(sky.palette("classic", True, 0)["ink"], (0xe9, 0xed, 0xf5))
        self.assertEqual(sky.palette("classicLight", False, 0)["line"], (0xe4, 0xdc, 0xcb))
        self.assertEqual(sky.palette("sky", True, 30)["ink"], (0x14, 0x21, 0x3d))
        self.assertEqual(sky.palette("sky", True, -30)["ink"], (255, 255, 255))
        self.assertEqual(sky.palette("sky", True, -30)["panel"], (255, 255, 255, 36))

    def test_sky_colors_through_the_bands(self):
        self.assertEqual(sky.sky_colors(-90), ((6, 9, 26), (14, 20, 48)))
        self.assertEqual(sky.sky_colors(0), ((70, 110, 196), (255, 186, 128)))
        self.assertEqual(sky.sky_colors(90), ((52, 120, 214), (170, 210, 244)))

    def test_is_dark(self):
        night, day = sky.SkyState(sun=sky.Pos(0, -10)), sky.SkyState(sun=sky.Pos(0, 10))
        self.assertTrue(sky.is_dark("classic", day))
        self.assertFalse(sky.is_dark("classicLight", night))
        self.assertTrue(sky.is_dark("auto", night))
        self.assertFalse(sky.is_dark("auto", day))


@unittest.skipUnless(HAVE_PG and HAVE_ASTRO, "pygame or astronomy-engine is not installed")
class DrawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        sys.path.insert(0, str(UI))
        cls.canvas = load("canvas")

    def cv(self, w=800, h=480):
        surf = pygame.Surface((w, h))
        return self.canvas.Canvas(pygame, surf, self.canvas.Fonts(pygame, ROOT / "no-fonts")), surf

    def test_night_sky_draws_and_is_dark_at_top(self):
        s = sky.Sky(seed=1)
        st = s.compute(utc_ms(2026, 10, 23, 19, 30), JERUSALEM)
        cv, surf = self.cv()
        zen, hor = s.draw(cv, st, 1.0)
        sky.hills(cv, hor, 1)
        self.assertEqual((zen, hor), sky.sky_colors(st.sun.altitude))
        top = surf.get_at((5, 2))
        self.assertLess(sum(top[:3]), 120)

    def test_hebrew_does_not_mirror_the_sky(self):
        ms = utc_ms(2026, 10, 23, 15, 45)                             # dusk, the glow to the west
        a, sa = self.cv()
        s1 = sky.Sky(seed=1)
        s1.draw(a, s1.compute(ms, JERUSALEM), 1.0, lang="en")
        b, sb = self.cv()
        b.rtl = True
        s2 = sky.Sky(seed=1)
        s2.draw(b, s2.compute(ms, JERUSALEM), 1.0, lang="en")
        self.assertEqual(pygame.image.tostring(sa, "RGB"), pygame.image.tostring(sb, "RGB"))

    def test_classic_and_wide_screen(self):
        cv, surf = self.cv(1920, 1080)
        sky.classic(cv, 1.0, True)
        self.assertEqual(tuple(surf.get_at((2, 2)))[:3], (13, 20, 36))
        sky.classic(cv, 1.0, False)
        self.assertEqual(tuple(surf.get_at((2, 2)))[:3], (245, 241, 232))


if __name__ == "__main__":
    unittest.main()
