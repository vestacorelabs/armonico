#!/usr/bin/env python3
"""ha_setup.py - sets Home Assistant up from here, with one access token.

Without it the same work is five manual steps: install the Mosquitto app, make a user for
the piano, enable packages in configuration.yaml, copy the files, restart. With a token
from the Home Assistant profile page, all of it happens over Home Assistant's own API:

  broker    installs and starts the Mosquitto app (Home Assistant OS and Supervised), makes the
            piano's user in it, and answers with the address, the user and its password
  package   the sensors (MQTT discovery), the helpers, the automations and the blueprints

Nothing is copied into Home Assistant's folders and nothing needs a restart. The token is read
from the environment (HA_TOKEN), kept in memory, never written to a file, and sent only to the
Home Assistant address given. Delete it in the profile page when the setup is done.

  HA_URL=http://192.168.1.5:8123 HA_TOKEN=... python ha_setup.py broker
  HA_URL=... HA_TOKEN=... MQTT_HOST=... MQTT_PORT=1883 MQTT_USER=... MQTT_PASS=... python ha_setup.py package

Both print "key<TAB>value" lines: step results as "ok", "warn" or "fail" lines, and for the broker
also "host", "port", "user" and "pass".
"""
import base64
import json
import os
import re
import secrets
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
HA_DIR = HERE.parent / "home-assistant"
PIANO_DISPLAY = "Armonico Piano"
MOSQUITTO = "core_mosquitto"


class HAError(Exception):
    pass


def normalize_url(raw):
    """192.168.1.5 and homeassistant.local become http://…:8123. A full address stays as typed."""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raise HAError("The Home Assistant address is empty")
    bare = "://" not in raw
    if bare:
        raw = "http://" + raw
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HAError("The address must look like http://192.168.1.5:8123")
    if bare and u.port is None:         # only a bare address gets the default port
        raw = f"http://{u.hostname}:8123"
    return raw


def dedupe_token(token):
    """A token pasted twice or more in a row (a terminal that kept every paste) is the same
    token repeated: keep one copy."""
    for n in range(8, 1, -1):
        part, rest = divmod(len(token), n)
        if rest == 0 and part >= 40 and token[:part] * n == token:
            return token[:part]
    return token


def candidates(raw):
    """A bare address (192.168.1.5) is tried on 8123, Home Assistant's own port, and then on
    plain port 80, which is where a proxy in front of it answers. A full address is used as typed."""
    raw = (raw or "").strip().rstrip("/")
    first = normalize_url(raw)
    if "://" in raw:
        return [first]
    return [first, "http://" + urlparse(first).hostname]


