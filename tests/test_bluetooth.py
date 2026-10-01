"""Bluetooth sound on the device: the root actions against a stand-in bluetoothctl, the output keys
in audio.py, the Bluetooth page and its place in the Output list, the dropout check in the full check, and what
the installer and WirePlumber are told. No Bluetooth hardware is needed or touched.

Pairing answers what the device asks (a PIN code, a passkey to confirm), and a pairing that
does not finish is called off and the device forgotten, so it is free again for a phone.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from test_device_ui import load_screen, make_app, pygame
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "tests"))


FAKE_CTL = r'''#!/usr/bin/env python3
import sys
INFO = {
 "AA:BB:CC:DD:EE:01": "\tName: Sony WH-1000XM4\n\tAlias: Sony WH-1000XM4\n\tPaired: yes\n\tTrusted: yes\n\tConnected: %s\n\tIcon: audio-headset\n",
 "11:22:33:44:55:66": "\tName: JBL Flip 5\n\tAlias: JBL Flip 5\n\tPaired: no\n\tTrusted: no\n\tConnected: no\n\tIcon: audio-speaker\n",
 "22:22:33:44:55:77": "\tName: Fitness Band\n\tAlias: Fitness Band\n\tPaired: no\n\tTrusted: no\n\tConnected: no\n\tIcon: input-gaming\n",
 "33:22:33:44:55:88": "\tName: 33-22-33-44-55-88\n\tAlias: 33-22-33-44-55-88\n\tPaired: no\n\tTrusted: no\n\tConnected: no\n\tIcon: audio-card\n",
}
import os
connected = "no"
for line in sys.stdin:
    c = line.strip()
    if os.environ.get("BTLOG"):
        open(os.environ["BTLOG"], "a").write(c + "\n")
    if c == "quit":
        break
    if c == "show":
        print("Controller 00:11:22:33:44:55 (public)\n\tPowered: %s\n\tDiscoverable: no" % ("no" if os.environ.get("BTSTUCK") else "yes"))
    elif c == "power on" and os.environ.get("BTSTUCK"):
        print("Failed to set power on: org.bluez.Error.Failed")
    elif c == "power on":
        print("Changing power on succeeded")
    elif c == "scan on":
        print("Discovery started")
        print("[NEW] Device 11:22:33:44:55:66 JBL Flip 5")
        print("[CHG] Device 11:22:33:44:55:66 RSSI: -60")
    elif c == "scan off":
        print("Discovery stopped")
    elif c == "devices":
        print("Device AA:BB:CC:DD:EE:01 Sony WH-1000XM4")
        print("Device 11:22:33:44:55:66 JBL Flip 5")
        print("Device 22:22:33:44:55:77 Fitness Band")
        print("Device 33:22:33:44:55:88 33-22-33-44-55-88")
    elif c.startswith("info "):
        mac = c[5:]
        text = INFO.get(mac)
        print((text % connected if text and "%s" in text else text) if text else "Device %s not available" % mac)
    elif c.startswith("pair "):
        print("Attempting to pair with " + c[5:])
        print("Pairing successful")
    elif c.startswith("trust "):
        print("Changing %s trust succeeded" % c[6:])
    elif c.startswith("connect "):
        mac = c[8:]
        if mac == "99:99:99:99:99:99":
            print("Failed to connect: org.bluez.Error.Failed br-connection-page-timeout")
        elif mac == "55:55:55:55:55:55":
            print("Failed to connect: org.bluez.Error.InProgress br-connection-busy")
        elif mac == "77:77:77:77:77:77":
            print("Device 77:77:77:77:77:77 not available")
        else:
            connected = "yes"
            print("Connection successful")
    elif c.startswith("disconnect "):
        connected = "no"
        print("Successful disconnected")
    elif c.startswith("remove "):
        print("Device has been removed")
    sys.stdout.flush()
'''


def load_actions():
    import importlib.util
    spec = importlib.util.spec_from_file_location("armonico_root_actions_bt_test", ROOT / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, step, state="running", msg=""):
        self.calls.append((step, state, msg))

    def data(self):
        done = [c for c in self.calls if c[1] == "done"]
        return json.loads(done[-1][2]) if done else None


class Parsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ra = load_actions()
        cls.ra.BT_READY_WAIT = 0.1

    def test_an_address_is_checked_and_nothing_else_reaches_bluetoothctl(self):
        self.assertEqual(self.ra.check_mac("aa:bb:cc:dd:ee:01"), "AA:BB:CC:DD:EE:01")
        for bad in ("", None, "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:0G", "AA:BB:CC:DD:EE:01; quit", "AA:BB:CC:DD:EE:01\nquit",
                    "AA-BB-CC-DD-EE-01", "../../etc"):
            with self.assertRaises(ValueError):
                self.ra.check_mac(bad)

    def test_devices_and_info(self):
        t = "[NEW] Device AA:BB:CC:DD:EE:01 Sony WH\nDevice 11:22:33:44:55:66 JBL Flip 5\n[CHG] Device AA:BB:CC:DD:EE:01 RSSI: -50\n"
        self.assertEqual(self.ra.bt_devices(t), {"AA:BB:CC:DD:EE:01": "Sony WH", "11:22:33:44:55:66": "JBL Flip 5"})
        i = self.ra.bt_info("\tName: X\n\tAlias: Buds\n\tIcon: audio-headphones\n\tPaired: yes\n\tConnected: no\n")
        self.assertTrue(i["audio"] and i["paired"] and not i["connected"])
        self.assertEqual(i["name"], "Buds")
        self.assertEqual(self.ra.bt_info("\tAlias: 33-22-33-44-55-88\n")["name"], "", "an unnamed device has no name")
        self.assertTrue(self.ra.bt_info("\tUUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)\n")["audio"])

    def test_the_failures_have_words(self):
        r = self.ra.bt_reason
        self.assertEqual(r("Failed to connect: org.bluez.Error.Failed br-connection-page-timeout"), "unreachable")
        self.assertEqual(r("Failed to pair: org.bluez.Error.AuthenticationFailed"), "auth")
        self.assertEqual(r("Failed to set power on: org.bluez.Error.Blocked"), "blocked")
        self.assertEqual(r("No default controller available"), "no-adapter")
        self.assertEqual(r("Failed to connect: org.bluez.Error.InProgress"), "busy")
        self.assertEqual(r("Failed to connect: org.bluez.Error.AlreadyConnected"), "")
        self.assertEqual(r("something odd"), "failed")


class Actions(unittest.TestCase):
    """The root actions run against a stand-in bluetoothctl on PATH."""

    @classmethod
    def setUpClass(cls):
        cls.ra = load_actions()
        cls.ra.BT_READY_WAIT = 0.1
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "bluetoothctl"
        path.write_text(FAKE_CTL)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        cls.env = mock.patch.dict(os.environ, {"PATH": cls.tmp.name + os.pathsep + os.environ.get("PATH", "")})
        cls.env.start()
        cls.ra.BT_SCAN = (1, 20)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def test_status_lists_the_saved_devices_only(self):
        p = Recorder()
        self.ra.do_bt_status(p, {})
        d = p.data()
        self.assertTrue(d["hw"])
        self.assertEqual([x["mac"] for x in d["saved"]], ["AA:BB:CC:DD:EE:01"])
        self.assertEqual(d["saved"][0]["name"], "Sony WH-1000XM4")

    def test_a_chip_that_stopped_answering_is_said_and_not_shown_as_switched_off(self):
        # dmesg on the Pi: "hci0: hardware error 0x00", then the reset timing out; bluetoothd: Powered: no, and
        # power on fails although rfkill does not hold it
        p = Recorder()
        with mock.patch.dict(os.environ, {"BTSTUCK": "1"}), mock.patch.object(self.ra, "radio_blocked", return_value=False):
            self.ra.do_bt_status(p, {})
        self.assertEqual(p.data(), {"hw": False, "why": "stuck", "saved": []})

    def test_a_scan_finds_sound_devices_with_names_only(self):
        p = Recorder()
        self.ra.do_bt_scan(p, {"seconds": 1})
        d = p.data()
        # sound devices first (one that says it plays sound but has no name is listed too, with an empty name),
        # then a band marked "other"
        self.assertEqual([x["name"] for x in d["found"]], ["JBL Flip 5", "", "Fitness Band"])
        self.assertEqual([x["kind"] for x in d["found"]], ["audio", "audio", "other"])
        self.assertGreaterEqual(d["seen"], d["named"])
        self.assertEqual([x["mac"] for x in d["saved"]], ["AA:BB:CC:DD:EE:01"])
        with self.assertRaises(ValueError):
            self.ra.do_bt_scan(Recorder(), {"seconds": 999})
        with self.assertRaises(ValueError):
            self.ra.do_bt_scan(Recorder(), {"seconds": True})

    def test_info_is_read_to_its_end_when_the_lines_arrive_slowly(self):
        """Connected: comes before Icon in the stand-in (and before the UUID lines in BlueZ): the lines after it
        arrive a moment later and still count."""
        slow = Path(self.tmp.name) / "slow"
        slow.mkdir(exist_ok=True)
        ctl = slow / "bluetoothctl"
        ctl.write_text(r'''#!/usr/bin/env python3
import sys, time
for line in sys.stdin:
    c = line.strip()
    if c == "quit":
        break
    if c.startswith("info "):
        print("\tName: Slow Speaker\n\tAlias: Slow Speaker\n\tPaired: no\n\tTrusted: no\n\tConnected: no")
        sys.stdout.flush()
        time.sleep(0.03)
        print("\tIcon: audio-speaker")
    sys.stdout.flush()
''')
        ctl.chmod(ctl.stat().st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"PATH": str(slow) + os.pathsep + os.environ.get("PATH", "")}):
            c = self.ra.BtCtl()
            try:
                i = self.ra.bt_look(c, "AA:BB:CC:DD:EE:01")
            finally:
                c.close()
        self.assertEqual(i["name"], "Slow Speaker")
        self.assertTrue(i["audio"], "the icon line after Connected: was missed")

    def test_pair_connect_disconnect_forget(self):
        p = Recorder()
        self.ra.do_bt_pair(p, {"mac": "11:22:33:44:55:66"})
        self.assertEqual(p.data()["mac"], "11:22:33:44:55:66")
        p = Recorder()
        self.ra.do_bt_connect(p, {"mac": "AA:BB:CC:DD:EE:01"})
        self.assertTrue(p.data()["connected"])
        p = Recorder()
        self.ra.do_bt_disconnect(p, {"mac": "AA:BB:CC:DD:EE:01"})
        self.assertFalse(p.data()["connected"])
        p = Recorder()
        self.ra.do_bt_forget(p, {"mac": "AA:BB:CC:DD:EE:01"})
        self.assertEqual(p.data()["mac"], "AA:BB:CC:DD:EE:01")

    def log(self):
        f = Path(self.tmp.name) / "cmds.log"
        f.write_text("")
        return f, mock.patch.dict(os.environ, {"BTLOG": str(f)})

    def test_a_pairing_is_kept_when_the_first_connection_does_not_finish(self):
        """BlueZ answers InProgress while the headset is connecting itself after pairing: the pairing stays, the
        device is trusted before the pairing, and nothing is removed."""
        f, env = self.log()
        with env:
            p = Recorder()
            self.ra.do_bt_pair(p, {"mac": "55:55:55:55:55:55"})
        cmds = f.read_text().splitlines()
        self.assertEqual(p.data()["connected"], False)
        self.assertLess(cmds.index("trust 55:55:55:55:55:55"), cmds.index("pair 55:55:55:55:55:55"))
        self.assertNotIn("remove 55:55:55:55:55:55", cmds)
        self.assertNotIn("cancel-pairing 55:55:55:55:55:55", cmds)

    def test_a_disconnect_untrusts_first(self):
        f, env = self.log()
        with env:
            self.ra.do_bt_disconnect(Recorder(), {"mac": "AA:BB:CC:DD:EE:01"})
        cmds = f.read_text().splitlines()
        self.assertLess(cmds.index("untrust AA:BB:CC:DD:EE:01"), cmds.index("disconnect AA:BB:CC:DD:EE:01"))

    def test_a_device_bluez_does_not_know_answers_at_once(self):
        import time
        t = time.monotonic()
        with self.assertRaises(RuntimeError) as cm:
            self.ra.do_bt_connect(Recorder(), {"mac": "77:77:77:77:77:77"})
        self.assertEqual(str(cm.exception), "unreachable")
        self.assertLess(time.monotonic() - t, 5, "not the full wait")

    def test_a_device_that_does_not_answer_fails_in_a_word(self):
        with self.assertRaises(RuntimeError) as cm:
            self.ra.do_bt_connect(Recorder(), {"mac": "99:99:99:99:99:99"})
        self.assertEqual(str(cm.exception), "unreachable")

    def test_bad_addresses_never_start_bluetoothctl(self):
        with mock.patch.object(self.ra, "bt_open", side_effect=AssertionError("started")):
            for fn in (self.ra.do_bt_pair, self.ra.do_bt_connect, self.ra.do_bt_disconnect, self.ra.do_bt_forget):
                with self.assertRaises(ValueError):
                    fn(Recorder(), {"mac": "AA:BB:CC:DD:EE:01;halt"})

    def test_without_bluez_the_status_says_so_and_does_not_fail(self):
        with mock.patch.object(self.ra.shutil, "which", return_value=None):
            p = Recorder()
            self.ra.do_bt_status(p, {})
        self.assertEqual(p.data(), {"hw": False, "why": "no-bluez", "saved": []})

    def test_the_actions_are_registered(self):
        for a in ("bt-status", "bt-scan", "bt-pair", "bt-connect", "bt-disconnect", "bt-forget"):
            self.assertIn(a, self.ra.ACTIONS)


class Outputs(unittest.TestCase):
    DUMP = json.dumps([
        {"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "bluez_output.AA_BB_CC_DD_EE_01.1",
            "node.description": "Sony WH-1000XM4"}}},
        {"id": 41, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "alsa_output.platform-bcm2835_audio.stereo-fallback",
            "alsa.card_name": "bcm2835 Headphones"}}},
    ])

    def test_a_bluetooth_sink_is_an_output_of_its_own(self):
        from ui import audio
        found = audio.sinks(self.DUMP)
        keys = audio.keys_now(found, hdmi=False)
        self.assertIn(("bt:bluez_output.AA_BB_CC_DD_EE_01.1", "Sony WH-1000XM4"), keys)
        self.assertEqual(audio.pick("bt:bluez_output.AA_BB_CC_DD_EE_01.1", found), 40)
        self.assertEqual(audio.pick("aux", found), 41, "AUX is never the Bluetooth sink")
        self.assertTrue(audio.valid("bt:bluez_output.AA_BB_CC_DD_EE_01.1"))
        self.assertFalse(audio.valid("bt:"))
        self.assertTrue(audio.valid("mix", ["aux", "bt:bluez_output.AA_BB_CC_DD_EE_01.1"]))

    def test_the_device_behind_a_key_and_its_address(self):
        from ui import audio
        self.assertEqual(audio.usb_dev("bt:bluez_output.AA_BB_CC_DD_EE_01.1"), "bluez_output.AA_BB_CC_DD_EE_01")
        self.assertEqual(audio.usb_dev("bt:bluez_output.AA_BB_CC_DD_EE_01.2"), audio.usb_dev("bt:bluez_output.AA_BB_CC_DD_EE_01.1"))
        self.assertEqual(audio.bt_mac("bt:bluez_output.aa_bb_cc_dd_ee_01.1"), "AA:BB:CC:DD:EE:01")
        self.assertIsNone(audio.bt_mac("usb:alsa_output.usb-x"))
        self.assertTrue(audio.is_ext("bt:x") and audio.is_ext("usb:x") and not audio.is_ext("aux"))

    def test_a_combine_with_a_bluetooth_device_is_made_from_its_node_name(self):
        from ui import audio
        found = audio.sinks(self.DUMP)
        self.assertEqual(audio.node_name("bt:bluez_output.AA_BB_CC_DD_EE_01.1", found), "bluez_output.AA_BB_CC_DD_EE_01.1")

    def test_the_levels_of_the_built_in_outputs_do_not_touch_the_bluetooth_one(self):
        from ui import audio
        calls = []
        with mock.patch.object(audio, "_run", side_effect=lambda *c, **k: (calls.append(c), mock.Mock(returncode=0, stdout=""))[1]), \
                mock.patch.object(audio, "sinks", return_value=audio.sinks(self.DUMP)), \
                mock.patch.object(audio, "hdmi_profile_on", return_value=False):
            ok, _ = audio.apply("bt:bluez_output.AA_BB_CC_DD_EE_01.1")
        self.assertTrue(ok)
        levels = [c for c in calls if c[:2] == ("wpctl", "set-volume")]
        self.assertTrue(levels and all(c[2] != "40" for c in levels), levels)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, lang="en"):
        app = make_app(self.mod, lang=lang)
        app.snd_list = [("aux", "AUX")]
        return app

    def test_the_page_draws_in_every_state_and_both_languages(self):
        saved = [{"mac": "AA:BB:CC:DD:EE:01", "name": "Sony WH-1000XM4", "connected": True, "audio": True},
                 {"mac": "AA:BB:CC:DD:EE:02", "name": "A speaker with a very very long name that has to be cut to fit the row", "connected": False}]
        found = [{"mac": "11:22:33:44:55:66", "name": "JBL Flip 5"}]
        states = {"loading": {"hw": None}, "no adapter": {"hw": False, "why": "no-adapter"}, "no bluez": {"hw": False, "why": "no-bluez"},
                  "empty": {"hw": True, "saved": [], "found": []}, "full": {"hw": True, "saved": saved, "found": found},
                  "scanning": {"hw": True, "saved": saved, "found": [], "scanning": True},
                  "acting": {"hw": True, "saved": saved, "found": found, "act": {"11:22:33:44:55:66": "Pairing…"}}}
        for lang in ("en", "he"):
            app = self.app(lang)
            for name, st in states.items():
                app.bt.update({"hw": None, "why": "", "saved": [], "found": [], "scanning": False, "act": {}, "sel": 0, "screen": "list"})
                app.bt.update(st)
                app.bt["status_at"] = 1e12                         # not asked again while drawing
                app.go("bt")
                with self.subTest(lang=lang, state=name):
                    app.draw()

    def test_forget_screen_draws(self):
        app = self.app()
        app.bt.update(hw=True, saved=[{"mac": "AA:BB:CC:DD:EE:01", "name": "Sony", "connected": False}], screen="forget", status_at=1e12)
        app.go("bt")
        app.draw()
        app.bt.update(saved=[])
        app.draw()

    def test_the_path_chain_and_the_cell_on_the_output_page(self):
        app = self.app()
        self.assertEqual(app.trail_of("bt"), ["Settings", "Sound", "Output", "Bluetooth"])
        app.page = "outs"
        cells = app.output_cells()
        names = [c[0] for c in cells]
        self.assertIn("Bluetooth", names)
        self.assertEqual(names.index("Bluetooth"), len(names) - 2, "the cell before the 'no sound' one")

    def test_a_scan_takes_the_answer_and_a_pair_asks_root(self):
        app = self.app()
        app.bt.update(hw=True, saved=[], found=[])
        answers = {"scan": (True, {"hw": True, "saved": [], "found": [{"mac": "11:22:33:44:55:66", "name": "JBL Flip 5"}]}),
                   "pair": (True, {"mac": "11:22:33:44:55:66", "connected": True}),
                   "status": (True, {"hw": True, "saved": [{"mac": "11:22:33:44:55:66", "name": "JBL Flip 5", "connected": True}]})}
        calls = []

        def fake(action, timeout=60, ticket=None, **args):
            calls.append((action, args))
            return answers[action]
        with mock.patch("ui.views_bt.system.bt", side_effect=fake):
            app.bt_scan()
            self._wait(app)
            self.assertEqual([d["name"] for d in app.bt["found"]], ["JBL Flip 5"])
            app.bt_pair(app.bt["found"][0])
            self._wait(app)
        self.assertIn(("pair", {"mac": "11:22:33:44:55:66"}), calls)
        self.assertEqual(app.bt["saved"][0]["name"], "JBL Flip 5")
        self.assertEqual(app.bt["found"], [], "a paired device leaves the nearby list")

    def test_a_failure_is_told_in_words(self):
        app = self.app()
        app.bt.update(hw=True, saved=[{"mac": "AA:BB:CC:DD:EE:01", "name": "Sony", "connected": False}])
        with mock.patch("ui.views_bt.system.bt", side_effect=lambda a, timeout=60, **k: (False, "unreachable") if a == "connect" else (True, {"hw": True, "saved": []})):
            app.bt_toggle(app.bt["saved"][0])
            self._wait(app)
        self.assertIn("did not answer", app.bt_reason("unreachable"))
        self.assertEqual(app.bt_reason("nonsense"), app.tr("bt.e.failed"))

    def test_a_chosen_device_that_is_not_there_is_connected_again_with_a_growing_wait(self):
        app = self.app()
        app.view = "settings"
        calls = []
        with mock.patch("ui.views_bt.system.bt", side_effect=lambda a, timeout=60, **k: (calls.append((a, k)), (False, "unreachable"))[1]), \
                mock.patch("ui.views_bt.time.monotonic", return_value=1000.0):
            key = "bt:bluez_output.AA_BB_CC_DD_EE_01.1"
            app.follow_bt([key])
            self.assertEqual(calls, [], "not at once: the device may be about to connect by itself")
            app.bt["retry"]["AA:BB:CC:DD:EE:01"]["at"] = 0.0
            app.follow_bt([key])
            self._wait(app, retry=True)
        self.assertEqual(calls, [("connect", {"mac": "AA:BB:CC:DD:EE:01"})])
        self.assertGreater(app.bt["retry"]["AA:BB:CC:DD:EE:01"]["wait"], 20)

    def test_every_bluetooth_text_has_both_languages_and_opens_in_hebrew(self):
        from ui.texts import T
        import re
        keys = [k for k in T if k.startswith(("bt.", "c.bt", "c.outsBt", "snd.bt", "chk.l.bt", "chk.d.bt", "chk.r.bt", "chk.do.bt"))]
        self.assertGreater(len(keys), 30)
        for k in keys:
            en, he = T[k]
            with self.subTest(key=k):
                self.assertTrue(en and he)
                self.assertFalse(re.match(r"[A-Za-z]", he.strip()), "a Hebrew text opens with a Hebrew word: " + he)

    @staticmethod
    def _wait(app, retry=False):
        import time
        end = time.time() + 3
        while time.time() < end and (app.bt["busy"] or (retry and app.bt["retrying"])):
            time.sleep(0.01)
        time.sleep(0.05)


class Check(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fullcheck
        cls.fc = fullcheck

    TOP = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   41   1024  48000   31.2us  12.4us  0.00  0.00    0 S16LE 2 48000  alsa_output.platform-bcm2835_audio.stereo-fallback
R   40   1024  48000   30.1us  20.0us  0.00  0.00    7 S16LE 2 48000  bluez_output.AA_BB_CC_DD_EE_01.1
"""

    def test_the_bluetooth_outputs_and_their_dropouts_are_read(self):
        dump = Outputs.DUMP
        self.assertEqual(self.fc.bt_sink_names(dump), ["bluez_output.AA_BB_CC_DD_EE_01.1"])
        self.assertEqual(self.fc.bt_dropouts(["bluez_output.AA_BB_CC_DD_EE_01.1"], self.TOP), 7)
        self.assertEqual(self.fc.bt_dropouts(["bluez_output.AA_BB_CC_DD_EE_01.1"], "nothing"), None)

    def test_the_log_lines_that_count(self):
        t = "bluetoothd[1]: Failed to connect\nwireplumber: a2dp sink underrun\npipewire: all well\nsomething else"
        self.assertEqual(self.fc.bt_log_problems(t), 2)

    def test_the_rows(self):
        names = ["bluez_output.AA_BB_CC_DD_EE_01.1"]
        self.assertEqual(self.fc.bt_items({}, {}, names=[], errs=0, log=0), [], "no Bluetooth output: no row")
        ok = self.fc.bt_items({}, {}, names=names, errs=0, log=0)[0]
        self.assertEqual(ok.level, self.fc.OK)
        wifi = self.fc.bt_items({}, {}, names=names, errs=5, log=2, wifi={"nm": True, "connected": True})[0]
        self.assertEqual(wifi.level, self.fc.NOTE)
        self.assertIn("network cable", wifi.todo[0])
        cable = self.fc.bt_items({}, {}, names=names, errs=5, log=0, wifi={"nm": True, "connected": False, "wired": True})[0]
        self.assertIn("closer", cable.todo[0])
        self.assertNotIn("network cable", cable.todo[0])
        none = self.fc.bt_items({}, {}, names=names, errs=None, log=0)[0]
        self.assertEqual(none.level, self.fc.SKIP)


