"""Armonico Pi: every song fitted to the 13 keys, and a part learned by ear (listen, then play it back).

    python3 -m unittest tests.test_device_ear
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_game as game          # noqa: E402

LO, HI = 60, 72


class Fit(unittest.TestCase):
    def test_a_tune_within_an_octave_lands_on_the_keys_whole(self):
        tune = [67, 69, 71, 72, 74, 76, 77, 79]          # G4 to G5: an octave, but not C to C
        shift, notes = game.fit_device(tune, None)
        self.assertTrue(all(LO <= n <= HI for n in notes), notes)
        self.assertEqual([b - a for a, b in zip(notes, notes[1:])], [b - a for a, b in zip(tune, tune[1:])])
        self.assertEqual(notes[0] - tune[0], shift)

    def test_a_part_past_the_keys_moves_as_a_whole_not_note_by_note(self):
        rhythm = [(0.4, 0.1)] * 3 + [(0.4, 1.5)] + [(0.4, 0.1)] * 3 + [(0.4, 1.5)]
        tune = [60, 62, 64, 65] + [76, 77, 79, 81]       # the second part sits above the keys
        _, notes = game.fit_device(tune, rhythm)
        a, b = notes[:4], notes[4:]
        self.assertEqual([y - x for x, y in zip(b, b[1:])], [1, 2, 2])   # its steps unchanged
        self.assertTrue(all(LO <= n <= HI for n in notes), notes)

    def test_the_songs_in_the_project_fit(self):
        for f in sorted((ROOT / "setup" / "music").glob("*.mid")):
            timed = game.timed_melody(str(f))
            pitches = [n for n, _, _ in timed]
            rhythm = [(d, g) for _, d, g in timed]
            _, notes = game.fit_device(pitches, rhythm)
            self.assertTrue(all(LO <= n <= HI for n in notes), f.name)   # every note is heard where its key is


class FakeEngine(game.Engine):
    def __init__(self, keys):
        self.keys, self.played, self.trace, self.outcomes = list(keys), [], None, []
        self.mistakes, self.piece, self.speed, self.rhythm = {}, None, 60, None

    def next_key(self, timeout):
        return self.keys.pop(0) if self.keys else None

    def play(self, notes, velocity=80, follow=False, start=None, ui_at=None, listen=False):
        self.played.append(list(notes))
        self.listened = getattr(self, "listened", []) + [listen]
        return []

    def pause(self, seconds):
        pass

    def drain(self):
        pass

    def count_in(self):
        pass


class Echo(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(game.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_clean(self):
        eng = FakeEngine([60, 64, 67])
        self.assertIs(eng.echo([60, 64, 67], None), True)

    def test_a_wrong_key_says_higher_and_plays_the_step_again_then_gold(self):
        seen = []
        real = game.ui
        with mock.patch.object(game, "ui", lambda **kw: (seen.append(kw), real(**kw))):
            eng = FakeEngine([60, 62, 63, 64, 67])
            self.assertEqual(eng.echo([60, 64, 67], None), "fixed")
        self.assertIn([60, 64], eng.played)                       # the note after the one before it
        self.assertIn({"msg": game.tr("Higher")}, seen)
        self.assertTrue(any(kw.get("show") == "keys" for kw in seen))   # the second miss: gold

    def test_a_tune_wider_than_the_keys_keeps_only_its_pickup_below(self):
        """Bella ciao and Hatikvah: a minor tune from the 5 below to the 3 above, 17 semitones."""
        hatikvah = [64, 66, 67, 69, 71, 71, 72, 71, 72, 76, 59, 64, 66, 67, 69, 71, 64, 76, 76, 74, 72, 71]
        rhythm = [(0.4, 0.1)] * len(hatikvah)
        _, notes = game.fit_device(hatikvah, rhythm)
        self.assertTrue(all(LO <= n <= HI for n in notes), notes)
        self.assertEqual(notes[10] % 12, (hatikvah[10] - 4) % 12,
                         "the pickup below the keys is played and heard on the key of its name")
        self.assertEqual([b - a for a, b in zip(notes[11:], notes[12:])], [b - a for a, b in zip(hatikvah[11:], hatikvah[12:])])

    def test_higher_or_lower_points_along_the_keys_for_a_note_below_them(self):
        with mock.patch.object(game, "IS_DEVICE", True):
            self.assertEqual(game.key_at(55), 67)       # G3 is played on the G key
            self.assertEqual(game.key_at(84), 72)       # C6 on the high C
            seen = []
            real = game.ui
            with mock.patch.object(game, "ui", lambda **kw: (seen.append(kw), real(**kw))):
                eng = FakeEngine([64, 67, 60])
                self.assertEqual(eng.echo([55, 60], None), "fixed")
        self.assertIn({"msg": game.tr("Higher")}, seen)  # E pressed, the G key is to its right

    def test_three_misses_on_a_note_fail_the_round(self):
        eng = FakeEngine([61, 61, 61])
        self.assertIs(eng.echo([60, 64], None), False)

    def test_the_device_rounds_are_by_ear_then_memory(self):
        eng = FakeEngine([60, 64, 60, 64])
        eng.steps = ("numbers", "keys", "memory")
        eng.auto_tempo = False
        with mock.patch.object(game, "IS_DEVICE", True):
            fails = eng.cycle([60, 64], None, "Part 1")
        self.assertEqual(fails, 0)
        self.assertEqual(eng.played[:2], [[60, 64], [60, 64]])     # listened to twice, nothing shown


class EarFixes(unittest.TestCase):
    """What went wrong by ear on the device (7.11.2026): "Lower", a lower key, and still wrong."""
    def setUp(self):
        p = mock.patch.object(game.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def seen(self):
        got = []
        real = game.ui
        return got, mock.patch.object(game, "ui", lambda **kw: (got.append(kw), real(**kw)))

    def test_the_first_note_pressed_later_is_a_miss_not_a_silent_start_over(self):
        # E D C D E: at note 3 (C) the E is pressed; then C is right and the part goes on from there
        eng = FakeEngine([64, 62, 64, 60, 62, 64])
        self.assertEqual(eng.echo([64, 62, 60, 62, 64], None), "fixed")
        self.assertEqual(eng.keys, [], "every key was used, none judged against the wrong note")

    def test_a_second_miss_still_says_which_way(self):
        got, p = self.seen()
        with p:
            eng = FakeEngine([67, 65, 62])              # D wanted: G (lower), then F (still lower), then D
            self.assertEqual(eng.echo([62], None), "fixed")
        msgs = [kw["msg"] for kw in got if "msg" in kw]
        self.assertIn(game.tr("Lower"), msgs)
        self.assertIn(game.tr("Lower · the gold key"), msgs)

    def test_by_ear_listens_to_the_end_and_a_key_meanwhile_is_no_answer(self):
        eng = FakeEngine([60, 64])
        with mock.patch.object(game, "IS_DEVICE", True):
            eng.by_ear([60, 64], None, "Part 1")
        self.assertEqual(eng.played[:2], [[60, 64], [60, 64]])
        self.assertEqual(eng.listened[:2], [True, True])


class Rounds(unittest.TestCase):
    """A part from the easiest round to memory, picked by itself (07.11.2026: by ear with no help at all, at once, was hard)."""
    def setUp(self):
        p = mock.patch.object(game.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def eng(self, keys, **ear):
        e = FakeEngine(keys)
        e.ear = dict(game.EAR_DEFAULTS, **ear)
        e.auto_tempo, e.steps = False, None
        return e

    def test_auto_picks_by_how_new_the_part_is(self):
        e = self.eng([])
        self.assertEqual(e.ear_rounds([60, 62, 64, 65, 67], True), ["lead", "build", "ear", "memory"])
        self.assertEqual(e.ear_rounds([60, 62, 64], True), ["lead", "ear", "memory"])     # short: no note by note
        self.assertEqual(e.ear_rounds([60, 62, 64, 65, 67], False), ["ear", "memory"])    # known before
        self.assertEqual(self.eng([], ear_auto=False).ear_rounds([60, 62], False), ["demo", "lead", "build", "ear", "memory"])
        self.assertEqual(self.eng([], ear_lead=False, ear_auto=False).ear_rounds([60], True), ["demo", "build", "ear", "memory"])

    def test_auto_starts_a_new_part_by_the_level(self):
        seg = [60, 62, 64, 65, 67]
        for level, fresh, known in (("beginner", ["demo", "lead", "build", "ear", "memory"], ["lead", "ear", "memory"]),
                                    ("intermediate", ["lead", "build", "ear", "memory"], ["ear", "memory"]),
                                    ("advanced", ["ear", "memory"], ["memory"])):
            e = self.eng([])
            e.level = level
            self.assertEqual(e.ear_rounds(seg, True), fresh, level)
            self.assertEqual(e.ear_rounds(seg, False), known, level)

    def test_a_beginner_with_the_demo_off_starts_from_the_gold_key(self):
        e = self.eng([], ear_demo=False)
        e.level = "beginner"
        self.assertEqual(e.ear_rounds([60, 62, 64], True), ["lead", "ear", "memory"])

    def test_an_advanced_start_that_is_off_falls_back_to_the_easiest_on(self):
        e = self.eng([], ear_ear=False)
        e.level = "advanced"
        self.assertEqual(e.ear_rounds([60, 62, 64], True), ["lead", "memory"])

    def test_the_demo_round_plays_twice_then_the_gold_key_leads(self):
        seg = [60, 64, 67]
        e = self.eng(seg)
        with mock.patch.object(game, "IS_DEVICE", True):
            e.demo(seg, 0, "t")
        self.assertEqual(e.played, [seg, seg])
        self.assertEqual(e.listened, [True, True])

    def test_a_new_short_part_goes_lead_then_ear_then_memory(self):
        seg = [60, 64, 67]
        e = self.eng(seg * 3)
        with mock.patch.object(game, "IS_DEVICE", True):
            self.assertEqual(e.cycle(seg, None, "Part 1", new=True), 0)
        self.assertEqual(e.keys, [])

    def test_note_by_note_grows_to_the_whole_part(self):
        self.assertEqual(game.Engine.build_sizes(5), [1, 2, 3, 4, 5])
        self.assertEqual(game.Engine.build_sizes(9), [1, 2, 3, 4, 6, 8, 9])
        seg = [60, 62, 64, 65, 67]
        keys = [n for k in game.Engine.build_sizes(5) for n in seg[:k]]
        e = self.eng(keys)
        e.build(seg, None, "Part 1")
        self.assertEqual(e.played, [seg[:k] for k in (1, 2, 3, 4, 5)])
        self.assertEqual(e.keys, [])

    def test_the_settings_keep_one_round_at_least(self):
        out = game.lesson_prefs({"ear_demo": False, "ear_lead": False, "ear_build": False, "ear_ear": False,
                                 "ear_memory": False}, {})
        self.assertTrue(out["ear_memory"])
        out = game.lesson_prefs({"ear_gold": False, "ear_auto": "yes"}, {})
        self.assertFalse(out["ear_gold"])
        self.assertNotIn("ear_auto", out, "only true or false is taken")

    def test_without_the_gold_key_a_second_miss_is_another_hint(self):
        seen = []
        real = game.ui
        with mock.patch.object(game, "ui", lambda **kw: (seen.append(kw), real(**kw))):
            e = self.eng([67, 65, 62], ear_gold=False)
            self.assertEqual(e.echo([62], None), "fixed")
        self.assertFalse(any(kw.get("show") == "keys" for kw in seen))
        self.assertEqual([kw["msg"] for kw in seen if kw.get("msg") in (game.tr("Lower"),)], [game.tr("Lower")] * 2)

    def test_the_key_leads_lights_every_note(self):
        seen = []
        real = game.ui
        with mock.patch.object(game, "ui", lambda **kw: (seen.append(kw), real(**kw))):
            e = self.eng([60, 61, 64])
            e.lead([60, 64], None, "Part 1")
        self.assertTrue(all(kw.get("show") == "keys" for kw in seen if kw.get("mode") == "wait" and "expect" in kw))


if __name__ == "__main__":
    unittest.main()
