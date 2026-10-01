#!/usr/bin/env python3
"""root_actions.py - the few things the Armonico Pi screen may ask root to do.

The screen runs without root rights. It writes one JSON request file, device-request in the data
folder, and the root unit armonico-device-request.path runs this script, which reads it, removes it,
and does the one action it names, if it is one of these:

  shutdown, reboot          the power button and System, after a yes on the screen
  set-time                  the clock by hand, only while there is no network ("YYYY-MM-DD HH:MM:SS")
  set-timezone              the city's time zone (a name from /usr/share/zoneinfo)
  broker-local              Mosquitto on the device (installed when missing), then the switch to it
  broker-external           a broker at home: saved after a login test, then the switch to it
  usb-mount                 a USB stick the kernel sees, under /media/armonico (Pi OS Lite mounts nothing)
  usb-format                a USB stick erased into one exFAT or ext4 partition, then mounted
  set-brightness            the screen's backlight in /sys/class/backlight, as a share of its top (0 to 100)
  full-reset                everything deleted and installed again, after two yeses on the screen
                            (device/full_reset.sh, which shows its own steps: device/reset_screen.py)
  wifi-status, wifi-scan    Wi-Fi through NetworkManager (nmcli): the connection now, the networks around
  wifi-saved, wifi-connect  the saved Wi-Fi connections, a connection to one network (the password goes
  wifi-forget               to nmcli in a file only root reads, never on a command line), one saved forgotten
  telegram-keep, -test,     the bot token kept in /etc/armonico/secrets.env, the fixed test message with it,
  telegram-forget           and the token deleted
  telegram-kept             whether a bot token is kept: true or false, never the token (the full check)
  ha-keep, ha-kept,         Home Assistant's access token kept the same way (checked with Home Assistant first,
  ha-forget                 never handed back), whether one is kept, and the token deleted
  ha-telegram-connect       the Telegram bot connected to Home Assistant with the kept token
  bt-status, bt-scan        Bluetooth sound devices (headphones, speakers) through bluetoothctl: the saved ones
  bt-pair, bt-connect       and the ones nearby, a new one paired, trusted and connected, a saved one connected or
  bt-disconnect, bt-forget  disconnected, one forgotten (the address is checked, nothing else goes to bluetoothctl)
  bt-reconnect              a connected one asked for its missing sound profile, the link kept (after the boot, see do_bt_reconnect)
  radio                     the Wi-Fi or the Bluetooth radio switched on or off (nmcli radio wifi, bluetoothctl power,
                            rfkill), the result read back from /sys
  radio-chip                the Pi's Wi-Fi or Bluetooth chip itself turned off or on again from the next start, with the
                            Raspberry Pi overlays disable-wifi and disable-bt in /boot/firmware/config.txt (one marked block)
  telegram-report           the full check's summary sent with the kept token: counts and short lines, in a
                            fixed format, addresses taken out
  rec-export                the screen's recordings moved to a USB stick (device/screen_rec.py): the
                            one just ended (files), or all of them
  logs-read                 the kernel's I/O errors and the last 24 hours' errors per unit, for the checks (they run
                            as the armonico user, who cannot read either)
  boot-check                what the boot's file system check did (the journal), and fsck.repair=yes on the
                            kernel command line made sure of (the recovery screen after a power loss)
  service-start             armonico-bridge, -lessons, -device or mosquitto started when enabled and not running,
                            or restarted (restart)
  report-usb                the deep check's report files copied to a USB stick ("Armonico reports")
  soak-start, soak-stop     the long load test (device/soak.py): armed for once after a reboot, then the
                            reboot; stopped (its report kept) or, while it waits for the reboot, called off
  soak-export               the load test's last report (a ZIP in /var/lib/armonico-soak) copied to a USB stick ("Armonico reports")
  ssh-on                    the SSH server turned on (and installed when missing), for armonico connect

Not a request: connect_mark() is called by install.sh --connect (armonico connect, run over SSH as root)
to tell the screen what was set up there.

Anything else is refused. The progress goes to device-request.status as {id, step, state, msg},
so the screen shows each step while it runs.
"""
import importlib.util
import json
import os
import re
import secrets
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
DEFAULT_TZ = "Asia/Jerusalem"                # the zone of the screen's default city (DEFAULT_CITY in device/ui/app.py)
REQUEST = DATA_DIR / "device-request"
STATUS = DATA_DIR / "device-request.status"
MOSQ_CONF = Path("/etc/mosquitto/conf.d/armonico.conf")
MOSQ_USERS = Path("/etc/mosquitto/armonico.passwd")
MOSQ_ACL = Path("/etc/mosquitto/armonico.acl")
# the system packages the installer added, read by --purge and so by the full reset (install.sh PKG_LIST)
# where the reset's script is handed to its own unit: outside this unit's private /tmp and /var/tmp
FULL_RESET_COPY = "/run/armonico-full-reset.sh"
PKG_LIST = Path(os.environ.get("ARMONICO_PKG_LIST", "/etc/armonico/installed-packages"))

CANCEL = DATA_DIR / "device-cancel"          # the screen writes the id of the request it gives up on; see cancel_asked
CURRENT = {"rid": ""}


class Cancelled(RuntimeError):
    """The screen gave up on this action (Cancel, leaving the page, or its own timeout)."""


def cancel_asked():
    """True when the screen asked for the running request to be called off: device-cancel holds its id. A leftover
    file of an older request holds another id and does nothing."""
    try:
        return read_nofollow(CANCEL, 64).strip() == CURRENT["rid"] != ""
    except OSError:
        return False


def nap(seconds):
    """A sleep that ends early, with Cancelled, when the screen calls the action off."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel_asked():
            raise Cancelled("cancelled")
        time.sleep(min(0.25, max(0.0, end - time.monotonic())))


class Progress:
    def __init__(self, rid):
        self.rid = rid
        CURRENT["rid"] = rid

    def __call__(self, step, state="running", msg=""):
        # The data folder is the service user's: through the folder's own handle, a temp name of its
        # own and never through a link, so nothing planted there is written or chmodded by root
        safe_write(STATUS.parent, STATUS.name, json.dumps({"id": self.rid, "step": step, "state": state, "msg": msg}))


def safe_write(folder, name, text, mode=0o644, owned=False):
    """A small file written into a folder another user owns, as backup._root_safe_write does: opened
    with O_NOFOLLOW | O_EXCL through the folder's handle, so nothing placed there can redirect it.
    owned: the file is given to the folder's owner (a file the screen goes on writing itself)."""
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
        try:
            os.fchmod(fd, mode)
            if owned:
                st = os.fstat(dfd)
                os.fchown(fd, st.st_uid, st.st_gid)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def read_nofollow(path, limit=1 << 20):
    """A file in the service user's folder, read by root only when it is a plain file, not a link."""
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError("not a plain file")
        return f.read(limit).decode("utf-8", "replace")


def cli():
    spec = importlib.util.spec_from_file_location("armonico_cli_root", APP_DIR / "cli" / "armonico.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*cmd, timeout=600):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


def plain(v, limit=200):
    """A value that goes into config.env between single quotes."""
    v = str(v or "")
    if len(v) > limit or any(c in v for c in "'\r\n\0"):
        raise ValueError("unsupported character")
    return v


# ----------------------------------------------------------------------
def do_set_time(p, a):
    value = str(a.get("value", ""))
    # " UTC" at the end: the screen sends the time in UTC, so the machine's own time zone does not matter
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}( UTC)?", value):
        raise ValueError("bad time")
    run("timedatectl", "set-ntp", "false")
    r = run("timedatectl", "set-time", value)
    run("timedatectl", "set-ntp", "true")                # it corrects itself once a network comes
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "timedatectl failed")
    run("hwclock", "--systohc")


def do_set_timezone(p, a):
    tz = str(a.get("tz", ""))
    if not re.fullmatch(r"[A-Za-z_+\-]+(/[A-Za-z0-9_+\-]+){0,2}", tz) or not (Path("/usr/share/zoneinfo") / tz).is_file():
        raise ValueError("unknown time zone")
    r = run("timedatectl", "set-timezone", tz)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "timedatectl failed")
    # the lesson engine counts days in local time: it starts again with the new zone
    run("systemctl", "try-restart", "armonico-lessons")


def installed_packages():
    r = run("dpkg-query", "-W", "-f=${db:Status-Abbrev} ${binary:Package}\n", timeout=60)
    return {ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("ii ") and len(ln.split()) == 2}


def record_packages(before):
    """The packages an install here added go on the installer's list, as install.sh's record_packages
    does, so --purge (and the full reset) removes Mosquitto and what it brought."""
    added = installed_packages() - before
    if not added or not PKG_LIST.parent.is_dir():
        return
    have = set(PKG_LIST.read_text().split()) if PKG_LIST.exists() else set()
    tmp = PKG_LIST.with_name(PKG_LIST.name + ".tmp")
    old = os.umask(0o077)
    try:
        tmp.write_text("".join(f"{x}\n" for x in sorted(have | added)))
    finally:
        os.umask(old)
    os.replace(tmp, PKG_LIST)


def do_broker_local(p, a):
    c = cli()
    have = c.read_profile("standalone")
    p(0)
    if not shutil.which("mosquitto"):
        before = installed_packages()
        r = run("apt-get", "install", "-y", "-qq", "--no-install-recommends", "mosquitto", timeout=900)
        record_packages(before)
        if r.returncode:
            raise RuntimeError("Mosquitto did not install (no network?)")
    p(1)
    fresh = not have or have.get("MQTT_HOST") not in ("127.0.0.1", "localhost")
    if fresh:
        have = local_logins(c)
    p(2)
    for attempt in (1, 2):
        run("systemctl", "enable", "mosquitto")
        r = run("systemctl", "restart", "mosquitto", timeout=60)
        if r.returncode:
            raise RuntimeError("Mosquitto did not start: journalctl -u mosquitto")
        p(3)
        ok = False
        for _ in range(10):
            if mqtt_ok("127.0.0.1", 1883, have.get("MQTT_USER", ""), have.get("MQTT_PASS", "")):
                ok = True
                break
            time.sleep(1)
        if ok:
            break
        if fresh or attempt == 2:
            raise RuntimeError("The local broker did not accept the login")
        # the saved local profile's login no longer matches Mosquitto's files (they were replaced or
        # removed since): new logins once, as on a first switch, instead of failing every time
        have, fresh = local_logins(c), True
        p(2)
    p(4)
    p(4, "done")                                          # before the services (and this screen) restart
    c.switch_mode("standalone")


