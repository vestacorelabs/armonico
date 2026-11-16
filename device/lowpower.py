"""Whether this Pi is one of the slower ones (a Pi 3 or older, or any with about 1 GB of memory).

The device runs the same program on every Pi. On a slower one some things are lighter: the other voices keep
less in memory and the live view takes fewer frames (the rest video is 800x480 on every Pi). The screen stays
at 30 frames a second everywhere. ARMONICO_LOWPOWER=1 forces it on, 0 forces it off.
"""
import os
import re

_cache = {}

LOW_MEM_MB = 1500       # a 1 GB Pi reports about 900 MB; a 2 GB Pi about 1800 MB


def model():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode(errors="replace").strip("\0\n ")
    except OSError:
        return ""


def mem_mb():
    try:
        with open("/proc/meminfo") as f:
            m = re.search(r"^MemTotal:\s+(\d+)", f.read(), re.M)
        return int(m.group(1)) // 1024 if m else 0
    except OSError:
        return 0


def detect(env=None, model_text=None, memory_mb=None):
    """True on a Pi older than the Pi 4 (3, 2, Zero, Zero 2, 1) or with less than LOW_MEM_MB of memory.
    A computer that is not a Pi (no model text) is judged by its memory alone, and only when that is known."""
    env = os.environ if env is None else env
    v = str(env.get("ARMONICO_LOWPOWER", "")).strip().lower()
    if v in ("1", "yes", "on", "true"):
        return True
    if v in ("0", "no", "off", "false"):
        return False
    text = model() if model_text is None else model_text
    mb = mem_mb() if memory_mb is None else memory_mb
    m = re.search(r"Raspberry Pi (\d+)\b", text)
    if m and int(m.group(1)) < 4:
        return True
    if re.search(r"Raspberry Pi (Zero|Model [AB])", text):
        return True
    return 0 < mb < LOW_MEM_MB


def is_low():
    if "v" not in _cache:
        _cache["v"] = detect()
    return _cache["v"]
