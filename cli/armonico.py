#!/usr/bin/env python3
"""armonico - the piano from a terminal.

The piano commands (play, songs, shortcuts, lessons, profiles, getmidi) travel over
MQTT exactly like the Telegram commands, and the answers come back the same way,
addressed to this terminal instead of a chat. They need read access to the settings,
which root and members of the armonico group have. The system commands (update,
restart, unstick, reset, reconfigure, uninstall, purge) need root.

Installed as /usr/local/bin/armonico, a two-line wrapper that runs this file with
the program's own Python, which has paho-mqtt.
"""
import json
import getpass
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # /opt/<name>
APP = APP_DIR.name
CONFIG = Path(f"/etc/{APP}/config.env")
VERSION = (APP_DIR / "VERSION").read_text().strip() if (APP_DIR / "VERSION").exists() else "dev"
# What a person reads: 1.0.0 is written 1.0. Only a trailing zero goes, so 1.0.1 is shown whole
SHOWN = re.sub(r"^(\d+\.\d+)\.0$", r"\1", VERSION)
REPO = (APP_DIR / "REPO").read_text().strip() if (APP_DIR / "REPO").exists() else ""
SERVICES = [f"{APP}-bridge", f"{APP}-lessons"]
TTY = sys.stdout.isatty()


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if TTY else text


BOLD, DIM = "1", "2"
BLUE, GOLD = "1;38;2;29;107;224", "38;2;226;176;74"
RED, GREEN, YELLOW = "31", "32", "33"


def die(msg, code=1):
    print(c(RED, "✘ ") + msg, file=sys.stderr)
    sys.exit(code)


# ----------------------------------------------------------------------
# The logo, the same drawing the installer shows
# ----------------------------------------------------------------------
def show_logo():
    art = APP_DIR / "setup" / "logo.ans"
    cols = os.get_terminal_size().columns if TTY else 80
    term = os.environ.get("TERM", "dumb")
    try:
        colors = int(subprocess.run(["tput", "colors"], capture_output=True, text=True).stdout or 0)
    except (OSError, ValueError):
        colors = 0
    text = {5: c(BLUE, "Armonico"), 6: c(GOLD, "Your piano talks back"),
            8: c(DIM, f"Version {SHOWN}"), 9: c(DIM, f"github.com/{REPO}" if REPO else "")}
    # any color terminal: many that take 24-bit color still report TERM=xterm and 8 colors
    if not TTY or not art.exists() or term in ("linux", "dumb") or term.startswith(("vt", "cons")) or colors < 8 or cols < 32:
        print(f"{c(BLUE, 'Armonico')} {SHOWN}")
        return
    print()
    lines = art.read_text().splitlines()
    for i, line in enumerate(lines):
        if cols >= 64 and i in text:
            print(f"  {line}\033[34G{text[i]}")
        else:
            print(f"  {line}")
    if cols < 64:
        print()
        for i in (5, 6, 8, 9):
            print(f"  {text[i]}")


# ----------------------------------------------------------------------
# Settings and MQTT
# ----------------------------------------------------------------------
def settings():
    try:
        raw = CONFIG.read_text()
    except PermissionError:
        elevate()          # the group is not active in this login yet: ask sudo for it, once
        user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "$USER"
        die(f"No access to {CONFIG}.\n  Run it with sudo, or allow this user once:"
            f"\n    sudo usermod -aG {APP} {user}\n  and log in again.")
    except FileNotFoundError:
        die(f"{CONFIG} is missing. Is {APP} installed?")
    return dict(re.findall(r"^([A-Z_]+)='([^']*)'", raw, re.M))


def connect():
    global BROKER_REFUSED
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        die("paho-mqtt is missing. Run it as installed: armonico, not python3 armonico.py")
    cfg = settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-cli-{os.getpid()}")
    if cfg.get("MQTT_USER"):
        client.username_pw_set(cfg["MQTT_USER"], cfg.get("MQTT_PASS", ""))
    ready, answer = threading.Event(), {}

    def on_connect(cl, u, f, reason, p=None):
        answer["reason"] = reason
        ready.set()

    client.on_connect = on_connect
    try:
        client.connect(cfg.get("MQTT_HOST", ""), int(cfg.get("MQTT_PORT") or 1883), keepalive=30)
    except (OSError, ValueError) as e:
        die(f"The MQTT broker cannot be reached: {e}")
    client.loop_start()
    if not ready.wait(8):
        die("The MQTT broker did not answer within 8 seconds")
    # a refused login answers too: without this check every command went on and broke on
    # "the client is not currently connected", and the status showed the broker as fine
    reason = answer.get("reason")
    if getattr(reason, "is_failure", False):
        client.loop_stop()
        BROKER_REFUSED = str(reason)
        die(f"The MQTT broker refused the connection: {reason}. Check MQTT_USER and MQTT_PASS in {CONFIG}")
    return client


BROKER_REFUSED = ""          # why the broker refused the last connection, for the status


# Answers speak of Telegram commands; here they read as the commands of this tool.
# Longer names first, so /lessons does not turn into "armonico lesson start" + "s".
TG = [("/lessonstop", "armonico lesson stop"), ("/lessons", "armonico lesson list"),
      ("/lesson", "armonico lesson start"), ("/pianoplay", "armonico play"),
      ("/pianosongs", "armonico songs"), ("/pianostop", "armonico stop"),
      ("/pianoreset", "armonico reset"),
      ("/pianostatus", "armonico status"), ("/pianoprofile", "armonico profile"),
      ("/seqlearn", "armonico shortcut learn"), ("/seqcancel", "armonico shortcut cancel"),
      ("/seqlist", "armonico shortcut list"), ("/seqadd", "armonico shortcut add"),
      ("/seqdel", "armonico shortcut del"), ("/getmidi", "armonico getmidi"),
      ("/pianocode", "armonico code"), ("/showlog", "armonico logs -f"), ("/stoplog", "Ctrl+C")]


