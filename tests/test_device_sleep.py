"""The sleep screen of the Armonico Pi device (device/ui/sleep.py): the clock with the next alarm and
the weather, the Moon in its real phase, the figure asleep; drawn twice a second, woken by a touch.
No network: the weather's fetch is always a mock."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame


def ms_at(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp() * 1000


@unittest.skipIf(pygame is None, "pygame is not installed")
class SleepScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, size=(800, 480), lang="en", weather=None, alarm=None, **st):
        app = T.make_app(self.mod, size, lang, dict(sleep=1, **st, **({"alarm": alarm} if alarm else {})))
        from ui.sleep import SleepScreen, Weather
        app.sleep_screen = SleepScreen()
        w = app.sleep_screen.weather = Weather(None, fetch=mock.Mock(side_effect=OSError("no network in tests")))
        if weather:
            w.data = {"lat": round(app.city["lat"], 2), "lon": round(app.city["lon"], 2),
                      "temp": weather[0], "code": weather[1], "at": time.time()}
        app.last_input = time.monotonic() - 120
        self.assertTrue(app.asleep())
        return app

    def soon(self, app, minutes=60):
        """An alarm that rings in a while, every day, with a song."""
        loc = app.local()
        m = (loc.hour * 60 + loc.minute + minutes) % 1440
        return {"on": True, "time": f"{m // 60:02d}:{m % 60:02d}", "days": "all", "song": "ode_to_joy"}

    def test_it_draws_in_both_languages_and_sizes_without_overflow(self):
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                for kind in ("house", "keys", "grand", "metro"):
                    with self.subTest(size=size, lang=lang, kind=kind):
                        app = self.app(size, lang, weather=(-12.6, 95), character=kind)
                        app.save_settings = lambda **kw: None
                        app.st["alarm"] = self.soon(app)
                        app.draw()
                        boxes = {b[0]: b[1:] for b in app.sleep_screen.boxes}
                        self.assertEqual(set(boxes), {"clock", "line", "moon", "figure"})
                        for name, (x0, y0, x1, y1) in boxes.items():
                            self.assertTrue(0 <= x0 < x1 <= 800 and 0 <= y0 < y1 <= 480, (name, x0, x1))
                        for name in ("clock", "line"):
                            self.assertLess(boxes[name][2], boxes["figure"][0] - 20, f"{name} runs into the figure")
                        a = pygame.surfarray.array3d(app.cv.surf)
                        self.assertLessEqual(int(a.max()), 80, ("nothing on the sleep screen lights the room",
                                                                 [tuple(v) for v in zip(*((a.max(axis=2) > 80).nonzero()))][:5]))
                        # the clock on the left in English, on the right in Hebrew; the figure opposite
                        cv = app.cv
                        x0, y0, x1, y1 = (int(v * cv.scale) for v in boxes["clock"])
                        ox, oy = int(cv.ox), int(cv.oy)
                        left = a[ox + x0: ox + x1, oy + y0: oy + y1].sum()
                        right = a[ox + int(800 * cv.scale) - x1: ox + int(800 * cv.scale) - x0, oy + y0: oy + y1].sum()
                        self.assertTrue(left > 3 * right if lang == "en" else right > 3 * left)
                        self.assertEqual(app.hits, [])

    def test_the_weather_is_left_out_without_data(self):
        app = self.app()
        app.draw()
        self.assertEqual([b[0] for b in app.sleep_screen.boxes], ["clock", "moon", "figure"],
                         "no alarm and no weather: no line under the clock, and no error text")
        app = self.app(alarm=None)
        app.save_settings = lambda **kw: None
        app.st["alarm"] = self.soon(app)
        app.draw()
        alarm_only = [b for b in app.sleep_screen.boxes if b[0] == "line"][0]
        app2 = self.app(weather=(18.4, 2))
        app2.save_settings = lambda **kw: None
        app2.st["alarm"] = self.soon(app2)
        app2.draw()
        both = [b for b in app2.sleep_screen.boxes if b[0] == "line"][0]
        self.assertGreater(both[3] - both[1], alarm_only[3] - alarm_only[1] + 40)
        # an answer too old, or for another city, is not shown
        w = app2.sleep_screen.weather
        w.data["at"] = time.time() - 4 * 3600
        self.assertIsNone(w.now(app2.city))
        w.data["at"] = time.time()
        self.assertIsNone(w.now({"lat": 40.7, "lon": -74.0, "tz": "America/New_York"}))
        self.assertEqual(w.now(app2.city), (18, "w.partly"))

    def test_the_alarm_shows_only_when_the_device_rings_within_a_day(self):
        app = self.app()
        S = app.sleep_screen
        app.st["alarm"] = self.soon(app, 60)
        self.assertEqual(S.next_alarm(app), app.st["alarm"]["time"])
        app.st["alarm"] = dict(self.soon(app, 60), on=False)
        self.assertEqual(S.next_alarm(app), "")
        app.st["alarm"] = dict(self.soon(app, -60))       # an hour ago: tomorrow, still within 24 hours
        self.assertEqual(S.next_alarm(app), app.st["alarm"]["time"])
        wd = app.local().weekday()
        days = {"fri-sat": (4, 5), "mon-fri": (0, 1, 2, 3, 4), "sat-sun": (5, 6)}
        off = [d for d, ds in days.items() if wd not in ds and (wd + 1) % 7 not in ds]
        if off:
            app.st["alarm"] = dict(self.soon(app, 60), days=off[0])
            self.assertEqual(S.next_alarm(app), "", "not today and not tomorrow")

    def test_wmo_codes_map_to_short_words_in_both_languages(self):
        from ui.sleep import condition, WMO
        from ui.texts import T as TEXTS, tr
        self.assertEqual(condition(0), "w.clear")
        self.assertEqual(condition(2), "w.partly")
        self.assertEqual(condition(3), "w.cloudy")
        self.assertEqual(condition(48), "w.fog")
        self.assertEqual(condition(53), "w.drizzle")
        self.assertEqual(condition(63), "w.rain")
        self.assertEqual(condition(75), "w.snow")
        self.assertEqual(condition(81), "w.showers")
        self.assertEqual(condition(99), "w.storm")
        self.assertEqual(condition(42), "")
        self.assertEqual(condition(None), "")
        for code in WMO:
            key = condition(code)
            self.assertIn(key, TEXTS)
            self.assertTrue(tr("en", key) and tr("he", key) != tr("en", key))

    def test_the_weather_is_fetched_in_the_background_at_most_every_30_minutes(self):
        from ui.sleep import Weather
        d = Path(tempfile.mkdtemp())
        answer = json.dumps({"current": {"time": "2026-10-26T16:30", "interval": 900,
                                         "temperature_2m": 24.4, "weather_code": 3}}).encode()
        fetch = mock.Mock(return_value=answer)
        w = Weather(d, fetch=fetch)
        city = {"name": "Tel Aviv", "lat": 32.0809, "lon": 34.7806, "tz": "Asia/Jerusalem"}
        w.start(city, online=False)
        self.assertEqual(fetch.call_count, 0, "offline: not asked")
        w.start(city, online=True)
        w.wait()
        url = fetch.call_args[0][0]
        self.assertIn("api.open-meteo.com/v1/forecast", url)
        self.assertIn("current=temperature_2m,weather_code", url)
        self.assertIn("latitude=32.08", url)
        self.assertEqual(w.now(city), (24, "w.cloudy"))
        w.start(city, online=True)
        w.wait()
        self.assertEqual(fetch.call_count, 1, "asked again only after 30 minutes")
        self.assertEqual(Weather(d).now(city), (24, "w.cloudy"), "kept on disk")
        bad = Weather(Path(tempfile.mkdtemp()), fetch=mock.Mock(side_effect=OSError("down")))
        bad.start(city, online=True)
        bad.wait()
        self.assertIsNone(bad.now(city))

    def test_the_app_starts_no_fetch_in_tests(self):
        app = self.app()
        app.draw()
        self.assertEqual(app.sleep_screen.weather.fetch.call_count, 0)

    def test_the_moon_lit_side_follows_the_phase(self):
        from ui.sleep import moon_phase, MOON
        waxing, waning = ms_at(2026, 10, 15, 21), ms_at(2026, 11, 4, 21)
        self.assertIsNotNone(moon_phase(waxing), "No Moon phase: the device's sky needs astronomy-engine "
                             "(pip install astronomy-engine), which install.sh puts in the device's venv")
        self.assertLess(moon_phase(waxing), 180)
        self.assertGreater(moon_phase(waning), 180)
        for lang in ("en", "he"):
            for ms, lit in ((waxing, "right"), (waning, "left")):
                with self.subTest(lang=lang, lit=lit):
                    app = self.app(lang=lang)
                    with mock.patch.object(app, "now_ms", return_value=ms):
                        app.draw()
                    a = pygame.surfarray.array3d(app.cv.surf).sum(axis=2)
                    x, y, r = MOON
                    x = 800 - x if lang == "he" else x            # the Moon is in the clock's corner
                    L = a[x - r + 3: x - 5, y - 6: y + 6].mean()
                    R = a[x + 6: x + r - 3, y - 6: y + 6].mean()
                    self.assertGreater(R if lit == "right" else L, (L if lit == "right" else R) + 20)

    def test_the_figure_sleeps_with_its_eyes_shut(self):
        from ui.characters import Character
        c = Character(seed=1)
        c.cool_until = 1e9                              # even with the sunglasses Easter egg on
        shut = []
        c._bar = staticmethod(lambda cv: (lambda x, y, w: shut.append((x, y))))
        app = self.app()
        c.draw_sleeping(app.cv, "house", 400, 300, 1.0, 1.02)
        self.assertEqual(len(shut), 2)
        self.assertFalse(c.nap, "awake again after the drawing")

    def test_it_draws_15_times_a_second_while_asleep_and_wakes_on_a_touch(self):
        app = self.app()
        draws, start = [], time.monotonic()
        real = app.draw

        def draw():
            draws.append(app.asleep())
            real()

        def tick():
            if time.monotonic() - start > 1.6:
                pygame.event.post(pygame.event.Event(pygame.QUIT))

        with mock.patch.object(app, "draw", draw), mock.patch.object(app, "tick", tick):
            app.run()
        self.assertTrue(all(draws))
        self.assertLessEqual(len(draws), 32, "about 15 frames a second while asleep")
        self.assertGreaterEqual(len(draws), 12)
        app.pointer_down(10, 10)
        self.assertFalse(app.asleep())
        self.assertEqual(app.view, "rest")
        draws.clear()
        start = time.monotonic() - 1.3
        with mock.patch.object(app, "draw", draw), mock.patch.object(app, "tick", tick):
            app.run()
        self.assertGreater(len(draws), 7, "awake: the usual rate (30 a second, over 0.3 s)")
        self.assertFalse(any(draws))

    def test_the_sleep_screen_drifts_a_few_pixels(self):
        app = self.app()
        seen = set()
        for k in range(20):
            with mock.patch("time.time", return_value=1_800_000_000 + k * 30):
                app.follow_shift()
            seen.add(app.shift)
            self.assertTrue(abs(app.shift[0]) <= 4 and abs(app.shift[1]) <= 3)
        self.assertGreater(len(seen), 4)
        app.last_input = time.monotonic()
        app.follow_shift()
        self.assertEqual(app.shift, (0, 0), "awake with sleep on: no move, as before")

    def test_the_layer_is_drawn_once_a_minute(self):
        app = self.app(weather=(20, 0))
        S = app.sleep_screen
        with mock.patch.object(S, "_layer", wraps=S._layer) as lay:
            for _ in range(5):
                app.draw()
            self.assertEqual(lay.call_count, 1)
            with mock.patch.object(app, "now_ms", return_value=app.now_ms() + 61000):
                app.draw()
            self.assertEqual(lay.call_count, 2)


if __name__ == "__main__":
    unittest.main()
