"""views_check.py - the full check: Settings › System › Full check, as the approved demo shows it.

Six screens: the opening, the automatic checks (device/fullcheck.py, in background threads, each
row filled as its area ends), the 9 guided steps, and the summary. The diagnosis and the hardware
test stay as they are; this runs besides them.

Nothing the check does stays on the device. During the guided steps the keys are only named
(piano/device/test "quiet": armonico_pi.py plays nothing and sends nothing on, and the power button
held does not shut down), the brightness steps are drawn over the frame and never saved, and
leaving the screen at any point, a finished step or a skipped one, puts it all back: test mode off,
the brightness the settings have, the volume never touched. The only file written is the report.
The power button's step shows the shutdown question with a Yes that does not shut down.

After an update, the first start of the new version offers the check in one line at the top of
the rest screen (DATA_DIR/device/check-offer.json), once for each version.
"""
import json
import os
import threading
import time
from pathlib import Path

from .canvas import W, H
from .widgets import BAR, RED, NOTE_NAMES, WHITES, BLACKS, RING_INSET, alpha
from .texts import T

try:
    import fullcheck                               # device/fullcheck.py
except Exception:                                  # pragma: no cover - the screen still runs without it
    fullcheck = None

STEPS = ("touch", "keys", "buttons", "power", "right", "left", "quality", "usb", "bright")
WAIT = 30                       # seconds a key or a button has to answer
TONE_EVERY = 2.0                # seconds between the speaker steps' tones
LEVELS = (100, 75, 50, 25)      # the brightness step
LEVEL_EVERY = 1.2
PHRASE = ((60, 0.0), (64, 0.28), (67, 0.56), (72, 0.84), (67, 1.12), (64, 1.4), (60, 1.68))
PHRASE_HOLD_MS = 900            # each note of the phrase held this long,
PHRASE_RELEASE_MS = 220         # then let go as a key is (armonico_pi.RELEASE_MS)
BUTTONS = (("menu", "☰"), ("down", "▼"), ("up", "▲"), ("ok", "✓"))
OFFER = "check-offer.json"


def released(pg, snd, hold_ms=PHRASE_HOLD_MS, release_ms=PHRASE_RELEASE_MS):
    """The note held hold_ms and then faded to silence over release_ms, the fade put into the samples
    themselves. Played with maxtime=900 each note stopped at once, a click at every note's end: in a
    recording of the check, 7 clicks in a row at 0.28 s apart, each 0.9 s after its note began, heard
    as popcorn, and the last note fell from -27 dB to silence within 1 ms. SDL_mixer's own fadeout is
    no cure: it moves the level once a block, in steps (the crackle the keys once had).
    A mixer that is not 16-bit (never set so here) gets the note untouched, to its end."""
    from array import array
    freq, fmt, chans = pg.mixer.get_init()
    if fmt != -16:
        return snd
    raw = array("h", snd.get_raw())
    hold = int(freq * hold_ms / 1000) * chans
    rel = max(1, int(freq * release_ms / 1000))
    end = min(len(raw), hold + rel * chans)
    out = raw[:end]
    for i in range(hold, end):
        x = 1.0 - ((i - hold) // chans) / rel
        out[i] = int(out[i] * x * x)
    return pg.mixer.Sound(buffer=out.tobytes())


def data_dir():
    return Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))


