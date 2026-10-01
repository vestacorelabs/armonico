"""The recorder must never write the admin secret to its file."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
import mqtt_recorder as recorder


class RecorderTests(unittest.TestCase):
    def test_the_admin_secret_is_not_written(self):
        line = recorder.text(b"secret:0123abcd code on")
        self.assertNotIn("0123abcd", line)
        self.assertIn("secret:***", line)

    def test_other_payloads_are_unchanged(self):
        self.assertEqual(recorder.text(b"online"), "online")
        self.assertEqual(recorder.text(b""), "<empty>")


if __name__ == "__main__":
    unittest.main()