def terminal_text(text):
    for tg, cli in TG:
        text = re.sub(re.escape(tg) + r"\b", cli, text)
    return text


# The broker cannot be trusted to say who may publish what, so these three carry the
# secret from the settings file. A terminal that can read it is already trusted.
GUARDED_ACTIONS = ("code", "editmode", "profile", "plexfun")


def guarded(action, args):
    if action not in GUARDED_ACTIONS:
        return args
    secret = settings().get("MQTT_ADMIN_SECRET", "")
    if not secret:
        die(f"MQTT_ADMIN_SECRET is missing from {CONFIG}. Run: sudo armonico reconfigure")
    return f"secret:{secret} {args}".strip()


def ask(topic, action, args="", first_wait=10.0, quiet=1.5, keep_waiting=None, max_wait=0, on_ctrl_c=None, raw=False):
    """Sends one command the way Telegram does and prints the answers addressed to this terminal.

    Waits first_wait seconds for the first answer, then until no more arrive for `quiet`
    seconds. keep_waiting(last answer) true means a later answer is still coming, as when
    learning a shortcut, for up to max_wait seconds. raw: printed as it came, for data such as
    the text export's JSON, where a Telegram command name in a wording must stay as it is."""
    args = guarded(action, args)
    chat = f"cli-{uuid.uuid4().hex[:8]}"
    got, lock, last = [], threading.Lock(), [0.0]
    client = connect()

    def on_message(cl, u, msg):
        who, _, text = msg.payload.decode(errors="replace").partition("|")
        if who == chat:
            with lock:
                got.append(text)
                last[0] = time.time()
            print(text if raw else terminal_text(text), flush=True)

    client.on_message = on_message
    client.subscribe("piano/shortcut/reply", qos=1)
    time.sleep(0.3)
    client.publish(topic, f"{action}|{chat}|{args}", qos=1).wait_for_publish(5)
    start = time.time()
    try:
        while True:
            time.sleep(0.1)
            with lock:
                n, t = len(got), last[0]
                final = n and not (keep_waiting and keep_waiting(got[-1]))
            if n == 0 and time.time() - start > first_wait:
                client.loop_stop()
                die("No answer. Is the piano running?  systemctl status " + " ".join(SERVICES))
            if n and time.time() - t > quiet and (final or time.time() - start > max_wait):
                break
    except KeyboardInterrupt:
        if on_ctrl_c:
            client.publish(topic, f"{on_ctrl_c}|{chat}|", qos=1).wait_for_publish(5)
            time.sleep(1)
        print()
    client.loop_stop()
    client.disconnect()


def retained(topics, wait=1.5):
    client = connect()
    values = {}
    client.on_message = lambda cl, u, m: values.__setitem__(m.topic, m.payload.decode(errors="replace"))
    for t in topics:
        client.subscribe(t)
    time.sleep(wait)
    client.loop_stop()
    client.disconnect()
    return values


# ----------------------------------------------------------------------
# Piano commands
# ----------------------------------------------------------------------
def nice(s):
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", s).replace("_", " ").strip()


