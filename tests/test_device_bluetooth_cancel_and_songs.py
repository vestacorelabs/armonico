"""Bluetooth that can be called off and really disconnects, the quick check's Bluetooth going back to its
list, a song that starts at once and stops with the songs list, the sharper scaler on a wide screen, texts without the
project name next to a version, the Wi-Fi and Bluetooth switches, and no empty song line when the buttons are hidden."""
import importlib.util
import json
import os
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))

try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None


def load_ra():
    spec = importlib.util.spec_from_file_location("ra88", ROOT / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Root(unittest.TestCase):
    def test_a_cancel_for_this_request_is_seen_and_one_for_another_is_not(self):
        ra = load_ra()
        with tempfile.TemporaryDirectory() as d:
            ra.CANCEL = Path(d) / "device-cancel"
            ra.CURRENT["rid"] = "abc123"
            self.assertFalse(ra.cancel_asked())
            ra.CANCEL.write_text("zzz999")
            self.assertFalse(ra.cancel_asked())
            ra.CANCEL.write_text("abc123")
            self.assertTrue(ra.cancel_asked())
            with self.assertRaises(ra.Cancelled):
                ra.nap(5)

    def test_waits_are_shorter(self):
        ra = load_ra()
        self.assertLessEqual(ra.BT_PAIR_WAIT, 20)
        self.assertLessEqual(ra.BT_CONNECT_WAIT, 10)
        self.assertLessEqual(ra.BT_CONNECT_STEP_WAIT, 12)

    def test_the_radio_action_takes_only_a_known_radio(self):
        ra = load_ra()
        for bad in ({"kind": "usb", "on": True}, {"kind": "wifi", "on": "yes"}, {}):
            with self.assertRaises(ValueError):
                ra.do_radio(None, bad)
        self.assertIn("radio", ra.ACTIONS)

    def test_the_radio_state_is_read_from_rfkill(self):
        from ui import system
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for name, typ, soft, hard in (("rfkill0", "wlan", "0", "0"), ("rfkill1", "bluetooth", "1", "0")):
                (base / name).mkdir()
                (base / name / "type").write_text(typ + "\n")
                (base / name / "soft").write_text(soft + "\n")
                (base / name / "hard").write_text(hard + "\n")
            self.assertEqual(system.radio_state("wifi", str(base)), "on")
            self.assertEqual(system.radio_state("bluetooth", str(base)), "off")
            (base / "rfkill0" / "hard").write_text("1\n")
            self.assertEqual(system.radio_state("wifi", str(base)), "hard")
            self.assertIsNone(system.radio_state("wifi", str(base / "none")))

    def test_the_screen_writes_the_id_it_gives_up_on(self):
        from ui import system
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(system, "CANCEL", Path(d) / "device-cancel"):
                system._CURRENT_RID["rid"] = "r1r1r1"
                system.cancel_current()
                self.assertEqual((Path(d) / "device-cancel").read_text(), "r1r1r1")


class RadioChip(unittest.TestCase):
    """The Wi-Fi or Bluetooth chip itself off from the next start: disable-wifi / disable-bt in one marked block
    of config.txt, written once, removed when both are on again, and nothing else of the file touched."""

    BASE = "# the Pi's own settings\ndtparam=audio=on\n[pi4]\narm_boost=1\n"

    def setUp(self):
        self.ra = load_ra()
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.cfg = Path(self.d.name) / "config.txt"
        self.cfg.write_text(self.BASE)
        self.ra.BOOT_CONFIG = self.cfg
        self.cmds = []
        self.hci = "enabled"

        def run(*cmd, timeout=600):
            self.cmds.append(list(cmd))
            out = self.hci + "\n" if cmd[:2] == ("systemctl", "is-enabled") else ""
            return mock.Mock(returncode=0 if out.strip() == "enabled" or cmd[1] != "is-enabled" else 1, stdout=out)
        self.ra.run = run
        self.steps = []

    def p(self, *a):
        self.steps.append(a)

    def chip(self, kind, on):
        self.ra.do_radio_chip(self.p, {"kind": kind, "on": on})

    def test_off_on_and_idempotent(self):
        self.chip("wifi", False)
        text = self.cfg.read_text()
        self.assertTrue(text.startswith(self.BASE))
        self.assertIn("[all]\ndtoverlay=disable-wifi\n", text)
        self.chip("wifi", False)
        self.assertEqual(self.cfg.read_text(), text)                     # the same block, once
        self.chip("bluetooth", False)
        text = self.cfg.read_text()
        self.assertEqual(text.count(self.ra.CHIP_BEGIN), 1)
        self.assertIn("dtoverlay=disable-wifi\ndtoverlay=disable-bt\n", text)
        self.assertIn(["systemctl", "disable", "hciuart"], self.cmds)
        self.chip("wifi", True)
        self.assertNotIn("disable-wifi", self.cfg.read_text())
        self.chip("bluetooth", True)
        self.assertIn(["systemctl", "enable", "hciuart"], self.cmds)
        self.assertEqual(self.cfg.read_text(), self.BASE)                # back to the file as it was
        self.assertFalse([f for f in Path(self.d.name).iterdir() if f.name.startswith(".")])

    def test_hciuart_disabled_by_someone_else_is_left_alone(self):
        self.hci = "disabled"
        self.chip("bluetooth", False)
        self.chip("bluetooth", True)
        self.assertNotIn(["systemctl", "disable", "hciuart"], self.cmds)
        self.assertNotIn(["systemctl", "enable", "hciuart"], self.cmds)

    def test_a_line_written_by_hand_is_not_removed(self):
        self.cfg.write_text(self.BASE + "dtoverlay=disable-bt\n")
        with self.assertRaises(RuntimeError) as e:
            self.chip("bluetooth", True)
        self.assertEqual(str(e.exception), "by-hand")
        self.assertEqual(self.cfg.read_text(), self.BASE + "dtoverlay=disable-bt\n")

    def test_a_line_written_by_hand_is_made_a_comment_when_the_screen_asked(self):
        self.cfg.write_text(self.BASE + "dtoverlay=disable-bt\n")
        self.ra.do_radio_chip(self.p, {"kind": "bluetooth", "on": True, "hand": True})
        text = self.cfg.read_text()
        self.assertEqual(text, self.BASE + self.ra.CHIP_UNHAND + "dtoverlay=disable-bt\n")
        self.assertEqual(self.ra.chip_by_hand(text), set(), "the chip is on from the next start")

    def test_only_known_chips(self):
        for bad in ({"kind": "usb", "on": True}, {"kind": "wifi", "on": 1}, {}):
            with self.assertRaises(ValueError):
                self.ra.do_radio_chip(self.p, bad)
        self.assertIn("radio-chip", self.ra.ACTIONS)


class LocalBrokerFiles(unittest.TestCase):
    def test_switching_to_the_local_broker_keeps_the_websockets_listener(self):
        ra = load_ra()
        with tempfile.TemporaryDirectory() as d:
            ra.MOSQ_CONF, ra.MOSQ_USERS, ra.MOSQ_ACL = (Path(d) / "conf.d" / "armonico.conf", Path(d) / "armonico.passwd",
                                                       Path(d) / "armonico.acl")
            ra.run = lambda *a, **k: mock.Mock(returncode=0, stdout="")
            with mock.patch.object(ra.shutil, "chown"):
                ra.local_logins(mock.Mock())
            conf = ra.MOSQ_CONF.read_text()
        self.assertIn("listener 1883 127.0.0.1", conf)
        self.assertIn("listener 9001\nprotocol websockets", conf)
        self.assertEqual(conf.count("allow_anonymous false"), 2)


@unittest.skipIf(pygame is None, "no pygame")
class ChipCards(unittest.TestCase):
    """Settings › Network and links: a card per chip, with a question that says it takes a restart, a warning
    when the Wi-Fi in use would go with it, and the restart offered after."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.cfg = Path(self.d.name) / "config.txt"
        self.cfg.write_text("dtparam=audio=on\n")
        p = mock.patch("ui.views_settings.BOOT_CONFIG", str(self.cfg))
        p.start()
        self.addCleanup(p.stop)
        self.app = make_app(self.mod, lang="en")

    def cell(self, kind):
        self.app._chip_cache = (-1e9, set())
        self.app._radio_cache = {}
        name = self.app.tr("chip.wifi" if kind == "wifi" else "chip.bt")
        return [c for c in self.app.settings_cells()["chips"] if c[0] == name][0]

    def test_cards_follow_config_txt_and_the_hardware(self):
        app = self.app
        with mock.patch("ui.views_bt.system.radio_state", return_value="on"):
            self.assertEqual(self.cell("wifi")[1], app.tr("on"))
            self.cfg.write_text("dtparam=audio=on\ndtoverlay=disable-wifi\n")
            self.assertEqual(self.cell("wifi")[1], app.tr("chip.offNext"))
            self.assertEqual(self.cell("bluetooth")[1], app.tr("on"))
        with mock.patch("ui.views_bt.system.radio_state", return_value=None):
            self.assertEqual(self.cell("wifi")[1], app.tr("off"))
            c = self.cell("bluetooth")
            self.assertEqual((c[1], c[3]), (app.tr("radio.none"), None))     # no chip at all: nothing to press

    def test_the_two_cards_are_on_a_page_of_their_own_opened_from_the_last_network_card(self):
        names = [c[0] for c in self.app.settings_cells()["chips"]]
        self.assertEqual(names, [self.app.tr("chip.wifi"), self.app.tr("chip.bt")])
        net = self.app.settings_cells()["net"]
        self.assertEqual(net[-1][0], self.app.tr("g.chips"))
        self.app.open_page("net")
        net[-1][3]()
        self.assertEqual(self.app.page, "chips")

    def test_wifi_off_while_on_wifi_warns_and_then_offers_the_restart(self):
        app = self.app
        app.wf["via"] = "wifi"
        with mock.patch("ui.views_bt.system.radio_state", return_value="on"):
            self.cell("wifi")[3]()
        self.assertEqual(app.confirm["text"], app.tr("chip.wifiOffLinkQ"))
        calls = []
        with mock.patch("ui.views_settings.system.request", side_effect=lambda a, **k: (calls.append((a, k)), (True, ""))[1]):
            app.answer(True)
            for _ in range(100):
                if calls and app.confirm:
                    break
                time.sleep(0.01)
        self.assertEqual(calls, [("radio-chip", {"timeout": 40, "kind": "wifi", "on": False})])
        self.assertEqual(app.confirm["text"], app.tr("chip.restartQ"))

    def test_wifi_off_on_a_cable_and_bluetooth_off_ask_the_plain_question(self):
        app = self.app
        app.wf["via"] = "ethernet"
        with mock.patch("ui.views_bt.system.radio_state", return_value="on"):
            self.cell("wifi")[3]()
            self.assertEqual(app.confirm["text"], app.tr("chip.wifiOffQ"))
            self.cell("bluetooth")[3]()
            self.assertEqual(app.confirm["text"], app.tr("chip.btOffQ"))

    def test_texts_in_both_languages_without_only(self):
        from ui.texts import T
        for k, (en, he) in T.items():
            if k.startswith("chip."):
                self.assertTrue(en and he, k)
                self.assertNotIn("בלבד", he, k)
                self.assertNotIn(" only", en, k)


class Video(unittest.TestCase):
    def test_the_scaler_keeps_the_default_method(self):
        # the 4-tap method (method=2) is documented by GStreamer as slow: the 1080p frames are scaled on the CPU
        src = (ROOT / "device" / "screen.py").read_text()
        self.assertNotIn('scale.set_property("method"', src)


class Bridge(unittest.TestCase):
    def test_the_length_of_the_file_does_not_hold_the_player_up(self):
        src = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        start = src[src.index("start_song() {"):src.index("pause_song() {")]
        self.assertNotIn('-m "$(get_duration "$FILE")" -r\n    (', start)


@unittest.skipIf(pygame is None, "no pygame")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_the_quick_check_bluetooth_goes_back_to_the_quick_list(self):
        app = make_app(self.mod, lang="en")
        app.open_bt_quick()
        self.assertIn(app.tr("g.quick"), app.trail_of("bt"))
        self.assertNotIn(app.tr("g.outs"), app.trail_of("bt"))
        with mock.patch("ui.views_bt.system.cancel_current"):
            app.bt_leave()
        self.assertEqual(app.page, "quick")
        self.assertEqual(app.sel, [k for k, _ in app.quick_areas()].index("bt"))

    def test_the_output_page_bluetooth_still_goes_back_to_the_output_page(self):
        app = make_app(self.mod, lang="en")
        app.open_bt()
        app.bt_leave()
        self.assertEqual(app.page, "outs")

    def test_leaving_the_songs_list_stops_a_song(self):
        app = make_app(self.mod, lang="en")
        app.go("songs")
        s = app.screen
        s.song, s.alarm = "x", False
        sent = []
        s.pub = lambda t, p, retain=False: sent.append((t, p))
        app.go("rest")
        self.assertEqual([t for t, _ in sent if t == "piano/stop"], ["piano/stop"])

    def test_the_alarm_is_not_stopped_by_leaving_the_songs_list(self):
        app = make_app(self.mod, lang="en")
        app.go("songs")
        s = app.screen
        s.song, s.alarm = "x", True
        sent = []
        s.pub = lambda t, p, retain=False: sent.append((t, p))
        app.go("rest")
        self.assertEqual([t for t, _ in sent if t == "piano/stop"], [])

    def test_the_about_card_has_no_version_and_the_system_card_is_as_it_was(self):
        app = make_app(self.mod, lang="en")
        app.version = lambda: "1.1"
        top = app.settings_cells()["top"]
        about = next(c[1] for c in top if c[0] == app.tr("g.about"))
        system_v = next(c[1] for c in top if c[0] == app.tr("g.sys"))
        self.assertEqual(about, "Armonico Pi")
        self.assertEqual(system_v, "Armonico 1.1")

    def test_the_radios_are_switches_with_the_networks_and_devices_beside_them(self):
        from ui.widgets import SwitchVal
        app = make_app(self.mod, lang="en")
        for kind, sw in (("wifi", "c.wifiSw"), ("bluetooth", "c.btSw")):
            for st in ("on", "off"):
                with mock.patch("ui.views_bt.system.radio_state", return_value=st):
                    app._radio_cache = {}
                    net = app.settings_cells()["net"]
                    names = [c[0] for c in net]
                    i = names.index(app.tr(sw))          # one card: the switch and the networks or devices
                    self.assertIsInstance(net[i][1], SwitchVal)
                    self.assertEqual(net[i][1].on, st == "on")
                    self.assertTrue(net[i][3], "the card can be pressed: it opens the page, or turns the radio on")
                    self.assertTrue(callable(net[i][1].toggle), "a touch on the switch turns it off or on")

    def test_switching_is_done_at_once_with_no_question_and_the_same_card_turns_it_on(self):
        app = make_app(self.mod, lang="en")
        calls = []
        with mock.patch.object(type(app), "radio_apply", lambda self, k, on: calls.append((k, on))):
            for st in ("on", "off"):
                with mock.patch("ui.views_bt.system.radio_state", return_value=st):
                    app._radio_cache = {}
                    app.radio_toggle("wifi")
                    app.radio_toggle("bluetooth")
        self.assertEqual(calls, [("wifi", False), ("bluetooth", False), ("wifi", True), ("bluetooth", True)])
        self.assertFalse(app.confirm)

    def test_the_bluetooth_devices_row_returns_to_itself(self):
        app = make_app(self.mod, lang="en")
        app.open_bt("net")
        self.assertIn(app.tr("g.net"), app.trail_of("bt"))
        app.bt_leave()
        self.assertEqual((app.page, app.sel), ("net", 2))

    def test_the_recording_sound_card_is_gone_and_an_output_is_switched_on_when_none_is(self):
        app = make_app(self.mod, lang="en")
        self.assertEqual(len(app.settings_cells()["rec"]), 2)
        app.snd_list = [("aux", "AUX"), ("hdmi", "HDMI")]
        app.save(sound_out="off", sound_mix=None)
        calls = []
        app.set_sound = lambda out, mix=None: (calls.append((out, mix)), app.save(sound_out=out))
        app.sound_due, app.snd_applying = 0.0, False
        app.rec_sound_prepare()
        self.assertEqual(calls, [("aux", None)])
        app.rec_sound_restore()
        self.assertEqual(calls[-1], ("off", None))

    def test_nothing_is_switched_when_an_output_is_in_use(self):
        app = make_app(self.mod, lang="en")
        app.snd_list = [("aux", "AUX"), ("hdmi", "HDMI")]
        app.save(sound_out="hdmi", sound_mix=None)
        calls = []
        app.set_sound = lambda out, mix=None: calls.append((out, mix))
        app.rec_sound_prepare()
        app.rec_sound_restore()
        self.assertEqual(calls, [])

    def test_the_empty_song_line_hides_with_the_buttons(self):
        app = make_app(self.mod, lang="en")
        app.card_cache = (1e18, None)
        app.save(bar_hide=True)
        app.bar_until = 0
        app.go("rest")
        app.draw()
        self.assertIsNone(app.song_rect)
        app.save(bar_hide=False)
        app.draw()
        self.assertIsNotNone(app.song_rect)
        app.save(bar_hide=True)
        app.card_cache = (1e18, {"name": "x", "states": [2, 1]})
        app.draw()
        self.assertIsNotNone(app.song_rect, "a song to go on with still shows")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class FreePlayNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_the_line_of_notes_is_named_in_one_way_and_follows_the_choice(self):
        app = make_app(self.mod, lang="en")
        app.screen.engine.state = {"device_labels": "num", "device_key_labels": [str(i + 1) for i in range(12)]}
        app.free_notes = [(0, 4), (2, 4), (4, 4)]
        self.assertEqual(app.free_line(), "1  3  5")
        app.screen.engine.state = {"device_labels": "do", "device_key_labels": ["Do", "Do#", "Re", "Re#", "Mi", "Fa",
                                                                                "Fa#", "Sol", "Sol#", "La", "La#", "Si"]}
        self.assertEqual(app.free_line(), "Do  Re  Mi", "the same notes, named by the choice now")

    def test_an_empty_answer_of_the_engine_keeps_the_names(self):
        app = make_app(self.mod, lang="en")
        app.screen.engine.state = {"device_labels": "abc", "device_key_labels": list("CDEFGAB") + [""] * 5}
        app.free_notes = [(0, 4)]
        self.assertEqual(app.free_line(), "C")
        app.screen.engine.state = {}
        self.assertEqual(app.free_line(), "C", "not C4: the last answer is kept")


class GpuVideo(unittest.TestCase):
    def test_one_window_only_and_rows_read_by_their_stride(self):
        src = (ROOT / "device" / "screen.py").read_text()
        self.assertNotIn("class VideoWindow", src)
        self.assertIn("buffer_get_video_meta", src)

    def test_in_the_gpu_way_the_caps_carry_no_size(self):
        src = (ROOT / "device" / "screen.py").read_text()
        self.assertIn('caps = f"video/x-raw,format={self.fmt},pixel-aspect-ratio=1/1"', src)


@unittest.skipIf(pygame is None, "no pygame")
class GpuVideoApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_a_small_screen_or_no_wayland_keeps_the_cpu_way(self):
        app = make_app(self.mod, lang="en")
        self.assertFalse(app.gpu_video_wanted())

    def test_the_gpu_way_waits_while_anything_is_drawn_over_the_video(self):
        app = make_app(self.mod, lang="en")
        media = app.screen.media
        media.gpu = True
        app.covered = True
        app.st["rest"] = "video"
        app.view = "rest"
        if app.rest_content != "video":
            self.skipTest("rest content is not settable here")
        app.bar_until = time.time() + 5
        self.assertFalse(app.gpu_video_ok(), "the buttons are up")
        app.bar_until = 0
        app.screen.toast = ("hello", time.time() + 3)
        self.assertFalse(app.gpu_video_ok(), "a message is shown")
