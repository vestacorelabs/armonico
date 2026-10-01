#!/usr/bin/env python3
"""soak.py - the Armonico Pi's long load test: hours of real use, played by the test itself, measured all along.

What it plays, the way a person would: songs from the device on the keys (free play, with now and then a
neighboring key and its correction), songs played with the device, lessons answered as a learner does, the
lists browsed, the rest screen, the alarm, a screen recording, and when a broker on the network, Home
Assistant and Telegram are set up, commands from Home Assistant and from Telegram (real replies reach the
chat). Everything goes through the device's own ways in: the screen's key touches and buttons over MQTT and
the lesson engine's HTTP, so the keys' program, the bridge, the lesson engine and the screen all work as in use.

What it measures: every 5 s the CPU, each core, memory, swap, temperature, clock, disk writes, every program's
CPU, memory and threads, the lesson engine's answer and a round trip through the broker; every minute the
audio dropouts, under-voltage and throttling, services that restarted, the journal, PSS, open files, the
heaviest programs and threads, the broker's traffic and the logs' volume; for each key the time to the bridge
and to Home Assistant; at the start and at the end what the kernel reports (sensors, GPIO, USB, network,
radios, storage, display, power); and the boot time before and after.

It keeps the Pi safe: it does not start when the Pi is hot, under-voltage, short of memory or of space, and it
stops everything at once when any of that happens on the way, when a service crashes or when the device
chokes. Lessons run in a profile of their own ("Soak test"), so nobody's progress changes. At the end the
profile, the octave, the voice and the pedal are back, the temporary automations in Home Assistant and the
screen recordings it made are gone, and the report is a ZIP in /var/lib/armonico-soak (a copy in the home
folder and, from the screen, on a USB stick).

Started from the screen (Settings > System > Tests, a card shown after 7 touches on the Armonico logo in
About) or as root:

  soak.py start 4h [--no-reboot] [--no-telegram] [--no-alarm]   a reboot, then the test starts once after it
  soak.py stop | status | recover | ha-clean | report <dir>

A restart in the middle never starts it again: after the next boot only the clean-up runs (recover), and
the report says it was cut off.
"""
import argparse
import bisect
import csv
import heapq
import html
import json
import os
import random
import re
import shutil
import stat
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict, deque
from pathlib import Path

APP = "armonico"
ETC = Path(f"/etc/{APP}/config.env")
DATA = Path(f"/var/lib/{APP}")
# root's own folder, not inside the data folder: that one belongs to the service user, who could put a link
# there that root would then write through. The screen only reads status.json and the reports.
OUT_ROOT = Path(f"/var/lib/{APP}-soak")
UNITS = [f"{APP}-device", f"{APP}-screen", f"{APP}-lessons", f"{APP}-bridge", f"{APP}-recorder", "mosquitto"]
NEEDED = [f"{APP}-device", f"{APP}-screen", f"{APP}-lessons", f"{APP}-bridge"]
TEST_PROFILE = "Soak test"
REST_VIDEO = Path(__file__).resolve().parent / "media" / "armonico-japanese.mp4"   # as screen.py REST_VIDEO
ACTIVE = OUT_ROOT / "active.json"           # בדיקה שרצה עכשיו: מה להחזיר אם היא נקטעת
UNIT = f"{APP}-soak"
RECOVER_UNIT = Path(f"/etc/systemd/system/{APP}-soak-recover.service")
MAX_MINUTES = 12 * 60
BOOT_FLAG = OUT_ROOT / "start-after-boot.json"   # הבדיקה מתחילה אחרי הריבוט הבא, פעם אחת
BOOT_UNIT = Path(f"/etc/systemd/system/{APP}-soak-boot.service")
AFTER_UNIT = Path(f"/etc/systemd/system/{APP}-soak-after.service")

# הגבולות שבהם הבדיקה עוצרת הכל מיד. ה-Pi 4 מוריד תדר בעצמו מ-80°, ומ-85° בכוח.
GUARD = {"temp_stop": 80.0, "temp_start": 70.0, "mem_stop": 120, "mem_start": 300, "disk_stop": 300,
         "disk_start": 1024, "swap_grow": 200, "psi_mem": 30.0, "psi_cpu": 85.0, "http_fails": 6,
         "mqtt_down": 60, "lat_ms": 1000, "lost_share": 0.3}
FAST, SLOW = 5, 60                     # שניות בין מדידות
BASELINE = 30
TICK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
REC_NAME = re.compile(r"armonico-\d{4}-\d\d-\d\d-\d{6}\.mkv")

GROUPS = ["device", "screen", "lessons", "bridge", "audio", "broker", "media", "recorder", "soak", "other"]
GROUP_HE = {
    "device": "קלידים וצליל (armonico_pi)", "screen": "המסך (screen.py)", "lessons": "מנוע השיעורים",
    "bridge": "הגשר (bridge)", "audio": "שרת השמע (PipeWire)", "broker": "הברוקר (Mosquitto)",
    "media": "וידאו והקלטה (mpv, ffmpeg)", "recorder": "מקליט ה-MQTT", "soak": "הבדיקה עצמה",
    "other": "שאר המערכת",
}
PHASE_HE = {"free": "נגינה חופשית", "song": "השמעת שיר", "lesson": "שיעור", "browse": "דפדוף במסכים",
            "rest": "מסך מנוחה", "video": "מסך מנוחה עם סרטון", "alarm": "שעון מעורר", "rec": "הקלטת מסך", "ha": "פקודות מ-Home Assistant",
            "telegram": "פקודות מטלגרם"}
PHASE_EN = {"free": "free play", "song": "a song", "lesson": "a lesson", "browse": "browsing",
            "rest": "rest screen", "video": "rest video", "alarm": "alarm", "rec": "screen recording", "ha": "Home Assistant",
            "telegram": "Telegram"}
PHASE_COLOR = {"free": "#2a78d6", "song": "#eb6834", "lesson": "#1baf7a", "browse": "#eda100",
               "rest": "#b9b8b0", "video": "#8a6d3b", "alarm": "#e34948", "rec": "#6250d6", "ha": "#008300", "telegram": "#e87ba4"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def load_env():
    env = {}
    for line in read(ETC).splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            env[m.group(1)] = v
    return env


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


# ====================================================================== מדידה
def trim_heap():
    """Free memory that glibc keeps after free() handed back to the system (malloc_trim(0)). A long run of
    reads and subprocess output otherwise grows this process's own RSS hour by hour. No-op off glibc."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


class Csv:
    def __init__(self, path, fields):
        new = not Path(path).exists()                   # קובץ קיים (שחזור אחרי ריסטרט) נמשך, לא נדרס
        self.f = open(path, "a", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=fields, extrasaction="ignore")
        if new:
            self.w.writeheader()
        self.lock = threading.Lock()

    def row(self, d):
        with self.lock:
            self.w.writerow(d)
            self.f.flush()

    def close(self):
        with self.lock:
            self.f.close()


class Procs:
    """זמן מעבד וזיכרון לכל קבוצת תהליכים, לפי ה-cgroup של השירות או לפי שם התהליך."""

    def __init__(self):
        self.kind = {}                 # (pid, starttime) -> group
        self.prev = {}                 # (pid, starttime) -> ticks
        self.me = os.getpid()
        self.screen_pids = set()

    def classify(self, pid, comm, cg):
        if comm in ("mpv", "ffmpeg", "gst-launch-1.0") or comm.startswith("mpv"):
            return "media"
        m = re.search(rf"/{APP}-([a-z-]+)\.service", cg)
        if m:
            name = m.group(1)
            if name == "soak":
                return "soak"
            if name in ("device", "lessons", "bridge", "recorder"):
                return name
            if name == "screen":
                return "screen"
            return "other"
        cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ")
        if "screen.py" in cmd or "session.sh" in cmd:
            return "screen"
        if comm in ("pipewire", "pipewire-pulse", "wireplumber"):
            return "audio"
        if comm == "mosquitto":
            return "broker"
        return "other"

    def scan(self, slow=False):
        ticks, rss, thr, pss, fds = (defaultdict(float) for _ in range(5))
        seen, screens = set(), set()
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            pid = int(d)
            st = read(f"/proc/{pid}/stat")
            if not st:
                continue
            try:
                comm = st[st.index("(") + 1:st.rindex(")")]
                f = st[st.rindex(")") + 2:].split()
                key = (pid, f[19])
                t = int(f[11]) + int(f[12])
                threads = int(f[17])
            except (ValueError, IndexError):
                continue
            seen.add(key)
            g = self.kind.get(key)
            if g is None:
                g = self.kind[key] = self.classify(pid, comm, read(f"/proc/{pid}/cgroup"))
            if g == "screen" and "screen.py" in read(f"/proc/{pid}/cmdline"):
                screens.add(pid)
            ticks[g] += t - self.prev.get(key, t)
            self.prev[key] = t
            statm = read(f"/proc/{pid}/statm").split()
            if len(statm) > 1:
                rss[g] += int(statm[1]) * PAGE / 1048576
            thr[g] += threads
            if slow and g not in ("other",):
                m = re.search(r"^Pss:\s+(\d+)", read(f"/proc/{pid}/smaps_rollup"), re.M)
                if m:
                    pss[g] += int(m.group(1)) / 1024
                try:
                    fds[g] += len(os.listdir(f"/proc/{pid}/fd"))
                except OSError:
                    pass
        for k in list(self.prev):
            if k not in seen:
                self.prev.pop(k, None)
                self.kind.pop(k, None)
        changed = bool(self.screen_pids) and screens != self.screen_pids and bool(screens)
        self.screen_pids = screens or self.screen_pids
        return ticks, rss, thr, pss, fds, changed


class Monitor:
    def __init__(self, out, soak):
        self.out, self.soak = out, soak
        self.procs = Procs()
        self.prev_forks = None
        self.sys_csv = Csv(out / "system.csv", ["t", "cpu", "core_max", "load1", "forks", "mem_used", "mem_avail",
                                                "swap_used", "temp", "freq", "psi_cpu", "psi_mem", "psi_io",
                                                "disk_w_kbs", "disk_free_mb", "http_ms", "http_code"])
        pf = ["t", "kind"] + GROUPS
        self.proc_csv = Csv(out / "procs.csv", pf)
        self.slow_csv = Csv(out / "slow.csv", ["t", "throttled", "xruns", "journal_warn", "restarts",
                                               "pss_total", "journal_lines", "mqtt_msgs", "sd_w_mb", "poe_ma",
                                               "bounces"])
        self.thr_csv = Csv(out / "threads.csv", ["t", "proc", "tid", "name", "cpu"])
        self.top_csv = Csv(out / "top.csv", ["t", "pid", "comm", "group", "cpu", "rss"])
        self.mqtt_csv = Csv(out / "mqtt.csv", ["t", "topic", "per_min"])
        self.wr_csv = Csv(out / "writes.csv", ["t", "pid", "comm", "group", "kb"])
        self.prev_wr = {}                      # (pid, starttime) -> write_bytes
        self.jvol_csv = Csv(out / "journal_volume.csv", ["t", "unit", "lines"])
        self.prev_thr = {}                     # (pid, tid) -> ticks
        self.prev_top = {}                     # (pid, starttime) -> ticks
        self.prev_slow_t = None
        self.sd0 = None
        self.bounces = 0
        self.jvol = defaultdict(int)
        self.prev_cpu = None
        self.prev_disk = None
        self.last_journal = time.time()
        self.unit_state = {}
        self.xrun_seen = {}
        self.xruns_total = 0
        self.problems = defaultdict(int)       # מילת מפתח -> כמות
        self.examples = []
        self.alsa_underruns = 0
        self.example_n = {}                     # (unit, line with its numbers as #) -> how many times
        self.common = Counter()                 # the same for lines that are not warnings, up to 400 kinds
        self.stop = threading.Event()

    # ---- מערכת
    def cpu(self):
        rows = {}
        for line in read("/proc/stat").splitlines():
            if line.startswith("cpu"):
                p = line.split()
                v = list(map(int, p[1:9]))
                rows[p[0]] = (sum(v), v[3] + v[4])
        out, core_max = None, 0.0
        if self.prev_cpu:
            for k, (tot, idle) in rows.items():
                pt, pi = self.prev_cpu.get(k, (tot, idle))
                busy = 100 * (1 - (idle - pi) / max(1, tot - pt))
                if k == "cpu":
                    out = busy
                else:
                    core_max = max(core_max, busy)
        self.prev_cpu = rows
        return out, core_max

    @staticmethod
    def meminfo():
        m = {}
        for line in read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            try:
                m[k] = int(v.split()[0]) / 1024
            except (IndexError, ValueError):
                pass
        total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
        return total - avail, avail, m.get("SwapTotal", 0) - m.get("SwapFree", 0), total

    @staticmethod
    def psi(name):
        m = re.search(r"some avg10=([\d.]+)", read(f"/proc/pressure/{name}"))
        return float(m.group(1)) if m else ""

    def disk(self, dt):
        sect = 0
        for line in read("/proc/diskstats").splitlines():
            p = line.split()
            if len(p) > 9 and re.fullmatch(r"mmcblk\d|sd[a-z]|nvme\d+n\d", p[2]):
                sect += int(p[9])
        kbs = "" if self.prev_disk is None else (sect - self.prev_disk) * 512 / 1024 / max(dt, 0.1)
        self.prev_disk = sect
        st = os.statvfs("/")
        return kbs, st.f_bavail * st.f_frsize / 1048576

    def http(self):
        t0 = time.monotonic()
        code = self.soak.api_get("/state")[0]
        return round((time.monotonic() - t0) * 1000, 1), code

    # ---- דקה
    def throttled(self):
        rc, out, _ = run(["vcgencmd", "get_throttled"], 5)
        m = re.search(r"0x([0-9a-fA-F]+)", out) if rc == 0 else None
        return int(m.group(1), 16) if m else None

    def xruns(self):
        rc, out, _ = run(as_sound_user(["pw-top", "-b", "-n", "2"]), 10)
        if rc != 0 or not out:
            return None
        last = {}
        for line in out.splitlines():
            t = line.split()
            if len(t) >= 10 and t[1].isdigit() and t[8].isdigit():
                last[t[1] + ":" + t[-1]] = int(t[8])
        new = 0
        for k, v in last.items():
            before = self.xrun_seen.get(k, v)
            new += v - before if v >= before else v
            self.xrun_seen[k] = v
        self.xruns_total += new
        return self.xruns_total

    def threads(self, t, dt):
        """זמן המעבד של כל thread בתוכנת המקשים ובתוכנת המסך: מי בדיוק עובד כשהמכשיר "סתם" עומד."""
        pids = {}
        main = self.unit_state.get(f"{APP}-device.service", self.unit_state.get(f"{APP}-device", {})).get("MainPID")
        if main and main != "0":
            pids["device"] = int(main)
        for p in self.procs.screen_pids:
            pids["screen"] = p
        seen = set()
        for proc, pid in pids.items():
            rows = []
            try:
                tids = os.listdir(f"/proc/{pid}/task")
            except OSError:
                continue
            for tid in tids:
                st = read(f"/proc/{pid}/task/{tid}/stat")
                try:
                    name = st[st.index("(") + 1:st.rindex(")")]
                    f = st[st.rindex(")") + 2:].split()
                    ticks = int(f[11]) + int(f[12])
                except (ValueError, IndexError):
                    continue
                k = (pid, tid)
                seen.add(k)
                if k in self.prev_thr:
                    rows.append((100 * (ticks - self.prev_thr[k]) / TICK / max(dt, 0.1), tid, name))
                self.prev_thr[k] = ticks
            for cpu, tid, name in sorted(rows, key=lambda r: -r[0])[:8]:
                self.thr_csv.row({"t": t, "proc": proc, "tid": tid, "name": name, "cpu": round(cpu, 1)})
        for k in list(self.prev_thr):
            if k not in seen:
                del self.prev_thr[k]

    def top(self, t, dt):
        """10 התוכנות הכבדות בדקה האחרונה, כל תוכנה בנפרד (לא לפי קבוצה)."""
        cur, rows, wcur, wrows = {}, [], {}, []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            st = read(f"/proc/{d}/stat")
            try:
                comm = st[st.index("(") + 1:st.rindex(")")]
                f = st[st.rindex(")") + 2:].split()
                key, ticks, rss = (int(d), f[19]), int(f[11]) + int(f[12]), int(f[21]) * PAGE / 1048576
            except (ValueError, IndexError):
                continue
            cur[key] = ticks
            if key in self.prev_top:
                rows.append((100 * (ticks - self.prev_top[key]) / TICK / max(dt, 0.1), key, comm, rss))
            # what each program writes toward the disk (write_bytes: what reaches the block layer, not a write
            # that is overwritten in the page cache first); root reads it for every process
            m = re.search(r"^write_bytes:\s+(\d+)", read(f"/proc/{d}/io"), re.M)
            if m:
                wb = int(m.group(1))
                wcur[key] = wb
                if key in self.prev_wr and wb > self.prev_wr[key]:
                    wrows.append((wb - self.prev_wr[key], key, comm))
        self.prev_top = cur
        self.prev_wr = wcur
        for b, key, comm in sorted(wrows, key=lambda r: -r[0])[:8]:
            self.wr_csv.row({"t": t, "pid": key[0], "comm": comm, "group": self.procs.kind.get(key, ""),
                             "kb": round(b / 1024)})
        for cpu, key, comm, rss in sorted(rows, key=lambda r: -r[0])[:10]:
            self.top_csv.row({"t": t, "pid": key[0], "comm": comm, "group": self.procs.kind.get(key, ""),
                              "cpu": round(cpu, 1), "rss": round(rss)})

    def sd_written(self):
        """כמה MB נכתבו לכרטיס מתחילת הבדיקה (sectors written ב-/sys/block/mmcblk0/stat)."""
        p = read("/sys/block/mmcblk0/stat").split()
        if len(p) < 7:
            return ""
        mb = int(p[6]) * 512 / 1048576
        if self.sd0 is None:
            self.sd0 = mb
        return round(mb - self.sd0, 1)

    def slow_extra(self, t):
        dt = 60.0 if self.prev_slow_t is None else max(1.0, t - self.prev_slow_t)
        first = self.prev_slow_t is None
        self.prev_slow_t = t
        self.threads(t, dt)
        self.top(t, dt)
        counts, self.soak.mqtt_count = dict(self.soak.mqtt_count), defaultdict(int)
        if not first:
            for k, v in sorted(counts.items()):
                self.mqtt_csv.row({"t": t, "topic": k, "per_min": round(v * 60 / dt, 1)})
        vol, self.jvol = dict(self.jvol), defaultdict(int)
        for k, v in sorted(vol.items(), key=lambda x: -x[1])[:12]:
            self.jvol_csv.row({"t": t, "unit": k, "lines": v})
        return {"journal_lines": sum(vol.values()), "mqtt_msgs": "" if first else round(sum(counts.values()) * 60 / dt),
                "sd_w_mb": self.sd_written(), "poe_ma": "" if poe_current() is None else poe_current(),
                "bounces": self.bounces}

    BAD = re.compile(r"Traceback|Exception|\bError\b|error:|failed|underrun|xrun|Out of memory|oom-kill|"
                     r"Under-voltage|voltage|throttl|segfault|Killed", re.I)

    def journal(self):
        since = self.last_journal
        self.last_journal = time.time()
        rc, out, _ = run(["journalctl", "--since", f"@{since:.0f}", "-q", "--no-pager", "-o", "json",
                          "--output-fields=_SYSTEMD_UNIT,SYSLOG_IDENTIFIER,MESSAGE,PRIORITY,_TRANSPORT"], 20)
        if rc != 0:
            return None
        n = 0
        for line in out.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            unit = e.get("_SYSTEMD_UNIT", "") or e.get("SYSLOG_IDENTIFIER", "")
            if unit.startswith(f"{APP}-soak"):
                continue
            self.jvol[(unit or "kernel").replace(".service", "")] += 1
            msg = e.get("MESSAGE", "")
            if isinstance(msg, list):
                msg = bytes(msg).decode(errors="replace")
            if "Key bounce" in msg:
                self.bounces = sum(int(x) for x in re.findall(r"×(\d+)", msg)) or self.bounces
            pri = int(e.get("PRIORITY", 6))
            # every line by kind (its numbers as #), from any unit: the report names what fills the journal
            kind = ((unit or "kernel").replace(".service", ""), re.sub(r"\d+", "#", msg[:120]))
            if kind in self.common or len(self.common) < 400:
                self.common[kind] += 1
            ours = unit.startswith(APP) or e.get("_TRANSPORT") == "kernel" or \
                e.get("SYSLOG_IDENTIFIER") in ("pipewire", "wireplumber", "pipewire-pulse", "mosquitto")
            if not ours:
                continue
            under = re.match(r"ALSA: (\d+) underruns in the last", msg)
            if under:
                # the device's own minute count (device/armonico_pi.py, alsa_messages_counted): the keys' stream
                # while the rest video holds 2048-frame blocks, known and not heard. Counted, not a warning
                self.alsa_underruns += int(under.group(1))
                continue
            if e.get("_TRANSPORT") == "kernel" and re.search(r"Out of memory|oom-kill|Under-voltage", msg):
                self.soak.abort("הקרנל דיווח: " + msg[:80], "kernel: " + msg[:80])
            if pri <= 4 or self.BAD.search(msg):
                n += 1
                src = unit.replace(".service", "") or "kernel"
                self.problems[src] += 1
                key = (src, re.sub(r"\d+", "#", msg[:200]))
                if key in self.example_n:
                    self.example_n[key] += 1
                elif len(self.example_n) < 60:
                    self.example_n[key] = 1
                    self.examples.append((time.time() - self.soak.t0, src, msg[:300]))
        return n

    def services(self):
        restarts = 0
        for u in UNITS:
            rc, out, _ = run(["systemctl", "show", "-p", "NRestarts,ActiveState,MainPID", u], 5)
            if rc != 0:
                continue
            d = dict(x.split("=", 1) for x in out.split() if "=" in x)
            old = self.unit_state.get(u)
            if old:
                if d.get("NRestarts") != old.get("NRestarts") or \
                        (d.get("MainPID") not in ("0", old.get("MainPID")) and old.get("MainPID") != "0"):
                    restarts += 1
                    self.soak.event(f"השירות {u} עלה מחדש (PID {old.get('MainPID')} ל-{d.get('MainPID')})")
                    self.soak.crash(u)
                if d.get("ActiveState") != old.get("ActiveState"):
                    self.soak.event(f"מצב השירות {u}: {old.get('ActiveState')} ל-{d.get('ActiveState')}")
            self.unit_state[u] = d
        return restarts

    def loop(self):
        self.cpu()
        self.procs.scan()
        self.disk(1)
        last_slow, last_fast = 0.0, time.monotonic()
        while not self.stop.is_set():
            now = time.monotonic()
            dt = now - last_fast
            last_fast = now
            t = round(time.time() - self.soak.t0, 1)
            slow = now - last_slow >= SLOW
            try:
                cpu, core = self.cpu()
                used, avail, swap, _ = self.meminfo()
                kbs, free = self.disk(dt)
                ms, code = self.http()
                tv = read("/sys/class/thermal/thermal_zone0/temp").strip()
                self.last_thr = self.throttled()               # כל 5 שניות: מתח נמוך עוצר מיד
                self.soak.guard({"temp": int(tv) / 1000 if tv.isdigit() else None, "avail": avail, "swap": swap,
                                 "free": free, "psi_mem": num(self.psi("memory")), "psi_cpu": num(self.psi("cpu")),
                                 "http_code": code, "throttled": getattr(self, "last_thr", None)})
                temp = read("/sys/class/thermal/thermal_zone0/temp").strip()
                freq = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").strip()
                fk = re.search(r"^processes (\d+)", read("/proc/stat"), re.M)
                fk = int(fk.group(1)) if fk else None
                forks = "" if fk is None or self.prev_forks is None else round((fk - self.prev_forks) / max(dt, 0.1), 1)
                self.prev_forks = fk
                self.sys_csv.row({
                    "t": t, "cpu": "" if cpu is None else round(cpu, 1), "core_max": round(core, 1), "forks": forks,
                    "load1": read("/proc/loadavg").split()[0], "mem_used": round(used), "mem_avail": round(avail),
                    "swap_used": round(swap), "temp": round(int(temp) / 1000, 1) if temp.isdigit() else "",
                    "freq": int(freq) // 1000 if freq.isdigit() else "", "psi_cpu": self.psi("cpu"),
                    "psi_mem": self.psi("memory"), "psi_io": self.psi("io"),
                    "disk_w_kbs": "" if kbs == "" else round(kbs), "disk_free_mb": round(free),
                    "http_ms": ms, "http_code": code})
                ticks, rss, thr, pss, fds, scr = self.procs.scan(slow)
                if scr:
                    self.soak.event("תוכנת המסך (screen.py) עלתה מחדש")
                self.proc_csv.row(dict({"t": t, "kind": "cpu"},
                                       **{g: round(100 * ticks[g] / TICK / max(dt, 0.1), 1) for g in GROUPS}))
                self.proc_csv.row(dict({"t": t, "kind": "rss"}, **{g: round(rss[g], 1) for g in GROUPS}))
                self.proc_csv.row(dict({"t": t, "kind": "threads"}, **{g: int(thr[g]) for g in GROUPS}))
                if slow:
                    last_slow = now
                    trim_heap()
                    self.proc_csv.row(dict({"t": t, "kind": "pss"}, **{g: round(pss[g], 1) for g in GROUPS}))
                    self.proc_csv.row(dict({"t": t, "kind": "fds"}, **{g: int(fds[g]) for g in GROUPS}))
                    thr_v = self.last_thr
                    row = {"t": t, "throttled": "" if thr_v is None else hex(thr_v),
                           "xruns": self.xruns(), "journal_warn": self.journal(),
                           "restarts": self.services(), "pss_total": round(sum(pss.values()))}
                    row.update(self.slow_extra(t))
                    self.slow_csv.row(row)
            except Exception as e:                       # מדידה אחת שנכשלה לא עוצרת את הבדיקה
                log(f"מדידה נכשלה: {type(e).__name__}: {e}")
            self.stop.wait(max(0.5, FAST - (time.monotonic() - now)))


# ====================================================================== מצב המערכת וזמן עלייה
def sh(cmd, timeout=8):
    """פקודת קריאה בלבד; הפלט, או ריק אם היא לא קיימת או נכשלה."""
    rc, out, err = run(cmd, timeout)
    return out.strip() if rc == 0 else ""


def as_sound_user(cmd):
    import pwd
    try:
        uid = pwd.getpwnam(APP).pw_uid
    except KeyError:
        return cmd
    if shutil.which("setpriv"):                # בלי PAM: בלי שתי שורות ביומן בכל קריאה
        import pwd as _p
        gid = _p.getpwnam(APP).pw_gid
        return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups", "--", "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}", *cmd]
    return ["runuser", "-u", APP, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}", *cmd]


def inventory():
    """תמונת מצב של מה שהליבה ומערכת ההפעלה מדווחות: חיישנים, מתחים, GPIO, USB, רשת פיזית ורדיו,
    אחסון, תצוגה, שמע ושגיאות. רק קריאה, שום דבר לא משתנה."""
    raw = {}
    for name, cmd in {
        "os": ["sh", "-c", ". /etc/os-release; echo $PRETTY_NAME"], "uname": ["uname", "-a"],
        "cmdline": ["cat", "/proc/cmdline"], "uptime": ["cat", "/proc/uptime"],
        "temp": ["vcgencmd", "measure_temp"], "throttled": ["vcgencmd", "get_throttled"],
        "volts": ["sh", "-c", "for v in core sdram_c sdram_i sdram_p; do printf '%s ' $v; vcgencmd measure_volts $v; done"],
        "clocks": ["sh", "-c", "for c in arm core emmc; do printf '%s ' $c; vcgencmd measure_clock $c; done"],
        "mem_split": ["sh", "-c", "vcgencmd get_mem arm; vcgencmd get_mem gpu"],
        "firmware": ["vcgencmd", "version"],
        "gpio": ["pinctrl", "get"], "gpio_old": ["raspi-gpio", "get"], "gpioinfo": ["gpioinfo"],
        "usb": ["lsusb"], "usb_tree": ["lsusb", "-t"],
        "ip_addr": ["ip", "-br", "addr"], "ip_link": ["ip", "-s", "link"], "routes": ["ip", "route"],
        "wifi_link": ["iw", "dev", "wlan0", "link"], "wifi_info": ["iw", "dev", "wlan0", "info"],
        "wifi_reg": ["iw", "reg", "get"], "rfkill": ["rfkill", "list"],
        "bluetooth": ["bluetoothctl", "--timeout", "3", "show"],
        "df": ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"], "lsblk": ["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,RO"],
        "i2c": ["sh", "-c", "ls /dev/i2c* /dev/spidev* 2>/dev/null"],
        # what wears the SD card: how / is mounted, swap on it, where the journal is kept, log2ram
        "root_mount": ["findmnt", "-no", "SOURCE,FSTYPE,OPTIONS", "/"],
        "swaps": ["cat", "/proc/swaps"],
        "journald": ["sh", "-c", "grep -hs '^[[:space:]]*Storage' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf; "
                     "[ -d /var/log/journal ] && echo 'persistent /var/log/journal' || echo 'volatile (no /var/log/journal)'; "
                     "du -sh /var/log/journal 2>/dev/null"],
        "log2ram": ["systemctl", "is-active", "log2ram"],
        "zram": ["sh", "-c", "ls /sys/block | grep zram"],
        "drm": ["sh", "-c", "for d in /sys/class/drm/card*-*; do [ -e $d ] || continue; echo \"$(basename $d) $(cat $d/status 2>/dev/null)\"; done"],
        "backlight": ["sh", "-c", "for b in /sys/class/backlight/*; do [ -e $b ] || continue; echo \"$(basename $b) $(cat $b/brightness)/$(cat $b/max_brightness)\"; done"],
        "power": ["sh", "-c", "for p in /sys/class/power_supply/*; do [ -e $p ] || continue; echo \"$(basename $p): $(cat $p/online $p/type 2>/dev/null | tr '\\n' ' ')\"; done"],
        "audio_cards": ["aplay", "-l"], "audio_nodes": as_sound_user(["wpctl", "status"]),
        "failed": ["systemctl", "--failed", "--no-legend", "--plain"],
        "kernel_warn": ["journalctl", "-k", "-b", "-p", "warning", "-q", "--no-pager", "-o", "short-monotonic"],
    }.items():
        raw[name] = sh(cmd, 10)
    hw = []
    for d in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        name = read(d / "name").strip()
        for f in sorted(d.glob("*_input")):
            hw.append(f"{name} {f.name.replace('_input', '')} {read(f).strip()}")
    for d in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        hw.append(f"{read(d / 'type').strip()} {d.name} {read(d / 'temp').strip()}")
    raw["sensors"] = "\n".join(hw)
    nets = {}
    for d in sorted(Path("/sys/class/net").iterdir()):
        if d.name == "lo":
            continue
        st = {k: read(d / "statistics" / k).strip() for k in ("rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
                                                              "rx_bytes", "tx_bytes")}
        nets[d.name] = dict(st, state=read(d / "operstate").strip(), speed=read(d / "speed").strip(),
                            duplex=read(d / "duplex").strip(), carrier_changes=read(d / "carrier_changes").strip(),
                            mac=read(d / "address").strip())
    raw["net_json"] = json.dumps(nets)
    sd = Path("/sys/block/mmcblk0/device")
    raw["sd"] = " · ".join(x for x in (read(sd / "name").strip(), read(sd / "date").strip(),
                                       read(sd / "manfid").strip()) if x)
    raw["sd_type"] = read(sd / "type").strip()
    # only eMMC reports how worn it is (life_time, pre_eol_info); an SD card says nothing about it
    raw["sd_life"] = " ".join(x for x in (read(sd / "life_time").strip(), read(sd / "pre_eol_info").strip()) if x)
    raw["t"] = time.strftime("%Y-%m-%d %H:%M:%S")
    raw["power_src"] = json.dumps(power_source(nets), ensure_ascii=False)
    raw["usb_ext"] = json.dumps(usb_external(), ensure_ascii=False)
    raw["gpio_low"] = json.dumps(gpio_held_low(raw.get("gpio", "")))
    raw["regdom"] = json.dumps(regdom(raw.get("cmdline", ""), raw.get("wifi_reg", "")), ensure_ascii=False)
    return raw


POE_DIRS = ("/sys/devices/platform/rpi-poe-power-supply@0/power_supply/rpi-poe",
            "/sys/class/power_supply/rpi-poe")


def poe_current():
    """זרם מ-PoE+ HAT הרשמי (mA), אם הוא קיים; אחרת None."""
    for d in POE_DIRS:
        v = read(Path(d) / "current_now").strip()
        if v.lstrip("-").isdigit():
            return round(int(v) / 1000)
    return None


def power_source(nets=None):
    """מאיפה ה-Pi מקבל חשמל: PoE (HAT רשמי או HAT אחר שמוצהר ב-device tree) או USB-C.
    ה-Pi 4 לא מדווח ישירות על USB-C, ולכן זו הסקה: בלי HAT של PoE ובלי כבל רשת מחובר זה בטוח USB-C."""
    hat = read("/proc/device-tree/hat/product").strip("\0 \n")
    vendor = read("/proc/device-tree/hat/vendor").strip("\0 \n")
    ma = poe_current()
    eth = (nets or {}).get("eth0", {})
    carrier = read("/sys/class/net/eth0/carrier").strip() == "1"
    d = {"hat": hat, "hat_vendor": vendor, "poe_ma": ma, "eth_carrier": carrier, "eth_speed": eth.get("speed", "")}
    if ma is not None or any(Path(x).exists() for x in POE_DIRS):
        d.update(src="PoE", how="poe", sure=True,
                 he="PoE דרך ה-PoE+ HAT הרשמי" + (f" · {ma} mA" if ma is not None else ""),
                 en="PoE through the official PoE+ HAT" + (f" · {ma} mA" if ma is not None else ""))
    elif hat and re.search(r"poe", hat, re.I):
        d.update(src="PoE", how="hat", sure=True, he=f"PoE דרך HAT: {hat}", en=f"PoE through a HAT: {hat}")
    elif not carrier:
        d.update(src="USB-C", how="no-eth", sure=True,
                 he="USB-C (אין כבל רשת מחובר, אז אין PoE)", en="USB-C (no network cable, so no PoE)")
    else:
        d.update(src="?", how="eth-no-hat", sure=False,
                 he="כנראה USB-C: כבל רשת מחובר אבל אין HAT של PoE שמדווח. HAT של PoE שלא מוצהר לא נראה מכאן",
                 en="Probably USB-C: a network cable is in but no PoE HAT reports itself")
    return d


def usb_external():
    """התקני USB חיצוניים בלבד: בלי ה-root hubs ובלי ה-hub של VL805 שבנוי בלוח ה-Pi 4 (2109:3431)."""
    ext = []
    for d in sorted(Path("/sys/bus/usb/devices").glob("[0-9]*-*")):
        if ":" in d.name:
            continue
        cls = read(d / "bDeviceClass").strip()
        vid, pid = read(d / "idVendor").strip(), read(d / "idProduct").strip()
        if cls == "09" and (vid, pid) == ("2109", "3431"):
            continue
        name = " ".join(x for x in (read(d / "manufacturer").strip(), read(d / "product").strip()) if x)
        ext.append({"port": d.name, "id": f"{vid}:{pid}", "name": name or "?", "hub": cls == "09",
                    "speed": read(d / "speed").strip()})
    return ext


def gpio_held_low(pinctrl_out):
    """פינים במחבר (GPIO 2-27) שמוגדרים כקלט עם pull-up אבל קוראים 0: משהו חיצוני מושך אותם למטה
    (מתג לחוץ, HAT שמשתמש בפין, קצר). שורות pinctrl נראות כך: ' 4: ip    pu | lo // GPIO4 = input'."""
    low = []
    for m in re.finditer(r"^\s*(\d+):\s+ip\s+pu\s*\|\s*lo\b", pinctrl_out or "", re.M):
        n = int(m.group(1))
        if 2 <= n <= 27:
            low.append(n)
    return low


def regdom(cmdline, reg):
    """מדינת הרדיו: מה שה-cmdline מבקש לעומת מה שהליבה בפועל משתמשת בו (global ב-iw reg get)."""
    want = re.search(r"cfg80211\.ieee80211_regdom=(\w\w)", cmdline or "")
    want = want.group(1) if want else ""
    glob = re.search(r"^global\s*\n\s*country (\w\w)", reg or "", re.M)
    glob = glob.group(1) if glob else ""
    phy = re.findall(r"^phy#\d+.*\n\s*country (\w\w)", reg or "", re.M)
    # phy "99": הרדיו מנהל את המדינה בעצמו (self-managed, כמו הרדיו של ה-Pi). אז ה-global הוא רק של cfg80211,
    # שמאמץ אחרי החיבור את קוד המדינה שהנתב משדר, והוא לא מה שהרדיו משדר בו
    selfm = bool(phy) and all(c == "99" for c in phy)
    d = {"want": want, "global": glob, "phy": phy, "self": selfm,
         "ok": not want or not glob or want == glob or selfm}
    if want and glob and want != glob and selfm:
        d["he"] = (f"התבקש {want}. הרשימה הכללית של הליבה מראה {glob}, כי היא לוקחת אחרי החיבור את קוד המדינה "
                   f"שהנתב משדר. הרדיו של ה-Pi מנהל את המדינה בעצמו, ולכן זה לא משנה את השידור")
    if not d["ok"]:
        d["he"] = (f"התבקש {want}, אבל הליבה משתמשת ב-{glob}. בדרך כלל הסיבה היא שהנתב משדר קוד מדינה אחר "
                   f"(Country IE), והליבה מאמצת אותו אחרי החיבור. כדאי לקבוע בנתב את האזור לישראל")
        d["en"] = f"cmdline asks for {want} but the kernel uses {glob}, usually taken from the router's Country IE"
    return d


def boot_info():
    """זמן העלייה: כמה לקח עד שכל שירות של Armonico עלה, ועד שהמכשיר מוכן (נקרא אחרי wait_ready)."""
    info = {"analyze": sh(["systemd-analyze"], 20), "blame": "\n".join(sh(["systemd-analyze", "blame"], 20).splitlines()[:20]),
            "chain": sh(["systemd-analyze", "critical-chain", f"{APP}-screen.service"], 20), "units": {}}
    for u in UNITS + ["network-online.target", "greetd.service"]:
        v = sh(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", u], 5)
        if v.isdigit() and int(v):
            info["units"][u] = round(int(v) / 1e6, 1)
    return info


def wait_ready(env, timeout=300):
    """עד שהכל עלה ועונה: השירותים, המכשיר בברוקר ומנוע השיעורים. מחזיר שניות מהדלקה, או None."""
    import paho.mqtt.client as mqtt
    end = time.monotonic() + timeout
    state = {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-soak-ready-{os.getpid()}")
    if env.get("MQTT_USER"):
        c.username_pw_set(env["MQTT_USER"], env.get("MQTT_PASS", ""))
    c.on_connect = lambda cl, u, f, rc, p=None: cl.subscribe("piano/device/state")
    c.on_message = lambda cl, u, m: state.update(dev=m.payload.decode(errors="replace"))
    connected = False
    code = read(DATA / "screen_code").strip()
    port = env.get("LESSON_UI_PORT", "8099")
    try:
        while time.monotonic() < end:
            if not connected:
                try:
                    c.connect(env.get("MQTT_HOST", "localhost"), int(env.get("MQTT_PORT", "1883")), 30)
                    c.loop_start()
                    connected = True
                except OSError:
                    pass
            units = all(sh(["systemctl", "is-active", u], 5) == "active" for u in NEEDED)
            api = False
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/overview", headers={"X-Screen-Code": code})
                with urllib.request.urlopen(req, timeout=3) as r:
                    api = r.status == 200
            except (OSError, ValueError):
                pass
            if units and api and state.get("dev") == "online":
                return round(float(read("/proc/uptime").split()[0]), 1)
            time.sleep(2)
        return None
    finally:
        c.loop_stop()


def install_unit(path, desc, cmd, condition):
    path.write_text(f"""[Unit]
Description={desc}
After=network-online.target {APP}-lessons.service {APP}-device.service {APP}-screen.service mosquitto.service
Wants=network-online.target
ConditionPathExists={condition}

[Service]
# exec ולא oneshot: העלייה לא מחכה ליחידה הזאת, וזמן העלייה שנמדד נשאר אמיתי
Type=exec
ExecStart={cmd}

[Install]
WantedBy=multi-user.target
""")
    run(["systemctl", "daemon-reload"], 20)
    run(["systemctl", "enable", path.name], 20)


def remove_unit(path):
    if path.exists():
        run(["systemctl", "disable", path.name], 20)
        try:
            path.unlink()
        except OSError:
            pass
        run(["systemctl", "daemon-reload"], 20)


def venv_python():
    """הפייתון של Armonico, שבו paho ו-mido מותקנים; פייתון המערכת לא תמיד כולל אותם."""
    for py in (f"/opt/{APP}/venv/bin/python", f"/opt/{APP}/device-venv/bin/python"):
        if os.path.exists(py):
            return py
    return sys.executable


def keep_copy():
    """The test runs from the program's own folder (/opt/armonico/device): nothing is copied anywhere."""
    out_root()
    return Path(__file__).resolve()


def out_root():
    """The results folder, readable by the screen (it shows the state and the last result); only the
    file with what to put back (active.json, it may hold a PIN) is root's alone."""
    try:
        os.mkdir(OUT_ROOT, 0o755)
    except FileExistsError:
        pass
    st = os.lstat(OUT_ROOT)
    if not stat.S_ISDIR(st.st_mode) or (os.geteuid() == 0 and st.st_uid != 0):
        raise SystemExit(f"{OUT_ROOT} אינה תיקייה של root: לא כותב אליה")
    try:
        os.chmod(OUT_ROOT, 0o755)
    except OSError:
        pass
    return OUT_ROOT


def write_status(**d):
    """status.json: what the screen's card shows (waiting for the reboot, running, cleaning up, done) and the
    last result. Written whole and renamed, so the screen never reads half of it."""
    try:
        cur = json.loads(read(OUT_ROOT / "status.json") or "{}")
    except ValueError:
        cur = {}
    cur.update(d, at=round(time.time()))
    tmp = out_root() / "status.json.tmp"
    tmp.write_text(json.dumps(cur, ensure_ascii=False))
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT_ROOT / "status.json")


