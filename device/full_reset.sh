#!/bin/bash
# full_reset.sh - the full reset of the Armonico Pi, asked for on its screen (Settings › System).
#
# Everything this program put on the device goes: the program, the data, the backups kept on it,
# the system packages the installer added (Mosquitto among them, with its settings and data), the
# system users those packages made, the pip cache, the folder the project was unpacked in, and the
# system journal. And what the screen changed in the system itself: the Bluetooth devices paired
# (BlueZ keeps them in /var/lib/bluetooth, outside the program, and the Bluetooth page lists them
# from there, so they were still shown after a reset), the Wi-Fi networks saved, the radios switched
# off, the radio chips turned off in config.txt, and the USB sticks mounted. Then it is installed again as on a first install: the release on GitHub when it is
# newer than the version on the device, else the version that was on the device, and after a
# restart the setup opens. The installer's --purge does the deleting.
#
# The packages go, so the install afterwards needs them again: with a network they are downloaded
# before anything is deleted (apt's cache) and the Python packages come from PyPI. Without one the
# install comes from the offline kit the installer keeps on the device ($KIT: the .deb files and the
# Python wheels of the install), so the reset works with no network at all. With neither a network
# nor a kit nothing is deleted and the device goes back to its screen. If the install stops, it
# tries again, 3 times: from the kit when there is one, else after waiting for the network.
#
# Runs as root, from a copy in /run, started by root_actions.py (full-reset) through systemd-run,
# so it outlives the services and the program it deletes. While it runs, reset_screen.py shows the
# steps on the device's screen; everything also goes to /var/lib/armonico-reset/reset.log, which is
# deleted with the rest when the reset ends well and stays when it stops.
set -Euo pipefail

APP="armonico"
# the folders can be moved for the tests (tests/test_full_reset.py); on the device they never are
OPT="${ARMONICO_RESET_OPT:-/opt/$APP}"
DATA="${ARMONICO_RESET_DATA:-/var/lib/$APP}"
ETC="${ARMONICO_RESET_ETC:-/etc/$APP}"
# in /var/lib, which only root writes to: in /var/tmp, open to everyone, a folder or a link of that
# name made there first was taken over by root as it was
WORK="${ARMONICO_RESET_WORK:-/var/lib/$APP-reset}"
SELF="${ARMONICO_RESET_SELF:-/run/$APP-full-reset.sh}"      # root_actions.py FULL_RESET_COPY
LOCK="${ARMONICO_RESET_LOCK:-/run/$APP-full-reset.lock}"
PIP_CACHE="${ARMONICO_RESET_PIP_CACHE:-/root/.cache/pip}"
BT_DIR="${ARMONICO_RESET_BT:-/var/lib/bluetooth}"                 # BlueZ: the paired devices, per adapter
RFKILL_DIR="${ARMONICO_RESET_RFKILL:-/var/lib/systemd/rfkill}"    # the radios' on/off kept across boots
BOOT_CONFIG="${ARMONICO_RESET_BOOT:-/boot/firmware/config.txt}"   # root_actions.py BOOT_CONFIG
USB_DIR="${ARMONICO_RESET_USB:-/media/$APP}"                       # root_actions.py usb-mount
CHIP_BEGIN="# >>> Armonico: radio chips"                           # root_actions.py CHIP_BEGIN (its start)
CHIP_END="# <<< Armonico: radio chips"
# the offline kit (install.sh KIT): outside the folders the reset deletes, kept by it
KIT="${ARMONICO_RESET_KIT:-/var/lib/$APP-offline}"
export ARMONICO_OFFLINE_KIT="$KIT"
UI="$WORK/ui"
LOG="$WORK/reset.log"
TRIES=3
# The recovery after a power cut: a systemd unit written here, outside the program and the data the reset
# deletes, so it exists when the installed system is gone. While the reset runs, REC_DIR holds a copy of this
# script and the step it reached; at the next boot the unit starts the copy in resume mode, which shows the
# reset's screen and goes on from that step. Both are removed when the reset ends well.
REC_DIR="${ARMONICO_RESET_REC:-/var/lib/$APP-recovery}"
REC_UNIT="${ARMONICO_RESET_UNIT:-/etc/systemd/system/$APP-recovery.service}"
RESUME="${1:-}"                                   # "--resume": started by the recovery unit

exec 9>"$LOCK"
flock -n 9 || exit 0

