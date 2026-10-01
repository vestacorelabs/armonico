#!/usr/bin/env python3
"""screen.py - the 800x480 screen of the Armonico Pi device, drawn with pygame on KMS.

It reads the lesson engine the way the lesson screen in a browser does: /state every
100 ms and /api/overview every few seconds, with the screen code from the data folder.
The buttons arrive from armonico_pi.py over MQTT.

Views, in the order ☰ walks through them:
  rest       the house with eyes under a night sky, or the Armonico video in a loop
  songs      the song list: ▲▼ move, ✓ starts a lesson, ✓ held plays the song
  free       free play: the octave, the last notes, the keys as they are pressed
  settings   connection, network, volume, pedal, ghost run, rest screen, hardware test
  test       the hardware test, from the settings: every key, button, touch and speaker
A lesson takes the screen by itself while it runs, and gives it back when it ends.

Rest screen (settings.json, "rest"):
  video      the bundled Armonico video, device/media/armonico-japanese.mp4
  animation  the house and the sky, without video or music (the default on a new install)
"""
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV = os.environ
DATA_DIR = Path(ENV.get("DATA_DIR", "/var/lib/armonico"))
DEV_DIR = DATA_DIR / "device"
SETTINGS_FILE = DEV_DIR / "settings.json"
FONT_DIR = DEV_DIR / "fonts"
# The rest video is intentionally fixed and shipped with the Device mini-project.
REST_VIDEO = Path(__file__).resolve().parent / "media" / "armonico-japanese.mp4"
MODES_DIR = Path("/etc/armonico/modes")                # saved local and external-broker profiles
MODE_REQUEST = DATA_DIR / "device-mode-request"     # the root unit armonico-device-mode.path acts on it
UI_URL = f"http://127.0.0.1:{ENV.get('LESSON_UI_PORT', '8099')}"
CODE_FILE = DATA_DIR / "screen_code"
VERSION = (Path(__file__).resolve().parent.parent / "VERSION")

MQTT_HOST = ENV.get("MQTT_HOST", "localhost")
MQTT_PORT = int(ENV.get("MQTT_PORT", "1883"))
MQTT_USER = ENV.get("MQTT_USER", "")
MQTT_PASS = ENV.get("MQTT_PASS", "")
MQTT_LOCAL = ENV.get("MQTT_LOCAL", "no") == "yes"

W, H, FPS = 800, 480, 30
# the wiring, the same settings armonico_pi.py reads: shown on the hardware test
KEY_PINS = ENV.get("DEVICE_KEY_PINS", "4,10,17,9,18,27,25,22,11,23,8,24,13").split(",")
BUTTON_PINS = dict(x.split(":") for x in ENV.get("DEVICE_BUTTON_PINS", "menu:7,down:5,up:6,ok:12").split(","))
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()


def key_total():
    """How many keys the device has: 13, C to C, the last the high C."""
    return 13
SKILL_NAMES = {"black": "black keys", "leap": "leaps", "long": "long parts", "rhythm": "rhythm", "memory": "from memory",
               "fluency": "fluency"}
REST_MODES = ("video", "animation")
REST_NAMES = {"video": "Armonico video", "animation": "Animation"}
LOOP_PAUSE_SECONDS = 1.0
SYMBOLS = set("✓▲▼☰♪▶◀→■")
BAR_H = 58                 # the 4 touch buttons along the bottom, the same 4 as on the device
BAR_SHOW = 5               # seconds the bar stays up over the video after a touch

# the palette of the approved screens
NIGHT, INK, DIM = (13, 20, 36), (233, 237, 245), (141, 151, 171)
GOLD, SKY, MINT, ROSE = (232, 182, 79), (143, 205, 246), (142, 227, 184), (243, 154, 168)
PANEL, LINE, TRACK = (22, 31, 51), (31, 42, 66), (38, 49, 75)
WHITE_KEY, BLACK_KEY, KEY_TEXT = (243, 244, 247), (28, 34, 51), (106, 115, 135)


