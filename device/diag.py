#!/usr/bin/env python3
"""The full diagnosis on the Armonico Pi screen.

The same checks as `armonico status` on the machine, in English and Hebrew, plus a few the
device needs: the network address, the clock, the temperature, the display and the power
button. Every check has its own timeout and they run side by side, so the whole run ends in
a few seconds even when something does not answer.

A fix that needs root (restarting mosquitto or the Armonico services) is asked for with a
request file, like the mode switch: the screen writes it, and the root unit
armonico-device-fix.path runs `diag.py apply`, which only knows the actions in FIXES.
"""
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def _load_cli():
    spec = importlib.util.spec_from_file_location("armonico_cli_for_diag", APP_DIR / "cli" / "armonico.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()           # its helpers, names and service list, so both say the same things
APP = cli.APP
VERSION = cli.VERSION
GITHUB_REPO = cli.REPO or "vestacorelabs/armonico"
DATA_DIR = Path(os.environ.get("DATA_DIR", f"/var/lib/{APP}"))
FIX_REQUEST = DATA_DIR / "device-fix-request"       # the root unit armonico-device-fix.path acts on it
UPDATE_CACHE = DATA_DIR / "device" / "update-check.json"
UPDATE_EVERY = 6 * 3600
BOOT_CONFIGS = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
DRM = Path("/sys/class/drm")
STEP_TIMEOUT = 2            # seconds for one command or connection
RUN_TIMEOUT = 4             # seconds for the whole run

Check = namedtuple("Check", "level key label_en label_he detail_en detail_he fix")
OK, NOTE, PROBLEM = 0, 1, 2

UNIT_HE = {"bridge": "גשר", "lessons": "שיעורים", "screen": "מסך", "device": "מכשיר", "recorder": "מקליט"}


def run_out(*cmd, timeout=STEP_TIMEOUT):
    return cli.run_out(*cmd, timeout=timeout)


def config():
    """The settings file when it can be read, else the environment the service started with."""
    cfg = dict(os.environ)
    try:
        cfg.update(re.findall(r"^([A-Z_]+)='([^']*)'", cli.CONFIG.read_text(), re.M))
    except OSError:
        pass
    return cfg


# ----------------------------------------------------------------------
# Network and display, also used by the screen
# ----------------------------------------------------------------------
def _usable(addr):
    try:
        ip = ipaddress.IPv4Address(addr)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def ipv4():
    """The machine's own IPv4 address, from the interface of the default route when there is one.
    Never IPv6, 127.x or 169.254.x; "" when there is no address."""
    found = []                                  # (interface, address) in the order ip lists them
    for line in run_out("ip", "-4", "-o", "addr", "show", "scope", "global").splitlines():
        m = re.match(r"\s*\d+:\s+(\S+?)(?:@\S+)?\s+inet\s+([\d.]+)/", line)
        if m and _usable(m.group(2)):
            found.append((m.group(1), m.group(2)))
    if found:
        for line in run_out("ip", "-4", "route", "show", "default").splitlines():
            m = re.search(r"\bdev\s+(\S+)", line)
            src = re.search(r"\bsrc\s+([\d.]+)", line)
            if src and _usable(src.group(1)) and any(a == src.group(1) for _, a in found):
                return src.group(1)
            if m:
                for dev, addr in found:
                    if dev == m.group(1):
                        return addr
        return found[0][1]
    # without the ip tool: hostname -I lists IPv6 too, in any order
    for word in run_out("hostname", "-I").split():
        if _usable(word):
            return word
    return ""


def mdns_name():
    name = socket.gethostname().split(".")[0]
    return f"{name}.local" if name else ""


def displays(root=DRM):
    """Connectors and their preferred mode: the first line of `modes` is the EDID's preferred one.
    [{'name': 'DSI-1', 'connected': True, 'mode': (800, 480)}, ...], connected ones first.
    HDMI off (DSI only): the HDMI ports are left out, so nothing on the device sees, lights or chooses an
    HDMI screen. The device runs on its own DSI screen, the BIGTREETECH PiTFT50 the case is built for."""
    out = []
    try:
        entries = sorted(Path(root).glob("card*-*"))
    except OSError:
        return out
    seen = set()
    for d in entries:
        name = d.name.split("-", 1)[1]
        if name.startswith("Writeback") or name in seen:
            continue
        if name.startswith("HDMI"):                  # HDMI off (DSI only): remove this line to see HDMI again
            continue
        try:
            status = (d / "status").read_text().strip()
        except OSError:
            continue
        seen.add(name)
        mode = None
        try:
            first = next((x for x in (d / "modes").read_text().split() if x), "")
            m = re.match(r"(\d+)x(\d+)", first)
            mode = (int(m.group(1)), int(m.group(2))) if m else None
        except OSError:
            pass
        out.append({"name": name, "connected": status == "connected", "mode": mode})
    out.sort(key=lambda x: not x["connected"])
    return out


# ----------------------------------------------------------------------
# Update check
# ----------------------------------------------------------------------
def _version(text):
    """A version to compare: 1.1.0 > 1.1.0-rc2 > 1.1.0-rc1 > 1.0.9 (a pre-release before its release,
    as semver has it; before, every number counted, and v1.1.0-rc2 came out newer than 1.1.0).
    None for text with no version in it."""
    if not isinstance(text, str):
        return None
    m = re.search(r"(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z.-]+)|([A-Za-z][0-9A-Za-z.-]*))?", text)
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split(".")]
    pre = m.group(2) or m.group(3)
    # each part of a pre-release by itself: numbers as numbers, below words (rc2 < rc10, alpha < beta)
    parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x.lower())
                  for x in re.findall(r"\d+|[A-Za-z]+", pre or ""))
    return tuple(nums) + (0,) * (4 - len(nums)) + ((0, parts) if pre else (1, ()))


