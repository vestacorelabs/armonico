#!/usr/bin/env python3
"""
backup.py - backup and restore of the whole Armonico Pi in one file

The file: armonico-backup-YYYY-MM-DD-HHMM.armonico, a normal ZIP (7-Zip opens it, or rename it
to .zip). Inside:
  manifest.json   format "armonico-backup", format_version, the Armonico version, "device" or
                  "regular", when, the host name, and the path and SHA-256 of every file
  etc/            config.env and modes/*.env from /etc/armonico (the broker passwords are in them)
  data/           the data folder: profiles, progress, history, recordings, songs, the device's
                  settings.json, the screen code. Not the backups themselves, nor what "prepare" renders.

Sizes: no limit of its own. A backup is written only where there is room for all of it (checked
first), a file that could not be read is named on the screen, and a failed file is deleted.

A restore checks the file first, keeps the current state as before-restore-<date>.armonico in
the backups folder, writes the backup in, puts the restored broker logins into the local
broker's users file and logs in with them, restarts the services and checks that they came up.
If they did not, it goes back to the before-restore file by itself. undo_last() goes back to it
on request.

Where the backup goes or comes from: Local (USB sticks, the SD card), Smb, Scp, Ftp, and Http
(download only). Each has test(), listdir(), get() and put().

Root: the screen runs as the service user. It writes a request (request()), and the root unit
armonico-device-backup.path runs "backup.py run-request". Root does only what needs root:
/etc/armonico, systemctl, and writing to a USB stick. Reading and writing the data folder and
the network transfers run in a child process under the service user (the "_worker" commands).

  backup.py create <folder>      backup.py validate <file>     backup.py restore <file>
  backup.py undo                 backup.py run-request (root, from the path unit)
"""

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMAT = "armonico-backup"
FORMAT_VERSION = 1
SUFFIX = ".armonico"
PROFILE_ITEMS = ("lessons.json", "history.json", "settings.json", "learner.json")   # as lessons/piano_game.py
KEEP_BEFORE = 3                       # before-restore files kept; older ones go
# No size limit of its own: every file of the data folder goes in, whatever its size, and the free
# space decides. A backup is written only when the place it goes has room for it, and kept free
# on the SD card is RESERVE, so a backup never fills the system's own disk.
RESERVE = 100 * 1024 * 1024
MAX_ENTRIES = 50000
MOSQ_DIR = Path("/etc/mosquitto")     # the local broker's files, as install.sh writes them
TIMEOUT = 20                          # seconds, for every network step that waits for an answer
CHECK_WAIT = 60                       # seconds the services get to come up after a restore

# what is never backed up: requests, temporary files, the backups, and what "prepare" renders again
# requests to root and their status: a restore that wrote one back would start that action again
SKIP_TOP = {"backups", ".restore.zip", "update-request", "reset-request", "device-mode-request",
            "device-backup-request", "device-request", "device-request.status", "device-fix-request", "edit-mode",
            "stop-request"}
SKIP_DIRS = {"device/samples", "device/fonts", "device/gm",
             # the power-loss state of this boot (device/powerloss.py): a restore that wrote it back would open the
             # recovery screen with no power loss, and put old "last good" copies over the files
             "device/running.json", "device/unclean.json", "device/last.json", "device/last-good",
             "device/recordings/.interrupted"}
SKIP_SUFFIX = (".lock", ".tmp", ".part")
CONFIG_LINE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*='[^'\n]*'")   # also safe for the installer to source
MODE_NAME = re.compile(r"[a-z0-9_-]{1,32}\.env")
# The settings a restore may write into config.env and the mode files. The installer reads these
# files with the shell's ".", as root, so a line of any other name (PATH, or one of the installer's
# own variables) is dropped, unless this machine's own config.env already has it.
CONFIG_KEYS = {"AMIDI_DEVICE", "DEVICE", "DEVICE_BUTTON_PINS", "DEVICE_KEY_PINS", "DEVICE_OCTAVE",
               "DEVICE_VELOCITY", "HA_URL", "HA_TOKEN", "KEY_HIGHEST", "KEY_LOWEST", "LESSON_BIND", "LESSON_HOSTS",
               "LESSON_UI_PORT", "MIDI_DEVICE", "MQTT_ADMIN_SECRET", "MQTT_HOST", "MQTT_LOCAL", "MQTT_PASS", "MQTT_PORT",
               "MQTT_RECORD_DAYS", "MQTT_REC_PASS", "MQTT_REC_USER", "MQTT_USER", "SCREEN_CODE", "STOP_KEY",
               "TELEGRAM_TOKEN", "UI_LANG"}

Entry = namedtuple("Entry", "name is_dir size mtime")


# ----------------------------------------------------------------------
# Paths and messages
# ----------------------------------------------------------------------
def _read_env_file(path):
    try:
        return dict(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)='([^']*)'", Path(path).read_text(), re.M))
    except (OSError, UnicodeDecodeError):
        return {}


@dataclass
class Paths:
    """Where everything is. From the environment (DATA_DIR, SONGS_DIR, ARMONICO_ETC_DIR,
    ARMONICO_APP_DIR) or config.env, so the tests can point it at temporary folders."""
    data: Path
    etc: Path
    songs: Path
    app: Path

    @property
    def backups(self):
        return self.data / "backups"

    @property
    def work(self):
        return self.backups / ".work"

    @property
    def request(self):
        return self.data / "device-backup-request"

    @property
    def job(self):
        return self.backups / "job.json"

    @property
    def state(self):
        return self.backups / "restore-state.json"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        etc = Path(env.get("ARMONICO_ETC_DIR", "/etc/armonico"))
        cfg = _read_env_file(etc / "config.env")
        data = Path(env.get("DATA_DIR") or cfg.get("DATA_DIR") or "/var/lib/armonico")
        songs = Path(env.get("SONGS_DIR") or cfg.get("SONGS_DIR") or data / "songs")
        app = Path(env.get("ARMONICO_APP_DIR") or Path(__file__).resolve().parent.parent)
        return cls(data, etc, songs, app)


def _paths(paths):
    return paths if paths is not None else Paths.from_env()


MSG = {
    "ok": ("The file is fine", "הקובץ תקין"),
    "missing": ("The file is not there: {path}", "הקובץ לא נמצא: {path}"),
    "not_zip": ("Not a backup file: it is not a ZIP", "זה לא קובץ גיבוי: הוא לא ZIP"),
    "no_manifest": ("Not an Armonico backup: no manifest.json inside", "לא קובץ גיבוי של Armonico: אין בו manifest.json"),
    "newer": ("This backup was made by a newer Armonico. Update first, then restore",
              "הגיבוי נוצר בגרסה חדשה יותר של Armonico. קודם לעדכן, ואחר כך לשחזר"),
    "too_large": ("The backup is too large", "קובץ הגיבוי גדול מדי"),
    "unsafe": ("The backup holds a file with an unsafe name or content", "יש בגיבוי קובץ עם שם או תוכן לא בטוחים"),
    "request_file": ("The backup holds a request to the system ({name}), which a backup never has",
                     "יש בגיבוי קובץ בקשה למערכת ({name}), ובגיבוי אמיתי אין קובץ כזה"),
    "checksum": ("The backup is damaged: a file inside does not match its SHA-256",
                 "קובץ הגיבוי פגום: קובץ בתוכו לא תואם ל-SHA-256 שלו"),
    "regular": ("A backup of a regular install, not a device: only progress and songs can come from it",
                "גיבוי של התקנה רגילה, לא של מכשיר: אפשר לשחזר ממנו רק התקדמות ושירים"),
    "device_here": ("A backup of a device, not of a regular install: only progress and songs can come from it",
                    "גיבוי של מכשיר, לא של התקנה רגילה: אפשר לשחזר ממנו רק התקדמות ושירים"),
    "no_room": ("Not enough free space in {where}: {need} MB are needed, there is room for {free} MB",
                "אין מספיק מקום פנוי ב-{where}: צריך {need}MB ויש מקום ל-{free}MB"),
    "restored": ("Restored, and everything came up", "השחזור הושלם, והכל עלה"),
    "restored_broker": ("Restored, and the services came up, but the broker did not accept the login: {detail}",
                        "השחזור הושלם והשירותים עלו, אבל הברוקר לא קיבל את ההתחברות: {detail}"),
    "rolled_back": ("Armonico did not come up after the restore, so it went back to the state before it",
                    "Armonico לא עלה אחרי השחזור, ולכן הוא חזר לבד למצב שלפניו"),
    "failed": ("The restore failed, and going back did not bring Armonico up either: {detail}",
               "השחזור נכשל, וגם החזרה למצב הקודם לא העלתה את Armonico: {detail}"),
    "no_undo": ("There is no restore to undo", "אין שחזור לבטל"),
    "undone": ("Back to the state before the restore", "המצב חזר למה שהיה לפני השחזור"),
    "saved": ("Saved: {name}", "נשמר: {name}"),
    "busy": ("Another backup or restore is running", "גיבוי או שחזור אחר כבר רץ"),
    "bad_request": ("The request was not understood", "הבקשה לא הובנה"),
}

