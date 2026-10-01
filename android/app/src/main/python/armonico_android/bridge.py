"""bridge.py - the Armonico bridge for Android.

The same MQTT surface as bridge/piano_bridge.sh (see docs/mqtt.md), so Home Assistant, the
Telegram automation and the armonico command see no difference between a Linux machine and a
phone. The shell bridge talks to the keyboard through ALSA (aseqdump, aplaymidi), which an
Android app cannot reach. Here the app's Kotlin side owns the USB MIDI device and hands this
module the raw bytes; this module hands bytes back to be sent.

Everything that decides goes through one thread (the logic thread) and one queue, so a key, an
MQTT command and a disconnect are handled one after the other and never race. Songs and the
alarm play in a player thread of their own, which only sends bytes and reports when it ends.

Kotlin calls:
    start(config_json, link)   link: send(byte[]), isConnected(), deviceName(), reopen(), status(str)
    stop()
    midi_in(byte[])            raw bytes from the keyboard
    device_changed(connected)  the keyboard came or went
    local_stop(reason)         the stop button in the app or in the notification
    play_local(song)           the test button
    status_json()
"""
import json
import os
import queue
import re
import secrets
import threading
import time
from collections import deque

try:
    import mido
except Exception:                    # the tests run without it only for the parts that need none
    mido = None
import paho.mqtt.client as mqtt

try:
    import piano_lang                 # copied in from lessons/ at build time
except Exception:
    piano_lang = None

from . import lessons
from .broker import Broker
from .recorder import Recorder
from . import console
from .telegram import Bot, PREFIX as APP_CHAT, nice

# ---------------------------------------------------------------------------
# Settings that are not user settings (the same values as the shell bridge)
# ---------------------------------------------------------------------------
SHORT_MAX = 3            # up to 3 keys: matched only when nothing else was played before the pause
SHORT_IDLE_MS = 1500     # the pause that closes a short sequence
LEARN_IDLE_MS = 5000     # the pause that ends a sequence being learned
LEARN_TIMEOUT = 60       # learning closes after 60 seconds with no keys
BUFFER_TIMEOUT = 8       # seconds with no key that empty the buffer
MAX_BUFFER = 70
PEDAL_HOLD_SECS = 2
KEEPALIVE_SECS = 300
STOP_LOCK_SECS = 3
# piano_bridge.sh waits RECONNECT_TIMEOUT seconds for a lost keyboard, then hands over to systemd,
# which starts it again; STUCK_THRESHOLD such failed starts in a row publish "stuck" and an alert.
# Here the bridge never exits, so the same states follow from how long the keyboard has been away.
RECONNECT_TIMEOUT = 30
STUCK_THRESHOLD = 3
START_CYCLE = 45         # one failed start there: 10 s of looking, 30 s of waiting, 5 s before systemd restarts it
TICK = 0.1               # how often the logic thread looks at the clock when nothing arrives

COMMAND_TOPICS = ("piano/play", "piano/alarm/set", "piano/stop", "piano/pause",
                  "piano/log/control", "piano/shortcut/cmd", "piano/game/cmd", "piano/status",
                  "piano/shortcut/reply")
SCREEN_QUIET = 3         # seconds after the lesson screen's last note before keys count again
PAGE_QUIET = 15          # seconds after the last sign of an open lesson screen

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(n):
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def to_display(name):
    """'WeAreTheChampions' and 'ode_to_joy' become 'We Are The Champions' and 'ode to joy'."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", s)
    return s.replace("_", " ")


def duration_words(secs):
    secs = int(secs)
    if secs < 60:
        return "1 second" if secs == 1 else f"{secs} seconds"
    m, s = divmod(secs, 60)
    mw = "1 minute" if m == 1 else f"{m} minutes"
    if s == 0:
        return mw
    return f"{mw} 1 second" if s == 1 else f"{mw} {s} seconds"


def song_length_words(secs):
    """The shell bridge's get_duration: '45 seconds', '3 minutes 12 seconds', or 'unknown'."""
    secs = int(secs)
    if secs <= 0:
        return "unknown"
    if secs < 60:
        return f"{secs} seconds"
    m, s = divmod(secs, 60)
    mw = "1 minute" if m == 1 else f"{m} minutes"
    return mw if s == 0 else f"{mw} {s} seconds"


def snapshot(d):
    """A copy of a deque another thread may be adding to: list() of one that changes midway raises."""
    for _ in range(5):
        try:
            return list(d)
        except RuntimeError:
            time.sleep(0.001)
    return []


def panic_bytes():
    """All Notes Off, pedal up, Reset All Controllers, volume 100 and expression 127 on 16 channels.
    Reset All Controllers leaves volume and expression alone (MIDI RP-015), so they are set too."""
    out = bytearray()
    for ch in range(16):
        st = 0xB0 | ch
        out += bytes((st, 123, 0, st, 64, 0, st, 121, 0, st, 7, 100, st, 11, 127))
    return bytes(out)


# ---------------------------------------------------------------------------
# Reading the keyboard's bytes
# ---------------------------------------------------------------------------
class MidiParser:
    """Raw MIDI bytes to messages, with running status. Real-time bytes (clock, active sensing)
    may sit anywhere, even inside another message, and are passed over. Sysex is skipped."""

    def __init__(self):
        self.status = 0
        self.data = []
        self.in_sysex = False

    @staticmethod
    def _need(status):
        hi = status & 0xF0
        if hi in (0xC0, 0xD0):
            return 1
        if hi == 0xF0:
            return {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)
        return 2

    def feed(self, data):
        msgs = []
        for b in data:
            if b >= 0xF8:                       # real-time: no effect on anything around it
                continue
            if b == 0xF0:
                self.in_sysex = True
                continue
            if b == 0xF7:
                self.in_sysex = False
                continue
            if self.in_sysex:
                if b & 0x80:                    # a status byte ends a sysex that lost its F7
                    self.in_sysex = False
                else:
                    continue
            if b & 0x80:
                self.status = b
                self.data = []
                if self._need(b) == 0:
                    self.status = 0
                continue
            if not self.status:
                continue
            self.data.append(b)
            if len(self.data) == self._need(self.status):
                msgs.append((self.status, *self.data))
                self.data = []
                if self.status >= 0xF0:         # system common messages have no running status
                    self.status = 0
        return msgs


# ---------------------------------------------------------------------------
# Playing a MIDI file on the keyboard
# ---------------------------------------------------------------------------
def load_events(path):
    """(seconds from the start, bytes) for every message of the file that is not meta."""
    mid = mido.MidiFile(path)
    t = 0.0
    events = []
    for msg in mid:
        t += msg.time
        if msg.is_meta:
            continue
        events.append((t, bytes(msg.bytes()), msg))
    return events, mid.length


def setup_before(events, upto):
    """What sets up the sound before a point in the song: the last word on every program,
    controller and pitch bend, and every sysex, in order. Sent again after a pause, because the
    panic at the pause reset the controllers."""
    last = {}
    sysex = []
    for at, data, msg in events:
        if at >= upto:
            break
        t = msg.type
        if t == "program_change":
            last[("p", msg.channel)] = data
        elif t == "control_change":
            last[("c", msg.channel, msg.control)] = data
        elif t == "pitchwheel":
            last[("b", msg.channel)] = data
        elif t == "sysex":
            sysex.append(data)
    return sysex + list(last.values())


