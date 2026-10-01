"""The screens of the Armonico Pi device (device/ui): every view draws in both languages and at
two sizes, and the buttons, touches and settings do what the approved demo does."""
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["DATA_DIR"] = str(TMP)

try:
    import pygame
    pygame.display.init()
    pygame.font.init()
except Exception:                                   # pragma: no cover - no pygame here
    pygame = None


def load_screen():
    spec = importlib.util.spec_from_file_location("armonico_screen_ui_test", ROOT / "device" / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def font_dir():
    """Heebo from the lesson screen, made into TrueType by armonico_pi.py prepare on the device;
    here any TrueType Heebo found, or pygame's own font."""
    for d in (TMP / "fonts", ROOT / "lessons" / "fonts"):
        if any(d.glob("Heebo-*.ttf")):
            return d
    return TMP / "fonts"


class Media:
    status, size = "", (800, 480)

    def surface(self):
        return None

    def lyric(self):
        return ""

    stop = tick = set_volume = start = lambda self, *a: None
    playing = None


def make_app(mod, size=(800, 480), lang="en", settings=None):
    pygame.display.set_mode(size)
    sc = object.__new__(mod.Screen)
    sc.pg = pygame
    sc.surf = pygame.display.get_surface()
    sc.settings = dict(setup_done=True, lang=lang, theme="classic", **(settings or {}))
    sc.engine = mock.Mock(state={"mode": "idle", "volume": 70}, ok=True, why="", over_why="",
                          overview={"stats": {"streak": 4, "week_minutes": 35},
                                    "songs": [{"name": "ode_to_joy", "total": 8, "learned": 5, "last": time.time()},
                                              {"name": "fur_elise", "total": 12, "learned": 0}],
                                    "profiles": {"active": "main", "list": [{"id": "main", "name": "", "locked": False}]}})
    sc.media = Media()
    sc.view, sc.sel_song, sc.song_first, sc.sel_cell = "rest", 0, 0, 0
    sc.octave, sc.pedal = 4, False
    sc.last_live, sc.trail, sc.flash, sc.seen_volume = 0, [], {}, None
    sc.hop, sc.notes, sc.alarm, sc.song, sc.key_hold, sc.was_busy = 0.0, [], False, False, False, False
    sc.media_try, sc.bar_until, sc.shift_msg, sc.toast = 0.0, 0.0, ("", 0.0), ("", 0.0)
    sc.summary_id, sc.summary_until, sc.ghost_id, sc.confirm = None, 0, mod.Screen.UNSEEN, None
    sc.last_tap_at, sc.last_tap_pos = 0.0, None
    sc.lesson_request_pending, sc.lesson_request_at, sc.lesson_request_song = False, 0.0, ""
    sc.lesson_request_accepted = False
    sc.events, sc.ev_lock, sc.mqtt, sc.ip, sc.ip_at = [], threading.Lock(), None, "", 0.0
    saved = {}
    sys.path.insert(0, str(ROOT / "device"))
    from ui.app import App
    app = App(sc, pygame, lambda **kw: saved.update(kw), font_dir(), background=False)
    app.saved = saved
    app.ip = "192.168.10.25"
    sc.ui = app
    return app


@unittest.skipIf(pygame is None, "pygame is not installed")
class Views(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def views(self, app):
        app.found_displays = [{"name": "DSI-1", "connected": True, "mode": (800, 480)},
                              {"name": "HDMI-A-1", "connected": True, "mode": (1920, 1080)}]
        yield "rest", lambda: app.go("rest")
        yield "songs", lambda: app.go("songs")
        yield "free", lambda: app.go("free")
        yield "test", app.open_test
        for page in ("top", "look", "sound", "play", "place", "conn", "ha", "tg", "net", "bkp", "sys", "alarm", "kbd", "less"):
            yield "settings:" + page, lambda p=page: app.open_page(p)
        yield "settings:voice", lambda: (app.open_page("play"), setattr(app, "voice_adjust", True))
        yield "test:usb", lambda: (app.open_test(), app.screen.test["usb"].update(name="Digital Keyboard", note=61, n=3))
        yield "imp", app.open_import
        yield "edit", lambda: app.edit_field("Address", lambda: "1.2.3.4", lambda v: None)
        yield "place", lambda: app.open_place("settings")
        yield "net", lambda: app.open_net("rest")
        yield "about", app.open_about
        yield "credits", app.open_credits
        yield "displays", lambda: app.go("displays")
        yield "upd", lambda: (setattr(app, "update", ("9.9", "- new")), app.go("upd"))
        yield "diag", lambda: (setattr(app, "diag_res", []), app.go("diag"))
        for step in ("lang", "place", "time", "sound", "conn", "look", "done"):
            yield "setup:" + step, lambda s=step: app.open_setup(s)
        yield "lesson", lambda: app.screen.engine.state.update(mode="wait", midi=[60, 62, 64], expect=1, show=True, total=3, pos=1)

    def test_every_view_draws_in_both_languages_and_sizes(self):
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                app = make_app(self.mod, size, lang)
                for name, open_it in self.views(app):
                    app.screen.engine.state = {"mode": "idle", "volume": 70}
                    open_it()
                    with self.subTest(size=size, lang=lang, view=name):
                        app.draw()
                        self.assertTrue(app.bar_items, "every view has its buttons")

    def test_one_canvas_at_the_native_resolution(self):
        app = make_app(self.mod, (1920, 1080))
        self.assertEqual(app.cv.surf.get_size(), (1920, 1080))
        self.assertAlmostEqual(app.cv.scale, 2.25)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Settings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def setUp(self):
        self.app = make_app(self.mod)
        self.app.open_page("top")
        self.app.draw()

    def test_eight_groups_and_the_links_behind_the_network(self):
        cells = self.app.settings_cells()
        self.assertEqual(len(cells["top"]), 8)
        self.assertEqual(len(cells["net"]), 8)
        self.assertIn(self.app.tr("g.conn"), [c[0] for c in cells["net"]])
        conn = [c[0] for c in cells["conn"]]               # Home Assistant and Telegram behind the broker's card
        self.assertEqual(conn[-2:], [self.app.tr("g.ha"), self.app.tr("g.tg")])
        self.app.open_page("conn")
        self.app.settings_cells()["conn"][-2][3]()           # Home Assistant
        self.assertEqual(self.app.page, "ha")
        self.app.draw()
        self.app.press("menu")                               # back to the broker's page, on that cell
        self.assertEqual((self.app.page, self.app.sel), ("conn", len(conn) - 2))
        self.app.press("menu")
        self.assertEqual((self.app.page, self.app.sel), ("net", 5))

    def test_info_cells_are_skipped_and_ok_carries_the_framed_name(self):
        app = self.app
        app.open_page("net")
        app.draw()
        cells = app.settings_cells()["net"]
        acts = [i for i, c in enumerate(cells) if c[3]]
        self.assertIsNone(cells[0][3], "the link only informs")
        self.assertEqual(app.sel, acts[0])
        ok = [b for b in app.bar_items if b[0] == "✓"][0]
        self.assertEqual(ok[1], cells[acts[0]][0])
        app.press("up")
        app.draw()
        self.assertEqual(app.sel, acts[-1], "up from the first cell that acts goes round to the last one")

    def test_the_lesson_screen_code_shows_on_the_network_page_and_in_the_setup(self):
        app = self.app
        (TMP / "screen_code").write_text("482913\n")
        app._code_seen = (-1e9, "")                  # the files are read again every 3 s: at once here
        try:
            with mock.patch.dict(os.environ, {"SCREEN_CODE": "on"}):
                app.open_page("net")
                cell = [c for c in app.settings_cells()["net"] if c[0] == app.tr("c.code")][0]
                self.assertEqual(cell[1], "482913")
                self.assertIsNotNone(cell[3], "a new code can be drawn from it")
                app.open_setup("done")
                with mock.patch.object(app, "online", return_value=True):
                    intro = app.setup_cards(0)[0]
                self.assertIn("482913", intro)
                (TMP / "code_enabled").write_text("off\n")
                app._code_seen = (-1e9, "")
                cell = [c for c in app.settings_cells()["net"] if c[0] == app.tr("c.code")][0]
                self.assertEqual(cell[1], app.tr("c.codeOff"))
        finally:
            for f in ("screen_code", "code_enabled"):
                (TMP / f).unlink(missing_ok=True)

    def test_a_broker_chosen_but_not_applied_is_not_kept(self):
        app = self.app
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            app.reset_broker()
            app.open_page("conn")
            app.settings_cells()["conn"][0][3]()          # local -> external, only chosen
            self.assertEqual(app.broker["type"], "external")
            net = app.settings_cells()["net"]
            self.assertTrue(net[5][1].startswith(app.tr("local")), "the network page shows the broker in use")
            with mock.patch.object(app, "ask") as ask:
                app.settings_cells()["conn"][0][3]()      # and back: no question, no switch to run
                ask.assert_not_called()
            self.assertEqual(app.broker["type"], "local")
            app.settings_cells()["conn"][0][3]()
            app.draw()
            app.press("menu")                             # left without Apply
            self.assertEqual(app.page, "net")
            self.assertEqual(app.broker["type"], "local")

    def test_leaving_the_connection_page_keeps_a_real_failed_status(self):
        app = self.app
        from ui import views_settings
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "no", "MQTT_HOST": "10.0.0.9"}):
            app.reset_broker()
            app.open_page("conn")
            done = threading.Event()

            def slow(*a):
                done.wait(2)
                return ("fail", 0)
            with mock.patch.object(views_settings.system, "mqtt_test", return_value=("fail", 0)):
                app.test_broker()
                for _ in range(100):
                    if app.broker["state"] != "testing":
                        break
                    time.sleep(0.02)
            app.reset_broker()
            self.assertEqual(app.broker["state"], "fail", "the tested status of the broker in use stays")
            # a test still running for a choice that was dropped does not write its result
            app.broker.update(type="external", host="10.9.9.9", dirty=True)
            with mock.patch.object(views_settings.system, "mqtt_test", side_effect=slow):
                app.test_broker()
                app.reset_broker()
                done.set()
                time.sleep(0.2)
            self.assertEqual(app.broker["state"], "fail")
            self.assertEqual(app.broker["host"], "10.0.0.9")

    def test_a_touched_key_is_one_note_in_free_play(self):
        app = self.app
        app.go("free")
        sc = app.screen
        with mock.patch.object(sc, "pub", create=True):
            app.free_key(7)
            app.follow_keys_ui()
            sc.engine.state = {"mode": "idle", "volume": 70, "live": [[1, 67]]}   # the engine's copy of it
            sc.follow_keys()
            app.follow_keys_ui()
            self.assertEqual(len(app.free_notes), 1, app.free_notes)
            sc.engine.state = {"mode": "idle", "volume": 70, "live": [[1, 67], [2, 67]]}   # a real key after it
            time.sleep(0.01)
            sc.follow_keys()
            app.follow_keys_ui()
            self.assertEqual(len(app.free_notes), 2, app.free_notes)
            app.release_touch_key(getattr(app, "pointer_id", "mouse"))

    def test_every_about_page_fits_its_card(self):
        # view_about draws a line only while its baseline is above the card's foot (y + h - 24):
        # a longer page lost its last lines with no sign
        from ui.texts import ABOUT
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            for title, paras in ABOUT[lang]:
                ly, last = 56, 0
                for p in paras:
                    for _ in app.cv.wrap(p.format(ip="192.168.100.200", port="8099"), 800 - 48 - 40, 15):
                        last = ly
                        ly += 21
                    ly += 7
                with self.subTest(lang=lang, page=title):
                    self.assertLess(last, 232 - 24)

    def test_free_play_names_keys_as_the_lessons_do(self):
        app = self.app
        app.screen.engine.state = {"mode": "idle", "volume": 70, "device_labels": "do",
                                   "device_key_labels": ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]}
        self.assertEqual(app.free_label(7), "Sol")
        app.screen.engine.state["device_labels"] = "none"
        self.assertEqual(app.free_label(7), "G4")
        app.screen.engine.state = {"mode": "idle"}             # the engine not answered yet
        self.assertEqual(app.free_label(7), "G4")

    def test_brightness_follows_day_night_and_sleep(self):
        app = self.app
        night = mock.Mock(sun=mock.Mock(altitude=-10.0))
        day = mock.Mock(sun=mock.Mock(altitude=30.0))
        app.st.update(brightness=75, night_when="sun", night_level=3)
        app.sky_st = day
        self.assertEqual(app.light_level(), 75)
        app.sky_st = night
        self.assertEqual(app.light_level(), 25, "strength 3: 25% brightness")
        app.st["night_when"] = "off"
        self.assertEqual(app.light_level(), 75, "night mode off: the day level all night")
        app.st.update(night_when="sun", night_level=1)
        self.assertEqual(app.light_level(), 75, "never brighter than the day level")

    def test_the_backlight_is_set_once_per_change_or_drawn_darker_without_one(self):
        from ui.light import Light
        root = TMP / "backlight"
        (root / "10-0045").mkdir(parents=True, exist_ok=True)
        (root / "10-0045" / "max_brightness").write_text("255\n")
        (root / "10-0045" / "brightness").write_text("255\n")
        calls = []
        lt = Light(lambda *a, **k: (calls.append(k["pct"]) or (True, "")), root=str(root))
        self.assertTrue(lt.has)
        self.assertAlmostEqual(lt.want(40), 0.6, msg="a level in between is drawn")
        for _ in range(50):
            if not lt.busy:
                break
            time.sleep(0.01)
        lt.want(25)
        time.sleep(0.05)
        self.assertEqual(calls, [100], "the backlight only fully on: a value in between turned the V2.1 dark")
        lt.want(0)
        for _ in range(50):
            if not lt.busy:
                break
            time.sleep(0.01)
        self.assertEqual(calls, [100, 0], "off only for the night's sleep")
        none = Light(lambda *a, **k: (True, ""), root=str(TMP / "no-backlight"))
        self.assertFalse(none.has)
        self.assertAlmostEqual(none.want(25), 0.75)
        self.assertEqual(none.want(100), 0.0)

    def test_the_rest_screen_moves_a_pixel_only_when_it_never_sleeps(self):
        app = self.app
        app.go("rest")
        app.st["sleep"] = 5
        app.follow_shift()
        self.assertEqual(app.shift, (0, 0))
        app.st["sleep"] = 0
        with mock.patch("time.time", return_value=180 * 3):
            app.follow_shift()
        self.assertNotEqual(app.shift, (0, 0))
        app.draw()
        app.go("settings")
        app.follow_shift()
        self.assertEqual(app.shift, (0, 0))

    def test_the_keyboards_shift_is_off_until_pressed_and_after_a_letter(self):
        # the keyboard's ⇧ once shared its name with the move against retention, a (0, 0) that is
        # always true: every letter came out a capital and ⇧ stayed lit, whatever was pressed
        app = self.app
        got = []
        app.edit_field("Password", lambda: "", got.append)
        app.draw()
        self.assertFalse(app.caps)
        app.edit_key("a")
        app.caps = True
        app.draw()
        app.edit_key("A")
        app.draw()
        self.assertFalse(app.caps, "one capital, then back to small letters")
        self.assertEqual(app.edit["val"], "aA")

    def test_the_alarm_rings_once_at_its_minute_on_its_days(self):
        import datetime as dt
        app = self.app
        sc = app.screen
        app.save_alarm(on=True, time="07:00", days="sun-thu", song="ode_to_joy", by="device")
        app.st.update(app.saved)
        sunday_7 = dt.datetime(2026, 10, 25, 7, 0)                 # a Sunday
        with mock.patch.object(sc, "pub", create=True) as pub, mock.patch.object(app, "local", return_value=sunday_7), \
             mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            app._alarm_seen = 0
            app.follow_alarm()
            pub.assert_called_once_with("piano/alarm/set", "ode_to_joy|Armonico Pi alarm")
            app.st.update(app.saved)
            app._alarm_seen = 0
            app.follow_alarm()
            self.assertEqual(pub.call_count, 1, "once a day")
        friday = dt.datetime(2026, 10, 30, 7, 0)
        app.st.pop("alarm_last", None)
        with mock.patch.object(sc, "pub", create=True) as pub, mock.patch.object(app, "local", return_value=friday), \
             mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            app._alarm_seen = 0
            app.follow_alarm()
            pub.assert_not_called()
        # Home Assistant chosen: on an external broker the device stays quiet, on the local one it rings
        app.save_alarm(by="ha")
        app.st.update(app.saved)
        app.st.pop("alarm_last", None)
        with mock.patch.object(sc, "pub", create=True) as pub, mock.patch.object(app, "local", return_value=sunday_7):
            with mock.patch.dict(os.environ, {"MQTT_LOCAL": "no"}):
                app._alarm_seen = 0
                app.follow_alarm()
                pub.assert_not_called()
            with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
                app._alarm_seen = 0
                app.follow_alarm()
                self.assertEqual(pub.call_count, 1)

    def test_a_recording_ends_when_free_play_is_left(self):
        app = self.app
        app.go("free")
        app.rec_start()
        app.rec_note("60 1.0")
        app.go("rest")
        self.assertFalse(app.rec["on"])
        app.rec_note("62 2.0")
        self.assertEqual(app.rec["notes"], [(0.0, 60)], "kept, and nothing added after leaving")

    def test_the_alarm_moved_later_rings_again_and_a_skipped_hour_rings_late(self):
        import datetime as dt
        app = self.app
        sc = app.screen
        app.save_alarm(on=True, time="07:00", days="all", song="ode_to_joy", by="device")
        app.st.update(app.saved)
        with mock.patch.object(sc, "pub", create=True) as pub, mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            for t in (dt.datetime(2026, 10, 25, 7, 0),):
                with mock.patch.object(app, "local", return_value=t):
                    app._alarm_seen = 0
                    app.follow_alarm()
            app.st.update(app.saved)
            app.save_alarm(time="07:20")                     # a snooze by hand
            app.st.update(app.saved)
            for t in (dt.datetime(2026, 10, 25, 7, 10), dt.datetime(2026, 10, 25, 7, 20)):
                with mock.patch.object(app, "local", return_value=t):
                    app._alarm_seen = 0
                    app.follow_alarm()
            self.assertEqual(pub.call_count, 2)
            # 02:30 on the night the clock jumps from 02:00 to 03:00: seen at 01:59, rings at 03:00
            app.save_alarm(time="02:30")
            app.st.update(app.saved)
            for t in (dt.datetime(2026, 3, 27, 1, 59), dt.datetime(2026, 3, 27, 3, 0)):
                with mock.patch.object(app, "local", return_value=t):
                    app._alarm_seen = 0
                    app.follow_alarm()
            self.assertEqual(pub.call_count, 3)
            # a song removed since: the alarm takes one that exists instead of ringing nothing
            app.save_alarm(song="gone_song")
            app.st.update(app.saved)
            self.assertEqual(app.alarm()["song"], "ode_to_joy")

    def test_the_alarm_time_moves_by_5_minutes_around_midnight(self):
        app = self.app
        app.save_alarm(time="23:55")
        app.st.update(app.saved)
        app.alarm_move(5)
        app.st.update(app.saved)
        self.assertEqual(app.alarm()["time"], "00:00")
        app.alarm_move(-5)
        app.st.update(app.saved)
        self.assertEqual(app.alarm()["time"], "23:55")

    def test_songs_come_in_from_a_usb_stick(self):
        app = self.app
        stick = TMP / "stick"
        (stick / "music").mkdir(parents=True, exist_ok=True)
        (stick / "music" / "Bella Ciao.mid").write_bytes(b"MThd" + b"\0" * 20)
        (stick / "notes.txt").write_text("x")
        from ui import views_more
        fake = mock.Mock()
        fake.Local.sticks.return_value = [str(stick)]
        with mock.patch.object(views_more, "backup", fake):
            files = app.find_midi()
        self.assertEqual([f.name for f in files], ["Bella Ciao.mid"])
        app.screen.engine.upload = mock.Mock()
        app.import_one(files[0])
        name, blob = app.screen.engine.upload.call_args[0][:2]
        self.assertEqual(name, "Bella Ciao.mid")
        self.assertTrue(blob.startswith(b"MThd"))
        app.screen.engine.upload.reset_mock()
        app.import_one(TMP / "stick" / "music" / "ode_to_joy.mid")       # already in the songs: not sent
        app.screen.engine.upload.assert_not_called()

    def test_free_play_records_and_plays_back_in_order(self):
        app = self.app
        app.go("free")
        app.rec_start()
        for note, at in ((60, 10.0), (64, 10.5), (67, 11.0)):
            app.rec_note(f"{note} {at}")
        app.rec_stop()
        self.assertEqual(app.rec["notes"], [(0.0, 60), (0.5, 64), (1.0, 67)])
        app.rec["notes"] = [(0.0, 60), (0.02, 64)]
        sent = []
        with mock.patch.object(app.screen, "pub", create=True, side_effect=lambda t, p: sent.append(p)):
            app.rec_play()
            for _ in range(300):
                if not app.rec["playing"]:
                    break
                time.sleep(0.01)
        self.assertEqual([p for p in sent if p.startswith("on")], ["on 60", "on 64"])
        self.assertEqual(sorted(p for p in sent if p.startswith("off")), ["off 60", "off 64"])

    def test_a_finger_sliding_over_the_keys_plays_each_key_it_reaches(self):
        app = self.app
        app.go("free")
        app.draw()
        sent = []
        whites = [r for r in app.key_rects if r[3] == 78]       # the white keys (78 wide)
        with mock.patch.object(app.screen, "pub", create=True, side_effect=lambda t, p, **k: sent.append(p)), \
             mock.patch.object(app.cv, "to_plan", side_effect=lambda x, y: (x, y)):
            app.pointer_id = 1
            k0, k1 = whites[0], whites[1]
            app.pointer_down(k0[1] + 10, k0[2] + 150)
            app.pointer_move(k0[1] + 30, k0[2] + 150)          # the same key: nothing new
            app.pointer_move(k1[1] + 10, k1[2] + 150)          # the next key
            app.pointer_move(k1[1] + 10, 20)                    # off the keys
            app.pointer_up(k1[1] + 10, 20)
            app.pointer_move(k0[1] + 10, k0[2] + 150)          # lifted: a move plays nothing
        notes = [p for p in sent if p.startswith(("on", "off"))]
        n0, n1 = 12 * (app.screen.octave + 1) + k0[0], 12 * (app.screen.octave + 1) + k1[0]
        self.assertEqual(notes, [f"on {n0}", f"off {n0}", f"on {n1}", f"off {n1}"])

    def test_play_with_turns_the_keys_or_the_touch_off(self):
        app = self.app
        app.go("free")
        sent = []
        with mock.patch.object(app.screen, "pub", create=True, side_effect=lambda t, p, **k: sent.append((t, p, k))):
            app.st["play"] = "touch"
            app.follow_input()
            self.assertIn(("piano/device/keys/set", "off", {"retain": True}), sent)
            app.go("test")
            app.follow_input()
            self.assertEqual(sent[-1][1], "on", "the hardware test always hears the keys")
            app.go("free")
            app.st["play"] = "off"
            app.follow_input()
            sent.clear()
            app.free_key(4)
            self.assertFalse([x for x in sent if x[0] == "piano/device/note"], "touch is off")

    def test_the_keyboard_page_lists_usb_keyboards_and_sends_the_choice(self):
        app = self.app
        app.mqtt_event("piano/device/keyboards", '["Yamaha PSR-E363", "Arturia MiniLab"]')
        self.assertEqual(app.keyboards, ["Yamaha PSR-E363", "Arturia MiniLab"])
        app.st["play_input"] = "touch"                      # from before: every key silent, as on the device
        self.assertEqual(app.play_mode(), "touch")
        cells = app.keyboard_cells()
        self.assertEqual(cells[0][1], app.tr("pm.touch"), "one choice says what plays")
        self.assertEqual([c[1] for c in cells[1:3]], ["Yamaha PSR-E363", "Arturia MiniLab"])
        cells[2][3]()                                        # choose the second: plays from USB now
        app.st.update(app.saved)
        self.assertEqual(app.keyboard_choice(), {"mode": "external", "name": "Arturia MiniLab", "out": "both"})
        self.assertEqual(app.play_input(), "both", "choosing a keyboard turns the keys back on")
        sent = []
        with mock.patch.object(app.screen, "pub", create=True, side_effect=lambda t, p, **k: sent.append((t, p))):
            app.follow_input()
        kb = [json.loads(p) for t, p in sent if t == "piano/device/keyboard/set"]
        self.assertEqual(kb, [{"mode": "external", "name": "Arturia MiniLab", "out": "both"}])
        cells = app.keyboard_cells()
        self.assertEqual(cells[-1][1], app.tr("ko.both"))
        cells[-1][3]()                                       # the sound: next, the device alone
        app.st.update(app.saved)
        self.assertEqual(app.keyboard_choice()["out"], "device")
        app.st["keyboard"] = {"name": "x", "sound": False}   # saved before the choice: its own sound only
        self.assertEqual(app.keyboard_choice()["out"], "keyboard")
        app.mqtt_event("piano/device/keyboards", "[]")
        self.assertEqual(app.keyboard_cells()[1][3], None, "none connected: a line to read")

    def test_home_assistant_and_telegram_are_off_on_the_local_broker(self):
        app = self.app
        app.st.update(ha_done=True, tg_done=True)
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            conn = app.settings_cells()["conn"]
            self.assertEqual(conn[-2][1], app.tr("offLocal"))
            self.assertEqual(conn[-1][1], app.tr("offLocal"))
            self.assertEqual(app.settings_cells()["tg"][0][0], app.tr("c.needExt"))
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "no"}):
            self.assertEqual(app.settings_cells()["conn"][-2][1], app.tr("setUp"))

    def test_a_failed_external_switch_keeps_apply_and_the_setup_on_the_broker_step(self):
        app = self.app
        from ui import views_settings
        app.broker.update(type="external", host="10.0.0.9", dirty=True)
        with mock.patch.object(views_settings.system, "request", return_value=(False, "refused")):
            job = app.switch_external(back="setup")
            for _ in range(100):
                if job.done:
                    break
                time.sleep(0.02)
        self.assertFalse(job.ok)
        self.assertTrue(app.broker["dirty"], "Apply stays, to try again")
        self.assertEqual(app.setup_step, "broker")
        self.assertEqual(app.saved.get("setup_step"), "broker")

    def test_the_local_step_comes_back_to_itself_after_the_switch(self):
        app = self.app
        from ui import views_settings
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "no"}), \
             mock.patch.object(views_settings.system, "request", return_value=(True, "")):
            app.broker["type"] = "local"
            app.setup_job = None
            app.start_local_step()
        self.assertEqual(app.saved.get("setup_step"), "local", "not Look: the test runs on this step")
        for _ in range(100):
            if app.setup_job.done:
                break
            time.sleep(0.02)
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "no"}):
            app.setup_step = "local"
            self.assertFalse(app.setup_cards(0)[2], "no Continue in the moment before the restart")
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}), \
             mock.patch("ui.system.mqtt_test", return_value=("fail", 0)), mock.patch("time.sleep"):
            app.setup_job = None
            app.setup_step = "local"
            app.start_local_step()
            for _ in range(100):
                if app.setup_job.done:
                    break
                time.sleep(0.02)
            self.assertFalse(app.setup_cards(0)[2], "no Continue while the test failed")
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}), \
             mock.patch("ui.system.mqtt_test", return_value=("ok", 12)):
            app.setup_job = None
            app.start_local_step()
            for _ in range(100):
                if app.setup_job.done:
                    break
                time.sleep(0.02)
            self.assertTrue(app.setup_cards(0)[2])

    def test_no_fixed_text_is_cut_in_a_button_or_a_card(self):
        from ui.texts import T
        fixed = {v for pair in T.values() if isinstance(pair, tuple) for v in pair}
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang)
            app.short_missed = set()
            app.st.update(ha_done=True)
            for name, open_it in Views.views(None, app):
                app.screen.engine.state = {"mode": "idle", "volume": 70}
                open_it()
                app.draw()
                if app.view == "settings":
                    for typ in ("local", "external"):
                        app.broker["type"] = typ
                        cells = app.settings_cells()[app.page]
                        for i, c in enumerate(cells):
                            if c[3]:
                                app.sel = i
                                app.draw()
            cut = {m[1] for m in app.short_missed if m[1] in fixed}
            self.assertEqual(cut, set(), f"{lang}: give these a shorter wording in texts.SHORT")

    def test_theme_changes_and_is_saved(self):
        app = self.app
        app.open_page("look")
        app.sel = 1                                       # Look and screen: Language, then Theme
        app.draw()
        app.press("ok")
        self.assertEqual(app.saved.get("theme"), "classicLight")

    def test_hebrew_touch_is_mirrored(self):
        app = make_app(self.mod, lang="he")
        app.open_page("look")
        app.draw()
        # the second cell (Theme) is on the left half in English, on the right... of row 1: x 406..776
        px, py = (800 - 406 - 100), 80                    # mirrored into the left half in Hebrew
        app.pointer_down(*[v * app.cv.scale for v in (px, py)])
        app.pointer_up(*[v * app.cv.scale for v in (px, py)])      # the page scrolls (9 cards): a touch presses when it lifts
        self.assertIn("theme", app.saved)

    def test_language_switch_mirrors_the_whole_screen(self):
        app = self.app
        app.save(lang="he")
        app.draw()
        self.assertTrue(app.cv.rtl)


