#!/usr/bin/env python3
"""deepcheck.py - the deep system check, for the device's test window and for `armonico check --deep`.

One engine, on any Linux (Raspberry Pi, other ARM, x86): it measures what is there and skips what is not.

  collect()        the services (state, restarts, CPU and memory averaged over the whole time each has been
                   up, from systemd's own accounting, and over a short live window), the system (CPU, memory,
                   temperature, throttling and under-voltage, disk and the disk's write rate), the boot time by
                   stage, audio dropouts, the network and radios, the journal's errors of the last 24 hours,
                   and the history the sampler kept
  report_text()    the same report as text, in one language; save_report() writes it and a JSON copy
  sample()         one light sample for the history: `deepcheck.py sample`, run every 5 minutes by
                   armonico-sampler.timer. Samples collect in RAM (/run, a tmpfs); once an hour they are
                   rolled into one line of <data>/checks/history.json, and 7 days are kept: a few kilobytes
                   written 24 times a day.

Levels are as the full check's: OK 0, NOTE 1 (worth a look), PROBLEM 2, SKIP 3 (not there or not readable).
Everything that reads the machine goes through _run(), PROC, SYS and a few small functions, so the tests
replace them with fakes.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

OK, NOTE, PROBLEM, SKIP = 0, 1, 2, 3
LIVE_SECONDS = 30
SAMPLE_EVERY = 300
KEEP_DAYS = 7
KEEP_REPORTS = 10
PROC = "/proc"
SYS = "/sys"
RUN_DIR = os.environ.get("DEEPCHECK_RUN", "/run/armonico-deepcheck")
UNSET = 2 ** 64 - 1

# the services looked at, in this order: (unit, label)
BASE_UNITS = (("armonico-device", "armonico-device"), ("armonico-screen", "armonico-screen"),
              ("armonico-lessons", "armonico-lessons"), ("armonico-bridge", "armonico-bridge"),
              ("armonico-recorder", "armonico-recorder"), ("mosquitto", "mosquitto"),
              ("pipewire", "pipewire"), ("wireplumber", "wireplumber"), ("pipewire-pulse", "pipewire-pulse"),
              ("bluetooth", "bluetooth"), ("NetworkManager", "NetworkManager"))
# a program that runs in the user's session instead of a system unit: found by its process name
PROCESS_FALLBACK = {"pipewire": "pipewire", "wireplumber": "wireplumber", "pipewire-pulse": "pipewire-pulse"}

TX = {
    "title": ("Deep check", "בחינה מעמיקה"),
    "g.summary": ("Summary", "סיכום"), "g.services": ("Services", "שירותים"), "g.boot": ("Boot stages", "שלבי העלייה"),
    "g.system": ("System resources", "משאבי המערכת"), "g.audio": ("Audio", "שמע"),
    "g.net": ("Network and radios", "רשת ורדיו"), "g.storage": ("Storage", "אחסון"),
    "g.logs": ("Logs, errors in the last 24 hours", "יומנים, שגיאות ב-24 השעות האחרונות"),
    "g.history": ("History", "היסטוריה"),
    "cpuIdle": ("CPU idle", "מעבד במנוחה"), "memUsed": ("Memory in use", "זיכרון בשימוש"),
    "temp": ("Temperature", "טמפרטורה"), "bootTime": ("Boot time", "זמן עלייה"),
    "svc": ("Service", "שירות"), "state": ("State", "מצב"), "cpuAvg": ("CPU avg", "מעבד ממוצע"),
    "cpuNow": ("CPU now", "מעבד עכשיו"), "mem": ("Memory", "זיכרון"), "write": ("Writes", "כתיבה"),
    "restarts": ("Restarts", "הפעלות מחדש"),
    "active": ("active", "פעיל"), "inactive": ("stopped", "כבוי"), "failed": ("failed", "נכשל"),
    "notfound": ("not installed", "לא מותקן"), "activating": ("starting", "עולה"),
    "cpu": ("CPU", "מעבד"), "load": ("Load average", "עומס ממוצע"), "cores": ("Cores", "ליבות"),
    "memory": ("Memory", "זיכרון"), "swap": ("Swap", "Swap"), "uptime": ("Up for", "פעיל כבר"),
    "throttle": ("Power and speed limits", "מתח והגבלת מהירות"),
    "thOk": ("none seen since boot", "לא נראו מאז העלייה"),
    "thUnder": ("under-voltage now", "מתח נמוך עכשיו"), "thUnderPast": ("under-voltage since boot", "מתח נמוך מאז העלייה"),
    "thCap": ("speed limited now", "מהירות מוגבלת עכשיו"), "thCapPast": ("speed limited since boot", "מהירות הוגבלה מאז העלייה"),
    "thHot": ("temperature limit now", "הגבלת חום עכשיו"), "thHotPast": ("temperature limit since boot", "הגבלת חום מאז העלייה"),
    "disk": ("Disk", "דיסק"), "diskWrite": ("Disk writes", "כתיבה לדיסק"),
    "diskLive": ("in the check", "בזמן הבחינה"), "diskAvg": ("average since boot", "ממוצע מאז העלייה"),
    "free": ("free", "פנוי"),
    "bootFirmware": ("Firmware", "קושחה"), "bootLoader": ("Loader", "מנהל אתחול"), "bootKernel": ("Kernel", "קרנל"),
    "bootInitrd": ("Initrd", "initrd"), "bootUser": ("Services start", "עליית השירותים"), "bootTotal": ("Total", "סך הכל"),
    "bootSlow": ("slowest units", "היחידות האיטיות"),
    "xruns": ("Audio dropouts this boot", "קיטועי שמע מאז העלייה"), "none": ("none", "אין"),
    "net": ("Connection", "חיבור"), "netNone": ("not connected", "לא מחובר"),
    "radio": ("Radio", "רדיו"), "on": ("on", "פועל"), "off": ("off", "כבוי"), "blocked": ("blocked", "חסום"),
    "wifiSignal": ("Wi-Fi signal", "עוצמת Wi-Fi"),
    "errors": ("errors", "שגיאות"), "noErrors": ("no errors", "אין שגיאות"), "noAccess": ("not readable here", "לא נקרא כאן"),
    "histNone": ("not enough yet, the sampler has run for less than an hour", "עוד אין מספיק, הדוגם פועל פחות משעה"),
    "histSpan": ("Covers", "טווח"), "histCpu": ("CPU, average and peak", "מעבד, ממוצע ושיא"),
    "histTemp": ("Temperature, peak", "טמפרטורה, שיא"), "histMem": ("Memory, average", "זיכרון, ממוצע"),
    "histBusy": ("Busiest hours", "השעות העמוסות"), "histWrite": ("Disk writes, per hour", "כתיבה לדיסק, לשעה"),
    "hours": ("hours", "שעות"), "days": ("days", "ימים"),
    "measured": ("measured", "נמדד"), "skipped": ("not available", "לא זמין"),
    "report": ("Deep check report", "דוח בחינה מעמיקה"), "when": ("When", "מתי"), "machine": ("Machine", "מכונה"),
    "live": ("Live window", "חלון חי"), "sec": ("s", "ש'"),
    "counts": ("{p} problems, {w} to look at, {o} fine, {s} not available", "{p} בעיות, {w} לבדיקה, {o} תקינים, {s} לא זמינים"),
}


def tx(key, lang="en", **kw):
    pair = TX.get(key)
    s = key if pair is None else pair[1 if lang == "he" else 0]
    return s.format(**kw) if kw else s


# ---------------------------------------------------------------------- reading the machine
def _run(cmd, timeout=10):
    """(returncode, stdout); (127, "") when the program is not there."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def now_mono():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def have(cmd):
    return shutil.which(cmd) is not None


