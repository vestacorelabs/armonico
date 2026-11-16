"""piano_topics.py - the root of every MQTT topic, in one place.

A regular install talks on piano/ (piano/notes, piano/play, piano/status ...). The Armonico Pi
(install.sh --device, DEVICE='yes' in config.env) talks on pianoDevice/ instead, so a big piano
and the device on the same broker are two pianos: a command for one never reaches the other,
and Home Assistant keeps each one's sensors apart.

The shell side reads the same setting: bridge/piano_bridge.sh, bridge/piano_reset.sh, install.sh.
The Android app is a third piano: pianoAndroid/ (android/app/src/main/python/armonico_android).
"""
import os
import re
from pathlib import Path

PIANO = "piano"
DEVICE = "pianoDevice"
ANDROID = "pianoAndroid"
# Telegram has one list of commands for every piano (/pianostatus, /lesson ...). A word after the command
# names the piano when Home Assistant knows more than one: /pianoplay device ode_to_joy is for the Armonico
# Pi, /pianostatus android for the phone. Without it the command goes to the one Home Assistant has
# (the big piano first). Windows will get a root of its own (pianoWindows) when it is built.
TARGET = {PIANO: "piano", DEVICE: "device", ANDROID: "android"}
# /opt/<app>/lessons/piano_topics.py: the settings are in /etc/<app>/config.env
CONFIG = Path("/etc") / Path(__file__).resolve().parent.parent.name / "config.env"


def _from_config():
    """DEVICE from config.env, for a program the services did not start (a terminal, sudo)."""
    try:
        text = CONFIG.read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.findall(r"^DEVICE=['\"]?([A-Za-z]*)['\"]?\s*$", text, re.M)
    return found[-1] if found else ""


def root(settings=None):
    """piano, pianoDevice or pianoAndroid. DEVICE='yes' (install.sh --device) is the Armonico Pi,
    ARMONICO_ANDROID='yes' (set by the Android app) the phone. From a dict of config.env when one is
    given, otherwise the environment (the services read config.env into it), otherwise config.env."""
    s = os.environ if settings is None else settings
    if (s.get("ARMONICO_ANDROID") or "").strip() == "yes":
        return ANDROID
    if settings is not None:
        value = settings.get("DEVICE") or ""
    elif "DEVICE" in os.environ:
        value = os.environ["DEVICE"]
    else:
        value = _from_config()
    return DEVICE if value.strip() == "yes" else PIANO


def is_device(settings=None):
    return root(settings) == DEVICE


# The Telegram commands, the same on every piano
COMMANDS = ("pianostatus", "pianoplay", "pianosongs", "pianostop", "pianoreset", "pianoprofile", "pianocode",
            "showlog", "stoplog", "seqlist", "seqlearn", "seqadd", "seqdel", "seqcancel",
            "lesson", "lessons", "lessonstop", "getmidi")
_CMD = re.compile(r"(?<![\w/])/(" + "|".join(sorted(COMMANDS, key=len, reverse=True)) + r")(?![\w])")


def target(settings=None):
    """The word that names this piano after a command: "" on a regular install, device or android."""
    top = root(settings)
    return "" if top == PIANO else TARGET[top]


def commands_in(text, settings=None):
    """The Telegram commands named in an answer, with this piano's name after them, so the command
    copied from it comes back here: /pianoplay becomes /pianoplay device on the Armonico Pi."""
    word = target(settings)
    if not word or "/" not in text:
        return text
    return _CMD.sub(lambda m: f"/{m.group(1)} {word}", text)


def split_target(args):
    """("device", "ode_to_joy") from "device ode_to_joy"; ("", args) when it names no piano."""
    first, _, rest = (args or "").strip().partition(" ")
    if first.lower() in TARGET.values():
        return first.lower(), rest.strip()
    return "", (args or "").strip()