class WebSocket:
    """The little of RFC 6455 Home Assistant's API needs: text frames, ping, close."""

    def __init__(self, url, timeout=30):
        u = urlparse(url)
        secure = u.scheme == "wss"
        port = u.port or (443 if secure else 80)
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise HAError("Home Assistant closed the connection")
            head += chunk
        head, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0] + b" ":
            raise HAError("Home Assistant did not accept the WebSocket connection")
        self.sock = sock

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HAError("The connection to Home Assistant closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text, opcode=1):
        data = text.encode() if isinstance(text, str) else text
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if opcode == 8:
                raise HAError("Home Assistant closed the connection")
            if opcode == 9:
                self.send(payload, 10)
                continue
            if opcode in (0, 1, 2):
                message += payload
                if b1 & 0x80:
                    return message.decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class HA:
    def __init__(self, url, token):
        self.url = normalize_url(url)
        self.token = dedupe_token((token or "").strip())
        if not self.token:
            raise HAError("The access token is empty")
        self.ws = None
        self.next_id = 0

    # ---- REST ----
    def rest(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise HAError("Home Assistant refused the token. Create a new one, and copy it whole")
            detail = e.read()[:200].decode(errors="replace")
            raise HAError(f"Home Assistant answered {e.code} to {method} {path}: {detail}")
        except (urllib.error.URLError, OSError) as e:
            raise HAError(f"Cannot reach Home Assistant at {self.url}: {getattr(e, 'reason', e)}")
        return json.loads(raw) if raw else None

    # ---- WebSocket ----
    def call(self, type_, wait=120, **fields):
        if self.ws is None:
            u = urlparse(self.url)
            self.ws = WebSocket(f"{'wss' if u.scheme == 'https' else 'ws'}://{u.netloc}/api/websocket", timeout=wait)
            first = json.loads(self.ws.recv())
            if first.get("type") != "auth_required":
                raise HAError("Home Assistant did not ask for authentication")
            self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            if json.loads(self.ws.recv()).get("type") != "auth_ok":
                raise HAError("Home Assistant refused the token. Create a new one, and copy it whole")
        self.ws.sock.settimeout(wait)
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "type": type_, **fields}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != mid or msg.get("type") != "result":
                continue
            if not msg.get("success"):
                err = msg.get("error") or {}
                raise HAError(f"{type_}: {err.get('message', 'failed')}")
            return msg.get("result")

    def supervisor(self, endpoint, method="get", timeout=300, **data):
        body = {"endpoint": endpoint, "method": method, "timeout": timeout}
        if data:
            body["data"] = data
        return self.call("supervisor/api", wait=timeout + 10, **body)

    def close(self):
        if self.ws:
            self.ws.close()


def say(kind, text):
    """One result line for the installer or the setup page: ok, warn or fail, then a sentence."""
    print(f"{kind}\t{text}", flush=True)


# ----------------------------------------------------------------------
# broker: the Mosquitto app and the piano's user
# ----------------------------------------------------------------------
def ha_host(ha):
    return urlparse(ha.url).hostname


def make_piano_user(ha, username, password, local_only):
    """The piano's own Home Assistant user, made for it and never anyone else's. The Mosquitto
    app accepts every Home Assistant user, so this is the broker login too.

    A person's account is never touched, whatever it is called: a user named "piano" that
    already exists (an administrator, say) is left as it is. Only a user this setup made earlier
    (same display name, not an administrator, not the owner) is deleted first."""
    for old in ha.call("config/auth/list"):
        if (old.get("name") == PIANO_DISPLAY and not old.get("system_generated") and not old.get("is_owner")
                and "system-admin" not in old.get("group_ids", [])):
            ha.call("config/auth/delete", user_id=old["id"])
    fields = {"name": PIANO_DISPLAY, "group_ids": ["system-users"]}
    if local_only:
        fields["local_only"] = True
    user = ha.call("config/auth/create", **fields)["user"]
    ha.call("config/auth_provider/homeassistant/create", user_id=user["id"],
            username=username, password=password)


# Why a plain, reused user name was refused although the Supervisor logged "Successful login":
# the Supervisor keeps a cache of user name -> hash of the last password that worked, in a file.
# A login for a cached name with a DIFFERENT password gets "False" at once ("use the cache and
# update it in the background", supervisor/auth.py check_login), and only then does the Supervisor
# check with Home Assistant, which is the "Successful login" line. The broker (go-auth) keeps that
# refusal for 5 minutes, per name and password. A name that was never used has no cache entry, so
# it worked. Removing the app or the user does not touch that file. The Supervisor has a call that
# empties it (DELETE /auth/cache), made before every attempt, so the plain name works every time.
PIANO_USER = "armonicopiano"
LOGIN_VARIANTS = tuple((PIANO_USER if n <= 4 else f"{PIANO_USER}{n - 3}", False, "plain") for n in range(1, 7))


def clear_login_cache(ha):
    try:
        ha.supervisor("/auth/cache", "delete", timeout=30)
        return True
    except HAError:
        return False            # an install without a Supervisor has no such cache


def local_login(ha, host):
    """The way the Mosquitto app documents for logins of its own: the "logins" option. Those are
    checked by the broker from a file of the app, without the Supervisor and Home Assistant in
    the middle, which is where the other kind of login was refused although the Supervisor had
    accepted it. Setting the option restarts the app, which takes a minute or two."""
    username, password = f"{PIANO_USER}local", secrets.token_hex(12)
    info = ha.supervisor(f"/addons/{MOSQUITTO}/info")
    options = dict(info.get("options") or {})
    options["logins"] = [x for x in options.get("logins") or [] if not str(x.get("username", "")).startswith(PIANO_USER)] + [
        {"username": username, "password": password}]
    say("ok", "Adding a login of the Mosquitto app itself for the piano. The app restarts, this takes a minute or two")
    ha.supervisor(f"/addons/{MOSQUITTO}/options", "post", timeout=60, options=options)
    ha.supervisor(f"/addons/{MOSQUITTO}/restart", "post", timeout=240)
    time.sleep(10)
    reason = "no answer"
    for attempt in range(1, 7):
        reason = broker_login(host, username, password)
        say("ok", f"Login of the app, attempt {attempt} of 6: {'the broker accepts it' if reason == 'ok' else reason}")
        if reason == "ok":
            return username, password, "ok"
        time.sleep(10)
    return username, password, reason


def provision_login(ha, host):
    """The piano's Home Assistant user, and a broker login that works."""
    time.sleep(20)              # the app's login check (nginx, Supervisor, Home Assistant) needs a moment
    username = password = None
    reason = "no answer"
    for attempt, (name, local_only, kind) in enumerate(LOGIN_VARIANTS, 1):
        username = name
        password = secrets.token_hex(12) if kind == "plain" else secrets.token_urlsafe(18)
        label = f"user {username}"
        clear_login_cache(ha)
        try:
            make_piano_user(ha, username, password, local_only)
        except HAError as e:
            say("ok", f"Attempt {attempt} of {len(LOGIN_VARIANTS)} ({label}): Home Assistant refused: {e}")
            continue
        time.sleep(3)           # Home Assistant saves the user before the check can see it
        reason = broker_login(host, username, password)
        if reason == "ok":
            say("ok", f"Attempt {attempt} of {len(LOGIN_VARIANTS)} ({label}): the broker accepts it")
            return username, password, "ok"
        say("ok", f"Attempt {attempt} of {len(LOGIN_VARIANTS)} ({label}): the broker says {reason}")
        time.sleep(12)
    try:
        return local_login(ha, host)
    except HAError as e:
        say("ok", f"The app's own login could not be set: {e}")
        return username, password, reason


# the plain value of a setting that has to be given, for a broker without certificates
_PLAIN = {"set_client_cert": False, "set_ca_cert": "off"}


def _plain_value(name, field):
    if name in _PLAIN:
        return _PLAIN[name]
    if "default" in field:
        return field["default"]
    if field.get("type") == "boolean":
        return False
    options = field.get("options") or []
    if options:
        first = options[0]
        values = [o["value"] if isinstance(o, dict) else o for o in options]
        return "off" if "off" in values else (first["value"] if isinstance(first, dict) else first)
    return ""


def add_mqtt_by_hand(ha, username, password, broker="core-mosquitto", port=1883):
    """The MQTT integration added the way the interface does it: the broker, then its login.
    Returns "" when it worked, otherwise what Home Assistant answered."""
    flow = ha.rest("POST", "/api/config/config_entries/flow", {"handler": "mqtt", "show_advanced_options": False})
    for _ in range(6):
        kind = flow.get("type")
        if kind == "create_entry":
            return ""
        if kind == "abort":
            return f"Home Assistant stopped the setup: {flow.get('reason', 'abort')}"
        if kind is None or "flow_id" not in flow:
            return f"unexpected answer: {str(flow)[:150]}"
        flow_id = flow["flow_id"]
        url = f"/api/config/config_entries/flow/{flow_id}"
        if kind == "menu":
            options = flow.get("menu_options") or []
            flow = ha.rest("POST", url, {"next_step_id": "broker" if "broker" in options else options[0]}, timeout=20)
            continue
        schema = flow.get("data_schema") or []
        fields = {f.get("name") for f in schema}
        if "broker" in fields:
            payload = {"broker": broker, "port": port, "username": username, "password": password}
            # Newer Home Assistant (2026.9) has a section "other_settings" with two required
            # settings, which Home Assistant's own source lists: set_client_cert (a boolean) and
            # set_ca_cert ("off", "auto" or "custom"). A broker on port 1883 has neither.
            for f in schema:
                if f.get("type") == "expandable":
                    payload[f["name"]] = {sub["name"]: _plain_value(sub["name"], sub)
                                          for sub in f.get("schema") or [] if sub.get("required")}
            for _ in range(4):
                try:
                    flow = ha.rest("POST", url, payload, timeout=40)
                    break
                except HAError as e:
                    # "required key not provided at 'section.key'": that key is added with its plain value
                    missing = re.findall(r"required key not provided at '([a-z_]+)\.([a-z_]+)'", str(e).replace("\\'", "'"))
                    missing += [(m, None) for m in re.findall(r'"errors":\{"([a-z_]+)":"required key not provided"', str(e))]
                    missing += [(m, None) for m in re.findall(r"required key not provided at '([a-z_]+)'", str(e).replace("\\'", "'"))]
                    added = False
                    for section, key in missing:
                        if key is None:         # a whole section is missing: it starts empty
                            if section not in payload:
                                payload[section] = {}
                                added = True
                        elif key not in payload.setdefault(section, {}):
                            payload[section][key] = _plain_value(key, {})
                            added = True
                    if not added:
                        raise
        else:
            flow = ha.rest("POST", url, {}, timeout=20)
        if flow.get("errors"):
            return f"Home Assistant could not connect to {broker}:{port}: {flow['errors']}"
    return "" if flow.get("type") == "create_entry" else f"the setup did not finish: {str(flow)[:150]}"


def connect_ha_mqtt(ha, username, password, seconds=90):
    """Gets the MQTT integration to exist. First by hand, with the login the broker was just seen
    to accept: that is deterministic, and what Home Assistant answers is shown. The app's own
    "Configure" card is the second try, because it logs in with the app's internal login."""
    for attempt in range(1, 4):
        if mqtt_entry_exists(ha):
            return True
        try:
            detail = add_mqtt_by_hand(ha, username, password)
        except HAError as e:
            detail = str(e)
        if detail == "":
            return True
        say("ok", f"Adding the MQTT integration, attempt {attempt} of 3: {detail}")
        time.sleep(5)
    say("ok", "Trying the app's own Configure card now, up to 90 seconds")
    deadline = time.time() + seconds
    while time.time() < deadline and not mqtt_entry_exists(ha):
        try:
            flows = [f for f in ha.call("config_entries/flow/progress") if f.get("handler") == "mqtt"]
        except HAError:
            flows = []
        if flows:
            try:
                step = ha.rest("POST", f"/api/config/config_entries/flow/{flows[0]['flow_id']}", {})
                if step.get("type") == "create_entry":
                    return True
            except HAError:
                pass
        time.sleep(6)
    return mqtt_entry_exists(ha)


def mqtt_entry_exists(ha):
    return any(e.get("domain") == "mqtt" for e in ha.rest("GET", "/api/config/config_entries/entry"))


def broker_login(host, user, password, port=1883):
    """One real MQTT login: "ok", or the broker's own words for why not."""
    import paho.mqtt.client as paho
    result = {}
    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="armonico-ha-check")
    client.username_pw_set(user, password)

    def on_connect(c, userdata, flags, reason, props=None):
        result["r"] = "ok" if not reason.is_failure else str(reason)

    client.on_connect = on_connect
    try:
        client.connect(host, port, 10)
    except OSError as e:
        return f"no answer on {host}:{port} ({e})"
    client.loop_start()
    for _ in range(50):
        if "r" in result:
            break
        time.sleep(0.1)
    client.loop_stop()
    client.disconnect()
    return result.get("r", "no answer")


