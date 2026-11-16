"""The idle frame rate of the Armonico Pi device (device/ui/app.py run): a still page nobody touches is drawn 8 times
a second instead of 30, and anything that happens brings the full rate back at once. The loop is run for real
with a fake clock (every clock.tick moves it by 1/fps), so the tests take no real waiting. Also the render cache
of device/ui/canvas.py: the oldest lines go, never all of them."""
import time
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame


class Fake:
    """The clock of the loop: monotonic() and time() move only when the loop's clock.tick waits."""

    def __init__(self):
        self.start = time.monotonic()      # the app's own t0 comes from the real clock: the fake starts there, so t() >= 0 on any host uptime
        self.now = self.start

    def mono(self):
        return self.now

    def wall(self):
        # a fixed wall time that is 10 s into a minute: the background layer is made again when the minute turns, and a
        # turn inside a measured window would hold the full rate for a moment and make the tests depend on the host clock
        return 1_800_000_010 + (self.now - self.start)


@unittest.skipIf(pygame is None, "pygame is not installed")
class IdleRate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()
        sys.path.insert(0, str(T.ROOT / "device"))

    def setUp(self):
        from ui import app as app_mod
        self.am = app_mod
        self.app = T.make_app(self.mod, (800, 480), "en", {"sleep": 0})
        self.app.save_settings = lambda **kw: None
        self.app.view = "settings"
        # the sky spawns meteors at random on a dark night with an active shower, and a live meteor keeps the full
        # rate by design: the tests must not depend on the real date and time of day
        import random

        class NoMeteors(random.Random):
            def random(self):
                return 1.0
        if getattr(self.app, "sky", None) is not None:
            self.app.sky.rnd = NoMeteors()
            self.app.sky.meteors = []
        self.fake = Fake()
        self.draws, self.ticks, self.script = [], [], []
        self.t_start = self.fake.now

    def run_loop(self, seconds, view="settings"):
        """Runs the real loop for `seconds` of fake time. self.script: (at, fn) called when the fake time passes `at`
        (seconds after the start), in the loop's clock.tick, i.e. between two frames. Returns the draw times."""
        app, fake, am = self.app, self.fake, self.am
        app.view = view
        start = fake.now
        script = sorted(self.script, key=lambda s: s[0])
        real_draw = app.draw

        def draw():
            self.draws.append(fake.now - start)
            real_draw()

        class Clock:
            def tick(inner, fps=0):
                self.ticks.append(fps)
                fake.now += 1.0 / fps if fps else 0.001
                while script and fake.now - start >= script[0][0]:
                    script.pop(0)[1]()
                if fake.now - start >= seconds:
                    pygame.event.post(pygame.event.Event(pygame.QUIT))

        clock_time = types.SimpleNamespace(monotonic=fake.mono, time=fake.wall, sleep=lambda s: None,
                                           strftime=__import__("time").strftime, localtime=__import__("time").localtime,
                                           gmtime=__import__("time").gmtime, struct_time=__import__("time").struct_time)
        pygame.event.clear()
        with mock.patch.object(am, "time", clock_time), mock.patch.object(pygame.time, "Clock", Clock), \
                mock.patch.object(app, "draw", draw), mock.patch.object(am, "IDLE_FPS", self.idle_fps()):
            app.last_input = fake.now
            app.run()
        return self.draws

    def idle_fps(self):
        return 8

    def rate(self, a, b):
        """Frames a second drawn between a and b seconds."""
        return sum(1 for d in self.draws if a <= d < b) / (b - a)

    def test_a_still_page_drops_to_the_low_rate_after_the_quiet_time(self):
        self.run_loop(4.0)
        self.assertGreaterEqual(self.rate(0.0, 0.9), 25, "busy at the start: the full rate")
        self.assertLessEqual(self.rate(2.0, 4.0), 10, "quiet: about 8 a second")
        self.assertGreaterEqual(self.rate(2.0, 4.0), 6)

    def test_a_pointer_down_wakes_it_and_the_next_frame_is_drawn_at_once(self):
        at = 2.05
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(790, 4), button=1)
        self.script.append((at, lambda: pygame.event.post(down)))
        self.run_loop(4.0)
        after = [d for d in self.draws if d > at]
        self.assertLess(after[0] - at, 0.04, "drawn on the next look at the input, not after up to 125 ms")
        self.assertGreaterEqual(self.rate(at, at + 0.9), 25, "the full rate again")
        self.assertLessEqual(self.rate(3.6, 4.0), 12, "and quiet again after a second")

    def test_a_key_event_wakes_it_too(self):
        at = 2.2
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0, unicode="", scancode=0)
        self.script.append((at, lambda: pygame.event.post(ev)))
        self.run_loop(3.5)
        after = [d for d in self.draws if d > at]
        self.assertLess(after[0] - at, 0.04)

    def test_a_message_from_the_engine_or_a_button_wakes_it(self):
        at = 2.3

        def message():
            with self.app.screen.ev_lock:
                self.app.screen.events.append(("piano/device/unknown", "x"))
        self.script.append((at, message))
        self.run_loop(3.6)
        after = [d for d in self.draws if d > at]
        self.assertLess(after[0] - at, 0.04)
        self.assertGreaterEqual(self.rate(at, at + 0.9), 25)

    def test_a_view_that_is_not_on_the_list_never_drops(self):
        for view in ("rest", "free", "test", "diag", "edit", "check", "qr", "place"):
            with self.subTest(view=view):
                self.draws.clear()
                self.ticks.clear()
                self.script.clear()
                self.fake.now = self.fake.start
                self.run_loop(3.0, view)
                self.assertNotIn(view, self.am.IDLE_VIEWS)
                self.assertGreaterEqual(self.rate(1.5, 3.0), 25, view)

    def test_a_change_of_state_wakes_it(self):
        at = 2.5
        self.script.append((at, lambda: self.app.go("about")))
        self.run_loop(4.5)
        self.assertGreaterEqual(self.rate(at + 0.05, at + 0.9), 25)
        self.assertLessEqual(self.rate(3.9, 4.5), 12)
        # a layer made dirty (a setting changed, a network up or down) is a change as well
        self.draws.clear()
        self.script.clear()
        self.script.append((2.5, self.app.layer_dirty))
        self.run_loop(3.0)
        self.assertGreaterEqual(self.rate(2.55, 3.0), 25)

    def test_a_toast_keeps_the_full_rate_until_it_is_gone(self):
        self.script.append((2.0, lambda: self.app.toast("saved", 2.0)))
        self.run_loop(6.0)
        self.assertGreaterEqual(self.rate(2.1, 3.9), 25, "while the message is up")
        self.assertLessEqual(self.rate(5.2, 6.0), 12, "and quiet after it, a second later")

    def test_a_drag_or_a_scroll_never_drops(self):
        self.script.append((1.5, lambda: setattr(self.app, "drag", {"who": "mouse", "x": 1, "y": 1, "hit": None,
                                                                    "z": None, "far": 0.0, "dead": False,
                                                                    "at": self.fake.now})))
        self.script.append((4.0, lambda: setattr(self.app, "drag", None)))
        self.run_loop(7.0)
        self.assertGreaterEqual(self.rate(2.5, 3.9), 25, "a finger is down")
        self.assertLessEqual(self.rate(6.0, 7.0), 12, "it lifted: quiet again")

    def test_a_held_button_never_drops(self):
        self.script.append((1.5, lambda: self.app.down.update(menu=self.fake.now)))
        self.script.append((4.0, lambda: self.app.down.clear()))
        self.run_loop(4.0)
        self.assertGreaterEqual(self.rate(2.0, 3.9), 25)

    def _clock_view(self, text_of):
        """The settings page with a line drawn in the header, like the alarm chip: text_of(seconds) is its text."""
        real = self.app.view_settings

        def view(t):
            real(t)
            self.app.cv.text(text_of(self.fake.now - self.fake.start), 700, 20, 14, (255, 255, 255))
        return mock.patch.object(self.app, "view_settings", view)

    def test_a_header_clock_that_changes_each_second_keeps_the_full_rate(self):
        with self._clock_view(lambda s: f"alarm {int(s)}"):
            self.run_loop(5.0)
        self.assertGreaterEqual(self.rate(2.0, 5.0), 25, "the drawn text changes: no frame may be skipped")

    def test_a_header_clock_that_changes_each_minute_costs_one_busy_second_a_minute(self):
        with self._clock_view(lambda s: f"alarm {int(s // 3)}"):    # a change every 3 s, to see it in a short test
            self.run_loop(9.0)
        slow = [d for d in self.draws if 1.5 < d < 9.0]
        self.assertLess(len(slow), 7.5 * 30 * 0.7, "dropped between the changes")
        # the change itself is drawn within one idle frame (125 ms) and wakes the full rate for a second
        self.assertGreaterEqual(self.rate(3.2, 3.9), 25)

    def test_it_can_be_switched_off(self):
        self.idle_fps = lambda: None
        self.run_loop(4.0)
        self.assertGreaterEqual(self.rate(2.0, 4.0), 25)
        self.assertEqual(set(self.ticks), {self.am.FPS})

    def test_the_environment_override(self):
        import subprocess
        for value, want in (("off", "None"), ("0", "None"), ("5", "5")):
            out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); from ui import app; print(app.IDLE_FPS)"
                                  % str(T.ROOT / "device")], env={**os.environ, "ARMONICO_IDLE_FPS": value},
                                 capture_output=True, text=True, timeout=120).stdout.split()[-1]
            self.assertEqual(out, want, value)


