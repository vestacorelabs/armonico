#!/bin/sh
# session.sh - the Armonico Pi screen's login session, run by greetd as the screen user
# (system/screen.service, device/greetd.toml). greetd starts it again whenever it ends: after a
# change of screens or of the screen choice, the program ends on purpose and this decides again.
#
# One screen (the one chosen, or the one connected): the program draws straight on KMS, sharp at
# the screen's own resolution, with no compositor. A big HDMI screen alone, with no DSI connected (wider than 1280), runs under labwc
# instead, as "both" does: there the rest video goes to a window of its own and the GPU stretches it
# (screen.VideoWindow), where on KMS the CPU scaled and copied every 1920x1080 frame (12 to 15 a second on a
# Pi 4). ARMONICO_GPU_VIDEO=0 in config.env keeps it on KMS.
# Both screens, when "both" is chosen and both are connected: labwc lights them, the program's
# window fills the device's own screen and a second window shows the same picture on the other one,
# stretched to fill it (wl-mirror, fitted with bars, if that window cannot open).
# If the window has not opened under labwc after 25 seconds, the program draws on KMS instead.
# Everything goes to $VAR/device/session.log, which armonico-pi-report collects.
#
# HDMI off (DSI only): the device runs on its own DSI screen, the BIGTREETECH PiTFT50 the case is
# built for. An HDMI screen is never looked for or lit, so the plan is always one screen on KMS.
# The HDMI parts below are kept as comments, marked "HDMI off (DSI only)".

OPT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$(basename "$OPT")"
VAR="/var/lib/$APP"
LOG="$VAR/device/session.log"
PY="$OPT/device-venv/bin/python"
SCREEN="$OPT/device/screen.py"

set -a
[ -r "/etc/$APP/config.env" ] && . "/etc/$APP/config.env"
set +a
export DATA_DIR="$VAR" PYTHONUNBUFFERED=1 ARMONICO_SESSION=1
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
READY="$XDG_RUNTIME_DIR/$APP-screen.ready"
export ARMONICO_READY_FILE="$READY"
cd "$VAR" 2>/dev/null || cd /

[ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"
exec >>"$LOG" 2>&1
# What the last session may have left behind still holds the screens (DRM): a labwc or a wl-mirror
# from "both", or a screen program that has not finished going. Then the next one, lit straight on
# KMS, could not take the DSI, which stayed dark until a reboot.
if pgrep -u "$(id -u)" -x labwc >/dev/null || pgrep -u "$(id -u)" -x wl-mirror >/dev/null || pgrep -u "$(id -u)" -f "$SCREEN" >/dev/null; then
    echo "=== $(date '+%T') ending what the last session left: $(pgrep -u "$(id -u)" -a -f "labwc|wl-mirror|$SCREEN" | tr '\n' ' ')"
    pkill -u "$(id -u)" -x labwc; pkill -u "$(id -u)" -x wl-mirror; pkill -u "$(id -u)" -f "$SCREEN"
    sleep 1
    pkill -9 -u "$(id -u)" -x labwc; pkill -9 -u "$(id -u)" -x wl-mirror; pkill -9 -u "$(id -u)" -f "$SCREEN"
fi
# The plan, worked out here in the shell (a Python start costs a second or two on a Pi 4): "both" only
# when both is chosen and a DSI and an HDMI screen are connected
# shellcheck disable=SC2034  # read for the HDMI code below, which is commented out (DSI only)
PICK="$(grep -o '"pick": *"[^"]*"' "$VAR/device/settings.json" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
screens() {
    DSI="" HDMI=""
    for f in /sys/class/drm/card*-*/status; do
        [ "$(cat "$f" 2>/dev/null)" = connected ] || continue
        n="$(basename "$(dirname "$f")")"; n="${n#card*-}"
        # case "$n" in DSI*) [ -z "$DSI" ] && DSI="$n" ;; HDMI*) [ -z "$HDMI" ] && HDMI="$n" ;; esac    # HDMI off (DSI only)
        case "$n" in DSI*) [ -z "$DSI" ] && DSI="$n" ;; esac
    done
}
screens
# Right after boot one screen can show up a few seconds after the other (the DSI panel is found late):
# with "both" chosen, wait up to 8 seconds for the second before lighting one alone. Without this the
# Pi came up on HDMI only after a reboot, though both screens were chosen and connected.
# HDMI off (DSI only):
# if [ "$PICK" = both ] && { [ -z "$DSI" ] || [ -z "$HDMI" ]; }; then
#     i=0
#     while [ $i -lt 16 ] && { [ -z "$DSI" ] || [ -z "$HDMI" ]; }; do
#         sleep 0.5; screens; i=$((i + 1))
#     done
#     echo "=== waited $((i / 2)) s for both screens: DSI '${DSI}' HDMI '${HDMI}'"
# fi
# the width of the HDMI screen's preferred mode (the first line of its modes), 0 when unknown
hdmi_width() {
    w="$(head -n 1 /sys/class/drm/card*-"$HDMI"/modes 2>/dev/null | head -n 1 | cut -d x -f 1)"
    case "$w" in ''|*[!0-9]*) echo 0 ;; *) echo "$w" ;; esac
}
# HDMI off (DSI only):
# if [ "$PICK" = both ] && [ -n "$DSI" ] && [ -n "$HDMI" ]; then
#     PLAN="both $DSI $HDMI"
# elif [ -n "$HDMI" ] && [ -z "$DSI" ] \
#         && [ "${ARMONICO_GPU_VIDEO:-1}" != 0 ] && [ "$(hdmi_width)" -gt 1280 ]; then
#     # only with the HDMI screen alone: with the DSI connected too, everything stays as it was (both, or one on KMS)
#     PLAN="one $HDMI"
# else
#     PLAN="kms"
# fi
PLAN="kms"
echo "=== $(date '+%F %T') session on $(tty 2>/dev/null) · seat ${XDG_SEAT:-?} · plan: $PLAN"
rm -f "$READY"

