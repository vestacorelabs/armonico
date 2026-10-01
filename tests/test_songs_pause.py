"""The songs list's pause is quick both ways: the ▶/‖ sign changes on the touch itself (the
bridge's answer only confirms it, and wins after 3 s if it disagrees), the bridge cuts the copy the song goes
on from at pause time instead of at resume, and waits for the player to die instead of a fixed 0.2 s.

The lesson engine and the song player: the weakest skill counts only what was
practiced, a song can be paused and resumed from the same place (the bridge's piano/pause and
bridge/midi_seek.py), the device's ▶ pauses, resumes or switches songs, the tricky-step title names
the two notes, and the About text says what the lab is and that the characters and the sky are
design only.
"""
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_bridge import Bridge
import subprocess
import tempfile
sys.path.insert(0, str(ROOT / "lessons"))
sys.path.insert(0, str(ROOT / "bridge"))
import piano_learner as L
try:
    import mido
except ImportError:                 # pragma: no cover
    mido = None


class BridgePauseSpeed(Bridge):
    def setUp(self):
        super().setUp()
        seek = self.t / "seek.sh"                   # stands in for midi_seek.py: records the second, copies the file
        seek.write_text('#!/bin/bash\necho "SEEK $3 $(date +%s.%N)" >> "$T/pub.log"; cp "$1" "$2"\n')
        seek.chmod(0o755)
        self.env.update(PYTHON="bash", MIDI_SEEK=str(seek))

    def test_the_copy_is_cut_once_at_pause_time_and_resume_does_not_cut_again(self):
        self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.6\n'
                'pause_request pause; sleep 0.4\n'
                'pause_request resume; sleep 0.5\n'
                'stop_now "end"; wait')
        log = self.pubs()
        self.assertEqual(len(re.findall(r"SEEK ", log)), 1)
        self.assertRegex(log, r"APLAY .*piano_song_seek_[0-9.]+\.mid")

    def test_pause_and_resume_wait_no_fixed_fifth_of_a_second(self):
        text = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        a = text.index("pause_song() {")
        b = text.index("pause_request() {")
        self.assertNotIn("sleep 0.2", text[a:b])
        # no sleep in the pause path or the song's wait for the resume is longer than 0.05 s
        for m in re.finditer(r"sleep ([0-9.]+)", text[a:b]):
            self.assertLessEqual(float(m.group(1)), 0.05)
        loop = text[text.index('local play_file="$FILE" base=0'):text.index("# the flag is gone = the alarm replaced")]
        for m in re.finditer(r"sleep ([0-9.]+)", loop):
            self.assertLessEqual(float(m.group(1)), 0.05)

    def test_pause_to_resume_to_first_note_is_quick(self):
        # pause, then resume at once: the time from the resume to the player's next start
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.6\n'
                      't0=$EPOCHREALTIME; pause_request pause; t1=$EPOCHREALTIME\n'
                      'sleep 0.3\n'
                      't2=$EPOCHREALTIME; pause_request resume\n'
                      'for i in $(seq 1 100); do n=$(grep -c "^APLAY.*seek_" "$T/pub.log" 2>/dev/null); [ "${n:-0}" -ge 1 ] && break; sleep 0.01; done\n'
                      't3=$EPOCHREALTIME\n'
                      'echo "PAUSE_MS $(awk -v a=${t0/,/.} -v b=${t1/,/.} \'BEGIN{printf "%d",(b-a)*1000}\')"\n'
                      'echo "RESUME_MS $(awk -v a=${t2/,/.} -v b=${t3/,/.} \'BEGIN{printf "%d",(b-a)*1000}\')"\n'
                      'stop_now "end"; wait')
        pause_ms = int(re.search(r"PAUSE_MS (\d+)", out).group(1))
        resume_ms = int(re.search(r"RESUME_MS (\d+)", out).group(1))
        print(f"\npause call {pause_ms} ms, resume to the player's start {resume_ms} ms")
        self.assertLess(pause_ms, 600)      # includes the stubbed panic play and the publish
        self.assertLess(resume_ms, 300)

    def test_a_pause_straight_after_a_resume_pauses_again(self):
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.6\n'
                      'pause_request pause; sleep 0.3\n'
                      'pause_request resume; pause_request pause; sleep 0.4\n'
                      '[ -f "$FLAG_PAUSE" ] && echo STILL_PAUSED\n'
                      'echo "PLAYERS $(pgrep -fc "aplaymid[i] -p")"\n'
                      'stop_now "end"; wait')
        self.assertIn("STILL_PAUSED", out)
        self.assertIn("PLAYERS 0", out)

    def test_a_stop_or_the_alarm_still_takes_over_from_a_paused_song(self):
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.6\n'
                      'pause_request pause; sleep 0.3\n'
                      'stop_now "x"; sleep 0.3\n'
                      '[ -f "$FLAG_SONG" ] || echo NO_SONG; wait')
        self.assertIn("NO_SONG", out)


