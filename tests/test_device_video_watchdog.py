"""The video watchdog, the direct frame copy, quick repeat taps, the rest screen's still
layer and the figure that hops only when touched."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame


@unittest.skipIf(pygame is None, "pygame is not installed")
class Watchdog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def media(self):
        m = self.mod.Media(pygame)
        m.gst_player = mock.Mock()
        m.gst_player.get_state.return_value = (None, mock.Mock(value_nick="paused"), None)
        m.gst_loop, m.gst_loop_restart_at, m.playing = True, None, self.mod.REST_VIDEO
        m.gst_silent = False
        m._close_gst = mock.Mock()
        m._play_video = mock.Mock()
        return m

    def test_no_picture_starts_again_then_without_sound(self):
        m = self.media()
        m._watchdog()                                  # the first look: the clock starts
        m.wd[1] -= m.STALL_SECONDS + 0.1
        m._watchdog()
        m._play_video.assert_called_with(self.mod.REST_VIDEO, True, silent=False)
        m.wd[1] -= m.STALL_SECONDS + 0.1
        m._watchdog()
        m._play_video.assert_called_with(self.mod.REST_VIDEO, True, silent=True)

    def test_frames_keep_it_quiet(self):
        m = self.media()
        m._watchdog()
        m.seq = 5
        m.wd[1] -= m.STALL_SECONDS + 0.1
        m._watchdog()                                  # a new frame came: no restart
        m._play_video.assert_not_called()

    def test_gives_up_after_three_tries(self):
        m = self.media()
        m._watchdog()
        for _ in range(4):
            m.wd[1] -= m.STALL_SECONDS + 0.1
            m._watchdog()
        self.assertIsNone(m.playing)
        self.assertIn("no picture", m.status)

    def test_frame_written_straight_into_the_screen(self):
        m = self.mod.Media(pygame)
        m.native, m.size = True, (8, 2)
        m.frame = bytes(range(64))
        target = pygame.Surface((8, 2), 0, 32)
        if target.get_pitch() != 32:
            self.skipTest("padded rows here")
        self.assertTrue(m.paint_into(target))
        self.assertEqual(bytes(target.get_buffer().raw)[:64], bytes(range(64)))
        self.assertFalse(m.paint_into(pygame.Surface((4, 2), 0, 32)))      # another size: not this way


@unittest.skipIf(pygame is None, "pygame is not installed")
class Taps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_two_quick_clicks_on_a_key_are_two_notes(self):
        app = T.make_app(self.mod)
        app.go("free")
        app.draw()
        pubs = []
        app.screen.pub = lambda topic, payload, retain=False: pubs.append(payload)
        key = next(h for h in app.hits if h[1] > 140)          # a key of the keyboard
        x, y = app.cv.ox + (key[0] + key[2] / 2) * app.cv.scale, app.cv.oy + (key[1] + key[3] / 2) * app.cv.scale
        app.pointer_id = "mouse"
        for _ in range(2):
            app.pointer_down(x, y)
            app.pointer_up(x, y)
        self.assertEqual(sum(1 for p in pubs if p.startswith("on ")), 2)

    def test_one_touch_seen_as_finger_and_mouse_is_one_note(self):
        app = T.make_app(self.mod)
        app.go("free")
        app.draw()
        pubs = []
        app.screen.pub = lambda topic, payload, retain=False: pubs.append(payload)
        key = next(h for h in app.hits if h[1] > 140)
        x, y = app.cv.ox + (key[0] + key[2] / 2) * app.cv.scale, app.cv.oy + (key[1] + key[3] / 2) * app.cv.scale
        app.pointer_id = 3
        app.pointer_down(x, y)
        app.pointer_id = "mouse"
        app.pointer_down(x, y)
        self.assertEqual(sum(1 for p in pubs if p.startswith("on ")), 1)


@unittest.skipIf(pygame is None, "pygame is not installed")
class StillLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_layer_kept_between_frames_with_its_buttons(self):
        for theme in ("classic", "sky"):
            app = T.make_app(self.mod, (1920, 1080), "he", settings={"rest": "animation"})
            app.st["theme"] = theme
            app.go("rest")
            app.draw()
            first = list(app._layers.values())[0]
            hits = len(app.hits)
            self.assertGreater(hits, 4)                 # the 4 buttons and the song card at least
            app.draw()
            self.assertIs(list(app._layers.values())[0], first)    # the same layer, not drawn again
            self.assertEqual(len(app.hits), hits)
            app.layer_dirty()
            app.draw()
            self.assertIsNot(list(app._layers.values())[0], first)

    def test_layer_drawn_again_when_old(self):
        app = T.make_app(self.mod, settings={"rest": "animation"})
        app.go("rest")
        app.draw()
        L = list(app._layers.values())[0]
        L["at"] -= app.LAYER_MAX_AGE + 1
        app.draw()
        self.assertIsNot(list(app._layers.values())[0], L)

    def test_not_over_the_video(self):
        app = T.make_app(self.mod, settings={"rest": "video"})
        app.screen.media.frame = b"x"
        app.go("rest")
        self.assertFalse(app.layer_ok())


if __name__ == "__main__":
    unittest.main()


class SystemSongs(unittest.TestCase):
    """A restore keeps the songs that come with Armonico (setup/music) and puts back missing ones."""

    def test_full_restore_keeps_and_reseeds_them(self):
        import tempfile
        import zipfile
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device"))
        import backup
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            data, etc, app = t / "data", t / "etc", t / "app"
            (app / "setup" / "music").mkdir(parents=True)
            (data / "songs").mkdir(parents=True)
            etc.mkdir()
            for n in ("fur_elise.mid", "ode_to_joy.mid"):
                (app / "setup" / "music" / n).write_bytes(b"MThd-system")
            (data / "songs" / "fur_elise.mid").write_bytes(b"MThd-system")
            (data / "songs" / "mine.mid").write_bytes(b"MThd-mine")
            paths = backup.Paths(data, etc, data / "songs", app)
            src = t / "b.zip"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("data/songs/other.mid", b"MThd-other")      # a backup without the system songs
            backup.apply_data(src, paths, partial=False)
            names = sorted(p.name for p in (data / "songs").iterdir())
            self.assertEqual(names, ["fur_elise.mid", "ode_to_joy.mid", "other.mid"])   # mine.mid: not in it


@unittest.skipIf(pygame is None, "pygame is not installed")
class Samples(unittest.TestCase):
    def test_mono_48k_loaded_in_the_players_format(self):
        import tempfile
        import wave
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device"))
        import armonico_pi
        try:
            s = object.__new__(armonico_pi.Sampler)
            s.pg = pygame
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "60.wav"
                with wave.open(str(f), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(48000)
                    w.writeframes(b"\x01\x00\x02\x00" * 2400)        # 4800 frames, 0.1 s
                snd = s._sound(f)
                self.assertEqual(len(snd), 4800)                     # kept once, mono: both sides at play time
                self.assertEqual(snd[:4].tolist(), [1, 2, 1, 2])
        finally:
            pass


@unittest.skipIf(pygame is None, "pygame is not installed")
class AuditFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def test_question_redraws_the_rest_layer(self):
        app = T.make_app(self.mod, settings={"rest": "animation"})
        app.go("rest")
        app.draw()
        first = list(app._layers.values())[0]
        app.ask("?", lambda: None)
        app.draw()
        self.assertIsNot(list(app._layers.values())[0], first)
        app.confirm = None                              # timed out
        app.draw()
        menu = [fn for _i, _l, fn in app.bar_items][0]
        self.assertIsNotNone(menu)

    def test_lesson_over_the_nasa_picture_is_not_covered(self):
        app = T.make_app(self.mod, settings={"rest": "nasa"})
        app.apod.image = pygame.Surface((10, 10))
        app.go("rest")
        app.screen.lesson_on = lambda: True
        app.background(1.0)
        self.assertFalse(app.covered)

    def test_update_screen_opens_from_the_loop(self):
        app = T.make_app(self.mod)
        app.open_page("sys")
        app.upd_due = True
        app.follow_upd()
        self.assertEqual(app.view, "upd")


@unittest.skipIf(pygame is None, "pygame is not installed")
class FallbackScreens(unittest.TestCase):
    """The previous screens, which run when device/ui does not load, still draw every view and take
    touches and buttons (run in a child process: it breaks the ui package on purpose)."""

    def test_every_view_draws(self):
        import subprocess
        import tempfile
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        script = f"""
import os, sys, types, importlib.util, random
sys.path.insert(0, {str(root / 'device')!r})
bad = types.ModuleType("ui.app")
def broken(name):
    raise ImportError("broken on purpose")
