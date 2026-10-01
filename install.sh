#!/usr/bin/env bash
#
# install.sh - installer for Armonico
#
# Installs the MIDI bridge and the lesson engine on Debian or Ubuntu,
# runs them as systemd services under their own user, and opens a
# one-time setup screen in the browser to connect MQTT and the keyboard.
#
#   sudo ./install.sh                 install, setup in the browser
#   sudo ./install.sh --cli           install, setup in the terminal
#   sudo ./install.sh --reconfigure   run the setup again, keep everything else
#   sudo ./install.sh --integrations  refresh the Home Assistant parts and the Telegram bot's profile
#                                     (with the tokens kept at install, or new ones it asks for)
#   sudo ./install.sh --uninstall     remove the program, the settings, the songs, the progress
#                                     and this folder. Nothing of it is left on the machine
#   sudo ./install.sh --purge         the same, and the system packages, Mosquitto and the
#                                     retained messages on the broker (asks first; --yes skips)
#
# Running it again on an installed machine updates the code and keeps
# the settings, the progress and the songs.

set -Eeuo pipefail

# ----------------------------------------------------------------------
# Names and paths. APP is the only line to change when renaming.
# ----------------------------------------------------------------------
APP="armonico"
APP_TITLE="Armonico"
REPO="vestacorelabs/armonico"          # GitHub owner/name, where updates come from

OPT_DIR="/opt/$APP"                 # code and the Python venv, owned by root
ETC_DIR="/etc/$APP"                 # settings, readable by root and the $APP group
VAR_DIR="/var/lib/$APP"             # progress and shortcuts, owned by the service user
SONGS_DIR="$VAR_DIR/songs"          # MIDI files
CONFIG="$ETC_DIR/config.env"
VENV="$OPT_DIR/venv"
SERVICE_USER="$APP"
SERVICES=("$APP-bridge" "$APP-lessons")
OPTIONAL_SERVICES=("$APP-recorder")   # installed, started only when turned on (armonico record on)
UNIT_DIR="/etc/systemd/system"

SETUP_PORT=8098                     # the one-time setup screen
SETUP_TIMEOUT=900                   # an unused setup screen stops after 15 minutes
LESSON_UI_PORT=8099                 # the lesson screen, always on

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# alsa-utils: aconnect, amidi, aplaymidi, aseqdump. util-linux: flock, used by the bridge.
# unzip: opens the release archive when the program updates itself.
APT_PACKAGES=(alsa-utils mosquitto-clients util-linux curl unzip python3 python3-venv ca-certificates)
# paho-mqtt 2 is not packaged by every distribution, which is why a venv is used at all
PY_PACKAGES=("mido>=1.3" "python-rtmidi>=1.5" "paho-mqtt>=2.0,<3" "PyYAML>=6")
# Optional: mDNS, to find Home Assistant on the network. Everything works without it.
PY_OPTIONAL=("zeroconf>=0.130")
# needed only when python-rtmidi has no ready wheel for the CPU and has to be compiled
BUILD_PACKAGES=(build-essential python3-dev libasound2-dev pkg-config)

# Files the installer copies. A missing one stops the install before anything changes.
REQUIRED_FILES=(
    bridge/piano_bridge.sh
    bridge/shortcuts.conf.example
    lessons/piano_game.py
    lessons/piano_numbers.py
    lessons/piano_hands.py
    lessons/fonts/heebo-hebrew.woff2
    lessons/fonts/heebo-latin.woff2
    lessons/fonts/heebo-latin-ext.woff2
    lessons/fonts/OFL.txt
    lessons/sounds/piano/A4.mp3
    lessons/sounds/piano/SOURCE.md
    setup/setup_web.py
    setup/find_ha.py
    setup/ha_setup.py
    setup/telegram_setup.py
    setup/bot-photo.jpg
    home-assistant/piano.yaml
    setup/install_music.py
    system/bridge.service
    system/lessons.service
    system/update.service
    system/update.path
    system/reset.service
    system/reset.path
    bridge/piano_reset.sh
    system/recorder.service
    bridge/mqtt_recorder.py
    setup/update.sh
    screen/piano-status.html
    setup/logo.ans
    cli/armonico.py
    VERSION
    bridge/piano_unstick.sh
)

# What a person reads: 1.0.0 is written 1.0. Only a trailing zero goes, so 1.0.1 is shown whole
shown_version() { sed -E 's/^([0-9]+\.[0-9]+)\.0$/\1/' "$REPO_DIR/VERSION" 2>/dev/null; }

# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------
if [ -t 1 ]; then
    C_BLUE=$'\e[1;34m'; C_GREEN=$'\e[1;32m'; C_YELLOW=$'\e[1;33m'; C_RED=$'\e[1;31m'; C_OFF=$'\e[0m'
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_OFF=""
fi
# Messages wrap at the terminal's width on whole words, with the next lines
# indented under the text, so a long message never breaks in the middle of a word.
COLS=80
[ -t 1 ] && COLS="$(tput cols 2>/dev/null || echo 80)"
wrap() {            # wrap INDENT TEXT
    local w=$((COLS - $1 - 1))
    [ "$w" -lt 30 ] && w=30
    printf '%s\n' "$2" | fold -s -w "$w" | sed -e 's/ *$//' -e "2,\$ s/^/$(printf '%*s' "$1" '')/"
}
# The house beside the name, as on the lesson screen, drawn in 24-bit color. Many
# terminals that take 24-bit color still report TERM=xterm and 8 colors (PuTTY, and
# SSH from some clients), so any color terminal gets it. The Linux text console, a
# dumb or VT terminal, and a log file get the name alone.
# Below 64 columns the text goes under the drawing instead of beside it.
show_logo() {
    local art="$REPO_DIR/setup/logo.ans" colors lines i text=()
    colors="$(tput colors 2>/dev/null || echo 0)"
    if [ ! -t 1 ] || [ ! -r "$art" ] || [ "$COLS" -lt 32 ] || [ "${colors:-0}" -lt 8 ]; then
        colors=0
    fi
    case "${TERM:-dumb}" in linux|dumb|vt*|cons*) colors=0 ;; esac
    if [ "$colors" -eq 0 ]; then
        printf '%s%s%s %s\n' "$C_BLUE" "$APP_TITLE" "$C_OFF" "$(shown_version)"
        return 0
    fi
    text=("" "" "" "" ""
          $'\e[1;38;2;29;107;224m'"$APP_TITLE"$'\e[0m'
          $'\e[38;2;226;176;74m'"Your piano talks back"$'\e[0m'
          ""
          $'\e[2m'"Version $(shown_version)"$'\e[0m'
          $'\e[2m'"github.com/$REPO"$'\e[0m')
    mapfile -t lines < "$art"
    echo
    for i in "${!lines[@]}"; do
        if [ "$COLS" -ge 64 ] && [ -n "${text[$i]:-}" ]; then
            printf '  %s\e[34G%s\n' "${lines[$i]}" "${text[$i]}"
        else
            printf '  %s\n' "${lines[$i]}"
        fi
    done
    if [ "$COLS" -lt 64 ]; then
        echo
        for i in 5 6 8 9; do printf '  %s\n' "${text[$i]}"; done
    fi
}

step() { printf '\n%s==>%s %s\n' "$C_BLUE" "$C_OFF" "$(wrap 4 "$*")"; }
ok()   { printf '  %s✔%s %s\n' "$C_GREEN" "$C_OFF" "$(wrap 4 "$*")"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$(wrap 4 "$*")"; }
die()  { printf '\n%s✘ %s%s\n' "$C_RED" "$(wrap 2 "$*")" "$C_OFF" >&2; exit 1; }

trap 'die "Stopped at line $LINENO: $BASH_COMMAND"' ERR

usage() {
    sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

# ----------------------------------------------------------------------
# Arguments
# ----------------------------------------------------------------------
MODE="web"
ACTION="install"
ASSUME_YES="no"
for arg in "$@"; do
    case "$arg" in
        --cli)          MODE="cli" ;;
        --reconfigure)  ACTION="reconfigure" ;;
        --integrations) ACTION="integrations" ;;
        --uninstall)    ACTION="uninstall" ;;
        --purge)        ACTION="purge" ;;
        --yes)          ASSUME_YES="yes" ;;
        -h|--help)      usage ;;
        *)              die "Unknown option: $arg (see --help)" ;;
    esac
done

# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------
require_root() {
    [ "$(id -u)" -eq 0 ] || die "Run as root: sudo $0 $*"
}

check_os() {
    # Termux runs Debian-like packages on Android, but has no systemd and no access to USB MIDI
    if [ -n "${TERMUX_VERSION:-}" ] || [ -d /data/data/com.termux ]; then
        die "Termux on Android is not supported: Android gives apps no access to the ALSA MIDI devices, and there is no systemd. See docs/installation.md"
    fi
    [ -r /etc/os-release ] || die "Cannot identify the system: /etc/os-release is missing"
    # shellcheck source=/dev/null
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*) ok "System: ${PRETTY_NAME:-$ID}" ;;
        *) die "Supported systems are Debian, Ubuntu and their derivatives. Found: ${PRETTY_NAME:-unknown}" ;;
    esac
    command -v systemctl >/dev/null || die "systemd is required"
    [ -d /run/systemd/system ] || die "systemd is installed but not running (a container or chroot?)"
}

# Nothing in the code depends on the CPU: the scripts are bash and Python, and apt
# and pip pick the right build for the machine. The one exception is python-rtmidi,
# which has ready-made packages for 64-bit PCs and 64-bit ARM. On 32-bit ARM
# (Raspberry Pi OS 32-bit, Pi Zero, Pi 1 to 3) Raspberry Pi OS usually gets one from
# piwheels; anywhere else it is compiled, which build_venv handles.
check_platform() {
    local arch mem_mb free_mb
    arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
    case "$arch" in
        amd64|arm64)  ok "CPU: $arch" ;;
        armhf|armel|i386)
                      ok "CPU: $arch (32-bit: one Python package may have to be compiled, a few minutes)" ;;
        *)            warn "CPU: $arch is untested. The install continues and compiles what it has to" ;;
    esac
    mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
    if [ "${mem_mb:-0}" -lt 400 ]; then
        warn "Memory: ${mem_mb} MB. It runs in about 100 MB, but compiling on so little memory can fail; a swap file helps"
    else
        ok "Memory: ${mem_mb} MB"
    fi
    free_mb=$(df -Pm / | awk 'NR == 2 {print $4}')
    [ "${free_mb:-0}" -ge 300 ] || die "Only ${free_mb} MB free on /. At least 300 MB are needed (about 100 MB stay in use)"
    ok "Free space: ${free_mb} MB"
}

check_repo() {
    local f missing=0
    for f in "${REQUIRED_FILES[@]}"; do
        if [ ! -f "$REPO_DIR/$f" ]; then
            warn "Missing: $f"
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || die "The repository is incomplete. Clone it again and rerun the installer."
    ok "Repository files found"
}

check_python() {
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
        || die "Python 3.8 or newer is required, found $(python3 -V 2>&1)"
    ok "$(python3 -V)"
}

# ----------------------------------------------------------------------
# Packages this program added. Each install run compares the installed packages
# before and after and adds the new ones to a list, dependencies included, so
# --purge removes those and nothing that was on the machine before.
# ----------------------------------------------------------------------
PKG_LIST="$ETC_DIR/installed-packages"
PKG_KEEP=(curl ca-certificates)     # stay even when this program brought them: other tools expect curl
PKG_BEFORE=""

installed_packages() {
    dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' 2>/dev/null \
        | awk '$1 == "ii" {print $2}' | LC_ALL=C sort -u
}

snapshot_packages() {
    PKG_BEFORE="$(mktemp)"
    installed_packages > "$PKG_BEFORE"
}

# runs from the EXIT trap as well, so packages added by a run that stopped halfway are listed too
record_packages() {
    [ -n "$PKG_BEFORE" ] && [ -f "$PKG_BEFORE" ] || return 0
    if [ -d "$ETC_DIR" ]; then
        { cat "$PKG_LIST" 2>/dev/null || true; LC_ALL=C comm -13 "$PKG_BEFORE" <(installed_packages); } \
            | LC_ALL=C sort -u > "$PKG_LIST.tmp"
        chmod 600 "$PKG_LIST.tmp"
        mv -f "$PKG_LIST.tmp" "$PKG_LIST"
    fi
    rm -f "$PKG_BEFORE"
    PKG_BEFORE=""
}

