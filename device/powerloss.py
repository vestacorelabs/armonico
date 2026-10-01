#!/usr/bin/env python3
"""powerloss.py - telling an unclean shutdown from a clean one, and what the recovery screen repairs.

The device keeps a "running" marker, <data>/device/running.json, written at every boot and removed by
a clean shutdown or reboot (the unit armonico-boot-mark.service: ExecStart here at boot, ExecStop at
shutdown). Found at the next boot with another boot id, it means the power went (or the kernel died):
the old marker's data is kept in <data>/device/unclean.json, the settings and learning data are put
right there and then (before any service reads them), and the screen's recovery
(device/ui/views_power.py) reads it, works through the rest and removes it. A service restarted by hand removes and
writes nothing here, and is not taken for a power loss.

The screen also keeps "where it was last", <data>/device/last.json (LastWriter): written only when the
view, the lesson or the profile changes, and as a heartbeat every 5 minutes; fsync'd at most every
2 minutes, so the SD card is not worn.

The repairs are plain functions here, with no screen in them (tests/test_power_loss.py):
  config_files, check_file, snapshot, repair   the settings and learning data, each parsed; one broken is
                                               put back from its last good copy (<data>/device/last-good/),
                                               which snapshot() keeps while the files are good
  check_songs                                  the MIDI files: a damaged one is moved to songs/_damaged/
  stash_recordings, recover_recordings         the parts of a screen recording the power cut off
  fsck_summary, ensure_fsck_repair             what the boot's file system check did; fsck.repair=yes

  powerloss.py boot | clean | snapshot | ensure-fsck [cmdline.txt]
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

RUNNING = "running.json"
LAST = "last.json"
PENDING = "unclean.json"
GOOD = "last-good"
BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"
FSYNC_EVERY = 120.0          # seconds between two fsyncs of last.json
BEAT_EVERY = 300.0           # a heartbeat write when nothing changed
CHANGE_GAP = 5.0             # at most one write per 5 seconds for changes
INTERRUPTED = ".interrupted"


def data_dir():
    return Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))


def dev_dir():
    return data_dir() / "device"


def boot_id():
    try:
        return Path(BOOT_ID_FILE).read_text().strip()
    except OSError:
        return ""


def write_json(path, obj, sync=False):
    """Written whole: a temporary file in the same folder, then renamed. sync: fsync'd too."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        if sync:
            os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path):
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------- the marker
def mark_boot(version=""):
    """At boot. "unclean" when the marker of an earlier boot is still there (its data and the last place
    go to unclean.json, and the parts of a cut-off screen recording are put aside), "clean" after a clean
    shutdown, "first" on the very first start. A marker that cannot be written (the folder is not the unit's
    user's, a full or read-only card) is logged to stderr (the journal) and never raises: the boot goes on;
    the state is then "error"."""
    try:
        return _mark_boot(version)
    except OSError as e:
        print(f"armonico-boot-mark: the running marker in {dev_dir()} could not be written ({e}); "
              "a power loss will not be noticed at the next boot", file=sys.stderr, flush=True)
        return "error"


def _mark_boot(version):
    d = dev_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = boot_id()
    state = "first"
    old = None
    if (d / RUNNING).exists():
        old = read_json(d / RUNNING)
        state = "unclean" if (old is None or old.get("boot") != now) else "clean"
    elif (d / LAST).exists():
        state = "clean"
    if state == "unclean":
        if not (d / PENDING).exists():                 # one not yet handled keeps its older, fuller data
            write_json(d / PENDING, {"at": time.time(), "running": old or {}, "last": read_json(d / LAST) or {}}, sync=True)
        stash_recordings(d / "recordings")
        # the settings and the learning data put right here, before any service reads them (this unit runs
        # before the device, the screen, the lessons and the bridge): a service that read a broken file would
        # start over from nothing, or write its own over it. The recovery screen says what was done.
        fixed = {}
        for group in ("config", "learning"):
            try:
                fixed[group] = [x for x in repair(group) if x["state"] != "ok"]
            except OSError:
                fixed[group] = []
        if any(fixed.values()):
            info = read_json(d / PENDING) or {}
            for group, res in fixed.items():
                info.setdefault("repaired", {}).setdefault(group, []).extend(res)
            write_json(d / PENDING, info, sync=True)
    else:
        try:
            snapshot()
        except OSError:
            pass
    write_json(d / RUNNING, {"boot": now, "started": time.time(), "version": version})
    return state


