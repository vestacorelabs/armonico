"""app.py - the whole Armonico Pi screen, drawn on one Canvas at the display's own resolution.

screen.py keeps what it always did: the lesson engine over HTTP, the rest video, MQTT and the
lesson requests. This App takes the drawing and the input: touches, a mouse, a USB keyboard and
the 4 buttons (over MQTT from armonico_pi.py). Every view is a method view_<name>
in one of the views_*.py mixins; each draws itself and declares its bar, the 4 buttons under the
screen, so a touch on the bar and a press of the real button do the same thing.

Background threads keep slow things off the drawing: the sky (once a minute), the address
(every 10 s), the full diagnosis (every 5 minutes, for the note on the rest screen) and the
update check (diag caches it 6 hours).
"""
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEVICE_DIR = HERE.parent
APP_DIR = DEVICE_DIR.parent
if str(DEVICE_DIR) not in sys.path:                # diag.py and backup.py sit next to screen.py
    sys.path.insert(0, str(DEVICE_DIR))

from . import canvas as canvas_mod                 # noqa: E402
from .canvas import Canvas, Fonts, W, H            # noqa: E402
from .texts import tr as _tr, SCREEN_SAYS, T, ROOT_ERRORS   # noqa: E402
from . import screensize                          # noqa: E402
from .widgets import Widgets, NOTE_NAMES          # noqa: E402
from .views_main import MainViews                  # noqa: E402
from .views_settings import SettingsViews          # noqa: E402
from .views_setup import SetupViews                # noqa: E402
from .views_more import MoreViews                  # noqa: E402
from .views_extra import ExtraViews                # noqa: E402
from .tour import TourViews                        # noqa: E402
from .talk import TalkViews                        # noqa: E402
from .views_wifi import WifiViews                  # noqa: E402
from .views_bt import BtViews                      # noqa: E402
from .views_check import CheckViews                # noqa: E402
from .views_whatsnew import WhatsNewViews          # noqa: E402
from .views_songnet import SongNetViews            # noqa: E402
from .views_power import PowerViews                # noqa: E402
from .views_deep import DeepViews                  # noqa: E402
from .views_game import GameViews                  # noqa: E402
from .views_plex import PlexViews                  # noqa: E402
from . import sky as sky_mod                       # noqa: E402
from .characters import Character                  # noqa: E402
from .sleep import SleepScreen                     # noqa: E402
from .light import DAY_LEVELS, NIGHT_AUTO, NIGHT_STRENGTH, NIGHT_WHEN, night_volume  # noqa: E402

try:
    import diag                                    # device/diag.py
except Exception:                                  # pragma: no cover - the screen still runs without it
    diag = None

canvas_mod.SYMBOLS.update("⇧⌫▸›✔")                 # Heebo has no glyphs for these; DejaVu does

FPS = 30
VIDEO_FPS = 60          # how often the loop looks for a new video frame (see run)
SLEEP_FPS = 15          # the sleep screen is drawn 15 times a second (see run): the breath, the z's and the stars move smoothly ...
SLEEP_POLL = 30         # ... and the input looked at 30 times a second (every second look draws), so a touch wakes it at once
IDLE_FPS = 8            # a still list/settings page nobody touches is drawn only this often (None: always FPS) ...
IDLE_AFTER = 1.0        # ... after this many quiet seconds (no input, event, change of what is drawn)
IDLE_POLL = 60          # ... while the input and the engine are looked at this often, so a touch is answered at once
IDLE_VIEWS = frozenset(("settings", "about", "whatsnew", "songs", "bk", "imp", "net", "wifi", "bt"))
if os.environ.get("ARMONICO_IDLE_FPS") is not None:     # for tests and trials: a number, or 0/off/none for no drop
    _v = os.environ["ARMONICO_IDLE_FPS"].strip().lower()
    IDLE_FPS = int(_v) if _v.isdigit() and int(_v) > 0 else None
BAR_SHOW = 5          # seconds the buttons stay up over the video or the picture after a touch
DEFAULT_CITY = {"name": "Tel Aviv", "country": "Israel", "admin": "", "lat": 32.0809, "lon": 34.7806,
                "tz": "Asia/Jerusalem"}
THEMES = ("classic", "classicLight", "auto", "sky")
CHARS = ("house", "keys", "grand")   # , "metro": the metronome was taken out; a saved "metro" shows the house
RESTS = ("video", "animation", "nasa")
SLEEPS = (0, 1, 5, 15)
# OUTPUTS = ("aux", "hdmi", "both", "off")    # HDMI off (DSI only)
OUTPUTS = ("aux", "off")
STOPS = ("ok", "up", "down", "menu", "key:0")
NEXT = {"rest": "songs", "songs": "free", "free": "game", "game": "settings", "settings": "rest", "place": "settings",
        "about": "settings", "displays": "rest", "setup": "rest", "net": "settings", "job": "settings",
        "edit": "settings", "diag": "settings", "upd": "rest", "credits": "about", "bk": "settings",
        "test": "settings", "imp": "songs", "wifi": "settings", "check": "settings", "bt": "settings",
        "qr": "settings", "plex": "settings"}
LIST_VIEWS = ("settings", "setup", "bk", "diag", "about", "place", "edit", "displays", "job", "net", "upd", "imp", "wifi",
              "check", "whatsnew", "bt", "qr", "songnet")
DIAG_EVERY = 300
DOWN_STALE = 5.0      # seconds: a button held longer than this with no release is taken as released


def log(msg):
    print(msg, flush=True)


STALE_DRAG = 0.8     # seconds: a drag whose finger has not moved this long no longer blocks a new finger