class Wiring(unittest.TestCase):
    def test_the_installer_installs_bluez_and_the_module_and_undoes_the_group(self):
        src = (ROOT / "install.sh").read_text()
        self.assertIn("DEVICE_APT+=(bluez libspa-0.2-bluetooth)", src)
        self.assertIn('change_add GROUP_ADDED "$SERVICE_USER:bluetooth"', src)
        self.assertIn("systemctl enable --now bluetooth.service", src)

    def test_wireplumber_keeps_a_bluetooth_output_on_a2dp_and_open(self):
        conf = (ROOT / "device" / "pipewire" / "80-armonico-wireplumber.conf").read_text()
        self.assertIn("bluetooth.autoswitch-to-headset-profile = false", conf)
        self.assertIn('node.name = "~bluez_output.*"', conf)
        self.assertIn("session.suspend-timeout-seconds = 0", conf)

    def test_system_bt_parses_the_answer(self):
        from ui import system
        with mock.patch.object(system, "request", return_value=(True, json.dumps({"hw": True, "saved": []}))):
            self.assertEqual(system.bt("status"), (True, {"hw": True, "saved": []}))
        with mock.patch.object(system, "request", return_value=(False, "unreachable")):
            self.assertEqual(system.bt("connect", mac="AA:BB:CC:DD:EE:01"), (False, "unreachable"))


