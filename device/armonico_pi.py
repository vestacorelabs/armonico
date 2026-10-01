#!/usr/bin/env python3
"""armonico_pi.py - the keys and the sound of the Armonico Pi device.

One process, so a key sounds with no hop between programs:

  * 13 keys and 4 buttons on GPIO, read with gpiozero (pull-up inside the Pi,
    a press pulls the pin to ground).
  * One ALSA sequencer client named "Armonico Pi" (MIDI_DEVICE in config.env), with one
    port that sends the keys and takes notes in. The bridge and the lesson engine find
    it by that name, exactly like a USB keyboard. What is written to it is played and is
    never sent back out, so nothing hears its own notes.
  * The lesson engine plays through amidi on a virtual raw MIDI device (snd-virmidi,
    AMIDI_DEVICE in config.env). This process listens to that device too, so the demos,
    the band, the keys of the lesson screen, its pedal and its volume all sound here.
  * A small sampler with a mixer of its own on SDL's default output (class SdlOut), from the Salamander Grand samples in the project
    (lossless, device/samples), rendered once to one WAV per key by "armonico_pi.py prepare".
    The other voices and the drums (device/samples/gm, rendered from FluidR3_GM) play on the
    same mixer, so nothing else runs for them: one voice for each of the 16 General MIDI
    families, chosen by a song's program change, and the drum kit on channel 10.

The stop button: one of the 4 buttons or one of the 13 keys, chosen on the device's screen
or the lesson screen (settings.json "stop_button": ok, up, down, menu, or key:0 to key:12
from C, ✓ by default). While the alarm rings or a song plays it only stops them (piano/stop)
and does nothing else, so a key chosen for it does not sound then; the rest of the time it
is an ordinary button or key.

The buttons are not notes. They go to MQTT for the screen:
  piano/device/button        menu | down | up | ok, and menu:hold ... after 0.8 s
  piano/device/button/up     the name of a button let go (☰ and ✓ held together on About)
  piano/device/power         press: the power button, so the screen asks before it shuts down
  piano/device/octave        the octave of the keys, retained
Commands it takes:
  piano/device/octave/set    1 to 7, or +1 / -1
  piano/device/expect        the note a lesson waits for: its key sounds at its height. -1 in a lesson
                             that waits for none yet, empty when it is over; dropped after 10 s unsent
  piano/device/pedal/set     on | off: the sustain pedal, a switch in the settings
  piano/device/voice/set     0 to 15: the voice of the keys and the touches, a General MIDI family
  piano/device/test          on | quiet | off: the hardware test is open. The keys then also say which key
                             they are (piano/device/test/key, 0 to 12), and a USB keyboard only says what
                             it played (piano/device/test/usb, JSON {"name", "note"}): it does not play.
                             quiet is the full check's guided steps: the same, and the keys play nothing
                             either, and the power button held 4 seconds does not shut down
  piano/device/keys/set      on | off, retained: the keys play or are silent (the screen's "Play with"
                             setting); the stop key still stops the alarm and songs when they are off
  piano/device/keyboard/set  retained JSON {"mode": internal|external|both, "name": "...",
                             "out": device|keyboard|both}: which keyboard plays (the keys or a USB
                             MIDI keyboard by its ALSA name), and where the sound comes out while a USB
                             keyboard is chosen: these speakers, the keyboard's own, or both
And it tells:
  piano/device/keyboards     retained JSON list of the USB MIDI keyboards connected now

  python3 armonico_pi.py            run (the armonico-device service)
  python3 armonico_pi.py prepare    render the samples and the fonts (the installer runs it)
"""
import shutil
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ENV = os.environ
APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(ENV.get("DATA_DIR", "/var/lib/armonico"))
DEV_DIR = DATA_DIR / "device"
SAMPLE_DIR = DEV_DIR / "samples"
FONT_DIR = DEV_DIR / "fonts"
SETTINGS_FILE = DEV_DIR / "settings.json"      # shared with the screen
SOURCE_SAMPLES = APP_DIR / "lessons" / "sounds" / "piano"
# The same recordings without loss (FLAC, 48 kHz, device/samples/SOURCE.md): the device renders
# from these, and from the lesson screen's MP3s only when one is missing
DEVICE_SAMPLES = APP_DIR / "device" / "samples"
GM_SAMPLES = DEVICE_SAMPLES / "gm"                 # the other voices and the drums (gm/SOURCE.md)
GM_DIR = DEV_DIR / "gm"                           # the same as WAV, written by prepare
# The 16 General MIDI families (programs 0-7, 8-15 ...): the folder of each, the grand piano first
FAMILIES = ("piano", "01-vibraphone", "02-organ", "03-guitar", "04-bass", "05-violin", "06-strings",
            "07-trumpet", "08-sax", "09-flute", "10-lead", "11-pad", "12-atmosphere", "13-kalimba",
            "14-steeldrums", "15-birds")
SOURCE_FONTS = APP_DIR / "lessons" / "fonts"

NAME = ENV.get("MIDI_DEVICE", "Armonico Pi")
MQTT_HOST = ENV.get("MQTT_HOST", "localhost")
MQTT_PORT = int(ENV.get("MQTT_PORT", "1883"))
MQTT_USER = ENV.get("MQTT_USER", "")
MQTT_PASS = ENV.get("MQTT_PASS", "")

# The wiring (the "Armonico Pi wiring" page): BCM numbers, the keys in the order C, C#, D ... B
KEY_PINS = [int(p) for p in ENV.get("DEVICE_KEY_PINS", "4,10,17,9,18,27,25,22,11,23,8,24,13").split(",")]
BUTTON_PINS = dict(x.split(":") for x in ENV.get("DEVICE_BUTTON_PINS", "menu:7,down:5,up:6,ok:12").split(","))
VELOCITY = int(ENV.get("DEVICE_VELOCITY", "127"))   # tact switches have no touch: every press is this loud, full by default
OCTAVE = int(ENV.get("DEVICE_OCTAVE", "4"))          # 4: the first key is middle C (60)
BOUNCE = 0.008                                       # seconds; a tact switch settles in about 5 ms
HOLD = 0.8                                           # a button held this long is a second action

LOW, HIGH = 21, 108                                  # A0 to C8, the range of the samples
RELEASE_MS = 220                                     # a released key fades this fast, like a damper
VOICES = 40
# The pedal. A key let go under the pedal rings on, but no longer to the end of its sample: the
# samples' tails are lifted for a key that is held (see prepare), and under a pedal that stays down
# (the device's pedal is a switch) every note piled up on the others. Such a note now dies away
# over PEDAL_DECAY seconds. And on the device's own channel the pedal is "smart": a note played
# after a pause of SMART_GAP seconds lets the notes left ringing go in SMART_FADE seconds, as a
# foot lifted and pressed again between two phrases. Notes played closer than that still ring together.
PEDAL_DECAY = 3.0
SMART_GAP = 0.6
SMART_FADE = 0.35
# Many notes at once are turned down together, so they never add up past the top and crackle:
# up to LIMIT_VOICES ringing notes nothing changes, above that the level goes down by the square root.
LIMIT_VOICES = 6
# The other voices and the drums: notes made from their samples when first played, kept in memory
# up to GM_BUDGET bytes, the least recently played let go first. The grand piano is not counted.
GM_BUDGET = 64 * 1024 * 1024
# The keys and the keys touched on the screen sound on a sampler channel of their own. The lesson
# engine's output comes in on channels 0 and up, and it ends lessons, replays and the band with
# "pedal up" on them: on channel 0 that let go of the device's pedal while the screen still said on,
# and the pedal was never heard. The MIDI the keys send out is still on channel 0.
DEV_CH = 13
# A USB keyboard sounds on sampler channels of its own, 16 + its MIDI channel: on channel 0 the lesson
# engine's "pedal up" and "all notes off" let go of the notes the player held, and a song's note at
# the same height cut the player's own.
USB_CH = 16
# The note a lesson waits for (piano/device/expect) is sent again every 3 s during a lesson (-1 while
# it waits for none): one not heard of for this long is a lesson that ended without its last word.
EXPECT_TTL = 10


def log(msg):
    print(msg, flush=True)


# The bridge's flags on this machine (bridge/piano_bridge.sh, RUN_DIR in its unit): they say whether the
# alarm or a song plays when the broker cannot. The folder belongs to the same user, so they can be read.
RUN_DIR = Path(ENV.get("RUN_DIR") or f"/run/{APP_DIR.name}")
PLAY_FLAGS = ("piano_alarm_active", "piano_song_active")


def local_playing():
    """True or False from the bridge's flags, None when they cannot be read (no bridge on this machine)."""
    try:
        if not RUN_DIR.is_dir():
            return None
        return any((RUN_DIR / f).exists() for f in PLAY_FLAGS)
    except OSError:
        return None


