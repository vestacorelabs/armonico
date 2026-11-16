"""The high C is 8, the song archives (zips of movements, Hebrew words, a file that is no lesson is
not kept), a MIDI file with a few bytes after its last track is not damaged, and the
saving of a screen recording shows how far it is."""
import io
import sys
import tempfile
import time
import unittest
import unittest.mock
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "lessons"))
import powerloss                      # noqa: E402
from ui import audio, recorder        # noqa: E402
from ui.widgets import key_name       # noqa: E402
try:
    import piano_game as game         # noqa: E402
except ImportError:                   # mido missing
    game = None

LABELS = ["1", "1#", "2", "2#", "3", "4", "4#", "5", "5#", "6", "6#", "7"]
MIDI = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
        b"MTrk\x00\x00\x00\x0c\x00\x90\x3c\x40\x60\x80\x3c\x40\x00\xff\x2f\x00")


class Grid(unittest.TestCase):
    def test_three_columns_become_two_at_140_percent(self):
        from ui.widgets import Widgets
        w = Widgets.__new__(Widgets)
        w.screen_inch = 7.0
        w.zoom_pct = lambda: 100
        self.assertEqual(w.grid_geom()[0], 3)
        w.zoom_pct = lambda: 150
        self.assertEqual(w.grid_geom()[0], 2)
        w.screen_inch = 5.0
        self.assertEqual(w.grid_geom()[0], 2)


class HighC(unittest.TestCase):
    def test_the_high_c_is_8_in_numbers_and_c_in_letters(self):
        self.assertEqual(key_name(LABELS, 12, "num"), "8")
        self.assertEqual(key_name(LABELS, 0, "num"), "1")
        self.assertEqual(key_name(list("CDEFGAB") + [""] * 5, 12, "abc"), "C")
        self.assertEqual(key_name(None, 12, "num"), "")

    @unittest.skipIf(game is None, "no mido")
    def test_the_browser_keyboard_on_the_device_numbers_the_high_c_8(self):
        self.assertIn("DEVMODE ? (k === 7 ? 8 : k % 7 + 1)", game.PAGE)


class DamagedMidi(unittest.TestCase):
    def check(self, blob):
        with tempfile.NamedTemporaryFile(suffix=".mid") as f:
            f.write(blob)
            f.flush()
            return powerloss.midi_ok(f.name)[0]

    def test_bytes_after_the_last_track_are_not_damage_and_a_cut_track_is(self):
        self.assertTrue(self.check(MIDI))
        self.assertTrue(self.check(MIDI + b"\x00" * 9))
        self.assertFalse(self.check(MIDI[:-3]))
        self.assertFalse(self.check(b"\x00" * len(MIDI)))