def local_logins(c):
    """Mosquitto's files and the saved local profile, with 2 new logins."""
    # the same two logins as install.sh makes: piano acts in its branch, piano-rec only listens
    pw, rec = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    old = os.umask(0o077)
    try:
        MOSQ_USERS.write_text(f"piano:{pw}\npiano-rec:{rec}\n")
    finally:
        os.umask(old)
    run("mosquitto_passwd", "-U", str(MOSQ_USERS))
    MOSQ_ACL.write_text("# Armonico: who may touch what.\nuser piano\ntopic readwrite piano/#\n\n"
                        "user piano-rec\ntopic read #\n")
    for f in (MOSQ_USERS, MOSQ_ACL):
        shutil.chown(f, "mosquitto", "mosquitto")
    os.chmod(MOSQ_USERS, 0o700)
    os.chmod(MOSQ_ACL, 0o640)
    MOSQ_CONF.parent.mkdir(parents=True, exist_ok=True)
    # both listeners, as install.sh writes them: before, only 1883 was written and the status screen,
    # which speaks MQTT over websockets on 9001, lost its broker after a switch back to the local one
    MOSQ_CONF.write_text("# Armonico: the local broker.\nper_listener_settings true\nlistener 1883 127.0.0.1\n"
                         f"allow_anonymous false\npassword_file {MOSQ_USERS}\nacl_file {MOSQ_ACL}\n\n"
                         "# the touch screen (screen/piano-status.html) speaks MQTT over websockets\n"
                         "listener 9001\nprotocol websockets\n"
                         f"allow_anonymous false\npassword_file {MOSQ_USERS}\nacl_file {MOSQ_ACL}\n")
    have = {"MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1883", "MQTT_USER": "piano", "MQTT_PASS": pw,
            "MQTT_LOCAL": "yes", "MQTT_REC_USER": "piano-rec", "MQTT_REC_PASS": rec}
    c.save_profile(have, "standalone")
    return have


def do_broker_external(p, a):
    host, user, pw = plain(a.get("host"), 120), plain(a.get("user"), 120), plain(a.get("pass"), 200)
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        raise ValueError("bad address")
    port = int(a.get("port") or 1883)
    if not 1 <= port <= 65535:
        raise ValueError("bad port")
    p(0)
    if not mqtt_ok(host, port, user, pw):
        raise RuntimeError("The broker did not accept the login")
    p(1)
    c = cli()
    c.save_profile({"MQTT_HOST": host, "MQTT_PORT": str(port), "MQTT_USER": user, "MQTT_PASS": pw, "MQTT_LOCAL": "no",
                    "MQTT_REC_USER": user, "MQTT_REC_PASS": pw}, "lab")
    p(2)
    p(2, "done")
    c.switch_mode("lab")


def mqtt_ok(host, port, user, pw):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return True                                      # nothing to test with: the switch tells
    import threading
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-root-test-{os.getpid()}")
    if user:
        c.username_pw_set(user, pw)
    ev, res = threading.Event(), {"ok": False}

    def on_connect(cl, u, f, rc, props=None):
        res["ok"] = not rc.is_failure
        ev.set()

    c.on_connect = on_connect
    try:
        c.connect(host, port, keepalive=10)
        c.loop_start()
        ev.wait(6)
    except (OSError, ValueError):
        return False
    finally:
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:
            pass
    return res["ok"]


def backup_mod():
    spec = importlib.util.spec_from_file_location("armonico_backup_root", APP_DIR / "device" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def usb_entry(dev, key="dev"):
    """Only a device lsblk reports on USB: never the SD card, whatever the request says."""
    found = [d for d in backup_mod().usb_drives() if d[key] == str(dev)]
    if not found:
        raise ValueError("not a USB storage device")
    if system_disk(found[0]["disk"]):
        raise ValueError("the system's own disk")
    return found


def disk_of(path):
    """The whole disk (as /dev/sdX) under a mounted folder, from its device number in /sys."""
    try:
        st = os.stat(path)
        node = Path(os.path.realpath(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"))
    except OSError:
        return ""
    if (node / "partition").exists():
        node = node.parent
    return f"/dev/{node.name}" if node.name else ""


def system_disk(disk):
    """True for a disk the Pi itself runs from: one holding / or /boot/firmware (a Pi booted from a
    USB SSD has them on USB), or one with anything mounted outside /media/armonico (swap included).
    When lsblk cannot tell, the disk counts as the system's: never erased on a guess."""
    if disk in {disk_of(m) for m in ("/", "/boot/firmware", "/boot")}:
        return True
    r = run("lsblk", "-J", "-o", "PATH,MOUNTPOINTS", disk, timeout=10)
    if r.returncode:
        r = run("lsblk", "-J", "-o", "PATH,MOUNTPOINT", disk, timeout=10)
    try:
        nodes = json.loads(r.stdout).get("blockdevices") or []
    except ValueError:
        return True
    if r.returncode or not nodes:
        return True
    while nodes:
        n = nodes.pop()
        nodes.extend(n.get("children") or [])
        for m in (n.get("mountpoints") or []) + [n.get("mountpoint")]:
            if m and not m.startswith("/media/armonico/"):
                return True
    return False


def device_utc_offset():
    """Minutes from UTC in the device's own time zone (its city, in the screen's settings), now.

    A device whose city was never chosen has none in its settings, and the screen then uses its default
    city, Asia/Jerusalem (DEFAULT_CITY in device/ui/app.py): its recordings were named in that zone. Here
    no zone gave no offset at all, so a FAT stick got the files' times in UTC, 2 or 3 hours early in
    Windows. The same default is used here."""
    try:
        try:
            st = json.loads(read_nofollow(DATA_DIR / "device" / "settings.json"))
        except (OSError, ValueError):
            st = {}
        city = st.get("city") if isinstance(st, dict) else None
        tz = city.get("tz") if isinstance(city, dict) else None
        if not isinstance(tz, str) or not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*", tz):
            tz = DEFAULT_TZ
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return int(datetime.now(ZoneInfo(tz)).utcoffset().total_seconds() // 60)
    except Exception:
        return None


def mount_usb(d):
    if d["mount"]:
        return d["mount"]
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", d["label"] or Path(d["dev"]).name)[:32] or "usb"
    where = Path("/media/armonico") / name
    where.mkdir(parents=True, exist_ok=True)
    opts = []
    if d["fs"] in ("vfat", "exfat", "ntfs"):
        import pwd
        u = pwd.getpwnam("armonico")
        opts = ["-o", f"uid={u.pw_uid},gid={u.pw_gid},umask=0007"]
        if d["fs"] == "vfat":
            # FAT keeps local time with no zone, and Windows reads it as local: written in UTC (the Pi's
            # clock is often on UTC) a file showed hours off. The device's own zone sets the offset.
            off = device_utc_offset()
            if off is not None:
                opts[1] += f",time_offset={off}"
    fstype = ["-t", "ntfs3"] if d["fs"] == "ntfs" else []
    # systemd-mount: the mount is made by systemd itself, so every service sees it, not only this one
    r = run("systemd-mount", "--no-block", "--collect", *fstype, *opts, d["dev"], str(where), timeout=60)
    if r.returncode and d["fs"] == "ntfs":
        r = run("systemd-mount", "--no-block", "--collect", "-t", "ntfs-3g", *opts, d["dev"], str(where), timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "mount failed")
    for _ in range(50):
        if os.path.ismount(where):
            return str(where)
        time.sleep(0.2)
    raise RuntimeError("the stick did not open")


def do_usb_mount(p, a):
    d = usb_entry(a.get("dev", ""))[0]
    if not d["usable"]:
        raise ValueError(f"{d['fs'] or 'no'} file system")
    p(0)
    mount_usb(d)
    p(1)


def do_usb_format(p, a):
    fs = str(a.get("fs", ""))
    if fs not in ("exfat", "ext4"):
        raise ValueError("unknown file system")
    parts = usb_entry(a.get("disk", ""), "disk")
    disk = parts[0]["disk"]
    p(0)
    for d in parts:
        if d["mount"]:
            run("systemd-umount", d["mount"], timeout=60)
            run("umount", d["dev"], timeout=30)
    p(1)
    run("wipefs", "-a", disk, timeout=60)
    r = subprocess.run(["sfdisk", "--label", "dos", disk], input="type=7\n" if fs == "exfat" else "type=83\n",
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "sfdisk failed")
    run("udevadm", "settle", timeout=30)
    part = next((d["dev"] for d in backup_mod().usb_drives() if d["disk"] == disk and d["dev"] != disk), None)
    if not part:
        raise RuntimeError("the new partition did not show up")
    p(2)
    cmd = ["mkfs.exfat", "-L", "ARMONICO", part] if fs == "exfat" else ["mkfs.ext4", "-F", "-L", "ARMONICO", part]
    r = run(*cmd, timeout=600)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "format failed")
    run("udevadm", "settle", timeout=30)
    p(3)
    d = usb_entry(part)[0]
    where = mount_usb(d)
    if fs == "ext4":
        run("chown", "armonico:armonico", where)


def do_set_brightness(p, a):
    """The backlight of every screen in /sys/class/backlight, at a share of its own top: 255 steps on
    one screen, 31 on another. A level above 0 never rounds down to 0 (off)."""
    pct = int(a.get("pct"))
    if not 0 <= pct <= 100:
        raise ValueError("bad level")
    root = Path(os.environ.get("ARMONICO_BACKLIGHT", "/sys/class/backlight"))
    done = 0
    for d in sorted(root.glob("*")):
        try:
            top = int((d / "max_brightness").read_text().strip())
            value = max(1 if pct else 0, round(top * pct / 100))
            (d / "brightness").write_text(f"{value}\n")
            done += 1
        except (OSError, ValueError):
            continue
    if not done:
        raise RuntimeError("no backlight to set")


def do_full_reset(p, args):
    """Everything deleted and installed again (device/full_reset.sh). The script is copied out of the
    program first and runs as its own unit: it deletes the program, this script and the service that
    started it, and has to go on after all of them."""
    p(1)
    src = APP_DIR / "device" / "full_reset.sh"
    # In /run, not /var/tmp: this unit runs with PrivateTmp=yes, so a copy in its /var/tmp is in a
    # folder of its own that the reset's unit never sees, and the reset ended at once, doing nothing.
    dst = Path(FULL_RESET_COPY)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o700)
    subprocess.run(["systemctl", "reset-failed", "armonico-full-reset"], capture_output=True, timeout=10)
    r = run("systemd-run", "--unit=armonico-full-reset", "--collect", "--quiet", "/bin/bash", str(dst), timeout=30)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "the full reset did not start")
    # systemd-run only starts the unit: a reset that ended at once is caught here, not left silent
    time.sleep(2)
    if run("systemctl", "is-active", "--quiet", "armonico-full-reset", timeout=10).returncode:
        raise RuntimeError("the full reset stopped at once: journalctl -u armonico-full-reset")
    p(1, "done")


# ----------------------------------------------------------------------
# Wi-Fi, through NetworkManager's nmcli (Raspberry Pi OS since Bookworm). Every value is checked
# here, nmcli is run with a list of arguments (no shell), and the password goes to nmcli in a file
# only root reads (passwd-file), never on a command line that ps shows. NetworkManager keeps it,
# as a system-owned secret of the connection; Armonico keeps no Wi-Fi password itself.
# ----------------------------------------------------------------------
WIFI_TYPE = "802-11-wireless"
WIFI_WAIT = 20                                   # seconds nmcli waits for a connection to come up
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class WifiError(RuntimeError):
    """A failure the screen says in its own words: wrong-password, password-needed, timeout,
    not-found, unsupported, no-adapter, no-nm, blocked."""


def nmcli(*args, timeout=30):
    env = dict(os.environ, LC_ALL="C", LANG="C")     # nmcli's messages in English, so they can be read here
    try:
        return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise WifiError("no-nm")
    except subprocess.TimeoutExpired:
        raise WifiError("timeout")


def terse_split(line):
    """One line of nmcli -t: fields split at ':', with '\\:' and '\\\\' standing for ':' and '\\'."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def terse_value(s):
    """One value of nmcli -g, its escapes undone."""
    return re.sub(r"\\(.)", r"\1", s.rstrip("\n"))


def wifi_kind(security):
    """psk, sae (WPA3 only), open, or a kind the screen cannot join: eap (802.1X), wep, owe."""
    s = security.upper()
    if not s or s == "--":
        return "open"
    if "802.1X" in s:
        return "eap"
    if "WEP" in s:
        return "wep"
    if "OWE" in s:
        return "owe"
    if "WPA3" in s and "WPA2" not in s and "WPA1" not in s:
        return "sae"
    return "psk"


def parse_wifi_list(text):
    """nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list: one entry for each name, the
    strongest of its access points (a home with 2.4 and 5 GHz, or a mesh, lists the same name a few
    times), connected when any of them is. Networks with no name (hidden) are left out."""
    nets = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        f = terse_split(line)
        if len(f) != 4:
            continue
        use, ssid, sig, sec = f
        if ssid in ("", "--"):
            continue
        try:
            check_ssid(ssid)
        except ValueError:
            continue                             # a name the screen could not send back anyway
        try:
            sig = max(0, min(100, int(sig)))
        except ValueError:
            sig = 0
        sec = "" if sec.strip() in ("", "--") else sec.strip()
        item = {"ssid": ssid, "signal": sig, "secure": bool(sec), "kind": wifi_kind(sec), "active": use.strip() == "*"}
        cur = nets.get(ssid)
        if cur is None:
            nets[ssid] = item
        else:
            active = cur["active"] or item["active"]
            if sig > cur["signal"]:
                nets[ssid] = item
            nets[ssid]["active"] = active
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"], n["ssid"].lower()))


def check_ssid(v):
    """A network name: 1 to 32 bytes in UTF-8, no control characters (no newline)."""
    if not isinstance(v, str):
        raise ValueError("bad network name")
    b = v.encode("utf-8", "strict")
    if not 1 <= len(b) <= 32 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in v):
        raise ValueError("bad network name")
    return v


def check_password(v):
    """A WPA password: 8 to 63 printable ASCII characters, or the 64 hex digits of the key. "" or
    None is no password (an open network, or a saved one)."""
    if v is None or v == "":
        return ""
    if not isinstance(v, str):
        raise ValueError("bad password")
    if re.fullmatch(r"[0-9A-Fa-f]{64}", v):
        return v
    if 8 <= len(v) <= 63 and all(32 <= ord(c) <= 126 for c in v):
        return v
    raise ValueError("bad password")


def check_flag(v):
    if not isinstance(v, bool):
        raise ValueError("bad flag")
    return v


def nm_running():
    if not shutil.which("nmcli"):
        return False
    try:
        r = nmcli("-t", "-f", "RUNNING", "general", timeout=10)
    except WifiError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "running"


def nm_devices():
    """[(device, type, state)] from nmcli -t -f DEVICE,TYPE,STATE device."""
    r = nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device", timeout=10)
    out = []
    for line in r.stdout.splitlines():
        f = terse_split(line)
        if len(f) == 3:
            out.append(tuple(f))
    return out


def wifi_device():
    return next((d for d, t, s in nm_devices() if t == "wifi"), "")


def radio_on():
    """Wi-Fi's radio on, unblocking it first when it is off. A Raspberry Pi flashed with no Wi-Fi
    country starts with Wi-Fi blocked by rfkill, and then every scan comes back empty with no reason.
    False when it stays off (a hard block, or a country still needed)."""
    def on():
        return nmcli("radio", "wifi", timeout=10).stdout.strip() == "enabled"
    if on():
        return True
    if shutil.which("rfkill"):
        subprocess.run(["rfkill", "unblock", "wifi"], capture_output=True, timeout=10)
    nmcli("radio", "wifi", "on", timeout=10)
    time.sleep(2)                                    # the adapter comes back up before it can scan
    return on()


def ipv4_of(dev):
    """The device's IPv4 address, without the prefix length ("" when it has none)."""
    if not dev:
        return ""
    r = nmcli("-g", "IP4.ADDRESS", "device", "show", dev, timeout=10)
    first = terse_value(r.stdout).split("|")[0].strip()
    return first.split("/")[0] if re.fullmatch(r"\d+\.\d+\.\d+\.\d+/\d+", first) else ""


def wifi_saved():
    """The saved Wi-Fi connections: [{uuid, name, ssid, at, active}]; at is the last time used (Unix)."""
    r = nmcli("-t", "-f", "NAME,UUID,TYPE,TIMESTAMP,ACTIVE", "connection", "show", timeout=15)
    out = []
    for line in r.stdout.splitlines():
        f = terse_split(line)
        if len(f) != 5 or f[2] != WIFI_TYPE or not UUID_RE.fullmatch(f[1]):
            continue
        s = nmcli("-g", WIFI_TYPE + ".ssid", "connection", "show", "uuid", f[1], timeout=10)
        try:
            at = int(f[3])
        except ValueError:
            at = 0
        out.append({"uuid": f[1], "name": f[0], "ssid": terse_value(s.stdout), "at": at, "active": f[4] == "yes"})
    return out


def wifi_now():
    """{nm, dev, ssid, ip, wired}: the Wi-Fi connection now, and whether a cable has an address."""
    if not nm_running():
        return {"nm": False}
    devs = nm_devices()
    dev = next((d for d, t, s in devs if t == "wifi"), "")
    wired = any(t == "ethernet" and s.startswith("connected") and ipv4_of(d) for d, t, s in devs)
    st = {"nm": True, "dev": dev, "ssid": "", "ip": "", "wired": wired, "signal": 0, "gw": "", "via": "", "cable_ip": ""}
    if dev and any(d == dev and s.startswith("connected") for d, t, s in devs):
        r = nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no", "ifname", dev)
        cur = [n for n in parse_wifi_list(r.stdout) if n["active"]]
        st["ssid"] = cur[0]["ssid"] if cur else ""
        st["signal"] = int(cur[0].get("signal") or 0) if cur else 0
        st["ip"] = ipv4_of(dev)
    # what carries the traffic now: the default route's device and its gateway (the cable wins when both are up)
    gw_dev, gw = default_route()
    kinds = {d: t for d, t, s in devs}
    st["via"] = kinds.get(gw_dev, "") if kinds.get(gw_dev) in ("ethernet", "wifi") else "ethernet" if wired else ""
    st["gw"] = gw
    if st["via"] == "ethernet":
        st["cable_ip"] = ipv4_of(gw_dev if kinds.get(gw_dev) == "ethernet" else next(
            (d for d, t, s in devs if t == "ethernet" and s.startswith("connected") and ipv4_of(d)), ""))
    return st


def default_route():
    """(device, gateway) of the IPv4 default route, ("", "") when there is none."""
    try:
        out = run("ip", "-4", "route", "show", "default", timeout=5).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return "", ""
    best = None
    for line in out.splitlines():
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+) dev (\S+)(?:.*?metric (\d+))?", line)
        if m and (best is None or int(m.group(3) or 0) < best[0]):
            best = (int(m.group(3) or 0), m.group(2), m.group(1))
    return (best[1], best[2]) if best else ("", "")