# Written by the screen program once its screen is open ("<pid> <driver>"), and as "exit <pid>" just
# before it ends on purpose (device/session.sh, screen.py): a pid that is gone without that word is a
# screen that crashed or was stopped.
SCREEN_READY = Path(ENV.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / f"{APP_DIR.name}-screen.ready"


def read_screen_ready():
    """(pid, ended on purpose) from the screen's file, (None, False) when there is none."""
    try:
        parts = SCREEN_READY.read_text().split()
    except OSError:
        return None, False
    if len(parts) >= 2 and parts[0] == "exit" and parts[1].isdigit():
        return int(parts[1]), True
    if parts and parts[0].isdigit():
        return int(parts[0]), False
    return None, False


_WARM_LOCK = threading.Lock()                        # Device.warm_soon


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    """Read, change and write under one lock: the screen and the lesson engine write this file too,
    and without it an octave change could put back a stop button chosen a moment earlier."""
    import fcntl
    try:
        DEV_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = load_settings()
            data.update(changes)
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(SETTINGS_FILE)
    except OSError as e:
        log(f"Settings not saved: {e}")


# ----------------------------------------------------------------------
# prepare: one WAV per key, and the fonts pygame can read
# ----------------------------------------------------------------------
SAMPLE_RATE = 48000                                  # the player's rate (Sampler: pre_init)
SAMPLE_PCS = {0: "C", 3: "Ds", 6: "Fs", 9: "A"}      # the recorded notes, every third semitone


def sample_for(note):
    """The recorded file closest to a note and the semitones to move it: never more than 1.
    The lossless FLAC when it is there, the MP3 otherwise."""
    best = None
    for n in range(note - 1, note + 2):
        if n % 12 in SAMPLE_PCS:
            name = f"{SAMPLE_PCS[n % 12]}{n // 12 - 1}"
            for f in (DEVICE_SAMPLES / f"{name}.flac", SOURCE_SAMPLES / f"{name}.mp3"):
                if f.exists():
                    if best is None or abs(note - n) < abs(note - best[1]):
                        best = (f, n)
                    break
    if best is None:
        return None, 0
    return best[0], note - best[1]


def prepare():
    if not shutil_which("ffmpeg"):
        sys.exit("ffmpeg is missing: sudo apt install ffmpeg")
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    # The recorded notes fall fast: middle C is 22 dB down after one second, so a key held down
    # sounded like a short tap that had stopped. The tail is lifted step by step (12 dB over the
    # first second, 3 more by the third), so a held key keeps ringing as on a piano; the strike
    # itself is untouched. The source is the lossless FLAC: on the lesson screen's MP3s (about
    # 39 kbps) the same lift raised their compression noise with the tail, and the notes sounded
    # harsh. The gain is updated every 64 samples (about 1.3 ms) and not once per decoded frame
    # (about 26 ms), so it rises smoothly, without steps heard as grain.
    # Files rendered by an older recipe are rendered again.
    sustain = "asetnsamples=n=64,volume='pow(10,(12*min(t,1)+3*max(0,min(t-1,2)))/20)':eval=frame"
    # 48 kHz, the rate the player runs at, so a file is loaded as it is (see Sampler._sound). The same
    # sound as before: only the last resampling moved from the player's load to ffmpeg's.
    recipe_text = sustain + " @48000 flac"
    recipe = SAMPLE_DIR / "recipe.txt"
    try:
        old = recipe.read_text()
    except OSError:
        old = ""
    if old != recipe_text:
        for f in SAMPLE_DIR.glob("*.wav"):
            f.unlink()
    made = 0
    for note in range(LOW, HIGH + 1):
        out = SAMPLE_DIR / f"{note}.wav"
        if out.exists():
            continue
        src, shift = sample_for(note)
        if src is None:
            continue
        # Moving the pitch by resampling also changes the length a little (6% for a semitone),
        # which a piano note hides. 7 seconds, faded at the end, keeps the memory small:
        # 88 mono files are about 50 MB.
        rate = (48000 if src.suffix == ".flac" else 44100) * 2 ** (shift / 12)
        af = f"asetrate={rate:.2f},aresample={SAMPLE_RATE},atrim=0:7,{sustain},afade=t=out:st=5.5:d=1.5"
        # into a temp file first: an install stopped half way left a cut file the next one kept
        tmp = out.with_name(f".{out.name}.part")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af,
                            "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", "-f", "wav", str(tmp)])
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            sys.exit(f"ffmpeg failed on {src.name}")
        tmp.replace(out)
        made += 1
    tmp = recipe.with_name(".recipe.txt.part")
    tmp.write_text(recipe_text)
    tmp.replace(recipe)
    log(f"Samples: {made} rendered, {len(list(SAMPLE_DIR.glob('*.wav')))} in {SAMPLE_DIR}")
    prepare_gm()

    # pygame reads TrueType, not woff2, and the Heebo files are variable fonts: one fixed
    # weight each, Latin and Hebrew merged into one file per weight
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
        from fontTools.merge import Merger
    except ImportError:
        sys.exit("fontTools is missing in the venv: pip install fonttools brotli")
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    # Medium and Bold are the weights of the new screens; Regular and ExtraBold stay for the
    # older views. The Hebrew letters are also kept in a file of their own for each new weight,
    # so Hebrew shows even when the two subsets refuse to merge into one file.
    for weight, label in ((400, "Regular"), (800, "ExtraBold"), (500, "Medium"), (700, "Bold")):
        target = FONT_DIR / f"Heebo-{label}.ttf"
        hebrew = FONT_DIR / f"Heebo-Hebrew-{label}.ttf"
        if target.exists() and (weight in (400, 800) or hebrew.exists()):
            continue
        parts = []
        for f in ("heebo-latin.woff2", "heebo-hebrew.woff2"):
            font = TTFont(SOURCE_FONTS / f)
            font = instantiateVariableFont(font, {"wght": weight})
            font.flavor = None
            part = FONT_DIR / f".{f}.{weight}.ttf"
            font.save(part)
            parts.append(str(part))
        if weight in (500, 700):
            shutil.copyfile(parts[1], hebrew)
        try:
            Merger().merge(parts).save(target)
        except Exception as e:                 # merging can refuse two subsets; Latin alone is enough
            log(f"Fonts: merging Heebo-{label} failed ({e}); Latin alone, Hebrew from its own file")
            Path(parts[0]).replace(target)
        for p in parts:
            Path(p).unlink(missing_ok=True)
    absent = [n for n in ("Heebo-Regular.ttf", "Heebo-Medium.ttf", "Heebo-Bold.ttf", "Heebo-Hebrew-Medium.ttf",
                          "Heebo-Hebrew-Bold.ttf") if not (FONT_DIR / n).exists()]
    if absent:
        sys.exit(f"Fonts were not created in {FONT_DIR}: {', '.join(absent)} (Hebrew would show as squares)")
    log(f"Fonts in {FONT_DIR}")


def prepare_gm():
    """The other voices and the drums, from FLAC to WAV as they are (the player reads WAV). The notes
    in between are made when they are first played, so this is quick and small."""
    made = 0
    if GM_SAMPLES.is_dir():
        for src in sorted(GM_SAMPLES.glob("*/*.flac")):
            out = GM_DIR / src.parent.name / (src.stem + ".wav")
            if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f".{out.name}.part")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1",
                                "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", "-f", "wav", str(tmp)])
            if r.returncode != 0:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ffmpeg failed on {src}")
            tmp.replace(out)
            made += 1
    log(f"Voices and drums: {made} prepared, {len(list(GM_DIR.glob('*/*.wav')))} in {GM_DIR}")


def shutil_which(cmd):
    from shutil import which
    return which(cmd)


# ----------------------------------------------------------------------
# The sampler
# ----------------------------------------------------------------------

def loudness(share):
    """A volume (0 to 1, as the screens set it) as the gain that sounds that loud. The ear hears level in
    decibels, so a straight share barely changed anything until the bottom: 50% was only 6 dB down.
    Cubed, as PulseAudio's own volumes are: 70% is about 9 dB down, 50% 18 dB, 25% 36 dB."""
    share = max(0.0, min(1.0, float(share)))
    return share ** 3

MIX_RATE = 48000                 # the mixer's rate and the samples' (prepare renders them at it)
MIX_BLOCK = 512                  # frames the sound card asks for at a time: about 11 ms from key to sound


