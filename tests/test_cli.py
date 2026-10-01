"""The armonico command: the part that can be checked without a piano."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("armonico_cli", ROOT / "cli" / "armonico.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Elevate(unittest.TestCase):
    def test_sudo_is_given_the_interpreter_and_the_script(self):
        """The script file is not executable on its own: `sudo /opt/armonico/cli/armonico.py` failed with
        "command not found". The wrapper starts it with the program's Python, so sudo must too."""
        cli = load()
        with mock.patch.object(cli.os, "geteuid", return_value=1000), \
             mock.patch.object(cli.shutil, "which", return_value="/usr/bin/sudo"), \
             mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
             mock.patch.object(cli.sys, "argv", ["/opt/armonico/cli/armonico.py", "update", "1.0"]), \
             mock.patch.object(cli.os, "execvp") as ex:
            cli.elevate()
        cmd, argv = ex.call_args[0]
        self.assertEqual(cmd, "sudo")
        self.assertEqual(argv[:3], ["sudo", sys.executable, "/opt/armonico/cli/armonico.py"])
        self.assertEqual(argv[3:], ["update", "1.0"])

    def test_nothing_happens_for_root_or_without_a_terminal(self):
        cli = load()
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(cli.os, "execvp") as ex:
            cli.elevate()
        ex.assert_not_called()
        with mock.patch.object(cli.os, "geteuid", return_value=1000), \
             mock.patch.object(cli.shutil, "which", return_value="/usr/bin/sudo"), \
             mock.patch.object(cli.sys.stdin, "isatty", return_value=False), \
             mock.patch.object(cli.os, "execvp") as ex:
            cli.elevate()
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
