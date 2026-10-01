"""The Armonico Pi device: the parts that can be checked without a Pi, GPIO or ALSA."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Midi(unittest.TestCase):
    def setUp(self):
        self.dev = load("armonico_pi", ROOT / "device" / "armonico_pi.py")

    def test_running_status_and_realtime_bytes(self):
        """aplaymidi and amidi may leave the status byte out between notes, and the clock
        byte can sit in the middle of anything: every note still comes out whole."""
        got = self.dev.split_midi(bytes([0x90, 60, 90, 0xF8, 64, 90, 0x80, 60, 0, 0xC0, 5]))
        self.assertEqual(got, [bytes([0x90, 60, 90]), bytes([0x90, 64, 90]),
                               bytes([0x80, 60, 0]), bytes([0xC0, 5])])

    def test_sysex_is_one_message(self):
        vol = bytes([0xF0, 0x7F, 0x7F, 0x04, 0x01, 0x7F, 0x7F, 0xF7])
        self.assertEqual(self.dev.split_midi(vol + bytes([0x90, 60, 1])), [vol, bytes([0x90, 60, 1])])

    def test_every_key_has_a_sample_within_a_semitone(self):
        for note in range(self.dev.LOW, self.dev.HIGH + 1):
            src, shift = self.dev.sample_for(note)
            self.assertIsNotNone(src, note)
            self.assertLessEqual(abs(shift), 1, note)
            self.assertTrue(src.exists())

    def test_the_wiring_has_thirteen_different_keys(self):
        pins = self.dev.KEY_PINS + [int(p) for p in self.dev.BUTTON_PINS.values()]
        self.assertEqual(len(self.dev.KEY_PINS), 13)
        self.assertEqual(len(pins), len(set(pins)), "a GPIO pin is used twice")
        self.assertNotIn(3, pins, "GPIO3 belongs to the power button")


class DeviceMode(unittest.TestCase):
    def test_switch_keeps_every_other_line(self):
        """The switch rewrites the broker lines only: the keyboard, the paths and anything
        added by hand stay exactly as they were."""
        cli = load("armonico_cli", ROOT / "cli" / "armonico.py")
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.env"
            cfg.write_text("MQTT_HOST='10.0.0.5'\nMQTT_PORT='1883'\nMQTT_USER='ha'\nMQTT_PASS='x'\n"
                           "MQTT_LOCAL='no'\nMIDI_DEVICE='Armonico Pi'\nAMIDI_DEVICE='hw:7,0'\n")
            modes = Path(d) / "modes"
            modes.mkdir()
            (modes / "standalone.env").write_text("MQTT_HOST='localhost'\nMQTT_PORT='1883'\nMQTT_USER='piano'\n"
                                                  "MQTT_PASS='y'\nMQTT_LOCAL='yes'\n")
            with mock.patch.object(cli, "CONFIG", cfg), mock.patch.object(cli, "MODES_DIR", modes), \
                 mock.patch.object(cli.os, "chown"), mock.patch.object(cli.subprocess, "run") as run:
                run.return_value.returncode = 1
                cli.switch_mode("standalone")
            text = cfg.read_text()
            self.assertIn("MQTT_HOST='localhost'", text)
            self.assertIn("MQTT_LOCAL='yes'", text)
            self.assertIn("AMIDI_DEVICE='hw:7,0'", text)
            self.assertIn("MIDI_DEVICE='Armonico Pi'", text)
            self.assertIn("MQTT_HOST='10.0.0.5'", (modes / "lab.env").read_text())   # the way back

    def test_set_lab_keeps_the_lab_broker_without_switching(self):
        cli = load("armonico_cli2", ROOT / "cli" / "armonico.py")
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.env"
            cfg.write_text("MQTT_HOST='localhost'\nMQTT_LOCAL='yes'\n")
            modes = Path(d) / "modes"
            with mock.patch.object(cli, "CONFIG", cfg), mock.patch.object(cli, "MODES_DIR", modes), \
                 mock.patch.object(cli.os, "chown"), mock.patch.object(cli.os, "geteuid", return_value=0):
                cli.cmd_device_mode(["set-lab", "10.0.0.5", "1884", "ha", "pw"])
            lab = (modes / "lab.env").read_text()
            self.assertIn("MQTT_HOST='10.0.0.5'", lab)
            self.assertIn("MQTT_PORT='1884'", lab)
            self.assertIn("MQTT_LOCAL='no'", lab)
            self.assertIn("MQTT_HOST='localhost'", cfg.read_text())      # nothing was switched

    def test_switch_to_shared_does_not_overwrite_new_profile_when_already_shared(self):
        cli = load("armonico_cli3", ROOT / "cli" / "armonico.py")
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.env"
            cfg.write_text("MQTT_HOST='old-shared'\nMQTT_PORT='1883'\nMQTT_USER='old'\n"
                           "MQTT_PASS='oldpass'\nMQTT_LOCAL='no'\n")
            modes = Path(d) / "modes"
            modes.mkdir()
            (modes / "lab.env").write_text("MQTT_HOST='new-shared'\nMQTT_PORT='1884'\n"
                                            "MQTT_USER='new'\nMQTT_PASS='newpass'\nMQTT_LOCAL='no'\n")
            with mock.patch.object(cli, "CONFIG", cfg), mock.patch.object(cli, "MODES_DIR", modes), \
                 mock.patch.object(cli.os, "chown"), mock.patch.object(cli.subprocess, "run") as run:
                run.return_value.returncode = 1
                cli.switch_mode("lab")
            self.assertIn("MQTT_HOST='new-shared'", cfg.read_text())
            self.assertIn("MQTT_HOST='new-shared'", (modes / "lab.env").read_text())

    def test_lessons_get_a_range_the_songs_fit_in(self):
        """The keys sound in every octave. Telling the engine the keyboard has one octave of keys made every
        song too wide for it, and every song showed as empty on the device."""
        for path in ("install.sh", "setup/setup_web.py"):
            text = (ROOT / path).read_text()
            self.assertNotIn('KEY_HIGHEST="71"', text, path)
            self.assertNotIn('C_KEY_HIGHEST="71"', text, path)


class DeviceScreenConfiguration(unittest.TestCase):
    def test_rest_choices_are_only_bundled_video_or_animation(self):
        source = (ROOT / "device" / "screen.py").read_text()
        self.assertIn('REST_MODES = ("video", "animation")', source)
        self.assertNotIn("REST_YOUTUBE", source)
        self.assertNotIn("fetch_youtube", source)
        self.assertNotIn("REST_FILES", source)
        self.assertIn("LOOP_PAUSE_SECONDS = 1.0", source)
        self.assertIn("time.monotonic() + LOOP_PAUSE_SECONDS", source)
        self.assertIn("self.activate(self.CELLS[i])  # one touch selects and activates the setting", source)

    def test_public_mode_name_is_external_broker_with_legacy_aliases(self):
        cli = (ROOT / "cli" / "armonico.py").read_text()
        screen = (ROOT / "device" / "screen.py").read_text()
        self.assertIn('"external-broker"', cli)
        self.assertIn('"setup-external"', cli)
        self.assertIn('"shared-broker"', cli)          # the older names still work
        self.assertIn('"setup-shared"', cli)
        self.assertIn('"External broker"', screen)
        # Keep these on-disk profile names for upgrades from installed versions.
        self.assertIn('"lab"', cli)


if __name__ == "__main__":
    unittest.main()
