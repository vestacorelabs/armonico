#!/bin/sh
# Root side of the screen's backup and restore: started by armonico-device-backup.service when
# the screen writes the request file. Uses the device's Python, or the system's when it is not there.
DIR="$(dirname "$(readlink -f "$0")")"
PY="$(dirname "$DIR")/device-venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" -I "$DIR/backup.py" run-request
