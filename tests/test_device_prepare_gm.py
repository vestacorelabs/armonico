"""device/armonico_pi.py prepare_gm: an update with the same voices makes nothing again (the installer copies the
files with new times, which made all 361 again, minutes on a Pi 3), a changed FLAC is made again."""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg here")
class PrepareGm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        with mock.patch.dict(os.environ, {"DATA_DIR": str(base / "data"), "SDL_AUDIODRIVER": "dummy"}):
            spec = importlib.util.spec_from_file_location("armonico_pi_prepare_test", ROOT / "device" / "armonico_pi.py")
            self.m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.m)
        self.src = base / "gm"
        (self.src / "piano").mkdir(parents=True)
        for n, f in (("60", 440), ("64", 554)):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={f}:duration=0.2",
                            str(self.src / "piano" / f"{n}.flac")], check=True)
        self.out = base / "data" / "device" / "gm"

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self):
        made = []
        real = self.m._ffmpeg_all

        def counted(jobs):
            made.extend(j[0].name for j in jobs)
            return real(jobs)
        with mock.patch.object(self.m, "GM_SAMPLES", self.src), mock.patch.object(self.m, "GM_DIR", self.out), \
                mock.patch.object(self.m, "_ffmpeg_all", counted):
            self.m.prepare_gm()
        return sorted(made)

    def test_only_what_changed_is_made_again(self):
        self.assertEqual(self.run_it(), ["60.flac", "64.flac"])
        self.assertTrue((self.out / "piano" / "60.wav").exists())
        for f in self.src.glob("*/*.flac"):
            os.utime(f, None)                                     # copied again by an update: new times
        self.assertEqual(self.run_it(), [], "same content, nothing made")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=0.2",
                        str(self.src / "piano" / "64.flac")], check=True)
        self.assertEqual(self.run_it(), ["64.flac"])


if __name__ == "__main__":
    unittest.main()
