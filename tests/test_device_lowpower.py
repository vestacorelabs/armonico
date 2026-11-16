"""device/lowpower.py: which Pi gets the light settings, and what they change."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))

import lowpower  # noqa: E402


class Detect(unittest.TestCase):
    def d(self, model, mb, env=None):
        return lowpower.detect(env or {}, model, mb)

    def test_older_pis_are_low(self):
        self.assertTrue(self.d("Raspberry Pi 3 Model B Rev 1.2", 905))
        self.assertTrue(self.d("Raspberry Pi 3 Model B Plus Rev 1.3", 905))
        self.assertTrue(self.d("Raspberry Pi 2 Model B Rev 1.1", 925))
        self.assertTrue(self.d("Raspberry Pi Zero 2 W Rev 1.0", 416))
        self.assertTrue(self.d("Raspberry Pi Model B Plus Rev 1.2", 430))

    def test_pi4_and_newer_are_not(self):
        self.assertFalse(self.d("Raspberry Pi 4 Model B Rev 1.5", 7800))
        self.assertFalse(self.d("Raspberry Pi 4 Model B Rev 1.4", 1850))
        self.assertFalse(self.d("Raspberry Pi 5 Model B Rev 1.0", 4000))

    def test_small_memory_alone_is_low(self):
        self.assertTrue(self.d("", 900))
        self.assertFalse(self.d("", 0), "an unknown memory is not judged")
        self.assertFalse(self.d("", 16000))

    def test_env_wins(self):
        self.assertTrue(self.d("Raspberry Pi 5 Model B Rev 1.0", 8000, {"ARMONICO_LOWPOWER": "1"}))
        self.assertFalse(self.d("Raspberry Pi 3 Model B Rev 1.2", 905, {"ARMONICO_LOWPOWER": "0"}))


class Used(unittest.TestCase):
    def run_py(self, code, **env):
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); %s" % (str(ROOT / "device"), code)],
                             env={**os.environ, **env}, capture_output=True, text=True, timeout=120)
        return out.stdout.strip()

    def test_one_video_for_every_pi_at_the_screen_size(self):
        video = ROOT / "device" / "media" / "armonico-japanese.mp4"
        self.assertTrue(video.exists())
        self.assertEqual(sorted(p.name for p in (ROOT / "device" / "media").iterdir()), ["armonico-japanese.mp4"])
        if subprocess.call(["which", "ffprobe"], stdout=subprocess.DEVNULL) == 0:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name",
                                  "-of", "csv=p=0", str(video)], capture_output=True, text=True)
            self.assertIn("h264,800,480", out.stdout)
        for low in ("1", "0"):
            self.assertEqual(self.run_py("import soak; print(soak.REST_VIDEO.name)", ARMONICO_LOWPOWER=low),
                             "armonico-japanese.mp4")

    def test_frames_and_live_view_rates(self):
        low = self.run_py("from ui import app, live; print(app.FPS, live.FPS)", ARMONICO_LOWPOWER="1")
        full = self.run_py("from ui import app, live; print(app.FPS, live.FPS)", ARMONICO_LOWPOWER="0")
        self.assertEqual(low.split()[-2:], ["30", "3"], "the screen stays at 30 (the video's rate); only the live view slows")
        self.assertEqual(full.split()[-2:], ["30", "5"])


class SoakWatches(unittest.TestCase):
    def test_frames_line_is_read(self):
        import soak
        m = soak.Monitor.FRAMES.search("Frames: 22.4 a second, drawing 31.0 ms, flip 2.5 ms (rest, anime, video 24.0")
        self.assertEqual(m.groups(), ("22.4", "31.0", "2.5", "rest"))


if __name__ == "__main__":
    unittest.main()
