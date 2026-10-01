"""recorder.py - the screen recording of the Armonico Pi, made by the screen program itself.

The program hands every frame it shows to ffmpeg through a pipe, so the recording is exactly what was
drawn, with no reading back from the graphics memory (kmsgrab copied each frame out of uncached video
memory, which cost the CPU the screen needed, and the recording came out choppy). No root is needed:
the screen runs as the same user as PipeWire.

One file, recordings/armonico-<date>-<time>.mkv in the data folder, with up to 2 tracks:
  video     the screen at its own size, 30 frames a second, x264 (ultrafast, crf 18), every frame
            stamped with the clock when it was drawn, so a slower moment repeats a frame, never skips time
  audio     the device's own sound (the monitor of the default output sink, see device_monitor), mixed with the
            narration of a USB microphone (headphones, a USB mic) when one is there: one track, one AAC encode
The screen keeps only its latest frame; a writer thread sends one every 1/30 s of the clock, the latest
repeated when none came, so frame n is the screen at n/30 s and the screen never waits for ffmpeg. The
picture and each sound are recorded by an ffmpeg of their own and put together after the stop (class
Recorder). The picture's ffmpeg runs at a lower priority (nice 10) than the screen; the sounds' ffmpegs (flac,
cheap) at the normal one, so under load the picture falls behind, never the sound.

Nothing cuts a recording by its length: it runs until it is stopped, or until the SD card has no more room than
the finished file needs (the parts are put together into a new file of about the same size). Each ffmpeg is
tied to the screen program (the kernel ends it when the program dies), and parts left by a program that died
are removed, with their ffmpegs, when the program starts again and before each recording.
"""
import ctypes
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

FPS = 30
WATCH_EVERY = 2.0            # seconds between two looks at the sounds' ffmpegs and the free space
SOUND_RETRY = 10.0           # seconds before a sound that ended by itself (its output gone) is tried again
ROOM = 200 * 1000 * 1000     # bytes kept free on top of what putting the file together needs
IDLE_END = 20.0              # an ffmpeg finishing its file is waited for while the file grows; this long still: stopped


def _load_prctl():
    try:
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _load_prctl()           # looked up here: a child between fork and exec should not load a library
_PR_SET_PDEATHSIG = 1


