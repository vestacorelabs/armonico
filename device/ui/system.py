"""system.py - what the screen asks of the machine: root actions, the broker, Home Assistant, Telegram.

The screen runs without root rights. A root action is a small JSON request file; the root unit
armonico-device-request.path starts device/root_actions.py, which does only the actions it knows
and writes its progress next to the request (device-request.status), so the screen can show each
step as it runs. Everything here blocks: the screen calls it from a job thread, never while drawing.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
REQUEST = DATA_DIR / "device-request"
STATUS = DATA_DIR / "device-request.status"
SETUP_DIR = APP_DIR / "setup"


def main_python():
    """The main install's venv has zeroconf and the setup's helpers; the device venv may not."""
    for p in (APP_DIR / "venv" / "bin" / "python", Path(sys.executable)):
        if p.exists():
            return str(p)
    return sys.executable


# ----------------------------------------------------------------------
# root actions
# ----------------------------------------------------------------------
_ONE_AT_A_TIME = threading.Lock()


def request(action, wait=True, timeout=600, on_step=None, ticket=None, **args):
    """Hands one action to the root unit. on_step(i, state) follows its progress.
    Returns (ok, message). With wait=False it returns as soon as the request is written.
    One at a time: the root unit takes one request file and writes one status file, so a second
    request during a long one (a time zone during the broker's install) was dropped after 15 s as
    "no-root" and wiped the first one's status. Now it waits its turn.
    ticket: a Ticket the caller keeps, to call this request off with cancel(ticket) while it waits its
    turn (it then never runs) or while it runs. Its started field says when it got its turn."""
    t = ticket or Ticket()
    with _ONE_AT_A_TIME:
        with _STATE:
            if t.cancelled:
                return False, "cancelled"
            t.started = time.monotonic()
            _CURRENT_RID["rid"] = t.rid
        try:
            return _request(action, wait, timeout, on_step, args, t.rid)
        finally:
            with _STATE:
                if _CURRENT_RID["rid"] == t.rid:
                    _CURRENT_RID["rid"] = ""
            t.ended = True


class Ticket:
    """One root request the page that made it can call off: system.cancel(ticket). rid is the request's id;
    started is the monotonic time it got its turn (None while it waits behind another request)."""

    def __init__(self):
        self.rid = uuid.uuid4().hex[:12]
        self.started = None
        self.cancelled = False
        self.ended = False

    def waiting(self):
        """True while it waits behind another root action."""
        return self.started is None and not self.cancelled and not self.ended


_STATE = threading.Lock()
_CURRENT_RID = {"rid": ""}
CANCEL = DATA_DIR / "device-cancel"


def cancel(ticket):
    """Calls off this caller's own request, and only it. Still waiting its turn: it is dropped and answers
    "cancelled" without running. Running: its id goes into device-cancel (see cancel_current). Done: nothing."""
    if ticket is None:
        return
    with _STATE:
        ticket.cancelled = True
        running = ticket.started is not None and _CURRENT_RID["rid"] == ticket.rid
    if running:
        _write_cancel(ticket.rid)


def cancel_current():
    """Gives up on the root action that is running: its id goes into device-cancel, which the root unit looks at
    while it waits for bluetoothctl and the like (root_actions.cancel_asked). It ends within a fraction of a second
    and the request then answers "cancelled". Does not wait for the lock, so it works while a request is being waited on.
    A page should use cancel(ticket): this one stops whatever runs, also another page's request."""
    rid = _CURRENT_RID["rid"]
    if rid:
        _write_cancel(rid)


def _write_cancel(rid):
    tmp = CANCEL.with_name(".device-cancel.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(rid)
        os.replace(tmp, CANCEL)
    except OSError:
        pass


def _request(action, wait, timeout, on_step, args, rid):
    body = json.dumps({"id": rid, "action": action, "args": args})
    tmp = REQUEST.with_name(".device-request.tmp")
    try:
        STATUS.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, REQUEST)
    except OSError as e:
        return False, str(e)
    if not wait:
        return True, ""
    end = time.monotonic() + timeout
    taken_by = time.monotonic() + 15
    seen = -1
    while time.monotonic() < end:
        st = read_status(rid)
        if st:
            step = int(st.get("step", 0))
            if on_step and (step != seen or st.get("state") in ("done", "fail")):
                on_step(step, st.get("state", "running"))
                seen = step
            if st.get("state") == "done":
                return True, st.get("msg", "")
            if st.get("state") == "fail":
                return False, st.get("msg", "")
        elif REQUEST.exists() and time.monotonic() > taken_by:
            try:
                REQUEST.unlink()                 # nobody took it: the path unit is not there
            except OSError:
                pass
            return False, "no-root"
        time.sleep(0.3)
    _write_cancel(rid)                               # the root side stops too, so it does not hold up the next request
    return False, "timeout"


def read_status(rid):
    try:
        st = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return None
    return st if st.get("id") == rid else None


# ----------------------------------------------------------------------
# Wi-Fi: the root actions wifi-* (NetworkManager), and a look at the cable from here
# ----------------------------------------------------------------------
def wifi(action, timeout=60, ticket=None, **args):
    """One Wi-Fi root action (status, scan, saved, connect, forget). Returns (True, its answer as a
    dict) or (False, the failure: wrong-password, password-needed, timeout, no-nm, no-root...)."""
    ok, msg = request("wifi-" + action, timeout=timeout, ticket=ticket, **args)
    if not ok:
        return False, msg or "failed"
    try:
        data = json.loads(msg) if msg else {}
    except ValueError:
        data = {}
    return True, data if isinstance(data, dict) else {}


def bt(action, timeout=60, ticket=None, **args):
    """One Bluetooth root action (status, scan, pair, connect, disconnect, forget). Returns (True, its answer
    as a dict) or (False, the failure in a word: unreachable, auth, blocked, no-adapter, no-bluez, busy,
    profile, failed, timeout...)."""
    ok, msg = request("bt-" + action, timeout=timeout, ticket=ticket, **args)
    if not ok:
        return False, msg or "failed"
    try:
        data = json.loads(msg) if msg else {}
    except ValueError:
        data = {}
    return True, data if isinstance(data, dict) else {}


BOOT_CONFIGS = (os.environ.get("ARMONICO_BOOT_CONFIG", "/boot/firmware/config.txt"), "/boot/config.txt")
CHIP_BEGIN = "# >>> Armonico: radio chips"


def chips_off():
    """The radio chips config.txt turns off from the start, read without root: ({kinds off}, {kinds off by a line
    Armonico did not write}). kinds: "wifi" (dtoverlay=disable-wifi), "bluetooth" (dtoverlay=disable-bt)."""
    text = ""
    for path in BOOT_CONFIGS:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            break
        except OSError:
            continue
    off, hand, ours = set(), set(), False
    for ln in text.splitlines():
        raw = ln.strip()
        if raw.startswith(CHIP_BEGIN):
            ours = True
            continue
        if raw.startswith("# <<< Armonico: radio chips"):
            ours = False
            continue
        s = raw.split("#", 1)[0].replace(" ", "")
        for kind, ov in (("wifi", "disable-wifi"), ("bluetooth", "disable-bt")):
            if s == f"dtoverlay={ov}" or s.startswith(f"dtoverlay={ov},"):
                off.add(kind)
                if not ours:
                    hand.add(kind)
    return off, hand


def board_bluetooth(model_path="/proc/device-tree/model"):
    """True when the board has Bluetooth of its own: every Raspberry Pi since the 3, the 400 and 500, and the
    Zero W and Zero 2 W. False for a Pi without it (1, 2, Zero), None when it cannot be told (not a Pi, or a
    Compute Module, which comes with or without wireless)."""
    try:
        model = Path(model_path).read_text(errors="replace").strip("\0\n ")
    except OSError:
        return None
    if not model.startswith("Raspberry Pi") or "Compute Module" in model:
        return None
    if any(x in model for x in (" 3 ", " 4 ", " 5 ", " 400", " 500", "Zero W", "Zero 2 W")) \
            or model.startswith(("Raspberry Pi 3", "Raspberry Pi 4", "Raspberry Pi 5")):
        return True
    return False


# ----------------------------------------------------------------------
# the Wi-Fi and Bluetooth radios: the state read from /sys (no root), the switch a root action
# ----------------------------------------------------------------------
def radio_state(kind, base="/sys/class/rfkill"):
    """"on", "off", "hard" (a switch on the hardware holds it) or None when this machine has no such radio.
    kind: "wifi" or "bluetooth"."""
    want = "wlan" if kind == "wifi" else "bluetooth"
    seen, state = False, "on"
    try:
        for d in Path(base).glob("rfkill*"):
            try:
                if (d / "type").read_text().strip() != want:
                    continue
                seen = True
                if (d / "hard").read_text().strip() == "1":
                    return "hard"
                if (d / "soft").read_text().strip() == "1":
                    state = "off"
            except OSError:
                continue
    except OSError:
        return None
    return state if seen else None


def radio_set(kind, on):
    """The radio switched on or off by the root unit. (True, "") or (False, the failure)."""
    return request("radio", timeout=40, kind=kind, on=bool(on))


def wifi_status():
    """The Wi-Fi connection now, read by this program itself: reading NetworkManager's state needs no
    root, so it never waits in the root unit's queue behind a scan or an install. The same code as the
    root action wifi-status (root_actions.wifi_now). (True, {nm, dev, ssid, ip, wired}) or (False, why)."""
    global _RA
    try:
        if _RA is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("armonico_root_actions_read", APP_DIR / "device" / "root_actions.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _RA = mod
        return True, _RA.wifi_now()
    except Exception as e:                       # nmcli missing, slow or odd: the cell says so, nothing more
        return False, str(e) or "failed"


_RA = None


def wired_up(net_dir="/sys/class/net"):
    """Whether a network cable is plugged in with an IPv4 address: a real (not virtual) interface that
    is not Wi-Fi, with carrier, and an address. Read from /sys and one ioctl each, quick enough for
    the setup to decide on its steps."""
    import fcntl
    import socket
    import struct
    root = Path(net_dir)
    try:
        names = sorted(p.name for p in root.iterdir())
    except OSError:
        return False
    for name in names:
        d = root / name
        if name == "lo" or not (d / "device").exists() or (d / "wireless").exists() or (d / "phy80211").exists():
            continue
        try:
            if (d / "carrier").read_text().strip() != "1":
                continue
        except OSError:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))   # SIOCGIFADDR
            return True
        except OSError:
            continue                              # no IPv4 address on it
    return False


