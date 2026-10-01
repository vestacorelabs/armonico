"""armonico connect: the SSH step of the device's setup, the card in Settings, what root writes back to the
screen (connect_mark), the login shown, the choice among several Home Assistant instances, the CLI command and
the installer's --connect, and the Telegram setup's result file (never the token)."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)
from test_device_ui_audit import texts               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
pygame = T.pygame


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(pygame is None, "pygame is not installed")
class SshStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.app = T.make_app(self.mod)
        self.app.draw()
        from ui import system
        self.system = system
        self.p = [mock.patch.object(system, "ssh_on", return_value=True),
                  mock.patch.object(system, "login_user", return_value="pi"),
                  mock.patch.object(system, "host_name", return_value="armonico")]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_an_external_broker_goes_to_the_ssh_step(self):
        app = self.app
        app.broker["type"] = "external"
        ss = app.setup_steps()
        self.assertEqual(ss[ss.index("conn") + 1:], ["ssh", "look", "done"])

    def test_the_step_shows_the_login_and_the_command(self):
        app = self.app
        app.ip = "192.168.1.40"
        app.broker["type"] = "external"
        app.open_setup("ssh")
        lines = " ".join(t for t, _, _ in texts(app))
        self.assertIn("ssh pi@192.168.1.40", lines)
        self.assertIn("armonico connect", lines)

    def test_ssh_off_offers_to_turn_it_on_after_asking(self):
        app = self.app
        app.broker["type"] = "external"
        with mock.patch.object(self.system, "ssh_on", return_value=False):
            app.open_setup("ssh")
            app.ssh = None
            _, opts = app.ssh_cards()
        self.assertEqual(opts[0][0], app.tr("ssh.turnOn"))
        with mock.patch.object(self.system, "request", return_value=(True, "")) as req:
            opts[0][3]()
            self.assertEqual(app.confirm["text"], app.tr("ssh.onQ"))
            req.assert_not_called()

    def test_entering_on_the_screen_brings_back_the_old_steps(self):
        app = self.app
        app.broker["type"] = "external"
        app.open_setup("ssh")
        _, opts = app.ssh_cards()
        opts[-1][3]()
        self.assertTrue(app.st.get("setup_manual"))
        self.assertEqual(app.setup_step, "broker")

    def test_the_settings_card_opens_the_step_alone_and_back_returns(self):
        app = self.app
        app.open_page("conn")
        cell = app.settings_cells()["conn"][0]
        self.assertEqual(cell[0], app.tr("c.ssh"))
        cell[3]()
        self.assertEqual((app.view, app.setup_steps()), ("setup", ["ssh"]))
        app.draw()
        app.press("menu")
        self.assertEqual((app.view, app.page), ("settings", "conn"))


class ConnectMark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "device").mkdir()
        with mock.patch.dict(os.environ, {"DATA_DIR": str(self.data)}):
            self.ra = load("ra_connect", ROOT / "device" / "root_actions.py")

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self):
        return json.loads((self.data / "device" / "settings.json").read_text())

    def test_marks_go_into_the_settings_and_the_setup_goes_on(self):
        (self.data / "device" / "settings.json").write_text(json.dumps({"lang": "he", "setup_step": "ssh"}))
        self.ra.connect_mark({"ha_done": True, "ha_kept": True, "tg_done": True, "tg_bot": "my_bot",
                              "tg_chats": [123, "x", True], "setup_done": True})
        st = self.settings()
        self.assertEqual(st["lang"], "he", "the rest of the settings stays")
        self.assertEqual(st["setup_step"], "look")
        self.assertEqual(st["tg_chats"], [123], "numbers only")
        self.assertNotIn("setup_done", st, "only the connection's own keys are taken")
        self.assertFalse(st["setup_manual"])

    def test_a_finished_setup_is_not_opened_again(self):
        (self.data / "device" / "settings.json").write_text(json.dumps({"setup_done": True}))
        self.ra.connect_mark({"ha_done": True})
        self.assertNotIn("setup_step", self.settings())

    def test_a_link_in_place_of_the_settings_is_not_followed(self):
        target = self.data / "elsewhere.json"
        target.write_text("{}")
        (self.data / "device" / "settings.json").symlink_to(target)
        self.ra.connect_mark({"ha_done": True})
        self.assertEqual(target.read_text(), "{}", "the file the link points to is untouched")
        self.assertFalse((self.data / "device" / "settings.json").is_symlink())


class Login(unittest.TestCase):
    def test_the_installing_user_is_the_login(self):
        from ui import system
        with tempfile.TemporaryDirectory() as d:
            g, p = Path(d, "group"), Path(d, "passwd")
            g.write_text("armonico:x:990:armonico,svc,pi\n")
            p.write_text("svc:x:5:5::/:/usr/sbin/nologin\npi:x:1000:1000::/home/pi:/bin/bash\n")
            self.assertEqual(system.login_user(passwd=str(p), groups=str(g)), "pi")
            g.write_text("other:x:1:\n")
            self.assertEqual(system.login_user(passwd=str(p), groups=str(g)), "")


class Installer(unittest.TestCase):
    def setUp(self):
        self.sh = (ROOT / "install.sh").read_text()

    def test_connect_is_an_action_of_its_own(self):
        self.assertIn('--connect)      ACTION="connect"', self.sh)
        self.assertIn("device_connect()", self.sh)
        self.assertIn("connect_mark(marks)", self.sh)

    def test_several_home_assistants_are_offered_to_choose(self):
        body = self.sh[self.sh.index("choose_home_assistant() {"):]
        body = body[:body.index("\n}\n") + 3]
        script = ("ok(){ :; }; warn(){ :; }\n" + body +
                  'HA_HOST=1.1.1.1 HA_WHERE=network HA_VERSION=1 HA_NAME=A HA_URL_FOUND=http://1.1.1.1:8123 HA_BROKER=yes\n'
                  'v=$\'2.2.2.2\\tnetwork\\t\\t\\thttp://2.2.2.2:8123\\tno\'\n'
                  'choose_home_assistant "${v//$\'\\t\'/$\'\\x1f\'}"\n'
                  'echo "$HA_HOST|$HA_VERSION|$HA_NAME|$HA_URL_FOUND|$HA_BROKER"\n')
        # no terminal: the first one, with no question
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout.strip(), "1.1.1.1|1|A|http://1.1.1.1:8123|yes")
        # a terminal's answer: the second one, its empty fields kept empty
        chosen = script.replace("[ ! -t 0 ]", "false")
        r = subprocess.run(["bash", "-c", chosen], capture_output=True, text=True, input="2\n")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "2.2.2.2|||http://2.2.2.2:8123|no")

    def test_the_cli_runs_it_only_on_the_device(self):
        cli = (ROOT / "cli" / "armonico.py").read_text()
        self.assertIn('"connect": cmd_connect', cli)
        self.assertIn('installer("connect", ["--connect"])', cli)


class FindHa(unittest.TestCase):
    def test_other_instances_are_written_on_lines_of_their_own(self):
        fh = load("find_ha_t", ROOT / "setup" / "find_ha.py")
        out = []
        with mock.patch.object(fh.sys.stdout, "write", out.append), self.assertRaises(SystemExit):
            fh.answer(True, host="1.1.1.1", where="network", others=[
                {"host": "2.2.2.2", "where": "network", "version": "", "name": "T\tx", "url": "http://2.2.2.2:8123",
                 "broker": False}])
        self.assertIn("other\t2.2.2.2\tnetwork\t\tTx\thttp://2.2.2.2:8123\tno\n", out)


class TelegramResult(unittest.TestCase):
    def test_the_result_has_no_token(self):
        tg = load("tg_result", ROOT / "setup" / "telegram_setup.py")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "r.json")
            with mock.patch.dict(os.environ, {"RESULT_FILE": str(f)}):
                tg.write_result(bot="b", chats=[1], connected=True)
            self.assertEqual(json.loads(f.read_text()), {"bot": "b", "chats": [1], "connected": True})
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
