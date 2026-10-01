"""
piano_learner.py - what Armonico learns about the player, lesson by lesson.

Small models from before the time of AI: each one is a few lines of arithmetic, explains
itself, and runs on a Raspberry Pi in no time. None of them changes its mind after one lesson:
each moves a little with every piece of evidence, so one bad day does not undo a good week.

1. Memory, for every part of a song. A part has a stability S, the days until the chance of
   remembering it falls to 90%, and a difficulty D from 1 to 10. Every showing from memory gets
   a grade from 1 to 4 (again, hard, good, easy) from the help it needed, the wrong keys and the
   hesitations in it. A good showing grows S, and grows it more the longer the part went
   unplayed; a hard one grows it a little, an easy one a lot, and again shrinks it. The curves are
   those of FSRS-4.5 (open-spaced-repetition, MIT license), with its published default weights.
   Armonico adds its own rules around them: a part shown again the same day moves by a bounded
   factor only (one slip in a second lesson that day does not erase weeks), a part starts from how
   hard it was to learn, and the grade is about memory, while a finger slipping to the next key is
   kept for the drills instead. Once the player has 100 graded showings, the two weights for
   growing and shrinking are fitted to the player's own history, at most once a day, and the new
   fit is kept only when it predicts the latest showings better than both the fit in use and the
   defaults.
2. Skills (Bayesian Knowledge Tracing). For each of black keys, leaps, long parts, rhythm,
   memory and fluency, the chance that the skill is already the player's own. Every part needs
   some of them, and every attempt at a part moves each of its skills a little, up or down.
   Fluency moves with hesitations: a right note after an unusually long pause is known, but not
   fluent yet.
3. Pace, for every song. The speed the player holds in the full runs, smoothed with a trend
   (Holt's method), so a run starts at the speed the player is heading for, not at the last one.
4. Steps between two notes that keep going wrong, by their place in the song and by their two
   notes, with the weight of an error halving every week.
5. Fatigue within one lesson: the mistakes of the last rounds against those of the first ones,
   with a test that rarely calls a steady lesson tired.

The lesson engine asks it which slipped parts to practice first, how many new parts a lesson
takes, at what speed the runs start, which parts of other songs to warm up before they fade,
which step to drill alone, and when to suggest stopping. Nothing here reads or writes files:
what it keeps lives in the profile's learner.json and in the parts of lessons.json.
"""
import math

DAY = 86400.0

# ---------- 1. memory ----------
# FSRS-4.5 and its default weights w0..w16 (open-spaced-repetition, MIT license), as published in
# https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367,
     1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755)
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
DECAY, FACTOR = -0.5, 19 / 81           # R = (1 + FACTOR t / S) ^ DECAY, so R is 90% when t = S
TARGET = 0.9                            # the retention the parts are kept at
S_START, D_START = 1.0, 5.0
# Armonico's own start: a part learned at the first try is held longer than one that needed rounds
# again (the fails while learning it are the first evidence of how hard it is)
S_LEARNED = {0: 2.0, 1: 1.0}            # 2 fails or more: S_HARD_START
S_HARD_START = 0.5
# Armonico's own same-day rule: a showing within half a day of the first showing of that day moves S
# by a bounded factor only, exp(SAME_DAY_K * (grade - 3)), and all of that day's showings together
# keep it between SAME_DAY_FLOOR and exp(SAME_DAY_K) of what the first one left. Good keeps it, easy
# grows it a little, again takes it down by at most half. The day is counted from its first showing
# and not from the last one: lessons 8 to 11 hours apart were each "the same day" as the one before,
# so the part never grew, and the slips of the chain multiplied.
SAME_DAY = 0.5                          # days
SAME_DAY_K, SAME_DAY_FLOOR = 0.25, 0.5
S_MIN, S_MAX = 0.2, 365.0
DEFAULT_FIT = {"grow": W[8], "fall": W[11]}     # the two weights fitted to the player


