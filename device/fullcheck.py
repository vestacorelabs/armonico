#!/usr/bin/env python3
"""fullcheck.py - the full check of the Armonico Pi: its automatic part, and the report file.

Settings › System › Full check on the screen runs this, then the 9 guided steps (device/ui/
views_check.py); `armonico check` in a terminal runs this part alone. It reuses the checks of
diag.py and adds what the diagnosis does not look at: the bridge's status kept on the broker,
Wi-Fi, the internet services the device uses, power and memory since boot, the SD card, the
settings files, the log, the backups, the alarm clock and Telegram.

Nothing here changes the device: no alarm, no backup, no message, no setting, no fix. Commands are
only read (vcgencmd, nmcli, wpctl, pinctrl, journalctl, dmesg); what cannot be read without root is
marked "not checked", never failed. The one root action asked for, telegram-kept, answers only
whether a bot token is kept. The only file written is the report, in DATA_DIR/device/checks.

21 areas, each giving one or more results. Every word is in device/ui/texts.py, in English and
Hebrew; a result keeps both, so the screen shows its language and the report is in English.
"""
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import diag                                       # noqa: E402  (device/diag.py)


def _load_texts():
    spec = importlib.util.spec_from_file_location("armonico_texts_for_check", HERE / "ui" / "texts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


texts = _load_texts()

OK, NOTE, PROBLEM, SKIP = 0, 1, 2, 3        # passed, warning, failed, not checked
MARK = {OK: "OK", NOTE: "WARN", PROBLEM: "FAIL", SKIP: "SKIP"}
AREA_TIMEOUT = 15          # seconds for one area; one that has not answered by then is "not checked"
NET_TIMEOUT = 4            # seconds for one internet service
WORKERS = 4                # areas checked at a time, so the device stays quick while it runs
KEEP_REPORTS = 10
PROC = Path("/proc")
FAMILIES = ("01-vibraphone", "02-organ", "03-guitar", "04-bass", "05-violin", "06-strings", "07-trumpet",
            "08-sax", "09-flute", "10-lead", "11-pad", "12-atmosphere", "13-kalimba", "14-steeldrums",
            "15-birds", "drums")          # armonico_pi.FAMILIES after the piano, and the drum kit
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()
WHITES = (0, 2, 4, 5, 7, 9, 11)
BLACK_OF = {1: 1, 3: 2, 6: 4, 8: 5, 10: 6}      # a black key is named after the white key under it: 1# to 6#


class Named(str):
    """A text with its Hebrew twin, for a name that carries a word ("high"): L() uses .he for Hebrew."""
    he = ""


def key_name(pc):
    """A key as the device labels it (1 to 7, 1# to 6#), with its note: "5 (G)". Key 12 is the high C
    (one octave above key 0, also number 1): "1 (C, high)"."""
    if pc == 12:
        n = Named(f"1 ({NOTE_NAMES[0]}, {texts.tr('en', 'chk.high')})")
        n.he = f"1 ({NOTE_NAMES[0]}, {texts.tr('he', 'chk.high')})"
        return n
    pc %= 12
    num = str(WHITES.index(pc) + 1) if pc in WHITES else f"{BLACK_OF[pc]}#"
    return f"{num} ({NOTE_NAMES[pc]})"
UNIT_KEY = {"bridge": "u.bridge", "lessons": "u.lessons", "device": "u.device", "screen": "u.screen",
            "recorder": "u.recorder"}

# One result. Every text is a pair (English, Hebrew). area: the area's key; row: the short text of
# the area's row on the screen; detail: the whole result; todo: one line of what to do, for a
# warning or a failure; fix: a diag.FIXES action, or ""; link: "test" for the hardware test, or "".
Item = namedtuple("Item", "level area label row detail todo fix link")


def key_names(pcs):
    """Several keys named, joined, in both languages (a Named)."""
    names = [key_name(p) for p in pcs]
    out = Named(", ".join(names))
    out.he = ", ".join(getattr(n, "he", "") or str(n) for n in names)
    return out


def L(key, **kw):
    """One text in both languages."""
    he = {k: (v.he if isinstance(v, Named) and v.he else v) for k, v in kw.items()}
    return texts.tr("en", key, **kw), texts.tr("he", key, **he)


def same(s):
    return str(s), str(s)


NONE = ("", "")


def item(level, area, row, detail=None, todo=NONE, fix="", link="", label=None):
    lab = label or L("chk.a." + area)
    return Item(level, area, lab, row, detail or row, todo, fix, link)


def skip(area, why_key, **kw):
    t = L(why_key, **kw)
    return item(SKIP, area, L("chk.notChecked"), t)


# ----------------------------------------------------------------------
# What the checks read
# ----------------------------------------------------------------------
def run(*cmd, timeout=5):
    """(returncode, stdout, stderr), or None when the command is missing or hangs. In English."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode, r.stdout, r.stderr


def data_dir(cfg):
    return Path(cfg.get("DATA_DIR") or diag.DATA_DIR)


def device_settings(cfg, ctx):
    """The device's settings: the screen's own copy when the screen runs the check, else the file."""
    s = (ctx or {}).get("settings")
    if isinstance(s, dict):
        return s
    try:
        d = json.loads((data_dir(cfg) / "device" / "settings.json").read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def online(cfg, ctx):
    v = (ctx or {}).get("online")
    return bool(diag.ipv4()) if v is None else bool(v)


def from_diag(c, row=None, todo_key=None, area=None, link=""):
    """A diag.Check as a result. Its own texts stay as diag wrote them."""
    level = {diag.OK: OK, diag.NOTE: NOTE, diag.PROBLEM: PROBLEM}.get(c.level, NOTE)
    detail = (c.detail_en, c.detail_he)
    todo = L(todo_key) if todo_key and level in (NOTE, PROBLEM) else NONE
    fix = c.fix if level == PROBLEM and c.fix in diag.FIXES else ""
    return Item(level, area or c.key, (c.label_en, c.label_he), row or (L("chk.ok") if level == OK else detail),
                detail, todo, fix, link)


def first(text):
    return text.split(" · ")[0]


# ----------------------------------------------------------------------
# The 21 areas
# ----------------------------------------------------------------------
def a_services(cfg, ctx):
    c = diag.check_services(cfg)
    if c.level == diag.OK:
        return [from_diag(c, area="services")]
    down = re.findall(r"(\w+) \(", c.detail_en)
    row = (texts.tr("en", "chk.r.down", x=", ".join(down)),
           texts.tr("he", "chk.r.down", x=", ".join(texts.tr("he", UNIT_KEY[d]) if d in UNIT_KEY else d for d in down)))
    return [from_diag(c, area="services", row=row, todo_key="chk.do.services")]


def mqtt_retained(cfg, topic, timeout=3):
    """The value kept on the broker for one topic, "" when none, None when it cannot be asked."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return None
    got, done = {}, threading.Event()
    cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-check-{os.getpid()}-{secrets.token_hex(3)}")
    if cfg.get("MQTT_USER"):
        cl.username_pw_set(cfg["MQTT_USER"], cfg.get("MQTT_PASS", ""))

    def on_connect(c, u, f, rc, p=None):
        if getattr(rc, "is_failure", False):
            done.set()
            return
        c.subscribe(topic)
        threading.Timer(1.5, done.set).start()    # no retained value comes within that: there is none

    def on_message(c, u, m):
        if m.retain:
            got["v"] = m.payload.decode(errors="replace").strip()
        done.set()
    cl.on_connect, cl.on_message = on_connect, on_message
    try:
        cl.connect(cfg.get("MQTT_HOST") or "localhost", int(cfg.get("MQTT_PORT") or 1883), keepalive=10)
        cl.loop_start()
        done.wait(timeout)
    except (OSError, ValueError):
        return None
    finally:
        try:
            cl.disconnect()
            cl.loop_stop()
        except Exception:
            pass
    return got.get("v", "")


def a_broker(cfg, ctx):
    c = diag.check_broker(cfg)
    out = [from_diag(c, area="broker", row=None if c.level == diag.OK else L("chk.r.noBroker"),
                     todo_key="chk.do.broker")]
    if c.level != diag.OK:
        out.append(item(SKIP, "broker", L("chk.notChecked"), L("chk.d.statusNoBroker"), label=L("chk.l.status")))
        return out
    v = mqtt_retained(cfg, "piano/status")
    lab = L("chk.l.status")
    if v is None:
        out.append(item(SKIP, "broker", L("chk.notChecked"), L("chk.d.noPaho"), label=lab))
    elif v == "online":
        out.append(item(OK, "broker", L("chk.ok"), L("chk.d.statusOnline"), label=lab))
    elif v == "":
        out.append(item(NOTE, "broker", L("chk.r.noStatus"), L("chk.d.noStatus"), L("chk.do.services"),
                        fix="services", label=lab))
    elif v == "offline":
        out.append(item(PROBLEM, "broker", L("chk.r.offline"), L("chk.d.offline"), L("chk.do.services"),
                        fix="services", label=lab))
    else:
        out.append(item(NOTE, "broker", same(v), L("chk.d.statusOther", v=v), L("chk.do.statusOther"), label=lab))
    return out


def a_lesson_screen(cfg, ctx):
    c = diag.check_lesson_screen(cfg)
    if c.level == diag.PROBLEM:
        return [from_diag(c, area="lesson_screen", row=L("chk.r.noAnswer"), todo_key="chk.do.services")]
    if c.level == diag.NOTE and "code" in c.detail_en:
        return [from_diag(c, area="lesson_screen", row=L("chk.r.noCode"), todo_key="chk.do.code")]
    bad = re.search(r"(\d+) of \d+ songs", c.detail_en)
    out = [from_diag(c, area="lesson_screen", row=L("chk.r.notLessons", n=bad.group(1)) if bad else None,
                     todo_key="chk.do.notLesson")]
    port = cfg.get("LESSON_UI_PORT") or "8099"
    data = data_dir(cfg)
    lab = L("chk.l.songList")
    try:
        code = data / "screen_code"
        head = {"X-Screen-Code": code.read_text().strip()} if code.exists() else {}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/overview", headers=head)
        with urllib.request.urlopen(req, timeout=diag.STEP_TIMEOUT) as r:
            body = json.loads(r.read())
    except Exception:
        out.append(item(SKIP, "lesson_screen", L("chk.notChecked"), L("chk.d.noList"), label=lab))
        return out
    songs = body.get("songs") if isinstance(body, dict) else None
    if not isinstance(songs, list) or not all(isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"]
                                              for x in songs):
        out.append(item(PROBLEM, "lesson_screen", L("chk.r.badList"), L("chk.d.badList"), L("chk.do.services"),
                        fix="services", label=lab))
    elif not songs:
        out.append(item(NOTE, "lesson_screen", L("chk.r.noSongs"), L("chk.d.noSongs"), L("chk.do.noSongs"), label=lab))
    else:
        out.append(item(OK, "lesson_screen", L("chk.r.songs", n=len(songs)), L("chk.d.songs", n=len(songs)), label=lab))
    return out


def _ra():
    """root_actions.py for its nmcli helpers: reading NetworkManager needs no root."""
    spec = importlib.util.spec_from_file_location("armonico_root_actions_check", HERE / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wifi_state():
    """{nm, dev, radio, connected, ssid, signal, wired}, read without root and without changing anything
    (root_actions.radio_on would turn the radio on: it is not called here)."""
    ra = _ra()
    if not ra.nm_running():
        return {"nm": False}
    devs = ra.nm_devices()
    dev = next((d for d, t, s in devs if t == "wifi"), "")
    wired = any(t == "ethernet" and s.startswith("connected") for d, t, s in devs)
    st = {"nm": True, "dev": dev, "wired": wired, "radio": True, "connected": False, "ssid": "", "signal": None}
    if not dev:
        return st
    st["radio"] = ra.nmcli("radio", "wifi", timeout=5).stdout.strip() == "enabled"
    st["connected"] = any(d == dev and s.startswith("connected") for d, t, s in devs)
    if st["connected"]:
        r = ra.nmcli("-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list", "--rescan", "no", "ifname", dev, timeout=8)
        for line in r.stdout.splitlines():
            f = ra.terse_split(line)
            if len(f) == 3 and f[0].strip() == "*":
                try:
                    st["signal"] = max(0, min(100, int(f[1])))
                except ValueError:
                    pass
                st["ssid"] = f[2]
                break
    return st


def a_network(cfg, ctx):
    c = diag.check_network(cfg)
    out = [from_diag(c, area="network", row=L("chk.r.noNet") if c.level != diag.OK else L("chk.ok"),
                     todo_key="chk.do.noNet")]
    lab = L("chk.l.wifi")
    try:
        st = wifi_state()
    except Exception:
        st = None
    if st is None:
        out.append(item(SKIP, "network", L("chk.notChecked"), L("chk.d.noNmcli"), label=lab))
    elif not st["nm"]:
        out.append(item(PROBLEM, "network", L("chk.r.noNm"), L("chk.d.noNm"), L("chk.do.noNm"), label=lab))
    elif not st["dev"]:
        lvl = OK if st["wired"] else NOTE
        out.append(item(lvl, "network", L("chk.r.noAdapter"), L("chk.d.noAdapter"),
                        L("chk.do.noAdapter") if lvl else NONE, label=lab))
    elif not st["radio"]:
        lvl = OK if st["wired"] else NOTE
        out.append(item(lvl, "network", L("chk.r.radioOff"), L("chk.d.radioOff"),
                        L("chk.do.radioOff") if lvl else NONE, label=lab))
    elif not st["connected"]:
        lvl = OK if st["wired"] else NOTE
        out.append(item(lvl, "network", L("chk.r.cable") if st["wired"] else L("chk.r.wifiOff"),
                        L("chk.d.cable") if st["wired"] else L("chk.d.wifiOff"),
                        NONE if st["wired"] else L("chk.do.wifiOff"), label=lab))
    else:
        sig = st["signal"]
        if sig is None:
            out.append(item(OK, "network", L("chk.r.wifiOn"), L("chk.d.wifiOn", s=st["ssid"]), label=lab))
        elif sig < 40:
            out.append(item(NOTE, "network", L("chk.r.weak", n=sig), L("chk.d.weak", n=sig, s=st["ssid"]),
                            L("chk.do.weak"), label=lab))
        else:
            out.append(item(OK, "network", L("chk.r.signal", n=sig), L("chk.d.signal", n=sig, s=st["ssid"]), label=lab))
    out += chip_items()
    return out


BOOT_CONFIG = os.environ.get("ARMONICO_BOOT_CONFIG", "/boot/firmware/config.txt")


def chips_off(text):
    """The radio chips config.txt turns off ("wifi", "bluetooth"), in Armonico's block or by a line written by hand."""
    off = set()
    for ln in text.splitlines():
        s = ln.split("#", 1)[0].strip().replace(" ", "")
        for kind, ov in (("wifi", "disable-wifi"), ("bluetooth", "disable-bt")):
            if s == f"dtoverlay={ov}" or s.startswith(f"dtoverlay={ov},"):
                off.add(kind)
    return off


def chip_items(text=None, hci=None):
    """With the Wi-Fi chip off and the Bluetooth chip meant to be on: is the Bluetooth adapter there? disable-wifi
    makes the firmware pull the chip's WL_ON line low, and on boards where Bluetooth's BT_ON follows that line,
    Bluetooth goes with it (raspberrypi/firmware issue 1611). Nothing here when the Wi-Fi chip is on, or when
    Bluetooth is off by choice."""
    if text is None:
        try:
            with open(BOOT_CONFIG, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return []
    off = chips_off(text)
    if "wifi" not in off or "bluetooth" in off:
        return []
    if hci is None:
        try:
            hci = any(n.startswith("hci") for n in os.listdir("/sys/class/bluetooth"))
        except OSError:
            hci = False
    lab = L("chk.l.chips")
    if hci:
        return [item(OK, "network", L("chk.r.btWithWifi"), L("chk.d.btWithWifi"), label=lab)]
    return [item(PROBLEM, "network", L("chk.r.btGoneWifi"), L("chk.d.btGoneWifi"), L("chk.do.btGoneWifi"), label=lab)]


SERVICES_ONLINE = (("github", "chk.l.github", "https://api.github.com/repos/{repo}"),
                   ("weather", "chk.l.weather",
                    "https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}&current=temperature_2m"),
                   ("nasa", "chk.l.nasa", "https://science.nasa.gov/feed/apod-basic/"))


def reach(url, timeout=NET_TIMEOUT):
    """(True, ms) when the service answers at all (an HTTP error is an answer), else (False, why)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"{diag.APP}-check/{diag.VERSION}"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
    except urllib.error.HTTPError:
        pass
    except Exception as e:
        return False, type(e).__name__
    return True, round((time.monotonic() - t0) * 1000)


def a_internet(cfg, ctx):
    if not online(cfg, ctx):
        return [skip("internet", "chk.d.noNetSkip")]
    city = device_settings(cfg, ctx).get("city") or {}
    try:
        lat, lon = float(city["lat"]), float(city["lon"])
    except (KeyError, TypeError, ValueError):
        lat, lon = 32.0809, 34.7806                  # the screen's default city (app.DEFAULT_CITY)
    found = [None] * len(SERVICES_ONLINE)

    def one(i, url):
        found[i] = reach(url)
    threads = []
    for i, (_, _, url) in enumerate(SERVICES_ONLINE):
        th = threading.Thread(target=one, args=(i, url.format(repo=diag.GITHUB_REPO, lat=lat, lon=lon)), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(NET_TIMEOUT + 1)
    out = []
    n_ok = sum(1 for f in found if f and f[0])
    for (key, lab, _), f in zip(SERVICES_ONLINE, found):
        if f and f[0]:
            out.append(item(OK, "internet", L("chk.r.reachedOf", n=n_ok, m=len(found)), L("chk.d.reached", ms=f[1]),
                            label=L(lab)))
        else:
            out.append(item(NOTE, "internet", L("chk.r.unreached." + key), L("chk.d.unreached", s=NET_TIMEOUT),
                            L("chk.do.unreached." + key), label=L(lab)))
    return out


def a_time(cfg, ctx):
    out = [from_diag(diag.check_time(cfg), area="time", row=None, todo_key="chk.do.ntp")]
    if out[0].level == OK:
        out[0] = out[0]._replace(row=L("chk.r.synced"))
    else:
        out[0] = out[0]._replace(row=L("chk.r.notSynced"))
    city = device_settings(cfg, ctx).get("city") or {}
    want = city.get("tz") if isinstance(city, dict) and isinstance(city.get("tz"), str) else "Asia/Jerusalem"
    r = run("timedatectl", "show", "-p", "Timezone", "--value", timeout=4)
    lab = L("chk.l.zone")
    if not r or r[0] != 0 or not r[1].strip():
        out.append(item(SKIP, "time", L("chk.notChecked"), L("chk.d.noTimedatectl"), label=lab))
    elif r[1].strip() == want:
        out.append(item(OK, "time", L("chk.ok"), L("chk.d.zoneOk", z=want), label=lab))
    else:
        out.append(item(NOTE, "time", L("chk.r.zone"), L("chk.d.zone", z=r[1].strip(), c=want), L("chk.do.zone"), label=lab))
    return out


def a_power(cfg, ctx):
    thr = diag.run_out("vcgencmd", "get_throttled").strip()
    if not thr.startswith("throttled="):
        return [skip("power", "chk.d.noVcgencmd")]
    try:
        bits = int(thr.split("=", 1)[1], 16)
    except ValueError:
        return [skip("power", "chk.d.noVcgencmd")]
    code = f"get_throttled={thr.split('=', 1)[1]}"
    out = []
    if bits & 0x1:
        out.append(item(PROBLEM, "power", L("chk.r.underNow"), L("chk.d.underNow", c=code), L("chk.do.under")))
    elif bits & 0x10000:
        out.append(item(PROBLEM, "power", L("chk.r.underBoot"), L("chk.d.underBoot", c=code), L("chk.do.under")))
    else:
        out.append(item(OK, "power", L("chk.ok"), L("chk.d.noUnder", c=code)))
    lab = L("chk.l.slow")
    if bits & 0x6:
        out.append(item(PROBLEM, "power", L("chk.r.slowNow"), L("chk.d.slowNow", c=code), L("chk.do.slow"), label=lab))
    elif bits & 0xE0000 or bits & 0x8:
        out.append(item(NOTE, "power", L("chk.r.slowBoot"), L("chk.d.slowBoot", c=code), L("chk.do.slow"), label=lab))
    else:
        out.append(item(OK, "power", L("chk.ok"), L("chk.d.noSlow"), label=lab))
    return out


def a_temperature(cfg, ctx):
    c = diag.check_temperature(cfg)
    if c is None:
        return [skip("temperature", "chk.d.noTemp")]
    t = first(c.detail_en).replace("°C", "°")
    return [from_diag(c, area="temperature", row=same(t), todo_key="chk.do.hot")]


def meminfo(path=None):
    out = {}
    try:
        for line in Path(path or PROC / "meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            m = re.match(r"\s*(\d+)", v)
            if m:
                out[k.strip()] = int(m.group(1)) * 1024
    except OSError:
        pass
    return out


def armonico_processes(proc=None):
    """[(name, rss bytes)] of the processes that run Armonico's programs, the largest first."""
    proc = Path(proc or PROC)
    app = str(diag.APP_DIR)
    found = []
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return found
    for p in pids:
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")
            status = (p / "status").read_text()
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in cmd if a]
        script = next((a for a in args if a.startswith(app + "/") or f"/{diag.APP}/" in a), "")
        if not script:
            continue
        m = re.search(r"^VmRSS:\s*(\d+)\s*kB", status, re.M)
        if m:
            found.append((Path(script).name, int(m.group(1)) * 1024))
    found.sort(key=lambda x: -x[1])
    return found


def gb(n):
    return f"{n / 1e9:.1f}GB" if n >= 1e9 else f"{round(n / 1e6)}MB"


def a_memory(cfg, ctx):
    mi = meminfo()
    total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable")
    if not total or avail is None:
        return [skip("memory", "chk.d.noMeminfo")]
    out = []
    share = avail / total
    sw_t, sw_f = mi.get("SwapTotal", 0), mi.get("SwapFree", 0)
    swap = L("chk.d.swap", u=gb(sw_t - sw_f), t=gb(sw_t)) if sw_t else L("chk.d.noSwap")
    det = tuple(a + " · " + b for a, b in zip(L("chk.d.mem", a=gb(avail), t=gb(total)), swap))
    if avail < 100e6 or share < 0.05:
        out.append(item(PROBLEM, "memory", L("chk.r.memLow", a=gb(avail)), det, L("chk.do.restart")))
    elif share < 0.15:
        out.append(item(NOTE, "memory", L("chk.r.memLow", a=gb(avail)), det, L("chk.do.restart")))
    elif sw_t and (sw_t - sw_f) / sw_t > 0.5:
        out.append(item(NOTE, "memory", L("chk.r.swap"), det, L("chk.do.restart")))
    else:
        out.append(item(OK, "memory", L("chk.r.memFree", a=gb(avail)), det))
    procs = armonico_processes()
    lab = L("chk.l.proc")
    if not procs:
        out.append(item(SKIP, "memory", L("chk.notChecked"), L("chk.d.noProcs"), label=lab))
    else:
        name, rss = procs[0]
        if rss > 0.35 * total:
            out.append(item(NOTE, "memory", L("chk.r.procBig", p=name), L("chk.d.procBig", p=name, m=gb(rss)),
                            L("chk.do.restart"), label=lab))
        else:
            out.append(item(OK, "memory", L("chk.ok"), L("chk.d.proc", p=name, m=gb(rss)), label=lab))
    return out


IO_ERROR = re.compile(r"I/O error|EXT4-fs error|Buffer I/O|blk_update_request|mmc\d+:.*(?:error|timeout)", re.I)
NO_ACCESS = re.compile(r"insufficient permissions|not seeing messages|Operation not permitted|Permission denied|"
                       r"No journal files were", re.I)


def mounts(path=None):
    """[(mount point, options)] from /proc/mounts."""
    out = []
    try:
        for line in Path(path or PROC / "mounts").read_text().splitlines():
            f = line.split()
            if len(f) >= 4:
                out.append((f[1].replace("\\040", " "), f[3].split(",")))
    except OSError:
        pass
    return out


def kernel_errors():
    """(lines) of read and write errors in the kernel log since boot, or None when it cannot be read."""
    r = run("dmesg", "--level=err,crit,alert,emerg", timeout=5)
    if r and r[0] == 0 and not NO_ACCESS.search(r[2]):
        text = r[1]
    else:
        r = run("journalctl", "-k", "-b", "-p", "err", "-o", "cat", "-q", "--no-pager", timeout=8)
        if not r or r[0] != 0 or NO_ACCESS.search(r[2] + r[1][:300]):
            return None
        text = r[1]
    return [x.strip() for x in text.splitlines() if IO_ERROR.search(x)]


def a_sd(cfg, ctx):
    out = []
    c = diag.check_disk(cfg)
    if c is None:
        out.append(skip("sd", "chk.d.noDisk"))
    else:
        free = re.match(r"([\d.]+)GB", c.detail_en)
        row = L("chk.r.free", f=f"{float(free.group(1)):.0f}GB") if free else L("chk.ok")
        out.append(from_diag(c, area="sd", row=row if c.level == diag.OK else L("chk.r.diskLow"), todo_key="chk.do.disk"))
    data = str(data_dir(cfg))
    ms = mounts()
    lab = L("chk.l.ro")
    if not ms:
        out.append(item(SKIP, "sd", L("chk.notChecked"), L("chk.d.noMounts"), label=lab))
    else:
        mine = max((m for m in ms if data == m[0] or data.startswith(m[0].rstrip("/") + "/")), key=lambda m: len(m[0]),
                   default=None)
        root = next((m for m in ms if m[0] == "/"), None)
        ro = [m[0] for m in (root, mine) if m and "ro" in m[1]]
        if ro:
            out.append(item(PROBLEM, "sd", L("chk.r.ro"), L("chk.d.ro", m=", ".join(dict.fromkeys(ro))), L("chk.do.ro"), label=lab))
        else:
            out.append(item(OK, "sd", L("chk.ok"), L("chk.d.rw"), label=lab))
    lab = L("chk.l.io")
    errs = kernel_errors()
    if errs is None:
        out.append(item(SKIP, "sd", L("chk.notChecked"), L("chk.d.noKernelLog"), L("chk.do.sudo"), label=lab))
    elif errs:
        out.append(item(PROBLEM, "sd", L("chk.r.io", n=len(errs)), tuple(a + ": " + errs[-1][:160] for a in L("chk.d.io", n=len(errs))),
                        L("chk.do.io"), label=lab))
    else:
        out.append(item(OK, "sd", L("chk.ok"), L("chk.d.noIo"), label=lab))
    return out


def bt_sink_names(dump=None):
    """The Bluetooth outputs PipeWire has now (bluez_output.<address>.<n>), from pw-dump's JSON text; None when
    PipeWire could not be asked."""
    if dump is None:
        cmd = diag.as_sound_user("pw-dump")
        r = run(*cmd, timeout=6) if cmd else None
        if not r or r[0] != 0:
            return None
        dump = r[1]
    try:
        objs = json.loads(dump or "[]")
    except ValueError:
        return None
    out = []
    for o in objs:
        props = ((o.get("info") or {}).get("props") or {})
        name = props.get("node.name", "")
        if props.get("media.class") == "Audio/Sink" and name.startswith("bluez_output."):
            out.append(name)
    return out


def bt_dropouts(names, top=None):
    """The most dropouts (pw-top's ERR, the count of buffers that came too late since the output started) of
    any of these outputs; None when pw-top gave nothing. top: pw-top -b's text, for tests."""
    if top is None:
        cmd = diag.as_sound_user("pw-top", "-b", "-n", "2")
        r = run(*cmd, timeout=8) if cmd else None
        if not r or r[0] != 0:
            return None
        top = r[1]
    worst = None
    for line in top.splitlines():
        t = line.split()
        if len(t) < 10 or not t[1].isdigit() or not t[8].isdigit() or t[-1] not in names:
            continue
        worst = max(worst or 0, int(t[8]))
    return worst


def bt_log_problems(text):
    """How many lines of a log say the Bluetooth link or its sound fell short."""
    return sum(1 for x in (text or "").splitlines()
               if re.search(r"(a2dp|bluez|bluetooth|bluetoothd).*(underrun|xrun|timeout|timed out|disconnect|fail|error)", x, re.I))


def bt_log(cfg):
    """Warnings in the last 30 minutes, or None when the log could not be read (no journalctl, or a user that is
    not allowed to read it: journalctl then answers 0 with only a hint on stderr, as a_journal checks)."""
    n = 0
    for args in (("-u", "bluetooth.service"), ("_COMM=pipewire",), ("_COMM=wireplumber",)):
        r = run("journalctl", *args, "--since", "-30min", "-o", "cat", "-q", "--no-pager", timeout=6)
        if not r or r[0] != 0 or NO_ACCESS.search(r[2]):
            return None
        n += bt_log_problems(r[1])
    return n


_ASK = object()


def bt_items(cfg, ctx, names=None, errs=None, log=_ASK, wifi=None):
    """The Bluetooth output's own row of the sound check: only when one is connected. Dropouts counted by PipeWire
    since the output started and warnings from bluetoothd or PipeWire in the last 30 minutes; with the likely cause
    (the Pi's one radio shares 2.4 GHz with Wi-Fi) and what to do. Nothing here changes anything."""
    names = bt_sink_names() if names is None else names
    if not names:
        return []
    lab = L("chk.l.bt")
    errs = bt_dropouts(names) if errs is None else errs
    log = bt_log(cfg) if log is _ASK else log
    if errs is None and not log:
        return [item(SKIP, "sound", L("chk.notChecked"), L("chk.d.btNoTop"), label=lab)]
    if log is None and not errs:                 # no dropouts counted, but the log was not read: not "no warnings"
        return [item(SKIP, "sound", L("chk.notChecked"), L("chk.d.journalNoAccess"), L("chk.do.sudo"), label=lab)]
    log = log or 0
    if not errs and not log:
        return [item(OK, "sound", L("chk.ok"), L("chk.d.btOk"), label=lab)]
    try:
        wifi = wifi_state() if wifi is None else wifi
    except Exception:
        wifi = {}
    on_wifi = bool(wifi.get("nm") and wifi.get("connected"))
    todo = L("chk.do.btWifi") if on_wifi else L("chk.do.bt")
    n = (errs or 0) + log
    return [item(NOTE, "sound", L("chk.r.bt", n=n), L("chk.d.bt", e=errs or 0, l=log),
                 todo, label=lab)]


def a_sound(cfg, ctx):
    c = diag.check_sound(cfg)
    out = [from_diag(c, area="sound", todo_key="chk.do.sound" if "PipeWire" in c.detail_en else "chk.do.aux")]
    if c.level == diag.PROBLEM:
        out[0] = out[0]._replace(row=L("chk.r.noPw") if "PipeWire" in c.detail_en else L("chk.r.noAux"))
    lab = L("chk.l.volume")
    vol = (ctx or {}).get("volume")
    if isinstance(vol, (int, float)) and vol <= 0:
        out.append(item(NOTE, "sound", L("chk.r.vol0"), L("chk.d.vol0"), L("chk.do.vol0"), label=lab))
        return out
    cmd = diag.as_sound_user("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    r = run(*cmd, timeout=4) if cmd else None
    m = re.search(r"Volume:\s*([\d.]+)", r[1]) if r and r[0] == 0 else None
    if not m:
        out.append(item(SKIP, "sound", L("chk.notChecked"), L("chk.d.noWpctl"), label=lab))
    elif "MUTED" in r[1] or float(m.group(1)) <= 0:
        out.append(item(NOTE, "sound", L("chk.r.muted"), L("chk.d.muted"), L("chk.do.muted"), label=lab))
    else:
        pct = round(float(m.group(1)) * 100)
        own = f" · Armonico {int(vol)}%" if isinstance(vol, (int, float)) else ""
        out.append(item(OK, "sound", L("chk.ok"), tuple(x + own for x in L("chk.d.vol", n=pct)), label=lab))
    out += bt_items(cfg, ctx)
    return out


def a_samples(cfg, ctx):
    c = diag.check_samples(cfg)
    folder = data_dir(cfg) / "device" / "gm"
    have = [f for f in FAMILIES if (folder / f).is_dir() and any((folder / f).glob("*.wav"))]
    missing = [f for f in FAMILIES if f not in have]
    n = (1 if c.level == diag.OK else 0) + len(have)
    m = 1 + len(FAMILIES)
    row = L("chk.r.of", n=n, m=m)
    if c.level != diag.OK:
        return [from_diag(c, area="samples", row=row, todo_key="chk.do.samples"),
                item(NOTE if not missing else PROBLEM, "samples", row, L("chk.d.gm", n=len(have), m=len(FAMILIES)),
                     L("chk.do.samples") if missing else NONE, label=L("chk.l.gm"))]
    out = [from_diag(c, area="samples", row=row)]
    if missing:
        names = ", ".join(re.sub(r"^\d+-", "", f) for f in missing)
        out.append(item(PROBLEM, "samples", row, L("chk.d.gmMissing", x=names), L("chk.do.samples"), label=L("chk.l.gm")))
    else:
        out.append(item(OK, "samples", row, L("chk.d.gm", n=len(have), m=len(FAMILIES)), label=L("chk.l.gm")))
    return out


def usb_keyboards(path=None):
    """The names of the USB sound cards (a USB MIDI keyboard is one), from /proc/asound/cards."""
    try:
        text = Path(path or PROC / "asound" / "cards").read_text()
    except OSError:
        return []
    return [m.group(1).strip() for m in re.finditer(r"USB-Audio - (.+)", text)]


def a_midi(cfg, ctx):
    out = [from_diag(c, area="midi", row=None if c.level == diag.OK else L("chk.r.noKeysSeq" if c.key == "keys" else "chk.r.noVirmidi"),
                     todo_key="chk.do.services" if c.key == "keys" else "chk.do.virmidi")
           for c in diag.check_midi(cfg)]
    lab = L("chk.l.usb")
    kb = (ctx or {}).get("keyboards")
    names = list(kb) if isinstance(kb, (list, tuple)) else usb_keyboards()
    if not names:
        out.append(item(SKIP, "midi", L("chk.notChecked"), L("chk.d.noUsb"), label=lab))
    else:
        seq = diag.run_out("aconnect", "-i")
        seen = [n for n in names if n.split(" ")[0] in seq]
        if seen or isinstance(kb, (list, tuple)):
            out.append(item(OK, "midi", L("chk.ok"), L("chk.d.usb", n=", ".join(names)), label=lab))
        else:
            out.append(item(NOTE, "midi", L("chk.r.usbNotSeq"), L("chk.d.usbNotSeq", n=", ".join(names)),
                            L("chk.do.usbNotSeq"), label=lab))
    return out


def pin_states():
    """{pin: (function, level)} for every GPIO pin: function "input", "output" or the alternate
    function's name; level "hi" or "lo". From pinctrl (Bookworm) or raspi-gpio. None when neither reads."""
    r = run("pinctrl", "get", timeout=4)
    out = {}
    if r and r[0] == 0:
        for line in r[1].splitlines():
            m = re.match(r"\s*(\d+):\s*(\w+)\s*(?:\w+)?\s*\|\s*(hi|lo)(?:\s*//\s*\S+\s*=\s*(\S+))?", line)
            if m:
                fn = {"ip": "input", "op": "output"}.get(m.group(2), m.group(4) or m.group(2))
                out[int(m.group(1))] = (fn, m.group(3))
        if out:
            return out
    r = run("raspi-gpio", "get", timeout=4)
    if r and r[0] == 0:
        for line in r[1].splitlines():
            m = re.match(r"GPIO (\d+): level=(\d).*?func=(\S+)", line)
            if m:
                fn = {"INPUT": "input", "OUTPUT": "output"}.get(m.group(3), m.group(3))
                out[int(m.group(1))] = (fn, "hi" if m.group(2) == "1" else "lo")
    return out or None


def wiring(cfg):
    keys = [int(p) for p in (cfg.get("DEVICE_KEY_PINS") or "4,10,17,9,18,27,25,22,11,23,8,24,13").split(",") if p.strip().isdigit()]
    btns = dict(x.split(":") for x in (cfg.get("DEVICE_BUTTON_PINS") or "menu:7,down:5,up:6,ok:12").split(",") if ":" in x)
    return keys, {k: int(v) for k, v in btns.items() if v.isdigit()}


def a_gpio(cfg, ctx):
    c = diag.check_gpio(cfg)
    out = [from_diag(c, area="gpio", row=L("chk.r.gpioNote") if c.level == diag.NOTE else L("chk.r.noChip") if c.level else None,
                     todo_key="chk.do.gpioTest" if c.level == diag.NOTE else "chk.do.gpioChip",
                     link="test" if c.level == diag.NOTE else "")]
    if c.level == diag.PROBLEM:
        return out
    keys, btns = wiring(cfg)
    a = pin_states()
    lab = L("chk.l.pins")
    if a is None:
        out.append(item(SKIP, "gpio", L("chk.notChecked"), L("chk.d.noPinctrl"), label=lab))
        return out
    time.sleep(0.6)
    b = pin_states() or a
    pins = keys + list(btns.values()) + [3]
    taken = [p for p in pins if p in a and a[p][0] != "input"]
    if taken:
        out.append(item(PROBLEM, "gpio", L("chk.r.taken", p=", ".join(map(str, taken))),
                        L("chk.d.taken", p=", ".join(f"GPIO{p} ({a[p][0]})" for p in taken)), L("chk.do.taken"), label=lab))
    else:
        out.append(item(OK, "gpio", L("chk.ok"), L("chk.d.free", n=len(pins)), label=lab))
    held = [i for i, p in enumerate(keys) if a.get(p, ("", "hi"))[1] == "lo" and b.get(p, ("", "hi"))[1] == "lo"]
    held_b = [n for n, p in btns.items() if a.get(p, ("", "hi"))[1] == "lo" and b.get(p, ("", "hi"))[1] == "lo"]
    if held:
        i = held[0]
        out.append(item(NOTE, "gpio", L("chk.r.held", k=key_name(i)), L("chk.d.held", k=key_names(held)),
                        L("chk.do.held"), link="test"))
    if held_b:
        out.append(item(NOTE, "gpio", L("chk.r.heldB"), L("chk.d.heldB", b=", ".join(held_b)), L("chk.do.held"), link="test"))
    if not held and not held_b:
        out.append(item(OK, "gpio", L("chk.ok"), L("chk.d.noHeld")))
    return out


def a_display(cfg, ctx):
    c = diag.check_display(cfg)
    out = [from_diag(c, area="display", row=same(c.detail_en.split(",")[0]) if c.level == diag.OK else L("chk.r.noDisplay"),
                     todo_key="chk.do.display")]
    for f, key in ((diag.check_config_txt, "chk.do.configTxt"), (diag.check_power_button, "chk.do.powerOverlay")):
        r = f(cfg)
        if r is not None:
            out.append(from_diag(r, area="display", row=L("chk.ok") if r.level == diag.OK else L("chk.r.configTxt"),
                                 todo_key=key))
    out += [i for i in a_fonts(cfg, ctx) if i.level != OK]
    return out


def a_fonts(cfg, ctx):
    """A row of the Display area (no new area, the screen's two columns stay as they are). Heebo (made by 'prepare' in the device's fonts folder) and DejaVu (fonts-dejavu-core) draw every letter and symbol."""
    folder = data_dir(cfg) / "device" / "fonts"
    heebo = [n for n in ("Heebo-Hebrew-Medium.ttf", "Heebo-Hebrew-Bold.ttf", "Heebo-Medium.ttf", "Heebo-Bold.ttf")
             if not (folder / n).exists()]
    dejavu = not any(Path("/usr/share/fonts/truetype/dejavu", n).exists() for n in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"))
    out = []
    if heebo:
        out.append(item(NOTE, "display", L("chk.r.fonts"), L("chk.d.fontsHeebo", x=str(folder)), L("chk.do.fonts"), label=L("chk.a.fonts")))
    if dejavu:
        out.append(item(NOTE, "display", L("chk.r.fonts"), L("chk.d.fontsDejavu"), L("chk.do.fonts"), label=L("chk.a.fonts")))
    return out or [item(OK, "display", L("chk.ok"), L("chk.ok"), label=L("chk.a.fonts"))]


def json_files(data):
    """The settings, the progress, the profiles and what the learner keeps: every file of them there is."""
    names = ["device/settings.json", "settings.json", "profiles.json", "lessons.json", "history.json", "learner.json"]
    found = [data / n for n in names]
    try:
        for d in sorted((data / "profiles").iterdir()):
            if d.is_dir():
                found += [d / n for n in ("settings.json", "lessons.json", "history.json", "learner.json")]
    except OSError:
        pass
    return [p for p in found if p.exists()]


def a_files(cfg, ctx):
    data = data_dir(cfg)
    files = json_files(data)
    bad = []
    for p in files:
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except PermissionError:
            continue
        except (OSError, ValueError, UnicodeDecodeError):
            bad.append(str(p.relative_to(data)))
    if not files:
        return [skip("files", "chk.d.noFiles")]
    if bad:
        return [item(PROBLEM, "files", L("chk.r.badFile", f=b), L("chk.d.badFile", f=b), L("chk.do.badFile"))
                for b in bad]
    return [item(OK, "files", L("chk.r.of", n=len(files), m=len(files)), L("chk.d.files", n=len(files)))]


def units(cfg):
    u = list(diag._units(cfg))
    if cfg.get("DEVICE", "yes") == "yes" and f"{diag.APP}-recorder" not in u:
        r = run("systemctl", "is-enabled", "--quiet", f"{diag.APP}-recorder", timeout=3)
        if r and r[0] == 0:
            u.append(f"{diag.APP}-recorder")
    return u


def a_journal(cfg, ctx):
    found, no_access = [], False
    for u in units(cfg):
        r = run("journalctl", "-u", u, "-p", "err", "--since", "-24h", "-o", "cat", "-q", "--no-pager", timeout=8)
        if r is None:
            return [skip("journal", "chk.d.noJournal")]
        if NO_ACCESS.search(r[2]) or r[0] != 0:
            no_access = True
            break
        lines = [x for x in r[1].splitlines() if x.strip()]
        found.append((u, lines))
    if no_access:
        return [item(SKIP, "journal", L("chk.notChecked"), L("chk.d.journalNoAccess"), L("chk.do.sudo"))]
    out = []
    for u, lines in found:
        if not lines:
            continue
        short = u[len(diag.APP) + 1:] if u.startswith(diag.APP + "-") else u
        name = L(UNIT_KEY[short]) if short in UNIT_KEY else same(short)
        last = lines[-1].strip()[:160]
        row = L("chk.r.error1") if len(lines) == 1 else L("chk.r.errors", n=len(lines))
        out.append(item(NOTE, "journal", row, tuple(f"{d} · {last}" for d in L("chk.d.errors", n=len(lines))),
                        L("chk.do.errors", u=u), label=tuple(f"{a} · {b}" for a, b in zip(L("chk.a.journal"), name))))
    if not out:
        out.append(item(OK, "journal", L("chk.r.noErrors"), L("chk.d.noErrors", n=len(found))))
    return out


def a_backup(cfg, ctx):
    folder = data_dir(cfg) / "backups"
    out = []
    try:
        files = [p for p in folder.glob("*.armonico") if p.is_file()]
    except OSError:
        files = []
    if not files:
        out.append(item(NOTE, "backup", L("chk.r.noBackup"), L("chk.d.noBackup"), L("chk.do.backup")))
    else:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        days = int((time.time() - newest.stat().st_mtime) // 86400)
        when = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d")
        row = L("chk.r.today") if days == 0 else L("chk.r.day1") if days == 1 else L("chk.r.days", n=days)
        if days > 30:
            out.append(item(NOTE, "backup", row, L("chk.d.oldBackup", d=when, n=days), L("chk.do.backup")))
        else:
            out.append(item(OK, "backup", row, L("chk.d.backup", d=when, f=newest.name)))
    lab = L("chk.l.backupDir")
    if not folder.is_dir():
        out.append(item(PROBLEM, "backup", L("chk.r.noDir"), L("chk.d.noDir", p=str(folder)), L("chk.do.reinstall"), label=lab))
    elif not os.access(folder, os.W_OK | os.X_OK):
        out.append(item(PROBLEM, "backup", L("chk.r.notWritable"), L("chk.d.notWritable", p=str(folder)), L("chk.do.reinstall"),
                        label=lab))
    else:
        out.append(item(OK, "backup", L("chk.ok"), L("chk.d.writable", p=str(folder)), label=lab))
    return out


def a_alarm(cfg, ctx):
    data = data_dir(cfg)
    st = device_settings(cfg, ctx)
    a = st.get("alarm") if isinstance(st.get("alarm"), dict) else {}
    out = []
    songs = Path(cfg.get("SONGS_DIR") or data / "songs")
    song = str(a.get("song") or "")
    if not a.get("on"):
        out.append(item(SKIP, "alarm", L("chk.r.alarmOff"), L("chk.d.alarmOff")))
    elif not song:
        out.append(item(PROBLEM, "alarm", L("chk.r.noAlarmSong"), L("chk.d.noAlarmSong"), L("chk.do.alarmSong")))
    elif any((songs / (song + ext)).is_file() for ext in (".mid", ".midi", ".kar", ".MID")):
        out.append(item(OK, "alarm", L("chk.ok"), L("chk.d.alarmSong", s=song.replace("_", " "), t=a.get("time", ""))))
    else:
        out.append(item(PROBLEM, "alarm", L("chk.r.alarmGone"), L("chk.d.alarmGone", s=song.replace("_", " ")),
                        L("chk.do.alarmSong")))
    lab = L("chk.l.stopPath")
    stop = data / "stop-request"
    if not os.access(data, os.W_OK | os.X_OK):
        out.append(item(PROBLEM, "alarm", L("chk.r.stopNo"), L("chk.d.stopNo", p=str(data)), L("chk.do.reinstall"), label=lab))
    elif stop.exists() and time.time() - stop.stat().st_mtime > 5:
        age = int(time.time() - stop.stat().st_mtime)
        out.append(item(NOTE, "alarm", L("chk.r.stopStale"), L("chk.d.stopStale", n=age), L("chk.do.services"),
                        fix="services", label=lab))
    else:
        out.append(item(OK, "alarm", L("chk.ok"), L("chk.d.stopOk"), label=lab))
    return out


def tg_kept(request, timeout=10):
    """True / False from the root action telegram-kept, or None when root did not answer."""
    if request is None:
        return None
    try:
        ok, msg = request("telegram-kept", timeout=timeout)
    except Exception:
        return None
    if not ok:
        return None
    try:
        return bool(json.loads(msg).get("kept"))
    except (ValueError, AttributeError, TypeError):
        return None


def a_telegram(cfg, ctx):
    st = device_settings(cfg, ctx)
    if not st.get("tg_done") and not st.get("tg_kept"):
        return [item(SKIP, "telegram", L("chk.r.tgNone"), L("chk.d.tgNone"))]
    kept = tg_kept((ctx or {}).get("request"))
    if kept is None:
        return [item(SKIP, "telegram", L("chk.notChecked"), L("chk.d.tgNoRoot"))]
    if kept:
        return [item(OK, "telegram", L("chk.r.tgKept"), L("chk.d.tgKept"))]
    return [item(NOTE, "telegram", L("chk.r.tgNot"), L("chk.d.tgNot"), L("chk.do.tg"))]


def a_update(cfg, ctx):
    if not online(cfg, ctx):
        return [skip("update", "chk.d.noNetSkip")]
    try:
        tag, _ = diag.latest_release(timeout=NET_TIMEOUT)
    except Exception:
        return [skip("update", "chk.d.noGithub")]
    mine, theirs = diag._version(diag.VERSION), diag._version(tag)
    if mine and theirs and theirs > mine:
        v = diag.shown(tag)
        return [item(NOTE, "update", L("chk.r.newer", v=v), L("chk.d.newer", v=v, m=diag.shown(diag.VERSION)), L("chk.do.update"))]
    return [item(OK, "update", L("chk.r.latest"), L("chk.d.latest", v=diag.shown(diag.VERSION)))]


AREAS = [("services", a_services), ("broker", a_broker), ("lesson_screen", a_lesson_screen), ("network", a_network),
         ("internet", a_internet), ("time", a_time), ("power", a_power), ("temperature", a_temperature),
         ("memory", a_memory), ("sd", a_sd), ("sound", a_sound), ("samples", a_samples), ("midi", a_midi),
         ("gpio", a_gpio), ("display", a_display), ("files", a_files), ("journal", a_journal),
         ("backup", a_backup), ("alarm", a_alarm), ("telegram", a_telegram), ("update", a_update)]
DEVICE_ONLY = {"sound", "samples", "midi", "gpio", "display", "network", "alarm", "telegram"}


def areas(cfg):
    dev = cfg.get("DEVICE", "yes") == "yes"
    return [(k, f) for k, f in AREAS if dev or k not in DEVICE_ONLY]


def check_area(key, fn, cfg, ctx):
    try:
        res = fn(cfg, ctx)
    except Exception as e:
        res = [item(SKIP, key, L("chk.notChecked"), L("chk.d.failed", e=str(e)[:120] or type(e).__name__))]
    return [r for r in (res or []) if r is not None]


def run_all(cfg=None, ctx=None, on_area=None, timeout=AREA_TIMEOUT, workers=WORKERS, only=None):
    """Every area, WORKERS at a time, each in a thread of its own with its own limit: one that does not
    answer is "not checked" and its thread is left behind (daemon). on_area(i, items) as each ends, from
    the checking threads. Returns [(area key, [Item])] in the areas' order."""
    cfg = cfg if cfg is not None else diag.config()
    todo = [a for a in areas(cfg) if only is None or a[0] in only]
    results = [None] * len(todo)
    lock = threading.Lock()
    nxt = [0]

    def worker():
        while True:
            with lock:
                i = nxt[0]
                nxt[0] += 1
            if i >= len(todo):
                return
            key, fn = todo[i]
            box = {}
            th = threading.Thread(target=lambda: box.setdefault("r", check_area(key, fn, cfg, ctx)), daemon=True,
                                  name=f"check-{key}")
            th.start()
            th.join(timeout)
            res = box.get("r")
            if res is None:
                res = [item(SKIP, key, L("chk.notChecked"), L("chk.d.late", s=timeout))]
            results[i] = (key, res)
            if on_area:
                try:
                    on_area(i, res)
                except Exception:
                    pass
    pool = [threading.Thread(target=worker, daemon=True, name=f"check-pool-{n}") for n in range(workers)]
    for th in pool:
        th.start()
    for th in pool:
        th.join()
    return results


# ----------------------------------------------------------------------
# The summary and the report
# ----------------------------------------------------------------------
def counts(items):
    return {lvl: sum(1 for i in items if i.level == lvl) for lvl in (OK, NOTE, PROBLEM, SKIP)}


def ordered(items):
    """Failures first, then warnings: the order of the summary."""
    return [i for i in items if i.level == PROBLEM] + [i for i in items if i.level == NOTE]


def line(i, k=0):
    return f"{i.label[k]}: {i.row[k]}" if i.row[k] else i.label[k]


def report_text(auto, guided=None, version=None, when=None, guided_done=True):
    """The report in English, for the log and for SSH."""
    when = when or datetime.now()
    items = list(auto) + list(guided or [])
    n = counts(items)
    out = ["Armonico Pi full check",
           f"Version: {diag.shown(version or diag.VERSION)}",
           f"Date: {when.strftime('%Y-%m-%d %H:%M')}",
           f"Result: {n[PROBLEM]} failed, {n[NOTE]} warnings, {n[OK]} passed, {n[SKIP]} not checked", ""]
    if n[PROBLEM] or n[NOTE]:
        out.append("To do")
        for i in ordered(items):
            out.append(f"  [{MARK[i.level]}] {line(i)}")
            if i.todo[0]:
                out.append(f"         {i.todo[0]}")
        out.append("")
    out.append("Automatic checks")
    if not auto:
        out.append("  not run")
    for i in auto:
        out.append(f"  [{MARK[i.level]:>4}] {i.label[0]}: {i.detail[0]}" + (f" ({i.todo[0]})" if i.level == SKIP and i.todo[0] else ""))
    out.append("")
    out.append("Guided steps")
    if guided:
        for i in guided:
            out.append(f"  [{MARK[i.level]:>4}] {i.label[0]}: {i.detail[0]}")
    else:
        out.append("  not run" if not guided_done else "  none")
    return "\n".join(out) + "\n"


def report_dir(cfg=None):
    return data_dir(cfg or {}) / "device" / "checks"


def save_report(text, cfg=None, when=None, keep=KEEP_REPORTS):
    """Written whole (a temporary file, then renamed) as YYYY-MM-DD-HHMM.txt; the newest `keep` stay.
    Through the folder's own handle and never through a link: `sudo armonico check` writes into the
    service user's folder, and the file is then given to that folder's owner. Returns the path."""
    folder = report_dir(cfg)
    when = when or datetime.now()
    folder.mkdir(parents=True, exist_ok=True)
    name = when.strftime("%Y-%m-%d-%H%M") + ".txt"
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            if os.geteuid() == 0:
                st = os.fstat(dfd)
                os.chown(tmp, st.st_uid, st.st_gid, dir_fd=dfd, follow_symlinks=False)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
        old = sorted(n for n in os.listdir(dfd) if re.fullmatch(r"\d{4}-\d\d-\d\d-\d{4}\.txt", n))
        for n in old[:-keep] if keep else []:
            try:
                os.unlink(n, dir_fd=dfd)
            except OSError:
                pass
    finally:
        os.close(dfd)
    return folder / name


def flat(results):
    return [i for _, items in results for i in items]
