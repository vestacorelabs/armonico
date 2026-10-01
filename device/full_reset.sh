#!/bin/bash
# full_reset.sh - the full reset of the Armonico Pi, asked for on its screen (Settings › System).
#
# Everything this program put on the device goes: the program, the data, the backups kept on it,
# the system packages the installer added (Mosquitto among them, with its settings and data), the
# system users those packages made, the pip cache, the folder the project was unpacked in, and the
# system journal. Then it is installed again as on a first install: the release on GitHub when it is
# newer than the version on the device, else the version that was on the device, and after a
# restart the setup opens. The installer's --purge does the deleting.
#
# The packages go, so it needs a network: it is checked first, and without one nothing is deleted
# and the device goes back to its screen. The packages are downloaded before anything is deleted,
# so the install afterwards takes them from apt's cache and only the Python packages need the
# network again; if the install stops, it waits for the network and tries again, 3 times.
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
UI="$WORK/ui"
LOG="$WORK/reset.log"
TRIES=3

exec 9>"$LOCK"
flock -n 9 || exit 0

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
# the reset's screen, again if it stopped: it runs on the system's pygame, which the purge takes away
# and the install brings back
start_screen() {
    if [ -f "$UI/reset_screen.py" ] && { [ -z "$SCREEN_PID" ] || ! kill -0 "$SCREEN_PID" 2>/dev/null; }; then
        python3 "$UI/reset_screen.py" "$UI" >> "$LOG" 2>&1 &
        SCREEN_PID=$!
    fi
}

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
cp "$OPT/device/ui/data/logos/armonico-icon-dark.png" "$UI/logo.png" 2>/dev/null || true
for d in "$OPT"/device-venv/lib/python3*/site-packages/bidi; do
    [ -d "$d" ] && cp -r "$d" "$UI/lib/"
done
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
online || give_up nonet "No network: apt's package lists or PyPI did not answer"
SRC="$WORK/local"
SOURCE="Armonico $(cat "$WORK/local/VERSION" 2>/dev/null) · from the device"
REPO="$(cat "$OPT/REPO" 2>/dev/null || echo vestacorelabs/armonico)"
TAG="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/latest" 2>>"$LOG" \
       | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || TAG=""
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
if [[ "$TAG" =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]] && [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
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
if [ -s "$ETC/installed-packages" ]; then
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

# ---- 2. deleting everything: program, data, backups, the service user, the system packages the
# installer added and the users they made, Mosquitto with its data, the folder it was unpacked in
STEP=2
status running
follow env ARMONICO_FULL_RESET=yes bash "$WORK/remove/install.sh" --purge --yes \
    || fail "The removal stopped. Details: $LOG"
rm -rf "${DATA:?}" "${ETC:?}"            # anything left of the data folder, the backups in it too
rm -rf "${PIP_CACHE:?}"                  # the wheels pip kept from the installs
# the system journal: on this device it holds the program's lines and nothing anyone else needs
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true

# ---- 3. installing again, as a first install; when it stops, waiting for the network and again
STEP=3
status running
try=1
until follow bash "$WORK/src/install.sh" --device; do
    [ "$try" -ge "$TRIES" ] && fail "The install stopped $TRIES times. Details: $LOG"
    try=$((try + 1))
    echo "=== the install stopped, try $try of $TRIES" >> "$LOG"
    waited=0
    until online; do
        status running "Waiting for the network, then try $try of $TRIES"
        sleep 15
        waited=$((waited + 15))
        [ "$waited" -ge 600 ] && fail "No network for 10 minutes. Details: $LOG"
    done
    status running "Try $try of $TRIES"
done
start_screen

# ---- 4. starting again: the setup opens by itself, since nothing is set up
STEP=4
status "done" "$SOURCE"
echo "=== done, restarting" >> "$LOG"
sleep 4
# nothing of the reset stays either: its folder, its log and this script (bash keeps it open)
rm -rf "${WORK:?}" "$SELF"
systemctl reboot