def mark_clean():
    """At a clean shutdown or reboot: the last good copies refreshed, then the marker removed."""
    try:
        snapshot()
    except OSError:
        pass
    try:
        (dev_dir() / RUNNING).unlink()
    except FileNotFoundError:
        pass


def pending():
    return read_json(dev_dir() / PENDING)


def clear_pending():
    try:
        (dev_dir() / PENDING).unlink()
    except FileNotFoundError:
        pass


class LastWriter:
    """last.json: {view, lesson, profile, ts}. note() is cheap and called often; it writes only on a change
    (at most every CHANGE_GAP seconds) and as a heartbeat every BEAT_EVERY seconds."""

    def __init__(self, path=None, clock=time.monotonic):
        self.path = Path(path) if path else dev_dir() / LAST
        self.clock = clock
        self.key = None
        self.wrote = -1e9
        self.synced = -1e9
        self.writes = 0

    def note(self, view, lesson="", profile=""):
        now = self.clock()
        key = (view, lesson, profile)
        due = (key != self.key and now - self.wrote >= CHANGE_GAP) or now - self.wrote >= BEAT_EVERY
        if not due:
            return False
        sync = now - self.synced >= FSYNC_EVERY
        try:
            write_json(self.path, {"view": view, "lesson": lesson, "profile": profile, "ts": time.time()}, sync=sync)
        except OSError:
            return False
        self.key, self.wrote, self.writes = key, now, self.writes + 1
        if sync:
            self.synced = now
        return True


# ---------------------------------------------------------------------- the files
def config_files(data=None):
    """[(path, kind, group)]: kind json-dict | json-list | text; group "config" or "learning". Only files
    that exist (a missing one is not a damaged one)."""
    data = Path(data) if data else data_dir()
    out = [(data / rel, kind, "config") for rel, kind in
           (("device/settings.json", "json-dict"), ("settings.json", "json-dict"),
            ("text_overrides.json", "json-dict"), ("shortcuts.conf", "text"))]
    out.append((data / "profiles.json", "json-dict", "learning"))
    bases = [data] + (sorted((data / "profiles").glob("p*")) if (data / "profiles").is_dir() else [])
    for b in bases:
        for name, kind in (("lessons.json", "json-dict"), ("history.json", "json-list"),
                           ("settings.json", "json-dict"), ("learner.json", "json-dict")):
            if b == data and name == "settings.json":
                continue                                   # the main profile's settings are listed above
            out.append((b / name, kind, "learning"))
    return [x for x in out if x[0].is_file()]


def check_file(path, kind):
    """(True, "") when the file reads and parses as its kind, else (False, why)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return False, str(e)
    if not raw.strip(b"\0 \t\r\n"):
        return False, "empty"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not text"
    if "\0" in text:
        return False, "null bytes"
    if kind == "text":
        return True, ""
    try:
        v = json.loads(text)
    except ValueError:
        return False, "does not parse"
    if kind == "json-dict" and not isinstance(v, dict):
        return False, "not an object"
    if kind == "json-list" and not isinstance(v, list):
        return False, "not a list"
    return True, ""


def good_path(path, data=None):
    data = Path(data) if data else data_dir()
    try:
        rel = Path(path).relative_to(data)
    except ValueError:
        rel = Path(Path(path).name)
    return data / "device" / GOOD / "__".join(rel.parts)


def _digest(p):
    try:
        return hashlib.sha1(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot(data=None):
    """Every file that parses is copied to last-good, only when it differs from the copy there (so the SD card
    is written only for a changed file). Returns how many were copied."""
    n = 0
    for path, kind, _ in config_files(data):
        if not check_file(path, kind)[0]:
            continue
        dst = good_path(path, data)
        if _digest(path) == _digest(dst):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name("." + dst.name + ".tmp")
        shutil.copyfile(path, tmp)
        os.replace(tmp, dst)
        n += 1
    return n


def check_group(group, data=None):
    """[{"path", "state"}] for the files of one group, nothing changed: ok | broken."""
    return [{"path": str(p), "state": "ok" if check_file(p, k)[0] else "broken"}
            for p, k, g in config_files(data) if g == group]


def repair(group, data=None):
    """Every file of the group checked and a broken one put right. Returns [{"path", "state", "why"}], state
    ok | restored (from the last good copy) | removed (broken and no good copy: set aside as .broken, the
    program starts it new) | failed."""
    res = []
    for path, kind, g in config_files(data):
        if g != group:
            continue
        good, why = check_file(path, kind)
        if good:
            res.append({"path": str(path), "state": "ok"})
            continue
        copy = good_path(path, data)
        try:
            os.replace(path, path.with_name(path.name + ".broken"))
            if copy.is_file() and check_file(copy, kind)[0]:
                shutil.copyfile(copy, path)
                res.append({"path": str(path), "state": "restored", "why": why})
            else:
                res.append({"path": str(path), "state": "removed", "why": why})
        except OSError as e:
            res.append({"path": str(path), "state": "failed", "why": str(e)})
    return res


# ---------------------------------------------------------------------- MIDI songs
def midi_ok(path):
    """A Standard MIDI file whose header and every track chunk are whole. Bytes after the last track the header
    counts are left alone: many files from the archives end with a few, and the player and the lessons read
    them as they are (mido stops at the last track). Only a track that runs past the end of the file is cut off."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return False, str(e)
    if len(raw) < 14 or raw[:4] != b"MThd" or int.from_bytes(raw[4:8], "big") < 6:
        return False, "no MIDI header"
    ntrk = int.from_bytes(raw[10:12], "big")
    pos = 8 + int.from_bytes(raw[4:8], "big")
    seen = 0
    while pos + 8 <= len(raw) and (seen < ntrk or raw[pos:pos + 4] == b"MTrk"):
        if raw[pos:pos + 4] != b"MTrk":
            return False, "bad track"
        pos += 8 + int.from_bytes(raw[pos + 4:pos + 8], "big")
        seen += 1
    if pos > len(raw) or seen < 1 or seen < ntrk:
        return False, "cut off"
    return True, ""


