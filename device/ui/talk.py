"""talk.py - what the figure on the rest screen says and plays, and the stars that ring.

A touch on the figure (characters.py: "talk") brings a line in a bubble a moment later, unless a quick
second touch came first (then it laughs, and at the fifth it spins). A line is put together from parts:
a group has a face and two or more lists, one pick from each. Which groups come first follows what just
happened: a pause, too many touches, keys played, the night, the echo game. A line said once is not
said again until the screen restarts, a part is not used again in the next few lines, and a line that
does not fit one row of the bubble is never said, so the bubble never wraps.

The echo game: now and then the figure plays 3 notes and waits for them on the keys. Three dots over it
light as it plays and fill as they come back. Touching it while it waits plays them again; nothing for a
while and it takes offense.

A touch on a star (the classic look or the sky) lights it up and rings a note, higher the higher it is.

No thread and no timer: tick() runs from the loop (App.tick), draw() from the rest screen's moving part.
"""
import math
import random
import time

from .canvas import W, H

BUBBLE, BUBBLE_INK = (255, 255, 255), (29, 36, 51)
DOT_OFF, DOT_ON, DOT_BAD = (255, 255, 255, 60), (232, 182, 79), (226, 92, 92)
STAR_GLOW = (255, 236, 180)

SAY_AFTER = 0.42          # seconds after a touch before it talks: a quick second touch makes it laugh instead
MAXW, SIZE = 520, 16      # the bubble's one row, in plan units, and its text size
TYPE_CPS = 45             # letters a second as the line is typed out
ECHO_EVERY = 5            # about every fifth line is the echo game
ECHO_GAP, ECHO_HOLD = 0.6, 0.45
ECHO_WAIT = 9.0           # seconds with no answer before it takes offense
ECHO_KEYS = (0, 2, 4, 5, 7, 9, 11, 12)
STAR_R = 16               # plan units around a star that count as touching it
STAR_FOR = 1.2