# the steps the screen shows, with the demo's wording
STEP = {
    "settings": ("Device settings and look", "הגדרות המכשיר והמראה"),
    "progress": ("Progress, profiles and Race yourself", "התקדמות, פרופילים ומרוץ מול עצמך"),
    "songs": ("Songs, including yours", "שירים, כולל אלה שהוספת"),
    "connection": ("Connection: broker, Home Assistant, Telegram", "חיבור: ברוקר, Home Assistant, טלגרם"),
    "pack": ("Packing {name}", "אריזה ל-{name}"),
    "save": ("Saving to {where}", "שמירה ב-{where}"),
    "send": ("Sending to {where}", "שליחה ל-{where}"),
    "verify": ("Reading it back and checking SHA-256", "קריאה חוזרת ובדיקת SHA-256"),
    "download": ("Downloading", "הורדה"),
    "check": ("Checking the file: ZIP, manifest.json and SHA-256", "בדיקת הקובץ: ZIP, manifest.json ו-SHA-256"),
    "snapshot": ("A backup of the current state, to go back if needed", "גיבוי של המצב הנוכחי, לחזרה אם צריך"),
    "restore": ("Restoring settings, progress and songs", "שחזור הגדרות, התקדמות ושירים"),
    "restore_connection": ("Restoring the connection, and a test", "שחזור החיבור ובדיקה"),
    "restart": ("Restart, and a check that all came up", "הפעלה מחדש ובדיקה שהכל עלה"),
    "rollback": ("It did not come up: going back to the state before", "לא עלה: חזרה למצב הקודם"),
    "undo": ("Restoring the earlier state", "שחזור המצב הקודם"),
}

# what a step found that the person should know, shown as a step of its own marked as not done
WARN = {
    "left_out": ("Left out of the backup, could not be read: {n} files ({names})",
                 "לא נכנסו לגיבוי כי לא היה אפשר לקרוא אותם: {n} קבצים ({names})"),
    "broker": ("The broker did not accept the login: {detail}", "הברוקר לא קיבל את ההתחברות: {detail}"),
}


def msg(key, **kw):
    en, he = MSG[key]
    return en.format(**kw), he.format(**kw)


def _step(progress, key, **kw):
    if progress:
        en, he = STEP[key]
        progress(key, en.format(**kw), he.format(**kw))


def _warn(progress, key, **kw):
    if progress:
        en, he = WARN[key]
        progress("warn_" + key, en.format(**kw), he.format(**kw))


class BackupError(Exception):
    """A failure the screen can show as it is, in English and in Hebrew."""

    def __init__(self, key, en, he):
        super().__init__(en)
        self.key, self.en, self.he = key, en, he


@dataclass
class Check:
    ok: bool
    reason_key: str
    reason_en: str
    reason_he: str
    info: dict = field(default_factory=dict)


def _refuse(key, **kw):
    return Check(False, key, *msg(key, **kw))


# ----------------------------------------------------------------------
# What goes in
# ----------------------------------------------------------------------
def install_type(paths):
    if (paths.etc / "device").is_file() or _read_env_file(paths.etc / "config.env").get("DEVICE") == "yes":
        return "device"
    return "regular"


def version(paths):
    try:
        return (paths.app / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def category(arc):
    """Which step a file belongs to: connection, settings, progress or songs."""
    if arc.startswith("etc/"):
        return "connection"
    rel = arc[len("data/"):]
    if rel.startswith("songs/"):
        return "songs"
    if rel in PROFILE_ITEMS or rel == "profiles.json" or rel.startswith(("profiles/", "recordings/")):
        return "progress"
    return "settings"


def _skipped(rel):
    parts = rel.split("/")
    return (parts[0] in SKIP_TOP or any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS)
            or parts[-1].endswith(SKIP_SUFFIX))


def _walk(base, prefix, skip_songs=None):
    """Regular files under base, never through a link, as (arcname, path)."""
    out = []
    if not base.is_dir():
        return out
    for top, dirs, files in os.walk(base, followlinks=False):
        here = Path(top)
        rel_top = here.relative_to(base).as_posix()
        rel_top = "" if rel_top == "." else rel_top + "/"
        # the songs always come from the songs folder, even when SONGS_DIR points elsewhere
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink() and not _skipped(rel_top + d)
                         and (skip_songs is None or (here / d != skip_songs and rel_top + d != "songs")))
        for f in sorted(files):
            p = here / f
            rel = rel_top + f
            try:
                st = p.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or _skipped(rel):
                continue
            out.append((prefix + rel, p))
    return out


def collect(paths):
    """Every file a backup holds, as (arcname, path)."""
    files = []
    if (paths.etc / "config.env").is_file():
        files.append(("etc/config.env", paths.etc / "config.env"))
    modes = paths.etc / "modes"
    if modes.is_dir():
        for p in sorted(modes.iterdir()):
            if MODE_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink():
                files.append((f"etc/modes/{p.name}", p))
    files += _walk(paths.data, "data/", skip_songs=paths.songs)
    files += _walk(paths.songs, "data/songs/")
    return files


def backup_name(prefix="armonico-backup", when=None):
    return f"{prefix}-{(when or datetime.now()).strftime('%Y-%m-%d-%H%M')}{SUFFIX}"


def _free_name(folder, name):
    """name, or name-2, name-3 ... when a backup of the same minute is already there."""
    p = Path(folder) / name
    k = 2
    while p.exists():
        p = Path(folder) / name.replace(SUFFIX, f"-{k}{SUFFIX}")
        k += 1
    return p


def _size(files):
    total = 0
    for _, p in files:
        try:
            total += p.lstat().st_size
        except OSError:
            pass
    return total


def _free(folder):
    """Free bytes where folder is (or its nearest existing parent)."""
    folder = Path(folder)
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return 0


def _mb(n):
    return max(0, -(-int(n) // 1048576))


def need_room(folder, size, reserve=RESERVE):
    """Raises no_room when folder has no room for size bytes, keeping reserve free."""
    free = _free(folder)
    if size + reserve > free:
        raise BackupError("no_room", *msg("no_room", need=_mb(size + reserve), free=_mb(free), where=str(folder)))


def _room_for(local):
    """Bytes a download into local may take: the free space there, less RESERVE."""
    return max(0, _free(Path(local).parent) - RESERVE)


def _disk_full(e):
    import errno
    return isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT)


def write_backup(out, paths, progress=None):
    """Writes the backup into the open binary file out. Returns the manifest; a file that could not
    be read is listed in manifest["left_out"] and shown as a step."""
    files = collect(paths)
    entries, left_out = [], []
    order = ("settings", "progress", "songs", "connection")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for cat in order:
            _step(progress, cat)
            for arc, p in files:
                if category(arc) != cat:
                    continue
                try:
                    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                except FileNotFoundError:
                    continue                                  # deleted since the list was made
                except OSError:
                    left_out.append(arc)                      # not readable: left out, and said so
                    continue
                with os.fdopen(fd, "rb") as src:
                    st = os.fstat(src.fileno())
                    info = zipfile.ZipInfo(arc, time.localtime(max(st.st_mtime, 315532800))[:6])
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o640 << 16
                    h, size = hashlib.sha256(), 0
                    with z.open(info, "w", force_zip64=True) as dst:
                        while chunk := src.read(1 << 16):
                            h.update(chunk)
                            dst.write(chunk)
                            size += len(chunk)
                entries.append({"path": arc, "sha256": h.hexdigest(), "size": size})
        manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "version": version(paths),
                    "install": install_type(paths), "created": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "hostname": socket.gethostname(), "files": entries}
        if left_out:
            manifest["left_out"] = left_out
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    if left_out:
        names = ", ".join(a.split("/", 1)[-1] for a in left_out[:3]) + (" …" if len(left_out) > 3 else "")
        _warn(progress, "left_out", n=len(left_out), names=names)
    return manifest


