"""Two locks that are easy to lose in a later edit.

The Telegram automation in the Home Assistant package has to carry the admin secret on every
guarded command, or the lesson engine refuses it. The root scripts must not write into the
data folder (which belongs to the service user) by a plain redirect, which follows a link.
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "setup"))
sys.path.insert(0, str(ROOT / "lessons"))

import ha_setup                     # noqa: E402
import piano_game as game           # noqa: E402

# the installer's venv has PyYAML, the CI job may not
HAVE_YAML = importlib.util.find_spec("yaml") is not None


class TelegramCarriesTheSecret(unittest.TestCase):

    @unittest.skipUnless(HAVE_YAML, "PyYAML is not installed")
    def test_the_secret_is_put_in(self):
        pkg = ha_setup.load_package(admin_secret="test-secret-123")
        auto = next(a for a in pkg["automation"] if a["id"] == "piano_telegram_commands")
        self.assertEqual(auto["variables"]["admin"], "test-secret-123")

    def test_every_guarded_command_carries_it(self):
        payloads = re.findall(r"payload: \"([^\"]+)\"", (ROOT / "home-assistant" / "piano.yaml").read_text())
        seen = set()
        for p in payloads:
            action = p.split("|", 1)[0]
            if action in game.GUARDED_ACTIONS:
                seen.add(action)
                self.assertIn("|secret:{{ admin }}", p, p)
        self.assertEqual(seen, {"code", "profile"})


class RootWritesNoRedirectIntoData(unittest.TestCase):

    def test_update_and_reset(self):
        for script in ("setup/update.sh", "bridge/piano_reset.sh"):
            text = (ROOT / script).read_text()
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                self.assertNotRegex(line, r'>>?\s*"\$(LOG|STATUS|DATA)', f"{script}: {line.strip()}")
            self.assertIn("install -T", text, script)


if __name__ == "__main__":
    unittest.main()
