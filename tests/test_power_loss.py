"""The power-loss recovery (device/powerloss.py, device/ui/views_power.py): the running marker, the record of
where the screen was, the repairs (settings, learning data, songs, a cut-off screen recording), the file system
check's result, the root actions, and the screen that works through them."""
import importlib.util
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
import powerloss as PL                               # noqa: E402

pygame = T.pygame
MIDI = (b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + (96).to_bytes(2, "big")
        + b"MTrk" + (4).to_bytes(4, "big") + b"\x00\xff\x2f\x00")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        (self.data / "device").mkdir(parents=True)
        self.boot = self.tmp / "boot_id"
        self.boot.write_text("boot-1\n")
        for p in (mock.patch.dict(os.environ, {"DATA_DIR": str(self.data)}),
                  mock.patch.object(PL, "BOOT_ID_FILE", str(self.boot))):
            p.start()
            self.addCleanup(p.stop)
        self.dev = self.data / "device"

    def put(self, rel, text):
        f = self.data / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f


class Marker(Base):
    def test_first_clean_and_unclean_boots(self):
        self.assertEqual(PL.mark_boot("1.0"), "first")
        self.assertTrue((self.dev / "running.json").exists())
        self.assertIsNone(PL.pending())
        PL.mark_clean()                                           # a clean shutdown removes the marker
        self.assertFalse((self.dev / "running.json").exists())
        self.boot.write_text("boot-2\n")
        PL.LastWriter().note("settings", "", "")                  # a record exists now
        self.assertEqual(PL.mark_boot("1.0"), "clean")
        self.boot.write_text("boot-3\n")                          # the power went: no clean shutdown in between
        self.assertEqual(PL.mark_boot("1.0"), "unclean")
        info = PL.pending()
        self.assertEqual(info["last"]["view"], "settings")
        self.assertEqual(info["running"]["boot"], "boot-2")

    def test_the_boot_puts_broken_settings_right_before_the_services_start(self):
        """The device's own settings broken by the cut: put back at boot, so the screen opens on them (not the
        setup wizard) and no service writes over the broken file first; the screen is told what was done."""
        self.put("device/settings.json", '{"setup_done": true, "lang": "he"}')
        self.put("lessons.json", '{"a": 1}')
        PL.mark_boot("1.0")                                        # the last good copies made at this boot
        self.put("device/settings.json", "")                       # the power went in the middle of a write
        self.boot.write_text("boot-2\n")
        self.assertEqual(PL.mark_boot("1.0"), "unclean")
        self.assertEqual(json.loads((self.dev / "settings.json").read_text()), {"setup_done": True, "lang": "he"})
        rep = PL.pending()["repaired"]
        self.assertEqual([x["state"] for x in rep["config"]], ["restored"])
        self.assertEqual(rep["learning"], [], "what was fine is not listed")

    def test_the_same_boot_is_not_a_power_loss(self):
        PL.mark_boot("1.0")
        self.assertEqual(PL.mark_boot("1.0"), "clean", "the marker of this very boot: a service restart")
        self.assertIsNone(PL.pending())

    def test_a_pending_record_is_not_overwritten_by_a_second_loss(self):
        PL.mark_boot("1.0")
        self.boot.write_text("boot-2\n")
        PL.LastWriter().note("songs", "", "Main")
        PL.mark_boot("1.0")
        first = PL.pending()
        self.boot.write_text("boot-3\n")
        PL.mark_boot("1.0")
        self.assertEqual(PL.pending()["at"], first["at"])
        PL.clear_pending()
        self.assertIsNone(PL.pending())

    def test_a_cut_off_recording_is_put_aside_before_the_recorder_deletes_it(self):
        rec = self.dev / "recordings"
        rec.mkdir()
        (rec / ".rec-ab12cd34-video.mkv").write_bytes(b"x" * 10)
        (rec / ".rec-ab12cd34-device-1.mka").write_bytes(b"y" * 10)
        (rec / "last.log").write_text("ffmpeg ...\n" + str(rec / "armonico-2026-11-01-210000.mkv") + "\n")
        PL.mark_boot("1.0")
        self.boot.write_text("boot-2\n")
        PL.mark_boot("1.0")
        self.assertEqual(sorted(p.name for p in rec.glob(".rec-*")), [], "nothing left for clean_stale to delete")
        self.assertTrue((rec / ".interrupted" / ".rec-ab12cd34-video.mkv").exists())
        # and the recorder's own clean_stale leaves the stash alone
        sys.path.insert(0, str(ROOT / "device"))
        from ui import recorder
        recorder.clean_stale(rec)
        self.assertTrue((rec / ".interrupted" / ".rec-ab12cd34-video.mkv").exists())