def run_out(*cmd, timeout=4):
    """A command's output, or "" when it is missing or fails: a check never breaks the status."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def checks(cfg, broker_ok):
    """Health of the machine and, on Armonico Pi, of the device: (name, mark, text) rows.
    Each check looks at one thing that has gone wrong before and says what to do about it."""
    ok, warn, bad = c(GREEN, "✔"), c(YELLOW, "!"), c(RED, "✘")
    rows = []
    device = cfg.get("DEVICE") == "yes"
    data = Path(cfg.get("DATA_DIR") or f"/var/lib/{APP}")

    rows.append(("Broker", ok if broker_ok else bad,
                 f"{cfg.get('MQTT_HOST')}:{cfg.get('MQTT_PORT')}" if broker_ok
                 else f"{cfg.get('MQTT_HOST')}:{cfg.get('MQTT_PORT')} refused the login: {BROKER_REFUSED}" if BROKER_REFUSED
                 else f"{cfg.get('MQTT_HOST')}:{cfg.get('MQTT_PORT')} does not answer"))

    # the lesson screen, and the songs that cannot be lessons with the reason why
    try:
        from urllib.request import Request, urlopen
        code_file = data / "screen_code"
        head = {"X-Screen-Code": code_file.read_text().strip()} if code_file.exists() else {}
        port = cfg.get("LESSON_UI_PORT") or "8099"
        with urlopen(Request(f"http://127.0.0.1:{port}/api/overview", headers=head), timeout=4) as r:
            songs = json.loads(r.read()).get("songs", [])
        bad_songs = [x for x in songs if not x.get("total")]
        rows.append(("Lesson screen", ok, f"answering on port {port}, {len(songs)} songs"))
        for x in bad_songs:
            rows.append(("Not a lesson", warn, f"{nice(x['name'])}: {x.get('problem') or 'cannot be read'}"))
    except PermissionError:
        rows.append(("Lesson screen", warn, "the screen code cannot be read: run it with sudo"))
    except Exception as e:
        rows.append(("Lesson screen", bad, f"no answer ({e})"))

    try:
        st = os.statvfs(data if data.exists() else "/")
        free = st.f_bavail * st.f_frsize / 1e9
        rows.append(("Disk", ok if free > 1 else warn, f"{free:.1f} GB free" + ("" if free > 1 else ", running low")))
    except OSError:
        pass

    # a Pi says whether its power ever dipped: on PoE this is the first thing to look at
    thr = run_out("vcgencmd", "get_throttled").strip()
    if thr.startswith("throttled="):
        bits = int(thr.split("=")[1], 16)
        if bits & 0x1:
            rows.append(("Power", bad, "under-voltage right now: the PoE switch or HAT is not giving enough"))
        elif bits & 0x50000:
            rows.append(("Power", warn, "under-voltage or throttling happened since the last start"))
        else:
            rows.append(("Power", ok, "no under-voltage since the last start"))

    if not device:
        return rows
    samples = len(list((data / "device" / "samples").glob("*.wav"))) if (data / "device" / "samples").is_dir() else 0
    rows.append(("Piano samples", ok if samples >= 88 else bad,
                 f"{samples} of 88" + ("" if samples >= 88 else ": sudo ./install.sh --device prepares them")))
    cards = run_out("aplay", "-l")
    rows.append(("Sound card", ok if "Headphones" in cards else bad,
                 "headphone jack found" if "Headphones" in cards else "no headphone jack in aplay -l"))
    mix = re.search(r"\[(-?[\d.]+dB)\]", run_out("amixer", "-c", "Headphones", "sget", "PCM"))
    if mix:
        rows.append(("Jack level", ok, mix.group(1)))
    amidi = cfg.get("AMIDI_DEVICE", "hw:7,0")
    rows.append(("Lesson output", ok if amidi.split(":")[-1] in run_out("amidi", "-l") else bad,
                 amidi if amidi.split(":")[-1] in run_out("amidi", "-l") else f"{amidi} missing: sudo modprobe snd-virmidi"))
    name = cfg.get("MIDI_DEVICE", "Armonico Pi")
    rows.append(("Keys", ok if name in run_out("aconnect", "-i") else bad,
                 f"'{name}' on the sequencer" if name in run_out("aconnect", "-i") else f"'{name}' not on the sequencer: armonico logs"))
    rows.append(("GPIO", ok if Path("/dev/gpiochip0").exists() else bad,
                 "/dev/gpiochip0" if Path("/dev/gpiochip0").exists() else "no /dev/gpiochip0"))
    dri = sorted(p.name for p in Path("/dev/dri").glob("card*")) if Path("/dev/dri").is_dir() else []
    rows.append(("Display", ok if dri else bad, ", ".join(dri) if dri else "no /dev/dri: vc4-kms-v3d is off in config.txt"))
    boot = next((p for p in (Path("/boot/firmware/config.txt"), Path("/boot/config.txt")) if p.exists()), None)
    if boot:
        text = boot.read_text(errors="replace")
        on = lambda key: re.search(rf"^\s*{re.escape(key)}", text, re.M) is not None
        problems = []
        if not on("dtoverlay=gpio-shutdown"):
            problems.append("no power button overlay")
        if on("dtparam=spi=on"):
            problems.append("SPI is on, the keys on GPIO 7 to 11 need it off")
        if on("dtparam=i2c_arm=on"):
            problems.append("I2C is on, the power button on GPIO3 needs it off")
        rows.append(("config.txt", warn if problems else ok, "; ".join(problems) if problems else "power button on, SPI and I2C off"))
    return rows


def cmd_status(_):
    names = ["status", "play/status", "play/current", "play/source", "play/duration", "play/last_finished",
             "play/stopped_by", "alarm/status", "alarm/last_song", "alarm/stopped_by", "game/status",
             "game/song", "profile", "practice"]
    cfg = settings()
    try:
        v, broker_ok = retained([f"piano/{n}" for n in names]), True
    except SystemExit:                       # the broker is down: the rest of the checks still run
        v, broker_ok = {}, False
    g = lambda n: v.get(f"piano/{n}", "")
    conn = {"online": c(GREEN, "connected"), "offline": c(RED, "offline"), "starting": "starting",
            "resetting": "resetting", "reconnecting": "reconnecting", "disconnected": c(YELLOW, "disconnected"),
            "timeout": c(RED, "did not come back"), "stuck": c(RED, "stuck, needs a power-cycle")}
    rows = []
    for s in SERVICES + (DEVICE_UNITS if cfg.get("DEVICE") == "yes" else []):
        state = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True).stdout.strip()
        rows.append((s, c(GREEN, state) if state == "active" else c(RED, state or "unknown")))
    rows.append(("Keyboard", conn.get(g("status"), g("status") or "no report yet")))
    if g("play/status") == "playing":
        rows.append(("Playing", f"{nice(g('play/current'))}, {g('play/duration')}".rstrip(", ")))
        if g("play/source"):
            rows.append(("Started by", g("play/source")))
    elif g("play/last_finished"):
        end = "played to the end" if g("play/stopped_by") == "Finished" else f"stopped by {g('play/stopped_by')}"
        rows.append(("Last song", f"{nice(g('play/last_finished'))}, {end}"))
    rows.append(("Alarm", c(GOLD, "ringing") if g("alarm/status") == "on" else "off"))
    if g("alarm/last_song"):
        rows.append(("Last alarm song", nice(g("alarm/last_song"))))
    if g("game/status") == "lesson":
        rows.append(("Lesson", nice(g("game/song"))))
    if g("profile"):
        rows.append(("Profile", g("profile")))
    try:
        p = json.loads(g("practice") or "{}")
        if p:
            n = lambda k, one, many: f"{p.get(k, 0)} {one if p.get(k, 0) == 1 else many}"
            rows.append(("Today", f"{n('lessons_today', 'lesson', 'lessons')}, {n('minutes_today', 'minute', 'minutes')}, "
                                  f"{n('due', 'part', 'parts')} due, {p.get('streak', 0)} day streak"))
    except ValueError:
        pass
    found = checks(cfg, broker_ok)
    width = max(len(k) for k, _ in rows + [(k, 0) for k, _, _ in found])
    for k, val in rows:
        print(f"  {c(DIM, k.ljust(width))}  {val}")
    print()
    print(f"  {c(BOLD, 'Checks')}")
    for k, mark, val in found:
        print(f"  {mark} {c(DIM, k.ljust(width))}  {val}")


def check_request(action, timeout=10):
    """The one root action the full check asks, telegram-kept: as root it is read here directly (only
    whether a token is kept), otherwise through the device's root unit, when this user may ask it."""
    sys.path.insert(0, str(APP_DIR / "device"))
    if os.geteuid() == 0:
        import fullcheck
        return True, json.dumps({"kept": bool(fullcheck._ra().secret_read("TELEGRAM_TOKEN"))})
    from ui import system
    return system.request(action, timeout=timeout)


