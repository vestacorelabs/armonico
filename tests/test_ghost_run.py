"""The ghost run: a full run races the previous full run of the same song.

The run itself is played with scripted keys instead of a keyboard: poll_key hands out the
notes at set times, so the race is real time, only short.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_game as game          # noqa: E402

NOTES = [60, 62, 64, 65, 67, 69]


class FakeEngine:
    """Just what run_with_band reads, with the keys played at the given gaps."""
    run_with_band = game.Engine.run_with_band

    def __init__(self, gap):
        self.band, self.ghost_song, self.mistakes = False, "ode", {}
        self.gap, self.next_at, self.queue = gap, None, list(NOTES)
        self.starts = [i * 0.5 for i in range(len(NOTES))]     # half a second apart in the file

    def tempo_scale(self):
        return 1.0

    def count_in(self):
        self.next_at = time.time() + self.gap

    def check_abort(self):
        pass

    def send_raw(self, data):
        pass

    def band_off(self, sounding):
        pass

    def run_scores(self, accuracy, hits, played, n):
        return {}

    def poll_key(self):
        if self.queue and time.time() >= self.next_at:
            self.next_at = time.time() + self.gap
            return self.queue.pop(0)
        return None


class GhostRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.multiple(game, profile_dir=lambda pid=None: self.tmp,
                                         ui=self.ui, TEST_END_SILENCE=1.0, HINT_SILENCE=5.0)
        self.patch.start()
        self.seen = {}

    def tearDown(self):
        self.patch.stop()

    def ui(self, **kw):
        if kw.get("ghost") is not None:
            self.seen.setdefault("ghosts", []).append(kw["ghost"])
        if "ghost_result" in kw:
            self.seen["result"] = kw["ghost_result"]

    def test_first_run_has_no_ghost_and_is_kept(self):
        FakeEngine(0.02).run_with_band(NOTES, band=False)
        self.assertNotIn("ghosts", self.seen)
        self.assertNotIn("result", self.seen)
        self.assertEqual(len(game.load_ghost("ode", False, len(NOTES))), len(NOTES))
        self.assertIsNone(game.load_ghost("ode", True, len(NOTES)))      # the band run is another race

    def test_faster_run_is_ahead_and_slower_is_behind(self):
        FakeEngine(0.08).run_with_band(NOTES, band=False)          # the ghost: slow
        FakeEngine(0.02).run_with_band(NOTES, band=False)          # faster than it
        self.assertGreater(self.seen["result"]["ahead"], 0)
        self.assertTrue(self.seen["ghosts"])                        # the ring moved during the run
        first = self.seen["result"]["id"]
        FakeEngine(0.08).run_with_band(NOTES, band=False)          # slower than the last one
        self.assertLess(self.seen["result"]["ahead"], 0)
        self.assertNotEqual(self.seen["result"]["id"], first)

    def test_shorter_run_races_the_start_of_a_longer_one(self):
        FakeEngine(0.02).run_with_band(NOTES, band=False)
        self.assertEqual(len(game.load_ghost("ode", False, 3)), 3)
        self.assertIsNone(game.load_ghost("ode", False, len(NOTES) + 1))

    def test_unfinished_run_is_not_kept(self):
        eng = FakeEngine(0.02)
        eng.queue = NOTES[:3]                                       # stops halfway, the run times out
        eng.run_with_band(NOTES, band=False)
        self.assertIsNone(game.load_ghost("ode", False, len(NOTES)))

    def test_the_run_reports_the_speed_it_was_played_at(self):
        eng = FakeEngine(0.25)                                      # twice as fast as the file
        eng.run_with_band(NOTES, band=False)
        self.assertEqual(eng.run_held, 150)                         # twice the file, held_speed caps at 150%
        eng = FakeEngine(1.0)
        eng.run_with_band(NOTES, band=False)
        self.assertAlmostEqual(eng.run_held, 50, delta=5)

    def test_reset_forgets_the_song(self):
        FakeEngine(0.02).run_with_band(NOTES, band=False)
        game.forget_ghost("ode")
        self.assertIsNone(game.load_ghost("ode", False, len(NOTES)))


def load_screen():
    import importlib.util
    spec = importlib.util.spec_from_file_location("armonico_screen_ghost_test", ROOT / "device" / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScreenGhostResult(unittest.TestCase):
    """The device says how the race ended, once, and only with the ghost run on."""

    def setUp(self):
        self.mod = load_screen()
        sc = self.screen = object.__new__(self.mod.Screen)
        sc.settings, sc.toast, sc.ghost_id = {}, ("", 0), self.mod.Screen.UNSEEN
        sc.engine = mock.Mock(ok=True, state={"ghost_result": {"id": 7, "ahead": 2}})
        sc.say = mock.Mock()

    def look(self, rid, ahead):
        self.screen.engine.state["ghost_result"] = {"id": rid, "ahead": ahead}
        self.mod.Screen.follow_ghost(self.screen)

    def test_result_from_before_the_screen_started_is_not_shown(self):
        self.mod.Screen.follow_ghost(self.screen)
        self.screen.say.assert_not_called()

    def test_ahead_behind_and_level(self):
        self.mod.Screen.follow_ghost(self.screen)
        self.look(8, 3)
        self.assertIn("3 keys ahead", self.screen.say.call_args.args[0])
        self.look(9, -1)
        self.assertIn("1 key behind", self.screen.say.call_args.args[0])
        self.look(10, 0)
        self.assertIn("level", self.screen.say.call_args.args[0])
        self.look(10, 0)                                           # the same result is said once
        self.assertEqual(self.screen.say.call_count, 3)

    def test_off_says_nothing(self):
        self.mod.Screen.follow_ghost(self.screen)
        self.screen.settings["ghost"] = False
        self.look(8, 3)
        self.screen.say.assert_not_called()


if __name__ == "__main__":
    unittest.main()