def shown(text):
    v = re.sub(r"^v", "", text.strip())
    return re.sub(r"^(\d+\.\d+)\.0$", r"\1", v)


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"{APP}-device/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def latest_release(timeout=4):
    """(tag, notes) of the newest release, or of the newest tag when there are no releases."""
    base = f"https://api.github.com/repos/{GITHUB_REPO}"
    try:
        rel = _get_json(f"{base}/releases/latest", timeout)
        if rel.get("tag_name"):
            return rel["tag_name"], (rel.get("body") or "").strip()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    tags = _get_json(f"{base}/tags", timeout)
    best = max((t["name"] for t in tags if _version(t.get("name"))), key=_version, default="")
    return best, ""


_update_memo = {"at": 0.0, "value": None}


def update_available(timeout=4):
    """(newer version, notes) when GitHub has a newer release than this one, else None.
    Asked at most every 6 hours; offline gives None and asks again after 10 minutes."""
    now = time.time()
    if now - _update_memo["at"] < (UPDATE_EVERY if _update_memo["value"] is not False else 600):
        return _update_memo["value"] or None
    tag = notes = None
    try:
        cached = json.loads(UPDATE_CACHE.read_text())
        if now - cached.get("at", 0) < UPDATE_EVERY:
            tag, notes = cached.get("tag", ""), cached.get("notes", "")
    except (OSError, ValueError, AttributeError, TypeError):   # TypeError: an "at" that is not a number
        pass
    if tag is None:
        try:
            tag, notes = latest_release(timeout)
        except Exception:
            _update_memo.update(at=now, value=False)
            return None
        try:
            UPDATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = UPDATE_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": now, "tag": tag, "notes": notes}))
            tmp.replace(UPDATE_CACHE)
        except OSError:
            pass
    mine, theirs = _version(VERSION), _version(tag)
    value = (shown(tag), notes) if mine and theirs and theirs > mine else None
    _update_memo.update(at=now, value=value)
    return value


