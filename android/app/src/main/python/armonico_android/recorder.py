"""recorder.py - every MQTT message on the broker, to one file per day, as bridge/mqtt_recorder.py.

On Linux it is the optional <name>-recorder service, turned on with `armonico record on`. Here it
runs inside the app, turned on with the switch in the settings or "record on" in the commands.
The files are the same: <data folder>/mqtt/YYYY-MM-DD.log, one line per message:

    2026-09-29 14:03:22.481  R  piano/status  online

R marks a retained message the broker handed over on connecting, a dash a live one. A payload
with line breaks keeps them as \\n, so every message stays on one line. The admin secret is never
written. Files older than record_days (14 by default) are deleted.

The login is piano-rec on the built-in broker (reads everything, writes nothing), and the piano's
own login on a broker on the network, as there.
"""
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

MAX_PAYLOAD = 16384
SECRET = re.compile(r"secret:\S+")
# The answer that hands out the screen code carries it as six digits; never written either.
CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")


def text(payload, topic=""):
    size = len(payload)
    body = payload[:MAX_PAYLOAD]
    try:
        s = body.decode("utf-8")
    except UnicodeDecodeError:
        s = "<binary> " + body.hex()
    s = SECRET.sub("secret:***", s)
    if topic.endswith("/shortcut/reply"):
        s = CODE.sub("******", s)
    s = s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
    if size > MAX_PAYLOAD:
        s += f"  <cut, {size} bytes in all>"
    return s if size else "<empty>"


class Recorder:
    def __init__(self, cfg):
        self.cfg = cfg
        self.folder = os.path.join(cfg["data_dir"], "mqtt")
        self.flag = os.path.join(cfg["data_dir"], "record_on")
        self.days = int(cfg.get("record_days") or 14)
        self.lock = threading.Lock()
        self.day = None
        self.file = None
        self.client = None

    # ---- the switch, kept in a file so it survives a restart, as the systemd unit's enable
    def wanted(self):
        return os.path.exists(self.flag)

    def set(self, on):
        if on:
            open(self.flag, "w").close()
            self.start()
        else:
            try:
                os.remove(self.flag)
            except FileNotFoundError:
                pass
            self.stop()

    def running(self):
        return self.client is not None

    # ---- the files
    def _out(self):
        day = datetime.now().strftime("%Y-%m-%d")
        if day != self.day:
            if self.file:
                self.file.close()
            os.makedirs(self.folder, exist_ok=True)
            self.file = open(os.path.join(self.folder, f"{day}.log"), "a", buffering=1, encoding="utf-8")
            self.day = day
            self.prune()
        return self.file

    def prune(self):
        limit = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        for name in self.files():
            if name[:10] < limit:
                try:
                    os.remove(os.path.join(self.folder, name))
                except OSError:
                    pass

    def files(self):
        try:
            return sorted(n for n in os.listdir(self.folder) if re.fullmatch(r"\d{4}-\d\d-\d\d\.log", n))
        except FileNotFoundError:
            return []

    def write(self, line):
        with self.lock:
            self._out().write(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "  " + line + "\n")

    def tail(self, n=20, match=""):
        names = self.files()
        if not names:
            return []
        try:
            with open(os.path.join(self.folder, names[-1]), encoding="utf-8", errors="replace") as f:
                lines = [x.rstrip("\n") for x in f if match in x]
        except OSError:
            return []
        return lines[-n:]

    def summary(self):
        """The state for the app. The folder is read at most every 5 seconds: the status goes out often."""
        now = time.monotonic()
        cached = getattr(self, "_summary", None)
        if cached and now - cached[0] < 5 and cached[1]["on"] == self.running():
            return cached[1]
        names = self.files()
        size = 0
        for n in names:
            try:
                size += os.path.getsize(os.path.join(self.folder, n))
            except OSError:
                pass
        out = {"on": self.running(), "folder": self.folder, "days": len(names),
               "mb": round(size / 1048576, 1), "today": names[-1] if names else "", "keep": self.days}
        self._summary = (now, out)
        return out

    # ---- the client
    def start(self):
        if self.client is not None:
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"piano-recorder-{secrets.token_hex(3)}")
        if self.cfg.get("mode") == "local":
            host, port = "127.0.0.1", int(self.cfg.get("local_port", 1883))
            c.username_pw_set("piano-rec", self.cfg.get("local_rec_password", ""))
        else:
            host, port = self.cfg.get("host", ""), int(self.cfg.get("port", 1883))
            if self.cfg.get("user"):
                c.username_pw_set(self.cfg["user"], self.cfg.get("password") or None)
        if not host:
            return
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = lambda cl, u, msg: self.write(f"{'R' if msg.retain else '-'}  {msg.topic}  {text(msg.payload, msg.topic)}")
        c.reconnect_delay_set(1, 30)
        self.client = c
        try:
            c.connect_async(host, port, keepalive=60)
            c.loop_start()
        except Exception as e:
            self.write(f"#  recorder  the broker cannot be reached ({e})")

    def stop(self):
        c, self.client = self.client, None
        if c is None:
            return
        self.write("#  recorder  stopped")
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:
            pass
        with self.lock:
            if self.file:
                self.file.close()
                self.file, self.day = None, None

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason.is_failure:
            self.write(f"#  recorder  the broker refused the connection: {reason}")
            return
        self.write("#  recorder  connected, recording every topic")
        client.subscribe("#", qos=0)

    def _on_disconnect(self, client, userdata, flags, reason, props=None):
        if self.client is client:
            self.write(f"#  recorder  disconnected ({reason}), reconnecting")
