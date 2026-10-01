"""From the 8-hour load test (soak-2026-11-11): the screen's memory after each rest video, ALSA's underrun lines
in the journal, and the test's own sleep that went negative."""
import ctypes
import os
import re
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DEVICE = ROOT / "device"


def alsa_part():
    """alsa_messages_counted and what it needs, from device/armonico_pi.py without importing the whole program."""
    src = (DEVICE / "armonico_pi.py").read_text()
    i, j = src.index("_ALSA_HANDLER = None"), src.index("\nclass SdlOut:")
    said = []
    ns = {"time": time, "os": os, "log": said.append}
    exec(src[i:j], ns)
    return ns, said


class Memory(unittest.TestCase):
    def test_the_session_caps_glibc_pools(self):
        s = (DEVICE / "session.sh").read_text()
        self.assertIn('export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"', s)
        self.assertLess(s.index("MALLOC_ARENA_MAX"), s.index('exec >>"$LOG"'))

    def test_closing_a_video_gives_memory_back(self):
        s = (DEVICE / "screen.py").read_text()
        close = s[s.index("    def _close_gst(self):"):s.index("    FADE_STEPS")]
        self.assertIn("trim_heap()", close)
        self.assertIn("def trim_heap():", s)


class AlsaLines(unittest.TestCase):
    def setUp(self):
        try:
            self.asound = ctypes.CDLL("libasound.so.2")
        except OSError:
            self.skipTest("no libasound here")

    def test_underruns_become_one_line_a_minute(self):
        ns, said = alsa_part()
        ns["alsa_messages_counted"]()
        self.assertIsNotNone(ns["_ALSA_HANDLER"])
        h = ns["_ALSA_HANDLER"]
        if hasattr(self.asound, "snd_lib_log_set_handler"):
            def call(fmt):
                h(3, 0, b"pcm.c", 1, b"snd_pcm_recover", 0, fmt, None)
        else:
            def call(fmt):
                h(b"pcm.c", 1, b"snd_pcm_recover", 0, fmt)
        for _ in range(250):
            call(b"underrun occurred")
        for _ in range(250):
            call(b"%s occurred")            # alsa-lib 1.2.14: "underrun" is the argument
        self.assertEqual(said, [])
        ns["_ALSA_SEEN"]["minute"] -= 61
        call(b"underrun occurred")
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"^ALSA: 500 underruns in the last \d+ min")

    def test_each_other_kind_is_written_once_then_counted(self):
        ns, said = alsa_part()
        ns["alsa_messages_counted"]()
        pcm = ctypes.c_void_p()
        for _ in range(15):
            self.asound.snd_pcm_open(ctypes.byref(pcm), b"armonico_no_such_pcm", 0, 0)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("ALSA: ") and "Unknown PCM" in said[0] and "said once" in said[0])
        ns["_ALSA_SEEN"]["minute"] -= 61
        self.asound.snd_pcm_open(ctypes.byref(pcm), b"armonico_no_such_pcm", 0, 0)
        self.assertEqual(len(said), 2)
        self.assertRegex(said[1], r"^ALSA: 14 other messages in the last \d+ min, each kind written once before "
                         r"\(snd_pcm_open_noupdate ×14\)")

    def test_the_soak_counts_the_minute_line_and_does_not_call_it_a_warning(self):
        s = (DEVICE / "soak.py").read_text()
        self.assertIn('re.match(r"ALSA: (\\d+) underruns in the last", msg)', s)
        self.assertRegex("ALSA: 1380 underruns in the last 1 min (x)", r"ALSA: (\d+) underruns in the last")


class QuietUnits(unittest.TestCase):
    def test_the_request_unit_keeps_errors_and_drops_routine_lines(self):
        s = (ROOT / "system" / "device-request.service").read_text()
        self.assertIn("LogLevelMax=notice\n", s)
        self.assertIn("SyslogLevel=warning\n", s)

    def test_rtkit_filter_is_put_in_and_taken_out(self):
        s = (ROOT / "install.sh").read_text()
        conf = '/etc/systemd/system/rtkit-daemon.service.d/$APP-device.conf'
        self.assertEqual(s.count(conf), 3)          # checked, written, removed
        self.assertIn("LogFilterPatterns=~^Supervising [0-9]+ threads of", s)
        self.assertTrue(re.fullmatch(r"^Supervising [0-9]+ threads of.*",
                                     "Supervising 5 threads of 3 processes of 1 users."))


class LeakVerdict(unittest.TestCase):
    def test_a_cache_that_stops_is_not_a_leak(self):
        s = (DEVICE / "soak.py").read_text()
        self.assertIn("s2 = slope(rt[h:], rg[g][h:])", s)


class SoakWait(unittest.TestCase):
    def test_wait_never_sleeps_a_negative_time(self):
        src = (DEVICE / "soak.py").read_text()
        body = src[src.index("    def wait(self, sec):"):src.index("    def event(self, text):")]
        ns = {"time": time}
        exec("class S:\n" + body, ns)
        s = ns["S"]()
        s.quit = mock.Mock(is_set=lambda: False)
        clock = iter([0.0] + [0.05 * i for i in range(1, 400)])
        s.deadline = 1.0
        s.left = lambda: 0
        slept = []
        with mock.patch("time.monotonic", lambda: next(clock)), mock.patch("time.sleep", slept.append):
            s.wait(0.3)
        self.assertTrue(slept and min(slept) > 0)

    def test_the_journal_is_read_with_the_fields_it_needs(self):
        s = (DEVICE / "soak.py").read_text()
        self.assertIn("--output-fields=_SYSTEMD_UNIT,SYSLOG_IDENTIFIER,MESSAGE,PRIORITY,_TRANSPORT", s)
        self.assertTrue(re.search(r"if slow:\n\s+last_slow = now\n\s+trim_heap\(\)", s))


if __name__ == "__main__":
    unittest.main()