def uptime():
    try:
        return float(_read(f"{PROC}/uptime").split()[0])
    except (ValueError, IndexError):
        return 0.0


def cpu_times():
    """(busy, total) jiffies from /proc/stat, all cores together."""
    for line in _read(f"{PROC}/stat").splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:9]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            return sum(v) - idle, sum(v)
    return 0, 0


def meminfo():
    d = {}
    for line in _read(f"{PROC}/meminfo").splitlines():
        k, _, rest = line.partition(":")
        try:
            d[k] = int(rest.split()[0]) * 1024
        except (ValueError, IndexError):
            pass
    return d


def temperature():
    """Degrees C: the first thermal zone, else the Pi's vcgencmd; None when neither."""
    try:
        return int(_read(f"{SYS}/class/thermal/thermal_zone0/temp").strip()) / 1000.0
    except ValueError:
        pass
    if have("vcgencmd"):
        rc, out = _run(["vcgencmd", "measure_temp"], 5)
        m = re.search(r"temp=([\d.]+)", out)
        if rc == 0 and m:
            return float(m.group(1))
    for i in range(6):
        try:
            return int(_read(f"{SYS}/class/thermal/thermal_zone{i}/temp").strip()) / 1000.0
        except ValueError:
            continue
    return None


def throttled():
    """The bits of vcgencmd get_throttled, or None when it is not a Pi (or vcgencmd is not there)."""
    if not have("vcgencmd"):
        return None
    rc, out = _run(["vcgencmd", "get_throttled"], 5)
    m = re.search(r"0x([0-9a-fA-F]+)", out)
    if rc != 0 or not m:
        return None
    v = int(m.group(1), 16)
    return {"raw": v, "under_now": bool(v & 1), "cap_now": bool(v & 2), "throttled_now": bool(v & 4), "hot_now": bool(v & 8),
            "under_past": bool(v & 0x10000), "cap_past": bool(v & 0x20000), "throttled_past": bool(v & 0x40000),
            "hot_past": bool(v & 0x80000)}


def root_disk():
    """The whole disk the root file system is on (mmcblk0, nvme0n1, sda), or ""."""
    dev = ""
    for line in _read(f"{PROC}/mounts").splitlines():
        f = line.split()
        if len(f) > 1 and f[1] == "/" and f[0].startswith("/dev/"):
            dev = f[0][5:]
    if not dev:
        return ""
    m = re.fullmatch(r"(mmcblk\d+|nvme\d+n\d+)p\d+", dev) or re.fullmatch(r"([a-z]+)\d+", dev)
    return m.group(1) if m else dev


def disk_written(disk):
    """Bytes written to the disk since boot (/proc/diskstats), or None."""
    for line in _read(f"{PROC}/diskstats").splitlines():
        f = line.split()
        if len(f) > 9 and f[2] == disk:
            return int(f[9]) * 512
    return None


def systemd_up():
    return os.path.isdir("/run/systemd/system") and have("systemctl")


def parse_show(text):
    """systemctl show output (blocks separated by empty lines) -> [dict]."""
    blocks, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


SHOW_PROPS = "Id,LoadState,ActiveState,SubState,NRestarts,CPUUsageNSec,MemoryCurrent,MemoryPeak,IOWriteBytes,ActiveEnterTimestampMonotonic,MainPID"