# ----------------------------------------------------------------------
# Install steps
# ----------------------------------------------------------------------
# The two packages the music needs come first, so the keyboard can already
# play while everything else installs.
early_packages() {
    step "Keyboard check"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends alsa-utils python3 >/dev/null
    ok "alsa-utils, python3"
}

install_packages() {
    step "System packages"
    apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}" >/dev/null
    ok "${APT_PACKAGES[*]}"
}

# ----------------------------------------------------------------------
# Music while installing. Optional, offered only when someone is at the
# terminal and a keyboard is connected. Playing proves the output works,
# and any key pressed stops it, which proves the input works too.
# ----------------------------------------------------------------------
MUSIC_PID=""
MUSIC_PORT=""
MUSIC_END="stop"            # stop: cut it when the install is done. finish: let the piece end.
MUSIC_PY="$REPO_DIR/setup/install_music.py"

start_music() {
    [ -t 0 ] && [ -t 1 ] || return 0
    local found name answer
    found="$(python3 "$MUSIC_PY" detect 2>/dev/null)" || {
        warn "No MIDI keyboard found yet. That is fine, the setup looks for it again later."
        return 0
    }
    MUSIC_PORT="${found%%$'\t'*}"
    name="${found#*$'\t'}"
    ok "Keyboard found: $name ($MUSIC_PORT)"
    read -r -p "  Play some classical music on it while the rest installs? [Y/n] " answer
    case "${answer,,}" in n|no) return 0 ;; esac
    read -r -p "  When the install is done: stop right away, or let the piece finish? [S/f] " answer
    case "${answer,,}" in f|finish) MUSIC_END="finish" ;; esac
    echo "  Press any key on the keyboard to stop the music at any time."
    python3 "$MUSIC_PY" play "$MUSIC_PORT" &
    MUSIC_PID=$!
}

stop_music() {
    [ -n "$MUSIC_PORT" ] && [ -n "$MUSIC_PID" ] || return 0
    local rc=0
    if kill -0 "$MUSIC_PID" 2>/dev/null; then
        if [ "$MUSIC_END" = "finish" ]; then
            echo "  ♪ Letting the piece finish..."
            kill -USR1 "$MUSIC_PID" 2>/dev/null || true
        else
            kill -TERM "$MUSIC_PID" 2>/dev/null || true
        fi
    fi
    wait "$MUSIC_PID" 2>/dev/null || rc=$?
    MUSIC_PID=""
    # the closing flourish, unless the music never managed to play at all
    if [ "$rc" -ne 1 ]; then
        python3 "$MUSIC_PY" finale "$MUSIC_PORT" 2>/dev/null || true
    fi
}

# on errors and Ctrl+C: silence the keyboard, never leave it playing on its own
cleanup() {
    if [ -n "$MUSIC_PID" ] && kill -0 "$MUSIC_PID" 2>/dev/null; then
        kill -TERM "$MUSIC_PID" 2>/dev/null || true
        wait "$MUSIC_PID" 2>/dev/null || true
    fi
    record_packages
}
trap cleanup EXIT

# The ALSA sequencer is what aconnect and aseqdump talk to. Minimal and
# virtual systems often ship without it loaded, and then no keyboard shows up.
enable_sequencer() {
    if [ ! -e /dev/snd/seq ]; then
        modprobe snd-seq 2>/dev/null || true
    fi
    if [ -e /dev/snd/seq ]; then
        echo "snd-seq" > "/etc/modules-load.d/$APP.conf"
        ok "ALSA sequencer is available"
        # Without the usual udev rule (minimal and virtual systems) the node belongs to root, and the
        # services and the setup screen, which run as a user of the audio group, cannot open it.
        if [ "$(stat -c %G /dev/snd/seq 2>/dev/null)" != "audio" ]; then
            printf 'SUBSYSTEM=="sound", KERNEL=="seq", GROUP="audio", MODE="0660"\n' > "/etc/udev/rules.d/60-$APP-seq.rules"
            udevadm control --reload 2>/dev/null || true
            udevadm trigger --subsystem-match=sound 2>/dev/null || true
            # the rule applies at the next device event: the node is set right now as well
            chgrp audio /dev/snd/seq 2>/dev/null && chmod 660 /dev/snd/seq 2>/dev/null || true
            if [ "$(stat -c %G /dev/snd/seq 2>/dev/null)" = "audio" ]; then
                ok "/dev/snd/seq now belongs to the audio group, so the services can open it"
            else
                warn "/dev/snd/seq is not in the audio group. By hand: sudo chgrp audio /dev/snd/seq && sudo chmod 660 /dev/snd/seq"
            fi
        fi
    else
        warn "The ALSA sequencer is not available. A USB MIDI keyboard will not be seen until it is."
    fi
}

# A system user with no login and no password. Membership in the audio
# group is what gives it access to /dev/snd, so the services never run as root.
NEW_GROUPS="no"
create_user() {
    step "Service user"
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        usermod -aG audio "$SERVICE_USER"
        ok "$SERVICE_USER exists"
    else
        useradd --system --home-dir "$VAR_DIR" --no-create-home \
                --shell /usr/sbin/nologin --groups audio "$SERVICE_USER"
        ok "$SERVICE_USER created"
    fi
    # the person who ran sudo may use the armonico command without sudo, after logging in again:
    # the armonico group reads the settings, and adm / systemd-journal read the service logs
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        local g added=""
        for g in "$SERVICE_USER" adm systemd-journal; do
            getent group "$g" >/dev/null 2>&1 || continue
            if ! id -nG "$SUDO_USER" | grep -qw "$g"; then
                if usermod -aG "$g" "$SUDO_USER"; then added="$added $g"; fi
            fi
        done
        if [ -n "$added" ]; then
            NEW_GROUPS="yes"
            ok "$SUDO_USER added to the groups:$added"
        fi
    fi
}

create_dirs() {
    install -d -m 755 -o root -g root "$OPT_DIR"
    # the group may read the settings, so its members run the armonico command without sudo
    install -d -m 750 -o root -g "$SERVICE_USER" "$ETC_DIR"
    if [ -f "$CONFIG" ]; then chown "root:$SERVICE_USER" "$CONFIG"; chmod 640 "$CONFIG"; fi
    install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$VAR_DIR" "$SONGS_DIR"
}

