"""screensize.py - the physical size class of the connected screen: 3.5", 4.3", 5", 7" or 10".

The class comes from the screen's own size in millimetres (the EDID the display driver publishes under
/sys/class/drm), because a 5" and a 7" panel can both be 800x480. When the panel reports none (a DSI
panel often does not), the resolution decides. ARMONICO_SCREEN_INCH forces a class, for testing.
The plan stays 800x480 and is scaled to the screen's pixels by the canvas; the class sets how many
cards a settings page holds and how large the text is drawn in them."""
import glob
import math
import os

CLASSES = (3.5, 4.3, 5.0, 7.0, 10.0)

# class: (columns, rows, text factor). Cards per page = columns x rows.
LAYOUT = {3.5: (1, 4, 1.18), 4.3: (2, 3, 1.1), 5.0: (2, 4, 1.0), 7.0: (3, 4, 1.0), 10.0: (3, 5, 1.0)}


def classify(inch):
    """The nearest class to a diagonal in inches (the bounds sit between the classes)."""
    if inch < 3.9:
        return 3.5
    if inch < 4.65:
        return 4.3
    if inch < 6.0:
        return 5.0
    if inch < 8.5:
        return 7.0
    return 10.0


def _edid_mm(raw):
    if len(raw) < 23 or raw[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    w, h = raw[21] * 10, raw[22] * 10                  # centimetres in the base block
    if len(raw) >= 72 and raw[54] | raw[55]:           # the first detailed timing has it in millimetres
        w, h = raw[66] | ((raw[68] & 0xF0) << 4), raw[67] | ((raw[68] & 0x0F) << 8)
    return (w, h) if w > 0 and h > 0 else None


INTERNAL = ("DSI", "DPI", "EDP", "LVDS")


def drm_inches(root="/sys/class/drm"):
    """The diagonal in inches of the device's own panel (a DSI, DPI, eDP or LVDS connector) that reports its size,
    or None. An HDMI monitor or any other external connector is ignored: the device is built around its panel."""
    for d in sorted(glob.glob(os.path.join(root, "card*-*"))):
        kind = os.path.basename(d).split("-", 1)[1].upper()
        if not kind.startswith(INTERNAL):
            continue
        try:
            if open(os.path.join(d, "status")).read().strip() != "connected":
                continue
            mm = _edid_mm(open(os.path.join(d, "edid"), "rb").read())
        except OSError:
            continue
        if mm:
            return math.hypot(*mm) / 25.4
    return None


def by_resolution(w, h):
    """The class when the screen reports no size: the usual panel for each resolution."""
    px = max(w, h)
    if px <= 480:
        return 3.5
    if px <= 800:
        return 5.0
    if px <= 1024:
        return 7.0
    return 10.0


def detect(w=800, h=480, env=None, root="/sys/class/drm"):
    v = (os.environ if env is None else env).get("ARMONICO_SCREEN_INCH", "").strip()
    try:
        if v:
            return classify(float(v))
    except ValueError:
        pass
    inch = drm_inches(root)
    return classify(inch) if inch else by_resolution(w, h)


def layout(cls):
    """(columns, rows, text factor) of a class."""
    return LAYOUT.get(cls, LAYOUT[5.0])
