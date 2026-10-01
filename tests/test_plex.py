"""The TV card (lessons/piano_plex.py and its doors in the engine): hidden until turned on, the library listed
from a Plex server by name, the token kept and never sent back, and the reward after a good lesson.

A small fake Plex server answers on a real socket, the way the engine would meet a real one.
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))
import piano_plex as plex                       # noqa: E402

TOKEN = "tok-123"
LIB = {
    "/library/sections": {"MediaContainer": {"Directory": [{"key": "1", "title": "Anime", "type": "show"},
                                                           {"key": "2", "title": "Movies", "type": "movie"}]}},
    "/library/sections/1/all": {"MediaContainer": {"Metadata": [{"ratingKey": "10", "title": "Aharen", "type": "show", "year": 2022}]}},
    "/library/sections/2/all": {"MediaContainer": {"Metadata": [{"ratingKey": "30", "title": "A movie", "type": "movie", "duration": 5400000}]}},
    "/library/metadata/10/children": {"MediaContainer": {"Metadata": [{"ratingKey": "11", "title": "Season 1", "type": "season", "parentTitle": "Aharen"}]}},
    "/library/metadata/11/children": {"MediaContainer": {"Metadata": [{"ratingKey": "12", "title": "Ep 1", "type": "episode",
                                                                       "parentIndex": 1, "index": 1, "grandparentTitle": "Aharen"}]}},
    "/": {"MediaContainer": {"friendlyName": "NAS"}},
    "/identity": {"MediaContainer": {"machineIdentifier": "abc"}},
    "/library/metadata/10": {"MediaContainer": {"Metadata": [{"ratingKey": "10", "type": "show",
                                                               "OnDeck": {"Metadata": [{"ratingKey": "12"}]}}]}},
    "/library/metadata/40": {"MediaContainer": {"Metadata": [{"ratingKey": "40", "type": "show"}]}},
    "/library/metadata/40/allLeaves": {"MediaContainer": {"Metadata": [{"ratingKey": "41", "parentIndex": 0},
                                                                       {"ratingKey": "42", "parentIndex": 1}]}},
    "/library/metadata/30": {"MediaContainer": {"Metadata": [{"ratingKey": "30", "type": "movie"}]}},
}


class FakePlexTV(BaseHTTPRequestHandler):
    """plex.tv: a code, the token once the code was entered (the second ask), and the account's servers."""
    asked = 0
    server = ""

    def answer(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.answer({"id": 7, "code": "ABCD"}, 201)

    def do_GET(self):
        if self.path.startswith("/api/v2/pins/7"):
            FakePlexTV.asked += 1
            return self.answer({"id": 7, "authToken": "user-token" if FakePlexTV.asked > 1 else None})
        if self.path.startswith("/api/v2/resources"):
            return self.answer([{"name": "NAS", "provides": "server", "owned": True, "clientIdentifier": "abc",
                                 "accessToken": TOKEN, "connections": [{"uri": FakePlexTV.server, "local": True}]}])
        self.answer({}, 404)

    def log_message(self, *a):
        pass


class FakePlex(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if self.headers.get("X-Plex-Token") != TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(LIB.get(path, {"MediaContainer": {}})).encode()
        self.send_response(200 if path in LIB else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Module(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(FakePlex)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self):
        plex.set_on(self.tmp, True)
        cfg = plex.load(self.tmp)
        cfg.update(url=self.url, token=TOKEN)
        plex.save(self.tmp, cfg)

    def test_off_until_turned_on(self):
        self.assertFalse(plex.is_on(self.tmp))
        plex.set_on(self.tmp, True)
        self.assertTrue(plex.is_on(self.tmp))
        plex.set_on(self.tmp, False)
        self.assertFalse(plex.is_on(self.tmp))

    def test_address_is_made_whole(self):
        self.assertEqual(plex.norm_url("192.168.1.20"), "http://192.168.1.20:32400")
        self.assertEqual(plex.norm_url("nas.local:32400/"), "http://nas.local:32400")
        self.assertEqual(plex.norm_url("https://x.plex.direct:8443"), "https://x.plex.direct:8443")
        with self.assertRaises(plex.PlexError):
            plex.norm_url("ftp://nas")

    def test_check_takes_only_the_right_token(self):
        self.assertEqual(plex.check(self.url, TOKEN), "NAS")
        with self.assertRaises(plex.PlexError) as e:
            plex.check(self.url, "wrong")
        self.assertEqual(str(e.exception), "token")

    def test_a_server_that_is_not_there(self):
        with self.assertRaises(plex.PlexError) as e:
            plex.check("http://127.0.0.1:9", TOKEN)
        self.assertEqual(str(e.exception), "net")

    def test_browse_by_name_down_to_an_episode(self):
        self.connect()
        libs = plex.browse(self.tmp)
        self.assertEqual([x["title"] for x in libs], ["Anime", "Movies"])
        self.assertTrue(all(x["open"] and not x["pick"] for x in libs))
        shows = plex.browse(self.tmp, "s:1")
        self.assertEqual(shows[0]["key"], "10")
        self.assertTrue(shows[0]["pick"] and shows[0]["open"], "a series opens, and can be picked whole")
        seasons = plex.browse(self.tmp, "10")
        self.assertTrue(seasons[0]["open"] and not seasons[0]["pick"])
        eps = plex.browse(self.tmp, "11")
        self.assertEqual(eps[0]["sub"], "S1·E1 · Aharen")
        self.assertTrue(eps[0]["leaf"] and eps[0]["pick"])
        movie = plex.browse(self.tmp, "s:2")[0]
        self.assertEqual(movie["sub"], "90 min")

    def test_browse_refuses_odd_keys(self):
        self.connect()
        for bad in ("s:1/../x", "../x", "1;2"):
            with self.assertRaises(plex.PlexError):
                plex.browse(self.tmp, bad)

    def test_sign_in_with_plex_brings_the_token_and_the_server(self):
        tv = serve(FakePlexTV)
        self.addCleanup(tv.shutdown)
        FakePlexTV.asked, FakePlexTV.server = 0, self.url
        with mock.patch.object(plex, "discover", return_value=[]):
            got = plex.link_start(self.tmp, tv=f"http://127.0.0.1:{tv.server_address[1]}", poll=0.05)
            self.assertEqual((got["state"], got["code"]), ("waiting", "ABCD"))
            self.assertIn("code=ABCD", got["url"])
            for _ in range(100):
                if plex.link_state()["state"] != "waiting":
                    break
                threading.Event().wait(0.05)
        self.assertEqual(plex.link_state()["state"], "done")
        cfg = plex.load(self.tmp)
        self.assertEqual((cfg["url"], cfg["token"], cfg["server_id"]), (self.url, TOKEN, "abc"))
        self.assertTrue(cfg["client"].startswith("armonico-"), "the piano's own id at plex.tv, kept")

    def test_what_plays_for_a_picked_item(self):
        self.assertEqual(plex.resolve(self.url, TOKEN, "10"), "12", "a series: the episode under Continue Watching")
        self.assertEqual(plex.resolve(self.url, TOKEN, "40"), "42", "none: its first episode, the specials left out")
        self.assertEqual(plex.resolve(self.url, TOKEN, "30"), "30", "a movie: itself")

    def test_the_engine_plays_one_after_another_and_tells_an_hour_of_pause(self):
        self.connect()
        sent, now = [], [0.0]
        seq = iter([{}, {"12": "playing"},                       # the first item starts
                    {"12": "paused"}, {"12": "paused"}, {},      # an hour of pause, then it ends
                    {"30": "playing"}, {}])                      # the second item
        step = lambda s: now.__setitem__(0, now[0] + (4000 if s == plex.END_POLL else s))
        with mock.patch.object(plex, "sessions", side_effect=lambda u, t: next(seq)):
            p = plex.Player()
            p.gen = 1
            p._run(self.tmp, ["10", "30"], True, lambda tp, m: sent.append(m), 1, step, lambda: now[0])
        self.assertEqual(sent, [f"open|1|{plex.uri('abc', '12')}", "idle", f"open|2|{plex.uri('abc', '30')}", "off"])
        self.assertEqual(plex.load(self.tmp)["server_id"], "abc", "the server's id, found once and kept")

    def test_only_with_home_assistant(self):
        self.assertFalse(plex.allowed("localhost"))
        self.assertFalse(plex.allowed("127.0.0.1"))
        self.assertTrue(plex.allowed("192.168.1.5"))
        self.assertEqual(plex.parse("play|12|30|off"), (["12", "30"], True))
        self.assertEqual(plex.parse("stop"), ([], False))

    def test_the_message_home_assistant_reads(self):
        self.assertEqual(plex.message(["12", "30"], True), "play|12|30|off")
        self.assertEqual(plex.message(["12", "x", ""], False), "play|12")
        self.assertEqual(plex.message([], True), "")

    def test_reward_after_a_good_lesson_once_a_day(self):
        plex.set_on(self.tmp, True)
        cfg = plex.load(self.tmp)
        cfg["reward"].update(on=True, items=[{"key": "12", "title": "Ep 1"}], off=True, min=80, daily=True)
        plex.save(self.tmp, cfg)
        self.assertEqual(plex.reward_due(self.tmp, 70, None, 300, "2026-11-13"), "", "under the score")
        self.assertEqual(plex.reward_due(self.tmp, 95, "stop", 300, "2026-11-13"), "", "a stopped lesson")
        self.assertEqual(plex.reward_due(self.tmp, 95, None, 30, "2026-11-13"), "", "too short")
        self.assertEqual(plex.reward_due(self.tmp, 95, None, 300, "2026-11-13"), "play|12|off")
        self.assertEqual(plex.reward_due(self.tmp, 95, None, 300, "2026-11-13"), "", "once a day")
        self.assertEqual(plex.reward_due(self.tmp, None, None, 300, "2026-11-14"), "play|12|off", "no score counts")
        plex.set_on(self.tmp, False)
        self.assertEqual(plex.reward_due(self.tmp, 95, None, 300, "2026-11-15"), "", "the card is off")

    def test_a_server_is_found_on_the_network(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))

        def answer():
            data, addr = srv.recvfrom(1024)
            if data.startswith(b"M-SEARCH"):
                srv.sendto(b"HTTP/1.0 200 OK\r\nContent-Type: plex/media-server\r\nName: NAS\r\nPort: 32400\r\n", addr)
        threading.Thread(target=answer, daemon=True).start()
        with mock.patch.object(plex, "GDM", ("127.0.0.1", srv.getsockname()[1])), \
                mock.patch.object(plex, "FIND_SECONDS", 0.6), mock.patch.object(plex, "_mdns", return_value=[]):
            self.assertEqual(plex.discover(), [{"name": "NAS", "url": "http://127.0.0.1:32400"}])
        srv.close()

    def test_the_token_file_is_private(self):
        self.connect()
        self.assertEqual(os.stat(self.tmp / "plex.json").st_mode & 0o777, 0o600)
        self.assertNotIn(TOKEN, json.dumps(plex.public(self.tmp)))


# ---- the engine's doors
TMP = Path(tempfile.mkdtemp(prefix="armonico-plex-"))
os.environ.setdefault("DATA_DIR", str(TMP / "data"))
os.environ.setdefault("SONGS_DIR", str(TMP / "songs"))
os.environ.setdefault("RUN_DIR", str(TMP / "run"))
os.environ.setdefault("MQTT_ADMIN_SECRET", "secret-for-the-test")
import piano_game as game                       # noqa: E402


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plexsrv = serve(FakePlex)
        cls.url = f"http://127.0.0.1:{cls.plexsrv.server_address[1]}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), game.UIHandler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.plexsrv.shutdown()
        plex.set_on(game.DATA_DIR, False)

    def setUp(self):
        game.set_code_on(False)
        self.sent = []
        eng = mock.Mock()
        eng.pub = lambda topic, msg, retain=False: self.sent.append((topic, msg))
        self.played = []
        self.patches = [mock.patch.object(game, "ENGINE", eng), mock.patch.object(game, "LIVE_OUT", object()),
                        mock.patch.object(game, "MQTT_HOST", "192.168.1.5"),           # Home Assistant's broker
                        mock.patch.object(plex.PLAYER, "play", side_effect=lambda d, k, o, pub: self.played.append((k, o)) or bool(k)),
                        mock.patch.object(plex.PLAYER, "stop")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        game.set_code_on(True)

    def ask(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, data, headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, (json.loads(raw) if raw[:1] == b"{" else raw)

    def test_hidden_until_turned_on(self):
        plex.set_on(game.DATA_DIR, False)
        self.assertEqual(self.ask("GET", "/api/plex")[0], 404)
        self.assertEqual(self.ask("POST", "/plex/play", {"keys": ["1"]})[0], 404)
        page = self.ask("GET", "/")[1].decode()
        self.assertIn("const PLEXFUN = false", page)
        plex.set_on(game.DATA_DIR, True)
        self.assertIn("const PLEXFUN = true", self.ask("GET", "/")[1].decode())
        with mock.patch.object(game, "MQTT_HOST", "localhost"):                    # the local broker: no TV
            self.assertEqual(self.ask("GET", "/api/plex")[0], 404)
            self.assertIn("const PLEXFUN = false", self.ask("GET", "/")[1].decode())

    def test_connect_browse_play_and_reward(self):
        plex.set_on(game.DATA_DIR, True)
        st, j = self.ask("POST", "/plex/config", {"url": self.url, "token": "wrong"})
        self.assertEqual(j, {"ok": False, "error": "token"})
        st, j = self.ask("POST", "/plex/config", {"url": self.url, "token": TOKEN})
        self.assertTrue(j["ok"])
        st, j = self.ask("GET", "/api/plex")
        self.assertTrue(j["token"])
        self.assertNotIn(TOKEN, json.dumps(j), "the token never goes back to a screen")
        st, j = self.ask("POST", "/plex/config", {"url": self.url, "token": ""})
        self.assertTrue(j["ok"], "an empty token keeps the saved one")
        st, j = self.ask("GET", "/api/plex/browse?key=s%3A1")
        self.assertEqual(j["items"][0]["title"], "Aharen")
        st, j = self.ask("POST", "/plex/play", {"keys": ["12", "30"], "off": True})
        self.assertEqual(self.played[-1], (["12", "30"], True), "the engine plays them itself")
        self.assertEqual(self.ask("POST", "/plex/play", {"keys": ["x"]})[0], 400)
        self.ask("POST", "/plex/stop", {})
        self.assertEqual(self.sent[-1], ("tv/plex", "stop"))
        st, j = self.ask("POST", "/plex/reward", {"on": True, "items": [{"key": "12", "title": "Ep 1"}], "off": False, "min": 90, "daily": False})
        self.assertEqual(j["reward"]["min"], 90)
        self.assertEqual(plex.load(game.DATA_DIR)["reward"]["items"], [{"key": "12", "title": "Ep 1"}])

    def test_the_hidden_command_needs_the_secret(self):
        # another test file may have imported the engine before the secret was set
        p = mock.patch.object(game, "MQTT_ADMIN_SECRET", "secret-for-the-test")
        p.start()
        self.addCleanup(p.stop)
        eng = game.Engine.__new__(game.Engine)
        eng.replies = []
        eng.reply = lambda text, chat="", **kw: eng.replies.append(text)
        msg = mock.Mock(topic="piano/game/cmd", retain=False, payload=b"plexfun|cli|on")
        plex.set_on(game.DATA_DIR, False)
        eng._on_message(None, None, msg)
        self.assertFalse(plex.is_on(game.DATA_DIR), "no secret, no card")
        msg.payload = f"plexfun|cli|secret:{game.MQTT_ADMIN_SECRET} on".encode()
        with mock.patch.object(game, "MQTT_HOST", "localhost"):
            eng._on_message(None, None, msg)
        self.assertFalse(plex.is_on(game.DATA_DIR), "the local broker: refused")
        with mock.patch.object(game, "MQTT_HOST", "192.168.1.5"):
            eng._on_message(None, None, msg)
        self.assertTrue(plex.is_on(game.DATA_DIR))
        msg.payload = f"plexfun|cli|secret:{game.MQTT_ADMIN_SECRET} off".encode()
        eng._on_message(None, None, msg)
        self.assertFalse(plex.is_on(game.DATA_DIR))



class HomeAssistantSide(unittest.TestCase):
    """setup/ha_setup.py plex: the blueprint and an automation from it, with the TV's remote found by itself."""
    def setUp(self):
        sys.path.insert(0, str(ROOT / "setup"))
        import ha_setup
        self.hs = ha_setup
        self.calls, self.saved = [], {}
        reg = [{"entity_id": "remote.living_room_tv", "platform": "androidtv_remote"},
               {"entity_id": "remote.other", "platform": "broadlink"},
               {"entity_id": "button.wake_tv", "platform": "wake_on_lan"}]
        ha = mock.Mock()
        ha.call = lambda kind, **kw: self.calls.append((kind, kw)) or (reg if kind == "config/entity_registry/list" else None)

        def rest(method, path, body=None):
            if method == "GET":
                if path in self.saved:
                    return self.saved[path]
                raise ha_setup.HAError("not there")
            self.saved[path] = body
        ha.rest = rest
        self.ha = ha

    def test_the_automation_goes_in_by_itself(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(self.hs, "say"):
            os.environ.pop("PLEX_REMOTE", None)
            os.environ.pop("PLEX_WAKE", None)
            self.assertEqual(self.hs.cmd_plex(self.ha), 0)
        self.assertEqual(self.calls[0][0], "blueprint/save")
        self.assertIn("open|N|URI", self.calls[0][1]["yaml"])
        auto = self.saved["/api/config/automation/config/armonico_tv_plex"]
        self.assertEqual(auto["use_blueprint"]["input"], {"remote": "remote.living_room_tv", "wake": "button.wake_tv"})
        # a notification set in Home Assistant by hand stays when the setup runs again
        auto["use_blueprint"]["input"]["notify"] = [{"action": "notify.phone"}]
        with mock.patch.object(self.hs, "say"):
            self.hs.cmd_plex(self.ha)
        self.assertEqual(self.saved["/api/config/automation/config/armonico_tv_plex"]["use_blueprint"]["input"]["notify"],
                         [{"action": "notify.phone"}])


if __name__ == "__main__":
    unittest.main()
