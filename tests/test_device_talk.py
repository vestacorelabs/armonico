"""What the figure on the rest screen says, the echo game and the stars that ring (device/ui/talk.py)."""
import itertools
import unittest
from unittest import mock

from test_device_ui import load_screen, make_app, pygame


class Clock:
    def __init__(self, t=100.0):
        self.now = t

    def __call__(self):
        return self.now


def app_at(mod, lang="en", kind="house"):
    app = make_app(mod, (800, 480), lang, {"character": kind})
    app.clock = Clock()
    app.t = app.clock
    app.screen.pub = mock.Mock()
    app.draw()
    return app


def later(app, secs):
    app.clock.now += secs
    app.talk_tick()


def notes_played(app):
    return [c.args[1] for c in app.screen.pub.call_args_list if c.args and c.args[0] == "piano/device/note"]


@unittest.skipIf(pygame is None, "pygame is not installed")
class Lines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()
        app_at(cls.mod)                              # puts device/ on the path, as the screen does
        from ui import talk
        cls.talk = talk

    def test_every_group_has_lines_that_fit_one_row(self):
        app = app_at(self.mod)
        facts = {"streak": 4, "week": 35, "hour": 1, "hm": "01:20", "played": 0, "touches": 0, "idle": 0,
                 "miss": "second", "song": "Ode to Joy", "k": 4, "n": 6, "left": 2}
        for lang in ("en", "he"):
            f = dict(facts, miss="השני" if lang == "he" else "second")
            for name, (face, by) in self.talk.GROUPS.items():
                with self.subTest(lang=lang, group=name):
                    lines = self.talk.Lines(seed=1)
                    got = lines.compose(name, lang, f, app.talk_fits)
                    self.assertIsNotNone(got)
                    self.assertTrue(app.talk_fits(got[0]))
                    self.assertEqual(got[1], face)
                    self.assertNotIn("{", got[0])

    def test_a_line_is_not_said_twice_until_all_were_then_it_starts_over(self):
        # it went quiet once everything was said, and looked as if it no longer answered: now it starts over
        app = app_at(self.mod, "he")
        lines = self.talk.Lines(seed=3)
        f = app.talk_facts(app.t())
        said = []
        for _ in range(400):
            got = lines.pick("he", f, app.talk_fits, "house")
            self.assertIsNotNone(got)
            said.append(got[0])
        seen, first_repeat = set(), None
        for i, x in enumerate(said):
            if x in seen:
                first_repeat = i
                break
            seen.add(x)
        # how many lines fit depends on the day and the hour the test runs (talk_facts), so not a fixed 50: most of
        # them said first (the last group's own lines can wait for the round after, pick() never says it twice running)
        self.assertGreaterEqual(first_repeat or 400, int(len(set(said)) * 0.8), "no line twice before most were said")

    def test_parts_rest_between_lines(self):
        app = app_at(self.mod)
        lines = self.talk.Lines(seed=5)
        f = app.talk_facts(app.t())
        a = lines.compose("angry", "en", f, app.talk_fits)[0]
        b = lines.compose("angry", "en", f, app.talk_fits)[0]
        self.assertNotEqual(a.split()[0], b.split()[0], "the opening word of the last line is not used again at once")

    def test_what_comes_first_follows_what_happened(self):
        app = app_at(self.mod)
        lines = self.talk.Lines(seed=7)
        f = app.talk_facts(app.t())
        g = self.talk.GROUPS
        self.assertEqual(lines.pick("en", dict(f, touches=9), app.talk_fits, "house")[1], g["angry"][0])
        self.assertEqual(lines.pick("en", dict(f, played=12), app.talk_fits, "house")[1], g["played"][0])
        self.assertEqual(lines.pick("en", dict(f, idle=120, hour=14), app.talk_fits, "house")[1], "surprised")
        self.assertEqual(lines.pick("en", dict(f, idle=120, hour=2), app.talk_fits, "house")[1], "sleepy")


@unittest.skipIf(pygame is None, "pygame is not installed")
class LinesInARow(unittest.TestCase):
    """Many touches in a row: it always answers, never the same kind of line twice running, never angry twice
    running, never a part's name or a field said as it is, and one of something said as one."""

    def facts(self, **kw):
        return dict(dict(song="Bella ciao", k=1, n=15, left=14, streak=3, week=1, hour=15, hm="15:00", played=0,
                         touches=9, idle=3, miss="הראשון"), **kw)

    def test_three_hundred_touches(self):
        from ui import talk
        for lang in ("he", "en"):
            for touches in (0, 9):
                lines = talk.Lines(7)
                out = []
                for _ in range(300):
                    got = lines.pick(lang, self.facts(touches=touches), lambda s: True, "house")
                    self.assertIsNotNone(got)
                    out.append((lines.groups[-1], got[1], got[0]))
                for a, b in zip(out, out[1:]):
                    self.assertNotEqual(a[0], b[0], (a, b))
                    self.assertFalse(a[1] == b[1] == "angry", (a, b))
                for _, _, text in out:
                    self.assertNotIn("@", text)
                    self.assertNotIn("{", text)
                    self.assertNotRegex(text, r"(^|\s)1 (דקות|חלקים|minutes|parts)\b")

    def test_the_song_line(self):
        from ui import talk
        got = talk.Lines(1).compose("song", "he", self.facts(), lambda s: True)
        self.assertTrue(got[0].startswith("ב-Bella ciao למדת חלק אחד מתוך 15."), got)
        self.assertNotIn("@", got[0])