def _num(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return None if n >= UNSET else n


def units_snapshot(units):
    """{unit: info} for the units systemd knows, in one systemctl call."""
    if not systemd_up():
        return {}
    rc, out = _run(["systemctl", "show", "-p", SHOW_PROPS, "--"] + [u + ".service" for u in units], 15)
    res = {}
    for b in parse_show(out):
        name = b.get("Id", "").removesuffix(".service")
        if not name:
            continue
        res[name] = {"load": b.get("LoadState", ""), "active": b.get("ActiveState", ""), "sub": b.get("SubState", ""),
                     "restarts": _num(b.get("NRestarts")) or 0, "cpu_ns": _num(b.get("CPUUsageNSec")),
                     "mem": _num(b.get("MemoryCurrent")), "mem_peak": _num(b.get("MemoryPeak")),
                     "io_write": _num(b.get("IOWriteBytes")),
                     "since": (_num(b.get("ActiveEnterTimestampMonotonic")) or 0) / 1e6, "pid": _num(b.get("MainPID")) or 0}
    scr = res.get("armonico-screen")
    if scr and scr["load"] != "not-found":
        p = screen_process()
        if p:                                     # the screen program in its login session, added to greetd's own
            for k, v in (("cpu_ns", p[0]), ("mem", p[1]), ("io_write", p[2])):
                if v is not None:
                    scr[k] = (scr[k] or 0) + v
    return res


def screen_process():
    """The screen program (device/screen.py), which greetd starts in a login session: PAM moves it out of
    armonico-screen.service into the user's session scope, so systemd's counters of the unit hold greetd only.
    (cpu nanoseconds, rss bytes, bytes written) of it, or None when it is not found or is in the unit after all."""
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        return None
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    for pid in pids:
        args = _read(f"{PROC}/{pid}/cmdline").split("\0")
        if not any(a.endswith("/device/screen.py") for a in args[1:3]):
            continue
        if "armonico-screen.service" in _read(f"{PROC}/{pid}/cgroup"):
            return None                           # counted by the unit already
        rest = _read(f"{PROC}/{pid}/stat").rsplit(")", 1)[-1].split()
        try:
            cpu = (int(rest[11]) + int(rest[12])) / hz * 1e9
        except (IndexError, ValueError):
            continue
        rss = wrote = None
        for line in _read(f"{PROC}/{pid}/status").splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
        for line in _read(f"{PROC}/{pid}/io").splitlines():
            if line.startswith("write_bytes:"):
                wrote = int(line.split()[1])
        return cpu, rss, wrote
    return None


def discovered_units():
    """armonico-*.service units that are running or failed and not in the base list."""
    if not systemd_up():
        return []
    rc, out = _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "armonico-*"], 10)
    known = {u for u, _ in BASE_UNITS}
    found = []
    for line in out.splitlines():
        f = line.split()
        if len(f) > 3 and f[0].endswith(".service"):
            name = f[0].removesuffix(".service")
            if name not in known and f[2] in ("active", "failed", "activating"):
                found.append(name)
    return found


def process_stats(comm):
    """For a program with no system unit: (cpu seconds, rss bytes, age seconds) of the oldest process by that
    name, from /proc, or None."""
    best = None
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        return None
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    for pid in pids:
        if _read(f"{PROC}/{pid}/comm").strip() != comm:
            continue
        stat = _read(f"{PROC}/{pid}/stat")
        rest = stat.rsplit(")", 1)[-1].split()
        try:
            cpu = (int(rest[11]) + int(rest[12])) / hz
            start = int(rest[19]) / hz
        except (IndexError, ValueError):
            continue
        rss = 0
        for line in _read(f"{PROC}/{pid}/status").splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
        age = max(1.0, uptime() - start)
        if best is None or age > best[2]:
            best = (cpu, rss, age)
    return best


# ---------------------------------------------------------------------- the measurements
def service_rows(first, last, span, units_all):
    """The services' rows from two snapshots `span` seconds apart (the live window)."""
    rows = []
    now = now_mono()
    for unit, label in units_all:
        a, b = first.get(unit), last.get(unit)
        row = {"unit": unit, "label": label, "state": "notfound", "level": SKIP, "restarts": 0, "cpu_avg": None,
               "cpu_live": None, "mem": None, "mem_peak": None, "write_mbh": None, "source": ""}
        if b is None or b["load"] == "not-found":
            ps = process_stats(PROCESS_FALLBACK[unit]) if unit in PROCESS_FALLBACK else None
            if ps:
                row.update(state="active", level=OK, cpu_avg=100.0 * ps[0] / ps[2], mem=ps[1], source="process")
            rows.append(row)
            continue
        act = b["active"]
        row["state"] = {"active": "active", "failed": "failed", "activating": "activating"}.get(act, "inactive")
        row["restarts"] = b["restarts"]
        row["source"] = "systemd"
        up = max(1.0, now - b["since"]) if b["since"] else None
        if b["cpu_ns"] is not None and up and act == "active":
            row["cpu_avg"] = 100.0 * (b["cpu_ns"] / 1e9) / up
        if a and a["cpu_ns"] is not None and b["cpu_ns"] is not None and span > 0 and act == "active":
            row["cpu_live"] = max(0.0, 100.0 * (b["cpu_ns"] - a["cpu_ns"]) / 1e9 / span)
        row["mem"], row["mem_peak"] = b["mem"], b["mem_peak"]
        if b["io_write"] is not None and up and act == "active":
            row["write_mbh"] = b["io_write"] / 1e6 / (up / 3600.0)
        if act == "failed":
            row["level"] = PROBLEM
        elif act != "active":
            row["level"] = SKIP if unit not in ("armonico-bridge", "armonico-lessons", "mosquitto") else NOTE
        elif row["restarts"] > 0:
            row["level"] = NOTE
        elif (row["cpu_avg"] or 0) > 50 or (row["cpu_live"] or 0) > 80:
            row["level"] = NOTE
        else:
            row["level"] = OK
        rows.append(row)
    return rows


