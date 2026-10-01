"""In a lesson the keys stay put. Key 1 is always C and the numbers on the keys never
jump; the key of the note the lesson waits for sounds at the song's own height; the corner shows the
octave the next note really sounds in, an arrow marks a note above or below, and a line shows the tune."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame
ROOT = Path(__file__).resolve().parent.parent


def load_pi(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "device" / "armonico_pi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KeysProgram(unittest.TestCase):
    def device(self):
        mod = load_pi("armonico_pi_v54")
        d = object.__new__(mod.Device)
        d.sampler, d.port, d.pub = mock.Mock(), mock.Mock(), mock.Mock()
        d.port.here, d.port.there = True, False
        d.octave, d.set_octave = 4, mock.Mock()
        d.keys_on, d.kb, d.kb_seen = True, {"mode": "internal", "name": "", "out": "both"}, []
        d.held, d.alarm, d.song, d.testing = {}, False, False, False
        return mod, d

    def test_the_key_of_the_note_expected_sounds_at_its_height(self):
        mod, d = self.device()
        d.expect = 72                                   # C5, the keys in octave 4
        with mock.patch.object(mod, "load_settings", return_value={}):
            d.key(0, True)                              # key 1, C
            d.key(2, True)                              # D: not the note expected, its own octave
        self.assertEqual(d.held, {0: 72, 2: 62})
        d.expect = 69                                   # the lesson moved on before C was let go
        with mock.patch.object(mod, "load_settings", return_value={}):
            d.key(0, False)
        d.sampler.note_off.assert_any_call(mod.DEV_CH, 72)   # let go as it was played: nothing left ringing

    def test_without_a_lesson_every_key_plays_its_own_octave(self):
        mod, d = self.device()
        d.expect = None
        with mock.patch.object(mod, "load_settings", return_value={}):
            d.key(0, True)
        self.assertEqual(d.held, {0: 60})

    def test_a_touch_is_let_go_as_it_was_played(self):
        mod, d = self.device()
        d.expect = 72
        d.touch_note(60, True)
        d.expect = None
        d.touch_note(60, False)
        sent = [c.args[0] for c in d.port.send.call_args_list]
        self.assertEqual(sent, [[0x90, 72, mod.VELOCITY], [0x80, 72, 0]])

    def test_a_usb_keyboard_does_not_move_the_octave_in_a_lesson(self):
        mod, d = self.device()
        d.kb["mode"] = "both"
        d.expect = 72
        d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_not_called()
        d.expect = None
        d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_called_once_with(6)


class Labels(unittest.TestCase):
    def test_the_numbers_on_the_12_keys_are_1_to_7(self):
        sys.path.insert(0, str(ROOT / "lessons"))
        try:
            import piano_game
        except ImportError as e:                        # pragma: no cover - mido or paho missing here
            self.skipTest(str(e))
        got = [piano_game.device_number(pc) for pc in range(12)]
        self.assertEqual(got, ["1", "1#", "2", "2#", "3", "4", "4#", "5", "5#", "6", "6#", "7"])


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def lesson(self, app, expect, mode="wait", show=True):
        app.screen.engine.state = dict(mode=mode, song="bella_ciao", title="Part 1", midi=[64, 69, 71, 72, 69],
                                       seq=["17", "20", "21", "22", "20"], expect=expect, show=show, total=5, pos=1,
                                       device_labels="num",
                                       device_key_labels=["1", "1#", "2", "2#", "3", "4", "4#", "5", "5#", "6", "6#", "7"])

    def test_the_keys_stay_in_the_part_octave_and_the_note_expected_is_sent(self):
        app = T.make_app(self.mod)
        s = app.screen
        s.pub = mock.Mock()
        self.lesson(app, 3)                             # C5 next, the part from E4
        s.follow_lesson()
        s.pub.assert_any_call("piano/device/expect", "72")
        self.assertEqual(s.octave, 4)
        self.assertFalse(any(c.args[0] == "piano/device/octave/set" for c in s.pub.call_args_list))
        self.lesson(app, 4)
        s.follow_lesson()
        s.pub.assert_called_with("piano/device/expect", "69")
        self.assertEqual(s.octave, 4)                   # the keys never moved
        s.send_expect(None)
        s.pub.assert_called_with("piano/device/expect", "")

    def test_a_part_lower_down_sets_the_octave_once(self):
        app = T.make_app(self.mod)
        s = app.screen
        s.pub = mock.Mock()
        app.screen.engine.state = dict(mode="wait", midi=[50, 55, 59], expect=0, show=True, total=3, pos=0)
        s.follow_lesson()
        s.pub.assert_any_call("piano/device/octave/set", "3")
        s.pub.reset_mock()
        s.follow_lesson()
        self.assertFalse(any(c.args[0] == "piano/device/octave/set" for c in s.pub.call_args_list))

    def test_the_lesson_draws_in_every_round_and_both_languages(self):
        for lang in ("en", "he"):
            app = T.make_app(self.mod, lang=lang)
            app.screen.pub = mock.Mock()
            for show in (True, "keys", False):
                for at in (0, 3, 4):
                    self.lesson(app, at, show=show)
                    app.screen.engine.state["msg"] = "Stuck. Note 4 is 22"
                    app.view = "lesson"
                    with self.subTest(lang=lang, show=show, at=at):
                        app.draw()

    def test_the_engine_message_names_the_key_by_its_own_number(self):
        app = T.make_app(self.mod)
        self.lesson(app, 3)
        app.screen.engine.state["msg"] = "Stuck. Note 4 is 22"
        app.view = "lesson"
        drawn = []
        real = app.cv.text
        with mock.patch.object(app.cv, "text", side_effect=lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]):
            app.draw()
        self.assertTrue(any("Note 4 is 8" in str(x) for x in drawn), drawn)        # the high C: key 8
        self.assertFalse(any("is 22" in str(x) for x in drawn))


if __name__ == "__main__":
    unittest.main()
