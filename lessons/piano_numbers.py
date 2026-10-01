#!/usr/bin/env python3
"""
piano_numbers.py - converts a MIDI file into white-key numbers for the connected keyboard.
Key 1 is the leftmost white key; black keys are written as the white on their left plus #.
The keyboard's range comes from KEY_LOWEST and KEY_HIGHEST, measured by the installer.

Usage:
  python3 piano_numbers.py song.mid              # list tracks + best transposition
  python3 piano_numbers.py song.mid --track 1    # pick a specific track
  python3 piano_numbers.py song.mid --json out.json
"""
import argparse, difflib, json, os, sys
import mido

LOW = int(os.environ.get("KEY_LOWEST", "36"))      # physical range of the keyboard,
HIGH = int(os.environ.get("KEY_HIGHEST", "96"))    # 36..96 is a 61-key keyboard
WHITE_STEPS = [0, 2, 4, 5, 7, 9, 11]
BLACK = {1, 3, 6, 8, 10}


def is_black(n):
    return n % 12 in BLACK


def _whites_up_to(n):
    """How many white keys there are from MIDI 0 up to and including n."""
    octave, step = divmod(n, 12)
    return octave * 7 + sum(1 for s in WHITE_STEPS if s <= step)


FIRST_WHITE = LOW + 1 if is_black(LOW) else LOW    # key number 1
_BEFORE = _whites_up_to(FIRST_WHITE - 1)
KEYS = _whites_up_to(HIGH) - _BEFORE               # white keys on the keyboard


def white_number(n):
    """MIDI note -> white key number, 1 being the leftmost white. Black keys return None."""
    if is_black(n):
        return None
    return _whites_up_to(n) - _BEFORE


def white_to_midi(number):
    """White key number -> MIDI note. The inverse of white_number."""
    octave, index = divmod(number + _BEFORE - 1, 7)
    return octave * 12 + WHITE_STEPS[index]


def nearest_white(n):
    return n - 1 if is_black(n) else n     # black keys drop to the white on their left


def read_tracks(path):
    mid = mido.MidiFile(path)
    tracks = []
    for i, tr in enumerate(mid.tracks):
        t, notes = 0, []
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", 0) != 9:
                notes.append((t, msg.note))
        if notes:
            name = next((m.name for m in tr if m.type == "track_name"), "")
            tracks.append((i, name, notes))
    return mid.ticks_per_beat, tracks


def melody(notes, window):
    """Skyline: for notes starting together keep only the highest one."""
    notes = sorted(notes)
    out = []
    for t, n in notes:
        if out and t - out[-1][0] <= window:
            if n > out[-1][1]:
                out[-1] = (out[-1][0], n)
        else:
            out.append((t, n))
    return out