# ----------------------------------------------------------------------
# The checks. Each returns one Check, or a list of them.
# ----------------------------------------------------------------------
def _units(cfg):
    units = list(cli.SERVICES)
    if cfg.get("DEVICE") == "yes":
        units += cli.DEVICE_UNITS
    return units


def check_services(cfg):
    units = _units(cfg)
    states = run_out("systemctl", "is-active", *units).split()
    states += ["unknown"] * (len(units) - len(states))
    short = [u[len(APP) + 1:] if u.startswith(APP + "-") else u for u in units]
    down = [(s, st) for s, st in zip(short, states) if st != "active"]
    if not down:
        return Check(OK, "services", "Services", "שירותים",
                     f"{', '.join(short)}: all running",
                     f"{', '.join(UNIT_HE.get(s, s) for s in short)}: כולם רצים", "")
    return Check(PROBLEM, "services", "Services", "שירותים",
                 "not running: " + ", ".join(f"{s} ({st})" for s, st in down),
                 "לא רצים: " + ", ".join(UNIT_HE.get(s, s) for s, _ in down), "services")


def _mqtt_login(host, port, user, password, timeout=STEP_TIMEOUT):
    """One MQTT 3.1.1 CONNECT and its CONNACK: True, or the reason it failed."""
    def s(x):
        b = x.encode()
        return struct.pack("!H", len(b)) + b
    flags = 0x02 | (0x80 if user else 0) | (0x40 if user else 0)
    body = s("MQTT") + bytes([4, flags]) + struct.pack("!H", 10) + s(f"{APP}-diag-{os.getpid()}")
    if user:
        body += s(user) + s(password)
    n, size = len(body), b""
    while True:
        n, b = divmod(n, 128)
        size += bytes([b | (0x80 if n else 0)])
        if not n:
            break
    with socket.create_connection((host, port), timeout=timeout) as sk:
        sk.settimeout(timeout)
        sk.sendall(b"\x10" + size + body)
        ack = b""
        while len(ack) < 4:
            chunk = sk.recv(4 - len(ack))
            if not chunk:
                break
            ack += chunk
        try:
            sk.sendall(b"\xe0\x00")
        except OSError:
            pass
    if len(ack) < 4 or ack[0] != 0x20:
        return "no MQTT answer"
    return True if ack[3] == 0 else {4: "wrong user name or password", 5: "not allowed"}.get(ack[3], f"refused ({ack[3]})")


def _local(host):
    return host in ("", "localhost", "::1") or host.startswith("127.")


def check_broker(cfg):
    host, port = cfg.get("MQTT_HOST", "localhost"), cfg.get("MQTT_PORT") or "1883"
    where = f"{host}:{port}"
    local = cfg.get("MQTT_LOCAL") == "yes" or _local(host)
    start = time.monotonic()
    try:
        res = _mqtt_login(host, int(port), cfg.get("MQTT_USER", ""), cfg.get("MQTT_PASS", ""))
    except (OSError, ValueError) as e:
        res = "no answer" if isinstance(e, (socket.timeout, ConnectionRefusedError)) else str(e)
    ms = round((time.monotonic() - start) * 1000)
    if res is True:
        return Check(OK, "broker", "Broker", "ברוקר", f"connected · {ms}ms", f"מחובר · {ms}ms", "")
    if local and run_out("systemctl", "is-active", "mosquitto").strip() != "active":
        return Check(PROBLEM, "broker", "Broker", "ברוקר", f"no answer at {where} · mosquitto stopped",
                     f"לא עונה ב-{where} · השירות mosquitto נעצר", "mosquitto")
    if res == "wrong user name or password":
        return Check(PROBLEM, "broker", "Broker", "ברוקר", f"{where} refused the login: wrong user name or password",
                     f"{where} דחה את ההתחברות: שם משתמש או סיסמה שגויים", "")
    return Check(PROBLEM, "broker", "Broker", "ברוקר", f"{where}: {res}",
                 f"לא עונה ב-{where}" if res == "no answer" else f"{where}: {res}",
                 "mosquitto" if local else "")


