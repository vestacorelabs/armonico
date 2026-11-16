"""The phone's MQTT topics: pianoAndroid/, so a phone, a big piano and an Armonico Pi on one broker are three
pianos. The Telegram commands are the same on all of them; a word after one names the piano (/pianoplay
android ode_to_joy), and the phone's answers name it so. The rule is lessons/piano_topics.py, copied in at
build time; the engine in the app gets ARMONICO_ANDROID='yes' (lessons.py) and follows it too."""
try:
    import piano_topics                 # copied in from lessons/ at build time
except Exception:                       # the tests run it from the repository
    import os
    import sys
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 6, "lessons"))
    import piano_topics

ANDROID = {"ARMONICO_ANDROID": "yes"}
TOPICS = piano_topics.root(ANDROID)     # pianoAndroid
WORD = piano_topics.target(ANDROID)     # android


def for_me(args):
    """(True, the rest) when a command is the phone's: it names no piano, the phone, or "piano" (the app's
    own bot reaches the phone only). (False, ...) when it names another piano (device)."""
    word, rest = piano_topics.split_target(args)
    return word in ("", WORD, "piano"), rest


def commands_in(text):
    return piano_topics.commands_in(text, ANDROID)
