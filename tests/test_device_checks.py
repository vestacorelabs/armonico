"""The device's safety and test tools: judging the note on the device's keys, asking before what cannot
be undone, the hardware test, the settings lock, and the checks of armonico status."""
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_game as game          # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SameKey(unittest.TestCase):
    def test_full_keyboard_needs_the_exact_key(self):
        with mock.patch.object(game, "IS_DEVICE", False):
            self.assertTrue(game.same_key(60, 60))
            self.assertFalse(game.same_key(48, 60))

    def test_device_takes_the_note_in_any_octave(self):
        with mock.patch.object(game, "IS_DEVICE", True):
            self.assertTrue(game.same_key(48, 60))       # C3 for C4: the octave had not moved yet
            self.assertFalse(game.same_key(61, 60))
            self.assertFalse(game.same_key(None, 60))


class FakeRect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.center = (x + w // 2, y + h // 2)
        self.left, self.right, self.top, self.bottom = x, x + w, y, y + h

    def collidepoint(self, x, y):
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h


class FakePg:
    Rect = FakeRect


class Confirm(unittest.TestCase):
    def setUp(self):
        self.mod = load("armonico_screen_checks_test", "device/screen.py")
        sc = self.sc = object.__new__(self.mod.Screen)
        sc.pg, sc.confirm, sc.toast, sc.key_hold = FakePg(), None, ("", 0), False
        sc.last_tap_at, sc.last_tap_pos, sc.summary_until, sc.view = 0.0, None, 0, "settings"
        sc.engine = mock.Mock(state={"mode": "idle"})
        sc.done = []

    def ask(self):
        self.mod.Screen.ask(self.sc, "Sure?", "", lambda: self.sc.done.append(1))

    def test_ok_says_yes_and_other_buttons_cancel(self):
        self.ask()
        self.mod.Screen.button(self.sc, "down", False)
        self.assertEqual(self.sc.done, [])
        self.assertIsNone(self.sc.confirm)
        self.ask()
        self.mod.Screen.button(self.sc, "ok", True)       # the hold of the press that asked
        self.assertEqual(self.sc.done, [])
        self.mod.Screen.button(self.sc, "ok", False)
        self.assertEqual(self.sc.done, [1])

    def test_touch_beside_the_question_does_nothing(self):
        self.ask()
        self.mod.Screen.tap(self.sc, 5, 5)
        self.assertIsNotNone(self.sc.confirm)
        _, yes, _ = self.mod.Screen.confirm_rects(self.sc)
        time.sleep(0.31)
        self.mod.Screen.tap(self.sc, *yes.center)
        self.assertEqual(self.sc.done, [1])

    def test_unanswered_question_runs_nothing(self):
        self.ask()
        self.sc.confirm["until"] = time.time() - 1
        self.mod.Screen.draw_confirm(self.sc)
        self.assertIsNone(self.sc.confirm)
        self.assertEqual(self.sc.done, [])

    def test_leaving_a_lesson_asks_first(self):
        self.sc.engine.state["mode"] = "wait"
        self.mod.Screen.button(self.sc, "menu", True)
        self.sc.engine.post.assert_not_called()
        self.mod.Screen.button(self.sc, "ok", False)
        self.sc.engine.post.assert_called_once_with("/lessonstop")


class HardwareTest(unittest.TestCase):
    def setUp(self):
        self.mod = load("armonico_screen_hwtest", "device/screen.py")
        sc = self.sc = object.__new__(self.mod.Screen)
        sc.confirm, sc.key_hold, sc.view, sc.flash = None, False, "test", {}
        sc.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": ""}
        sc.engine = mock.Mock(state={"mode": "idle"})
        sc.go = mock.Mock()

    def test_buttons_light_up_and_do_not_navigate(self):
        for name in ("menu", "down", "up", "ok"):
            self.mod.Screen.button(self.sc, name, False)
        self.assertEqual(set(self.sc.test["buttons"]), {"menu", "down", "up", "ok"})
        self.sc.go.assert_not_called()
        self.mod.Screen.button(self.sc, "menu", True)
        self.sc.go.assert_called_once_with("settings")

    def test_up_and_down_stay_in_the_test(self):
        self.mod.Screen.move(self.sc, 1)
        self.mod.Screen.move(self.sc, -1)
        self.sc.go.assert_not_called()


class SettingsLock(unittest.TestCase):
    def test_writers_from_two_threads_keep_every_change(self):
        tmp = Path(tempfile.mkdtemp())
        mod = load("armonico_pi_lock_test", "device/armonico_pi.py")
        with mock.patch.multiple(mod, DEV_DIR=tmp, SETTINGS_FILE=tmp / "settings.json"):
            threads = [threading.Thread(target=lambda k=k: [mod.save_settings(**{f"{k}{i}": i}) for i in range(30)])
                       for k in "ab"]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            data = json.loads((tmp / "settings.json").read_text())
        self.assertEqual(len(data), 60)


class StatusChecks(unittest.TestCase):
    def test_songs_that_cannot_be_lessons_are_named_with_the_reason(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"songs": [{"name": "ode_to_joy", "total": 4},
                                             {"name": "fur_elise", "total": 0, "problem": "too wide"}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cli = load("armonico_cli_checks", "cli/armonico.py")
        cfg = {"DEVICE": "yes", "DATA_DIR": tempfile.mkdtemp(), "LESSON_UI_PORT": str(srv.server_port),
               "MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1883"}
        rows = cli.checks(cfg, False)
        srv.shutdown()
        names = [r[0] for r in rows]
        self.assertIn("Broker", names)
        self.assertIn("Piano samples", names)                 # a device install checks the device too
        bad = [r[2] for r in rows if r[0] == "Not a lesson"]
        self.assertEqual(bad, ["fur elise: too wide"])


if __name__ == "__main__":
    unittest.main()
