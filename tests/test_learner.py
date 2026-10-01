"""piano_learner.py: memory per part, skills, and pace, each moving a little with every lesson."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_learner as L          # noqa: E402
import piano_game as game          # noqa: E402

DAY = L.DAY


class Memory(unittest.TestCase):
    def part(self, weakness=0.0):
        p = {}
        L.start_memory(p, weakness, 0)
        return p

    def test_a_clean_showing_after_a_day_grows_more_than_the_same_day(self):
        a, b = self.part(), self.part()
        L.update_memory(a, True, 0)                  # the same moment: nothing had time to fade
        L.update_memory(b, True, DAY)
        self.assertAlmostEqual(a["S"], L.S_START, places=2)
        self.assertGreater(b["S"], 2.5)

    def test_a_slip_shrinks_and_makes_it_harder(self):
        p = self.part()
        L.update_memory(p, True, DAY)
        grown, d = p["S"], p["D"]
        L.update_memory(p, False, 3 * DAY)
        self.assertLess(p["S"], grown)
        self.assertGreater(p["D"], d)

    def test_weak_skills_start_a_part_harder(self):
        self.assertGreater(self.part(1.0)["D"], self.part(0.0)["D"])

    def test_one_bad_day_does_not_undo_a_good_week(self):
        p = self.part()
        for day in (1, 3, 7, 14):
            L.update_memory(p, True, day * DAY)
        strong = p["S"]
        L.update_memory(p, False, 15 * DAY)
        self.assertGreater(p["S"], 1.0)              # down, but nowhere near a part never learned
        self.assertGreater(strong, 20)

    def test_drills_take_the_hardest_part_first(self):
        parts = {"0": {"S": 5, "D": 3}, "1": {"S": 1, "D": 8}, "2": {"S": 0.5, "D": 8}}
        self.assertEqual(L.drill_order([0, 1, 2], parts, 0), [2, 1, 0])


class NewParts(unittest.TestCase):
    def test_fewer_while_parts_fade_and_more_when_all_hold(self):
        fading = {str(k): {"S": 0.5, "D": 6, "mem_at": 0} for k in range(3)}
        self.assertEqual(L.new_count(2, fading, [0, 1, 2], 5 * DAY), (1, "fading"))
        solid = {str(k): {"S": 30, "D": 3, "mem_at": 0} for k in range(3)}
        self.assertEqual(L.new_count(2, solid, [0, 1, 2], DAY), (3, "solid"))
        self.assertEqual(L.new_count(1, fading, [0, 1, 2], 5 * DAY), (1, "fading"))   # never below 1

    def test_parts_learned_before_the_model_change_nothing(self):
        old = {"0": {"streak": 2}, "1": {"streak": 3}}
        self.assertEqual(L.new_count(2, old, [0, 1], DAY), (2, None))


class Skills(unittest.TestCase):
    def test_what_a_part_asks_for(self):
        self.assertEqual(L.part_skills([60, 62, 64]), ["memory"])
        got = L.part_skills([60, 61, 72] + [60] * 8, [(0.2, 0)] * 10 + [(0.9, 0)])
        self.assertEqual(set(got), {"memory", "black", "leap", "long", "rhythm"})

    def test_skills_move_a_little_each_time(self):
        sk = {}
        L.update_skills(sk, ["black"], True)
        once = sk["black"]
        self.assertGreater(once, L.BKT_L0)
        self.assertLess(once, 0.75)                  # one good part is not mastery
        L.update_skills(sk, ["black"], False)
        self.assertLess(sk["black"], once)
        self.assertGreater(sk["black"], 0.1)         # and one slip is not failure

    def test_weakest(self):
        sk = {"black": 0.2, "leap": 0.8, "long": 0.6, "rhythm": 0.9, "memory": 0.7}
        self.assertEqual(L.weakest(sk)["skill"], "black")
        self.assertIsNone(L.weakest({}))


class Pace(unittest.TestCase):
    def test_held_speed_from_the_right_keys(self):
        starts = [0, 0.5, 1.0, 1.5, 2.0]                     # half a second apart in the file
        hits = [(i, i * 1.0) for i in range(5)]               # played a second apart: half speed
        self.assertAlmostEqual(L.held_speed(hits, starts), 50.0)
        self.assertIsNone(L.held_speed(hits[:2], starts))

    def test_the_next_run_follows_the_trend(self):
        pace = None
        for held in (50, 55, 60, 65):
            pace = L.update_pace(pace, held)
        self.assertGreater(L.next_speed(pace), pace["level"])     # heading up
        self.assertIsNone(L.next_speed(L.update_pace(None, 50)))  # one run is not a trend yet


class Grades(unittest.TestCase):
    def test_a_grade_from_the_help_the_wrong_keys_and_the_hesitations(self):
        self.assertEqual(L.grade("memory"), L.GOOD)
        self.assertEqual(L.grade("memory", even=True), L.EASY)
        self.assertEqual(L.grade("memory", hesitant=1, even=True), L.HARD)
        self.assertEqual(L.grade("memory", wrong=1), L.GOOD)       # a finger slipped, the note was there
        self.assertEqual(L.grade("memory", wrong=1, even=True), L.GOOD)
        self.assertEqual(L.grade("memory", wrong=2), L.HARD)
        self.assertEqual(L.grade("memory", skipped=1), L.AGAIN)     # a note that did not come
        self.assertEqual(L.recall_grade("memory", wrong=1), (L.GOOD, 1))
        self.assertEqual(L.grade("memory", named=True), L.AGAIN)
        self.assertEqual(L.grade("keys"), L.AGAIN)
        self.assertEqual(L.grade("numbers"), L.AGAIN)

    def test_each_grade_grows_the_part_more_than_the_one_below_it(self):
        got = []
        for g in (L.AGAIN, L.HARD, L.GOOD, L.EASY):
            p = {}
            L.start_memory(p, 0.0, 0)
            L.update_memory(p, g, 3 * DAY)
            got.append(p["S"])
        self.assertEqual(got, sorted(got))
        self.assertEqual(len(set(got)), 4)

    def test_true_and_false_from_before_the_grades_are_good_and_again(self):
        self.assertEqual(L.as_grade(True), L.GOOD)
        self.assertEqual(L.as_grade(False), L.AGAIN)
        a, b = {}, {}
        for p, g in ((a, True), (b, L.GOOD)):
            L.start_memory(p, 0.0, 0)
            L.update_memory(p, g, 2 * DAY)
        self.assertEqual(a, b)

    def test_stability_is_the_days_until_ninety_percent(self):
        self.assertAlmostEqual(L.recall(7, 7), L.TARGET, places=3)


def history(grow, parts=60, seed=3):
    """A made-up player whose memory follows FSRS with this growth weight."""
    import random
    rnd, rows = random.Random(seed), []
    w = {"grow": grow, "fall": L.W[11]}
    for k in range(parts):
        s, d, t = L.S_START, L.D_START, 0.0
        rows.append([f"song#{k}", 0, 0, d])
        for gap in (1, 2, 4, 7, 12):
            t += gap * DAY
            r = L.recall(gap, s)
            g = L.GOOD if rnd.random() < r else L.AGAIN
            rows.append([f"song#{k}", int(t), g])
            s, d = L.next_memory(s, d, r, g, w)
    return rows


class OwnCurve(unittest.TestCase):
    def test_defaults_until_there_is_enough_history(self):
        brain = {"reviews": history(L.W[8])[:40]}
        self.assertIsNone(L.refit(brain, 100 * DAY))
        self.assertEqual(L.weights(brain), L.DEFAULT_FIT)

    def test_a_player_who_forgets_faster_moves_the_growth_down_a_bounded_step_once_a_day(self):
        brain = {"reviews": history(L.W[8] - 1.0)}
        fit = L.refit(brain, 100 * DAY)
        self.assertIsNotNone(fit)
        self.assertLess(fit["grow"], L.W[8])
        for k in ("grow", "fall"):
            self.assertLessEqual(abs(fit[k] - L.DEFAULT_FIT[k]), L.FIT_MOVES * L.FIT_STEP[k] + 1e-9)
        self.assertIsNone(L.refit(brain, 100 * DAY + 3600))          # not twice in one day
        again = L.refit(brain, 101 * DAY)
        self.assertLessEqual(again["grow"], fit["grow"])
        self.assertEqual(L.weights(brain)["grow"], again["grow"])

    def test_the_fitted_curve_fits_the_history_better(self):
        rows = sorted(history(L.W[8] - 1.0), key=lambda r: (r[0], r[1]))
        brain = {"reviews": rows}
        L.refit(brain, 100 * DAY)
        self.assertLess(L.log_loss(rows, L.weights(brain)), L.log_loss(rows, L.DEFAULT_FIT))

    def test_a_broken_history_or_fit_falls_back_to_the_defaults(self):
        brain = {"reviews": [["x", "y", 9], "z", None], "fit": {"grow": "a", "fall": 99}}
        self.assertIsNone(L.refit(brain, DAY))
        self.assertEqual(L.weights(brain), L.DEFAULT_FIT)

    def test_the_history_is_kept_short(self):
        brain = {}
        for i in range(L.REVIEWS_MAX + 10):
            L.log_review(brain, "s#0", i, L.GOOD)
        self.assertEqual(len(brain["reviews"]), L.REVIEWS_MAX)
        L.log_review(brain, "s#1", 0, 0, 6.5)
        self.assertEqual(brain["reviews"][-1], ["s#1", 0, 0, 6.5])


class WarmUp(unittest.TestCase):
    def test_only_parts_about_to_fade(self):
        now = 10 * DAY
        songs = {"a": {"0": {"S": 10, "D": 5, "mem_at": now - 9.8 * DAY},     # just over 90%, under it soon
                       "1": {"S": 30, "D": 5, "mem_at": now - DAY},           # holds for weeks
                       "2": {"S": 0.5, "D": 8, "mem_at": now - 9 * DAY},      # long gone: its own lesson
                       "3": {"streak": 2}},                                   # from before the model
                 "b": {"4": {"S": 4, "D": 5, "mem_at": now - 4.5 * DAY}}}    # already a little under
        self.assertEqual([(s, k) for s, k, _ in L.about_to_fade(songs, now)], [("b", 4), ("a", 0)])
        self.assertEqual(len(L.about_to_fade(songs, now, limit=1)), 1)


class Steps(unittest.TestCase):
    notes = [60, 64, 67, 72, 67, 64]

    def test_two_errors_make_a_step_weak_and_the_weeks_wash_it_out(self):
        brain = {}
        keys = L.step_keys("s", self.notes, 2)
        self.assertEqual(keys, ("s#2", "4>7+3"))
        L.step_error(brain, keys, 0)
        self.assertIsNone(L.weak_step(brain, "s", self.notes, range(6), 0))
        L.step_error(brain, keys, 0)
        self.assertEqual(L.weak_step(brain, "s", self.notes, range(6), 0), 2)
        self.assertIsNone(L.weak_step(brain, "s", self.notes, range(6), 14 * DAY))

    def test_a_drill_that_went_right_halves_it(self):
        brain = {}
        for _ in range(2):
            L.step_error(brain, L.step_keys("s", self.notes, 2), 0)
        L.step_drilled(brain, L.step_keys("s", self.notes, 2), 0)
        self.assertIsNone(L.weak_step(brain, "s", self.notes, range(6), 0))

    def test_the_same_two_notes_in_another_song(self):
        brain = {}
        for _ in range(2):
            L.step_error(brain, L.step_keys("s", self.notes, 2), 0)
        other = [55, 50, 55, 64, 67]                  # E up to G at index 4, like s's step 2
        self.assertEqual(L.weak_step(brain, "t", other, range(5), DAY), 4)
        self.assertIsNone(L.weak_step(brain, "t", other, range(4), DAY))


class Hesitation(unittest.TestCase):
    def test_a_long_pause_against_the_parts_own_gaps(self):
        times = [0, 0.5, 1.0, 3.0, 3.5]              # the third gap is four times the usual one
        n, even, mid = L.hesitations(L.gaps(times))
        self.assertEqual((n, even, mid), (1, False, 0.5))
        n, even, _ = L.hesitations(L.gaps([0, 0.5, 1.0, 1.5, 2.0]))
        self.assertEqual((n, even), (0, True))

    def test_a_long_note_in_the_song_is_not_a_pause(self):
        times = [0, 0.5, 1.0, 3.0, 3.5]
        song = [0, 0.5, 1.0, 3.0, 3.5]              # the song itself waits there
        self.assertEqual(L.hesitations(L.gaps(times, song))[0], 0)

    def test_quick_playing_never_hesitates(self):
        self.assertEqual(L.hesitations(L.gaps([0, 0.05, 0.1, 0.4]))[0], 0)

    def test_the_parts_remembered_gap(self):
        part = {}
        L.update_flow(part, 0.5)
        L.update_flow(part, 1.0)
        self.assertAlmostEqual(part["flow"], 0.65)
        times = [0, 0.3, 0.6, 1.6]                   # slow only against this part's own pace
        self.assertEqual(L.hesitations(L.gaps(times))[0], 1)
        self.assertEqual(L.hesitations(L.gaps([0, 0.3, 0.6, 1.4]))[0], 0, "few gaps: a pause under 1 s is not one")
        self.assertEqual(L.hesitations(L.gaps(times), part["flow"])[0], 0)

    def test_fluency_is_a_skill(self):
        self.assertIn("fluency", L.SKILLS)
        sk = {}
        L.update_fluency(sk, 0)
        up = sk["fluency"]
        L.update_fluency(sk, 2)
        self.assertGreater(up, L.BKT_L0)
        self.assertLess(sk["fluency"], up)

    def test_fluency_is_not_the_weakest_before_it_was_measured(self):
        sk = {"black": 0.5, "leap": 0.8, "long": 0.6, "rhythm": 0.9, "memory": 0.7}
        self.assertEqual(L.weakest(sk)["skill"], "black")
        sk["fluency"] = 0.2
        self.assertEqual(L.weakest(sk)["skill"], "fluency")


class Fatigue(unittest.TestCase):
    def test_only_when_the_mistakes_clearly_rise(self):
        good, bad = [False] * 9, [True] * 9
        self.assertFalse(L.tired(good + bad[:5]))            # too few rounds to tell
        self.assertTrue(L.tired(good + [True, False, True, True, True, True, False, True, True]))
        self.assertFalse(L.tired(bad + bad))                 # a hard day from the start is not fatigue
        self.assertFalse(L.tired(good + good))
        self.assertFalse(L.tired(good + [False] * 3 + [True, False] * 3))   # a little worse, by chance

    def test_a_steady_lesson_rarely_calls_itself_tired(self):
        import random
        rnd = random.Random(7)
        for rate in (0.2, 0.4):
            alarms = 0
            for _ in range(1000):
                outs, said, look = [], False, 0
                for _ in range(30):
                    outs.append(rnd.random() < rate)
                    if len(outs) - look >= L.TIRED_EVERY:      # as the lesson looks: every 3 rounds at most
                        look = len(outs)
                        said = said or L.tired(outs)
                alarms += said
            self.assertLess(alarms / 1000, 0.1, rate)
        rises = 0
        for _ in range(500):
            outs, said, look = [], False, 0
            for i in range(30):
                outs.append(rnd.random() < (0.1 if i < 15 else 0.7))
                if len(outs) - look >= rnd.choice((3, 4, 6)):   # looks at uneven points, as after a practice
                    look = len(outs)
                    said = said or L.tired(outs)
            rises += said
        self.assertGreater(rises / 500, 0.5)
        # a clear rise is seen at any length, not only at some: 12 clean then 12 wrong
        self.assertTrue(all(L.tired([False] * 12 + [True] * k) for k in range(10, 13)))


class Learner2026(unittest.TestCase):
    """Armonico's own rules around the curves: same day, the start, the safe fit, skills, pace."""

    def test_one_slip_in_a_second_lesson_the_same_day_does_not_erase_weeks(self):
        p = {"S": 21.0, "D": 5.0, "mem_at": 0}
        L.update_memory(p, L.AGAIN, 600)                   # 10 minutes later
        self.assertGreaterEqual(p["S"], 10.0)
        q = {"S": 21.0, "D": 5.0, "mem_at": 0}
        L.update_memory(q, L.GOOD, 600)
        self.assertAlmostEqual(q["S"], 21.0, places=1)
        r = {"S": 21.0, "D": 5.0, "mem_at": 0}
        L.update_memory(r, L.AGAIN, 2 * DAY)              # another day: the full fall, as before
        self.assertLess(r["S"], 10.0)

    def test_a_part_learned_at_once_starts_more_stable(self):
        got = []
        for fails in (0, 1, 2, 5):
            p = {}
            L.start_memory(p, 0.0, 0, fails)
            got.append(p["S"])
        self.assertEqual(got, sorted(got, reverse=True))
        self.assertGreater(got[0], got[2])
        p = {}
        L.start_memory(p, 0.0, 0)
        self.assertEqual(p["S"], L.S_START)

    def test_a_steady_player_keeps_the_defaults_and_a_new_fit_must_predict_better(self):
        brain = {"reviews": history(L.W[8])}
        fit = L.refit(brain, 100 * DAY)
        self.assertFalse(fit["kept"])
        self.assertEqual(L.weights(brain), L.DEFAULT_FIT)
        self.assertLess(fit["cal"]["rmse"], 0.05)

    def test_calibration_tells_a_wrong_curve(self):
        rows = sorted(history(L.W[8]), key=lambda r: (r[0], r[1]))
        good = L.calibration(rows, L.DEFAULT_FIT)
        bad = L.calibration(rows, {"grow": L.W[8] + 1.0, "fall": L.W[11]})
        self.assertLess(good["loss"], bad["loss"])

    def test_skills_move_once_per_part_in_a_lesson_and_fade_unused(self):
        skills, seen = {}, set()
        for _ in range(4):
            L.update_skills(skills, ["leap"], False, seen, "a#0")
        once = {}
        L.update_skills(once, ["leap"], False)
        self.assertEqual(skills, once)
        brain = {"skills": {"leap": 0.9}, "skills_at": 0}
        L.age_skills(brain, 90 * DAY)
        self.assertAlmostEqual(brain["skills"]["leap"], L.BKT_L0 + (0.9 - L.BKT_L0) / 2, places=3)

    def test_a_steady_pace_does_not_overshoot_and_a_stop_is_not_slow_playing(self):
        pace = None
        for held in (50, 60, 70, 80, 80, 80, 80, 80, 80, 80):
            pace = L.update_pace(pace, held)
        self.assertLess(L.next_speed(pace), 84)
        starts = [i * 0.5 for i in range(10)]
        hits = [(i, i * 0.5) for i in range(10)]
        stalled = hits[:5] + [(i, t + 5.0) for i, t in hits[5:]]   # 5 s stuck on a wrong key once
        self.assertAlmostEqual(L.held_speed(stalled, starts), 100.0)

    def test_a_song_with_parts_gone_far_is_worth_a_lesson(self):
        now = 100 * DAY
        parts = {"0": {"S": 1.0, "D": 5, "mem_at": now - 30 * DAY}, "1": {"S": 50, "D": 5, "mem_at": now - DAY}}
        self.assertEqual(L.comeback(parts, now), 1)