class Voice:
    """One mixer channel: the calls the sampler makes of a pygame Channel (play, set_volume, fadeout,
    stop, get_busy), with every change of level spread over the block it falls in.

    SDL's own mixer, which the keys once played through, holds one level for a whole
    block of 512 frames and jumps to the next at its border. Every key let go (a fade of 220 ms is
    20 such jumps), every key struck again and every move of the limiter was a row of small steps,
    heard as crackling, the more so the faster the notes and the louder the headphones. Measured on
    the sampler itself through SDL's file output: of the clicks in a fast passage, nearly all fell
    exactly on those borders. Here a level moves sample by sample from where it was to where it goes."""

    def __init__(self, mixer):
        self.m = mixer
        self.data = None             # the sound playing: int16 mono samples at MIX_RATE
        self.pos = 0
        self.vol = 1.0               # the level asked for
        self.cur = 0.0               # the level the last block ended on
        self.left = None             # frames left of a fade out, and its length
        self.total = 0

    def play(self, snd):
        with self.m.lock:
            if self.data is not None and self.cur > 0:
                self.m.tail(self)    # a note cut for a new one dies away in a few ms, not at once
            self.data, self.pos, self.left, self.cur = snd, 0, None, self.vol

    def set_volume(self, v):
        self.vol = max(0.0, min(1.0, float(v)))

    def fadeout(self, ms):
        with self.m.lock:
            if self.data is not None:
                n = max(MIX_BLOCK // 4, int(MIX_RATE * ms / 1000))
                if self.left is None or n < self.left:
                    self.left, self.total = n, n

    def stop(self):
        self.fadeout(5)

    def get_busy(self):
        return self.data is not None


class Mixer:
    """The voices summed for the sound card, in the card's own callback, one block at a time."""

    def __init__(self, voices):
        self.lock = threading.Lock()
        self.voices = [Voice(self) for _ in range(voices)]
        self.tails = []              # [data, pos, level, frames left, length] of notes cut short

    def tail(self, v):
        n = int(MIX_RATE * 0.005)
        self.tails.append([v.data, v.pos, v.cur, n, n])

    def render(self, n):
        import numpy as np
        acc = np.zeros(n, np.float32)
        ramp = np.arange(1, n + 1, dtype=np.float32) / n
        with self.lock:
            for v in self.voices:
                if v.data is None:
                    continue
                seg = v.data[v.pos:v.pos + n]
                end = v.vol
                if v.left is not None:
                    end *= max(0.0, v.left - n) / v.total
                g = v.cur + (end - v.cur) * ramp[:len(seg)]
                acc[:len(seg)] += seg * g
                v.cur, v.pos = end, v.pos + n
                if v.left is not None:
                    v.left -= n
                if v.pos >= len(v.data) or (v.left is not None and v.left <= 0):
                    v.data, v.left = None, None
            keep = []
            for t in self.tails:
                data, pos, lvl, left, total = t
                seg = data[pos:pos + n]
                k = min(len(seg), left)
                if k > 0:
                    g = lvl * (left - np.arange(k, dtype=np.float32)) / total
                    acc[:k] += seg[:k] * g
                t[1], t[3] = pos + n, left - n
                if t[3] > 0 and t[1] < len(data):
                    keep.append(t)
            self.tails = keep
        np.clip(acc, -32768, 32767, out=acc)
        return acc.astype("<i2")

    def block(self, nbytes):
        """The next block for the card, nbytes long: interleaved 16-bit stereo, the same on both sides."""
        try:
            import numpy as np
            return np.repeat(self.render(nbytes // 4), 2).tobytes()
        except Exception:
            return bytes(nbytes)


class SdlOut:
    """The sound card through SDL itself (libSDL2, which pygame.mixer used), opened as SDL's default
    output: on ALSA that is the device "default" (or $AUDIODEV), PipeWire's default output, which
    follows the output chosen on the screen.

    Only with no name: SDL opens a device by name only if the name is in its own list, and its ALSA
    backend lists just the best prefix it finds, hw: first, so on the Pi the list holds the cards
    themselves and not "default" (src/audio/alsa/SDL_alsa_audio.c, ALSA_HotplugIteration; the ALSA
    backend does not set AllowsArbitraryDeviceNames, src/audio/SDL_audio.c, open_audio_device). pygame's
    AudioDevice takes a name only, and versions 70 and 71 opened nothing: the keys and songs were silent.
    With NULL, SDL asks its backend for its default, as SDL_mixer did."""

    def __init__(self, block):
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("SDL2-2.0") or "libSDL2-2.0.so.0")
        CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_int)

        class Spec(ctypes.Structure):           # SDL_AudioSpec (SDL2)
            _fields_ = [("freq", ctypes.c_int), ("format", ctypes.c_uint16), ("channels", ctypes.c_uint8),
                        ("silence", ctypes.c_uint8), ("samples", ctypes.c_uint16), ("padding", ctypes.c_uint16),
                        ("size", ctypes.c_uint32), ("callback", CB), ("userdata", ctypes.c_void_p)]

        self.dead = False                       # replaced by a new output: silence, the mixer is not read
        self.last = time.monotonic()            # the last time SDL asked for a block

        def fill(_userdata, stream, n):
            self.last = time.monotonic()
            data = bytes(n) if self.dead else block(n)
            ctypes.memmove(stream, data if len(data) == n else bytes(n), n)

        self.cb = CB(fill)                      # kept: SDL calls it for as long as the card is open
        lib.SDL_GetError.restype = ctypes.c_char_p
        lib.SDL_InitSubSystem.argtypes = [ctypes.c_uint32]
        lib.SDL_OpenAudioDevice.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Spec),
                                            ctypes.POINTER(Spec), ctypes.c_int]
        lib.SDL_OpenAudioDevice.restype = ctypes.c_uint32
        lib.SDL_PauseAudioDevice.argtypes = [ctypes.c_uint32, ctypes.c_int]
        lib.SDL_GetAudioDeviceStatus.argtypes = [ctypes.c_uint32]
        lib.SDL_GetAudioDeviceStatus.restype = ctypes.c_int
        lib.SDL_CloseAudioDevice.argtypes = [ctypes.c_uint32]
        if lib.SDL_InitSubSystem(0x10) != 0:    # SDL_INIT_AUDIO
            raise RuntimeError((lib.SDL_GetError() or b"").decode(errors="replace"))
        want = Spec(freq=MIX_RATE, format=0x8010, channels=2, samples=MIX_BLOCK, callback=self.cb)
        have = Spec()
        # 48 kHz, 16-bit stereo, 512 frames: anything else the card wants, SDL converts to
        self.id = lib.SDL_OpenAudioDevice(None, 0, ctypes.byref(want), ctypes.byref(have), 0)
        if not self.id:
            raise RuntimeError((lib.SDL_GetError() or b"").decode(errors="replace"))
        self.lib = lib
        lib.SDL_PauseAudioDevice(self.id, 0)

    STALL = 3.0      # seconds with no block asked for: the output is stuck (it asks about 94 times a second)

    def alive(self):
        """False when the output is gone: SDL stopped it after a write error (PipeWire or WirePlumber
        restarted, SDL_GetAudioDeviceStatus is then SDL_AUDIO_STOPPED), or its callback has not run for
        STALL seconds. Two cheap reads, no system call into the sound stack."""
        try:
            if self.lib.SDL_GetAudioDeviceStatus(self.id) == 0:
                return False
        except Exception:
            pass
        return time.monotonic() - self.last < self.STALL

    def close(self):
        """Silent at once, then closed in a thread of its own: closing waits for SDL's audio thread,
        which can be stuck in a write to a sound server that went away."""
        self.dead = True
        threading.Thread(target=self.lib.SDL_CloseAudioDevice, args=(self.id,), daemon=True).start()