def create(dest, progress=None, paths=None, transport=None, name=None):
    """Makes a backup and saves it in dest: a folder (or a file path) here, or a folder on
    transport. Checks it by reading it back. Returns where it went."""
    paths = _paths(paths)
    name = name or backup_name()
    if transport is None:
        dest = Path(dest)
        target = dest if dest.suffix == SUFFIX else _free_name(dest, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        # room for everything, checked before a byte is written (a ZIP is never much larger than its files)
        need = _size(collect(paths)) * 101 // 100 + 1048576
        need_room(target.parent, need)
        _step(progress, "pack", name=target.name)
        tmp = target.with_name("." + target.name + ".part")
        try:
            with open(tmp, "wb") as f:
                write_backup(f, paths, progress)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            _step(progress, "save", where=str(target.parent))
            tmp.replace(target)
        except OSError as e:
            if not _disk_full(e):
                raise
            tmp.unlink(missing_ok=True)                       # the free space comes back before the message
            raise BackupError("no_room", *msg("no_room", need=_mb(need + RESERVE), free=_mb(_free(target.parent)),
                                              where=str(target.parent))) from e
        finally:
            tmp.unlink(missing_ok=True)
        _step(progress, "verify")
        chk = validate(target, paths)
        if not chk.ok:
            target.unlink(missing_ok=True)                    # a file that failed its check is never kept
            raise BackupError("verify", *chk_text(chk))
        return str(target)
    # elsewhere: packed in the work folder, sent, fetched back and checked
    paths.work.mkdir(parents=True, exist_ok=True)
    local = paths.work / name
    try:
        create(local, progress, paths)
        remote = _join(str(dest), name)
        _step(progress, "send", where=f"{transport.label} {dest}")
        transport.put(str(local), remote)
        _step(progress, "verify")
        back = paths.work / ("back-" + name)
        try:
            transport.get(remote, str(back))
            if _sha(back) != _sha(local):
                raise BackupError("verify", "The copy on the server does not match: SHA-256 differs",
                                  "העותק בשרת לא תואם: ה-SHA-256 שונה")
        finally:
            back.unlink(missing_ok=True)
        return remote
    finally:
        local.unlink(missing_ok=True)


def chk_text(chk):
    return chk.reason_en, chk.reason_he


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def _join(folder, name):
    return (folder.rstrip("/") + "/" + name) if folder else name


# ----------------------------------------------------------------------
# Checking a file
# ----------------------------------------------------------------------
def _safe_arc(n):
    return (n == "manifest.json" or n.startswith(("etc/", "data/"))) and not (
        n.startswith("/") or "\\" in n or "\x00" in n or ".." in n.split("/") or "" in n.split("/")
        or "." in n.split("/"))


def config_safe(text):
    """config.env and the mode files are read by the installer with the shell's ".": only
    KEY='value' lines and comments are let in, so nothing in a backup can run as a command."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not CONFIG_LINE.fullmatch(line):
            return False
    return True


def filter_config(text, allowed):
    """Only comments and KEY='value' lines of the allowed names; DATA_DIR and SONGS_DIR are set by apply_config."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            out.append(line)
        elif CONFIG_LINE.fullmatch(line) and line.split("=", 1)[0] in allowed - {"DATA_DIR", "SONGS_DIR"}:
            out.append(line)
    return "\n".join(out) + "\n"


def _open_zip(src):
    """src: a path, or an open binary file (the root helper hands the file it opened)."""
    return zipfile.ZipFile(src)


def validate(path, paths=None):
    """Checks a backup without changing anything. Check.ok is False for a file that must not be
    restored; a backup of the other install type is ok but partial (info["partial"])."""
    paths = _paths(paths)
    if isinstance(path, (str, Path)) and not Path(path).is_file():
        return _refuse("missing", path=path)
    try:
        if not isinstance(path, (str, Path)):
            path.seek(0)
        z = _open_zip(path)
    except (zipfile.BadZipFile, OSError, ValueError):
        return _refuse("not_zip")
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos if not i.is_dir()]
        if "manifest.json" not in names:
            return _refuse("no_manifest")
        try:
            if z.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
                return _refuse("too_large")
            man = json.loads(z.read("manifest.json"))
            ok = isinstance(man, dict) and man.get("format") == FORMAT and isinstance(man.get("format_version"), int) \
                and not isinstance(man.get("format_version"), bool)
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, OSError):
            ok = False
        if not ok:
            return _refuse("no_manifest")
        if man["format_version"] > FORMAT_VERSION:
            return _refuse("newer")
        if man["format_version"] < 1 or not isinstance(man.get("files"), list):
            return _refuse("no_manifest")
        if len(infos) > MAX_ENTRIES:
            return _refuse("too_large")
        # No fixed size limit. What the entries say they unpack to (zipfile never reads past that) has
        # to fit where a restore writes it: the free space there and the files a restore replaces.
        # A file that claims more cannot be a backup of a device like this one (a ZIP bomb, say),
        # and is refused before a byte of it is read.
        declared = sum(i.file_size for i in infos)
        room = _free(paths.data) + _size(collect(paths))
        if declared > room:
            return _refuse("no_room", need=_mb(declared), free=_mb(room), where=str(paths.data))
        if any(not _safe_arc(n) for n in names) or len(set(names)) != len(names):
            return _refuse("unsafe")
        # a request to root (device-request, update-request, backups/ ...) is never backed up: one in
        # the file was put there by hand, and restoring it would start that action as root
        bad = next((n for n in names if n.startswith("data/") and n.split("/")[1] in SKIP_TOP), None)
        if bad:
            return _refuse("request_file", name=bad[len("data/"):])
        listed = {}
        for e in man["files"]:
            if not (isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("sha256"), str)) \
                    or e["path"] == "manifest.json" or e["path"] in listed:
                return _refuse("no_manifest")
            listed[e["path"]] = e["sha256"].lower()
        if set(listed) != set(names) - {"manifest.json"}:
            return _refuse("checksum")
        try:
            for n, want in listed.items():
                h = hashlib.sha256()
                with z.open(n) as f:
                    while chunk := f.read(1 << 16):
                        h.update(chunk)
                if h.hexdigest() != want:
                    return _refuse("checksum")
            for n in names:
                if n == "etc/config.env" or (n.startswith("etc/modes/") and n.endswith(".env")):
                    if n != "etc/config.env" and not MODE_NAME.fullmatch(n[len("etc/modes/"):]):
                        return _refuse("unsafe")
                    if not config_safe(z.read(n).decode("utf-8")):
                        return _refuse("unsafe")
                elif n.startswith("etc/"):
                    return _refuse("unsafe")                 # nothing else of /etc is ever written
        except (zipfile.BadZipFile, OSError, UnicodeDecodeError, EOFError, zlib.error):
            return _refuse("checksum")
        info = _info(z, man, names)
    here = install_type(paths)
    info["partial"] = info["type"] != here
    if info["type"] == "regular" and here == "device":
        return Check(True, "regular", *msg("regular"), info)
    if info["partial"]:
        return Check(True, "device_here", *msg("device_here"), info)
    return Check(True, "ok", *msg("ok"), info)


def _info(z, man, names):
    profiles = 0
    if "data/profiles.json" in names:
        try:
            profiles = len(json.loads(z.read("data/profiles.json")).get("list", []))
        except (ValueError, AttributeError, UnicodeDecodeError):
            profiles = 0
    if not profiles:
        profiles = int(any(n[5:] in PROFILE_ITEMS for n in names)) + len(
            {n.split("/")[2] for n in names if n.startswith("data/profiles/") and n.count("/") >= 3})
    songs = sum(1 for n in names if n.startswith("data/songs/") and n.lower().endswith((".mid", ".midi")))
    broker = None
    if "etc/config.env" in names:
        cfg = dict(re.findall(r"^([A-Z_]+)='([^']*)'", z.read("etc/config.env").decode("utf-8"), re.M))
        broker = "local" if cfg.get("MQTT_LOCAL") == "yes" else "shared"
    typ = man.get("install") if man.get("install") in ("device", "regular") else "regular"
    return {"version": str(man.get("version") or "")[:20], "type": typ, "date": str(man.get("created") or "")[:32],
            "hostname": str(man.get("hostname") or "")[:64], "profiles": profiles, "songs": songs,
            "broker": broker, "files": len(names) - 1}


# ----------------------------------------------------------------------
# Writing a backup in
# ----------------------------------------------------------------------
def _allowed(arc, partial):
    return not partial or category(arc) in ("progress", "songs")


def _target(arc, paths):
    rel = arc[len("data/"):]
    if rel.startswith("songs/"):
        return paths.songs / rel[len("songs/"):]
    return paths.data / rel


def system_songs(paths):
    """The songs that come with Armonico (setup/music), by file name."""
    folder = paths.app / "setup" / "music"
    try:
        return {p.name: p for p in folder.glob("*.mid") if p.is_file()}
    except OSError:
        return {}


def seed_system_songs(paths):
    """Puts back every song that comes with Armonico and is missing from the songs folder, as the
    installer does. A song of the same name already there is never replaced."""
    added = 0
    try:
        paths.songs.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    for name, src in system_songs(paths).items():
        dst = paths.songs / name
        if dst.exists() or dst.is_symlink():
            continue
        try:
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o640)
            added += 1
        except OSError:
            pass
    return added


