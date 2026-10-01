"""The Android app's local mode: the built-in broker (a real MQTT client talks to it over TCP) and
the bot it reads from Telegram (against a stand-in for Telegram's API on this machine)."""
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "android" / "app" / "src" / "main" / "python"))
sys.path.insert(0, str(ROOT / "lessons"))

import paho.mqtt.client as mqtt  # noqa: E402

from armonico_android import bridge as B  # noqa: E402
from armonico_android import telegram as T  # noqa: E402
from armonico_android.broker import topic_matches  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeTelegram:
    """getMe, getUpdates (from a list the test fills), sendMessage (kept), setMyCommands."""

    def __init__(self):
        self.updates = []
        self.sent = []
        self.methods = []
        self.next_id = 1
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
                try:
                    body = json.loads(raw)
                except ValueError:                             # a multipart upload: the bot's picture
                    body = {"multipart": raw}
                with fake.lock:
                    fake.methods.append(method)
                result = True
                if method == "getMe":
                    result = {"id": 1, "username": "test_piano_bot", "first_name": "Piano"}
                elif method == "getUpdates":
                    end = time.time() + 1
                    while time.time() < end:
                        with fake.lock:
                            off = body.get("offset", 0)
                            out = [u for u in fake.updates if u["update_id"] >= off]
                        if out:
                            break
                        time.sleep(0.05)
                    result = out
                elif method == "sendMessage":
                    with fake.lock:
                        fake.sent.append((body["chat_id"], body["text"]))
                data = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def say(self, chat, text):
        with self.lock:
            self.updates.append({"update_id": self.next_id, "message": {"chat": {"id": chat}, "text": text}})
            self.next_id += 1

    def last_to(self, chat):
        with self.lock:
            v = [t for c, t in self.sent if c == chat]
        return v[-1] if v else None

    def wait_for(self, chat, pred, secs=5):
        end = time.time() + secs
        while time.time() < end:
            t = self.last_to(chat)
            if t is not None and pred(t):
                return t
            time.sleep(0.05)
        return self.last_to(chat)


class FakeLink:
    def __init__(self):
        self.sent = []

    def send(self, b):
        self.sent.append(bytes(b))
        return True

    def isConnected(self):
        return True

    def deviceName(self):
        return "Test Piano"

    def reopen(self):
        pass

    def status(self, j):
        pass


class LocalModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tg = FakeTelegram()
        T.API = cls.tg.url
        cls.tmp = Path(tempfile.mkdtemp())
        songs = cls.tmp / "songs"
        songs.mkdir()
        for f in (ROOT / "setup" / "music").glob("*.mid"):
            shutil.copy(f, songs)
        cls.port = free_port()
        cls.ws_port = free_port()
        cls.link = FakeLink()
        cfg = dict(mode="local", local_port=cls.port, local_user="piano", local_password="pw123",
                   local_rec_password="rec123", local_ws_port=cls.ws_port,
                   telegram_token="123:ABCDEF", data_dir=str(cls.tmp), songs_dir=str(songs),
                   stop_key=36, lang="en", admin_secret="s3", engine_dir=str(ROOT), lessons=False)
        B.start(json.dumps(cfg), cls.link)
        B.device_changed(True)
        cls.got = []
        cls.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ha-test")
        cls.c.username_pw_set("piano", "pw123")
        cls.c.on_message = lambda cl, u, m: cls.got.append((m.topic, m.payload.decode()))
        end = time.time() + 5
        while True:
            try:
                cls.c.connect("127.0.0.1", cls.port)
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.1)
        cls.c.subscribe("piano/#")
        cls.c.loop_start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.c.loop_stop()
        cls.c.disconnect()
        B.stop()
        cls.tg.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def last(self, topic):
        v = [p for t, p in self.got if t == topic]
        return v[-1] if v else None

    def wait(self, topic, value, secs=4):
        end = time.time() + secs
        while time.time() < end:
            if self.last(topic) == value:
                return True
            time.sleep(0.05)
        return False

    def test_1_network_client_reaches_the_bridge(self):
        self.assertTrue(self.wait("piano/status", "online"))
        self.c.publish("piano/play", "ode_to_joy|HA")
        self.assertTrue(self.wait("piano/play/status", "playing"))
        self.c.publish("piano/stop", "HA")
        self.assertTrue(self.wait("piano/play/stopped_by", "HA"))

    def test_2_wrong_password_is_refused(self):
        bad = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="intruder")
        bad.username_pw_set("piano", "nope")
        codes = []
        bad.on_connect = lambda cl, u, f, rc, p=None: codes.append(rc)
        bad.connect("127.0.0.1", self.port)
        bad.loop_start()
        time.sleep(0.5)
        bad.loop_stop()
        self.assertTrue(codes and codes[0].is_failure)

    def test_3_bot_ignores_until_paired_then_answers(self):
        self.tg.say(555, "/pianostatus")
        time.sleep(1.2)
        self.assertIsNone(self.tg.last_to(555))
        B.telegram_pair(30)
        self.tg.say(555, "/start")
        self.assertIn("connected", self.tg.wait_for(555, lambda t: "connected" in t) or "")
        self.tg.say(999, "/pianostatus")                       # a stranger, after pairing
        self.tg.say(555, "/pianostatus")
        self.assertIn("Piano status", self.tg.wait_for(555, lambda t: "Piano status" in t) or "")
        time.sleep(0.5)
        self.assertIsNone(self.tg.last_to(999))

    def test_4_bot_commands_and_no_echo_on_the_broker(self):
        self.tg.say(555, "/pianosongs elise")
        self.assertIn("fur_elise", self.tg.wait_for(555, lambda t: "elise" in t) or "")
        self.tg.say(555, "/pianoplay minuet_in_g")
        self.assertTrue(self.wait("piano/play/source", "Telegram"))
        self.tg.say(555, "/pianostop")
        self.assertTrue(self.wait("piano/play/stopped_by", "Telegram"))
        self.tg.say(555, "/seqadd lamp 70 72")
        self.assertIn("saved", self.tg.wait_for(555, lambda t: "saved" in t) or "")
        self.tg.say(555, "/lesson fur_elise")
        self.assertIn("not running", self.tg.wait_for(555, lambda t: "not running" in t) or "")
        replies = [p for t, p in self.got if t == "piano/shortcut/reply"]
        self.assertFalse(any(p.startswith("app-") for p in replies))

    def test_5_live_log_reaches_the_chat(self):
        self.tg.say(555, "/showlog")
        self.tg.wait_for(555, lambda t: "Live log on" in t)
        self.c.publish("piano/play", "ode_to_joy")
        self.assertIn("Song starting", self.tg.wait_for(555, lambda t: "Song starting" in t) or "")
        self.tg.say(555, "/stoplog")
        self.tg.wait_for(555, lambda t: "Live log off" in t)
        self.c.publish("piano/stop", "x")

    def test_5b_the_bots_profile_as_the_linux_setup_gives_it(self):
        with self.tg.lock:
            m = list(self.tg.methods)
        self.assertIn("setMyCommands", m)
        self.assertIn("setMyDescription", m)
        self.assertIn("setMyShortDescription", m)
        self.assertIn("setMyProfilePhoto", m)

    def test_6_topic_filters(self):
        self.assertTrue(topic_matches("piano/#", "piano/play/status"))
        self.assertFalse(topic_matches("piano/+", "piano/play/status"))
        self.assertFalse(topic_matches("#", "$SYS/broker"))

    # ---- the same broker install.sh sets up: two logins with their rules, websockets for the screen
    def connect(self, user, pw, cid, transport="tcp", port=None):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, transport=transport)
        c.username_pw_set(user, pw)
        got, ok = [], threading.Event()
        c.on_connect = lambda cl, u, f, rc, p=None: ok.set() if not rc.is_failure else None
        c.on_message = lambda cl, u, m: got.append((m.topic, m.payload.decode(), m.retain))
        c.connect("127.0.0.1", port or self.port)
        c.loop_start()
        self.assertTrue(ok.wait(3), f"{user} over {transport} did not connect")
        return c, got

    def test_7_the_rules_of_the_two_logins(self):
        rec, seen = self.connect("piano-rec", "rec123", "rec-test")
        rec.subscribe("#")
        time.sleep(0.4)
        self.c.publish("other/topic", "outside")              # piano: only piano/# is its own
        self.c.publish("piano/test/rule", "inside")
        rec.publish("piano/stop", "from the recorder")        # piano-rec writes nothing
        time.sleep(0.6)
        topics = [t for t, _, _ in seen]
        self.assertIn("piano/test/rule", topics)
        self.assertNotIn("other/topic", topics)
        self.assertNotIn(("piano/stop", "from the recorder"), self.got)
        self.assertTrue(any(r for _, _, r in seen), "retained messages reach the recorder on connecting")
        rec.loop_stop()
        rec.disconnect()

    def test_8_websockets_for_the_status_screen(self):
        ws, seen = self.connect("piano", "pw123", "screen-test", transport="websockets", port=self.ws_port)
        ws.subscribe("piano/status")
        end = time.time() + 3
        while time.time() < end and not any(t == "piano/status" for t, _, _ in seen):
            time.sleep(0.05)
        self.assertIn(("piano/status", "online", True), seen)
        ws.publish("piano/stop", "Status screen")              # the screen's stop button
        end = time.time() + 3
        while time.time() < end and ("piano/stop", "Status screen") not in self.got:
            time.sleep(0.05)
        self.assertIn(("piano/stop", "Status screen"), self.got)
        ws.loop_stop()
        ws.disconnect()
        st = json.loads(B.status_json())
        self.assertTrue(st["ws"])

    def test_9_the_recorder(self):
        B.record(True)
        time.sleep(0.8)
        self.c.publish("piano/game/cmd", "profile|x|secret:s3 Noa")
        time.sleep(0.6)
        B.record(False)
        files = sorted((self.tmp / "mqtt").glob("????-??-??.log"))
        self.assertTrue(files)
        text = files[-1].read_text()
        self.assertIn("#  recorder  connected, recording every topic", text)
        self.assertIn("R  piano/status  online", text)
        self.assertIn("secret:***", text)
        self.assertNotIn("secret:s3", text)
        self.assertFalse(json.loads(B.status_json())["record"]["on"])

    def console_after(self, line, pred, secs=4):
        n = json.loads(B.status_json())["console_seq"]
        B.command(line)
        end = time.time() + secs
        while time.time() < end:
            st = json.loads(B.status_json())
            outs = [x["t"] for x in st["console"] if x["k"] == "out"]
            if st["console_seq"] > n and outs and pred(outs[-1]):
                return outs[-1]
            time.sleep(0.05)
        outs = [x["t"] for x in json.loads(B.status_json())["console"] if x["k"] == "out"]
        return outs[-1] if outs else ""

    def test_10_the_command_screen(self):
        self.assertIn("fur_elise", self.console_after("songs elise", lambda t: "elise" in t))
        self.assertIn("Piano status", self.console_after("status", lambda t: "Piano status" in t))
        self.assertIn("Piano status", self.console_after("/pianostatus", lambda t: "Piano status" in t))
        self.assertIn("Armonico", self.console_after("version", lambda t: "Armonico" in t))
        self.assertIn("shortcut learn", self.console_after("help", lambda t: "shortcut learn" in t))
        self.assertIn("Usage", self.console_after("shortcut add x", lambda t: "Usage" in t))
        self.assertIn("lamp2", self.console_after("shortcut add lamp2 60 64", lambda t: "lamp2" in t))
        check = self.console_after("check", lambda t: "Full check" in t)
        self.assertIn("✔ Broker", check)
        self.assertIn("✔ Websockets", check)
        self.assertIn("Report:", check)
        self.assertIn("Recorder", self.console_after("record", lambda t: "Recorder" in t))
        self.link.sent.clear()
        self.assertIn("Unstick done", self.console_after("unstick", lambda t: "Unstick" in t, 6))
        self.assertIn(bytes([0xF7]), self.link.sent)
        self.assertIn(bytes([0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7]), self.link.sent)
        self.assertIn("Unknown", self.console_after("nothing", lambda t: "Unknown" in t))
        replies = [p for t, p in self.got if t == "piano/shortcut/reply"]
        self.assertFalse(any(p.startswith("app-console") for p in replies))


if __name__ == "__main__":
    unittest.main()
