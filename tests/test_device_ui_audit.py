"""The device screen after an audit: the setup's radio steps start on the current choice
and ask before a broker switch, the clock moved by hand is undone by Back, the diagnosis says when it
could not run, the broker's status comes from the screen's own MQTT connection, the phone's paste page
refuses bad lengths and locks out one address only, the update follows the installer, and the smaller
fixes of texts, threads, file reads and layout."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers)

pygame = T.pygame
TMP = T.TMP


def texts(app, view_fn=None):
    """Every line drawn on one frame: (text, x, y)."""
    from ui import canvas as C
    got, orig = [], C.Canvas.text

    def rec(cv, s, x, y, *a, **k):
        got.append((str(s), x, y))
        return orig(cv, s, x, y, *a, **k)
    with mock.patch.object(C.Canvas, "text", rec):
        app.draw()
    return got


@unittest.skipIf(pygame is None, "pygame is not installed")
class Setup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.app = T.make_app(self.mod)
        self.app.draw()

    def test_the_language_step_starts_on_the_current_language_and_ok_keeps_it(self):
        app = self.app
        app.go("settings")
        app.open_setup("lang")
        app.draw()
        self.assertEqual(app.setup_sel, 1, "English is the current choice")
        app.press("ok")
        self.assertEqual(app.lang, "en", "a ✓ on the current choice changes nothing")
        self.assertNotEqual(app.setup_step, "lang", "and goes on")

    def test_ok_on_an_external_broker_in_use_goes_on_with_no_switch(self):
        app, calls = self.app, []
        from ui import system
        env = {"MQTT_LOCAL": "no", "MQTT_HOST": "192.168.1.5"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(system, "request", side_effect=lambda a, *x, **k: (calls.append(a), (False, "no-root"))[1]):
            app.reset_broker()
            app.go("settings")
            app.open_setup("conn")
            app.draw()
            self.assertEqual(app.setup_sel, 1, "framed on the external broker, the one in use")
            app.press("ok")
            time.sleep(0.2)
            self.assertEqual(app.setup_step, "ssh", "an external broker goes on to the SSH step (armonico connect)")
            self.assertEqual(app.broker["type"], "external")
            self.assertEqual(calls, [])
            self.assertIsNone(app.confirm)

    def test_picking_the_local_broker_over_an_external_one_asks_first(self):
        app = self.app
        env = {"MQTT_LOCAL": "no", "MQTT_HOST": "192.168.1.5"}
        with mock.patch.dict(os.environ, env), mock.patch.object(app, "start_local_step") as start:
            app.reset_broker()
            app.open_setup("conn")
            app.draw()
            app.press("up")
            app.draw()
            app.press("ok")
            self.assertIsNotNone(app.confirm, "asked, as in the settings")
            self.assertEqual(app.confirm["text"], app.tr("c.toLocalQ"))
            self.assertEqual(app.setup_step, "conn")
            app.answer(False)
            self.assertEqual(app.broker["type"], "external", "Cancel changes nothing")
            app.draw()
            app.press("ok")
            app.answer(True)
            self.assertEqual(app.broker["type"], "local")
            self.assertEqual(app.setup_step, "local")
            start.assert_called_once()

    def test_applying_an_external_broker_from_the_setup_asks_first(self):
        app = self.app
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}), mock.patch.object(app, "switch_external") as sw:
            app.broker.update(type="external", host="10.0.0.2", state="ok")
            app.save(setup_manual=True)                 # "Enter on this screen" on the SSH step
            app.open_setup("broker")
            app.setup_go(1)
            sw.assert_not_called()
            self.assertEqual(app.confirm["text"], app.tr("c.applyQ"))
            app.answer(True)
            sw.assert_called_once()
            self.assertEqual(app.setup_step, "ha")

    def test_the_wifi_step_still_shows_in_the_setup(self):
        app = self.app
        app.setup_wifi = True
        with mock.patch.object(app, "open_wifi", create=True):
            app.open_setup("lang")
            app.setup_wifi = True
            app.draw()
            app.press("ok")
        self.assertEqual(app.setup_step, "wifi")
        app.draw()

    def test_back_from_the_time_step_undoes_the_clock_moved_by_hand(self):
        app = self.app
        with mock.patch.object(app, "online", return_value=False):
            app.open_setup("time")
            app.draw()
            for _ in range(2):
                app.setup_cards(0)[1][1][3]()          # one hour later, twice
            self.assertEqual(app.time_adj, 7200)
            app.setup_go(-1)
            self.assertEqual(app.time_adj, 0.0)
            app.go("settings")
            app.open_setup("time", single=True)          # Place › Time from the settings
            app.setup_cards(0)[1][1][3]()
            app.setup_go(-1)
            self.assertEqual(app.time_adj, 0.0)
            self.assertEqual(app.view, "settings")

    def test_finish_returns_where_the_setup_was_opened(self):
        app = self.app
        app.go("settings")
        app.open_setup("done")
        app.finish_setup()
        self.assertEqual(app.view, "settings")
        app.go("rest")
        app.open_setup("done", start=True)
        app.finish_setup()
        self.assertEqual(app.view, "rest")

    def test_two_screens_at_the_first_start_go_on_into_the_setup(self):
        app = self.app
        app.st["setup_done"] = False
        with mock.patch.object(app, "two_displays", return_value=True):
            app.found_displays = [{"name": "DSI-1", "mode": (800, 480)}, {"name": "HDMI-A-1", "mode": (1920, 1080)}]
            app.screen.open_on_display = None
            app.first_view()
        self.assertEqual(app.view, "displays")
        app.draw()
        app.pick_display(0)
        self.assertEqual(app.view, "setup", "a pick with no restart goes on into the setup")


@unittest.skipIf(pygame is None, "pygame is not installed")
class Screens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.app = T.make_app(self.mod)
        self.app.draw()

    def test_the_touch_keyboard_types_links_shares_and_passwords(self):
        from ui.views_settings import EROWS, SROWS
        keys = set("".join(EROWS + SROWS)) | set("".join(EROWS).upper()) | {" "}
        for s in ("https://example.com/armonico-backup.armonico", "//192.168.1.10/share", "P@ss:w0rd/!#$%^&*()"):
            self.assertLessEqual(set(s), keys, s)
        app = self.app
        app.edit_field("x", lambda: "", lambda v: None)
        app.sym = True
        app.draw()
        labels = {t for t, _, _ in texts(app)}
        self.assertTrue({":", "/"} <= labels, "the symbols layer shows : and /")

    def test_a_backup_password_is_kept_as_typed(self):
        app, got = self.app, []
        from ui import views_more
        app.bk = {"step": "net", "proto": "smb", "where": "net", "host": "", "port": "445", "user": "", "pass": "",
                  "test": "untested", "path": "/", "root": "/", "mode": "restore", "url": ""}
        with mock.patch.object(views_more, "backup", mock.Mock()):
            opts = app.bk_options()[1]
        pw = [o for o in opts if o[0] == app.tr("c.pass")][0]
        pw[2]()
        self.assertTrue(app.edit["keep"])
        app.edit["val"] = " a b "
        app.edit_done()
        self.assertEqual(app.bk["pass"], " a b ")

    def test_a_diagnosis_that_cannot_run_says_so(self):
        app = self.app
        fake = mock.Mock(run_checks=mock.Mock(side_effect=RuntimeError("diag broke")))
        with mock.patch.dict(sys.modules, {"diag": fake}):
            app.open_diag("settings")
            for _ in range(100):
                if not app.diag_busy:
                    break
                time.sleep(0.01)
            drawn = [t for t, _, _ in texts(app)]
        self.assertIn(app.tr("dg.cantRun"), drawn)
        self.assertIn(app.tr("dg.cantRunT"), drawn)
        self.assertIn("diag broke", " ".join(drawn))
        self.assertNotIn(app.tr("c.allFine"), drawn)

    def test_the_broker_status_follows_the_screens_own_connection(self):
        app = self.app
        with mock.patch.dict(os.environ, {"MQTT_LOCAL": "yes"}):
            app.broker.pop("used", None)
            app.reset_broker()
            self.assertEqual(app.broker["state"], "unknown", "not ✔ Connected before anything is known")
            self.assertEqual(app.broker_text(), app.tr("bt.unknown"))
            app.screen.mqtt = mock.Mock(is_connected=mock.Mock(return_value=False))
            with mock.patch.object(app, "t", return_value=2):
                app.follow_broker()
            self.assertEqual(app.broker["state"], "unknown", "the first seconds: still connecting")
            with mock.patch.object(app, "t", return_value=60):
                app.follow_broker()
            self.assertEqual(app.broker["state"], "fail")
            app.screen.mqtt.is_connected.return_value = True
            app.follow_broker()
            self.assertEqual(app.broker["state"], "ok")
            self.assertEqual(app.broker["used"][0], "ok")
        app.screen.mqtt = None

    def test_check_for_update_says_when_it_could_not_check(self):
        app = self.app
        fake = mock.Mock(update_available=mock.Mock(side_effect=OSError("down")), _update_memo={})
        with mock.patch.dict(sys.modules, {"diag": fake}), mock.patch.object(app, "online", return_value=True):
            app.check_update()
            time.sleep(0.2)
        self.assertEqual(app.screen.toast[0], app.tr("updCheckFail"))
        fake = mock.Mock(update_available=mock.Mock(return_value=None), _update_memo={"value": False})
        with mock.patch.dict(sys.modules, {"diag": fake}), mock.patch.object(app, "online", return_value=True):
            app.check_update()
            time.sleep(0.2)
        self.assertEqual(app.screen.toast[0], app.tr("updCheckFail"), "GitHub did not answer: not 'No new version'")

    def test_the_night_texts_say_what_happens(self):
        app = self.app
        app.st.update(night_when="sun")
        look = {c[0]: c for c in app.settings_cells()["look"]}
        self.assertIn(app.tr("nm.sun"), look[app.tr("c.night")][1])
        self.assertEqual(app.tr("c.nmLevel"), "Dimming" if app.lang == "en" else "הנמכה")

    def test_the_settings_read_their_files_a_few_times_not_every_frame(self):
        app = self.app
        app.open_page("net")
        app.draw()
        reads = []
        orig = Path.read_text

        def count(p, *a, **k):
            reads.append(p.name)
            return orig(p, *a, **k)
        load = getattr(self.mod, "load_settings", None)
        with mock.patch.object(Path, "read_text", count), \
                mock.patch.object(self.mod, "load_settings", side_effect=lambda: (reads.append("settings"), load())[1],
                                  create=True):
            for _ in range(30):
                app.draw()
        self.assertLessEqual(len(reads), 6, reads)

    def test_a_touched_key_starts_no_thread_and_is_let_go_from_the_loop(self):
        app = self.app
        app.go("free")
        app.draw()
        sent = []
        before = threading.active_count()
        with mock.patch.object(app.screen, "pub", create=True, side_effect=lambda t, p, **k: sent.append(p)):
            for pc in range(12):
                app.pointer_id = pc + 1                  # a finger on each key, each one still down
                app.free_key(pc)
            self.assertEqual(threading.active_count(), before, "no timer thread for each key")
            app.follow_touch_keys()
            self.assertFalse([p for p in sent if p.startswith("off")])
            later = time.monotonic() + app.TOUCH_KEY_MAX + 1
            with mock.patch.object(time, "monotonic", return_value=later):
                app.follow_touch_keys()
        self.assertTrue([p for p in sent if p.startswith("off")], "the key whose lift never came is let go")

    def test_hebrew_toasts_say_names_in_hebrew_or_as_the_list_shows_them(self):
        app = T.make_app(self.mod, lang="he")
        app.screen.pub = mock.Mock()
        app.songs_play()
        self.assertNotIn("_", app.screen.toast[0])
        self.assertIn("Ode to joy", app.screen.toast[0])
        app.screen.engine.state = {"mode": "wait", "midi": [60, 62], "expect": 0, "show": True, "total": 2, "pos": 0}
        app.draw()
        lab = [h for h in app.hits if 96 <= h[1] <= 132 and h[3] == 36]
        for h in lab:
            h[4]()
            self.assertNotIn("None", app.screen.toast[0])
        self.assertEqual(app.root_msg("bad address"), "הכתובת לא תקינה")
        self.assertEqual(app.root_msg("no-root"), app.tr("noRoot"))

    def test_the_import_walks_the_sticks_off_the_drawing_thread_and_stats_first(self):
        app = self.app
        where = []
        with mock.patch.object(app, "find_midi", side_effect=lambda: (where.append(threading.current_thread()), [])[1]):
            app.open_import()
            app.draw()
            for _ in range(100):
                if app.imp["files"] is not None:
                    break
                time.sleep(0.01)
        self.assertTrue(where and where[0] is not threading.main_thread())
        big = TMP / "big.mid"
        big.write_bytes(b"MThd" + b"\0" * 2_100_000)
        try:
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read whole")):
                app.import_one(big)
            self.assertEqual(app.imp["done"][str(big)], "bad")
        finally:
            big.unlink()

    def test_the_hardware_tests_header_is_clear_of_the_corner_rings(self):
        app = self.app
        app.open_test()
        drawn = texts(app)
        title = [d for d in drawn if d[0] == app.tr("test")][0]
        self.assertGreaterEqual(title[1], 30 + 18 + 4, "the title starts after the top left ring")
        hint = [d for d in drawn if app.tr("testHint")[:10] in d[0]][0]
        self.assertLessEqual(hint[1], 800 - 30 - 18 - 4, "the hint ends before the top right ring")

    def test_the_lesson_marks_the_current_part_and_the_next_note_far_into_a_song(self):
        app = self.app
        midi = [60 + (i % 12) for i in range(40)]
        midi[5] = None
        app.screen.engine.state = {"mode": "wait", "midi": midi, "expect": 31, "show": True, "total": 30, "pos": 20}
        from ui import canvas as C
        rects, circles = [], []
        o_rect, o_circle = C.Canvas.rect, C.Canvas.circle
        with mock.patch.object(C.Canvas, "rect", lambda cv, *a, **k: (rects.append((a, k)), o_rect(cv, *a, **k))[1]), \
                mock.patch.object(C.Canvas, "circle", lambda cv, *a, **k: (circles.append(a), o_circle(cv, *a, **k))[1]):
            app.draw()
        P = app.P
        dots = [a for a, k in rects if len(a) >= 5 and a[1] == 66 and a[3] == 8 and a[2] == 30]
        self.assertTrue(any(a[4] == P["gold"] for a in dots), "the current part has its gold dot")
        self.assertTrue(any(len(a) >= 4 and a[2] == 6 and a[3] == P["gold"] for a in circles), "the next note's gold dot")

    def test_the_black_keys_show_their_labels(self):
        app = self.app
        app.screen.engine.state = {"mode": "wait", "midi": [61, 62], "expect": 0, "show": True, "total": 2, "pos": 0,
                                   "device_labels": "num",
                                   "device_key_labels": ["1", "1#", "2", "2#", "3", "4", "4#", "5", "5#", "6", "6#", "7"]}
        drawn = [t for t, _, _ in texts(app)]
        for lab in ("1#", "2#", "4#", "5#", "6#"):
            self.assertIn(lab, drawn)

    def test_a_kept_city_without_a_name_draws(self):
        app = self.app
        app.st["city"] = {"lat": 51.5, "lon": -0.12, "tz": "Europe/London"}
        self.assertEqual(app.city["name"], "London")
        for page in ("top", "place"):
            app.open_page(page)
            app.draw()
        app.open_place("settings")
        app.draw()
        app.open_setup("place")
        app.draw()
        app.st["city"] = {"lat": 51.5, "tz": "Europe/London"}
        self.assertEqual(app.city["name"], "Tel Aviv", "no longitude: the default city")

    def test_a_lost_release_does_not_open_the_credits(self):
        app = self.app
        app.open_about()
        app.draw()
        old = time.monotonic() - 60
        app.down.update(menu=old, ok=old)
        with mock.patch.object(app, "open_credits") as credits:
            app.follow_buttons()
        credits.assert_not_called()
        self.assertEqual(app.down, {})

    def test_the_direct_links_english_subtitle_has_a_short_form(self):
        from ui.texts import short_forms
        app = self.app
        self.assertTrue(short_forms("en", app.tr("bk.urlD")))

    def test_the_network_screen_has_no_down_and_up_that_do_nothing(self):
        app = self.app
        app.open_net("rest")
        app.draw()
        for ic, label, fn in app.bar_items:
            if ic in ("▼", "▲"):
                self.assertTrue(fn or not label, (ic, label))

    def test_down_and_up_move_through_the_city_results(self):
        app = self.app
        app.open_place("settings")
        app.cities.results = [{"name": n, "country": "X", "lat": 1, "lon": 2, "tz": "UTC"} for n in "ABC"]
        app.draw()
        app.press("down")
        app.draw()
        with mock.patch.object(app, "place_pick") as pick:
            app.press("ok")
        self.assertEqual(pick.call_args[0][0]["name"], "B")

    def test_the_telegram_test_after_a_restart_says_why(self):
        app = self.app
        app.st.update(tg_done=True, ha_done=True, tg_chats=[1])
        app.tg["token"] = ""
        app.tg_test()
        self.assertEqual(app.screen.toast[0], app.tr("tg.tokenAgain"))

    def test_the_telegram_setup_after_a_restart_asks_for_the_ha_token_first(self):
        app = self.app
        app.go("settings")
        app.tg["token"] = "123:abc"
        app.ha.update(token="", token_mem="")
        self.assertIsNone(app.run_tg_setup())
        self.assertEqual(app.view, "edit")
        self.assertEqual(app.edit["note"], app.tr("tg.haAgain"))
        app.close_paste()

    def test_later_on_the_update_returns_where_it_was_opened(self):
        app = self.app
        app.update = ("9.9", "- new")
        app.go("settings")
        app.open_upd("settings")
        app.draw()
        app.press("menu")
        self.assertEqual(app.view, "settings")

    def test_the_sleep_screens_temperature_keeps_its_degree_after_the_number_in_hebrew(self):
        from ui.sleep import SleepScreen, Weather
        app = T.make_app(self.mod, lang="he", settings=dict(sleep=1))
        app.sleep_screen = SleepScreen()
        w = app.sleep_screen.weather = Weather(None, fetch=mock.Mock(side_effect=OSError("no network")))
        w.data = {"lat": round(app.city["lat"], 2), "lon": round(app.city["lon"], 2), "temp": 18.4, "code": 2,
                  "at": time.time()}
        app.last_input = time.monotonic() - 120
        drawn = [t for t, _, _ in texts(app)]
        self.assertIn("18°", drawn, "the number and its degree drawn as one piece, in this order")
        self.assertFalse([t for t in drawn if "°" in t and t != "18°"], "no degree sign alone or in a Hebrew text")


class Phone(unittest.TestCase):
    def post(self, port, body, length, src="127.0.0.1"):
        c = socket.socket()
        c.bind((src, 0))
        c.connect(("127.0.0.1", port))
        c.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: " + length + b"\r\n\r\n" + body)
        c.shutdown(socket.SHUT_WR)
        out = b""
        c.settimeout(3)
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    break
                out += d
        except OSError:
            pass
        c.close()
        return out

    def test_a_negative_length_is_refused(self):
        import sys
        dev = str(T.ROOT / "device")
        if dev not in sys.path:                          # alone, nothing has put the device folder on the path yet
            sys.path.insert(0, dev)
        from ui.phone import PasteServer
        got = []
        s = PasteServer("x", got.append, port=18197)
        self.assertTrue(s.start())
        try:
            body = b"code=" + s.code.encode() + b"&value=" + b"A" * 50000
            out = self.post(18197, body, b"-1")
            self.assertIn(b"411", out.split(b"\r\n")[0])
            self.assertEqual(got, [])
            self.post(18197, body, b"99999")
            self.assertEqual(got, [])
        finally:
            s.stop()

    def test_wrong_codes_lock_out_that_address_only(self):
        from ui.phone import PasteServer
        got = []
        s = PasteServer("x", got.append, port=18198)
        self.assertTrue(s.start())
        try:
            wrong = "0000" if s.code != "0000" else "1111"
            for _ in range(5):
                body = b"code=" + wrong.encode() + b"&value=x"
                self.post(18198, body, str(len(body)).encode())
            self.assertIsNotNone(s.srv, "the server keeps running")
            body = b"code=" + s.code.encode() + b"&value=tok"
            self.post(18198, body, str(len(body)).encode())
            self.assertEqual(got, [], "the address that guessed is refused")
            try:
                self.post(18198, body, str(len(body)).encode(), src="127.0.0.2")
            except OSError:
                self.skipTest("no second loopback address here")
            self.assertEqual(got, ["tok"], "another address still pastes")
        finally:
            s.stop()


@unittest.skipIf(pygame is None, "pygame is not installed")
class Update(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def setUp(self):
        self.app = T.make_app(self.mod)
        from ui.widgets import Job
        self.job = Job("u", ["a", "b", "c", "d"], "rest")
        self.status = TMP / "update-status.json"

    def tearDown(self):
        for f in ("update-status.json", "update-request"):
            (TMP / f).unlink(missing_ok=True)

    def test_a_failed_installer_is_shown_at_once(self):
        asked = time.time()
        self.status.write_text(json.dumps({"state": "failed", "message": "The installer stopped", "time": asked + 1}))
        t0 = time.monotonic()
        self.app.follow_update(self.job, asked, limit=5, every=0.01)
        self.assertLess(time.monotonic() - t0, 1)
        self.assertTrue(self.job.done and not self.job.ok)
        self.assertIn("The installer stopped", self.job.error)

    def test_an_old_status_is_not_taken_and_the_limit_ends_the_wait(self):
        asked = time.time()
        self.status.write_text(json.dumps({"state": "failed", "message": "old", "time": asked - 3600}))
        self.app.follow_update(self.job, asked, limit=0.2, every=0.01)
        self.assertEqual(self.job.error, self.app.tr("up.stuck"))

    def test_a_request_nobody_takes_is_said(self):
        (TMP / "update-request").write_text("9.9\n")
        self.app.follow_update(self.job, time.time(), limit=5, req_wait=0.1, every=0.01)
        self.assertEqual(self.job.error, self.app.tr("up.noUnit"))

    def test_done_moves_to_the_last_step(self):
        asked = time.time()
        self.status.write_text(json.dumps({"state": "done", "message": "Updated", "time": asked + 1}))
        self.app.follow_update(self.job, asked, limit=5, every=0.01)
        self.assertEqual(self.job.state[3], 1)
        self.assertFalse(self.job.done and not self.job.ok)

    def test_a_silent_home_assistant_setup_still_times_out(self):
        from ui import system
        d = Path(tempfile.mkdtemp())
        (d / "ha_setup.py").write_text("import time\ntime.sleep(30)\n")
        with mock.patch.object(system, "SETUP_DIR", d), mock.patch.object(system, "main_python", return_value=sys.executable):
            t0 = time.monotonic()
            code, lines = system.ha_run("check", "http://x", "t", timeout=0.5)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertNotEqual(code, 0)
        self.assertIn(("fail", "timeout"), lines)


if __name__ == "__main__":
    unittest.main()
