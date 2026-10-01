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


class FakeClient:
    """Stands in for paho's client: connect answers at once with the reason code it is given."""
    reason = None

    def __init__(self, *a, **k):
        self.on_connect = None

    def username_pw_set(self, *a):
        pass

    def connect(self, *a, **k):
        pass

    def loop_start(self):
        self.on_connect(self, None, None, FakeClient.reason, None)

    def loop_stop(self):
        pass


class RefusedLogin(unittest.TestCase):
    """A refused login answers too, and was taken for a connection: the commands then broke on
    "the client is not currently connected" and the status showed the broker as fine."""

    def setUp(self):
        import paho.mqtt.client as mqtt
        from paho.mqtt.packettypes import PacketTypes
        from paho.mqtt.reasoncodes import ReasonCode
        self.cli = load()
        FakeClient.reason = ReasonCode(PacketTypes.CONNACK, "Not authorized")
        p = mock.patch.object(mqtt, "Client", FakeClient)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"MQTT_HOST": "broker", "MQTT_PORT": "1883", "MQTT_USER": "piano", "MQTT_PASS": "x"}

    def test_connect_stops_with_the_reason(self):
        with mock.patch.object(self.cli, "settings", return_value=self.cfg), \
             mock.patch("sys.stderr") as err, self.assertRaises(SystemExit) as e:
            self.cli.connect()
        self.assertEqual(e.exception.code, 1)
        self.assertIn("refused", "".join(c.args[0] for c in err.write.call_args_list))
        self.assertEqual(self.cli.BROKER_REFUSED, "Not authorized")

    def test_the_status_shows_the_refusal(self):
        self.cli.BROKER_REFUSED = "Not authorized"
        with mock.patch.object(self.cli, "run_out", return_value=""):
            rows = self.cli.checks(self.cfg, False)
        self.assertIn("refused the login: Not authorized", rows[0][2])


class SetExternal(unittest.TestCase):
    """set-external saves what it is given: it now checks it as setup-external does."""

    def run_it(self, *args):
        cli = load()
        with mock.patch.object(cli, "root"), mock.patch.object(cli, "save_profile") as save, \
             mock.patch("sys.stderr"):
            try:
                cli.cmd_device_mode(["set-external", *args])
            except SystemExit:
                pass
        return save

    def test_bad_values_save_nothing(self):
        for args in (("host", "99999"), ("host", "port"), ("ho'st",), ("host", "1883", "us\ner"),
                     ("host", "1883", "user", "pa'ss")):
            self.run_it(*args).assert_not_called()

    def test_good_values_are_saved(self):
        save = self.run_it("192.168.1.5", "", "piano", "secret")
        prof = save.call_args[0][0]
        self.assertEqual((prof["MQTT_HOST"], prof["MQTT_PORT"], prof["MQTT_PASS"]), ("192.168.1.5", "1883", "secret"))


class Restart(unittest.TestCase):
    def test_the_device_units_are_restarted_when_enabled(self):
        cli = load()
        done = mock.Mock(returncode=0, stdout="active")
        with mock.patch.object(cli, "root"), mock.patch.object(cli.subprocess, "run", return_value=done) as run, \
             mock.patch("builtins.print"):
            cli.cmd_restart([])
        restart = next(c.args[0] for c in run.call_args_list if c.args[0][:2] == ["systemctl", "restart"])
        for unit in cli.DEVICE_UNITS:
            self.assertIn(unit, restart)


if __name__ == "__main__":
    unittest.main()
