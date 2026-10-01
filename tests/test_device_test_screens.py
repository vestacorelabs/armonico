"""The device-test window after the round of wording and flow fixes: nothing moves on by itself, the hardware test
is one of the window's ways in, every summary card opens its details (with the log lines or every service), all
results can be listed, the CPU temperature shows on the test screens, and a number keeps its Latin unit beside it
in a Hebrew line."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_device_ui as T

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT))
import fullcheck as F          # noqa: E402

pygame = T.pygame


try:
    import bidi.algorithm                # noqa: F401  python-bidi: on the device (python3-bidi), not in the CI's requirements
    HAS_BIDI = True
except ImportError:
    HAS_BIDI = False


@unittest.skipUnless(HAS_BIDI, "python-bidi is not installed: Hebrew is not reordered here at all")
class UnitsInHebrew(unittest.TestCase):
    def order(self, s):
        sys.path.insert(0, str(ROOT / "device" / "ui"))
        from ui import canvas as C
        return C.get_display(C.UNIT_BIND.sub("‎", s), base_dir="R").replace("‎", "")

    def test_a_number_and_its_unit_stay_together(self):
        self.assertTrue(self.order("פועל כבר 5.2 שעות · 58 MB").startswith("58 MB"))
        self.assertTrue(self.order("בשימוש 547 / 3794 MB").startswith("547 / 3794 MB"))
        self.assertTrue(self.order("ענה תוך 140 ms").startswith("140 ms"))

    def test_hebrew_alone_and_words_with_digits_are_left_as_they_were(self):
        sys.path.insert(0, str(ROOT / "device" / "ui"))
        from ui import canvas as C
        for s in ("ב-24 השעות האחרונות", "קליד mmc0 שגיאה", "13 קלידים, 4 כפתורים"):
            self.assertEqual(self.order(s), C.get_display(s, base_dir="R"))


class Gpio(unittest.TestCase):
    def test_automatic_only_does_not_warn_that_the_keys_were_not_pressed(self):
        note = F.diag.Check(F.diag.NOTE, "gpio", "Keys and buttons", "קלידים וכפתורים", "not tested", "לא נבדקו", "")
        with mock.patch.object(F.diag, "check_gpio", return_value=note), mock.patch.object(F, "pin_states", return_value=None):
            auto = F.a_gpio({}, {"hand_steps": False})
            both = F.a_gpio({}, {"hand_steps": True})
        self.assertEqual(auto[0].level, F.OK)
        self.assertEqual(auto[0].link, "")
        self.assertEqual(both[0].level, F.NOTE)
        self.assertEqual(both[0].link, "test")


class Services(unittest.TestCase):
    def test_a_line_per_service_in_words(self):
        name, state, lvl = F.svc_row(("svc", "armonico-bridge", "active", "running", 0, 18720, 58e6))
        self.assertEqual(name[1], "גשר · armonico-bridge")
        self.assertEqual(state[1], "פועל כבר 5.2 שעות · 58 MB")
        self.assertEqual(lvl, F.OK)
        name, state, lvl = F.svc_row(("svc", "mosquitto", "failed", "failed", 3, None, None))
        self.assertEqual(state[0], "failed · restarted 3 times")
        self.assertEqual(lvl, F.PROBLEM)
        text = F.report_text([F.item(F.OK, "services", F.L("chk.ok"))._replace(
            lines=(("svc", "mosquitto", "failed", "failed", 3, None, None),))])
        self.assertIn("mosquitto: failed · restarted 3 times", text)


class BluetoothCheck(unittest.TestCase):
    """07.11.2026: the quick check's Bluetooth opened the pairing page instead of checking."""
    def run_bt(self, svc="active", radio="on", status=None):
        asked = []

        def request(action, timeout=10, **kw):
            asked.append((action, kw))
            return (True, json.dumps(status)) if status is not None else (False, "no-root")
        with mock.patch.object(F, "bt_service", return_value=svc), mock.patch.object(F, "bt_radio", return_value=radio), \
                mock.patch.object(F, "bt_items", return_value=[]), mock.patch.object(F, "chip_items", return_value=[]):
            res = F.a_bluetooth({}, {"request": request})
        return res, asked

    def test_it_looks_and_changes_nothing(self):
        res, asked = self.run_bt(status={"hw": True, "powered": True, "saved": [
            {"mac": "AA:BB:CC:DD:EE:01", "name": "JBL Go 3", "connected": True, "audio": True},
            {"mac": "AA:BB:CC:DD:EE:02", "name": "Watch", "connected": False, "audio": False}]})
        self.assertEqual(asked, [("bt-status", {"look": True})], "root only looks: it never turns the adapter on")
        self.assertTrue(all(i.level == F.OK for i in res))
        self.assertIn("JBL Go 3", [i.row[0] for i in res])
        self.assertIn("2 paired: JBL Go 3, Watch", [i.detail[0] for i in res])

    def test_what_goes_wrong_says_so(self):
        res, _ = self.run_bt(svc="inactive")
        self.assertEqual((res[0].level, res[0].link), (F.PROBLEM, "bt"))
        res, _ = self.run_bt(radio="off", status={"hw": False, "why": "stuck", "saved": []})
        self.assertEqual([i.level for i in res], [F.OK, F.NOTE, F.PROBLEM])
        res, _ = self.run_bt(status={"hw": True, "powered": True, "saved": [
            {"mac": "AA", "name": "Galaxy Watch", "connected": True, "audio": False}]})
        self.assertEqual(res[-1].level, F.NOTE)

    def test_it_is_a_quick_check_only(self):
        self.assertNotIn("bluetooth", [k for k, _ in F.areas({"DEVICE": "yes"})], "the full check keeps its 21")
        self.assertEqual(len(F.areas({"DEVICE": "yes"})), 21)
        self.assertEqual([k for k, _ in F.areas({"DEVICE": "yes"}, ["bluetooth"])], ["bluetooth"])


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screens(unittest.TestCase):
    def test_the_quick_bluetooth_check_checks(self):
        app = self.app()
        dict(app.quick_areas())["bt"]()
        self.assertEqual(app.view, "check")
        self.assertEqual(app.chk["only"], ["bluetooth"])
        app.draw()

    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, lang="he"):
        app = T.make_app(self.mod, (800, 480), lang)
        app.screen.pub = lambda *a, **k: None
        return app

    def summary(self, app):
        app.open_check("settings", "auto")
        c = app.chk
        c["areas"] = ["services", "sd", "time"]
        c["rows"] = [[F.item(F.PROBLEM, "services", F.L("chk.r.down1", x="armonico-bridge"), todo=F.L("chk.do.services"),
                             fix="services")._replace(lines=(("svc", "armonico-bridge", "failed", "failed", 1, None, None),))],
                     [F.item(F.PROBLEM, "sd", F.L("chk.r.io", n=1), F.L("chk.d.io", n=1), F.L("chk.do.io"),
                             label=F.L("chk.l.io"))._replace(lines=("[ 12.3] mmc0: error -110",))],
                     [F.item(F.OK, "time", F.L("chk.ok"))]]
        c["phase"], c["auto_end"] = "auto", app.t()
        app.draw()
        self.assertEqual(app.chk["phase"], "auto", "the list waits for Finish")
        app.press("ok")
        self.assertEqual(app.chk["phase"], "summary")
        return app

    def test_cards_open_their_details_and_all_results(self):
        for lang in ("he", "en"):
            app = self.summary(self.app(lang))
            app.draw()
            sd = next(i for i in app.chk["summary"]["list"] if i.area == "sd")
            app.chk_open_detail(sd, "summary")
            app.draw()
            kinds = [k for k, _ in app.chk_detail_lines(sd)]
            self.assertIn("log", kinds)
            app.press("menu")
            self.assertEqual(app.chk["phase"], "summary")
            svc = next(i for i in app.chk["summary"]["list"] if i.area == "services")
            app.chk_open_detail(svc, "summary")
            app.draw()
            self.assertIn("svc", [k for k, _ in app.chk_detail_lines(svc)])
            self.assertEqual(app.bar_items[3][1], app.tr("chk.fix"), "the fix stays one press away")
            app.chk_open_all()
            app.draw()
            self.assertEqual(app.chk["phase"], "all")
            app.press("menu")
            self.assertEqual(app.chk["phase"], "summary")

    def test_the_hardware_test_is_in_the_window(self):
        app = self.app()
        cells = app.settings_cells()["test"]
        self.assertIn(app.tr("tm.hw"), [c[0] for c in cells])

    def test_the_temperature_shows_on_the_test_screens(self):
        f = Path(tempfile.mkdtemp()) / "temp"
        f.write_text("71500\n")
        app = self.app("en")
        with mock.patch.object(type(app), "TEMP_FILE", str(f)):
            app._temp_seen = (-99.0, None)
            got = []
            from ui import canvas as C
            orig = C.Canvas.text

            def rec(cv, s, *a, **k):
                got.append(str(s))
                return orig(cv, s, *a, **k)
            with mock.patch.object(C.Canvas, "text", rec):
                app.open_check("settings", "auto")
                app.draw()
        self.assertIn("CPU 72°C", got)
