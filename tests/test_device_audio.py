"""The sound output through PipeWire: which of its outputs each setting picks."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("armonico_device_audio", ROOT / "device" / "ui" / "audio.py")
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


def node(i, name, card, desc):
    return {"id": i, "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": "Audio/Sink", "node.name": name, "alsa.card_name": card,
                               "node.description": desc}}}


DUMP = json.dumps([
    node(40, "alsa_output.platform-bcm2835_audio.stereo-fallback", "bcm2835 Headphones", "Built-in Audio Stereo"),
    node(41, "alsa_output.platform-fef00700.hdmi.hdmi-stereo", "vc4-hdmi-0", "Built-in Audio Digital Stereo (HDMI)"),
    node(42, "alsa_output.platform-fef05700.hdmi.hdmi-stereo", "vc4-hdmi-1", "Built-in Audio Digital Stereo (HDMI 2)"),
    node(50, "armonico_both", "", "Armonico AUX + HDMI"),
    {"id": 7, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source", "node.name": "mic"}}},
])


class Pick(unittest.TestCase):
    def setUp(self):
        self.found = audio.sinks(DUMP)

    def test_only_outputs(self):
        self.assertEqual([s["id"] for s in self.found], [40, 41, 42, 50])

    def test_each_setting(self):
        self.assertEqual(audio.pick("aux", self.found), 40)
        self.assertEqual(audio.pick("off", self.found), 40)
        # self.assertEqual(audio.pick("both", self.found), 50)    # HDMI off (DSI only)
        self.assertIsNone(audio.pick("both", self.found))

    @unittest.skip("HDMI off (DSI only): HDMI is not offered on the device")
    def test_hdmi_follows_the_port_with_the_screen(self):
        self.assertEqual(audio.pick("hdmi", self.found, port=0), 41)
        self.assertEqual(audio.pick("hdmi", self.found, port=1), 42)

    def test_nothing_yet(self):
        self.assertIsNone(audio.pick("hdmi", []))
        self.assertEqual(audio.sinks("not json"), [])


if __name__ == "__main__":
    unittest.main()
