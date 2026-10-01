#!/usr/bin/env python3
"""
piano_hands.py - hand positions and fingers for a melody, the piano's home row.

Each hand rests on 5 white keys, one finger per key, like the fingers on the home row
of a keyboard. The song is split into as few hand positions as possible: a note inside
a hand's 5 keys is played without moving, a note one key past the edge is a stretch of
the edge finger, and anything else is a shift of one hand, named after where its thumb
lands. The split is found by dynamic programming over every pair of positions, so the
number of shifts is the true minimum for the song, not a greedy guess.

Numbering as everywhere else: white key 1 is the leftmost white key of the keyboard.
Fingers: 1 thumb, 2 index, 3 middle, 4 ring, 5 little. The left hand runs 5..1 from left
to right, the right hand 1..5.

  python3 piano_hands.py 10 11 12 13 14 15 16 ...     # white key numbers, 11# for a black
"""
import sys

from piano_numbers import white_number, white_to_midi, nearest_white, is_black, KEYS

SPAN = 5                   # white keys under one resting hand
SHIFT_COST = 1.0           # a hand leaving its place
SHIFT_STEP = 0.02          # plus a little per key travelled, so a short move beats a long one
STRETCH_COST = 0.35        # reaching one key past the edge without moving the hand
HOME_KEY = white_number(60)  # middle C: the right thumb's natural home, used only to break ties

NOTE_NAMES = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
FINGER_NAMES = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "little"}
HAND_NAMES = {"L": "Left", "R": "Right"}
HAND_SHORT = {"L": "L", "R": "R"}


def note_name(white):
    """White key number -> its letter name, C for every C."""
    return NOTE_NAMES[white_to_midi(white) % 12]


def octave_name(midi):
    """MIDI note -> "C4": the name with its octave, middle C is C4."""
    white = white_number(nearest_white(midi))
    sharp = "#" if is_black(midi) else ""
    return f"{note_name(white)}{sharp}{midi // 12 - 1}"


def is_anchor(white):
    """C and F sit right after a gap between black groups, so they are found by touch."""
    return white_to_midi(white) % 12 in (0, 5)


def finger_on(hand, pos, white):
    """The finger resting over a white key, for a hand whose leftmost finger is on pos."""
    offset = white - pos                      # 0..4 inside the hand
    return offset + 1 if hand == "R" else SPAN - offset


def reach(hand, pos, white, black):
    """(finger, stretch) if the hand at pos can play the note without moving, else None.

    A black key is played by the finger over the white on its left, the same white its
    label is written after (11# by the finger over 11), or the one on its right when the
    left one is outside the hand. Black keys are never stretched to.
    """
    inside = lambda w: pos <= w < pos + SPAN
    if black:
        for w in (white, white + 1):
            if inside(w):
                return finger_on(hand, pos, w), False
        return None
    if inside(white):
        return finger_on(hand, pos, white), False
    if white == pos - 1:                      # past the leftmost finger
        return (1 if hand == "R" else 5), True
    if white == pos + SPAN:                   # past the rightmost finger
        return (5 if hand == "R" else 1), True
    return None


def fit_positions(white, black):
    """Every position a hand could shift to so that the note sits under a finger."""
    out = []
    for pos in range(1, KEYS - SPAN + 2):
        got = reach("R", pos, white, black)
        if got and not got[1]:
            out.append(pos)
    return out