@unittest.skipIf(game is None, "no mido")
class Archives(unittest.TestCase):
    def test_hebrew_words_are_searched_by_their_english_names(self):
        self.assertEqual(game.search_words("חורף ויוואלדי"), "winter vivaldi")
        self.assertEqual(game.search_words("והחורף Bach"), "winter Bach")
        self.assertEqual(game.search_words("שלום"), "שלום")

    def test_a_search_leaves_out_hits_with_none_of_the_words(self):
        hits = [("Winter - Vivaldi", "u1", {}), ("Prelude in C - Bach", "u2", {}), ("Winter Sonata - Other", "u3", {}),
                ("Concerto Vivaldi", "u4", {})]
        with unittest.mock.patch.object(game, "SEARCHERS", (("Mutopia Project", lambda q: list(hits)),)), \
                unittest.mock.patch.object(game, "search_mutopia", return_value=[]):
            found, _, _ = game.search_midi("winter vivladi")
        names = [h[0] for h in found]
        self.assertNotIn("Prelude in C - Bach", names)
        self.assertEqual(names[0], "Winter - Vivaldi")
        self.assertEqual(len(names), 3)
        with unittest.mock.patch.object(game, "SEARCHERS", (("Mutopia Project", lambda q: list(hits)),)), \
                unittest.mock.patch.object(game, "search_mutopia", return_value=[]):
            found, _, _ = game.search_midi("zzzz")
        self.assertEqual(len(found), 4)                 # nothing matches at all: nothing is hidden

    def test_the_movements_of_a_zip_come_in_the_order_of_the_piece(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("winter-score-2.mid", MIDI + b"3")
            z.writestr("winter-score.mid", MIDI + b"1")
            z.writestr("winter-score-1.mid", MIDI + b"2")
            z.writestr("readme.txt", b"x")
        got = game.zip_movements(buf.getvalue())
        self.assertEqual([g[-1:] for g in got], [b"1", b"2", b"3"])

    def test_a_zip_is_saved_as_one_song_for_each_movement(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.mid", MIDI)
            z.writestr("a-1.mid", MIDI)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(game, "MIDI_DIR", Path(d)), \
                mock.patch.object(game, "_get", return_value=buf.getvalue()):
            saved = game.download_midi("https://www.mutopiaproject.org/ftp/X/a-mids.zip", "Piece - X", {"source": "t"})
            self.assertEqual([p.name for p in saved], ["Piece - X - 1.mid", "Piece - X - 2.mid"])
            self.assertTrue(all(p.with_suffix(".credit.json").exists() for p in saved))
            self.assertFalse(list(Path(d).glob("*.part")))

    def test_an_answer_larger_than_the_limit_is_refused_not_cut(self):
        class R:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"x" * n
        with mock.patch.object(game._OPENER, "open", return_value=R()):
            with self.assertRaises(ValueError):
                game._get("https://www.mutopiaproject.org/ftp/x.mid")


class BluetoothCodec(unittest.TestCase):
    def test_the_codec_and_a_call_profile_are_read_from_pipewire(self):
        import json
        dump = json.dumps([{"type": "PipeWire:Interface:Node", "id": 7, "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "bluez_output.AA_BB_CC_DD_EE_FF.1",
            "api.bluez5.codec": "sbc_xq", "api.bluez5.profile": "a2dp-sink"}}},
            {"type": "PipeWire:Interface:Node", "id": 8, "info": {"props": {
                "media.class": "Audio/Sink", "node.name": "bluez_output.11_22_33_44_55_66.1",
                "api.bluez5.codec": "cvsd", "api.bluez5.profile": "headset-head-unit"}}}])
        q = audio.bt_quality(audio.sinks(dump))
        self.assertEqual(q["bt:bluez_output.AA_BB_CC_DD_EE_FF.1"], ("SBC-XQ", False))
        self.assertEqual(q["bt:bluez_output.11_22_33_44_55_66.1"], ("CVSD", True))


class SavingProgress(unittest.TestCase):
    def test_the_picture_then_the_join(self):
        with tempfile.TemporaryDirectory() as d:
            r = recorder.Recorder(d)
            self.assertIsNone(r.save_progress())
            prog = Path(d) / "p"
            prog.write_text("frame=10\nprogress=continue\nframe=30\n")
            r.save = {"stage": "picture", "frames": 60, "progress": prog, "tmp": None, "expect": 0}
            self.assertEqual(r.save_progress(), (0.25, "picture"))
            tmp = Path(d) / "t"
            tmp.write_bytes(b"x" * 50)
            r.save = dict(r.save, stage="join", tmp=tmp, expect=100)
            frac, stage = r.save_progress()
            self.assertEqual(stage, "join")
            self.assertAlmostEqual(frac, 0.745)

    def test_a_microphone_cleaned_beside_the_encoder_is_not_cleaned_again_in_the_mux(self):
        raw = " ".join(recorder.mux_cmd("/x/v.mkv", 0.0, [("/x/d.mka", 0.0, "Armonico"), ("/x/m.mka", 0.0, "Microphone")], "/x/o"))
        done = " ".join(recorder.mux_cmd("/x/v.mkv", 0.0, [("/x/d.mka", 0.0, "Armonico"), ("/x/m.clean.mka", 0.0, "Microphone")], "/x/o"))
        self.assertEqual(raw.count("afftdn"), 1)
        self.assertEqual(done.count("afftdn"), 0)
        self.assertIn("afftdn", " ".join(recorder.clean_cmd("/x/m.mka", "/x/m.clean.mka")))

    def test_the_picture_command_writes_its_progress_only_when_asked(self):
        self.assertNotIn("-progress", recorder.video_cmd((800, 480), "/x/v.mkv"))
        cmd = recorder.video_cmd((800, 480), "/x/v.mkv", "/x/v.progress")
        self.assertEqual(cmd[-3:], ["-progress", "/x/v.progress", "/x/v.mkv"])