class Sampler:
    """Plays MIDI bytes on the samples. A channel's program change picks its voice by General MIDI
    family: the piano family (and a channel that never chose) on the grand piano, each other family
    on a voice of its own (device/samples/gm), and channel 10 on the drum kit."""

    def __init__(self):
        os.environ.setdefault("SDL_AUDIODRIVER", "alsa")
        self.mixer = Mixer(VOICES)
        self.device = None           # the sound card, opened by _load
        # the card's callback runs in Python: it gets the interpreter within 2 ms of asking, not the
        # default 5, so a block is never late while another thread works
        sys.setswitchinterval(0.002)
        self.sounds = {}
        self.lock = threading.RLock()  # re-entrant: Device.set_voice checks and lets go under it
        self.playing = {}            # (channel, note) -> mixer channel number
        self.sustained = set()       # released while the pedal was down
        self.pedal = {}              # MIDI channel -> pedal down
        self.master = 1.0
        self.chans = []              # the mixer's channels, made once: pygame gives a new object each time
        self.started = {}            # mixer channel number -> when its note started (the oldest gives way)
        self.level = {}              # mixer channel number -> its level from velocity and volume, before the limiter
        self.fade = {}               # mixer channel number -> (gain at start, start, seconds): dying away under the pedal
        self.gain = {}               # mixer channel number -> where its own fade is now (1 when none)
        self.limit = 1.0             # the level of everything together, under 1 when many notes ring
        self.program = {}            # MIDI channel -> program, from program changes
        self.voice_of = {}           # MIDI channel -> family 0 to 15 chosen for it by the device (the keys)
        self.smart = set()           # MIDI channels with the smart pedal: the device's own
        self.last_on = {}            # MIDI channel -> when its last note started
        from collections import OrderedDict
        self.gm = OrderedDict()      # (folder, note) -> Sound, least recently played first
        self.gm_bytes = 0
        self.gm_size = {}            # (folder, note) -> its bytes
        self.gm_lock = threading.Lock()   # the keys, the songs and the warm-up all make notes
        self.gm_notes = {}           # folder -> the notes it has a sample for
        threading.Thread(target=self._load, daemon=True).start()
        threading.Thread(target=self._tick, daemon=True).start()

    def _load(self):
        # The sound card can be missing or busy at start. The keys still go out as MIDI meanwhile,
        # and the sound comes as soon as the card opens, without restarting the service.
        warned = False
        # 48 kHz stereo, 512 frames a block, on SDL's default output (PipeWire), as SDL's mixer had
        while self.device is None:
            try:
                self.device = SdlOut(self.mixer.block)
            except Exception as e:
                if not warned:
                    log(f"No sound card yet ({e}), trying again every 5 seconds. The keys still work as MIDI")
                    warned = True
                time.sleep(5)
        if warned:
            log("The sound card opened")
        self.chans = self.mixer.voices
        # middle C first, so the keys sound while the rest is still loading
        order = sorted(range(LOW, HIGH + 1), key=lambda n: abs(n - 66))
        for n in order:
            f = SAMPLE_DIR / f"{n}.wav"
            if f.exists():
                try:
                    self.sounds[n] = self._sound(f)
                except (OSError, EOFError, ValueError) as e:
                    log(f"Sample {f.name}: {e}")
        if not self.sounds:
            log(f"No samples in {SAMPLE_DIR}. Run: armonico_pi.py prepare")
        else:
            log(f"{len(self.sounds)} samples loaded")
        self._watch_output()

    OUTPUT_CHECK = 2          # seconds between two looks at the sound output
    OUTPUT_RETRY_MAX = 30     # the longest wait between two tries to open it again

    def _watch_output(self, rounds=None):
        """The output opened once stays with the PipeWire that was running then: after PipeWire or
        WirePlumber restarts, SDL stops it and the keys went silent until the service restarted. Every
        OUTPUT_CHECK seconds it is looked at (SdlOut.alive, two reads), and a lost one is opened again,
        waiting 2, 4, 8 ... up to OUTPUT_RETRY_MAX seconds between tries while the sound server is away.
        rounds: for the tests, how many looks before it returns."""
        wait = 2
        while rounds is None or rounds > 0:
            if rounds is not None:
                rounds -= 1
            time.sleep(self.OUTPUT_CHECK)
            dev = self.device
            if dev is None or dev.alive():
                wait = 2
                continue
            log("The sound output was lost (PipeWire restarted?): opening it again")
            dev.close()
            said = False
            while True:
                try:
                    self.device = SdlOut(self.mixer.block)
                    log("The sound output is open again")
                    wait = 2
                    break
                except Exception as e:
                    if not said:
                        log(f"The sound output did not open ({e}), trying again (2 s, then up to {self.OUTPUT_RETRY_MAX} s apart)")
                        said = True
                    time.sleep(wait)
                    wait = min(wait * 2, self.OUTPUT_RETRY_MAX)
                    if rounds is not None:
                        return

    def _sound(self, f):
        """One sample as the mixer plays it: 16-bit mono at MIX_RATE, kept once (about 0.7 MB for 7 s;
        the two sides are made only as each block goes out). A file at another rate is resampled, a
        stereo one is made mono."""
        import wave
        import numpy as np
        with wave.open(str(f), "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError("not 16 bit")
            nch, file_rate = w.getnchannels(), w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        if nch > 1:
            a = a.reshape(-1, nch).mean(axis=1).astype("<i2")
        if file_rate != MIX_RATE:
            pos = np.arange(0, len(a) - 1, file_rate / MIX_RATE)
            a = np.interp(pos, np.arange(len(a)), a.astype(np.float32)).astype("<i2")
        return a

    # ---- the other voices and the drums
    def family(self, ch):
        """The family a MIDI channel plays in: 0 is the grand piano, -1 the drums."""
        if ch in (9, USB_CH + 9):
            return -1
        if ch in self.voice_of:
            return self.voice_of[ch]
        return self.program.get(ch, 0) // 8

    def _gm_sound(self, folder, note):
        """A note of another voice, or of the drums: made from the nearest sample, moved in pitch by
        resampling, the first time it is played, and kept while memory allows. None when there is
        no sample near enough (an instrument does not play that high or low)."""
        with self.gm_lock:
            return self._gm_make(folder, note)

    def _gm_make(self, folder, note):
        key = (folder, note)
        snd = self.gm.get(key)
        if snd is not None:
            self.gm.move_to_end(key)
            return snd
        have = self.gm_notes.get(folder)
        if have is None:
            have = sorted(int(f.stem) for f in (GM_DIR / folder).glob("*.wav") if f.stem.isdigit())
            self.gm_notes[folder] = have
        if not have:
            return None
        if folder == "drums":
            if note not in have:
                return None
            src, shift = note, 0
        else:
            src = min(have, key=lambda n: abs(n - note))
            shift = note - src
            if abs(shift) > 12:
                return None
        try:
            import wave
            import numpy as np
            rate = MIX_RATE
            with wave.open(str(GM_DIR / folder / f"{src}.wav"), "rb") as w:
                if w.getsampwidth() != 2 or w.getnchannels() != 1:
                    log(f"Voice {folder} {src}: not 16 bit mono")
                    return None
                file_rate = w.getframerate()
                data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            # the pitch, and the mixer's own rate when the card opened at another (44.1 kHz)
            ratio = 2 ** (shift / 12) * file_rate / rate
            if abs(ratio - 1) > 1e-6:
                pos = np.arange(0, len(data) - 1, ratio)
                data = np.interp(pos, np.arange(len(data)), data.astype(np.float32)).astype("<i2")
            snd = np.ascontiguousarray(data, dtype="<i2")
        except (OSError, EOFError, ValueError, ImportError) as e:
            log(f"Voice {folder} {note}: {e}")
            return None
        self.gm[key] = snd
        self.gm_size[key] = data.nbytes
        self.gm_bytes += data.nbytes
        while self.gm_bytes > GM_BUDGET and len(self.gm) > 1:
            k, _old = self.gm.popitem(last=False)
            self.gm_bytes -= self.gm_size.pop(k, 0)
        return snd

    def sound_for(self, ch, note):
        fam = self.family(ch)
        if fam == 0:
            return self.sounds.get(note)
        if fam == -1:
            return self._gm_sound("drums", note)
        return self._gm_sound(FAMILIES[fam], note)

    # ---- notes
    def _free(self):
        """A mixer channel for a new note: a free one, or the one whose note started first."""
        free = next((i for i, c in enumerate(self.chans) if not c.get_busy()), None)
        if free is None:
            free = min(range(len(self.chans)), key=lambda i: self.started.get(i, 0))
        for key in [k for k, v in self.playing.items() if v == free]:
            self.playing.pop(key)
            self.sustained.discard(key)
        self.fade.pop(free, None)
        return free

    def _let_go(self, i, ms):
        """A note's own fade, handed to the mixer: no longer turned by the pedal or the limiter."""
        self.fade.pop(i, None)
        self.level.pop(i, None)
        self.gain.pop(i, None)
        self.chans[i].fadeout(ms)

    def note_on(self, ch, note, vel):
        if vel == 0:
            return self.note_off(ch, note)
        snd = self.sound_for(ch, note)
        if snd is None or not self.chans:
            return
        now = time.monotonic()
        with self.lock:
            # the smart pedal: a note after a pause lets go of the notes left ringing on the pedal
            if ch in self.smart and self.pedal.get(ch) and now - self.last_on.get(ch, 0) >= SMART_GAP:
                for key in [k for k in self.sustained if k[0] == ch]:
                    self.sustained.discard(key)
                    i = self.playing.pop(key, None)
                    if i is not None:
                        self._let_go(i, int(SMART_FADE * 1000))
            self.last_on[ch] = now
            old = self.playing.pop((ch, note), None)
            if old is not None:
                self._let_go(old, 30)            # the same key struck again
            self.sustained.discard((ch, note))
            i = self._free()
            lvl = (vel / 127) ** 1.6 * self.master
            self.level[i], self.gain[i], self.started[i] = lvl, 1.0, now
            mc = self.chans[i]
            mc.set_volume(lvl * self.limit)
            mc.play(snd)
            if self.family(ch) == -1:
                self.level.pop(i, None)          # a drum is not held: it plays to its end
                self.gain.pop(i, None)
            else:
                self.playing[(ch, note)] = i

    def note_off(self, ch, note):
        with self.lock:
            if self.pedal.get(ch):
                i = self.playing.get((ch, note))
                if i is not None and (ch, note) not in self.sustained:
                    self.sustained.add((ch, note))
                    self.fade[i] = (self.gain.get(i, 1.0), time.monotonic(), PEDAL_DECAY)
                return
            i = self.playing.pop((ch, note), None)
            if i is not None:
                self._let_go(i, RELEASE_MS)

    def set_pedal(self, ch, down):
        with self.lock:
            self.pedal[ch] = down
            if down:
                return
            for key in [k for k in self.sustained if k[0] == ch]:
                self.sustained.discard(key)
                i = self.playing.pop(key, None)
                if i is not None:
                    self._let_go(i, RELEASE_MS)

    def all_off(self, ch=None):
        with self.lock:
            for key in [k for k in self.playing if ch is None or k[0] == ch]:
                self._let_go(self.playing.pop(key), RELEASE_MS)
                self.sustained.discard(key)
            if ch in (None, 9):
                for i, c in enumerate(self.chans):   # the drums ring to their end: stopped here too
                    if i not in self.playing.values() and c.get_busy():
                        c.fadeout(RELEASE_MS)

    def _tick(self):
        """100 times a second while notes ring, 10 while none: the notes under the pedal die away, and
        the level of all of them follows how many ring together. Only the levels change here."""
        last_error = None
        while True:
            busy = False
            try:
                with self.lock:
                    if self.chans:
                        busy = any(c.get_busy() for c in self.chans)
                        # the notes held or ringing on the pedal; the short tails of keys let go and the drums
                        # are not counted, so a quick run without the pedal plays as it always did. Only the
                        # ones still sounding: a held key whose sample ended kept the rest turned down
                        ringing = sum(1 for i in self.level if self.chans[i].get_busy()) if busy else 0
                        want = 1.0 if ringing <= LIMIT_VOICES else (LIMIT_VOICES / ringing) ** 0.5
                        now = time.monotonic()
                        # down at once, back up over about half a second, so it is not heard pumping;
                        # with nothing held it is back at once
                        step = min(1.0, (now - getattr(self, "_ticked", now)) / 0.5)
                        self._ticked = now
                        self.limit = want if want < self.limit or not ringing else self.limit + (want - self.limit) * step
                        for i, (g0, t0, dur) in list(self.fade.items()):
                            x = (now - t0) / dur
                            if x >= 1:
                                self.fade.pop(i)
                                key = next((k for k, v in self.playing.items() if v == i), None)
                                if key is not None:
                                    self.playing.pop(key)
                                    self.sustained.discard(key)
                                self._let_go(i, 30)
                                continue
                            self.gain[i] = g0 * (1 - x) ** 2
                        for i, lvl in self.level.items():
                            self.chans[i].set_volume(lvl * self.gain.get(i, 1.0) * self.limit)
            except Exception as e:           # a sound card error must not end the levels for good
                if str(e) != last_error:
                    log(f"Sampler: {e}")
                    last_error = str(e)
                time.sleep(0.5)
                continue
            time.sleep(0.01 if busy else 0.1)

    def midi(self, data, base=0):
        """One complete MIDI message as bytes. base: USB_CH for a USB keyboard's, on channels of its own."""
        if not data:
            return
        st = data[0]
        if st == 0xF0:
            # Universal Master Volume, as the lesson screen's volume sends it
            if len(data) >= 8 and data[1:5] == bytes([0x7F, 0x7F, 0x04, 0x01]):
                self.master = loudness(((data[6] << 7) | data[5]) / 16383)
            return
        kind, ch = st & 0xF0, st & 0x0F
        if len(data) < 3 and kind not in (0xC0, 0xD0):
            return
        if ch == DEV_CH and not base:
            ch = 15                      # a song's own channel 14 sounds on 16: the device's keys keep theirs
        ch += base
        if kind == 0x90:
            self.note_on(ch, data[1], data[2])
        elif kind == 0x80:
            self.note_off(ch, data[1])
        elif kind == 0xC0 and len(data) >= 2:
            self.program[ch] = data[1] & 0x7F
        elif kind == 0xB0:
            if data[1] == 64:
                self.set_pedal(ch, data[2] >= 64)
            elif data[1] in (120, 123):
                self.all_off(ch)
            elif data[1] == 121:         # reset all controllers: back to the grand piano too
                self.program.pop(ch, None)


def split_midi(buf):
    """Raw MIDI bytes into whole messages, running status included."""
    out, i, status = [], 0, None
    while i < len(buf):
        b = buf[i]
        if b == 0xF0:
            j = buf.find(b"\xF7", i)
            if j < 0:
                break
            out.append(bytes(buf[i:j + 1]))
            i = j + 1
            continue
        if b >= 0xF8:                    # clock, active sensing: nothing to play
            i += 1
            continue
        if b & 0x80:
            status, i = b, i + 1
        if status is None:
            i += 1
            continue
        if status >= 0xF1:               # System Common: F1 and F3 have 1 data byte, F2 has 2, the rest none
            size = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)
        else:
            size = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
        if i + size > len(buf):
            break
        out.append(bytes([status]) + bytes(buf[i:i + size]))
        i += size
        if status >= 0xF1:
            status = None                # no running status after a System Common message
    return out


