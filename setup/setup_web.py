#!/usr/bin/env python3
"""
setup_web.py - the one-time setup screen the installer opens in the browser.

It asks for the MQTT broker and tests it, lists the connected MIDI keyboards, and
measures the chosen one by waiting for its lowest and highest key. It never writes
the settings file itself: the values go back to install.sh in a small result file,
one "KEY<tab>value" per line, and install.sh writes the settings. So the format of
the settings file is defined in one place only.

install.sh runs it as the service user, not as root: it needs a MIDI port, a broker to
test and one file to write, and nothing more.

Access needs the 6-digit code that install.sh prints in the terminal (SETUP_CODE in the
environment). Wrong codes are slowed down, and the screen closes after saving or after
--timeout seconds.

Exit codes: 0 saved, 3 timed out, 2 bad arguments.
"""
import argparse
import hmac
import json
import os
import re
import socket
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mido
import paho.mqtt.client as mqtt

KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASS",
        "MIDI_DEVICE", "KEY_LOWEST", "KEY_HIGHEST", "STOP_KEY", "UI_LANG", "LESSON_HOSTS",
        "SCREEN_CODE")
MAX_TRIES = 5              # wrong codes in a row before a pause
LOCK_SECONDS = 60
KEY_WAIT = 30              # seconds to wait for a key press
NAMES = "C C# D D# E F F# G G# A A# B".split()
FONT_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "lessons" / "fonts"   # the lesson screen's Heebo


