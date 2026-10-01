"""The other voices and the drums on the device's own sound, the pedal that dies away and
lets go between phrases, the level of many notes together, the hardware test of a USB keyboard, the
voice of the keys and the lessons' settings on the device."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import wave
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


def wav(path, secs=0.2, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(value.to_bytes(2, "little", signed=True) * int(48000 * secs))


class Samples(unittest.TestCase):
    def test_every_family_and_the_drums_are_in_the_project_with_their_credit(self):
        mod = load_pi("armonico_pi_files")
        for folder in mod.FAMILIES[1:] + ("drums",):
            files = list((mod.GM_SAMPLES / folder).glob("*.flac"))
            self.assertTrue(files, folder)
        self.assertTrue((mod.GM_SAMPLES / "drums" / "76.flac").exists())      # the count-in's click
        self.assertIn("FluidR3", (ROOT / "NOTICE").read_text())
        self.assertIn("MIT", (mod.GM_SAMPLES / "SOURCE.md").read_text())


@unittest.skipIf(pygame is None, "pygame is not installed")
class SamplerVoices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = load_pi("armonico_pi_voices")
        self.mod.GM_DIR = Path(self.tmp.name) / "gm"
        wav(self.mod.GM_DIR / "02-organ" / "57.wav")
        wav(self.mod.GM_DIR / "drums" / "76.wav")
        import numpy as np
        mixer = self.mod.Mixer(self.mod.VOICES)
        s = object.__new__(self.mod.Sampler)
        s.__dict__.update(pg=pygame, sounds={60: np.ones(4800, "<i2")}, playing={}, mixer=mixer,
                          sustained=set(), pedal={}, master=1.0, started={}, level={}, fade={}, gain={},
                          limit=1.0, program={}, voice_of={}, smart=set(), last_on={}, gm_bytes=0, gm_size={},
                          gm_notes={}, chans=mixer.voices)
        import collections
        import threading
        s.gm, s.lock, s.gm_lock = collections.OrderedDict(), threading.Lock(), threading.Lock()
        self.s = s

    def tearDown(self):
        self.tmp.cleanup()

    def test_program_change_picks_the_family(self):
        s = self.s
        self.assertIs(s.sound_for(0, 60), s.sounds[60])               # no program: the grand piano
        s.midi(bytes([0xC0, 4]))                                      # electric piano: still the piano family
        self.assertIs(s.sound_for(0, 60), s.sounds[60])
        s.midi(bytes([0xC0, 19]))                                     # church organ: the organ voice
        snd = s.sound_for(0, 59)                                      # two semitones up from its 57
        self.assertIsNotNone(snd)
        self.assertAlmostEqual(len(snd) / self.mod.MIX_RATE, 0.2 / 2 ** (2 / 12), places=2)   # moved by resampling
        self.assertIsNone(s.sound_for(0, 57 + 13))                    # too far from any sample: silent
        s.midi(bytes([0xB0, 121, 0]))                                 # reset all controllers: the piano again
        self.assertIs(s.sound_for(0, 60), s.sounds[60])

    def test_the_drums_sound_and_the_count_in_with_them(self):
        s = self.s
        self.assertIsNotNone(s.sound_for(9, 76))
        self.assertIsNone(s.sound_for(9, 30))                         # not in the kit
        s.midi(bytes([0x99, 76, 70]))
        self.assertTrue(any(c.get_busy() for c in s.chans))
        self.assertEqual(s.playing, {})                               # a drum is not held

    def test_memory_stays_under_the_budget(self):
        s = self.s
        self.mod.GM_BUDGET = 60000
        s.midi(bytes([0xC0, 16]))
        for n in range(50, 64):
            s.sound_for(0, n)
        self.assertLessEqual(s.gm_bytes, 60000 + 40000)
        self.assertLess(len(s.gm), 14)

    def test_the_pedal_dies_away_and_lets_go_between_phrases(self):
        s, ch = self.s, self.mod.DEV_CH
        s.smart.add(ch)
        s.set_pedal(ch, True)
        s.note_on(ch, 60, 127)
        s.note_off(ch, 60)
        i = s.playing[(ch, 60)]
        self.assertIn(i, s.fade)                                      # rings on, but dies away
        self.assertEqual(s.fade[i][2], self.mod.PEDAL_DECAY)
        s.last_on[ch] = time.monotonic() - 1                          # a pause, then a new note
        s.note_on(ch, 60, 127)
        self.assertNotIn((ch, 60), s.sustained)
        s.note_off(ch, 60)
        # a note right after (a chord, a quick run) keeps the one before ringing
        s.note_on(ch, 60, 127)
        s.note_off(ch, 60)
        s.last_on[ch] = time.monotonic()
        s.sounds[62] = s.sounds[60]
        s.note_on(ch, 62, 127)
        self.assertIn((ch, 60), s.sustained)

    def test_a_voice_is_made_at_the_mixers_rate(self):
        s = self.s
        s.midi(bytes([0xC0, 16]))
        self.assertAlmostEqual(len(s.sound_for(0, 57)) / self.mod.MIX_RATE, 0.2, places=2)   # the same pitch

    def test_many_notes_are_turned_down_together(self):
        s = self.s
        for n in range(60, 72):
            s.sounds[n] = s.sounds[60]
            s.note_on(0, n, 127)
        want = (self.mod.LIMIT_VOICES / 12) ** 0.5                   # 12 held notes
        # one pass of the tick's level, as _tick does it
        with mock.patch.object(self.mod.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                s._tick()
        self.assertAlmostEqual(s.limit, want, places=3)


class DeviceTest(unittest.TestCase):
    def device(self):
        mod = load_pi("armonico_pi_test")
        d = object.__new__(mod.Device)
        d.sampler, d.port, d.pub = mock.Mock(), mock.Mock(), mock.Mock()
        d.port.here, d.port.there = True, False
        d.octave, d.set_octave = 4, mock.Mock()
        d.keys_on, d.kb, d.kb_seen = True, {"mode": "internal", "name": "", "out": "both"}, ["Digital Keyboard"]
        d.held, d.alarm, d.song, d.testing = {}, False, False, True
        return mod, d

    def test_the_12_keys_say_which_key_in_the_test(self):
        mod, d = self.device()
        d.kb["mode"] = "external"                                     # even with a USB keyboard chosen alone
        with mock.patch.object(mod, "load_settings", return_value={}):
            d.key(3, True)
        d.pub.assert_any_call("piano/device/test/key", "3")
        self.assertEqual(d.held, {3: 63})                             # and it plays, so it is heard

    def test_a_usb_keyboard_only_reports_in_the_test(self):
        mod, d = self.device()
        d.external(bytes([0x90, 61, 90]))
        d.pub.assert_called_once_with("piano/device/test/usb", json.dumps({"name": "Digital Keyboard", "note": 61}))
        d.port.send.assert_not_called()                               # not a press of the keys
        d.sampler.midi.assert_not_called()
        d.set_octave.assert_not_called()
        d.external(bytes([0x80, 61, 0]))                              # a key let go still goes on: nothing stays held
        self.assertEqual(d.port.send.call_count, 1)
        d.testing = False
        d.port.send.reset_mock()
        d.external(bytes([0x90, 61, 90]))                             # not chosen to play: connected for the test only
        d.port.send.assert_not_called()
        d.kb["mode"] = "both"
        d.external(bytes([0x90, 61, 90]))
        d.port.send.assert_called_once()


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_the_test_marks_keys_from_the_device_and_a_usb_keyboard_apart(self):
        app = T.make_app(self.mod)
        app.screen.pub = mock.Mock()
        app.open_test()
        app.follow_input()
        app.screen.pub.assert_any_call("piano/device/test", "on", retain=True)
        app.mqtt_event("piano/device/test/key", "5")
        app.mqtt_event("piano/device/test/usb", json.dumps({"name": "Digital Keyboard", "note": 60}))
        app.mqtt_event("piano/device/test/usb", json.dumps({"name": "Digital Keyboard", "note": 64}))
        self.assertEqual(app.screen.test["keys"], {5})
        self.assertEqual(app.screen.test["usb"], {"name": "Digital Keyboard", "note": 64, "n": 2})
        self.assertIn("E4", app.usb_test_line(app.screen.test))
        app.go("settings")
        app.follow_input()
        app.screen.pub.assert_any_call("piano/device/test", "off", retain=True)

    def test_the_voice_is_chosen_with_the_buttons(self):
        app = T.make_app(self.mod)
        app.screen.pub = mock.Mock()
        app.open_page("play")
        app.voice_adjust = True
        app.draw()
        app.press("up")
        app.screen.pub.assert_called_with("piano/device/voice/set", "1")
        app.press("down")
        app.press("down")
        app.screen.pub.assert_called_with("piano/device/voice/set", "15")
        app.mqtt_event("piano/device/voice", "7")
        self.assertEqual(app.voice(), 7)

    def test_lessons_page_sets_the_engine(self):
        app = T.make_app(self.mod)
        app.screen.engine.overview["settings"] = {"count_in": 8, "pace": "fast", "steps": ["numbers", "memory"]}
        app.open_page("less")
        cells = app.settings_cells()["less"]
        cells[0][3]()                                                 # 8 clicks, then off
        app.screen.engine.post.assert_called_with("/settings", {"count_in": 0})
        cells = app.settings_cells()["less"]
        self.assertEqual(cells[0][1], app.tr("c.countOff"))
        cells[1][3]()
        app.screen.engine.post.assert_called_with("/settings", {"pace": "relaxed"})
        app.settings_cells()["less"][3][3]()                          # keys lit on
        app.screen.engine.post.assert_called_with("/settings", {"steps": ["numbers", "keys", "memory"]})
        app.screen.engine.overview["settings"]["steps"] = ["memory"]
        app.screen.engine.post.reset_mock()
        app.settings_cells()["less"][4][3]()                          # the last one stays on
        app.screen.engine.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class SmoothMixer(unittest.TestCase):
    """A level never jumps at a block's border: SDL's mixer did, and every key let go crackled."""

    @unittest.skipIf(importlib.util.find_spec("numpy") is None, "numpy is not installed")
    def test_a_fade_and_a_level_change_move_sample_by_sample(self):
        import numpy as np
        mod = load_pi("armonico_pi_mixer")
        m = mod.Mixer(2)
        v = m.voices[0]
        v.set_volume(1.0)
        v.play(np.full(48000, 10000, "<i2"))
        out = np.concatenate([m.render(512) for _ in range(2)]).astype(float)
        v.set_volume(0.5)                                     # the limiter turning it down
        out = np.concatenate([out] + [m.render(512) for _ in range(2)]).astype(float)
        v.fadeout(50)                                         # a key let go
        out = np.concatenate([out] + [m.render(512) for _ in range(6)]).astype(float)
        self.assertLess(np.abs(np.diff(out)).max(), 30)       # 10000 on a constant: never a step
        self.assertEqual(out[-1], 0)
        self.assertFalse(v.get_busy())

    @unittest.skipIf(importlib.util.find_spec("numpy") is None, "numpy is not installed")
    def test_a_note_cut_for_another_dies_away(self):
        import numpy as np
        mod = load_pi("armonico_pi_mixer2")
        m = mod.Mixer(1)
        v = m.voices[0]
        v.play(np.full(48000, 10000, "<i2"))
        m.render(512)
        v.play(np.zeros(48000, "<i2"))                        # every voice busy: the oldest is taken
        out = m.render(512).astype(float)
        self.assertLess(np.abs(np.diff(out)).max(), 100)
        self.assertEqual(out[-1], 0)

    def test_the_card_is_sdls_default_opened_with_no_name(self):
        mod = load_pi("armonico_pi_card")
        lib = mock.Mock()
        lib.SDL_InitSubSystem.return_value = 0
        lib.SDL_OpenAudioDevice.return_value = 2
        with mock.patch("ctypes.CDLL", return_value=lib):
            out = mod.SdlOut(lambda n: bytes(n))
        # no name: SDL's ALSA backend lists only hw: cards, and a name must be in that list
        self.assertIsNone(lib.SDL_OpenAudioDevice.call_args.args[0])
        lib.SDL_PauseAudioDevice.assert_called_once_with(2, 0)
        self.assertEqual(out.id, 2)
        lib.SDL_OpenAudioDevice.return_value = 0
        lib.SDL_GetError.return_value = b"no card"
        with mock.patch("ctypes.CDLL", return_value=lib), self.assertRaises(RuntimeError):
            mod.SdlOut(lambda n: bytes(n))