class App(Widgets, MainViews, SettingsViews, SetupViews, MoreViews, ExtraViews, TourViews, TalkViews, WifiViews, BtViews, CheckViews, WhatsNewViews, SongNetViews, PowerViews, DeepViews, GameViews, PlexViews):
    def __init__(self, screen, pg, save_settings, font_dir, background=True):
        self.screen, self.pg, self.save_settings = screen, pg, save_settings
        self.cv = Canvas(pg, pg.display.get_surface(), Fonts(pg, font_dir))
        self.screen_inch = screensize.detect(*pg.display.get_surface().get_size())   # 3.5, 4.3, 5.0, 7.0 or 10.0
        self.t0 = time.monotonic()
        self.st = screen.settings                  # the same dict screen.py reads
        screen.mixer_lock = threading.Lock()       # the test tone and the player opened again (follow_jobs)
        self.hits, self.bar_items = [], []
        self.P = sky_mod.palette("classic", True, None)
        self.confirm = None
        self.view = "rest"
        self.down = {}                             # buttons held now: name -> when
        self.about_wait = {}                       # ☰ or ✓ on About, acted on at release (or a combo)
        self.last_input = time.monotonic()
        self.last_tap = (0.0, None)
        self.swipe = None
        self.mouse_at = 0.0
        self.off_until = 0.0                       # the screen dark: restart or shutdown asked
        self.boot_at = -9.0
        self.oct_at = -9.0
        self.owl_night = ""
        self.free_notes = []
        self.free_lit = (None, 0.0)
        self.seen_flash = {}
        self.ver_taps = []
        self.vcl_taps = []
        self.rec_shown = False                     # the screen recording's card, shown until the next restart (rec_card_on)
        self.soak_taps = []
        self.soak_shown = False                    # the load test's card, shown until the next restart (soak_card_on)
        self.ip = ""
        self.mdns = (diag.mdns_name() if diag else "") or "armonico-pi.local"
        self.checks = []
        self.update = None                         # (version, notes) when a newer release is out
        self.time_adj = 0.0                        # the clock moved by hand in the setup, before it is applied
        self.night_on_now = False
        self.night_cap_now = None
        from . import system
        from .light import Light
        self.light, self.dim, self.shift = Light(system.request), 0.0, (0, 0)
        self.background_on = background            # tests run with no threads and no network (sleep.py's weather)
        self.char = Character()
        self.sky = sky_mod.Sky(HERE / "data")
        self.cities = sky_mod.CityFinder(HERE / "data")
        self.apod = sky_mod.Apod(Path(os.environ.get("DATA_DIR", "/var/lib/armonico")) / "device")
        from .recorder import Recorder
        self.srec = Recorder(Path(os.environ.get("DATA_DIR", "/var/lib/armonico")) / "device" / "recordings")
        self.sky_st = None
        self.card_cache = (0.0, None)
        self.init_settings_state()
        self.init_setup_state()
        self.init_extra_state()
        self.init_more_state()
        self.init_tour_state()
        self.init_talk_state()
        self.init_game_state()
        self.init_plex_state()
        self.init_check_state()
        self.init_power_state()
        self.init_deep_state()
        self.init_whatsnew_state()
        if background:
            self.chk_offer_load()                  # the full check offered once after an update (views_check.py)
            self.wn_load()                         # What's new once after an update, before that offer (views_whatsnew.py)
        screen.say = self.say                      # its messages come out in the screen's language
        self.size_media()
        self.renew_sound_conf()
        if diag:
            try:
                self.ip = diag.ipv4()
            except Exception:
                self.ip = ""
        self.sky_st = self.compute_sky()
        if background:
            for fn in (self.loop_sky, self.loop_net, self.loop_diag):
                threading.Thread(target=fn, daemon=True).start()
        self.first_view()

    # ------------------------------------------------------------------ basics
    @property
    def lang(self):
        return "he" if self.st.get("lang") == "he" else "en"

    def tr(self, key, **kw):
        return _tr(self.lang, key, **kw)

    def L(self, en, he):
        return he if self.lang == "he" else en

    def t(self):
        return time.monotonic() - self.t0

    def save(self, **kw):
        self.st.update(kw)
        self.save_settings(**kw)
        if "lang" in kw:
            # the lesson engine writes the lesson's own lines (part N of M, from memory...):
            # it speaks the device's language too, so a Hebrew screen has no English in it
            try:
                self.screen.engine.post("/settings", {"lang": kw["lang"]})
            except Exception:
                pass

    def setting(self, key, choices, default):
        v = self.st.get(key, default)
        return v if v in choices else default

    @property
    def theme(self):
        return self.setting("theme", THEMES, "classic")

    @property
    def kind(self):
        return self.setting("character", CHARS, "house")

    @property
    def rest_content(self):
        if getattr(self, "tour_step", None) is not None and self.tour_on():
            return "animation"                      # the tour explains the rest screen as the animation shows it
        o = getattr(self.screen, "soak_rest_now", lambda: None)()
        if o:
            return o                                # the load test measures the video and the animation in turn
        v = self.st.get("rest", "animation")
        return "animation" if v == "none" else v if v in RESTS else "animation"

    @property
    def output(self):
        """The sound setting: aux, hdmi, both, off, "usb:<name>" (one USB sound device), "bt:<name>" (a
        Bluetooth one) or "mix" (the
        outputs in sound_mix together)."""
        v = self.st.get("sound_out", "aux")
        if v in OUTPUTS or (isinstance(v, str) and v.startswith(("usb:", "bt:")) and len(v) > 4):
            return v
        return "mix" if v == "mix" and len(self.output_mix) >= 2 else "aux"

    @property
    def output_mix(self):
        m = self.st.get("sound_mix")
        return [k for k in m if isinstance(k, str) and k] if isinstance(m, list) else []

    def output_keys(self):
        """The chosen outputs as a list of keys (aux, hdmi, usb:<name>); off is none. AUX is always one of them:
        it is not a choice in the settings (the speakers are on it, and a cable pulled out is how it goes quiet),
        so a USB or Bluetooth device plays beside it. A setting kept from before with a device alone gets it too."""
        out = self.output
        # return {"both": ["aux", "hdmi"], "off": [], "mix": self.output_mix}.get(out, [out])    # HDMI off (DSI only)
        keys = {"both": ["aux"], "off": [], "mix": self.output_mix}.get(out, [out])
        return keys if not keys or "aux" in keys else ["aux"] + keys

    @staticmethod
    def keys_setting(keys):
        """The setting for a list of chosen outputs: (sound_out, sound_mix). AUX is always in it (output_keys)."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return "off", None
        if "aux" not in keys:
            keys = ["aux"] + keys
        if len(keys) == 1:
            return keys[0], None
        # HDMI off (DSI only):
        # if set(keys) == {"aux", "hdmi"}:
        #     return "both", None
        return "mix", keys

    def output_label(self):
        """The outputs that play now, by name: "USB Headset", "Speaker + USB Headset", Sound on (the jack
        alone: it always plays, so it is never named), or off."""
        if self.output == "off":
            return self.tr("off")
        names = dict(getattr(self, "snd_list", None) or [])
        keys = [k for k in self.output_keys() if k != "hdmi" or self.hdmi_here()] or ["aux"]
        known = self.st.get("snd_names") or {}

        def label(k):
            if k in ("aux", "hdmi"):
                return {"aux": "AUX", "hdmi": "HDMI"}[k]
            if k in names:
                return names[k]
            # chosen but not connected now (Bluetooth headphones switched off, a USB device unplugged): its
            # own kind and the name it had, never "USB" for a Bluetooth device
            from . import audio
            kind = "Bluetooth" if k.startswith(audio.BT) else "USB"
            return self.tr("snd.away", n=known.get(k) or kind)
        named = [label(k) for k in keys if k != "aux"]
        return " + ".join(named) if named else self.tr("snd.on")

    def hdmi_here(self):
        """Whether an HDMI screen is connected, looked at once in 2 seconds (it is read on every frame).
        HDMI off (DSI only): always False, so nothing about HDMI shows or plays anywhere."""
        # HDMI off (DSI only):
        # now = time.monotonic()
        # seen = getattr(self, "_hdmi_seen", None)
        # if seen is None or now - seen[0] > 2:
        #     try:
        #         from . import audio
        #         here = audio.hdmi_connected()
        #     except Exception:
        #         here = False
        #     seen = self._hdmi_seen = (now, here)
        # return seen[1]
        return False

    def outputs_here(self):
        """The sound outputs offered: with no HDMI screen connected, nothing about HDMI shows anywhere."""
        # return OUTPUTS if self.hdmi_here() else ("aux", "off")    # HDMI off (DSI only)
        return ("aux", "off")

    @property
    def output_shown(self):
        """The output as it plays now: HDMI or both kept in the settings play on AUX while no HDMI is there."""
        out = self.output
        if out == "mix" or out.startswith(("usb:", "bt:")):
            return out
        return out if out in self.outputs_here() else "aux"

    @property
    def city(self):
        """The kept city, checked: a place with no usable lat, lon or zone is the default one, and one
        with no name gets its zone's city as a name (city['name'] is shown on every settings, place and
        setup frame, and a city kept without one broke them all)."""
        c = self.st.get("city")
        if not isinstance(c, dict) or not isinstance(c.get("tz"), str) or not c["tz"]:
            return DEFAULT_CITY
        try:
            float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            return DEFAULT_CITY
        if not isinstance(c.get("name"), str) or not c["name"].strip():
            c = dict(c, name=c["tz"].rsplit("/", 1)[-1].replace("_", " "))
        return c

    def online(self):
        return bool(self.ip)

    def now_ms(self):
        return (time.time() + self.time_adj) * 1000

    def local(self, ms=None):
        return sky_mod.local_dt(self.now_ms() if ms is None else ms, self.city.get("tz") or "UTC")

    def hm(self, ms=None):
        return self.local(ms).strftime("%H:%M")

    def say(self, msg, secs=2.5):
        """Messages from screen.py come in English; in Hebrew the known ones are put in Hebrew."""
        if self.lang == "he":
            for pat, he in SCREEN_SAYS:
                m = re.match(pat, msg)
                if m:
                    msg = he.format(*m.groups())
                    break
        self.screen.toast = (msg, time.time() + secs)

    def toast(self, msg, secs=2.2):
        self.screen.toast = (msg, time.time() + secs)

    def root_msg(self, msg):
        """A failure the root unit or a job answered (English, a few known words) in the screen's language;
        one it does not know stays as it came."""
        msg = str(msg or "")
        if msg == "no-root":
            return self.tr("noRoot")
        key = ROOT_ERRORS.get(msg)
        return self.tr(key) if key else msg

    def fail_toast(self, msg, secs=5):
        """A failure from below (a root action, PipeWire) in the screen's language only: its own words come
        in English, so in Hebrew the screen says it in Hebrew and the English detail goes to the log."""
        if msg == "no-root" or msg in ROOT_ERRORS:
            return self.toast(self.root_msg(msg), secs)
        if msg in T:
            return self.toast(self.tr(msg), secs)
        if self.lang == "he" and re.search(r"[A-Za-z]{3}", str(msg or "")):
            log(f"Failed: {msg}")
            return self.toast(self.tr("failed"), secs)
        self.toast(str(msg) if msg else self.tr("failed"), secs)

    # ------------------------------------------------------------------ background
    def compute_sky(self):
        try:
            return self.sky.compute(self.now_ms(), self.city)
        except Exception as e:
            log(f"Sky: {e}")
            return self.sky_st

    def loop_sky(self):
        while True:
            st = self.compute_sky()
            if st is not None:
                self.sky_st = st
            time.sleep(2)

    NET_POLL_OFFLINE = 2       # seconds between two looks for an address while there is none
    NET_POLL_ONLINE = 10

    def loop_net(self):
        """The address, looked up again and again. Without one it is looked for every 2 seconds, so that the weather,
        the date, the picture of the day and the update check start right after the connection came up
        and not up to 10 seconds later (10 is enough once there is an address)."""
        while True:
            time.sleep(self.NET_POLL_ONLINE if self.ip else self.NET_POLL_OFFLINE)
            if diag:
                try:
                    self.ip = diag.ipv4()
                except Exception:
                    self.ip = ""

    def loop_diag(self):
        time.sleep(20)                             # after the start, when the services had time to come up
        while True:
            if diag:
                try:
                    self.checks = diag.run_checks()
                except Exception as e:
                    log(f"Diagnosis: {e}")
                if self.online():
                    try:
                        self.update = diag.update_available()
                    except Exception:
                        pass
            time.sleep(DIAG_EVERY)

    # ------------------------------------------------------------------ views
    def first_view(self):
        """The setup on the first start after the install; the display chooser when two are found."""
        self.view = "rest"
        if self.st.get("setup_step") or not self.st.get("setup_done"):
            self.open_setup(self.st.get("setup_step") or "lang", start=True)
        if self.two_displays():
            # the setup goes on after the pick (a pick that needs no restart landed on Rest, setup not done)
            self.display_from = "setup" if self.view == "setup" else "rest"
            self.go("displays")
        if self.view == "rest":
            self.pl_start()                          # after a power loss: the recovery screen first (views_power.py)

    def next_view(self):
        nxt = NEXT.get(self.view, "rest")
        return nxt if hasattr(self, "view_" + nxt) else NEXT.get(nxt, "rest")

    def go(self, view):
        self.layer_dirty()
        if self.view == "check" and view != "check":
            self.chk_leave()                         # test mode, brightness: back as they were (views_check.py)
        if self.view == "songs" and view != "songs":
            self.songs_leave()                       # a song heard from the list stops with the list (views_main.py)
        elif view != self.view and getattr(self.screen, "song", False) and not getattr(self.screen, "alarm", False) \
                and time.monotonic() - getattr(self, "last_input", 0) < 2:
            # no song goes on behind another screen the player opened, wherever it was started from (the alarm is
            # never stopped here). A change the screen made by itself stops nothing: a lesson that closed after 2
            # minutes with no key stopped a song Home Assistant had started 4 seconds before (load test)
            self.screen.pub("piano/stop", "Armonico Pi: another screen")
        if view != "free" and hasattr(self, "rec"):
            self.rec_leave()                         # the recording and its playback belong to free play
        if view == "settings" and self.view not in ("settings",):
            self.sel = max(0, self.sel)
        self.view = view
        self.sync_screen()

    def screen_view(self):
        """What screen.py should think is in view: the rest video plays only on the rest screen."""
        if self.view == "rest":
            ok = self.rest_content == "video" and self.output != "off" and not self.asleep()
            return "rest" if ok else "free"
        if self.view in ("test", "songs", "free"):
            return self.view
        return "settings" if self.view in LIST_VIEWS else "free"

    def sync_screen(self):
        want = self.screen_view()
        if getattr(self.screen, "view", None) != want:
            self.screen.view = want

    def asleep(self):
        if getattr(self.screen, "soak_rest_now", lambda: None)() in ("video", "animation"):
            # the load test's rest phases measure the rest screen, the video or the animation, as with sleep off.
            # Only the animation's phase used to sleep after the setting's minutes, and then measured the sleep
            # screen under the rest screen's name. Never saved: the setting is as it was after the test
            return False
        mins = self.st.get("sleep", 5)
        mins = mins if mins in SLEEPS else 5
        return (mins > 0 and self.view == "rest" and not self.screen.lesson_on() and not self.confirm
                and time.monotonic() - self.last_input > mins * 60)

    def bar_hidden(self):
        """The buttons under the screen are hidden by the setting (Settings › Look and screen), and no touch has
        brought them up in the last BAR_SHOW seconds. Never in the tour or under a question."""
        return bool(self.st.get("bar_hide")) and time.time() >= getattr(self, "bar_until", 0) \
            and not self.confirm and not self.tour_on()

    def press_bar(self, fn):
        fn()

    def ask(self, text, yes, no=None):
        """A question with Yes and Cancel. no: called on Cancel (the full check's power button step)."""
        self.confirm = {"text": text, "yes": yes, "no": no}     # no timer: it stays until it is answered

    def answer(self, yes):
        c, self.confirm = self.confirm, None
        self.layer_dirty()
        if c and yes:
            c["yes"]()
        elif c and c.get("no"):
            c["no"]()
        elif c:
            self.toast(self.tr("cancelled"), 1.2)

    # ------------------------------------------------------------------ input
    def woke(self):
        """Any input: the first one only wakes a sleeping screen."""
        was = self.asleep()
        self.last_input = time.monotonic()
        self.bump()
        return was

    def pointer_down(self, px, py):
        now = time.monotonic()
        src = "mouse" if getattr(self, "pointer_id", "mouse") == "mouse" else "finger"
        t0, pos, was = (self.last_tap + ("",))[:3]
        if pos and src != was and now - t0 < 0.3 and abs(px - pos[0]) <= 18 and abs(py - pos[1]) <= 18:
            return                                 # the same touch, reported twice (as a finger and as a mouse)
        # Only a finger and a mouse event of one touch are the same press. Two presses from the same
        # source are two presses: the second click on a key within 0.3 s used to be dropped, so quick
        # repeats on a key waited about half a second between notes.
        self.last_tap = (now, (px, py), src)
        self.layer_dirty()
        self.screen.key_hold = False
        if self.woke() or time.time() < self.off_until:
            return
        x, y = self.cv.to_plan(px, py)
        self.swipe = (x, y)
        if self.view == "rest" or self.st.get("bar_hide"):
            self.bar_until = time.time() + BAR_SHOW   # over the video or the picture, or hidden by the setting, a touch brings the buttons up
        if time.time() < self.screen.summary_until:
            self.screen.summary_until = 0
            return
        if self.view == "check" and not self.screen.lesson_on() and self.chk_touch(x, y):
            return                                 # the full check's touch step, as the hardware test's rings
        if self.view == "test" and not self.screen.lesson_on():
            # the touch test: a ring in each corner of the screen, found before any button under it
            for i, (cx, cy) in enumerate(self.touch_targets()):
                if (x - cx) ** 2 + (y - cy) ** 2 <= 34 ** 2:
                    self.screen.test.setdefault("corners", set()).add(i)
                    return
        if not self.confirm and self.tour_on():
            self.tour_tap(x, y)                    # the tour takes every touch: nothing under it is pressed
            return
        if not self.confirm and self.tip_tap(x, y):
            return
        who = getattr(self, "pointer_id", "mouse")
        cur = getattr(self, "drag", None)
        if cur and (time.monotonic() - cur["at"] > 5 or cur.get("view") != self.view
                    or (cur["who"] != who and time.monotonic() - cur.get("moved_at", cur["at"]) > STALE_DRAG)):
            # its finger never reported lifting (a lost FINGERUP), the screen changed under it, or it has not moved
            # for a while: forgotten. Before, every new touch was a "second finger" and pressed nothing for up to
            # 5 s, which read as the touch screen not taking presses (each touch has a finger id of its own)
            cur = self.drag = None
        if cur and cur["who"] != who:
            cur["dead"] = True                       # a second finger during a drag: nothing is pressed, by either
            return
        z = getattr(self, "zone", None)
        if z and not self.confirm and z[0] <= x <= z[0] + z[2] and z[1] <= y <= z[1] + z[3]:
            # in a scrolling area the press waits for the finger to lift: a drag scrolls, a short touch presses
            self.drag = {"who": who, "x": x, "y": y, "hit": self.hit_at(x, y), "z": z, "far": 0.0, "dead": False, "at": time.monotonic(),
                         "view": self.view}
            return
        for hx, hy, hw, hh, fn in reversed(self.hits):
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                fn()
                break
        if self.view == "rest" and not self.confirm:      # About shows the logos now, not the figure
            self.rest_touch(x, y, px, py)            # the figure talks, laughs or spins; a star rings (talk.py)

    def hit_at(self, x, y):
        """The touch area under a plan point as (rectangle, action), or None. The areas are rebuilt every frame, so
        a press is identified by its rectangle, never by the action object."""
        return next((((hx, hy, hw, hh), fn) for hx, hy, hw, hh, fn in reversed(self.hits)
                     if hx <= x <= hx + hw and hy <= y <= hy + hh), None)

    def drag_track(self, px, py):
        """A finger or the mouse moving with the button down: how far a press in a scrolling area has traveled,
        so one that moved and came back is still a scroll, never a press."""
        d = getattr(self, "drag", None)
        if d and d["who"] == getattr(self, "pointer_id", "mouse"):
            x, y = self.cv.to_plan(px, py)
            d["far"] = max(d["far"], abs(x - d["x"]), abs(y - d["y"]))
            d["dy"] = y - d["y"]
            d["moved_at"] = time.monotonic()
            if d["dead"] or abs(x - d["x"]) > max(14, abs(y - d["y"])):
                return                               # a second finger, or a sideways swipe: no scroll
            # the list follows the finger while it moves, a row each time the finger passes one, instead of
            # one jump of many rows when it lifts
            want = int(-(y - d["y"]) / d["z"][4]) if abs(y - d["y"]) >= 14 else 0
            if want != d.get("moved", 0):
                d["z"][5](want - d.get("moved", 0))
                d["moved"] = want
                self.bump()

    def touch_targets(self):
        """The touch test's 4 rings, in the screen's real corners (the plan may not reach them on a wide screen)."""
        cv, m = self.cv, 30
        return [(cv.vx + m, cv.vy + m), (cv.vx + cv.vw - m, cv.vy + m),
                (cv.vx + m, cv.vy + cv.vh - m), (cv.vx + cv.vw - m, cv.vy + cv.vh - m)]

    BACK_LABELS = ("back", "cancel", "w.prev", "exit", "later")

    def mouse_back(self):
        """The right mouse button: the screen's own back (Back, Cancel, Previous...), or nothing."""
        if self.woke() or time.time() < self.off_until:
            return
        if self.confirm:
            self.answer(False)
            return
        if self.tour_on():
            self.tour_end()                        # the right button is Back: on the tour, Skip
            return
        labels = {self.tr(k) for k in self.BACK_LABELS}
        for _, label, fn in self.bar_items:
            if fn and label in labels:
                self.press_bar(fn)
                return

    WHEEL_GAP = 0.09      # seconds: at most one step of the list for each notch, however many events it sends
    WHEEL_IDLE = 0.35     # a pause this long starts the count of a notch again

    def mouse_wheel(self, d):
        """The wheel moves like ▼ and ▲ where they move through a list or pages (settings, songs,
        About). On the rest screen and in free play ▼▲ change the octave, so the wheel does nothing there.
        d: the turn as SDL gives it, positive down; a fraction of a notch on a fine wheel or a touchpad.
        One step for each whole notch, and at most one every WHEEL_GAP: a wheel that sends a notch as several
        events (a fine wheel, a touchpad, the screen's driver reading it twice) moved the list two or three
        rows at once, jumping."""
        now = time.monotonic()
        acc = getattr(self, "_wheel_acc", 0.0)
        if now - getattr(self, "_wheel_at", 0.0) > self.WHEEL_IDLE or (acc and (acc > 0) != (d > 0)):
            acc = 0.0                               # a new turn, or the other way: counted from 0
        self._wheel_at = now
        acc += d
        if abs(acc) < 1 - 1e-6:
            self._wheel_acc = acc
            return
        self._wheel_acc = 0.0                       # what is left of a fast turn is not kept for later
        if now - getattr(self, "_wheel_step", 0.0) < self.WHEEL_GAP:
            return
        self._wheel_step = now
        if self.woke() or self.confirm or self.screen.lesson_on() or self.view in ("rest", "free", "test"):
            return
        self.press("down" if acc > 0 else "up")

    def pointer_up(self, px, py):
        who = getattr(self, "pointer_id", "mouse")
        getattr(self, "_sliding", set()).discard(who)
        self.release_touch_key(who)
        drag = getattr(self, "drag", None)
        if drag and drag["who"] != who:
            return                                   # another finger lifting: the drag goes on
        self.drag = None
        if drag:
            x, y = self.cv.to_plan(px, py)
            dx, dy = x - drag["x"], y - drag["y"]
            far = max(drag["far"], abs(dx), abs(dy))
            if drag["dead"]:
                self.swipe = None
                return
            if abs(dx) > max(14, abs(dy)) and far >= 14:
                drag = None                          # a sideways swipe: the screen's own (About's pages); nothing pressed
            else:
                if far < 14:                         # a tap with a little jitter, lifted over the card it began on
                    now = self.hit_at(x, y)             # looked up again: the frames in between made new actions
                    if drag["hit"] and now and now[0] == drag["hit"][0]:
                        now[1]()
                elif abs(dy) >= 14:
                    # what the finger moved past half a row more than the rows already followed it (drag_track)
                    z, moved = drag["z"], drag.get("moved", 0)
                    rest = int(round(-dy / z[4])) - moved
                    if not moved and not rest:
                        rest = 1 if dy < 0 else -1      # a short drag still moves one row
                    if rest:
                        z[5](rest)
                self.swipe = None                    # moved far, or back where it began: only a scroll, never a press
                return
        if self.swipe is None:
            return
        x0, _ = self.swipe
        self.swipe = None
        x, _ = self.cv.to_plan(px, py)
        dx = x - x0
        if self.view == "about" and abs(dx) >= 50 and not self.confirm:
            # in Hebrew the next page comes from the left, as the dots run
            nxt = dx > 0 if self.lang == "he" else dx < 0
            self.about_turn(1 if nxt else -1)

    def type_text(self, s):
        self.woke()
        if self.view == "edit" and self.edit:
            if self.edit.get("digits"):
                s = "".join(c for c in s if c.isdigit())[:max(0, self.pad_max(self.edit) - len(self.edit["val"]))]
            self.edit["val"] += s
        elif self.view == "place":
            self.place_type(s)

    def type_key(self, key):
        pg = self.pg
        self.woke()
        if self.view == "edit" and self.edit:
            if key == pg.K_BACKSPACE:
                self.edit["val"] = self.edit["val"][:-1]
            elif key in (pg.K_RETURN, pg.K_KP_ENTER):
                self.edit_done()
            elif key == pg.K_ESCAPE:
                self.edit_cancel()
        elif self.view == "place":
            if key == pg.K_BACKSPACE:
                self.place_type(None)
            elif key in (pg.K_RETURN, pg.K_KP_ENTER):
                if self.cities.results:
                    self.place_pick(self.cities.results[0])
            elif key == pg.K_ESCAPE:
                self.query = ""
                self.cities.search("", self.lang, self.online())

    # the Israeli standard layout: the letter on each US key (Hebrew letters do not change with Shift)
    HEBREW = dict(zip("qwertyuiopasdfghjkl;'zxcvbnm,./", "/'קראטוןםפשדגכעיחלךף,זסבהנמצתץ."))

    def kb_lang(self):
        """The language of the typing keyboard now: "he" or "en", the same one the on-screen keyboard shows."""
        if self.view == "edit" and self.edit:
            return "he" if self.edit.get("kb") == "he" else "en"
        if self.view == "place":
            return "he" if self.kb == "he" else "en"
        return "en"

    def kb_toggle(self):
        """Left Shift + left Alt: the keyboard changes between Hebrew and English, the physical one and the
        one on the screen together (their letters, and the EN / עב key)."""
        if self.view == "edit" and self.edit:
            self.edit["kb"] = "en" if self.edit.get("kb") == "he" else "he"
            self.sym, self.caps = False, False
        elif self.view == "place":
            self.kb = "en" if self.kb == "he" else "he"
        else:
            return
        self.woke()

    SHIFTED = dict(zip("1234567890-=[];'\\,./`", '!@#$%^&*()_+{}:"|<>?~'))

    def key_fallback(self, e):
        """A USB keyboard on a screen with no desktop (straight on KMS) sends key presses but, with no
        console to translate them, no text: SDL's TEXTINPUT never comes and nothing could be typed. The
        letter is made from the key here (a US layout) and typed by flush_typed only when no TEXTINPUT came
        with it, so a system that does send text types each letter once."""
        pg = self.pg
        if self.view not in ("edit", "place"):
            return
        if e.mod & (pg.KMOD_CTRL | pg.KMOD_ALT | pg.KMOD_META):
            return
        if self.kb_lang() == "he":
            name = pg.key.name(e.key)
            ch = self.HEBREW.get(name, name if len(name) == 1 and not name.isalpha() else
                                 name[1] if len(name) == 3 and name[0] == "[" else "")
            if name == "space":
                ch = " "
            if ch:
                self.type_text(ch)
            return
        ch = getattr(e, "unicode", "") or ""
        if not (len(ch) == 1 and ch.isprintable()):
            name = pg.key.name(e.key)
            ch = " " if name == "space" else name[1] if len(name) == 3 and name[0] == "[" else \
                name if len(name) == 1 else ""
            if ch.isalpha():
                ch = ch.upper() if bool(e.mod & pg.KMOD_SHIFT) != bool(e.mod & pg.KMOD_CAPS) else ch
            elif ch and e.mod & pg.KMOD_SHIFT and not name.startswith("["):
                ch = self.SHIFTED.get(ch, ch)
        if ch:
            self._pending_text = getattr(self, "_pending_text", []) + [ch]

    def flush_typed(self):
        """After a batch of events: the letters made from key presses are typed when the batch had no
        TEXTINPUT of its own."""
        pending, got = getattr(self, "_pending_text", []), getattr(self, "_batch_text", False)
        self._pending_text, self._batch_text = [], False
        if pending and not got:
            for ch in pending:
                self.type_text(ch)

    def button(self, name, hold):
        """One of the 4 buttons, from MQTT: press, or hold after 0.8 s."""
        self.screen.key_hold = False
        self.layer_dirty()
        if not hold:
            self.down[name] = time.monotonic()
        if self.woke() or time.time() < self.off_until:
            return
        if time.time() < self.screen.summary_until:
            self.screen.summary_until = 0
            return
        if self.confirm:
            if not hold:                           # the hold of the press that asked is not an answer
                if name == "ok":
                    self.answer(True)
                elif name == "menu":
                    self.answer(False)
            return
        if self.screen.lesson_on():
            if name == "menu" and hold:
                self.leave_lesson()
            return
        if self.tour_on():
            self.tour_button(name, hold)           # ✓ next, ☰ skip; nothing under the tour is pressed
            return
        self.tip = None
        if self.view == "check" and self.chk_button(name, hold):
            return                                 # the full check's buttons step counts the press
        if self.view == "test":
            self.screen.button(name, hold)
            if self.screen.view != "test":
                self.go("settings")
            return
        if self.view == "credits":
            if not hold:
                self.go("about")
            return
        if self.view == "about" and name in ("menu", "ok"):
            if not hold:
                self.about_wait[name] = time.monotonic()
            return
        if hold:
            # the settings' page counts only in the settings: left on a sub-page by the setup or the update,
            # it kept ☰ held from working in Songs and Free play
            if name == "menu" and (self.view in ("songs", "free", "game") or (self.view == "settings" and self.page == "top")):
                self.go("rest")
            elif name == "ok" and self.view == "songs" and not getattr(self, "sn_entry", False) \
                    and not getattr(self, "imp_entry", False):
                self.songs_play()                  # not on Search online (views_songnet.py)
            elif name == "ok" and self.view == "free":
                self.rec_toggle()
            return
        self.press(name)

    def press(self, name):
        icon = {"menu": "☰", "down": "▼", "up": "▲", "ok": "✓"}.get(name)
        for ic, _, fn in self.bar_items:
            if ic == icon and fn:
                fn()
                return

    def button_up(self, name):
        self.down.pop(name, None)
        t = self.about_wait.pop(name, None)
        if t is not None and self.view == "about":
            self.press(name)

    def follow_buttons(self):
        """☰ and ✓ held together for 1.5 s on About: the credits. A single one acts on release,
        or after 2 s when the release never comes (an older armonico_pi without release events)."""
        now = time.monotonic()
        # a press whose release never came (QoS 0 drops one now and then) is forgotten after a while: kept,
        # it waited there and opened the credits on About by itself at a later press of the other button
        for name, at in list(self.down.items()):
            if now - at > DOWN_STALE:
                self.down.pop(name, None)
        if self.view == "about" and "menu" in self.down and "ok" in self.down:
            if now - max(self.down["menu"], self.down["ok"]) >= 1.5:
                self.about_wait.clear()
                self.down.clear()
                self.open_credits()
                return
        for name, at in list(self.about_wait.items()):
            other = "ok" if name == "menu" else "menu"
            if now - at > 2.0 and other not in self.down:
                self.about_wait.pop(name)
                self.down.pop(name, None)
                if self.view == "about":
                    self.press(name)

    def soak_rest_from(self, d):
        """The rest screen the load test asks for now (its strip's "rest", while it runs), for 3 minutes past its
        last beat. A change stops what plays, so the rest screen starts again with the other one."""
        sc = self.screen
        want, until = None, 0
        if isinstance(d, dict) and d.get("state") in ("start", "run") and d.get("rest") in ("video", "animation"):
            try:
                want, until = d["rest"], float(d.get("beat", 0)) + 180
            except (TypeError, ValueError):
                want = None
        if want != getattr(sc, "soak_rest", None):
            media = getattr(sc, "media", None)
            if media is not None and getattr(media, "playing", None) is not None:
                media.stop()
        sc.soak_rest, sc.soak_rest_until = want, until

    def mqtt_event(self, topic, payload):
        if topic == "piano/device/soak":           # armonico-soak.py, the load test: its strip at the top
            try:
                self.soak = json.loads(payload) if payload else None
            except ValueError:
                self.soak = None
            self.soak_rest_from(self.soak)
            self.layer_dirty()
            return
        if topic == "piano/device/rec":            # armonico screen-rec on|off, from a terminal
            if payload == "on" and not self.srec.on:
                self.screen_rec_toggle()
            elif payload == "off" and self.srec.on:
                self.screen_rec_stop()
            return
        if topic == "piano/device/button":
            name, _, action = payload.partition(":")
            if name == "power":
                self.power_button()
            else:
                self.button(name, action == "hold")
        elif topic == "piano/device/button/up":
            self.button_up(payload)
        elif topic == "piano/device/power":
            self.power_button()
        elif topic == "piano/notes":
            self.rec_note(payload)
            if self.view == "game":
                self.game_note(payload)               # judged at the time it came (views_game.py)
        elif topic == "piano/device/test/key":
            if self.view == "test" and payload.isdigit() and int(payload) < 13:
                self.screen.test["keys"].add(int(payload))
                self.screen.test.setdefault("at", {})[int(payload)] = time.time()   # which key, 12 being the high C
            elif self.view == "check" and payload.isdigit() and int(payload) < 13:
                self.chk_key(int(payload))
        elif topic == "piano/device/test/usb":
            try:
                u = json.loads(payload)
                note = int(u.get("note"))
            except (ValueError, TypeError, AttributeError):
                return
            if self.view == "test" and 0 <= note <= 127:
                cur = self.screen.test.setdefault("usb", {"name": "", "note": None, "n": 0})
                cur.update(name=str(u.get("name") or ""), note=note, n=cur.get("n", 0) + 1)
            elif self.view == "check" and 0 <= note <= 127:
                self.chk_usb(str(u.get("name") or ""), note)
        elif topic == "piano/device/voice":
            if payload.isdigit():
                self.voice_now = int(payload)
        elif topic == "piano/device/keyboards":
            try:
                found = json.loads(payload)
            except ValueError:
                found = []
            self.keyboards = [str(x) for x in found] if isinstance(found, list) else []
        else:
            if topic == "connected":
                self._keys_sent = self._kb_sent = self._test_sent = None   # sent again on the new connection
            self.screen.handle(topic, payload)

    def power_button(self):
        if self.view == "check" and self.chk_power():
            return                                 # the full check's step: the question, with a Yes that shuts nothing down
        self.woke()
        self.ask(self.tr("shutQ"), lambda: self.root_power("shutdown"))

    def root_power(self, action):
        from . import system
        self.off_until = time.time() + 120
        self.screen.media.stop()

        def go():
            ok, msg = system.request(action, timeout=30)
            if not ok:
                self.off_until = 0
                self.boot_at = self.t()
                self.fail_toast(msg)
        threading.Thread(target=go, daemon=True).start()

    def leave_lesson(self):
        self.ask(self.tr("leaveQ"), lambda: (self.screen.engine.post("/lessonstop"), self.say("Leaving the lesson")))

    # ------------------------------------------------------------------ keys, night, owl
    def follow_keys_ui(self):
        t = self.t()
        for pc, at in self.screen.flash.items():
            if at > self.seen_flash.get(pc, 0):
                self.seen_flash[pc] = at
                self.woke()
                self.char.key(pc, t)
                if self.view == "rest":
                    self.talk_key(pc, t)
                elif self.view == "game" and self.screen.mqtt is None:
                    self.game_key(pc, t)                # no broker: the key as /state lists it, a little late
                self.free_lit = (pc, t)
                self.free_notes = (self.free_notes + [(pc, self.screen.octave)])[-8:]      # named when drawn: see free_line
                hour = self.local().hour
                night = self.local().strftime("%Y-%m-%d")
                if hour == 3 and self.owl_night != night and self.view == "rest":
                    self.owl_night = night
                    self.char.start_owl(t)

    def free_label(self, pc, octave=None):
        """A key played in free play, named as the lessons name keys (1 2 3, C D E or Do Re Mi, chosen on
        the lesson screen; the lesson engine works the names out). With no labels chosen, or before
        the engine ever answered, the note's name with its octave, as before (G4). The last answer of the engine
        is kept, so a poll that came back empty does not turn the names into G4 for a moment."""
        st = self.screen.engine.state or {}
        if isinstance(st.get("device_key_labels"), list):
            self._free_names = (st.get("device_labels"), st["device_key_labels"])
        mode, names = getattr(self, "_free_names", (None, None))
        if mode != "none" and isinstance(names, list) and pc % 12 < len(names) and names[pc % 12]:
            return "8" if pc == 12 and mode == "num" else str(names[pc % 12])
        return NOTE_NAMES[pc % 12] + str((self.screen.octave if octave is None else octave) + pc // 12)

    def free_line(self):
        """The notes played in free play as one line, every one named in the way the lessons name keys now, so a change
        of that choice (on the lesson screen, in the browser, or with another profile) changes the whole line
        and it never mixes numbers, letters and Do Re Mi."""
        return "  ".join(self.free_label(pc, octave) for pc, octave in self.free_notes)

    def night_on(self):
        """The night mode switch. From before it existed, the one choice (night_when, or on and off before that)
        said it: anything but off was on."""
        v = self.st.get("night_on")
        if isinstance(v, bool):
            return v
        w = self.st.get("night_when")
        if w in NIGHT_WHEN:
            return w != "off"
        return bool(self.st.get("night", False))

    def night_auto(self):
        """What turns it on while the switch is on: "sun" (sunset to sunrise), "hours" (between 2 chosen times) or
        "manual" (until the switch is turned off). From before it, the old choice said it, and off kept sunset."""
        v = self.st.get("night_auto")
        if v in NIGHT_AUTO:
            return v
        return "hours" if self.st.get("night_when") == "hours" else "sun"

    def night_when(self):
        """The two together: "off" while the switch is off, else "sun", "hours" or "manual"."""
        return self.night_auto() if self.night_on() else "off"

    def night_level(self):
        """The strength, 1 to 3 (NIGHT_STRENGTH). From before it, the nearest one to the old Screen at night."""
        v = self.st.get("night_level")
        if v in (1, 2, 3):
            return v
        return {25: 3, 10: 3, 0: 3}.get(self.st.get("night_screen", 100), 2)

    def night_hours(self):
        """The chosen hours as minutes since midnight: (from, to)."""
        def minutes(key, default):
            v = self.st.get(key, default)
            return v if isinstance(v, int) and 0 <= v < 1440 else default
        return minutes("night_from", 22 * 60 + 30), minutes("night_to", 6 * 60 + 30)

    def follow_alarm_volume(self):
        """The alarm always rings at 100%, day or night. The volume before it is kept in the settings (so a power cut
        while it rings does not leave the device at 100%) and comes back when the alarm stops."""
        ringing = bool(getattr(self.screen, "alarm", False))
        vol = self.screen.engine.state.get("volume")
        if ringing:
            if not getattr(self, "alarm_loud", False) and vol is not None:
                self.alarm_loud = True
                if not isinstance(self.st.get("alarm_volume_before"), int):
                    self.save(alarm_volume_before=vol)
                if vol != 100:
                    self.set_volume(100)
            return
        before = self.st.get("alarm_volume_before")
        if getattr(self, "alarm_loud", False) or isinstance(before, int):
            self.alarm_loud = False
            if isinstance(before, int):
                self.save(alarm_volume_before=None)
                self.set_volume(before)

    def follow_night(self):
        """Night mode: while it is active the volume is the strength's fixed volume (never above the day volume); the day volume
        comes back when it ends, and follows a new strength chosen meanwhile. A day volume still kept when night
        mode is not active (the power was cut at night, and the device started in the day) comes back too: the
        night volume was left in the engine and stayed for the whole day. While the alarm rings, the alarm's own
        volume (follow_alarm_volume) holds, and night mode waits."""
        if self.night_when() == "sun" and self.sky_st is None:
            return
        night = self.is_night()
        vol = self.screen.engine.state.get("volume")
        fixed = NIGHT_STRENGTH[self.night_level() - 1][1]
        if getattr(self, "alarm_loud", False):
            return
        if night:
            if vol is not None and (not self.night_on_now or self.night_cap_now != fixed):
                self.night_on_now, self.night_cap_now = True, fixed
                saved = self.st.get("night_day_volume")
                if not isinstance(saved, int):
                    self.save(night_day_volume=vol)
                    saved = vol
                target = night_volume(saved, fixed)
                if target != vol:
                    self.set_volume(target)
        elif self.night_on_now or isinstance(self.st.get("night_day_volume"), int):
            self.night_on_now, self.night_cap_now = False, None
            back = self.st.get("night_day_volume")
            if isinstance(back, int):
                self.save(night_day_volume=None)
                self.set_volume(back)

    def follow_input(self):
        """The keys on or off for armonico_pi.py (retained, so it holds after a restart of either).
        On the hardware test they always play: the test is how the keys are checked. And which keyboard
        plays: the device's keys, a USB MIDI keyboard or both (piano/device/keyboard/set)."""
        mode = self.play_input()
        want = "off" if mode in ("touch", "off") and self.view != "test" else "on"
        if want != getattr(self, "_keys_sent", None):
            self._keys_sent = want
            self.screen.pub("piano/device/keys/set", want, retain=True)
        # the hardware test: the keys say which key, and a USB keyboard is heard by the test alone;
        # the full check's guided steps: the same, and the keys play nothing (quiet)
        test = "on" if self.view == "test" else "quiet" if self.chk_testing() else "off"
        if test != getattr(self, "_test_sent", None):
            self._test_sent = test
            self.screen.pub("piano/device/test", test, retain=True)
        kb = json.dumps(self.keyboard_choice(), sort_keys=True)
        if kb != getattr(self, "_kb_sent", None):
            self._kb_sent = kb
            self.screen.pub("piano/device/keyboard/set", kb, retain=True)

    def is_night(self):
        when = self.night_when()
        if when == "sun":
            st = self.sky_st
            return st is not None and st.sun.altitude < -0.833
        if when == "hours":
            a, b = self.night_hours()
            now = self.local()
            m = now.hour * 60 + now.minute
            return a <= m < b if a < b else (a != b and (m >= a or m < b))
        return when == "manual"

    def light_level(self):
        """The screen's level now: the day level, or while night mode is active the strength's
        brightness when it is lower."""
        if getattr(self, "chk_light", None) is not None:
            return self.chk_light                    # the full check's brightness step, never saved
        day = self.st.get("brightness", 100)
        day = day if day in DAY_LEVELS else 100
        if not self.is_night():
            return day
        return min(day, NIGHT_STRENGTH[self.night_level() - 1][0])

    def follow_light(self):
        self.dim = self.light.want(self.light_level())

    def follow_shift(self):
        """Against a picture left in the LCD (image retention) when the screen never sleeps: the rest
        screen moves by 1 pixel every 3 minutes around a small square, so the clock, the hills and the
        buttons do not sit on the same pixels for days. The sleep screen drifts too, a few pixels
        around a slow loop of 10 minutes, a step at most every half minute."""
        if self.asleep():
            a = 2 * math.pi * (int(time.time() // 30) * 30 % 600) / 600
            self.shift = (int(round(4 * math.cos(a))), int(round(3 * math.sin(2 * a))))
            return
        if self.st.get("sleep", 5) != 0 or self.view != "rest" or self.screen.lesson_on():
            self.shift = (0, 0)
            return
        steps = ((0, 0), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
        self.shift = steps[int(time.time() // 180) % len(steps)]

    def finish_frame(self):
        """The last touches on a frame: the move against retention, then the darkening when the
        brightness cannot be set on the screen itself."""
        cv = self.cv
        self.follow_shift()
        dx, dy = self.shift
        if dx or dy:
            k = max(1, round(cv.scale))
            cv.surf.scroll(dx * k, dy * k)
        if self.dim > 0:
            with cv.physical():
                cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (0, 0, 0, int(255 * self.dim)))

    def renew_sound_conf(self):
        """A start after a screen change: the sound is set again with the screen (ARMONICO_REPLUG)."""
        replug = os.environ.pop("ARMONICO_REPLUG", "")
        if not replug:                               # in the device's session: a file (screen.replug_mark)
            try:
                replug = (Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "armonico-replug").read_text()
            except OSError:
                replug = ""
        try:
            self._restarted_at = float(replug)       # when the last start for a screen change was
        except ValueError:
            self._restarted_at = 0.0

    def follow_plugs(self):
        """A screen plugged in or out. KMS does not set a returning screen up again by itself, and
        the Pi's HDMI sound only plays while its screen is lit, so the screen program starts again
        (it lights the screens as they are now) and the HDMI sound with it. Seen twice in a row,
        2 seconds apart, so a cable that is still going in counts once."""
        now = time.monotonic()
        if not diag or now - getattr(self, "_plug_at", 0.0) < 2:
            return                                   # under labwc too: the way the screens are lit may change
        self._plug_at = now
        try:
            seen = tuple(sorted(d["name"] for d in diag.displays() if d.get("connected")))
        except Exception:
            return
        first = getattr(self, "_plugs", None)
        if first is None:
            self._plugs, self._plug_new = seen, None
            return
        if seen == first:
            self._plug_new = None
            return
        if self._plug_new != seen:
            self._plug_new = seen
            return
        if time.time() - getattr(self, "_restarted_at", 0.0) < 10:
            return                                   # once in 10 seconds at most; the change is acted on after
        log_ = getattr(sys.modules.get(type(self.screen).__module__), "log", print)
        log_(f"Screens changed: {', '.join(first) or 'none'} → {', '.join(seen) or 'none'}. Starting again")
        restart = getattr(self.screen, "restart_self", None)
        if restart:
            restart()
        self._plugs = seen

    def mixer_ready(self):
        """The test tone's player, opened now when it was not (the output was busy or just changed)."""
        if self.pg.mixer.get_init():
            return True
        try:
            self.pg.mixer.init()
            return True
        except self.pg.error:
            return False

    def volume_now(self):
        """The volume as last set here for 3 seconds, then as the engine reports it. The engine's
        state comes every 0.1 s and still holds the old value for a moment after a press, which
        made the number jump back and the next press start from the wrong place."""
        v, at = getattr(self, "vol_set", (None, 0.0))
        if v is not None and time.time() - at < 3:
            return v
        vol = self.screen.engine.state.get("volume")
        return 80 if vol is None else vol

    def set_volume(self, vol):
        vol = max(0, min(100, int(vol)))
        self.vol_set = (vol, time.time())
        self.screen.engine.state["volume"] = vol
        self.screen.engine.post("/volume", {"value": vol})
        self.screen.media.set_volume(vol)

    def octave_move(self, d):
        s = self.screen
        s.pub("piano/device/octave/set", f"{d:+d}")
        s.octave = max(1, min(7, s.octave + d))
        self.oct_at = self.t()
        self.char.octave(d, self.t())

    # ------------------------------------------------------------------ displays and media size
    GPU_FROM_W = 1280      # a screen wider than this shows the rest video through the GPU (screen.DisplayVideo)

    def gpu_video_wanted(self):
        # HDMI off (DSI only): reached only under labwc with an HDMI screen, which device/session.sh no longer starts.
        """The rest video stretched by the GPU: a screen wider than GPU_FROM_W opened through SDL's renderer under labwc
        (device/session.sh runs a big HDMI screen alone under labwc for this), and it has not failed before."""
        try:
            wayland = self.pg.display.get_driver() == "wayland"
        except Exception:
            wayland = False
        mod = sys.modules.get(type(self.screen).__module__)
        return (wayland and bool(getattr(mod, "SCALED_DISPLAY", False)) and hasattr(mod, "open_video_output")
                and not getattr(self, "_gpu_failed", False) and self.cv.surf.get_width() > self.GPU_FROM_W)

    # Two screens off (DSI only): not called (size_media)
    def mpv_both_wanted(self):
        """Both screens under labwc (device/session.sh's "both" plan): the video plays in mpv on each."""
        try:
            wayland = self.pg.display.get_driver() == "wayland"
        except Exception:
            wayland = False
        return wayland and bool(os.environ.get("ARMONICO_MIRROR"))

    def gpu_video_ok(self):
        """The rest video can go to the GPU's window now: the video is on, in the GPU's way, and nothing else is
        drawn over it (buttons, a question, a message, the octave, a tip, the tour, a recording of the screen)."""
        s = self.screen
        media = s.media
        msg, until = getattr(s, "toast", ("", 0.0))
        return bool(getattr(media, "gpu", False) and getattr(self, "covered", False) and self.rest_content == "video"
                    and self.view == "rest" and not self.asleep() and not self.confirm
                    and time.time() >= getattr(self, "bar_until", 0) and not (msg and until > time.time())
                    and self.t() - self.oct_at > 1.6 and not self.srec.on and not getattr(self, "rec_starting", False)
                    and not self.tour_on() and not s.lesson_on() and time.time() >= s.summary_until)

    # Two screens off (DSI only): reached only while mpv plays (run: media.external()), which it never does
    def mpv_ok(self):
        """The video's own window (mpv) can be over the screen now: nothing else has to be shown over it."""
        s = self.screen
        msg, until = getattr(s, "toast", ("", 0.0))
        return bool(self.view == "rest" and self.rest_content == "video" and not self.asleep() and not self.confirm
                    and time.time() >= getattr(self, "bar_until", 0) and not (msg and until > time.time())
                    and self.t() - self.oct_at > 1.6 and not self.srec.on and not getattr(self, "rec_starting", False)
                    and not self.tour_on() and not s.lesson_on() and time.time() >= s.summary_until)

    def video_gpu_frame(self):
        """The newest frame stretched by the GPU in its window, opened here the first time. False when there is no
        frame yet, or the window could not open (the video then goes back to the CPU's way, for good)."""
        media = self.screen.media
        with media.lock:
            raw, size = media.frame, media.size
        if raw is None or len(raw) != size[0] * size[1] * 4:
            return False
        vw = getattr(self, "video_win", None)
        try:
            if vw is None:
                mod = sys.modules.get(type(self.screen).__module__)
                vw = self.video_win = mod.open_video_output(self.pg, self.cv.surf.get_size())
            if hasattr(vw, "present_raw") and media.pg_fmt == "RGBX":
                vw.present_raw(raw, size)            # straight to SDL, without holding Python's lock (DisplayVideo)
            else:
                vw.present(self.pg.image.frombuffer(raw, size, media.pg_fmt))
            return True
        except Exception as e:
            print(f"Video window failed ({type(e).__name__}: {e}): the video is drawn on the CPU again", flush=True)
            self.video_gpu_drop()                    # frames at the screen's size again, the video starts again
            return False

    def video_gpu_close(self):
        """Back from the GPU's video to the screen's own drawing. The window stays (screen.DisplayVideo): from now on
        the screen is shown through it (present_frame)."""
        self.layer_dirty()

    def present_frame(self):
        """The frame just drawn, on the screen: through the GPU's window once it exists (it covers the screen's own
        window for the rest of the session), else the usual flip."""
        vw = getattr(self, "video_win", None)
        if vw is not None and hasattr(vw, "present_screen"):
            try:
                vw.present_screen(self.cv.surf)
                return
            except Exception as e:
                print(f"Video window failed ({type(e).__name__}: {e}): the screen is shown as before", flush=True)
                self.video_gpu_drop()
        self.pg.display.flip()

    def video_gpu_drop(self):
        """The GPU's window given up for good: closed in order, the screen and the video back to the CPU's way."""
        vw = self.__dict__.pop("video_win", None)
        if vw is not None:
            vw.close()
        self._gpu_failed = True
        self.layer_dirty()
        self.size_media()

    def size_media(self):
        """The video comes out of GStreamer at the screen's own size (to 1920 wide at most), so each
        frame is only copied to the screen, never scaled on the drawing loop. On a big screen under labwc it
        comes out at its own size instead and the GPU stretches it (gpu_video_wanted)."""
        media = self.screen.media
        # Two screens off (DSI only): the video is never moved to mpv
        # if hasattr(media, "mpv_both"):
        #     # both screens under labwc: the video in mpv on each of them (screen.Media._play_mpv)
        #     both = self.mpv_both_wanted()
        #     if both != media.mpv_both:
        #         media.mpv_both = both
        #         if getattr(media, "playing", None) is not None:
        #             media.stop()                     # started again by follow_music in the new way
        if hasattr(media, "gpu"):
            want = self.gpu_video_wanted()
            if want != media.gpu:
                media.gpu = want
                if getattr(media, "playing", None) is not None:
                    media.stop()                     # started again by follow_music in the new way
            if media.gpu:
                return
        sw, sh = self.cv.surf.get_size()
        k = min(1.0, 1920 / max(1, sw))
        w = max(4, int(sw * k) // 4 * 4)
        h = max(2, int(sh * k) // 2 * 2)
        if hasattr(self.screen.media, "size"):
            self.screen.media.size = (w, h)

    def resize(self):
        self.cv.set_surface(self.pg.display.get_surface())
        self.cv._cache.clear()
        media = self.screen.media
        before = getattr(media, "size", None)
        self.size_media()
        if before != getattr(media, "size", None) and getattr(media, "playing", None) is not None:
            # GStreamer was set up for frames of the old size, which no longer fit the screen's: the
            # video went blank after labwc sized the window or a screen change. It starts again at once
            # at the new size (follow_music, which waits 2 seconds between tries otherwise).
            media.stop()
            self.screen.media_try = 0.0

    def two_displays(self):
        """Two screens connected and no choice kept for this pair yet."""
        if not diag:
            return False
        try:
            con = [d for d in diag.displays() if d.get("connected")]
        except Exception:
            return False
        self.found_displays = con
        names = sorted(d["name"] for d in con)
        if len(con) < 2:
            return False
        keep = self.st.get("display") or {}
        return keep.get("set") != names

    # ------------------------------------------------------------------ the loop
    def tick(self):
        s = self.screen
        self.flush_typed()
        with s.ev_lock:
            events, s.events = s.events, []
        if events:
            self.bump()                            # a message from the engine or a button: the full rate for a while
        for topic, payload in events:
            self.mqtt_event(topic, payload)
        s.follow_keys()
        self.follow_keys_ui()
        self.follow_buttons()
        self.follow_touch_keys()
        self.talk_tick()                           # the figure's line, its notes and the echo game (talk.py)
        self.sync_screen()
        self.pl_track()                            # where the screen is, for the next boot after a power loss (views_power.py)
        s.follow_music()
        lesson = s.lesson_on()
        s.follow_request()
        if lesson:
            s.follow_lesson()
        elif getattr(s, "sent_expect", None) is not None:
            s.send_expect(None)          # the lesson is over: every key plays its own octave again
        s.follow_ghost()
        if lesson != getattr(self, "was_lesson", False):
            self.was_lesson = lesson
        self.follow_alarm_volume()
        self.follow_night()
        self.follow_light()
        self.follow_alarm()
        self.follow_input()
        self.follow_plugs()
        self.follow_jobs()
        self.follow_broker()
        self.follow_away()
        self.follow_home()
        if self.view == "rest" and self.rest_content == "nasa":
            self.apod.start(self.online())
        if self.mouse_at and time.monotonic() - self.mouse_at > 3:     # the pointer goes after 3 still seconds
            self.mouse_at = 0
            self.pg.mouse.set_visible(False)
        self.follow_size()
        self.follow_upd()
        s.media.tick()

    def follow_upd(self):
        """A new version found by "check for update": its screen opens, from the loop and only if the
        settings are still in front. It was opened from the checking thread, in the middle of a frame,
        and wherever the player had gone meanwhile."""
        if getattr(self, "upd_due", False):
            self.upd_due = False
            if self.view == "settings":
                self.open_upd("settings")

    def follow_size(self):
        """The video's frame size checked against the screen's on every loop, not only on a resize
        event: after a change of screens the window could get its size with no event this program saw,
        and GStreamer went on making frames of the old size, which never fit and never showed."""
        media = self.screen.media
        if not hasattr(media, "size"):
            return
        if hasattr(media, "gpu") and media.gpu != self.gpu_video_wanted():
            self.resize()
            return
        if getattr(media, "gpu", False):
            return                                   # the frames are the video's own size, not the screen's
        sw, sh = self.cv.surf.get_size()
        k = min(1.0, 1920 / max(1, sw))
        want = (max(4, int(sw * k) // 4 * 4), max(2, int(sh * k) // 2 * 2))
        if want != media.size:
            log_ = getattr(sys.modules.get(type(self.screen).__module__), "log", print)
            log_(f"Video size {media.size[0]}x{media.size[1]} → {want[0]}x{want[1]}: the video starts again at the new size")
            self.resize()

    def event(self, e):
        pg = self.pg
        self.bump()                                # any pointer, key, touch or window event
        mirror = getattr(sys.modules.get(type(self.screen).__module__), "MIRROR", None)
        sw, sh = self.cv.surf.get_size()
        if mirror is not None and e.type != pg.QUIT and mirror.owns(e):
            # The second screen shows the same picture stretched: the mouse over it works as over the
            # device's screen, its position scaled back. Before, every event there was dropped, so the
            # pointer never appeared on the HDMI screen and clicks did nothing.
            pos = getattr(e, "pos", None)
            if e.type in (pg.MOUSEMOTION, pg.MOUSEBUTTONDOWN, pg.MOUSEBUTTONUP):
                if pos is None:
                    return True
                e = pg.event.Event(e.type, {**e.dict, "pos": self.mirror_pos(mirror, pos)})
            # the wheel, the keyboard and typed text have no position: they work over the HDMI screen as they
            # are (a click there gives that window the keyboard, and typing used to stop until the 5" was clicked)
        vw = getattr(self, "video_win", None)
        if vw is not None and e.type != pg.QUIT and vw.owns(e) and e.type in (
                getattr(pg, "VIDEORESIZE", -1), getattr(pg, "WINDOWSIZECHANGED", -2), getattr(pg, "WINDOWEVENT", -5),
                getattr(pg, "WINDOWSHOWN", -6), getattr(pg, "WINDOWEXPOSED", -7), getattr(pg, "WINDOWRESIZED", -8),
                getattr(pg, "WINDOWFOCUSGAINED", -9), getattr(pg, "WINDOWFOCUSLOST", -10), getattr(pg, "WINDOWENTER", -11),
                getattr(pg, "WINDOWLEAVE", -12), getattr(pg, "WINDOWMOVED", -13)):
            return True
        if vw is not None and e.type in (pg.FINGERDOWN, pg.MOUSEBUTTONDOWN, pg.KEYDOWN, getattr(pg, "MOUSEWHEEL", -3)):
            self.bar_until = time.time() + BAR_SHOW  # a touch on the video brings the buttons up, as on the screen
        if e.type == pg.QUIT:
            return False
        self.pointer_id = getattr(e, "finger_id", "mouse")     # which finger holds which key (free_key)
        if e.type == pg.FINGERDOWN:
            self.pointer_down(e.x * sw, e.y * sh)
        elif e.type == pg.FINGERUP:
            self.pointer_up(e.x * sw, e.y * sh)
        elif e.type == getattr(pg, "FINGERMOTION", -4):
            self.drag_track(e.x * sw, e.y * sh)
            self.pointer_move(e.x * sw, e.y * sh)
        elif e.type == pg.MOUSEBUTTONDOWN and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_down(*e.pos)
        elif e.type == pg.MOUSEBUTTONDOWN and e.button == 3 and not getattr(e, "touch", False):
            self.mouse_back()
        elif e.type == getattr(pg, "MOUSEWHEEL", -3) and not getattr(e, "touch", False):
            y = getattr(e, "precise_y", None)
            y = float(e.y if y is None else y)
            if getattr(e, "flipped", False):
                y = -y
            if y:
                self.mouse_wheel(-y)
        elif e.type == pg.MOUSEBUTTONUP and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_up(*e.pos)
        elif e.type == pg.MOUSEMOTION and not getattr(e, "touch", False):
            if not self.mouse_at:
                pg.mouse.set_visible(True)
            self.mouse_at = time.monotonic()
            if e.buttons and e.buttons[0]:
                self.drag_track(*e.pos)
                self.pointer_move(*e.pos)            # dragged with the button down: a slide over the keys
        elif e.type == pg.TEXTINPUT:
            if self.kb_lang() == "he":
                return True                          # a Hebrew keyboard types from the keys (key_fallback), not from SDL's text
            self._batch_text = True
            self.type_text(e.text)
        elif e.type == pg.KEYDOWN:
            if e.key in (pg.K_LSHIFT, pg.K_LALT) and e.mod & pg.KMOD_LSHIFT and e.mod & pg.KMOD_LALT:
                self.kb_toggle()                     # left Shift + left Alt, as in Windows
                return True
            self.type_key(e.key)
            self.key_fallback(e)
        elif e.type in (getattr(pg, "VIDEORESIZE", -1), getattr(pg, "WINDOWSIZECHANGED", -2)):
            self.resize()
        return True

    def big_cursor(self):
        """The usual Linux mouse pointer (a black arrow with a white edge, as the system's own), larger than SDL's
        small default (shown only while a mouse is moving): about 24 px on the 5" and 50 px on a 1080p screen, at
        most 64 (what the Pi's cursor plane takes). Only the size is changed, not the look."""
        pg = self.pg
        try:
            sw, sh = self.cv.surf.get_size()
            n = max(24, min(64, int(round(22 * sh / 480))))
            big = 8 * n
            edge = max(4, big // 14)                  # the white edge, before the picture is brought down to n
            pad = edge // 2 + 2
            surf = pg.Surface((big, big), pg.SRCALPHA)
            # the shape of X's left_ptr, the arrow Linux desktops show
            pts = [(0, 0), (0, .84), (.21, .63), (.37, .95), (.47, .89), (.32, .58), (.58, .58)]
            k = (big - 2 * pad) * .97
            pts = [(int(x * k) + pad, int(y * k) + pad) for x, y in pts]
            pg.draw.polygon(surf, (0, 0, 0), pts)
            pg.draw.polygon(surf, (255, 255, 255), pts, edge)
            surf = pg.transform.smoothscale(surf, (n, n))
            hot = int(round(pad * n / big))
            pg.mouse.set_cursor(pg.cursors.Cursor((hot, hot), surf))
        except Exception:
            pass

    def run(self):
        pg, clock = self.pg, self.pg.time.Clock()
        self.big_cursor()
        try:
            pg.key.start_text_input()
        except Exception:
            pass
        perf = [time.monotonic(), 0, 0.0, 0.0]     # since, frames, drawing seconds, flip seconds
        drawn_at, drawn_seq = 0.0, -1
        idle_next, last_sig = 0.0, None      # the idle frame rate (IDLE_FPS): when the next frame is due; what the last one drew
        self.bump()
        while True:
            t0 = time.monotonic()
            if self.asleep() and t0 - drawn_at < 1 / SLEEP_FPS:
                # asleep: drawn SLEEP_FPS times a second, the input and the rest looked at more often,
                # so a touch or a key wakes the screen as quickly as before
                try:
                    for e in pg.event.get():
                        if not self.event(e):
                            return
                    self.tick()
                except Exception as e:
                    print(f"Tick failed: {type(e).__name__}: {e}", flush=True)
                if self.asleep():
                    clock.tick(SLEEP_POLL)
                    continue
                t0 = time.monotonic()
            if idle_next and t0 < idle_next:
                # a still page nobody touches: drawn IDLE_FPS times a second, the input and the engine looked at
                # IDLE_POLL times a second. Anything that bumps (an event, a touch, a key, a message) ends the wait
                # and the frame is drawn at once; so does a change of screen or state (idle_view_ok / the busy time).
                seen = self._busy_until
                try:
                    for e in pg.event.get():
                        if not self.event(e):
                            return
                    self.tick()
                except Exception as e:
                    print(f"Tick failed: {type(e).__name__}: {e}", flush=True)
                if self._busy_until == seen and self.idle_now():
                    clock.tick(IDLE_POLL)
                    continue
                idle_next = 0.0
                t0 = time.monotonic()
            # The video in mpv's own window (screen.MpvPlayer): the screen is not drawn under it. Its picture is
            # switched off while something has to be shown over it (the buttons after a touch, a question, a
            # message), and the screen is drawn as usual meanwhile; the sound goes on.
            media = self.screen.media
            ext = getattr(media, "external", None)
            if ext is not None and ext():
                on_rest = self.view == "rest" and self.rest_content == "video"
                if on_rest and media.mpv_touched():
                    self.woke()
                    self.bar_until = time.time() + BAR_SHOW     # a touch on the video brings the buttons up
                ok = on_rest and self.mpv_ok()
                media.mpv_show(ok)
                if ok:
                    try:
                        for e in pg.event.get():
                            if not self.event(e):
                                return
                        self.tick()
                    except Exception as e:
                        print(f"Tick failed: {type(e).__name__}: {e}", flush=True)
                    clock.tick(FPS)
                    continue
            # Over the video the screen is drawn when a new frame of it came (24 a second), checked 60
            # times a second, so each frame is shown on the screen's next refresh. Drawn at a steady 30
            # a second, a 24 frame video showed some frames twice as long as others: a fine stutter.
            media = self.screen.media
            video = getattr(self, "covered", False) and self.rest_content == "video"
            seq = getattr(media, "seq", 0)
            if video and seq == drawn_seq and t0 - drawn_at < 1 / FPS:
                try:
                    for e in pg.event.get():
                        if not self.event(e):
                            return
                    self.tick()
                except Exception as e:
                    print(f"Tick failed: {type(e).__name__}: {e}", flush=True)
                clock.tick(VIDEO_FPS)
                continue
            drawn_at, drawn_seq = t0, seq
            if video and self.gpu_video_ok():
                # the frame goes to the GPU through the screen's renderer (screen.DisplayVideo); the screen is not drawn
                try:
                    for e in pg.event.get():
                        if not self.event(e):
                            return
                    self.tick()
                except Exception as e:
                    print(f"Tick failed: {type(e).__name__}: {e}", flush=True)
                if self.video_gpu_frame():
                    self._gpu_shown = True
                    t2 = time.monotonic()
                    perf[1] += 1
                    perf[2] += t2 - t0
                    if t2 - perf[0] >= 60:
                        n = max(1, perf[1])
                        came = seq - getattr(self, "_perf_seq", seq)    # new frames out of GStreamer in that time
                        print(f"Frames: {n / (t2 - perf[0]):.1f} a second, drawing {perf[2] / n * 1000:.1f} ms, "
                              f"flip {perf[3] / n * 1000:.1f} ms (rest, gpu video, "
                              f"{self.cv.surf.get_width()}x{self.cv.surf.get_height()}, "
                              f"{came / (t2 - perf[0]):.1f} new video frames a second)", flush=True)
                        perf[:] = [t2, 0, 0.0, 0.0]
                        self._perf_seq = seq
                    clock.tick(VIDEO_FPS)
                    continue
            if getattr(self, "_gpu_shown", False):
                self._gpu_shown = False
                self.video_gpu_close()               # the screen is drawn now, through the same window
            self.cv.sig, self.cv.sig_on = 0, bool(IDLE_FPS) and self.view in IDLE_VIEWS
            try:
                for e in pg.event.get():
                    if not self.event(e):
                        return
                self.tick()
                self.draw()
            except Exception as e:
                # one frame that fails is written to session.log and the screen goes on: an error in
                # drawing used to end the program, and both screens went dark until it started again
                key = f"{type(e).__name__}: {e}"
                if key != getattr(self, "_err_key", None) or t0 - getattr(self, "_err_at", 0.0) > 30:
                    import traceback
                    self._err_key, self._err_at = key, t0
                    print(f"Frame failed ({self.view}): {key}\n{traceback.format_exc()}", flush=True)
            t1 = time.monotonic()
            self.splash_fade(t0)
            self.present_frame()
            if self.srec.on:
                try:
                    self.srec.feed(pg.display.get_surface(), pg)    # the frame just shown, to the recording
                except Exception as e:             # a frame the recording could not take never ends the program
                    print(f"Recording frame failed: {type(e).__name__}: {e}", flush=True)
            mirror = getattr(sys.modules.get(type(self.screen).__module__), "MIRROR", None)
            if mirror is not None:
                self.mirror_frame(mirror)
            t2 = time.monotonic()
            perf[1] += 1
            perf[2] += t1 - t0
            perf[3] += t2 - t1
            if t2 - perf[0] >= 60:                 # once a minute in session.log, for armonico-pi-report
                n = max(1, perf[1])
                vs = getattr(self.screen.media, "video_stats", lambda: None)() if self.view == "rest" else None
                last = getattr(self, "_perf_video", None)
                video = ""
                if vs and last and vs[0] >= last[0]:
                    shown, late = vs[0] - last[0], vs[1] - last[1]
                    video = f", video {shown / (t2 - perf[0]):.1f} frames a second, {late} dropped as late"
                self._perf_video = vs
                print(f"Frames: {n / (t2 - perf[0]):.1f} a second, drawing {perf[2] / n * 1000:.1f} ms, "
                      f"flip {perf[3] / n * 1000:.1f} ms ({self.view}, {self.theme}, "
                      f"{self.cv.surf.get_width()}x{self.cv.surf.get_height()}{video})", flush=True)
                perf[:] = [t2, 0, 0.0, 0.0]
            if self.cv.sig_on:                     # the frame drew something other than the last one: not quiet yet
                sig = hash((self.cv.sig, self.dim, self.shift))
                if sig != last_sig:
                    last_sig = sig
                    self.bump()
            else:
                last_sig = None
            if not video and self.idle_now():
                idle_next = t0 + 1 / IDLE_FPS
                clock.tick(IDLE_POLL)
                continue
            idle_next = 0.0
            clock.tick(VIDEO_FPS if video else FPS)

    def splash_fade(self, now):
        """The start-up picture (screen.py splash) fades out over the first frames of the screens, so the
        opening is one movement and not a cut. It is dropped after 0.6 s, or when the size changed."""
        sp = getattr(sys.modules.get(type(self.screen).__module__), "SPLASH", None)
        img = sp.get("surf") if sp else None
        if img is None:
            return
        surf = self.pg.display.get_surface()
        if "t0" not in sp:
            sp["t0"] = now
        k = (now - sp["t0"]) / 0.6
        if k >= 1 or img.get_size() != surf.get_size():
            sp["surf"] = None
            return
        img.set_alpha(int(255 * (1 - k)))
        surf.blit(img, (0, 0))

    MIRROR_FPS = 15        # the second screen's own drawing: half the device's rate, so touches stay quick
    FPS_LAYERED = FPS      # ... and every frame over the rest animation (draw_rest_layer)

    def mirror_frame(self, mirror):
        """Both screens: the second one gets a picture drawn at its own resolution, sharp, at up to
        MIRROR_FPS. Over the video or the NASA picture it shows the device's frame stretched instead,
        at the full rate: the video is 1280 wide at the source, so drawing it again would only cost time.
        Before, every frame was the 5" picture stretched to the 24", soft on every screen."""
        now = time.monotonic()
        if getattr(self, "covered", False) or not hasattr(mirror, "win"):
            self.mirror_sharp = False
            mirror.present(self.cv.surf)
            return
        # the rest animation, drawn from its still layer, is cheap enough for every frame on the second
        # screen too: at 15 a second the figure and the stars moved in jerks on the HDMI screen
        fps = self.FPS_LAYERED if self.layer_ok() else self.MIRROR_FPS
        if now - getattr(self, "mirror_at", 0.0) < 1 / fps - 0.004:
            return
        self.mirror_at = now
        size = tuple(mirror.win.size)
        cv2 = getattr(self, "cv2", None)
        if cv2 is None or cv2.surf.get_size() != size:
            cv2 = self.cv2 = canvas_mod.Canvas(self.pg, self.pg.Surface(size), self.cv.fonts)
        own, hits, bar = self.cv, self.hits, self.bar_items
        self.cv = cv2
        try:
            self.draw()
        except Exception as e:
            self.cv = own
            print(f"Second screen frame failed: {type(e).__name__}: {e}", flush=True)
            mirror.present(own.surf)
            return
        finally:
            self.cv, self.hits, self.bar_items = own, hits, bar
        self.mirror_sharp = True
        mirror.present(cv2.surf)

    def mirror_pos(self, mirror, pos):
        """A point on the second screen, as a point on the device's screen."""
        cv = self.cv
        cv2 = getattr(self, "cv2", None)
        if getattr(self, "mirror_sharp", False) and cv2 is not None:
            x, y = cv2.to_plan(*pos)                 # the same plan, drawn at the second screen's scale
            return int(cv.ox + x * cv.scale), int(cv.oy + y * cv.scale)
        mw, mh = mirror.win.size
        sw, sh = cv.surf.get_size()
        return int(pos[0] * sw / max(1, mw)), int(pos[1] * sh / max(1, mh))

    # ------------------------------------------------------------------ drawing
    def background(self, t, live=None):
        """The sky or the classic background. live: a list, when it is drawn into the rest screen's
        still layer: the stars go into the list instead (see draw_rest_layer)."""
        st = self.sky_st
        dark = sky_mod.is_dark(self.theme, st) if st else True
        self.dark, self.zen, self.hor = dark, (13, 20, 36), (34, 49, 79)
        # only the rest screen itself: a lesson or its summary started from it keeps the view "rest",
        # and with the NASA picture kept their background came out black
        covered = self.view == "rest" and not self.screen.lesson_on() and time.time() >= self.screen.summary_until and (
            (self.rest_content == "video" and getattr(self.screen.media, "frame", None) is not None)
            or (self.rest_content == "nasa" and self.apod.image is not None))
        self.covered = covered
        if covered:
            pass                                   # the video or the picture fills the screen: no sky under it
        elif self.theme == "sky" and st is not None:
            self.zen, self.hor = self.sky.draw(self.cv, st, t, faint=1.0 if self.view == "rest" else 0.22,
                                               lang=self.lang, live=live)
        else:
            sky_mod.classic(self.cv, t, dark, live=live)
        self.P = sky_mod.palette(self.theme, dark, st.sun.altitude if st else None)

    def hills(self, strength):
        self._hill_call = strength
        if strength == getattr(self, "_hill_done", None):
            return                                  # already in the backdrop under this frame (draw_backdrop)
        if self.theme == "sky":
            sky_mod.hills(self.cv, self.hor, strength)

    def draw_backdrop(self, t, scene):
        """The background behind every screen but the rest screen, kept as a layer: the sky (or the
        classic look) with the hills the screen draws over it, made again only when the minute, the
        theme, the screen or the size changes, or after LAYER_MAX_AGE seconds. Each frame copies it and
        twinkles the faint stars over it. Drawn anew on every frame, the sky and its hills (a whole-width
        layer with alpha) were half the time of a frame at 1920x1080, and the lists, the free play and
        the settings came out slow on the HDMI screen, the keys answering late."""
        cv = self.cv
        sw, sh = cv.surf.get_size()
        st = self.sky_st
        seen = getattr(self, "_hill_seen", None)
        if seen is None:
            seen = self._hill_seen = {}
        if scene not in seen:                       # a screen not seen yet: drawn plainly once, to learn its hills
            self._hill_done = None
            self.background(t)
            return
        hill = seen[scene]
        key = (scene, hill, self.theme, self.lang, int(self.now_ms() // 60000), id(cv),
               round(st.sun.altitude, 1) if st else None)
        drops = getattr(self, "_backdrops", None)
        if drops is None:
            drops = self._backdrops = {}
        B = drops.get((sw, sh))
        now = time.monotonic()
        if B is None or B["key"] != key or now - B["at"] > self.LAYER_MAX_AGE:
            surf = B["surf"] if B is not None and B["surf"].get_size() == (sw, sh) else \
                self.pg.Surface((sw, sh), 0, cv.surf)
            stars = []
            screen_surf, cv.surf, cv.rec = cv.surf, surf, []
            mirrored, sig_on = cv.rtl, cv.sig_on
            cv.sig_on = False                       # the kept background is made again every 10 s, the same: not a change
            try:
                self.background(t, live=stars)
                if hill is not None and self.theme == "sky":
                    sky_mod.hills(cv, self.hor, hill)
            finally:
                rects, cv.surf, cv.rec, cv.rtl, cv.sig_on = cv.rec, screen_surf, None, mirrored, sig_on
            stars = [q for q in stars if not any(x0 <= q[0] <= x1 and y0 <= q[1] <= y1 for x0, y0, x1, y1 in rects)]
            B = drops[(sw, sh)] = {"key": key, "at": now, "surf": surf, "stars": stars, "rects": rects,
                                   "look": (self.P, self.zen, self.hor, self.dark)}
        else:
            self.P, self.zen, self.hor, self.dark = B["look"]
        self.covered = False
        cv.surf.blit(B["surf"], (0, 0))
        if self.theme == "sky" and st is not None:
            self.sky.draw_live(cv, st, t, B["stars"], 0.22, B["rects"])
        else:
            sky_mod.classic_live(cv, B["stars"], t, self.dark)
        self._hill_done = hill

    def draw(self):
        cv, t, s = self.cv, self.t(), self.screen
        self.hits, self.bar_items, self.zone = [], [], None
        cv.rtl = self.lang == "he"
        cv.zoom = min(1.5, self.zoom_pct() / 100 * screensize.layout(self.screen_inch)[2])
        self.char.update(t)
        if time.time() < self.off_until:          # shutting down or restarting: dark, only the question
            cv.fill((0, 0, 0))
            self.P = sky_mod.palette("classic", True, None)
            self.draw_confirm()
            return
        if self.asleep():                          # dimmed to a small clock: nothing else is drawn
            self.draw_sleep()
            self.finish_frame()
            return
        hid = self.bar_hidden()
        if hid != getattr(self, "_bar_hid", hid):
            self.layer_dirty()                     # the buttons came up or went down: the kept picture is stale
        self._bar_hid = hid
        if self.tour_draw(t):                      # the opening tour over the rest screen (tour.py)
            return
        if self.wn_due and self.wn_ready():        # What's new, once after an update (views_whatsnew.py)
            self.open_whatsnew()
        # no black fill first: the background, the video or the picture covers the whole screen, and
        # a fill of the 24" screen on every frame was 8 MB written for nothing
        summary = s.engine.state.get("summary")
        if summary and summary.get("id") != s.summary_id:
            s.summary_id = summary.get("id")
            s.summary_until = time.time() + 12 if s.summary_worth(summary) else 0   # nothing played: no card
        layered = self.layer_ok()
        scene = "lesson" if s.lesson_on() else "summary" if time.time() < s.summary_until else self.view
        self._hill_call = self._hill_done = None
        if layered:
            self.draw_rest_layer(t)
        elif scene == "rest":
            self.background(t)                      # the video or the picture: no sky under it
        else:
            self.draw_backdrop(t, scene)
        if layered:
            pass
        elif s.lesson_on():
            self.view_lesson(t)
        elif time.time() < s.summary_until:
            self.view_summary(summary or {}, t)
        else:
            getattr(self, "view_" + self.view, self.view_rest)(t)
        if not layered and scene != "rest":
            self._hill_seen[scene] = self._hill_call      # which hills this screen draws: the next backdrop has them
        self.tip_draw(t)                            # a one-time tip in a corner (tour.py)
        self.draw_saving()                          # a stopped screen recording being saved: how far it is
        self.draw_soak()                            # the load test is running: a strip at the top
        self.draw_toast()
        if t - self.boot_at < 1.6:
            cv.rect(cv.vx, cv.vy, cv.vw, cv.vh, (0, 0, 0, int(255 * (1 - (t - self.boot_at) / 1.6))))
        self.draw_confirm()
        self.finish_frame()

    # ------------------------------------------------------------------ the rest screen's still layer
    LAYER_MAX_AGE = 10.0      # seconds: drawn again at least this often, whatever changed

    def layer_ok(self):
        """The rest screen with its animation (not the video or the picture): drawn as a still layer
        plus the few things that move."""
        s = self.screen
        if self.view != "rest" or s.lesson_on() or time.time() < s.summary_until:
            return False
        return not ((self.rest_content == "video" and getattr(s.media, "frame", None) is not None)
                    or (self.rest_content == "nasa" and self.apod.image is not None))

    def layer_dirty(self):
        """Something may have changed what the still layer shows: it is drawn again on the next frame."""
        self._layers = {}
        self._backdrops = {}
        self.bump()

    # ------------------------------------------------------------------ the idle frame rate
    def bump(self, secs=None):
        """Something happened (input, an event, a change): the screen is drawn at the full rate until IDLE_AFTER
        seconds of quiet have passed. The one place every input and change path reports to."""
        until = time.monotonic() + (IDLE_AFTER if secs is None else secs)
        if until > getattr(self, "_busy_until", 0.0):
            self._busy_until = until

    def idle_view_ok(self):
        """The screen is one of the still ones (IDLE_VIEWS) and nothing on it is timed, held, asked or shown over it.
        Whatever else moves on its own (a spinner, a blinking cursor, a clock, a progress bar) changes what is drawn,
        which the loop sees (canvas sig) and keeps the full rate for."""
        s = self.screen
        now = time.time()
        msg, until = getattr(s, "toast", ("", 0.0))
        return bool(IDLE_FPS and self.view in IDLE_VIEWS and not self.asleep() and not self.confirm
                    and not s.lesson_on() and now >= s.summary_until and now >= self.off_until
                    and not (msg and until > now) and not self.tour_on() and not self.srec.on
                    and getattr(self, "drag", None) is None and not self.down and not self.wn_due
                    and not getattr(self, "covered", False) and not self.sky.meteors
                    and self.t() - self.boot_at > 2.0)

    def idle_now(self):
        """Quiet long enough on a still screen: the next frame may wait."""
        return bool(self.idle_view_ok() and time.monotonic() >= getattr(self, "_busy_until", 0.0))

    def draw_rest_layer(self, t):
        """The rest screen in two parts. What stays the same for minutes (the sky's colors, the Moon,
        the hills, the clock, the texts, the buttons) is drawn once into a layer and copied onto every
        frame; only what moves is drawn each frame: the stars twinkling, the meteors, the figure, the
        song card and the octave note. Drawing everything on every frame at 1920x1080 was too slow for
        the Pi 4, and the animation on the HDMI screen came out slow and jerky.
        The layer is drawn again when the minute, the theme, the language, the network or the screen
        changes, after any touch or button, and every LAYER_MAX_AGE seconds."""
        cv = self.cv
        sw, sh = cv.surf.get_size()
        st = self.sky_st
        # a question open greys out ☰ in the bar: the layer is drawn again when it opens and closes
        key = (self.theme, self.lang, self.kind, int(self.now_ms() // 60000), self.online(),
               bool(self.update), bool(self.confirm), id(cv), round(st.sun.altitude, 1) if st else None,
               bool(getattr(self, "chk_offer", False)))
        layers = getattr(self, "_layers", None)
        if layers is None:
            layers = self._layers = {}
        L = layers.get((sw, sh))
        now = time.monotonic()
        if L is None or L["key"] != key or now - L["at"] > self.LAYER_MAX_AGE:
            old_surf = L["surf"] if L is not None else None
            surf = old_surf if old_surf is not None and old_surf.get_size() == (sw, sh) else \
                self.pg.Surface((sw, sh), 0, cv.surf)
            stars = []
            screen_surf, cv.surf, cv.rec = cv.surf, surf, []
            try:
                self.background(t, live=stars)
                self.view_rest(t, part="still")
            finally:
                rects, cv.surf, cv.rec = cv.rec, screen_surf, None
            # a star under a hill, a line of text or the Moon stays hidden, as when it was drawn first
            stars = [q for q in stars if not any(x0 <= q[0] <= x1 and y0 <= q[1] <= y1 for x0, y0, x1, y1 in rects)]
            L = layers[(sw, sh)] = {"key": key, "at": now, "surf": surf, "stars": stars, "rects": rects,
                                    "hits": list(self.hits), "bar": list(self.bar_items),
                                    "look": (self.P, self.zen, self.hor, self.dark)}
        else:
            self.hits, self.bar_items = list(L["hits"]), list(L["bar"])
            self.P, self.zen, self.hor, self.dark = L["look"]
        self.covered = False
        cv.surf.blit(L["surf"], (0, 0))
        if self.theme == "sky" and st is not None:
            self.sky.draw_live(cv, st, t, L["stars"], 1.0, L["rects"])
        else:
            sky_mod.classic_live(cv, L["stars"], t, self.dark)
        self.view_rest(t, part="live")

    def draw_sleep(self):
        """The sleep screen: the clock, the next alarm and the weather, the Moon, and the figure asleep
        (sleep.py), all dim."""
        self.hits = []
        self.covered = False                       # the second screen draws it sharp, as any screen
        if getattr(self, "sleep_screen", None) is None:
            self.sleep_screen = SleepScreen()
        self.sleep_screen.draw(self)
