"""lessons.py - the lesson engine (lessons/piano_game.py) inside the app, unchanged.

The engine is the same file as on Linux, read from a copy the app unpacks on the phone. It talks
to the broker and serves the lesson screen on port 8099 exactly as there. Only its way to the
keyboard is different: on Linux it opens ALSA ports through mido and writes raw MIDI with amidi;
here both go through the bridge, which owns the USB device.

So mido's port functions are replaced with ports that the bridge feeds, and the engine's few
raw-MIDI methods send through the bridge. Nothing in the lessons themselves is touched.
"""
import os
import queue
import sys
import threading
import time

import mido

DEVICE_NAME = "Armonico Android USB MIDI"     # the name the engine looks the keyboard up by

_hub = None
_pg = None            # the engine's module, once loaded
_engine = None
_servers = []         # the lesson screen's HTTP servers, so a stop of the bridge can close them
_thread = None
last_error = ""       # why the engine is not up, for the app's card
started_at = 0.0


class _Input:
    """A mido input port fed by the bridge: iter_pending, receive, poll, or a callback."""

    def __init__(self, hub, callback=None):
        self.hub = hub
        self.callback = callback
        self.q = queue.Queue()
        self.closed = False
        self.name = DEVICE_NAME

    def _put(self, msg):
        if self.closed:
            return
        if self.callback is not None:
            try:
                self.callback(msg)
            except Exception:
                pass
        else:
            self.q.put(msg)

    def iter_pending(self):
        while True:
            try:
                yield self.q.get_nowait()
            except queue.Empty:
                return

    def poll(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def receive(self, block=True):
        if not block:
            return self.poll()
        return self.q.get()

    def __iter__(self):
        while not self.closed:
            yield self.q.get()

    def close(self):
        self.closed = True
        self.hub.drop(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _Output:
    """A mido output port whose messages go to the keyboard through the bridge."""

    def __init__(self, hub):
        self.hub = hub
        self.closed = False
        self.name = DEVICE_NAME

    def send(self, msg):
        self.hub.send(bytes(msg.bytes()))

    def reset(self):
        self.hub.bridge.panic()

    panic = reset

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Hub:
    def __init__(self, bridge):
        self.bridge = bridge
        self.inputs = []
        self.lock = threading.Lock()
        self.parser = mido.Parser()

    def connected(self):
        return self.bridge.keyboard_there()

    def feed(self, data):
        """Raw bytes from the keyboard, to every open input of the engine."""
        if not self.inputs:
            return
        self.parser.feed(data)
        msgs = list(self.parser)
        with self.lock:
            ports = list(self.inputs)
        for m in msgs:
            if m.type in ("clock", "active_sensing"):
                continue
            for p in ports:
                p._put(m)

    def open_input(self, name=None, callback=None, **kw):
        if not self.connected():
            raise IOError("the keyboard is not connected")
        p = _Input(self, callback)
        with self.lock:
            self.inputs.append(p)
        return p

    def drop(self, p):
        with self.lock:
            if p in self.inputs:
                self.inputs.remove(p)

    def send(self, data):
        try:
            self.bridge.send(data)
        except Exception:
            pass                     # a cut link drops the message, as the engine's raw output does


def _patch(hub):
    """mido's ports, and the engine's raw MIDI, through the bridge."""
    mido.get_input_names = lambda *a, **k: [DEVICE_NAME] if hub.connected() else []
    mido.get_output_names = lambda *a, **k: [DEVICE_NAME] if hub.connected() else []
    mido.open_input = hub.open_input
    mido.open_output = lambda name=None, **kw: _Output(hub)
    pg = _pg
    # the engine asks for a fault dump on SIGUSR1; a phone may refuse that signal to an app,
    # and a refused dump is no reason to stop the lessons
    reg = pg.faulthandler.register

    def safe_register(*a, **k):
        try:
            reg(*a, **k)
        except Exception:
            pass
    pg.faulthandler.register = safe_register
    pg.OUT_BACKEND = "rtmidi"                               # its mido path: the output above
    pg.PORT_WARMUP = 0
    pg.Engine.amidi_device = lambda self: None
    pg.Engine.open_raw = lambda self, device: None
    pg.Engine.send_raw = lambda self, data: hub.send(data)

    def live_open(self):
        if self.device is None and hub.connected():
            self.device = DEVICE_NAME
            if self.pedal:
                hub.send(bytes([0xB0, 64, 127]))

    def live_write(self, data, retry=True):
        hub.send(data)

    def live_drop(self):
        self.fd, self.device = None, None

    pg.LiveOut._open = live_open
    pg.LiveOut._write = live_write
    pg.LiveOut._drop = live_drop

    # the lesson screen's server, kept so the bridge can close it and open it again
    base = pg.ThreadingHTTPServer

    class Kept(base):
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            _servers.append(self)
    pg.ThreadingHTTPServer = Kept


def _env(cfg, engine_dir):
    run = os.path.join(cfg["data_dir"], "run")
    os.makedirs(run, exist_ok=True)
    local = cfg.get("mode", "external") == "local"
    env = {
        "DATA_DIR": cfg["data_dir"],
        "SONGS_DIR": cfg["songs_dir"],
        "RUN_DIR": run,
        "MIDI_DEVICE": DEVICE_NAME,
        "MQTT_HOST": "127.0.0.1" if local else cfg.get("host", ""),
        "MQTT_PORT": str(cfg.get("local_port", 1883) if local else cfg.get("port", 1883)),
        "MQTT_USER": cfg.get("local_user", "piano") if local else cfg.get("user", ""),
        "MQTT_PASS": cfg.get("local_password", "") if local else cfg.get("password", ""),
        "MQTT_ADMIN_SECRET": cfg.get("admin_secret", ""),
        "KEY_LOWEST": str(cfg.get("key_lowest", 36)),
        "KEY_HIGHEST": str(cfg.get("key_highest", 96)),
        "UI_LANG": cfg.get("lang", "en"),
        "LESSON_UI_PORT": str(cfg.get("lesson_port", 8099)),
        "SCREEN_CODE": "on",
    }
    os.environ.update(env)
    return env


def run_dir(cfg):
    return os.path.join(cfg["data_dir"], "run")


def start(bridge, cfg):
    """Loads the engine the first time and starts it; later, opens its screen and broker link again."""
    global _hub, _pg, _engine, _thread, last_error, started_at
    last_error = ""
    started_at = time.time()
    engine_dir = cfg.get("engine_dir", "")
    lessons_dir = os.path.join(engine_dir, "lessons")
    if not os.path.isfile(os.path.join(lessons_dir, "piano_game.py")):
        last_error = cfg.get("engine_error") or "the engine's files are not on the phone"
        bridge.log(f"⚠️ Lessons: {last_error}")
        return False
    env = _env(cfg, engine_dir)
    if _hub is None:
        _hub = Hub(bridge)
    _hub.bridge = bridge
    bridge.lesson_hub = _hub
    if _pg is None:
        if lessons_dir not in sys.path:
            sys.path.insert(0, lessons_dir)
        try:
            import piano_game as pg
        except Exception as e:
            import traceback
            last_error = f"{type(e).__name__}: {e}"
            bridge.log(f"⚠️ Lessons: the engine did not load: {last_error}")
            for line in traceback.format_exc().splitlines()[-6:]:
                bridge.log(f"   {line}")
            return False
        _pg = pg
        _patch(_hub)
        _engine = pg.Engine()
        _thread = threading.Thread(target=_run, daemon=True, name="armonico-lessons")
        _thread.start()
        for _ in range(100):                    # the screen opens in the engine's own thread
            if _servers or last_error:
                break
            time.sleep(0.1)
        if _servers:
            bridge.log(f"✅ Lesson screen on port {env['LESSON_UI_PORT']}")
        return True
    # started before in this process: the screen and the broker link again, with today's settings
    pg = _pg
    pg.MQTT_HOST, pg.MQTT_PORT = env["MQTT_HOST"], int(env["MQTT_PORT"])
    pg.MQTT_USER, pg.MQTT_PASS = env["MQTT_USER"], env["MQTT_PASS"]
    pg.MQTT_ADMIN_SECRET = env["MQTT_ADMIN_SECRET"]
    try:
        _engine.mq.username_pw_set(pg.MQTT_USER or None, pg.MQTT_PASS or None)
        _engine.mq.connect_async(pg.MQTT_HOST, pg.MQTT_PORT)
        _engine.mq.loop_start()
    except Exception as e:
        bridge.log(f"⚠️ Lessons: the broker link did not open: {e}")
    if not _servers:
        pg.start_ui()
    bridge.log(f"✅ Lesson screen on port {env['LESSON_UI_PORT']}")
    return True


def _run():
    global last_error
    try:
        if _pg.MQTT_USER:
            _engine.mq.username_pw_set(_pg.MQTT_USER, _pg.MQTT_PASS or None)
        _engine.run()
    except BaseException as e:
        import traceback
        last_error = f"{type(e).__name__}: {e}"
        print(f"Lessons: the engine stopped: {last_error}")
        traceback.print_exc()


def error():
    """Why the lesson screen is not up: the reason kept, or a minute gone by without it."""
    if running():
        return ""
    if last_error:
        return last_error
    if started_at and time.time() - started_at > 60:
        return "it did not come up within a minute"
    return ""


def stop():
    """The bridge was turned off: the screen closes and the engine leaves the broker. The engine's
    own thread stays, idle, and is picked up again by the next start."""
    while _servers:
        s = _servers.pop()
        try:
            s.shutdown()
            s.server_close()
        except Exception:
            pass
    if _engine is not None:
        try:
            _engine.stop_event.set()
            _engine.mq.disconnect()
            _engine.mq.loop_stop()
        except Exception:
            pass


def screen_code():
    if _pg is None:
        return ""
    try:
        return _pg.screen_code() if _pg.code_on() else ""
    except Exception:
        return ""


def running():
    return bool(_servers)
