"""The path line over every title below the top of Settings is one chain, built from one table
(widgets.SCREEN_HOME and views_settings.SUB_PAGES): the screens that led here, outermost first.

Night mode as one setting (when it is active, and how strong).

Group B: the alarm clock in the top bar, the path line over a Settings title, and the
What's new screen after an update (device/ui/views_whatsnew.py).

One device-test window (4 ways in), a System page of 7 cards, the interface size setting.

The corner rings clear of the buttons, Bluetooth devices with no name, the card text at 150%,
and the connection and address cards on the network page.

The buttons under the screen can be hidden (Settings › Look and screen). The real buttons keep
working, a touch on the screen shows them for 5 seconds, and the tour and the questions always show them.

The QR code of the lesson screen's address (qrmini.py, no library) and where it shows: the end of the
first setup, a Wi-Fi connection that worked, and Settings › Network and links (a page of its own).

Each way into the check opens with its own title and lines.
"""
import sys
import unittest
from pathlib import Path
from test_device_ui import load_screen, make_app, pygame
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT))                 # "from device import fullcheck" finds the project folder
import os
from unittest import mock
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import unittest.mock
import time
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:                      # no pygame
    pygame = None
import random
sys.path.insert(0, str(ROOT / "device" / "ui"))
import qrmini
try:
    import zxingcpp
except ImportError:
    zxingcpp = None
try:
    import numpy
except ImportError:
    numpy = None
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None


@unittest.skipIf(pygame is None, "pygame is not installed")
class PathOnEveryScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def apps(self):
        for lang in ("en", "he"):
            yield make_app(self.mod, lang=lang)

    def test_every_settings_page_has_its_chain_and_its_parent_last(self):
        from ui.views_settings import PAGES, SUB_PAGES
        for app in self.apps():
            for page in [p for p in PAGES if p != "top"] + list(SUB_PAGES):
                app.page = page
                app.go("settings")
                trail = app.trail_of("settings")
                self.assertGreaterEqual(len(trail), 2, page)
                self.assertEqual(trail[0], app.tr("settings"))
                self.assertEqual(trail[-1], app.tr("g." + page), page)
                parent = SUB_PAGES[page][0] if page in SUB_PAGES else None
                if parent:
                    self.assertEqual(trail[-2], app.tr("g." + parent), page)
                path = app.screen_path(app.tr("g." + page))
                self.assertTrue(path.startswith(app.tr("settings")), page)
                self.assertNotIn(app.tr("g." + page), path.split(" • ")[-1] if path else "x", page)
                app.draw()                                   # and it draws

    def test_the_top_of_settings_and_the_screens_outside_it_have_none(self):
        for app in self.apps():
            app.page = "top"
            app.go("settings")
            self.assertEqual(app.screen_path(app.tr("settings")), "")
            for view in ("rest", "songs", "free", "setup", "job", "displays", "whatsnew"):
                self.assertEqual(app.trail_of(view), [], view)

    def test_a_full_screen_sits_under_the_page_that_opens_it(self):
        from ui.widgets import Widgets
        for app in self.apps():
            app.diag_from, app.upd_from, app.place_return, app.net_from = ("settings",) * 4
            app.wf["from"] = "settings"
            app.chk = {"back": "settings"}
            for view, (page, key) in Widgets.SCREEN_HOME.items():
                trail = app.trail_of(view)
                self.assertEqual(trail[:len(app.page_trail(page))], app.page_trail(page), view)
                if key:
                    self.assertEqual(trail[-1], app.tr(key), view)
                else:
                    self.assertEqual(trail[-1], app.tr("g." + page), view)

    def test_a_screen_opened_from_elsewhere_shows_no_path(self):
        for app in self.apps():
            app.diag_from, app.upd_from, app.place_return, app.net_from = "rest", "rest", "setup", "rest"
            for view in ("diag", "upd", "place", "net"):
                self.assertEqual(app.trail_of(view), [], view)
            app.wf["from"] = "setup"
            self.assertEqual(app.trail_of("wifi"), [])
            app.chk = {"back": "rest"}
            self.assertEqual(app.trail_of("check"), [])

    def test_a_field_takes_the_chain_of_the_screen_it_edits(self):
        for app in self.apps():
            app.page = "tg"
            app.go("settings")
            app.edit_field("Name", lambda: "", lambda v: None)
            self.assertEqual(app.view, "edit")
            self.assertEqual(app.screen_path("Name"), " • ".join(app.page_trail("tg")))
            app.draw()

    def test_the_saved_networks_and_the_check_steps_sit_one_level_deeper(self):
        for app in self.apps():
            app.view = "wifi"
            app.wf["from"] = "settings"
            self.assertEqual(app.screen_path(app.tr("wf.saved")), " • ".join(app.page_trail("net") + [app.tr("wf.title")]))
            self.assertEqual(app.screen_path(app.tr("wf.title")), " • ".join(app.page_trail("net")))
            app.view, app.chk = "check", {"back": "settings"}
            self.assertEqual(app.screen_path("step 2"), " • ".join(app.page_trail("test") + [app.tr("chk")]))

    def test_a_chain_too_wide_drops_its_middle_levels(self):
        app = next(self.apps())
        app.draw()
        wide = app.fit_chain("Settings • Network and links • Wi-Fi • Saved networks", 150)
        self.assertIn("…", wide)
        self.assertTrue(wide.startswith("Settings"))
        self.assertEqual(app.fit_chain("Settings • System", 700), "Settings • System")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "pygame is not installed")
class NightMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, **settings):
        app = make_app(self.mod)
        app.st.update(settings)
        app.screen.engine.state = {"mode": "idle", "volume": 80}
        app.set_volume = mock.Mock(side_effect=lambda v: app.screen.engine.state.update(volume=v))
        return app

    def at(self, app, hour, minute=0):
        return mock.patch.object(app, "local", return_value=mock.Mock(hour=hour, minute=minute))

    def test_from_before_on_means_sunset_to_sunrise_and_the_screen_level_maps_to_a_strength(self):
        app = self.app(night=True, night_screen=100)
        self.assertEqual((app.night_when(), app.night_level()), ("sun", 2))
        self.assertEqual(self.app(night_screen=25).night_level(), 3)
        self.assertEqual(self.app(night_screen=0).night_level(), 3)
        self.assertEqual(self.app().night_when(), "off", "off after an install, as before")

    def test_chosen_hours_wrap_past_midnight(self):
        app = self.app(night_when="hours", night_from=22 * 60 + 30, night_to=6 * 60 + 30)
        for h, m, want in ((22, 29, False), (22, 30, True), (23, 59, True), (0, 0, True), (6, 29, True), (6, 30, False), (12, 0, False)):
            with self.at(app, h, m):
                self.assertEqual(app.is_night(), want, f"{h}:{m}")
        app.st.update(night_from=8 * 60, night_to=17 * 60)
        with self.at(app, 12):
            self.assertTrue(app.is_night())
        with self.at(app, 20):
            self.assertFalse(app.is_night())
        app.st.update(night_from=600, night_to=600)
        with self.at(app, 10):
            self.assertFalse(app.is_night(), "the same time twice: never")

    def test_brightness_and_volume_drop_together_by_strength(self):
        app = self.app(night_when="hours", night_from=0, night_to=1439, brightness=100)
        for level, (b, v) in ((1, (75, 70)), (2, (50, 40)), (3, (25, 20))):
            app.st["night_level"] = level
            with self.at(app, 3):
                self.assertEqual(app.light_level(), b)
        app.st["brightness"] = 25
        app.st["night_level"] = 1
        with self.at(app, 3):
            self.assertEqual(app.light_level(), 25, "never brighter than the day level")

    def test_the_volume_drops_to_the_strengths_fixed_volume_and_comes_back(self):
        app = self.app(night_when="hours", night_from=22 * 60, night_to=6 * 60, night_level=2)
        with self.at(app, 12):
            app.follow_night()
        app.set_volume.assert_not_called()
        with self.at(app, 23):
            app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 55)
            app.st["night_level"] = 3                      # a stronger one while it is on
            app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 40)
            app.st["night_level"] = 1                      # a lighter one: from the day volume, not from the last
            app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 70)
        with self.at(app, 7):
            app.follow_night()
        self.assertEqual(app.screen.engine.state["volume"], 80, "the day volume comes back")
        self.assertIsNone(app.saved.get("night_day_volume"))

    def test_a_quiet_volume_is_not_lowered_below_the_floor_and_never_silenced(self):
        from ui.light import NIGHT_FLOOR, NIGHT_STRENGTH, night_volume
        app = self.app(night_when="hours", night_from=0, night_to=1439, night_level=3)
        app.screen.engine.state["volume"] = 10
        with self.at(app, 3):
            app.follow_night()
        self.assertEqual(app.screen.engine.state["volume"], 10)
        self.assertEqual(night_volume(40, NIGHT_STRENGTH[2][1]), 40, "a day volume at the strength is left as it is")
        self.assertEqual(night_volume(100, NIGHT_STRENGTH[2][1]), 40)
        for day in range(NIGHT_FLOOR + 1, 101):
            for _, fixed in NIGHT_STRENGTH:
                self.assertGreaterEqual(night_volume(day, fixed), NIGHT_FLOOR, "no strength mutes the device or the alarm")
                self.assertLessEqual(night_volume(day, fixed), day, "never louder than the day volume")

    def test_turning_it_off_while_active_gives_the_volume_back(self):
        app = self.app(night_when="hours", night_from=0, night_to=1439, night_level=2)
        with self.at(app, 3):
            app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 55)
            app.st["night_when"] = "off"
            app.follow_night()
        self.assertEqual(app.screen.engine.state["volume"], 80)

    def test_the_alarm_rings_at_100_and_the_volume_comes_back(self):
        app = self.app(night_when="hours", night_from=0, night_to=1439, night_level=2)
        with self.at(app, 3):
            for _ in range(2):
                app.follow_alarm_volume()
                app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 55)
            app.screen.alarm = True
            for _ in range(2):
                app.follow_alarm_volume()
                app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 100, "the alarm at 100, night mode or not")
            app.screen.alarm = False
            for _ in range(2):
                app.follow_alarm_volume()
                app.follow_night()
            self.assertEqual(app.screen.engine.state["volume"], 55, "the volume before it, once it stops")
            self.assertIsNone(app.saved.get("alarm_volume_before"))

    def test_a_power_cut_while_the_alarm_rings_gives_the_volume_back(self):
        app = self.app(alarm_volume_before=45)
        app.screen.engine.state["volume"] = 100
        app.follow_alarm_volume()
        self.assertEqual(app.screen.engine.state["volume"], 45)

    def test_the_power_cut_at_night_gives_the_day_volume_back_at_the_next_start(self):
        # the cable pulled at night: the engine kept the night volume, the day volume is still saved
        app = self.app(night_when="hours", night_from=22 * 60, night_to=6 * 60, night_level=2, night_day_volume=80)
        app.screen.engine.state["volume"] = 55
        with self.at(app, 12):
            app.follow_night()
        self.assertEqual(app.screen.engine.state["volume"], 80)
        self.assertIsNone(app.saved.get("night_day_volume"))

    def test_the_page_has_a_switch_then_automatic_then_dimming_and_moves_the_hours(self):
        app = self.app(night_when="sun", night_level=2)
        app.open_page("night")
        app.draw()
        cells = app.settings_cells()["night"]
        self.assertEqual([c[0] for c in cells], [app.tr("c.night"), app.tr("c.nmAuto"), app.tr("c.nmLevel")])
        cells[0][3]()
        self.assertIs(app.saved["night_on"], False, "a touch on the switch turns it off")
        app.st["night_on"] = True
        cells = app.settings_cells()["night"]
        cells[1][3]()
        self.assertEqual(app.saved["night_auto"], "hours")
        app.st["night_auto"] = "hours"
        cells = app.settings_cells()["night"]
        self.assertEqual([c[0] for c in cells][2:4], [app.tr("c.nmFrom"), app.tr("c.nmTo")])
        cells[2][3]()
        self.assertEqual(app.saved["night_from"], 23 * 60)
        cells[3][3]()
        self.assertEqual(app.saved["night_to"], 7 * 60)
        cells[-1][3]()
        self.assertEqual(app.saved["night_level"], 3)
        app.st["night_level"] = 3
        app.settings_cells()["night"][-1][3]()
        self.assertEqual(app.saved["night_level"], 1)
        app.st["night_on"] = False
        cells = app.settings_cells()["night"]
        self.assertTrue(all(c[3] is None for c in cells[1:]), "everything under the switch is dimmed while it is off")
        cells[0][3]()
        self.assertIs(app.saved["night_on"], True)

    def test_the_old_choice_becomes_the_switch_and_automatic(self):
        self.assertEqual((self.app(night_when="hours").night_on(), self.app(night_when="hours").night_auto()), (True, "hours"))
        self.assertEqual((self.app(night_when="sun").night_on(), self.app(night_when="sun").night_auto()), (True, "sun"))
        off = self.app(night_when="off")
        self.assertEqual((off.night_on(), off.night_auto(), off.night_when()), (False, "sun", "off"))
        new = self.app(night_when="off", night_on=True, night_auto="manual")
        self.assertEqual(new.night_when(), "manual", "the new keys win over the old one")

    def test_manual_is_night_until_the_switch_is_turned_off(self):
        app = self.app(night_on=True, night_auto="manual")
        with self.at(app, 13):
            self.assertTrue(app.is_night())
        app.st["night_on"] = False
        with self.at(app, 13):
            self.assertFalse(app.is_night())

    def test_back_from_the_page_returns_to_look_and_screen(self):
        from ui.views_settings import SUB_PAGES
        app = self.app()
        look = [c[0] for c in app.settings_cells()["look"]]
        self.assertEqual(SUB_PAGES["night"], ("look", look.index(app.tr("c.night"))))
        self.assertEqual(len(look), len(set(look)), "one night cell in the look page, the old 2 are gone")

    def test_the_page_draws_in_both_languages_without_cut_text(self):
        from ui.texts import T
        fixed = {v for pair in T.values() if isinstance(pair, tuple) for v in pair}
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            app.short_missed = set()
            for when in ("sun", "hours", "manual", "off"):
                app.st.update(night_on=when != "off", night_auto="sun" if when == "off" else when)
                app.open_page("night")
                for i, c in enumerate(app.settings_cells()["night"]):
                    app.sel = i
                    app.draw()
                app.open_page("look")
                app.draw()
            cut = {m[1] for m in app.short_missed if m[1] in fixed}
            self.assertEqual(cut, set(), f"{lang}: give these a shorter wording in texts.SHORT")

    def test_the_rest_screen_says_until_when_for_chosen_hours(self):
        app = self.app(night_when="hours", night_from=0, night_to=7 * 60 + 15)
        with self.at(app, 3):
            self.assertTrue(app.is_night())
        self.assertEqual(app.tr("quietSubH", t="07:15"), "quieter and darker until 07:15")