class Player(threading.Thread):
    """One song, or the alarm (the same song in a loop). Stopping and pausing are events, so the
    player answers within a few milliseconds and never needs to be killed."""

    def __init__(self, bridge, path, name, loop):
        super().__init__(daemon=True, name="armonico-player")
        self.bridge = bridge
        self.path = path
        self.name_ = name
        self.loop = loop
        self.stop_evt = threading.Event()
        self.pause_evt = threading.Event()      # set while paused
        self.resume_evt = threading.Event()
        self.position = 0.0                     # seconds into the song, kept at a pause

    def run(self):
        result = "Finished"
        try:
            events, _ = load_events(self.path)
            rounds = 0
            fails = 0
            while not self.stop_evt.is_set():
                rounds += 1
                if self.loop:
                    self.bridge.log(f"🎵 Alarm playing: {to_display(self.name_)}" if rounds == 1
                                    else f"🔁 Alarm, round {rounds}: {to_display(self.name_)}")
                try:
                    self._play_once(events)
                    fails = 0
                except LinkError:
                    if self.stop_evt.is_set():
                        break
                    if not self.bridge.keyboard_there():
                        result = "Keyboard disconnected"
                        break
                    fails += 1
                    if not self.loop or fails >= 3:
                        result = "Playback error"
                        break
                if not self.loop:
                    break
                if self.stop_evt.wait(0.5):
                    break
        except Exception as e:                  # a file mido cannot read
            self.bridge.log(f"⚡ Playback failed: {e}")
            result = "Playback error"
        if self.stop_evt.is_set():
            return                              # whoever stopped it publishes the reason
        self.bridge.events.put(("player_done", self, result))

    def _play_once(self, events):
        self.position = 0.0
        start = time.monotonic()
        i = 0
        while i < len(events):
            if self.stop_evt.is_set():
                return
            if self.pause_evt.is_set():
                self.position = time.monotonic() - start
                self.bridge.panic()
                self.bridge.log(f"⏸ Song paused at {self.position:.3f} s")
                while self.pause_evt.is_set() and not self.stop_evt.is_set():
                    self.resume_evt.wait(0.05)
                if self.stop_evt.is_set():
                    return
                self.resume_evt.clear()
                for data in setup_before(events, self.position):
                    self.bridge.send(data)
                # the notes that were sounding at the pause were cut by the panic: go on from
                # the next note, as the shell bridge's cut copy of the file does
                while i < len(events) and events[i][0] < self.position:
                    i += 1
                start = time.monotonic() - self.position
                continue
            at, data, _ = events[i]
            wait = start + at - time.monotonic()
            if wait > 0:
                # short waits, so a pause or a stop is seen at once
                self.stop_evt.wait(min(wait, 0.05))
                continue
            self.bridge.send(data)
            i += 1