def _child(nice=0):
    """What a recording's ffmpeg does before it starts: its priority, and an end tied to the screen program.
    PR_SET_PDEATHSIG sends it SIGTERM (which ffmpeg takes as q: the file is closed properly) when the thread
    that started it ends, so a screen program that dies leaves no ffmpeg writing until the SD card is full.
    The recorder starts its ffmpegs from the thread that lives as long as the recording."""
    parent = os.getpid()

    def setup():
        if nice:
            os.nice(nice)
        if _PRCTL is not None:
            _PRCTL(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
            if os.getppid() != parent:           # the program died before the line above: end now
                os.kill(os.getpid(), signal.SIGTERM)
    return setup


def clean_stale(folder, proc_dir="/proc"):
    """The parts of a recording whose program died: their ffmpegs ended and their files removed. Only processes
    whose command names a part file in this folder are touched. Never while this program records."""
    folder = Path(folder)
    mark = str(folder / ".rec-")
    try:
        pids = [d for d in os.listdir(proc_dir) if d.isdigit()]
    except OSError:
        pids = []
    me = os.getpid()
    for pid in pids:
        if int(pid) == me:
            continue
        try:
            with open(os.path.join(proc_dir, pid, "cmdline"), "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if not argv or os.path.basename(argv[0]) != "ffmpeg" or not any(a.startswith(mark) for a in argv):
            continue
        try:
            os.kill(int(pid), signal.SIGKILL)    # its files are removed below: nothing to finish
        except OSError:
            pass
    try:
        stale = list(folder.glob(".rec-*")) + list(folder.glob("armonico-*.mkv.part"))
    except OSError:
        stale = []
    for f in stale:
        try:
            if f.is_file() and not f.is_symlink():
                f.unlink()
        except OSError:
            pass


def _pactl(*args, timeout=5):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)


def find_mic():
    """The PipeWire source of a USB microphone, made ready: (name, description) or (None, "").

    A USB headset whose card was left on an output-only profile has no source at all: its profile is
    moved to the best one with an input as well. The source is then unmuted and set to full level,
    since WirePlumber may keep a new one muted or low."""
    try:
        moved = _usb_input_profile()
        # after a profile change the new source shows up a moment later, and a fixed half second was
        # sometimes too short: the recording started with no microphone. Looked for every 0.1 s, at most 3 s
        tries = 30 if moved else 1
        for i in range(tries):
            name = _mic_source()
            if name is not None or i == tries - 1:
                break
            time.sleep(0.1)
        # A Bluetooth headset is not used as the microphone: its microphone works only on the headset profile
        # (HSP/HFP), which is mono and narrow-band for everything heard as well, so the whole device sounded bad
        # for the length of the recording. Its sound stays on A2DP; a microphone is a USB one.
    except (OSError, subprocess.SubprocessError):
        restore_bt()                             # a headset moved to its headset profile goes back
        return None, ""
    if name is None:
        restore_bt()
        return None, ""
    try:
        _pactl("set-source-mute", name, "0")
        _pactl("set-source-volume", name, "100%")
    except (OSError, subprocess.SubprocessError):
        pass
    return name, _label(name)


def _mic_source():
    """The first USB input PipeWire has now, or any other real input, or None."""
    r = _pactl("list", "short", "sources")
    names = [f[1] for f in (line.split("\t") for line in (r.stdout or "").splitlines())
             if len(f) > 1 and not f[1].endswith(".monitor")]
    usb = [n for n in names if n.startswith("alsa_input.usb-")]
    # any other real input (a USB mic that names itself otherwise); the Pi itself has no input. A Bluetooth
    # headset's input is left out (see find_mic)
    other = [n for n in names if n.startswith("alsa_input.") and "platform-" not in n]
    return (usb or other or [None])[0]


def _label(name):
    if name.startswith("bluez_input."):
        return "a Bluetooth headset"
    core = name.split(".", 1)[-1]
    core = core[4:] if core.startswith("usb-") else core
    core = core.rsplit(".", 1)[0].rsplit("-", 1)[0]
    return core.replace("_", " ")[:40]


def _usb_input_profile():
    """Each USB sound card on a profile with no input: moved to its best available profile with one.
    True when a profile was changed."""
    r = _pactl("list", "cards")
    card, profiles, active = None, [], ""
    blocks = []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("Name: "):
            if card:
                blocks.append((card, profiles, active))
            card, profiles, active = s[6:], [], ""
        elif card and s.startswith(("output:", "input:")) and "(sinks:" in s:
            pname = s.split(": ", 1)[0]
            avail = "available: no" not in s
            prio = 0
            if "priority: " in s:
                try:
                    prio = int(s.split("priority: ", 1)[1].split(",")[0].split(")")[0])
                except ValueError:
                    pass
            profiles.append((pname, prio, avail))
        elif card and s.startswith("Active Profile: "):
            active = s[16:]
    if card:
        blocks.append((card, profiles, active))
    moved = False
    for card, profiles, active in blocks:
        if not card.startswith("alsa_card.usb-") or "input:" in active:
            continue
        good = sorted((p for p in profiles if "input:" in p[0] and p[2]), key=lambda p: -p[1])
        if good:
            _pactl("set-card-profile", card, good[0][0])
            moved = True                         # find_mic waits for the new source
    return moved


_BT_BACK = []                                    # [(card, profile)] to put back after the recording (none are moved now)


def restore_bt():
    """The Bluetooth cards moved to a headset profile for a recording, back to what they were."""
    while _BT_BACK:
        card, profile = _BT_BACK.pop()
        try:
            _pactl("set-card-profile", card, profile)
        except (OSError, subprocess.SubprocessError):
            pass


def video_cmd(size, out, progress=None):
    """The picture alone: raw RGB frames on the pipe, one every 1/30 s of the device's own clock. Each
    frame's time is its place in the stream (frame n at n/30 s), set by the recorder's writer, never by
    when ffmpeg got round to reading it. progress: a file where ffmpeg writes how many frames it encoded
    (frame=N, twice a second), for the saving's progress (Recorder.save_progress)."""
    w, h = size
    tail = ["-progress", str(progress)] if progress else []
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{w}x{h}", "-framerate", str(FPS),
            "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"] + tail + [str(out)]