def cmd_check_deep():
    """The deep check (device/deepcheck.py), the same engine as the device's Deep check button: the services
    over their whole uptime and a 30-second live window, the system, boot stages, storage and logs. Prints
    the report and saves it as text and JSON in the data folder's checks/."""
    cfg = dict(os.environ)
    if CONFIG.exists():                       # a machine without an install can still be measured
        cfg.update(settings())
    os.environ.setdefault("DATA_DIR", cfg.get("DATA_DIR") or (f"/var/lib/{APP}" if CONFIG.exists() else str(Path.home() / f".local/share/{APP}")))
    sys.path.insert(0, str(APP_DIR / "device"))
    try:
        import deepcheck
    except Exception as e:
        die(f"The deep check cannot run here: {e}")
    print(f"  {c(BOLD, 'Deep check')}  {c(DIM, 'Armonico ' + SHOWN)}  {c(DIM, f'measuring for {deepcheck.LIVE_SECONDS} seconds')}", flush=True)
    rep = deepcheck.collect()
    print()
    print(deepcheck.report_text(rep))
    try:
        path = deepcheck.save_report(rep)
        print(f"  Report: {path} (and {path.with_suffix('.json').name})")
    except OSError as e:
        print(c(YELLOW, "  ! ") + f"The report was not saved: {e}")
    if rep["counts"][deepcheck.PROBLEM]:
        sys.exit(1)


def cmd_check(a):
    """The full check's automatic part, as Settings › System › Device test runs it, without the guided
    steps. The results here, the same report file in the data folder, exit 1 when something failed.
    `check --deep`: the deep check instead."""
    if "--deep" in a:
        return cmd_check_deep()
    cfg = dict(os.environ)
    cfg.update(settings())
    sys.path.insert(0, str(APP_DIR / "device"))
    try:
        import fullcheck
    except Exception as e:
        die(f"The full check cannot run here: {e}")
    F = fullcheck
    os.environ.setdefault("DATA_DIR", cfg.get("DATA_DIR") or f"/var/lib/{APP}")
    print(f"  {c(BOLD, 'Full check')}  {c(DIM, 'Armonico ' + SHOWN)}")
    ctx = {"request": check_request}
    results = F.run_all(cfg, ctx)
    items = F.flat(results)
    mark = {F.OK: c(GREEN, "✔"), F.NOTE: c(YELLOW, "!"), F.PROBLEM: c(RED, "✘"), F.SKIP: c(DIM, "-")}
    width = max([len(i.label[0]) for i in items] + [10])
    for i in items:
        print(f"  {mark[i.level]} {c(DIM, i.label[0].ljust(width))}  {i.detail[0]}")
        if i.level in (F.NOTE, F.PROBLEM) and i.todo[0]:
            print(f"    {' ' * width}  {c(GOLD, '→ ' + i.todo[0])}")
    n = F.counts(items)
    print()
    print(f"  {n[F.PROBLEM]} failed, {n[F.NOTE]} warnings, {n[F.OK]} passed, {n[F.SKIP]} not checked")
    try:
        path = F.save_report(F.report_text(items, None, guided_done=False), cfg)
        print(f"  Report: {path}")
    except OSError as e:
        print(c(YELLOW, "  ! ") + f"The report was not saved: {e}")
    if n[F.PROBLEM]:
        sys.exit(1)


def cmd_play(a):
    need(a, 1, "armonico play <song>")
    ask("piano/shortcut/cmd", "play", " ".join(a))


def cmd_songs(a):
    ask("piano/shortcut/cmd", "songs", " ".join(a))


def cmd_stop(_):
    client = connect()
    client.publish("piano/stop", "Terminal", qos=1).wait_for_publish(5)
    client.loop_stop()
    client.disconnect()
    print("⏹️ Stop sent")


def cmd_shortcut(a):
    sub = a[0] if a else "list"
    rest = " ".join(a[1:])
    if sub == "list":
        ask("piano/shortcut/cmd", "list")
    elif sub == "add":
        need(a, 3, "armonico shortcut add <name> <note> <note> ...   (MIDI note numbers, middle C is 60)")
        ask("piano/shortcut/cmd", "add", rest)
    elif sub in ("del", "delete", "rm"):
        need(a, 2, "armonico shortcut del <name>")
        ask("piano/shortcut/cmd", "del", rest)
    elif sub == "learn":
        need(a, 2, "armonico shortcut learn <name>")
        # the 🎓 answer asks to play; the next one says it was saved, or closed after a minute
        ask("piano/shortcut/cmd", "learn", rest, keep_waiting=lambda t: "🎓" in t, max_wait=75, on_ctrl_c="cancel")
    elif sub == "cancel":
        ask("piano/shortcut/cmd", "cancel")
    else:
        die(f"Unknown: shortcut {sub}. See: armonico help")


def cmd_lesson(a):
    sub = a[0] if a else "list"
    if sub == "list":
        ask("piano/game/cmd", "lessons")
    elif sub == "start":
        need(a, 2, "armonico lesson start <song> [reset]")
        ask("piano/game/cmd", "lesson", " ".join(a[1:]))
    elif sub == "stop":
        ask("piano/game/cmd", "lessonstop")
    else:
        die(f"Unknown: lesson {sub}. See: armonico help")


def cmd_getmidi(a):
    need(a, 1, "armonico getmidi <name>, then armonico getmidi <number> to download a result")
    ask("piano/game/cmd", "getmidi", " ".join(a), first_wait=45, quiet=2.5)


def cmd_profile(a):
    ask("piano/game/cmd", "profile", " ".join(a))


def cmd_code(a):
    what = (a[0] if a else "").lower()
    if what in ("", "show"):
        ask("piano/game/cmd", "code")
    elif what in ("on", "off", "new"):
        ask("piano/game/cmd", "code", what)
    else:
        die("Usage: armonico code [show | on | off | new]")