def recall(days, stability):
    """The chance a part is still remembered after this many days (the FSRS-4.5 power curve)."""
    return (1 + FACTOR * max(0.0, days) / max(S_MIN, stability)) ** DECAY


def recall_now(part, now):
    if "S" not in part:
        return None
    return recall((now - part.get("mem_at", now)) / DAY, part["S"])


def start_s(fails):
    if not isinstance(fails, int) or fails < 0:
        return S_START
    return S_LEARNED.get(fails, S_HARD_START)


def start_memory(part, weakness, now, fails=None):
    """A part just learned. A part whose skills are still weak starts harder, and one that needed
    rounds again while it was learned starts less stable (fails None: from before, S_START)."""
    part["S"] = start_s(fails)
    part["D"] = round(min(10.0, D_START + 3 * weakness), 2)
    part["mem_at"] = now
    part["day_at"], part["day_S"] = now, part["S"]      # the lesson it was learned in opens its day


def as_grade(grade):
    """A grade from 1 to 4. True and False, from before the grades, are good and again."""
    if grade is True:
        return GOOD
    if grade is False or grade is None:
        return AGAIN
    return max(AGAIN, min(EASY, int(grade)))


def grade(help="memory", wrong=0, named=False, hesitant=0, even=False, skipped=0):
    """How a showing went, for memory: did the next note come? (recall_grade below does the work.)"""
    return recall_grade(help, wrong, named, hesitant, even, skipped)[0]


def recall_grade(help="memory", wrong=0, named=False, hesitant=0, even=False, skipped=0):
    """(memory grade, slips): the two tracks of a showing kept apart.

    Memory: Again when a note had to be named, was skipped, or the part needed the keys lit or the
    numbers; Hard after a hesitation, or 2 wrong keys or more found again (the note was there, but
    not sure); Easy clean from memory with every gap even; Good the rest. A single wrong key that
    the player found again is a finger that slipped, not a note forgotten: it stays Good for memory
    and goes to the slips, which the lesson gives to the drills of that step."""
    if named or skipped or help != "memory":
        g = AGAIN
    elif wrong >= 2 or hesitant:
        g = HARD
    else:
        g = EASY if even and not wrong else GOOD
    return g, int(wrong)


def next_memory(s, d, r, grade, w=None, days=None):
    """One FSRS-4.5 step: the stability and difficulty after a showing of this grade, when the
    chance of remembering it was r. w holds the fitted growth (w8) and fall (w11) weights. days,
    the time since the last showing: under SAME_DAY, Armonico's bounded same-day rule instead."""
    w = w or DEFAULT_FIT
    if days is not None and days < SAME_DAY:
        s2 = max(s * SAME_DAY_FLOOR, s * math.exp(SAME_DAY_K * (grade - GOOD)))
    elif grade > AGAIN:
        bonus = W[15] if grade == HARD else W[16] if grade == EASY else 1.0
        s2 = s * (1 + math.exp(w["grow"]) * (11 - d) * s ** -W[9] * (math.exp(W[10] * (1 - r)) - 1) * bonus)
    else:
        s2 = min(s, w["fall"] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r)))
    d2 = d - W[6] * (grade - 3)
    d2 = W[7] * W[4] + (1 - W[7]) * d2              # a little back toward the starting difficulty
    return max(S_MIN, min(S_MAX, s2)), max(1.0, min(10.0, d2))


def day_step(s, d, r, grade, w, since_day, s_day):
    """One showing: (S, D, whether it opens a new day). since_day, the days since the first showing
    of the day; s_day, the stability that first showing left. Within SAME_DAY of it, the bounded
    same-day step, with the day's total kept between SAME_DAY_FLOOR and exp(SAME_DAY_K) of s_day;
    later, the full FSRS step, and this showing is the first of a new day."""
    if since_day < SAME_DAY:
        s2, d2 = next_memory(s, d, r, grade, w, 0.0)
        s2 = max(s_day * SAME_DAY_FLOOR, min(s_day * math.exp(SAME_DAY_K), s2))
        return max(S_MIN, min(S_MAX, s2)), d2, False
    s2, d2 = next_memory(s, d, r, grade, w)
    return s2, d2, True