try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:                      # no pygame
    pygame = None


@unittest.skipIf(pygame is None, "no pygame")
class SongsOnTheDevice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_a_song_is_deleted_after_a_question_and_leaves_the_list(self):
        app = make_app(self.mod)
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None: posted.append((path, body, on_result))
        app.go("songs")
        app.draw()
        app.song_delete("fur_elise")
        self.assertEqual(posted[0][:2], ("/songs/delete", {"name": "fur_elise"}))
        posted[0][2](True, None)
        self.assertEqual([x["name"] for x in app.screen.songs()], ["ode_to_joy"])
        app.draw()

    def test_a_screen_change_the_player_did_not_make_stops_no_song(self):
        # a lesson that closed by itself after 2 minutes with no key stopped a song Home Assistant had started
        app = make_app(self.mod)
        app.screen.pub = mock.Mock()
        app.screen.song, app.screen.alarm = True, False
        app.last_input = time.monotonic() - 120
        app.go("rest")
        app.screen.pub.assert_not_called()
        app.woke()                                   # the player touches something: then it stops, as before
        app.go("settings")
        app.screen.pub.assert_any_call("piano/stop", "Armonico Pi: another screen")

    def test_no_song_goes_on_behind_another_screen_or_a_lesson(self):
        app = make_app(self.mod)
        app.screen.pub = mock.Mock()
        app.screen.song, app.screen.alarm = True, False
        app.go("rest")
        app.go("settings")
        app.screen.pub.assert_any_call("piano/stop", "Armonico Pi: another screen")
        app.screen.pub.reset_mock()
        app.screen.alarm = True                      # the alarm is never stopped by a screen
        app.go("free")
        app.screen.pub.assert_not_called()
        app.screen.alarm = False
        with mock.patch.object(app.screen, "lesson_on", return_value=True):
            app.follow_jobs()
            app.follow_jobs()
        self.assertEqual([c for c in app.screen.pub.call_args_list if c.args[0] == "piano/stop"],
                         [mock.call("piano/stop", "Armonico Pi: a lesson")])


class BuiltInSongDeleted(unittest.TestCase):
    def test_a_built_in_song_is_noted_when_deleted_and_the_installer_skips_it(self):
        src = (ROOT / "lessons" / "piano_game.py").read_text(encoding="utf-8")
        self.assertNotIn("if not name or name in SYSTEM_SONGS:", src)
        self.assertIn('DATA_DIR / "songs_removed.txt"', src)
        install = (ROOT / "install.sh").read_text()
        self.assertIn('grep -qxF "$(basename "$f")" "$VAR_DIR/songs_removed.txt"', install)

if __name__ == "__main__":
    unittest.main()


