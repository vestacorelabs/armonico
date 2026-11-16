"""views_game.py - the game on the device: the notes of a song fall onto the keys, in the song's own time.

The song runs at its tempo and does not wait: each note falls down the lane over its key and has to be
played as it touches the top of its key. A note played close to that moment says how close
(perfect, good or almost) and, when not perfect, whether it was early or late, so the player knows which
way to correct. A note that passes its key unplayed is a miss, and so is a wrong key: each costs one of
5 lives, and 10 in a row win one back. No lives left: out, and any key starts again. A song played to its
end can be played again 10% faster.

The game plays on the rest screen: the time and the date stay where they are, the figure stands smaller
in the middle on a keyboard as wide as the screen, and the notes fall over the sky onto its keys. The song
and the tempo are in the corner: ‹ › change the song (the engine's pick first: a started song that is
fading, or the one remembered least, or the easiest: /api/game, game_song in piano_game.py, then all the
others), − + and ▼ ▲ the tempo (60% to 120%). Any key or ✓ plays the song shown; while it plays, ✓ stops it.
The keys are the device's own keys, on the screen and on the device, and the keys of a keyboard on USB.
A key is judged at the moment it was pressed, not at the frame that sees it: pianoDevice/notes carries the time
it came (screen.py), and a touch is judged as it lands (game_touched). Before, the presses came through the
engine's /state, asked every 0.1 s, so every key was late by up to a fifth of a second and the
judgement said late or early when the key was on time.
The sound is the lesson's: the keys stay in the octave every song is fitted to, and the key of the note coming
sounds at the song's own height (game_want, screen.follow_game, pianoDevice/device/expect). Before, the keys played
in whatever octave free play had left them, so the song was heard at the wrong height.
A song whose file has a band is played with it, in the game's time, so the ear has the beat and not only the
eye (game_band_sync). A song with the tune alone plays as before.
"""
import threading
import time

from .canvas import W
from .widgets import alpha

from .widgets import BAR

# The keyboard, as wide as the screen. A little taller than it was (98 and 60), toward the lesson's keys (172 and
# 104, widgets.keyboard) but not as tall, so the notes still have the sky to fall in
KW, KH, GAP = 92, 124, 4
BW, BH = 54, 76
KEYS_Y = 480 - BAR - 8 - KH      # just over the bar
TOP = 140                        # the notes come into view under the date
LAND = KEYS_Y                    # a note is due as it touches the top of its key
SPEED = (LAND - TOP) / 1.5       # plan units a second: each note is in view for a second and a half
PERFECT, GOOD, ALMOST = 0.09, 0.18, 0.3      # seconds from the key's edge, early or late
# A press is always seen a little after the hand made it, and the picture a little after it was drawn: a key of
# the device is held back 20 ms against chatter (BOUNCE, armonico_pi.py) and comes through the broker, a touch waits
# for the next frame, and a frame drawn is on the screen about a frame later (30 a second). Together about 60 ms,
# an estimate and not a measurement: without it a key played exactly on its note was told "late" every time.
SEEN_LATE = 0.06
# What the hand adds on top, learned from the player: a Bluetooth speaker plays about 0.2 s late, and a player who
# follows the sound then presses every key late by as much, and was told "late" or "missed" again and again. After
# each song the median of how far the keys came from their notes moves this halfway there (game_learn), kept in
# the settings ("game_latency"), within LATENCY_MAX. A player who is sometimes early and sometimes late moves it
# by nothing: only a steady lead or lag is taken away.
LATENCY_MAX = 0.3
LEARN_MIN = 8                    # hits in a song before it teaches anything
LIVES, BACK_AFTER = 5, 10
TEMPOS = (0.6, 0.7, 0.8, 0.85, 0.9, 1.0, 1.1, 1.2)
COUNT_BEATS = 4
QUICKEST = 0.2                   # seconds: the least time between two notes at the tempo a song starts at
FIG_S = 0.66                     # the figure's size in the game, standing on the keys
TOUCH_COPY = 1.5                 # a touched key comes back on pianoDevice/notes within this: the same press
WHITES = (0, 2, 4, 5, 7, 9, 11, 12)
BLACKS = ((1, 0), (3, 1), (6, 3), (8, 4), (10, 5))
LABEL = {0: "1", 1: "1#", 2: "2", 3: "2#", 4: "3", 5: "4", 6: "4#", 7: "5", 8: "5#", 9: "6", 10: "6#", 11: "7", 12: "8"}   # as the keys under them are numbered
WRONG = (217, 72, 95)
SKY = (143, 205, 246)
GREEN = (122, 194, 154)
GOLD = (232, 182, 79)
X0 = (W - 8 * KW - 7 * GAP) / 2


