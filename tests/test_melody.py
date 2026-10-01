"""Telling the tune from the accompaniment, and fitting a wide tune on the keyboard."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))

import piano_game as game          # noqa: E402
import piano_numbers as numbers    # noqa: E402

MUSIC = ROOT / "setup" / "music"


class TuneFromBothHands(unittest.TestCase):
    def test_fur_elise_opening_is_the_written_tune(self):
        # E D# E D# E B D C A | C E A B | E G# B C | E D# ... : no left-hand notes in between
        written = [76, 75, 76, 75, 76, 71, 74, 72, 69, 60, 64, 69, 71, 64, 68, 71, 72, 64, 76]
        got = [n for _, n, _ in numbers.melody_events(str(MUSIC / "fur_elise.mid"))][:len(written)]
        self.assertEqual(got, written)

    def test_no_song_jumps_up_and_down_by_octaves(self):
        for f in MUSIC.glob("*.mid"):
            notes = [n for _, n, _ in numbers.melody_events(str(f))]
            jumps = sum(1 for a, b in zip(notes, notes[1:]) if abs(a - b) >= 12)
            self.assertLess(jumps, len(notes) * 0.06, f.name)   # Für Elise leaps E4 to E5 in its own tune

    def test_a_chord_under_a_held_tune_note_is_left_out(self):
        # D, then C held while a chord with A on top comes in under it, then B
        events = [(0.0, 74, 0.6), (0.6, 72, 1.2), (0.9, 54, 1.2), (0.9, 62, 1.2), (0.9, 69, 1.2), (1.2, 71, 1.8)]
        self.assertEqual([n for _, n, _ in numbers.melody_line(events)], [74, 72, 71])

    def test_a_melody_alone_keeps_its_leaps(self):
        events = [(0.0, 72, 0.4), (0.5, 60, 0.9), (1.0, 72, 1.4), (1.5, 60, 1.9)]   # no overlaps at all
        self.assertEqual([n for _, n, _ in numbers.melody_line(events)], [72, 60, 72, 60])

    def test_a_real_drop_of_the_tune_stays(self):
        # the tune itself goes down an octave and stays there
        events = [(0.0, 76, 0.4), (0.0, 48, 2.0), (0.5, 64, 0.9), (1.0, 62, 1.4), (1.5, 60, 1.9), (2.0, 62, 2.4)]
        self.assertEqual([n for _, n, _ in numbers.melody_line(events)], [76, 64, 62, 60, 62])


class WideTune(unittest.TestCase):
    def test_parts_move_by_octaves_and_keep_their_notes(self):
        # two parts two octaves apart, on a 25-key keyboard (48..72)
        pitches = [60, 62, 64, 65, 67] + [84, 86, 88, 89, 91]
        rhythm = [(0.3, 0.05)] * 4 + [(0.3, 1.5)] + [(0.3, 0.05)] * 5
        with mock.patch.multiple(game, LOW=48, HIGH=72):
            shift, notes = game.fit_by_parts(pitches, rhythm)
        self.assertTrue(all(48 <= n <= 72 for n in notes))
        for part in (notes[:5], notes[5:]):
            moved = [b - a for a, b in zip(part, part[1:])]
            self.assertEqual(moved, [2, 2, 1, 2])       # the tune inside each part is unchanged


def _file(path, tracks, type_=1):
    """A small MIDI file: tracks is [(name, program, channel, [(note, start_beat, beats)])]."""
    import mido
    mid = mido.MidiFile(type=type_, ticks_per_beat=480)
    merged = []
    for name, program, ch, notes in tracks:
        ev = [(0, mido.Message("program_change", program=program, channel=ch))]
        for n, at, beats in notes:
            ev.append((int(at * 480), mido.Message("note_on", note=n, velocity=80, channel=ch)))
            ev.append((int((at + beats) * 480), mido.Message("note_off", note=n, velocity=0, channel=ch)))
        if type_ == 0:
            merged += ev
            continue
        tr = mido.MidiTrack([mido.MetaMessage("track_name", name=name)])
        tick = 0
        for t, m in sorted(ev, key=lambda e: e[0]):
            tr.append(m.copy(time=t - tick))
            tick = t
        mid.tracks.append(tr)
    if type_ == 0:
        tr, tick = mido.MidiTrack(), 0
        for t, m in sorted(merged, key=lambda e: e[0]):
            tr.append(m.copy(time=t - tick))
            tick = t
        mid.tracks.append(tr)
    mid.save(path)


class TuneFromTheBand(unittest.TestCase):
    TUNE = [(64, i, 1) for i in range(0, 24, 2)] + [(67, i, 1) for i in range(1, 24, 2)]
    PAD = [(n, i * 4, 4) for i in range(6) for n in (72, 76, 79)]        # a string pad held over the tune
    BASS = [(36, i, 1) for i in range(24)]

    def test_rondo_opening_leaves_the_left_hand_chord_out(self):
        got = [n for _, n, _ in numbers.melody_events(str(MUSIC / "rondo_alla_turca.mid"))][:10]
        self.assertEqual(got, [71, 69, 68, 69, 72, 74, 72, 71, 72, 76])   # B A G# A C D C B C E

    def test_one_track_with_a_channel_each_reads_the_tune_channel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "band.mid"
            _file(f, [("", 0, 0, self.TUNE), ("", 89, 1, self.PAD), ("", 33, 2, self.BASS)], type_=0)
            got = {n for _, n, _ in numbers.melody_events(str(f))}
        self.assertEqual(got, {64, 67})

    def test_the_names_beat_a_high_pad_track(self):
        import mido, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "named.mid"
            _file(f, [("Melody", 0, 0, self.TUNE), ("Pad", 89, 1, self.PAD), ("Bass", 33, 2, self.BASS)])
            self.assertEqual(numbers.melody_track(mido.MidiFile(str(f))), 0)

    def test_the_notes_decide_when_the_names_are_wrong(self):
        import mido, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wrong.mid"
            # the tune is named "Bass" and played on a bass program, the pad is named "Melody": the notes still win
            _file(f, [("Melody", 0, 1, self.PAD), ("Bass", 33, 0, self.TUNE), ("Piano", 0, 2, self.BASS)])
            self.assertEqual(numbers.melody_track(mido.MidiFile(str(f))), 1)

    def test_no_names_at_all(self):
        import mido, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "plain.mid"
            _file(f, [("", 0, 0, self.BASS), ("", 0, 1, self.PAD), ("", 0, 2, self.TUNE)])
            self.assertEqual(numbers.melody_track(mido.MidiFile(str(f))), 2)

    def test_a_short_high_flourish_is_not_the_tune(self):
        import mido, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "flourish.mid"
            _file(f, [("", 0, 0, self.TUNE * 3), ("", 0, 1, [(96, 0, 0.25), (98, 0.25, 0.25), (100, 0.5, 0.25)])])
            self.assertEqual(numbers.melody_track(mido.MidiFile(str(f))), 0)


if __name__ == "__main__":
    unittest.main()