class BluetoothAfterBoot(unittest.TestCase):
    """The Pi's Bluetooth chip went down ("hci0: hardware error 0x00") when a headset that had just set up its sound
    was disconnected and connected again after the boot. Now the link is never dropped, and a device whose sound
    output PipeWire already has is not touched at all."""

    def test_the_root_action_never_drops_the_link(self):
        import inspect
        import root_actions
        src = inspect.getsource(root_actions.do_bt_reconnect)
        self.assertNotIn('"disconnect', src.split('"""')[-1])
        self.assertIn("bt_connect_step", src)

    @unittest.skipIf(pygame is None, "no pygame")
    def test_only_a_connected_device_with_no_output_is_asked_for_its_sound(self):
        app = make_app(load_screen())
        calls = []
        saved = [{"mac": "AA:BB:CC:DD:EE:FF", "connected": True}, {"mac": "11:22:33:44:55:66", "connected": True}]

        def bt(action, timeout=0, **kw):
            calls.append((action, kw.get("mac")))
            return True, {}
        app.bt_take = lambda data: app.bt.update(saved=saved)
        app.bt_job = lambda fn, queue=False: fn()
        app.radio_now = lambda kind: "on"
        with mock.patch("ui.views_bt.system.bt", side_effect=bt), mock.patch("ui.views_bt.time.monotonic", return_value=60):
            app.bt_boot_cycle(["aux", "bt:bluez_output.AA_BB_CC_DD_EE_FF.1"])
        self.assertEqual(calls, [("status", None), ("reconnect", "11:22:33:44:55:66")])

    @unittest.skipIf(pygame is None, "no pygame")
    def test_later_a_device_silent_at_two_looks_is_asked_and_not_forever(self):
        app = make_app(load_screen())
        calls = []
        saved = [{"mac": "11:22:33:44:55:66", "connected": True}]

        def bt(action, timeout=0, **kw):
            calls.append(action)
            return True, {}
        app.bt_take = lambda data: app.bt.update(saved=saved)
        app.bt_job = lambda fn, queue=False: fn()
        app.radio_now = lambda kind: "on"
        clock = [1000.0]
        radio = []
        app.toast = lambda *a, **k: None
        with mock.patch("ui.views_bt.system.bt", side_effect=bt), \
                mock.patch("ui.views_bt.system.radio_set", side_effect=lambda k, on: radio.append(on) or (True, "")), \
                mock.patch("ui.views_bt.audio.bt_a2dp", return_value=[]), \
                mock.patch("ui.views_bt.time.monotonic", side_effect=lambda: clock[0]):
            for _ in range(6):                       # 6 looks, 30 s apart, long after the boot
                app.bt_boot_cycle(["aux"])
                clock[0] += 31
            self.assertEqual(calls.count("reconnect"), 3, "asked from the second silent look, at most 3 times")
            self.assertEqual(calls[:3], ["status", "status", "reconnect"])
            self.assertEqual(radio, [False, True], "still silent after that: the radio off and on, once")
            for _ in range(40):                      # silent for another 20 minutes
                app.bt_boot_cycle(["aux"])
                clock[0] += 31
        self.assertEqual(radio.count(False), 2, "at most 2 restarts of the radio in an hour")


@unittest.skipIf(pygame is None, "no pygame")
class BluetoothPageStaysPut(unittest.TestCase):
    """The Bluetooth page jumped by itself: a look every 8 s that left a device out for a moment moved the rows, and
    the frame stayed on the same number instead of the same row."""

    def test_a_device_missing_from_one_look_stays_and_the_frame_stays_on_its_row(self):
        app = make_app(load_screen())
        app.radio_now = lambda kind: "on"
        app.bt_follow = lambda: None
        a = {"mac": "AA:BB:CC:DD:EE:01", "name": "Sony", "connected": True}
        c = {"mac": "AA:BB:CC:DD:EE:02", "name": "JBL", "connected": False}
        app.bt_take({"hw": True, "saved": [a, c]})
        app.go("bt")
        app.bt["sel"] = 1                            # the JBL row
        app.draw()
        self.assertEqual(app.bt["sel_ref"][1], ("saved", c["mac"]))
        app.bt_take({"hw": True, "saved": [c]})      # one late answer: the Sony left out
        self.assertEqual([d["mac"] for d in app.bt["saved"]], [c["mac"], a["mac"]])
        app.draw()
        self.assertEqual(app.bt["sel_ref"][1], ("saved", c["mac"]), "the frame follows its row")
        app.bt_take({"hw": True, "saved": [c]})      # gone a second time: gone
        app.draw()
        self.assertEqual([d["mac"] for d in app.bt["saved"]], [c["mac"]])
        self.assertEqual(app.bt["sel_ref"][1], ("saved", c["mac"]))


