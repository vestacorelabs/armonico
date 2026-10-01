"""The 13th key of the Armonico Pi: a high C, one octave above the first C (GPIO 13). The device has 13 keys
from C to C, always."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "device"))
import test_device_ui as T                          # noqa: E402  (sets the dummy SDL drivers)
import test_device_ui_audit as A                    # noqa: E402
import fullcheck as F                               # noqa: E402

PINS = [15, 10, 17, 9, 18, 27, 25, 22, 11, 23, 8, 24, 13]
pygame = T.pygame


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Wiring(unittest.TestCase):
    def test_the_default_is_13_pins(self):
        pi = load("pi_13", "device/armonico_pi.py")
        self.assertEqual(pi.KEY_PINS, PINS)
        self.assertEqual(len(set(pi.KEY_PINS) | {int(p) for p in pi.BUTTON_PINS.values()}), 17, "no pin twice")
        keys, btns = F.wiring({})
        self.assertEqual(keys, PINS)
        self.assertEqual(btns, {"menu": 7, "down": 5, "up": 6, "ok": 12})

    def test_the_device_always_has_13_keys(self):
        sc = T.load_screen()
        self.assertEqual(sc.key_total(), 13)


class Pitch(unittest.TestCase):
    def setUp(self):
        self.pi = load("pi_13b", "device/armonico_pi.py")
        self.d = object.__new__(self.pi.Device)
        self.d.octave = 4

    def test_key_12_is_the_same_note_one_octave_up(self):
        self.assertEqual(self.d.base() + 12, 72)
        self.assertEqual(self.d.at_height(self.d.base() + 12), 72)
        self.assertEqual(self.d.at_height(self.d.base()), 60)

    def test_key_12_sounds_the_high_c_when_a_key_is_pressed(self):
        d = self.d
        d.held, d.alarm, d.song, d.keys_on, d.kb = {}, False, False, True, {}
        d.expect = d.expect_at = None
        with mock.patch.object(d, "sound_note") as sn, mock.patch.object(d, "broker_up", return_value=True), \
                mock.patch.object(d, "pub") as pub:
            d.key(12, True)
            d.key(12, False)
        self.assertEqual([c.args for c in sn.call_args_list], [(72, True), (72, False)])
        pub.assert_called_once_with("piano/device/key", "12")
        self.assertLessEqual(d.base() + 12, self.pi.HIGH)

    def test_in_a_lesson_both_c_keys_are_number_1_at_the_song_s_own_height(self):
        d = self.d
        d.expect, d.expect_at = 84, self.pi.time.monotonic()      # C6 is what the lesson waits for
        self.assertEqual(d.at_height(60), 84)                     # key 0
        self.assertEqual(d.at_height(72), 84)                     # key 12
        d.expect = 67                                             # a G: key 12 is a plain C above
        self.assertEqual(d.at_height(72), 72)

    def test_stop_button_may_be_the_high_c_key(self):
        sc = T.load_screen()
        self.assertIn("key:12", sc.Screen.STOP_BUTTONS)
        self.assertEqual(sc.Screen.BUTTON_NAMES["key:12"], "high C key")


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_the_lesson_lights_the_high_c_only_for_the_c_above_the_keys(self):
        app = T.make_app(self.mod)                                # octave 4: the keys start at C4 (60)
        self.assertEqual([app.lit_key(n) for n in (60, 62, 71, 72, 84, 48)], [0, 2, 11, 12, 0, 0])
        self.assertEqual(app.screen.lit_key(72), 12)

    def test_the_lesson_draws_8_white_keys_and_the_arrow_stays_off_the_high_c(self):
        app = T.make_app(self.mod)
        app.screen.engine.state = {"mode": "wait", "midi": [60, 72], "expect": 1, "show": True, "total": 2, "pos": 1,
                                   "device_labels": "num",
                                   "device_key_labels": ["1", "1#", "2", "2#", "3", "4", "4#", "5", "5#", "6", "6#", "7"]}
        with mock.patch.object(app, "key_arrow") as arrow:
            app.view = "lesson"
            app.draw()
        arrow.assert_not_called()
        self.assertEqual(len([r for r in app.key_rects if r[0] in (0, 2, 4, 5, 7, 9, 11, 12)]), 8)
        self.assertEqual(len(app.key_rects), 13)

    def test_the_drawing_has_8_white_and_5_black_keys_the_13th_at_the_right_end(self):
        app = T.make_app(self.mod)
        app.go("free")
        app.draw()
        rects = app.key_rects
        self.assertEqual(len(rects), 13)
        whites = [r for r in rects if r[0] in (0, 2, 4, 5, 7, 9, 11, 12)]
        blacks = [r for r in rects if r[0] in (1, 3, 6, 8, 10)]
        self.assertEqual((len(whites), len(blacks)), (8, 5))
        self.assertEqual(max(whites, key=lambda r: r[1])[0], 12)
        self.assertEqual(rects[[r[0] for r in rects].index(12)][3], 78)

    def test_touching_the_high_c_plays_the_c_an_octave_up(self):
        app = T.make_app(self.mod)
        app.go("free")
        app.screen.pub = mock.Mock()
        app.free_key(12)
        app.screen.pub.assert_any_call("piano/device/note", "on 72")
        self.assertIn(12, app.screen.flash)
        self.assertEqual(app.free_label(12), "C5")

    def test_the_hardware_test_counts_13_keys_and_shows_the_gpio_of_the_13th(self):
        app = T.make_app(self.mod)
        app.open_test()
        seen = A.texts(app)
        self.assertIn("GPIO 13", [t[0] for t in seen])
        self.assertIn(app.tr("keysOf", n=0, t=13), [t[0] for t in seen])
        self.assertEqual(app.tr("keysOf", n=13, t=13), "Physical keys 13 of 13")
        for k in range(12):
            app.mqtt_event("piano/device/test/key", str(k))
        self.assertEqual(len(app.screen.test["keys"]), 12)
        self.assertIn(app.tr("keysOf", n=12, t=13), [t[0] for t in A.texts(app)], "12 of 13 is not done")
        app.mqtt_event("piano/device/test/key", "12")
        self.assertEqual(app.screen.test["keys"], set(range(13)))
        self.assertIn("Physical keys 13 of 13", [t[0] for t in A.texts(app)])

    def test_the_full_check_names_the_high_c_in_both_languages(self):
        self.assertEqual(F.key_name(0), "1 (C)")
        self.assertEqual(F.key_name(7), "5 (G)")
        n = F.key_name(12)
        self.assertEqual(str(n), "1 (C, high)")
        self.assertEqual(n.he, "1 (דו גבוה)")
        en, he = F.L("chk.r.held", k=n)
        self.assertEqual(en, "key 1 (C, high) held down all the time")
        self.assertIn("גבוה", he)
        self.assertNotIn("high", he)
        en, he = F.L("chk.d.held", k=F.key_names([3, 12]))
        self.assertIn("2# (D#)", en)
        self.assertTrue(en.endswith("1 (C, high)") and he.endswith("1 (דו גבוה)"))

    def test_the_check_step_wants_all_13_keys(self):
        app = T.make_app(self.mod)
        app.open_check("settings")
        app.chk["steps"] = ["keys"]
        app.chk["step"] = 0
        app.chk["phase"], app.chk["st"], app.chk["res"] = "guided", {}, {}
        with mock.patch.object(app, "chk_testing", return_value=True):
            for k in range(12):
                app.chk_key(k)
            self.assertNotIn("keys", app.chk["res"])
            app.chk_key(12)
        self.assertEqual(app.chk["res"]["keys"].level, F.OK)
        self.assertEqual(app.chk["res"]["keys"].row[0], "13 of 13")

    def legacy(self):
        """The first screen module (device/screen.py) with what draw_test needs, text recorded."""
        app = T.make_app(self.mod)
        sc = app.screen
        sc.f = {}
        sc.test = {"keys": {0, 12}, "buttons": {}, "taps": [], "speaker": ""}
        sc.flash.clear()
        said = []
        sc.text = lambda s, *a, **k: said.append((str(s), a))
        return sc, said

    def test_the_first_screen_module_draws_13_cells_with_high_c_and_its_gpio(self):
        sc, said = self.legacy()
        sc.draw_test()
        words = [s for s, _ in said]
        self.assertEqual([w for w in words if w in ("C", "high")].count("high"), 1)
        self.assertEqual(words.count("C"), 2, "C and the high C")
        self.assertIn("GPIO 13", words)
        self.assertIn("Keys 2 of 13", words)
        self.assertNotIn("C'", words)


    def test_a_press_flashes_only_the_key_that_was_pressed(self):
        app = T.make_app(self.mod)
        sc = app.screen
        sc.octave = 4
        for pressed in (0, 12):
            sc.flash.clear()
            sc.last_live, sc.touch_sent = 0, []
            sc.engine.state = {"mode": "wait", "midi": [72], "expect": 0, "volume": 70, "live": [(pressed + 1, 72)]}
            sc.handle("piano/device/key", str(pressed))      # the lesson sounds key 0 and key 12 as the same C5
            sc.follow_keys()
            self.assertEqual(set(sc.flash), {pressed}, f"key {pressed}")
        sc.flash.clear()
        sc.key_pressed = (0, sc.key_pressed[1] - 5)           # an old press: the note decides (C5 is the high C)
        sc.last_live = 0
        sc.engine.state["live"] = [(9, 72)]
        sc.follow_keys()
        self.assertEqual(set(sc.flash), {12})

    def test_a_slide_over_a_key_does_not_replay_it_even_when_the_octave_moves(self):
        app = T.make_app(self.mod)
        app.go("free")
        app.draw()
        app.screen.pub = mock.Mock()
        r = {k[0]: k for k in app.key_rects}
        app.free_key(12)
        app.screen.octave = 5                                  # the octave moved under the finger
        who = getattr(app, "pointer_id", "mouse")
        px, py = r[12][1] + 10, r[12][2] + 100
        with mock.patch.object(app.cv, "to_plan", return_value=(px, py)), mock.patch.object(app, "free_key") as fk:
            app.pointer_move(0, 0)
            fk.assert_not_called()
            x0 = r[0]
            with mock.patch.object(app.cv, "to_plan", return_value=(x0[1] + 10, x0[2] + 120)):
                app.pointer_move(0, 0)
            fk.assert_called_once_with(0)

    def test_the_check_s_small_keyboard_draws_8_white_and_5_black(self):
        app = T.make_app(self.mod)
        rects = []
        orig = type(app.cv).rect

        def rec(cv, x, y, w, h, *a, **k):
            rects.append((w, h))
            return orig(cv, x, y, w, h, *a, **k)
        with mock.patch.object(type(app.cv), "rect", rec):
            app.chk_piano({0, 12}, 196)
        self.assertEqual(rects.count((60, 120)), 8)
        self.assertEqual(rects.count((40, 72)), 5)


if __name__ == "__main__":
    unittest.main()
