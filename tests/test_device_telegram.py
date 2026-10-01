"""The Telegram bot's token on the Armonico Pi: kept by root in its own file after the device's setup,
used by root for the fixed test message, never written to the screen's settings or handed back."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456789:" + "A" * 35


def root_actions():
    spec = importlib.util.spec_from_file_location("ra_tg", ROOT / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KeptToken(unittest.TestCase):
    def setUp(self):
        self.ra = root_actions()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "data" / "device").mkdir(parents=True)
        self.patches = [mock.patch.object(self.ra, "SECRETS", self.tmp / "etc" / "secrets.env"),
                        mock.patch.object(self.ra, "DATA_DIR", self.tmp / "data"),
                        mock.patch.object(self.ra, "TG_STAMP", self.tmp / "stamp")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.calls, self.out = [], []

    def tg(self, fail=False):
        def call(token, method, data=None):
            self.calls.append((token, method, data))
            if fail:
                raise RuntimeError(f"https://api.telegram.org/bot{token}/{method}: 401")
            return {"username": "piano_bot"}
        return mock.patch.object(self.ra, "tg_call", side_effect=call)

    def p(self, step, state="running", msg=""):
        self.out.append((state, msg))

    def chats(self, value):
        (self.tmp / "data" / "device" / "settings.json").write_text(json.dumps({"tg_chats": value}))

    def test_a_token_is_checked_then_kept_root_only(self):
        with self.tg():
            self.ra.do_telegram_keep(self.p, {"token": TOKEN})
        f = self.ra.SECRETS
        self.assertEqual(f.read_text(), f"TELEGRAM_TOKEN='{TOKEN}'\nTELEGRAM_ON_DEVICE='1'\n")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(self.calls[0][1], "getMe")

    def test_other_kept_tokens_stay(self):
        self.ra.SECRETS.parent.mkdir(parents=True)
        self.ra.SECRETS.write_text("HA_URL='http://ha:8123'\nHA_TOKEN='abc'\n")
        with self.tg():
            self.ra.do_telegram_keep(self.p, {"token": TOKEN})
        self.assertEqual(self.ra.secret_read("HA_TOKEN"), "abc")
        self.assertEqual(self.ra.secret_read("TELEGRAM_TOKEN"), TOKEN)

    def test_a_bad_or_refused_token_is_not_kept_and_not_repeated(self):
        for bad in ("", "abc", "123:short", TOKEN + "'\nX='y", "1234567:" + "a" * 30 + " x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.ra.do_telegram_keep(self.p, {"token": bad})
        with self.tg(fail=True), self.assertRaises(ValueError) as e:
            self.ra.do_telegram_keep(self.p, {"token": TOKEN})
        self.assertNotIn(TOKEN, str(e.exception), "the token never goes into the status file")
        self.assertFalse(self.ra.SECRETS.exists())

    def test_the_test_message_uses_the_kept_token_and_the_settings_chats_only(self):
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        self.chats([111, "-222", "x; rm", True, 3.5])
        with self.tg():
            self.ra.do_telegram_test(self.p, {"token": "999:ignored", "text": "anything"})
        sent = [(c[2]["chat_id"], c[2]["text"]) for c in self.calls]
        self.assertEqual(sent, [(111, "Armonico Pi is connected"), (-222, "Armonico Pi is connected")])
        self.assertTrue(all(c[0] == TOKEN for c in self.calls))
        self.assertEqual(json.loads(self.out[-1][1]), {"sent": 2})
        self.assertNotIn(TOKEN, json.dumps(self.out))

    def test_one_test_message_every_10_seconds(self):
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        self.chats([111])
        with self.tg():
            self.ra.do_telegram_test(self.p, {})
            with self.assertRaises(ValueError) as e:
                self.ra.do_telegram_test(self.p, {})
        self.assertEqual(str(e.exception), "too soon")
        old = time.time() - 11
        os.utime(self.ra.TG_STAMP, (old, old))
        with self.tg():
            self.ra.do_telegram_test(self.p, {})

    def test_no_token_or_no_chat_says_which(self):
        self.chats([111])
        with self.assertRaises(ValueError) as e:
            self.ra.do_telegram_test(self.p, {})
        self.assertEqual(str(e.exception), "no-token")
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        self.chats([])
        with self.assertRaises(ValueError) as e:
            self.ra.do_telegram_test(self.p, {})
        self.assertEqual(str(e.exception), "no-chats")

    def test_forget_removes_only_the_bot_token(self):
        self.ra.secret_write("HA_TOKEN", "abc")
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        self.ra.do_telegram_forget(self.p, {})
        self.assertEqual(self.ra.secret_read("TELEGRAM_TOKEN"), "")
        self.assertEqual(self.ra.secret_read("HA_TOKEN"), "abc")

    def test_the_actions_are_in_the_list(self):
        for a in ("telegram-keep", "telegram-test", "telegram-forget"):
            self.assertIn(a, self.ra.ACTIONS)


try:
    import pygame
except ImportError:
    pygame = None


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    def app(self, **settings):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_device_ui as T
        return T.make_app(T.load_screen(), settings=dict(tg_done=True, tg_chats=[111], **settings))

    def test_after_a_restart_the_test_goes_through_root(self):
        app = self.app(tg_kept=True)
        import ui.system as system
        with mock.patch.object(system, "request", return_value=(True, "")) as req, \
                mock.patch("threading.Thread", side_effect=lambda target, daemon: mock.Mock(start=target)):
            app.tg_test()
        req.assert_called_once_with("telegram-test", timeout=60)

    def test_without_a_kept_token_it_asks_for_one(self):
        app = self.app()
        import ui.system as system
        with mock.patch.object(system, "request") as req:
            app.tg_test()
        req.assert_not_called()

    def test_the_page_offers_to_forget_a_kept_token_and_asks_first(self):
        app = self.app(tg_kept=True, ha_done=True)
        with mock.patch.object(type(app), "local_now", lambda self: False):
            cells = app.settings_cells()["tg"]
            app.open_page("tg")
            app.draw()
        forget = [c for c in cells if c[0] == app.tr("tg.kept")]
        self.assertEqual(len(forget), 1)
        called = []
        app.tg_forget = lambda: called.append(1)
        forget[0][3]()
        self.assertTrue(app.confirm, "forgetting asks first")
        self.assertEqual(called, [])
        app.answer(True)
        self.assertEqual(called, [1])

if __name__ == "__main__":
    unittest.main()