# ---------------------------------------------------------------- the lines
# A group: its face (characters.MOODS) and its parts. A part is a list, or the name of one the screen
# fills in (see parts()). {k}-style fields are filled from the screen's facts (facts()).
GROUPS = {
    "surprised": ("surprised", {
        "en": [["Oh,", "Whoa,", "Hey,"],
               ["I didn't hear you come in.", "I nearly fell asleep here.", "I thought you went to the big piano.",
                "I'd started talking to the stars.",
                "I thought it was the wind.", "I was just thinking about you."]],
        "he": [["אה,", "הופה,", "וואו,"],
               ["לא שמעתי אותך נכנס.", "כמעט נרדמתי פה.", "חשבתי שהלכת לפסנתר הגדול.", "כבר התחלתי לדבר עם הכוכבים.",
                "חשבתי שזה הרוח.", "בדיוק חשבתי עליך."]]}),
    "touched": ("smug", {
        "en": [["You touched me", "You came to visit me"], "@touched"],
        "he": [["נגעת בי", "ביקרת אותי"], "@touched"]}),
    "streak": ("smug", {
        "en": [["{streak} days in a row."], ["I'm counting, just so you know.", "Shame to break it today.",
                                             "One more week and I get emotional."]],
        "he": [["{streak} ימים ברצף."], ["אני סופר, שלא תחשוב.", "חבל לשבור את זה היום.", "עוד שבוע ואני מתחיל להתרגש."]]}),
    "song": ("smug", {
        "en": ["@learned", "@left"],
        "he": ["@learned", "@left"]}),
    "thirteen": ("smug", {
        "en": [["13 keys, and you picked me.", "13 keys waiting, and you're playing on me.",
                "13 keys down there, and I'm up here."]],
        "he": [["13 קלידים, ובחרת דווקא בי.", "13 קלידים מחכים, ואתה מנגן עליי.", "13 קלידים למטה, ואני פה למעלה."]]}),
    "week": ("smug", {
        "en": ["@week", ["Not bad, for a cat.", "Some pianos settle for less. Not me.",
                                              "The neighbors' piano did 40."]],
        "he": ["@week", ["לא רע, בשביל חתול.", "יש פסנתרים שמסתפקים בפחות. אני לא.", "הפסנתר של השכנים עשה 40."]]}),
    "angry": ("angry", {
        "en": [["Enough,", "Come on,", "Seriously,"],
               ["I'm not a button.", "I'm not a stress ball.", "that finger belongs on the keys.",
                "one more touch and I charge admission.",
                "the keys are down there, not here.", "I have limits too."]],
        "he": [["די,", "מספיק,", "נו באמת,"],
               ["אני לא כפתור.", "אני לא כדור לחץ.", "האצבע הזאת שייכת לקלידים.", "עוד נגיעה ואני גובה דמי כניסה.",
                "הקלידים למטה, לא פה.", "גם לי יש גבולות."]]}),
    "hurt": ("offended", {
        "en": [["I saw how you look at the big piano.", "I heard there's a bigger piano at home.",
                "Someone here misses another piano."],
               ["I'm not saying anything.", "I'm not hurt. Okay, a little.", "Just saying."]],
        "he": [["ראיתי איך אתה מסתכל על הפסנתר הגדול.", "שמעתי שיש בבית פסנתר יותר גדול.", "מישהו פה מתגעגע לפסנתר אחר."],
               ["אני לא אומר כלום.", "לא נעלבתי. טוב, קצת.", "אני סתם אומר."]]}),
    "bored": ("smug", {
        "en": [["Quiet here.", "A calm day."], ["Suspicious.", "Too calm.", "Let's change that."]],
        "he": [["שקט פה.", "יום רגוע."], ["חשוד.", "יותר מדי רגוע.", "בוא נשנה את זה."]]}),
    "boast": ("proud", {
        "en": [["I know all your songs by heart.", "I know where every C is."], ["I even like some of them.", "Ask me anything."]],
        "he": [["אני מכיר בעל פה את כל השירים שלך.", "אני יודע איפה כל דו נמצא."], ["חלק מהם אני גם אוהב.", "תשאל אותי משהו."]]}),
    "spun": ("offended", {
        "en": [["You spun me and now you're smiling?", "Once more and I throw up notes.",
                "The room is still spinning. Thanks a lot."]],
        "he": [["סחררת אותי ועכשיו אתה מחייך?", "עוד פעם ככה ואני מקיא תווים.", "החדר עדיין מסתובב. תודה רבה."]]}),
    "ignored": ("offended", {
        "en": [["Not playing? Fine, I'll play with the stars.", "Okay, I'll repeat it to myself.",
                "You passed on a challenge. Noted."]],
        "he": [["לא רוצה? בסדר, אשחק עם הכוכבים.", "טוב, אחזור על זה לבד.", "ויתרת על אתגר. רשמתי."]]}),
    "win": ("proud", {
        "en": [["Spot on.", "Right, all 3.", "3 out of 3."],
               ["Someone's been practicing in secret.", "I'm a good teacher, what can I say.", "Next time I add a fourth."]],
        "he": [["בול.", "נכון, כל ה-3.", "3 מתוך 3."],
               ["מישהו פה התאמן בסתר.", "אני מורה טוב, מה לעשות.", "בפעם הבאה אני מוסיף רביעי."]]}),
    "played": ("proud", {
        "en": [["I heard that.", "That's starting to sound like something."],
               ["Not bad at all.", "A bit more and it's a song.", "The neighbors haven't complained yet."]],
        "he": [["שמעתי את זה.", "זה כבר נשמע כמו משהו."], ["לא רע בכלל.", "עוד קצת וזה שיר.", "השכנים עוד לא התלוננו."]]}),
    "fail": ("smug", {
        "en": [["Almost.", "Close.", "Not quite."], ["The {miss} note got away.", "But with full confidence.",
                                                    "Next time I'll play it slower. Maybe."]],
        "he": [["כמעט.", "קרוב.", "לא בדיוק."], ["הצליל {miss} ברח לך.", "אבל בביטחון מלא.", "בפעם הבאה אני מנגן לאט. אולי."]]}),
    "night": ("sleepy", {
        "en": [["Late.", "{hm} at night."], ["If we play, we play quietly.", "I'm half asleep already.",
                                            "The stars are up. I'm less so."]],
        "he": [["מאוחר.", "{hm} בלילה."], ["אם מנגנים, אז בשקט.", "אני כבר חצי ישן.", "הכוכבים ערים, אני פחות."]]}),
    "morning": ("sleepy", {
        "en": [["Early.", "Morning."], ["I'm not fully awake yet.", "Without coffee I only play C."]],
        "he": [["מוקדם.", "בוקר."], ["עוד לא התעוררתי עד הסוף.", "בלי קפה אני מנגן רק דו."]]}),
    "dare": ("smug", {
        "en": [["Let's see you.", "A small challenge.", "Wait, a game."], ["3 notes, you play them back.", "I play 3, you follow."]],
        "he": [["בוא נראה אותך.", "אתגר קטן.", "רגע, משחק."], ["3 צלילים, ואתה מחזיר.", "אני מנגן 3, אתה אחריי."]]}),
    "house": ("proud", {
        "en": [["I have a chimney and no fireplace.", "Notes come out of my chimney, not smoke."],
               ["Don't ask.", "The neighbors already asked.", "That's how they built me.", "It's a style thing."]],
        "he": [["יש לי ארובה ואין לי אח.", "מהארובה שלי יוצאים תווים, לא עשן."], ["אל תשאל.", "השכנים כבר שאלו.", "ככה בנו אותי.", "זה עניין של סגנון."]]}),
    "keys": ("proud", {
        "en": [["My walls are made of keys.", "I'm the house from the logo."],
               ["Yes, a little famous.", "Touching my wall is almost a tune.", "Autographs after the lesson.",
                "Even the roof is in tune."]],
        "he": [["הקירות שלי עשויים מקלידים.", "אני הבית מהלוגו."], ["כן, קצת מפורסם.", "נגיעה בקיר זה כמעט מנגינה.", "חתימות אחרי השיעור.", "גם הגג מכוון."]]}),
    "grand": ("proud", {
        "en": [["The bow tie is real.", "A grand piano the size of a hand."],
               ["The wing is rented.", "Small crowd, big show.", "I don't get it either.", "No flash, please."]],
        "he": [["הפפיון אמיתי.", "פסנתר כנף בגודל כף יד."], ["הכנף בהשכרה.", "הקהל קטן, ההופעה גדולה.", "גם אני לא מבין איך.", "בבקשה בלי פלאש."]]}),
}
ORDINAL = {"en": ("first", "second", "third"), "he": ("הראשון", "השני", "השלישי")}


