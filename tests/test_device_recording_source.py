"""The screen recording's sound source (a chosen output, the default sink's monitor) and a Bluetooth microphone."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
from ui import recorder as R  # noqa: E402


def out(text):
    return mock.Mock(stdout=text)


class Source(unittest.TestCase):
    SINKS = ("37\tarmonico_both\tPipeWire\tfloat32le 2ch 48000Hz\tIDLE\n"
             "61\talsa_output.platform-fe00b840.mailbox.stereo-fallback\tPipeWire\ts16le 2ch 48000Hz\tRUNNING\n"
             "62\talsa_output.platform-fef00700.hdmi.hdmi-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
             "70\tbluez_output.AA_BB.1\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n")

    def pactl(self, *a, **k):
        if a[:1] == ("get-default-sink",):
            return out("alsa_output.platform-fef00700.hdmi.hdmi-stereo\n")
        return out(self.SINKS)

    def test_auto_is_the_default_sinks_monitor(self):
        with mock.patch.object(R, "_pactl", self.pactl):
            self.assertEqual(R.device_monitor(), "alsa_output.platform-fef00700.hdmi.hdmi-stereo.monitor")

    def test_a_connected_output_can_be_chosen(self):
        with mock.patch.object(R, "_pactl", self.pactl):
            self.assertEqual(R.device_monitor("bluez_output.AA_BB.1"), "bluez_output.AA_BB.1.monitor")
            self.assertEqual([l for _, l in R.outputs()], ["Mix", "Jack", "HDMI", "Bluetooth"])

    def test_an_unplugged_choice_is_auto(self):
        with mock.patch.object(R, "_pactl", self.pactl):
            self.assertEqual(R.device_monitor("alsa_output.usb-gone.analog-stereo"),
                             "alsa_output.platform-fef00700.hdmi.hdmi-stereo.monitor")


class BluetoothMic(unittest.TestCase):
    def test_a_bluetooth_headset_is_not_the_microphone(self):
        """Its microphone needs the headset profile, which made everything heard mono and narrow-band."""
        r = out("9\tbluez_input.AA_BB.0\tPipeWire\ts16le 1ch 16000Hz\tIDLE\n"
                "8\talsa_output.x.monitor\tPipeWire\ts16le 2ch\tIDLE\n")
        with mock.patch.object(R, "_pactl", lambda *a, **k: r):
            self.assertIsNone(R._mic_source())

    def test_no_card_is_moved_to_the_headset_profile(self):
        calls = []

        def pactl(*a, **k):
            calls.append(a)
            return out("")
        with mock.patch.object(R, "_pactl", pactl), mock.patch.object(R.time, "sleep"):
            self.assertEqual(R.find_mic(), (None, ""))
        self.assertFalse([c for c in calls if c[:1] == ("set-card-profile",)])


if __name__ == "__main__":
    unittest.main()