@unittest.skipIf(pygame is None, "pygame is not installed")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def setUp(self):
        self.app = make_app(self.mod)
        self.app.draw()

    def test_confirm_owns_the_buttons(self):
        app, done = self.app, []
        app.ask("Sure?", lambda: done.append(1))
        app.draw()
        app.button("down", False)
        self.assertIsNotNone(app.confirm)
        app.button("ok", True)                            # the hold of the press that asked
        self.assertEqual(done, [])
        app.button("ok", False)
        self.assertEqual(done, [1])
        app.ask("Sure?", lambda: done.append(2))
        app.button("menu", False)
        self.assertIsNone(app.confirm)
        self.assertEqual(done, [1])

    def test_confirm_times_out_doing_nothing(self):
        app, done = self.app, []
        app.ask("Sure?", lambda: done.append(1))
        app.confirm["until"] = time.time() - 1
        app.draw()
        self.assertIsNone(app.confirm)
        self.assertEqual(done, [])

    def test_power_button_asks_first(self):
        app = self.app
        with mock.patch.object(app, "root_power") as power:
            app.mqtt_event("piano/device/power", "press")
            power.assert_not_called()
            app.button("ok", False)
            power.assert_called_once_with("shutdown")

    def test_menu_names_the_next_screen(self):
        app = self.app
        menu = [b for b in app.bar_items if b[0] == "☰"][0]
        self.assertEqual(menu[1], app.tr("songs"))
        app.press("menu")
        self.assertEqual(app.view, "songs")

    def test_sleep_wakes_on_the_first_touch_only(self):
        app = self.app
        app.st["sleep"] = 1
        app.last_input = time.monotonic() - 120
        self.assertTrue(app.asleep())
        app.draw()
        app.pointer_down(10, 10)
        self.assertFalse(app.asleep())
        self.assertEqual(app.view, "rest")

    def test_octave_buttons_move_the_keys(self):
        app = self.app
        app.screen.pub = mock.Mock()
        app.press("up")
        app.screen.pub.assert_called_with("piano/device/octave/set", "+1")
        self.assertEqual(app.screen.octave, 5)

    def test_keep_practicing_starts_the_last_song(self):
        app = self.app
        app.card_cache = (time.time() + 99, {"name": "ode_to_joy", "states": [2, 1, 0]})
        app.screen.start_lesson = mock.Mock()
        app.press("ok")
        app.screen.start_lesson.assert_called_once_with("ode_to_joy")

    def test_about_swipe_direction_follows_the_language(self):
        app = self.app
        app.open_about()
        app.draw()
        s = app.cv.scale
        app.pointer_down(500 * s, 300 * s)
        app.pointer_up(400 * s, 300 * s)                   # to the left: next page in English
        self.assertEqual(app.about_page, 1)
        app.save(lang="he")
        app.draw()
        app.last_tap = (0.0, None)
        app.pointer_down(300 * s, 300 * s)
        app.pointer_up(400 * s, 300 * s)                   # to the right: next page in Hebrew
        self.assertEqual(app.about_page, 2)

    def test_menu_and_ok_held_together_roll_the_credits(self):
        app = self.app
        app.open_about()
        app.draw()
        app.button("menu", False)
        app.button("ok", False)
        self.assertEqual(app.view, "about")                # nothing acts at the press
        app.down = {"menu": time.monotonic() - 2, "ok": time.monotonic() - 2}
        app.follow_buttons()
        self.assertEqual(app.view, "credits")
        app.draw()
        app.button("down", False)
        self.assertEqual(app.view, "about")

    def test_a_single_button_on_about_acts_at_release(self):
        app = self.app
        app.open_about()
        app.draw()
        app.button("ok", False)
        self.assertEqual(app.view, "about")
        app.button_up("ok")
        self.assertEqual(app.view, "settings")

    def test_seven_taps_on_the_version(self):
        app = self.app
        for _ in range(7):
            app.ver_tap()
        self.assertGreater(app.char.cool_until, app.t())
        self.assertNotIn("😎", app.screen.toast[0])

    def test_owl_once_a_night_at_three(self):
        app = self.app
        three = app.local().replace(hour=3, minute=10)
        with mock.patch.object(app, "local", return_value=three), mock.patch.object(app.char, "start_owl") as owl:
            app.screen.flash[0] = time.time()
            app.follow_keys_ui()
            app.screen.flash[2] = time.time() + 1
            app.follow_keys_ui()
            owl.assert_called_once()

    def test_setup_steps_follow_the_broker(self):
        app = self.app
        app.broker["type"] = "external"
        self.assertIn("ha", app.setup_steps())
        app.open_setup("conn")
        app.draw()
        self.assertEqual(app.setup_sel, 1, "framed on the current choice, the external broker")
        app.press("up")                                    # to the local broker
        app.draw()
        with mock.patch.object(app, "start_local_step"), mock.patch.dict(os.environ, {"MQTT_HOST": ""}):
            app.press("ok")                                # the framed card: local broker, picked and moved on
        self.assertEqual(app.broker["type"], "local")
        self.assertEqual(app.setup_step, "local")
        self.assertNotIn("ha", app.setup_steps())

    def test_setup_back_on_the_first_step_leaves(self):
        app = self.app
        app.go("settings")
        app.open_setup("lang")
        app.draw()
        app.press("menu")
        self.assertEqual(app.view, "settings")

    def test_usb_keyboard_types_into_the_city_search(self):
        app = self.app
        app.open_place("settings")
        for ch in "Lon":
            app.type_text(ch)
        self.assertEqual(app.query, "Lon")
        app.type_key(pygame.K_BACKSPACE)
        self.assertEqual(app.query, "Lo")

    def test_edit_screen_takes_typing_and_saves(self):
        app, got = self.app, []
        app.go("settings")
        app.edit_field("User", lambda: "", got.append)
        app.type_text("piano")
        app.type_key(pygame.K_RETURN)
        self.assertEqual(got, ["piano"])
        self.assertEqual(app.view, "settings")

    def test_hebrew_toasts_from_the_screen(self):
        app = self.app
        app.save(lang="he")
        app.screen.say("Starting lesson: ode to joy", 3)
        self.assertTrue(app.screen.toast[0].startswith("התחלת שיעור"))


