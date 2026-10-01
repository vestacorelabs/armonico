"""Root requests called off by their own ticket, Bluetooth actions that wait their turn, the Bluetooth log that could
not be read, and the screen recording: tied to the program, never cut short, its parts its own."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, str(ROOT / "device"))

try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None

from ui import system  # noqa: E402
from ui import recorder as R  # noqa: E402
import fullcheck as F  # noqa: E402


class RootTicket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.p = [mock.patch.object(system, "REQUEST", d / "device-request"),
                  mock.patch.object(system, "STATUS", d / "device-request.status"),
                  mock.patch.object(system, "CANCEL", d / "device-cancel"),
                  # its own lock: a request left running by another test holds the real one
                  mock.patch.object(system, "_ONE_AT_A_TIME", threading.Lock())]
        for p in self.p:
            p.start()
        self.d = d

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.tmp.cleanup()

    def test_a_request_called_off_while_it_waits_never_runs(self):
        t = system.Ticket()
        out = {}
        lock = system._ONE_AT_A_TIME
        lock.acquire()                                     # another request holds the root unit
        try:
            th = threading.Thread(target=lambda: out.update(r=system.request("bt-pair", timeout=5, ticket=t, mac="x")))
            th.start()
            time.sleep(0.1)
            self.assertTrue(t.waiting())
            system.cancel(t)
            self.assertFalse((self.d / "device-cancel").exists(), "nothing running of its own to cancel")
        finally:
            lock.release()
        th.join(3)
        self.assertEqual(out["r"], (False, "cancelled"))
        self.assertFalse((self.d / "device-request").exists(), "it was never handed to the root unit")

    def test_cancel_names_only_its_own_running_request(self):
        mine, other = system.Ticket(), system.Ticket()
        system._CURRENT_RID["rid"] = other.rid
        other.started = time.monotonic()
        mine.started = time.monotonic() - 5
        system.cancel(mine)                                # mine is over: another request runs now
        self.assertFalse((self.d / "device-cancel").exists())
        system._CURRENT_RID["rid"] = mine.rid
        system.cancel(mine)
        self.assertEqual((self.d / "device-cancel").read_text(), mine.rid)
        system._CURRENT_RID["rid"] = ""

    def test_the_ticket_says_when_it_started(self):
        t = system.Ticket()
        with mock.patch.object(system, "_request", return_value=(True, "")) as rq:
            self.assertEqual(system.bt("status", ticket=t), (True, {}))
        self.assertIsNotNone(t.started)
        self.assertTrue(t.ended)
        self.assertNotIn("ticket", rq.call_args.args[4])   # not handed to root as an argument
        self.assertEqual(rq.call_args.args[5], t.rid)


class BtLog(unittest.TestCase):
    def test_an_unreadable_journal_is_not_checked(self):
        with mock.patch.object(F, "run", return_value=(0, "", "No journal files were found.\n")):
            self.assertIsNone(F.bt_log({}))
        with mock.patch.object(F, "run", return_value=None):
            self.assertIsNone(F.bt_log({}))
        row = F.bt_items({}, {}, names=["bluez_output.AA.1"], errs=0, log=None)[0]
        self.assertEqual(row.level, F.SKIP)
        self.assertIn("systemd-journal", " ".join(row.detail) if isinstance(row.detail, tuple) else str(row.detail))

    def test_a_readable_journal_counts(self):
        with mock.patch.object(F, "run", return_value=(0, "bluetoothd[1]: Failed to connect\n", "")):
            self.assertEqual(F.bt_log({}), 3)
        row = F.bt_items({}, {}, names=["bluez_output.AA.1"], errs=0, log=0)[0]
        self.assertEqual(row.level, F.OK)
        row = F.bt_items({}, {}, names=["bluez_output.AA.1"], errs=4, log=None, wifi={})[0]
        self.assertEqual(row.level, F.NOTE)


class RecorderParts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_feed_and_stop_never_meet_a_half_made_recording(self):
        r = R.Recorder(self.folder)
        r.proc = object()                                  # proc there, size not yet: feed takes nothing
        r.feed(mock.Mock(), mock.Mock())
        r.stop()                                           # no ending event yet: nothing to set, no error
        self.assertIsNone(r.frame)

    def test_no_new_recording_while_the_last_one_is_put_together(self):
        r = R.Recorder(self.folder)
        r.saving = True
        self.assertEqual(r.start((800, 480)), "already recording")

    def test_each_recording_has_part_names_of_its_own(self):
        r = R.Recorder(self.folder)
        made = []
        with mock.patch.object(R, "find_mic", return_value=(None, "")), \
                mock.patch.object(R, "device_monitor", return_value="x.monitor"), \
                mock.patch.object(R.subprocess, "Popen", side_effect=FileNotFoundError), \
                mock.patch.object(R, "video_cmd", side_effect=lambda size, out, *rest: (made.append(out), ["ffmpeg"])[1]):
            self.assertEqual(r.start((800, 480)), "ffmpeg is missing")
            self.assertEqual(r.start((800, 480)), "ffmpeg is missing")
        self.assertEqual(len(set(made)), 2)
        self.assertFalse(r.saving)

    def test_a_failed_start_puts_the_headset_back(self):
        r = R.Recorder(self.folder)
        with mock.patch.object(R, "find_mic", return_value=("bluez_input.AA.0", "a Bluetooth headset")), \
                mock.patch.object(R, "device_monitor", return_value="x.monitor"), \
                mock.patch.object(R.subprocess, "Popen", side_effect=FileNotFoundError), \
                mock.patch.object(R, "restore_bt") as back:
            r.start((800, 480))
        back.assert_called()

    def test_find_mic_puts_the_headset_back_when_no_microphone_came(self):
        with mock.patch.object(R, "_usb_input_profile", return_value=False), \
                mock.patch.object(R, "_mic_source", return_value=None), \
                mock.patch.object(R.time, "sleep"), mock.patch.object(R, "restore_bt") as back:
            self.assertEqual(R.find_mic(), (None, ""))
        back.assert_called_once()

    def test_the_mux_keeps_every_sound_whole(self):
        cmd = R.mux_cmd("v.mkv", 100.0, [("d.mka", 100.0, "Armonico"), ("m1.mka", 101.0, "Microphone"),
                                         ("m2.mka", 160.0, "Microphone")], "o.part")
        s = " ".join(cmd)
        self.assertNotIn("-shortest", cmd)                 # an unplugged microphone no longer cuts the file
        self.assertIn("adelay=60000:all=1", s)             # the microphone's second part at its own time
        self.assertIn("amix=inputs=2:duration=longest:normalize=0", s)
        self.assertNotIn("asplit", s)
        self.assertEqual(cmd.count("-map"), 2)
        self.assertIn("aresample=48000:async=1", s)

    def test_parts_and_ffmpegs_left_by_a_program_that_died_are_cleaned(self):
        part = self.folder / ".rec-dead0000-device-1.mka"
        part.write_bytes(b"x")
        (self.folder / "armonico-2026-10-27-120509.mkv.part").write_bytes(b"x")
        keep = self.folder / "armonico-2026-10-27-120509.mkv"
        keep.write_bytes(b"x")
        p = subprocess.Popen(["ffmpeg", "-c", "import time; time.sleep(30)", str(part)], executable=sys.executable)
        try:
            R.clean_stale(self.folder)
            self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertFalse(part.exists())
        self.assertEqual(sorted(f.name for f in self.folder.iterdir()), [keep.name])

    @unittest.skipUnless(sys.platform.startswith("linux") and R._PRCTL is not None, "Linux prctl")
    def test_an_ffmpeg_ends_with_the_thread_that_started_it(self):
        box = {}

        def starter():
            box["p"] = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], preexec_fn=R._child())
        th = threading.Thread(target=starter)
        th.start()
        th.join()
        p = box["p"]
        try:
            self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)
        finally:
            if p.poll() is None:
                p.kill()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is not installed")
class RecorderRun(unittest.TestCase):
    """A short real recording: a synthetic sound in place of PipeWire."""

    def test_saving_lasts_until_the_file_is_put_together(self):
        with tempfile.TemporaryDirectory() as d:
            r = R.Recorder(d)

            def sound(src, out):
                return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi", "-i",
                        "sine=f=440:sample_rate=48000", "-c:a", "flac", str(out)]
            pg = mock.Mock()
            pg.image.tobytes.side_effect = lambda s, fmt: b"\x80" * (64 * 48 * 3)
            surf = mock.Mock()
            surf.get_size.return_value = (64, 48)
            with mock.patch.object(R, "find_mic", return_value=(None, "")), \
                    mock.patch.object(R, "device_monitor", return_value="x.monitor"), \
                    mock.patch.object(R, "audio_cmd", side_effect=sound):
                self.assertEqual(r.start((64, 48)), "")
                self.assertTrue(r.on and r.saving)
                end = time.monotonic() + 1.5
                while time.monotonic() < end:
                    r.feed(surf, pg)
                    time.sleep(0.02)
                r.stop()
                self.assertFalse(r.on)
                self.assertEqual(r.start((64, 48)), "already recording", "the last one is still being put together")
                end = time.monotonic() + 60
                while r.done is None and time.monotonic() < end:
                    time.sleep(0.05)
            out, ok = r.done
            self.assertTrue(ok)
            self.assertFalse(r.saving)
            self.assertTrue(out.exists())
            self.assertEqual(list(Path(d).glob(".rec-*")), [], "the parts are removed once the file is made")


@unittest.skipIf(pygame is None, "no pygame")
class BtQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self):
        app = make_app(self.mod, lang="en")
        app.snd_list = [("aux", "AUX")]
        app.bt.update(hw=True, saved=[{"mac": "AA:BB:CC:DD:EE:01", "name": "Sony", "connected": False}], found=[])
        return app

    @staticmethod
    def wait(app):
        end = time.time() + 3
        while time.time() < end and app.bt["busy"]:
            time.sleep(0.01)
        time.sleep(0.05)

    def test_a_tap_while_busy_waits_its_turn_and_then_runs(self):
        app = self.app()
        gate = threading.Event()
        calls = []

        def fake(action, timeout=60, ticket=None, **args):
            calls.append(action)
            if action == "scan":
                gate.wait(3)
            return True, {"hw": True}
        with mock.patch("ui.views_bt.system.bt", side_effect=fake):
            app.bt_scan()
            time.sleep(0.05)
            self.assertTrue(app.bt["busy"])
            app.bt_toggle(app.bt["saved"][0])
            self.assertEqual(app.bt["act"]["AA:BB:CC:DD:EE:01"], app.tr("bt.waiting"))
            app.bt_forget(app.bt["saved"][0])
            gate.set()
            self.wait(app)
        self.assertEqual([c for c in calls if c != "status"], ["scan", "connect", "forget"])
        self.assertEqual(app.bt["act"], {}, "the row shows the device again")
        self.assertFalse(app.bt["busy"])

    def test_cancel_drops_a_waiting_tap_and_clears_its_row(self):
        app = self.app()
        gate = threading.Event()
        calls = []

        def fake(action, timeout=60, ticket=None, **args):
            calls.append(action)
            if action == "scan":
                gate.wait(3)
            return True, {"hw": True}
        with mock.patch("ui.views_bt.system.bt", side_effect=fake), \
                mock.patch("ui.views_bt.system.cancel_current") as everyone:
            app.bt_scan()
            time.sleep(0.05)
            app.bt_toggle(app.bt["saved"][0])
            app.bt_cancel()
            gate.set()
            self.wait(app)
        everyone.assert_not_called()                        # another page's root action is left alone
        self.assertNotIn("connect", calls)
        self.assertEqual(app.bt["act"], {})

    def test_a_failed_status_says_why_and_keeps_asking(self):
        app = self.app()
        app.bt.update(hw=None)
        with mock.patch("ui.views_bt.system.bt", return_value=(False, "no-root")):
            app.bt_refresh()
            self.wait(app)
        self.assertEqual(app.bt["status_err"], "no-root")
        app.bt["status_at"] = 1e12
        app.go("bt")
        app.draw()
        app.bt["status_at"] = 0.0
        with mock.patch("ui.views_bt.system.bt", return_value=(True, {"hw": True, "saved": []})):
            app.bt_follow()
            self.wait(app)
        self.assertEqual(app.bt["status_err"], "")
        self.assertTrue(app.bt["hw"])


@unittest.skipIf(pygame is None, "no pygame")
class WifiTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_cancel_drops_the_waiting_connection_and_the_clock_waits_for_its_turn(self):
        app = make_app(self.mod, lang="en")
        got = {}

        def fake(action, timeout=60, ticket=None, **args):
            got["ticket"] = ticket
            time.sleep(0.2)
            return False, "cancelled"
        with mock.patch("ui.views_wifi.system.wifi", side_effect=fake):
            app.wifi_connect("Home", "password1", False)
            time.sleep(0.05)
            t = app.wf["ticket"]
            self.assertIs(got["ticket"], t)
            self.assertAlmostEqual(app.wifi_started(), time.monotonic(), delta=0.5)   # not started: no clock yet
            app.go("wifi")
            app.draw()
            app.wifi_cancel()
            self.assertTrue(t.cancelled)
        self.assertEqual(app.wf["screen"], "list")


if __name__ == "__main__":
    unittest.main()


class RecToggle(unittest.TestCase):
    """The recording's saving flag is up for the whole recording, so the toggle must look at on first:
    with saving checked first, a running recording could not be stopped (reported on armonico_99)."""

    def test_a_running_recording_stops_even_while_saving_is_up(self):
        from ui.views_settings import SettingsViews
        calls = []

        class Rec:
            on, saving = True, True

        class App:
            srec = Rec()
            screen_rec_stop = lambda self: calls.append("stop")
            toast = lambda self, *a, **k: calls.append("toast")
            tr = lambda self, k, **kw: k
        SettingsViews.screen_rec_toggle(App())
        self.assertEqual(calls, ["stop"])

    def test_a_recording_still_put_together_says_so(self):
        from ui.views_settings import SettingsViews
        calls = []

        class Rec:
            on, saving = False, True

        class App:
            srec = Rec()
            screen_rec_stop = lambda self: calls.append("stop")
            toast = lambda self, *a, **k: calls.append("toast")
            tr = lambda self, k, **kw: k
        SettingsViews.screen_rec_toggle(App())
        self.assertEqual(calls, ["toast"])


class WifiRestForBluetooth(unittest.TestCase):
    """Wi-Fi shares the Pi's chip and antenna with Bluetooth: it rests while a Bluetooth output plays on a
    device with a network cable, and comes back after."""

    def app(self, rested=False):
        from ui.views_settings import SettingsViews
        from ui import audio

        class App(SettingsViews):
            def __init__(s):
                s.st, s.calls = {"wifi_rested": rested}, []
            def save(s, **kw):
                s.st.update(kw)
            def toast(s, *a, **k):
                s.calls.append("toast")
            def tr(s, k, **kw):
                return k
        return App(), audio.BT + "bluez_output.AA_BB_CC_DD_EE_01.1"

    def test_rests_on_a_wired_device_and_comes_back(self):
        a, bt = self.app()
        with mock.patch.object(system, "radio_state", return_value="on"), \
                mock.patch.object(system, "wired_up", return_value=True), \
                mock.patch.object(system, "radio_set", return_value=(True, "")) as rs:
            a.bt_wifi_rest([bt], {bt: "Headphones"})
        rs.assert_called_once_with("wifi", False)
        self.assertTrue(a.st["wifi_rested"])
        with mock.patch.object(system, "radio_state", return_value="off"), \
                mock.patch.object(system, "radio_set", return_value=(True, "")) as rs:
            a.bt_wifi_rest(["aux"], {"aux": "AUX"})
        rs.assert_called_once_with("wifi", True)
        self.assertFalse(a.st["wifi_rested"])

    def test_the_wifi_comes_back_when_the_cable_is_pulled_while_bluetooth_plays(self):
        a, bt = self.app(rested=True)
        with mock.patch.object(system, "radio_state", return_value="off"), \
                mock.patch.object(system, "wired_up", return_value=False), \
                mock.patch.object(system, "radio_set", return_value=(True, "")) as rs:
            a.bt_wifi_rest([bt], {bt: "Headphones"})
        rs.assert_called_once_with("wifi", True)
        self.assertFalse(a.st["wifi_rested"])

    def test_never_without_a_cable(self):
        a, bt = self.app()
        with mock.patch.object(system, "radio_state", return_value="on"), \
                mock.patch.object(system, "wired_up", return_value=False), \
                mock.patch.object(system, "radio_set") as rs:
            a.bt_wifi_rest([bt], {bt: "Headphones"})
        rs.assert_not_called()
        self.assertFalse(a.st["wifi_rested"])


class OutputLabelAway(unittest.TestCase):
    """A chosen output that is not connected now showed as "USB", Bluetooth headphones included."""

    def label(self, keys, snd_list, known):
        from ui.app import App
        from ui import audio

        class A:
            output = "mix"
            st = {"snd_names": known}
            snd_list = None
            output_label = App.output_label
            def output_keys(self):
                return keys
            def hdmi_here(self):
                return False
            def tr(self, k, **kw):
                return "{n} (not connected)".format(**kw) if k == "snd.away" else k
        a = A()
        a.snd_list = snd_list
        return a.output_label()

    def test_a_bluetooth_device_away_keeps_its_name_and_kind(self):
        from ui import audio
        bt = audio.BT + "bluez_output.AA_BB_CC_DD_EE_01.1"
        self.assertEqual(self.label(["aux", bt], [("aux", "AUX")], {bt: "Sony WH-1000XM4"}),
                         "Sony WH-1000XM4 (not connected)")
        self.assertEqual(self.label([bt], [("aux", "AUX")], {}), "Bluetooth (not connected)")

    def test_a_connected_device_shows_its_name(self):
        from ui import audio
        bt = audio.BT + "bluez_output.AA_BB_CC_DD_EE_01.1"
        self.assertEqual(self.label([bt], [("aux", "AUX"), (bt, "Speaker")], {}), "Speaker")
