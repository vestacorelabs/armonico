#!/usr/bin/env python3
"""
install_music.py - classical music played on the keyboard while the installer works.

It proves the USB connection works in both directions before anything else is
installed: the keyboard plays (output), and pressing any key stops the music (input).

Only the Python standard library is used, because it runs before the venv exists.
The pieces are MIDI files in music/, excerpts of public domain editions.

  install_music.py detect         prints "PORT<tab>NAME" of the first keyboard found
  install_music.py play PORT      plays random pieces until told to stop
                                    SIGTERM or SIGINT  stop now
                                    SIGUSR1            finish the current piece, then stop
                                    any key pressed    stop now
  install_music.py finale PORT    a short closing flourish
  install_music.py write DIR      writes the finale and the silence files, for testing

Exit codes of "play": 0 stopped as asked, 4 stopped by a key press, 1 playback failed.
"""
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

TPQ = 480                 # MIDI ticks per quarter note
GAP_BETWEEN = 1.5         # seconds of silence between two pieces
KEY_STOPPED = 4           # exit code when a key press stopped the music

# ----------------------------------------------------------------------
# Writing MIDI files
# ----------------------------------------------------------------------
NOTE_RE = re.compile(r"^([A-G])([#b]?)(-?\d)$")
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def pitch(name):
    """ "C4" -> 60, "F#5" -> 78, "Bb3" -> 58. Middle C is C4."""
    m = NOTE_RE.match(name)
    if not m:
        raise ValueError(f"bad note name: {name}")
    letter, accidental, octave = m.groups()
    shift = 1 if accidental == "#" else -1 if accidental == "b" else 0
    return (int(octave) + 1) * 12 + STEPS[letter] + shift


def vlq(n):
    """A number in the variable-length format MIDI uses for time gaps."""
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


class Piece:
    """Notes placed on a beat grid. One beat is one quarter note."""

    def __init__(self, title, composer, bpm):
        self.title, self.composer, self.bpm = title, composer, bpm
        self.events = []          # (tick, order, bytes): note-offs sort before note-ons

    def note(self, beat, length, midi, velocity):
        on = int(round(beat * TPQ))
        off = int(round((beat + length) * TPQ)) - 8     # a hair of air between repeated notes
        self.events.append((on, 1, bytes([0x90, midi, velocity])))
        self.events.append((max(off, on + 1), 0, bytes([0x80, midi, 0])))

    def line(self, beat, text, velocity):
        """Plays "E4:1 D#4:.5 r:.5 C4+E4+G4:2" from the given beat, returns where it ends.

        Each token is notes:length. Notes joined with + sound together, r is a rest.
        """
        for token in text.split():
            notes, length = token.split(":")
            length = float(length)
            if notes != "r":
                for n in notes.split("+"):
                    self.note(beat, length, pitch(n), velocity)
            beat += length
        return beat

    def data(self):
        """The piece as a standard MIDI file, format 0, piano on channel 1."""
        tempo = int(60_000_000 / self.bpm)
        track = vlq(0) + bytes([0xFF, 0x51, 0x03]) + tempo.to_bytes(3, "big")
        track += vlq(0) + bytes([0xC0, 0])                # grand piano
        last = 0
        for tick, _, msg in sorted(self.events, key=lambda e: (e[0], e[1])):
            track += vlq(tick - last) + msg
            last = tick
        track += vlq(TPQ) + bytes([0xFF, 0x2F, 0x00])
        head = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TPQ)
        return head + b"MTrk" + struct.pack(">I", len(track)) + track


# ----------------------------------------------------------------------
# The pieces: excerpts of public domain editions from the Mutopia Project,
# cut to 30 to 60 seconds and set to piano. Sources are listed in music/SOURCES.md.
# ----------------------------------------------------------------------
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
PIECES = [
    ("ode_to_joy", "Ode to Joy", "Beethoven"),
    ("minuet_in_g", "Minuet in G", "Petzold, from the Anna Magdalena Bach notebook"),
    ("fur_elise", "Für Elise", "Beethoven"),
    ("rondo_alla_turca", "Rondo alla Turca", "Mozart"),
    ("eine_kleine_nachtmusik", "Eine kleine Nachtmusik", "Mozart"),
    ("prelude_in_c", "Prelude in C", "J. S. Bach"),
]


def finale():
    """Ta-da: a rising arpeggio into a full C major chord."""
    p = Piece("Finale", "", 132)
    p.line(0, "C4:.25 E4:.25 G4:.25 C5:.25", 96)
    p.line(1, "C3+G3+C4+E4+G4+C5:2.5", 110)
    return p




def panic():
    """All notes off and pedal up on every channel, so nothing is left ringing."""
    p = Piece("Panic", "", 120)
    for ch in range(16):
        p.events.append((0, 0, bytes([0xB0 | ch, 64, 0])))
        p.events.append((0, 0, bytes([0xB0 | ch, 123, 0])))
        p.events.append((0, 0, bytes([0xB0 | ch, 120, 0])))
    return p