@unittest.skipIf(pygame is None, "pygame is not installed")
class RestScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def touch_figure(self, app):
        x, y, s = app.char.last
        app.rest_touch(x, y - 10, x, y - 10)

    def test_a_touch_talks_a_moment_later_with_a_face(self):
        app = app_at(self.mod)
        self.touch_figure(app)
        self.assertIsNone(app.bubble)
        later(app, 0.5)
        self.assertTrue(app.bubble and app.bubble["text"])
        self.assertIsNotNone(app.char.face(app.t()))
        app.draw()

    def test_quick_touches_laugh_instead_and_five_spin_it(self):
        app = app_at(self.mod)
        self.touch_figure(app)
        later(app, 0.2)
        self.touch_figure(app)
        self.assertEqual(app.char.reaction(app.t()), "happy")
        later(app, 0.5)
        self.assertIsNone(app.bubble, "a laugh says nothing")
        for _ in range(3):
            later(app, 0.2)
            self.touch_figure(app)
        self.assertEqual(app.char.reaction(app.t()), "dizzy")
        later(app, 2.5)
        self.assertTrue(app.bubble, "a line while still dizzy")
        later(app, 1.2)
        self.assertEqual(app.char.face(app.t()), "offended", "and then it takes offense")

    def test_the_echo_game_win_fail_and_ignored(self):
        for answer in ("right", "wrong", "none"):
            app = app_at(self.mod)
            app.talk_n = 2                           # the next line is the third: the game
            self.touch_figure(app)
            later(app, 0.5)
            self.assertIsNotNone(app.echo)
            notes = app.echo["notes"]
            for _ in range(12):
                later(app, 0.3)
            on = [n for n in notes_played(app) if n.startswith("on ")]
            self.assertEqual([int(n[3:]) % 12 for n in on[-3:]], [n % 12 for n in notes])
            self.assertTrue(app.echo["wait"])
            app.draw()
            with self.subTest(answer=answer):
                if answer == "none":
                    later(app, 10)
                    self.assertIsNone(app.echo)
                    self.assertEqual(app.char.face(app.t()), "offended")
                    continue
                keys = list(notes) if answer == "right" else [notes[0], (notes[1] + 1) % 13, notes[2]]
                for k in keys:
                    app.talk_key(k, app.t())
                later(app, 0.8)
                self.assertIsNone(app.echo)
                self.assertEqual(app.char.face(app.t()), "proud" if answer == "right" else "smug")
                if answer == "wrong":
                    self.assertEqual(app.echo_miss, 1)

    def test_a_touch_while_it_waits_plays_the_notes_again(self):
        app = app_at(self.mod)
        app.talk_n = 2
        self.touch_figure(app)
        later(app, 0.5)
        for _ in range(12):
            later(app, 0.3)
        n = len(notes_played(app))
        self.touch_figure(app)
        for _ in range(8):
            later(app, 0.3)
        self.assertEqual(len(notes_played(app)), n + 6)

    def test_a_star_rings(self):
        app = app_at(self.mod)
        L = app._layers[app.cv.surf.get_size()]
        px, py = L["stars"][0][0], L["stars"][0][1]
        x, y = app.cv.to_plan(px, py)
        if app.char.box_hit(x, y):
            self.skipTest("the first star sits behind the figure")
        app.rest_touch(x, y, px + 2, py + 2)
        self.assertTrue(app.star_flares)
        later(app, 0.1)
        self.assertTrue(any(n.startswith("on ") for n in notes_played(app)))
        app.draw()
        self.assertIsNone(app.bubble, "a star does not make it talk")

    def test_keys_played_count_and_the_figures_notes_are_not_keys(self):
        app = app_at(self.mod)
        for pc in (0, 2, 4):
            app.talk_key(pc, app.t())
        self.assertEqual(app.played_since, 3)
        app.fig_note(64, app.t(), 0.3)
        later(app, 0.1)
        self.assertTrue(any(q[0] == 4 for q in app.screen.touch_sent), "its copy back from the engine is dropped")

    def test_the_bubble_stays_on_one_row_in_both_languages(self):
        for lang, kind in itertools.product(("en", "he"), ("house", "keys", "grand")):
            app = app_at(self.mod, lang, kind)
            for _ in range(30):
                self.touch_figure(app)
                later(app, 1.5)
                if app.bubble:
                    with self.subTest(lang=lang, kind=kind, text=app.bubble["text"]):
                        self.assertTrue(app.talk_fits(app.bubble["text"]))
                app.echo = None
                later(app, 1.0)
            app.draw()


if __name__ == "__main__":
    unittest.main()
