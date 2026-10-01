"""Regression tests for touch input on the Armonico Pi screen."""
import importlib.util
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_screen():
    spec = importlib.util.spec_from_file_location("armonico_screen_touch_test", ROOT / "device" / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HitRect:
    def __init__(self, x1, y1, x2, y2):
        self.bounds = (x1, y1, x2, y2)
        self.center = ((x1 + x2) // 2, (y1 + y2) // 2)

    def collidepoint(self, x, y):
        x1, y1, x2, y2 = self.bounds
        return x1 <= x <= x2 and y1 <= y <= y2


class FakePygame:
    @staticmethod
    def Rect(x, y, width, height):
        return HitRect(x, y, x + width, y + height)


class DeviceScreenTouch(unittest.TestCase):
    def setUp(self):
        self.mod = load_screen()
        self.screen = object.__new__(self.mod.Screen)
        self.screen.key_hold = False
        self.screen.summary_until = 0
        self.screen.last_tap_at = 0.0
        self.screen.last_tap_pos = None
        self.screen.bar_until = 0.0
        self.screen.view = "songs"
        self.screen.sel_song = 0
        self.screen.song_first = 0
        self.screen.pg = FakePygame()
        self.screen.bar_rects = lambda: []
        self.screen.lesson_on = lambda: False
        self.screen.songs = lambda: [
            {"name": "ode_to_joy", "total": 4},
            {"name": "fur_elise", "total": 8},
        ]
        self.screen.song_rows = lambda: [
            (0, HitRect(24, 64, 776, 122)),
            (1, HitRect(24, 130, 776, 188)),
        ]
        self.screen.ev_lock = threading.Lock()
        self.screen.events = []
        self.screen.lesson_request_pending = False
        self.screen.lesson_request_at = 0.0
        self.screen.lesson_request_song = ""
        self.screen.lesson_request_accepted = False
        self.screen.say = mock.Mock()
        self.screen.engine = mock.Mock()

    def test_touching_unselected_song_starts_it_on_first_tap(self):
        with mock.patch.object(self.mod.Screen, "start_lesson", autospec=True) as start:
            self.screen.tap(100, 150)
        self.assertEqual(self.screen.sel_song, 1)
        start.assert_called_once_with(self.screen, "fur_elise")

    def test_duplicate_synthetic_event_for_same_tap_is_ignored(self):
        with mock.patch.object(self.mod.Screen, "start_lesson", autospec=True) as start:
            self.screen.tap(100, 150)
            self.screen.tap(101, 151)  # near-identical mouse event from the same touch
        start.assert_called_once_with(self.screen, "fur_elise")

    def test_hardware_ok_button_uses_same_guarded_start_path(self):
        with mock.patch.object(self.mod.Screen, "start_lesson", autospec=True) as start:
            self.screen.button("ok", False)
        start.assert_called_once_with(self.screen, "ode_to_joy")

    def test_only_one_lesson_request_can_be_in_flight(self):
        self.screen.start_lesson("fur_elise")
        self.screen.start_lesson("fur_elise")
        self.screen.engine.post.assert_called_once()
        self.assertTrue(self.screen.lesson_request_pending)
        args, kwargs = self.screen.engine.post.call_args
        self.assertEqual(args[:2], ("/lesson", {"song": "fur_elise"}))
        self.assertIn("on_result", kwargs)


    def test_song_list_does_not_recenter_when_a_visible_row_is_tapped(self):
        self.screen.songs = lambda: [{"name": f"song_{i}", "total": 4} for i in range(10)]
        self.screen.song_rows = lambda: self.mod.Screen.song_rows(self.screen)
        first_rows = [i for i, _ in self.mod.Screen.song_rows(self.screen)]
        self.assertEqual(first_rows, [0, 1, 2, 3, 4])
        with mock.patch.object(self.mod.Screen, "start_lesson", autospec=True):
            self.mod.Screen.tap(self.screen, 100, 64 + 4 * 66 + 20)
        next_rows = [i for i, _ in self.mod.Screen.song_rows(self.screen)]
        self.assertEqual(self.screen.sel_song, 4)
        self.assertEqual(next_rows, [0, 1, 2, 3, 4])

    def test_song_list_scrolls_only_when_selection_reaches_below_viewport(self):
        self.screen.songs = lambda: [{"name": f"song_{i}", "total": 4} for i in range(10)]
        for _ in range(5):
            self.mod.Screen.move(self.screen, 1)
        self.assertEqual(self.screen.sel_song, 5)
        self.assertEqual(self.screen.song_first, 1)
        self.assertEqual([i for i, _ in self.mod.Screen.song_rows(self.screen)], [1, 2, 3, 4, 5])

    def test_touch_label_button_saves_profile_mode_during_lesson(self):
        self.screen.lesson_on = lambda: True
        self.mod.Screen.tap(self.screen, 218 + 104 + 8 + 12, 110)
        self.screen.engine.post.assert_called_once_with("/settings", {"labels": "abc"})
        self.screen.say.assert_called_once_with("Key labels: C D E", 1.4)

    def test_http_error_is_reported_instead_of_saying_lesson_started(self):
        self.screen.lesson_request_pending = True
        self.screen.lesson_request_song = "fur_elise"
        self.mod.Screen.handle(self.screen, "screen/lesson-start-result", "http:409")
        self.assertFalse(self.screen.lesson_request_pending)
        self.screen.say.assert_called_once()
        self.assertIn("already running", self.screen.say.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