def check_songs(songs_dir, move=True):
    """(checked, [damaged names]). A damaged file is moved to <songs>/_damaged/ so nothing plays half a song."""
    folder = Path(songs_dir)
    bad, n = [], 0
    try:
        files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".mid", ".midi"))
    except OSError:
        return 0, []
    for p in files:
        n += 1
        if midi_ok(p)[0]:
            continue
        bad.append(p.name)
        if move:
            try:
                (folder / "_damaged").mkdir(exist_ok=True)
                os.replace(p, folder / "_damaged" / p.name)
            except OSError:
                pass
    return n, bad


# ---------------------------------------------------------------------- the screen recording
def stash_recordings(folder):
    """At boot, before the recorder starts (it deletes stale parts): the parts of a recording that was cut off
    are moved under .interrupted/, where recover_recordings finds them. Returns how many files."""
    folder = Path(folder)
    try:
        parts = list(folder.glob(".rec-*")) + list(folder.glob("armonico-*.mkv.part"))
    except OSError:
        return 0
    if not parts:
        return 0
    box = folder / INTERRUPTED
    n = 0
    try:
        box.mkdir(exist_ok=True)
    except OSError:
        return 0
    for p in parts:
        try:
            if not p.is_file() or p.is_symlink():
                continue
            if p.name.endswith((".progress", ".clean.mka")):
                p.unlink()                       # the saving's own helpers: nothing to recover in them
                continue
            os.replace(p, box / p.name)
            n += 1
        except OSError:
            pass
    try:
        if (folder / "last.log").is_file():
            shutil.copyfile(folder / "last.log", box / "last.log")
    except OSError:
        pass
    return n


def interrupted(folder):
    """{token: {"video": Path|None, "sounds": [Path], "out": Path|None}} of the stashed parts."""
    box = Path(folder) / INTERRUPTED
    groups = {}
    try:
        names = sorted(os.listdir(box))
    except OSError:
        return groups
    out = None
    try:
        lines = [x.strip() for x in (box / "last.log").read_text().splitlines() if x.strip()]
        if lines and lines[-1].endswith(".mkv"):
            out = Path(lines[-1])
    except OSError:
        pass
    for n in names:
        m = re.fullmatch(r"\.rec-([0-9a-f]+)-(video\.mkv|(?:device|mic)-\d+\.mka)", n)
        if m:
            g = groups.setdefault(m.group(1), {"video": None, "sounds": [], "out": out})
            if m.group(2) == "video.mkv":
                g["video"] = box / n
            else:
                g["sounds"].append(box / n)
        elif n.endswith(".mkv.part"):
            groups.setdefault("part-" + n, {"video": None, "sounds": [], "out": None, "part": box / n})
    return groups