# Where the project was unpacked, kept so that --uninstall and --purge can remove that folder
# even when they run from /opt (the armonico command). Not recorded by an update, which runs
# from a temporary folder, or by a run that has no terminal.
record_source_dir() {
    [ "$REPO_DIR" != "$OPT_DIR" ] && [ -t 0 ] && [ -d "$ETC_DIR" ] || return 0
    case "$REPO_DIR" in /tmp/*|/var/tmp/*) return 0 ;; esac
    ( umask 077; printf '%s\n' "$REPO_DIR" > "$ETC_DIR/source_dir" )
}

install_files() {
    step "Program files"
    local d
    record_source_dir
    # system/ and install.sh come along, so the armonico command can run the setup
    # again and remove the program without a copy of the repository
    for d in bridge lessons setup system cli screen home-assistant; do
        rm -rf "${OPT_DIR:?}/$d"
        cp -r "$REPO_DIR/$d" "$OPT_DIR/$d"
    done
    install -m 755 "$REPO_DIR/install.sh" "$OPT_DIR/install.sh"
    chown -R root:root "$OPT_DIR/bridge" "$OPT_DIR/lessons" "$OPT_DIR/setup" "$OPT_DIR/system" "$OPT_DIR/cli" "$OPT_DIR/screen" "$OPT_DIR/home-assistant"
    find "$OPT_DIR/bridge" "$OPT_DIR/lessons" "$OPT_DIR/setup" "$OPT_DIR/system" "$OPT_DIR/cli" "$OPT_DIR/home-assistant" -type f -exec chmod 644 {} +
    chmod 755 "$OPT_DIR/bridge/piano_bridge.sh" "$OPT_DIR/bridge/piano_unstick.sh" "$OPT_DIR/bridge/piano_reset.sh" "$OPT_DIR/setup/update.sh" "$OPT_DIR/bridge/mqtt_recorder.py"
    printf '#!/bin/sh\nexec %s/venv/bin/python %s/cli/armonico.py "$@"\n' "$OPT_DIR" "$OPT_DIR" > "/usr/local/bin/$APP"
    chmod 755 "/usr/local/bin/$APP"
    install -m 644 "$REPO_DIR/VERSION" "$OPT_DIR/VERSION"
    printf '%s\n' "$REPO" > "$OPT_DIR/REPO"
    ok "Code copied to $OPT_DIR (version $(shown_version))"

    # the shortcuts file belongs to the user: created once, never overwritten.
    # No shortcuts are shipped, so this only seeds an empty file with the format notes.
    if [ ! -f "$VAR_DIR/shortcuts.conf" ]; then
        install -m 640 -o "$SERVICE_USER" -g "$SERVICE_USER" \
            "$REPO_DIR/bridge/shortcuts.conf.example" "$VAR_DIR/shortcuts.conf"
        ok "Empty shortcuts file created in $VAR_DIR/shortcuts.conf"
    else
        ok "Existing shortcuts kept"
    fi

    # the installer's music doubles as the first songs, so there is something to
    # play right away. A song already there with the same name is never replaced.
    local f added=0
    for f in "$REPO_DIR"/setup/music/*.mid; do
        [ -e "$SONGS_DIR/$(basename "$f")" ] && continue
        install -m 640 -o "$SERVICE_USER" -g "$SERVICE_USER" "$f" "$SONGS_DIR/"
        added=$((added + 1))
    done
    [ "$added" -gt 0 ] && ok "$added songs added to $SONGS_DIR"
    return 0
}

# Everything here is bash and pure Python, the same on every CPU. The one exception is
# python-rtmidi, which has compiled code. Where a ready-made package exists for this CPU
# and Python version it is downloaded; everywhere else it is compiled once, here.
#   x86_64 and aarch64 (64-bit Raspberry Pi OS): ready-made up to Python 3.12 on PyPI
#   armv7l and armv6l (32-bit Raspberry Pi OS): ready-made on piwheels.org
#   anything else, or a newer Python: compiled
build_venv() {
    step "Python environment"
    local arch pyver pip_extra=() need_build=0
    arch="$(uname -m)"
    pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    case "$arch" in
        x86_64|aarch64)
            python3 -c 'import sys; sys.exit(0 if sys.version_info < (3, 13) else 1)' || need_build=1 ;;
        armv7l|armv6l)
            pip_extra=(--extra-index-url https://www.piwheels.org/simple) ;;
        *)
            need_build=1 ;;
    esac
    ok "CPU $arch, Python $pyver"
    if [ "$need_build" -eq 1 ]; then
        warn "No ready-made python-rtmidi for this CPU and Python, it is compiled (a few minutes on a Raspberry Pi)"
        apt-get install -y -qq --no-install-recommends "${BUILD_PACKAGES[@]}" >/dev/null
    fi

    # after a system upgrade to a newer Python the old environment points at a Python
    # that is gone; it is rebuilt instead of failing on the next step
    if [ -e "$VENV" ] && ! "$VENV/bin/python" -c '' 2>/dev/null; then
        warn "The Python environment belongs to an older Python, rebuilding it"
        rm -rf "${VENV:?}"
    fi
    if [ ! -x "$VENV/bin/python" ]; then
        python3 -m venv "$VENV"
    fi
    "$VENV/bin/pip" install -q --upgrade pip >/dev/null
    if ! "$VENV/bin/pip" install -q "${pip_extra[@]}" --upgrade "${PY_PACKAGES[@]}" >/dev/null 2>&1; then
        # usually python-rtmidi on a CPU with no ready-made wheel: compile it
        warn "Ready-made packages did not fit this CPU, compiling (this can take a few minutes)"
        apt-get install -y -qq --no-install-recommends "${BUILD_PACKAGES[@]}" >/dev/null
        "$VENV/bin/pip" install -q "${pip_extra[@]}" --upgrade "${PY_PACKAGES[@]}" \
            || die "Python packages failed to install. Run again with a network connection."
    fi
    "$VENV/bin/python" -c 'import mido, rtmidi, paho.mqtt' \
        || die "Python packages are installed but do not load"
    ok "${PY_PACKAGES[*]}"

    # Only for finding Home Assistant on the network, and the setup works without it,
    # so a failure here is a warning and not the end of the install
    if "$VENV/bin/pip" install -q "${pip_extra[@]}" --upgrade "${PY_OPTIONAL[@]}" >/dev/null 2>&1; then
        ok "${PY_OPTIONAL[*]}"
    else
        warn "${PY_OPTIONAL[*]} did not install. The setup will ask for the MQTT address instead of finding it."
    fi
}

# The unit files in system/ hold placeholders, so a rename touches one line here only.
install_units() {
    step "Services"
    local src dst
    # bridge and lessons, plus the update pair: a path unit that watches for a request
    # from the lesson screen and the root service it starts
    for src in "$REPO_DIR"/system/*.service "$REPO_DIR"/system/*.path; do
        dst="$UNIT_DIR/$APP-$(basename "$src")"
        sed -e "s|@APP@|$APP|g" \
            -e "s|@APP_TITLE@|$APP_TITLE|g" \
            -e "s|@OPT_DIR@|$OPT_DIR|g" \
            -e "s|@ETC_DIR@|$ETC_DIR|g" \
            -e "s|@VAR_DIR@|$VAR_DIR|g" \
            -e "s|@USER@|$SERVICE_USER|g" \
            "$src" > "$dst"
        chmod 644 "$dst"
    done
    systemctl daemon-reload
    systemctl enable --now "$APP-update.path" "$APP-reset.path" >/dev/null 2>&1 || true
    ok "${SERVICES[*]}, $APP-update.path for updates from the lesson screen, and $APP-reset.path for the full reset"
}

stop_services() {
    local s
    for s in "${SERVICES[@]}" "${OPTIONAL_SERVICES[@]}"; do
        systemctl stop "$s" 2>/dev/null || true
    done
}

start_services() {
    step "Starting"
    local s
    for s in "${SERVICES[@]}"; do
        systemctl enable "$s" >/dev/null 2>&1
        systemctl restart "$s" || true
    done
    for s in "${OPTIONAL_SERVICES[@]}"; do
        systemctl is-enabled --quiet "$s" 2>/dev/null && systemctl restart "$s" || true
    done
    sleep 3
    for s in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$s"; then
            ok "$s is running"
        else
            warn "$s did not start. Details: journalctl -u $s -n 30"
        fi
    done
}

# ----------------------------------------------------------------------
# Helpers shared by the terminal setup. Values travel in environment
# variables, never as arguments, so a password never shows up in ps.
# ----------------------------------------------------------------------
py() { "$VENV/bin/python" -; }

# prints OK, or a sentence that says what went wrong
mqtt_test() {
    MQ_HOST="$1" MQ_PORT="$2" MQ_USER="$3" MQ_PASS="$4" py <<'PY'
import os, threading
import paho.mqtt.client as mqtt

result, done = [], threading.Event()
def on_connect(client, userdata, flags, reason, props=None):
    result.append(reason)
    done.set()

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="piano-setup-test")
if os.environ["MQ_USER"]:
    c.username_pw_set(os.environ["MQ_USER"], os.environ["MQ_PASS"])
c.on_connect = on_connect
try:
    c.connect(os.environ["MQ_HOST"], int(os.environ["MQ_PORT"]), keepalive=10)
except OSError as e:
    print(f"The broker cannot be reached: {e}")
    raise SystemExit
c.loop_start()
done.wait(8)
c.loop_stop()
if not result:
    print("The broker did not answer within 8 seconds")
elif result[0].is_failure:
    print(f"The broker refused the connection: {result[0]}")
else:
    print("OK")
c.disconnect()
PY
}

# one line per MIDI input: the ALSA client name, which is what the services match on
midi_devices() {
    py <<'PY'
import mido
seen = []
try:
    names = mido.get_input_names()
except Exception:            # no ALSA sequencer: report no devices instead of a traceback
    names = []
for name in names:
    client = name.split(":")[0]
    if client not in seen and "Midi Through" not in client and "RtMidi" not in client:
        seen.append(client)
print("\n".join(seen))
PY
}

# waits for one key press on the device and prints its MIDI note number
wait_key() {
    MIDI_NAME="$1" WAIT_SECS="$2" py <<'PY'
import os, sys, time
import mido

name = os.environ["MIDI_NAME"]
ports = [n for n in mido.get_input_names() if n.split(":")[0] == name]
if not ports:
    sys.exit(2)
with mido.open_input(ports[0]) as port:
    for _ in port.iter_pending():
        pass
    end = time.time() + float(os.environ["WAIT_SECS"])
    while time.time() < end:
        for msg in port.iter_pending():
            if msg.type == "note_on" and msg.velocity > 0:
                print(msg.note)
                sys.exit(0)
        time.sleep(0.01)
sys.exit(1)
PY
}

note_name() {
    local names=(C C# D D# E F F# G G# A A# B)
    echo "${names[$(( $1 % 12 ))]}$(( $1 / 12 - 1 ))"
}

# ----------------------------------------------------------------------
# Settings file
# ----------------------------------------------------------------------
# Single quotes are read the same way by systemd and by bash, and nothing
# inside them is expanded, so passwords with $ or " are safe. A single
# quote itself cannot be stored, and the setup refuses it.
cfg_line() { printf "%s='%s'\n" "$1" "$2"; }

write_config() {
    local tmp
    # Drawn once and kept. Python, not a shell pipeline, so a short read cannot pass unnoticed.
    if [ -z "${C_MQTT_ADMIN_SECRET:-}" ]; then
        C_MQTT_ADMIN_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
        [ "${#C_MQTT_ADMIN_SECRET}" -ge 24 ] || die "The admin secret could not be created"
    fi
    tmp="$(mktemp "$ETC_DIR/.config.XXXXXX")"
    {
        echo "# $APP_TITLE settings. Written by the setup, safe to edit by hand."
        echo "# Apply changes with: sudo systemctl restart ${SERVICES[*]}"
        echo
        echo "# MQTT broker"
        cfg_line MQTT_HOST "$C_MQTT_HOST"
        cfg_line MQTT_PORT "$C_MQTT_PORT"
        cfg_line MQTT_USER "$C_MQTT_USER"
        cfg_line MQTT_PASS "$C_MQTT_PASS"
        echo "# Proves a command came from here and not from anyone else who reached the broker."
        echo "# The three commands that hand out the screen code, open the editing tools or"
        echo "# change the active profile answer only to a request carrying it."
        cfg_line MQTT_ADMIN_SECRET "$C_MQTT_ADMIN_SECRET"
        echo "# The recorder's own login: it sees every topic and writes to none."
        echo "# Empty means it falls back to the login above, which is where an outside"
        echo "# broker leaves it, since its logins are not this setup's to make."
        cfg_line MQTT_REC_USER "${C_MQTT_REC_USER:-}"
        cfg_line MQTT_REC_PASS "${C_MQTT_REC_PASS:-}"
        echo "# yes: the broker was installed by this program, and --purge removes its settings"
        cfg_line MQTT_LOCAL "$C_MQTT_LOCAL"
        echo
        echo "# Keyboard: the ALSA client name as shown by 'aconnect -i'"
        cfg_line MIDI_DEVICE "$C_MIDI_DEVICE"
        echo "# Lowest and highest key, as MIDI note numbers (middle C is 60)"
        cfg_line KEY_LOWEST "$C_KEY_LOWEST"
        cfg_line KEY_HIGHEST "$C_KEY_HIGHEST"
        echo "# The key that stops the alarm and anything playing"
        cfg_line STOP_KEY "$C_STOP_KEY"
        echo
        echo "# Paths and the lesson screen"
        cfg_line DATA_DIR "$VAR_DIR"
        cfg_line SONGS_DIR "$SONGS_DIR"
        cfg_line LESSON_UI_PORT "$LESSON_UI_PORT"
        echo "# en or he: the language of the lesson screen and Telegram, until it is changed on the screen"
        cfg_line UI_LANG "${C_UI_LANG:-en}"
        echo "# Optional: extra names the lesson screen answers to, comma separated (a reverse proxy, a DNS name)"
        cfg_line LESSON_HOSTS "${C_LESSON_HOSTS:-}"
        echo "# on makes the lesson screen ask for a 6-digit code once on each device (armonico code)"
        cfg_line SCREEN_CODE "${C_SCREEN_CODE:-on}"
        echo
        echo "# Optional keys, added by hand and kept when the setup runs again:"
        echo "#   LESSON_SHOW_KEY='37'   the key that shows the numbers again during a lesson (MIDI note)"
        echo "#   LESSON_EXIT_KEY='42'   the key that ends a lesson (MIDI note)"
        echo "#   MQTT_RECORD_DAYS='14'  days the MQTT recorder keeps its files (armonico record on)"
        if [ -n "${C_EXTRA:-}" ]; then
            printf '%s\n' "$C_EXTRA"
        fi
    } > "$tmp"
    chmod 640 "$tmp"
    chown "root:$SERVICE_USER" "$tmp"
    mv -f "$tmp" "$CONFIG"
    ok "Settings saved to $CONFIG"
}

# ----------------------------------------------------------------------
# Terminal setup
# ----------------------------------------------------------------------
ask() {             # ask VAR "question" "default"
    local reply
    read -r -p "  $2${3:+ [$3]}: " reply
    printf -v "$1" '%s' "${reply:-$3}"
}

ask_secret() {      # ask_secret VAR "question"
    local reply
    read -r -s -p "  $2: " reply
    echo
    printf -v "$1" '%s' "$reply"
}

no_quote() { [[ "$1" != *"'"* ]]; }

setup_cli() {
    step "MQTT broker"
    [ "$C_MQTT_LOCAL" = "yes" ] && ok "The local broker, 127.0.0.1:1883"
    local ha_login="no"
    if [ "$HA_DONE" = "yes" ] && [ "$C_MQTT_LOCAL" != "yes" ] && [ -n "${C_MQTT_USER:-}" ]; then
        ha_login="yes"          # the password was made by the installer and nobody knows it: nothing to ask
        ok "The broker Home Assistant gave: $C_MQTT_HOST:$C_MQTT_PORT, user $C_MQTT_USER"
    fi
    while [ "$C_MQTT_LOCAL" != "yes" ] && [ "$ha_login" != "yes" ]; do
        ask C_MQTT_HOST "Broker address" "${C_MQTT_HOST:-}"
        ask C_MQTT_PORT "Port" "${C_MQTT_PORT:-1883}"
        ask C_MQTT_USER "User name (empty for none)" "${C_MQTT_USER:-}"
        C_MQTT_PASS=""
        [ -n "$C_MQTT_USER" ] && ask_secret C_MQTT_PASS "Password"
        if [ -z "$C_MQTT_HOST" ] || ! [[ "$C_MQTT_PORT" =~ ^[0-9]+$ ]]; then
            warn "An address and a numeric port are required"
            continue
        fi
        if ! no_quote "$C_MQTT_USER$C_MQTT_PASS"; then
            warn "A single quote (') cannot be used in the user name or password"
            continue
        fi
        local res
        res="$(mqtt_test "$C_MQTT_HOST" "$C_MQTT_PORT" "$C_MQTT_USER" "$C_MQTT_PASS")"
        if [ "$res" = "OK" ]; then
            ok "Connected to $C_MQTT_HOST:$C_MQTT_PORT"
            break
        fi
        warn "$res"
    done

    step "Keyboard"
    [ -e /dev/snd/seq ] || die "The ALSA sequencer is missing (/dev/snd/seq), so no MIDI keyboard can be seen. On a virtual machine, pass the USB keyboard through to it and load the module: sudo modprobe snd-seq"
    local devices=() i choice
    while true; do
        mapfile -t devices < <(midi_devices)
        if [ "${#devices[@]}" -eq 0 ]; then
            warn "No MIDI keyboard found. Connect it by USB, switch it on, and press Enter."
            read -r _
            continue
        fi
        for i in "${!devices[@]}"; do
            printf '    %d) %s\n' "$((i + 1))" "${devices[$i]}"
        done
        ask choice "Which one" "1"
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#devices[@]}" ]; then
            C_MIDI_DEVICE="${devices[$((choice - 1))]}"
            break
        fi
        warn "Pick a number from the list"
    done

    # Pressing the two end keys proves the right device was picked and
    # measures the keyboard, so any size works: 25, 49, 61 or 88 keys.
    local low high
    while true; do
        echo "  Press the LOWEST key on the keyboard (30 seconds)"
        low="$(wait_key "$C_MIDI_DEVICE" 30)" || { warn "No key arrived. Check the cable and that the right device was picked."; continue; }
        ok "Lowest key: $(note_name "$low") (MIDI $low)"
        echo "  Press the HIGHEST key on the keyboard (30 seconds)"
        high="$(wait_key "$C_MIDI_DEVICE" 30)" || { warn "No key arrived."; continue; }
        ok "Highest key: $(note_name "$high") (MIDI $high)"
        if [ "$high" -gt "$low" ]; then
            break
        fi
        warn "The highest key came out lower than the lowest one. Once more."
    done
    C_KEY_LOWEST="$low"
    C_KEY_HIGHEST="$high"
    C_STOP_KEY="$low"
    ok "$((high - low + 1)) keys. The stop key is the lowest one, $(note_name "$low")."

    step "Language"
    wrap 2 "  The language of the lesson screen and the Telegram answers: en for English, he for Hebrew. It can be changed later on the lesson screen."
    while :; do
        ask C_UI_LANG "Language (en or he)" "${C_UI_LANG:-en}"
        case "$C_UI_LANG" in en|he) break ;; *) warn "Type en or he" ;; esac
    done

    step "Lesson screen code"
    wrap 2 "  Without the code, the lesson screen is open to everyone on the network: anyone who reaches it can start a lesson, play on the keyboard, read the practice history, download a full backup and ask for a system update. With the code on, each device enters a 6-digit code once and is then remembered for 14 days, renewed every time it is used. The code is shown by 'armonico code' or by /pianocode in Telegram, and it can be turned off later in the screen's settings or with 'armonico code off'."
    while :; do
        ask C_SCREEN_CODE "Ask for a code on every screen (on or off)" "${C_SCREEN_CODE:-on}"
        case "$C_SCREEN_CODE" in
            on|off) break ;;
            yes|true|1) C_SCREEN_CODE="on"; break ;;
            no|false|0) C_SCREEN_CODE="off"; break ;;
            *) warn "Type on or off" ;;
        esac
    done

    write_config
}

# ----------------------------------------------------------------------
# Home Assistant. It publishes itself on the network over mDNS, so the broker
# address can be filled in instead of typed. setup/find_ha.py does the looking
# and answers in "key<tab>value" lines. Its add-on does not publish itself and
# its users cannot be read from outside, so the address and the port are all
# that can be found: a user name and a password are still asked for.
# ----------------------------------------------------------------------
HA_FOUND="no"; HA_HOST=""; HA_WHERE=""; HA_VERSION=""; HA_NAME=""; HA_BROKER="no"; HA_URL_FOUND=""

find_home_assistant() {
    step "Home Assistant"
    local key value
    while IFS=$'\t' read -r key value; do
        case "$key" in
            found)   HA_FOUND="$value" ;;
            host)    HA_HOST="$value" ;;
            where)   HA_WHERE="$value" ;;
            version) HA_VERSION="$value" ;;
            name)    HA_NAME="$value" ;;
            broker)  HA_BROKER="$value" ;;
            url)     HA_URL_FOUND="$value" ;;
        esac
    done < <("$VENV/bin/python" "$OPT_DIR/setup/find_ha.py" 2>/dev/null || true)

    # a container on this machine that publishes no port still shows up in docker
    if [ "$HA_FOUND" != "yes" ] && command -v docker >/dev/null 2>&1 \
        && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'homeassistant'; then
        HA_FOUND="yes"; HA_HOST="127.0.0.1"; HA_WHERE="local"
        port_in_use 1883 && HA_BROKER="yes" || true
    fi

    if [ "$HA_FOUND" != "yes" ]; then
        ok "Not found. The setup asks for the broker address."
        return 0
    fi
    local what="$HA_HOST"
    [ -n "$HA_NAME" ] && what="$what ($HA_NAME)"
    [ -n "$HA_VERSION" ] && what="$what, version $HA_VERSION"
    [ "$HA_WHERE" = "local" ] && what="$what, on this machine"
    ok "Found: $what"
    if [ "$HA_BROKER" = "yes" ]; then
        ok "A broker answers on $HA_HOST:1883, so the Mosquitto add-on is installed"
    else
        warn "Nothing answers on $HA_HOST:1883, so the Mosquitto add-on is probably missing"
    fi
    # the address is offered as the default of the broker question. set -u is on and
    # nothing has set it yet on a first install, so it is read with a fallback
    [ -z "${C_MQTT_HOST:-}" ] && C_MQTT_HOST="$HA_HOST"
    return 0
}

# ----------------------------------------------------------------------
# Home Assistant from here. One access token from the Home Assistant profile page lets
# ha_setup.py do what is otherwise five manual steps: the Mosquitto app, the piano's user,
# the sensors, the helpers, the automations and the blueprints. The token is asked for with
# no echo, handed to that one process through its environment (never the command line),
# never written to disk, and its deletion is the last thing this says about it.
# ----------------------------------------------------------------------
HA_DONE="no"; HA_URL=""
ha_run() {          # ha_run COMMAND: ha_setup.py's lines, shown as ok / warn / fail
    # the admin secret goes into the Telegram automation, so /pianocode and /pianoprofile pass.
    # Read from the settings file, which is written by the time the package is sent.
    local admin
    admin="$(sed -n "s/^MQTT_ADMIN_SECRET='\\(.*\\)'$/\\1/p" "$CONFIG" 2>/dev/null | tail -1 || true)"
    HA_URL="$HA_URL" HA_TOKEN="$HA_TOKEN" MQTT_HOST="${C_MQTT_HOST:-}" MQTT_PORT="${C_MQTT_PORT:-1883}" \
        MQTT_USER="${C_MQTT_USER:-}" MQTT_PASS="${C_MQTT_PASS:-}" MQTT_ADMIN_SECRET="${admin:-${C_MQTT_ADMIN_SECRET:-}}" \
        "$VENV/bin/python" "$OPT_DIR/setup/ha_setup.py" "$1" 2>&1
}

connect_home_assistant() {
    [ -t 0 ] || return 0
    step "Home Assistant"
    wrap 2 "  This installer can set Home Assistant up for you: the MQTT broker, the piano's sensors and automations. It needs one access token, which you make in Home Assistant in 30 seconds. Without it, the setup screen walks you through the same steps by hand, with a button for each."
    local answer
    read -r -p "  Set Home Assistant up now? [Y/n] " answer
    case "$answer" in n|N|no|NO) ok "Skipped. The setup screen will guide you through it"; return 0 ;; esac
    local url="${HA_URL_FOUND:-${HA_HOST:+http://$HA_HOST:8123}}"
    if [ "$HA_FOUND" != "yes" ]; then
        wrap 2 "  Home Assistant was not found on its own (discovery over mDNS does not cross to another VLAN or subnet, and some routers block it). Type its address below and the setup goes on exactly the same way. If it lives on another VLAN or subnet: the router has to route between the two networks, and its firewall has to let this machine reach Home Assistant on port 8123 (the web page and its API) and port 1883 (the broker), in both directions for replies. Some switches and access points also block traffic between devices (client isolation): that has to be off for these two."
    fi
    echo
    echo "  1. In a browser, open Home Assistant, then your profile (your name, bottom left)."
    echo "  2. Security tab, scroll down to \"Long-lived access tokens\", Create token, name it armonico."
    echo "  3. Copy the token (it is shown once) and paste it here."
    echo "  Direct link: ${url:-http://<home assistant address>:8123}/profile/security"
    echo
    local text out tries=0
    while :; do
        ask HA_URL "Home Assistant address (as you open it in the browser)" "$url"
        [ -n "$HA_URL" ] || { ok "Skipped"; return 0; }
        if [ -z "$HA_TOKEN" ]; then
            ask_secret HA_TOKEN "Access token (hidden while you paste, empty to skip)"
            [ -n "$HA_TOKEN" ] || { ok "Skipped. The setup screen will guide you through it"; return 0; }
            # nothing shows while pasting, so say what arrived: the length and both ends
            local n part rep i
            for n in 8 7 6 5 4 3 2; do      # a terminal that kept every paste: the same token, repeated
                part=$(( ${#HA_TOKEN} / n ))
                [ $((part * n)) -eq ${#HA_TOKEN} ] && [ "$part" -ge 40 ] || continue
                rep=""; for ((i = 0; i < n; i++)); do rep+="${HA_TOKEN:0:part}"; done
                if [ "$rep" = "$HA_TOKEN" ]; then
                    warn "The token was pasted $n times in a row. One copy is used"
                    HA_TOKEN="${HA_TOKEN:0:part}"; break
                fi
            done
            ok "Token received: ${#HA_TOKEN} characters, ${HA_TOKEN:0:4}...${HA_TOKEN: -4}"
            [ "${#HA_TOKEN}" -ge 100 ] || warn "That is short for a token (they are about 180 characters). If the next step refuses it, copy it again, whole"
        fi
        if out="$(HA_URL="$HA_URL" HA_TOKEN="$HA_TOKEN" "$VENV/bin/python" "$OPT_DIR/setup/ha_setup.py" check 2>&1)" \
             && ! grep -q '^fail' <<<"$out"; then
            break
        fi
        text="$(grep '^fail' <<<"$out" | cut -f2- | head -1 || true)"
        warn "$text"
        tries=$((tries + 1))
        if [ "$tries" -ge 3 ]; then
            warn "Skipped. The setup screen will guide you through it"; HA_TOKEN=""; return 0
        fi
        case "$text" in
            *"refused the token"*) HA_TOKEN="" ;;      # the token is what to type again
            *) wrap 2 "  Use the address exactly as it opens in the browser, with its port: http://192.168.20.101:8123, or https://… when it has a certificate. Empty skips." ;;
        esac
        url="$HA_URL"
    done
    ok "Connected to Home Assistant $(grep '^version' <<<"$out" | cut -f2)"
    if [ "$HA_FOUND" != "yes" ]; then
        # typed by hand: the address is split out of the URL, and the setup carries on as if it was found
        HA_HOST="$(sed -E 's#^[a-zA-Z]+://##; s#[/?].*$##; s#:[0-9]+$##' <<<"$HA_URL")"
        HA_FOUND="yes"; HA_WHERE="network"
        [ -n "$HA_NAME" ] || HA_NAME="$(grep '^name' <<<"$out" | cut -f2 || true)"
        [ -n "$HA_VERSION" ] || HA_VERSION="$(grep '^version' <<<"$out" | cut -f2 || true)"
        HA_BROKER="no"
        "$VENV/bin/python" -c "import socket,sys; socket.create_connection((sys.argv[1], 1883), 2)" "$HA_HOST" 2>/dev/null && HA_BROKER="yes"
        [ -n "${C_MQTT_HOST:-}" ] || C_MQTT_HOST="$HA_HOST"
    fi
    if [ "$C_MQTT_LOCAL" != "yes" ]; then
        local key value
        wrap 2 "  This takes 1 to 5 minutes: the Mosquitto app is installed and started, and the broker is tested. Each step is shown as it finishes."
        local no_apps="no"
        while IFS=$'\t' read -r key value; do      # shown as they arrive, not after the end
            case "$key" in
                ok) ok "$value" ;;  warn) warn "$value" ;;
                fail) warn "$value"; case "$value" in *"no apps"*) no_apps="yes" ;; esac ;;
                host) C_MQTT_HOST="$value" ;; port) C_MQTT_PORT="$value" ;;
                user) C_MQTT_USER="$value" ;; pass) C_MQTT_PASS="$value" ;;
            esac
        done < <(ha_run broker || true)
        if [ -z "${C_MQTT_USER:-}" ]; then
            if [ "$no_apps" = "yes" ]; then
                # Container or Core: the broker is the setup's question, and once it is answered the
                # sensors, helpers, automations and blueprints are added over the same token
                warn "The setup asks for the broker. The sensors, automations and blueprints are added right after it"
                HA_DONE="package"; return 0
            fi
            warn "No broker came out of Home Assistant. The setup asks for it instead"
            HA_TOKEN=""; return 0
        fi
        HA_DONE="yes"
        HA_BROKER="yes"          # the Mosquitto app is installed and answered: the setup screen must not say it is missing
    else
        HA_DONE="yes"
    fi
}

# After the settings are saved and the broker answers: the sensors and automations.
# The optional Telegram bot. It asks before each step, and prints the manual way for any step declined.
telegram_step() {
    [ -t 0 ] || return 0
    HA_URL="$HA_URL" HA_TOKEN="$HA_TOKEN" SECRETS_FILE="${SECRETS_FILE:-}" "$VENV/bin/python" "$OPT_DIR/setup/telegram_setup.py" || true
}

# Tokens kept for updates: Home Assistant's, and Telegram's when the bot was set up. They live in a
# file of their own that only root reads (the settings file is readable by the armonico group), are
# read back as text and never run, and go only to Home Assistant and to Telegram.
SECRETS="$ETC_DIR/secrets.env"
secret_get() { { sed -n "s/^$1='\(.*\)'\$/\1/p" "$SECRETS" 2>/dev/null || true; } | tail -1; }
secret_put() {          # secret_put KEY VALUE
    no_quote "$2" || return 0
    local tmp
    tmp="$(mktemp)"
    [ -f "$SECRETS" ] && grep -v "^$1=" "$SECRETS" > "$tmp" || true
    printf "%s='%s'\n" "$1" "$2" >> "$tmp"
    install -m 600 -o root -g root "$tmp" "$SECRETS"
    rm -f "$tmp"
}
yes_no() {              # yes_no "question" default(y|n)
    local reply hint="[y/N]"
    [ "$2" = "y" ] && hint="[Y/n]"
    read -r -p "  $1 $hint " reply || reply=""
    reply="${reply:-$2}"
    case "$reply" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
ask_keep_tokens() {
    KEEP_TOKENS="no"
    if [ -t 0 ]; then
        wrap 2 "  Keep the tokens, so that future updates refresh the Home Assistant parts (sensors, automations, blueprints) and the Telegram bot's profile by themselves? They are stored in $SECRETS, readable by root only, and used only to talk to Home Assistant and Telegram. If not, an update leaves those parts as they are, and you refresh them with: sudo $APP integrations (it asks for new tokens)."
        yes_no "Keep the tokens for updates?" n && KEEP_TOKENS="yes"
    fi
    if [ "$KEEP_TOKENS" = "yes" ]; then
        [ -n "$HA_TOKEN" ] && { secret_put HA_URL "$HA_URL"; secret_put HA_TOKEN "$HA_TOKEN"; }
        ok "Tokens kept in $SECRETS"
    else
        rm -f "$SECRETS"        # an earlier answer of yes is withdrawn
    fi
}

finish_home_assistant() {
    { [ "$HA_DONE" = "yes" ] || [ "$HA_DONE" = "package" ]; } && [ -n "$HA_TOKEN" ] || return 0
    step "Home Assistant: sensors and automations"
    local out kind text
    ask_keep_tokens
    while IFS=$'\t' read -r kind text; do
        case "$kind" in ok) ok "$text" ;; warn) warn "$text" ;; fail) warn "$text. Do it by hand: docs/home-assistant.md" ;; esac
    done < <(ha_run package || true)
    SECRETS_FILE=""
    [ "$KEEP_TOKENS" = "yes" ] && SECRETS_FILE="$SECRETS"
    telegram_step
    HA_TOKEN=""
    echo
    if [ "$KEEP_TOKENS" = "yes" ]; then
        wrap 2 "  The Home Assistant token stays in Home Assistant too, because updates use it. To stop that: delete it there (Profile, Security, Long-lived access tokens) and run: sudo $APP forget-tokens"
    else
        wrap 2 "  Last step in Home Assistant: delete the token, it is not needed anymore. Profile, Security, Long-lived access tokens, the armonico token, Delete."
    fi
}

# An update (no terminal) or "armonico integrations" (a terminal): the Home Assistant parts and the
# Telegram bot's profile, from the kept tokens, or from new ones when none are kept.
refresh_integrations() {
    local url tok tg
    url="$(secret_get HA_URL)"; tok="$(secret_get HA_TOKEN)"; tg="$(secret_get TELEGRAM_TOKEN)"
    if [ -z "$tok" ] && [ -z "$tg" ]; then
        if [ ! -t 0 ]; then
            ok "Home Assistant and Telegram were not touched: no tokens are kept. To refresh them: sudo $APP integrations"
            return 0
        fi
        step "Home Assistant and Telegram"
        wrap 2 "  Nothing is kept from the install, so this needs new tokens. Enter the ones you want refreshed; empty skips."
        local out text tries=0
        if yes_no "Refresh the Home Assistant parts?" y; then
            while [ "$tries" -lt 3 ]; do
                ask url "Home Assistant address (as you open it in the browser)" "${C_MQTT_HOST:+http://$C_MQTT_HOST:8123}"
                [ -n "$url" ] || break
                ask_secret tok "Long-lived access token (hidden)"
                [ -n "$tok" ] || break
                if out="$(HA_URL="$url" HA_TOKEN="$tok" "$VENV/bin/python" "$OPT_DIR/setup/ha_setup.py" check 2>&1)" \
                   && ! grep -q '^fail' <<<"$out"; then break; fi
                text="$(grep '^fail' <<<"$out" | cut -f2- | head -1 || true)"; warn "${text:-Home Assistant did not answer}"
                tok=""; tries=$((tries + 1))
            done
        fi
        if yes_no "Refresh the Telegram bot's profile (commands, description, picture)?" n; then
            ask_secret tg "Bot token from BotFather (hidden)"
        fi
        { [ -n "$tok" ] || [ -n "$tg" ]; } || { ok "Nothing to refresh"; return 0; }
        HA_URL="$url"; HA_TOKEN="$tok"
        run_refresh "$url" "$tok" "$tg"
        ask_keep_tokens_for "$url" "$tok" "$tg"
        HA_TOKEN=""
        return 0
    fi
    step "Home Assistant and Telegram: updating from the kept tokens"
    run_refresh "$url" "$tok" "$tg"
}

run_refresh() {         # run_refresh URL HA_TOKEN TELEGRAM_TOKEN
    local kind text
    if [ -n "$2" ]; then
        HA_URL="$1" HA_TOKEN="$2"
        while IFS=$'\t' read -r kind text; do
            case "$kind" in ok) ok "$text" ;; warn) warn "$text" ;; fail) warn "$text. Run: sudo $APP integrations" ;; esac
        done < <(ha_run package || true)
    fi
    if [ -n "$3" ]; then
        TELEGRAM_TOKEN="$3" "$VENV/bin/python" "$OPT_DIR/setup/telegram_setup.py" --refresh || warn "The Telegram bot was not updated. Run: sudo $APP integrations"
    fi
}

ask_keep_tokens_for() {  # asks, then keeps what was just used
    if yes_no "Keep these tokens, so updates refresh them by themselves? (stored in $SECRETS, root only)" n; then
        [ -n "$2" ] && { secret_put HA_URL "$1"; secret_put HA_TOKEN "$2"; }
        [ -n "$3" ] && secret_put TELEGRAM_TOKEN "$3"
        ok "Tokens kept in $SECRETS"
    fi
}
HA_TOKEN=""

# ----------------------------------------------------------------------
# MQTT broker. The two services talk to each other over MQTT, so a broker is
# needed even without Home Assistant. Asked once, on the first install only.
# ----------------------------------------------------------------------
C_MQTT_LOCAL="no"
LOCAL_BROKER_CONF="/etc/mosquitto/conf.d/$APP.conf"
LOCAL_BROKER_USERS="/etc/mosquitto/$APP.passwd"
LOCAL_BROKER_ACL="/etc/mosquitto/$APP.acl"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# The real question is not "does something answer on this port" but "can this program
# listen on it", and those are not the same. A service bound to one address of this
# machine answers nothing on 127.0.0.1, yet still blocks a listener on every address.
# So the check makes the same move the server will make: bind 0.0.0.0 on that port and
# see what happens. The socket is closed at once, and SO_REUSEADDR is deliberately left
# off, so this asks exactly the question the server asks. HTTP is TCP, and so is this.
port_in_use() {
    "$PYTHON_BIN" -c '
import socket, sys
sock = socket.socket()
try:
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)          # taken
finally:
    sock.close()
sys.exit(1)              # free
' "$1" 2>/dev/null
}

# A home machine often runs plenty of other things, and 8098 and 8099 are ordinary numbers
# that something else may already have. Failing to listen at the last moment leaves an
# install half done with nothing said, so the port is settled before anything opens it.
# HTTP is TCP, and so is the check.
who_has_port() {
    local p="$1" who=""
    if command -v ss >/dev/null 2>&1; then
        who="$(ss -ltnp "sport = :$p" 2>/dev/null | awk 'NR>1 {print $NF}' | head -1)"
    fi
    [ -n "$who" ] && echo "$who"
}

# first free port at or after $1, looking at most 20 ahead
free_port_from() {
    local p="$1" tries=0
    while [ "$tries" -lt 20 ]; do
        port_in_use "$p" || { echo "$p"; return 0; }
        p=$((p + 1)); tries=$((tries + 1))
    done
    return 1
}

# The address of the interface that actually carries the network. `hostname -I` prints
# every address in no useful order, so on a machine with docker, a VPN or a second card it
# often printed one nothing outside can reach.
lan_ip() {
    local ip=""
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
    [ -n "$ip" ] || ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | head -1)"
    printf '%s' "$ip"
}

settle_port() {          # settle_port VAR_NAME "what it is for"
    local var="$1" what="$2" want="${!1}" free="" answer=""
    port_in_use "$want" || return 0
    local who; who="$(who_has_port "$want")"
    warn "Port $want is already taken on this machine${who:+ by $who}, and $what needs one."
    free="$(free_port_from "$((want + 10))")" || die "No free port was found near $want. Free one, or set $var by hand."
    if [ -t 0 ]; then
        read -r -p "  Use port $free instead? Enter another number, or press Enter for $free: " answer
        case "$answer" in
            "") ;;
            *[!0-9]*) warn "Not a number, using $free" ;;
            *) if [ "$answer" -ge 1 ] && [ "$answer" -le 65535 ] && ! port_in_use "$answer"; then
                   free="$answer"
               else
                   warn "Port $answer is not usable, using $free"
               fi ;;
        esac
    fi
    printf -v "$var" '%s' "$free"
    ok "$what will use port $free"
}

choose_broker() {
    step "MQTT broker"
    echo "  The piano needs an MQTT broker, also without Home Assistant:"
    echo "  the bridge and the lessons talk to each other through it."
    echo
    echo "    1) I have one: Home Assistant's Mosquitto app, or another broker"
    echo "       That broker belongs to someone else, so this setup does not touch its"
    echo "       rules. Which client may reach which topic is decided there, and in the"
    echo "       Home Assistant add-on that decision is not enforced at all today."
    echo "       The piano guards its own three sensitive commands instead."
    echo
    echo "    2) I have none: install Mosquitto on this machine"
    echo "       The setup owns this broker: it writes a rule file, holds the piano to"
    echo "       its own branch of the topics, gives the recorder a read-only login,"
    echo "       and keeps port 1883 on this machine (only the status screen's port 9001 is open)."
    echo
    if [ "$HA_FOUND" = "yes" ] && [ "$HA_BROKER" = "yes" ]; then
        ok "Home Assistant at $HA_HOST answers on port 1883: choice 1, with a user of that broker"
    elif [ "$HA_FOUND" = "yes" ]; then
        warn "Home Assistant was found at $HA_HOST, but nothing answers on its port 1883"
    fi
    if [ ! -t 0 ]; then
        ok "No terminal to ask in: using an existing broker"; return 0
    fi
    local answer
    read -r -p "  Choose 1 or 2 [1]: " answer
    [ "$answer" = "2" ] || return 0
    # a broker that already runs here has its own listeners and users; a second
    # listener on the same port would stop it from starting
    if port_in_use 1883 || systemctl is-active --quiet mosquitto 2>/dev/null; then
        warn "A broker already listens on port 1883 of this machine, so none is installed."
        warn "In the setup, use 127.0.0.1 with a user of that broker."
        return 0
    fi
    install_local_broker
}

# Mosquitto on this machine. Two logins, because two jobs that have no reason to share
# one: "piano" acts and is held to its own branch of the topics, "piano-rec" watches
# everything and can write nothing. A stolen recorder login listens; it cannot command.
# The broker stays on this machine unless the setup is told another machine needs it.
# Passwords are random, go to config.env only, and are written to the users file in
# plain text and hashed there with -U, so neither ever appears on a command line.
new_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(18))'; }

install_local_broker() {
    apt-get install -y -qq --no-install-recommends mosquitto >/dev/null
    C_MQTT_HOST="127.0.0.1"; C_MQTT_PORT="1883"; C_MQTT_USER="piano"
    C_MQTT_PASS="$(new_secret)"
    C_MQTT_REC_USER="piano-rec"
    C_MQTT_REC_PASS="$(new_secret)"
    [ "${#C_MQTT_PASS}" -ge 16 ] && [ "${#C_MQTT_REC_PASS}" -ge 16 ] \
        || die "The broker passwords could not be created"

    # Port 1883 stays on this machine: the piano, the recorder and the command line are all here.
    # Port 9001 (websockets) answers on the network, because the status screen runs in a browser
    # on another device and connects from there. Both need a login and are held to the rules below.
    local bind="127.0.0.1 "
    ok "The broker answers this machine on 1883, and the status screen's websockets on the network (9001)"

    ( umask 077; printf 'piano:%s\npiano-rec:%s\n' "$C_MQTT_PASS" "$C_MQTT_REC_PASS" > "$LOCAL_BROKER_USERS" )
    mosquitto_passwd -U "$LOCAL_BROKER_USERS"
    chown mosquitto:mosquitto "$LOCAL_BROKER_USERS"
    chmod 0700 "$LOCAL_BROKER_USERS"

    {
        echo "# $APP_TITLE: who may touch what. Removed by install.sh --purge."
        echo "# piano acts, and only inside its own branch."
        echo "user piano"
        echo "topic readwrite piano/#"
        echo
        echo "# The recorder watches the whole broker and writes nothing."
        echo "user piano-rec"
        echo "topic read #"
    } > "$LOCAL_BROKER_ACL"
    chown mosquitto:mosquitto "$LOCAL_BROKER_ACL"
    chmod 0640 "$LOCAL_BROKER_ACL"

    {
        echo "# $APP_TITLE: the local broker. Removed by install.sh --purge."
        echo "per_listener_settings true"
        echo "listener 1883 ${bind}"
        echo "allow_anonymous false"
        echo "password_file $LOCAL_BROKER_USERS"
        echo "acl_file $LOCAL_BROKER_ACL"
        echo
        echo "# the touch screen (screen/piano-status.html) speaks MQTT over websockets"
        echo "listener 9001"
        echo "protocol websockets"
        echo "allow_anonymous false"
        echo "password_file $LOCAL_BROKER_USERS"
        echo "acl_file $LOCAL_BROKER_ACL"
    } > "$LOCAL_BROKER_CONF"
    systemctl enable mosquitto >/dev/null 2>&1 || true
    systemctl restart mosquitto
    local res
    res="$(mqtt_test "$C_MQTT_HOST" "$C_MQTT_PORT" "$C_MQTT_USER" "$C_MQTT_PASS")"
    [ "$res" = "OK" ] || die "The local broker did not accept the login: $res. Details: journalctl -u mosquitto -n 20"
    res="$(mqtt_test "$C_MQTT_HOST" "$C_MQTT_PORT" "$C_MQTT_REC_USER" "$C_MQTT_REC_PASS")"
    [ "$res" = "OK" ] || die "The local broker did not accept the recorder login: $res"
    C_MQTT_LOCAL="yes"
    ok "Mosquitto installed, users piano and piano-rec, rules in $LOCAL_BROKER_ACL"
}

# ----------------------------------------------------------------------
# Browser setup
# ----------------------------------------------------------------------
setup_web() {
    step "Setup screen"
    local code ip
    local again="sudo $0"
    [ "$ACTION" = "reconfigure" ] && again="$again --reconfigure"
    settle_port SETUP_PORT "the setup screen"
    code="$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )))"
    ip="$(lan_ip)"
    echo
    echo "  Open in a browser on any device in the same network:"
    echo
    echo "      http://${ip:-<this machine>}:$SETUP_PORT"
    echo
    echo "  One-time code:  $code"
    echo
    echo "  After saving, the setup continues here. Unused, the screen stops"
    echo "  after $((SETUP_TIMEOUT / 60)) minutes."
    echo "  No browser around? Stop with Ctrl+C and run: $again --cli"
    echo

    # The screen only collects and tests the values. It hands them back in a
    # temporary file, one "KEY<tab>value" per line, and this script writes the
    # settings file, so its format is defined in one place: write_config.
    #
    # The screen runs as $SERVICE_USER, not as root. It is an HTTP server open to
    # the network for as long as the setup lasts, and nothing it does needs root:
    # a MIDI port comes from the audio group, the broker test is network only, and
    # the answer goes into a folder of its own that belongs to that user. So a hole
    # in the screen reaches that user and no further.
    local rc=0 result key value defaults="$CONFIG" work
    local -a cmd
    work="$(mktemp -d)"
    chown "$SERVICE_USER:$SERVICE_USER" "$work"
    chmod 700 "$work"
    result="$work/result"
    if [ "$C_MQTT_LOCAL" = "yes" ] || [ "$HA_DONE" = "yes" ]; then
        # the screen shows the local broker filled in; its password stays on this machine.
        # The settings file comes first, so the rest of the answers stay the defaults of a
        # second run; the broker lines are appended, and a later line wins.
        # It holds the broker password, so the screen may read it and not write it.
        defaults="$work/defaults"
        : > "$defaults"
        chown "root:$SERVICE_USER" "$defaults"
        chmod 640 "$defaults"
        [ -r "$CONFIG" ] && cat "$CONFIG" > "$defaults"
        { cfg_line MQTT_HOST "$C_MQTT_HOST"; cfg_line MQTT_PORT "$C_MQTT_PORT"
          cfg_line MQTT_USER "$C_MQTT_USER"; cfg_line MQTT_PASS "$C_MQTT_PASS"; } >> "$defaults"
    fi
    cmd=("$VENV/bin/python" "$OPT_DIR/setup/setup_web.py"
         --result "$result" --defaults "$defaults" --port "$SETUP_PORT"
         --timeout "$SETUP_TIMEOUT" --title "$APP_TITLE" --repo "$REPO" --lesson-port "$LESSON_UI_PORT"
         --ha-host "$HA_HOST" --ha-where "$HA_WHERE" --ha-version "$HA_VERSION"
         --ha-name "$HA_NAME" --ha-broker "$HA_BROKER" --ha-done "$HA_DONE"
         --local-broker "$C_MQTT_LOCAL")
    # runuser keeps the environment, so the one-time code stays out of the command
    # line, where any user on the machine could read it with ps.
    if command -v runuser >/dev/null 2>&1 && id "$SERVICE_USER" >/dev/null 2>&1; then
        cmd=(runuser -u "$SERVICE_USER" -- "${cmd[@]}")
    else
        warn "runuser was not found, so the setup screen runs with full rights until it closes"
    fi
    SETUP_CODE="$code" "${cmd[@]}" || rc=$?
    case "$rc" in
        0) ;;
        3) rm -rf "$work"; die "The setup screen timed out. Run again: sudo $0 --reconfigure" ;;
        *) rm -rf "$work"; die "The setup screen stopped (code $rc). Try the terminal instead: $again --cli" ;;
    esac
    while IFS=$'\t' read -r key value; do
        case "$key" in
            MQTT_HOST|MQTT_PORT|MQTT_USER|MQTT_PASS|MIDI_DEVICE|KEY_LOWEST|KEY_HIGHEST|STOP_KEY|UI_LANG|LESSON_HOSTS|SCREEN_CODE)
                no_quote "$value" || die "The setup screen returned a value with a single quote"
                printf -v "C_$key" '%s' "$value" ;;
        esac
    done < "$result"
    rm -rf "$work"
    write_config
}

# On --reconfigure the current values become the defaults of every question.
load_existing() {
    [ -r "$CONFIG" ] || return 0
    local MQTT_HOST="" MQTT_PORT="" MQTT_USER="" MQTT_PASS="" MIDI_DEVICE="" MQTT_LOCAL="no" UI_LANG="en" LESSON_HOSTS="" SCREEN_CODE="on" MQTT_ADMIN_SECRET="" MQTT_REC_USER="" MQTT_REC_PASS=""
    # LESSON_UI_PORT is not local: a port that was moved because something else had 8099 has to
    # reach the rest of the installer (the summary, a new settings file), not end with this function
    local keep_port="$LESSON_UI_PORT"
    # shellcheck source=/dev/null
    . "$CONFIG"
    C_MQTT_LOCAL="$MQTT_LOCAL"
    C_MQTT_HOST="$MQTT_HOST"; C_MQTT_PORT="$MQTT_PORT"
    C_MQTT_USER="$MQTT_USER"; C_MQTT_PASS="$MQTT_PASS"
    C_MIDI_DEVICE="$MIDI_DEVICE"
    C_UI_LANG="$UI_LANG"
    C_LESSON_HOSTS="$LESSON_HOSTS"
    C_SCREEN_CODE="$SCREEN_CODE"
    C_MQTT_ADMIN_SECRET="$MQTT_ADMIN_SECRET"
    C_MQTT_REC_USER="$MQTT_REC_USER"; C_MQTT_REC_PASS="$MQTT_REC_PASS"
    case "$LESSON_UI_PORT" in ""|*[!0-9]*) LESSON_UI_PORT="$keep_port" ;; esac
    # lines added by hand (LESSON_SHOW_KEY and the like) go back into the rewritten file as they are
    C_EXTRA="$(grep -E "^[A-Z_]+='" "$CONFIG" \
        | grep -vE "^(MQTT_HOST|MQTT_PORT|MQTT_USER|MQTT_PASS|MQTT_LOCAL|MIDI_DEVICE|KEY_LOWEST|KEY_HIGHEST|STOP_KEY|DATA_DIR|SONGS_DIR|LESSON_UI_PORT|UI_LANG|LESSON_HOSTS|SCREEN_CODE|MQTT_ADMIN_SECRET|MQTT_REC_USER|MQTT_REC_PASS)=" || true)"
}

# Lessons, reviews, the streak and the alarm all count days on this machine's clock.
# The time zone is the system's own setting (timedatectl), kept after every restart.
check_timezone() {
    step "Time zone"
    local tz answer
    tz="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo unknown)"
    wrap 2 "  Lessons, reviews, the practice streak and the alarm clock follow this machine's clock. With a wrong time zone every day starts at the wrong hour, so reviews come early or late and the alarm rings at another time."
    echo "  Now: $tz, the time here is $(date '+%H:%M')"
    if [ ! -t 0 ]; then
        case "$tz" in UTC|Etc/UTC|unknown) warn "The time zone is $tz. To change it: sudo timedatectl set-timezone Asia/Jerusalem" ;; esac
        return 0
    fi
    while true; do
        read -r -p "  Enter keeps it, or type another, for example Asia/Jerusalem: " answer
        [ -z "$answer" ] && { ok "Time zone kept: $tz"; return 0; }
        if timedatectl list-timezones 2>/dev/null | grep -qx "$answer"; then
            timedatectl set-timezone "$answer"
            ok "Time zone set to $answer, now $(date '+%H:%M'). It is saved in the system and stays after restarts"
            return 0
        fi
        warn "Unknown time zone: $answer. The full list: timedatectl list-timezones"
    done
}

configure() {
    load_existing
    [ -n "${LESSON_UI_PORT_SET:-}" ] || settle_port LESSON_UI_PORT "the lesson screen"
    LESSON_UI_PORT_SET=1
    check_timezone
    find_home_assistant
    [ "$ACTION" = "install" ] && connect_home_assistant
    [ "$ACTION" = "install" ] && [ "$HA_DONE" != "yes" ] && choose_broker
    if [ "$MODE" = "cli" ]; then
        setup_cli
    else
        setup_web
    fi
}

# ----------------------------------------------------------------------
# Removal
# ----------------------------------------------------------------------
# The data folders as the services see them. config.env may point them
# somewhere else than the defaults, and --purge has to find them there too.
DATA_DIRS=()
find_data_dirs() {
    local d cfg_data="" cfg_songs=""
    if [ -r "$CONFIG" ]; then
        cfg_data="$(  (set +u; . "$CONFIG"; printf '%s' "${DATA_DIR:-}")  2>/dev/null || true)"
        cfg_songs="$( (set +u; . "$CONFIG"; printf '%s' "${SONGS_DIR:-}") 2>/dev/null || true)"
    fi
    DATA_DIRS=("$VAR_DIR")
    for d in "$cfg_data" "$cfg_songs"; do
        [ -n "$d" ] || continue
        d="$(realpath -m "$d")"
        case "$d/" in "$VAR_DIR"/*) continue ;; esac      # already inside the default folder
        # never a system folder, even if config.env says so by mistake
        case "$d" in
            /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/lib)
                warn "Skipped $d: config.env points there, and it is not a folder this program owns"; continue ;;
        esac
        [ "$(dirname "$d")" = "/home" ] && { warn "Skipped $d: a home folder is never removed"; continue; }
        DATA_DIRS+=("$d")
    done
}

count_files() { find "$@" -type f 2>/dev/null | wc -l; }

# The local broker goes completely only when this program installed the package
# and no other program keeps its own settings for it.
BROKER_GOES="no"
PKG_REMOVE=()
plan_packages() {
    local p keep others
    PKG_REMOVE=(); BROKER_GOES="no"
    others="$(find /etc/mosquitto/conf.d -mindepth 1 -maxdepth 1 ! -name README ! -name "$APP.conf" 2>/dev/null || true)"
    # The settings file of this program is not required: after a plain --uninstall it is gone,
    # and the package list that was kept says the broker was installed here
    if [ -z "$others" ] && grep -qx 'mosquitto\(:.*\)\{0,1\}' "$PKG_LIST" 2>/dev/null; then
        BROKER_GOES="yes"
    fi
    [ -r "$PKG_LIST" ] || return 0
    local cand=()
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii' || continue
        for keep in "${PKG_KEEP[@]}"; do [ "${p%%:*}" = "$keep" ] && continue 2; done
        [ "${p%%:*}" = "mosquitto" ] && [ "$BROKER_GOES" = "no" ] && continue
        [ "$(dpkg-query -W -f='${Essential}' "$p" 2>/dev/null)" = "yes" ] && continue
        cand+=("$p")
    done < "$PKG_LIST"
    # A package goes only when removing it takes nothing outside the list with it:
    # something installed later may depend on it. Checked with apt's dry run, and
    # repeated until nothing more drops out.
    local changed=1 extra
    while [ "$changed" -eq 1 ] && [ "${#cand[@]}" -gt 0 ]; do
        changed=0
        extra="$(outside_removals "${cand[@]}")"
        [ -z "$extra" ] && break
        local kept=()
        for p in "${cand[@]}"; do
            if [ -n "$(outside_removals_of "$p" "${cand[@]}")" ]; then changed=1; else kept+=("$p"); fi
        done
        cand=("${kept[@]}")
        # nothing single dropped out but the set still takes more: remove nothing at all
        [ "$changed" -eq 0 ] && cand=()
    done
    PKG_REMOVE=("${cand[@]}")
}

# the packages apt would remove for "purge ARGS" that are not among SET (names without :arch)
_apt_removals() { { apt-get -s purge "$@" 2>/dev/null || true; } | awk '/^(Purg|Remv) / {sub(/:.*/, "", $2); print $2}' | LC_ALL=C sort -u; }
_names() { printf '%s\n' "$@" | sed 's/:.*//' | LC_ALL=C sort -u; }
outside_removals() { LC_ALL=C comm -23 <(_apt_removals "$@") <(_names "$@"); }
outside_removals_of() { local one="$1"; shift; LC_ALL=C comm -23 <(_apt_removals "$one") <(_names "$@"); }

