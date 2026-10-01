"""Everything in the lesson engine that can be checked without a keyboard.

The engine only runs with a MIDI keyboard plugged in, so the playing itself cannot be
tested here. What can be tested is everything the playing rests on: how a song is cut
into parts, how a run is scored, when a part counts as steady, and whether every line
the player reads has a Hebrew version. Those are plain functions with no hardware, no
network and no files of their own, and they are where a mistake goes unnoticed the
longest, because a wrong number on the screen still looks like a number.

Run from the project folder:

    python3 -m unittest discover -s tests

mido and paho-mqtt have to be importable, because the engine imports them. On an
installed Armonico they already are.
"""
import ast
import random
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_game as game          # noqa: E402
import piano_hands as hands        # noqa: E402
import piano_lang as lang          # noqa: E402
import piano_numbers as numbers    # noqa: E402

SOURCE = (ROOT / "lessons" / "piano_game.py").read_text(encoding="utf-8")
SCRIPT = re.findall(r"<script>(.*?)</script>", SOURCE, re.S)[0]      # the lesson screen
SERVER = SOURCE.replace(SCRIPT, "")                                  # everything but it
SETTINGS = {"pace": "steady", "auto_tempo": True, "speed": 70, "lang": "he"}


def scored(hits, played, notes, starts, accuracy=90):
    """run_scores without an Engine: it reads nothing but self.starts."""
    return game.Engine.run_scores(types.SimpleNamespace(starts=starts), accuracy, hits, played, notes)


class Phrases(unittest.TestCase):
    """A song is cut where the music breathes, and no note may fall out in the cutting."""

    def test_nothing_to_cut(self):
        self.assertEqual(game.phrases([], []), [])

    def test_one_note_is_one_part(self):
        self.assertEqual(game.phrases([60], [None]), [[0]])

    def test_short_song_without_rhythm(self):
        # 5 to 7 notes with no rhythm made exactly two groups, and joining the short tail
        # to the group before it read one index and wrote another
        for n in (5, 6, 7):
            parts = game.phrases([60 + i for i in range(n)], [])
            self.assertEqual([i for p in parts for i in p], list(range(n)))

    def test_every_length_keeps_every_note(self):
        random.seed(7)
        for n in range(0, 121):
            song = [60 + (i % 12) for i in range(n)]
            for rhythm in (None, [], [(0, 0)] * n, [(0.2, 0.05)] * n,
                           [(random.choice([0.0, 0.1, 0.6]), 0.05) for _ in range(n)]):
                parts = game.phrases(song, rhythm)
                self.assertEqual([i for p in parts for i in p], list(range(n)),
                                 f"notes went missing: {n} notes, rhythm {rhythm[:2] if rhythm else rhythm}")

    def test_parts_stay_in_size(self):
        song = [60 + (i % 12) for i in range(60)]
        parts = game.phrases(song, [(0.2, 0.05)] * 60)
        self.assertTrue(all(len(p) <= game.MAX_PHRASE for p in parts))
        self.assertTrue(all(len(p) >= game.MIN_PHRASE for p in parts[:-1]))

    def test_a_rest_ends_a_part(self):
        gaps = [(0.1, 0.0)] * 12
        gaps[5] = (0.9, 0.0)                     # one long rest after the sixth note
        parts = game.phrases([60 + i for i in range(12)], gaps)
        self.assertIn(5, [p[-1] for p in parts])


