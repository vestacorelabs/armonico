"""The TV card on the device (device/ui/views_plex.py): a row in Settings › Playing only while the card is on,
the library walked as a list by name, and the list played now or kept as the reward."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None

LIBS = {"items": [{"key": "s:1", "title": "Anime", "type": "section", "sub": "show", "pick": False, "open": True}]}
SHOWS = {"items": [{"key": "10", "title": "Aharen", "type": "show", "sub": "2022", "pick": True, "open": True}]}
SEASON = {"items": [{"key": "12", "title": "Ep 1", "type": "episode", "sub": "S1·E1", "pick": True, "open": False, "leaf": True}]}


class Sync:
    """Runs the card's threads at once, so the test sees their result."""
    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


@unittest.skipIf(pygame is None, "pygame is not installed")
class TvCard(unittest.TestCase):
    def setUp(self):
        mod = load_screen()
        self.app = make_app(mod, (800, 480), "he")
        self.calls = []

        def req(path, body=None, timeout=3):
            self.calls.append((path, body))
            if path == "/api/plex":
                return {"on": True, "url": "http://nas:32400", "token": True,
                        "reward": {"on": False, "items": [], "off": True, "min": 80, "daily": True}}
            if path.startswith("/api/plex/browse"):
                key = path.split("key=")[1]
                return {"": LIBS, "s%3A1": SHOWS, "10": {"items": []}}.get(key, SEASON)
            return {"ok": True, "reward": body}
        self.app.screen.engine._req = req
        self.p = mock.patch("threading.Thread", Sync)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_the_row_shows_only_while_the_card_is_on(self):
        a = self.app
        a.px_refresh(force=True)
        self.assertTrue(a.px_on)
        a.screen.engine._req = mock.Mock(side_effect=OSError("down"))
        a.px_refresh(force=True)
        self.assertFalse(a.px_on)

    def test_walk_pick_play_and_keep(self):
        a = self.app
        a.open_plex()
        self.assertEqual(a.view, "plex")
        self.assertEqual(a.px["items"][0]["title"], "Anime")
        a.draw()
        a.px_ok(0)                                     # the library
        self.assertEqual(a.px["items"][0]["title"], "Aharen")
        a.px_ok(0)                                     # the series: its whole-series row comes first
        self.assertTrue(a.px["items"][0].get("whole"))
        a.draw()
        a.px_ok(0)                                     # picked whole
        self.assertEqual(a.px["queue"], [{"key": "10", "title": "Aharen"}])
        rows = a.px_rows()
        self.assertEqual([r.get("act") for r in rows[:4]], ["play", "reward", "off", "clear"])
        a.px_ok(0)                                     # play now
        self.assertEqual(self.calls[-1], ("/plex/play", {"keys": ["10"], "off": True}))
        a.px_ok(2)                                     # the TV stays on at the end
        self.assertFalse(a.px["off"])
        a.px_ok(1)                                     # kept as the reward
        path, body = self.calls[-1]
        self.assertEqual(path, "/plex/reward")
        self.assertTrue(body["on"])
        self.assertEqual(body["items"], [{"key": "10", "title": "Aharen"}])
        a.draw()
        a.px_back()
        a.px_back()
        a.px_back()
        self.assertEqual(a.view, "settings")


@unittest.skipIf(pygame is None, "pygame is not installed")
class ConnectOnTheDevice(unittest.TestCase):
    """Not connected yet: the address and the token are typed (or pasted from a phone) here, then Connect."""
    def setUp(self):
        mod = load_screen()
        self.app = make_app(mod, (800, 480), "en")
        self.calls, self.ok = [], {"ok": False, "error": "token"}

        def req(path, body=None, timeout=3):
            self.calls.append((path, body))
            if path == "/api/plex":
                return {"on": True, "url": "", "token": False, "reward": {"on": False, "items": [], "off": True}}
            if path == "/plex/config":
                return self.ok
            return LIBS
        self.app.screen.engine._req = req
        self.p = mock.patch("threading.Thread", Sync)
        self.p.start()
        # the keyboard screen (and its phone paste server) is the settings' own: here only what it was given
        self.fields = []
        self.app.edit_field = lambda label, get, set_, **kw: self.fields.append((label, set_, kw))
    def tearDown(self):
        self.p.stop()

    def test_type_connect_and_reach_the_library(self):
        a = self.app
        a.open_plex()
        self.assertTrue(a.px["setup"])
        self.assertEqual([r["act"] for r in a.px_rows()], ["link", "find", "url", "token", "connect"])
        a.draw()
        a.px_ok(2)                                     # the address: the keyboard opens, with the phone's QR
        self.assertTrue(self.fields[-1][2]["phone"])
        self.fields[-1][1]("192.168.1.20")
        a.px_ok(3)
        self.assertTrue(self.fields[-1][2]["secret"])
        self.fields[-1][1]("abc123")
        a.px_ok(4)                                     # a wrong token: told so, still on the connection
        self.assertEqual(self.calls[-1], ("/plex/config", {"url": "192.168.1.20", "token": "abc123"}))
        self.assertTrue(a.px["setup"])
        self.assertEqual(a.px["note"][0], a.tr("px.eToken"))
        self.ok = {"ok": True, "name": "NAS", "url": "http://192.168.1.20:32400"}
        a.px_ok(4)
        self.assertFalse(a.px["setup"])
        self.assertEqual(a.px["items"][0]["title"], "Anime")
        self.assertEqual(a.px_rows()[-1]["act"], "setup", "the connection stays a row at the libraries' level")
        a.draw()

    def test_sign_in_with_plex_shows_the_code_and_comes_back_connected(self):
        a = self.app
        link = {"state": "waiting", "code": "ABCD", "url": "https://app.plex.tv/auth#?clientID=x&code=ABCD"}
        answers = iter([{"on": True, "url": "", "token": False, "reward": {"on": False, "items": [], "off": True}},
                        {"on": True, "url": "", "token": False, "link": link, "reward": {}},
                        {"on": True, "url": "http://nas:32400", "token": True, "reward": {"on": False, "items": [], "off": True},
                         "link": {"state": "done", "server": "NAS"}}])
        seen = []

        def req(path, body=None, timeout=3):
            if path == "/plex/link":
                return {"ok": True, "link": link}
            if path == "/api/plex":
                got = next(answers)
                if got.get("link", {}).get("state") == "waiting":
                    a.draw()                                 # the code and its QR, while it waits
                    seen.append(a.px["link"]["code"])
                return got
            return LIBS
        a.screen.engine._req = req
        a.open_plex()
        with mock.patch("device.ui.views_plex.time.sleep", lambda s: None) if "device.ui.views_plex" in __import__("sys").modules \
                else mock.patch.object(__import__("time"), "sleep", lambda s: None):
            a.px_ok(0)
        self.assertEqual(seen, ["ABCD"])
        self.assertFalse(a.px["setup"])
        self.assertEqual(a.px["url"], "http://nas:32400")
        self.assertEqual(a.px["items"][0]["title"], "Anime")


if __name__ == "__main__":
    unittest.main()