remove_packages() {
    [ "${#PKG_REMOVE[@]}" -gt 0 ] || { ok "No system packages to remove"; return 0; }
    if apt-get purge -y -qq "${PKG_REMOVE[@]}" >/dev/null 2>&1; then
        ok "${#PKG_REMOVE[@]} system packages this program installed removed"
    else
        warn "The system packages could not be removed. By hand: sudo apt-get purge ${PKG_REMOVE[*]}"
    fi
}

# The folder the project was unpacked in goes too, with whatever else was put in it, unless it is a
# system or home folder, one of the person's own folders, or a git checkout with uncommitted changes.
REPO_GOES="no"
REPO_KEEP_WHY=""
plan_repo_dir() {
    local d="$REPO_DIR" f
    REPO_GOES="no"; REPO_KEEP_WHY=""
    if [ "$d" = "$OPT_DIR" ]; then
        # run by the armonico command: the folder the project was unpacked in was recorded at install
        d="$(head -1 "$ETC_DIR/source_dir" 2>/dev/null || true)"
        [ -n "$d" ] && [ -d "$d" ] || return 0
    fi
    d="$(realpath -m "$d")"
    REPO_DIR="$d"
    case "$d" in
        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/usr/*|/var|/var/*)
            REPO_KEEP_WHY="it is a system folder"; return 0 ;;
    esac
    [ "$(dirname "$d")" = "/home" ] && { REPO_KEEP_WHY="it is a home folder"; return 0; }
    for f in install.sh VERSION lessons/piano_game.py; do
        [ -f "$d/$f" ] || { REPO_KEEP_WHY="it does not look like a copy of this project"; return 0; }
    done
    # the project was unpacked straight into a folder that holds a person's own things
    case "$(basename "$d")" in Desktop|Documents|Downloads|Pictures|Music|Videos|Public|Templates)
        REPO_KEEP_WHY="it looks like one of your own folders. Unpack the project in a folder of its own"; return 0 ;;
    esac
    for f in .bashrc .profile .ssh .config Documents; do
        [ -e "$d/$f" ] && { REPO_KEEP_WHY="it holds $f, so it is not a folder made for the project"; return 0; }
    done
    # Whatever else was put in that folder goes with it: it is the folder the project was unpacked in.
    # Only a git checkout with changes that were never committed stays.
    if [ -d "$d/.git" ] && command -v git >/dev/null \
       && [ -n "$(git -C "$d" status --porcelain 2>/dev/null)" ]; then
        REPO_KEEP_WHY="it has local changes in git"; return 0
    fi
    REPO_GOES="yes"
}

# Shows exactly what --purge is about to delete and waits for the word DELETE.
confirm_purge() {
    local d recs songs size
    step "About to remove $APP_TITLE and ALL of its data"
    recs=0; songs=0
    for d in "${DATA_DIRS[@]}"; do
        [ -d "$d" ] || continue
        recs=$((recs + $(find "$d" -path '*/recordings/*' -name '*.json' -type f 2>/dev/null | wc -l)))
        songs=$((songs + $(find "$d" -name '*.mid' -not -path '*/recordings/*' -type f 2>/dev/null | wc -l)))
        size="$(du -sh "$d" 2>/dev/null | cut -f1)"
        echo "  ${C_RED}✘${C_OFF} $d  (${size:-0}, $(count_files "$d") files)"
    done
    echo "  ${C_RED}✘${C_OFF} $ETC_DIR  (settings and the MQTT password)"
    echo "  ${C_RED}✘${C_OFF} $OPT_DIR  (the program)"
    echo "  ${C_RED}✘${C_OFF} the services ${SERVICES[*]} ${OPTIONAL_SERVICES[*]}, the user $SERVICE_USER, /run/$APP"
    echo "  ${C_RED}✘${C_OFF} the retained piano/ messages on the MQTT broker"
    if [ "$BROKER_GOES" = "yes" ]; then
        echo "  ${C_RED}✘${C_OFF} the local broker this program installed (Mosquitto and its data, removed completely)"
    elif [ -f "$LOCAL_BROKER_CONF" ]; then
        echo "  ${C_RED}✘${C_OFF} this program's settings in the local broker (Mosquitto itself stays)"
    fi
    [ "${#PKG_REMOVE[@]}" -gt 0 ] && echo "  ${C_RED}✘${C_OFF} system packages this program installed: $(wrap 6 "${PKG_REMOVE[*]}")"
    [ "$REPO_GOES" = "yes" ] && echo "  ${C_RED}✘${C_OFF} $REPO_DIR  (the copy of the project this installer runs from)"
    echo
    printf '  %sThis deletes %s lesson recordings, %s songs (including every uploaded one),\n' "$C_YELLOW" "$recs" "$songs"
    printf '  the lesson progress and history, the shortcuts and the settings.\n'
    printf '  It cannot be undone.%s\n' "$C_OFF"
    echo "  To keep a copy first:"
    echo "    (umask 077; sudo tar czf ~/$APP-backup.tar.gz ${DATA_DIRS[*]} $ETC_DIR)"
    echo
    [ "$ASSUME_YES" = "yes" ] && { warn "--yes given, not asking"; return 0; }
    [ -t 0 ] || die "No terminal to ask in, so nothing was removed. To purge without the question: sudo $0 --purge --yes"
    local answer
    read -r -p "  Type DELETE to remove everything, anything else cancels: " answer
    [ "$answer" = "DELETE" ] || die "Cancelled, nothing was removed"
}

# The bridge keeps its state on the broker as retained messages, so Home Assistant
# would go on showing it after the program is gone. An empty retained message
# deletes one. The login comes from config.env through the environment, never argv.
clear_retained_mqtt() {
    local res
    if [ ! -r "$CONFIG" ]; then
        warn "Retained MQTT messages not cleared (no settings left). By hand: mosquitto_sub -t 'piano/#' --retained-only -v"
        return 0
    fi
    if [ ! -x "$VENV/bin/python" ]; then
        clear_retained_mqtt_clients       # the program was already removed with --uninstall
        return 0
    fi
    res="$( (set +u; set -a; . "$CONFIG"; set +a; py) <<'EOF' 2>&1
import os, time
import paho.mqtt.client as mqtt
topics = set()
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="piano-purge")
if os.environ.get("MQTT_USER"):
    c.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS", ""))
# the piano's own topics, and the sensors it announced to Home Assistant (the others are not its)
mine = lambda t: t.startswith("piano/") or (t.startswith("homeassistant/") and "/piano_" in t and t.endswith("/config"))
c.on_message = lambda cl, u, m: topics.add(m.topic) if m.retain and m.payload and mine(m.topic) else None
try:
    c.connect(os.environ.get("MQTT_HOST", ""), int(os.environ.get("MQTT_PORT") or 1883), 10)
except Exception as e:
    print(f"FAIL the broker did not answer ({e})"); raise SystemExit
c.loop_start()
c.subscribe([("piano/#", 0), ("homeassistant/+/+/config", 0)])
time.sleep(3)                       # retained messages arrive right after subscribing
c.unsubscribe(["piano/#", "homeassistant/+/+/config"])
for t in sorted(topics):
    c.publish(t, b"", qos=1, retain=True).wait_for_publish(5)
time.sleep(0.5)
c.loop_stop(); c.disconnect()
print(f"OK {len(topics)}")
EOF
)" || true
    case "$res" in
        "OK "*) ok "${res#OK } retained MQTT messages cleared from the broker" ;;
        *)      warn "Retained MQTT messages not cleared: ${res#FAIL }" ;;
    esac
}

# The same without the Python environment, with the mosquitto clients the installer
# added. Their options go in a private file read through XDG_CONFIG_HOME, so the
# password stays out of the process list here too.
clear_retained_mqtt_clients() {
    command -v mosquitto_sub >/dev/null && command -v mosquitto_pub >/dev/null || {
        warn "Retained MQTT messages not cleared: mosquitto-clients is missing"; return 0; }
    local tmp t n=0 host port user pass
    host="$( (set +u; . "$CONFIG"; printf '%s' "${MQTT_HOST:-}") )"
    port="$( (set +u; . "$CONFIG"; printf '%s' "${MQTT_PORT:-1883}") )"
    user="$( (set +u; . "$CONFIG"; printf '%s' "${MQTT_USER:-}") )"
    pass="$( (set +u; . "$CONFIG"; printf '%s' "${MQTT_PASS:-}") )"
    tmp="$(mktemp -d)"; chmod 700 "$tmp"
    {
        printf -- '-h %s\n-p %s\n' "$host" "${port:-1883}"
        [ -n "$user" ] && printf -- '-u %s\n-P %s\n' "$user" "$pass"
    } > "$tmp/mosquitto_sub"
    cp "$tmp/mosquitto_sub" "$tmp/mosquitto_pub"
    local list rc=0
    list="$(XDG_CONFIG_HOME="$tmp" timeout 8 mosquitto_sub -t 'piano/#' --retained-only -F '%t' -W 3 2>&1)" || rc=$?
    # 27 is the normal end of -W: the retained messages came and the wait ran out
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 27 ]; then
        rm -rf "$tmp"
        warn "Retained MQTT messages not cleared: ${list:-the broker did not answer}"
        return 0
    fi
    # the sensors the piano announced to Home Assistant, so the device goes from there too
    list="$list"$'\n'"$(XDG_CONFIG_HOME="$tmp" timeout 8 mosquitto_sub -t 'homeassistant/+/+/config' --retained-only -F '%t' -W 3 2>/dev/null || true)"
    while IFS= read -r t; do
        case "$t" in piano/*) ;; homeassistant/*/piano_*/config) ;; *) continue ;; esac
        XDG_CONFIG_HOME="$tmp" mosquitto_pub -t "$t" -r -n -q 1 2>/dev/null && n=$((n + 1))
    done <<< "$list"
    rm -rf "$tmp"
    ok "$n retained MQTT messages cleared from the broker"
}

