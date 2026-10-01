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
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        die("paho-mqtt is missing. Run it as installed: armonico, not python3 armonico.py")
    cfg = settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-cli-{os.getpid()}")
    if cfg.get("MQTT_USER"):
        client.username_pw_set(cfg["MQTT_USER"], cfg.get("MQTT_PASS", ""))
    ready = threading.Event()
    client.on_connect = lambda cl, u, f, reason, p=None: ready.set()
    try:
        client.connect(cfg.get("MQTT_HOST", ""), int(cfg.get("MQTT_PORT") or 1883), keepalive=30)
    except (OSError, ValueError) as e:
        die(f"The MQTT broker cannot be reached: {e}")
    client.loop_start()
    if not ready.wait(8):
        die("The MQTT broker did not answer within 8 seconds")
    return client


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
GUARDED_ACTIONS = ("code", "editmode", "profile")


def guarded(action, args):
    if action not in GUARDED_ACTIONS:
        return args
    secret = settings().get("MQTT_ADMIN_SECRET", "")
    if not secret:
        die(f"MQTT_ADMIN_SECRET is missing from {CONFIG}. Run: sudo armonico reconfigure")
    return f"secret:{secret} {args}".strip()


def ask(topic, action, args="", first_wait=10.0, quiet=1.5, keep_waiting=None, max_wait=0, on_ctrl_c=None):
    """Sends one command the way Telegram does and prints the answers addressed to this terminal.

    Waits first_wait seconds for the first answer, then until no more arrive for `quiet`
    seconds. keep_waiting(last answer) true means a later answer is still coming, as when
    learning a shortcut, for up to max_wait seconds."""
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
            print(terminal_text(text), flush=True)

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


def cmd_status(_):
    names = ["status", "play/status", "play/current", "play/source", "play/duration", "play/last_finished",
             "play/stopped_by", "alarm/status", "alarm/last_song", "alarm/stopped_by", "game/status",
             "game/song", "profile", "practice"]
    v = retained([f"piano/{n}" for n in names])
    g = lambda n: v.get(f"piano/{n}", "")
    conn = {"online": c(GREEN, "connected"), "offline": c(RED, "offline"), "starting": "starting",
            "resetting": "resetting", "reconnecting": "reconnecting", "disconnected": c(YELLOW, "disconnected"),
            "timeout": c(RED, "did not come back"), "stuck": c(RED, "stuck, needs a power-cycle")}
    rows = []
    for s in SERVICES:
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
    width = max(len(k) for k, _ in rows)
    for k, val in rows:
        print(f"  {c(DIM, k.ljust(width))}  {val}")


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
    ask("piano/game/cmd", "textexport", quiet=1.0)


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
    # the recorder too, when it was turned on: the full reset restarts it the same way
    if subprocess.run(["systemctl", "is-enabled", "--quiet", f"{APP}-recorder"]).returncode == 0:
        units.append(f"{APP}-recorder")
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
HELP = """\
Piano (root or the {app} group)
  status                          keyboard, player, alarm, lesson and today's practice
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

System (sudo)
  update [version]                install the newest release, or the one given
  restart                         restart the services
  logs [lessons] [-f]             the bridge's log, or the lesson engine's; -f follows it
  record on | off                 record every MQTT message, of every topic, to a file per day
  record [follow [filter]]        the recorder's state and files, or follow today's file live
  unstick                         bring a connected but silent keyboard back
  reset                           full reset: unstick, USB reset, restart everything
  reconfigure [--cli]             run the setup again
  telegram                        set the Telegram bot up, a question before each step
  uninstall                       remove the program, the settings, the songs and its folder
  integrations                    refresh the Home Assistant parts and the Telegram bot's profile
                                  (uses the tokens kept at install, or asks for new ones)
  forget-tokens                   delete the tokens kept for those updates
  purge [--yes]                   remove everything, after asking
  version                         the installed version
"""

COMMANDS = {"status": cmd_status, "play": cmd_play, "songs": cmd_songs, "stop": cmd_stop,
            "shortcut": cmd_shortcut, "lesson": cmd_lesson, "getmidi": cmd_getmidi, "profile": cmd_profile, "code": cmd_code,
            "text": cmd_text,
            "update": cmd_update, "restart": cmd_restart, "logs": cmd_logs, "record": cmd_record, "unstick": cmd_unstick, "reset": cmd_reset,
            "reconfigure": cmd_reconfigure, "telegram": cmd_telegram, "integrations": cmd_integrations, "forget-tokens": cmd_forget_tokens, "uninstall": cmd_uninstall, "purge": cmd_purge,
            "version": cmd_version}
# not listed anywhere: turns the screen's own editing tools on and off
COMMANDS["EditMode10"] = lambda a: ask("piano/game/cmd", "editmode", "on")
COMMANDS["EditMode10Off"] = lambda a: ask("piano/game/cmd", "editmode", "off")


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