FAKE_UNBUFFERED = r"""#!/usr/bin/env python3
import sys
# Prints without flushing, as a program does that writes to a pipe: only a terminal makes it appear at once.
print("Agent registered")
for line in sys.stdin:
    c = line.strip()
    if c == "quit":
        break
    print("echo:" + c)
"""


FAKE_SILENT = r"""#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if "--timeout" in args:
    print("Discovery started")
    print("[NEW] Device 11:22:33:44:55:66 JBL Flip 5")
elif args[:1] == ["devices"]:
    print("Device 11:22:33:44:55:66 JBL Flip 5")
elif args[:1] == ["info"]:
    print("Device %s (public)\n\tName: JBL Flip 5\n\tAlias: JBL Flip 5\n\tIcon: audio-card\n\tPaired: no\n\tTrusted: no\n\tConnected: no\n\tUUID: Audio Sink" % args[1])
else:
    for line in sys.stdin:                          # the session answers a power on and nothing else
        c = line.strip()
        if c == "quit":
            break
        if c == "power on":
            print("Changing power on succeeded")
            sys.stdout.flush()
"""


class Terminal(unittest.TestCase):
    """bluetoothctl runs on a terminal of its own, and a session that says nothing falls back to the one-off commands."""

    def _on_path(self, script):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "bluetoothctl"
        path.write_text(script)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        env = mock.patch.dict(os.environ, {"PATH": tmp.name + os.pathsep + os.environ.get("PATH", "")})
        env.start()
        self.addCleanup(env.stop)

    def test_the_output_arrives_line_by_line_without_a_flush(self):
        ra = load_actions()
        self._on_path(FAKE_UNBUFFERED)
        c = ra.BtCtl()
        try:
            self.assertIsNotNone(c.master)
            self.assertIn("Agent registered", c.read(3, r"Agent registered"))
            c.send("scan on")
            self.assertIn("echo:scan on", c.read(3, r"echo:scan on"))
        finally:
            c.close()

    def test_a_session_that_says_nothing_still_finds_the_device(self):
        ra = load_actions()
        ra.BT_SCAN = (1, 20)
        self._on_path(FAKE_SILENT)
        p = Recorder()
        ra.do_bt_scan(p, {"seconds": 1})
        self.assertEqual([x["name"] for x in p.data()["found"]], ["JBL Flip 5"])