class Lines:
    """The sentences of one screen's run: what was said, the parts used lately."""

    def __init__(self, seed=None):
        self.rnd = random.Random(seed)
        self.said = set()
        self.recent = []
        self.groups = []              # the groups of the last lines, newest last

    def parts(self, name, lang, f):
        """The lists a part names (@...), from the facts."""
        if name == "@touched":
            if f.get("played"):
                return {"en": ["more than you played today.", "like I'm the most interesting key here."],
                        "he": ["יותר פעמים משניגנת היום.", "כאילו אני הקליד הכי מעניין פה."]}[lang]
            return {"en": ["and still haven't played a note.", "like I'm the most interesting key here.",
                           "more than you played today."],
                    "he": ["ועדיין לא ניגנת תו.", "כאילו אני הקליד הכי מעניין פה.", "יותר פעמים משניגנת היום."]}[lang]
        if name == "@learned":
            # how many of the song's parts are learned, one part and many said as they are said
            k, n = f.get("k", 0), f.get("n", 0)
            if lang == "he":
                return ["ב-{song} למדת חלק אחד מתוך {n}." if k == 1 else "ב-{song} למדת {k} חלקים מתוך {n}."]
            return ["In {song} you've learned 1 part of {n}." if k == 1 else "In {song} you've learned {k} parts of {n}."]
        if name == "@left":
            left = f.get("n", 0) - f.get("k", 0)
            if lang == "he":
                own = "נשאר עוד אחד, והוא לא יילמד לבד." if left == 1 else f"נשארו עוד {left}, והם לא יילמדו לבד."
                return [own, "השאר מחכים בסבלנות. אני פחות."]
            own = "One more to go, and it won't learn itself." if left == 1 else f"{left} more to go, and they won't learn themselves."
            return [own, "The rest wait patiently. I don't."]
        if name == "@week":
            w = f.get("week", 0)
            if lang == "he":
                return ["דקה אחת השבוע." if w == 1 else "{week} דקות השבוע."]
            return ["1 minute this week." if w == 1 else "{week} minutes this week."]
        return []

    def usable(self, group, f):
        """A group whose facts are there: a song to talk about, a streak, minutes this week."""
        if group == "song":
            return bool(f.get("song")) and 0 < f.get("k", 0) < f.get("n", 0)
        if group == "streak":
            return f.get("streak", 0) > 1
        if group == "week":
            return f.get("week", 0) > 0
        return group in GROUPS

    def compose(self, group, lang, f, fits):
        """A new line of the group, as (text, face), or None when the group has nothing new that fits."""
        if not self.usable(group, f):
            return None
        face, by = GROUPS[group]
        lists = [self.parts(p, lang, f) if isinstance(p, str) else p for p in by["he" if lang == "he" else "en"]]
        lists = [[x for x in part if not x.startswith("@")] for part in lists]      # a name is never said as it is
        if not all(lists):
            return None
        for tries in range(40):
            picks = [self.rnd.choice(x) for x in lists]
            if tries < 25 and len(lists) > 1 and any(p in self.recent for p in picks):
                continue
            try:
                s = " ".join(picks).format(**f)
            except (KeyError, IndexError):
                return None
            if s in self.said or not fits(s):
                continue
            self.said.add(s)
            self.recent = (self.recent + picks)[-14:]
            self.groups = (self.groups + [group])[-4:]
            return s, face
        return None

    def pick(self, lang, f, fits, kind):
        """What to say after a touch: the groups that fit what just happened first, then the rest in
        a shuffled order, then any that still has something new. None when everything was said."""
        order = []
        if f.get("touches", 0) >= 7:
            order.append("angry")
        if f.get("played", 0) >= 8:
            order.append("played")
        h = f.get("hour", 12)
        night, morning = h >= 23 or h < 5, 5 <= h < 9
        if f.get("idle", 0) > 45:
            order.append("night" if night else "morning" if morning else "surprised")
        elif night and self.rnd.random() < 0.45:
            order.append("night")
        rest = ["touched", "streak", "song", "thirteen", "week", "hurt", "bored", "boast", kind, kind]
        self.rnd.shuffle(rest)
        # not the same group twice running, and not angry twice running (it was: 7 touches in a minute kept it
        # angry at every touch); a group of the last 3 lines comes after the others
        last = self.groups[-1] if self.groups else None
        recent = set(self.groups[-3:])
        order = [g for g in order if g not in recent or g not in ("angry", "played")]   # angry once in 4 lines at most
        seq = [g for g in order if g != last] + [g for g in rest if g not in recent] + [g for g in rest if g in recent]
        seq += [g for g in ("surprised", "touched", "bored", "boast", kind, "hurt") if g not in seq]
        seq = [g for g in seq if g != last]
        for g in seq:
            got = self.compose(g, lang, f, fits)
            if got:
                return got
        # everything else was said: it starts over (it went quiet for good before, and looked as if it did not
        # answer; then it said the one group left again and again)
        self.said, self.recent = set(), []
        for g in seq + ([last] if last else []):
            got = self.compose(g, lang, f, fits)
            if got:
                return got
        return None