class DeviceSign(unittest.TestCase):
    def screen(self):
        sys.path.insert(0, str(ROOT))
        from test_device_ui import load_screen
        mod = load_screen()
        s = mod.Screen.__new__(mod.Screen)
        s.song_paused, s.paused_bridge, s.paused_hint = False, False, None
        s.mqtt = None
        return mod, s

    def test_the_sign_changes_on_the_touch_before_the_broker_answers(self):
        _, s = self.screen()
        s.hint_paused(True)
        self.assertTrue(s.paused_state())

    def test_an_older_state_from_the_broker_does_not_flicker_the_sign_back(self):
        mod, s = self.screen()
        s.hint_paused(True)
        s.paused_bridge = False            # what the bridge said before the touch arrives late
        hint = s.paused_hint
        self.assertTrue(hint[0])
        self.assertTrue(s.paused_state())

    def test_the_bridge_wins_when_it_still_disagrees_after_3_seconds(self):
        _, s = self.screen()
        s.hint_paused(True)
        s.paused_bridge = False
        s.paused_hint = (True, time.monotonic() - 0.01)      # the hold is over
        self.assertFalse(s.paused_state())
        self.assertIsNone(s.paused_hint)

    def test_a_message_that_agrees_ends_the_hold(self):
        _, s = self.screen()
        s.hint_paused(True)
        s.handle("piano/play/paused", "on")
        self.assertTrue(s.song_paused)
        self.assertIsNone(s.paused_hint)

    def test_a_message_that_disagrees_is_held_back_for_the_3_seconds(self):
        _, s = self.screen()
        s.hint_paused(True)
        s.handle("piano/play/paused", "off")          # the retained state from before the touch
        self.assertTrue(s.song_paused)
        self.assertFalse(s.paused_bridge)


if __name__ == "__main__":
    unittest.main()


class WeakestSkill(unittest.TestCase):
    def test_nothing_practiced_says_nothing(self):
        self.assertIsNone(L.weakest({}))
        self.assertIsNone(L.weakest(None))

    def test_a_skill_with_no_data_is_not_the_weakest(self):
        """Before, black keys unpracticed showed as the weakest at the start value, 30%."""
        sk = {"leap": 0.8, "memory": 0.7}
        self.assertEqual(L.weakest(sk), {"skill": "memory", "level": 0.7})
        self.assertNotEqual(L.weakest(sk)["skill"], "black")

    def test_a_practiced_weak_skill_still_shows(self):
        sk = {"black": 0.2, "leap": 0.8}
        self.assertEqual(L.weakest(sk)["skill"], "black")

    def test_fluency_counts_once_it_was_measured(self):
        self.assertEqual(L.weakest({"black": 0.5})["skill"], "black")
        self.assertEqual(L.weakest({"black": 0.5, "fluency": 0.2})["skill"], "fluency")

    def test_bad_values_are_ignored(self):
        self.assertIsNone(L.weakest({"black": "x", "leap": None, "long": True}))