def check_lesson_screen(cfg):
    port = cfg.get("LESSON_UI_PORT") or "8099"
    data = Path(cfg.get("DATA_DIR") or DATA_DIR)
    try:
        code = data / "screen_code"
        head = {"X-Screen-Code": code.read_text().strip()} if code.exists() else {}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/overview", headers=head)
        with urllib.request.urlopen(req, timeout=STEP_TIMEOUT) as r:
            songs = json.loads(r.read()).get("songs", [])
    except PermissionError:
        return Check(NOTE, "lesson_screen", "Lesson screen", "מסך השיעור", "the screen code cannot be read",
                     "אין גישה לקוד המסך", "")
    except Exception:
        return Check(PROBLEM, "lesson_screen", "Lesson screen", "מסך השיעור", f"no answer on port {port}",
                     f"לא עונה בפורט {port}", "services")
    bad = [cli.nice(x.get("name", "")) for x in songs if not x.get("total")]
    if bad:
        return Check(NOTE, "lesson_screen", "Lesson screen", "מסך השיעור",
                     f"answers on port {port} · {len(bad)} of {len(songs)} songs cannot be lessons: {', '.join(bad[:3])}",
                     f"עונה בפורט {port} · {len(bad)} מתוך {len(songs)} שירים לא יכולים להיות שיעור: {', '.join(bad[:3])}", "")
    return Check(OK, "lesson_screen", "Lesson screen", "מסך השיעור", f"answers on port {port} · {len(songs)} songs",
                 f"עונה בפורט {port} · {len(songs)} שירים", "")


def check_network(cfg):
    ip = ipv4()
    if not ip:
        return Check(NOTE, "network", "Network", "רשת", "no network · everything on the device works",
                     "אין רשת · הכל במכשיר עובד", "")
    name = mdns_name() if run_out("systemctl", "is-active", "avahi-daemon").strip() == "active" else ""
    text = f"IPv4 {ip}" + (f" · {name}" if name else "")
    return Check(OK, "network", "Network", "רשת", text, text, "")


def check_time(cfg):
    synced = run_out("timedatectl", "show", "-p", "NTPSynchronized", "--value").strip()
    if synced == "yes":
        return Check(OK, "time", "Time", "שעה", "synced from the network (NTP)", "מסונכרנת מהרשת (NTP)", "")
    # the Pi 4 has no clock of its own: unsynced, it goes on from the last shutdown
    return Check(NOTE, "time", "Time", "שעה", "not synced · goes on from the last shutdown",
                 "לא מסונכרנת · נמשכת מהכיבוי האחרון", "")


def check_power(cfg):
    thr = run_out("vcgencmd", "get_throttled").strip()
    if not thr.startswith("throttled="):
        return None
    try:
        bits = int(thr.split("=")[1], 16)
    except ValueError:
        return None
    if bits & 0x1:
        return Check(PROBLEM, "power", "Power", "חשמל",
                     "under-voltage right now: the PoE switch or HAT is not giving enough",
                     "ירידת מתח עכשיו: מתג ה-PoE או ה-HAT לא נותנים מספיק", "")
    if bits & 0x50000:
        return Check(NOTE, "power", "Power", "חשמל", "under-voltage or throttling happened since the last start",
                     "הייתה ירידת מתח או האטה מאז ההפעלה האחרונה", "")
    return Check(OK, "power", "Power", "חשמל", "no under-voltage since the last start (vcgencmd)",
                 "אין ירידת מתח מאז ההפעלה האחרונה (vcgencmd)", "")


def cpu_temp():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000
    except (OSError, ValueError):
        m = re.search(r"temp=([\d.]+)", run_out("vcgencmd", "measure_temp"))
        return float(m.group(1)) if m else None