# ----------------------------------------------------------------------
# The ALSA port
# ----------------------------------------------------------------------
class Port:
    def __init__(self, sampler):
        from alsa_midi import SequencerClient, PortCaps, PortType
        self.am = __import__("alsa_midi")
        self.sampler = sampler
        self.client = SequencerClient(NAME)
        caps = PortCaps.READ | PortCaps.SUBS_READ | PortCaps.WRITE | PortCaps.SUBS_WRITE
        self.port = self.client.create_port(NAME, caps,
                                            PortType.MIDI_GENERIC | PortType.HARDWARE | PortType.APPLICATION)
        self.out_lock = threading.Lock()
        self.ext = set()                 # (client, port) of a USB keyboard connected in: its keys are forwarded
        self.on_external = None          # Device.external, for the messages from those
        # A second port, out to the USB keyboard's own sound: the lessons, the songs, the keys and
        # the touches go there too when its sound is chosen. Its own keys never go back to it (it
        # plays them itself), so it has a port of its own, apart from the one the bridge hears.
        self.out = self.client.create_port(NAME + " to keyboard", PortCaps.READ | PortCaps.SUBS_READ,
                                           PortType.MIDI_GENERIC | PortType.APPLICATION)
        self.ext_out = set()             # (client, port) of the USB keyboard's input, connected to self.out
        self.here, self.there = True, False   # the sound on these speakers, and on the keyboard
        log(f"ALSA client {self.client.client_id}: '{NAME}'")
        if not self.listen_virmidi():
            threading.Thread(target=self._virmidi_later, daemon=True).start()
        threading.Thread(target=self._input, daemon=True).start()

    def listen_virmidi(self, quiet=False):
        """The lesson engine's output (snd-virmidi) comes in here, to be played: the device AMIDI_DEVICE
        names (hw:7,0 is "Virtual Raw MIDI 7-0"), or the first one when it names none by number."""
        import re
        m = re.fullmatch(r"hw:(\d+),(\d+)(,\d+)?", ENV.get("AMIDI_DEVICE", "").strip())
        want = f"Virtual Raw MIDI {m.group(1)}-{m.group(2)}" if m else "Virtual Raw MIDI"
        try:
            for p in self.client.list_ports(input=True):
                if (p.client_name == want) if m else p.client_name.startswith(want):
                    self.port.connect_from(p)
                    log(f"Listening to {p.client_name} ({p.client_id}:{p.port_id}), the lesson engine's output")
                    return True
        except self.am.ALSAError as e:
            if not quiet:
                log(f"snd-virmidi: {e}")
        if not quiet:
            log(f"No {want} device yet: lessons will not sound here until it comes, looked for every 10 s. "
                "The installer loads it (modprobe snd-virmidi)")
        return False

    def _virmidi_later(self):
        """snd-virmidi loaded after this started: connected as soon as it is there."""
        while True:
            time.sleep(10)
            if self.listen_virmidi(quiet=True):
                return

    def send(self, data):
        """A key, out to everyone subscribed: the bridge and the lesson engine."""
        with self.out_lock:
            try:
                self.client.event_output(self.am.MidiBytesEvent(bytes(data)), port=self.port)
                self.client.drain_output()
            except self.am.ALSAError as e:
                log(f"Send failed: {e}")

    def _input(self):
        last_error = None
        while True:
            try:
                ev = self.client.event_input(prefer_bytes=True, timeout=1)
            except self.am.ALSAError as e:
                log(f"Input: {e}")
                time.sleep(0.5)
                continue
            except Exception as e:       # never the end of every note in
                if str(e) != last_error:
                    log(f"Input: {e}")
                    last_error = str(e)
                time.sleep(0.5)
                continue
            if ev is None:
                continue
            try:
                data = getattr(ev, "midi_bytes", None)
                if data:
                    src = getattr(ev, "source", None)
                    from_ext = src is not None and (src.client_id, src.port_id) in self.ext and self.on_external
                    for msg in split_midi(bytes(data)):
                        if from_ext:
                            self.on_external(msg)
                        else:            # the lesson engine's and the songs' notes
                            self.play(msg)
            except Exception as e:       # one message that failed to sound: the next ones still play
                if str(e) != last_error:
                    log(f"Input: {e}")
                    last_error = str(e)
                time.sleep(0.05)

    def play(self, msg):
        """A message to sound: on these speakers, on the USB keyboard, or both (here and there)."""
        if self.here:
            self.sampler.midi(msg)
        if self.there:
            self.to_keyboard(msg)

    def to_keyboard(self, msg):
        if not self.ext_out or not msg or msg[0] >= 0xF0:
            return
        with self.out_lock:
            try:
                self.client.event_output(self.am.MidiBytesEvent(bytes(msg)), port=self.out)
                self.client.drain_output()
            except self.am.ALSAError as e:
                log(f"To the keyboard: {e}")

    def keyboard_inputs(self):
        """{name: (client, port)} of the ports that take MIDI in, for the sound of a USB keyboard."""
        found = {}
        try:
            for p in self.client.list_ports(output=True, include_midi_through=False):
                if p.client_id == self.client.client_id or p.client_name.startswith(self.SKIP):
                    continue
                found.setdefault(p.client_name, (p.client_id, p.port_id))
        except self.am.ALSAError as e:
            log(f"Keyboards: {e}")
        return found

    def follow_out(self, want):
        """self.out connected to exactly the keyboard inputs in want."""
        if self.ext_out - want:
            for ch in range(16):         # nothing left ringing on it
                self.to_keyboard([0xB0 | ch, 123, 0])
        for addr in list(self.ext_out - want):
            try:
                self.out.disconnect_to(addr)
            except self.am.ALSAError:
                pass
            self.ext_out.discard(addr)
            log(f"USB keyboard {addr[0]}:{addr[1]} no longer sounds")
        for addr in want - self.ext_out:
            try:
                self.out.connect_to(addr)
                self.ext_out.add(addr)
                log(f"USB keyboard {addr[0]}:{addr[1]} sounds")
            except self.am.ALSAError as e:
                log(f"USB keyboard {addr}: {e}")

    SKIP = ("Virtual Raw MIDI", "Midi Through", "System")

    def keyboards(self):
        """{name: (client, port)} of the MIDI keyboards that can play in: USB keyboards and the like,
        not this program, not the lesson engine's virtual output, not ALSA's own."""
        found = {}
        try:
            for p in self.client.list_ports(input=True, include_midi_through=False):
                if p.client_id == self.client.client_id or p.client_name.startswith(self.SKIP):
                    continue
                found.setdefault(p.client_name, (p.client_id, p.port_id))
        except self.am.ALSAError as e:
            log(f"Keyboards: {e}")
        return found

    def follow_external(self, want):
        """Connected in from exactly the keyboards in want ({(client, port)}), the rest let go.
        Returns the ones let go."""
        gone = self.ext - want
        for addr in list(gone):
            try:
                self.port.disconnect_from(addr)
            except self.am.ALSAError:
                pass
            self.ext.discard(addr)
            log(f"USB keyboard {addr[0]}:{addr[1]} let go")
        for addr in want - self.ext:
            try:
                self.port.connect_from(addr)
                self.ext.add(addr)
                log(f"USB keyboard {addr[0]}:{addr[1]} plays in")
            except self.am.ALSAError as e:
                log(f"USB keyboard {addr}: {e}")
        return gone