if __name__ == "__main__":
    unittest.main()


CHANGELOG = """# Changelog

## 1.1 (in progress)

### Highlights

**New**
- First thing
- Second thing

**Fixed**
- A fix

- A long old bullet that is not a highlight.

## 1.0

- Old.
"""


class Parse(unittest.TestCase):
    def test_reads_the_block_of_the_version_only(self):
        from ui.views_whatsnew import parse_highlights
        g = parse_highlights(CHANGELOG, "1.1")
        self.assertEqual(g, [("new", ["First thing", "Second thing"]), ("fixed", ["A fix"])])
        self.assertIsNone(parse_highlights(CHANGELOG, "1.0"))      # a section with no block
        self.assertIsNone(parse_highlights(CHANGELOG, "9.9"))      # no such section
        self.assertIsNone(parse_highlights("", "1.1"))

    def test_the_shipped_changelog_has_highlights_for_its_version(self):
        from ui.views_whatsnew import parse_highlights
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        g = parse_highlights((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
        self.assertTrue(g)
        self.assertEqual([k for k, _ in g], ["new", "improved", "fixed"])
        self.assertTrue(all(len(x) < 90 for _, items in g for x in items), "short lines only")


@unittest.skipIf(pygame is None, "pygame is not installed")
class WhatsNew(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, lang="en", **settings):
        app = make_app(self.mod, lang=lang, settings=settings)
        app.version = lambda: "1.1"
        return app

    def load(self, app, text=CHANGELOG):
        with mock.patch("ui.views_whatsnew.changelog_path") as p:
            p.return_value = mock.Mock(read_text=lambda encoding=None: text)
            app.wn_load()

    def test_due_on_a_device_in_use_after_an_update(self):
        app = self.app()
        self.load(app)
        self.assertTrue(app.wn_due)
        self.assertNotIn("whatsnew_seen", app.saved)

    def test_not_on_a_fresh_install_or_reset_and_recorded(self):
        app = self.app()
        app.st.pop("setup_done")
        self.load(app)
        self.assertFalse(app.wn_due)
        self.assertEqual(app.saved.get("whatsnew_seen"), "1.1")

    def test_not_without_highlights_and_not_twice(self):
        app = self.app()
        self.load(app, "# Changelog\n\n## 1.1\n\n- Something.\n")
        self.assertFalse(app.wn_due)
        self.assertEqual(app.saved.get("whatsnew_seen"), "1.1")
        app = self.app(whatsnew_seen="1.1")
        self.load(app)
        self.assertFalse(app.wn_due)
        self.assertNotIn("whatsnew_seen", app.saved)

    def test_shows_on_the_idle_rest_screen_then_got_it_records_and_leaves_the_check_offer(self):
        for lang in ("en", "he"):
            app = self.app(lang)
            self.load(app)
            app.chk_offer = True                      # the full check's offer after the update waits behind it
            app.draw()
            self.assertEqual(app.view, "whatsnew", lang)
            app.draw()
            app.button("ok", False)
            self.assertEqual(app.view, "rest")
            self.assertEqual(app.saved.get("whatsnew_seen"), "1.1")
            self.assertFalse(app.wn_due)
            self.assertTrue(app.chk_offer, "the check is still offered on the rest screen")
            app.draw()
            self.assertEqual(app.view, "rest", "once only")

    def test_waits_for_a_lesson_or_the_tour(self):
        app = self.app()
        self.load(app)
        app.st["tour_done"] = False
        self.assertFalse(app.wn_ready())
        app.st["tour_done"] = True
        self.assertTrue(app.wn_ready())
        with mock.patch.object(app, "device_busy", return_value=True):
            self.assertFalse(app.wn_ready())

    def test_every_row_fits_and_scrolls_in_both_languages(self):
        for lang in ("en", "he"):
            app = self.app(lang)
            self.load(app)
            app.open_whatsnew()
            app.draw()
            for _ in range(4):
                app.wn_move(1)
                app.draw()
            app.wn_move(-9)
            app.draw()
            self.assertEqual(app.wn_scroll, 0)


@unittest.skipIf(pygame is None, "pygame is not installed")
class TopBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_alarm_chip_only_when_on_and_ringing_here(self):
        app = make_app(self.mod, settings={"alarm": {"on": True, "time": "06:30", "days": "all", "by": "device", "song": "ode_to_joy"}})
        self.assertEqual(app.alarm_chip(), "06:30")
        app.save_alarm(on=False)
        self.assertEqual(app.alarm_chip(), "", "a change shows at once")
        app.save_alarm(on=True, by="ha")
        with mock.patch.object(app, "local_now", return_value=False):
            app._chip = (-1e9, "")
            self.assertEqual(app.alarm_chip(), "", "Home Assistant rings it on an external broker")

    def test_headers_with_a_path_draw_in_both_languages(self):
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang, settings={"alarm": {"on": True, "time": "06:30", "days": "all", "by": "device", "song": "ode_to_joy"}})
            app.page = "sys"
            app.go("settings")
            app.draw()
            app.open_diag("settings")
            app.draw()
            app.open_check("settings")
            app.draw()
            app.open_import()
            app.draw()
            app.view, app.page = "diag", "sys"
            app.diag_from = "settings"
            self.assertEqual(app.screen_path(app.tr("diag")), app.tr("settings") + " • " + app.tr("g.sys") + " • " + app.tr("g.test"))
            app.diag_from = "rest"
            self.assertEqual(app.screen_path(app.tr("diag")), "")


class InstallerKeepsTheChangelog(unittest.TestCase):
    """What's new reads CHANGELOG.md from the installed folder, so the installer has to put it there."""

    def test_the_installer_copies_and_requires_it(self):
        src = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
        self.assertIn('install -m 644 "$REPO_DIR/CHANGELOG.md" "$OPT_DIR/CHANGELOG.md"', src)
        self.assertIn("    CHANGELOG.md\n    bridge/piano_bridge.sh", src)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class DeviceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def apps(self):
        for lang in ("en", "he"):
            yield make_app(self.mod, lang=lang)

    def test_the_system_page_has_4_cards_and_one_test_card(self):
        for app in self.apps():
            cells = app.settings_cells()["sys"]
            self.assertEqual(len(cells), 4)                  # the restart and the two resets on one page
            labels = [c[0] for c in cells]
            for gone in ("diag", "chk", "c.hw", "c.rec", "c.recExport", "c.wizard", "c.tour"):
                self.assertNotIn(app.tr(gone), labels, gone)
            self.assertEqual(labels[0], app.tr("c.devtest"))

    def test_ten_touches_on_the_vcl_logo_show_the_recording_card_in_its_place(self):
        for app in self.apps():
            clock = [100.0]
            app.t = lambda: clock[0]
            for _ in range(9):
                app.vcl_tap()
                clock[0] += 0.5
            self.assertFalse(app.rec_card_on())
            clock[0] += 2                                # a pause: the count starts over
            for _ in range(10):
                app.vcl_tap()
                clock[0] += 0.5
            self.assertTrue(app.rec_card_on())
            self.assertEqual(app.screen.toast[0], app.tr("recShown"))
            labels = [c[0] for c in app.settings_cells()["sys"]]
            self.assertEqual(len(labels), 5)
            self.assertEqual(labels[2], app.tr("c.rec"))
            for _ in range(10):                          # more touches never hide it
                app.vcl_tap()
            self.assertTrue(app.rec_card_on())

    def test_back_from_the_guide_page_lands_on_its_card_with_the_recording_hidden_or_shown(self):
        for app in self.apps():
            for shown, want in ((False, 2), (True, 3)):
                app.rec_shown = shown
                app.go("settings")
                app.page, app.sel = "guide", 0
                app.draw()
                app.press("menu")                        # back
                self.assertEqual((app.page, app.sel), ("sys", want))
                self.assertEqual(app.settings_cells()["sys"][want][0], app.tr("g.guide"))

    def test_the_test_page_has_6_ways_in(self):
        for app in self.apps():
            cells = app.settings_cells()["test"]
            self.assertEqual(len(cells), 6)
            modes = []
            for c in cells[:3]:
                app.go("settings")
                c[3]()
                self.assertEqual(app.view, "check")
                modes.append(app.chk["mode"])
            self.assertEqual(modes, ["both", "auto", "guided"])
            app.page = "test"
            app.go("settings")
            cells[3][3]()
            self.assertEqual(app.view, "test", "the hardware test is in the window")
            app.page = "test"
            app.go("settings")
            cells[4][3]()
            self.assertEqual(app.view, "deep")
            app.page = "test"
            app.go("settings")
            cells[5][3]()
            self.assertEqual(app.page, "quick")

    def test_modes_pick_what_runs(self):
        app = next(self.apps())
        app.open_check("settings", "auto")
        self.assertEqual(app.chk["steps"], [])
        app.open_check("settings", "guided")
        self.assertEqual(app.chk["only"], [])
        self.assertEqual(len(app.chk["steps"]), 8, "no USB MIDI keyboard: its step is left out")
        app.open_check("settings", "both")
        self.assertIsNone(app.chk["only"])
        self.assertEqual(len(app.chk["steps"]), 8)
        app.keyboards = ["Digital Keyboard"]
        app.open_check("settings", "guided")
        self.assertEqual(len(app.chk["steps"]), 9)
        self.assertIn("usb", app.chk["steps"])

    def test_a_guided_only_check_goes_straight_to_the_steps_and_ends_in_a_report(self):
        app = next(self.apps())
        app.open_check("settings", "guided", None)
        app.chk_start()
        self.assertEqual(app.chk["phase"], "guided")
        for _ in range(len(app.chk["steps"])):
            app.chk_skip()
        self.assertEqual(app.chk["phase"], "summary")
        app.draw()

    def test_an_auto_only_check_never_reaches_the_guided_part(self):
        from device import fullcheck as F
        app = next(self.apps())
        app.open_check("settings", "auto")
        app.chk.update(phase="auto", rows=[[F.item(F.OK, "time", F.L("chk.ok"), F.L("chk.ok"))]], areas=["time"],
                       auto_end=app.t() - 5)
        app.draw()
        self.assertEqual(app.chk["phase"], "auto", "the list stays until Finish")
        self.assertEqual(app.bar_items[3][1], app.tr("chk.finish"))
        app.press("ok")
        self.assertEqual(app.chk["phase"], "summary")

    def test_the_quick_areas_run_only_their_own_part(self):
        app = next(self.apps())
        cells = app.settings_cells()["quick"]
        self.assertEqual(len(cells), 7)
        app.go("settings")
        cells[0][3]()                                       # sound
        self.assertEqual(app.chk["only"], ["sound", "samples"])
        self.assertEqual(app.chk["steps"], ["right", "left", "quality"])
        cells[3][3]()                                       # network: no guided step
        self.assertEqual(app.chk["steps"], [])
        cells[1][3]()                                       # touch: no automatic area
        app.chk_start()
        self.assertEqual(app.chk["phase"], "guided")
        self.assertEqual(app.chk["steps"], ["touch"])

    def test_run_all_can_be_limited_to_some_areas(self):
        from device import fullcheck as F
        res = F.run_all({"DEVICE": "no"}, None, only=["time"])
        self.assertEqual([k for k, _ in res], ["time"])

    def test_the_report_says_what_was_not_run(self):
        from device import fullcheck as F
        text = F.report_text([], [], guided_done=False)
        self.assertIn("Automatic checks\n  not run", text)
        self.assertIn("Guided steps\n  not run", text)


@unittest.skipIf(pygame is None, "no pygame")
class Zoom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_steps_stay_in_80_to_150_and_save(self):
        app = make_app(self.mod)
        self.assertEqual(app.zoom_pct(), 100)
        for _ in range(10):
            app.zoom_move(1)
        self.assertEqual(app.zoom_pct(), 150)
        for _ in range(10):
            app.zoom_move(-1)
        self.assertEqual(app.zoom_pct(), 80)
        app.st["zoom"] = 137                                # a value that is not a step: the default
        self.assertEqual(app.zoom_pct(), 100)

    def test_the_card_opens_the_adjust_mode_and_the_reset_card_goes_back_to_100(self):
        app = make_app(self.mod)
        look = app.settings_cells()["look"]
        self.assertEqual(look[2][0], app.tr("c.zoom"))
        app.open_page("zoom")
        app.settings_cells()["zoom"][0][3]()
        self.assertTrue(app.zoom_adjust)
        app.draw()
        app.zoom_move(2)
        self.assertEqual(app.zoom_pct(), 120)
        app.zoom_adjust = False
        app.settings_cells()["zoom"][1][3]()
        self.assertEqual(app.zoom_pct(), 100)
        self.assertIsNone(app.settings_cells()["zoom"][1][3])    # already 100: nothing to reset

    def test_the_text_grows_with_the_setting_and_every_page_draws_at_both_ends(self):
        app = make_app(self.mod)
        cv = app.cv
        cv.zoom = 1.0
        w1 = cv.measure("Armonico", 16)
        cv.zoom = 1.5
        self.assertGreater(cv.measure("Armonico", 16), w1 * 1.3)
        cv.zoom = 1.0
        from ui.views_settings import PAGES, SUB_PAGES
        for lang in ("en", "he"):
            a = make_app(self.mod, lang=lang)
            for z in (80, 150):
                a.st["zoom"] = z
                for page in [p for p in PAGES] + list(SUB_PAGES):
                    a.page, a.sel = page, 0
                    a.go("settings")
                    a.draw()


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class Fixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_no_button_lies_on_a_corner_ring(self):
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            app.open_test()
            app.draw()
            rings = app.touch_targets()
            for hx, hy, hw, hh, _ in app.hits:
                for cx, cy in rings:
                    near_x = min(max(cx, hx), hx + hw)
                    near_y = min(max(cy, hy), hy + hh)
                    self.assertGreater((near_x - cx) ** 2 + (near_y - cy) ** 2, 34 ** 2, (lang, hx, hy))
            app.open_check("settings", "guided", None)
            app.chk_guided_start()
            app.draw()
            for hx, hy, hw, hh, _ in app.hits:
                for cx, cy in rings:
                    near_x = min(max(cx, hx), hx + hw)
                    near_y = min(max(cy, hy), hy + hh)
                    self.assertGreater((near_x - cx) ** 2 + (near_y - cy) ** 2, 34 ** 2, (lang, "touch step"))

    def test_devices_with_no_name_sit_in_one_folded_row(self):
        app = make_app(self.mod, lang="he")
        b = app.bt
        b.update(hw=True, seen=35, named=1, status_at=1e18)
        b["found"] = [{"mac": "AA:BB:CC:00:00:01", "name": "Watch", "kind": "other"},
                      {"mac": "AA:BB:CC:00:00:02", "name": "", "kind": "audio"}] + \
                     [{"mac": f"11:22:33:44:55:{i:02x}", "name": "", "kind": "unknown"} for i in range(30)]
        kinds = [k for k, _ in app.bt_lines()]
        self.assertEqual(kinds.count("found"), 2, "the named one and the nameless sound device")
        self.assertEqual(kinds.count("hidden"), 1)
        self.assertEqual(app.bt_lines()[kinds.index("hidden")][1], 30)
        row = next(l for l in app.bt_lines() if l[0] == "hidden")
        app.bt_act(row)
        self.assertEqual([k for k, _ in app.bt_lines()].count("found"), 32)
        self.assertEqual(app.bt_ok_label(row), app.tr("bt.hide"))
        self.assertIn("55:02", app.bt_name(b["found"][-28]))
        app.go("bt")
        app.draw()

    def test_the_bluetooth_header_is_short_enough(self):
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            app.cv.zoom = 1.5
            text = app.tr("bt.seen", n=35, m=1)
            self.assertLess(app.cv.measure(text, 14), 230, lang)

    def test_a_card_gives_the_zoom_back_and_its_lines_do_not_cross(self):
        app = make_app(self.mod, lang="he")
        app.cv.zoom = 1.5
        app.cell(24, 58, 370, 64, "שפה", "עברית", "מעבר ל-English", False, True, lambda: None)
        self.assertEqual(app.cv.zoom, 1.5)
        app.cv.zoom = 1.0

    def test_the_network_page_shows_the_connection_and_the_address(self):
        app = make_app(self.mod, lang="he")
        w = app.wf
        w.update(nm=True, via="wifi", ssid="Home", signal=70, ip="192.168.10.25", gw="192.168.10.1")
        net = app.settings_cells()["net"]
        self.assertEqual(net[0][0], app.tr("c.link"))
        self.assertEqual(net[0][1], "Wi-Fi · Home")
        # the address in the connection's own card (it had a card of its own): the address and the signal
        self.assertEqual(net[0][2], "192.168.10.25 · " + app.tr("c.sig", n=3))
        w.update(via="ethernet", cable_ip="192.168.10.40")
        net = app.settings_cells()["net"]
        self.assertEqual(net[0][1], app.tr("c.viaCable"))
        self.assertEqual(net[0][2], "192.168.10.40 · " + app.tr("c.gw", g="192.168.10.1"))
        for lang in ("en", "he"):
            a = make_app(self.mod, lang=lang)
            a.st["zoom"] = 150
            a.page = "net"
            a.go("settings")
            a.draw()


@unittest.skipIf(pygame is None, "no pygame")
class StartAndSleep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_the_splash_shows_the_logo_a_status_line_and_a_bar_that_fills(self):
        import ui.app as appmod
        self.assertTrue(self.mod.SPLASH_LOGO.exists())
        for lang in ("en", "he"):
            self.assertEqual(len(self.mod.SPLASH_STEPS[lang]), 4)
        for text in self.mod.SPLASH_STEPS["he"]:
            self.assertNotIn(".", text, "the Hebrew Heebo file has no punctuation")
            self.assertNotIn("…", text)
        pygame.display.set_mode((800, 480))
        pygame.font.init()
        shots = []
        with unittest.mock.patch.object(self.mod, "load_settings", lambda: {"lang": "he"}):
            for step in range(4):
                self.mod.splash(pygame, step)
                shots.append(pygame.image.tostring(self.mod.SPLASH["surf"], "RGB"))
        self.assertEqual(len(set(shots)), 4, "each step looks different")
        self.assertIsNotNone(self.mod.SPLASH["surf"])
        app = make_app(self.mod)
        app.pg.display.get_surface().fill((0, 0, 0))
        app.splash_fade(100.0)
        app.splash_fade(100.3)
        self.assertIsNotNone(self.mod.SPLASH["surf"], "still fading at 0.3 s")
        app.splash_fade(100.7)
        self.assertIsNone(self.mod.SPLASH["surf"], "gone after 0.6 s")
        self.assertGreaterEqual(appmod.SLEEP_FPS, 12)

    def test_the_sleep_screen_moves_smoothly(self):
        import ui.app as appmod
        from ui import sleep
        self.assertGreaterEqual(appmod.SLEEP_FPS, 12)
        self.assertEqual(appmod.SLEEP_POLL % appmod.SLEEP_FPS, 0, "every second look draws, evenly")
        self.assertGreaterEqual(sleep.POSES, 10)
        app = make_app(self.mod)
        sl = sleep.SleepScreen()
        for _ in range(3):
            sl.draw(app)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class BarHide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def bar_hits(self, app):
        return [h for h in app.hits if h[1] >= 480 - 58]

    def test_the_setting_is_a_card_and_toggles(self):
        app = make_app(self.mod, lang="en")
        cell = next(c for c in app.settings_cells()["look"] if c[0] == app.tr("c.bar"))
        self.assertEqual(cell[1], app.tr("bar.shown"))
        cell[3]()
        self.assertTrue(app.st.get("bar_hide"))
        cell = next(c for c in app.settings_cells()["look"] if c[0] == app.tr("c.bar"))
        self.assertEqual(cell[1], app.tr("bar.hidden"))

    def test_hidden_buttons_have_no_touch_but_the_real_ones_work(self):
        app = make_app(self.mod, lang="en")
        app.go("free")
        app.draw()
        self.assertTrue(self.bar_hits(app), "shown: the bar takes touches")
        app.save(bar_hide=True)
        app.bar_until = 0
        app.draw()
        self.assertFalse(self.bar_hits(app), "hidden: nothing under the bar's place takes a touch")
        self.assertEqual(len(app.bar_items), 4, "the real buttons still know what they do")
        before = app.screen.octave
        app.press("up")
        self.assertEqual(app.screen.octave, before + 1)

    def test_a_touch_shows_the_buttons_for_5_seconds(self):
        app = make_app(self.mod, lang="en")
        app.save(bar_hide=True)
        app.go("free")
        app.bar_until = 0
        app.draw()
        self.assertFalse(self.bar_hits(app))
        app.pointer_down(*app.cv.to_screen(400, 100)) if hasattr(app.cv, "to_screen") else app.pointer_down(400, 100)
        app.draw()
        self.assertTrue(self.bar_hits(app), "the buttons are up after a touch")
        app.bar_until = time.time() - 1
        app.draw()
        self.assertFalse(self.bar_hits(app), "and gone again after the time")

    def test_a_question_shows_them_as_always(self):
        app = make_app(self.mod, lang="en")
        app.save(bar_hide=True)
        app.bar_until = 0
        app.ask("?", lambda: None)
        self.assertFalse(app.bar_hidden())


if __name__ == "__main__":
    unittest.main()


class Encoder(unittest.TestCase):
    def test_the_sizes_and_the_fixed_parts(self):
        for text, side in (("a" * 14, 21), ("a" * 15, 25), ("http://192.168.0.101:8099", 25), ("a" * 106, 41)):
            m = qrmini.modules(text)
            self.assertEqual((len(m), len(m[0])), (side, side), text)
            n = side
            for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):      # the 3 finders: a dark centre, a light ring, a dark ring
                self.assertTrue(m[cy][cx] and m[cy + 3][cx + 3 - 3] and not m[cy + 2][cx + 2])
            self.assertTrue(all(m[6][i] == (i % 2 == 0) for i in range(8, n - 8)), "timing row")
            self.assertTrue(m[n - 8][8], "the dark module")

    def test_the_text_that_does_not_fit_is_refused(self):
        self.assertEqual(qrmini.MAX_BYTES[6], 106)
        with self.assertRaises(ValueError):
            qrmini.modules("a" * 107)

    @unittest.skipIf(zxingcpp is None or numpy is None, "no QR reader to check against")
    def test_a_reader_reads_what_was_written(self):
        random.seed(7)
        texts = ["http://192.168.0.101:8099", "x", "q" * 27] + [
            "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789:/.-") for _ in range(random.randint(1, 106)))
            for _ in range(60)]
        for t in texts:
            m = qrmini.modules(t)
            n, k, q = len(m), 6, 4
            img = numpy.full(((n + 2 * q) * k,) * 2, 255, numpy.uint8)
            for y, row in enumerate(m):
                for x, v in enumerate(row):
                    if v:
                        img[(y + q) * k:(y + q + 1) * k, (x + q) * k:(x + q + 1) * k] = 0
            got = zxingcpp.read_barcodes(img)
            self.assertTrue(got and got[0].text == t, t)


