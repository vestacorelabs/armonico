"""light.py - the screen's brightness: the day level, and the night level while night mode is active.

The levels are drawn: a dark layer over the whole frame, on every screen. The backlight in
/sys/class/backlight is only ever set fully on, or off while the screen sleeps at night (it
level 0). Writing a level in between turned the PiTFT50 V2.1 dark altogether on the device
(its board takes the value as on or off, whatever its max_brightness says), and a screen left dark
cannot be set back from itself. Fully on at every start, so a restart always brings the light back.
Writing the backlight needs root: the root unit does it (root_actions.py set-brightness).
"""
import glob
import os
import threading
import time

BACKLIGHT = "/sys/class/backlight"
DAY_LEVELS = (100, 75, 50, 25)
# night mode's 3 strengths: (screen brightness %, volume as a share of the day's %), both dropped together.
# The volume is cubed on its way to the speaker (armonico_pi.loudness), so a fixed cap of 20% was -42 dB: on AUX
# and on Bluetooth night mode sounded like no sound at all. A share of the day's volume is about -4, -7 and -11 dB.
NIGHT_STRENGTH = ((75, 85), (50, 75), (25, 65))
NIGHT_FLOOR = 35        # night mode never takes the volume below this (cubed, about -27 dB); a lower day volume stays as it is


def night_volume(day, share):
    """The volume while night mode is active: the day's volume times the strength's share, never under NIGHT_FLOOR,
    and never above the day's own."""
    day = max(0, min(100, int(day)))
    if day <= NIGHT_FLOOR:
        return day
    return max(NIGHT_FLOOR, round(day * share / 100))
NIGHT_WHEN = ("sun", "hours", "off")  # the older single choice: sunset to sunrise, 2 chosen times, or never
NIGHT_AUTO = ("sun", "hours", "manual")  # what turns night mode on while its switch is on: sunset to sunrise, 2 chosen times, or only the switch


def backlights(root=BACKLIGHT):
    """[(path, max)] of the backlights the system shows, readable without root."""
    found = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        try:
            top = int(open(os.path.join(d, "max_brightness")).read().strip())
        except (OSError, ValueError):
            continue
        if top > 0 and os.path.exists(os.path.join(d, "brightness")):
            found.append((d, top))
    return found


class Light:
    """Follows the wanted level and changes the backlight only when it changes, so the screen's own
    buttons keep working until the next change (sunset, sunrise, a new choice in the settings)."""

    def __init__(self, request, root=BACKLIGHT):
        self.request, self.root = request, root
        self.has = bool(backlights(root))
        self.applied = None
        self.busy = False
        self.fails, self.wait_until = 0, 0.0

    def want(self, pct):
        """pct 0..100. Returns the darkening for the drawing (0..1); the backlight goes off only at 0."""
        pct = max(0, min(100, int(pct)))
        dim = min(0.85, (100 - pct) / 100) if 0 < pct < 100 else 0.0
        if not self.has:
            return 0.85 if pct == 0 else dim
        pct = 100 if pct > 0 else 0              # the backlight: fully on, or off
        if pct != self.applied and not self.busy and time.monotonic() >= self.wait_until:
            self.busy, self.applied = True, pct

            def go():
                try:
                    ok, _ = self.request("set-brightness", pct=pct, timeout=30)
                    if ok:
                        self.fails = 0
                    else:
                        # tried again later, waiting longer each time (30 s, 1, 2, 4 minutes, then 10):
                        # every try starts the root unit, and other requests wait behind it
                        self.fails += 1
                        self.wait_until = time.monotonic() + min(600, 30 * 2 ** (self.fails - 1))
                        self.applied = None
                finally:
                    self.busy = False
            threading.Thread(target=go, daemon=True).start()
        return dim