# ----------------------------------------------------------------------
# GPIO and MQTT
# ----------------------------------------------------------------------
POWER_LOOK = 10          # seconds between two looks for the power button's input device
INPUT_SYS = Path("/sys/class/input")


def find_power_button(sys_dir=None):
    """/dev/input/eventN of the power button, or None. Only a device with KEY_POWER counts, and of
    those the gpio-keys one of the gpio-shutdown overlay first. A Pi 4 also has the HDMI's CEC remote
    (vc4-hdmi, an rc device) with KEY_POWER, numbered before it at times: CEC and rc devices are never
    taken. Any other device with KEY_POWER is taken only when no gpio-keys one is there."""
    import struct
    KEY_POWER = 116
    hexw = struct.calcsize("l") * 2                  # the words are kernel longs, highest first
    best, other = None, None
    for d in sorted((sys_dir or INPUT_SYS).glob("event*"), key=lambda p: (len(p.name), p.name)):
        try:
            caps = (d / "device" / "capabilities" / "key").read_text().split()
        except OSError:
            continue
        try:
            bits = int("".join(f"{int(w, 16):0{hexw}x}" for w in caps), 16) if caps else 0
        except ValueError:
            continue
        if not bits >> KEY_POWER & 1:
            continue
        info = []
        for name in ("name", "phys"):
            try:
                info.append((d / "device" / name).read_text().strip().lower())
            except OSError:
                info.append("")
        try:
            where = str((d / "device").resolve()).lower()
        except OSError:
            where = ""
        text = " ".join(info)
        if "cec" in text or "hdmi" in text or "/rc/rc" in where:
            continue
        if "gpio" in text or "shutdown" in text or "gpio-keys" in where or "gpio_keys" in where:
            best = best or d
        else:
            other = other or d
    pick = best or other
    return Path("/dev/input") / pick.name if pick is not None else None


