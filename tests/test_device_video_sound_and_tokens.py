"""The video's sound faded out before the player stops, no clock over the
video, and Home Assistant's token kept by root like the bot's."""
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 40
sys.path.insert(0, str(ROOT / "tests"))

try:
    import pygame
except ImportError:
    pygame = None


def root_actions():
    spec = importlib.util.spec_from_file_location("ra_v75", ROOT / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSink:
    def __init__(self):
        self.vol = []

    def find_property(self, name):
        return object() if name == "volume" else None

    def get_property(self, name):
        return 1.0

    def set_property(self, name, value):
        self.vol.append(value)


class FakePlayer:
    def __init__(self, state, sink):
        self.state, self.sink, self.log = state, sink, []

    def get_state(self, _):
        return (0, self.state, 0)

    def get_property(self, name):
        return self.sink

    def set_state(self, state):
        self.log.append(("state", state, list(self.sink.vol)))


@unittest.skipIf(pygame is None, "pygame is not installed")
class VideoClose(unittest.TestCase):
    def media(self, silent=False, playing=True):
        import test_device_ui as T
        mod = T.load_screen()
        m = mod.Media.__new__(mod.Media)
        m.gst = SimpleNamespace(State=SimpleNamespace(PLAYING="P", NULL="N"))
        sink = FakeSink()
        player = FakePlayer("P" if playing else "X", sink)
        m.gst_player, m.gst_sink, m.gst_bus, m.gst_sample_handler = player, None, None, None
        m.gst_silent = silent
        m.FADE_MS = 1                        # the steps' length is not what is tested
        return m, player, sink

    def test_the_sound_is_brought_to_zero_before_the_player_stops(self):
        m, player, sink = self.media()
        m._close_gst()
        self.assertEqual(len(sink.vol), m.FADE_STEPS)
        self.assertEqual(sink.vol[-1], 0.0)
        self.assertEqual(sink.vol, sorted(sink.vol, reverse=True))
        state = player.log[0]
        self.assertEqual(state[1], "N")
        self.assertEqual(state[2][-1], 0.0, "the stop came after the last step")

    def test_a_silent_or_not_playing_video_has_nothing_to_fade(self):
        for kw in ({"silent": True}, {"playing": False}):
            with self.subTest(kw):
                m, player, sink = self.media(**kw)
                m._close_gst()
                self.assertEqual(sink.vol, [])
                self.assertEqual(player.log[0][1], "N")

    def test_a_player_that_answers_nothing_is_still_stopped(self):
        m, player, sink = self.media()
        player.get_state = mock.Mock(side_effect=RuntimeError("gone"))
        m._close_gst()
        self.assertEqual(player.log[0][1], "N")

    def test_the_whole_fade_is_about_40_ms(self):
        import test_device_ui as T
        mod = T.load_screen()
        self.assertEqual(mod.Media.FADE_MS, 40)
        self.assertEqual(mod.Media.FADE_STEPS, 5)


class NoClockOverTheVideo(unittest.TestCase):
    def test_the_small_corner_clock_is_gone_and_the_picture_keeps_its_clock(self):
        src = (ROOT / "device" / "ui" / "views_main.py").read_text()
        self.assertNotIn("cv.text(self.hm(ms), 16, 26, 15", src)
        self.assertIn("cv.text(self.hm(ms), 22, 62, 46", src)


class KeptHomeAssistantToken(unittest.TestCase):
    def setUp(self):
        self.ra = root_actions()
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(self.ra, "SECRETS", self.tmp / "etc" / "secrets.env")
        p.start()
        self.addCleanup(p.stop)
        self.out = []
        self.opened = []

    def p(self, step, state="running", msg=""):
        self.out.append((state, msg))

    def open_ok(self, ok=True):
        def fake(url, token):
            self.opened.append((url, token))
            return (object(), "http://ha:8123") if ok else (None, None)
        return mock.patch.object(self.ra, "ha_open", side_effect=fake)

    def test_it_is_checked_with_home_assistant_then_kept_root_only(self):
        with self.open_ok():
            self.ra.do_ha_keep(self.p, {"token": TOKEN, "url": "http://ha:8123"})
        f = self.ra.SECRETS
        self.assertEqual(f.read_text(), f"HA_DEVICE_TOKEN='{TOKEN}'\nHA_DEVICE_URL='http://ha:8123'\n")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(self.opened, [("http://ha:8123", TOKEN)])
        self.assertEqual(json.loads(self.out[-1][1]), {"kept": True})

    def test_the_installers_own_keys_and_the_bots_token_stay(self):
        self.ra.secret_write("HA_TOKEN", "inst")
        self.ra.secret_write("TELEGRAM_TOKEN", "bot")
        with self.open_ok():
            self.ra.do_ha_keep(self.p, {"token": TOKEN, "url": "http://ha:8123"})
        self.assertEqual(self.ra.secret_read("HA_TOKEN"), "inst")
        self.assertEqual(self.ra.secret_read("TELEGRAM_TOKEN"), "bot")

    def test_a_bad_or_refused_token_or_address_is_not_kept(self):
        for token, url in (("", "http://ha:8123"), ("short", "http://ha:8123"), (TOKEN + "'\nX='y", "http://ha"),
                           (TOKEN, "ftp://ha"), (TOKEN, "http://ha'\nX='y"), (TOKEN, "")):
            with self.subTest(token=token[:12], url=url), self.open_ok(), self.assertRaises(ValueError):
                self.ra.do_ha_keep(self.p, {"token": token, "url": url})
        with self.open_ok(False), self.assertRaises(ValueError) as e:
            self.ra.do_ha_keep(self.p, {"token": TOKEN, "url": "http://ha:8123"})
        self.assertNotIn(TOKEN, str(e.exception))
        self.assertEqual(self.ra.secret_read("HA_DEVICE_TOKEN"), "")

    def test_kept_says_only_whether_and_forget_removes_only_these_keys(self):
        self.ra.secret_write("HA_TOKEN", "inst")
        self.ra.do_ha_kept(self.p, {})
        self.assertEqual(json.loads(self.out[-1][1]), {"kept": False})
        with self.open_ok():
            self.ra.do_ha_keep(self.p, {"token": TOKEN, "url": "http://ha:8123"})
        self.ra.do_ha_kept(self.p, {})
        self.assertEqual(json.loads(self.out[-1][1]), {"kept": True})
        self.assertNotIn(TOKEN, self.out[-1][1])
        self.ra.do_ha_forget(self.p, {})
        self.assertEqual(self.ra.secret_read("HA_DEVICE_TOKEN"), "")
        self.assertEqual(self.ra.secret_read("HA_DEVICE_URL"), "")
        self.assertEqual(self.ra.secret_read("HA_TOKEN"), "inst")

    def test_root_connects_the_bot_with_the_kept_token(self):
        tg = "123456789:" + "A" * 35
        with self.open_ok():
            self.ra.do_ha_keep(self.p, {"token": TOKEN, "url": "http://ha:8123"})
        mod = mock.Mock()
        mod.telegram_connect.return_value = (True, "entry")
        with self.open_ok(), mock.patch.object(self.ra, "ha_module", return_value=mod):
            self.ra.do_ha_telegram_connect(self.p, {"token": tg, "chats": [111]})
        self.assertEqual(mod.telegram_connect.call_args[0][1:], (tg, [111]))
        self.assertEqual(self.opened[-1], ("http://ha:8123", TOKEN))
        mod.telegram_connect.return_value = (False, "stopped")
        with self.open_ok(), mock.patch.object(self.ra, "ha_module", return_value=mod), self.assertRaises(ValueError):
            self.ra.do_ha_telegram_connect(self.p, {"token": tg, "chats": [111]})

    def test_connecting_without_a_kept_token_or_with_bad_input_is_refused(self):
        tg = "123456789:" + "A" * 35
        with self.assertRaises(ValueError) as e:
            self.ra.do_ha_telegram_connect(self.p, {"token": tg, "chats": [1]})
        self.assertEqual(str(e.exception), "no-token")
        for chats in (None, [], ["1"], [True], list(range(11))):
            with self.subTest(chats=chats), self.assertRaises(ValueError):
                self.ra.do_ha_telegram_connect(self.p, {"token": tg, "chats": chats})

    def test_the_bots_token_is_marked_as_kept_on_the_device(self):
        with mock.patch.object(self.ra, "tg_call", return_value={}):
            self.ra.do_telegram_keep(self.p, {"token": "123456789:" + "A" * 35})
        self.assertEqual(self.ra.secret_read("TELEGRAM_ON_DEVICE"), "1")
        self.ra.do_telegram_forget(self.p, {})
        self.assertEqual(self.ra.secret_read("TELEGRAM_ON_DEVICE"), "")

    def test_the_actions_are_in_the_list(self):
        for a in ("ha-keep", "ha-kept", "ha-forget", "ha-telegram-connect"):
            self.assertIn(a, self.ra.ACTIONS)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    def app(self, **settings):
        import test_device_ui as T
        return T.make_app(T.load_screen(), settings=dict(settings))

    def test_a_kept_token_means_the_bot_setup_does_not_ask_for_it(self):
        app = self.app(ha_done=True, ha_kept=True)
        app.go("settings")
        app.tg["token"] = "123:abc"
        app.ha.update(token="", token_mem="")
        import ui.system as system
        with mock.patch.object(system, "request", return_value=(True, "")), \
                mock.patch.object(type(app), "start_job", lambda self, *a: a) as sj:
            got = app.run_tg_setup()
        self.assertNotEqual(app.view, "edit")
        self.assertIsNotNone(got)

    def test_without_a_kept_token_it_still_asks(self):
        app = self.app(ha_done=True)
        app.go("settings")
        app.tg["token"] = "123:abc"
        app.ha.update(token="", token_mem="")
        self.assertIsNone(app.run_tg_setup())
        self.assertEqual(app.view, "edit")
        app.close_paste()

    def test_the_ha_page_offers_to_forget_a_kept_token_and_asks_first(self):
        app = self.app(ha_done=True, ha_kept=True)
        with mock.patch.object(type(app), "local_now", lambda self: False):
            cells = app.settings_cells()["ha"]
        kept = [c for c in cells if c[0] == app.tr("tg.kept")]
        self.assertEqual(len(kept), 1)
        import ui.system as system
        with mock.patch.object(system, "request", return_value=(True, "")) as req, \
                mock.patch("threading.Thread", side_effect=lambda target, daemon: mock.Mock(start=target)):
            kept[0][3]()
            self.assertTrue(app.confirm, "forgetting asks first")
            req.assert_not_called()
            app.answer(True)
        req.assert_called_once_with("ha-forget", timeout=30)
        self.assertFalse(app.st.get("ha_kept"))

    def test_the_page_has_no_forget_row_without_a_kept_token(self):
        app = self.app(ha_done=True)
        with mock.patch.object(type(app), "local_now", lambda self: False):
            cells = app.settings_cells()["ha"]
        self.assertEqual([c for c in cells if c[0] == app.tr("tg.kept")], [])


class InstallerKeepsDeviceTokens(unittest.TestCase):
    def run_no(self, content):
        import subprocess
        import test_installer as T
        tmp = Path(tempfile.mkdtemp())
        (tmp / "secrets.env").write_text(content)
        script = (T.functions("secret_get", "secret_drop") +
                  '\nSECRETS="$T/secrets.env"\n' +
                  'secret_drop HA_URL; secret_drop HA_TOKEN\n'
                  '[ -n "$(secret_get TELEGRAM_ON_DEVICE)" ] || secret_drop TELEGRAM_TOKEN\n'
                  '[ -s "$SECRETS" ] || rm -f "$SECRETS"\n')
        src = (ROOT / "install.sh").read_text()
        self.assertIn('secret_drop HA_URL; secret_drop HA_TOKEN', src)
        self.assertNotIn('rm -f "$SECRETS"        # an earlier answer', src)
        r = subprocess.run(["bash", "-c", script], env={"T": str(tmp), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        f = tmp / "secrets.env"
        return f.read_text() if f.exists() else None

    def test_no_withdraws_the_installers_tokens_only(self):
        got = self.run_no("HA_URL='u'\nHA_TOKEN='a'\nTELEGRAM_TOKEN='b'\n")
        self.assertIsNone(got)
        got = self.run_no("HA_URL='u'\nHA_TOKEN='a'\nHA_DEVICE_TOKEN='d'\nHA_DEVICE_URL='x'\n"
                          "TELEGRAM_TOKEN='b'\nTELEGRAM_ON_DEVICE='1'\n")
        self.assertEqual(got, "HA_DEVICE_TOKEN='d'\nHA_DEVICE_URL='x'\nTELEGRAM_TOKEN='b'\nTELEGRAM_ON_DEVICE='1'\n")


if __name__ == "__main__":
    unittest.main()
