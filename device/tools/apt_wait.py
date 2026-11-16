"""ExecCondition of apt's daily units on the device (install.sh --device): exit 1 (skip this turn) while a
lesson runs, 0 (go on) otherwise. When the lesson engine does not answer, apt goes on as usual: the updates are
never held back by Armonico being off or broken."""
import json
import os
import sys
import urllib.request

port = os.environ.get("LESSON_UI_PORT", "8099")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=5) as r:
        state = json.load(r)
except Exception:
    sys.exit(0)
sys.exit(0 if not isinstance(state, dict) or state.get("mode", "idle") == "idle" else 1)
