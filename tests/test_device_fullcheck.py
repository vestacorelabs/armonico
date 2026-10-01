"""The full check of the Armonico Pi (device/fullcheck.py, device/ui/views_check.py): each new check
with the commands it reads mocked (passed, failed, not checked), nothing written but the report, the
guided steps, the summary, the report file, Telegram through root only, the CLI's exit code and the
offer after an update. Every new screen draws in both languages at both sizes with nothing cut."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers and DATA_DIR)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
import fullcheck as F                                # noqa: E402
import diag                                          # noqa: E402

pygame = T.pygame
TOKEN = "123456789:" + "A" * 35


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ok(level):
    return diag.Check(level, "x", "X", "X", "detail", "פירוט", "")


def levels(items):
    return [i.level for i in items]


class Areas(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.cfg = {"DEVICE": "yes", "DATA_DIR": str(self.data)}

    # ---- power
    def power(self, value):
        with mock.patch.object(diag, "run_out", return_value=value):
            return F.a_power(self.cfg, {})

    def test_power_now_since_boot_and_not_checked(self):
        self.assertEqual(levels(self.power("throttled=0x0\n")), [F.OK, F.OK])
        res = self.power("throttled=0x50000\n")
        self.assertEqual(levels(res), [F.PROBLEM, F.NOTE])
        self.assertEqual(res[0].row[0], "under-voltage since boot")
        self.assertIn("HAT", res[0].todo[0])
        res = self.power("throttled=0x50005\n")
        self.assertEqual(res[0].row[0], "under-voltage now")
        self.assertEqual(res[1].level, F.PROBLEM, "slowed down right now")
        self.assertEqual(levels(self.power("")), [F.SKIP])

    # ---- the bridge's status kept on the broker
    def test_the_status_kept_on_the_broker(self):
        with mock.patch.object(diag, "check_broker", return_value=ok(diag.OK)):
            for kept, want in (("online", F.OK), ("offline", F.PROBLEM), ("", F.NOTE), (None, F.SKIP)):
                with self.subTest(kept=kept), mock.patch.object(F, "mqtt_retained", return_value=kept):
                    res = F.a_broker(self.cfg, {})
                    self.assertEqual(res[1].level, want)
            with mock.patch.object(F, "mqtt_retained", return_value="offline"):
                self.assertEqual(F.a_broker(self.cfg, {})[1].fix, "services", "a stale offline: the services' fix")
        with mock.patch.object(diag, "check_broker", return_value=ok(diag.PROBLEM)), \
                mock.patch.object(F, "mqtt_retained") as asked:
            res = F.a_broker(self.cfg, {})
        asked.assert_not_called()
        self.assertEqual(levels(res), [F.PROBLEM, F.SKIP])

    # ---- the lesson screen's song list
    def overview(self, body):
        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return mock.patch("urllib.request.urlopen", return_value=R(json.dumps(body).encode()))

    def test_the_song_list(self):
        with mock.patch.object(diag, "check_lesson_screen", return_value=ok(diag.OK)):
            with self.overview({"songs": [{"name": "ode_to_joy", "total": 3}]}):
                self.assertEqual(F.a_lesson_screen(self.cfg, {})[1].level, F.OK)
            with self.overview({"songs": "nope"}):
                self.assertEqual(F.a_lesson_screen(self.cfg, {})[1].level, F.PROBLEM)
            with self.overview({"songs": []}):
                self.assertEqual(F.a_lesson_screen(self.cfg, {})[1].level, F.NOTE)

    # ---- Wi-Fi
    def test_wifi(self):
        cases = [({"nm": False}, F.PROBLEM),
                 ({"nm": True, "dev": "", "wired": True}, F.OK),
                 ({"nm": True, "dev": "wlan0", "wired": False, "radio": False, "connected": False}, F.NOTE),
                 ({"nm": True, "dev": "wlan0", "wired": False, "radio": True, "connected": True, "ssid": "Home", "signal": 72}, F.OK),
                 ({"nm": True, "dev": "wlan0", "wired": False, "radio": True, "connected": True, "ssid": "Home", "signal": 22}, F.NOTE)]
        with mock.patch.object(diag, "check_network", return_value=ok(diag.OK)):
            for st, want in cases:
                with self.subTest(st=st), mock.patch.object(F, "wifi_state", return_value=st):
                    self.assertEqual(F.a_network(self.cfg, {})[1].level, want)
            with mock.patch.object(F, "wifi_state", return_value=cases[3][0]):
                self.assertEqual(F.a_network(self.cfg, {})[1].row[1], "אות 72%")

    def test_wifi_is_read_without_turning_the_radio_on(self):
        ra = F._ra()
        calls = []

        class R:
            def __init__(self, out):
                self.stdout, self.returncode = out, 0

        def nmcli(*args, timeout=30):
            calls.append(args)
            if args[:2] == ("radio", "wifi") and len(args) == 2:
                return R("disabled\n")
            return R("")
        with mock.patch.object(F, "_ra", return_value=ra), mock.patch.object(ra, "nm_running", return_value=True), \
                mock.patch.object(ra, "nm_devices", return_value=[("wlan0", "wifi", "disconnected")]), \
                mock.patch.object(ra, "nmcli", side_effect=nmcli), mock.patch.object(ra, "radio_on") as on:
            st = F.wifi_state()
        self.assertFalse(st["radio"])
        on.assert_not_called()
        self.assertNotIn(("radio", "wifi", "on"), calls)

    # ---- internet
    def test_the_internet_only_when_online_each_with_its_time(self):
        with mock.patch.object(F, "reach") as reach:
            res = F.a_internet(self.cfg, {"online": False})
        reach.assert_not_called()
        self.assertEqual(levels(res), [F.SKIP])
        answers = {"api.github.com": (True, 120), "open-meteo": (False, "timeout"), "nasa": (True, 310)}

        def fake(url, timeout=4):
            return next(v for k, v in answers.items() if k in url)
        with mock.patch.object(F, "reach", side_effect=fake):
            res = F.a_internet(self.cfg, {"online": True})
        self.assertEqual(levels(res), [F.OK, F.NOTE, F.OK])
        self.assertIn("120 ms", res[0].detail[0])
        self.assertEqual(res[1].label[0], "Open-Meteo (weather)")

    # ---- memory
    def test_memory_and_a_process_using_too_much(self):
        proc = self.data / "proc"
        (proc / "41").mkdir(parents=True)
        (proc / "41" / "cmdline").write_bytes(b"python3\0" + str(diag.APP_DIR / "device" / "screen.py").encode() + b"\0")
        (proc / "41" / "status").write_text("Name:\tpython3\nVmRSS:\t 1600000 kB\n")
        (proc / "meminfo").write_text("MemTotal: 4000000 kB\nMemAvailable: 1200000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
        with mock.patch.object(F, "PROC", proc):
            res = F.a_memory(self.cfg, {})
        self.assertEqual(levels(res), [F.OK, F.NOTE])
        self.assertIn("screen.py", res[1].row[0])
        (proc / "meminfo").write_text("MemTotal: 4000000 kB\nMemAvailable: 50000 kB\n")
        with mock.patch.object(F, "PROC", proc):
            self.assertEqual(F.a_memory(self.cfg, {})[0].level, F.PROBLEM)

    # ---- the SD card
    def test_read_only_and_the_kernel_log(self):
        proc = self.data / "proc"
        proc.mkdir()
        (proc / "mounts").write_text("/dev/mmcblk0p2 / ext4 ro,noatime 0 0\n")
        no = (1, "", "dmesg: read kernel buffer failed: Operation not permitted\n")
        hint = (0, "", "Hint: You are currently not seeing messages from other users and the system.\n")
        with mock.patch.object(F, "PROC", proc), mock.patch.object(diag, "check_disk", return_value=None), \
                mock.patch.object(F, "run", side_effect=lambda *c, **k: no if c[0] == "dmesg" else hint):
            res = F.a_sd(self.cfg, {})
        self.assertEqual(levels(res), [F.SKIP, F.PROBLEM, F.SKIP], "the kernel log unreadable: not checked, not failed")
        (proc / "mounts").write_text("/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n")
        errs = (0, "[ 12.3] mmc0: error -110 whilst initialising SD card\n[ 99.1] usb 1-1: new device\n", "")
        with mock.patch.object(F, "PROC", proc), mock.patch.object(diag, "check_disk", return_value=None), \
                mock.patch.object(F, "run", return_value=errs):
            res = F.a_sd(self.cfg, {})
        self.assertEqual(levels(res), [F.SKIP, F.OK, F.PROBLEM])
        self.assertIn("mmc0", res[2].lines[-1], "the lines themselves are kept, to read on the screen")

    # ---- the samples
    def test_the_voices_and_the_drums(self):
        gm = self.data / "device" / "gm"
        for f in F.FAMILIES:
            (gm / f).mkdir(parents=True)
            (gm / f / "60.wav").write_bytes(b"x")
        pi = load("armonico_pi_families", "device/armonico_pi.py")
        self.assertEqual(F.FAMILIES[:-1], pi.FAMILIES[1:], "the same voices as armonico_pi.py")
        with mock.patch.object(diag, "check_samples", return_value=ok(diag.OK)):
            res = F.a_samples(self.cfg, {})
            self.assertEqual(levels(res), [F.OK, F.OK])
            self.assertEqual(res[0].row[0], "17 of 17")
            (gm / "drums" / "60.wav").unlink()
            res = F.a_samples(self.cfg, {})
        self.assertEqual(res[1].level, F.PROBLEM)
        self.assertIn("drums", res[1].detail[0])

    # ---- GPIO
    def test_pins_in_use_and_a_key_held(self):
        out = "\n".join(f"{p}: ip    pu | hi // GPIO{p} = input" for p in range(28))
        held = out.replace("17: ip    pu | hi", "17: ip    pu | lo").replace("9: ip    pu | hi", "9: a0    pn | lo // GPIO9 = SPI0_MISO\nX")
        with mock.patch.object(diag, "check_gpio", return_value=ok(diag.OK)), mock.patch("time.sleep"):
            with mock.patch.object(F, "run", return_value=(0, out, "")):
                self.assertEqual(levels(F.a_gpio(self.cfg, {})), [F.OK, F.OK, F.OK])
            with mock.patch.object(F, "run", return_value=(0, held, "")):
                res = F.a_gpio(self.cfg, {})
            self.assertEqual(levels(res), [F.OK, F.PROBLEM, F.NOTE])
            self.assertIn("9", res[1].row[0])
            self.assertEqual(res[2].row[0], "key 2 (D) held down all the time", "GPIO17 is the D key")
            self.assertEqual(res[2].link, "test")
            with mock.patch.object(F, "run", return_value=None):
                self.assertEqual(F.a_gpio(self.cfg, {})[1].level, F.SKIP)

    # ---- the settings files
    def test_a_damaged_file_is_named(self):
        (self.data / "device").mkdir()
        (self.data / "device" / "settings.json").write_text("{}")
        (self.data / "profiles" / "kid").mkdir(parents=True)
        (self.data / "profiles" / "kid" / "lessons.json").write_text("{\"a\": ")
        (self.data / "profiles.json").write_text("{\"list\": []}")
        res = F.a_files(self.cfg, {})
        self.assertEqual(levels(res), [F.PROBLEM])
        self.assertEqual(res[0].row[0], "profiles/kid/lessons.json is damaged")
        (self.data / "profiles" / "kid" / "lessons.json").write_text("{}")
        self.assertEqual(levels(F.a_files(self.cfg, {})), [F.OK])

    # ---- the log
    def test_the_log_per_service(self):
        def jr(*cmd, **k):
            if cmd[0] == "systemctl":
                return (1, "", "")
            unit = cmd[cmd.index("-u") + 1]
            return (0, "boom 1\nboom 2\n", "") if unit.endswith("bridge") else (0, "", "")
        with mock.patch.object(F, "run", side_effect=jr):
            res = F.a_journal(self.cfg, {})
        self.assertEqual(levels(res), [F.NOTE])
        self.assertEqual(res[0].row[0], "2 errors")
        self.assertEqual(res[0].lines, ("boom 1", "boom 2"))
        hint = (0, "", "Hint: You are currently not seeing messages from other users and the system.\n")
        with mock.patch.object(F, "run", return_value=hint):
            self.assertEqual(levels(F.a_journal(self.cfg, {})), [F.SKIP])

    # ---- the backups
    def test_the_backups(self):
        res = F.a_backup(self.cfg, {})
        self.assertEqual(levels(res), [F.NOTE, F.PROBLEM], "no backup, no folder")
        b = self.data / "backups"
        b.mkdir()
        f = b / "armonico-2026-01-01.armonico"
        f.write_bytes(b"x")
        old = time.time() - 40 * 86400
        os.utime(f, (old, old))
        self.assertEqual(levels(F.a_backup(self.cfg, {})), [F.NOTE, F.OK])
        os.utime(f, None)
        self.assertEqual(levels(F.a_backup(self.cfg, {})), [F.OK, F.OK])

    # ---- the alarm clock
    def test_the_alarm_song_and_the_stop_path(self):
        (self.data / "songs").mkdir()
        st = {"alarm": {"on": True, "time": "07:00", "song": "ode_to_joy"}}
        res = F.a_alarm(self.cfg, {"settings": st})
        self.assertEqual(levels(res), [F.PROBLEM, F.OK])
        (self.data / "songs" / "ode_to_joy.mid").write_bytes(b"MThd")
        stop = self.data / "stop-request"
        stop.write_text("x\n")
        os.utime(stop, (time.time() - 60, time.time() - 60))
        res = F.a_alarm(self.cfg, {"settings": st})
        self.assertEqual(levels(res), [F.OK, F.NOTE])
        self.assertTrue(stop.exists(), "the check leaves the stop request where it is")

    def test_an_alarm_that_is_off_is_still_checked(self):
        songs = self.data / "songs"
        songs.mkdir()
        off = lambda song="": F.a_alarm(self.cfg, {"settings": {"alarm": {"on": False, "song": song}}})[0]
        self.assertEqual(off().level, F.NOTE, "no song at all to ring with")
        (songs / "ode_to_joy.mid").write_bytes(b"MThd")
        self.assertEqual(off().level, F.OK)
        self.assertEqual(off("ode_to_joy").level, F.OK)
        self.assertEqual(off("gone").level, F.NOTE, "its song is missing")
        self.assertNotEqual(off().level, F.SKIP)

    def test_only_usb_cards_with_midi_are_keyboards(self):
        d = Path(tempfile.mkdtemp())
        (d / "cards").write_text(" 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
                                 " 1 [Keyboard       ]: USB-Audio - Digital Keyboard\n"
                                 " 2 [Headset        ]: USB-Audio - USB Headset\n")
        (d / "card1").mkdir()
        (d / "card1" / "midi0").write_text("")
        (d / "card2").mkdir()
        self.assertEqual(F.usb_keyboards(d / "cards"), ["Digital Keyboard"], "a USB headset has no MIDI")

    def test_no_usb_keyboard_checks_that_one_would_be_seen(self):
        d = Path(tempfile.mkdtemp())
        (d / "snd_usb_audio").mkdir()
        with mock.patch.object(F, "run", return_value=(0, "client 0: 'System'", "")):
            self.assertTrue(F.usb_midi_ready(d))
        with mock.patch.object(F, "run", return_value=(1, "", "")):
            self.assertFalse(F.usb_midi_ready(d))

    # ---- Telegram
    def test_telegram_asks_root_only_whether_a_token_is_kept(self):
        asked = []

        def req(action, timeout=10):
            asked.append(action)
            return True, json.dumps({"kept": True})
        st = {"tg_done": True}
        self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": st, "request": req})), [F.OK])
        self.assertEqual(asked, ["telegram-kept"])
        self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": st, "request": lambda a, timeout=10: (True, '{"kept": false}')})),
                         [F.NOTE])
        self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": st, "request": lambda a, timeout=10: (False, "no-root")})),
                         [F.SKIP])
        # not set up: whether the device reaches Telegram is checked instead
        with mock.patch.object(F, "reach", return_value=(True, 40)):
            self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": {}, "online": True})), [F.OK])
        with mock.patch.object(F, "reach", return_value=(False, "URLError")):
            self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": {}, "online": True})), [F.NOTE])
        self.assertEqual(levels(F.a_telegram(self.cfg, {"settings": {}, "online": False})), [F.SKIP])

    def test_update_is_skipped_offline(self):
        with mock.patch.object(diag, "latest_release") as lr:
            self.assertEqual(levels(F.a_update(self.cfg, {"online": False})), [F.SKIP])
        lr.assert_not_called()
        with mock.patch.object(diag, "latest_release", return_value=("v99.0", "")):
            self.assertEqual(levels(F.a_update(self.cfg, {"online": True})), [F.NOTE])

    # ---- the run
    def test_a_check_that_hangs_or_fails_is_not_checked(self):
        def slow(cfg, ctx):
            time.sleep(2)
            return []

        def bad(cfg, ctx):
            raise RuntimeError("boom")
        seen = []
        with mock.patch.object(F, "AREAS", [("power", slow), ("memory", bad)]):
            res = F.run_all(self.cfg, {}, on_area=lambda i, items: seen.append(i), timeout=0.2)
        self.assertEqual([k for k, _ in res], ["power", "memory"])
        self.assertEqual([levels(i) for _, i in res], [[F.SKIP], [F.SKIP]])
        self.assertIn("boom", res[1][1][0].detail[0])
        self.assertEqual(sorted(seen), [0, 1])

    def test_21_areas_each_with_its_name_in_both_languages(self):
        from ui.texts import T as texts
        self.assertEqual(len(F.AREAS), 21)
        for key, _ in F.AREAS:
            self.assertIn("chk.a." + key, texts)


class Report(unittest.TestCase):
    def test_the_report_and_the_last_10(self):
        data = Path(tempfile.mkdtemp())
        auto = [F.item(F.PROBLEM, "power", F.L("chk.r.underBoot"), F.L("chk.d.underBoot", c="get_throttled=0x50000"),
                       F.L("chk.do.under")),
                F.item(F.OK, "memory", F.L("chk.r.memFree", a="1.2GB"), F.L("chk.d.mem", a="1.2GB", t="4.0GB"))]
        guided = [F.Item(F.OK, "keys", F.L("chk.s.keys"), F.L("chk.g.keysOk", t=13), F.L("chk.g.keysOk", t=13), F.NONE, "", "")]
        text = F.report_text(auto, guided, version="1.1.0", when=datetime(2026, 10, 27, 9, 14))
        self.assertIn("Version: 1.1", text)
        self.assertIn("Date: 2026-10-27 09:14", text)
        self.assertIn("Result: 1 failed, 0 warnings, 2 passed, 0 not checked", text)
        self.assertIn("[FAIL] Power: under-voltage since boot", text)
        self.assertIn("Memory: 1.2GB available of 4.0GB", text)
        self.assertIn("Piano keys: 13 of 13", text)
        self.assertNotRegex(text, r"[֐-׿]", "in English")
        start = datetime(2026, 10, 1, 8, 0)
        for i in range(12):
            F.save_report(text, {"DATA_DIR": str(data)}, when=start + timedelta(minutes=i))
        files = sorted(p.name for p in (data / "device" / "checks").iterdir())
        self.assertEqual(len(files), 10)
        self.assertEqual(files[0], "2026-10-01-0802.txt")
        self.assertEqual((data / "device" / "checks" / files[-1]).read_text(), text)

    def test_the_summary_order(self):
        items = [F.item(F.NOTE, "a", F.L("chk.ok")), F.item(F.OK, "b", F.L("chk.ok")),
                 F.item(F.PROBLEM, "c", F.L("chk.ok")), F.item(F.SKIP, "d", F.L("chk.ok")), F.item(F.PROBLEM, "e", F.L("chk.ok"))]
        self.assertEqual([i.area for i in F.ordered(items)], ["c", "e", "a"])


class RootActions(unittest.TestCase):
    def setUp(self):
        self.ra = load("ra_check", "device/root_actions.py")
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "data" / "device").mkdir(parents=True)
        (self.tmp / "data" / "device" / "settings.json").write_text(json.dumps({"tg_chats": [111]}))
        for name, val in (("SECRETS", self.tmp / "secrets.env"), ("DATA_DIR", self.tmp / "data"),
                          ("TG_STAMP", self.tmp / "stamp"), ("TG_REPORT_STAMP", self.tmp / "rstamp")):
            p = mock.patch.object(self.ra, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.out, self.calls = [], []

    def p(self, step, state="running", msg=""):
        self.out.append((state, msg))

    def tg(self):
        return mock.patch.object(self.ra, "tg_call", side_effect=lambda t, m, d=None: self.calls.append((t, m, d)))

    def test_kept_answers_true_or_false_only(self):
        self.ra.do_telegram_kept(self.p, {})
        self.assertEqual(json.loads(self.out[-1][1]), {"kept": False})
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        self.ra.do_telegram_kept(self.p, {})
        self.assertEqual(json.loads(self.out[-1][1]), {"kept": True})
        self.assertNotIn(TOKEN, json.dumps(self.out))
        self.assertIn("telegram-kept", self.ra.ACTIONS)

    def args(self, **kw):
        a = {"lang": "en", "problems": 1, "warnings": 1, "passed": 27, "skipped": 0,
             "lines": [{"level": "fail", "text": "Broker: 192.168.1.5:1883 refused " + TOKEN},
                       {"level": "warn", "text": "GPIO: key 5 (G) held down all the time"}],
             "report": "2026-10-27-0914.txt"}
        a.update(kw)
        return a

    def test_the_report_is_sent_with_the_kept_token_in_a_fixed_form(self):
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        with self.tg():
            self.ra.do_telegram_report(self.p, self.args(token="999:ignored"))
        (token, method, data), = self.calls
        self.assertEqual((token, method, data["chat_id"]), (TOKEN, "sendMessage", 111))
        text = data["text"].splitlines()
        self.assertTrue(text[0].startswith("Armonico Pi full check · "))
        self.assertEqual(text[1], "1 problem, 1 warning · 27 passed")
        self.assertEqual(text[2], "✗ Broker: … refused …", "no address and no token in the message")
        self.assertEqual(text[3], "! GPIO: key 5 (G) held down all the time")
        self.assertTrue(text[4].endswith("device/checks/2026-10-27-0914.txt"))
        self.assertNotIn(TOKEN, json.dumps(self.out))

    def test_rate_limited_and_checked(self):
        self.ra.secret_write("TELEGRAM_TOKEN", TOKEN)
        with self.tg():
            self.ra.do_telegram_report(self.p, self.args())
            with self.assertRaises(ValueError) as e:
                self.ra.do_telegram_report(self.p, self.args())
        self.assertEqual(str(e.exception), "too soon")
        os.utime(self.ra.TG_REPORT_STAMP, (time.time() - 11, time.time() - 11))
        for bad in (dict(problems=-1), dict(passed=True), dict(lines=[{"level": "ok", "text": "x"}]),
                    dict(lines=[{"level": "fail", "text": "a\nb"}]), dict(lines=[{"level": "fail", "text": "x"}] * 13),
                    dict(report="../../etc/passwd")):
            with self.subTest(bad=bad), self.tg(), self.assertRaises(ValueError):
                self.ra.do_telegram_report(self.p, self.args(**bad))

    def test_no_token_says_so(self):
        with self.assertRaises(ValueError) as e:
            self.ra.do_telegram_report(self.p, self.args())
        self.assertEqual(str(e.exception), "no-token")


class QuietTestMode(unittest.TestCase):
    def setUp(self):
        self.mod = load("armonico_pi_quiet", "device/armonico_pi.py")
        d = self.d = object.__new__(self.mod.Device)
        d.testing = d.test_quiet = False
        d.alarm = d.song = False
        d.keys_on, d.kb, d.held = True, {"mode": "internal"}, {}
        d.pub, d.sound_note = mock.Mock(), mock.Mock()
        d.at_height, d.base = (lambda n: n), (lambda: 60)

    def test_quiet_names_the_key_and_plays_nothing(self):
        d = self.d
        d.testing, d.test_quiet = True, True
        d.key(4, True)
        d.key(4, False)
        d.pub.assert_called_once_with("piano/device/test/key", "4")
        d.sound_note.assert_not_called()
        d.test_quiet = False                     # the hardware test: the key still sounds there
        d.let_go = {}                            # pressed again later, not within the bounce lockout (bounced)
        d.key(4, True)
        d.sound_note.assert_called_once()

    def test_the_power_button_held_does_not_shut_down_in_quiet(self):
        d = self.d
        d.test_quiet, d.power_down_at = True, 5.0
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(self.mod, "DATA_DIR", tmp):
            d.power_held(5.0)
        self.assertFalse((tmp / "device-request").exists())


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, size=(800, 480), lang="en", **st):
        app = T.make_app(self.mod, size, lang, settings=st)
        app.pubs = []
        app.screen.pub = lambda topic, payload, retain=False: app.pubs.append((topic, payload))
        return app

    def fake_rows(self, app):
        c = app.chk
        c["areas"] = [k for k, _ in F.areas({"DEVICE": "yes"})]
        rows = [[F.item(F.OK, k, F.L("chk.ok"))] for k in c["areas"]]
        rows[0] = [F.item(F.PROBLEM, "services", F.L("chk.r.noAnswer"), todo=F.L("chk.do.services"), fix="services")]
        rows[6] = [F.item(F.PROBLEM, "power", F.L("chk.r.underBoot"), todo=F.L("chk.do.under"))]
        rows[13] = [F.item(F.NOTE, "gpio", F.L("chk.r.held", k=F.key_name(7)), todo=F.L("chk.do.held"), link="test")]
        rows[19] = [F.item(F.OK, "telegram", F.L("chk.r.tgKept"))]
        c["rows"], c["phase"], c["auto_end"] = rows, "auto", None
        return rows

    def through(self, app, upto="summary"):
        """Opens the check with made-up automatic results and goes through the guided steps."""
        app.open_check("settings")
        self.fake_rows(app)
        app.chk_guided_start()
        return app

    def test_the_settings_offer_it_under_system(self):
        app = self.app()
        cells = app.settings_cells()["test"]            # System > Device test: the first card is the full check
        cell = next(c for c in cells if c[0] == app.tr("tm.both"))
        cell[3]()
        self.assertEqual(app.view, "check")
        self.assertEqual(app.chk["phase"], "intro")
        self.assertEqual(app.chk["mode"], "both")

    def test_the_automatic_part_writes_nothing_and_asks_root_only_whether_a_token_is_kept(self):
        app = self.app(tg_done=True, alarm={"on": True, "time": "07:00", "song": "ode_to_joy"})
        data = Path(os.environ["DATA_DIR"])
        before = {p: p.stat().st_mtime for p in data.rglob("*") if p.is_file()}
        asked = []
        from ui import system
        with mock.patch.object(F, "run", return_value=None), mock.patch.object(diag, "run_out", return_value=""), \
                mock.patch.object(F, "reach", return_value=(True, 10)), mock.patch.object(F, "mqtt_retained", return_value="online"), \
                mock.patch.object(F, "wifi_state", return_value={"nm": False}), \
                mock.patch.object(diag, "latest_release", return_value=("v1.0", "")), \
                mock.patch.object(diag, "_mqtt_login", return_value=True), \
                mock.patch.object(system, "request", side_effect=lambda a, *x, **k: (asked.append(a), (True, '{"kept": true}'))[1]), \
                mock.patch.object(diag, "fix") as fix:
            app.open_check("settings")
            app.chk_start()
            for _ in range(400):
                if app.chk["auto_end"] is not None:
                    break
                time.sleep(0.02)
            app.draw()
        self.assertIsNotNone(app.chk["auto_end"])
        self.assertEqual(len(app.chk["rows"]), 21)
        self.assertTrue(all(r for r in app.chk["rows"]))
        self.assertEqual(app.saved, {}, "no settings written")
        self.assertLessEqual(set(asked), {"telegram-kept", "logs-read"}, "root is only asked to read: whether a token is kept, the logs")
        self.assertIn("telegram-kept", asked)
        fix.assert_not_called()
        after = {p: p.stat().st_mtime for p in data.rglob("*") if p.is_file()}
        changed = [p for p in after if before.get(p) != after[p] and "checks" not in p.parts]
        self.assertEqual(changed, [], "no file written")

    def test_the_guided_steps_advance_skip_and_put_everything_back(self):
        app = self.through(self.app())
        app.follow_input()
        self.assertIn(("piano/device/test", "quiet"), app.pubs, "the keys only named while the steps show")
        # 1 touch, in order: a circle out of turn is not taken
        tg = app.touch_targets()
        app.chk_touch(*tg[1])
        self.assertEqual(app.chk["st"].get("corners"), [])
        for p in tg:
            app.chk_touch(*p)
        self.assertEqual(app.chk["res"]["touch"].level, F.OK)
        # a step that ended by itself stays on the screen with its result until ✓ Next
        self.assertEqual(app.chk["step"], 0)
        self.assertEqual(app.chk["st"]["held"][0], F.OK)
        app.draw()
        app.press("ok")
        self.assertEqual(app.chk["step"], 1)
        # 2 keys, from MQTT
        for pc in range(13):
            app.mqtt_event("piano/device/test/key", str(pc))
        self.assertEqual(app.chk["res"]["keys"].level, F.OK)
        app.mqtt_event("piano/device/test/key", "3")             # a key after the result changes nothing
        self.assertEqual(app.chk["step"], 1)
        app.draw()
        app.press("ok")
        # 3 buttons: counted, not acting as the bar
        for name in ("menu", "down", "up", "ok"):
            app.button(name, False)
        self.assertEqual(app.chk["res"]["buttons"].level, F.OK)
        self.assertEqual(app.view, "check")
        self.assertEqual(app.chk["step"], 2)
        app.draw()
        app.press("ok")                                           # ✓ is the bar again: Next
        self.assertEqual(app.chk["step"], 3)
        # 4 the power button: the question, and its Yes shuts nothing down
        with mock.patch.object(app, "root_power") as power:
            app.mqtt_event("piano/device/power", "press")
            self.assertTrue(app.confirm)
            app.answer(True)
        power.assert_not_called()
        self.assertEqual(app.chk["res"]["power"].level, F.OK)
        self.assertEqual(app.chk["res"]["power"].detail[0], "the question appeared, answered Yes; nothing was shut down")
        app.draw()
        app.press("ok")
        # 5, 6 the speakers; 7 skipped
        with mock.patch.object(app, "mixer_ready", return_value=True):
            app.chk_answer(True)
            app.chk_answer(False)
            self.assertEqual(app.chk["res"]["left"].level, F.PROBLEM)
            app.draw()
            app.press("menu")
        self.assertEqual(app.chk["res"]["quality"].level, F.SKIP)
        # 8 a USB keyboard, none connected: passed over by itself, with no result (it is not "not checked")
        self.assertNotIn("usb", app.chk["res"])
        # 9 the brightness: drawn, never saved, back when the step ends
        app.draw()
        self.assertIn(app.chk_light, (100, 75, 50, 25))
        self.assertEqual(app.light_level(), app.chk_light)
        app.press("ok")
        self.assertIsNone(app.chk_light)
        self.assertEqual(app.light_level(), 100)
        self.assertEqual(app.chk["phase"], "summary")
        self.assertEqual(app.saved, {})
        app.go("settings")
        app.follow_input()
        self.assertEqual(next(p for p in reversed(app.pubs) if p[0] == "piano/device/test"), ("piano/device/test", "off"))

    def test_leaving_in_the_middle_restores_test_mode_and_brightness(self):
        app = self.through(self.app())
        while app.chk["step"] < app.chk["steps"].index("bright"):   # no USB keyboard: its step is left out
            app.chk_skip()
        app.draw()
        self.assertIsNotNone(app.chk_light)
        app.follow_input()
        self.assertEqual(app._test_sent, "quiet")
        app.press("menu")                            # ☰ on the brightness step: skip, the last step
        self.assertIsNone(app.chk_light)
        app = self.through(self.app())
        while app.chk["step"] < app.chk["steps"].index("bright"):   # no USB keyboard: its step is left out
            app.chk_skip()
        app.draw()
        app.go("rest")
        self.assertIsNone(app.chk_light)
        app.follow_input()
        self.assertEqual(app._test_sent, "off")
        self.assertEqual(app.chk["phase"], "left")

    def test_a_key_not_pressed_in_30_seconds_does_not_respond(self):
        app = self.through(self.app())
        app.chk_touch(*app.touch_targets()[0])
        app.chk_skip()
        for pc in (0, 2, 4):
            app.chk_key(pc)
        app.chk["st"]["last"] -= 31
        app.draw()
        r = app.chk["res"]["keys"]
        self.assertEqual(r.level, F.PROBLEM)
        self.assertIn("1# (C#)", r.row[0])
        self.assertEqual(r.link, "test")

    def test_the_power_button_outside_its_step_asks_as_always(self):
        app = self.through(self.app())
        with mock.patch.object(app, "root_power") as power:
            app.mqtt_event("piano/device/power", "press")
            app.answer(True)
        power.assert_called_once_with("shutdown")

    def summary(self, app):
        for _ in range(9):
            if app.chk["phase"] == "summary":
                break
            app.chk_skip()
        return app.chk["summary"]

    def test_the_summary_failures_first_then_warnings_then_the_count(self):
        app = self.through(self.app())
        sm = self.summary(app)
        self.assertEqual([i.level for i in sm["list"]], [F.PROBLEM, F.PROBLEM, F.NOTE])
        self.assertEqual(sm["list"][0].area, "services")
        self.assertEqual(sm["n"][F.OK], 18)
        self.assertTrue(sm["tg"])
        app.draw()
        labels = [b[1] for b in app.bar_items]
        self.assertEqual(labels[0], app.tr("done"))
        self.assertEqual(labels[3], app.tr("chk.tg"))
        for _ in range(100):
            if isinstance(app.chk["report"], Path):
                break
            time.sleep(0.02)
        rep = app.chk["report"]
        self.assertTrue(rep.read_text().startswith("Armonico Pi full check"))
        self.assertEqual(rep.parent, Path(os.environ["DATA_DIR"]) / "device" / "checks")

    def test_fix_only_where_the_diagnosis_can(self):
        app = self.through(self.app())
        self.summary(app)
        fixed = []
        with mock.patch.object(app, "chk_fix", side_effect=fixed.append), mock.patch.object(app, "open_test") as hw:
            app.draw()
            for x, y, w, h, fn in list(app.hits):
                if y < 480 - 58:                     # the cards' links, not the bar
                    fn()
        self.assertEqual(fixed, ["services"], "one Fix, for the services")
        hw.assert_called_once()

    def test_telegram_through_root_never_with_the_token(self):
        app = self.through(self.app())
        self.summary(app)
        from ui import system
        sent = []
        app.draw()
        with mock.patch.object(system, "request", side_effect=lambda a, **k: (sent.append((a, k)), (True, ""))[1]), \
                mock.patch("threading.Thread", side_effect=lambda target, daemon=True, name=None: mock.Mock(start=target)):
            app.press("ok")
        (action, args), = sent
        self.assertEqual(action, "telegram-report")
        self.assertNotIn("token", args)
        self.assertEqual((args["problems"], args["warnings"]), (2, 1))
        self.assertEqual(args["lines"][0], {"level": "fail", "text": "Services: no answer"})

    def test_no_telegram_button_without_a_kept_token(self):
        app = self.app()
        app.open_check("settings")
        self.fake_rows(app)
        app.chk["rows"][19] = [F.item(F.SKIP, "telegram", F.L("chk.r.tgNone"))]
        app.chk_guided_start()
        self.summary(app)
        app.draw()
        self.assertIsNone(app.bar_items[3][2])

    def test_the_offer_after_an_update_shows_once(self):
        data = Path(os.environ["DATA_DIR"]) / "device"
        (data / "check-offer.json").unlink(missing_ok=True)
        app = self.app()
        app.chk_offer_load()
        self.assertTrue(app.chk_offer, "set up before, no record: an update")
        app.go("rest")
        app.draw()
        note = app.chk_offer_note()
        self.assertIn("full check", note[0])
        note[1]()
        self.assertEqual(app.view, "check")
        app2 = self.app()
        app2.chk_offer_load()
        self.assertFalse(app2.chk_offer, "once for each version")
        (data / "check-offer.json").write_text(json.dumps({"version": "0.9", "done": True}))
        app3 = self.app()
        app3.chk_offer_load()
        self.assertTrue(app3.chk_offer, "a new version offers it again")
        app3.go("settings")
        app3.open_check("settings")
        self.assertFalse(app3.chk_offer, "running it from the settings answers it too")
        (data / "check-offer.json").unlink()
        app4 = self.app()
        app4.st["setup_done"] = False
        app4.chk_offer_load()
        self.assertFalse(app4.chk_offer, "a new install is not an update")

    def screens(self, app):
        yield "intro", lambda: app.open_check("settings")

        def auto():
            app.open_check("settings")
            self.fake_rows(app)
            app.chk["rows"] = app.chk["rows"][:14] + [None] * 7
        yield "auto", auto

        def step(n, **st):
            def go():
                app.keyboards = ["Digital Keyboard"]       # before the check opens: with none, the USB step is left out
                app.open_check("settings")
                self.fake_rows(app)
                with mock.patch.object(app, "mixer_ready", return_value=True), \
                        mock.patch.object(app, "chk_repeat"), mock.patch.object(app, "chk_phrase"):
                    app.chk_guided_start()
                    while app.chk["step"] < n:
                        app.chk_skip()
                app.chk["st"].update(st)
            return go
        for n in range(9):
            yield f"step{n + 1}", step(n)
        yield "step8:key", step(7, usb=("Digital Keyboard", 61))
        yield "step7:nosamples", step(6, nosamples=True)

        def summary():
            app.open_check("settings")
            self.fake_rows(app)
            app.chk_guided_start()
            self.summary(app)
        yield "summary", summary

        def offer():
            app.chk_offer = True
            app.go("rest")
        yield "offer", offer

    def test_every_new_screen_draws_in_both_languages_and_sizes_with_nothing_cut(self):
        from ui.texts import T as texts
        from ui import canvas as C
        fixed = {v for pair in texts.values() if isinstance(pair, tuple) for v in pair}
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                app = self.app(size, lang)
                app.short_missed = set()
                for name, open_it in self.screens(app):
                    with self.subTest(size=size, lang=lang, screen=name):
                        open_it()
                        got, orig = [], C.Canvas.text

                        def rec(cv, s, x, y, *a, **k):
                            w = orig(cv, s, x, y, *a, **k)
                            got.append((str(s), x, w, cv.mirrored))
                            return w
                        with mock.patch.object(C.Canvas, "text", rec):
                            app.draw()
                        self.assertTrue(app.bar_items)
                        for s, x, w, mirrored in got:
                            self.assertNotIn("chk.", s, "every text has its words")
                            self.assertFalse(s.startswith("…"), f"cut: {s}")
                        joined = " ".join(s for s, *_ in got)
                        if lang == "he" and name != "offer":
                            self.assertRegex(joined, r"[֐-׿]")
                cut = {m[1] for m in app.short_missed if m[1] in fixed}
                self.assertEqual(cut, set(), f"{lang}: give these a shorter wording in texts.SHORT")

    def test_nothing_runs_past_the_screen(self):
        """Every text of the new screens inside the plan's 800 wide area (on the rings' screen, the physical edges)."""
        from ui import canvas as C
        for lang in ("en", "he"):
            app = self.app((800, 480), lang)
            for name, open_it in self.screens(app):
                open_it()
                boxes, orig = [], C.Canvas.text

                def rec(cv, s, x, y, size, color, bold=False, align="left"):
                    w = orig(cv, s, x, y, size, color, bold, align)
                    left = x if align == "left" else x - w if align == "right" else x - w / 2
                    boxes.append((s, left, left + w))
                    return w
                with mock.patch.object(C.Canvas, "text", rec):
                    app.draw()
                for s, a, b in boxes:
                    with self.subTest(lang=lang, screen=name, text=s):
                        self.assertGreaterEqual(a, -1)
                        self.assertLessEqual(b, 801)


class Cli(unittest.TestCase):
    def run_check(self, items):
        cli = load("armonico_cli_check", "cli/armonico.py")
        data = Path(tempfile.mkdtemp())
        out = io.StringIO()
        with mock.patch.object(cli, "settings", return_value={"DEVICE": "yes", "DATA_DIR": str(data)}), \
                mock.patch.object(F, "run_all", return_value=[("power", items)]), \
                mock.patch("sys.stdout", out):
            try:
                cli.main(["check"])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), data

    def test_exit_code_and_report(self):
        bad = [F.item(F.PROBLEM, "power", F.L("chk.r.underBoot"), F.L("chk.d.underBoot", c="x"), F.L("chk.do.under"))]
        code, text, data = self.run_check(bad)
        self.assertEqual(code, 1)
        self.assertIn("1 failed", text)
        self.assertIn("A weak power supply", text)
        reports = list((data / "device" / "checks").glob("*.txt"))
        self.assertEqual(len(reports), 1)
        self.assertIn("Guided steps\n  not run", reports[0].read_text())
        code, text, _ = self.run_check([F.item(F.OK, "power", F.L("chk.ok"))])
        self.assertEqual(code, 0)

    def test_in_the_help(self):
        cli = load("armonico_cli_check_help", "cli/armonico.py")
        self.assertIn("check", cli.COMMANDS)
        self.assertIn("  check ", cli.HELP)
        self.assertIn("armonico check", (ROOT / "docs" / "commands.md").read_text())


if __name__ == "__main__":
    unittest.main()


class TextKeys(unittest.TestCase):
    def test_no_text_key_is_written_twice(self):
        # a key written twice in one block keeps only its last wording: the broker's "offline" once
        # showed the internet check's "no network" text
        import ast
        tree = ast.parse((Path(__file__).resolve().parents[1] / "device" / "ui" / "texts.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                self.assertEqual(sorted({k for k in keys if keys.count(k) > 1}), [])

class IoAdvice(unittest.TestCase):
    def test_errors_on_a_usb_drive_do_not_say_replace_the_sd_card(self):
        import device.fullcheck as F
        usb = ["blk_update_request: I/O error, dev sda, sector 123", "Buffer I/O error on dev sda1"]
        sd = ["mmc0: error -110 whilst initialising", "blk_update_request: I/O error, dev mmcblk0"]
        self.assertEqual(F.io_todo_key(usb), "chk.do.ioUsb")
        self.assertEqual(F.io_todo_key(sd), "chk.do.io")
        self.assertEqual(F.io_todo_key(usb + sd), "chk.do.io")