# ----------------------------------------------------------------------
# Finding the keyboard
# ----------------------------------------------------------------------
def detect():
    """First MIDI output that is a real device, from "aplaymidi -l"."""
    try:
        out = subprocess.run(["aplaymidi", "-l"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines()[1:]:
        m = re.match(r"\s*(\d+:\d+)\s+(.+?)\s{2,}", line + "  ")
        if m and "Midi Through" not in m.group(2):
            return m.group(1), m.group(2).strip()
    return None


# ----------------------------------------------------------------------
# Playing
# ----------------------------------------------------------------------
class Player:
    def __init__(self, port):
        self.port = port
        self.dir = tempfile.mkdtemp(prefix="install-music-")
        self.stop_now = threading.Event()
        self.finish_piece = threading.Event()
        self.key_pressed = threading.Event()
        self.proc = None
        self.listener = None

    def write(self, piece, name):
        path = os.path.join(self.dir, name + ".mid")
        with open(path, "wb") as f:
            f.write(piece.data())
        return path

    def listen(self):
        """Any key pressed on the keyboard stops the music. Proves input works too."""
        try:
            self.listener = subprocess.Popen(["aseqdump", "-p", self.port],
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True)
        except OSError:
            return
        for line in self.listener.stdout:
            m = re.search(r"Note on\s+\d+,\s*note\s+\d+,\s*velocity\s+(\d+)", line)
            if m and int(m.group(1)) > 0:
                self.key_pressed.set()
                self.stop_now.set()
                return

    def send(self, path):
        subprocess.run(["aplaymidi", "-p", self.port, path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def play_one(self, path):
        """Plays one file. True if it ran to the end, False if it was cut."""
        self.proc = subprocess.Popen(["aplaymidi", "-p", self.port, path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        started = time.time()
        while self.proc.poll() is None:
            if self.stop_now.is_set():
                self.proc.terminate()
                self.proc.wait()
                return False
            time.sleep(0.05)
        if self.proc.returncode != 0 and time.time() - started < 1:
            raise RuntimeError(self.proc.stderr.read().decode(errors="replace").strip()
                               or "aplaymidi failed")
        return True

    def run(self):
        silence = self.write(panic(), "panic")
        paths = [((title, composer), os.path.join(MUSIC_DIR, name + ".mid"))
                 for name, title, composer in PIECES
                 if os.path.isfile(os.path.join(MUSIC_DIR, name + ".mid"))]
        if not paths:
            raise RuntimeError(f"no music files in {MUSIC_DIR}")
        threading.Thread(target=self.listen, daemon=True).start()

        order, last = [], None
        try:
            while not self.stop_now.is_set():
                if not order:
                    order = random.sample(paths, len(paths))
                    if order[0] == last and len(order) > 1:   # never the same piece twice in a row
                        order.append(order.pop(0))
                last = order.pop(0)
                (title, composer), path = last
                print(f"  \u266a Now playing: {title} ({composer})", flush=True)
                if not self.play_one(path) or self.finish_piece.is_set():
                    break
                end = time.time() + GAP_BETWEEN
                while time.time() < end and not self.stop_now.is_set() \
                        and not self.finish_piece.is_set():
                    time.sleep(0.05)
                if self.finish_piece.is_set():
                    break
        finally:
            self.send(silence)
            if self.listener and self.listener.poll() is None:
                self.listener.terminate()
            shutil.rmtree(self.dir, ignore_errors=True)
        if self.key_pressed.is_set():
            print("  ♪ Stopped from the keyboard. Input works too.", flush=True)
            return KEY_STOPPED
        return 0


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]

    if cmd == "detect":
        found = detect()
        if not found:
            sys.exit(1)
        print(f"{found[0]}\t{found[1]}")

    elif cmd == "write" and len(sys.argv) == 3:
        os.makedirs(sys.argv[2], exist_ok=True)
        for fn in (finale, panic):
            piece = fn()
            with open(os.path.join(sys.argv[2], fn.__name__ + ".mid"), "wb") as f:
                f.write(piece.data())
            print(f"{fn.__name__}.mid  {piece.title}")

    elif cmd == "finale" and len(sys.argv) == 3:
        player = Player(sys.argv[2])
        try:
            player.send(player.write(finale(), "finale"))
        finally:
            shutil.rmtree(player.dir, ignore_errors=True)

    elif cmd == "play" and len(sys.argv) == 3:
        player = Player(sys.argv[2])
        signal.signal(signal.SIGTERM, lambda *_: player.stop_now.set())
        signal.signal(signal.SIGINT, lambda *_: player.stop_now.set())
        signal.signal(signal.SIGUSR1, lambda *_: player.finish_piece.set())
        try:
            sys.exit(player.run())
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            print(f"  ♪ The music could not play: {e}", flush=True)
            sys.exit(1)

    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
