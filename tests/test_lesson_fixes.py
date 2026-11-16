"""The lesson engine's fixes after an audit: each test is one finding, failing before its fix.

Run from the project folder:

    python3 -m unittest discover -s tests
"""
import http.client
import json
import math
import sys
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))
sys.path.insert(0, str(ROOT / "tests"))

import mido                        # noqa: E402
import piano_game as game          # noqa: E402
import piano_learner as L          # noqa: E402
import piano_numbers as pn         # noqa: E402
import test_learner as TL          # noqa: E402  (the lesson harness, used as is)

DAY = L.DAY


class Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload, self.retain = topic, payload, False


def quiet_engine(replies):
    eng = game.Engine()
    eng.reply = lambda text, chat=None, raw=False: replies.append(text)
    eng.pub = lambda *a, **k: None
    return eng


class Folder(unittest.TestCase):
    """Every file the engine keeps, in a folder of the test's own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.dir = Path(self.tmp.name)
        for name, value in (("DATA_DIR", d), ("FLAG_GAME", d / "flag"), ("PROFILES_FILE", d / "profiles.json"),
                            ("SETTINGS_FILE", d / "settings.json"), ("MIDI_DIR", d / "songs"),
                            ("SHORTCUTS_FILE", d / "shortcuts.conf"), ("LAST_CHAT_FILE", d / "chat"),
                            ("SCREEN_CODE_FILE", d / "screen_code"), ("CODE_STATE_FILE", d / "code_enabled"),
                            ("SCREEN_SECRET_FILE", d / "screen_secret"), ("EDIT_FLAG", d / "edit-mode")):
            p = mock.patch.object(game, name, value)
            p.start()
            self.addCleanup(p.stop)
        (d / "songs").mkdir()
        game.LESSON_PENDING.clear()
        game.LESSON_CANCEL.clear()
        self.addCleanup(game.LESSON_PENDING.clear)
        self.addCleanup(game.LESSON_CANCEL.clear)


# ---------- 1. one bad MQTT message killed the network thread ----------
class MqttMessages(Folder):
    def test_a_secret_outside_ascii_is_refused_not_raised(self):
        with mock.patch.object(game, "MQTT_ADMIN_SECRET", "s3cret"):
            self.assertFalse(game.admin_ok("secret:\xe9"))
            self.assertFalse(game.admin_ok("secret:\ud800"))
            self.assertTrue(game.admin_ok("secret:s3cret on"))

    def test_nothing_escapes_on_message(self):
        replies = []
        eng = quiet_engine(replies)
        with mock.patch.object(game, "MQTT_ADMIN_SECRET", "s3cret"):
            eng.on_message(None, None, Msg("piano/game/cmd", "code||secret:\xe9".encode()))
        eng._on_message = mock.Mock(side_effect=RuntimeError("boom"))
        eng.on_message(None, None, Msg("piano/game/cmd", b"lessons||"))      # logged, not raised
        eng2 = quiet_engine(replies)
        eng2.on_message(None, None, Msg("piano/game/cmd", b"lessons||"))
        self.assertEqual(eng2.cmds.qsize(), 1)                              # the next message still goes through

    def test_the_editmode_reply_is_in_hebrew(self):
        replies = []
        eng = quiet_engine(replies)
        game.save_settings(dict(game.load_settings(), lang="he"))
        with mock.patch.object(game, "MQTT_ADMIN_SECRET", "s3cret"):
            eng.on_message(None, None, Msg("piano/game/cmd", b"editmode||secret:s3cret on"))
        self.assertEqual(replies[-1], "מצב עריכה פעיל, צריך לרענן את המסך")


# ---------- 2. edited wording that ran as code, or broke the page ----------
class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.comments = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag

    def handle_comment(self, data):
        self.comments.append(data)


class EditedHtml(Folder):
    def parsed(self, html):
        p = Tags()
        p.feed(game.clean_html(html))
        p.close()
        return p

    def test_a_space_after_the_bracket_is_text_not_a_tag(self):
        # "a < b > c" read as a <b> tag made the rest of the line bold; a browser reads it as text
        self.assertEqual(game.clean_html("a < b > c"), "a &lt; b > c")
        self.assertEqual(game.clean_html("<b>x</b>"), "<b>x</b>")

    def test_wording_saved_before_the_cleaner_is_cleaned_on_the_way_in(self):
        p = mock.patch.object(game, "TEXT_OVERRIDES_FILE", self.dir / "text_overrides.json")
        p.start()
        self.addCleanup(p.stop)
        game.TEXT_OVERRIDES_FILE.write_text(json.dumps({"en": {"content.about.p1": "<img src=x onerror=alert(1) '>hi"}, "he": {}}))
        got = game.load_overrides()["en"]["content.about.p1"]
        self.assertNotIn("<img", got)
        self.assertTrue(got.endswith("hi"))

    def test_a_tag_the_cleaning_cannot_read_is_text(self):
        for html in ("<img src=x onerror=alert(1) '>", '<img src=x onerror=alert(1) ">',
                     "<b>x</b><img src=x onerror=alert(1) 'a>", "<!--<script ", "<script ",
                     "<a href='x' onclick=f() \"", "<svg/onload=alert(1) '>"):
            with self.subTest(html=html):
                p = self.parsed(html)
                for tag, attrs in p.tags:
                    self.assertIn(tag, game.EDIT_TAGS)
                    self.assertFalse([a for a in attrs if a.startswith("on")], (tag, attrs))
                self.assertEqual(p.comments, [])
                self.assertNotIn("<script", game.clean_html(html).lower())

    def test_what_was_kept_before_is_kept(self):
        self.assertEqual(game.clean_html('<p class="a">x</p><!-- note --><b>y</b>'), '<p class="a">x</p><b>y</b>')
        self.assertEqual(game.clean_html("<script>x</script>ok"), "ok")

    def test_the_cleaning_takes_linear_time(self):
        for bad in ("<a x " * 16000, "<script " * 16000, "<!--" * 20000, '<a "' * 20000, "<script>" * 16000):
            with self.subTest(bad=bad[:8]):
                t = time.time()
                game.clean_html(bad)
                self.assertLess(time.time() - t, 1.5)

    def test_wording_in_the_page_cannot_end_its_script(self):
        evil = {"en": {"x": "</script><script>alert(1)</script><!--<script>"}, "he": {}}
        plain = game.render_page()
        with mock.patch.object(game, "load_overrides", lambda: evil):
            page = game.render_page()
        self.assertEqual(page.count("</script>"), plain.count("</script>"))
        self.assertEqual(page.count("<!--"), plain.count("<!--"))
        line = next(ln for ln in page.splitlines() if ln.startswith("const OV = "))
        self.assertEqual(json.loads(line[len("const OV = "):line.index(";   //")]), evil)


# ---------- 3. a long run scored 40% for one wrong key ----------
class FakeRun:
    run_with_band = game.Engine.run_with_band

    def __init__(self, keys):
        self.band, self.ghost_song, self.mistakes, self.starts = False, None, {}, None
        self.queue = list(keys)

    def tempo_scale(self):
        return 1.0

    def count_in(self):
        pass

    def check_abort(self):
        pass

    def send_raw(self, data):
        pass

    def band_off(self, sounding):
        pass

    def run_scores(self, accuracy, hits, played, n):
        return {}

    def poll_key(self):
        return self.queue.pop(0) if self.queue else None


class LongRuns(unittest.TestCase):
    def song(self):
        import random
        rnd = random.Random(1)
        return [rnd.choice([60, 62, 64, 65, 67, 69, 71, 72]) for _ in range(250)]

    def test_one_wrong_key_in_250_notes_is_still_almost_all(self):
        song = self.song()
        keys = list(song)
        keys.insert(100, 61)
        with mock.patch.multiple(game, ui=lambda **k: None, TEST_END_SILENCE=0.2, HINT_SILENCE=5.0):
            score = FakeRun(keys).run_with_band(song, band=False)
        self.assertGreaterEqual(score, 99)

    def test_a_long_shortcut_takes_its_whole_rhythm_from_the_file(self):
        song = self.song()
        with tempfile.TemporaryDirectory() as d:
            mid = mido.MidiFile()
            tr = mido.MidiTrack()
            mid.tracks.append(tr)
            played = list(song)
            played[120] = 61                          # the file differs from the shortcut in one note
            for n in played:
                tr.append(mido.Message("note_on", note=n, velocity=80, time=0))
                tr.append(mido.Message("note_off", note=n, velocity=0, time=240))
            path = str(Path(d) / "s.mid")
            mid.save(path)
            plan, starts, shift = pn.rhythm_and_starts(song, path)
        self.assertGreaterEqual(sum(1 for p in plan if p), 249)


# ---------- 4, 5, 6, 14, 15: a lesson from start to end ----------
class Flow(unittest.TestCase):
    setUp = TL.LessonFlow.setUp
    next_key = TL.LessonFlow.next_key
    fake_run = TL.LessonFlow.fake_run
    save = TL.LessonFlow.save
    load = TL.LessonFlow.load

    def tearDown(self):
        game.LESSON_PENDING.clear()
        game.LESSON_CANCEL.clear()

    def other_profile(self):
        self.save("profiles.json", {"active": "main", "list": [{"id": "main", "name": ""},
                                                               {"id": "p0000aaaa", "name": "Dan"}]})

    def test_a_profile_switched_while_the_lesson_starts_does_not_take_its_progress(self):
        self.other_profile()
        busy = []

        def open_ports():
            busy.append(game.lesson_busy())               # the endpoints that switch refuse now
            with game.PROFILE_LOCK:                        # as if a switch got through at that moment
                prof = game.load_profiles()
                game.save_profiles(dict(prof, active="p0000aaaa"))
            return True
        self.eng.open_ports = open_ports
        self.eng.lesson("", "a")
        self.assertEqual(busy, [True])
        self.assertIn("a", self.load("lessons.json"))                       # main's, where it began
        self.assertFalse((self.dir / "profiles" / "p0000aaaa" / "lessons.json").exists())
        self.assertTrue((self.dir / "history.json").exists())
        self.assertTrue((self.dir / "learner.json").exists())
        self.assertFalse(game.lesson_busy())

    def test_a_second_start_is_refused_while_one_is_queued(self):
        replies = []
        eng = quiet_engine(replies)
        eng.on_message(None, None, Msg("piano/game/cmd", b"lesson||a"))
        eng.on_message(None, None, Msg("piano/game/cmd", b"lesson||b"))
        self.assertEqual(eng.cmds.qsize(), 1)
        self.assertFalse(game.claim_lesson())
        self.assertTrue(replies)                           # the second one was told why

    def test_a_stop_while_queued_cancels_the_lesson(self):
        replies = []
        eng = quiet_engine(replies)
        eng.on_message(None, None, Msg("piano/game/cmd", b"lesson||a"))
        eng.on_message(None, None, Msg("piano/game/cmd", b"lessonstop||"))
        self.assertEqual(replies[-1], game.tr("⏹ Stopping the lesson..."))
        self.eng.lesson("", "a")                           # the queue gets to it
        self.assertFalse((self.dir / "flag").exists())
        self.assertFalse((self.dir / "lessons.json").exists())
        self.assertIn(game.tr("⏹ The lesson was stopped before it started"), self.replies)
        self.assertTrue(game.claim_lesson())               # and the next one may start

    def test_a_stop_while_the_lesson_starts_is_not_lost(self):
        def open_ports():
            self.eng.stop_lesson()                         # /lessonstop while amidi -l is still running
            return True
        self.eng.open_ports = open_ports
        self.eng.lesson("", "a")
        self.assertFalse((self.dir / "flag").exists())
        self.assertEqual(self.rounds, 0)
        self.assertIn(game.tr("⏹ The lesson was stopped before it started"), self.replies)

    def test_a_replay_stops_when_a_lesson_starts(self):
        fake = mock.Mock()
        with mock.patch.object(game, "REPLAY", fake):
            self.eng.lesson("", "a")
        fake.stop.assert_called()

    @unittest.skip("no warm-up: a lesson opens on the song it was started for (piano_game.py, warm = [])")
    def test_one_slip_found_again_in_the_warm_up_stays_good(self):
        now = time.time()
        self.save("lessons.json", {"b": {"learned": 2, "parts": {"0": TL.old_part(now, 9.8), "1": TL.old_part(now, 1)}}})
        self.bad = {1}                                     # the warm-up's round: one wrong key, found again
        self.eng.lesson("", "a")
        rows = self.load("learner.json")["reviews"]
        self.assertEqual(rows[0][:1], ["b#0"])
        self.assertEqual(rows[0][2], L.GOOD)
        titles = [c.get("title", "") for c in self.calls]
        self.assertFalse([t for t in titles if "Warm-up" in t and "keys lit" in t])
        steps = self.load("learner.json")["transitions"]
        self.assertIn("b#1", steps)                        # the slip goes to the drills of that step

    def test_a_song_that_starts_over_loses_its_old_steps(self):
        self.save("learner.json", {"skills": {}, "reviews": [["a#0", 1, 3], ["b#0", 1, 3]],
                                   "transitions": {"a#2": [3, 1], "b#1": [1, 1], "0>4+4": [2, 1]}})
        self.save("lessons.json", {"a": {"learned": 1}})
        game.forget_learner("a")
        brain = self.load("learner.json")
        self.assertEqual(brain["reviews"], [["b#0", 1, 3]])
        self.assertEqual(set(brain["transitions"]), {"b#1", "0>4+4"})
        self.save("learner.json", {"skills": {}, "transitions": {"a#2": [3, time.time()]}})
        self.eng.lesson("", "a reset")
        self.assertNotIn("a#2", self.load("learner.json")["transitions"])


# ---------- 7 and the skills: Armonico's own rules around the curves ----------
class SameDay(unittest.TestCase):
    def test_practice_every_9_hours_grows_the_part(self):
        p = {}
        L.start_memory(p, 0.0, 0, 0)
        start = p["S"]
        for k in range(1, 9):
            L.update_memory(p, L.GOOD, k * 9 * 3600)
        self.assertGreater(p["S"], start * 1.5)

    def test_slips_in_one_day_take_at_most_half(self):
        p = {"S": 21.0, "D": 5.0, "mem_at": 0}
        for k in range(1, 5):
            L.update_memory(p, L.AGAIN, k * 3600)
        self.assertGreaterEqual(p["S"], 10.5)

    def test_the_fit_replays_the_same_rule(self):
        rows = [["a#0", 0, 0, 5.0], ["a#0", 9 * 3600, 3], ["a#0", 18 * 3600, 3]]
        p = {}
        L.start_memory(p, 0.0, 0)
        L.update_memory(p, L.GOOD, 9 * 3600)
        L.update_memory(p, L.GOOD, 18 * 3600)
        rows.append(["a#0", 30 * DAY, 3])
        got = L.replay(rows, L.DEFAULT_FIT)
        self.assertAlmostEqual(got[-1][2], L.recall(30 - 18 / 24, p["S"]), places=3)

    def test_a_skill_the_last_lesson_used_does_not_drift(self):
        brain = {"skills": {"leap": 0.9, "black": 0.9}, "skills_at": 0}
        L.age_skills(brain, 0.5)                           # a lesson begins: the state is kept
        brain["skills"]["leap"] = 0.92                     # that lesson used leaps only
        L.age_skills(brain, 90 * DAY)
        self.assertEqual(brain["skills"]["leap"], 0.92)
        self.assertAlmostEqual(brain["skills"]["black"], L.BKT_L0 + (0.9 - L.BKT_L0) / 2, places=3)
        L.age_skills(brain, 180 * DAY)                     # leaps unused since that lesson: 180 days of it
        self.assertAlmostEqual(brain["skills"]["leap"], L.BKT_L0 + (0.92 - L.BKT_L0) / 4, places=3)


# ---------- 8, 10, 11, 12, 15: the screen's actions ----------
class Screen(Folder):
    def setUp(self):
        super().setUp()
        self.keys = []
        live = mock.Mock()
        live.key = lambda note, down, *a: self.keys.append(note) or True
        for name, value in (("LIVE_OUT", live), ("ENGINE", mock.Mock())):
            p = mock.patch.object(game, name, value)
            p.start()
            self.addCleanup(p.stop)
        game.set_code_on(False)
        game.FAILS.clear()
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), game.UIHandler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def ask(self, path, raw, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, raw if isinstance(raw, bytes) else json.dumps(raw).encode(),
                     dict({"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}, **(headers or {})))
        got = conn.getresponse()
        got.read()
        return got.status

    def test_numbers_that_are_not_numbers_are_refused(self):
        for path, raw in (("/settings", b'{"speed": NaN}'), ("/volume", b'{"value": Infinity}'),
                          ("/volume", b'{"value": 1e999}'), ("/settings", b"[1]"),
                          ("/settings", b'{"pace": []}'), ("/replay", b'{"at": 1' + b"0" * 400 + b"}")):
            with self.subTest(path=path, raw=raw[:20]):
                self.assertIn(self.ask(path, raw), (400, 404, 204))
        self.assertEqual(self.ask("/settings", b'{"speed": NaN}'), 400)
        self.assertEqual(self.ask("/settings", {"speed": 70}), 204)

    def test_a_code_outside_ascii_is_wrong_and_counted(self):
        game.set_code_on(True)
        self.assertEqual(self.ask("/code", {"code": "אב"}), 401)
        self.assertEqual(self.ask("/settings", {}, {"X-Screen-Code": "\xe9\xe9"}), 401)
        self.assertEqual(len(game.FAILS["127.0.0.1"]), 2)
        self.assertFalse(game.pass_ok("1" * 12 + ".\xe9"))

    def test_the_device_keys_play_in_every_octave_of_the_device(self):
        with mock.patch.object(game, "IS_DEVICE", True):
            for note in (24, 35, 96, 107, 108):          # 108: the high C key in octave 7
                self.assertEqual(self.ask("/key", {"note": note, "down": True}), 204, note)
            self.assertEqual(self.ask("/key", {"note": 109, "down": True}), 400)
            self.assertEqual(self.ask("/key", {"note": 23, "down": True}), 400)
            self.assertEqual(game.label(62), "2")
            self.assertEqual(game.label(25), "1#")
        self.assertEqual(self.ask("/key", {"note": 24, "down": True}), 400)   # a keyboard keeps its range
        self.assertNotIn("keyNumber", game.PAGE)
        self.assertIn("w.dataset.n = DEVMODE ? (k === 7 ? 8 : k % 7 + 1) : k + 1;", game.PAGE)

    def test_the_browser_draws_the_devices_13_keys(self):
        """On the device the browser's keyboard is the device's own: 13 keys, the high C its own key, numbered 1."""
        with mock.patch.object(game, "IS_DEVICE", True):
            page = game.render_page()
            self.assertIn("const DEVKEYS = 13;", page)
            self.assertIn("LAST = FIRST + DEVKEYS - 1", page)
            self.assertIn("m === FIRST + 12 ? m : FIRST + pc(m)", page)
        self.assertIn("Octave of the device's keys", game.PAGE)

    def test_a_song_with_a_shortcut_shows_and_resets_the_progress_its_lessons_keep(self):
        mid = mido.MidiFile()
        tr = mido.MidiTrack()
        mid.tracks.append(tr)
        for n in [60, 62, 64, 65, 67, 69, 71, 72]:
            tr.append(mido.Message("note_on", note=n, velocity=80, time=0))
            tr.append(mido.Message("note_off", note=n, velocity=0, time=480))
        mid.save(str(self.dir / "songs" / "ode_to_joy.mid"))
        (self.dir / "shortcuts.conf").write_text("60 62 64 65 67 69 71 72|Ode to joy\n")
        now = time.time()
        game.save_progress({"Ode to joy": {"learned": 1, "parts": {"0": {"streak": 1, "first": now, "last": now}}}})
        game._SONG_CACHE.clear()
        card = next(s for s in game.song_list() if s["name"] == "ode_to_joy")
        self.assertEqual(card["learned"], 1)
        self.assertEqual(self.ask("/progress/reset", {"song": "ode_to_joy"}), 204)
        self.assertEqual(game.load_progress(), {})


# ---------- 13. recordings that grew with the square of the lesson ----------
class Recordings(unittest.TestCase):
    def test_the_notes_of_a_piece_are_kept_once(self):
        r = game.Recorder.__new__(game.Recorder)
        r.events, r.lock, r.t0, r.last_screen = [], threading.Lock(), time.time(), None
        seq = list(range(1000))
        for pos in range(200):
            r.screen({"mode": "wait", "seq": seq, "midi": seq, "expect": pos})
        with_notes = [e for e in r.events if "seq" in e[2]]
        self.assertEqual(len(with_notes), 1)
        self.assertEqual(len(r.events), 200)
        self.assertLess(len(json.dumps(r.events)), 60000)
        r.screen({"mode": "wait", "seq": [1], "midi": [1], "expect": 0})   # a new piece: its notes again
        self.assertEqual(r.events[-1][2]["seq"], [1])

    def test_the_replay_carries_the_notes_on(self):
        self.assertIn("if (!('seq' in e[2])) e[2].seq = carry.seq;", game.PAGE)


# ---------- 9. the recorder read twice ----------
class RecorderOnce(unittest.TestCase):
    def test_ui_survives_the_lesson_ending_meanwhile(self):
        class Ending:
            def screen(self, snap):
                game.RECORDER = None
        with mock.patch.object(game, "RECORDER", Ending()):
            game.ui(msg="x")
            game.ui(msg="y")


# ---------- 16. saves at once ----------
class Saves(Folder):
    def test_saves_at_once_never_meet(self):
        errors = []

        def many(i):
            try:
                for k in range(40):
                    game.save_progress({"n": i * 100 + k})
                    game.save_settings(dict(game.load_settings(), speed=50 + (k % 40)))
                    with game.PROFILE_LOCK:
                        game.save_profiles(game.load_profiles())
            except Exception as e:                    # the shared .tmp: FileNotFoundError on replace
                errors.append(e)
        threads = [threading.Thread(target=many, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        json.loads((self.dir / "lessons.json").read_text())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")), [])
        self.assertEqual((self.dir / "profiles.json").stat().st_mode & 0o777, 0o600)

    def test_save_profiles_waits_for_the_lock(self):
        held, done = threading.Event(), []

        def holder():
            with game.PROFILE_LOCK:
                held.set()
                time.sleep(0.3)
                done.append("holder")
        t = threading.Thread(target=holder)
        t.start()
        held.wait()
        game.save_profiles(game.load_profiles())
        done.append("save")
        t.join()
        self.assertEqual(done, ["holder", "save"])


if __name__ == "__main__":
    unittest.main()
