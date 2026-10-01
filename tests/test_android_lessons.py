"""The lesson engine inside the Android app: lessons/piano_game.py, unchanged, run through the
Android bridge (its keyboard ports are the bridge's, its broker the app's built-in one). A lesson
starts from the broker, plays on the keyboard through the bridge, hears the keys, stops for the
alarm, and the screen answers on its port and sounds its own keys through the bridge."""
import http.cookiejar
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "android" / "app" / "src" / "main" / "python"))

import mido  # noqa: E402
import paho.mqtt.client as mqtt  # noqa: E402

from armonico_android import bridge as B  # noqa: E402
from armonico_android import lessons as LS  # noqa: E402


def free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Link:
    def __init__(self):
        self.sent = []
        self.conn = True

    def send(self, b):
        self.sent.append(bytes(b))
        return True

    def isConnected(self):
        return self.conn

    def deviceName(self):
        return "Test Piano"

    def reopen(self):
        pass

    def status(self, j):
        pass


class LessonsOnAndroidTest(unittest.TestCase):
    def setUp(self):
        # the app patches mido, faulthandler, the engine module and the environment for good, as it
        # should on a phone; here those are put back after the test, so the test files that run
        # later get the real ones (test_lessons.py shares the piano_game module and mido)
        import faulthandler
        kept_mido = {n: getattr(mido, n) for n in ("get_input_names", "get_output_names", "open_input", "open_output")}
        kept_reg, kept_env = faulthandler.register, dict(os.environ)
        kept_pg = sys.modules.pop("piano_game", None)          # the app loads a copy of its own

        def restore():
            for n, f in kept_mido.items():
                setattr(mido, n, f)
            faulthandler.register = kept_reg
            os.environ.clear()
            os.environ.update(kept_env)
            sys.modules.pop("piano_game", None)
            if kept_pg is not None:
                sys.modules["piano_game"] = kept_pg
            LS._pg = LS._engine = LS._hub = None
        self.addCleanup(restore)

    def test_lessons_through_the_bridge(self):
        W = str(ROOT)
        tmp = tempfile.mkdtemp()
        songs = os.path.join(tmp, "songs")
        os.makedirs(songs)
        for f in os.listdir(W + "/setup/music"):
            if f.endswith(".mid"):
                shutil.copy(os.path.join(W, "setup/music", f), songs)
        bp, lp = free(), free()
        cfg = dict(mode="local", local_port=bp, local_user="piano", local_password="pw", data_dir=tmp,
                   songs_dir=songs, stop_key=36, lang="en", admin_secret="s3", engine_dir=W, lessons=True,
                   lesson_port=lp)
        link = Link()
        B.start(json.dumps(cfg), link)
        B.device_changed(True)
        got = []
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="t")
        c.username_pw_set("piano", "pw")
        c.on_message = lambda cl, u, m: got.append((m.topic, m.payload.decode()))
        time.sleep(1.5)
        c.connect("127.0.0.1", bp)
        c.subscribe("piano/#")
        c.loop_start()

        def check(name, ok):
            self.assertTrue(ok, name)

        def last(t):
            v = [p for tt, p in got if tt == t]
            return v[-1] if v else None

        def wait(pred, secs=10):
            end = time.time() + secs
            while time.time() < end:
                if pred():
                    return True
                time.sleep(0.1)
            return False
        check("lesson screen up", wait(lambda: LS.running(), 15))
        r=urllib.request.urlopen(f"http://127.0.0.1:{lp}/",timeout=5)
        check("screen answers 200", r.status==200 and b"Armonico" in r.read())
        code=LS.screen_code(); check("screen code exists", len(code)==6)
        st=json.loads(B.status_json()); check("status says lessons", st["lessons"] and st["code"]==code)
        cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        def post(path,data):
            rq=urllib.request.Request(f"http://127.0.0.1:{lp}{path}",json.dumps(data).encode(),{"Content-Type":"application/json","Origin":f"http://127.0.0.1:{lp}"})
            try: return op.open(rq,timeout=5).status
            except urllib.error.HTTPError as e: return e.code
        check("code accepted", post("/code",{"code":code})==200)
        n1=len(link.sent)
        st1=post("/key",{"note":60,"down":True}); time.sleep(0.2); post("/key",{"note":60,"down":False})
        check("screen key sounds through the bridge", st1 in (200,204) and any(b[:2]==bytes([0x90,60]) for b in link.sent[n1:]))
        check("screen flag written", os.path.exists(os.path.join(tmp,"run","piano_screen_keys")))
        check("engine idle on broker", wait(lambda: last("piano/game/status")=="idle"))
        n0=len(link.sent)
        c.publish("piano/game/cmd","lesson|555|ode_to_joy")
        check("lesson starts", wait(lambda: last("piano/game/status")=="lesson", 20))
        check("flag game written", os.path.exists(os.path.join(tmp,"run","piano_game_active")))
        check("engine plays through the bridge", wait(lambda: len(link.sent)>n0+3, 15))
        c.publish("piano/play","fur_elise")
        time.sleep(1)
        check("song blocked during lesson", last("piano/play/status")!="playing")
        # keys reach the engine: press keys and see it notice (piano/notes from bridge + engine last_key_time)
        p=mido.open_input("x")
        B.midi_in(bytes([0x90,61,80,0xF8,0x80,61,0]))
        m=[x for x in p.iter_pending()]; p.close()
        check("an open port gets the keys", [x.type for x in m]==["note_on","note_off"])
        before=LS._engine.last_key_time
        end=time.time()+15
        while time.time()<end and LS._engine.last_key_time<=before:
            B.midi_in(bytes([0x90,64,90])); time.sleep(0.2); B.midi_in(bytes([0x80,64,0])); time.sleep(0.3)
        check("keys reach the lesson", LS._engine.last_key_time>before)
        # the alarm cuts the lesson
        c.publish("piano/alarm/set","minuet_in_g|Test")
        check("alarm on", wait(lambda: last("piano/alarm/status")=="on"))
        check("lesson stops for the alarm", wait(lambda: last("piano/game/status")=="idle", 20))
        c.publish("piano/stop","x"); time.sleep(0.5)
        check("alarm flag cleared", not os.path.exists(os.path.join(tmp,"run","piano_alarm_active")))
        # lessons list answer goes to shortcut/reply
        c.publish("piano/game/cmd","lessons|777|")
        check("engine answers lessons", wait(lambda: (last("piano/shortcut/reply") or "").startswith("777|🎹")))
        # the command screen: a lesson command's answer comes back to it, not to the bot
        def console_out():
            return [x["t"] for x in json.loads(B.status_json())["console"] if x["k"] == "out"]
        B.command("lesson list")
        check("command screen gets the engine's answer", wait(lambda: any(t.startswith("🎹") for t in console_out())))
        B.command("check")
        check("check sees the lesson screen", wait(lambda: any("✔ Lesson screen" in t for t in console_out()), 15))
        # the status screen, served at /status, gets the piano's login and the broker on this phone
        r = op.open(f"http://127.0.0.1:{lp}/status", timeout=5)
        check("status screen served", r.status == 200 and b"mqtt" in r.read())
        login = json.loads(op.open(f"http://127.0.0.1:{lp}/api/status-login", timeout=5).read())
        check("status screen login", login["user"] == "piano" and login["host"] == "127.0.0.1")
        B.stop(); time.sleep(0.5)
        check("screen closed at stop", not LS.running())
        B.start(json.dumps(cfg),link); B.device_changed(True)
        check("screen back after restart", wait(lambda: LS.running(), 10))
        B.stop()

        c.loop_stop()
        c.disconnect()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