def cmd_text(a):
    """Prints this machine's edited wording, as the file a release carries.

        armonico text export > ~/armonico/lessons/text_defaults.json

    Commit that file and the next release looks the way this screen looks. A machine that
    installs it can still edit on top; its own edits win over what came with the code."""
    if (a[0] if a else "") != "export":
        die("Usage: armonico text export > lessons/text_defaults.json")
    ask("piano/game/cmd", "textexport", quiet=1.0, raw=True)


def cmd_version(_):
    print(f"Armonico {SHOWN}")


# ----------------------------------------------------------------------
# System commands, as root
# ----------------------------------------------------------------------
def elevate():
    """Runs this same command again through sudo: one password question, nothing to retype."""
    if os.geteuid() != 0 and shutil.which("sudo") and sys.stdin.isatty():
        # the interpreter first: the file is a script that is not executable by itself (the armonico
        # command is a small wrapper that starts it with the program's own Python)
        os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]])


def root(what):
    """These commands change the system, so they run as root. Without it the command asks sudo
    for itself, so nobody has to type sudo in front of it."""
    if os.geteuid() != 0:
        elevate()
        die(f"This needs root: sudo armonico {what}")


def run(argv):
    try:
        os.execvp(argv[0], argv)
    except OSError as e:
        die(f"{argv[0]}: {e}")


def cmd_update(a):
    """Asks the update service for a release, as the lesson screen does, and follows it."""
    root("update")
    cfg = settings()
    data = Path(cfg.get("DATA_DIR") or f"/var/lib/{APP}")
    tag = a[0] if a else "latest"
    if not re.fullmatch(r"latest|v?\d+(\.\d+){1,3}", tag):
        die("A version looks like 1.0.1, or leave it out for the newest one")
    if not data.is_dir():
        die(f"{data} is missing. Is {APP} installed?")
    status = data / "update-status.json"
    before = status.stat().st_mtime if status.exists() else 0
    (data / "update-request").write_text(tag + "\n")
    print(f"Update to {tag} requested")
    # the path unit starts the update service; when it is not running, the update runs right here
    watching = subprocess.run(["systemctl", "is-active", "--quiet", f"{APP}-update.path"]).returncode == 0
    if not watching:
        subprocess.run([str(APP_DIR / "setup" / "update.sh")], check=False)
    seen, start = "", time.time()
    while time.time() - start < 1800:
        time.sleep(1)
        if not status.exists() or status.stat().st_mtime <= before:
            continue
        try:
            st = json.loads(status.read_text())
        except ValueError:
            continue
        line = st.get("message", "")
        if line != seen:
            print(f"  {line}")
            seen = line
        if st.get("state") in ("done", "failed"):
            if st["state"] == "failed":
                print(f"  Details: {data / 'update.log'}")
            sys.exit(0 if st["state"] == "done" else 1)
    die("No result after 30 minutes. Details: journalctl -u " + f"{APP}-update")


def cmd_restart(_):
    root("restart")
    units = list(SERVICES)
    # the recorder too, when it was turned on: the full reset restarts it the same way. On the
    # Armonico Pi the keys and the screen as well, as a switch of the broker restarts them
    for u in DEVICE_UNITS + [f"{APP}-recorder"]:
        if subprocess.run(["systemctl", "is-enabled", "--quiet", u]).returncode == 0:
            units.append(u)
    subprocess.run(["systemctl", "restart", *units], check=False)
    for s in units:
        state = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True).stdout.strip()
        print(f"  {s}: {c(GREEN, state) if state == 'active' else c(RED, state)}")


def cmd_logs(a):
    """The system journal. Members of the adm or systemd-journal group read it without sudo."""
    unit = f"{APP}-lessons" if "lessons" in a else f"{APP}-bridge"
    argv = ["journalctl", "-u", unit, "-n", "50", "--no-pager"]
    if "-f" in a or "--follow" in a:
        argv = ["journalctl", "-fu", unit, "-n", "20"]
    run(argv)


def cmd_record(a):
    """Every MQTT message on the broker, to one file per day. See bridge/mqtt_recorder.py."""
    unit = f"{APP}-recorder"
    sub = a[0] if a else "status"
    data = Path(settings().get("DATA_DIR") or f"/var/lib/{APP}")
    folder = data / "mqtt"
    if sub in ("on", "off"):
        root(f"record {sub}")
        subprocess.run(["systemctl", "enable" if sub == "on" else "disable", "--now", unit],
                       check=False, capture_output=True)
        sub = "status"
    if sub == "status":
        active = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
        files = sorted(folder.glob("????-??-??.log")) if folder.is_dir() else []
        size = sum(f.stat().st_size for f in files)
        print(f"  {c(DIM, 'Recorder')}  {c(GREEN, 'on') if active == 'active' else 'off'}")
        print(f"  {c(DIM, 'Folder  ')}  {folder}")
        if files:
            print(f"  {c(DIM, 'Files   ')}  {len(files)} days, {size / 1048576:.1f} MB, today: {files[-1].name}")
        print(f"  {c(DIM, 'Kept    ')}  {settings().get('MQTT_RECORD_DAYS') or 14} days (MQTT_RECORD_DAYS)")
    elif sub == "follow":
        match = " ".join(a[1:])
        follow(folder, match)
    else:
        die(f"Unknown: record {sub}. See: armonico help")


def follow(folder, match=""):
    """Like tail -F on today's file, moving to the next file at midnight. A filter keeps lines that contain it."""
    from datetime import date
    path, f = None, None
    try:
        while True:
            today = folder / f"{date.today():%Y-%m-%d}.log"
            if today != path and today.exists():
                if f:
                    f.close()
                f, path = today.open(encoding="utf-8", errors="replace"), today
                if not match:
                    lines = f.readlines()[-20:]
                    print("".join(lines), end="")
                else:
                    f.seek(0, 2)
            line = f.readline() if f else ""
            if line:
                if match in line:
                    print(line, end="", flush=True)
            else:
                time.sleep(0.3)
    except KeyboardInterrupt:
        print()
    except PermissionError:
        elevate()
        die(f"No access to {folder}. Run it with sudo, or join the {APP} group")