def update_memory(part, grade, now, w=None):
    """After a showing from memory: the run at the start of a lesson, or a warm-up."""
    g = as_grade(grade)
    if "S" not in part:
        start_memory(part, 0.0, part.get("first", now))
    days = (now - part.get("mem_at", now)) / DAY
    r = recall(days, part["S"])
    # a part saved before the day was kept: its day starts at its last showing, as it did then
    day_at = part.get("day_at", part.get("mem_at", now))
    s_day = part.get("day_S", part["S"])
    s, d, new_day = day_step(part["S"], part["D"], r, g, w, (now - day_at) / DAY, s_day)
    part["S"] = round(s, 3)
    part["D"] = round(d, 2)
    part["mem_at"] = now
    if new_day:
        part["day_at"], part["day_S"] = now, part["S"]
    else:
        part["day_at"], part["day_S"] = day_at, s_day     # the day goes on from where it began
    return g


def drill_order(slipped, parts, now):
    """The slipped parts, the ones memory holds least firmly first: hardest, then least stable."""
    def key(k):
        p = parts.get(str(k), {})
        return (-p.get("D", D_START), p.get("S", S_START), k)
    return sorted(slipped, key=key)


FADING = 0.7          # a part this unlikely to be remembered now is fading
SOLID_R, SOLID_S = 0.9, 3.0


def new_count(allowed, parts, known, now):
    """How many new parts this lesson takes, around the pace's number.

    One fewer while two or more parts are fading, so the lesson has room to bring them back;
    one more when every part is both fresh and stable for at least 3 days. Never below 1."""
    rs = [(recall_now(parts[str(k)], now), parts[str(k)].get("S", S_START)) for k in known if str(k) in parts]
    rs = [(r, s) for r, s in rs if r is not None]
    if not rs:
        return allowed, None
    fading = sum(1 for r, _ in rs if r < FADING)
    if fading >= 2:
        return max(1, allowed - 1), "fading"
    if len(rs) >= 2 and all(r >= SOLID_R and s >= SOLID_S for r, s in rs):
        return allowed + 1, "solid"
    return allowed, None


# ---------- 1b. the player's own curve ----------
REVIEWS_MAX = 3000        # rows of history kept, the oldest go first
FIT_MIN = 100             # graded showings (not the same day) before the curve is the player's own
FIT_EVERY = DAY           # at most one refit a day
FIT_STEP = {"grow": 0.1, "fall": 0.15}
FIT_MOVES = 2             # steps a weight may move in one refit, either way
FIT_RANGE = {"grow": (W[8] - 1.0, W[8] + 1.0), "fall": (W[11] * 0.5, W[11] * 1.5)}
FIT_LAPSES = 15           # forgotten showings before the fall weight is fitted at all: few say little
FIT_HOLD = 0.2            # the latest share of the history that judges a fit, never used to make it
FIT_GAIN = 0.01           # a new fit has to predict those showings this much better (relative) to be kept
FIT_PULL = 0.1            # how hard the fit is pulled back toward the defaults, over the number of showings
CAL_BINS = ((0, 1), (1, 3), (3, 7), (7, 30), (30, 10 ** 6))   # days since the last showing


def log_review(brain, pid, now, grade, d0=None, s0=None):
    """One row of the player's history: [part, time, grade], or [part, time, 0, difficulty] when
    the part was just learned ([part, time, 0, difficulty, stability] when it started off S_START).
    pid names the part: "song#index"."""
    rows = brain.get("reviews")
    if not isinstance(rows, list):
        rows = brain["reviews"] = []
    row = [pid, int(now), int(grade)]
    if grade == 0:
        row.append(round(d0 if d0 is not None else D_START, 2))
        if s0 is not None and s0 != S_START:
            row.append(round(s0, 3))
    rows.append(row)
    del rows[:-REVIEWS_MAX]