class BluetoothBackToA2dp(unittest.TestCase):
    """A Bluetooth device left on its headset profile, or with its profile off, plays nothing the mix can use: it is
    set back to its best A2DP profile. One already on A2DP is not touched."""

    CARDS = """Card #51
\tName: bluez_card.AA_BB_CC_DD_EE_FF
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\ta2dp-sink-sbc: High Fidelity Playback (A2DP Sink, codec SBC) (sinks: 1, sources: 0, priority: 18, available: yes)
\t\ta2dp-sink: High Fidelity Playback (A2DP Sink, codec AAC) (sinks: 1, sources: 0, priority: 19, available: yes)
\t\theadset-head-unit: Headset Head Unit (HSP/HFP) (sinks: 1, sources: 1, priority: 1, available: yes)
\tActive Profile: headset-head-unit
Card #52
\tName: bluez_card.11_22_33_44_55_66
\tProfiles:
\t\ta2dp-sink: High Fidelity Playback (A2DP Sink) (sinks: 1, sources: 0, priority: 16, available: yes)
\tActive Profile: a2dp-sink
"""

    def test_only_the_card_on_its_headset_profile_is_set_to_its_best_a2dp(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import audio
        runs = []
        with mock.patch.object(audio, "_run", side_effect=lambda *c, timeout=5: runs.append(c)):
            done = audio.bt_a2dp(self.CARDS)
        self.assertEqual(done, ["bluez_card.AA_BB_CC_DD_EE_FF"])
        self.assertEqual(runs, [("pactl", "set-card-profile", "bluez_card.AA_BB_CC_DD_EE_FF", "a2dp-sink")])

    def test_a_device_on_its_call_link_only_is_asked_for_its_music_link(self):
        # as pactl showed it on the device after WirePlumber restarted under connected headphones
        only_call = """Card #61
\tName: bluez_card.4D_73_FD_D9_ED_EB
\tProfiles:
\t\theadset-head-unit: Headset Head Unit (HSP/HFP, codec CVSD) (sinks: 1, sources: 1, priority: 2, available: yes)
\tActive Profile: headset-head-unit
"""
        sys.path.insert(0, str(ROOT / "device"))
        from ui import audio
        self.assertEqual(audio.bt_no_a2dp(only_call), ["4D:73:FD:D9:ED:EB"])
        self.assertEqual(audio.bt_no_a2dp(self.CARDS), [], "a music profile there: bt_a2dp sets it, no reconnect")

    def test_a_device_back_as_a_new_node_is_a_new_key_id(self):
        sys.path.insert(0, str(ROOT / "device"))
        from ui import audio
        one = [{"id": 70, "name": "bluez_output.AA_BB_CC_DD_EE_FF.1", "card": "", "desc": "Sony"}]
        two = [dict(one[0], id=91)]
        with mock.patch.object(audio, "is_bt", side_effect=lambda s: s["name"].startswith("bluez_output.")), \
                mock.patch.object(audio, "is_usb", return_value=False):
            a, b = audio.ext_ids(one), audio.ext_ids(two)
        key = audio.BT + "bluez_output.AA_BB_CC_DD_EE_FF.1"
        self.assertEqual((a[key], b[key]), (70, 91))


class WifiChipTakesBluetooth(unittest.TestCase):
    """dtoverlay=disable-wifi makes the firmware pull WL_ON low, and on boards where BT_ON follows it Bluetooth goes
    too (raspberrypi/firmware issue 1611). The full check says so instead of leaving Bluetooth silently gone."""

    OURS = ("# >>> Armonico: radio chips turned off in Settings > Network and links (written by Armonico)\n"
            "[all]\ndtoverlay=disable-wifi\n# <<< Armonico: radio chips\n")

    def setUp(self):
        import fullcheck
        self.fc = fullcheck

    def test_the_chips_off_are_read_from_the_block_and_from_a_line_by_hand(self):
        self.assertEqual(self.fc.chips_off(self.OURS), {"wifi"})
        self.assertEqual(self.fc.chips_off("dtoverlay=disable-bt  # by hand\n#dtoverlay=disable-wifi\n"), {"bluetooth"})

    def test_bluetooth_gone_with_the_wifi_chip_is_a_problem_with_what_to_do(self):
        [it] = self.fc.chip_items(self.OURS, hci=False)
        self.assertEqual(it.level, self.fc.PROBLEM)
        [ok] = self.fc.chip_items(self.OURS, hci=True)
        self.assertEqual(ok.level, self.fc.OK)

    def test_nothing_when_the_wifi_chip_is_on_or_bluetooth_is_off_by_choice(self):
        self.assertEqual(self.fc.chip_items("", hci=False), [])
        both = self.OURS.replace("dtoverlay=disable-wifi", "dtoverlay=disable-wifi\ndtoverlay=disable-bt")
        self.assertEqual(self.fc.chip_items(both, hci=False), [])


@unittest.skipIf(pygame is None, "no pygame")
class NoAdapterFlash(unittest.TestCase):
    """The Bluetooth page said "No Bluetooth adapter" for a few seconds while bluetoothd had not picked the adapter
    up yet. With hci0 in the kernel, one or two such answers are not believed; the page checks again at once."""

    def test_no_adapter_is_believed_on_the_third_answer_while_the_kernel_has_one(self):
        app = make_app(load_screen())
        with mock.patch("ui.views_bt.hci_present", return_value=True):
            app.bt_take({"hw": False, "why": "no-adapter"})
            self.assertIsNone(app.bt["hw"], "still checking")
            app.bt_take({"hw": False, "why": "no-adapter"})
            self.assertIsNone(app.bt["hw"])
            app.bt_take({"hw": False, "why": "no-adapter"})
            self.assertIs(app.bt["hw"], False)
            app.bt_take({"hw": True, "saved": []})
            app.bt_take({"hw": False, "why": "no-adapter"})
            self.assertIs(app.bt["hw"], True, "a working adapter is not taken away by one odd answer")

    def test_with_no_adapter_in_the_kernel_it_is_said_at_once(self):
        app = make_app(load_screen())
        with mock.patch("ui.views_bt.hci_present", return_value=False):
            app.bt_take({"hw": False, "why": "no-adapter"})
        self.assertIs(app.bt["hw"], False)


class BluetoothMissingSaysWhy(unittest.TestCase):
    """A Pi with Bluetooth of its own always has an adapter: "no adapter" is never the answer there."""

    def test_the_boards_with_bluetooth_of_their_own(self):
        from ui import system
        cases = {"Raspberry Pi 4 Model B Rev 1.4": True, "Raspberry Pi 3 Model B Plus Rev 1.3": True,
                 "Raspberry Pi Zero W Rev 1.1": True, "Raspberry Pi Zero 2 W Rev 1.0": True,
                 "Raspberry Pi 400 Rev 1.0": True, "Raspberry Pi 5 Model B Rev 1.0": True,
                 "Raspberry Pi Zero Rev 1.3": False, "Raspberry Pi 2 Model B Rev 1.1": False,
                 "Raspberry Pi Model B Plus Rev 1.2": False, "Raspberry Pi Compute Module 4 Rev 1.0": None}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model"
            for model, want in cases.items():
                p.write_text(model + "\0")
                self.assertIs(system.board_bluetooth(str(p)), want, model)
            self.assertIsNone(system.board_bluetooth(str(Path(d) / "none")))

    def test_the_chips_off_and_who_turned_them_off(self):
        from ui import system
        text = ("dtoverlay=disable-bt\n# >>> Armonico: radio chips turned off in Settings > Network and links\n"
                "[all]\ndtoverlay=disable-wifi\n# <<< Armonico: radio chips\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.txt"
            p.write_text(text)
            with mock.patch.object(system, "BOOT_CONFIGS", (str(p),)):
                self.assertEqual(system.chips_off(), ({"wifi", "bluetooth"}, {"bluetooth"}))

    @unittest.skipIf(pygame is None, "no pygame")
    def test_the_page_says_why_and_never_no_adapter_on_a_pi_with_one(self):
        app = make_app(load_screen())
        tr = app.tr
        with mock.patch("ui.views_bt.system.board_bluetooth", return_value=True):
            with mock.patch("ui.views_bt.system.chips_off", return_value=({"bluetooth"}, set())):
                self.assertEqual(app.bt_missing(), (tr("bt.chipOff"), tr("bt.chipOffD")))
            with mock.patch("ui.views_bt.system.chips_off", return_value=({"bluetooth"}, {"bluetooth"})):
                self.assertEqual(app.bt_missing()[1], tr("bt.chipOffHandD"))
            with mock.patch("ui.views_bt.system.chips_off", return_value=({"wifi"}, set())):
                self.assertEqual(app.bt_missing()[0], tr("bt.chipWithWifi"))
            with mock.patch("ui.views_bt.system.chips_off", return_value=(set(), set())):
                self.assertEqual(app.bt_missing()[0], tr("bt.stuck"))
                self.assertEqual(app.bt_reason("no-adapter"), tr("bt.stuck"))
        with mock.patch("ui.views_bt.system.board_bluetooth", return_value=False), \
                mock.patch("ui.views_bt.system.chips_off", return_value=(set(), set())):
            self.assertEqual(app.bt_missing()[0], tr("bt.noAdapter"))


@unittest.skipIf(pygame is None, "no pygame")
class BluetoothPageTurnsTheChipOn(unittest.TestCase):
    """No file to edit by hand: the page itself turns on the chip that keeps Bluetooth off."""

    def test_the_page_offers_the_chip_that_brings_bluetooth_back(self):
        app = make_app(load_screen())
        app.bt.update(hw=False, why="no-adapter")
        with mock.patch("ui.views_bt.system.board_bluetooth", return_value=True):
            with mock.patch("ui.views_bt.system.chips_off", return_value=({"bluetooth"}, {"bluetooth"})):
                self.assertEqual(app.bt_fix(), "bluetooth")
            with mock.patch("ui.views_bt.system.chips_off", return_value=({"wifi"}, set())):
                self.assertEqual(app.bt_fix(), "wifi")
            with mock.patch("ui.views_bt.system.chips_off", return_value=(set(), set())):
                self.assertIsNone(app.bt_fix())

    def test_a_chip_off_by_hand_is_asked_and_turned_on_with_the_hand_flag(self):
        app = make_app(load_screen())
        sent = []
        app.chips_off = lambda: {"bluetooth"}
        with mock.patch("ui.views_settings.system.chips_off", return_value=({"bluetooth"}, {"bluetooth"})), \
                mock.patch("ui.views_settings.system.request", side_effect=lambda *a, **k: sent.append(k) or (True, "")), \
                mock.patch("ui.views_settings.threading.Thread", side_effect=lambda target, daemon: mock.Mock(start=target)):
            app.chip_ask("bluetooth")
            self.assertIn(app.tr("chip.onHandQ", n=app.tr("chip.bt")), app.confirm["text"])
            app.confirm["yes"]()
        self.assertTrue(sent and sent[0].get("hand") is True and sent[0].get("on") is True)