mark() {          # mark STEP: where a power cut would find the reset
    mkdir -p "$REC_DIR" && echo "$1" > "$REC_DIR/step" || true
}
arm_recovery() {
    mkdir -p "$REC_DIR" || return 0
    cp "$SELF" "$REC_DIR/full_reset.sh" 2>/dev/null || cp "${BASH_SOURCE[0]}" "$REC_DIR/full_reset.sh" 2>/dev/null || true
    cat > "$REC_UNIT" <<UNIT
[Unit]
Description=Armonico: goes on with a full reset that a power cut stopped
ConditionPathExists=$REC_DIR/step
After=local-fs.target

[Service]
Type=simple
ExecStart=/bin/bash $REC_DIR/full_reset.sh --resume
Nice=5

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable "$APP-recovery" >/dev/null 2>&1 || true
}
disarm_recovery() {
    systemctl disable "$APP-recovery" >/dev/null 2>&1 || true
    rm -f "$REC_UNIT"
    rm -rf "${REC_DIR:?}"
    systemctl daemon-reload 2>/dev/null || true
}

LANG_UI="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('lang', 'en'))" \
           "$DATA/device/settings.json" 2>/dev/null || echo en)"
STEP=0
SOURCE=""
SCREEN_PID=""

status() {        # status STATE [LINE]
    python3 - "$UI/status.json" "$LANG_UI" "$STEP" "$1" "${2:-}" "$SOURCE" <<'P' 2>/dev/null || true
import json, os, sys
f, lang, step, state, line, source = sys.argv[1:7]
tmp = f + ".tmp"
with open(tmp, "w") as out:
    json.dump({"lang": lang, "step": int(step), "state": state, "line": line[:160], "source": source}, out)
os.replace(tmp, f)
P
}
fail() {
    echo "=== stopped: $1" >> "$LOG"
    status failed "$1"
    # the screen keeps showing what stopped and how to go on by hand: the unit, and the screen with
    # it, would end with this script (the program that would show anything else is gone by now)
    sleep infinity
}
# Before anything is deleted: the reset ends, and the device goes back to its own screen as it was.
give_up() {       # give_up nonet|cancelled LINE
    echo "=== nothing deleted: $2" >> "$LOG"
    disarm_recovery
    status "$1" "$2"
    sleep 8
    [ -n "$SCREEN_PID" ] && kill "$SCREEN_PID" 2>/dev/null
    sleep 1
    systemctl start "$APP-screen" 2>/dev/null || true
    exit 0
}
# runs a command, its output to the log, and its latest line on the screen
follow() {
    local line
    "$@" < /dev/null 2>&1 | while IFS= read -r line; do
        printf '%s\n' "$line" >> "$LOG"
        line="$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | tr -d '\r')"
        [ -n "${line// }" ] && status running "$line"
    done
    return "${PIPESTATUS[0]}"
}
# The network the install needs: the package lists from every apt source, and PyPI.
online() {
    apt-get update -qq --error-on=any >> "$LOG" 2>&1 \
        && curl -fsS --max-time 15 -o /dev/null https://pypi.org/simple/pip/ 2>>"$LOG"
}
kit_ready() { compgen -G "$KIT/debs/*.deb" >/dev/null && compgen -G "$KIT/wheels/*" >/dev/null; }
# the reset's screen, again if it stopped: it runs on the system's pygame, which the purge takes away
# and the install brings back
start_screen() {
    if [ -f "$UI/reset_screen.py" ] && { [ -z "$SCREEN_PID" ] || ! kill -0 "$SCREEN_PID" 2>/dev/null; }; then
        python3 "$UI/reset_screen.py" "$UI" >> "$LOG" 2>&1 &
        SCREEN_PID=$!
    fi
}

