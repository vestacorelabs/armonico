"""The song list while the songs are read: 9.11.2026, a big songs folder kept the lesson engine at full
processor for minutes after a start, since every overview read every song again at the same time, and port
8099 did not answer. Now one song is read at a time, in the background, and an overview never waits for it."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))
import piano_game as game          # noqa: E402


class SongWarm(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        (self.t / "songs").mkdir()
        import mido
        m = mido.MidiFile()
        tr = mido.MidiTrack()
        m.tracks.append(tr)
        for n in (60, 62, 64, 65, 67, 69, 71, 72):
            tr.append(mido.Message("note_on", note=n, velocity=80, time=0))
            tr.append(mido.Message("note_off", note=n, velocity=0, time=240))
        m.save(str(self.t / "songs" / "One.mid"))
        self.patches = [mock.patch.object(game, "MIDI_DIR", self.t / "songs"),
                        mock.patch.object(game, "_SONG_DISK", self.t / "songs_cache.json"),
                        mock.patch.object(game, "_SONG_CACHE", {}),
                        mock.patch.object(game, "_SONG_WARM", {"started": False, "done": False})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_an_overview_does_not_wait_for_the_songs(self):
        slow = threading.Event()
        real = game.load_song

        def load(name):
            slow.wait(5)
            return real(name)
        with mock.patch.object(game, "load_song", load):
            t0 = time.time()
            out = game.song_list()
            self.assertLess(time.time() - t0, 1.0, "no waiting for a song to be read")
            self.assertEqual([x["name"] for x in out], ["One"])
            slow.set()
            for _ in range(50):
                if game._SONG_WARM["done"]:
                    break
                time.sleep(0.1)
        self.assertTrue(game._SONG_WARM["done"])
        self.assertGreater(game.song_list()[0]["total"], 0)

    def test_after_a_restart_the_songs_come_from_the_disk(self):
        game._SONG_WARM["done"] = True
        game.song_list()
        self.assertTrue((self.t / "songs_cache.json").exists())
        game._SONG_CACHE.clear()
        with mock.patch.object(game, "load_song", side_effect=AssertionError("read again")):
            self.assertGreater(game.song_list()[0]["total"], 0)


if __name__ == "__main__":
    unittest.main()