def cmd_broker(ha):
    """Mosquitto app installed and running, a user for the piano. Prints the broker's login."""
    host = ha_host(ha)
    try:
        info = ha.supervisor(f"/addons/{MOSQUITTO}/info")
    except HAError:
        say("fail", "This Home Assistant has no apps (it is Container or Core). Install a broker "
                    "yourself, or let this installer install one on the piano machine")
        return 2
    if info.get("state") is None or info.get("version") is None:
        say("ok", "Installing the Mosquitto broker app. This can take a few minutes, and nothing else shows until it is done")
        ha.supervisor(f"/addons/{MOSQUITTO}/install", "post", timeout=600)
        info = ha.supervisor(f"/addons/{MOSQUITTO}/info")
    if info.get("state") != "started":
        ha.supervisor(f"/addons/{MOSQUITTO}/start", "post", timeout=120)
        say("ok", "The Mosquitto broker app is running")
    else:
        say("ok", "The Mosquitto broker app was already running")
    # the touch status screen speaks MQTT over websockets, which the app offers on port 1884.
    # Its port list is replaced as a whole, so the ones it has are kept and 1884 is made sure of
    network = dict(info.get("network") or {})
    if network.get("1884/tcp") != 1884:
        network["1884/tcp"] = 1884
        try:
            ha.supervisor(f"/addons/{MOSQUITTO}/options", "post", timeout=60, network=network)
            ha.supervisor(f"/addons/{MOSQUITTO}/restart", "post", timeout=120)
            say("ok", "Port 1884 (websockets) opened on the Mosquitto app, for the status screen")
        except HAError:
            say("warn", "Port 1884 could not be opened. In the Mosquitto app, Configuration, Network: 1884 for the status screen")
    if info.get("boot") != "auto":          # it comes back by itself after a restart of Home Assistant
        try:
            ha.supervisor(f"/addons/{MOSQUITTO}/options", "post", timeout=60, boot="auto")
        except HAError:
            pass
    username, password, reason = provision_login(ha, host)
    if reason != "ok":
        say("warn", f"The broker still refuses the piano's login ({reason}). Look at the app's Log: "
                    "Settings, Apps, Mosquitto broker, Log")
    else:
        say("ok", f"A Home Assistant user '{username}' for the piano, and the broker accepts its login")
        if not mqtt_entry_exists(ha):
            say("ok", "Connecting Home Assistant to the broker, up to 2 minutes")
            connect_ha_mqtt(ha, username, password)
        say("ok" if mqtt_entry_exists(ha) else "warn",
            "The MQTT integration is set up" if mqtt_entry_exists(ha)
            else "The MQTT integration still has to be accepted: Settings, Devices & services, Configure")
    print(f"host\t{host}\nport\t1883\nuser\t{username}\npass\t{password}", flush=True)
    return 0