def lane(pc):
    """(x, width, black) of the key a note falls to."""
    if pc in WHITES:
        return X0 + WHITES.index(pc) * (KW + GAP), KW, False
    a = dict(BLACKS)[pc]
    return X0 + (a + 1) * (KW + GAP) - GAP / 2 - BW / 2, BW, True


class GameViews:
    def init_game_state(self):
        self.game = None             # the song in play, see game_start
        self.game_song = None        # what /api/game answered, or {"ok": False}
        self.game_loading = False
        self.game_tempo = 0.85       # the tempo for this sitting: ▼ ▲, and 10% up after a song played through
        self.game_tempo_song = None  # the song the tempo was set for: another song starts at its own (game_start)
        self.game_pick = {"sel": 0, "first": 0}      # the song list before a song
        self.game_go = False         # a song was picked: it starts as soon as its notes arrive
        self.game_ready = None       # the picked song's notes, from the engine
        self.game_touch = []         # keys touched on the screen: (key, time), their pianoDevice/notes copy is dropped
        self.game_runs = 0           # songs started in this sitting: each run of the band has its number
        self.game_band_run = None    # the run the engine's band plays for now (game_band_sync)

    # ---- the song
    def open_game(self):
        self.go("game")
        self.game_load()

    def game_load(self, other=False, song=""):
        """The song is asked off the loop: the engine may need a moment to read the file. other: "Other
        song", or a key after the end: the song on screen is passed over when the rules allow another."""
        if self.game_loading:
            return
        skip = (self.game_song or {}).get("name", "") if other else ""
        self.game, self.game_loading = None, True
        if not song:
            self.game_song = None

        def go():
            from urllib.parse import quote
            q = f"?song={quote(song)}" if song else f"?skip={quote(skip)}" if skip else ""
            try:
                got = self.screen.engine._req("/api/game" + q, timeout=8)
            except Exception:
                got = {"ok": False}
            got = got if isinstance(got, dict) else {"ok": False}
            if song:
                self.game_ready = got                 # the picked song's notes: game_tick starts it
            else:
                self.game_song = got                  # the engine's pick, first on the list
            self.game_loading = False
        threading.Thread(target=go, daemon=True).start()

    def game_list(self):
        """The songs to pick from: the engine's pick first, then the rest in the Songs order."""
        rec = (self.game_song or {}).get("name") if (self.game_song or {}).get("ok") else None
        names = [s["name"] for s in self.screen.songs()]
        return ([rec] if rec else []) + [n for n in names if n != rec]

    def game_pick_move(self, d):
        gp, n = self.game_pick, len(self.game_list())
        if not n:
            return
        gp["sel"] = (gp["sel"] + d) % n
        self.game = None                          # ‹ › while a song plays: the other song waits for a key

    def game_play(self, i=None):
        """✓ on the list (or any key): the marked song is asked for and starts when it arrives."""
        names = self.game_list()
        if i is not None:
            self.game_pick["sel"] = i
        if not names or self.game_loading:
            return
        name = names[min(self.game_pick["sel"], len(names) - 1)]
        self.game_go = True
        self.game_ready = None
        self.game_load(song=name)

    def game_back(self):
        """✓ while a song plays or after it: stopped, the song just played still shown."""
        name = (self.game or {}).get("name")
        self.game = None
        names = self.game_list()
        if name in names:
            self.game_pick["sel"] = names.index(name)

    def game_start(self, t, faster=False, song=None):
        song = song or self.game_ready or self.game_song or {}
        notes = [(int(n), float(s)) for n, s in song.get("notes") or []]
        if not notes:
            return
        if faster:
            self.game_tempo = min(TEMPOS[-1], round(self.game_tempo + 0.1, 2))
        elif song.get("name") != self.game_tempo_song:
            # a song the player did not set a tempo for starts at the fastest tempo, up to its own, at which its
            # quickest notes can still be played one by one. Its time is the file's (game_song): the notes and the
            # band slow down together, and the rhythm stays the song's
            # the quick notes that make up the song, not its one ornament: the shortest tenth of its steps (by the
            # single shortest, a grace note or two held most songs at 60%)
            gaps = sorted(s for _, s in notes[:-1] if s > 0)
            quick = gaps[len(gaps) // 10] if gaps else 1.0
            fits = [x for x in TEMPOS if x <= 1.0 and quick / x >= QUICKEST]
            self.game_tempo = max(fits) if fits else TEMPOS[0]
            self.game_tempo_song = song.get("name")
        tempo = self.game_tempo
        steps = sorted(s for _, s in notes)
        beat = max(0.3, min(1.0, steps[len(steps) // 2])) / tempo
        base = min(n for n, _ in notes) // 12 * 12          # the song's lowest C is key 1
        at, obs = COUNT_BEATS * beat + 0.6, []
        for n, step in notes:
            k = n - base
            obs.append({"key": k if 0 <= k <= 12 else k % 12, "at": at, "note": n})
            at += step / tempo
        self.game = {"obs": obs, "beat": beat, "i": 0, "clock": 0.0, "last": t, "n": 0, "acc": 0.0,
                     "combo": 0, "best": 0, "lives": LIVES, "life_at": -9.0, "done": None, "out": False,
                     "words": [], "wrong_key": None, "off": 0.0, "hits": 0,
                     "t0": t, "run": self.game_run_new(), "zero": time.monotonic() - (self.t() - t), "name": song.get("name", ""), "why": song.get("why", ""), "song": song}

    def game_set_tempo(self, d):
        """▼ ▲: the tempo one step down or up; a song in play starts again at it."""
        i = min(range(len(TEMPOS)), key=lambda k: abs(TEMPOS[k] - self.game_tempo))
        self.game_tempo = TEMPOS[max(0, min(len(TEMPOS) - 1, i + d))]
        self.game_tempo_song = (self.game or self.game_ready or self.game_song or {}).get("name")   # kept for this song
        if self.game is not None:
            self.game = None                          # the next key starts at the new tempo

    # ---- the keys
    def game_key(self, pc, t):
        g = self.game
        if g is None:
            self.game_play()                          # on the list: any key plays the song marked
            return
        if g["done"] is not None:
            if t - g["done"] > 1.0:
                self.game_start(t, faster=not g["out"], song=g["song"])   # again: out as it was, played through 10% faster
            return
        o = g["obs"][g["i"]] if g["i"] < len(g["obs"]) else None
        if o is None:
            return
        # the song's clock when the key went down: t is the press's own time, a little before this frame
        now = g["clock"] + (t - g["last"]) - SEEN_LATE - self.game_latency()
        d = now - o["at"]                             # minus: early, plus: late
        # In a quick run two or three notes are due within the window at once. The key is matched to the note
        # of its own key that is nearest in time among them, and the ones before it that are already past their
        # time count as missed. Before, only the next note was looked at: a key played on time for the note
        # after it was a wrong key, and the note it was meant for came as a miss a moment later.
        near = []
        for j in range(g["i"], min(len(g["obs"]), g["i"] + 4)):
            dj = now - g["obs"][j]["at"]
            if dj < -ALMOST:
                break
            if abs(dj) <= ALMOST and pc % 12 == g["obs"][j]["key"] % 12:
                near.append((abs(dj), j, dj))
        if near:
            _, j, dj = min(near)
            while g["i"] < j and g["done"] is None:
                past = g["obs"][g["i"]]
                g["i"] += 1
                self.game_lose(past["key"], "g.miss", t)
            if g["done"] is None:
                self.game_hit(g["obs"][j], dj, t)
            return
        if pc % 12 == o["key"] % 12 and d < 0:
            return                                    # far too soon for this one: let be
        # the key of a note just counted as missed, played a little after it: the player is behind the notes (a
        # Bluetooth speaker, say). Not a wrong key on top of the miss, and it teaches the lag (game_learn)
        for mk, mat in reversed(g.get("missed", [])[-3:]):
            if mk == pc % 12 and 0 < now - mat <= 0.8:
                g["missed"].remove((mk, mat))
                g.setdefault("offs", []).append(now - mat)
                return
        if t - g.get("hit_at", -9.0) < 0.12 and pc % 12 == g.get("hit_key"):
            return                                    # the same key again at once: a bounce of the key just played
        g["wrong_key"] = (pc, t)
        self.game_lose(pc, "g.wrongKey", t)

    def game_note(self, payload):
        """A key from pianoDevice/notes (\"<note> <monotonic time>\", stamped as it came: screen.py), judged at that
        time. The copy of a key touched on the screen is dropped: the touch was judged when it landed."""
        parts = payload.split()
        try:
            note = int(parts[0])
            at = float(parts[1]) - self.t0 if len(parts) > 1 else self.t()
        except (ValueError, IndexError, AttributeError):
            return
        now = self.t()
        at = min(at, now)
        self.game_touch = [x for x in self.game_touch if now - x[1] < TOUCH_COPY]
        mine = next((x for x in self.game_touch if x[0] == note % 12), None)
        if mine is not None:
            self.game_touch.remove(mine)
            return
        self.game_key(self.screen.flash_key(note), at)

    def game_touched(self, pc):
        """A key touched on the screen: judged now (free_key, views_main.py)."""
        now = self.t()
        self.game_touch = [x for x in self.game_touch if now - x[1] < TOUCH_COPY] + [(pc % 12, now)]
        self.game_key(pc, now)

    def game_hit(self, o, d, t):
        g = self.game
        a = abs(d)
        if a <= PERFECT:
            key, col, worth, side = "g.perfect", GOLD, 1.0, ""
            self.char.set_mood("happy", t, 0.9)
        elif a <= GOOD:
            key, col, worth, side = "g.good", GREEN, 0.8, "g.early" if d < 0 else "g.late"
        else:
            key, col, worth, side = "g.almost", SKY, 0.5, "g.early" if d < 0 else "g.late"
        self.game_word(o["key"], self.tr(key), col, self.tr(side) if side else "", t)
        g["hit_at"], g["hit_key"] = t, o["key"] % 12
        g["acc"] += worth
        g["off"] += d
        g.setdefault("offs", []).append(d)
        g["hits"] += 1
        g["n"] += 1
        g["i"] += 1
        g["combo"] += 1
        g["best"] = max(g["best"], g["combo"])
        if g["combo"] in (5, 10, 20, 30, 40):
            self.game_girl("happy")
        if g["combo"] % BACK_AFTER == 0 and g["lives"] < LIVES:
            g["lives"] += 1
            g["life_at"] = t
        if g["combo"] in (5, 10, 20):
            self.char.set_mood("proud", t, 2.0)
        if g["i"] >= len(g["obs"]):
            g["done"] = t
            self.game_girl("proud")

    def game_girl(self, what):
        """The anime theme's girl reacts in the game as in a lesson, in the left corner: a laugh at a streak, the
        small nod at a mistake, joy with her hands together when the song is played through. Silent while the
        song plays (her motifs would cover the keys); her joy at the end has its motif. Not over a reaction that
        is still on, so a run of misses does not keep starting her nod again."""
        if getattr(self, "theme", None) != "anime":
            return
        a = self.anime
        busy = a.cutin is not None and time.monotonic() < a.cutin["until"]
        if what == "proud":
            a.react_surprise()
        elif not busy:
            a.react_happy(quiet=True) if what == "happy" else a.react_oops()

    def game_lose(self, key, word, t):
        g = self.game
        g["combo"] = 0
        g["lives"] -= 1
        g["life_at"] = t
        g["n"] += 1
        self.game_word(key, self.tr(word), WRONG, "", t)
        self.char.set_mood("offended", t, 1.2)
        self.game_girl("oops")
        if g["lives"] <= 0:
            g["done"], g["out"] = t, True

    def game_word(self, key, text, col, side, t):
        """The word over a key: one at a time on each key, so they never pile up."""
        self.game["words"] = [w for w in self.game["words"] if t - w[4] < 0.8 and w[0] != key][-6:] + [(key, text, col, side, t)]

    def game_accuracy(self):
        g = self.game
        return round(g["acc"] / g["n"] * 100) if g["n"] else 100

    def game_want(self):
        """The note of the song coming next while a song plays, at the song's own height, or None: the keys'
        program sounds its key there, as in a lesson (screen.follow_game)."""
        g = self.game
        if g is None or g["done"] is not None or g["i"] >= len(g["obs"]):
            return None
        return g["obs"][g["i"]].get("note")

    def game_run_new(self):
        self.game_runs += 1
        return f"{self.game_runs}-{time.monotonic():.3f}"

    def game_band_sync(self):
        """The song's own band behind the game, when its file has one (the engine's GameBand, piano_game.py): it
        starts with a song and stops when the song is stopped, changed or lost, or the game is left. A song
        played to its end lets the band ring out. Looked at every frame (app.py); the engine is asked off the
        loop, and only when the run changes. "zero" is time.monotonic() at the song's clock 0: the engine runs
        on this machine, so it reads the same clock."""
        g = self.game if self.view == "game" else None
        if g is not None and g["done"] is not None:
            if not g["out"] and self.game_band_run == g["run"]:
                return                                # played through: the last notes ring
            g = None
        want = g["run"] if g is not None else None
        old = self.game_band_run
        if want == old:
            return
        self.game_band_run = want
        body = {"song": g["name"], "ats": [round(o["at"], 4) for o in g["obs"]], "zero": g["zero"], "id": want} if g else None
        req = self.screen.engine._req

        def go():
            try:
                if old:
                    req("/game/band/stop", {"id": old}, timeout=3)
                if body:
                    req("/game/band", body, timeout=6)
            except Exception:
                pass                                  # no band: the game plays on without it
        threading.Thread(target=go, daemon=True).start()

    def game_latency(self):
        try:
            return max(-LATENCY_MAX, min(LATENCY_MAX, float(self.st.get("game_latency", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def game_learn(self, g):
        """After a song: half of the steady lead or lag of this song's keys goes into game_latency."""
        offs = sorted(g.get("offs") or [])
        if len(offs) < LEARN_MIN:
            return
        mid = offs[len(offs) // 2]
        if abs(mid) < 0.02:
            return
        new = max(-LATENCY_MAX, min(LATENCY_MAX, self.game_latency() + mid / 2))
        self.save(game_latency=round(new, 3))

    def game_said(self):
        """One line in the log when a song ends: how far from the notes the keys came, on average. A number
        far from 0 on every song means SEEN_LATE is off for this device."""
        g = self.game
        if g and g["hits"] and not g.get("said"):
            g["said"] = True
            from .app import log
            before = self.game_latency()
            self.game_learn(g)
            log(f"Game: {g['hits']} of {len(g['obs'])} notes played, {g['off'] / g['hits'] * 1000:+.0f} ms from the note on average "
                f"(minus: early), the player's lag {before * 1000:+.0f} ms, now {self.game_latency() * 1000:+.0f} ms")

    # ---- the clock: the song runs at its tempo
    def game_tick(self, t):
        g = self.game
        if g is None:
            if self.game_go and self.game_ready is not None:
                self.game_go = False
                if self.game_ready.get("ok"):
                    self.game_start(t, song=self.game_ready)
            return
        if g["done"] is not None:
            self.game_said()
            return
        # The song's clock is the real time since it started. Before, a slow frame held the clock back (at most a
        # quarter second a frame), so on a busy Pi the notes drifted behind the band, which plays on the real
        # time, and behind the hand: a key played with the band was told late, or wrong.
        g["clock"] = max(g["clock"], t - g.get("t0", t - g["clock"]))
        g["last"] = t
        while g["i"] < len(g["obs"]) and g["clock"] - g["obs"][g["i"]]["at"] > ALMOST + SEEN_LATE + max(0.0, self.game_latency()):
            o = g["obs"][g["i"]]
            g["i"] += 1                               # it went past the line unplayed: a miss
            g.setdefault("missed", []).append((o["key"] % 12, o["at"]))
            self.game_lose(o["key"], "g.miss", t)
            if g["done"] is not None:
                return
        if g["i"] >= len(g["obs"]):
            g["done"] = t
            return
        o = g["obs"][g["i"]]
        if 0 < o["at"] - g["clock"] < 0.5 and g.get("looked") != g["i"]:
            g["looked"] = g["i"]
            self.char.key(o["key"] % 12, t)           # the figure glances at the key coming

    # ---- drawing
    def game_keys(self, lit, t):
        """The keyboard under the figure, as wide as the screen: its number on each key, the key coming lit
        gold, a key played green, a wrong one red for a moment. Touches go through touch_keys."""
        P, cv = self.P, self.cv
        flash = self.screen.flash
        g = self.game
        wk = g["wrong_key"] if g else None
        rects = []
        import time as _t
        now = _t.time()

        def col(pc, rest):
            if wk and wk[0] == pc and t - wk[1] < 0.3:
                return WRONG
            return P["ok"] if now - flash.get(pc, 0) < 0.18 else GOLD if pc == lit else rest
        with cv.physical():
            for i, pc in enumerate(WHITES):
                x = X0 + i * (KW + GAP)
                c = col(pc, P["key"])
                down = 3 if c in (WRONG, P["ok"]) else 0
                cv.rect(x, KEYS_Y + down, KW, KH - down, c, r=10)
                if P.get("line"):
                    cv.rect(x, KEYS_Y + down, KW, KH - down, P["line"], r=10, width=1.5)
                cv.text(LABEL[pc], x + KW / 2, KEYS_Y + KH - 14, 17, (59, 42, 6) if pc == lit else P["onkey"], True, "center")
                rects.append((pc, x, KEYS_Y, KW, KH))
            for pc, a in BLACKS:
                x, _, _ = lane(pc)
                c = col(pc, P["black"])
                cv.rect(x, KEYS_Y, BW, BH, c, r=8)
                cv.text(LABEL[pc], x + BW / 2, KEYS_Y + BH - 10, 12, (29, 29, 29) if c != P["black"] else (238, 240, 247, 190), True, "center")
                # first in the list, as widgets.keyboard gives them: touch_keys and key_at take the first key under
                # the finger, and appended after the white keys a black key was never reached (the white under it won)
                rects.insert(0, (pc, x, KEYS_Y, BW, BH))
        return rects

    def game_corner(self, t):
        """The corner the rest screen keeps its streak in: the song with ‹ ›, the tempo with − +, the lives
        and the accuracy. On the plan, so in Hebrew they mirror with the time."""
        P, cv, tr = self.P, self.cv, self.tr
        from .views_main import song_title
        g = self.game
        names = self.game_list()
        gp = self.game_pick
        if gp["sel"] >= len(names):
            gp["sel"] = 0
        name = g["name"] if g else names[gp["sel"]] if names else ""
        label = song_title(name) if name else tr("g.loading") if self.game_loading else tr("g.none")
        label = self.short(label, 250 if g else 190, 15, True)   # between songs, clear of the title in the middle
        nw = cv.measure(label, 15, True)
        right = W - 24
        many = len(names) > 1
        if many:
            self.hit(right - 30, 12, 30, 30, lambda: self.game_pick_move(1))
            cv.rect(right - 30, 12, 30, 30, P["panel"], r=15)
            cv.text("›", right - 15, 33, 19, P["gold"], True, "center")
            right -= 38
        cv.text(label, right, 33, 15, P["ink"], True, "right")
        if many:
            x = right - nw - 38
            self.hit(x, 12, 30, 30, lambda: self.game_pick_move(-1))
            cv.rect(x, 12, 30, 30, P["panel"], r=15)
            cv.text("‹", x + 15, 33, 19, P["gold"], True, "center")
        rec = (self.game_song or {}) if (self.game_song or {}).get("ok") else None
        if g is None and rec and name == rec.get("name"):
            why = tr("g.only") if rec.get("only") else tr("g.why." + rec.get("why", "least"))
            cv.text(self.short(why, 300, 12), W - 24, 56, 12, P["gold"], False, "right")
        tc = tr("g.tempo", n=round(self.game_tempo * 100))
        tw = cv.measure(tc, 13, True) + 22
        y = 66
        self.hit(W - 52, y, 28, 26, lambda: self.game_set_tempo(1))
        cv.rect(W - 52, y, 28, 26, P["panel"], r=13)
        cv.text("+", W - 38, y + 19, 16, P["gold"], True, "center")
        cv.rect(W - 58 - tw, y, tw, 26, P["panel"], r=13)
        cv.text(tc, W - 58 - tw / 2, y + 18, 13, P["dim"], True, "center")
        self.hit(W - 92 - tw, y, 28, 26, lambda: self.game_set_tempo(-1))
        cv.rect(W - 92 - tw, y, 28, 26, P["panel"], r=13)
        cv.text("−", W - 78 - tw, y + 19, 16, P["gold"], True, "center")
        if g is not None:
            for i in range(LIVES):
                on = i < g["lives"]
                blink = t - g["life_at"] < 0.6 and i in (g["lives"], g["lives"] - 1) and int((t - g["life_at"]) * 8) % 2
                cv.text("♥", W - 34 - i * 20, 118, 16, (255, 255, 255) if blink else WRONG if on else (255, 255, 255, 38), True, "center")
            line = tr("g.acc", n=self.game_accuracy()) + (" · " + tr("g.streak", n=g["combo"]) if g["combo"] > 1 else "")
            cv.text(line, W - 34 - LIVES * 20, 118, 12, P["gold"] if g["combo"] > 1 else P["dim"], True, "right")

    def view_game(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        if self.game_song is None and not self.game_loading:
            self.game_load()                         # opened from the menu's round of screens
        self.hills(1)
        # the rest screen's time and date stay where they are
        ms = self.now_ms()
        cv.text(self.hm(ms), 28, 64, 48, P["ink"], True)
        cv.text(self.day_name(), 30, 92, 16, P["dim"])
        self.game_tick(t)
        g = self.game
        nxt = g["obs"][g["i"]] if g and g["done"] is None and g["i"] < len(g["obs"]) else None
        lit = nxt["key"] if nxt and nxt["at"] - g["clock"] < 0.5 else None
        self.game_corner(t)
        if g is not None:
            with cv.physical():
                for pc in WHITES:                    # a thin line over each white key, for the eye to follow
                    lx, lw, _ = lane(pc)
                    cv.rect(lx + lw / 2 - 0.5, TOP, 1, LAND - TOP - 4, (255, 255, 255, 22))
        # the figure, smaller, in the middle, standing on the keys
        kind = self.kind
        from .characters import LOW
        self.char.draw(cv, kind, W / 2, KEYS_Y - 2 - LOW.get(kind, 50) * FIG_S, t, FIG_S)
        rects = self.game_keys(lit, t)
        self.touch_keys(rects)
        self.game_octave(rects, nxt, lit)
        with cv.physical():
            if g is not None:
                self.game_notes(g, t)
        if self.theme == "anime":
            self.anime.draw_cutin(cv, left=True)     # the girl's reactions (game_girl)
        if g is None:
            self.game_title(t)
            names = self.game_list()
            msg = tr("g.anyKey") if names and not self.game_loading else tr("g.loading") if self.game_loading else ""
            if msg:
                cv.text(self.short(msg, 360, 14), W / 2, TOP + 20, 14, P["dim"], False, "center")
            self.bar([("☰", tr("menu"), None), ("▼", tr("g.slower"), lambda: self.game_set_tempo(-1)),
                      ("▲", tr("g.faster"), lambda: self.game_set_tempo(1)),
                      ("✓", tr("g.go") if names else "", self.game_play if names else None)])
        else:
            self.bar([("☰", tr("menu"), None), ("▼", tr("g.slower"), lambda: self.game_set_tempo(-1)),
                      ("▲", tr("g.faster"), lambda: self.game_set_tempo(1)), ("✓", tr("g.stop"), self.game_back)])

    # the logo's house (armonico-logo-light.svg, its 120 x 120 icon): the roof's two strokes, 4 white keys and 3 black
    LOGO_WHITE = (21.0, 41.85, 62.7, 83.55)
    LOGO_BLACK = (34.05, 54.9, 75.75)

    def game_title(self, t):
        """The game's title between songs (approved as direction C): the Armonico logo's own house, its keys
        lighting gold one after another in the rhythm of the song marked, and the name beside it. Gone while a
        song plays, so it never stands over the falling notes. Drawn as the logo is, not mirrored in Hebrew."""
        P, cv, tr = self.P, self.cv, self.tr
        k, size = 0.48, 58                            # 120 plan units of the logo's icon to 58 on the screen
        title, sub = tr("g.title"), tr("g.sub")
        tw = max(cv.measure(title, 22, True), cv.measure(sub, 12))
        x0 = W / 2 - (size + 12 + tw) / 2
        y0 = 14
        lit = self.game_title_key(t)
        P_ = lambda x, y: (x0 + x * k, y0 + y * k)
        with cv.physical():
            gold, navy, ice, ink = (226, 176, 74), (14, 42, 71), (238, 246, 255), (11, 26, 48)
            cv.polyline([P_(4, 50), P_(60, 3), P_(116, 50)], gold, 5 * k)
            cv.polyline([P_(12, 58), P_(60, 17), P_(108, 58)], (74, 160, 238), 10 * k)
            for i, x in enumerate(self.LOGO_WHITE):
                (ax, ay), h = P_(x, 60), (51 if i != 1 else 45) * k
                cv.rect(ax, ay, 17.25 * k, h, gold if lit == ("w", i) else ice, r=5 * k)
                cv.rect(ax, ay, 17.25 * k, h, navy, r=5 * k, width=1.2)
            for i, x in enumerate(self.LOGO_BLACK):
                ax, ay = P_(x, 60)
                cv.rect(ax, ay, 12 * k, 29 * k, gold if lit == ("b", i) else ink, r=3 * k)
            tx = x0 + size + 12
            cv.text(title, tx, y0 + 30, 22, (243, 231, 201), True)
            cv.text(sub, tx, y0 + 48, 12, P["dim"])

    def game_title_key(self, t):
        """The logo key lit now: the marked song's notes in turn, each held for its own time, then a rest with
        the logo's own gold key; with no song, the logo's own gold key."""
        song = self.game_ready or self.game_song or {}
        notes = [(int(n), float(s)) for n, s in (song.get("notes") or [])[:16] if isinstance(s, (int, float))]
        if not notes:
            return ("w", 1)
        total = sum(max(0.18, s) for _, s in notes) + 2.0
        u = t % total
        for n, s in notes:
            s = max(0.18, s)
            if u < s:
                slot = round((n % 12) * 6 / 11)        # the 12 notes over the logo's 7 keys, low to high
                order = [("w", 0), ("b", 0), ("w", 1), ("b", 1), ("w", 2), ("b", 2), ("w", 3)]
                return order[slot] if u < s * 0.8 else None
            u -= s
        return ("w", 1)

    def game_octave(self, rects, nxt, lit):
        """As in a lesson: the side says the octave the next note really sounds in, with an arrow (and in green)
        when it is above or below the keys' own octave, and the arrow stands over its key too."""
        if nxt is None or nxt.get("note") is None:
            return
        P, cv, s = self.P, self.cv, self.screen
        note, key = nxt["note"], nxt["key"]
        up = (note - (12 * (s.octave + 1) + key)) // 12      # how many octaves the note is from where its key sounds
        real = note // 12 - 1
        cv.text(self.tr("octave"), 30, 120, 13, P["dim"])
        cv.text(("↑ " if up > 0 else "↓ " if up < 0 else "") + str(real), 30, 146, 22, P["ok"] if up else P["sel"], True)
        if up and lit is not None:
            self.key_arrow(rects, lit, up > 0, False)

    def game_notes(self, g, t):
        P, cv = self.P, self.cv
        c = g["clock"]
        if g["done"] is None and c < g["obs"][0]["at"]:
            n = COUNT_BEATS - int((c - 0.6) // g["beat"]) if c > 0.6 else None
            if n is not None and 1 <= n <= COUNT_BEATS:
                cv.text(str(n), W / 2, 200, 56, (238, 240, 247, 215), True, "center")
        seen = range(g["i"], min(len(g["obs"]), g["i"] + 14)) if g["done"] is None else ()
        for j in seen:
            o = g["obs"][j]
            lx, lw, black = lane(o["key"])
            bottom = LAND - (o["at"] - c) * SPEED     # always where its time puts it: the eye reads the time from it
            if bottom < TOP + 8:
                break
            if bottom > LAND + 20:
                continue
            nw, nh = (36 if black else 50), 24
            # the same key again right after: this one is drawn shorter, so both show with a gap and neither moves.
            # Before, the second was pushed up to make room and dropped back when the first was played, so it
            # looked later than it was.
            again = next((g["obs"][k]["at"] for k in range(j + 1, min(len(g["obs"]), j + 6)) if g["obs"][k]["key"] == o["key"]), None)
            if again is not None:
                nh = max(10, min(nh, (again - o["at"]) * SPEED - 5))
            top = max(TOP, bottom - nh)
            x = lx + lw / 2 - nw / 2
            cv.rect(x, top, nw, bottom - top, P["gold"] if j == g["i"] else SKY, r=10)
            if bottom - top >= 18:
                cv.text(LABEL.get(o["key"], ""), x + nw / 2, bottom - 7, 13, (29, 29, 29), True, "center")
        for key, text, col, side, at in g["words"]:
            k = t - at
            if k < 0.8:
                lx, lw, _ = lane(key)
                a = min(1.0, (0.8 - k) * 3)
                cv.text(text + (" · " + side if side else ""), lx + lw / 2, LAND - 34 - k * 24, 14, alpha(col, a), True, "center")
        if g["done"] is not None:
            bx, bw = W / 2 - 200, 400
            cv.rect(bx, 150, bw, 96, alpha(P["panel"], 0.95), r=16)
            if g["out"]:
                cv.text(self.tr("g.out"), W / 2, 182, 20, WRONG, True, "center")
                cv.text(self.tr("g.outSum", a=g["i"], b=len(g["obs"]), c=g["best"]), W / 2, 208, 14, P["dim"], False, "center")
                cv.text(self.tr("g.retry"), W / 2, 230, 12, P["dim"], False, "center")
            else:
                cv.text(self.tr("g.done"), W / 2, 182, 20, P["ink"], True, "center")
                cv.text(self.tr("g.sum", a=self.game_accuracy(), b=g["best"]), W / 2, 208, 14, P["dim"], False, "center")
                cv.text(self.tr("g.againFaster", n=round(min(TEMPOS[-1], self.game_tempo + 0.1) * 100)), W / 2, 230, 12, P["dim"], False, "center")