class Texts(unittest.TestCase):
    def test_every_key_used_has_both_languages(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import texts, texts_b
        known = {**texts.T, **texts_b.TEXTS}    # texts_b is merged in by the screens that use it, when they load
        used = set()
        for f in (ROOT / "device" / "ui").glob("*.py"):
            used |= set(re.findall(r"""\btr\(\s*["']([A-Za-z][\w.]*)["']""", f.read_text()))
        missing = sorted(k for k in used if k not in known and not k.endswith("."))   # "th." + a name: prefixes
        self.assertEqual(missing, [])
        for k, pair in texts.T.items():
            self.assertEqual(len(pair), 2, k)

    def test_no_emoji_in_device_text(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import texts
        for k, (en, he) in texts.T.items():
            for s in (en, he):
                self.assertFalse(any(ord(c) > 0x1F000 for c in s), k)


class PhonePaste(unittest.TestCase):
    def test_only_the_code_shown_on_the_screen_is_taken(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui.phone import PasteServer
        got = []
        srv = PasteServer("Token", got.append, port=0)
        srv.port = 0
        self.assertTrue(srv.start())
        port = srv.srv.server_address[1]

        def post(code):
            data = f"code={code}&value=abc+def".encode()
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", data=data, timeout=3).read()
            except Exception:
                pass
        post("0000" if srv.code != "0000" else "1111")
        self.assertEqual(got, [])
        post(srv.code)
        self.assertEqual(got, ["abcdef"])
        srv.stop()


class RootActions(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("armonico_root_actions_test", ROOT / "device" / "root_actions.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_unknown_actions_are_refused(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.multiple(self.mod, REQUEST=tmp / "device-request", STATUS=tmp / "device-request.status"):
            (tmp / "device-request").write_text(json.dumps({"id": "abc123", "action": "rm -rf", "args": {}}))
            self.mod.main()
            st = json.loads((tmp / "device-request.status").read_text())
        self.assertEqual(st["state"], "fail")
        self.assertFalse((tmp / "device-request").exists())

    def test_values_are_checked(self):
        with self.assertRaises(ValueError):
            self.mod.do_set_time(lambda *a: None, {"value": "now; reboot"})
        with self.assertRaises(ValueError):
            self.mod.do_usb_format(lambda *a: None, {"disk": "/dev/mmcblk0", "fs": "fat; reboot"})
        self.assertNotIn("sound-output", self.mod.ACTIONS)   # the sound goes through PipeWire, as the user
        with self.assertRaises(ValueError):
            self.mod.do_set_timezone(lambda *a: None, {"tz": "../../etc/passwd"})
        with self.assertRaises(ValueError):
            self.mod.plain("pa'ss")

    def test_the_request_file_is_written_for_the_root_unit(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import system
        tmp = Path(tempfile.mkdtemp())
        # a lock of its own: a request an earlier test left waiting in the background holds the shared one
        with mock.patch.multiple(system, REQUEST=tmp / "device-request", STATUS=tmp / "device-request.status",
                                 _ONE_AT_A_TIME=threading.Lock()):
            ok, _ = system.request("reboot", wait=False)
            body = json.loads((tmp / "device-request").read_text())
        self.assertTrue(ok)
        self.assertEqual(body["action"], "reboot")
        self.assertEqual(oct((tmp / "device-request").stat().st_mode & 0o777), "0o600")


class UsbKeyboard(unittest.TestCase):
    def test_a_usb_keyboard_plays_and_goes_on_unless_the_keys_are_off(self):
        spec = importlib.util.spec_from_file_location("armonico_pi_kb", ROOT / "device" / "armonico_pi.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        d = object.__new__(mod.Device)
        d.sampler, d.port = mock.Mock(), mock.Mock()
        d.port.here, d.port.there = True, False
        d.octave, d.set_octave = 4, mock.Mock()
        d.keys_on, d.kb = True, {"mode": "external", "name": "x", "out": "both"}
        d.external(bytes([0x90, 60, 100]))
        d.sampler.midi.assert_called_once()
        d.port.send.assert_called_once_with([0x90, 60, 100])
        d.external(bytes([0xF8]))                            # clock: stays with the keyboard
        self.assertEqual(d.port.send.call_count, 1)
        d.keys_on = False
        d.external(bytes([0x90, 62, 100]))                   # Play with: touch only
        self.assertEqual(d.port.send.call_count, 1)
        d.external(bytes([0x80, 60, 0]))                     # a note let go still goes, so none hangs
        self.assertEqual(d.port.send.call_count, 2)
        d.set_octave.assert_not_called()                     # middle C is octave 4
        d.keys_on, d.port.here = True, False
        d.sampler.reset_mock()
        d.external(bytes([0x90, 84, 100]))                   # its own sound only, two octaves up
        d.sampler.midi.assert_not_called()
        d.set_octave.assert_called_once_with(6)
        d.port.to_keyboard.assert_not_called()               # it plays its own keys itself
        # the keys are silent while a USB keyboard plays alone
        d.held, d.alarm, d.song, d.octave = {}, False, False, 4
        with mock.patch.object(mod, "load_settings", return_value={}):
            d.key(0, True)
        self.assertEqual(d.held, {})
        # a touch goes to the keyboard's sound, not to these speakers, when the keyboard's alone was chosen
        d.port.there = True
        d.touch_note(60, True)
        d.port.to_keyboard.assert_called_once_with([0x90, 60, mod.VELOCITY])
        d.sampler.note_on.assert_not_called()
        d.port.send.assert_called_with([0x90, 60, mod.VELOCITY])

    def test_songs_and_lessons_sound_where_chosen(self):
        spec = importlib.util.spec_from_file_location("armonico_pi_out", ROOT / "device" / "armonico_pi.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p = object.__new__(mod.Port)
        p.sampler, p.to_keyboard = mock.Mock(), mock.Mock()
        p.here, p.there = True, True
        p.play(bytes([0x91, 67, 90]))
        p.sampler.midi.assert_called_once()
        p.to_keyboard.assert_called_once()
        p.here = False
        p.play(bytes([0x81, 67, 0]))
        self.assertEqual(p.sampler.midi.call_count, 1)


class Brightness(unittest.TestCase):
    def test_the_root_action_sets_each_backlight_at_its_own_scale(self):
        spec = importlib.util.spec_from_file_location("armonico_root_bright", ROOT / "device" / "root_actions.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        root = TMP / "bl-root"
        for name, top in (("a", "255"), ("b", "31")):
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "max_brightness").write_text(top + "\n")
            (root / name / "brightness").write_text(top + "\n")
        with mock.patch.dict(os.environ, {"ARMONICO_BACKLIGHT": str(root)}):
            mod.do_set_brightness(lambda *a: None, {"pct": 10})
            self.assertEqual((root / "a" / "brightness").read_text().strip(), "26")
            self.assertEqual((root / "b" / "brightness").read_text().strip(), "3")
            mod.do_set_brightness(lambda *a: None, {"pct": 1})
            self.assertEqual((root / "b" / "brightness").read_text().strip(), "1", "above 0 never rounds to off")
            with self.assertRaises(ValueError):
                mod.do_set_brightness(lambda *a: None, {"pct": 150})
        self.assertIn("set-brightness", mod.ACTIONS)


class Installer(unittest.TestCase):
    def test_the_request_unit_is_wired_only_for_the_device(self):
        text = (ROOT / "install.sh").read_text()
        self.assertIn("system/device-request.path", text)
        self.assertIn('"$APP-device-request.path"', text)
        finish = text[text.index("device_finish() {"):text.index("device_note() {")]
        self.assertIn("$APP-device-request.path", finish)

    def test_armonico_pi_reports_the_power_key_and_releases(self):
        src = (ROOT / "device" / "armonico_pi.py").read_text()
        self.assertIn('"piano/device/power"', src)
        self.assertIn('"piano/device/button/up"', src)


if __name__ == "__main__":
    unittest.main()


class LabwcConfig(unittest.TestCase):
    def test_rc_xml_parses_with_both_window_rules(self):
        """A duplicate attribute in rc.xml made labwc show "config errors detected" and ignore every
        rule, so both windows opened on the HDMI screen."""
        import xml.dom.minidom
        mod = load_screen()
        folder = mod.write_labwc_config(tempfile.mkdtemp(), "DSI-1", "HDMI-A-1")
        doc = xml.dom.minidom.parse(str(Path(folder) / "rc.xml"))
        moves = {a.getAttribute("output") for a in doc.getElementsByTagName("action")
                 if a.getAttribute("name") == "MoveToOutput"}
        self.assertEqual(moves, {"DSI-1", "HDMI-A-1"})


@unittest.skipIf(pygame is None, "pygame is not installed")
class SoundAtStart(unittest.TestCase):
    def run_due(self, app):
        """follow_jobs runs the output in a thread: run it here and wait for it."""
        with mock.patch("threading.Thread") as th:
            app.sound_due = time.time() - 1
            app.follow_jobs()
            th.call_args.kwargs["target"]()

    @unittest.skip("HDMI off (DSI only): HDMI is not offered on the device")
    def test_hdmi_kept_but_no_hdmi_screen_plays_on_aux_with_no_message(self):
        from ui import audio
        app = make_app(load_screen(), settings={"sound_out": "hdmi"})
        app.sound_by_hand = False
        with mock.patch.object(audio, "hdmi_connected", return_value=False), \
             mock.patch.object(audio, "apply", return_value=(True, "")) as ap, \
             mock.patch.object(app, "fail_toast") as toast:
            self.run_due(app)
        ap.assert_called_once_with("aux")
        toast.assert_not_called()
        self.assertEqual(app.output, "hdmi")             # the choice stays for when the screen is back

    @unittest.skip("HDMI off (DSI only): HDMI is not offered on the device")
    def test_hdmi_chosen_by_hand_with_no_hdmi_screen_says_so(self):
        from ui import audio
        app = make_app(load_screen(), settings={"sound_out": "aux"})
        with mock.patch.object(audio, "hdmi_connected", return_value=False), \
             mock.patch.object(audio, "apply", return_value=(True, "")) as ap, \
             mock.patch.object(app, "fail_toast") as toast:
            app.set_sound("hdmi")
            self.run_due(app)
        ap.assert_called_once_with("aux")
        toast.assert_called_once_with("snd.none")


@unittest.skipIf(pygame is None, "pygame is not installed")
class MirrorMouse(unittest.TestCase):
    def test_a_click_on_the_hdmi_copy_lands_on_the_same_spot_of_the_screen(self):
        mod = load_screen()
        app = make_app(mod, (800, 480))
        win = object()
        mirror = mock.Mock(win=mock.Mock(size=(1920, 1080)))
        mirror.owns = lambda e: getattr(e, "window", None) is win
        with mock.patch.object(mod, "MIRROR", mirror), \
             mock.patch.object(app, "pointer_down") as down:
            app.event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(960, 540), window=win))
            app.event(pygame.event.Event(pygame.MOUSEMOTION, pos=(10, 10), rel=(1, 1), buttons=(0, 0, 0), window=win))
        down.assert_called_once_with(400, 240)
        self.assertTrue(app.mouse_at, "the pointer shows over the copy too")

    def test_the_second_screen_is_drawn_at_its_own_size_and_clicks_map_back(self):
        mod = load_screen()
        app = make_app(mod, (800, 480))
        app.go("songs")
        app.draw()
        mirror = mock.Mock(win=mock.Mock(size=(1920, 1080)))
        hits = list(app.hits)
        app.mirror_frame(mirror)
        shown = mirror.present.call_args.args[0]
        self.assertEqual(shown.get_size(), (1920, 1080))
        self.assertEqual(app.cv.surf.get_size(), (800, 480), "the device's own canvas is back")
        self.assertEqual(len(app.hits), len(hits))
        # the middle of the 24" is the middle of the 5"
        self.assertEqual(app.mirror_pos(mirror, (960, 540)), (400, 240))


@unittest.skipIf(pygame is None, "pygame is not installed")
class TouchKeyHold(unittest.TestCase):
    def test_a_touched_key_sounds_until_the_finger_lifts(self):
        app = make_app(load_screen())
        app.screen.pub = mock.Mock()
        app.screen.octave = 4
        app.pointer_id = 7
        app.free_key(0)
        time.sleep(0.5)                                   # longer than the old fixed 0.35 s
        self.assertEqual([c.args for c in app.screen.pub.call_args_list], [("piano/device/note", "on 60")])
        app.swipe = None
        app.pointer_up(10, 10)
        self.assertEqual(app.screen.pub.call_args_list[-1].args, ("piano/device/note", "off 60"))

    def test_two_fingers_hold_two_keys_apart(self):
        app = make_app(load_screen())
        app.screen.pub = mock.Mock()
        app.screen.octave = 4
        app.pointer_id = 1
        app.free_key(0)
        app.pointer_id = 2
        app.free_key(4)
        app.swipe = None
        app.pointer_up(10, 10)                            # finger 2 lifts
        self.assertEqual(app.screen.pub.call_args_list[-1].args, ("piano/device/note", "off 64"))
        self.assertIn(1, app._touch_held)


@unittest.skipIf(pygame is None, "pygame is not installed")
class ReviewFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_menu_works_over_the_picture_before_a_touch(self):
        for lang in ("en", "he"):
            app = make_app(self.mod, lang=lang, settings={"rest": "nasa"})
            app.apod.image = pygame.Surface((64, 48))
            app.bar_until = 0
            app.go("rest")
            app.draw()
            self.assertEqual([b[0] for b in app.bar_items], ["☰"], "only ☰ acts while the buttons are hidden")
            octave = app.screen.octave
            for name in ("down", "up", "ok"):
                app.press(name)
                self.assertEqual(app.view, "rest", name)
            self.assertEqual(app.screen.octave, octave)
            app.press("menu")
            self.assertEqual(app.view, "songs")

    def test_choose_a_profile_has_a_title(self):
        for lang, title in (("en", "Choose a profile"), ("he", "בחירת פרופיל")):
            app = make_app(self.mod, lang=lang)
            app.go("settings")
            app.open_page("profpick")
            app.draw()
            self.assertEqual(app.page_trail("profpick")[-1], title)
            self.assertNotIn("g.", app.screen_path(""))

    def test_the_local_step_checks_again_with_ok_after_a_failure(self):
        from ui.widgets import Job
        app = make_app(self.mod)
        app.broker["type"] = "local"
        app.open_setup("local")
        job = Job("x", ["a"], "setup")
        job.fail("refused")
        app.setup_job = job
        with mock.patch.object(type(app), "start_local_step") as again:
            app.draw()
            ok = [b for b in app.bar_items if b[0] == "✓"][0]
            self.assertEqual(ok[1], app.tr("dg.again"))
            self.assertIsNotNone(ok[2])
            app.press("ok")
            again.assert_called()

    def test_no_songs_leaves_the_keys_that_do_nothing_blank(self):
        app = make_app(self.mod)
        app.screen.engine.overview["songs"] = []
        app.go("songs")
        app.draw()
        labels = {b[0]: (b[1], b[2]) for b in app.bar_items}
        for ic in ("▼", "▲"):
            self.assertEqual(labels[ic][0], app.tr("down" if ic == "▼" else "up"), ic)   # Search online or Songs from USB
        self.assertEqual(labels["✓"], (app.tr("sn.find"), app.open_songnet))   # Search online comes first here
        app.press("down")
        app.draw()
        labels = {b[0]: (b[1], b[2]) for b in app.bar_items}
        self.assertEqual(labels["✓"], (app.tr("c.open"), app.open_import))
        self.assertTrue(labels["☰"][1])