# The broker installed by choose_broker. When this program installed the package and
# no other program left settings next to ours (BROKER_GOES), the broker goes completely:
# the service stops, the package and the dependencies it brought are purged with the
# other packages, and its data and log folders are removed, so no trace of MQTT is left.
# Otherwise only our settings are removed and Mosquitto stays.
remove_local_broker() {
    [ -f "$LOCAL_BROKER_CONF" ] || return 0
    rm -f "$LOCAL_BROKER_CONF" "$LOCAL_BROKER_USERS" "$LOCAL_BROKER_ACL"
    if [ "$BROKER_GOES" = "yes" ]; then
        systemctl disable --now mosquitto 2>/dev/null || true
        ok "The local broker stopped, it is removed with the packages"
    else
        systemctl restart mosquitto 2>/dev/null || true
        ok "The local broker's settings removed; Mosquitto stays, other settings use it"
    fi
}

# What stays in Home Assistant. Removing it there would need an access token at the moment of
# the removal, would delete things that may have been edited since, and would fail when Home
# Assistant is not reachable. So it is listed, with where each item is.
ha_leftovers() {
    echo
    echo "  Outside this machine, in Home Assistant, which is left as it is:"
    echo "    - Settings, Automations: the 5 named \"Piano: ...\", and the blueprints under \"piano\""
    echo "    - Settings, Devices & services, Helpers: piano_alarm_stop, piano_log_chat_id, piano_last_played"
    echo "    - Settings, Devices & services, MQTT: the device \"Piano\" (Delete). Its 28 sensors go with it"
    echo "    - Settings, People, Users: the user armonicopiano (the tab shows with Advanced mode on in your profile)"
    echo "    - Settings, Apps: the Mosquitto broker app, when nothing else uses it"
    echo "    - The Telegram bot is Home Assistant's own integration, and stays until it is removed there"
    echo "  Nothing of it stops working harmfully: the sensors only stay empty. Where to click, in detail:"
    echo "  https://github.com/${REPO:-vestacorelabs/armonico}/blob/main/docs/home-assistant.md#taking-it-out-again"
}