def parse_duration(s):
    """'1min 2.5s', '345ms', '2.1s' -> seconds."""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)\s*(min|ms|us|µs|s|h)\b", s):
        v = float(num)
        total += {"h": 3600, "min": 60, "s": 1, "ms": 0.001, "us": 1e-6, "µs": 1e-6}[unit] * v
    return total


def boot_info():
    if not have("systemd-analyze"):
        return None
    rc, out = _run(["systemd-analyze", "time"], 20)
    if rc != 0 or not out.strip():
        return None
    stages = {}
    for num, name in re.findall(r"((?:[\d.]+\s*(?:min|ms|s)\s*)+)\((\w+)\)", out):
        stages[name] = parse_duration(num)
    m = re.search(r"=\s*((?:[\d.]+\s*(?:min|ms|s)\s*)+)$", out.strip().splitlines()[0] if out.strip() else "")
    total = parse_duration(m.group(1)) if m else sum(stages.values())
    rc, bl = _run(["systemd-analyze", "blame", "--no-pager"], 20)
    blame = []
    for line in bl.splitlines():
        m = re.match(r"\s*((?:[\d.]+\s*(?:min|ms|s)\s*)+)(\S+)", line)
        if m:
            blame.append((m.group(2), parse_duration(m.group(1))))
    ours = [(u, s) for u, s in blame if u.startswith("armonico")]
    others = [(u, s) for u, s in blame if not u.startswith("armonico")][:3]
    return {"total": total, "stages": stages, "ours": ours[:8], "slowest": others}


JOURNAL_GROUPS = ("systemd-journal", "adm", "wheel")


def journal_readable():
    """The system journal can be read: root, or a member of a group journald lets read it. For anyone else
    journalctl answers 0 with only the user's own lines (and -q hides its hint), which must not read as
    "no errors"."""
    if os.geteuid() == 0:
        return True
    try:
        import grp
        mine = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (ImportError, KeyError, OSError):
        return False
    return bool(mine & set(JOURNAL_GROUPS))


def journal_errors():
    """{unit: [count, last message]} of the last 24 hours, or None when the journal cannot be read."""
    if not have("journalctl") or not journal_readable():
        return None
    rc, out = _run(["journalctl", "-p", "err", "--since", "24 hours ago", "-o", "json", "--no-pager", "-q", "-n", "3000",
                    "--output-fields=_SYSTEMD_UNIT,SYSLOG_IDENTIFIER,MESSAGE"], 25)
    if rc != 0 and not out.strip():
        return None
    res = {}
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or "system"
        msg = e.get("MESSAGE")
        if isinstance(msg, list):
            msg = "(binary)"
        c = res.setdefault(u, [0, ""])
        c[0] += 1
        c[1] = str(msg or "")[:140]
    return res


def audio_xruns():
    """How many dropouts the audio server logged since boot, or None. PipeWire runs in the screen user's own
    session, so its lines are readable to that user without the journal groups."""
    if not have("journalctl"):
        return None
    rc, out = _run(["journalctl", "-b", "-g", "xrun|underrun|buffer underflow", "--no-pager", "-q", "-n", "500",
                    "-o", "cat"], 15)
    if rc not in (0, 1):
        return None
    return len([x for x in out.splitlines() if x.strip()])


VIRTUAL_NICS = ("ifb", "veth", "docker", "br-", "virbr", "tailscale", "wg", "tun", "tap")


def network_info():
    info = {"devices": [], "radios": [], "wifi_signal": None}
    if have("nmcli"):
        rc, out = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], 10)
        for line in out.splitlines():
            f = line.replace("\\:", "\0").split(":")
            if len(f) >= 3 and f[1] not in ("loopback", "bridge", "tun", "wifi-p2p") and not f[0].startswith(VIRTUAL_NICS):
                info["devices"].append({"dev": f[0], "type": f[1], "state": f[2], "conn": (f[3] if len(f) > 3 else "").replace("\0", ":")})
    else:
        try:
            for n in sorted(os.listdir(f"{SYS}/class/net")):
                if n == "lo" or n.startswith(VIRTUAL_NICS):
                    continue
                st = _read(f"{SYS}/class/net/{n}/operstate").strip()
                info["devices"].append({"dev": n, "type": "wifi" if n.startswith("wl") else "ethernet", "state": "connected" if st == "up" else st, "conn": ""})
        except OSError:
            pass
    try:
        for n in sorted(os.listdir(f"{SYS}/class/rfkill")):
            base = f"{SYS}/class/rfkill/{n}"
            kind = _read(f"{base}/type").strip()
            if kind in ("wlan", "bluetooth"):
                info["radios"].append({"type": kind, "soft": _read(f"{base}/soft").strip() == "1",
                                       "hard": _read(f"{base}/hard").strip() == "1"})
    except OSError:
        pass
    for line in _read(f"{PROC}/net/wireless").splitlines()[2:]:
        f = line.split()
        if len(f) > 3:
            try:
                info["wifi_signal"] = int(float(f[2].rstrip(".")))
            except ValueError:
                pass
    return info


def is_pi():
    return "raspberry pi" in _read(f"{PROC}/device-tree/model").lower() or have("vcgencmd")


def machine_kind():
    m = platform.machine().lower()
    if is_pi():
        model = _read(f"{PROC}/device-tree/model").strip("\0\n ")
        return model or "Raspberry Pi"
    return "ARM" if m.startswith(("arm", "aarch")) else "x86" if m in ("x86_64", "amd64", "i686", "i386") else m


