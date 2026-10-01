#!/bin/bash
#
# piano_bridge.sh - connects a USB MIDI keyboard to MQTT
#
# Listens to the keys, recognizes shortcuts (short melodies) and publishes them,
# plays MIDI files and the alarm on the keyboard, and watches the USB link.
# Runs as a systemd service. Settings come from the environment, which systemd
# loads from the config file the installer writes.
#
# Topics it listens to:
#   piano/play           "song" or "song|who asked"    play a MIDI file once
#   piano/alarm/set      "song" or "song|who asked"    play it in a loop until stopped
#   piano/stop           "reason"                      stop whatever is playing
#   piano/log/control    start | stop                  mirror the log to piano/log
#   piano/shortcut/cmd   "action|chat_id|arguments"    commands from the Telegram bot
#
# Topics it publishes: see docs/mqtt.md.

# =========================
# Settings
# =========================
BROKER="${MQTT_HOST:?MQTT_HOST is not set}"
BROKER_PORT="${MQTT_PORT:-1883}"
USER="${MQTT_USER:-}"
PASS="${MQTT_PASS:-}"
PIANO_NAME="${MIDI_DEVICE:?MIDI_DEVICE is not set}"
MIDI_DIR="${SONGS_DIR:-/var/lib/armonico/songs}"
SHORTCUTS_FILE="${DATA_DIR:-/var/lib/armonico}/shortcuts.conf"
ALARM_STOP_KEY="${STOP_KEY:-36}"
PYTHON="${PYTHON:-python3}"          # the installer points this at its venv, which has mido
RUN="${RUN_DIR:-/tmp}"               # flags shared with the lesson engine

MQTT_CLIENT_ID="piano-bridge-$$"
PUB_TIMEOUT=5
PANIC_MIDI="$RUN/piano_panic_v2.mid"   # v2 also restores volume and expression

FLAG_SONG="$RUN/piano_song_active"
FLAG_ALARM="$RUN/piano_alarm_active"
FLAG_SHORTCUT="$RUN/piano_shortcut_lock"
FLAG_CONNECTED="$RUN/piano_connected"
FLAG_WATCHDOG_DISCONNECT="$RUN/piano_watchdog_disconnect"
FLAG_LIVE_LOG="$RUN/piano_live_log"
FLAG_STOP_LOCK="$RUN/piano_stop_lock"
FLAG_RESTART_LISTENER="$RUN/piano_restart_listener"
# when the keyboard went away. It survives the bridge's restart by systemd, so the
# time away is measured from the first disconnect, not from the start of the wait.
DISCONNECTED_AT="$RUN/piano_disconnected_at"
# how many startups in a row failed to find the keyboard, and whether the "stuck" alert already
# went out. Both survive the systemd restart loop (they live in RUN, cleared on a real reboot).
# After a few failed cycles the keyboard is likely stuck and needs a physical power-cycle, so a
# distinct signal goes out once, instead of the silent retry loop.
STUCK_COUNT="$RUN/piano_stuck_count"
STUCK_ALERTED="$RUN/piano_stuck_alerted"
STUCK_THRESHOLD=3
# touched by the lesson screen while its keys or a replay sound on the keyboard. A keyboard
# that echoes what it receives would otherwise hand those notes back here as if played.
FLAG_SCREEN="$RUN/piano_screen_keys"
SCREEN_QUIET=3                          # seconds after the screen's last note before keys count again
# touched by the lesson engine while a lesson screen is open and in view on any device. While it
# is fresh, keys trigger nothing: no shortcuts and no learning. The stop key still works.
FLAG_PAGE="$RUN/piano_page_open"
PAGE_QUIET=15                           # seconds after the last sign of an open screen
LAST_LOG_BASE_FILE="$RUN/piano_last_log_base"
KEY_LISTENER_PID_FILE="$RUN/piano_key_listener_pid"
SHUTTING_DOWN="$RUN/piano_shutdown"

WATCHDOG_INTERVAL=1
RECONNECT_TIMEOUT=30
BUFFER_TIMEOUT=8
MAX_BUFFER=70
KEEPALIVE_INTERVAL=300     # 300 watchdog rounds of 1 second: every 5 minutes
MAX_RESTARTS=10
restart_count=0
restart_window_start=0

PEDAL_HOLD_SECS=2          # seconds the sustain pedal is held before "hold" is published

CONNECT_TIME=0

# =====================================================================
# Shortcuts: note sequences stored in a file, managed from the bot
# =====================================================================
SHORTCUTS_STAMP=""
declare -a SHORTCUTS=()

SHORT_MAX=3            # up to 3 keys: matched only when nothing else was played before the pause
SHORT_IDLE_MS=1500     # the pause that closes a short sequence
LEARN_IDLE_MS=5000     # the pause that ends a sequence being learned
LEARN_TIMEOUT=60       # learning mode closes after 60 seconds with no keys
FLAG_LEARN="$RUN/piano_learn"
FLAG_GAME="$RUN/piano_game_active"   # a lesson is running; holds the lesson engine's PID

LAST_KEY_MS=0
IDLE_DONE=1
LEARN_ARMED=""

KEY_LISTENER_PID=0
WATCHDOG_PID=0
MQTT_PID=0

PID_FILE_ALARM="$RUN/piano_alarm_pid"
PID_FILE_SONG="$RUN/piano_song_pid"
CURRENT_ALARM_SONG="$RUN/piano_current_alarm_song"
CURRENT_SONG="$RUN/piano_current_song"

mkdir -p "$RUN" "$MIDI_DIR" 2>/dev/null

# =========================
# Helpers
# =========================

# Arguments shared by every mosquitto call. The password is visible to
# "ps" only inside the service: the unit file sets ProtectProc=invisible.
mqtt_args() {
    MQTT_ARGS=(-h "$BROKER" -p "$BROKER_PORT")
    [ -n "$USER" ] && MQTT_ARGS+=(-u "$USER" -P "$PASS")
}
mqtt_args

pub() {
    # timeout, so a stuck broker or a dead network never freezes the bridge
    timeout "$PUB_TIMEOUT" mosquitto_pub "${MQTT_ARGS[@]}" "$@" 2>/dev/null
}

# a real retained delete: an empty payload with the retain flag
clear_retained() {
    local t
    for t in "$@"; do
        pub -t "$t" -r -n
    done
}

# Waits for the broker, so the first messages are not lost when the
# service starts before the network is up.
wait_for_broker() {
    local tries=0
    while [ $tries -lt 60 ]; do
        if pub -t "piano/status" -m "starting" -r; then
            [ $tries -gt 0 ] && echo "Broker reachable after ${tries} seconds"
            return 0
        fi
        tries=$((tries + 1))
        sleep 1
    done
    echo "Broker not reachable after 60 seconds, continuing anyway"
    return 1
}

log() {
    local base="$*"
    local msg
    msg="$base · $(date '+%H:%M:%S')"
    echo "$msg"
    if [ -f "$FLAG_LIVE_LOG" ]; then
        local last_base=""
        [ -f "$LAST_LOG_BASE_FILE" ] && last_base=$(cat "$LAST_LOG_BASE_FILE" 2>/dev/null)
        if [ "$base" != "$last_base" ]; then
            echo "$base" > "$LAST_LOG_BASE_FILE"
            pub -t "piano/log" -m "$msg"
        fi
    fi
}

# "WeAreTheChampions" and "ode_to_joy" become "We Are The Champions" and "ode to joy"
to_display() {
    echo "$1" \
        | sed 's/\([a-z]\)\([A-Z]\)/\1 \2/g' \
        | sed 's/\([A-Z]\)\([A-Z][a-z]\)/\1 \2/g' \
        | sed 's/_/ /g'
}