def note_name(n):
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def read_defaults(path):
    """Values from an existing settings file, so running the setup again starts from them."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^([A-Z_]+)='(.*)'\s*$", line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def clean(value):
    """A value is refused when it could break the settings file."""
    value = str(value).strip()
    if "'" in value or "\t" in value or "\n" in value or "\r" in value:
        raise ValueError("A single quote, tab or line break cannot be used")
    return value


# ----------------------------------------------------------------------
# The checks the screen asks for
# ----------------------------------------------------------------------
def mqtt_test(host, port, user, password):
    """"OK", or a sentence that says what went wrong."""
    result, done = [], threading.Event()

    def on_connect(client, userdata, flags, reason, props=None):
        result.append(reason)
        done.set()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="piano-setup-web")
    if user:
        c.username_pw_set(user, password)
    c.on_connect = on_connect
    try:
        c.connect(host, int(port), keepalive=10)
    except (OSError, ValueError) as e:
        return f"The broker cannot be reached: {e}"
    c.loop_start()
    done.wait(8)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass
    if not result:
        return "The broker did not answer within 8 seconds"
    if result[0].is_failure:
        return f"The broker refused the connection: {result[0]}"
    return "OK"


def midi_devices():
    """ALSA client names of the MIDI inputs, the same way the services match them."""
    try:
        names = mido.get_input_names()
    except Exception:
        return []
    seen = []
    for name in names:
        client = name.split(":")[0]
        if client not in seen and "Midi Through" not in client and "RtMidi" not in client:
            seen.append(client)
    return seen


def wait_key(device, seconds=KEY_WAIT):
    """The MIDI note of the next key pressed on the device, or None."""
    ports = [n for n in mido.get_input_names() if n.split(":")[0] == device]
    if not ports:
        return None
    with mido.open_input(ports[0]) as port:
        for _ in port.iter_pending():
            pass
        end = time.time() + seconds
        while time.time() < end:
            for msg in port.iter_pending():
                if msg.type == "note_on" and msg.velocity > 0:
                    return msg.note
            time.sleep(0.01)
    return None


# ----------------------------------------------------------------------
# The server
# ----------------------------------------------------------------------
class State:
    def __init__(self, args):
        self.args = args
        self.code = os.environ.get("SETUP_CODE", "")
        self.session = secrets.token_hex(16)
        self.defaults = read_defaults(args.defaults) if args.defaults else {}
        self.fails = 0
        self.locked_until = 0.0
        self.saved = threading.Event()
        self.key_lock = threading.Lock()   # one key measurement at a time


def host_allowed(host):
    """The same rule the lesson screen keeps. A page on the internet whose name points at
    this machine could otherwise drive the setup from any browser on the home network,
    and the setup decides what the whole install connects to."""
    import ipaddress, socket
    name = str(host).strip().lower()
    if name.startswith("["):
        name = name[1:].split("]")[0]
    elif name.count(":") == 1:
        name = name.split(":")[0]
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    me = socket.gethostname().lower()
    return name in {"localhost", me, me + ".local"}


def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


class Handler(BaseHTTPRequestHandler):
    timeout = 20            # a client that stops sending mid-request cannot hold a thread
    state = None

    def log_message(self, *a):
        pass

    # --- helpers ---
    def send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "frame-ancestors 'none'; default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200, headers=None):
        self.send(code, json.dumps(obj), headers=headers)

    def body(self):
        # A negative length reads until the client stops sending, and a length that is not
        # a number raised where nothing caught it. This answers the network, so neither
        # is allowed to reach the read.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        n = 0 if n < 0 else min(n, 10_000)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def authed(self):
        cookie = self.headers.get("Cookie", "")
        m = re.search(r"setup=([0-9a-f]+)", cookie)
        return bool(m) and hmac.compare_digest(m.group(1), self.state.session)

    # --- routes ---
    def do_GET(self):
        if not host_allowed(self.headers.get("Host", "")):
            return self.send(403, "{}")
        st = self.state
        if self.path == "/":
            page = (PAGE.replace("__TITLE__", st.args.title).replace("__REPO__", st.args.repo)
                        .replace("__LESSON_PORT__", str(st.args.lesson_port))
                        .replace("__LOGO_LIGHT__", LOGO_LIGHT).replace("__LOGO_DARK__", LOGO_DARK))
            self.send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            authed = self.authed()
            d = st.defaults if authed else {}      # nothing about the setup before the code
            # the address of the house's Home Assistant is told only after the code
            # asked now and not taken from the start of the install: the broker may have been installed since
            broker_up = authed and bool(st.args.ha_host) and (st.args.ha_done == "yes" or st.args.ha_broker == "yes"
                                                              or port_open(st.args.ha_host, 1883))
            ha = {"host": st.args.ha_host, "where": st.args.ha_where,
                  "version": st.args.ha_version, "name": st.args.ha_name,
                  "broker": broker_up} if (authed and st.args.ha_host) else None
            self.json({"authed": authed, "ha": ha, "haDone": st.args.ha_done == "yes",
                       "localBroker": st.args.local_broker == "yes", "lang": st.defaults.get("UI_LANG", ""),
                       "defaults": {"MQTT_HOST": d.get("MQTT_HOST", ""),
                                    "MQTT_PORT": d.get("MQTT_PORT", "1883"),
                                    "MQTT_USER": d.get("MQTT_USER", ""),
                                    "MIDI_DEVICE": d.get("MIDI_DEVICE", ""),
                                    "LESSON_HOSTS": d.get("LESSON_HOSTS", ""),
                                    "SCREEN_CODE": d.get("SCREEN_CODE", "")}})
        elif self.path == "/api/devices":
            if not self.authed():
                return self.json({"error": "Enter the code first"}, 403)
            self.json({"devices": midi_devices()})
        elif re.fullmatch(r"/fonts/heebo-(hebrew|latin|latin-ext)\.woff2", self.path):
            try:
                data = (FONT_DIR / self.path.rsplit("/", 1)[1]).read_bytes()
            except OSError:
                return self.send(404, "not found", "text/plain")
            self.send(200, data, "font/woff2", {"Cache-Control": "max-age=86400"})
        else:
            self.send(404, "not found", "text/plain")

    def do_POST(self):
        if not host_allowed(self.headers.get("Host", "")):
            return self.send(403, "{}")
        st = self.state
        data = self.body()

        if self.path == "/api/login":
            now = time.time()
            if now < st.locked_until:
                return self.json({"error": f"Too many wrong codes. Try again in "
                                           f"{int(st.locked_until - now) + 1} seconds"}, 429)
            if st.code and hmac.compare_digest(str(data.get("code", "")).strip(), st.code):
                st.fails = 0
                return self.json({"ok": True}, headers={
                    "Set-Cookie": f"setup={st.session}; HttpOnly; SameSite=Strict; Path=/"})
            st.fails += 1
            if st.fails >= MAX_TRIES:
                st.fails = 0
                st.locked_until = now + LOCK_SECONDS
            time.sleep(1)                       # slows guessing down
            return self.json({"error": "Wrong code. It is shown in the installer's terminal"}, 403)

        if not self.authed():
            return self.json({"error": "Enter the code first"}, 403)

        if self.path == "/api/mqtt":
            try:
                host = clean(data.get("host", ""))
                port = clean(data.get("port", "1883"))
                user = clean(data.get("user", ""))
                password = clean(data.get("password", ""))
            except ValueError as e:
                return self.json({"error": str(e)})
            if not host or not port.isdigit():
                return self.json({"error": "An address and a numeric port are needed"})
            if user and not password and st.defaults.get("MQTT_USER") == user:
                password = st.defaults.get("MQTT_PASS", "")   # an empty field keeps the saved password
            return self.json({"result": mqtt_test(host, port, user, password)})

        if self.path == "/api/key":
            device = str(data.get("device", ""))
            if device not in midi_devices():
                return self.json({"error": "That keyboard is not connected anymore"})
            if not st.key_lock.acquire(blocking=False):
                return self.json({"error": "Already waiting for a key"})
            try:
                note = wait_key(device)
            finally:
                st.key_lock.release()
            if note is None:
                return self.json({"error": f"No key arrived within {KEY_WAIT} seconds"})
            return self.json({"note": note, "name": note_name(note)})

        if self.path == "/api/save":
            try:
                values = {k: clean(data.get(k, "")) for k in KEYS}
            except ValueError as e:
                return self.json({"error": str(e)})
            if values["UI_LANG"] not in ("en", "he"):
                values["UI_LANG"] = "en"
            # extra names the lesson screen answers to, for a reverse proxy or a DNS name
            hosts = [h.strip().lower() for h in values["LESSON_HOSTS"].split(",") if h.strip()]
            if any(not re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", h) for h in hosts):
                return self.json({"error": "A host name has letters, digits, dots and hyphens only"})
            values["LESSON_HOSTS"] = ",".join(hosts)
            values["SCREEN_CODE"] = "on"        # the screen always asks for its code; turning it off is `armonico code off`
            if values["MQTT_USER"] and not values["MQTT_PASS"] \
                    and st.defaults.get("MQTT_USER") == values["MQTT_USER"]:
                values["MQTT_PASS"] = st.defaults.get("MQTT_PASS", "")
            try:
                low, high = int(values["KEY_LOWEST"]), int(values["KEY_HIGHEST"])
            except ValueError:
                return self.json({"error": "Measure the keyboard first"})
            if not 0 <= low < high <= 127:
                return self.json({"error": "The highest key must be above the lowest one"})
            if not values["MQTT_HOST"] or not values["MIDI_DEVICE"]:
                return self.json({"error": "Something is missing"})
            values["STOP_KEY"] = str(low)
            tmp = st.args.result + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for k in KEYS:
                    f.write(f"{k}\t{values[k]}\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, st.args.result)
            self.json({"ok": True})
            st.saved.set()
            return

        self.send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--defaults", default="")
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--title", default="Piano")
    ap.add_argument("--repo", default="vestacorelabs/armonico")
    ap.add_argument("--lesson-port", default="8099")
    # what install.sh found with setup/find_ha.py, so the address does not have to be typed
    ap.add_argument("--ha-host", default="")
    ap.add_argument("--ha-where", default="")
    ap.add_argument("--ha-version", default="")
    ap.add_argument("--ha-name", default="")
    ap.add_argument("--ha-broker", default="no")
    ap.add_argument("--local-broker", default="no")   # Mosquitto was installed on this machine: no Home Assistant steps follow
    ap.add_argument("--ha-done", default="no")      # the installer already set Home Assistant up with a token
    args = ap.parse_args()
    if not re.fullmatch(r"\d{6}", os.environ.get("SETUP_CODE", "")):
        print("SETUP_CODE must hold the 6-digit code", file=sys.stderr)
        sys.exit(2)

    Handler.state = State(args)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    saved = Handler.state.saved.wait(args.timeout)
    time.sleep(0.5)                     # lets the last answer reach the browser
    server.shutdown()
    sys.exit(0 if saved else 3)


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ setup</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%20120%20120%22%20width%3D%22120%22%20height%3D%22120%22%3E%3Cdefs%3E%3ClinearGradient%20id%3D%22ailight%22%20gradientUnits%3D%22userSpaceOnUse%22%20x1%3D%2212%22%20y1%3D%2217%22%20x2%3D%22108%22%20y2%3D%2258%22%3E%3Cstop%20offset%3D%220%22%20stop-color%3D%22%238AD6FF%22%2F%3E%3Cstop%20offset%3D%221%22%20stop-color%3D%22%231D6BE0%22%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Cpath%20d%3D%22M4%2050%20L60%203%20L116%2050%22%20fill%3D%22none%22%20stroke%3D%22%23E2B04A%22%20stroke-width%3D%225%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3Cpath%20d%3D%22M21%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M41.85%2060h17.25v40a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23E2B04A%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M62.7%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M83.55%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M34.05%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M54.9%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M75.75%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M12%2058%20L60%2017%20L108%2058%22%20fill%3D%22none%22%20stroke%3D%22url%28%23ailight%29%22%20stroke-width%3D%2210%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E">
<style>
  /* no scrollbars anywhere; scrolling itself still works */
  * { scrollbar-width: none !important; }
  *::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
  /* Heebo, served from the lesson engine's folder, so this screen looks like the lesson screen with no internet */
  @font-face { font-family: "Heebo"; font-weight: 100 900; font-display: swap; src: url(/fonts/heebo-hebrew.woff2) format("woff2");
               unicode-range: U+0307-0308, U+0590-05FF, U+200C-2010, U+20AA, U+25CC, U+FB1D-FB4F; }
  @font-face { font-family: "Heebo"; font-weight: 100 900; font-display: swap; src: url(/fonts/heebo-latin-ext.woff2) format("woff2");
               unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+1E00-1E9F, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF; }
  @font-face { font-family: "Heebo"; font-weight: 100 900; font-display: swap; src: url(/fonts/heebo-latin.woff2) format("woff2");
               unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
  /* the lesson screen's palette */
  :root { --navy: #0e2a47; --blue: #0b4cb0; --gold: #e2b04a; --gold2: #c9962f; --ok: #1f9d55; --bad: #e5484d;
          --bg: #ffffff; --surface: #ffffff; --surface2: #f3f7fc; --head: #ffffff; --chip: #eef3f9;
          --title: #0e2a47; --ink: #13233a; --muted: #56667d; --line: #dce5f0; --link: #0b4cb0;
          --hero: linear-gradient(180deg, #eef4fb 0%, #f6f9fd 100%); --diag: rgba(11,76,176,.10);
          --cardShadow: 0 0.25rem 0.9rem -0.5rem rgba(14,42,71,.16); --h1a: #1257c4; }
  html[data-theme="dark"] { --bg: #0a1120; --surface: #111b2e; --surface2: #0d1628; --head: #0a1120; --chip: #19263c;
          --title: #eef3fb; --ink: #dbe4f0; --muted: #a3b3ca; --line: #223049; --link: #8fc6f2;
          --hero: linear-gradient(180deg, #0f1b31 0%, #0a1120 100%); --diag: rgba(143,198,242,.08); --cardShadow: none; --h1a: #7cc4f5; }
  * { box-sizing: border-box; }
  body { margin: 0; color: var(--ink); background: var(--bg); font: 16px/1.55 "Heebo", "Segoe UI", system-ui, Roboto, Arial, sans-serif; }
  a { color: var(--link); }
  button, input, select { font: inherit; }
  :focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
  .wrap { max-width: 44rem; margin: 0 auto; padding: 0 1.25rem; }
  header { position: sticky; top: 0; z-index: 5; background: var(--head); border-bottom: 1px solid var(--line); }
  header .wrap { display: flex; align-items: center; gap: 0.75rem; height: 4rem; max-width: 60rem; }
  .logo svg { height: 2.1rem; width: auto; display: block; }
  .logo .for-dark, html[data-theme="dark"] .logo .for-light { display: none; }
  html[data-theme="dark"] .logo .for-dark { display: block; }
  .tools { margin-inline-start: auto; display: flex; gap: 0.35rem; }
  .tools button { border: 1px solid var(--line); background: var(--surface); color: var(--muted); font-weight: 700; font-size: 0.875rem;
                  height: 2.1rem; min-width: 2.1rem; padding: 0 0.7rem; border-radius: 0.6rem; cursor: pointer; }
  .tools button:hover { color: var(--link); border-color: var(--link); }
  .hero { background: var(--hero); border-bottom: 1px solid var(--line); position: relative; overflow: hidden; }
  .hero::after { content: ""; position: absolute; inset: 0 0 0 auto; width: 40%; pointer-events: none;
                 background: repeating-linear-gradient(135deg, var(--diag) 0 1px, transparent 1px 14px);
                 mask-image: linear-gradient(90deg, transparent, #000); -webkit-mask-image: linear-gradient(90deg, transparent, #000); }
  html[dir="rtl"] .hero::after { inset: 0 auto 0 0; transform: scaleX(-1); }
  .hero .wrap { padding-top: 2.25rem; padding-bottom: 1.75rem; position: relative; z-index: 1; }
  h1 { margin: 0 0 0.35rem; color: var(--title); font-size: 2.1rem; line-height: 1.15; font-weight: 800; }
  h1 span { color: var(--h1a); }
  .sub { color: var(--muted); margin: 0; }
  .steps { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 1.25rem; padding: 0; list-style: none; }
  .steps li { display: flex; align-items: center; gap: 0.45rem; background: var(--surface); border: 1px solid var(--line);
              border-radius: 99px; padding: 0.3rem 0.8rem 0.3rem 0.35rem; font-size: 0.875rem; font-weight: 700; color: var(--muted); }
  html[dir="rtl"] .steps li { padding: 0.3rem 0.35rem 0.3rem 0.8rem; }
  .steps li i { width: 1.5rem; height: 1.5rem; border-radius: 50%; display: grid; place-items: center; font-style: normal;
                background: var(--chip); color: var(--muted); font-size: 0.8rem; }
  .steps li.now { color: var(--title); border-color: var(--blue); } .steps li.now i { background: var(--blue); color: #fff; }
  .steps li.ok i { background: var(--ok); color: #fff; }
  main { padding: 1.5rem 0 3rem; }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 1rem; box-shadow: var(--cardShadow);
          padding: 1.4rem; margin-bottom: 1rem; }
  .card h2 { font-size: 1.2rem; margin: 0 0 0.75rem; color: var(--title); display: flex; gap: 0.6rem; align-items: center; }
  .num { width: 1.9rem; height: 1.9rem; border-radius: 50%; background: var(--chip); color: var(--title); display: inline-grid;
         place-items: center; font-size: 0.9rem; flex: none; }
  .done .num { background: var(--ok); color: #fff; }
  label { display: block; font-size: 0.875rem; font-weight: 700; color: var(--title); margin: 0.8rem 0 0.3rem; }
  input, select { width: 100%; padding: 0.65rem 0.8rem; border-radius: 0.75rem; border: 1.5px solid var(--line);
                  background: var(--surface2); color: var(--title); font-size: 1rem; }
  input:focus, select:focus { border-color: var(--link); outline: none; }
  .row { display: flex; gap: 0.75rem; } .row > div { flex: 1; } .row > .small { flex: 0 0 7rem; }
  /* Home Assistant's own button: the blue, the capitals and the 4px corners of its raised buttons */
  a.ha-btn { display: inline-flex; align-items: center; gap: 0.5rem; min-height: 36px; padding: 0 16px; border-radius: 4px; background: #03a9f4; color: #fff;
             font: 500 14px/1 Roboto, "Heebo", system-ui, sans-serif; letter-spacing: 0.0892857143em; text-transform: uppercase; text-decoration: none;
             box-shadow: 0 2px 2px rgba(0,0,0,.14), 0 3px 1px -2px rgba(0,0,0,.12), 0 1px 5px rgba(0,0,0,.2); }
  a.ha-btn:hover { background: #0288d1; } a.ha-btn svg { width: 18px; height: 18px; fill: currentColor; }
  ol.guide { padding-inline-start: 1.2rem; } ol.guide li { margin: 0 0 1rem; } ol.guide .ha-btn { margin-top: 0.4rem; }
  .btns { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 1rem; }
  button.b { border: 0; border-radius: 0.625rem; padding: 0.7rem 1.2rem; font-weight: 700; font-size: 0.9375rem; cursor: pointer;
             background: var(--gold); color: #1d1606; }
  button.b:hover { background: #ebbd5c; } button.b:active { background: var(--gold2); }
  button.b.ghost { background: var(--surface); color: var(--title); border: 1px solid var(--line); }
  button.b.ghost:hover { border-color: var(--link); color: var(--link); }
  button:disabled { opacity: .5; cursor: default; }
  .msg { margin-top: 0.75rem; min-height: 1.4rem; font-weight: 600; font-size: 0.9375rem; }
  .msg.ok { color: var(--ok); } .msg.bad { color: var(--bad); }
  .hidden { display: none; }
  .keys { display: flex; gap: 0.75rem; margin-top: 0.9rem; }
  .keys div { flex: 1; text-align: center; padding: 0.9rem; border-radius: 0.875rem; background: var(--surface2); border: 1px dashed var(--line);
              color: var(--muted); font-size: 0.875rem; font-weight: 600; }
  .keys b { display: block; font-size: 1.7rem; color: var(--title); direction: ltr; }
  #code { font-size: 1.8rem; letter-spacing: 0.5rem; text-align: center; direction: ltr; }
  #host, #port, #user, #pass, #hosts, #dev { direction: ltr; }
  .hint { font-size: 0.8125rem; color: var(--muted); margin: 0.4rem 0 0; line-height: 1.6; }
  .found { background: var(--chip); border: 1px solid var(--line); border-inline-start: 3px solid var(--ok);
           border-radius: 0.625rem; padding: 0.7rem 0.9rem; margin: 0 0 0.75rem; font-size: 0.875rem; line-height: 1.6; }
  .found.nobroker { border-inline-start-color: var(--gold2); }
  .found b { color: var(--title); font-weight: 700; }
  .found .sub { color: var(--muted); margin-top: 0.2rem; }
  .found code { background: var(--surface2); border-radius: 0.25rem; padding: 0 0.25rem; }
  .check { display: flex; align-items: center; gap: 0.5rem; margin-top: 1.1rem; font-weight: 600; color: var(--title); cursor: pointer; }
  .check input { width: 1.1rem; height: 1.1rem; margin: 0; flex: none; accent-color: var(--link); }
  .note { background: var(--chip); border-radius: 0.875rem; padding: 0.8rem 1rem; font-size: 0.875rem; line-height: 1.65; color: var(--ink); margin: 0 0 0.5rem; }
  .note b { color: var(--title); }
  html[dir="rtl"] input::placeholder { text-align: right; }
  .next ol { margin: 0.25rem 0 0; padding-inline-start: 1.25rem; line-height: 1.75; }
  .next li { margin-bottom: 0.35rem; }
  .next code { white-space: nowrap; background: var(--chip); padding: 0.1rem 0.4rem; border-radius: 0.4rem; font-size: 0.875rem; direction: ltr; unicode-bidi: isolate; }
  .hint code { white-space: nowrap; background: var(--chip); padding: 0.1rem 0.4rem; border-radius: 0.4rem; direction: ltr; unicode-bidi: isolate; }
  .sumline { color: var(--muted); font-size: 0.9375rem; margin-top: 0.75rem; }
  @media (max-width: 34rem) { h1 { font-size: 1.7rem; } .row { flex-direction: column; } .row > .small { flex: 1; } }
</style>
</head>
<body>
<header><div class="wrap">
  <span class="logo" aria-label="__TITLE__"><span class="for-light">__LOGO_LIGHT__</span><span class="for-dark">__LOGO_DARK__</span></span>
  <div class="tools"><button id="b-theme" title="Dark or light">☾</button><button class="lang" id="b-lang" lang="he">עברית</button></div>
</div></header>
<section class="hero"><div class="wrap">
  <h1>Setup, <span>one time</span></h1>
  <p class="sub">The broker and the keyboard. After saving, the installer continues in the terminal.</p>
  <ol class="steps" id="steps"><li data-s="s-mqtt"><i>1</i>MQTT broker</li><li data-s="s-kbd"><i>2</i>Keyboard</li><li data-s="s-save"><i>3</i>Save</li></ol>
</div></section>
<main><div class="wrap">

  <section class="card" id="s-login">
    <h2><span class="num">🔒</span>Setup code</h2>
    <label for="code">The 6-digit code shown in the installer's terminal</label>
    <input id="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code">
    <div class="btns"><button class="b" id="b-login">Continue</button></div>
    <div class="msg" id="m-login"></div>
  </section>

  <section class="card hidden" id="s-mqtt">
    <h2><span class="num">1</span>MQTT broker</h2>
    <p class="note">The piano talks only MQTT. Home Assistant connects to the same broker. If you skipped the token in the terminal, the page at the end of this setup walks you through Home Assistant, step by step.</p>
    <div class="found hidden" id="ha"></div>
    <div class="row">
      <div><label for="host">Address</label><input id="host" placeholder="192.168.1.10"></div>
      <div class="small"><label for="port">Port</label><input id="port" value="1883"></div>
    </div>
    <label for="user">User name (empty for none)</label><input id="user" autocomplete="off">
    <label for="pass">Password</label><input id="pass" type="password" autocomplete="new-password">
    <p class="hint">With Home Assistant's Mosquitto broker app: the Home Assistant machine's address, port 1883, and a Home Assistant user made for the piano.</p>
    <div class="btns"><button class="b" id="b-mqtt">Test the connection</button></div>
    <div class="msg" id="m-mqtt"></div>
  </section>

  <section class="card hidden" id="s-kbd">
    <h2><span class="num">2</span>Keyboard</h2>
    <label for="dev">Connected MIDI keyboards</label>
    <select id="dev"></select>
    <div class="keys">
      <div>Lowest key<b id="low">?</b></div>
      <div>Highest key<b id="high">?</b></div>
    </div>
    <div class="btns"><button class="b" id="b-measure">Measure: press the lowest key, then the highest</button><button class="b ghost" id="b-refresh">Refresh the list</button></div>
    <p class="hint">The lowest key becomes the stop key: it stops the alarm and anything playing.</p>
    <div class="msg" id="m-kbd"></div>
  </section>

  <section class="card hidden" id="s-save">
    <h2><span class="num">3</span>Save</h2>
    <label for="hosts">Extra names for the lesson screen (optional)</label>
    <input id="hosts" placeholder="piano.vcl, piano.home" autocomplete="off">
    <p class="hint">For a reverse proxy such as Caddy, or a name in the local DNS. Comma separated. The machine's address and name always work.</p>
    <div id="summary" class="sumline"></div>
    <div class="btns"><button class="b" id="b-save">Save and finish</button></div>
    <div class="msg" id="m-save"></div>
  </section>

  <section class="card next hidden" id="s-next">
    <h2><span class="num">✓</span>What comes next</h2>
    <ol>
      <li><b>The lesson screen</b>, once the installer finishes: <a id="lessonUrl" href="#"></a></li>
    </ol>
    <div id="haDone" class="hidden"><p class="note">✔ <b>Home Assistant was set up by the installer</b>: the broker, the sensors, the helpers, the automations and the blueprints. One thing is left there: delete the access token you made (profile, Security, Long-lived access tokens).</p></div>
    <div id="haGuide">
      <h3>Home Assistant, step by step</h3>
      <label for="haAddr">Your Home Assistant address</label>
      <input id="haAddr" placeholder="192.168.1.10:8123" autocomplete="off">
      <ol class="guide">
        <li><b>Install the MQTT broker.</b> Settings, Apps, Mosquitto broker, Install, Start.<br><a class="ha-btn" data-path="hassio/addon/core_mosquitto/info" target="_blank" rel="noopener">Open Home Assistant</a></li>
        <li><b>Accept the MQTT integration.</b> It appears by itself under Devices &amp; services, with a Configure button.<br><a class="ha-btn" data-path="config/integrations/dashboard" target="_blank" rel="noopener">Open Home Assistant</a></li>
        <li><b>Make a user for the piano.</b> Add user, name <code>piano</code>, a password, and leave Administrator off. This user name and password are the ones asked in step 1 above (Users appears with Advanced mode on in your profile).<br><a class="ha-btn" data-path="config/users" target="_blank" rel="noopener">Open Home Assistant</a></li>
        <li><b>Let the installer do the rest.</b> Make an access token (profile, Security, Long-lived access tokens), then run <code>sudo ./install.sh --reconfigure</code> and paste it. The sensors, helpers and automations are added with no files to copy.<br><a class="ha-btn" data-path="profile/security" target="_blank" rel="noopener">Open Home Assistant</a></li>
        <li><b>The status screen</b> needs the websockets port of the Mosquitto app: Configuration, Network, 1884.<br><a class="ha-btn" data-path="hassio/addon/core_mosquitto/config" target="_blank" rel="noopener">Open Home Assistant</a></li>
      </ol>
    </div>
    <ol>
      <li><b>Telegram</b> (optional, needs Home Assistant): after the setup, run "armonico telegram" on the machine. You make the bot in BotFather, it sets the rest up and asks before each step. The token is not kept on the piano. <a href="https://github.com/__REPO__/blob/main/docs/telegram.md" target="_blank" rel="noopener">Guide</a></li>
      <li><b>Every setting and where it lives</b>, in one table: <a href="https://github.com/__REPO__/blob/main/docs/configuration.md" target="_blank" rel="noopener">configuration.md</a></li>
    </ol>
    <p class="hint">Settings saved to <code>/etc/…/config.env</code>. To change them later: <code>sudo ./install.sh --reconfigure</code>. This page can be closed.</p>
  </section>
</div></main>
<script>
const $ = id => document.getElementById(id);
// ---------- English or Hebrew, for this page only. The piano itself starts in English: the lesson screen has its own language button ----------
const HE = {
  'Setup,': 'הגדרה', 'one time': 'חד-פעמית', 'Dark or light': 'כהה או בהיר',
  'The broker and the keyboard. After saving, the installer continues in the terminal.': 'הברוקר והמקלדת. אחרי השמירה ההתקנה ממשיכה בטרמינל.',
  'The piano talks only MQTT.': 'הפסנתר מדבר רק MQTT.',
  'Home Assistant and Telegram are not set up here:': 'את Home Assistant ואת טלגרם לא מגדירים כאן:',
  'they connect to the same broker, and are set up in Home Assistant after the install. The page at the end of this setup lists what to do.':
    'הם מתחברים לאותו ברוקר, ומוגדרים ב-Home Assistant אחרי ההתקנה. בסוף ההגדרה מופיעה רשימה של מה עושים.',
  "With Home Assistant's Mosquitto broker app: the Home Assistant machine's address, port 1883, and a Home Assistant user made for the piano.":
    'עם אפליקציית Mosquitto broker של Home Assistant: הכתובת של מחשב ה-Home Assistant, פורט 1883, ומשתמש Home Assistant שנפתח בשביל הפסנתר.',
  'The lowest key becomes the stop key: it stops the alarm and anything playing.': 'הקליד הכי נמוך הופך למקש העצירה: הוא עוצר את השעון המעורר וכל מה שמתנגן.',
  'What comes next': 'מה עושים עכשיו', 'The lesson screen': 'מסך השיעורים', ', once the installer finishes:': ', כשההתקנה מסתיימת:',
  '(optional): copy': '(לא חובה): מעתיקים את', "into Home Assistant's": 'לתיקייה', 'folder, and the blueprints into': 'של Home Assistant, ואת ה-blueprints לתיקייה',
  'Guide': 'מדריך', 'Open Home Assistant': 'פתח את Home Assistant', 'Home Assistant, step by step': 'Home Assistant, צעד אחרי צעד', 'Your Home Assistant address': 'הכתובת של Home Assistant שלך', 'Telegram': 'טלגרם',
  '(optional, needs Home Assistant): after the setup, run "armonico telegram" on the machine. You make the bot in BotFather, it sets the rest up and asks before each step. The token is not kept on the piano.':
    '(לא חובה, דורש Home Assistant): אחרי ההגדרה הריצו "armonico telegram" במחשב. יוצרים את הבוט ב-BotFather, והשאר מוגדר בשבילכם, עם שאלה לפני כל שלב. הטוקן לא נשאר על הפסנתר.',
  'Every setting and where it lives': 'כל ההגדרות ואיפה כל אחת נמצאת', ', in one table:': ', בטבלה אחת:',
  'Settings saved to': 'ההגדרות נשמרו ב-', '. To change them later:': '. לשינוי בהמשך:', '. This page can be closed.': '. אפשר לסגור את הדף.',
  'Setup code': 'קוד ההגדרה', "The 6-digit code shown in the installer's terminal": 'הקוד בן 6 הספרות שמופיע בטרמינל של ההתקנה',
  'Continue': 'המשך', 'MQTT broker': 'ברוקר MQTT', 'Address': 'כתובת', 'Port': 'פורט',
  'User name (empty for none)': 'שם משתמש (ריק אם אין)', 'Password': 'סיסמה', 'Test the connection': 'בדיקת החיבור',
  'Keyboard': 'מקלדת', 'Connected MIDI keyboards': 'מקלדות MIDI מחוברות', 'Refresh the list': 'רענון הרשימה',
  'Lowest key': 'הקליד הכי נמוך', 'Highest key': 'הקליד הכי גבוה',
  'Measure: press the lowest key, then the highest': 'מדידה: ללחוץ על הקליד הכי נמוך, ואז על הכי גבוה',
  'Save': 'שמירה', 'Save and finish': 'לשמור ולסיים',
  'Extra names for the lesson screen (optional)': 'שמות נוספים למסך השיעורים (לא חובה)',
  "For a reverse proxy such as Caddy, or a name in the local DNS. Comma separated. The machine's address and name always work.":
    'בשביל reverse proxy כמו Caddy, או שם ב-DNS המקומי. מפרידים בפסיקים. הכתובת והשם של המחשב עובדים תמיד.',
  'Ask for a code on every screen': 'לבקש קוד בכל מסך',
  'Home Assistant found {where}': 'נמצא Home Assistant {where}',
  'Home Assistant found {where}, without a broker': 'נמצא Home Assistant {where}, בלי ברוקר',
  'on this machine': 'על המחשב הזה', 'on the network': 'ברשת', 'version {v}': 'גרסה {v}',
  'A broker answers on port 1883, so the Mosquitto broker app is installed. Its user name and password are still needed.':
    'יש ברוקר שעונה בפורט 1883, כלומר אפליקציית Mosquitto broker מותקנת. עדיין צריך את שם המשתמש והסיסמה שלה.',
  'Nothing answers on port 1883, so the Mosquitto broker app is probably not installed.':
    'שום דבר לא עונה בפורט 1883, כך שכנראה אפליקציית Mosquitto broker לא מותקנת.',
  'On by default. It means the lesson screen asks for a 6-digit code once on each device before it starts a lesson, plays or changes anything. The code comes from':
    'דלוק כברירת מחדל. כלומר מסך השיעורים מבקש קוד בן 6 ספרות פעם אחת בכל מכשיר, לפני שהוא מתחיל שיעור, מנגן או משנה משהו. הקוד מגיע מ-',
  'on the machine, or': 'במחשב, או', 'in Telegram.': 'בטלגרם.',
  'A host name has letters, digits, dots and hyphens only': 'שם מחשב יכול להכיל רק אותיות, ספרות, נקודות ומקפים',
  'Leave empty to keep the saved password': 'להשאיר ריק כדי לשמור את הסיסמה הקיימת', 'Testing…': 'בודק…', '✔ Connected': '✔ מחובר',
  'No MIDI keyboard found. Connect it by USB, switch it on, and refresh.': 'לא נמצאה מקלדת MIDI. צריך לחבר אותה ב-USB, להדליק אותה ולרענן.',
  'Pick a keyboard first': 'קודם בוחרים מקלדת', '⬇ Press the LOWEST key now': '⬇ עכשיו ללחוץ על הקליד הכי נמוך',
  '⬆ Now press the HIGHEST key': '⬆ ועכשיו על הקליד הכי גבוה', 'The highest key came out lower. Once more.': 'הקליד הגבוה יצא נמוך יותר. עוד פעם.',
  '✔ {n} keys. The lowest key will stop the alarm and anything playing.': '✔ {n} קלידים. הקליד הכי נמוך יעצור את השעון המעורר וכל מה שמתנגן.',
  'Broker {b}': 'ברוקר {b}', ' · keyboard {d}': ' · מקלדת {d}', ' · {n} keys': ' · {n} קלידים',
  ' · lessons and Telegram in English': ' · שיעורים וטלגרם באנגלית', ' · lessons and Telegram in English ': ' · שיעורים וטלגרם באנגלית',
  ' · lessons and Telegram in Hebrew': ' · שיעורים וטלגרם בעברית',
  ' · lessons and Telegram start in English, and the language button on the lesson screen changes it': ' · שיעורים וטלגרם מתחילים באנגלית, וכפתור השפה במסך השיעורים משנה זאת',
  '✔ Saved. The installer continues in the terminal, this page can be closed.': '✔ נשמר. ההתקנה ממשיכה בטרמינל, ואפשר לסגור את הדף.',
  // the server's answers
  'Enter the code first': 'קודם צריך להזין את הקוד', 'Too many wrong codes. Try again in {x} seconds': 'יותר מדי קודים שגויים. אפשר לנסות שוב בעוד {x} שניות',
  "Wrong code. It is shown in the installer's terminal": 'קוד שגוי. הקוד מופיע בטרמינל של ההתקנה',
  'A single quote, tab or line break cannot be used': 'אי אפשר להשתמש בגרש, בטאב או בירידת שורה',
  'An address and a numeric port are needed': 'צריך כתובת ופורט שהוא מספר', 'That keyboard is not connected anymore': 'המקלדת הזאת כבר לא מחוברת',
  'Already waiting for a key': 'כבר מחכה ללחיצה', 'No key arrived within {x} seconds': 'לא הגיעה לחיצה תוך {x} שניות',
  'Measure the keyboard first': 'קודם צריך למדוד את המקלדת', 'The highest key must be above the lowest one': 'הקליד הגבוה צריך להיות מעל הנמוך',
  'Something is missing': 'משהו חסר', 'The broker cannot be reached: {x}': 'אין גישה לברוקר: {x}',
  'The broker did not answer within 8 seconds': 'הברוקר לא ענה תוך 8 שניות', 'The broker refused the connection: {x}': 'הברוקר סירב לחיבור: {x}',
};
let LANG = 'en';      // English until the language button is used
const t = (s, v) => { let o = LANG === 'he' && HE[s] !== undefined ? HE[s] : s; if (v) for (const k in v) o = o.split('{' + k + '}').join(v[k]); return o; };
// a sentence from the server, with a number or an error in it, matched against the known ones
function ts(m) {
  if (!m || LANG !== 'he') return m;
  if (HE[m]) return HE[m];
  for (const k of Object.keys(HE).filter(k => k.includes('{x}'))) {
    const re = new RegExp('^' + k.split('{x}').map(part => part.replace(/[.*+?^$()|[\]\\]/g, '\\$&')).join('(.+)') + '$');
    const got = m.match(re); if (got) return t(k, { x: got[1] });
  }
  return m;
}
function translatePage() {
  const walk = el => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { if (n._en === undefined) n._en = n.nodeValue; const c = n._en.trim(); if (c) n.nodeValue = n._en.replace(c, t(c)); }
      else if (n.nodeType === 1 && !['SCRIPT', 'STYLE', 'svg', 'CODE'].includes(n.nodeName) && n.id !== 'b-lang') {
        if (n.title) { if (n._t === undefined) n._t = n.title; n.title = t(n._t); }
        walk(n);
      }
    }
  };
  walk(document.body);
}
function applyLang() {
  document.documentElement.lang = LANG; document.documentElement.dir = LANG === 'he' ? 'rtl' : 'ltr';
  $('b-lang').textContent = LANG === 'he' ? 'English' : 'עברית'; $('b-lang').lang = LANG === 'he' ? 'en' : 'he';
  translatePage();
  if (typeof summary === 'function' && S.mqtt !== undefined) summary();
  if (typeof haPanel === 'function' && S.ha !== undefined) haPanel();
  if (typeof passHint === 'function' && S.savedUser !== undefined) passHint();
}
$('b-lang').onclick = () => { LANG = LANG === 'he' ? 'en' : 'he'; applyLang(); };
// dark or light, the system's choice first, as on the lesson screen
let theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
try { theme = localStorage.getItem('pianoTheme') || theme; } catch (e) { /* private window */ }
function applyTheme() { document.documentElement.dataset.theme = theme; $('b-theme').textContent = theme === 'dark' ? '☀' : '☾'; }
$('b-theme').onclick = () => { theme = theme === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('pianoTheme', theme); } catch (e) {} applyTheme(); };
applyTheme();
// the three steps at the top: done in green, the open one in blue
function paintSteps() {
  let current = true;
  document.querySelectorAll('#steps li').forEach(li => {
    const card = $(li.dataset.s), ok = card.classList.contains('done');
    li.classList.toggle('ok', ok);
    li.classList.toggle('now', !ok && current && !card.classList.contains('hidden'));
    if (!ok) current = false;
  });
}
const S = { mqtt: false, low: null, high: null, ha: undefined, savedUser: undefined };
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
}
function msg(id, text, good) { const m = $(id); m.textContent = text; m.className = 'msg ' + (good ? 'ok' : 'bad'); }
function show(id) { $(id).classList.remove('hidden'); if (typeof paintSteps === 'function') paintSteps(); }
function done(id) { $(id).classList.add('done'); paintSteps(); }

async function start() {
  const st = await api('/api/state');
  if (st.lang === 'he' || st.lang === 'en') LANG = st.lang;
  applyLang();
  if (st.authed) unlocked();
}
// the only placeholder with words in it, so it is redrawn on a language change
function passHint() { if (S.savedUser) $('pass').placeholder = t('Leave empty to keep the saved password'); }
async function fill() {
  const st = await api('/api/state');
  const d = st.defaults;
  $('host').value = d.MQTT_HOST; $('port').value = d.MQTT_PORT || '1883'; $('user').value = d.MQTT_USER;
  S.savedUser = d.MQTT_USER; passHint();
  S.savedDevice = d.MIDI_DEVICE; $('hosts').value = d.LESSON_HOSTS || '';
  S.ha = st.ha || null; S.haDone = !!st.haDone; S.localBroker = !!st.localBroker;
  if (S.ha && !$('host').value) { $('host').value = S.ha.host; $('port').value = '1883'; }
  haPanel();
}
// What the installer found over mDNS. The app's users cannot be read from
// outside, so only the address is filled in and the login is still asked for.
// It is redrawn on a language change, the same way the summary line is.
function haPanel() {
  const el = $('ha'), ha = S.ha;
  if (!ha) { el.classList.add('hidden'); return; }
  const where = ha.where === 'local' ? t('on this machine') : t('on the network');
  let about = ha.host;
  if (ha.name) about += ' · ' + ha.name;
  if (ha.version) about += ' · ' + ha.version;
  const head = ha.broker ? t('Home Assistant found {where}', { where: where })
                         : t('Home Assistant found {where}, without a broker', { where: where });
  const sub = ha.broker ? t('A broker answers on port 1883, so the Mosquitto broker app is installed. Its user name and password are still needed.')
                        : t('Nothing answers on port 1883, so the Mosquitto broker app is probably not installed.');
  el.className = 'found' + (ha.broker ? '' : ' nobroker');
  el.innerHTML = '';
  const line1 = document.createElement('div');
  line1.appendChild(document.createElement('b')).textContent = head;
  // the address keeps a line of its own: in Hebrew a latin address and a hebrew
  // sentence on one line are reordered, and the sentence ends up read first
  const line2 = document.createElement('div');
  line2.className = 'sub';
  const code = document.createElement('code');
  code.textContent = about; code.dir = 'ltr';
  line2.appendChild(code);
  const line3 = document.createElement('div');
  line3.className = 'sub';
  line3.textContent = sub;
  el.appendChild(line1); el.appendChild(line2); el.appendChild(line3);
}
function unlocked() { $('s-login').classList.add('hidden'); show('s-mqtt'); fill(); }

$('b-login').onclick = async () => {
  $('b-login').disabled = true;
  const r = await api('/api/login', { code: $('code').value });
  $('b-login').disabled = false;
  r.ok ? unlocked() : msg('m-login', ts(r.error));
};
$('code').addEventListener('keydown', e => { if (e.key === 'Enter') $('b-login').click(); });

$('b-mqtt').onclick = async () => {
  $('b-mqtt').disabled = true; msg('m-mqtt', t('Testing…'), true);
  const r = await api('/api/mqtt', { host: $('host').value, port: $('port').value,
                                     user: $('user').value, password: $('pass').value });
  $('b-mqtt').disabled = false;
  if (r.result === 'OK') {
    S.mqtt = true; msg('m-mqtt', t('✔ Connected'), true); done('s-mqtt'); show('s-kbd'); loadDevices();
  } else { S.mqtt = false; msg('m-mqtt', ts(r.result || r.error)); }
  summary();
};

async function loadDevices() {
  const r = await api('/api/devices');
  const sel = $('dev'); sel.innerHTML = '';
  (r.devices || []).forEach(d => { const o = document.createElement('option'); o.textContent = d; sel.appendChild(o); });
  if (S.savedDevice && (r.devices || []).includes(S.savedDevice)) sel.value = S.savedDevice;
  if (!sel.options.length) msg('m-kbd', t('No MIDI keyboard found. Connect it by USB, switch it on, and refresh.'));
  else msg('m-kbd', '', true);
}
$('b-refresh').onclick = loadDevices;
$('dev').onchange = () => { S.low = S.high = null; $('low').textContent = $('high').textContent = '?'; summary(); };

$('b-measure').onclick = async () => {
  const device = $('dev').value;
  if (!device) return msg('m-kbd', t('Pick a keyboard first'));
  $('b-measure').disabled = true;
  msg('m-kbd', t('⬇ Press the LOWEST key now'), true);
  let r = await api('/api/key', { device });
  if (r.error) { $('b-measure').disabled = false; return msg('m-kbd', ts(r.error)); }
  S.low = r.note; $('low').textContent = r.name;
  msg('m-kbd', t('⬆ Now press the HIGHEST key'), true);
  r = await api('/api/key', { device });
  $('b-measure').disabled = false;
  if (r.error) return msg('m-kbd', ts(r.error));
  if (r.note <= S.low) { S.low = null; $('low').textContent = '?'; return msg('m-kbd', t('The highest key came out lower. Once more.')); }
  S.high = r.note; $('high').textContent = r.name;
  msg('m-kbd', t('✔ {n} keys. The lowest key will stop the alarm and anything playing.', { n: S.high - S.low + 1 }), true);
  done('s-kbd'); show('s-save'); summary();
};

function summary() {
  $('summary').textContent = t('Broker {b}', { b: $('host').value + ':' + $('port').value }) +
    ($('dev').value ? t(' · keyboard {d}', { d: $('dev').value }) : '') +
    (S.high ? t(' · {n} keys', { n: S.high - S.low + 1 }) : '') +
    t(' · lessons and Telegram start in English, and the language button on the lesson screen changes it');
  $('b-save').disabled = !(S.mqtt && S.high);
}

$('b-save').onclick = async () => {
  $('b-save').disabled = true;
  const r = await api('/api/save', {
    MQTT_HOST: $('host').value, MQTT_PORT: $('port').value, MQTT_USER: $('user').value,
    MQTT_PASS: $('pass').value, MIDI_DEVICE: $('dev').value,
    KEY_LOWEST: String(S.low), KEY_HIGHEST: String(S.high), STOP_KEY: String(S.low), UI_LANG: 'en',
    LESSON_HOSTS: $('hosts').value, SCREEN_CODE: 'on' });
  if (r.ok) {
    msg('m-save', t('✔ Saved. The installer continues in the terminal, this page can be closed.'), true); done('s-save');
    const url = 'http://' + location.hostname + ':__LESSON_PORT__';
    $('lessonUrl').textContent = url; $('lessonUrl').href = url;
    $('haDone').classList.toggle('hidden', !S.haDone); $('haGuide').classList.toggle('hidden', !!S.haDone || !!S.localBroker);
    if (S.ha && S.ha.host) $('haAddr').value = S.ha.host + ':8123'; haLinks();
    show('s-next'); $('s-next').scrollIntoView({ behavior: 'smooth' });
  }
  else { $('b-save').disabled = false; msg('m-save', ts(r.error)); }
};
function haLinks() {
  let a = $('haAddr').value.trim().replace(/\/+$/, '');
  if (a && !/^https?:\/\//.test(a)) a = 'http://' + a + (a.includes(':') ? '' : ':8123');
  document.querySelectorAll('a.ha-btn').forEach(l => { l.href = a ? a + '/' + l.dataset.path : '#'; l.style.opacity = a ? '' : '.55'; });
}
$('haAddr').addEventListener('input', haLinks);
start();
</script>
</body>
</html>
"""

