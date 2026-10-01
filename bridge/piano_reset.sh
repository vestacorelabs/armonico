#!/bin/bash
# piano_reset.sh - the full reset of the piano connection, for a keyboard that unstick cannot bring back.
# Runs as root. By hand: sudo armonico reset
# From Telegram (/pianoreset): the bridge writes <data folder>/reset-request and the root reset
# service starts this script, because the bridge itself has no right to touch USB.
#
# In order:
#   1. stops the bridge, the lesson engine and the recorder, so nothing holds the MIDI port
#   2. piano_unstick.sh: closes an open SysEx, GM reset, controllers and notes off on 16 channels
#   3. a USB reset of the keyboard: the device is switched off and on, as if the cable was pulled
#   4. waits for the keyboard to come back, then runs unstick once more
#   5. restarts the local MQTT broker (only when this program installed it), then every service
#   6. answers the Telegram chat that asked, if there was one
set -Euo pipefail

APP_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
APP="$(basename "$APP_DIR")"
CONFIG="/etc/$APP/config.env"
DATA="/var/lib/$APP"
# Only the values this script needs, read as text: the file is never run as root
cfg_get() {
    sed -n "s/^$1='\\(.*\\)'$/\\1/p" "$CONFIG" 2>/dev/null | tail -1
}
CFG_DATA="$(cfg_get DATA_DIR)"
case "$CFG_DATA" in /*) DATA="$CFG_DATA" ;; esac
NAME="$(cfg_get MIDI_DEVICE)"
REQ="$DATA/reset-request"
LOG="$DATA/reset.log"
CHAT="$(head -c 40 "$REQ" 2>/dev/null | tr -cd '0-9-')"
rm -f "$REQ"
# one reset at a time, a second request while this runs is dropped
exec 9>"/run/$APP-reset.lock"
flock -n 9 || exit 0

# The data folder belongs to the service user, so reset.log there can be a link this script did
# not make, and an append as root would follow it to any file on the machine. This run is logged
# in a folder of root's own, and on the way out the earlier log (read without following a link)
# and this run go back in place through install, which replaces whatever is at the name.
OWN="$(mktemp -d)"
publish_log() {
    local owner
    owner="$(stat -c '%u:%g' "$DATA" 2>/dev/null || echo 0:0)"
    { dd if="$LOG" iflag=nofollow,nonblock status=none 2>/dev/null | tail -n 2000; cat "$OWN/reset.log" 2>/dev/null; } \
        > "$OWN/all.log" 2>/dev/null
    install -T -m 0644 -o "${owner%:*}" -g "${owner#*:}" "$OWN/all.log" "$LOG" 2>/dev/null || true
    rm -rf "$OWN"
}
trap publish_log EXIT
say() { echo "$*"; echo "$(date '+%F %T') $*" >> "$OWN/reset.log" 2>/dev/null || true; }
SERVICES=("$APP-bridge" "$APP-lessons")
systemctl is-enabled --quiet "$APP-recorder" 2>/dev/null && SERVICES+=("$APP-recorder")

reply() {   # reply TEXT: to the chat that asked, through the broker
    [ -n "$CHAT" ] || return 0
    local host port user pass args
    host="$(cfg_get MQTT_HOST)"; port="$(cfg_get MQTT_PORT)"; user="$(cfg_get MQTT_USER)"; pass="$(cfg_get MQTT_PASS)"
    [ -n "$host" ] || return 0
    args=(-h "$host" -p "${port:-1883}")
    [ -n "$user" ] && args+=(-u "$user" -P "$pass")
    mosquitto_pub "${args[@]}" -t "piano/shortcut/reply" -m "$CHAT|$1" 2>/dev/null || true
}
lang_he() { grep -q '"lang": *"he"' "$DATA/settings.json" 2>/dev/null; }
L() { if lang_he; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# the USB device of the keyboard: the sound card's sysfs node, then up to the device that has idVendor
usb_dev() {
    local line card p
    line="$(amidi -l 2>/dev/null | awk -v n="$NAME" 'index($0, n) {print $2; exit}')"
    [ -n "$line" ] || return 1
    card="${line#hw:}"; card="${card%%,*}"
    p="$(readlink -f "/sys/class/sound/card$card/device" 2>/dev/null)" || return 1
    while [ -n "$p" ] && [ "$p" != "/" ] && [ "$p" != "/sys" ]; do
        [ -e "$p/idVendor" ] && [ -w "$p/authorized" ] && { echo "$p"; return 0; }
        p="$(dirname "$p")"
    done
    return 1
}

say "=== full reset ==="
[ "$(id -u)" -eq 0 ] || { echo "Run it as root: sudo armonico reset"; exit 1; }
reply "$(L "🔄 Full reset started. It takes about 30 seconds" "🔄 איפוס מלא התחיל. זה לוקח בערך 30 שניות")"

# the USB device is found before the services stop: it is read from the card that is still there
USB=""
[ -n "$NAME" ] && USB="$(usb_dev || true)"
say "1. stopping ${SERVICES[*]}"
systemctl stop "${SERVICES[@]}" 2>/dev/null || true
pkill -f "aseqdump -p"  2>/dev/null || true
pkill -f "aplaymidi -p" 2>/dev/null || true
sleep 1

if [ -n "$NAME" ]; then
    say "2. unstick"
    timeout 30 "$APP_DIR/bridge/piano_unstick.sh" >> "$OWN/reset.log" 2>&1 || say "   unstick did not finish (the keyboard may be off the bus)"
else
    say "2. unstick skipped: MIDI_DEVICE is not set"
fi

if [ -n "$USB" ]; then
    say "3. USB reset of $USB"
    echo 0 > "$USB/authorized" 2>/dev/null || say "   could not switch the device off"
    sleep 2
    echo 1 > "$USB/authorized" 2>/dev/null || say "   could not switch the device on"
else
    say "3. no USB device found for the keyboard, reloading the sound USB driver instead"
    modprobe -r snd_usb_audio 2>/dev/null || say "   the driver is in use by another device, left as it is"
    sleep 1
    modprobe snd_usb_audio 2>/dev/null || true
fi

say "4. waiting for the keyboard"
BACK=no
for _ in $(seq 1 30); do
    if [ -n "$NAME" ] && amidi -l 2>/dev/null | grep -qF "$NAME"; then BACK=yes; break; fi
    [ -z "$NAME" ] && { BACK=yes; break; }
    sleep 1
done
if [ "$BACK" = yes ] && [ -n "$NAME" ]; then
    sleep 1
    timeout 30 "$APP_DIR/bridge/piano_unstick.sh" >> "$OWN/reset.log" 2>&1 || true
fi
say "   keyboard back: $BACK"

if [ "$(cfg_get MQTT_LOCAL)" = yes ] && systemctl is-active --quiet mosquitto 2>/dev/null; then
    say "5. restarting the MQTT broker"
    systemctl restart mosquitto 2>/dev/null || say "   the broker did not restart"
    sleep 2
fi
say "5. starting ${SERVICES[*]}"
systemctl reset-failed "${SERVICES[@]}" 2>/dev/null || true
systemctl restart "${SERVICES[@]}" 2>/dev/null || true
sleep 3
BAD=""
for s in "${SERVICES[@]}"; do systemctl is-active --quiet "$s" || BAD+=" $s"; done

if [ -z "$BAD" ] && [ "$BACK" = yes ]; then
    say "✅ full reset done"
    reply "$(L "✅ Full reset done. The keyboard is back and the services run" "✅ האיפוס המלא הסתיים. המקלדת חזרה והשירותים רצים")"
elif [ "$BACK" != yes ]; then
    say "❌ the keyboard did not come back"
    reply "$(L "❌ Reset done, but the keyboard did not come back. Check the cable and that it is switched on" "❌ האיפוס הסתיים, אבל המקלדת לא חזרה. צריך לבדוק כבל ושהיא דלוקה")"
    exit 3
else
    say "❌ not running:$BAD"
    reply "$(L "❌ Reset done, but these did not start:$BAD" "❌ האיפוס הסתיים, אבל אלה לא עלו:$BAD")"
    exit 4
fi