class LinkError(Exception):
    pass


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------
class Bridge:
    def __init__(self, config, link):
        self.cfg = config
        self.link = link
        self.data_dir = config["data_dir"]
        self.songs_dir = config["songs_dir"]
        self.shortcuts_file = os.path.join(self.data_dir, "shortcuts.conf")
        os.makedirs(self.songs_dir, exist_ok=True)
        self.stop_key = int(config.get("stop_key", 36))
        self.stop_key_name = note_name(self.stop_key)
        self.admin_secret = config.get("admin_secret", "")
        self._lang = config.get("lang", "en")
        self.settings_file = os.path.join(self.data_dir, "settings.json")
        # written while the alarm rings and found at the next start when the bridge stopped in the middle of
        # it (the app restarted, the phone rebooted): that ends the alarm, as in the shell bridge ("Restart")
        self.alarm_mark = os.path.join(self.data_dir, "alarm-ringing")
        self.events = queue.Queue()
        self.parser = MidiParser()
        self.send_lock = threading.Lock()
        self.logs = deque(maxlen=300)
        self.running = False
        self.state = {}                        # the last value published on every topic, for /pianostatus
        self.mode = config.get("mode", "external")
        self.broker = None                     # the built-in broker, in local mode
        self.bot = None                        # the Telegram bot read by the app
        self.recorder = Recorder(config)       # armonico record: every MQTT message to a file
        self.console = deque(maxlen=400)       # the app's command screen: what was typed and the answers
        self.console_seq = 0
        self.live_log_bot = False
        self.lessons_on = bool(config.get("lessons", True) and config.get("engine_dir"))
        self.lesson_hub = None                 # set by lessons.start: the engine's keyboard ports
        # the flags the lesson engine and the bridge share, as on Linux (RUN_DIR there is /tmp)
        self.run_dir = lessons.run_dir(config)
        os.makedirs(self.run_dir, exist_ok=True)
        self.flag_game = os.path.join(self.run_dir, "piano_game_active")
        self.flag_alarm = os.path.join(self.run_dir, "piano_alarm_active")
        self.flag_song = os.path.join(self.run_dir, "piano_song_active")
        self.flag_screen = os.path.join(self.run_dir, "piano_screen_keys")
        self.flag_page = os.path.join(self.run_dir, "piano_page_open")
        for f in (self.flag_alarm, self.flag_song):
            self._unflag(f)
        self.capture = False                   # the next key is a setting being measured, not a key
        self.captured = None                   # (note, time) of the last key measured

        # playback
        self.player = None
        self.alarm = False                     # the player is the alarm
        self.current = ""                      # the song playing now
        self.paused = False
        self.last_stop = 0.0                   # for the stop lock

        # keys
        self.buffer = []
        self.last_key_ms = 0
        self.last_note_time = 0
        self.idle_done = True
        self.shortcut_lock_until = 0.0
        self.learn = None                      # {"id", "chat", "since", "armed"}
        self.pedal_down = 0.0
        self.pedal_hold_sent = False
        self.shortcuts = []
        self.shortcuts_stamp = None

        # connection
        self.connected = False
        self.connect_time = 0
        self.disconnected_at = None
        self.live_log = False
        self.last_log_base = ""
        self.broker_up = False
        self.broker_error = ""                 # why the broker is not connected, for the app
        self.first_connect = True
        self.last_keepalive = time.monotonic()
        self.away_since = time.monotonic()      # the keyboard is not there since, for reconnecting, timeout, stuck
        self.away_state = ""                    # what piano/status said about it last
        self.stuck_alerted = False

        cid = f"piano-bridge-android-{secrets.token_hex(3)}"
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        if self.mode == "local":
            self.mq.username_pw_set(config.get("local_user", "piano"), config.get("local_password") or None)
        elif config.get("user"):
            self.mq.username_pw_set(config["user"], config.get("password") or None)
        self.mq.will_set("piano/status", "offline", qos=1, retain=True)
        self.mq.reconnect_delay_set(1, 30)
        self.mq.on_connect = self._on_connect
        self.mq.on_disconnect = self._on_disconnect
        self.mq.on_connect_fail = self._on_connect_fail
        self.mq.on_message = self._on_message

    # ----------------------------------------------------------------- helpers
    def pub(self, topic, payload, retain=False):
        self.state[topic] = "" if payload is None else str(payload)
        try:
            self.mq.publish(topic, payload, qos=0 if not retain else 1, retain=retain)
        except Exception:
            pass                                # a dead broker never holds the keys up

    def clear(self, *topics):
        for t in topics:
            self.pub(t, None, retain=True)      # an empty retained payload deletes it

    def log(self, base):
        msg = f"{base} · {time.strftime('%H:%M:%S')}"
        print(msg)
        self.logs.append(msg)
        if self.live_log and base != self.last_log_base:
            self.last_log_base = base
            self.pub("piano/log", msg)
            if self.live_log_bot and self.bot is not None and self.bot.owner is not None:
                self.bot.send_async(self.bot.owner, msg)
        self._status()

    @property
    def lang(self):
        """The language of the Telegram answers: settings.json, which the lesson screen and the app's
        settings both write, as the Linux bridge reads it; the app's setting when the file has none."""
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                v = json.load(f).get("lang")
            if v in ("en", "he"):
                return v
        except Exception:
            pass
        return self._lang

    @lang.setter
    def lang(self, v):
        self._lang = v

    def L(self, en, he):
        return he if self.lang == "he" else en

    def reply(self, chat, text):
        if self.lang == "he":
            try:
                global piano_lang
                if piano_lang is None:
                    import piano_lang                # found later too: the lessons' folder holds it as well
                text = piano_lang.telegram(text)
            except Exception:
                pass
        chat = str(chat)
        if chat == console.CHAT:
            self.console_add("out", console.screen_text(text))
            return
        if chat.startswith(APP_CHAT):
            # a command that came through the app's own bot is answered there, never on the broker
            if self.bot is not None:
                self.bot.send_async(chat[len(APP_CHAT):], text)
            return
        self.pub("piano/shortcut/reply", f"{chat}|{text}")

    def console_add(self, kind, text):
        """A line on the app's command screen: "in" what was typed, "out" an answer."""
        self.console.append({"k": kind, "t": text, "at": time.strftime("%H:%M:%S")})
        self.console_seq += 1
        self._status()

    # ---- the flags shared with the lesson engine ----
    @staticmethod
    def _flag(path):
        try:
            with open(path, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

    @staticmethod
    def _unflag(path):
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _fresh(path, secs):
        try:
            return time.time() - os.stat(path).st_mtime < secs
        except OSError:
            return False

    def game_on(self):
        """A lesson is running: keys teach and trigger nothing, and songs wait."""
        return os.path.exists(self.flag_game)

    def screen_active(self):
        """The lesson screen is sounding notes, which some keyboards hand back as if played."""
        return self._fresh(self.flag_screen, SCREEN_QUIET)

    def page_open(self):
        """A lesson screen is open and in view somewhere."""
        return self._fresh(self.flag_page, PAGE_QUIET)

    def _flags_for_player(self):
        """FLAG_ALARM and FLAG_SONG follow what plays, for the engine (it stops a lesson for the alarm)."""
        playing = self.player is not None
        (self._flag if playing and self.alarm else self._unflag)(self.flag_alarm)
        (self._flag if playing and not self.alarm else self._unflag)(self.flag_song)

    def keyboard_there(self):
        try:
            return bool(self.link.isConnected())
        except Exception:
            return False

    def send(self, data):
        with self.send_lock:
            try:
                ok = self.link.send(bytes(data))
            except Exception as e:
                raise LinkError(str(e))
        if ok is False:
            raise LinkError("the keyboard did not take the message")

    def panic(self):
        try:
            self.send(panic_bytes())
        except LinkError:
            pass

    def _status(self):
        try:
            self._flags_for_player()
        except Exception:
            pass
        try:
            self.link.status(self.status_json())
        except Exception:
            pass

    def status_json(self):
        return json.dumps({
            "broker": self.broker_up,
            "keyboard": self.connected,
            "device": self._device_name(),
            "alarm": self.alarm and self.player is not None,
            "song": self.current if self.player is not None else "",
            "paused": self.paused,
            "mode": self.mode,
            "source": self.state.get("piano/play/source", "") if self.player is not None else "",
            "live": self.live_log,
            "lessons": lessons.running(),
            "lesson_port": int(self.cfg.get("lesson_port", 8099)),
            "code": lessons.screen_code() if lessons.running() else "",
            "capture": self.capture,
            "captured": self.captured[0] if self.captured else None,
            "captured_at": self.captured[1] if self.captured else 0,
            "broker_error": self.broker_error,
            "local_broker": self.broker is not None,
            "ws": self.broker is not None and self.broker.ws_server is not None,
            "game": self.game_on(),
            "lessons_error": lessons.error(),
            "telegram": self.bot.info() if self.bot is not None else {"on": False},
            "record": self.recorder.summary(),
            "console_seq": self.console_seq,
            "console": snapshot(self.console)[-150:],
            "log": snapshot(self.logs)[-30:],
        })

    def _device_name(self):
        try:
            return str(self.link.deviceName() or "")
        except Exception:
            return ""

    # ------------------------------------------------------------- life cycle
    def start(self):
        self.running = True
        self.log("✅ Piano bridge starting (Android)")
        self.connected = self.keyboard_there()
        host, port = self.cfg.get("host", ""), int(self.cfg.get("port", 1883))
        if self.mode == "local":
            # the built-in broker, on this phone only, as Mosquitto on 127.0.0.1 in the Linux install
            port = int(self.cfg.get("local_port", 1883))
            host = "127.0.0.1"
            # two logins, as install.sh makes them: piano acts in its own branch, piano-rec only listens
            users = {self.cfg.get("local_user", "piano"): self.cfg.get("local_password", ""),
                     "piano-rec": self.cfg.get("local_rec_password", "")}
            acl = {self.cfg.get("local_user", "piano"): [("piano/#", "readwrite")],
                   "piano-rec": [("#", "read")]}
            try:
                self.broker = Broker("127.0.0.1", port, users, os.path.join(self.data_dir, "broker_retained.json"),
                                     log=self.log, acl=acl, ws_port=int(self.cfg.get("local_ws_port", 9001))).start()
            except Exception as e:
                self.broker = None
                self.broker_error = f"the built-in broker did not start on port {port}: {e}"
                self.log(f"⚠️ The built-in broker did not start on port {port}: {e}")
        if self.recorder.wanted():
            self.recorder.start()
        token = (self.cfg.get("telegram_token") or "").strip()
        if token:
            self.bot = Bot(self, token, os.path.join(self.data_dir, "telegram.json"))
            self.bot.start()
        try:
            if not host:
                self.broker_error = "no broker address in the settings"
                raise ValueError("no broker address")
            self.mq.connect_async(host, port, keepalive=60)
            self.mq.loop_start()
        except Exception as e:
            self.log(f"⚠️ Broker settings are not usable: {e}")
        threading.Thread(target=self._logic, daemon=True, name="armonico-logic").start()
        if self.lessons_on:
            threading.Thread(target=self._start_lessons, daemon=True, name="armonico-lessons-start").start()

    def _start_lessons(self):
        time.sleep(1)                           # the broker first, so the engine's link opens at once
        try:
            lessons.start(self, self.cfg)
        except Exception as e:
            self.log(f"⚠️ Lessons did not start: {type(e).__name__}: {e}")
        self._status()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.log("🧹 Cleaning up...")
        if self.player is not None:
            self._song_cut("Bridge stopped")
        self._end_player()
        self.panic()
        for t, v in (("piano/status", "offline"), ("piano/play/status", "idle"),
                     ("piano/play/current", "unknown")):
            self.pub(t, v, retain=True)
        self.clear("piano/play/duration")
        time.sleep(0.3)                         # the last messages leave before the socket closes
        try:
            self.mq.disconnect()
            self.mq.loop_stop()
        except Exception:
            pass
        if self.bot is not None:
            self.bot.stop()
        lessons.stop()
        for f in (self.flag_alarm, self.flag_song):
            self._unflag(f)
        self.recorder.stop()
        if self.broker is not None:
            self.broker.stop()
        self.events.put(("quit",))

    # ----------------------------------------------------------------- MQTT
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.broker_up = False
            self.broker_error = f"refused: {reason_code}"
            self.log(f"⚠️ The broker refused the connection: {reason_code}")
            self._status()
            return
        self.broker_up = True
        self.broker_error = ""
        self.events.put(("broker_up",))

    def _on_connect_fail(self, client, userdata):
        """No answer at that address and port (paho tries again by itself)."""
        why = f"no answer from {self.cfg.get('host', '')}:{self.cfg.get('port', 1883)}"
        if why != self.broker_error:
            self.broker_error = why
            self.log(f"⚠️ Broker: {why}")
        self._status()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if self.broker_up and self.running:
            self.log("⚠️ Lost the broker, reconnecting...")
        self.broker_up = False
        self._status()

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
        except Exception:
            return
        self.events.put(("mqtt", msg.topic, payload))

    def _after_connect(self):
        if self.first_connect:
            # retained commands left from before are cleared before subscribing, so an old
            # piano/play does not play a song the moment the bridge starts
            self.first_connect = False
            self.pub("piano/status", "starting", retain=True)     # as wait_for_broker in the shell bridge
            self.clear("piano/stop", "piano/alarm/set", "piano/play", "piano/log/control")
            self.pub("piano/alarm/status", "off", retain=True)
            if os.path.exists(self.alarm_mark):
                self.log("⏰ The alarm was ringing when the bridge stopped: it is not rung again")
                self.pub("piano/alarm/stopped_by", "Restart", retain=True)
                self._unmark_alarm()
            else:
                self.pub("piano/alarm/stopped_by", "unknown", retain=True)
            self.pub("piano/play/status", "idle", retain=True)
            self.pub("piano/play/paused", "off", retain=True)
            self.pub("piano/play/current", "unknown", retain=True)
            self.clear("piano/play/duration")
            self.pub("piano/game/status", "idle", retain=True)
            self.log("🔁 Connected to the broker")
        else:
            self.log("🔁 Broker connection back")
        self.pub("piano/range", f"{self.cfg.get('key_lowest', 36)} {self.cfg.get('key_highest', 96)}", retain=True)
        self.pub("piano/lang", self.lang, retain=True)
        if self.connected:
            self.pub("piano/status", "online", retain=True)
            self.pub("piano/port", self._device_name(), retain=True)
        else:
            self.pub("piano/status", "disconnected", retain=True)
            self.away_state = ""                 # the tick says reconnecting, timeout or stuck again
        self.mq.subscribe([(t, 0) for t in COMMAND_TOPICS])

    # ------------------------------------------------------------- the logic thread
    def _logic(self):
        while True:
            try:
                ev = self.events.get(timeout=TICK)
            except queue.Empty:
                ev = None
            try:
                if ev is None:
                    self._tick()
                    continue
                kind = ev[0]
                if kind == "quit":
                    return
                if kind == "broker_up":
                    self._after_connect()
                elif kind == "mqtt":
                    self._command(ev[1], ev[2])
                elif kind == "midi":
                    for m in self.parser.feed(ev[1]):
                        self._midi_message(m)
                elif kind == "device":
                    self._device(ev[1])
                elif kind == "player_done":
                    self._player_done(ev[1], ev[2])
                elif kind == "stop":
                    self.stop_now(ev[1])
                elif kind == "play_local":
                    self.start_song(f"{ev[1]}|Armonico app")
                elif kind == "capture":
                    self.capture = bool(ev[1])
                    self._status()
                elif kind == "live_log":
                    self.live_log = self.live_log_bot = bool(ev[1])
                    self.log("🟢 Live log on" if ev[1] else "🔴 Live log off")
                self._tick()
            except Exception as e:              # one bad message never ends the bridge
                self.log(f"⚠️ {type(e).__name__}: {e}")

    def _tick(self):
        now = time.monotonic()
        if self.pedal_down and not self.pedal_hold_sent and now - self.pedal_down >= PEDAL_HOLD_SECS:
            self.pedal_hold_sent = True
            self.pub("piano/pedal", "hold")
        self._on_idle()
        # what the app shows changes on its own here: a lesson begins or ends, the lesson screen's minute runs out
        key = (self.game_on(), bool(lessons.error()), lessons.running())
        if key != getattr(self, "_shown_key", None):
            self._shown_key = key
            self._status()
        if now - self.last_keepalive >= KEEPALIVE_SECS:
            self.last_keepalive = now
            if self.connected and self.broker_up:
                self.pub("piano/status", "online", retain=True)
        self._away(now)

    def _away(self, now):
        """reconnecting while the bridge waits, timeout after RECONNECT_TIMEOUT, stuck once after
        STUCK_THRESHOLD failed starts' worth of time, with a notification on piano/alert, as the
        shell bridge publishes them. A resetting bridge says so itself."""
        if self.connected or not self.broker_up:
            return
        gone = now - self.away_since
        if gone >= RECONNECT_TIMEOUT + (STUCK_THRESHOLD - 1) * START_CYCLE:
            want = "stuck"
        elif gone >= RECONNECT_TIMEOUT:
            want = "timeout"
        else:
            want = "reconnecting"
        if want == self.away_state:
            return
        self.away_state = want
        self.pub("piano/status", want, retain=True)
        if want == "timeout":
            self.log(f"❌ Timeout: the keyboard did not come back within {RECONNECT_TIMEOUT}s")
        elif want == "stuck" and not self.stuck_alerted:
            self.stuck_alerted = True
            # not retained: a notification, so Home Assistant does not resend the old one on every reload
            self.pub("piano/alert", f"The keyboard has not come back after {STUCK_THRESHOLD} tries. If it is on, it is "
                                    "probably stuck: turn it off, wait a few seconds and turn it on again (or replug its USB cable).")
            self.log(f"🚨 Keyboard stuck after {STUCK_THRESHOLD} tries, likely needs a power-cycle")

    # ------------------------------------------------------------- commands
    def _command(self, topic, raw):
        raw = raw.replace("\r", "")
        if topic == "piano/shortcut/reply":
            # the lesson engine answers an app chat on the broker: the app's bot takes it from there
            chat, _, text = raw.partition("|")
            if chat == console.CHAT:
                self.console_add("out", console.screen_text(text))
            elif chat.startswith(APP_CHAT) and self.bot is not None:
                self.bot.send_async(chat[len(APP_CHAT):], text)
            return
        if topic == "piano/status":
            # the retained last will of a dropped connection, while the bridge is alive
            if raw == "offline" and self.running:
                self.pub("piano/status", "online" if self.connected else "disconnected", retain=True)
            return
        if not raw.strip() or raw in ("null", "(null)"):
            return
        if topic == "piano/shortcut/cmd":
            self.handle_shortcut_cmd(raw)
        elif topic == "piano/game/cmd":
            if not lessons.running():           # the engine answers these itself when it runs
                self.handle_game_cmd(raw)
        elif topic == "piano/log/control":
            cmd = raw.strip()
            if cmd == "start":
                self.live_log = True
                self.clear("piano/log/control")
                self.log("🟢 Live log on")
            elif cmd == "stop":
                self.log("🔴 Live log off")
                self.live_log = False
                self.clear("piano/log/control")
        elif topic == "piano/stop":
            self.stop_now(raw or "Manual stop")
            self.clear("piano/stop")
        elif topic == "piano/pause":
            self.pause_request(raw.strip())
        elif topic == "piano/alarm/set":
            self.start_alarm(raw)
        elif topic == "piano/play":
            self.start_song(raw)

    @staticmethod
    def split_request(raw, default_source):
        if "|" in raw:
            song, source = raw.split("|", 1)
        else:
            song, source = raw, ""
        return song.rstrip(), source or default_source

    def find_midi(self, want):
        want = want.rstrip()
        if not want:
            return None
        try:
            names = sorted(os.listdir(self.songs_dir))
        except OSError:
            return None
        for f in names:
            base, ext = os.path.splitext(f)
            if ext.lower() in (".mid", ".midi") and base.lower() == want.lower():
                return os.path.join(self.songs_dir, f)
        return None

    def song_names(self):
        try:
            names = sorted(os.listdir(self.songs_dir), key=str.lower)
        except OSError:
            return []
        return [os.path.splitext(f)[0] for f in names if os.path.splitext(f)[1].lower() in (".mid", ".midi")]

    def _length(self, path):
        try:
            return song_length_words(mido.MidiFile(path).length)
        except Exception:
            return "unknown"

    # ------------------------------------------------------------- songs and the alarm
    def start_song(self, raw):
        song, source = self.split_request(raw, "MQTT")
        self.clear("piano/play")
        path = self.find_midi(song)
        if not path:
            self.log(f"❌ Song: no file named {song}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        if self.player is not None and self.alarm:
            self.log(f"⚡ Blocked: the alarm is playing, {to_display(name)} skipped")
            return
        if self.player is not None:
            self.log(f"⚡ Blocked: a song is already playing, {to_display(name)} skipped")
            return
        if self.game_on():
            self.log(f"⚡ Blocked: a lesson is running, {to_display(name)} skipped")
            return
        if not self.keyboard_there():
            self.log("⚡ The keyboard is not connected, the song was skipped")
            return
        self.log(f"🎵 Song starting: {to_display(name)}, started by {source}")
        self.alarm = False
        self.current = name
        self.paused = False
        self.player = Player(self, path, name, loop=False)
        self.player.start()
        self.pub("piano/play/status", "playing", retain=True)
        self.pub("piano/play/paused", "off", retain=True)
        self.pub("piano/play/current", name, retain=True)
        self.pub("piano/play/source", source, retain=True)
        self.pub("piano/play/duration", self._length(path), retain=True)
        self._status()

    def start_alarm(self, raw):
        song, source = self.split_request(raw, "Alarm")
        self.clear("piano/alarm/set")
        path = self.find_midi(song)
        if not path:
            self.log(f"❌ Alarm: no file named {song}")
            if not (self.player is not None and self.alarm):
                self._alarm_failed(f"Song not found: {song}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        if self.player is not None and self.alarm:
            return                                  # one already ringing is left as it is
        if not self.keyboard_there():
            self.log("⚡ The keyboard is not connected, the alarm was skipped")
            self._alarm_failed("Keyboard not connected")
            return
        self.log(f"⏰ Alarm started by {source}: {to_display(name)}")
        if self.player is not None:                 # the alarm replaces a song
            self._song_cut("Alarm")
            self._end_player()
            if self.paused:
                self.pub("piano/play/paused", "off", retain=True)
        self.alarm = True
        self.current = name
        self.paused = False
        self.player = Player(self, path, name, loop=True)
        self.player.start()
        self.clear("piano/alarm/error")
        try:
            open(self.alarm_mark, "w").close()
        except OSError:
            pass
        self.pub("piano/alarm/status", "on", retain=True)
        self.pub("piano/alarm/stopped_by", "unknown", retain=True)
        self.pub("piano/play/status", "playing", retain=True)
        self.pub("piano/play/current", name, retain=True)
        self.pub("piano/play/source", source, retain=True)
        self.pub("piano/play/duration", self._length(path), retain=True)
        self._status()

    def _alarm_failed(self, why):
        self.pub("piano/alarm/error", why, retain=True)
        self.pub("piano/alarm/status", "failed", retain=True)

    def _end_player(self):
        p = self.player
        self.player = None
        if p is None:
            return
        p.stop_evt.set()
        p.resume_evt.set()
        p.join(0.5)
        self.panic()

    def _song_cut(self, reason):
        """A song that was cut, published as the last song with the reason it stopped."""
        if self.player is not None and not self.alarm and self.current:
            self.pub("piano/play/last_finished", self.current, retain=True)
            self.pub("piano/play/stopped_by", reason, retain=True)

    def _player_done(self, player, result):
        if player is not self.player:
            return                                  # an old player, already replaced
        if result == "Keyboard disconnected":
            # the player's state is still in place here, so the cut song is published with it
            self._lost_keyboard("The keyboard did not take the song", "song_send")
            return
        name = self.current
        was_alarm = self.alarm
        self.player = None
        self.alarm = False
        self.paused = False
        self.current = ""
        if was_alarm:                               # only after 3 failures in a row
            self.log("❌ Alarm failed 3 times in a row, stopping")
            self.panic()
            self._publish_alarm_stopped("Playback error", name)
            return
        if result == "Finished":
            self.log(f"✅ Song finished: {to_display(name)}")
            self.panic()                            # the file may have left the volume down
        else:
            self.log(f"⚡ Song: playback failed: {to_display(name)}")
        self.pub("piano/play/last_finished", name, retain=True)
        self.pub("piano/play/stopped_by", result, retain=True)
        self.pub("piano/play/status", "idle", retain=True)
        self.pub("piano/play/paused", "off", retain=True)
        self.pub("piano/play/current", "unknown", retain=True)
        self.clear("piano/play/duration")
        self._status()

    def _unmark_alarm(self):
        try:
            os.remove(self.alarm_mark)
        except OSError:
            pass

    def _publish_alarm_stopped(self, reason, song):
        self._unmark_alarm()
        self.pub("piano/alarm/status", "off", retain=True)
        self.pub("piano/alarm/stopped_by", reason, retain=True)
        self.pub("piano/play/status", "idle", retain=True)
        self.pub("piano/play/current", "unknown", retain=True)
        self.clear("piano/alarm/set", "piano/play/duration")
        if song:
            self.pub("piano/play/last_finished", song, retain=True)
            self.pub("piano/play/stopped_by", reason, retain=True)
            self.pub("piano/alarm/last_song", song, retain=True)
            self.log(f"⏰ Alarm stopped: {reason} | {to_display(song)}")
        else:
            self.log(f"⏰ Alarm stopped: {reason}")
        self._status()

    def _publish_all_stopped(self, reason, song):
        if song:
            self.pub("piano/play/last_finished", song, retain=True)
            self.pub("piano/play/stopped_by", reason, retain=True)
        self.pub("piano/alarm/status", "off", retain=True)
        self.pub("piano/play/status", "idle", retain=True)
        self.pub("piano/play/paused", "off", retain=True)
        self.pub("piano/play/current", "unknown", retain=True)
        self.clear("piano/play", "piano/alarm/set", "piano/play/duration")
        self.log("✅ Everything stopped")

    def stop_now(self, reason):
        if self.player is None:
            self.log("ℹ️ Stop: nothing is playing")
            return False
        song = self.current
        was_alarm = self.alarm
        self.last_stop = time.monotonic()
        self._end_player()
        self.alarm = False
        self.paused = False
        self.current = ""
        if was_alarm:
            self._publish_alarm_stopped(reason, song)
        else:
            self._publish_all_stopped(reason, song)
            self.log(f"⏹ Stopped: {reason}")
        return True

    def pause_request(self, what):
        if what == "toggle":
            what = "resume" if self.paused else "pause"
        if what == "pause":
            if self.player is not None and self.alarm:
                self.log("ℹ️ Pause: the alarm is not paused")
            elif self.player is None:
                self.log("ℹ️ Pause: no song is playing")
            elif not self.paused:
                self.paused = True
                self.player.pause_evt.set()
                self.pub("piano/play/paused", "on", retain=True)
        elif what == "resume":
            if self.player is None or not self.paused:
                self.log("ℹ️ Resume: no song is paused")
                return
            self.paused = False
            self.player.pause_evt.clear()
            self.player.resume_evt.set()
            self.log("▶ Song resumed")
            self.pub("piano/play/paused", "off", retain=True)
        self._status()

    # ------------------------------------------------------------- the keyboard coming and going
    def _device(self, connected):
        if connected and not self.connected:
            self.connected = True
            self.connect_time = time.time()
            self.pub("piano/status", "online", retain=True)
            self.pub("piano/port", self._device_name(), retain=True)
            self.pub("piano/game/event", "connected", retain=True)
            self.log(f"✅ Keyboard connected: {self._device_name()}")
            self.away_state = ""
            if self.stuck_alerted:
                self.stuck_alerted = False
                self.pub("piano/alert", "The keyboard is back.")
                self.log("🔌 Keyboard recovered from the stuck state")
            self.panic()
            if self.disconnected_at is not None:
                away = duration_words(time.time() - self.disconnected_at)
                self.disconnected_at = None
                self.pub("piano/reconnect/time", time.strftime("%H:%M:%S"), retain=True)
                self.pub("piano/reconnect/downtime", away, retain=True)
                self.log(f"🔌 Keyboard back after {away}")
        elif not connected and self.connected:
            self._lost_keyboard("The keyboard was unplugged", "android_midi")
        self._status()

    def _lost_keyboard(self, reason, source):
        self._disconnect_event(reason, source)
        self.connected = False
        if self.player is not None:
            song, was_alarm = self.current, self.alarm
            p = self.player
            self.player = None
            p.stop_evt.set()
            p.resume_evt.set()
            self.alarm = False
            self.paused = False
            self.current = ""
            if was_alarm:
                self.pub("piano/alarm/status", "off", retain=True)
                self.pub("piano/alarm/stopped_by", "Keyboard disconnected", retain=True)
            self.pub("piano/play/last_finished", song, retain=True)
            self.pub("piano/play/stopped_by", "Keyboard disconnected", retain=True)
        self.buffer = []
        self.away_since = time.monotonic()
        self.away_state = "disconnected"
        self.pub("piano/status", "disconnected", retain=True)
        self.pub("piano/play/status", "idle", retain=True)
        self.pub("piano/play/paused", "off", retain=True)
        self.pub("piano/play/current", "unknown", retain=True)
        self.clear("piano/play/duration")
        self.pub("piano/alarm/status", "off", retain=True)
        self.pub("piano/game/event", "disconnected", retain=True)
        self.log("❌ Keyboard disconnected")
        self._status()

    def _disconnect_event(self, reason, source):
        up = "unknown"
        if self.connect_time:
            up = song_length_words(time.time() - self.connect_time) if time.time() - self.connect_time >= 1 else "0 seconds"
        activity = "idle"
        if self.player is not None and self.alarm:
            activity = f"alarm: {to_display(self.current)}"
        elif self.player is not None:
            activity = f"playing: {to_display(self.current)}"
        self.log(f"❌ Disconnected | reason: {reason} | source: {source} | connected for: {up} | activity: {activity}")
        if self.disconnected_at is None:
            self.disconnected_at = time.time()
        self.pub("piano/disconnect/reason", reason, retain=True)
        self.pub("piano/disconnect/source", source, retain=True)
        self.pub("piano/disconnect/uptime", up, retain=True)
        self.pub("piano/disconnect/activity", activity, retain=True)
        self.pub("piano/disconnect/time", time.strftime("%H:%M:%S"), retain=True)

    def _reset(self, chat):
        """/pianoreset: on a phone there is no USB reset to run as root. The MIDI device is opened
        again and the broker connection is made again, which clears what a reset clears there."""
        if chat:
            self.reply(chat, self.L("🔄 Full reset started. It takes a few seconds",
                                    "🔄 איפוס מלא התחיל. זה לוקח כמה שניות"))
        if self.player is not None:
            self.stop_now("Reset: requested")
        self.pub("piano/status", "resetting", retain=True)
        # as piano_reset.sh: unstick, the device opened again (the USB reset there), unstick once more,
        # then the broker connection and the recorder again
        self.recorder.stop()
        self.unstick(None)
        try:
            self.link.reopen()
        except Exception as e:
            self.log(f"⚠️ Reopening the keyboard failed: {e}")
        time.sleep(1.5)
        there = self.keyboard_there()
        if there != self.connected:
            self._device(there)
        if there:
            self.unstick(None)
        try:
            self.mq.reconnect()
        except Exception:
            pass
        if self.recorder.wanted():
            self.recorder.start()
        self.pub("piano/status", "online" if self.connected else "disconnected", retain=True)
        self.away_state = ""
        self.log("✅ Full reset done")
        if chat:
            if self.connected:
                self.reply(chat, self.L("✅ Full reset done, the keyboard is connected",
                                        "✅ האיפוס המלא הסתיים, המקלדת מחוברת"))
            else:
                self.reply(chat, self.L("⚠️ Full reset done, but the keyboard is not connected",
                                        "⚠️ האיפוס המלא הסתיים, אבל המקלדת לא מחוברת"))

    def unstick(self, chat, test_note=True):
        """piano_unstick.sh: brings a connected but silent keyboard back. A lone F7 closes a SysEx left
        open (a keyboard inside one ignores everything else), GM System On puts every setting back,
        then on 16 channels: controllers reset, volume 100, full expression, pedal up, notes off.
        Last a test note, middle C: hearing it means the keyboard was deaf and is back."""
        if not self.keyboard_there():
            if chat:
                self.reply(chat, self.L("❌ The keyboard is not connected. This is a USB disconnect, not a deaf keyboard",
                                        "❌ המקלדת לא מחוברת. זה ניתוק USB, לא מקלדת חירשת"))
            return False
        steps = [("1. closing an open SysEx", bytes([0xF7]), 0.1),
                 ("2. GM System On", bytes([0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7]), 0.5),
                 ("3. resetting 16 channels",
                  b"".join(bytes([0xB0 | ch, 0x79, 0, 0xB0 | ch, 0x07, 0x64, 0xB0 | ch, 0x0B, 0x7F,
                                  0xB0 | ch, 0x40, 0, 0xB0 | ch, 0x7B, 0]) for ch in range(16)), 0.2)]
        done = []
        for label, data, wait in steps:
            try:
                self.send(data)
            except LinkError:
                self.log(f"❌ Unstick: sending failed at {label}")
                if chat:
                    self.reply(chat, self.L(f"❌ Sending failed ({label}). The USB output is stuck, the keyboard is not the problem",
                                            f"❌ השליחה נכשלה ({label}). יציאת ה-USB תקועה, לא המקלדת"))
                return False
            done.append(label)
            time.sleep(wait)
        if test_note:
            try:
                self.send(bytes([0x90, 0x3C, 0x60]))
                time.sleep(0.8)
                self.send(bytes([0x80, 0x3C, 0x00]))
            except LinkError:
                pass
        self.log("🩹 Unstick: SysEx closed, GM reset, 16 channels reset" + (", test note sent" if test_note else ""))
        if chat:
            self.reply(chat, self.L(
                "🩹 Unstick done:\n" + "\n".join(done) + "\n4. test note, middle C\n\n"
                "Did you hear the note? Yes: the keyboard was deaf and the reset fixed it. No: the problem is before the keyboard, in USB",
                "🩹 השחרור הסתיים:\n1. נסגר SysEx פתוח\n2. GM System On\n3. אופסו 16 ערוצים\n4. תו בדיקה, דו אמצעי\n\n"
                "שמעת את התו? כן: המקלדת הייתה חירשת והאיפוס תיקן. לא: הבעיה לפני המקלדת, ב-USB"))
        return True

    # ------------------------------------------------------------- keys
    def _midi_message(self, m):
        status = m[0] & 0xF0
        if status == 0xB0 and len(m) == 3 and m[1] == 64:
            # by the MIDI standard 64 and above is down; only changes are published
            if m[2] >= 64:
                if not self.pedal_down:
                    self.pedal_down = time.monotonic()
                    self.pedal_hold_sent = False
                    self.pub("piano/pedal", "down")
            elif self.pedal_down:
                self.pedal_down = 0.0
                self.pub("piano/pedal", "up")
            return
        if status != 0x90 or len(m) != 3 or m[2] == 0:
            return                                  # velocity 0 is a key let go
        note = m[1]
        if self.capture and not (self.player is not None and self.alarm):
            # the settings screen is measuring a key (the stop key, the lowest, the highest): this key
            # goes into nothing else
            self.capture = False
            self.captured = (note, time.time())
            self.log(f"🎹 Key measured: {note} ({note_name(note)})")
            return
        # the alarm beats everything, so the stop key stops it even while the screen sounds keys
        if note == self.stop_key and (self.alarm and self.player is not None or not self.screen_active()) \
                and self.stop_key_pressed():
            self.pub("piano/notes", str(note))
            return
        self.pub("piano/notes", str(note))
        now = time.time()
        if now - self.last_note_time > BUFFER_TIMEOUT and self.buffer:
            self.buffer = []
        self.last_note_time = now
        self.buffer.append(note)
        if len(self.buffer) > MAX_BUFFER:
            self.buffer = self.buffer[1:]
        self._on_key()

    def stop_key_pressed(self):
        """True when the key was taken as a stop, so it goes into no shortcut and no learning."""
        name = f"Stop key ({self.stop_key_name})"
        if self.player is not None:
            what = "alarm" if self.alarm else "song"
            self.stop_now(name)
            self.log(f"✅ Stop key {self.stop_key_name} stops the {what}")
            return True
        if time.monotonic() - self.last_stop < STOP_LOCK_SECS:
            return True                             # a second press of the same stop
        self.log(f"ℹ️ Stop key {self.stop_key_name}: nothing is playing")
        return False

    def _on_key(self):
        if self.screen_active() or self.page_open():
            self.buffer = []
            return
        if self.player is not None and self.alarm:
            self.buffer = []                        # keys while the alarm rings are somebody stopping it
            return
        self.last_key_ms = int(time.time() * 1000)
        self.idle_done = False
        if self.learn is not None:
            if not self.learn["armed"]:
                self.learn["armed"] = True
                self.buffer = self.buffer[-1:]      # start clean from the current key
            return
        if self.game_on():
            return
        if time.monotonic() < self.shortcut_lock_until:
            return
        self._load_shortcuts()
        self._match_long()

    def _on_idle(self):
        if self.learn is not None and not self.learn["armed"] \
                and time.time() - self.learn["since"] > LEARN_TIMEOUT:
            lid, chat = self.learn["id"], self.learn["chat"]
            self.learn = None
            self.reply(chat, self.L(f"⌛ Nothing was played, learning {lid} was closed",
                                    f"⌛ לא נוגן כלום, הלמידה של {lid} נסגרה"))
            return
        if not self.buffer:
            return
        if self.player is not None and self.alarm:
            self.buffer = []
            return
        if self.idle_done:
            return
        gap = int(time.time() * 1000) - self.last_key_ms
        if self.learn is not None:
            if gap < LEARN_IDLE_MS:
                return
            lid, chat = self.learn["id"], self.learn["chat"]
            self.learn = None
            self.shortcut_save(lid, " ".join(map(str, self.buffer)), chat)
            self.buffer = []
            self.idle_done = True
            return
        if gap < SHORT_IDLE_MS:
            return
        self.idle_done = True
        if self.screen_active():
            return
        if self.page_open():
            self.buffer = []
            return
        if self.game_on():
            return
        if time.monotonic() < self.shortcut_lock_until:
            return
        self._load_shortcuts()
        self._match_short()

    # ------------------------------------------------------------- /pianostatus for the app's own bot
    def status_text(self):
        """The same message as the /pianostatus answer of Home Assistant's automation."""
        st = self.state
        bad = ("", "unknown", "unavailable", "none")
        he = self.lang == "he"
        playing = self.player is not None
        last_sc = st.get("piano/shortcut", "")
        last_song = st.get("piano/play/last_finished", "")
        song_stop = st.get("piano/play/stopped_by", "")
        alarm_song = st.get("piano/alarm/last_song", "")
        stopped_by = st.get("piano/alarm/stopped_by", "")
        alarm_on = playing and self.alarm
        if he:
            kb = "מחוברת ✅" if self.connected else "לא מחוברת ❌"
            out = ["🎹 מצב הפסנתר", "", f"🔌 מקלדת: {kb}", f"🎼 נגן: {'מנגן 🎶' if playing else 'במנוחה 💤'}"]
            if playing:
                out.append(f"▶️ מתנגן עכשיו: {nice(self.current)}")
                if st.get("piano/play/duration", "") not in bad:
                    out.append(f"⏱️ אורך: {st['piano/play/duration']}")
                if st.get("piano/play/source", "") not in bad:
                    out.append(f"🤖 הופעל על ידי: {st['piano/play/source']}")
            elif last_song not in bad:
                out.append(f"⏮️ השיר האחרון: {nice(last_song)}")
                if song_stop == "Finished":
                    out.append("✅ נוגן עד הסוף")
                elif song_stop not in bad:
                    out.append(f"🛑 נעצר על ידי: {song_stop}")
            out += ["", f"⏰ שעון מעורר: {'מצלצל 🔔' if alarm_on else 'כבוי 🔕'}"]
            if alarm_song not in bad:
                out.append(f"🎵 שיר ההשכמה האחרון: {nice(alarm_song)}")
            if not alarm_on and stopped_by not in bad and not (alarm_song == last_song and stopped_by == song_stop):
                out.append(f"🛑 נעצר על ידי: {stopped_by}")
            out += ["", f"⌨️ הקיצור האחרון: {nice(last_sc) if last_sc not in bad else 'אין'}",
                    f"🕐 עודכן: \u200e{time.strftime('%d/%m %H:%M:%S')}"]
        else:
            kb = "connected ✅" if self.connected else "disconnected ⚠️"
            out = ["🎹 Piano status", "", f"🔌 Keyboard: {kb}", f"🎼 Player: {'playing 🎶' if playing else 'idle 💤'}"]
            if playing:
                out.append(f"▶️ Now playing: {nice(self.current)}")
                if st.get("piano/play/duration", "") not in bad:
                    out.append(f"⏱️ Length: {st['piano/play/duration']}")
                if st.get("piano/play/source", "") not in bad:
                    out.append(f"🤖 Started by: {st['piano/play/source']}")
            elif last_song not in bad:
                out.append(f"⏮️ Last song: {nice(last_song)}")
                if song_stop == "Finished":
                    out.append("✅ Played to the end")
                elif song_stop not in bad:
                    out.append(f"🛑 Stopped by: {song_stop}")
            out += ["", f"⏰ Alarm: {'ringing 🔔' if alarm_on else 'off 🔕'}"]
            if alarm_song not in bad:
                out.append(f"🎵 Last alarm song: {nice(alarm_song)}")
            if not alarm_on and stopped_by not in bad and not (alarm_song == last_song and stopped_by == song_stop):
                out.append(f"🛑 Stopped by: {stopped_by}")
            out += ["", f"⌨️ Last shortcut: {nice(last_sc) if last_sc not in bad else 'none'}",
                    f"🕐 Updated: {time.strftime('%d/%m %H:%M:%S')}"]
        return "\n".join(out)

    # ------------------------------------------------------------- shortcuts
    def _read_shortcuts(self):
        out = []
        try:
            with open(self.shortcuts_file, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line or line.startswith("#") or "|" not in line:
                        continue
                    notes, sid = line.split("|", 1)
                    if notes and sid:
                        out.append((notes.split(), sid))
        except OSError:
            pass
        return out

    def _load_shortcuts(self):
        try:
            st = os.stat(self.shortcuts_file)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            self.shortcuts, self.shortcuts_stamp = [], None
            return
        if stamp == self.shortcuts_stamp:
            return
        self.shortcuts_stamp = stamp
        self.shortcuts = [([int(n) for n in notes if n.isdigit()], sid) for notes, sid in self._read_shortcuts()]
        self.log(f"🔄 {len(self.shortcuts)} shortcuts loaded")

    def _fire(self, sid):
        self.log(f"✅ Shortcut recognized: {to_display(sid)}")
        self.pub("piano/shortcut", sid)
        self.buffer = []
        self.shortcut_lock_until = time.monotonic() + 1

    def _match_long(self):
        buf = self.buffer
        for seq, sid in self.shortcuts:
            n = len(seq)
            if n <= SHORT_MAX or n > len(buf):
                continue
            for i in range(len(buf) - n + 1):
                if buf[i:i + n] == seq:
                    self._fire(sid)
                    return True
        return False

    def _match_short(self):
        for seq, sid in self.shortcuts:
            if len(seq) <= SHORT_MAX and self.buffer == seq:
                self._fire(sid)
                return True
        return False

    def keys_word(self, n, en=False):
        if not en and self.lang == "he":
            return "קליד אחד" if n == 1 else f"{n} קלידים"
        return "1 key" if n == 1 else f"{n} keys"

    def mode_word(self, n):
        if n <= SHORT_MAX:
            return self.L("recognized after 2 seconds of silence", "מזוהה אחרי 2 שניות של שקט")
        return self.L("recognized instantly while playing", "מזוהה מיד תוך כדי נגינה")

    def _write_shortcuts(self, rows):
        tmp = self.shortcuts_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for notes, sid in rows:
                f.write(f"{' '.join(notes)}|{sid}\n")
        os.replace(tmp, self.shortcuts_file)

    def shortcut_save(self, sid, notes_text, chat):
        sid = sid.replace(" ", "_")
        arr = notes_text.split()
        n = len(arr)
        L = self.L
        if not sid or "|" in sid:
            self.reply(chat, L("❌ Invalid name", "❌ שם לא תקין"))
            return False
        if n < 1 or n > MAX_BUFFER:
            self.reply(chat, L(f"❌ A shortcut needs 1 to {MAX_BUFFER} keys", f"❌ קיצור צריך בין 1 ל-{MAX_BUFFER} קלידים"))
            return False
        for k in arr:
            if not k.isdigit() or int(k) > 127:
                self.reply(chat, L(f"❌ Invalid key: {k} (0 to 127)", f"❌ קליד לא תקין: {k} (בין 0 ל-127)"))
                return False
        notes = " ".join(arr)
        if n == 1 and notes == str(self.stop_key):
            self.reply(chat, L(f"❌ Key {self.stop_key} ({self.stop_key_name}) is reserved for stopping the alarm",
                               f"❌ קליד {self.stop_key} ({self.stop_key_name}) שמור לעצירת השעון המעורר"))
            return False
        rows = self._read_shortcuts()
        for r_notes, r_id in rows:
            if " ".join(r_notes) == notes and r_id != sid:
                self.reply(chat, L(f"❌ This sequence already exists as {r_id}", f"❌ הרצף הזה כבר שמור בשם {r_id}"))
                return False
        rows = [r for r in rows if r[1] != sid] + [(arr, sid)]
        self._write_shortcuts(rows)
        if n <= SHORT_MAX:
            test = L("To test: play the keys and wait 2 seconds", "לבדיקה: לנגן את הקלידים ולחכות 2 שניות")
        else:
            test = L("To test: play the keys, it is recognized right away", "לבדיקה: לנגן את הקלידים, והקיצור מזוהה מיד")
        self.log(f"💾 Shortcut saved: {to_display(sid)} ({self.keys_word(n, en=True)})")
        self.reply(chat, L(f"✅ Shortcut {sid} saved", f"✅ הקיצור {sid} נשמר")
                   + f"\n{notes}\n{self.keys_word(n)} · {self.mode_word(n)}\n{test}")
        return True

    def shortcut_delete(self, sid, chat):
        sid = sid.replace(" ", "_")
        rows = self._read_shortcuts()
        if not any(r[1] == sid for r in rows):
            self.reply(chat, self.L(f"❌ No shortcut named {sid}", f"❌ אין קיצור בשם {sid}"))
            return
        self._write_shortcuts([r for r in rows if r[1] != sid])
        self.log(f"🗑️ Shortcut deleted: {to_display(sid)}")
        self.reply(chat, self.L(f"🗑️ Deleted: {sid}", f"🗑️ נמחק: {sid}"))

    def shortcut_list(self, chat):
        rows = self._read_shortcuts()
        if not rows:
            self.reply(chat, self.L("🎹 No shortcuts saved", "🎹 אין קיצורים שמורים"))
            return
        txt = ""
        for notes, sid in rows:
            shown = " ".join(notes[:8]) + (" …" if len(notes) > 8 else "")
            txt += f"\n🔹 {sid}\n{shown}\n{self.keys_word(len(notes))} · {self.mode_word(len(notes))}\n"
        self.reply(chat, self.L(f"🎹 Saved shortcuts: {len(rows)}", f"🎹 קיצורים שמורים: {len(rows)}") + "\n" + txt)

    def midi_list(self, chat, flt):
        names = self.song_names()
        L = self.L
        if not names:
            self.reply(chat, L("🎹 The songs folder is empty", "🎹 תיקיית השירים ריקה"))
            return
        if flt:
            names = [n for n in names if flt.lower() in n.lower()]
        txt = "".join(f"\n🎵 {n}" for n in names)
        if not names:
            self.reply(chat, L(f"🎹 No songs match: {flt}", f"🎹 אין שירים שמתאימים לחיפוש: {flt}"))
        elif flt:
            self.reply(chat, L(f"🔍 Search: {flt}", f"🔍 חיפוש: {flt}") + "\n"
                       + L(f"🎹 Found: {len(names)}", f"🎹 נמצאו: {len(names)}") + "\n" + txt)
        else:
            self.reply(chat, L(f"🎹 Songs: {len(names)}", f"🎹 שירים: {len(names)}") + "\n" + txt)

    def telegram_play(self, chat, name):
        L = self.L
        name = name.rstrip()
        if not name:
            self.reply(chat, L("❌ Missing a song name. Example: /pianoplay ode_to_joy",
                               "❌ חסר שם של שיר. לדוגמה: /pianoplay ode_to_joy"))
            return
        path = self.find_midi(name)
        if not path:
            self.reply(chat, L(f"❌ No song named: {name}. See /pianosongs", f"❌ אין שיר בשם: {name}. הרשימה: /pianosongs"))
            return
        name = os.path.splitext(os.path.basename(path))[0]
        if self.player is not None and self.alarm:
            self.reply(chat, L("⏰ The alarm is playing, the song was not started", "⏰ השעון המעורר מנגן, השיר לא התחיל"))
        elif self.player is not None:
            self.reply(chat, L("🎶 A song is already playing. Stop it first: /pianostop",
                               "🎶 כבר מתנגן שיר. קודם צריך לעצור אותו: /pianostop"))
        elif self.game_on():
            self.reply(chat, L("🎹 A lesson is running, the song was not started", "🎹 יש שיעור פעיל, השיר לא התחיל"))
        elif not self.keyboard_there():
            self.reply(chat, L("🔌 The keyboard is not connected, the song was not started",
                               "🔌 המקלדת לא מחוברת, השיר לא התחיל"))
        else:
            self.pub("piano/play", f"{name}|{'Terminal' if chat.startswith('cli-') else 'Telegram'}")
            self.reply(chat, L(f"▶️ Playing: {name}", f"▶️ מנגן: {name}"))

    def admin_ok(self, args):
        first = args.strip().split(" ", 1)[0] if args.strip() else ""
        return bool(self.admin_secret) and first == f"secret:{self.admin_secret}"

    def handle_shortcut_cmd(self, raw):
        parts = raw.split("|", 2)
        action = parts[0]
        chat = parts[1] if len(parts) > 1 else ""
        args = parts[2] if len(parts) > 2 else ""
        L = self.L
        if action == "list":
            self.shortcut_list(chat)
        elif action == "add":
            sid, _, notes = args.partition(" ")
            self.shortcut_save(sid, notes, chat)
        elif action == "del":
            self.shortcut_delete(args, chat)
        elif action == "learn":
            sid, sep, notes = args.partition(" ")
            if sep and re.fullmatch(r"[0-9 ]+", notes):
                self.shortcut_save(sid, notes, chat)
                return
            sid = args.replace(" ", "_")
            if not sid:
                self.reply(chat, L("❌ A name is needed: /seqlearn name", "❌ צריך שם: /seqlearn ושם הקיצור"))
                return
            if self.page_open():
                self.reply(chat, L(f"🖥️ A lesson screen is open, so a shortcut cannot be learned now. Close it, or use /seqadd {sid} with the notes",
                                   f"🖥️ מסך השיעורים פתוח, ולכן אי אפשר ללמד קיצור עכשיו. צריך לסגור אותו, או להשתמש ב-/seqadd {sid} עם התווים"))
                return
            if self.game_on():
                self.reply(chat, L(f"🎹 A lesson is running, so a shortcut cannot be learned now. Try again after it, or use /seqadd {sid} with the notes",
                                   f"🎹 יש שיעור פעיל, ולכן אי אפשר ללמד קיצור עכשיו. אפשר לנסות שוב אחריו, או להשתמש ב-/seqadd {sid} עם התווים"))
                return
            self.learn = {"id": sid, "chat": chat, "since": time.time(), "armed": False}
            self.log(f"🎓 Learning mode: {to_display(sid)}")
            self.reply(chat, L(f"🎓 Play the sequence for {sid} now\nIt is saved after 5 seconds of silence. Cancel: /seqcancel",
                               f"🎓 עכשיו לנגן את הרצף של {sid}\nהוא נשמר אחרי 5 שניות של שקט. ביטול: /seqcancel"))
        elif action == "cancel":
            if self.learn is not None:
                self.learn = None
                self.reply(chat, L("↩️ Learning canceled", "↩️ הלמידה בוטלה"))
            else:
                self.reply(chat, L("ℹ️ Nothing is being learned", "ℹ️ אין למידה פעילה"))
        elif action in ("midi", "midilist", "songs"):
            self.midi_list(chat, args)
        elif action == "play":
            self.telegram_play(chat, args)
        elif action == "reset":
            if not self.admin_ok(args):
                self.log(f"🚫 Full reset refused, no valid secret (chat {chat})")
                self.reply(chat, L("🚫 That command needs the admin secret from the app's settings",
                                   "🚫 הפקודה הזו דורשת את סוד הניהול מההגדרות של האפליקציה"))
                return
            self.log(f"🔄 Full reset requested from chat {chat}")
            self._reset(chat)
        else:
            self.reply(chat, L(f"❌ Unknown command: {action}", f"❌ פקודה לא מוכרת: {action}"))

    def handle_game_cmd(self, raw):
        """A lesson command while the lesson engine is not up: one plain answer, so a command
        never goes unanswered. The engine answers the rest itself."""
        parts = raw.split("|", 2)
        chat = parts[1] if len(parts) > 1 else ""
        if chat:
            why = lessons.error() if self.lessons_on else (self.cfg.get("engine_error") or "its files are missing")
            if why:
                self.reply(chat, self.L(f"⚠️ The lesson engine is not running: {why}",
                                        f"⚠️ מנוע השיעורים לא פועל: {why}"))
            else:
                self.reply(chat, self.L("⏳ The lesson engine is still starting. Try again in a moment",
                                        "⏳ מנוע השיעורים עוד עולה. כדאי לנסות שוב עוד רגע"))


# ---------------------------------------------------------------------------
# What Kotlin calls
# ---------------------------------------------------------------------------
_bridge = None


def start(config_json, link):
    global _bridge
    if _bridge is not None:
        _bridge.stop()
    _bridge = Bridge(json.loads(config_json), link)
    _bridge.start()


def stop():
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


def midi_in(data):
    if _bridge is not None:
        b = bytes(data)
        hub = _bridge.lesson_hub
        if hub is not None:
            hub.feed(b)                         # the lesson engine reads the keys at once, not after the queue
        _bridge.events.put(("midi", b))


def device_changed(connected):
    if _bridge is not None:
        _bridge.events.put(("device", bool(connected)))


def local_stop(reason):
    if _bridge is not None:
        _bridge.events.put(("stop", str(reason)))


def play_local(song):
    if _bridge is not None:
        _bridge.events.put(("play_local", str(song)))


def telegram_pair(seconds=120):
    if _bridge is not None and _bridge.bot is not None:
        _bridge.bot.pair(int(seconds))
        _bridge._status()
        return True
    return False


def command(line):
    """A line typed on the app's command screen (console.py)."""
    if _bridge is not None:
        console.run(_bridge, str(line))


def record(on):
    """The recorder's switch in the settings, as `armonico record on|off`."""
    b = _bridge
    if b is None:
        return
    b.recorder.set(bool(on))
    b.log("⏺️ MQTT recorder on" if on else "⏹️ MQTT recorder off")
    b._status()


def pause_toggle():
    if _bridge is not None:
        _bridge.events.put(("mqtt", "piano/pause", "toggle"))


def lesson_stop():
    if _bridge is not None:
        _bridge.pub("piano/game/cmd", "lessonstop||")


def capture_key(on=True):
    if _bridge is not None:
        _bridge.events.put(("capture", bool(on)))
        return True
    return False


def live_log(on):
    if _bridge is not None:
        _bridge.events.put(("mqtt", "piano/log/control", "start" if on else "stop"))


def song_list(songs_dir):
    """The songs in a folder with their length in seconds, for the songs screen. Works with no bridge."""
    out = []
    try:
        names = sorted(os.listdir(songs_dir), key=str.lower)
    except OSError:
        names = []
    for f in names:
        base, ext = os.path.splitext(f)
        if ext.lower() not in (".mid", ".midi"):
            continue
        try:
            secs = int(mido.MidiFile(os.path.join(songs_dir, f)).length)
        except Exception:
            secs = 0
        out.append({"name": base, "file": f, "secs": secs})
    return json.dumps(out)


def song_names():
    return json.dumps(_bridge.song_names() if _bridge is not None else [])


def status_json():
    return _bridge.status_json() if _bridge is not None else json.dumps({"broker": False, "keyboard": False})