def plan_hands(notes):
    """Assigns a hand and a finger to every note of a melody.

    notes are MIDI numbers. Returns a dict:
      fingers  [(hand, finger, stretch)] per note, hand is "L" or "R"
      where    [(left_pos, right_pos)] per note, the leftmost key under each hand
      shifts   [(note index, hand, old_pos, new_pos)]
      stretches [note index]
      opening  (left_pos, right_pos)
    """
    if not notes:
        return None
    keys = [(white_number(nearest_white(n)), is_black(n)) for n in notes]

    # state (L, R): leftmost key of each hand, hands side by side or apart, never crossed
    states = [(l, r) for l in range(1, KEYS - SPAN + 2) for r in range(l + SPAN, KEYS - SPAN + 2)]

    def home_cost(l, r):
        # only breaks ties: right thumb near middle C, hands close together, edges on anchors
        return 0.001 * abs(r - HOME_KEY) + 0.001 * (r - l - SPAN) \
            - 0.0005 * is_anchor(r) - 0.0005 * is_anchor(l + SPAN - 1)

    cost = {s: home_cost(*s) for s in states}
    back = []                                   # per note: state -> (previous state, how)
    for i, (w, black) in enumerate(keys):
        new, how = {}, {}
        fits = fit_positions(w, black)

        def offer(state, c, prev, what):
            if c < new.get(state, float("inf")):
                new[state] = c
                how[state] = (prev, what)

        for (l, r), c in cost.items():
            # 1. played where the hands already are
            for hand, pos in (("L", l), ("R", r)):
                got = reach(hand, pos, w, black)
                if got:
                    offer((l, r), c + (STRETCH_COST if got[1] else 0), (l, r), (hand, got[0], got[1]))
            # 2. one hand moves so the note falls under a finger
            for pos in fits:
                if pos != l and pos + SPAN <= r:
                    offer((pos, r), c + SHIFT_COST + SHIFT_STEP * abs(pos - l), (l, r),
                          ("L", reach("L", pos, w, black)[0], False))
                if pos != r and pos >= l + SPAN:
                    offer((l, pos), c + SHIFT_COST + SHIFT_STEP * abs(pos - r), (l, r),
                          ("R", reach("R", pos, w, black)[0], False))
        if not new:                              # outside the keyboard, cannot happen after best_shift
            raise ValueError(f"note {i + 1} cannot be reached")
        cost = new
        back.append(how)

    state = min(cost, key=cost.get)
    path, fingers = [], []
    for how in reversed(back):
        prev, what = how[state]
        path.append(state)
        fingers.append(what)
        state = prev
    path.reverse()
    fingers.reverse()
    opening = state

    shifts, stretches = [], []
    before = opening
    for i, (l, r) in enumerate(path):
        if l != before[0]:
            shifts.append((i, "L", before[0], l))
        if r != before[1]:
            shifts.append((i, "R", before[1], r))
        if fingers[i][2]:
            stretches.append(i)
        before = (l, r)
    return {"fingers": fingers, "where": path, "shifts": shifts,
            "stretches": stretches, "opening": opening}


def hand_label(f, full=False):
    """("R", 3, False) -> "R3", or "Right 3" in full. A stretch is marked with ↔."""
    hand, finger, stretch = f
    mark = "↔" if stretch else ""
    return f"{HAND_NAMES[hand]} {finger}{mark}" if full else f"{HAND_SHORT[hand]}{finger}{mark}"


def thumb_key(hand, pos):
    """The white key under the thumb of a hand whose leftmost finger is on pos."""
    return pos if hand == "R" else pos + SPAN - 1


def shift_text(hand, new_pos, numbers=True):
    """Right hand moves: thumb to G (18)."""
    thumb = thumb_key(hand, new_pos)
    num = f" ({thumb})" if numbers else ""
    return f"{HAND_NAMES[hand]} hand moves: thumb to {note_name(thumb)}{num}"


def stretch_text(f, white, numbers=True):
    hand, finger, _ = f
    num = f" ({white})" if numbers else ""
    return f"{HAND_NAMES[hand]} {finger} stretches to {note_name(white)}{num}"


def position_text(hand, pos, numbers=True):
    """Left 10-14: thumb on B, little finger on E."""
    lo, hi = pos, pos + SPAN - 1
    little, thumb = (hi, lo) if hand == "R" else (lo, hi)
    rng = f" {lo}-{hi}" if numbers else ""
    return (f"{HAND_NAMES[hand]}{rng}: thumb on {note_name(thumb)}, "
            f"little finger on {note_name(little)}")


def events_between(plan, first, last):
    """Shifts and stretches that happen on notes first..last, in order, as (index, kind, data)."""
    out = [(i, "shift", (hand, new)) for i, hand, _, new in plan["shifts"] if first <= i <= last]
    out += [(i, "stretch", None) for i in plan["stretches"] if first <= i <= last]
    return sorted(out, key=lambda e: e[0])


def parse_keys(tokens):
    """ "11#" -> MIDI of the black key right of white 11."""
    out = []
    for t in tokens:
        black = t.endswith("#")
        out.append(white_to_midi(int(t.rstrip("#"))) + (1 if black else 0))
    return out


if __name__ == "__main__":
    midi = parse_keys(sys.argv[1:])
    p = plan_hands(midi)
    l, r = p["opening"]
    print(position_text("L", l))
    print(position_text("R", r))
    print(" ".join(hand_label(f) for f in p["fingers"]))
    for i, hand, _, new in p["shifts"]:
        print(f"note {i + 1}: {shift_text(hand, new)}")
    for i in p["stretches"]:
        print(f"note {i + 1}: {stretch_text(p['fingers'][i], white_number(nearest_white(midi[i])))}")
