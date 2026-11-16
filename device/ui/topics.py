"""The root of the MQTT topics for the device's screens: pianoDevice on the Armonico Pi.
The one rule is lessons/piano_topics.py; this only finds it."""
import sys
from pathlib import Path

_LESSONS = str(Path(__file__).resolve().parent.parent.parent / "lessons")
if _LESSONS not in sys.path:
    sys.path.append(_LESSONS)

import piano_topics  # noqa: E402

TOPICS = piano_topics.root()