def cmd_unstick(a):
    root("unstick")
    run([str(APP_DIR / "bridge" / "piano_unstick.sh"), *a])


def cmd_reset(a):
    """The full reset: unstick, a USB reset of the keyboard and a restart of everything. Same as /pianoreset."""
    root("reset")
    run([str(APP_DIR / "bridge" / "piano_reset.sh"), *a])


def installer(what, flags):
    root(what)
    run(["bash", str(APP_DIR / "install.sh"), *flags])


def cmd_reconfigure(a):
    installer("reconfigure", ["--reconfigure", *[x for x in a if x == "--cli"]])


def cmd_telegram(_):
    """The Telegram bot, step by step, with a question before each one. See setup/telegram_setup.py."""
    run([str(APP_DIR / "venv" / "bin" / "python"), str(APP_DIR / "setup" / "telegram_setup.py")])


def cmd_integrations(_):
    """Refreshes the Home Assistant parts and the Telegram bot's profile, after an update."""
    installer("integrations", ["--integrations"])


def cmd_connect(_):
    """Armonico Pi: the external broker, Home Assistant and Telegram from this terminal, as the install
    sets them up. The device's screen shows how to get here over SSH; it goes on by itself at the end."""
    if not Path(f"/etc/{APP}/device").exists():
        die(f"armonico connect is for the Armonico Pi. On this machine: sudo {APP} reconfigure")
    installer("connect", ["--connect"])


def cmd_forget_tokens(_):
    """Deletes the tokens the installer kept for updates."""
    root("forget-tokens")
    f = Path(f"/etc/{APP}/secrets.env")
    if f.exists():
        f.unlink()
        print("The kept tokens are deleted. Delete the token in Home Assistant too (Profile, Security).")
    else:
        print("No tokens are kept.")


def cmd_uninstall(_):
    installer("uninstall", ["--uninstall"])


def cmd_purge(a):
    installer("purge", ["--purge", *[x for x in a if x == "--yes"]])


# ----------------------------------------------------------------------
# The Armonico Pi device: two brokers, one switch
# ----------------------------------------------------------------------
MODE_KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASS", "MQTT_LOCAL", "MQTT_REC_USER", "MQTT_REC_PASS")
MODES_DIR = CONFIG.parent / "modes"
DEVICE_UNITS = [f"{APP}-device", f"{APP}-screen"]


def mode_of(cfg):
    """Return the internal profile name for the broker currently in use."""
    return "standalone" if cfg.get("MQTT_LOCAL") == "yes" else "lab"


