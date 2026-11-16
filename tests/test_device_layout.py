"""Layout by the screen's physical size, scrolling, cv.fit direction and the numeric keypad (device/ui)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import test_device_ui as T

pygame = T.pygame
ROOT = T.ROOT
sys.path.insert(0, str(ROOT / "device"))
from ui import screensize  # noqa: E402


class Sizes(unittest.TestCase):
    def test_classes_by_diagonal(self):
        for inch, cls in ((3.2, 3.5), (3.5, 3.5), (4.3, 4.3), (5.0, 5.0), (5.5, 5.0), (7.0, 7.0), (10.1, 10.0), (13, 10.0)):
            self.assertEqual(screensize.classify(inch), cls)

    def test_env_overrides_and_resolution_is_the_fallback(self):
        self.assertEqual(screensize.detect(800, 480, env={"ARMONICO_SCREEN_INCH": "7"}, root="/nonexistent"), 7.0)
        self.assertEqual(screensize.detect(800, 480, env={}, root="/nonexistent"), 5.0)
        self.assertEqual(screensize.detect(480, 320, env={}, root="/nonexistent"), 3.5)
        self.assertEqual(screensize.detect(1280, 800, env={}, root="/nonexistent"), 10.0)

    def test_physical_size_beats_resolution(self):
        import tempfile
        d = Path(tempfile.mkdtemp()) / "card0-DSI-1"
        d.mkdir()
        (d / "status").write_text("connected\n")
        edid = bytearray(128)
        edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
        edid[21], edid[22] = 15, 9                      # 150 x 90 mm: a 7" panel, whatever its pixels
        (d / "edid").write_bytes(bytes(edid))
        self.assertEqual(screensize.detect(800, 480, env={}, root=str(d.parent)), 7.0)

    def _tree(self, **conn):
        """A fake /sys/class/drm: name -> diagonal in mm of its (connected) screen."""
        import tempfile
        root = Path(tempfile.mkdtemp())
        for name, (w, h) in conn.items():
            d = root / name
            d.mkdir()
            (d / "status").write_text("connected\n")
            e = bytearray(128)
            e[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
            e[21], e[22] = w // 10, h // 10
            (d / "edid").write_bytes(bytes(e))
        return str(root)

    def test_only_the_own_panel_counts(self):
        both = self._tree(**{"card1-DSI-1": (110, 66), "card1-HDMI-A-1": (600, 340)})     # 5" panel + 27" monitor
        self.assertEqual(screensize.detect(1920, 1080, env={}, root=both), 5.0)
        hdmi = self._tree(**{"card1-HDMI-A-1": (600, 340)})
        self.assertEqual(screensize.detect(800, 480, env={}, root=hdmi), 5.0)             # resolution rule
        self.assertEqual(screensize.detect(1920, 1080, env={}, root=hdmi), 10.0)
        self.assertEqual(screensize.detect(800, 480, env={"ARMONICO_SCREEN_INCH": "3.5"}, root=both), 3.5)

    def test_cards_per_page(self):
        for cls, per in ((3.5, 4), (4.3, 6), (5.0, 8), (7.0, 12), (10.0, 15)):
            cols, rows, _ = screensize.layout(cls)
            self.assertEqual(cols * rows, per)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Scrolling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, inch, lang="en", zoom=100):
        os.environ["ARMONICO_SCREEN_INCH"] = str(inch)
        try:
            return T.make_app(self.mod, (800, 480), lang, {"zoom": zoom})
        finally:
            os.environ.pop("ARMONICO_SCREEN_INCH")

    def test_nothing_is_drawn_under_the_bar_and_the_window_follows_the_selection(self):
        for inch in (3.5, 4.3, 5, 7, 10):
            for zoom in (100, 150):
                app = self.app(inch, zoom=zoom)
                cols, rows, w, h, gap = app.grid_geom()
                self.assertLessEqual(58 + rows * h + (rows - 1) * gap, 480 - 58, (inch, zoom))
                app.open_page("net")
                n = len(app.settings_cells()["net"])
                seen = set()
                for _ in range(n + 2):
                    app.draw()
                    cells = [(x, y, w2, h2) for x, y, w2, h2, fn in app.hits if y < 480 - 58 and h2 > 40]
                    self.assertTrue(all(y + h2 <= 480 - 58 for _, y, _, h2 in cells), (inch, zoom))
                    self.assertLessEqual(len(cells), cols * rows)
                    seen.add(app.sel)
                    app.press("down")
                self.assertGreater(len(seen), 3)

    def test_selection_row_always_inside_the_window(self):
        app = self.app(5)
        app.open_page("net")
        for _ in range(30):
            app.draw()
            first = app.scroll["settings:net"]
            self.assertLessEqual(first, app.sel // 2)
            self.assertLessEqual(app.sel // 2, first + 3)
            app.press("down")

    def test_drag_scrolls_and_a_short_touch_presses(self):
        app = self.app(4.3)                             # 6 cards a page: the 8 of the network page scroll
        app.open_page("net")
        app.draw()
        s = app.cv.scale
        app.pointer_down(300 * s, 300 * s)
        app.pointer_up(300 * s, 120 * s)               # dragged up: later rows
        self.assertGreater(app.scroll["settings:net"], 0)

    def test_fit_keeps_the_start_and_the_end_on_request(self):
        app = self.app(5)
        cv = app.cv
        s = "A very long name of a song that cannot fit"
        out = cv.fit(s, 120, 15)
        self.assertTrue(out.startswith("A very") and out.endswith("…"))
        out = cv.fit("/home/user/some/long/folder/file.mid", 120, 15, end=True)
        self.assertTrue(out.startswith("…") and out.endswith("file.mid"))
        self.assertEqual(cv.fit("short", 400, 15), "short")


@unittest.skipIf(pygame is None, "pygame is not installed")
class Keypad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_pin_field_is_a_phone_keypad(self):
        app = T.make_app(self.mod, (800, 480), "he")
        got = []
        app.edit_field("PIN", lambda: "", got.append, secret=True, keep=True, digits=True)
        app.draw()
        self.assertEqual(app.PAD_KEYS, ("1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓"))
        for k in ("1", "2", "3", "4"):
            app.pad_press(k)
        app.pad_press("⌫")
        app.pad_press("5")
        app.type_text("x9z")                              # a physical keyboard: only digits get in
        self.assertEqual(app.edit["val"], "12359")
        app.pad_press("✓")
        self.assertEqual(got, ["12359"])

    def test_buttons_work_on_the_keypad(self):
        app = T.make_app(self.mod, (800, 480), "en")
        app.edit_field("Port", lambda: "", lambda v: None, digits=True)
        app.draw()
        app.pad_move(1)                                   # ▼ to the 2
        app.draw()
        app.bar_items[3][2]()                             # ✓ presses it
        self.assertEqual(app.edit["val"], "2")
        app.bar_items[0][2]()                             # ☰ cancels
        self.assertIsNone(app.edit)

    def test_the_length_is_capped(self):
        app = T.make_app(self.mod, (800, 480), "en")
        app.edit_field("PIN", lambda: "", lambda v: None, secret=True, digits=True)
        for _ in range(12):
            app.pad_press("7")
        self.assertEqual(len(app.edit["val"]), 8)

    def test_text_field_is_not_a_keypad(self):
        app = T.make_app(self.mod, (800, 480), "en")
        app.edit_field("Name", lambda: "", lambda v: None)
        app.draw()
        self.assertFalse(app.edit["digits"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "pygame is not installed")
class TouchSafety(unittest.TestCase):
    """A drag in a scrolling area only scrolls; a short deliberate tap still presses. The screen is really redrawn
    between touch-down and touch-up, so the touch areas are new objects, as in the running app."""

    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def make(self, inch=4.3):
        os.environ["ARMONICO_SCREEN_INCH"] = str(inch)
        try:
            a = T.make_app(self.mod, (800, 480), "en")
        finally:
            os.environ.pop("ARMONICO_SCREEN_INCH")
        a.open_page("look")                          # 8 cards, all pressable: it scrolls on every class up to 4.3"
        a.draw()
        self.app, self.s = a, a.cv.scale
        self.pressed = []
        return a

    def redraw(self):
        """The real draw, then every action replaced by a counter that is new each frame."""
        a = self.app
        a.draw()
        a.hits = [(x, y, w, h, (lambda i=i: self.pressed.append(i))) for i, (x, y, w, h, _) in enumerate(a.hits)]

    def cards(self):
        return [(x, y, w, h) for x, y, w, h, _ in self.app.hits if h > 40 and y < 422]

    def down(self, x, y, who="mouse"):
        self.app.pointer_id = who
        self.app.pointer_down(x * self.s, y * self.s)

    def move(self, x, y, who="mouse"):
        self.app.pointer_id = who
        self.app.drag_track(x * self.s, y * self.s)

    def up(self, x, y, who="mouse"):
        self.app.pointer_id = who
        self.app.pointer_up(x * self.s, y * self.s)

    def centre(self, i):
        x, y, w, h = self.cards()[i]
        return x + w / 2, y + h / 2

    def each(self):
        for inch in (4.3, 3.5):
            self.make(inch)
            self.redraw()
            yield inch

    def test_a_plain_tap_presses_after_a_redraw(self):
        for inch in self.each():
            x, y = self.centre(1)
            self.down(x, y)
            self.redraw()
            self.up(x, y)
            self.assertEqual(len(self.pressed), 1, inch)

    def test_the_real_action_fires_on_the_real_page(self):
        for inch in (4.3, 3.5):
            a = self.make(inch)
            self.assertEqual(a.sel, 0)
            cards = [(x, y, w, h) for x, y, w, h, _ in a.hits if h > 40 and y < 422]
            x, y = cards[1][0] + cards[1][2] / 2, cards[1][1] + cards[1][3] / 2
            a.pointer_id = "mouse"
            a.pointer_down(x * self.s, y * self.s)
            a.draw()                                     # real frames in between: new touch areas
            a.draw()
            a.pointer_up(x * self.s, y * self.s)
            self.assertEqual(a.sel, 1, inch)             # the card's own action ran (it selects and acts)
            a.sel = 0
            before = dict(a.saved)
            a.pointer_down(x * self.s, y * self.s)
            a.drag_track(x * self.s, (y - 50) * self.s)
            a.draw()
            a.pointer_up(x * self.s, (y - 50) * self.s)
            self.assertEqual(a.saved, before, inch)      # a drag scrolls (moving the selection) and runs no card's action

    def test_a_press_then_a_drag_then_redraw_fires_nothing(self):
        for inch in self.each():
            x, y = self.centre(0)
            self.down(x, y)
            self.move(x, y - 60)
            self.redraw()
            self.up(x, y - 120)
            self.assertEqual(self.pressed, [], inch)

    def test_drag_beyond_threshold_from_a_card(self):
        for inch in self.each():
            x, y = self.centre(-1)
            self.down(x, y)
            self.move(x, y + 16)
            self.redraw()
            self.up(x, y + 16)
            self.assertEqual(self.pressed, [], inch)

    def test_release_over_another_card(self):
        for inch in self.each():
            x, y = self.centre(0)
            x2, y2 = self.centre(1)
            self.down(x, y)
            self.redraw()
            self.up(x2, y2)
            self.assertEqual(self.pressed, [], inch)

    def test_jitter_tap_presses(self):
        for inch in self.each():
            x, y = self.centre(1)
            self.down(x, y)
            self.move(x + 3, y - 4)
            self.redraw()
            self.up(x + 2, y - 3)
            self.assertEqual(len(self.pressed), 1, inch)

    def test_away_and_back_does_not_press(self):
        for inch in self.each():
            x, y = self.centre(1)
            self.down(x, y)
            self.move(x, y + 80)
            self.move(x, y)
            self.redraw()
            self.up(x, y)
            self.assertEqual(self.pressed, [], inch)

    def test_second_finger_presses_nothing(self):
        for inch in self.each():
            x, y = self.centre(0)
            x2, y2 = self.centre(-1)
            self.down(x, y, "f1")
            self.down(x2, y2, "f2")
            self.redraw()
            self.up(x2, y2, "f2")
            self.up(x, y, "f1")
            self.assertEqual(self.pressed, [], inch)

    def test_drag_ending_on_the_bar_presses_no_button(self):
        for inch in self.each():
            x, y = self.centre(0)
            self.down(x, y)
            self.move(200, 440)
            self.redraw()
            self.up(100, 440)
            self.assertEqual(self.pressed, [], inch)


@unittest.skipIf(pygame is None, "pygame is not installed")
class AboutDrag(unittest.TestCase):
    def test_a_long_drag_scrolls_the_page_and_never_turns_it(self):
        app = T.make_app(T.load_screen())
        app.about_page, app.about_lim, app.about_scroll = 0, 3, 0
        app.about_drag(5)                                  # a drag of 5 lines on a page 3 lines longer than its box
        self.assertEqual((app.about_page, app.about_scroll), (0, 3))
        app.about_drag(-9)
        self.assertEqual((app.about_page, app.about_scroll), (0, 0), "an upward drag at the top stays on the page")
        app.about_turn(1)                                  # ▼ still scrolls first, then turns
        self.assertEqual((app.about_page, app.about_scroll), (0, 1))