def home_copy(z):
    """A copy of the ZIP in the home folder of the device's first user (uid 1000, pi on Raspberry Pi OS),
    owned by them, so WinSCP or a file manager reaches it without root."""
    import pwd
    try:
        u = next(x for x in pwd.getpwall() if 1000 <= x.pw_uid < 60000 and x.pw_dir.startswith("/home/")
                 and os.path.isdir(x.pw_dir))
    except StopIteration:
        return None
    # the home folder is the user's: a name opened only as a new file (never through a link they put there),
    # through the folder's own handle, and renamed into place
    name = Path(z).name
    try:
        dfd = os.open(u.pw_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    tmp = f".{name}.{os.getpid()}.part"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        with os.fdopen(fd, "wb") as g, open(z, "rb") as f:
            shutil.copyfileobj(f, g, 1 << 20)
            os.fchown(g.fileno(), u.pw_uid, u.pw_gid)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        return Path(u.pw_dir) / name
    except OSError:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        return None
    finally:
        os.close(dfd)


# ====================================================================== רשת, Home Assistant וטלגרם
SECRETS = Path(f"/etc/{APP}/secrets.env")
SOAK_ID = "armonico_soak_"                 # הקידומת של כל האוטומציות הזמניות


def load_secrets(extra=None):
    """הטוקנים ששמרה ההתקנה (root בלבד), ואם ניתן --secrets, קובץ משלך באותו מבנה שגובר עליהם."""
    sec = {}
    for f in (SECRETS, extra):
        if not f:
            continue
        for line in read(f).splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if m:
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                sec[m.group(1)] = v
    return sec


class HA:
    """Home Assistant דרך ה-REST API, עם הטוקן. כל קריאה מחזירה (קוד, תשובה, מילישניות)."""

    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, path, body=None, timeout=10):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        except (OSError, ValueError):
            data, code = b"", 0
        ms = round((time.monotonic() - t0) * 1000, 1)
        try:
            return code, json.loads(data or b"null"), ms
        except ValueError:
            return code, None, ms

    def automations(self):
        """מזהה האוטומציה (attributes.id) -> entity_id, לכל האוטומציות הזמניות שלנו."""
        code, states, _ = self.call("GET", "/api/states", timeout=20)
        out = {}
        for st in states or []:
            if st.get("entity_id", "").startswith("automation.") and \
                    str(st.get("attributes", {}).get("id", "")).startswith(SOAK_ID):
                out[st["attributes"]["id"]] = st["entity_id"]
        return out

    def remove_ours(self):
        n = 0
        for aid in self.automations():
            if self.call("DELETE", f"/api/config/automation/config/{aid}")[0] == 200:
                n += 1
        return n


def soak_automations(telegram):
    """האוטומציות הזמניות. כל אחת בודקת כיוון אחר: HA שומע את הברוקר ועונה, HA שומע את הקלידים,
    HA מפעיל את הפסנתר, ו-HA מאשר שהודעת טלגרם יצאה."""
    pub = lambda topic, payload: {"action": "mqtt.publish", "data": {"topic": topic, "payload": payload}}
    a = {
        "echo": {"alias": "Armonico soak: echo", "mode": "parallel", "max": 20,
                 "triggers": [{"trigger": "mqtt", "topic": "piano/soak/ha/ping"}],
                 "actions": [pub("piano/soak/ha/pong", "{{ trigger.payload }}")]},
        "keys": {"alias": "Armonico soak: keys", "mode": "queued", "max": 100,
                 "triggers": [{"trigger": "mqtt", "topic": "piano/notes"}],
                 "actions": [pub("piano/soak/ha/key", "{{ trigger.payload }}")]},
        "play": {"alias": "Armonico soak: play", "mode": "single",
                 "triggers": [{"trigger": "mqtt", "topic": "piano/soak/ha/play"}],
                 "actions": [pub("piano/play", "{{ trigger.payload }}|HA soak test")]},
        "stop": {"alias": "Armonico soak: stop", "mode": "single", "triggers": [],
                 "actions": [pub("piano/stop", "HA soak test")]},
    }
    if telegram:
        a["tgsent"] = {"alias": "Armonico soak: Telegram sent", "mode": "queued", "max": 20,
                       "triggers": [{"trigger": "event", "event_type": "telegram_sent"}],
                       "actions": [pub("piano/soak/ha/tgsent", "{{ trigger.event.data.chat_id }}")]}
    for k, v in a.items():
        v["description"] = "Temporary, made by armonico-soak.py for the load test; it deletes it at the end."
        v.setdefault("conditions", [])
    return a


class Net:
    """בדיקות הרשת שרצות כל הזמן ברקע: הלוך-חזור דרך הברוקר, ping לברוקר ולנתב, Wi-Fi, תעבורה,
    DNS, Telegram ו-Home Assistant."""

    def __init__(self, soak):
        self.s = soak
        self.csv = Csv(soak.out / "net.csv", ["t", "mqtt_ms", "ha_echo_ms", "ping_broker", "loss_broker",
                                               "ping_gw", "loss_gw", "wifi_tx", "wifi_dbm", "rx_kbs", "tx_kbs",
                                               "ha_ms", "ha_code", "dns_ms", "tg_ms", "tg_code"])
        self.prev_bytes = None
        self.stop = threading.Event()
        host = soak.env.get("MQTT_HOST", "localhost")
        self.broker = None if host in ("localhost", "127.0.0.1", "::1", "") else host
        rc, out, _ = run(["ip", "route", "show", "default"], 5)
        m = re.search(r"via (\S+) dev (\S+)", out)
        self.gw, self.dev = (m.group(1), m.group(2)) if m else (None, None)

    @staticmethod
    def ping(host):
        rc, out, _ = run(["ping", "-n", "-q", "-c", "3", "-i", "0.3", "-W", "1", host], 8)
        loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
        avg = re.search(r"= [\d.]+/([\d.]+)/", out)
        return (float(avg.group(1)) if avg else ""), (float(loss.group(1)) if loss else 100.0)

    def wifi(self):
        """עוצמת ה-Wi-Fi דרך iw (nl80211). /proc/net/wireless עובד דרך wireless extensions, והקרנל כותב
        עליו אזהרה ביומן, אז לא קוראים אותו."""
        dev = self.dev if self.dev and self.dev.startswith("wl") else "wlan0"
        out = sh(["iw", "dev", dev, "link"], 5)
        m = re.search(r"signal:\s*(-?\d+)", out)
        tx = re.search(r"tx bitrate:\s*([\d.]+)", out)
        return (float(tx.group(1)) if tx else ""), (float(m.group(1)) if m else "")

    def traffic(self, dt):
        rx = tx = 0
        for line in read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            if name.strip() == "lo":
                continue
            p = rest.split()
            rx, tx = rx + int(p[0]), tx + int(p[8])
        out = ("", "") if self.prev_bytes is None else \
            ((rx - self.prev_bytes[0]) / 1024 / dt, (tx - self.prev_bytes[1]) / 1024 / dt)
        self.prev_bytes = (rx, tx)
        return out

    def dns(self):
        import socket
        t0 = time.monotonic()
        try:
            socket.getaddrinfo("api.telegram.org", 443)
            return round((time.monotonic() - t0) * 1000, 1)
        except OSError:
            return -1

    def telegram(self):
        """getMe עם הטוקן: בודק שהגעה ל-Telegram עובדת, בלי לשלוח הודעה ובלי להפריע ל-HA שמושך עדכונים."""
        tok = self.s.secrets.get("TELEGRAM_TOKEN")
        url = f"https://api.telegram.org/bot{tok}/getMe" if tok else "https://api.telegram.org/"
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (OSError, ValueError):
            code = 0
        return round((time.monotonic() - t0) * 1000, 1), code

    def loop(self):
        last = {"30": 0.0, "60": 0.0, "300": 0.0}
        prev = time.monotonic()
        self.traffic(1)
        while not self.stop.is_set():
            now = time.monotonic()
            row = {"t": self.s.t()}
            try:
                row["mqtt_ms"] = self.s.roundtrip("piano/soak/rtt", 3)
                if self.s.ha_ready:
                    row["ha_echo_ms"] = self.s.roundtrip("piano/soak/ha/ping", 5, "piano/soak/ha/pong")
                rx, tx = self.traffic(max(0.1, now - prev))
                row["rx_kbs"], row["tx_kbs"] = ("" if rx == "" else round(rx, 1)), ("" if tx == "" else round(tx, 1))
                if now - last["30"] >= 30:
                    last["30"] = now
                    row["wifi_tx"], row["wifi_dbm"] = self.wifi()
                    if self.broker:
                        row["ping_broker"], row["loss_broker"] = self.ping(self.broker)
                    if self.gw:
                        row["ping_gw"], row["loss_gw"] = self.ping(self.gw)
                if now - last["60"] >= 60 and self.s.ha:
                    last["60"] = now
                    row["ha_code"], _, row["ha_ms"] = self.s.ha.call("GET", "/api/")
                if now - last["300"] >= 300:
                    last["300"] = now
                    row["dns_ms"] = self.dns()
                    row["tg_ms"], row["tg_code"] = self.telegram()
                self.csv.row(row)
            except Exception as e:
                log(f"בדיקת רשת נכשלה: {type(e).__name__}: {e}")
            prev = now
            self.stop.wait(max(0.5, FAST - (time.monotonic() - now)))


