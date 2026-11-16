"""The level chip on the device's Songs screen: All, then each level that has songs; only that list is filtered."""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

import screen as screen_mod   # noqa: E402

SONGS = [{"name": "a", "total": 2, "level": "beginner"}, {"name": "b", "total": 200, "level": "advanced"},
         {"name": "c", "total": 9, "level": "beginner"}, {"name": "d", "total": 0, "level": None}]


class LevelFilter(unittest.TestCase):
    def make(self, view):
        s = types.SimpleNamespace(engine=types.SimpleNamespace(overview={"songs": SONGS}), view=view, song_lv="")
        s.songs = types.MethodType(screen_mod.Screen.songs, s)
        return s

    def test_the_songs_screen_shows_only_the_chosen_level(self):
        s = self.make("songs")
        self.assertEqual([x["name"] for x in s.songs()], ["a", "b", "c"])
        s.song_lv = "beginner"
        self.assertEqual([x["name"] for x in s.songs()], ["a", "c"])
        s.song_lv = "advanced"
        self.assertEqual([x["name"] for x in s.songs()], ["b"])

    def test_other_screens_see_every_song(self):
        s = self.make("game")
        s.song_lv = "advanced"
        self.assertEqual(len(s.songs()), 3)


if __name__ == "__main__":
    unittest.main()