def weights(brain):
    """The growth and fall weights to use: the fitted ones when there are, else FSRS's own."""
    out = dict(DEFAULT_FIT)
    fit = brain.get("fit") if isinstance(brain, dict) else None
    if isinstance(fit, dict):
        for k, (lo, hi) in FIT_RANGE.items():
            v = fit.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
                out[k] = float(v)
    return out


def _rows(brain):
    rows = brain.get("reviews") if isinstance(brain, dict) else None
    good = [r for r in rows or [] if isinstance(r, list) and len(r) >= 3 and isinstance(r[0], str)
            and isinstance(r[1], (int, float)) and r[2] in (0, 1, 2, 3, 4)]
    return sorted(good, key=lambda r: (r[0], r[1]))


def replay(rows, w):
    """Every graded showing of the history replayed in order with these weights:
    [(time, days since the last showing, the chance the curve gave, remembered)]. Showings the
    same day move the state (as in a lesson) but are not scored: there memory had no time to fade.
    rows must be sorted by part, then time."""
    state, out = {}, []
    for row in rows:
        pid, t, g = row[0], row[1], row[2]
        if g == 0:
            d0 = row[3] if len(row) > 3 and isinstance(row[3], (int, float)) else D_START
            s0 = row[4] if len(row) > 4 and isinstance(row[4], (int, float)) else S_START
            state[pid] = (s0, d0, t, t, s0)
            continue
        if pid not in state:              # learned before the history was kept: it starts here
            state[pid] = (S_START, D_START, t, t, S_START)
            continue
        s, d, t0, day_at, s_day = state[pid]
        days = (t - t0) / DAY
        r = min(1 - 1e-4, max(1e-4, recall(days, s)))
        if days >= SAME_DAY:
            out.append((t, days, r, g > AGAIN))
        s, d, new_day = day_step(s, d, r, g, w, (t - day_at) / DAY, s_day)   # as update_memory does
        if new_day:
            day_at, s_day = t, s
        state[pid] = (s, d, t, day_at, s_day)
    return out


def _loss(scored, since=None):
    got = [(r, ok) for t, _, r, ok in scored if since is None or t >= since]
    if not got:
        return None
    return -sum(math.log(r) if ok else math.log(1 - r) for r, ok in got) / len(got)


def log_loss(rows, w, since=None):
    """How surprised the curve with these weights is by the history (the showings from since on,
    when given). Lower is better. rows must be sorted by part, then time."""
    return _loss(replay(rows, w), since)


def calibration(rows, w):
    """How well the curve's chances match what happened: log-loss, and the weighted root mean
    square gap between the chance given and the share remembered, in bins of days since the last
    showing. Only to read: it changes nothing."""
    scored = replay(rows, w)
    if not scored:
        return None
    err, n = 0.0, 0
    for lo, hi in CAL_BINS:
        b = [(r, ok) for _, days, r, ok in scored if lo <= days < hi]
        if b:
            gap = sum(r for r, _ in b) / len(b) - sum(1 for _, ok in b if ok) / len(b)
            err += len(b) * gap * gap
            n += len(b)
    return {"loss": round(_loss(scored), 4), "rmse": round(math.sqrt(err / n), 4), "n": len(scored),
            "lapses": sum(1 for *_, ok in scored if not ok)}


