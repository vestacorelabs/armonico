"""The rest video's sound: pulsesink when pipewire-pulse is there, pipewiresink when it is not."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)


@unittest.skipIf(T.pygame is None, "pygame is not installed")
class VideoSink(unittest.TestCase):
    def sink_for(self, with_pulse):
        mod = T.load_screen()
        m = mod.Media(T.pygame)
        made = []
        Gst = mock.Mock()
        Gst.ElementFactory.make.side_effect = lambda kind, name: made.append(kind) or mock.Mock(kind=kind)
        with tempfile.TemporaryDirectory() as run:
            if with_pulse:
                Path(run, "pulse").mkdir()
                Path(run, "pulse", "native").touch()
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": run}):
                return m._audio_sink(Gst).kind

    def test_pulsesink_when_pipewire_pulse_runs(self):
        self.assertEqual(self.sink_for(True), "pulsesink")

    def test_pipewiresink_without_it(self):
        self.assertEqual(self.sink_for(False), "pipewiresink")


if __name__ == "__main__":
    unittest.main()