class Record(Base):
    def test_written_only_on_a_change_with_a_heartbeat_and_rare_fsync(self):
        clock = [1000.0]
        w = PL.LastWriter(self.dev / "last.json", clock=lambda: clock[0])
        with mock.patch.object(os, "fsync") as fs:
            self.assertTrue(w.note("rest"))
            self.assertFalse(w.note("rest"), "no change, no write")
            clock[0] += 2
            self.assertFalse(w.note("songs"), "a change within 5 seconds waits")
            clock[0] += 4
            self.assertTrue(w.note("songs"))
            for _ in range(20):
                clock[0] += 6
                w.note("free" if _ % 2 else "songs", "", "")
            clock[0] += 400
            self.assertTrue(w.note("songs"), "the heartbeat")
            self.assertLess(fs.call_count, w.writes, "fsync far less often than the writes")
            self.assertGreaterEqual(fs.call_count, 1)
        d = json.loads((self.dev / "last.json").read_text())
        self.assertEqual(d["view"], "songs")
        self.assertIn("ts", d)


class Files(Base):
    def setUp(self):
        super().setUp()
        self.put("device/settings.json", '{"lang": "he"}')
        self.put("profiles.json", '{"list": []}')
        self.put("lessons.json", '{"a": 1}')
        self.put("history.json", '[1, 2]')
        self.put("profiles/p0a1b2c3d/learner.json", '{"x": 1}')
        self.put("shortcuts.conf", "a=b\n")

    def test_groups_and_checking(self):
        files = {p.name: g for p, k, g in PL.config_files()}
        self.assertEqual(files["settings.json"], "config")
        self.assertEqual(files["learner.json"], "learning")
        self.assertTrue(all(x["state"] == "ok" for x in PL.check_group("config")))
        self.put("history.json", '{"not": "a list"}')
        self.assertFalse(PL.check_file(self.data / "history.json", "json-list")[0])
        self.put("lessons.json", "")
        self.assertEqual(PL.check_file(self.data / "lessons.json", "json-dict")[1], "empty")
        (self.data / "lessons.json").write_bytes(b"\0" * 20)
        self.assertFalse(PL.check_file(self.data / "lessons.json", "json-dict")[0])

    def test_snapshot_copies_only_good_changed_files(self):
        self.assertEqual(PL.snapshot(), 6)
        self.assertEqual(PL.snapshot(), 0, "nothing changed: nothing written")
        self.put("lessons.json", '{"a": 2}')
        self.put("history.json", "{broken")
        self.assertEqual(PL.snapshot(), 1, "the changed good file only; a broken one is never kept as good")
        good = PL.good_path(self.data / "history.json")
        self.assertEqual(json.loads(good.read_text()), [1, 2])

    def test_a_broken_file_is_put_back_from_the_last_good_copy(self):
        PL.snapshot()
        self.put("device/settings.json", '{"lang": "he", "vol')               # cut off by the power
        res = PL.repair("config")
        by = {Path(r["path"]).name: r["state"] for r in res}
        self.assertEqual(by["settings.json"], "restored")
        self.assertEqual(json.loads((self.dev / "settings.json").read_text()), {"lang": "he"})
        self.assertTrue((self.dev / "settings.json.broken").exists(), "the damaged file is kept for a look")

    def test_a_broken_file_with_no_copy_is_set_aside(self):
        self.put("lessons.json", "{broken")
        res = PL.repair("learning")
        self.assertEqual({Path(r["path"]).name: r["state"] for r in res}["lessons.json"], "removed")
        self.assertFalse((self.data / "lessons.json").exists())

    def test_an_alarm_stays_off_a_clean_file_is_never_touched(self):
        before = (self.data / "profiles.json").stat().st_mtime_ns
        PL.repair("learning")
        self.assertEqual((self.data / "profiles.json").stat().st_mtime_ns, before)