def refit(brain, now):
    """Fits the growth and fall weights to this player, safely.

    A small grid around the weights in use, each moving at most FIT_MOVES steps, is fitted on the
    older showings, with a pull back toward the defaults that weakens as the history grows. The
    latest FIT_HOLD of the showings, never used to fit, then judges it: the new pair is kept only
    when it predicts them better, by FIT_GAIN, than both the pair in use and the defaults. The fall
    weight moves only after FIT_LAPSES forgotten showings. Not before FIT_MIN graded showings
    (same-day ones do not count), at most once a day. Returns the fit (kept or not, with its
    calibration), or None when nothing was tried."""
    rows = _rows(brain)
    fit = brain.get("fit") if isinstance(brain.get("fit"), dict) else {}
    if now - fit.get("at", 0) < FIT_EVERY:
        return None
    cur = weights(brain)
    scored = replay(rows, cur)
    if len(scored) < FIT_MIN:
        return None
    times = sorted(t for t, *_ in scored)
    cut = times[int(len(times) * (1 - FIT_HOLD))]
    train_n = sum(1 for t in times if t < cut)
    lapses = sum(1 for *_, ok in scored if not ok)
    pull = FIT_PULL / max(1, train_n)

    def train_loss(w):
        base = _loss([x for x in replay(rows, w) if x[0] < cut])
        if base is None:
            return None
        return base + pull * sum(((w[k] - DEFAULT_FIT[k]) / FIT_STEP[k]) ** 2 for k in w)

    best = None
    for i in range(-FIT_MOVES, FIT_MOVES + 1):
        for j in range(-FIT_MOVES, FIT_MOVES + 1):
            if j and lapses < FIT_LAPSES:
                continue                       # too few lapses to say anything about the fall
            w = {}
            for k, step in (("grow", i), ("fall", j)):
                lo, hi = FIT_RANGE[k]
                w[k] = max(lo, min(hi, cur[k] + step * FIT_STEP[k]))
            loss = train_loss(w)
            if loss is None:
                return None
            rank = (round(loss, 6), abs(i) + abs(j))
            if best is None or rank < best[0]:
                best = (rank, w)
    held = {name: _loss(replay(rows, w), cut) for name, w in (("new", best[1]), ("cur", cur), ("default", DEFAULT_FIT))}
    keep = held["new"] is not None and all(held["new"] < held[o] * (1 - FIT_GAIN) for o in ("cur", "default")
                                           if held[o] is not None)
    use = best[1] if keep else cur
    brain["fit"] = {"grow": round(use["grow"], 4), "fall": round(use["fall"], 4),
                    "at": int(now), "reviews": len(scored), "kept": bool(keep),
                    "held_loss": round(held["new" if keep else "cur"] or 0.0, 4),
                    "cal": calibration(rows, use)}
    return brain["fit"]


# ---------- 1c. review before forgetting ----------
WARM_MAX = 3              # parts in a warm-up, at most
WARM_AHEAD = DAY / 2      # a part that falls under the target within this time is about to fade
WARM_FLOOR = 0.6          # below this it is gone too far for a warm-up: its own lesson brings it back


def about_to_fade(songs, now, limit=WARM_MAX):
    """Parts that memory is about to let go of: still remembered now, but under the target
    retention within half a day. songs is {song: its parts}. The closest to forgetting first:
    [(song, index, recall now)]."""
    out = []
    for song, parts in songs.items():
        if not isinstance(parts, dict):
            continue
        for k, p in parts.items():
            if not (isinstance(p, dict) and "S" in p and str(k).isdigit()):
                continue
            r = recall_now(p, now)
            soon = recall((now + WARM_AHEAD - p.get("mem_at", now)) / DAY, p["S"])
            if r >= WARM_FLOOR and soon < TARGET:
                out.append((r, song, int(k)))
    out.sort()
    return [(song, k, round(r, 3)) for r, song, k in out[:limit]]


def comeback(parts, now):
    """The parts of a song that memory has let go of further than a warm-up can bring back (under
    WARM_FLOOR now): the song is worth a lesson of its own. parts is the song's parts."""
    if not isinstance(parts, dict):
        return 0
    return sum(1 for k, p in parts.items() if isinstance(p, dict) and "S" in p and str(k).isdigit()
               and recall_now(p, now) < WARM_FLOOR)


# ---------- 2. skills ----------
SKILLS = ("black", "leap", "long", "rhythm", "memory", "fluency")
BKT_L0, BKT_T, BKT_G, BKT_S = 0.3, 0.08, 0.25, 0.1     # start, learn, guess, slip
LEAP = 7              # semitones between two notes that count as a leap
LONG = 10             # notes in a part that count as a long part
RHYTHM_SPREAD = 3.0   # longest note over shortest: a part with this much rhythm in it


