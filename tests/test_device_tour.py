"""The opening tour and the one-time tips of the Armonico Pi screen (device/ui/tour.py): when they
come and when not, how they move on and end, and that every step and tip fits the screen in both
languages and at both sizes without covering the character."""
import time
import unittest
from unittest import mock

from test_device_ui import load_screen, make_app, pygame

W, H, BAR = 800, 480, 58


def overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def inside(a):
    x, y, w, h = a
    return x >= 0 and y >= 0 and x + w <= W and y + h <= H


def tap(app, x, y):
    """A click at plan point (x, y) of the physical screen."""
    cv = app.cv
    app.last_tap = (0.0, None, "")
    app.pointer_down(cv.ox + x * cv.scale, cv.oy + y * cv.scale)
    app.pointer_up(cv.ox + x * cv.scale, cv.oy + y * cv.scale)


def centre(box):
    x, y, w, h = box
    return x + w / 2, y + h / 2


def fresh(mod, size=(800, 480), lang="en", **settings):
    """A new install: the setup not done yet, then finished."""
    app = make_app(mod, size, lang, settings)
    app.st.pop("setup_done", None)
    app.screen.start_lesson = mock.Mock()
    app.open_setup("done")
    app.finish_setup()
    return app


@unittest.skipIf(pygame is None, "pygame is not installed")
class Tour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_starts_after_the_first_setup(self):
        app = fresh(self.mod)
        self.assertEqual(app.view, "rest")
        self.assertIs(app.saved.get("tour_done"), False)
        self.assertEqual(app.saved.get("tips_seen"), [])
        self.assertTrue(app.tour_on())
        app.draw()
        self.assertEqual(app.tour_step, 0)

    def test_not_on_a_device_set_up_before_the_tour_existed(self):
        app = make_app(self.mod)                     # setup_done, and no tour key: set up before this update
        self.assertFalse(app.tour_on())
        app.draw()
        self.assertTrue(app.bar_items, "the rest screen as always, with its buttons")
        app.open_setup("lang")                       # the setup run again from the settings
        app.finish_setup()
        self.assertFalse(app.tour_on())
        self.assertNotIn("tour_done", app.saved)
        app.go("songs")
        app.draw()
        self.assertIsNone(app.tip, "no tips on a device in use either")

    def test_ok_and_a_tap_on_the_bubble_go_on_and_done_ends(self):
        app = fresh(self.mod)
        app.draw()
        app.button("ok", False)
        self.assertEqual(app.tour_step, 1)
        app.button("ok", True)                       # the hold of that press is not a second press
        self.assertEqual(app.tour_step, 1)
        app.draw()
        tap(app, *centre(app._tour_lay["bubble"]))
        self.assertEqual(app.tour_step, 2)
        app.draw()
        tap(app, *centre(app._tour_lay["next"][0]))
        self.assertEqual(app.tour_step, 3)
        for _ in range(4):
            app.button("ok", False)
            app.draw()
        self.assertEqual(app.tour_step, 7)
        self.assertEqual(app._tour_lay["next"][1], app.tr("tour.done"))
        app.button("ok", False)
        self.assertFalse(app.tour_on())
        self.assertIs(app.st.get("tour_done"), True)
        app.draw()
        self.assertTrue(app.bar_items)

    def test_skip_ends_it(self):
        app = fresh(self.mod)
        app.draw()
        app.button("menu", False)
        self.assertFalse(app.tour_on())
        self.assertIs(app.saved.get("tour_done"), True)
        app = fresh(self.mod, lang="he")
        app.draw()
        tap(app, *centre(app._tour_lay["skip"][0]))
        self.assertIs(app.saved.get("tour_done"), True)

    def test_nothing_under_the_tour_is_pressed(self):
        app = fresh(self.mod)
        app.draw()
        octave = app.screen.octave
        app.button("up", False)
        app.button("down", False)
        self.assertEqual(app.screen.octave, octave)
        for i in range(4):                           # the 4 buttons on the screen and the song card under the dimming
            x, y, w, h = app.bar_rects[i]
            tap(app, x + w / 2, y + h / 2)
        tap(app, *centre(app.song_rect))
        app.screen.start_lesson.assert_not_called()
        self.assertEqual(app.view, "rest")
        self.assertEqual(app.tour_step, 0)
        self.assertEqual(app.screen.octave, octave)

    def test_the_rest_video_waits_for_the_tour(self):
        app = fresh(self.mod, rest="video")
        self.assertEqual(app.rest_content, "animation")
        self.assertNotEqual(app.screen_view(), "rest")
        app.tour_end()
        self.assertEqual(app.rest_content, "video")

    def test_never_during_a_lesson_an_alarm_or_a_song_and_back_after(self):
        app = fresh(self.mod)
        app.draw()
        app.button("ok", False)
        for busy in ("lesson", "alarm", "song"):
            if busy == "lesson":
                app.screen.engine.state.update(mode="wait", midi=[60, 62], expect=0, show=True, total=2, pos=0)
            else:
                setattr(app.screen, busy, True)
            with self.subTest(busy=busy):
                self.assertFalse(app.tour_on())
                app.draw()
                self.assertIsNone(app.tip if busy != "lesson" else None)
            app.screen.engine.state = {"mode": "idle", "volume": 70}
            app.screen.alarm = app.screen.song = False
            self.assertTrue(app.tour_on(), "it goes on when the device is free again")
            self.assertEqual(app.tour_step, 1, "from the step it was on")

    def test_every_step_fits_in_both_languages_and_sizes(self):
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                for kind in ("house", "keys", "grand", "metro"):
                    for songs in (True, False):
                        app = fresh(self.mod, size, lang, character=kind)
                        if not songs:
                            app.screen.engine.overview["songs"] = []
                            app.card_cache = (0.0, None)
                        for step in range(8):
                            app.tour_step = step
                            with self.subTest(size=size, lang=lang, kind=kind, songs=songs, step=step):
                                app.draw()
                                lay = app._tour_lay
                                self.assertEqual(lay["step"], step)
                                self.assertTrue(inside(lay["bubble"]), lay["bubble"])
                                self.assertTrue(inside(lay["cbox"]), lay["cbox"])
                                self.assertFalse(overlap(lay["bubble"], lay["cbox"]))
                                if lay["spot"]:
                                    self.assertFalse(overlap(lay["bubble"], lay["spot"]))
                                    self.assertFalse(overlap(lay["cbox"], lay["spot"]))
                                self.assertTrue(lay["lines"])
                                self.assertNotIn("tour.", " ".join(lay["lines"]))
                                # the text stays inside the bubble
                                bx, by, bw, bh = lay["bubble"]
                                for line in lay["lines"]:
                                    self.assertLessEqual(app.cv.measure(line, lay["size"]), bw - 2 * lay["pad"] + 1)

    def test_the_outlines_come_from_the_real_rest_screen(self):
        app = fresh(self.mod)
        app.draw()
        app.tour_step = 1
        app.draw()
        bars = app.bar_rects
        self.assertEqual(app._tour_lay["spot"], (bars[0][0], bars[0][1], bars[-1][0] + bars[-1][2] - bars[0][0], bars[0][3]))
        app.tour_step = 2
        app.draw()
        self.assertEqual(app._tour_lay["spot"], app.song_rect)
        app.tour_step = 5
        app.draw()
        self.assertEqual(app._tour_lay["spot"], (bars[0][0], bars[0][1], bars[-1][0] + bars[-1][2] - bars[0][0], bars[0][3]))
        app.tour_step = 6
        app.draw()
        self.assertEqual(app._tour_lay["spot"], bars[0])

    def test_the_picture_is_drawn_once_a_step(self):
        app = fresh(self.mod)
        app.draw()
        with mock.patch.object(app, "draw_rest_layer") as layer:
            app.draw()
            app.draw()
            layer.assert_not_called()
            app.button("ok", False)
            app.draw()
            layer.assert_called_once()

    def test_show_the_tour_again(self):
        app = make_app(self.mod)
        app.open_page("guide")
        cells = app.settings_cells()["guide"]
        cell = next(c for c in cells if c[0] == app.tr("c.tour"))
        cell[3]()
        self.assertEqual(app.view, "rest")
        self.assertIs(app.st.get("tour_done"), False)
        self.assertEqual(app.st.get("tips_seen"), [])
        self.assertTrue(app.tour_on())
        self.assertEqual(app.tour_step, 0)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Tips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def done(self, size=(800, 480), lang="en", **kw):
        app = fresh(self.mod, size, lang, **kw)
        app.tour_end()
        return app

    def open(self, app, name):
        if name == "lesson":
            app.screen.engine.state.update(mode="wait", midi=[60, 62, 64], expect=1, show=True, total=3, pos=1)
        else:
            app.go(name)

    def test_each_tip_once(self):
        for name in ("songs", "free", "lesson"):
            app = self.done()
            with self.subTest(tip=name):
                self.open(app, name)
                app.draw()
                self.assertEqual(app.tip[0], name)
                self.assertIn(name, app.saved.get("tips_seen"))
                app.tip = (name, app.t() - 9)        # 8 seconds later
                app.draw()
                self.assertIsNone(app.tip)
                app.screen.engine.state = {"mode": "idle", "volume": 70}
                app.go("rest")
                app.draw()
                self.open(app, name)
                app.draw()
                self.assertIsNone(app.tip, "never again")

    def test_a_tap_on_the_tip_closes_it_and_presses_nothing(self):
        app = self.done()
        app.go("songs")
        app.draw()
        start = app.screen.start_lesson
        tap(app, *centre(app._tip_lay["bubble"]))
        self.assertIsNone(app.tip)
        start.assert_not_called()
        self.assertEqual(app.view, "songs")

    def test_no_tip_while_the_tour_is_due_or_an_alarm_rings(self):
        app = fresh(self.mod)
        app.screen.alarm = True                      # the tour waits; Songs opened meanwhile: no tip yet
        app.go("songs")
        app.draw()
        self.assertIsNone(app.tip)
        app.screen.alarm = False
        app.tour_end()
        app.screen.alarm = True
        app.draw()
        self.assertIsNone(app.tip)
        app.screen.alarm = False
        app.draw()
        self.assertEqual(app.tip[0], "songs")

    def test_songs_tip_waits_for_songs_and_free_tip_for_touch(self):
        app = self.done()
        app.screen.engine.overview["songs"] = []
        app.go("songs")
        app.draw()
        self.assertIsNone(app.tip)
        app.save(play="off")
        app.go("free")
        app.draw()
        self.assertIsNone(app.tip)
        self.assertEqual(app.st.get("tips_seen"), [])

    def test_every_tip_fits_in_both_languages_and_sizes(self):
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                for kind in ("house", "keys", "grand", "metro"):
                    for name in ("songs", "free", "lesson"):
                        app = self.done(size, lang, character=kind)
                        self.open(app, name)
                        with self.subTest(size=size, lang=lang, kind=kind, tip=name):
                            app.draw()
                            lay = app._tip_lay
                            self.assertTrue(inside(lay["bubble"]), lay["bubble"])
                            self.assertTrue(inside(lay["cbox"]), lay["cbox"])
                            self.assertFalse(overlap(lay["bubble"], lay["cbox"]))
                            for box in (lay["bubble"], lay["cbox"]):     # never over the buttons under the screen
                                self.assertLessEqual(box[1] + box[3], H - BAR + 8)


if __name__ == "__main__":
    unittest.main()
