"""install.sh --device finding what it runs on, the install going on with no keyboard, and the terminal's
text fitting a phone's narrow terminal."""
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"


def function(name):
    src = INSTALL.read_text()
    m = re.search(rf"^{re.escape(name)}\(\) \{{", src, re.M)
    assert m, name
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def load_cli():
    spec = importlib.util.spec_from_file_location("armonico_cli_narrow", ROOT / "cli" / "armonico.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DeviceKind(unittest.TestCase):
    def kind(self, env=None):
        e = dict(os.environ)
        e.pop("ARMONICO_DEVICE_KIND", None)
        e.update(env or {})
        r = subprocess.run(["bash", "-c", function("detect_device_kind") + "\ndetect_device_kind"],
                           env=e, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_the_override_wins(self):
        for k in ("pi", "android", "linux"):
            self.assertEqual(self.kind({"ARMONICO_DEVICE_KIND": k}), k)

    def test_an_unknown_override_is_ignored(self):
        self.assertIn(self.kind({"ARMONICO_DEVICE_KIND": "toaster"}), ("pi", "android", "linux"))

    def test_this_machine_is_one_of_the_three(self):
        self.assertIn(self.kind(), ("pi", "android", "linux"))

    def test_only_the_pi_gets_the_boot_settings_and_the_screen_login(self):
        src = INSTALL.read_text()
        # each Pi-only part sits behind the kind
        for part in ('dtoverlay=gpio-shutdown,gpio_pin=3\\n', 'HandlePowerKey=ignore', 'ConditionUser=%s', 'ensure-fsck',
                     'disable --now ModemManager.service'):
            i = src.index(part)
            self.assertIn('if [ "$DEVICE_KIND" = pi ]; then', src[max(0, i - 3000):i], part)
        # the screen's own login at boot: the Pi, and a Linux machine with no desktop
        for part in ('systemctl disable --now greetd.service', 'systemctl enable "$APP-screen"'):
            i = src.index(part)
            self.assertIn('"$DEVICE_SCREEN" = kms ]', src[max(0, i - 300):i], part)

    def test_the_screen_follows_the_kind_and_the_desktop(self):
        src = INSTALL.read_text()
        block = src[src.index('DEVICE_SCREEN=""'):]
        block = block[:block.index("\nfi\n") + 4]
        for kind, desktop, want in (("pi", 1, "kms"), ("android", 1, "window"), ("android", 0, "window"),
                                    ("linux", 1, "window"), ("linux", 0, "kms")):
            script = (f'DEVICE=yes; DEVICE_KIND={kind}\nhas_desktop() {{ return {0 if desktop else 1}; }}\n'
                      + block + '\necho "$DEVICE_SCREEN"')
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.stdout.strip(), want, (kind, desktop, r.stderr))

    def test_the_window_launcher_is_installed_and_runs(self):
        src = INSTALL.read_text()
        self.assertIn("    device/window.sh\n", src)
        self.assertIn('chmod 755 "$OPT_DIR/device/window.sh"', src)
        r = subprocess.run(["sh", "-n", str(ROOT / "device" / "window.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        r = subprocess.run(["sh", str(ROOT / "device" / "window.sh")], env=env, capture_output=True, text=True,
                           timeout=10)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Termux:X11", r.stderr)


class ScreenCommand(unittest.TestCase):
    def test_refuses_on_the_pi_and_before_a_device_install(self):
        cli = load_cli()
        for kind, words in (("", "--device"), ("pi", "starts by itself"), ("linux", "no desktop")):
            with mock.patch.object(cli, "device_kind", return_value=kind), \
                    mock.patch.object(cli, "device_screen", return_value="kms"), \
                    contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                cli.cmd_screen([])
            self.assertIn(words, err.getvalue().replace("\n  ", " "))

    def test_opens_the_window_elsewhere(self):
        cli = load_cli()
        with mock.patch.object(cli, "device_kind", return_value="android"), \
                mock.patch.object(cli, "device_screen", return_value="window"), \
                mock.patch.object(cli, "run") as run, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUDO_USER", None)
            if os.geteuid() == 0:
                os.environ.pop("SUDO_USER", None)
            cli.cmd_screen([])
        self.assertTrue(run.call_args[0][0][0].endswith("device/window.sh"))


class NoKeyboard(unittest.TestCase):
    def test_the_terminal_setup_can_go_on_without_one(self):
        src = INSTALL.read_text()
        self.assertNotIn('die "The ALSA sequencer is missing', src)
        self.assertIn("Or type s to continue without a keyboard", src)
        self.assertIn('C_MIDI_DEVICE=""; C_KEY_LOWEST="21"; C_KEY_HIGHEST="108"', src)

    def test_the_bridge_waits_instead_of_failing(self):
        src = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        self.assertNotIn("MIDI_DEVICE:?", src)
        self.assertIn("exec sleep infinity", src)

    def test_the_setup_screen_offers_it(self):
        src = (ROOT / "setup" / "setup_web.py").read_text()
        self.assertIn('id="b-nokbd"', src)
        self.assertIn("NO_KEYBOARD", src)
        self.assertIn("'Continue without a keyboard':", src)


class Narrow(unittest.TestCase):
    def test_the_help_fits_a_phone(self):
        cli = load_cli()
        with mock.patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((56, 40))):
            text = cli.fit(cli.HELP.format(app="armonico"))
        long = [ln for ln in text.splitlines() if len(ln) > 56 and " " in ln.strip()]
        self.assertEqual(long, [])

    def test_a_pair_goes_under_its_label_when_it_does_not_fit(self):
        cli = load_cli()
        out = io.StringIO()
        with mock.patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((40, 20))), \
                mock.patch.object(cli, "c", side_effect=lambda code, t: t), contextlib.redirect_stdout(out):
            cli.pair("Keyboard", "connected, listening on the sequencer and answering", 12)
            cli.pair("Alarm", "off", 12)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "  Keyboard")
        self.assertTrue(all(len(x) <= 40 for x in lines))
        self.assertEqual(lines[-1], "  Alarm         off")

    def test_the_installer_help_fits_a_phone(self):
        r = subprocess.run(["bash", str(INSTALL), "--help"], capture_output=True, text=True, timeout=10)
        self.assertIn("--device", r.stdout)
        self.assertTrue(all(len(x) <= 72 for x in r.stdout.splitlines()))


if __name__ == "__main__":
    unittest.main()