def check_temperature(cfg):
    t = cpu_temp()
    if t is None:
        return None
    text = f"{t:.0f}°C"
    if t >= 80:
        return Check(PROBLEM, "temperature", "Temperature", "טמפרטורה", f"{text} · the Pi slows itself down",
                     f"{text} · ה-Pi מאט את עצמו", "")
    if t >= 70:
        return Check(NOTE, "temperature", "Temperature", "טמפרטורה", f"{text} · hot, check the air around it",
                     f"{text} · חם, כדאי לבדוק שיש אוויר מסביב", "")
    return Check(OK, "temperature", "Temperature", "טמפרטורה", text, text, "")


def check_disk(cfg):
    data = Path(cfg.get("DATA_DIR") or DATA_DIR)
    try:
        st = os.statvfs(data if data.exists() else "/")
    except OSError:
        return None
    free, total = st.f_bavail * st.f_frsize / 1e9, st.f_blocks * st.f_frsize / 1e9
    if free > 1:
        return Check(OK, "disk", "Disk", "דיסק", f"{free:.1f}GB free of {total:.0f}GB",
                     f"{free:.1f}GB פנויים מתוך {total:.0f}GB", "")
    return Check(NOTE, "disk", "Disk", "דיסק", f"{free:.1f}GB free of {total:.0f}GB, running low",
                 f"רק {free:.1f}GB פנויים מתוך {total:.0f}GB", "")


def as_sound_user(*cmd):
    """A PipeWire command (wpctl) run against the device's own PipeWire, the service user's, which plays
    everything and is the only one there (install.sh starts PipeWire for that user alone). As that user:
    as it is. As root: through runuser. As anyone else: None, since that PipeWire's socket is in the
    service user's /run/user, which no other user may open. Before, a check run from a login as pi asked
    pi's own PipeWire, a second one that knew nothing of the device's sound."""
    import pwd
    try:
        uid = pwd.getpwnam(APP).pw_uid
    except KeyError:
        return list(cmd)                     # no service user: not a device, asked as it is
    if os.geteuid() == uid:
        return list(cmd)
    if os.geteuid() == 0 and shutil.which("runuser"):
        return ["runuser", "-u", APP, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}", *cmd]
    return None


def check_sound(cfg):
    if "Headphones" not in run_out("aplay", "-l"):
        return Check(PROBLEM, "sound", "Sound card", "כרטיס קול", "no headphone jack in aplay -l",
                     "אין יציאת אוזניות ב-aplay -l", "")
    if shutil.which("wpctl"):
        # everything plays through PipeWire: it has to answer, and its default is where the sound goes
        cmd = as_sound_user("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@")
        if cmd is None:
            return Check(NOTE, "sound", "Sound", "שמע", f"PipeWire not checked: it is the user {APP}'s, run with sudo",
                         f"PipeWire לא נבדק: הוא של המשתמש {APP}, להריץ עם sudo", "")
        out = run_out(*cmd)
        name = next((x.split("=", 1)[1].strip().strip('"') for x in out.splitlines() if "node.description" in x), "")
        if not name:
            return Check(PROBLEM, "sound", "Sound", "שמע", "PipeWire does not answer, so nothing plays",
                         "PipeWire לא עונה, ולכן שום צליל לא מתנגן", "")
        return Check(OK, "sound", "Sound", "שמע", f"PipeWire · {name}", f"PipeWire · {name}", "")
    mix = re.search(r"\[(-?[\d.]+dB)\]", run_out("amixer", "-c", "Headphones", "sget", "PCM"))
    en = f"Headphones · AUX {mix.group(1)}" if mix else "Headphones"
    he = f"Headphones · AUX \u200f{mix.group(1)}" if mix else "Headphones"     # the mark keeps "-6dB" whole in Hebrew
    return Check(OK, "sound", "Sound card", "כרטיס קול", en, he, "")