if __name__ == "__main__":
    unittest.main()


FAKE = r'''#!/usr/bin/env python3
import os, sys
LOG = os.environ["FAKE_BT_LOG"]
pending = None
for line in sys.stdin:
    c = line.strip()
    with open(LOG, "a") as f:
        f.write(c + "\n")
    if c == "quit":
        break
    if c == "power on":
        print("Changing power on succeeded")
    elif c.startswith("info "):
        mac = c[5:]
        paired = "yes" if mac.endswith("05") else "no"     # 05: paired before the attempt
        print("\tName: Dev %s\n\tAlias: Dev %s\n\tPaired: %s\n\tTrusted: no\n\tConnected: no\n\tIcon: audio-headset" % (mac, mac, paired))
    elif c.startswith("pair "):
        mac = c[5:]
        print("Attempting to pair with " + mac)
        if mac.endswith("01"):
            print("[agent] Enter PIN code: ", end=""); pending = "0000"
        elif mac.endswith("02"):
            print("[agent] Confirm passkey 123456 (yes/no): ", end=""); pending = "yes"
        elif mac.endswith("03"):
            pass                                           # never answers
        elif mac.endswith(("05", "06")):
            print("Failed to pair: org.bluez.Error.AlreadyExists")
        else:
            print("Pairing successful")
    elif pending is not None and c == pending:
        pending = None
        print("Pairing successful")
    elif c.startswith("trust "):
        print("Changing %s trust succeeded" % c[6:])
    elif c.startswith("connect "):
        if c.endswith(("04", "05", "06")):
            print("Failed to connect: org.bluez.Error.Failed br-connection-page-timeout")
        else:
            print("Connection successful")
    elif c.startswith("cancel-pairing "):
        print("Cancel pairing successful")
    elif c.startswith("disconnect "):
        print("Successful disconnected")
    elif c.startswith("remove "):
        print("Device has been removed")
    sys.stdout.flush()
'''