def part_skills(seg, timing=None):
    """The skills a part asks for, from its own notes and, when known, their lengths."""
    out = ["memory"]
    if any(n % 12 in (1, 3, 6, 8, 10) for n in seg):
        out.append("black")
    if any(abs(a - b) >= LEAP for a, b in zip(seg, seg[1:])):
        out.append("leap")
    if len(seg) >= LONG:
        out.append("long")
    lens = [t[0] for t in (timing or []) if t]
    if len(lens) >= 3 and min(lens) > 0 and max(lens) / min(lens) >= RHYTHM_SPREAD:
        out.append("rhythm")
    return out


def skill_level(skills, name):
    return skills.get(name, BKT_L0)


SKILL_HALF = 90 * DAY     # a skill not used drifts halfway back to the start in this long


def age_skills(brain, now):
    """Skills unused for a while drift back toward the start: what is not played fades a little.
    Once a lesson, from the time kept in brain["skills_at"]. Only a skill the lessons since then
    did not use drifts, and by the whole time since it was last used: one the last lesson moved
    (its value is no longer the one kept in brain["skills_snap"] when that lesson began) starts
    its time unused from that lesson. brain["skills_aged"] keeps, for each skill, the time it has
    drifted up to. A brain from before these were kept drifts every skill, as it did then."""
    skills = brain.get("skills") if isinstance(brain.get("skills"), dict) else None
    at = brain.get("skills_at")
    brain["skills_at"] = int(now)
    snap = brain.get("skills_snap") if isinstance(brain.get("skills_snap"), dict) else None
    aged = brain.get("skills_aged") if isinstance(brain.get("skills_aged"), dict) else {}
    if skills and isinstance(at, (int, float)) and now > at:
        for name in SKILLS:
            v = skills.get(name)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if snap is not None and snap.get(name) != v:
                aged[name] = at                  # used in the last lesson: unused only since it
                continue
            since = aged.get(name, at)
            since = since if isinstance(since, (int, float)) and since <= now else at
            skills[name] = round(BKT_L0 + (v - BKT_L0) * 0.5 ** ((now - since) / SKILL_HALF), 4)
            aged[name] = int(now)
    brain["skills_aged"] = aged
    brain["skills_snap"] = {k: v for k, v in (skills or {}).items() if k in SKILLS}


def update_skills(skills, names, ok, seen=None, key=None):
    """One attempt at a part: Bayes on what it showed, then the chance of having learned it.
    seen and key: once for each part in a lesson (its first attempt), as the same part tried
    again minutes later is not new evidence about the skill."""
    if seen is not None and key is not None:
        if key in seen:
            return
        seen.add(key)
    for name in names:
        p = skill_level(skills, name)
        if ok:
            post = p * (1 - BKT_S) / (p * (1 - BKT_S) + (1 - p) * BKT_G)
        else:
            post = p * BKT_S / (p * BKT_S + (1 - p) * (1 - BKT_G))
        skills[name] = round(post + (1 - post) * BKT_T, 4)


def update_fluency(skills, hesitant):
    """A part played with the right notes: fluent when none of them came after a hesitation."""
    update_skills(skills, ["fluency"], hesitant == 0)


def weakness(skills, names):
    """0 when the part's weakest skill is the player's own, 1 when none of it is yet."""
    return 1 - min(skill_level(skills, n) for n in names) if names else 0.0


def weakest(skills):
    """The weakest of the skills the player has actually practiced: a skill with no data is not
    a weakness (it would show the start value, 30%), so with none practiced there is nothing to say."""
    if not isinstance(skills, dict):
        return None
    names = [n for n in SKILLS if isinstance(skills.get(n), (int, float)) and not isinstance(skills.get(n), bool)]
    if not names:
        return None
    name = min(names, key=lambda n: skill_level(skills, n))
    return {"skill": name, "level": round(skill_level(skills, name), 2)}