# ----------------------------------------------------------------------
# package: sensors, helpers, automations, blueprints
# ----------------------------------------------------------------------
def backup_dir():
    return Path(os.environ.get("HA_BACKUP_DIR") or "/etc/armonico/ha-backup")


def keep_edited(ha, auto):
    """An update replaces the piano's automations. One that was changed in Home Assistant since is kept
    as a file first, so the change can be put back by hand. Returns 1 when something was kept."""
    try:
        old = ha.rest("GET", f"/api/config/automation/config/{auto['id']}")
    except HAError:
        return 0                                # not there yet (a first run), or not readable: nothing to keep
    if not isinstance(old, dict):
        return 0
    strip = lambda d: {k: v for k, v in d.items() if k not in ("id",)}
    if strip(old) == strip(auto):
        return 0
    try:
        folder = backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        (folder / f"{auto['id']}.json").write_text(json.dumps(old, indent=1, ensure_ascii=False))
        return 1
    except OSError:
        return 0



def load_package():
    try:
        import yaml
    except ImportError:
        raise HAError("PyYAML is missing, so the package cannot be read. Run the installer again")
    return yaml.safe_load((HA_DIR / "piano.yaml").read_text(encoding="utf-8"))


def publish_discovery(mqtt, sensors):
    """Every sensor as a retained MQTT discovery message: Home Assistant creates it by itself,
    so no YAML has to be copied there. The sensors keep the names the YAML package gave them."""
    import paho.mqtt.client as paho
    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="armonico-ha-setup")
    if mqtt.get("user"):
        client.username_pw_set(mqtt["user"], mqtt.get("pass", ""))
    client.connect(mqtt["host"], int(mqtt.get("port", 1883)), 30)
    client.loop_start()
    try:
        for s in sensors:
            s = dict(s)
            # The device is called "Piano", so Home Assistant puts that in front of the name:
            # "Piano Piano Status", sensor.piano_piano_status. The name drops its own "Piano ",
            # and the entity id is the one the automations and blueprints use (sensor.piano_status).
            if s["name"].startswith("Piano "):
                s["name"] = s["name"][len("Piano "):]
            s["default_entity_id"] = f"sensor.{s['unique_id']}"
            info = client.publish(f"homeassistant/sensor/{s['unique_id']}/config",
                                  json.dumps(s), qos=1, retain=True)
            info.wait_for_publish(10)
    finally:
        client.loop_stop()
        client.disconnect()


