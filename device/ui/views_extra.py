"""views_extra.py - the alarm clock on the device, songs from a USB stick, and free play's recording.

The alarm: a time, the days and a song, kept in settings.json. At that minute the device asks the
bridge to ring (piano/alarm/set), as Home Assistant's blueprint does, so it rings with no Home
Assistant and no network. On an external broker Home Assistant may ring instead: one of the two, never
both. The stop button stops it, as any alarm.

Songs from a USB stick: the MIDI files on a stick go to the lesson engine one by one through its
upload (/songs/upload, as the lesson screen in a browser sends them).

The recording: the keys played in free play, with their timing (piano/notes, timed as they came),
played back on the device's own sound through piano/device/note. Kept in memory until the next one.
"""
import os
import threading
import time
from pathlib import Path

from .canvas import W

ALARM_DAYS = {                      # Monday is 0 (datetime.weekday)
    "all": (0, 1, 2, 3, 4, 5, 6), "sun-thu": (6, 0, 1, 2, 3), "mon-fri": (0, 1, 2, 3, 4),
    "fri-sat": (4, 5), "sat-sun": (5, 6),
}
DAY_ORDER = ("sun-thu", "all", "mon-fri", "fri-sat", "sat-sun")
KB_MODES = ("internal", "external", "both")     # the device's keys, a USB MIDI keyboard, or both together
# what plays, in one choice: the touch screen plays along with a keyboard in the first three
KB_OUTS = ("both", "device", "keyboard")
PLAY_MODES = KB_MODES + ("touch", "off")
REC_MAX = 120.0                     # seconds of playing kept in one recording
REC_HOLD = 1.2                      # the longest a recorded note sounds back (a held key is not known)
MIDI_EXT = (".mid", ".midi", ".kar")


def stored_name(stem):
    """The name the lesson engine gives an uploaded song (piano_game.save_upload), to tell one already in."""
    import re
    safe = re.sub(r"[^\w \-().\u0590-\u05ff]", "_", stem).strip(" ._") or "uploaded"
    return safe


def song_name(name):
    return str(name).replace("_", " ")