# ====================================================================== המשתמש המדומה
class Soak:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.env = load_env()
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        self.out = Path(args.out or OUT_ROOT / f"soak-{stamp}")
        self.out.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.deadline = time.monotonic() + args.minutes * 60
        self.quit = threading.Event()
        self.retained = {}
        self.expect = (None, 0.0)
        self.pending = defaultdict(deque)
        self.offs, self.offs_lock = [], threading.Lock()
        self.counts = defaultdict(int)
        self.code = read(DATA / "screen_code").strip() or self.env.get("SCREEN_CODE", "")
        self.port = int(self.env.get("LESSON_UI_PORT", "8099"))
        self.lat_csv = Csv(self.out / "latency.csv", ["t", "note", "ms"])
        self.act_csv = Csv(self.out / "actions.csv", ["t", "phase", "what"])
        self.phases = []                                  # (start, end, kind)
        self.events = []
        self.orig = {}
        self.test_profile = None
        self.orig_profile = None
        self.songs = []
        self.lessons_ok = True
        self.rec_before = set(self.recordings())
        self.mqtt = None
        self.notes = []
        self.seen, self.seen_lock = deque(maxlen=20000), threading.Lock()
        self.mqtt_count = defaultdict(int)              # "piano/<x>" -> הודעות מאז המדידה הקודמת
        self.abort_reason = self.abort_en = None
        self.strip_last = ("start", None)
        self.lat_window = deque()                       # (זמן, ms) של הדקה האחרונה, לשמירה על ה-Pi
        self.lat_seen = 0
        self.secrets = load_secrets(args.secrets)
        self.ha = None
        self.ha_ready = False
        self.ha_ids = {}
        self.chat = args.chat_id or ""
        self.ha_keys_pending = deque()
        self.ha_keys_csv = Csv(self.out / "ha_keys.csv", ["t", "ms"])
        self.remote_csv = Csv(self.out / "remote.csv", ["t", "kind", "step", "ms", "ok", "detail"])

    # ---- כלים
    def t(self):
        return round(time.time() - self.t0, 1)

    def left(self):
        return 0 if self.quit.is_set() else self.deadline - time.monotonic()

    def wait(self, sec):
        """שינה שנקטעת בעצירה או בסוף הזמן."""
        end = time.monotonic() + max(0, sec)
        while not self.quit.is_set():
            gap = min(end, self.deadline) - time.monotonic()    # read once: two reads could cross 0 in between
            if gap <= 0:
                break
            time.sleep(min(0.1, gap))
        return not self.quit.is_set() and self.left() > 0

    def event(self, text):
        log(text)
        self.events.append((self.t(), text))

    def act(self, phase, what):
        self.act_csv.row({"t": self.t(), "phase": phase, "what": what})

    def pub(self, topic, payload, retain=False):
        self.mqtt.publish(topic, payload, retain=retain)

    def strip(self, state, kind=None):
        """הפס בראש המסך: בדיקה מתחילה, כמה זמן עבר ומה רץ, ובסוף שהיא הסתיימה. נשמר בברוקר, כך שמסך
        שעלה מחדש באמצע מציג אותו שוב."""
        d = {"state": state, "t0": round(self.t0), "total": round(self.args.minutes * 60), "beat": round(time.time())}
        if kind:
            d.update(phase=PHASE_HE[kind], phase_en=PHASE_EN[kind])
        if kind in ("rest", "video"):
            d["rest"] = "video" if kind == "video" else "animation"     # the screen shows that one now
        if state == "stopped":
            d.update(reason=self.abort_reason or "", reason_en=self.abort_en or "")
        self.strip_last = (state, kind)
        self.pub("piano/device/soak", json.dumps(d, ensure_ascii=False), retain=True)
        write_status(state={"start": "running", "run": "running", "end": "cleaning", "stopped": "cleaning"}[state],
                     t0=round(self.t0), total=round(self.args.minutes * 60), phase=d.get("phase", ""),
                     phase_en=d.get("phase_en", ""), abort=self.abort_reason or "", abort_en=self.abort_en or "")

    def api(self, method, path, body=None, timeout=4):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode())
        req.add_header("X-Screen-Code", self.code)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return r.status, data
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (OSError, ValueError):
            return 0, b""

    def api_get(self, path):
        return self.api("GET", path)

    def api_json(self, method, path, body=None):
        code, data = self.api(method, path, body)
        try:
            return code, json.loads(data or b"{}")
        except ValueError:
            return code, {}

    @staticmethod
    def recordings():
        d = DATA / "device" / "recordings"
        try:
            return [f.name for f in d.iterdir() if REC_NAME.fullmatch(f.name)]
        except OSError:
            return []

    # ---- MQTT
    def connect(self):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-soak-{os.getpid()}")
        if self.env.get("MQTT_USER"):
            c.username_pw_set(self.env["MQTT_USER"], self.env.get("MQTT_PASS", ""))
        ready = threading.Event()

        def on_connect(cl, u, f, rc, p=None):
            if getattr(rc, "is_failure", False):
                log(f"הברוקר סירב לחיבור: {rc}")
                return
            cl.subscribe("piano/#", 0)             # מנוי אחד: כל הודעה מגיעה פעם אחת, וכך נמדד גם קצב הברוקר
            if ready.is_set():
                self.event("החיבור לברוקר חזר")
            ready.set()

        def on_disconnect(cl, u, f, rc, p=None):
            self.counts["mqtt_drops"] += 1
            self.event(f"החיבור לברוקר נפל: {rc}")

        def on_message(cl, u, msg):
            text = msg.payload.decode(errors="replace").strip()
            self.mqtt_count["/".join(msg.topic.split("/")[:2])] += 1
            old = self.retained.get(msg.topic)
            self.retained[msg.topic] = text
            with self.seen_lock:
                self.seen.append((time.monotonic(), msg.topic, text))
            if msg.topic in ("piano/status", "piano/device/state") and old is not None and old != text:
                self.event(f"{msg.topic}: {old} ל-{text}")
            if msg.topic == "piano/soak/ha/key":
                q = self.ha_keys_pending
                if q:
                    self.ha_keys_csv.row({"t": self.t(), "ms": round((time.monotonic() - q.popleft()) * 1000, 1)})
                    self.counts["ha_keys"] += 1
                return
            if msg.topic == "piano/notes":
                if self.ha_ready:
                    self.ha_keys_pending.append(time.monotonic())
                    # כל לחיצה שהגשר דיווח, גם כאלה שהבדיקה לא לחצה (הדמות במסך המנוחה מנגנת כמה צלילים)
                    self.counts["bridge_notes"] += 1
                    while len(self.ha_keys_pending) > 500:
                        self.ha_keys_pending.popleft()
                try:
                    note = int(text)
                except ValueError:
                    return
                q = self.pending.get(note)
                if q:
                    sent = q.popleft()
                    ms = round((time.monotonic() - sent) * 1000, 1)
                    self.lat_csv.row({"t": self.t(), "note": note, "ms": ms})
                    self.lat_seen += 1
                    self.lat_window.append((time.monotonic(), ms))
            elif msg.topic == "piano/device/expect":
                self.expect = (int(text) if re.fullmatch(r"-?\d+", text) else None, time.monotonic())

        c.on_connect, c.on_message, c.on_disconnect = on_connect, on_message, on_disconnect
        c.reconnect_delay_set(1, 30)
        c.connect(self.env.get("MQTT_HOST", "localhost"), int(self.env.get("MQTT_PORT", "1883")), 30)
        c.loop_start()
        if not ready.wait(10):
            raise SystemExit("אין חיבור לברוקר. בדוק MQTT_HOST, MQTT_USER ו-MQTT_PASS ב-/etc/armonico/config.env")
        self.mqtt = c
        time.sleep(2)                                    # ההודעות השמורות מגיעות

    # ---- שמירה על ה-Pi
    def abort(self, reason, reason_en):
        """עצירה מיידית: שום לחיצה, שיר או הקלטה לא ממשיכים, ואחר כך הניקוי הרגיל."""
        if self.abort_reason:
            return
        self.abort_reason, self.abort_en = reason, reason_en
        self.event("עצירת חירום: " + reason)
        self.quit.set()
        try:
            with self.offs_lock:
                left = [n for _, n in self.offs]
                self.offs.clear()
            for n in set(left):
                self.pub("piano/device/note", f"off {n}")
            self.pub("piano/device/rec", "off")
            self.pub("piano/stop", "Soak test: emergency stop")
            self.pub("piano/device/pedal/set", "off")
            if self.retained.get("piano/game/status") == "lesson":
                self.api_json("POST", "/lessonstop", {})
            self.strip("stopped")
        except Exception as e:
            log(f"עצירה: {e}")

    def guard(self, m):
        """נקרא אחרי כל מדידה (כל 5 שניות). m: temp, avail, swap, free, psi_mem, psi_cpu, http_code, throttled."""
        g = self.guard_state = getattr(self, "guard_state", {"hot": 0, "http": 0, "cpu": 0, "swap0": None,
                                                                "down_since": None})
        temp = m.get("temp")
        if isinstance(temp, float):
            g["hot"] = g["hot"] + 1 if temp >= GUARD["temp_stop"] else 0
            if g["hot"] >= 2 or temp >= GUARD["temp_stop"] + 2:
                return self.abort(f"טמפרטורה {temp:.0f}°", f"temperature {temp:.0f}°")
        thr = m.get("throttled")
        if isinstance(thr, int) and thr & 0x1:
            return self.abort("מתח נמוך עכשיו (get_throttled)", "under-voltage now")
        avail = m.get("avail")
        if isinstance(avail, (int, float)) and avail < GUARD["mem_stop"]:
            return self.abort(f"נשארו {avail:.0f} MB זיכרון פנוי", f"{avail:.0f} MB of memory left")
        swap = m.get("swap")
        if isinstance(swap, (int, float)):
            g["swap0"] = swap if g["swap0"] is None else g["swap0"]
            if swap - g["swap0"] > GUARD["swap_grow"]:
                return self.abort(f"ה-swap גדל ב-{swap - g['swap0']:.0f} MB", "swap kept growing")
        free = m.get("free")
        if isinstance(free, (int, float)) and free < GUARD["disk_stop"]:
            return self.abort(f"נשארו {free:.0f} MB בכרטיס", f"{free:.0f} MB left on the card")
        pm = m.get("psi_mem")
        if isinstance(pm, float) and pm > GUARD["psi_mem"]:
            return self.abort(f"לחץ זיכרון {pm:.0f}%", f"memory pressure {pm:.0f}%")
        pc = m.get("psi_cpu")
        g["cpu"] = g["cpu"] + 1 if isinstance(pc, float) and pc > GUARD["psi_cpu"] else 0
        if g["cpu"] >= 12:                               # דקה שלמה של מעבד חנוק
            return self.abort(f"המעבד חנוק דקה שלמה (PSI {pc:.0f}%)", "CPU choked for a minute")
        g["http"] = g["http"] + 1 if m.get("http_code") != 200 else 0
        if g["http"] >= GUARD["http_fails"]:
            return self.abort("מנוע השיעורים לא עונה חצי דקה", "the lesson engine stopped answering")
        if not self.mqtt.is_connected():
            g["down_since"] = g["down_since"] or time.monotonic()
            if time.monotonic() - g["down_since"] > GUARD["mqtt_down"]:
                return self.abort("אין חיבור לברוקר יותר מדקה", "no broker for over a minute")
        else:
            g["down_since"] = None
        now = time.monotonic()
        while self.lat_window and now - self.lat_window[0][0] > 60:
            self.lat_window.popleft()
        if len(self.lat_window) >= 10:
            p95 = pct([x[1] for x in self.lat_window], 95)
            if p95 > GUARD["lat_ms"]:
                return self.abort(f"השהיית קליד {p95:.0f} ms בדקה האחרונה", f"key delay {p95:.0f} ms")
        if self.lat_seen >= 20:                          # רק כשברור שהגשר מדווח על לחיצות
            sent = self.counts["notes"] - g.get("notes0", 0)
            lost = self.counts["lost"] - g.get("lost0", 0)
            if now - g.get("win_at", 0) > 60:
                g.update(win_at=now, notes0=self.counts["notes"], lost0=self.counts["lost"])
            elif sent >= 30 and lost / sent > GUARD["lost_share"]:
                return self.abort(f"{lost} מתוך {sent} לחיצות לא הגיעו לגשר בדקה", "key presses are getting lost")

    def crash(self, what):
        """שירות שעלה מחדש באמצע: זה כבר מצב לא תקין, ולא ממשיכים להעמיס."""
        self.abort(f"{what} קרס ועלה מחדש", f"{what} crashed and restarted")

    def quiet_shortcuts(self):
        """The bridge's shortcuts (a few keys that run something in Home Assistant, a light, a scene) must not
        fire from the songs the test plays. The same flag the lesson screen touches while it sounds notes
        (piano_screen_keys in the bridge's RUN_DIR) is kept fresh: the keys still reach the bridge and are
        measured, they only make no shortcut. It is touched every second and ages out 3 s after the test."""
        run_dir = self.env.get("RUN_DIR") or f"/run/{APP}"
        while not self.quit.wait(1):
            try:
                dfd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError:
                continue
            try:
                # the folder is the service user's: never through a link, and the file stays theirs
                fd = os.open("piano_screen_keys", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
                try:
                    st = os.fstat(dfd)
                    if os.fstat(fd).st_uid != st.st_uid:
                        os.fchown(fd, st.st_uid, st.st_gid)
                    os.utime(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
            finally:
                os.close(dfd)

    def beat(self):
        """הפס במסך מתעדכן כל דקה: מסך שלא קיבל עדכון 3 דקות (הבדיקה נהרגה, ריסטרט) מסתיר אותו."""
        while not self.quit.wait(60):
            st, kind = self.strip_last
            if st in ("start", "run"):
                self.strip(st, kind)

    def safe_to_start(self):
        """לפני שמתחילים: ה-Pi צריך להיות במצב טוב. אחרת לא מתחילים בכלל."""
        why = []
        temp = read("/sys/class/thermal/thermal_zone0/temp").strip()
        if temp.isdigit() and int(temp) / 1000 >= GUARD["temp_start"]:
            why.append(f"חם מדי להתחלה: {int(temp) / 1000:.0f}°")
        rc, out, _ = run(["vcgencmd", "get_throttled"], 5)
        m = re.search(r"0x([0-9a-fA-F]+)", out) if rc == 0 else None
        if m and int(m.group(1), 16) & 0x5:
            why.append("יש עכשיו מתח נמוך או הגבלת תדר")
        _, avail, _, _ = Monitor.meminfo()
        if avail < GUARD["mem_start"]:
            why.append(f"רק {avail:.0f} MB זיכרון פנוי")
        st = os.statvfs("/")
        if st.f_bavail * st.f_frsize / 1048576 < GUARD["disk_start"]:
            why.append("פחות מ-1 GB פנוי בכרטיס")
        if self.retained.get("piano/game/status") == "lesson":
            why.append("שיעור רץ עכשיו")
        if self.retained.get("piano/alarm/status") == "on" or self.retained.get("piano/play/status") == "playing":
            why.append("שיר או שעון מעורר מתנגנים עכשיו")
        if why:
            raise SystemExit("הבדיקה לא התחילה: " + "; ".join(why))

    # ---- נקטעה באמצע: מה להחזיר
    def save_active(self):
        d = {"out": str(self.out), "t0": self.t0, "minutes": self.args.minutes, "orig": self.orig,
             "orig_profile": self.orig_profile, "test_profile": self.test_profile, "pin": self.args.pin or "",
             "rec_before": sorted(self.rec_before), "ha_url": self.ha.url if self.ha else "",
             "secrets": self.args.secrets or "", "chat": self.chat or "", "no_telegram": bool(self.args.no_telegram)}
        ACTIVE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ACTIVE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, ensure_ascii=False)

    @staticmethod
    def arm_recovery():
        """יחידת systemd שרצה פעם אחת בעלייה הבאה, רק אם הבדיקה נקטעה: מנקה, שומרת דוח ומוחקת את עצמה.
        היא לא מריצה בדיקה."""
        keep = keep_copy()
        install_unit(RECOVER_UNIT, "Armonico soak test: restore after an interrupted test (once, then removed)",
                     f"{venv_python()} {keep} recover", ACTIVE)

    @staticmethod
    def disarm_recovery():
        try:
            ACTIVE.unlink()
        except OSError:
            pass
        remove_unit(RECOVER_UNIT)

    # ---- המתנה להודעות
    def mark(self):
        return time.monotonic()

    def wait_topic(self, since, topic, timeout, match=None):
        """מילישניות מ-since עד שהגיעה הודעה בנושא (ואם ניתן match, שתוכנה מתאים). None אם לא הגיעה."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.quit.is_set():
            with self.seen_lock:
                for at, top, text in self.seen:
                    if at >= since and top == topic and (match is None or match(text)):
                        return round((at - since) * 1000, 1)
                while self.seen and time.monotonic() - self.seen[0][0] > 120:
                    self.seen.popleft()
            time.sleep(0.02)
        return None

    def roundtrip(self, topic, timeout, back=None):
        """הודעה לעצמנו דרך הברוקר (או דרך HA כשיש back): הזמן עד שחזרה. -1 אם לא חזרה."""
        tag = f"{os.getpid()}-{time.monotonic():.6f}"
        since = self.mark()
        self.pub(topic, tag)
        ms = self.wait_topic(since, back or topic, timeout, lambda x: x == tag)
        return -1 if ms is None else ms

    def remote_row(self, kind, step, ms, ok, detail=""):
        if not ok:
            self.save_bridge_log(kind, step)
        self._remote_row(kind, step, ms, ok, detail)

    def save_bridge_log(self, kind, step):
        """שלב של HA או טלגרם שלא הצליח: מה הגשר ומנוע השיעורים כתבו ביומן בחצי הדקה שלפניו, כדי לדעת
        אם הבקשה הגיעה ומה נעשה איתה."""
        rc, out, _ = run(["journalctl", "--since", "-40s", "-q", "--no-pager", "-o", "short-iso",
                          "-u", f"{APP}-bridge", "-u", f"{APP}-lessons", "-u", f"{APP}-device"], 15)
        try:
            with open(self.out / "remote_fail.txt", "a") as f:
                f.write(f"== {self.t():.1f} s · {kind} · {step}\n{out if rc == 0 else '(היומן לא נקרא)'}\n\n")
        except OSError:
            pass

    def _remote_row(self, kind, step, ms, ok, detail=""):
        self.remote_csv.row({"t": self.t(), "kind": kind, "step": step, "ms": "" if ms is None else ms,
                             "ok": 1 if ok else 0, "detail": detail})

    # ---- קלידים וכפתורים
    def press(self, note, dur):
        """מגע בקליד, אותה דרך שמגע במסך עובר: צליל, הגשר ומנוע השיעורים."""
        note = max(24, min(108, int(note)))
        self.pending[note].append(time.monotonic())
        self.pub("piano/device/note", f"on {note}")
        self.counts["notes"] += 1
        with self.offs_lock:
            heapq.heappush(self.offs, (time.monotonic() + dur, note))

    def releaser(self):
        while not self.quit.is_set():
            now = time.monotonic()
            with self.offs_lock:
                due = []
                while self.offs and self.offs[0][0] <= now:
                    due.append(heapq.heappop(self.offs)[1])
            for n in due:
                self.pub("piano/device/note", f"off {n}")
            # הודעה שלא חזרה אחרי 3 שניות נספרת כאבודה
            for n, q in list(self.pending.items()):
                while q and now - q[0] > 3:
                    q.popleft()
                    self.counts["lost"] += 1
            time.sleep(0.01)

    def button(self, name, hold=False):
        self.pub("piano/device/button", name + (":hold" if hold else ""))
        time.sleep(0.12)
        self.pub("piano/device/button/up", name)
        self.counts["buttons"] += 1
        time.sleep(0.5)

    def home(self):
        """☰ בלחיצה ארוכה פעמיים: מעיר את המסך אם ישן, ותמיד חוזר למסך המנוחה."""
        self.button("menu", True)
        self.wait(0.8)
        self.button("menu", True)
        self.wait(1.0)

    def octave(self):
        try:
            return int(self.retained.get("piano/device/octave", "4"))
        except ValueError:
            return 4

    def keys(self):
        base = 12 * (self.octave() + 1)
        return [base + i for i in range(13)]

    # ---- נגינה של שירים אמיתיים
    def load_melodies(self):
        """קו המנגינה של כל שיר במכשיר, באותה דרך שמנוע השיעורים מחלץ אותו (piano_numbers.melody_events),
        כדי שהנגינה החופשית והנגינה לצד שיר יהיו שירים אמיתיים ולא לחיצות סתם."""
        self.melodies = {}
        sys.path.insert(0, f"/opt/{APP}/lessons")
        try:
            import piano_numbers
        except Exception as e:
            self.event(f"המנגינות לא נטענו ({type(e).__name__}): הנגינה תהיה סולמות פשוטים")
            return
        folder = Path(self.env.get("SONGS_DIR") or DATA / "songs")
        for name in self.songs:
            f = folder / f"{name}.mid"
            if not f.exists():
                continue
            try:
                ev = piano_numbers.melody_events(str(f))
            except Exception:
                continue
            if len(ev) >= 8:
                self.melodies[name] = [(float(a), int(n), float(b)) for a, n, b in ev]
        log(f"מנגינות: {len(self.melodies)} מתוך {len(self.songs)} שירים")

    def fit(self, notes):
        """השיר כולו מוזז לגובה שבו הכי הרבה צלילים נופלים על 13 הקלידים (ואחר כך הכי מעט שחורים),
        וצליל שעדיין בחוץ עובר אוקטבה אל הקליד בשמו. כמו שמישהו מנגן שיר על 13 קלידים."""
        lo = self.keys()[0]
        hi = lo + 12
        black = {1, 3, 6, 8, 10}

        def score(sh):
            moved = [n + sh for n in notes]
            return (sum(lo <= n <= hi for n in moved), -sum((n % 12) in black for n in moved), -abs(sh))
        sh = max(range(-48, 49), key=score)
        out = []
        for n in notes:
            n += sh
            while n < lo:
                n += 12
            while n > hi:
                n -= 12
            out.append(n)
        return out

    def phrases_of(self, ev):
        """משפטים: הפסקה של יותר מ-0.6 שניות, או 12 צלילים לכל היותר."""
        out, cur = [], []
        for i, e in enumerate(ev):
            if cur and (e[0] - cur[-1][2] > 0.6 or len(cur) >= 12):
                out.append(cur)
                cur = []
            cur.append(e)
        if cur:
            out.append(cur)
        return out

    def play_phrase(self, ev, notes, tempo=1.0, slip=0.0):
        """משפט אחד בקצב של השיר. slip: הסיכוי לפספס קליד שכן ולתקן מיד, כמו שקורה לבן אדם."""
        t0 = ev[0][0]
        start = time.monotonic()
        for (a, _, b), n in zip(ev, notes):
            due = start + (a - t0) * tempo
            if not self.wait(due - time.monotonic()):
                return False
            if slip and self.rng.random() < slip:
                ks = self.keys()
                wrong = n + self.rng.choice([-2, -1, 1, 2])
                self.press(wrong if ks[0] <= wrong <= ks[-1] else n + (1 if n < ks[-1] else -1), 0.12)
                self.counts["play_slips"] += 1
                if not self.wait(self.rng.uniform(0.15, 0.3)):
                    return False
            self.press(n, max(0.08, min(1.5, (b - a) * tempo * 0.95)))
        last = ev[-1]
        return self.wait((last[2] - last[0]) * tempo)

    def play_song(self, end, phase="free"):
        """שיר אחד מהמכשיר: לפעמים מההתחלה ולפעמים מאמצע, כמה משפטים ברצף, ולפעמים משפט שחוזר שוב
        כמו בתרגול. בסוף שיר לפעמים אקורד של הצליל האחרון."""
        if not getattr(self, "melodies", None):
            base = self.keys()[0]
            for d in (0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2, 0):
                self.press(base + d, 0.3)
                if not self.wait(0.35):
                    return
            return
        name = self.rng.choice(list(self.melodies))
        ev = self.melodies[name]
        notes = self.fit([n for _, n, _ in ev])
        phr = self.phrases_of(ev)
        idx = {id(e): i for i, e in enumerate(ev)}
        i = 0 if self.rng.random() < 0.5 else self.rng.randrange(len(phr))
        tempo = self.rng.uniform(0.9, 1.2)                # קצת יותר לאט מהמקור, כמו בנגינה ביד
        pedal = self.rng.random() < 0.25
        self.act(phase, f"מנגן {name}" + (" עם פדל" if pedal else ""))
        if pedal:
            self.pub("piano/device/pedal/set", "on")
        count = self.rng.randint(3, 8)
        while count and i < len(phr) and time.monotonic() < end and self.left() > 0:
            p = phr[i]
            ns = [notes[idx[id(e)]] for e in p]
            if not self.play_phrase(p, ns, tempo, slip=0.03):
                break
            if self.rng.random() < 0.2:                  # המשפט הזה עוד פעם
                self.wait(self.rng.uniform(0.6, 1.5))
                continue
            i += 1
            count -= 1
            self.wait(self.rng.uniform(0.15, 0.8))
        if self.rng.random() < 0.3 and i:
            root = notes[idx[id(phr[min(i, len(phr)) - 1][-1])]]
            for iv in (0, 4, 7):
                n = root + iv
                self.press(n if n <= self.keys()[-1] else n - 12, 1.2)
            self.wait(1.4)
        if pedal:
            self.pub("piano/device/pedal/set", "off")

    # ---- שלבים
    def phase_free(self, dur, phase="free"):
        self.home()
        self.button("menu")                    # מנוחה -> שירים
        self.button("menu")                    # שירים -> נגינה חופשית
        end = time.monotonic() + dur
        voice_changed = False
        if phase == "free" and self.rng.random() < 0.15:
            v = self.rng.randint(1, 15)
            self.pub("piano/device/voice/set", str(v))
            self.act(phase, f"צליל {v}")
            voice_changed = True
            self.wait(4)
        octave_moved = False
        while time.monotonic() < end and self.left() > 0 and not self.quit.is_set():
            # ▲▼ כמו אדם שמנגן: בערך בשליש מהשירים, ולפחות פעם אחת בכל נגינה חופשית (היה 8%, כמעט לא קרה)
            if self.rng.random() < 0.3 or (not octave_moved and end - time.monotonic() < dur / 2):
                octave_moved = True
                o = self.octave()
                name = "down" if o >= 6 else "up" if o <= 2 else self.rng.choice(["up", "down"])
                self.button(name)                # ▲▼ בנגינה חופשית: אוקטבה
                self.act(phase, f"אוקטבה {name}")
            self.play_song(end, phase)
            self.wait(self.rng.uniform(1.5, 5))  # בין שיר לשיר
        if voice_changed:
            self.pub("piano/device/voice/set", self.orig.get("voice", "0"))
        self.pub("piano/device/pedal/set", self.orig.get("pedal", "off"))

    def phase_song(self, dur):
        mel = getattr(self, "melodies", {})
        song = self.rng.choice(list(mel) if mel else self.songs)
        self.pub("piano/play", f"{song}|Soak test")
        self.act("song", song)
        self.counts["songs"] += 1
        for _ in range(100):
            if self.retained.get("piano/play/status") == "playing" or not self.wait(0.1):
                break
        started = time.monotonic()
        end = started + dur
        ev = mel.get(song)
        notes = self.fit([n for _, n, _ in ev]) if ev else []
        paused_once = False
        while time.monotonic() < end and self.retained.get("piano/play/status") == "playing" and self.left() > 0:
            r = self.rng.random()
            if ev and r < 0.5:
                # מנגן יחד עם השיר, באותו מקום בשיר שבו הוא נמצא עכשיו
                pos = time.monotonic() - started
                part = [(e, n) for e, n in zip(ev, notes) if pos + 0.5 <= e[0] <= pos + self.rng.uniform(8, 20)]
                if part:
                    self.act("song", "מנגן יחד עם השיר")
                    first = part[0][0][0]
                    self.wait(first - (time.monotonic() - started))
                    self.play_phrase([e for e, _ in part], [n for _, n in part], 1.0, slip=0.02)
                    continue
            # השהיה אחת, כמו אדם: רק אחרי שהשיר כבר התנגן קצת (בהתחלה זה נראה כמו שיר שנקטע), ולמשך
            # כמה שניות. השעון של השיר זז בדיוק בזמן שעמד, כך שהנגינה יחד איתו נשארת באותו מקום בשיר
            if r > 0.85 and not paused_once and time.monotonic() - started > 15 and end - time.monotonic() > 12:
                self.pub("piano/pause", "pause")
                self.act("song", "השהיה")
                p0 = time.monotonic()
                self.wait(self.rng.uniform(3, 6))
                self.pub("piano/pause", "resume")
                stood = time.monotonic() - p0
                paused_once = True
                end += stood
                started += stood
                continue
            self.wait(self.rng.uniform(3, 8))
        if self.retained.get("piano/play/status") == "playing":
            self.pub("piano/stop", "Soak test")
        self.wait(2)

    def phase_lesson(self, dur):
        song = self.rng.choice(self.songs)
        code, _ = self.api_json("POST", "/lesson", {"song": song})
        self.act("lesson", f"{song} ({code})")
        if code not in (200, 204):
            self.event(f"שיעור לא התחיל ({song}): HTTP {code}")
            return
        self.counts["lessons"] += 1
        # a long piece takes the engine a while to prepare (on the Pi, a symphony movement took more than
        # 20 s): it waited 20 s, took the lesson for one that never came, moved on, and the lesson then
        # started alone and waited 2 minutes for keys nobody played. Up to 2 minutes now, and the time it
        # took is a measure of its own.
        asked = time.monotonic()
        while self.retained.get("piano/game/status") != "lesson" and time.monotonic() - asked < 120:
            if not self.wait(0.2):
                break
        took = round(time.monotonic() - asked, 1)
        self.remote_row("lesson", "השיעור התחיל", took * 1000, self.retained.get("piano/game/status") == "lesson", song)
        if self.retained.get("piano/game/status") != "lesson":
            self.event(f"השיעור ({song}) לא התחיל תוך {took:.0f} שניות")
            self.api_json("POST", "/lessonstop", {})
            return
        if took > 10:
            self.event(f"השיעור ({song}) התחיל אחרי {took:.0f} שניות")
        begun = time.monotonic()
        end = begun + dur
        last_press = progress = time.monotonic()
        gap = 1.0
        stalls = 0
        last_want = None
        while time.monotonic() < end and self.left() > 0 and not self.quit.is_set():
            if self.retained.get("piano/game/status") != "lesson":
                break
            want, at = self.expect
            now = time.monotonic()
            if want is not None and want >= 0 and now - at < 12 and now - last_press > gap:
                # תלמיד אמיתי: בהתחלה טועה יותר (בערך 1 מ-10), אחרי כמה דקות כמעט לא (1 מ-40). טעות היא
                # הקליד השכן, ואחריה הקליד הנכון, בלי לחיצות סתם
                skill = min(1.0, (now - begun) / 240)
                miss = 0.10 - 0.075 * skill
                if want != last_want and self.rng.random() < miss:
                    lo = self.keys()[0]
                    wrong = want + self.rng.choice([-2, -1, 1, 2])
                    self.press(wrong if lo <= wrong <= lo + 12 else want + (1 if want < lo + 12 else -1), 0.25)
                    self.counts["lesson_slips"] += 1
                    last_press = progress = now
                    gap = self.rng.uniform(0.5, 1.2)          # שומע שזה לא נכון ומתקן
                    last_want = want
                    continue
                self.press(want, self.rng.uniform(0.2, 0.5))
                last_press = progress = now
                last_want = None
                gap = self.rng.uniform(2.5, 5) if self.rng.random() < 0.04 else self.rng.uniform(0.35, 0.9)
            elif want is not None and want >= 0 and now - progress > 8:
                # שיעור שמחכה לקליד ואף אחד לא עונה לו: הבדיקה לא משאירה אותו לחכות לך. הקליד שהוא מבקש
                # שוב, ואחרי 3 פעמים בלי התקדמות השיעור נעצר והבדיקה ממשיכה
                stalls += 1
                if stalls >= 3:
                    self.counts["lesson_stalls"] += 1
                    self.act("lesson", "השיעור לא התקדם: נעצר")
                    break
                self.press(want, 0.3)
                progress = now
                self.act("lesson", "חיכה, נלחץ שוב הקליד שהוא מבקש")
            elif (want is None or want < 0) and now - max(progress, at) > 25:
                # אין קליד לבקש ושום דבר לא זז 25 שניות (הדגמה ארוכה לוקחת פחות): משהו אחר מחכה לאדם
                self.counts["lesson_stalls"] += 1
                self.act("lesson", "השיעור חיכה בלי לבקש קליד: נעצר")
                break
            time.sleep(0.05)
        self.api_json("POST", "/lessonstop", {})     # always: also a lesson that is still being prepared
        self.wait(3)
        self.home()                              # ☰ ארוך סוגר גם את כרטיס הסיכום

    def phase_browse(self, dur):
        self.home()
        self.button("menu")                      # שירים
        end = time.monotonic() + dur
        while time.monotonic() < end and self.left() > 0:
            self.button("down")
            if not self.wait(self.rng.uniform(0.6, 2.5)):
                break
            if self.rng.random() < 0.12:
                self.button("ok", True)          # ✓ ארוך ברשימה: מנגן את השיר
                self.act("browse", "השמעה מהרשימה")
                self.wait(self.rng.uniform(15, 40))
        self.home()

    def nothing_left_playing(self, kind):
        """בסוף כל שלב: שום שיר ושום שעון לא ממשיכים לשלב הבא. אם משהו עוד מתנגן, זה נרשם (העצירה של
        השלב לא עבדה) והבדיקה עוצרת אותו, כך שהשלב הבא נמדד לבד."""
        if self.retained.get("piano/game/status") == "lesson" and not self.quit.is_set():
            self.event(f"בסוף השלב {PHASE_HE[kind]} שיעור עדיין רץ: נעצר לפני השלב הבא")
            self.counts["left_playing"] += 1
            self.api_json("POST", "/lessonstop", {})
            self.wait(2)
        def playing():
            return self.retained.get("piano/play/status") == "playing" or self.retained.get("piano/alarm/status") == "on"
        # a stop the phase sent itself takes the bridge about 2.5 s to report: that is not "left playing"
        for _ in range(60):
            if not playing() or self.quit.is_set():
                return
            time.sleep(0.1)
        self.event(f"בסוף השלב {PHASE_HE[kind]} שיר או שעון עדיין התנגנו: נעצרו לפני השלב הבא")
        self.counts["left_playing"] += 1
        self.pub("piano/stop", "Soak test: end of phase")
        for _ in range(50):
            if not playing():
                break
            time.sleep(0.1)

    def phase_rest(self, dur):
        """The rest screen as the animation (the strip tells the screen), whatever the setting is."""
        self.home()
        self.wait(dur)

    def phase_video(self, dur):
        """The rest screen with its video (device/media/armonico-japanese.mp4): GStreamer decoding, frames drawn
        on the screen and its sound in the mixer, for minutes on end, as when the device stands on a shelf."""
        self.home()
        self.wait(dur)

    def phase_alarm(self, dur):
        """השעון המעורר: מתחיל, מצלצל ונעצר. העצירה היא הדבר החשוב: שעון חייב תמיד להיעצר."""
        song = self.rng.choice(self.songs)
        since = self.mark()
        self.pub("piano/alarm/set", f"{song}|Soak test")
        self.act("alarm", song)
        self.counts["alarms"] += 1
        ms = self.wait_topic(since, "piano/alarm/status", 15, lambda x: x == "on")
        self.remote_row("alarm", "השעון התחיל לצלצל", ms, ms is not None, song)
        self.wait(dur)
        since = self.mark()
        self.pub("piano/stop", "Soak test")
        ms = self.wait_topic(since, "piano/alarm/status", 15, lambda x: x == "off")
        self.remote_row("alarm", "השעון נעצר", ms, ms is not None)
        self.wait(3)

    def phase_rec(self, dur):
        st = os.statvfs("/")
        if st.f_bavail * st.f_frsize / 1048576 < 2048:
            self.act("rec", "דולג: פחות מ-2 GB פנוי")
            return self.phase_free(dur)
        self.pub("piano/device/rec", "on")
        self.act("rec", "ההקלטה התחילה")          # "התחלה" של השלב כבר נרשמה בלולאה הראשית
        self.counts["recordings"] += 1
        self.wait(4)
        self.phase_free(dur, "rec")
        self.pub("piano/device/rec", "off")
        self.act("rec", "סוף")
        self.wait(15)                            # האיחוד של התמונה והשמע בסוף ההקלטה
        self.home()

    def phase_ha(self, dur):
        """HA מפעיל את הפסנתר: שיר דרך אוטומציה שמקשיבה לברוקר, בדיקה שהחיישן ב-HA התעדכן, ועצירה
        דרך קריאת API שמפעילה אוטומציה (automation.trigger)."""
        song = self.rng.choice(self.songs)
        since = self.mark()
        self.pub("piano/soak/ha/play", song)
        ms = self.wait_topic(since, "piano/play", 10)
        self.remote_row("ha", "HA קיבל ושלח לגשר", ms, ms is not None, song)
        ms2 = self.wait_topic(since, "piano/play/status", 15, lambda x: x == "playing")
        self.remote_row("ha", "השיר התחיל", ms2, ms2 is not None)
        if ms2 is not None:
            t0 = time.monotonic()
            got = None
            while time.monotonic() - t0 < 15:
                code, st, _ = self.ha.call("GET", "/api/states/sensor.piano_play_status")
                if code == 200 and (st or {}).get("state") == "playing":
                    got = round((time.monotonic() - t0) * 1000, 1)
                    break
                time.sleep(0.5)
            self.remote_row("ha", "החיישן ב-HA הראה playing", got, got is not None)
        self.wait(self.rng.uniform(8, 20))
        if ms2 is not None and self.retained.get("piano/play/status") != "playing":
            # stopped before HA was asked to stop it: by whom, from the bridge's own reason
            why = self.retained.get("piano/play/stopped_by", "") or "?"
            self.remote_row("ha", "השיר נעצר לפני הפקודה", None, False, why)
            self.event(f"השיר מ-HA נעצר לבד לפני פקודת העצירה: {why}")
        since = self.mark()
        code, _, api_ms = self.ha.call("POST", "/api/services/automation/trigger",
                                       {"entity_id": self.ha_ids.get(SOAK_ID + "stop", "")})
        self.remote_row("ha", "קריאת API ל-HA", api_ms, code == 200, f"HTTP {code}")
        ms = self.wait_topic(since, "piano/stop", 10)
        self.remote_row("ha", "HA שלח עצירה", ms, ms is not None)
        ms = self.wait_topic(since, "piano/play/status", 15, lambda x: x == "idle")
        self.remote_row("ha", "השיר נעצר", ms, ms is not None)
        if ms is None:
            self.nothing_left_playing("ha")          # the song must not go on under the next phases
        else:
            # and HA sees it stopped too: its sensor, which its own automations and dashboards read
            t0, got = time.monotonic(), None
            while time.monotonic() - t0 < 10:
                code, st, _ = self.ha.call("GET", "/api/states/sensor.piano_play_status")
                if code == 200 and (st or {}).get("state") == "idle":
                    got = round((time.monotonic() - t0) * 1000, 1)
                    break
                time.sleep(0.5)
            self.remote_row("ha", "החיישן ב-HA הראה idle", got, got is not None)
        self.counts["ha_rounds"] += 1
        self.wait(max(0, dur - 40))

    def tg(self, command, args=()):
        """פקודת טלגרם כאילו הגיעה מהצ'אט שלך: אותו אירוע telegram_command ש-HA יוצר מהודעה אמיתית,
        כך שהאוטומציות של Armonico רצות כרגיל והתשובה נשלחת אליך בטלגרם."""
        since = self.mark()
        code, _, ms = self.ha.call("POST", "/api/events/telegram_command",
                                   {"command": command, "args": list(args), "chat_id": int(self.chat),
                                    "user_id": int(self.chat), "from_first": "Soak test", "text": command})
        self.remote_row("telegram", f"{command}: אירוע ב-HA", ms, code == 200, f"HTTP {code}")
        return since

    def phase_telegram(self, dur):
        song = self.rng.choice(self.songs)
        since = self.tg("/pianostatus")
        ms = self.wait_topic(since, "piano/soak/ha/tgsent", 25)
        self.remote_row("telegram", "/pianostatus: ההודעה יצאה לטלגרם", ms, ms is not None)
        self.wait(3)
        since = self.tg("/pianoplay", [song])
        ms = self.wait_topic(since, "piano/shortcut/cmd", 10, lambda x: x.startswith("play|"))
        self.remote_row("telegram", "/pianoplay: HA העביר לגשר", ms, ms is not None, song)
        ms = self.wait_topic(since, "piano/play/status", 15, lambda x: x == "playing")
        self.remote_row("telegram", "/pianoplay: השיר התחיל", ms, ms is not None)
        ms = self.wait_topic(since, "piano/shortcut/reply", 15, lambda x: x.startswith(str(self.chat) + "|"))
        self.remote_row("telegram", "/pianoplay: הגשר ענה", ms, ms is not None)
        ms = self.wait_topic(since, "piano/soak/ha/tgsent", 25)
        self.remote_row("telegram", "/pianoplay: התשובה יצאה לטלגרם", ms, ms is not None)
        self.wait(self.rng.uniform(8, 15))
        if self.retained.get("piano/play/status") != "playing":
            why = self.retained.get("piano/play/stopped_by", "") or "?"
            self.remote_row("telegram", "השיר נעצר לפני /pianostop", None, False, why)
            self.event(f"השיר מטלגרם נעצר לבד לפני פקודת העצירה: {why}")
        since = self.tg("/pianostop")
        ms = self.wait_topic(since, "piano/play/status", 15, lambda x: x == "idle")
        self.remote_row("telegram", "/pianostop: השיר נעצר", ms, ms is not None)
        ms = self.wait_topic(since, "piano/soak/ha/tgsent", 25)
        self.remote_row("telegram", "/pianostop: התשובה יצאה לטלגרם", ms, ms is not None)
        self.counts["tg_rounds"] += 1
        self.wait(max(0, dur - 60))

    def ha_setup(self):
        url = self.args.ha_url or self.secrets.get("HA_DEVICE_URL") or self.secrets.get("HA_URL")
        token = self.secrets.get("HA_DEVICE_TOKEN") or self.secrets.get("HA_TOKEN")
        if self.args.no_ha or not (url and token):
            self.event("Home Assistant לא נבדק: " + ("--no-ha" if self.args.no_ha else
                       "אין HA_URL ו-HA_TOKEN ב-/etc/armonico/secrets.env (אפשר קובץ משלך עם --secrets)"))
            return
        self.ha = HA(url, token)
        code, _, ms = self.ha.call("GET", "/api/")
        if code != 200:
            self.event(f"Home Assistant לא עונה ב-{url} (HTTP {code}): הבדיקות שלו ידולגו")
            self.ha = None
            return
        left = self.ha.remove_ours()
        if left:
            self.event(f"נמחקו {left} אוטומציות שנשארו מבדיקה קודמת")
        if not self.chat and not self.args.no_telegram:
            code, st, _ = self.ha.call("GET", "/api/states/input_text.piano_alert_chat_id")
            val = str((st or {}).get("state", ""))
            self.chat = val if re.fullmatch(r"-?\d+", val) else ""
        telegram = bool(self.chat) and not self.args.no_telegram
        for key, conf in soak_automations(telegram).items():
            code, _, _ = self.ha.call("POST", f"/api/config/automation/config/{SOAK_ID}{key}", conf)
            if code != 200:
                self.event(f"לא נוצרה אוטומציה זמנית {key} (HTTP {code})")
        for _ in range(20):
            self.ha_ids = self.ha.automations()
            if len(self.ha_ids) >= (5 if telegram else 4):
                break
            time.sleep(1)
        (self.out / "ha.json").write_text(json.dumps({"url": url, "ids": self.ha_ids}, indent=1))
        if SOAK_ID + "echo" not in self.ha_ids:
            self.event("האוטומציות הזמניות לא נוצרו ב-HA: הבדיקות שלו ידולגו")
            self.ha = None
            return
        self.ha_ready = True
        self.tg_ok = telegram
        self.event(f"נוצרו {len(self.ha_ids)} אוטומציות זמניות ב-HA" +
                   ("" if telegram else ". טלגרם לא נבדק: לא נמצא צ'אט (piano_alert_chat_id), אפשר --chat-id"))

    def ha_cleanup(self):
        if not self.ha:
            return
        self.ha_ready = False
        n = self.ha.remove_ours()
        left = self.ha.automations()
        self.event(f"נמחקו {n} אוטומציות זמניות מ-HA" + (f", נשארו {len(left)}: sudo armonico Soak10 ha-clean"
                                                          if left else ""))

    # ---- הכנה ושחזור
    def preflight(self):
        bad = []
        for u in NEEDED:
            rc, out, _ = run(["systemctl", "is-active", u], 5)
            if out.strip() != "active":
                bad.append(u)
        if bad:
            raise SystemExit("שירותים לא פעילים: " + ", ".join(bad) + ". אין טעם להריץ בדיקת עומס כך")
        if ACTIVE.exists():
            raise SystemExit("בדיקה קודמת לא נוקתה. קודם: sudo armonico Soak10 recover")
        self.connect()
        if self.retained.get("piano/device/state") != "online":
            raise SystemExit("המכשיר (armonico-device) לא מחובר לברוקר")
        self.safe_to_start()
        for k, topic in (("octave", "piano/device/octave"), ("voice", "piano/device/voice"),
                         ("pedal", "piano/device/pedal")):
            if topic in self.retained:
                self.orig[k] = self.retained[topic]
        code, ov = self.api_json("GET", "/api/overview")
        if code != 200:
            raise SystemExit(f"מנוע השיעורים לא עונה ל-/api/overview (HTTP {code}). בדוק את קוד המסך")
        self.songs = [s["name"] for s in ov.get("songs", []) if s.get("name")]
        if not self.songs:
            raise SystemExit("אין שירים במכשיר")
        self.load_melodies()
        prof = ov.get("profiles", {})
        self.orig_profile = prof.get("active")
        me = next((p for p in prof.get("list", []) if p["id"] == self.orig_profile), {})
        if me.get("locked") and not self.args.pin:
            self.lessons_ok = False
            self.event("הפרופיל הפעיל נעול ב-PIN ולא ניתן --pin: השיעורים ידולגו כדי לא לגעת בהתקדמות שלך")
        elif not self.args.no_lessons:
            self.make_profile(prof)
        else:
            self.lessons_ok = False
        self.ha_setup()
        info = {"start": time.strftime("%Y-%m-%d %H:%M:%S"), "minutes": self.args.minutes,
                "broker": "מקומי" if self.env.get("MQTT_HOST", "localhost") in ("localhost", "127.0.0.1", "::1", "")
                else f"ברשת ({self.env.get('MQTT_HOST')})", "ha": bool(self.ha_ready),
                "telegram": bool(self.ha_ready and getattr(self, "tg_ok", False)),
                "seed": self.args.seed, "songs": len(self.songs), "orig": self.orig,
                "version": read("/opt/armonico/VERSION").strip(),
                "model": read("/proc/device-tree/model").strip("\0 \n"),
                "mem_total": round(Monitor.meminfo()[3]), "kernel": os.uname().release,
                "rest": json.loads(read(DATA / "device" / "settings.json", "{}") or "{}").get("rest", "animation"),
                "lessons": self.lessons_ok, "rec": not self.args.no_rec, "alarm": not self.args.no_alarm}
        (self.out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
        self.save_active()
        self.arm_recovery()
        (self.out / "inventory_start.json").write_text(json.dumps(inventory(), ensure_ascii=False, indent=1))
        if self.args.boot_info and Path(self.args.boot_info).exists():
            shutil.copy2(self.args.boot_info, self.out / "boot_before.json")
        log(f"מוכן: {len(self.songs)} שירים, {info['model']}, {info['mem_total']} MB, תוצאות ב-{self.out}")

    def make_profile(self, prof):
        found = next((p for p in prof.get("list", []) if p.get("name") == TEST_PROFILE), None)
        if not found:
            code, res = self.api_json("POST", "/profiles/add", {"name": TEST_PROFILE})
            if code != 200:
                self.lessons_ok = False
                self.event(f"לא נוצר פרופיל בדיקה (HTTP {code}): השיעורים ידולגו")
                return
            found = next((p for p in res.get("profiles", {}).get("list", []) if p.get("name") == TEST_PROFILE), None)
        if not found:
            self.lessons_ok = False
            return
        code, _ = self.api_json("POST", "/profiles/switch", {"id": found["id"]})
        if code != 200:
            self.lessons_ok = False
            self.event(f"לא עבר לפרופיל הבדיקה (HTTP {code}): השיעורים ידולגו")
            return
        self.test_profile = found["id"]
        self.event(f"עבר לפרופיל זמני \"{TEST_PROFILE}\"")

    def cleanup(self):
        log("מנקה ומחזיר את המכשיר למצב שלפני הבדיקה")
        self.strip("stopped" if self.abort_reason else "end")
        ended = time.monotonic()
        try:
            self.api_json("POST", "/lessonstop", {})     # also one still being prepared
            self.pub("piano/device/rec", "off")
            self.pub("piano/stop", "Soak test")
            time.sleep(2)
            if "octave" in self.orig:
                self.pub("piano/device/octave/set", self.orig["octave"])
            if "voice" in self.orig:
                self.pub("piano/device/voice/set", self.orig["voice"])
            self.pub("piano/device/pedal/set", self.orig.get("pedal", "off"))
            for _ in range(3):
                self.pub("piano/device/note", "off 0")
            with self.offs_lock:
                left = [n for _, n in self.offs]
                self.offs.clear()
            for n in set(left):
                self.pub("piano/device/note", f"off {n}")
            self.deadline = time.monotonic() + 30
            self.quit.clear()
            self.home()
        except Exception as e:
            log(f"שחזור המכשיר: {e}")
        try:
            self.ha_cleanup()
        except Exception as e:
            log(f"ניקוי HA: {e}")
        if self.test_profile:
            for _ in range(10):                           # אחרי שהשיעור נעצר
                code, _ = self.api_json("POST", "/profiles/switch", {"id": self.orig_profile, "pin": self.args.pin or ""})
                if code == 200:
                    break
                time.sleep(2)
            if code == 200:
                code, _ = self.api_json("POST", "/profiles/delete", {"id": self.test_profile})
                self.event("הפרופיל שלך חזר, ופרופיל הבדיקה נמחק" if code == 200 else
                           f"הפרופיל שלך חזר; פרופיל הבדיקה לא נמחק (HTTP {code}), אפשר למחוק אותו במסך הפרופילים")
            else:
                self.event(f"לא הצליח לחזור לפרופיל שלך (HTTP {code}). לחזור ידנית במסך השיעורים")
        time.sleep(5 if self.abort_reason else 20)        # הקלטה אחרונה שעוד נשמרת
        new = [n for n in self.recordings() if n not in self.rec_before]
        mb = 0.0
        for n in new:
            f = DATA / "device" / "recordings" / n
            try:
                mb += f.stat().st_size / 1048576
                f.unlink()
            except OSError:
                pass
        if new:
            self.event(f"נמחקו {len(new)} הקלטות מסך שהבדיקה יצרה ({mb:.0f} MB)")
        self.counts["rec_mb"] = round(mb, 1)
        self.ended = ended

    # ---- הלולאה
    def main(self):
        self.preflight()
        mon = Monitor(self.out, self)
        threading.Thread(target=mon.loop, daemon=True).start()
        net = Net(self)
        threading.Thread(target=net.loop, daemon=True).start()
        threading.Thread(target=self.releaser, daemon=True).start()
        threading.Thread(target=self.partial_reports, daemon=True).start()
        threading.Thread(target=self.beat, daemon=True).start()
        threading.Thread(target=self.quiet_shortcuts, daemon=True).start()

        def stop(sig, frm):
            log("התקבלה בקשת עצירה")
            self.quit.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        self.strip("start")
        self.wait(BASELINE)                               # חצי דקה של מנוחה בהתחלה: קו בסיס
        self.phases.append((0, self.t(), "rest"))
        last_alarm = last_rec = last_ha = last_tg = -1e9
        # סבב ראשון: כל סוג שלב פעם אחת בסדר אקראי, באורך שמתאים לזמן שיש, כך שגם בהרצה קצרה הכל נבדק.
        # אחריו ממשיכים בהגרלה רגילה לפי משקל
        video = REST_VIDEO.exists()
        cover = ["free", "song", "browse", "rest"] + (["video"] if video else []) + \
            (["lesson"] if self.lessons_ok else []) + \
            ([] if self.args.no_alarm else ["alarm"]) + ([] if self.args.no_rec else ["rec"]) + \
            (["ha"] if self.ha_ready else []) + (["telegram"] if self.ha_ready and getattr(self, "tg_ok", False) else [])
        self.rng.shuffle(cover)
        floor = {"alarm": 15, "telegram": 75}
        while self.left() > 20 and not self.quit.is_set():
            now = time.monotonic()
            weights = {"free": 30, "song": 15, "browse": 10, "rest": 8}
            if video:
                weights["video"] = 12                    # the rest screen's video: the heaviest thing it does alone
            if self.lessons_ok:
                weights["lesson"] = 20
            if not self.args.no_alarm and now - last_alarm > 50 * 60:
                weights["alarm"] = 8
            if not self.args.no_rec and now - last_rec > 60 * 60:
                weights["rec"] = 8
            if self.ha_ready and now - last_ha > 15 * 60:
                weights["ha"] = 12
            if self.ha_ready and getattr(self, "tg_ok", False) and now - last_tg > self.args.tg_every * 60:
                weights["telegram"] = 12
            ranges = {"free": (240, 600), "song": (60, 240), "lesson": (360, 720), "browse": (60, 180),
                      "rest": (120, 360), "video": (300, 900), "alarm": (15, 30), "rec": (60, 90), "ha": (60, 90), "telegram": (75, 100)}
            if cover:
                kind = cover.pop(0)
                share = (self.left() - 20) / (len(cover) + 1)
                dur = max(floor.get(kind, 60), min(self.rng.uniform(*ranges[kind]), share))
            else:
                kind = self.rng.choices(list(weights), list(weights.values()))[0]
                dur = self.rng.uniform(*ranges[kind])
            dur = min(dur, max(10, self.left() - 15))
            start = self.t()
            log(f"שלב: {PHASE_HE[kind]} ({dur / 60:.1f} דק')")
            self.act(kind, "התחלה")
            self.strip("run", kind)
            try:
                getattr(self, "phase_" + kind)(dur)
            except Exception as e:
                self.event(f"שלב {PHASE_HE[kind]} נכשל: {type(e).__name__}: {e}")
            self.nothing_left_playing(kind)
            if kind == "alarm":
                last_alarm = now
            if kind == "rec":
                last_rec = now
            if kind == "ha":
                last_ha = now
            if kind == "telegram":
                last_tg = now
            self.phases.append((start, self.t(), kind))
            self.save_state()
        mon.stop.set()
        net.stop.set()
        time.sleep(1)
        try:
            (self.out / "inventory_end.json").write_text(json.dumps(inventory(), ensure_ascii=False, indent=1))
        except Exception as e:
            log(f"תמונת מצב בסוף: {e}")
        self.cleanup()
        self.save_state()
        for c in (mon.sys_csv, mon.proc_csv, mon.slow_csv, self.lat_csv, self.act_csv, net.csv, self.remote_csv,
                  self.ha_keys_csv):
            c.close()
        (self.out / "journal.json").write_text(json.dumps(
            {"counts": dict(mon.problems), "examples": mon.examples, "xruns": mon.xruns_total,
             "alsa_underruns": mon.alsa_underruns,
             "times": [mon.example_n.get((src, re.sub(r"\d+", "#", m[:200])), 1) for _, src, m in mon.examples],
             "common": [[u, m, n] for (u, m), n in mon.common.most_common(20)]},
            ensure_ascii=False, indent=1))
        report(self.out)
        z = pack(self.out)
        self.disarm_recovery()
        home = home_copy(z)
        write_status(state="done", result=result_line(self.out), zip=z.name)
        if self.ha and self.chat and not self.args.no_telegram:
            sent = send_summary(self.out, self.ha, self.chat, self.secrets.get("TELEGRAM_TOKEN", ""), home or z)
            log("הסיכום נשלח לטלגרם" if sent else "הסיכום לא נשלח לטלגרם")
        log((f"נעצרה: {self.abort_reason}. " if self.abort_reason else "הסתיים. ") + f"הדוח: {self.out / 'report.html'}")
        log(f"הכל בקובץ אחד לשליחה: {z}")
        time.sleep(max(0, 60 - (time.monotonic() - getattr(self, "ended", 0))))   # "הסתיימה" נשאר דקה במסך
        self.pub("piano/device/soak", "", retain=True)
        time.sleep(1)
        self.mqtt.loop_stop()
        if self.args.reboot_after:
            # ריבוט רגיל בסוף, ואחריו רישום אחד של זמן העלייה לדוח. שום בדיקה לא מתחילה מחדש
            (OUT_ROOT / "after-boot.json").write_text(json.dumps({"out": str(self.out)}))
            install_unit(AFTER_UNIT, "Armonico soak test: boot time after the test (once, then removed)",
                         f"{venv_python()} {keep_copy()} after-boot", OUT_ROOT / "after-boot.json")
            log("ריבוט רגיל בסוף הבדיקה")
            run(["systemctl", "reboot"], 20)

    def save_state(self):
        (self.out / "run.json").write_text(json.dumps(
            {"phases": self.phases, "events": self.events, "counts": dict(self.counts), "abort": self.abort_reason,
             "abort_en": self.abort_en,
             "end": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed": self.t()}, ensure_ascii=False, indent=1))

    def partial_reports(self):
        """דוח ביניים כל 15 דקות, כדי שיהיה מה לפתוח גם אם משהו נופל באמצע."""
        while not self.quit.wait(15 * 60) and self.left() > 60:
            try:
                self.save_state()
                report(self.out, partial=True)
            except Exception as e:
                log(f"דוח ביניים: {e}")


def recover():
    """אחרי בדיקה שנקטעה (ריסטרט, כיבוי, קריסה): מחזיר את המכשיר, מוחק את מה שהבדיקה יצרה ושומר דוח.
    לא מתחיל בדיקה מחדש."""
    try:
        d = json.loads(read(ACTIVE) or "{}")
    except ValueError:
        d = {}
    if not d:
        Soak.disarm_recovery()
        print("אין בדיקה שנקטעה")
        return
    out = Path(d["out"])
    log(f"בדיקה שנקטעה ב-{out.name}: מחזיר את המכשיר")
    a = argparse.Namespace(minutes=d.get("minutes", 0), seed=0, out=str(out), pin=d.get("pin", ""), no_lessons=True,
                           no_rec=True, no_alarm=True, secrets=d.get("secrets") or None, chat_id=None, ha_url=None,
                           no_ha=True, no_telegram=True, tg_every=30)
    s = Soak(a)
    s.t0 = d.get("t0", time.time())
    s.orig, s.orig_profile, s.test_profile = d.get("orig", {}), d.get("orig_profile"), d.get("test_profile")
    s.rec_before = set(d.get("rec_before", []))
    st = json.loads(read(out / "run.json", "{}") or "{}")
    s.phases, s.events = st.get("phases", []), [tuple(x) for x in st.get("events", [])]
    s.counts.update(st.get("counts", {}))
    last = rows(out / "system.csv")
    stopped_at = num(last[-1]["t"]) if last else 0
    s.events.append((stopped_at or 0, "הבדיקה נקטעה כאן (ריסטרט, כיבוי או קריסה) ולא חזרה"))
    s.abort_reason, s.abort_en = "נקטעה באמצע (ריסטרט, כיבוי או קריסה)", "interrupted (restart, power or crash)"
    for i in range(36):                               # עד 3 דקות לשירותים אחרי עלייה
        try:
            s.connect()
            if s.api_json("GET", "/api/overview")[0] == 200:
                break
        except SystemExit:
            pass
        time.sleep(5)
    if d.get("ha_url"):
        sec = load_secrets(d.get("secrets") or None)
        tok = sec.get("HA_DEVICE_TOKEN") or sec.get("HA_TOKEN")
        if tok:
            s.ha = HA(d["ha_url"], tok)
    if s.mqtt is not None:
        s.cleanup()
    s.save_state()
    st = json.loads(read(out / "run.json", "{}") or "{}")
    st["elapsed"] = stopped_at or st.get("elapsed", 0)
    st["interrupted"] = True
    (out / "run.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    report(out)
    z = pack(out)
    Soak.disarm_recovery()
    remove_unit(BOOT_UNIT)
    home = home_copy(z)
    write_status(state="done", result=result_line(out), zip=z.name)
    chat = d.get("chat", "")
    if s.ha and not chat and not d.get("no_telegram"):
        code, sv, _ = s.ha.call("GET", "/api/states/input_text.piano_alert_chat_id")
        chat = str((sv or {}).get("state", ""))
    if s.ha and not d.get("no_telegram"):
        send_summary(out, s.ha, chat, load_secrets(d.get("secrets") or None).get("TELEGRAM_TOKEN", ""), home or z)
    if s.mqtt is not None:
        s.pub("piano/device/soak", "", retain=True)
        time.sleep(1)
    log(f"הוחזר. הדוח: {out / 'report.html'} · ZIP: {z}")


def parse_duration(text):
    """15m, 90m, 1h, 1h30m, 4h, או מספר דקות."""
    t = str(text).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t)
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m)?", t)
    if not m or not (m.group(1) or m.group(2)):
        raise SystemExit(f"זמן לא מובן: {text}. דוגמאות: 15m, 30m, 1h, 1h30m, 4h")
    return float(m.group(1) or 0) * 60 + float(m.group(2) or 0)


def launch(minutes, rest, boot_info_file=None, reboot_after=False):
    """הבדיקה עצמה כשירות זמני של systemd: ממשיכה גם כש-SSH מתנתק, ולא חוזרת אחרי ריסטרט."""
    py = venv_python()
    cmd = ["systemd-run", f"--unit={UNIT}", "--collect", "-p", "TimeoutStopSec=300", "-p", "Nice=10",
           "-p", "IOSchedulingClass=idle", py, str(keep_copy()), "run", "--minutes", str(minutes)] + list(rest)
    if boot_info_file:
        cmd += ["--boot-info", str(boot_info_file)]
    if reboot_after:
        cmd += ["--reboot-after"]
    rc, out, err = run(cmd, 20)
    if rc != 0:
        raise SystemExit("לא הופעלה: " + (err or out).strip())


def start(a, rest):
    if not ETC.exists() or not Path(f"/opt/{APP}/device").is_dir():
        raise SystemExit("זה לא נראה כמו Armonico Pi: אין /etc/armonico/config.env או /opt/armonico/device")
    down = [u for u in NEEDED if sh(["systemctl", "is-active", u], 5) != "active"]
    if down:
        raise SystemExit("שירותים לא פעילים: " + ", ".join(down) + ". הבדיקה לא הופעלה")
    minutes = parse_duration(a.duration)
    if not 5 <= minutes <= MAX_MINUTES:
        raise SystemExit("הזמן צריך להיות בין 5 דקות ל-12 שעות")
    if run(["systemctl", "is-active", UNIT], 5)[1].strip() in ("active", "activating"):
        raise SystemExit("בדיקה כבר רצה. מצב: sudo armonico Soak10 status")
    if ACTIVE.exists():
        raise SystemExit("בדיקה קודמת לא נוקתה. קודם: sudo armonico Soak10 recover")
    h, m = divmod(int(minutes), 60)
    if a.no_reboot:
        launch(minutes, rest)
        print(f"הבדיקה התחילה: {h}:{m:02d} שעות, ועוד כדקה וחצי לניקוי ולדוח.")
    else:
        keep = keep_copy()
        BOOT_FLAG.write_text(json.dumps({"minutes": minutes, "rest": rest, "at": time.time()}))
        os.chmod(BOOT_FLAG, 0o600)
        install_unit(BOOT_UNIT, "Armonico soak test: start once after this reboot (the flag is removed first)",
                     f"{venv_python()} {keep} boot", BOOT_FLAG)
        write_status(state="waiting", total=round(minutes * 60), t0=0, phase="", phase_en="", abort="", abort_en="")
        print(f"ה-Pi עולה מחדש עכשיו. אחרי העלייה, כשהכל מוכן, הבדיקה מתחילה לבד: {h}:{m:02d} שעות.")
        print("בסוף הבדיקה ה-Pi עולה מחדש פעם אחת, רגיל, בלי בדיקה.")
    print("  מה קורה:  journalctl -u armonico-soak -f")
    print("  מצב:      sudo armonico Soak10 status")
    print("  עצירה:    sudo armonico Soak10 stop")
    if not a.no_reboot and not a.reboot_later:
        time.sleep(3)
        run(["systemctl", "reboot"], 20)


def boot():
    """רץ מיחידת ה-systemd אחרי הריבוט שלפני הבדיקה. מוחק את הדגל לפני הכל, כך שריסטרט נוסף לא יתחיל
    בדיקה שוב, מודד את זמן העלייה ומפעיל את הבדיקה."""
    try:
        flag = json.loads(read(BOOT_FLAG) or "{}")
    except ValueError:
        flag = {}
    try:
        BOOT_FLAG.unlink()
    except OSError:
        pass
    remove_unit(BOOT_UNIT)
    if not flag or time.time() - flag.get("at", 0) > 3600:
        log("אין דגל עדכני להתחלה אחרי עלייה: לא מתחיל")
        write_status(state="idle")
        return
    write_status(state="booting")
    ready = wait_ready(load_env())
    if ready is None:
        log("השירותים לא עלו תוך 5 דקות מההדלקה: הבדיקה לא מתחילה")
        write_status(state="done", result={"level": 2, "he": "לא התחילה: השירותים לא עלו תוך 5 דקות",
                                            "en": "Not started: the services were not up within 5 minutes"})
        return
    log(f"הכל מוכן {ready:.0f} שניות אחרי ההדלקה; עוד דקה של שקט לפני הבדיקה")
    time.sleep(60)
    info = boot_info()
    info["ready_s"] = ready
    f = OUT_ROOT / "boot-before.json"
    f.write_text(json.dumps(info, ensure_ascii=False, indent=1))
    launch(flag["minutes"], flag.get("rest", []), f, reboot_after=True)


def after_boot():
    """פעם אחת אחרי הריבוט שבסוף: זמן העלייה נכנס לדוח, וה-ZIP נארז מחדש."""
    try:
        d = json.loads(read(OUT_ROOT / "after-boot.json") or "{}")
    except ValueError:
        d = {}
    try:
        (OUT_ROOT / "after-boot.json").unlink()
    except OSError:
        pass
    remove_unit(AFTER_UNIT)
    out = Path(d.get("out", ""))
    if not out.is_dir():
        return
    ready = wait_ready(load_env())
    info = boot_info()
    info["ready_s"] = ready
    (out / "boot_after.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
    report(out)
    pack(out)
    log("זמן העלייה אחרי הבדיקה נוסף לדוח")


def status():
    active = run(["systemctl", "is-active", UNIT], 5)[1].strip()
    if BOOT_FLAG.exists():
        print("ממתין לריבוט: הבדיקה תתחיל אחרי העלייה")
        return
    if sh(["systemctl", "is-active", BOOT_UNIT.name], 5) in ("active", "activating"):
        print("ה-Pi עלה, וממתין שהכל יעלה ויענה ועוד דקה של שקט. הבדיקה תתחיל לבד")
        return
    d = json.loads(read(ACTIVE) or "{}") if ACTIVE.exists() else {}
    if not d:
        print("אין בדיקה פעילה" + (" (השירות במצב " + active + ")" if active not in ("inactive", "") else ""))
        last = sorted(OUT_ROOT.glob("soak-*.zip"))
        if last:
            print("התוצאה האחרונה:", last[-1])
        return
    el = time.time() - d["t0"]
    st = json.loads(read(Path(d["out"]) / "run.json", "{}") or "{}")
    ph = st.get("phases", [])
    print(f"רצה {int(el // 3600)}:{int(el % 3600 // 60):02d} מתוך {int(d['minutes'] // 60)}:{int(d['minutes'] % 60):02d}"
          f" · שלבים שהסתיימו {len(ph)} · שירות {active}")
    if active not in ("active", "activating"):
        print("השירות לא רץ אבל הבדיקה לא נוקתה: sudo armonico Soak10 recover")


def stop_cmd():
    """עצירה: בדיקה שרצה נעצרת ושומרת דוח; בדיקה שמחכה לריבוט מתבטלת ולא תתחיל."""
    pending = BOOT_FLAG.exists()
    for f in (BOOT_FLAG,):
        try:
            f.unlink()
        except OSError:
            pass
    remove_unit(BOOT_UNIT)
    active = run(["systemctl", "is-active", "--quiet", UNIT], 5)[0] == 0
    if active:
        run(["systemctl", "stop", UNIT], 320)
        print("נעצרה, הדוח נשמר")
    elif pending:
        write_status(state="idle")
        print("הבדיקה שחיכתה לריבוט בוטלה")
    else:
        print("לא רצה בדיקה")
    return status()


def result_line(out):
    """The line the screen's card shows for the last test: how it ended and its worst finding."""
    out = Path(out)
    st = json.loads(read(out / "run.json", "{}") or "{}")
    v = json.loads(read(out / "verdicts.json", "[]") or "[]")
    worst = max([x[0] for x in v] + [0])
    if st.get("interrupted"):
        he, en, lvl = "נקטעה באמצע", "Cut off in the middle", 2
    elif st.get("abort"):
        he, en, lvl = "נעצרה: " + st["abort"], "Stopped: " + (st.get("abort_en") or ""), 2
    else:
        he = {0: "עברה, הכל תקין", 1: "עברה, יש מה לבדוק", 2: "נמצאו בעיות"}[worst]
        en = {0: "Passed, all fine", 1: "Passed, some things to look at", 2: "Problems found"}[worst]
        lvl = worst
    bad = [x[1] for x in v if x[0] == 2][:3]
    return {"level": lvl, "he": he + (" · " + ", ".join(bad) if bad and lvl == 2 and not st.get("abort") else ""),
            "en": en, "out": out.name, "hours": round(float(st.get("elapsed", 0)) / 3600, 2)}


def summary_text(out, zip_path=None):
    """ההודעה הקצרה לטלגרם בסוף הבדיקה: התוצאה, מה לבדוק, כמה מספרים עיקריים ואיפה הדוח."""
    out = Path(out)
    r = result_line(out)
    v = json.loads(read(out / "verdicts.json", "[]") or "[]")
    val = {x[1]: str(x[2]) for x in v}
    icon = {0: "✅", 1: "🟡", 2: "🔴"}.get(r["level"], "🔴")
    rl = "\u200f"                                  # כל שורה נפתחת מימין לשמאל גם כשיש בה מספר או מילה לועזית
    h = float(r["hours"] or 0)
    took = f"{h * 60:.0f} דק'" if h < 1 else f"{h:.1f} שעות"
    lines = [f"{icon} בדיקת העומס של Armonico הסתיימה", f"התוצאה: {r['he']} · {took}"]
    look = [x[1] for x in v if x[0] >= 1]
    if look:
        lines.append("לבדוק: " + ", ".join(look[:5]) + (f" ועוד {len(look) - 5}" if len(look) > 5 else ""))
    def part(name, *idx, sep=" · "):
        bits = [b.strip() for b in val.get(name, "").split(sep)]
        return " · ".join(bits[i] for i in idx if -len(bits) <= i < len(bits) and bits[i])
    for short, text in (("מעבד", part("עומס מעבד", 0, -1)), ("חום", part("טמפרטורת המעבד", 0, sep=",")),
                        ("זיכרון", part("זיכרון פנוי", 1)), ("קליד", part("השהיית קליד (מגע עד הגשר)", 1, -1))):
        if text:
            lines.append(f"{short}: {text}")
    if zip_path:
        lines.append(f"הדוח: {Path(zip_path).name} בתיקיית הבית")
    return "\n".join(rl + x for x in lines)


def send_summary(out, ha, chat, token="", zip_path=None):
    """שולח את הסיכום לטלגרם: דרך HA (telegram_bot.send_message, כמו שאר ההודעות של הפסנתר), ואם HA לא
    הצליח ויש טוקן במכשיר, ישר ל-Bot API. sendMessage לא מפריע ל-HA שמושך עדכונים. כשל לא עוצר כלום."""
    if not chat or not re.fullmatch(r"-?\d+", str(chat)):
        return False
    try:
        text = summary_text(out, zip_path)
    except Exception as e:
        log(f"סיכום לטלגרם: {e}")
        return False
    if ha is not None:
        code, _, _ = ha.call("POST", "/api/services/telegram_bot/send_message",
                             {"chat_id": int(chat), "message": text, "parse_mode": "plain_text"}, timeout=20)
        if code == 200:
            return True
        log(f"סיכום לטלגרם דרך HA: HTTP {code}")
    if token:
        body = urllib.parse.urlencode({"chat_id": int(chat), "text": text}).encode()
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=15) as r:
                return r.status == 200
        except (OSError, ValueError) as e:
            log(f"סיכום לטלגרם ישירות: {type(e).__name__}")
    return False


