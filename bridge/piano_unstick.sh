#!/bin/bash
# piano_unstick.sh - brings a silent keyboard back without unplugging it, and shows what was stuck.
# Run it when the keyboard is connected but plays nothing:
#   sudo /opt/<name>/bridge/piano_unstick.sh
#
# Each step rules out a different cause:
#   1. the byte counters of the MIDI port. If they grow while a song plays,
#      the data does leave the computer
#   2. a lone F7: closes a SysEx message that was opened and never closed.
#      A keyboard in the middle of a SysEx ignores everything else
#   3. GM System On: puts every setting back to the General MIDI default
#   4. on all 16 channels: reset controllers, volume 100, full expression, all notes off.
#      Reset All Controllers leaves the volume alone by the standard, so volume is sent separately
#   5. one test note. Hearing it means the keyboard was deaf and is back now

# installed as /opt/<name>/bridge/piano_unstick.sh, so the settings are in /etc/<name>/config.env
APP_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
CONFIG="/etc/$(basename "$APP_DIR")/config.env"
# shellcheck source=/dev/null
[ -r "$CONFIG" ] && . "$CONFIG"
NAME="${MIDI_DEVICE:-${1:-}}"
if [ -z "$NAME" ]; then
    echo "Usage: $0 \"keyboard name as shown by amidi -l\""
    exit 1
fi

DEV=$(amidi -l 2>/dev/null | awk -v n="$NAME" 'index($0, n) && ($1=="IO" || $1=="O") {print $2; exit}')
if [ -z "$DEV" ]; then
    echo "❌ amidi cannot find \"$NAME\". This is a USB disconnect, not a deaf keyboard"
    exit 1
fi
echo "Device: $DEV"

show_counters() {
    local f
    for f in /proc/asound/card*/midi*; do
        [ -r "$f" ] && grep -E "Output|Tx bytes|Input|Rx bytes" "$f" | tr -s ' ' | tr '\n' ' '
    done
    echo
}

send() {
    if ! timeout 3 amidi -p "$DEV" -S "$1" 2>&1; then
        echo "❌ Sending got stuck or failed ($1). The USB output is stuck, the keyboard is not the problem"
        exit 2
    fi
}

echo "--- counters before"
show_counters

echo "--- 1. closing an open SysEx"
send "F7"
sleep 0.1

echo "--- 2. GM System On"
send "F0 7E 7F 09 01 F7"
sleep 0.5

echo "--- 3. resetting 16 channels"
for ch in 0 1 2 3 4 5 6 7 8 9 A B C D E F; do
    send "B$ch 79 00 B$ch 07 64 B$ch 0B 7F B$ch 40 00 B$ch 7B 00"
done
sleep 0.2

echo "--- 4. test note, middle C"
send "90 3C 60"
sleep 0.8
send "80 3C 00"

echo "--- counters after"
show_counters

echo
echo "Did you hear the note? Yes: the keyboard was deaf and the reset fixed it. No: the problem is before the keyboard, in USB"
echo "--- recent USB messages"
dmesg -T 2>/dev/null | grep -i -E "usb|midi" | tail -8