def check_samples(cfg):
    folder = Path(cfg.get("DATA_DIR") or DATA_DIR) / "device" / "samples"
    n = len(list(folder.glob("*.wav"))) if folder.is_dir() else 0
    if n >= 88:
        return Check(OK, "samples", "Piano samples", "דגימות הפסנתר", f"{n} of 88", f"{n} מתוך 88", "")
    return Check(PROBLEM, "samples", "Piano samples", "דגימות הפסנתר",
                 f"{n} of 88: sudo ./install.sh --device prepares them",
                 f"{n} מתוך 88: ההתקנה sudo ./install.sh --device מכינה אותן", "")


def check_midi(cfg):
    amidi = cfg.get("AMIDI_DEVICE", "hw:7,0")
    if amidi.split(":")[-1] in run_out("amidi", "-l"):
        out = Check(OK, "midi_out", "Lesson output", "פלט השיעור", amidi, amidi, "")
    else:
        out = Check(PROBLEM, "midi_out", "Lesson output", "פלט השיעור", f"{amidi} missing: sudo modprobe snd-virmidi",
                    f"{amidi} חסר: sudo modprobe snd-virmidi", "")
    name = cfg.get("MIDI_DEVICE", "Armonico Pi")
    if name in run_out("aconnect", "-i"):
        keys = Check(OK, "keys", "Keys", "קלידים", f"'{name}' on the sequencer", f"'{name}' מחובר", "")
    else:
        keys = Check(PROBLEM, "keys", "Keys", "קלידים", f"'{name}' not on the sequencer",
                     f"'{name}' לא מחובר", "services")
    return [out, keys]


def check_gpio(cfg):
    """The chip is there; whether the keys and buttons are wired cannot be read from it.
    Each input has a pull-up, so a loose wire reads exactly like a key that is not pressed.
    Only the hardware test, a press of each one by hand, tells, and it is kept once passed."""
    if not Path("/dev/gpiochip0").exists():
        return Check(PROBLEM, "gpio", "GPIO", "GPIO", "no /dev/gpiochip0", "אין /dev/gpiochip0", "")
    try:
        data = Path(cfg.get("DATA_DIR", "/var/lib/armonico"), "device", "settings.json").read_text()
        passed = (json.loads(data).get("hw_test") or {}).get("at")
    except (OSError, ValueError, AttributeError):
        passed = None
    if not passed:
        return Check(NOTE, "gpio", "Keys and buttons", "קלידים וכפתורים",
                     "not tested by hand yet · Settings › System › Device test",
                     "עוד לא נבדקו ידנית · הגדרות › מערכת › בדיקת המכשיר", "")
    when = time.strftime("%d/%m/%Y", time.localtime(passed))
    return Check(OK, "gpio", "Keys and buttons", "קלידים וכפתורים", f"all pressed in the hardware test, {when}",
                 f"כולם נלחצו בבדיקת החומרה, {when}", "")


def check_display(cfg):
    on = [d for d in displays() if d["connected"]]
    if on:
        text = ", ".join(d["name"] + (f" · {d['mode'][0]}×{d['mode'][1]}" if d["mode"] else "") for d in on)
        return Check(OK, "display", "Display", "מסך", text, text, "")
    if Path("/dev/dri").is_dir() and any(Path("/dev/dri").glob("card*")):
        return Check(NOTE, "display", "Display", "מסך", "no connected display found", "לא נמצא מסך מחובר", "")
    return Check(PROBLEM, "display", "Display", "מסך", "no /dev/dri: vc4-kms-v3d is off in config.txt",
                 "אין /dev/dri: ההגדרה vc4-kms-v3d כבויה ב-config.txt", "")


def boot_config():
    return next((p for p in BOOT_CONFIGS if p.exists()), None)


