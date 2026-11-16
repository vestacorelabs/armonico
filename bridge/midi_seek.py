#!/usr/bin/env python3
"""midi_seek.py - a copy of a MIDI file that begins at a given second, for the bridge's pause.

    midi_seek.py IN.mid OUT.mid SECONDS

aplaymidi cannot start in the middle of a file, so a paused song is resumed from a file cut here:
what sets up the sound (program changes, controllers such as volume and pedal, pitch bend, sysex)
before SECONDS is kept, put at the start, and the notes before it are dropped. A note still held at
SECONDS is dropped with its end, so the song goes on from the next note. Times are real seconds,
tempo changes included (mido's own timing); the copy has one track at a fixed tempo."""
import sys

import mido

TICKS = 480
TEMPO = 500000                      # 480 ticks per half second: 960 ticks a second


def seek(src, dst, offset):
    mid = mido.MidiFile(src)
    held = set()                    # (channel, note) sounding at the cut: its note_off is dropped
    setup, rest = [], []
    t = 0.0
    for msg in mid:                 # msg.time is the delta in seconds
        t += msg.time
        if msg.is_meta:
            continue
        if msg.type in ("note_on", "note_off"):
            on = msg.type == "note_on" and msg.velocity > 0
            k = (msg.channel, msg.note)
            if t < offset:
                held.add(k) if on else held.discard(k)
            elif on:
                rest.append((t - offset, msg))
            elif k in held:
                held.discard(k)     # the end of a note that began before the cut
            else:
                rest.append((t - offset, msg))
        elif t < offset:
            setup.append(msg)       # the last word before the cut on every controller wins in order
        else:
            rest.append((t - offset, msg))
    out = mido.MidiFile(type=0, ticks_per_beat=TICKS)
    track = mido.MidiTrack()
    out.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=TEMPO, time=0))
    for msg in setup:
        track.append(msg.copy(time=0))
    last = 0
    for at, msg in rest:
        tick = int(round(at * 960))
        track.append(msg.copy(time=max(0, tick - last)))
        last = max(last, tick)
    track.append(mido.MetaMessage("end_of_track", time=0))
    out.save(dst)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: midi_seek.py IN.mid OUT.mid SECONDS")
    try:
        seek(sys.argv[1], sys.argv[2], max(0.0, float(sys.argv[3])))
    except Exception as e:      # the bridge plays from the start when this fails
        sys.exit(f"midi_seek: {e}")