# ---------------------------------------------------------------------- the history
def data_dir():
    return Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))


def checks_dir():
    return data_dir() / "checks"


def history_file():
    return checks_dir() / "history.json"


def load_history():
    try:
        h = json.loads(history_file().read_text())
        return [x for x in h.get("hours", []) if isinstance(x, dict) and "h" in x] if isinstance(h, dict) else []
    except (OSError, ValueError):
        return []


def snapshot_sample(units):
    """One light reading for the history."""
    busy, total = cpu_times()
    mem = meminfo()
    disk = root_disk()
    s = {"t": time.time(), "mono": now_mono(), "busy": busy, "total": total,
         "mem_used": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0), "temp": temperature(),
         "disk_w": disk_written(disk) if disk else None, "units": {}}
    for u, v in units_snapshot(units).items():
        if v["load"] != "not-found":
            s["units"][u] = [v["cpu_ns"], v["mem"], v["io_write"]]
    return s


def roll_up(samples):
    """One history line from consecutive samples of one hour (the first may be the previous hour's last)."""
    if len(samples) < 2:
        return None
    first, last = samples[0], samples[-1]
    span = last["mono"] - first["mono"]
    if span <= 0:
        return None
    db, dt = last["busy"] - first["busy"], last["total"] - first["total"]
    temps = [s["temp"] for s in samples if s.get("temp") is not None]
    h = int(samples[-1]["t"] // 3600) * 3600
    row = {"h": h, "cpu": round(100.0 * db / dt, 1) if dt > 0 else 0.0,
           "mem": round(sum(s["mem_used"] for s in samples) / len(samples) / 1e6, 1),
           "temp": round(max(temps), 1) if temps else None, "units": {}}
    if first.get("disk_w") is not None and last.get("disk_w") is not None and last["disk_w"] >= first["disk_w"]:
        row["wr"] = round((last["disk_w"] - first["disk_w"]) / 1e6 / (span / 3600.0), 2)
    for u, v in last["units"].items():
        p = first["units"].get(u)
        cpu = None
        if p and v[0] is not None and p[0] is not None and v[0] >= p[0]:
            cpu = round(100.0 * (v[0] - p[0]) / 1e9 / span, 2)
        peak = max([x["units"][u][1] or 0 for x in samples if u in x["units"]] or [0])
        row["units"][u] = {"cpu": cpu, "mem": round(peak / 1e6, 1)}
    return row


def sample(run_dir=None, now=None):
    """The sampler: one reading appended to the RAM file; when an hour has ended, its line is rolled into the
    history on disk. Returns the rolled hours (0 or more)."""
    run = Path(run_dir or RUN_DIR)
    try:
        run.mkdir(parents=True, exist_ok=True)
    except OSError:
        run = Path("/tmp/armonico-deepcheck")
        run.mkdir(parents=True, exist_ok=True)
    f = run / "samples.jsonl"
    units = [u for u, _ in BASE_UNITS] + discovered_units()
    cur = snapshot_sample(units)
    if now is not None:
        cur["t"] = now
    old = []
    for line in _read(f).splitlines():
        try:
            old.append(json.loads(line))
        except ValueError:
            pass
    # a restart of the machine: the counters began again, the old samples cannot be compared with the new
    if old and (cur["mono"] < old[-1]["mono"] or cur["total"] < old[-1]["total"]):
        old = []
    hour = int(cur["t"] // 3600)
    done, keep = [], old
    if old and int(old[-1]["t"] // 3600) != hour:
        done = old
        keep = [old[-1]]
    rolled = 0
    if done:
        row = roll_up(done)
        if row:
            hist = [x for x in load_history() if x["h"] > cur["t"] - KEEP_DAYS * 86400 and x["h"] != row["h"]]
            hist.append(row)
            try:
                checks_dir().mkdir(parents=True, exist_ok=True)
                write_whole(checks_dir(), history_file().name, json.dumps({"v": 1, "hours": hist}))
                rolled = 1
            except OSError:
                pass
    keep.append(cur)
    tmp = f.with_name(".samples.tmp")
    tmp.write_text("\n".join(json.dumps(x) for x in keep) + "\n")
    os.replace(tmp, f)
    return rolled


def history_summary(hours):
    """Averages, peaks and busy hours of the kept lines, or None when there is less than an hour."""
    if not hours:
        return None
    n = len(hours)
    cpus = [h["cpu"] for h in hours]
    temps = [h["temp"] for h in hours if h.get("temp") is not None]
    byhour = {}
    for h in hours:
        byhour.setdefault(datetime.fromtimestamp(h["h"]).hour, []).append(h["cpu"])
    busy = sorted(((sum(v) / len(v), k) for k, v in byhour.items()), reverse=True)[:3]
    wr = [h["wr"] for h in hours if h.get("wr") is not None]
    units = {}
    for h in hours:
        for u, v in h.get("units", {}).items():
            d = units.setdefault(u, {"cpu": [], "mem": 0.0})
            if v.get("cpu") is not None:
                d["cpu"].append(v["cpu"])
            d["mem"] = max(d["mem"], v.get("mem") or 0.0)
    return {"hours": n, "span": hours[-1]["h"] - hours[0]["h"] + 3600, "cpu_avg": sum(cpus) / n, "cpu_peak": max(cpus),
            "temp_peak": max(temps) if temps else None, "mem_avg": sum(h["mem"] for h in hours) / n,
            "busy_hours": [(k, round(v, 1)) for v, k in busy], "wr_avg": sum(wr) / len(wr) if wr else None,
            "units": {u: {"cpu_avg": sum(d["cpu"]) / len(d["cpu"]) if d["cpu"] else None,
                          "cpu_peak": max(d["cpu"]) if d["cpu"] else None, "mem_peak": d["mem"]} for u, d in units.items()}}


# ---------------------------------------------------------------------- the check
def collect(live=None, progress=None, sleep=None):
    """Measures everything. progress(fraction, step) is called as it goes, step one of: services, live, system,
    boot, logs, history. Returns the report dict (see build_groups for how it reads)."""
    live = LIVE_SECONDS if live is None else live
    sleep = sleep or time.sleep

    def say(frac, step):
        if progress:
            try:
                progress(frac, step)
            except Exception:
                pass

    rep = {"v": 1, "when": datetime.now().isoformat(timespec="seconds"), "host": platform.node(), "kind": machine_kind(),
           "pi": is_pi(), "uptime": uptime(), "live": live, "systemd": systemd_up()}
    say(0.0, "services")
    units_all = list(BASE_UNITS) + [(u, u) for u in discovered_units()]
    names = [u for u, _ in units_all]
    s1, c1 = units_snapshot(names), cpu_times()
    mem1 = meminfo()
    disk = root_disk()
    w1 = disk_written(disk) if disk else None
    t1 = now_mono()
    for i in range(int(live)):
        say(0.05 + 0.75 * i / max(1, live), "live")
        sleep(1)
    span = max(float(live), now_mono() - t1) if live else 0.0
    s2, c2 = units_snapshot(names), cpu_times()
    w2 = disk_written(disk) if disk else None
    say(0.8, "system")
    rep["services"] = service_rows(s1, s2, span if live else 0, units_all)
    db, dt = c2[0] - c1[0], c2[1] - c1[1]
    mem = meminfo()
    load = _read(f"{PROC}/loadavg").split()
    sysd = {"cores": os.cpu_count() or 1, "load": [float(x) for x in load[:3]] if len(load) >= 3 else None,
            "mem_total": mem.get("MemTotal"), "mem_avail": mem.get("MemAvailable"),
            "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0),
            "cpu_busy": round(100.0 * db / dt, 1) if dt > 0 else None, "temp": temperature(), "throttled": throttled()}
    sysd["cpu_idle"] = round(100.0 - sysd["cpu_busy"], 1) if sysd["cpu_busy"] is not None else None
    rep["system"] = sysd
    st = {"disks": []}
    seen = set()
    for label, p in (("/", "/"), ("data", str(data_dir()))):
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        if (u.total, u.free) in seen:
            continue
        seen.add((u.total, u.free))
        st["disks"].append({"label": label, "path": p, "total": u.total, "free": u.free})
    if disk and w2 is not None:
        up = max(1.0, uptime())
        st["device"] = disk
        st["write_avg_mbh"] = w2 / 1e6 / (up / 3600.0)
        if w1 is not None and span > 0:
            st["write_live_mbh"] = max(0, w2 - w1) / 1e6 / (span / 3600.0)
    rep["storage"] = st
    rep["boot"] = boot_info()
    say(0.88, "boot")
    rep["audio"] = {"xruns": audio_xruns()}
    rep["network"] = network_info()
    say(0.94, "logs")
    rep["journal"] = journal_errors()
    rep["history"] = history_summary(load_history())
    rep["groups"] = build_groups(rep)
    n = counts(rep["groups"])
    rep["counts"] = n
    say(1.0, "history")
    return rep


def counts(groups):
    n = {OK: 0, NOTE: 0, PROBLEM: 0, SKIP: 0}
    for g in groups:
        for r in g["rows"]:
            n[r["level"]] += 1
    return n


def mb(v):
    return "-" if v is None else f"{v / 1e6:.0f} MB"


def gb(v):
    return f"{v / 1e9:.1f} GB"


def pct(v):
    return "-" if v is None else f"{v:.1f}%"


def row(label, value, level=OK, detail=""):
    return {"label": label, "value": value, "level": level, "detail": detail}


def build_groups(rep):
    """[{key, rows: [{label: key or text, value, level, detail}]}]: keys of TX in `label`/`value` are translated
    by group_rows(); plain strings are kept."""
    groups = []
    sysd, svc = rep["system"], rep["services"]
    # services: one row per service; the table shows the numbers, the group row the verdict
    rows = []
    for s in svc:
        if s["state"] == "notfound" and s["source"] == "":
            rows.append(row(s["label"], "notfound", SKIP))
            continue
        rows.append(row(s["label"], s["state"], s["level"],
                        f"cpu {pct(s['cpu_avg'])} / {pct(s['cpu_live'])}, mem {mb(s['mem'])}, restarts {s['restarts']}"))
    groups.append({"key": "services", "rows": rows})
    b = rep.get("boot")
    rows = []
    if b:
        for k, name in (("firmware", "bootFirmware"), ("loader", "bootLoader"), ("kernel", "bootKernel"), ("initrd", "bootInitrd"),
                        ("userspace", "bootUser")):
            if k in b["stages"]:
                rows.append(row(name, f"{b['stages'][k]:.1f} s"))
        rows.append(row("bootTotal", f"{b['total']:.1f} s", NOTE if b["total"] > 60 else OK))
        for u, sec in b["ours"]:
            rows.append(row(u, f"{sec:.1f} s", NOTE if sec > 20 else OK))
        for u, sec in b["slowest"]:
            rows.append(row(u, f"{sec:.1f} s", OK, "bootSlow"))
    else:
        rows.append(row("bootTotal", "skipped", SKIP))
    groups.append({"key": "boot", "rows": rows})
    rows = []
    idle = sysd.get("cpu_idle")
    rows.append(row("cpu", "-" if idle is None else f"{sysd['cpu_busy']:.1f}% / {idle:.1f}%", OK if idle is None or idle > 20 else NOTE,
                    "busy / idle"))
    if sysd.get("load"):
        ld = sysd["load"]
        rows.append(row("load", " ".join(f"{x:.2f}" for x in ld), NOTE if ld[0] > sysd["cores"] * 1.5 else OK))
    rows.append(row("cores", str(sysd["cores"])))
    if sysd.get("mem_total"):
        used = sysd["mem_total"] - (sysd["mem_avail"] or 0)
        low = sysd["mem_avail"] is not None and sysd["mem_avail"] < sysd["mem_total"] * 0.1
        rows.append(row("memory", f"{mb(used)} / {mb(sysd['mem_total'])}", NOTE if low else OK))
        if sysd.get("swap_total"):
            su = sysd["swap_total"] - sysd["swap_free"]
            rows.append(row("swap", f"{mb(su)} / {mb(sysd['swap_total'])}", NOTE if su > sysd["swap_total"] * 0.5 else OK))
    t = sysd.get("temp")
    rows.append(row("temp", "skipped" if t is None else f"{t:.0f}°C", SKIP if t is None else PROBLEM if t >= 80 else NOTE if t >= 70 else OK))
    th = sysd.get("throttled")
    if th is None:
        rows.append(row("throttle", "skipped", SKIP))
    else:
        bad = [(k, lvl) for k, lvl in (("thUnder", PROBLEM if th["under_now"] else 0), ("thCap", NOTE if th["cap_now"] or th["throttled_now"] else 0),
                                        ("thHot", NOTE if th["hot_now"] else 0), ("thUnderPast", NOTE if th["under_past"] and not th["under_now"] else 0),
                                        ("thCapPast", NOTE if (th["cap_past"] or th["throttled_past"]) and not (th["cap_now"] or th["throttled_now"]) else 0),
                                        ("thHotPast", NOTE if th["hot_past"] and not th["hot_now"] else 0)) if lvl]
        if not bad:
            rows.append(row("throttle", "thOk", OK))
        for k, lvl in bad:
            rows.append(row("throttle", k, lvl))
    groups.append({"key": "system", "rows": rows})
    x = rep["audio"]["xruns"]
    groups.append({"key": "audio", "rows": [row("xruns", "skipped" if x is None else "none" if x == 0 else str(x),
                                                SKIP if x is None else OK if x == 0 else NOTE if x < 20 else PROBLEM)]})
    net = rep["network"]
    rows = []
    for d in net["devices"]:
        up = d["state"].startswith("connected")
        rows.append(row("net", f"{d['dev']} {d['type']}: {d['state']}" + (f" ({d['conn']})" if d["conn"] and up else ""), OK if up or d["state"] in ("unavailable", "disconnected", "down", "unmanaged") else NOTE))
    if net["devices"] and not any(d["state"].startswith("connected") for d in net["devices"]):
        rows.append(row("net", "netNone", NOTE))
    if not net["devices"]:
        rows.append(row("net", "skipped", SKIP))
    for r in net["radios"]:
        state = "blocked" if r["hard"] else "off" if r["soft"] else "on"
        name = "Wi-Fi" if r["type"] == "wlan" else "Bluetooth"
        rows.append(row("radio", {"en": f"{name}: {tx(state, 'en')}", "he": f"{name}: {tx(state, 'he')}"}, OK))
    if net.get("wifi_signal") is not None:
        rows.append(row("wifiSignal", f"{net['wifi_signal']}/70", NOTE if net["wifi_signal"] < 30 else OK))
    groups.append({"key": "net", "rows": rows})
    st = rep["storage"]
    rows = []
    for d in st["disks"]:
        used = 100.0 * (d["total"] - d["free"]) / max(1, d["total"])
        rows.append(row("disk", f"{d['path']}: {gb(d['total'] - d['free'])} / {gb(d['total'])} ({used:.0f}%)",
                        PROBLEM if used >= 95 else NOTE if used >= 85 else OK))
    if "write_avg_mbh" in st:
        rows.append(row("diskWrite", f"{st['write_avg_mbh']:.0f} MB/h", NOTE if st["write_avg_mbh"] > 300 else OK, "diskAvg"))
    if "write_live_mbh" in st:
        rows.append(row("diskWrite", f"{st['write_live_mbh']:.0f} MB/h", NOTE if st["write_live_mbh"] > 600 else OK, "diskLive"))
    if not rows:
        rows.append(row("disk", "skipped", SKIP))
    groups.append({"key": "storage", "rows": rows})
    j = rep.get("journal")
    rows = []
    if j is None:
        rows.append(row("errors", "noAccess", SKIP))
    elif not j:
        rows.append(row("errors", "noErrors", OK))
    else:
        for u, (n, msg) in sorted(j.items(), key=lambda kv: -kv[1][0])[:8]:
            rows.append(row(u, str(n), PROBLEM if n >= 200 else NOTE, msg))
    groups.append({"key": "logs", "rows": rows})
    h = rep.get("history")
    rows = []
    if not h:
        rows.append(row("histCpu", "histNone", SKIP))
    else:
        span = h["span"] / 3600.0
        rows.append(row("histSpan", f"{span:.0f} h" if span < 48 else f"{span / 24:.1f} d"))
        rows.append(row("histCpu", f"{h['cpu_avg']:.1f}% / {h['cpu_peak']:.1f}%", OK if h["cpu_peak"] < 90 else NOTE))
        if h["temp_peak"] is not None:
            rows.append(row("histTemp", f"{h['temp_peak']:.0f}°C", PROBLEM if h["temp_peak"] >= 80 else NOTE if h["temp_peak"] >= 70 else OK))
        rows.append(row("histMem", mb(h["mem_avg"] * 1e6)))
        if h["wr_avg"] is not None:
            rows.append(row("histWrite", f"{h['wr_avg']:.0f} MB/h", NOTE if h["wr_avg"] > 300 else OK))
        rows.append(row("histBusy", ", ".join(f"{k:02d}:00 ({v:.0f}%)" for k, v in h["busy_hours"])))
        for u, v in sorted(h["units"].items(), key=lambda kv: -(kv[1]["cpu_avg"] or 0))[:6]:
            if v["cpu_avg"] is not None:
                rows.append(row(u, f"{v['cpu_avg']:.1f}% / {v['cpu_peak']:.1f}%, {v['mem_peak']:.0f} MB", OK))
    groups.append({"key": "history", "rows": rows})
    return groups


def cell(text, lang):
    """A row's label or value: a key of TX is translated, anything else stays."""
    if isinstance(text, dict):
        return text.get(lang, text["en"])
    return tx(text, lang) if text in TX else text


def summary(rep, lang="en"):
    """[(label, value)]: the four numbers of the summary row."""
    s = rep["system"]
    mem = "-"
    if s.get("mem_total"):
        mem = f"{(s['mem_total'] - (s['mem_avail'] or 0)) / 1e6:.0f} / {s['mem_total'] / 1e6:.0f} MB"
    b = rep.get("boot")
    return [(tx("cpuIdle", lang), pct(s.get("cpu_idle"))), (tx("memUsed", lang), mem),
            (tx("temp", lang), "-" if s.get("temp") is None else f"{s['temp']:.0f}°C"),
            (tx("bootTime", lang), f"{b['total']:.1f} s" if b else "-")]


def service_table(rep, lang="en"):
    """Rows for the services table: header first. Only the services that exist."""
    out = [[tx("svc", lang), tx("state", lang), tx("cpuAvg", lang), tx("cpuNow", lang), tx("mem", lang), tx("write", lang), tx("restarts", lang)]]
    for s in rep["services"]:
        if s["state"] == "notfound":
            continue
        out.append([s["label"], tx(s["state"], lang), pct(s["cpu_avg"]), pct(s["cpu_live"]), mb(s["mem"]),
                    "-" if s["write_mbh"] is None else f"{s['write_mbh']:.1f} MB/h", str(s["restarts"])])
    return out


def table_levels(rep):
    return [s["level"] for s in rep["services"] if s["state"] != "notfound"]


def report_text(rep, lang="en"):
    L = []
    L.append(f"Armonico - {tx('report', lang)}")
    L.append(f"{tx('when', lang)}: {rep['when']}   {tx('machine', lang)}: {rep['kind']} ({rep['host']})   "
             f"{tx('uptime', lang)}: {rep['uptime'] / 3600:.1f} h   {tx('live', lang)}: {rep['live']} {tx('sec', lang)}")
    n = rep["counts"]
    L.append(tx("counts", lang, p=n[PROBLEM], w=n[NOTE], o=n[OK], s=n[SKIP]))
    L.append("")
    L.append(tx("g.summary", lang))
    for k, v in summary(rep, lang):
        L.append(f"  {k}: {v}")
    L.append("")
    L.append(tx("g.services", lang))
    tab = service_table(rep, lang)
    widths = [max(len(r[i]) for r in tab) for i in range(len(tab[0]))]
    for r in tab:
        L.append("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    mark = {OK: "OK  ", NOTE: "WARN", PROBLEM: "FAIL", SKIP: "SKIP"}
    for g in rep["groups"]:
        if g["key"] == "services":
            continue
        L.append("")
        L.append(tx("g." + g["key"], lang))
        for r in g["rows"]:
            d = f"  ({cell(r['detail'], lang)})" if r["detail"] else ""
            L.append(f"  {mark[r['level']]} {cell(r['label'], lang)}: {cell(r['value'], lang)}{d}")
    return "\n".join(L) + "\n"


def report_dir():
    d = checks_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.access(d, os.W_OK):
            return d
    except OSError:
        pass
    d = Path.home() / ".local" / "share" / "armonico" / "checks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_whole(folder, name, body):
    """name in folder, written whole (a temporary file with a name no one can guess, then renamed),
    through the folder's own handle and never through a link: `sudo armonico check --deep` writes
    into the service user's folder. A file made by root is given to that folder's owner."""
    import secrets
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
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
    finally:
        os.close(dfd)


def save_report(rep, lang="en", when=None):
    """deep-YYYY-MM-DD-HHMM.txt and .json, written whole; the newest KEEP_REPORTS stay. Returns the .txt path."""
    d = report_dir()
    stem = "deep-" + (when or datetime.now()).strftime("%Y-%m-%d-%H%M")
    for ext, body in ((".json", json.dumps(rep, ensure_ascii=False, indent=1, default=str)), (".txt", report_text(rep, lang))):
        write_whole(d, stem + ext, body)
    old = sorted(p for p in d.glob("deep-*.txt"))
    for p in old[:-KEEP_REPORTS]:
        for q in (p, p.with_suffix(".json")):
            try:
                q.unlink()
            except OSError:
                pass
    return d / (stem + ".txt")


def main(argv):
    what = argv[0] if argv else "check"
    if what == "sample":
        return 0 if sample() is not None else 1
    rep = collect(progress=None)
    print(report_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