def best_shift(pitches):
    """Tries -11..+12 plus whole octaves: fewest black keys, fits the keyboard,
    then keeps the melody closest to middle C."""
    if not pitches:
        raise ValueError("A song with no melody notes cannot be placed on the keyboard")
    median = sorted(pitches)[len(pitches) // 2]
    options = []
    for s in range(-11, 13):
        for octave in (-24, -12, 0, 12, 24):
            shift = s + octave
            moved = [p + shift for p in pitches]
            if min(moved) < LOW or max(moved) > HIGH:
                continue
            blacks = sum(is_black(p) for p in moved)
            options.append((blacks, abs(median + shift - 60), abs(shift), shift))
    if not options:
        # an exception, not an exit: the lesson engine runs this and must survive it
        raise ValueError(f"The melody is wider than the keyboard ({HIGH - LOW + 1} keys) and cannot fit")
    blacks, _, _, shift = min(options)
    return shift, blacks


def phrases(mel, tpb):
    """Splits the melody where the gap between notes is longer than 2 beats."""
    if not mel:
        return []
    groups, cur = [], [mel[0]]
    for prev, nxt in zip(mel, mel[1:]):
        if nxt[0] - prev[0] > 2 * tpb:
            groups.append(cur)
            cur = []
        cur.append(nxt)
    groups.append(cur)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("midi")
    ap.add_argument("--track", type=int, help="track index (default: the highest-pitched track, usually the melody)")
    ap.add_argument("--json", help="save the result for the learning game")
    a = ap.parse_args()

    tpb, tracks = read_tracks(a.midi)
    if not tracks:
        sys.exit("No notes found.")

    print("Tracks:")
    for i, name, notes in tracks:
        print(f"  {i}: {name or '(no name)'} - {len(notes)} notes")

    chosen = next((t for t in tracks if t[0] == a.track), None) if a.track is not None \
        else max(tracks, key=lambda t: sum(n for _, n in t[2]) / len(t[2]))
    if not chosen:
        sys.exit(f"Track {a.track} does not exist.")

    mel = melody(chosen[2], window=tpb // 8)
    try:
        shift, blacks = best_shift([n for _, n in mel])
    except ValueError as e:
        sys.exit(str(e))
    mel = [(t, n + shift) for t, n in mel]

    print(f"\nTrack {chosen[0]} | {len(mel)} notes | shift {shift:+d} semitones | black keys left: {blacks}\n")

    result = []
    for i, ph in enumerate(phrases(mel, tpb), 1):
        line = []
        for _, n in ph:
            num = white_number(nearest_white(n))
            line.append(f"{num}*" if is_black(n) else str(num))
            result.append({"phrase": i, "key": num, "midi": n, "was_black": is_black(n)})
        print(f"Phrase {i:>2}: " + " ".join(line))

    if blacks:
        print("\n* = originally a black key, replaced by the white key to its left")

    if a.json:
        with open(a.json, "w") as f:
            json.dump({"file": a.midi, "shift": shift, "notes": result}, f, indent=1)
        print(f"\nSaved to {a.json}")



# ---------------------------------------------------------------------------
# Rhythm: the melody with real durations, so a lesson sounds like music
# ---------------------------------------------------------------------------
def tempo_map(mid):
    """[(tick, tempo)] for the whole file, so one track can be read on its own clock."""
    marks, tick, tempo = [(0, 500000)], 0, 500000
    for msg in mido.merge_tracks(mid.tracks):
        tick += msg.time
        if msg.type == "set_tempo" and msg.tempo != tempo:
            tempo = msg.tempo
            marks.append((tick, tempo))
    return marks


def seconds_at(marks, tpb, tick):
    """Where a tick falls in seconds, through the tempo changes before it."""
    t, prev_tick, prev_tempo = 0.0, 0, marks[0][1]
    for mark_tick, mark_tempo in marks[1:]:
        if mark_tick >= tick:
            break
        t += mido.tick2second(mark_tick - prev_tick, tpb, prev_tempo)
        prev_tick, prev_tempo = mark_tick, mark_tempo
    return t + mido.tick2second(tick - prev_tick, tpb, prev_tempo)


def melody_events(path, track=None):
    """[(start_sec, midi_note, end_sec)] for the melody line of a MIDI file.

    An arranged file holds the tune and its accompaniment on separate tracks, and
    a bass note that falls in a gap between two tune notes is alone at that moment,
    so merging everything first would let it pass as part of the tune. The melody
    track is therefore read on its own, with the tempo taken from the whole file,
    since tempo usually lives on a conductor track that plays nothing.

    The absolute start times are what a lesson needs in order to begin in the
    middle of a song: the accompaniment has to be cut at the same second the
    chosen note is played.
    """
    mid = mido.MidiFile(path)
    if track is None and mid.type != 2 and len(mid.tracks) > 1:
        track = melody_track(mid)
    chosen = [mid.tracks[track]] if track is not None else mid.tracks

    marks = tempo_map(mid)
    open_notes, events = {}, []
    for tr in chosen:
        tick = 0
        for msg in tr:
            tick += msg.time
            if getattr(msg, "channel", 0) == 9:
                continue
            t = seconds_at(marks, mid.ticks_per_beat, tick)
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault(msg.note, []).append(t)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                starts = open_notes.get(msg.note)
                if starts:
                    events.append((starts.pop(0), msg.note, t))
    if not events:
        return []
    events.sort()

    # skyline again, this time on absolute seconds: notes starting together keep the highest
    line = []
    for start, note, end in events:
        if line and start - line[-1][0] <= 0.03:
            if note > line[-1][1]:
                line[-1] = (line[-1][0], note, end)
        else:
            line.append((start, note, end))
    return line


def timed_melody(path, track=None):
    """[(midi_note, duration_sec, gap_to_next_sec)] for the melody line of a MIDI file."""
    line = melody_events(path, track)
    out = []
    for i, (start, note, end) in enumerate(line):
        nxt = line[i + 1][0] if i + 1 < len(line) else end
        out.append((note, max(0.08, end - start), max(0.0, nxt - end)))
    return out


def rhythm_for(notes, path, track=None):
    """Borrows durations from a MIDI file for a key sequence learned by ear."""
    return rhythm_and_starts(notes, path, track)[0]


def rhythm_and_starts(notes, path, track=None):
    """Borrows durations from a MIDI file for a key sequence learned by ear.

    The sequence and the file rarely match note for note, so the two are aligned
    and only the matching stretches take their timing from the file. Returns both
    the (length, gap) plan and, for the same positions, the note's second inside
    the file, which is where the accompaniment has to be cut when practice starts
    in the middle.
    """
    line = melody_events(path, track)
    timed = timed_melody(path, track)
    if not timed:
        return None, None, 0

    # the sequence was learned in whatever octave suits the keyboard, so the file
    # is shifted to wherever it lines up best before the timing is borrowed
    best = (0, 0)
    for shift in range(-24, 25):
        moved = [n + shift for n, _, _ in timed]
        size = sum(b.size for b in difflib.SequenceMatcher(None, notes, moved).get_matching_blocks())
        if size > best[0]:
            best = (size, shift)
    if best[0] < max(4, len(notes) // 4):
        return None, None, 0

    midi_notes = [n + best[1] for n, _, _ in timed]
    plan = [None] * len(notes)
    starts = [None] * len(notes)
    for a, b, size in difflib.SequenceMatcher(None, notes, midi_notes).get_matching_blocks():
        for k in range(size):
            plan[a + k] = (timed[b + k][1], timed[b + k][2])
            starts[a + k] = line[b + k][0]
    if not any(plan):
        return None, None, 0
    return plan, starts, best[1]


def melody_track(mid):
    """Index of the track that carries the melody: the highest average pitch."""
    best, idx = -1, None
    for i, tr in enumerate(mid.tracks):
        notes = [m.note for m in tr
                 if m.type == "note_on" and m.velocity > 0 and getattr(m, "channel", 0) != 9]
        if notes:
            avg = sum(notes) / len(notes)
            if avg > best:
                best, idx = avg, i
    return idx


def tick_at_second(mid, seconds):
    """The tick position of a point in time, read through the file's own tempo map."""
    tempo, t, ticks = 500000, 0.0, 0
    for msg in mido.merge_tracks(mid.tracks):
        step = mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
        if t + step >= seconds:
            return ticks + int(mido.second2tick(max(0.0, seconds - t), mid.ticks_per_beat, tempo))
        t += step
        ticks += msg.time
        if msg.type == "set_tempo":
            tempo = msg.tempo
    return ticks


# messages that describe how the instrument is set up rather than what it plays:
# when a copy starts in the middle, these are carried over to its first moment
SETUP = ("program_change", "control_change", "pitchwheel", "set_tempo",
         "time_signature", "key_signature")


def backing_file(src, dst, speed=1.0, keep_melody=False, start_sec=0.0):
    """Writes a copy of the song with the melody silenced, for playing along to.

    Timing meta stays in place, so tempo and time signature survive even when the
    melody's own track is the one holding them. speed above 1 makes it slower.
    start_sec cuts the beginning off, so practice can start inside the song with
    the accompaniment coming in at the same place.
    """
    mid = mido.MidiFile(src)
    mel = melody_track(mid)
    cut = tick_at_second(mid, start_sec) if start_sec > 0 else 0
    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for i, tr in enumerate(mid.tracks):
        new = mido.MidiTrack()
        now, prev = 0, cut          # absolute tick of the message, and of the last one kept
        for msg in tr:
            now += msg.time
            if msg.type == "set_tempo" and speed != 1:
                msg = msg.copy(tempo=int(msg.tempo * speed))
            drop = i == mel and not keep_melody and msg.type in ("note_on", "note_off")
            if now < cut:
                # before the cut nothing sounds, but the instrument's settings are kept,
                # stacked at the very start so the copy opens with the right sound
                if msg.type in SETUP:
                    new.append(msg.copy(time=0))
                continue
            if drop:
                continue
            new.append(msg.copy(time=now - prev))
            prev = now
        out.tracks.append(new)
    out.save(dst)
    return dst


def band_events(path, skip_track, shift=0):
    """[(second, raw bytes)] for everything in the file except the melody's own notes.

    This is the accompaniment as single messages, so a lesson can release it bar by bar
    behind the player instead of running it as one closed file. The melody was moved to
    sit on the white keys, so every pitched note of the band is moved by the same shift,
    otherwise the band plays in another key. Drums are never moved, their note numbers
    are instruments. SysEx is left out on purpose: a reset meant for another brand of
    instrument has no business reaching this one in the middle of a lesson.
    """
    mid = mido.MidiFile(path)
    marks = tempo_map(mid)
    out = []
    for i, tr in enumerate(mid.tracks):
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.is_meta or msg.type == "sysex":
                continue
            is_note = msg.type in ("note_on", "note_off")
            if is_note and i == skip_track:
                continue
            if is_note and msg.channel != 9 and shift:
                moved = msg.note + shift
                if not 0 <= moved <= 127:
                    continue
                msg = msg.copy(note=moved)
            out.append((seconds_at(marks, mid.ticks_per_beat, tick), bytes(msg.bytes())))
    out.sort(key=lambda e: e[0])
    return out


if __name__ == "__main__":
    main()