# ---------------------------------------------------------------- the screen's side
class TalkViews:
    def init_talk_state(self):
        self.lines = Lines()
        self.talk_due = None          # (time, what) of what comes next: "line", "spun"
        self.bubble = None            # {"text", "at", "until"}
        self.echo = None              # the echo game while it runs
        self.echo_miss = 0
        self.talk_n = 0               # lines said since the screen started, for the echo game
        self.touch_log = []
        self.last_talk_touch = -999.0
        self.played_since = 0
        self.star_flares = []         # (x, y, since) in physical plan units
        self.fig_notes = []           # (time, "on 64" / "off 64") the figure plays

    # ---- facts and fitting
    def talk_facts(self, t):
        s = self.screen
        stats = (s.engine.overview or {}).get("stats") or {}
        card = self.song_card_data() if hasattr(self, "song_card_data") else None
        now = self.local()
        f = {"streak": int(stats.get("streak", 0) or 0), "week": int(stats.get("week_minutes", 0) or 0),
             "hour": now.hour, "hm": f"{now.hour:02d}:{now.minute:02d}", "played": self.played_since,
             "touches": len([q for q in self.touch_log if t - q < 60]), "idle": t - self.last_talk_touch,
             "miss": ORDINAL["he" if self.lang == "he" else "en"][max(0, min(2, self.echo_miss))]}
        if card:
            from .views_main import song_title
            states = card.get("states") or []
            f.update(song=song_title(card["name"]), k=sum(1 for v in states if v), n=len(states))
            f["left"] = f["n"] - f["k"]
        return f

    def talk_fits(self, s):
        return self.cv.measure(s, SIZE, True) <= MAXW

    def say_line(self, got, t):
        if not got:
            return
        text, face = got
        until = t + 2.4 + len(text) * 0.06
        self.bubble = {"text": text, "at": t, "until": until}
        self.char.set_mood(face, t, until - t + 0.4)

    def talk_group(self, group, t):
        self.say_line(self.lines.compose(group, self.lang, self.talk_facts(t), self.talk_fits), t)

    # ---- input
    def rest_touch(self, x, y, px, py):
        """A touch on the rest screen (App.pointer_down): a star, the figure, or only a glance."""
        t = self.t()
        if not self.char.box_hit(x, y) and self.star_touch(px, py, t):
            return
        if self.kind == "grand" and not self.echo and not self.char.busy(t) and self.char.keys_hit(x, y):
            # a touch on the grand's own keys is an ask to play: the echo game, on top of every fifth line
            self.char.look_t, self.char.look_hold = [0.0, 0.6], t + 1.6
            self.bubble, self.talk_due = None, None
            self.talk_group("dare", t)
            self.echo_start(t + 1.6)
            return
        got = self.char.touch(x, y, t)
        if not got:
            return
        if got in ("laugh", "spin", "dizzy"):
            self.bubble = None
            self.talk_due = (t + 2.3, "spun") if got == "spin" else None if got != "dizzy" else self.talk_due
            return
        if self.echo and self.echo.get("wait"):
            self.echo_play(t + 0.2)                   # waiting for the notes: it plays them again
            return
        self.talk_due = (t + SAY_AFTER, "line")

    def star_touch(self, px, py, t):
        """A touch on a star of the rest screen's still layer: it flares and rings."""
        L = (getattr(self, "_layers", None) or {}).get(self.cv.surf.get_size())
        if not L or not L.get("stars"):
            return False
        r = STAR_R * self.cv.scale
        best = min(L["stars"], key=lambda q: (q[0] - px) ** 2 + (q[1] - py) ** 2)
        if (best[0] - px) ** 2 + (best[1] - py) ** 2 > r * r:
            return False
        x, y = self.cv.to_plan(best[0], best[1])
        self.star_flares = [f for f in self.star_flares if t - f[2] < STAR_FOR][-5:] + [(x, y, t)]
        steps = (0, 2, 4, 7, 9)
        k = int(max(0.0, min(0.999, (380 - y) / 380)) * 10)
        self.fig_note(72 + (k // 5) * 12 + steps[k % 5] - 12, t, 0.9)
        self.char.look_t = [max(-1.0, min(1.0, (x - self.char.last[0]) / 260)), max(-1.0, min(1.0, (y - self.char.last[1]) / 160))]
        self.char.look_hold = t + 1.6
        return True

    def talk_key(self, pc, t):
        """A key pressed while the rest screen shows: an answer to the echo game, or just playing."""
        if self.echo and self.echo.get("wait"):
            e = self.echo
            if pc != e["notes"][len(e["got"])]:
                self.char.light(pc, t, 0.35, "wrong")
            e["got"].append(pc)
            e["since"] = t
            if len(e["got"]) == len(e["notes"]):
                miss = next((i for i, (a, b) in enumerate(zip(e["got"], e["notes"])) if a != b), -1)
                self.echo_miss = max(0, miss)
                e["done"], e["wait"] = (miss < 0, t), False
                self.talk_due = (t + 0.7, "win" if miss < 0 else "fail")
            return
        self.played_since += 1

    # ---- the echo game
    def fig_note(self, note, t, hold):
        """A note the figure plays: through the device's sound as a touched key, and marked as sent, so
        the copy the engine reports back is not taken for a key played (as free_key does)."""
        note = max(21, min(108, int(note)))
        self.fig_notes += [(t, f"on {note}"), (t + hold, f"off {note}")]

    def echo_start(self, t):
        rnd = self.lines.rnd
        self.echo = {"notes": [rnd.choice(ECHO_KEYS) for _ in range(3)], "got": [], "wait": False, "lit": -1}
        self.echo_play(t)

    def echo_play(self, at):
        e = self.echo
        e.update(got=[], wait=False, play_at=at, lit=-1, done=None)
        base = 12 * (self.screen.octave + 1)
        for i, pc in enumerate(e["notes"]):
            self.fig_note(base + pc, at + i * ECHO_GAP, ECHO_HOLD)
            self.char.light(pc, at + i * ECHO_GAP, ECHO_HOLD, "play")      # the grand plays them on its own keys
        if self.bubble:
            self.bubble["until"] = min(self.bubble["until"], at)

    # ---- the loop
    def talk_tick(self):
        """From App.tick: the notes the figure plays, the line due, the echo game's clock."""
        t = self.t()
        if self.fig_notes:
            due = [n for n in self.fig_notes if n[0] <= t]
            if due:
                self.fig_notes = [n for n in self.fig_notes if n[0] > t]
                sent = getattr(self.screen, "touch_sent", None)
                if sent is None:
                    sent = self.screen.touch_sent = []
                for _, msg in due:
                    self.screen.pub("piano/device/note", msg)
                    if msg.startswith("on "):
                        sent[:] = [q for q in sent if time.time() - q[1] < 2] + [(int(msg[3:]) % 12, time.time())]
        self.char.lid_shut = bool(self.echo)          # the grand keeps its lid down under the echo game's dots
        if self.view != "rest" or self.tour_on():
            return
        e = self.echo
        if e and not e.get("wait") and not e.get("done") and "play_at" in e:
            i = int((t - e["play_at"]) // ECHO_GAP)
            e["lit"] = i if 0 <= i < len(e["notes"]) else -1
            if i >= len(e["notes"]):
                e.update(wait=True, since=t, lit=-1)
        if e and e.get("wait") and t - e.get("since", t) > ECHO_WAIT:
            self.echo = None
            self.talk_group("ignored", t)
        if self.talk_due and t >= self.talk_due[0]:
            what = self.talk_due[1]
            self.talk_due = None
            if what == "line":
                self.touch_log = [q for q in self.touch_log if t - q < 60] + [t]
                self.talk_n += 1
                if self.talk_n % ECHO_EVERY == 3 and not self.echo:
                    self.talk_group("dare", t)
                    self.echo_start(t + 1.6)
                else:
                    self.say_line(self.lines.pick(self.lang, self.talk_facts(t), self.talk_fits, self.kind), t)
                self.last_talk_touch, self.played_since = t, 0
            else:
                if what in ("win", "fail"):
                    self.echo = None
                    if what == "win":
                        self.char.move, self.char.move_t = "jump", t
                self.talk_group(what, t)

    def talk_busy(self):
        """Something of the figure's is on screen or about to be: the frame rate stays up."""
        t = self.t()
        return bool(self.bubble or self.echo or self.talk_due or self.fig_notes
                    or any(t - f[2] < STAR_FOR for f in self.star_flares))

    # ---- drawing, in physical plan units over the rest screen's moving part
    def talk_draw(self, t):
        cv = self.cv
        cx, cy, s = self.char.last
        top = cy - 95 * s - 14 - 38
        with cv.physical():
            for x, y, at in self.star_flares:
                k = t - at
                if 0 <= k < STAR_FOR:
                    a = 1 - k / STAR_FOR
                    cv.circle(x, y, 3 + k * 10, STAR_GLOW + (int(90 * a),))
                    cv.circle(x, y, 2.5, STAR_GLOW + (int(255 * a),))
                    d = 8 + k * 6
                    cv.line(x - d, y, x + d, y, STAR_GLOW + (int(200 * a),), 1.5)
                    cv.line(x, y - d, x, y + d, STAR_GLOW + (int(200 * a),), 1.5)
            b = self.bubble
            if b and t > b["until"]:
                b = self.bubble = None
            e = self.echo
            if e and (not b or e.get("done")):
                for i in range(len(e["notes"])):
                    x = cx + (i - 1) * 22
                    got = len(e["got"]) > i
                    if e.get("lit") == i:
                        col, r = DOT_ON, 7
                    elif got:
                        bad = e.get("done") and not e["done"][0] and i == self.echo_miss
                        col, r = (DOT_BAD if bad else (255, 255, 255)), 6
                    else:
                        col, r = DOT_OFF, 6
                    cv.circle(x, top + 19, r, col)
            if b and t >= b["at"]:
                full = b["text"]
                n = min(len(full), int((t - b["at"]) * TYPE_CPS) + 1)
                w = cv.measure(full, SIZE, True) + 30
                bx = max(12, min(W - 12 - w, cx - w / 2))
                a = min(1.0, (b["until"] - t) * 3)
                col = BUBBLE + (int(255 * a),) if a < 1 else BUBBLE
                cv.polygon([(cx - 10, top + 36), (cx + 2, top + 52), (cx + 12, top + 36)], col)
                cv.rect(bx, top, w, 38, col, r=19)
                ink = BUBBLE_INK + (int(255 * a),) if a < 1 else BUBBLE_INK
                if self.lang == "he":
                    cv.text(full[:n], bx + w - 15, top + 25, SIZE, ink, True, "right")
                else:
                    cv.text(full[:n], bx + 15, top + 25, SIZE, ink, True)