# ---------- a whole lesson, with the keyboard played by the test ----------
SONGS = {"a": [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65],     # 3 parts of 4 notes
         "b": [60, 64, 67, 72, 67, 64, 60, 62]}                     # 2 parts


def fake_song(name):
    notes = SONGS.get(name)
    return (name, list(notes), None, None, "shortcut", 0) if notes else (None, None, None, None, None, 0)


class FakeRecorder:
    def __init__(self, song):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def save(self):
        return None

    def screen(self, snap):
        pass


def old_part(now, days_ago, s=10.0, streak=3):
    return {"streak": streak, "attempts": 3, "errors": 0, "first": now - 30 * DAY, "last": now - days_ago * DAY,
            "adj": 0, "S": s, "D": 5.0, "mem_at": now - days_ago * DAY}


class LessonFlow(unittest.TestCase):
    """lesson() from start to end, with the keys pressed by the test and nothing else faked
    but the keyboard, the band and the files' folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.dir = d
        self.calls = []
        real_ui = game.ui

        def ui(**kw):
            self.calls.append(kw)
            real_ui(**kw)
        for name, value in (("DATA_DIR", d), ("FLAG_GAME", d / "flag"), ("LAST_CHAT_FILE", d / "chat"),
                            ("PROFILES_FILE", d / "profiles.json"), ("SETTINGS_FILE", d / "settings.json"),
                            ("load_song", fake_song), ("Recorder", FakeRecorder), ("find_midi", lambda n: None),
                            ("ui", ui), ("RECORDER", None)):
            p = mock.patch.object(game, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(game.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.rounds, self.bad, self.run_slips = 0, set(), []
        self.replies = []
        eng = self.eng = game.Engine()
        for name in ("prepare_band", "status", "pub", "publish_practice", "close_ports", "pause", "drain",
                     "count_in", "check_abort"):
            setattr(eng, name, lambda *a, **k: None)
        eng.open_ports = lambda: True
        eng.play = lambda *a, **k: []
        eng.reply = lambda text, chat=None: self.replies.append(text)
        eng.run_with_band = self.fake_run
        real_answer = eng.answer

        def answer(seg, early=None):
            self.rounds += 1
            self.wrong_given = False
            return real_answer(seg, early)
        eng.answer = answer
        eng.next_key = self.next_key

    def next_key(self, timeout):
        at = game.UI["expect"]
        if self.rounds in self.bad and at == 1 and not self.wrong_given:
            self.wrong_given = True
            return 1                          # a key in none of the songs
        return game.UI["midi"][at]

    def fake_run(self, notes, offset=0, band=True, title=None):
        e = self.eng
        e.run_slips, e.run_reached, e.run_held, e.run_detail = list(self.run_slips), len(notes), None, None
        e.run_wrong, e.run_named = {j: 1 for j in self.run_slips}, []
        e.run_hits = [(j, float(j)) for j in range(len(notes))]       # one note a second: even
        return 90

    def save(self, name, data):
        (self.dir / name).write_text(json.dumps(data))

    def load(self, name):
        return json.loads((self.dir / name).read_text())

    def test_parts_of_other_songs_about_to_fade_are_warmed_up_first(self):
        now = time.time()
        self.save("lessons.json", {"b": {"learned": 2, "parts": {"0": old_part(now, 9.8), "1": old_part(now, 1)}}})
        self.eng.lesson("", "a")
        prog, brain = self.load("lessons.json"), self.load("learner.json")
        b0 = prog["b"]["parts"]["0"]
        self.assertGreater(b0["mem_at"], now - 60)            # reviewed now
        self.assertGreater(b0["S"], 10)                       # clean from memory: it grew
        self.assertEqual(b0["streak"], 3)                     # the method's count is not touched
        self.assertLess(prog["b"]["parts"]["1"]["mem_at"], now - 60)   # the part that holds waits
        rows = brain["reviews"]
        self.assertIn(rows[0][:2], [["b#0", rows[0][1]]])
        self.assertIn(rows[0][2], (L.GOOD, L.EASY))
        self.assertEqual([r[0] for r in rows if r[2] == 0], ["a#0", "a#1"])   # the parts learned today
        self.assertIn("fluency", brain["skills"])
        warm = [c["warmup"] for c in self.calls if c.get("warmup")]
        self.assertEqual(warm[0], {"parts": [{"song": "b", "part": 1}], "at": 0})
        sm = game.UI["summary"]
        self.assertEqual((sm["warmup"], sm["drills"], sm["tired"]), (1, 0, False))
        self.assertIsNone(game.UI["warmup"])

    def test_the_opening_run_is_graded_and_a_tricky_step_is_drilled_alone(self):
        now = time.time()
        parts = {"0": old_part(now, 1, s=1.0, streak=1), "1": old_part(now, 1, s=1.0, streak=1)}
        self.save("lessons.json", {"a": {"learned": 2, "parts": parts}})
        self.save("learner.json", {"skills": {}, "transitions": {"a#5": [1.0, int(now)]}})   # went wrong before
        self.run_slips = [5]                  # one wrong key in part 2, note 6
        self.eng.lesson("", "a")
        brain = self.load("learner.json")
        graded = {r[0]: r[2] for r in brain["reviews"] if r[2]}
        self.assertEqual(graded, {"a#0": L.EASY, "a#1": L.GOOD})      # even and clean; one wrong key found again
        self.assertIn({"part": 2, "notes": [5, 6, 7]}, [c.get("drill") for c in self.calls])
        self.assertEqual(game.UI["summary"]["drills"], 1)
        self.assertIsNone(game.UI["drill"])
        prog = self.load("lessons.json")
        self.assertEqual(prog["a"]["parts"]["1"]["streak"], 0)       # a slip still starts the count over
        self.assertEqual(prog["a"]["parts"]["0"]["streak"], 2)

    def test_mistakes_rising_late_in_a_lesson_suggest_a_stop_once(self):
        self.save("settings.json", {"pace": "fast"})                 # 3 new parts, many rounds
        self.bad = set(range(8, 13))           # part 3 goes wrong for a while, then right
        # a new part going wrong for a while is not fatigue (the test in piano_learner.tired says
        # so): here the test itself says tired once the rounds pass 10, to follow the lesson's side
        real = L.tired
        with mock.patch.object(game.learner, "tired", side_effect=lambda o, *a: len(o) >= 10 or real(o)):
            self.eng.lesson("", "a")
        hints = [c["stop_hint"] for c in self.calls if c.get("stop_hint")]
        self.assertEqual(hints, ["Good place to stop: end on this one"])
        self.assertEqual(self.replies.count("Good place to stop: end on this one"), 1)
        self.assertTrue(game.UI["summary"]["tired"])
        self.assertIsNone(game.UI["stop_hint"])
        brain = self.load("learner.json")
        self.assertTrue(any(k.startswith("a#") for k in brain["transitions"]))   # the steps that went wrong

    def test_a_learner_file_from_before_loads_and_keeps_its_skills(self):
        self.save("learner.json", {"skills": {"black": 0.6}})
        self.eng.lesson("", "a")
        brain = self.load("learner.json")
        self.assertGreaterEqual(brain["skills"]["black"], 0.0)
        self.assertIn("reviews", brain)


if __name__ == "__main__":
    unittest.main()