@unittest.skipIf(mido is None, "mido is not installed")
class MidiSeek(unittest.TestCase):
    def make(self, d):
        m = mido.MidiFile(ticks_per_beat=480)
        t = mido.MidiTrack()
        m.tracks.append(t)
        t.append(mido.Message("program_change", program=5, time=0))
        t.append(mido.Message("control_change", control=7, value=90, time=0))
        for i in range(8):                      # a note every half second, each lasting that long
            t.append(mido.Message("note_on", note=60 + i, velocity=80, time=0))
            t.append(mido.Message("note_off", note=60 + i, velocity=0, time=480))
        src = Path(d) / "a.mid"
        m.save(src)
        return src

    def test_the_copy_begins_at_the_second_and_keeps_the_sound_setup(self):
        import midi_seek
        with tempfile.TemporaryDirectory() as d:
            src, dst = self.make(d), Path(d) / "b.mid"
            midi_seek.seek(str(src), str(dst), 2.25)    # note 64 sounds then: dropped with its end
            out = mido.MidiFile(str(dst))
            msgs = [m for m in out if not m.is_meta]
            self.assertEqual([m.type for m in msgs[:2]], ["program_change", "control_change"])
            notes = [m.note for m in msgs if m.type == "note_on"]
            self.assertEqual(notes, [65, 66, 67])
            self.assertEqual(len([m for m in msgs if m.type in ("note_on", "note_off")]), 6)
            self.assertAlmostEqual(out.length, 1.75, places=2)

    def test_the_script_runs_from_the_command_line_and_fails_on_a_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = self.make(d), Path(d) / "b.mid"
            ok = subprocess.run([sys.executable, str(ROOT / "bridge" / "midi_seek.py"), str(src), str(dst), "1"])
            self.assertEqual(ok.returncode, 0)
            self.assertTrue(dst.exists())
            bad = subprocess.run([sys.executable, str(ROOT / "bridge" / "midi_seek.py"), str(Path(d) / "no.mid"),
                                  str(dst), "1"], capture_output=True)
            self.assertNotEqual(bad.returncode, 0)


class PauseSong(Bridge):
    def setUp(self):
        super().setUp()
        seek = self.t / "seek.sh"                   # stands in for midi_seek.py: records the second, copies the file
        seek.write_text('#!/bin/bash\necho "SEEK $3" >> "$T/pub.log"; cp "$1" "$2"\n')
        seek.chmod(0o755)
        self.env.update(PYTHON="bash", MIDI_SEEK=str(seek))

    def test_a_song_pauses_and_goes_on_from_the_same_second(self):
        self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.8\n'
                'pause_request pause; sleep 0.6\n'
                '[ -f "$FLAG_PAUSE" ] && echo PAUSED_FLAG; [ -f "$FLAG_SONG" ] && echo SONG_KEPT\n'
                'pause_request resume; sleep 0.8\n'
                'stop_now "end of test"; wait')
        log = self.pubs()
        self.assertIn("piano/play/paused -m on", log)
        self.assertIn("piano/play/paused -m off", log)
        seek = re.findall(r"SEEK ([0-9.]+)", log)
        self.assertEqual(len(seek), 1)
        self.assertGreater(float(seek[0]), 0.5)      # about the 0.8 s it had played
        self.assertLess(float(seek[0]), 1.5)
        self.assertIn("wake_up.mid", log)            # the first play of the file
        self.assertRegex(log, r"piano_song_seek_[0-9.]+\.mid")    # and the one that goes on from the copy cut at the pause

    def test_pause_is_flagged_and_the_song_stays_playing_on_the_broker(self):
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.8\n'
                      'pause_request toggle; sleep 0.5\n'
                      '[ -f "$FLAG_PAUSE" ] && echo PAUSED; [ -f "$FLAG_SONG" ] && echo SONG\n'
                      'pause_request toggle; sleep 0.5\n'
                      '[ -f "$FLAG_PAUSE" ] || echo RESUMED\n'
                      'stop_now "end of test"; wait')
        self.assertIn("PAUSED", out)
        self.assertIn("SONG", out)
        self.assertIn("RESUMED", out)

    def test_a_stop_ends_a_paused_song(self):
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.8\n'
                      'pause_request pause; sleep 0.5\n'
                      'stop_now "while paused"; sleep 0.5\n'
                      '[ -f "$FLAG_SONG" ] || echo NO_SONG; [ -f "$FLAG_PAUSE" ] || echo NO_PAUSE; wait')
        self.assertIn("NO_SONG", out)
        self.assertIn("NO_PAUSE", out)
        self.assertIn("piano/play/paused -m off", self.pubs())
        self.assertIn("piano/play/stopped_by -m while paused", self.pubs())

    def test_the_alarm_is_never_paused(self):
        out = self.sh('touch "$FLAG_ALARM" "$FLAG_SONG"; pause_request pause\n'
                      '[ -f "$FLAG_PAUSE" ] && echo PAUSED || echo NOT_PAUSED')
        self.assertIn("NOT_PAUSED", out)

    def test_pause_with_nothing_playing_does_nothing(self):
        out = self.sh('pause_request pause; pause_request resume; [ -f "$FLAG_PAUSE" ] && echo PAUSED || echo NONE')
        self.assertIn("NONE", out)
        self.assertNotIn("piano/play/paused -m on", self.pubs())

    def test_the_alarm_takes_over_from_a_paused_song(self):
        out = self.sh('APLAY_SECS=3 start_song "wake_up" & sleep 0.8\n'
                      'pause_request pause; sleep 0.4\n'
                      'start_alarm "wake_up" & sleep 0.8\n'
                      '[ -f "$FLAG_ALARM" ] && echo ALARM_ON\n'
                      'stop_now "end of test"; sleep 0.4; [ -f "$FLAG_ALARM" ] || echo ALARM_STOPPED; wait')
        self.assertIn("ALARM_ON", out)
        self.assertIn("ALARM_STOPPED", out)

    def test_the_pause_topic_is_subscribed_and_documented(self):
        text = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        self.assertIn('-t "piano/pause"', text)
        self.assertIn("piano/pause)", text)
        self.assertIn("`piano/pause`", (ROOT / "docs" / "mqtt.md").read_text())
        self.assertIn("bridge/midi_seek.py", (ROOT / "install.sh").read_text())