uninstall() {
    local purge="$1" s d
    # Both paths clear the machine of everything this program put on it and then remove
    # the folder the installer was run from, last of all. plan_repo_dir decides whether
    # removing that folder is safe: a system folder, a home folder, a folder holding
    # anything that is not part of the project, or a checkout with local changes stays.
    # --purge goes further: the system packages, Mosquitto itself and the retained
    # messages on the broker, and it asks before any of it.
    plan_repo_dir
    find_data_dirs
    if [ "$purge" = "yes" ]; then
        plan_packages
        confirm_purge
    fi
    step "Removing $APP_TITLE"
    for s in "${SERVICES[@]}" "${OPTIONAL_SERVICES[@]}" "$APP-update.path" "$APP-reset.path"; do
        systemctl disable --now "$s" 2>/dev/null || true
    done
    rm -f "$UNIT_DIR/$APP-update.path" "$UNIT_DIR/$APP-update.service" "$UNIT_DIR/$APP-reset.path" "$UNIT_DIR/$APP-reset.service"
    # after the services stop, so the bridge cannot publish its state again
    clear_retained_mqtt          # both ways: the piano's retained messages and its sensors go from the broker, so Home Assistant forgets the device
    for s in "${SERVICES[@]}" "${OPTIONAL_SERVICES[@]}"; do
        rm -f "$UNIT_DIR/$s.service"
        [ "$purge" = "yes" ] && rm -rf "${UNIT_DIR:?}/$s.service.d"    # overrides made with systemctl edit
        systemctl reset-failed "$s" 2>/dev/null || true
    done
    systemctl daemon-reload 2>/dev/null || true
    rm -rf "${OPT_DIR:?}" "/etc/modules-load.d/$APP.conf" "/etc/udev/rules.d/60-$APP-seq.rules" "/run/${APP:?}" "/usr/local/bin/${APP:?}"
    ok "Program and services removed"
    # The data, the settings and the user go either way. Nothing of this program is left
    # behind to be found months later by someone who no longer knows what it was.
    for d in "${DATA_DIRS[@]}"; do rm -rf "${d:?}"; done
    remove_local_broker
    # A plain --uninstall keeps the list of packages the installer added (no password in it), so a
    # later --purge still knows which ones are this program's, Mosquitto among them
    local pkg_keep=""
    if [ "$purge" != "yes" ] && [ -s "$PKG_LIST" ]; then
        pkg_keep="$(mktemp)"; cp "$PKG_LIST" "$pkg_keep"
    fi
    rm -rf "${ETC_DIR:?}"
    if [ -n "$pkg_keep" ]; then
        install -d -m 700 "$ETC_DIR"
        install -m 600 "$pkg_keep" "$PKG_LIST"; rm -f "$pkg_keep"
    fi
    userdel "$SERVICE_USER" 2>/dev/null || true
    getent group "$SERVICE_USER" >/dev/null && groupdel "$SERVICE_USER" 2>/dev/null || true
    ok "Recordings, songs, progress, history, shortcuts, settings and the service user removed"
    if [ "$purge" = "yes" ]; then
        # the broker is stopped before its package goes: a process that outlives the package
        # keeps port 1883, and the next install finds the port taken
        if [ "$BROKER_GOES" = "yes" ]; then
            systemctl disable --now mosquitto 2>/dev/null || true
        fi
        remove_packages
        if [ "$BROKER_GOES" = "yes" ]; then
            pkill -x mosquitto 2>/dev/null || true
            rm -rf /etc/mosquitto /var/lib/mosquitto /var/log/mosquitto
            systemctl reset-failed mosquitto 2>/dev/null || true
            systemctl daemon-reload 2>/dev/null || true
        fi
        wrap 2 "  Left on purpose: ${PKG_KEEP[*]}, and packages that were on the machine before or that something else now needs. The service's lines in the system journal age out by themselves."
        ha_leftovers
    else
        wrap 2 "  Still installed: the system packages the installer added, and Mosquitto itself if it was installed here. Their list is kept in $PKG_LIST. Removing those too: unpack the project again and run --purge."
        ha_leftovers
    fi
    # Last of all, once nothing on the machine needs it any more.
    remove_repo_dir
}