def apply_data(src, paths, partial=False):
    """Writes the data folder part of a checked backup. A full restore also removes the files a
    backup would hold that are not in this one (a profile added since, say); a partial one only
    adds and replaces progress and songs. The songs that come with Armonico always stay: a full
    restore of a backup without them deleted them, and the song list lost its first songs.
    Runs as the service user (see _worker)."""
    builtin = set(system_songs(paths))
    with _open_zip(src) as z:
        names = [n for n in z.namelist() if n.startswith("data/") and not n.endswith("/") and _allowed(n, partial)
                 and not _skipped(n[len("data/"):])]          # never a request, a temp file or a backup
        if not partial:
            keep = set(names)
            for arc, p in collect(paths):
                if arc.startswith("data/") and arc not in keep:
                    if p.parent == paths.songs and p.name in builtin:
                        continue
                    p.unlink(missing_ok=True)
        for n in names:
            dst = _target(n, paths)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink():
                dst.unlink()
            tmp = dst.with_name(dst.name + ".restore.tmp")
            with z.open(n) as f, open(tmp, "wb") as out:
                shutil.copyfileobj(f, out)
            os.chmod(tmp, 0o640)
            if n == "data/device/settings.json":
                # under the same lock as screen.py and armonico_pi.py
                with open(dst.with_suffix(".lock"), "a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    tmp.replace(dst)
            else:
                tmp.replace(dst)
    seed_system_songs(paths)
    return len(names)


def _write_root_file(path, text, gid):
    tmp = path.with_name("." + path.name + ".restore")
    old = os.umask(0o027)
    try:
        tmp.unlink(missing_ok=True)
        tmp.write_text(text)
    finally:
        os.umask(old)
    if os.geteuid() == 0:
        os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    tmp.replace(path)


def apply_config(src, paths):
    """config.env and the broker modes from a checked backup. This machine's DATA_DIR and
    SONGS_DIR stay as they are. Root, since /etc/armonico is root's."""
    with _open_zip(src) as z:
        names = z.namelist()
        if "etc/config.env" not in names:
            return 0
        gid = paths.etc.stat().st_gid if paths.etc.exists() else 0
        text = z.read("etc/config.env").decode("utf-8")
        here = _read_env_file(paths.etc / "config.env")
        text = filter_config(text, set(here) | CONFIG_KEYS)
        for k in ("DATA_DIR", "SONGS_DIR"):
            if k in here:
                line = f"{k}='{here[k]}'"
                if re.search(rf"^{k}='[^']*'$", text, re.M):
                    text = re.sub(rf"^{k}='[^']*'$", lambda m: line, text, flags=re.M)
                else:
                    text = text.rstrip("\n") + "\n" + line + "\n"
        paths.etc.mkdir(parents=True, exist_ok=True)
        _write_root_file(paths.etc / "config.env", text, gid)
        modes = paths.etc / "modes"
        wanted = {n[len("etc/modes/"):] for n in names if n.startswith("etc/modes/")}
        if wanted or modes.is_dir():
            modes.mkdir(mode=0o750, exist_ok=True)
            if os.geteuid() == 0:
                os.chown(modes, 0, gid)
            for p in modes.glob("*.env"):
                if p.name not in wanted:
                    p.unlink()
            for name in wanted:
                _write_root_file(modes / name, filter_config(z.read("etc/modes/" + name).decode("utf-8"), CONFIG_KEYS), gid)
        return 1 + len(wanted)


# ----------------------------------------------------------------------
# The services
# ----------------------------------------------------------------------
class Systemd:
    """Stops, starts and checks the Armonico services. The tests hand restore() their own."""

    def __init__(self, paths):
        self.paths = paths
        self.app = paths.etc.name or "armonico"

    def units(self):
        base = [f"{self.app}-bridge", f"{self.app}-lessons"]
        for u in (f"{self.app}-device", f"{self.app}-recorder", f"{self.app}-screen"):
            if subprocess.run(["systemctl", "is-enabled", "--quiet", u], timeout=TIMEOUT).returncode == 0:
                base.append(u)
        return base

    def stop(self):
        # the screen keeps running, so it shows the steps; it restarts at the end with the rest
        units = [u for u in self.units() if not u.endswith("-screen")]
        subprocess.run(["systemctl", "stop", *units], timeout=120, check=False)

    def start(self):
        subprocess.run(["systemctl", "restart", "--no-block", *self.units()], timeout=120, check=False)

    def check(self, wait=CHECK_WAIT):
        """Every unit active, and the lesson engine answering on its port."""
        port = _read_env_file(self.paths.etc / "config.env").get("LESSON_UI_PORT", "8099")
        end, detail = time.time() + wait, ""
        while True:
            down = [u for u in self.units() if subprocess.run(["systemctl", "is-active", "--quiet", u],
                                                              timeout=TIMEOUT).returncode != 0]
            answers = engine_answers(port)
            if not down and answers:
                return True, ""
            detail = ("not running: " + " ".join(down)) if down else f"the lesson engine does not answer on port {port}"
            if time.time() > end:
                return False, detail
            time.sleep(2)


    def broker(self):
        """After the connection is restored: the local broker's users file made to match the restored
        logins, then a real login with them. "" when it works, else what went wrong."""
        return broker_check(self.paths)


def _broker_settings(paths):
    """The logins the local broker has to accept: config.env's when the local broker is the one in
    use, else the saved local profile's (the way back to it). (settings, in_use) or (None, False)."""
    cfg = _read_env_file(paths.etc / "config.env")
    if cfg.get("MQTT_LOCAL") == "yes":
        return cfg, True
    prof = _read_env_file(paths.etc / "modes" / "standalone.env")
    if prof.get("MQTT_LOCAL") == "yes" and prof.get("MQTT_PASS"):
        return prof, False
    return None, False


def sync_broker(paths, mosq_dir=None, run=subprocess.run):
    """Writes the local broker's users file (/etc/mosquitto/armonico.passwd) with the restored logins,
    as install.sh does: plain text with umask 077, hashed in place by mosquitto_passwd -U, then
    mosquitto reloads it. Before, a restore wrote the passwords into config.env only, and after a
    full reset and a restore the bridge and the recorder got "not authorized".
    Returns "" when done or nothing was needed, else why it could not be done."""
    mosq_dir = Path(mosq_dir or MOSQ_DIR)
    cfg, in_use = _broker_settings(paths)
    if cfg is None:
        return ""
    app = paths.etc.name or "armonico"
    if not (mosq_dir / "conf.d" / f"{app}.conf").is_file():
        return "the local broker is not set up on this device" if in_use else ""
    users = []
    for u, pw in ((cfg.get("MQTT_USER", ""), cfg.get("MQTT_PASS", "")),
                  (cfg.get("MQTT_REC_USER", ""), cfg.get("MQTT_REC_PASS", ""))):
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", u) and pw and ":" not in pw and u not in dict(users):
            users.append((u, pw))
    if not users:
        return "no broker login in the restored settings" if in_use else ""
    target = mosq_dir / f"{app}.passwd"
    tmp = target.with_name("." + target.name + ".restore")
    old = os.umask(0o077)
    try:
        tmp.unlink(missing_ok=True)
        tmp.write_text("".join(f"{u}:{pw}\n" for u, pw in users))
    finally:
        os.umask(old)
    try:
        r = run(["mosquitto_passwd", "-U", str(tmp)], capture_output=True, timeout=TIMEOUT)
        if r.returncode != 0:
            return "mosquitto_passwd did not hash the logins"
        if os.geteuid() == 0:
            shutil.chown(tmp, "mosquitto", "mosquitto")
        os.chmod(tmp, 0o700)
        tmp.replace(target)
    except (OSError, LookupError, subprocess.SubprocessError) as e:
        return f"the broker's users file could not be written: {e}"
    finally:
        tmp.unlink(missing_ok=True)                      # never a plain-text password left behind
    # mosquitto reads the users file again on a reload (SIGHUP); a restart when it was not running.
    # When the device uses another broker, the switch back to the local one restarts it then.
    if in_use:
        run(["systemctl", "reload-or-restart", "mosquitto"], capture_output=True, timeout=60)
    return ""


def mqtt_login(host, port, user, password, timeout=5):
    """One MQTT 3.1.1 CONNECT with the login, read back from the CONNACK. "" when the broker accepted
    it, else why not. Plain sockets, so it works without paho in root's Python."""
    def utf8(t):
        b = t.encode("utf-8")
        return len(b).to_bytes(2, "big") + b
    flags, payload = 0x02, utf8(f"armonico-restore-test-{os.getpid()}")
    if user:
        flags |= 0x80 | 0x40
        payload += utf8(user) + utf8(password)
    body = utf8("MQTT") + bytes([4, flags]) + (10).to_bytes(2, "big") + payload
    length, n = b"", len(body)
    while True:
        byte, n = n % 128, n // 128
        length += bytes([byte | (0x80 if n else 0)])
        if not n:
            break
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x10" + length + body)
            ack = b""
            while len(ack) < 4:
                chunk = sock.recv(4 - len(ack))
                if not chunk:
                    break
                ack += chunk
            try:
                sock.sendall(b"\xe0\x00")                   # DISCONNECT
            except OSError:
                pass
    except (OSError, ValueError) as e:
        return f"the broker at {host}:{port} cannot be reached ({e})"
    if len(ack) < 4 or ack[0] != 0x20:
        return f"the broker at {host}:{port} did not answer as a broker"
    return {0: "", 4: "wrong user name or password", 5: "not authorized"}.get(
        ack[3], f"the broker refused the login (code {ack[3]})")


def broker_check(paths, mosq_dir=None, run=subprocess.run, login=mqtt_login, tries=10):
    """sync_broker, then each restored login tried for up to tries seconds (mosquitto reloading)."""
    fault = sync_broker(paths, mosq_dir, run)
    if fault:
        return fault
    cfg = _read_env_file(paths.etc / "config.env")
    host, port = cfg.get("MQTT_HOST", ""), cfg.get("MQTT_PORT") or "1883"
    if not host:
        return ""
    logins = [(cfg.get("MQTT_USER", ""), cfg.get("MQTT_PASS", ""))]
    if cfg.get("MQTT_REC_USER") and cfg.get("MQTT_REC_USER") != cfg.get("MQTT_USER"):
        logins.append((cfg["MQTT_REC_USER"], cfg.get("MQTT_REC_PASS", "")))
    # the local broker gets a few seconds to read its users file again; another broker answers once
    if host not in ("127.0.0.1", "localhost", "::1"):
        tries = 1
    for user, pw in logins:
        for i in range(tries):
            fault = login(host, port, user, pw)
            if not fault:
                break
            if i + 1 < tries:
                time.sleep(1)
        if fault:
            return f"{user or 'no user'}: {fault}"
    return ""


def engine_answers(port, host="127.0.0.1"):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True                                   # it answered, even if with 401 or 404
    except (OSError, ValueError):
        return False


# ----------------------------------------------------------------------
# Restore and undo
# ----------------------------------------------------------------------
def _as_user(paths):
    """(uid, gid) to run the data folder's part under, or None to run it here."""
    if os.geteuid() != 0:
        return None
    try:
        st = paths.data.stat()
    except OSError:
        return None
    return None if st.st_uid == 0 else (st.st_uid, st.st_gid)


def _worker(paths, op, args, stdin=None, progress=None, payload=None):
    """Runs a data folder or network step as the service user, in a child process, and relays
    its steps. Without root it runs right here."""
    who = _as_user(paths)
    if who is None:
        return _worker_main(op, args, stdin, progress, paths, payload)
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "DATA_DIR": str(paths.data),
           "SONGS_DIR": str(paths.songs), "ARMONICO_ETC_DIR": str(paths.etc), "ARMONICO_APP_DIR": str(paths.app),
           "HOME": str(paths.data)}
    with tempfile.TemporaryFile() as err_out:
        if payload is not None:
            # passwords travel on stdin, never on the command line
            stdin = tempfile.TemporaryFile()
            stdin.write(json.dumps(payload).encode())
            stdin.seek(0)
        proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "_worker", op, *args],
                                stdin=stdin if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=err_out, env=env, user=who[0], group=who[1], extra_groups=[], cwd="/")
        result = error = None
        for line in proc.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "step" in m and progress:
                progress(m["step"], m["en"], m["he"])
            elif "result" in m:
                result = m["result"]
            elif "error" in m:
                error = BackupError(m["error"], m["en"], m["he"])
        proc.wait()
        if payload is not None:
            stdin.close()
        if error:
            raise error
        if proc.returncode != 0 and result is None:
            err_out.seek(0)
            tail = err_out.read()[-400:].decode("utf-8", "replace").strip()
            raise BackupError("worker", f"The step '{op}' failed: {tail}", f"השלב '{op}' נכשל: {tail}")
        return result