class Songs(Base):
    def test_midi_files_are_validated_and_a_damaged_one_moved_aside(self):
        songs = self.data / "songs"
        songs.mkdir()
        (songs / "good.mid").write_bytes(MIDI)
        (songs / "cut.mid").write_bytes(MIDI[:-3])
        (songs / "junk.mid").write_bytes(b"hello")
        (songs / "readme.txt").write_text("not a song")
        n, bad = PL.check_songs(songs)
        self.assertEqual((n, sorted(bad)), (3, ["cut.mid", "junk.mid"]))
        self.assertTrue((songs / "good.mid").exists())
        self.assertTrue((songs / "_damaged" / "cut.mid").exists())
        self.assertEqual(PL.check_songs(songs), (1, []))


class Recording(Base):
    def stash(self, with_sound=True, video_bytes=10):
        rec = self.dev / "recordings"
        box = rec / ".interrupted"
        box.mkdir(parents=True)
        (box / ".rec-ab12cd34-video.mkv").write_bytes(b"v" * video_bytes)
        if with_sound:
            (box / ".rec-ab12cd34-device-1.mka").write_bytes(b"a" * 10)
        (box / "last.log").write_text("cmd\n" + str(rec / "armonico-2026-11-01-210000.mkv") + "\n")
        return rec

    def test_the_parts_are_put_together_into_the_recordings_own_file(self):
        rec = self.stash()
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "ffprobe":
                return subprocess.CompletedProcess(cmd, 0, "720.5\n", "")
            Path(cmd[-1]).write_bytes(b"muxed")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def mux(video, t0, sounds, out):
            return ["ffmpeg", "-i", str(video), str(out)]
        res = PL.recover_recordings(rec, mux, lambda f: 100.0, run=run)
        self.assertEqual([r["state"] for r in res], ["recovered"])
        self.assertEqual(res[0]["file"], "armonico-2026-11-01-210000.mkv")
        self.assertAlmostEqual(res[0]["seconds"], 720.5)
        self.assertTrue((rec / "armonico-2026-11-01-210000.mkv").exists())
        self.assertFalse((rec / ".interrupted").exists(), "the parts and the stash folder are gone")

    def test_a_recording_with_no_picture_is_cleaned_up(self):
        rec = self.stash(video_bytes=0)
        res = PL.recover_recordings(rec, lambda *a: [], lambda f: None, run=mock.Mock())
        self.assertEqual([r["state"] for r in res], ["cleaned"])
        self.assertFalse((rec / ".interrupted").exists())

    def test_a_failed_put_together_keeps_the_parts(self):
        rec = self.stash(with_sound=False)
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "bad"))
        res = PL.recover_recordings(rec, lambda *a: [], lambda f: None, run=run)
        self.assertEqual([r["state"] for r in res], ["failed"])
        self.assertTrue((rec / ".interrupted" / ".rec-ab12cd34-video.mkv").exists())


class Fsck(Base):
    def test_what_the_boot_check_said(self):
        self.assertEqual(PL.fsck_summary("rootfs: clean, 201133/3820 files, 1/2 blocks\n")[0], "clean")
        self.assertEqual(PL.fsck_summary("rootfs: recovering journal\nrootfs: clean, 5/6")[0], "repaired")
        self.assertEqual(PL.fsck_summary("Fixing FAT dirty bit\n")[0], "repaired")
        self.assertEqual(PL.fsck_summary("rootfs: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.")[0], "errors")
        self.assertEqual(PL.fsck_summary("")[0], "unknown")

    def test_fsck_repair_on_the_kernel_command_line(self):
        f = self.tmp / "cmdline.txt"
        f.write_text("console=tty1 root=PARTUUID=1 rootfstype=ext4 fsck.repair=yes rootwait\n")
        self.assertEqual(PL.ensure_fsck_repair(str(f)), "present")
        f.write_text("console=tty1 root=PARTUUID=1 rootfstype=ext4 rootwait\n")
        self.assertEqual(PL.ensure_fsck_repair(str(f)), "added")
        self.assertEqual(f.read_text(), "console=tty1 root=PARTUUID=1 rootfstype=ext4 rootwait fsck.repair=yes\n", "one line")
        self.assertTrue((self.tmp / "cmdline.txt.armonico-bak").exists())
        f.write_text("root=x fsck.repair=no rootwait\n")
        self.assertEqual(PL.ensure_fsck_repair(str(f)), "added")
        self.assertEqual(f.read_text().count("fsck.repair"), 1)
        self.assertEqual(PL.ensure_fsck_repair(str(self.tmp / "none")), "none")

    def test_the_command_line_entry_points(self):
        self.assertEqual(PL.main(["boot"]), 0)
        self.assertTrue((self.dev / "running.json").exists())
        self.assertEqual(PL.main(["clean"]), 0)
        self.assertFalse((self.dev / "running.json").exists())
        self.assertEqual(PL.main([]), 2)