class Device:
    def __init__(self):
        self.sampler = Sampler()
        self.port = Port(self.sampler)
        self.octave = int(load_settings().get("octave", OCTAVE))
        self.held = {}               # key index -> the note it sent, so a release matches its press
        self.pedal_latched = bool(load_settings().get("pedal", False))
        self.alarm = self.song = False   # from the bridge: something the stop button can stop
        self.keys_on = True              # piano/device/keys/set: off when the screen plays by touch only
        self.kb = {"mode": "internal", "name": "", "out": "both"}   # piano/device/keyboard/set
        self.kb_seen = None
        self.port.on_external = self.external
        self.testing = False             # piano/device/test: the hardware test is open
        self.test_quiet = False          # ... as "quiet", by the full check: the keys play nothing
        self.sampler.smart.add(DEV_CH)   # the smart pedal is for the device's own keys and touches
        voice = load_settings().get("voice", 0)
        self.sampler.voice_of[DEV_CH] = voice if isinstance(voice, int) and 0 <= voice < len(FAMILIES) else 0
        threading.Thread(target=self.warm_voice, daemon=True).start()
        self.swallowed = None            # the stop button's press, kept from its hold and release
        self.mqtt = None
        self.start_mqtt()
        threading.Thread(target=self.watch_keyboards, daemon=True).start()
        self.start_gpio()
        threading.Thread(target=self.watch_power, daemon=True).start()
        threading.Thread(target=self.watch_screen, daemon=True).start()
        threading.Thread(target=self._volume_file, daemon=True).start()
        if self.pedal_latched:
            self.set_pedal(True)

    def _volume_file(self):
        """The volume set on any screen is kept by the lesson engine in a file; this follows it, so the
        keys do not depend on a MIDI message getting through."""
        path, seen = DATA_DIR / "volume", None
        while True:
            try:
                stamp = path.stat().st_mtime
                if stamp != seen:
                    seen = stamp
                    vol = max(0, min(100, int(path.read_text().strip())))
                    self.sampler.master = loudness(vol / 100)
                    log(f"Volume {vol}%")
            except (OSError, ValueError):
                pass
            time.sleep(0.3)

    # ---- keys
    def base(self):
        return 12 * (self.octave + 1)

    def warm_voice(self):
        """The notes of the keys in the voice chosen, made before they are played (not the piano,
        which is in memory already), so the first touch of each key is not later than the next."""
        if self.sampler.voice_of.get(DEV_CH, 0) == 0:
            return
        for _ in range(100):             # the sound card first
            if self.sampler.chans:
                break
            time.sleep(0.1)
        for n in range(self.base() - 2, self.base() + 14):
            if LOW <= n <= HIGH:
                self.sampler.sound_for(DEV_CH, n)
        want = getattr(self, "expect", None)     # a lesson's note at the song's own height
        if want is not None and LOW <= want <= HIGH:
            self.sampler.sound_for(DEV_CH, want)

    def warm_soon(self):
        """warm_voice again, in the background, after the octave or the lesson's note moved: one at a
        time, and an octave moved fast warms only where it stopped."""
        if self.sampler.voice_of.get(DEV_CH, 0) == 0:
            return
        with _WARM_LOCK:
            self.warm_again = True
            if getattr(self, "warming", False):
                return
            self.warming = True
        threading.Thread(target=self._warm_loop, daemon=True).start()

    def _warm_loop(self):
        while True:
            with _WARM_LOCK:
                if not self.warm_again:
                    self.warming = False
                    return
                self.warm_again = False
            try:
                self.warm_voice()
            except Exception as e:
                log(f"Voice: {e}")

    def set_voice(self, value):
        if not 0 <= value < len(FAMILIES):
            return
        self.sampler.voice_of[DEV_CH] = value
        save_settings(voice=value)
        self.pub("piano/device/voice", str(value), retain=True)
        log(f"Voice {value} ({FAMILIES[value]})")
        self.warm_voice()
        note = self.base()               # a short note to hear it, as the voice is chosen
        self.sampler.note_on(DEV_CH, note, VELOCITY)
        def off():
            # key 0 pressed or the note touched meanwhile: it is the player's note now. Checked under the
            # sampler's lock, which a key's note_on also takes, so the two never cross
            with self.sampler.lock:
                if note not in tuple(self.held.values()) + tuple((getattr(self, "touched", None) or {}).values()):
                    self.sampler.note_off(DEV_CH, note)
        threading.Timer(0.6, off).start()

    def stop_now(self, reason):
        """The stop key or button: through the broker, and also through a file the bridge looks at every
        second, so the alarm stops even when the broker is gone (an external one restarting in the
        morning). Whichever comes second finds nothing playing."""
        self.pub("piano/stop", reason)
        self.write_stop_request(reason)

    def write_stop_request(self, reason):
        try:
            tmp = DATA_DIR / f".stop-request.{os.getpid()}.tmp"
            tmp.write_text(reason + "\n")
            tmp.replace(DATA_DIR / "stop-request")
        except OSError as e:
            log(f"Stop: the local stop request could not be written ({e})")

    def broker_up(self):
        c = getattr(self, "mqtt", None)
        try:
            return bool(c is not None and c.is_connected())
        except Exception:
            return False

    def stop_press(self, reason):
        """A press of the stop key or button. True when it was taken as a stop (and does nothing else).
        The alarm and the song are known from the bridge over MQTT. With the broker away they are not, so
        the bridge's own flags on this machine are asked: something playing there, or flags that cannot
        be read, and the stop goes to the bridge through the local file (the bridge finds nothing to
        stop when nothing plays). No MQTT publish then: it would go nowhere, or come late."""
        if self.alarm or self.song:
            self.stop_now(reason)
            log(f"{reason}: stopping the {'alarm' if self.alarm else 'song'}")
            return True
        if self.broker_up():
            return False
        here = local_playing()
        if here is False:
            return False
        self.write_stop_request(reason)
        log(f"{reason}: the broker is not connected, the stop goes to the bridge through the local file")
        return here is True

    def key(self, i, down):
        # key 12 is the high C: base() + 12, one octave above key 0 (the pitch class is the same)
        if down and getattr(self, "testing", False):   # the hardware test: which key, apart from the notes
            self.pub("piano/device/test/key", str(i))
        if down and (self.alarm or self.song or not self.broker_up()) \
                and load_settings().get("stop_button") == f"key:{i}" and self.stop_press("Armonico Pi stop key"):
            return                       # not held, so its release is ignored as well
        if down and getattr(self, "test_quiet", False):
            return                       # the full check: the key is only named, no note and nothing sent on
        if down and not getattr(self, "testing", False) and (not self.keys_on or self.kb.get("mode") == "external"):
            return                       # the keys are off (touch only, both off, or a USB keyboard alone); a held key still lets go
        if down:
            note = self.at_height(self.base() + i)
            if not LOW <= note <= HIGH:
                return
            self.held[i] = note
            self.pub("piano/device/key", str(i))      # which key: a lesson sounds key 0 and the high C alike
            self.sound_note(note, True)
        else:
            note = self.held.pop(i, None)
            if note is None:
                return
            self.sound_note(note, False)

    def sound_note(self, note, down):
        """One of the keys or a touch: sounded where the sound was chosen, and sent on to the bridge."""
        if self.port.here:
            if down:
                self.sampler.note_on(DEV_CH, note, VELOCITY)
            else:
                self.sampler.note_off(DEV_CH, note)
        if self.port.there:              # channel 1 on the keyboard: its own voice, not a song's part
            self.port.to_keyboard([0x90, note, VELOCITY] if down else [0x80, note, 0])
        self.port.send([0x90, note, VELOCITY] if down else [0x80, note, 0])

    def external(self, msg):
        """A message from the USB keyboard chosen: its notes, pedal and controls go on to the bridge and
        the lesson engine as the keys' do, and sound here unless its own sound was chosen."""
        if not msg or not 0x80 <= msg[0] <= 0xEF:
            return                       # clock, active sensing and the like stay with the keyboard
        kind = msg[0] & 0xF0
        on = kind == 0x90 and len(msg) > 2 and msg[2]
        if getattr(self, "testing", False) and on:   # the hardware test: it says what it played and plays nothing
            name = self.kb.get("name") if self.kb.get("name") in (self.kb_seen or []) else next(iter(self.kb_seen or []), "")
            self.pub("piano/device/test/usb", json.dumps({"name": name, "note": msg[1]}))
            return                       # a key let go, or the pedal, still goes on, so nothing is left held
        if on and self.kb.get("mode") not in ("external", "both"):
            return                       # connected for the test only, until the next look lets it go
        if on and not self.keys_on:
            return                       # Play with: touch only, or off
        if on and self.current_expect() is None:   # the octave follows the hand (not in a lesson: there the keys stay put)
            o = max(1, min(7, msg[1] // 12 - 1))
            if o != self.octave:
                self.set_octave(o)
        self.track_external(msg)
        if self.port.here:               # the keyboard sounds its own keys itself, so never sent back to it
            self.sampler.midi(msg, base=USB_CH)
        self.port.send(list(msg))

    def track_external(self, msg):
        """The USB keyboard's notes held and pedals down, so an unplugged keyboard leaves none on."""
        held = getattr(self, "ext_held", None)
        if held is None:
            held = self.ext_held = set()
        if getattr(self, "ext_pedal", None) is None:
            self.ext_pedal = set()
        kind, ch = msg[0] & 0xF0, msg[0] & 0x0F
        if kind == 0x90 and len(msg) > 2 and msg[2]:
            held.add((ch, msg[1]))
        elif kind in (0x80, 0x90) and len(msg) > 1:
            held.discard((ch, msg[1]))
        elif kind == 0xB0 and len(msg) > 2 and msg[1] == 64:
            (self.ext_pedal.add if msg[2] >= 64 else self.ext_pedal.discard)(ch)
        elif kind == 0xB0 and len(msg) > 1 and msg[1] in (120, 123):
            held.difference_update({k for k in held if k[0] == ch})

    def release_external(self):
        """The USB keyboard went (unplugged, or no longer chosen): its notes and its pedal let go, here
        and for the bridge and the lesson engine, which would otherwise keep them on (piano/pedal)."""
        held, pedal = getattr(self, "ext_held", None) or set(), getattr(self, "ext_pedal", None) or set()
        self.ext_held, self.ext_pedal = set(), set()
        for ch in sorted(pedal):
            self.sampler.set_pedal(USB_CH + ch, False)
            self.port.send([0xB0 | ch, 64, 0])
        for ch, note in sorted(held):
            self.sampler.note_off(USB_CH + ch, note)
            self.port.send([0x80 | ch, note, 0])
        if held or pedal:
            log(f"USB keyboard gone: {len(held)} notes and {len(pedal)} pedals let go")

    def watch_keyboards(self):
        """Every 2 s: the list of USB keyboards for the screen (when it changes), and the one chosen
        connected in, again after it is unplugged and plugged back."""
        while True:
            try:
                found = self.port.keyboards()
                names = sorted(found)
                if names != self.kb_seen:
                    self.kb_seen = names
                    self.pub("piano/device/keyboards", json.dumps(names), retain=True)
                want, out = set(), set()
                name = (self.kb.get("name") if self.kb.get("name") in found else names[0]) if names else None
                if (self.kb.get("mode") in ("external", "both") or getattr(self, "testing", False)) and name:   # the test hears it in any mode
                    want = {found[name]}
                if self.port.follow_external(want):
                    self.release_external()
                where = self.kb.get("out", "both")
                if name and where != "device":
                    inputs = self.port.keyboard_inputs()
                    if name in inputs:
                        out = {inputs[name]}
                self.port.follow_out(out)
                # with no keyboard to take it (unplugged, or none chosen) the sound stays here
                self.port.here = not out or where != "keyboard"
                # its sound is for whichever plays: the keys, the screen, a song or a lesson
                self.port.there = bool(out)
            except Exception as e:           # never the end of the keys' program
                log(f"Keyboards: {e}")
            time.sleep(2)

    def touch_note(self, note, down):
        """A key touched on the screen, sounded and sent on exactly like one of the keys."""
        if not LOW <= note <= HIGH:
            return
        touched = getattr(self, "touched", None)
        if touched is None:
            touched = self.touched = {}
        if down:
            played = touched[note] = self.at_height(note)
        else:
            played = touched.pop(note, note)      # let go as it was played, even if the lesson moved on
        self.sound_note(played, down)

    def at_height(self, note):
        """In a lesson the keys stay in one octave, and the key of the note the lesson waits for
        sounds at the song's own height: C above or below is still key 1 (the high C key is key 1 too) (piano/device/expect)."""
        want = self.current_expect()
        if want is not None and note % 12 == want % 12 and LOW <= want <= HIGH:
            return want
        return note

    def current_expect(self):
        """The note the lesson waits for: -1 in a lesson that waits for none (a demo, the count-in, a
        pause), None out of one. One not sent again for EXPECT_TTL seconds is dropped, so a screen
        that restarted or a lost last "" never leaves the keys in a lesson."""
        want = getattr(self, "expect", None)
        at = getattr(self, "expect_at", None)
        if want is not None and at is not None and time.monotonic() - at > EXPECT_TTL:
            self.expect = want = None
        return want

    def set_octave(self, value):
        value = max(1, min(7, value))
        if value != self.octave:
            self.octave = value
            save_settings(octave=value)
            log(f"Octave {value}")
            self.warm_soon()             # the voice's notes for the keys where they are now
        self.pub("piano/device/octave", str(self.octave), retain=True)

    def set_pedal(self, down):
        self.pedal_latched = down
        save_settings(pedal=down)
        self.sampler.set_pedal(DEV_CH, down)
        if self.port.there:
            self.port.to_keyboard([0xB0, 64, 127 if down else 0])
        self.port.send([0xB0, 64, 127 if down else 0])      # the bridge publishes it as piano/pedal
        self.pub("piano/device/pedal", "on" if down else "off", retain=True)

    # ---- the buttons
    def button(self, name, action=""):
        if action == "hold" and self.swallowed == name:
            return                       # the same press that stopped something
        stop = load_settings().get("stop_button", "ok")
        if not action and name == stop and self.stop_press("Armonico Pi stop button"):
            self.swallowed = name
            return
        self.pub("piano/device/button", name + (":" + action if action else ""))

    def button_up(self, name):
        if self.swallowed == name:
            self.swallowed = None
        self.pub("piano/device/button/up", name)

    def watch_screen(self, rounds=None):
        """The automatic stop for the screen: the screen program that crashed, was killed or was stopped
        while the alarm or a song plays stops them, with no key pressed (as the units' ExecStopPost do for
        this service and the lesson engine). Once a second, and only while something plays it reads one
        small file and looks for one /proc entry; the bridge's flags otherwise (two stat calls).
        rounds: for the tests, how many looks before it returns."""
        seen = None                       # the screen's pid, seen alive while something played
        said = None
        while rounds is None or rounds > 0:
            if rounds is not None:
                rounds -= 1
            time.sleep(1)
            try:
                if not (self.alarm or self.song or local_playing() is True):
                    seen = None
                    continue
                pid, on_purpose = read_screen_ready()
                if seen is not None and not os.path.exists(f"/proc/{seen}"):
                    if not (on_purpose and pid == seen):
                        log(f"The screen program ({seen}) ended while the {'alarm' if self.alarm else 'song'} played: stopping it")
                        self.stop_now("Screen stopped")
                    seen = None
                if pid is not None and not on_purpose and os.path.exists(f"/proc/{pid}"):
                    seen = pid
            except Exception as e:
                if str(e) != said:
                    log(f"Screen watch: {e}")
                    said = str(e)

    def watch_power(self):
        """The power button (GPIO3, the gpio-shutdown overlay) is a key on an input device. logind
        leaves it alone on the device, so a press asks on the screen; held 4 seconds it shuts down
        without the screen, through the same root request the screen uses.

        Not found yet (or its device went away): looked for again every POWER_LOOK seconds, a few
        reads in /sys, instead of giving up for the life of the service."""
        import struct
        fmt = "llHHi"                                   # struct input_event: time, type, code, value
        size, KEY_POWER = struct.calcsize(fmt), 116
        self.power_down_at = None
        said = None                                     # the last thing logged, so a wait logs once
        while True:
            dev = find_power_button()
            if dev is None:
                if said != "none":
                    log(f"Power button: no gpio-keys device with KEY_POWER yet, looking again every {POWER_LOOK} s")
                    said = "none"
                time.sleep(POWER_LOOK)
                continue
            try:
                f = open(dev, "rb", buffering=0)
            except OSError as e:
                if said != f"open {dev}":
                    log(f"Power button: {e} (the input group is needed), trying again every {POWER_LOOK} s")
                    said = f"open {dev}"
                time.sleep(POWER_LOOK)
                continue
            log(f"Power button on {dev}")
            said = None
            try:
                self._read_power(f, fmt, size, KEY_POWER)
            except OSError as e:
                log(f"Power button: {e}")
            finally:
                f.close()
            log("Power button: its input device went away, looking for it again")
            time.sleep(POWER_LOOK)

    def _read_power(self, f, fmt, size, KEY_POWER):
        import struct
        while True:
            data = f.read(size)
            if len(data) < size:
                return
            _, _, typ, code, value = struct.unpack(fmt, data)
            if typ != 1 or code != KEY_POWER:
                continue
            if value == 1:
                down_at = self.power_down_at = time.monotonic()
                self.pub("piano/device/power", "press")
                threading.Timer(4.0, self.power_held, args=(down_at,)).start()
            elif value == 0:
                self.power_down_at = None

    def power_held(self, at):
        if getattr(self, "test_quiet", False):
            log("Power button held during the full check: not shutting down")
            return
        if getattr(self, "power_down_at", None) == at:
            try:
                # whole or not at all: the root unit can start on the file's creation, and read it empty
                req = DATA_DIR / "device-request"
                tmp = req.with_name(f".device-request.{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(json.dumps({"id": "0" * 12, "action": "shutdown", "args": {}}))
                os.replace(tmp, req)
                log("Power button held: shutting down")
            except OSError as e:
                log(f"Power button: {e}")

    def start_gpio(self):
        try:
            from gpiozero import Button
        except ImportError:
            log("gpiozero is missing: no keys. sudo apt install python3-gpiozero python3-lgpio")
            return
        except Exception as e:
            log(f"gpiozero did not load ({type(e).__name__}: {e}): no keys or buttons")
            return
        # Any failure of gpiozero (no pin factory, a pin busy or claimed by an overlay, a GPIO chip
        # that is not there) costs only the keys or buttons it hit: the sound, MQTT and the USB
        # keyboard go on. A failure of the pin factory itself hits every pin, so it ends the setup at once.
        self.keys, self.buttons, failed = [], [], []

        def make(what, pin, **kw):
            try:
                return Button(pin, pull_up=True, bounce_time=BOUNCE, **kw)
            except Exception as e:
                failed.append(what)
                log(f"GPIO: {what} on GPIO{pin} skipped: {type(e).__name__}: {e}")
                if type(e).__name__ == "BadPinFactory":
                    raise
                return None
        try:
            for i, pin in enumerate(KEY_PINS):
                b = make(f"key {i}", pin)
                if b is None:
                    continue
                b.when_pressed = lambda i=i: self.key(i, True)
                b.when_released = lambda i=i: self.key(i, False)
                self.keys.append(b)
            for name, pin in BUTTON_PINS.items():
                b = make(f"button {name}", int(pin), hold_time=HOLD)
                if b is None:
                    continue
                b.when_pressed = lambda n=name: self.button(n)
                b.when_held = lambda n=name: self.button(n, "hold")
                b.when_released = lambda n=name: self.button_up(n)
                self.buttons.append(b)
        except Exception as e:
            log(f"GPIO: no pin factory ({type(e).__name__}: {e}): no keys or buttons. "
                "The sound, MQTT and a USB keyboard still work")
        log(f"GPIO: {len(self.keys)} keys, {len(self.buttons)} buttons"
            + (f", {len(failed)} skipped ({', '.join(failed)})" if failed else ""))

    def start_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log("paho-mqtt is missing: no buttons on the screen")
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-device-{os.getpid()}")
        if MQTT_USER:
            c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.will_set("piano/device/state", "offline", retain=True)

        def on_connect(cl, u, f, rc, p=None):
            cl.subscribe([("piano/device/octave/set", 0),
                          ("piano/device/pedal/set", 0), ("piano/device/note", 0),
                          ("piano/device/keys/set", 0), ("piano/device/keyboard/set", 0),
                          ("piano/device/voice/set", 0), ("piano/device/test", 0),
                          ("piano/device/expect", 0),
                          ("piano/alarm/status", 0), ("piano/play/status", 0)])   # retained, from the bridge
            cl.publish("piano/device/state", "online", retain=True)
            self.kb_seen = None              # the keyboards' list again on this connection
            cl.publish("piano/device/octave", str(self.octave), retain=True)
            cl.publish("piano/device/pedal", "on" if self.pedal_latched else "off", retain=True)
            cl.publish("piano/device/voice", str(self.sampler.voice_of.get(DEV_CH, 0)), retain=True)

        def on_message(cl, u, msg):
            # nothing a message holds may end paho's network thread (paho 2 lets the error out of it,
            # and the device then lost MQTT while the service still looked healthy)
            try:
                handle(msg)
            except Exception as e:
                log(f"MQTT {msg.topic}: {e}")

        def handle(msg):
            import re
            text = msg.payload.decode(errors="replace").strip().lower()
            if msg.topic == "piano/device/octave/set":
                try:
                    self.set_octave(self.octave + int(text) if text[:1] in "+-" else int(text))
                except ValueError:
                    pass
            elif msg.topic == "piano/device/note":     # a key touched on the screen: "on 64" / "off 64"
                parts = text.split()
                if len(parts) == 2 and parts[0] in ("on", "off") and re.fullmatch(r"[0-9]+", parts[1]):
                    self.touch_note(int(parts[1]), parts[0] == "on")
            elif msg.topic == "piano/device/pedal/set":
                self.set_pedal(text in ("on", "1", "true"))
            elif msg.topic == "piano/device/expect":   # the note a lesson waits for, -1 for none yet, or empty
                old = getattr(self, "expect", None)
                self.expect = int(text) if re.fullmatch(r"-1|[0-9]+", text) else None
                self.expect_at = time.monotonic()
                if self.expect != old and self.expect is not None and self.expect >= 0:
                    self.warm_soon()
            elif msg.topic == "piano/device/keys/set":
                self.keys_on = text != "off"
            elif msg.topic == "piano/device/voice/set":
                if re.fullmatch(r"[0-9]+", text):
                    threading.Thread(target=self.set_voice, args=(int(text),), daemon=True).start()
            elif msg.topic == "piano/device/test":
                self.testing = text in ("on", "quiet")
                self.test_quiet = text == "quiet"
            elif msg.topic == "piano/device/keyboard/set":
                try:
                    kb = json.loads(msg.payload.decode(errors="replace"))
                except ValueError:
                    kb = None
                if isinstance(kb, dict) and kb.get("mode") in ("internal", "external", "both"):
                    out = kb.get("out")
                    if out not in ("device", "keyboard", "both"):   # an older screen: "sound" on or off
                        out = "both" if kb.get("sound", True) else "keyboard"
                    self.kb = {"mode": kb["mode"], "name": str(kb.get("name") or ""), "out": out}
                    self.kb_seen = None      # the list again with the next look, for a screen that just came
            elif msg.topic == "piano/alarm/status":
                self.alarm = text == "on"
            elif msg.topic == "piano/play/status":
                self.song = text == "playing"

        c.on_connect, c.on_message = on_connect, on_message
        c.reconnect_delay_set(1, 30)
        try:
            c.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
            c.loop_start()
            self.mqtt = c
        except (OSError, ValueError) as e:
            log(f"MQTT: {e}")

    def pub(self, topic, payload, retain=False):
        if self.mqtt is not None:
            self.mqtt.publish(topic, payload, retain=retain)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "prepare":
        return prepare()
    Device()
    log("Armonico Pi is ready")
    threading.Event().wait()


if __name__ == "__main__":
    main()
