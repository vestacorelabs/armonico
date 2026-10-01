"""views_main.py - the screens of playing: rest, lesson, summary, songs, free play, hardware test,
the network screen and the two-displays chooser."""
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from .canvas import W, H
from .characters import LOW
from .texts import NET_LISTS, NET_LISTS_EXTERNAL
from .widgets import BAR, RED, PINK, NOTE_NAMES, RING_INSET, alpha, key_name
from . import sky as sky_mod
from .light import DAY_LEVELS

try:
    import diag                                    # device/diag.py
except Exception:                                  # pragma: no cover
    diag = None

SKYH = sky_mod.SKYH
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
LABEL_MODES = (("num", "1 2 3"), ("abc", "C D E"), ("do", "Do Re Mi"), ("none", "None"))
LESSON_UI_PORT = os.environ.get("LESSON_UI_PORT", "8099")
APP_DIR = Path(__file__).resolve().parent.parent.parent


def song_title(name):
    return str(name or "").replace("_", " ").strip().capitalize()


class MainViews:
    # ------------------------------------------------------------------ the rest screen
    def day_name(self):
        d = self.local()
        if self.lang == "he":
            return f"יום {DAYS_HE[d.weekday()]}, {d.day} ב{sky_mod.MONTHS_HE[d.month - 1]}"
        return f"{DAYS_EN[d.weekday()]} {d.day} {sky_mod.MONTHS_EN[d.month - 1]}"

    def song_card_data(self):
        """The song played last, and for each of its parts: 2 remembered, 1 fading, 0 not learned yet.
        From the overview and the active profile's progress file; worked out every 10 seconds."""
        at, data = self.card_cache
        if time.time() - at < 10:
            return data
        data = None
        songs = [s for s in self.screen.songs() if s.get("last")]
        if songs:
            song = max(songs, key=lambda s: s.get("last") or 0)
            total, learned = int(song.get("total", 0)), int(song.get("learned", 0))
            parts = self.part_records(song["name"])
            states = []
            for k in range(total):
                p = parts.get(str(k)) if parts else None
                if p is None:
                    states.append(2 if parts is None and k < learned else 0)
                else:
                    states.append(1 if self.fading(p) else 2)
            data = {"name": song["name"], "states": states}
        self.card_cache = (time.time(), data)
        return data

    def part_records(self, name):
        data_dir = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
        try:
            active = json.loads((data_dir / "profiles.json").read_text()).get("active", "main")
        except (OSError, ValueError, AttributeError):
            active = "main"
        folder = data_dir if active == "main" or not re.fullmatch(r"p[0-9a-f]{8}", str(active)) else data_dir / "profiles" / active
        try:
            rec = json.loads((folder / "lessons.json").read_text()).get(name) or {}
            return rec.get("parts") if isinstance(rec.get("parts"), dict) else None
        except (OSError, ValueError, AttributeError):
            return None

    def fading(self, part):
        """The learning engine's own measure: a part this unlikely to be remembered now is fading."""
        try:
            if str(APP_DIR / "lessons") not in sys.path:
                sys.path.append(str(APP_DIR / "lessons"))
            import piano_learner
            r = piano_learner.recall_now(part, time.time())
            if r is not None:
                return r < getattr(piano_learner, "FADING", 0.7)
        except Exception:
            pass
        return part.get("streak", 0) == 0

    def rest_bar(self):
        self.bar([("☰", self.tr("menu"), None), ("▼", self.tr("oct"), lambda: self.octave_move(-1)),
                  ("▲", self.tr("oct"), lambda: self.octave_move(1)), ("✓", self.tr("keep"), self.keep_practicing)])

    def keep_practicing(self):
        card = self.song_card_data()
        if card:
            self.screen.start_lesson(card["name"])
        else:
            self.go("songs")

    def oct_note(self, t):
        if t - self.oct_at > 1.6:
            return
        P, cv = self.P, self.cv
        a = min(1.0, (1.6 - (t - self.oct_at)) * 3)
        label = self.tr("octNote", n=self.screen.octave)
        w = cv.measure(label, 16, True) + 40
        cv.rect(W / 2 - w / 2, 196, w, 40, alpha(P["panel"], a), r=20)
        cv.text(label, W / 2, 222, 16, alpha(P["ink"], a), True, "center")

    def top_notes(self):
        """A problem found by the diagnosis first, then an update."""
        P, cv = self.P, self.cv
        notes = []
        bad = next((c for c in self.checks if c.level == 2), None)
        if bad:
            what = bad.detail_he if self.lang == "he" else bad.detail_en
            notes.append((self.tr("problem", what=cv.fit(what, 300, 13, True) if what else ""), RED,
                          lambda: self.open_diag("rest")))
        if self.update:
            notes.append((self.tr("update", v=self.update[0]), P["gold"], lambda: self.open_upd("rest")))
        offer = self.chk_offer_note() if hasattr(self, "chk_offer_note") else None
        if offer:                                    # the first start after an update: the full check, once
            notes.append((offer[0], P["ok"], offer[1]))
        for i, (label, col, fn) in enumerate(notes):
            label = cv.fit(label, 560, 13, True)
            w = cv.measure(label, 13, True) + 28
            x, y = W / 2 - w / 2, 10 + i * 32
            self.hit(x, y, w, 26, fn)
            cv.rect(x, y, w, 26, P["panel"], r=13)
            cv.rect(x, y, w, 26, col, r=13, width=1.5)
            cv.text(label, W / 2, y + 18, 13, P["ink"], True, "center")

    def view_rest(self, t, part="all"):
        """part: "all"; or, for the still layer (App.draw_rest_layer), "still" for what stays the same
        and "live" for what moves (the figure, the song card, the octave note)."""
        cv, s = self.cv, self.screen
        ms = self.now_ms()
        if part != "all":
            return self.rest_animation(t, ms, part)
        frame, painted = None, False
        if self.rest_content == "video":
            # the frame is written straight into the screen when it is the screen's size: one copy,
            # instead of a copy into a picture and a second one onto the screen (8 MB each on the 24")
            paint = getattr(s.media, "paint_into", None)
            painted = bool(paint and paint(cv.surf))
            frame = None if painted else s.media.surface()
        pic = self.apod.image if self.rest_content == "nasa" else None
        if painted or frame is not None or pic is not None:
            if not painted:
                img = frame if frame is not None else pic
                iw, ih = img.get_size()
                k = max(cv.vw / iw, cv.vh / ih)
            with cv.physical():
                if not painted:
                    cv.image(img, cv.vx + (cv.vw - iw * k) / 2, cv.vy + (cv.vh - ih * k) / 2, iw * k, ih * k)
                if pic is not None:
                    cv.rect(cv.vx, H - BAR - 54, cv.vw, 54, (0, 0, 0, 128))
            self.top_notes()
            if pic is not None:
                d = self.apod.date
                when = ""
                if d:
                    when = (f", {d.day} ב{sky_mod.MONTHS_HE[d.month - 1]}" if self.lang == "he"
                            else f", {sky_mod.MONTHS_EN[d.month - 1]} {d.day}")
                cv.text(self.tr("picture") + when, 16, H - BAR - 32, 13, (255, 255, 255, 204))
                with cv.physical() if self.lang != "he" else _null():
                    cv.text(cv.fit(self.apod.name, 760, 16, True), 16, H - BAR - 11, 16, (255, 255, 255), True)
            else:
                line = s.media.lyric()
                if line:
                    with cv.physical():
                        cv.text(cv.fit(line, 760, 21), W / 2, H - BAR - 20, 21, (255, 255, 255), align="center")
            if not (painted or frame is not None):   # no clock over the video: the sleep screen has it
                cv.text(self.hm(ms), 22, 62, 46, (255, 255, 255), True)
            self.oct_note(t)
            if time.time() < getattr(self, "bar_until", 0):
                self.rest_bar()                      # hidden over the video and the picture until a touch
            else:
                # the buttons are not drawn, but ☰ still does what it does with them up
                self.bar_items = [("☰", "", lambda n=self.next_view(): self.go(n))]
            return
        if getattr(self, "covered", False):
            cv.fill((0, 0, 0))                       # the video was there a moment ago, and has no frame now
            self.background(t)
        self.rest_animation(t, ms, "all")

    def rest_animation(self, t, ms, part):
        P, cv, s = self.P, self.cv, self.screen
        if part == "live":
            self.rest_moving(t)
            return
        self.hills(1)
        self.top_notes()
        st, he = self.sky_st, self.lang == "he"
        if not self.online():
            label = self.tr("noNet")
            bw = cv.measure(label, 13, True) + 26
            self.hit(W - 26 - bw, 118, bw, 26, lambda: self.open_net("rest"))
            cv.rect(W - 26 - bw, 118, bw, 26, P["panel"], r=13)
            cv.text(label, W - 39, 136, 13, P["ink"], True, "right")
        if self.rest_content == "video" and s.media.status:
            cv.text(cv.fit(self.tr("videoFail"), 300, 13), W - 26, 166, 13, P["dim"], align="right")
        if self.rest_content == "nasa" and self.apod.image is None:
            note = sky_mod.Apod.TEXT[self.lang].get(self.apod.status, "")
            if note:
                cv.text(cv.fit(self.tr("picture") + ": " + note, 320, 13), W - 26, 166, 13, P["dim"], align="right")
        cv.text(self.hm(ms), 28, 64, 48, P["ink"], True)
        cv.text(self.day_name(), 30, 92, 16, P["dim"])
        ly = 112
        if he and st is not None:
            hd = sky_mod.hebrew_date(ms, st.tz, sky_mod.after_sunset(st, ms), online=self.online())
            if hd:
                cv.text(hd, 30, 112, 15, P["dim"])
                ly = 132
        stats = s.engine.overview.get("stats") or {}
        n = int(stats.get("streak", 0) or 0)
        cv.text(self.tr("streak", n=n) if n > 1 else self.tr("streak1") if n == 1 else self.tr("streak0"),
                W - 26, 46, 22 if n else 15, P["gold"], True, "right")
        cv.text(self.tr("week", n=stats.get("week_minutes", 0) or 0), W - 26, 70, 13, P["dim"], align="right")
        if st is not None:
            if st.rise and st.set:
                cv.text(f"{self.tr('rise')} {self.hm(st.rise)} · {self.tr('set')} {self.hm(st.set)}", 30, ly, 13, P["dim"])
                ly += 19
            if self.is_night():
                ny = 86
                label = self.tr("quiet")
                ix = W - 26 - cv.measure(label, 13, True) - 14
                cv.circle(ix, ny - 5, 6, P["gold"])
                bg = self.zen if self.theme == "sky" else (13, 20, 36) if self.dark else (245, 241, 232)
                cv.circle(ix + 3, ny - 8, 5.5, bg)
                cv.text(label, W - 26, ny + 4, 13, P["ink"], True, "right")
                # darker only when the night level is below the day's (100%, the default, is not)
                darker = self.light_level() < (self.st.get("brightness", 100) if self.st.get("brightness", 100) in DAY_LEVELS else 100)
                if self.night_when() == "hours":
                    sub_text = self.tr("quietSubH" if darker else "quietSubHQ", t=f"{self.night_hours()[1] // 60:02d}:{self.night_hours()[1] % 60:02d}")
                elif self.night_when() == "manual":
                    sub_text = self.tr("quietSubM" if darker else "quietSubMQ")
                else:
                    sub_text = self.tr("quietSub" if darker else "quietSubQ")
                cv.text(sub_text, W - 26, ny + 21, 12, P["dim"], align="right")
            if self.theme == "sky":
                for ev in sky_mod.events_week(st, ms)[:3]:
                    text, date = sky_mod.event_text(ev, self.lang, st.tz)
                    cv.text(cv.fit(f"{text} · {date}", 420, 13), 30, ly, 13, P["dim"])
                    ly += 18
        if part == "all":
            self.rest_moving(t)
        self.rest_bar()

    def rest_moving(self, t):
        cv, kind = self.cv, self.kind
        cy = min(SKYH - 36 if self.theme == "sky" else 290, H - BAR - 50 - 4 - 2 - LOW[kind])
        if not self.tour_on():                       # on the tour the figure stands beside its bubble (tour.py)
            self.char.draw(cv, kind, W / 2, cy, t)
            self.char.draw_owl(cv, self.lang, t)
            self.talk_draw(t)                        # its bubble, the echo game's dots, a star that rings (talk.py)
        self.song_card(t)
        self.oct_note(t)

    def song_card(self, t):
        P, cv = self.P, self.cv
        y = H - BAR - 50
        card = self.song_card_data()
        if not card and self.bar_hidden():
            # the buttons are hidden (Settings › Look and screen) and there is no song to go on with: the line that
            # only says "No song played yet" is left out too. With a song to go on with, it shows as before.
            self.song_rect = None
            return
        self.song_rect = (12, y, W - 24, 42)         # for the tour's outline
        cv.rect(12, y, W - 24, 42, P["panel"], r=12)
        if not card:
            cv.text(self.tr("noSong"), 26, y + 26, 14, P["ink"], True)
            cv.text(self.tr("noSongSub"), W - 26, y + 26, 13, P["gold"], True, "right")
            self.hit(12, y, W - 24, 42, lambda: self.go("songs"))
            return
        cv.text(self.tr("lastSong"), 26, y + 15, 10, P["dim"], True)
        # the name takes the room it needs (up to 200), and the parts start after it; a longer one ends in …
        nm = song_title(card["name"])
        name_w = min(200, max(92, cv.measure(nm, 14, True) + 2))
        xs = 34 + name_w
        cv.text(cv.fit(nm, name_w, 14, True), 26, y + 33, 14, P["ink"], True)
        states = card["states"]
        n = max(1, len(states))
        step = min(19.0, 150.0 / n)
        kw = max(3.0, step - 4)
        for i, v in enumerate(states):
            x = xs + i * step
            a = 0.45 + 0.45 * math.sin(t * 4) if v == 1 else 1.0
            col = alpha(P["gold"], a) if v else (128, 128, 128, 64)
            cv.rect(x, y + 10, kw, 22, col, r=min(3, kw / 2))
            if v == 1:
                cv.rect(x, y + 10, kw, 22, P["gold"], r=min(3, kw / 2), width=1.5)
        keep = sum(1 for v in states if v)
        fade = next((i for i, v in enumerate(states) if v == 1), None)
        right = self.tr("practice", p=fade + 1) if fade is not None else self.tr("practiceOn")
        rw = cv.measure(right, 13, True)
        cv.text(right, W - 26, y + 26, 13, P["gold"], True, "right")
        mid = self.tr("remembered", k=keep, n=len(states))
        if fade is not None:
            mid += " · " + self.tr("fadingPart", p=fade + 1)
        x0 = xs + n * step + 10
        cv.text(cv.fit(mid, W - 40 - rw - x0, 12), x0, y + 26, 12, P["dim"])
        self.hit(12, y, W - 24, 42, self.keep_practicing)

    # ------------------------------------------------------------------ the lesson
    def view_lesson(self, t):
        P, cv, s = self.P, self.cv, self.screen
        st = s.engine.state
        self.hills(0.5)
        pos, total = st.get("pos", 0) or 0, st.get("total", 0) or 0
        song = song_title(st.get("song") or "")
        # the engine's titles and lines start with an emoji for the browser; here they would be a "?"
        self.header(self.tr("lesson") + (" · " + song if song else ""), cv.drawable(st.get("title") or ""))
        drill = st.get("drill") if isinstance(st.get("drill"), dict) else None
        marked = {n - 1 for n in (drill or {}).get("notes", []) if isinstance(n, int)}
        # 16 dots at most: past 16 parts, the 16 around the current one (it was never marked from 17 on)
        first = max(0, min(pos - 8, total - 16))
        for j, i in enumerate(range(first, min(total, first + 16))):
            col = P["ok"] if i < pos else P["gold"] if i == pos else P["panel"]
            cv.rect(24 + j * 36, 66, 30, 8, col, r=4)
            if i in marked:                          # the notes of the step drilled alone
                cv.rect(22 + j * 36, 64, 34, 12, P["lit"], r=6, width=2)
        midi = st.get("midi") or []
        at = st.get("expect", -1)
        if at is None or at < 0:
            at = st.get("demo", -1)
        if at is None:
            at = -1
        mod = sys.modules.get(type(s).__module__)
        stage = mod.lesson_display_stage(st.get("show", True)) if hasattr(mod, "lesson_display_stage") else "numbers"
        label_mode = st.get("device_labels", "num")
        colors_mode = st.get("device_colors", "notes")
        labels = st.get("device_key_labels") or None
        colours = st.get("device_key_colours") or None
        next_pc, finger, target = None, "", ""
        if 0 <= at < len(midi) and stage in ("numbers", "keys"):
            next_pc = self.lit_key(midi[at])
            if stage == "numbers":
                fing = st.get("fing") or []
                m = re.search(r"\d", fing[at]) if at < len(fing) and fing[at] else None
                finger = m.group(0) if m else ""
                if labels:
                    target = key_name(labels, next_pc, label_mode)
        for i, (mode, label) in enumerate(LABEL_MODES):
            x = 218 + i * 112
            on = mode == label_mode
            shown = "בלי" if mode == "none" and self.lang == "he" else label
            # the toast says the label as the button shows it ("None" came in the middle of Hebrew)
            self.hit(x, 96, 104, 36, lambda m=mode, lb=shown: (s.engine.post("/settings", {"labels": m}),
                                                               self.say(f"Key labels: {lb}", 1.4)))
            cv.rect(x, 96, 104, 36, P["panel"], r=10)
            if on:
                cv.rect(x, 96, 104, 36, P["sel"], r=10, width=2)
            cv.text(shown, x + 52, 120, 14, P["sel"] if on else P["ink"], True, "center")
        # the keys stay put in a lesson: the corner says the octave the next note really sounds in,
        # with an arrow (and in green) when it is above or below the keys' own octave
        want = s.lesson_expect() if hasattr(s, "lesson_expect") else None
        real = want // 12 - 1 if want is not None else s.octave
        up = real - s.octave
        if next_pc == 12:
            up = 0                                   # the high C has a key of its own: no arrow
        cv.text(self.tr("octave"), W - 24, 80, 13, P["dim"], align="right")
        cv.text(("↑ " if up > 0 else "↓ " if up < 0 else "") + str(real), W - 24, 108, 24,
                P["ok"] if up else P["sel"], True, "right")
        if stage in ("numbers", "keys") and midi:
            self.contour(midi, at)
        self.round_line(st, 140)
        g = st.get("ghost")
        ghost = self.lit_key(midi[g]) if s.ghost_on() and isinstance(g, int) and 0 <= g < len(midi) else None
        rects = self.keys(172, next_pc, ghost, labels=labels if stage == "numbers" else [""] * 12, colours=colours,
                          label_mode=label_mode, colors_mode=colors_mode, stage=stage, finger=finger, flash=s.flash)
        self.touch_keys(rects)                       # the lesson is played by touch too, as free play
        if want is not None and up and stage in ("numbers", "keys"):
            self.key_arrow(rects, want % 12, up > 0, bool(finger) and stage == "numbers")
        cv.rect(24, 356, W - 48, 8, P["panel"], r=4)
        if total:
            cv.rect(24, 356, max(8, (W - 48) * min(1, pos / total)), 8, P["sel"], r=4)
        line = cv.drawable(st.get("stop_hint") or st.get("msg") or "")
        if line and labels and label_mode in ("num", "abc", "do"):
            # "Note 4 is 22": the engine names a key by the big keyboard's number; here it is the device keys' own
            def own(m):
                k = int(m.group(2)) - 1
                return m.group(0) if not 0 <= k < len(midi) else f"{m.group(1)} {m.group(2)} {m.group(3)} {key_name(labels, self.lit_key(midi[k]), label_mode)}"
            line = re.sub(r"(Note|תו) (\d+) (is|הוא) (\S+)", own, str(line))
        if line:
            cv.text(cv.fit(str(line), 560, 15, True), 24, 386, 15, P["ok"] if st.get("stop_hint") else P["ink"], True)
        warm = st.get("warmup") if isinstance(st.get("warmup"), dict) else None
        side = ""
        if drill:
            side = self.tr("drill")
        elif warm and warm.get("parts"):
            k = warm.get("at", 0) if isinstance(warm.get("at"), int) else 0
            part = warm["parts"][max(0, min(k, len(warm["parts"]) - 1))]
            side = self.tr("warmup", s=song_title(part.get("song", "")), n=int(part.get("part", 1)))
        if side:
            cv.text(cv.fit(side, 380, 13, True), W - 24, 408, 13, P["lit"], True, "right")
        if stage == "numbers" and next_pc in (1, 3, 6, 8, 10) and label_mode in ("num", "abc", "do") and target:
            cv.text(self.tr("next", k=target), W - 24, 386, 15, P["gold"], True, "right")
        if ghost is not None:
            cv.text(self.tr("ghost"), 24, 408, 13, PINK)
        self.bar([("■", self.tr("leave"), self.leave_lesson)])

    def round_line(self, st, y):
        """The rounds this part goes through, from the easiest, the one now in gold (and how far note by note
        has got), the ones done in green: a new round never comes as a surprise. Nothing for a single round."""
        P, cv = self.P, self.cv
        rounds = [r for r in (st.get("rounds") or []) if r in ("lead", "build", "ear", "memory")]
        if len(rounds) < 2:
            return
        now, done = st.get("round_now"), set(st.get("round_done") or [])
        sub = str(st.get("round_sub") or "")
        x = 24
        for r in rounds:
            text = self.tr("er.r." + r)
            if r == now and r == "build" and re.fullmatch(r"\d+/\d+", sub):
                a, b = sub.split("/")
                text += " · " + self.tr("chk.of", n=a, m=b)
            elif r in done:
                text = "✓ " + text
            w = cv.measure(text, 12, r == now) + 20
            if r == now:
                cv.rect(x, y, w, 22, P["sel"], r=11)
                cv.text(text, x + w / 2, y + 16, 12, (13, 20, 36), True, "center")
            else:
                cv.rect(x, y, w, 22, P["panel"], r=11)
                cv.text(text, x + w / 2, y + 16, 12, P["ok"] if r in done else P["dim"], False, "center")
            x += w + 6

    def contour(self, midi, at):
        """The tune of the part as a line that goes up and down: it shows that the C after a B goes up,
        though on the device's keys it is the key on the far left. The notes played are green, the next gold.
        Shown with the numbers and with the keys lit, not from memory."""
        P, cv = self.P, self.cv
        # 24 notes at most, around the next one; `at` counts in the whole list, other entries and all, so
        # each dot keeps its place in it (the gold dot went missing past 24 notes, or moved with a gap)
        idx = [i for i, n in enumerate(midi) if isinstance(n, int)]
        if len(idx) < 2:
            return
        k = next((j for j, i in enumerate(idx) if i >= at), len(idx)) if at >= 0 else 0
        first = max(0, min(k - 12, len(idx) - 24))
        idx = idx[first:first + 24]
        notes = [midi[i] for i in idx]
        x0, x1, top, bot = 30, 200, 90, 130
        lo, hi = min(notes), max(notes)
        step = (x1 - x0) / (len(notes) - 1)
        pts = [(x0 + i * step, (top + bot) / 2 if hi == lo else bot - (n - lo) / (hi - lo) * (bot - top))
               for i, n in enumerate(notes)]
        cv.polyline(pts, P["dim"], 2)
        for i, (x, y) in zip(idx, pts):
            if i == at:
                cv.circle(x, y, 6, P["gold"])
            else:
                cv.circle(x, y, 3.5, P["ok"] if 0 <= at and i < at else P["dim"])

    def key_arrow(self, rects, pc, up, beside):
        """↑ or ↓ over the key of the next note when it sounds above or below the keys' octave; beside
        the gold finger mark when there is one."""
        P, cv = self.P, self.cv
        r = next((k for k in rects if k[0] == pc), None)
        if not r:
            return
        _, x, y, w, _ = r
        with cv.physical():
            cv.text("↑" if up else "↓", x + w / 2 + (28 if beside else 0), y - 14, 22, P["ok"], True, "center")

    def view_summary(self, sm, t):
        P, cv = self.P, self.cv
        self.hills(0.4)
        x, y, w, h = 140, 110, 520, 250
        cv.rect(x, y, w, h, P["panel"], r=16)
        cv.text(cv.fit(song_title(sm.get("song", "")), w - 40, 24, True), x + w / 2, y + 48, 24, P["ink"], True, "center")
        cv.text(self.tr("learnedOf", k=sm.get("learned", 0), n=sm.get("total", 0)), x + w / 2, y + 88, 17, P["dim"], align="center")
        if sm.get("score") is not None:
            cv.text(f"{sm['score']}%", x + w / 2, y + 152, 48, P["gold"], True, "center")
        wk = sm.get("weakest")
        if isinstance(wk, dict) and wk.get("skill"):
            name = self.tr("skill." + str(wk["skill"]))
            cv.text(self.tr("weakest", s=name, p=round(100 * wk.get("level", 0))), x + w / 2, y + 196, 15, P["lit"], True, "center")
        extra = []
        if sm.get("warmup"):
            extra.append(self.tr("sumWarm1") if sm["warmup"] == 1 else self.tr("sumWarm", n=sm["warmup"]))
        if sm.get("drills"):
            extra.append(self.tr("sumDrill1") if sm["drills"] == 1 else self.tr("sumDrills", n=sm["drills"]))
        if sm.get("tired"):
            extra.append(self.tr("sumTired"))
        if extra:
            cv.text(cv.fit(" · ".join(extra), w - 40, 13), x + w / 2, y + 220, 13, P["dim"], align="center")
        cv.text(self.tr("goOn"), x + w / 2, y + h - 12, 13, P["dim"], align="center")

    # ------------------------------------------------------------------ songs and free play
    @staticmethod
    def same_song(a, b):
        """The bridge names the song playing as its file name, the list as the engine's name: the same
        words, whatever the spaces, underscores and case."""
        norm = lambda x: re.sub(r"[\W_]+", " ", str(x or "")).strip().lower()
        return bool(norm(a)) and norm(a) == norm(b)

    def song_is_playing(self, name):
        """This song plays now and is not paused: its button shows the pause sign."""
        s = self.screen
        return bool(s.song and not s.alarm and not s.paused_state() and self.same_song(s.song_name, name))

    def songs_play(self):
        """The ▶ of a song. A song that plays now: pause, or go on from the same place when paused
        (piano/pause, the bridge keeps the second). Another song while one plays: that one is stopped
        and this one starts. The alarm is never touched here."""
        s = self.screen
        songs = s.songs()
        if songs:
            name = songs[s.sel_song % len(songs)]["name"]
            if s.song and not s.alarm:
                if self.same_song(s.song_name, name):
                    was = s.paused_state()
                    s.hint_paused(not was)                     # the sign changes now, the bridge confirms
                    s.pub("piano/pause", "resume" if was else "pause")
                    self.say(self.tr("songResumed" if was else "songPaused"))
                    return
                s.pub("piano/stop", "Armonico Pi: another song")
            s.pub("piano/play", f"{name}|Armonico Pi")
            self.say(f"Playing {song_title(name)}")      # the name as the list shows it, not the file's

    def bin_icon(self, cx, cy, col):
        """A small bin, drawn: the font has no such sign."""
        cv = self.cv
        cv.rect(cx - 9, cy - 8, 18, 3, col, r=1)               # the lid
        cv.rect(cx - 3, cy - 11, 6, 3, col, r=1)               # its handle
        cv.rect(cx - 7, cy - 4, 14, 15, col, r=2)              # the body
        for dx in (-3, 0, 3):
            cv.rect(cx + dx - 0.6, cy - 1, 1.2, 9, (0, 0, 0, 90))

    def song_delete(self, name):
        """A song taken off the device with its progress (the lesson engine's /songs/delete). One that plays now is
        stopped first; a song that came with the device is not put back by an update (the engine notes it)."""
        s = self.screen
        if s.song and not s.alarm and self.same_song(s.song_name, name):
            s.pub("piano/stop", "Armonico Pi: song deleted")

        def done(ok, code):
            if ok:
                ov = s.engine.overview
                ov["songs"] = [x for x in ov.get("songs", []) if x.get("name") != name]
                n = len(s.songs())
                s.sel_song = min(s.sel_song, max(0, n - 1))
                self.toast(self.tr("delSongDone", s=song_title(name)), 3)
            else:
                self.toast(self.tr("resetBusy") if code == 409 else self.tr("failed"), 3)
            self.layer_dirty()
        s.engine.post("/songs/delete", {"name": name}, on_result=done)

    def songs_leave(self):
        """Out of the songs list: a song playing from it (or paused) is stopped, so it does not go on behind another
        screen. The alarm is never touched here."""
        s = self.screen
        if s.song and not s.alarm:
            s.pub("piano/stop", "Armonico Pi: left the songs")

    def songs_start(self):
        songs = self.screen.songs()
        if songs:
            self.screen.start_lesson(songs[self.screen.sel_song % len(songs)]["name"])

    def view_songs(self, t):
        P, cv, s = self.P, self.cv, self.screen
        self.hills(0.45)
        songs = s.songs()
        if not songs and getattr(s, "song_lv", ""):
            s.song_lv = ""                                 # a level left with no songs: all again
            songs = s.songs()
        self.header(self.tr("songs"), str(len(songs)) if songs else "")
        on_net = bool(songs) and getattr(self, "sn_entry", False)       # Search online framed (views_songnet.py)
        on_imp = bool(songs) and getattr(self, "imp_entry", False)      # Songs from USB framed
        if songs:
            self.lv_chip(self.imp_entry_chip(self.sn_entry_chip(on_net) + 10, on_imp) + 10)
            self.bar([("☰", "", None), ("▼", self.tr("down"), lambda: self.songs_step(1)),
                      ("▲", self.tr("up"), lambda: self.songs_step(-1)),
                      ("✓", self.tr("sn.find"), self.open_songnet) if on_net else
                      ("✓", self.tr("c.open"), self.open_import) if on_imp else ("✓", self.tr("start"), self.songs_start)])
        else:
            # no songs: Search online and Songs from USB; ▼▲ choose between them, ✓ opens
            imp = getattr(self, "imp_entry", False)
            flip = lambda: setattr(self, "imp_entry", not getattr(self, "imp_entry", False))
            self.bar([("☰", "", None), ("▼", self.tr("down"), flip), ("▲", self.tr("up"), flip),
                      ("✓", self.tr("c.open"), self.open_import) if imp else ("✓", self.tr("sn.find"), self.open_songnet)])
        if not songs:
            self.sn_entry_card(24, 66, W - 48, 62, not getattr(self, "imp_entry", False))
            self.imp_entry_card(24, 136, W - 48, 62, getattr(self, "imp_entry", False))
            ov = s.engine.overview
            if not s.engine.ok:
                lines = [(self.tr("noEngine"), 18, P["ink"]), (s.engine.why[:80], 13, P["dim"])]
            elif s.engine.over_why:
                lines = [(self.tr("noLoad"), 18, P["ink"]), (s.engine.over_why[:80], 13, P["dim"])]
                if "401" in s.engine.over_why:
                    lines.append((self.tr("noCode"), 14, P["gold"]))
            elif ov.get("songs"):
                why = next((x.get("problem") for x in ov["songs"] if x.get("problem")), "")
                lines = [(self.tr("noneRead", n=len(ov["songs"])), 18, P["ink"]), (why[:80] or self.tr("seeLogs"), 13, P["dim"])]
            else:
                lines = [(self.tr("noSongs"), 18, P["ink"])]
            for i, (line, size, col) in enumerate(lines):
                cv.text(cv.fit(line, W - 48, size), 24, 232 + i * 28, size, col, size >= 18)
            return
        for i, _ in s.song_rows():
            song = songs[i]
            y = 66 + (i - s.song_first) * 68
            total, learned = int(song.get("total", 0)), int(song.get("learned", 0))
            done = learned >= total > 0
            sub = self.tr("partsDone", n=total) if done else self.tr("partsSome", n=total, k=learned) if learned \
                else self.tr("partsNew", n=total)
            if song.get("faded"):
                sub += " · " + (self.tr("faded1") if song["faded"] == 1 else self.tr("faded", n=song["faded"]))
            if song.get("level") in ("beginner", "intermediate", "advanced"):
                sub += " · " + self.tr("lv." + song["level"])
            self.hit(24, y, W - 48, 58, lambda i=i: (setattr(s, "sel_song", i), s.start_lesson(songs[i]["name"])))
            # ▶ on the card plays the song as it is, to hear it before learning it (✓ held does the same)
            px = W - 326                                 # between the name and the progress bar
            self.hit(px, y + 4, 52, 50, lambda i=i: (setattr(s, "sel_song", i), setattr(self, "sn_entry", False), setattr(self, "imp_entry", False),
                                                     self.songs_play()))
            cv.rect(24, y, W - 48, 58, P["panel"], r=14)
            if i == s.sel_song % len(songs) and not on_net and not on_imp:
                cv.rect(24, y, W - 48, 58, P["sel"], r=14, width=2)
            cv.ticker(song_title(song["name"]), 44, y + 26, W - 372, 19, P["ink"], t, True)
            cv.text(self.short(sub, W - 380, 13), 44, y + 46, 13, P["dim"])
            cv.rect(px + 6, y + 11, 40, 36, (128, 128, 128, 40), r=10)
            cx = W - (px + 26) if cv.mirrored else px + 26
            with cv.physical():                          # ▶ points the same way in Hebrew: a sign, not text
                if self.song_is_playing(song["name"]):   # playing now: ‖, a touch pauses it
                    cv.rect(cx - 7, y + 20, 5, 18, P["gold"], r=1)
                    cv.rect(cx + 2, y + 20, 5, 18, P["gold"], r=1)
                else:
                    cv.polygon([(cx - 6, y + 20), (cx - 6, y + 38), (cx + 9, y + 29)], P["dim"])
            cv.rect(W - 272, y + 25, 110, 8, (128, 128, 128, 64), r=4)
            if learned and total:
                cv.rect(W - 272, y + 25, max(8, 110 * min(1, learned / total)), 8, P["ok"], r=4)
            if learned or song.get("last"):
                # this song from the start: a touch asks first (the row itself starts the lesson)
                bx = W - 24 - 124
                self.hit(bx, y + 6, 56, 46, lambda n=song["name"]: self.ask(
                    self.tr("resetSongQ", s=song_title(n)), lambda: self.reset_song(n)))
                cv.rect(bx + 8, y + 11, 40, 36, (128, 128, 128, 40), r=10)
                cx, cy = bx + 28, y + 29                 # a circular arrow, drawn: the font has no such sign
                cv.arc(cx, cy, 9, 0.6, 2 * math.pi - 0.2, P["dim"], 2.5)
                ax, ay = cx + 9 * math.cos(0.6), cy + 9 * math.sin(0.6)
                cv.polygon([(ax - 5, ay - 1), (ax + 4, ay - 3), (ax + 1, ay + 6)], P["dim"])
            # the song deleted from the device: a touch asks first (song_delete)
            dx = W - 24 - 62
            self.hit(dx, y + 6, 56, 46, lambda n=song["name"]: self.ask(
                self.tr("delSongQ", s=song_title(n)), lambda: self.song_delete(n)))
            cv.rect(dx + 8, y + 11, 40, 36, (128, 128, 128, 40), r=10)
            self.bin_icon(dx + 28, y + 29, P["dim"])
        bad = [x for x in s.engine.overview.get("songs", []) if not x.get("total")]
        if bad:
            first = f'{song_title(bad[0]["name"])}: {bad[0].get("problem") or "?"}'
            more = f" (+{len(bad) - 1})" if len(bad) > 1 else ""
            cv.text(cv.fit(self.tr("notLesson", x=first) + more, W - 48, 12), 24, 414, 12, P["dim"])

    TOUCH_KEY_MAX = 20          # seconds: a key whose finger was never seen lifting is let go then

    def touch_keys(self, rects):
        """The keys drawn on this frame take touches, and a finger sliding over them plays each key
        it reaches (key_at, pointer_move), as a hand run along a piano."""
        self.key_rects = rects
        for kpc, x, y, w, h in reversed(rects):
            self.hits.append((x, y, w, h, lambda p=kpc: self.free_key(p)))

    def key_at(self, x, y):
        for kpc, kx, ky, kw, kh in getattr(self, "key_rects", ()) or ():   # the black keys come first
            if kx <= x <= kx + kw and ky <= y <= ky + kh:
                return kpc
        return None

    def pointer_move(self, px, py):
        """A finger (or the mouse button) held down and moving: on a new key, the key before lets go and
        the new one sounds. Only for a press that began on a key, so a swipe elsewhere plays nothing."""
        who = getattr(self, "pointer_id", "mouse")
        if who not in getattr(self, "_sliding", set()) or (self.view not in ("free", "game") and not self.screen.lesson_on()):
            return
        x, y = self.cv.to_plan(px, py)
        pc = self.key_at(x, y)
        cur = getattr(self, "_touch_held", {}).get(who)
        if pc is None:
            self.release_touch_key(who)              # slid off the keys: silent until it reaches one again
        elif cur is None or getattr(self, "_touch_key", {}).get(who) != pc:
            self.free_key(pc)

    def touch_off(self):
        return self.play_input() == "off"

    def free_key(self, pc):
        """A touch on a key plays it on the device's own sound at once, like a real key (armonico_pi.py,
        piano/device/note), and the lesson engine hears it as one. The note sounds while the finger (or
        the mouse button) stays down and is let go when it lifts, as on a piano: before, every touch was
        let go after a fixed 0.35 s, so a held key stopped after about half a second, pedal or not."""
        if self.touch_off():
            if time.time() - getattr(self, "_touch_off_said", 0) > 4:
                self._touch_off_said = time.time()
                self.toast(self.tr("touchOff"), 3)
            return
        note = 12 * (self.screen.octave + 1) + pc
        who = getattr(self, "pointer_id", "mouse")
        held = getattr(self, "_touch_held", {})
        self._touch_held = held
        if who in held:
            self.release_touch_key(who)
        gen = getattr(self, "_touch_gen", {})
        self._touch_gen = gen
        gen[note] = gen.get(note, 0) + 1
        mine = gen[note]
        held[who] = (note, mine)
        self._touch_key = {**getattr(self, "_touch_key", {}), who: pc}     # the key under the finger, whatever the octave
        self._sliding = getattr(self, "_sliding", set()) | {who}   # until it lifts: a slide plays on (pointer_move)
        self.screen.pub("piano/device/note", f"on {note}")
        # the lift never came (a lost event): let go by follow_touch_keys on the loop. A timer thread
        # for each key made hundreds of threads in a glissando, and quitting waited for them
        due = getattr(self, "_touch_due", {})
        self._touch_due = due
        due[who] = (time.monotonic() + self.TOUCH_KEY_MAX, note, mine)
        self.screen.flash[pc] = time.time()
        if self.view == "game":
            self.game_touched(pc)                    # judged as it lands (views_game.py)
        # the lesson engine hears this press too, and lists it a moment later with the keys pressed:
        # that copy is the same press (follow_keys takes it off), not a second note
        sent = getattr(self.screen, "touch_sent", None)
        if sent is None:
            sent = self.screen.touch_sent = []
        sent[:] = [x for x in sent if time.time() - x[1] < 2] + [(pc % 12, time.time())]   # no copy came: dropped

    def follow_touch_keys(self):
        """A touched key still held TOUCH_KEY_MAX seconds after its press is let go (from tick)."""
        due = getattr(self, "_touch_due", None)
        if not due:
            return
        now, held = time.monotonic(), getattr(self, "_touch_held", {})
        for who, (at, note, mine) in list(due.items()):
            if held.get(who) != (note, mine):
                due.pop(who, None)                   # lifted, or another press of this finger since
            elif now >= at:
                due.pop(who, None)
                self.release_touch_key(who)

    def release_touch_key(self, who):
        held = getattr(self, "_touch_held", {})
        got = held.pop(who, None)
        if got is None:
            return
        note, mine = got
        if getattr(self, "_touch_gen", {}).get(note) == mine:   # not struck again by another finger meanwhile
            self.screen.pub("piano/device/note", f"off {note}")

    def view_free(self, t):
        P, cv, s = self.P, self.cv, self.screen
        self.hills(0.45)
        self.header(self.tr("free"), self.tr("pedalOn") if s.pedal else self.tr("pedalOff"))
        cv.text(self.tr("octave"), W - 24, 80, 13, P["dim"], align="right")
        cv.text(str(s.octave), W - 24, 108, 24, P["sel"], True, "right")
        # the notes played sit in the middle, cut from the start, so they never reach the recording's
        # buttons on one side or the octave's corner on the other
        self.draw_rec_buttons(t)
        if self.free_notes:
            with cv.physical():
                cv.text(cv.fit(self.free_line(), 340, 20, True), W / 2, 104, 20, P["ink"], True, "center")
        else:
            cv.text(self.short(self.tr("playSomething"), 340, 20, True), W / 2, 104, 20, P["ink"], True, "center")
        pc, at = self.free_lit
        rects = self.keys(150, pc if t - at < 0.35 else None, flash=s.flash)
        self.touch_keys(rects)
        self.bar([("☰", self.tr("menu"), None), ("▼", self.tr("oct"), lambda: self.octave_move(-1)),
                  ("▲", self.tr("oct"), lambda: self.octave_move(1)), ("✓", self.tr("songs"), lambda: self.go("songs"))])

    # ------------------------------------------------------------------ hardware test
    def open_test(self):
        self.screen.test = {"keys": set(), "buttons": {}, "taps": [], "speaker": "",
                            "usb": {"name": "", "note": None, "n": 0}}
        self.screen.view = "test"
        self.go("test")

    def tone(self, side):
        s = self.screen
        if not self.mixer_ready():
            self.toast(self.tr("noSound"), 4)
            return
        s.test_tone(side, quiet=True)
        self.toast(self.tr("toneSide", s=self.tr("left" if side == "left" else "right")), 2)

    def usb_test_line(self, tst):
        """A USB keyboard has a test of its own: the keys' test does not hear it, and in the test it
        plays nothing (armonico_pi.py only says what was played)."""
        u = tst.get("usb") or {}
        if u.get("n"):
            n = u["note"]
            return self.tr("usbOf", name=u.get("name") or self.tr("c.kbUsb"),
                           note=f"{NOTE_NAMES[n % 12]}{n // 12 - 1}", n=u["n"])
        return self.tr("usbPlay") if getattr(self, "keyboards", None) else self.tr("usbNone")

    def view_test(self, t):
        P, cv, s = self.P, self.cv, self.screen
        mod = sys.modules.get(type(s).__module__)
        tst = s.test
        # the texts clear of the corner rings, which sat on them (ring 30 px in, 18 px round)
        self.header(self.tr("test"), self.tr("testHint"), inset=max(0, 56 - int(min(22 - cv.vx, 22 + cv.vx + cv.vw - W))), temp=True)
        now = time.time()
        key_pins = getattr(mod, "KEY_PINS", [])
        btn_pins = getattr(mod, "BUTTON_PINS", {})
        with cv.physical():
            n_keys = self.key_total()
            kp = 58                                   # 13 keys: C to C, the last the high C
            for pc in range(n_keys):
                x, y = 24 + pc * kp, 62
                live = now - max(s.flash.get(pc, 0), tst.get("at", {}).get(pc, 0)) < 0.25
                done = pc in tst["keys"]
                cv.rect(x, y, kp - 5, 62, P["ok"] if live else P["gold"] if done else P["panel"], r=8)
                col = (13, 20, 36) if live or done else P["ink"]
                cv.text(NOTE_NAMES[pc % 12], x + (kp - 5) / 2, y + 34 if pc >= 12 else y + 26, 17, col, True, "center")
                if pc >= 12:                         # the high C: a small word over its name
                    zoom = cv.zoom                   # the small word keeps its size: the box does not grow
                    cv.zoom = 1.0
                    try:
                        cv.text(cv.fit(self.tr("chk.high"), kp - 9, 9), x + (kp - 5) / 2, y + 13, 9, col, align="center")
                    finally:
                        cv.zoom = zoom
                pin = f"GPIO {key_pins[pc] if pc < len(key_pins) else '?'}"
                cv.text(pin if cv.measure("GPIO 27", 11) <= kp - 7 else pin[5:], x + (kp - 5) / 2, y + 52, 11, col, align="center")   # "GPIO 17", or "17" when it does not fit
            for i, (name, sym) in enumerate((("menu", "☰"), ("down", "▼"), ("up", "▲"), ("ok", "✓"))):
                x, y = 24 + i * 190, 134                 # the 4 buttons across the row
                n = tst["buttons"].get(name, 0)
                cv.rect(x, y, 182, 62, P["gold"] if n else P["panel"], r=8)
                col = (13, 20, 36) if n else P["ink"]
                cv.text(sym, x + 91, y + 28, 20, col, True, "center")
                lab = f"GPIO {btn_pins.get(name, '?')} · {n}"
                cv.text(lab if cv.measure(lab, 12) <= 172 else lab[5:], x + 91, y + 52, 12, col, align="center")
        corners = tst.setdefault("corners", set())
        cv.rect(24, 206, 276, 200, P["panel"], r=10)
        for i, line in enumerate(cv.wrap(self.tr("touchHere", n=len(corners)), 240, 15)):
            cv.text(line, 36, 236 + i * 22, 15, P["ok"] if len(corners) == 4 else P["ink"], True)

        cv.rect(312, 206, 464, 200, P["panel"], r=10)
        keys_ok, btn_ok = len(tst["keys"]) == n_keys, len(tst["buttons"]) >= 4
        if keys_ok and btn_ok and not tst.get("kept"):
            tst["kept"] = True                       # the diagnosis counts the wiring as checked from now on
            self.save(hw_test={"at": time.time()})
        spk = tst["speaker"]
        lines = [(self.tr("keysOf", n=len(tst["keys"]), t=n_keys), P["ok"] if keys_ok else P["ink"]),
                 (self.tr("btnsOf", n=len(tst["buttons"])), P["ok"] if btn_ok else P["ink"]),
                 (self.usb_test_line(tst), P["ok"] if tst.get("usb", {}).get("n") else P["ink"]),
                 (self.tr("speakers", s=self.tr("played", s=self.tr("left" if spk == "left" else "right")) if spk
                          else self.tr("speakersHint")), P["ink"]),
                 (self.tr("netIs", ip=self.ip or self.tr("c.noNet")), P["ink"]),
                 (self.tr("powerHint"), P["dim"]), (self.tr("frameHint"), P["dim"])]
        parts = [(part, col) for line, col in lines for part in cv.wrap(line, 440, 13)]
        # the lines share the panel: the pitch shrinks when there are many (a larger interface size wraps more)
        pitch = min(22 * max(1.0, min(cv.zoom, 1.2)), (404 - 230) / max(1, len(parts)))
        yy = 230
        for part, col in parts:
            cv.text(part, 324, yy, 13, col)
            yy += pitch
        with cv.physical():
            cv.rect(cv.vx + 1, cv.vy + 1, cv.vw - 2, cv.vh - 2, P["gold"], width=2)
        self.bar([("◀", self.tr("left"), lambda: self.tone("left")), ("▶", self.tr("right"), lambda: self.tone("right")),
                  ("■", self.tr("done"), lambda: self.go("settings"))], inset=RING_INSET)
        with cv.physical():                          # the corner rings over everything, the button bar too
            for i, (tx, ty) in enumerate(self.touch_targets()):
                on = i in corners
                cv.circle(tx, ty, 18, P["ok"] if on else P["gold"], width=0 if on else 4)

    # ------------------------------------------------------------------ the network screen
    def open_net(self, back):
        self.net_from = back
        self.go("net")

    def view_net(self, t):
        P, cv = self.P, self.cv
        self.hills(0.35)
        self.header(self.tr("network"), self.tr("connectedIp", ip=self.ip) if self.online() else self.tr("notConnected"))
        lists = NET_LISTS if os.environ.get("MQTT_LOCAL", "no") == "yes" else NET_LISTS_EXTERNAL
        for c, (title, items) in enumerate(lists[self.lang]):
            x, y, w, h = 24 + c * 382, 64, 370, 344
            col = RED if c else P["ok"]
            cv.rect(x, y, w, h, P["panel"], r=14)
            cv.text(("✕ " if c else "✓ ") + title, x + 16, y + 28, 15, col, True)
            # the line pitch grows with the interface size so wrapped lines never touch, and shrinks back when the
            # list would run past its panel
            wrapped = [cv.wrap(it, w - 44, 13) for it in items]
            n_lines = sum(len(ls) for ls in wrapped)
            pitch = min(18 * max(1.0, cv.zoom), (h - 56 - 8 * len(items)) / max(1, n_lines))
            ly = y + 56
            for lines in wrapped:
                cv.circle(x + 18, ly - 5, 3, col)
                for line in lines:
                    cv.text(line, x + 28, ly, 13, P["ink"])
                    ly += pitch
                ly += 8
        # two fixed lists, nothing to move through: ▼▲ carry no label (they said Down and Up and did nothing)
        self.bar([("☰", "", lambda: self.go(self.net_from)), ("▼", "", None), ("▲", "", None),
                  ("✓", self.tr("back"), lambda: self.go(self.net_from))])

    # ------------------------------------------------------------------ two displays
    def pick_display(self, i):
        con = getattr(self, "found_displays", [])
        names = sorted(d["name"] for d in con)
        if not con:
            self.go("rest")
            return
        # the third choice, after the two screens: both, the same picture on each
        name = "both" if i >= min(2, len(con)) and len(con) > 1 else con[min(i, len(con) - 1)]["name"]
        before = (self.st.get("display") or {}).get("pick")
        self.save(display={"set": names, "pick": name})
        back = getattr(self, "display_from", "rest")
        self.display_from = "rest"
        open_on = getattr(self.screen, "open_on_display", None)
        if open_on and name != before:
            open_on(name)
            self.resize()
        self.go(back)

    def view_displays(self, t):
        P, cv = self.P, self.cv
        self.hills(0.35)
        self.header(self.tr("displays"))
        cv.text(self.tr("dsp.q"), 40, 104, 20, P["ink"], True)
        con = getattr(self, "found_displays", [])[:2]
        sel = getattr(self, "display_sel", 0)
        opts = con + ([{"name": "both"}] if len(con) == 2 else [])
        for i, d in enumerate(opts):
            x, y, w = 24 + i * 256, 140, 240
            self.hit(x, y, w, 170, lambda i=i: self.pick_display(i))
            cv.rect(x, y, w, 170, P["panel"], r=16)
            if i == sel:
                cv.rect(x, y, w, 170, P["sel"], r=16, width=2)
            if d["name"] == "both":
                cv.rect(x + 24, y + 38, 60, 36, P["dim"], r=5, width=3)
                cv.rect(x + 96, y + 30, 84, 50, P["dim"], r=5, width=3)
                cv.text(self.tr("dsp.both"), x + 24, y + 112, 17, P["ink"], True)
                sub, ss = self.fit_text(self.tr("dsp.bothD"), w - 48, 13)   # inside its own card
                cv.text(sub, x + 24, y + 140, ss, P["dim"])
                continue
            mode = d.get("mode") or (0, 0)
            own = d["name"].startswith("DSI")
            cv.rect(x + 24, y + 30, 60 if own else 84, 36 if own else 50, P["dim"], r=5, width=3)
            cv.text(d["name"], x + 24, y + 112, 17, P["ink"], True)
            sub, ss = self.fit_text(f"{mode[0]}×{mode[1]} · " + (self.tr("dsp.own") if own else self.tr("dsp.ext")), w - 48, 13)
            cv.text(sub, x + 24, y + 140, ss, P["dim"])
        cv.text(self.tr("dsp.keep"), 40, 352, 14, P["dim"])

        def move(d):
            self.display_sel = (sel + d) % max(1, len(opts))
        back = getattr(self, "display_from", "rest")
        self.bar([("☰", self.tr("back") if back != "rest" else "", lambda: self.go(back)), ("▼", self.tr("down"), lambda: move(1)),
                  ("▲", self.tr("up"), lambda: move(-1)), ("✓", self.tr("pick"), lambda: self.pick_display(sel))])

    def open_displays(self, back="settings"):
        """The chooser, from the settings: only while 2 screens are connected."""
        con = self.connected_displays()
        if len(con) < 2:
            self.toast(self.tr("dsp.one"))
            return
        self.found_displays, self.display_from = con, back
        pick = (self.st.get("display") or {}).get("pick")
        self.display_sel = 2 if pick == "both" else next((i for i, d in enumerate(con[:2]) if d["name"] == pick), 0)
        self.go("displays")

    def connected_displays(self):
        """The connected screens, read again every 3 seconds at most (the settings draw every frame)."""
        at, con = getattr(self, "_dsp_seen", (0.0, []))
        if time.monotonic() - at > 3:
            try:
                con = [d for d in diag.displays() if d.get("connected")] if diag else []
            except Exception:
                con = []
            self._dsp_seen = (time.monotonic(), con)
        return con


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