class Pairing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ra = load_actions()
        cls.ra.BT_READY_WAIT = 0.1
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "bluetoothctl"
        path.write_text(FAKE)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        cls.log = Path(cls.tmp.name) / "log"
        cls.env = mock.patch.dict(os.environ, {"PATH": cls.tmp.name + os.pathsep + os.environ.get("PATH", ""),
                                               "FAKE_BT_LOG": str(cls.log)})
        cls.env.start()
        cls.ra.BT_PAIR_WAIT = 2
        cls.ra.BT_CONNECT_WAIT = 2

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def sent(self):
        return self.log.read_text().splitlines()

    def pair(self, mac):
        self.log.write_text("")
        p = Recorder()
        self.ra.do_bt_pair(p, {"mac": mac})
        return p.data()

    def test_a_pin_code_is_answered_with_0000(self):
        self.assertEqual(self.pair("44:44:44:44:44:01")["mac"], "44:44:44:44:44:01")
        self.assertIn("0000", self.sent())
        self.assertNotIn("remove 44:44:44:44:44:01", self.sent())

    def test_a_passkey_is_confirmed(self):
        self.assertEqual(self.pair("44:44:44:44:44:02")["mac"], "44:44:44:44:44:02")
        self.assertEqual(self.sent().count("yes"), 1, "one question, one answer")

    def test_a_pairing_that_does_not_finish_is_called_off_and_the_device_forgotten(self):
        with self.assertRaises(RuntimeError) as e:
            self.pair("44:44:44:44:44:03")
        self.assertEqual(str(e.exception), "timeout")
        cmds = self.sent()
        for c in ("cancel-pairing", "disconnect", "remove"):
            self.assertIn(f"{c} 44:44:44:44:44:03", cmds)

    def test_a_connection_that_fails_after_pairing_keeps_the_pairing(self):
        """Paired is paired: a first connection that does not finish (BlueZ busy while the headset connects by
        itself) keeps the device in the saved list instead of forgetting it."""
        self.pair("44:44:44:44:44:04")
        self.assertNotIn("remove 44:44:44:44:44:04", self.sent())

    def test_a_device_paired_before_is_never_removed_when_the_attempt_fails(self):
        """AlreadyExists counted as success, and a connection failing after it ran bt_release, which removed a
        pairing that was there before and worked."""
        for mac in ("44:44:44:44:44:05", "44:44:44:44:44:06"):   # paired per info, and per the AlreadyExists answer
            self.pair(mac)
            self.assertNotIn(f"remove {mac}", self.sent(), mac)
            self.assertNotIn(f"disconnect {mac}", self.sent(), mac)