def last_frame(path):
    """The last frame=N an ffmpeg -progress file holds, or 0."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("ascii", "replace")
    except OSError:
        return 0
    found = [ln[6:] for ln in tail.splitlines() if ln.startswith("frame=")]
    try:
        return int(found[-1]) if found else 0
    except ValueError:
        return 0


def output_label(name):
    """A short name for a sound output: the jack, HDMI, a USB device by its name, Bluetooth, or the mix."""
    if name == "armonico_mix" or name.startswith("armonico_"):
        return "Mix"
    low = name.lower()
    if "hdmi" in low:                                # HDMI off (DSI only): never chosen, the name stays for old recordings
        return "HDMI"
    if "mailbox" in low or "bcm2835" in low:
        return "Jack"
    if name.startswith("bluez_output."):
        return "Bluetooth"
    if name.startswith("alsa_output.usb-"):
        core = name[len("alsa_output.usb-"):].rsplit(".", 1)[0].rsplit("-", 1)[0]
        return "USB " + core.replace("_", " ")[:30]
    return name.split(".", 1)[-1][:30]


def outputs():
    """[(sink name, label)] of the sound outputs PipeWire has now, for the choice of what to record."""
    try:
        r = _pactl("list", "short", "sinks")
    except (OSError, subprocess.SubprocessError):
        return []
    names = [f[1] for f in (line.split("\t") for line in (r.stdout or "").splitlines()) if len(f) > 1]
    return [(n, output_label(n)) for n in names]


def device_monitor(choice="auto"):
    """The monitor of the output the sound goes to, by its name. choice: "auto" (the default sink, the one
    chosen in Settings › Sound), or the name of another sink that is connected now (HDMI, a USB device,
    Bluetooth, the mix); a choice that is no longer connected is the default sink again. "@DEFAULT_MONITOR@" came out as the
    default source, which on the Pi is the monitor of the AUX jack, so a device on HDMI recorded silence
    (-91 dB). The default sink's own monitor is what the keys and the songs play into."""
    try:
        if choice and choice != "auto" and choice in [n for n, _ in outputs()]:
            return f"{choice}.monitor"
        name = _pactl("get-default-sink").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return f"{name}.monitor" if name else "@DEFAULT_MONITOR@"


def audio_cmd(source, out):
    """One live sound source alone, losslessly, each packet stamped with the wall clock (the pulse input's
    own clock, kept by -copyts): the mux lines it up with the picture afterwards."""
    # a deep input queue: a moment when the SD card is slow to take a write never makes the sound lose packets
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-thread_queue_size", "1024", "-f", "pulse", "-i", source, "-copyts", "-c:a", "flac", str(out)]


def mux_cmd(video, t0, sounds, out):
    """The finished file from the parts. sounds: [(file, start, title)], start being the wall-clock time of
    the sound's first packet; t0 the wall-clock time of the first frame. Each sound is moved by its own
    distance from t0, so they start where they were heard. A sound recorded in more than one part (its output
    changed or its microphone came back during the recording) is one track again, each part at its own time.
    With a microphone: first the two mixed (what a player plays), then each on its own.
    Every track keeps its full length: the file is as long as its longest part (no -shortest, which cut the
    whole file where a microphone was unplugged). aresample's async=1 fills a gap in a sound (packets the
    source lost) with silence, so what comes after it stays in place."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video)]
    for f, _, _ in sounds:
        cmd += ["-i", str(f)]
    groups = {}
    for i, (f, start, title) in enumerate(sounds, 1):
        ms = max(0, int(round((start - t0) * 1000)))
        # the microphone's own hiss (a steady -50 dB under the voice, measured on a LX-3000 at full level)
        # taken out by FFT noise reduction: on that recording 31 dB less between words, the voice unchanged.
        # The device's sound is digital silence between notes and is left as it is
        clean = "," + MIC_CLEAN if title == "Microphone" and not str(f).endswith(".clean.mka") else ""
        groups.setdefault(title, []).append(f"[{i}:a]aresample=48000:async=1{clean},asetpts=PTS-STARTPTS,adelay={ms}:all=1")
    parts = []
    for g, (title, chains) in enumerate(groups.items()):
        if len(chains) == 1:
            parts.append(chains[0])
        else:
            labels = [f"[g{g}p{j}]" for j in range(len(chains))]
            parts.append(";".join(c + l for c, l in zip(chains, labels)) + ";" + "".join(labels) +
                         f"amix=inputs={len(chains)}:duration=longest:normalize=0")
    names = list(groups)
    if len(parts) == 2:
        # one mixed track: a single AAC encode, instead of the mix and each sound alone (3 encodes)
        graph = f"{parts[0]}[d1];{parts[1]}[m1];[d1][m1]amix=inputs=2:duration=longest:normalize=0[mix]"
        maps = ["[mix]"]
        titles = [" + ".join(names)]
    else:
        graph = f"{parts[0]}[d]"
        maps = ["[d]"]
        titles = [names[0]]
    cmd += ["-filter_complex", graph, "-map", "0:v"]
    for m in maps:
        cmd += ["-map", m]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    for i, t in enumerate(titles):
        cmd += [f"-metadata:s:a:{i}", f"title={t}"]
    return cmd + ["-f", "matroska", str(out)]


MIC_CLEAN = "highpass=f=80,afftdn=nr=30:nf=-35"


def clean_cmd(src, dst):
    """The microphone's hiss taken out (MIC_CLEAN) on its own, right after the stop, beside the picture's encoder:
    lossless (flac), so the mux only lines it up. The mux keeps the same filter for a part this did not clean."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(src), "-af", MIC_CLEAN,
            "-c:a", "flac", str(dst)]