def check_mqtt_broker(host, port, user, password):
    """Verify that the requested MQTT broker accepts this connection before saving it."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        die("paho-mqtt is missing. Run this command with the installed armonico CLI.")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"armonico-setup-check-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    if user:
        client.username_pw_set(user, password)
    connected = threading.Event()
    result = {"ok": False, "reason": "no response"}

    def on_connect(cl, userdata, flags, reason_code, properties=None):
        result["ok"] = not reason_code.is_failure
        result["reason"] = str(reason_code)
        connected.set()

    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()
        if not connected.wait(6):
            result["reason"] = "broker did not answer within 6 seconds"
    except (OSError, ValueError) as e:
        result["reason"] = str(e)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass
    if not result["ok"]:
        die(f"Could not connect to the MQTT broker at {host}:{port}: {result['reason']}. Nothing was changed.")


def configure_shared_broker():
    """Interactively configure and verify the external broker on the home network, without exposing the password in argv."""
    if not sys.stdin.isatty():
        die("Run interactively: sudo armonico device-mode setup-external")
    print("Set up the MQTT broker used by the Armonico system on your home network.\n"
          "This is only needed once. Switching modes later will not ask for these details again.")
    host = input("Broker address (IP or host name): ").strip()
    if not host or any(ch in host for ch in "'\r\n"):
        die("A valid broker address is required. Nothing was changed.")
    port_text = input("Broker port [1883]: ").strip() or "1883"
    try:
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        die("Port must be a number between 1 and 65535. Nothing was changed.")
    user = input("MQTT username (leave empty if not required): ").strip()
    if any(ch in user for ch in "'\r\n"):
        die("The username contains a character unsupported by the current settings format. Nothing was changed.")
    password = getpass.getpass("MQTT password (input hidden): ") if user else ""
    if any(ch in password for ch in "'\r\n"):
        die("The password contains a character unsupported by the current settings format. Nothing was changed.")
    check_mqtt_broker(host, port, user, password)
    profile = {"MQTT_HOST": host, "MQTT_PORT": str(port), "MQTT_USER": user, "MQTT_PASS": password,
               "MQTT_LOCAL": "no", "MQTT_REC_USER": user, "MQTT_REC_PASS": password}
    save_profile(profile, "lab")
    print(f"Verified and saved the external broker at {host}:{port}.")
    print("Switching to it now. Armonico services will restart; you can switch back from device Settings.")
    switch_mode("lab")


def read_profile(name):
    try:
        return dict(re.findall(r"^([A-Z_]+)='([^']*)'", (MODES_DIR / f"{name}.env").read_text(), re.M))
    except OSError:
        return None


def save_profile(cfg, name=None):
    name = name or mode_of(cfg)
    MODES_DIR.mkdir(mode=0o750, exist_ok=True)
    os.chown(MODES_DIR, 0, CONFIG.stat().st_gid)
    path = MODES_DIR / f"{name}.env"
    old = os.umask(0o027)
    try:
        path.write_text("".join(f"{k}='{cfg.get(k, '')}'\n" for k in MODE_KEYS))
    finally:
        os.umask(old)
    os.chown(path, 0, CONFIG.stat().st_gid)
    return name


def switch_mode(name):
    prof = read_profile(name)
    if prof is None:
        die(f"No saved {name} settings. Set the broker with: sudo armonico reconfigure, "
            "then save it: sudo armonico device-mode save")
    cfg = settings()
    if mode_of(cfg) != name:
        save_profile(cfg)                           # keep the current broker for the way back
    text = CONFIG.read_text()
    for k in MODE_KEYS:
        line = f"{k}='{prof.get(k, '')}'"
        if re.search(rf"^{k}='[^']*'$", text, re.M):
            text = re.sub(rf"^{k}='[^']*'$", lambda m: line, text, flags=re.M)
        else:
            text += line + "\n"
    tmp = CONFIG.with_name(".config.mode")
    old = os.umask(0o027)
    try:
        tmp.write_text(text)
    finally:
        os.umask(old)
    os.chown(tmp, 0, CONFIG.stat().st_gid)
    tmp.replace(CONFIG)
    units = SERVICES + [u for u in DEVICE_UNITS + [f"{APP}-recorder"]
                        if subprocess.run(["systemctl", "is-enabled", "--quiet", u]).returncode == 0]
    subprocess.run(["systemctl", "restart", *units], check=False)
    public_name = "Local broker" if name == "standalone" else "External broker"
    print(f"Connection: {public_name} ({prof.get('MQTT_HOST')}:{prof.get('MQTT_PORT')}). Restarted: {' '.join(units)}")


def cmd_device_mode(a):
    """Public names are 'local-broker' and 'external-broker'; the older names (shared-broker, setup-shared and
    the internal ones) are still accepted."""
    if not a or a[0] in ("status",):
        cfg = settings()
        current = mode_of(cfg)
        current_name = "Local broker" if current == "standalone" else "External broker"
        print(f"Connection mode: {current_name} ({cfg.get('MQTT_HOST')}:{cfg.get('MQTT_PORT')})")
        for name, label in (("standalone", "Local broker"), ("lab", "External broker")):
            prof = read_profile(name)
            print(f"  {label}: " + (f"{prof.get('MQTT_HOST')}:{prof.get('MQTT_PORT')}" if prof else "not configured"))
        print("Switching uses the saved profile and restarts the Armonico services.")
        return
    root("device-mode " + a[0])
    cmd = a[0]
    if cmd == "save":
        print(f"Saved the current broker as the {save_profile(settings())} profile")
    elif cmd in ("setup-external", "setup-shared"):
        configure_shared_broker()
    elif cmd in ("set-external", "set-shared", "set-lab"):
        if len(a) < 2:
            die(f"Usage: sudo {APP} device-mode {cmd} <broker address> [port] [user] [password] (prefer setup-external)")
        host, port, user, pw = (a[1:] + ["", "", "", ""])[:4]
        # the same checks as setup-external: a quote or a line break would break the settings file
        if not host or any(ch in host for ch in "'\r\n"):
            die("A valid broker address is required. Nothing was changed.")
        try:
            if not 1 <= int(port or "1883") <= 65535:
                raise ValueError
        except ValueError:
            die("Port must be a number between 1 and 65535. Nothing was changed.")
        if any(ch in user for ch in "'\r\n"):
            die("The username contains a character unsupported by the current settings format. Nothing was changed.")
        if any(ch in pw for ch in "'\r\n"):
            die("The password contains a character unsupported by the current settings format. Nothing was changed.")
        port = str(int(port or "1883"))
        prof = {"MQTT_HOST": host, "MQTT_PORT": port or "1883", "MQTT_USER": user, "MQTT_PASS": pw,
                "MQTT_LOCAL": "no", "MQTT_REC_USER": user, "MQTT_REC_PASS": pw}
        save_profile(prof, "lab")
        print(f"Saved the external broker at {host}:{prof['MQTT_PORT']}. Switch with: sudo {APP} device-mode external-broker, "
              "or in Settings › Network and links › Broker and services on the device.")
    elif cmd in ("local-broker", "local", "standalone", "external-broker", "external", "shared-broker", "shared", "network", "lab", "shared-server", "this-device"):
        switch_mode("standalone" if cmd in ("local-broker", "local", "standalone", "this-device") else "lab")
    elif cmd == "apply":
        # The screen request is handed over by the root unit armonico-device-mode.path.
        req = Path(settings().get("DATA_DIR") or f"/var/lib/{APP}") / "device-mode-request"
        try:
            want = req.read_text().strip()
        except OSError:
            return
        req.unlink(missing_ok=True)
        if want in ("standalone", "lab") and want != mode_of(settings()):
            switch_mode(want)
    else:
        die(f"Usage: sudo {APP} device-mode [status | setup-external | local-broker | external-broker | save]")


# ----------------------------------------------------------------------
HELP = """\
Piano (root or the {app} group)
  status                          keyboard, player, alarm, lesson and today's practice
  check --deep                    the deep check: services over their whole uptime and 30 s live, boot stages,
                                  system, storage, logs; saved as text and JSON
  check                           the full check's automatic part: every check, its result and what
                                  to do, saved as a report; exits 1 when something failed
  play <song>                     play a song from the songs folder
  songs [filter]                  list the songs
  stop                            stop the alarm or the song
  shortcut list                   list the shortcuts
  shortcut learn <name>           play it on the keyboard, saved after 5 seconds of silence
  shortcut add <name> <notes...>  save one from MIDI note numbers, middle C is 60
  shortcut del <name>             delete one
  lesson list                     songs and how much of each is learned
  lesson start <song> [reset]     start today's lesson, reset starts the song over
  lesson stop                     stop the lesson and save
  getmidi <name> | <number>       search free MIDI archives, then download a result
  profile [name]                  list the profiles, or switch to one
  code [show | on | off | new]    the code a lesson screen asks for once on each device:
                                  show it, turn asking on or off, or draw a new one
  text export                     the wording edited on this screen, as the file a release
                                  carries: armonico text export > lessons/text_defaults.json
  telegram                        set the Telegram bot up, a question before each step