class ExtraViews:
    def init_extra_state(self):
        self.alarm_adjust = False
        self.rec = {"on": False, "notes": [], "t0": 0.0, "playing": False, "gen": 0}
        self.imp = {"files": None, "sel": 0, "busy": "", "done": {}, "at": 0.0}
        self.keyboards = []                          # USB MIDI keyboards connected (piano/device/keyboards)

    # ------------------------------------------------------------------ the alarm
    def alarm(self):
        a = self.st.get("alarm")
        a = dict(a) if isinstance(a, dict) else {}
        a.setdefault("on", False)
        a.setdefault("time", "07:00")
        a.setdefault("days", "sun-thu")
        a.setdefault("by", "device")
        if a["days"] not in ALARM_DAYS:
            a["days"] = "sun-thu"
        names = self.song_names()
        if names and a.get("song") not in names:
            a["song"] = names[0]                     # empty, or a song removed or renamed since: one that exists
        a.setdefault("song", "")
        return a

    def save_alarm(self, **kw):
        a = self.alarm()
        a.update(kw)
        self.save(alarm=a)
        self._chip = (-1e9, "")                      # the top bar's alarm clock is looked at again now

    def alarm_chip(self):
        """The alarm's time for the top bar when it is on and rings on this device, else "".
        Looked at once a second at most: the header is drawn on every frame."""
        now = time.monotonic()
        at, v = getattr(self, "_chip", (-1e9, ""))
        if now - at > 1.0:
            try:
                a = self.alarm()
                v = a["time"] if a["on"] and a["song"] and self.alarm_rings_here(a) else ""
            except Exception:
                v = ""
            self._chip = (now, v)
        return v

    def song_names(self):
        return [x.get("name") for x in self.screen.engine.overview.get("songs", []) if x.get("name")]

    def alarm_rings_here(self, a=None):
        """Who rings: the device, unless Home Assistant was chosen while an external broker is in use
        (on the local broker Home Assistant cannot reach the piano, so the device always does)."""
        a = a or self.alarm()
        return a["by"] != "ha" or self.local_now()

    def alarm_text(self):
        a = self.alarm()
        if not a["on"]:
            return self.tr("off")
        return f"{a['time']} · " + self.tr("ad." + a["days"])

    def alarm_move(self, minutes):
        a = self.alarm()
        h, m = (int(x) for x in a["time"].split(":"))
        t = ((h * 60 + m + minutes) // 5 * 5) % (24 * 60)
        self.save_alarm(time=f"{t // 60:02d}:{t % 60:02d}")

    def alarm_cells(self):
        tr, a = self.tr, self.alarm()
        names = self.song_names()

        def next_song():
            if names:
                i = names.index(a["song"]) if a["song"] in names else -1
                self.save_alarm(song=names[(i + 1) % len(names)])

        def next_days():
            self.save_alarm(days=DAY_ORDER[(DAY_ORDER.index(a["days"]) + 1) % len(DAY_ORDER)])
        cells = [
            (tr("c.alarmOn"), tr("on") if a["on"] else tr("off"), tr("c.alarmOnD"),
             lambda: self.save_alarm(on=not a["on"])),
            (tr("c.alarmTime"), f"◂ {a['time']} ▸" if self.alarm_adjust else a["time"],
             tr("c.alarmTimeOn") if self.alarm_adjust else tr("c.alarmTimeD"),
             lambda: setattr(self, "alarm_adjust", not self.alarm_adjust)),
            (tr("c.alarmDays"), tr("ad." + a["days"]), tr("c.next"), next_days),
            (tr("c.alarmSong"), song_name(a["song"]) if a["song"] else tr("noSongYet"), tr("c.next") if names else "",
             next_song if names else None),
        ]
        if not self.local_now():
            cells.append((tr("c.alarmBy"), tr("c.byDevice") if a["by"] != "ha" else "Home Assistant", tr("c.alarmByD"),
                          lambda: self.save_alarm(by="device" if a["by"] == "ha" else "ha")))
        cells.append((tr("c.alarmTest"), tr("c.alarmTestV"), tr("c.alarmTestD"),
                      (lambda: self.ring_alarm(test=True)) if a["song"] else None))
        return cells

    def ring_alarm(self, test=False):
        a = self.alarm()
        if a["song"]:
            self.screen.pub("piano/alarm/set", f"{a['song']}|Armonico Pi alarm{' test' if test else ''}")
            if test:
                self.toast(self.tr("alarmTest"), 4)

    def follow_alarm(self):
        """Once a minute is enough: the minute set, on a day chosen, once a day (a restart within the
        minute does not ring it again: the day it rang is kept)."""
        now = time.monotonic()
        if now - getattr(self, "_alarm_seen", 0.0) < 5:
            return
        self._alarm_seen = now
        a = self.alarm()
        if not a["on"] or not a["song"] or not self.alarm_rings_here(a):
            return
        local = self.local()
        day = local.strftime("%Y-%m-%d")
        h, m = (int(x) for x in a["time"].split(":"))
        now_min, set_min = local.hour * 60 + local.minute, h * 60 + m
        if now_min < set_min:
            self._alarm_armed = f"{day} {a['time']}"  # seen before its minute: it may ring late (below)
        # the minute set, or up to 59 minutes after it when that minute never came on this clock (it
        # moved forward an hour for summer time while the alarm waited): late is better than never
        if not 0 <= now_min - set_min < 60 or local.weekday() not in ALARM_DAYS[a["days"]]:
            return
        stamp = f"{day} {a['time']}"                 # the day and the time: a time moved later the same day rings again
        if self.st.get("alarm_last") == stamp:
            return
        if now_min != set_min and getattr(self, "_alarm_armed", None) != stamp:
            # late rings only for an alarm already set before its minute passed, not one set just now
            # for a time a moment ago
            return
        if self.screen.lesson_on():
            self.screen.engine.post("/lessonstop")   # the alarm wins over everything, a lesson too
        self.save(alarm_last=stamp)
        self.woke()
        self.ring_alarm()

    # ------------------------------------------------------------------ the keyboard: the device's keys or USB
    def play_mode(self):
        """One of PLAY_MODES. From before it was one choice: "Play with" touch or off wins, else the
        keyboard chosen (Play with "keys only" is now a keyboard with touch too)."""
        m = self.st.get("play")
        if m in PLAY_MODES:
            return m
        old = self.st.get("play_input")
        if old in ("touch", "off"):
            return old
        kb = self.st.get("keyboard")
        mode = kb.get("mode") if isinstance(kb, dict) else None
        return mode if mode in KB_MODES else "internal"

    def play_input(self):
        """What the keys and the touch do, from the play mode: both, or touch only, or off."""
        m = self.play_mode()
        return m if m in ("touch", "off") else "both"

    def keyboard_choice(self):
        kb = self.st.get("keyboard")
        kb = dict(kb) if isinstance(kb, dict) else {}
        m = self.play_mode()
        kb["mode"] = m if m in KB_MODES else "internal"
        kb["name"] = str(kb.get("name") or "")
        out = kb.get("out")
        if out not in KB_OUTS:           # saved before there was a choice: "on the speakers" on or off
            out = "both" if kb.get("sound", True) else "keyboard"
        return {"mode": kb["mode"], "name": kb["name"], "out": out}

    def save_keyboard(self, **kw):
        kb = self.keyboard_choice()
        kb.update(kw)
        self.save(keyboard=kb)       # "sound" from before goes: "out" says it now

    def pick_keyboard(self, name):
        """A USB keyboard chosen: it plays from now on, with the device's keys when both were playing."""
        mode = self.play_mode()
        self.save_keyboard(name=name)
        self.save(play="both" if mode == "both" else "external")
        if mode in ("touch", "off"):
            self.toast(self.tr("kbUnmuted"), 4)

    def keyboard_text(self):
        kb, tr, mode = self.keyboard_choice(), self.tr, self.play_mode()
        if mode not in ("external", "both"):
            return tr("pm." + mode)
        name = kb["name"] or (self.keyboards[0] if getattr(self, "keyboards", None) else "")
        return tr("pm." + mode) + (f" · {name}" if name else "")

    def keyboard_cells(self):
        tr, kb = self.tr, self.keyboard_choice()
        found = list(getattr(self, "keyboards", None) or [])
        chosen = kb["name"] if kb["name"] in found else (found[0] if found else "")
        mode = self.play_mode()
        # one choice for what plays: before, "Play with: touch only" on another line silenced the
        # keyboard chosen here, with nothing to say why
        cells = [(tr("c.kbMode"), tr("pm." + mode), tr("c.kbModeD"),
                  lambda: self.save(play=PLAY_MODES[(PLAY_MODES.index(mode) + 1) % len(PLAY_MODES)]))]
        if found:
            for name in found[:6]:
                on = name == chosen and kb["mode"] != "internal"
                cells.append((tr("c.kbUsb"), name, tr("c.kbChosen") if on else tr("c.kbPick"),
                              lambda n=name: self.pick_keyboard(n)))
        else:
            cells.append((tr("c.kbUsb"), tr("c.kbNone"), tr("c.kbNoneD"), None))
        # where everything sounds while a USB keyboard is connected: the keys, the touches, songs, lessons
        out = kb["out"]
        cells.append((tr("c.kbOut"), tr("ko." + out), tr("c.kbOutD"),
                      lambda: self.save_keyboard(out=KB_OUTS[(KB_OUTS.index(out) + 1) % len(KB_OUTS)])))
        return cells

    # ------------------------------------------------------------------ songs from a USB stick
    def open_import(self):
        self.imp.update(files=None, sel=0, busy="", done={}, at=0.0, walking=False)
        self.go("imp")

    def find_midi(self):
        """The MIDI files on the mounted sticks, 3 folders deep at most, 60 at most."""
        from . import views_more
        backup = getattr(views_more, "backup", None)
        roots = backup.Local.sticks() if backup else []
        found = []
        for r in roots:
            base = Path(r)
            for dirpath, dirs, files in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                dirs[:] = [] if depth >= 3 else sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.lower().endswith(MIDI_EXT) and not f.startswith("."):
                        found.append(Path(dirpath) / f)
                        if len(found) >= 60:
                            return found
        return found

    def import_one(self, path):
        if self.imp["busy"]:
            return                                   # one at a time
        have = {str(n).lower() for n in self.song_names()}
        stem = path.stem
        if stored_name(stem).lower() in have:
            self.toast(self.tr("impHave"), 3)
            return
        try:
            # the size first, from the folder: a big file was read whole before it was refused
            big = path.stat().st_size > 2_000_000
            blob = b"" if big else path.read_bytes()
        except OSError as e:
            self.toast(str(e), 4)
            return
        if big or not blob.startswith(b"MThd"):
            self.imp["done"][str(path)] = "bad"
            self.toast(self.tr("impBad"), 4)
            return
        self.imp["busy"] = str(path)

        def done(ok, ans):
            self.imp["busy"] = ""
            good = ok and ans.get("ok", True) is not False
            self.imp["done"][str(path)] = "ok" if good else "bad"
            self.toast(self.tr("impOk", n=song_name(ans.get("name") or stem)) if good
                       else self.tr("impFail", e=ans.get("why", "")), 4)
        self.screen.engine.upload(path.name, blob, on_result=done)

    def view_imp(self, t):
        P, cv, tr, m = self.P, self.cv, self.tr, self.imp
        self.hills(0.35)
        if hasattr(self, "usb_seen"):
            self.usb_seen()                          # a stick put in is mounted, as on the backup screen
        if (m["files"] is None or time.monotonic() - m["at"] > 3) and not m.get("walking"):
            # the sticks are walked in a thread of their own, every 3 seconds: on the drawing thread a
            # slow stick held the screen still
            m["walking"], m["at"] = True, time.monotonic()

            def walk():
                try:
                    found = self.find_midi()
                except Exception:
                    found = []
                m["files"], m["at"], m["walking"] = found, time.monotonic(), False
            threading.Thread(target=walk, daemon=True).start()
        files = m["files"]
        if files is None:                            # the first look at the sticks is still going
            self.header(tr("impTitle"), tr("checking"))
            self.bar([("☰", tr("back"), lambda: self.go("songs")), ("▼", "", None), ("▲", "", None), ("✓", "", None)])
            return
        have = {str(n).lower() for n in self.song_names()}
        self.header(tr("impTitle"), str(len(files)) if files else "")
        if not files:
            cv.text(tr("impNone"), 24, 100, 17, P["ink"], True)
            cv.text(self.short(tr("impNoneD"), W - 48, 13), 24, 126, 13, P["dim"])
            self.bar([("☰", tr("back"), lambda: self.go("songs")), ("▼", "", None), ("▲", "", None), ("✓", "", None)])
            return
        m["sel"] = max(0, min(m["sel"], len(files) - 1))
        per = 6
        first = max(0, min(m["sel"] - per + 1, len(files) - per))
        for j, f in enumerate(files[first:first + per]):
            i = first + j
            y = 62 + j * 58
            state = "have" if stored_name(f.stem).lower() in have and m["done"].get(str(f)) != "ok" else m["done"].get(str(f), "")
            self.hit(24, y, W - 48, 50, lambda i=i: (m.update(sel=i), self.import_one(files[i])))
            cv.rect(24, y, W - 48, 50, P["panel"], r=12)
            if i == m["sel"]:
                cv.rect(24, y, W - 48, 50, P["sel"], r=12, width=2.5)
            cv.text(cv.fit(f.name, W - 260, 16, True), 40, y + 23, 16, P["ink"], True)
            cv.text(cv.fit(str(f.parent), W - 260, 11, end=True), 40, y + 41, 11, P["dim"])
            mark = tr("impIn") if state in ("ok", "have") else tr("impBadS") if state == "bad" else \
                (tr("impGoing") if m["busy"] == str(f) else "")
            if mark:
                cv.text(mark, W - 40, y + 31, 13, P["ok"] if state in ("ok", "have") else P["gold"], True, "right")
        self.list_marks(first, per, len(files), 62, 58, lambda d: m.update(sel=max(0, min(len(files) - 1, m["sel"] + d))))
        cur = files[m["sel"]]
        can = not m["busy"] and stored_name(cur.stem).lower() not in have
        self.bar([("☰", tr("back"), lambda: self.go("songs")),
                  ("▼", tr("down"), lambda: m.update(sel=(m["sel"] + 1) % len(files))),
                  ("▲", tr("up"), lambda: m.update(sel=(m["sel"] - 1) % len(files))),
                  ("✓", tr("impB") if can else "", (lambda: self.import_one(cur)) if can else None)])

    # ------------------------------------------------------------------ free play's recording
    def rec_note(self, payload):
        """A key from piano/notes ("<note> <monotonic time>"), kept while recording."""
        r = self.rec
        if not r["on"]:
            return
        if self.view != "free" or self.screen.lesson_on():
            self.rec_leave()
            return
        parts = payload.split()
        try:
            note, at = int(parts[0]), float(parts[1]) if len(parts) > 1 else time.monotonic()
        except (ValueError, IndexError):
            return
        if not r["notes"]:
            r["t0"] = at
        if at - r["t0"] > REC_MAX:
            self.rec_stop()
            return
        r["notes"].append((at - r["t0"], note))

    def rec_start(self):
        self.rec_stop_play()
        self.rec.update(on=True, notes=[], t0=0.0)
        self.toast(self.tr("recOn"), 3)

    def rec_stop(self):
        if self.rec["on"]:
            self.rec["on"] = False
            n = len(self.rec["notes"])
            self.toast(self.tr("recDone", n=n) if n else self.tr("recEmpty"), 3)

    def rec_leave(self):
        """Free play left (or a lesson started): a recording in progress ends, quietly, and its
        playback stops; the recording itself is kept for the next visit."""
        self.rec["on"] = False
        if self.rec["playing"]:
            self.rec_stop_play()

    def rec_toggle(self):
        if self.rec["on"]:
            self.rec_stop()
        else:
            self.rec_start()

    def rec_stop_play(self):
        self.rec["gen"] += 1
        self.rec["playing"] = False

    def rec_play(self):
        """Back on the device's own sound, as it was played: each note lasts until the next one, up to
        REC_HOLD (the recording knows when a key went down, not when it came up)."""
        r = self.rec
        if r["on"]:
            self.rec_stop()
        if r["playing"]:
            self.rec_stop_play()
            return
        notes = list(r["notes"])
        if not notes:
            self.toast(self.tr("recEmpty"), 3)
            return
        r["gen"] += 1
        gen = r["gen"]
        r["playing"] = True
        pub = self.screen.pub

        def go():
            start = time.monotonic()
            sounding = []
            try:
                for i, (at, note) in enumerate(notes):
                    while time.monotonic() - start < at:
                        if r["gen"] != gen:
                            return
                        time.sleep(0.005)
                        for off_at, n in list(sounding):
                            if time.monotonic() - start >= off_at:
                                pub("piano/device/note", f"off {n}")
                                sounding.remove((off_at, n))
                    nxt = notes[i + 1][0] if i + 1 < len(notes) else at + REC_HOLD
                    # struck again before its own end: the earlier end is dropped, or it would silence
                    # this new strike at once (fast repeats sounded clipped)
                    sounding = [(o, n) for o, n in sounding if n != note]
                    pub("piano/device/note", f"on {note}")
                    sounding.append((at + max(0.08, min(REC_HOLD, nxt - at)), note))
                end = notes[-1][0] + REC_HOLD
                while time.monotonic() - start < end and r["gen"] == gen:
                    time.sleep(0.01)
            finally:
                for _, n in sounding:
                    pub("piano/device/note", f"off {n}")
                if r["gen"] == gen:
                    r["playing"] = False
        threading.Thread(target=go, daemon=True).start()

    def draw_rec_buttons(self, t):
        """Two touch buttons on free play's top left: record (or stop) and play back (or stop)."""
        P, cv, tr, r = self.P, self.cv, self.tr, self.rec
        items = [(tr("recStop") if r["on"] else tr("recB"), self.rec_toggle, r["on"]),
                 (tr("recStopPlay") if r["playing"] else tr("recPlay"), self.rec_play, r["playing"])]
        for i, (label, fn, live) in enumerate(items):
            x, y, w, h = 24 + i * 96, 64, 88, 34
            self.hit(x, y, w, h, fn)
            cv.rect(x, y, w, h, P["panel"], r=10)
            if live:
                cv.rect(x, y, w, h, (226, 118, 111) if i == 0 else P["sel"], r=10, width=2)
            if i == 0 and r["on"] and int(t * 2) % 2:
                cv.circle(x + 14, y + h / 2, 5, (226, 118, 111))
            lab, ls = self.fit_text(label, w - (30 if i == 0 else 14), 13, True)
            cv.text(lab, x + w / 2 + (6 if i == 0 else 0), y + 22, ls, P["ink"], True, "center")
