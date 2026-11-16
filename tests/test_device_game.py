"""The device's game (device/ui/views_game.py) and its song (game_song in lessons/piano_game.py).

The notes fall onto the keys and the song waits on each one until it is played: the clock stops at
a note that landed, a wrong key counts against the accuracy and breaks the streak but ends nothing,
and the song played is a started song that fades, never one not started, or the easiest with none started.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None

SONG = {"ok": True, "name": "ode_to_joy", "why": "fading", "recall": 0.6,
        "notes": [[64, 0.5], [64, 0.5], [65, 0.5], [67, 0.5], [60, 0.5], [72, 0.5]]}


class _Game:
    """The game's own numbers (LAND, SPEED, SEEN_LATE), from the module the screen loaded."""
    def __getattr__(self, name):
        mod = next(m for n, m in sys.modules.items() if n.endswith("views_game") and hasattr(m, "SEEN_LATE"))
        return getattr(mod, name)


GAME = _Game()


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@unittest.skipIf(pygame is None, "pygame is not installed")
class GameOnTheKeys(unittest.TestCase):
    """The song runs at its tempo: a note is played as it touches the line, early or late says which way,
    a miss or a wrong key costs a life, 10 in a row win one back, no lives left is out."""
    def setUp(self):
        mod = load_screen()
        self.app = make_app(mod, (800, 480), "he")
        self.app.clock = Clock()
        self.app.t = self.app.clock
        self.app.screen.pub = mock.Mock()
        self.app.go("game")
        self.app.game_loading, self.app.game_song = False, dict(SONG)

    def run_for(self, secs, step=0.02):
        end = self.app.clock.now + secs
        while self.app.clock.now < end:
            self.app.clock.now += step
            self.app.game_tick(self.app.clock.now)

    def to_note(self, plus=0.0):
        """The clock to the moment the hand plays the next note, plus some (late) or minus (early): the press
        is seen SEEN_LATE after the hand made it (views_game.py), so the clock stands that much further."""
        g = self.app.game
        self.run_for(g["obs"][g["i"]]["at"] - g["clock"] + plus + GAME.SEEN_LATE)

    def test_any_key_starts_and_the_song_does_not_wait(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        self.assertIsNotNone(a.game)
        first = a.game["obs"][0]
        self.assertEqual(first["key"], 4)                  # E over the song's lowest C
        self.run_for(first["at"] + 0.5)                    # past the line, unplayed
        self.assertEqual(a.game["i"], 1)
        self.assertEqual(a.game["lives"], 4)
        self.assertEqual(a.game["words"][-1][1], a.tr("g.miss"))

    def test_on_the_line_early_and_late(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        self.to_note(-0.02)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["i"], a.game["words"][-1][1], a.game["words"][-1][3]), (1, a.tr("g.perfect"), ""))
        self.to_note(-0.15)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["words"][-1][1], a.game["words"][-1][3]), (a.tr("g.good"), a.tr("g.early")))
        self.to_note(0.25)
        a.game_key(5, a.clock.now)
        self.assertEqual((a.game["words"][-1][1], a.game["words"][-1][3]), (a.tr("g.almost"), a.tr("g.late")))
        self.assertEqual(a.game["lives"], 5)

    def test_a_key_played_on_its_note_is_perfect_though_it_is_seen_late(self):
        """A key of the device comes about 60 ms after the hand (the debounce, the broker, the frame): judged
        without that, a note played exactly on time was told "late"."""
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        g = a.game
        self.run_for(g["obs"][0]["at"] - g["clock"] + GAME.SEEN_LATE)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["words"][-1][1], a.game["words"][-1][3]), (a.tr("g.perfect"), ""))
        self.assertLess(abs(a.game["off"]), 0.03)

    def test_the_keys_sound_at_the_songs_height_as_in_a_lesson(self):
        """The note coming is what the keys' program is told (piano/device/expect), in the lesson's octave:
        before, the song was heard in whatever octave free play had left the keys in."""
        a = self.app
        self.assertIsNone(a.game_want())                   # on the list: every key plays its own octave
        a.game_start(a.clock.now, song=a.game_song)
        first = a.game["obs"][0]
        self.assertEqual(a.game_want(), first["note"])
        self.assertEqual(first["note"] % 12, first["key"] % 12)
        a.screen.octave, a.screen.sent_expect = 2, None
        a.screen.follow_game(a.game_want())
        sent = [c.args for c in a.screen.pub.call_args_list]
        self.assertIn(("piano/device/octave/set", "4"), sent)
        self.assertIn(("piano/device/expect", str(first["note"])), sent)
        self.to_note()
        a.game_key(first["key"], a.clock.now)
        self.assertEqual(a.game_want(), a.game["obs"][1]["note"])    # the next one, as soon as this is played
        for _ in range(5):
            a.game_key(11 if a.game["obs"][a.game["i"]]["key"] != 11 else 10, a.clock.now)
        self.assertIsNone(a.game_want())                   # out: the keys are free again

    def test_the_same_key_twice_in_a_row_is_drawn_where_its_time_puts_it(self):
        """The second of two on one key was pushed up to make room and dropped back when the first was played,
        so it looked later than it was. Now the first is drawn shorter and neither moves."""
        a = self.app
        a.game_song = dict(SONG, notes=[[64, 0.25], [64, 0.25], [64, 0.5]])
        a.game_start(a.clock.now, song=a.game_song)
        self.to_note(-0.3)
        drawn = []
        with mock.patch.object(a.cv, "rect", side_effect=lambda x, y, w, h, *k, **kw: drawn.append((y, y + h))):
            a.game_notes(a.game, a.clock.now)
        g = a.game
        for (top, bottom), o in zip(drawn, g["obs"]):
            self.assertAlmostEqual(bottom, GAME.LAND - (o["at"] - g["clock"]) * GAME.SPEED, places=3)
        self.assertGreater(drawn[0][0], drawn[1][1])       # a gap between the two

    def test_the_band_starts_with_a_song_and_stops_when_it_is_lost_or_left(self):
        """The engine is asked once for each run: the song, when each note is due and the clock's zero. Out, the
        song stopped or the game left: that run is stopped. Played to its end: the band rings out."""
        a = self.app
        asked = []
        a.screen.engine._req = lambda path, body=None, timeout=3: asked.append((path, body)) or {"ok": True}

        def sync(n):
            a.game_band_sync()
            end = time.monotonic() + 2
            while len(asked) < n and time.monotonic() < end:
                time.sleep(0.01)                           # the engine is asked off the loop
        sync(0)
        self.assertEqual(asked, [])                        # on the list: nothing plays
        a.game_start(a.clock.now, song=a.game_song)
        started = time.monotonic()
        sync(1)
        sync(1)                                             # the next frames ask nothing again
        self.assertEqual([p for p, _ in asked], ["/game/band"])
        body = asked[0][1]
        self.assertEqual((body["song"], len(body["ats"]), body["id"]), ("ode_to_joy", 6, a.game["run"]))
        self.assertAlmostEqual(body["zero"], started, delta=1.0)   # the clock's zero, on the machine's own clock
        first = a.game["run"]
        for _ in range(5):
            a.game_key(11, a.clock.now)                    # out
        sync(2)
        self.assertEqual(asked[-1], ("/game/band/stop", {"id": first}))
        a.clock.now += 1.5
        a.game_key(0, a.clock.now)                         # again: a new run, a new band
        sync(3)
        self.assertEqual(asked[-1][0], "/game/band")
        self.assertNotEqual(asked[-1][1]["id"], first)
        n = len(asked)
        for o in a.game["obs"]:
            self.to_note()
            a.game_key(o["key"], a.clock.now)
        self.assertFalse(a.game["out"])
        sync(3)
        self.assertEqual(len(asked), n)                    # played through: left to ring
        a.go("settings")
        sync(4)
        self.assertEqual(asked[-1][0], "/game/band/stop")  # the game was left

    def test_a_quick_run_takes_the_key_meant_for_the_note_after(self):
        """Two notes due within the window: the key of the second, on its time, is a hit on it and the first
        one counts as missed; it is not a wrong key."""
        a = self.app
        song = dict(SONG, name="quick", notes=[[64, 0.12], [67, 0.12], [65, 0.5], [64, 0.5]])
        a.game_start(a.clock.now, song=song)
        g = a.game
        self.run_for(g["obs"][1]["at"] - g["clock"] + GAME.SEEN_LATE)
        a.game_key(7, a.clock.now)                         # G, the second note, right on it
        self.assertEqual((g["i"], g["lives"]), (2, 4))     # E missed, G played
        self.assertNotEqual(g["words"][-1][1], a.tr("g.wrongKey"))

    def test_the_clock_is_the_real_time_even_when_frames_are_slow(self):
        """A slow frame does not hold the song back: the band plays on the real time, so the notes must too."""
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        start = a.clock.now
        a.clock.now += 0.9                                 # one frame of nearly a second
        a.game_tick(a.clock.now)
        self.assertAlmostEqual(a.game["clock"], a.clock.now - start, places=6)

    def test_a_song_starts_at_a_tempo_its_quickest_notes_can_be_played(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        self.assertEqual(a.game_tempo, 1.0)                # half a second apart: its own speed
        a.game = None
        a.game_start(a.clock.now, song=dict(SONG, name="quick", notes=[[64, 0.15], [65, 0.15], [67, 0.5]]))
        self.assertLess(a.game_tempo, 1.0)
        self.assertGreaterEqual(0.15 / a.game_tempo, GAME.QUICKEST - 1e-9)
        a.game_set_tempo(1)                                # the player's choice is kept for that song
        chosen = a.game_tempo
        a.game = None
        a.game_start(a.clock.now, song=dict(SONG, name="quick", notes=[[64, 0.15], [65, 0.15], [67, 0.5]]))
        self.assertEqual(a.game_tempo, chosen)

    def test_the_octave_of_the_next_note_is_shown_as_in_a_lesson(self):
        a = self.app
        a.screen.octave = 4
        a.game_start(a.clock.now, song=dict(SONG, notes=[[76, 0.5], [77, 0.5]]))   # E5 on the E key of octave 4
        said = []
        real = a.cv.text
        a.cv.text = lambda s, *k, **kw: said.append(str(s)) or real(s, *k, **kw)
        a.draw()
        self.assertIn("↑ 5", said)

    def test_the_anime_girl_reacts_in_the_game_and_only_in_that_theme(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        a.game_key(11, a.clock.now)                        # a wrong key, another theme: no girl
        self.assertNotIn("_anime", a.__dict__)
        a.st["theme"] = "anime"
        a.game_key(11, a.clock.now)
        self.assertIsNotNone(a.anime.cutin)

    def test_the_title_shows_between_songs_and_lights_the_logos_keys_in_the_songs_time(self):
        a = self.app
        said = []
        real = a.cv.text
        a.cv.text = lambda s, *k, **kw: said.append(str(s)) or real(s, *k, **kw)
        a.game = None
        a.draw()
        self.assertIn(a.tr("g.title"), said)
        lit = {a.game_title_key(x / 10) for x in range(0, 60)}
        self.assertGreater(len(lit), 2, "the keys follow the song, not one key")
        said.clear()
        a.game_start(a.clock.now, song=dict(SONG))
        a.draw()
        self.assertNotIn(a.tr("g.title"), said, "gone while a song plays")

    def test_a_black_key_takes_less_than_it_shows(self):
        a = self.app
        rects = a.game_keys(None, a.clock.now)
        a.touch_keys(rects)
        pc, x, y, w, h = next(r for r in rects if r[0] == 1)
        self.assertEqual(a.key_at(x + 2, y + 10), 0)       # its edge: the white key under it
        self.assertEqual(a.key_at(x + w / 2, y + 10), 1)
        self.assertEqual(a.key_at(x + w / 2 - 8, y + h - 4), 0)          # its lower end: the white key (the middle is the gap)

    def test_far_too_soon_is_let_be_and_a_wrong_key_costs_a_life(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        self.to_note(-1.0)
        a.game_key(4, a.clock.now)
        self.assertEqual((a.game["i"], a.game["lives"]), (0, 5))
        a.game_key(2, a.clock.now)
        self.assertEqual((a.game["i"], a.game["lives"], a.game["combo"]), (0, 4, 0))

    def test_out_when_the_lives_run_out_and_any_key_starts_again(self):
        a = self.app
        a.game_start(a.clock.now, song=a.game_song)
        for _ in range(5):
            a.game_key(11, a.clock.now)
        self.assertTrue(a.game["out"])
        self.assertIsNotNone(a.game["done"])
        a.clock.now += 1.5
        a.game_key(0, a.clock.now)                         # any key after: the same song again
        self.assertFalse(a.game["out"])
        self.assertEqual(a.game["lives"], 5)

    def test_ten_in_a_row_win_a_life_back(self):
        a = self.app
        a.game_song = dict(SONG, notes=SONG["notes"] * 3)
        a.game_start(a.clock.now, song=a.game_song)
        a.game_key(11, a.clock.now)                        # a wrong key: 4 left
        for _ in range(10):
            self.to_note()
            o = a.game["obs"][a.game["i"]]
            a.game_key(o["key"], a.clock.now)
        self.assertEqual(a.game["lives"], 5)

    def test_tempo_steps_and_the_whole_song_and_the_high_c(self):
        a = self.app
        a.game_set_tempo(1)
        self.assertEqual(a.game_tempo, 0.9)
        a.game_start(a.clock.now, song=a.game_song)
        self.assertEqual(a.game["obs"][-1]["key"], 12)     # the C an octave up is key 1'
        for o in a.game["obs"]:
            self.to_note()
            a.game_key(o["key"], a.clock.now)
        self.assertIsNotNone(a.game["done"])
        self.assertFalse(a.game["out"])
        self.assertEqual(a.game["best"], len(SONG["notes"]))

    def test_a_key_is_judged_when_it_was_pressed_not_when_the_frame_sees_it(self):
        a = self.app
        a.game_start(a.clock.now, song=dict(SONG))
        self.to_note(0.15)                              # the frame comes 0.15 s after the line
        a.game_key(4, a.clock.now - 0.15)               # but the key went down on the line
        self.assertEqual(a.game["i"], 1)
        self.assertEqual(a.game["words"][-1][1], a.tr("g.perfect"))

    def test_a_note_on_the_broker_carries_its_time_and_a_touch_is_not_counted_twice(self):
        a = self.app
        a.t0 = 0.0
        a.game_start(a.clock.now, song=dict(SONG))
        self.to_note(0.12)
        a.game_note(f"64 {a.clock.now - 0.12:.4f}")       # E, stamped as it came, on the line
        self.assertEqual(a.game["i"], 1)
        self.to_note()
        a.game_touched(4)                               # touched on the screen: judged at once
        a.game_note(f"64 {a.clock.now:.4f}")              # its copy from the engine: dropped
        self.assertEqual(a.game["i"], 2)
        self.assertEqual(a.game["lives"], 5)

    def test_the_list_puts_the_pick_first_and_plays_the_one_marked(self):
        a = self.app
        a.screen.songs = lambda: [{"name": "twinkle", "total": 3}, {"name": "ode_to_joy", "total": 4}, {"name": "jingle", "total": 2}]
        self.assertEqual(a.game_list(), ["ode_to_joy", "twinkle", "jingle"])
        a.game_pick_move(1)
        asked = []
        with mock.patch.object(a, "game_load", side_effect=lambda other=False, song="": asked.append(song)):
            a.game_key(7, a.clock.now)                     # any key on the list: the song marked
        self.assertEqual(asked, ["twinkle"])
        a.game_ready = dict(SONG, name="twinkle")
        self.run_for(0.1)
        self.assertEqual(a.game["name"], "twinkle")
        a.game_back()
        self.assertIsNone(a.game)
        self.assertEqual(a.game_list()[a.game_pick["sel"]], "twinkle")
        a.draw()

    def test_it_draws_in_every_state(self):
        a = self.app
        for song in (None, {"ok": False}, dict(SONG)):
            a.game, a.game_song = None, song
            a.game_loading = song is None
            a.draw()
        a.game_start(a.clock.now, song=a.game_song)
        self.run_for(2.0)
        a.draw()
        self.run_for(10.0)
        a.draw()
        a.game["out"] = True
        a.draw()


class TheGamesSong(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lessons"))
        import piano_game
        cls.pg = piano_game

    def call(self, songs, progress, recall, skip="", song=""):
        pg = self.pg
        notes = [60, 62, 64]
        with mock.patch.object(pg, "song_list", return_value=songs), \
                mock.patch.object(pg, "load_progress", return_value=progress), \
                mock.patch.object(pg.learner, "recall_now", side_effect=lambda p, now: recall[p["id"]]), \
                mock.patch.object(pg, "load_song", side_effect=lambda n: (n, notes, [(0.4, 0.1)] * 3, None, "", 0)):
            return pg.game_song(skip=skip, song=song)

    def band_file(self, folder, two_tracks=True):
        """A song of 4 notes half a second apart, with a bass note under each (channel 2) when two_tracks."""
        import mido
        mid = mido.MidiFile(ticks_per_beat=480)
        tune, bass = mido.MidiTrack(), mido.MidiTrack()
        tune.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        for n in (60, 62, 64, 65):
            tune.append(mido.Message("note_on", note=n, velocity=90, time=0))
            tune.append(mido.Message("note_off", note=n, velocity=0, time=480))
        bass.append(mido.Message("program_change", channel=1, program=32, time=0))
        for n in (36, 38, 40, 41):
            bass.append(mido.Message("note_on", channel=1, note=n, velocity=70, time=0))
            bass.append(mido.Message("note_off", channel=1, note=n, velocity=0, time=480))
        mid.tracks.append(tune)
        if two_tracks:
            mid.tracks.append(bass)
        path = Path(folder) / "band_song.mid"
        mid.save(str(path))
        return path

    def test_the_game_keeps_the_files_own_time(self):
        """No gap is squeezed: a fast note and a long rest keep their length (the screen picks the speed)."""
        pg = self.pg
        with mock.patch.object(pg, "song_list", return_value=[{"name": "s", "total": 3}]), \
                mock.patch.object(pg, "load_progress", return_value={}), \
                mock.patch.object(pg, "load_song", side_effect=lambda n: (n, [60, 62, 64], [(0.08, 0.02), (2.0, 1.5), (0.4, 0.1)], None, "", 0)):
            got = pg.game_song(song="s")
        self.assertEqual([st for _, st in got["notes"]], [0.1, 3.5, 0.5])

    def test_the_band_is_fitted_to_the_games_time_between_the_notes(self):
        """What the file plays from one note of the tune to the next lands between the same two notes of the
        game, however far apart the game puts them; the tune itself is never played by the band."""
        import tempfile
        pg = self.pg
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pg, "MIDI_DIR", Path(d)), \
                mock.patch.object(pg, "load_shortcuts", return_value={}), mock.patch.object(pg, "IS_DEVICE", True):
            self.band_file(d)
            getattr(pg, "_SONG_CACHE", {}).clear()
            band = pg.GameBand()
            plan = band.plan("band_song", 4)
            self.assertIsNotNone(plan)
            ats = [2.0, 3.0, 4.0, 5.0]                     # the game plays it at half the file's speed
            line = band.timeline(plan, ats, 100.0)
            ons = [(round(t, 3), b[1]) for t, b in line if b[0] & 0xF0 == 0x90 and b[2]]
            self.assertEqual([t for t, _ in ons], [102.0, 103.0, 104.0, 105.0])
            self.assertTrue(all(b[0] & 0x0F == 1 for _, b in line))         # the bass alone: no copy of the tune
            self.assertEqual(len({n % 12 for _, n in ons}), 4)
            sent = []
            with mock.patch.object(pg, "LIVE_OUT", mock.Mock(raw=lambda data: sent.append(bytes(data)))), \
                    mock.patch.object(pg, "keyboard_busy", return_value=None):
                self.assertTrue(band.start("band_song", [0.02, 0.06, 0.10, 0.14], time.monotonic(), "run-1"))
                band.stop("another-run")                   # a late stop of the run before: this one plays on
                band.thread.join(timeout=2)
            self.assertEqual(sum(1 for b in sent if b[0] == 0x91 and b[2]), 4)
            self.assertIn(bytes([0xB1, 123, 0]), sent)     # nothing left sounding at the end
            getattr(pg, "_SONG_CACHE", {}).clear()

    def test_a_song_with_the_tune_alone_has_no_band(self):
        import tempfile
        pg = self.pg
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pg, "MIDI_DIR", Path(d)), \
                mock.patch.object(pg, "load_shortcuts", return_value={}):
            self.band_file(d, two_tracks=False)
            getattr(pg, "_SONG_CACHE", {}).clear()
            band = pg.GameBand()
            self.assertIsNone(band.plan("band_song", 4))
            self.assertFalse(band.start("band_song", [1.0, 2.0], time.monotonic(), "x"))
            self.assertFalse(band.start("no_such_song", [1.0, 2.0], time.monotonic(), "x"))
            getattr(pg, "_SONG_CACHE", {}).clear()

    def test_a_fading_song_is_chosen_and_one_not_started_never(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "b", "total": 4, "level": "advanced"},
                 {"name": "c", "total": 2, "level": "beginner"}]
        progress = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}},
                    self.pg.progress_key("b"): {"parts": {"1": {"id": "b"}}}}
        got = self.call(songs, progress, {"a": 0.95, "b": 0.4})
        self.assertEqual((got["name"], got["why"]), ("b", "fading"))
        self.assertEqual(got["notes"][0], [60, 0.5])
        got = self.call(songs, progress, {"a": 0.95, "b": 0.9})
        self.assertEqual((got["name"], got["why"]), ("b", "least"))

    def test_nothing_started_plays_the_easiest(self):
        songs = [{"name": "hard", "total": 3, "level": "advanced"}, {"name": "long", "total": 9, "level": "beginner"},
                 {"name": "easy", "total": 4, "level": "beginner"}]
        got = self.call(songs, {}, {})
        self.assertEqual((got["name"], got["why"]), ("easy", "easiest"))
        self.assertEqual(self.call([], {}, {}), {"ok": False})

    def test_a_song_picked_on_the_screen_plays_whatever_the_rules(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "c", "total": 2, "level": "beginner"}]
        got = self.call(songs, {}, {}, song="a")
        self.assertEqual((got["name"], got["why"]), ("a", "chosen"))
        self.assertEqual(self.call(songs, {}, {}, song="nope"), {"ok": False})

    def test_other_song_passes_over_the_one_on_screen(self):
        songs = [{"name": "a", "total": 4, "level": "beginner"}, {"name": "b", "total": 4, "level": "advanced"},
                 {"name": "c", "total": 2, "level": "beginner"}]
        progress = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}},
                    self.pg.progress_key("b"): {"parts": {"1": {"id": "b"}}}}
        got = self.call(songs, progress, {"a": 0.95, "b": 0.9}, skip="b")
        self.assertEqual((got["name"], got["why"], got["only"]), ("a", "least", False), "the next started song")
        got = self.call(songs, progress, {"a": 0.95, "b": 0.4}, skip="b")
        self.assertEqual((got["name"], got["why"]), ("a", "least"), "with the only fading song on screen")
        one = {self.pg.progress_key("a"): {"parts": {"1": {"id": "a"}}}}
        got = self.call(songs, one, {"a": 0.95}, skip="a")
        self.assertEqual((got["name"], got["only"]), ("a", True), "a song never started is never the other one")
        got = self.call(songs, {}, {}, skip="c")
        self.assertEqual((got["name"], got["why"], got["only"]), ("a", "easiest", False), "none started: the next easiest")


if __name__ == "__main__":
    unittest.main()


class BlackKeysTouch(unittest.TestCase):
    """15.11.2026: a black key touched in the game played the white key under it."""

    def test_a_touch_on_a_black_key_plays_the_black_key(self):
        src = (Path(__file__).resolve().parent.parent / "device" / "ui" / "views_game.py").read_text()
        body = src[src.index("    def game_keys(self, lit, t):"):src.index("    def game_corner(self, t):")]
        self.assertIn("rects.insert(0, (pc, x, KEYS_Y, BW, BH))", body)
        self.assertNotIn("rects.append((pc, x, KEYS_Y, BW, BH))", body)