# What the screen changed in the system, outside the program's folders: undone after the purge
forget_system() {
    # the USB sticks the screen mounted
    if [ -d "$USB_DIR" ]; then
        for m in "$USB_DIR"/*; do
            [ -d "$m" ] && { umount "$m" 2>/dev/null || umount -l "$m" 2>/dev/null || true; }
        done
        rm -rf "${USB_DIR:?}"
    fi
    # Bluetooth: every paired device forgotten, with bluetoothd stopped while its store is emptied
    if [ -d "$BT_DIR" ]; then
        systemctl stop bluetooth 2>/dev/null || true
        find "$BT_DIR" -mindepth 1 -delete 2>/dev/null || true
        echo "=== Bluetooth: the paired devices forgotten" >> "$LOG"
        systemctl start bluetooth 2>/dev/null || true
    fi
    # the radios on again (the switches in Settings › Network and links), and nothing kept off for the next boot
    command -v rfkill >/dev/null 2>&1 && rfkill unblock all 2>/dev/null || true
    [ -d "$RFKILL_DIR" ] && find "$RFKILL_DIR" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
    command -v nmcli >/dev/null 2>&1 && nmcli radio wifi on 2>/dev/null || true
    # the radio chips turned off from the screen: Armonico's block in config.txt goes (from the next start),
    # and hciuart, disabled with Bluetooth's chip, is enabled again
    if [ -f "$BOOT_CONFIG" ] && grep -qF "$CHIP_BEGIN" "$BOOT_CONFIG"; then
        grep -qF "hciuart.service disabled" "$BOOT_CONFIG" && { systemctl enable hciuart 2>/dev/null || true; }
        awk -v b="$CHIP_BEGIN" -v e="$CHIP_END" 'index($0, b) == 1 {skip = 1; next} skip && index($0, e) == 1 {skip = 0; next} !skip' \
            "$BOOT_CONFIG" > "$BOOT_CONFIG.armonico-reset" && cat "$BOOT_CONFIG.armonico-reset" > "$BOOT_CONFIG"
        rm -f "$BOOT_CONFIG.armonico-reset"
        echo "=== config.txt: the radio chips are on again from the next start" >> "$LOG"
    fi
}
# The Wi-Fi networks saved: last of all, after the install that needs the network. A cable stays connected;
# on Wi-Fi the setup asks for a network again after the restart.
forget_wifi() {
    command -v nmcli >/dev/null 2>&1 || return 0
    local uuid kind n=0
    while IFS=: read -r uuid kind; do
        [ "$kind" = "802-11-wireless" ] || continue
        nmcli connection delete uuid "$uuid" >/dev/null 2>&1 && n=$((n + 1))
    done < <(nmcli -t -f UUID,TYPE connection show 2>/dev/null)
    echo "=== Wi-Fi: $n saved network(s) forgotten" >> "$LOG"
}

if [ "$RESUME" = "--resume" ]; then
    # ---- a power cut stopped the reset: its folder, with the copy of the installer, is still here
    mkdir -p "$WORK/ui"
    START_STEP="$(cat "$REC_DIR/step" 2>/dev/null || echo 3)"
    [[ "$START_STEP" =~ ^[0-9]$ ]] || START_STEP=3
    echo "=== resumed after a power cut, $(date '+%F %T'), at step $START_STEP" >> "$LOG"
    if [ "$START_STEP" -le 1 ]; then
        # cut before anything was deleted: nothing to go on with, the device is as it was
        echo "=== nothing was deleted yet: the reset is dropped" >> "$LOG"
        disarm_recovery
        rm -rf "${WORK:?}"
        exit 0
    fi
    [ -f "$WORK/src/install.sh" ] || { STEP=3; fail "The reset was cut and its files are gone. By hand: install.sh --device from the project's folder"; }
    SRC="$WORK/src"
    SOURCE="Armonico $(cat "$SRC/VERSION" 2>/dev/null) · resumed after a power cut"
    OFFLINE=no
    online || { kit_ready && OFFLINE=yes; }
    STEP="$START_STEP"
    status running
    # the installed system may have started its own screen at this boot: the reset's takes the display
    systemctl stop "$APP-screen" 2>/dev/null || true
    sleep 1
    start_screen
else
# ---- 0. getting ready: what the reset needs, outside the program it deletes
rm -rf "$WORK"
# made anew under its root-owned parent with mktemp (mode 700), and moved to its own name: never
# a folder that was there before
mkdir -p "$(dirname "$WORK")"
NEW_WORK="$(mktemp -d "$(dirname "$WORK")/.$(basename "$WORK").XXXXXX")"
chmod 755 "$NEW_WORK"
mv -T "$NEW_WORK" "$WORK"
mkdir -p "$UI/lib"
: > "$LOG"
echo "=== Armonico full reset, $(date '+%F %T')" >> "$LOG"
cp "$OPT/device/reset_screen.py" "$UI/" 2>/dev/null
cp "$DATA"/device/fonts/Heebo-*.ttf "$UI/" 2>/dev/null || true
cp "$OPT/device/ui/data/logos/armonico-logo-dark.png" "$UI/logo.png" 2>/dev/null || true
cp "$OPT/device/ui/data/logos/vcl-dark.png" "$UI/vcl.png" 2>/dev/null || true
for d in "$OPT"/device-venv/lib/python3*/site-packages/bidi; do
    [ -d "$d" ] && cp -r "$d" "$UI/lib/"
done
arm_recovery
mark 1
status running
# the screen program lets go of the screen, and this one takes it
systemctl stop "$APP-screen" 2>/dev/null || true
sleep 1
start_screen
# the device's own copy, without its Python environments (the install makes them again)
mkdir -p "$WORK/local"
for d in bridge lessons setup system cli screen home-assistant device; do
    [ -d "$OPT/$d" ] && cp -r "$OPT/$d" "$WORK/local/"
done
cp "$OPT/install.sh" "$OPT/VERSION" "$WORK/local/" 2>/dev/null || give_up cancelled "The installer is missing in $OPT"
# the installer lists CHANGELOG.md among the files it needs next to itself (REQUIRED_FILES) and copies it to
# the program's folder: without it the install from this copy stopped at "The repository is incomplete", 3 times
cp "$OPT/CHANGELOG.md" "$WORK/local/" 2>/dev/null || give_up cancelled "CHANGELOG.md is missing in $OPT"
[ -f "$OPT/REPO" ] && cp "$OPT/REPO" "$WORK/local/"

# ---- 1. the network, the newest release from GitHub (checked against its SHA256SUMS, else the
# device's own copy), and the system packages, downloaded while nothing is deleted yet
STEP=1
status running
export DEBIAN_FRONTEND=noninteractive
OFFLINE=no
if ! online; then
    kit_ready || give_up nonet "No network, and no offline kit on the device"
    OFFLINE=yes
    echo "=== no network: installing from the offline kit in $KIT" >> "$LOG"
fi
SRC="$WORK/local"
SOURCE="Armonico $(cat "$WORK/local/VERSION" 2>/dev/null) · from the device"
REPO="$(cat "$OPT/REPO" 2>/dev/null || echo vestacorelabs/armonico)"
TAG=""
if [ "$OFFLINE" = "no" ]; then
    TAG="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/latest" 2>>"$LOG" \
           | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || TAG=""
fi
# GitHub's release only when it is newer than the version on the device, as the updates decide
# (device/diag.py update_available): an older or the same one would put back a version behind the
# one that was running, as with a release that was published long before the code on the device
MINE="$(tr -d '[:space:]' < "$WORK/local/VERSION" 2>/dev/null)"
# a version as diag.py _version compares it: the v taken off, the numbers padded to 4 parts (1.0 is
# 1.0.0.0), so v1.0.0 or 1.0.0.0 is the same version as 1.0 and never newer. sort -V on the raw text
# had v1.0.0 newer than 1.0. A pre-release (1.1-rc1) comes before its release (1.1), as in diag.py.
vkey() {          # vkey TEXT: 4 numbers of 10 digits each, then 1 for a release or 0 for a pre-release
    local v="${1#v}" a b c d
    [[ "$v" =~ ^([0-9]{1,9})(\.([0-9]{1,9}))?(\.([0-9]{1,9}))?(\.([0-9]{1,9}))?(.*)$ ]] || return 1
    a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[3]:-0}" c="${BASH_REMATCH[5]:-0}" d="${BASH_REMATCH[7]:-0}"
    printf '%010d%010d%010d%010d%d' "$((10#$a))" "$((10#$b))" "$((10#$c))" "$((10#$d))" \
        "$([ -z "${BASH_REMATCH[8]}" ] && echo 1 || echo 0)"
}
newer() {         # newer THEIRS MINE: true only when THEIRS is a later version; the same one never is
    local a b
    a="$(vkey "$1")" && b="$(vkey "$2")" || return 1
    [[ "$a" > "$b" ]]
}
# a release taken for the reinstall must carry the device's files: a release without device/
# folder, and installing from it would have left a device without its program. The files the
# release's own installer requires under device/, and the reset itself, all present.
has_device() {    # has_device DIR
    local d="$1" f n=0
    for f in device/armonico_pi.py device/full_reset.sh device/root_actions.py; do
        [ -f "$d/$f" ] || return 1
    done
    while read -r f; do
        [ -f "$d/$f" ] || return 1
        n=$((n + 1))
    done < <(sed -n '/^REQUIRED_FILES=(/,/^)/p' "$d/install.sh" 2>/dev/null | grep -oE '^[[:space:]]*device/[^[:space:]#]+')
    [ "$n" -gt 0 ]
}
if [ "$OFFLINE" = "no" ] && [[ "$TAG" =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]] && [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
   && newer "$TAG" "$MINE"; then
    BASE="https://github.com/$REPO/releases/download/$TAG"
    mkdir -p "$WORK/net"
    if curl -fsSL --proto '=https' --tlsv1.2 --max-time 300 -o "$WORK/release.zip" "$BASE/armonico-$TAG.zip" 2>>"$LOG" \
        && curl -fsSL --proto '=https' --tlsv1.2 --max-time 60 -o "$WORK/sha256" "$BASE/SHA256SUMS" 2>>"$LOG" \
        && [ "$(awk -v f="armonico-$TAG.zip" '{n = $2; sub(/^\*/, "", n)} n == f && $1 ~ /^[0-9a-f]+$/ && length($1) == 64 {print $1; exit}' "$WORK/sha256")" \
             = "$(sha256sum "$WORK/release.zip" | cut -d' ' -f1)" ] \
        && unzip -q -o "$WORK/release.zip" -d "$WORK/net" 2>>"$LOG"; then
        found="$(find "$WORK/net" -maxdepth 3 -name install.sh -type f | head -1)"
        if [ -n "$found" ] && has_device "$(dirname "$found")"; then
            SRC="$(dirname "$found")"
            SOURCE="Armonico $TAG · from GitHub"
        elif [ -n "$found" ]; then
            echo "=== GitHub's $TAG has no device files: installing from the device's own copy" >> "$LOG"
        fi
    fi
fi
echo "=== GitHub's latest: ${TAG:-none} · on the device: ${MINE:-unknown} · installing from $SRC ($SOURCE)" >> "$LOG"
# Every package the installer added, into apt's cache: the install after the purge takes them from
# there. A package apt cannot fetch any more is fetched by the install itself, so this never stops.
if [ "$OFFLINE" = "no" ] && [ -s "$ETC/installed-packages" ]; then
    mapfile -t PKGS < <(grep -E '^[a-z0-9][a-z0-9+.:-]*$' "$ETC/installed-packages")
    if [ "${#PKGS[@]}" -gt 0 ]; then
        follow apt-get install -y -q --download-only --reinstall --no-install-recommends "${PKGS[@]}" \
            || echo "=== some packages were not downloaded ahead, the install fetches them" >> "$LOG"
    fi
fi
# one copy removes (the installer deletes the folder it runs from), one installs
cp -r "$SRC" "$WORK/remove"
cp -r "$SRC" "$WORK/src"
chmod 755 "$WORK/remove/install.sh" "$WORK/src/install.sh"
fi        # not resumed

# ---- 2. deleting everything: program, data, backups, the service user, the system packages the
# installer added and the users they made, Mosquitto with its data, the folder it was unpacked in
if [ "${START_STEP:-0}" -le 2 ]; then
STEP=2
mark 2
status running
# after a cut in the middle of the removal the folder it ran from may be gone: nothing is left to remove then
if [ -f "$WORK/remove/install.sh" ]; then
    # a removal that was cut and runs again finds half of it done and may stop: the install after it
    # puts everything back anyway
    follow env ARMONICO_FULL_RESET=yes bash "$WORK/remove/install.sh" --purge --yes \
        || { [ "$RESUME" = "--resume" ] || fail "The removal stopped. Details: $LOG"; }
fi
rm -rf "${DATA:?}" "${ETC:?}"            # anything left of the data folder, the backups in it too
rm -rf "${PIP_CACHE:?}"                  # the wheels pip kept from the installs
forget_system
# the system journal: on this device it holds the program's lines and nothing anyone else needs
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
fi

# ---- 3. installing again, as a first install; when it stops, waiting for the network and again
STEP=3
mark 3
status running
try=1
until follow env ARMONICO_OFFLINE="$([ "$OFFLINE" = yes ] && echo yes)" bash "$WORK/src/install.sh" --device; do
    [ "$try" -ge "$TRIES" ] && fail "The install stopped $TRIES times. Details: $LOG"
    try=$((try + 1))
    echo "=== the install stopped, try $try of $TRIES" >> "$LOG"
    if kit_ready; then
        # the kit needs no network: the next try uses it
        OFFLINE=yes
    else
        waited=0
        until online; do
            status running "Waiting for the network, then try $try of $TRIES"
            sleep 15
            waited=$((waited + 15))
            [ "$waited" -ge 600 ] && fail "No network for 10 minutes. Details: $LOG"
        done
    fi
    status running "Try $try of $TRIES"
done
start_screen

# ---- 4. starting again: the setup opens by itself, since nothing is set up
STEP=4
status "done" "$SOURCE"
forget_wifi
echo "=== done, restarting" >> "$LOG"
sleep 4
# nothing of the reset stays either: its folder, its log and this script (bash keeps it open)
rm -rf "${WORK:?}" "$SELF"
disarm_recovery
systemctl reboot