# ----------------------------------------------------------------------
# MQTT login test (the external broker), from the screen itself
# ----------------------------------------------------------------------
def mqtt_test(host, port, user, password, timeout=6):
    """('ok', ms) / ('login', 0) / ('fail', 0)."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return "fail", 0
    try:
        port = int(port)
    except (TypeError, ValueError):
        return "fail", 0
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-screen-test-{uuid.uuid4().hex[:6]}")
    if user:
        c.username_pw_set(user, password)
    done, res = threading.Event(), {"rc": None}

    def on_connect(cl, u, f, rc, p=None):
        res["rc"] = rc
        done.set()

    c.on_connect = on_connect
    t0 = time.monotonic()
    try:
        c.connect(host, port, keepalive=10)
        c.loop_start()
        done.wait(timeout)
    except (OSError, ValueError):
        return "fail", 0
    finally:
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:
            pass
    rc = res["rc"]
    if rc is None:
        return "fail", 0
    if getattr(rc, "is_failure", False):
        return "login", 0
    return "ok", int((time.monotonic() - t0) * 1000)


# ----------------------------------------------------------------------
# SSH, for armonico connect: whether the server answers here, and who logs in
# ----------------------------------------------------------------------
def ssh_on(port=22):
    """True when something answers on port 22 of this machine (a quick local look, no login)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def login_user(group="armonico", passwd="/etc/passwd", groups="/etc/group"):
    """The person's login on this machine: the installer adds the user who ran it to the program's group, so the
    first member of that group with a login shell (not the service user itself). "" when there is none."""
    members = []
    try:
        for line in open(groups, encoding="utf-8", errors="replace"):
            f = line.rstrip("\n").split(":")
            if len(f) >= 4 and f[0] == group:
                members = [m for m in f[3].split(",") if m and m != group]
    except OSError:
        return ""
    shells = {}
    try:
        for line in open(passwd, encoding="utf-8", errors="replace"):
            f = line.rstrip("\n").split(":")
            if len(f) >= 7:
                shells[f[0]] = f[6]
    except OSError:
        pass
    for m in members:
        if not shells.get(m, "").endswith(("nologin", "false")):
            return m
    return ""


