"""The deep check (device/deepcheck.py, device/ui/views_deep.py, `armonico check --deep`): every reading of the
machine replaced by a fake (systemctl, journalctl, vcgencmd, systemd-analyze, nmcli and the /proc and /sys files),
the averages over the uptime and over the live window, the sampler's rolling into the history, the report in both
languages, the CLI and the device screens."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers and DATA_DIR)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
import deepcheck as D                                # noqa: E402

pygame = T.pygame
MONO = 100000.0           # the fake monotonic clock, seconds


def show(unit, cpu_ns, mem=88_000_000, restarts=0, active="active", since=MONO - 7200, peak=120_000_000, io=5_000_000):
    return (f"Id={unit}.service\nLoadState=loaded\nActiveState={active}\nSubState=running\nNRestarts={restarts}\n"
            f"CPUUsageNSec={cpu_ns}\nMemoryCurrent={mem}\nMemoryPeak={peak}\nIOWriteBytes={io}\n"
            f"ActiveEnterTimestampMonotonic={int(since * 1e6)}\nMainPID=1\n\n")


def missing(unit):
    return f"Id={unit}.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nNRestarts=0\n\n"


class Fake:
    """A Raspberry Pi 4 as the check sees it. `calls` records what was run; `shows` counts the systemctl shows
    (the first is the start of the live window, the second its end)."""

    def __init__(self, tmp, pi=True, journal=True, throttled="0x0"):
        self.tmp, self.pi, self.journal, self.throttled = Path(tmp), pi, journal, throttled
        self.proc, self.sys = self.tmp / "proc", self.tmp / "sys"
        self.calls, self.shows = [], 0
        for d in ("proc", "sys/class/thermal/thermal_zone0", "sys/class/rfkill/rfkill0", "sys/class/rfkill/rfkill1", "proc/net", "proc/device-tree"):
            (self.tmp / d).mkdir(parents=True, exist_ok=True)
        (self.proc / "uptime").write_text("7200.50 25000.0\n")
        (self.proc / "loadavg").write_text("0.40 0.35 0.30 1/200 999\n")
        (self.proc / "meminfo").write_text("MemTotal: 3888000 kB\nMemAvailable: 3276000 kB\nSwapTotal: 524288 kB\nSwapFree: 524288 kB\n")
        (self.proc / "mounts").write_text("/dev/mmcblk0p2 / ext4 rw 0 0\n/dev/mmcblk0p1 /boot/firmware vfat rw 0 0\n")
        (self.proc / "stat").write_text("cpu 100 0 50 800 50 0 0 0\n")
        (self.proc / "diskstats").write_text("179 0 mmcblk0 1 0 2 0 3 0 4096 0 0 0 0\n")
        (self.proc / "net" / "wireless").write_text("Inter-| sta\n face | tus\n wlan0: 0000 55. -50. -256 0 0 0 0 0 0\n")
        if pi:
            (self.proc / "device-tree" / "model").write_text("Raspberry Pi 4 Model B Rev 1.4\0")
        (self.sys / "class/thermal/thermal_zone0/temp").write_text("51000\n")
        for n, kind in (("rfkill0", "wlan"), ("rfkill1", "bluetooth")):
            (self.sys / "class/rfkill" / n / "type").write_text(kind + "\n")
            (self.sys / "class/rfkill" / n / "soft").write_text("0\n")
            (self.sys / "class/rfkill" / n / "hard").write_text("0\n")
        self.cpu = iter([(100, 1000), (330, 2000)])             # 23% busy in the window
        self.disk = iter([40_000_000, 40_000_000 + 2_000_000])
        self.start = {}

    def run(self, cmd, timeout=10):
        self.calls.append(cmd)
        c = cmd[0]
        if c == "systemctl" and cmd[1] == "show":
            self.shows += 1
            late = self.shows > 1
            out = ""
            for arg in cmd:
                if not arg.endswith(".service"):
                    continue
                u = arg.removesuffix(".service")
                if u == "armonico-device":
                    out += show(u, 7200 * 10**9 // 50 + (3 * 10**9 if late else 0), restarts=0)       # 2% for 2 h
                elif u == "armonico-screen":
                    out += show(u, 7200 * 10**9 * 3 // 100 + (6 * 10**9 if late else 0), mem=141_000_000)
                elif u == "pipewire":
                    out += show(u, 7200 * 10**9 // 70, restarts=1)
                elif u == "mosquitto":
                    out += show(u, 10**9, mem=6_000_000)
                elif u == "armonico-bridge":
                    out += show(u, 10**9, active="failed")
                else:
                    out += missing(u)
            return 0, out
        if c == "systemctl" and cmd[1] == "list-units":
            return 0, "armonico-extra.service loaded active running Extra\narmonico-old.service loaded inactive dead Old\n"
        if c == "vcgencmd" and cmd[1] == "measure_temp":
            return 0, "temp=51.0'C\n"
        if c == "vcgencmd":
            return 0, f"throttled={self.throttled}\n"
        if c == "systemd-analyze" and cmd[1] == "time":
            return 0, "Startup finished in 5.012s (firmware) + 2.1s (loader) + 3.2s (kernel) + 1min 14.5s (userspace) = 1min 24.8s\n"
        if c == "systemd-analyze":
            return 0, "12.345s armonico-screen.service\n  3.100s armonico-device.service\n 30.5s NetworkManager-wait-online.service\n 2.0s dev-mmcblk0p2.device\n"
        if c == "journalctl" and "-g" in cmd:
            return 1, "" if not self.journal else ""
        if c == "journalctl":
            if not self.journal:
                return 1, ""
            return 0, "\n".join(json.dumps(e) for e in (
                {"_SYSTEMD_UNIT": "armonico-screen.service", "MESSAGE": "Tick failed: boom"},
                {"_SYSTEMD_UNIT": "armonico-screen.service", "MESSAGE": "again"},
                {"SYSLOG_IDENTIFIER": "kernel", "MESSAGE": "mmc0: error -110"})) + "\n"
        if c == "nmcli":
            return 0, "wlan0:wifi:connected:Home\\:Net\neth0:ethernet:unavailable:\nlo:loopback:unmanaged:\n"
        return 127, ""

    @contextlib.contextmanager
    def on(self):
        which = lambda c: "/usr/bin/" + c if (self.pi or c != "vcgencmd") else None
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(D, "_run", self.run))
            st.enter_context(mock.patch.object(D, "PROC", str(self.proc)))
            st.enter_context(mock.patch.object(D, "SYS", str(self.sys)))
            st.enter_context(mock.patch.object(D, "have", lambda c: which(c) is not None))
            st.enter_context(mock.patch.object(D, "systemd_up", lambda: True))
            st.enter_context(mock.patch.object(D, "now_mono", lambda: MONO))
            st.enter_context(mock.patch.object(D, "cpu_times", lambda: next(self.cpu, (330, 2000))))
            st.enter_context(mock.patch.object(D, "disk_written", lambda d: next(self.disk, 42_000_000)))
            st.enter_context(mock.patch.object(D.shutil, "disk_usage", lambda p: mock.Mock(total=29_000_000_000, free=19_000_000_000, used=10_000_000_000)))
            st.enter_context(mock.patch.object(D.platform, "node", lambda: "armonico-pi"))
            yield self


def collect(fake, live=10, **kw):
    with fake.on():
        return D.collect(live=live, sleep=lambda s: None, **kw)


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.tmp / "data")})
        p.start()
        self.addCleanup(p.stop)

    def rep(self, **kw):
        return collect(Fake(self.tmp / "m", **kw))

    def svc(self, rep, name):
        return next(s for s in rep["services"] if s["unit"] == name)

    def test_services_averaged_over_the_uptime_and_over_the_live_window(self):
        rep = self.rep()
        s = self.svc(rep, "armonico-device")
        self.assertAlmostEqual(s["cpu_avg"], 2.0 + 3 / 7200 * 100, delta=0.1, msg="systemd's CPUUsageNSec over the time up")
        self.assertAlmostEqual(s["cpu_live"], 30.0, delta=1, msg="3 s of CPU over a 10 s window")
        self.assertEqual(s["mem"], 88_000_000)
        self.assertEqual(s["mem_peak"], 120_000_000)
        self.assertAlmostEqual(s["write_mbh"], 2.5, delta=0.01)
        self.assertEqual(s["level"], D.OK)

    def test_restarts_failures_and_missing_services(self):
        rep = self.rep()
        self.assertEqual((self.svc(rep, "pipewire")["restarts"], self.svc(rep, "pipewire")["level"]), (1, D.NOTE))
        self.assertEqual(self.svc(rep, "armonico-bridge")["level"], D.PROBLEM)
        self.assertEqual(self.svc(rep, "bluetooth")["state"], "notfound")
        self.assertEqual(self.svc(rep, "bluetooth")["level"], D.SKIP)
        names = [s["unit"] for s in rep["services"]]
        self.assertIn("armonico-extra", names, "a running armonico unit that is not in the list is added")
        self.assertNotIn("armonico-old", names, "a stopped one is not")

    def test_a_program_without_a_unit_is_read_from_proc(self):
        fake = Fake(self.tmp / "m")
        d = fake.proc / "321"
        d.mkdir()
        (d / "comm").write_text("wireplumber\n")
        (d / "stat").write_text("321 (wireplumber) S " + " ".join(["0"] * 10) + " 500 500 0 0 20 0 1 0 100 1000 0\n")
        (d / "status").write_text("VmRSS:\t 31000 kB\n")
        rep = collect(fake)
        s = self.svc(rep, "wireplumber")
        self.assertEqual((s["state"], s["source"], s["mem"]), ("active", "process", 31_744_000))
        self.assertGreater(s["cpu_avg"], 0)

    def test_the_screen_program_in_its_login_session_is_counted_with_the_screen(self):
        """greetd's PAM login moves the screen program out of armonico-screen.service: it is found by its
        command line and added; when it is in the unit after all, it is not counted twice."""
        def with_screen(cgroup):
            fake = Fake(self.tmp / ("m" + cgroup[-5:]))
            d = fake.proc / "4321"
            d.mkdir()
            (d / "cmdline").write_text("/opt/armonico/device-venv/bin/python\0/opt/armonico/device/screen.py\0")
            (d / "cgroup").write_text(f"0::{cgroup}\n")
            (d / "stat").write_text("4321 (python) S " + " ".join(["0"] * 10) + " 500 500 0 0 20 0 1 0 100 1000 0\n")
            (d / "status").write_text("VmRSS:\t 100000 kB\n")
            (d / "io").write_text("write_bytes: 4096\n")
            return self.svc(collect(fake), "armonico-screen")
        base = self.svc(self.rep(), "armonico-screen")
        moved = with_screen("/user.slice/user-999.slice/session-3.scope")
        self.assertEqual(moved["mem"], base["mem"] + 102_400_000)
        self.assertGreater(moved["cpu_avg"], base["cpu_avg"])
        inside = with_screen("/system.slice/armonico-screen.service")
        self.assertEqual(inside["mem"], base["mem"])

    def test_known_system_messages_are_counted_apart_and_not_to_look_at(self):
        known = ('/usr/lib/udev/rules.d/90-alsa-restore.rules:22 GOTO="alsa_restore_std" has no matching label, ignoring.',
                 "/usr/lib/udev/rules.d/90-alsa-restore.rules:22 The line has no effect any more, dropping.",
                 "bgscan simple: Failed to enable signal strength monitoring")
        for m in known:
            self.assertTrue(D.KNOWN_JOURNAL.search(m), m)
        for m in ("other.rules:3 The line has no effect any more, dropping.", "Failed to start armonico-bridge.service"):
            self.assertFalse(D.KNOWN_JOURNAL.search(m), m)

    def test_a_journal_this_user_may_not_read_is_not_taken_for_no_errors(self):
        import grp
        with mock.patch.object(D.os, "geteuid", return_value=1000), mock.patch.object(D.os, "getgroups", return_value=[]):
            rep = self.rep()
        self.assertIsNone(rep["journal"])
        flat = {(g["key"], r["label"]): (r["value"], r["level"]) for g in rep["groups"] for r in g["rows"]}
        self.assertEqual(flat[("logs", "errors")], ("noAccess", D.SKIP))
        member = mock.Mock(gr_name="systemd-journal")
        with mock.patch.object(D.os, "geteuid", return_value=1000), mock.patch.object(D.os, "getgroups", return_value=[5]), \
                mock.patch.object(grp, "getgrgid", return_value=member):
            self.assertTrue(D.journal_readable())

    def test_temperature_reads_the_thermal_file_first_then_vcgencmd(self):
        fake = Fake(self.tmp / "m")
        f = fake.sys / "class/thermal/thermal_zone0/temp"
        f.write_text("48500\n")
        with fake.on():
            self.assertEqual(D.temperature(), 48.5)               # the file wins over vcgencmd's 51.0
            f.unlink()
            self.assertEqual(D.temperature(), 51.0)               # file missing: vcgencmd

    def test_system_numbers(self):
        rep = self.rep()
        sysd = rep["system"]
        self.assertAlmostEqual(sysd["cpu_busy"], 23.0, delta=0.1)
        self.assertAlmostEqual(sysd["cpu_idle"], 77.0, delta=0.1)
        self.assertEqual(sysd["temp"], 51.0)
        self.assertEqual(sysd["cores"], os.cpu_count() or 1)
        self.assertEqual(sysd["mem_total"], 3888000 * 1024)
        self.assertEqual(rep["kind"], "Raspberry Pi 4 Model B Rev 1.4")
        st = rep["storage"]
        self.assertEqual(st["device"], "mmcblk0")
        self.assertGreater(st["write_live_mbh"], 0)
        self.assertIn("write_avg_mbh", st)

    def test_throttling_and_undervoltage(self):
        self.assertEqual(self.rep(throttled="0x0")["system"]["throttled"]["under_now"], False)
        rep = self.rep(throttled="0x50005")
        th = rep["system"]["throttled"]
        self.assertTrue(th["under_now"] and th["throttled_now"] and th["under_past"])
        rows = {r["value"]: r["level"] for g in rep["groups"] if g["key"] == "system" for r in g["rows"] if r["label"] == "throttle"}
        self.assertEqual(rows["thUnder"], D.PROBLEM)
        past = self.rep(throttled="0x50000")
        rows = {r["value"]: r["level"] for g in past["groups"] if g["key"] == "system" for r in g["rows"] if r["label"] == "throttle"}
        self.assertEqual(rows, {"thUnderPast": D.NOTE, "thCapPast": D.NOTE})

    def test_boot_time_by_stage_and_the_slow_units(self):
        b = self.rep()["boot"]
        self.assertAlmostEqual(b["total"], 84.8, delta=0.1)
        self.assertAlmostEqual(b["stages"]["userspace"], 74.5)
        self.assertEqual([u for u, _ in b["ours"]], ["armonico-screen.service", "armonico-device.service"])
        self.assertEqual(b["slowest"][0][0], "NetworkManager-wait-online.service")
        self.assertEqual(D.parse_duration("1min 2.5s"), 62.5)
        self.assertAlmostEqual(D.parse_duration("345ms"), 0.345)

    def test_journal_errors_by_unit_and_network_and_radios(self):
        rep = self.rep()
        self.assertEqual(rep["journal"]["armonico-screen.service"], [2, "again"])
        self.assertEqual(rep["journal"]["kernel"][0], 1)
        net = rep["network"]
        self.assertEqual([d["dev"] for d in net["devices"]], ["wlan0", "eth0"], "the loopback is not listed")
        self.assertEqual(net["devices"][0]["conn"], "Home:Net")
        self.assertEqual({r["type"] for r in net["radios"]}, {"wlan", "bluetooth"})
        self.assertEqual(net["wifi_signal"], 55)

    def test_what_is_missing_is_skipped_not_an_error(self):
        rep = self.rep(pi=False, journal=False)
        self.assertIsNone(rep["system"]["throttled"])
        self.assertIsNone(rep["journal"])
        flat = {(g["key"], r["label"]): r["level"] for g in rep["groups"] for r in g["rows"]}
        self.assertEqual(flat[("system", "throttle")], D.SKIP)
        self.assertEqual(flat[("logs", "errors")], D.SKIP)
        self.assertEqual(sum(rep["counts"].values()), sum(len(g["rows"]) for g in rep["groups"]))

    def test_progress_reports_every_step(self):
        steps = []
        collect(Fake(self.tmp / "m"), progress=lambda f, s: steps.append((f, s)))
        names = [s for _, s in steps]
        self.assertEqual(names[0], "services")
        self.assertIn("live", names)
        self.assertEqual(names[-1], "history")
        self.assertEqual(steps[-1][0], 1.0)
        self.assertEqual([f for f, _ in steps], sorted(f for f, _ in steps))


class Sampler(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / "run"
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.tmp / "data")})
        p.start()
        self.addCleanup(p.stop)
        self.fake = Fake(self.tmp / "m")
        self.n = 0

    def take(self, t, busy, total, disk, cpu_ns, mono):
        self.fake.cpu = iter([(busy, total)] * 5)
        self.fake.disk = iter([disk] * 5)
        self.fake.shows = 0
        old = self.fake.run

        def run(cmd, timeout=10):
            if cmd[0] == "systemctl" and cmd[1] == "show":
                return 0, show("armonico-device", cpu_ns) + show("mosquitto", cpu_ns // 10)
            return old(cmd, timeout)
        with self.fake.on(), mock.patch.object(D, "_run", run), mock.patch.object(D, "now_mono", lambda: mono), \
                mock.patch.object(D, "discovered_units", lambda: []):
            return D.sample(run_dir=self.run, now=t)

    def test_samples_stay_in_ram_until_an_hour_ends_then_one_line_is_written(self):
        h0 = 1_800_000_000 // 3600 * 3600
        for i in range(10):
            rolled = self.take(h0 + 60 + i * 300, 100 + 50 * i, 1000 + 200 * i, 40_000_000 + i * 1_000_000, i * 6 * 10**9, 1000 + i * 300)
            self.assertEqual(rolled, 0)
        self.assertFalse(D.history_file().exists(), "nothing is written to disk within the hour")
        self.assertTrue((self.run / "samples.jsonl").exists())
        rolled = self.take(h0 + 3600 + 30, 100 + 50 * 12, 1000 + 200 * 12, 60_000_000, 12 * 6 * 10**9, 1000 + 12 * 300)
        self.assertEqual(rolled, 1)
        hist = json.loads(D.history_file().read_text())["hours"]
        self.assertEqual(len(hist), 1)
        row = hist[0]
        self.assertAlmostEqual(row["cpu"], 25.0, delta=0.5)
        self.assertAlmostEqual(row["units"]["armonico-device"]["cpu"], 2.0, delta=0.2)
        self.assertEqual(row["units"]["armonico-device"]["mem"], 88.0)
        self.assertIn("wr", row)
        # the RAM file starts the next hour with the last sample
        self.assertLessEqual(len((self.run / "samples.jsonl").read_text().splitlines()), 3)

    def test_seven_days_are_kept_and_a_reboot_starts_the_comparison_again(self):
        h0 = 1_800_000_000 // 3600 * 3600
        old = [{"h": h0 - 9 * 86400 + i * 3600, "cpu": 5.0, "mem": 600.0, "temp": 50.0, "units": {}} for i in range(3)]
        recent = [{"h": h0 - 3600, "cpu": 7.0, "mem": 600.0, "temp": 55.0, "units": {}}]
        D.history_file().parent.mkdir(parents=True, exist_ok=True)
        D.history_file().write_text(json.dumps({"v": 1, "hours": old + recent}))
        self.take(h0 + 100, 100, 1000, 1, 10**9, 1000)
        self.take(h0 + 400, 200, 1500, 2, 2 * 10**9, 1300)
        self.take(h0 + 3700, 300, 2000, 3, 3 * 10**9, 1600)
        hours = [x["h"] for x in D.load_history()]
        self.assertNotIn(old[0]["h"], hours, "older than 7 days: dropped")
        self.assertIn(recent[0]["h"], hours)
        # a reboot: the counters began again, the old samples are not compared with the new
        self.take(h0 + 7300, 5, 50, 1, 10**8, 50)
        self.assertEqual(len((self.run / "samples.jsonl").read_text().splitlines()), 1)

    def test_the_history_summary_shows_averages_peaks_and_busy_hours(self):
        hours = [{"h": 1_800_000_000 // 3600 * 3600 + i * 3600, "cpu": c, "mem": 600.0, "temp": 50.0 + i, "wr": 10.0,
                  "units": {"a": {"cpu": c / 2, "mem": 100.0 + i}}} for i, c in enumerate((5.0, 40.0, 10.0))]
        s = D.history_summary(hours)
        self.assertAlmostEqual(s["cpu_avg"], 18.333, delta=0.01)
        self.assertEqual(s["cpu_peak"], 40.0)
        self.assertEqual(s["temp_peak"], 52.0)
        self.assertEqual(s["busy_hours"][0][1], 40.0)
        self.assertEqual(s["units"]["a"]["mem_peak"], 102.0)
        self.assertIsNone(D.history_summary([]))


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.tmp / "data")})
        p.start()
        self.addCleanup(p.stop)
        self.rep = collect(Fake(self.tmp / "m"))

    def test_the_text_in_both_languages_has_every_group(self):
        en, he = D.report_text(self.rep, "en"), D.report_text(self.rep, "he")
        for text, words in ((en, ("Deep check report", "Services", "Boot stages", "System resources", "Audio", "Network and radios", "Storage", "Logs", "armonico-device")),
                            (he, ("דוח בחינה מעמיקה", "שירותים", "שלבי העלייה", "משאבי המערכת", "שמע", "רשת ורדיו", "אחסון", "יומנים", "armonico-device"))):
            for w in words:
                self.assertIn(w, text)
        self.assertNotIn("בלבד", he)
        self.assertNotIn("{", en + he, "no placeholder left")

    def test_save_keeps_a_text_and_a_json_and_the_newest_ten(self):
        from datetime import datetime, timedelta
        base = datetime(2026, 11, 1, 20, 0)
        for i in range(12):
            D.save_report(self.rep, "en", base + timedelta(minutes=i))
        d = D.checks_dir()
        self.assertEqual(len(list(d.glob("deep-*.txt"))), 10)
        self.assertEqual(len(list(d.glob("deep-*.json"))), 10)
        newest = d / "deep-2026-11-01-2011.json"
        self.assertEqual(json.loads(newest.read_text())["v"], 1)
        self.assertFalse(list(d.glob(".*.tmp")))

    def test_the_summary_and_table(self):
        s = {k: v for k, v, x in D.summary(self.rep, "en")}
        self.assertEqual(s["Temperature"], "51°C")
        self.assertIn("MB", s["Memory in use"])
        self.assertEqual(s["Boot time"], "84.8 s")
        tab = D.service_table(self.rep, "en")
        self.assertEqual(tab[0][0], "Service")
        self.assertNotIn("bluetooth", [r[0] for r in tab], "a service that is not installed has no row")
        self.assertEqual(len(tab) - 1, len(D.table_levels(self.rep)))

    def test_every_orange_or_red_service_row_says_why(self):
        whys = D.table_whys(self.rep, "en")
        self.assertEqual(len(whys), len(D.table_levels(self.rep)))
        for lvl, why in zip(D.table_levels(self.rep), whys):
            if lvl in (D.NOTE, D.PROBLEM):
                self.assertTrue(why, "a marked row has its reason")
            elif lvl == D.OK:
                self.assertEqual(why, "")
        text = D.report_text(self.rep, "he")
        self.assertNotIn("whyCpu", text, "the reasons are texts, never keys")


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        spec = importlib.util.spec_from_file_location("armonico_cli_deep", ROOT / "cli" / "armonico.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.tmp / "data")})
        p.start()
        self.addCleanup(p.stop)

    def test_check_deep_prints_the_report_and_saves_text_and_json(self):
        fake = Fake(self.tmp / "m")
        out = io.StringIO()
        with fake.on(), mock.patch.object(D, "LIVE_SECONDS", 2), mock.patch.object(D.time, "sleep", lambda s: None), \
                mock.patch.object(self.cli, "settings", return_value={"DATA_DIR": str(self.tmp / "data")}), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ex:        # a service is failed in the fake: exit 1
                self.cli.cmd_check(["--deep"])
        self.assertEqual(ex.exception.code, 1)
        text = out.getvalue()
        self.assertIn("Deep check", text)
        self.assertIn("armonico-device", text)
        self.assertIn("Report:", text)
        files = sorted(p.name for p in (self.tmp / "data" / "checks").glob("deep-*"))
        self.assertEqual([Path(f).suffix for f in files], [".json", ".txt"])

    def test_plain_check_is_untouched(self):
        src = (ROOT / "cli" / "armonico.py").read_text()
        self.assertIn("def cmd_check(a)", src)
        self.assertIn("Full check", src)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.tmp / "data")})
        p.start()
        self.addCleanup(p.stop)
        self.rep = collect(Fake(self.tmp / "m"))

    def app(self, lang="en", size=(800, 480)):
        return T.make_app(self.mod, size, lang)

    def wait(self, app, phase):
        end = time.time() + 20
        while app.dc["phase"] != phase and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(app.dc["phase"], phase)

    def test_the_button_is_in_the_device_test_window(self):
        app = self.app()
        app.open_page("test")
        cells = app.pages()["test"] if hasattr(app, "pages") else None
        text = json.dumps([c[0] for c in (cells or [])]) if cells else ""
        self.assertTrue(not cells or "Deep check" in text)
        src = (ROOT / "device" / "ui" / "views_settings.py").read_text()
        self.assertIn("self.open_deep", src)

    def test_open_measure_and_show_the_result_in_both_languages_and_sizes(self):
        for size in ((800, 480), (1024, 600), (480, 320)):
            for lang in ("en", "he"):
                app = self.app(lang, size)
                app.open_deep()
                with self.subTest(size=size, lang=lang):
                    app.draw()                                   # the opening
                    self.assertEqual(app.view, "deep")
                    fake = Fake(self.tmp / "m2")
                    with fake.on(), mock.patch.object(D, "LIVE_SECONDS", 2), mock.patch.object(D.time, "sleep", lambda s: None):
                        app.dc_start()
                        app.draw()                               # the progress
                        self.wait(app, "result")
                    app.draw()
                    self.assertTrue(app.dc["file"].exists())
                    first = app.dc["first"]
                    app.bar_items[1][2]()                        # ▼
                    app.draw()
                    self.assertGreater(app.dc["first"], first)
                    app.bar_items[2][2]()                        # ▲
                    self.assertEqual(app.dc["first"], first)

    def test_save_to_usb_asks_root_for_the_two_files(self):
        app = self.app()
        app.open_deep()
        app.dc.update(rep=self.rep, phase="result")
        app.dc["file"] = D.save_report(self.rep, "en")
        seen = {}

        def req(action, timeout=10, **kw):
            seen.update(action=action, **kw)
            return True, json.dumps({"copied": 2, "to": "/media/armonico/USB/Armonico reports"})
        from ui import system
        with mock.patch.object(system, "request", req):
            app.dc_save()
            end = time.time() + 5
            while app.dc["saving"] and time.time() < end:
                time.sleep(0.05)
        self.assertEqual(seen["action"], "report-usb")
        self.assertEqual(len(seen["files"]), 2)
        self.assertTrue(all(f.startswith("deep-") for f in seen["files"]))

    def test_leaving_drops_a_running_measurement(self):
        app = self.app()
        app.open_deep()
        gen = app.dc["gen"]
        app.dc_back()
        self.assertGreater(app.dc["gen"], gen)
        self.assertEqual(app.view, "settings")


if __name__ == "__main__":
    unittest.main()


class RootLogs(unittest.TestCase):
    """The device's own user cannot read the journal or the kernel log: the root unit reads them for the checks."""

    def test_the_journal_comes_from_the_root_unit_when_this_user_cannot_read_it(self):
        import json as _json
        asked = []

        def request(action, timeout=0):
            asked.append(action)
            return True, _json.dumps({"kernel": [], "journal": {"kernel": [3, "Buffer I/O error on dev sda1"]}})
        self.assertEqual(D.root_journal_errors(request), {"kernel": [3, "Buffer I/O error on dev sda1"]})
        self.assertEqual(asked, ["logs-read"])
        self.assertIsNone(D.root_journal_errors(None), "no way to ask: not readable, as before")
        self.assertIsNone(D.root_journal_errors(lambda a, timeout=0: (False, "no-root")))
        self.assertIsNone(D.root_journal_errors(lambda a, timeout=0: (True, "not json")))

    def test_the_kernel_log_comes_from_the_root_unit_too(self):
        import json as _json
        import fullcheck as F
        ok = lambda a, timeout=0: (True, _json.dumps({"kernel": ["Buffer I/O error on dev sda1"], "journal": None}))
        self.assertEqual(F.root_kernel_errors(ok), ["Buffer I/O error on dev sda1"])
        self.assertIsNone(F.root_kernel_errors(None))
        self.assertIsNone(F.root_kernel_errors(lambda a, timeout=0: (False, "x")))