class RootActions(Base):
    def setUp(self):
        super().setUp()
        self.ra = load("ra_power", "device/root_actions.py")
        self.out = []
        for name, val in (("DATA_DIR", self.data),):
            p = mock.patch.object(self.ra, name, val)
            p.start()
            self.addCleanup(p.stop)

    def p(self, step, state="running", msg=""):
        self.out.append((state, msg))

    def test_the_actions_exist(self):
        for a in ("boot-check", "service-start", "report-usb"):
            self.assertIn(a, self.ra.ACTIONS)

    def test_boot_check_reads_the_journal_and_makes_sure_of_fsck_repair(self):
        cmd = self.tmp / "cmdline.txt"
        cmd.write_text("root=x rootwait\n")
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "rootfs: recovering journal\nrootfs: clean, 1/2\n", ""))
        with mock.patch.object(self.ra, "run", run), mock.patch.object(self.ra, "BOOT_CMDLINE", str(cmd)):
            self.ra.do_boot_check(self.p, {})
        d = json.loads(self.out[-1][1])
        self.assertEqual((d["fsck"], d["cmdline"]), ("repaired", "added"))
        self.assertIn("fsck.repair=yes", cmd.read_text())

    def test_service_start_takes_only_the_known_units(self):
        with self.assertRaises(ValueError):
            self.ra.do_service_start(self.p, {"units": ["ssh"]})
        with self.assertRaises(ValueError):
            self.ra.do_service_start(self.p, {"units": [], "restart": ["sshd"]})
        calls = []

        def run(*cmd, timeout=0):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if cmd[1] != "is-active" or "--quiet" not in cmd else 3, "active\n", "")
        with mock.patch.object(self.ra, "run", run):
            self.ra.do_service_start(self.p, {"units": ["armonico-bridge"], "restart": ["armonico-lessons"]})
        self.assertIn(("systemctl", "restart", "armonico-lessons"), calls)
        self.assertIn(("systemctl", "start", "armonico-bridge"), calls)
        self.assertEqual(json.loads(self.out[-1][1]), {"armonico-bridge": "active"})

    def test_report_usb_takes_only_deep_report_names(self):
        for bad in (["../etc/passwd"], ["deep-2026-11-01-2114.sh"], [], "x"):
            with self.assertRaises((ValueError,)):
                self.ra.do_report_usb(self.p, {"files": bad})


