"""The load test (device/soak.py) and the fixes that came with its first report.

The card: hidden in Settings › System › Tests until 7 touches on the Armonico logo in About (each within 1.5 s),
its page and the three root actions (soak-start, -stop, -export) with what they accept. The report's
helpers: GPIO pins held low, the radio's country, the power source, the result line for the card.

The fixes: a key that chatters is pressed once (Device.bounced), the mixer gives the same silence without
work (Mixer.block), a title's emoji is left out instead of drawn as "?" (Canvas.drawable), and the bridge
sends each key through one mosquitto_pub that stays open (note_pub), not a new one per key.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:                      # no pygame
    pygame = None


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(pygame is None, "pygame is not installed")
class Card(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def apps(self):
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            app.SOAK_STATUS = Path(tempfile.mkdtemp()) / "status.json"     # no test on this machine
            yield app

    def test_seven_touches_on_the_armonico_logo_show_the_card_last_on_the_test_page(self):
        for app in self.apps():
            clock = [100.0]
            app.t = lambda: clock[0]
            self.assertFalse(app.soak_card_on())
            self.assertEqual(len(app.settings_cells()["test"]), 6)
            for _ in range(6):
                app.soak_tap()
                clock[0] += 0.5
            self.assertFalse(app.soak_card_on())
            clock[0] += 2                                # a pause: the count starts over
            for _ in range(7):
                app.soak_tap()
                clock[0] += 0.5
            self.assertTrue(app.soak_card_on())
            self.assertEqual(app.screen.toast[0], app.tr("soakShown"))
            cells = app.settings_cells()["test"]
            self.assertEqual(len(cells), 7)
            self.assertEqual(cells[6][0], app.tr("sk.t"))
            from ui.views_settings import SUB_PAGES
            self.assertEqual(SUB_PAGES["soak"], ("test", 6))

    def test_the_logo_is_a_touch_target_in_about(self):
        for app in self.apps():
            app.go("about")
            app.draw()
            from ui.canvas import W
            boxes = [h[:4] for h in app.hits if getattr(h[-1], "__name__", "") == "soak_tap"]
            want = (W - 24 - 92) if app.lang == "he" else 24          # on the logo, on the reading side
            self.assertEqual(boxes, [(want, 70, 92, 92)])

    def test_the_page_offers_lengths_switches_and_a_start_that_asks_first(self):
        for app in self.apps():
            app.soak_shown = True
            cells = app.settings_cells()["soak"]
            self.assertEqual(len(cells), 7)
            self.assertIsNone(cells[0][3])               # the state is shown, not touched
            seen = set()
            for _ in range(5):
                seen.add(app.soak_len_name())
                app.settings_cells()["soak"][1][3]()
            self.assertEqual(len(seen), 5)
            tg = app.soak_tg
            app.settings_cells()["soak"][2][3]()
            self.assertEqual(app.soak_tg, not tg)
            with mock.patch("ui.system.request") as req:
                app.settings_cells()["soak"][4][3]()
                self.assertIsNotNone(app.confirm)        # asked, nothing sent yet
                req.assert_not_called()
            self.assertIsNone(app.settings_cells()["soak"][5][3])   # nothing to stop
            self.assertIsNone(app.settings_cells()["soak"][6][3])   # no report yet

    def test_a_running_test_locks_the_choices_and_offers_stop(self):
        for app in self.apps():
            app.SOAK_STATUS.write_text(json.dumps({"state": "running", "t0": time.time() - 600, "total": 3600,
                                                   "phase": "שיעור", "phase_en": "lesson"}))
            app._soak_seen = (-1e9, {})
            self.assertTrue(app.soak_card_on())          # shown even without the touches
            cells = app.settings_cells()["soak"]
            for i in (1, 2, 3, 4, 6):
                self.assertIsNone(cells[i][3], i)
            self.assertIsNotNone(cells[5][3])
            self.assertIn("0:10", cells[0][1])

    def test_a_test_far_past_its_end_is_stale_and_hides_again(self):
        for app in self.apps():
            app.SOAK_STATUS.write_text(json.dumps({"state": "running", "t0": time.time() - 9000, "total": 900}))
            app._soak_seen = (-1e9, {})
            self.assertEqual(app.soak_state()["state"], "stale")
            self.assertFalse(app.soak_card_on())

    def test_the_last_result_and_its_report(self):
        for app in self.apps():
            app.soak_shown = True
            app.SOAK_STATUS.write_text(json.dumps({"state": "done", "zip": "soak-2026-11-10-193913.zip",
                                                   "result": {"level": 0, "he": "עברה", "en": "Passed"}}))
            app._soak_seen = (-1e9, {})
            cells = app.settings_cells()["soak"]
            self.assertIn("עברה" if app.lang == "he" else "Passed", cells[0][1])
            self.assertIsNotNone(cells[6][3])


@unittest.skipIf(pygame is None, "pygame is not installed")
class RestVideo(unittest.TestCase):
    """The test shows the rest screen as the video and as the animation in turn, never saving the setting."""
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def strip(self, app, **d):
        app.mqtt_event("piano/device/soak", json.dumps(dict({"state": "run", "beat": time.time()}, **d)))

    def test_the_strip_picks_the_rest_screen_while_the_test_runs(self):
        app = make_app(self.mod, settings={"rest": "animation"})
        self.strip(app, rest="video")
        self.assertEqual(app.rest_content, "video")
        self.assertEqual(app.screen.rest_mode(), "video")
        self.assertEqual(app.st["rest"], "animation")              # the setting itself is untouched
        app.view, app.last_input = "rest", -1e9
        self.assertFalse(app.asleep())                             # the video plays on, as with sleep off
        self.strip(app, rest="animation")
        self.assertEqual(app.rest_content, "animation")
        self.assertFalse(app.asleep(), "the animation's phase measures the rest screen, not the sleep screen")
        self.strip(app, state="end")
        self.assertTrue(app.asleep(), "after the test the sleep setting is back in force")

    def test_a_test_that_died_or_ended_leaves_the_setting(self):
        app = make_app(self.mod, settings={"rest": "animation"})
        app.mqtt_event("piano/device/soak", json.dumps({"state": "run", "beat": time.time() - 400, "rest": "video"}))
        self.assertEqual(app.rest_content, "animation")            # no beat for minutes
        self.strip(app, state="end", rest="video")
        self.assertEqual(app.rest_content, "animation")
        app.mqtt_event("piano/device/soak", "")
        self.assertEqual(app.screen.rest_mode(), "animation")


class RootActions(unittest.TestCase):
    def setUp(self):
        self.ra = load("ra_soak", "device/root_actions.py")
        self.p = mock.Mock()

    def test_the_actions_are_known(self):
        for a in ("soak-start", "soak-stop", "soak-export"):
            self.assertIn(a, self.ra.ACTIONS)

    def test_start_takes_only_the_lengths_the_screen_offers(self):
        for bad in (5, 30, 999, "60", None, 60.5):
            with mock.patch.object(self.ra, "soak_cmd") as cmd:
                with self.assertRaises(ValueError):
                    self.ra.do_soak_start(self.p, {"minutes": bad})
                cmd.assert_not_called()

    def test_start_arms_then_restarts(self):
        with mock.patch.object(self.ra, "soak_cmd", return_value=(0, "ok")) as cmd, \
                mock.patch.object(self.ra, "nap"), mock.patch.object(self.ra, "run") as run:
            self.ra.do_soak_start(self.p, {"minutes": 480, "telegram": False, "alarm": True})
        args = cmd.call_args[0]
        self.assertEqual(args[:3], ("start", "480m", "--reboot-later"))
        self.assertIn("--no-telegram", args)
        self.assertNotIn("--no-alarm", args)
        run.assert_called_once_with("systemctl", "reboot")

    def test_start_that_fails_does_not_restart(self):
        with mock.patch.object(self.ra, "soak_cmd", return_value=(1, "שירותים לא פעילים")), \
                mock.patch.object(self.ra, "run") as run:
            with self.assertRaises(RuntimeError):
                self.ra.do_soak_start(self.p, {"minutes": 60})
        run.assert_not_called()

    def test_export_copies_the_newest_report_only(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        src, stick = t / "soak", t / "stick" / "Armonico recordings"
        src.mkdir()
        stick.mkdir(parents=True)
        (src / "soak-2026-11-01-100000.zip").write_bytes(b"old")
        (src / "soak-2026-11-10-193913.zip").write_bytes(b"new" * 1000)
        (src / "status.json").write_text("{}")
        (src / "soak-x.zip").write_bytes(b"not a report")
        sr = mock.Mock(stick=lambda: stick, open_dir=lambda base, parts: os.open(base, os.O_RDONLY | os.O_DIRECTORY))
        with mock.patch.object(self.ra, "SOAK_DIR", src), mock.patch.object(self.ra, "screen_rec", return_value=sr):
            self.ra.do_soak_export(self.p, {})
        out = t / "stick" / self.ra.REPORTS_ON_STICK
        self.assertEqual([x.name for x in out.iterdir()], ["soak-2026-11-10-193913.zip"])
        self.assertEqual((out / "soak-2026-11-10-193913.zip").read_bytes(), b"new" * 1000)
        self.assertEqual(json.loads(self.p.call_args[0][2])["copied"], 1)

    def test_export_with_no_report_says_so(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        with mock.patch.object(self.ra, "SOAK_DIR", t / "missing"):
            self.ra.do_soak_export(self.p, {})
        self.assertTrue(json.loads(self.p.call_args[0][2])["none"])


class Helpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load("soak_mod", "device/soak.py")

    def test_lengths(self):
        p = self.s.parse_duration
        self.assertEqual(p("15m"), 15)
        self.assertEqual(p("8h"), 480)
        self.assertEqual(p("1h30m"), 90)

    def test_a_pin_held_low_with_its_pull_up_is_found_on_the_header_only(self):
        out = textwrap.dedent("""\
             3: ip    pu | hi // GPIO3 = input
             4: ip    pu | lo // GPIO4 = input
            17: ip    pu | hi // GPIO17 = input
            30: ip    pu | lo // GPIO30 = input
            22: op dh pd | hi // GPIO22 = output
        """)
        self.assertEqual(self.s.gpio_held_low(out), [4])
        self.assertEqual(self.s.gpio_held_low(""), [])

    def test_the_telegram_summary_is_short_and_right_to_left(self):
        import tempfile, json as j
        d = Path(tempfile.mkdtemp())
        (d / "run.json").write_text(j.dumps({"elapsed": 900}))
        (d / "verdicts.json").write_text(j.dumps([
            [1, "מדינת הרדיו", "x", ""], [0, "עומס מעבד", "ממוצע 26% · 95% 47% · שיא 53%", ""],
            [0, "השהיית קליד (מגע עד הגשר)", "חציון 49 ms · 95% 60 ms · 99% 64 ms · אבדו 0 מתוך 481", ""]]))
        t = self.s.summary_text(d, "/home/pi/soak-a.zip")
        self.assertTrue(all(x.startswith("\u200f") for x in t.splitlines()))
        self.assertIn("15 דק'", t)
        self.assertIn("לבדוק: מדינת הרדיו", t)
        self.assertIn("מעבד: ממוצע 26% · שיא 53%", t)
        self.assertIn("קליד: 95% 60 ms · אבדו 0 מתוך 481", t)
        self.assertIn("soak-a.zip", t)
        self.assertFalse(self.s.send_summary(d, None, ""))          # בלי צ'אט: לא שולח כלום

    def test_the_radio_country_against_the_cmdline(self):
        reg = "global\ncountry DE: DFS-ETSI\n\t(2400 - 2483 @ 40)\n\nphy#0 (self-managed)\ncountry 99: DFS-UNSET\n"
        r = self.s.regdom("console=tty1 cfg80211.ieee80211_regdom=IL rootwait", reg)
        # הרדיו מנהל את המדינה בעצמו (phy 99): ה-DE הכללי לא משנה את השידור, ולכן זה תקין עם הסבר
        self.assertEqual((r["want"], r["global"], r["ok"], r["self"]), ("IL", "DE", True, True))
        self.assertIn("he", r)
        hard = self.s.regdom("cfg80211.ieee80211_regdom=IL", reg.replace("country 99", "country DE"))
        self.assertEqual((hard["ok"], hard["self"]), (False, False))
        self.assertIn("he", hard)
        self.assertTrue(self.s.regdom("console=tty1", reg)["ok"])
        self.assertTrue(self.s.regdom("cfg80211.ieee80211_regdom=DE", reg)["ok"])

    def test_the_power_source(self):
        with mock.patch.object(self.s, "poe_current", return_value=None), \
                mock.patch.object(self.s, "read", side_effect=lambda p, d="": "0" if "carrier" in str(p) else ""), \
                mock.patch.object(self.s, "POE_DIRS", ()):
            self.assertEqual(self.s.power_source({})["src"], "USB-C")
        with mock.patch.object(self.s, "poe_current", return_value=1250), mock.patch.object(self.s, "read", return_value=""):
            d = self.s.power_source({})
            self.assertEqual(d["src"], "PoE")
            self.assertIn("1250", d["he"])

    def test_the_result_line_for_the_card(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        (t / "run.json").write_text(json.dumps({"elapsed": 7200}))
        (t / "verdicts.json").write_text(json.dumps([[0, "a", "", ""], [1, "b", "", ""]]))
        r = self.s.result_line(t)
        self.assertEqual((r["level"], r["hours"]), (1, 2.0))
        (t / "run.json").write_text(json.dumps({"elapsed": 60, "interrupted": True}))
        self.assertEqual(self.s.result_line(t)["level"], 2)

    def test_the_songs_it_plays_make_no_shortcut(self):
        import threading
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        fake = mock.Mock(env={"RUN_DIR": str(t)}, quit=threading.Event())
        th = threading.Thread(target=self.s.Soak.quiet_shortcuts, args=(fake,), daemon=True)
        th.start()
        time.sleep(1.3)
        fake.quit.set()
        th.join(3)
        flag = t / "piano_screen_keys"
        self.assertTrue(flag.exists())
        self.assertLess(time.time() - flag.stat().st_mtime, 3)
        flag.unlink()
        (t / "piano_screen_keys").symlink_to(t / "elsewhere")          # never written through a link
        fake.quit.clear()
        th = threading.Thread(target=self.s.Soak.quiet_shortcuts, args=(fake,), daemon=True)
        th.start()
        time.sleep(1.3)
        fake.quit.set()
        th.join(3)
        self.assertFalse((t / "elsewhere").exists())

    def test_the_card_wear_section(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        (t / "writes.csv").write_text("t,pid,comm,group,kb\n60,1,journald,other,2048\n120,2,python3,device,512\n")
        (t / "system.csv").write_text("t,swap_used\n0,0\n")
        got = []
        h = self.s.wear_section(t, {"root_mount": "/dev/mmcblk0p2 ext4 rw,relatime", "swaps": "Filename\n/var/swap file 100 0 -2"},
                                [{"sd_w_mb": "10"}, {"sd_w_mb": "400"}], 3600, lambda *a: got.append(a))
        self.assertIn("journald", h)
        names = [g[1] for g in got]
        self.assertIn("כתיבה לכרטיס SD", names)
        self.assertIn("גישה לקבצים נכתבת לכרטיס", names)          # no noatime
        self.assertIn("swap על הכרטיס", names)
        self.assertEqual(dict((g[1], g[0]) for g in got)["כתיבה לכרטיס SD"], 1)   # 400 MB in an hour

    def test_the_rest_video_is_a_phase_of_its_own(self):
        self.assertIn("video", self.s.PHASE_HE)
        self.assertTrue(hasattr(self.s.Soak, "phase_video"))
        self.assertTrue(str(self.s.REST_VIDEO).endswith("device/media/armonico-japanese.mp4"))

    def test_results_live_in_roots_own_folder_not_the_data_folder(self):
        self.assertEqual(str(self.s.OUT_ROOT), "/var/lib/armonico-soak")
        ra = load("ra_soak2", "device/root_actions.py")
        self.assertEqual(ra.SOAK_DIR, self.s.OUT_ROOT)
        self.assertIn('rm -rf "/var/lib/${APP:?}-soak"', (ROOT / "install.sh").read_text())


class Fixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pi = load("pi_soak_fixes", "device/armonico_pi.py")

    def test_a_key_that_chatters_is_one_press_and_one_release(self):
        d = object.__new__(self.pi.Device)
        now = [10.0]
        with mock.patch.object(self.pi.time, "monotonic", lambda: now[0]), mock.patch.object(self.pi, "log") as log:
            self.assertFalse(d.bounced(3, True))      # pressed
            now[0] += 0.12
            self.assertFalse(d.bounced(3, False))     # let go
            now[0] += 0.015
            self.assertTrue(d.bounced(3, True))       # 15 ms later: the contact bounced
            now[0] += 0.01
            self.assertTrue(d.bounced(3, False))      # its release is left out too
            now[0] += 0.2
            self.assertFalse(d.bounced(3, True))      # a real second press
            self.assertFalse(d.bounced(4, True))      # another key is not touched
        self.assertEqual(d.bounces, {3: 1})
        self.assertIn("Key bounce", log.call_args[0][0])

    def test_the_debounce_holds_long_enough_for_a_tact_switch(self):
        self.assertGreaterEqual(self.pi.BOUNCE, 0.015)
        self.assertGreater(self.pi.REPRESS, self.pi.BOUNCE)

    def test_the_midi_input_waits_about_a_second_not_a_millisecond(self):
        # alsa_midi hands its seconds to select.poll(), which counts milliseconds: timeout=1 was a busy loop
        import inspect
        src = inspect.getsource(self.pi.Device._input) if hasattr(self.pi.Device, "_input") else \
            next(inspect.getsource(c._input) for c in vars(self.pi).values() if isinstance(c, type) and hasattr(c, "_input"))
        self.assertIn("timeout=1000", src)

    def test_the_tempo_map_is_the_same_without_merging_the_whole_file(self):
        import random
        import mido
        sys.path.insert(0, str(ROOT / "lessons"))
        import piano_numbers as pn

        def merged(mid):                     # what it was: every message of the file merged and copied
            marks, tick, tempo = [(0, 500000)], 0, 500000
            for msg in mido.merge_tracks(mid.tracks):
                tick += msg.time
                if msg.type == "set_tempo" and msg.tempo != tempo:
                    tempo = msg.tempo
                    marks.append((tick, tempo))
            return marks
        rnd = random.Random(3)
        m = mido.MidiFile(ticks_per_beat=480)
        for _ in range(5):                   # tempo changes on several tracks, some at the same tick
            tr = mido.MidiTrack()
            m.tracks.append(tr)
            for _ in range(300):
                if rnd.random() < 0.1:
                    tr.append(mido.MetaMessage("set_tempo", tempo=rnd.choice([400000, 500000, 600000]),
                                               time=rnd.choice([0, 240])))
                tr.append(mido.Message("note_on", note=60, velocity=70, time=rnd.choice([0, 120])))
        files = [m] + [mido.MidiFile(f) for f in sorted((ROOT / "setup" / "music").glob("*.mid"))]
        for f in files:
            self.assertEqual(pn.tempo_map(f), merged(f))

    def test_silence_is_the_same_block_every_time(self):
        m = self.pi.Mixer(4)
        a, b = m.block(4096), m.block(4096)
        self.assertEqual(a, bytes(4096))
        self.assertIs(a, b)
        self.assertEqual(len(m.block(2048)), 2048)

    def test_threads_get_their_function_names(self):
        import threading
        if sys.version_info >= (3, 14) or not Path("/proc/self/task").exists():
            self.skipTest("Python names threads itself, or no /proc")
        got = []

        def watch_power():
            got.append(Path(f"/proc/self/task/{threading.get_native_id()}/comm").read_text().strip())
        th = threading.Thread(target=watch_power)
        th.start()
        th.join()
        self.assertEqual(got, ["watch_power"])

    @unittest.skipIf(pygame is None, "pygame is not installed")
    def test_a_character_no_font_has_is_left_out(self):
        app = make_app(load_screen())
        cv = app.cv
        missing = "\U0001F539"
        with mock.patch.object(cv.fonts, "route", side_effect=lambda ch: (None,) if ch == missing else object()):
            self.assertEqual(cv.drawable(missing + " Part 1 of 22"), "Part 1 of 22")


STUB_PUB = r'''#!/bin/bash
case " $* " in
  *" -l "*) echo START >> "$T/pub.log"
            n=0; while IFS= read -r line; do echo "L $line" >> "$T/pub.log"; n=$((n+1))
            [ -e "$T/die" ] && [ "$n" -ge 1 ] && exit 1; done ;;
  *) echo "ONE $*" >> "$T/pub.log" ;;
esac
'''


class NotePub(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, ignore_errors=True)
        b = self.t / "bin"
        b.mkdir()
        (b / "mosquitto_pub").write_text(STUB_PUB)
        (b / "mosquitto_pub").chmod(0o755)
        for d in ("run", "data", "songs"):
            (self.t / d).mkdir()
        text = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        (self.t / "lib.sh").write_text(text[:text.index("# Entry point")])
        self.env = dict(os.environ, T=str(self.t), PATH=f"{b}:{os.environ['PATH']}", MQTT_HOST="127.0.0.1",
                        MQTT_PORT="1", RUN_DIR=str(self.t / "run"), DATA_DIR=str(self.t / "data"),
                        SONGS_DIR=str(self.t / "songs"), STOP_KEY="36", UI_LANG="en", MIDI_DEVICE="x")

    def sh(self, code):
        script = f'source "$T/lib.sh"\ntrap - EXIT SIGTERM SIGINT\n{code}'
        subprocess.run(["setsid", "bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=30)
        return (self.t / "pub.log").read_text() if (self.t / "pub.log").exists() else ""

    def test_keys_go_through_one_publisher(self):
        log = self.sh("note_pub 60; note_pub 62; note_pub 64; note_pub 65; sleep 0.3; note_pub_stop; wait")
        self.assertEqual(log.count("START"), 1)
        # the first key goes on its own while the publisher connects: a publisher that cannot connect exits and
        # would take the key with it; from then on all of them through the one publisher
        self.assertEqual(log.count("ONE"), 1)
        self.assertIn("-t piano/notes -m 60", log)
        self.assertEqual([x for x in log.splitlines() if x.startswith("L ")], ["L 62", "L 64", "L 65"])
        self.assertEqual(list((self.t / "run").glob("note-pub.*")), [])      # the fifo's name is gone

    def test_a_publisher_that_died_falls_back_to_one_per_key_and_comes_back(self):
        (self.t / "die").touch()
        log = self.sh("NOTE_PUB_RETRY=1; note_pub 59; note_pub 60; sleep 0.3; note_pub 61; rm -f \"$T/die\"; "
                      "sleep 1.1; note_pub 62; note_pub 63; sleep 0.3; note_pub_stop; wait")
        self.assertIn("L 60", log)                                          # then it died
        self.assertIn("-t piano/notes -m 61", log)                          # nothing lost while it was down
        self.assertEqual(log.count("START"), 2)                             # and it came back once
        self.assertIn("L 63", log)


if __name__ == "__main__":
    unittest.main()
