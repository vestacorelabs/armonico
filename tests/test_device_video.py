"""Regression checks for the Armonico Pi background video playback path."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DeviceVideo(unittest.TestCase):
    def test_video_streams_use_one_playbin(self):
        source = (ROOT / "device" / "screen.py").read_text()
        media = source[source.index("class Media:"):source.index("# The screen", source.index("class Media:"))]

        self.assertIn('ElementFactory.make("playbin"', media)
        self.assertIn('player.set_property("video-sink"', media)
        self.assertIn('player.set_property("audio-sink", audio_sink)', media)
        self.assertIn('query_position(self.gst.Format.TIME)', media)
        # These were the old independent paths responsible for startup delay/drift.
        self.assertNotIn('"-re"', media)
        self.assertNotIn('"fps=24"', media)
        self.assertNotIn('pygame.mixer.music.load(str(audio))', media)
        self.assertNotIn('subprocess.Popen(cmd', media)

    def test_device_installer_installs_gstreamer_and_checks_python_bindings(self):
        installer = (ROOT / "install.sh").read_text()
        for package in (
            "python3-gi", "python3-gst-1.0", "gstreamer1.0-alsa",
            "gstreamer1.0-plugins-base", "gstreamer1.0-plugins-good", "gstreamer1.0-libav",
        ):
            self.assertIn(package, installer)
        self.assertIn('gi.require_version("Gst", "1.0")', installer)
        self.assertIn("Gst.init(None)", installer)

    def test_unsupported_video_fallback_is_not_used(self):
        source = (ROOT / "device" / "screen.py").read_text()
        media = source[source.index("class Media:"):source.index("# The screen", source.index("class Media:"))]
        self.assertIn("Never split the audio and picture into independent players", media)
        self.assertIn("VIDEO_SUFFIXES", media)


if __name__ == "__main__":
    unittest.main()