def host_name():
    try:
        return socket.gethostname()
    except OSError:
        return ""


# ----------------------------------------------------------------------
# Home Assistant: found by mDNS, set up with one token (setup/ha_setup.py)
# ----------------------------------------------------------------------
def find_ha(timeout=12):
    """{'found', 'host', 'url', 'broker', ...} from setup/find_ha.py, or {} when it cannot run."""
    try:
        out = subprocess.run([main_python(), str(SETUP_DIR / "find_ha.py")], capture_output=True, text=True,
                             timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def ha_run(cmd, url, token, on_line=None, timeout=600):
    """Runs ha_setup.py check|package|broker with the token in its environment only.
    Returns (code, [(kind, text)])."""
    env = dict(os.environ, HA_URL=url, HA_TOKEN=token)
    lines = []
    try:
        p = subprocess.Popen([main_python(), str(SETUP_DIR / "ha_setup.py"), cmd], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        return 1, [("fail", str(e))]
    # the limit kept by a timer of its own: checked only when a line came, a silent hang never ended
    late = threading.Event()

    def kill():
        late.set()
        p.kill()
    timer = threading.Timer(timeout, kill)
    timer.daemon = True
    timer.start()
    try:
        for line in p.stdout:
            kind, _, text = line.rstrip("\n").partition("\t")
            lines.append((kind, text))
            if on_line:
                on_line(kind, text)
            if late.is_set():
                break
    finally:
        timer.cancel()
    code = p.wait()
    if late.is_set():
        lines.append(("fail", "timeout"))
        if on_line:
            on_line("fail", "timeout")
    return code, lines


# ----------------------------------------------------------------------
# Telegram, through setup/telegram_setup.py's helpers
# ----------------------------------------------------------------------
def telegram_module():
    if str(SETUP_DIR) not in sys.path:
        sys.path.insert(0, str(SETUP_DIR))
    import telegram_setup
    telegram_setup.QUIET = True
    return telegram_setup


def tg_wait_chat(tg, token, seconds=120, stop=None):
    """The chats that wrote to the bot in the next two minutes (they press Start)."""
    try:
        tg.tg(token, "deleteWebhook")
    except Exception:
        pass
    found, offset, end = {}, None, time.time() + seconds
    while time.time() < end and not found and not (stop and stop()):
        data = {"timeout": 10, "allowed_updates": ["message"]}
        if offset:
            data["offset"] = offset
        try:
            ups = tg.tg(token, "getUpdates", data, timeout=30) or []
        except Exception:
            time.sleep(2)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id") is not None:
                found[chat["id"]] = chat.get("first_name") or chat.get("title") or str(chat["id"])
    return list(found)


