"""A USB sound device as an output, several outputs at once, and the screen recording."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame
ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audio = load("armonico_audio_usb", "device/ui/audio.py")


def node(i, name, card, desc):
    return {"id": i, "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": "Audio/Sink", "node.name": name, "alsa.card_name": card,
                               "node.description": desc}}}


USB_NAME = "alsa_output.usb-Logitech_USB_Headset-00.analog-stereo"
DUMP = json.dumps([
    node(40, "alsa_output.platform-bcm2835_audio.stereo-fallback", "bcm2835 Headphones", "Built-in Audio Stereo"),
    node(41, "alsa_output.platform-fef00700.hdmi.hdmi-stereo", "vc4-hdmi-0", "Built-in Audio Digital Stereo (HDMI)"),
    node(60, USB_NAME, "Logitech USB Headset", "Logitech USB Headset Analog Stereo"),
    node(70, "armonico_mix", "", "Simultaneous output"),
])


class Outputs(unittest.TestCase):
    def setUp(self):
        self.found = audio.sinks(DUMP)

    def test_usb_found_by_name_with_a_short_label(self):
        keys = audio.keys_now(self.found, hdmi=False)
        self.assertEqual(keys, [("aux", "AUX"), ("usb:" + USB_NAME, "Logitech USB Headset")])
        # self.assertIn(("hdmi", "HDMI"), audio.keys_now(self.found, hdmi=True))    # HDMI off (DSI only)
        self.assertNotIn(("hdmi", "HDMI"), audio.keys_now(self.found, hdmi=True))

    def test_pick_usb_and_mix(self):
        self.assertEqual(audio.pick("usb:" + USB_NAME, self.found), 60)
        self.assertIsNone(audio.pick("usb:alsa_output.usb-gone", self.found))
        self.assertEqual(audio.pick("mix", self.found), 70)
        self.assertEqual(audio.pick("aux", self.found), 40)   # the USB device never counts as AUX

    def test_valid_settings(self):
        self.assertTrue(audio.valid("usb:x"))
        self.assertFalse(audio.valid("usb:"))
        self.assertFalse(audio.valid("mix", ["aux"]))
        self.assertTrue(audio.valid("mix", ["aux", "usb:x"]))

    def test_mix_is_made_from_node_names(self):
        calls = []

        def fake(*cmd, timeout=5):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="")
        with mock.patch.object(audio, "_run", fake), mock.patch.object(audio, "sinks", return_value=self.found), \
                mock.patch.object(audio, "hdmi_profile_on", return_value=False):
            ok, _ = audio.apply("mix", ["aux", "usb:" + USB_NAME])
        self.assertTrue(ok)
        load_cmd = next(c for c in calls if c[:2] == ("pactl", "load-module"))
        self.assertIn("sinks=alsa_output.platform-bcm2835_audio.stereo-fallback," + USB_NAME, load_cmd)
        vols = [c[2] for c in calls if c[:2] == ("wpctl", "set-volume")]
        self.assertNotIn("60", vols, "a USB device keeps its own level")

    def test_both_never_takes_a_usb_device(self):
        conf = (ROOT / "device" / "pipewire" / "80-armonico.conf").read_text()
        self.assertIn('node.name = "~alsa_output.platform.*"', conf)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Choice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, **st):
        a = T.make_app(self.mod, settings=st)
        a._hdmi_seen = (1e18, False)
        a.snd_list = [("aux", "AUX"), ("usb:" + USB_NAME, "Logitech USB Headset")]
        return a

    def test_keys_and_settings(self):
        a = self.app()
        self.assertEqual(a.keys_setting([]), ("off", None))
        # self.assertEqual(a.keys_setting(["hdmi", "aux"]), ("both", None))    # HDMI off (DSI only)
        self.assertEqual(a.keys_setting(["usb:x"]), ("mix", ["aux", "usb:x"]), "AUX always plays beside a device")
        self.assertEqual(a.keys_setting(["aux"]), ("aux", None))
        self.assertEqual(a.keys_setting(["aux", "usb:x"]), ("mix", ["aux", "usb:x"]))

    def test_flip_adds_usb_and_remembers_the_output_before(self):
        a = self.app(sound_out="aux")
        a.flip_output("usb:" + USB_NAME)
        self.assertEqual(a.output, "mix")
        self.assertEqual(a.output_mix, ["aux", "usb:" + USB_NAME])
        self.assertEqual(a.st.get("sound_prev"), "aux")
        self.assertEqual(a.output_label(), "AUX + Logitech USB Headset")
        a.flip_output("usb:" + USB_NAME)
        self.assertEqual(a.output, "aux", "the device off: AUX alone")

    def test_no_sound_is_a_switch_that_brings_aux_back(self):
        a = self.app(sound_out="aux")
        cell = [c for c in a.output_cells() if c[0] == a.tr("w.none")][0]
        cell[3]()
        self.assertEqual(a.output, "off")
        cell = [c for c in a.output_cells() if c[0] == a.tr("w.none")][0]
        cell[3]()
        self.assertEqual(a.output, "aux", "with no AUX cell, No sound again is the way back")

    def test_a_device_alone_from_before_plays_beside_aux(self):
        a = self.app(sound_out="usb:" + USB_NAME)
        self.assertEqual(a.output_keys(), ["aux", "usb:" + USB_NAME])
        self.assertEqual(self.app(sound_out="off").output_keys(), [])

    def run_watch(self, a, here):
        with mock.patch("threading.Thread") as th, mock.patch.object(a, "toast"):
            from ui import audio as ui_audio
            with mock.patch.object(ui_audio, "keys_now", return_value=here), \
                    mock.patch.object(ui_audio, "sinks", return_value=[{"id": 40}]), \
                    mock.patch.object(ui_audio, "default_id", return_value="40"):
                a.snd_at, a.sound_due = 0.0, 0.0
                a.follow_sound()
                th.call_args.kwargs["target"]()

    def test_unplugged_goes_back_and_the_same_device_brings_it_back(self):
        # a = self.app(sound_out="usb:" + USB_NAME, sound_prev="hdmi")    # HDMI off (DSI only)
        a = self.app(sound_out="usb:" + USB_NAME, sound_prev="aux")
        a.snd_seen.add("usb:" + USB_NAME)
        self.run_watch(a, [("aux", "AUX")])
        self.assertEqual(a.output, "usb:" + USB_NAME, "missing once is not gone yet")
        self.run_watch(a, [("aux", "AUX")])
        # self.assertEqual(a.output, "hdmi")    # HDMI off (DSI only)
        self.assertEqual(a.output, "aux")
        self.assertTrue(a.sound_due)
        a.sound_due = 0.0
        a.snd_now = (("aux",), "40")
        other = "alsa_output.usb-Other_Speaker-00.analog-stereo"
        self.run_watch(a, [("aux", "AUX"), ("usb:" + other, "Other Speaker")])
        # a device just plugged in becomes an output at once, beside AUX
        self.assertEqual((a.output, a.output_mix), ("mix", ["aux", "usb:" + other]))
        self.assertEqual(a.st.get("sound_prev"), "aux", "and when it goes, the sound goes back there")
        self.assertTrue(a.sound_due)
        a.sound_due = 0.0
        # the same headset back, even on another profile: the sound on it again
        back = USB_NAME.rsplit(".", 1)[0] + ".iec958-stereo"
        self.run_watch(a, [("aux", "AUX"), ("usb:" + other, "Other Speaker"), ("usb:" + back, "Logitech USB Headset")])
        self.assertEqual(a.output_keys(), ["aux", "usb:" + back])
        self.assertTrue(a.sound_due)
        self.assertIsNone(a.st.get("sound_back"))

    def test_a_device_there_from_the_start_or_back_in_a_moment_is_not_chosen_by_itself(self):
        a = self.app(sound_out="aux")
        self.run_watch(a, [("aux", "AUX"), ("usb:" + USB_NAME, "Logitech USB Headset")])
        self.assertEqual(a.output, "aux", "there when the screen started: the choice stays")
        self.run_watch(a, [("aux", "AUX")])
        self.run_watch(a, [("aux", "AUX"), ("usb:" + USB_NAME, "Logitech USB Headset")])
        self.assertEqual(a.output, "aux", "away for a moment only (one odd answer): not a new device")

    def test_a_device_not_seen_yet_waits_a_little_after_the_start(self):
        a = self.app(sound_out="usb:" + USB_NAME)
        self.run_watch(a, [("aux", "AUX")])
        self.run_watch(a, [("aux", "AUX")])
        self.assertEqual(a.output, "usb:" + USB_NAME)
        a.snd_started -= 30
        self.run_watch(a, [("aux", "AUX")])
        self.assertEqual(a.output, "aux")

    def test_no_answer_from_pipewire_takes_nothing_away(self):
        a = self.app(sound_out="usb:" + USB_NAME)
        a.snd_seen.add("usb:" + USB_NAME)
        from ui import audio as ui_audio
        for _ in range(3):
            with mock.patch("threading.Thread") as th, mock.patch.object(ui_audio, "sinks", return_value=[]):
                a.snd_at, a.sound_due = 0.0, 0.0
                a.follow_sound()
                th.call_args.kwargs["target"]()
        self.assertEqual(a.output, "usb:" + USB_NAME)

    def test_the_watcher_waits_for_an_apply(self):
        a = self.app(sound_out="aux")
        a.snd_at, a.sound_due, a.snd_applying = 0.0, 0.0, True
        with mock.patch("threading.Thread") as th:
            a.follow_sound()
        th.assert_not_called()

    def test_the_output_page(self):
        a = self.app(sound_out="aux")
        cells = a.settings_cells()["outs"]
        self.assertEqual([c[0] for c in cells], ["Logitech USB Headset", a.tr("c.bt"), a.tr("w.none")],
                         "no AUX cell: it always plays")

    def test_recording_cells_and_no_stop_combo(self):
        a = self.app()
        with tempfile.TemporaryDirectory() as d:
            a.srec.folder = Path(d)
            a.srec.proc, a.srec.since = object(), time.time() - 65
            (Path(d) / "armonico-2026-10-26-120000.mkv").write_bytes(b"x")
            (Path(d) / "armonico-2026-10-26-130000.mkv").write_bytes(b"x")
            cells = a.settings_cells()["sys"]
            rec = next(c for c in cells if c[0] == a.tr("c.rec"))
            self.assertIn("01:0", rec[1])
            exp = next(c for c in a.settings_cells()["rec"] if c[0] == a.tr("c.recExport"))
            self.assertIsNotNone(exp[3])
            now = time.monotonic()
            a.down = {"up": now - 2, "down": now - 2}
            with mock.patch.object(a, "screen_rec_stop") as stop:
                a.follow_buttons()
            stop.assert_not_called()           # ▲ and ▼ together are not a way to stop it
            a.srec.proc = None

    def test_the_terminal_starts_and_stops_it(self):
        a = self.app()
        with mock.patch.object(a, "screen_rec_toggle") as on:
            a.mqtt_event("piano/device/rec", "on")
        on.assert_called_once()


class Recorder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = load("armonico_recorder_test", "device/ui/recorder.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_part_is_recorded_on_its_own(self):
        v = " ".join(self.rec.video_cmd((800, 480), "/x/.rec-video.mkv"))
        self.assertIn("-f rawvideo -pix_fmt rgb24 -video_size 800x480 -framerate 30 -i pipe:0", v)
        self.assertNotIn("wallclock", v)                # a frame's time is its place, not when it was read
        self.assertNotIn("pulse", v)
        a = " ".join(self.rec.audio_cmd("alsa_input.usb-mic", "/x/.rec-mic.mka"))
        self.assertIn("-f pulse -i alsa_input.usb-mic -copyts", a)
        self.assertNotIn("kmsgrab", v + a)

    def test_the_mux_moves_each_sound_to_the_first_frame(self):
        cmd = self.rec.mux_cmd("v.mkv", 100.0, [("d.mka", 100.25, "Armonico"), ("m.mka", 101.5, "Microphone")], "o.part")
        s = " ".join(cmd)
        self.assertIn("adelay=250:all=1", s)
        self.assertIn("adelay=1500:all=1", s)
        self.assertEqual(s.count("afftdn"), 1)          # the microphone's hiss only, not the device's sound
        self.assertEqual(cmd.count("-map"), 4)          # the picture, the mix first, then each sound alone
        self.assertIn("title=Armonico + Microphone", s)
        self.assertIn("-disposition:a:0 default", s)
        self.assertIn("-c:v copy", s)
        one = self.rec.mux_cmd("v.mkv", 100.0, [("d.mka", 99.9, "Armonico")], "o.part")
        self.assertEqual(one.count("-map"), 2)
        self.assertIn("adelay=0:all=1", " ".join(one))

    def test_the_name_is_in_the_device_time(self):
        import datetime
        r = self.rec.Recorder(self.tmp.name)
        with mock.patch.object(self.rec, "find_mic", return_value=(None, "")), \
                mock.patch.object(self.rec.subprocess, "Popen", side_effect=FileNotFoundError):
            r.start((800, 480), datetime.datetime(2026, 10, 27, 12, 5, 9))
        self.assertTrue((Path(self.tmp.name) / "last.log").read_text().endswith("armonico-2026-10-27-120509.mkv\n"))

    def test_the_screen_keeps_only_the_latest_frame_and_never_waits(self):
        r = self.rec.Recorder(self.tmp.name)
        r.proc, r.size = object(), (4, 2)
        pg = mock.Mock()
        pg.image.tobytes.side_effect = [b"a", b"b", b"c"]
        surf = mock.Mock()
        surf.get_size.return_value = (4, 2)
        for _ in range(3):
            r.next_at = 0
            r.feed(surf, pg)
        self.assertEqual(r.frame, b"c")
        self.assertEqual(r.dropped, 2)

    def test_the_mic_is_found_and_made_ready(self):
        out = "1\talsa_output.usb-X.analog-stereo.monitor\tPipeWire\n2\talsa_input.usb-Logitech_USB_Headset-00.mono-fallback\tPipeWire\n"
        calls = []

        def fake(*args, timeout=5):
            calls.append(args)
            return mock.Mock(stdout=out if args[:2] == ("list", "short") else "", returncode=0)
        with mock.patch.object(self.rec, "_pactl", fake):
            name, label = self.rec.find_mic()
        self.assertEqual(name, "alsa_input.usb-Logitech_USB_Headset-00.mono-fallback")
        self.assertEqual(label, "Logitech USB Headset")
        self.assertIn(("set-source-mute", name, "0"), calls)
        self.assertIn(("set-source-volume", name, "100%"), calls)

    def test_an_output_only_headset_gets_its_microphone(self):
        cards = ("Card #1\n\tName: alsa_card.usb-Logitech_USB_Headset-00\n\tProfiles:\n"
                 "\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)\n"
                 "\t\toutput:analog-stereo+input:mono-fallback: Analog Stereo Output + Mono Input "
                 "(sinks: 1, sources: 1, priority: 6501, available: yes)\n"
                 "\tActive Profile: output:analog-stereo\n")
        calls = []

        def fake(*args, timeout=5):
            calls.append(args)
            return mock.Mock(stdout=cards if args == ("list", "cards") else "", returncode=0)
        with mock.patch.object(self.rec, "_pactl", fake), mock.patch.object(self.rec.time, "sleep"):
            self.rec._usb_input_profile()
        self.assertIn(("set-card-profile", "alsa_card.usb-Logitech_USB_Headset-00",
                       "output:analog-stereo+input:mono-fallback"), calls)


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.src = base / "data" / "device" / "recordings"
        self.src.mkdir(parents=True)
        self.stick = base / "stick"
        self.stick.mkdir()
        with mock.patch.dict(os.environ, {"DATA_DIR": str(base / "data")}):
            self.rec = load("armonico_screen_rec_test", "device/screen_rec.py")

    def tearDown(self):
        self.tmp.cleanup()

    def run_export(self, names=None):
        with mock.patch.object(self.rec, "stick", return_value=self.stick):
            return self.rec.export(names)

    def test_moved_only_after_a_full_copy(self):
        f = self.src / "armonico-2026-10-26-120000.mkv"
        f.write_bytes(os.urandom(5000))
        self.assertEqual(self.run_export()[0], 1)
        self.assertFalse(f.exists())
        self.assertEqual((self.stick / f.name).stat().st_size, 5000)

    def test_links_lead_nowhere(self):
        victim = Path(self.tmp.name) / "victim"
        victim.write_text("keep")
        secret = Path(self.tmp.name) / "secret"
        secret.write_text("root only")
        (self.src / "armonico-2026-10-26-120000.mkv").symlink_to(secret)     # planted by the screen's user
        f = self.src / "armonico-2026-10-26-130000.mkv"
        f.write_bytes(b"video")
        (self.stick / (f.name + ".part")).symlink_to(victim)                 # planted on the stick
        self.assertEqual(self.run_export()[0], 1)
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse((self.stick / "armonico-2026-10-26-120000.mkv").exists())
        self.assertEqual((self.stick / f.name).read_bytes(), b"video")

    def test_only_the_named_one_and_none_without_a_stick(self):
        a = self.src / "armonico-2026-10-26-120000.mkv"
        b = self.src / "armonico-2026-10-26-130000.mkv"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        self.assertEqual(self.run_export([b.name])[0], 1)
        self.assertTrue(a.exists())
        with mock.patch.object(self.rec, "stick", return_value=None):
            self.assertEqual(self.rec.export(), (0, ""))
        self.assertTrue(a.exists())

    def test_a_fat_stick_is_mounted_in_the_device_zone(self):
        ra = load("armonico_ra_tz", "device/root_actions.py")
        d = Path(self.tmp.name) / "data" / "device"
        (d / "settings.json").write_text(json.dumps({"city": {"tz": "Asia/Jerusalem"}}))
        with mock.patch.object(ra, "DATA_DIR", Path(self.tmp.name) / "data"):
            self.assertIn(ra.device_utc_offset(), (120, 180))
            # a zone that is no zone, or no city at all (never chosen): the screen's default city's zone,
            # as the recordings' names use; before, no offset at all left the stick's times in UTC
            (d / "settings.json").write_text(json.dumps({"city": {"tz": "../../etc/x"}}))
            self.assertIn(ra.device_utc_offset(), (120, 180))
            (d / "settings.json").write_text(json.dumps({"lang": "he"}))
            self.assertIn(ra.device_utc_offset(), (120, 180))

    def test_an_old_fat_mount_is_opened_again_with_the_offset(self):
        ra = mock.Mock()
        ra.backup_mod.return_value.usb_drives.return_value = [
            {"usable": True, "disk": "/dev/sda", "fs": "vfat", "mount": str(self.stick)}]
        ra.system_disk.return_value = False
        ra.mount_usb.side_effect = lambda d: str(self.stick)
        ra.run.return_value = mock.Mock(returncode=0)
        with mock.patch.object(self.rec, "root_actions", return_value=ra), \
                mock.patch.object(self.rec, "_has_offset", return_value=False):
            self.rec.stick()
        ra.run.assert_called_once_with("umount", str(self.stick), timeout=30)
        self.assertEqual(ra.mount_usb.call_args_list[-1].args[0]["mount"], "")

    def test_root_actions_know_it(self):
        ra = load("armonico_ra_rec", "device/root_actions.py")
        self.assertIn("rec-export", ra.ACTIONS)
        self.assertNotIn("rec-start", ra.ACTIONS)


class Loudness(unittest.TestCase):
    def test_the_volume_is_heard_along_its_whole_range(self):
        pi = load("armonico_pi_loud", "device/armonico_pi.py")
        self.assertEqual(pi.loudness(1), 1.0)
        self.assertEqual(pi.loudness(0), 0.0)
        self.assertAlmostEqual(pi.loudness(0.5), 0.125)       # 18 dB down, not 6
        self.assertEqual(pi.loudness(2), 1.0)


if __name__ == "__main__":
    unittest.main()
