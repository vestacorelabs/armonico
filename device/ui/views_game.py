"""views_game.py - the game on the device: the notes of a song fall onto the keys, in the song's own time.

The song runs at its tempo and does not wait: each note falls down the lane over its key and has to be
played as it touches the gold line over the key. A note played close to the line says how close
(perfect, good or almost) and, when not perfect, whether it was early or late, so the player knows which
way to correct. A note that passes the line unplayed is a miss, and so is a wrong key: each costs one of
5 lives, and 10 in a row win one back. No lives left: out, and any key starts again. A song played to its
end can be played again 10% faster.

The game plays on the rest screen: the time and the date stay where they are, the figure stands smaller
in the middle on a keyboard as wide as the screen, and the notes fall over the sky onto its keys. The song
and the tempo are in the corner: ‹ › change the song (the engine's pick first: a started song that is
fading, or the one remembered least, or the easiest: /api/game, game_song in piano_game.py, then all the
others), − + and ▼ ▲ the tempo (60% to 120%). Any key or ✓ plays the song shown; while it plays, ✓ stops it.
The keys are the device's own keys, on the screen and on the device, and the keys of a keyboard on USB.
A key is judged at the moment it was pressed, not at the frame that sees it: piano/notes carries the time
it came (screen.py), and a touch is judged as it lands (game_touched). Before, the presses came through the
engine's /state, asked every 0.1 s, so every key was late by up to a fifth of a second and the
judgement said late or early when the key was on time.
"""
import threading

from .canvas import W
from .widgets import alpha

from .widgets import BAR

KW, KH, GAP = 92, 98, 4          # the keyboard, as wide as the screen
BW, BH = 54, 60
KEYS_Y = 480 - BAR - 8 - KH      # just over the bar
TOP = 140                        # the notes come into view under the date
LAND = KEYS_Y - 6                # the line the notes land on
SPEED = (LAND - TOP) / 1.5       # plan units a second: each note is in view for a second and a half
PERFECT, GOOD, ALMOST = 0.09, 0.18, 0.3      # seconds from the line, early or late
LIVES, BACK_AFTER = 5, 10
TEMPOS = (0.6, 0.7, 0.8, 0.85, 0.9, 1.0, 1.1, 1.2)
COUNT_BEATS = 4
FIG_S = 0.66                     # the figure's size in the game, standing on the keys
TOUCH_COPY = 1.5                 # a touched key comes back on piano/notes within this: the same press
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
        self.game_pick = {"sel": 0, "first": 0}      # the song list before a song
        self.game_go = False         # a song was picked: it starts as soon as its notes arrive
        self.game_ready = None       # the picked song's notes, from the engine
        self.game_touch = []         # keys touched on the screen: (key, time), their piano/notes copy is dropped

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
        tempo = self.game_tempo
        steps = sorted(s for _, s in notes)
        beat = max(0.3, min(1.0, steps[len(steps) // 2])) / tempo
        base = min(n for n, _ in notes) // 12 * 12          # the song's lowest C is key 1
        at, obs = COUNT_BEATS * beat + 0.6, []
        for n, step in notes:
            k = n - base
            obs.append({"key": k if 0 <= k <= 12 else k % 12, "at": at})
            at += step / tempo
        self.game = {"obs": obs, "beat": beat, "i": 0, "clock": 0.0, "last": t, "n": 0, "acc": 0.0,
                     "combo": 0, "best": 0, "lives": LIVES, "life_at": -9.0, "done": None, "out": False,
                     "words": [], "wrong_key": None, "name": song.get("name", ""), "why": song.get("why", ""), "song": song}

    def game_set_tempo(self, d):
        """▼ ▲: the tempo one step down or up; a song in play starts again at it."""
        i = min(range(len(TEMPOS)), key=lambda k: abs(TEMPOS[k] - self.game_tempo))
        self.game_tempo = TEMPOS[max(0, min(len(TEMPOS) - 1, i + d))]
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
        d = g["clock"] + (t - g["last"]) - o["at"]      # minus: early, plus: late
        if pc % 12 == o["key"] % 12:
            if abs(d) <= ALMOST:
                self.game_hit(o, d, t)
                return
            if d < 0:
                return                                # far too soon for this one: let be
        g["wrong_key"] = (pc, t)
        self.game_lose(pc, "g.wrongKey", t)

    def game_note(self, payload):
        """A key from piano/notes (\"<note> <monotonic time>\", stamped as it came: screen.py), judged at that
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
        g["acc"] += worth
        g["n"] += 1
        g["i"] += 1
        g["combo"] += 1
        g["best"] = max(g["best"], g["combo"])
        if g["combo"] % BACK_AFTER == 0 and g["lives"] < LIVES:
            g["lives"] += 1
            g["life_at"] = t
        if g["combo"] in (5, 10, 20):
            self.char.set_mood("proud", t, 2.0)
        if g["i"] >= len(g["obs"]):
            g["done"] = t

    def game_lose(self, key, word, t):
        g = self.game
        g["combo"] = 0
        g["lives"] -= 1
        g["life_at"] = t
        g["n"] += 1
        self.game_word(key, self.tr(word), WRONG, "", t)
        self.char.set_mood("offended", t, 1.2)
        if g["lives"] <= 0:
            g["done"], g["out"] = t, True

    def game_word(self, key, text, col, side, t):
        """The word over a key: one at a time on each key, so they never pile up."""
        self.game["words"] = [w for w in self.game["words"] if t - w[4] < 0.8 and w[0] != key][-6:] + [(key, text, col, side, t)]

    def game_accuracy(self):
        g = self.game
        return round(g["acc"] / g["n"] * 100) if g["n"] else 100

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
            return
        g["clock"] += min(0.25, max(0.0, t - g["last"]))   # a long frame slows the song a little, it never jumps
        g["last"] = t
        while g["i"] < len(g["obs"]) and g["clock"] - g["obs"][g["i"]]["at"] > ALMOST:
            o = g["obs"][g["i"]]
            g["i"] += 1                               # it went past the line unplayed: a miss
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
                rects.append((pc, x, KEYS_Y, BW, BH))      # in the order keys() gives them: touch_keys looks at the blacks first
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
        label = self.short(label, 250, 15, True)
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
        with cv.physical():
            if g is not None:
                self.game_notes(g, t)
        if g is None:
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

    def game_notes(self, g, t):
        P, cv = self.P, self.cv
        c = g["clock"]
        if g["done"] is None and c < g["obs"][0]["at"]:
            n = COUNT_BEATS - int((c - 0.6) // g["beat"]) if c > 0.6 else None
            if n is not None and 1 <= n <= COUNT_BEATS:
                cv.text(str(n), W / 2, 200, 56, (238, 240, 247, 215), True, "center")
        above = {}
        for j in range(g["i"], min(len(g["obs"]), g["i"] + 14)) if g["done"] is None else ():
            o = g["obs"][j]
            lx, lw, black = lane(o["key"])
            bottom = LAND - (o["at"] - c) * SPEED
            if o["key"] in above:                     # two of the same in a row keep a gap, so both show
                bottom = min(bottom, above[o["key"]] - 30)
            if bottom < TOP + 8:
                break
            if bottom > LAND + 20:
                continue
            nw, nh = (36 if black else 50), 24
            top = max(TOP, bottom - nh)
            x = lx + lw / 2 - nw / 2
            cv.rect(x, top, nw, bottom - top, P["gold"] if j == g["i"] else SKY, r=10)
            if bottom - top >= 18:
                cv.text(LABEL.get(o["key"], ""), x + nw / 2, bottom - 7, 13, (29, 29, 29), True, "center")
            above[o["key"]] = top
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