class DeviceSongButton(unittest.TestCase):
    """The ▶ of a song in the device's list, against stand-ins for the screen."""
    def make(self, playing, name, paused=False, alarm=False):
        sys.path.insert(0, str(ROOT))
        from device.ui import views_main
        v = views_main.MainViews()
        v.screen = mock.Mock()
        v.screen.songs.return_value = [{"name": "ode_to_joy"}, {"name": "wake_up"}]
        v.screen.sel_song = 0
        v.screen.song, v.screen.alarm = playing, alarm
        v.screen.song_name, v.screen.song_paused = name, paused
        v.screen.paused_state.return_value = paused
        v.say = mock.Mock()
        v.tr = lambda k, **kw: k
        return v

    def sent(self, v):
        return [c.args for c in v.screen.pub.call_args_list]

    def test_play_on_a_song_that_is_not_playing_starts_it(self):
        v = self.make(False, "")
        v.songs_play()
        self.assertEqual(self.sent(v), [("piano/play", "ode_to_joy|Armonico Pi")])

    def test_play_on_the_song_playing_pauses_it(self):
        v = self.make(True, "ode_to_joy")
        v.songs_play()
        self.assertEqual(self.sent(v), [("piano/pause", "pause")])

    def test_play_on_the_paused_song_resumes_it(self):
        v = self.make(True, "ode_to_joy", paused=True)
        v.songs_play()
        self.assertEqual(self.sent(v), [("piano/pause", "resume")])

    def test_play_on_another_song_stops_the_current_one_and_starts_the_new(self):
        v = self.make(True, "wake_up")
        v.songs_play()
        sent = self.sent(v)
        self.assertEqual(sent[0][0], "piano/stop")
        self.assertEqual(sent[1], ("piano/play", "ode_to_joy|Armonico Pi"))

    def test_the_names_match_whatever_the_spaces_and_case(self):
        v = self.make(True, "Ode to joy")
        self.assertTrue(v.song_is_playing("ode_to_joy"))
        self.assertFalse(v.song_is_playing("wake_up"))
        v.screen.paused_state.return_value = True
        self.assertFalse(v.song_is_playing("ode_to_joy"))     # paused: the ▶ shows again


class Texts(unittest.TestCase):
    def test_the_tricky_step_names_its_two_notes_in_both_languages(self):
        import piano_lang
        key = "🎯 Part {k}: practicing the move between note {a} and note {b}"
        self.assertEqual(piano_lang.HE[key].format(k=2, a=3, b=4), "🎯 חלק 2: תרגול המעבר בין צליל 3 לצליל 4")
        src = (ROOT / "lessons" / "piano_game.py").read_text()
        self.assertIn(key, src)
        self.assertNotIn("a tricky step\"", src)

    def test_about_says_what_the_lab_is_and_that_the_sky_is_design(self):
        src = (ROOT / "lessons" / "piano_game.py").read_text()
        self.assertIn("It is not a registered business", src)
        self.assertIn("The characters and the sky on the Armonico Pi screen are decoration, separate from the learning", src)
        self.assertIn("היא לא עסק רשום", src)
        self.assertIn("נפרדים מהלמידה", src)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("not a registered business", readme)
        self.assertNotIn("venture", readme.lower())


if __name__ == "__main__":
    unittest.main()
