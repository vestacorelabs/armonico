#!/usr/bin/env python3
"""screen_rec.py - the screen recordings moved from the SD card to a USB stick (root).

The recording itself is made by the screen program (device/ui/recorder.py), into the data folder's
device/recordings. This part runs as root, since Raspberry Pi OS Lite mounts no stick by itself:
it mounts the first usable USB stick (never the disk the Pi runs from) under /media/armonico and moves
the files into "Armonico recordings" on it: copied, written to the stick for good, checked by size,
and only then removed from the SD card.

The recordings folder belongs to the screen's user and the stick to anyone who plugs one in, so root
goes through both by folder handles opened without following a link, reads only regular files, and
writes a new file that cannot exist already: a link planted in either place leads nowhere.

  export [name...]   the named recordings (or all of them) to a stick; prints how many moved
  list               the recordings on the SD card
"""
import errno
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
REC_PARTS = ("device", "recordings")
STICK_FOLDER = "Armonico recordings"
NAME = re.compile(r"armonico-\d{4}-\d\d-\d\d-\d{6}\.mkv")


def open_dir(base, parts):
    """A handle on base/parts..., each step opened without following a link."""
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError:
        os.close(fd)
        raise
    return fd


def recordings(dfd):
    """The recordings in the folder: regular files with the recorder's own names, oldest first."""
    out = []
    for name in sorted(os.listdir(dfd)):
        if not NAME.fullmatch(name):
            continue
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            out.append((name, st.st_size))
    return out


def root_actions():
    spec = importlib.util.spec_from_file_location("armonico_root_actions_rec", APP_DIR / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_offset(where):
    """Whether a mount already carries time_offset (from /proc/mounts)."""
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            f = line.split()
            if len(f) > 3 and f[1] == str(where):
                return "time_offset=" in f[3] or "tz=" in f[3]
    except OSError:
        pass
    return True                                  # cannot tell: left as it is


def stick():
    """The folder on a USB stick the recordings go to (mounted when it was not), or None."""
    ra = root_actions()
    for d in ra.backup_mod().usb_drives():
        if not d["usable"] or ra.system_disk(d["disk"]):
            continue
        where = Path(ra.mount_usb(d))
        if d["fs"] == "vfat" and d["mount"] and not _has_offset(where):
            # opened before the device's offset was given to FAT sticks (an older version, or by hand):
            # opened again with it, or Windows shows the files' times hours off
            if ra.run("umount", str(where), timeout=30).returncode == 0:
                d = dict(d, mount="")
                where = Path(ra.mount_usb(d))
        try:
            os.mkdir(where / STICK_FOLDER)
        except FileExistsError:
            pass
        folder = where / STICK_FOLDER
        if folder.is_symlink() or not folder.is_dir():
            continue
        return folder
    return None


def move(src_fd, files, folder):
    """The files moved from the recordings folder to the stick's folder. Returns how many."""
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    moved = 0
    try:
        need = sum(size for _, size in files)
        if shutil.disk_usage(folder).free < need + 10 * 1000 * 1000:
            raise RuntimeError("the USB stick is full")
        for name, _ in files:
            part = name + ".part"
            try:
                os.unlink(part, dir_fd=dfd)      # a copy cut off earlier, or anything planted under its name
            except FileNotFoundError:
                pass
            sfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_fd)
            try:
                st = os.fstat(sfd)
                if not stat.S_ISREG(st.st_mode):
                    continue
                ofd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
                try:
                    with os.fdopen(sfd, "rb", closefd=False) as src, os.fdopen(ofd, "wb") as dst:
                        shutil.copyfileobj(src, dst, 4 << 20)
                        dst.flush()
                        os.fsync(dst.fileno())
                        copied = os.fstat(dst.fileno()).st_size
                except OSError as e:
                    try:
                        os.unlink(part, dir_fd=dfd)      # no cut-off copy is left on the stick
                    except OSError:
                        pass
                    if e.errno == errno.EFBIG:           # FAT32 holds at most 4 GB in one file; the recording stays on the SD card
                        raise RuntimeError("the recording is larger than 4 GB, more than a FAT32 USB stick holds in one file") from e
                    raise
            finally:
                os.close(sfd)
            if copied != st.st_size:
                os.unlink(part, dir_fd=dfd)
                raise RuntimeError("the copy on the USB stick came out short")
            os.replace(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.unlink(name, dir_fd=src_fd)
            moved += 1
        os.fsync(dfd)
    finally:
        os.close(dfd)
    os.sync()
    return moved


def export(names=None):
    """(moved, where). names: only these recordings (the one just ended); none: all of them.
    With no stick plugged in, (0, "")."""
    try:
        src = open_dir(DATA_DIR, REC_PARTS)
    except FileNotFoundError:
        return 0, ""
    try:
        files = recordings(src)
        if names:
            files = [f for f in files if f[0] in set(names)]
        if not files:
            return 0, ""
        folder = stick()
        if folder is None:
            return 0, ""
        return move(src, files, folder), str(folder)
    finally:
        os.close(src)


def main(argv):
    if os.geteuid() != 0:
        sys.exit("root only: sudo armonico screen-rec export")
    what = argv[0] if argv else "list"
    if what == "export":
        try:
            n, where = export(argv[1:] or None)
        except Exception as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{n} recording(s) moved to {where}" if n else "Nothing moved: no recordings, or no USB stick")
        return 0
    try:
        src = open_dir(DATA_DIR, REC_PARTS)
    except FileNotFoundError:
        print("No recordings")
        return 0
    try:
        files = recordings(src)
    finally:
        os.close(src)
    print(json.dumps([n for n, _ in files]) if "--json" in argv else
          "\n".join(f"{n}  {s / 1e6:.1f} MB" for n, s in files) or "No recordings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