def start_of(f):
    """The wall-clock time of a sound part's first packet, or None."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=start_time", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, timeout=10)
        return float((r.stdout or "").split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class Recorder:
    """Three ffmpeg processes while recording, each on its own so none can hold up another: the picture
    from the pipe, the device's sound and the microphone. Before, one ffmpeg took all three live, and a
    source that came late (a microphone just switched on) held up the rest: the frames, stamped with the
    clock only when ffmpeg read them, piled up and came out squeezed or stretched (a clock on the screen
    read 00:03 eight seconds into the file), and the sound had seconds of silence put in, so picture and
    sound no longer matched. Now each frame's time is its place in the stream, each sound keeps its own
    clock, and the file is put together after the stop, every part moved to the same start.

    One thread per recording starts its ffmpegs, writes the frames and puts the file together; saving is True
    from the start until that thread is done, and no new recording starts meanwhile. Each recording's part
    files carry a name of their own, so nothing of one is written over by the next."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.proc = None                         # the picture's ffmpeg, while recording; set last, once all is ready
        self.sounds = []                         # [[process, file, title, source]], one per part
        self.size = None
        self.since = 0.0
        self.file = None
        self.mic = ""
        self.next_at = 0.0
        self.dropped = 0
        self.frame = None                        # the latest frame shown, as RGB bytes
        self.lock = threading.Lock()
        self.taken = True                        # whether the writer took the latest frame
        self.done = None                         # (file, ok) of the last recording, once it is put together
        self.ending = None
        self.saving = False                      # a recording's thread still runs (recording or putting together)
        self.full = False                        # the last recording was stopped because the SD card was nearly full
        self.save = None                         # after the stop, while the file is put together: what save_progress reads
        clean_stale(self.folder)                 # an ffmpeg left by a program that died stops writing now

    @property
    def on(self):
        return self.proc is not None

    MUX_BYTES = 16000            # bytes a second of the sound track in the finished file (aac at 128 kb/s)

    def save_progress(self):
        """(fraction 0 to 1, stage) while a stopped recording is being saved, else None. The first half is the
        picture's encoder finishing the frames it was given (frame=N of its -progress file against the frames
        sent), the second the parts put together into one file (its size against the size expected)."""
        sv = self.save
        if not sv or self.proc is not None:
            return None
        if sv["stage"] == "picture":
            frac = min(1.0, last_frame(sv["progress"]) / sv["frames"]) if sv["frames"] else 1.0
            return 0.5 * frac, "picture"
        if sv["stage"] == "sounds":
            return 0.5, "sounds"                 # the sounds ending, and the microphone's noise reduction
        if sv["stage"] == "join":
            try:
                got = Path(sv["tmp"]).stat().st_size
            except OSError:
                got = 0
            frac = min(1.0, got / sv["expect"]) if sv["expect"] else 0.0
            return 0.5 + 0.49 * frac, "join"
        return 0.5, "join"

    def part(self, token, name):
        return self.folder / f".rec-{token}-{name}"

    def start(self, size, when=None, sound="auto"):
        """Starts the recording for frames of this size. when: the device's own time (its city's zone), for
        the file's name; the system clock may be on UTC. Returns an error text, or ""."""
        if self.proc is not None or self.saving:
            return "already recording"
        w, h = size
        size = (w - w % 2, h - h % 2)           # x264 wants even sides
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return str(e)
        name = "armonico-%Y-%m-%d-%H%M%S.mkv"
        out = self.folder / (when.strftime(name) if when is not None else time.strftime(name))
        self.saving = True
        try:
            err = self._start(size, out, sound)
        except Exception as e:                   # nothing may leave saving set or a headset on its headset profile
            err = str(e) or "failed"
        if err:
            restore_bt()                         # find_mic may have moved a Bluetooth headset for its microphone
            self.saving = False
        return err

    def _start(self, size, out, sound):
        clean_stale(self.folder)
        token = uuid.uuid4().hex[:8]
        mic, self.mic = find_mic()
        video = self.part(token, "video.mkv")
        cmds = [("video", video_cmd(size, video, self.part(token, "video.progress")))]
        sounds = [("Armonico", device_monitor(sound), self.part(token, "device-1.mka"))]
        if mic:
            sounds.append(("Microphone", mic, self.part(token, "mic-1.mka")))
        for title, src, f in sounds:
            cmds.append((title, audio_cmd(src, f)))
        try:
            log = open(self.folder / "last.log", "w")
            for _, c in cmds:
                log.write(" ".join(c) + "\n")
            log.write(str(out) + "\n")
            log.flush()
        except OSError as e:
            return str(e)
        ready, started = {}, threading.Event()
        threading.Thread(target=self._run, args=(cmds, sounds, size, out, log, video, token, sound, ready, started),
                         daemon=True).start()
        started.wait()
        return ready.get("err", "")

    def _run(self, cmds, sounds, size, out, log, video, token, sound, ready, started):
        """The recording's own thread: it starts the ffmpegs (so they live as long as it, see _child), then
        writes the frames and puts the file together."""
        # the frames' clock starts now: frame 0 is the screen at t0, frame n at t0 + n/30
        t0, mono0 = time.time(), time.monotonic()
        try:
            proc = subprocess.Popen(cmds[0][1], stdin=subprocess.PIPE, stdout=log, stderr=log, preexec_fn=_child(10))
        except OSError as e:
            log.close()
            ready["err"] = "ffmpeg is missing" if isinstance(e, FileNotFoundError) else str(e)
            started.set()
            return
        parts = []
        for (title, src, f), (_, c) in zip(sounds, cmds[1:]):
            try:
                p = subprocess.Popen(c, stdin=subprocess.DEVNULL, stdout=log, stderr=log, preexec_fn=_child())
                parts.append([p, f, title, src])
            except OSError:
                pass
        ending = threading.Event()
        with self.lock:
            self.frame, self.taken = None, True
        self.sounds, self.size, self.file, self.since, self.dropped = parts, size, out, time.time(), 0
        self.next_at, self.full = 0.0, False
        self.ending = ending
        self.proc = proc                         # last: feed() and stop() only ever see a recording that is ready
        started.set()
        try:
            self._write(proc, parts, ending, out, log, t0, mono0, video, token, sound)
        except Exception:
            import traceback
            try:
                log.write(traceback.format_exc())
                log.close()
            except (OSError, ValueError):
                pass
            if self.proc is proc:
                self.proc = None
            restore_bt()
            self.save = None
            self.saving = False
            self.done = (out, False)

    def feed(self, surface, pg):
        """The frame just shown, at most 30 a second: kept as the latest; the writer takes it on its own
        clock. Never waits for ffmpeg."""
        proc, size = self.proc, self.size
        if proc is None or size is None:
            return
        now = time.monotonic()
        if now < self.next_at:
            return
        self.next_at = max(self.next_at + 1 / FPS, now - 1 / FPS)
        if surface.get_size() != size:
            surf = surface.subsurface((0, 0) + size) if surface.get_width() >= size[0] and \
                surface.get_height() >= size[1] else pg.transform.scale(surface, size)
        else:
            surf = surface
        data = pg.image.tobytes(surf, "RGB")
        with self.lock:
            if self.frame is not None and self.taken is False:
                self.dropped += 1                # shown but replaced before the writer took it
            self.frame, self.taken = data, False

    def stop(self):
        """Ends the recording: the picture's input closes, the sounds are stopped, and the file is put
        together from the parts."""
        proc, ending = self.proc, self.ending
        if proc is None or ending is None:
            return
        ending.set()
        if self.proc is proc:
            self.proc = None

    def _watch(self, parts, log, token, sound, ending, retry):
        """Every WATCH_EVERY seconds while recording. A sound whose ffmpeg ended by itself (its output was
        disconnected or changed, the microphone unplugged) is recorded again into a part of its own, from the
        output in use now or the microphone there now; the file puts the parts back in their places. And when
        the SD card has no more room than putting the file together needs, the recording ends here, properly,
        instead of every ffmpeg failing on a full card."""
        now = time.monotonic()
        last = {}
        for part in parts:
            last[part[2]] = part
        for title, part in last.items():
            if part[0].poll() is None or now < retry.get(title, 0.0):
                continue
            retry[title] = now + SOUND_RETRY
            try:
                src = device_monitor(sound) if title == "Armonico" else _mic_source()
            except (OSError, subprocess.SubprocessError):
                src = None
            if not src:
                continue
            k = sum(1 for p in parts if p[2] == title) + 1
            f = self.part(token, f"{'device' if title == 'Armonico' else 'mic'}-{k}.mka")
            c = audio_cmd(src, f)
            try:
                log.write(" ".join(c) + "\n")
                log.flush()
                parts.append([subprocess.Popen(c, stdin=subprocess.DEVNULL, stdout=log, stderr=log, preexec_fn=_child()),
                              f, title, src])
            except (OSError, ValueError):
                pass
        try:
            used = sum(f.stat().st_size for f in self.folder.glob(f".rec-{token}-*"))
            free = shutil.disk_usage(self.folder).free
        except OSError:
            return
        if free < used + ROOM:
            self.full = True
            try:
                log.write(f"stopped: {free} bytes free, {used} recorded\n")
            except (OSError, ValueError):
                pass
            ending.set()

    def _write(self, proc, parts, ending, out, log, t0, mono0, video, token, sound):
        ok = True
        n = 0
        retry = {}
        watch_at = time.monotonic() + WATCH_EVERY
        while not ending.is_set():
            due = mono0 + n / FPS
            wait = due - time.monotonic()
            if wait > 0:
                ending.wait(wait)
                if ending.is_set():
                    break
            if time.monotonic() >= watch_at:
                watch_at = time.monotonic() + WATCH_EVERY
                self._watch(parts, log, token, sound, ending, retry)
                if ending.is_set():
                    break
            with self.lock:
                data, self.taken = self.frame, True
            if data is None:                     # nothing drawn yet: frame 0 waits for the first one
                mono0 += 1 / FPS
                t0 += 1 / FPS
                continue
            try:
                # one frame for each 1/30 s, the latest one shown repeated if none came: the picture's
                # time stays the device's time even when ffmpeg was slow for a moment
                proc.stdin.write(data)
            except (BrokenPipeError, OSError, ValueError):
                ok = False
                break
            n += 1
        prog = video.with_name(video.name.replace("video.mkv", "video.progress"))
        self.save = {"stage": "picture", "frames": n, "progress": prog, "tmp": None, "expect": 0}
        behind = (time.monotonic() - mono0) - n / FPS
        if n and behind > 1:
            try:
                log.write(f"the picture is {behind:.1f} s shorter than the recording: the encoder fell behind\n")
            except (OSError, ValueError):
                pass
        try:
            proc.stdin.close()
        except OSError:
            pass
        # the sounds end first, while the picture's encoder still finishes its frames: they no longer run on past the
        # stop, and the microphone's noise reduction (the slowest part of the saving) runs beside the encoder, on
        # another core, instead of after it inside the mux
        for p, f, _, _ in parts:                 # live sources never end by themselves: q's signal ends them
            if p.poll() is None:
                ok = _end(p, log) and ok
        restore_bt()                             # a Bluetooth headset moved to its headset profile for the microphone
        found = [(f, start_of(f), title) for _, f, title, _ in parts if f.exists() and f.stat().st_size > 0]
        found = [x for x in found if x[1] is not None]
        cleaning = []
        for f, _, title in found:
            if title == "Microphone":
                clean = f.with_name(f.name.replace(".mka", ".clean.mka"))
                try:
                    cleaning.append((f, clean, subprocess.Popen(clean_cmd(f, clean), stdout=log, stderr=log,
                                                                preexec_fn=_child(10))))
                except OSError:
                    pass
        ok = _end(proc, log, end_input=True, grows=video) and ok
        if self.proc is proc:                    # ffmpeg ended by itself (an error): the recording is over
            self.proc = None
        good = False
        done_clean = {}
        if cleaning:
            self.save = dict(self.save, stage="sounds")
        for f, clean, c in cleaning:
            try:
                if _wait_growing(c, clean, IDLE_END) and c.returncode == 0 and clean.exists() and clean.stat().st_size > 0:
                    done_clean[f] = clean
            except (OSError, subprocess.SubprocessError):
                pass
        # a microphone part cleaned already goes into the mux as it is; one whose cleaning failed is cleaned there
        found = [(done_clean.get(f, f), start, title) for f, start, title in found]
        if n and video.exists() and found:
            tmp = out.with_name(out.name + ".part")
            try:
                expect = video.stat().st_size + self.MUX_BYTES * n / FPS
            except OSError:
                expect = 0
            self.save = dict(self.save, stage="join", tmp=tmp, expect=expect)
            cmd = mux_cmd(video, t0, found, tmp)
            log.write(" ".join(cmd) + "\n")
            log.flush()
            try:
                m = subprocess.Popen(cmd, stdout=log, stderr=log, preexec_fn=_child(10))
                # as long as it takes: a long recording takes long, and it is stopped only when it no longer writes
                if _wait_growing(m, tmp, IDLE_END * 6) and m.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                    os.replace(tmp, out)
                    good = True
            except (OSError, subprocess.SubprocessError):
                pass
        if good:
            for f in [video] + [f for _, f, _, _ in parts] + list(done_clean.values()):
                try:
                    f.unlink()
                except OSError:
                    pass
        try:
            prog.unlink()                        # only the saving's progress: kept by no one
        except OSError:
            pass
        log.close()
        self.save = None
        self.saving = False
        self.done = (out, ok and good)