System (sudo)
  update [version]                install the newest release, or the one given
  restart                         restart the services
  logs [lessons] [-f]             the bridge's log, or the lesson engine's; -f follows it
  record on | off                 record every MQTT message, of every topic, to a file per day
  record [follow [filter]]        the recorder's state and files, or follow today's file live
  unstick                         bring a connected but silent keyboard back
  reset                           full reset: unstick, USB reset, restart everything
  reconfigure [--cli]             run the setup again
  uninstall                       remove the program, the settings, the songs and its folder
  integrations                    refresh the Home Assistant parts and the Telegram bot's profile
                                  (uses the tokens kept at install, or asks for new ones)
  forget-tokens                   delete the tokens kept for those updates
  purge [--yes]                   remove everything, after asking
  version                         the installed version

Armonico Pi device (sudo)
  connect                         the external broker, Home Assistant and Telegram, step by step as in the
                                  install: run it over SSH, the device's screen goes on by itself after it
  device-mode                     show the current connection and saved broker profiles
  device-mode setup-external      guided setup; tests the login, saves it and switches
  device-mode local-broker        use the MQTT broker installed on this Pi
  device-mode external-broker     use the saved MQTT broker on the home network
  device-mode save                save the broker currently in use for future switching
  device-mode set-external <host> [port user password]   non-interactive setup; prefer setup-external,
                                  a password given here shows in the process list and the shell history
  screen-rec on                   record the screen to a file, with the device's sound and a USB microphone
  screen-rec off                  stop; the file goes to a USB stick when one is plugged in
  screen-rec export               move the recordings left on the SD card to a USB stick (sudo)
  screen-rec list                 the recordings on the SD card (sudo)
"""

def cmd_screen_rec(a):
    """The screen recording of the Armonico Pi: on and off go to the screen program over MQTT, as the
    Settings › System cell does; export and list run as root (device/screen_rec.py)."""
    what = a[0] if a else "list"
    if what not in ("on", "off", "export", "list"):
        die(f"Usage: {APP} screen-rec on|off|export|list")
    rec = APP_DIR / "device" / "screen_rec.py"
    if not rec.exists():
        die("Only on the Armonico Pi device (install.sh --device)")
    if what in ("on", "off"):
        client = connect()
        client.publish("piano/device/rec", what, qos=1).wait_for_publish(5)
        client.loop_stop()
        client.disconnect()
        print("Recording: the screen shows the time. To stop: armonico screen-rec off, or ▲ and ▼ held together"
              if what == "on" else "Stopping: the file is closed, then moved to a USB stick if one is plugged in")
        return
    root("screen-rec " + what)
    r = subprocess.run([sys.executable, str(rec), what])
    sys.exit(r.returncode)


COMMANDS = {"status": cmd_status, "check": cmd_check, "play": cmd_play, "songs": cmd_songs, "stop": cmd_stop,
            "shortcut": cmd_shortcut, "lesson": cmd_lesson, "getmidi": cmd_getmidi, "profile": cmd_profile, "code": cmd_code,
            "text": cmd_text,
            "update": cmd_update, "restart": cmd_restart, "logs": cmd_logs, "record": cmd_record, "unstick": cmd_unstick, "reset": cmd_reset,
            "reconfigure": cmd_reconfigure, "telegram": cmd_telegram, "integrations": cmd_integrations, "forget-tokens": cmd_forget_tokens, "connect": cmd_connect, "uninstall": cmd_uninstall, "purge": cmd_purge,
            "version": cmd_version, "device-mode": cmd_device_mode, "screen-rec": cmd_screen_rec}
# the screen's own editing tools on and off
COMMANDS["EditMode10"] = lambda a: ask("piano/game/cmd", "editmode", "on")
COMMANDS["EditMode10Off"] = lambda a: ask("piano/game/cmd", "editmode", "off")
# the TV card (Plex) on the screens, on and off: not in the help either
def cmd_plexfun(_):
    """The TV card on (hidden, like EditMode10), then its automation in Home Assistant over the kept token
    (install.sh --plex-ha). As root, since that token is root's only."""
    root("PlexFun10")
    ask("piano/game/cmd", "plexfun", "on")
    subprocess.run(["bash", str(APP_DIR / "install.sh"), "--plex-ha"], check=False)


COMMANDS["PlexFun10"] = cmd_plexfun
COMMANDS["PlexFun10Off"] = lambda a: ask("piano/game/cmd", "plexfun", "off")


def cmd_soak(a):
    """The device's long load test (device/soak.py), not in the help: start <length> (15m, 1h, 2h30m... up to 12h;
    the Pi restarts first), stop, status, recover, ha-clean, report <folder>. --no-telegram, --no-alarm, --no-reboot."""
    soak = APP_DIR / "device" / "soak.py"
    if not soak.exists():
        die("The load test is on the device only (Armonico Pi)")
    if not a:
        die("Usage: sudo armonico Soak10 start 8h [--no-telegram] [--no-alarm] | stop | status | recover")
    root("Soak10 " + " ".join(a))
    py = APP_DIR / "venv" / "bin" / "python"
    run([str(py) if py.exists() else sys.executable, str(soak), *a])


COMMANDS["Soak10"] = cmd_soak


def need(a, n, usage):
    if len(a) < n:
        die(f"Usage: {usage}")


def main(argv):
    if not argv or argv[0] in ("help", "-h", "--help"):
        show_logo()
        print()
        print(HELP.format(app=APP))
        return
    if argv[0] in ("-v", "--version"):
        return cmd_version([])
    fn = COMMANDS.get(argv[0])
    if not fn:
        die(f"Unknown command: {argv[0]}. See: armonico help")
    fn(argv[1:])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print()
        sys.exit(130)