def _worker_main(op, args, stdin, progress, paths, payload=None):
    if op == "create":
        return create(args[0], progress, paths, name=args[1] if len(args) > 1 else None)
    if op == "apply-data":
        return apply_data(stdin, paths, partial=args[0] == "partial")
    if op == "upload":
        t = transport_from(payload, paths)
        return create(payload["path"], progress, paths, transport=t)
    if op == "download":
        t = transport_from(payload, paths)
        _step(progress, "download")
        paths.work.mkdir(parents=True, exist_ok=True)
        local = paths.work / ("in-" + Path(payload.get("file") or "download").name)
        local.unlink(missing_ok=True)
        try:
            t.get(payload.get("file") or "", str(local))
        except BaseException:
            local.unlink(missing_ok=True)                     # a partial download never stays on the card
            raise
        return str(local)
    if op == "prune":
        return prune(paths, int(args[0]) if args and args[0].isdigit() else KEEP_BEFORE)
    raise BackupError("bad_request", *msg("bad_request"))


def prune(paths, keep=KEEP_BEFORE):
    """The newest keep before-restore files stay; older ones go, and so does what a snapshot that
    stopped part way left behind (its .part file)."""
    for p in paths.backups.glob(".before-restore-*" + SUFFIX + ".part"):
        p.unlink(missing_ok=True)
    old = sorted(paths.backups.glob("before-restore-*" + SUFFIX), key=lambda p: p.stat().st_mtime)
    gone = old[:-keep] if keep > 0 else old
    for p in gone:
        p.unlink(missing_ok=True)
    return len(gone)


def _only_warnings(progress):
    """The snapshot's own steps stay quiet under "snapshot"; what it left out is still shown."""
    if not progress:
        return None
    return lambda key, en, he: progress(key, en, he) if str(key).startswith("warn_") else None


def _apply(src, paths, partial, services, progress, notes=None):
    services.stop()
    try:
        _step(progress, "restore")
        src.seek(0)
        _worker(paths, "apply-data", ["partial" if partial else "full"], stdin=src)
        if not partial:
            _step(progress, "restore_connection")
            src.seek(0)
            apply_config(src, paths)
            # the restored broker logins into the local broker's users file, then a real login with them
            broker = getattr(services, "broker", None)
            fault = broker() if broker else ""
            if fault:
                _warn(progress, "broker", detail=fault)
                if notes is not None:
                    notes["broker"] = fault
    except Exception as e:
        # stopped part way (a full restore had already deleted files): the services always come up
        # again, and restore() goes back to the before-restore file
        services.start()
        return False, (getattr(e, "en", "") or f"{type(e).__name__}: {e}")[:200]
    _step(progress, "restart")
    services.start()
    return services.check()


def _open_backup(path):
    """Opens the backup once, without following a link, and every later step reads this handle."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    return os.fdopen(fd, "rb")


def restore(path, progress=None, paths=None, services=None, snapshot=True):
    """Restores a backup file here. Returns a dict for the screen: ok, key, en, he, before, rolled_back.
    Raises BackupError when the file is refused; nothing has changed then."""
    paths = _paths(paths)
    services = services or Systemd(paths)
    _step(progress, "check")
    try:
        src = _open_backup(path)
    except OSError:
        raise BackupError("missing", *msg("missing", path=path))
    with src:
        chk = validate(src, paths)
        if not chk.ok:
            raise BackupError(chk.reason_key, chk.reason_en, chk.reason_he)
        before = None
        if snapshot:
            _step(progress, "snapshot")
            # room for the new one first: the older ones beyond what is kept go before it is made
            _worker(paths, "prune", [str(KEEP_BEFORE - 1)])
            name = backup_name("before-restore")
            try:
                before = _worker(paths, "create", [str(paths.backups), name], progress=_only_warnings(progress))
            except BackupError:
                _worker(paths, "prune", [str(KEEP_BEFORE)])   # nothing of a failed snapshot stays
                raise
        notes = {}
        ok, detail = _apply(src, paths, chk.info["partial"], services, progress, notes)
    result = {"ok": ok, "before": before, "rolled_back": False, "file": Path(str(path)).name, "info": chk.info}
    if not ok and before:
        _step(progress, "rollback")
        with _open_backup(before) as back:
            ok2, detail2 = _apply(back, paths, False, services, None)
        result.update(rolled_back=ok2, key="rolled_back" if ok2 else "failed")
        en, he = msg("rolled_back") if ok2 else msg("failed", detail=detail2)
    elif not ok:
        result["key"] = "failed"
        en, he = msg("failed", detail=detail)
    elif notes.get("broker"):
        # everything came up, but the connection did not: said as it is, not as "everything came up"
        result["key"] = "restored_broker"
        en, he = msg("restored_broker", detail=notes["broker"])
        detail = notes["broker"]
    else:
        result["key"] = "restored"
        en, he = msg("restored")
    result.update(en=en, he=he, detail=detail)
    if snapshot:
        save_state(paths, {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                           "at_hm": datetime.now().strftime("%H:%M"), "file": result["file"], "before": before,
                           "result": result["key"], "undone": False})
        _worker(paths, "prune", [])
    return result


def last_restore(paths=None):
    """The last restore, for the screen's "Restored at …" and its undo; None when there is none."""
    paths = _paths(paths)
    try:
        st = json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return None
    st["can_undo"] = bool(st.get("result") == "restored" and not st.get("undone")
                          and st.get("before") and Path(st["before"]).is_file())
    return st


def undo_last(progress=None, paths=None, services=None):
    """Goes back to the state before the last restore, from the before-restore file made then."""
    paths = _paths(paths)
    st = last_restore(paths)
    if not st or not st["can_undo"]:
        raise BackupError("no_undo", *msg("no_undo"))
    _step(progress, "undo")
    result = restore(st["before"], progress, paths, services, snapshot=False)
    if result["ok"]:
        result.update(key="undone", en=msg("undone")[0], he=msg("undone")[1])
    if result["ok"]:                                       # a failed undo can be tried again
        st.pop("can_undo", None)
        st.update(undone=True, undone_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        save_state(paths, st)
    return result


def save_state(paths, data):
    _root_safe_write(paths.backups, paths.state.name, json.dumps(data, ensure_ascii=False, indent=1))


def _root_safe_write(folder, name, text):
    """Writes a small file into the service user's folder. As root, through the folder's own
    handle and never through a link, so nothing the service user placed there can redirect it."""
    folder = Path(folder)
    if os.geteuid() != 0:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{name}.tmp"
        tmp.write_text(text)
        tmp.replace(folder / name)
        return
    if not folder.exists():
        folder.mkdir(mode=0o750)
        st = folder.parent.stat()
        os.chown(folder, st.st_uid, st.st_gid)          # the service user's, like the data folder
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = f".{name}.{os.getpid()}.tmp"
        try:
            os.unlink(tmp, dir_fd=dfd)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        # readable by the screen: the service's UMask=0027 made it 0640 root:root, so the screen never
        # saw the job's steps and its bar stayed empty while the backup itself finished
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)


# ----------------------------------------------------------------------
# Transports
# ----------------------------------------------------------------------
class TransportError(BackupError):
    pass


ERR = {
    "unreachable": ("Cannot reach the server: check the address and the port", "אין חיבור לשרת: לבדוק כתובת ופורט"),
    "login": ("The server refused the user name or the password", "השרת לא קיבל את שם המשתמש או הסיסמה"),
    "timeout": ("The server did not answer in time", "השרת לא ענה בזמן"),
    "not_found": ("Not found on the server: {path}", "לא נמצא בשרת: {path}"),
    "share": ("There is no shared folder by that name", "אין תיקייה משותפת בשם הזה"),
    "denied": ("No permission there: {path}", "אין הרשאה שם: {path}"),
    "tool": ("{tool} is not installed. Run: sudo apt install {tool}", "{tool} לא מותקן. להריץ: sudo apt install {tool}"),
    "hostkey": ("The server's SSH key is not the one it had before. If that is expected, remove the server from {file}",
                "מפתח ה-SSH של השרת השתנה מאז הפעם הקודמת. אם זה צפוי, למחוק את השרת מ-{file}"),
    "download_only": ("A direct link only downloads; a backup cannot be saved to it",
                      "קישור ישיר רק מוריד, אי אפשר לשמור אליו גיבוי"),
    "bad_url": ("The link has to start with http:// or https://", "הקישור צריך להתחיל ב-http:// או https://"),
    "http": ("The server answered {code}", "השרת ענה {code}"),
    "too_large": ("Not enough free space on the device for the file: there is room for {mb} MB",
                  "אין במכשיר מספיק מקום פנוי לקובץ: יש מקום ל-{mb}MB"),
    "bad_name": ("This name cannot be used: {name}", "אי אפשר להשתמש בשם הזה: {name}"),
    "outside": ("That folder is not on a USB stick or in the SD card's backups folder",
                "התיקייה הזאת לא בדיסק און קי ולא בתיקיית הגיבויים בכרטיס ה-SD"),
    "failed": ("The transfer failed: {detail}", "ההעברה נכשלה: {detail}"),
}


def terr(key, **kw):
    en, he = ERR[key]
    return TransportError(key, en.format(**kw), he.format(**kw))


class Transport:
    label = ""

    def test(self):
        """True when the place answers; raises TransportError with the reason otherwise."""
        self.listdir("")
        return True

    def listdir(self, path):
        raise NotImplementedError

    def get(self, remote, local):
        raise NotImplementedError

    def put(self, local, remote):
        raise NotImplementedError


def _sorted(entries):
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


USB_FS = ("vfat", "exfat", "ext4", "ntfs")      # what the Pi reads and writes; anything else is offered a format


def usb_drives(lsblk=None):
    """USB storage the kernel sees, mounted or not (Pi OS Lite mounts nothing by itself):
    [{dev, disk, name, size, fs, label, mount, usable}], one per partition, or the disk itself
    when it has none. `lsblk` is its JSON text, for tests."""
    if lsblk is None:
        try:
            lsblk = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,TRAN,SIZE,FSTYPE,LABEL,MOUNTPOINT"],
                                   capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    try:
        disks = json.loads(lsblk).get("blockdevices") or []
    except ValueError:
        return []
    out = []
    for d in disks:
        if d.get("type") != "disk" or (d.get("tran") or "") != "usb":
            continue
        parts = [c for c in d.get("children") or [] if c.get("type") == "part"] or [d]
        for p in parts:
            fs = (p.get("fstype") or "").lower()
            size = int(p.get("size") or 0)
            out.append({"dev": p.get("path") or "/dev/" + p["name"], "disk": d.get("path") or "/dev/" + d["name"],
                        "name": p.get("label") or p["name"], "size": size, "size_text": _size_text(size),
                        "fs": fs, "label": p.get("label") or "", "mount": p.get("mountpoint") or "",
                        "usable": fs in USB_FS})
    return out