def done_with(p, data):
    p(1, "done", json.dumps(data, ensure_ascii=False))


def do_wifi_status(p, a):
    done_with(p, wifi_now())


def do_wifi_scan(p, a):
    """The networks around, with what is saved and the connection now. rescan: a fresh scan, waited
    for (on opening the page); otherwise NetworkManager scans when its list is older than 30 s."""
    rescan = check_flag(a.get("rescan", False))
    st = wifi_now()
    if not st["nm"] or not st["dev"]:
        done_with(p, dict(st, nets=[], saved=[]))
        return
    if not radio_on():
        done_with(p, dict(st, nets=[], saved=wifi_saved(), blocked=True))
        return
    r = nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
              "--rescan", "yes" if rescan else "auto", "ifname", st["dev"], timeout=40)
    saved = wifi_saved()
    known = {c["ssid"] for c in saved}
    nets = parse_wifi_list(r.stdout)
    for n in nets:
        n["known"] = n["ssid"] in known
    done_with(p, dict(st, nets=nets, saved=saved))


def do_wifi_saved(p, a):
    done_with(p, {"nm": nm_running(), "saved": wifi_saved() if nm_running() else []})


def wifi_reason(r):
    """nmcli's failure in a word the screen knows, or its own first line."""
    text = (r.stderr or r.stdout or "").strip()
    if re.search(r"[Ss]ecrets were required|802\.1X supplicant|no-secrets|password", text):
        return "wrong-password"
    if re.search(r"[Tt]imeout", text):
        return "timeout"
    if re.search(r"[Nn]o network with SSID|[Nn]o suitable device|not found|could not be found", text):
        return "not-found"
    return text.splitlines()[0][:160] if text else "nmcli failed"


def do_wifi_connect(p, a):
    ssid = check_ssid(a.get("ssid"))
    pw = check_password(a.get("password"))
    hidden = check_flag(a.get("hidden", False))
    if not nm_running():
        raise WifiError("no-nm")
    dev = wifi_device()
    if not dev:
        raise WifiError("no-adapter")
    if not radio_on():
        raise WifiError("blocked")
    p(0)
    saved = [c for c in wifi_saved() if c["ssid"] == ssid]
    if not pw and saved:
        # a saved network: its own profile, with the password NetworkManager keeps
        r = nmcli("--wait", str(WIFI_WAIT), "connection", "up", "uuid", saved[0]["uuid"], timeout=WIFI_WAIT + 15)
        if r.returncode:
            raise WifiError(wifi_reason(r))
        p(1)
        done_with(p, {"ssid": ssid, "ip": wait_ipv4(dev)})
        return
    # what the network asks for, from NetworkManager's own list (a hidden one is not in it)
    found = [] if hidden else [n for n in parse_wifi_list(nmcli(
        "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no", "ifname", dev).stdout)
        if n["ssid"] == ssid]
    if found and found[0]["kind"] not in ("psk", "sae", "open"):
        raise WifiError("unsupported")
    if not pw and found and found[0]["secure"]:
        raise WifiError("password-needed")
    kind = ("sae" if found and found[0]["kind"] == "sae" else "psk") if pw else "open"
    # a new profile with no secret in it; the password comes with "up" from a file, and
    # NetworkManager saves it in the profile (psk-flags 0: owned by the system, not by an agent)
    cmd = ["connection", "add", "type", "wifi", "con-name", ssid, "ifname", dev, "ssid", ssid,
           "connection.autoconnect", "yes"]
    if hidden:
        cmd += ["wifi.hidden", "yes"]
    if pw:
        cmd += ["wifi-sec.key-mgmt", "sae" if kind == "sae" else "wpa-psk", "wifi-sec.psk-flags", "0"]
    r = nmcli(*cmd, timeout=20)
    # "Connection '<name>' (<uuid>) successfully added.": the UUID is the one in the brackets, read
    # there rather than anywhere in the line, since the name before it is the network's own
    m = re.search(r"\((" + UUID_RE.pattern + r")\) successfully added", r.stdout or "")
    if r.returncode or not m:
        raise WifiError(wifi_reason(r))
    uuid = m.group(1)
    try:
        if pw:
            fd, path = tempfile.mkstemp(prefix="armonico-wifi-")     # 0600, root's, in this unit's private /tmp
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(f"802-11-wireless-security.psk:{pw}\n")
                r = nmcli("--wait", str(WIFI_WAIT), "connection", "up", "uuid", uuid, "passwd-file", path,
                          timeout=WIFI_WAIT + 15)
            finally:
                os.unlink(path)
        else:
            r = nmcli("--wait", str(WIFI_WAIT), "connection", "up", "uuid", uuid, timeout=WIFI_WAIT + 15)
    except BaseException:
        nmcli("connection", "delete", "uuid", uuid, timeout=15)
        raise
    if r.returncode:
        # nothing kept of a try that failed: a wrong password is not saved and retried by itself
        nmcli("connection", "delete", "uuid", uuid, timeout=15)
        reason = wifi_reason(r)
        raise WifiError("password-needed" if not pw and reason == "wrong-password" else reason)
    # the new password works: older profiles of the same network (with an old password) go
    for c in saved:
        nmcli("connection", "delete", "uuid", c["uuid"], timeout=15)
    p(1)
    done_with(p, {"ssid": ssid, "ip": wait_ipv4(dev)})


