#!/bin/bash
# armonico-pi-report: everything about the Armonico Pi's screen and sound in one go, for a bug report.
# No questions: it writes the screen's and the sound's state to /var/lib/armonico/device/report.txt.
# Installed by install.sh --device as /usr/local/bin/armonico-pi-report. Run with sudo.
set -u
[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo: sudo armonico-pi-report"; exit 1; }
OUT=/var/lib/armonico/device/report.txt
mkdir -p "$(dirname "$OUT")"
# The report is written in root's own temp file and put in place with install, which removes what
# is there first and creates the file anew: the folder is the service user's, and a link left in it
# as report.txt was followed by root, who wrote and chmodded the file it pointed to.
TMP="$(mktemp)"
LOGS="$(mktemp -d)"
trap 'rm -rf "$TMP" "$LOGS"' EXIT
UID_A=$(id -u armonico)
AS=(runuser -u armonico -- env "XDG_RUNTIME_DIR=/run/user/$UID_A")
echo "Armonico Pi report: collecting, a few seconds."

{
    echo "=== Armonico Pi report, $(date '+%Y-%m-%d %H:%M')"
    echo "version: $(cat /opt/armonico/VERSION 2>/dev/null)   os: $(. /etc/os-release; echo "$PRETTY_NAME")   kernel: $(uname -r)"
    echo
    echo "=== Sound"
    echo "--- /etc/asound.conf"; cat /etc/asound.conf 2>/dev/null || echo "(none: the ALSA default is PipeWire)"
    echo "--- cards"; cat /proc/asound/cards
    echo "--- PipeWire"; "${AS[@]}" wpctl status 2>&1 | sed -n '/Audio/,/Video/p' | head -30
    echo "--- default output"; "${AS[@]}" wpctl inspect @DEFAULT_AUDIO_SINK@ 2>&1 | grep -E 'node.(name|description)' | head -2
    echo "--- latency and level of each output (pw-top: QUANT, the ALSA buffer per node)"
    "${AS[@]}" timeout 4 pw-top -b -n 2 2>&1 | tail -25
    for id in $("${AS[@]}" wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | sed -n 's/^[^0-9]*\([0-9]\+\)\. .*/\1/p'); do
        echo "sink $id: $("${AS[@]}" wpctl get-volume "$id" 2>&1) · $("${AS[@]}" wpctl inspect "$id" 2>/dev/null | grep -E 'node.name|api.alsa.(period-size|headroom|period-num)' | tr -s ' ' | tr '\n' ' ')"
    done
    echo "--- jack mixer"; amixer -c Headphones sget PCM 2>&1 | tail -2
    echo "--- real-time sound (FF = real-time; TS = ordinary, the sound can run dry when the Pi is busy)"
    echo "rtkit: $(systemctl is-active rtkit-daemon 2>/dev/null) · groups of armonico: $(id -nG armonico 2>/dev/null)"
    ps -eLo user:10,pid,cls,rtprio,comm 2>/dev/null | awk 'NR==1 || /pipewire|wireplumber|data-loop/' | head -12
    echo "--- user services: $(systemctl --user -M armonico@ is-active pipewire wireplumber 2>/dev/null | tr '\n' ' ')"
    echo
    echo "=== Screens"
    for f in /sys/class/drm/card*-*/status; do
        d=$(dirname "$f"); echo "$(basename "$d"): $(cat "$f") · $(head -1 "$d/modes" 2>/dev/null) · enabled $(cat "$d/enabled" 2>/dev/null)"
    done
    echo "settings: $(python3 -c "import json;d=json.load(open('/var/lib/armonico/device/settings.json'));print({k:d.get(k) for k in ('rest','sound_out','display','theme','lang','sleep','brightness','night','night_screen','night_when','night_on','night_auto','night_level','night_from','night_to')})" 2>/dev/null)"
    echo "--- MIDI keyboards (aconnect -i: a USB keyboard shows here when the system sees it)"
    aconnect -i 2>&1 | grep -E "^client" | head -10
    echo "--- backlight (none: the brightness is drawn darker instead)"
    for b in /sys/class/backlight/*; do
        [ -e "$b/brightness" ] && echo "$(basename "$b"): $(cat "$b/brightness") of $(cat "$b/max_brightness")"
    done
    echo
    echo "=== Video"
    /opt/armonico/device-venv/bin/python - <<'PY' 2>&1
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
print("GStreamer", Gst.version_string(), "· hardware H.264 decoder:", "yes" if Gst.ElementFactory.find("v4l2h264dec") else "no")
import pygame
print("pygame", pygame.version.ver, "SDL", ".".join(map(str, pygame.get_sdl_version())))
PY
    echo "throttled: $(vcgencmd get_throttled 2>/dev/null) · temp: $(vcgencmd measure_temp 2>/dev/null)"
    echo "--- CPU now"; top -bn1 | sed -n '1,5p;7,14p'
    echo
    echo "=== Screen"
    echo "screen unit (greetd): $(systemctl is-active armonico-screen) · $(labwc -v 2>/dev/null) · $(pgrep -a labwc | head -1) · $(pgrep -a wl-mirror | head -1)"
    loginctl list-sessions --no-legend 2>&1
    echo "--- greetd (last 20 lines)"
    journalctl -u armonico-screen -b -n 20 --no-pager -o cat
    echo "--- the session: labwc and the screen program (first 60 and last 60 lines)"
    L=/var/lib/armonico/device/session.log
    # every thread's Python stack into the log (faulthandler in screen.py), to see where a frozen screen waits
    # only the Python processes: labwc's own command line names screen.py too, and SIGUSR1 would end it
    for p in $(pgrep -f "/device/screen.py"); do
        case "$(cat /proc/$p/comm 2>/dev/null)" in python*) kill -USR1 "$p" 2>/dev/null ;; esac
    done
    sleep 1
    # The logs are in the service user's folder: read as that user, and only a regular file, never a
    # link. As root, a link left there as session.log was followed to any file of the system.
    for f in "$L" "$L.1"; do
        runuser -u armonico -- sh -c '[ -f "$1" ] && [ ! -L "$1" ] && exec timeout 10 cat -- "$1"' sh "$f" \
            > "$LOGS/${f##*/}" 2>/dev/null || rm -f "$LOGS/${f##*/}"
    done
    S="$LOGS/session.log"
    echo "--- frame rate (once a minute)"; grep -h "^Frames:" "$S" 2>/dev/null | tail -5
    if [ -f "$S" ]; then head -60 "$S"; echo "..."; tail -120 "$S"; else echo "(no session log: greetd did not start the session)"; fi
    [ -f "$S.1" ] && { echo "--- the session before (last 15 lines)"; tail -15 "$S.1"; }
    echo
    echo "=== Bluetooth"
    echo "bluetooth.service: $(systemctl is-active bluetooth 2>&1) · enabled: $(systemctl is-enabled bluetooth 2>&1) · $(bluetoothctl --version 2>&1)"
    dpkg -l bluez pi-bluetooth bluez-firmware libspa-0.2-bluetooth 2>&1 | grep -E "^(ii|un|rc)|no packages" | cut -c1-110
    echo "--- rfkill"; rfkill list 2>&1
    echo "--- adapters: $(ls /sys/class/bluetooth 2>&1 | tr '\n' ' ')"
    timeout 6 bluetoothctl list 2>&1 | head -5
    timeout 6 bluetoothctl show 2>&1 | head -14
    echo "--- scan 8 s, the way BlueZ documents it for scripts"
    timeout 14 bluetoothctl --timeout 8 scan on 2>&1 | grep -v "RSSI\|ManufacturerData\|ServiceData" | tail -25
    echo "--- known devices"; timeout 6 bluetoothctl devices 2>&1 | head -15
    echo "--- bluetoothd (last 20 lines)"; journalctl -u bluetooth -b -n 20 --no-pager -o cat 2>&1
    echo "--- kernel"; dmesg 2>/dev/null | grep -i -E "bluetooth|hci|brcm|btbcm" | tail -12
    echo "--- PipeWire sees: $(sudo -u armonico env XDG_RUNTIME_DIR="/run/user/$(id -u armonico)" wpctl status 2>&1 | grep -i -c bluez) bluez lines"
    echo
    echo "=== Device program (last 15 lines)"
    journalctl -u armonico-device -b -n 15 --no-pager -o cat
    echo
    echo "=== Screen requests (last 15 lines)"
    journalctl -u armonico-device-request -b -n 15 --no-pager -o cat
} > "$TMP" 2>&1
install -m 644 -T "$TMP" "$OUT"
cat "$TMP"
echo
echo "=== End of the report. It is also kept in $OUT"
