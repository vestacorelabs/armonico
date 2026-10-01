"""The Android bridge (android/app/src/main/python/armonico_android/bridge.py), driven directly
with a stand-in for the keyboard and one for the broker: the same topics and replies as the shell
bridge for songs, the alarm, the stop key, pause, shortcuts, learning, the keyboard going away,
the guarded reset, and the parser of the keyboard's raw bytes."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "android" / "app" / "src" / "main" / "python"))
sys.path.insert(0, str(ROOT / "lessons"))

from armonico_android import bridge as B  # noqa: E402


class FakeLink:
    def __init__(self):
        self.conn = True
        self.sent = []

    def send(self, b):
        if not self.conn:
            raise IOError("gone")
        self.sent.append(bytes(b))
        return True

    def isConnected(self):
        return self.conn

    def deviceName(self):
        return "Test Piano"

    def reopen(self):
        pass

    def status(self, j):
        pass


class FakeMqtt:
    def __init__(self):
        self.out = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.out.append((topic, payload, retain))

    def subscribe(self, *a):
        pass

    def reconnect(self):
        pass

    def last(self, topic):
        v = [p for t, p, r in self.out if t == topic]
        return v[-1] if v else None


class AndroidBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        songs = self.tmp / "songs"
        songs.mkdir()
        for f in (ROOT / "setup" / "music").glob("*.mid"):
            shutil.copy(f, songs)
        self.link = FakeLink()
        cfg = dict(host="127.0.0.1", port=1883, data_dir=str(self.tmp), songs_dir=str(songs),
                   stop_key=36, lang="en", admin_secret="abc")
        self.b = B.Bridge(cfg, self.link)
        self.mq = FakeMqtt()
        self.b.mq = self.mq
        self.b.running = True
        self.b.connected = True

    def tearDown(self):
        self.b._end_player()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def key(self, n):
        self.b._midi_message((0x90, n, 100))

    def test_away_states_as_the_shell_bridge(self):
        """reconnecting, timeout, then stuck with one alert, and "The keyboard is back." on return."""
        self.b.broker_up = True
        self.link.conn = False
        self.b._device(False)
        self.assertEqual(self.mq.last("piano/status"), "disconnected")
        t0 = self.b.away_since
        self.b._away(t0 + 1)
        self.assertEqual(self.mq.last("piano/status"), "reconnecting")
        self.b._away(t0 + B.RECONNECT_TIMEOUT + 1)
        self.assertEqual(self.mq.last("piano/status"), "timeout")
        self.assertIsNone(self.mq.last("piano/alert"))
        stuck = t0 + B.RECONNECT_TIMEOUT + (B.STUCK_THRESHOLD - 1) * B.START_CYCLE
        self.b._away(stuck)
        self.b._away(stuck + 60)
        self.assertEqual(self.mq.last("piano/status"), "stuck")
        alerts = [p for t, p, r in self.mq.out if t == "piano/alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("has not come back after 3 tries", alerts[0])
        self.assertFalse([r for t, p, r in self.mq.out if t == "piano/alert" and r])
        self.link.conn = True
        self.b._device(True)
        self.assertEqual(self.mq.last("piano/status"), "online")
        self.assertEqual(self.mq.last("piano/alert"), "The keyboard is back.")

    def test_song_plays_and_stop_key_stops_it(self):
        self.b._command("piano/play", "ode_to_joy|Test")
        self.assertEqual(self.mq.last("piano/play/status"), "playing")
        self.assertEqual(self.mq.last("piano/play/source"), "Test")
        time.sleep(0.6)
        self.assertTrue(self.link.sent)
        self.key(36)
        self.assertEqual(self.mq.last("piano/play/stopped_by"), "Stop key (C2)")
        self.assertEqual(self.mq.last("piano/play/status"), "idle")
        self.assertEqual(self.link.sent[-1], B.panic_bytes())

    def test_alarm_blocks_songs_and_stops_with_reason(self):
        self.b._command("piano/alarm/set", "fur_elise|Morning")
        self.assertEqual(self.mq.last("piano/alarm/status"), "on")
        self.b._command("piano/play", "ode_to_joy")
        self.assertEqual(self.mq.last("piano/play/current"), "fur_elise")
        self.b._command("piano/stop", "Kitchen")
        self.assertEqual(self.mq.last("piano/alarm/stopped_by"), "Kitchen")
        self.assertEqual(self.mq.last("piano/alarm/last_song"), "fur_elise")

    def test_alarm_that_cannot_start_says_why(self):
        self.b._command("piano/alarm/set", "nothing_here")
        self.assertEqual(self.mq.last("piano/alarm/error"), "Song not found: nothing_here")
        self.assertEqual(self.mq.last("piano/alarm/status"), "failed")
        self.link.conn = False
        self.b._command("piano/alarm/set", "fur_elise")
        self.assertEqual(self.mq.last("piano/alarm/error"), "Keyboard not connected")

    def test_pause_and_resume(self):
        self.b._command("piano/play", "prelude_in_c")
        time.sleep(0.4)
        self.b._command("piano/pause", "pause")
        self.assertEqual(self.mq.last("piano/play/paused"), "on")
        time.sleep(0.2)
        n = len(self.link.sent)
        time.sleep(0.5)
        self.assertEqual(len(self.link.sent), n)
        self.b._command("piano/pause", "toggle")
        self.assertEqual(self.mq.last("piano/play/paused"), "off")
        time.sleep(0.5)
        self.assertGreater(len(self.link.sent), n)

    def test_long_and_short_shortcuts(self):
        self.b.handle_shortcut_cmd("add|1|night 60 62 64 65")
        self.b.handle_shortcut_cmd("add|1|lamp 70")
        for n in (50, 60, 62, 64, 65):
            self.key(n)
        self.assertEqual(self.mq.last("piano/shortcut"), "night")
        self.b.shortcut_lock_until = 0
        self.key(70)
        self.b._on_idle()
        self.assertEqual(self.mq.last("piano/shortcut"), "night")      # not before the pause
        self.b.last_key_ms -= B.SHORT_IDLE_MS + 10
        self.b._on_idle()
        self.assertEqual(self.mq.last("piano/shortcut"), "lamp")

    def test_learning_saves_after_silence(self):
        self.b.handle_shortcut_cmd("learn|7|wake")
        for n in (40, 41, 42, 43):
            self.key(n)
        self.b.last_key_ms -= B.LEARN_IDLE_MS + 10
        self.b._on_idle()
        self.assertTrue(self.mq.last("piano/shortcut/reply").startswith("7|✅ Shortcut wake saved\n40 41 42 43"))
        self.assertIn("40 41 42 43|wake", (self.tmp / "shortcuts.conf").read_text())

    def test_stop_key_is_reserved(self):
        self.b.handle_shortcut_cmd("add|1|bad 36")
        self.assertIn("reserved", self.mq.last("piano/shortcut/reply"))

    def test_unplugged_during_the_alarm(self):
        self.b._command("piano/alarm/set", "fur_elise")
        self.link.conn = False
        self.b._device(False)
        self.assertEqual(self.mq.last("piano/alarm/stopped_by"), "Keyboard disconnected")
        self.assertEqual(self.mq.last("piano/status"), "disconnected")
        self.assertEqual(self.mq.last("piano/game/event"), "disconnected")
        self.link.conn = True
        self.b._device(True)
        self.assertEqual(self.mq.last("piano/status"), "online")
        self.assertIsNotNone(self.mq.last("piano/reconnect/downtime"))

    def test_reset_needs_the_secret(self):
        self.b.handle_shortcut_cmd("reset|9|secret:wrong")
        self.assertTrue(self.mq.last("piano/shortcut/reply").startswith("9|🚫"))
        self.b.handle_shortcut_cmd("reset|9|secret:abc")
        self.assertTrue(self.mq.last("piano/shortcut/reply").startswith("9|✅ Full reset done"))

    def test_hebrew_replies_are_laid_out(self):
        self.b.lang = "he"
        self.b.handle_shortcut_cmd("cancel|5|")
        self.assertTrue(self.mq.last("piano/shortcut/reply").startswith("5|‏"))

    def test_lessons_answer_plainly(self):
        self.b.handle_game_cmd("lesson|3|fur_elise")
        self.assertEqual(self.mq.last("piano/shortcut/reply"), "3|⚠️ The lesson engine is not running: its files are missing")

    def test_a_measured_key_goes_nowhere_else(self):
        self.b.handle_shortcut_cmd("add|1|lamp 60")
        self.b.capture = True
        self.key(60)
        self.assertEqual(self.b.captured[0], 60)
        self.assertFalse(self.b.capture)
        self.assertIsNone(self.mq.last("piano/notes"))
        self.b.last_key_ms -= B.SHORT_IDLE_MS + 10
        self.b._on_idle()
        self.assertIsNone(self.mq.last("piano/shortcut"))

    def test_parser(self):
        p = B.MidiParser()
        msgs = p.feed(bytes([0xF8, 0x90, 60, 100, 62, 0, 0xFE, 0xF0, 1, 2, 0xF7, 0xB0, 64, 127, 0xC0, 5]))
        self.assertEqual(msgs, [(0x90, 60, 100), (0x90, 62, 0), (0xB0, 64, 127), (0xC0, 5)])
        self.assertEqual(p.feed(bytes([0x90, 60])) + p.feed(bytes([90])), [(0x90, 60, 90)])


if __name__ == "__main__":
    unittest.main()
