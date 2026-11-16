"""The rest video on Armonico Pi: a played key stops it, and the end of a lesson, alarm or song brings it back."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_screen():
    spec = importlib.util.spec_from_file_location("armonico_screen_rest_test", ROOT / "device" / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RestResume(unittest.TestCase):
    def setUp(self):
        self.mod = load_screen()
        sc = self.sc = object.__new__(self.mod.Screen)
        sc.view, sc.alarm, sc.song, sc.key_hold, sc.was_busy = "rest", False, False, False, False
        sc.media_try = 0.0
        sc.lesson = False
        sc.lesson_on = lambda: sc.lesson
        sc.rest_mode = lambda: "video"
        sc.media = mock.Mock(playing=None)

    def look(self):
        self.sc.media.start.reset_mock()
        self.sc.media_try = 0.0
        self.mod.Screen.follow_music(self.sc)

    def test_a_key_alone_keeps_it_quiet(self):
        self.sc.key_hold = True
        self.look()
        self.sc.media.start.assert_not_called()

    def test_back_by_itself_after_a_song_alarm_or_lesson(self):
        for what in ("song", "alarm", "lesson"):
            self.sc.key_hold = True
            setattr(self.sc, what, True)
            self.look()
            self.sc.media.start.assert_not_called()
            setattr(self.sc, what, False)
            self.look()
            self.sc.media.start.assert_called_once_with("video")
            self.assertFalse(self.sc.key_hold)


if __name__ == "__main__":
    unittest.main()
