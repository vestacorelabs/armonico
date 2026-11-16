"""A drag scrolls the long lists that had ▼ ▲ only: Songs, the online search and the TV library.

Each of them draws a position bar and sets the drag zone (widgets.scroll_zone) when it holds more rows than
the page shows; the marked row stays on the page, so ✓ never acts on a row that is out of sight.
"""
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None


@unittest.skipIf(pygame is None, "pygame is not installed")
class ADragScrollsTheLongLists(unittest.TestCase):
    def setUp(self):
        self.app = make_app(load_screen(), (800, 480), "en")

    def test_songs(self):
        a = self.app
        songs = [{"name": f"song_{i}", "total": 4, "learned": 0} for i in range(9)]
        a.screen.songs = lambda: songs
        a.screen.sel_song, a.screen.song_first = 0, 0
        a.go("songs")
        a.draw()
        self.assertIsNotNone(a.zone)
        a.zone[5](3)                                       # three rows down
        self.assertEqual((a.screen.song_first, a.screen.sel_song), (3, 3))
        a.draw()
        self.assertEqual(a.screen.song_first, 3)           # the page stays where the finger left it
        a.zone[5](99)
        self.assertEqual((a.screen.song_first, a.screen.sel_song), (4, 4))    # it stops at the end
        a.zone[5](-99)
        self.assertEqual((a.screen.song_first, a.screen.sel_song), (0, 4))    # the marked song still on the page
        a.screen.songs = lambda: songs[:5]
        a.draw()
        self.assertIsNone(a.zone)                          # all of it shown: nothing to scroll

    def test_the_online_search(self):
        a = self.app
        a.go("songnet")
        sn = a.sn_state()
        sn.update(results=[{"title": f"r{i}", "id": str(i)} for i in range(12)], sel=1, first=0, busy=False, error="")
        a.draw()
        self.assertIsNotNone(a.zone)
        a.zone[5](4)
        self.assertEqual((sn["first"], sn["sel"]), (4, 5))
        sn["sel"] = 0                                      # the search field marked: it stays marked
        a.zone[5](2)
        self.assertEqual((sn["first"], sn["sel"]), (6, 0))
        a.zone[5](99)
        self.assertEqual(sn["first"], 7)

    def test_the_tv_library(self):
        a = self.app
        a.px = {"cfg": {"url": "http://x", "token": True}, "path": [], "items": [], "sel": 0, "first": 0, "queue": [],
                "off": True, "busy": False, "error": None, "note": ("", 0.0), "setup": False, "url": "http://x", "token": ""}
        rows = [{"act": "open", "title": f"row {i}", "sub": ""} for i in range(11)]
        a.px_rows = lambda: rows
        a.view = "plex"
        a.draw()
        self.assertIsNotNone(a.zone)
        a.zone[5](2)
        self.assertEqual((a.px["first"], a.px["sel"]), (2, 2))
        a.zone[5](99)
        self.assertEqual((a.px["first"], a.px["sel"]), (6, 6))


if __name__ == "__main__":
    unittest.main()


class Fling(unittest.TestCase):
    """15.11.2026: a list let go while the finger still moved goes on by itself and slows down."""

    def test_a_quick_lift_goes_on_and_slows_down(self):
        import time as _time
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device"))
        from ui.app import App
        steps = []
        app = object.__new__(App)
        app.view, app.drag = "songs", None
        app.bump = lambda: None
        now = [100.0]
        with mock.patch.object(_time, "monotonic", lambda: now[0]):
            z = (0, 0, 800, 300, 50, steps.append)
            app.fling_start({"z": z, "track": [(99.95, 300.0), (100.0, 250.0)]})   # 1000 px/s up: 20 rows a second
            self.assertIsNotNone(app.fling)
            for _ in range(60):
                now[0] += 1 / 30
                app.fling_tick()
        self.assertGreater(sum(steps), 3)
        self.assertTrue(all(s > 0 for s in steps))
        self.assertIsNone(app.fling, "it stops by itself")

    def test_a_slow_lift_does_not_fling(self):
        import time as _time
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device"))
        from ui.app import App
        app = object.__new__(App)
        app.view, app.drag, app.fling = "songs", None, None
        with mock.patch.object(_time, "monotonic", lambda: 100.0):
            app.fling_start({"z": (0, 0, 800, 300, 50, lambda n: None), "track": [(99.95, 300.0), (100.0, 299.0)]})
        self.assertIsNone(app.fling)
