"""Wi-Fi on the Armonico Pi device: the root actions (nmcli's terse output, the checks on every value,
the password kept off the command line, the status file never written through a link) and the screens
(the page in both languages and sizes, the symbols layer, Forget asking first, the setup's step only
without a cable)."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_device_ui as UI                         # noqa: E402  (make_app, the SDL dummy driver)

pygame = UI.pygame


def root_actions():
    spec = importlib.util.spec_from_file_location("armonico_root_wifi_test", ROOT / "device" / "root_actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Done:
    """A finished subprocess, as nmcli would leave it."""
    def __init__(self, out="", err="", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


NETS = [{"ssid": "Home", "signal": 90, "secure": True, "kind": "psk", "active": True, "known": True},
        {"ssid": "Cafe: 2nd floor", "signal": 70, "secure": True, "kind": "psk", "active": False, "known": False},
        {"ssid": "Free", "signal": 40, "secure": False, "kind": "open", "active": False, "known": False},
        {"ssid": "Office", "signal": 30, "secure": True, "kind": "eap", "active": False, "known": False}]
SAVED = [{"uuid": "11111111-1111-1111-1111-111111111111", "name": "Home", "ssid": "Home", "at": 1761400000, "active": True},
         {"uuid": "22222222-2222-2222-2222-222222222222", "name": "Savta", "ssid": "Savta", "at": 1760200000, "active": False}]


class Parser(unittest.TestCase):
    def setUp(self):
        self.ra = root_actions()

    def test_terse_fields_with_escaped_colons_and_backslashes(self):
        self.assertEqual(self.ra.terse_split(r"*:Cafe\: 2nd floor:70:WPA2"), ["*", "Cafe: 2nd floor", "70", "WPA2"])
        self.assertEqual(self.ra.terse_split(r" :back\\slash:5:"), [" ", "back\\slash", "5", ""])
        self.assertEqual(self.ra.terse_value("Cafe\\: 2nd\n"), "Cafe: 2nd")

    def test_duplicates_collapse_to_the_strongest_and_hidden_ones_are_left_out(self):
        text = "\n".join([
            " :Home:45:WPA2",
            "*:Home:62:WPA2",                      # the 2.4 GHz one, in use
            " :Home:88:WPA2",                      # the 5 GHz one, stronger
            r" :Cafe\: 2nd floor:70:WPA1 WPA2",
            " ::80:WPA2",                          # hidden: no name
            " :--:50:WPA2",
            " :Free:40:",
            " :Free5:30:--",
            " :Office:20:WPA2 802.1X",
            " :Only3:20:WPA3",
            " :Old:10:WEP",
            "garbage line",
        ])
        nets = self.ra.parse_wifi_list(text)
        names = [n["ssid"] for n in nets]
        self.assertEqual(names.count("Home"), 1)
        home = nets[0]
        self.assertEqual((home["ssid"], home["signal"], home["active"]), ("Home", 88, True),
                         "the strongest access point, and connected because one of them is")
        self.assertIn("Cafe: 2nd floor", names)
        self.assertNotIn("", names)
        self.assertNotIn("--", names)
        kinds = {n["ssid"]: n["kind"] for n in nets}
        self.assertEqual((kinds["Free"], kinds["Free5"], kinds["Office"], kinds["Only3"], kinds["Old"]),
                         ("open", "open", "eap", "sae", "wep"))
        self.assertFalse(next(n for n in nets if n["ssid"] == "Free")["secure"])
        self.assertEqual([n["signal"] for n in nets[1:]], sorted([n["signal"] for n in nets[1:]], reverse=True))


class Checks(unittest.TestCase):
    def setUp(self):
        self.ra = root_actions()

    def test_bad_names_and_passwords_are_refused(self):
        ra = self.ra
        for bad in ("", "a" * 33, "א" * 17, "two\nlines", "tab\there", "nul\0", "del\x7f", 5, None, ["x"]):
            with self.subTest(ssid=bad), self.assertRaises(ValueError):
                ra.check_ssid(bad)
        self.assertEqual(ra.check_ssid("a" * 32), "a" * 32)
        self.assertEqual(ra.check_ssid("בית"), "בית")
        for bad in ("short7!", "x" * 64, "g" * 64, "pässwörd", "new\nline1", "tab\tpass", 12345678, ["p"]):
            with self.subTest(pw=bad), self.assertRaises(ValueError):
                ra.check_password(bad)
        for good in ("12345678", "x" * 63, "ab:/?&=+*#$%!(),;'\"~^<>[]{}|\\`", "0123456789abcdefABCDEF" * 2 + "0" * 20):
            self.assertEqual(ra.check_password(good), good)
        self.assertEqual(ra.check_password(""), "")
        with self.assertRaises(ValueError):
            ra.check_flag("yes")

    def test_nothing_reaches_nmcli_with_a_bad_value(self):
        ra = self.ra
        with mock.patch.object(ra, "nmcli") as nm, mock.patch.object(ra, "nm_running", return_value=True):
            for args in ({"ssid": "x\ny", "password": "12345678"}, {"ssid": "ok", "password": "short"},
                         {"ssid": "ok", "password": "12345678", "hidden": "yes"}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    ra.do_wifi_connect(lambda *a: None, args)
            with self.assertRaises(ValueError):
                ra.do_wifi_forget(lambda *a: None, {"uuid": "../../etc; reboot"})
            nm.assert_not_called()
            with self.assertRaises(ValueError):
                ra.do_wifi_scan(lambda *a: None, {"rescan": "yes"})

    def test_only_a_saved_wifi_connection_can_be_forgotten(self):
        ra = self.ra
        with mock.patch.object(ra, "nm_running", return_value=True), \
                mock.patch.object(ra, "wifi_saved", return_value=SAVED), mock.patch.object(ra, "nmcli") as nm:
            with self.assertRaises(ValueError):
                ra.do_wifi_forget(lambda *a: None, {"uuid": "33333333-3333-3333-3333-333333333333"})
            nm.assert_not_called()
            nm.return_value = Done("Connection deleted")
            ra.do_wifi_forget(lambda *a: None, {"uuid": SAVED[1]["uuid"]})
            nm.assert_called_once_with("connection", "delete", "uuid", SAVED[1]["uuid"], timeout=20)

    def test_wifi_actions_are_in_the_list(self):
        for a in ("wifi-status", "wifi-scan", "wifi-saved", "wifi-connect", "wifi-forget"):
            self.assertIn(a, self.ra.ACTIONS)


class Connect(unittest.TestCase):
    """The connection with nmcli itself replaced: what it is asked, and what it is never given."""

    def setUp(self):
        self.ra = root_actions()
        self.calls, self.files = [], []

    def fake(self, up_result, radio="enabled", name="Home"):
        uuid = "33333333-3333-3333-3333-333333333333"

        def nmcli(*args, timeout=30):
            self.calls.append(args)
            if args == ("radio", "wifi"):
                return Done(radio + "\n")
            if args[:2] == ("-t", "-f") and "wifi" in args:
                return Done(f" :{name}:80:WPA2\n")
            if args[:2] == ("connection", "add"):
                return Done(f"Connection '{name}' ({uuid}) successfully added.\n")
            if "up" in args:
                if "passwd-file" in args:
                    path = args[args.index("passwd-file") + 1]
                    st = os.stat(path)
                    self.files.append((path, Path(path).read_text(), oct(st.st_mode & 0o777)))
                return up_result
            if args[:2] == ("-g", "IP4.ADDRESS"):
                return Done("192.168.1.40/24\n")
            return Done("")
        return nmcli

    def run_connect(self, up_result, password="correct horse", radio="enabled", ssid="Home"):
        ra = self.ra
        out = []
        with mock.patch.object(ra, "nmcli", side_effect=self.fake(up_result, radio, ssid)), \
                mock.patch.object(ra.time, "sleep"), \
                mock.patch.object(ra, "nm_running", return_value=True), \
                mock.patch.object(ra, "wifi_device", return_value="wlan0"), \
                mock.patch.object(ra, "wifi_saved", return_value=[]):
            ra.do_wifi_connect(lambda step, state="running", msg="": out.append((state, msg)),
                               {"ssid": ssid, "password": password, "hidden": False})
        return out

    def test_the_password_goes_in_a_private_file_never_on_a_command_line(self):
        out = self.run_connect(Done("Connection successfully activated"))
        for args in self.calls:
            self.assertFalse(any("correct horse" in a for a in args), args)
        add = next(a for a in self.calls if a[:2] == ("connection", "add"))
        self.assertIn("wifi-sec.key-mgmt", add)
        self.assertEqual(add[add.index("wifi-sec.psk-flags") + 1], "0", "kept by NetworkManager itself")
        self.assertEqual(len(self.files), 1)
        path, text, mode = self.files[0]
        self.assertEqual(text, "802-11-wireless-security.psk:correct horse\n")
        self.assertEqual(mode, "0o600")
        self.assertFalse(Path(path).exists(), "removed after the try")
        state, msg = out[-1]
        self.assertEqual(state, "done")
        self.assertEqual(json.loads(msg), {"ssid": "Home", "ip": "192.168.1.40"})

    def test_a_blocked_radio_is_turned_on_or_said(self):
        # a Pi flashed with no Wi-Fi country starts with Wi-Fi blocked: it is unblocked first, and
        # when it stays off the screen says so instead of an empty list
        with self.assertRaises(self.ra.WifiError) as e:
            self.run_connect(Done("ok"), radio="disabled")
        self.assertEqual(str(e.exception), "blocked")
        self.assertIn(("radio", "wifi", "on"), self.calls)
        self.assertFalse(any(a[:2] == ("connection", "add") for a in self.calls))

    def test_a_wrong_password_says_so_and_keeps_nothing(self):
        with self.assertRaises(self.ra.WifiError) as e:
            self.run_connect(Done("", "Error: Connection activation failed: Secrets were required, but not provided.", 4))
        self.assertEqual(str(e.exception), "wrong-password")
        self.assertIn(("connection", "delete", "uuid", "33333333-3333-3333-3333-333333333333"), self.calls)
        self.assertFalse(Path(self.files[0][0]).exists())


class StatusFile(unittest.TestCase):
    def test_root_never_writes_through_a_link_in_the_data_folder(self):
        ra = root_actions()
        tmp = Path(tempfile.mkdtemp())
        target = tmp / "elsewhere"
        target.write_text("keep")
        status = tmp / "device-request.status"
        os.symlink(target, tmp / f".device-request.status.{os.getpid()}.tmp")
        os.symlink(target, status)
        with mock.patch.object(ra, "STATUS", status):
            ra.Progress("abc")(1, "done", "{}")
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse(status.is_symlink())
        self.assertEqual(json.loads(status.read_text())["state"], "done")
        self.assertEqual(oct(status.stat().st_mode & 0o777), "0o644")


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = UI.load_screen()

    def app(self, size=(800, 480), lang="en"):
        app = UI.make_app(self.mod, size, lang)
        app.wf.update(nm=True, dev="wlan0", ssid="Home", ip="192.168.1.40", nets=[dict(n) for n in NETS],
                      saved=[dict(c) for c in SAVED], scan_at=time.monotonic(), scanned=True)
        return app

    def test_every_wifi_screen_draws_in_both_languages_and_sizes(self):
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                app = self.app(size, lang)
                with mock.patch.object(app, "wifi_follow"):
                    app.open_wifi("settings")
                    app.wf.update(nets=[dict(n) for n in NETS], saved=[dict(c) for c in SAVED], nm=True, dev="wlan0")
                    shots = [("list", lambda: None),
                             ("pw", lambda: app.wifi_password("Cafe: 2nd floor")),
                             ("pw-symbols", lambda: (setattr(app, "sym", True), app.edit.update(shown=True, note="x"))),
                             ("connecting", lambda: (app.go("wifi"), app.wf.update(screen="connecting", target="Cafe", t0=time.monotonic()))),
                             ("done", lambda: app.wf.update(screen="done", result={"ssid": "Cafe", "ip": "192.168.1.40"})),
                             ("fail", lambda: app.wf.update(screen="fail", fail="timeout", last=("Cafe", "", False))),
                             ("saved", lambda: app.wf.update(screen="saved")),
                             ("no-nm", lambda: app.wf.update(screen="list", nm=False)),
                             ("setup", lambda: (app.wf.update(nm=True), setattr(app, "setup_wifi", True), app.open_setup("wifi")))]
                    for name, open_it in shots:
                        open_it()
                        with self.subTest(size=size, lang=lang, screen=name):
                            app.short_missed = set()
                            app.draw()
                            self.assertTrue(app.bar_items)
                            self.assertEqual(app.short_missed, set(), "every line fits its place")

    def test_the_network_page_has_a_wifi_cell_that_opens_the_page_and_comes_back_to_it(self):
        app = self.app()
        with mock.patch.object(app, "wifi_status_soon"):
            app.open_page("net")
        cells = app.settings_cells()["net"]
        from ui.views_wifi import NET_CELL
        self.assertEqual(cells[NET_CELL][0], app.tr("c.wifiSw"))         # the switch and the networks: one card
        self.assertEqual(cells[NET_CELL][1], "Home")
        with mock.patch.object(app, "wifi_follow"):
            cells[NET_CELL][3]()
            self.assertEqual(app.view, "wifi")
            app.draw()
            app.press("menu")
        self.assertEqual((app.view, app.page, app.sel), ("settings", "net", NET_CELL))
        app.wf["nm"] = False
        self.assertEqual(app.wifi_cell_text(), app.tr("wf.naShort"))

    def test_the_symbols_layer_types_colon_and_slash(self):
        app = self.app()
        got = []
        app.edit_field("Link", lambda: "https", got.append, keep=True)
        app.draw()
        app.pointer_down(220, 333)                       # the !#$ key
        app.draw()
        self.assertTrue(app.sym)
        x0 = (800 - 11 * 65) / 2
        for ci in (1, 8, 8):                             # ':' '/' '/' on the symbols' last row
            app.last_tap = (0.0, None, "")
            app.pointer_down(x0 + ci * 65 + 30, 142 + 3 * 43 + 19)
            app.draw()
        self.assertEqual(app.edit["val"], "https://")
        app.press("ok")
        self.assertEqual(got, ["https://"])

    def test_show_reveals_the_password_and_spaces_are_kept(self):
        app = self.app()
        got = []
        app.edit_field("Password", lambda: " pass word ", got.append, secret=True, keep=True, reveal=True)
        app.draw()
        app.pointer_down(800 - 80, 83)                 # Show
        self.assertTrue(app.edit["shown"])
        app.press("ok")
        self.assertEqual(got, [" pass word "])

    def test_forget_asks_first(self):
        app = self.app()
        with mock.patch.object(app, "wifi_follow"), mock.patch("ui.system.wifi", return_value=(True, {})) as wifi:
            app.open_wifi("settings")
            app.wf.update(screen="saved", ssel=0)
            app.draw()
            app.press("down")                            # the second, not connected
            app.draw()
            app.press("ok")
            self.assertIsNotNone(app.confirm, "a question first")
            wifi.assert_not_called()
            app.answer(True)
            for _ in range(50):
                if wifi.called:
                    break
                time.sleep(0.02)
        wifi.assert_called_once_with("forget", uuid=SAVED[1]["uuid"], timeout=40)

    def test_a_wrong_password_returns_to_the_keyboard_with_what_was_typed(self):
        app = self.app()
        with mock.patch.object(app, "wifi_follow"), \
                mock.patch("ui.system.wifi", return_value=(False, "wrong-password")) as wifi:
            app.open_wifi("settings")
            app.wifi_password("Cafe: 2nd floor")
            app.edit["val"] = "abc"                       # too short: stays, and says why
            app.draw()
            app.press("ok")
            self.assertEqual(app.view, "edit")
            self.assertEqual(app.edit["val"], "abc")
            self.assertEqual(app.edit["note"], app.tr("wf.pwLen"))
            wifi.assert_not_called()
            app.edit["val"] = "tr0ub4dor&3"
            app.draw()
            app.press("ok")
            self.assertEqual((app.view, app.wf["screen"]), ("wifi", "connecting"))
            for _ in range(100):
                if app.wf["due"]:
                    break
                time.sleep(0.02)
            app.draw()
        self.assertEqual(app.view, "edit")
        self.assertEqual(app.edit["val"], "tr0ub4dor&3")
        self.assertEqual(app.edit["note"], app.tr("wf.badPw"))
        self.assertEqual(wifi.call_args.kwargs["password"], "tr0ub4dor&3")

    def test_open_and_saved_networks_skip_the_password(self):
        app = self.app()
        with mock.patch.object(app, "wifi_connect") as conn, mock.patch.object(app, "wifi_password") as pw:
            app.wifi_pick(NETS[2])                       # open
            app.wifi_pick(dict(NETS[1], known=True))     # saved
            app.wifi_pick(NETS[3])                       # 802.1X: not offered
        self.assertEqual([c.args for c in conn.call_args_list], [("Free", "", False), ("Cafe: 2nd floor", "", False)])
        pw.assert_not_called()

    def test_scans_only_while_the_page_is_open(self):
        app = self.app()
        with mock.patch("ui.system.wifi", return_value=(True, {"nm": True, "dev": "wlan0", "nets": [], "saved": []})) as wifi:
            app.open_wifi("settings")
            app.draw()
            for _ in range(50):
                if not app.wf["busy"]:
                    break
                time.sleep(0.02)
            self.assertEqual(wifi.call_args_list[0].args, ("scan",))
            self.assertTrue(wifi.call_args_list[0].kwargs["rescan"], "a fresh scan on opening")
            n = wifi.call_count
            app.wf["scan_at"] = 0.0                      # due again, but the page is closed
            app.go("rest")
            for _ in range(5):
                app.draw()
            app.open_page("top")
            app.draw()
            self.assertEqual(wifi.call_count, n)


@unittest.skipIf(pygame is None, "pygame is not installed")
class SetupStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = UI.load_screen()

    def test_the_step_shows_only_without_a_cable(self):
        app = UI.make_app(self.mod)
        with mock.patch("ui.system.wired_up", return_value=True):
            app.open_setup("lang")
            self.assertNotIn("wifi", app.setup_steps())
            app.open_setup("wifi")
            self.assertEqual(app.setup_step, "place", "a saved Wi-Fi step goes on to the next one")
        app.go("settings")
        with mock.patch("ui.system.wired_up", return_value=False):
            app.open_setup("lang")
            steps = app.setup_steps()
        self.assertEqual(steps[:3], ["lang", "wifi", "place"])
        with mock.patch.object(app, "wifi_follow"):
            app.setup_step = "wifi"
            app.draw()
            self.assertEqual([b[1] for b in app.bar_items][0], app.tr("w.skipBtn"))
            app.press("menu")                            # Skip
        self.assertEqual(app.setup_step, "place")
        app.setup_go(-1)
        self.assertEqual(app.setup_step, "wifi", "the steps did not change under the setup")

    def test_a_cable_with_an_address_is_seen(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import system
        net = Path(tempfile.mkdtemp())
        (net / "lo").mkdir()
        (net / "wlan0" / "wireless").mkdir(parents=True)
        (net / "wlan0" / "device").mkdir()
        (net / "wlan0" / "carrier").write_text("1\n")
        self.assertFalse(system.wired_up(str(net)))
        (net / "eth0" / "device").mkdir(parents=True)
        (net / "eth0" / "carrier").write_text("0\n")
        self.assertFalse(system.wired_up(str(net)), "no carrier")
        (net / "eth0" / "carrier").write_text("1\n")
        with mock.patch("fcntl.ioctl", return_value=b""):
            self.assertTrue(system.wired_up(str(net)))
        with mock.patch("fcntl.ioctl", side_effect=OSError(99, "no address")):
            self.assertFalse(system.wired_up(str(net)))


if __name__ == "__main__":
    unittest.main()