# MIDI note number to its name: 36 -> C2, 60 -> C4
note_name() {
    local names=(C C# D D# E F F# G G# A A# B)
    echo "${names[$(( $1 % 12 ))]}$(( $1 / 12 - 1 ))"
}
STOP_KEY_NAME="$(note_name "$ALARM_STOP_KEY")"

# =========================
# Shortcuts
# =========================

# no external process: on_idle runs dozens of times a second because of MIDI clock messages
now_ms() { local t=${EPOCHREALTIME/[.,]/}; echo $(( t / 1000 )); }

# The language chosen on the lesson screen (English or Hebrew), for the Telegram replies.
# A Hebrew reply is laid out for right-to-left reading by the lessons' own helper.
LESSONS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lessons" 2>/dev/null && pwd)"
ui_lang() {
    local l
    l=$(grep -o '"lang": *"[a-z]*"' "${DATA_DIR:-/var/lib/armonico}/settings.json" 2>/dev/null | grep -o '[a-z]*"$' | tr -d '"')
    echo "${l:-${UI_LANG:-en}}"
}
L() { if [ "$(ui_lang)" = he ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
sc_reply() {
    local text="$2"
    if [ "$(ui_lang)" = he ] && [ -n "$LESSONS_DIR" ]; then
        text=$("$PYTHON" "$LESSONS_DIR/piano_lang.py" <<< "$2" 2>/dev/null) || text="$2"
    fi
    pub -t "piano/shortcut/reply" -m "$1|$text"
}

# reloads only when the file changed (time, size, inode)
load_shortcuts() {
    local st notes id
    st=$(stat -c '%Y:%s:%i' "$SHORTCUTS_FILE" 2>/dev/null) || { SHORTCUTS=(); SHORTCUTS_STAMP=""; return; }
    [ "$st" = "$SHORTCUTS_STAMP" ] && return
    SHORTCUTS_STAMP=$st
    SHORTCUTS=()
    while IFS='|' read -r notes id; do
        [[ -z "$notes" || "$notes" == \#* || -z "$id" ]] && continue
        SHORTCUTS+=("$notes|$id")
    done < "$SHORTCUTS_FILE"
    log "🔄 ${#SHORTCUTS[@]} shortcuts loaded"
}

fire_shortcut() {
    log "✅ Shortcut recognized: $(to_display "$1")"
    pub -t "piano/shortcut" -m "$1"
    KEY_BUFFER=()
    touch "$FLAG_SHORTCUT"
    (sleep 1; rm -f "$FLAG_SHORTCUT") &
}

# long sequences: checked while playing, anywhere inside the buffer
match_long() {
    local buf=" ${KEY_BUFFER[*]} " e seq arr
    for e in "${SHORTCUTS[@]}"; do
        seq=${e%%|*}; read -ra arr <<< "$seq"
        [ "${#arr[@]}" -le "$SHORT_MAX" ] && continue
        if [[ "$buf" == *" $seq "* ]]; then fire_shortcut "${e#*|}"; return 0; fi
    done
    return 1
}

# short sequences and single keys: only if they are all that was played before the pause
match_short() {
    local buf="${KEY_BUFFER[*]}" e seq arr
    for e in "${SHORTCUTS[@]}"; do
        seq=${e%%|*}; read -ra arr <<< "$seq"
        [ "${#arr[@]}" -gt "$SHORT_MAX" ] && continue
        if [ "$buf" = "$seq" ]; then fire_shortcut "${e#*|}"; return 0; fi
    done
    return 1
}

keys_word() {        # "en" as the second argument: for the log, which stays in English
    if [ "${2:-}" != en ] && [ "$(ui_lang)" = he ]; then
        [ "$1" -eq 1 ] && echo "קליד אחד" || echo "$1 קלידים"
    else
        [ "$1" -eq 1 ] && echo "1 key" || echo "$1 keys"
    fi
}
mode_word() {
    if [ "$1" -le "$SHORT_MAX" ]; then L "recognized after 2 seconds of silence" "מזוהה אחרי 2 שניות של שקט"
    else L "recognized instantly while playing" "מזוהה מיד תוך כדי נגינה"; fi
}

shortcut_save() {
    local id="${1// /_}" notes chat="$3" k n mode dup arr
    read -ra arr <<< "$2"
    notes="${arr[*]}"
    n=${#arr[@]}
    if [[ -z "$id" || "$id" == *"|"* ]]; then sc_reply "$chat" "$(L "❌ Invalid name" "❌ שם לא תקין")"; return 1; fi
    if [ "$n" -lt 1 ] || [ "$n" -gt "$MAX_BUFFER" ]; then
        sc_reply "$chat" "$(L "❌ A shortcut needs 1 to $MAX_BUFFER keys" "❌ קיצור צריך בין 1 ל-$MAX_BUFFER קלידים")"; return 1
    fi
    for k in "${arr[@]}"; do
        if ! [[ "$k" =~ ^[0-9]+$ ]] || [ "$k" -gt 127 ]; then
            sc_reply "$chat" "$(L "❌ Invalid key: $k (0 to 127)" "❌ קליד לא תקין: $k (בין 0 ל-127)")"; return 1
        fi
    done
    if [ "$n" -eq 1 ] && [ "$notes" = "$ALARM_STOP_KEY" ]; then
        sc_reply "$chat" "$(L "❌ Key $ALARM_STOP_KEY ($STOP_KEY_NAME) is reserved for stopping the alarm" "❌ קליד $ALARM_STOP_KEY ($STOP_KEY_NAME) שמור לעצירת השעון המעורר")"; return 1
    fi
    touch "$SHORTCUTS_FILE"
    dup=$(awk -F'|' -v s="$notes" -v i="$id" '$1==s && $2!=i {print $2; exit}' "$SHORTCUTS_FILE")
    if [ -n "$dup" ]; then sc_reply "$chat" "$(L "❌ This sequence already exists as $dup" "❌ הרצף הזה כבר שמור בשם $dup")"; return 1; fi
    (
        flock 9
        awk -F'|' -v i="$id" '$2!=i' "$SHORTCUTS_FILE" > "$SHORTCUTS_FILE.tmp"
        echo "$notes|$id" >> "$SHORTCUTS_FILE.tmp"
        mv "$SHORTCUTS_FILE.tmp" "$SHORTCUTS_FILE"
    ) 9>"$SHORTCUTS_FILE.lock"
    if [ "$n" -le "$SHORT_MAX" ]; then
        mode=$(L "To test: play the keys and wait 2 seconds" "לבדיקה: לנגן את הקלידים ולחכות 2 שניות")
    else
        mode=$(L "To test: play the keys, it is recognized right away" "לבדיקה: לנגן את הקלידים, והקיצור מזוהה מיד")
    fi
    log "💾 Shortcut saved: $(to_display "$id") ($(keys_word "$n" en))"
    sc_reply "$chat" "$(L "✅ Shortcut $id saved" "✅ הקיצור $id נשמר")"$'\n'"$notes"$'\n'"$(keys_word "$n") · $(mode_word "$n")"$'\n'"$mode"
}

shortcut_delete() {
    local id="${1// /_}" chat="$2"
    if ! awk -F'|' -v i="$id" '$2==i {f=1} END {exit !f}' "$SHORTCUTS_FILE" 2>/dev/null; then
        sc_reply "$chat" "$(L "❌ No shortcut named $id" "❌ אין קיצור בשם $id")"; return 1
    fi
    (
        flock 9
        awk -F'|' -v i="$id" '$2!=i' "$SHORTCUTS_FILE" > "$SHORTCUTS_FILE.tmp"
        mv "$SHORTCUTS_FILE.tmp" "$SHORTCUTS_FILE"
    ) 9>"$SHORTCUTS_FILE.lock"
    log "🗑️ Shortcut deleted: $(to_display "$id")"
    sc_reply "$chat" "$(L "🗑️ Deleted: $id" "🗑️ נמחק: $id")"
}

shortcut_list() {
    local chat="$1" txt="" notes id arr i=0 shown
    while IFS='|' read -r notes id; do
        [[ -z "$notes" || "$notes" == \#* || -z "$id" ]] && continue
        read -ra arr <<< "$notes"; i=$((i+1))
        shown="${arr[*]:0:8}"
        [ "${#arr[@]}" -gt 8 ] && shown+=" …"
        txt+=$'\n'"🔹 $id"$'\n'"$shown"$'\n'"$(keys_word ${#arr[@]}) · $(mode_word ${#arr[@]})"$'\n'
    done < "$SHORTCUTS_FILE" 2>/dev/null
    if [ "$i" -eq 0 ]; then
        sc_reply "$chat" "$(L "🎹 No shortcuts saved" "🎹 אין קיצורים שמורים")"
    else
        sc_reply "$chat" "$(L "🎹 Saved shortcuts: $i" "🎹 קיצורים שמורים: $i")"$'\n'"$txt"
    fi
}

# Finds a MIDI file by name. The name must match the file name without its
# extension; case and trailing spaces are ignored. Prints the full path.
find_midi() {
    local want="$1" f base
    want="${want%"${want##*[![:space:]]}"}"
    [ -z "$want" ] && return 1
    shopt -s nullglob
    for f in "$MIDI_DIR"/*.mid "$MIDI_DIR"/*.midi; do
        base=$(basename "$f"); base="${base%.*}"
        if [ "${base,,}" = "${want,,}" ]; then
            shopt -u nullglob
            echo "$f"
            return 0
        fi
    done
    shopt -u nullglob
    return 1
}

# the song name as in its file name, without folder and extension
midi_name() { local b; b=$(basename "$1"); echo "${b%.*}"; }

# "song|who" -> SONG and SOURCE. Who asked travels with the command itself,
# so the status never shows a stale "triggered by" left from an earlier run.
split_request() {
    local raw="$1" default_source="$2"
    if [[ "$raw" == *"|"* ]]; then
        SONG="${raw%%|*}"
        SOURCE="${raw#*|}"
    else
        SONG="$raw"
        SOURCE=""
    fi
    SONG="${SONG%"${SONG##*[![:space:]]}"}"
    SOURCE="${SOURCE:-$default_source}"
}

# a song that was cut: published as the last song, with the reason it stopped
publish_song_stopped() {
    local reason="$1" song=""
    [ -f "$FLAG_SONG" ] || return 0
    [ -f "$CURRENT_SONG" ] && song=$(cat "$CURRENT_SONG")
    [ -z "$song" ] && return 0
    pub -t "piano/play/last_finished" -m "$song"   -r
    pub -t "piano/play/stopped_by"    -m "$reason" -r
}

# playing from Telegram or the armonico command: checks first and answers, then publishes piano/play
telegram_play() {
    local chat="$1" name="$2" file
    name="${name%"${name##*[![:space:]]}"}"
    if [ -z "$name" ]; then
        sc_reply "$chat" "$(L "❌ Missing a song name. Example: /pianoplay ode_to_joy" "❌ חסר שם של שיר. לדוגמה: /pianoplay ode_to_joy")"
        return
    fi
    file=$(find_midi "$name") || { sc_reply "$chat" "$(L "❌ No song named: $name. See /pianosongs" "❌ אין שיר בשם: $name. הרשימה: /pianosongs")"; return; }
    name=$(midi_name "$file")
    if [ -f "$FLAG_ALARM" ]; then
        sc_reply "$chat" "$(L "⏰ The alarm is playing, the song was not started" "⏰ השעון המעורר מנגן, השיר לא התחיל")"
    elif [ -f "$FLAG_SONG" ]; then
        sc_reply "$chat" "$(L "🎶 A song is already playing. Stop it first: /pianostop" "🎶 כבר מתנגן שיר. קודם צריך לעצור אותו: /pianostop")"
    elif [ -f "$FLAG_GAME" ]; then
        sc_reply "$chat" "$(L "🎹 A lesson is running, the song was not started" "🎹 יש שיעור פעיל, השיר לא התחיל")"
    elif ! is_piano_connected; then
        sc_reply "$chat" "$(L "🔌 The keyboard is not connected, the song was not started" "🔌 המקלדת לא מחוברת, השיר לא התחיל")"
    else
        # the armonico command sends its commands with a chat id that starts with cli-
        if [[ "$chat" == cli-* ]]; then pub -t "piano/play" -m "$name|Terminal"; else pub -t "piano/play" -m "$name|Telegram"; fi
        sc_reply "$chat" "$(L "▶️ Playing: $name" "▶️ מנגן: $name")"
    fi
}

# the MIDI files in the songs folder, optionally filtered
midi_list() {
    local chat="$1" filter="${2:-}" txt="" count=0 name f
    shopt -s nullglob
    local files=("$MIDI_DIR"/*.mid "$MIDI_DIR"/*.midi)
    shopt -u nullglob

    if [ ${#files[@]} -eq 0 ]; then
        sc_reply "$chat" "$(L "🎹 The songs folder is empty" "🎹 תיקיית השירים ריקה")"
        return
    fi
    for f in "${files[@]}"; do
        name=$(basename "$f")
        name="${name%.*}"
        if [ -n "$filter" ] && [[ "${name,,}" != *"${filter,,}"* ]]; then
            continue
        fi
        count=$((count + 1))
        txt+=$'\n'"🎵 $name"
    done
    if [ "$count" -eq 0 ]; then
        sc_reply "$chat" "$(L "🎹 No songs match: $filter" "🎹 אין שירים שמתאימים לחיפוש: $filter")"
    elif [ -n "$filter" ]; then
        sc_reply "$chat" "$(L "🔍 Search: $filter" "🔍 חיפוש: $filter")"$'\n'"$(L "🎹 Found: $count" "🎹 נמצאו: $count")"$'\n'"$txt"
    else
        sc_reply "$chat" "$(L "🎹 Songs: $count" "🎹 שירים: $count")"$'\n'"$txt"
    fi
}

# called from the MQTT listener. payload: action|chat_id|arguments
handle_shortcut_cmd() {
    local action chat args id notes
    IFS='|' read -r action chat args <<< "$1"
    case "$action" in
        list)       shortcut_list "$chat" ;;
        add)        id=${args%% *}; notes=${args#* }
                    [ "$id" = "$args" ] && notes=""
                    shortcut_save "$id" "$notes" "$chat" ;;
        del)        shortcut_delete "$args" "$chat" ;;
        learn)      # a name followed by numbers saves right away, like add
                    id=${args%% *}; notes=${args#* }
                    if [ "$id" != "$args" ] && [[ "$notes" =~ ^[0-9\ ]+$ ]]; then
                        shortcut_save "$id" "$notes" "$chat"; return
                    fi
                    id=${args// /_}
                    if [ -z "$id" ]; then sc_reply "$chat" "$(L "❌ A name is needed: /seqlearn name" "❌ צריך שם: /seqlearn ושם הקיצור")"; return; fi
                    if page_open; then
                        sc_reply "$chat" "$(L "🖥️ A lesson screen is open, so the keys teach nothing now. Close it, or use /seqadd $id with the notes" \
                                               "🖥️ מסך השיעורים פתוח, אז המקשים לא מלמדים כלום עכשיו. צריך לסגור אותו, או להשתמש ב-/seqadd $id עם התווים")"
                        return
                    fi
                    echo "$id|$chat" > "$FLAG_LEARN"
                    log "🎓 Learning mode: $(to_display "$id")"
                    sc_reply "$chat" "$(L "🎓 Play the sequence for $id now"$'\n'"It is saved after 5 seconds of silence. Cancel: /seqcancel" \
                                           "🎓 עכשיו לנגן את הרצף של $id"$'\n'"הוא נשמר אחרי 5 שניות של שקט. ביטול: /seqcancel")" ;;
        cancel)     if [ -f "$FLAG_LEARN" ]; then rm -f "$FLAG_LEARN"; sc_reply "$chat" "$(L "↩️ Learning cancelled" "↩️ הלמידה בוטלה")"
                    else sc_reply "$chat" "$(L "ℹ️ Nothing is being learned" "ℹ️ אין למידה פעילה")"; fi ;;
        midi|midilist|songs) midi_list "$chat" "$args" ;;
        play)       telegram_play "$chat" "$args" ;;
        reset)      # the bridge has no root rights: the request file wakes the root reset unit,
                    # which stops this very service, so it answers first and the unit answers at the end
                    log "🔄 Full reset requested from chat $chat"
                    if echo "$chat" > "${DATA_DIR:-/var/lib/armonico}/reset-request" 2>/dev/null; then
                        sc_reply "$chat" "$(L "🔄 Full reset requested: unstick, USB reset and a restart of everything. I will answer when it is done" \
                                               "🔄 התבקש איפוס מלא: שחרור, איפוס USB והפעלה מחדש של הכול. אענה כשזה יסתיים")"
                    else
                        sc_reply "$chat" "$(L "❌ The reset could not be requested. Run it on the machine: sudo armonico reset" \
                                               "❌ לא ניתן לבקש איפוס. אפשר להריץ במחשב: sudo armonico reset")"
                    fi ;;
        *)          sc_reply "$chat" "$(L "❌ Unknown command: $action" "❌ פקודה לא מוכרת: $action")" ;;
    esac
}

# called from the key listener whenever read timed out without a new line
on_idle() {
    local lid lchat age gap
    if [ -f "$FLAG_LEARN" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$FLAG_LEARN") ))
        # LEARN_ARMED is set by the first key of the lesson; different means nothing was played yet
        if [ "$age" -gt "$LEARN_TIMEOUT" ] && [ "$(stat -c '%Y:%i' "$FLAG_LEARN")" != "$LEARN_ARMED" ]; then
            IFS='|' read -r lid lchat < "$FLAG_LEARN"
            rm -f "$FLAG_LEARN"
            sc_reply "$lchat" "$(L "⌛ Nothing was played, learning $lid was closed" "⌛ לא נוגן כלום, הלמידה של $lid נסגרה")"
            return
        fi
    fi
    [ ${#KEY_BUFFER[@]} -eq 0 ] && return
    [ "$IDLE_DONE" = 1 ] && return
    gap=$(( $(now_ms) - LAST_KEY_MS ))

    if [ -f "$FLAG_LEARN" ]; then
        [ "$gap" -lt "$LEARN_IDLE_MS" ] && return
        IFS='|' read -r lid lchat < "$FLAG_LEARN"
        rm -f "$FLAG_LEARN"
        shortcut_save "$lid" "${KEY_BUFFER[*]}" "$lchat"
        KEY_BUFFER=(); IDLE_DONE=1
        return
    fi

    [ "$gap" -lt "$SHORT_IDLE_MS" ] && return
    IDLE_DONE=1
    screen_active && return
    page_open && { KEY_BUFFER=(); return; }
    [ -f "$FLAG_GAME" ] && return
    [ -f "$FLAG_SHORTCUT" ] && return
    load_shortcuts
    match_short
}

# true while notes from the lesson screen (its keys, or a replay) may still come back
screen_active() {
    [ -f "$FLAG_SCREEN" ] || return 1
    [ $((EPOCHSECONDS - $(stat -c %Y "$FLAG_SCREEN" 2>/dev/null || echo 0))) -lt "$SCREEN_QUIET" ]
}

# true while a lesson screen is open and in view somewhere
page_open() {
    [ -f "$FLAG_PAGE" ] || return 1
    [ $((EPOCHSECONDS - $(stat -c %Y "$FLAG_PAGE" 2>/dev/null || echo 0))) -lt "$PAGE_QUIET" ]
}

# called from the key listener after the note was added to KEY_BUFFER
on_key() {
    if screen_active || page_open; then KEY_BUFFER=(); return; fi
    LAST_KEY_MS=$(now_ms)
    IDLE_DONE=0
    if [ -f "$FLAG_LEARN" ]; then
        local stamp; stamp=$(stat -c '%Y:%i' "$FLAG_LEARN")
        if [ "$stamp" != "$LEARN_ARMED" ]; then
            LEARN_ARMED=$stamp
            KEY_BUFFER=("${KEY_BUFFER[-1]}")   # start clean from the current key
        fi
        return
    fi
    [ -f "$FLAG_GAME" ] && return
    [ -f "$FLAG_SHORTCUT" ] && return
    load_shortcuts
    match_long
}

get_port() {
    aconnect -i 2>/dev/null | grep -m1 -F "$PIANO_NAME" | awk -F'[: ]+' '{print $2}'
}

is_piano_connected() {
    aconnect -i 2>/dev/null | grep -qF "$PIANO_NAME" || return 1
    local port
    port=$(get_port)
    [ -n "$port" ] || return 1
    return 0
}

# Drawn once per run, so nothing that arrives over the network can imitate it.
MSG_MARK="ARM$(od -An -tx1 -N9 /dev/urandom | tr -d ' \n')"

is_valid_cmd() {
    local c="$1"
    [ -z "$c" ]          && return 1
    [ "$c" = "(null)" ]  && return 1
    [ "$c" = "null" ]    && return 1
    [[ "$c" =~ ^[[:space:]]+$ ]] && return 1
    return 0
}

# length of a MIDI file, as "45 seconds" or "3 minutes 12 seconds"
get_duration() {
    local secs min sec
    secs=$("$PYTHON" -c 'import sys, mido; print(int(mido.MidiFile(sys.argv[1]).length))' "$1" 2>/dev/null)
    [[ "$secs" =~ ^[0-9]+$ ]] && [ "$secs" -gt 0 ] || { echo "unknown"; return; }
    if [ "$secs" -lt 60 ]; then
        echo "${secs} seconds"
    else
        min=$(( secs / 60 )); sec=$(( secs % 60 ))
        local mw="${min} minutes"; [ "$min" -eq 1 ] && mw="1 minute"
        [ "$sec" -eq 0 ] && echo "$mw" || echo "$mw ${sec} seconds"
    fi
}

# =========================
# MIDI panic
# =========================
# When aplaymidi is killed in the middle of a song, the note-off messages are
# never sent and the notes keep ringing. A tiny MIDI file with All Notes Off,
# pedal up and Reset All Controllers on all 16 channels is played through the
# same sequencer port instead. amidi is not used here: its raw port name is not
# reliable on virtual machines.
build_panic_midi() {
    [ -s "$PANIC_MIDI" ] && return 0
    local ch payload=""
    for ch in 0 1 2 3 4 5 6 7 8 9 a b c d e f; do
        payload+="\\x00\\xb${ch}\\x7b\\x00"   # 123 All Notes Off
        payload+="\\x00\\xb${ch}\\x40\\x00"   #  64 Sustain off
        payload+="\\x00\\xb${ch}\\x79\\x00"   # 121 Reset All Controllers
        # Reset All Controllers leaves volume and expression alone (MIDI RP-015), so a
        # file that turned them down would leave the keyboard quiet after it ends
        payload+="\\x00\\xb${ch}\\x07\\x64"   #   7 Volume 100
        payload+="\\x00\\xb${ch}\\x0b\\x7f"   #  11 Expression 127
    done
    payload+="\\x00\\xff\\x2f\\x00"           # End of Track
    # 16 channels * 5 messages * 4 bytes + 4 bytes end = 324 = 0x0144
    printf '\x4d\x54\x68\x64\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60' > "$PANIC_MIDI"
    printf '\x4d\x54\x72\x6b\x00\x00\x01\x44' >> "$PANIC_MIDI"
    # shellcheck disable=SC2059   # the payload is a format string of escapes on purpose
    printf "$payload" >> "$PANIC_MIDI"
}

midi_panic() {
    local port
    port=$(get_port)
    [ -z "$port" ] && return 1
    build_panic_midi || return 1
    aplaymidi -p "$port" "$PANIC_MIDI" 2>/dev/null
    return 0
}

# =========================
# Cleanup on exit
# =========================
CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1
    trap - EXIT SIGTERM SIGINT
    echo "🧹 Cleaning up..."

    # 1. the shutdown flag and the playback flags go first. The alarm loop checks
    #    [ -f "$FLAG_ALARM" ], so removing it last would let it start one more round.
    touch "$SHUTTING_DOWN"
    publish_song_stopped "Bridge stopped"
    rm -f "$FLAG_ALARM" "$FLAG_SONG" "$FLAG_SHORTCUT" "$FLAG_LEARN"
    local pf
    for pf in "$PID_FILE_ALARM" "$PID_FILE_SONG"; do
        [ -f "$pf" ] && kill "$(cat "$pf" 2>/dev/null)" 2>/dev/null
    done

    # 2. the known processes
    [ "${KEY_LISTENER_PID:-0}" -ne 0 ] && kill "$KEY_LISTENER_PID" 2>/dev/null
    [ "${WATCHDOG_PID:-0}"     -ne 0 ] && kill "$WATCHDOG_PID"     2>/dev/null
    [ "${MQTT_PID:-0}"         -ne 0 ] && kill "$MQTT_PID"         2>/dev/null

    # 3. children and grandchildren. The alarm loop is a child of the MQTT
    #    listener, not of this process, so names are matched too.
    pkill -P $$ 2>/dev/null
    pkill -f "mosquitto_sub .*-i $MQTT_CLIENT_ID" 2>/dev/null
    pkill -f "aseqdump -p"  2>/dev/null
    pkill -f "aplaymidi -p" 2>/dev/null
    sleep 0.5
    pkill -9 -P $$ 2>/dev/null
    pkill -9 -f "aplaymidi -p" 2>/dev/null

    midi_panic
    rm -f "$FLAG_STOP_LOCK" "$FLAG_RESTART_LISTENER" "$FLAG_WATCHDOG_DISCONNECT" \
          "$PID_FILE_ALARM" "$PID_FILE_SONG" "$CURRENT_SONG" "$CURRENT_ALARM_SONG" \
          "$KEY_LISTENER_PID_FILE" "$LAST_LOG_BASE_FILE" "$FLAG_CONNECTED"
    pub -t "piano/status"       -m "offline" -r
    pub -t "piano/play/status"  -m "idle"    -r
    pub -t "piano/play/current" -m "unknown" -r
    rm -f "$SHUTTING_DOWN"
    echo "🧹 Cleanup done"
}

# In bash a signal handler runs and then execution continues where it was.
# Without an explicit exit the main loop would carry on after the cleanup,
# start the key listener again, and systemd would have to SIGKILL it.
on_signal() {
    cleanup
    exit 0
}
trap cleanup EXIT
trap on_signal SIGTERM SIGINT

set_key_listener_pid() {
    KEY_LISTENER_PID=$1
    echo "$1" > "$KEY_LISTENER_PID_FILE"
}

# =========================
# Disconnect reporting
# =========================

publish_disconnect_event() {
    local reason="${1:-unknown}"
    local source="${2:-unknown}"
    local uptime_str="unknown"
    if [ "$CONNECT_TIME" -gt 0 ]; then
        local uptime_secs
        uptime_secs=$(( $(date +%s) - CONNECT_TIME ))
        if [ "$uptime_secs" -lt 60 ]; then
            uptime_str="${uptime_secs} seconds"
        else
            local min=$(( uptime_secs / 60 ))
            local sec=$(( uptime_secs % 60 ))
            local mw="${min} minutes"; [ "$min" -eq 1 ] && mw="1 minute"
            [ "$sec" -eq 0 ] && uptime_str="$mw" || uptime_str="$mw ${sec} seconds"
        fi
    fi
    local activity="idle"
    if [ -f "$FLAG_ALARM" ] && [ -f "$CURRENT_ALARM_SONG" ]; then
        activity="alarm: $(to_display "$(cat "$CURRENT_ALARM_SONG")")"
    elif [ -f "$FLAG_SONG" ] && [ -f "$CURRENT_SONG" ]; then
        activity="playing: $(to_display "$(cat "$CURRENT_SONG")")"
    fi
    log "❌ Disconnected | reason: $reason | source: $source | connected for: $uptime_str | activity: $activity"
    # one disconnect often reports twice (the watchdog, then the full reset): keep the first
    [ -f "$DISCONNECTED_AT" ] || date +%s > "$DISCONNECTED_AT"
    pub -t "piano/disconnect/reason"   -m "$reason"             -r
    pub -t "piano/disconnect/source"   -m "$source"             -r
    pub -t "piano/disconnect/uptime"   -m "$uptime_str"         -r
    pub -t "piano/disconnect/activity" -m "$activity"           -r
    pub -t "piano/disconnect/time"     -m "$(date '+%H:%M:%S')" -r
}

# "1 minute 5 seconds" from a number of seconds
duration_words() {
    local secs=$1 min sec mw
    [ "$secs" -lt 60 ] && { [ "$secs" -eq 1 ] && echo "1 second" || echo "$secs seconds"; return; }
    min=$(( secs / 60 )); sec=$(( secs % 60 ))
    mw="${min} minutes"; [ "$min" -eq 1 ] && mw="1 minute"
    [ "$sec" -eq 0 ] && echo "$mw" && return
    [ "$sec" -eq 1 ] && echo "$mw 1 second" || echo "$mw ${sec} seconds"
}

publish_connected() {
    local port=$1
    CONNECT_TIME=$(date +%s)
    touch "$FLAG_CONNECTED"
    pub -t "piano/status" -m "online" -r
    pub -t "piano/port"   -m "$port"  -r
    pub -t "piano/game/event" -m "connected" -r    # tells the lesson engine at once
    log "✅ Keyboard connected on port $port"
    if [ -f "$STUCK_ALERTED" ]; then
        pub -t "piano/alert" -m "The keyboard is back."
        log "🔌 Keyboard recovered from the stuck state"
    fi
    rm -f "$STUCK_COUNT" "$STUCK_ALERTED"           # a clean start clears the failed-startup streak
    publish_reconnected
}

# after a disconnect: when the keyboard came back and how long it was away
publish_reconnected() {
    [ -f "$DISCONNECTED_AT" ] || return 0
    local since away
    since=$(cat "$DISCONNECTED_AT" 2>/dev/null)
    rm -f "$DISCONNECTED_AT"
    [[ "$since" =~ ^[0-9]+$ ]] || return 0
    away=$(duration_words $(( $(date +%s) - since )))
    pub -t "piano/reconnect/time"     -m "$(date '+%H:%M:%S')" -r
    pub -t "piano/reconnect/downtime" -m "$away"               -r
    log "🔌 Keyboard back after $away (away since $(date -d "@$since" '+%H:%M:%S'))"
}

publish_disconnected() {
    rm -f "$FLAG_CONNECTED"
    pub -t "piano/status"       -m "offline"  -r
    pub -t "piano/play/status"  -m "idle"     -r
    pub -t "piano/play/current" -m "unknown"  -r
    pub -t "piano/alarm/status" -m "off"      -r
    pub -t "piano/game/event"   -m "disconnected" -r
    log "❌ Keyboard disconnected"
}

# counts a failed startup (the keyboard was not there when the bridge started, and did not come
# back before it handed control to systemd). Once too many pile up in a row, it publishes a
# distinct "stuck" signal once, so a Home Assistant automation can tell the user to power-cycle the
# keyboard instead of leaving them to guess why the silent retry loop never ends.
note_startup_failure() {
    local count
    count=$(cat "$STUCK_COUNT" 2>/dev/null)
    [[ "$count" =~ ^[0-9]+$ ]] || count=0
    count=$(( count + 1 ))
    echo "$count" > "$STUCK_COUNT"
    if [ "$count" -ge "$STUCK_THRESHOLD" ] && [ ! -f "$STUCK_ALERTED" ]; then
        touch "$STUCK_ALERTED"
        pub -t "piano/status" -m "stuck" -r
        # not retained: a notification, like piano/log, so Home Assistant does not resend the old
        # message every time it reloads and re-subscribes
        pub -t "piano/alert"  -m "The keyboard has not come back after $count tries. If it is on, it is probably stuck: turn it off, wait a few seconds and turn it on again (or replug its USB cable)."
        log "🚨 Keyboard stuck after $count tries, likely needs a power-cycle"
    fi
}

stop_all_playback() {
    local reason="${1:-Manual stop}"
    log "⏹ Stopping all playback..."
    publish_song_stopped "$reason"
    if [ -f "$PID_FILE_ALARM" ]; then
        kill "$(cat "$PID_FILE_ALARM")" 2>/dev/null
        rm -f "$PID_FILE_ALARM"
    fi
    if [ -f "$PID_FILE_SONG" ]; then
        kill "$(cat "$PID_FILE_SONG")" 2>/dev/null
        rm -f "$PID_FILE_SONG"
    fi
    pkill -f "aplaymidi -p" 2>/dev/null
    sleep 0.2
    midi_panic
    rm -f "$FLAG_ALARM" "$FLAG_SONG" "$FLAG_SHORTCUT" "$CURRENT_SONG" "$CURRENT_ALARM_SONG"
    pub -t "piano/alarm/status" -m "off"     -r
    pub -t "piano/play/status"  -m "idle"    -r
    pub -t "piano/play/current" -m "unknown" -r
    clear_retained "piano/play" "piano/alarm/set"
    log "✅ Everything stopped"
}

reset_alarm() {
    local reason="${1:-Manual stop}"
    local alarm_song=""
    [ -f "$CURRENT_ALARM_SONG" ] && alarm_song=$(cat "$CURRENT_ALARM_SONG")
    rm -f "$FLAG_ALARM" "$FLAG_SONG" "$CURRENT_ALARM_SONG"
    if [ -f "$PID_FILE_ALARM" ]; then
        kill "$(cat "$PID_FILE_ALARM")" 2>/dev/null
        rm -f "$PID_FILE_ALARM"
    fi
    pkill -f "aplaymidi -p" 2>/dev/null
    sleep 0.1
    pkill -9 -f "aplaymidi -p" 2>/dev/null
    midi_panic
    rm -f "$FLAG_STOP_LOCK"  # cleared at once, so the next repeat can be stopped too
    sleep 0.1
    pub -t "piano/alarm/status"     -m "off"      -r
    pub -t "piano/alarm/stopped_by" -m "$reason"  -r
    pub -t "piano/play/status"      -m "idle"     -r
    pub -t "piano/play/current"     -m "unknown"  -r
    clear_retained "piano/alarm/set"
    if [ -n "$alarm_song" ]; then
        pub -t "piano/play/last_finished" -m "$alarm_song" -r
        pub -t "piano/play/stopped_by"    -m "$reason"     -r
        pub -t "piano/alarm/last_song"    -m "$alarm_song" -r
        log "⏰ Alarm stopped: $reason | $(to_display "$alarm_song")"
    else
        log "⏰ Alarm stopped: $reason"
    fi
}

alsa_logical_reset() {
    log "🔄 ALSA soft reset..."
    midi_panic
    pkill -f "aseqdump -p"  2>/dev/null
    pkill -f "aplaymidi -p" 2>/dev/null
    sleep 0.5
    # "aconnect -x" is never run here: it makes the keyboard vanish from ALSA
    # even while it is physically connected
    log "✅ ALSA soft reset done"
}

full_reset_on_error() {
    local reason="${1:-unknown}"
    publish_disconnect_event "$reason" "full_reset"
    log "❌ Full reset starting..."
    pub -t "piano/status" -m "resetting" -r
    stop_all_playback "Reset: $reason"
    [ "$KEY_LISTENER_PID" -ne 0 ] && kill "$KEY_LISTENER_PID" 2>/dev/null
    [ "$WATCHDOG_PID"     -ne 0 ] && kill "$WATCHDOG_PID"     2>/dev/null
    [ "$MQTT_PID"         -ne 0 ] && kill "$MQTT_PID"         2>/dev/null
    publish_disconnected
    alsa_logical_reset
    log "✅ Full reset done"
}

wait_for_reconnect() {
    log "⏳ Waiting for the keyboard (timeout ${RECONNECT_TIMEOUT}s)..."
    pub -t "piano/status" -m "reconnecting" -r
    local elapsed=0
    while [ $elapsed -lt $RECONNECT_TIMEOUT ]; do
        is_piano_connected && log "✅ Keyboard back after ${elapsed} seconds" && return 0
        sleep 1
        elapsed=$((elapsed + 1))
        # every 5 seconds, not every second, so the live log in Telegram is not flooded
        [ $((elapsed % 5)) -eq 0 ] && log "⏳ Waiting... ${elapsed}s"
    done
    log "❌ Timeout: the keyboard did not come back within ${RECONNECT_TIMEOUT}s"
    pub -t "piano/status" -m "timeout" -r
    return 1
}

alarm_disconnected() {
    local cmd="$1" reason="$2" source="$3"
    publish_disconnect_event "$reason" "$source"
    rm -f "$FLAG_ALARM" "$CURRENT_ALARM_SONG"
    pub -t "piano/alarm/status"       -m "off"                   -r
    pub -t "piano/alarm/stopped_by"   -m "Keyboard disconnected" -r
    pub -t "piano/play/last_finished" -m "$cmd"                  -r
    pub -t "piano/play/stopped_by"    -m "Keyboard disconnected" -r
    pub -t "piano/play/status"        -m "idle"                  -r
    pub -t "piano/play/current"       -m "unknown"               -r
    touch "$FLAG_WATCHDOG_DISCONNECT"
}

run_alarm_loop() {
    local file=$1 cmd=$2
    local display
    display=$(to_display "$cmd")
    trap 'pkill -f "aplaymidi -p" 2>/dev/null; midi_panic; exit 0' SIGTERM SIGINT
    echo "$cmd" > "$CURRENT_ALARM_SONG"
    log "⏰ Alarm starting: $display"

    local iteration=0 fails=0
    while [ -f "$FLAG_ALARM" ] && [ ! -f "$SHUTTING_DOWN" ]; do
        iteration=$((iteration + 1))
        if ! is_piano_connected; then
            alarm_disconnected "$cmd" "Keyboard disconnected during the alarm" "alarm_loop"
            return
        fi
        local port_now exit_code
        port_now=$(get_port)
        if [ "$iteration" -eq 1 ]; then
            log "🎵 Alarm playing on port $port_now: $display"
        else
            log "🔁 Alarm, round $iteration: $display"
        fi
        # stopping kills aplaymidi on purpose; the group hides bash's "Terminated"/"Killed" notice, aplaymidi's own errors still reach the log
        { aplaymidi -p "$port_now" "$file" 2>&3; } 3>&2 2>/dev/null
        exit_code=$?
        if [ $exit_code -ne 0 ] && [ -f "$FLAG_ALARM" ]; then
            log "⚡ Alarm: aplaymidi failed (exit $exit_code)"
            if ! is_piano_connected; then
                alarm_disconnected "$cmd" "aplaymidi failed during the alarm" "alarm_aplaymidi"
                return
            fi
            fails=$((fails + 1))
            if [ "$fails" -ge 3 ]; then
                log "❌ Alarm failed 3 times in a row, stopping"
                rm -f "$PID_FILE_ALARM"   # otherwise reset_alarm kills this loop halfway
                reset_alarm "Playback error"
                return
            fi
        elif [ $exit_code -eq 0 ]; then
            fails=0
        fi
        sleep 0.5
    done
    log "✅ Alarm loop ended"
}

run_key_listener() {
    local port=$1
    local -a KEY_BUFFER=()
    local LAST_NOTE_TIME=0
    log "✅ Listening to the keys on port $port"

    local PEDAL_DOWN_TIME=0
    local PEDAL_HOLD_SENT=0
    local line rc pedal_val note velocity NOW

    aseqdump -p "$port" 2>/dev/null | while true; do
        # read with a timeout, so a pause after a short sequence can be noticed.
        # A code above 128 means time passed with no line; any other means aseqdump closed.
        IFS= read -r -t 0.5 line; rc=$?
        [ -f "$FLAG_WATCHDOG_DISCONNECT" ] && log "⚡ Key listener: the watchdog marked a disconnect, leaving" && break

        # sustain pedal held: checked every round, so "hold" is published while it is still down
        if [ "$PEDAL_DOWN_TIME" -gt 0 ] && [ "$PEDAL_HOLD_SENT" -eq 0 ] \
           && [ $((EPOCHSECONDS - PEDAL_DOWN_TIME)) -ge "$PEDAL_HOLD_SECS" ]; then
            PEDAL_HOLD_SENT=1
            pub -t "piano/pedal" -m "hold"
        fi

        if [ $rc -gt 128 ]; then on_idle; continue; fi
        [ $rc -ne 0 ] && break

        # sustain pedal (CC 64), published only. piano/pedal:
        #   down = pressed | hold = held PEDAL_HOLD_SECS seconds | up = released
        if [[ "$line" == *"Control change"* && "$line" == *"controller 64"* ]]; then
            pedal_val=$(echo "$line" | grep -oP 'value \K[0-9]+')
            [ -z "$pedal_val" ] && continue
            # by the MIDI standard 64 and above is down. Only changes are published,
            # a repeated "down" would otherwise restart the hold timer.
            if [ "$pedal_val" -ge 64 ]; then
                if [ "$PEDAL_DOWN_TIME" -eq 0 ]; then
                    PEDAL_DOWN_TIME=$EPOCHSECONDS
                    PEDAL_HOLD_SENT=0
                    pub -t "piano/pedal" -m "down"
                fi
            elif [ "$PEDAL_DOWN_TIME" -gt 0 ]; then
                PEDAL_DOWN_TIME=0
                pub -t "piano/pedal" -m "up"
            fi
            on_idle
            continue
        fi

        # Many keyboards send Clock and Active Sensing without pause, so read almost
        # never times out. The silence check therefore runs on every other line too.
        if [[ "$line" != *"Note on"* ]]; then on_idle; continue; fi
        note=$(echo "$line" | grep -oP 'note \K[0-9]+')
        [ -z "$note" ] && continue
        # Many keyboards send Note on with velocity 0 instead of Note off.
        # Without this every key release would count as another note.
        velocity=$(echo "$line" | grep -oP 'velocity \K[0-9]+')
        [ "${velocity:-1}" -eq 0 ] && continue
        pub -t "piano/notes" -m "$note"
        NOW=$(date +%s)
        [ $((NOW - LAST_NOTE_TIME)) -gt $BUFFER_TIMEOUT ] && [ ${#KEY_BUFFER[@]} -gt 0 ] && KEY_BUFFER=()
        LAST_NOTE_TIME=$NOW
        KEY_BUFFER+=("$note")
        [ ${#KEY_BUFFER[@]} -gt $MAX_BUFFER ] && KEY_BUFFER=("${KEY_BUFFER[@]:1}")
        # long shortcuts are matched now, short ones in on_idle after a pause
        on_key
        if [ "$note" == "$ALARM_STOP_KEY" ] && [ ! -f "$FLAG_STOP_LOCK" ] && ! screen_active; then
            touch "$FLAG_STOP_LOCK"
            if [ -f "$FLAG_ALARM" ]; then
                log "✅ Stop key $STOP_KEY_NAME stops the alarm"
                reset_alarm "Stop key ($STOP_KEY_NAME)"
            elif [ -f "$FLAG_SONG" ]; then
                log "✅ Stop key $STOP_KEY_NAME stops the song"
                stop_all_playback "Stop key ($STOP_KEY_NAME)"
            else
                log "ℹ️ Stop key $STOP_KEY_NAME: nothing is playing"
            fi
            (sleep 3; rm -f "$FLAG_STOP_LOCK") &
        fi
    done

    log "⚡ Key listener exited"
    # the connection is not checked here: the main loop decides, after a pause and a retry
}

# =========================
# Watchdog: only marks problems, run_bridge acts on them.
# Every 5 minutes it also checks that aseqdump is alive and the port exists.
# =========================
run_watchdog() {
    local port=$1
    local keepalive_counter=0
    log "✅ Watchdog running on port $port"

    while true; do
        sleep "$WATCHDOG_INTERVAL"
        keepalive_counter=$((keepalive_counter + 1))

        # an orphaned lesson flag: the lesson engine died without cleaning up.
        # /proc is checked instead of kill -0, which fails for another user's live process.
        if [ -f "$FLAG_GAME" ]; then
            local game_pid
            game_pid=$(cat "$FLAG_GAME" 2>/dev/null)
            if ! [[ "$game_pid" =~ ^[0-9]+$ ]] || [ ! -d "/proc/$game_pid" ]; then
                rm -f "$FLAG_GAME"
                log "🧹 Orphaned lesson flag removed, shortcuts are back"
            fi
        fi

        # a real disconnect: checked 3 times before it is announced, against false alarms.
        # A keyboard switched off is gone from ALSA at once, so about 2 seconds is enough.
        if ! aconnect -i 2>/dev/null | grep -qF "$PIANO_NAME"; then
            sleep 0.5
            if ! aconnect -i 2>/dev/null | grep -qF "$PIANO_NAME"; then
                sleep 0.5
                if ! aconnect -i 2>/dev/null | grep -qF "$PIANO_NAME"; then
                    publish_disconnect_event "The keyboard vanished from aconnect" "watchdog_aconnect"
                    pub -t "piano/status"       -m "disconnected" -r
                    pub -t "piano/play/status"  -m "idle"         -r
                    pub -t "piano/play/current" -m "unknown"      -r
                    pub -t "piano/game/event"   -m "disconnected" -r
                    touch "$FLAG_WATCHDOG_DISCONNECT"
                    pkill -f "aseqdump -p" 2>/dev/null
                    sleep 1
                    pkill -9 -f "aseqdump -p" 2>/dev/null
                    return
                fi
            fi
        fi

        # aseqdump died: mark it for a restart, run_bridge handles it
        if ! pgrep -f "aseqdump -p" > /dev/null 2>&1; then
            # A grace period: the main loop deletes the restart flag before it starts a
            # new aseqdump. Without this wait the watchdog would see the empty moment,
            # mark it again, and both would loop restarting forever.
            sleep 3
            if pgrep -f "aseqdump -p" > /dev/null 2>&1; then
                continue
            fi
            if [ ! -f "$FLAG_RESTART_LISTENER" ]; then
                log "⚠️ Watchdog: aseqdump died, marking it for a restart"
                touch "$FLAG_RESTART_LISTENER"
                local kl_pid
                kl_pid=$(cat "$KEY_LISTENER_PID_FILE" 2>/dev/null)
                if [ -n "$kl_pid" ] && [ "$kl_pid" -gt 0 ] 2>/dev/null; then
                    kill "$kl_pid" 2>/dev/null
                fi
                pkill -f "aseqdump -p" 2>/dev/null
            fi
            continue
        fi

        # the keyboard came back on a new ALSA port (a quick off/on usually bumps the
        # client number). aconnect still shows the name and aseqdump is still running, but
        # on the old, dead port, so keys stop arriving with nothing looking wrong. Move the
        # listener to the current port.
        local cur_port
        cur_port=$(get_port)
        if [ -n "$cur_port" ] && [ "$cur_port" != "$port" ]; then
            log "🔀 Keyboard is on a new port ($port → $cur_port), restarting the listener"
            port=$cur_port
            if [ ! -f "$FLAG_RESTART_LISTENER" ]; then
                touch "$FLAG_RESTART_LISTENER"
                local kl_pid2
                kl_pid2=$(cat "$KEY_LISTENER_PID_FILE" 2>/dev/null)
                if [ -n "$kl_pid2" ] && [ "$kl_pid2" -gt 0 ] 2>/dev/null; then
                    kill "$kl_pid2" 2>/dev/null
                fi
                pkill -f "aseqdump -p" 2>/dev/null
            fi
            continue
        fi

        # keepalive every 5 minutes: the ALSA port, and the status refreshed.
        # The refresh fixes a late last-will from an old connection overwriting online.
        if [ $((keepalive_counter % KEEPALIVE_INTERVAL)) -eq 0 ]; then
            if [ -f "$FLAG_CONNECTED" ]; then
                pub -t "piano/status" -m "online" -r
            fi
            if [ -z "$(get_port)" ]; then
                log "⚠️ Watchdog keepalive: the ALSA port is gone"
                publish_disconnect_event "The ALSA port is gone" "watchdog_keepalive"
                pub -t "piano/game/event" -m "disconnected" -r
                touch "$FLAG_WATCHDOG_DISCONNECT"
                pkill -f "aseqdump -p" 2>/dev/null
                return
            fi
        fi
    done
}

# =========================
# Starting a song or the alarm
# =========================
start_alarm() {
    local raw="$1" FILE cmd
    split_request "$raw" "Alarm"
    clear_retained "piano/alarm/set"
    FILE=$(find_midi "$SONG") || { log "❌ Alarm: no file named $SONG"; return; }
    cmd=$(midi_name "$FILE")
    [ -f "$FLAG_ALARM" ] && return
    if ! is_piano_connected; then log "⚡ The keyboard is not connected, the alarm was skipped"; return; fi
    log "⏰ Alarm started by $SOURCE: $(to_display "$cmd")"
    publish_song_stopped "Alarm"
    rm -f "$FLAG_SONG"
    pkill -f aplaymidi 2>/dev/null; sleep 0.2
    rm -f "$PID_FILE_SONG" "$CURRENT_SONG"
    touch "$FLAG_ALARM"
    echo "$cmd" > "$CURRENT_ALARM_SONG"
    pub -t "piano/alarm/status"     -m "on"        -r
    pub -t "piano/alarm/stopped_by" -m "unknown"   -r
    pub -t "piano/play/status"      -m "playing"   -r
    pub -t "piano/play/current"     -m "$cmd"      -r
    pub -t "piano/play/source"      -m "$SOURCE"   -r
    pub -t "piano/play/duration"    -m "$(get_duration "$FILE")"
    run_alarm_loop "$FILE" "$cmd" &
    echo $! > "$PID_FILE_ALARM"
}

start_song() {
    local raw="$1" FILE cmd display port_now
    split_request "$raw" "MQTT"
    clear_retained "piano/play"
    FILE=$(find_midi "$SONG") || { log "❌ Song: no file named $SONG"; return; }
    cmd=$(midi_name "$FILE")
    display=$(to_display "$cmd")
    [ -f "$FLAG_ALARM" ] && { log "⚡ Blocked: the alarm is playing, $display skipped"; return; }
    [ -f "$FLAG_SONG"  ] && { log "⚡ Blocked: a song is already playing, $display skipped"; return; }
    [ -f "$FLAG_GAME"  ] && { log "⚡ Blocked: a lesson is running, $display skipped"; return; }
    if ! is_piano_connected; then log "⚡ The keyboard is not connected, the song was skipped"; return; fi
    port_now=$(get_port)
    log "🎵 Song starting: $display on port $port_now, started by $SOURCE"
    touch "$FLAG_SONG"
    echo "$cmd" > "$CURRENT_SONG"
    pub -t "piano/play/status"   -m "playing"   -r
    pub -t "piano/play/current"  -m "$cmd"      -r
    pub -t "piano/play/source"   -m "$SOURCE"   -r
    pub -t "piano/play/duration" -m "$(get_duration "$FILE")"
    (
        # stopping kills aplaymidi on purpose; the group hides bash's "Terminated"/"Killed" notice, aplaymidi's own errors still reach the log
        { aplaymidi -p "$port_now" "$FILE" 2>&3; } 3>&2 2>/dev/null
        exit_code=$?
        # the flag is gone = the alarm replaced this song, its status must stay
        [ -f "$FLAG_SONG" ] || exit 0
        rm -f "$FLAG_SONG" "$PID_FILE_SONG" "$CURRENT_SONG"
        if [ $exit_code -ne 0 ]; then
            log "⚡ Song: aplaymidi failed (exit $exit_code): $display"
            pub -t "piano/play/last_finished" -m "$cmd" -r
            if is_piano_connected; then
                pub -t "piano/play/stopped_by" -m "Playback error" -r
            else
                pub -t "piano/play/stopped_by" -m "Keyboard disconnected" -r
                publish_disconnect_event "aplaymidi failed during a song" "song_aplaymidi"
                pub -t "piano/status" -m "disconnected" -r
                touch "$FLAG_WATCHDOG_DISCONNECT"
            fi
        else
            log "✅ Song finished: $display"
            pub -t "piano/play/last_finished" -m "$cmd" -r
            pub -t "piano/play/stopped_by"    -m "Finished" -r
            midi_panic       # the file may have left the volume down or a voice changed
        fi
        pub -t "piano/play/status"  -m "idle"    -r
        pub -t "piano/play/current" -m "unknown" -r
    ) &
    echo $! > "$PID_FILE_SONG"
}

stop_request() {
    local reason="${1:-Manual stop}"
    [ -f "$FLAG_STOP_LOCK" ] && return
    touch "$FLAG_STOP_LOCK"
    clear_retained "piano/stop"
    if [ -f "$FLAG_ALARM" ]; then
        reset_alarm "$reason"
    elif [ -f "$FLAG_SONG" ]; then
        stop_all_playback "$reason"
        log "⏹ Stopped: $reason"
    else
        log "ℹ️ Stop: nothing is playing"
    fi
    (sleep 3; rm -f "$FLAG_STOP_LOCK") &
}

# =========================
# Main
# =========================
run_bridge() {
    rm -f "$FLAG_WATCHDOG_DISCONNECT" "$FLAG_STOP_LOCK" "$FLAG_RESTART_LISTENER" \
          "$LAST_LOG_BASE_FILE" "$KEY_LISTENER_PID_FILE" "$SHUTTING_DOWN"
    KEY_LISTENER_PID=0
    restart_count=0
    restart_window_start=$(date +%s)

    log "🔍 Looking for the keyboard ($PIANO_NAME)..."

    # 5 tries: ALSA is sometimes slow right after boot
    local piano_found=false i
    for i in 1 2 3 4 5; do
        if is_piano_connected; then
            piano_found=true
            break
        fi
        log "⏳ ALSA not ready yet, try $i/5"
        sleep 2
    done

    if [ "$piano_found" = false ]; then
        log "⚡ The keyboard is not connected, resetting"
        publish_disconnected
        full_reset_on_error "Keyboard not found at start"
        if ! wait_for_reconnect; then
            log "❌ The keyboard did not come back, handing over to systemd"
            pub -t "piano/status" -m "offline" -r
            note_startup_failure
            exit 1
        fi
    fi

    PORT=$(get_port)
    if [ -z "$PORT" ]; then
        log "❌ No port found, handing over to systemd"
        pub -t "piano/status" -m "offline" -r
        note_startup_failure
        exit 1
    fi

    pkill -f "aseqdump -p"  2>/dev/null
    pkill -f "aplaymidi -p" 2>/dev/null
    sleep 0.3
    midi_panic
    rm -f "$FLAG_SONG" "$FLAG_ALARM" "$FLAG_SHORTCUT" "$FLAG_LEARN" "$PID_FILE_ALARM" \
          "$PID_FILE_SONG" "$CURRENT_ALARM_SONG" "$CURRENT_SONG" "$FLAG_STOP_LOCK"

    clear_retained "piano/stop" "piano/alarm/set" "piano/play" "piano/log/control"
    pub -t "piano/alarm/status"     -m "off"     -r
    pub -t "piano/alarm/stopped_by" -m "unknown" -r
    pub -t "piano/play/status"      -m "idle"    -r
    pub -t "piano/play/current"     -m "unknown" -r
    publish_connected "$PORT"

    # ─── MQTT listener ───
    (
        local line topic raw cmd
        while true; do
            # Last will: the broker publishes offline by itself the moment the
            # connection drops without a clean close, so a crash or a reboot never
            # leaves a stuck "online" behind.
            # Each message is printed after a mark drawn at startup. Without it, a payload
            # holding a line break was read as a second line, and that line's first word was
            # taken for a topic: a sender allowed one topic could reach the others. The mark
            # is random and never published, so only real messages begin with it, and the
            # extra lines of a multi-line payload are passed over.
            mosquitto_sub "${MQTT_ARGS[@]}" \
                -t "piano/play" -t "piano/alarm/set" \
                -t "piano/stop" -t "piano/log/control" \
                -t "piano/shortcut/cmd" \
                --keepalive 60 \
                -i "$MQTT_CLIENT_ID" \
                --will-topic "piano/status" \
                --will-payload "offline" \
                --will-retain \
                -F "$MSG_MARK %t %p" | while read -r line; do

                case "$line" in "$MSG_MARK "*) line=${line#"$MSG_MARK "} ;; *) continue ;; esac
                topic=${line%% *}
                case "$topic" in
                    piano/play|piano/alarm/set|piano/stop|piano/log/control|piano/shortcut/cmd) ;;
                    *) continue ;;
                esac
                # everything after the topic, untouched: song names keep their spaces and quotes
                raw=$(echo "$line" | cut -s -d' ' -f2- | tr -d '\r')
                is_valid_cmd "$raw" || continue

                case "$topic" in
                    piano/shortcut/cmd)
                        handle_shortcut_cmd "$raw" ;;
                    piano/log/control)
                        cmd=$(echo "$raw" | xargs)
                        if [[ "$cmd" == "start" ]]; then
                            touch "$FLAG_LIVE_LOG"
                            clear_retained "piano/log/control"
                            log "🟢 Live log on"
                        elif [[ "$cmd" == "stop" ]]; then
                            log "🔴 Live log off"
                            rm -f "$FLAG_LIVE_LOG"
                            clear_retained "piano/log/control"
                        fi ;;
                    piano/stop)
                        stop_request "$raw" ;;
                    piano/alarm/set)
                        start_alarm "$raw" ;;
                    piano/play)
                        start_song "$raw" ;;
                esac
            done
            log "⚠️ Lost the broker, reconnecting..."
            sleep 1
        done
    ) &
    MQTT_PID=$!

    run_key_listener "$PORT" &
    set_key_listener_pid $!

    run_watchdog "$PORT" &
    WATCHDOG_PID=$!

    # ─── main wait loop ───
    # In order of priority:
    # 1. a real disconnect (the watchdog marked it)
    # 2. aseqdump restart (the watchdog asked for it)
    # 3. an unexpected exit: restart it, and count
    local now NEW_PORT
    while true; do
        wait "$KEY_LISTENER_PID"

        # in the middle of shutting down there is nothing to revive
        [ -f "$SHUTTING_DOWN" ] && break

        sleep 2  # give ALSA a moment to settle

        [ -f "$SHUTTING_DOWN" ] && break

        # 1. a real disconnect, marked by the watchdog
        if [ -f "$FLAG_WATCHDOG_DISCONNECT" ]; then
            log "❌ Real disconnect, full reset"
            kill "$WATCHDOG_PID" 2>/dev/null
            kill "$MQTT_PID"    2>/dev/null
            wait "$WATCHDOG_PID" "$MQTT_PID" 2>/dev/null
            full_reset_on_error "Key listener exited and the keyboard is gone"
            wait_for_reconnect || true
            log "⚡ Bridge exiting, systemd starts it again"
            break
        fi

        # 2. aseqdump died and the watchdog asked for a restart (keyboard still there)
        if [ -f "$FLAG_RESTART_LISTENER" ]; then
            rm -f "$FLAG_RESTART_LISTENER"
            now=$(date +%s)
            # a real window: reset an hour after the window began, not after the last
            # restart, otherwise frequent restarts would never reset the count
            if [ $((now - restart_window_start)) -gt 3600 ]; then
                restart_count=0
                restart_window_start=$now
            fi
            restart_count=$((restart_count + 1))
            if [ $restart_count -gt $MAX_RESTARTS ]; then
                log "❌ More than $MAX_RESTARTS restarts, full reset"
                kill "$WATCHDOG_PID" 2>/dev/null; kill "$MQTT_PID" 2>/dev/null
                full_reset_on_error "Too many aseqdump restarts"
                wait_for_reconnect || true
                break
            fi
            NEW_PORT=$(get_port)
            [ -n "$NEW_PORT" ] && PORT=$NEW_PORT
            log "🔄 Restarting aseqdump ($restart_count/$MAX_RESTARTS) on port $PORT..."
            pkill -f "aseqdump" 2>/dev/null
            sleep 0.5
            run_key_listener "$PORT" &
            set_key_listener_pid $!
            log "✅ aseqdump restarted, PID $KEY_LISTENER_PID"
            continue
        fi

        # 3. check the keyboard is there before restarting
        if ! is_piano_connected; then
            log "❌ The keyboard is gone after the key listener exited, full reset"
            kill "$WATCHDOG_PID" 2>/dev/null; kill "$MQTT_PID" 2>/dev/null
            wait "$WATCHDOG_PID" "$MQTT_PID" 2>/dev/null
            full_reset_on_error "Keyboard not found after the key listener exited"
            wait_for_reconnect || true
            log "⚡ Bridge exiting, systemd starts it again"
            break
        fi

        # 4. an unexpected exit: restart it
        now=$(date +%s)
        if [ $((now - restart_window_start)) -gt 3600 ]; then
            restart_count=0
            restart_window_start=$now
        fi
        restart_count=$((restart_count + 1))
        if [ $restart_count -gt $MAX_RESTARTS ]; then
            log "❌ Repeated unexpected exits, full reset"
            kill "$WATCHDOG_PID" 2>/dev/null; kill "$MQTT_PID" 2>/dev/null
            full_reset_on_error "Repeated unexpected exits"
            wait_for_reconnect || true
            break
        fi
        NEW_PORT=$(get_port)
        [ -n "$NEW_PORT" ] && PORT=$NEW_PORT
        log "⚠️ The key listener exited for no reason ($restart_count/$MAX_RESTARTS), restarting on port $PORT"
        sleep 0.5
        run_key_listener "$PORT" &
        set_key_listener_pid $!
        continue
    done

    pub -t "piano/status"       -m "offline" -r
    pub -t "piano/play/status"  -m "idle"    -r
    pub -t "piano/play/current" -m "unknown" -r
    exit 1
}

# =========================
# Entry point
# =========================
log "✅ Piano bridge starting"
build_panic_midi
wait_for_broker
pub -t "piano/play/status"  -m "idle"     -r
pub -t "piano/play/current" -m "unknown"  -r
run_bridge