def fix_entity_ids(ha, sensors, seconds=40):
    """Makes the sensors' entity ids what the automations expect: sensor.piano_status and the rest.
    Home Assistant keeps the id an entity was first registered with, and older versions of it do not
    read "default_entity_id" at all, so the ids are set directly once the sensors are registered."""
    want = {s["unique_id"]: f"sensor.{s['unique_id']}" for s in sensors}
    deadline = time.time() + seconds
    renamed = 0
    done = set()
    while time.time() < deadline and len(done) < len(want):
        for e in ha.call("config/entity_registry/list"):
            uid = e.get("unique_id")
            if e.get("platform") != "mqtt" or uid not in want or uid in done:
                continue
            if e["entity_id"] == want[uid]:
                done.add(uid)
                continue
            try:
                ha.call("config/entity_registry/update", entity_id=e["entity_id"], new_entity_id=want[uid])
                renamed += 1
                done.add(uid)
            except HAError:
                pass            # the wanted id is taken: left as it is, and reported below
        if len(done) < len(want):
            time.sleep(2)
    return len(done), len(want), renamed


def cmd_package(ha, mqtt):
    pkg = load_package()

    # the helpers: skipped when they exist
    states = {s["entity_id"] for s in ha.rest("GET", "/api/states")}
    made = kept = 0
    for domain in ("input_boolean", "input_text", "input_datetime"):
        for object_id, conf in (pkg.get(domain) or {}).items():
            if f"{domain}.{object_id}" in states:
                kept += 1
                continue
            ha.call(f"{domain}/create", **conf)
            made += 1
    say("ok", f"Helpers: {made} created, {kept} already there")

    # the automations: written under their own ids, so a second run replaces them
    saved = 0
    for auto in pkg.get("automation", []):
        saved += keep_edited(ha, auto)
        ha.rest("POST", f"/api/config/automation/config/{auto['id']}", auto)
    if saved:
        say("ok", f"{saved} automation(s) had been changed in Home Assistant: the earlier version is kept in "
                  f"{backup_dir()} before it was replaced")
    time.sleep(3)
    loaded = [x for x in ha.rest("GET", "/api/states") if x["entity_id"].startswith("automation.piano_")]
    if len(loaded) >= len(pkg.get("automation", [])):
        say("ok", f"{len(loaded)} automations, running")
    else:
        say("warn", f"{len(pkg.get('automation', []))} automations were saved, but only {len(loaded)} are loaded. "
                    "configuration.yaml needs the line: automation: !include automations.yaml")

    # the blueprints
    names = []
    for f in sorted(HA_DIR.glob("blueprint_*.yaml")):
        ha.call("blueprint/save", domain="automation", path=f"piano/{f.name}",
                yaml=f.read_text(encoding="utf-8"), allow_override=True)
        names.append(f.stem)
    say("ok", f"{len(names)} blueprints in the automation blueprints list")

    # the sensors, through the broker
    sensors = (pkg.get("mqtt") or {}).get("sensor", [])
    publish_discovery(mqtt, sensors)
    ok_ids, total, renamed = fix_entity_ids(ha, sensors)
    if ok_ids == total:
        say("ok", f"{total} sensors under one device named Piano, with the entity ids the automations use")
    else:
        say("warn", f"{total} sensors announced, {ok_ids} have the expected entity id. The others may still be "
                    "registering: run the installer again in a minute, or check Settings, Devices & services, MQTT")
    say("ok", "Done. The Telegram bot is the one thing left, and it is set up in Home Assistant itself")
    return 0