@unittest.skipIf(pygame is None, "pygame is not installed")
class RenderCache(unittest.TestCase):
    def test_over_the_cap_the_oldest_lines_go_and_the_newest_stay(self):
        from ui import canvas
        cv = canvas.Canvas(pygame, pygame.display.set_mode((800, 480)), canvas.Fonts(pygame, T.font_dir()))
        with mock.patch.object(canvas, "CACHE_MAX", 60), mock.patch.object(canvas, "CACHE_DROP", 10):
            for i in range(200):
                cv.render(f"line {i}", 16, (255, 255, 255))
                self.assertLessEqual(len(cv._cache), 60)
            keys = [k[0] for k in cv._cache]
            for i in range(150, 200):
                self.assertIn(f"line {i}", keys, "recent lines survive")
            self.assertNotIn("line 0", keys)
            a = cv.render("line 199", 16, (255, 255, 255))
            self.assertIs(a, cv.render("line 199", 16, (255, 255, 255)), "a hit is the same image, as before")

    def test_the_size_stays_bounded_with_a_ticking_string(self):
        from ui import canvas
        cv = canvas.Canvas(pygame, pygame.display.set_mode((800, 480)), canvas.Fonts(pygame, T.font_dir()))
        for i in range(canvas.CACHE_MAX + 400):
            cv.render(f"12:{i:04d}", 20, (255, 255, 255))
        self.assertLessEqual(len(cv._cache), canvas.CACHE_MAX)
        self.assertGreater(len(cv._cache), canvas.CACHE_MAX - canvas.CACHE_DROP - 1, "not emptied")


if __name__ == "__main__":
    unittest.main()