@unittest.skipIf(pygame is None, "no pygame")
class Screens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, lang="he", ip="192.168.0.101"):
        app = make_app(self.mod, lang=lang)
        app.ip = ip
        return app

    def test_the_network_card_opens_a_page_with_the_code(self):
        for lang in ("he", "en"):
            app = self.app(lang)
            cell = next(c for c in app.settings_cells()["net"] if c[0] == app.tr("c.name"))
            self.assertEqual(cell[1], "192.168.0.101:8099")
            self.assertIsNotNone(cell[3])
            cell[3]()
            self.assertEqual(app.view, "qr")
            app.draw()
            self.assertEqual(len(app.bar_items), 4)
            app.bar_items[3][2]()
            self.assertEqual(app.view, "settings")

    def test_with_no_network_the_card_does_nothing_and_the_page_says_so(self):
        app = self.app(ip="")
        cell = next(c for c in app.settings_cells()["net"] if c[0] == app.tr("c.name"))
        self.assertIsNone(cell[3])
        app.go("qr")
        app.draw()

    def test_the_end_of_the_setup_and_a_wifi_that_worked_draw_it(self):
        seen = []
        for lang in ("he", "en"):
            app = self.app(lang)
            orig = app.draw_qr
            app.draw_qr = lambda *a, **k: (seen.append(a[3]), orig(*a, **k))[1]
            app.online = lambda: True
            app.setup_step = "done"
            app.go("setup")
            app.draw()
            app.wf["result"] = {"ssid": "Home", "ip": "192.168.0.50"}
            app.wf["screen"] = "done"
            app.go("wifi")
            app.draw()
        self.assertEqual(seen.count("http://192.168.0.101:8099"), 2)
        self.assertEqual(seen.count("http://192.168.0.50:8099"), 2)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class Intro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def lines(self, opener):
        app = make_app(self.mod, lang="he")
        opener(app)
        shown = []
        orig = app.cv.text
        app.cv.text = lambda s, *a, **k: (shown.append(s), orig(s, *a, **k))[1]
        app.draw()
        return " | ".join(shown)

    def test_the_intros_differ(self):
        seen = {}
        seen["both"] = self.lines(lambda a: a.open_check("settings", "both"))
        seen["auto"] = self.lines(lambda a: a.open_check("settings", "auto"))
        seen["guided"] = self.lines(lambda a: a.open_check("settings", "guided"))
        app = make_app(self.mod, lang="he")
        for key, fn in app.quick_areas():
            if key != "bt":
                seen[key] = self.lines(lambda a, k=key: dict(a.quick_areas())[k]())
        self.assertEqual(len(set(seen.values())), len(seen), "every opening has its own text")
        self.assertIn("בדיקות אוטומטיות", seen["auto"])
        self.assertIn("בדיקת מגע", seen["touch"])


if __name__ == "__main__":
    unittest.main()
