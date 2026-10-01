#!/usr/bin/env python3
"""mqtt_recorder.py - writes every MQTT message on the broker to a local file.

Runs as the optional <name>-recorder service, turned on with `sudo armonico record on`.
Subscribes to # (every topic, not only piano/), so the file shows what Home Assistant,
the bridge, the lesson engine and anything else sent, in the order the broker passed it on.

One file per day in <data folder>/mqtt/YYYY-MM-DD.log, one line per message:

    2026-09-29 14:03:22.481  R  piano/status  online

R marks a retained message the broker handed over on connecting, a dash a live one.
A payload with line breaks keeps them as \\n, so every message stays on one line.
Files older than MQTT_RECORD_DAYS (14 by default) are deleted.
"""
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import paho.mqtt.client as mqtt

ENV = os.environ
DATA_DIR = Path(ENV.get("DATA_DIR", "/var/lib/armonico"))
OUT_DIR = DATA_DIR / "mqtt"
KEEP_DAYS = int(ENV.get("MQTT_RECORD_DAYS") or 14)
MAX_PAYLOAD = 16384            # longer payloads are cut, with a note of the full size

state = {"day": None, "file": None}

# The commands that need the admin secret carry it as "secret:<value>" in the payload.
# The value is never written to the file.
SECRET = re.compile(r"secret:\S+")


def out_file():
    day = datetime.now().strftime("%Y-%m-%d")
    if day != state["day"]:
        if state["file"]:
            state["file"].close()
        OUT_DIR.mkdir(mode=0o750, exist_ok=True)
        path = OUT_DIR / f"{day}.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        state["file"] = os.fdopen(fd, "a", buffering=1, encoding="utf-8")
        state["day"] = day
        prune()
    return state["file"]


def prune():
    limit = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for f in OUT_DIR.glob("????-??-??.log"):
        if f.stem < limit:
            f.unlink(missing_ok=True)


def write(line):
    out_file().write(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "  " + line + "\n")


def text(payload):
    size = len(payload)
    body = payload[:MAX_PAYLOAD]
    try:
        s = body.decode("utf-8")
    except UnicodeDecodeError:
        s = "<binary> " + body.hex()
    s = SECRET.sub("secret:***", s)
    s = s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
    if size > MAX_PAYLOAD:
        s += f"  <cut, {size} bytes in all>"
    return s if size else "<empty>"


def on_connect(client, userdata, flags, reason, props=None):
    if reason.is_failure:
        write(f"#  recorder  the broker refused the connection: {reason}")
        return
    write("#  recorder  connected, recording every topic")
    client.subscribe("#", qos=0)


def on_disconnect(client, userdata, flags, reason, props=None):
    write(f"#  recorder  disconnected ({reason}), reconnecting")


def on_message(client, userdata, msg):
    write(f"{'R' if msg.retain else '-'}  {msg.topic}  {text(msg.payload)}")


def stop(*_):
    write("#  recorder  stopped")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, stop)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"piano-recorder-{os.getpid()}")
    # Its own login where the broker is this program's to configure: it may read every
    # topic and write to none, so a login taken from this file listens and commands nothing.
    # An outside broker hands out its own logins, and there the piano's login is all there is.
    user = ENV.get("MQTT_REC_USER") or ENV.get("MQTT_USER")
    if user:
        password = ENV.get("MQTT_REC_PASS", "") if ENV.get("MQTT_REC_USER") else ENV.get("MQTT_PASS", "")
        client.username_pw_set(user, password)
    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(1, 30)
    while True:
        try:
            client.connect(ENV["MQTT_HOST"], int(ENV.get("MQTT_PORT") or 1883), keepalive=60)
            break
        except OSError as e:
            write(f"#  recorder  the broker cannot be reached ({e}), trying again in 10 seconds")
            time.sleep(10)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