class RunScores(unittest.TestCase):
    """The four numbers a full run is read by."""

    def test_nothing_played(self):
        r = scored([], [], 0, [])
        self.assertEqual((r["rhythm"], r["hesitations"], r["wrong"], r["reached"]), (None, None, 0, 0))

    def test_identical_timestamps(self):
        # every gap the same left a median of zero, and the rhythm was worked out by dividing by it
        r = scored([(i, 5.0) for i in range(5)], [60] * 5, 5, [0, 1, 2, 3, 4])
        self.assertIsNone(r["rhythm"])

    def test_too_few_gaps_to_judge(self):
        self.assertIsNone(scored([(0, 1.0), (1, 2.0)], [60, 62], 2, [0, 1])["rhythm"])

    def test_steady_playing_reads_as_steady(self):
        hits = [(i, 1.0 + i) for i in range(6)]
        r = scored(hits, [60] * 6, 6, list(range(6)))
        self.assertEqual((r["rhythm"], r["hesitations"]), (100, 0))

    def test_one_long_gap_is_a_hesitation(self):
        hits = [(0, 1.0), (1, 2.0), (2, 3.0), (3, 9.0), (4, 10.0), (5, 11.0)]
        r = scored(hits, [60] * 6, 6, list(range(6)))
        self.assertEqual(r["hesitations"], 1)
        self.assertLess(r["rhythm"], 100)

    def test_wrong_keys_are_the_presses_that_matched_nothing(self):
        r = scored([(0, 1.0), (1, 2.0)], [60, 61, 62, 63], 2, [0, 1])
        self.assertEqual(r["wrong"], 2)


class PartCount(unittest.TestCase):
    """When a part counts as steady, and what a slip does to it."""

    def test_a_new_part_starts_at_one(self):
        part = game.new_part(1000.0)
        self.assertEqual(part["streak"], 1)
        self.assertEqual(game.part_state(part), "settling")

    def test_two_clean_opening_runs_settle_a_new_part(self):
        part = game.new_part(1000.0)
        for _ in range(game.STEADY_STREAK - 1):
            game.schedule(part, True, 2000.0)
        self.assertEqual(game.part_state(part), "steady")
        self.assertFalse(game.needs_review(part))

    def test_a_slip_starts_the_count_over(self):
        part = game.new_part(1000.0)
        for _ in range(5):
            game.schedule(part, True, 2000.0)
        game.schedule(part, False, 3000.0)
        self.assertEqual(part["streak"], 0)
        self.assertEqual(game.part_state(part), "settling")
        self.assertEqual(part["errors"], 1)

    def test_a_steady_part_needs_three_clean_runs_after_a_slip(self):
        part = game.new_part(1000.0)
        game.schedule(part, False, 2000.0)
        for _ in range(game.STEADY_STREAK - 1):
            game.schedule(part, True, 3000.0)
        self.assertEqual(game.part_state(part), "settling")
        game.schedule(part, True, 4000.0)
        self.assertEqual(game.part_state(part), "steady")


class LessonPlan(unittest.TestCase):
    """What a lesson of a song does now, from what the song's record holds."""

    def test_a_song_never_played(self):
        plan = game.song_plan({}, 12, SETTINGS, 1000.0)
        self.assertEqual(plan["known"], [])
        self.assertEqual(plan["new"], [0, 1])                 # the steady pace brings two
        self.assertEqual(plan["states"], {"settling": 0, "steady": 0})

    def test_the_pace_sets_how_many_are_new(self):
        for pace, count in (("relaxed", 1), ("steady", 2), ("fast", 3)):
            plan = game.song_plan({}, 12, dict(SETTINGS, pace=pace), 1000.0)
            self.assertEqual(len(plan["new"]), count)

    def test_nothing_new_at_the_end_of_a_song(self):
        rec = {"parts": {str(k): game.new_part(1000.0) for k in range(4)}}
        self.assertEqual(game.song_plan(rec, 4, SETTINGS, 2000.0)["new"], [])

    def test_states_count_every_part_learned(self):
        rec = {"parts": {str(k): game.new_part(1000.0) for k in range(5)}}
        for k in ("0", "1"):
            for _ in range(game.STEADY_STREAK):
                game.schedule(rec["parts"][k], True, 2000.0)
        plan = game.song_plan(rec, 5, SETTINGS, 3000.0)
        self.assertEqual(plan["states"], {"settling": 3, "steady": 2})
        self.assertEqual(sum(plan["states"].values()), len(plan["known"]))

    def test_a_song_learned_before_the_parts_existed(self):
        plan = game.song_plan({"learned": 3, "last": 500.0}, 10, SETTINGS, 1000.0)
        self.assertEqual(plan["known"], [0, 1, 2])
        self.assertEqual(plan["new"], [3, 4])

    def test_more_learned_than_the_song_has(self):
        plan = game.song_plan({"learned": 50}, 5, SETTINGS, 1000.0)
        self.assertEqual(plan["known"], [0, 1, 2, 3, 4])
        self.assertEqual(plan["new"], [])

    def test_parts_saved_when_they_had_a_clock(self):
        rec = {"parts": {"0": {"due": 1, "stage": "review", "interval": 9, "speed": 80},
                         "1": {"due": 1, "stage": "learn", "interval": 1, "speed": 60}}}
        plan = game.song_plan(rec, 5, dict(SETTINGS, speed=70), 1000.0)
        self.assertNotIn("due", rec["parts"]["0"])
        self.assertNotIn("speed", rec["parts"]["0"])
        self.assertEqual(rec["parts"]["0"]["adj"], 10)        # 80 was ten above the slider's 70
        self.assertEqual(game.part_state(rec["parts"]["0"]), "steady")   # a long gap meant it held
        self.assertEqual(plan["due"], [1])