class Installer(unittest.TestCase):
    def test_the_units_and_files_are_installed(self):
        text = (ROOT / "install.sh").read_text()
        for f in ("device/powerloss.py", "device/deepcheck.py", "system/boot-mark.service", "system/sampler.service", "system/sampler.timer"):
            self.assertIn(f, text)
            self.assertTrue((ROOT / f).is_file(), f)
        self.assertIn('system/*.timer', text, "timers are installed with the other units")
        self.assertIn('enable --now "$APP-sampler.timer"', text)
        self.assertIn('enable --now "$APP-boot-mark.service"', text)
        self.assertIn("ensure-fsck", text)
        mark = (ROOT / "system" / "boot-mark.service").read_text()
        self.assertIn("powerloss.py boot", mark)
        self.assertIn("powerloss.py clean", mark)
        self.assertIn("Before=@APP@-device.service @APP@-screen.service", mark)
        self.assertIn("OnUnitActiveSec=5min", (ROOT / "system" / "sampler.timer").read_text())


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        (self.data / "device" / "recordings").mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"DATA_DIR": str(self.data), "SONGS_DIR": str(self.data / "songs")})
        p.start()
        self.addCleanup(p.stop)
        for rel, text in (("device/settings.json", '{"lang": "he"}'), ("lessons.json", '{"a": 1}')):
            f = self.data / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        self.info = {"at": time.time(), "running": {"boot": "x"},
                     "last": {"view": "settings", "lesson": "ode_to_joy", "profile": "Main", "ts": time.time() - 90}}

    def app(self, lang="en", size=(800, 480)):
        app = T.make_app(self.mod, size, lang)
        app.background_on = True
        return app

    def run_recovery(self, app, request=None):
        from ui import system
        req = request or (lambda action, timeout=10, **kw: (True, json.dumps({"fsck": "clean", "line": "", "cmdline": "present"})))
        fc = mock.patch("ui.views_power.fullcheck.run_all", return_value=[])
        # the services step asks systemd about the real machine: here none of the units is set up,
        # so a Mosquitto the test machine happens to have enabled is not taken for one that is down
        real = subprocess.run

        def run(cmd, *a, **kw):
            if list(cmd[:2]) == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return real(cmd, *a, **kw)
        svc = mock.patch("subprocess.run", side_effect=run)
        with mock.patch.object(system, "request", req), fc, svc:
            app.pl_begin(self.info, pace=0)
            end = time.time() + 20
            while app.pl["phase"] != "done" and time.time() < end:
                time.sleep(0.05)
        return app.pl

    def test_every_step_runs_without_a_key_and_the_marker_is_cleared(self):
        PL.write_json(self.data / "device" / "unclean.json", self.info)
        app = self.app()
        p = self.run_recovery(app)
        self.assertEqual(p["phase"], "done")
        self.assertEqual([r["state"] for r in p["rows"]], ["done"] * 7)
        self.assertEqual({r["level"] for r in p["rows"]}, {0})
        self.assertIsNone(PL.pending(), "handled: the next boot is not taken for another loss")

    def test_a_broken_setting_is_restored_and_said_on_the_screen(self):
        PL.snapshot()
        (self.data / "device" / "settings.json").write_text('{"lang": ')
        app = self.app()
        p = self.run_recovery(app)
        row = next(r for r in p["rows"] if r["key"] == "cfg")
        self.assertEqual(row["text"][0], "pl.r.cfgRestored")
        self.assertEqual(json.loads((self.data / "device" / "settings.json").read_text()), {"lang": "he"})
        self.assertTrue(p["restored"], "the lesson engine is restarted to read its files again")

    def test_what_the_boot_put_right_is_said_and_needs_no_restart(self):
        path = str(self.data / "device" / "settings.json")
        self.info["repaired"] = {"config": [{"path": path, "state": "restored", "why": "empty"}], "learning": []}
        app = self.app()
        p = self.run_recovery(app)
        row = next(r for r in p["rows"] if r["key"] == "cfg")
        self.assertEqual(row["text"], ("pl.r.cfgRestored", {"n": 0, "r": 1}), "the file counted once, as put back")
        self.assertFalse(p["restored"], "put right before the services started: nothing to restart")

    def test_a_file_system_the_boot_could_not_repair_stays_on_screen(self):
        app = self.app()
        p = self.run_recovery(app, lambda action, timeout=10, **kw: (True, json.dumps({"fsck": "errors"})))
        self.assertEqual(p["rows"][0]["level"], 2)
        app.draw()
        self.assertTrue(any(i[1] for i in app.bar_items), "the button to go on")
        p["end"] = time.monotonic() - 60
        app.draw()
        self.assertFalse(p["left"], "it does not leave by itself while something is not repaired")
        app.pl_leave()
        self.assertEqual(app.view, "rest")

    def test_no_root_unit_is_a_note_not_a_crash(self):
        app = self.app()
        p = self.run_recovery(app, lambda action, timeout=10, **kw: (False, "no-root"))
        self.assertEqual(p["rows"][0]["level"], 1)

    def test_a_fine_recovery_goes_on_by_itself(self):
        app = self.app()
        self.run_recovery(app)
        app.pl["end"] = time.monotonic() - 10
        app.draw()
        self.assertTrue(app.pl["left"])
        self.assertEqual(app.view, "rest")

    def test_skip_is_offered_only_after_the_file_system_and_the_settings(self):
        app = self.app()
        app.pl = {"info": self.info, "phase": "run", "gen": 0, "pace": 0, "t0": 0, "end": None, "left": False, "restored": False,
                  "rows": [{"key": k, "state": "wait", "level": 0, "text": ("pl.w." + k, {})} for k in ("fs", "cfg", "learn", "rec", "songs", "svc", "snd")]}
        app.go("power")
        app.draw()
        self.assertEqual(app.bar_items[3][1], "", "nothing done yet: no skip")
        app.pl["rows"][0]["state"] = "done"
        app.draw()
        self.assertEqual(app.bar_items[3][1], "", "the file system alone is not enough")
        app.pl["rows"][1]["state"] = "done"
        app.draw()
        self.assertEqual(app.bar_items[3][1], app.tr("pl.skip"))

    def test_an_alarm_that_was_ringing_does_not_ring_again(self):
        app = self.app()
        app.save(alarm={"on": True, "time": "00:00", "days": "sun-sat", "song": "ode_to_joy", "by": "device"})
        app.song_names = lambda: ["ode_to_joy"]
        app.screen.pub = mock.Mock()
        app.pl_hold_alarm()
        local = app.local()
        self.assertEqual(app.st["alarm_last"], f"{local.strftime('%Y-%m-%d')} 00:00")
        app.screen.pub.assert_called_with("piano/alarm/set", "", retain=True)

    def test_the_last_line_names_what_was_open(self):
        app = self.app("en")
        app.pl = {"info": self.info}
        line = app.pl_last_line()
        self.assertIn('lesson "Ode to joy"', line)
        self.assertIn("profile Main", line)
        app = self.app("he")
        app.pl = {"info": self.info}
        self.assertIn("פרופיל Main", app.pl_last_line())
        app.pl = {"info": {"last": {}}}
        self.assertEqual(app.pl_last_line(), app.tr("pl.lastNone"))

    def test_the_record_follows_the_view_and_writes_little(self):
        app = self.app()
        w = app.pl_last
        w.path = self.data / "device" / "last.json"
        for view in ("settings", "settings", "rest"):
            app.view = view
            app.pl_seen = 0.0
            app.pl_track()
        self.assertEqual(w.writes, 1, "the second change comes within 5 seconds")
        self.assertEqual(json.loads(w.path.read_text())["view"], "settings")
        app.view = "power"
        app.pl_seen = 0.0
        w.wrote = -1e9
        app.pl_track()
        self.assertEqual(w.writes, 1, "the recovery screen itself is never recorded as where it was")

    def test_start_needs_a_pending_record_and_a_finished_setup(self):
        app = self.app()
        self.assertFalse(app.pl_start())
        PL.write_json(self.data / "device" / "unclean.json", self.info)
        with mock.patch.object(app, "pl_begin", return_value=True) as b:
            self.assertTrue(app.pl_start())
            app.st["setup_done"] = False
            self.assertFalse(app.pl_start())
        self.assertEqual(b.call_count, 1)

    def test_it_draws_in_both_languages_and_sizes_mid_repair_and_done(self):
        for size in ((800, 480), (1024, 600), (480, 320)):
            for lang in ("en", "he"):
                app = self.app(lang, size)
                app.pl = {"info": self.info, "phase": "run", "gen": 0, "pace": 0, "t0": 0, "end": None, "left": False, "restored": False,
                          "rows": [{"key": k, "state": s, "level": 0, "text": ("pl.r.songsNone", {})} for k, s in
                                   zip(("fs", "cfg", "learn", "rec", "songs", "svc", "snd"), ("done", "done", "done", "now", "wait", "wait", "wait"))]}
                app.go("power")
                with self.subTest(size=size, lang=lang, state="mid"):
                    app.draw()
                    self.assertTrue(app.bar_items)
                for r in app.pl["rows"]:
                    r["state"] = "done"
                app.pl["phase"], app.pl["end"] = "done", time.monotonic()
                with self.subTest(size=size, lang=lang, state="done"):
                    app.draw()


if __name__ == "__main__":
    unittest.main()


class MarkerUnwritable(Base):
    def test_an_unwritable_folder_logs_and_does_not_raise(self):
        import io
        err = io.StringIO()
        with mock.patch.object(PL, "write_json", side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(PL.mark_boot("1.0"), "error")          # no exception: the boot goes on
        self.assertIn("could not be written", err.getvalue())
        self.assertIn("Permission denied", err.getvalue())
        self.assertFalse((self.dev / "running.json").exists())


class BackupSkips(unittest.TestCase):
    def test_the_power_loss_state_is_never_backed_up_or_restored(self):
        backup = load("armonico_backup_pl", "device/backup.py")
        for rel in ("device/running.json", "device/unclean.json", "device/last.json", "device/last-good/settings.json",
                    "device/recordings/.interrupted/a.mp4"):
            self.assertTrue(backup._skipped(rel), rel)
        for rel in ("device/settings.json", "lessons.json", "device/recordings/a.mp4"):
            self.assertFalse(backup._skipped(rel), rel)