def _size_text(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


class Local(Transport):
    """USB sticks (mounted under /media or /mnt) and the SD card's backups folder. Paths are
    absolute and have to stay inside one of those places."""
    label = "USB"

    def __init__(self, paths=None, roots=None):
        self.paths = _paths(paths)
        self.roots = [Path(r) for r in (roots or ("/media", "/mnt", self.paths.backups))]

    @staticmethod
    def sticks(bases=("/media", "/mnt")):
        """The mounted USB sticks: /media/<user>/<name>, /media/<name> and /mnt/<name>."""
        found = []
        for base in bases:
            b = Path(base)
            if not b.is_dir():
                continue
            for p in sorted(b.iterdir()):
                cands = [p] + (sorted(c for c in p.iterdir() if c.is_dir()) if p.is_dir() and not os.path.ismount(p) else [])
                for c in cands:
                    try:
                        if os.path.ismount(c):
                            found.append(str(c))
                    except OSError:
                        pass
        return found

    def _inside(self, path):
        p = Path(os.path.realpath(path))
        for r in self.roots:
            r = Path(os.path.realpath(r))
            if p == r or r in p.parents:
                return p
        raise terr("outside")

    def test(self):
        for r in self.roots:
            if Path(r).is_dir():
                return True
        raise terr("not_found", path=" ".join(map(str, self.roots)))

    def listdir(self, path):
        p = self._inside(path or self.roots[0])
        try:
            out = []
            for c in p.iterdir():
                if c.name.startswith(".") or c.is_symlink():
                    continue
                st = c.stat()
                out.append(Entry(c.name, c.is_dir(), 0 if c.is_dir() else st.st_size, st.st_mtime))
            return _sorted(out)
        except FileNotFoundError:
            raise terr("not_found", path=path)
        except PermissionError:
            raise terr("denied", path=path)

    def get(self, remote, local):
        src = self._inside(remote)
        room = _room_for(local)
        if src.stat().st_size > room:
            raise terr("too_large", mb=_mb(room))
        try:
            shutil.copyfile(src, local)
        except FileNotFoundError:
            raise terr("not_found", path=remote)
        except PermissionError:
            raise terr("denied", path=remote)

    def put(self, local, remote):
        dst = Path(remote)
        if dst.is_dir():
            dst = dst / Path(local).name
        dst = self._inside(dst.parent) / dst.name
        tmp = dst.with_name("." + dst.name + ".part")
        try:
            shutil.copyfile(local, tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            tmp.replace(dst)
        except PermissionError:
            raise terr("denied", path=str(dst.parent))
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise terr("failed", detail=e.strerror or str(e))


def _clean_name(path):
    if any(c in path for c in '"\n\r;\x00'):
        raise terr("bad_name", name=path)
    return path


class Smb(Transport):
    """A Windows or NAS shared folder, through the smbclient command. host may carry the share
    (//192.168.1.30/share); without one, the top folder lists the shares."""
    label = "SMB"

    def __init__(self, host, user="", password="", port=445, share="", domain="", timeout=TIMEOUT):
        h = host.strip().replace("\\", "/").strip("/")
        h = re.sub(r"^smb:/+", "", h)
        parts = [x for x in h.split("/") if x]
        self.host = parts[0] if parts else ""
        self.share = share or (parts[1] if len(parts) > 1 else "")
        self.base = "/".join(parts[2:])
        if "@" in self.host or not self.host:
            raise terr("unreachable")
        self.user, self.password, self.domain = user, password, domain
        self.port, self.timeout = int(port or 445), timeout

    def _split(self, path):
        p = "/".join(x for x in (self.base, (path or "").strip("/")) if x)
        if self.share:
            return self.share, p
        share, _, rest = p.partition("/")
        return share, rest

    def _run(self, share, cmd, listing=False):
        with tempfile.NamedTemporaryFile("w", prefix="smb-", delete=True) as auth:
            os.chmod(auth.name, 0o600)
            args = ["smbclient", "-p", str(self.port), "-t", str(self.timeout)]
            if self.user:
                auth.write(f"username = {self.user}\npassword = {self.password}\n")
                if self.domain:
                    auth.write(f"domain = {self.domain}\n")
                auth.flush()
                args += ["-A", auth.name]
            else:
                args += ["-N"]
            args += ["-L", f"//{self.host}", "-g"] if listing else [f"//{self.host}/{share}", "-c", cmd]
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout * 6 + 600)
            except FileNotFoundError:
                raise terr("tool", tool="smbclient")
            except subprocess.TimeoutExpired:
                raise terr("timeout")
        out = r.stdout + r.stderr
        bad = re.search(r"NT_STATUS_[A-Z_]+", out)
        if r.returncode != 0 or (bad and not listing):
            code = bad.group(0) if bad else ""
            if code in ("NT_STATUS_LOGON_FAILURE", "NT_STATUS_ACCOUNT_DISABLED", "NT_STATUS_WRONG_PASSWORD"):
                raise terr("login")
            if code == "NT_STATUS_BAD_NETWORK_NAME":
                raise terr("share")
            if code in ("NT_STATUS_OBJECT_NAME_NOT_FOUND", "NT_STATUS_OBJECT_PATH_NOT_FOUND", "NT_STATUS_NO_SUCH_FILE"):
                raise terr("not_found", path=cmd)
            if code == "NT_STATUS_ACCESS_DENIED":
                raise terr("denied", path=share)
            if code in ("NT_STATUS_IO_TIMEOUT",):
                raise terr("timeout")
            if code in ("NT_STATUS_HOST_UNREACHABLE", "NT_STATUS_CONNECTION_REFUSED", "NT_STATUS_UNSUCCESSFUL",
                        "NT_STATUS_NETWORK_UNREACHABLE", "NT_STATUS_INVALID_PARAMETER") or not code:
                raise terr("unreachable")
            raise terr("failed", detail=code)
        return r.stdout

    def test(self):
        if self.share:
            self._run(self.share, "ls")
        else:
            self._run("", "", listing=True)
        return True

    def listdir(self, path):
        share, rest = self._split(path)
        if not share:
            return _sorted(parse_smb_shares(self._run("", "", listing=True)))
        cmd = f'cd "{_clean_name(rest)}"; ls' if rest else "ls"
        return _sorted(parse_smb_ls(self._run(share, cmd)))

    def get(self, remote, local):
        share, rest = self._split(remote)
        self._run(share, f'get "{_clean_name(rest)}" "{_clean_name(local)}"')

    def put(self, local, remote):
        share, rest = self._split(remote)
        self._run(share, f'put "{_clean_name(local)}" "{_clean_name(rest)}"')


SMB_LS = re.compile(r"^  (.+?)\s+([A-Z]*)\s+(\d+)\s+(\w{3}\s+\w{3}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+\d{4})\s*$")


def parse_smb_ls(text):
    out = []
    for line in text.splitlines():
        m = SMB_LS.match(line)
        if not m or m.group(1) in (".", ".."):
            continue
        try:
            mtime = time.mktime(time.strptime(re.sub(r"\s+", " ", m.group(4)), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            mtime = 0
        is_dir = "D" in m.group(2)
        out.append(Entry(m.group(1), is_dir, 0 if is_dir else int(m.group(3)), mtime))
    return out


def parse_smb_shares(text):
    return [Entry(p[1], True, 0, 0) for p in (line.split("|") for line in text.splitlines())
            if len(p) >= 2 and p[0] == "Disk" and not p[1].endswith("$")]


class Scp(Transport):
    """A Linux machine over SSH, with a password: SFTP through paramiko when it is installed,
    otherwise through sshpass and the sftp command."""
    label = "SCP"

    def __init__(self, host, user="", password="", port=22, timeout=TIMEOUT, known_hosts=None, use_paramiko=None):
        self.host, self.user, self.password = host.strip(), user, password
        self.port, self.timeout = int(port or 22), timeout
        self.known_hosts = str(known_hosts or (_paths(None).backups / "known_hosts"))
        if use_paramiko is None:
            try:
                import paramiko  # noqa: F401
                use_paramiko = True
            except ImportError:
                use_paramiko = False
        self.use_paramiko = use_paramiko

    # paramiko
    def _sftp(self):
        import paramiko
        c = paramiko.SSHClient()
        try:
            c.load_host_keys(self.known_hosts)
        except (OSError, IOError):
            pass
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(self.host, port=self.port, username=self.user, password=self.password, timeout=self.timeout,
                      banner_timeout=self.timeout, auth_timeout=self.timeout, allow_agent=False, look_for_keys=False)
        except paramiko.BadHostKeyException:
            raise terr("hostkey", file=self.known_hosts)
        except paramiko.AuthenticationException:
            raise terr("login")
        except (socket.timeout, TimeoutError):
            raise terr("timeout")
        except (OSError, paramiko.SSHException):
            raise terr("unreachable")
        try:
            Path(self.known_hosts).parent.mkdir(parents=True, exist_ok=True)
            c.save_host_keys(self.known_hosts)
        except OSError:
            pass
        s = c.open_sftp()
        s.get_channel().settimeout(self.timeout * 6)
        return c, s

    def _paramiko(self, fn):
        c, s = self._sftp()
        try:
            return fn(s)
        except FileNotFoundError as e:
            raise terr("not_found", path=str(e.filename or ""))
        except PermissionError as e:
            raise terr("denied", path=str(e.filename or ""))
        except (socket.timeout, TimeoutError):
            raise terr("timeout")
        except OSError as e:
            raise terr("failed", detail=str(e))
        finally:
            c.close()

    # sshpass and sftp
    def _batch(self, commands):
        with tempfile.NamedTemporaryFile("w", prefix="sftp-", suffix=".txt") as b:
            b.write("\n".join(commands) + "\n")
            b.flush()
            target = f"{self.user}@{self.host}" if self.user else self.host
            # BatchMode=no comes before -b, which turns it on: ssh keeps the first value it sees
            args = ["sshpass", "-e", "sftp", "-oBatchMode=no", "-oStrictHostKeyChecking=accept-new",
                    f"-oUserKnownHostsFile={self.known_hosts}", f"-oConnectTimeout={self.timeout}",
                    "-oPubkeyAuthentication=no", "-oPreferredAuthentications=password,keyboard-interactive",
                    f"-oServerAliveInterval={self.timeout}", "-oServerAliveCountMax=3",
                    "-P", str(self.port), "-b", b.name, target]
            try:
                Path(self.known_hosts).parent.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout * 6 + 600,
                                   env={**os.environ, "SSHPASS": self.password})
            except FileNotFoundError:
                raise terr("tool", tool="sshpass")
            except subprocess.TimeoutExpired:
                raise terr("timeout")
        out = r.stdout + r.stderr
        if r.returncode != 0:
            if r.returncode == 5 or "Permission denied" in r.stderr and "remote" not in r.stderr.lower():
                raise terr("login")
            if r.returncode == 6 or "Host key verification failed" in out or "IDENTIFICATION HAS CHANGED" in out:
                raise terr("hostkey", file=self.known_hosts)
            if "sftp: not found" in out or "No such file or directory" in out and "sftp" in out and "exec" in out:
                raise terr("tool", tool="openssh-client")
            if re.search(r"timed out", out, re.I):
                raise terr("timeout")
            if re.search(r"Connection refused|No route to host|Could not resolve|Network is unreachable|Connection closed", out):
                raise terr("unreachable")
            m = re.search(r'(?:not found|No such file)[^\n]*', out)
            if m:
                raise terr("not_found", path=m.group(0))
            if "Permission denied" in out:
                raise terr("denied", path=out.strip().splitlines()[-1][:120])
            raise terr("failed", detail=(out.strip().splitlines() or ["sftp"])[-1][:200])
        return r.stdout

    @staticmethod
    def _q(p):
        return '"' + _clean_name(p).replace("\\", "\\\\") + '"'

    def test(self):
        if self.use_paramiko:
            return self._paramiko(lambda s: s.listdir(".")) is not None
        self._batch(["pwd"])
        return True

    def listdir(self, path):
        path = path or "."
        if self.use_paramiko:
            import stat as st
            return _sorted(Entry(a.filename, st.S_ISDIR(a.st_mode or 0), a.st_size or 0, a.st_mtime or 0)
                           for a in self._paramiko(lambda s: s.listdir_attr(path)) if not a.filename.startswith("."))
        return _sorted(e for e in parse_ls_l(self._batch([f"cd {self._q(path)}", "ls -ln"])) if not e.name.startswith("."))

    def get(self, remote, local):
        if self.use_paramiko:
            def fetch(s):
                room = _room_for(local)
                if s.stat(remote).st_size > room:
                    raise terr("too_large", mb=_mb(room))
                s.get(remote, local)
            return self._paramiko(fetch)
        self._batch([f"get {self._q(remote)} {self._q(local)}"])

    def put(self, local, remote):
        if self.use_paramiko:
            def send(s):
                s.put(local, remote + ".part")
                try:
                    s.posix_rename(remote + ".part", remote)
                except OSError:
                    s.rename(remote + ".part", remote)
            return self._paramiko(send)
        self._batch([f"put {self._q(local)} {self._q(remote + '.part')}",
                     f"rename {self._q(remote + '.part')} {self._q(remote)}"])


LS_L = re.compile(r"^([dlcbps-])[rwxsStT-]{9}\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+(\d{1,2}:\d\d|\d{4})\s+(.+)$")


def parse_ls_l(text):
    """The long listing of sftp's "ls -l" and of FTP's LIST, in the usual Unix form."""
    out = []
    now = time.localtime()
    for line in text.splitlines():
        m = LS_L.match(line.strip())
        if not m:
            continue
        kind, size, mon, day, tm, name = m.groups()
        if kind == "l":
            name = name.split(" -> ")[0]
        if name in (".", ".."):
            continue
        try:
            if ":" in tm:
                t = time.strptime(f"{mon} {day} {now.tm_year} {tm}", "%b %d %Y %H:%M")
                mtime = time.mktime(t)
                if mtime > time.time() + 86400 * 2:          # last year's date, shown without the year
                    mtime = time.mktime(time.strptime(f"{mon} {day} {now.tm_year - 1} {tm}", "%b %d %Y %H:%M"))
            else:
                mtime = time.mktime(time.strptime(f"{mon} {day} {tm}", "%b %d %Y"))
        except ValueError:
            mtime = 0
        out.append(Entry(name, kind == "d", 0 if kind == "d" else int(size), mtime))
    return out


class Ftp(Transport):
    """An FTP server, through ftplib."""
    label = "FTP"

    def __init__(self, host, user="", password="", port=21, timeout=TIMEOUT, tls=False):
        self.host, self.user, self.password = host.strip(), user, password
        self.port, self.timeout, self.tls = int(port or 21), timeout, tls

    def _connect(self):
        import ftplib
        f = ftplib.FTP_TLS() if self.tls else ftplib.FTP()
        try:
            f.connect(self.host, self.port, timeout=self.timeout)
        except (socket.timeout, TimeoutError):
            raise terr("timeout")
        except OSError:
            raise terr("unreachable")
        try:
            f.login(self.user or "anonymous", self.password or ("anonymous@" if not self.user else ""))
            if self.tls:
                f.prot_p()
        except ftplib.error_perm:
            f.close()
            raise terr("login")
        except (ftplib.Error, OSError):
            f.close()
            raise terr("unreachable")
        f.set_pasv(True)
        return f

    def _do(self, fn, path=""):
        import ftplib
        f = self._connect()
        try:
            return fn(f)
        except ftplib.error_perm as e:
            text = str(e)
            if text.startswith("550"):
                raise terr("not_found", path=path)
            if text.startswith(("530", "532")):
                raise terr("login")
            raise terr("denied", path=path)
        except (socket.timeout, TimeoutError):
            raise terr("timeout")
        except (ftplib.Error, OSError, EOFError) as e:
            raise terr("failed", detail=str(e))
        finally:
            try:
                f.quit()
            except Exception:
                f.close()

    def test(self):
        self._do(lambda f: f.pwd())
        return True

    def listdir(self, path):
        import ftplib

        def ls(f):
            try:
                out = []
                for name, facts in f.mlsd(path or "", facts=["type", "size", "modify"]):
                    kind = facts.get("type", "")
                    if kind in ("cdir", "pdir") or name in (".", "..") or name.startswith("."):
                        continue
                    try:
                        mtime = time.mktime(time.strptime(facts.get("modify", "")[:14], "%Y%m%d%H%M%S"))
                    except ValueError:
                        mtime = 0
                    out.append(Entry(name, kind == "dir", int(facts.get("size") or 0), mtime))
                return out
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
            lines = []
            f.retrlines("LIST" + (f" {path}" if path else ""), lines.append)
            return [e for e in parse_ls_l("\n".join(lines)) if not e.name.startswith(".")]
        return _sorted(self._do(ls, path))

    def get(self, remote, local):
        def fetch(f):
            got, room = [0], _room_for(local)
            with open(local, "wb") as out:
                def write(chunk):
                    got[0] += len(chunk)
                    if got[0] > room:
                        raise terr("too_large", mb=_mb(room))
                    out.write(chunk)
                f.retrbinary(f"RETR {remote}", write)
        self._do(fetch, remote)

    def put(self, local, remote):
        def send(f):
            with open(local, "rb") as src:
                f.storbinary(f"STOR {remote}.part", src)
            try:
                f.delete(remote)
            except Exception:
                pass
            f.rename(remote + ".part", remote)
        self._do(send, remote)


class Http(Transport):
    """A direct link to a backup file, http or https, with an optional user and password.
    Download only."""
    label = "URL"

    def __init__(self, url, user="", password="", timeout=TIMEOUT):
        self.url = url.strip()
        if not re.match(r"^https?://[^/\s]+", self.url, re.I):
            raise terr("bad_url")
        self.user, self.password, self.timeout = user, password, timeout

    def _request(self, url, method="GET", headers=None):
        import base64
        import urllib.request
        req = urllib.request.Request(url or self.url, method=method, headers={"User-Agent": "Armonico-backup",
                                                                             **(headers or {})})
        if self.user:
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)
        return req

    def _open(self, req):
        import urllib.error
        import urllib.request
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise terr("login")
            if e.code == 404:
                raise terr("not_found", path=req.full_url)
            raise terr("http", code=e.code)
        except (socket.timeout, TimeoutError):
            raise terr("timeout")
        except (urllib.error.URLError, OSError):
            raise terr("unreachable")

    def _head(self):
        try:
            return self._open(self._request(None, "HEAD"))
        except TransportError as e:
            if e.key != "http":
                raise
        return self._open(self._request(None, "GET", {"Range": "bytes=0-0"}))   # a server that refuses HEAD

    def name(self):
        from urllib.parse import unquote, urlparse
        return Path(unquote(urlparse(self.url).path)).name or "download" + SUFFIX

    def test(self):
        with self._head():
            return True

    def listdir(self, path=""):
        from email.utils import parsedate_to_datetime
        with self._head() as r:
            size = int(r.headers.get("Content-Length") or 0)
            if r.status == 206:
                m = re.search(r"/(\d+)$", r.headers.get("Content-Range") or "")
                size = int(m.group(1)) if m else 0
            try:
                mtime = parsedate_to_datetime(r.headers.get("Last-Modified")).timestamp()
            except (TypeError, ValueError):
                mtime = 0
        return [Entry(self.name(), False, size, mtime)]

    def get(self, remote, local):
        url = remote if re.match(r"^https?://", remote or "", re.I) else self.url
        room = _room_for(local)
        with self._open(self._request(url)) as r, open(local, "wb") as out:
            got = 0
            while chunk := r.read(1 << 16):
                got += len(chunk)
                if got > room:
                    raise terr("too_large", mb=_mb(room))
                out.write(chunk)

    def put(self, local, remote):
        raise terr("download_only")


def transport_from(spec, paths=None):
    """A transport from the screen's choice: {"kind": local|smb|scp|ftp|http, host, port, user,
    password, url}."""
    kind = (spec or {}).get("kind")
    s = {k: str(v) for k, v in (spec or {}).items() if isinstance(v, (str, int)) and not isinstance(v, bool)}
    if kind == "local":
        return Local(paths)
    if kind == "smb":
        return Smb(s.get("host", ""), s.get("user", ""), s.get("password", ""), s.get("port") or 445, s.get("share", ""))
    if kind == "scp":
        return Scp(s.get("host", ""), s.get("user", ""), s.get("password", ""), s.get("port") or 22,
                   known_hosts=_paths(paths).backups / "known_hosts")
    if kind == "ftp":
        return Ftp(s.get("host", ""), s.get("user", ""), s.get("password", ""), s.get("port") or 21)
    if kind in ("http", "url"):
        return Http(s.get("url", ""), s.get("user", ""), s.get("password", ""))
    raise BackupError("bad_request", *msg("bad_request"))


# ----------------------------------------------------------------------
# The screen's request, carried out by root
# ----------------------------------------------------------------------
def request(action, where=None, path="", file="", paths=None):
    """Called by the screen: asks the root unit for a backup ("create"), a restore ("restore")
    or an undo ("undo"). where is a transport spec (see transport_from); path is the folder to
    save in, file the backup to restore. Progress and the result appear in job_status()."""
    paths = _paths(paths)
    job_id = f"{int(time.time() * 1000):x}"
    body = {"id": job_id, "action": action, "where": where or {"kind": "local"}, "path": path, "file": file}
    tmp = paths.request.with_name(".device-backup-request.tmp")
    old = os.umask(0o077)
    try:
        tmp.write_text(json.dumps(body))
    finally:
        os.umask(old)
    tmp.replace(paths.request)
    return job_id


def job_status(paths=None):
    paths = _paths(paths)
    try:
        return json.loads(paths.job.read_text())
    except (OSError, ValueError):
        return None


class Job:
    """The job file the screen reads: every step with its state, then the result."""

    def __init__(self, paths, req):
        self.paths = paths
        self.data = {"id": str(req.get("id", ""))[:32], "action": req.get("action"), "state": "running",
                     "steps": [], "result": None, "started": time.time(), "updated": time.time()}
        self.write()

    def write(self):
        self.data["updated"] = time.time()
        _root_safe_write(self.paths.backups, self.paths.job.name, json.dumps(self.data, ensure_ascii=False, indent=1))

    def progress(self, key, en, he):
        for s in self.data["steps"]:
            if s["state"] == "running":
                s["state"] = "done"
        # a warning (files left out, a broker that refused the login) stays on the list marked as not done
        warn = str(key).startswith("warn_")
        self.data["steps"].append({"key": key, "en": en, "he": he, "state": "failed" if warn else "running"})
        self.write()

    def finish(self, ok, result):
        for s in self.data["steps"]:
            if s["state"] == "running":
                s["state"] = "done" if ok else "failed"
        self.data.update(state="done" if ok else "failed", result=result)
        self.write()


def _read_request(paths):
    """Reads and removes the request. It is the service user's file, so it is read as data only."""
    try:
        fd = os.open(paths.request, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        raw = f.read(65536)
    paths.request.unlink(missing_ok=True)
    try:
        req = json.loads(raw)
    except ValueError:
        return {}
    return req if isinstance(req, dict) else {}


def _local_dest(paths, folder):
    """Where a local backup goes: the SD folder (written by the service user), or a USB stick (by root)."""
    folder = Path(os.path.realpath(folder or paths.backups))
    sd = Path(os.path.realpath(paths.backups))
    return folder, (folder == sd or sd in folder.parents)


def run_request(paths=None, services=None):
    """The root side: one request from the screen, carried out, with its steps in the job file."""
    paths = _paths(paths)
    lock_dir = Path("/run") if os.geteuid() == 0 and Path("/run").is_dir() else paths.data
    with open(lock_dir / "armonico-device-backup.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None               # one is running; the request stays, and the path unit starts it after
        req = _read_request(paths)
        if req is None:
            return None
        job = Job(paths, req)
        try:
            result = _carry_out(req, paths, services, job.progress)
            job.finish(bool(result.get("ok", True)), result)
        except BackupError as e:
            job.finish(False, {"ok": False, "key": e.key, "en": e.en, "he": e.he})
        except Exception as e:                       # shown on the screen rather than lost in the journal
            job.finish(False, {"ok": False, "key": "error", "en": f"Unexpected error: {e}", "he": f"שגיאה לא צפויה: {e}"})
        return job.data


def _carry_out(req, paths, services, progress):
    action = req.get("action")
    where = req.get("where") if isinstance(req.get("where"), dict) else {"kind": "local"}
    kind = where.get("kind", "local")
    if action == "undo":
        return undo_last(progress, paths, services)
    if action == "create":
        if kind != "local":
            payload = {**where, "path": str(req.get("path") or "")}
            remote = _worker(paths, "upload", [], progress=progress, payload=payload)
            return {"ok": True, "key": "saved", "file": remote, **dict(zip(("en", "he"), msg("saved", name=remote)))}
        folder, on_sd = _local_dest(paths, req.get("path"))
        if on_sd:
            out = _worker(paths, "create", [str(folder)], progress=progress)
        else:
            Local(paths)._inside(folder)                       # a USB stick, or refused here
            built = _worker(paths, "create", [str(paths.work)], progress=progress)
            try:
                with _open_backup(built) as src:
                    out = str(_free_name(folder, Path(built).name))
                    need_room(folder, os.fstat(src.fileno()).st_size, reserve=1048576)   # a stick keeps nothing back
                    _root_safe_copy(src, Path(out))
                _step(progress, "verify")
                chk = validate(out, paths)
                if not chk.ok:
                    _root_unlink(folder, Path(out).name)        # a copy that failed its check is never kept
                    raise BackupError("verify", chk.reason_en, chk.reason_he)
            finally:
                _root_unlink(paths.work, Path(built).name)
        return {"ok": True, "key": "saved", "file": out, **dict(zip(("en", "he"), msg("saved", name=Path(out).name)))}
    if action == "restore":
        if kind != "local":
            payload = {**where, "file": str(req.get("file") or "")}
            got = _worker(paths, "download", [], progress=progress, payload=payload)
            try:
                return restore(got, progress, paths, services)
            finally:
                _root_unlink(paths.work, Path(got).name)
        file = str(req.get("file") or "")
        if not file:
            raise BackupError("bad_request", *msg("bad_request"))
        Local(paths)._inside(file)
        return restore(file, progress, paths, services)
    raise BackupError("bad_request", *msg("bad_request"))


def _root_safe_copy(src, out):
    """Copies src into out on a USB stick as root. An ext4 stick belongs to the service user, so
    through the folder's own handle, a temp name of its own and never through a link: nothing
    placed on the stick can redirect the write to another file."""
    dfd = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = f".{out.name}.{os.getpid()}.{secrets.token_hex(4)}.part"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(tmp, out.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _root_unlink(folder, name):
    try:
        dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return
    try:
        os.unlink(name, dir_fd=dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


# ----------------------------------------------------------------------
def _print_step(key, en, he):
    print(f"  {en}", flush=True)


def _worker_entry(argv):
    """python backup.py _worker <op> [args]: run by _worker() as the service user. Steps and the
    result go out on stdout, one JSON object per line."""
    op, args = argv[0], argv[1:]
    out = sys.stdout

    def progress(key, en, he):
        out.write(json.dumps({"step": key, "en": en, "he": he}) + "\n")
        out.flush()
    payload = None
    stdin = None
    if op in ("upload", "download"):
        payload = json.loads(sys.stdin.read() or "{}")
    elif op == "apply-data":
        stdin = os.fdopen(os.dup(0), "rb")
    try:
        result = _worker_main(op, args, stdin, progress, Paths.from_env(), payload)
        out.write(json.dumps({"result": result}) + "\n")
    except BackupError as e:
        out.write(json.dumps({"error": e.key, "en": e.en, "he": e.he}) + "\n")
        return 1
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.strip())
        return 2
    cmd = argv[0]
    try:
        if cmd == "_worker":
            return _worker_entry(argv[1:])
        if cmd == "run-request":
            r = run_request()
            if r:
                print(f"{r.get('action')}: {r.get('state')} {(r.get('result') or {}).get('en', '')}")
            return 0
        if cmd == "create":
            print(create(argv[1] if len(argv) > 1 else str(Paths.from_env().backups), _print_step))
            return 0
        if cmd == "validate" and len(argv) > 1:
            chk = validate(argv[1])
            print(json.dumps({"ok": chk.ok, "reason": chk.reason_en, "info": chk.info}, indent=1, ensure_ascii=False))
            return 0 if chk.ok else 1
        if cmd == "restore" and len(argv) > 1:
            r = restore(argv[1], _print_step)
            print(r["en"])
            return 0 if r["ok"] else 1
        if cmd == "undo":
            r = undo_last(_print_step)
            print(r["en"])
            return 0 if r["ok"] else 1
    except BackupError as e:
        print(e.en, file=sys.stderr)
        return 1
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