# The folder the installer was run from, once nothing on the machine needs it.
remove_repo_dir() {
    if [ "$REPO_GOES" = "yes" ]; then
        cd /
        rm -rf -- "${REPO_DIR:?}"
        ok "The project folder $REPO_DIR removed. The shell may still sit in it: cd ~"
    elif [ -n "$REPO_KEEP_WHY" ]; then
        ok "Kept the project folder $REPO_DIR: $REPO_KEEP_WHY"
    fi
}

summary() {
    local ip
    ip="$(lan_ip)"
    step "Done"
    echo "  Lesson screen:   http://${ip:-<this machine>}:$LESSON_UI_PORT"
    echo "  Songs folder:    $SONGS_DIR"
    echo "  Shortcuts:       $VAR_DIR/shortcuts.conf"
    echo "  Settings:        $CONFIG"
    echo "  Command:         $APP help"
    echo "  Live log:        $APP logs -f"
    echo "  Silent keyboard: sudo $APP unstick, and if that is not enough: sudo $APP reset"
    if [ "${C_SCREEN_CODE:-on}" = "on" ]; then
        local code_file="$VAR_DIR/screen_code" code=""
        # The engine draws it on first use; drawing it here means the installer can show it
        if [ ! -s "$code_file" ]; then
            ( umask 077; python3 -c 'import secrets; print("%06d" % secrets.randbelow(10**6))' > "$code_file" )
            chown "$SERVICE_USER:$SERVICE_USER" "$code_file"
            chmod 600 "$code_file"
        fi
        [ -r "$code_file" ] && code="$(tr -dc '0-9' < "$code_file" | head -c 6)"
        echo "  Screen code:     ${code:-drawn when the screen is first opened}"
        echo "                   Entered once on each device, then remembered for 14 days."
        echo "                   Again later: $APP code   Off: sudo $APP code off"
    else
        echo "  Screen code:     off. Every device on the network can use the lesson screen."
        echo "                   Turn it on: sudo $APP code on"
    fi
    if [ "$C_MQTT_LOCAL" = "yes" ]; then
        # The machine's own address on the network. config.env keeps 127.0.0.1 for the
        # services here, because that one holds even when the machine changes network.
        echo "  Broker:          ${ip:-<this machine>}:${C_MQTT_PORT}, Mosquitto on this machine, user piano"
        echo "                   Port 1883 answers this machine only; the status screen uses port 9001"
        echo "                   (chosen by the page itself, nothing to type)."
        echo "                   Its password: sudo grep MQTT_PASS $CONFIG"
    else
        echo "  Broker:          ${C_MQTT_HOST}:${C_MQTT_PORT}"
    fi
    echo "  First test:      $APP play ode_to_joy"
    if [ "$NEW_GROUPS" = "yes" ]; then
        echo "                   Group membership starts with a new login. Until then, in this terminal:"
        echo "                     newgrp $SERVICE_USER      (or log out and in again)"
        echo "                   Commands that change the system ask for the password by themselves."
    fi
    echo
}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
require_root "$@"
show_logo

