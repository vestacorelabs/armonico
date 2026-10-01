#!/bin/bash
# update.sh - installs a newer release from GitHub. Started by the update service when the
# lesson screen writes <data folder>/update-request, or by hand: sudo /opt/<name>/setup/update.sh
#
# Only a version number is taken from the request file. The download address is built here
# from the repository name the installer wrote, over HTTPS from github.com, so the screen
# cannot point it anywhere else.
set -Euo pipefail

APP_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
APP="$(basename "$APP_DIR")"
CONFIG="/etc/$APP/config.env"
DATA="/var/lib/$APP"
# Only the one value this script needs, read as text. Sourcing the file ran whatever was
# in it as root, and a line edited by hand could carry more than a setting.
cfg_get() {
    sed -n "s/^$1='\\(.*\\)'$/\\1/p" "$CONFIG" 2>/dev/null | tail -1
}
if [ -r "$CONFIG" ]; then
    CFG_DATA="$(cfg_get DATA_DIR)"
    case "$CFG_DATA" in
        /*) DATA="$CFG_DATA" ;;
        "") ;;
        *) echo "DATA_DIR in $CONFIG is not an absolute path, using $DATA" >&2 ;;
    esac
fi
REQ="$DATA/update-request"
STATUS="$DATA/update-status.json"
LOG="$DATA/update.log"
REPO="$(cat "$APP_DIR/REPO" 2>/dev/null)"

# The data folder belongs to the service user, so a name in it can be a link this script did not
# make. A redirect as root would follow it to any file on the machine. Everything is written in
# a folder of root's own first, then put in place by install: it removes whatever is at the
# name and creates a new file there (O_EXCL), so a link is replaced, never written through.
OWN="$(mktemp -d)"          # this script's own files: the status and the installer's log
TMP="$(mktemp -d)"          # the download and what it unpacks to
trap 'rm -rf "$OWN" "$TMP"' EXIT
OWNER="$(stat -c '%u:%g' "$DATA" 2>/dev/null || echo 0:0)"
publish() {      # publish FILE NAME: a file of root's own, as NAME in the data folder
    install -T -m 0644 -o "${OWNER%:*}" -g "${OWNER#*:}" "$1" "$2" 2>/dev/null || true
}

status() {       # status STATE MESSAGE
    printf '{"state": "%s", "message": "%s", "time": %s}\n' "$1" "${2//\"/\'}" "$(date +%s)" > "$OWN/status"
    publish "$OWN/status" "$STATUS"
}

TAG="$(head -c 40 "$REQ" 2>/dev/null | tr -d '[:space:]')"
rm -f "$REQ"
[ -n "$TAG" ] || TAG="latest"
if [ "$TAG" = "latest" ]; then
    TAG="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/latest" \
           | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
fi
if ! [[ "$TAG" =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]] || ! [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    status failed "No valid version to install"; exit 1
fi

status running "Downloading $TAG"
BASE="https://github.com/$REPO/releases/download/$TAG"
# Only the file the release itself carries is used, because it is the one SHA256SUMS describes.
# The archive GitHub builds from a tag is a different file and would never match.
if ! curl -fsSL --proto '=https' --tlsv1.2 --max-time 300 -o "$TMP/release.zip" \
        "$BASE/armonico-$TAG.zip"; then
    status failed "The download of $TAG failed"; exit 1
fi

# What arrived has to be what the release says arrived. Without this, anything that can
# answer for github.com, or a release replaced after the fact, runs as root on this machine.
if curl -fsSL --proto '=https' --tlsv1.2 --max-time 60 -o "$TMP/sha256" "$BASE/SHA256SUMS" 2>/dev/null; then
    WANT="$(grep -o '^[0-9a-f]\{64\}' "$TMP/sha256" | head -1)"
    GOT="$(sha256sum "$TMP/release.zip" | cut -d' ' -f1)"
    if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
        status failed "The download does not match the checksum published for $TAG"
        exit 1
    fi
    echo "checksum verified: $GOT" >&2
elif [ "${UPDATE_REQUIRE_CHECKSUM:-yes}" = "yes" ]; then
    status failed "No checksum was published for $TAG, so it was not installed"
    exit 1
fi

# A release is a set of files, not a way to choose who owns them here: both readers below
# write the files as this script's own user and take nothing from the archive but content.
# unzip is what the installer adds; python3 is always there, and reads the same file, so an
# installation from before unzip was on the list still updates.
if command -v unzip >/dev/null 2>&1; then
    unzip -q -o "$TMP/release.zip" -d "$TMP" \
        || { status failed "The download was damaged"; exit 1; }
else
    python3 -m zipfile -e "$TMP/release.zip" "$TMP" \
        || { status failed "The download was damaged"; exit 1; }
fi
SRC="$(find "$TMP" -mindepth 2 -maxdepth 2 -name install.sh -printf '%h\n' | sort | head -1)"
[ -n "$SRC" ] || { status failed "The release has no installer"; exit 1; }
[ "$(find "$TMP" -mindepth 2 -maxdepth 2 -name install.sh | wc -l)" -eq 1 ] \
    || { status failed "The release holds more than one installer"; exit 1; }

status running "Installing $TAG"
# no terminal: the installer asks nothing, keeps the settings, and restarts the services
if (cd "$SRC" && bash ./install.sh </dev/null >"$OWN/update.log" 2>&1); then
    publish "$OWN/update.log" "$LOG"
    status "done" "Updated to $TAG"     # quoted: bare done reads as the end of a loop
else
    publish "$OWN/update.log" "$LOG"
    status failed "The installer stopped. Details: $LOG"
    exit 1
fi
