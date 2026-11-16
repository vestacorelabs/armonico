#!/bin/sh
# window.sh - the Armonico Pi screen as a window, on a machine that is not the Armonico Pi: a Linux
# container on Android (in Termux:X11) or a regular Linux machine (on its desktop). install.sh --device
# found which, and 'armonico screen' runs this from the user's own graphical session.
#
# The screen program runs as the service user, as on the Pi, so it reads and writes the device's own
# files. It draws on this user's X display, which xhost opens to that one user (a desktop on Wayland has
# XWayland for it). An 800x480 window, the size of the Armonico Pi's screen.

OPT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$(basename "$OPT")"
VAR="/var/lib/$APP"
PY="$OPT/device-venv/bin/python"
SCREEN="$OPT/device/screen.py"

# From SSH or adb, with no DISPLAY of its own: the first X display running on this machine (Termux:X11 shares
# its socket with the container as display :0, and a desktop is usually :0 too)
if [ -z "${DISPLAY:-}" ]; then
    for s in /tmp/.X11-unix/X*; do
        [ -S "$s" ] && { DISPLAY=":${s##*/X}"; export DISPLAY; break; }
    done
fi
if [ -z "${DISPLAY:-}" ]; then
    echo "No display found. On Android open the Termux:X11 app first; on a PC log in to its desktop, or run this from a terminal there." >&2
    exit 1
fi
if command -v xhost >/dev/null 2>&1; then
    xhost "+SI:localuser:$APP" >/dev/null 2>&1 || echo "xhost could not open the display to $APP: the window may not open" >&2
fi
echo "The screen opens in a window. Close it, or press Ctrl+C here, to stop."
if [ "$(id -un)" = "$APP" ]; then RUN=""; else RUN="sudo -u $APP"; fi
# shellcheck disable=SC2086  # RUN is empty or "sudo -u <user>", split on purpose
exec $RUN env DISPLAY="$DISPLAY" SDL_VIDEODRIVER=x11 ARMONICO_SCREEN_SIZE=800x480 \
    DATA_DIR="$VAR" PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    XDG_RUNTIME_DIR="/run/user/$(id -u "$APP")" \
    sh -c 'set -a; [ -r "/etc/'"$APP"'/config.env" ] && . "/etc/'"$APP"'/config.env"; set +a; cd "'"$VAR"'" 2>/dev/null; exec "'"$PY"'" "'"$SCREEN"'"'