# No screen: the program counts each time it could not open one (screen.py, no_display) and ends. A DSI
# panel cannot be connected while the Pi is on, so after NO_DISPLAY_TRIES misses in a row, and once the
# boot is NO_DISPLAY_AFTER seconds behind (a panel found late at boot still gets its chance), the
# session waits here doing nothing instead of starting the program again every few seconds (about half
# a core). The count lives in /run/user, so a reboot tries again; so does deleting the file.
# The keys, the sound and MQTT are another service (armonico_pi.py) and go on.
NO_DISPLAY="$XDG_RUNTIME_DIR/$APP-screen.nodisplay"
NO_DISPLAY_TRIES=3
NO_DISPLAY_AFTER=90
misses="$(cat "$NO_DISPLAY" 2>/dev/null)"
case "$misses" in ''|*[!0-9]*) misses=0 ;; esac
up="$(cut -d . -f 1 /proc/uptime 2>/dev/null)"
case "$up" in ''|*[!0-9]*) up=0 ;; esac
if [ "$misses" -ge "$NO_DISPLAY_TRIES" ] && [ "$up" -ge "$NO_DISPLAY_AFTER" ]; then
    echo "=== $(date '+%T') no screen: it did not open $misses times in a row. The screen program is not started again until a reboot (or until $NO_DISPLAY is deleted and the screen service restarted). Keys, sound and MQTT go on"
    exec sleep infinity
fi

kms() {
    SDL_VIDEODRIVER=kmsdrm exec "$PY" "$SCREEN"
}

set -- $PLAN
case "$1" in both|one) ;; *) kms ;; esac

# under labwc: the program on $2 and, for both screens, its mirror on $3 (one big HDMI screen: no mirror)
LABWC_DIR="$XDG_RUNTIME_DIR/$APP-labwc"
"$PY" "$SCREEN" --labwc-config "$LABWC_DIR" "$2" "${3:-}" || kms
export ARMONICO_SCREEN_OUTPUT="$2"     # the program's screen by name: the video player (mpv) goes full screen there
if [ "$1" = both ]; then
    export ARMONICO_MIRROR="$3"        # the program opens a second window there, the picture stretched to fill it
fi
# the window's app_id, which the window rule matches (SDL 2.32 reads SDL_APP_ID, older ones the second)
export SDL_APP_ID=armonico-screen SDL_VIDEO_WAYLAND_WMCLASS=armonico-screen
echo "=== $(labwc -v 2>/dev/null)"
SDL_VIDEODRIVER=wayland labwc -C "$LABWC_DIR" -S "$PY $SCREEN" &
LABWC=$!
i=0
while [ $i -lt 25 ]; do
    if [ -e "$READY" ]; then
        echo "=== $(date '+%T') up under labwc ($1)"
        wait "$LABWC"
        echo "=== $(date '+%T') labwc ended ($?)"
        exit 0
    fi
    kill -0 "$LABWC" 2>/dev/null || break
    sleep 1
    i=$((i + 1))
done

echo "=== $(date '+%T') no window under labwc after ${i} s: one screen, straight on KMS"
kill "$LABWC" 2>/dev/null
sleep 1
kill -9 "$LABWC" 2>/dev/null
pkill -9 -u "$(id -u)" -f "$SCREEN" 2>/dev/null
pkill -9 -u "$(id -u)" wl-mirror 2>/dev/null
unset ARMONICO_MIRROR ARMONICO_SCREEN_OUTPUT
sleep 1
kms
