"""views_settings.py - the settings in 12 groups, the edit screen (touch keyboard, a USB keyboard,
or a phone for long tokens), the city search and the job screen.

Only cells with an action can be framed: ▼▲ skip the cells that only inform. ✓ acts on the framed
cell, and the ✓ button carries its name. Every change is saved to settings.json at once.
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .canvas import W
from .texts import steps as text_steps
from .texts import T as TEXTS
from .widgets import Job
from . import system
from . import audio
from .widgets import SwitchVal
from .light import DAY_LEVELS, NIGHT_AUTO, NIGHT_STRENGTH

THEMES = ("classic", "classicLight", "auto", "sky")
CHARS = ("house", "keys", "grand")   # , "metro": the metronome was taken out
RESTS = ("video", "animation", "nasa")
SLEEPS = (0, 1, 5, 15)
NEW_AFTER = 20                 # seconds a sound device must have been away to count as just connected (follow_sound)
# OUTPUTS = ("aux", "hdmi", "both", "off")    # HDMI off (DSI only)
OUTPUTS = ("aux", "off")
STOPS = ("ok", "up", "down", "menu", "key:0")
PAGES = ("top", "look", "sound", "play", "place", "net", "bkp", "sys")
# a page opened from a cell of another page: (that page, the cell)
SUB_PAGES = {"outs": ("sound", 0), "alarm": ("place", 2), "kbd": ("play", 0), "less": ("play", 3), "conn": ("net", 5), "ha": ("conn", -2), "tg": ("conn", -1),
             "chips": ("net", 7), "night": ("look", 5), "rest": ("look", 6), "test": ("sys", 0), "zoom": ("look", 2), "quick": ("test", 5), "soak": ("test", 6),
             "rec": ("sys", 2), "guide": ("sys", 3), "reset": ("sys", 4), "profiles": ("play", 5), "prof": ("profiles", None)}
# a page opened from a cell of another page: (that page, the cell; below 0 counted from the end, None: the profile's card)
EROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm.-_@")
# the symbols layer (the !#$ key): what passwords and links need besides letters and digits
HEROWS = ("1234567890", "קראטוןםפ", "שדגכעיחלךף", "זסבהנמצתץ.-")      # the letters in the order of a Hebrew keyboard, as the city search has them
SROWS = ("1234567890", "!@#$%^&*()", "-_=+[]{}\\|~", ";:'\",.<>/?`")
CROWS = {"en": ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"), "he": ("קראטוןםפ", "שדגכעיחלךף", "זסבהנמצתץ")}
LESSON_UI_PORT = os.environ.get("LESSON_UI_PORT", "8099")
BOOT_CONFIG = os.environ.get("ARMONICO_BOOT_CONFIG", "/boot/firmware/config.txt")   # read only: root writes it
PROFILES_MAX = 12          # as lessons/piano_game.py, which refuses one more


def nxt(seq, cur):
    seq = list(seq)
    return seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]


def masked(v):
    return ("●●●●●● " + v[-4:]) if v else ""


class SettingsViews:
    def init_settings_state(self):
        self.page, self.sel = "top", 0
        self.edit, self.caps, self.paste = None, False, None
        self.query, self.kb, self.place_return = "", "en", "settings"
        self.job = None
        self.broker = {}
        self.reset_broker()
        self.ha = {"token": "", "found": None, "searching": False}
        self.tg = {"token": ""}
        self.sound_due = time.time() + 2.0          # the saved output, made PipeWire's default once at start
        self.sound_tries = 0
        self.snd_list = []                    # the outputs there now: [(key, name)], from PipeWire every 3 s
        self.snd_seen = set()                 # the USB devices seen since the start
        self.snd_miss = {}                    # a chosen USB device missing: how many looks in a row
        self.snd_started = time.monotonic()
        self.snd_at = 0.0
        self.snd_now = None                   # (the outputs played, PipeWire's default id) of the last apply
        self.snd_ids = {}                     # the USB and Bluetooth outputs' node ids at the last apply (audio.ext_ids)
        self.vol_adjust = False
        self.voice_adjust = False
        self.voice_now = None                # the voice of the keys, as armonico_pi.py last said

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def local_now():
        """The broker in use now (config.env), not the one chosen on the screen and not yet applied."""
        return os.environ.get("MQTT_LOCAL", "no") == "yes"

    def reset_broker(self):
        """The choice on the screen back to the broker in use. On the local broker, the external one's
        fields come from its saved profile, so a way back to it does not start from empty fields.
        The status is the last one tested on the broker in use (a test of a choice left unapplied
        is not its status), and a test still running for the old choice no longer writes it."""
        env = os.environ
        if self.local_now():
            ext = saved_profile("lab")
            self.broker.update(type="local", host=ext.get("MQTT_HOST", ""), port=ext.get("MQTT_PORT") or "1883",
                               user=ext.get("MQTT_USER", ""), **{"pass": ext.get("MQTT_PASS", "")})
        else:
            self.broker.update(type="external", host=env.get("MQTT_HOST", ""), port=env.get("MQTT_PORT", "1883"),
                               user=env.get("MQTT_USER", ""), **{"pass": env.get("MQTT_PASS", "")})
        # unknown until this screen's own MQTT connection says (follow_broker): "✔ Connected" was the
        # default, shown after every start whatever the broker did
        state, ms = self.broker.get("used", ("unknown", 0))
        self.broker.update(state=state, ms=ms, dirty=False, gen=self.broker.get("gen", 0) + 1)

    def on(self, v):
        return self.tr("on") if v else self.tr("off")

    def broker_text(self):
        b = self.broker
        t = self.tr("bt." + b["state"])
        return t + (f" · {b['ms']}ms" if b["state"] == "ok" and b["ms"] else "")

    def version(self):
        # read once a minute at most: the settings draw every frame, and asked it twice a frame
        at, v = getattr(self, "_ver_seen", (-1e9, ""))
        if time.monotonic() - at > 60:
            try:
                v = (Path(__file__).resolve().parent.parent.parent / "VERSION").read_text().strip()
            except OSError:
                v = ""
            v = re.sub(r"^(\d+\.\d+)\.0$", r"\1", v)
            self._ver_seen = (time.monotonic(), v)
        return v

    def profiles(self):
        p = self.screen.engine.overview.get("profiles")
        return p if isinstance(p, dict) and isinstance(p.get("list"), list) else {"active": "main", "list": []}

    def profile_name(self):
        p = self.profiles()
        cur = next((x for x in p["list"] if x.get("id") == p.get("active")), None)
        return (cur or {}).get("name") or self.tr("mainProfile")

    def edit_field(self, label, get, set_, secret=False, phone=False, keep=False, reveal=False, ok=None, note="",
                   back=None, kb="en", digits=False):
        """keep: the value as typed, spaces and all (a password); reveal: a Show/Hide for a secret;
        ok: the ✓ button's word; note: a line under the field (why it opened again);
        kb: the letters the keyboard opens with, "en" or "he" (a name in Hebrew; the key beside the space changes it).
        digits: a field that takes only digits (a PIN, a port) opens the phone keypad instead of the letters."""
        if back is None:
            back = self.edit["back"] if self.view == "edit" and self.edit else self.view
        self.close_paste()
        self.edit = {"label": label, "val": get() or "", "set": set_, "secret": secret, "phone": phone, "back": back,
                     "keep": keep, "reveal": reveal, "shown": False, "ok": ok, "note": note,
                     "kb": kb if kb in ("en", "he") else "en", "digits": bool(digits)}
        self.caps = False
        self.sym = False
        self.pad_sel = 0
        if phone:
            from .phone import PasteServer
            self.paste = PasteServer(label, self.pasted)
            if not self.paste.start():
                self.paste = None
        self.go("edit")

    def pasted(self, value):
        if self.edit is not None:
            self.edit["val"] = value

    def close_paste(self):
        if self.paste:
            self.paste.stop()
            self.paste = None

    def edit_done(self):
        e, self.edit = self.edit, None
        self.close_paste()
        if e:
            e["set"](e["val"] if e.get("keep") else e["val"].strip())
            if self.edit is None:                    # set may open the next field (a new profile: its PIN)
                self.go(e["back"])

    def edit_cancel(self):
        e, self.edit = self.edit, None
        self.close_paste()
        self.go(e["back"] if e else "settings")

    def start_job(self, title, labels, back, work):
        self.job = Job(title, labels, back)
        self.go("job")
        return self.job.run(work)

    def follow_jobs(self):
        s = self.screen
        # a lesson started from anywhere (the browser, Telegram): a song that plays is stopped under it, once
        if s.lesson_on() and getattr(s, "song", False) and not getattr(s, "alarm", False):
            if not getattr(self, "_lesson_stop_sent", False):
                self._lesson_stop_sent = True
                s.pub("piano/stop", "Armonico Pi: a lesson")
        else:
            self._lesson_stop_sent = False
        j = self.job
        if j is not None and j.done and j.ok and j.after:
            fn, j.after = j.after, None
            fn()
        self.follow_sound()                          # not while an apply is due: that one comes first
        self.follow_rec()
        if self.sound_due and time.time() > self.sound_due:
            self.sound_due = 0.0
            out = self.output
            by_hand = getattr(self, "sound_by_hand", False)

            def go():
                try:
                    apply_now()
                finally:
                    self.snd_applying = False

            def apply_now():
                # An output kept in the settings that is not there now (HDMI while the Pi started with the
                # 5" only, a USB device not plugged in yet): the rest of the choice plays, or AUX when
                # nothing of it is there, with no message, and the choice stays. A screen plugged in
                # later starts the program again (follow_plugs); a USB device is looked for every 3 s
                # (follow_sound). The message is only for a choice made by hand just now.
                found = audio.sinks()
                here = audio.keys_now(found)
                self.snd_list = here
                keys = [] if out == "off" else self.output_keys()
                have = [k for k in keys if k in dict(here)]
                missing = len(have) < len(keys)
                if out == "off":
                    eff, mix = "off", None
                else:
                    eff, mix = self.keys_setting(have or ["aux"])
                was = audio.default_id()
                ok, msg = audio.apply(eff, mix) if mix else audio.apply(eff)
                if not ok:
                    if self.sound_tries < 10:      # PipeWire still starting (just after boot): again soon
                        self.sound_tries += 1
                        self.sound_due = time.time() + 3
                        return
                    if eff not in ("aux", "off"):
                        audio.apply("aux")         # the output did not appear: the speaker on AUX at least
                    if by_hand:
                        self.fail_toast(msg)
                    else:
                        print(f"Sound output {out} did not open ({msg}): on the headphone jack meanwhile", flush=True)
                elif missing and by_hand:
                    self.fail_toast("snd.none")
                self.snd_now = (tuple(have or ["aux"]) if out != "off" else (), audio.default_id())
                self.snd_ids = audio.ext_ids()       # the USB and Bluetooth outputs as they were when the mix was made
                self.sound_tries = 0
                self.sound_by_hand = False
                # the video and the tone start again on the new output, only when it really changed: at
                # start the output is usually the one already set, and the video began again 2 s in
                if by_hand or was is None or audio.default_id() != was:
                    self.sound_applied = True
            self.snd_applying = True                 # the watcher waits: it compared with the old choice
            threading.Thread(target=go, daemon=True).start()
        if getattr(self, "sound_applied", False):
            self.sound_applied = False
            self.screen.media.stop()
            self.screen.media_try = 0.0            # the video again at once on the new output, not 2 s later
            timer = getattr(self, "tone_timer", None)
            if timer is not None:
                timer.cancel()
            # The test tone's player opens the new output too. Under one lock with the tone itself: a
            # tone still due from the timer played into a player being closed, and with a few quick
            # presses of AUX, HDMI and the test the program fell (the screens went black and came back)
            with self.screen.mixer_lock:
                try:
                    self.pg.mixer.quit()
                    self.pg.mixer.init()
                except self.pg.error:
                    pass
            want, self.tone_after = getattr(self, "tone_after", False), False
            if want:
                # only while still on the step where AUX or HDMI was touched, and soon after: a tone that
                # came half a minute later, on the broker's step, sounded like a random test
                view, step, at = want if isinstance(want, tuple) else (self.view, getattr(self, "setup_step", ""), time.time())
                if self.view == view and getattr(self, "setup_step", "") == step and time.time() - at < 8:
                    self.test_tone()
        self.follow_backup_job()

    def follow_sound(self):
        """Every 3 s: the outputs there now, for the settings and the setup. A USB device that was chosen
        and is gone (or did not come within 20 s of the start) is taken out of the choice, which goes back
        to the output before it. When that same device is plugged in again, and the choice was not changed
        by hand meanwhile, the choice it was in comes back. The same device on another profile (its node
        name ends in the profile) stays chosen under its new name. An output of the choice that came back,
        or a default PipeWire moved by itself, is put right again."""
        now = time.monotonic()
        # while the screen sleeps a look every 15 s is enough: a device unplugged then is still noticed, at a fifth of the work
        every = 15 if self.asleep() else 3
        if now - self.snd_at < every or self.sound_due or getattr(self, "snd_busy", False) \
                or getattr(self, "snd_applying", False):
            return
        self.snd_at, self.snd_busy = now, True

        def go():
            try:
                found = audio.sinks()
                if not found:
                    return                           # PipeWire did not answer (restarting, busy): no news
                here = audio.keys_now(found)
                self.snd_list = here
                self.snd_bt_quality = audio.bt_quality(found)
                names = dict(here)
                self.bt_boot_cycle([k for k, _ in here])   # a connected device with no sound output asked for its sound
                if self.bt.get("cycling"):
                    return                           # their outputs go and come back: nothing to follow meanwhile
                known = dict(self.st.get("snd_names") or {})
                seen = {k: v for k, v in here if audio.is_ext(k) and known.get(k) != v}
                if seen:                             # the names of devices that come and go, for when they are away
                    known.update(seen)
                    self.save(snd_names=known)
                keys = self.output_keys()
                usb = [k for k in keys if audio.is_ext(k)]
                # a Bluetooth device in the choice that is not connected now: connected again, with a growing wait
                back0 = self.st.get("sound_back")
                self.follow_bt([k for k in keys + (list(back0.get("keys") or []) if isinstance(back0, dict) else [])
                                if isinstance(k, str) and k.startswith(audio.BT) and k not in names])
                devs = {audio.usb_dev(k): k for k in names if audio.is_ext(k)}

                def now_key(k):
                    return devs.get(audio.usb_dev(k), k) if audio.is_ext(k) else k
                # the same device under another profile's name (the recorder moves a card to a profile
                # with its microphone): the choice follows the new name instead of losing the device
                if any(k not in names and audio.usb_dev(k) in devs for k in usb):
                    out, mix = self.keys_setting([now_key(k) for k in keys])
                    self.save(sound_out=out, sound_mix=mix)
                    self.snd_miss = {}
                    self.sound_due = time.time()
                    return
                # the device that was unplugged is back: the choice it was in, unless one was made by hand since
                back = self.st.get("sound_back")
                if isinstance(back, dict) and back.get("keys") and \
                        all(audio.usb_dev(k) in devs for k in back["keys"]):
                    bout, bmix = back.get("out"), back.get("mix")
                    # old = {"both": ["aux", "hdmi"], "off": [], "mix": bmix or []}.get(bout, [bout])    # HDMI off (DSI only)
                    old = {"both": ["aux"], "off": [], "mix": bmix or []}.get(bout, [bout])
                    old = [k for k in old if k != "hdmi"] or ["aux"]          # HDMI off (DSI only)
                    out, mix = self.keys_setting([now_key(k) for k in old if isinstance(k, str)] or ["aux"])
                    self.save(sound_out=out, sound_mix=mix, sound_back=None)
                    self.snd_seen.update(now_key(k) for k in back["keys"])
                    self.snd_miss = {}
                    self.toast(self.tr("snd.btBack" if all(k.startswith(audio.BT) for k in back["keys"]) else "snd.usbBack",
                                       out=self.output_label()), 4)
                    self.sound_due = time.time()
                    return
                # a sound device just connected (headphones, a speaker, a USB card) becomes the output at once,
                # as AUX is when nothing else is there: no going to Settings to choose it. A device there since the
                # screen started is left as chosen, and so is one seen in the last NEW_AFTER seconds (one odd answer
                # from PipeWire, a headset's profile changing). When it goes, the choice before it comes back.
                now_m = time.monotonic()
                last = self.__dict__.setdefault("snd_ext_last", {})
                first_look = not self.__dict__.get("snd_ext_looked")
                self.snd_ext_looked = True
                chosen = {audio.usb_dev(k) for k in keys if audio.is_ext(k)}
                fresh = [k for k in names if audio.is_ext(k) and audio.usb_dev(k) not in chosen
                         and now_m - last.get(audio.usb_dev(k), -1e9) > NEW_AFTER]
                for k in names:
                    if audio.is_ext(k):
                        last[audio.usb_dev(k)] = now_m
                srec = getattr(self, "srec", None)
                recording = bool(getattr(srec, "on", False) or getattr(srec, "saving", False)
                                 or getattr(self, "rec_saving", False) or getattr(self, "rec_sound_back", None))
                if fresh and not first_look and self.output != "off" and not recording:
                    # never during a screen recording: it records the output it started on
                    k = fresh[-1]
                    out, mix = self.keys_setting([k])
                    kw = {"sound_out": out, "sound_mix": mix, "sound_back": None,
                          "sound_prev": self.output, "sound_prev_mix": self.output_mix if self.output == "mix" else None}
                    self.save(**kw)
                    self.snd_seen.add(k)
                    self.snd_miss = {}
                    self.toast(self.tr("snd.newOut", out=names.get(k) or self.output_label()), 4)
                    self.sound_due = time.time()
                    return
                self.snd_seen.update(k for k in usb if k in names)
                # missing twice in a row, 3 s apart: one odd answer from PipeWire takes nothing away
                miss = {k: self.snd_miss.get(k, 0) + 1 for k in usb if k not in names}
                self.snd_miss = miss
                gone = [k for k, n in miss.items() if n >= 2
                        and (k in self.snd_seen or time.monotonic() - self.snd_started > 20)]
                if gone:
                    was = {"keys": gone, "out": self.output, "mix": self.output_mix if self.output == "mix" else None}
                    left = [k for k in keys if k not in gone]
                    if left:
                        out, mix = self.keys_setting(left)
                    else:
                        prev = self.st.get("sound_prev")
                        pm = self.st.get("sound_prev_mix")
                        # the output before it: a USB or Bluetooth device in it only when it is there now
                        pkeys = (pm or []) if prev == "mix" else [prev]
                        here_now = all(not audio.is_ext(k) or (k in names and k not in gone) for k in pkeys)
                        out, mix = (prev, pm) if isinstance(prev, str) and here_now and \
                            (prev != "mix" or isinstance(pm, list)) else ("aux", None)
                    self.save(sound_out=out, sound_mix=mix, sound_back=was)
                    self.snd_seen.difference_update(gone)
                    self.snd_miss = {}
                    self.toast(self.tr("snd.btGone" if all(k.startswith(audio.BT) for k in gone) else "snd.usbGone",
                                       out=self.output_label()), 4)
                    self.sound_due = time.time()
                    return
                self.bt_wifi_rest(keys, names)
                if self.snd_now is not None and self.output != "off":
                    want = tuple(k for k in keys if k in names) or ("aux",)
                    # a chosen USB or Bluetooth output that went and came back between two looks (a headset that
                    # dropped for a second) is a new node: the mix made for the old one is made again for it
                    ids = audio.ext_ids(found)
                    renewed = any(k in ids and k in self.snd_ids and ids[k] != self.snd_ids[k] for k in want)
                    moved = bool(self.snd_now[1] and audio.default_id() != self.snd_now[1])
                    if want != self.snd_now[0] or renewed or moved:
                        print(f"Sound output made again: {'a device came back as a new node' if renewed else 'the default moved' if moved else 'the outputs changed'}"
                              f" ({' + '.join(want)})", flush=True)
                        if renewed:
                            audio.mix_unload()     # made anew for the new node: an existing mix is otherwise kept (mix_load)
                        self.sound_due = time.time()
            finally:
                self.snd_busy = False
        threading.Thread(target=go, daemon=True).start()

    def bt_wifi_rest(self, keys, names):
        """The Pi's Wi-Fi and Bluetooth share one chip and one antenna on 2.4 GHz, and Wi-Fi traffic cuts into
        Bluetooth sound (dropouts, crackle). While a Bluetooth output plays and a network cable is connected,
        the Wi-Fi radio rests; it is turned on again when no Bluetooth output is in use. Never when the device's
        network is Wi-Fi alone, and never against a switch made by hand (radio_toggle clears the rest)."""
        bt_on = any(isinstance(k, str) and k.startswith(audio.BT) and k in names for k in keys)
        rested = bool(self.st.get("wifi_rested"))
        if bt_on and not rested:
            if system.radio_state("wifi") == "on" and system.wired_up():
                ok, _ = system.radio_set("wifi", False)
                if ok:
                    self.save(wifi_rested=True)
                    self.__dict__.setdefault("_radio_cache", {}).pop("wifi", None)
                    self.toast(self.tr("bt.wifiRest"), 5)
        elif rested and (not bt_on or not system.wired_up()):
            # Bluetooth stopped, or the network cable was pulled while it played: the device is not left offline
            if system.radio_state("wifi") == "off":
                system.radio_set("wifi", True)
                self.__dict__.setdefault("_radio_cache", {}).pop("wifi", None)
            self.save(wifi_rested=False)

    # ------------------------------------------------------------------ the load test (device/soak.py)
    SOAK_STATUS = Path("/var/lib/armonico-soak/status.json")       # root writes it, the screen only reads
    SOAK_LENGTHS = ((15, "sk.m15"), (60, "sk.h1"), (120, "sk.h2"), (240, "sk.h4"), (480, "sk.h8"))
    soak_len = 3                     # 4 hours until changed; not kept: the card is hidden anyway
    soak_tg = True
    soak_alarm = True

    def soak_state(self):
        """status.json as the test left it, read once a second at most. A test that says it runs but is far past
        its end (the Pi went off in the middle) is "stale": the next start cleans it up."""
        at, st = getattr(self, "_soak_seen", (-1e9, {}))
        if time.monotonic() - at > 1:
            try:
                st = json.loads(self.SOAK_STATUS.read_text())
                if not isinstance(st, dict):
                    st = {}
            except (OSError, ValueError):
                st = {}
            self._soak_seen = (time.monotonic(), st)
        st = dict(st)
        if st.get("state") in ("running", "cleaning", "booting"):
            end = (st.get("t0") or st.get("at") or 0) + (st.get("total") or 0) + 30 * 60
            if time.time() > end:
                st["state"] = "stale"
        return st

    def soak_active(self):
        return self.soak_state().get("state") in ("waiting", "booting", "running", "cleaning")

    def soak_card_on(self):
        """The load test's card in Settings › System › Tests is hidden. Seven touches on the Armonico logo in About
        show it until the screen program starts again (soak_tap); a test that waits or runs shows it too, so it can
        be followed and stopped."""
        return bool(getattr(self, "soak_shown", False) or self.soak_active())

    def soak_text(self):
        tr, st = self.tr, self.soak_state()
        s = st.get("state")
        if s == "running":
            el = max(0, time.time() - (st.get("t0") or time.time()))
            hm = lambda v: f"{int(v // 3600)}:{int(v % 3600 // 60):02d}"
            ph = st.get("phase") if self.lang == "he" else st.get("phase_en")
            return tr("sk.running", a=hm(el), b=hm(st.get("total") or 0), p=ph or "…")
        if s in ("waiting", "booting", "cleaning", "stale"):
            return tr("sk." + s)
        r = st.get("result") or {}
        if r:
            return tr("sk.last", r=r.get("he") if self.lang == "he" else r.get("en") or r.get("he", ""))
        return tr("sk.idle")

    def soak_len_name(self):
        return self.tr(self.SOAK_LENGTHS[self.soak_len % len(self.SOAK_LENGTHS)][1])

    def soak_cells(self):
        tr, active = self.tr, self.soak_active()
        st = self.soak_state()
        has_zip = bool(st.get("zip")) and not active
        busy = getattr(self, "soak_busy", False)

        def flip(name):
            return lambda: setattr(self, name, not getattr(self, name))
        return [
            (tr("sk.state"), self.soak_text(), tr("sk.stateD"), None),
            (tr("sk.len"), self.soak_len_name(), tr("sk.lenD"),
             None if active else lambda: setattr(self, "soak_len", (self.soak_len + 1) % len(self.SOAK_LENGTHS))),
            (tr("sk.tg"), self.on(self.soak_tg), tr("sk.tgD"), None if active else flip("soak_tg")),
            (tr("sk.alarm"), self.on(self.soak_alarm), tr("sk.alarmD"), None if active else flip("soak_alarm")),
            (tr("sk.go"), tr("sk.goV"), tr("sk.goD"),
             None if active or busy else lambda: self.ask(tr("sk.goQ", n=self.soak_len_name()), self.soak_start)),
            (tr("sk.stop"), tr("sk.stopV"), tr("sk.stopping") if busy else "",
             (lambda: self.ask(tr("sk.stopQ"), self.soak_stop)) if active and not busy else None),
            (tr("sk.usb"), st.get("zip") if has_zip else tr("sk.busy") if active else tr("sk.none"), tr("sk.usbD"),
             self.soak_export if has_zip and not busy else None),
        ]

    def soak_run(self, action, wait_msg, done, timeout, **args):
        """One root action of the load test in the background, the page told while it runs."""
        if getattr(self, "soak_busy", False):
            return
        self.soak_busy = True
        self.toast(self.tr(wait_msg), 4)

        def go():
            try:
                okr, msg = system.request(action, timeout=timeout, **args)
            finally:
                self.soak_busy = False
                self._soak_seen = (-1e9, {})
            if not okr:
                return self.fail_toast(msg)
            try:
                data = json.loads(msg or "{}")
            except ValueError:
                data = {}
            done(data if isinstance(data, dict) else {})
        threading.Thread(target=go, daemon=True).start()

    def soak_start(self):
        minutes = self.SOAK_LENGTHS[self.soak_len % len(self.SOAK_LENGTHS)][0]
        # done: the root unit restarts the Pi two seconds later; nothing more to show
        self.soak_run("soak-start", "sk.arming", lambda d: self.toast(self.tr("sk.waiting"), 5), 120,
                      minutes=minutes, telegram=bool(self.soak_tg), alarm=bool(self.soak_alarm))

    def soak_stop(self):
        self.soak_run("soak-stop", "sk.stopping", lambda d: self.toast(self.tr("sk.stopped"), 4), 400)

    def soak_export(self):
        def done(d):
            try:
                n = int(d.get("copied") or 0)
            except (TypeError, ValueError):
                n = 0
            self.toast(self.tr("sk.usbDone", n=d.get("name", "")) if n else
                       self.tr("sk.none") if d.get("none") else self.tr("re.noStick"), 5)
        self.soak_run("soak-export", "sk.copying", done, 300)

    # ------------------------------------------------------------------ the screen recording
    def rec_card_on(self):
        """The screen recording's card in Settings › System is hidden. Ten touches on the Vesta Core Labs logo in
        About show it until the screen program starts again (vcl_tap); a recording that is running shows it too, so
        it can be seen and stopped."""
        return bool(getattr(self, "rec_shown", False) or getattr(getattr(self, "srec", None), "on", False))

    def rec_files(self):
        """The recordings on the SD card, looked at once a second at most."""
        at, n = getattr(self, "_rec_seen", (-1e9, 0))
        if time.monotonic() - at > 1:
            try:
                n = len([f for f in self.srec.folder.glob("armonico-*.mkv") if f.is_file() and not f.is_symlink()])
            except OSError:
                n = 0
            self._rec_seen = (time.monotonic(), n)
        return n

    def rec_count(self):
        return self.rec_files() if not self.srec.on else max(0, self.rec_files() - 1)

    def rec_text(self):
        if self.srec.on:
            secs = max(0, int(time.time() - self.srec.since))
            return self.tr("c.recOn", t=f"{secs // 60:02d}:{secs % 60:02d}")
        if getattr(self, "rec_saving", False) or self.srec.saving:
            got = self.srec.save_progress() if hasattr(self.srec, "save_progress") else None
            return self.tr("c.recSavingP", p=int(got[0] * 100)) if got else self.tr("c.recSaving")
        return self.tr("off")

    def rec_state(self):
        return {"state": "recording" if self.srec.on else "exporting" if getattr(self, "rec_saving", False) else "idle"}

    def screen_rec_toggle(self):
        if self.srec.on:                         # first: saving is True for the whole recording, not only after it
            self.screen_rec_stop()
        elif getattr(self, "rec_saving", False) or self.srec.saving:   # also one that ended by itself and is still put together
            self.toast(self.tr("c.recSaving"))
        elif not getattr(self, "rec_starting", False):
            self.rec_starting = True
            size = self.pg.display.get_surface().get_size()

            def go():
                try:
                    # the name in the device's own time (its city's zone), not the system clock's
                    self.rec_sound_prepare()
                    err = self.srec.start(size, self.local(), "auto")   # looks for the mic first: not on the screen's time
                finally:
                    self.rec_starting = False
                if err:
                    self.rec_sound_restore()
                    self.fail_toast(err)
                else:
                    self.toast(self.tr("rec.startedMic", mic=self.srec.mic) if self.srec.mic
                               else self.tr("rec.startedNoMic"), 6)
            threading.Thread(target=go, daemon=True).start()

    def rec_sound_prepare(self):
        """The device's sound is always what the recording takes: the monitor of the output in use. When no output is
        in use (the sound is off, or the chosen one is not connected now), AUX is switched on for the length of the
        recording, or another output that is connected when AUX is not there, and put back when it ends."""
        names = [k for k, _ in (self.snd_list or [])]
        if not names or [k for k in self.output_keys() if k in names]:
            return
        target = "aux" if "aux" in names else names[0]
        self.rec_sound_back = (self.output, self.output_mix, target)
        self.set_sound(*self.keys_setting([target]))
        end = time.monotonic() + 5
        time.sleep(0.5)                                   # the choice is applied after a short delay (set_sound)
        while time.monotonic() < end and (self.sound_due or getattr(self, "snd_applying", False)):
            time.sleep(0.1)

    def rec_sound_restore(self):
        """After the recording: the sound choice as it was before rec_sound_prepare, unless it was changed by hand since."""
        back = self.__dict__.pop("rec_sound_back", None)
        if back and self.output == back[2]:
            self.set_sound(back[0], back[1] if back[0] == "mix" else None)

    def screen_rec_stop(self):
        if not self.srec.on:
            return
        self.rec_saving = True
        self.toast(self.tr("rec.stopping"), 4)
        self.srec.stop()

    def follow_rec(self):
        """A recording that ended: moved to a USB stick when one is plugged in (by root, which mounts it)."""
        done, self.srec.done = self.srec.done, None
        if done is None:
            return
        self.rec_sound_restore()
        out, ok = done
        if not ok:
            self.rec_saving = False
            self.fail_toast("ffmpeg did not record")
            return

        def go():
            okr, msg = system.request("rec-export", timeout=900, files=[out.name])
            self.rec_saving = False
            self._rec_seen = (-1e9, 0)
            moved = 0
            try:
                moved = int(json.loads(msg or "{}").get("moved", 0)) if okr else 0
            except (ValueError, AttributeError):
                pass
            where = self.tr("rec.onStick") if moved else self.tr("rec.onSd")
            self.toast(self.tr("rec.full") + " " + where if self.srec.full else where, 7 if self.srec.full else 5)
        threading.Thread(target=go, daemon=True).start()

    def screen_rec_export(self):
        if self.srec.on or getattr(self, "rec_saving", False):
            return self.toast(self.tr("re.recBusy"))
        self.toast(self.tr("rec.exporting"), 4)

        def go():
            okr, msg = system.request("rec-export", timeout=900)
            self._rec_seen = (-1e9, 0)
            if not okr:
                return self.fail_toast(msg)
            try:
                n = int(json.loads(msg or "{}").get("moved", 0))
            except (ValueError, AttributeError):
                n = 0
            self.toast(self.tr("rec.exported", n=n) if n else self.tr("re.noStick"), 5)
        threading.Thread(target=go, daemon=True).start()

    def flip_output(self, key):
        """One output on or off in the choice (the Output page). The last one stays: no sound is its own cell."""
        keys = self.output_keys()
        keys = [k for k in keys if k != key] if key in keys else keys + [key]
        if not keys:
            self.toast(self.tr("c.outsOne"), 3)
            return
        self.set_sound(*self.keys_setting(keys))

    def output_cells(self):
        tr, keys = self.tr, self.output_keys()
        cells = []
        quality = getattr(self, "snd_bt_quality", {}) or {}
        for k, name in self.snd_list or []:
            if k == "aux":
                continue                             # AUX always plays: not a choice here (output_keys)
            d = tr("w.aux") if k == "aux" else tr("w.hdmi") if k == "hdmi" else tr("c.outsBt") if k.startswith(audio.BT) \
                else tr("c.outsUsb")
            codec, call = quality.get(k, ("", False))
            if call:
                d = tr("c.outsBtCall")               # the headset's call profile: why it sounds poor
            elif codec:
                d = tr("c.outsBtCodec", c=codec)
            cells.append((name, self.on(k in keys), d, lambda k=k: self.flip_output(k)))
        cells.append((tr("c.bt"), self.bt_cell_text(), tr("c.btD"), self.open_bt))
        # No sound is a switch: on silences everything, off brings the sound back on AUX. With AUX no longer a cell,
        # a second press was the only way back from No sound when no USB or Bluetooth device was there
        cells.append((tr("w.none"), self.on(self.output == "off"), tr("c.outsOffD"),
                      lambda: self.set_sound("aux" if self.output == "off" else "off")))
        return cells

    # ------------------------------------------------------------------ actions
    def set_sound(self, out, mix=None):
        """A new sound setting. Moving to a choice with a USB device in it from one without, the old one is
        kept: when the device is unplugged, the sound goes back to it."""
        def usb_in(o, m):
            return audio.is_ext(o) or any(audio.is_ext(k) for k in (m or []))
        old, old_mix = self.output, self.output_mix
        # a choice by hand: a USB device plugged in again no longer brings back the choice before it
        kw = {"sound_out": out, "sound_mix": list(mix) if out == "mix" and mix else None, "sound_back": None}
        if usb_in(out, mix) and not usb_in(old, old_mix if old == "mix" else None):
            kw.update(sound_prev=old, sound_prev_mix=old_mix if old == "mix" else None)
        self.save(**kw)
        # a Bluetooth device chosen here by hand: connected again on its own, with its tries starting over
        b = getattr(self, "bt", None)
        if b:
            for k in (mix if out == "mix" and mix else [out]):
                mac = audio.bt_mac(k) if isinstance(k, str) and k.startswith(audio.BT) else None
                if mac:
                    b["retry"].pop(mac, None)
                    b["manual_off"].discard(mac)
            self.bt_keep_off()
        self.sound_by_hand = True
        self.sound_tries = 0
        self.screen.media.stop()
        self.sound_due = time.time() + 0.4          # several presses in a row: applied once, after the last

    def test_tone(self):
        s = self.screen
        if not getattr(s, "test", None):
            s.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": ""}
        if self.output == "off" or not self.mixer_ready():
            self.toast(self.tr("noSound"), 4)
            return
        s.test_tone("left", quiet=True)
        old = getattr(self, "tone_timer", None)
        if old is not None:
            old.cancel()                           # pressed again: one right-side tone, not one for each press
        self.tone_timer = threading.Timer(0.9, lambda: s.test_tone("right", quiet=True))
        self.tone_timer.daemon = True
        self.tone_timer.start()
        self.toast(self.tr("playingLR"))

    def profile_cells(self):
        """Settings › Playing › Profiles: Add first, then a card for each profile; ✓ on one opens its own page
        (profile_page_cells)."""
        tr, p = self.tr, self.profiles()
        n = len(p["list"])
        cells = [(tr("pr.add"), tr("pr.addV", n=n, m=PROFILES_MAX), tr("pr.addD") if n < PROFILES_MAX else tr("pr.full"),
                  self.profile_add if n < PROFILES_MAX else None)]
        for x in p["list"]:
            here = x.get("id") == p.get("active")
            kind = tr("pr.cardHere") if here else tr("pr.cardMain") if x.get("id") == "main" else tr("pr.card")
            if x.get("locked"):
                kind += " · " + tr("pr.locked")
            cells.append((kind, x.get("name") or tr("mainProfile"), tr("pr.openPage"), lambda x=x: self.open_profile(x)))
        return cells

    def open_profile(self, x):
        self.prof_id = x["id"]
        self.open_page("prof")

    def profile_here(self):
        """The profile whose page is open, or None when it is gone (deleted from the browser meanwhile)."""
        pid = getattr(self, "prof_id", None)
        return next((x for x in self.profiles()["list"] if x.get("id") == pid), None)

    def profile_page_cells(self):
        """One profile's page. The one at the piano: Rename and its PIN (the engine edits only that one), and Delete,
        which moves the piano to the main profile first (profile_delete_here). Any other: Switch to it (its PIN first
        when it has one) and Delete, after the backup the engine keeps (lessons/piano_game.py archive_profile).
        The main profile is never deleted: the others are made from it and its files are the piano's own."""
        tr, p, x = self.tr, self.profiles(), self.profile_here()
        if x is None:
            return [(tr("pr.gone"), "", "", None)]
        name = x.get("name") or tr("mainProfile")
        here = x.get("id") == p.get("active")
        if here:
            cells = [(tr("pr.ren"), name, tr("pr.renD2"), lambda: self.profile_rename(x)),
                     (tr("pr.pinCh"), tr("pr.pinYes") if x.get("locked") else tr("pr.pinNo"), tr("pr.pinChD2"),
                      lambda: self.profile_pin_change(x))]
        else:
            cells = [(tr("pr.switchTo"), name, tr("pr.switchPin") if x.get("locked") else tr("pr.switchNow"),
                      lambda: self.profile_switch_pin(x))]
        if x.get("id") == "main":
            cells.append((tr("pr.delete"), tr("pr.notNow"), tr("pr.mainD"), None))
        elif here:
            cells.append((tr("pr.delete"), tr("pr.delV", n=name), tr("pr.delHereD"),
                          lambda: self.ask(tr("pr.delHereQ", n=name), lambda: self.profile_delete_here(x))))
        else:
            cells.append((tr("pr.delete"), tr("pr.delV", n=name), tr("pr.delD"),
                          lambda: self.ask(tr("pr.delQ", n=name), lambda: self.profile_delete_pin(x))))
        return cells

    def profile_delete_here(self, x, pin=None, main_pin=None):
        """The profile at the piano deleted: its own PIN when it has one, the main profile's PIN when that one has
        one, then the piano moves to the main profile (/profiles/switch) and the profile is deleted (the engine
        never deletes the one at the piano)."""
        tr = self.tr
        main = next((y for y in self.profiles()["list"] if y.get("id") == "main"), {"id": "main"})
        if x.get("locked") and pin is None:
            self.edit_field(tr("pr.pinFor", n=x.get("name") or x["id"]), lambda: "",
                            lambda v: self.profile_delete_here(x, v, main_pin) if v else None,
                            secret=True, keep=True, digits=True, ok=tr("chk.next"), back="settings")
            return
        if main.get("locked") and main_pin is None:
            self.edit_field(tr("pr.pinFor", n=main.get("name") or tr("mainProfile")), lambda: "",
                            lambda v: self.profile_delete_here(x, pin or "", v) if v else None,
                            secret=True, keep=True, digits=True, ok=tr("pr.delete"), back="settings")
            return

        def moved(ok, code):
            if not ok:
                self.profile_fail(code)
                return
            self.profiles()["active"] = "main"
            self.card_cache = (0.0, None)
            self.profile_delete(x, pin or "")
        self.toast(tr("pr.switching", n=main.get("name") or tr("mainProfile")), 15)
        self.screen.engine.post("/profiles/switch", {"id": "main", "pin": main_pin or ""}, on_result=moved)

    def profile_fail(self, code):
        """The engine's answer to a profile action that did not go through, said plainly."""
        tr, now = self.tr, time.time()
        if code == 403:
            self.pin_fails = [t for t in getattr(self, "pin_fails", []) if now - t < 60] + [now]
            left = max(0, 5 - len(self.pin_fails))
            self.toast(tr("pr.wrongPin", n=left) if left else tr("pr.wait"), 5)
        elif code == 429:
            self.toast(tr("pr.wait"), 5)
        elif code == 409:
            self.toast(tr("pr.busy"), 5)
        elif code == 500:
            self.toast(tr("pr.noBackup"), 6)
        elif code == "connection":
            self.toast(tr("pr.noEngine"), 6)
        else:
            self.toast(tr("pr.failed"), 5)

    def profile_switch_pin(self, x):
        if x.get("locked"):
            self.edit_field(self.tr("pr.pinFor", n=x.get("name") or self.tr("mainProfile")), lambda: "",
                            lambda pin: self.profile_switch_send(x, pin) if pin else None, secret=True, keep=True, digits=True,
                            ok=self.tr("pr.switch"), back="settings")
        else:
            self.profile_switch_send(x, "")

    def profile_switch_send(self, x, pin):
        """The engine's /profiles/switch with the PIN, on a thread; its answer as a message."""
        tr, name = self.tr, x.get("name") or self.tr("mainProfile")
        fails = getattr(self, "pin_fails", [])
        self.toast(tr("pr.switching", n=name), 15)

        def done(ok, code):
            now = time.time()
            if ok:
                self.profiles()["active"] = x["id"]
                self.card_cache = (0.0, None)
                if self.page == "prof":
                    self.sel = 0                     # its page now has the cards of the one at the piano
                self.toast(tr("pr.switched", n=name), 6)
            elif code == 403:
                self.pin_fails = [t for t in fails if now - t < 60] + [now]
                left = max(0, 5 - len(self.pin_fails))
                self.toast(tr("pr.wrongPin", n=left) if left else tr("pr.wait"), 5)
            elif code == 429:
                self.toast(tr("pr.wait"), 5)
            elif code == 409:
                self.toast(tr("pr.busy"), 5)
            else:
                self.toast(tr("pr.switchFailed"), 5)
        self.screen.engine.post("/profiles/switch", {"id": x["id"], "pin": pin}, on_result=done)

    def profile_add(self):
        """A name on the keyboard, then the PIN: 4 to 8 digits, or empty for none. The engine's /profiles/add makes it; the
        new profile is not switched to (that stays the Profile card's job)."""
        tr = self.tr
        self.edit_field(tr("pr.name"), lambda: "", self.profile_add_name, ok=tr("chk.next"), back="settings",
                        kb="he" if self.lang == "he" else "en")

    def profile_add_name(self, name):
        tr = self.tr
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:24]
        if not name:
            return
        if any((x.get("name") or "").lower() == name.lower() for x in self.profiles()["list"]):
            self.toast(tr("pr.taken", n=name), 5)
            return
        self.profile_add_pin(name)

    def profile_add_pin(self, name, note=""):
        tr = self.tr

        def got(pin):
            if not pin or re.fullmatch(r"\d{4,8}", pin):
                self.profile_add_send(name, pin)          # empty: no PIN
            else:
                self.profile_add_pin(name, tr("pr.pinRule"))
        self.edit_field(tr("pr.pinNew", n=name), lambda: "", got, secret=True, keep=True, digits=True, ok=tr("pr.addOk"),
                        note=note or tr("pr.pinRule"), back="settings")

    def profile_add_send(self, name, pin):
        tr = self.tr
        self.toast(tr("pr.adding", n=name), 15)

        def done(ok, code):
            if ok:
                self.toast(tr("pr.added", n=name), 6)
            elif code == 409:
                self.toast(tr("pr.addBusy"), 6)
            else:
                self.toast(tr("pr.addFailed"), 5)
        self.screen.engine.post("/profiles/add", {"name": name, "pin": pin}, on_result=done)

    def profile_edit_send(self, body, ok_msg, after=None):
        """The engine's /profiles/edit (only the profile at the piano, with its PIN) on a thread; its answer as a message."""
        tr = self.tr
        self.toast(tr("pr.editing"), 15)
        fails = getattr(self, "pin_fails", [])

        def done(ok, code):
            now = time.time()
            if ok:
                if after:
                    after()
                self.toast(ok_msg, 6)
            elif code == 403:
                self.pin_fails = [t for t in fails if now - t < 60] + [now]
                left = max(0, 5 - len(self.pin_fails))
                self.toast(tr("pr.wrongPin", n=left) if left else tr("pr.wait"), 5)
            elif code == 429:
                self.toast(tr("pr.wait"), 5)
            elif code == 409:
                self.toast(tr("pr.editBusy"), 6)
            else:
                self.toast(tr("pr.editFailed"), 5)
        self.screen.engine.post("/profiles/edit", body, on_result=done)

    def profile_rename(self, me):
        """A new name on the keyboard, then the PIN when the profile has one. The main profile may be left empty
        (it is then called by its usual name again)."""
        tr = self.tr
        old = me.get("name") or ""

        def got(name):
            name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:24]
            if name == old:
                return
            if not name and me.get("id") != "main":
                return
            if any(y is not me and (y.get("name") or "").lower() == name.lower() for y in self.profiles()["list"]):
                self.toast(tr("pr.taken", n=name), 5)
                return

            def send(pin):
                self.profile_edit_send({"name": name, "pin": pin}, tr("pr.renamed", n=name or tr("mainProfile")),
                                       lambda: me.__setitem__("name", name))
            if me.get("locked"):
                self.edit_field(tr("pr.pinFor", n=old or tr("mainProfile")), lambda: "", send, secret=True, keep=True, digits=True,
                                ok=tr("pr.renOk"), back="settings")
            else:
                send("")
        self.edit_field(tr("pr.renName", n=old or tr("mainProfile")), lambda: old, got, ok=tr("pr.renOk" if not me.get("locked") else "chk.next"),
                        back="settings", kb="he" if self.lang == "he" else "en")

    def profile_pin_change(self, me, cur=None, note=""):
        """The PIN of the profile at the piano: its present PIN first when it has one, then the new one (4 to 8
        digits, or empty for none)."""
        tr = self.tr
        name = me.get("name") or tr("mainProfile")
        if me.get("locked") and cur is None:
            self.edit_field(tr("pr.pinFor", n=name), lambda: "", lambda pin: self.profile_pin_change(me, pin) if pin else None,
                            secret=True, keep=True, digits=True, ok=tr("chk.next"), back="settings")
            return

        def got(new):
            if new and not re.fullmatch(r"\d{4,8}", new):
                self.profile_pin_change(me, cur or "", tr("pr.pinRule"))
                return
            self.profile_edit_send({"pin": cur or "", "new_pin": new}, tr("pr.pinSet") if new else tr("pr.pinOff"),
                                   lambda: me.__setitem__("locked", bool(new)))
        self.edit_field(tr("pr.pinNewFor", n=name), lambda: "", got, secret=True, keep=True, digits=True, ok=tr("pr.renOk"),
                        note=note or tr("pr.pinRule"), back="settings")

    def profile_delete_pin(self, x):
        if x.get("locked"):
            self.edit_field(self.tr("pr.pinFor", n=x.get("name") or x["id"]), lambda: "",
                            lambda pin: self.profile_delete(x, pin), secret=True, keep=True, digits=True, ok=self.tr("pr.delete"),
                            back="settings")
        else:
            self.profile_delete(x, "")

    def profile_delete(self, x, pin):
        """The engine's /profiles/delete (lessons/piano_game.py), on a thread; its answer as a message."""
        tr, name = self.tr, x.get("name") or x["id"]
        if x.get("locked") and not pin:
            return
        self.toast(tr("pr.saving", n=name), 60)

        def done(ok, code):
            if ok:
                p = self.profiles()
                p["list"] = [y for y in p["list"] if y.get("id") != x["id"]]
                if self.page == "prof":
                    self.page = "profiles"
                self.sel = 0
                self.toast(tr("pr.deleted", n=name), 6)
            else:
                self.profile_fail(code)
        # a backup of the profile is written and read back before anything is deleted: on the Pi's SD card that can
        # take longer than the 3 seconds other actions are given, and then a delete that went through said it failed
        self.screen.engine.post("/profiles/delete", {"id": x["id"], "pin": pin}, on_result=done, timeout=60)

    def set_city(self, c):
        self.save(city={k: c.get(k) for k in ("name", "country", "admin", "lat", "lon", "tz")})
        self.sky_st = self.compute_sky()
        self.sync_zone(by_hand=True)

    @staticmethod
    def system_zone():
        """The whole system's time zone (what timedatectl set), from /etc/localtime's link."""
        try:
            target = os.path.realpath("/etc/localtime")
        except OSError:
            return ""
        mark = "/zoneinfo/"
        return target.split(mark, 1)[1] if mark in target else ""

    def sync_zone(self, by_hand=False):
        """The Pi's own time zone (all of Linux: the logs, cron, the files' times) made the zone of the
        city chosen on the screen: any city in Israel is Asia/Jerusalem, as the city's own data says.
        Nothing is asked when the system is on that zone already; a failed change by hand says why."""
        tz = self.city.get("tz")
        if not tz or tz == self.system_zone():
            return

        def go():
            ok, msg = system.request("set-timezone", tz=tz, timeout=60)
            if not ok and by_hand:
                self.fail_toast(msg)
            elif not ok:
                print(f"Time zone {tz} not set: {msg}", flush=True)
        threading.Thread(target=go, daemon=True).start()

    def test_broker(self):
        b = self.broker
        b["state"] = "testing"
        gen = b.get("gen", 0)
        # the broker in use, as it is: its result is also the status kept for the main page
        in_use = (b["type"] == "local") == self.local_now() and not b["dirty"]

        def go():
            if b["type"] == "local":
                if not self.local_now():
                    # the local broker is chosen but not in use yet: its login is made by the switch
                    b["state"], b["ms"] = "untested", 0
                    return
                host, port, user, pw = "127.0.0.1", os.environ.get("MQTT_PORT", "1883"), \
                    os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", "")
            else:
                host, port, user, pw = b["host"], b["port"], b["user"], b["pass"]
            res, ms = system.mqtt_test(host, port, user, pw) if host else ("fail", 0)
            if b.get("gen", 0) != gen:
                return                               # the choice changed meanwhile: not its result
            b["state"], b["ms"] = {"ok": "ok", "login": "login"}.get(res, "fail"), ms
            if in_use:
                b["used"] = (b["state"], b["ms"])
        threading.Thread(target=go, daemon=True).start()

    BROKER_GRACE = 10                            # seconds after the start before no connection counts as one

    def follow_broker(self):
        """The status of the broker in use from this screen's own MQTT client (screen.py's paho client):
        connected is ✔, not connected after the first seconds is ✕. A test in progress, a choice not
        applied yet and a refused login found by a test are left as they are."""
        b, c = self.broker, getattr(self.screen, "mqtt", None)
        if c is None or not hasattr(c, "is_connected"):
            return
        if (b["type"] == "local") != self.local_now() or b["dirty"] or b["state"] == "testing":
            return
        try:
            live = bool(c.is_connected())
        except Exception:
            return
        if live and b["state"] != "ok":
            b["state"], b["ms"] = "ok", 0
        elif not live and b["state"] in ("unknown", "ok") and self.t() > self.BROKER_GRACE:
            b["state"], b["ms"] = "fail", 0
        else:
            return
        b["used"] = (b["state"], b["ms"])

    AWAY_AFTER = 60                              # seconds without the external broker before the offer
    HOME_EVERY = 300                             # seconds between two looks for the broker at home

    def follow_away(self):
        """An offer, never a switch by itself. On the external broker, a minute with no connection (the
        device away from home) offers the local broker, once until the connection comes back; only when
        a local broker was set up before, since the switch then needs no network. After a switch made
        from that offer, the broker at home is looked for every few minutes, and when it answers the
        way back is offered, once."""
        c = getattr(self.screen, "mqtt", None)
        if c is None or not hasattr(c, "is_connected") or self.job or self.confirm \
                or self.view == "setup" or self.tour_on() or getattr(self.screen, "alarm", False):
            return
        now = time.monotonic()
        if not self.local_now():
            try:
                live = bool(c.is_connected())
            except Exception:
                return
            if live:
                self._away_since, self._away_asked = None, False
                if self.st.get("away_switch"):
                    self.save(away_switch=False)  # home again by hand: nothing left to offer
                return
            if getattr(self, "_away_since", None) is None:
                self._away_since = now
            if getattr(self, "_away_asked", False) or now - self._away_since < self.AWAY_AFTER \
                    or self.t() < self.BROKER_GRACE or saved_profile("standalone").get("MQTT_LOCAL") != "yes":
                return
            self._away_asked = True
            self.woke()
            self.ask(self.tr("away.q"), lambda: (self.save(away_switch=True), self.switch_local()))
            return
        if not self.st.get("away_switch") or not self.online() or getattr(self, "_home_busy", False) \
                or now - getattr(self, "_home_seen", -1e9) < self.HOME_EVERY:
            return
        ext = saved_profile("lab")
        if not ext.get("MQTT_HOST"):
            return
        self._home_seen, self._home_busy = now, True

        def look():
            try:
                res, _ = system.mqtt_test(ext["MQTT_HOST"], ext.get("MQTT_PORT") or "1883",
                                          ext.get("MQTT_USER", ""), ext.get("MQTT_PASS", ""))
            except Exception:
                res = "fail"
            self._home_busy = False
            if res == "ok" and self.local_now() and self.st.get("away_switch"):
                self._home_found = True
        threading.Thread(target=look, daemon=True).start()

    def follow_home(self):
        """The way back, asked from the main loop once the broker at home answered (follow_away)."""
        if not getattr(self, "_home_found", False) or self.job or self.confirm or self.view == "setup" \
                or self.tour_on():
            return
        self._home_found = False

        def back():
            self.save(away_switch=False)
            self.reset_broker()                  # on the local broker: the external profile's fields
            self.broker["type"] = "external"
            self.switch_external()

        def stay():
            self.save(away_switch=False)         # asked once: staying on the local broker is a choice
            self.toast(self.tr("cancelled"), 1.2)
        self.woke()
        self.ask(self.tr("home.q"), back, stay)

    def switch_local(self, back="settings", show=True):
        labels = text_steps(self.lang, "local")

        def work(job):
            ok, msg = system.request("broker-local", on_step=lambda i, s: job.step(i), timeout=1200)
            if not ok:
                # the switch did not happen: the settings show the broker that is still in use; in the
                # setup the choice stays local (its steps follow it) and the step stays open to try again
                if back == "setup":
                    self.broker["state"] = "fail"
                    self.save(setup_step="local")
                else:
                    self.reset_broker()
                job.fail(self.root_msg(msg))
                return
            self.broker["state"] = "ok"
        # "testing" while it runs: it was marked OK before the install had even started
        self.broker.update(type="local", state="testing")
        if not show:
            return Job(self.tr("j.local"), labels, back).run(work)
        return self.start_job(self.tr("j.local"), labels, back, work)

    def switch_external(self, back="settings"):
        b = dict(self.broker)
        labels = text_steps(self.lang, "external")

        def work(job):
            ok, msg = system.request("broker-external", host=b["host"], port=b["port"], user=b["user"], **{"pass": b["pass"]},
                                     on_step=lambda i, s: job.step(i), timeout=300)
            if not ok:
                # still on the broker in use: the fields and Apply stay, to fix and try again; the setup
                # goes back to the broker's step, not on to Home Assistant over the old broker
                self.broker["state"] = "fail"
                if back == "setup":
                    self.setup_step, self.setup_sel = "broker", -1
                    self.save(setup_step="broker")
                job.fail(self.root_msg(msg))
                return
            self.broker["dirty"] = False             # only once it is saved: a failed switch kept no Apply
        return self.start_job(self.tr("j.ext"), labels, back, work)

    # ------------------------------------------------------------------ the radio chips themselves
    # The Wi-Fi and Bluetooth switches above turn the radio off and leave the chip on. These cards turn the
    # chip itself off from the next start (root_actions radio-chip: disable-wifi / disable-bt in config.txt).
    def chips_off(self):
        """The chips config.txt turns off (by Armonico's block or by a line of its own), read at most every 2 s."""
        at, val = self.__dict__.get("_chip_cache", (-1e9, set()))
        if time.monotonic() - at > 2:
            val = set()
            try:
                text = Path(BOOT_CONFIG).read_text()
            except (OSError, UnicodeDecodeError):
                text = ""
            for ln in text.splitlines():
                s = ln.split("#", 1)[0].strip().replace(" ", "")
                for kind, ov in (("wifi", "disable-wifi"), ("bluetooth", "disable-bt")):
                    if s == f"dtoverlay={ov}" or s.startswith(f"dtoverlay={ov},"):
                        val.add(kind)
            self._chip_cache = (time.monotonic(), val)
        return val

    def chip_cell(self, kind):
        tr = self.tr
        name = tr("chip.wifi" if kind == "wifi" else "chip.bt")
        if self.__dict__.get("_chip_busy", {}).get(kind):
            return (name, tr("radio.working"), tr("chip.D"), None)
        off, here = kind in self.chips_off(), self.radio_now(kind) is not None
        changed = kind in self.__dict__.get("_chip_changed", set())
        if off:
            value = tr("chip.offNext") if here else tr("off")
        elif not here:
            if not changed:
                return (name, tr("radio.none"), "", None)        # no such chip on this board
            value = tr("chip.onNext")
        else:
            value = tr("on")
        return (name, value, tr("chip.D"), lambda: self.chip_ask(kind))

    def chip_ask(self, kind):
        tr = self.tr
        if kind in self.chips_off():
            name = tr("chip.wifi" if kind == "wifi" else "chip.bt")
            if kind in system.chips_off()[1]:
                # turned off by a line of someone else's in config.txt: the device turns it on itself, after a yes
                self.ask(tr("chip.onHandQ", n=name), lambda: self.chip_apply(kind, True, hand=True))
                return
            self.ask(tr("chip.onQ", n=name), lambda: self.chip_apply(kind, True))
            return
        if kind == "wifi":
            # the connection in use goes with the chip: said before, not found out after the restart
            q = tr("chip.wifiOffLinkQ") if self.wf.get("via") == "wifi" else tr("chip.wifiOffQ")
        else:
            q = tr("chip.btOffQ")
        self.ask(q, lambda: self.chip_apply(kind, False))

    def chip_apply(self, kind, on, hand=False):
        busy = self.__dict__.setdefault("_chip_busy", {})
        if busy.get(kind):
            return
        busy[kind] = True

        def go():
            try:
                args = {"hand": True} if hand else {}
                ok, msg = system.request("radio-chip", timeout=40, kind=kind, on=bool(on), **args)
            finally:
                busy[kind] = False
                self._chip_cache = (-1e9, set())
            if ok:
                self.__dict__.setdefault("_chip_changed", set()).add(kind)
                self.ask(self.tr("chip.restartQ"), self.restart_device)
            else:
                self.toast(self.tr("chip.byHand" if "by-hand" in str(msg) else "chip.failed"), 5)
        threading.Thread(target=go, daemon=True).start()

    def find_ha(self):
        if not self.online():
            self.toast(self.tr("c.noNet"))
            return
        self.ha["searching"] = True

        def go():
            res = system.find_ha()
            self.ha["found"] = res if res.get("found") == "yes" else {}
            self.ha["searching"] = False
            if self.ha["found"] and self.broker["type"] == "external" and not self.broker["host"]:
                self.broker["host"] = self.ha["found"].get("host", "")
            self.toast(self.tr("foundAgain", h=res.get("host", "")) if self.ha["found"] else self.tr("notFound"))
        threading.Thread(target=go, daemon=True).start()

    def ha_url(self):
        f = self.ha.get("found") or {}
        return f.get("url") or os.environ.get("HA_URL", "") or (f"http://{f['host']}:8123" if f.get("host") else "")

    def run_ha_setup(self, back="settings"):
        token, url = self.ha["token"], self.ha_url()
        labels = text_steps(self.lang, "ha")

        def work(job):
            job.step(0)
            code, lines = system.ha_run("check", url, token, timeout=60)
            if code:
                job.fail(self.root_msg(next((t for k, t in lines if k == "fail"), "Home Assistant did not answer")))
                return
            job.step(1)
            seen = [0]

            def line(kind, text):
                seen[0] += 1
                job.step(min(3, 1 + seen[0] // 3))
            code, lines = system.ha_run("package", url, token, on_line=line)
            if code:
                job.fail(self.root_msg(next((t for k, t in lines if k == "fail"), "the setup failed")))
                return
            job.step(4)
            # the token goes to root's own file (never to the settings), like the bot's, so the Telegram
            # step after a restart needs no typing; root checks it with Home Assistant before it keeps it
            kept, _ = system.request("ha-keep", timeout=60, token=token, url=url)
            self.ha["token_mem"] = "" if kept else token     # in memory only when root did not keep it
            self.ha["token"] = ""
            self.save(ha_done=True, ha_kept=bool(kept))
            job.finish()
        return self.start_job("Home Assistant", labels, back, work)

    def run_tg_setup(self, back="settings"):
        token = self.tg["token"]
        if not (self.ha.get("token_mem") or self.ha.get("token") or self.st.get("ha_kept")):
            # Home Assistant's token is not kept (root did not, or it was forgotten), so it is asked for here,
            # before the two minutes of waiting for Start (it failed only at the end of them)
            self.edit_field(self.tr("c.tokenE"), lambda: self.ha["token"], lambda v: self.ha.update(token=v),
                            secret=True, phone=True, note=self.tr("tg.haAgain"))
            return None
        labels = text_steps(self.lang, "tg", bot="…")

        def work(job):
            tg = system.telegram_module()
            job.step(0)
            me = tg.tg(token, "getMe")
            bot = me.get("username", "")
            job.labels[2] = text_steps(self.lang, "tg", bot=bot)[2]
            job.step(1)
            try:
                tg.profile_steps(token, me)
            except Exception:
                pass
            job.step(2)
            chats = system.tg_wait_chat(tg, token, stop=lambda: self.job is not job)
            if not chats:
                job.fail(self.L("no message arrived", "לא הגיעה הודעה"))
                return
            job.step(3)
            ha_token = self.ha.get("token_mem") or self.ha.get("token")
            if not ha_token and self.st.get("ha_kept"):
                # root connects the bot with the Home Assistant token it keeps; the screen never sees it
                ok, msg = system.request("ha-telegram-connect", timeout=180, token=token, chats=chats)
                if not ok:
                    job.fail(self.L("the Home Assistant access token is needed again", "צריך שוב את מפתח הגישה ל-Home Assistant")
                             if msg == "no-token" else self.root_msg(msg))
                    return
            else:
                if not ha_token:
                    job.fail(self.L("the Home Assistant access token is needed again", "צריך שוב את מפתח הגישה ל-Home Assistant"))
                    return
                import ha_setup
                ha = None
                for cand in ha_setup.candidates(self.ha_url()):
                    ha = ha_setup.HA(cand, ha_token)
                    try:
                        ha.rest("GET", "/api/config")
                        break
                    except ha_setup.HAError:
                        ha = None
                if ha is None:
                    job.fail(self.root_msg("Home Assistant did not answer"))
                    return
                done, info = ha_setup.telegram_connect(ha, token, chats)
                if not done:
                    job.fail(info)
                    return
            job.step(4)
            for cid in chats:
                tg.tg(token, "sendMessage", {"chat_id": cid, "text": "Armonico Pi is connected. Try /pianostatus"})
            self.save(tg_done=True, tg_bot=bot, tg_chats=chats)
            # the token goes to root's own file (never to the settings), so a test after a restart
            # needs no typing; root checks it with Telegram before it keeps it
            kept, _ = system.request("telegram-keep", timeout=60, token=token)
            self.save(tg_kept=bool(kept))
            job.finish()
        return self.start_job(self.tr("g.tg"), labels, back, work)

    def tg_test(self):
        token, chats = self.tg["token"], self.st.get("tg_chats") or []
        if not token and self.st.get("tg_kept"):
            # after a restart: root sends the fixed test message with the token it keeps, which the
            # screen never sees
            def by_root():
                ok, msg = system.request("telegram-test", timeout=60)
                why = {"no-token": "tg.tokenAgain", "no-chats": "tg.noChats", "too soon": "tg.tooSoon"}.get(msg)
                self.toast(self.tr("testSent") if ok else self.tr(why) if why else self.tr("testNotSent", e=self.root_msg(msg)), 4)
            threading.Thread(target=by_root, daemon=True).start()
            return
        if not token or not chats:
            self.toast(self.tr("tg.tokenAgain") if chats and self.st.get("tg_done") else self.tr("tokenFirst"), 5)
            return

        def go():
            try:
                tg = system.telegram_module()
                for cid in chats:
                    tg.tg(token, "sendMessage", {"chat_id": cid, "text": "Armonico Pi is connected"})
                self.toast(self.tr("testSent"))
            except Exception as e:
                self.toast(self.tr("testNotSent", e=e), 4)
        threading.Thread(target=go, daemon=True).start()

    def tg_forget(self):
        def go():
            ok, msg = system.request("telegram-forget", timeout=30)
            if ok:
                self.save(tg_kept=False)
            self.toast(self.tr("tg.forgot") if ok else self.root_msg(msg), 3)
        threading.Thread(target=go, daemon=True).start()

    def ha_forget(self):
        def go():
            ok, msg = system.request("ha-forget", timeout=30)
            if ok:
                self.save(ha_kept=False)
            self.toast(self.tr("ha.forgot") if ok else self.root_msg(msg), 3)
        threading.Thread(target=go, daemon=True).start()

    def reset_song(self, name):
        """One song from the start: its parts, scores and history (the lesson engine's /progress/reset)."""
        def done(ok, code):
            self.toast(self.tr("resetSongDone") if ok or code == 404 else self.tr("resetBusy") if code == 409 else self.tr("failed"), 3)
        self.screen.engine.post("/progress/reset", {"song": name}, on_result=done)

    def reset_all_songs(self):
        songs = [x.get("name") for x in self.screen.engine.overview.get("songs", []) if x.get("name")]
        for name in songs:
            self.screen.engine.post("/progress/reset", {"song": name})
        self.toast(self.tr("resetSongsDone", n=len(songs)), 3)

    def full_reset(self):
        """Everything deleted and installed again. From here the reset's own screen shows the steps
        (device/full_reset.sh); this program is one of the things it deletes."""
        from . import system
        if self.screen.lesson_on():
            self.toast(self.tr("resetBusy"), 3)
            return
        # no network needed: the install afterwards comes from the offline kit kept on the device
        # (install.sh --offline-kit), and full_reset.sh refuses to start when the kit is missing
        # and there is no network either
        self.toast(self.tr("fullResetGo"), 6)

        def go():
            ok, msg = system.request("full-reset", timeout=60)
            if not ok:
                self.toast(self.tr("noRoot") if msg == "no-root" else self.tr("failed"), 4)
        threading.Thread(target=go, daemon=True).start()

    def check_update(self):
        if not self.online():
            self.toast(self.tr("needsNet"))
            return
        self.toast(self.tr("checking"))

        def go():
            import diag
            try:
                self.update = diag.update_available()
            except Exception:
                self.update = None
                self.toast(self.tr("updCheckFail"), 3)   # not "No new version": the check itself failed
                return
            if self.update:
                self.upd_due = True              # the screen moves on the drawing loop (follow_upd), not here
            elif (getattr(diag, "_update_memo", None) or {}).get("value") is False:
                self.toast(self.tr("updCheckFail"), 3)   # GitHub did not answer: diag keeps that as False
            else:
                self.toast(self.tr("noNewVersion"))
        threading.Thread(target=go, daemon=True).start()

    # ------------------------------------------------------------------ the 12 groups
    def settings_cells(self):
        tr = self.tr
        s = self.screen
        ext = self.broker["type"] == "external"      # the choice on the Connection page
        ext_now = not self.local_now()               # the broker in use: the rest of the settings follow it
        on = self.online()
        ha_ok, tg_ok = bool(self.st.get("ha_done")), bool(self.st.get("tg_done"))
        snd = self.output_label()
        vol = self.volume_now()
        restn = tr("rs." + self.rest_content)
        sleep = self.st.get("sleep", 5) if self.st.get("sleep", 5) in SLEEPS else 5
        last = self.last_restore()
        bad = any(c.level == 2 for c in self.checks)
        ver = self.version()
        found = self.ha.get("found") or {}
        stop = load_stop(self)
        city = self.city
        dsp = self.connected_displays()                # the screen's choice shows only while 2 are connected
        day = self.st.get("brightness", 100) if self.st.get("brightness", 100) in DAY_LEVELS else 100
        P = {
            "top": [
                # 8 groups (12 before): the screen and the rest screen together, the network with the
                # broker, Home Assistant and Telegram behind it, night mode with the look
                (tr("g.look"), tr("th." + self.theme) + " · " + restn, tr("d.look"), lambda: self.open_page("look")),
                (tr("g.sound"), f"{snd} · {vol}%", tr("d.sound"), lambda: self.open_page("sound")),
                (tr("g.play"), self.keyboard_text() + " · " + self.profile_name(), tr("d.play"), lambda: self.open_page("play")),
                (tr("g.place"), f"{city['name']} · {self.hm()}" + (" · " + self.alarm()["time"] if self.alarm()["on"] else ""),
                 tr("d.place"), lambda: self.open_page("place")),
                (tr("g.net"), (self.ip or tr("notConnected")) + " · " + (tr("external") if ext_now else tr("local")),
                 tr("d.net"), lambda: self.open_page("net")),
                (tr("g.bkp"), tr("restoredAt", t=last.get("at_hm", "")) if last else tr("oneFile"), tr("d.bkp"), lambda: self.open_page("bkp")),
                (tr("g.sys"), tr("problemFound") if bad else tr("updAvail", v=self.update[0]) if self.update else f"Armonico {ver}",
                 tr("d.sys"), lambda: self.open_page("sys")),
                (tr("g.about"), "Armonico Pi", tr("d.about"), self.open_about),
            ],
            "look": [
                (tr("c.lang"), "עברית" if self.lang == "he" else "English", tr("c.langD"),
                 lambda: self.save(lang="en" if self.lang == "he" else "he")),
                (tr("c.theme"), tr("th." + self.theme), tr("c.themeD"), lambda: self.save(theme=nxt(THEMES, self.theme))),
                (tr("c.zoom"), f"{self.zoom_pct()}%", tr("c.zoomD"), lambda: self.open_page("zoom")),
                (tr("c.bar"), tr("bar.hidden") if self.st.get("bar_hide") else tr("bar.shown"), tr("c.barD"),
                 lambda: self.save(bar_hide=not self.st.get("bar_hide"))),
                (tr("c.bright"), f"{day}%", tr("c.brightD"), lambda: self.save(brightness=nxt(DAY_LEVELS, day))),
                (tr("c.night"), self.night_text(), tr("c.nightD"), lambda: self.open_page("night")),
                (tr("c.rest"), restn + " · " + tr("ch." + self.kind), tr("c.restD2"), lambda: self.open_page("rest")),
                (tr("c.sleep"), tr("c.sleepV", n=sleep) if sleep else tr("c.never"), tr("c.sleepD"),
                 lambda: self.save(sleep=nxt(SLEEPS, sleep))),
            ] + ([(tr("c.display"), (lambda p: tr("dsp.both") if p == "both" else p)((self.st.get("display") or {}).get("pick") or dsp[0]["name"]), tr("c.displayD"),
                   lambda: self.open_displays("settings"))] if len(dsp) > 1 else []),
            "sound": [
                (tr("c.out"), snd, tr("c.outD") if self.hdmi_here() else tr("c.outDaux"), lambda: self.open_page("outs")),
                (tr("c.vol"), f"◂ {vol}% ▸" if self.vol_adjust else f"{vol}%", tr("c.volOn") if self.vol_adjust else tr("c.volD"),
                 lambda: setattr(self, "vol_adjust", not self.vol_adjust)),
            ],
            "play": [
                (tr("c.kb"), self.keyboard_text(), tr("c.kbD"), lambda: self.open_page("kbd")),
                (tr("c.voice"), ("◂ " + tr("vc.%d" % self.voice()) + " ▸") if self.voice_adjust else tr("vc.%d" % self.voice()),
                 tr("c.voiceOn") if self.voice_adjust else tr("c.voiceD"),
                 lambda: setattr(self, "voice_adjust", not self.voice_adjust)),
                (tr("c.pedal"), self.on(s.pedal), tr("c.pedalD"), lambda: s.pub("piano/device/pedal/set", "off" if s.pedal else "on")),
                (tr("g.less"), self.lessons_text(), tr("d.less"), lambda: self.open_page("less")),
                (tr("c.stop"), tr("b." + stop) if stop in STOPS else tr("b.keyN", n=int(stop[4:]) + 1), tr("c.stopD"),
                 lambda: self.save(stop_button=nxt(STOPS, stop))),
                (tr("c.profiles"), self.profile_name() + " · " + (tr("c.profDelV1") if len(self.profiles()["list"]) == 1 else tr("c.profDelV", n=len(self.profiles()["list"]))),
                 tr("c.profilesD"), lambda: self.open_page("profiles")),
            ] + ([(tr("px.title"), "Plex", tr("px.rowD"), self.open_plex)] if self.px_on else []),
            "profiles": self.profile_cells(),
            "prof": self.profile_page_cells(),
            "rest": [
                (tr("c.restWhat"), tr("rs.nasaLong") if self.rest_content == "nasa" else restn, tr("c.restD"), self.next_rest),
                (tr("c.char"), tr("ch." + self.kind), tr("c.charD"), lambda: self.save(character=nxt(CHARS, self.kind))),
            ],
            "less": self.lesson_cells(),
            "night": self.night_cells(),
            "place": [
                (tr("c.city"), f"{city['name']}, {city.get('country', '')}".strip(", "), tr("c.cityD"), lambda: self.open_place("settings")),
                (tr("c.time"), self.hm(), tr("c.timeOn") if on else tr("c.timeOff"),
                 None if on else (lambda: self.open_setup("time", single=True))),
                (tr("c.alarm"), self.alarm_text(), tr("c.alarmD"), lambda: self.open_page("alarm")),
            ],
            "alarm": self.alarm_cells(),
            "kbd": self.keyboard_cells(),
            "outs": self.output_cells(),
            "conn": [(tr("c.ssh"), tr("c.sshV"), tr("c.sshD"), lambda: self.open_setup("ssh", single=True))] +
                    self.conn_cells(ext, on, found) + [
                # set up once and then on the local broker: kept, and back when the external one is
                (tr("g.ha"), (tr("setUp") if ha_ok else tr("notSetUp")) if ext_now else tr("offLocal") if ha_ok else tr("notSetUp"),
                 tr("d.haExt") if ext_now else tr("d.haLocal"), lambda: self.open_page("ha")),
                (tr("g.tg"), (tr("connected") if tg_ok else tr("notSetUp")) if ext_now else tr("offLocal") if tg_ok else tr("notSetUp"),
                 (tr("d.tg") if ha_ok else tr("d.tgNo")) if ext_now else tr("d.haLocal"), lambda: self.open_page("tg")),
            ],
            "ha": ([(tr("c.status"), tr("setUp") if ha_ok else tr("notSetUp"), "", None),
                    (tr("c.found"), (f"{found.get('host')}" if found else tr("c.searching") if self.ha["searching"]
                                     else tr("c.notFound") if found == {} else "—") if on else tr("c.noNet"),
                     tr("c.searchAgain"), self.find_ha),
                    (tr("c.token"), masked(self.ha["token"]) or tr("empty"), tr("c.tokenD"),
                     lambda: self.edit_field(tr("c.tokenE"), lambda: self.ha["token"], lambda v: self.ha.update(token=v),
                                             secret=True, phone=True)),
                    (tr("c.auto"), tr("c.autoV"), tr("c.run") if self.ha["token"] else tr("c.tokenFirst"),
                     lambda: self.run_ha_setup() if self.ha["token"] and self.ha_url() else
                     self.toast(tr("tokenFirst") if not self.ha["token"] else tr("haUrlFirst")))]
                   + ([(tr("tg.kept"), tr("tg.keptV"), tr("tg.forget"),
                        lambda: self.ask(tr("ha.forgetQ"), self.ha_forget))] if self.st.get("ha_kept") else [])
                   if ext_now else [(tr("c.needExt"), tr("c.nowLocal"), tr("c.openConn"), lambda: self.open_page("conn"))]),
            "tg": ([(tr("c.status"), tr("connected") if tg_ok else tr("notSetUp"), "", None),
                    (tr("c.bot"), masked(self.tg["token"]) or (("@" + self.st["tg_bot"]) if self.st.get("tg_bot") else tr("empty")),
                     tr("c.botD"), lambda: self.edit_field(tr("c.botE"), lambda: self.tg["token"],
                                                           lambda v: self.tg.update(token=v), secret=True, phone=True)),
                    (tr("c.tgSetup"), tr("c.tgSetupV"), tr("c.run") if self.tg["token"] else tr("c.tokenFirst"),
                     lambda: self.run_tg_setup() if self.tg["token"] else self.toast(tr("tokenFirst"))),
                    (tr("c.tgTest"), tr("c.tgTestV"),
                     tr("c.send") if self.tg["token"] or self.st.get("tg_kept") else tr("c.tokenFirst"), self.tg_test)]
                   + ([(tr("tg.kept"), tr("tg.keptV"), tr("tg.forget"),
                        lambda: self.ask(tr("tg.forgetQ"), self.tg_forget))] if self.st.get("tg_kept") else [])
                   if ha_ok and ext_now else
                   [(tr("c.needExt"), tr("c.nowLocal"), tr("c.openConn"), lambda: self.open_page("conn"))] if not ext_now else
                   [(tr("c.needHA"), tr("c.throughHA"), tr("c.openHA"), lambda: self.open_page("ha"))]),
            "net": [
                # 8 cards (14 before): the address in the connection's card, each radio's switch in its own card,
                # Home Assistant and Telegram on the Connections page, the two chips on a page of their own
                (tr("c.link"), self.link_text()[0], self.link_line(), None),
                self.radio_cell("wifi"),
                self.radio_cell("bluetooth"),
                (tr("c.name"), f"{self.ip}:{LESSON_UI_PORT}" if self.ip else tr("c.noNet"), tr("c.nameQr") if self.ip else "",
                 self.open_qr if self.ip else None),
                # the code a lesson screen in a browser asks for once; without it there is no way in
                (tr("c.code"), self.lesson_code() or tr("c.codeOff"), tr("c.codeD") if self.lesson_code() else tr("c.codeOffD"),
                 (lambda: self.ask(tr("c.codeQ"), self.new_lesson_code)) if self.lesson_code() else None),
                (tr("g.conn"), (tr("external") if ext_now else tr("local")) + " · " + self.broker_text(), tr("d.conn"),
                 lambda: self.open_page("conn")),
                (tr("c.offline"), tr("c.offlineV"), tr("c.open"), lambda: self.open_net("settings")),
                (tr("g.chips"), self.chips_text(), tr("c.open"), lambda: self.open_page("chips")),
            ],
            "chips": [self.chip_cell("wifi"), self.chip_cell("bluetooth")],
            "bkp": [
                (tr("c.backup"), tr("oneFile"), tr("c.backupD"), lambda: self.open_bk("save")),
                (tr("c.restore"), tr("c.restoreV"), tr("c.restoreD"), lambda: self.open_bk("restore")),
                (tr("c.undo"), (last or {}).get("file", "").rsplit("/", 1)[-1] if last and last.get("can_undo") else tr("c.noRestore"),
                 tr("c.undoD") if last and last.get("can_undo") else tr("c.undoNo"),
                 (lambda: self.ask(tr("c.undoQ"), self.undo_restore)) if last and last.get("can_undo") else None),
            ],
            "sys": [
                # 7 cards (11 before): the three diagnosis windows are one, the recording and its export
                # share a page, so do the setup wizard and the opening tour
                (tr("c.devtest"), tr("problemFound") if bad else tr("c.allFine"), tr("c.devtestD"), lambda: self.open_page("test")),
                (tr("c.update"), f"{ver} → {self.update[0]}" if self.update else f"Armonico {ver}",
                 (tr("c.updWhat") if self.update else tr("c.updCheck")) if on else tr("c.needsNet"),
                 lambda: self.open_upd("settings") if self.update else self.check_update()),
            ] + ([
                (tr("c.rec"), self.rec_text(), tr("c.recPageD"), lambda: self.open_page("rec")),
            ] if self.rec_card_on() else []) + [
                (tr("g.guide"), tr("c.guideV"), tr("c.open"), lambda: self.open_page("guide")),
                (tr("g.reset"), tr("c.resetV"), tr("c.open"), lambda: self.open_page("reset")),
            ],
            "reset": [
                # one restart, of the whole Pi: it starts everything again. The restart of the services
                # alone and the reset of the whole device were dropped: the first did less
                # than this one, the second is the installer's --purge.
                (tr("c.restart"), tr("c.restartV"), tr("c.restartD"), lambda: self.ask(tr("restartQ"), self.restart_device)),
                (tr("c.resetSongs"), tr("c.resetSongsV"), tr("c.resetSongsD"), lambda: self.ask(tr("resetSongsQ"), self.reset_all_songs)),
                (tr("c.fullReset"), tr("c.fullResetV"), tr("c.fullResetD"),
                 lambda: self.ask(tr("fullResetQ1"), lambda: self.ask(tr("fullResetQ2"), self.full_reset))),
            ],
            "zoom": [
                (tr("c.zoom"), f"◂ {self.zoom_pct()}% ▸" if self.zoom_adjust else f"{self.zoom_pct()}%",
                 tr("c.zoomOn") if self.zoom_adjust else tr("c.zoomAdj"),
                 lambda: setattr(self, "zoom_adjust", not self.zoom_adjust)),
                (tr("c.zoomReset"), "100%", tr("c.zoomResetD") if self.zoom_pct() != 100 else tr("c.zoomIs100"),
                 (lambda: self.save(zoom=100)) if self.zoom_pct() != 100 else None),
            ],
            "test": [
                (tr("tm.both"), tr("tm.bothV"), tr("tm.bothD"), lambda: self.open_check("settings", "both")),
                (tr("tm.auto"), tr("tm.autoV"), tr("tm.autoD"), lambda: self.open_check("settings", "auto")),
                (tr("tm.hand"), tr("tm.handV"), tr("tm.handD"), lambda: self.open_check("settings", "guided")),
                (tr("tm.hw"), tr("tm.hwV"), tr("tm.hwD"), self.open_test),
                (tr("dc.t"), tr("dc.v"), tr("dc.d"), self.open_deep),
                (tr("tm.quick"), tr("tm.quickV"), tr("tm.quickD"), lambda: self.open_page("quick")),
            ] + ([
                (tr("sk.t"), self.soak_text(), tr("sk.d"), lambda: self.open_page("soak")),
            ] if self.soak_card_on() else []),
            "soak": self.soak_cells(),
            "quick": [(tr("qa." + k), tr("qa." + k + "V"), tr("qa.go"), fn) for k, fn in self.quick_areas()],
            "rec": [
                (tr("c.rec"), self.rec_text(), tr("c.recStopD") if self.rec_state().get("state") == "recording" else tr("c.recD"),
                 self.screen_rec_toggle),
                (tr("c.recExport"), tr("c.recN", n=self.rec_count()) if self.rec_count() else tr("c.recNone"),
                 tr("c.recExportD"), self.screen_rec_export if self.rec_count() else None),
            ],
            "guide": [
                (tr("c.wizard"), tr("c.wizardV"), tr("c.open"), lambda: self.open_setup("lang")),
                (tr("c.tour"), tr("c.tourV"), tr("c.tourD"), self.tour_again),
            ],
        }
        return P

    ZOOMS = tuple(range(80, 151, 10))

    def zoom_pct(self):
        v = self.st.get("zoom", 100)
        return v if v in self.ZOOMS else 100

    def zoom_move(self, d):
        z = self.zoom_pct()
        i = max(0, min(len(self.ZOOMS) - 1, self.ZOOMS.index(z) + d))
        self.save(zoom=self.ZOOMS[i])

    zoom_adjust = False

    def quick_areas(self):
        """The quick check of one area: (key, what opens it). Each runs only that area's automatic checks and
        guided steps through the full check, so the report is the same."""
        def chk(areas, steps, topic):
            return lambda: self.open_check("settings", "both", (areas, steps), topic)
        return [("audio", chk(["sound", "samples"], ["right", "left", "quality"], "audio")),
                ("touch", chk([], ["touch"], "touch")),
                ("keys", chk(["gpio", "midi"], ["keys", "buttons", "usb"], "keys")),
                ("net", chk(["network", "internet", "broker"], [], "net")),
                ("bt", chk(["bluetooth"], [], "bt")),           # a check that looks, not the pairing page (it is a link there)
                ("screen", chk(["display"], ["bright"], "screen")),
                ("svc", chk(["services", "lesson_screen"], [], "svc"))]

    def night_text(self):
        w = self.night_when()
        if w == "off":
            return self.tr("nm.off")
        if w == "hours":
            a, b = self.night_hours()
            return f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"
        return self.tr("nm." + w)

    def night_status(self):
        """The line over Night mode's rows: whether it is on, and when."""
        tr, when = self.tr, self.night_when()
        fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        if when == "off":
            return tr("nm.stOff")
        if when == "hours":
            a, b = self.night_hours()
            return tr("nm.stHours", a=fmt(a), b=fmt(b))
        if when == "manual":
            return tr("nm.stManual")
        st = self.sky_st
        if st is not None and st.rise and st.set:
            return tr("nm.stSun", a=self.hm(st.set), b=self.hm(st.rise))
        return tr("nm.stSunNo")

    def night_cells(self):
        """Night mode's page: the switch, what turns it on, the 2 times (chosen hours only) and how much it
        dims (brightness and volume together). Nothing below the switch can be touched while it is off."""
        tr, on, auto = self.tr, self.night_on(), self.night_auto()
        a, b = self.night_hours()
        fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        cells = [
            (tr("c.night"), tr("on") if on else tr("off"), "", lambda: self.save(night_on=not on)),
            (tr("c.nmAuto"), tr("nm." + auto), tr("nm." + auto + "D"),
             (lambda: self.save(night_auto=NIGHT_AUTO[(NIGHT_AUTO.index(auto) + 1) % len(NIGHT_AUTO)])) if on else None),
        ]
        if auto == "hours":
            cells += [(tr("c.nmFrom"), fmt(a), tr("c.nmMove"), (lambda: self.save(night_from=(a + 30) % 1440)) if on else None),
                      (tr("c.nmTo"), fmt(b), tr("c.nmMove"), (lambda: self.save(night_to=(b + 30) % 1440)) if on else None)]
        lvl = self.night_level()
        bright, vol = NIGHT_STRENGTH[lvl - 1]
        cells.append((tr("c.nmLevel"), tr("nm.bright", b=bright) + " · " + tr("nm.sound", v=vol), tr("c.nmLevelD"),
                      (lambda: self.save(night_level=lvl % len(NIGHT_STRENGTH) + 1)) if on else None))
        return cells

    def night_draw(self, cells, can):
        """Night mode's page drawn as the approved demo: the status line, then full-width rows: the switch,
        Automatic, Hours with its 2 times as touchable pills, and Dimming with its 3 steps."""
        P, cv, tr = self.P, self.cv, self.tr
        on, auto = self.night_on(), self.night_auto()
        x, w = 24, 752
        gold = P["gold"]
        faint = tuple(P["dim"][:3]) + (90,)
        cv.text(cv.fit(self.night_status(), w, 15, True), x + 4, 78, 15, gold, True)
        y = 90
        # the switch
        h = 58
        self.hit(x, y, w, h, (lambda: self.sel_act(0)) if can[0] else None)
        cv.rect(x, y, w, h, P["panel"], r=12)
        if self.sel == 0:
            cv.rect(x, y, w, h, P["sel"], r=12, width=2.5)
        cv.text(tr("c.night"), x + 18, y + 36, 18, P["ink"], True)
        tx, ty = x + w - 18 - 54, y + 15
        cv.rect(tx, ty, 54, 28, gold if on else faint, r=14)
        cv.circle(tx + (40 if on else 14), ty + 14, 11, (26, 20, 5) if on else P["dim"])
        y += h + 8
        # automatic
        h = 64
        dim = not on
        ink, soft = (P["dim"], P["dim"]) if dim else (P["ink"], P["dim"])
        if can[1]:
            self.hit(x, y, w, h, lambda: self.sel_act(1))
        cv.rect(x, y, w, h, P["panel"], r=12)
        if self.sel == 1 and can[1]:
            cv.rect(x, y, w, h, P["sel"], r=12, width=2.5)
        cv.text(tr("c.nmAuto"), x + 18, y + 28, 18, ink, True)
        cv.text(cv.fit(tr("nm." + auto + "D"), 470, 12), x + 18, y + 50, 12, soft)
        cv.text(cv.fit(tr("nm." + auto), 220, 17, True), x + w - 18, y + 36, 17, faint if dim else gold, True, "right")
        y += h + 8
        # the 2 times
        if auto == "hours":
            h = 58
            cv.rect(x, y, w, h, P["panel"], r=12)
            cv.text(tr("c.nmHours"), x + 18, y + 26, 18, ink, True)
            cv.text(cv.fit(tr("c.nmMove"), 360, 12), x + 18, y + 46, 12, soft)
            pw, ph = 92, 36
            px = x + w - 18 - (2 * pw + 50)
            for k, (m, lab) in enumerate(((self.night_hours()[0], "c.nmFrom"), (self.night_hours()[1], "c.nmTo"))):
                bx = px + k * (pw + 50)
                idx = 2 + k
                framed = self.sel == idx and can[idx]
                if can[idx]:
                    self.hit(bx, y + 11, pw, ph, lambda idx=idx: self.sel_act(idx))
                cv.rect(bx, y + 11, pw, ph, P["sel"] if framed else faint, r=8, width=2.5 if framed else 1.5)
                cv.text(f"{m // 60:02d}:{m % 60:02d}", bx + pw / 2, y + 36, 17, faint if dim else P["ink"], True, "center")
            cv.text(tr("nm.to"), px + pw + 25, y + 35, 15, soft, False, "center")
            y += h + 8
        # dimming
        h = 76
        last = len(cells) - 1
        if can[last]:
            self.hit(x, y, w, h, lambda: self.sel_act(last))
        cv.rect(x, y, w, h, P["panel"], r=12)
        if self.sel == last and can[last]:
            cv.rect(x, y, w, h, P["sel"], r=12, width=2.5)
        cv.text(tr("c.nmLevel"), x + 18, y + 28, 18, ink, True)
        cv.text(cv.fit(tr("c.nmLevelD"), 470, 12), x + 18, y + 48, 12, soft)
        lvl = self.night_level()
        bright, vol = NIGHT_STRENGTH[lvl - 1]
        cv.text(tr("nm.bright", b=bright), x + w - 18, y + 32, 17, faint if dim else gold, True, "right")
        cv.text(tr("nm.sound", v=vol), x + w - 18, y + 54, 13, faint if dim else soft, False, "right")
        for k in range(len(NIGHT_STRENGTH)):
            cv.rect(x + 18 + k * 28, y + 60, 22, 6, gold if (k + 1 == lvl and not dim) else faint, r=3)

    def sel_act(self, i):
        """A touch on row i of a settings page, as the ✓ key would act on it."""
        self.sel = i
        fn = self.settings_cells()[self.page][i][3]
        if fn:
            fn()

    def conn_cells(self, ext, on, found):
        tr, b = self.tr, self.broker
        current_local = os.environ.get("MQTT_LOCAL", "no") == "yes"

        def edit(label, key, secret=False, digits=False):
            def set_(v):
                b[key] = v
                b["state"], b["dirty"] = "untested", True
                b["gen"] = b.get("gen", 0) + 1
            return lambda: self.edit_field(label, lambda: b[key], set_, secret=secret, digits=digits)

        def toggle():
            if ext and current_local:
                self.reset_broker()                  # only chosen, never applied: back to the local in use
            elif ext:
                self.ask(tr("c.toLocalQ"), self.switch_local)
            else:
                b.update(type="external", state="untested", gen=b.get("gen", 0) + 1)
                if on and not b["host"] and found:
                    b["host"] = found.get("host", "")
                if on and self.ha.get("found") is None:
                    self.find_ha()
        cells = [(tr("c.broker"), tr("c.brokerExt") if ext else tr("c.brokerLocal"), tr("c.toLocal") if ext else tr("c.toExt"), toggle)]
        if ext:
            cells += [(tr("c.addr"), (b["host"] or tr("empty")) + ":" + str(b["port"]),
                       tr("c.editFound", h=found.get("host")) if found else tr("c.edit"), edit(tr("c.addrE"), "host")),
                      (tr("c.user"), b["user"] or tr("empty"), tr("c.userD"), edit(tr("c.userE"), "user")),
                      (tr("c.pass"), "●●●●●●" if b["pass"] else tr("empty"), tr("c.edit"), edit(tr("c.pass"), "pass", True)),
                      (tr("c.port"), str(b["port"]), tr("c.portD"), edit(tr("c.port"), "port", digits=True))]
        cells.append((tr("c.status"), self.broker_text(), tr("c.testNow"), self.test_broker))
        if ext and (current_local or b["dirty"]):
            cells.append((tr("c.apply"), b["host"] or tr("empty"), tr("c.applyD"),
                          (lambda: self.ask(tr("c.applyQ"), self.switch_external)) if b["host"] else None))
        return cells

    def lesson_code(self):
        """The lesson screen's code, or "" when the lesson screen asks for none (turned off there or
        in the terminal, or by the installer's SCREEN_CODE). The files are the lesson engine's, read
        again every 3 seconds at most (asked three times a frame, the settings read them 6 times)."""
        at, code = getattr(self, "_code_seen", (-1e9, ""))
        if time.monotonic() - at > 3:
            code = self.read_lesson_code()
            self._code_seen = (time.monotonic(), code)
        return code

    def read_lesson_code(self):
        data = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
        try:
            state = (data / "code_enabled").read_text().strip().lower()
        except OSError:
            state = ""
        if state not in ("on", "off"):
            state = "off" if os.environ.get("SCREEN_CODE", "on").strip().lower() in ("off", "no", "false", "0") else "on"
        if state == "off":
            return ""
        try:
            code = (data / "screen_code").read_text().strip()
        except OSError:
            return ""
        return code if re.fullmatch(r"\d{6}", code) else ""

    def new_lesson_code(self):
        def done(ok, err):
            self._code_seen = (-1e9, "")             # the new code read at once, not 3 seconds later
            self.toast(self.tr("codeNew", c=self.lesson_code()) if ok else self.tr("failed"), 6 if ok else 3)
        self.screen.engine.post("/code/new", {}, on_result=done)

    def next_rest(self):
        nr = nxt(RESTS, self.rest_content)
        self.save(rest=nr)
        self.screen.media.stop()
        if nr == "nasa":
            self.apod.start(self.online())

    # ------------------------------------------------------------------ the voice and the lessons
    VOICE_COUNT = 16                             # the General MIDI families, armonico_pi.FAMILIES

    def voice(self):
        v = self.voice_now if self.voice_now is not None else self.st.get("voice", 0)
        return v if isinstance(v, int) and 0 <= v < self.VOICE_COUNT else 0

    def voice_move(self, d):
        v = (self.voice() + d) % self.VOICE_COUNT
        self.voice_now = v
        self.screen.pub("piano/device/voice/set", str(v))

    def lesson_settings(self):
        """The lesson engine's own settings, the ones the lesson screen in a browser sets too."""
        return self.screen.engine.overview.get("settings") or {}

    def set_lesson(self, **kw):
        cur = dict(self.lesson_settings())
        cur.update(kw)                               # shown at once; the engine's copy comes back within 8 s
        self.screen.engine.overview["settings"] = cur
        self.screen.engine.post("/settings", kw)

    LESSON_STEPS = ("numbers", "keys", "memory")
    EAR_ROUNDS = ("demo", "lead", "build", "ear", "memory")     # piano_game.EAR_ROUNDS
    LEVELS = ("auto", "beginner", "intermediate", "advanced")   # piano_game.LEVEL_CHOICES
    PACES = ("relaxed", "steady", "fast")

    def lessons_text(self):
        ls = self.lesson_settings()
        c = ls.get("count_in", 4) if isinstance(ls.get("count_in"), int) else 4
        return self.tr("pc." + (ls.get("pace") if ls.get("pace") in self.PACES else "steady")) + " · " + \
            (self.tr("c.countN", n=c) if c else self.tr("c.countOff"))

    def lesson_cells(self):
        tr, ls = self.tr, self.lesson_settings()
        c = ls.get("count_in", 4) if isinstance(ls.get("count_in"), int) else 4
        pace = ls.get("pace") if ls.get("pace") in self.PACES else "steady"
        steps = [x for x in (ls.get("steps") or self.LESSON_STEPS) if x in self.LESSON_STEPS] or list(self.LESSON_STEPS)

        def flip(name):
            new = [x for x in self.LESSON_STEPS if (x in steps) != (x == name)]
            if new:                                  # at least one round stays, as on the lesson screen
                self.set_lesson(steps=new)
            else:
                self.toast(tr("c.stepsOne"))
        level = ls.get("level") if ls.get("level") in self.LEVELS else "auto"
        now_level = (self.screen.engine.overview.get("learner") or {}).get("level")
        level_txt = tr("lv." + level) + (" · " + tr("lv." + now_level) if level == "auto" and now_level in self.LEVELS else "")
        cells = [(tr("c.count"), tr("c.countN", n=c) if c else tr("c.countOff"), tr("c.countD"),
                  lambda: self.set_lesson(count_in=(c + 1) % 9)),
                 (tr("c.pace"), tr("pc." + pace), tr("c.paceD"), lambda: self.set_lesson(pace=nxt(self.PACES, pace))),
                 (tr("c.level"), level_txt, tr("c.levelD"), lambda: self.set_lesson(level=nxt(self.LEVELS, level)))]
        # the device learns by ear: its own rounds and helps (piano_game.EAR_DEFAULTS). The browser's rounds (numbers,
        # keys lit) are not shown here: on the device the engine never used the keys lit, and "numbers" was by ear
        def ear(k):
            v = ls.get("ear_" + k)
            return v if isinstance(v, bool) else True

        def flip_round(k):
            if ear(k) and sum(ear(r) for r in self.EAR_ROUNDS) == 1:
                self.toast(tr("c.stepsOne"))         # one round at least
                return
            self.set_lesson(**{"ear_" + k: not ear(k)})
        cells.append((tr("er.auto"), self.on(ear("auto")), tr("er.autoD"), lambda: self.set_lesson(ear_auto=not ear("auto"))))
        for k in self.EAR_ROUNDS:
            cells.append((tr("er." + k), self.on(ear(k)), tr("er.%sD" % k), lambda k=k: flip_round(k)))
        for k in ("first_gold", "direction", "gold"):
            cells.append((tr("er." + k), self.on(ear(k)), tr("er.%sD" % k), lambda k=k: self.set_lesson(**{"ear_" + k: not ear(k)})))
        fade = ls.get("review_fading") if isinstance(ls.get("review_fading"), bool) else True
        cells.append((tr("c.fade"), self.on(fade), tr("c.fadeD"), lambda: self.set_lesson(review_fading=not fade)))
        s = self.screen
        cells.append((tr("c.race"), self.on(s.ghost_on()), tr("c.raceD"), lambda: self.save(ghost=not s.ghost_on())))
        return cells

    def link_line(self):
        """The connection card's second line: the address, and the Wi-Fi signal or the gateway."""
        addr, gw = self.link_address()
        extra = self.link_text()[1]
        if self.wf.get("via") == "ethernet":
            extra = gw
        return " · ".join(x for x in (addr if addr != self.tr("notConnected") else "", extra or gw) if x)

    def radio_cell(self, kind):
        """Wi-Fi or Bluetooth in one card: the network or the device connected, and the radio's switch at the card's
        end. ✓ (or a touch on the card) opens the networks or the devices, and turns the radio on when it is off;
        a touch on the switch turns it off or on."""
        tr, st = self.tr, self.radio_now(kind)
        label = tr("c.wifiSw") if kind == "wifi" else tr("c.btSw")
        sw = self.radio_switch(kind)
        if st != "off":
            busy = self.__dict__.get("_radio_busy", {}).get(kind)
            text = str(sw) if busy else self.wifi_cell_text() if kind == "wifi" else self.bt_cell_text()
            if isinstance(sw, SwitchVal):
                v = SwitchVal(text)
                v.on = sw.on
                v.toggle = lambda: self.radio_toggle(kind)
            else:
                v = text if st is None else f"{text} · {sw}"   # no switch to show: none found, or held by hardware
            act = (lambda: self.open_wifi("settings")) if kind == "wifi" else (lambda: self.open_bt("net"))
            return (label, v, tr("c.wifiD") if kind == "wifi" else tr("c.btD"), act)
        v = SwitchVal(tr("off"))
        v.on = False
        v.toggle = lambda: self.radio_toggle(kind)
        return (label, v, tr("c.radioOnD"), lambda: self.radio_toggle(kind))

    def chips_text(self):
        off = self.chips_off()
        if not off:
            return self.tr("chip.allOn")
        return " · ".join(self.tr("chip.wifi" if k == "wifi" else "chip.bt") + " " + self.tr("off") for k in sorted(off, reverse=True))

    def open_page(self, page):
        self.page, self.sel = page, 0
        if page == "play":
            self.px_refresh()                        # whether the TV row shows (views_plex.py)
        if page == "ha" and not self.local_now() and self.ha.get("found") is None and self.online() \
                and not self.ha["searching"]:
            self.find_ha()                           # Home Assistant is looked for by mDNS when its page opens
        if page == "outs":
            self.snd_at = 0.0                        # the outputs looked for again now, not in up to 3 s
            self.bt_refresh()                        # the Bluetooth cell's connected device
        if page == "net":
            self.wifi_status_soon()                  # the Wi-Fi cell's network, asked once when the page opens
        self.go("settings")

    def view_settings(self, t):
        tr = self.tr
        self.hills(0.4)
        allp = self.settings_cells()
        if self.page not in allp:
            self.page = "top"
        cells, top = allp[self.page], self.page == "top"
        here = self.profile_here() if self.page == "prof" else None
        can = [bool(c[3]) for c in cells]
        self.sel = min(self.sel, len(cells) - 1)

        def step(d):
            i = self.sel
            for _ in range(len(cells)):
                i = (i + d) % len(cells)
                if can[i]:
                    return i
            return self.sel
        if not can[self.sel]:
            self.sel = step(1)
        # the counter ("1-8 of 9") goes through the header, which lays it out beside the CPU temperature chip:
        # drawn on its own at the same corner, the two covered each other
        count = "" if self.page == "night" else self.grid_count(cells, "settings:" + self.page, self.sel)
        self.header(tr("settings") if top else (here.get("name") or tr("mainProfile")) if here else tr("g." + self.page), count,
                    temp=self.page in ("test", "quick", "soak"))

        def act(i):
            pg = self.page
            self.sel = i
            cells[i][3]()
            if self.page != pg:
                self.sel = 0
        if self.page == "night":
            self.night_draw(cells, can)
        else:
            self.card_grid(cells, can, act, "settings:" + self.page, self.sel, header_right=False)

        def back():
            if self.page in SUB_PAGES:
                if self.page == "conn" and not (self.job and not self.job.done):
                    self.reset_broker()              # a choice left without Apply is not kept
                was = self.page
                self.page, self.sel = SUB_PAGES[was]
                if self.sel is None:                 # back from a profile's page: on its card
                    ids = [x.get("id") for x in self.profiles()["list"]]
                    pid = getattr(self, "prof_id", None)
                    self.sel = ids.index(pid) + 1 if pid in ids else 0
                elif self.sel < 0:
                    self.sel = max(0, len(self.settings_cells()[self.page]) + self.sel)
                if self.page == "sys" and self.sel > SUB_PAGES["rec"][1] and not self.rec_card_on():
                    self.sel -= 1                    # the recording's card is hidden: the cards after it move up one
                self.alarm_adjust = False
                return
            self.sel = max(0, PAGES.index(self.page) - 1)
            self.page = "top"
        if self.vol_adjust and self.page != "sound":
            self.vol_adjust = False
        if self.voice_adjust and self.page != "play":
            self.voice_adjust = False
        if self.voice_adjust:
            # the voice cell is open: ▼▲ (and the wheel) move through the 16 voices, each one
            # heard with a short note; ✓ or ☰ closes it
            done = lambda: setattr(self, "voice_adjust", False)
            self.bar([("☰", tr("done"), done), ("▼", tr("c.voicePrev"), lambda: self.voice_move(-1)),
                      ("▲", tr("c.voiceNext"), lambda: self.voice_move(1)), ("✓", tr("done"), done)])
            return
        if self.zoom_adjust and self.page != "zoom":
            self.zoom_adjust = False
        if self.zoom_adjust:
            # the size cell is open: ▼ is A−, ▲ is A+, ✓ or ☰ closes it
            done = lambda: setattr(self, "zoom_adjust", False)
            self.bar([("☰", tr("done"), done), ("▼", "A−", lambda: self.zoom_move(-1)),
                      ("▲", "A+", lambda: self.zoom_move(1)), ("✓", tr("done"), done)])
            return
        if self.alarm_adjust and self.page != "alarm":
            self.alarm_adjust = False
        if self.alarm_adjust:
            # the time's cell is open: ▼▲ (and the wheel) move it by 5 minutes, ✓ or ☰ closes it
            done = lambda: setattr(self, "alarm_adjust", False)
            self.bar([("☰", tr("done"), done), ("▼", tr("c.earlier"), lambda: self.alarm_move(-5)),
                      ("▲", tr("c.later"), lambda: self.alarm_move(5)), ("✓", tr("done"), done)])
            return
        if self.vol_adjust:
            # the volume cell is open: ▼▲ (and the wheel) change it by 10, ✓ or ☰ closes it
            def vol(d):
                v = self.volume_now()
                self.set_volume(min(100, (v // 10 + 1) * 10) if d > 0 else max(0, (v + 9) // 10 * 10 - 10))
            done = lambda: setattr(self, "vol_adjust", False)
            self.bar([("☰", tr("done"), done), ("▼", tr("c.softer"), lambda: vol(-1)),
                      ("▲", tr("c.louder"), lambda: vol(1)), ("✓", tr("done"), done)])
            return
        self.bar([("☰", "" if top else tr("back"), None if top else back),
                  ("▼", tr("down"), lambda: setattr(self, "sel", step(1))),
                  ("▲", tr("up"), lambda: setattr(self, "sel", step(-1))),
                  ("✓", (tr("radio.turnOff" if cells[self.sel][1].on else "radio.turnOn") if isinstance(cells[self.sel][1], SwitchVal) and not getattr(cells[self.sel][1], "toggle", None) and can[self.sel] else tr("c.open") if self.page == "profiles" and self.sel > 0 else cells[self.sel][0]) if can[self.sel] else "",
                   (lambda: act(self.sel)) if can[self.sel] else None)])

    def grid_count(self, cells, key, sel):
        """The counter card_grid would show for these cards ("1-8 of 16"), worked out before the header is
        drawn, so the header places it (header_right=False then keeps card_grid from drawing it again)."""
        cols, rows = self.grid_geom()[:2]
        total_rows = (len(cells) + cols - 1) // cols
        first = self.first_row(key, max(0, sel) // cols, rows, total_rows)
        return self.page_counter(first * cols, rows * cols, len(cells))

    def card_grid(self, cells, can, act, key, sel, header_right=True):
        """The cards of a page in the grid of the screen's size class (columns x rows per page). A page with more
        cards scrolls by whole rows when the selection leaves the window; only the cards shown are drawn and
        touchable. The counter ("1-8 of 16") is drawn at the top, the position bar on the outer side."""
        cols, rows, w, h, gap = self.grid_geom()
        total_rows = (len(cells) + cols - 1) // cols
        first = self.first_row(key, sel // cols, rows, total_rows)
        pos = self.glide(key, first) if total_rows > rows else first
        bottom = 58 + rows * (h + gap) - gap
        with self.cv.band(52, bottom + 4):
            for i, (lb, v, d, fn) in enumerate(cells):
                r = i // cols - pos
                if -1 < r < rows:
                    x, y = 24 + (i % cols) * (w + 12), 58 + r * (h + gap)
                    # a card half out of the window is drawn, but only a whole one takes a touch
                    whole = 0 <= round(r, 3) and y + h <= bottom + 0.5
                    self.cell(x, y, w, h, lb, v, d, i == sel and can[i], can[i] and whole, lambda i=i: act(i))
        shown = rows * cols
        count = self.page_counter(first * cols, shown, len(cells)) if header_right else ""
        if count:
            self.cv.text(count, W - 22, 33, 13, self.P["dim"], align="right")
        self.scroll_bar(58, rows * h + (rows - 1) * gap, first, rows, total_rows)
        if total_rows > rows:
            self.scroll_zone(24, 58, 752, rows * (h + gap), h + gap, lambda d: self.scroll_cards(cells, can, cols, rows, d))

    def scroll_cards(self, cells, can, cols, rows, d):
        """A drag: the window moves d rows and the selection with it, to the nearest card that can be pressed."""
        total = (len(cells) + cols - 1) // cols
        key = "settings:" + self.page
        first = max(0, min(max(0, total - rows), self.scroll.get(key, 0) + d))
        self.scroll[key] = first
        i = max(0, min(len(cells) - 1, first * cols + (self.sel % cols)))
        if not can[i]:
            i = next((j for j in range(first * cols, min(len(cells), (first + rows) * cols)) if can[j]), self.sel)
        self.sel = i

    # ------------------------------------------------------------------ the edit screen
    def view_edit(self, t):
        P, cv, e = self.P, self.cv, self.edit
        if not e:
            self.go("settings")
            return
        self.hills(0.3)
        self.header(e["label"])
        cv.rect(24, 62, W - 48, 42, P["panel"], r=10)
        hide = e["secret"] and not e.get("shown")
        shown = "●" * min(len(e["val"]), 40) if hide else e["val"]
        field_w = W - 90 - (110 if e.get("reveal") else 0)
        if e.get("digits"):
            self.view_pad(t, e, shown)
            return
        # the text runs the way its language does: a Hebrew keyboard (or Hebrew text) is written from the right and
        # aligned to the right, an English one from the left
        rtl = (e.get("kb") == "he" and not hide) or (not hide and any("֐" <= c <= "׿" for c in e["val"]))
        with cv.physical():
            line = cv.fit(shown + ("|" if int(t * 2) % 2 else ""), field_w, 17, True, end=True)
            if rtl:
                cv.text(line, W - 40 - (110 if e.get("reveal") else 0), 90, 17, P["ink"], True, "right")
            else:
                cv.text(line, 40, 90, 17, P["ink"], True)
            if e.get("reveal"):
                # Show / Hide, inside the field at its end: the secret checked before it is sent
                lab = self.tr("wf.hide") if e.get("shown") else self.tr("wf.show")
                cv.rect(W - 130, 68, 100, 30, P["sel"], r=8, width=1.5)
                cv.text(lab, W - 80, 89, 14, P["gold"], True, "center")
                self.hit(W - 130, 68, 100, 30, lambda: e.update(shown=not e.get("shown")))
        if e["phone"] and self.paste:
            cv.text(cv.fit(self.tr("phone", a=f"{self.ip or self.mdns}:{self.paste.port}", c=self.paste.code), W - 48, 13, True),
                    24, 126, 13, P["gold"], True)
        elif e.get("note"):
            cv.text(self.short(e["note"], W - 48, 14, True), 24, 128, 14, (226, 118, 111), True)
        kw, kh = 60, 38
        sym = getattr(self, "sym", False)
        with cv.physical():
            he_kb = e.get("kb") == "he"
            for ri, row in enumerate(SROWS if sym else HEROWS if he_kb else EROWS):
                x0 = (W - len(row) * (kw + 5)) / 2
                for ci, c0 in enumerate(row):
                    ch = c0.upper() if self.caps and not sym and not he_kb else c0
                    x, y = x0 + ci * (kw + 5), 142 + ri * (kh + 5)
                    cv.rect(x, y, kw, kh, P["panel"], r=8)
                    cv.text(ch, x + kw / 2, y + 26, 17, P["ink"], True, "center")
                    self.hit(x, y, kw, kh, lambda ch=ch: self.edit_key(ch))
            y4 = 142 + 4 * (kh + 5)
            def switch_kb():
                e["kb"] = "en" if e.get("kb") == "he" else "he"
                self.sym, self.caps = False, False
            for x, w, label, fn in ((70, 90, "⇧", (lambda: None) if he_kb and not sym else lambda: setattr(self, "caps", not self.caps)),
                                    (170, 100, "abc" if sym else "!#$", lambda: setattr(self, "sym", not getattr(self, "sym", False))),
                                    (280, 220, TEXTS["space"][1 if he_kb else 0], lambda: self.edit_key(" ")),
                                    (510, 110, "⌫", lambda: e.update(val=e["val"][:-1])),
                                    (630, 100, "EN" if he_kb else "עב", switch_kb)):
                cv.rect(x, y4, w, kh, P["sel"] if label == "⇧" and self.caps and not sym else P["panel"], r=8)
                if label == "EN" or label == "עב":
                    cv.rect(x, y4, w, kh, P["sel"], r=8, width=1.5)
                cv.text(label, x + w / 2, y4 + 26, 16, P["dim"] if label == "⇧" and he_kb and not sym else P["ink"], True, "center")
                self.hit(x, y4, w, kh, fn)
        self.bar([("☰", self.tr("cancel"), self.edit_cancel), ("▼", "", None), ("▲", "", None),
                  ("✓", e.get("ok") or self.tr("save"), self.edit_done)])

    def edit_key(self, ch):
        if self.edit is not None:
            if self.edit.get("digits") and (not ch.isdigit() or len(self.edit["val"]) >= self.pad_max(self.edit)):
                return
            self.edit["val"] += ch
            self.caps = False

    @staticmethod
    def pad_max(e):
        """How many digits the keypad takes: a PIN is 4 to 8, a port 5."""
        return 8 if e.get("secret") else 5

    # the keypad's keys, left to right and top to bottom: 1-9, then ⌫ 0 ✓
    PAD_KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓")
    # key width, height and gap in plan units for each screen class (the small screens get the larger keys)
    PAD_SIZE = {3.5: (150, 62, 8), 4.3: (130, 60, 8), 5.0: (110, 52, 7), 7.0: (110, 52, 7), 10.0: (110, 52, 7)}

    def pad_press(self, k):
        e = self.edit
        if e is None:
            return
        if k == "⌫":
            e["val"] = e["val"][:-1]
        elif k == "✓":
            self.edit_done()
        else:
            self.edit_key(k)

    def pad_move(self, d):
        self.pad_sel = (getattr(self, "pad_sel", 0) + d) % len(self.PAD_KEYS)

    def view_pad(self, t, e, shown):
        """The phone-style keypad for a field that takes only digits: 3 x 3 digits, then ⌫ 0 ✓, nothing else. The
        buttons work on it: ▼ ▲ move over the keys, ✓ presses the framed one, ☰ cancels."""
        P, cv, tr = self.P, self.cv, self.tr
        kw, kh, gap = self.PAD_SIZE.get(self.screen_inch, self.PAD_SIZE[5.0])
        n = len(e["val"])
        mx = self.pad_max(e)
        dots = shown if not e["secret"] or e.get("shown") else "●" * n
        with cv.physical():
            if e["secret"] and not e.get("shown"):
                line = " ".join(["●"] * n + ["○"] * max(0, min(mx, max(4, n + 1)) - n)) if n < mx else " ".join(["●"] * n)
            else:
                line = " ".join(dots) if dots else "|"
            cv.text(cv.fit(line, W - 90, 22, True), W / 2, 92, 22, P["ink"], True, "center")
            x0 = (W - (3 * kw + 2 * gap)) / 2
            y0 = 118
            sel = getattr(self, "pad_sel", 0) % len(self.PAD_KEYS)
            for i, k in enumerate(self.PAD_KEYS):
                x, y = x0 + (i % 3) * (kw + gap), y0 + (i // 3) * (kh + gap)
                cv.rect(x, y, kw, kh, P["panel"], r=10)
                if i == sel:
                    cv.rect(x, y, kw, kh, P["sel"], r=10, width=2.5)
                label = {"⌫": "⌫ " + tr("pad.del"), "✓": "✓ " + tr("pad.ok")}.get(k, k)
                small = k in ("⌫", "✓")
                cv.text(self.short(label, kw - 12, 14 if small else 24, True), x + kw / 2, y + kh / 2 + (5 if small else 8),
                        14 if small else 24, P["gold"] if k == "✓" else P["dim"] if small else P["ink"], True, "center")
                self.hit(x, y, kw, kh, lambda k=k, i=i: (setattr(self, "pad_sel", i), self.pad_press(k)))
        if e.get("note"):
            cv.text(self.short(e["note"], W - 48, 14, True), W / 2, y0 + 4 * (kh + gap) + 18, 14, (226, 118, 111), True, "center")
        k = self.PAD_KEYS[sel]
        self.bar([("☰", tr("cancel"), self.edit_cancel), ("▼", tr("down"), lambda: self.pad_move(1)),
                  ("▲", tr("up"), lambda: self.pad_move(-1)),
                  ("✓", (e.get("ok") or tr("save")) if k == "✓" else k if k != "⌫" else tr("pad.del"), lambda: self.pad_press(k))])

    # ------------------------------------------------------------------ the city search
    def open_place(self, back):
        self.place_return = back
        self.query = ""
        self.kb = "he" if self.lang == "he" else "en"
        self.place_sel = 0
        self.cities.search("", self.lang, self.online())
        self.go("place")

    def place_type(self, s):
        if s is None:
            self.query = self.query[:-1]
        else:
            self.query += s
            if any("֐" <= c <= "׿" for c in s):
                self.kb = "he"
            elif s.isascii() and s.isalpha():
                self.kb = "en"
        self.place_sel = 0                           # new results: the first one framed
        self.cities.search(self.query, self.lang, self.online())

    def place_pick(self, c):
        self.set_city(c)
        self.query = ""
        self.go(self.place_return)

    def view_place(self, t):
        P, cv, f = self.P, self.cv, self.cities
        self.hills(0.35)
        city = self.city
        self.header(self.tr("place"), f"{city['name']}, {city.get('country', '')}".strip(", "))
        cv.rect(24, 62, W - 48, 40, P["panel"], r=10)
        # written from the right when the keyboard (or the text) is Hebrew, from the left in English
        rtl = self.kb == "he" or any("֐" <= c <= "׿" for c in self.query)
        with cv.physical():
            line = cv.fit((self.query or self.tr("search")) + ("|" if int(t * 2) % 2 else ""), W - 200, 18, True, end=True)
            cv.text(line, W - 40 if rtl else 40, 89, 18, P["ink"] if self.query else P["dim"], True, "right" if rtl else "left")
            # the side opposite the text, on the screen itself (inside physical, so a Hebrew interface does not
            # mirror it back onto the text)
            if f.offline and self.query:
                cv.text(self.tr("builtin"), 40 if rtl else W - 40, 89, 12, P["dim"], align="left" if rtl else "right")
            elif f.busy:
                cv.text("…", 40 if rtl else W - 40, 89, 18, P["dim"], align="left" if rtl else "right")
        shown = f.results[:4]
        sel = getattr(self, "place_sel", 0) % len(shown) if shown else 0
        for i, r in enumerate(shown):
            x, y, w = 24 + (i % 2) * 382, 110 + (i // 2) * 44, 370
            self.hit(x, y, w, 38, lambda r=r: self.place_pick(r))
            cv.rect(x, y, w, 38, P["panel"], r=10)
            if i == sel and len(shown) > 1:          # the one ✓ picks, moved by ▼▲
                cv.rect(x, y, w, 38, P["sel"], r=10, width=2)
            cv.text(cv.fit(r["name"], 200, 15, True), x + 14, y + 25, 15, P["ink"], True)
            sub = (r["admin"] + ", " if r.get("admin") else "") + (r.get("country") or "")
            cv.text(cv.fit(sub, 150, 12), x + w - 14, y + 25, 12, P["dim"], align="right")
        kw, kh = 64, 44
        with cv.physical():
            for ri, row in enumerate(CROWS[self.kb]):
                x0 = (W - len(row) * (kw + 6)) / 2
                for ci, ch in enumerate(row):
                    x, y = x0 + ci * (kw + 6), 206 + ri * (kh + 6)
                    cv.rect(x, y, kw, kh, P["panel"], r=9)
                    cv.text(ch, x + kw / 2, y + 29, 18, P["ink"], True, "center")
                    self.hit(x, y, kw, kh, lambda ch=ch: self.place_type(ch.lower() if self.kb == "en" and self.query else ch))
            y4 = 206 + 3 * (kh + 6)
            for x, w, label, fn in ((60, 80, "עב" if self.kb == "en" else "EN",
                                     lambda: setattr(self, "kb", "he" if self.kb == "en" else "en")),
                                    (150, 250, TEXTS["space"][1 if self.kb == "he" else 0], lambda: self.place_type(" ")),
                                    (410, 120, "⌫", lambda: self.place_type(None))):
                cv.rect(x, y4, w, kh - 4, P["panel"], r=9)
                cv.text(label, x + w / 2, y4 + 27, 16, P["ink"], True, "center")
                self.hit(x, y4, w, kh - 4, fn)
        def move(d):
            self.place_sel = (sel + d) % len(shown)
        many = len(shown) > 1                        # ▼▲ said Down and Up and did nothing
        self.bar([("☰", self.tr("back"), lambda: self.go(self.place_return)),
                  ("▼", self.tr("down") if many else "", (lambda: move(1)) if many else None),
                  ("▲", self.tr("up") if many else "", (lambda: move(-1)) if many else None),
                  ("✓", self.tr("results"), (lambda: self.place_pick(shown[sel])) if shown else None)])

    # ------------------------------------------------------------------ the job screen
    def view_job(self, t):
        j = self.job
        if not j:
            self.go("settings")
            return
        self.hills(0.35)
        self.draw_job(j, t)
        self.bar([("☰", self.tr("back"), lambda: self.go(j.back)), ("▼", "", None), ("▲", "", None),
                  ("✓", self.tr("continue") if j.done else self.tr("working"), (lambda: self.go(j.back)) if j.done else None)])


def saved_profile(name):
    """A saved broker profile (/etc/armonico/modes, readable by the screen's group), or {}."""
    try:
        text = (Path("/etc/armonico/modes") / f"{name}.env").read_text()
    except OSError:
        return {}
    return dict(re.findall(r"^([A-Z_]+)='([^']*)'", text, re.M))


def load_stop(app):
    """The stop button, read from the file: the lesson web page sets it too, any of the keys."""
    # read again every 3 seconds at most, and at once after a change made here: the settings draw
    # every frame, and settings.json was read on each of them
    at, v = getattr(app, "_stop_seen", (-1e9, None))
    if v is None or v != app.st.get("stop_button") or time.monotonic() - at > 3:
        load = getattr(sys.modules.get(type(app.screen).__module__), "load_settings", None)
        try:
            v = (load() if load else app.st).get("stop_button", "ok")
        except Exception:
            v = app.st.get("stop_button", "ok")
        if v != app.st.get("stop_button"):
            app.st["stop_button"] = v
        app._stop_seen = (time.monotonic(), v)
    if v in STOPS or v in tuple(f"key:{i}" for i in range(13)):
        return v
    return "ok"