class Versions(unittest.TestCase):
    """The update check compares this version with the newest release on GitHub."""

    def test_reading_a_tag(self):
        self.assertEqual(game.version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(game.version_tuple("nonsense"), (0,))

    def test_newer_wins(self):
        self.assertGreater(game.version_tuple("1.0.1"), game.version_tuple("1.0.0"))
        self.assertGreater(game.version_tuple("1.10.0"), game.version_tuple("1.9.0"))
        self.assertEqual(game.version_tuple("v1.0.0"), game.version_tuple("1.0.0"))


class KeyNumbers(unittest.TestCase):
    """White key numbers, the way the screen and the lessons name a key."""

    def test_a_number_and_its_note_are_the_same_key(self):
        for n in range(1, numbers.KEYS + 1):
            self.assertEqual(numbers.white_number(numbers.white_to_midi(n)), n)

    def test_black_keys_have_no_number(self):
        # not every white key has a black one above it: E and B have none
        black = next(n for n in range(60, 72) if numbers.is_black(n))
        self.assertIsNone(numbers.white_number(black))

    def test_a_black_key_drops_to_the_white_on_its_left(self):
        black = next(n for n in range(60, 72) if numbers.is_black(n))
        self.assertEqual(numbers.nearest_white(black), black - 1)
        self.assertEqual(numbers.nearest_white(black - 1), black - 1)


class Hands(unittest.TestCase):
    """Which hand and finger plays each note."""

    def test_no_notes_no_plan(self):
        self.assertIsNone(hands.plan_hands([]))

    def test_a_finger_for_every_note(self):
        song = [60, 62, 64, 65, 67, 65, 64, 62, 60]
        plan = hands.plan_hands(song)
        self.assertEqual(len(plan["fingers"]), len(song))
        self.assertEqual(len(plan["where"]), len(song))
        self.assertEqual(len(plan["opening"]), 2)
        for hand, finger, _stretch in plan["fingers"]:
            self.assertIn(hand, ("L", "R"))
            self.assertIn(finger, range(1, 6))

    def test_a_song_wider_than_two_hands_says_so(self):
        # the lesson catches this and runs on numbers alone
        with self.assertRaises(ValueError):
            hands.plan_hands([21, 108, 60, 61])


class Language(unittest.TestCase):
    """Hebrew, and the way a Hebrew line is laid out for Telegram."""

    def test_a_line_with_no_hebrew_stays_english(self):
        self.assertEqual(lang.translate("he", "nothing here has hebrew"), "nothing here has hebrew")

    def test_values_are_filled_in(self):
        self.assertIn("5", lang.translate("en", "Part {k}", k=5))

    def test_one_and_many(self):
        self.assertNotEqual(lang.plural("en", 1, "{n} part", "{n} parts"),
                            lang.plural("en", 2, "{n} part", "{n} parts"))

    def test_a_hebrew_line_is_marked_right_to_left(self):
        out = lang.telegram("🎹 שיעור פסנתר")
        self.assertTrue(out.startswith(lang.RLM))
        self.assertTrue(out.rstrip().endswith("🎹"))          # the emoji moves to where the reading ends

    def test_key_numbers_keep_their_order(self):
        self.assertIn(lang.LRM, lang.telegram("הקלידים הם 17 16 15#"))

    def test_an_english_line_is_left_alone(self):
        self.assertEqual(lang.telegram("Piano lesson: ode_to_joy"), "Piano lesson: ode_to_joy")


class Translations(unittest.TestCase):
    """Every line the player reads has a Hebrew version, in the engine and on the screen."""

    def test_every_engine_line_has_hebrew(self):
        missing = []
        for node in ast.walk(ast.parse(SOURCE)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None)
            args = ([node.args[0]] if name == "tr" and node.args else
                    node.args[1:3] if name == "trn" and len(node.args) >= 3 else [])
            for a in args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value not in lang.HE:
                    missing.append(f"line {node.lineno}: {a.value!r}")
        self.assertEqual(missing, [])

    def screen_dictionary(self):
        start = SOURCE.index("const HE = {")
        block = SOURCE[start:SOURCE.index("\n};", start)]
        return (set(re.findall(r"'((?:[^'\\]|\\.)*)'\s*:", block))
                | set(re.findall(r'"((?:[^"\\]|\\.)*)"\s*:', block)))

    def test_every_screen_line_has_hebrew(self):
        known = self.screen_dictionary()
        used = set(re.findall(r"\bt\(\s*'((?:[^'\\]|\\.)*)'", SCRIPT))
        for m in re.finditer(r"\btn\(\s*[^,]+?,\s*'((?:[^'\\]|\\.)*)'\s*,\s*'((?:[^'\\]|\\.)*)'", SCRIPT):
            used |= {m.group(1), m.group(2)}
        self.assertEqual(sorted(used - known), [])

    def test_every_written_block_has_hebrew(self):
        # the pages the reader actually reads: the method, the about page and the footer
        known = self.screen_dictionary()
        missing = []
        for _, fragment in re.findall(r'data-tkey="(content\.|foot\.)[^"]*"[^>]*>(.*?)</(?:p|div|span)>', SERVER, re.S):
            for text in re.split(r"<[^>]+>", fragment):
                text = text.strip()
                if len(text) > 12 and text not in known:
                    missing.append(text[:60])
        self.assertEqual(missing, [])


class Screen(unittest.TestCase):
    """The screen and the engine have to agree on what they send each other."""

    def test_the_summary_sends_what_the_card_reads(self):
        start = SOURCE.index('summary={"id"')
        sent = set(re.findall(r'"([a-z_]+)":', SOURCE[start:SOURCE.index('"complete":', start) + 40]))
        read = set(re.findall(r"\bsm\.([a-z_]+)\b", SCRIPT)) - {"textcontent"}
        self.assertEqual(sorted(read - sent), [])

    def test_every_address_the_screen_calls_is_served(self):
        called = {re.sub(r"\$\{.*?\}", "", c).split("?")[0]
                  for c in re.findall(r"(?:fetch|post)\(\s*'(/[^']*)'", SCRIPT)}
        self.assertEqual(sorted(c for c in called if c not in SERVER), [])


class EditingTools(unittest.TestCase):
    """The screen's own editing tools are off unless they were asked for."""

    def test_the_page_carries_the_switch(self):
        self.assertIn("__EDITMODE__", game.PAGE)
        self.assertIn("const EDIT_ALLOWED = __EDITMODE__;", game.PAGE)

    def test_the_server_fills_the_switch_in(self):
        self.assertIn('.replace("__EDITMODE__", "true" if EDIT_FLAG.exists() else "false")', SERVER)

    def test_the_tools_come_off_the_page_when_it_is_off(self):
        guard = game.PAGE[game.PAGE.index("const EDIT_ALLOWED"):][:400]
        self.assertIn("if (!EDIT_ALLOWED)", guard)
        self.assertIn("edToggle.remove()", guard)
        self.assertIn("setEditMode", guard)

    def test_saving_an_edit_is_refused_while_it_is_off(self):
        route = SERVER[SERVER.index('elif path == "/text-edit":'):][:400]
        self.assertIn("EDIT_FLAG.exists()", route)


class BugFixes(unittest.TestCase):
    def test_the_search_pages_are_reachable_but_not_downloadable(self):
        """The search itself was refused as "only the archives that are searched"."""
        for url in (game.MUTOPIA_SEARCH.format("minuet"), game.COMMONS_API + "?action=query"):
            self.assertTrue(url.startswith(game.SEARCH_PREFIXES))
            self.assertFalse(url.startswith(game.DOWNLOAD_PREFIXES))
        with self.assertRaises(ValueError):
            game._get(game.MUTOPIA_SEARCH.format("minuet"))          # a file download never takes a search address
        with self.assertRaises(ValueError):
            game._get("https://example.com/", search=True)

    def test_a_hebrew_title_does_not_break_the_midi_file(self):
        import tempfile
        events = [[0.0, "u", {"mode": "demo", "title": "🔹 חלק 1"}], [0.1, "d", 60, 90], [0.5, "d", 60, 0],
                  [1.0, "u", {"mode": "wait", "title": "🔹 חלק 1"}], [1.2, "k", 62, 80], [1.4, "k", 62, 0]]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.mid"
            game.Recorder.to_midi(events).save(str(f))
            self.assertGreater(f.stat().st_size, 40)
            import mido
            self.assertEqual(len(mido.MidiFile(str(f)).tracks), 2)

    def test_a_pause_is_not_counted_as_a_mistake(self):
        route = SERVER[SERVER.index("def answer(self, seg, early=None):"):][:2500]
        self.assertIn("if key is not None:", route[route.index("if key is None or key != seg[i]:"):][:400])

    def test_a_hebrew_file_name_fits_in_a_header(self):
        """http.server writes headers as Latin-1: a Hebrew song name made the download fail."""
        v = game.attachment("שיר-abc.mid")
        v.encode("latin-1")
        self.assertIn("filename*=UTF-8''", v)

    def _piano(self, d):
        """A data folder with a main profile and one called Dan, who has progress, and a song."""
        import json
        (d / "profiles" / "pabcdef01").mkdir(parents=True)
        (d / "songs").mkdir()
        (d / "songs" / "x.mid").write_bytes(b"MThd")
        (d / "lessons.json").write_text('{"main": 1}')
        (d / "profiles" / "pabcdef01" / "lessons.json").write_text('{"dan": 2}')
        (d / "profiles" / "pabcdef01" / "history.json").write_text("[]")
        return {"active": "pabcdef01", "list": [{"id": "main", "name": ""}, {"id": "pabcdef01", "name": "דן"}]}

    def _patched(self, d):
        from unittest import mock
        return mock.patch.multiple(game, DATA_DIR=d, PROFILES_FILE=d / "profiles.json", MIDI_DIR=d / "songs",
                                   RESTORE_FILE=d / ".restore.zip")

    def test_a_backup_holds_the_signed_in_profile_only_and_restores_into_it(self):
        import io, json, tempfile, zipfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            profs = self._piano(d)
            with self._patched(d):
                game.save_profiles(profs)
                raw = game.backup_zip()
                names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
                self.assertIn("backup.json", names)
                self.assertIn("songs/x.mid", names)
                self.assertFalse(any(n.startswith("profiles") for n in names))      # nobody else's data
                self.assertEqual(json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("lessons.json")), {"dan": 2})
                name = game.backup_name()
                self.assertIn("דן", name)
                self.assertIn("filename*=UTF-8''", game.attachment(name))
                game.attachment(name).encode("latin-1")                               # fits in a header
                info = game.restore_inspect(raw)
                self.assertEqual([(p["id"], p["name"], p["exists"]) for p in info["profiles"]], [("pabcdef01", "דן", True)])
                (d / "profiles" / "pabcdef01" / "lessons.json").write_text("{}")
                game.save_profiles(dict(profs, active="main"))                       # somebody else is at the piano
                out = game.restore_into("pabcdef01", True)
                self.assertEqual(out["id"], "pabcdef01")
                self.assertEqual(json.loads((d / "profiles" / "pabcdef01" / "lessons.json").read_text()), {"dan": 2})
                self.assertEqual(json.loads((d / "lessons.json").read_text()), {"main": 1})   # the other profile is untouched
                self.assertEqual(game.load_profiles()["active"], "pabcdef01")                 # and Dan is at the piano now

    def test_a_deleted_profile_is_made_again_by_its_restore(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            profs = self._piano(d)
            with self._patched(d):
                game.save_profiles(profs)
                raw = game.backup_zip()
                game.save_profiles({"active": "main", "list": [{"id": "main", "name": ""}]})   # Dan was deleted
                import shutil
                shutil.rmtree(d / "profiles")
                info = game.restore_inspect(raw)
                self.assertFalse(info["profiles"][0]["exists"])
                game.restore_into("pabcdef01", True)
                now = game.load_profiles()
                self.assertEqual([p["id"] for p in now["list"]], ["main", "pabcdef01"])
                self.assertEqual(now["active"], "pabcdef01")
                self.assertTrue((d / "profiles" / "pabcdef01" / "lessons.json").exists())

    def test_a_zip_that_is_not_this_programs_backup_is_refused(self):
        import io, json, tempfile, zipfile

        def make(files):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for n, c in files.items():
                    z.writestr(n, c)
            return buf.getvalue()

        man = json.dumps({"app": "armonico", "format": 2, "profile": {"id": "main", "name": ""}})
        bad = {
            "a text file": b"not a zip at all",
            "an unknown file": make({"backup.json": man, "lessons.json": "{}", "notes.txt": "x"}),
            "someone else's zip": make({"photo.jpg": "x", "doc.docx": "y"}),
            "the wrong app": make({"backup.json": json.dumps({"app": "other", "format": 2, "profile": {"id": "main"}}), "lessons.json": "{}"}),
            "a newer format": make({"backup.json": json.dumps({"app": "armonico", "format": 99, "profile": {"id": "main"}}), "lessons.json": "{}"}),
            "damaged progress": make({"backup.json": man, "lessons.json": "[1, 2]"}),
            "a song that is not midi": make({"backup.json": man, "songs/x.mid": "hello"}),
            "a recording without events": make({"backup.json": man, "recordings/abc-1.json": "{}"}),
        }
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self._patched(d):
                for what, raw in bad.items():
                    with self.subTest(what):
                        with self.assertRaises(ValueError):
                            game.restore_inspect(raw)
                        self.assertFalse((d / ".restore.zip").exists())               # nothing of it was kept
                ok = make({"backup.json": man, "lessons.json": "{}", "songs/x.mid": b"MThd"})
                game.save_profiles({"active": "main", "list": [{"id": "main", "name": ""}]})
                self.assertEqual(game.restore_inspect(ok)["songs"], 1)

    def test_the_band_never_starts_before_the_players_first_key(self):
        """An arrangement can play a whole verse before its melody track enters (a 54 second 'intro'
        in one song). It used to play at the start of the run, before any key was touched."""
        import types
        on = lambda ch, n, v=80: bytes([0x90 | ch, n, v])
        band = [(0.0, bytes([0xC1, 40])), (0.0, bytes([0xB1, 7, 90])), (1.0, bytes([0xB1, 7, 100])),
                (2.0, on(1, 60)), (3.0, bytes([0x80 | 1, 60, 0])),             # the verse before the melody
                (10.0, on(1, 64)), (10.5, bytes([0x80 | 1, 64, 0]))]            # under the first melody note
        fake = types.SimpleNamespace(band=band, starts=[10.0, 11.0])
        plan = game.Engine.band_plan(fake, [72, 74], 0)
        self.assertEqual(plan["intro"], [])
        self.assertEqual(sorted(plan["setup"]), sorted([bytes([0xC1, 40]), bytes([0xB1, 7, 100])]))   # the last value of each
        self.assertEqual([d for _, d in plan["segs"][0]], [on(1, 64), bytes([0x80 | 1, 64, 0])])

    def test_restore_refuses_a_zip_with_an_unsafe_name(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.json", "{}")
        with self.assertRaises(ValueError):
            game._backup_names(zipfile.ZipFile(io.BytesIO(buf.getvalue())))


if __name__ == "__main__":
    unittest.main()
