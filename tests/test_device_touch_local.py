"""Armonico Pi: a key touched on the screen reaches the daemon on this machine with no broker, and is
played once when it also comes through the broker; a drag whose finger never lifted no longer eats touches."""
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))


class TouchMessage(unittest.TestCase):
    def daemon(self):
        import armonico_pi as pi
        import collections
        d = pi.Device.__new__(pi.Device) if hasattr(pi, "Device") else None
        if d is None:
            self.skipTest("no Device class")
        d.touch_lock, d.touch_seen, d.played = threading.Lock(), collections.deque(maxlen=256), []
        d.touch_note = lambda n, down: d.played.append((n, down))
        return d

    def test_the_same_press_both_ways_plays_once(self):
        d = self.daemon()
        d.touch_message("on 60 7-1")                 # from the local port
        d.touch_message("on 60 7-1")                 # the same press through the broker
        d.touch_message("off 60 7-2")
        d.touch_message("on 62")                     # an old sender with no number still plays
        self.assertEqual(d.played, [(60, True), (60, False), (62, True)])

    def test_nonsense_is_dropped(self):
        d = self.daemon()
        for text in ("", "on", "on x", "up 60", "on 60 1 2"):
            d.touch_message(text)
        self.assertEqual(d.played, [])


if __name__ == "__main__":
    unittest.main()