bad.__getattr__ = broken
sys.modules["ui.app"] = bad
import pygame
spec = importlib.util.spec_from_file_location("scr", {str(root / 'device' / 'screen.py')!r})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.Screen.start_mqtt = lambda self: None
pg = mod.open_display()
sc = mod.Screen(pg)
assert sc.ui is None
for view in ("rest", "songs", "free", "settings", "test"):
    sc.go(view) if view == "test" else setattr(sc, "view", view)
    for i in range(8):
        sc.draw()
        sc.tap(random.randrange(800), random.randrange(480))
        sc.handle("piano/device/button", "down:press")
sc.engine.state.update(mode="wait", midi=[60, 62, 64], expect=1, show=True, total=3, pos=1)
sc.draw()
print("FALLBACK OK")
os._exit(0)
"""
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", DATA_DIR=tempfile.mkdtemp())
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=env)
        self.assertIn("FALLBACK OK", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])


@unittest.skipIf(pygame is None, "pygame is not installed")
class NoHdmiNoHdmiOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def outs(self, here):
        app = T.make_app(self.mod, settings={"sound_out": "hdmi"})
        app._hdmi_seen = (1e18, here)                  # looked at "now": no sysfs here
        return app

    def test_offered_outputs(self):
        self.assertEqual(self.outs(False).outputs_here(), ("aux", "off"))
        # self.assertEqual(self.outs(True).outputs_here(), ("aux", "hdmi", "both", "off"))    # HDMI off (DSI only)
        self.assertEqual(self.outs(True).outputs_here(), ("aux", "off"))

    def test_kept_hdmi_shows_as_aux_without_a_screen(self):
        self.assertEqual(self.outs(False).output_shown, "aux")
        # self.assertEqual(self.outs(True).output_shown, "hdmi")    # HDMI off (DSI only)
        self.assertEqual(self.outs(True).output_shown, "aux")

    def test_settings_text_has_no_hdmi(self):
        app = self.outs(False)
        cells = app.settings_cells()["sound"]
        self.assertNotIn("HDMI", " ".join(str(c[1]) + str(c[2]) for c in cells))