def _seconds(path, run):
    try:
        r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=20)
        return float((r.stdout or "").split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


def _drop(paths):
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


def recover_recordings(folder, mux_cmd, start_of, run=subprocess.run, timeout=900):
    """Each stashed recording put together into a file when its picture is readable, otherwise removed.
    mux_cmd and start_of are the recorder's own (device/ui/recorder.py). Returns
    [{"state": "recovered" | "cleaned" | "failed", "file", "seconds"}]."""
    folder = Path(folder)
    res = []
    for g in interrupted(folder).values():
        video = g.get("video")
        paths = ([video] if video else []) + g["sounds"] + ([g["part"]] if g.get("part") else [])
        if not (video and video.is_file() and video.stat().st_size > 0):
            _drop(paths)
            res.append({"state": "cleaned", "file": "", "seconds": 0})
            continue
        out = g.get("out")
        if out is None or out.exists():
            stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(video.stat().st_mtime))
            out = folder / f"armonico-{stamp}-recovered.mkv"
        tmp = out.with_name(out.name + ".part")
        found = []
        for s in g["sounds"]:
            if s.is_file() and s.stat().st_size > 0:
                st = start_of(s)
                if st is not None:
                    found.append((s, st, "Microphone" if "mic-" in s.name else "Armonico"))
        if found:
            cmd = mux_cmd(video, min(f[1] for f in found), found, tmp)
        else:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-c", "copy",
                   "-f", "matroska", str(tmp)]
        done = False
        try:
            r = run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                done = True
        except (OSError, subprocess.SubprocessError):
            pass
        if done:
            res.append({"state": "recovered", "file": out.name, "seconds": _seconds(out, run)})
            _drop(paths)
        else:
            _drop([tmp])
            res.append({"state": "failed", "file": video.name, "seconds": 0})      # the parts stay for another try
    box = folder / INTERRUPTED
    try:
        if not [n for n in os.listdir(box) if n != "last.log"]:
            shutil.rmtree(box, ignore_errors=True)
    except OSError:
        pass
    return res


# ---------------------------------------------------------------------- the file system check at boot
def fsck_summary(text):
    """What the boot's check said, from the journal lines of systemd-fsck: ("clean" | "repaired" | "errors" |
    "unknown", the telling line)."""
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    for pat, state in ((r"UNEXPECTED INCONSISTENCY|exited with status code [48]|Failed to start File System Check|"
                        r"cannot be fixed|run fsck manually", "errors"),
                       (r"FILE SYSTEM WAS MODIFIED|recovering journal|Fixing|Fixed|Clearing orphaned|Deleted inode|"
                        r"Setting free|[Mm]arked clean|dirty bit|exited with status code 1\b", "repaired"),
                       (r"\bclean,|is clean|No errors", "clean")):
        for x in lines:
            if re.search(pat, x):
                return state, x[:160]
    return "unknown", ""


def ensure_fsck_repair(cmdline="/boot/firmware/cmdline.txt"):
    """fsck.repair=yes on the kernel command line, so the boot's check repairs what it finds without asking
    (Raspberry Pi OS has it; here it is made sure). Returns "present", "added" or "none" (no file)."""
    p = Path(cmdline)
    try:
        text = p.read_text()
    except OSError:
        return "none"
    if re.search(r"(^|\s)fsck\.repair=(yes|preen|true|1)(\s|$)", text):
        return "present"
    line = re.sub(r"(^|\s)fsck\.repair=\S+", " ", text.strip()).split("\n")[0].strip()
    bak = p.with_name(p.name + ".armonico-bak")
    if not bak.exists():
        shutil.copyfile(p, bak)
    tmp = p.with_name("." + p.name + ".armonico")
    # the boot partition, right after a power loss: everything on the card before the rename, and the rename
    # on it after, so a second cut cannot leave an empty or half command line (the Pi would not boot)
    with open(tmp, "w") as f:
        f.write(line + " fsck.repair=yes\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    try:
        fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        os.sync()
    return "added"


def main(argv):
    what = argv[0] if argv else ""
    if what == "boot":
        ver = ""
        try:
            ver = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
        except OSError:
            pass
        print(mark_boot(ver))
    elif what == "clean":
        mark_clean()
    elif what == "snapshot":
        print(snapshot())
    elif what == "ensure-fsck":
        print(ensure_fsck_repair(*argv[1:2]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