# the brand wordmark, the same files as docs/images, for a light and a dark background
LOGO_LIGHT = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 426.0 120" width="426" height="120"><defs><linearGradient id="sl-anlight" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#8AD6FF"/><stop offset="1" stop-color="#1D6BE0"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A" stroke="#0E2A47" stroke-width="2.4"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#sl-anlight)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><path fill="#0E2A47" d="M156.368 68.48H180.048L178.64 62.08H157.84ZM168.08 47.872 175.248 64.512 175.376 66.368 181.45600000000002 80.0H190.032L168.08 32.704L146.192 80.0H154.704L160.912 65.984L161.04 64.32ZM201.424 50.56H194.576V80.0H201.424ZM210.192 57.92 213.584 52.096000000000004Q212.56 50.879999999999995 211.152 50.367999999999995Q209.744 49.855999999999995 208.144 49.855999999999995Q205.904 49.855999999999995 203.824 51.488Q201.744 53.120000000000005 200.43200000000002 55.84Q199.12 58.56 199.12 62.08L201.424 63.424Q201.424 61.312 201.904 59.744Q202.384 58.176 203.47199999999998 57.28Q204.56 56.384 206.288 56.384Q207.56799999999998 56.384 208.432 56.768Q209.296 57.152 210.192 57.92ZM260.496 61.248000000000005Q260.496 57.664 259.44 55.104Q258.384 52.544 256.336 51.232Q254.288 49.92 251.088 49.92Q248.144 49.92 245.808 51.232Q243.472 52.544 241.87199999999999 55.168Q240.912 52.608000000000004 238.768 51.264Q236.624 49.92 233.424 49.92Q230.54399999999998 49.92 228.49599999999998 51.168Q226.448 52.416 225.10399999999998 54.848V50.56H218.32V80.0H225.10399999999998V62.08Q225.10399999999998 59.968 225.83999999999997 58.496Q226.576 57.024 227.88799999999998 56.256Q229.2 55.488 230.928 55.488Q233.488 55.488 234.672 57.12Q235.856 58.751999999999995 235.856 62.08V80.0H242.768V62.08Q242.768 59.968 243.50400000000002 58.496Q244.24 57.024 245.55200000000002 56.256Q246.864 55.488 248.656 55.488Q251.152 55.488 252.33599999999998 57.12Q253.51999999999998 58.751999999999995 253.51999999999998 62.08V80.0H260.496ZM267.024 65.28Q267.024 69.76 269.10400000000004 73.248Q271.184 76.736 274.8 78.688Q278.416 80.64 282.896 80.64Q287.44 80.64 291.024 78.688Q294.608 76.736 296.688 73.248Q298.76800000000003 69.76 298.76800000000003 65.28Q298.76800000000003 60.736000000000004 296.688 57.28Q294.608 53.824 291.024 51.872Q287.44 49.92 282.896 49.92Q278.416 49.92 274.8 51.872Q271.184 53.824 269.10400000000004 57.28Q267.024 60.736000000000004 267.024 65.28ZM274.128 65.28Q274.128 62.528 275.28 60.416Q276.432 58.304 278.41600000000005 57.152Q280.40000000000003 56.0 282.896 56.0Q285.392 56.0 287.376 57.152Q289.36 58.304 290.512 60.416Q291.664 62.528 291.664 65.28Q291.664 68.032 290.512 70.112Q289.36 72.19200000000001 287.376 73.376Q285.392 74.56 282.896 74.56Q280.40000000000003 74.56 278.41600000000005 73.376Q276.432 72.19200000000001 275.28 70.112Q274.128 68.032 274.128 65.28ZM324.30400000000003 62.08V80.0H331.408V61.248000000000005Q331.408 56.0 328.784 52.96Q326.16 49.92 321.168 49.92Q318.16 49.92 315.952 51.2Q313.744 52.480000000000004 312.336 55.104V50.56H305.36V80.0H312.336V62.08Q312.336 60.096000000000004 313.136 58.592Q313.93600000000004 57.088 315.408 56.288Q316.88 55.488 318.86400000000003 55.488Q321.61600000000004 55.488 322.96000000000004 57.152Q324.30400000000003 58.816 324.30400000000003 62.08ZM339.98400000000004 38.848Q339.98400000000004 40.576 341.29600000000005 41.824Q342.608 43.072 344.336 43.072Q346.192 43.072 347.472 41.824Q348.752 40.576 348.752 38.848Q348.752 37.056 347.472 35.84Q346.192 34.624 344.336 34.624Q342.608 34.624 341.29600000000005 35.84Q339.98400000000004 37.056 339.98400000000004 38.848ZM340.944 50.56V80.0H347.79200000000003V50.56ZM361.93600000000004 65.28Q361.93600000000004 62.528 363.15200000000004 60.416Q364.36800000000005 58.304 366.44800000000004 57.088Q368.528 55.872 371.088 55.872Q373.136 55.872 375.05600000000004 56.512Q376.97600000000006 57.152 378.48 58.272Q379.98400000000004 59.391999999999996 380.68800000000005 60.864000000000004V53.184Q379.15200000000004 51.712 376.528 50.816Q373.90400000000005 49.92 370.76800000000003 49.92Q366.288 49.92 362.672 51.872Q359.05600000000004 53.824 356.9440000000001 57.28Q354.83200000000005 60.736000000000004 354.83200000000005 65.28Q354.83200000000005 69.76 356.9440000000001 73.248Q359.05600000000004 76.736 362.672 78.688Q366.288 80.64 370.76800000000003 80.64Q373.90400000000005 80.64 376.528 79.744Q379.15200000000004 78.848 380.68800000000005 77.312V69.696Q379.98400000000004 71.104 378.51200000000006 72.22399999999999Q377.04 73.344 375.15200000000004 74.01599999999999Q373.264 74.688 371.088 74.688Q368.528 74.688 366.44800000000004 73.47200000000001Q364.36800000000005 72.256 363.15200000000004 70.144Q361.93600000000004 68.032 361.93600000000004 65.28ZM386.064 65.28Q386.064 69.76 388.144 73.248Q390.22400000000005 76.736 393.84000000000003 78.688Q397.456 80.64 401.93600000000004 80.64Q406.48 80.64 410.064 78.688Q413.648 76.736 415.72800000000007 73.248Q417.80800000000005 69.76 417.80800000000005 65.28Q417.80800000000005 60.736000000000004 415.72800000000007 57.28Q413.648 53.824 410.064 51.872Q406.48 49.92 401.93600000000004 49.92Q397.456 49.92 393.84000000000003 51.872Q390.22400000000005 53.824 388.144 57.28Q386.064 60.736000000000004 386.064 65.28ZM393.168 65.28Q393.168 62.528 394.32000000000005 60.416Q395.47200000000004 58.304 397.456 57.152Q399.44000000000005 56.0 401.93600000000004 56.0Q404.432 56.0 406.41600000000005 57.152Q408.40000000000003 58.304 409.552 60.416Q410.704 62.528 410.704 65.28Q410.704 68.032 409.552 70.112Q408.40000000000003 72.19200000000001 406.41600000000005 73.376Q404.432 74.56 401.93600000000004 74.56Q399.44000000000005 74.56 397.456 73.376Q395.47200000000004 72.19200000000001 394.32000000000005 70.112Q393.168 68.032 393.168 65.28Z"/></svg>"""
LOGO_DARK = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 426.0 120" width="426" height="120"><defs><linearGradient id="sd-andark" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#B8E8FF"/><stop offset="1" stop-color="#5AA8F5"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#sd-andark)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><path fill="#EEF3FB" d="M156.368 68.48H180.048L178.64 62.08H157.84ZM168.08 47.872 175.248 64.512 175.376 66.368 181.45600000000002 80.0H190.032L168.08 32.704L146.192 80.0H154.704L160.912 65.984L161.04 64.32ZM201.424 50.56H194.576V80.0H201.424ZM210.192 57.92 213.584 52.096000000000004Q212.56 50.879999999999995 211.152 50.367999999999995Q209.744 49.855999999999995 208.144 49.855999999999995Q205.904 49.855999999999995 203.824 51.488Q201.744 53.120000000000005 200.43200000000002 55.84Q199.12 58.56 199.12 62.08L201.424 63.424Q201.424 61.312 201.904 59.744Q202.384 58.176 203.47199999999998 57.28Q204.56 56.384 206.288 56.384Q207.56799999999998 56.384 208.432 56.768Q209.296 57.152 210.192 57.92ZM260.496 61.248000000000005Q260.496 57.664 259.44 55.104Q258.384 52.544 256.336 51.232Q254.288 49.92 251.088 49.92Q248.144 49.92 245.808 51.232Q243.472 52.544 241.87199999999999 55.168Q240.912 52.608000000000004 238.768 51.264Q236.624 49.92 233.424 49.92Q230.54399999999998 49.92 228.49599999999998 51.168Q226.448 52.416 225.10399999999998 54.848V50.56H218.32V80.0H225.10399999999998V62.08Q225.10399999999998 59.968 225.83999999999997 58.496Q226.576 57.024 227.88799999999998 56.256Q229.2 55.488 230.928 55.488Q233.488 55.488 234.672 57.12Q235.856 58.751999999999995 235.856 62.08V80.0H242.768V62.08Q242.768 59.968 243.50400000000002 58.496Q244.24 57.024 245.55200000000002 56.256Q246.864 55.488 248.656 55.488Q251.152 55.488 252.33599999999998 57.12Q253.51999999999998 58.751999999999995 253.51999999999998 62.08V80.0H260.496ZM267.024 65.28Q267.024 69.76 269.10400000000004 73.248Q271.184 76.736 274.8 78.688Q278.416 80.64 282.896 80.64Q287.44 80.64 291.024 78.688Q294.608 76.736 296.688 73.248Q298.76800000000003 69.76 298.76800000000003 65.28Q298.76800000000003 60.736000000000004 296.688 57.28Q294.608 53.824 291.024 51.872Q287.44 49.92 282.896 49.92Q278.416 49.92 274.8 51.872Q271.184 53.824 269.10400000000004 57.28Q267.024 60.736000000000004 267.024 65.28ZM274.128 65.28Q274.128 62.528 275.28 60.416Q276.432 58.304 278.41600000000005 57.152Q280.40000000000003 56.0 282.896 56.0Q285.392 56.0 287.376 57.152Q289.36 58.304 290.512 60.416Q291.664 62.528 291.664 65.28Q291.664 68.032 290.512 70.112Q289.36 72.19200000000001 287.376 73.376Q285.392 74.56 282.896 74.56Q280.40000000000003 74.56 278.41600000000005 73.376Q276.432 72.19200000000001 275.28 70.112Q274.128 68.032 274.128 65.28ZM324.30400000000003 62.08V80.0H331.408V61.248000000000005Q331.408 56.0 328.784 52.96Q326.16 49.92 321.168 49.92Q318.16 49.92 315.952 51.2Q313.744 52.480000000000004 312.336 55.104V50.56H305.36V80.0H312.336V62.08Q312.336 60.096000000000004 313.136 58.592Q313.93600000000004 57.088 315.408 56.288Q316.88 55.488 318.86400000000003 55.488Q321.61600000000004 55.488 322.96000000000004 57.152Q324.30400000000003 58.816 324.30400000000003 62.08ZM339.98400000000004 38.848Q339.98400000000004 40.576 341.29600000000005 41.824Q342.608 43.072 344.336 43.072Q346.192 43.072 347.472 41.824Q348.752 40.576 348.752 38.848Q348.752 37.056 347.472 35.84Q346.192 34.624 344.336 34.624Q342.608 34.624 341.29600000000005 35.84Q339.98400000000004 37.056 339.98400000000004 38.848ZM340.944 50.56V80.0H347.79200000000003V50.56ZM361.93600000000004 65.28Q361.93600000000004 62.528 363.15200000000004 60.416Q364.36800000000005 58.304 366.44800000000004 57.088Q368.528 55.872 371.088 55.872Q373.136 55.872 375.05600000000004 56.512Q376.97600000000006 57.152 378.48 58.272Q379.98400000000004 59.391999999999996 380.68800000000005 60.864000000000004V53.184Q379.15200000000004 51.712 376.528 50.816Q373.90400000000005 49.92 370.76800000000003 49.92Q366.288 49.92 362.672 51.872Q359.05600000000004 53.824 356.9440000000001 57.28Q354.83200000000005 60.736000000000004 354.83200000000005 65.28Q354.83200000000005 69.76 356.9440000000001 73.248Q359.05600000000004 76.736 362.672 78.688Q366.288 80.64 370.76800000000003 80.64Q373.90400000000005 80.64 376.528 79.744Q379.15200000000004 78.848 380.68800000000005 77.312V69.696Q379.98400000000004 71.104 378.51200000000006 72.22399999999999Q377.04 73.344 375.15200000000004 74.01599999999999Q373.264 74.688 371.088 74.688Q368.528 74.688 366.44800000000004 73.47200000000001Q364.36800000000005 72.256 363.15200000000004 70.144Q361.93600000000004 68.032 361.93600000000004 65.28ZM386.064 65.28Q386.064 69.76 388.144 73.248Q390.22400000000005 76.736 393.84000000000003 78.688Q397.456 80.64 401.93600000000004 80.64Q406.48 80.64 410.064 78.688Q413.648 76.736 415.72800000000007 73.248Q417.80800000000005 69.76 417.80800000000005 65.28Q417.80800000000005 60.736000000000004 415.72800000000007 57.28Q413.648 53.824 410.064 51.872Q406.48 49.92 401.93600000000004 49.92Q397.456 49.92 393.84000000000003 51.872Q390.22400000000005 53.824 388.144 57.28Q386.064 60.736000000000004 386.064 65.28ZM393.168 65.28Q393.168 62.528 394.32000000000005 60.416Q395.47200000000004 58.304 397.456 57.152Q399.44000000000005 56.0 401.93600000000004 56.0Q404.432 56.0 406.41600000000005 57.152Q408.40000000000003 58.304 409.552 60.416Q410.704 62.528 410.704 65.28Q410.704 68.032 409.552 70.112Q408.40000000000003 72.19200000000001 406.41600000000005 73.376Q404.432 74.56 401.93600000000004 74.56Q399.44000000000005 74.56 397.456 73.376Q395.47200000000004 72.19200000000001 394.32000000000005 70.112Q393.168 68.032 393.168 65.28Z"/></svg>"""

if __name__ == "__main__":
    main()