class CheckViews:
    def init_check_state(self):
        self.chk = None
        self.chk_light = None                      # the brightness step's level, drawn; None: the settings' own
        self.chk_offer = False

    # ------------------------------------------------------------------ the offer after an update
    def chk_version(self):
        try:
            return (Path(__file__).resolve().parent.parent.parent / "VERSION").read_text().strip()
        except OSError:
            return ""

    def chk_offer_load(self):
        """The first start after the installed version changed offers the check, once for that version.
        A new install (the setup not done yet) does not: there was no update."""
        f = data_dir() / "device" / OFFER
        v = self.chk_version()
        try:
            d = json.loads(f.read_text())
            d = d if isinstance(d, dict) else None
        except (OSError, ValueError):
            d = None
        if d is None:
            new = not self.st.get("setup_done")
            self.chk_offer_write(v, done=new)
            self.chk_offer = not new and bool(v)
        elif d.get("version") != v:
            self.chk_offer_write(v, done=False)
            self.chk_offer = bool(v)
        else:
            self.chk_offer = not d.get("done")

    def chk_offer_write(self, version, done):
        f = data_dir() / "device" / OFFER
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f".{OFFER}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"version": version, "done": bool(done)}))
            tmp.replace(f)
        except OSError:
            pass

    def chk_offer_off(self):
        if self.chk_offer:
            self.chk_offer = False
            self.chk_offer_write(self.chk_version(), done=True)
            self.layer_dirty()

    def chk_offer_note(self):
        """The line on the rest screen (views_main.top_notes), or None."""
        if not self.chk_offer:
            return None
        return self.tr("chk.notice", v=self.version()), lambda: self.open_check("rest")

    # ------------------------------------------------------------------ opening and leaving
    def open_check(self, back="settings", mode="both", only=None, topic=None):
        """mode: "both" (automatic, then the guided steps), "auto" or "guided"; `only` = (area keys, step
        names) for the quick check of one area. The same report is saved in every mode."""
        self.chk_offer_off()                       # running it is the answer to the offer
        areas, steps = (list(only[0]), list(only[1])) if only else (None, list(STEPS))
        if not getattr(self, "keyboards", None):
            # no USB MIDI keyboard connected: its step is left out before the start, so "step 3 of 9" counts
            # only the steps shown (chk_step_begin also passes over it when one is unplugged on the way)
            steps = [x for x in steps if x != "usb"]
        if mode == "auto":
            steps = []
        elif mode == "guided":
            areas = []
        self.chk = {"phase": "intro", "back": back, "mode": mode, "only": areas, "steps": steps, "gen": 0, "rows": [], "areas": [], "auto_end": None,
                    "topic": topic, "step": 0, "res": {}, "summary": None, "report": None, "fixed": set(), "first": 0}
        self.chk_light = None
        self.go("check")

    def chk_testing(self):
        """The guided steps are on screen: the keys are named and play nothing (follow_input)."""
        c = self.chk
        return self.view == "check" and bool(c) and c["phase"] == "guided"

    def chk_leave(self):
        """Leaving the screen: test mode off and the brightness back (both follow the view, so they come
        back on the next frame), the threads' answers dropped, and what was done kept in a report."""
        c = self.chk
        self.chk_light = None
        if not c:
            return
        if c["phase"] == "guided":
            self.chk_save(partial=True)
        if c["phase"] in ("auto", "guided", "intro"):
            c["gen"] += 1
            c["phase"] = "left"

    def chk_back(self):
        c = self.chk
        back = c["back"] if c else "settings"
        self.go(back)

    # ------------------------------------------------------------------ part 1: the automatic checks
    def chk_start(self):
        c = self.chk
        if not fullcheck:
            return
        c["gen"] += 1
        gen = c["gen"]
        cfg = fullcheck.diag.config()
        c["areas"] = [k for k, _ in fullcheck.areas(cfg, c["only"])]
        if not c["areas"]:                          # no automatic part: straight to the guided steps (or the end)
            c["rows"] = []
            if c["steps"]:
                self.chk_guided_start()
            else:
                self.chk_summary_start()
            return
        c["rows"] = [None] * len(c["areas"])
        c["phase"], c["auto_end"] = "auto", None
        from . import system
        ctx = {"settings": dict(self.st), "online": self.online(), "keyboards": list(getattr(self, "keyboards", []) or []),
               "guided": list(self.chk.get("steps") or []),
               "volume": self.volume_now(), "request": lambda a, timeout=10, **kw: system.request(a, timeout=timeout, **kw),
               "hand_steps": "keys" in c["steps"]}      # False: the keys are not pressed in this run, so "not tested by hand" is no warning

        def one(i, items):
            if c["gen"] == gen:
                c["rows"][i] = items

        def go():
            try:
                fullcheck.run_all(cfg, ctx, on_area=one, only=c["areas"])
            except Exception as e:                  # never on the screen as a crash: as one row not checked
                if c["gen"] == gen:
                    for i, r in enumerate(c["rows"]):
                        if r is None:
                            c["rows"][i] = [fullcheck.item(fullcheck.SKIP, c["areas"][i], fullcheck.L("chk.notChecked"),
                                                           fullcheck.L("chk.d.failed", e=str(e)[:120]))]
            if c["gen"] == gen:
                c["auto_end"] = self.t()
        threading.Thread(target=go, daemon=True, name="full-check").start()

    def chk_row(self, items):
        """An area's row: its worst result (a failure, then a warning), or the first result that says
        more than OK."""
        F = fullcheck
        for lvl in (F.PROBLEM, F.NOTE):
            bad = [i for i in items if i.level == lvl]
            if bad:
                return lvl, bad[0].row
        ok = [i for i in items if i.level == F.OK]
        if not ok:
            return F.SKIP, items[0].row if items else F.L("chk.notChecked")
        okt = F.L("chk.ok")
        return F.OK, next((i.row for i in ok if i.row != okt and i.row[0]), okt)

    def view_check(self, t):
        c = self.chk
        if not c or c["phase"] == "left" or not fullcheck:
            self.go("settings")
            return
        self.hills(0.3)
        getattr(self, "chk_" + c["phase"])(t)

    def chk_intro(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        # each way in has its own opening: the full check, the automatic checks, the steps by hand, and one
        # for each area of the quick check
        key = (self.chk.get("topic") or self.chk.get("mode")) if self.chk else "both"
        if "ci." + key + ".t" not in T:
            key = "both"
        self.header("", temp=True)
        cv.text(tr("ci." + key + ".t"), W / 2, 170, 30, P["ink"], True, "center")
        y = 214
        n_areas = len(fullcheck.AREAS) if fullcheck else 0
        for para in (tr("ci." + key + ".1", areas=n_areas, steps=len(STEPS)), tr("ci." + key + ".2")):
            for line in cv.wrap(para, 600, 16):
                cv.text(line, W / 2, y, 16, P["ink"], align="center")
                y += 25
            y += 8
        self.bar([("☰", tr("back"), self.chk_back), ("▼", "", None), ("▲", "", None), ("✓", tr("chk.start"), self.chk_start)])

    def chk_auto(self, t):
        P, cv, tr, c = self.P, self.cv, self.tr, self.chk
        F = fullcheck
        n, done = len(c["rows"]), sum(1 for r in c["rows"] if r is not None)
        over = c["auto_end"] is not None
        self.header(tr("chk.auto"), (tr("chk.autoDone") + " · " if over else "") + tr("chk.of", n=done, m=n), temp=True)
        cv.rect(24, 60, W - 48, 6, P["panel"], r=3)
        if done:
            cv.rect(24, 60, max(6, (W - 48) * done / max(1, n)), 6, P["ok"], r=3)
        per = (n + 1) // 2
        pitch = min(32.0, (H - BAR - 80) / max(1, per))
        he = self.lang == "he"
        k = 1 if he else 0
        for i, key in enumerate(c["areas"]):
            col, row = divmod(i, per)
            x, y, w, h = 24 + col * 382, 74 + row * pitch, 370, pitch - 4
            cv.rect(x, y, w, h, P["panel"], r=8)
            label = F.L("chk.a." + key)[k]
            lw = cv.measure(label, 13, True)
            cv.text(label, x + 12, y + h / 2 + 5, 13, P["ink"], True)
            items = c["rows"][i]
            if items is None:
                self.icon(self.spinner(t + i * 0.1), x + w - 18, y + h / 2 + 5, 13, P["dim"])
                continue
            lvl, text = self.chk_row(items)
            mark = {F.OK: "✓", F.NOTE: "!", F.PROBLEM: "✕", F.SKIP: "·"}[lvl]
            colr = {F.OK: P["ok"], F.NOTE: P["gold"], F.PROBLEM: RED, F.SKIP: P["dim"]}[lvl]
            s = f"{mark} {text[k]}"
            room = w - 24 - lw - 16
            if cv.measure(s, 13) <= room:
                cv.text(s, x + w - 12, y + h / 2 + 5, 13, colr, align="right")
            else:
                cv.ticker(s, x + w - 12 - room, y + h / 2 + 5, room, 13, colr, t)
        # the list stays until ✓: Next to the steps, or Finish to the summary (nothing moves on by itself)
        nxt = (self.chk_guided_start if c["steps"] else self.chk_summary_start) if over else None
        self.bar([("☰", tr("back") if over else tr("chk.stop"), self.chk_back), ("▼", "", None), ("▲", "", None),
                  ("✓", (tr("chk.next2") if c["steps"] else tr("chk.finish")) if over else "", nxt)])

    # ------------------------------------------------------------------ part 2: the guided steps
    def chk_guided_start(self):
        c = self.chk
        c["phase"], c["step"] = "guided", 0
        self.chk_step_begin()

    def chk_step_begin(self):
        c = self.chk
        c["st"] = {"t0": self.t(), "last": self.t()}
        c["gen"] += 1
        step = c["steps"][c["step"]]
        if step == "usb" and not getattr(self, "keyboards", None):
            # nothing connected: nothing to press, and no screen for it. It is not a step that was skipped, so it
            # leaves no result and is not counted or listed as "not checked" (only steps that were on the screen are)
            self.chk_advance()
            return
        if step in ("right", "left", "quality") and not self.mixer_ready():
            c["st"]["nosound"] = True
            return
        if step in ("right", "left"):
            self.chk_repeat(lambda: self.chk_tone(step), TONE_EVERY)
        elif step == "quality":
            self.chk_phrase()

    def chk_repeat(self, fn, every):
        """fn now and every `every` seconds, while this step of this check is on screen."""
        c = self.chk
        gen = c["gen"]

        def go():
            while self.chk is c and c["gen"] == gen and self.view == "check":
                try:
                    fn()
                except Exception:
                    return
                time.sleep(every)
        threading.Thread(target=go, daemon=True, name="check-tone").start()

    def chk_tone(self, side):
        s = self.screen
        if not hasattr(s, "test"):
            s.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": ""}
        s.test_tone(side, quiet=True)

    def chk_phrase(self):
        """A short phrase on the piano's own samples, at the volume the device plays now."""
        c = self.chk
        gen = c["gen"]
        vol = max(0, min(100, int(self.volume_now() or 0)))
        c["st"]["vol"] = vol
        pg = self.pg
        folder = data_dir() / "device" / "samples"

        def go():
            try:
                sounds = {n: released(pg, pg.mixer.Sound(str(folder / f"{n}.wav"))) for n in {n for n, _ in PHRASE}}
            except Exception:
                if self.chk is c and c["gen"] == gen:
                    c["st"]["nosamples"] = True
                return
            for snd in sounds.values():
                snd.set_volume((vol / 100) ** 3)      # as the keys hear it (armonico_pi.loudness)
            t0 = time.monotonic()
            for n, at in PHRASE:
                if self.chk is not c or c["gen"] != gen or self.view != "check":
                    break
                time.sleep(max(0.0, at - (time.monotonic() - t0)))
                with getattr(self.screen, "mixer_lock", threading.Lock()):
                    sounds[n].play()
        threading.Thread(target=go, daemon=True, name="check-phrase").start()

    def chk_result(self, level, row_key, todo_key="", detail_key=None, link="", hold=False, shown=None, **kw):
        """The current step's result. An answer or a skip goes straight on to the next step (or the summary after
        the last); a step that ended by itself (every key pressed, the time over) holds its result on the screen
        until Next: hold=True, with `shown` the line to show (a text key) when the row is not the one to show."""
        F, c = fullcheck, self.chk
        step = c["steps"][c["step"]]
        row = F.L(row_key, **kw)
        c["res"][step] = F.Item(level, step, F.L("chk.s." + step), row, F.L(detail_key, **kw) if detail_key else row,
                                F.L(todo_key) if todo_key else F.NONE, "", link)
        self.chk_light = None
        if hold:
            c["gen"] += 1                           # a tone or a phrase still going stops
            text = self.tr(shown, **kw) if shown else row[1 if self.lang == "he" else 0]
            c["st"]["held"] = (level, text[:1].upper() + text[1:])      # a row starts small ("keys 3 not responding")
            return
        self.chk_advance()

    def chk_advance(self):
        c = self.chk
        if c["step"] + 1 >= len(c["steps"]):
            self.chk_summary_start()
            return
        c["step"] += 1
        self.chk_step_begin()

    def chk_skip(self):
        if self.chk["st"].get("held"):
            self.chk_advance()                      # the result is recorded already: ☰ moves on
            return
        self.chk_result(fullcheck.SKIP, "chk.skipped")

    def chk_answer(self, yes):
        F, c = fullcheck, self.chk
        step = c["steps"][c["step"]]
        if step in ("right", "left"):
            self.chk_result(F.OK if yes else F.PROBLEM, "chk.g.heard" if yes else "chk.g.notHeard",
                            "" if yes else f"chk.g.{step}Do")
        elif step == "quality":
            self.chk_result(F.OK if yes else F.PROBLEM, "chk.g.clean" if yes else "chk.g.crackle", "" if yes else "chk.g.crackleDo")
        elif step == "bright":
            u = self.chk_user_level()
            self.chk_result(F.OK if yes else F.PROBLEM, "chk.g.brightOk" if yes else "chk.g.brightNo",
                            "" if yes else "chk.g.brightDo", u=u)

    def chk_user_level(self):
        keep, self.chk_light = self.chk_light, None
        try:
            return self.light_level()
        finally:
            self.chk_light = keep

    # ---- what comes in while a step shows: touches, keys, buttons, the power button, USB
    def chk_touch(self, x, y):
        """A touch on the touch step: the highlighted circle, in order. True when it was taken."""
        c = self.chk
        if not (self.chk_testing() and c["steps"][c["step"]] == "touch") or self.confirm or c["st"].get("held"):
            return False
        st = c["st"]
        done = st.setdefault("corners", [])
        targets = self.touch_targets()
        for i, (cx, cy) in enumerate(targets):
            if (x - cx) ** 2 + (y - cy) ** 2 <= 34 ** 2:
                want = len(done)
                if i == want:
                    done.append(i)
                    st["last"] = self.t()
                    if len(done) == 4:
                        F = fullcheck
                        if st.get("wrong", 0) >= 2:
                            self.chk_result(F.PROBLEM, "chk.g.touchWrong", "chk.g.touchDo", "chk.g.touchWrongD", hold=True, n=st["wrong"])
                        else:
                            self.chk_result(F.OK, "chk.g.touchOk", hold=True, shown="chk.g.touchDone")
                elif i not in done:
                    st["wrong"] = st.get("wrong", 0) + 1
                return True
        return False

    def chk_key(self, pc):
        c = self.chk
        if self.chk_testing() and c["steps"][c["step"]] == "keys" and 0 <= pc < self.key_total() and not c["st"].get("held"):
            keys = c["st"].setdefault("keys", set())
            if pc not in keys:
                keys.add(pc)
                c["st"]["last"] = self.t()
            if len(keys) == self.key_total():
                self.chk_result(fullcheck.OK, "chk.g.keysOk", hold=True, shown="chk.g.keysDone", t=self.key_total())

    def chk_button(self, name, hold):
        """A button on the buttons step counts it, instead of acting as the bar. True when taken."""
        c = self.chk
        if not (self.chk_testing() and c["steps"][c["step"]] == "buttons"):
            return False
        if c["st"].get("held"):
            return False                            # the result shows: the bar is the bar again (✓ Next, ☰ on)
        if hold:
            if name == "menu":
                self.chk_skip()
            return True
        got = c["st"].setdefault("btns", set())
        if name in dict(BUTTONS):
            if name not in got:
                got.add(name)
                c["st"]["last"] = self.t()
            if len(got) == 4:
                self.chk_result(fullcheck.OK, "chk.g.btnOk", hold=True, shown="chk.g.btnDone")
        return True

    def chk_power(self):
        """The power button on its step: the question shows, and its Yes shuts nothing down. True when taken."""
        c = self.chk
        if not (self.chk_testing() and c["steps"][c["step"]] == "power") or c["st"].get("held"):
            return False
        F = fullcheck
        self.woke()
        self.ask(self.tr("shutQ"), lambda: self.chk_result(F.OK, "chk.g.powerOk", detail_key="chk.g.powerYes", hold=True, shown="chk.g.powerDone"),
                 no=lambda: self.chk_result(F.OK, "chk.g.powerOk", detail_key="chk.g.powerNo", hold=True, shown="chk.g.powerDone"))
        return True

    def chk_usb(self, name, note):
        c = self.chk
        if self.chk_testing() and c["steps"][c["step"]] == "usb":
            c["st"]["usb"] = (name or self.tr("c.kbUsb"), note)

    # ---- drawing the steps
    def chk_guided(self, t):
        P, cv, tr, c = self.P, self.cv, self.tr, self.chk
        F = fullcheck
        step = c["steps"][c["step"]]
        st = c["st"]
        now = self.t()
        # a step that runs out of time ends here, before anything is drawn: its result shows in this same frame
        if step in ("keys", "buttons") and not st.get("held") and WAIT - (now - st["last"]) <= 0:
            self.chk_timeout(step)
        held = st.get("held")
        self.header(tr("chk.stepOf", n=c["step"] + 1, m=len(c["steps"]), s=tr("chk.s." + step)), "" if held else tr("chk.skipHint"),
                    inset=56 if step == "touch" else 0, temp=True)
        skip = ("☰", tr("chk.skip"), self.chk_skip)
        if held:
            # the step ended: its result stays on the screen, and ✓ goes on (☰ too, without a second "Next")
            level, text = held
            col = {F.OK: P["ok"], F.PROBLEM: RED, F.NOTE: P["gold"]}.get(level, P["dim"])
            mark = {F.OK: "✓", F.PROBLEM: "✕", F.NOTE: "!"}.get(level, "·")
            for n, line in enumerate(cv.wrap(f"{mark} {text}", W - 80, 17, True)[:2]):
                cv.text(line, W / 2, H - BAR - 40 + n * 24, 17, col, True, "center")
            skip = ("☰", "", self.chk_advance)
        if step == "touch":
            n = len(st.get("corners", []))
            self.chk_ask(tr("chk.i.touch"), tr("chk.i.touchS", n=n), 200)
            self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", tr("chk.next2") if held else "", self.chk_advance if held else None)],
                     inset=RING_INSET)
            with cv.physical():                      # over everything, the bar too, in the screen's real corners
                for i, (tx, ty) in enumerate(self.touch_targets()):
                    if i in st.get("corners", []):
                        cv.circle(tx, ty, 18, P["ok"])
                    elif i == n:
                        cv.circle(tx, ty, 18, P["gold"], width=5)
                    else:
                        cv.circle(tx, ty, 14, alpha(P["dim"], 0.6), width=2)
            return
        if step == "keys":
            keys = st.setdefault("keys", set())
            left = max(0, int(WAIT - (now - st["last"]) + 0.999))
            sub = tr("chk.of", n=len(keys), m=self.key_total()) if held else tr("chk.i.keysS", n=len(keys), t=self.key_total(), s=left)
            self.chk_ask(tr("chk.i.keys", t=self.key_total()), sub, 130)
            self.chk_piano(keys, 196)
            self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", tr("chk.next2") if held else "", self.chk_advance if held else None)])
            return
        if step == "buttons":
            got = st.setdefault("btns", set())
            self.chk_ask(tr("chk.i.buttons"), tr("chk.of", n=len(got), m=4) if held else tr("chk.i.buttonsS", n=len(got)), 150)
            with cv.physical():
                for i, (name, sym) in enumerate(BUTTONS):
                    x, y = W / 2 - 2 * 116 + i * 116 + 4, 214
                    on = name in got
                    cv.rect(x, y, 108, 80, P["ok"] if on else P["panel"], r=12)
                    cv.text(sym, x + 54, y + 50, 26, (13, 20, 36) if on else P["ink"], True, "center")
            # the bar is the buttons themselves here: a touch on Skip skips, a press is counted (until the result shows)
            self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", tr("chk.next2") if held else "", self.chk_advance if held else None)])
            return
        if step == "power":
            self.chk_ask(tr("chk.i.power"), tr("chk.i.powerS"), 210)
            self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", tr("chk.next2") if held else "", self.chk_advance if held else None)])
            return
        if step in ("right", "left", "quality", "bright"):
            if st.get("nosound") or st.get("nosamples"):
                self.chk_ask(tr("chk.s." + step), tr("chk.noSound") if st.get("nosound") else tr("chk.noSamples"), 200)
                self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", "", None)])
                return
            if step == "bright":
                k = int((now - st["t0"]) / LEVEL_EVERY) % len(LEVELS)
                self.chk_light = LEVELS[k]
                sub = tr("chk.i.brightS", v=LEVELS[k], u=self.chk_user_level())
            elif step == "quality":
                sub = tr("chk.i.qualityS", v=st.get("vol", self.volume_now()))
            else:
                sub = tr("chk.i.toneS")
            self.chk_ask(tr("chk.i." + step), sub, 180)
            self.chk_yes_no(260)
            again = ("▲", tr("chk.again"), self.chk_phrase) if step == "quality" else ("▲", "", None)
            self.bar([skip, ("▼", tr("chk.no"), lambda: self.chk_answer(False)), again,
                      ("✓", tr("yes"), lambda: self.chk_answer(True))])
            return
        if step == "usb":
            got = st.get("usb")
            if got:
                name, note = got
                sub = tr("chk.g.usbOk", name=name, note=f"{NOTE_NAMES[note % 12]}{note // 12 - 1}")
            else:
                sub = tr("chk.i.usbS")
            self.chk_ask(tr("chk.i.usb"), sub, 200)
            nxt = (lambda: self.chk_result(F.OK, "chk.g.usbOk", name=name,
                                           note=f"{NOTE_NAMES[note % 12]}{note // 12 - 1}")) if got else None
            self.bar([skip, ("▼", "", None), ("▲", "", None), ("✓", tr("chk.next2") if got else "", nxt)])

    def chk_timeout(self, step):
        """The keys or the buttons step ran out of time: what was not pressed, held on the screen."""
        F, st = fullcheck, self.chk["st"]
        if step == "keys":
            keys = st.setdefault("keys", set())
            miss = [pc for pc in range(self.key_total()) if pc not in keys]
            self.chk_result(F.PROBLEM, "chk.g.key1" if len(miss) == 1 else "chk.g.keysN", "chk.g.keysDo", "chk.g.keysD",
                            link="test", hold=True, k=F.key_names(miss))
        else:
            got = st.setdefault("btns", set())
            miss = [sym for name, sym in BUTTONS if name not in got]
            self.chk_result(F.PROBLEM, "chk.g.btn1" if len(miss) == 1 else "chk.g.btnN", "chk.g.btnDo", "chk.g.btnD",
                            link="test", hold=True, b=" ".join(miss))

    def chk_ask(self, big, small, y):
        P, cv = self.P, self.cv
        for i, line in enumerate(cv.wrap(big, 700, 24, True)[:2]):
            cv.text(line, W / 2, y + i * 32, 24, P["ink"], True, "center")
        y2 = y + 32 * min(2, len(cv.wrap(big, 700, 24, True))) + 4
        for i, line in enumerate(cv.wrap(small, 700, 15)[:2]):
            cv.text(line, W / 2, y2 + i * 22, 15, P["dim"], align="center")

    def chk_yes_no(self, y):
        """Yes and No to touch, on the side of their buttons below: No over ▼, Yes over ✓ (physical)."""
        P, cv, tr = self.P, self.cv, self.tr
        with cv.physical():
            for x, label, yes in ((W / 2 - 170, tr("chk.no"), False), (W / 2 + 10, tr("yes"), True)):
                self.hit(x, y, 160, 56, lambda yes=yes: self.chk_answer(yes))
                cv.rect(x, y, 160, 56, P["panel"], r=14)
                cv.rect(x, y, 160, 56, P["ok"] if yes else RED, r=14, width=2)
                cv.text(label, x + 80, y + 36, 18, P["ink"], True, "center")

    def chk_piano(self, keys, y):
        """The keys small, as on the device (physical): each pressed one green. 13 keys: the high C on the right."""
        P, cv = self.P, self.cv
        kw, kh, gap = 60, 120, 6
        whites = WHITES + (12,)
        x0 = (W - len(whites) * kw - (len(whites) - 1) * gap) / 2
        with cv.physical():
            for i, pc in enumerate(whites):
                x = x0 + i * (kw + gap)
                on = pc in keys
                cv.rect(x, y, kw, kh, P["ok"] if on else P["key"], r=8)
                cv.text(str(i + 1) if pc < 12 else "1", x + kw / 2, y + kh - 14, 15, (13, 20, 36) if on else P["onkey"], True, "center")
            for pc, a in BLACKS:
                x = x0 + (a + 1) * (kw + gap) - 20
                on = pc in keys
                cv.rect(x - 3, y - 1, 46, 76, (13, 20, 36), r=7)      # a dark edge: pressed black and white keys apart
                cv.rect(x, y, 40, 72, P["ok"] if on else P["black"], r=6)

    # ------------------------------------------------------------------ the summary
    def chk_summary_start(self):
        """Worked out once: failures, then warnings, then how many passed. The report saved in a thread."""
        F, c = fullcheck, self.chk
        auto = [i for r in c["rows"] for i in (r or [])]
        guided = [c["res"][s] for s in c["steps"] if s in c["res"]]
        hand = c["res"].get("keys"), c["res"].get("buttons")
        if all(h is not None and h.level == F.OK for h in hand):
            # the keys and buttons were pressed by hand just now: the diagnosis' "not tested by hand" is answered
            auto = [i._replace(level=F.OK, row=F.L("chk.ok"), detail=F.L("chk.g.keysOk", t=self.key_total()), todo=F.NONE, link="")
                    if i.area == "gpio" and i.level == F.NOTE and i.link == "test" and i.row == F.L("chk.r.gpioNote") else i
                    for i in auto]
        items = auto + guided
        n = F.counts(items)
        tg = next((i for i in auto if i.area == "telegram"), None)
        c["summary"] = {"auto": auto, "guided": guided, "list": F.ordered(items), "n": n,
                        "skipped": [i for i in items if i.level == F.SKIP],
                        "tg": bool(tg and tg.level == F.OK)}
        c["phase"], c["first"] = "summary", 0
        self.chk_light = None
        self.chk_save()

    def chk_save(self, partial=False):
        F, c = fullcheck, self.chk
        if partial:
            auto = [i for r in c["rows"] for i in (r or [])]
            guided = [c["res"].get(s) or F.Item(F.SKIP, s, F.L("chk.s." + s), F.L("chk.notReached"),
                                                 F.L("chk.notReached"), F.NONE, "", "") for s in c["steps"]]
        else:
            auto, guided = c["summary"]["auto"], c["summary"]["guided"]
        c["report"] = "saving"

        def go():
            try:
                text = F.report_text(auto, guided, guided_done=bool(c["steps"]))
                c["report"] = F.save_report(text, {"DATA_DIR": str(data_dir())})
            except Exception as e:
                print(f"Full check: the report was not saved ({e})", flush=True)
                c["report"] = "failed"
        threading.Thread(target=go, daemon=True, name="check-report").start()

    def chk_fix(self, action):
        diag = fullcheck.diag
        c = self.chk
        k = 1 if self.lang == "he" else 0
        labels = [p[k] for p in diag.FIXES[action]]

        def work(job):
            res = diag.fix(action, progress=job.step)
            if not res or not all(s.get("ok") for s in res):
                job.fail(self.L("not fixed", "לא תוקן"))
                return
            c["fixed"].add(action)
        self.start_job(self.tr("j.fix"), labels, "check", work)

    def chk_send(self):
        """The summary to Telegram: root sends it with the token it keeps; the screen never has the token."""
        F, sm = fullcheck, self.chk["summary"]
        k = 1 if self.lang == "he" else 0
        lines = [{"level": "fail" if i.level == F.PROBLEM else "warn", "text": F.line(i, k)} for i in sm["list"]][:12]
        n = sm["n"]
        rep = self.chk.get("report")
        args = {"lang": self.lang, "problems": n[F.PROBLEM], "warnings": n[F.NOTE], "passed": n[F.OK], "skipped": n[F.SKIP],
                "lines": lines, "report": rep.name if isinstance(rep, Path) else ""}
        from . import system

        def go():
            ok, msg = system.request("telegram-report", timeout=60, **args)
            why = {"no-token": "tg.tokenAgain", "no-chats": "tg.noChats", "too soon": "chk.tgSoon"}.get(msg)
            self.toast(self.tr("chk.tgSent") if ok else self.tr(why) if why else self.tr("testNotSent", e=self.root_msg(msg)), 4)
        threading.Thread(target=go, daemon=True, name="check-telegram").start()

    def chk_summary(self, t):
        P, cv, tr, c = self.P, self.cv, self.tr, self.chk
        F, sm = fullcheck, c["summary"]
        k = 1 if self.lang == "he" else 0
        n = sm["n"]
        self.header(self.chk_counts_text(), temp=True)
        cards = []
        for i in sm["list"]:
            fixed = i.fix and i.fix in c["fixed"]
            act = self.chk_action(i) or (tr("chk.detail"), lambda i=i: self.chk_open_detail(i, "summary"))
            todo = tr("chk.fixed") if fixed else i.todo[k]
            cards.append(("✕" if i.level == F.PROBLEM else "!", RED if i.level == F.PROBLEM else P["gold"], F.line(i, k), todo, act,
                          lambda i=i: self.chk_open_detail(i, "summary")))
        rep = c.get("report")
        saved = tr("chk.savedTo", f="checks/" + rep.name) if isinstance(rep, Path) else \
            tr("chk.notSaved") if rep == "failed" else tr("chk.saving")
        every = (tr("chk.all"), self.chk_open_all)
        cards.append(("✓", P["ok"], tr("chk.passed1") if n[F.OK] == 1 else tr("chk.passed", n=n[F.OK]), saved, every, self.chk_open_all))
        if sm["skipped"]:
            names = ", ".join(dict.fromkeys(i.label[k] for i in sm["skipped"]))
            cards.append(("·", P["dim"], tr("chk.notRun", x=names), "", None, self.chk_open_all))
        # each card as tall as its lines: nothing is cut, a long line goes on to a second one
        zc = max(1.0, cv.zoom * 0.9)                 # the lines of a card keep apart as the interface grows
        laid = []
        for ic, col, title, todo, act, whole in cards:
            aw = (cv.measure(act[0], 13, True) + 28) if act else 0
            tl = cv.wrap(title, W - 48 - 60 - aw, 15, True)
            dl = cv.wrap(todo, W - 48 - 60 - aw, 13) if todo else []
            laid.append((ic, col, tl, dl, act, aw, 16 + 21 * zc * len(tl) + 18 * zc * len(dl), whole))
        top, bottom = 62, H - BAR - 6
        first = min(c["first"], max(0, len(laid) - 1))
        y = top
        shown = 0
        for ic, col, tl, dl, act, aw, h, whole in laid[first:]:
            if y + h > bottom and shown:
                break
            x, w = 24, W - 48
            self.hit(x, y, w, h, whole)               # the whole card opens its details; its action on top
            cv.rect(x, y, w, h, P["panel"], r=12)
            self.icon(ic, x + 26, y + 26, 18, col)
            ly = y + 26
            for line in tl:
                cv.text(line, x + 50, ly, 15, P["ink"], True)
                ly += 21 * zc
            for line in dl:
                cv.text(line, x + 50, ly - 2, 13, P["dim"])
                ly += 18 * zc
            if act:
                self.hit(x + w - aw - 6, y + 4, aw, h - 8, act[1])
                cv.text(act[0], x + w - 16, y + 26, 13, P["gold"], True, "right")
            y += h + 6
            shown += 1
        more = first + shown < len(laid)
        self.scroll_bar(top, bottom - top - 4, first, shown, len(laid))
        if len(laid) > shown:
            cv.text(self.page_counter(first, shown, len(laid)), W - 22, 47, 11, P["dim"], align="right")
            self.scroll_zone(24, top, W - 48, bottom - top, 70, lambda d: c.update(first=max(0, min(len(laid) - 1, c["first"] + d))))

        def scroll(d):
            c["first"] = max(0, min(len(laid) - 1, c["first"] + d))
        can_down, can_up = more, first > 0
        self.bar([("☰", tr("done"), self.chk_back),
                  ("▼", tr("down") if can_down else "", (lambda: scroll(1)) if can_down else None),
                  ("▲", tr("up") if can_up else "", (lambda: scroll(-1)) if can_up else None),
                  ("✓", tr("chk.tg") if sm["tg"] else "", self.chk_send if sm["tg"] else None)])

    def chk_action(self, i):
        """A result's own action: its fix, or the hardware test; None when it has neither."""
        F, c, tr = fullcheck, self.chk, self.tr
        if i.fix and i.fix in F.diag.FIXES and i.fix not in c["fixed"]:
            return tr("chk.fix"), lambda a=i.fix: self.chk_fix(a)
        if i.link == "test":
            return tr("chk.hw"), self.open_test
        if i.link == "bt":
            return tr("chk.btPage"), lambda: self.open_bt("quick")
        return None

    def chk_open_detail(self, item, back):
        c = self.chk
        c.update(phase="detail", detail=item, dback=back, dfirst=0)

    def chk_open_all(self):
        c = self.chk
        c.update(phase="all", afirst=c.get("afirst", 0))

    def chk_detail_lines(self, i):
        """The details' lines: [(kind, data)], kind "head" (a small heading), "text" (a line of the result), "log" (a
        line as the system wrote it, kept left to right), "svc" (a service: name, state, level). Each is one line high."""
        F, cv, tr = fullcheck, self.cv, self.tr
        k = 1 if self.lang == "he" else 0
        out = []
        wide = W - 72
        out.append(("head", tr("chk.found")))
        out += [("text", ln) for ln in cv.wrap(i.detail[k] or i.row[k], wide, 15)]
        if i.todo[k]:
            out.append(("gap", None))
            out.append(("head", tr("chk.todo")))
            out += [("text", ln) for ln in cv.wrap(i.todo[k], wide, 15)]
        lines = list(i.lines or ())
        if lines:
            svc = all(isinstance(e, tuple) and e and e[0] == "svc" for e in lines)
            out.append(("gap", None))
            out.append(("head", tr("chk.linesSvc", n=len(lines)) if svc else tr("chk.lines", n=len(lines))))
            for e in lines:
                if isinstance(e, tuple) and e and e[0] == "svc":
                    name, state, lvl = F.svc_row(e)
                    out.append(("svc", (name[k], state[k], lvl)))
                else:
                    for part in cv.wrap(str(e), W - 76, 12) or [""]:
                        out.append(("log", part))
        rep = self.chk.get("report")
        if isinstance(rep, Path):
            out.append(("gap", None))
            out.append(("note", tr("chk.fullIn", f="checks/" + rep.name)))
        return out

    def chk_detail(self, t):
        P, cv, tr, c = self.P, self.cv, self.tr, self.chk
        F = fullcheck
        i = c["detail"]
        k = 1 if self.lang == "he" else 0
        self.header(i.label[k], temp=True)              # the area's name: the result itself is the first line below
        lines = self.chk_detail_lines(i)
        pitch = {"head": 26, "text": 22, "log": 18, "svc": 26, "gap": 8, "note": 22}
        top, bottom = 62, H - BAR - 6
        first = min(c.get("dfirst", 0), max(0, len(lines) - 1))
        y, shown = top, 0
        col = {F.OK: P["ok"], F.NOTE: P["gold"], F.PROBLEM: RED, F.SKIP: P["dim"]}
        for kind, data in lines[first:]:
            h = pitch[kind] * max(1.0, min(cv.zoom, 1.3))
            if y + h > bottom and shown:
                break
            shown += 1
            if kind == "head":
                cv.text(data, 28, y + 19, 13, P["gold"], True)
            elif kind == "text":
                cv.text(data, 36, y + 16, 15, P["ink"])
            elif kind == "note":
                cv.text(cv.fit(data, W - 72, 12), 36, y + 16, 12, P["dim"])
            elif kind == "log":
                with cv.physical():                  # the system's own words: left to right, on the left, in any language
                    cv.rect(28, y, W - 56, h, P["panel"])
                    cv.text(data, 38, y + 13, 12, P["ink"])
            elif kind == "svc":
                name, state, lvl = data
                cv.rect(28, y + 1, W - 56, h - 3, P["panel"], r=6)
                half = (W - 72) * 0.45
                cv.text(cv.fit(name, half, 13, True), 40, y + 17, 13, P["ink"], True)
                cv.text(cv.fit(state, W - 80 - half - 10, 13), W - 40, y + 17, 13, col[lvl] if lvl != F.OK else P["ink"], align="right")
            y += h
        more = first + shown < len(lines)
        self.scroll_bar(top, bottom - top - 4, first, shown, len(lines))

        def move(d):
            c["dfirst"] = max(0, min(len(lines) - 1, c.get("dfirst", 0) + d))
        if more or first:
            self.scroll_zone(24, top, W - 48, bottom - top, 20, move)
        act = self.chk_action(i)
        self.bar([("☰", tr("back"), lambda: c.update(phase=c.get("dback", "summary"))),
                  ("▼", tr("down") if more else "", (lambda: move(4)) if more else None),
                  ("▲", tr("up") if first else "", (lambda: move(-4)) if first else None),
                  ("✓", act[0] if act else "", act[1] if act else None)])

    def chk_all_items(self):
        sm = self.chk["summary"]
        return list(sm["auto"]) + list(sm["guided"])

    def chk_all(self, t):
        """Every result of the check, the passed ones too, one row each, in the order they were checked."""
        P, cv, tr, c = self.P, self.cv, self.tr, self.chk
        F = fullcheck
        k = 1 if self.lang == "he" else 0
        items = self.chk_all_items()
        self.header(tr("chk.allT", n=len(items)), temp=True)
        pitch = 32 * max(1.0, min(cv.zoom, 1.3))
        top, bottom = 62, H - BAR - 6
        per = max(1, int((bottom - top) // pitch))
        first = min(c.get("afirst", 0), max(0, len(items) - per))
        mark = {F.OK: "✓", F.NOTE: "!", F.PROBLEM: "✕", F.SKIP: "·"}
        col = {F.OK: P["ok"], F.NOTE: P["gold"], F.PROBLEM: RED, F.SKIP: P["dim"]}
        for n, i in enumerate(items[first:first + per]):
            y = top + n * pitch
            self.hit(24, y, W - 48, pitch - 4, lambda i=i: self.chk_open_detail(i, "all"))
            cv.rect(24, y, W - 48, pitch - 4, P["panel"], r=8)
            lw = min(cv.measure(i.label[k], 14, True), (W - 72) * 0.45)
            cv.text(cv.fit(i.label[k], lw + 1, 14, True), 36, y + 19, 14, P["ink"], True)
            room = W - 72 - lw - 20
            cv.text(cv.fit(f"{mark[i.level]} {i.row[k]}", room, 13), W - 36, y + 19, 13, col[i.level], align="right")

        def move(d):
            c["afirst"] = max(0, min(max(0, len(items) - per), c.get("afirst", 0) + d))
        self.list_marks(first, per, len(items), top, pitch, move)
        more = first + per < len(items)
        self.bar([("☰", tr("back"), lambda: c.update(phase="summary")),
                  ("▼", tr("down") if more else "", (lambda: move(per - 1)) if more else None),
                  ("▲", tr("up") if first else "", (lambda: move(-(per - 1))) if first else None), ("✓", "", None)])

    def chk_counts_text(self, lang=None):
        F, n = fullcheck, self.chk["summary"]["n"]
        from .texts import check_counts
        return check_counts(lang or self.lang, n[F.PROBLEM], n[F.NOTE], n[F.OK], n[F.SKIP])

    def chk_left(self, t):
        self.go("settings")