def telegram_connect(ha, token, chat_ids, endpoint=None, report=None):
    """The Telegram bot integration of Home Assistant, in polling mode, with the chats that may
    use it. Home Assistant keeps the allowed chats as sub-entries of the bot's entry.
    Returns (True, entry_id) when the bot and every chat are in, otherwise (False, reason)."""
    report = report or say
    entries = lambda: [e for e in ha.rest("GET", "/api/config/config_entries/entry") if e.get("domain") == "telegram_bot"]
    flow = ha.rest("POST", "/api/config/config_entries/flow", {"handler": "telegram_bot", "show_advanced_options": False}, timeout=60)
    if flow.get("type") == "abort":
        return False, f"Home Assistant stopped the setup: {flow.get('reason')}"
    schema = flow.get("data_schema") or []
    payload = {}
    for f in schema:
        name = f.get("name")
        if name == "platform":
            payload[name] = "polling"          # polling receives the commands, and needs no address from outside
        elif name == "api_key":
            payload[name] = token
        elif f.get("type") == "expandable":
            payload[name] = {}
            for sub in f.get("schema") or []:
                if sub.get("name") == "api_endpoint" and endpoint:
                    payload[name]["api_endpoint"] = endpoint
                elif sub.get("required") or "default" in sub:
                    payload[name][sub["name"]] = _plain_value(sub["name"], sub)
    for _ in range(3):
        try:
            result = ha.rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", payload, timeout=120)
            break
        except HAError as e:
            missing = re.findall(r"required key not provided at '([a-z_]+)\.([a-z_]+)'", str(e).replace("\\'", "'"))
            added = False
            for section, key in missing:
                if isinstance(payload.get(section), dict) and key not in payload[section]:
                    payload[section][key] = _plain_value(key, {})
                    added = True
            if not added:
                return False, str(e)
    if result.get("type") == "abort":
        reason = result.get("reason")
        if reason != "already_configured":
            return False, f"Home Assistant stopped the setup: {reason}"
        found = entries()
        if len(found) != 1:
            return False, "This bot is already in Home Assistant, under one of several Telegram bots: add its chats there"
        entry_id = found[0]["entry_id"]
        report("ok", "This bot was already in Home Assistant")
    elif result.get("errors"):
        return False, f"Home Assistant refused the bot: {result['errors']}"
    else:
        entry_id = (result.get("result") or {}).get("entry_id")
        if not entry_id:
            return False, f"unexpected answer: {str(result)[:150]}"
        report("ok", f"The Telegram bot is in Home Assistant, as '{result.get('title', 'the bot')}'")
    deadline = time.time() + 40
    while time.time() < deadline:                # the chats can only be added to a bot that is running
        if any(e["entry_id"] == entry_id and e.get("state") == "loaded" for e in entries()):
            break
        time.sleep(2)
    else:
        return False, "The bot was added but Home Assistant has not started it. Look at Settings, Devices & services, Telegram bot"
    for chat in chat_ids:
        sub = ha.rest("POST", "/api/config/config_entries/subentries/flow", {"handler": [entry_id, "allowed_chat_ids"]}, timeout=60)
        if sub.get("type") == "abort":
            return False, f"Home Assistant stopped adding the chat {chat}: {sub.get('reason')}"
        done = ha.rest("POST", f"/api/config/config_entries/subentries/flow/{sub['flow_id']}", {"chat_id": int(chat)}, timeout=60)
        if done.get("errors"):
            return False, (f"Telegram has no chat {chat} with this bot yet ({done['errors']}). Open the bot in Telegram, "
                           "press Start, and run this again")
        report("ok", f"Chat {done.get('title', chat)} may use the bot")
    have = next((e.get("num_subentries", 0) for e in entries() if e["entry_id"] == entry_id), 0)
    if have < len(chat_ids):
        return False, f"Only {have} of {len(chat_ids)} chats are in Home Assistant"
    return True, entry_id


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("broker", "package", "check"):
        print(__doc__)
        return 2
    try:
        cfg = ha = None
        error = None
        for url in candidates(os.environ.get("HA_URL", "")):
            ha = HA(url, os.environ.get("HA_TOKEN", ""))
            try:
                cfg = ha.rest("GET", "/api/config")
                break
            except HAError as e:
                error = e
        if cfg is None:
            raise error
        if sys.argv[1] == "check":
            print(f"version\t{cfg.get('version', '')}\nname\t{cfg.get('location_name', '')}")
            return 0
        if sys.argv[1] == "broker":
            return cmd_broker(ha)
        mqtt = {"host": os.environ.get("MQTT_HOST", ""), "port": os.environ.get("MQTT_PORT", "1883"),
                "user": os.environ.get("MQTT_USER", ""), "pass": os.environ.get("MQTT_PASS", "")}
        return cmd_package(ha, mqtt)
    except HAError as e:
        say("fail", str(e))
        return 1
    except Exception as e:                      # nothing may leave without a sentence
        say("fail", f"{type(e).__name__}: {e}")
        return 1
    finally:
        try:
            ha.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