def _wait_growing(proc, path, idle):
    """Waits for proc to end for as long as the file it writes keeps growing; True when it ended. After idle
    seconds with no growth it is killed and False comes back."""
    size, still = -1, time.monotonic()
    while True:
        try:
            proc.wait(timeout=1)
            return True
        except subprocess.TimeoutExpired:
            pass
        try:
            now_size = Path(path).stat().st_size
        except OSError:
            now_size = -1
        if now_size != size:
            size, still = now_size, time.monotonic()
        elif time.monotonic() - still > idle:
            proc.kill()
            proc.wait()
            return False


def _end(proc, log, end_input=False, grows=None):
    """Waits for one ffmpeg to finish its file: SIGINT (what q does) when it does not end by itself. The picture's
    ffmpeg, its input closed, still encodes what it had: it is given as long as its file grows."""
    if end_input and grows is not None:
        try:
            proc.wait(timeout=2)
            return True
        except subprocess.TimeoutExpired:
            pass
        size, still = -1, time.monotonic()
        while time.monotonic() - still < IDLE_END:
            try:
                proc.wait(timeout=1)
                return True
            except subprocess.TimeoutExpired:
                pass
            try:
                now_size = Path(grows).stat().st_size
            except OSError:
                now_size = -1
            if now_size != size:
                size, still = now_size, time.monotonic()
        steps = ((signal.SIGINT, 30), (signal.SIGTERM, 5))
    else:
        steps = ((None, 10), (signal.SIGINT, 30), (signal.SIGTERM, 5)) if end_input else \
            ((signal.SIGINT, 30), (signal.SIGTERM, 5))
    for sig, wait in steps:
        if sig is not None:
            try:
                proc.send_signal(sig)
            except OSError:
                pass
        try:
            proc.wait(timeout=wait)
            return sig != signal.SIGTERM
        except subprocess.TimeoutExpired:
            continue
    proc.kill()
    return False