def log(msg):
    print(msg, flush=True)


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    """Under the same lock as armonico_pi.py and the lesson engine, which write this file too."""
    import fcntl
    try:
        DEV_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = load_settings()
            data.update(changes)
            tmp = SETTINGS_FILE.with_suffix(".screen.tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(SETTINGS_FILE)
    except OSError as e:
        log(f"Settings not saved: {e}")


def lan_ip():
    # always IPv4, from the default route's interface: hostname -I may list IPv6 first
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import diag
        return diag.ipv4() or "no network"
    except Exception:
        return "no network"


def lesson_display_stage(show):
    """Translate the lesson engine's exact show state into a visual help stage."""
    if show is True:
        return "numbers"
    if show == "keys":
        return "keys"
    return "memory"


def lesson_labels_visible(stage, label_mode):
    """Show only piano-note labels on the Pi, never desktop-computer key labels."""
    return stage == "numbers" and label_mode in ("num", "abc", "do")


# ----------------------------------------------------------------------
# The lesson engine, over HTTP on this machine
# ----------------------------------------------------------------------
class Engine:
    def __init__(self):
        self.state, self.overview = {}, {}
        self.ok, self.why, self.over_why = False, "", ""
        # set by the screen: true while nothing needs the state quickly (asleep, or the rest screen with
        # no input for a while, and no lesson). /state is then asked once a second, not ten times.
        self.slow = None
        threading.Thread(target=self._poll, daemon=True).start()

    POLL_FAST, POLL_SLOW = 0.1, 1.0

    def _quiet(self):
        try:
            return bool(self.slow and self.slow())
        except Exception:
            return False

    def _pause(self):
        """The wait between two looks at /state: POLL_FAST, or up to POLL_SLOW while quiet. The slow wait
        is cut into POLL_FAST steps, so a touch, a key or a lesson brings the quick looks back at once."""
        waited = 0.0
        while True:
            time.sleep(self.POLL_FAST)
            waited += self.POLL_FAST
            if waited >= self.POLL_SLOW - 1e-9 or not self._quiet():
                return

    def _req(self, path, body=None, timeout=3):
        headers = {"Host": "localhost"}
        try:
            headers["X-Screen-Code"] = CODE_FILE.read_text().strip()
        except OSError:
            pass
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(UI_URL + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw[:1] in (b"{", b"[") else {}

    def upload(self, name, blob, on_result=None):
        """A MIDI file into the songs, as the lesson screen's upload does (/songs/upload, the file as
        it is, its name in X-Filename). on_result(ok, answer or error)."""
        def go():
            headers = {"Host": "localhost", "X-Filename": urllib.parse.quote(name), "Content-Type": "audio/midi"}
            try:
                headers["X-Screen-Code"] = CODE_FILE.read_text().strip()
            except OSError:
                pass
            req = urllib.request.Request(UI_URL + "/songs/upload", data=blob, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                ans = json.loads(raw) if raw[:1] == b"{" else {}
                ok = ans.get("ok", True) is not False
                on_result and on_result(ok, ans)
            except urllib.error.HTTPError as e:
                try:
                    why = json.loads(e.read() or b"{}").get("why", "")
                except ValueError:
                    why = ""
                on_result and on_result(False, {"why": why or f"HTTP {e.code}"})
            except (OSError, ValueError) as e:
                on_result and on_result(False, {"why": str(e)})
        threading.Thread(target=go, daemon=True).start()

    def post(self, path, body=None, on_result=None, timeout=3):
        """Send a UI action off-thread; optionally report HTTP/transport success. timeout: for an action the
        engine takes long over (a profile is deleted only after a backup of it is written and read back)."""
        def go():
            try:
                self._req(path, body or {}, timeout)
            except urllib.error.HTTPError as e:
                log(f"POST {path}: HTTP {e.code}")
                if on_result is not None:
                    on_result(False, e.code)
            except (OSError, ValueError) as e:
                log(f"POST {path}: {e}")
                if on_result is not None:
                    on_result(False, "connection")
            else:
                if on_result is not None:
                    on_result(True, None)
        threading.Thread(target=go, daemon=True).start()

    def _poll(self):
        last_over = 0
        while True:
            try:
                self.state = self._req("/state?seen=1")
                self.ok = True
            except (OSError, ValueError) as e:
                self.ok, self.why = False, str(e)
                time.sleep(1)
                continue
            if time.time() - last_over > (8 if self.overview else 2):
                last_over = time.time()
                try:
                    self.overview, self.over_why = self._req("/api/overview", timeout=8), ""
                except (OSError, ValueError) as e:
                    self.over_why = str(e)           # shown where the songs would be
            self._pause()


# ----------------------------------------------------------------------
# Idle music and video
# ----------------------------------------------------------------------
def read_srt(path):
    """[(start, end, text)] in seconds from an SRT file; [] when there is none."""
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    def sec(t):
        h, m, s = t.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    out = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", line)
            if m:
                text = " ".join(x.strip() for x in lines[i + 1:] if x.strip())
                out.append((sec(m.group(1)), sec(m.group(2)), re.sub(r"<[^>]+>", "", text)))
                break
    return out


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_NOLOCK = _NoLock()


class MpvPlayer:
    # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
    """The rest video in mpv, a real video player, in its own full-screen window over the screen's window (a big HDMI
    screen alone under labwc). mpv decodes, scales and shows the picture and plays the sound itself, as any video
    player does: no frame passes through Python. The screen's own window stays under it.

    When the screen has to show something over the video (the buttons after a touch, a question, a message), the
    video track is switched off: mpv closes its window and the sound goes on, uncut; switched on again, the window
    comes back where the video is. A click, a touch or a key on mpv's window comes back as a message on mpv's control
    socket (touched), and the screen brings its buttons up for it, as a touch on the video always did."""

    TITLE = "Armonico video"
    TOUCH = "armonico-touch"
    KEYS = ("MBTN_LEFT", "MBTN_RIGHT", "MBTN_MID", "WHEEL_UP", "WHEEL_DOWN", "ENTER", "KP_ENTER", "SPACE", "ESC",
            "UP", "DOWN", "LEFT", "RIGHT", "BS", "TAB", "q", "f")

    SMALL = "Armonico video small"   # the second, silent player on the 5" when both screens are on (Media)

    def __init__(self, path, volume_pct, start=0.0, title=None, sound=True, screen=None):
        run = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
        tag = "small" if not sound else "main"
        self.title = title or self.TITLE
        self.sock_path = run / f"armonico-mpv-{os.getpid()}-{tag}.sock"
        self.conf = run / f"armonico-mpv-{os.getpid()}-{tag}.conf"
        self.pos = None                    # (seconds into the video, monotonic time it was read): kept in step
        self.replies = {}                  # mpv's answers to get_property, by request id (report)
        self.conf.write_text("".join(f"{k} script-message {self.TOUCH}\n" for k in self.KEYS))
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        cmd = [
            "mpv", "--no-config", "--really-quiet", "--msg-level=all=error",
            # full screen on its screen, by the screen's name (HDMI-A-1, DSI-1: device/session.sh); --panscan=1: it
            # fills the screen without bars, as the screen always showed it (a 5" of another shape is cut a little)
            f"--title={self.title}", "--no-border", "--fs", "--panscan=1.0",
            *([f"--fs-screen-name={screen}"] if screen and re.fullmatch(r"[\w.-]+", screen) else []),
            "--loop-file=inf", "--no-osc", "--osd-level=0", "--no-osd-bar", "--cursor-autohide=always",
            "--no-input-default-bindings", f"--input-conf={self.conf}", "--input-cursor=yes",
            "--no-terminal", f"--input-ipc-server={self.sock_path}",
            # light scaling, as mpv's own "fast" profile: the V3D of a Pi stretching 1280x720 to the screen
            "--scale=bilinear", "--dscale=bilinear", "--cscale=bilinear", "--deband=no", "--dither-depth=no",
            "--correct-downscaling=no", "--linear-downscaling=no", "--sigmoid-upscaling=no",
            "--gamma=12",                              # the footage is dark: lifted a little, as GStreamer's gamma did
            *(["--ao=pulse,pipewire,alsa", f"--audio-client-name={self.TITLE}",
               f"--volume={max(0, min(100, int(round(volume_pct))))}", "--volume-max=100"] if sound
              else ["--no-audio"]),
            f"--start={max(0.0, start):.2f}", str(path),
        ]

        def die_with_us():
            # mpv ends with the screen's program, even if that one is killed: no video left over the next start
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").prctl(1, 15)      # PR_SET_PDEATHSIG, SIGTERM
            except Exception:
                pass
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, preexec_fn=die_with_us)
        self.started = time.monotonic()
        self.conn = None
        self.shown = True
        self.touched_at = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True, name="mpv-ipc").start()

    def alive(self):
        return self.proc.poll() is None

    def error_text(self):
        try:
            return (self.proc.stderr.read() or b"").decode(errors="replace").strip()[-400:]
        except Exception:
            return ""

    def _connect(self):
        import socket
        for _ in range(100):                           # up to 5 s for mpv to open its socket
            if not self.alive():
                return None
            try:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.connect(str(self.sock_path))
                return c
            except OSError:
                time.sleep(0.05)
        return None

    def _reader(self):
        c = self._connect()
        if c is None:
            return
        with self.lock:
            self.conn = c
        self.command("observe_property", 1, "time-pos")
        buf = b""
        while True:
            try:
                data = c.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("event") == "client-message" and self.TOUCH in (msg.get("args") or []):
                    self.touched_at = time.monotonic()
                elif "request_id" in msg and msg.get("request_id"):
                    self.replies[msg["request_id"]] = msg.get("data")
                elif msg.get("event") == "property-change" and msg.get("name") == "time-pos":
                    v = msg.get("data")
                    self.pos = (float(v), time.monotonic()) if isinstance(v, (int, float)) else None

    def command(self, *args):
        with self.lock:
            c = self.conn
        if c is None:
            return False
        try:
            c.sendall((json.dumps({"command": list(args)}) + "\n").encode())
            return True
        except OSError:
            return False

    def show(self, on):
        """The picture on (mpv's window over the screen) or off (the window closes, the sound goes on)."""
        if on != self.shown and self.command("set_property", "vid", "auto" if on else "no"):
            self.shown = on

    def touched(self):
        """True once for each click, touch or key on mpv's window."""
        if self.touched_at:
            self.touched_at = 0.0
            return True
        return False

    def volume(self, pct):
        self.command("set_property", "volume", max(0, min(100, int(round(pct)))))

    REPORT = ("avsync", "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "estimated-vf-fps")

    def ask_report(self):
        """Asks mpv how the playback goes; the answers come into replies (report reads them a moment later)."""
        for i, name in enumerate(self.REPORT, 1):
            with self.lock:
                c = self.conn
            if c is None:
                return
            try:
                c.sendall((json.dumps({"command": ["get_property", name], "request_id": 100 + i}) + "\n").encode())
            except OSError:
                return

    def report(self):
        """One line for session.log: the sound and the picture apart (seconds), frames dropped, frames late."""
        r = {name: self.replies.get(100 + i) for i, name in enumerate(self.REPORT, 1)}
        if all(v is None for v in r.values()):
            return ""
        av, fps = r["avsync"], r["estimated-vf-fps"]
        return (f"sound and picture {av * 1000:+.0f} ms apart, " if isinstance(av, (int, float)) else "") + \
            f"dropped {r['frame-drop-count']} (decoder {r['decoder-frame-drop-count']}), " \
            f"late {r['vo-delayed-frame-count']}" + (f", {fps:.1f} frames a second" if isinstance(fps, (int, float)) else "")

    def now(self):
        """Seconds into the video now, from mpv's last report, or None."""
        p = self.pos
        if p is None or time.monotonic() - p[1] > 1.0:
            return None
        return p[0] + time.monotonic() - p[1]

    def close(self):
        if self.alive():
            self.command("quit")
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        with self.lock:
            c, self.conn = self.conn, None
        if c is not None:
            try:
                c.close()
            except OSError:
                pass
        for f in (self.sock_path, self.conf):
            try:
                f.unlink()
            except OSError:
                pass


class Media:
    """Idle audio and video, played by their normal playback engines.

    Audio-only files use pygame. Video files use one GStreamer playbin pipeline for
    both the audio and video streams. The pipeline reads the media timestamps and
    drives both outputs together; pygame only displays the video frames it receives.
    """

    VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".m4v")
    VIDEO_GAMMA = 1.25        # above 1 brightens (GStreamer's gamma element); 1.0 leaves the picture as it is
    FORCE_SW_DECODERS = 1 << 12   # playbin's force-sw-decoders flag: the 1280x720 video decodes in software

    def __init__(self, pg):
        self.pg = pg
        self.frame = None
        self.playing = None
        self.lock = threading.Lock()
        self.queue, self.index = [], 0
        self.status = ""
        self.vol = 0.8 ** 3       # 80% before the engine says, on the same curve as set_volume
        self.lyrics = []            # (start, end, text) from an SRT beside the file
        self.size = (W, H)          # the frame size; device/ui sets the screen's own (width a multiple of 4)
        # RGBx frames reach the screen without a 3-to-4 byte conversion on every frame; RGB on an
        # older pygame that does not take them
        try:
            pg.image.frombuffer(b"\0" * 4, (1, 1), "RGBX")
            self.fmt, self.bpp, self.pg_fmt = "RGBx", 4, "RGBX"
        except Exception:
            self.fmt, self.bpp, self.pg_fmt = "RGB", 3, "RGB"

        self.gst = None
        self.gst_player = None
        self.gst_sink = None
        self.gst_bus = None
        self.gst_sample_handler = None
        self.gst_ended = False
        self.gst_loop = False
        self.gst_loop_restart_at = None
        self.plain = False        # set after a video sent no picture: no brightening, no forced decoder
        # gpu: frames come out at the video's own size (1280x720) and the GPU stretches them to the screen in a
        # screen's own renderer (DisplayVideo), instead of the CPU scaling every frame to 1920x1080. Set by device/ui
        # on a big screen under labwc; the size is then the stream's, read from each frame.
        self.gpu = False
        self.mpv = None           # MpvPlayer: the video in mpv on that same big screen (_play_mpv)
        self.mpv_small = None     # ... and a second one, silent, on the 5" when both screens are on (mpv_both)
        self.mpv_both = False     # set by device/ui under labwc with both screens
        self.mpv_fails = 0
        self.vol_pct = 80

    def files(self, mode):
        # Only one bundled rest video is supported. Animation is drawn by the screen itself.
        if mode == "video":
            return [REST_VIDEO] if REST_VIDEO.exists() else []
        return []

    def start(self, mode):
        self.stop()
        self.queue, self.index = self.files(mode), 0
        if self.queue:
            self._play(self.queue[0])

    def _get_gst(self):
        if self.gst is None:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self.gst = Gst
        return self.gst

    def _make_video_sink(self, Gst):
        """Give GStreamer RGB frames for pygame while keeping its normal media clock."""
        if self.gpu:
            self.native = False
            self.fmt, self.bpp, self.pg_fmt = "RGBx", 4, "RGBX"   # a texture takes these as they are
        else:
            self._match_display()
        sink_bin = Gst.Bin.new("armonico-video-sink")
        convert = Gst.ElementFactory.make("videoconvert", "armonico-video-convert")
        scale = Gst.ElementFactory.make("videoscale", "armonico-video-scale")
        capsfilter = Gst.ElementFactory.make("capsfilter", "armonico-video-caps")
        appsink = Gst.ElementFactory.make("appsink", "armonico-video-frames")
        if any(element is None for element in (sink_bin, convert, scale, capsfilter, appsink)):
            raise RuntimeError("GStreamer video plugins are missing")
        # Frames come out at the screen's own size, so the screen only copies them. The scaling
        # runs here, on all four cores and off the drawing loop, where it does not slow the touches.
        for element in (convert, scale):
            if element.find_property("n-threads") is not None:
                element.set_property("n-threads", 4)

        # Preserve the original aspect ratio and letterbox into the screen dimensions.
        scale.set_property("add-borders", True)
        # The picture is lifted while it is still the video's own size (1280x720), before the scaling to the screen, where it costs
        # next to nothing: the footage is dark and on an HDMI screen it came out dimmer than on the 5".
        lift = None if self.plain else Gst.ElementFactory.make("gamma", "armonico-video-gamma")
        if lift is not None:
            lift.set_property("gamma", self.VIDEO_GAMMA)
        if self.gpu:
            # the video's own size: videoscale only squares the pixels, the GPU does the stretching
            scale.set_property("add-borders", False)
            caps = f"video/x-raw,format={self.fmt},pixel-aspect-ratio=1/1"
        else:
            caps = f"video/x-raw,format={self.fmt},width={self.size[0]},height={self.size[1]},pixel-aspect-ratio=1/1"
        capsfilter.set_property("caps", Gst.Caps.from_string(caps))
        # GStreamer presents each frame at its media timestamp. If pygame is busy,
        # discard stale frames rather than letting a video queue fall behind the audio.
        appsink.set_property("sync", True)
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 2)
        appsink.set_property("drop", True)
        appsink.set_property("enable-last-sample", False)

        # Lifted and scaled first, in the decoder's own format (half the bytes of RGB), then turned into RGB
        chain = ([lift] if lift is not None else []) + [scale, convert, capsfilter, appsink]
        for element in chain:
            sink_bin.add(element)
        for left, right in zip(chain, chain[1:]):
            if not left.link(right):
                raise RuntimeError("Could not link GStreamer video output")
        ghost = Gst.GhostPad.new("sink", chain[0].get_static_pad("sink"))
        if ghost is None or not sink_bin.add_pad(ghost):
            raise RuntimeError("Could not connect GStreamer video output")
        self.gst_sink = appsink
        self.gst_sample_handler = appsink.connect("new-sample", self._on_video_sample)
        return sink_bin

    def _on_video_sample(self, sink):
        """Copy the current timestamped frame; the callback runs on GStreamer's thread."""
        Gst = self.gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buffer = sample.get_buffer()
        if buffer is None:
            return Gst.FlowReturn.OK
        if self.gpu:
            try:
                st = sample.get_caps().get_structure(0)
                ok_w, w = st.get_int("width")
                ok_h, h = st.get_int("height")
                if ok_w and ok_h and (w, h) != self.size:
                    with self.lock:
                        self.size = (w, h)
            except Exception:
                pass
        expected = self.size[0] * self.size[1] * self.bpp
        row = self.size[0] * self.bpp
        offset, stride = self._frame_layout(buffer, row)
        if stride != row:
            # rows padded or placed apart in memory (a video meta on the buffer says so): taken row by row, or the
            # picture comes out slanted in bands that move with the video
            h = self.size[1]
            if buffer.get_size() >= offset + stride * (h - 1) + row:
                data = buffer.extract_dup(offset, stride * (h - 1) + row)
                raw = b"".join(data[i * stride:i * stride + row] for i in range(h))
                with self.lock:
                    self.frame = raw
                    self.seq = getattr(self, "seq", 0) + 1
            return Gst.FlowReturn.OK
        if buffer.get_size() >= offset + expected:
            raw = buffer.extract_dup(offset, expected)  # one copy
            with self.lock:
                self.frame = raw
                self.seq = getattr(self, "seq", 0) + 1     # the screen draws when a new frame came
        return Gst.FlowReturn.OK

    def _frame_layout(self, buffer, row):
        """(offset, stride) of the frame's first plane: from the buffer's video meta when it carries one (a decoder or
        a converter can pad the rows), else packed rows from the start."""
        try:
            if getattr(self, "_gst_video", None) is None:
                import gi
                gi.require_version("GstVideo", "1.0")
                from gi.repository import GstVideo
                self._gst_video = GstVideo
            meta = self._gst_video.buffer_get_video_meta(buffer)
            if meta is not None and meta.stride[0] >= row:
                return int(meta.offset[0]), int(meta.stride[0])
        except Exception:
            self._gst_video = False if getattr(self, "_gst_video", None) is None else self._gst_video
        return 0, row

    def _play_video(self, path, single, silent=False):
        """Play the original audio and video streams together, as a normal media player does.
        silent: the sound output would not open, so the picture plays on its own, still on time."""
        Gst = self._get_gst()
        player = Gst.ElementFactory.make("playbin", "armonico-media-player")
        audio_sink = None if silent else self._audio_sink(Gst)
        if silent:
            audio_sink = Gst.ElementFactory.make("fakesink", "armonico-audio-output")
        if silent and audio_sink is not None:
            audio_sink.set_property("sync", True)
        self.gst_silent = silent
        if player is None or audio_sink is None:
            raise RuntimeError("GStreamer playback or ALSA output plugin is missing")

        player.set_property("uri", path.resolve().as_uri())
        try:
            if self.plain:
                raise RuntimeError("plain mode")
            # A hardware decoder hands over frames in memory the CPU reads slowly, and the scaling and
            # conversion that follow read every one of them; at this size the software decoder is lighter.
            player.set_property("flags", int(player.get_property("flags")) | self.FORCE_SW_DECODERS)
        except Exception as e:
            if not self.plain:
                log(f"Video: software decoding not forced ({e})")
        player.set_property("video-sink", self._make_video_sink(Gst))
        player.set_property("audio-sink", audio_sink)
        player.set_property("volume", self.vol)
        try:
            player.set_property("mute", False)
        except Exception:
            pass
        self.gst_player = player
        self.gst_bus = player.get_bus()
        self.gst_loop = single
        self.gst_ended = False
        self.gst_loop_restart_at = None
        self.wd = [getattr(self, "seq", 0), time.monotonic()]     # the stall watchdog (tick): last frame seen, when
        result = player.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._close_gst()
            raise RuntimeError("GStreamer could not start video playback")

    def _audio_sink(self, Gst):
        """The video's sound through pulsesink into pipewire-pulse, as the desktop plays it. pulsesink
        keeps a ring buffer of its own (GstAudioBaseSink): measured on the Pi, its stream had no dropout
        in 4 runs of up to 50 seconds. pipewiresink has none (a GstBaseSink that sends each buffer only
        when it is due) and dropped 2 to 8 times in 15 seconds, heard as jumps. pipewiresink, then
        alsasink with a longer buffer, stay the fallbacks when pipewire-pulse is not there."""
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime and Path(runtime, "pulse", "native").exists():
            sink = Gst.ElementFactory.make("pulsesink", "armonico-audio-output")
            if sink is not None:
                try:
                    # the name pipewire-pulse matches (device/pipewire/80-armonico-pulse.conf), and the
                    # same 2048-frame blocks asked here too, while the video plays (see that file)
                    sink.set_property("client-name", "Armonico video")
                    sink.set_property("stream-properties",
                                      Gst.Structure.new_from_string("props,node.force-quantum=(string)2048,"
                                                                    "state.restore-props=(string)false,"
                                                                    "state.restore-target=(string)false"))
                except Exception:
                    pass
                try:
                    # the stream unmuted and at a known level: _fade_out leaves it at 0 when a video closes, and that 0
                    # must not be where the next one starts. playbin sets the video's own volume on it right after
                    sink.set_property("volume", 1.0)
                    sink.set_property("mute", False)
                except Exception:
                    pass
                return sink
        sink = Gst.ElementFactory.make("pipewiresink", "armonico-audio-output")
        if sink is not None:
            try:
                sink.set_property("client-name", "Armonico video")
            except Exception:
                pass
            return sink
        sink = Gst.ElementFactory.make("alsasink", "armonico-audio-output")
        if sink is not None:
            sink.set_property("buffer-time", 400000)      # microseconds: 400 ms ahead
            sink.set_property("latency-time", 40000)      # in 40 ms periods
        return sink

    def _close_gst(self):
        player, sink, handler = self.gst_player, self.gst_sink, self.gst_sample_handler
        self.gst_player = self.gst_sink = self.gst_bus = self.gst_sample_handler = None
        self.gst_ended = self.gst_loop = False
        self.gst_loop_restart_at = None
        if player is not None:
            self._fade_out(player)
            try:
                player.set_state(self.gst.State.NULL)
            except Exception:
                pass
        if sink is not None and handler is not None:
            try:
                sink.disconnect(handler)
            except Exception:
                pass

    FADE_STEPS = 5          # the sound's way out when a video is closed: 5 steps in about 40 ms
    FADE_MS = 40

    def _fade_out(self, player):
        """The sound brought to 0 over about 40 ms before the player is stopped: State.NULL cuts the
        stream at whatever level it is at, heard as a click when leaving the rest screen's video. The
        level is lowered on the sound output's own stream (pulsesink's volume, which also reaches what
        its buffer still holds), else on the player's. A few short steps in the caller's thread: one
        frame of the screen, once. A silent video (no sound output) has nothing to fade."""
        if getattr(self, "gst_silent", False) or not self.FADE_MS:
            return
        try:
            if player.get_state(0)[1] != self.gst.State.PLAYING:
                return
        except Exception:
            return
        target, prop = player, "volume"
        try:
            out = player.get_property("audio-sink")
            if out is not None and out.find_property("volume") is not None:
                target = out
        except Exception:
            pass
        try:
            start = float(target.get_property(prop))
        except Exception:
            return
        gap = self.FADE_MS / 1000 / self.FADE_STEPS
        try:
            for i in range(1, self.FADE_STEPS + 1):
                target.set_property(prop, start * (1 - i / self.FADE_STEPS))
                time.sleep(gap)
        except Exception:
            pass

    MPV_FAILS = 2           # mpv ended by itself this many times: the video goes back to GStreamer for the session

    def _play_mpv(self, path):
        """The video in mpv (MpvPlayer). False when mpv is not installed or has failed before: GStreamer plays it.
        Both screens: a second mpv on the 5", without sound, kept in step with the first (keep_in_step)."""
        if self.mpv_fails >= self.MPV_FAILS or not shutil.which("mpv") or ENV.get("ARMONICO_MPV", "1") == "0":
            return False
        try:
            own = os.environ.get("ARMONICO_SCREEN_OUTPUT")          # the device's screen (device/session.sh)
            # big = os.environ.get("ARMONICO_MIRROR") if self.mpv_both else own    # HDMI off (DSI only)
            big = own
            self.mpv = MpvPlayer(path, self.vol_pct, screen=big)
            if self.mpv_both:
                self.mpv_small = MpvPlayer(path, 0, title=MpvPlayer.SMALL, sound=False, screen=own)
            self.step_at = time.monotonic()
            log(f"Video: playing in mpv{' on both screens' if self.mpv_both else ''}")
            return True
        except Exception as e:
            self._close_mpv()
            self.mpv_fails = self.MPV_FAILS
            log(f"Video: mpv did not start ({type(e).__name__}: {e}): GStreamer plays it")
            return False

    def _close_mpv(self):
        m, self.mpv = self.mpv, None
        small, self.mpv_small = self.mpv_small, None
        for p in (m, small):
            if p is not None:
                p.close()

    def players(self):
        return [p for p in (self.mpv, self.mpv_small) if p is not None]

    def external(self):
        """The video is playing in mpv's own window (MpvPlayer), not as frames for the screen."""
        return self.mpv is not None

    def mpv_show(self, on):
        for p in self.players():
            p.show(on)

    def mpv_touched(self):
        hit = False
        for p in self.players():
            hit = p.touched() or hit
        return hit

    REPORT_EVERY = 60       # seconds: how the video plays, in session.log (for armonico-pi-report)

    def mpv_report(self):
        now = time.monotonic()
        at = getattr(self, "report_at", None)
        if at is None:
            self.report_at = now
            return
        if now - at >= self.REPORT_EVERY and not getattr(self, "report_asked", False):
            self.report_asked = True
            for p in self.players():
                p.ask_report()
        elif now - at >= self.REPORT_EVERY + 1:
            self.report_at, self.report_asked = now, False
            for name, p in (("big screen", self.mpv), ("5\"", self.mpv_small)):
                line = p.report() if p is not None else ""
                if line:
                    log(f"Video in mpv ({name}): {line}")
            if self.mpv is not None and self.mpv_small is not None:
                a, b = self.mpv.now(), self.mpv_small.now()
                if a is not None and b is not None:
                    log(f"Video in mpv: the two screens {abs(a - b) * 1000:.0f} ms apart")

    STEP_EVERY = 1.0        # seconds between two looks at the two players' places in the video
    STEP_OFF = 0.08         # seconds apart that the 5"'s player is brought back to the big screen's
    STEP_REST = 3.0         # seconds after a correction before the next one

    def keep_in_step(self):
        """The 5"'s player at the big screen's place in the video (that one has the sound and leads). Both start
        together and run at the same speed; a start a little later, or a window shown again, moves them apart
        by a fraction of a second, and the 5"'s is moved back to where the sound is."""
        big, small = self.mpv, self.mpv_small
        now = time.monotonic()
        if big is None or small is None or now - getattr(self, "step_at", 0.0) < self.STEP_EVERY or not big.shown:
            return
        self.step_at = now
        a, b = big.now(), small.now()
        if a is None or b is None or abs(a - b) > 30:          # not known yet, or one has just looped back to 0
            return
        if abs(a - b) > self.STEP_OFF and now - getattr(self, "stepped_at", 0.0) > self.STEP_REST:
            self.stepped_at = now
            small.command("seek", round(a + 0.05, 3), "absolute+exact")   # the seek itself takes a moment

    def _play(self, path):
        self._close_mpv()
        self._close_gst()
        try:
            if self.sound_ok():
                self.pg.mixer.music.stop()
        except self.pg.error:
            pass
        self.playing = path
        self.frame = None
        self.status = ""
        self.lyrics = read_srt(path.with_suffix(".srt"))
        single = len(self.queue) == 1

        if path.suffix.lower() in self.VIDEO_SUFFIXES:
            if (self.gpu or self.mpv_both) and self._play_mpv(path):
                return
            try:
                try:
                    self._play_video(path, single)
                except RuntimeError as e:
                    # the sound output did not open (busy, or the HDMI mix not running): the picture
                    # still plays, on the pipeline's own clock, and the reason goes to the journal
                    self._close_gst()
                    log(f"The video did not start with sound ({e}): playing it without sound")
                    self._play_video(path, single, silent=True)
            except Exception as e:
                # Never split the audio and picture into independent players as a fallback.
                self._close_gst()
                self.playing = None
                self.status = f"Video playback failed: {e}"
                log(self.status)
            return

        try:
            if not self.sound_ok():
                raise self.pg.error("no sound card")
            self.pg.mixer.music.load(str(path))
            self.pg.mixer.music.set_volume(self.vol)
            self.pg.mixer.music.play(loops=-1 if single else 0)
        except (self.pg.error, OSError) as e:
            self.status = f"Audio playback failed: {e}"
            log(self.status)

    def _poll_gst(self):
        if self.gst_bus is None:
            return
        Gst = self.gst
        while True:
            message = self.gst_bus.pop()
            if message is None:
                return
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                detail = error.message if error else "unknown GStreamer error"
                if debug:
                    log(f"Video playback details: {debug}")
                src = message.src.get_name() if message.src is not None else ""
                if src.startswith("armonico-audio") and not getattr(self, "gst_silent", False) and self.playing is not None:
                    # a sound output that does not open must not take the picture with it
                    log(f"The video's sound did not open ({detail}): the video plays without sound")
                    self.status = ""
                    path, single = self.playing, self.gst_loop
                    self._close_gst()
                    try:
                        self._play_video(path, single, silent=True)
                    except Exception as e:
                        self._close_gst()
                        self.playing = None
                        self.status = f"Video playback failed: {e}"
                        log(self.status)
                    return
                self.status = f"Video playback failed: {detail}"
                log(self.status)
                self._close_gst()
                self.playing = None
                self.frame = None
                self.queue = []
                return
            if message.type == Gst.MessageType.EOS:
                if self.gst_loop and self.gst_player is not None:
                    # Keep the final frame visible for a short pause, then restart both streams.
                    # The same GStreamer pipeline continues to own audio/video timing.
                    self.gst_loop_restart_at = time.monotonic() + LOOP_PAUSE_SECONDS
                else:
                    self.gst_ended = True

    STALL_SECONDS = 5

    def _watchdog(self):
        """A video that plays but sends no picture: started again, and the second time without sound.

        After a change of screens the sound output is being set up again in the same seconds, and the
        video's sound output could wait on it without ever starting: no frame came, no error came, and the
        rest screen showed the animation though the video was chosen. Now no frame for 5 seconds starts
        the video again; if that stalls as well, it plays without sound, so the picture is always there."""
        if self.gst_player is None or self.gst_loop_restart_at is not None:
            if hasattr(self, "wd"):
                self.wd[1] = time.monotonic()
            return
        now, seq = time.monotonic(), getattr(self, "seq", 0)
        wd = getattr(self, "wd", None)
        if wd is None or seq != wd[0]:
            self.wd = [seq, now]
            self.stalls = 0
            return
        if now - wd[1] < self.STALL_SECONDS:
            return
        try:
            state = self.gst_player.get_state(0)[1].value_nick
        except Exception:
            state = "?"
        self.stalls = getattr(self, "stalls", 0) + 1
        if self.stalls > 3:
            self._close_gst()
            self.playing = None
            self.status = "Video playback failed: no picture came"
            log(f"{self.status} after {self.stalls - 1} tries")
            return
        silent = getattr(self, "gst_silent", False) or self.stalls >= 2
        if not self.plain:
            self.plain = True       # the first try added the brightening and the software decoder: dropped now
            log("The video starts again plain (no brightening, no forced software decoder)")
        log(f"The video sent no picture for {self.STALL_SECONDS} s (state {state}, try {self.stalls}): "
            f"starting it again{' without sound' if silent else ''}")
        path, single = self.playing, self.gst_loop
        self._close_gst()
        try:
            self._play_video(path, single, silent=silent)
        except Exception as e:
            self._close_gst()
            self.playing = None
            self.status = f"Video playback failed: {e}"
            log(self.status)

    def tick(self):
        """Advance the file list when the active audio or video file has ended."""
        if self.mpv is not None:
            dead = next((p for p in self.players() if not p.alive()), None)
            if dead is not None:
                # mpv ended by itself (it loops the file, so it never ends on its own): once more, then GStreamer
                self.mpv_fails += 1
                log(f"Video: mpv ended ({dead.proc.returncode}): {dead.error_text() or 'no message'}")
                self._close_mpv()
                if self.playing is not None:
                    self._play(self.playing)
                return
            self.keep_in_step()
            self.mpv_report()
            return
        self._poll_gst()
        if self.playing is None:
            return
        self._watchdog()
        if self.playing is None:
            return
        if (self.gst_player is not None and self.gst_loop and
                self.gst_loop_restart_at is not None and time.monotonic() >= self.gst_loop_restart_at):
            player = self.gst_player
            self.gst_loop_restart_at = None
            flags = self.gst.SeekFlags.FLUSH | self.gst.SeekFlags.KEY_UNIT
            if player.seek_simple(self.gst.Format.TIME, flags, 0):
                player.set_state(self.gst.State.PLAYING)
            else:
                # Some containers do not support seeking after EOS; reopen after the same pause.
                self._play(self.playing)
            return
        if len(self.queue) <= 1:
            return
        if self.gst_player is not None:
            ended = self.gst_ended
        else:
            ended = not (self.sound_ok() and self.pg.mixer.music.get_busy())
        if ended:
            self.index = (self.index + 1) % len(self.queue)
            self._play(self.queue[self.index])

    def stop(self):
        self._close_mpv()
        self._close_gst()
        self.playing, self.frame = None, None
        try:
            if self.sound_ok():
                self.pg.mixer.music.stop()
        except self.pg.error:
            pass

    def set_volume(self, pct):
        # cubed, as the keys' volume (armonico_pi.loudness): a straight share was hardly heard
        self.vol = (max(0, min(100, pct)) / 100) ** 3
        self.vol_pct = max(0, min(100, pct))             # mpv takes the percent and applies the same cubed curve
        if self.mpv is not None:
            self.mpv.volume(self.vol_pct)
        if self.gst_player is not None:
            try:
                self.gst_player.set_property("volume", self.vol)
            except Exception as e:
                log(f"Video volume: {e}")
        if self.sound_ok():
            self.pg.mixer.music.set_volume(self.vol)

    def sound_ok(self):
        """Return whether pygame's audio output is available for audio-only files."""
        if self.pg.mixer.get_init():
            return True
        if time.time() - getattr(self, "_retry", 0) > 10:
            self._retry = time.time()
            try:
                self.pg.mixer.init()
                return True
            except self.pg.error:
                pass
        return False

    def lyric(self):
        """Return the subtitle for the current media position."""
        if not self.lyrics or self.playing is None:
            return ""
        if self.gst_player is not None:
            try:
                ok, position = self.gst_player.query_position(self.gst.Format.TIME)
                if not ok:
                    return ""
                at = position / self.gst.SECOND
            except Exception:
                return ""
        else:
            if not self.sound_ok():
                return ""
            at = self.pg.mixer.music.get_pos() / 1000
        return next((text for start, end, text in self.lyrics if start <= at < end), "")

    def title(self):
        if self.playing is None:
            return ""
        return re.sub(r"[-_]+", " ", self.playing.stem).strip().capitalize()

    def _match_display(self):
        """Frames in the screen's own byte order when it is the usual XRGB8888 of KMS and Wayland
        (blue, green, red, pad in memory): GStreamer gives them as BGRx and the screen copies them
        as they are, instead of swapping red and blue in every pixel of every frame (2 million
        pixels a frame on the 24")."""
        self.native = False
        if self.fmt == "BGRx":
            self.fmt, self.bpp = "RGBx", 4             # back to the frames frombuffer reads (RGBX)
        try:
            surf = self.pg.display.get_surface()
            if surf is not None and surf.get_bytesize() == 4 and surf.get_masks()[:3] == (0xFF0000, 0xFF00, 0xFF):
                self.fmt, self.bpp, self.native = "BGRx", 4, True
        except Exception:
            pass

    def paint_into(self, target):
        """The newest frame written straight into target (the screen) when it has the frame's size and
        byte order: one copy. False when it cannot, and surface() is used instead."""
        if not getattr(self, "native", False):
            return False
        with self.lock:
            raw = self.frame
        if raw is None:
            return False
        try:
            w, h = self.size
            if target.get_size() != (w, h) or target.get_bytesize() != 4 or target.get_pitch() != w * 4 \
                    or len(raw) != w * h * 4:
                return False
            target.get_buffer().write(raw, 0)
            return True
        except Exception:
            return False

    def surface(self):
        with self.lock:
            raw = self.frame
        if raw is None:
            return None
        if getattr(self, "native", False):
            try:
                dst = getattr(self, "dst", None)
                if dst is None or dst.get_size() != self.size:
                    dst = self.dst = self.pg.Surface(self.size, 0, self.pg.display.get_surface())
                if dst.get_pitch() == self.size[0] * 4 and len(raw) == self.size[0] * self.size[1] * 4:
                    dst.get_buffer().write(raw, 0)
                    return dst
            except Exception:
                pass
            return None
        if len(raw) != self.size[0] * self.size[1] * self.bpp:
            return None                                  # the size just changed (the GPU's way reads it from the stream)
        return self.pg.image.frombuffer(raw, self.size, self.pg_fmt)