case "$ACTION" in
    uninstall) uninstall no; exit 0 ;;
    purge)     uninstall yes; exit 0 ;;
esac

if [ "$ACTION" = "integrations" ]; then
    [ -x "$VENV/bin/python" ] || die "Nothing installed yet. Run: sudo $0"
    load_existing
    refresh_integrations
    exit 0
fi

[ "$REPO_DIR" = "$OPT_DIR" ] && [ "$ACTION" = "install" ] \
    && die "This is the installed copy. To update: sudo $APP update. To install again, run install.sh from a copy of the repository"

step "Checks"
check_os
check_repo
check_platform

if [ "$ACTION" = "reconfigure" ]; then
    [ -x "$VENV/bin/python" ] || die "Nothing installed yet. Run: sudo $0"
    stop_services
    configure
    start_services
    finish_home_assistant
    summary
    exit 0
fi

# A broker this program installed before the touch screen existed has no websockets
# listener. Added on an update, on the same address as its 1883 listener, and only once.
ensure_ws_listener() {
    [ "$C_MQTT_LOCAL" = "yes" ] && [ -f "$LOCAL_BROKER_CONF" ] || return 0
    if grep -q '^protocol websockets' "$LOCAL_BROKER_CONF"; then
        # an earlier version kept the status screen's listener on this machine, where no other
        # device can reach it: it answers on the network now
        if grep -qE '^listener +9001 +[0-9.]+' "$LOCAL_BROKER_CONF"; then
            sed -i -E 's/^listener +9001 +[0-9.]+.*/listener 9001/' "$LOCAL_BROKER_CONF"
            if systemctl restart mosquitto 2>/dev/null; then
                ok "The status screen's websockets listener (9001) now answers on the network"
            else
                warn "Mosquitto did not restart. Details: journalctl -u mosquitto -n 20"
            fi
        fi
        return 0
    fi
    if port_in_use 9001; then
        warn "Port 9001 is taken, so the touch screen's websockets listener was not added"
        return 0
    fi
    # two listeners with their own password_file and acl_file need per-listener settings,
    # or Mosquitto refuses to start on the duplicate
    grep -q '^per_listener_settings' "$LOCAL_BROKER_CONF" || sed -i '1i per_listener_settings true' "$LOCAL_BROKER_CONF"
    {
        echo
        echo "# the touch screen (screen/piano-status.html) speaks MQTT over websockets"
        echo "listener 9001"
        echo "protocol websockets"
        echo "allow_anonymous false"
        echo "password_file $LOCAL_BROKER_USERS"
        echo "acl_file $LOCAL_BROKER_ACL"
    } >> "$LOCAL_BROKER_CONF"
    if systemctl restart mosquitto 2>/dev/null; then
        ok "The local broker now also answers websockets on port 9001, for the touch screen"
    else
        warn "Mosquitto did not restart after adding the websockets listener. Details: journalctl -u mosquitto -n 20"
    fi
}

snapshot_packages
early_packages
start_music
install_packages
check_python
enable_sequencer
create_user
create_dirs
stop_services
install_files
build_venv
install_units
stop_music              # the setup needs the keys, so the music ends here

UPDATING="no"
if [ -f "$CONFIG" ]; then
    step "Settings"
    load_existing
    ensure_ws_listener
    UPDATING="yes"
    ok "Existing settings kept. To change them: sudo $0 --reconfigure"
else
    configure
fi

start_services
finish_home_assistant
[ "$UPDATING" = "yes" ] && { refresh_integrations || true; }
summary