def wait_ipv4(dev, seconds=6):
    end = time.monotonic() + seconds
    while True:
        ip = ipv4_of(dev)
        if ip or time.monotonic() > end:
            return ip
        time.sleep(0.5)


def do_wifi_forget(p, a):
    """One saved Wi-Fi connection deleted, by its UUID, only when it is one of the saved Wi-Fi ones."""
    uuid = str(a.get("uuid", ""))
    if not UUID_RE.fullmatch(uuid):
        raise ValueError("bad connection")
    if not nm_running():
        raise WifiError("no-nm")
    if uuid not in {c["uuid"] for c in wifi_saved()}:
        raise ValueError("not a saved Wi-Fi connection")
    r = nmcli("connection", "delete", "uuid", uuid, timeout=20)
    if r.returncode:
        raise WifiError(wifi_reason(r))
    done_with(p, {"forgot": uuid})


# ---------------------------------------------------------------------- the Telegram bot's token
# Kept where the installer keeps it, in a file only root reads (/etc/armonico/secrets.env), never in
# the screen's settings (the service user's) and never handed back to the screen: the screen asks
# root to send its fixed test message, root reads the token and sends it.
SECRETS = Path(os.environ.get("ARMONICO_SECRETS", "/etc/armonico/secrets.env"))
TG_TOKEN_RE = re.compile(r"[0-9]{5,15}:[A-Za-z0-9_-]{30,64}")
TG_TEST_TEXT = "Armonico Pi is connected"
TG_GAP = 10                                     # seconds between two test messages, at least
TG_STAMP = Path(os.environ.get("ARMONICO_RUN", "/run")) / "armonico-telegram-test"   # root's, gone at boot


def secret_read(key):
    """One value from the secrets file, read as text (KEY='value'), never run."""
    try:
        text = SECRETS.read_text()
    except OSError:
        return ""
    got = ""
    for line in text.splitlines():
        m = re.fullmatch(re.escape(key) + r"='([^'\n]*)'", line.strip())
        if m:
            got = m.group(1)
    return got


def secret_write(key, value):
    """Sets or (value None) removes one line of the secrets file: written whole, 0600, root's."""
    try:
        lines = [l for l in SECRETS.read_text().splitlines() if not l.startswith(key + "=")]
    except OSError:
        lines = []
    if value is not None:
        lines.append(f"{key}='{value}'")
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(SECRETS.parent), prefix=".secrets.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(l + "\n" for l in lines))
        os.replace(tmp, SECRETS)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_tg_token(value):
    token = str(value or "").strip()
    if not TG_TOKEN_RE.fullmatch(token):
        raise ValueError("bad bot token")
    return token


def tg_call(token, method, data=None):
    sys.path.insert(0, str(APP_DIR / "setup"))
    import telegram_setup
    telegram_setup.QUIET = True
    return telegram_setup.tg(token, method, data, timeout=20)


def tg_chats():
    """The chats the device's own Telegram setup found, from its settings: numbers only."""
    try:
        data = json.loads(read_nofollow(DATA_DIR / "device" / "settings.json"))
    except (OSError, ValueError):
        return []
    out = []
    for c in data.get("tg_chats") or []:
        if isinstance(c, int) and not isinstance(c, bool):
            out.append(c)
        elif isinstance(c, str) and re.fullmatch(r"-?[0-9]{1,20}", c):
            out.append(int(c))
    return out[:10]


def do_telegram_keep(p, a):
    """The bot's token, after the device's Telegram setup worked: checked with Telegram, then kept."""
    token = check_tg_token(a.get("token"))
    try:
        tg_call(token, "getMe")
    except Exception:
        raise ValueError("Telegram did not accept the token")
    secret_write("TELEGRAM_TOKEN", token)
    secret_write("TELEGRAM_ON_DEVICE", "1")      # the installer's "no" to keeping tokens leaves it alone
    done_with(p, {"kept": True})


def do_telegram_test(p, a):
    """The fixed test message to the device's chats, with the kept token. Nothing comes from the screen."""
    # each request is a new process: the time of the last message is kept in a file of root's
    try:
        if time.time() - TG_STAMP.stat().st_mtime < TG_GAP:
            raise ValueError("too soon")
    except FileNotFoundError:
        pass
    token = secret_read("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("no-token")
    chats = tg_chats()
    if not chats:
        raise ValueError("no-chats")
    TG_STAMP.touch()
    sent = 0
    for cid in chats:
        try:
            tg_call(token, "sendMessage", {"chat_id": cid, "text": TG_TEST_TEXT})
            sent += 1
        except Exception:
            pass
    if not sent:
        raise ValueError("Telegram did not take the message")
    done_with(p, {"sent": sent})


def do_telegram_kept(p, a):
    """Only whether a token is kept, for the full check: the token itself never leaves this process."""
    done_with(p, {"kept": bool(secret_read("TELEGRAM_TOKEN"))})


TG_REPORT_STAMP = Path(os.environ.get("ARMONICO_RUN", "/run")) / "armonico-telegram-report"
TG_REPORT_LINES = 12
ADDRESS_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b|\[[0-9a-fA-F:]+\](?::\d+)?|\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}\b")