# ----------------------------------------------------------------------
# The screen
# ----------------------------------------------------------------------
class Screen:
    VIEWS = ("rest", "songs", "free", "settings")
    CELLS = ("mode", "network", "volume", "pedal", "ghost", "rest", "stop", "profile", "test")
    UNSEEN = object()
    LABEL_MODES = (("num", "1 2 3"), ("abc", "C D E"), ("do", "Do Re Mi"), ("none", "None"))
    # the 4 buttons and the 13 keys; the power button is never one of them
    STOP_BUTTONS = ("ok", "up", "down", "menu") + tuple(f"key:{i}" for i in range(13))
    BUTTON_NAMES = {"ok": "✓ button", "up": "▲ button", "down": "▼ button", "menu": "☰ button",
                    **{f"key:{i}": n + " key" for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())},
                    "key:12": "high C key"}

    def __init__(self, pg):
        self.pg = pg
        self.surf = pg.display.get_surface()
        self.clock = pg.time.Clock()
        self.f = {}
        self.engine = Engine()
        self.media = Media(pg)
        self.settings = load_settings()
        self.view, self.sel_song, self.sel_cell = "rest", 0, 0
        self.song_first = 0  # stable scroll offset; tapping a row must not recenter the list
        self.octave, self.pedal = int(self.settings.get("octave", 4)), bool(self.settings.get("pedal", False))
        self.last_live, self.trail, self.flash = 0, [], {}
        self.touch_sent = []                   # keys touched on the screen whose engine copy is still due
        self.seen_volume = None
        self.hop, self.notes = 0.0, []
        self.alarm = self.song = False         # from the bridge, over MQTT
        self.song_paused, self.song_name = False, ""   # the song is paused, and which one plays (piano/play/*)
        self.paused_bridge, self.paused_hint = False, None   # what the bridge said last; a touch's own answer, held 3 s
        self.key_hold = False                  # a key was played: the idle music waits for a touch or a button
        self.was_busy = False                  # a lesson, alarm or song had the device at the last look
        self.media_try = 0.0
        self.bar_until = 0.0
        self.shift_msg = ("", 0.0)
        self.toast = ("", 0.0)
        self.summary_id, self.summary_until = None, 0
        self.ghost_id = self.UNSEEN           # the last ghost result shown; the one there at start is old
        self.confirm = None                   # a question before something that cannot be taken back
        self.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": ""}
        # Touchscreens may report the same physical tap as FINGERDOWN and a synthetic
        # mouse click. Also keep only one lesson-start request in flight.
        self.last_tap_at, self.last_tap_pos = 0.0, None
        self.lesson_request_pending = False
        self.lesson_request_at = 0.0
        self.lesson_request_song = ""
        self.lesson_request_accepted = False
        self.stars = [(random.randrange(W), random.randrange(int(H * 0.7)), random.random() * 4) for _ in range(60)]
        self.events = []
        self.ev_lock = threading.Lock()
        self.mqtt = self.start_mqtt()
        self.ip, self.ip_at = lan_ip(), time.time()
        self.ui = start_ui(self, pg)
        self.engine.slow = self.engine_quiet
        splash(pg, 3)                         # the bar full: the first frame of the screens fades out of it

    # ---- helpers
    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.f:
            path = FONT_DIR / ("Heebo-ExtraBold.ttf" if bold else "Heebo-Regular.ttf")
            self.f[key] = self.pg.font.Font(str(path) if path.exists() else None, size)
        return self.f[key]

    def symbol_font(self, size):
        key = ("sym", size)
        if key not in self.f:
            path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            self.f[key] = self.pg.font.Font(str(path) if path.exists() else None, size)
        return self.f[key]

    def render(self, s, size, color, bold=False):
        """Heebo for the words, DejaVu for ✓ ▲ ▼ ☰ ♪ ▶, which Heebo does not have."""
        s = str(s)
        runs, cur, sym = [], "", None
        for ch in s:
            is_sym = ch in SYMBOLS
            if sym is not None and is_sym != sym:
                runs.append((cur, sym))
                cur = ""
            cur, sym = cur + ch, is_sym
        if cur:
            runs.append((cur, sym))
        imgs = [(self.symbol_font(int(size * 0.85)) if is_sym else self.font(size, bold)).render(t, True, color)
                for t, is_sym in runs] or [self.font(size, bold).render("", True, color)]
        if len(imgs) == 1:
            return imgs[0]
        h = max(i.get_height() for i in imgs)
        out = self.pg.Surface((sum(i.get_width() for i in imgs), h), self.pg.SRCALPHA)
        x = 0
        for i in imgs:
            out.blit(i, (x, (h - i.get_height()) // 2))
            x += i.get_width()
        return out

    def text(self, s, size, color, pos, bold=False, anchor="topleft"):
        img = self.render(s, size, color, bold)
        r = img.get_rect(**{anchor: pos})
        self.surf.blit(img, r)
        return r

    def rest_mode(self):
        m = self.settings.get("rest", "animation")
        # Migrate the old silent-rest value to the clearer public name.
        if m == "none":
            m = "animation"
        return m if m in REST_MODES else "animation"

    PAUSE_HINT_S = 3.0

    def hint_paused(self, value):
        """A touch on the ▶/‖ of the playing song: the sign changes at once, without waiting for the broker.
        The bridge's own answer replaces it when it agrees; if it still says otherwise after 3 s it wins."""
        self.song_paused = value
        self.paused_hint = (value, time.monotonic() + self.PAUSE_HINT_S)

    def paused_state(self):
        """Whether the playing song shows as paused: the touch's own answer for 3 s, then the bridge's."""
        hint = self.paused_hint
        if hint is not None and time.monotonic() >= hint[1]:
            self.song_paused, self.paused_hint = self.paused_bridge, None
        return self.song_paused

    def pub(self, topic, payload, retain=False):
        if self.mqtt is not None:
            self.mqtt.publish(topic, payload, retain=retain)

    def start_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return None
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armonico-screen-{os.getpid()}")
        if MQTT_USER:
            c.username_pw_set(MQTT_USER, MQTT_PASS)

        def on_connect(cl, u, f, rc, p=None):
            cl.subscribe([("piano/device/button", 0),
                          ("piano/device/octave", 0), ("piano/device/pedal", 0),
                          ("piano/alarm/status", 0), ("piano/play/status", 0),
                          ("piano/play/paused", 0), ("piano/play/current", 0),
                          ("piano/device/button/up", 0), ("piano/device/power", 0),
                          ("piano/notes", 0), ("piano/device/keyboards", 0),
                          ("piano/device/test/key", 0), ("piano/device/key", 0), ("piano/device/test/usb", 0), ("piano/device/voice", 0),
                          ("piano/device/rec", 0)])

        def on_message(cl, u, msg):
            text = msg.payload.decode(errors="replace").strip()
            if msg.topic == "piano/notes":
                text += f" {time.monotonic():.4f}"       # when it came: free play's recording keeps the rhythm
            with self.ev_lock:
                self.events.append((msg.topic, text))

        c.on_connect, c.on_message = on_connect, on_message
        c.reconnect_delay_set(1, 30)
        try:
            c.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
            c.loop_start()
        except (OSError, ValueError) as e:
            log(f"MQTT: {e}")
            return None
        return c

    def lesson_on(self):
        return self.engine.state.get("mode", "idle") not in ("idle", None)

    QUIET_AFTER = 30          # seconds on the rest screen with no touch or key before /state is asked slowly

    def engine_quiet(self):
        """Engine._poll's slow looks: the screen asleep, or on the rest screen untouched for QUIET_AFTER
        seconds, and no lesson. Called from the poll thread: reads only."""
        ui = getattr(self, "ui", None)
        if ui is None or self.lesson_on():
            return False
        if ui.asleep():
            return True
        return getattr(ui, "view", None) == "rest" and time.monotonic() - getattr(ui, "last_input", 0) > self.QUIET_AFTER

    def say(self, msg, secs=2.5):
        self.toast = (msg, time.time() + secs)

    # ---- asking first: one touch or one button must never restart the services or end a lesson
    CONFIRM_SECS = 12

    def ask(self, title, detail, action):
        """Shows a question with Yes and Cancel; action runs only on Yes (✓ or a touch on Yes)."""
        self.confirm = {"title": title, "detail": detail, "action": action,
                        "until": time.time() + self.CONFIRM_SECS}

    def answer_confirm(self, yes):
        ask, self.confirm = getattr(self, "confirm", None), None
        if ask and yes:
            ask["action"]()
        elif ask:
            self.say("Canceled", 1.2)

    def confirm_rects(self):
        box = self.pg.Rect(110, 120, 580, 220)
        return box, self.pg.Rect(box.left + 30, box.bottom - 78, 245, 54), \
            self.pg.Rect(box.right - 275, box.bottom - 78, 245, 54)

    def draw_confirm(self):
        ask = getattr(self, "confirm", None)
        if not ask:
            return
        if time.time() > ask["until"]:
            self.confirm = None              # nobody answered: nothing happens
            return
        shade = self.pg.Surface((W, H), self.pg.SRCALPHA)
        shade.fill((*NIGHT, 215))
        self.surf.blit(shade, (0, 0))
        box, yes, no = self.confirm_rects()
        self.pg.draw.rect(self.surf, PANEL, box, border_radius=16)
        self.pg.draw.rect(self.surf, GOLD, box, 2, border_radius=16)
        self.text(ask["title"], 22, INK, (box.centerx, box.top + 40), True, "center")
        self.text(ask["detail"], 15, DIM, (box.centerx, box.top + 80), False, "center")
        self.pg.draw.rect(self.surf, GOLD, yes, border_radius=10)
        self.text("✓ Yes", 18, NIGHT, yes.center, True, "center")
        self.pg.draw.rect(self.surf, TRACK, no, border_radius=10)
        self.text("Cancel", 18, INK, no.center, True, "center")
        left = max(0, int(ask["until"] - time.time()))
        self.text(f"✓ or a touch on Yes. Any other button cancels ({left})", 13, DIM,
                  (box.centerx, box.bottom - 12), False, "midbottom")

    def leave_lesson(self):
        self.ask("Leave the lesson?", "What was learned so far stays saved",
                 lambda: (self.engine.post("/lessonstop"), self.say("Leaving the lesson")))

    # ---- input
    def handle(self, topic, payload):
        if topic == "screen/lesson-start-result":
            # The network worker posts results back to the main pygame thread.
            if not self.lesson_request_pending:
                return
            if payload == "ok":
                self.lesson_request_accepted = True
                self.say(f"Starting lesson: {self.lesson_request_song.replace('_', ' ')}", 5)
            else:
                self.lesson_request_pending = False
                self.lesson_request_accepted = False
                messages = {
                    "http:400": "Song could not be loaded. Check the song file.",
                    "http:401": "Screen authorization failed. Run: sudo armonico code",
                    "http:409": "A lesson is already running. Hold Menu to leave the lesson first.",
                    "http:503": "Lesson engine is unavailable. Check armonico logs.",
                    "connection": "Cannot reach the lesson engine. Check armonico logs.",
                }
                self.say(messages.get(payload, f"Lesson request failed ({payload}). Check armonico logs."), 6)
        elif topic == "piano/device/octave":
            try:
                self.octave = int(payload)
            except ValueError:
                pass
        elif topic == "piano/device/pedal":
            self.pedal = payload == "on"
        elif topic == "piano/alarm/status":
            self.alarm = payload == "on"
        elif topic == "piano/play/status":
            self.song = payload == "playing"
        elif topic == "piano/play/paused":
            self.paused_bridge = payload == "on"
            hint = self.paused_hint
            if hint is not None and time.monotonic() < hint[1] and self.paused_bridge != hint[0]:
                pass                    # the state from before the touch: the touch's answer stays for 3 s
            else:
                self.song_paused, self.paused_hint = self.paused_bridge, None
        elif topic == "piano/play/current":
            self.song_name = "" if payload == "unknown" else payload
        elif topic == "piano/device/key":
            if payload.isdigit():
                self.key_pressed = (int(payload), time.monotonic())     # which key: 0 and 12 sound the same C in a lesson
        elif topic == "piano/device/button":
            name, _, action = payload.partition(":")
            self.button(name, action == "hold")

    def move(self, step):
        if self.view == "test":
            return
        if self.view == "songs":
            n = len(self.songs())
            if n:
                self.sel_song = (self.sel_song + step) % n
                # Scroll only when the selection moves beyond the five visible rows.
                # Keeping song_first stable is important for touch: the row under a
                # finger must still be the same row on the next frame.
                first = max(0, min(getattr(self, "song_first", 0), max(0, n - 5)))
                if self.sel_song < first:
                    first = self.sel_song
                elif self.sel_song >= first + 5:
                    first = self.sel_song - 4
                self.song_first = max(0, min(first, max(0, n - 5)))
        elif self.view == "settings":
            self.sel_cell = (self.sel_cell + step) % len(self.CELLS)

    def button(self, name, hold):
        self.key_hold = False
        if getattr(self, "confirm", None):
            if not hold:                          # the hold of the press that asked is not an answer
                self.answer_confirm(name == "ok")
            return
        if self.lesson_on():
            if name == "menu" and hold:
                self.leave_lesson()               # ☰ held leaves the lesson, after a yes
            return
        if self.view == "test":
            if name == "done" or (name == "menu" and hold):
                self.go("settings")
            elif name in ("left", "right"):
                self.test_tone(name)
            elif not hold:
                self.test["buttons"][name] = self.test["buttons"].get(name, 0) + 1
            return
        if name in ("octdn", "octup"):
            step = 1 if name == "octup" else -1
            self.pub("piano/device/octave/set", f"{step:+d}")
            self.say(f"Octave {max(1, min(7, self.octave + step))}", 1.2)
        elif name == "menu":
            if hold:
                self.go("rest")
            else:
                self.go(self.VIEWS[(self.VIEWS.index(self.view) + 1) % len(self.VIEWS)])
        elif name in ("up", "down"):
            step = -1 if name == "up" else 1
            if self.view == "settings" and self.CELLS[self.sel_cell] == "volume":
                self.volume(-5 * step)
            elif self.view in ("free", "rest"):
                self.pub("piano/device/octave/set", f"{-step:+d}")
            else:
                self.move(step)
        elif name == "ok":
            if self.view == "songs":
                songs = self.songs()
                if songs:
                    song = songs[self.sel_song % len(songs)]["name"]
                    if hold:
                        self.pub("piano/play", f"{song}|Armonico Pi")
                        self.say(f"Playing {song}")
                    else:
                        self.start_lesson(song)
            elif self.view == "settings":
                self.activate(self.CELLS[self.sel_cell])
            elif self.view in ("rest", "free"):
                self.go("songs")

    def start_lesson(self, song):
        """Start exactly one lesson request and surface any HTTP/connection failure."""
        song = str(song).strip()
        if not song:
            self.say("Choose a song first")
            return
        if self.lesson_on():
            self.say("A lesson is already running. Hold Menu to leave the lesson first.", 5)
            return
        if self.lesson_request_pending:
            # Ignore another tap/button while the previous request is still starting.
            return

        if getattr(self, "song", False) and not getattr(self, "alarm", False):
            self.pub("piano/stop", "Armonico Pi: a lesson")   # no song goes on under a lesson
        self.lesson_request_song = song
        self.lesson_request_at = time.monotonic()
        self.lesson_request_pending = True
        self.lesson_request_accepted = False
        self.say(f"Starting lesson: {song.replace('_', ' ')}", 5)

        def result(ok, code):
            value = "ok" if ok else (f"http:{code}" if isinstance(code, int) else str(code))
            with self.ev_lock:
                self.events.append(("screen/lesson-start-result", value))

        self.engine.post("/lesson", {"song": song}, on_result=result)

    def go(self, view):
        self.view = view

    def activate(self, cell):
        if cell == "mode":
            want = "lab" if MQTT_LOCAL else "standalone"
            if not (MODES_DIR / f"{want}.env").exists():
                self.say("External broker not set up. Run: sudo armonico device-mode setup-external", 7)
                return
            label = "External broker" if want == "lab" else "Local broker"

            def switch():
                try:
                    MODE_REQUEST.write_text(want + "\n")
                    self.say(f"Switching to {label}; Armonico services will restart", 5)
                except OSError as e:
                    self.say(f"Not switched: {e}")
            self.ask(f"Switch to {label}?", "Every Armonico service restarts, a lesson or a song stops", switch)
        elif cell == "pedal":
            self.pub("piano/device/pedal/set", "off" if self.pedal else "on")
        elif cell == "ghost":
            on = not self.ghost_on()
            self.settings["ghost"] = on
            save_settings(ghost=on)
        elif cell == "rest":
            nxt = REST_MODES[(REST_MODES.index(self.rest_mode()) + 1) % len(REST_MODES)]
            self.settings["rest"] = nxt
            save_settings(rest=nxt)
            self.media.stop()                  # the new choice starts when the rest screen is next in view
        elif cell == "stop":
            now = load_settings().get("stop_button", "ok")      # the lesson screen may have changed it
            nxt = self.STOP_BUTTONS[(self.STOP_BUTTONS.index(now) + 1) % len(self.STOP_BUTTONS)] \
                if now in self.STOP_BUTTONS else "ok"
            self.settings["stop_button"] = nxt
            save_settings(stop_button=nxt)
        elif cell == "volume":
            self.volume(10)
        elif cell == "network":
            self.ip, self.ip_at = lan_ip(), time.time()
        elif cell == "test":
            self.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": ""}
            self.go("test")

    def volume(self, delta):
        vol = self.engine.state.get("volume")
        vol = 80 if vol is None else vol
        vol = max(0, min(100, vol + delta))
        self.engine.state["volume"] = vol
        self.engine.post("/volume", {"value": vol})
        self.media.set_volume(vol)

    def songs(self):
        return [s for s in self.engine.overview.get("songs", []) if s.get("total")]

    # ---- what the keys did, from the engine's list of presses
    def follow_keys(self):
        vol = self.engine.state.get("volume")
        if vol is not None and vol != self.seen_volume:      # from this screen or the web page
            self.seen_volume = vol
            self.media.set_volume(vol)
        live = self.engine.state.get("live") or []
        new = [n for i, n in live if i > self.last_live]
        if live:
            self.last_live = max(i for i, _ in live)
        sent = getattr(self, "touch_sent", None)
        for n in new:
            self.key_hold = True
            # a key touched on the screen already lit and counted itself (free_key): the engine's copy of
            # the same press, within 2 s, is taken off here. It was shown as a second note (G4 G4).
            mine = next((x for x in (sent or []) if x[0] == n % 12 and time.time() - x[1] < 2), None)
            if mine is not None:
                sent.remove(mine)
            else:
                self.flash[self.flash_key(n)] = time.time()
            self.trail = (self.trail + ["C C# D D# E F F# G G# A A# B".split()[n % 12]])[-8:]
            self.hop = time.time()
            self.notes.append([330 + random.random() * 140, 260.0, random.uniform(-2, 2),
                               random.choice((GOLD, SKY, MINT, ROSE)), time.time()])

    def follow_music(self):
        """The idle music plays on the rest screen and nowhere else, and only while nothing else has the device."""
        busy = bool(self.lesson_on() or self.alarm or self.song)
        if self.was_busy and not busy:
            self.key_hold = False              # a lesson, alarm or song ended: the idle music comes back by itself
        self.was_busy = busy
        want = self.view == "rest" and not (busy or self.key_hold)
        if not want and self.media.playing is not None:
            self.media.stop()
            self.media_try = 0.0
        elif want and self.media.playing is None and time.time() - self.media_try > 2:
            self.media_try = time.time()       # a mode with no file is asked again every 2 seconds, not every frame
            self.media.start(self.rest_mode())

    def ghost_on(self):
        return bool(self.settings.get("ghost", True))

    def follow_ghost(self):
        """After a full run that had a ghost: how far ahead of the last run, or behind it."""
        res = self.engine.state.get("ghost_result")
        rid = res.get("id") if isinstance(res, dict) else None
        if self.ghost_id is self.UNSEEN:
            if self.engine.ok:
                self.ghost_id = rid
            return
        if rid is None or rid == self.ghost_id:
            return
        self.ghost_id = rid
        if not self.ghost_on():
            return
        ahead = int(res.get("ahead", 0))
        keys = "key" if abs(ahead) == 1 else "keys"
        self.say("Ghost run: level with your last run" if ahead == 0 else
                 f"Ghost run: {ahead} {keys} ahead of your last run" if ahead > 0 else
                 f"Ghost run: {-ahead} {keys} behind your last run", 5)

    def lesson_expect(self):
        """The note the lesson waits for (the first of the part while it is played to you), or None."""
        st = self.engine.state
        midi = st.get("midi") or []
        at = st.get("expect", -1)
        if (at is None or at < 0) and st.get("mode") == "demo":
            at = 0                       # a key played along with the demo starts the answer
        if not midi or at is None or not 0 <= at < len(midi) or not isinstance(midi[at], int):
            return None
        return midi[at]

    def follow_lesson(self):
        """In a lesson the keys stay where they are: key 1 is always C, and the numbers on them
        never jump. The octave is set once for each part, to its lowest note, and the note the lesson
        waits for goes to the keys' program, which sounds its key at the song's own height
        (piano/device/expect): the C above is played on the same key as the C below."""
        midi = [n for n in (self.engine.state.get("midi") or []) if isinstance(n, int)]
        if midi:
            want = max(1, min(7, min(midi) // 12 - 1))
            if want != self.octave:
                self.pub("piano/device/octave/set", str(want))
                self.octave = want
        want = self.lesson_expect()
        self.send_expect(-1 if want is None else want)   # -1: in the lesson, no note yet (demo, count-in)

    def send_expect(self, note):
        """Sent when it changes, and again every 3 s in a lesson, so a keys' program that restarted
        in the middle of one hears it too."""
        now = time.monotonic()
        if note != getattr(self, "sent_expect", None) or (note is not None and now - getattr(self, "sent_at", 0) > 3):
            self.sent_expect, self.sent_at = note, now
            self.pub("piano/device/expect", "" if note is None else str(note))

    # ---- drawing
    def keyboard(self, top, next_pc=None, finger="", ghost=None, *, key_labels=None,
                 key_colours=None, label_mode="abc", colors_mode="notes", stage="free"):
        """Draw the same label system and hint stage as the browser lesson keyboard.

        stage='numbers' shows the profile's labels and note colors; stage='keys' shows
        only the next key in gold; stage='memory' shows no answer, label or note color.
        Free play deliberately keeps its simple C-D-E names.
        """
        pitch, kw, kh, bw, bh = 84, 80, 176, 50, 106
        whites = [0, 2, 4, 5, 7, 9, 11, 12]          # 12: the high C, on the right
        x0 = (W - len(whites) * pitch) // 2
        black_positions = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}
        now = time.time()
        has_labels = lesson_labels_visible(stage, label_mode)
        lit_next = stage in ("numbers", "keys") and next_pc is not None

        def key_colour(pc):
            pc %= 12
            if key_colours and 0 <= pc < len(key_colours):
                value = key_colours[pc]
                if isinstance(value, (list, tuple)) and len(value) >= 3:
                    return tuple(max(0, min(255, int(v))) for v in value[:3])
            return SKY

        def active_colour(pc):
            pc %= 12
            if stage == "keys" or colors_mode == "simple":
                return GOLD
            return key_colour(pc)

        for i, key in enumerate(whites):
            pc = key % 12
            r = self.pg.Rect(x0 + i * pitch + 2, top, kw, kh)
            is_target = lit_next and key == next_pc
            is_flash = now - self.flash.get(key, 0) < 0.18
            base = MINT if is_flash else active_colour(pc) if is_target else WHITE_KEY
            self.pg.draw.rect(self.surf, base, r, border_bottom_left_radius=12, border_bottom_right_radius=12)

            if key_labels is None:
                # The free-play screen is not a lesson and retains its simple note names.
                note_label = "CDEFGABC"[i]
                self.text(note_label, 19, (59, 42, 5) if is_target else KEY_TEXT,
                          (r.centerx, r.bottom - 12), True, "midbottom")
            elif has_labels:
                note_label = "8" if key == 12 and label_mode == "num" else key_labels[pc] if pc < len(key_labels) else ""
                if note_label:
                    badge = (255, 255, 255) if is_target else key_colour(pc)
                    self.pg.draw.circle(self.surf, badge, (r.centerx, r.bottom - 32), 18)
                    # The lesson page uses dark text on its yellow/orange stickers.
                    text_colour = (29, 29, 29) if is_target or (colors_mode == "notes" and pc in (2, 3, 4)) else (255, 255, 255)
                    size = 12 if len(note_label) >= 4 else 15 if len(note_label) >= 3 else 18
                    self.text(note_label, size, text_colour, (r.centerx, r.bottom - 32), True, "center")

        for i, pc in enumerate((1, 3, 6, 8, 10)):
            r = self.pg.Rect(x0 + (i + 1) * pitch - bw // 2, top, bw, bh)
            is_target = lit_next and pc == next_pc
            is_flash = now - self.flash.get(pc, 0) < 0.18
            base = MINT if is_flash else active_colour(pc) if is_target else BLACK_KEY
            self.pg.draw.rect(self.surf, base, r, border_bottom_left_radius=9, border_bottom_right_radius=9)

        if ghost is not None and 0 <= ghost < 13:
            # the previous run's place: a ring on the key, quiet enough not to be taken for the answer
            if ghost in whites:
                cx, cy = x0 + whites.index(ghost) * pitch + 2 + kw // 2, top + kh - 62   # above the labels
            else:
                cx, cy = x0 + (black_positions[ghost] + 1) * pitch, top + bh - 22
            self.pg.draw.circle(self.surf, ROSE, (cx, cy), 15, 3)

        if lit_next and finger:
            col = whites.index(next_pc) if next_pc in whites else None
            cx = x0 + col * pitch + pitch // 2 if col is not None else \
                x0 + (black_positions[next_pc] + 1) * pitch
            self.pg.draw.circle(self.surf, GOLD, (cx, top - 27), 17)
            self.text(finger, 20, (59, 42, 5), (cx, top - 27), True, "center")

    def top_bar(self, title, right):
        self.pg.draw.line(self.surf, LINE, (0, 52), (W, 52))
        self.text(title, 21, INK, (22, 26), True, "midleft")
        self.text(right, 16, DIM, (W - 22, 26), False, "midright")

    def mascot(self, cx, bottom):
        t = time.time()
        s = self.pg.Surface((170, 150), self.pg.SRCALPHA)
        self.pg.draw.lines(s, GOLD, False, [(12, 70), (85, 12), (158, 70)], 6)
        self.pg.draw.lines(s, SKY, False, [(28, 76), (85, 31), (142, 76)], 10)
        self.pg.draw.rect(s, WHITE_KEY, (34, 78, 102, 66), border_radius=12)
        self.pg.draw.rect(s, BLACK_KEY, (58, 78, 12, 36), border_radius=4)
        self.pg.draw.rect(s, BLACK_KEY, (100, 78, 12, 36), border_radius=4)
        blink = 0.1 if (t % 5) > 4.7 else 1.0
        for ex in (66, 104):
            h = max(2, int(14 * blink))
            self.pg.draw.ellipse(s, BLACK_KEY, (ex - 6, 124 - h // 2, 12, h))
        self.pg.draw.arc(s, BLACK_KEY, (78, 128, 14, 10), math.pi, 2 * math.pi, 3)
        for chx in (52, 118):
            self.pg.draw.circle(s, (*ROSE, 140), (chx, 132), 5)
        # breathing, and a hop on every key
        sx, sy = 1 + 0.03 * math.sin(t * 1.4), 1 - 0.02 * math.sin(t * 1.4)
        dy, hop = 0, t - self.hop
        if hop < 0.45:
            dy = -16 * math.sin(hop / 0.45 * math.pi)
        img = self.pg.transform.smoothscale(s, (int(170 * sx), int(150 * sy)))
        self.surf.blit(img, img.get_rect(midbottom=(cx, bottom + dy)))

    def draw_notes(self):
        now = time.time()
        keep = []
        for n in self.notes:
            x, y, dx, color, born = n
            age = now - born
            if age > 3.2:
                continue
            keep.append(n)
            a = max(0, 255 - int(age / 3.2 * 255))
            px, py = int(x + dx * age * 18), int(y - age * 80)
            s = self.pg.Surface((24, 30), self.pg.SRCALPHA)
            self.pg.draw.ellipse(s, (*color, a), (2, 18, 12, 9))
            self.pg.draw.line(s, (*color, a), (13, 22), (13, 3), 3)
            self.pg.draw.line(s, (*color, a), (13, 3), (21, 8), 3)
            self.surf.blit(s, (px, py))
        self.notes = keep

    def draw_rest(self):
        frame = self.media.surface()
        if frame is not None:
            self.surf.blit(frame, (0, 0))
            self.text(time.strftime("%H:%M"), 26, INK, (20, 14), True)
            if time.time() >= self.bar_until:
                self.text("tap for the buttons", 13, INK, (W - 14, 12), False, "topright")
            return
        self.surf.fill(NIGHT)
        glow = self.pg.Surface((W, H), self.pg.SRCALPHA)
        self.pg.draw.ellipse(glow, (34, 49, 79, 120), (-200, int(H * 0.55), W + 400, H))
        self.surf.blit(glow, (0, 0))
        t = time.time()
        if t - getattr(self, "_last_auto", 0) > 0.9:
            self._last_auto = t
            self.notes.append([60 + random.random() * 680, 470.0, 0, (44, 61, 97), t])
        for x, y, ph in self.stars:
            v = int(150 + 80 * math.sin(t * 1.6 + ph))
            self.surf.fill((v, v, min(255, v + 20)), (x, y, 2, 2))
        self.text(time.strftime("%H:%M"), 46, INK, (28, 16), True)
        self.text(time.strftime("%A · %d %b"), 16, DIM, (30, 74))
        stats = self.engine.overview.get("stats") or {}
        streak = stats.get("streak", 0)
        self.text(f"{streak} day{'s' if streak != 1 else ''}", 22, GOLD, (W - 26, 24), True, "topright")
        self.text(f"in a row · {stats.get('week_minutes', 0)} min this week", 14, DIM, (W - 26, 54), False, "topright")
        self.draw_notes()
        self.mascot(W // 2, H - 150)
        if self.media.playing is not None:
            line = self.media.lyric()
            if line:
                self.text(line, 21, INK, (W // 2, H - BAR_H - 34), False, "midbottom")
            self.text("♪ " + self.media.title(), 13, DIM, (W // 2, H - BAR_H - 12), False, "midbottom")
        elif self.media.status:
            self.text(self.media.status, 13, DIM, (W // 2, H - BAR_H - 12), False, "midbottom")

    def draw_lesson(self):
        st = self.engine.state
        self.surf.fill(NIGHT)
        pos, total = st.get("pos", 0) or 0, st.get("total", 0) or 0
        self.top_bar((st.get("song") or "Lesson").replace("_", " ").capitalize(), st.get("title", "")[-48:])
        for i in range(min(total, 16)):
            color = MINT if i < pos else GOLD if i == pos else TRACK
            self.pg.draw.rect(self.surf, color, (24 + i * 36, 66, 30, 8), border_radius=4)
        midi = st.get("midi") or []
        at = st.get("expect", -1)
        if at is None or at < 0:
            at = st.get("demo", -1)
        if at is None:
            at = -1
        show = st.get("show", True)
        stage = lesson_display_stage(show)
        label_mode = st.get("device_labels", "num")
        colors_mode = st.get("device_colors", "notes")
        key_labels = st.get("device_key_labels") or None
        key_colours = st.get("device_key_colours") or None
        next_pc, finger, target_label = None, "", ""
        if 0 <= at < len(midi) and stage in ("numbers", "keys"):
            next_pc = self.lit_key(midi[at])
            if stage == "numbers":
                fing = st.get("fing") or []
                m = re.search(r"\d", fing[at]) if at < len(fing) and fing[at] else None
                finger = m.group(0) if m else ""
                if key_labels and next_pc % 12 < len(key_labels):
                    target_label = "8" if next_pc == 12 and label_mode == "num" else key_labels[next_pc % 12]
        self.text("octave", 16, DIM, (W - 24, 70), False, "topright")
        self.text(str(self.octave), 32, SKY, (W - 24, 88), True, "topright")
        msg, until = self.shift_msg
        if time.time() < until:
            self.text(f"▶ {msg}", 15, GOLD, (W - 24, 143), True, "topright")
        elif 0 <= at < len(midi) - 1 and midi[at + 1] // 12 - 1 != midi[at] // 12 - 1:
            self.text(f"next note ▶ octave {midi[at + 1] // 12 - 1}", 14, GOLD, (W - 24, 143), True, "topright")
        if st.get("msg"):
            self.text(st["msg"], 18, INK, (24, 96))
        # Same four label modes as the browser lesson toolbar, but never PC-key mode:
        # the Pi has piano keys, not a desktop typing keyboard.
        self.draw_label_mode_buttons(label_mode)
        # Black piano keys do not carry normal text in the browser keyboard, so show
        # the target label separately. Desktop-computer key mappings are not used here.
        if (stage == "numbers" and next_pc in (1, 3, 6, 8, 10)
                and label_mode in ("num", "abc", "do") and target_label):
            self.text(f"Next: {target_label}", 17, GOLD, (24, 151), True)
        g = st.get("ghost")
        ghost_pc = self.lit_key(midi[g]) if self.ghost_on() and isinstance(g, int) and 0 <= g < len(midi) else None
        if ghost_pc is not None:
            self.text("ring: your last run", 14, ROSE, (24, 175))
        self.keyboard(196, next_pc, finger, ghost_pc, key_labels=key_labels, key_colours=key_colours,
                      label_mode=label_mode, colors_mode=colors_mode, stage=stage)
        self.pg.draw.rect(self.surf, TRACK, (24, H - BAR_H - 22, W - 48, 8), border_radius=4)
        if total:
            self.pg.draw.rect(self.surf, SKY, (24, H - BAR_H - 22, int((W - 48) * pos / total), 8), border_radius=4)

    def draw_summary(self, s):
        self.surf.fill(NIGHT)
        card = self.pg.Rect(140, 120, 520, 240)
        self.pg.draw.rect(self.surf, PANEL, card, border_radius=16)
        self.text(s.get("song", "").replace("_", " ").capitalize(), 24, INK, (card.centerx, card.top + 40), True, "center")
        self.text(f"{s.get('learned', 0)} of {s.get('total', 0)} parts learned", 18, DIM, (card.centerx, card.top + 90), False, "center")
        if s.get("score") is not None:
            self.text(f"{s['score']}%", 48, GOLD, (card.centerx, card.top + 150), True, "center")
        w = s.get("weakest")
        if isinstance(w, dict) and w.get("skill") in SKILL_NAMES:
            # what piano_learner.py sees as the player's weakest skill now
            self.text(f"Weakest now: {SKILL_NAMES[w['skill']]} {round(100 * w.get('level', 0))}%", 15, SKY,
                      (card.centerx, card.top + 196), True, "center")
        self.text("✓, any button or a touch to go on", 14, DIM, (card.centerx, card.bottom - 24), False, "center")

    def draw_songs(self):
        self.surf.fill(NIGHT)
        self.top_bar("Songs", "▲▼ or touch · ✓ lesson · hold ✓ to play")
        songs = self.songs()
        if not songs:
            if not self.engine.ok:
                self.text("The lesson engine is not answering", 18, DIM, (24, 80))
                self.text(self.engine.why[:70], 14, DIM, (24, 112))
            elif self.engine.over_why:
                self.text("The songs did not load", 18, DIM, (24, 80))
                self.text(self.engine.over_why[:70], 14, DIM, (24, 112))
                if "401" in self.engine.over_why:
                    self.text("The screen code is missing: sudo armonico code", 14, GOLD, (24, 140))
            elif self.engine.overview.get("songs"):
                self.text(f"{len(self.engine.overview['songs'])} songs found, none could be read as a lesson", 18, DIM, (24, 80))
                why = next((x.get("problem") for x in self.engine.overview["songs"] if x.get("problem")), "")
                self.text((why[:70] if why else "see: armonico logs"), 14, DIM, (24, 112))
                self.text("all of them: armonico status", 14, DIM, (24, 136))
            else:
                self.text("No songs yet", 18, DIM, (24, 80))
            return
        for i, r in self.song_rows():
            s = songs[i]
            if i == self.sel_song:
                self.pg.draw.rect(self.surf, PANEL, r, border_radius=10)
                self.pg.draw.rect(self.surf, GOLD, r, 2, border_radius=10)
            self.text(s["name"].replace("_", " ").capitalize(), 22, INK, (r.left + 20, r.centery), False, "midleft")
            learned, total = s.get("learned", 0), s.get("total", 0)
            done = bool(total) and learned >= total
            note = "learned" if done else "new" if not learned else f"{learned} of {total}"
            self.text(note, 16, MINT if done else GOLD if learned else DIM, (r.right - 20, r.centery), False, "midright")
            track = self.pg.Rect(r.right - 270, r.centery - 4, 150, 8)      # one place on every row, so the rows line up
            self.pg.draw.rect(self.surf, TRACK, track, border_radius=4)
            if learned:
                fill = track.copy()
                fill.width = max(8, int(track.width * min(1, learned / max(1, total))))
                self.pg.draw.rect(self.surf, MINT if done else GOLD, fill, border_radius=4)
        bad = [x for x in self.engine.overview.get("songs", []) if not x.get("total")]
        if bad:
            first = f'{bad[0]["name"].replace("_", " ")}: {bad[0].get("problem") or "cannot be read"}'
            more = f" (+{len(bad) - 1} more, armonico status)" if len(bad) > 1 else ""
            self.text(f"Not a lesson: {first}"[:80] + more, 13, DIM, (24, 398))

    def draw_free(self):
        self.surf.fill(NIGHT)
        self.top_bar("Free play", f"grand piano · pedal {'on' if self.pedal else 'off'}")
        self.text("octave", 16, DIM, (W - 24, 70), False, "topright")
        self.text(str(self.octave), 32, SKY, (W - 24, 88), True, "topright")
        self.text("▲▼ change octave", 13, DIM, (W - 24, 128), False, "topright")
        self.text("  ".join(self.trail), 22, SKY, (24, 76), True)
        self.keyboard(200)

    def draw_settings(self):
        self.surf.fill(NIGHT)
        self.top_bar("Status", "Armonico " + (VERSION.read_text().strip() if VERSION.exists() else ""))
        if time.time() - self.ip_at > 30:
            self.ip, self.ip_at = lan_ip(), time.time()
        prof = next((p.get("name") for p in self.engine.overview.get("profiles", []) if p.get("active")), "") \
            if isinstance(self.engine.overview.get("profiles"), list) else ""
        vol = self.engine.state.get("volume")
        cells = {
            "mode": ("Connection", "Local broker" if MQTT_LOCAL else "External broker",
                     ("✓ switch to " + ("External broker" if MQTT_LOCAL else "Local broker"))
                     if (MODES_DIR / ("lab.env" if MQTT_LOCAL else "standalone.env")).exists()
                     else "Set up once: armonico device-mode setup-external"),
            "network": ("Network", self.ip, "PoE · wired" if self.ip != "no network" else ""),
            "volume": ("Volume", None, "▲▼ or ✓"),
            "pedal": ("Sustain pedal", "On" if self.pedal else "Off", "✓ switches"),
            "ghost": ("Ghost run", "On" if self.ghost_on() else "Off", "race your last full run in a lesson"),
            "rest": ("Idle music", REST_NAMES[self.rest_mode()], self.media.status or "✓ next choice"),
            "stop": ("Stop button", self.BUTTON_NAMES.get(load_settings().get("stop_button", "ok"), "✓ button"),
                     "stops the alarm and songs only while they play"),
            "profile": ("Profile", prof or "Main", ""),
            "test": ("Hardware test", "Keys, buttons, touch, speakers", "✓ opens it"),
        }
        for i, key in enumerate(self.CELLS):
            r = self.cell_rect(i)
            self.pg.draw.rect(self.surf, PANEL, r, border_radius=12)
            if i == self.sel_cell:
                self.pg.draw.rect(self.surf, GOLD, r, 2, border_radius=12)
            title, value, hint = cells[key]
            self.text(title, 14, DIM, (r.left + 16, r.top + 6))
            if value is None:
                bar = self.pg.Rect(r.left + 16, r.top + 38, r.width - 32, 8)
                self.pg.draw.rect(self.surf, TRACK, bar, border_radius=4)
                self.pg.draw.rect(self.surf, SKY, (bar.left, bar.top, int(bar.width * (80 if vol is None else vol) / 100), 8), border_radius=4)
            else:
                self.text(value, 20, INK, (r.left + 16, r.top + 24), True)
            if hint:
                self.text(hint, 13, DIM, (r.left + 16, r.bottom - 6), False, "bottomleft")

    # ---- the hardware test
    def test_tone(self, side, quiet=False):
        """A short tone on one speaker only, so a swapped or silent side shows at once.
        quiet: the screens of device/ui say it themselves, in the screen's language."""
        mixer = self.pg.mixer
        if not mixer.get_init():
            if not quiet:
                self.say("No sound card: check armonico status", 4)
            return
        rate, chans = mixer.get_init()[0], mixer.get_init()[2]
        frames = int(rate * 0.7)
        data = bytearray()
        for i in range(frames):
            # in over 10 ms and out over 100: started at full level, every tone began with a click
            v = int(12000 * math.sin(2 * math.pi * 523.25 * i / rate) * min(1, i / (rate * 0.01), (frames - i) / (rate * 0.1)))
            b = v.to_bytes(2, "little", signed=True)
            z = b"\0\0"
            data += (b + z if side == "left" else z + b) if chans >= 2 else b
        with getattr(self, "mixer_lock", _NOLOCK):
            if not mixer.get_init():
                return
            try:
                mixer.Sound(buffer=bytes(data)).play()
            except self.pg.error:
                return
        self.test["speaker"] = side
        if not quiet:
            self.say(f"{'Left' if side == 'left' else 'Right'} speaker: a tone from that side only", 2)

    def flash_key(self, note):
        """The key to flash for a note heard: the one just pressed when it is a C key (0 or 12), since a
        lesson sounds both at the song's own height; else as lit_key."""
        k, at = getattr(self, "key_pressed", (None, 0))
        if note % 12 == 0 and k in (0, 12) and time.monotonic() - at < 1.0:
            return k
        return self.lit_key(note)

    def lit_key(self, note):
        """The key that lights for a note in a lesson: key 12 (the high C) when the note is the C one
        octave above the keys' first C, else its pitch class (the keys stay in one octave)."""
        if note % 12 == 0 and note == 12 * (self.octave + 1) + 12:
            return 12
        return note % 12

    def draw_test(self):
        self.surf.fill(NIGHT)
        t = self.test
        self.top_bar("Hardware test", "hold ☰ or touch Done to leave")
        now = time.time()
        # the keys, in the order of the octave (the 13th is the high C), each with its GPIO
        n_keys = key_total()
        pitch = 58
        for pc in range(n_keys):
            r = self.pg.Rect(24 + pc * pitch, 62, pitch - 5, 62)
            live = now - max(self.flash.get(pc, 0), t.get("at", {}).get(pc, 0)) < 0.25
            done = pc in t["keys"]
            self.pg.draw.rect(self.surf, MINT if live else GOLD if done else PANEL, r, border_radius=8)
            col = NIGHT if live or done else INK
            self.text(NOTE_NAMES[pc % 12], 17, col, (r.centerx, r.top + (31 if pc >= 12 else 18)), True, "center")
            if pc >= 12:                          # the high C: a small word over its name
                self.text("high", 9, col, (r.centerx, r.top + 10), False, "center")
            self.text(f"GPIO {KEY_PINS[pc] if pc < len(KEY_PINS) else '?'}", 11, col, (r.centerx, r.bottom - 14), False, "center")
        # the 4 buttons, across the row
        names = (("menu", "☰"), ("down", "▼"), ("up", "▲"), ("ok", "✓"))
        for i, (name, sym) in enumerate(names):
            r = self.pg.Rect(24 + i * 190, 134, 182, 62)
            n = t["buttons"].get(name, 0)
            self.pg.draw.rect(self.surf, GOLD if n else PANEL, r, border_radius=8)
            col = NIGHT if n else INK
            img = self.symbol_font(20).render(sym, True, col)
            self.surf.blit(img, img.get_rect(center=(r.centerx, r.top + 20)))
            self.text(f"GPIO {BUTTON_PINS.get(name, '?')} · {n}", 12, col, (r.centerx, r.bottom - 14), False, "center")
        # the touch area: every touch leaves a dot where the screen felt it
        area = self.pg.Rect(24, 206, 420, 200)
        self.pg.draw.rect(self.surf, PANEL, area, border_radius=10)
        self.text(f"Touch here and in the corners · {len(t['taps'])}", 14, DIM, (area.left + 12, area.top + 8))
        for x, y in t["taps"]:
            self.pg.draw.circle(self.surf, ROSE, (x, y), 9, 3)
        # what cannot light up by itself
        info = self.pg.Rect(456, 206, 320, 200)
        self.pg.draw.rect(self.surf, PANEL, info, border_radius=10)
        keys_ok, btn_ok = len(t["keys"]) == key_total(), len(t["buttons"]) >= 4
        lines = [
            (f"Keys {len(t['keys'])} of {key_total()}", MINT if keys_ok else INK),
            (f"Buttons {len(t['buttons'])} of 4", MINT if btn_ok else INK),
            (f"Speakers: {t['speaker'] + ' played' if t['speaker'] else 'the two buttons below'}", INK),
            ("Network: " + (self.ip or "no network"), INK),
            ("Power button (GPIO3): a press", DIM),
            ("shuts the Pi down. Test it last", DIM),
            ("The gold frame must show on all 4 edges", DIM),
        ]
        for i, (line, colr) in enumerate(lines):
            self.text(line, 14, colr, (info.left + 12, info.top + 10 + i * 26))
        self.pg.draw.rect(self.surf, GOLD, (0, 0, W, H), 2)      # the whole picture is on the panel

    # ---- the touch bar and touches
    def song_rows(self):
        """(index, rect) for a stable five-row viewport above the touch bar."""
        songs = self.songs()
        if not songs:
            self.song_first = 0
            return []
        n = len(songs)
        first = max(0, min(getattr(self, "song_first", 0), max(0, n - 5)))
        selected = int(getattr(self, "sel_song", 0)) % n
        # Keep a programmatically changed selection visible without recentering it
        # when a user merely taps a row that's already on screen.
        if selected < first:
            first = selected
        elif selected >= first + 5:
            first = selected - 4
        first = max(0, min(first, max(0, n - 5)))
        self.song_first = first
        return [(first + row, self.pg.Rect(24, 64 + row * 66, W - 48, 58))
                for row in range(min(5, n - first))]

    def label_mode_buttons(self):
        """Four on-device label choices, placed like the browser lesson toolbar."""
        width, gap, x0, y, height = 104, 8, 218, 96, 36
        return [(mode, label, self.pg.Rect(x0 + i * (width + gap), y, width, height))
                for i, (mode, label) in enumerate(self.LABEL_MODES)]

    def draw_label_mode_buttons(self, current_mode):
        for mode, label, rect in self.label_mode_buttons():
            active = mode == current_mode
            self.pg.draw.rect(self.surf, PANEL, rect, border_radius=9)
            self.pg.draw.rect(self.surf, GOLD if active else LINE, rect, 2 if active else 1,
                              border_radius=9)
            self.text(label, 16 if mode == "do" else 17, GOLD if active else INK,
                      rect.center, active, "center")

    def cell_rect(self, i):
        rows = (len(self.CELLS) + 1) // 2
        pitch = min(88, (H - BAR_H - 62 - 6) // rows)      # every row above the touch bar
        return self.pg.Rect(24 + (i % 2) * 381, 62 + (i // 2) * pitch, 371, pitch - 6)

    def bar_buttons(self):
        """[(name, symbol, label)] of the bar for this screen, or [] when it is hidden."""
        if getattr(self, "confirm", None):
            return []
        if self.lesson_on():
            return [("stop", "■", "Leave the lesson")]
        if time.time() < self.summary_until:
            return []
        if self.view == "rest" and self.media.surface() is not None and time.time() >= self.bar_until:
            return []                    # over the video only after a touch, so the subtitles stay clear
        if self.view == "test":
            return [("left", "◀", "Left speaker"), ("right", "▶", "Right speaker"), ("done", "■", "Done")]
        ok = {"rest": "Songs", "free": "Songs", "songs": "Lesson", "settings": "Change"}[self.view]
        if self.view in ("rest", "free"):
            return [("menu", "☰", "Menu"), ("down", "▼", "Octave"), ("up", "▲", "Octave"), ("ok", "✓", ok)]
        return [("menu", "☰", "Menu"), ("down", "▼", "Down"), ("up", "▲", "Up"),
                ("octdn", "−", "Octave"), ("octup", "+", "Octave"), ("ok", "✓", ok)]

    def bar_rects(self):
        items = self.bar_buttons()
        if not items:
            return []
        gap, n = 8, len(items)
        w = (W - 20 - gap * (n - 1)) // n
        return [(items[i], self.pg.Rect(10 + i * (w + gap), H - BAR_H + 6, w, BAR_H - 14)) for i in range(n)]

    def draw_bar(self):
        rects = self.bar_rects()
        if not rects:
            return
        back = self.pg.Surface((W, BAR_H), self.pg.SRCALPHA)
        back.fill((*NIGHT, 235))
        self.surf.blit(back, (0, H - BAR_H))
        self.pg.draw.line(self.surf, LINE, (0, H - BAR_H), (W, H - BAR_H))
        for (name, sym, label), r in rects:
            self.pg.draw.rect(self.surf, PANEL, r, border_radius=10)
            sym_img = self.symbol_font(18).render(sym, True, GOLD)
            txt = self.font(16).render(label, True, INK)
            left = r.centerx - (sym_img.get_width() + 10 + txt.get_width()) // 2
            self.surf.blit(sym_img, sym_img.get_rect(midleft=(left, r.centery)))
            self.surf.blit(txt, txt.get_rect(midleft=(left + sym_img.get_width() + 10, r.centery)))

    def tap(self, x, y):
        # Some touch panels emit both FINGERDOWN and a synthetic mouse click for the
        # same physical tap. Suppress only the matching nearby event; other taps work.
        now = time.monotonic()
        if self.last_tap_pos is not None and now - self.last_tap_at < 0.30:
            if abs(x - self.last_tap_pos[0]) <= 18 and abs(y - self.last_tap_pos[1]) <= 18:
                return
        self.last_tap_at, self.last_tap_pos = now, (x, y)
        self.key_hold = False
        if getattr(self, "confirm", None):
            _, yes, no = self.confirm_rects()
            if yes.collidepoint(x, y):
                self.answer_confirm(True)
            elif no.collidepoint(x, y):
                self.answer_confirm(False)
            return                                # a touch beside the question does nothing
        if time.time() < self.summary_until:
            self.summary_until = 0
            return
        for (name, _, _), r in self.bar_rects():
            if r.collidepoint(x, y):
                if name == "stop":
                    self.leave_lesson()
                else:
                    self.button(name, False)
                if self.view == "rest":
                    self.bar_until = time.time() + BAR_SHOW
                return
        if self.lesson_on():
            for mode, label, r in self.label_mode_buttons():
                if r.collidepoint(x, y):
                    # The web lesson already owns this setting per active profile;
                    # saving through the same endpoint keeps both screens in sync.
                    self.engine.post("/settings", {"labels": mode})
                    self.say(f"Key labels: {label}", 1.4)
                    return
            return
        if self.view == "test":
            self.test["taps"] = (self.test["taps"] + [(x, y)])[-12:]
            return
        if self.view == "rest":
            self.bar_until = time.time() + BAR_SHOW      # a touch on the video brings the buttons up
        elif self.view == "songs":
            for i, r in self.song_rows():
                if r.collidepoint(x, y):
                    songs = self.songs()
                    if 0 <= i < len(songs):
                        self.sel_song = i
                        self.start_lesson(songs[i]["name"])  # one tap both selects and starts
                    return
        elif self.view == "settings":
            for i in range(len(self.CELLS)):
                if self.cell_rect(i).collidepoint(x, y):
                    self.sel_cell = i
                    self.activate(self.CELLS[i])  # one touch selects and activates the setting
                    return

    def draw(self):
        st = self.engine.state
        s = st.get("summary")
        if s and s.get("id") != self.summary_id:
            self.summary_id, self.summary_until = s.get("id"), time.time() + 12
        if self.lesson_on():
            self.draw_lesson()
        elif time.time() < self.summary_until:
            self.draw_summary(s or {})
        else:
            {"rest": self.draw_rest, "songs": self.draw_songs, "free": self.draw_free,
             "settings": self.draw_settings, "test": self.draw_test}[self.view]()
        self.draw_bar()
        self.draw_confirm()
        msg, until = self.toast
        if time.time() < until:
            img = self.render(msg, 16, NIGHT, True)
            r = img.get_rect(midbottom=(W // 2, H - BAR_H - 16)).inflate(28, 14)
            self.pg.draw.rect(self.surf, GOLD, r, border_radius=10)
            self.surf.blit(img, img.get_rect(center=r.center))
        self.pg.display.flip()

    def follow_request(self):
        """A lesson asked for: said when it starts, or why it did not within 8 seconds."""
        if self.lesson_on() and self.lesson_request_pending:
            started_song = self.lesson_request_song.replace("_", " ")
            self.lesson_request_pending = False
            self.lesson_request_accepted = False
            self.say(f"Lesson started: {started_song}", 3)
        elif self.lesson_request_pending and time.monotonic() - self.lesson_request_at > 8:
            started_song = self.lesson_request_song.replace("_", " ")
            accepted = self.lesson_request_accepted
            self.lesson_request_pending = False
            self.lesson_request_accepted = False
            reason = "the engine accepted the request but did not enter lesson mode" if accepted else "no response from the lesson engine"
            self.say(f"Lesson did not start ({started_song}): {reason}. Check armonico logs.", 7)

    def restart_self(self):
        """The screen program again, from the start: the screens lit as they are now. In the device's
        session (device/session.sh) the program ends and the session starts again, because a change of
        screens can also change the way they are lit (KMS for one screen, labwc for both)."""
        self.media.stop()
        replug_mark(time.time())
        self.pg.quit()
        if os.environ.get("ARMONICO_SESSION"):
            mark_exit()
            os._exit(0)
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)], dict(os.environ, ARMONICO_REPLUG=str(time.time())))

    def open_on_display(self, name):
        """The screen choice: a screen by its name, or "both" (the same picture on both). The choice is
        already saved; the session starts again and lights the screens for it."""
        log(f"Screen choice: {name}")
        self.media.stop()
        self.pg.quit()
        if os.environ.get("ARMONICO_SESSION"):
            mark_exit()
            os._exit(0)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])

    def run(self):
        if getattr(self, "ui", None) is not None:
            return self.ui.run()                 # the screens of device/ui; what follows is the fallback
        was_lesson = False
        while True:
            for e in self.pg.event.get():
                if e.type == self.pg.QUIT:
                    return
                if e.type == self.pg.FINGERDOWN:
                    self.tap(int(e.x * W), int(e.y * H))
                elif e.type == self.pg.MOUSEBUTTONDOWN and e.button == 1 and not getattr(e, "touch", False):
                    self.tap(*e.pos)             # a mouse, for testing; a touch already came as FINGERDOWN
            with self.ev_lock:
                events, self.events = self.events, []
            for topic, payload in events:
                if time.time() < self.summary_until and topic == "piano/device/button":
                    self.summary_until = 0           # any button closes the card
                    continue
                self.handle(topic, payload)
            self.follow_keys()
            self.follow_music()
            lesson = self.lesson_on()
            self.follow_request()
            if lesson:
                self.follow_lesson()
            self.follow_ghost()
            if lesson != was_lesson:
                was_lesson = lesson
                if not lesson:
                    self.go(self.view)
            self.media.tick()
            self.draw()
            if MIRROR is not None:
                MIRROR.present(self.surf)        # both screens: the same picture on the HDMI
            self.clock.tick(FPS)


def start_ui(screen, pg):
    """The screens of device/ui, drawn at the display's own resolution. When they cannot load,
    the screens above run as before, on an 800x480 picture."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from ui.app import App
        return App(screen, pg, save_settings, FONT_DIR)
    except Exception as e:
        import traceback
        log(f"The new screens did not load, the previous ones run: {e}\n{traceback.format_exc()}")
        open_fallback(pg)
        screen.surf = pg.display.get_surface()
        return None


def open_fallback(pg):
    """The previous screens draw an 800x480 picture, full screen, as they always did, now on the device's
    own 5" screen (DSI) first. Under labwc (both screens) the window stays where labwc put it, on the DSI,
    and the HDMI gets the same picture through the mirror window (Screen.run)."""
    con = connected()
    dsi = next((d["name"] for d in con if d["name"].startswith("DSI")), None)
    try:
        if pg.display.get_driver() == "wayland":
            pg.display.set_mode((W, H), pg.NOFRAME)
            return
        idx = display_index(pg, dsi) if dsi else DISPLAY_NOW
        for flags, kw in ((pg.FULLSCREEN, {"display": idx}), (pg.FULLSCREEN, {})):
            try:
                pg.display.set_mode((W, H), flags, **kw)       # the 5" at its own 800x480 needs no stretch
                return
            except (pg.error, TypeError):
                continue
        raise pg.error("no mode for 800x480")
    except pg.error as e:
        log(f"The previous screens did not open at 800x480 ({e}): they draw on the screen as it is")


DISPLAY_NOW = 0
DRAW_MAX_W = 1920          # wider screens (4K) are drawn at this width and stretched by the GPU


def display_index(pygame, name=None):
    """SDL's number of the display kept in the settings when two are connected, else the first.

    SDL numbers the displays in the order of the DRM driver's connectors (on a Pi 4 the HDMI
    ports come before DSI), not by their names, so the kept one is found by its size first."""
    want = name or (load_settings().get("display") or {}).get("pick")
    con = connected()
    if want == "both":                           # both: the picture is drawn for the device's own screen
        want = next((d["name"] for d in con if d["name"].startswith("DSI")), None)
    count = max(1, pygame.display.get_num_displays())
    pick = next((d for d in con if d["name"] == want), None)
    # HDMI off (DSI only): a screen kept in the settings that is not there now (an HDMI one) falls back to
    # the DSI, never to SDL's display 0, which on a Pi 4 is the HDMI port
    if pick is None:
        pick = next((d for d in con if d["name"].startswith("DSI")), None)
    if pick is None:
        return 0
    try:
        sizes = [tuple(x) for x in pygame.display.get_desktop_sizes()]
    except (pygame.error, AttributeError):
        sizes = []
    mode = tuple(pick["mode"]) if pick.get("mode") else None
    same = [i for i, size in enumerate(sizes) if mode and (size == mode or size == mode[::-1])]
    if len(same) == 1:
        return same[0]
    # two screens of one size: the connector order, HDMI before DSI as the driver lists them
    # HDMI off (DSI only): con holds no HDMI any more, and SDL still lists the HDMI ports first, so the DSI is
    # SDL's last display
    # order = sorted(con, key=lambda d: (not d["name"].startswith("HDMI"), d["name"]))
    # i = next((k for k, d in enumerate(order) if d["name"] == want), 0)
    # return max(0, min(i, count - 1))
    return count - 1


def connected():
    """The connected screens from the kernel: [{name, mode, connected}], the preferred mode first."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import diag
        return [d for d in diag.displays() if d.get("connected")]
    except Exception:
        return []


def replug_mark(at=None):
    """When the program last started again for a change of screens: a file in the session's run folder,
    since in the device's session the program ends and a new one starts. None reads it."""
    f = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "armonico-replug"
    if at is None:
        try:
            return float(f.read_text())
        except (OSError, ValueError):
            return 0.0
    try:
        f.write_text(str(at))
    except OSError:
        pass


def write_labwc_config(folder, output=None, mirror_to=None):  # noqa: C901
    # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
    """labwc's settings for the session of both screens: device/labwc with a window rule that puts
    the screen's window on its screen, full screen, and wl-mirror showing that screen on the other.
    The window opens borderless at the screen's size and labwc makes it full screen: SDL 2.32
    asking for full screen itself waits for labwc forever."""
    src = Path(__file__).resolve().parent / "labwc"
    dst = Path(folder)
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("environment", "menu.xml"):
        if (src / name).exists():
            (dst / name).write_text((src / name).read_text())
    ok = re.compile(r"[\w.-]+")

    def rule(title, out):
        # output= only: a second name= on the same element is a duplicate attribute (the element is
        # already <action name=...>), the whole rc.xml failed to parse ("config errors detected"),
        # no rule applied and both windows opened on the HDMI screen
        move = f'      <action name="MoveToOutput" output="{out}" />\n' if out and ok.fullmatch(out) else ""
        return (f'    <windowRule title="{title}">\n{move}'
                '      <action name="ToggleFullscreen" />\n    </windowRule>\n')
    rules = rule("Armonico Pi", output)
    if mirror_to and ok.fullmatch(mirror_to):
        rules += rule(Mirror.TITLE, mirror_to)
    # no rule for the video's windows (MpvPlayer): mpv asks for full screen on its screen by name itself. With a
    # rule as well, the two full-screen requests cancelled each other and a window came back small, on the 5"
        # wl-mirror, the fallback when the mirror window cannot open, is placed by its own option
    rc = (src / "rc.xml").read_text().replace("  </windowRules>", rules + "  </windowRules>", 1)
    (dst / "rc.xml").write_text(rc)
    (dst / "autostart").unlink(missing_ok=True)
    return dst


def set_display(pygame, index=0):
    """Full screen at the display's own size (ARMONICO_SCREEN_SIZE=WxH overrides it, for tests).
    On KMS the size is the screen's preferred mode as the kernel reads it from the screen, not
    whatever mode the screen was left in (a 24" screen came up at 720x480, soft and stretched)."""
    m = re.fullmatch(r"(\d+)x(\d+)", ENV.get("ARMONICO_SCREEN_SIZE", ""))
    if m:
        return pygame.display.set_mode((int(m.group(1)), int(m.group(2))))
    else:
        try:
            sizes = pygame.display.get_desktop_sizes()
            size = sizes[index] if index < len(sizes) else sizes[0]
        except (pygame.error, IndexError, AttributeError):
            size = (W, H)
        if pygame.display.get_driver() == "KMSDRM":
            want = (load_settings().get("display") or {}).get("pick")
            con = connected()
            if want == "both":
                want = next((d["name"] for d in con if d["name"].startswith("DSI")), None)
            mine = next((d for d in con if d["name"] == want), None) or (con[0] if len(con) == 1 else None)
            if mine and mine.get("mode"):
                size = tuple(mine["mode"])
    global DISPLAY_NOW
    DISPLAY_NOW = index
    if pygame.display.get_driver() == "wayland":
        # Under labwc: a borderless window at the screen's size, which labwc's window rule moves to the
        # chosen screen and makes full screen (write_labwc_config). pygame.FULLSCREEN here never returns.
        flags = pygame.NOFRAME
        global SCALED_DISPLAY
        if (GPU_VIDEO_FROM_W < size[0] <= DRAW_MAX_W and not ENV.get("ARMONICO_MIRROR")
                and ENV.get("ARMONICO_GPU_VIDEO", "1") != "0"):
            # a big screen alone under labwc: through SDL's renderer at a factor of 1 (still drawn 1 to 1), so the
            # rest video can go to the GPU at its own size (DisplayVideo). Under labwc the frames are never torn.
            try:
                surf = pygame.display.set_mode(size, flags | pygame.SCALED)
                SCALED_DISPLAY = True
                log(f"Screen through the renderer at {size[0]}x{size[1]}: the video is stretched by the GPU")
                return surf
            except pygame.error as e:
                log(f"Renderer drawing did not open ({e}): drawing as before")
        if size[0] > DRAW_MAX_W:
            plan = (DRAW_MAX_W, int(round(DRAW_MAX_W * size[1] / size[0] / 2)) * 2)
            try:
                return pygame.display.set_mode(plan, flags | pygame.SCALED)
            except pygame.error as e:
                log(f"Scaled drawing did not open ({e}): drawing at the screen's own size")
        return pygame.display.set_mode(size, flags)
    if size[0] > DRAW_MAX_W:
        # A 4K screen is drawn at 1920 wide and the GPU stretches it: a Pi 4 drawing every pixel of
        # 3840x2160 in Python, and a video on it, falls far behind. Up to 1920 it draws sharp, 1 to 1.
        plan = (DRAW_MAX_W, int(round(DRAW_MAX_W * size[1] / size[0] / 2)) * 2)
        try:
            return pygame.display.set_mode(plan, pygame.FULLSCREEN | pygame.SCALED, display=index)
        except (pygame.error, TypeError) as e:
            log(f"Scaled drawing did not open ({e}): drawing at the screen's own size")
    try:
        return pygame.display.set_mode(size, pygame.FULLSCREEN, display=index)
    except pygame.error:
        return pygame.display.set_mode(size, pygame.FULLSCREEN)


SPLASH = {"surf": None, "logo": None}                 # the last splash frame, which the first frame of the screens fades out of
SPLASH_LOGO = Path(__file__).resolve().parent / "ui" / "data" / "logos" / "armonico-logo-dark.png"
# no dots in Hebrew: the Hebrew Heebo file has no punctuation, only letters
SPLASH_STEPS = {"he": ("מזהה מסכים", "טוען צלילים", "מכין את המסך", "מוכן"),
                "en": ("Detecting screens…", "Loading sounds…", "Preparing the screen…", "Ready")}


def splash(pygame, step=0):
    """Something on the screen at once, while the rest loads (a few seconds on a Pi 4): the screen
    stayed black until the first full frame. The logo (transparent, with its line, the version for a dark
    background), what is being done now, and a thin gold bar that fills as the steps go by. The last frame
    is kept, and the first frame of the screens fades out of it (ui/app.py run)."""
    try:
        surf = pygame.display.get_surface()
        sw, sh = surf.get_size()
        k = sh / 480
        surf.fill((14, 20, 38))
        logo = SPLASH["logo"]
        if logo is None or logo[0] != sw:
            img = pygame.image.load(str(SPLASH_LOGO)).convert_alpha()
            w = int(sw * 0.52)
            logo = SPLASH["logo"] = (sw, pygame.transform.smoothscale(img, (w, int(w * img.get_height() / img.get_width()))))
        img = logo[1]
        surf.blit(img, ((sw - img.get_width()) // 2, int(sh * 0.5 - img.get_height() * 0.5 - sh * 0.04)))
        lang = "he" if load_settings().get("lang") == "he" else "en"
        text = SPLASH_STEPS[lang][max(0, min(step, 3))]
        font_file = next((str(FONT_DIR / n) for n in (("Heebo-Hebrew-Medium.ttf",) if lang == "he" else ()) +
                          ("Heebo-Medium.ttf", "Heebo-Regular.ttf") if (FONT_DIR / n).exists()), None)
        font = pygame.font.Font(font_file, max(14, int(17 * k)))
        if lang == "he":
            try:
                from bidi.algorithm import get_display
                text = get_display(text, base_dir="R")
            except ImportError:
                pass
        label = font.render(text, True, (159, 176, 212))
        surf.blit(label, ((sw - label.get_width()) // 2, int(sh - 88 * k - label.get_height() * 0.8)))
        bw, bh = int(sw * 0.35), max(2, int(3 * k))
        bx, by = (sw - bw) // 2, int(sh - 66 * k)
        pygame.draw.rect(surf, (34, 44, 74), (bx, by, bw, bh), border_radius=bh // 2)
        pygame.draw.rect(surf, (226, 176, 74), (bx, by, int(bw * (step + 1) / 4), bh), border_radius=bh // 2)
        pygame.display.flip()
        SPLASH["surf"] = surf.copy()
    except Exception:
        pass


NO_DISPLAY_EXIT = 3          # the screen did not open; device/session.sh counts these


def no_display_file():
    """How many times in a row the screen did not open since boot (a tmpfs, cleared by a reboot)."""
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(run) / f"{Path(__file__).resolve().parent.parent.name}-screen.nodisplay"


def no_display(err):
    """No screen to open. In the device's session the miss is counted and the program ends with
    NO_DISPLAY_EXIT: after a few in a row, device/session.sh stops starting it until a reboot, since a DSI
    panel cannot be connected while the Pi is on. The keys, the sound and MQTT are another service."""
    if not os.environ.get("ARMONICO_SESSION"):
        sys.exit(f"No display: {err}")
    f = no_display_file()
    try:
        n = int(f.read_text().strip() or 0) + 1
    except (OSError, ValueError):
        n = 1
    try:
        f.write_text(f"{n}\n")
    except OSError:
        pass
    log(f"No display ({err}), miss {n} in a row")
    sys.exit(NO_DISPLAY_EXIT)


def mark_exit():
    """Ending on purpose: the ready file says so ("exit <pid>"), so the device service, which stops the
    alarm or a song when the screen program dies, leaves them playing (armonico_pi.Device.watch_screen)."""
    ready = os.environ.get("ARMONICO_READY_FILE")
    if not ready:
        return
    try:
        p = Path(ready)
        if p.read_text().split()[:1] == [str(os.getpid())]:
            p.write_text(f"exit {os.getpid()}\n")
    except (OSError, IndexError):
        pass


def open_display():
    """KMS for one screen, straight on it with no desktop; Wayland under labwc for both."""
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    os.environ.setdefault("SDL_AUDIODRIVER", "alsa")
    os.environ.setdefault("PYGAME_FORCE_SCALE", "photo")     # the stretch is smooth, not blocky
    # With no desktop, SDL moves the pointer one pixel for each unit the mouse reports, which crawls
    # on a big screen. The speed grows with the width: 1 on the 800 wide screen, 2.4 on a 1920 one.
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import diag
        wide = max((d["mode"][0] for d in diag.displays() if d.get("connected") and d.get("mode")), default=W)
    except Exception:
        wide = W
    if os.environ.get("SDL_VIDEODRIVER") == "kmsdrm":                  # under labwc, libinput sets the speed
        # 1.5 times that again: still a little slow on both screens
        os.environ.setdefault("SDL_MOUSE_NORMAL_SPEED_SCALE", f"{1.5 * max(1.0, wide / W):.1f}")
        # Each frame on its screen's refresh. Without vsync SDL asks KMS for an immediate (async) page
        # flip, which the Pi 4's driver reports it can do and then refuses (mesa issue 10242): most
        # frames never reached the screen, so video, touches and the pointer all looked slow, on the
        # 5" as on the 24". labwc (version 21) flips on the refresh, which is why it was smooth there.
        os.environ.setdefault("SDL_RENDER_VSYNC", "1")
    import pygame
    pygame.mixer.pre_init(48000, -16, 2, 4096)      # the idle music needs no quick start, and a long buffer rides over a busy Pi
    try:
        pygame.display.init()
        pygame.display.set_caption("Armonico Pi")     # before the window maps: labwc's rule matches this title
        set_display(pygame, display_index(pygame))
    except pygame.error as e:
        no_display(e)
    ready = os.environ.get("ARMONICO_READY_FILE")      # device/session.sh waits for it before giving up on labwc
    if ready:
        try:
            Path(ready).write_text(f"{os.getpid()} {pygame.display.get_driver()}\n")
        except OSError:
            pass
    try:
        no_display_file().unlink(missing_ok=True)   # a screen that opened: the count of misses starts again
    except OSError:
        pass
    log(f"Screen open: {pygame.display.get_driver()}, {pygame.display.get_surface().get_size()}")
    pygame.font.init()
    splash(pygame, 0)
    splash(pygame, 1)
    try:
        pygame.mixer.init()
    except pygame.error as e:
        log(f"No sound for the idle music: {e}")
    pygame.mouse.set_visible(False)
    pygame.display.set_caption("Armonico Pi")
    splash(pygame, 2)
    global MIRROR
    # HDMI off (DSI only): no second screen, so no mirror window
    # target = os.environ.get("ARMONICO_MIRROR")
    # if target and pygame.display.get_driver() == "wayland":
    #     MIRROR = Mirror.open(pygame, target)
    return pygame


MIRROR = None


GPU_VIDEO_FROM_W = 1280      # a screen wider than this shows the rest video through the GPU
SCALED_DISPLAY = False       # the screen was opened through SDL's renderer under labwc (set_display)


def open_video_output(pygame, size):
    # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
    """Where the rest video goes to be stretched by the GPU: the screen's own renderer, when set_display opened the
    screen through it (a big HDMI screen alone under labwc). RuntimeError otherwise: the screen is drawn as before."""
    if pygame.display.get_driver() == "wayland" and SCALED_DISPLAY:
        return DisplayVideo(pygame, size)
    raise RuntimeError("no GPU output for the video here")


class DisplayVideo:
    # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
    """The rest video through the screen's own window and SDL renderer (the screen is opened SCALED at a factor of 1
    under labwc, still drawn 1 to 1): each frame goes to the GPU at the video's own size (1280x720) and is stretched by
    it to the screen, instead of the CPU scaling and copying 1920x1080 frames (12 to 15 a second on a Pi 4). Anything
    else is the screen's usual flip, through the same renderer. One window only: a second window over the screen for
    the video left labwc without an output for it ("view has no output") and the picture in moving bands on the Pi,
    and closing it ended the program in a test.

    The upload and the wait for the screen's refresh are called in SDL through ctypes, which lets go of Python's lock
    (the GIL) for the call, as pygame's own flip does. pygame._sdl2's Texture.update and Renderer.present keep it: for
    up to a refresh each frame GStreamer's thread could not hand over the next frame (its callback is Python), frames
    came late and were dropped, and the video was slow and jerky on the Pi. pygame._sdl2 stays only as the fallback."""

    XBGR8888 = 0x16561804        # SDL_PIXELFORMAT_XBGR8888: bytes R, G, B, pad in memory, as the frames come (RGBx)
    STREAMING = 1                # SDL_TEXTUREACCESS_STREAMING: a texture updated every frame

    def __init__(self, pygame, size):
        from pygame._sdl2.video import Window, Renderer, Texture
        self.pg, self.Texture = pygame, Texture
        self.win = Window.from_display_module()
        self.ren = Renderer.from_window(self.win)
        self.size = size
        self.tex = None
        self.tex_size = None
        self.sdl = self.cren = self.ctex = None
        self.last = None
        try:
            self._open_sdl()
        except Exception as e:
            self.sdl = None
            log(f"GPU video through pygame's own calls ({type(e).__name__}: {e})")
        import atexit
        atexit.register(self.close)          # the texture goes before pygame closes the display, or the exit crashed

    @staticmethod
    def _sdl_path():
        """The SDL library pygame loaded (the system's, or the one in pygame's own wheel), from this process' map."""
        with open("/proc/self/maps") as f:
            for line in f:
                path = line.split()[-1]
                name = path.rsplit("/", 1)[-1]
                # libSDL2-2.0.so.0 (the system's) or libSDL2-2-<hash>.0.so.0 (pygame's wheel); not SDL2_image and the like
                if name.startswith(("libSDL2-", "libSDL2.")) and ".so" in name:
                    return path
        raise RuntimeError("SDL is not loaded")

    def _open_sdl(self):
        import ctypes
        sdl = ctypes.CDLL(self._sdl_path())             # the same library, already loaded: the same renderer
        vp, i32, u32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
        sdl.SDL_GetWindowFromID.restype, sdl.SDL_GetWindowFromID.argtypes = vp, [u32]
        sdl.SDL_GetRenderer.restype, sdl.SDL_GetRenderer.argtypes = vp, [vp]
        sdl.SDL_CreateTexture.restype, sdl.SDL_CreateTexture.argtypes = vp, [vp, u32, i32, i32, i32]
        sdl.SDL_UpdateTexture.restype, sdl.SDL_UpdateTexture.argtypes = i32, [vp, vp, ctypes.c_char_p, i32]
        sdl.SDL_RenderCopy.restype, sdl.SDL_RenderCopy.argtypes = i32, [vp, vp, vp, vp]
        sdl.SDL_RenderPresent.restype, sdl.SDL_RenderPresent.argtypes = None, [vp]
        sdl.SDL_DestroyTexture.restype, sdl.SDL_DestroyTexture.argtypes = None, [vp]
        win = sdl.SDL_GetWindowFromID(int(self.win.id))
        ren = sdl.SDL_GetRenderer(win) if win else None
        if not ren:
            raise RuntimeError("no renderer on the screen's window")
        self.sdl, self.cren = sdl, ren

    def owns(self, event):
        return False

    def present_raw(self, raw, size):
        """A frame of RGBx bytes, size (w, h), stretched over the whole screen."""
        if self.sdl is None:
            return self.present(self.pg.image.frombuffer(raw, size, "RGBX"))
        sdl = self.sdl
        if self.ctex is None or self.tex_size != size:
            if self.ctex:
                sdl.SDL_DestroyTexture(self.ctex)
            self.ctex = sdl.SDL_CreateTexture(self.cren, self.XBGR8888, self.STREAMING, size[0], size[1])
            if not self.ctex:
                raise RuntimeError("the video texture did not open")
            self.tex_size, self.last = size, None
            self._log(size)
        if raw is not self.last:                        # the same frame again is only shown, not sent again
            if sdl.SDL_UpdateTexture(self.ctex, None, raw, size[0] * 4) != 0:
                raise RuntimeError("the video frame did not reach the GPU")
            self.last = raw
        # the whole screen, exactly where the screen's own frames go (pygame's flip copies with no rectangle too)
        rc = sdl.SDL_RenderCopy(self.cren, self.ctex, None, None)
        if rc != 0 and not getattr(self, "_rc_logged", False):
            self._rc_logged = True
            sdl.SDL_GetError.restype = __import__("ctypes").c_char_p
            log(f"GPU video: the copy failed: {sdl.SDL_GetError()}")
        sdl.SDL_RenderPresent(self.cren)

    def _log(self, size):
        if not getattr(self, "_logged", False):
            self._logged = True
            try:
                log(f"GPU video: frame {size[0]}x{size[1]}, screen drawn at {self.ren.logical_size}, window "
                    f"{tuple(self.win.size)}, viewport {self.ren.get_viewport()}, "
                    f"{'SDL calls without the GIL' if self.sdl else 'pygame calls'}")
            except Exception:
                pass

    def present(self, surf):
        size = surf.get_size()
        if self.tex is None or self.tex_size != size:
            self.tex = self.Texture.from_surface(self.ren, surf)      # the surface's own pixel format: no conversion
            self.tex_size = size
            self._log(size)
        else:
            self.tex.update(surf)
        # the whole screen, exactly where the screen's own frames go: no size of ours to get wrong
        self.tex.draw()
        self.ren.present()

    def close(self):
        import gc
        if self.ctex and self.sdl is not None:
            try:
                self.sdl.SDL_DestroyTexture(self.ctex)
            except Exception:
                pass
        self.ctex = None
        self.last = None
        self.tex = None
        gc.collect()


class Mirror:
    # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
    """Both screens: the same picture on the second one, stretched to fill it.

    A second window of this program (labwc's rule puts it on the second screen, full screen), and each
    frame of the device's screen copied into it by the GPU, scaled to the whole window: no bars at the
    sides. Clicks on it do nothing. When it cannot open, wl-mirror shows the screen there instead
    (fitted, with bars)."""

    TITLE = "Armonico mirror"

    def __init__(self, pygame, size):
        from pygame._sdl2.video import Window, Renderer, Texture
        self.Texture = Texture
        self.win = Window(self.TITLE, size=size, borderless=True)
        self.ren = Renderer(self.win, accelerated=1, vsync=False)
        self.tex = None
        self.size = None
        self.at = 0.0

    @classmethod
    def open(cls, pygame, target):
        mode = next((tuple(d["mode"]) for d in connected() if d["name"] == target and d.get("mode")), (1920, 1080))
        try:
            m = cls(pygame, mode)
            log(f"Mirror on {target}: {mode[0]}x{mode[1]}, stretched")
            return m
        except Exception as e:
            log(f"Mirror window did not open ({e}): wl-mirror instead")
            src = next((d["name"] for d in connected() if d["name"].startswith("DSI")), "")
            if src and shutil.which("wl-mirror"):
                subprocess.Popen(["wl-mirror", "--fullscreen-output", target, "--scaling", "fit", src],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None

    def owns(self, event):
        """An event from the mirror's window (a mouse over the second screen): not for the screens."""
        w = getattr(event, "window", None)
        return w is not None and w == self.win

    def present(self, surf):
        now = time.monotonic()
        if now - self.at < 1 / 45:                   # the loop runs at 30: a 1/30 limit dropped every frame
            return                                   # that came a little early, and the HDMI copy stuttered
        self.at = now
        try:
            size = surf.get_size()
            if self.tex is None or self.size != size:
                self.tex = self.Texture(self.ren, size, streaming=True)
                self.size = size
            self.tex.update(surf)
            self.ren.clear()
            self.tex.draw()                          # the whole window: stretched to fill it
            self.ren.present()
        except Exception as e:
            log(f"Mirror: {e}")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--labwc-config":      # device/session.sh, before labwc starts
        write_labwc_config(sys.argv[2], sys.argv[3], sys.argv[4])
        sys.exit(0)
    try:
        # armonico-pi-report sends SIGUSR1: every thread's Python stack goes to session.log, so a screen
        # that looks frozen shows where it waits
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (ImportError, AttributeError, ValueError, RuntimeError):
        pass
    Screen(open_display()).run()
    mark_exit()