class Wording(unittest.TestCase):
    def test_the_hebrew_says_tzimud(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui.texts import T
        he = " ".join(v[1] for v in T.values() if isinstance(v, tuple) and len(v) > 1)
        self.assertNotIn("זיווג", he)
        self.assertNotIn("מזווג", he)
        self.assertEqual(T["bt.pair"][1], "צימוד וחיבור")


SLOW = r'''#!/usr/bin/env python3
import os, sys
STATE = os.environ["FAKE_BT_STATE"]
for line in sys.stdin:
    c = line.strip()
    if c == "quit":
        break
    n = int(open(STATE).read() or 0) if os.path.exists(STATE) else 0
    if c in ("power on", "show"):
        open(STATE, "w").write(str(n + 1))
        if n < int(os.environ.get("FAKE_BT_MISSING", "1")):
            print("No default controller available")
        elif c == "power on":
            print("Changing power on succeeded")
        else:
            print("Controller 00:11:22:33:44:55 (public)\n\tPowered: yes")
    elif c == "devices":
        pass
    sys.stdout.flush()
'''


class NoControllerYet(unittest.TestCase):
    """"No default controller" is believed only when it holds after a second look."""

    @classmethod
    def setUpClass(cls):
        cls.ra = load_actions()
        cls.ra.BT_READY_WAIT = 0.1
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "bluetoothctl"
        path.write_text(SLOW)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        cls.state = Path(cls.tmp.name) / "state"
        cls.env = mock.patch.dict(os.environ, {"PATH": cls.tmp.name + os.pathsep + os.environ.get("PATH", ""),
                                               "FAKE_BT_STATE": str(cls.state)})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def status(self, missing, present, woke):
        self.state.write_text("0")
        p = Recorder()
        with mock.patch.dict(os.environ, {"FAKE_BT_MISSING": str(missing)}), \
                mock.patch.object(self.ra, "bt_hci_present", lambda: present), \
                mock.patch.object(self.ra, "bt_wake", woke), mock.patch.object(self.ra.time, "sleep", lambda s: None):
            self.ra.do_bt_status(p, {})
        return p.data()

    def test_a_moment_late_adapter_is_asked_again_and_found(self):
        woke = mock.Mock()
        self.assertTrue(self.status(1, True, woke)["hw"])
        woke.assert_not_called()

    def test_a_missing_adapter_is_woken_and_found(self):
        woke = mock.Mock()
        self.assertTrue(self.status(1, False, woke)["hw"])
        woke.assert_called_once()

    def test_a_chip_that_stopped_answering_is_started_again(self):
        woke = mock.Mock()
        self.assertTrue(self.status(2, True, woke)["hw"])
        woke.assert_called_once()

    def test_the_chip_is_restarted_through_its_driver(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "serial0-0").mkdir()
            (d / "unbind").write_text("")
            (d / "bind").write_text("")
            with mock.patch.object(self.ra, "BT_SERDEV", d), mock.patch.object(self.ra.time, "sleep", lambda s: None):
                self.assertTrue(self.ra.bt_rebind())
            self.assertEqual((d / "unbind").read_text(), "serial0-0")
            self.assertEqual((d / "bind").read_text(), "serial0-0")
            with mock.patch.object(self.ra, "BT_SERDEV", d / "none"):
                self.assertFalse(self.ra.bt_rebind())

    def test_an_adapter_that_stays_missing_is_reported(self):
        d = self.status(9, False, mock.Mock())
        self.assertFalse(d["hw"])
        self.assertEqual(d["why"], "no-adapter")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "pygame is not installed")
class ManualOff(unittest.TestCase):
    def test_a_disconnect_by_hand_holds_after_a_restart_and_a_forgotten_device_is_not_chased(self):
        import test_device_ui as T
        mod = T.load_screen()
        app = T.make_app(mod)
        app.bt["manual_off"].add("AA:BB:CC:DD:EE:01")
        app.bt_keep_off()
        self.assertEqual(app.saved["bt_manual_off"], ["AA:BB:CC:DD:EE:01"])
        again = T.make_app(mod, settings={"bt_manual_off": ["AA:BB:CC:DD:EE:01"]})
        self.assertIn("AA:BB:CC:DD:EE:01", again.bt["manual_off"], "a restart of the screen keeps it")
        back = {"keys": ["bt:bluez_output.AA_BB_CC_DD_EE_01.1"], "out": "aux", "mix": None}
        gone = T.make_app(mod, settings={"sound_back": back})
        gone.bt_drop_back("AA:BB:CC:DD:EE:01")
        self.assertIsNone(gone.saved["sound_back"])