def texts():
    spec = importlib.util.spec_from_file_location("armonico_texts_root", APP_DIR / "device" / "ui" / "texts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def report_count(v):
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 999:
        raise ValueError("bad count")
    return v


def report_line(v):
    """One line of the summary, as the screen wrote it: plain text, short, no address and nothing that
    looks like a token."""
    text = str(v or "")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("unsupported character")
    text = TG_TOKEN_RE.sub("…", ADDRESS_RE.sub("…", text)).strip()
    return text[:160]


def do_telegram_report(p, a):
    """The full check's summary to the device's chats, with the kept token. The screen sends counts and
    short lines; the message's form is fixed here: a heading with the date, the counts, the lines."""
    try:
        if time.time() - TG_REPORT_STAMP.stat().st_mtime < TG_GAP:
            raise ValueError("too soon")
    except FileNotFoundError:
        pass
    lang = a.get("lang") if a.get("lang") in ("en", "he") else "en"
    n = [report_count(a.get(k, 0)) for k in ("problems", "warnings", "passed", "skipped")]
    lines = a.get("lines") or []
    if not isinstance(lines, list) or len(lines) > TG_REPORT_LINES:
        raise ValueError("bad lines")
    body = []
    for item in lines:
        if not isinstance(item, dict) or item.get("level") not in ("fail", "warn"):
            raise ValueError("bad lines")
        body.append(("✗ " if item["level"] == "fail" else "! ") + report_line(item.get("text")))
    rep = str(a.get("report") or "")
    if rep and not re.fullmatch(r"\d{4}-\d\d-\d\d-\d{4}\.txt", rep):
        raise ValueError("bad report name")
    token = secret_read("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("no-token")
    chats = tg_chats()
    if not chats:
        raise ValueError("no-chats")
    t = texts()
    text = "\n".join([t.tr(lang, "chk.tgHead", d=time.strftime("%Y-%m-%d %H:%M")), t.check_counts(lang, *n)] + body +
                     ([t.tr(lang, "chk.tgSaved", f=f"{DATA_DIR}/device/checks/{rep}")] if rep else []))
    TG_REPORT_STAMP.touch()
    sent = 0
    for cid in chats:
        try:
            tg_call(token, "sendMessage", {"chat_id": cid, "text": text})
            sent += 1
        except Exception:
            pass
    if not sent:
        raise ValueError("Telegram did not take the message")
    done_with(p, {"sent": sent})


def do_telegram_forget(p, a):
    secret_write("TELEGRAM_TOKEN", None)
    secret_write("TELEGRAM_ON_DEVICE", None)
    done_with(p, {"kept": False})


# ---------------------------------------------------------------------- Home Assistant's token
# Kept like the bot's token: in root's secrets file, under keys of its own (HA_DEVICE_TOKEN,
# HA_DEVICE_URL: the installer's HA_TOKEN and HA_URL are its own and it may withdraw them), checked
# with Home Assistant first, and never handed back to the screen.
HA_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{20,4096}")


def ha_module():
    sys.path.insert(0, str(APP_DIR / "setup"))
    import ha_setup
    return ha_setup


def check_ha_token(value):
    token = str(value or "").strip()
    if not HA_TOKEN_RE.fullmatch(token):
        raise ValueError("bad Home Assistant token")
    return token


def check_ha_url(value):
    url = str(value or "").strip()
    if not re.fullmatch(r"https?://[A-Za-z0-9._:\[\]-]{1,200}(?:/[A-Za-z0-9._~/-]{0,200})?", url):
        raise ValueError("bad Home Assistant address")
    return url


def ha_open(url, token):
    """A Home Assistant client that answered, from the address as given (tried as the setup tries it)."""
    mod = ha_module()
    for cand in mod.candidates(url):
        ha = mod.HA(cand, token)
        try:
            ha.rest("GET", "/api/config")
            return ha, cand
        except mod.HAError:
            continue
    return None, None


def do_ha_keep(p, a):
    """The access token, after the device's Home Assistant setup worked: checked with Home Assistant, then kept."""
    token, url = check_ha_token(a.get("token")), check_ha_url(a.get("url"))
    ha, found = ha_open(url, token)
    if ha is None:
        raise ValueError("Home Assistant did not accept the token")
    secret_write("HA_DEVICE_TOKEN", token)
    secret_write("HA_DEVICE_URL", found)
    done_with(p, {"kept": True})


def do_ha_kept(p, a):
    done_with(p, {"kept": bool(secret_read("HA_DEVICE_TOKEN"))})


def do_ha_forget(p, a):
    secret_write("HA_DEVICE_TOKEN", None)
    secret_write("HA_DEVICE_URL", None)
    done_with(p, {"kept": False})


def do_ha_telegram_connect(p, a):
    """The bot connected to Home Assistant with the kept token: the screen sends the bot's token and the
    chats it found, the Home Assistant token is read here and never leaves this process."""
    tg_token = check_tg_token(a.get("token"))
    chats = a.get("chats")
    if (not isinstance(chats, list) or not 1 <= len(chats) <= 10 or
            not all(isinstance(c, int) and not isinstance(c, bool) for c in chats)):
        raise ValueError("bad chats")
    token, url = secret_read("HA_DEVICE_TOKEN"), secret_read("HA_DEVICE_URL")
    if not token or not url:
        raise ValueError("no-token")
    ha, _ = ha_open(url, token)
    if ha is None:
        raise ValueError("Home Assistant did not answer")
    done, info = ha_module().telegram_connect(ha, tg_token, chats)
    if not done:
        raise ValueError(str(info))
    done_with(p, {"connected": True})


# ---------------------------------------------------------------------- armonico connect, over SSH
# The tokens are too long to type on the device, so they are entered in a terminal: install.sh
# --connect runs the install's own Home Assistant, broker and Telegram steps there, then tells the
# screen what came out through connect_mark, and the screen goes on from it after its restart.
CONNECT_KEYS = {"ha_done", "ha_kept", "tg_done", "tg_bot", "tg_chats", "tg_kept"}
SETUP_STEPS_LEFT = ("ssh", "broker", "ha", "tg")


def connect_mark(changes, advance=True):
    """Writes what armonico connect set up into the screen's settings, under the screen's own lock
    (device/screen.py save_settings) and never through a link. advance: a setup in progress on one of
    the connection's steps goes on to the step after them."""
    import fcntl
    changes = {k: v for k, v in changes.items() if k in CONNECT_KEYS}
    if "tg_chats" in changes:
        changes["tg_chats"] = [int(c) for c in changes["tg_chats"] or []
                               if isinstance(c, int) and not isinstance(c, bool)][:10]
    if "tg_bot" in changes:
        changes["tg_bot"] = re.sub(r"[^A-Za-z0-9_]", "", str(changes["tg_bot"] or ""))[:64]
    dev = DATA_DIR / "device"
    dfd = os.open(dev, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        owner = os.fstat(dfd)
        lfd = os.open("settings.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        if os.fstat(lfd).st_uid != owner.st_uid:
            os.fchown(lfd, owner.st_uid, owner.st_gid)    # made here: the screen opens it too
        fcntl.flock(lfd, fcntl.LOCK_EX)
        try:
            data = json.loads(read_nofollow(dev / "settings.json"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.update(changes)
        if advance and (data.get("setup_step") in SETUP_STEPS_LEFT or not data.get("setup_done")):
            data["setup_step"] = "look"
        data["setup_manual"] = False
        safe_write(dev, "settings.json", json.dumps(data, indent=1), mode=0o644, owned=True)
    finally:
        os.close(lfd)


def do_ssh_on(p, a):
    """The SSH server on, so armonico connect can be run from a computer or a phone."""
    if not shutil.which("sshd") and not Path("/usr/sbin/sshd").exists():
        before = installed_packages()
        r = run("apt-get", "install", "-y", "-qq", "--no-install-recommends", "openssh-server", timeout=900)
        record_packages(before)
        if r.returncode:
            raise RuntimeError("The SSH server did not install (no network?)")
    p(1)
    r = run("systemctl", "enable", "--now", "ssh", timeout=60)
    if r.returncode:
        raise RuntimeError("SSH did not start: journalctl -u ssh")
    done_with(p, {"on": True})


def screen_rec():
    spec = importlib.util.spec_from_file_location("armonico_screen_rec", APP_DIR / "device" / "screen_rec.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def do_rec_export(p, a):
    files = a.get("files")
    if files is not None and (not isinstance(files, list) or not all(isinstance(f, str) for f in files)):
        raise ValueError("unsupported character")
    n, where = screen_rec().export(files or None)
    done_with(p, {"moved": n, "to": where})



# ----------------------------------------------------------------------
# Bluetooth: sound devices through bluetoothctl (BlueZ), run as root so the screen's user needs no D-Bus rights
# ----------------------------------------------------------------------
BT_MAC = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
BT_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02\r]")
BT_PROMPT = re.compile(r"\[[^\]\n]*\]# ")
BT_MAX = 40                  # devices listed in one answer (as many as a scan looks at)
BT_SEEN = 40                 # devices looked at in one scan (an address-only one costs one quick `info`)
BT_KIND_ORDER = {"audio": 0, "unknown": 1, "other": 2}
BT_SCAN = (4, 20)            # seconds a scan may last


def check_mac(v):
    """An address as bluetoothctl writes it, capitals: the only thing from the screen that reaches it."""
    m = str(v or "").strip().upper()
    if not BT_MAC.match(m):
        raise ValueError("bad address")
    return m


def bt_clean(text):
    return BT_PROMPT.sub("", BT_ANSI.sub("", text))


class BtCtl:
    """One bluetoothctl on pipes. Its commands go to its input, one line each, and what it prints is read
    back until a pattern shows or the time is up (bluetoothctl keeps no state between runs that a script
    could count on: pairing needs its agent registered in the same session)."""

    def __init__(self):
        # A terminal of its own (pty), not pipes: on pipes bluetoothctl's output is held back in a block buffer
        # and, in some BlueZ versions, its input is not read at all, so nothing came back from a scan.
        # Through a terminal it is line by line, as it is in a shell. Wide, so no line is wrapped.
        self.master = None
        try:
            import fcntl
            import pty
            import struct
            import termios
            master, slave = pty.openpty()
            try:
                attrs = termios.tcgetattr(slave)
                attrs[3] &= ~termios.ECHO
                termios.tcsetattr(slave, termios.TCSANOW, attrs)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 500, 0, 0))
                self.proc = subprocess.Popen(["bluetoothctl"], stdin=slave, stdout=slave, stderr=slave,
                                             close_fds=True, start_new_session=True)
            except Exception:
                os.close(master)
                raise
            finally:
                os.close(slave)
            self.master = master
            self.fd = master
        except Exception:                                   # no terminal to be had: pipes, as before
            self.master = None
            self.proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
            self.fd = self.proc.stdout.fileno()
        os.set_blocking(self.fd, False)
        self.buf = ""
        self.ignore_cancel = False                          # set while an undo runs, which must not be cut short

    def send(self, cmd):
        data = (cmd + "\n").encode()
        if self.master is not None:
            os.write(self.master, data)
        else:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def settle(self, quiet=0.06, cap=0.4):
        """Whatever else is already on its way, taken in: reads until `quiet` seconds pass with nothing new (at most
        `cap`). A pattern can show in the first lines of an answer, and the lines after it come in a moment later."""
        end = time.monotonic() + cap
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], quiet)
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buf += bt_clean(chunk.decode("utf-8", "replace"))

    def read(self, seconds, stop=None, each=None, settle=False):
        """What came in during the next seconds, ending early when the pattern stop shows in it (with settle,
        after the rest of that answer has come in). Cancelled when the screen calls the action off."""
        end = time.monotonic() + seconds
        start = len(self.buf)
        while time.monotonic() < end:
            if not self.ignore_cancel and cancel_asked():
                raise Cancelled("cancelled")
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except BlockingIOError:
                    chunk = b"\0"
                except OSError:
                    chunk = b""                              # a terminal's end of file is an error
                if not chunk:
                    break                                    # bluetoothctl ended
                if chunk != b"\0":
                    self.buf += bt_clean(chunk.decode("utf-8", "replace"))
            seg = self.buf[start:]
            if each:
                each(seg)
            if stop and re.search(stop, seg, re.I):
                if settle:
                    self.settle()
                break
        return self.buf[start:]

    def close(self):
        try:
            self.send("quit")
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass


def bt_devices(text):
    """{address: name} from `devices` or a scan's [NEW] lines. Lines with [CHG] are changes, not names."""
    out = {}
    for m in re.finditer(r"^(?:\[NEW\] )?Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.+)$", text, re.M | re.I):
        out[m.group(1).upper()] = m.group(2).strip()
    return out


def bt_info(text):
    """The fields of `info` that matter: name, paired, trusted, connected, and whether it plays sound."""
    f = {}
    for m in re.finditer(r"^\s*(Name|Alias|Paired|Trusted|Connected|Icon): (.*)$", text, re.M):
        f.setdefault(m.group(1), m.group(2).strip())
    name = f.get("Alias") or f.get("Name") or ""
    unnamed = bool(re.fullmatch(r"(?:[0-9A-F]{2}-){5}[0-9A-F]{2}", name, re.I))
    audio = bool(re.search(r"^\s*Icon: audio-", text, re.M) or re.search(r"UUID: Audio Sink", text))
    return {"name": "" if unnamed else name[:60], "paired": f.get("Paired") == "yes", "trusted": f.get("Trusted") == "yes",
            "connected": f.get("Connected") == "yes", "audio": audio, "known": "not available" not in text,
            "icon": f.get("Icon", "")}


def bt_kind(info):
    """"audio" when BlueZ says it plays sound, "other" when its icon says it is something else (a phone, a
    computer, a watch), "unknown" when there is no icon: a headset seen only through its LE advertisement."""
    if info.get("audio"):
        return "audio"
    return "other" if info.get("icon") else "unknown"


def bt_reason(text):
    """bluetoothctl's failure in one of the words the screen knows."""
    t = text or ""
    if re.search(r"AlreadyConnected|Already Connected", t, re.I):
        return ""
    if re.search(r"Authentication(Failed|Rejected|Canceled|Timeout)|auth", t, re.I):
        return "auth"
    if re.search(r"Blocked|rfkill", t, re.I):
        return "blocked"
    if re.search(r"^cancelled$", t.strip(), re.I):
        return "cancelled"
    if re.search(r"No default controller", t, re.I):
        return "no-adapter"
    if re.search(r"InProgress", t, re.I):
        return "busy"
    if re.search(r"page-timeout|Page Timeout|not available|Host is down|ConnectionAttemptFailed|abort-by", t, re.I):
        return "unreachable"
    if re.search(r"profile-unavailable|NotSupported", t, re.I):
        return "profile"
    return "failed"


def bt_open():
    if not shutil.which("bluetoothctl"):
        raise RuntimeError("no-bluez")
    try:
        c = BtCtl()
    except OSError:
        raise RuntimeError("no-bluez")
    # bluetoothctl answers "No default controller available" to a command sent before it has heard of the
    # adapter from bluetoothd, a moment after it starts: it is waited for here, up to 3 seconds
    c.read(BT_READY_WAIT, r"Controller (?:[0-9A-F]{2}:){5}[0-9A-F]{2}")
    return c


def bt_hci_present():
    """Whether the kernel has a Bluetooth adapter at all (hci0 ...), whatever bluetoothd says."""
    try:
        return any(Path("/sys/class/bluetooth").glob("hci*"))
    except OSError:
        return False


BT_SERDEV = Path("/sys/bus/serial/drivers/hci_uart_bcm")


def bt_rebind():
    """The built-in radio of a Pi 4 (CYW43455 on the UART, attached by the kernel, not by hciuart) started
    again from nothing: its driver let go and taken back, which powers the chip down and up and loads its
    firmware again, as at boot. A chip that stopped answering (dmesg: Opcode 0x0c03 failed: -110, the reset
    timing out) is back after it. True when there was a chip to do it on."""
    try:
        devs = [d.name for d in BT_SERDEV.iterdir() if d.name.startswith("serial")]
    except OSError:
        return False
    for d in devs:
        try:
            (BT_SERDEV / "unbind").write_text(d)
        except OSError:
            pass
        nap(0.5)
        try:
            (BT_SERDEV / "bind").write_text(d)
        except OSError:
            pass
    return bool(devs)


def bt_wake():
    """The adapter brought back when bluetoothd has none: the chip started again (bt_rebind), or on an older
    system hciuart (pi-bluetooth); then bluetoothd, and the adapter waited for."""
    if bt_rebind():
        end = time.monotonic() + 8
        while time.monotonic() < end and not bt_hci_present():
            nap(0.5)
    for unit in ("hciuart", "bluetooth"):
        try:
            if run("systemctl", "cat", unit, timeout=5).returncode == 0:
                run("systemctl", "restart", unit, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
    end = time.monotonic() + 8
    while time.monotonic() < end and not bt_hci_present():
        nap(0.5)
    nap(1)                                                 # bluetoothd takes the adapter up a moment after


def bt_ask(c, cmd, wait, stop):
    """cmd, and its answer. "No default controller" is believed only when it holds: asked again after a
    moment when the kernel has an adapter, and if that does not help, after bt_wake starts the chip again."""
    c.send(cmd)
    t = c.read(wait, stop + r"|No default controller")
    if not re.search(r"No default controller", t, re.I):
        return t, c
    if bt_hci_present():
        nap(1.5)                                       # most often bluetoothd was a moment late
        c.send(cmd)
        t = c.read(wait, stop + r"|No default controller")
        if not re.search(r"No default controller", t, re.I):
            return t, c
    # the chip itself stopped answering, or is gone: started again, once
    bt_wake()
    c.read(5, r"Controller (?:[0-9A-F]{2}:){5}[0-9A-F]{2}")       # bluetoothctl finds bluetoothd again by itself
    c.send(cmd)
    return c.read(wait, stop + r"|No default controller"), c


def bt_power(c):
    """The adapter on. RuntimeError no-adapter when there is none (after bt_ask's second look), blocked when
    rfkill holds it. Returns the bluetoothctl to go on with."""
    t, c = bt_ask(c, "power on", 4, r"power on succeeded|Failed to set power")
    if re.search(r"No default controller", t, re.I):
        raise RuntimeError("no-adapter")
    if re.search(r"Failed to set power", t, re.I):
        if shutil.which("rfkill"):
            run("rfkill", "unblock", "bluetooth", timeout=5)
        c.send("power on")
        t = c.read(4, r"power on succeeded|Failed to set power")
        if re.search(r"Failed to set power", t, re.I):
            # rfkill does not hold it, and it still does not come on: the chip itself stopped answering (dmesg:
            # "hci0: hardware error", "Opcode 0x0c03 failed: -110"), which only a restart of the Pi brings back
            raise RuntimeError("blocked" if radio_blocked("bluetooth") else "stuck")
    return c


def bt_look(c, mac):
    c.send(f"info {mac}")
    # Connected: comes before the UUID lines in BlueZ's answer (and, in a stand-in, before the icon): the rest of
    # the answer is taken in before it is read, or a device that plays sound could look like something else
    t = c.read(3, r"Connected: (yes|no)|not available", settle=True)
    return bt_info(t)


def bt_saved(c, text):
    """The paired or trusted devices among `devices`' lines, each looked at."""
    out = []
    for mac, name in list(bt_devices(text).items())[:BT_MAX]:
        i = bt_look(c, mac)
        if i["paired"] or i["trusted"] or i["connected"]:
            out.append({"mac": mac, "name": i["name"] or name[:60], "connected": i["connected"], "audio": i["audio"]})
    return out


def do_bt_status(p, a):
    try:
        c = bt_open()
    except RuntimeError as e:
        done_with(p, {"hw": False, "why": str(e), "saved": []})
        return
    try:
        t, c = bt_ask(c, "show", 3, r"Powered: (yes|no)")
        if re.search(r"No default controller", t, re.I):
            done_with(p, {"hw": False, "why": "no-adapter", "saved": []})
            return
        powered = bool(re.search(r"Powered: yes", t))
        if not powered and not a.get("look") and radio_blocked("bluetooth") is False:   # look: the check, which changes nothing
            # not held off by the radio switch, and still not on: turned on here, and a chip that no longer answers
            # is said as such instead of looking switched off
            try:
                c = bt_power(c)
                powered = True
            except RuntimeError as e:
                if str(e) == "stuck":
                    done_with(p, {"hw": False, "why": "stuck", "saved": []})
                    return
        c.send("devices")
        d = c.read(2)
        done_with(p, {"hw": True, "powered": powered, "saved": bt_saved(c, d)})
    finally:
        c.close()


def do_bt_scan(p, a):
    """Devices that play sound, nearby and not paired yet, found in a few seconds (discovery on, then off)."""
    secs = a.get("seconds", 10)
    if not isinstance(secs, int) or isinstance(secs, bool) or not BT_SCAN[0] <= secs <= BT_SCAN[1]:
        raise ValueError("bad time")
    try:
        c = bt_open()
        c = bt_power(c)
    except RuntimeError as e:
        done_with(p, {"hw": False, "why": str(e), "saved": [], "found": []})
        return
    try:
        c.send("scan on")
        try:
            seen = c.read(secs)
        except Cancelled:
            seen = ""                                       # Stop: what was heard so far is listed, nothing more waited for
            c.ignore_cancel = True
        c.ignore_cancel = True if cancel_asked() else c.ignore_cancel
        c.send("scan off")
        c.read(1, r"Discovery stopped")
        c.send("devices")
        d = c.read(2)
        look = lambda mac: bt_look(c, mac)
        if not bt_devices(seen + "\n" + d):
            # Nothing came back from the session at all: the same scan once more the way BlueZ documents for
            # scripts (bluetoothctl --timeout N scan on, then devices and info as one-off commands)
            try:
                out = run("bluetoothctl", "--timeout", str(secs), "scan", "on", timeout=secs + 15).stdout or ""
                d = bt_clean(out) + "\n" + (bt_clean(run("bluetoothctl", "devices", timeout=10).stdout or ""))
            except (OSError, subprocess.SubprocessError):
                d = ""

            def look(mac):
                try:
                    return bt_info(bt_clean(run("bluetoothctl", "info", mac, timeout=10).stdout or ""))
                except (OSError, subprocess.SubprocessError):
                    return bt_info("")
        saved, found = [], []
        seen = bt_devices(d)
        for mac, name in list(seen.items())[:BT_SEEN]:
            i = look(mac)
            if i["paired"] or i["trusted"] or i["connected"]:
                saved.append({"mac": mac, "name": i["name"] or name[:60], "connected": i["connected"], "audio": i["audio"]})
            elif i["name"]:
                found.append({"mac": mac, "name": i["name"], "kind": bt_kind(i)})
            else:
                # no name (most of what is in the air: LE advertisements with a random address): kept, the
                # screen puts them in one folded row, or lists one that says it plays sound
                found.append({"mac": mac, "name": "", "kind": bt_kind(i)})
        named = sum(1 for x in found if x["name"]) + len(saved)
        # sound devices first, then the ones whose kind BlueZ does not say (a headset that advertises on LE
        # alone has no class yet), then the rest, which are listed but cannot be paired as sound; a device
        # without a name goes after the named ones of its kind, and one that is not sound at the very end
        found.sort(key=lambda x: (BT_KIND_ORDER[x["kind"]] + (3 if not x["name"] and x["kind"] != "audio" else 0),
                                  not x["name"]))
        done_with(p, {"hw": True, "powered": True, "saved": saved, "found": found[:BT_MAX],
                      "seen": len(seen), "named": named})
    finally:
        c.close()


def bt_connect_step(c, mac):
    c.send(f"connect {mac}")
    # "not available": BlueZ does not know the address (forgotten, or not seen for a long time): an answer at once,
    # not the full wait, so a retry of a device that is gone does not hold the root actions up
    t = c.read(BT_CONNECT_STEP_WAIT, r"Connection successful|Failed to connect|not available")
    if re.search(r"Connection successful|AlreadyConnected", t, re.I):
        return ""
    if re.search(r"not available", t, re.I):
        return "unreachable"
    if not re.search(r"Failed to connect", t, re.I):
        c.send(f"disconnect {mac}")                           # no answer in time: the attempt is dropped, not left hanging
        return "timeout"
    return bt_reason(t) or "failed"


BT_READY_WAIT = 3            # seconds a new bluetoothctl is given to hear of the adapter (bt_open)
BT_PAIR_WAIT = 20            # seconds the pairing itself is given; then it is called off
BT_CONNECT_WAIT = 10         # and the first connection after it
BT_CONNECT_STEP_WAIT = 12    # and a connection to a saved device


def bt_release(c, mac, forget=True):
    """A pairing that did not finish, undone: called off, disconnected and forgotten, so the device is free
    again for a phone or anything else (a half-paired speaker can refuse everyone until it is reset).
    forget=False for a device that was paired before this attempt: only the attempt is called off, and its
    pairing, made earlier and working, stays."""
    c.ignore_cancel = True
    cmds = ((f"cancel-pairing {mac}", 1), (f"disconnect {mac}", 2), (f"remove {mac}", 2)) if forget \
        else ((f"cancel-pairing {mac}", 1),)
    for cmd, wait in cmds:
        try:
            c.send(cmd)
            c.read(wait, r"succe|Failed|not available|Device has been removed|Successful")
        except OSError:
            pass


def do_bt_pair(p, a):
    """A new device: found, paired, trusted (so it comes back by itself when it is switched on) and connected.
    The agent answers what the device asks for: yes to a passkey to confirm or a service to allow, 0000 to a
    PIN code (an older headset). With nothing new from the device for BT_PAIR_WAIT seconds, or the pairing
    failing, it is called off and the device forgotten (bt_release), so it is not left half-paired. Once the
    pairing succeeded it stays: a first connection that does not finish is answered as connected False."""
    mac = check_mac(a.get("mac"))
    c = bt_open()
    done = False
    was_paired = False            # paired before this attempt: a failure here never removes it
    try:
        c = bt_power(c)
        p(1)
        before = bt_look(c, mac)
        was_paired = before["paired"]
        if not before["name"]:                         # BlueZ forgets a device that was not seen for a while
            c.send("scan on")
            c.read(8, re.escape(mac))
            c.send("scan off")
            c.read(1, r"Discovery stopped")
        # bluetoothctl registers its own agent as it starts: "agent NoInputNoOutput" then only answers
        # "already registered", and default-agent makes that one the agent. Both lines are read to their answer.
        c.send("agent NoInputNoOutput")
        c.read(1, r"Agent registered|already (exists|registered)|Failed")
        c.send("default-agent")
        c.read(1, r"Default agent request successful|Failed")
        # trusted before the pairing (BlueZ keeps Trusted on any device it knows), so a headset that opens its
        # sound itself right after pairing is let in without a question, and it comes back by itself later
        c.send(f"trust {mac}")
        c.read(2, r"trust succeeded|Failed to trust|not available")
        c.send(f"pair {mac}")
        answered = [0]

        def each(seg):
            # each question of the agent once, in order: a PIN code gets 0000, a yes/no (a passkey to
            # confirm, a service to allow) gets yes
            asks = re.findall(r"Enter PIN code|Enter passkey|\(yes/no\)", seg, re.I)
            for ask in asks[answered[0]:]:
                c.send("0000" if ask.lower().startswith("enter") else "yes")
            answered[0] = len(asks)
        t = c.read(BT_PAIR_WAIT, r"Pairing successful|Failed to pair|AlreadyExists|Already Exists", each)
        if not re.search(r"Pairing successful|AlreadyExists|Already Exists", t, re.I):
            raise RuntimeError(bt_reason(t) if re.search(r"Failed to pair", t, re.I) else "timeout")
        # paired now: from here on a failure keeps the pairing (the device is in the saved list and connects from
        # there, or by itself); only a pairing that never finished is undone
        was_paired = True
        p(2)
        answered[0] = 0                                # a question asked during the connection is answered too
        c.send(f"connect {mac}")
        t = c.read(BT_CONNECT_WAIT, r"Connection successful|Failed to connect", each)
        i = bt_look(c, mac)
        if not i["trusted"]:                           # the trust before pairing did not hold: once more
            c.send(f"trust {mac}")
            c.read(2, r"trust succeeded|Failed to trust|not available", each)
        connected = i["connected"] or bool(re.search(r"Connection successful|AlreadyConnected", t, re.I))
        done = True
        done_with(p, {"mac": mac, "name": i["name"], "connected": connected})
    finally:
        if not done:
            bt_release(c, mac, forget=not was_paired)
        c.close()


def do_bt_connect(p, a):
    mac = check_mac(a.get("mac"))
    c = bt_open()
    try:
        c = bt_power(c)
        why = bt_connect_step(c, mac)
        if why:
            raise RuntimeError(why)
        c.send(f"trust {mac}")                              # a device the screen connected comes back by itself again
        c.read(1, r"trust succeeded|Failed to trust|not available")
        done_with(p, {"mac": mac, "connected": True})
    finally:
        c.close()


def do_bt_reconnect(p, a):
    """A device that connected at boot before the sound server was there: bluetoothd starts long before the screen's
    PipeWire, a trusted headset calls back at once, and its sound profile (A2DP) was not set up, so it showed as
    connected and stayed silent. Here the device is asked to connect once more while it is connected: BlueZ then
    connects the profiles that are missing (A2DP) and leaves the link as it is. The link is never dropped first:
    a disconnect right after a headset set up its sound took the Pi's Bluetooth chip down (dmesg: "hci0: hardware
    error 0x00", then "Opcode 0x0c03 failed: -110"), and it stayed off until a restart."""
    mac = check_mac(a.get("mac"))
    c = bt_open()
    try:
        c = bt_power(c)
        why = bt_connect_step(c, mac)                       # all there already: AlreadyConnected, nothing done
        if why:
            raise RuntimeError(why)
        done_with(p, {"mac": mac, "connected": True})
    finally:
        c.close()


def bt_still_connected(c, mac):
    return bt_look(c, mac)["connected"]


def do_bt_disconnect(p, a):
    """A real disconnect: the link dropped and checked, not only asked for. The device is then untrusted, so a pair of
    headphones that are still switched on does not call back at once (BlueZ takes a connection from a trusted device
    without asking); the next connect from the screen trusts it again. A link that will not drop after two tries is
    removed (forgotten), which always ends it; the device then has to be paired again."""
    mac = check_mac(a.get("mac"))
    c = bt_open()
    try:
        removed = False
        # untrusted first: a trusted headset that is still on would call back the moment the link drops
        c.send(f"untrust {mac}")
        c.read(2, r"untrust succeeded|Failed to untrust|not available")
        for _ in range(2):
            c.send(f"disconnect {mac}")
            c.read(6, r"Successful disconnected|Failed to disconnect|not available")
            if not bt_still_connected(c, mac):
                break
        else:
            c.send(f"remove {mac}")
            t = c.read(6, r"Device has been removed|Failed to remove|not available")
            if re.search(r"Failed to remove", t, re.I):
                # still connected and kept: trusted again, as it was before the disconnect was asked for
                c.send(f"trust {mac}")
                c.read(2, r"trust succeeded|Failed to trust|not available")
                raise RuntimeError("failed")
            removed = True
        done_with(p, {"mac": mac, "connected": False, "removed": removed})
    finally:
        c.close()


def do_bt_forget(p, a):
    mac = check_mac(a.get("mac"))
    c = bt_open()
    try:
        c.send(f"remove {mac}")
        t = c.read(6, r"Device has been removed|Failed to remove|not available")
        if re.search(r"Failed to remove", t, re.I):
            raise RuntimeError(bt_reason(t) or "failed")
        done_with(p, {"mac": mac})
    finally:
        c.close()


def radio_blocked(kind):
    """Whether rfkill holds the radio (soft or hard), read from /sys: None when there is no such radio."""
    seen = False
    try:
        for d in Path("/sys/class/rfkill").glob("rfkill*"):
            try:
                if (d / "type").read_text().strip() != kind:
                    continue
                seen = True
                if (d / "soft").read_text().strip() == "1" or (d / "hard").read_text().strip() == "1":
                    return True
            except OSError:
                continue
    except OSError:
        return None
    return False if seen else None


def do_radio(p, a):
    """The Wi-Fi or the Bluetooth radio switched on or off from Settings. Wi-Fi through NetworkManager (nmcli radio
    wifi), Bluetooth through bluetoothctl power; rfkill backs both, and the result is read back from /sys."""
    kind, on = a.get("kind"), a.get("on")
    if kind not in ("wifi", "bluetooth") or not isinstance(on, bool):
        raise ValueError("bad radio")
    rk = "wlan" if kind == "wifi" else "bluetooth"
    have_rfkill = bool(shutil.which("rfkill"))
    if kind == "wifi":
        if on:
            if have_rfkill:
                run("rfkill", "unblock", "wifi", timeout=5)
            run("nmcli", "radio", "wifi", "on", timeout=15)
        else:
            run("nmcli", "radio", "wifi", "off", timeout=15)
            if have_rfkill and radio_blocked("wlan") is False:
                run("rfkill", "block", "wifi", timeout=5)
    else:
        if on:
            if have_rfkill:
                run("rfkill", "unblock", "bluetooth", timeout=5)
            nap(0.5)
            run("bluetoothctl", "power", "on", timeout=10)
        else:
            run("bluetoothctl", "power", "off", timeout=10)      # ends every link first
            if have_rfkill:
                run("rfkill", "block", "bluetooth", timeout=5)
    nap(0.3)
    blocked = radio_blocked(rk)
    if blocked is None:
        raise RuntimeError("no-radio")
    if blocked == on:
        raise RuntimeError("failed")
    done_with(p, {"kind": kind, "on": on})


# ---------------------------------------------------------------- the radio chips themselves
# The radio switch above leaves the chip powered and its driver loaded. Turning the chip off is done by the
# Raspberry Pi overlays disable-wifi and disable-bt (documented in /boot/firmware/overlays/README), read by the
# firmware at boot: the change takes effect after a restart. Armonico writes them in one block of its own at
# the end of config.txt and removes the block when both are on again; nothing else in the file is touched.
BOOT_CONFIG = Path(os.environ.get("ARMONICO_BOOT_CONFIG", "/boot/firmware/config.txt"))
CHIP_OVERLAY = {"wifi": "disable-wifi", "bluetooth": "disable-bt"}
CHIP_BEGIN = "# >>> Armonico: radio chips turned off in Settings > Network and links (written by Armonico)"
CHIP_END = "# <<< Armonico: radio chips"
CHIP_HCIUART = "# hciuart.service disabled with it, enabled again when Bluetooth is on"


def chip_split(text):
    """(the file without Armonico's block, the kinds the block turns off, whether hciuart was disabled with it)."""
    lines, out, ours, inside, hci = text.splitlines(), [], set(), False, False
    for ln in lines:
        s = ln.strip()
        if s == CHIP_BEGIN:
            inside = True
            continue
        if inside:
            if s == CHIP_END:
                inside = False
            elif s == CHIP_HCIUART:
                hci = True
            else:
                for kind, ov in CHIP_OVERLAY.items():
                    if s == f"dtoverlay={ov}":
                        ours.add(kind)
            continue
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else ""), ours, hci


def chip_by_hand(rest):
    """The kinds config.txt turns off outside Armonico's block (a line someone wrote by hand)."""
    found = set()
    for ln in rest.splitlines():
        s = ln.split("#", 1)[0].strip().replace(" ", "")
        for kind, ov in CHIP_OVERLAY.items():
            if s == f"dtoverlay={ov}" or s.startswith(f"dtoverlay={ov},"):
                found.add(kind)
    return found


CHIP_UNHAND = "# turned on from Armonico's Settings › Network and links, was: "


def chip_unhand(text, kind):
    """config.txt with every line outside Armonico's block that turns this chip off made a comment, with a note."""
    ov, out, inside = CHIP_OVERLAY[kind], [], False
    for ln in text.splitlines(keepends=True):
        s = ln.strip()
        if s == CHIP_BEGIN:
            inside = True
        elif s == CHIP_END:
            inside = False
        elif not inside:
            code = s.split("#", 1)[0].strip().replace(" ", "")
            if code == f"dtoverlay={ov}" or code.startswith(f"dtoverlay={ov},"):
                ln = CHIP_UNHAND + ln.lstrip()
        out.append(ln)
    return "".join(out)


def chip_text(text, off, hciuart=False):
    """config.txt with Armonico's block for the kinds in off (none: no block at all). [all], so no filter section
    above it ([pi5], [cm4] ...) narrows it."""
    rest, _, _ = chip_split(text)
    if not off:
        return rest
    block = [CHIP_BEGIN, "[all]"] + [f"dtoverlay={CHIP_OVERLAY[k]}" for k in ("wifi", "bluetooth") if k in off]
    if hciuart:
        block.append(CHIP_HCIUART)
    return rest + ("\n" if rest else "") + "\n".join(block + [CHIP_END]) + "\n"


def write_boot_config(text):
    """config.txt replaced in one step: a copy beside it, flushed, then renamed over it (the FAT boot partition
    renames in place), so a power cut leaves the old file or the new one, never half of one."""
    tmp = BOOT_CONFIG.with_name(".config.txt.armonico")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOOT_CONFIG)
    try:
        fd = os.open(BOOT_CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def hciuart_enabled():
    """hciuart.service is there and enabled: only then is it disabled with the chip, and enabled again with it."""
    r = run("systemctl", "is-enabled", "hciuart.service", timeout=10)
    return r.returncode == 0 and r.stdout.strip() == "enabled"


def do_radio_chip(p, a):
    """The Wi-Fi or the Bluetooth chip turned off (or on again) from the next start. Bluetooth off also disables
    hciuart.service, which otherwise fails at every start looking for the chip (as the Raspberry Pi
    documentation advises with disable-bt); it is enabled again with the chip."""
    kind, on = a.get("kind"), a.get("on")
    if kind not in CHIP_OVERLAY or not isinstance(on, bool):
        raise ValueError("bad radio chip")
    p(0)
    try:
        text = BOOT_CONFIG.read_text()
    except OSError:
        raise RuntimeError("no-config")
    orig = text
    rest, ours, hci = chip_split(text)
    if on and kind in chip_by_hand(rest):
        if a.get("hand") is not True:
            raise RuntimeError("by-hand")                # turned off by a line Armonico did not write: asked first
        # the person agreed on the screen: the line is kept as a comment with a note, not deleted, so whoever
        # wrote it can see what happened and put it back
        text = chip_unhand(text, kind)
        rest, ours, hci = chip_split(text)
    off = (ours - {kind}) if on else (ours | {kind})
    if kind == "bluetooth":
        if not on and not hci and hciuart_enabled():
            run("systemctl", "disable", "hciuart", timeout=30)
            hci = True
        elif on and hci:
            run("systemctl", "enable", "hciuart", timeout=30)
            hci = False
    new = chip_text(text, off, hci and "bluetooth" in off)
    changed = new != orig
    if changed:
        write_boot_config(new)
    p(1)
    done_with(p, {"kind": kind, "on": on, "changed": changed, "off": sorted(off)})


# ----------------------------------------------------------------------
# After a power loss (device/ui/views_power.py) and the deep check's report (device/ui/views_deep.py)
# ----------------------------------------------------------------------
BOOT_CMDLINE = os.environ.get("ARMONICO_CMDLINE", "/boot/firmware/cmdline.txt")
START_UNITS = ("armonico-bridge", "armonico-lessons", "armonico-device", "mosquitto")
DEEP_REPORT = re.compile(r"deep-\d{4}-\d\d-\d\d-\d{4}\.(txt|json)")
REPORTS_ON_STICK = "Armonico reports"


def powerloss_mod():
    spec = importlib.util.spec_from_file_location("armonico_powerloss_root", APP_DIR / "device" / "powerloss.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def do_boot_check(p, a):
    """What the file system check did at this boot (systemd-fsck's lines in the journal, and the initramfs'
    own log), and fsck.repair=yes on the kernel command line made sure of."""
    pl = powerloss_mod()
    r = run("journalctl", "-b", "-o", "cat", "--no-pager", "-q", "-u", "systemd-fsck-root.service",
            "-u", "systemd-fsck@*.service", timeout=20)
    text = r.stdout if r.returncode == 0 else ""
    try:
        text += "\n" + Path("/run/initramfs/fsck.log").read_text(errors="replace")
    except OSError:
        pass
    state, line = pl.fsck_summary(text)
    done_with(p, {"fsck": state, "line": line, "cmdline": pl.ensure_fsck_repair(BOOT_CMDLINE)})


BT_PROBLEM = re.compile(r"(a2dp|bluez|bluetooth|bluetoothd).*(underrun|xrun|timeout|timed out|disconnect|fail|error)", re.I)
KERNEL_IO = re.compile(r"I/O error|EXT4-fs error|Buffer I/O|blk_update_request|mmc\d+:.*(?:error|timeout)", re.I)


def deepcheck_mod():
    spec = importlib.util.spec_from_file_location("armonico_deepcheck_root", APP_DIR / "device" / "deepcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def do_logs_read(p, a):
    """The logs only root can read, for the checks that run as the armonico user (which is in no journal group, and
    the kernel log is closed to it): the kernel's read and write errors since boot (the last 40 lines) and the
    errors of the last 24 hours per unit (the deep check's list). {"kernel": [lines] | None, "journal": {...} | None}"""
    kernel = None
    for cmd in (("dmesg", "--level=err,crit,alert,emerg"), ("journalctl", "-k", "-b", "-p", "err", "-o", "cat", "-q", "--no-pager")):
        try:
            r = run(*cmd, timeout=8)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            kernel = [x.strip()[:200] for x in r.stdout.splitlines() if KERNEL_IO.search(x)][-40:]
            break
    journal = None
    try:
        journal = deepcheck_mod().journal_errors()
    except Exception:
        pass
    # the Bluetooth output's warnings of the last 30 minutes (bluetoothd, PipeWire, WirePlumber), the count the
    # quick check's sound row reads as fullcheck.bt_log does: that user cannot read these either
    bt = 0
    for args in (("-u", "bluetooth.service"), ("_COMM=pipewire",), ("_COMM=wireplumber",)):
        try:
            r = run("journalctl", *args, "--since", "-30min", "-o", "cat", "-q", "--no-pager", timeout=6)
        except (OSError, subprocess.SubprocessError):
            bt = None
            break
        if r.returncode != 0:
            bt = None
            break
        bt += sum(1 for x in r.stdout.splitlines() if BT_PROBLEM.search(x))
    done_with(p, {"kernel": kernel, "journal": journal, "bt": bt})


def do_service_start(p, a):
    """The named services started when they are not running (only the ones in START_UNITS), and each one's
    state read back."""
    units, again = a.get("units"), a.get("restart") or []
    for names in (units, again):
        if not isinstance(names, list) or not all(isinstance(u, str) and u in START_UNITS for u in names):
            raise ValueError("bad unit")
    out = {}
    for u in again:                              # a file put back under it: the service reads its files again
        run("systemctl", "restart", u, timeout=60)
    for u in units:
        if run("systemctl", "is-active", "--quiet", u, timeout=10).returncode != 0:
            if run("systemctl", "is-enabled", "--quiet", u, timeout=10).returncode == 0:
                run("systemctl", "start", u, timeout=60)
        out[u] = run("systemctl", "is-active", u, timeout=10).stdout.strip() or "unknown"
    done_with(p, out)


def do_report_usb(p, a):
    """The deep check's report files (checks/deep-*.txt and .json) copied to a USB stick, in "Armonico reports"."""
    names = a.get("files")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and DEEP_REPORT.fullmatch(n) for n in names):
        raise ValueError("unsupported character")
    sr = screen_rec()
    folder = sr.stick()
    if folder is None:
        done_with(p, {"copied": 0, "to": ""})
        return
    dest = folder.parent / REPORTS_ON_STICK
    try:
        os.mkdir(dest)
    except FileExistsError:
        pass
    if dest.is_symlink() or not dest.is_dir():
        raise RuntimeError("no folder on the stick")
    src = sr.open_dir(DATA_DIR, ("checks",))
    n = 0
    try:
        for name in names:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src)
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    continue
                data = f.read(8 << 20)
            tmp = dest / (name + ".part")
            with open(tmp, "wb") as g:
                g.write(data)
                g.flush()
                os.fsync(g.fileno())
            os.replace(tmp, dest / name)
            n += 1
    finally:
        os.close(src)
    os.sync()
    done_with(p, {"copied": n, "to": str(dest)})


SOAK_MINUTES = (15, 60, 120, 240, 480)
SOAK_DIR = Path("/var/lib/armonico-soak")     # root's own (device/soak.py OUT_ROOT), not the data folder
SOAK_ZIP = re.compile(r"soak-\d{4}-\d\d-\d\d-\d{6}\.zip")


def soak_cmd(*args, timeout=330):
    """device/soak.py with the venv's Python (paho), as its own process: what it prints, and its exit code."""
    py = APP_DIR / "venv" / "bin" / "python"
    exe = str(py) if py.exists() else sys.executable
    r = subprocess.run([exe, str(APP_DIR / "device" / "soak.py"), *args], capture_output=True, text=True,
                       timeout=timeout, env=dict(os.environ, DATA_DIR=str(DATA_DIR)))
    return r.returncode, (r.stdout + r.stderr).strip()


def do_soak_start(p, a):
    """The load test armed: it starts by itself, once, after the reboot that follows (the screen showed what
    it will do and got a yes). Only the lengths the screen offers, and two switches."""
    minutes = a.get("minutes")
    if minutes not in SOAK_MINUTES:
        raise ValueError("unsupported length")
    args = ["start", f"{minutes}m", "--reboot-later"]
    for key, flag in (("telegram", "--no-telegram"), ("alarm", "--no-alarm")):
        if a.get(key) is False:
            args.append(flag)
    p(1, "run", "")
    rc, out = soak_cmd(*args, timeout=60)
    if rc != 0:
        raise RuntimeError(out.splitlines()[-1] if out else "soak start failed")
    done_with(p, {"armed": True, "minutes": minutes})
    nap(2)
    run("systemctl", "reboot")


def do_soak_stop(p, a):
    """A running test stopped (it cleans up and writes its report, up to a few minutes), or one waiting for the
    reboot called off."""
    p(1, "run", "")
    rc, out = soak_cmd("stop")
    done_with(p, {"ok": rc == 0, "msg": out.splitlines()[0] if out else ""})


def do_soak_export(p, a):
    """The newest load-test report (soak-*.zip in /var/lib/armonico-soak) copied to a USB stick, in
    "Armonico reports", checked by size after it is written."""
    src_dir = SOAK_DIR
    zips = sorted(x.name for x in src_dir.iterdir() if SOAK_ZIP.fullmatch(x.name)) if src_dir.is_dir() else []
    if not zips:
        done_with(p, {"copied": 0, "to": "", "none": True})
        return
    name = zips[-1]
    sr = screen_rec()
    folder = sr.stick()
    if folder is None:
        done_with(p, {"copied": 0, "to": ""})
        return
    dest = folder.parent / REPORTS_ON_STICK
    try:
        os.mkdir(dest)
    except FileExistsError:
        pass
    if dest.is_symlink() or not dest.is_dir():
        raise RuntimeError("no folder on the stick")
    src = sr.open_dir(SOAK_DIR, ())
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src)
    finally:
        os.close(src)
    tmp = dest / (name + ".part")
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise RuntimeError("not a file")
        size = os.fstat(f.fileno()).st_size
        if size > 512 << 20:
            raise RuntimeError("report too large")
        with open(tmp, "wb") as g:
            shutil.copyfileobj(f, g, 1 << 20)
            g.flush()
            os.fsync(g.fileno())
    if tmp.stat().st_size != size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("the copy on the stick is short")
    os.replace(tmp, dest / name)
    os.sync()
    done_with(p, {"copied": 1, "to": str(dest), "name": name})


ACTIONS = {
    "soak-start": do_soak_start,
    "soak-stop": do_soak_stop,
    "soak-export": do_soak_export,
    "rec-export": do_rec_export,
    "boot-check": do_boot_check,
    "logs-read": do_logs_read,
    "service-start": do_service_start,
    "report-usb": do_report_usb,
    "full-reset": do_full_reset,
    "wifi-status": do_wifi_status,
    "wifi-scan": do_wifi_scan,
    "wifi-saved": do_wifi_saved,
    "wifi-connect": do_wifi_connect,
    "wifi-forget": do_wifi_forget,
    "bt-status": do_bt_status,
    "bt-scan": do_bt_scan,
    "bt-pair": do_bt_pair,
    "bt-connect": do_bt_connect,
    "bt-disconnect": do_bt_disconnect,
    "bt-reconnect": do_bt_reconnect,
    "bt-forget": do_bt_forget,
    "radio": do_radio,
    "radio-chip": do_radio_chip,
    "telegram-keep": do_telegram_keep,
    "telegram-test": do_telegram_test,
    "telegram-forget": do_telegram_forget,
    "telegram-kept": do_telegram_kept,
    "telegram-report": do_telegram_report,
    "ha-keep": do_ha_keep,
    "ha-kept": do_ha_kept,
    "ha-forget": do_ha_forget,
    "ha-telegram-connect": do_ha_telegram_connect,
    "ssh-on": do_ssh_on,
    "shutdown": lambda p, a: run("systemctl", "poweroff"),
    "reboot": lambda p, a: run("systemctl", "reboot"),
    "set-time": do_set_time,
    "set-timezone": do_set_timezone,
    "set-brightness": do_set_brightness,
    "broker-local": do_broker_local,
    "broker-external": do_broker_external,
    "usb-mount": do_usb_mount,
    "usb-format": do_usb_format,
}


def main():
    try:
        req = json.loads(read_nofollow(REQUEST))
    except (OSError, ValueError):
        REQUEST.unlink(missing_ok=True)
        return
    REQUEST.unlink(missing_ok=True)
    rid = re.sub(r"[^0-9a-f]", "", str(req.get("id", "")))[:12]
    action, args = req.get("action"), req.get("args") or {}
    p = Progress(rid)
    if action not in ACTIONS or not isinstance(args, dict):
        p(0, "fail", "unknown action")
        return
    p(0)
    try:
        ACTIONS[action](p, args)
    except Exception as e:
        p(0, "fail", str(e)[:200])
        return
    try:
        st = json.loads(read_nofollow(STATUS))
        if st.get("state") != "done":
            p(int(st.get("step", 0)), "done")
    except (OSError, ValueError):
        p(0, "done")


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("root only: the armonico-device-request unit runs this")
    main()