def check_config_txt(cfg, path=None):
    path = path or boot_config()
    if not path:
        return None
    text = path.read_text(errors="replace")
    on = lambda key: re.search(rf"^\s*{re.escape(key)}", text, re.M) is not None
    en, he = [], []
    if on("dtparam=spi=on"):
        en.append("SPI is on, the keys on GPIO 7 to 11 need it off")
        he.append("SPI פועל, והקלידים ב-GPIO 7 עד 11 צריכים אותו כבוי")
    if on("dtparam=i2c_arm=on"):
        en.append("I2C is on, the power button needs it off")
        he.append("I2C פועל, וכפתור ההפעלה צריך אותו כבוי")
    if en:
        return Check(NOTE, "config_txt", "config.txt", "config.txt", "; ".join(en), "; ".join(he), "")
    return Check(OK, "config_txt", "config.txt", "config.txt", "SPI and I2C off", "SPI ו-I2C כבויים", "")


def check_power_button(cfg, path=None):
    """Only GPIO3 can start a halted Pi again, so the shutdown overlay belongs there."""
    path = path or boot_config()
    if not path:
        return None
    line = next((x.strip() for x in path.read_text(errors="replace").splitlines()
                 if re.match(r"\s*dtoverlay=gpio-shutdown\b", x)), "")
    if not line:
        return Check(NOTE, "power_button", "Power button", "כפתור הפעלה",
                     "no power button overlay: dtoverlay=gpio-shutdown,gpio_pin=3",
                     "אין שכבת כפתור הפעלה: dtoverlay=gpio-shutdown,gpio_pin=3", "")
    m = re.search(r"gpio_pin=(\d+)", line)
    pin = int(m.group(1)) if m else 3
    if pin == 3:
        return Check(OK, "power_button", "Power button", "כפתור הפעלה", "shuts down and starts the Pi",
                     "מכבה ומדליק את ה-Pi", "")
    return Check(NOTE, "power_button", "Power button", "כפתור הפעלה",
                 f"on GPIO{pin}: it shuts down, but cannot start the Pi again",
                 f"על GPIO{pin}: מכבה, אבל לא מדליק את ה-Pi מחדש", "")


BASE_CHECKS = [check_services, check_broker, check_lesson_screen, check_network, check_time,
               check_power, check_temperature, check_disk]
DEVICE_CHECKS = [check_sound, check_samples, check_midi, check_gpio, check_display,
                 check_config_txt, check_power_button]
LATE = {check_services: ("services", "Services", "שירותים"), check_broker: ("broker", "Broker", "ברוקר"),
        check_lesson_screen: ("lesson_screen", "Lesson screen", "מסך השיעור"), check_network: ("network", "Network", "רשת"),
        check_time: ("time", "Time", "שעה"), check_power: ("power", "Power", "חשמל"),
        check_temperature: ("temperature", "Temperature", "טמפרטורה"), check_disk: ("disk", "Disk", "דיסק"),
        check_sound: ("sound", "Sound card", "כרטיס קול"), check_samples: ("samples", "Piano samples", "דגימות הפסנתר"),
        check_midi: ("midi_out", "Lesson output", "פלט השיעור"), check_gpio: ("gpio", "GPIO", "GPIO"),
        check_display: ("display", "Display", "מסך"), check_config_txt: ("config_txt", "config.txt", "config.txt"),
        check_power_button: ("power_button", "Power button", "כפתור הפעלה")}


def run_checks(timeout=RUN_TIMEOUT, cfg=None):
    """Every check at once, each in its own thread. One that has not answered by the end is
    reported as such; its thread is left behind (daemon) and never holds the screen."""
    cfg = cfg if cfg is not None else config()
    funcs = BASE_CHECKS + (DEVICE_CHECKS if cfg.get("DEVICE", "yes") == "yes" else [])
    results = [None] * len(funcs)
    done = [threading.Event() for _ in funcs]

    def one(i, f):
        try:
            results[i] = f(cfg)
        except Exception as e:
            key, en, he = LATE[f]
            results[i] = Check(NOTE, key, en, he, f"the check failed: {e}", f"הבדיקה נכשלה: {e}", "")
        done[i].set()

    for i, f in enumerate(funcs):
        threading.Thread(target=one, args=(i, f), daemon=True, name=f"diag-{f.__name__}").start()
    end = time.monotonic() + timeout
    for ev in done:
        ev.wait(max(0.0, end - time.monotonic()))
    out = []
    for i, f in enumerate(funcs):
        r = results[i] if done[i].is_set() else None
        if not done[i].is_set():
            key, en, he = LATE[f]
            r = Check(NOTE, key, en, he, f"no answer within {timeout:g} seconds", f"לא ענה תוך {timeout:g} שניות", "")
        if isinstance(r, list):
            out.extend(r)
        elif r is not None:
            out.append(r)
    return out