# ---------- 2b. hesitation ----------
HESITATE = 2.0        # a gap this many times the part's usual one is a hesitation
HESITATE_MIN = 0.5    # seconds: a shorter gap is never one
HESITATE_FEW = 1.0    # seconds: the shortest hesitation in a showing with fewer than 4 gaps
EVEN = 1.3            # every gap within this much of the usual one: the part flowed evenly
FLOW_A = 0.3          # how fast a part's remembered usual gap follows a new showing


def gaps(times, file_times=None):
    """The gaps between notes played one after the other: [(gap, seconds)]. With the song's own
    note times, each gap is over the gap in the song, so a long note is not a pause; without
    them it is in seconds. A note not played (None) leaves out its two gaps."""
    out = []
    for i in range(1, len(times)):
        a, b = times[i - 1], times[i]
        if a is None or b is None:
            continue
        sec = b - a
        if file_times is not None:
            fa, fb = file_times[i - 1], file_times[i]
            if fa is None or fb is None or fb - fa < 0.08:
                continue
            out.append((sec / (fb - fa), sec))
        else:
            out.append((sec, sec))
    return out


def hesitations(pairs, flow=None):
    """(hesitations, even, middle gap) of one showing. The usual gap is the player's own for the
    part: the middle gap of this showing, or the part's remembered one when that is longer."""
    if not pairs:
        return 0, False, None
    vals = sorted(v for v, _ in pairs)
    mid = vals[len(vals) // 2]
    ref = max(mid, flow) if isinstance(flow, (int, float)) and flow > 0 else mid
    # few gaps say little about what is usual: then a pause has to be long in seconds too
    floor = HESITATE_MIN if len(pairs) >= 4 else max(HESITATE_MIN, HESITATE_FEW)
    n = sum(1 for v, sec in pairs if v > HESITATE * ref and sec >= floor)
    even = len(pairs) >= 3 and all(v <= EVEN * ref for v, _ in pairs)
    return n, even, mid


def update_flow(part, mid):
    """The part's usual gap, moving part of the way to this showing's middle gap."""
    if not mid or mid <= 0:
        return
    old = part.get("flow")
    part["flow"] = round(mid if not isinstance(old, (int, float)) else (1 - FLOW_A) * old + FLOW_A * mid, 4)


# ---------- 3. pace ----------
PACE_A, PACE_B = 0.4, 0.3       # how fast the level and the trend follow a new run
PACE_PHI = 0.9                  # the trend fades a little each run (a damped trend): no overshoot


def held_speed(hits, starts):
    """The speed of a run as percent of the file: for each two right keys in a row, the seconds in
    the file over the seconds the player took, and the middle one of those. A stop on a wrong key
    is one gap among many, so it no longer counts as slow playing. hits is [(index, time)]."""
    if not starts or len(hits) < 4:
        return None
    ratios, file_total = [], 0.0
    for (k1, t1), (k2, t2) in zip(hits, hits[1:]):
        if k2 != k1 + 1 or k2 >= len(starts) or starts[k1] is None or starts[k2] is None:
            continue
        f, p = starts[k2] - starts[k1], t2 - t1
        if f >= 0.08 and p > 0:
            ratios.append(f / p)
            file_total += f
    if len(ratios) < 3 or file_total < 1.0:
        return None
    ratios.sort()
    return max(20.0, min(150.0, 100 * ratios[len(ratios) // 2]))


def update_pace(pace, held):
    """Holt's smoothing: a level and a trend, each moving part of the way to the new run."""
    if held is None:
        return pace
    if not pace:
        return {"level": round(held, 1), "trend": 0.0, "runs": 1}
    level = PACE_A * held + (1 - PACE_A) * (pace["level"] + PACE_PHI * pace["trend"])
    trend = PACE_B * (level - pace["level"]) + (1 - PACE_B) * PACE_PHI * pace["trend"]
    return {"level": round(level, 1), "trend": round(trend, 2), "runs": pace.get("runs", 0) + 1}


def next_speed(pace):
    """The speed the next run starts at, or None before the song has a run of its own."""
    if not pace or pace.get("runs", 0) < 2:
        return None
    return pace["level"] + PACE_PHI * pace["trend"]


# ---------- 4. steps that keep going wrong ----------
STEP_HALF = 7 * DAY   # an error weighs half as much a week later
STEP_WEAK = 1.5       # the weight that makes a step worth a drill of its own: two errors within a week
STEPS_MAX = 400       # steps remembered, the lightest go first


def step_keys(song, notes, i):
    """The two names of the step from note i-1 to note i: its place in the song, and its two
    notes as pitch classes with the interval between them, which other songs share."""
    a, b = notes[i - 1], notes[i]
    return f"{song}#{i}", f"{a % 12}>{b % 12}{b - a:+d}"


def _steps(brain):
    st = brain.get("transitions")
    if not isinstance(st, dict):
        st = brain["transitions"] = {}
    return st


def step_weight(entry, now):
    """The errors of a step, each weighing less as the weeks pass."""
    if not (isinstance(entry, list) and len(entry) == 2
            and all(isinstance(x, (int, float)) for x in entry)):
        return 0.0
    w, at = entry
    return w * 0.5 ** (max(0.0, now - at) / STEP_HALF)


def step_error(brain, keys, now):
    """A wrong key, or a note that had to be named, at the end of this step."""
    st = _steps(brain)
    for key in keys:
        st[key] = [round(step_weight(st.get(key), now) + 1, 3), int(now)]
    if len(st) > STEPS_MAX:
        for key in sorted(st, key=lambda k: step_weight(st[k], now))[:len(st) - STEPS_MAX]:
            del st[key]


def step_drilled(brain, keys, now):
    """The step came out right when drilled alone: half its weight goes."""
    st = _steps(brain)
    for key in keys:
        if key in st:
            st[key] = [round(step_weight(st[key], now) / 2, 3), int(now)]


def weak_step(brain, song, notes, idx, now):
    """The step into one of these notes (a part's) that went wrong most lately, when it weighs
    STEP_WEAK or more by its place or by its notes; else None."""
    st = brain.get("transitions") if isinstance(brain.get("transitions"), dict) else {}
    best = None
    for i in idx:
        if i <= 0 or i >= len(notes):
            continue
        w = max(step_weight(st.get(k), now) for k in step_keys(song, notes, i))
        if w >= STEP_WEAK and (best is None or w > best[0]):
            best = (w, i)
    return best[1] if best else None


# ---------- 5. fatigue ----------
TIRED_N = 8           # rounds at least, at the start and at the end of a lesson
TIRED_P = 0.02        # how unlikely the rise must be by chance alone (a one-sided Fisher test)
TIRED_EVERY = 3       # rounds at least between two looks (the lesson keeps the count), so many looks
                      # do not add up to a false alarm


def _fisher_rise(a, n1, b, n2):
    """One-sided Fisher exact test: the chance of b or more mistakes in the last n2 rounds, with
    a + b mistakes over n1 + n2 rounds and no change at all between the two."""
    k, n = a + b, n1 + n2
    total = math.comb(n, k)
    return sum(math.comb(n2, x) * math.comb(n1, k - x) for x in range(b, min(k, n2) + 1)) / total


def tired(outcomes, n=TIRED_N):
    """True when the last rounds of a lesson went worse than its first ones beyond what chance
    gives (TIRED_P), the last ones went wrong at least half the time, and the lesson has at least
    2n rounds. A steady lesson, good or hard, rarely calls itself tired. outcomes holds every
    round in order: True for a round with a mistake."""
    if len(outcomes) < 2 * n:
        return False
    m = max(n, len(outcomes) // 3)
    first, last = outcomes[:m], outcomes[-m:]
    a, b = sum(1 for x in first if x), sum(1 for x in last if x)
    if b < m / 2 or b <= a:
        return False
    return _fisher_rise(a, m, b, m) < TIRED_P
