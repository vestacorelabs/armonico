"""The device's game (device/ui/views_game.py) and its song (game_song in lessons/piano_game.py).

The notes fall onto the keys and the song waits on each one until it is played: the clock stops at
a note that landed, a wrong key counts against the accuracy and breaks the streak but ends nothing,
and the song played is a started song that fades, never one not started, or the easiest with none started.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None

SONG = {"ok": True, "name": "ode_to_joy", "why": "fading", "recall": 0.6,
        "notes": [[64, 0.5], [64, 0.5], [65, 0.5], [67, 0.5], [60, 0.5], [72, 0.5]]}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@unittest.skipIf(pygame is None, "pygame is not installed")
class GameOnTheKeys(unittest.TestCase):
    """The song runs at its tempo: a note is played as it touches the line, early or late says which way,
    a miss or a wrong key costs a life, 10 in a row win one back, no lives left is out."""
    def setUp(self):
        mod = load_screen()
        self.app = make_app(mod, (800, 480), "he")
        self.app.clock = Clock()
        self.app.t = self.app.clock
        self.app.screen.pub = mock.Mock()
        self.app.go("game")
        self.app.game_loading, self.app.game_song = False, dict(SONG)

    def run_for(self, secs, step=0.02):
        end = self.app.clock.now + secs
        while self.app.clock.now < end:
            self.app.clock.now += step
            self.app.game_tick(self.app.clock.now)

    def to_note(self, plus=0.0):
        """The clock to the next note's line, plus some (late) or minus (early)."""
        g = self.app.game
        self.run_for(g["obs"][g["i"]]["at"] - g["clock"] + plus)

    def test_any_key_starts_and_the_song_does_not_wait(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        self.assertIsNotNone(a.game)
        first = a.game["obs"][0]
        self.assertEqual(first["key"], 4)                  # E over the song's lowest C
        self.run_for(first["at"] + 0.5)                    # past the line, unplayed
        self.assertEqual(a.game["i"], 1)
        self.assertEqual(a.game["lives"], 4)
        self.assertEqual(a.game["words"][-1][1], a.tr("g.miss"))

    def test_on_the_line_early_and_late(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        self.to_note(-0.02)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["i"], a.game["words"][-1][1], a.game["words"][-1][3]), (1, a.tr("g.perfect"), ""))
        self.to_note(-0.15)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["words"][-1][1], a.game["words"][-1][3]), (a.tr("g.good"), a.tr("g.early")))
        self.to_note(0.25)
        a.game_key(5, a.clock.now)
        self.assertEqual((a.game["words"][-1][1], a.game["words"][-1][3]), (a.tr("g.almost"), a.tr("g.late")))
        self.assertEqual(a.game["lives"], 5)

    def test_far_too_soon_is_let_be_and_a_wrong_key_costs_a_life(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        self.to_note(-1.0)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["i"], a.game["lives"]), (0, 5))
        a.game_key(2, a.clock.now)
        self.assertEqual((a.game["i"], a.game["lives"], a.game["combo"]), (0, 4, 0))

    def test_out_when_the_lives_run_out_and_any_key_starts_again(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        for _ in range(5):
            a.game_key(11, a.clock.now)
        self.assertTrue(a.game["out"])
        self.assertIsNotNone(a.game["done"])
        a.clock.now += 1.5
        a.game_key(0, a.clock.now)                         # any key after: the same song again
        self.assertFalse(a.game["out"])
        self.assertEqual(a.game["lives"], 5)

    def test_ten_in_a_row_win_a_life_back(self):
        a = self.app
        a.game_song = dict(SONG, notes=SONG["notes"] * 3)
        a.game_start(a.clock.now, song=a.game_song)
        a.game_key(11, a.clock.now)                        # a wrong key: 4 left
        for _ in range(10):
            self.to_note()
            o = a.game["obs"][a.game["i"]]
            a.game_key(o["key"], a.clock.now)
        self.assertEqual(a.game["lives"], 5)

    def test_tempo_steps_and_the_whole_song_and_the_high_c(self):
        a = self.app
        a.game_set_tempo(1)
        self.assertEqual(a.game_tempo, 0.9)
        a.game_start(a.clock.now, song=a.game_song)
        self.assertEqual(a.game["obs"][-1]["key"], 12)     # the C an octave up is key 1'
        for o in a.game["obs"]:
            self.to_note()
            a.game_key(o["key"], a.clock.now)
        self.assertIsNotNone(a.game["done"])
        self.assertFalse(a.game["out"])
        self.assertEqual(a.game["best"], len(SONG["notes"]))

    def test_a_key_is_judged_when_it_was_pressed_not_when_the_frame_sees_it(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        self.to_note(0.15)                              # the frame comes 0.15 s after the line
        a.game_key(4, a.clock.now - 0.15)               # but the key went down on the line
        self.assertEqual(a.game["i"], 1)
        self.assertEqual(a.game["words"][-1][1], a.tr("g.perfect"))

    def test_a_note_on_the_broker_carries_its_time_and_a_touch_is_not_counted_twice(self):
        a = self.app
        a.t0 = 0.0
        a.game_start(a.clock.now, song=dict(SONG))
        self.to_note(0.12)
        a.game_note(f"64 {a.clock.now - 0.12:.4f}")       # E, stamped as it came, on the line
        self.assertEqual(a.game["i"], 1)
        self.to_note()
        a.game_touched(4)                               # touched on the screen: judged at once
        a.game_note(f"64 {a.clock.now:.4f}")              # its copy from the engine: dropped
        self.assertEqual(a.game["i"], 2)
        self.assertEqual(a.game["lives"], 5)

    def test_the_list_puts_the_pick_first_and_plays_the_one_marked(self):
        a = self.app
        a.screen.songs = lambda: [{"name": "twinkle", "total": 3}, {"name": "ode_to_joy", "total": 4}, {"name": "jingle", "total": 2}]
        self.assertEqual(a.game_list(), ["ode_to_joy", "twinkle", "jingle"])
        a.game_pick_move(1)
        asked = []
        with mock.patch.object(a, "game_load", side_effect=lambda other=False, song="": asked.append(song)):
            a.game_key(7, a.clock.now)                     # any key on the list: the song marked
        self.assertEqual(asked, ["twinkle"])
        a.game_ready = dict(SONG, name="twinkle")
        self.run_for(0.1)
        self.assertEqual(a.game["name"], "twinkle")
        a.game_back()
        self.assertIsNone(a.game)
        self.assertEqual(a.game_list()[a.game_pick["sel"]], "twinkle")
        a.draw()

    def test_it_draws_in_every_state(self):
        a = self.app
        for song in (None, {"ok": False}, dict(SONG)):
            a.game, a.game_song = None, song
            a.game_loading = song is None
            a.draw()
        a.game_start(a.clock.now, song=a.game_song)
        self.run_for(2.0)
        a.draw()
        self.run_for(10.0)
        a.draw()
        a.game["out"] = True
        a.draw()


class TheGamesSong(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lessons"))
        import piano_game
        cls.pg = piano_game

    def call(self, songs, progress, recall, skip="", song=""):
        pg = self.pg
        notes = [60, 62, 64]
        with mock.patch.object(pg, "song_list", return_value=songs), \
                mock.patch.object(pg, "load_progress", return_value=progress), \
                mock.patch.object(pg.learner, "recall_now", side_effect=lambda p, now: recall[p["id"]]), \
                mock.patch.object(pg, "load_song", side_effect=lambda n: (n, notes, [(0.4, 0.1)] * 3, None, "", 0)):
            return pg.game_song(skip=skip, song=song)

    def test_a_fading_song_is_chosen_and_one_not_started_never(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "b", "total": 4, "level": "advanced"},
                 {"name": "c", "total": 2, "level": "beginner"}]
        progress = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}},
                    self.pg.progress_key("b"): {"parts": {"1": {"id": "b"}}}}
        got = self.call(songs, progress, {"a": 0.95, "b": 0.4})
        self.assertEqual((got["name"], got["why"]), ("b", "fading"))
        self.assertEqual(got["notes"][0], [60, 0.5])
        got = self.call(songs, progress, {"a": 0.95, "b": 0.9})
        self.assertEqual((got["name"], got["why"]), ("b", "least"))

    def test_nothing_started_plays_the_easiest(self):
        songs = [{"name": "hard", "total": 3, "level": "advanced"}, {"name": "long", "total": 9, "level": "beginner"},
                 {"name": "easy", "total": 4, "level": "beginner"}]
        got = self.call(songs, {}, {})
        self.assertEqual((got["name"], got["why"]), ("easy", "easiest"))
        self.assertEqual(self.call([], {}, {}), {"ok": False})

    def test_a_song_picked_on_the_screen_plays_whatever_the_rules(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "c", "total": 2, "level": "beginner"}]
        got = self.call(songs, {}, {}, song="a")
        self.assertEqual((got["name"], got["why"]), ("a", "chosen"))
        self.assertEqual(self.call(songs, {}, {}, song="nope"), {"ok": False})

    def test_other_song_passes_over_the_one_on_screen(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "b", "total": 4, "level": "advanced"},
                 {"name": "c", "total": 2, "level": "beginner"}]
        progress = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}},
                    self.pg.progress_key("b"): {"parts": {"1": {"id": "b"}}}}
        got = self.call(songs, progress, {"a": 0.95, "b": 0.9}, skip="b")
        self.assertEqual((got["name"], got["why"], got["only"]), ("a", "least", False), "the next started song")
        got = self.call(songs, progress, {"a": 0.95, "b": 0.4}, skip="b")
        self.assertEqual((got["name"], got["why"]), ("a", "least"), "with the only fading song on screen")
        one = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}}}
        got = self.call(songs, one, {"a": 0.95}, skip="a")
        self.assertEqual((got["name"], got["only"]), ("a", True), "a song never started is never the other one")
        got = self.call(songs, {}, {}, skip="c")
        self.assertEqual((got["name"], got["why"], got["only"]), ("a", "easiest", False), "none started: the next easiest")


if __name__ == "__main__":
    unittest.main()