def pack(out):
    z = out.parent / f"{out.name}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for p in sorted(out.iterdir()):
            f.write(p, f"{out.name}/{p.name}")
    return z


# ====================================================================== הדוח
def rows(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))]


def slope(ts, vs):
    """שיפוע ב-MB לשעה, מהרבע שעה הראשונה והלאה (אחרי החימום)."""
    pts = [(t, v) for t, v in zip(ts, vs) if v is not None and t > 900]
    if len(pts) < 20:
        return None
    mt = statistics.fmean(p[0] for p in pts)
    mv = statistics.fmean(p[1] for p in pts)
    den = sum((p[0] - mt) ** 2 for p in pts)
    return 0 if den == 0 else sum((p[0] - mt) * (p[1] - mv) for p in pts) / den * 3600


def bucket(ts, vs, size, fn):
    out = defaultdict(list)
    for t, v in zip(ts, vs):
        if v is not None:
            out[int(t // size)].append(v)
    keys = sorted(out)
    return [(k * size + size / 2) for k in keys], [fn(out[k]) for k in keys]


CHART_N = [0]


def chart(title, series, unit, span, phases, height=200, ymin=None, ymax=None, rule=None, note=""):
    """גרף קווי SVG עם פס שלבים למעלה ותווית מעבר עכבר. series: [(שם, צבע, ts, vs)]."""
    CHART_N[0] += 1
    cid = f"c{CHART_N[0]}"
    W, H, L, R, T, B = 900, height, 56, 16, 22, 26
    allv = [v for s in series for v in s[3] if v is not None]
    if not allv:
        return f'<section class="chart"><h3>{title}</h3><p class="muted">אין נתונים</p></section>'
    lo = ymin if ymin is not None else min(0, min(allv))
    hi = ymax if ymax is not None else max(allv) * 1.1 or 1
    if rule is not None:
        hi = max(hi, rule * 1.05)
    span = max(span, 1)

    def X(t):
        return L + (W - L - R) * t / span

    def Y(v):
        return T + (H - T - B) * (1 - (v - lo) / ((hi - lo) or 1))

    g = []
    for i in range(5):                                  # רשת ותוויות ציר
        v = lo + (hi - lo) * i / 4
        y = Y(v)
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        g.append(f'<text x="{L - 6}" y="{y + 4:.1f}" class="ax" text-anchor="end">{v:.0f}</text>')
    hrs = span / 3600
    step = 0.5 if hrs <= 3 else 1
    if hrs < 0.75:
        step = 0.25 if hrs > 0.3 else 1 / 12
    k = 0.0
    while k <= hrs + 1e-9:
        x = X(k * 3600)
        lab = f"{k:g} ש'" if step >= 0.25 else f"{k * 60:.0f} ד'"
        g.append(f'<text x="{x:.1f}" y="{H - 8}" class="ax" text-anchor="middle">{lab}</text>')
        k += step
    for a, b, kind in phases:                           # פס השלבים
        g.append(f'<rect x="{X(a):.1f}" y="4" width="{max(1, X(b) - X(a) - 1):.1f}" height="8" rx="2" '
                 f'fill="{PHASE_COLOR.get(kind, "#999")}"><title>{PHASE_HE.get(kind, kind)}</title></rect>')
    if rule is not None:
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(rule):.1f}" y2="{Y(rule):.1f}" class="rule"/>')
    data = []
    for name, color, ts, vs in series:
        pts, seg = [], []
        for t, v in zip(ts, vs):
            if v is None:
                if len(seg) > 1:
                    pts.append(seg)
                seg = []
                continue
            seg.append(f"{X(t):.1f},{Y(v):.1f}")
        if len(seg) > 1:
            pts.append(seg)
        for s in pts:
            g.append(f'<polyline points="{" ".join(s)}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round"/>')
        step_n = max(1, len(ts) // 400)
        data.append({"n": name, "c": color, "t": [round(t) for t in ts[::step_n]],
                     "v": [None if v is None else round(v, 1) for v in vs[::step_n]]})
    legend = "" if len(series) < 2 else '<div class="legend">' + "".join(
        f'<span><i style="background:{c}"></i>{html.escape(n)}</span>' for n, c, _, _ in series) + "</div>"
    meta = json.dumps({"L": L, "R": R, "W": W, "span": span, "unit": unit, "s": data}, ensure_ascii=False)
    return (f'<section class="chart"><h3>{title}</h3>{legend}'
            f'<div class="plot" id="{cid}"><svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">{"".join(g)}'
            f'<line class="cross" x1="0" x2="0" y1="{T}" y2="{H - B}"/></svg><div class="tip"></div></div>'
            f'{f"<p class=note>{note}</p>" if note else ""}'
            f'<script type="application/json" class="meta" data-for="{cid}">{meta}</script></section>')


def jget(inv, key, default=None):
    try:
        v = json.loads(inv.get(key) or "null")
    except ValueError:
        v = None
    return v if v is not None else ({} if default is None else default)


def usb_text(inv):
    ext = jget(inv, "usb_ext", [])
    if "usb_ext" not in inv:
        return f"{len(inv.get('usb', '').splitlines())} שורות ב-lsusb (כולל ה-hubs שבלוח)"
    if not ext:
        return "אין התקנים חיצוניים (ה-root hubs וה-hub של VL805 שבלוח לא נספרים)"
    return f"{len(ext)} חיצוניים: " + " · ".join(f"{e['name']} ({e['id']}, {e['speed']} Mb/s)" for e in ext)


def regdom_text(inv):
    r = jget(inv, "regdom")
    if not r:
        return (re.search(r"country (\w+)", inv.get("wifi_reg", "")) or [None, ""])[1]
    t = f"בפועל {r.get('global') or '?'}" + (f" · ה-cmdline מבקש {r['want']}" if r.get("want") else "")
    return t + (" · " + r["he"] if not r.get("ok") and r.get("he") else "")


def gpio_note(inv):
    low = jget(inv, "gpio_low", [])
    if not low:
        return ""
    return (" · פינים במשיכה למעלה שקוראים 0: " + ", ".join(f"GPIO{n}" for n in low)
            + ". כובע או רכיב על הלוח מחזיק אותם למטה (כובע Argon משתמש ב-GPIO4 לכפתור שלו)")


def wear_section(out, i0, slow, span, V):
    """שחיקת הכרטיס: כרטיס SD לא מדווח כמה הוא שחוק, אז מודדים את מה ששוחק אותו: כמה נכתב, מי כתב,
    ומה בהגדרות המערכת מגדיל את זה (swap על הכרטיס, יומן שנשמר בו, / בלי noatime)."""
    sd = [num(r.get("sd_w_mb")) for r in slow if num(r.get("sd_w_mb")) is not None]
    hours = max(span / 3600, 0.05)
    rows_, rate = [], None
    if sd:
        rate = sd[-1] / hours
        rows_ += [("נכתב לכרטיס בבדיקה", f"{sd[-1]:.0f} MB"), ("לשעה", f"{rate:.0f} MB"),
                  ("אם המכשיר עובד ככה 24 שעות", f"{rate * 24 / 1024:.1f} GB ליום"),
                  ("בשימוש של שעתיים ביום", f"{rate * 2 * 365 / 1024:.0f} GB לשנה")]
        V(1 if rate > 300 else 0, "כתיבה לכרטיס SD", f"{sd[-1]:.0f} MB, כ-{rate:.0f} MB לשעה",
          "הרבה כתיבה שוחקת כרטיס SD לאורך שנים" if rate > 300 else "רגיל לשימוש כזה")
    mnt = i0.get("root_mount", "")
    if mnt:
        noatime = "noatime" in mnt
        rows_.append(("/ מותקן", mnt))
        if not noatime:
            V(1, "גישה לקבצים נכתבת לכרטיס", "/ בלי noatime", "כל קריאת קובץ מעדכנת את זמן הגישה שלו בכרטיס")
    swaps = [l.split()[0] for l in i0.get("swaps", "").splitlines()[1:] if l.split()]
    on_card = [x for x in swaps if not x.startswith("/dev/zram")]
    if "swaps" in i0:
        rows_.append(("swap", ", ".join(swaps) or "אין"))
        if on_card:
            used = max([num(r.get("swap_used")) or 0 for r in rows(out / "system.csv")] + [0])
            V(1 if used > 50 else 0, "swap על הכרטיס", ", ".join(on_card) + f" · שיא בשימוש {used:.0f} MB",
              "כל דף שעובר ל-swap נכתב לכרטיס" if used > 50 else "קיים אבל כמעט לא בשימוש")
    if i0.get("journald"):
        rows_.append(("היומן", i0["journald"].replace("\n", " · ")))
    if i0.get("log2ram"):
        rows_.append(("log2ram", i0["log2ram"]))
    if i0.get("sd_life"):
        rows_.append(("שחיקה לפי הכרטיס (eMMC)", i0["sd_life"]))
    wr = rows(out / "writes.csv")
    who = ""
    if wr:
        agg = defaultdict(int)
        for r in wr:
            agg[r["comm"]] += int(num(r["kb"]) or 0)
        best = sorted(agg.items(), key=lambda x: -x[1])[:10]
        who = ("<table><tr><th>מי כותב</th><th>MB בבדיקה</th><th>MB לשעה</th></tr>" + "".join(
            f"<tr><td class=mono>{html.escape(k)}</td><td>{v / 1024:.1f}</td><td>{v / 1024 / hours:.1f}</td></tr>"
            for k, v in best) + "</table>")
    if not rows_ and not who:
        return ""
    return ("<h3 style='margin-top:18px'>שחיקת הכרטיס</h3><table>" + "".join(
        f"<tr><td>{k}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows_) + "</table>" + who +
        "<p class='note'>כרטיס SD לא מדווח כמה הוא שחוק. מודדים מה שוחק אותו: כמה נכתב, מי כתב, ומה בהגדרות "
        "מגדיל את זה. write_bytes של כל תוכנה הוא מה שהגיע לדיסק, אחרי מטמון הכתיבה.</p>")


def deep_section(out, i0, slow, span, phases, V):
    """סעיף "בדיקות עומק": threads, תוכנות כבדות, ברוקר, יומן, כרטיס, חשמל ומקשים."""
    parts = []
    thr = rows(out / "threads.csv")
    if thr:
        agg = defaultdict(list)
        for r in thr:
            agg[(r["proc"], r["name"])].append(num(r["cpu"]) or 0)
        n_min = max(1, len({r["t"] for r in thr}))
        best = sorted(((sum(v) / n_min, max(v), p, n) for (p, n), v in agg.items()), reverse=True)[:14]
        parts.append("<h3>מה עובד בתוך כל תוכנה (threads)</h3><table><tr><th>תוכנה</th><th>thread</th>"
                     "<th>מעבד ממוצע (% מליבה)</th><th>שיא</th></tr>" + "".join(
                         f"<tr><td>{'תוכנת המקשים' if p == 'device' else 'המסך'}</td><td class=mono>{html.escape(n)}</td>"
                         f"<td>{a:.1f}</td><td>{m:.1f}</td></tr>" for a, m, p, n in best) + "</table>"
                     "<p class='note'>נמדד כל דקה. שם ה-thread הוא שם הפונקציה שלו; SDLAudio הוא המיקסר של SDL.</p>")
    top = rows(out / "top.csv")
    if top:
        agg = defaultdict(list)
        for r in top:
            agg[r["comm"]].append(num(r["cpu"]) or 0)
        n_min = max(1, len({r["t"] for r in top}))
        best = sorted(((sum(v) / n_min, max(v), c, len(v)) for c, v in agg.items()), reverse=True)[:12]
        parts.append("<h3 style='margin-top:18px'>התוכנות הכבדות</h3><table><tr><th>תוכנה</th><th>מעבד ממוצע</th>"
                     "<th>שיא בדקה</th><th>דקות ב-10 הראשונות</th></tr>" + "".join(
                         f"<tr><td class=mono>{html.escape(c)}</td><td>{a:.1f}</td><td>{m:.1f}</td><td>{k}</td></tr>"
                         for a, m, c, k in best) + "</table>")
    mq = rows(out / "mqtt.csv")
    if mq:
        agg = defaultdict(list)
        for r in mq:
            agg[r["topic"]].append(num(r["per_min"]) or 0)
        n_min = max(1, len({r["t"] for r in mq}))
        best = sorted(((sum(v) / n_min, max(v), k) for k, v in agg.items()), reverse=True)
        tot = sum(a for a, _, _ in best)
        parts.append("<h3 style='margin-top:18px'>תנועה בברוקר (piano/#)</h3><table><tr><th>נושא</th>"
                     "<th>הודעות לדקה, ממוצע</th><th>שיא</th></tr>" + "".join(
                         f"<tr><td class=mono>{html.escape(k)}</td><td>{a:.0f}</td><td>{m:.0f}</td></tr>"
                         for a, m, k in best[:14]) + f"</table><p class='note'>סך הכל כ-{tot:.0f} הודעות לדקה.</p>")
        V(1 if tot > 3000 else 0, "תנועה בברוקר", f"{tot:.0f} הודעות לדקה",
          "הרבה הודעות קטנות עולות מעבד בכל מי שמאזין" if tot > 3000 else "קצב סביר")
    jv = rows(out / "journal_volume.csv")
    if jv:
        agg = defaultdict(int)
        for r in jv:
            agg[r["unit"]] += int(num(r["lines"]) or 0)
        hours = max(span / 3600, 0.05)
        best = sorted(agg.items(), key=lambda x: -x[1])[:10]
        parts.append("<h3 style='margin-top:18px'>כמה כותבים ליומן</h3><table><tr><th>מקור</th><th>שורות</th>"
                     "<th>לשעה</th></tr>" + "".join(f"<tr><td class=mono>{html.escape(k)}</td><td>{v}</td>"
                                                    f"<td>{v / hours:.0f}</td></tr>" for k, v in best) + "</table>")
        jr_ = json.loads(read(out / "journal.json", "{}") or "{}")
        if jr_.get("common"):
            parts.append("<h3 style='margin-top:18px'>מה ממלא את היומן</h3><table><tr><th>מקור</th><th>שורה (# במקום מספרים)"
                         "</th><th>פעמים</th></tr>" + "".join(
                             f"<tr><td class=mono>{html.escape(u)}</td><td class=mono><bdi>{html.escape(m)}</bdi></td>"
                             f"<td>{n}</td></tr>" for u, m, n in jr_["common"][:12]) + "</table>")
        if best and best[0][1] / hours > 1200:
            V(1, "יומן רועש", f"{best[0][0]}: {best[0][1] / hours:.0f} שורות לשעה",
              "כתיבה רבה ליומן שוחקת את הכרטיס ומקשה למצוא שגיאות אמיתיות")
    parts.append(wear_section(out, i0, slow, span, V))
    ps = jget(i0, "power_src")
    poe = [(num(r["t"]), num(r.get("poe_ma"))) for r in slow if num(r.get("poe_ma")) is not None]
    if ps:
        parts.append(f"<h3 style='margin-top:18px'>חשמל</h3><p>{html.escape(ps.get('he', ''))}</p>")
    if poe:
        parts.append(chart("זרם מה-PoE (mA)", [("זרם", "#eb6834", [t for t, _ in poe], [v for _, v in poe])], "mA",
                           span, phases, ymin=0))
        V(0, "זרם PoE", f"ממוצע {statistics.fmean(v for _, v in poe):.0f} mA, שיא {max(v for _, v in poe):.0f}", "")
    b = [int(num(r.get("bounces")) or 0) for r in slow if r.get("bounces") not in (None, "")]
    if b:
        V(1 if b[-1] else 0, "לחיצות כפולות שסוננו (קפיצת מגע)", f"{b[-1]}",
          "מקש שלחץ שוב פחות מ-40 ms אחרי שעזב: זה מגע שקפץ, לא יד. הן לא הגיעו לצליל" if b[-1]
          else "אף מקש לא קפץ")
    low = jget(i0, "gpio_low", [])
    if low:
        env = load_env()
        keys = [int(p) for p in re.findall(r"\d+", env.get("DEVICE_KEY_PINS", "15,10,17,9,18,27,25,22,11,23,8,24,13"))]
        hit = [f"GPIO{n} (מקש {keys.index(n) + 1})" for n in low if n in keys]
        one = len(low) == 1
        V(1 if hit else 0, "פין שנמצא במצב נמוך במנוחה" if one else "פינים שנמצאים במצב נמוך במנוחה",
          ", ".join(f"GPIO{n}" for n in low),
          (("הפין מוגדר עם pull-up ובכל זאת קורא 0, כלומר כובע או רכיב על הלוח מושך אותו למטה" if one else
            "הפינים מוגדרים עם pull-up ובכל זאת קוראים 0, כלומר כובע או רכיב על הלוח מושך אותם למטה")
           + " (כובע Argon, למשל, משתמש ב-GPIO4 לכפתור שלו). "
           + (("הוא גם פין של מקש בתוכנה: " if len(hit) == 1 else "הם גם פינים של מקשים בתוכנה: ") + ", ".join(hit)
              + (". מקש שיחובר אליו ייראה לחוץ כל הזמן, ולכן כשמחברים מקשים צריך להעביר את המקש הזה "
                 "לפין פנוי ב-DEVICE_KEY_PINS" if len(hit) == 1 else
                 ". מקשים שיחוברו אליהם ייראו לחוצים כל הזמן, ולכן כשמחברים מקשים צריך להעביר אותם "
                 "לפינים פנויים ב-DEVICE_KEY_PINS") if hit else
              "אין מקש בתוכנה על " + ("הפין הזה" if one else "הפינים האלה"))))
    rd = jget(i0, "regdom")
    if rd and (not rd.get("ok") or rd.get("he")):
        V(0 if rd.get("ok") else 1, "מדינת הרדיו", f"התבקש {rd.get('want')}, בליבה {rd.get('global')}", rd.get("he", ""))
    ext = jget(i0, "usb_ext", [])
    if "usb_ext" in i0:
        V(0, "התקני USB חיצוניים", str(len(ext)), "")
    if not parts:
        return ""
    return "<h2>בדיקות עומק</h2>" + "".join(parts)


LOGOS = Path(__file__).resolve().parent / "ui" / "data" / "logos"
_BIDI = re.compile(r"(?<![\w.:])(0x[0-9a-fA-F]+|-?\d+(?:[.,:]\d+)*(?:\s?(?:%|ms|MB|GB|dBm|°|mA|KB/s)(?![\w]))?)(?![\w])")


def logo_uri(name):
    """A logo of the device's own (device/ui/data/logos) inside the page, so the report is one file."""
    try:
        data = (LOGOS / name).read_bytes()
    except OSError:
        return ""
    kind = "svg+xml" if name.endswith(".svg") else "png"
    return f"data:image/{kind};base64," + __import__("base64").b64encode(data).decode()


def bidi(text):
    """Escaped, with each number and its unit kept left to right inside the Hebrew line."""
    return _BIDI.sub(lambda m: "<bdi>" + m.group(1) + "</bdi>", html.escape(text))


def report(out, partial=False):
    out = Path(out)
    info = json.loads(read(out / "info.json", "{}") or "{}")
    st = json.loads(read(out / "run.json", "{}") or "{}")
    jr = json.loads(read(out / "journal.json", "{}") or "{}")
    sysr = rows(out / "system.csv")
    procs = rows(out / "procs.csv")
    slow = rows(out / "slow.csv")
    lat = rows(out / "latency.csv")
    phases = st.get("phases", [])
    counts = st.get("counts", {})
    ts = [num(r["t"]) for r in sysr]
    span = max([t for t in ts if t] + [st.get("elapsed", 1)])

    def col(name, src=sysr):
        return [num(r.get(name)) for r in src]

    def avg(t, v, size=60):
        """ממוצע לכל דקה: 5 שניות על פני 4 שעות הן רעש ולא קו."""
        return bucket(t, v, size, statistics.fmean) if span > 1800 else (t, v)

    def line(name, color, t, v):
        x, y = avg(t, v)
        return (name, color, x, y)

    by = defaultdict(lambda: ([], defaultdict(list)))
    for r in procs:
        t, g = by[r["kind"]]
        t.append(num(r["t"]))
        for k in GROUPS:
            g[k].append(num(r.get(k)))

    # ---- הממצאים
    verdicts = []

    def V(level, name, value, why):
        verdicts.append((level, name, value, why))

    temp = [v for v in col("temp") if v is not None]
    if temp:
        tmax = max(temp)
        V(2 if tmax >= 80 else 1 if tmax >= 70 else 0, "טמפרטורת המעבד", f"שיא {tmax:.0f}°, ממוצע {statistics.fmean(temp):.0f}°",
          "מ-80° ה-Pi מוריד תדר בעצמו; מ-70° כדאי לחשוב על קירור במארז")
    thr = [int(r["throttled"], 16) for r in slow if r.get("throttled")]
    if thr:
        acc = 0
        for v in thr:
            acc |= v
        msg = []
        if acc & 0x50005:
            msg.append("מתח נמוך")
        if acc & 0xA000A:
            msg.append("הגבלת תדר או חום")
        V(2 if acc & 0x50005 else 1 if acc else 0, "מתח והגבלות (get_throttled)",
          hex(acc) + (" · " + ", ".join(msg) if msg else " · נקי"),
          "מתח נמוך פירושו שהספק (PoE או סוללה) לא מספיק בעומס, וזה גורם לקטיעות וקריסות")
    avail = [v for v in col("mem_avail") if v is not None]
    if avail:
        amin = min(avail)
        used = [v for v in col("mem_used") if v is not None]
        V(2 if amin < 150 else 1 if amin < 300 else 0, "זיכרון פנוי",
          f"מינימום {amin:.0f} MB · שיא בשימוש {max(used):.0f} MB מתוך {info.get('mem_total', '?')} MB",
          "MemAvailable: כמה זיכרון אפשר לתת בלי להחליף לדיסק. מתחת ל-150 MB מתחילות האטות")
    swap = [v for v in col("swap_used") if v is not None]
    if swap and max(swap) > 0:
        V(1 if max(swap) - swap[0] > 50 else 0, "שימוש ב-swap", f"מ-{swap[0]:.0f} עד {max(swap):.0f} MB",
          "swap שגדל לאורך הבדיקה אומר שהזיכרון לא הספיק בשלב מסוים")
    leaks = []
    rt, rg = by["rss"]
    for g in GROUPS:
        if g in ("other", "soak"):
            continue
        s = slope(rt, rg[g])
        vals = [v for v in rg[g] if v]
        # and still going up in the second half: a cache that fills (voices and songs loaded as they are first
        # used, then flat for hours) climbs in steps early and stops, a leak goes on
        h = len(rt) // 2
        s2 = slope(rt[h:], rg[g][h:]) if h >= 10 else s
        late = [v for v in rg[g][h:] if v]
        if (s is not None and vals and s > 5 and vals[-1] - vals[0] > 20
                and s2 is not None and s2 > 3 and late and late[-1] - late[0] > 10):
            leaks.append((GROUP_HE[g], s))
    if rt:
        V(2 if any(s > 20 for _, s in leaks) else 1 if leaks else 0, "דליפת זיכרון",
          ", ".join(f"{n} +{s:.0f} MB/שעה" for n, s in leaks) or "לא נמצאה עלייה קבועה באף רכיב",
          "עלייה שנמשכת גם בחצי השני של הבדיקה היא סימן לדליפה. מטמון שמתמלא עולה במדרגות בהתחלה ונעצר")
    lms = [num(r["ms"]) for r in lat]
    if lms:
        p50, p95, p99 = pct(lms, 50), pct(lms, 95), pct(lms, 99)
        lost = counts.get("lost", 0)
        sent = max(1, counts.get("notes", 1))
        V(2 if p95 > 120 or lost / sent > 0.02 else 1 if p95 > 60 or lost / sent > 0.005 else 0,
          "השהיית קליד (מגע עד הגשר)", f"חציון {p50:.0f} ms · 95% {p95:.0f} ms · 99% {p99:.0f} ms · אבדו {lost} מתוך {sent}",
          "הזמן מהלחיצה ועד שהגשר מדווח עליה. עלייה לאורך הזמן אומרת שמשהו נערם")
    http = [num(r["http_ms"]) for r in sysr if r.get("http_code") == "200"]
    bad_http = sum(1 for r in sysr if r.get("http_code") not in ("200", None, ""))
    if http:
        V(2 if pct(http, 95) > 500 or bad_http > 10 else 1 if pct(http, 95) > 150 or bad_http else 0,
          "תגובת מנוע השיעורים (/state)", f"95% {pct(http, 95):.0f} ms · שיא {max(http):.0f} ms · כשלונות {bad_http}",
          "המסך שואל את המנוע 10 פעמים בשנייה; תגובה איטית נראית כמסך שנתקע")
    xr = jr.get("xruns")
    if xr is None and slow:
        xs = [num(r.get("xruns")) for r in slow if r.get("xruns")]
        xr = max(xs) if xs else None
    V(-1 if xr is None else 2 if xr > 20 else 1 if xr else 0, "קטיעות שמע (pw-top ERR)",
      "לא נמדד" if xr is None else str(int(xr)), "חוצצים שהגיעו מאוחר לכרטיס הקול: נשמעים כקליק או חור בצליל")
    restarts = sum(int(num(r.get("restarts")) or 0) for r in slow)
    screen_re = sum(1 for _, e in st.get("events", []) if "screen.py" in e)
    V(2 if restarts or screen_re else 0, "הפעלות מחדש", f"שירותים {restarts} · תוכנת המסך {screen_re}",
      "כל עלייה מחדש באמצע שימוש היא קריסה, אלא אם הפעלת אותה בעצמך")
    jw = sum(int(num(r.get("journal_warn")) or 0) for r in slow)
    jc = jr.get("counts", {})
    V(1 if jw else 0, "אזהרות ושגיאות ביומן", f"{jw} שורות" + (" · " + ", ".join(f"{k} {v}" for k, v in
      sorted(jc.items(), key=lambda x: -x[1])[:5]) if jc else ""), "פירוט מלא בסוף הדוח")
    fks = [v for v in col("forks") if v is not None]
    if fks:
        V(1 if pct(fks, 95) > 30 else 0, "תהליכים חדשים בשנייה", f"ממוצע {statistics.fmean(fks):.0f} · 95% {pct(fks, 95):.0f} · שיא {max(fks):.0f}",
          "פקודות שנפתחות ונסגרות כל הזמן. הרבה מהן בזמן נגינה אומר שכל לחיצה מפעילה תוכנית חיצונית")
    cpu = [v for v in col("cpu") if v is not None]
    if cpu:
        V(1 if pct(cpu, 95) > 85 else 0, "עומס מעבד", f"ממוצע {statistics.fmean(cpu):.0f}% · 95% {pct(cpu, 95):.0f}% · שיא {max(cpu):.0f}%",
          "אחוז מכל 4 הליבות יחד")
    free = [v for v in col("disk_free_mb") if v is not None]
    if free:
        V(2 if min(free) < 500 else 1 if free[0] - min(free) > 300 else 0, "מקום פנוי בכרטיס",
          f"מ-{free[0]:.0f} עד {min(free):.0f} MB", "לוגים או קבצים שגדלים בלי סוף ימלאו את הכרטיס בשימוש ארוך")

    # ---- רשת, HA וטלגרם
    netr = rows(out / "net.csv")
    remote = rows(out / "remote.csv")
    hak = rows(out / "ha_keys.csv")
    nt = [num(r["t"]) for r in netr]
    ncol = lambda k: [num(r.get(k)) for r in netr]
    mq = [v for v in ncol("mqtt_ms") if v is not None]
    if mq:
        ok_mq = [v for v in mq if v >= 0]
        lost_mq = len(mq) - len(ok_mq)
        p95 = pct(ok_mq, 95) or 0
        V(2 if p95 > 200 or lost_mq > len(mq) * 0.02 else 1 if p95 > 50 or lost_mq else 0, "הלוך-חזור דרך הברוקר",
          f"חציון {pct(ok_mq, 50) or 0:.0f} ms · 95% {p95:.0f} ms · לא חזרו {lost_mq} מתוך {len(mq)} · הברוקר {info.get('broker', '')}",
          "הודעה שהבדיקה שולחת לעצמה כל 5 שניות. כך עוברת כל לחיצה, פקודה ותשובה")
    for key, name in (("broker", "ping לברוקר"), ("gw", "ping לנתב")):
        pv = [v for v in ncol("ping_" + key) if v is not None]
        lv = [v for v in ncol("loss_" + key) if v is not None]
        if lv:
            loss = statistics.fmean(lv)
            V(2 if loss > 5 else 1 if loss > 0.5 or (pv and pct(pv, 95) > 50) else 0, name,
              f"ממוצע {statistics.fmean(pv) if pv else 0:.1f} ms · 95% {pct(pv, 95) or 0:.0f} ms · אובדן {loss:.1f}%",
              "אובדן חבילות או קפיצות בזמן אומרים שהרשת היא צוואר הבקבוק, לא ה-Pi")
    wd = [v for v in ncol("wifi_dbm") if v is not None]
    if wd:
        V(2 if min(wd) < -80 else 1 if min(wd) < -70 else 0, "עוצמת Wi-Fi",
          f"ממוצע {statistics.fmean(wd):.0f} dBm · החלש ביותר {min(wd):.0f} dBm",
          "מ-70- dBm ומטה החיבור מתחיל לאבד חבילות; הבלוטוס חולק איתו את אותו רדיו")
    drops = counts.get("mqtt_drops", 0)
    if netr:
        V(2 if drops > 2 else 1 if drops else 0, "ניתוקים מהברוקר", str(drops), "כל ניתוק עוצר לרגע לחיצות, פקודות ותשובות")
    tgc = [r.get("tg_code") for r in netr if r.get("tg_code")]
    if tgc:
        bad = sum(1 for c in tgc if c != "200")
        V(1 if bad else 0, "הגעה ל-Telegram (getMe)", f"{len(tgc) - bad} מתוך {len(tgc)} הצליחו",
          "בלי הגעה ל-api.telegram.org אין פקודות ותשובות בטלגרם")
    he = [v for v in ncol("ha_echo_ms") if v is not None]
    if he:
        ok_he = [v for v in he if v >= 0]
        V(2 if len(ok_he) < len(he) * 0.95 else 1 if (pct(ok_he, 95) or 0) > 1000 else 0, "HA שומע ועונה (אוטומציה זמנית)",
          f"{len(ok_he)} מתוך {len(he)} חזרו · חציון {pct(ok_he, 50) or 0:.0f} ms · 95% {pct(ok_he, 95) or 0:.0f} ms",
          "הודעה לברוקר, אוטומציה ב-HA שמגיבה, ותשובה חזרה")
    hk = [num(r["ms"]) for r in hak]
    if info.get("ha") and lms:
        # מול כל מה שהגשר דיווח ולא מול לחיצות הבדיקה בלבד: הדמות במסך המנוחה מנגנת לפעמים כמה צלילים
        # באותה דרך, ו-HA רואה גם אותם (בדוח של 10.11 יצא "484 מתוך 481")
        sent = int(counts.get("bridge_notes") or 0) or len(lms)
        share = min(1.0, len(hk) / max(1, sent))
        V(2 if share < 0.9 else 1 if share < 0.99 else 0, "HA רואה את הלחיצות",
          f"{len(hk)} מתוך {sent} ({share * 100:.0f}%) · 95% {pct(hk, 95) or 0:.0f} ms",
          "כל לחיצה שהגשר מדווח, דרך אוטומציה ב-HA. לחיצות שחסרות ב-HA לא יפעילו אוטומציות")
    steps = defaultdict(list)
    for r in remote:
        steps[(r["kind"], r["step"])].append(r)
    for kind, name in (("ha", "פקודות מ-HA לפסנתר"), ("telegram", "סבבי טלגרם"), ("alarm", "השעון המעורר מתחיל ונעצר")):
        rs = [r for r in remote if r["kind"] == kind]
        if rs:
            bad = sum(1 for r in rs if r["ok"] != "1")
            rounds = {"ha": counts.get("ha_rounds", 0), "telegram": counts.get("tg_rounds", 0),
                      "alarm": counts.get("alarms", 0)}[kind]
            # a stuck alarm is the one failure that cannot wait
            stuck = kind == "alarm" and any(r["step"] == "השעון נעצר" and r["ok"] != "1" for r in rs)
            V(2 if stuck or bad > len(rs) * 0.1 else 1 if bad else 0, name,
              f"{len(rs) - bad} מתוך {len(rs)} שלבים הצליחו · סבבים {rounds}",
              "שעון שלא נעצר הוא הבעיה הכי חמורה" if stuck else "פירוט לפי שלב בחלק של HA וטלגרם")
    ls = [r for r in remote if r["kind"] == "lesson"]
    if ls:
        secs = [num(r["ms"]) / 1000 for r in ls if num(r["ms"]) is not None]
        bad = sum(1 for r in ls if r["ok"] != "1")
        worst_s = max(secs) if secs else 0
        V(2 if bad else 1 if worst_s > 10 else 0, "זמן עד שהשיעור מתחיל",
          f"שיא {worst_s:.0f} שניות · {len(ls) - bad} מתוך {len(ls)} התחילו",
          "שיר ארוך (פרק של סימפוניה) לוקח למנוע זמן להכין. כל השיעורים: " +
          ", ".join(f"{r['detail']} {num(r['ms']) / 1000:.0f} ש'" for r in ls[:6] if num(r["ms"]) is not None))
    if st.get("abort") or st.get("interrupted"):
        verdicts.insert(0, (2, "הבדיקה נעצרה" if not st.get("interrupted") else "הבדיקה נקטעה", st.get("abort") or "",
                            "הבדיקה עצרה הכל כדי לא לסכן את ה-Pi, והחזירה את המכשיר למצב רגיל" if not st.get("interrupted")
                            else "ה-Pi עלה מחדש או כבה באמצע. הנתונים עד אותו רגע נשמרו"))

    icon = {0: ("✓", "תקין", "ok"), 1: ("!", "לבדוק", "warn"), 2: ("✕", "בעיה", "bad"), -1: ("–", "לא נמדד", "na")}
    worst = max([v[0] for v in verdicts] + [0])


    # ---- טבלת רכיבים
    ct, cg = by["cpu"]
    pt, pg = by["pss"]
    ft, fg = by["fds"]
    tt, tg = by["threads"]
    grows = []
    for g in GROUPS:
        r_ = [v for v in rg.get(g, []) if v is not None]
        c_ = [v for v in cg.get(g, []) if v is not None]
        if not r_ or max(r_) < 1:
            continue
        p_ = [v for v in pg.get(g, []) if v]
        f_ = [v for v in fg.get(g, []) if v is not None]
        th = [v for v in tg.get(g, []) if v is not None]
        s = slope(rt, rg[g])
        grows.append(f"<tr><td>{GROUP_HE[g]}</td><td>{statistics.fmean(c_):.1f}%</td><td>{pct(c_, 95):.0f}%</td>"
                     f"<td>{r_[0]:.0f}</td><td>{r_[-1]:.0f}</td><td>{max(r_):.0f}</td>"
                     f"<td>{'' if not p_ else f'{max(p_):.0f}'}</td>"
                     f"<td>{'' if s is None else f'{s:+.1f}'}</td>"
                     f"<td>{'' if not f_ else f'{f_[0]:.0f} ← {f_[-1]:.0f}'}</td>"
                     f"<td>{'' if not th else f'{th[0]:.0f} ← {th[-1]:.0f}'}</td></tr>")

    # ---- גרפים
    lt = [num(r["t"]) for r in lat]
    bx, b50 = bucket(lt, lms, 300, lambda v: statistics.median(v))
    _, b95 = bucket(lt, lms, 300, lambda v: pct(v, 95))
    hx, h95 = bucket(ts, [num(r["http_ms"]) if r.get("http_code") == "200" else None for r in sysr], 300,
                     lambda v: pct(v, 95))
    charts = [
        chart("מעבד (%)", [line("כל הליבות", "#2a78d6", ts, col("cpu")), line("הליבה העמוסה", "#eb6834", ts, col("core_max"))],
              "%", span, phases, ymin=0, ymax=100),
        chart("טמפרטורה (°C)", [line("טמפרטורה", "#e34948", ts, col("temp"))], "°", span, phases, rule=80,
              note="הקו האופקי: 80°, הנקודה שבה ה-Pi מתחיל להוריד תדר"),
        chart("תדר המעבד (MHz)", [line("תדר", "#6250d6", ts, col("freq"))], "MHz", span, phases, ymin=0),
        chart("זיכרון (MB)", [line("בשימוש", "#2a78d6", ts, col("mem_used")), line("פנוי", "#1baf7a", ts, col("mem_avail")),
                              line("swap", "#eda100", ts, col("swap_used"))], "MB", span, phases, ymin=0),
        chart("לחץ משאבים, PSI (%)" + ("" if any(v is not None for v in col("psi_cpu")) else
              " · הקרנל של ה-Pi לא מודד את זה (psi=1 לא מופעל)"), [line("מעבד", "#2a78d6", ts, col("psi_cpu")), line("זיכרון", "#eb6834", ts, col("psi_mem")),
                                      line("דיסק", "#1baf7a", ts, col("psi_io"))], "%", span, phases, ymin=0,
              note="אחוז הזמן שתהליכים חיכו למשאב. מעבד מעל 20 או זיכרון מעל 5 לאורך זמן: צוואר בקבוק"),
        chart("השהיית קליד (ms, לכל 5 דקות)", [("חציון", "#2a78d6", bx, b50), ("95%", "#eb6834", bx, b95)], "ms",
              span, phases, ymin=0),
        chart("תגובת מנוע השיעורים (ms, 95% לכל 5 דקות)", [("95%", "#1baf7a", hx, h95)], "ms", span, phases, ymin=0),
        chart("תהליכים חדשים בשנייה", [line("תהליכים", "#eb6834", ts, col("forks"))], "/s", span, phases, ymin=0,
              note="כל פקודה שרצה (mosquitto_pub, amidi, vcgencmd) היא תהליך חדש. הרבה תהליכים קצרים עולים מעבד גם כשכל אחד קל"),
        chart("כתיבה לכרטיס (KB/s)", [line("כתיבה", "#6250d6", ts, col("disk_w_kbs"))], "KB/s", span, phases, ymin=0),
    ]
    minis = []
    for g in GROUPS:
        vals = [v for v in rg.get(g, []) if v is not None]
        if not vals or max(vals) < 5:
            continue
        minis.append(chart(f"{GROUP_HE[g]}: זיכרון (MB)", [line("RSS", "#2a78d6", rt, rg[g])], "MB", span, phases,
                           height=140, ymin=0))
        minis.append(chart(f"{GROUP_HE[g]}: מעבד (% מליבה)", [line("מעבד", "#eb6834", ct, cg[g])], "%", span, phases,
                           height=140, ymin=0))

    # ---- לפי סיטואציה
    ph = defaultdict(float)
    for a, b, k in phases:
        ph[k] += b - a
    starts = [a for a, b, k in phases]

    def phase_at(t):
        if t is None:
            return None
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0 and phases[i][0] <= t < phases[i][1]:
            return phases[i][2]
        return None

    sit = defaultdict(lambda: defaultdict(list))
    for r in sysr:
        k = phase_at(num(r["t"]))
        if k:
            for f in ("cpu", "core_max", "mem_used", "temp", "psi_cpu", "forks"):
                v = num(r.get(f))
                if v is not None:
                    sit[k][f].append(v)
    for r in lat:
        k = phase_at(num(r["t"]))
        if k:
            sit[k]["lat"].append(num(r["ms"]))
    for r in netr:
        k = phase_at(num(r["t"]))
        v = num(r.get("mqtt_ms"))
        if k and v is not None and v >= 0:
            sit[k]["mqtt"].append(v)
    gsit = defaultdict(lambda: defaultdict(list))
    for kind in ("cpu", "rss"):
        tt_, gg_ = by[kind]
        for i, t in enumerate(tt_):
            k = phase_at(t)
            if k:
                for g in GROUPS:
                    v = gg_[g][i] if i < len(gg_[g]) else None
                    if v is not None:
                        gsit[(k, kind)][g].append(v)
    f1 = lambda v: "" if not v else f"{statistics.fmean(v):.0f}"
    fx = lambda v: "" if not v else f"{max(v):.0f}"
    order = [k for k in PHASE_HE if k in sit]
    p95s = lambda v: "" if not v else f"{pct(v, 95):.0f}"
    short = lambda g: GROUP_HE[g].split(" (")[0]
    srows = []
    for k in order:
        d = sit[k]
        top = sorted(((statistics.fmean(v), g) for g, v in gsit[(k, "cpu")].items() if v and g not in ("other", "soak")),
                     reverse=True)[:3]
        srows.append(f"<tr><td><i class='dot' style='background:{PHASE_COLOR[k]}'></i>{PHASE_HE[k]}</td>"
                     f"<td>{ph[k] / 60:.0f} דק'</td><td>{f1(d['cpu'])}%</td><td>{fx(d['cpu'])}%</td><td>{fx(d['core_max'])}%</td>"
                     f"<td>{f1(d['mem_used'])}</td><td>{fx(d['mem_used'])}</td><td>{fx(d['temp'])}°</td>"
                     f"<td>{p95s(d['lat'])}</td><td>{p95s(d['mqtt'])}</td><td>{f1(d['forks'])}</td>"
                     f"<td>{' · '.join(short(g) + f' {v:.0f}%' for v, g in top)}</td></tr>")
    mrows = []
    for g in GROUPS:
        if g in ("soak",) or not any(gsit[(k, "rss")].get(g) for k in order):
            continue
        cells = "".join(f"<td>{f1(gsit[(k, 'cpu')].get(g))}% · {f1(gsit[(k, 'rss')].get(g))}</td>" for k in order)
        mrows.append(f"<tr><td>{GROUP_HE[g]}</td>{cells}</tr>")
    sit_html = "" if not srows else (
        "<h2>לפי סיטואציה</h2><table><tr><th>מה רץ</th><th>זמן</th><th>מעבד ממוצע</th><th>מעבד שיא</th>"
        "<th>ליבה עמוסה שיא</th><th>RAM ממוצע MB</th><th>RAM שיא MB</th><th>טמפ' שיא</th><th>קליד 95% ms</th>"
        "<th>ברוקר 95% ms</th><th>תהליכים/ש'</th><th>הכי עמוסים</th></tr>" + "".join(srows) + "</table>"
        "<h3 style='margin-top:18px'>כל רכיב בכל סיטואציה (מעבד ממוצע · RAM ממוצע ב-MB)</h3><div class='wide'><table><tr><th>רכיב</th>"
        + "".join(f"<th>{PHASE_HE[k]}</th>" for k in order) + "</tr>" + "".join(mrows) + "</table></div>")

    # ---- רשת
    mx, m95 = bucket(nt, [v if v is not None and v >= 0 else None for v in ncol("mqtt_ms")], 300, lambda v: pct(v, 95))
    ex_, e95 = bucket(nt, [v if v is not None and v >= 0 else None for v in ncol("ha_echo_ms")], 300, lambda v: pct(v, 95))
    pbx, pb = bucket(nt, ncol("ping_broker"), 300, statistics.fmean)
    pgx, pg_ = bucket(nt, ncol("ping_gw"), 300, statistics.fmean)
    net_charts = [] if not netr else [
        chart("הלוך-חזור דרך הברוקר (ms, 95% לכל 5 דקות)", [("ברוקר", "#2a78d6", mx, m95)] +
              ([("דרך HA", "#008300", ex_, e95)] if any(v is not None for v in e95) else []), "ms", span, phases, ymin=0),
        chart("ping (ms, ממוצע לכל 5 דקות)", ([("לברוקר", "#2a78d6", pbx, pb)] if any(pb) else []) +
              [("לנתב", "#eb6834", pgx, pg_)], "ms", span, phases, ymin=0),
        chart("עוצמת Wi-Fi (dBm)", [line("Wi-Fi", "#6250d6", nt, ncol("wifi_dbm"))], "dBm", span, phases),
        chart("תעבורת רשת (KB/s)", [line("נכנס", "#2a78d6", nt, ncol("rx_kbs")), line("יוצא", "#eb6834", nt, ncol("tx_kbs"))],
              "KB/s", span, phases, ymin=0),
    ]
    rrows = []
    for (kind, step), rs in steps.items():
        ms_ = [num(r["ms"]) for r in rs if r["ok"] == "1" and num(r["ms"]) is not None]
        okn = sum(1 for r in rs if r["ok"] == "1")
        rrows.append(f"<tr><td>{'HA' if kind == 'ha' else 'טלגרם'}</td><td>{html.escape(step)}</td><td>{okn} מתוך {len(rs)}</td>"
                     f"<td>{'' if not ms_ else f'{statistics.median(ms_):.0f}'}</td><td>{'' if not ms_ else f'{max(ms_):.0f}'}</td></tr>")
    remote_html = "" if not rrows else (
        "<h2>Home Assistant וטלגרם</h2><table><tr><th>דרך</th><th>שלב</th><th>הצליח</th><th>חציון ms</th><th>שיא ms</th></tr>"
        + "".join(rrows) + "</table><p class='note'>הזמן נמדד מתחילת הפקודה. פקודות טלגרם נשלחות ל-HA כאותו אירוע "
        "שהודעה אמיתית יוצרת, והתשובות מגיעות אליך בטלגרם. האוטומציות הזמניות נמחקות בסוף.</p>"
        + (f"<details><summary>מה הגשר ומנוע השיעורים כתבו ביומן כששלב לא הצליח</summary><pre class='mono'>"
           f"{html.escape(read(out / 'remote_fail.txt'))}</pre></details>" if (out / "remote_fail.txt").exists() else ""))
    net_html = "" if not netr else (
        f"<h2>רשת</h2><p class='muted'>הברוקר {html.escape(str(info.get('broker', '')))} · Home Assistant "
        f"{'נבדק' if info.get('ha') else 'לא נבדק'} · טלגרם {'נבדק' if info.get('telegram') else 'לא נבדק'}</p>"
        + "".join(net_charts))

    # ---- זמן עלייה
    boot_html = ""
    bb = json.loads(read(out / "boot_before.json", "{}") or "{}")
    ba = json.loads(read(out / "boot_after.json", "{}") or "{}")
    if bb or ba:
        def bcell(b, u):
            v = (b.get("units") or {}).get(u)
            return "" if v is None else f"{v:.1f}"
        names = list(dict.fromkeys(list((bb.get("units") or {})) + list((ba.get("units") or {}))))
        brow = "".join(f"<tr><td>{html.escape(u)}</td><td>{bcell(bb, u)}</td><td>{bcell(ba, u)}</td></tr>" for u in names)
        rb, ra = bb.get("ready_s"), ba.get("ready_s")
        if rb is not None:
            V(2 if rb > 120 else 1 if rb > 60 else 0, "זמן עלייה עד שהמכשיר מוכן",
              f"לפני הבדיקה {rb:.0f} שניות" + (f" · אחרי {ra:.0f} שניות" if ra else ""),
              "מהדלקה עד שהשירותים פעילים, המכשיר בברוקר ומנוע השיעורים עונה")
        boot_html = ("<h2>זמן עלייה</h2><table><tr><th>שלב (שניות מהדלקה)</th><th>לפני הבדיקה</th><th>אחרי הבדיקה</th></tr>"
                     f"<tr><td><b>הכל מוכן</b></td><td>{'' if rb is None else f'{rb:.1f}'}</td><td>{'' if ra is None else f'{ra:.1f}'}</td></tr>"
                     + brow + "</table>"
                     + "".join(f"<details><summary>{t}</summary><pre class='mono'>{html.escape(b.get(k, ''))}</pre></details>"
                               for t, b in (("לפני הבדיקה", bb), ("אחרי הבדיקה", ba)) if b
                               for k in ("analyze", "blame", "chain"))
                     + "<p class='note'>אם אחרי הבדיקה לא מופיע ערך, ה-ZIP נארז לפני הריבוט שבסוף. הדוח שנשאר ב-Pi מתעדכן אחריו.</p>")

    # ---- מצב החומרה והמערכת
    i0 = json.loads(read(out / "inventory_start.json", "{}") or "{}")
    i1 = json.loads(read(out / "inventory_end.json", "{}") or "{}")
    inv_html = ""
    if i0:
        def nets(i):
            try:
                return json.loads(i.get("net_json") or "{}")
            except ValueError:
                return {}
        n0, n1 = nets(i0), nets(i1)
        nrows = []
        for dev, a0 in n0.items():
            a1 = n1.get(dev, {})
            diffs = []
            for k in ("rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "carrier_changes"):
                try:
                    dlt = int(a1.get(k, a0.get(k)) or 0) - int(a0.get(k) or 0)
                except ValueError:
                    dlt = 0
                if dlt:
                    diffs.append(f"{k} +{dlt}")
            speed = a0.get("speed", "")
            speed = "" if not speed or speed.startswith("-") else f"{speed} Mb/s {a0.get('duplex', '')}"
            nrows.append(f"<tr><td>{html.escape(dev)}</td><td>{html.escape(a0.get('state', ''))}"
                         f"{' ← ' + html.escape(a1.get('state', '')) if a1 and a1.get('state') != a0.get('state') else ''}</td>"
                         f"<td>{html.escape(speed)}</td><td>{html.escape(', '.join(diffs) or 'בלי שגיאות חדשות')}</td></tr>")
            if any("errors" in x or "carrier" in x for x in diffs):
                V(1, f"הרשת הפיזית ({dev})", ", ".join(diffs), "שגיאות או נפילות קישור שנוספו בזמן הבדיקה")
        kw0 = len([x for x in i0.get("kernel_warn", "").splitlines() if x.strip()])
        kw1 = len([x for x in i1.get("kernel_warn", "").splitlines() if x.strip()]) if i1 else kw0
        new_k = "\n".join(i1.get("kernel_warn", "").splitlines()[kw0:]) if i1 else ""
        if i1:
            # שתי הודעות שמופיעות בכל עלייה של Raspberry Pi OS ואין בהן מה לטפל: ספק מתח שלא כתוב בעץ ההתקנים
            # ("using dummy regulator") ודרייבר של Broadcom מתיקיית staging
            known = ("using dummy regulator", "module is from the staging directory")
            boot = [x for x in i0.get("kernel_warn", "").splitlines() if x.strip()]
            odd = [x for x in boot if not any(k in x for k in known)]
            try:
                ago = max(0, round(float(str(i0.get("uptime", "0")).split()[0]) / 60))
            except (ValueError, IndexError):
                ago = 0
            when = f"{ago} דק' לפני הבדיקה" if ago else "לפני הבדיקה"
            V(1 if kw1 > kw0 or odd else 0, "אזהרות קרנל",
              f"בזמן הבדיקה {kw1 - kw0} · בעליית ה-Pi ({when}) {kw0}" + (f", מתוכן {len(odd)} לא מוכרות" if odd else ""),
              ("אזהרות מהעלייה שאינן מהסוג המוכר. הן מפורטות בסוף הדוח, בפלט המלא (kernel_warn)" if odd else
               "dmesg ברמת warning ומעלה. אלה מהעלייה מוכרות: ספקי מתח שלא כתובים בעץ ההתקנים (dummy regulator) "
               "ודרייברים של Broadcom מתיקיית staging. הן מופיעות בכל Raspberry Pi OS, ואין בהן מה לטפל"))
            fl = [x for x in i1.get("failed", "").splitlines() if x.strip()]
            V(2 if fl else 0, "שירותים שנכשלו (systemctl --failed)", ", ".join(x.split()[0] for x in fl) or "אין", "")
            u0, u1 = set(i0.get("usb", "").splitlines()), set(i1.get("usb", "").splitlines())
            if u0 != u1:
                V(1, "שינוי בהתקני USB", f"נוספו {len(u1 - u0)}, נעלמו {len(u0 - u1)}", "התקן שנעלם באמצע יכול להיות ניתוק חשמלי")
        gp = i0.get("gpio") or i0.get("gpio_old") or i0.get("gpioinfo") or ""
        funcs = defaultdict(int)
        for line in gp.splitlines():
            m = re.search(r"\b(ip|op|a\d|no)\b", line) or re.search(r"func=(\w+)", line)
            if m:
                funcs[m.group(1)] += 1
        gp_sum = " · ".join(f"{ {'ip': 'כניסה', 'op': 'יציאה', 'no': 'כבוי'}.get(k, k)} {v}" for k, v in sorted(funcs.items()))
        facts = [("מערכת", i0.get("os", "")), ("קרנל", i0.get("uname", "")), ("קושחה", i0.get("firmware", "").replace("\n", " · ")),
                 ("טמפרטורה", f"{i0.get('temp', '')} ← {i1.get('temp', '')}" if i1 and i0.get("temp") else i0.get("temp", "")),
                 ("מתחים", i0.get("volts", "").replace("\n", " · ")), ("שעונים", i0.get("clocks", "").replace("\n", " · ")),
                 ("חלוקת זיכרון", i0.get("mem_split", "").replace("\n", " · ")),
                 ("get_throttled", f"{i0.get('throttled', '')} ← {i1.get('throttled', '')}" if i1 and i0.get("throttled") else i0.get("throttled", "")),
                 ("GPIO", (gp_sum or "לא נקרא (אין pinctrl או raspi-gpio)") + gpio_note(i0)),
                 ("USB", usb_text(i0)), ("כרטיס SD", i0.get("sd", "")),
                 ("מקור חשמל", jget(i0, "power_src").get("he", "")),
                 ("Wi-Fi", " · ".join(x.strip() for x in i0.get("wifi_link", "").splitlines()
                                      if re.match(r"\s*(SSID|freq|signal|tx bitrate)", x)) or "לא מחובר"),
                 ("מדינת רדיו", regdom_text(i0)),
                 ("בלוטוס", " · ".join(x.strip() for x in i0.get("bluetooth", "").splitlines()
                                       if re.match(r"\s*(Powered|Discoverable|Pairable)", x)) or "לא נקרא"),
                 ("חסימות רדיו", " · ".join(x.strip() for x in i0.get("rfkill", "").splitlines() if "blocked: yes" in x) or "אין"),
                 ("מסכים", i0.get("drm", "").replace("\n", " · ")), ("תאורת מסך", i0.get("backlight", "")),
                 ("I2C ו-SPI", i0.get("i2c", "").replace("\n", " ") or "אין"), ("ספקי כוח", i0.get("power", "").replace("\n", " · "))]
        frows = "".join(f"<tr><td>{k}</td><td{'' if re.search('[א-ת]', str(v)) else ' class=mono'}>{html.escape(str(v))}</td></tr>"
                        for k, v in facts if v)
        raws = "".join(f"<details><summary>{html.escape(k)}</summary><pre class='mono'>{html.escape(str(v))}</pre></details>"
                       for k, v in i0.items() if v and k not in ("net_json", "t"))
        inv_html = ("<h2>מצב החומרה והמערכת</h2><p class='note'>מה שהליבה ומערכת ההפעלה מדווחות, בהתחלה ובסוף "
                    "(← בסוף). רק קריאה. GPIO לא נבדק כאן חשמלית; יש לזה בדיקה בהגדרות.</p>"
                    f"<table>{frows}</table><h3 style='margin-top:18px'>רשת פיזית</h3><table><tr><th>ממשק</th><th>מצב</th>"
                    f"<th>מהירות</th><th>במהלך הבדיקה</th></tr>{''.join(nrows)}</table>"
                    + (f"<h3 style='margin-top:18px'>אזהרות קרנל שנוספו</h3><pre class='mono'>{html.escape(new_k)}</pre>" if new_k else "")
                    + f"<h3 style='margin-top:18px'>הפלט המלא בהתחלה</h3>{raws}")

    # ---- בדיקות עומק: מה עובד בתוך כל תוכנה, הברוקר, היומן, הכרטיס, החשמל והמקשים
    deep_html = deep_section(out, i0, slow, span, phases, V)

    # ---- סטטיסטיקה
    stats_rows = [("דגימות מערכת (כל 5 שניות)", len(sysr)), ("דגימות רשת", len(netr)),
                  ("לחיצות קלידים", counts.get("notes", 0)), ("לחיצות שהגשר דיווח", len(lat)),
                  ("לחיצות שאבדו", counts.get("lost", 0)), ("לחיצות ש-HA ראה", len(hak)),
                  ("כפתורים", counts.get("buttons", 0)), ("שיעורים", counts.get("lessons", 0)),
                  ("שיעורים שנעצרו כי חיכו", counts.get("lesson_stalls", 0)), ("טעויות מכוונות בשיעור (קליד שכן ותיקון)", counts.get("lesson_slips", 0)),
                  ("החלקות בנגינה (קליד שכן ותיקון)", counts.get("play_slips", 0)), ("שירים", counts.get("songs", 0)),
                  ("שעונים מעוררים", counts.get("alarms", 0)), ("הקלטות מסך", counts.get("recordings", 0)),
                  ("סבבי HA", counts.get("ha_rounds", 0)), ("סבבי טלגרם (3 הודעות כל אחד)", counts.get("tg_rounds", 0)),
                  ("שלבי HA וטלגרם שהצליחו", f"{sum(1 for r in remote if r['ok'] == '1')} מתוך {len(remote)}"),
                  ("הודעות לברוקר שלא חזרו", sum(1 for v in ncol("mqtt_ms") if v is not None and v < 0)),
                  ("ניתוקים מהברוקר", counts.get("mqtt_drops", 0)),
                  ("שלבים שבסופם שיר או שעון עוד התנגנו", counts.get("left_playing", 0)),
                  ("שורות חשודות ביומן", sum(int(num(r.get("journal_warn")) or 0) for r in slow)),
                  ("קטיעות שמע", jr.get("xruns", "") if jr.get("xruns") is not None else "לא נמדד"),
                  ("underrun בזרם הקלידים בזמן סרטון (מוכר, לא נשמע)", jr.get("alsa_underruns", "לא נמדד"))]
    stats_html = "<h2>סטטיסטיקה</h2><table>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in stats_rows) + "</table>"

    legend = "".join(f'<span><i style="background:{c}"></i>{PHASE_HE[k]}</span>' for k, c in PHASE_COLOR.items())
    # כל שלב מבודד ולא נשבר באמצע, והמספר מבודד בתוכו: בלי זה "Home Assistant" והמספר שאחריו התחברו לרצף
    # אחד משמאל לימין, והשורה נשברה באמצע השם
    phase_txt = " · ".join(f"<bdi dir='rtl' style='white-space:nowrap'>{html.escape(PHASE_HE[k])} "
                           f"<bdi>{v / 60:.0f}</bdi> דק'</bdi>" for k, v in sorted(ph.items(), key=lambda x: -x[1]))
    ev = "".join(f"<tr><td>{t / 3600:.2f} ש'</td><td>{html.escape(e)}</td></tr>" for t, e in st.get("events", []))
    times = jr.get("times") or []
    ex = "".join(f"<tr><td>{t / 3600:.2f} ש'</td><td>{html.escape(s)}</td><td class='mono'>{html.escape(m)}</td>"
                 f"<td>{'×' + str(times[i]) if i < len(times) and times[i] > 1 else ''}</td></tr>"
                 for i, (t, s, m) in enumerate(jr.get("examples", [])[:120]))
    worst = max([v[0] for v in verdicts] + [0])
    try:
        (out / "verdicts.json").write_text(json.dumps(verdicts, ensure_ascii=False))
    except OSError:
        pass
    vt = "".join(f'<tr class="{icon[l][2]}"><td><b class="badge">{icon[l][0]} {icon[l][1]}</b></td><td>{n}</td>'
                 f'<td>{html.escape(str(v))}</td><td class="muted">{w}</td></tr>' for l, n, v, w in verdicts)
    head = {0: "המכשיר עבר את הבדיקה", 1: "עבר, עם כמה דברים לבדוק", 2: "נמצאו בעיות"}[worst]
    if st.get("interrupted"):
        head = "הבדיקה נקטעה באמצע"
    elif st.get("abort"):
        head = "הבדיקה נעצרה: " + st["abort"]
    title = "דוח ביניים" if partial else head

    # ---- the page: what matters on top (the result, what needs care, 3 graphs, what is fine), the rest folded
    def fold(section, open_=False):
        """A section of its own (it starts with its <h2>) folded under its title."""
        m = re.match(r"\s*<h2>(.*?)</h2>(.*)", section or "", re.S)
        if not m:
            return ""
        return f"<details{' open' if open_ else ''}><summary>{m.group(1)}</summary>{m.group(2)}</details>"

    bad = [v for v in verdicts if v[0] == 2]
    warn = [v for v in verdicts if v[0] == 1]
    good = [v for v in verdicts if v[0] == 0]
    mark = {2: "bad", 1: "warn", 0: "ok", -1: "na"}

    def item(v, why=True):
        l, n, val, w = v
        # השם והערך כל אחד מבודד (bdi): בלי זה מילה לועזית בסוף השם ("pull-up") והערך ("GPIO4") התחברו לרצף
        # אחד משמאל לימין, והשורה התהפכה
        return (f'<li class="{mark.get(l, "na")}"><span class="dot"></span><div><b><bdi dir="rtl">{n}</bdi></b> '
                f'<span class="val"><bdi dir="rtl">{bidi(str(val))}</bdi></span>' + (f"<p>{bidi(str(w))}</p>" if why and w else "") + "</div></li>")
    groups = [("המכשיר", ("טמפרטור", "מתח", "זיכרון", "דליפת", "עומס מעבד", "מקום פנוי", "הפעלות", "זמן עלייה", "כתיבה לכרטיס", "swap", "גישה לקבצים", "PoE")),
              ("נגינה ושמע", ("השהיית", "מנוע השיעורים", "קטיעות", "לחיצות כפולות", "אזהרות ושגיאות")),
              ("רשת", ("ברוקר", "ping", "Wi-Fi", "ניתוקים", "תנועה")),
              ("Home Assistant וטלגרם", ("Telegram", "HA ", "HA")),
              ("חומרה ומערכת", ())]

    def group_of(name):
        for g, keys in groups:
            if any(k in name for k in keys):
                return g
        return "חומרה ומערכת"
    fine = ""
    for g, _ in groups:
        gi = [v for v in good if group_of(v[1]) == g]
        if gi:
            fine += f'<h3>{g}</h3><ul class="list">' + "".join(item(v, False) for v in gi) + "</ul>"
    if bad:
        head = "נמצאה בעיה אחת" if len(bad) == 1 else f"נמצאו {len(bad)} בעיות"
    else:
        head = "יש מה לבדוק" if warn else "הכל תקין"
    if st.get("interrupted"):
        head = "הבדיקה נקטעה באמצע"
    elif st.get("abort"):
        head = "הבדיקה נעצרה: " + st["abort"]
    if partial:
        head = "דוח ביניים · " + head
    # "הבדיקה לא נעצרה" לא היה ברור: הכוונה שהיא רצה את כל הזמן, בלי עצירת בטיחות (חום, זיכרון) ובלי קטיעה
    nb = len(bad)
    sub = " · ".join(x for x in (
        (f"{nb} בעיות" if nb != 1 else "בעיה אחת") if nb else "",
        (f"{len(warn)} דברים לבדוק" if len(warn) != 1 else "דבר אחד לבדוק") if warn else "",
        f"{len(good)} בדיקות תקינות" if len(good) != 1 else "בדיקה אחת תקינה",
        "רצה את כל הזמן שנקבע" if not st.get("abort") and not st.get("interrupted") and not partial else "") if x)
    ps = jget(i0, "power_src") if i0 else {}
    meta = " · ".join(x for x in (html.escape(info.get("model", "")), f"זיכרון {info.get('mem_total', '?')} MB",
                                   f"התחלה {info.get('start', '')}", f"משך {span / 3600:.2f} שעות",
                                   html.escape(ps.get("src", "")) and "חשמל " + html.escape(ps.get("src", "")),
                                   f"Armonico {html.escape(info.get('version', ''))}") if x)
    main_charts = "".join(charts[i] for i in (0, 1, 3) if i < len(charts))
    other_charts = "".join(c for i, c in enumerate(charts) if i not in (0, 1, 3))
    comp = ("<h2>לפי רכיב (תוכנת המקשים, המסך, המנוע…)</h2><table><tr><th>רכיב</th><th>מעבד ממוצע</th><th>מעבד 95%</th>"
            "<th>זיכרון בהתחלה</th><th>בסוף</th><th>שיא</th><th>PSS שיא</th><th>שינוי MB/שעה</th><th>קבצים פתוחים</th>"
            f"<th>תהליכונים</th></tr>{''.join(grows)}</table><p class='note'>זיכרון ב-MB לפי RSS. PSS מחלק זיכרון משותף "
            "בין התהליכים, ולכן מדויק יותר כשמחברים רכיבים. מעבד: 100% הוא ליבה אחת מלאה. קבצים פתוחים ותהליכונים "
            f"שעולים כל הזמן הם דליפה מסוג אחר.</p><div class='minis'>{''.join(minis)}</div>")
    acted = (f"<h2>מה הבדיקה עשתה</h2><p>לחיצות קלידים {counts.get('notes', 0)} · כפתורים {counts.get('buttons', 0)} · "
             f"שיעורים {counts.get('lessons', 0)} · שירים {counts.get('songs', 0)} · שעונים מעוררים {counts.get('alarms', 0)} · "
             f"הקלטות מסך {counts.get('recordings', 0)}</p><p class='muted'>{phase_txt}</p><div class='legend'>{legend}</div>"
             "<p class='note'>הפס הצבעוני בראש כל גרף מראה איזה שלב רץ באותו רגע.</p>")
    events_ = ("<h2>אירועים ושורות מהיומן</h2><table>" + (ev or "<tr><td class='muted'>לא היו אירועים מיוחדים</td></tr>")
               + "</table><h3 style='margin-top:16px'>מהיומן</h3><table>" + (ex or "<tr><td class='muted'>אין שורות</td></tr>")
               + "</table><p class='note'>שורות ברמת warning ומעלה, ושורות עם Traceback, Error, failed, underrun ודומיהן "
               "משירותי Armonico, הקרנל, PipeWire ו-Mosquitto.</p>")
    a_l, a_d, v_l, v_d = (logo_uri(n) for n in ("armonico-logo-light.svg", "armonico-logo-dark.png", "vcl-day.png", "vcl-dark.png"))

    def logo(light, dark, cls, alt):
        return ((f'<img class="{cls} on-light" src="{light}" alt="{alt}">' if light else "")
                + (f'<img class="{cls} on-dark" src="{dark}" alt="{alt}">' if dark else ""))
    page = f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Armonico · דוח בדיקת עומס</title>
<style>
:root{{--bg:#fcfcfb;--ink:#141413;--dim:#5e5d59;--line:#e6e5e0;--grid:#efeeea;--panel:#f5f4f0;--ok:#1f8a3a;--warn:#b77900;--bad:#c0392b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--ink:#f0efec;--dim:#b4b3ab;--line:#33332f;--grid:#2a2a27;--panel:#222220;--ok:#3fb950;--warn:#d29922;--bad:#f0716b}}}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,"Segoe UI",Arial,sans-serif;margin:0 auto;padding:28px 22px 40px;max-width:900px}}
.on-dark{{display:none}} @media (prefers-color-scheme:dark){{.on-light{{display:none}} .on-dark{{display:inline}}}}
header{{display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding-bottom:14px}}
.arm{{height:54px}} .vcl{{height:30px}}
h1{{font-size:20px;margin:22px 0 2px;font-weight:650}} .meta{{color:var(--dim);font-size:13.5px}}
.result{{margin:18px 0 6px;padding:14px 16px;background:var(--panel);border-radius:10px;border-inline-start:4px solid var(--bad)}}
.result.good{{border-color:var(--ok)}} .result.warn{{border-color:var(--warn)}} .result b{{font-size:17px}} .result p{{margin:4px 0 0;color:var(--dim)}}
h2{{font-size:16px;margin:30px 0 8px}} h3{{font-size:13.5px;color:var(--dim);font-weight:600;margin:16px 0 4px}}
ul.list{{list-style:none;padding:0;margin:0}} ul.list li{{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}}
ul.list li:last-child{{border-bottom:0}}
.dot{{flex:none;width:9px;height:9px;border-radius:50%;margin-top:8px;background:var(--ok)}}
li.warn .dot{{background:var(--warn)}} li.bad .dot{{background:var(--bad)}} li.na .dot{{background:var(--dim)}}
.val{{color:var(--dim);margin-inline-start:6px}} ul.list p{{margin:2px 0 0;color:var(--dim);font-size:13.5px}}
.muted,.note{{color:var(--dim)}} .note{{font-size:13px;margin:4px 0 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:right;vertical-align:top}}
th{{color:var(--dim);font-weight:600}} .badge{{white-space:nowrap}} tr.ok .badge{{color:var(--ok)}} tr.warn .badge{{color:var(--warn)}}
tr.bad .badge{{color:var(--bad)}} tr.na .badge{{color:var(--dim)}} .mono{{font-family:ui-monospace,Consolas,monospace;font-size:12px;direction:ltr;text-align:left}}
.chart{{margin:14px 0}} .plot{{position:relative;direction:ltr}} svg{{width:100%;height:auto;display:block}}
.grid{{stroke:var(--grid);stroke-width:1}} .rule{{stroke:var(--bad);stroke-dasharray:4 4;stroke-width:1}}
.ax{{fill:var(--dim);font-size:11px}} .cross{{stroke:var(--dim);stroke-width:1;opacity:0}}
.tip{{position:absolute;top:0;pointer-events:none;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:12px;display:none;direction:rtl;white-space:nowrap}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--dim);margin-bottom:4px}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-inline-end:5px;vertical-align:-1px}}
details{{border-top:1px solid var(--line);padding:10px 0}} details>summary{{cursor:pointer;font-weight:600}}
details details{{border:0;padding:4px 0}} details details>summary{{font-weight:400;color:var(--dim)}}
pre.mono{{white-space:pre-wrap;background:var(--grid);padding:8px;border-radius:6px;max-height:340px;overflow:auto}}
.wide{{overflow-x:auto}}
footer{{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);display:flex;justify-content:space-between;color:var(--dim);font-size:12.5px}}
</style></head><body>
<header><span>{logo(a_l, a_d, "arm", "Armonico")}</span><span>{logo(v_l, v_d, "vcl", "Vesta Core Labs")}</span></header>
<h1>דוח בדיקת עומס · Armonico Pi</h1>
<div class="meta">{meta}</div>
<div class="result{' good' if not bad and not warn else ' warn' if not bad else ''}"><b>{html.escape(head)}</b><p>{sub}</p></div>
{('<h2>מה צריך טיפול</h2><ul class="list">' + "".join(item(v) for v in bad + warn) + "</ul>") if bad or warn else ""}
<h2>לאורך הבדיקה</h2>
{main_charts}
<h2>מה תקין</h2>
{fine or "<p class='muted'>אין</p>"}
<h2>פרטים</h2>
{fold(acted)}
{fold(sit_html)}
{fold("<h2>כל הגרפים</h2>" + other_charts)}
{fold(comp)}
{fold(net_html)}
{fold(remote_html)}
{fold(boot_html)}
{fold(stats_html)}
{fold(deep_html)}
{fold(inv_html)}
{fold(events_)}
<footer><span>Armonico · Your piano talks back</span><span>Vesta Core Labs</span></footer>
<script>
document.querySelectorAll('script.meta').forEach(function(m){{
  var d=JSON.parse(m.textContent), box=document.getElementById(m.dataset.for), svg=box.querySelector('svg'),
      tip=box.querySelector('.tip'), cross=svg.querySelector('.cross');
  function hide(){{tip.style.display='none';cross.style.opacity=0}}
  svg.addEventListener('mouseleave',hide);
  svg.addEventListener('mousemove',function(e){{
    var r=svg.getBoundingClientRect(), x=(e.clientX-r.left)/r.width*d.W;
    var t=(x-d.L)/(d.W-d.L-d.R)*d.span; if(t<0||t>d.span) return hide();
    var lines=[], hh=Math.floor(t/3600), mm=Math.floor(t%3600/60);
    lines.push('<b>'+hh+':'+(mm<10?'0':'')+mm+'</b>');
    d.s.forEach(function(s){{
      var i=0,best=1e18; for(var k=0;k<s.t.length;k++){{var dd=Math.abs(s.t[k]-t); if(dd<best){{best=dd;i=k}}}}
      if(s.v[i]!==null) lines.push('<span style="color:'+s.c+'">■</span> '+s.n+': '+s.v[i]+' '+d.unit);
    }});
    tip.innerHTML=lines.join('<br>'); tip.style.display='block';
    var px=(e.clientX-r.left); tip.style.left=Math.min(px+12, r.width-tip.offsetWidth-4)+'px';
    cross.setAttribute('x1',x); cross.setAttribute('x2',x); cross.style.opacity=.6;
  }});
}});
</script></body></html>"""
    (out / "report.html").write_text(page)
    return out / "report.html"


def main():
    ap = argparse.ArgumentParser(description="בדיקת עומס ומשאבים ל-Armonico Pi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--hours", type=float, default=4)
    r.add_argument("--minutes", type=float, default=None, help="במקום --hours")
    r.add_argument("--pin", default="", help="ה-PIN של הפרופיל שלך, אם הוא נעול")
    r.add_argument("--no-lessons", action="store_true")
    r.add_argument("--no-rec", action="store_true", help="בלי הקלטת מסך")
    r.add_argument("--no-alarm", action="store_true", help="בלי שעון מעורר (מצלצל תמיד ב-100%)")
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--out", default=None)
    r.add_argument("--secrets", default=None, help="קובץ עם HA_URL, HA_TOKEN, TELEGRAM_TOKEN (root בלבד)")
    r.add_argument("--ha-url", default=None)
    r.add_argument("--chat-id", default=None, help="הצ'אט לפקודות הטלגרם; ברירת מחדל: מי שהשתמש אחרון")
    r.add_argument("--tg-every", type=float, default=None,
                   help="דקות בין סבבי טלגרם (3 הודעות בכל סבב); ברירת מחדל 30, ובבדיקה של יותר משעתיים 60")
    r.add_argument("--no-ha", action="store_true")
    r.add_argument("--no-telegram", action="store_true")
    r.add_argument("--boot-info", default=None)
    r.add_argument("--reboot-after", action="store_true")
    h = sub.add_parser("ha-clean", help="מחיקת אוטומציות זמניות שנשארו (אם הבדיקה נהרגה)")
    h.add_argument("--secrets", default=None)
    h.add_argument("--ha-url", default=None)
    st = sub.add_parser("start", help="מפעיל בדיקה: start 15m | 30m | 1h | 2h | 4h | 1h30m")
    st.add_argument("duration")
    st.add_argument("--no-reboot", action="store_true", help="בלי ריבוט לפני ואחרי")
    st.add_argument("--reboot-later", action="store_true",
                    help="הכל מוכן לריבוט, אבל מי שקרא (המסך) מפעיל אותו בעצמו אחרי שהציג את ההודעה")
    sub.add_parser("boot")
    sub.add_parser("after-boot")
    sub.add_parser("stop", help="עוצר בדיקה שרצה: מנקה ומפיק דוח")
    sub.add_parser("status")
    sub.add_parser("recover", help="מחזיר את המכשיר אחרי בדיקה שנקטעה, בלי להתחיל בדיקה")
    p = sub.add_parser("report")
    p.add_argument("dir")
    a, rest = ap.parse_known_args()
    if a.cmd != "start" and rest:
        ap.error("ארגומנטים לא מוכרים: " + " ".join(rest))
    if a.cmd in ("start", "stop", "status", "recover", "boot", "after-boot") and os.geteuid() != 0:
        raise SystemExit("להריץ עם sudo")
    if a.cmd == "start":
        return start(a, rest)
    if a.cmd == "stop":
        return stop_cmd()
    if a.cmd == "status":
        return status()
    if a.cmd == "recover":
        return recover()
    if a.cmd == "boot":
        return boot()
    if a.cmd == "after-boot":
        return after_boot()
    if a.cmd == "report":
        print(report(a.dir))
        return
    if a.cmd == "ha-clean":
        sec = load_secrets(a.secrets)
        url = a.ha_url or sec.get("HA_DEVICE_URL") or sec.get("HA_URL")
        tok = sec.get("HA_DEVICE_TOKEN") or sec.get("HA_TOKEN")
        if not (url and tok):
            raise SystemExit("אין HA_URL ו-HA_TOKEN")
        print("נמחקו", HA(url, tok).remove_ours(), "אוטומציות")
        return
    if os.geteuid() != 0:
        raise SystemExit("להריץ עם sudo: הבדיקה קוראת את ההגדרות, היומן ונתוני כל השירותים")
    a.minutes = a.minutes if a.minutes is not None else a.hours * 60
    if a.tg_every is None:
        a.tg_every = 30 if a.minutes <= 120 else 60
    a.seed = a.seed if a.seed is not None else int(time.time()) % 100000
    Soak(a).main()


if __name__ == "__main__":
    main()