# ----------------------------------------------------------------------
# Fixes
# ----------------------------------------------------------------------
# action: the steps the screen shows while it runs, (English, Hebrew)
FIXES = {
    "mosquitto": [("Restarting mosquitto", "הפעלה מחדש של mosquitto"), ("Testing the connection", "בדיקת חיבור")],
    "services": [("Restarting the Armonico services", "הפעלה מחדש של שירותי Armonico"),
                 ("Checking the services", "בדיקת השירותים")],
}


def fix(action, progress=None, timeout=20, cfg=None):
    """Asks the root unit for one fix and follows it. progress(i) is called as step i starts.
    Returns the steps as [{'en', 'he', 'ok'}]; an unknown action returns []."""
    if action not in FIXES:
        return []
    cfg = cfg if cfg is not None else config()
    steps = [{"en": en, "he": he, "ok": False} for en, he in FIXES[action]]
    if progress:
        progress(0)
    try:
        # whole or not at all: the path unit can start on the file's creation, and read it empty
        tmp = FIX_REQUEST.with_name(f".{FIX_REQUEST.name}.{os.getpid()}.tmp")
        tmp.write_text(action + "\n")
        tmp.replace(FIX_REQUEST)
    except OSError:
        return steps
    end = time.monotonic() + timeout
    while FIX_REQUEST.exists() and time.monotonic() < end:
        time.sleep(0.25)
    if FIX_REQUEST.exists():               # nobody took it: the path unit is not running
        try:
            FIX_REQUEST.unlink()
        except OSError:
            pass
        return steps
    steps[0]["ok"] = True
    if progress:
        progress(1)
    check = check_broker if action == "mosquitto" else check_services
    while True:
        res = check(cfg)
        if res.level == OK or time.monotonic() >= end:
            break
        time.sleep(1)
    steps[1]["ok"] = res.level == OK
    return steps


def read_nofollow(path, limit=4096):
    """A file in the service user's folder, read by root only when it is a plain file: never through a
    link, and never waiting on a FIFO put there in its place (as root_actions.read_nofollow)."""
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError("not a plain file")
        return f.read(limit).decode("utf-8", "replace")


def apply():
    """As root, from armonico-device-fix.service: does the one action the request file names."""
    try:
        want = read_nofollow(FIX_REQUEST).strip()
    except OSError:
        try:
            FIX_REQUEST.unlink()                          # a link or a FIFO in its place: removed, nothing done
        except OSError:
            pass
        return
    try:
        FIX_REQUEST.unlink()
    except OSError:
        pass
    if want == "mosquitto":
        subprocess.run(["systemctl", "restart", "mosquitto"], check=False, timeout=60)
    elif want == "services":
        # not the screen: it asked for this and is waiting for the answer
        units = list(cli.SERVICES) + [u for u in (f"{APP}-device", f"{APP}-recorder")
                                      if subprocess.run(["systemctl", "is-enabled", "--quiet", u]).returncode == 0]
        subprocess.run(["systemctl", "restart", *units], check=False, timeout=90)


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        apply()
    else:
        for c in run_checks():
            print(f"{'✔!✘'[c.level]} {c.label_en:<14} {c.detail_en}" + (f"  [fix: {c.fix}]" if c.fix else ""))
