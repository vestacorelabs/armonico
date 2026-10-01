#!/usr/bin/env python3
"""
piano_game.py - learning songs on any MIDI keyboard, a few short pieces at a time.

The method: a short piece with the key numbers on screen, then from memory, then with the
numbers again, then from memory, until a round from memory comes out clean. That works for
short pieces and breaks on long ones, because too much new material in one sitting does not
stick. So a song is cut into short pieces where the music breathes, and learned a few at a time:

  1. measure - everything learned so far in one go, from the first note: once alone, then the
               same again with the song's own accompaniment. No numbers. This first run is
               what moves every piece's count, because a melody is remembered forward and not
               from its middle, and here nothing has warmed it up yet.
  2. new     - the pace's number of new pieces, each through the cycle: with, without, with, without.
               A piece is done when a round without numbers comes out clean.
  3. connect - every new piece is played together with the one before it, without numbers.
  4. measure - the same two runs again, now with today's piece in them. This one says how the
               lesson went; what slipped in either run is practiced again with the numbers back.

Commands (Telegram -> Home Assistant -> piano/game/cmd):
  /lesson <song>          today's session
  /lesson <song> reset    start the song over
  /lessonstop             stop and save
  /lessons                songs and how much of each is learned
  /getmidi <name|number>  search a public MIDI archive and download a song

Keys during a lesson: the first black key shows the numbers again, the third black key
exits. The stop key still stops the alarm. While a lesson runs, the lesson flag mutes the
bridge's shortcuts. The alarm always wins.
"""
import difflib, faulthandler, fcntl, hashlib, hmac, json, logging, os, queue, re, secrets, shutil, signal, \
    subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from urllib.request import urlopen, Request, HTTPRedirectHandler, build_opener
from pathlib import Path

import mido
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from piano_numbers import (best_shift, white_number, nearest_white, is_black, timed_melody,
                           melody_events, melody_track, rhythm_and_starts, band_events,
                           FIRST_WHITE, HIGH)
from piano_hands import plan_hands, hand_label, note_name, thumb_key
from piano_lang import translate, plural as _plural, telegram, LANGS

# =========================
# Settings
# =========================
# Everything that differs between machines comes from the environment, which systemd
# loads from the settings file the installer writes.
ENV = os.environ
DATA_DIR = Path(ENV.get("DATA_DIR", "/var/lib/armonico"))
MIDI_DIR = Path(ENV.get("SONGS_DIR", str(DATA_DIR / "songs")))
SHORTCUTS_FILE = DATA_DIR / "shortcuts.conf"
PROFILES_FILE = DATA_DIR / "profiles.json"     # who plays on this piano, and who is at it now
TEXT_OVERRIDES_FILE = DATA_DIR / "text_overrides.json"  # wording corrected on the screen; the code stays as is
# The wording that travels with the code, so a fresh install looks the way the project
# was written rather than the way it was first drafted. Whatever a machine edits for
# itself sits above this and wins, so an update never overwrites local wording.
TEXT_DEFAULTS_FILE = Path(__file__).resolve().parent / "text_defaults.json"
MEDIA_DIR = DATA_DIR / "media"                          # images uploaded from edit mode, kept beside the data
FONT_DIR = Path(__file__).resolve().parent / "fonts"   # Heebo, so the screen needs no internet
STATUS_PAGE = Path(__file__).resolve().parent.parent / "screen" / "piano-status.html"   # the touch status screen
SOUND_DIR = Path(__file__).resolve().parent / "sounds" / "piano"   # the grand piano, recorded (CC BY 3.0)
# the classical pieces shipped with the project (installed from setup/music): shown as "system"
# and never deletable, so only songs a person added can be removed from the screen
SYSTEM_SONGS = frozenset(p.stem for p in (Path(__file__).resolve().parent.parent / "setup" / "music").glob("*.mid"))
REC_KEEP = 30                                  # recordings kept; the oldest go first

RUN_DIR = Path(ENV.get("RUN_DIR", "/tmp"))      # flags shared with the bridge
FLAG_GAME = RUN_DIR / "piano_game_active"
FLAG_ALARM = RUN_DIR / "piano_alarm_active"
FLAG_SONG = RUN_DIR / "piano_song_active"      # the bridge is playing a song
FLAG_SCREEN = RUN_DIR / "piano_screen_keys"    # the screen is sounding notes: the bridge ignores them
FLAG_PAGE = RUN_DIR / "piano_page_open"        # a lesson screen is open and in view: no shortcuts from the keys
_page_seen = [0.0]
SCREEN_CODE_FILE = DATA_DIR / "screen_code"    # the code a screen needs before it can act
SCREEN_SECRET_FILE = DATA_DIR / "screen_secret"   # signs the pass a screen keeps; replacing it ends every pass
CODE_STATE_FILE = DATA_DIR / "code_enabled"    # the switch on the screen and in the terminal
EDIT_FLAG = DATA_DIR / "edit-mode"             # while it exists, the screen offers its editing tools
COOKIE_NAME = "armonico_pass"
COOKIE_DAYS = 14                               # a screen in daily use never asks again: every visit renews it
# On unless the installer turned it off. A screen that acts on the keyboard, reads the
# history and can start a system update is not something any device on the network should
# reach without being let in once.
SCREEN_CODE_DEFAULT = ENV.get("SCREEN_CODE", "on").strip().lower() not in ("off", "no", "false", "0")
LAST_CHAT_FILE = DATA_DIR / "last_chat"        # where a lesson started from the screen reports

PIANO_NAME = ENV.get("MIDI_DEVICE", "")
MQTT_HOST = ENV.get("MQTT_HOST", "localhost")
MQTT_PORT = int(ENV.get("MQTT_PORT", "1883"))
MQTT_USER = ENV.get("MQTT_USER", "")
MQTT_PASS = ENV.get("MQTT_PASS", "")
# The broker cannot be relied on to say who may publish what: the Home Assistant add-on
# does not enforce topic rules at all. So the three commands that hand out the screen
# code, open the editing tools or change whose progress is recorded carry their own proof.
MQTT_ADMIN_SECRET = ENV.get("MQTT_ADMIN_SECRET", "")
GUARDED_ACTIONS = ("code", "editmode", "profile")


def _control_keys():
    """The two lowest black keys of the keyboard, first and third: never needed for playing.

    On a keyboard that starts on C these are C# and F#, the keys numbered 1# and 4#.
    """
    low = int(ENV.get("KEY_LOWEST", "36"))
    blacks = [n for n in range(low, low + 12) if n % 12 in (1, 3, 6, 8, 10)]
    return (int(ENV.get("LESSON_SHOW_KEY", blacks[0])),
            int(ENV.get("LESSON_EXIT_KEY", blacks[2])))


KEY_SHOW, KEY_EXIT = _control_keys()

# The method
NEW_PER_DAY = 2           # new pieces per sitting: more than this does not stick
MIN_PHRASE, MAX_PHRASE = 4, 8   # notes per piece, about the size of the shortcuts you remember
WAIT_FIRST = 12           # seconds to wait for the first note of a piece
WAIT_NEXT = 6             # and between notes; silence this long counts as stuck
IDLE_LIMIT = 120          # no key at all for this long closes the lesson
WATCH_EVERY = 1           # seconds between checks that the piano is still plugged in
WATCH_MISSES = 2          # checks in a row that must fail before it counts as unplugged

# Sound
OUT_BACKEND = "amidi"     # "amidi" is the proven path on this machine
AMIDI_DEVICE = ""         # empty: found with "amidi -l"
PORT_WARMUP = 0.5
MELODY_PROGRAM = 0        # voice of channel 1, where your keys and the demos sound: grand piano
SPEED = 60                # demo and accompaniment speed, percent of the original
TEMPO_SCALE = 1.3         # the note length limits below are set for this scale
NOTE_SECS, NOTE_GAP = 0.45, 0.12   # demo timing when the song has no rhythm of its own
MIN_NOTE, MAX_NOTE = 0.18, 1.4
MAX_GAP = 0.9
PAUSE_AFTER = 1.2         # a breath between rounds
UI_BIND = ENV.get("LESSON_BIND", "").strip() or "0.0.0.0"   # which address the screen listens on; all of them unless set
UI_PORT = int(ENV.get("LESSON_UI_PORT", "8099"))   # the lesson screen: http://<this machine>:8099

# Count-in: 4 clicks on the drum channel before every round
CLICK_CHANNEL, CLICK_NOTE, CLICK_BEATS, CLICK_VELOCITY = 9, 76, 4, 70
MIN_BEAT, MAX_BEAT = 0.35, 1.0

# Accompaniment in the last part: taken from the song file, it follows your pace
FOLLOW_MIN, FOLLOW_MAX = 0.5, 3.0
FOLLOW_SMOOTH = 0.4
FINAL_RING = 2.5
DOUBLE_WINDOW = 0.06
HINT_SILENCE = 6          # seconds stuck in a run before the next note is named. Being stuck with
                          # nothing to go on teaches nothing, so the help always comes in the end
TEST_END_SILENCE = 45     # a run ends by itself only after this long with no key: the run at the
                          # start of a lesson is played cold, and stopping to think is normal.
                          # Leaving early is the stop button or the exit key, not a short silence
BAND_LEVEL = 80           # band loudness, percent of how hard you play
PLAYER_START = 70
PLAYER_SMOOTH = 0.3
BAND_HOLD = 1.5           # the band holds its chord this long while you hesitate

# Fetching songs
# The version, from the VERSION file at the top of the installed program
try:
    APP_VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
except OSError:
    APP_VERSION = "dev"
try:
    APP_REPO = (Path(__file__).resolve().parent.parent / "REPO").read_text().strip()
except OSError:
    APP_REPO = ""
UPDATE_REQUEST = DATA_DIR / "update-request"      # watched by the root update unit
UPDATE_STATUS = DATA_DIR / "update-status.json"   # written by it
_LATEST = {"checked": 0, "tag": None, "error": None}


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def update_info(force=False):
    """This version, the newest release on GitHub (asked at most once an hour), and the last update's state."""
    now = time.time()
    if APP_REPO and (force or now - _LATEST["checked"] > 3600):
        _LATEST.update(checked=now, error=None)
        try:
            req = Request(f"https://api.github.com/repos/{APP_REPO}/releases/latest",
                          headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
            with urlopen(req, timeout=HTTP_TIMEOUT) as r:
                _LATEST["tag"] = json.loads(r.read(200000)).get("tag_name")
        except Exception as e:
            _LATEST["error"] = ("no release published yet", {}) if "404" in str(e) else ("GitHub did not answer: {e}", {"e": e})
    try:
        status = json.loads(UPDATE_STATUS.read_text())
    except (OSError, ValueError):
        status = None
    tag = _LATEST["tag"]
    err = _LATEST["error"]
    return {"version": APP_VERSION, "latest": tag, "error": tr(err[0], **err[1]) if err else None,
            "available": bool(tag) and version_tuple(tag) > version_tuple(APP_VERSION),
            "requested": UPDATE_REQUEST.exists(), "status": status}


def attachment(name):
    """A Content-Disposition value that survives a Hebrew name: HTTP headers are Latin-1, so the
    plain name is ASCII only and the real one travels in the UTF-8 form (RFC 5987)."""
    from urllib.parse import quote as q
    plain = re.sub(r'[^A-Za-z0-9 ._()-]', "_", name) or "download"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{q(name)}"


_SAFE_SONG = r"[\w \-().,&֐-׿]+"
_BACKUP_SONG = re.compile(r"songs/(" + _SAFE_SONG + r"\.(mid|midi|credit\.json))", re.I)
_BACKUP_REC = r"recordings/[0-9a-f-]{1,40}\.(json|mid)"
PROFILE_ITEMS = ("lessons.json", "history.json", "settings.json")
RESTORE_MAX = 64 * 1024 * 1024            # the largest backup that is accepted
RESTORE_FILE = DATA_DIR / ".restore.zip"  # the uploaded backup, kept until it is applied
BACKUP_FORMAT = 2
# what an older backup (the whole data folder, every profile) could hold, besides the files above
_LEGACY_TOP = {"profiles.json", "shortcuts.conf", "text_overrides.json", "code_enabled", "volume", "last_chat",
               "install_id", "screen_secret", "edit-mode", "update-status.json"}


def backup_name():
    """The file name of a backup: the profile it holds and the day. Hebrew is fine: the header carries
    the name in UTF-8 (see attachment), and nothing a file system refuses is left in it."""
    prof = load_profiles()
    me = next((p for p in prof["list"] if p["id"] == prof["active"]), {})
    who = re.sub(r'[\\/:*?"<>|\x00-\x1f‎‏‪-‮]', "_", profile_label(me)).strip(" .") or "profile"
    return f"armonico-backup-{who[:30]}-{time.strftime('%Y-%m-%d')}.zip"


def backup_zip():
    """The profile that is signed in, in one zip: its progress, history, settings and recordings, and the
    songs (which every profile shares). Not the other profiles, and not the piano's own settings. Only
    files the restore accepts go in, so a backup always passes its own check."""
    import io, zipfile
    prof = load_profiles()
    pid = prof["active"]
    me = next((p for p in prof["list"] if p["id"] == pid), {})
    base = profile_dir(pid)
    manifest = {"app": "armonico", "format": BACKUP_FORMAT, "version": APP_VERSION,
                "date": time.strftime("%Y-%m-%d %H:%M"), "profile": {"id": pid, "name": me.get("name") or ""}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("backup.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for item in PROFILE_ITEMS:
            if (base / item).is_file():
                z.write(base / item, item)
        rec = base / "recordings"
        if rec.is_dir():
            for f in sorted(rec.iterdir()):
                if f.is_file() and re.fullmatch(_BACKUP_REC, f"recordings/{f.name}"):
                    z.write(f, Path("recordings") / f.name)
        if MIDI_DIR.is_dir():
            for f in sorted(MIDI_DIR.glob("*")):
                if f.is_file() and _BACKUP_SONG.fullmatch(f"songs/{f.name}"):
                    z.write(f, Path("songs") / f.name)
    return buf.getvalue()


def _backup_names(z):
    """The files of an uploaded backup, after the checks that keep a hostile zip from doing harm."""
    infos = z.infolist()
    if len(infos) > 20000 or sum(i.file_size for i in infos) > 150 * 1024 * 1024 or any(i.file_size > 32 * 1024 * 1024 for i in infos):
        raise ValueError("The backup is too large")
    names = []
    for i in infos:
        n = i.filename
        if i.is_dir():
            continue
        if n.startswith(("/", "\\")) or ".." in Path(n).parts or "\\" in n:
            raise ValueError("The backup holds a file with an unsafe name")
        names.append(n)
    return names


def _check_backup(z, names):
    """Accepts only a backup this program made: every file is one it writes, and the ones that are read
    back are in the form they are read in. Returns (manifest or None, profiles in it). Anything else is
    refused, and nothing of it is kept."""
    bad = ValueError("This is not a backup this version can restore")
    manifest = None
    if "backup.json" in names:
        try:
            manifest = json.loads(z.read("backup.json"))
            prof = manifest["profile"]
            ok = (manifest.get("app") == "armonico" and isinstance(manifest.get("format"), int)
                  and 1 <= manifest["format"] <= BACKUP_FORMAT and re.fullmatch(r"main|p[0-9a-f]{8}", str(prof["id"])))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise bad
        if not ok:
            raise ValueError("This backup was made by a newer version, or is not an Armonico backup")
    found = {}
    for n in names:
        top = "/" not in n
        if n == "backup.json" and manifest is not None:
            continue
        if top and n in PROFILE_ITEMS:
            found["main"] = ""
        elif re.fullmatch(_BACKUP_REC, n) or _BACKUP_SONG.fullmatch(n):
            if n.startswith("recordings/"):
                found["main"] = ""
        elif manifest is None and (n in _LEGACY_TOP or re.fullmatch(r"media/[A-Za-z0-9._-]+", n)):
            pass
        elif manifest is None and re.fullmatch(r"profiles/p[0-9a-f]{8}/((lessons|history|settings)\.json|" + _BACKUP_REC + ")", n):
            found[n.split("/")[1]] = ""
        else:
            raise bad                              # a file this program never writes: not its backup
    if manifest is not None:
        found = {str(manifest["profile"]["id"]): str(manifest["profile"].get("name") or "")[:24]}
    elif "profiles.json" in names:
        try:
            for p in json.loads(z.read("profiles.json")).get("list", []):
                if isinstance(p, dict) and p.get("id") in found:
                    found[p["id"]] = str(p.get("name") or "")[:24]
        except (ValueError, AttributeError):
            raise bad
    # what is read back has to be what it is said to be
    for n in names:
        try:
            if re.fullmatch(r"(profiles/p[0-9a-f]{8}/)?(lessons|history|settings)\.json", n):
                data = json.loads(z.read(n))
                if not isinstance(data, list if n.endswith("history.json") else dict):
                    raise ValueError
            elif re.fullmatch(r"(profiles/p[0-9a-f]{8}/)?recordings/.*\.json", n):
                if not isinstance(json.loads(z.read(n)).get("events"), list):
                    raise ValueError
            elif n.lower().startswith("songs/") and n.lower().endswith((".mid", ".midi")):
                with z.open(n) as f:
                    if f.read(4) != b"MThd":
                        raise ValueError
        except (ValueError, AttributeError, KeyError):
            raise ValueError("The backup holds a damaged file")
    return manifest, found


def restore_inspect(raw):
    """Reads an uploaded backup and says what is in it, and into which profile it would go. Nothing is
    written yet, and a file that is not a backup of this program is refused here."""
    import io, zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = _backup_names(z)
        manifest, found = _check_backup(z, names)
    except zipfile.BadZipFile:
        raise ValueError("This is not a backup file")
    prof = load_profiles()
    here = {p["id"]: p for p in prof["list"]}
    out = []
    for pid, name in found.items():
        p = here.get(pid)
        out.append({"id": pid, "name": (p.get("name") if p else name) or "", "exists": p is not None,
                    "locked": bool(p and p.get("pin") and prof["active"] != pid)})
    songs = sum(1 for n in names if _BACKUP_SONG.fullmatch(n) and not n.lower().endswith(".json"))
    if not out and not songs:
        raise ValueError("This is not a backup this version can restore")
    RESTORE_FILE.write_bytes(raw)
    os.chmod(RESTORE_FILE, 0o600)
    return {"profiles": out, "songs": songs, "date": str((manifest or {}).get("date") or "")[:20]}


def restore_into(source, with_songs):
    """Puts one profile of the uploaded backup into the same profile of this piano, which becomes the
    profile at the piano. A profile that was deleted is made again first. Its progress, history and
    settings are replaced; recordings and songs are added and never overwritten. Called with
    PROFILE_LOCK held and the PIN of a locked profile already checked."""
    import zipfile
    if not RESTORE_FILE.exists():
        raise ValueError("Choose the backup file again")
    if not re.fullmatch(r"main|p[0-9a-f]{8}", str(source)):
        raise ValueError("Unknown profile")
    z = zipfile.ZipFile(RESTORE_FILE)
    names = _backup_names(z)
    manifest, found = _check_backup(z, names)
    if source not in found:
        raise ValueError("Unknown profile")
    root = manifest is not None or source == "main"
    pre = "" if root else f"profiles/{source}/"
    prof = load_profiles()
    target = next((p for p in prof["list"] if p["id"] == source), None)
    if target is None:                                        # it was deleted: made again, under the same id
        if len(prof["list"]) >= PROFILES_MAX:
            raise ValueError("There is room for 12 profiles")
        name = valid_name(found.get(source)) or "Restored"
        taken = {(p.get("name") or "").lower() for p in prof["list"]}
        base, k = name, 2
        while name.lower() in taken:
            name, k = f"{base[:20]} ({k})", k + 1
        target = {"id": source, "name": name, "pin": None, "created": int(time.time()), "lang": load_settings()["lang"]}
        prof["list"].append(target)
        save_profiles(prof)
    dest = profile_dir(source)
    dest.mkdir(parents=True, exist_ok=True)
    wrote = {"files": 0, "recordings": 0, "songs": 0}
    for item in PROFILE_ITEMS:
        if pre + item in names:
            tmp = dest / (item + ".tmp")
            tmp.write_bytes(z.read(pre + item))
            tmp.replace(dest / item)
            wrote["files"] += 1
    for n in names:
        m = re.fullmatch(re.escape(pre) + r"recordings/([0-9a-f-]{1,40}\.(json|mid))", n)
        if m:
            f = dest / "recordings" / m.group(1)
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(z.read(n))
                wrote["recordings"] += 1
    if with_songs:
        MIDI_DIR.mkdir(parents=True, exist_ok=True)
        for n in names:
            m = _BACKUP_SONG.fullmatch(n)
            if m and not (MIDI_DIR / m.group(1)).exists():
                (MIDI_DIR / m.group(1)).write_bytes(z.read(n))
                wrote["songs"] += 1
    prof = load_profiles()
    if prof["active"] != source:
        switch_profile(prof, next(p for p in prof["list"] if p["id"] == source))
    RESTORE_FILE.unlink(missing_ok=True)
    return {"id": source, "name": target.get("name") or "", **wrote}


# Wikimedia asks every program to say who it is and where to find it
USER_AGENT = f"Armonico/{APP_VERSION} (+https://github.com/vestacorelabs/armonico)"

# Song search: only archives whose files may legally be downloaded and kept, each with its
# licence recorded next to the file. See docs/sources.md for why each one is in, or out.
MUTOPIA_SEARCH = "https://www.mutopiaproject.org/cgibin/make-table.cgi?searchingfor={}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
DOWNLOAD_PREFIXES = ("https://www.mutopiaproject.org/ftp/", "https://upload.wikimedia.org/wikipedia/commons/")
# the pages that are searched; a file is only ever downloaded from DOWNLOAD_PREFIXES
SEARCH_PREFIXES = ("https://www.mutopiaproject.org/cgibin/", "https://commons.wikimedia.org/w/api.php")
SEARCH_RESULTS = 60        # the screen pages through them, 10 at a time
TELEGRAM_RESULTS = 10      # a chat message stays short
SEARCH_PAGES = 4           # Mutopia answers 10 pieces a page
HTTP_TIMEOUT = 20

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("piano_game").info
def label(n):
    """MIDI note -> the number written on the key. Black keys get # after the white on their left."""
    num = white_number(nearest_white(n))
    return f"{num}#" if is_black(n) else str(num)


def labels(notes):
    return " ".join(label(n) for n in notes)


UI = {"mode": "idle", "song": "", "title": "", "seq": [], "midi": [], "show": True, "fing": [],
      "piano": None,
      "demo": -1, "expect": -1, "pos": 0, "total": 0, "msg": "",
      "live": [],           # the last keys pressed on the keyboard: [id, note], for live mode
      "notice": [0, ""],    # the last message the engine sent, for the screen as well as Telegram
      "summary": None,      # the result of the last lesson, shown on the screen when it ends
      "replay": None,       # the recording being played back: {"id", "started"}
      "pedal": False,       # the keyboard's own sustain pedal, from the bridge
      "volume": None}       # the master volume last set from the screen, percent
UI_LOCK = threading.Lock()
LIVE_KEEP = 32              # presses kept for the screen; it asks every 100 ms, so none is missed
_live_id = 0


def screen_code(fresh=False):
    """The 6-digit code a screen enters once. Made on first use, kept in the data folder."""
    if not fresh:
        try:
            code = SCREEN_CODE_FILE.read_text().strip()
            if re.fullmatch(r"\d{6}", code):
                return code
        except OSError:
            pass
    code = f"{secrets.randbelow(10 ** 6):06d}"
    try:
        SCREEN_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCREEN_CODE_FILE.write_text(code + "\n")
        os.chmod(SCREEN_CODE_FILE, 0o600)
    except OSError as e:
        log(f"The screen code was not saved: {e}")
    return code


def code_on():
    """Whether a screen is asked for the code. The installer sets the first answer; the
    settings and the terminal write the file that overrides it from then on."""
    try:
        state = CODE_STATE_FILE.read_text().strip().lower()
        if state in ("on", "off"):
            return state == "on"
    except OSError:
        pass
    return SCREEN_CODE_DEFAULT


def set_code_on(on):
    CODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODE_STATE_FILE.write_text("on\n" if on else "off\n")
    try:
        os.chmod(CODE_STATE_FILE, 0o600)
    except OSError:
        pass


def screen_secret(fresh=False):
    """The key the passes are signed with. It never leaves this machine. Replacing it is
    what "sign every screen out" does: a pass signed with the old key stops adding up."""
    if not fresh:
        try:
            got = SCREEN_SECRET_FILE.read_bytes().strip()
            if len(got) >= 32:
                return got
        except OSError:
            pass
    got = secrets.token_hex(32).encode()
    try:
        SCREEN_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCREEN_SECRET_FILE.write_bytes(got + b"\n")
        os.chmod(SCREEN_SECRET_FILE, 0o600)
    except OSError as e:
        log(f"The screen secret was not saved: {e}")
    return got


def issue_pass():
    """A pass is its own end date and a signature over it, and nothing else: no name, no
    address, nothing that says who is holding it."""
    ends = int(time.time()) + COOKIE_DAYS * 86400
    sig = hmac.new(screen_secret(), str(ends).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ends}.{sig}"


def pass_ok(token):
    parts = str(token).split(".")
    if len(parts) != 2 or not parts[0].isdigit():
        return False
    if int(parts[0]) < time.time():
        return False
    want = hmac.new(screen_secret(), parts[0].encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(parts[1], want)


def admin_ok(args):
    """A guarded command carries "secret:<value>" as its first word. With no secret set in
    the settings file nothing can prove itself, and the guarded commands stay shut."""
    if not MQTT_ADMIN_SECRET:
        return False
    first = str(args).strip().split(" ", 1)[0]
    if not first.startswith("secret:"):
        return False
    return hmac.compare_digest(first[7:], MQTT_ADMIN_SECRET)


def strip_admin(args):
    rest = str(args).strip().split(" ", 1)
    return rest[1].strip() if len(rest) > 1 else ""


def cookie_value(header, name):
    for part in str(header).split(";"):
        key, _, val = part.strip().partition("=")
        if key == name:
            return val
    return ""


def keyboard_busy():
    """Why the screen may not sound the keyboard now, or None."""
    if FLAG_GAME.exists():
        return "A lesson is running"
    if FLAG_ALARM.exists():
        return "The alarm is playing"
    if FLAG_SONG.exists():
        return "A song is playing"
    return None


def screen_sound():
    """Marks that the screen is sounding notes, so the bridge does not take them as played."""
    try:
        FLAG_SCREEN.touch()
    except OSError:
        pass


def live_press(note):
    """A key pressed on the keyboard, reported by the bridge on piano/notes."""
    global _live_id
    with UI_LOCK:
        _live_id += 1
        UI["live"] = (UI["live"] + [[_live_id, note]])[-LIVE_KEEP:]


class LiveOut:
    """Keys pressed on the screen, played on the keyboard. Only while no lesson runs.

    The output is opened when a key goes down and closed a few seconds after the last one
    comes up, so it is never held when a lesson starts and needs the same device. A key whose
    release never arrives (a closed tab, a lost touch) is released by the clock.
    """
    VELOCITY = 90
    HOLD_MAX = 6            # seconds before a key with no release is released anyway
    CLOSE_AFTER = 3         # seconds of quiet before the output is closed

    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()
        self.fd = None
        self.device = None
        self.held = {}      # note -> when it went down
        self.last = 0.0
        self.pedal = False  # the screen's sustain pedal
        threading.Thread(target=self._clock, daemon=True).start()

    def _open(self):
        if self.fd is not None or self.device:
            return
        self.device = self.engine.amidi_device()
        self.fd = self.engine.open_raw(self.device) if self.device else None
        if self.pedal and self.device:               # a pedal held on the screen, on a fresh device
            self._write(bytes([0xB0, 64, 127]), retry=False)

    def _drop(self):
        """Forgets the device without writing to it: after an unplug, the old one is gone."""
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd, self.device = None, None

    def _write(self, data, retry=True):
        if self.fd is not None:
            try:
                os.write(self.fd, data)
                return
            except BlockingIOError:
                return          # a busy or stuck link drops the note instead of waiting
            except OSError:
                # the device behind the open file is gone: the keyboard was unplugged and came
                # back under a new device number. Found again, and the note is sent once more.
                self._drop()
                if retry:
                    self._open()
                    self._write(data, retry=False)
                return
        elif self.device:
            try:
                subprocess.run(["amidi", "-p", self.device, "-S", data.hex(" ")],
                               capture_output=True, timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass

    def key(self, note, down, velocity=None):
        with self.lock:
            self._open()
            if down:
                screen_sound()
                self.held[note] = time.time()
                self._write(bytes([0x90, note, velocity or self.VELOCITY]))
            elif self.held.pop(note, None) is not None:
                self._write(bytes([0x80, note, 0]))
            self.last = time.time()

    def raw(self, data):
        with self.lock:
            self._open()
            if (data[0] & 0xF0) == 0x90 and len(data) > 2 and data[2]:
                screen_sound()
            self._write(bytes(data))
            self.last = time.time()

    def set_pedal(self, down):
        with self.lock:
            self._open()
            self.pedal = bool(down)
            self._write(bytes([0xB0, 64, 127 if down else 0]))
            self.last = time.time()

    def close(self):
        """Releases every key and the device. Called before a lesson opens its ports."""
        with self.lock:
            for note in list(self.held):
                self._write(bytes([0x80, note, 0]))
            self.held.clear()
            if self.pedal:
                self._write(bytes([0xB0, 64, 0]))
                self.pedal = False
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
            self.fd, self.device = None, None

    def _clock(self):
        while True:
            time.sleep(0.5)
            now = time.time()
            with self.lock:
                for note, since in list(self.held.items()):
                    if now - since > self.HOLD_MAX:
                        self.held.pop(note)
                        self._write(bytes([0x80, note, 0]))
                idle = not self.held and not self.pedal and now - self.last > self.CLOSE_AFTER
            if idle and (self.fd is not None or self.device):
                self.close()


LIVE_OUT = None             # set when the engine starts
VOLUME_FILE = DATA_DIR / "volume"


def master_volume(percent):
    """The keyboard's master volume, 0-100, by the universal Master Volume SysEx message.

    It sits above the volume of every channel, so a song or the band can still set their own
    balance and this stays the one knob for the whole instrument.
    """
    v = max(0, min(16383, round(percent * 16383 / 100)))
    return bytes([0xF0, 0x7F, 0x7F, 0x04, 0x01, v & 0x7F, v >> 7, 0xF7])


def load_volume():
    try:
        return max(0, min(100, int(VOLUME_FILE.read_text().strip())))
    except (OSError, ValueError):
        return None


class Replayer:
    """Plays a recorded lesson back on the keyboard, while the screen shows it."""

    def __init__(self):
        self.stop_flag = threading.Event()
        self.thread = None

    LESSON_CH, LESSON_VOICE = 1, 4       # the lesson's notes: channel 2, electric piano

    def start(self, rid, at=0.0):
        """Plays from `at` seconds into the recording, like dragging a video's slider."""
        self.stop()
        if not re.fullmatch(r"[0-9a-f-]{1,40}", str(rid)):
            return False               # a name, not a path: deleting checks this, playing did not
        try:
            path = (rec_dir() / f"{rid}.json").resolve()
            if path.parent != rec_dir().resolve():
                return False
            data = json.loads(path.read_text())
            events = data["events"]
        except (OSError, ValueError, KeyError):
            return False
        self.stop_flag.clear()
        at = max(0.0, float(at))
        started = time.time() - at
        ui(replay={"id": rid, "started": started, "at": at})
        self.thread = threading.Thread(target=self._run, args=(events, started, at), daemon=True)
        self.thread.start()
        return True

    def _run(self, events, t0, at):
        # the lesson's demo notes get their own voice, so you hear who is who
        LIVE_OUT.raw([0xC0 | self.LESSON_CH, self.LESSON_VOICE])
        pedal = next((e[2] for e in reversed(events) if e[1] == "p" and e[0] <= at), 0)
        LIVE_OUT.raw([0xB0, 64, pedal])
        for e in events:
            if e[0] < at or e[1] not in ("k", "d", "p"):
                continue
            wait = t0 + e[0] - time.time()
            if wait > 0 and self.stop_flag.wait(wait):
                break
            if keyboard_busy():                      # a lesson, the alarm or a song took over
                break
            if e[1] == "p":
                LIVE_OUT.raw([0xB0, 64, e[2]])
            elif e[1] == "d":
                LIVE_OUT.raw([(0x90 if e[3] else 0x80) | self.LESSON_CH, e[2], e[3]])
            else:
                LIVE_OUT.key(e[2], e[3] > 0, e[3] or None)
        LIVE_OUT.raw([0xB0, 64, 0])
        LIVE_OUT.raw([0xB0 | self.LESSON_CH, 123, 0])      # all notes off on the lesson's channel
        LIVE_OUT.close()
        if not self.stop_flag.is_set():
            ui(replay=None)

    def stop(self):
        self.stop_flag.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.thread = None


REPLAY = Replayer()
FAILS = {}                  # address -> times of wrong screen codes
FOUND = {}                  # search id -> [(name, url, credit)], one per screen
FOUND_LOCK = threading.Lock()   # two screens searching at once kept overwriting each other
UPLOAD_MAX = 4 * 1024 * 1024


def song_check(path):
    """What the screen says about a new song file: its name, notes and parts, or why it is no use."""
    _SONG_CACHE.clear()
    try:
        song, notes, rhythm, *_ = load_song(path.stem)
    except Exception as e:
        return {"name": path.stem, "ok": False, "why": tr("The file could not be read: {e}", e=e)}
    if not notes:
        return {"name": path.stem, "ok": False, "why": tr("No melody was found in it")}
    return {"name": path.stem, "ok": True, "notes": len(notes), "parts": len(phrases(notes, rhythm))}


def save_upload(filename, blob):
    """A MIDI file sent from the screen, saved in the songs folder under a safe name."""
    if not blob.startswith(b"MThd"):
        raise ValueError(tr("This is not a MIDI file"))
    stem = re.sub(r"\.(mid|midi|kar)$", "", Path(filename).name, flags=re.I)
    safe = re.sub(r"[^\w \-().\u0590-\u05ff]", "_", stem).strip(" ._") or "uploaded"
    path = MIDI_DIR / f"{safe}.mid"
    n = 2
    while path.exists():                       # never overwrite a song that is already there
        path = MIDI_DIR / f"{safe} ({n}).mid"
        n += 1
    path.write_bytes(blob)
    return path
ENGINE = None               # the engine, for the screen's actions
_SONG_CACHE = {}


def has_band(path):
    """Does the file have other instruments besides the melody? Only then is there a band.

    A shortcut, or a file with the melody alone, has one track: the full run is then the
    player alone, and nothing on the screen should promise a band.
    """
    if not path:
        return False
    try:
        mid = mido.MidiFile(str(path))
        if len(mid.tracks) < 2:
            return False
        skip = melody_track(mid)
        return any(msg.type == "note_on" and msg.velocity
                   for i, tr in enumerate(mid.tracks) if i != skip for msg in tr)
    except Exception:
        return False


def song_list():
    """Every song, with how far the lessons got: for the song cards on the screen."""
    progress = load_progress()
    settings, now = load_settings(), time.time()
    # Only real songs (MIDI files) belong on the screen. A shortcut is a trigger, not a song,
    # so it never appears here even when it has no matching file.
    names, taken = [], set()
    for f in sorted(MIDI_DIR.glob("*.mid"), key=lambda f: f.stem.lower()):
        if _song_norm(f.stem) not in taken:
            names.append(f.stem)
            taken.add(_song_norm(f.stem))
    out = []
    for name in names:
        f = find_midi(name)
        # "System" is a shipped classical file; any other file in the songs folder is user-uploaded.
        is_system = f is not None and f.stem in SYSTEM_SONGS
        key = (name, f.stat().st_mtime if f else 0)
        if key not in _SONG_CACHE:
            try:
                song, notes, rhythm, *_ = load_song(name)
                _SONG_CACHE[key] = (len(phrases(notes, rhythm)) if notes else 0, has_band(f))
            except Exception:
                _SONG_CACHE[key] = (0, False)
        total, band = _SONG_CACHE[key]
        rec = dict(progress.get(name)) if isinstance(progress.get(name), dict) else {}
        plan = song_plan(rec, total, settings, now) if total else None
        out.append({"name": name, "total": total, "learned": len(plan["known"]) if plan else 0,
                    "system": is_system,
                    "last": rec.get("last"), "score": rec.get("score"), "band": band,
                    "due": len(plan["due"]) if plan else 0, "new_next": len(plan["new"]) if plan else 0,
                    "states": plan["states"] if plan else {"settling": 0, "steady": 0},
                    "credit": song_credit(f)})
    return out


REC_UI = ("mode", "title", "seq", "midi", "show", "fing", "expect", "demo", "msg", "pos", "total")


def ui(**kw):
    with UI_LOCK:
        UI.update(kw)
        snap = {k: UI[k] for k in REC_UI} if RECORDER is not None else None
    if snap is not None and RECORDER is not None:
        RECORDER.screen(snap)


# =========================
# Recording and history
# =========================
RECORDER = None             # the recorder of the lesson running now


class Recorder:
    """Records a whole lesson: every key the player presses, every demo note, every screen.

    It listens on its own input port, next to the one the lesson reads from, so each key is
    stamped the moment it arrives and the lesson itself is not touched. Saved as JSON for the
    screen's replay and as a MIDI file (player on track 1, demos on track 2) to download.
    """

    def __init__(self, song):
        self.song = song
        self.rdir = rec_dir()
        self.t0 = time.time()
        self.events = []
        self.lock = threading.Lock()
        self.port = None
        self.last_screen = None

    def start(self):
        # a pedal held since before the lesson sends nothing new: start from the state the bridge knows
        if UI.get("pedal"):
            self.add("p", 127)
        ins = [n for n in mido.get_input_names() if PIANO_NAME in n]
        if not ins:
            return
        try:
            self.port = mido.open_input(ins[0], callback=self.key)
        except Exception as e:                      # no recording is better than no lesson
            log(f"Recording did not start: {e}")

    def add(self, *event):
        with self.lock:
            self.events.append([round(time.time() - self.t0, 3), *event])

    def key(self, msg):
        if msg.type == "note_on":
            self.add("k", msg.note, msg.velocity)
        elif msg.type == "note_off":
            self.add("k", msg.note, 0)
        elif msg.type == "control_change" and msg.control == 64:
            self.add("p", msg.value)          # the sustain pedal

    def screen(self, snap):
        if snap != self.last_screen:
            self.last_screen = snap
            self.add("u", snap)

    def stop(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
            self.port = None

    def save(self):
        """Writes the recording; returns its id, or None when nothing was played."""
        with self.lock:
            events = list(self.events)
        if not any(e[1] == "k" for e in events):
            return None
        # The date and time made two recordings a second apart collide, and made every
        # recording's name guessable from the hour a lesson was played.
        rid = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.t0)) + "-" + secrets.token_hex(4)
        rdir = self.rdir               # the profile the lesson was for, even if it changes later
        try:
            rdir.mkdir(parents=True, exist_ok=True)
            data = {"id": rid, "song": self.song, "start": int(self.t0),
                    "length": events[-1][0] if events else 0, "events": events}
            (rdir / f"{rid}.json").write_text(json.dumps(data, ensure_ascii=False))
            mid_path = rdir / f"{rid}.mid"
            try:
                self.to_midi(events).save(str(mid_path))
            except Exception:
                mid_path.unlink(missing_ok=True)     # never leave a half-written file that no player opens
                raise
            for old in sorted(rdir.glob("*.json"))[:-REC_KEEP]:
                old.unlink(missing_ok=True)
                old.with_suffix(".mid").unlink(missing_ok=True)
        except Exception as e:        # a recording that cannot be written never ends the lesson with an error
            log(f"The recording was not saved: {e}")
            (rdir / f"{rid}.json").unlink(missing_ok=True)
            return None
        return rid

    @staticmethod
    def to_midi(events):
        """Two tracks that never mix: "You" on channel 1 with a grand piano and your pedal,
        "The lesson" on channel 2 with an electric piano, so the two sound and look different in
        any MIDI player. Markers say who plays when: "The lesson plays", "Your turn"."""
        tpb, tempo = 480, 500000                   # 120 bpm: one second is 960 ticks
        tick = lambda sec: int(sec * tpb * 1_000_000 / tempo)
        mid = mido.MidiFile(ticks_per_beat=tpb)
        you, lesson = mido.MidiTrack(), mido.MidiTrack()
        you.append(mido.MetaMessage("track_name", name="You", time=0))
        you.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
        you.append(mido.Message("program_change", channel=0, program=0, time=0))       # grand piano
        lesson.append(mido.MetaMessage("track_name", name="The lesson (demo)", time=0))
        lesson.append(mido.Message("program_change", channel=1, program=4, time=0))    # electric piano
        last = {"you": 0, "lesson": 0}
        who = None

        def put(track, key, sec, msg):
            t = tick(sec)
            msg.time = max(0, t - last[key])
            last[key] = t
            track.append(msg)

        for e in events:
            sec, kind = e[0], e[1]
            if kind == "k":
                put(you, "you", sec, mido.Message("note_on" if e[3] else "note_off", channel=0, note=e[2], velocity=e[3]))
            elif kind == "p":
                put(you, "you", sec, mido.Message("control_change", channel=0, control=64, value=e[2]))
            elif kind == "d":
                put(lesson, "lesson", sec, mido.Message("note_on" if e[3] else "note_off", channel=1, note=e[2], velocity=e[3]))
            elif kind == "u":
                mode = e[2].get("mode")
                now = "lesson" if mode == "demo" else "you" if mode == "wait" else who
                if now != who and now is not None:
                    who = now
                    # a MIDI text event is Latin-1: a Hebrew title or an emoji made the file unwritable
                    text = "The lesson plays" if now == "lesson" else "Your turn"
                    put(you, "you", sec, mido.MetaMessage("marker", text=text))
        mid.tracks += [you, lesson]
        return mid


def load_history():
    try:
        data = json.loads(history_file().read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add_history(entry):
    data = load_history() + [entry]
    save_history(data)


def save_history(data):
    f = history_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data[-1000:], ensure_ascii=False))
    tmp.replace(f)


def _song_norm(name):
    return str(name or "").lower().replace("_", " ").strip()


def forget_history(song):
    """A song that starts over loses its lessons in the history too, so the progress list does not
    show lessons of a song that is back at the beginning. Recordings are left alone."""
    key = _song_norm(song)
    hist = load_history()
    kept = [h for h in hist if _song_norm(h.get("song")) != key]
    if len(kept) != len(hist):
        save_history(kept)


def existing_songs():
    """Every song still on disk, normalised the way the app matches names (case- and _-insensitive):
    the ones a person added and the shipped classical pieces."""
    try:
        stems = {_song_norm(p.stem) for p in MIDI_DIR.glob("*.mid")}
    except OSError:
        stems = set()
    # shortcuts are playable songs too (they may have no .mid), so their history and recordings
    # are not orphans: keep them, or prune_orphans would wipe a shortcut's rows on every restart
    return stems | {_song_norm(s) for s in SYSTEM_SONGS} | {_song_norm(s) for s in load_shortcuts()}


def prune_orphans(valid=None):
    """Clears ONLY the history rows and recordings a deleted song left behind, so no ghost rows
    remain. Progress is never touched here (a song's progress is removed, scoped, when the song
    itself is deleted). Best-effort, on the active profile. Returns the number of history rows removed."""
    valid = existing_songs() if valid is None else valid
    if not valid:
        return 0                    # an empty or unreadable songs folder: never delete anything
    removed = 0
    try:
        hist = load_history()
        kept = [h for h in hist if _song_norm(h.get("song")) in valid]
        if len(kept) != len(hist):
            removed = len(hist) - len(kept)
            save_history(kept)
    except (OSError, ValueError):
        pass
    try:
        for f in rec_dir().glob("*.json"):
            try:
                song = json.loads(f.read_text()).get("song")
            except (OSError, ValueError):
                continue
            if _song_norm(song) not in valid:
                f.unlink(missing_ok=True)
                f.with_suffix(".mid").unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def stats():
    """Practice numbers for the screen: streak of days, this week, the last score."""
    hist = load_history()
    days = {time.strftime("%Y-%m-%d", time.localtime(h.get("start", 0))) for h in hist}
    streak, day = 0, time.time()
    if time.strftime("%Y-%m-%d", time.localtime(day)) not in days:
        day -= 86400                               # today not played yet: the streak is still alive
    while time.strftime("%Y-%m-%d", time.localtime(day)) in days:
        streak += 1
        day -= 86400
    week = [h for h in hist if h.get("start", 0) > time.time() - 7 * 86400]
    last = next((h for h in reversed(hist) if h.get("score") is not None), None)
    return {"streak": streak,
            "week_minutes": round(sum(h.get("secs", 0) for h in week) / 60),
            "week_pieces": sum(h.get("new", 0) for h in week),
            "week_lessons": len(week),
            "last_score": last.get("score") if last else None,
            "lessons": len(hist)}


def practice_state():
    """Today in numbers, for reminders in Home Assistant: published on piano/practice."""
    today = time.strftime("%Y-%m-%d")
    hist = [h for h in load_history() if time.strftime("%Y-%m-%d", time.localtime(h.get("start", 0))) == today]
    songs = [s for s in song_list() if s["total"]]
    due = sum(s["due"] for s in songs)
    # the song to suggest: the one with most parts due, then the one played last
    pick = max(songs, key=lambda s: (s["due"], s["last"] or 0), default=None)
    return {"lessons_today": len(hist), "minutes_today": round(sum(h.get("secs", 0) for h in hist) / 60),
            "due": due, "streak": stats()["streak"], "song": pick["name"] if pick and (pick["due"] or pick["last"]) else ""}


def recent_lessons(limit=5):
    """The last lessons, newest first, for the progress list on the screen."""
    keep = ("song", "start", "secs", "score", "stopped")
    return [{k: h.get(k) for k in keep} for h in reversed(load_history()[-limit:])]


def lesson_secs():
    """How long a lesson of each song usually takes: the median of its last 5 finished lessons."""
    by_song = {}
    for h in load_history():
        if not h.get("stopped") and h.get("secs"):
            by_song.setdefault(h.get("song"), []).append(h["secs"])
    return {s: sorted(v[-5:])[len(v[-5:]) // 2] for s, v in by_song.items()}


def recordings(limit=40):     # the screen shows these in numbered pages, 8 to a page
    out = []
    for f in sorted(rec_dir().glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text())
            out.append({"id": d["id"], "song": d["song"], "start": d["start"],
                        "length": round(d.get("length", 0))})
        except (OSError, ValueError, KeyError):
            pass
    return out


# Names the screen may be reached by. Anything else in the Host header is refused, which
# stops DNS rebinding: a web page on the internet whose name points at this machine
# could otherwise read and control it from any browser in the home network.
EXTRA_HOSTS = {h.strip().lower() for h in ENV.get("LESSON_HOSTS", "").split(",") if h.strip()}


def host_allowed(host):
    import ipaddress, socket
    name = host.strip().lower()
    if name.startswith("["):                      # [ipv6]:port
        name = name[1:].split("]")[0]
    elif name.count(":") == 1:
        name = name.split(":")[0]
    try:
        ipaddress.ip_address(name)
        return True                               # an address typed by hand
    except ValueError:
        pass
    me = socket.gethostname().lower()
    return name in {"localhost", me, me + ".local"} | EXTRA_HOSTS


def shown_version(v=None):
    """The version as the footer shows it: 1.0 and not 1.0.0, a trailing zero patch dropped."""
    parts = str(APP_VERSION if v is None else v).split(".")
    return ".".join(parts[:2]) if len(parts) == 3 and parts[2] == "0" else ".".join(parts)


def install_id():
    """Changes with every fresh install (the data folder is new), so a browser that remembers the
    language or look of an earlier install on this address does not carry it over."""
    f = DATA_DIR / "install_id"
    try:
        return f.read_text().strip()
    except OSError:
        pass
    # an install that already holds lessons is an update, not a new install: its browsers keep their settings
    if (DATA_DIR / "lessons.json").exists() or (DATA_DIR / "history.json").exists():
        new = "legacy"
    else:
        new = secrets.token_hex(6)
    try:
        f.write_text(new)
    except OSError:
        return "unsaved"                # the same every time, so a read-only folder does not clear the browser on each visit
    return new


def render_page():
    """The lesson screen with everything the server fills in. The version goes into the page itself,
    so the footer is right on the first visit, before the code is entered and before any request."""
    return (PAGE.replace("/*FIRST*/36", str(FIRST_WHITE))
                .replace("/*LAST*/96", str(HIGH))
                .replace("__CODE_PATH__", str(SCREEN_CODE_FILE))
                .replace("__OVERRIDES__", json.dumps(load_overrides(), ensure_ascii=False).replace("</", "<\\/"))
                .replace("__EDITMODE__", "true" if EDIT_FLAG.exists() else "false")
                .replace("__VERSION__", shown_version())
                .replace("__INSTALL__", install_id())
                .replace("__REPO__", APP_REPO or "vestacorelabs/armonico"))


class UIHandler(BaseHTTPRequestHandler):
    timeout = 20            # a client that stops sending mid-request cannot hold a thread forever

    def refused(self):
        """Wrong Host, or a POST sent by a page from another site (cross-site request forgery)."""
        if not host_allowed(self.headers.get("Host", "")):
            self.answer(403)
            return True
        return False

    def cross_site(self):
        from urllib.parse import urlparse
        origin = self.headers.get("Origin")
        if not origin:
            return False                          # not a browser: curl, Home Assistant, scripts
        return urlparse(origin).netloc.lower() != self.headers.get("Host", "").lower()

    # Everything a screen reads or changes sits behind the same door. The page itself, the
    # fonts and the recorded piano stay open, so a screen that was never let in still draws
    # enough to ask for the code.
    OPEN_GETS = ("/sounds/", "/fonts/")

    def open_get(self, path):
        return path == "/" or path.startswith(self.OPEN_GETS)

    def slow_down(self):
        """Five wrong codes from one address in a minute, then that address waits."""
        ip, now = self.client_address[0], time.time()
        fails = [t for t in FAILS.get(ip, []) if now - t < 60]
        FAILS[ip] = fails
        return len(fails) >= 5

    def wrong_code(self):
        ip, now = self.client_address[0], time.time()
        FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 60] + [now]

    def authed(self):
        """A pass in the cookie, or the code in a header for a script that has it."""
        if not code_on():
            return True
        token = cookie_value(self.headers.get("Cookie", ""), COOKIE_NAME)
        if token and pass_ok(token):
            self.renew = True                     # every visit pushes the end date out again
            return True
        got = self.headers.get("X-Screen-Code", "")
        if got and not self.slow_down() and hmac.compare_digest(got, screen_code()):
            return True
        if got:
            self.wrong_code()
        return False

    def pass_cookie(self):
        token = issue_pass()
        return (f"{COOKIE_NAME}={token}; Max-Age={COOKIE_DAYS * 86400}; Path=/; "
                "HttpOnly; SameSite=Strict")

    def do_GET(self):
        path = self.path.split("?")[0]
        if self.refused():
            return
        if not self.open_get(path) and not self.authed():
            return self.answer(401)
        if path.startswith("/state"):
            # the page asks every 100 ms and says when it is in view; the bridge reads the flag's
            # age, so it is touched at most every 2 seconds and goes stale soon after the page does
            if "seen" in self.path and time.time() - _page_seen[0] > 2:
                _page_seen[0] = time.time()
                try:
                    FLAG_PAGE.touch()
                except OSError:
                    pass
            with UI_LOCK:
                body = json.dumps(UI).encode()
            ctype = "application/json"
        elif path == "/api/overview":
            body = json.dumps({"songs": song_list(), "stats": stats(), "version": APP_VERSION,
                               "recordings": recordings(), "settings": load_settings(), "pace_speed": PACE_SPEED,
                               "history": recent_lessons(), "lesson_secs": lesson_secs(),
                               "profiles": profiles_public()}).encode()
            ctype = "application/json"
        elif re.fullmatch(r"/sounds/piano/(A0|C8|(C|Ds|Fs|A)[1-7])\.mp3", path):
            body, ctype = (SOUND_DIR / path.rsplit("/", 1)[1]).read_bytes(), "audio/mpeg"
            self.extra = {"Cache-Control": "max-age=2592000"}      # the recordings never change
        elif re.fullmatch(r"/fonts/heebo-(hebrew|latin|latin-ext)\.woff2", path):
            body, ctype = (FONT_DIR / path.rsplit("/", 1)[1]).read_bytes(), "font/woff2"
            self.extra = {"Cache-Control": "max-age=604800"}
        elif re.fullmatch(r"/media/[A-Za-z0-9._-]+", path):
            f = MEDIA_DIR / path.rsplit("/", 1)[1]
            if not f.exists():
                return self.answer(404)
            body, ctype = f.read_bytes(), MEDIA_TYPES.get(f.suffix.lower(), "application/octet-stream")
            self.extra = {"Cache-Control": "max-age=604800"}      # named by content hash, so it never changes
        elif path == "/api/code":
            body = json.dumps({"on": code_on(),
                               "code": screen_code() if "show=1" in self.path else None}).encode()
            ctype = "application/json"
        elif path == "/api/update":
            body, ctype = json.dumps(update_info("force" in self.path)).encode(), "application/json"
        elif path == "/api/backup":
            body, ctype = backup_zip(), "application/zip"
            self.extra = {"Content-Disposition": attachment(backup_name())}
        elif path == "/api/search":
            from urllib.parse import parse_qs, urlparse
            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].strip()[:80]
            if not q:
                return self.answer(400)
            try:
                hits, missing, failed = search_midi(q)
            except Exception as e:
                log(f"Search failed: {e!r}")
                body = json.dumps({"error": tr("No archive answered")}).encode()
            else:
                token = secrets.token_hex(8)
                with FOUND_LOCK:
                    FOUND[token] = hits
                    for old in list(FOUND)[:-8]:      # the last few searches, not every search ever
                        FOUND.pop(old, None)
                body = json.dumps({"search": token,
                                   "results": [{"id": i, "name": n, "source": c["source"], "license": c["license"]}
                                               for i, (n, _, c) in enumerate(hits)],
                                   "missing": missing, "failed": failed}).encode()
            ctype = "application/json"
        elif path == "/api/status-login":
            # the broker address and login for the status screen, handed only to a screen that is signed in
            # (or to anyone, when the code is off), so the address stays /status with nothing in it
            body = json.dumps({"user": MQTT_USER or "piano", "pass": MQTT_PASS, "host": MQTT_HOST}).encode()
            ctype = "application/json"
            self.extra = {"Cache-Control": "no-store"}
        elif path == "/status":
            # the status screen speaks MQTT over websockets from the browser, so only this page
            # may connect out, and its fonts are inlined as data
            try:
                body = STATUS_PAGE.read_bytes()
            except OSError:
                return self.answer(404)
            ctype = "text/html; charset=utf-8"
            self.csp = ("frame-ancestors 'none'; default-src 'self'; img-src 'self' data:; "
                        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                        "font-src 'self' data:; connect-src 'self' ws: wss:; object-src 'none'; "
                        "base-uri 'none'; form-action 'none'")
        elif re.fullmatch(r"/api/recording/[0-9a-f-]{1,40}", path):
            f = rec_dir() / (path.rsplit("/", 1)[1] + ".json")
            if not f.exists():
                return self.answer(404)
            body, ctype = f.read_bytes(), "application/json"
        elif re.fullmatch(r"/recordings/[0-9a-f-]{1,40}\.mid", path):
            f = rec_dir() / path.rsplit("/", 1)[1]
            if not f.exists() or f.stat().st_size < 40:
                # a recording made before the fix may hold only the 14 byte header: rebuilt from its events
                try:
                    events = json.loads(f.with_suffix(".json").read_text())["events"]
                    Recorder.to_midi(events).save(str(f))
                except Exception:
                    return self.answer(404)
            body, ctype = f.read_bytes(), "audio/midi"
            try:
                song = json.loads(f.with_suffix(".json").read_text()).get("song", "lesson")
            except (OSError, ValueError):
                song = "lesson"
            song = re.sub(r'[^\w \-().]', "_", str(song))[:80] or "lesson"   # nothing that can break the header
            self.extra = {"Content-Disposition": attachment(f"{song}-{f.stem}.mid")}
        else:
            body = render_page().encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in getattr(self, "extra", {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in getattr(self, "extra", {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")                 # no framing by another page
        self.send_header("Content-Security-Policy", getattr(self, "csp", None) or
                         "frame-ancestors 'none'; default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=(), interest-cohort=()")
        self.renew_header()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """The screen's actions. Keys and replays are refused while a lesson runs.

        /key {"note": 60, "down": true}   a key on the screen, played on the keyboard
        /lesson {"song": "ode_to_joy"}    starts a lesson, as /lesson does in Telegram
        /lessonstop                       stops the lesson and saves it
        /replay {"id": "..."}             plays a recorded lesson back; /replay/stop ends it
        /summary/close                    the end-of-lesson card was closed
        """
        path = self.path.split("?")[0]
        if self.refused():
            return
        if self.cross_site():
            return self.answer(403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.answer(400)
        if length < 0:
            return self.answer(400)                # a negative length would read until the client stops
        if path in ("/songs/upload", "/media-upload", "/backup/inspect"):
            if length > (RESTORE_MAX if path == "/backup/inspect" else UPLOAD_MAX):
                return self.answer(413)
            raw = self.rfile.read(length)
            data = {}
        else:
            # edited wording can be a full block of HTML, past the tiny body every other action sends
            cap = 420000 if path == "/text-edit" else 500
            if length > cap:
                return self.answer(413)
            try:
                data = json.loads(self.rfile.read(min(length, cap)) or b"{}")
            except ValueError:
                return self.answer(400)
        if path == "/summary/close":               # closing a card needs no code
            ui(summary=None)
            return self.answer(204)
        if path == "/code":
            # The one door. A right code is answered with a pass the screen keeps, so the
            # code is typed once on a device and not again. Wrong guesses are slowed down:
            # after 5 in a minute that address waits a minute.
            if not code_on():
                return self.json({"ok": True})
            if self.slow_down():
                return self.answer(429)
            got = str(data.get("code", "")) or self.headers.get("X-Screen-Code", "")
            if not hmac.compare_digest(got, screen_code()):
                self.wrong_code()
                return self.answer(401)
            FAILS.pop(self.client_address[0], None)
            self.grant = True
            return self.json({"ok": True})
        if not self.authed():
            return self.answer(401)
        # The code and its switches are answered before this: a screen must be able to let
        # itself in while the keyboard is still starting, or after the engine restarted.
        if path.startswith("/code/"):
            pass
        elif LIVE_OUT is None or ENGINE is None:
            return self.answer(503)
        busy = FLAG_GAME.exists()
        if path == "/code/off":
            set_code_on(False)
            return self.json({"ok": True, "on": False})
        elif path == "/code/on":
            set_code_on(True)
            self.grant = True                      # the screen that turned it on stays in
            return self.json({"ok": True, "on": True, "code": screen_code()})
        elif path == "/code/new":
            code = screen_code(fresh=True)
            return self.json({"ok": True, "code": code})
        elif path == "/code/signout":
            # every pass ever handed out is signed with the old key, so all of them end here
            screen_secret(fresh=True)
            self.grant = True                      # except this one, which is signed again now
            return self.json({"ok": True})
        elif path == "/backup/inspect":
            try:
                return self.json(restore_inspect(raw))
            except (ValueError, OSError) as e:
                return self.json({"error": tr(str(e))}, 400)
        elif path == "/backup/restore":
            if busy or REPLAY.thread is not None and REPLAY.thread.is_alive():
                return self.answer(409)
            ip, now = self.client_address[0], time.time()
            fails = [t for t in PIN_FAILS.get(ip, []) if now - t < 60]
            if len(fails) >= 5:
                return self.answer(429)
            source = str(data.get("source", ""))
            try:
                with PROFILE_LOCK:
                    prof = load_profiles()
                    target = next((p for p in prof["list"] if p["id"] == source), None)
                    # a locked profile needs its PIN to be written over, like switching to it does
                    if target is not None and prof["active"] != source and not pin_ok(target, data.get("pin")):
                        PIN_FAILS[ip] = fails + [now]
                        return self.json({"error": "Wrong PIN", "why": "wrong_pin"}, 403)
                    PIN_FAILS.pop(ip, None)
                    wrote = restore_into(source, bool(data.get("songs", True)))
            except (ValueError, OSError, KeyError) as e:
                return self.json({"error": tr(str(e))}, 400)
            try:
                ENGINE.publish_profile()
                ENGINE.publish_practice()
            except Exception:
                pass
            return self.json({"ok": True, **wrote, "profiles": profiles_public()})
        elif path == "/key":
            try:
                note, down = int(data["note"]), bool(data["down"])
            except (ValueError, KeyError, TypeError):
                return self.answer(400)
            if not FIRST_WHITE <= note <= HIGH:
                return self.answer(400)
            if down and (keyboard_busy() or REPLAY.thread is not None and REPLAY.thread.is_alive()):
                return self.answer(409)
            LIVE_OUT.key(note, down)
        elif path == "/pedal":
            if data.get("down") and keyboard_busy():
                return self.answer(409)
            LIVE_OUT.set_pedal(bool(data.get("down")))
        elif path == "/volume":
            try:
                vol = max(0, min(100, int(data["value"])))
            except (ValueError, KeyError, TypeError):
                return self.answer(400)
            msg = master_volume(vol)
            if busy and (ENGINE.raw is not None or ENGINE.amidi):
                ENGINE.send_raw(msg)               # the lesson holds the output: send through it
            else:
                LIVE_OUT.raw(msg)
            try:
                VOLUME_FILE.write_text(f"{vol}\n")
            except OSError:
                pass
            ui(volume=vol)
        elif path == "/lesson":
            song = str(data.get("song", "")).strip()
            if not song or busy:
                return self.answer(409 if busy else 400)
            REPLAY.stop()
            ENGINE.cmds.put(("lesson", "", song + (" reset" if data.get("reset") else "")))
        elif path == "/progress/reset":
            # the song starts over: its parts, schedule, scores and lessons in the history go; recordings stay
            song = str(data.get("song", "")).strip()
            if busy:
                return self.answer(409)
            progress = load_progress()
            if song not in progress:
                return self.answer(404)
            del progress[song]
            try:
                save_progress(progress)
                forget_history(song)
            except OSError:
                return self.answer(500)
        elif path == "/lessonstop":
            ENGINE.stop_event.set()
        elif path == "/replay":
            try:
                at = float(data.get("at", 0))
            except (TypeError, ValueError):
                at = 0.0
            if keyboard_busy():
                return self.answer(409)
            if not REPLAY.start(str(data.get("id", "")), at):
                return self.answer(404)
        elif path == "/replay/stop":
            REPLAY.stop()
            ui(replay=None)
        elif path == "/settings":
            cur = load_settings()
            if data.get("pace") in PACES:
                cur["pace"] = data["pace"]
                if "speed" not in data:        # a new pace also moves the fixed speed to its own start
                    cur["speed"] = PACE_SPEED[data["pace"]]
            if isinstance(data.get("auto_tempo"), bool):
                cur["auto_tempo"] = data["auto_tempo"]
            if isinstance(data.get("speed"), (int, float)):
                cur["speed"] = max(SPEED_MIN, min(SPEED_MAX, int(data["speed"])))
            if data.get("lang") in LANGS:
                cur["lang"] = data["lang"]
            if data.get("labels") in LABELS:
                cur["labels"] = data["labels"]
            if data.get("colors") in COLORS:
                cur["colors"] = data["colors"]
            for k, allowed in LOOKS.items():
                if data.get(k) in allowed:
                    cur[k] = data[k]
            if isinstance(data.get("scale"), (int, float)) and not isinstance(data.get("scale"), bool) and 0.6 <= data["scale"] <= 2:
                cur["scale"] = round(float(data["scale"]), 1)
            try:
                save_settings(cur)
            except OSError:
                return self.answer(500)
            if ENGINE is not None and "lang" in data:
                ENGINE.pub("piano/lang", cur["lang"], retain=True)   # Home Assistant answers in it too
        elif path.startswith("/profiles/"):
            return self.profiles_action(path, data)
        elif path == "/recordings/delete":
            rid = str(data.get("id", ""))
            if not re.fullmatch(r"[0-9a-f-]{1,40}", rid):
                return self.answer(400)
            if REPLAY.thread is not None and REPLAY.thread.is_alive():
                REPLAY.stop()
            for ext in (".json", ".mid"):
                (rec_dir() / f"{rid}{ext}").unlink(missing_ok=True)
        elif path == "/songs/upload":
            from urllib.parse import unquote
            try:
                saved = save_upload(unquote(self.headers.get("X-Filename", "song.mid")), raw)
            except ValueError as e:
                return self.json({"ok": False, "why": str(e)}, 400)
            except OSError as e:
                return self.json({"ok": False, "why": tr("The file was not saved: {e}", e=e)}, 500)
            return self.json(song_check(saved))
        elif path == "/media-upload":
            from urllib.parse import unquote
            try:
                url = save_media(unquote(self.headers.get("X-Filename", "image")), raw)
            except ValueError as e:
                return self.json({"ok": False, "why": str(e)}, 400)
            except OSError as e:
                return self.json({"ok": False, "why": tr("The file was not saved: {e}", e=e)}, 500)
            return self.json({"ok": True, "url": url})
        elif path == "/update":
            info = update_info()
            if busy:
                return self.answer(409)            # not in the middle of a lesson
            if not info["available"]:
                return self.json({"ok": False, "why": tr(info["error"] or "This is already the newest version")}, 409)
            UPDATE_REQUEST.write_text(info["latest"] + "\n")
            return self.json({"ok": True, "version": info["latest"]}, 202)
        elif path == "/songs/fetch":
            try:
                with FOUND_LOCK:
                    hits = FOUND[str(data.get("search", ""))]
                name, url, credit = hits[int(data.get("id", -1))]
            except (KeyError, ValueError, IndexError, TypeError):
                return self.answer(404)
            if not url.startswith(DOWNLOAD_PREFIXES):
                return self.answer(400)          # only the archives that are searched
            try:
                saved = download_midi(url, name, credit)
            except Exception as e:
                return self.json({"ok": False, "why": tr("The download failed: {e}", e=e)}, 502)
            return self.json(song_check(saved))
        elif path == "/text-edit":
            if not EDIT_FLAG.exists():         # the screen is not offering this now
                return self.json({"ok": False, "why": ""}, 403)
            err = save_override(data.get("lang", ""), data.get("key", ""), data.get("value", ""))
            return self.json({"ok": not err, "why": err}, 400 if err else 200)
        elif path == "/songs/delete":
            # only a song a person added: the shipped classical pieces are kept
            name = str(data.get("name", "")).strip()
            if busy:
                return self.answer(409)
            if not name or name in SYSTEM_SONGS:
                return self.answer(400)
            f = find_midi(name)
            if f:
                stem = f.stem
                try:
                    f.unlink(missing_ok=True)
                    f.with_suffix(".credit.json").unlink(missing_ok=True)
                except OSError:
                    return self.answer(500)
                # a song can also carry a shortcut of the same name (shortcut + rhythm): clear it too
                delete_shortcut(stem)
            else:
                # no file: a shortcut kept only in shortcuts.conf (the bridge reloads on change)
                try:
                    if not delete_shortcut(name):
                        return self.answer(404)
                except OSError:
                    return self.answer(500)
                stem = name
            _SONG_CACHE.clear()
            # remove the deleted song's own progress, scoped to it exactly, then clear any
            # ghost history and recordings. Progress of other songs is never touched.
            try:
                progress = load_progress()
                for k in [k for k in progress if _song_norm(k) == _song_norm(stem)]:
                    del progress[k]
                save_progress(progress)
            except (OSError, ValueError):
                pass
            prune_orphans()
        else:
            return self.answer(404)
        self.answer(204)

    def profiles_action(self, path, data):
        """/profiles/switch {id, pin}, /profiles/add {name, pin}, /profiles/edit {name, pin, new_pin},
        /profiles/delete {id, pin}. Nothing changes while a lesson or a replay runs."""
        if FLAG_GAME.exists() or REPLAY.thread is not None and REPLAY.thread.is_alive():
            return self.answer(409)
        ip, now = self.client_address[0], time.time()
        fails = [t for t in PIN_FAILS.get(ip, []) if now - t < 60]
        if len(fails) >= 5:
            return self.answer(429)

        def wrong_pin():
            PIN_FAILS[ip] = fails + [now]
            return self.json({"ok": False, "why": "wrong_pin"}, 403)

        with PROFILE_LOCK:
            prof = load_profiles()
            by_id = {p["id"]: p for p in prof["list"]}
            target = by_id.get(str(data.get("id", "")))
            if path == "/profiles/switch":
                if target is None:
                    return self.answer(404)
                if not pin_ok(target, data.get("pin")):
                    return wrong_pin()
                PIN_FAILS.pop(ip, None)
                switch_profile(prof, target)
            elif path == "/profiles/add":
                import secrets
                name, pin = valid_name(data.get("name")), data.get("pin") or ""
                if name is None or (pin and not valid_pin(pin)):
                    return self.answer(400)
                if len(prof["list"]) >= PROFILES_MAX:
                    return self.json({"ok": False, "why": "full"}, 409)
                if any((p.get("name") or "").lower() == name.lower() for p in prof["list"]):
                    return self.json({"ok": False, "why": "taken"}, 409)
                new = {"id": "p" + secrets.token_hex(4), "name": name, "pin": pin_hash(pin) if pin else None,
                       "created": int(now), "lang": load_settings()["lang"]}
                prof["list"].append(new)
                save_profiles(prof)
                profile_dir(new["id"]).mkdir(parents=True, exist_ok=True)
                cur = load_settings()                  # starts in the piano's language, at the usual pace
                (profile_dir(new["id"]) / "settings.json").write_text(
                    json.dumps(dict(DEFAULT_SETTINGS, lang=cur["lang"], palette="vesta", voice="grand", scale=1.0,
                                    colors="notes", labels="num")))
            elif path == "/profiles/edit":             # only the active profile, with its PIN
                me = by_id[prof["active"]]
                if not pin_ok(me, data.get("pin")):
                    return wrong_pin()
                PIN_FAILS.pop(ip, None)
                if "name" in data:
                    name = valid_name(data.get("name"))
                    if name is None and me["id"] != "main":
                        return self.answer(400)
                    if name and any(p is not me and (p.get("name") or "").lower() == name.lower() for p in prof["list"]):
                        return self.json({"ok": False, "why": "taken"}, 409)
                    me["name"] = name or ""
                if "new_pin" in data:
                    new_pin = data.get("new_pin") or ""
                    if new_pin and not valid_pin(new_pin):
                        return self.answer(400)
                    me["pin"] = pin_hash(new_pin) if new_pin else None
                save_profiles(prof)
            elif path == "/profiles/delete":           # never the main one, never the one at the piano
                if target is None or target["id"] in ("main", prof["active"]):
                    return self.answer(400)
                if not pin_ok(target, data.get("pin")):
                    return wrong_pin()
                PIN_FAILS.pop(ip, None)
                prof["list"] = [p for p in prof["list"] if p is not target]
                save_profiles(prof)
                shutil.rmtree(profile_dir(target["id"]), ignore_errors=True)
                log(f"Profile deleted: {target.get('name')}")
            else:
                return self.answer(404)
        if ENGINE is not None:
            ENGINE.publish_profile()
        return self.json({"ok": True, "profiles": profiles_public()})

    def renew_header(self):
        if getattr(self, "renew", False) or getattr(self, "grant", False):
            self.send_header("Set-Cookie", self.pass_cookie())

    def answer(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.renew_header()
        self.end_headers()

    def json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.renew_header()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def local_ip():
    """The address other machines reach this one on, for the log line."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        sock.close()


def start_ui():
    try:
        srv = ThreadingHTTPServer((UI_BIND, UI_PORT), UIHandler)
    except OSError as e:
        log(f"The lesson screen did not start: {e}")
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"Lesson screen: http://{local_ip()}:{UI_PORT}")
    try:
        gone = prune_orphans()         # clear any rows left by songs deleted before this version
        if gone:
            log(f"Cleared {gone} lesson rows from songs no longer present")
    except Exception as e:
        log(f"Cleanup of old lesson rows skipped: {e}")


def phrases(notes, rhythm):
    """Splits a song where the music itself breathes, not every N notes.

    A rest between notes ends a phrase. Phrases longer than MAX_PHRASE are cut at
    their own longest inner rest, and phrases shorter than MIN_PHRASE join the one
    before them, so a lesson never jumps from 3 notes to 20.
    """
    # a phrase ends where the music rests: either a real gap, or a note held long.
    # both are the same signal to the ear, so they are measured together.
    gaps = [0.0] * len(notes)
    if rhythm:
        for i, r in enumerate(rhythm):
            if r:
                gaps[i] = r[0] + r[1]
    if not any(gaps):                      # no rhythm to go by: even groups of 4
        step = 4
        out = [list(range(i, min(i + step, len(notes)))) for i in range(0, len(notes), step)]
        if len(out) > 1 and len(out[-1]) < MIN_PHRASE:
            tail = out.pop()                 # a short tail joins the piece before it
            out[-1] += tail
        return out

    ordered = sorted(g for g in gaps if g > 0)
    typical = ordered[len(ordered) // 2]
    cut = max(typical * 1.6, 0.12)

    groups, cur = [], []
    for i in range(len(notes)):
        cur.append(i)
        if gaps[i] >= cut and len(cur) >= MIN_PHRASE:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    # anything still too long is cut again at its own biggest inner rest
    out = []
    queue = list(groups)
    while queue:
        g = queue.pop(0)
        if len(g) <= MAX_PHRASE:
            out.append(g)
            continue
        inner = g[MIN_PHRASE - 1:len(g) - MIN_PHRASE]
        split = max(inner, key=lambda i: gaps[i]) if inner else g[len(g) // 2 - 1]
        at = g.index(split) + 1
        queue.insert(0, g[at:])
        out.append(g[:at]) if len(g[:at]) <= MAX_PHRASE else queue.insert(0, g[:at])

    # a stray tail of one or two notes belongs to the phrase before it
    merged = []
    for g in out:
        if merged and len(g) < MIN_PHRASE and len(merged[-1]) + len(g) <= MAX_PHRASE + 2:
            merged[-1] = merged[-1] + g
        else:
            merged.append(g)
    return merged


class Stop(Exception):
    def __init__(self, reason):
        self.reason = reason


# =========================
# Songs and progress
# =========================
def load_shortcuts():
    songs = {}
    if SHORTCUTS_FILE.exists():
        for line in SHORTCUTS_FILE.read_text().splitlines():
            if not line.strip() or line.startswith("#") or "|" not in line:
                continue
            notes, name = line.rsplit("|", 1)
            try:
                songs[name.strip()] = [int(x) for x in notes.split()]
            except ValueError:
                continue
    return songs


def delete_shortcut(name):
    """Removes one shortcut line from shortcuts.conf, matched by name the way the screen shows it.

    Uses the same lock and atomic replace the bridge uses (flock on shortcuts.conf.lock, write a
    .tmp then rename), so the two never race and the bridge always reloads a whole file. Comments,
    blank lines and every other shortcut are kept exactly as they were. Returns True when a line
    was removed.
    """
    if not SHORTCUTS_FILE.exists():
        return False
    want = name.lower().replace("_", " ")
    lock = SHORTCUTS_FILE.parent / (SHORTCUTS_FILE.name + ".lock")   # the same lock the bridge takes
    tmp = SHORTCUTS_FILE.parent / (SHORTCUTS_FILE.name + ".tmp")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        kept, removed = [], False
        for line in SHORTCUTS_FILE.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "|" in line:
                _, ln_name = line.rsplit("|", 1)
                if ln_name.strip().lower().replace("_", " ") == want:
                    removed = True
                    continue
            kept.append(line)
        if removed:
            tmp.write_text("\n".join(kept) + "\n")
            os.replace(tmp, SHORTCUTS_FILE)
    return removed


def find_midi(name):
    want = name.lower().replace("_", " ")
    for f in MIDI_DIR.glob("*.mid"):
        if f.stem.lower().replace("_", " ") == want:
            return f
    return None


def load_song(name):
    """Shortcuts first, since they are exactly what you play. Otherwise a MIDI file.

    Either way the rhythm comes from a MIDI file when one is there, so the demo
    sounds like the song instead of a row of equal beeps. starts is each note's
    second inside that file, which is what lets practice begin in the middle with
    the accompaniment starting from the same place.
    """
    for key, notes in load_shortcuts().items():
        if key.lower().replace("_", " ") == name.lower().replace("_", " "):
            f = find_midi(key) or find_midi(name)
            rhythm, starts, shift = rhythm_and_starts(notes, str(f)) if f else (None, None, 0)
            source = "shortcut + rhythm from the file" if rhythm else "shortcut"
            return key, notes, rhythm, starts, source, shift
    f = find_midi(name)
    if not f:
        return None, None, None, None, None, 0
    events = melody_events(str(f))
    timed = timed_melody(str(f))
    if not timed:
        return None, None, None, None, None, 0
    pitches = [n for n, _, _ in timed]
    shift, _ = best_shift(pitches)
    notes = [p + shift for p in pitches]
    rhythm = [(d, g) for _, d, g in timed]
    starts = [s for s, _, _ in events]
    return f.stem, notes, rhythm, starts, "MIDI file", shift


class _NoRedirect(HTTPRedirectHandler):
    """The address is checked against the archives before it is fetched. Following a
    redirect would fetch a different address than the one that was checked, and the
    archives could then be used to reach anything this machine can reach."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).startswith(DOWNLOAD_PREFIXES + SEARCH_PREFIXES):
            raise ValueError(f"the archive redirected somewhere else: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_NoRedirect)


def _get(url, timeout=None, search=False):
    if not str(url).startswith(DOWNLOAD_PREFIXES + (SEARCH_PREFIXES if search else ())):
        raise ValueError("only the archives that are searched")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with _OPENER.open(req, timeout=timeout or HTTP_TIMEOUT) as r:
        return r.read(4 * 1024 * 1024)


def search_mutopia(query):
    """Mutopia Project: public domain and Creative Commons editions, one .mid per piece.

    Pieces that come only as a zip of several movements are left out.
    """
    import html as htmlmod
    blocks = []
    for n in range(SEARCH_PAGES):
        page = _get(MUTOPIA_SEARCH.format(quote(query)) + (f"&startat={n * 10}" if n else ""), search=True).decode("utf-8", "replace")
        blocks += page.split('class="table-bordered result-table"')[1:]
        if f"startat={(n + 1) * 10}&" not in page:
            break                                 # no next page
    out = []
    for block in blocks:
        cells = [htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", block, flags=re.S)]
        mid = re.search(r'href="(https://www\.mutopiaproject\.org/ftp/[^"]+\.mid)"', block)
        lic = re.search(r'legal\.html#[a-z]+">([^<]+)<', block)
        info = re.search(r'piece-info\.cgi\?id=(\d+)', block)
        if not (mid and cells):
            continue
        title = cells[0]
        composer = re.sub(r"^by\s+", "", cells[1]) if len(cells) > 1 else ""
        out.append((f"{title} - {composer.split(' (')[0]}".strip(" -"), mid.group(1), {
            "source": "Mutopia Project", "license": lic.group(1).strip() if lic else "see source",
            "author": composer,
            "page": f"https://www.mutopiaproject.org/cgibin/piece-info.cgi?id={info.group(1)}" if info else ""}))
    return out


def search_commons(query):
    """Wikimedia Commons: every file there is under a free licence, recorded per file."""
    from urllib.parse import urlencode
    q = urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                   "gsrsearch": f"filemime:audio/midi {query}", "gsrlimit": 50,
                   "prop": "imageinfo", "iiprop": "url|extmetadata"})
    data = json.loads(_get(f"{COMMONS_API}?{q}", search=True).decode("utf-8", "replace"))
    out = []
    for p in (data.get("query", {}).get("pages") or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        # Commons hangs tracking parameters on the file address, so the name of the file
        # is the path and not the whole address: without this every result is thrown away
        url = (info.get("url") or "").split("?")[0]
        if not url.lower().endswith((".mid", ".midi")):
            continue
        val = lambda k: re.sub(r"<[^>]+>", "", (meta.get(k) or {}).get("value", "")).strip()
        name = re.sub(r"^File:|\.midi?$", "", p.get("title", ""), flags=re.I).replace("_", " ")
        out.append((name, url, {"source": "Wikimedia Commons", "license": val("LicenseShortName") or "see source",
                                "author": val("Artist"), "page": info.get("descriptionurl", "")}))
    return out


SEARCHERS = (("Mutopia Project", search_mutopia), ("Wikimedia Commons", search_commons))


def search_midi(query):
    """Searches every archive and returns ([(name, url, credit)], words found nowhere, archives that failed).

    Hits with the most query words in the name come first; a word no hit contains at all is
    reported back, since it is usually a typo. One archive failing still leaves the others.
    """
    words = [w for w in re.split(r"[\s_\-]+", query.lower()) if w]
    hits, failed = [], []
    for label, fn in SEARCHERS:
        try:
            hits += fn(query)
        except Exception as e:
            log(f"search in {label} failed: {e}")
            failed.append(label)
    if failed and len(failed) == len(SEARCHERS):
        raise OSError("no archive answered")

    def score(name):
        low = name.lower()
        tokens = re.split(r"[^\w]+", low)
        total = 0.0
        for w in words:
            if w in low:
                total += 1
            elif difflib.get_close_matches(w, tokens, n=1, cutoff=0.8):
                total += 0.7               # one letter off still counts, a little less
        return total

    seen, unique = set(), []
    for h in sorted(hits, key=lambda h: (-score(h[0]), h[0].lower())):
        if h[1] not in seen:
            seen.add(h[1]); unique.append(h)
    names = " ".join(h[0] for h in unique).lower()
    missing = [w for w in words if w not in names]
    return unique[:SEARCH_RESULTS], missing, failed


def download_midi(url, name, credit=None):
    """Saves one MIDI file next to the others, with its credit beside it, and returns its path.

    The credit (archive, licence, author, page) goes to "<song>.credit.json": Creative Commons
    Attribution licences require the author and source to travel with the file.
    """
    if not url.startswith(DOWNLOAD_PREFIXES):
        raise ValueError("not an archive this program downloads from")
    safe = re.sub(r"[^\w \-().\u0590-\u05ff]", "_", name).strip() or "downloaded"
    if not safe.lower().endswith(".mid"):
        safe += ".mid"
    path = MIDI_DIR / safe
    n = 2
    while path.exists():                           # never overwrite a song that is already there
        path = MIDI_DIR / f"{safe[:-4]} ({n}).mid"
        n += 1
    blob = _get(url)
    if not blob.startswith(b"MThd"):
        raise ValueError("the file is not a MIDI file")
    path.write_bytes(blob)
    if credit:
        path.with_suffix(".credit.json").write_text(json.dumps(dict(credit, file=url), ensure_ascii=False, indent=1))
    return path


def song_credit(path):
    """The credit saved with a downloaded song, or None for the user's own files."""
    try:
        return json.loads(path.with_suffix(".credit.json").read_text())
    except (OSError, ValueError, AttributeError):
        return None


# ---------- settings chosen on the screen ----------
SETTINGS_FILE = DATA_DIR / "settings.json"
PACES = {"relaxed": 1, "steady": 2, "fast": 3}     # new parts in each lesson
PACE_SPEED = {"relaxed": 50, "steady": 60, "fast": 75}   # where the demo starts for each pace, percent
DEFAULT_LANG = ENV.get("UI_LANG", "en") if ENV.get("UI_LANG") in LANGS else "en"   # from the setup screen
DEFAULT_SETTINGS = {"pace": "steady", "auto_tempo": True, "speed": SPEED, "lang": DEFAULT_LANG}
LABELS = ("num", "abc", "do", "pc", "none")        # what the keys show: kept per profile, like the pace
COLORS = ("notes", "simple")
LOOKS = {"theme": ("dark", "light"), "palette": ("vesta", "ember", "forest", "slate", "plum"),
         "voice": ("grand", "epiano", "organ", "musicbox", "synth")}   # also kept per profile, so they follow it to every device


def load_settings():
    """The active profile's pace and tempo, and the language the whole piano speaks."""
    out = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(settings_file().read_text())
        if data.get("pace") in PACES:
            out["pace"] = data["pace"]
        if isinstance(data.get("auto_tempo"), bool):
            out["auto_tempo"] = data["auto_tempo"]
        if isinstance(data.get("speed"), (int, float)):
            out["speed"] = max(SPEED_MIN, min(SPEED_MAX, int(data["speed"])))
        if data.get("labels") in LABELS:
            out["labels"] = data["labels"]
        if data.get("colors") in COLORS:
            out["colors"] = data["colors"]
        for k, allowed in LOOKS.items():
            if data.get(k) in allowed:
                out[k] = data[k]
        if isinstance(data.get("scale"), (int, float)) and not isinstance(data.get("scale"), bool) and 0.6 <= data["scale"] <= 2:
            out["scale"] = round(float(data["scale"]), 1)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        lang = json.loads(SETTINGS_FILE.read_text()).get("lang")   # the bridge reads it here too
        if lang in LANGS:
            out["lang"] = lang
    except (OSError, ValueError, AttributeError):
        pass
    return out


def tr(text, **kw):
    """The text in the language chosen on the screen (English or Hebrew)."""
    return translate(load_settings()["lang"], text, **kw)


def trn(n, one, many, **kw):
    return _plural(load_settings()["lang"], n, one, many, **kw)


def save_settings(data):
    """The profile keeps its own copy of everything, the language included, so switching to it
    brings its language back. The piano's language stays in the main settings file."""
    f = settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    # Written aside and moved into place, the way the profiles already are. Two screens
    # saving at once used to be able to leave half a file behind, and half a file of
    # settings does not load.
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(f)
    if f != SETTINGS_FILE:
        try:
            root = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            root = {}
        if root.get("lang") != data.get("lang"):
            root["lang"] = data.get("lang")
            SETTINGS_FILE.write_text(json.dumps(root))
    # the main profile shares the piano's file, so each profile's language is also kept in its entry
    with PROFILE_LOCK_LANG:
        prof = load_profiles()
        me = next(p for p in prof["list"] if p["id"] == prof["active"])
        if me.get("lang") != data.get("lang") and data.get("lang") in LANGS:
            me["lang"] = data["lang"]
            save_profiles(prof)


# ---------- profiles: everyone who learns on this piano ----------
# One keyboard, several people. The active profile is whoever sits at the piano now, and it
# is the same for every screen and for Telegram. Each profile has its own progress, history,
# recordings, pace and language. The songs, the shortcuts and the volume are the piano's.
# The first profile ("main") keeps its files where they always were, so nothing moves.
# A PIN is optional. It stops others from switching into a profile and playing over its
# progress; it is not a lock on the network (that is the screen code's job).
PROFILES_MAX = 12
PIN_ITER = 60000                  # PBKDF2 rounds: a fraction of a second even on a small Pi
PIN_FAILS = {}                    # address -> times of wrong PINs
PROFILE_LOCK = threading.Lock()
PROFILE_LOCK_LANG = threading.Lock()   # save_settings runs inside PROFILE_LOCK when a profile switches


def load_profiles():
    try:
        data = json.loads(PROFILES_FILE.read_text())
        items = [p for p in data.get("list", []) if isinstance(p, dict) and re.fullmatch(r"main|p[0-9a-f]{8}", str(p.get("id")))]
    except (OSError, ValueError, AttributeError):
        data, items = {}, []
    if not any(p["id"] == "main" for p in items):
        items.insert(0, {"id": "main", "name": "", "pin": None, "created": 0})
    active = data.get("active") if any(p["id"] == data.get("active") for p in items) else "main"
    return {"active": active, "list": items}


def save_profiles(data):
    tmp = PROFILES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)                          # the PIN hashes stay with the service
    tmp.replace(PROFILES_FILE)


# Edit mode keeps corrected wording here, in the writable data folder, the way an app keeps content
# apart from its code. The code file stays fixed, so a system update never wipes an edit, and the
# service (which runs with the rest of the disk read-only) can still save it.
TEXT_OVERRIDE_LOCK = threading.Lock()
_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def _read_overrides(path):
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"en": {}, "he": {}}
    return {"en": dict(d.get("en") or {}), "he": dict(d.get("he") or {})}


def load_overrides():
    """{"en": {...}, "he": {...}}: for each English source text, the wording that replaces it.

    Two layers. What came with the code is the ground; what this machine edited sits on top,
    line by line rather than file by file, so editing one sentence here does not throw away
    the rest of what the release shipped."""
    out = _read_overrides(TEXT_DEFAULTS_FILE)
    here = _read_overrides(TEXT_OVERRIDES_FILE)
    for lang in ("en", "he"):
        out[lang].update(here[lang])
    return out


import html as htmlmod


# Edited wording is real HTML, because editing is how the page is laid out. What it may
# not carry is anything that runs: the text is written once and then drawn on every screen
# from then on, so a script saved here would outlive the editing session and follow every
# visitor. Everything that shapes and styles is kept; everything that executes is dropped.
EDIT_TAGS = {
    "a", "abbr", "b", "big", "blockquote", "br", "caption", "center", "cite", "code", "col",
    "colgroup", "dd", "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "kbd", "li", "mark", "ol", "p", "pre", "q", "s",
    "samp", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "u", "ul", "wbr",
}
EDIT_ATTRS = {"class", "style", "id", "title", "dir", "lang", "align", "colspan", "rowspan",
              "width", "height", "alt", "src", "href", "target", "rel", "data-tkey"}
_TAG_RE = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)\s*>")
_ATTR_RE = re.compile(r"""([A-Za-z_:][-\w:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""")
_URL_ATTRS = ("src", "href")


def _safe_url(value):
    """Only a plain address. javascript: runs, and a data: address can carry a whole page."""
    got = value.strip().replace("\x00", "")
    if re.match(r"^\s*(javascript|vbscript|data|file)\s*:", got, re.I):
        return None
    return got


def clean_html(raw):
    """Keeps the shape and the styling, drops anything that can execute: script and style
    blocks with their contents, every tag that is not in the list, every on... handler, and
    addresses that are really code."""
    text = re.sub(r"<\s*(script|style|iframe|object|embed|svg|math|template|noscript)\b.*?<\s*/\s*\1\s*>",
                  "", raw, flags=re.S | re.I)
    text = re.sub(r"<\s*/?\s*(script|style|iframe|object|embed|svg|math|template|noscript)\b[^>]*>",
                  "", text, flags=re.I)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    def one_tag(m):
        closing, name, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if name not in EDIT_TAGS:
            return ""
        if closing:
            return f"</{name}>"
        kept = []
        for attr in _ATTR_RE.finditer(attrs or ""):
            key = attr.group(1).lower()
            val = attr.group(2).strip("\"'")
            if key.startswith("on") or key not in EDIT_ATTRS:
                continue
            if key in _URL_ATTRS:
                val = _safe_url(val)
                if val is None:
                    continue
            kept.append(f'{key}="{htmlmod.escape(val, quote=True)}"')
        return f"<{name}{(' ' + ' '.join(kept)) if kept else ''}{'/' if selfclose else ''}>"

    return _TAG_RE.sub(one_tag, text)


def save_override(lang, key, value):
    """Sets one piece of wording, or clears it back to the built-in text. Returns None, or a reason."""
    if lang not in ("en", "he"):
        return "bad language"
    key, value = str(key), str(value)
    if not key.strip() or len(key) > 2000 or len(value) > 400000:
        return "bad text"
    # {word} markers only matter for the built-in strings keyed by their English text; a block
    # keyed by its data-tkey (e.g. "content.about.p1") is free HTML and carries no such markers.
    if _PLACEHOLDER_RE.search(key) and set(_PLACEHOLDER_RE.findall(key)) != set(_PLACEHOLDER_RE.findall(value)):
        return "the {word} markers have to stay, exactly as they are"
    value = clean_html(value)
    with TEXT_OVERRIDE_LOCK:
        d = load_overrides()
        if not value.strip() or value == key:
            d[lang].pop(key, None)
        else:
            d[lang][key] = value
        tmp = TEXT_OVERRIDES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        tmp.replace(TEXT_OVERRIDES_FILE)
    return None


# Images placed on the screen from edit mode: kept in the writable data folder, never in the code,
# and named by their content hash so the same picture is stored once and can be cached forever.
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MEDIA_MAX = 3 * 1024 * 1024


def _image_kind(raw):
    """The file extension for the picture in these bytes, or None if it is not an image we accept."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    # SVG is not accepted: it can carry script, and it is served from the same origin as the screen
    return None


def save_media(name, raw):
    """Stores one uploaded image and returns the path the page loads it from. Raises ValueError on a bad file."""
    if not raw:
        raise ValueError(tr("The file is empty"))
    if len(raw) > MEDIA_MAX:
        raise ValueError(tr("The image is larger than {n} MB", n=MEDIA_MAX // (1024 * 1024)))
    ext = _image_kind(raw)
    if ext is None:
        raise ValueError(tr("Only PNG, JPEG, GIF or WebP images can be used"))
    import hashlib
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fname = hashlib.sha256(raw).hexdigest()[:16] + ext
    dest = MEDIA_DIR / fname
    if not dest.exists():
        tmp = dest.with_suffix(ext + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(dest)
    return "/media/" + fname


def active_profile():
    return load_profiles()["active"]


def profile_dir(pid=None):
    pid = pid or active_profile()
    return DATA_DIR if pid == "main" else DATA_DIR / "profiles" / pid


def progress_file():
    return profile_dir() / "lessons.json"


def history_file():
    return profile_dir() / "history.json"          # one line per lesson: when, how long, what came of it


def rec_dir():
    return profile_dir() / "recordings"            # every lesson, recorded as it was played


def settings_file():
    return profile_dir() / "settings.json"


def pin_hash(pin, salt=None):
    import hashlib, secrets
    salt = salt or secrets.token_hex(16)
    return {"salt": salt, "hash": hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PIN_ITER).hex()}


def pin_ok(profile, pin):
    import hmac
    saved = profile.get("pin")
    if not saved:
        return True
    return isinstance(pin, str) and hmac.compare_digest(pin_hash(pin, saved["salt"])["hash"], saved["hash"])


def valid_name(name):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
    return name[:24] if name else None


def valid_pin(pin):
    return isinstance(pin, str) and re.fullmatch(r"\d{4,8}", pin) is not None


def switch_profile(prof, target):
    """Makes target the profile at the piano. Called with PROFILE_LOCK held."""
    by_id = {p["id"]: p for p in prof["list"]}
    by_id[prof["active"]]["lang"] = load_settings()["lang"]   # the one leaving keeps its language
    prof["active"] = target["id"]
    save_profiles(prof)
    lang = target.get("lang")                                  # the profile's own language comes back with it
    if lang in LANGS and lang != load_settings()["lang"]:
        save_settings(dict(load_settings(), lang=lang))
    log(f"Profile: {target.get('name') or 'main'}")


def profile_label(p):
    return p.get("name") or tr("Main")


def profiles_public():
    data = load_profiles()
    return {"active": data["active"],
            "list": [{"id": p["id"], "name": p.get("name", ""), "locked": bool(p.get("pin"))} for p in data["list"]]}


# ---------- every part of a song keeps its own record ----------
# No clock and no daily allowance: the player decides when to play and how much. Every lesson
# brings the pace's number of new parts, and another lesson can start right after it. A part
# is reviewed from memory at the start of each lesson until its count of clean showings in a row
# reaches 3, the lesson it was learned in being the first of them; a slip sends the count back to
# 0, so the part keeps coming back until it holds.
STEADY_STREAK = 3                          # clean showings in a row until a part counts as steady
DRILL_AFTER_RUN = 2                        # parts practised again after a run, the first that slipped
BETWEEN_RUNS = 4                           # seconds of quiet between one run and whatever follows it
END_RUN_PAUSE = 1.5                        # after the last full run of a lesson, with a message on the screen
BEFORE_BAND_RUN = 2                        # shorter: the same song twice in a row wants little wait
SPEED_MIN, SPEED_MAX, SPEED_STEP = 40, 100, 5


def new_part(now, adj=0):
    return {"streak": 1, "attempts": 1, "errors": 0, "first": now, "last": now, "adj": adj}


def schedule(part, clean, now):
    """Keeps the count of one part after a review from memory."""
    part["attempts"] = part.get("attempts", 0) + 1
    part["last"] = now
    if clean:
        part["streak"] = part.get("streak", 0) + 1
    else:
        part["streak"] = 0
        part["errors"] = part.get("errors", 0) + 1


def needs_review(part):
    return part.get("streak", 0) < STEADY_STREAK


def part_state(part):
    """What the screen shows: "settling" until a part's count of clean showings reaches 3, then "steady"."""
    return "settling" if needs_review(part) else "steady"


def base_speed(settings):
    """The speed a lesson starts from. With the tempo adapting, the pace chooses it; with it
    off, the slider's fixed speed is used for everything."""
    return PACE_SPEED[settings["pace"]] if settings["auto_tempo"] else settings["speed"]


def clamp_speed(sp):
    return max(SPEED_MIN, min(SPEED_MAX, int(sp)))


def ensure_parts(rec, total, speed, now):
    """Songs learned before the schedule existed: every learned part is due once, then goes on.

    Parts saved before the pace chose the speed kept an absolute speed that started from the
    slider. What they learned is the distance from that start, so it becomes their offset."""
    if "parts" not in rec:
        learned = min(int(rec.get("learned", 0)), total)
        when = rec.get("last") or now
        rec["parts"] = {str(k): {"streak": 1, "attempts": 1, "errors": 0, "first": when, "last": when, "adj": 0}
                        for k in range(learned)}
    for part in rec["parts"].values():
        if "adj" not in part:
            old = part.pop("speed", speed)
            part["adj"] = int(old) - speed if isinstance(old, (int, float)) else 0
        part.pop("speed", None)
        if "due" in part:                  # saved while the parts had a clock: a long gap meant it held
            if part.get("stage") == "review" and part.get("interval", 0) >= 7:
                part["streak"] = max(part.get("streak", 0), STEADY_STREAK)
            for key in ("stage", "step", "due", "interval"):
                part.pop(key, None)
    return rec["parts"]


def song_plan(rec, total, settings, now):
    """What a lesson of this song does now: the new parts, and the parts not steady yet.
    Nothing here depends on the time: a lesson started right after another one goes on."""
    parts = ensure_parts(rec, total, settings["speed"], now)
    known = sorted(int(k) for k in parts if int(k) < total)
    allowed = PACES[settings["pace"]]
    nxt = known[-1] + 1 if known else 0
    due = [k for k in known if needs_review(parts[str(k)])]
    new = list(range(nxt, min(nxt + allowed, total)))
    states = {"settling": 0, "steady": 0}
    for k in known:
        states[part_state(parts[str(k)])] += 1
    return {"known": known, "due": due, "new": new, "states": states,
            "new_left": allowed}


def load_progress():
    try:
        return json.loads(progress_file().read_text())
    except (OSError, ValueError):
        return {}


def save_progress(data):
    f = progress_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(f)


# =========================
# Engine
# =========================
class Engine:
    def __init__(self):
        self.cmds = queue.Queue()
        self.stop_event = threading.Event()
        self.chat = None
        self.inp = self.out = None
        self.amidi = None
        self.raw = None           # file descriptor of the raw MIDI output
        self.raw_warned = False
        self.last_key_time = 0
        self.rhythm = None
        self.starts = None
        self.band = None
        self.band_level = BAND_LEVEL
        self.touch = PLAYER_START
        self.speed = SPEED
        self.found = []
        self.unplugged = threading.Event()   # set by the watchdog while the piano is gone
        self.hands = None         # which hand and finger plays each note, from piano_hands.py
        self.mistakes = {}        # piece title -> mistakes in this lesson, for the summary
        self.piece = None         # what a mistake is counted under, when the title is not it
        self.auto_tempo = True    # from the settings, per lesson
        self.run_detail = None    # rhythm and hesitations of the last full run
        self.run_slips = []       # notes of the last full run that came out wrong or were skipped
        self.run_reached = 0      # how far that run got, so an abandoned tail is not judged

        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"piano-game-{os.getpid()}")
        if MQTT_USER:
            self.mq.username_pw_set(MQTT_USER, MQTT_PASS)
        self.mq.on_connect = self.on_connect
        self.mq.on_message = self.on_message

    # ---------- MQTT ----------
    def on_connect(self, client, userdata, flags, rc, props=None):
        client.subscribe("piano/game/cmd")
        client.subscribe("piano/game/event")
        client.subscribe("piano/notes")         # every key pressed, for the live screen
        client.subscribe("piano/pedal")         # the sustain pedal, shown on the screen
        client.publish("piano/lang", load_settings()["lang"], retain=True)   # Home Assistant answers in it
        self.publish_profile()
        self.status("idle")

    def on_message(self, client, userdata, msg):
        if msg.topic == "piano/game/event":
            self.keyboard_event(msg)
            return
        if msg.topic == "piano/pedal":
            ui(pedal=msg.payload.decode("utf-8", "replace").strip() in ("down", "hold"))
            return
        if msg.topic == "piano/notes":
            try:
                live_press(int(msg.payload))
            except ValueError:
                pass
            return
        parts = msg.payload.decode("utf-8", "replace").split("|", 2)
        action = parts[0].strip()
        chat = parts[1].strip() if len(parts) > 1 else ""
        args = parts[2].strip() if len(parts) > 2 else ""
        if action == "lessonstop":
            self.stop_event.set()      # handled right away, even in the middle of a lesson
            if FLAG_GAME.exists():
                self.reply(tr("⏹ Stopping the lesson..."), chat)
            else:
                self.reply(tr("ℹ️ No lesson is running"), chat)
            return
        if action in GUARDED_ACTIONS and not admin_ok(args):
            log(f"MQTT: {action} refused, no valid secret")
            self.reply(tr("🚫 That command needs the admin secret from the settings file"), chat)
            return
        if action in GUARDED_ACTIONS:
            args = strip_admin(args)
        if action == "textexport":
            self.reply(json.dumps(_read_overrides(TEXT_OVERRIDES_FILE),
                                  ensure_ascii=False, indent=1), chat)
            return
        if action == "code":
            what = args.strip().lower()
            if what == "off":
                set_code_on(False)
                self.reply(tr("🔓 The screens are no longer asked for a code. Everyone on the network can use the piano"), chat)
            elif what == "on":
                set_code_on(True)
                self.reply(tr("🔒 The screens are asked for a code again: {code}", code=screen_code()), chat)
            elif what == "new":
                self.reply(tr("🔑 A new code was drawn: {code}\nThe screens already signed in stay in", code=screen_code(fresh=True)), chat)
            elif code_on():
                self.reply(tr("🔑 Lesson screen code: {code}\nEnter it once on each screen, it is remembered there",
                              code=screen_code()), chat)
            else:
                self.reply(tr("🔓 The lesson screen has no code. Turn it on with: code on"), chat)
            return
        if action == "editmode":
            if args == "off":
                EDIT_FLAG.unlink(missing_ok=True)
            else:
                EDIT_FLAG.parent.mkdir(parents=True, exist_ok=True)
                EDIT_FLAG.touch()
            self.reply(("Editing is on, reload the screen" if EDIT_FLAG.exists()
                        else "Editing is off, reload the screen"), chat)
            return
        if action == "profile":
            self.profile_cmd(chat, args)
            return
        if action == "lesson" and FLAG_GAME.exists():
            self.reply(tr("ℹ️ A lesson is already running. To stop it: /lessonstop"), chat)
            return
        self.cmds.put((action, chat, args))

    def profile_cmd(self, chat, args):
        """/pianoprofile lists the profiles; /pianoprofile <name> switches to one without a PIN.
        A PIN is never typed into a chat, so a locked profile is chosen on the screen."""
        with PROFILE_LOCK:
            prof = load_profiles()
            if not args:
                lines = [tr("👥 Profiles:")]
                for p in prof["list"]:
                    mark = "▶" if p["id"] == prof["active"] else "•"
                    lines.append(f"{mark} {profile_label(p)}" + (" 🔒" if p.get("pin") else ""))
                lines.append(tr("To switch: /pianoprofile <name>"))
                self.reply("\n".join(lines), chat)
                return
            want = args.strip().lower()
            target = next((p for p in prof["list"] if profile_label(p).lower() == want
                           or (p["id"] == "main" and want in ("main", "ראשי"))), None)
            if target is None:
                self.reply(tr("❓ No profile named {name}. The list: /pianoprofile", name=args.strip()), chat)
                return
            if target["id"] == prof["active"]:
                self.reply(tr("ℹ️ {name} is already at the piano", name=profile_label(target)), chat)
                return
            if FLAG_GAME.exists() or REPLAY.thread is not None and REPLAY.thread.is_alive():
                self.reply(tr("ℹ️ Not while a lesson or a replay is playing"), chat)
                return
            if target.get("pin"):
                self.reply(tr("🔒 {name} has a PIN: switch on the lesson screen", name=profile_label(target)), chat)
                return
            switch_profile(prof, target)
        self.publish_profile()
        self.reply(tr("👋 {name} is at the piano now", name=profile_label(target)), chat)

    def keyboard_event(self, msg):
        """The bridge's own view of the USB link: connected or disconnected.

        It notices a cut from the key listener side, often before the watchdog here does,
        so a lesson stops at once. A retained message is only the last known state from
        before this engine started, not an event, and is ignored: taking an old
        "disconnected" at start would block every lesson until the next reconnect.
        """
        if msg.retain:
            return
        event = msg.payload.decode("utf-8", "replace").strip()
        if event == "disconnected":
            self.unplugged.set()
            self.stop_event.set()           # wakes any wait at once
            ui(piano=False)
            log("🔴 The bridge reports the keyboard disconnected")
        elif event == "connected":
            self.unplugged.clear()
            ui(piano=True)
            log("🟢 The bridge reports the keyboard connected")

    def pub(self, topic, msg, retain=False):
        info = self.mq.publish(topic, msg, qos=1, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log(f"MQTT publish failed on {topic}: rc={info.rc}")

    def publish_profile(self):
        """Who is at the piano, their language and their day, for Home Assistant."""
        prof = load_profiles()
        me = next(p for p in prof["list"] if p["id"] == prof["active"])
        self.pub("piano/profile", me.get("name") or "main", retain=True)
        self.pub("piano/lang", load_settings()["lang"], retain=True)
        self.publish_practice()

    def publish_practice(self):
        """Today's practice for Home Assistant's reminder. Retained, so a restart of either side keeps it."""
        try:
            self.pub("piano/practice", json.dumps(practice_state()), retain=True)
        except Exception as e:
            log(f"The practice state was not published: {e}")

    def practice_clock(self):
        """Parts come due as time passes, and a new day starts at midnight: published every 10 minutes."""
        while True:
            time.sleep(600)
            if not FLAG_GAME.exists():
                self.publish_practice()

    def reply(self, text, chat=None):
        chat = chat or self.chat
        if chat:
            self.pub("piano/shortcut/reply", f"{chat}|{telegram(text) if load_settings()['lang'] == 'he' else text}")
        with UI_LOCK:
            UI["notice"] = [UI["notice"][0] + 1, text]      # the screen shows it too

    def status(self, state, song=None):
        self.pub("piano/game/status", state, retain=True)
        if song is not None:
            self.pub("piano/game/song", song, retain=True)
        elif state == "idle":
            self.pub("piano/game/song", "", retain=True)     # no stale song after a lesson

    # ---------- MIDI ----------
    def amidi_device(self):
        """The raw MIDI device of the piano, e.g. hw:0,0,0, straight from amidi -l."""
        if AMIDI_DEVICE:
            return AMIDI_DEVICE
        if not shutil.which("amidi"):
            return None
        try:
            out = subprocess.run(["amidi", "-l"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in ("IO", "O") and PIANO_NAME in line:
                return parts[1]
        return None

    def open_ports(self):
        ins = [n for n in mido.get_input_names() if PIANO_NAME in n]
        if not ins:
            return False
        self.inp = mido.open_input(ins[0])

        self.out = None
        self.amidi = None
        if OUT_BACKEND in ("auto", "rtmidi"):
            outs = [n for n in mido.get_output_names() if PIANO_NAME in n]
            if outs:
                self.out = mido.open_output(outs[0])
                time.sleep(PORT_WARMUP)   # without this the first notes go out before ALSA subscribes
        if OUT_BACKEND in ("auto", "amidi") and self.out is None:
            self.amidi = self.amidi_device()
            self.raw = self.open_raw(self.amidi)
        if self.out is None and self.amidi is None:
            log("No output port found for the keyboard")
            self.close_ports()
            return False
        log(f"Ports: input {ins[0]} | output {'rtmidi' if self.out else 'amidi ' + self.amidi}")
        self.piano_voice()
        return True

    def piano_voice(self):
        """Puts channel 1 back on the piano voice.

        Channel 1 is the one the instrument's own keys play on, and the one demos and hints
        use. A song file that sets its melody track to strings or an organ would otherwise
        leave that voice on the keyboard, in the lesson and after it.
        """
        data = bytes([0xB0, 0, 0, 0xB0, 32, 0, 0xC0, MELODY_PROGRAM])
        if self.out is not None:
            for i in range(0, len(data), 3):
                msg = mido.Message.from_bytes(list(data[i:i + 3]))
                self.out.send(msg)
        else:
            self.send_raw(data)

    def open_raw(self, device):
        """Opens the piano's raw MIDI output once for the whole lesson, without blocking.

        amidi starts a new process for every message, which is fine for a click and far too
        slow for a band. Writing to the device file is what amidi does inside, only kept open.
        O_NONBLOCK means a stuck USB link drops a message instead of freezing the lesson,
        so the stop command and the control keys always keep working.
        """
        m = re.match(r"hw:(\d+),(\d+)", device or "")
        if not m:
            return None
        path = f"/dev/snd/midiC{m.group(1)}D{m.group(2)}"
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            log(f"Direct output did not open ({path}): {e}, falling back to amidi")
            return None

    def send_raw(self, data):
        """Raw MIDI bytes to the piano. Never waits."""
        if self.raw is not None:
            try:
                os.write(self.raw, data)
                return
            except (BlockingIOError, OSError) as e:
                if not self.raw_warned:
                    self.raw_warned = True
                    log(f"The keyboard is not taking data right now ({e}), messages are dropped instead of waiting")
                return
        if self.amidi:
            try:
                subprocess.run(["amidi", "-p", self.amidi, "-S", data.hex(" ")],
                               capture_output=True, timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass

    def send(self, note, velocity, channel=0):
        """One note on or off, through whichever output backend is open."""
        if RECORDER is not None and channel == 0:
            RECORDER.add("d", note, velocity)
        if self.out is not None:
            kind = "note_on" if velocity else "note_off"
            self.out.send(mido.Message(kind, note=note, velocity=velocity, channel=channel))
            return
        status = (0x90 if velocity else 0x80) | channel
        self.send_raw(bytes([status, note, velocity]))

    def prepare_band(self, song, src, shift):
        """Loads the accompaniment of the song as single messages, moved to the melody's key."""
        self.band = None
        if not src:
            return
        try:
            mid = mido.MidiFile(str(src))
            if len(mid.tracks) < 2:
                log("A single-track file: no accompaniment apart from the melody")
                return
            skip = melody_track(mid)
            # the band never changes the voice of channel 1: that channel is the player's piano
            events = [(t, b) for t, b in band_events(str(src), skip, shift)
                      if not ((b[0] & 0x0F) == 0 and ((b[0] & 0xF0) == 0xC0 or
                                                     ((b[0] & 0xF0) == 0xB0 and b[1] in (0, 32))))]
            if any((b[0] & 0xF0) == 0x90 and b[2] for _, b in events):
                self.band = events
                log(f"Accompaniment: {len(events)} messages, shift {shift:+d}")
        except Exception as e:                 # a broken file must not block the lesson
            log(f"The accompaniment could not be built: {e}")

    def band_plan(self, notes, offset):
        """Splits the band into the stretch that belongs to each melody note of this run.

        Segment k holds what the file plays from melody note k until the next one, timed
        from note k. Everything before the first note of the run is left out, except how the
        instruments are set up: the band starts with the player's first key, never before it.

        Many arrangements double the tune on another instrument. That copy is dropped, with
        its note-off: the player is the one playing the tune, and a second copy a few
        milliseconds off from the hand is what makes the sound smear.
        """
        if not self.band or not self.starts:
            return None
        count = len(notes)
        starts, last = [], None
        for k in range(count):
            t = self.starts[offset + k] if offset + k < len(self.starts) else None
            last = t if t is not None else (last if last is not None else 0.0)
            starts.append(last)
        after = self.starts[offset + count] if offset + count < len(self.starts) else None
        edges = starts + [after if after is not None else starts[-1] + FINAL_RING]
        setup_last, intro = {}, []             # intro stays empty: see below
        segs = [[] for _ in range(count)]
        k = 0
        doubled = set()                      # (channel, note) of a dropped copy, until its note-off
        for t, data in self.band:
            kind, ch = data[0] & 0xF0, data[0] & 0x0F
            if kind == 0x90 and data[2] and ch != 9:
                j = max(0, min(count - 1, k))
                near = [i for i in (j - 1, j, j + 1) if 0 <= i < count]
                if any(abs(t - starts[i]) <= DOUBLE_WINDOW and data[1] == notes[i] for i in near):
                    doubled.add((ch, data[1]))
                    continue
            elif kind in (0x80, 0x90) and (ch, data[1]) in doubled:
                doubled.discard((ch, data[1]))
                continue
            if t < edges[0]:
                # Whatever the file plays before the player's first note is never played: the band follows
                # the player and never starts on its own (an arrangement may play a whole verse before the
                # melody track comes in). Only how the instruments are set up is kept, the last value of each.
                if (data[0] & 0xF0) in (0xB0, 0xC0, 0xE0):
                    setup_last[(data[0] & 0xF0, ch, data[1] if kind == 0xB0 else 0)] = data
                continue
            if t >= edges[-1]:
                break
            while k + 1 < count and t >= edges[k + 1]:
                k += 1
            segs[k].append((t - edges[k], data))
        return {"setup": list(setup_last.values()), "intro": intro, "segs": segs,
                "gaps": [edges[i + 1] - edges[i] for i in range(count)]}

    def band_off(self, sounding):
        """Silences whatever the band left sounding, on every channel it used."""
        for ch, note in list(sounding):
            self.send_raw(bytes([0x80 | ch, note, 0]))
        for ch in {c for c, _ in sounding} | {CLICK_CHANNEL}:
            self.send_raw(bytes([0xB0 | ch, 64, 0, 0xB0 | ch, 123, 0]))
        sounding.clear()

    def beat(self):
        """The song's beat length in seconds, taken from its own note lengths."""
        spans = sorted(d + g for d, g in (r for r in (self.rhythm or []) if r))
        if not spans:
            return 0.5
        return min(MAX_BEAT, max(MIN_BEAT, spans[len(spans) // 2]))

    def count_in(self):
        """Four clicks on the drum channel, so a segment starts in tempo instead of on a guess."""
        gap = self.beat()
        ui(msg=tr("Count-in"))
        for _ in range(CLICK_BEATS):
            self.check_abort()
            self.send(CLICK_NOTE, CLICK_VELOCITY, CLICK_CHANNEL)
            time.sleep(0.06)
            self.send(CLICK_NOTE, 0, CLICK_CHANNEL)
            time.sleep(max(0.05, gap - 0.06))
        ui(msg="")

    def close_ports(self):
        try:
            if self.out is not None or self.raw is not None or self.amidi:
                self.piano_voice()
        except Exception:
            pass
        for p in (self.inp, self.out):
            try:
                if p:
                    p.close()
            except Exception:
                pass
        self.inp = self.out = None
        self.amidi = None
        if self.raw is not None:
            try:
                os.close(self.raw)
            except OSError:
                pass
        self.raw = None
        self.raw_warned = False

    def piano_present(self):
        """Is the piano on the ALSA bus, the same check the bridge's watchdog makes.

        A check that fails counts as absent: a hung aconnect usually means a stuck USB
        link. One miss is never enough to stop a lesson, WATCH_MISSES in a row are.
        """
        if not shutil.which("aconnect"):
            try:
                return any(PIANO_NAME in n for n in mido.get_input_names())
            except Exception:
                return False
        try:
            p = subprocess.run(["aconnect", "-i"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        return p.returncode == 0 and PIANO_NAME in p.stdout

    def watchdog(self):
        """Watches the USB link all the time, shows it on the screen, and stops a lesson on a cut.

        One failed check is not enough, the bus can blink during a reconnect; WATCH_MISSES
        in a row are. Coming back is taken at once.
        """
        misses, state = 0, None
        while True:
            ok = self.piano_present()
            misses = 0 if ok else misses + 1
            now = True if ok else (False if misses >= WATCH_MISSES else state)
            if now is not None and now != state:
                state = now
                ui(piano=state)
                if state:
                    self.unplugged.clear()
                    if LIVE_OUT is not None:     # the screen's keys open the new device on their next note
                        with LIVE_OUT.lock:
                            LIVE_OUT._drop()
                    log("🟢 Keyboard connected")
                else:
                    self.unplugged.set()
                    self.stop_event.set()        # wakes any wait at once
                    log("🔴 Keyboard disconnected")
                self.pub("piano/game/piano", "connected" if state else "disconnected", retain=True)
            time.sleep(WATCH_EVERY)

    def check_abort(self):
        if self.unplugged.is_set():
            raise Stop("unplugged")
        if FLAG_ALARM.exists():
            raise Stop("alarm")
        if self.stop_event.is_set():
            raise Stop("stop")
        if time.time() - self.last_key_time > IDLE_LIMIT:
            raise Stop("idle")

    def pause(self, seconds):
        """time.sleep that a stop, a disconnect or the alarm cuts short."""
        if self.stop_event.wait(seconds):
            self.check_abort()
        if FLAG_ALARM.exists():
            self.check_abort()

    def drain(self):
        for _ in self.inp.iter_pending():
            pass

    def tempo_scale(self):
        """Seconds of playing per second of the file: 60% speed plays everything 1.67 times longer."""
        return 100 / max(20, min(100, getattr(self, "speed", SPEED)))

    def timing(self, index):
        """(length, gap) for the note at this position in the song, in seconds."""
        if not self.rhythm or index is None or index >= len(self.rhythm) or not self.rhythm[index]:
            return NOTE_SECS, NOTE_GAP
        dur, gap = self.rhythm[index]
        scale = self.tempo_scale()
        return (min(MAX_NOTE * scale / TEMPO_SCALE, max(MIN_NOTE, dur * scale)),
                min(MAX_GAP * scale / TEMPO_SCALE, gap * scale))

    def play(self, notes, velocity=80, follow=False, start=None, ui_at=None):
        """Plays the segment, and gives up the moment a key is pressed.

        Returns the keys that came in while it played: they are the start of an
        answer, not noise, so the demo never talks over someone already playing.
        """
        log(f"Playing {labels(notes)} (velocity {velocity})")
        self.drain()
        caught = []
        for i, n in enumerate(notes):
            if follow:
                self.pub("piano/game/note", f"{i + 1}/{len(notes)} {label(n)}")
            ui(mode="demo", demo=(i if ui_at is None else ui_at), expect=-1)
            self.check_abort()
            dur, gap = self.timing(None if start is None else start + i)
            self.send(n, velocity)
            caught += self.wait_quiet(dur)
            self.send(n, 0)
            if caught:
                log(f"The player came in, the demo stops ({labels(caught)})")
                ui(mode="wait", demo=-1)
                return caught
            caught += self.wait_quiet(gap)
            if caught:
                log(f"The player came in, the demo stops ({labels(caught)})")
                ui(mode="wait", demo=-1)
                return caught
        caught += self.wait_quiet(0.2)
        ui(mode="wait", demo=-1)
        return caught

    def wait_quiet(self, seconds):
        """Sleeps, and collects any keys pressed during that time."""
        end = time.time() + seconds
        hits = []
        while time.time() < end:
            self.check_abort()
            for msg in self.inp.iter_pending():
                if msg.type == "note_on" and msg.velocity > 0:
                    self.last_key_time = time.time()
                    hits.append(msg.note)
            if self.stop_event.wait(0.01):
                self.check_abort()
        return hits

    def next_key(self, timeout):
        """Returns the next pressed note, or None after timeout seconds of silence."""
        end = time.time() + timeout
        while time.time() < end:
            self.check_abort()
            for msg in self.inp.iter_pending():
                if msg.type == "note_on" and msg.velocity > 0:
                    self.last_key_time = time.time()
                    log(f"Key {label(msg.note)}")
                    return msg.note
            time.sleep(0.01)
        return None

    def poll_key(self):
        """The next pressed key if there is one, without waiting."""
        for msg in self.inp.iter_pending():
            if msg.type == "note_on" and msg.velocity > 0:
                self.last_key_time = time.time()
                self.touch += (msg.velocity - self.touch) * PLAYER_SMOOTH
                log(f"Key {label(msg.note)}")
                return msg.note
        return None

    def run_with_band(self, notes, offset=0, band=True, title=None):
        """The whole song, or one part of it, in one go, with the band following the player.

        The band never runs ahead: its part between two melody notes is released when the
        first of them is played, and stretched to the pace the player has been keeping.
        Waiting on a note makes the band wait too. The run ends on the last note of the
        song, or after TEST_END_SILENCE without a key.

        No numbers on the screen: this is the part that is played from memory, for fun.
        offset is where these notes start in the song. band=False leaves the accompaniment
        out, so what is heard is the player alone: that is the run a part is measured by,
        because a chord under the melody is itself a reminder of what comes next.
        """
        controls = {KEY_SHOW: "show", KEY_EXIT: "exit"}
        n = len(notes)
        with_band = bool(self.band) and band
        ui(fing=[], title=title or tr("🎻 Everything in one go, with the band" if with_band else "▶ Everything learned so far, in one go"), seq=[label(x) for x in notes], midi=list(notes),
           show=False, total=n, pos=0)
        plan = self.band_plan(notes, offset) if with_band else None
        self.count_in()

        pending, sounding = [], set()        # pending: (due time, bytes), kept in time order
        held = []                            # note-offs put off while you hesitate: (deadline, bytes)
        pace = self.tempo_scale()            # player's seconds per second of the file
        # the band follows your touch: its typical note is kept at band_level percent of how
        # hard you play, and every note keeps its place relative to the others, so accents stay
        self.touch = PLAYER_START
        vels = sorted(d[2] for seg in ((plan or {}).get("segs") or []) + [(plan or {}).get("intro") or []]
                      for _, d in seg if (d[0] & 0xF0) == 0x90 and d[2])
        band_typical = vels[len(vels) // 2] if vels else 0
        played, pos, last_hit = [], 0, None
        hits = []                            # (index in the song, time) of every right key
        slips = set()                        # notes where a wrong key came, or that were skipped
        hinted = set()                       # notes already named, so the help is said once
        end_at = None

        def show_note(at):
            """Names the note the run is waiting for.

            A run measures what is remembered, and being stuck with no way forward measures
            nothing: the player sits in silence until the run times out and learns none of it.
            So the note is named, by the show key at any moment or by itself after a few seconds
            of silence, and it counts as a note that was not remembered, exactly like a wrong key.
            """
            if at >= n or at in hinted:
                return
            hinted.add(at)
            slips.add(at)
            ui(msg=tr("Note {i} is {key}", i=at + 1, key=label(notes[at])))
            log(f"Stuck at note {at + 1}: it is {label(notes[at])}")

        def release(items, at):
            for dt, data in items:
                pending.append((at + dt * pace, data))
            pending.sort(key=lambda e: e[0])

        def is_on(data):
            return (data[0] & 0xF0) == 0x90 and data[2] > 0

        def out(data):
            kind, ch = data[0] & 0xF0, data[0] & 0x0F
            if is_on(data):
                # a note struck again must not be cut by an old release still waiting in held
                held[:] = [h for h in held if not ((h[1][0] & 0x0F) == ch and h[1][1] == data[1])]
                if (ch, data[1]) in sounding:            # struck again before it was released
                    self.send_raw(bytes([0x80 | ch, data[1], 0]))
                sounding.add((ch, data[1]))
                if band_typical:
                    scale = self.touch * self.band_level / 100 / band_typical
                    data = bytes([data[0], data[1], max(1, min(127, round(data[2] * scale)))])
            elif kind in (0x80, 0x90):
                sounding.discard((ch, data[1]))
            self.send_raw(data)

        if plan:
            for data in plan["setup"]:
                self.send_raw(data)
            if plan["intro"]:
                # intro times are negative, counted back from the first note
                release([(dt - plan["intro"][0][0], d) for dt, d in plan["intro"]], time.time())
            log(f"Following band: {sum(len(x) for x in plan['segs'])} messages")
        last_key = time.time()
        try:
            while True:
                self.check_abort()
                now = time.time()
                while pending and pending[0][0] <= now:
                    due, data = pending.pop(0)
                    # while the next melody key is still to come, the band does not let go of its
                    # chord: a short hesitation is bridged instead of dropping into silence.
                    # Drums are not held, a held drum means nothing.
                    if (pos < n and (data[0] & 0xF0) in (0x80, 0x90) and not is_on(data)
                            and (data[0] & 0x0F) != 9):
                        held.append((due + BAND_HOLD, data))
                        continue
                    out(data)
                if held and held[0][0] <= now:
                    late = [h for h in held if h[0] <= now]
                    held[:] = [h for h in held if h[0] > now]
                    for _, data in late:
                        out(data)
                if end_at is not None and now >= end_at:
                    break
                ui(mode="wait", expect=min(pos, n - 1), demo=-1, pos=pos)
                key = self.poll_key()
                if key is None:
                    if end_at is None and played and now - last_key > TEST_END_SILENCE:
                        log("Silence, the run is over")
                        break
                    if end_at is None and now - last_key > HINT_SILENCE:
                        show_note(pos)
                    time.sleep(0.004)
                    continue
                last_key = now
                if key in controls:
                    if controls[key] == "exit":
                        raise Stop("keys")
                    show_note(pos)           # the show key asks for the next note, at any moment
                    continue
                played.append(key)
                if pos >= n:
                    continue
                hit = pos if key == notes[pos] else \
                    pos + 1 if pos + 1 < n and key == notes[pos + 1] else None
                if hit is None:
                    slips.add(pos)           # the run is what measures a part now
                    ui(msg=tr("Not that one"))   # without this a run says nothing about a wrong key
                    continue                 # a wrong key: the band waits for the right one
                if last_hit is not None and plan:
                    t_prev, k_prev = last_hit
                    file_gap = sum(plan["gaps"][k_prev:hit])
                    if file_gap > 0.08:
                        ratio = min(FOLLOW_MAX, max(FOLLOW_MIN, (now - t_prev) / file_gap))
                        pace = pace * (1 - FOLLOW_SMOOTH) + ratio * FOLLOW_SMOOTH
                if UI.get("msg"):
                    ui(msg="")               # the right key clears the warning
                last_hit = (now, hit)
                hits.append((offset + hit, now))
                for skipped in range(pos, hit):
                    slips.add(skipped)
                if plan:
                    # whatever is still waiting from earlier notes belongs to a moment that has
                    # passed: its note-ons are dropped, and its note-offs go out right now, so
                    # the old chord ends when the new melody note comes instead of ringing over it
                    old, pending[:] = pending[:], []
                    for _, data in held + old:
                        if not is_on(data):
                            out(data)
                    held.clear()
                    # a skipped note's stretch still holds the releases of notes struck earlier;
                    # without them those notes would hang until the end of the run
                    for skipped in range(pos, hit):
                        for _, data in plan["segs"][skipped]:
                            if not is_on(data):
                                out(data)
                    release(plan["segs"][hit], now)
                pos = hit + 1
                if pos >= n:
                    log("The last note was played")
                    end_at = now + (min(FINAL_RING, plan["gaps"][-1] * pace) if plan else 0.3)
        finally:
            self.band_off(sounding)
        log(f"Run finished: {pos}/{n} notes, {len(played)} key presses")
        score = round(difflib.SequenceMatcher(None, notes, played).ratio() * 100)
        self.run_detail = self.run_scores(score, hits, played, n)
        self.run_slips, self.run_reached = sorted(slips), pos
        wrong = max(0, len(played) - len(hits))
        if wrong:
            # a key that missed in a run is a mistake of the lesson like any other. All the runs
            # share one line: four of them would take the whole list and leave no room for the parts
            piece = tr("▶ The full runs")
            self.mistakes[piece] = self.mistakes.get(piece, 0) + wrong
        return score

    def run_scores(self, accuracy, hits, played, n):
        """The full run in more than one number: right notes, rhythm, hesitations, wrong keys.

        Rhythm compares the time between each two notes you played with the time between
        them in the song, after taking out your overall tempo: playing the whole song slower
        is fine, playing one note late is not. A gap more than twice as long as it should be
        counts as a hesitation.
        """
        ratios = []
        for (k1, t1), (k2, t2) in zip(hits, hits[1:]):
            if k2 != k1 + 1 or not self.starts or k2 >= len(self.starts):
                continue
            a, b = self.starts[k1], self.starts[k2]
            if a is None or b is None or b - a < 0.08:
                continue
            ratios.append((t2 - t1) / (b - a))
        rhythm = hesitations = None
        if len(ratios) >= 3:
            mid = sorted(ratios)[len(ratios) // 2]
            norm = [r / mid for r in ratios if mid > 0]
            if norm:
                rhythm = round(100 * sum(1 for r in norm if 0.7 <= r <= 1.3) / len(norm))
                hesitations = sum(1 for r in norm if r > 2.0)
        return {"accuracy": accuracy, "rhythm": rhythm, "hesitations": hesitations,
                "wrong": max(0, len(played) - len(hits)), "reached": len(hits), "notes": n}

    def get_midi(self, chat, args):
        """Searches a public archive for a full song, and downloads the chosen hit."""
        args = args.strip()
        if not args:
            self.reply(tr("❌ A name is needed. Example: /getmidi bella ciao"), chat)
            return
        if args.isdigit():
            pick = int(args)
            if not self.found or pick < 1 or pick > len(self.found):
                self.reply(tr("❌ No result with that number. Search first: /getmidi <name>"), chat)
                return
            name, url, credit = self.found[pick - 1]
            path = download_midi(url, name, credit)
            song, notes, rhythm, starts, source, shift = load_song(path.stem)
            if not notes:
                self.reply(tr("⬇️ Downloaded: {name}\nNo melody could be found in it", name=path.name), chat)
                return
            groups = phrases(notes, rhythm)
            self.reply(tr("⬇️ Downloaded: {name}\nFrom {source}, {license}\n{notes} notes, {parts} phrases\n"
                          "Opening: {opening}\nTo start: /lesson {stem}", name=path.name, source=credit['source'],
                          license=credit['license'], notes=len(notes), parts=len(groups),
                          opening=labels(notes[:8]), stem=path.stem), chat)
            return

        hits, missing, failed = search_midi(args)
        hits = hits[:TELEGRAM_RESULTS]
        self.found = hits
        typo = (tr("\n⚠️ The word {words} is in no name in the archives. A typo?", words=', '.join(missing))
                if missing else "")
        if failed:
            typo += tr("\n⚠️ Not answering right now: {names}", names=', '.join(failed))
        if not hits:
            self.reply(tr("❌ Nothing found for: {q}{typo}", q=args, typo=typo), chat)
            return
        lines = [tr("🔎 Results for {q}:", q=args)]
        lines += [f"{i}. {n} · {c['source']}" for i, (n, _, c) in enumerate(hits, 1)]
        lines.append(tr("\nTo download: /getmidi <number>") + typo)
        self.reply("\n".join(lines), chat)

    # ---------- The method ----------
    def fingers(self, start, count):
        """["Right 3", "Left 1 (stretch)", ...] for the notes of a piece, or [] with no hand plan."""
        if not self.hands or start is None:
            return []
        out = []
        for f in self.hands["fingers"][start:start + count]:
            hand, rest = hand_label(f, full=True).split(" ", 1)
            out.append(tr(hand + " {f}", f=rest.replace("↔", tr(" (stretch)"))))
        return out

    def answer(self, seg, early=None):
        """True = played clean, "fixed" = finished after one slip, False = failed,
        "show" = 1#, "exit" = 4#.

        A wrong key first gets one more try without the answer: memory often finds the key
        on the second go, and finding it yourself is what makes it stick. A second wrong key,
        or being stuck, shows the expected note, only that one, so the help lands exactly
        where memory failed.
        """
        pending = list(early or [])
        i, tried, clean = 0, False, True
        while i < len(seg):
            ui(mode="wait", expect=i, demo=-1)
            key = pending.pop(0) if pending else self.next_key(WAIT_FIRST if i == 0 else WAIT_NEXT)
            if key == KEY_EXIT and KEY_EXIT not in seg:
                return "exit"
            if key == KEY_SHOW and KEY_SHOW not in seg:
                return "show"
            if key is None or key != seg[i]:
                if key is not None and i > 0 and key == seg[0]:
                    i, tried = 1, False      # starting over from the top is a new try, not a mistake
                    continue
                if key is not None:          # no key at all is a pause, not a mistake: it is not counted
                    piece = self.piece or UI.get("title", "").split(" · ")[0]
                    self.mistakes[piece] = self.mistakes.get(piece, 0) + 1
                clean = False
                if key is not None and not tried:
                    tried = True
                    ui(msg=tr("Not that one. Try again"))
                    log(f"Not that one, one more try at note {i + 1}")
                    continue
                why = "Stuck" if key is None else "Not that one"
                ui(msg=tr(why + ". Note {i} is {key}", i=i + 1, key=label(seg[i])))
                log(f"{why}: note {i + 1} is {label(seg[i])}")
                self.pause(PAUSE_AFTER)
                self.drain()
                return False
            i, tried = i + 1, False
            if UI.get("msg", "") == tr("Not that one. Try again"):
                ui(msg="")
        time.sleep(0.3)
        return True if clean else "fixed"

    def with_numbers(self, seg, start, title):
        """Numbers on the screen and the piano plays it first. Repeated until it comes out right."""
        while True:
            ui(title=tr("{title} · with numbers", title=title), seq=[label(n) for n in seg], midi=list(seg),
               show=True, msg="", fing=self.fingers(start, len(seg)))
            self.count_in()
            early = self.play(seg, start=start)
            ok = self.answer(seg, early)
            if ok == "exit":
                raise Stop("keys")
            if ok is True or ok == "fixed":
                ui(mode="ok", msg=tr("Right"))
                self.pause(PAUSE_AFTER)
                self.drain()
                return

    def without_numbers(self, seg, title):
        """Nothing on the screen and no demo: straight from memory. One try."""
        ui(fing=[], title=tr("{title} · without numbers", title=title), seq=[label(n) for n in seg], midi=list(seg),
           show=False, msg=tr("From memory"))
        self.count_in()
        ok = self.answer(seg)
        if ok == "exit":
            raise Stop("keys")
        if ok is True:
            ui(mode="ok", msg=tr("Right, from memory"))
            self.pause(PAUSE_AFTER)
            self.drain()
        return ok is True

    def keys_only(self, seg, title):
        """The middle step: the key to play is lit, with no number and no demo. One try."""
        ui(fing=[], title=tr("{title} · keys lit", title=title), seq=[label(n) for n in seg], midi=list(seg),
           show="keys", msg="")
        self.count_in()
        ok = self.answer(seg)
        if ok == "exit":
            raise Stop("keys")
        if ok is True:
            ui(mode="ok", msg=tr("Right"))
            self.pause(PAUSE_AFTER)
            self.drain()
        return ok is True

    def cycle(self, seg, start, title):
        """Your method, with the help taken away in three steps: numbers and a demo, then only
        the key lit, then from memory. A slip from memory goes back one step, not to the start;
        a slip with the keys lit goes back to the numbers. Done when a round from memory is clean.

        Returns how many rounds from memory failed. With auto tempo, two failures in a row
        slow the demo down a step.
        """
        level, fails, in_row = "numbers", 0, 0
        while True:
            if level == "numbers":
                self.with_numbers(seg, start, title)
                level = "keys"
                continue
            ok = self.keys_only(seg, title) if level == "keys" else self.without_numbers(seg, title)
            if ok:
                if level == "memory":
                    return fails
                level, in_row = "memory", 0
                continue
            fails += 1
            in_row += 1
            level = "numbers" if level == "keys" else "keys"
            if in_row >= 2 and self.auto_tempo and self.speed > SPEED_MIN:
                self.speed = max(SPEED_MIN, self.speed - SPEED_STEP)
                ui(msg=tr("A little slower: {speed}%", speed=self.speed))
                log(f"Two slips in a row, tempo down to {self.speed}%")
                in_row = 0

    def lesson(self, chat, args):
        global RECORDER
        if chat:
            try:
                LAST_CHAT_FILE.write_text(chat)
            except OSError:
                pass
        else:                                   # started from the screen
            try:
                chat = LAST_CHAT_FILE.read_text().strip()
            except OSError:
                chat = ""
        self.chat = chat
        m = re.match(r"^(.*?)(?:\s+(reset))?$", args.strip())
        name, reset = (m.group(1).strip(), bool(m.group(2))) if m else (args.strip(), False)
        if not name:
            self.reply(tr("❌ A song name is needed. Example: /lesson ode_to_joy"))
            return
        if FLAG_ALARM.exists():
            self.reply(tr("⏰ The alarm is playing, the lesson did not start"))
            return
        song, notes, rhythm, starts, source, shift = load_song(name)
        if not notes:
            self.reply(tr("❌ No song named: {name}", name=name))
            return
        progress = load_progress()
        rec = progress.get(song)
        if not isinstance(rec, dict) or reset:
            rec = {}
        progress[song] = rec
        groups = phrases(notes, rhythm)
        settings = load_settings()
        self.auto_tempo = settings["auto_tempo"]
        now = time.time()
        parts = ensure_parts(rec, len(groups), settings["speed"], now)
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if rec.get("day") != today:
            rec["day"], rec["new_today"] = today, 0
        plan = song_plan(rec, len(groups), settings, now)
        learned = len(plan["known"])
        if reset:
            save_progress(progress)
            try:
                forget_history(song)
            except OSError as e:
                log(f"The history of {song} was not cleared: {e}")
            self.reply(tr("🔄 {song} starts over", song=song))
        if LIVE_OUT is not None:
            LIVE_OUT.close()                   # the screen's keys give the output back
        if self.unplugged.is_set() or not self.open_ports():
            self.reply(tr("🔌 The keyboard is not connected, the lesson did not start"))
            return

        self.rhythm, self.starts = rhythm, starts
        try:
            self.hands = plan_hands(notes)
        except Exception as e:                 # without a plan the lesson runs on numbers alone
            log(f"The hand plan failed: {e}")
            self.hands = None
        self.prepare_band(song, find_midi(song) or find_midi(name), shift)
        self.stop_event.clear()
        self.last_key_time = time.time()
        FLAG_GAME.write_text(str(os.getpid()))
        self.status("lesson", song)
        self.mistakes = {}
        self.piece = None
        self.run_detail = None
        began = time.time()
        ui(summary=None, replay=None)
        RECORDER = Recorder(song)
        RECORDER.start()
        due, new = plan["due"], plan["new"]
        items = []
        if learned:
            items.append(tr("everything learned so far, on your own and then with the band" if self.band
                            else "everything learned so far, on your own"))
        if new:
            items.append(trn(len(new), "{n} new part", "{n} new parts"))
        if learned or new:
            items.append(tr("all of it again at the end, with today's part in it"))
        if due:
            items.append(trn(len(due), "{n} part is not steady yet, and the run at the start is what settles it",
                             "{n} parts are not steady yet, and the run at the start is what settles them"))
        where = ""
        if self.hands:
            l, r = self.hands["opening"]
            where = tr("\n\n✋ Starting position:\n• Left: {l}-{l4} (thumb on {lt})\n• Right: {r}-{r4} (thumb on {rt})",
                       l=l, l4=l + 4, lt=note_name(thumb_key('L', l)), r=r, r4=r + 4, rt=note_name(thumb_key('R', r)))
        today_txt = "\n".join(f"• {item}" for item in items)
        self.reply(tr("🎹 Piano lesson: {song}\n📚 Learned: {learned} of the song's {total} parts\n\n"
                      "Today:\n{today}{where}\n\n⌨️ Keys:\n{show} = show the numbers again\n{exit} = leave the lesson",
                      song=song, learned=learned, total=len(groups), today=today_txt, where=where,
                      show=label(KEY_SHOW), exit=label(KEY_EXIT)))
        ui(song=song, pos=learned, total=len(groups), msg="")
        done_new, fixed, score, stopped = 0, [], None, False
        reviewed = 0
        run_before = run_after = None   # the two measured runs: before the new parts, and after them

        base = base_speed(settings)
        log(f"Tempo: {'adapts, starting from the pace' if self.auto_tempo else 'fixed'} at {base}%")

        def part_speed(k):
            if not self.auto_tempo:
                return base
            return clamp_speed(base + parts.get(str(k), {}).get("adj", 0))

        def keep_speed(k, fails):
            if not self.auto_tempo:
                return
            sp = self.speed
            if fails == 0:
                sp = min(SPEED_MAX, sp + SPEED_STEP)   # clean at the first try: a little faster next time
            parts[str(k)]["adj"] = sp - base

        def measure(upto, first):
            """One pass of the song so far: the player alone, then the same with the band.

            The run without the band is the one that is read. The band does not lead the
            player, but the chords under the melody are themselves a reminder of what comes
            next, so a part is measured where nothing is helping it. The run with the band
            follows right after, for the music, and is the one that gets the score.

            Only the run at the start of the lesson moves a part's count. It is the honest
            one: the part was last played a day or a week ago and nothing has warmed it up.
            The run at the end comes minutes after practising the same notes, so it says
            how the lesson went, not whether the part will still be there tomorrow.
            """
            nonlocal score
            end = groups[upto - 1][-1] + 1
            adjs = sorted(parts[k].get("adj", 0) for k in parts)
            self.speed = clamp_speed(base + adjs[len(adjs) // 2]) if self.auto_tempo else base
            alone = tr("▶ Everything learned so far, on your own") if first else \
                tr("▶ Everything, with today's part, on your own")
            self.run_with_band(notes[:end], band=False, title=alone)
            slips, reached = set(self.run_slips), self.run_reached
            judged = [k for k, g in enumerate(groups[:upto]) if g[-1] < reached]
            slipped = [k for k in judged if any(j in slips for j in groups[k])]
            clean = len(judged) - len(slipped)
            if first:
                for k in judged:
                    schedule(parts[str(k)], k not in slipped, time.time())
                save_progress(progress)
            if judged:
                self.reply(tr("On your own: {clean} of {n} parts clean", clean=clean, n=len(judged)))
            if self.band:
                # a breath between the two runs: the next one used to start the moment this
                # one ended, with nothing on the screen to say what had just happened
                if judged:
                    ui(msg=tr("{clean} of {n} parts clean. The same again, with the band",
                              clean=clean, n=len(judged)))
                self.pause(BEFORE_BAND_RUN)
                self.drain()                 # a key pressed in the breath is not part of the next run
                with_band = tr("🎻 The same with the band") if first else \
                    tr("🎻 Everything in one go, with the band")
                score = self.run_with_band(notes[:end], band=True, title=with_band)
            if first:
                ui(msg="")
                self.pause(BETWEEN_RUNS)
            else:
                # the run that ends the lesson: a short, named pause, so the screen does not look stuck
                ui(msg=tr("The full run is done. Well done"))
                self.pause(END_RUN_PAUSE)
            self.drain()
            return {"parts": len(judged), "clean": clean, "slipped": slipped}

        try:
            # 1. what is still there from the last lessons, before anything new is added
            run_before = measure(learned, True) if learned else None
            for k in (run_before or {}).get("slipped", [])[:DRILL_AFTER_RUN]:
                fixed.append(k + 1)
                self.speed = part_speed(k)
                fails = self.cycle([notes[j] for j in groups[k]], groups[k][0], tr("🔁 Part {k}", k=k + 1))
                keep_speed(k, fails)
                save_progress(progress)
            # 2 and 3. new parts, each one connected to the one before it
            for k in new:
                g = groups[k]
                seg = [notes[j] for j in g]
                fing = self.fingers(g[0], len(seg))
                self.reply(tr("🔹 Part {k} of {n}: {keys}", k=k + 1, n=len(groups), keys=labels(seg))
                           + (tr("\nSuggested fingers: {fingers}", fingers=', '.join(fing)) if fing else ""))
                self.speed = part_speed(k - 1) if k > 0 else base
                self.piece = tr("🔁 Part {k}", k=k + 1)
                fails = self.cycle(seg, g[0], tr("🔹 Part {k} of {n}", k=k + 1, n=len(groups)))
                self.piece = None                # what follows joins two parts, and is its own row
                if k > 0:
                    both = groups[k - 1] + g
                    pair = [notes[j] for j in both]
                    if not self.without_numbers(pair, tr("🔗 Parts {a} and {b}", a=k, b=k + 1)):
                        self.cycle(pair, both[0], tr("🔗 Parts {a} and {b}", a=k, b=k + 1))
                parts[str(k)] = new_part(time.time())
                keep_speed(k, fails)
                learned += 1
                done_new += 1
                rec["new_today"] = rec.get("new_today", 0) + 1
                rec["learned"] = learned
                save_progress(progress)
                ui(pos=learned)
            # 4. the same song again, now with what was learned today in it. A melody is
            #    remembered forward, from its first note, so a part is judged where it is
            #    really played, in order, and never as a piece pulled out of the middle.
            run_after = measure(learned, False) if learned else None
            reviewed = (run_before or run_after or {}).get("parts", 0)
            for k in (run_after or {}).get("slipped", [])[:DRILL_AFTER_RUN]:
                if k + 1 in fixed:
                    continue                 # already practised before the new parts
                fixed.append(k + 1)
                self.speed = part_speed(k)
                fails = self.cycle([notes[j] for j in groups[k]], groups[k][0], tr("🔁 Part {k}", k=k + 1))
                keep_speed(k, fails)
                save_progress(progress)
        except Stop as s:
            stopped = s.reason
            self.reply(tr({"alarm": "⏰ The alarm took over, the lesson stopped",
                           "stop": "⏹ The lesson stopped",
                           "keys": "⏹ Left the lesson",
                           "idle": "⌛ Nothing was played, the lesson closed",
                           "unplugged": "🔌 The keyboard disconnected, the lesson stopped. Progress is saved"}[s.reason]))
        except (OSError, IOError) as e:
            stopped = "unplugged"
            self.reply(tr("❌ The keyboard disconnected in the middle: {e}", e=e))
        finally:
            rec_id = None
            if RECORDER is not None:
                RECORDER.stop()
                rec_id = RECORDER.save()
                RECORDER = None
            secs = int(time.time() - began)
            try:
                add_history({"song": song, "start": int(began), "secs": secs, "new": done_new,
                             "fixed": len(fixed), "score": score, "learned": learned,
                             "total": len(groups), "mistakes": sum(self.mistakes.values()),
                             "reviewed": reviewed, "run": self.run_detail,
                             "stopped": stopped or None})
            except OSError as e:
                log(f"The history was not saved: {e}")
            after = song_plan(dict(rec), len(groups), settings, time.time())
            ui(mode="idle", song="", title="", seq=[], midi=[], demo=-1, expect=-1,
               pos=0, total=0, msg="", fing=[],
               summary={"id": int(time.time()), "song": song, "learned": learned, "total": len(groups),
                        "new": done_new, "new_today": rec.get("new_today", done_new),
                        "fixed": fixed, "score": score, "secs": secs,
                        "reviewed": reviewed, "run": self.run_detail,
                        "run_before": run_before and {k: v for k, v in run_before.items() if k != "slipped"},
                        "run_after": run_after and {k: v for k, v in run_after.items() if k != "slipped"},
                        "band": bool(self.band), "states": after["states"],
                        "mistakes": self.mistakes, "stopped": stopped or None, "recording": rec_id,
                        "complete": learned >= len(groups) and not stopped})
            rec["learned"] = learned
            rec["last"] = int(time.time())
            if score is not None:
                rec["score"] = score
                self.pub("piano/game/score", str(score), retain=True)
            try:
                save_progress(progress)
            except OSError as e:
                log(f"Saving failed: {e}")
            FLAG_GAME.unlink(missing_ok=True)
            self.close_ports()
            self.status("idle")
            self.publish_practice()             # after the save: the parts due follow the new schedule
        if stopped:
            return                      # the stop message already said it all; a summary would mislead
        lines = [tr("🏁 {song}: {learned} of the song's {total} parts learned", song=song, learned=learned, total=len(groups))]
        if run_before:
            lines.append(tr("At the start, on your own: {clean} of {n} parts clean",
                            clean=run_before["clean"], n=run_before["parts"]))
        if run_after:
            lines.append(tr("At the end, on your own: {clean} of {n} parts clean",
                            clean=run_after["clean"], n=run_after["parts"]))
        if done_new:
            lines.append(tr("New parts in this lesson: {n}", n=done_new))
        if fixed:
            lines.append(tr("Slipped and practiced again: parts {parts}", parts=', '.join(map(str, fixed))))
        if score is not None:
            d = self.run_detail or {}
            extra_txt = tr(", rhythm {rhythm}%", rhythm=d['rhythm']) if d.get("rhythm") is not None else ""
            lines.append(tr("In one go with the band: notes {score}%" if self.band else "In one go: notes {score}%",
                            score=score) + extra_txt)
        if learned >= len(groups):
            lines.append(tr("🎉 The whole song is learned. Every lesson from now on plays it through and works on the weak parts"))
        elif done_new:
            lines.append(tr("The next lesson can start right away, with the next parts"))
        self.reply("\n".join(lines))

    def list_lessons(self, chat):
        """Every song there is: the shortcuts and every MIDI file in the folder.

        Songs already started come first with how far they got; the rest are listed by name
        only, on as few lines as possible.
        """
        progress = load_progress()
        names = list(load_shortcuts())
        taken = {n.lower().replace("_", " ") for n in names}
        for f in sorted(MIDI_DIR.glob("*.mid"), key=lambda f: f.stem.lower()):
            if f.stem.lower().replace("_", " ") not in taken:
                names.append(f.stem)
                taken.add(f.stem.lower().replace("_", " "))
        started, fresh = [], []
        for name in names:
            rec = progress.get(name)
            if isinstance(rec, dict) and rec.get("learned"):
                song, notes, rhythm, *_ = load_song(name)
                total = len(phrases(notes, rhythm)) if notes else "?"
                started.append(tr("• {name}: {learned} of {total} parts learned", name=name, learned=rec['learned'], total=total))
            else:
                fresh.append(name)
        lines = [tr("🎹 {n} songs", n=len(names))]
        if started:
            lines += [tr("\nIn progress:")] + started
        if fresh:
            lines += [tr("\nNot started yet:"), " · ".join(fresh)]
        lines.append(tr("\nTo start: /lesson <name>"))
        self.reply("\n".join(lines), chat)

    def run(self):
        log("Lesson engine started")
        faulthandler.register(signal.SIGUSR1, all_threads=True)
        global LIVE_OUT, ENGINE
        LIVE_OUT = LiveOut(self)
        ENGINE = self
        ui(volume=load_volume())
        if code_on():
            log(f"Lesson screen code: {screen_code()}  (Telegram: /pianocode)")
        start_ui()
        threading.Thread(target=self.watchdog, daemon=True).start()
        threading.Thread(target=self.practice_clock, daemon=True).start()
        FLAG_GAME.unlink(missing_ok=True)
        self.mq.connect_async(MQTT_HOST, MQTT_PORT)
        self.mq.loop_start()
        while True:
            action, chat, args = self.cmds.get()
            try:
                if action == "lesson":
                    self.lesson(chat, args)
                elif action == "lessons":
                    self.list_lessons(chat)
                elif action == "getmidi":
                    self.get_midi(chat, args)
            except Exception as e:
                log(f"Command {action} failed: {e!r}")
                self.reply(tr("❌ {action} did not go through. The log has the details", action=action), chat)


PAGE = """<!DOCTYPE html>
<html lang="en" data-atop="yes">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Armonico</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%20120%20120%22%20width%3D%22120%22%20height%3D%22120%22%3E%3Cdefs%3E%3ClinearGradient%20id%3D%22ailight%22%20gradientUnits%3D%22userSpaceOnUse%22%20x1%3D%2212%22%20y1%3D%2217%22%20x2%3D%22108%22%20y2%3D%2258%22%3E%3Cstop%20offset%3D%220%22%20stop-color%3D%22%238AD6FF%22%2F%3E%3Cstop%20offset%3D%221%22%20stop-color%3D%22%231D6BE0%22%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Cpath%20d%3D%22M4%2050%20L60%203%20L116%2050%22%20fill%3D%22none%22%20stroke%3D%22%23E2B04A%22%20stroke-width%3D%225%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3Cpath%20d%3D%22M21%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M41.85%2060h17.25v40a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23E2B04A%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M62.7%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M83.55%2060h17.25v46a5%205%200%200%201-5%205h-7.25a5%205%200%200%201-5-5z%22%20fill%3D%22%23EEF6FF%22%20stroke%3D%22%230E2A47%22%20stroke-width%3D%222.4%22%2F%3E%3Cpath%20d%3D%22M34.05%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M54.9%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M75.75%2060h12v26a3%203%200%200%201-3%203h-5a3%203%200%200%201-3-3z%22%20fill%3D%22%230B1A30%22%2F%3E%3Cpath%20d%3D%22M12%2058%20L60%2017%20L108%2058%22%20fill%3D%22none%22%20stroke%3D%22url%28%23ailight%29%22%20stroke-width%3D%2210%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E">
<link rel="apple-touch-icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAAvB0lEQVR4nO19eYBcVbF+1Tl37WW2ZLLv+waBgIgoIAKKgiyKyCYgKoqoD4HA++lzR58mIIsPUPCBiuKCyqI8FFlkR5A9K0nISrbJ7L3c7VT9/ri3e7p7pieTpJOZTvpL/ujp7jlz7r3frfNVnaq62J0NoIYa+oIY7AnUMHRRI0cNZVEjRw1lUSNHDWVRI0cNZVEjRw1lUSNHDWVRI0cNZVEjRw1lUSNHDWVRI0cNZaEN9gQGH8wKUZLXSW2vAqJoOlTodeGbgz21QcaBTQ5mBkKU3o7X1KpbpNfKwL7erM28TB82n1khCEAc7FkOGvAA3pVlAABAb+PD/qrbBXLWZwCwdUEM2vRLzPEfzn9nEGc5iDhgycHhJXfeulPf/lBXOmBg7PkA6+Ka13yyPfPiwi8faDgQycGkUEj2M86ym+z0y+0pxUSI3PMFRhSiMSGdxOHm7K+gHgt/ZRDnPCg48MjBClCq9BZ36SLb39CeIgQVfQIIABgtJcAgGxMiq0+w5l0jYqPCXxy0aQ8GDjBysAKUftsSf9n1GnWmMowYMYMYJAIgKAKRW0OYZSKGgajX51ypN8070Phx4JCDmRlRuO88plbfxipwfBY5I0EAli5cnxDR1NDxGXs+QktHlJqcdqk59nhmQsQDRIIcIOTIyc/Vv9a2/DmVJcUkej6TMRNA2nLuNQAyWPpDQem0A/nlhgA0FHFbqNEfM6edXzjg/o0DgBxMgIKU6y6/2e76V3vKZ4K8/CSQDXHhyZH63Ku1ukkAEHRvCJYtNoLN7SkSeTnCiAIaE3o2+W5zzleENMNhB+uY9g32c3KEgU6VbfGWLra8Ne3dBfKTAVA2JWXWnmPOvVIY9cwKABAled3O0h/Hsm+2dyvmHkeGQTYmhWNMNeYulHbzfh9F3a/JwQpQBh0rvGWLddXeleG8JSAGIbAhoTn1x1mzL0Uh85aAmRAFM7kr7jDbHulMBwSc93MJZH0MPdlozFmoNczavyXq/koOBmZA4W15Olj5E2Av6xXJT12gaQiYcL45+eMADAz5lQYhDKszonDXP0Bv/yJQ5CnOLyEEaBsCUNdmfMkYcwwwwX4qUfdHcnAUsHDX/B7f+X3WVQEVXdqYIUCYctZXjJFHARMDEqMQPZdXEQhkCLddtr8YLL9RcDbjFktUgbYpefzZ5uSz8vTa54e6d7HfkSOSn4G74n/szqfb04oUFcYt6uLoa8O1OQv1+unAilHmL6mnAJgNLXqDATBcmLre9pcsMqilM0WFcREhRWNcZhuONWdeKqS+/0nU/YscrAAlue3uksW2u7KtmwBUfscEQDYlRdaYYcy7SlrDgBWBFAhuwM9s5De2c0sqAKIRdca8Zjh6PJq6IAYBClAqt91dcn3MXdHWrXqPmTZnWvOukmbTfiZB9iNyhNHPzjX+skVmsKMjTQILHFEJjXEtW3e0Ofuy8C5XICTC+g76zVJuIyNwSQU+M6DUpS6GSe/8g3hyo6YYJBCgYFLOilusjic7UgER560RcegMN+tzF2r1U/cnfuwf5GAmQiG9bc+plT9B5WS8Un0QM6Uad5Y15ezo2yykgNe3qt8sA5815aQVMxCwQCRmYDQSGvgXHYyHjtEUgcAoMOqsvVdsuMfxyC/QMWEYjdDUZ12ujzySSaEQ+4FErX5y5JwFd/19vPZXnl902QjQ1hGEoc28zBh9LDAxCmBAhH+uDR5cK5XnMwXEyAzMzAwQKlomFhpK42Mz1InTdWJABGQCFN6WZ4JVN4Mq8oAYURegawImXWBNOiMfrR+cc1IhVDc5opgEKXfFz8z2xzrSxQYfZJ2Nvmg05i3UGmYxKwYhEAHgvhXquRbDTac5UIyCmUOHgxmYI+eDSYEQml139KjsOfMNACBmAQQo/c6VwdLrdNXamWZRJFGxIaa5w04wZ34+DJZUNT+qmRyh/PQ63SU/tp0lbd0EvaKZWX2KMXehFhvRIz99+s0yXpEyvO4UKWZAhpzNiPjBzAihESEGBC1eP6c++7nDdFPHHonq7HCXLLa91e2FspcRUDQlZdaeZ869Qhh1VS1BqpYcoZPZvc5bsshU2wqdzKJ9kNlfEZoJTARCILRl6Rdv8lbPcLu7GcKlBJghCnv18Tr6QYs3jLGcLx4hhselIpAYOsyet/wnVtdz7SnFxD28ZFmfEK42yph7tZacWL38qEJy5LOCW/6tVtyIlM4U7KAygEBM2DIYfYY17VMAwBzGymF9B/1qKXQp3U93E4jQNuR4ULKs5D+K3gcmadUlteDSd8G04ZoikCK307vmN9rmP6WySnGRRI1boDCuzf6q0XxYleYqVxk5mAkBAdHd8Fe1+n8Vsa965V6g1GZcaow9oScMDvDaNvWHt9ALULlZAhGuI7mlhIukRv6jHD/C10AK9Zgu4OIFcMQEnRgQGIEhzBFZdRtTUY4IA+oaCkRt2mfNCScXzKVqUE3kyOu77Irbzda/d2UCIs7fjAQyaaMSSW32Vfqwg4AVgwQARHhig3p4nRa4HpHPLEpsAzOWWVYYAIu5ogB1oZlnzlEfmWXk+EGA0m9b6i+7TqfOrmzP9h4DCIF1tuY3f9ic+dnCQ6gKVA05wv1x9tPO0hvszGttKR8K0jKifE9tvHnQNTI2Oh8XZ+b73+IX23Q3lSZFAKKXbSh5jQVLTC8TAgBEIIRmNRwzNnvh4QZEOzn5vNTFtr++KC81zFVOykzsEGv2V4URr6KN/iohR5gVnNrkLl1sBZvau4uinyCgKaFn4wusOZejHsvLT8fn3yxXa7Km25VmYgKMbEaPvGBg7FlWCglRyo8C08IsEESscW5j9otHarYRRtkJUHCQdZbdZKf+XcJdAtmYEI4+3py7UMbHVotErQZyhHHx1teDZddLSqUcLpKfgMniGhNiFAitGbp7GWwPdLcrlaNFz/UukJ9cYBsIAJjzXgzn3i95DQwsmESsaYyVvfx92oikLJKoq+7St/21O0wEyR8EyISFgUjoc67Shx1cFfwY4uTIFaVt+nuw6nYi5QVcUDqAhoZCoDbtEmP8hyM3BlAirOugX6+AlNL9TIpZFEvOPmwDMCATGDFmIDdDhVIj+hWMWJTjCgMIJjTrkpr3lfdqM0eEUXbGMFtg49/UmttJkRsU5pGgpSEKKWd8wRx74tAvpxvK5MjfiL/Qtz3YnVHERTdi3AIScTnrCqN5QVQMzSAQXttGf1wFvkLlOaFjUsZNLfJU9UT9KJEi5q0q6XV1ECOXMRs9SwwAkELN1gR8/t3yqMl6OIHQKng7XlPLrxeUSvfpaY863Zp+QeFhDkEMVXIwAQr2s87ym+3USyVZwQyyISFcOUafu1BLTggdkzCI8M916pHNhu84RAGw4AIq9G02iBFBj9dNjbufP1wC4s9e8ld2WV5XJzFzZEKwgB+RZMmZEGBQAJrQ7bMOotPnGeGwYZQ9SG30ly42g3f6zlVOvMua8xXU7CGbCDIkyRHKz8wWb8l1lr+uD/GfkFn7YHPeFUJPMClGKRCI+YFV/Eqn7qWyKtoxKYxulUoNAAy3TzU7eUSze848KUSUInjP68EzW6wg3aVUtPPSO/IRWZCQH0QohLQb3j8++5kjTYGoiAUQCkl+t7v0RjvzWnuKCosuo1xlbZIxb6EcquV0Q48cYVy8fZm3dHFJ2IAIpIZ1Mc0b/kFzxiWImJefWZ9/txLWOprTlaFoc6Q3J0r4oQA1adgnT/ZOmq5HlgGRGQTCw2/5963QAi9LymcQPXai3BITujB207ym9H8cY8ZNQRzmGiIzOytvN1sf6coEKmCRsxHEsi6GgWzQ51ylNc4ZgvwYUuSIsoLdzY+rt24FCrJ+6Z64JoWYfJE56bTcRRECYUda3fMWtgS625XObaTlbEZfagMYABRqtkQ+fx4cPlaLduSjSUDIjxc3+ne9Ap4C5WYIQ3704fTmlhhmBoGEVuPYmLvw/dqo+lyUnQEQ3fUP0tt3Bor9yHcC6Mko0OWML5pjjhtqucpDhhzM4daDu+YesfmPmWwQMJRm0whbn3W5PuIIZoUoQpuxtoN+uwoySvcz6R75Wd4ryW2UJBPCu3g+Th0mVVglW4zwzdWt6pYXqMvXVLZLsSiMdkT/epkQgQR6Mqn7Vxyrzx2th/xgDnOVXwpW3CCoV64yYsyWNOYT5tRzCk/FoGNokINzm5wrbrE6ny0tSmPZkCgoSgt36hkEwqvb1H1vY0BCeQ7vRH5Gr5FJi9eNNNzPHiKGx0WfzAgRftSSUj95PngnGwtSbYqxQHmUBkLyKhVYgWZrEr74Hu3Y6UYuUSjMVV7nL11kqG0dqd5ZjHqm7ihr9peHTq7yECAH59Ij3lwc81e3dRf1REAQjUmZsWabc6+UZgOzgpxj8vhG+ucW3XccUopze2lFkrNgWcnf4FoiOTPuXniwsAyhiKXo7x4Nv+D4fNsL3pvtMZVqV4oJsEiN9vEaQ0EjdPvsg+kTCyzORdkRpXI7/KXXW87y9m7FQBitS5DLf55uzLtKWsOHggQZbHJEWcGr/KWLDNXalS7uiSBFfUy6TceZM6OitDAuTgz3r6Y3U7rbnVWKGRCKbEZhgDxHF1IghG4njmp2zpyrw4DDC/mv3f2y9/gG28+0kyIGUbCsFAbHergCRIBC2A3HTXS+eLQlBOYTQZiUu/I2s/2JklzlsHLCk8OMeVdrddMHnR+DSI5Ifnrbng1W/ATJzXjUT1EaMxMIiZDx6Q+rYL2nu51ZFXqkfcjPQg8WmRUKXWj6R6eo46dozAC4C6ovvPaI8H/LvN8tEcp3SXnMolCKFgVPC1SqQEZr2MHDs1cebyYtQQwIuVzldX/E9b9xvdKk15ghWJjarC8bI987uBJ1kMjBUVGas/aPuKH0BDFI24CSExTKz5Y0/W41tinN7c707JjsRH4qocc0pPPmwiGjZNibZVcFHzMQsxT44nrvpy+yG4RRdtHLcylQqcDAGAXErMYxcfdrJ+jjmvScC8OAwt/+nL/8ZiQ3W5wuH94YPOE8a/KZ0WEMhkQdDHJwvgzkNquj1LQSy/q48LVh2pyr9fppwIpBMKBAWN1O976NDml+JlOcypW3EyWvAZmknUwI9+KDxcQGEYW3dxeRC7MjuPHpoN0zyOlUJAqWFWbGkuUm5CgCgZ5I6sHVxxvzxxvRLkyUq7zKX7LIoF65ymGdd8Nx1qyiOu99iX1NjqgnQk6UtXUrYJW/KzgUZeZMY95V0mxiVoAyjDq8vI3+sgFVgOS7VCg/c0Hu3iYEmPREcrThffpgbLT7c0wGDkUgBbSk1I1PB+u7rSDTpgh7y9KeueWWGGACaUrELx+rnzDbpNB0hhLVafWWXme5b/XZIaJQjO/jRJB9S47Qnet8219+nRFs7UgX9kRA0asojUGE4uCxDerZVsNLO0Q5+VnghvQR6SJGRD0Rn53wzp2LpranNqMQ4VCOT7c8473SEgsybRRwtJELULysQAFdGICAJRrxcw+l846wo2gbhG687664xe58uj0VUFTGDRD1lkFPG6XP6XHjK3MYA8C+IgeHoUvhb3/RL1+0TmPPsqaeHV7k0DEJiB9YCysymtPlFFYSlJcaGGoUzY4f1eyeMUuDATsmu3Y0CADwy385f3vb8rMdrBSDLF5iioKn0Y95F2aS89XjYzLnwjAgIjprfivfuTdT3BagJAAIQMC7Lpp2C/uCHGESDaJw1z9Ib98VKPIVFGYF2wYC9hSl5eVnyqN71+DmQHM6s8TAPTk7fUS3gJkBo0o1IU+bSsdM1MK/sTdOZI8Ls9T9xctMgc/KjyrnSk1aqSclkNFsmtvsfP0kqyEmFbEUEPluW54MVt4C5GVLcpUlaFKIKZ82J56aP5974bCKsPfJEclPclfdYewo3XxilskY+qLRmLtQa5xVGP3cmqJ712Gn0tzuLAEWmoqS6Fb+NbJCI2YKOncmzx0pw3V9791jzEAMUsBL67ybn1HZAJWbzofj+l5i8oFaIDQbxsTcb55sThpuhFImivq0L/eXXaer9u7CTpgFm47WjEsAcR/kKu9dckQ5OH63s6Rsi63CorQ8M95qp/vXCYdlkM1SLpWrWGr0uAN5+anFEnXSu3AOjquvpMjoH+F1Xd0SXPeEv8PRe1yYvlVqzyEIJJbxuK6+fpJ1+KQifqjsdnfJIttbW5qugKIxKTP2Qda8K4We2NsSdW+SIypK2+gt/ZGltpRkBfcuSgszMATCS9v4b5tQKabAJxb92GfoOelkJBJjDO+CuVhv7TtmhAiva2taLXrMW9NpqUxbQOVMXdGPwATCQBSXH6efPN8mCjeHo/aH3rKbrO4XSxOdWDYkhauN0edcrSXH71WJunfIESpzlF7LK2rFDWVT5Uafbk27AAByWRkAAI9upBc7dC/tKUWQcwFKTEXReY8ck9ishHfObKFL3MfMCBH+UTfgm//pvLDZ9tOtTMD5XFTI77lwiQkBYCBEM3nOofSZo2PhmwIpfE6Ss+qX2tYHUtk+UyQTctZXjeYFwAr2Tjnd3iBHFNFzN/6fWnMHKS7MCo6K0oSU0y81x55QKD99ggfX8SpHc7scUkCIJaai2GvNreQodMt+7wj3lGkaFPgR+x75P33n85m/rDADp4tYAeVy2YGj+uxeVhCIAFFYTcdNdhaeFDM0kdvoZ0ThbvqHWvVTJuUERRI1TK6W0z5njv9IqGIqrrwrTI6eorS37jS3P9RZ8KgKAGCQiYKitMKs4G6P/7gWtinN6XTKy88iriATSB0RT5tCR40vStgZLIQhFiHwoSXOz55VREHowvRl9kodcoEMZtOcYc53T481xUMXBsNT5Le9GSy7TlJ3KltUloGA9XHNzZVlVFyiVpIcOfmZcZfdaKdf7p012ZAQjjbenHe1jI8pckzS/Kf12EXS7XbyqVy9JWchV4BJmDET/bNn4uzhYm87JgNH3oV5cZ13/eNB2gf2M4qKAnc9wbr869DaMoFZPzrhf+9Ua9rIYomafsddssgKNnX0mVEbX2DOvlwYscpK1MqRIzqGze7SRZa/scwxHGLNuSL/+JKcY8IPbkSXZZAtsBmlZqNYxxHp8XiD8M+fDaOT+1p+DgThdX27xf/BI97WtEFuRxhlL6OceqiDQCxjcZ2/eYr1nmlmOE50ury0u/wGO/1ayQNieu66XClopSRqZchRav0KHPS89fOaP2LN/AyEMTEUAIAA/97Oj2xBFTAHivrSa7nX+aJFRmYjGR+re+fNxqRZmR2TvYHwural1X//zVneZqlsW7/86InjAROjLqS84njz9MMiFwYgWjLclT/XWx7uyhYVkYeP/ihZr/f8EPacHJFucjb9g1b9lFmVtiGQYVFa1IYAEPKS8dFN/EpKc7s9Usz9yc+c2SBGAXoiNifufWKm0MTgOCYDRzg9L6AfP+Y8vd4KMq1MXJzoCn28DlNUQLCRPGdBcNnxydxQOaW//q/Bmv+l/ttPVCIRZA/JwbmitF9pW+8v43FFRWmFIsNT/NcN8LavOV2eUtzLZS25tyKqIKBm20c3ux8KE3aGhsjoH3n63vlc9t7XpXK7iVRYjlvWOc+JKkBEs+nYKdlvnJq0dFEoQbyWl4PlN0hKp90+YwRR45r8Bdo97AE5wqzgwHWX32x3l3lUhT5an3N1WJSWZ0aXx/etxxaW2U43qlrOn5ReGo2ZIdxL0wyBcNpkOmKMHAqOycARXmgh4KE3nJuf9EgRBx5FaQY9d0UvlQrMjMhgNs5tdn9wZrw5qUVJr1F0cb2/dJERbO1Il3n0Ry66uNuJILtLjnB+me3+0sWW93YfUd46mbXnmXOuEEZdocu6JQP3bYA0SzfllpGfXJyfAUAkrZiF/ienw/SmoSg/dwpmJgYp8KV13g/+5qZcwUFaEZYETAtjvlEsJ3RhjPqRcf+HZ8ZmjTECxZrE3IOSu9ylP7adJe1dvfYlEiJrTDHnLZT2iN2WqLtFDiZA0ff+UK7dojPsBGvm5xFFZGBCx6SD//oOeiR914WeDcz+Il1IrCdiDeifNwtGxKuSGXmE68LaluBbf81uSRnkdiiFPUfdy2mPfgxdGBGzDf7uafYxs6zc+pLLVX7rdrP10c50oIpzlaMdzXnhoz92x37sBjkYAN0tT6qVt/TxqAqJhiZg0oXWpNOj65xzTF7azk+0SOWTClSRbShcd3t0e3S6zGRsnO6fPQPixtB1TAaOcF1oz6jv/iX7ZotF2dYcPwoX1pJFloERkQh0QHnlB82z3xOP4jqQa9C77n5e90svIL/00R8IaMgZl5ljjt0N/bGL5GAK+6Npa2/tzqjeT0ojtLRZ/2GMPDLsn8cYScbH3uHXM5rT7SsVPuomv+NQRn4SC4F63Jqb8M+YKqSAqrYZhSACIcBXvPjvmcfWGCrTzooIC+tuuE8TEvZPBqPunMPUFR+ug9BOA4e9Ff1tL/grb0JysqXldCIRk8Hky8yxH9hV+7GrloNBue1Pfs6AlBtQT80ny4YEeqJZn3t1vjl8SFRPwUObYL2S2U4/l8qVtxllfDliFEJa5rEj/A9MCJ+eVAWOycCRj+fe+Uz67pcEOSlmRbm+qIVLTMmPCMQAaDYdO8W99uN1tlnkwgRda7wli0xqKcy/ZERTEz4mGo6+AzRzl4zHrq5DSE4r+p2up0L1w4wMclid8OzZ5uE/zDMjPP5Oj3+/HtYHMtvpEUPY9iYymNGrSIKFYwEgAAvD0HR52nj/AxNEFDfcj5gBACJcEggufl984QkodQOkiUyhLc0b0RAM+ddMLIiQs21PrDY+d1fn1o5AClAEgBJYaXVTzcN+6MdmD0sKBhlGFZDB9RR7ncpt3dVlZRfJwYxmE+r1tilYCAaJAhqTwmk41jr0O8JoBKa8y7o5A79djy2BzHZ5YU+EAh7kXxfSBZFIs+y4ThfOhAUjRXW5rLsEBBACFMFHDrIWn2knbQQ9gdjTRSxvMnKv82+DYgSnbekWedEdXUs2eFJAEPGDpNlozP92tv7YpqRAAQyShbBNKfR6YTblRxnoJHdPc+CaWzSJCOD6hBPPyT3KivOOycoO/ts24ZPwHa8n97NfqQHMRsJuRP/s6Tg8NtSjn5VCuC6s3+F/7b7Mhk6DnA6ini4SHJnn3ioVEBSLmKXD9z9un3BQLCDWBOYDo86a38PG35m6YIBAMU+9zBx7/N7WHBDuC/vty6nlOWCSI96nNc6OZo0YLgwvtfDTbVrgKeUrKJCfZb1WYgQw6qzx0j9rBtragcKMEKEL05lV37w//fI7FmVbFQFw315MuPLkJSqhDsJYeJJxwTGJnJQJzQMGHctp2zOMQo54r9Ywazc29Hc/ztHzU/FffXQLLnNFKD8B8+mfpeHhSGxw1DNJj5sHJYJTJyPi/uOYDByhCxMoWvRw5qFlOjntRMQFTet6BT/yHg0BCDDqzz5cff30esg5rKVU2C1Jv5sRUuZ87wAMJ8EAAcFDm2ATy0xnUFR91LOUcNFrCJ/NKDTTOHaEf+y4/dAxGTiIGRER4K6n07c/w8pLIwVh2IKZCyhSusQAMAKw0fj+ad7ic+stPaoE632NdhUV2LLn6Njgz2thuy4zbX7eKyvwWkuD4qFVRN2QQB+dyPObBQEMoZZHg4HwzAiEh9/IXPsXx1fMyiEqesBDr1hqxBUpgY1hR45P33bxMCmxIqlPFcgqYwYEeKUVWiyZbfej3ibRZ3mvFSOS595FJs224hp9agbMbw47RB/QzAAARBAIiuDDB8f+57x4Y0yilhQYNuCPvlPgueTtLwCAUiDd1n9tSt79TEogqF10TPrEnpIjXAUChld2kNcNFE6Ji5gA+SPIvQFMRtIapvkXz4SJdQeW/NwpwtDFoZPMn386Pnm4AqNRABVwIuyH2fs1KGLwun77bMoLWCLyHvND28PfD5e3Dhe7A0GkODIbHPVUyX8tP1NiBDDrrTHofXwKx03N85UQQLv796UQAKBotwcoHQ0RA6UqMppAFELs3miux+OH6T/9VPzrf0y99M5wTm8HEGFCYfiF/LZcHgSIyt3cietb/OmjDeI9XVj2lByh5cgokIakrA/hLjxgwQLS8yMToRRG3DgkySeNjzaRDb0CCW2arGRhz1AYTZMAAI0J49aLmm59ku9+ttnLtJMiBT0BU4BSfiAzaFZbOvfRnrFjT8kRktOWpHxg6EkoKJQd0SEQo5TS1I8bTtNky/W3/wOBmWm3j0AIQYCfPOUD9cn47fc8gAXmafdGUwynnfi+8WNH3nLXHwEY98Ayh6N96Ogj5s2eetPPf6cUIe7G9DisvjdEYG+1nMYPEWWRPQJZbiRG4MBviCFUQpDu8bICwABNJiZQZU3ddfz8u1xIcSZpmgLVyaO9g5vlE89tuPGuP7HyEYWUAgZy0hCIiJQqIBOjNGZMHD1+zMgbfn4vcOHKwogoBZYbGAGIgajgc2bUjBENCYSDb/j5H6BoToxCSCnLzhOBiZUKeubGjJphCB4+rO66n94DKAoGZEQhtfKjFYOYiYGcjmNOoc2JD3emdVSZ6HlTxRDAKO3R9f7kEQZzBdR9BVzZUE6+1ML/crRMq696BciRWbNNC4KPT4TxSWSG519+49zLvoWCPMfPpDMDOw4h2bXBLzpqRMsyESGbcYqOCtiVcV/WQfnrKVQmpjpLRzMNIWQmkykZzQPdQxPKSiNE9uPgc/F7pmFompZOp4tHAx+ki2b5uRWAwbDMRCLukfivL5z+gY986nM/b2nLaBikA1VqiXQBZA1feGJwwdGJKJq+Z6iA5QgFxYLh8PbaYHuTnmrzkaO7GJGZwKwz6yn4xGRotNBXpEvBzIrJy2Ynjx//ydNP8P184kffICLb1F555c0H77vfsm0qOCupVCj3et4RCI5L75qjTnwXZ13qfYqIwTLE8vV8/1PKNLDwFKeCAPKCKRoNnWz20MMPO+20j2TdQIhS/46ILVOuXr3unl/fYxhG4RULeo2GiK7jzJk39xNnneG4SvR7/YgoZsp/vvD6sy+8YtixjixOH8G//Fzyit+mV+1oxGwr5pq4hMqPrBHvGtt13nubqUJZUZUgBwIACMAzJsEj79ByU5c6UAAMgAJ8B6abwUnjwNKQC1xnRHTdYPLEsd+47Hy1M5daAWgA9/71yT/9/l47HocC/Z8/O4Xv+B4cMRm//THR3gW95aAiaIyL+/8l730CLCz5XSwdTQjfcQ+ZN+trXzw3AOitLQlAAjz/6lt3//LXJTKl92hCCN91Z0+f/PUyoxUiANABQBiPP/GsFU/oEohhYrP5y8/p1z7Q/ZfXbJQWswIAEJIC9+RZ2e+cOTzkW/832wBRAXJEQDAQThnPh6bVW924PcWIMDyGM8fx2BhCLuZf9BsIvh+kApXOcP/3kFKqMSEDpUCKEoHet8pDyPrQloL2NMhevFMEgWJPgZSlpr3MaOi6nqdUW0rJXlwjokRcOJ6v6RoVe9TlRvM831GqI0Wy9+QKECg1PCEzWSevLQWCF6iELX94dv05R/mPLnHXbldK8dTR8oS5yUMm5h5IWKGgUcXIkbeeY+MwNt4jviDnU/U5YUTUpNTkTsiBAELKWCyGKAao+AWCJkCKPsgR5lLEDJQ4QFGYnyf0QQ5EKUTMtgbusuZGw/7JAQCalCVnRmAUZZ8/QZ8/QS+aSaULhitnOXKGgXqd7wpEPxEZwLJMIcSAdNxORgNmMHUUAvfA+e0BMZimKaUMgkAIseehyf4RJkApCm1EbmsNdtLHfTdQSXKE2BuB8NAsmaYhtbIu/i4hJIcmYPfDLPm5ITKDYeiapvm+v/NfqBByVMhPv/LnffCf2zBAMIBpmpqUe35fhkEOXQNNYkXiAcxg6Lqh63vbZuxjVA85CEzTkFIy7/GeAQADGBpqsgIx5nBuuq7rhkY1cux7ICIxhDElrsT1ZGZDQ61CNVLEoBu6XrMcgwVm0HVd17WKXADmsDgPKnKvM4OuaYZu8J7vhA4lVBM5KrauIzCDJsHQKuCthOEmKaVpGhWxakMH1UQOXa+Y6WYATaKuVUyQahqapsFcmbSSIYLqIodmGHphf5jdA0LecuxqmU+5ubEmwTTNmiAdDCAyg5SaYZhMFYgPM4AUYOpl9/R3bTRmIcEyTaiEJzV0UB3kwCLTXZGbHaRAU0eqkEgQAJZlVsYzHjKoDnJAj+muDDkYQAgwdYTKaA6OyNF776CaUU3kCE03M1XAdDMIBEvHyoiOyHJYlRptiKBqyAEAAitmuhlAIFpGJZcAy7Jgf1pUqogczBW+OzGyHBUYKmSrbQ0s8696UDXkAGABkUdQkeEQoGKWgwEAbNvqI6OpmlFF5AAEsMILUKHhLKMylgMiy2FVZqwhg6ohBxdcgErdnJZesducQ+LCfrWwVA05Ckx3JbxPAKjgshJWdllWTwfn/QLVQ44e0Vcx2BUiR5ioFpKjIgMOEVQTOTjnLlZqOMuoWDu6aG41cgwOQtNtW+E+y56DASy9YvolzH+GvZ9dvC9RNeQoMN2VGZAZLKNSD1UsTI7ff1BlB1NB083Alo5QCWc2LBCwLFOICrRMGTqoJnJEphtLi952AxhVJ4DEyjifYXJ875KnqkY1kYMALMtCOdCit/4Rlq4MsP/DTlGc/7yfoHrIUVD0Vqk0UlNHWZmiN2QG0zQ0Layc2E98lqohR77obSAlqfneBP0gIscA1gFE7P96IwIzGIY5EMuBu9sVdN+jOmYJAIjoBzBm9Kh4PE5EovzVkgKYYOKI/io9EcBXPKJe1sWEov5KODUpOQgmT5ogejXgKkQQQPPwhqbGBqVUf3OTkoNg4sRxcs+aVO0bVBM5HIemjB92zic/ltm2nQHCGIUQotCBFALaO2nSWP2jR8RS2T46t+RGAy+AUY3y3GNiTqfKJ3AhYvFoom1H66iJE84+85SUz+WK4hHR91VT0vjMRed5bR2BUqGl6T1ae1t7Q3Pzp879WDaA/hsLDAVUDTkAQAhMO/y9b3z10iu+hADABMBhy6yoDTiz6/ERs8y7/mNYc53wg/4WFyGgO0tXnFb/5U/UawLyXaryozGR67gLFsz//d23Thg/wvH664gipezK8pe/8Kn/+v7XTcNgotA09IzG5DrOvLmzfnf3rbNnTMi4NPSDIpWvst97QERFAKjduvhrWzszDzz8WH1d/TVfPG94U8OV373J9Xn6CP7x+drs8bohMe1w/yc/FDGe4usvbmj1Yr9/PqiLi8suPHPuzClfuOaHjudPGjf6p4u/ecj8ubYFqfRAGhxg2lHfu+bSzqy69Y6765saLzjjQ8e857DPXnlt1nVHDG/62XXffNdhB8djsjvNcsgzA6qLHACACIEil1A3TNQNNOx3H37ohHGjhPEzUtRQx4dPl10ZzqidMCMaDUAxpLKk6RJ1HQ25YP7B7333fDQsDiDZ2HTku+c6DqTSu3CXKyJN11E30YjNO2jOice9Fw2LFceSdUe++1AE6EqpyvY53XuoAv6WIFzIc09joFQ609XVHZruQHHaYdiVHiFhl5/8aOlspqs7FS1SQZBKKWbYJftfOLdsJptKZ8MFi0ilUg5RddiMEFUz0XIQYYdQAMg1lt/j0XLnBFGIfjyPAY0mejRsONpQF6GFqHpy1LD3UCNHDWVRI0cNZVEjRw1lUSNHDWVRI0cNZVEjRw1lUSNHDWVRI0cNZVEjRw1lUfXkYIZ8W2NXAdEuly4Qg+sDIyGioes7/4UDBlVPDj8Imoc1aprQJWxsxe1dED60ZiBgAIGQcWHlVtYFK6XGjGoOgv2qXeSeoNrJwZ7n25Y5e9pky5RtabjzKWqIATMEBESgyvwPPwoUjKiD+17mZZshZor6RGzGlAmu6+5XpfJ7gGonB4R9YS866xRfYV1M3PFPvvHv3BCDpjgkbagr8z9pQ0MMRiThzy/zt+6juCEJtTNPOd4yDcf1KlZBW+WosmSf3giTLc48+fgH/v7UUy+9mbDhG39Sjy/HUw/FkXVRKWzJpQ7NQmcGHl3KD77KpiZB4LiRTZd/9mwA2OnTkw4cVD05QjDzrf999Xlf/tbry99O2vTUCvH4ctL7vcoBAYJIWIqFGDms8Zc3fquhLgllHzh2IGI/uUuIuT6ZuPe275/70Q+g0OIxUR+Xttnf/7qYTMYFgTj+qMPuv/NHM6ZMqNQj7PcbDI7lyCdEMe9O9UbuacY9Q4UVzLZt/ei/vvyZc0979KkX31ixpr2ri1WZ0QUmY/aMqRNPOPpdhx00CwCIWKDoNbdd7gDHXDS3gtSv3TvU0iPdlxgccsRsm4mkkC2t7a4Hu1QAhoCBwu072qQQzJxMxAAAGFAgMxPxjCkTZkyZMPABw7MvBBIRANq2pema5sv2jq50JltfFyMa+IVBQtza0ho26ojHbVPXLVN3fa+zK5VKpZMJYxfMEwMjbt3eGrIsEYsN/KAqgn29rISVPJMnjI7HLNu2V6x6+9UlK+ts9PwBPTXdD4JkDJeu3Pj60pWWZekSp04aB7nnZSKilIKIA6UUERH3818pCpRSivL3d/jI4BHDG8eNarYsa9PmrU//65V6c6BzC5SKmbhpW/cz/3rVtEwV+FMnjhMCJ4wbZZrmjta2x599MakPdDSlyDTE9k73iWdfNG0zCPw5M6fAvpVE+5ociKiI6pOJ9x91GEqNif7ftTd1drvD6jRmpv7B3JDUXE9dc+0NrutqunnkgoNGNQ8j4uLCMtSklEIIgf38l1JoUhb6JuHcNCk/eOyRDMIyje9c99M177SNrNeZgajf6TEnYtLS8Ws/uHlbyw7TtKZPHjd7+mQAOOXE9ynGmGX/4Mafv7lmy6h6nWHno9m2qDPxmz+6be2GTbYdGz2iccG8mcw7eQBvZTEIghQBmfmrn/kkkorHY6+8sfTUi656/uXlmoR4TCTiff+Px4Sh4atvrDr94mueev7lZDIZBP6VXzgPKlp0Gi5Vl5x/RsI2DdPa+M7mUy+64uGnXhao4jHsZ26mjqvf3nj2Zd/9w4OPNNTVK+KFn/+UoWtEfNGZJzc3JqSh72hrO+PiK//8j+eRg/5GiwvLwA0bt1xw5aI77/lzY2O9r+DKS86zLZP2bQk/dmcHZOUqC2IWiL+696GvLb5DImUyWSm1uTOnNDXWlzt4Zujo7Fy6cq3nuYl4LGD5n5ee+6VPf0KpnTz0e1ehiKQQjzz5wqev/IEU4LleQDRnxuSRw4dh+bu2O5Ve9tbaru7uhro6n8WFH/vg96/5glIECFKI5/79xjmXfZOZA993PG/OjMmjRgzvpxwmnc4sX72+vb29oa7eB/HxE4+66dqrwolV8Eh3isEhBwCEF/XO3z34nRvuYkQKvKzjEvWn1gQKy7akppNS/3nZ+V+66Ky9dL7CYf/y6NMLr70l43is/EzW6X9uiMIyTd0wlKLPnn3yt6+8RCkSAhExPNLHnnnp8m/f0JlymIJsJqv6Hw3QtEzDMBXRuaed8N//eVmoi/axwzJo5IAcP95cvubWu//87Iuvtnel+m9cwUz1icSRC+ZceuGZhx00i2gvLsDh3NZt3Hzrr/706DMvtezowP5ZyByzrQUHzbrkvNPe/54FJS1cwtE2bd1+26/+/Mg/X9ja0oqi34pIZsvS58+e8ZlzTj3p/UeG6+a+d2UHkxyQO2sA0NHVvf6drd3dmX4ERDJhjx8zalhjfeEv7oO5pdPZtRs3d3anicpu2MZi1rjRI0YOb4IwZNKLtfnRslln7cbN7Z0plevU0Bu2bY4d1Tx6xPBwtH1vM0IMMjkAgIiZy7a+6PP7APuouQURMcDAV67+j2VXR2Nm4sGsrR18coQI41c78eEZwlV8H80p/2c5LI3ud3oDntsAR0PctQLuvYGhsvGGiLJCDw+vOPJBsiE42l7FfrLxVsPeQI0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lEWNHDWURY0cNZRFjRw1lMX/B8Z0enrdf2ZxAAAAAElFTkSuQmCC">
<style>
  /* no scrollbars anywhere; scrolling itself still works */
  * { scrollbar-width: none !important; }
  *::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
  /* Heebo, kept with the project so the screen looks the same with no internet */
  @font-face { font-family: "Heebo"; font-style: normal; font-weight: 100 900; font-display: swap;
               src: url(/fonts/heebo-hebrew.woff2) format("woff2");
               unicode-range: U+0307-0308, U+0590-05FF, U+200C-2010, U+20AA, U+25CC, U+FB1D-FB4F; }
  @font-face { font-family: "Heebo"; font-style: normal; font-weight: 100 900; font-display: swap;
               src: url(/fonts/heebo-latin-ext.woff2) format("woff2");
               unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF; }
  @font-face { font-family: "Heebo"; font-style: normal; font-weight: 100 900; font-display: swap;
               src: url(/fonts/heebo-latin.woff2) format("woff2");
               unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }

  /* Vesta Core Labs palette, with the seven note colours of colour-coded learning keyboards:
     the same colour in every octave, so every C is red. */
  :root {
    --navy: #0e2a47; --navy2: #0a1b31; --blue: #0b4cb0; --sky: #a8d4f0; --ice: #e9eff7;
    --gold: #e2b04a; --gold2: #c9962f; --ink: #13233a; --muted: #56667d; --line: #d3e0ef; --card: #fff;
    /* softened, so the keys read as colour-coded and not as a rainbow */
    --c: #d9656b; --d: #e38f4f; --e: #e2bf4f; --f: #86b56a; --g: #3f9f98; --a: #6f8fd6; --b: #a07cc8;
    --ok: #1f9d55; --bad: #e5484d;
    /* light theme */
    --bg: #f2f7fd; --surface: #ffffff; --surface2: #e8f0fa; --head: #ffffff; --chip: #e4edf9; --chip2: #d3e5fb;
    --title: #0e2a47; --link: #0b4cb0; --track: #dfe9f6; --noteBg: #fff8e6; --noteLine: #f3dfae; --noteInk: #6b5212;
    --stepnum: #b9c9e0;
    --shadow: 0 1.25rem 2.5rem -1.5rem rgba(14,42,71,.28); --cardShadow: 0 0.25rem 0.9rem -0.5rem rgba(14,42,71,.16);
    --frame: #13233a; --foot: #0e2a47; --foot2: #0a1f37;
    --you: #e2b04a; --them: #7cc4f5; --h1a: #1257c4;
    --dueBg: #fff3dc; --dueInk: #8a5a00;
    /* the banner is the logo: these stay the brand's blue in every theme and colour set */
    --band1: #16386a; --band2: #0a1b31; --band3: #050a14; --bandKeys: #132c52;
  }
  /* Simple colours: every key sticker in the brand blue, the key to play in gold */
  html[data-colors="simple"] { --c: #3b6fb8; --d: #3b6fb8; --e: #3b6fb8; --f: #3b6fb8; --g: #3b6fb8; --a: #3b6fb8; --b: #3b6fb8; }
  html[data-colors="simple"] .lit { background: var(--gold) !important; box-shadow: 0 0 1.5rem 0.2rem var(--gold), inset 0 -0.375rem 0 rgba(0,0,0,.18) !important; }
  html[data-colors="simple"] .sticker:not(.neutral) { --col: var(--gold) !important; color: var(--navy); }
  html[data-colors="simple"] .bead.cur:not(.blank) { --col: var(--gold) !important; color: var(--navy); }
  /* dark theme: the brand's own dark, easy on the eyes */
  html[data-theme="dark"] {
    --bg: #0a1120; --surface: #111b2e; --surface2: #0d1628; --head: #0a1120; --chip: #19263c; --chip2: #1c2f4f;
    --title: #eef3fb; --ink: #dbe4f0; --muted: #a3b3ca; --line: #223049; --link: #8fc6f2; --track: #22314a;
    --noteBg: #2a2410; --noteLine: #4d4020; --noteInk: #f3d78a; --stepnum: #33486b;
    --shadow: 0 1.25rem 2.5rem -1.5rem rgba(0,0,0,.6); --cardShadow: none;
    --frame: #050a14; --foot: #070d19; --foot2: #050a14; --h1a: #7cc4f5;
    --dueBg: #3a2c0c; --dueInk: #f3d78a;
    --card: var(--surface2);   /* a white panel would leave the dark theme's own text unreadable on it */
  }
  /* ---------- colour sets: the page, the cards and the footer move with them.
     The banner is the logo and stays the brand's own blue, and so do the seven note colours
     and the gold key, which carry meaning in a lesson ---------- */
  html[data-palette="ember"] {
    --blue: #a8391f; --link: #9d3319; --title: #3b1b12; --h1a: #b4442a;
    --bg: #fdf4f0; --head: #fffaf8; --surface: #ffffff; --surface2: #f9e8e1;
    --chip: #f8e6de; --chip2: #f5d6c9; --line: #eedbd1; --track: #f0ded5; --stepnum: #e0bfb2;
    --frame: #33160f; --foot: #3b1b12; --foot2: #2a120c;
  }
  html[data-theme="dark"][data-palette="ember"] {
    --title: #eef3fb;   /* the dark theme's own headings, which the set above would otherwise darken */
    --link: #f0a189; --h1a: #f0a189;
    --bg: #160b07; --head: #160b07; --surface: #241310; --surface2: #1d100c;
    --chip: #33201a; --chip2: #40261e; --line: #3a241d; --track: #3a241d; --stepnum: #6b3d2e;
    --frame: #150805; --foot: #1b0a06; --foot2: #150805;
  }
  html[data-palette="forest"] {
    --blue: #1a6b4a; --link: #165d40; --title: #11312a; --h1a: #1f7a55;
    --bg: #f1f8f4; --head: #fbfefc; --surface: #ffffff; --surface2: #e2f1e9;
    --chip: #e1f0e8; --chip2: #cfe8da; --line: #d5e6dc; --track: #dcebe3; --stepnum: #b0d3bf;
    --frame: #0d251f; --foot: #11312a; --foot2: #0b241d;
  }
  html[data-theme="dark"][data-palette="forest"] {
    --title: #eef3fb;   /* the dark theme's own headings, which the set above would otherwise darken */
    --link: #7fd6ab; --h1a: #7fd6ab;
    --bg: #071310; --head: #071310; --surface: #0f221c; --surface2: #0c1a16;
    --chip: #172d26; --chip2: #1b3a30; --line: #1f3a31; --track: #1f3a31; --stepnum: #2f5a49;
    --frame: #050f0c; --foot: #081713; --foot2: #050f0c;
  }
  html[data-palette="slate"] {
    --blue: #4a5568; --link: #3d4657; --title: #1f2733; --h1a: #5a6b85;
    --bg: #f4f5f8; --head: #fdfdfe; --surface: #ffffff; --surface2: #e9ebf0;
    --chip: #e8eaf0; --chip2: #dcdfe8; --line: #dcdfe6; --track: #e2e5ec; --stepnum: #c3c9d4;
    --frame: #1a2029; --foot: #1f2733; --foot2: #161c25;
  }
  html[data-theme="dark"][data-palette="slate"] {
    --title: #eef3fb;   /* the dark theme's own headings, which the set above would otherwise darken */
    --link: #c3cbd9; --h1a: #c3cbd9;
    --bg: #0e1117; --head: #0e1117; --surface: #181d26; --surface2: #13171e;
    --chip: #232a35; --chip2: #2c3542; --line: #2a313c; --track: #2a313c; --stepnum: #495364;
    --frame: #0c0f14; --foot: #11151b; --foot2: #0c0f14;
  }
  html[data-palette="plum"] {
    --blue: #6b3fa0; --link: #5e3590; --title: #2e1b45; --h1a: #7a4bb5;
    --bg: #f8f4fc; --head: #fdfbff; --surface: #ffffff; --surface2: #efe6f8;
    --chip: #eee5f8; --chip2: #e1d2f3; --line: #e4dbf0; --track: #e8dff5; --stepnum: #cbb8e4;
    --frame: #24143a; --foot: #2e1b45; --foot2: #211031;
  }
  html[data-theme="dark"][data-palette="plum"] {
    --title: #eef3fb;   /* the dark theme's own headings, which the set above would otherwise darken */
    --link: #c9a5f0; --h1a: #c9a5f0;
    --bg: #120c1f; --head: #120c1f; --surface: #1d1430; --surface2: #161027;
    --chip: #2a1d3d; --chip2: #35244d; --line: #322546; --track: #322546; --stepnum: #553f75;
    --frame: #130a1e; --foot: #1a1029; --foot2: #130a1e;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { margin: 0; color: var(--ink); background: var(--bg); font-weight: 500;
         font-family: "Heebo", "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; }
  a { color: var(--link); }
  .wrap { width: 100%; max-width: 72rem; margin: 0 auto; padding: 0 1.75rem; }
  button, input { font: inherit; }
  button { cursor: pointer; }
  :focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }

  /* ---------- header ---------- */
  header.top { position: sticky; top: 0; z-index: 20; background: var(--head); border-bottom: 1px solid var(--line); }
  header.top .wrap { display: flex; align-items: center; gap: 1.5rem; height: 4rem; }
  section[id], .stage { scroll-margin-top: 4.5rem; }
  .logo { display: flex; align-items: center; gap: 0.45rem; }
  /* the house and the name are two pieces, so in Hebrew the house comes first, on the right */
  .logo svg { height: 2.25rem; width: auto; display: block; }
  nav { display: flex; gap: 0.25rem; flex: 1; }
  nav a { text-decoration: none; color: var(--muted); font-weight: 600; font-size: 0.9375rem; padding: 0.4rem 0.75rem; border-radius: 0.5rem; }
  nav a:hover { color: var(--link); }
  nav a.on { color: var(--title); background: var(--chip); }
  .tools { margin-inline-start: auto; display: inline-flex; gap: 0.25rem; }
  .tools .langBtn { font-weight: 700; }
  .tools button { border: 1px solid var(--line); background: var(--surface); color: var(--muted); font-weight: 700; font-size: 0.875rem;
                  min-width: 2.1rem; height: 2.1rem; padding: 0 0.5rem; border-radius: 0.5rem; }
  .tools button:hover { color: var(--link); border-color: var(--link); }
  .logo .for-dark { display: none; }
  html[data-theme="dark"] .logo .for-dark { display: block; } html[data-theme="dark"] .logo .for-light { display: none; }
  .pill { display: inline-flex; align-items: center; gap: 0.4375rem; padding: 0.4rem 0.75rem; border-radius: 99px;
          font-size: 0.8125rem; font-weight: 700; background: var(--chip); color: var(--muted); white-space: nowrap; }
  .pill i { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: #9aa8bb; }
  .pill.on { background: #e3f6eb; color: #17784a; } .pill.on i { background: var(--ok); }
  .pill.off { background: #fde8e8; color: #c0282d; } .pill.off i { background: var(--bad); }
  html[data-theme="dark"] .pill.on { background: #10301f; color: #6fd69d; }
  html[data-theme="dark"] .pill.off { background: #3a1416; color: #ff8a8d; }

  /* ---------- today: the card, and beside it the numbers and the pace ---------- */
  .band .wrap { padding-top: 2.5rem; padding-bottom: 3.25rem; }
  .band .cols { display: grid; grid-template-columns: minmax(0, 26rem) minmax(0, 1fr); gap: 2.5rem 3.5rem; align-items: stretch; }
  /* the card takes the height of the column beside it, and the button sits at its foot */
  .band .today { display: flex; flex-direction: column; }
  .band .today .go { margin-top: auto; padding-top: 1.5rem; }
  @media (max-width: 52rem) { .band .cols { grid-template-columns: 1fr; gap: 2rem; } .band .today .go { margin-top: 1.25rem; padding-top: 0; } }
  h1 { font-size: 2.75rem; line-height: 1.12; margin: 0 0 1rem; letter-spacing: -.02em; color: var(--title); font-weight: 800; }
  .today { width: 100%; background: var(--surface); border-radius: 1rem; padding: 1.5rem 1.625rem; box-shadow: var(--shadow); border: 1px solid var(--line); }
  .kicker { margin: 0 0 0.75rem; font-size: 0.75rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; color: var(--link); }
  .today .row { display: flex; align-items: center; gap: 1rem; }
  .today .song { font-size: 1.5rem; font-weight: 800; color: var(--title); margin: 0 0 0.25rem; }
  .today p { margin: 0; color: var(--muted); line-height: 1.5; font-size: 0.9375rem; }
  .today .go { display: flex; align-items: center; flex-wrap: wrap; gap: 0.75rem 1.125rem; margin-top: 1.25rem; }
  .today .go a { font-size: 0.9375rem; font-weight: 600; text-decoration: none; }
  .today .go a:hover { text-decoration: underline; }
  /* the column beside the card: the same numbers and controls, with no frame around them */
  .side h3 { margin: 0 0 0.5rem; font-size: 1rem; font-weight: 800; color: var(--title); }
  .side .sep { height: 1px; background: var(--line); margin: 1.5rem 0; }
  .statline { color: var(--muted); font-size: 0.9375rem; line-height: 2; }
  .statline b { color: var(--title); font-weight: 700; }
  .statline span { display: block; }
  .opts { display: flex; align-items: center; flex-wrap: wrap; gap: 0.625rem 1rem; }
  .tempo { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem 1rem; width: 100%; font-size: 0.875rem; color: var(--muted); margin-top: 0.875rem; }
  .tempo label { display: inline-flex; align-items: center; gap: 0.45rem; cursor: pointer; }
  .tempo input[type=range] { width: 7rem; accent-color: var(--blue); }
  .tempo b { color: var(--title); }
  .ring { --p: 0; width: 4.5rem; height: 4.5rem; border-radius: 50%; flex: none; display: grid; place-items: center;
          background: conic-gradient(var(--link) calc(var(--p) * 1%), var(--track) 0); }
  .ring > div, .ring > span { width: calc(100% - 0.75rem); height: calc(100% - 0.75rem); border-radius: 50%; background: var(--surface); display: grid; place-items: center; text-align: center; }
  .ring b { font-size: 0.9375rem; font-weight: 800; color: var(--title); line-height: 1; }
  .btn { border: 0; border-radius: 0.625rem; padding: 0.75rem 1.25rem; font-weight: 700; font-size: 0.9375rem; text-decoration: none; display: inline-flex; align-items: center; gap: 0.4rem; }
  .btn.gold { background: var(--gold); color: #1d1606; }
  .btn.gold:hover { background: #ebbd5c; }
  .btn.gold:active { background: var(--gold2); }
  .btn.ghost { background: var(--surface); color: var(--title); border: 1px solid var(--line); }
  .btn.ghost:hover { border-color: var(--link); color: var(--link); }
  .btn.red { background: #fde8e8; color: var(--bad); }
  .pseg { display: inline-flex; background: var(--chip); border-radius: 99px; padding: 0.2rem; }
  .pseg button { white-space: nowrap; border: 0; background: transparent; color: var(--muted); padding: 0.3rem 0.8rem; border-radius: 99px; font-weight: 700; font-size: 0.8125rem; }
  .pseg button.on { background: var(--blue); color: #fff; }

  /* ---------- sections ---------- */
  section.block { padding: 3.25rem 0; }
  section.block.alt { background: var(--surface2); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
  .sechead { display: flex; align-items: baseline; gap: 0.5rem 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
  .sechead h2 { margin: 0; font-size: 1.625rem; font-weight: 800; color: var(--title); letter-spacing: -.01em; }
  .sechead p { margin: 0; color: var(--muted); font-size: 0.9375rem; }

  /* ---------- the piano stage ---------- */
  /* idle it sits on the page like any section; in a lesson it turns into the dark stage */
  .stage { padding: 3.25rem 0 2.75rem; color: var(--ink);
           --t-bg: var(--chip); --t-fg: var(--muted); --t-on-bg: var(--blue); --t-on-fg: #fff; --t-strong: var(--title); }
  .stage .wrap { max-width: 72rem; }
  body.focus .stage { background: linear-gradient(180deg, var(--navy), var(--navy2)); color: #fff; padding: 1.125rem 0 1.625rem;
                      --t-bg: rgba(255,255,255,.08); --t-fg: #c8d6ea; --t-on-bg: #fff; --t-on-fg: var(--navy); --t-strong: #fff; }
  html[data-theme="dark"] body.focus .stage { background: linear-gradient(180deg, #0f1e36, #070d19); }
  body.focus .stage .wrap { max-width: 115rem; }
  body.focus .stage .sechead { display: none; }
  /* one line: the controls keep their size and the line above them gives way instead of breaking */
  .toolbar { display: flex; align-items: center; gap: 0.625rem; flex-wrap: nowrap; margin-bottom: 0.875rem;
             overflow-x: auto; scrollbar-width: none; padding-bottom: 0.15rem; }
  .toolbar::-webkit-scrollbar { display: none; }
  .toolbar > *:not(.title) { flex: none; }
  /* the title keeps the width of what is in it. Letting it shrink is what squeezed the
     sentence next to it until the words broke. */
  .toolbar .title { font-weight: 800; font-size: 1.125rem; margin-inline-end: auto; display: flex; align-items: center;
                    gap: 0.75rem; color: var(--t-strong); flex: 0 0 auto; }
  .toolbar .title > span:first-child { flex: none; }
  body:not(.focus) #stageTitle { display: none; }
  .seg { display: inline-flex; background: var(--t-bg); border-radius: 0.625rem; padding: 0.2rem; }
  .seg button { border: 0; background: transparent; color: var(--t-fg); padding: 0.375rem 0.55rem; border-radius: 0.5rem; font-weight: 700; font-size: 0.8125rem; }
  .seg button.on { background: var(--t-on-bg); color: var(--t-on-fg); }
  .live { border: 0; border-radius: 0.625rem; padding: 0.5rem 0.8rem; font-weight: 700; font-size: 0.8125rem; background: var(--t-bg); color: var(--t-fg);
          display: inline-flex; align-items: center; gap: 0.5rem; }
  .live i { width: 0.5625rem; height: 0.5625rem; border-radius: 50%; background: #8595ad; }
  .live.on { color: #fff; background: var(--blue); }
  .live.on i { background: #fff; animation: beat 1.2s infinite; }
  #soundBtn.on { color: #fff; background: var(--ok); } #soundBtn.on i { background: #fff; }
  #pedalBtn.on { color: var(--navy); background: var(--gold); } #pedalBtn.on i { background: var(--navy); }
  #pedalBtn.feet i { background: var(--gold); box-shadow: 0 0 0 0.25rem rgba(226,176,74,.35); }
  .vol { display: inline-flex; align-items: center; gap: 0.5rem; background: var(--t-bg); border-radius: 0.625rem;
         padding: 0.35rem 0.75rem; color: var(--t-fg); font-weight: 700; font-size: 0.8125rem; }
  .vol input { width: 5rem; accent-color: var(--gold); }
  .vol b { min-width: 2.6rem; text-align: end; }
  /* one line, whole words, never cut. It used to be nowrap with an ellipsis, which a
     flex container ignores, so the last word was cut in the middle of a letter. The row
     already scrolls sideways when the controls do not fit, and the sentence rides with it. */
  /* the sentence has a line of its own, so the controls above it never have to share their row */
  .heardrow { margin: -0.25rem 0 0.75rem; min-height: 1.3rem; }
  .heard { display: inline-flex; align-items: center; gap: 0.5rem; color: var(--t-fg); font-size: 0.875rem; font-weight: 600;
           white-space: nowrap; }
  /* the keyboard never mirrors, in any language */
  .kb .b { color: #fff; font-size: 0.7rem; font-weight: 700; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 0.3rem; }
  .pchint { margin: 0.625rem 0 0; font-size: 0.8125rem; color: var(--muted); text-align: center; }
  .pchint b { color: var(--title); direction: ltr; unicode-bidi: isolate; }
  #setSize button { direction: ltr; unicode-bidi: isolate; }
  .setsel { font: inherit; font-weight: 700; font-size: 0.875rem; padding: 0.45rem 0.6rem; border-radius: 0.625rem; border: 1px solid var(--line);
            background: var(--chip); color: var(--title); min-width: 9.5rem; }
  .kbframe { direction: ltr; background: var(--frame); border-radius: 0.875rem; padding: 0.375rem; box-shadow: var(--cardShadow); max-width: 100%; margin: 0 auto; }
  .kbwrap { direction: ltr; border-radius: 0.5rem; overflow: hidden; }
  .kbwrap.scroll { overflow-x: auto; scrollbar-width: none; }
  .kbwrap.scroll::-webkit-scrollbar { display: none; }
  .kb { --k: 2.5rem; position: relative; width: 100%; user-select: none; -webkit-user-select: none; touch-action: manipulation; margin: 0 auto; }
  .w { position: absolute; top: 0; bottom: 0; border-radius: 0 0 0.5rem 0.5rem;
       background: linear-gradient(180deg, #e8e2d4, #fbf8f1 10%, #fbf8f1 86%, #efe8da);
       box-shadow: inset 0 -0.4375rem 0 #d8cfbd, inset -1px 0 0 #cfc6b4;
       display: flex; align-items: flex-end; justify-content: center; padding-bottom: calc(var(--k) * .3);
       transition: background .07s, transform .07s; }
  .b { position: absolute; top: 0; height: 62%; z-index: 2; border-radius: 0 0 0.4375rem 0.4375rem;
       background: linear-gradient(180deg, #2c3446, #151a26 70%, #0b0e15);
       box-shadow: inset 0 -0.375rem 0 #04060a, 0 0.25rem 0.375rem rgba(0,0,0,.4); transition: background .07s, transform .07s;
       display: flex; align-items: flex-end; justify-content: center; padding-bottom: 0.5rem; }
  .dot { --col: #999; width: min(calc(var(--k) * .72), 2.25rem); height: min(calc(var(--k) * .72), 2.25rem); border-radius: 50%;
         background: var(--col); color: #fff; font-weight: 800; display: grid; place-items: center;
         font-size: min(calc(var(--k) * .34), 0.9375rem); box-shadow: inset 0 -2px 0 rgba(0,0,0,.18); }
  .dot.dark { color: #1d1d1d; }
  .dot.small { width: calc(var(--k) * .3); height: calc(var(--k) * .3); font-size: 0; }
  .b .dot { display: none; }
  .labels-none .dot { display: none; }
  .kb.blind .dot { visibility: hidden; }
  /* the middle step: no labels and no colours, only the key to play, lit in gold */
  .kb.keysonly .lit { background: var(--gold) !important; box-shadow: 0 0 1.5rem 0.2rem var(--gold), inset 0 -0.375rem 0 rgba(0,0,0,.18) !important; }
  .lit { background: var(--col) !important; box-shadow: 0 0 1.5rem 0.1875rem var(--col), inset 0 -0.375rem 0 rgba(0,0,0,.18) !important; transform: translateY(0.1875rem); }
  .w.lit .dot { background: #fff; color: #1d1d1d; }
  .lit.target { animation: glow 1s ease-in-out infinite; }
  .lit.demo { box-shadow: 0 0 0 0.1875rem #fff inset, 0 0 1.5rem 0.1875rem var(--col) !important; }
  .lit.wrong { background: var(--bad) !important; box-shadow: 0 0 1.625rem 0.25rem var(--bad) !important; animation: shake .25s; }
  .kb.live .w, .kb.live .b { cursor: pointer; }

  /* ---------- the lesson (focus mode) ---------- */
  .lesson { display: none; }
  body.focus .band, body.focus .sections, body.focus footer, body.focus nav, body.focus .idleonly { display: none !important; }
  body.focus .lesson { display: grid; }
  body.focus .stage { min-height: calc(100vh - 4.0625rem); display: flex; flex-direction: column; justify-content: center; }
  body.focus .kbframe { background: #050a14; }
  .lesson { grid-template-columns: 13.5rem 1fr; gap: 1.125rem; margin-bottom: 1.625rem; }
  .panel { background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.1); border-radius: 1.125rem; padding: 1.125rem; }
  .now { text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; position: relative; overflow: hidden; }
  .what { color: #c8d6ea; font-weight: 700; font-size: 0.9375rem; }
  .sticker { --col: #34507a; width: 7rem; height: 7rem; border-radius: 50%; margin: 0.6rem 0 0.5rem; display: grid; place-items: center;
             font-size: 3.3rem; font-weight: 900; color: #fff;
             background: radial-gradient(circle at 35% 30%, rgba(255,255,255,.35), transparent 55%), var(--col);
             box-shadow: 0 0.75rem 1.875rem -0.5rem var(--col), inset 0 -0.5rem 0 rgba(0,0,0,.18); }
  .sticker.dark { color: #1d1d1d; }
  .sticker.neutral { --col: #2b4468; color: #c8d6ea; font-size: 2.1rem; }
  .sticker.pulse { animation: pop .9s ease-in-out infinite; }
  .tags { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 0.5rem; min-height: 1.875rem; }
  .tag { padding: 0.25rem 0.6875rem; border-radius: 99px; font-weight: 700; font-size: 0.9375rem; background: rgba(255,255,255,.1); }
  .tag.hand { color: #ffd592; } .tag:empty { display: none; }
  .piece { display: flex; flex-direction: column; min-height: 15rem; }
  .piece h4 { margin: 0; padding-bottom: 0.7rem; border-bottom: 1px solid rgba(255,255,255,.08);
              font-size: 0.875rem; color: #c8d6ea; letter-spacing: .02em; }
  /* the padding is room for the ring and the glow around the note being played: the box scrolls,
     so anything drawn outside a bead is cut at its edge, and the first bead of a row sits there */
  .seq { display: flex; flex-wrap: wrap; gap: 0.625rem; direction: ltr; flex: 1; max-height: 16rem; padding: 1rem 0.5rem;
         align-content: safe center; justify-content: safe center; overflow-y: auto; scrollbar-width: thin; }
  .bead { --col: #34507a; min-width: 3.375rem; padding: 0.5rem 0.625rem 0.4375rem; border-radius: 0.875rem; text-align: center; font-size: 1.375rem; font-weight: 800;
          color: #fff; background: var(--col); box-shadow: inset 0 -0.25rem 0 rgba(0,0,0,.2); transition: transform .15s; }
  .bead.dark { color: #1d1d1d; }
  .bead small { display: block; font-size: 0.75rem; font-weight: 700; opacity: .85; }
  .bead.done { background: transparent; color: var(--col); box-shadow: inset 0 0 0 2px var(--col); transform: scale(.9); }
  .bead.cur { transform: translateY(-0.25rem) scale(1.12); box-shadow: 0 0 0 0.1875rem #fff, 0 0.625rem 1.5rem -0.375rem var(--col); }
  .bead.blank { --col: #2b4468; color: #c8d6ea; min-width: 2.125rem; font-size: 1rem; }
  .bead.blank.cur { --col: #4a6fa5; color: #fff; }
  .lessonbar { display: flex; align-items: center; gap: 0.875rem; margin-top: 0; padding-top: 0.9rem;
               border-top: 1px solid rgba(255,255,255,.08); }
  .progress { flex: 1; height: 0.625rem; border-radius: 99px; background: rgba(255,255,255,.1); overflow: hidden; }
  .progress i { display: block; height: 100%; width: 0; transition: width .4s; background: linear-gradient(90deg, var(--blue), var(--sky)); }
  .toast { position: fixed; left: 50%; bottom: 1.625rem; transform: translateX(-50%) translateY(1.25rem); opacity: 0; transition: .25s; z-index: 60; pointer-events: none;
           background: var(--navy); color: #fff; padding: 0.75rem 1.25rem; border-radius: 0.75rem; font-weight: 600; box-shadow: 0 0.875rem 2.5rem rgba(0,0,0,.25); max-width: 90vw; }
  .toast.show { opacity: 1; transform: translateX(-50%); }
  .toast.warn { background: #b45309; } .toast.good { background: var(--ok); }
  .burst i { position: absolute; left: 50%; top: 50%; width: 0.6rem; height: 0.6rem; border-radius: 50%; animation: fly .5s ease-out forwards; }

  /* ---------- songs ---------- */
  .songs { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: 0.875rem; }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 0.875rem; position: relative;
          box-shadow: var(--cardShadow); transition: border-color .15s, transform .15s; }
  .card:hover { border-color: var(--link); transform: translateY(-2px); }
  .card .go { display: flex; gap: 0.875rem; align-items: center; width: 100%; border: 0; background: none; color: inherit;
              padding: 1rem; text-align: start; border-radius: inherit; }
  .card .ring { width: 3.25rem; height: 3.25rem; } .card .ring > div, .ring > span { width: calc(100% - 0.5rem); height: calc(100% - 0.5rem); } .card .ring b { font-size: 0.75rem; }
  .card .name { display: block; margin: 0 0 0.2rem; font-size: 1rem; font-weight: 700; color: var(--title); }
  .card .meta { display: block; font-size: 0.8125rem; color: var(--muted); line-height: 1.45; }
  .card .due { color: var(--dueInk); background: var(--dueBg); font-weight: 700; padding: 0 0.35rem; border-radius: 0.3rem; }
  .card .done { color: var(--ok); font-weight: 700; }
  .card .reset { position: absolute; top: 0.375rem; inset-inline-end: 0.375rem; border: 0; background: transparent; color: var(--muted);
                 font-size: 0.875rem; padding: 0.2rem 0.4rem; border-radius: 0.4rem; opacity: 0; transition: opacity .15s; }
  .card:hover .reset, .card:focus-within .reset { opacity: 1; }
  .card .reset:hover { color: var(--bad); background: var(--chip); }
  html:not([data-device="desktop"]) .card .reset { opacity: .6; }
  .card.hasDel .reset { inset-inline-end: 2.1rem; }
  .card .del { position: absolute; top: 0.375rem; inset-inline-end: 0.375rem; border: 0; background: transparent; color: var(--muted);
               font-size: 0.875rem; padding: 0.2rem 0.4rem; border-radius: 0.4rem; opacity: 0; transition: opacity .15s; }
  .card:hover .del, .card:focus-within .del { opacity: 1; }
  .card .del:hover { color: var(--bad); background: var(--chip); }
  html:not([data-device="desktop"]) .card .del { opacity: .6; }
  .card .tag { display: inline-block; margin-top: 0.35rem; font-size: 0.6875rem; font-weight: 700; color: var(--muted);
               background: var(--chip); padding: 0.05rem 0.45rem; border-radius: 0.4rem; }
  .card .tag.up { color: var(--blue); }
  .card .credit { display: block; margin: -0.5rem 1rem 0.75rem; padding-top: 0.5rem; border-top: 1px dashed var(--line);
                  font-size: 0.75rem; color: var(--muted); text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  a.credit:hover { color: var(--link); }
  .addsong { margin-top: 1rem; background: var(--surface); border: 1px solid var(--line); border-radius: 0.875rem; padding: 0.875rem 1rem; box-shadow: var(--cardShadow); }
  .addsong.over { border-color: var(--link); border-style: dashed; background: var(--chip); }
  .addrow { display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap; }
  .addrow > b { color: var(--title); font-size: 0.9375rem; margin-inline-end: 0.25rem; }
  .addrow .btn { padding: 0.55rem 0.95rem; font-size: 0.875rem; }
  .addrow label.btn { cursor: pointer; }
  .addrow input { flex: 1; min-width: 12rem; padding: 0.55rem 0.9rem; border-radius: 0.625rem; border: 1px solid var(--line);
                  background: var(--surface2); color: var(--title); font-size: 0.9375rem; }
  .addsong small { display: block; margin-top: 0.6rem; color: var(--muted); font-size: 0.78rem; }
  .hit { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; border-top: 1px solid var(--line); }
  #qRes:not(:empty) { margin-top: 0.625rem; }
  .hit span { flex: 1; color: var(--ink); min-width: 0; } .hit .btn { padding: 0.4rem 0.8rem; font-size: 0.8125rem; }
  .hit b { display: block; font-weight: 600; color: var(--title); } .hit small { display: block; margin: 0; font-size: 0.75rem; color: var(--muted); }
  .hitcount { font-size: 0.8125rem; color: var(--muted); padding-bottom: 0.4rem; }
  .pager { display: flex; justify-content: center; gap: 0.3rem; padding-top: 0.75rem; flex-wrap: wrap; }
  .pager button { min-width: 2rem; height: 2rem; border: 1px solid var(--line); background: var(--surface); color: var(--ink); border-radius: 0.45rem; font-weight: 700; font-size: 0.8125rem; }
  .pager button.on { background: var(--blue); border-color: var(--blue); color: #fff; }
  .pager button:disabled:not(.on) { opacity: .4; cursor: default; }
  .pager .gap { color: var(--muted); padding: 0 0.15rem; font-size: 0.8125rem; align-self: center; }

  /* ---------- progress ---------- */
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .list { background: var(--surface); border: 1px solid var(--line); border-radius: 0.875rem; padding: 0.5rem 1.125rem 0.625rem; box-shadow: var(--cardShadow); }
  .list h3 { margin: 0.75rem 0 0.25rem; font-size: 1rem; font-weight: 700; color: var(--title); }
  .item { display: flex; align-items: center; gap: 0.625rem; padding: 0.625rem 0; border-top: 1px solid var(--line); font-size: 0.875rem; }
  .list h3 + div .item:first-child { border-top: 0; }
  .item .grow { flex: 1; min-width: 0; } .item b { color: var(--title); margin-inline-end: 0.5rem; } .item small { color: var(--muted); font-size: 0.8125rem; }
  .item .midi { color: var(--link); font-weight: 800; font-size: 0.75rem; text-decoration: none; letter-spacing: .03em; }
  .item .play { border: 0; width: 1.75rem; height: 1.75rem; border-radius: 0.4rem; background: var(--gold); color: #1d1606; font-size: 0.75rem; display: grid; place-items: center; }
  .item .del { border: 0; background: transparent; color: var(--muted); padding: 0.2rem 0.3rem; border-radius: 0.3rem; font-size: 0.8125rem; opacity: .7; }
  .item .del:hover { color: var(--bad); opacity: 1; }
  .empty { color: var(--muted); padding: 0.75rem 0; font-size: 0.875rem; }

  /* ---------- how a lesson works ---------- */
  /* the heading sits in the margin and the text beside it, so the line does not leave half the row empty */
  #method .wrap { display: grid; grid-template-columns: 11.5rem minmax(0, 1fr); gap: 0 2.5rem; align-items: start; }
  #method .sechead { grid-column: 1; grid-row: 1; margin-bottom: 0; display: block; }
  #method .methodtext { grid-column: 2; grid-row: 1; }
  /* the detail opens across the whole row, so the margin does not stay empty down the page */
  #method details.method { grid-column: 1 / -1; grid-row: 2; }
  @media (max-width: 58rem) {
    #method .wrap { display: block; }
    #method .sechead { margin-bottom: 1.25rem; }
  }
  .methodtext { max-width: 46rem; margin: 0; color: var(--ink); }
  .methodtext p { margin: 0 0 0.9rem; line-height: 1.75; font-size: 1.125rem; }
  .methodtext p:last-child { margin-bottom: 0; }
  details.method { margin-top: 1rem; }
  details.method summary { cursor: pointer; color: var(--link); font-weight: 700; font-size: 0.875rem; list-style: none; display: inline-block; }
  details.method summary::-webkit-details-marker { display: none; }
  details.method[open] summary .arr { display: inline-block; transform: rotate(90deg); }
  .deep { counter-reset: step; margin-top: 1.25rem; color: var(--ink); line-height: 1.7; font-size: 1rem; }
  .deep .lead { display: grid; grid-template-columns: 11.5rem 1fr; gap: 0.35rem 2.5rem;
                align-items: start; padding: 0.25rem 0 2rem; }
  .deep .lead h4 { grid-column: 1; grid-row: 1; }
  .deep .lead p { grid-column: 2; grid-row: 1; max-width: 46rem; font-size: 1.125rem; }
  .deep .note { margin-top: 2rem; margin-inline-start: 14rem; }   /* lines up with the text, not the margin */
  .deep h4 { margin: 0 0 0.25rem; color: var(--title); font-size: 1.0625rem; font-weight: 700; }
  .deep p { margin: 0; color: var(--ink); }
  .deep .step { display: grid; grid-template-columns: 11.5rem 1fr; grid-template-rows: auto 1fr;
                gap: 0.35rem 2.5rem; border-top: 1px solid var(--line); padding: 1.5rem 0; }
  .deep .step:last-of-type { border-bottom: 1px solid var(--line); }
  .deep .step::before { counter-increment: step; content: counter(step, decimal-leading-zero);
                        grid-column: 1; grid-row: 1; font-size: 2.25rem; font-weight: 800; line-height: 1;
                        color: var(--stepnum); font-variant-numeric: tabular-nums; }
  .deep .step h4 { grid-column: 1; grid-row: 2; align-self: start; }
  .deep .step p { grid-column: 2; grid-row: 1 / -1; max-width: 46rem; }
  @media (max-width: 46rem) {
    .deep .lead { grid-template-columns: 1fr; gap: 0.35rem; padding-bottom: 1.5rem; }
    .deep .lead h4, .deep .lead p { grid-column: 1; grid-row: auto; }
    .deep .note { margin-inline-start: 0; }
    .deep .step { grid-template-columns: auto 1fr; gap: 0.2rem 0.75rem; }
    .deep .step::before { font-size: 1.5rem; align-self: baseline; }
    .deep .step h4 { grid-column: 2; grid-row: 1; align-self: baseline; }
    .deep .step p { grid-column: 1 / -1; grid-row: 2; }
  }
  .note { border-inline-start: 3px solid var(--gold); padding-inline-start: 1.25rem; max-width: 48rem;
          color: var(--ink); line-height: 1.7; }
  .note b { color: var(--gold); }

  /* ---------- about: its own page, reached from the menu ---------- */
  #about { display: none; }
  body.about #about { display: block; }
  body.about .band, body.about .stage, body.about .sections { display: none; }
  body.focus #about { display: none !important; } body.focus.about .stage { display: flex; }   /* a lesson always wins */
  #about .wrap { max-width: 60rem; padding-top: 3.5rem; padding-bottom: 4rem; }
  #about .ahead { display: flex; align-items: center; gap: 1.25rem; margin-bottom: 2.25rem; }
  #about .ahead svg { width: 5rem; height: 5rem; flex: none; }
  #about h1 { margin: 0; }
  /* the same printed page as the method: a heading in the margin, the text beside it */
  #about p { display: grid; grid-template-columns: 11.5rem 1fr; gap: 0 2.5rem; align-items: start;
             border-top: 1px solid var(--line); padding: 1.6rem 0; margin: 0;
             font-size: 1.0625rem; line-height: 1.8; color: var(--ink); }
  #about p b { grid-column: 1; grid-row: 1; color: var(--title); font-size: 1.0625rem; font-weight: 700; line-height: 1.45; }
  #about p br { display: none; }
  /* the credits are a part of their own: the story closes on a firm rule, they sit on a sheet below it */
  #about .wrap > p:last-of-type { border-bottom: 2px solid var(--title); }
  #about .credbox { margin-top: 3.5rem; background: var(--surface2); border: 1px solid var(--line);
                  border-radius: 1.1rem; padding: 2.25rem 2.5rem 1.75rem; }
  #about .credhead { margin: 0 0 1.5rem; color: var(--muted); font-size: 0.9375rem; font-weight: 700;
                     letter-spacing: 0.12em; text-transform: uppercase; }
  .credits { list-style: none; margin: 0 0 1.5rem; padding: 0; }
  .credits li { display: grid; grid-template-columns: 9.5rem 1fr; gap: 0.25rem 2rem; align-items: start;
                border-bottom: 1px solid var(--line); padding: 1rem 0; }
  .credits li:last-child { border-bottom: 0; padding-bottom: 0.25rem; }
  .credits b { grid-column: 1; grid-row: 1; color: var(--link); font-size: 1rem; font-weight: 700; }
  #about .credits p { grid-column: 2; grid-row: 1; display: block; border: 0; padding: 0; margin: 0;
                      font-size: 0.9375rem; line-height: 1.7; }
  .credits .links { grid-column: 2; grid-row: 2; display: flex; flex-wrap: wrap; gap: 0.35rem 1.25rem;
                    font-size: 0.9375rem; font-weight: 700; margin-top: 0.35rem; }
  .credits .links a { text-decoration: none; unicode-bidi: isolate; }
  @media (max-width: 46rem) {
    #about p, .credits li { grid-template-columns: 1fr; gap: 0.35rem; }
    #about p b, .credits b, #about .credits p, .credits .links { grid-column: 1; grid-row: auto; }
    #about .credbox { padding: 1.5rem 1.25rem 1.25rem; }
  }
  #about .back { display: inline-block; margin-top: 0.75rem; font-weight: 700; font-size: 0.9375rem; text-decoration: none; }

  /* ---------- banner: the logo over a dark keyboard, under the header (idleonly hides it in a lesson) ---------- */
  .arm-banner{--kh:clamp(2.75rem,4vw,4.75rem);position:relative;display:grid;place-items:center;
    height:clamp(9.5rem,calc(10vw + 5.5rem),16.5rem);padding:1rem 1rem calc(var(--kh) + .85rem);overflow:hidden;
    background:url('data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%20width%3D%22245%22%20height%3D%22160%22%20viewBox%3D%220%200%20245%20160%22%3E%3Crect%20x%3D%220%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%2235%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%2270%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22105%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22140%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22175%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22210%22%20width%3D%221.5%22%20height%3D%22160%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%2225%22%20width%3D%2220%22%20height%3D%2296%22%20rx%3D%223%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%2260%22%20width%3D%2220%22%20height%3D%2296%22%20rx%3D%223%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22130%22%20width%3D%2220%22%20height%3D%2296%22%20rx%3D%223%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22165%22%20width%3D%2220%22%20height%3D%2296%22%20rx%3D%223%22%20fill%3D%22%23070f1e%22/%3E%3Crect%20x%3D%22200%22%20width%3D%2220%22%20height%3D%2296%22%20rx%3D%223%22%20fill%3D%22%23070f1e%22/%3E%3C/svg%3E') repeat-x center bottom/calc(var(--kh)*1.53125) var(--kh),
      linear-gradient(var(--bandKeys),var(--bandKeys)) repeat-x center bottom/100% var(--kh),
      radial-gradient(ellipse 70% 90% at 50% 38%,var(--band1),var(--band2) 62%,var(--band3))}
  .arm-banner::after{content:"";position:absolute;inset:auto 0 0;height:var(--kh);background:linear-gradient(rgba(5,10,20,.35),rgba(5,10,20,0) 40%);pointer-events:none}
  .arm-banner svg{display:block;width:100%;height:auto}
  .arm-banner .wide{width:clamp(15rem,calc(21vw + 6.5rem),31rem)}
  .arm-banner .stacked{display:none;width:min(12rem,56vw)}
  @media (max-width:560px){
    .arm-banner{height:auto;min-height:12rem;--kh:2.5rem}
    .arm-banner .wide{display:none} .arm-banner .stacked{display:block}
  }
  @media (max-height:500px) and (orientation:landscape){
    .arm-banner{height:8.5rem;min-height:0;--kh:2.25rem;padding-top:.75rem;padding-bottom:calc(var(--kh) + .6rem)}
    .arm-banner .wide{display:block;width:min(16rem,40vw)} .arm-banner .stacked{display:none}
  }

  /* ---------- footer ---------- */
  footer { background: var(--foot); color: #c8d6ea; font-size: 0.875rem; }
  footer .main { display: grid; grid-template-columns: 1.6fr 1fr 1fr; gap: 2rem; padding-top: 2.75rem; padding-bottom: 2.25rem; }
  footer .brand svg { height: 2.25rem; width: auto; display: block; margin-bottom: 0.875rem; }
  footer .brand p { margin: 0; max-width: 28rem; line-height: 1.55; color: #f0f5fc; font-size: 1.1875rem; font-weight: 700; }
  footer .brand p span { display: block; margin-top: 0.55rem; font-size: 1rem; font-weight: 500; color: #b9c9de; }
  footer h4 { margin: 0.35rem 0 0.75rem; color: #fff; font-size: 0.9375rem; }
  footer ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; }
  footer a { color: #c8d6ea; text-decoration: none; }
  footer a:hover { color: #fff; text-decoration: underline; }
  footer .base { border-top: 1px solid rgba(255,255,255,.1); }
  footer .base .wrap { display: flex; justify-content: space-between; gap: 0.5rem 1.5rem; flex-wrap: wrap; padding-top: 1rem; padding-bottom: 1.25rem; font-size: 0.8125rem; color: #9fb1ca; }
  footer .base a { color: #c8d6ea; }
  footer .base b { color: #fff; font-weight: 700; }
  /* ---------- language: a flag that opens the list of languages ---------- */
  .langPick { position: relative; }
  .langCur { display: inline-flex; align-items: center; gap: 0.35rem; height: 2.1rem; padding: 0 0.5rem; border: 1px solid var(--line);
             background: var(--surface); color: var(--muted); border-radius: 0.5rem; font-size: 0.75rem; }
  .langCur:hover, .langPick.open .langCur { border-color: var(--link); color: var(--link); }
  .flag { width: 1.375rem; height: 0.95rem; border-radius: 2px; box-shadow: 0 0 0 1px rgba(0,0,0,.12); display: block; }
  .langList { position: absolute; top: calc(100% + 0.375rem); inset-inline-end: 0; z-index: 30; margin: 0; padding: 0.3rem; list-style: none;
              background: var(--surface); border: 1px solid var(--line); border-radius: 0.625rem; box-shadow: var(--shadow); min-width: 9rem; display: none; }
  .langPick.open .langList { display: block; }
  .langPick.up .langList { top: auto; bottom: calc(100% + 0.375rem); inset-inline-end: auto; inset-inline-start: 0; }
  .langList li { display: flex; align-items: center; gap: 0.6rem; padding: 0.45rem 0.6rem; border-radius: 0.45rem; cursor: pointer;
                 color: var(--title); font-weight: 600; font-size: 0.875rem; }
  .langList li:hover, .langList li:focus { background: var(--chip); outline: none; }
  .langList li.on::after { content: "✓"; margin-inline-start: auto; color: var(--link); }
  footer .langPick { margin-top: 1rem; display: inline-block; }
  footer .langCur { background: transparent; border-color: rgba(255,255,255,.25); color: #c8d6ea; }
  footer .langCur:hover, footer .langPick.open .langCur { border-color: #fff; color: #fff; }

  /* ---------- Hebrew: the page mirrors, the keyboard and the note sequence do not ---------- */
  html[dir="rtl"] details.method[open] summary .arr { transform: rotate(-90deg); }
  /* labels made of Latin letters and digits only: they would otherwise read backwards (3 2 1, −A) */
  #labels [data-l="num"], #labels [data-l="abc"], #smaller, #bigger, #kLearned, #replayTime { direction: ltr; unicode-bidi: isolate; }
  html[dir="rtl"] .progress i { background: linear-gradient(270deg, var(--blue), var(--sky)); }
  html[dir="rtl"] h1, html[dir="rtl"] .sechead h2 { letter-spacing: 0; }
  html[dir="rtl"] .kicker, html[dir="rtl"] .eyebrow { letter-spacing: .02em; }

  /* ---------- end of lesson, replay ---------- */
  .eyebrow { display: inline-block; font-size: 0.75rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase;
             color: var(--link); background: var(--chip2); padding: 0.3rem 0.7rem; border-radius: 99px; }
  #settings .sheet { --t-bg: var(--chip); --t-fg: var(--muted); --t-on-bg: var(--blue); --t-on-fg: #fff; }
  .setrows { margin: 1rem 0 0.75rem; }
  /* who is playing: a round initial, the same colour for the same name on every screen */
  .whobtn { display: inline-flex; align-items: center; gap: 0.45rem; border: 1px solid var(--line); background: var(--surface); color: var(--title);
         border-radius: 99px; padding: 0.2rem 0.75rem 0.2rem 0.2rem; font: inherit; font-weight: 700; font-size: 0.875rem; cursor: pointer; max-width: 11rem; }
  html[dir="rtl"] .whobtn { padding: 0.2rem 0.2rem 0.2rem 0.75rem; }
  .whobtn:hover { border-color: var(--link); }
  .whobtn .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .av { width: 1.75rem; height: 1.75rem; border-radius: 50%; display: inline-grid; place-items: center; flex: none;
        background: var(--avc, var(--blue)); color: #fff; font-weight: 800; font-size: 0.875rem; }
  html[data-device="phone"] .whobtn .nm { display: none; }
  html[data-device="phone"] .whobtn { padding: 0.15rem; }
  .proflist { display: grid; grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr)); gap: 0.625rem; margin: 1rem 0; }
  .prof { position: relative; display: flex; flex-direction: column; align-items: center; gap: 0.4rem; padding: 1rem 0.5rem 0.8rem;
          border: 2px solid var(--line); border-radius: 1rem; background: var(--surface2); color: var(--title); font: inherit; cursor: pointer; }
  .prof:hover { border-color: var(--link); }
  .prof.on { border-color: var(--blue); cursor: default; }
  .prof .av { width: 3rem; height: 3rem; font-size: 1.3rem; }
  .prof b { font-size: 0.9375rem; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .prof small { font-size: 0.75rem; color: var(--muted); min-height: 1em; }
  .prof.on small { color: var(--link); font-weight: 700; }
  .prof .del { position: absolute; top: 0.3rem; inset-inline-end: 0.3rem; width: 1.6rem; height: 1.6rem; border-radius: 50%; border: 0;
               background: transparent; color: var(--muted); font-size: 1rem; cursor: pointer; }
  .prof .del:hover { background: var(--bad); color: #fff; }
  .profpin { background: var(--chip); border-radius: 0.875rem; padding: 0.875rem; margin-bottom: 0.75rem; }
  .profpin label { display: block; font-weight: 700; color: var(--title); margin-bottom: 0.5rem; }
  .frow { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.5rem; }
  .frow input { flex: 1 1 9rem; min-width: 0; font: inherit; padding: 0.6rem 0.75rem; border-radius: 0.75rem; border: 1.5px solid var(--line);
                background: var(--surface); color: var(--title); }
  .profform { border-top: 1px solid var(--line); padding: 0.75rem 0; }
  .profform summary { cursor: pointer; font-weight: 700; color: var(--link); }
  .chk { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.5rem; font-size: 0.875rem; color: var(--muted); }
  .setrow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.7rem 0; border-bottom: 1px solid var(--line); }
  .setrow > span { font-weight: 700; color: var(--title); font-size: 0.9375rem; }
  .setrow small { display: block; font-weight: 400; color: var(--muted); font-size: 0.8125rem; margin-top: 0.15rem; }
  .setrow > b { color: var(--title); font-size: 0.9375rem; }
  .pals { display: flex; gap: 0.5rem; flex: none; }
  .pals button { width: 1.75rem; height: 1.75rem; border-radius: 50%; padding: 0; cursor: pointer;
                 border: 2px solid transparent; box-shadow: 0 0 0 1px var(--line);
                 background: linear-gradient(135deg, var(--sw) 0 50%, var(--sw2) 50% 100%); }
  .pals button.on { border-color: var(--bg); box-shadow: 0 0 0 2px var(--title); }
  #setSize b { align-self: center; min-width: 3.2rem; text-align: center; font-size: 0.8125rem; color: var(--title); }
  .setrow .live { flex: none; min-width: 4.5rem; justify-content: center; }

  /* edit mode: whole blocks are edited as free HTML, in the language on screen */
  /* the edit button lives in the top toolbar, anchored, next to the settings gear */
  #edToggleBtn.edtool { background: var(--gold); color: #1d1606; font-weight: 800; border: 0; }
  #edToggleBtn.edtool .edtxt { font-size: 0.85rem; }
  body.edMode #edToggleBtn.edtool { background: var(--navy); color: #fff; }
  html[data-theme="dark"] body.edMode #edToggleBtn.edtool { background: var(--gold); color: #1d1606; }
  html[data-device="phone"] #edToggleBtn.edtool .edtxt { display: none; }
  /* the scope panel floats on the left edge of the screen while editing, always in reach */
  #edBar { position: fixed; top: 50%; left: 0.8rem; transform: translateY(-50%); z-index: 75; display: none;
           flex-direction: column; gap: 0.4rem; width: 8.5rem; background: var(--surface); border: 1px solid var(--line);
           border-radius: 0.9rem; padding: 0.7rem; box-shadow: 0 0.6rem 1.6rem rgba(10, 27, 49, .28); }
  body.edMode #edBar { display: flex; }
  #edBar .edBarLbl { font-weight: 800; color: var(--title); font-size: 0.8rem; text-align: center; padding-bottom: 0.15rem; border-bottom: 1px solid var(--line); }
  #edBar .edSpacer { display: none; }
  #edBar button { width: 100%; border: 1px solid var(--line); background: var(--chip); color: var(--ink);
                  border-radius: 0.6rem; padding: 0.4rem 0.6rem; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
  #edBar button.on { background: var(--navy); color: #fff; border-color: var(--navy); }
  html[data-theme="dark"] #edBar button.on { background: var(--gold); color: #1d1606; }
  #edBar .edDone { background: var(--gold); color: #1d1606; border-color: var(--gold); margin-top: 0.15rem; }
  html[data-device="phone"] #edBar { top: auto; bottom: 0.8rem; transform: none; width: auto; flex-direction: row; flex-wrap: wrap; max-width: calc(100vw - 1.6rem); }
  html[data-device="phone"] #edBar button { width: auto; }
  html[data-device="phone"] #edBar .edBarLbl { width: 100%; border-bottom: 0; }
  body.edMode [data-tkey] { cursor: default; }
  body.edMode [data-tkey].edPick { outline: 2px dashed var(--gold2); outline-offset: 3px; border-radius: 0.25rem; cursor: pointer; }
  body.edMode [data-tkey].edPick:hover { background: rgba(226, 176, 74, .16); }
  #edModal { position: fixed; inset: 0; background: rgba(10,27,49,.55); display: none; place-items: center; z-index: 90; padding: 1.1rem; }
  #edModal.show { display: grid; }
  #edModal .edBox { background: var(--surface); color: var(--ink); border: 1px solid var(--line); border-radius: 1rem;
                    width: min(46rem, 100%); max-height: 92vh; display: flex; flex-direction: column; overflow: hidden;
                    box-shadow: 0 1rem 3rem rgba(10, 27, 49, .35); }
  #edModal .edTop { display: flex; align-items: baseline; gap: 0.6rem; padding: 0.9rem 1.1rem; border-bottom: 1px solid var(--line); }
  #edModal .edTop b { font-size: 1rem; color: var(--title); }
  #edModal .edTop .edKey { font-size: 0.78rem; color: var(--muted); direction: ltr; unicode-bidi: isolate; }
  #edModal .edTop .edLang { margin-inline-start: auto; font-size: 0.8rem; font-weight: 700; color: var(--link); }
  #edModal .edPrev { padding: 0.9rem 1.1rem; border-bottom: 1px dashed var(--line); overflow: auto; max-height: 34vh; background: var(--card); }
  #edModal .edPrevLbl { font-size: 0.72rem; color: var(--muted); padding: 0.5rem 1.1rem 0; }
  #edModal textarea { border: 0; border-top: 1px solid var(--line); resize: vertical; min-height: 8rem; padding: 0.8rem 1.1rem;
                      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.82rem; line-height: 1.5;
                      direction: ltr; text-align: left; background: var(--surface); color: var(--ink); outline: none; }
  #edModal .edImgTools { display: none; flex-wrap: wrap; align-items: center; gap: 0.35rem; padding: 0.6rem 1.1rem; border-top: 1px solid var(--line); background: var(--card); }
  #edModal .edImgTools.show { display: flex; }
  #edModal .edImgTools .edImgLbl { font-size: 0.78rem; font-weight: 700; color: var(--muted); margin-inline: 0.3rem 0.1rem; }
  #edModal .edImgTools button { border: 1px solid var(--line); background: var(--surface); color: var(--ink);
                                border-radius: 0.5rem; padding: 0.3rem 0.6rem; font-weight: 700; font-size: 0.78rem; cursor: pointer; }
  #edModal .edImgTools button:hover { border-color: var(--gold2); }
  #edModal .edBtns { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; padding: 0.75rem 1.1rem; border-top: 1px solid var(--line); }
  #edModal .edBtns .edSpacer { flex: 1; }
  #edModal .edBtns button { border: 0; border-radius: 0.55rem; padding: 0.45rem 0.9rem; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
  #edModal .edBtns .save { background: var(--gold); color: #1d1606; }
  #edModal .edBtns .ghost { background: var(--chip); color: var(--muted); }
  #edModal .edBtns .warn { background: #fbe4e4; color: #9a2020; }
  html[data-theme="dark"] #edModal .edBtns .warn { background: #3a1416; color: #ff8a8d; }
  .setnote { color: var(--muted); font-size: 0.8125rem; line-height: 1.6; margin: 0 0 1rem; }
  .modal { position: fixed; inset: 0; background: rgba(10,27,49,.55); display: none; place-items: center; z-index: 50; padding: 1.25rem; }
  .modal.show { display: grid; }
  #restoreBox { z-index: 60; }          /* above the settings sheet it is opened from */
  .sheet { background: var(--surface); border-radius: 1.25rem; width: min(35rem, 100%); padding: 1.75rem; position: relative; overflow: hidden; max-height: 90vh; overflow-y: auto; }
  .sheet h2 { margin: 0.375rem 0 0.25rem; color: var(--title); font-size: 1.75rem; }
  .sheet .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(8.125rem, 1fr)); gap: 0.625rem; margin: 1.125rem 0; }
  .sheet .kpis div { background: var(--chip); border-radius: 0.75rem; padding: 0.75rem; text-align: center; }
  .sheet .kpis b { display: block; font-size: 1.5rem; color: var(--title); } .sheet .kpis span { font-size: 0.75rem; color: var(--muted); }
  .sheet ul { margin: 0 0 1rem; padding-inline-start: 1.125rem; color: var(--muted); line-height: 1.7; }
  .sheet .actions { display: flex; gap: 0.625rem; flex-wrap: wrap; }
  .confetti { position: absolute; inset: 0; pointer-events: none; }
  .confetti i { position: absolute; top: -0.75rem; width: 0.5625rem; height: 0.875rem; border-radius: 2px; animation: fall 2.2s linear forwards; }
  .replaybar { display: none; align-items: center; gap: 0.875rem; background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.12);
               color: #fff; padding: 0.75rem 1rem; border-radius: 0.875rem; font-weight: 700; margin-bottom: 0.875rem; flex-wrap: wrap; }
  body.replaying .replaybar { display: flex; }
  .replaybar .name { font-size: 0.9375rem; color: #c8d6ea; }
  .rbtn { border: 0; width: 2.75rem; height: 2.75rem; border-radius: 50%; background: var(--gold); color: var(--navy); font-size: 1.125rem; font-weight: 900; flex: none; }
  .scrub { position: relative; flex: 1; min-width: 12.5rem; height: 2.125rem; direction: ltr; }
  .lanes { position: absolute; left: 0; right: 0; top: 0.25rem; height: 1.625rem; border-radius: 0.5rem; background: rgba(255,255,255,.06); overflow: hidden; }
  .lanes i { position: absolute; height: 0.6875rem; border-radius: 0.1875rem; }
  .lanes i.you { top: 2px; background: var(--you); } .lanes i.them { bottom: 2px; background: var(--them); }
  .scrub input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: pointer; }
  .head { position: absolute; top: 0; bottom: 0; width: 0.1875rem; margin-left: -1px; background: #fff; border-radius: 2px; box-shadow: 0 0 0.5rem #fff; pointer-events: none; }
  .legend { display: inline-flex; gap: 0.75rem; font-size: 0.8125rem; color: #c8d6ea; }
  .legend b { display: inline-block; width: 0.75rem; height: 0.75rem; border-radius: 0.1875rem; margin-inline-end: 0.3125rem; vertical-align: -1px; }
  .who { display: none; font-weight: 800; font-size: 1.125rem; padding: 0.375rem 0.875rem; border-radius: 0.75rem; margin-bottom: 0.625rem; }
  body.replaying .who { display: inline-block; }
  .who.you { background: var(--you); color: var(--navy); } .who.them { background: var(--them); color: var(--navy); }
  .pedal { display: none; font-size: 0.8125rem; font-weight: 700; padding: 0.25rem 0.625rem; border-radius: 99px; background: rgba(255,255,255,.1); color: #c8d6ea; }
  body.replaying .pedal { display: inline-block; } .pedal.down { background: var(--you); color: var(--navy); }
  .lit.them { background: var(--them) !important; box-shadow: 0 0 1.5rem 0.1875rem var(--them), inset 0 -0.375rem 0 rgba(0,0,0,.18) !important; }

  @keyframes pop  { 50% { transform: scale(1.06); } }
  @keyframes glow { 50% { filter: brightness(1.2); } }
  @keyframes beat { 50% { box-shadow: 0 0 0 0.3125rem rgba(255,255,255,0); } }
  @keyframes shake { 25% { transform: translate(-2px, 0.1875rem); } 75% { transform: translate(2px, 0.1875rem); } }
  @keyframes fly  { from { transform: translate(-50%, -50%); opacity: 1; } to { transform: translate(calc(-50% + var(--x)), calc(-50% + var(--y))) scale(.4); opacity: 0; } }
  @keyframes fall { to { transform: translateY(110vh) rotate(540deg); } }

  /* ---------- devices ---------- */
  html[data-device="tablet"] h1 { font-size: 2.375rem; }
  html[data-device="phone"] .wrap { padding: 0 1rem; }
  html[data-device="phone"] header.top .wrap { height: 3.5rem; gap: 0.5rem; }
  html[data-device="phone"] .logo svg { height: 1.875rem; }
  /* a phone header has room for the house only; the name is in the banner below it */
  html[data-device="phone"] .logo .wd { display: none !important; }
  /* at the head of the page the banner already shows the name, so the menu carries the house alone */
  html[data-atop="yes"] .logo .wd { display: none !important; }
  html[data-device="phone"] nav { display: none; }
  html[data-device="phone"] .logo { flex: 1; }
  html[data-device="phone"] .pill span { display: none; }
  html[data-device="phone"] .pill { padding: 0.5rem; }
  html[data-device="phone"] .langCur { height: 1.9rem; padding: 0 0.35rem; }
  html[data-device="phone"] .tools button { min-width: 1.9rem; height: 1.9rem; padding: 0 0.35rem; font-size: 0.8rem; }
  html[data-device="phone"] .band .wrap { padding-top: 1rem; padding-bottom: 1.75rem; }
  html[data-device="phone"] h1 { font-size: 1.875rem; }
  html[data-device="phone"] .today { padding: 1.25rem; }
  html[data-device="phone"] .today .row { gap: 0.875rem; }
  html[data-device="phone"] .today .song { font-size: 1.3125rem; }
  html[data-device="phone"] .two, html[data-device="phone"] .deep { grid-template-columns: 1fr; }
  html[data-device="phone"] section.block, html[data-device="phone"] .stage { padding: 2.25rem 0; }
  html[data-device="phone"] footer .main { grid-template-columns: 1fr 1fr; }
  html[data-device="phone"] footer .brand { grid-column: 1 / -1; }
  html[data-device="phone"] .lesson { grid-template-columns: 1fr; gap: 0.75rem; }
  html[data-device="phone"] .sticker { width: 6.25rem; height: 6.25rem; font-size: 2.875rem; }
  html[data-device="phone"] .bead { min-width: 2.5rem; font-size: 1.125rem; padding: 0.375rem 0.4375rem; border-radius: 0.75rem; }
  html[data-device="phone"] .toolbar { flex-wrap: wrap; overflow-x: visible; }
  html[data-device="phone"] .toolbar .title { width: 100%; flex: none; overflow-x: auto; scrollbar-width: none; }
  html[data-device="phone"][data-orient="landscape"] .lesson { grid-template-columns: 10rem 1fr; }
  html[data-device="phone"][data-orient="landscape"] .sticker { width: 4rem; height: 4rem; font-size: 1.875rem; margin: 0.25rem 0; }
  html[data-device="phone"][data-orient="landscape"] body.focus .stage { min-height: 0; }
  @media (max-width: 980px) {
    html[data-device="desktop"] .two, html[data-device="desktop"] .deep { grid-template-columns: 1fr; }
    html[data-device="desktop"] nav { display: none; }
  }
  @media (max-width: 640px) {
    html[data-device="desktop"] footer .main { grid-template-columns: 1fr 1fr; }
    html[data-device="desktop"] footer .brand { grid-column: 1 / -1; }
  }
</style>
</head>
<body>
<header class="top">
  <div class="wrap">
    <a class="logo" href="/" aria-label="Armonico"><span class="ic"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120"><defs><linearGradient id="hi-ailight" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#8AD6FF"/><stop offset="1" stop-color="#1D6BE0"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A" stroke="#0E2A47" stroke-width="2.4"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#hi-ailight)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="wd for-light"><svg xmlns="http://www.w3.org/2000/svg" viewBox="150 0 276 120" width="276" height="120"><path fill="#0E2A47" d="M156.368 68.48H180.048L178.64 62.08H157.84ZM168.08 47.872 175.248 64.512 175.376 66.368 181.45600000000002 80.0H190.032L168.08 32.704L146.192 80.0H154.704L160.912 65.984L161.04 64.32ZM201.424 50.56H194.576V80.0H201.424ZM210.192 57.92 213.584 52.096000000000004Q212.56 50.879999999999995 211.152 50.367999999999995Q209.744 49.855999999999995 208.144 49.855999999999995Q205.904 49.855999999999995 203.824 51.488Q201.744 53.120000000000005 200.43200000000002 55.84Q199.12 58.56 199.12 62.08L201.424 63.424Q201.424 61.312 201.904 59.744Q202.384 58.176 203.47199999999998 57.28Q204.56 56.384 206.288 56.384Q207.56799999999998 56.384 208.432 56.768Q209.296 57.152 210.192 57.92ZM260.496 61.248000000000005Q260.496 57.664 259.44 55.104Q258.384 52.544 256.336 51.232Q254.288 49.92 251.088 49.92Q248.144 49.92 245.808 51.232Q243.472 52.544 241.87199999999999 55.168Q240.912 52.608000000000004 238.768 51.264Q236.624 49.92 233.424 49.92Q230.54399999999998 49.92 228.49599999999998 51.168Q226.448 52.416 225.10399999999998 54.848V50.56H218.32V80.0H225.10399999999998V62.08Q225.10399999999998 59.968 225.83999999999997 58.496Q226.576 57.024 227.88799999999998 56.256Q229.2 55.488 230.928 55.488Q233.488 55.488 234.672 57.12Q235.856 58.751999999999995 235.856 62.08V80.0H242.768V62.08Q242.768 59.968 243.50400000000002 58.496Q244.24 57.024 245.55200000000002 56.256Q246.864 55.488 248.656 55.488Q251.152 55.488 252.33599999999998 57.12Q253.51999999999998 58.751999999999995 253.51999999999998 62.08V80.0H260.496ZM267.024 65.28Q267.024 69.76 269.10400000000004 73.248Q271.184 76.736 274.8 78.688Q278.416 80.64 282.896 80.64Q287.44 80.64 291.024 78.688Q294.608 76.736 296.688 73.248Q298.76800000000003 69.76 298.76800000000003 65.28Q298.76800000000003 60.736000000000004 296.688 57.28Q294.608 53.824 291.024 51.872Q287.44 49.92 282.896 49.92Q278.416 49.92 274.8 51.872Q271.184 53.824 269.10400000000004 57.28Q267.024 60.736000000000004 267.024 65.28ZM274.128 65.28Q274.128 62.528 275.28 60.416Q276.432 58.304 278.41600000000005 57.152Q280.40000000000003 56.0 282.896 56.0Q285.392 56.0 287.376 57.152Q289.36 58.304 290.512 60.416Q291.664 62.528 291.664 65.28Q291.664 68.032 290.512 70.112Q289.36 72.19200000000001 287.376 73.376Q285.392 74.56 282.896 74.56Q280.40000000000003 74.56 278.41600000000005 73.376Q276.432 72.19200000000001 275.28 70.112Q274.128 68.032 274.128 65.28ZM324.30400000000003 62.08V80.0H331.408V61.248000000000005Q331.408 56.0 328.784 52.96Q326.16 49.92 321.168 49.92Q318.16 49.92 315.952 51.2Q313.744 52.480000000000004 312.336 55.104V50.56H305.36V80.0H312.336V62.08Q312.336 60.096000000000004 313.136 58.592Q313.93600000000004 57.088 315.408 56.288Q316.88 55.488 318.86400000000003 55.488Q321.61600000000004 55.488 322.96000000000004 57.152Q324.30400000000003 58.816 324.30400000000003 62.08ZM339.98400000000004 38.848Q339.98400000000004 40.576 341.29600000000005 41.824Q342.608 43.072 344.336 43.072Q346.192 43.072 347.472 41.824Q348.752 40.576 348.752 38.848Q348.752 37.056 347.472 35.84Q346.192 34.624 344.336 34.624Q342.608 34.624 341.29600000000005 35.84Q339.98400000000004 37.056 339.98400000000004 38.848ZM340.944 50.56V80.0H347.79200000000003V50.56ZM361.93600000000004 65.28Q361.93600000000004 62.528 363.15200000000004 60.416Q364.36800000000005 58.304 366.44800000000004 57.088Q368.528 55.872 371.088 55.872Q373.136 55.872 375.05600000000004 56.512Q376.97600000000006 57.152 378.48 58.272Q379.98400000000004 59.391999999999996 380.68800000000005 60.864000000000004V53.184Q379.15200000000004 51.712 376.528 50.816Q373.90400000000005 49.92 370.76800000000003 49.92Q366.288 49.92 362.672 51.872Q359.05600000000004 53.824 356.9440000000001 57.28Q354.83200000000005 60.736000000000004 354.83200000000005 65.28Q354.83200000000005 69.76 356.9440000000001 73.248Q359.05600000000004 76.736 362.672 78.688Q366.288 80.64 370.76800000000003 80.64Q373.90400000000005 80.64 376.528 79.744Q379.15200000000004 78.848 380.68800000000005 77.312V69.696Q379.98400000000004 71.104 378.51200000000006 72.22399999999999Q377.04 73.344 375.15200000000004 74.01599999999999Q373.264 74.688 371.088 74.688Q368.528 74.688 366.44800000000004 73.47200000000001Q364.36800000000005 72.256 363.15200000000004 70.144Q361.93600000000004 68.032 361.93600000000004 65.28ZM386.064 65.28Q386.064 69.76 388.144 73.248Q390.22400000000005 76.736 393.84000000000003 78.688Q397.456 80.64 401.93600000000004 80.64Q406.48 80.64 410.064 78.688Q413.648 76.736 415.72800000000007 73.248Q417.80800000000005 69.76 417.80800000000005 65.28Q417.80800000000005 60.736000000000004 415.72800000000007 57.28Q413.648 53.824 410.064 51.872Q406.48 49.92 401.93600000000004 49.92Q397.456 49.92 393.84000000000003 51.872Q390.22400000000005 53.824 388.144 57.28Q386.064 60.736000000000004 386.064 65.28ZM393.168 65.28Q393.168 62.528 394.32000000000005 60.416Q395.47200000000004 58.304 397.456 57.152Q399.44000000000005 56.0 401.93600000000004 56.0Q404.432 56.0 406.41600000000005 57.152Q408.40000000000003 58.304 409.552 60.416Q410.704 62.528 410.704 65.28Q410.704 68.032 409.552 70.112Q408.40000000000003 72.19200000000001 406.41600000000005 73.376Q404.432 74.56 401.93600000000004 74.56Q399.44000000000005 74.56 397.456 73.376Q395.47200000000004 72.19200000000001 394.32000000000005 70.112Q393.168 68.032 393.168 65.28Z"/></svg></span><span class="wd for-dark"><svg xmlns="http://www.w3.org/2000/svg" viewBox="150 0 276 120" width="276" height="120"><path fill="#EEF3FB" d="M156.368 68.48H180.048L178.64 62.08H157.84ZM168.08 47.872 175.248 64.512 175.376 66.368 181.45600000000002 80.0H190.032L168.08 32.704L146.192 80.0H154.704L160.912 65.984L161.04 64.32ZM201.424 50.56H194.576V80.0H201.424ZM210.192 57.92 213.584 52.096000000000004Q212.56 50.879999999999995 211.152 50.367999999999995Q209.744 49.855999999999995 208.144 49.855999999999995Q205.904 49.855999999999995 203.824 51.488Q201.744 53.120000000000005 200.43200000000002 55.84Q199.12 58.56 199.12 62.08L201.424 63.424Q201.424 61.312 201.904 59.744Q202.384 58.176 203.47199999999998 57.28Q204.56 56.384 206.288 56.384Q207.56799999999998 56.384 208.432 56.768Q209.296 57.152 210.192 57.92ZM260.496 61.248000000000005Q260.496 57.664 259.44 55.104Q258.384 52.544 256.336 51.232Q254.288 49.92 251.088 49.92Q248.144 49.92 245.808 51.232Q243.472 52.544 241.87199999999999 55.168Q240.912 52.608000000000004 238.768 51.264Q236.624 49.92 233.424 49.92Q230.54399999999998 49.92 228.49599999999998 51.168Q226.448 52.416 225.10399999999998 54.848V50.56H218.32V80.0H225.10399999999998V62.08Q225.10399999999998 59.968 225.83999999999997 58.496Q226.576 57.024 227.88799999999998 56.256Q229.2 55.488 230.928 55.488Q233.488 55.488 234.672 57.12Q235.856 58.751999999999995 235.856 62.08V80.0H242.768V62.08Q242.768 59.968 243.50400000000002 58.496Q244.24 57.024 245.55200000000002 56.256Q246.864 55.488 248.656 55.488Q251.152 55.488 252.33599999999998 57.12Q253.51999999999998 58.751999999999995 253.51999999999998 62.08V80.0H260.496ZM267.024 65.28Q267.024 69.76 269.10400000000004 73.248Q271.184 76.736 274.8 78.688Q278.416 80.64 282.896 80.64Q287.44 80.64 291.024 78.688Q294.608 76.736 296.688 73.248Q298.76800000000003 69.76 298.76800000000003 65.28Q298.76800000000003 60.736000000000004 296.688 57.28Q294.608 53.824 291.024 51.872Q287.44 49.92 282.896 49.92Q278.416 49.92 274.8 51.872Q271.184 53.824 269.10400000000004 57.28Q267.024 60.736000000000004 267.024 65.28ZM274.128 65.28Q274.128 62.528 275.28 60.416Q276.432 58.304 278.41600000000005 57.152Q280.40000000000003 56.0 282.896 56.0Q285.392 56.0 287.376 57.152Q289.36 58.304 290.512 60.416Q291.664 62.528 291.664 65.28Q291.664 68.032 290.512 70.112Q289.36 72.19200000000001 287.376 73.376Q285.392 74.56 282.896 74.56Q280.40000000000003 74.56 278.41600000000005 73.376Q276.432 72.19200000000001 275.28 70.112Q274.128 68.032 274.128 65.28ZM324.30400000000003 62.08V80.0H331.408V61.248000000000005Q331.408 56.0 328.784 52.96Q326.16 49.92 321.168 49.92Q318.16 49.92 315.952 51.2Q313.744 52.480000000000004 312.336 55.104V50.56H305.36V80.0H312.336V62.08Q312.336 60.096000000000004 313.136 58.592Q313.93600000000004 57.088 315.408 56.288Q316.88 55.488 318.86400000000003 55.488Q321.61600000000004 55.488 322.96000000000004 57.152Q324.30400000000003 58.816 324.30400000000003 62.08ZM339.98400000000004 38.848Q339.98400000000004 40.576 341.29600000000005 41.824Q342.608 43.072 344.336 43.072Q346.192 43.072 347.472 41.824Q348.752 40.576 348.752 38.848Q348.752 37.056 347.472 35.84Q346.192 34.624 344.336 34.624Q342.608 34.624 341.29600000000005 35.84Q339.98400000000004 37.056 339.98400000000004 38.848ZM340.944 50.56V80.0H347.79200000000003V50.56ZM361.93600000000004 65.28Q361.93600000000004 62.528 363.15200000000004 60.416Q364.36800000000005 58.304 366.44800000000004 57.088Q368.528 55.872 371.088 55.872Q373.136 55.872 375.05600000000004 56.512Q376.97600000000006 57.152 378.48 58.272Q379.98400000000004 59.391999999999996 380.68800000000005 60.864000000000004V53.184Q379.15200000000004 51.712 376.528 50.816Q373.90400000000005 49.92 370.76800000000003 49.92Q366.288 49.92 362.672 51.872Q359.05600000000004 53.824 356.9440000000001 57.28Q354.83200000000005 60.736000000000004 354.83200000000005 65.28Q354.83200000000005 69.76 356.9440000000001 73.248Q359.05600000000004 76.736 362.672 78.688Q366.288 80.64 370.76800000000003 80.64Q373.90400000000005 80.64 376.528 79.744Q379.15200000000004 78.848 380.68800000000005 77.312V69.696Q379.98400000000004 71.104 378.51200000000006 72.22399999999999Q377.04 73.344 375.15200000000004 74.01599999999999Q373.264 74.688 371.088 74.688Q368.528 74.688 366.44800000000004 73.47200000000001Q364.36800000000005 72.256 363.15200000000004 70.144Q361.93600000000004 68.032 361.93600000000004 65.28ZM386.064 65.28Q386.064 69.76 388.144 73.248Q390.22400000000005 76.736 393.84000000000003 78.688Q397.456 80.64 401.93600000000004 80.64Q406.48 80.64 410.064 78.688Q413.648 76.736 415.72800000000007 73.248Q417.80800000000005 69.76 417.80800000000005 65.28Q417.80800000000005 60.736000000000004 415.72800000000007 57.28Q413.648 53.824 410.064 51.872Q406.48 49.92 401.93600000000004 49.92Q397.456 49.92 393.84000000000003 51.872Q390.22400000000005 53.824 388.144 57.28Q386.064 60.736000000000004 386.064 65.28ZM393.168 65.28Q393.168 62.528 394.32000000000005 60.416Q395.47200000000004 58.304 397.456 57.152Q399.44000000000005 56.0 401.93600000000004 56.0Q404.432 56.0 406.41600000000005 57.152Q408.40000000000003 58.304 409.552 60.416Q410.704 62.528 410.704 65.28Q410.704 68.032 409.552 70.112Q408.40000000000003 72.19200000000001 406.41600000000005 73.376Q404.432 74.56 401.93600000000004 74.56Q399.44000000000005 74.56 397.456 73.376Q395.47200000000004 72.19200000000001 394.32000000000005 70.112Q393.168 68.032 393.168 65.28Z"/></svg></span></a>
    <nav id="nav"><a href="/#play" data-tkey="nav.play">Play</a><a href="/#songs" data-tkey="nav.songs">Songs</a><a href="/#progress" data-tkey="nav.progress">Progress</a><a href="/about" data-tkey="nav.about">About</a></nav>
    <div class="tools"><button id="smaller" title="Smaller text">A−</button><button id="bigger" title="Bigger text">A+</button><button id="theme" title="Dark or light"></button><button id="setBtn" title="Settings" aria-haspopup="dialog">⚙</button><button id="edToggleBtn" class="edtool" title="Edit the page">✎ <span class="edtxt">Edit</span></button></div>
    <button class="whobtn idleonly" id="whoBtn" title="Who is playing" aria-haspopup="dialog"><span class="av" id="whoAv"></span><span class="nm" id="whoName"></span></button>
    <span class="pill" id="pill"><i></i><span>Checking…</span></span>
    <div class="langPick"></div>
  </div>
</header>

<div class="arm-banner idleonly" role="img" aria-label="Armonico, Your piano talks back"><div class="wide"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 426.0 120" width="426" height="120"><defs><linearGradient id="bw-acdark" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#B8E8FF"/><stop offset="1" stop-color="#5AA8F5"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#bw-acdark)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><path fill="#EEF3FB" d="M156.368 55.480000000000004H180.048L178.64 49.08H157.84ZM168.08 34.872 175.248 51.512 175.376 53.368 181.45600000000002 67.0H190.032L168.08 19.704L146.192 67.0H154.704L160.912 52.984L161.04 51.32ZM201.424 37.56H194.576V67.0H201.424ZM210.192 44.92 213.584 39.096000000000004Q212.56 37.879999999999995 211.152 37.367999999999995Q209.744 36.855999999999995 208.144 36.855999999999995Q205.904 36.855999999999995 203.824 38.488Q201.744 40.120000000000005 200.43200000000002 42.84Q199.12 45.56 199.12 49.08L201.424 50.424Q201.424 48.312 201.904 46.744Q202.384 45.176 203.47199999999998 44.28Q204.56 43.384 206.288 43.384Q207.56799999999998 43.384 208.432 43.768Q209.296 44.152 210.192 44.92ZM260.496 48.248000000000005Q260.496 44.664 259.44 42.104Q258.384 39.544 256.336 38.232Q254.288 36.92 251.088 36.92Q248.144 36.92 245.808 38.232Q243.472 39.544 241.87199999999999 42.168Q240.912 39.608000000000004 238.768 38.264Q236.624 36.92 233.424 36.92Q230.54399999999998 36.92 228.49599999999998 38.168Q226.448 39.416 225.10399999999998 41.848V37.56H218.32V67.0H225.10399999999998V49.08Q225.10399999999998 46.968 225.83999999999997 45.496Q226.576 44.024 227.88799999999998 43.256Q229.2 42.488 230.928 42.488Q233.488 42.488 234.672 44.12Q235.856 45.751999999999995 235.856 49.08V67.0H242.768V49.08Q242.768 46.968 243.50400000000002 45.496Q244.24 44.024 245.55200000000002 43.256Q246.864 42.488 248.656 42.488Q251.152 42.488 252.33599999999998 44.12Q253.51999999999998 45.751999999999995 253.51999999999998 49.08V67.0H260.496ZM267.024 52.28Q267.024 56.76 269.10400000000004 60.248Q271.184 63.736 274.8 65.688Q278.416 67.64 282.896 67.64Q287.44 67.64 291.024 65.688Q294.608 63.736 296.688 60.248Q298.76800000000003 56.76 298.76800000000003 52.28Q298.76800000000003 47.736000000000004 296.688 44.28Q294.608 40.824 291.024 38.872Q287.44 36.92 282.896 36.92Q278.416 36.92 274.8 38.872Q271.184 40.824 269.10400000000004 44.28Q267.024 47.736000000000004 267.024 52.28ZM274.128 52.28Q274.128 49.528 275.28 47.416Q276.432 45.304 278.41600000000005 44.152Q280.40000000000003 43.0 282.896 43.0Q285.392 43.0 287.376 44.152Q289.36 45.304 290.512 47.416Q291.664 49.528 291.664 52.28Q291.664 55.032 290.512 57.111999999999995Q289.36 59.192 287.376 60.376000000000005Q285.392 61.56 282.896 61.56Q280.40000000000003 61.56 278.41600000000005 60.376000000000005Q276.432 59.192 275.28 57.111999999999995Q274.128 55.032 274.128 52.28ZM324.30400000000003 49.08V67.0H331.408V48.248000000000005Q331.408 43.0 328.784 39.96Q326.16 36.92 321.168 36.92Q318.16 36.92 315.952 38.2Q313.744 39.480000000000004 312.336 42.104V37.56H305.36V67.0H312.336V49.08Q312.336 47.096000000000004 313.136 45.592Q313.93600000000004 44.088 315.408 43.288Q316.88 42.488 318.86400000000003 42.488Q321.61600000000004 42.488 322.96000000000004 44.152Q324.30400000000003 45.816 324.30400000000003 49.08ZM339.98400000000004 25.848Q339.98400000000004 27.576 341.29600000000005 28.824Q342.608 30.072000000000003 344.336 30.072000000000003Q346.192 30.072000000000003 347.472 28.824Q348.752 27.576 348.752 25.848Q348.752 24.055999999999997 347.472 22.84Q346.192 21.624000000000002 344.336 21.624000000000002Q342.608 21.624000000000002 341.29600000000005 22.84Q339.98400000000004 24.055999999999997 339.98400000000004 25.848ZM340.944 37.56V67.0H347.79200000000003V37.56ZM361.93600000000004 52.28Q361.93600000000004 49.528 363.15200000000004 47.416Q364.36800000000005 45.304 366.44800000000004 44.088Q368.528 42.872 371.088 42.872Q373.136 42.872 375.05600000000004 43.512Q376.97600000000006 44.152 378.48 45.272Q379.98400000000004 46.391999999999996 380.68800000000005 47.864000000000004V40.184Q379.15200000000004 38.712 376.528 37.816Q373.90400000000005 36.92 370.76800000000003 36.92Q366.288 36.92 362.672 38.872Q359.05600000000004 40.824 356.9440000000001 44.28Q354.83200000000005 47.736000000000004 354.83200000000005 52.28Q354.83200000000005 56.76 356.9440000000001 60.248Q359.05600000000004 63.736 362.672 65.688Q366.288 67.64 370.76800000000003 67.64Q373.90400000000005 67.64 376.528 66.744Q379.15200000000004 65.848 380.68800000000005 64.312V56.696Q379.98400000000004 58.104 378.51200000000006 59.224000000000004Q377.04 60.344 375.15200000000004 61.016000000000005Q373.264 61.688 371.088 61.688Q368.528 61.688 366.44800000000004 60.472Q364.36800000000005 59.256 363.15200000000004 57.144Q361.93600000000004 55.032 361.93600000000004 52.28ZM386.064 52.28Q386.064 56.76 388.144 60.248Q390.22400000000005 63.736 393.84000000000003 65.688Q397.456 67.64 401.93600000000004 67.64Q406.48 67.64 410.064 65.688Q413.648 63.736 415.72800000000007 60.248Q417.80800000000005 56.76 417.80800000000005 52.28Q417.80800000000005 47.736000000000004 415.72800000000007 44.28Q413.648 40.824 410.064 38.872Q406.48 36.92 401.93600000000004 36.92Q397.456 36.92 393.84000000000003 38.872Q390.22400000000005 40.824 388.144 44.28Q386.064 47.736000000000004 386.064 52.28ZM393.168 52.28Q393.168 49.528 394.32000000000005 47.416Q395.47200000000004 45.304 397.456 44.152Q399.44000000000005 43.0 401.93600000000004 43.0Q404.432 43.0 406.41600000000005 44.152Q408.40000000000003 45.304 409.552 47.416Q410.704 49.528 410.704 52.28Q410.704 55.032 409.552 57.111999999999995Q408.40000000000003 59.192 406.41600000000005 60.376000000000005Q404.432 61.56 401.93600000000004 61.56Q399.44000000000005 61.56 397.456 60.376000000000005Q395.47200000000004 59.192 394.32000000000005 57.111999999999995Q393.168 55.032 393.168 52.28Z"/><path fill="#DBE4F0" d="M187.01599999999993 91.2 182.67199999999994 98.976 178.35199999999995 91.2H176.07199999999995L181.66399999999993 100.776V108.0H183.70399999999995V100.752L189.29599999999994 91.2ZM187.85599999999994 102.48Q187.85599999999994 104.136 188.61199999999994 105.44399999999999Q189.36799999999994 106.752 190.66399999999993 107.496Q191.95999999999992 108.24 193.56799999999993 108.24Q195.19999999999993 108.24 196.48399999999992 107.496Q197.76799999999994 106.752 198.52399999999994 105.44399999999999Q199.27999999999994 104.136 199.27999999999994 102.48Q199.27999999999994 100.8 198.52399999999994 99.50399999999999Q197.76799999999994 98.208 196.48399999999992 97.464Q195.19999999999993 96.72 193.56799999999993 96.72Q191.95999999999992 96.72 190.66399999999993 97.464Q189.36799999999994 98.208 188.61199999999994 99.50399999999999Q187.85599999999994 100.8 187.85599999999994 102.48ZM189.79999999999993 102.48Q189.79999999999993 101.328 190.29199999999992 100.428Q190.78399999999993 99.528 191.63599999999994 99.024Q192.48799999999994 98.52 193.56799999999993 98.52Q194.64799999999994 98.52 195.49999999999994 99.024Q196.35199999999995 99.528 196.84399999999994 100.428Q197.33599999999993 101.328 197.33599999999993 102.48Q197.33599999999993 103.632 196.84399999999994 104.52000000000001Q196.35199999999995 105.408 195.49999999999994 105.924Q194.64799999999994 106.44 193.56799999999993 106.44Q192.48799999999994 106.44 191.63599999999994 105.924Q190.78399999999993 105.408 190.29199999999992 104.52000000000001Q189.79999999999993 103.632 189.79999999999993 102.48ZM203.83999999999995 103.68V96.96H201.91999999999996V103.92Q201.91999999999996 105.888 202.95199999999994 107.064Q203.98399999999995 108.24 205.71199999999996 108.24Q206.81599999999995 108.24 207.63199999999995 107.74799999999999Q208.44799999999995 107.256 208.99999999999994 106.272V108.0H210.91999999999996V96.96H208.99999999999994V103.68Q208.99999999999994 104.496 208.65199999999993 105.12Q208.30399999999995 105.744 207.66799999999995 106.092Q207.03199999999995 106.44 206.19199999999995 106.44Q205.03999999999994 106.44 204.43999999999994 105.72Q203.83999999999995 105.0 203.83999999999995 103.68ZM216.43999999999994 96.96H214.51999999999995V108.0H216.43999999999994ZM219.77599999999995 99.072 220.83199999999994 97.488Q220.39999999999995 97.032 219.88399999999996 96.876Q219.36799999999994 96.72 218.76799999999994 96.72Q217.99999999999994 96.72 217.25599999999994 97.32Q216.51199999999994 97.92 216.04399999999993 98.94Q215.57599999999994 99.96 215.57599999999994 101.28H216.43999999999994Q216.43999999999994 100.488 216.59599999999995 99.864Q216.75199999999995 99.24 217.15999999999997 98.88Q217.56799999999996 98.52 218.28799999999995 98.52Q218.76799999999994 98.52 219.07999999999993 98.65199999999999Q219.39199999999994 98.78399999999999 219.77599999999995 99.072ZM231.75199999999992 113.28V96.96H229.83199999999994V113.28ZM240.99199999999993 102.48Q240.99199999999993 100.68 240.24799999999993 99.396Q239.50399999999993 98.112 238.26799999999992 97.416Q237.03199999999993 96.72 235.51999999999992 96.72Q234.15199999999993 96.72 233.10799999999995 97.416Q232.06399999999994 98.112 231.47599999999994 99.396Q230.88799999999992 100.68 230.88799999999992 102.48Q230.88799999999992 104.256 231.47599999999994 105.55199999999999Q232.06399999999994 106.848 233.10799999999995 107.544Q234.15199999999993 108.24 235.51999999999992 108.24Q237.03199999999993 108.24 238.26799999999992 107.544Q239.50399999999993 106.848 240.24799999999993 105.55199999999999Q240.99199999999993 104.256 240.99199999999993 102.48ZM239.04799999999994 102.48Q239.04799999999994 103.752 238.53199999999993 104.64Q238.01599999999993 105.528 237.16399999999993 105.98400000000001Q236.31199999999993 106.44 235.27999999999992 106.44Q234.43999999999994 106.44 233.62399999999994 105.98400000000001Q232.80799999999994 105.528 232.27999999999992 104.64Q231.75199999999992 103.752 231.75199999999992 102.48Q231.75199999999992 101.208 232.27999999999992 100.32Q232.80799999999994 99.432 233.62399999999994 98.976Q234.43999999999994 98.52 235.27999999999992 98.52Q236.31199999999993 98.52 237.16399999999993 98.976Q238.01599999999993 99.432 238.53199999999993 100.32Q239.04799999999994 101.208 239.04799999999994 102.48ZM243.63199999999995 92.4Q243.63199999999995 92.928 244.02799999999996 93.324Q244.42399999999995 93.72 244.95199999999994 93.72Q245.50399999999993 93.72 245.88799999999992 93.324Q246.27199999999993 92.928 246.27199999999993 92.4Q246.27199999999993 91.848 245.88799999999992 91.464Q245.50399999999993 91.08 244.95199999999994 91.08Q244.42399999999995 91.08 244.02799999999996 91.464Q243.63199999999995 91.848 243.63199999999995 92.4ZM243.99199999999993 96.96V108.0H245.91199999999995V96.96ZM250.73599999999993 104.592Q250.73599999999993 103.992 251.02399999999994 103.56Q251.31199999999995 103.128 251.89999999999995 102.888Q252.48799999999994 102.648 253.42399999999995 102.648Q254.43199999999996 102.648 255.31999999999996 102.9Q256.20799999999997 103.152 257.04799999999994 103.728V102.6Q256.87999999999994 102.384 256.4 102.036Q255.91999999999996 101.688 255.11599999999996 101.412Q254.31199999999995 101.136 253.11199999999994 101.136Q251.07199999999995 101.136 249.93199999999996 102.108Q248.79199999999994 103.08 248.79199999999994 104.688Q248.79199999999994 105.816 249.31999999999994 106.608Q249.84799999999996 107.4 250.72399999999993 107.82Q251.59999999999994 108.24 252.60799999999995 108.24Q253.51999999999995 108.24 254.44399999999996 107.904Q255.36799999999994 107.568 256.0039999999999 106.872Q256.63999999999993 106.176 256.63999999999993 105.12L256.256 103.68Q256.256 104.544 255.83599999999996 105.20400000000001Q255.41599999999994 105.864 254.70799999999994 106.224Q253.99999999999994 106.584 253.11199999999994 106.584Q252.41599999999994 106.584 251.87599999999995 106.356Q251.33599999999996 106.128 251.03599999999994 105.672Q250.73599999999993 105.216 250.73599999999993 104.592ZM250.44799999999995 99.504Q250.71199999999993 99.312 251.16799999999995 99.048Q251.62399999999994 98.78399999999999 252.28399999999993 98.592Q252.94399999999996 98.4 253.75999999999993 98.4Q254.26399999999995 98.4 254.71999999999997 98.49600000000001Q255.17599999999996 98.592 255.52399999999994 98.80799999999999Q255.87199999999996 99.024 256.06399999999996 99.396Q256.256 99.768 256.256 100.344V108.0H258.17599999999993V100.08Q258.17599999999993 99.0 257.63599999999997 98.256Q257.09599999999995 97.512 256.12399999999997 97.116Q255.15199999999996 96.72 253.85599999999994 96.72Q252.31999999999994 96.72 251.22799999999995 97.176Q250.13599999999994 97.632 249.51199999999994 98.088ZM268.4959999999999 101.28V108.0H270.41599999999994V101.04Q270.41599999999994 99.048 269.39599999999996 97.884Q268.3759999999999 96.72 266.6239999999999 96.72Q265.5439999999999 96.72 264.7159999999999 97.2Q263.8879999999999 97.68 263.33599999999996 98.688V96.96H261.41599999999994V108.0H263.33599999999996V101.28Q263.33599999999996 100.464 263.68399999999997 99.84Q264.0319999999999 99.216 264.6679999999999 98.868Q265.3039999999999 98.52 266.14399999999995 98.52Q267.29599999999994 98.52 267.89599999999996 99.21600000000001Q268.4959999999999 99.912 268.4959999999999 101.28ZM273.0559999999999 102.48Q273.0559999999999 104.136 273.8119999999999 105.44399999999999Q274.5679999999999 106.752 275.8639999999999 107.496Q277.15999999999997 108.24 278.768 108.24Q280.4 108.24 281.68399999999997 107.496Q282.96799999999996 106.752 283.72399999999993 105.44399999999999Q284.47999999999996 104.136 284.47999999999996 102.48Q284.47999999999996 100.8 283.72399999999993 99.50399999999999Q282.96799999999996 98.208 281.68399999999997 97.464Q280.4 96.72 278.768 96.72Q277.15999999999997 96.72 275.8639999999999 97.464Q274.5679999999999 98.208 273.8119999999999 99.50399999999999Q273.0559999999999 100.8 273.0559999999999 102.48ZM274.99999999999994 102.48Q274.99999999999994 101.328 275.49199999999996 100.428Q275.9839999999999 99.528 276.8359999999999 99.024Q277.68799999999993 98.52 278.768 98.52Q279.84799999999996 98.52 280.69999999999993 99.024Q281.55199999999996 99.528 282.044 100.428Q282.53599999999994 101.328 282.53599999999994 102.48Q282.53599999999994 103.632 282.044 104.52000000000001Q281.55199999999996 105.408 280.69999999999993 105.924Q279.84799999999996 106.44 278.768 106.44Q277.68799999999993 106.44 276.8359999999999 105.924Q275.9839999999999 105.408 275.49199999999996 104.52000000000001Q274.99999999999994 103.632 274.99999999999994 102.48ZM292.63999999999993 96.96V98.76H298.1599999999999V96.96ZM294.43999999999994 93.12V108.0H296.3599999999999V93.12ZM300.94399999999996 104.592Q300.94399999999996 103.992 301.23199999999997 103.56Q301.52 103.128 302.10799999999995 102.888Q302.69599999999997 102.648 303.63199999999995 102.648Q304.64 102.648 305.528 102.9Q306.416 103.152 307.256 103.728V102.6Q307.08799999999997 102.384 306.60799999999995 102.036Q306.128 101.688 305.32399999999996 101.412Q304.52 101.136 303.32 101.136Q301.28 101.136 300.14 102.108Q299.0 103.08 299.0 104.688Q299.0 105.816 299.528 106.608Q300.056 107.4 300.932 107.82Q301.808 108.24 302.816 108.24Q303.72799999999995 108.24 304.65199999999993 107.904Q305.57599999999996 107.568 306.212 106.872Q306.84799999999996 106.176 306.84799999999996 105.12L306.464 103.68Q306.464 104.544 306.044 105.20400000000001Q305.62399999999997 105.864 304.91599999999994 106.224Q304.20799999999997 106.584 303.32 106.584Q302.62399999999997 106.584 302.08399999999995 106.356Q301.544 106.128 301.24399999999997 105.672Q300.94399999999996 105.216 300.94399999999996 104.592ZM300.65599999999995 99.504Q300.91999999999996 99.312 301.376 99.048Q301.832 98.78399999999999 302.49199999999996 98.592Q303.152 98.4 303.96799999999996 98.4Q304.472 98.4 304.928 98.49600000000001Q305.38399999999996 98.592 305.73199999999997 98.80799999999999Q306.08 99.024 306.272 99.396Q306.464 99.768 306.464 100.344V108.0H308.38399999999996V100.08Q308.38399999999996 99.0 307.84399999999994 98.256Q307.304 97.512 306.332 97.116Q305.35999999999996 96.72 304.06399999999996 96.72Q302.52799999999996 96.72 301.436 97.176Q300.344 97.632 299.71999999999997 98.088ZM311.62399999999997 89.28V108.0H313.544V89.28ZM317.02399999999994 89.28V108.0H318.94399999999996V89.28ZM323.02399999999994 96.96 318.22399999999993 101.52 323.50399999999996 108.0H325.90399999999994L320.6239999999999 101.52L325.4239999999999 96.96ZM328.15999999999997 104.616 326.64799999999997 105.552Q326.9599999999999 106.2 327.5719999999999 106.824Q328.1839999999999 107.448 329.0719999999999 107.844Q329.9599999999999 108.24 331.06399999999996 108.24Q332.7679999999999 108.24 333.8119999999999 107.28Q334.85599999999994 106.32 334.85599999999994 104.88Q334.85599999999994 103.896 334.3879999999999 103.27199999999999Q333.91999999999996 102.648 333.12799999999993 102.20400000000001Q332.33599999999996 101.76 331.3759999999999 101.376Q330.79999999999995 101.136 330.27199999999993 100.872Q329.7439999999999 100.608 329.4079999999999 100.272Q329.07199999999995 99.936 329.07199999999995 99.504Q329.07199999999995 98.952 329.5039999999999 98.688Q329.9359999999999 98.424 330.53599999999994 98.424Q331.3999999999999 98.424 332.08399999999995 98.832Q332.7679999999999 99.24 333.22399999999993 99.864L334.75999999999993 98.88Q334.4239999999999 98.256 333.82399999999996 97.776Q333.22399999999993 97.29599999999999 332.44399999999996 97.008Q331.66399999999993 96.72 330.77599999999995 96.72Q329.8879999999999 96.72 329.05999999999995 97.032Q328.2319999999999 97.344 327.7159999999999 97.99199999999999Q327.19999999999993 98.64 327.19999999999993 99.624Q327.19999999999993 100.584 327.6919999999999 101.22Q328.1839999999999 101.856 328.91599999999994 102.252Q329.64799999999997 102.648 330.36799999999994 102.936Q331.01599999999996 103.176 331.592 103.452Q332.16799999999995 103.728 332.53999999999996 104.124Q332.9119999999999 104.52 332.9119999999999 105.096Q332.9119999999999 105.744 332.44399999999996 106.116Q331.97599999999994 106.488 331.15999999999997 106.488Q330.48799999999994 106.488 329.924 106.23599999999999Q329.35999999999996 105.984 328.92799999999994 105.55199999999999Q328.4959999999999 105.12 328.15999999999997 104.616ZM346.376 89.28H344.45599999999996V108.0H346.376ZM355.61599999999993 102.48Q355.61599999999993 100.68 354.87199999999996 99.396Q354.12799999999993 98.112 352.89199999999994 97.416Q351.65599999999995 96.72 350.14399999999995 96.72Q348.77599999999995 96.72 347.73199999999997 97.416Q346.68799999999993 98.112 346.0999999999999 99.396Q345.51199999999994 100.68 345.51199999999994 102.48Q345.51199999999994 104.256 346.0999999999999 105.55199999999999Q346.68799999999993 106.848 347.73199999999997 107.544Q348.77599999999995 108.24 350.14399999999995 108.24Q351.65599999999995 108.24 352.89199999999994 107.544Q354.12799999999993 106.848 354.87199999999996 105.55199999999999Q355.61599999999993 104.256 355.61599999999993 102.48ZM353.67199999999997 102.48Q353.67199999999997 103.752 353.15599999999995 104.64Q352.63999999999993 105.528 351.78799999999995 105.98400000000001Q350.936 106.44 349.90399999999994 106.44Q349.06399999999996 106.44 348.24799999999993 105.98400000000001Q347.43199999999996 105.528 346.904 104.64Q346.376 103.752 346.376 102.48Q346.376 101.208 346.904 100.32Q347.43199999999996 99.432 348.24799999999993 98.976Q349.06399999999996 98.52 349.90399999999994 98.52Q350.936 98.52 351.78799999999995 98.976Q352.63999999999993 99.432 353.15599999999995 100.32Q353.67199999999997 101.208 353.67199999999997 102.48ZM359.1199999999999 104.592Q359.1199999999999 103.992 359.4079999999999 103.56Q359.6959999999999 103.128 360.2839999999999 102.888Q360.8719999999999 102.648 361.8079999999999 102.648Q362.8159999999999 102.648 363.70399999999995 102.9Q364.5919999999999 103.152 365.4319999999999 103.728V102.6Q365.2639999999999 102.384 364.7839999999999 102.036Q364.3039999999999 101.688 363.4999999999999 101.412Q362.6959999999999 101.136 361.4959999999999 101.136Q359.4559999999999 101.136 358.3159999999999 102.108Q357.17599999999993 103.08 357.17599999999993 104.688Q357.17599999999993 105.816 357.70399999999995 106.608Q358.2319999999999 107.4 359.10799999999995 107.82Q359.9839999999999 108.24 360.9919999999999 108.24Q361.9039999999999 108.24 362.82799999999986 107.904Q363.7519999999999 107.568 364.3879999999999 106.872Q365.0239999999999 106.176 365.0239999999999 105.12L364.63999999999993 103.68Q364.63999999999993 104.544 364.2199999999999 105.20400000000001Q363.7999999999999 105.864 363.09199999999987 106.224Q362.3839999999999 106.584 361.4959999999999 106.584Q360.7999999999999 106.584 360.2599999999999 106.356Q359.7199999999999 106.128 359.4199999999999 105.672Q359.1199999999999 105.216 359.1199999999999 104.592ZM358.8319999999999 99.504Q359.0959999999999 99.312 359.5519999999999 99.048Q360.0079999999999 98.78399999999999 360.6679999999999 98.592Q361.3279999999999 98.4 362.1439999999999 98.4Q362.6479999999999 98.4 363.1039999999999 98.49600000000001Q363.5599999999999 98.592 363.9079999999999 98.80799999999999Q364.2559999999999 99.024 364.4479999999999 99.396Q364.63999999999993 99.768 364.63999999999993 100.344V108.0H366.5599999999999V100.08Q366.5599999999999 99.0 366.01999999999987 98.256Q365.4799999999999 97.512 364.5079999999999 97.116Q363.5359999999999 96.72 362.2399999999999 96.72Q360.7039999999999 96.72 359.6119999999999 97.176Q358.5199999999999 97.632 357.8959999999999 98.088ZM370.66399999999993 102.48Q370.66399999999993 101.328 371.15599999999995 100.428Q371.64799999999997 99.528 372.49999999999994 99.024Q373.3519999999999 98.52 374.43199999999996 98.52Q375.31999999999994 98.52 376.0999999999999 98.79599999999999Q376.87999999999994 99.072 377.4559999999999 99.52799999999999Q378.0319999999999 99.984 378.27199999999993 100.536V98.136Q377.69599999999997 97.464 376.62799999999993 97.092Q375.55999999999995 96.72 374.43199999999996 96.72Q372.82399999999996 96.72 371.52799999999996 97.464Q370.23199999999997 98.208 369.476 99.50399999999999Q368.71999999999997 100.8 368.71999999999997 102.48Q368.71999999999997 104.136 369.476 105.44399999999999Q370.23199999999997 106.752 371.52799999999996 107.496Q372.82399999999996 108.24 374.43199999999996 108.24Q375.55999999999995 108.24 376.62799999999993 107.868Q377.69599999999997 107.496 378.27199999999993 106.824V104.424Q378.0319999999999 104.952 377.4559999999999 105.42Q376.87999999999994 105.888 376.0999999999999 106.164Q375.31999999999994 106.44 374.43199999999996 106.44Q373.3519999999999 106.44 372.49999999999994 105.924Q371.64799999999997 105.408 371.15599999999995 104.52000000000001Q370.66399999999993 103.632 370.66399999999993 102.48ZM381.152 89.28V108.0H383.072V89.28ZM387.152 96.96 382.352 101.52 387.632 108.0H390.032L384.75199999999995 101.52L389.55199999999996 96.96Z"/></svg></div><div class="stacked"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 286.0 226" width="286" height="226"><defs><linearGradient id="bs-asdark" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#B8E8FF"/><stop offset="1" stop-color="#5AA8F5"/></linearGradient></defs><g transform="translate(83.0 0)"><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#bs-asdark)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></g><path fill="#EEF3FB" d="M16.376000000000012 166.48H40.05600000000001L38.64800000000001 160.07999999999998H17.84800000000001ZM28.08800000000001 145.872 35.256000000000014 162.512 35.384000000000015 164.368 41.46400000000001 178.0H50.04000000000001L28.08800000000001 130.704L6.20000000000001 178.0H14.71200000000001L20.92000000000001 163.984L21.04800000000001 162.32ZM61.432000000000016 148.56H54.58400000000002V178.0H61.432000000000016ZM70.20000000000002 155.92 73.59200000000001 150.096Q72.56800000000001 148.88 71.16000000000001 148.368Q69.75200000000001 147.856 68.15200000000002 147.856Q65.912 147.856 63.83200000000001 149.488Q61.75200000000001 151.12 60.44000000000001 153.84Q59.128000000000014 156.56 59.128000000000014 160.07999999999998L61.432000000000016 161.424Q61.432000000000016 159.312 61.91200000000001 157.744Q62.39200000000001 156.176 63.48000000000001 155.28Q64.56800000000001 154.38400000000001 66.29600000000002 154.38400000000001Q67.57600000000002 154.38400000000001 68.44000000000003 154.768Q69.30400000000002 155.152 70.20000000000002 155.92ZM120.504 159.248Q120.504 155.664 119.44800000000001 153.10399999999998Q118.39200000000001 150.544 116.34400000000001 149.232Q114.296 147.92 111.096 147.92Q108.15200000000002 147.92 105.816 149.232Q103.48 150.544 101.88000000000001 153.168Q100.92 150.608 98.77600000000001 149.264Q96.632 147.92 93.432 147.92Q90.552 147.92 88.504 149.168Q86.456 150.416 85.11200000000001 152.848V148.56H78.328V178.0H85.11200000000001V160.07999999999998Q85.11200000000001 157.968 85.84800000000001 156.49599999999998Q86.584 155.024 87.89600000000002 154.256Q89.20800000000001 153.488 90.936 153.488Q93.49600000000001 153.488 94.68 155.12Q95.864 156.752 95.864 160.07999999999998V178.0H102.77600000000001V160.07999999999998Q102.77600000000001 157.968 103.512 156.49599999999998Q104.248 155.024 105.56 154.256Q106.87200000000001 153.488 108.66400000000002 153.488Q111.16 153.488 112.344 155.12Q113.528 156.752 113.528 160.07999999999998V178.0H120.504ZM127.03200000000001 163.28Q127.03200000000001 167.76 129.11200000000002 171.248Q131.192 174.736 134.808 176.688Q138.424 178.64 142.904 178.64Q147.448 178.64 151.032 176.688Q154.616 174.736 156.69600000000003 171.248Q158.776 167.76 158.776 163.28Q158.776 158.736 156.69600000000003 155.28Q154.616 151.824 151.032 149.872Q147.448 147.92 142.904 147.92Q138.424 147.92 134.808 149.872Q131.192 151.824 129.11200000000002 155.28Q127.03200000000001 158.736 127.03200000000001 163.28ZM134.136 163.28Q134.136 160.528 135.288 158.416Q136.44 156.304 138.424 155.152Q140.40800000000002 154.0 142.904 154.0Q145.4 154.0 147.38400000000001 155.152Q149.368 156.304 150.51999999999998 158.416Q151.672 160.528 151.672 163.28Q151.672 166.032 150.51999999999998 168.11200000000002Q149.368 170.192 147.38400000000001 171.376Q145.4 172.56 142.904 172.56Q140.40800000000002 172.56 138.424 171.376Q136.44 170.192 135.288 168.11200000000002Q134.136 166.032 134.136 163.28ZM184.312 160.07999999999998V178.0H191.41600000000003V159.248Q191.41600000000003 154.0 188.79200000000003 150.95999999999998Q186.16800000000003 147.92 181.17600000000002 147.92Q178.16800000000003 147.92 175.96000000000004 149.2Q173.75200000000004 150.48 172.34400000000002 153.10399999999998V148.56H165.36800000000002V178.0H172.34400000000002V160.07999999999998Q172.34400000000002 158.096 173.144 156.59199999999998Q173.94400000000002 155.088 175.41600000000003 154.288Q176.88800000000003 153.488 178.872 153.488Q181.62400000000002 153.488 182.96800000000002 155.152Q184.312 156.816 184.312 160.07999999999998ZM199.99200000000002 136.848Q199.99200000000002 138.576 201.30400000000003 139.824Q202.616 141.072 204.34400000000002 141.072Q206.20000000000002 141.072 207.48000000000002 139.824Q208.76000000000002 138.576 208.76000000000002 136.848Q208.76000000000002 135.05599999999998 207.48000000000002 133.83999999999997Q206.20000000000002 132.624 204.34400000000002 132.624Q202.616 132.624 201.30400000000003 133.83999999999997Q199.99200000000002 135.05599999999998 199.99200000000002 136.848ZM200.95200000000003 148.56V178.0H207.8V148.56ZM221.94400000000002 163.28Q221.94400000000002 160.528 223.16000000000003 158.416Q224.376 156.304 226.45600000000002 155.08800000000002Q228.536 153.872 231.096 153.872Q233.144 153.872 235.06400000000002 154.512Q236.984 155.152 238.488 156.272Q239.99200000000002 157.392 240.69600000000003 158.864V151.184Q239.16000000000003 149.712 236.536 148.81599999999997Q233.912 147.92 230.776 147.92Q226.29600000000002 147.92 222.68 149.872Q219.06400000000002 151.824 216.952 155.28Q214.84 158.736 214.84 163.28Q214.84 167.76 216.952 171.248Q219.06400000000002 174.736 222.68 176.688Q226.29600000000002 178.64 230.776 178.64Q233.912 178.64 236.536 177.744Q239.16000000000003 176.848 240.69600000000003 175.312V167.696Q239.99200000000002 169.10399999999998 238.52 170.224Q237.048 171.344 235.16000000000003 172.016Q233.27200000000002 172.688 231.096 172.688Q228.536 172.688 226.45600000000002 171.47199999999998Q224.376 170.256 223.16000000000003 168.144Q221.94400000000002 166.032 221.94400000000002 163.28ZM246.072 163.28Q246.072 167.76 248.15200000000002 171.248Q250.23200000000003 174.736 253.848 176.688Q257.464 178.64 261.944 178.64Q266.488 178.64 270.072 176.688Q273.656 174.736 275.736 171.248Q277.81600000000003 167.76 277.81600000000003 163.28Q277.81600000000003 158.736 275.736 155.28Q273.656 151.824 270.072 149.872Q266.488 147.92 261.944 147.92Q257.464 147.92 253.848 149.872Q250.23200000000003 151.824 248.15200000000002 155.28Q246.072 158.736 246.072 163.28ZM253.17600000000002 163.28Q253.17600000000002 160.528 254.32800000000003 158.416Q255.48000000000002 156.304 257.46400000000006 155.152Q259.44800000000004 154.0 261.944 154.0Q264.44 154.0 266.424 155.152Q268.408 156.304 269.56 158.416Q270.712 160.528 270.712 163.28Q270.712 166.032 269.56 168.11200000000002Q268.408 170.192 266.424 171.376Q264.44 172.56 261.944 172.56Q259.44800000000004 172.56 257.46400000000006 171.376Q255.48000000000002 170.192 254.32800000000003 168.11200000000002Q253.17600000000002 166.032 253.17600000000002 163.28Z"/><path fill="#DBE4F0" d="M47.023999999999994 202.2 42.67999999999999 209.976 38.35999999999999 202.2H36.07999999999999L41.672 211.776V219.0H43.711999999999996V211.752L49.303999999999995 202.2ZM47.864 213.48Q47.864 215.136 48.61999999999999 216.44400000000002Q49.37599999999999 217.752 50.672 218.496Q51.967999999999996 219.24 53.57599999999999 219.24Q55.208 219.24 56.492 218.496Q57.775999999999996 217.752 58.532 216.44400000000002Q59.288 215.136 59.288 213.48Q59.288 211.8 58.532 210.50400000000002Q57.775999999999996 209.208 56.492 208.464Q55.208 207.72 53.57599999999999 207.72Q51.967999999999996 207.72 50.672 208.464Q49.37599999999999 209.208 48.61999999999999 210.50400000000002Q47.864 211.8 47.864 213.48ZM49.80799999999999 213.48Q49.80799999999999 212.328 50.3 211.428Q50.791999999999994 210.528 51.64399999999999 210.024Q52.495999999999995 209.52 53.57599999999999 209.52Q54.65599999999999 209.52 55.507999999999996 210.024Q56.35999999999999 210.528 56.85199999999999 211.428Q57.343999999999994 212.328 57.343999999999994 213.48Q57.343999999999994 214.632 56.85199999999999 215.51999999999998Q56.35999999999999 216.408 55.507999999999996 216.92399999999998Q54.65599999999999 217.44 53.57599999999999 217.44Q52.495999999999995 217.44 51.64399999999999 216.92399999999998Q50.791999999999994 216.408 50.3 215.51999999999998Q49.80799999999999 214.632 49.80799999999999 213.48ZM63.84799999999999 214.68V207.96H61.92799999999999V214.92Q61.92799999999999 216.888 62.959999999999994 218.06400000000002Q63.99199999999999 219.24 65.72 219.24Q66.824 219.24 67.63999999999999 218.748Q68.45599999999999 218.256 69.008 217.272V219.0H70.928V207.96H69.008V214.68Q69.008 215.496 68.66 216.12Q68.312 216.744 67.67599999999999 217.09199999999998Q67.03999999999999 217.44 66.19999999999999 217.44Q65.04799999999999 217.44 64.448 216.72Q63.84799999999999 216.0 63.84799999999999 214.68ZM76.448 207.96H74.52799999999999V219.0H76.448ZM79.78399999999999 210.072 80.83999999999999 208.488Q80.40799999999999 208.032 79.892 207.876Q79.37599999999999 207.72 78.776 207.72Q78.008 207.72 77.264 208.32Q76.52 208.92 76.05199999999999 209.94Q75.58399999999999 210.96 75.58399999999999 212.28H76.448Q76.448 211.488 76.60399999999998 210.864Q76.75999999999999 210.24 77.16799999999999 209.88Q77.576 209.52 78.29599999999999 209.52Q78.776 209.52 79.088 209.652Q79.39999999999999 209.784 79.78399999999999 210.072ZM91.75999999999999 224.28V207.96H89.84V224.28ZM101.0 213.48Q101.0 211.68 100.256 210.39600000000002Q99.512 209.112 98.276 208.416Q97.03999999999999 207.72 95.52799999999999 207.72Q94.16 207.72 93.116 208.416Q92.072 209.112 91.48400000000001 210.39600000000002Q90.896 211.68 90.896 213.48Q90.896 215.256 91.48400000000001 216.55200000000002Q92.072 217.848 93.116 218.544Q94.16 219.24 95.52799999999999 219.24Q97.03999999999999 219.24 98.276 218.544Q99.512 217.848 100.256 216.55200000000002Q101.0 215.256 101.0 213.48ZM99.056 213.48Q99.056 214.752 98.53999999999999 215.64Q98.024 216.528 97.172 216.98399999999998Q96.32 217.44 95.288 217.44Q94.448 217.44 93.632 216.98399999999998Q92.816 216.528 92.288 215.64Q91.75999999999999 214.752 91.75999999999999 213.48Q91.75999999999999 212.208 92.288 211.32Q92.816 210.432 93.632 209.976Q94.448 209.52 95.288 209.52Q96.32 209.52 97.172 209.976Q98.024 210.432 98.53999999999999 211.32Q99.056 212.208 99.056 213.48ZM103.63999999999999 203.4Q103.63999999999999 203.928 104.03599999999999 204.324Q104.43199999999999 204.72 104.96 204.72Q105.51199999999999 204.72 105.89599999999999 204.324Q106.27999999999999 203.928 106.27999999999999 203.4Q106.27999999999999 202.848 105.89599999999999 202.464Q105.51199999999999 202.07999999999998 104.96 202.07999999999998Q104.43199999999999 202.07999999999998 104.03599999999999 202.464Q103.63999999999999 202.848 103.63999999999999 203.4ZM103.99999999999999 207.96V219.0H105.91999999999999V207.96ZM110.744 215.592Q110.744 214.992 111.032 214.56Q111.32 214.128 111.90799999999999 213.88799999999998Q112.496 213.648 113.432 213.648Q114.44 213.648 115.328 213.89999999999998Q116.216 214.152 117.056 214.728V213.6Q116.888 213.384 116.408 213.036Q115.928 212.688 115.124 212.41199999999998Q114.32 212.136 113.12 212.136Q111.08 212.136 109.94 213.108Q108.8 214.08 108.8 215.688Q108.8 216.816 109.328 217.608Q109.856 218.4 110.732 218.82Q111.608 219.24 112.616 219.24Q113.52799999999999 219.24 114.452 218.904Q115.376 218.568 116.012 217.872Q116.648 217.176 116.648 216.12L116.264 214.68Q116.264 215.544 115.844 216.204Q115.42399999999999 216.864 114.716 217.224Q114.008 217.584 113.12 217.584Q112.42399999999999 217.584 111.88399999999999 217.356Q111.344 217.128 111.044 216.672Q110.744 216.216 110.744 215.592ZM110.456 210.504Q110.72 210.312 111.176 210.048Q111.632 209.784 112.292 209.59199999999998Q112.952 209.4 113.768 209.4Q114.27199999999999 209.4 114.728 209.496Q115.184 209.592 115.532 209.808Q115.88 210.024 116.072 210.39600000000002Q116.264 210.768 116.264 211.344V219.0H118.184V211.08Q118.184 210.0 117.644 209.256Q117.104 208.512 116.132 208.11599999999999Q115.16 207.72 113.864 207.72Q112.328 207.72 111.236 208.176Q110.144 208.632 109.52 209.088ZM128.504 212.28V219.0H130.424V212.04Q130.424 210.048 129.404 208.88400000000001Q128.384 207.72 126.63199999999999 207.72Q125.55199999999999 207.72 124.72399999999999 208.2Q123.896 208.68 123.344 209.688V207.96H121.42399999999999V219.0H123.344V212.28Q123.344 211.464 123.692 210.84Q124.03999999999999 210.216 124.67599999999999 209.868Q125.312 209.52 126.152 209.52Q127.304 209.52 127.904 210.216Q128.504 210.912 128.504 212.28ZM133.064 213.48Q133.064 215.136 133.82 216.44400000000002Q134.576 217.752 135.87199999999999 218.496Q137.16799999999998 219.24 138.77599999999998 219.24Q140.408 219.24 141.692 218.496Q142.976 217.752 143.732 216.44400000000002Q144.488 215.136 144.488 213.48Q144.488 211.8 143.732 210.50400000000002Q142.976 209.208 141.692 208.464Q140.408 207.72 138.77599999999998 207.72Q137.16799999999998 207.72 135.87199999999999 208.464Q134.576 209.208 133.82 210.50400000000002Q133.064 211.8 133.064 213.48ZM135.00799999999998 213.48Q135.00799999999998 212.328 135.5 211.428Q135.992 210.528 136.844 210.024Q137.696 209.52 138.77599999999998 209.52Q139.856 209.52 140.708 210.024Q141.56 210.528 142.052 211.428Q142.54399999999998 212.328 142.54399999999998 213.48Q142.54399999999998 214.632 142.052 215.51999999999998Q141.56 216.408 140.708 216.92399999999998Q139.856 217.44 138.77599999999998 217.44Q137.696 217.44 136.844 216.92399999999998Q135.992 216.408 135.5 215.51999999999998Q135.00799999999998 214.632 135.00799999999998 213.48ZM152.648 207.96V209.76H158.16799999999998V207.96ZM154.44799999999998 204.12V219.0H156.368V204.12ZM160.952 215.592Q160.952 214.992 161.24 214.56Q161.52800000000002 214.128 162.116 213.88799999999998Q162.704 213.648 163.64000000000001 213.648Q164.64800000000002 213.648 165.536 213.89999999999998Q166.424 214.152 167.264 214.728V213.6Q167.096 213.384 166.616 213.036Q166.13600000000002 212.688 165.33200000000002 212.41199999999998Q164.52800000000002 212.136 163.328 212.136Q161.288 212.136 160.14800000000002 213.108Q159.008 214.08 159.008 215.688Q159.008 216.816 159.536 217.608Q160.06400000000002 218.4 160.94 218.82Q161.816 219.24 162.824 219.24Q163.73600000000002 219.24 164.66000000000003 218.904Q165.584 218.568 166.22000000000003 217.872Q166.85600000000002 217.176 166.85600000000002 216.12L166.472 214.68Q166.472 215.544 166.05200000000002 216.204Q165.632 216.864 164.924 217.224Q164.216 217.584 163.328 217.584Q162.632 217.584 162.092 217.356Q161.55200000000002 217.128 161.252 216.672Q160.952 216.216 160.952 215.592ZM160.66400000000002 210.504Q160.928 210.312 161.38400000000001 210.048Q161.84 209.784 162.5 209.59199999999998Q163.16000000000003 209.4 163.976 209.4Q164.48000000000002 209.4 164.93600000000004 209.496Q165.39200000000002 209.592 165.74 209.808Q166.08800000000002 210.024 166.28000000000003 210.39600000000002Q166.472 210.768 166.472 211.344V219.0H168.39200000000002V211.08Q168.39200000000002 210.0 167.85200000000003 209.256Q167.312 208.512 166.34000000000003 208.11599999999999Q165.36800000000002 207.72 164.072 207.72Q162.536 207.72 161.44400000000002 208.176Q160.352 208.632 159.728 209.088ZM171.632 200.28V219.0H173.552V200.28ZM177.03199999999998 200.28V219.0H178.95199999999997V200.28ZM183.03199999999998 207.96 178.23199999999997 212.52 183.51199999999997 219.0H185.91199999999998L180.63199999999998 212.52L185.432 207.96ZM188.16799999999998 215.616 186.65599999999998 216.552Q186.968 217.2 187.57999999999998 217.824Q188.19199999999998 218.448 189.07999999999998 218.844Q189.968 219.24 191.07199999999997 219.24Q192.77599999999998 219.24 193.82 218.28Q194.86399999999998 217.32 194.86399999999998 215.88Q194.86399999999998 214.896 194.39599999999996 214.272Q193.92799999999997 213.648 193.13599999999997 213.204Q192.34399999999997 212.76 191.384 212.376Q190.80799999999996 212.136 190.27999999999997 211.872Q189.75199999999998 211.608 189.416 211.272Q189.07999999999998 210.936 189.07999999999998 210.504Q189.07999999999998 209.952 189.512 209.688Q189.944 209.424 190.54399999999998 209.424Q191.408 209.424 192.09199999999998 209.832Q192.77599999999998 210.24 193.23199999999997 210.864L194.76799999999997 209.88Q194.432 209.256 193.832 208.776Q193.23199999999997 208.296 192.45199999999997 208.00799999999998Q191.67199999999997 207.72 190.784 207.72Q189.896 207.72 189.06799999999998 208.03199999999998Q188.23999999999998 208.344 187.724 208.992Q187.20799999999997 209.64 187.20799999999997 210.624Q187.20799999999997 211.584 187.7 212.22Q188.19199999999998 212.856 188.92399999999998 213.252Q189.65599999999998 213.648 190.37599999999998 213.936Q191.02399999999997 214.176 191.59999999999997 214.452Q192.176 214.728 192.548 215.12400000000002Q192.92 215.52 192.92 216.096Q192.92 216.744 192.452 217.11599999999999Q191.98399999999998 217.488 191.16799999999998 217.488Q190.49599999999998 217.488 189.93199999999996 217.236Q189.36799999999997 216.984 188.93599999999998 216.55200000000002Q188.504 216.12 188.16799999999998 215.616ZM206.384 200.28H204.464V219.0H206.384ZM215.624 213.48Q215.624 211.68 214.88 210.39600000000002Q214.136 209.112 212.89999999999998 208.416Q211.664 207.72 210.152 207.72Q208.784 207.72 207.74 208.416Q206.696 209.112 206.108 210.39600000000002Q205.51999999999998 211.68 205.51999999999998 213.48Q205.51999999999998 215.256 206.108 216.55200000000002Q206.696 217.848 207.74 218.544Q208.784 219.24 210.152 219.24Q211.664 219.24 212.89999999999998 218.544Q214.136 217.848 214.88 216.55200000000002Q215.624 215.256 215.624 213.48ZM213.68 213.48Q213.68 214.752 213.164 215.64Q212.648 216.528 211.796 216.98399999999998Q210.944 217.44 209.91199999999998 217.44Q209.072 217.44 208.256 216.98399999999998Q207.44 216.528 206.91199999999998 215.64Q206.384 214.752 206.384 213.48Q206.384 212.208 206.91199999999998 211.32Q207.44 210.432 208.256 209.976Q209.072 209.52 209.91199999999998 209.52Q210.944 209.52 211.796 209.976Q212.648 210.432 213.164 211.32Q213.68 212.208 213.68 213.48ZM219.128 215.592Q219.128 214.992 219.416 214.56Q219.704 214.128 220.292 213.88799999999998Q220.88 213.648 221.816 213.648Q222.824 213.648 223.712 213.89999999999998Q224.6 214.152 225.44 214.728V213.6Q225.272 213.384 224.792 213.036Q224.312 212.688 223.508 212.41199999999998Q222.704 212.136 221.504 212.136Q219.464 212.136 218.324 213.108Q217.184 214.08 217.184 215.688Q217.184 216.816 217.712 217.608Q218.24 218.4 219.11599999999999 218.82Q219.992 219.24 221.0 219.24Q221.912 219.24 222.836 218.904Q223.76 218.568 224.39600000000002 217.872Q225.032 217.176 225.032 216.12L224.648 214.68Q224.648 215.544 224.228 216.204Q223.808 216.864 223.1 217.224Q222.392 217.584 221.504 217.584Q220.808 217.584 220.268 217.356Q219.728 217.128 219.428 216.672Q219.128 216.216 219.128 215.592ZM218.84 210.504Q219.10399999999998 210.312 219.56 210.048Q220.016 209.784 220.676 209.59199999999998Q221.336 209.4 222.152 209.4Q222.656 209.4 223.11200000000002 209.496Q223.568 209.592 223.916 209.808Q224.264 210.024 224.45600000000002 210.39600000000002Q224.648 210.768 224.648 211.344V219.0H226.568V211.08Q226.568 210.0 226.02800000000002 209.256Q225.488 208.512 224.51600000000002 208.11599999999999Q223.544 207.72 222.248 207.72Q220.712 207.72 219.62 208.176Q218.528 208.632 217.904 209.088ZM230.67199999999997 213.48Q230.67199999999997 212.328 231.164 211.428Q231.65599999999998 210.528 232.50799999999998 210.024Q233.35999999999999 209.52 234.43999999999997 209.52Q235.32799999999997 209.52 236.10799999999998 209.796Q236.88799999999998 210.072 237.464 210.52800000000002Q238.04 210.984 238.27999999999997 211.536V209.136Q237.70399999999998 208.464 236.63599999999997 208.09199999999998Q235.56799999999998 207.72 234.43999999999997 207.72Q232.832 207.72 231.536 208.464Q230.23999999999998 209.208 229.48399999999998 210.50400000000002Q228.72799999999998 211.8 228.72799999999998 213.48Q228.72799999999998 215.136 229.48399999999998 216.44400000000002Q230.23999999999998 217.752 231.536 218.496Q232.832 219.24 234.43999999999997 219.24Q235.56799999999998 219.24 236.63599999999997 218.868Q237.70399999999998 218.496 238.27999999999997 217.824V215.424Q238.04 215.952 237.464 216.42000000000002Q236.88799999999998 216.888 236.10799999999998 217.164Q235.32799999999997 217.44 234.43999999999997 217.44Q233.35999999999999 217.44 232.50799999999998 216.92399999999998Q231.65599999999998 216.408 231.164 215.51999999999998Q230.67199999999997 214.632 230.67199999999997 213.48ZM241.16000000000003 200.28V219.0H243.08V200.28ZM247.16000000000003 207.96 242.36 212.52 247.64000000000001 219.0H250.04000000000002L244.76000000000002 212.52L249.56000000000003 207.96Z"/></svg></div></div>

<section class="band" id="top">
  <div class="wrap">
    <div class="cols">
      <div class="today">
        <div class="kicker" id="todayHead">Today</div>
        <div class="row">
          <div class="ring" id="todayRing"><div><b id="todayPct">0%</b></div></div>
          <div><div class="song" id="todaySong">…</div><p id="todayText">Loading your songs…</p></div>
        </div>
        <div class="go">
          <button class="btn gold" id="todayGo">Start the first lesson</button>
          <a href="#songs">All songs</a>
        </div>
      </div>
      <div class="side">
        <h3>Where you stand</h3>
        <div class="statline"><span><b id="sStreak">0</b> day streak</span><span><b id="sMin">0</b> minutes this week</span><span><b id="sPieces">0</b> new parts this week</span><span>last full run <b id="sScore">–</b></span></div>
        <div class="sep"></div>
        <h3>Pace</h3>
        <div class="opts" id="pace">
          <div class="pseg" id="paceSeg"><button data-p="relaxed" title="1 new part in each lesson, and a slower demo">Relaxed</button><button data-p="steady" title="2 new parts in each lesson, and a medium demo">Steady</button><button data-p="fast" title="3 new parts in each lesson, and a faster demo">Fast</button></div>
          <div class="tempo">
            <label title="Slows the demo down after two slips in a row, and speeds it up when a part comes out clean"><input type="checkbox" id="autoTempo"> Tempo adapts to you</label>
            <span id="speedAuto"></span>
            <label id="speedBox"><span>Demo speed</span><input type="range" id="speed" min="40" max="100" step="5"><b id="speedTxt"></b></label>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="stage" id="play">
  <div class="wrap">
    <div class="sechead"><h2 data-tkey="head.play">Play</h2><p data-tkey="content.play.sub">The keyboard on the screen follows the real one.</p></div>
    <div class="replaybar" id="replaybar">
      <button class="rbtn" id="replayPlay" title="Play or pause (space)">❚❚</button>
      <div class="scrub"><div class="lanes" id="lanes"></div><div class="head" id="replayHead"></div><input type="range" id="seek" min="0" max="1" step="0.1" value="0" aria-label="Position"></div>
      <span id="replayTime">0:00 / 0:00</span>
      <span class="legend"><span><b style="background:var(--you)"></b>You</span><span><b style="background:var(--them)"></b>The lesson</span></span>
      <span class="name" id="replayName"></span>
      <button class="btn" style="background:#fff;color:var(--navy);padding:9px 16px" id="replayStop">Close</button>
    </div>
    <div class="lesson" id="lesson">
      <div class="panel now" id="now">
        <span class="who" id="who"></span>
        <div class="what" id="what"></div>
        <div class="sticker neutral" id="sticker">♪</div>
        <div class="tags"><span class="tag" id="noteTag"></span><span class="tag hand" id="hand"></span><span class="pedal" id="pedal">Pedal</span></div>
      </div>
      <div class="panel piece">
        <h4 id="pieceTitle"></h4>
        <div class="seq" id="seq"></div>
        <div class="lessonbar"><div class="progress"><i id="fill"></i></div><button class="btn red" id="stopBtn">Stop lesson</button></div>
      </div>
    </div>
    <div class="toolbar">
      <div class="title"><span id="stageTitle">Play</span></div>
      <div class="seg" id="colors" title="Key colors"><button data-c="notes">Colors</button><button data-c="simple">Simple</button></div>
      <div class="seg" id="labels"><button data-l="num" class="on">1 2 3</button><button data-l="abc">C D E</button><button data-l="do">Do Re Mi</button><button data-l="pc" title="The computer key that plays each note">⌨</button><button data-l="none">None</button></div>
      <button class="live idleonly" id="pedalBtn" title="Sustain pedal for the screen's keys"><i></i><span>Pedal</span></button>
      <label class="vol" title="Keyboard volume"><span>🔊</span><input type="range" id="vol" min="0" max="100" step="1" value="100"><b id="volTxt">100%</b></label>
      <button class="live" id="soundBtn" title="Play the notes on this computer's speakers too"><i></i><span>Computer sound</span></button>
      <button class="live idleonly" id="live"><i></i><span id="liveTxt">Live</span></button>
    </div>
    <div class="heardrow idleonly"><span class="heard" id="heard"></span></div>
    <div class="kbframe"><div class="kbwrap" id="kbwrap"><div class="kb" id="kb"></div></div></div>
    <p class="pchint idleonly" id="pcHint" hidden><span>Computer keys: Z to M and Q to P play, the space bar is the pedal, ← and → move an octave. Q is key</span> <b id="pcOct"></b></p>
    <p class="pchint idleonly">While this screen is open, keys on the keyboard trigger no shortcuts or automations. The stop key still works.</p>
  </div>
</section>

<div class="sections">
  <section class="block" id="songs">
    <div class="wrap">
      <div class="sechead"><h2 data-tkey="head.songs">Your songs</h2><p data-tkey="content.songs.sub">The ring shows how much of each song you have learned.</p></div>
      <div class="songs" id="songList"></div>
      <div class="addsong" id="drop">
        <div class="addrow">
          <b>Add a song</b>
          <label class="btn ghost" title="A MIDI file (.mid) from this computer or phone. It can also be dropped here.">⬆ Upload a MIDI file<input type="file" id="upload" accept=".mid,.midi,.kar,audio/midi" multiple hidden></label>
          <input id="q" placeholder="Search free archives, for example: minuet" aria-label="Search free archives">
          <button class="btn gold" id="qGo">Search</button>
        </div>
        <div id="qRes"></div>
        <small>Searched in: Mutopia Project · Wikimedia Commons. Only archives whose files may be kept. The same search as /getmidi in Telegram.</small>
      </div>
    </div>
  </section>

  <section class="block alt" id="progress">
    <div class="wrap">
      <div class="sechead"><h2 data-tkey="head.progress">Your progress</h2><p data-tkey="content.progress.sub">Every lesson you played, and its recording.</p></div>
      <div class="two">
        <div class="list"><h3>Recent lessons</h3><div id="histList"></div></div>
        <div class="list"><h3>Recordings</h3><div id="recList"></div></div>
      </div>
    </div>
  </section>

  <section class="block" id="method">
    <div class="wrap">
      <div class="sechead"><h2 data-tkey="head.method">How a lesson works</h2></div>
      <div class="methodtext" data-tkey="content.method.intro"><p>A lesson opens with a full run of everything learned so far, from the first note: once on your own, then the same again with the band. That first run is what decides which parts are steady.</p><p>Then come the new parts, each one first with the key numbers, the colors and a demo, then with only the key lit, and last from memory. A part that comes out from memory is joined to the part before it, and the lesson ends with the same two runs again, now with today’s part in them.</p></div>
      <details class="method">
        <summary data-tkey="content.method.more">More about the method <span class="arr">›</span></summary>
        <div class="deep">
          <div class="lead" data-tkey="content.method.deep1"><h4>The method in detail</h4><p>The lessons teach the melody of a song, cut into short parts where the music breathes. A few new parts in each lesson, at the pace you choose, and another lesson can start right after. Each part comes back until it holds. A mode for the full piano part, with the left hand's accompaniment, is planned.</p></div>
          <div class="step" data-tkey="content.method.deep2"><h4>The run that measures</h4><p>Everything learned so far, in order, from the first note of the song. First on your own, then the same again with the band. The run on your own is the one that is read: the band does not lead, but chords under the melody are themselves a reminder of what comes next. This run opens the lesson on purpose, when the parts were last played a day or a week ago and nothing has warmed them up.</p></div>
          <div class="step" data-tkey="content.method.deep3"><h4>New parts, and connecting them</h4><p>The help comes off in 3 steps: numbers, colors and a demo, then only the key lit, then from memory. A wrong key gets one more try before the answer shows, because finding it yourself is what makes it stick. Each new part is then played together with the part before it, from memory, so the song grows as one line.</p></div>
          <div class="step" data-tkey="content.method.deep4"><h4>The same run again, at the end</h4><p>The whole song so far with today’s part in it, on your own and then with the band, scored on the right notes, the rhythm and the hesitations. It comes minutes after practising those same notes, so it says how the lesson went rather than what will still be there tomorrow, and it does not move a part’s count. Comparing it with the run at the start is what shows the day’s gain.</p></div>
          <div class="step" data-tkey="content.method.deep5"><h4>When a part counts as steady</h4><p>A melody is remembered forward, from its beginning, not from the middle, so there is no separate test of a part on its own. The runs are the test: a part played clean in the run at the start counts clean, a part that slipped there starts counting from 0. A part counts as steady after 3 clean opening runs in a row. Whatever slipped in either run is practiced again right after it, with the numbers back on, up to two parts per run. The beginning of a song is played in every run and settles first, and the newest parts get the most work, which is also the order in which a song is really learned.</p></div>
          <div class="note" data-tkey="content.method.deep6"><b>Why colors, when a real piano has none?</b> They are training wheels. Every C has the same color, so while a part is new you find its keys at a glance and your attention stays on the music. A teacher in a lesson does the same by pointing at the keys. The rounds from memory take the colors and numbers away together, so what stays with you is where the key is on the piano, not its color. Learners who only ever practiced with colors tend to lean on them, and these rounds are what prevent that.</div>
        </div>
      </details>
    </div>
  </section>
</div>

<section id="about">
  <div class="wrap">
    <div class="ahead"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120"><defs><linearGradient id="ab-ailight" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#8AD6FF"/><stop offset="1" stop-color="#1D6BE0"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A" stroke="#0E2A47" stroke-width="2.4"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF" stroke="#0E2A47" stroke-width="2.4"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0B1A30"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#ab-ailight)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg><h1 data-tkey="head.about">About the project</h1></div>
    <p data-tkey="content.about.p1"><b>Vesta Core Labs</b><br>Vesta Core Labs is my home lab. I build and document systems there the way they would run in production: virtualization, networking, security and home automation. I learn by building rather than by reading, and a project is written down once it works, because that is when the understanding settles. Each one is documented well enough that someone else, or a later version of me, can pick it up and see how it works and why it was built that way.</p>
    <p data-tkey="content.about.p2"><b>Where Armonico came from</b><br>It started as one automation, the piano playing a wake-up song every morning. My first songs I had learned from short videos, with the key numbers written on the white keys, repeating a part until my hands knew it without looking. That worked until I sat at a piano I could not write on, so the numbers moved to a screen. Over time I needed them less, and the automation grew into the lesson engine running here.</p>
    <p data-tkey="content.about.what"><b>What it does</b><br>Armonico has two sides. It teaches: a song is cut into short parts, every white key carries a number, the screen lights the key to play next and names the finger for it, and each part comes back in later lessons until it holds. And it listens: the same keyboard is an input device for the house, so a short sequence of keys can set off a scene through MQTT, and the alarm in the morning is the piano itself playing, stopped by the lowest key. Both sides share one bridge and one set of commands, from the browser, from Telegram and from the terminal.</p>
    <p data-tkey="content.about.p3"><b>In daily use</b><br>It has run on a real keyboard every day for more than half a year, with a real alarm every morning, and AI was among its tools. Whatever broke along the way is already fixed in what runs here.</p>
    <div class="credbox">
    <h2 class="credhead" data-tkey="head.credits">Credits</h2>
    <ul class="credits">
      <li data-tkey="content.about.cred.songs"><b>Songs</b><p>Free archives whose files may be kept. The source and license of each song are on its card.</p><span class="links"><a href="https://www.mutopiaproject.org/" target="_blank" rel="noopener">Mutopia Project</a><a href="https://commons.wikimedia.org/" target="_blank" rel="noopener">Wikimedia Commons</a></span></li>
      <li data-tkey="content.about.cred.sound"><b>Piano sound</b><p>The grand piano on this screen is the Salamander Grand Piano by Alexander Holm, a Yamaha C5 recorded note by note, under Creative Commons Attribution 3.0. The files are the subset prepared by the Tone.js project.</p><span class="links"><a href="https://archive.org/details/SalamanderGrandPianoV3" target="_blank" rel="noopener">Salamander Grand Piano</a><a href="https://creativecommons.org/licenses/by/3.0/" target="_blank" rel="noopener">CC BY 3.0</a><a href="https://github.com/Tonejs/audio" target="_blank" rel="noopener">Tone.js</a></span></li>
      <li data-tkey="content.about.cred.keys"><b>Computer keys</b><p>Playing with the computer's keys follows Virtual Piano, where I learned a little before I had a keyboard.</p><span class="links"><a href="https://virtualpiano.net/" target="_blank" rel="noopener">virtualpiano.net</a></span></li>
      <li data-tkey="content.about.cred.method"><b>Method</b><p>Each part comes back until it holds, an idea borrowed from flashcard apps such as Anki.</p><span class="links"><a href="https://apps.ankiweb.net/" target="_blank" rel="noopener">Anki</a></span></li>
      <li data-tkey="content.about.cred.font"><b>Font</b><p>Heebo, by the Heebo Project Authors, under the SIL Open Font License 1.1. Included with the project, so the screen needs no internet.</p><span class="links"><a href="https://github.com/OdedEzer/heebo" target="_blank" rel="noopener">Heebo</a></span></li>
      <li data-tkey="content.about.cred.libs"><b>Libraries</b><p>mido for MIDI files, and Eclipse Paho for MQTT.</p><span class="links"><a href="https://github.com/mido/mido" target="_blank" rel="noopener">mido</a><a href="https://github.com/eclipse-paho/paho.mqtt.python" target="_blank" rel="noopener">Eclipse Paho</a></span></li>
    </ul>
    </div>
    <a class="back" href="/" data-tkey="content.about.back">‹ Back to the lessons</a>
  </div>
</section>

<footer>
  <div class="wrap main">
    <div class="brand">
      <span class="footlogo" data-tkey="foot.logo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120"><defs><linearGradient id="ft-aidark" gradientUnits="userSpaceOnUse" x1="12" y1="17" x2="108" y2="58"><stop offset="0" stop-color="#B8E8FF"/><stop offset="1" stop-color="#5AA8F5"/></linearGradient></defs><path d="M4 50 L60 3 L116 50" fill="none" stroke="#E2B04A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M41.85 60h17.25v40a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#E2B04A"/><path d="M62.7 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M83.55 60h17.25v46a5 5 0 0 1-5 5h-7.25a5 5 0 0 1-5-5z" fill="#EEF6FF"/><path d="M34.05 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M54.9 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M75.75 60h12v26a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3z" fill="#0A1120"/><path d="M12 58 L60 17 L108 58" fill="none" stroke="url(#ft-aidark)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      <p data-tkey="foot.tagline">Piano lessons on a MIDI keyboard, and smart home control from the same keys.<span>A song is cut into short parts, each part is learned until it comes out from memory, and it comes back in later lessons until it stays.</span></p>
      <div class="langPick up"></div>
    </div>
    <div><h4 data-tkey="foot.h.project">Project</h4><ul>
      <li data-tkey="foot.link.docs"><a href="https://github.com/__REPO__/tree/main/docs" target="_blank" rel="noopener">Documentation</a></li>
      <li data-tkey="foot.link.install"><a href="https://github.com/__REPO__/blob/main/docs/installation.md" target="_blank" rel="noopener">Installation</a></li>
      <li data-tkey="foot.link.github"><a href="https://github.com/__REPO__" target="_blank" rel="noopener">GitHub</a></li>
    </ul></div>
    <div><h4 data-tkey="foot.h.help">Help</h4><ul>
      <li data-tkey="foot.link.faq"><a href="https://github.com/__REPO__/blob/main/docs/faq.md" target="_blank" rel="noopener">Questions and pitfalls</a></li>
      <li data-tkey="foot.link.issues"><a href="https://github.com/__REPO__/issues" target="_blank" rel="noopener">Report a problem</a></li>
      <li data-tkey="foot.link.backup"><a href="/api/backup" data-save="piano-backup.zip" download>Download a backup</a></li>
    </ul></div>
  </div>
  <div class="base"><div class="wrap">
    <span data-tkey="foot.copyright">© <span id="year">2026</span> Vesta Core Labs</span>
    <span data-tkey="foot.version">Version <b id="ver">…</b> · <a href="#" id="updCheck">Check for updates</a> <span id="updState"></span></span>
  </div></div>
</footer>

<div class="modal" id="summary"><div class="sheet" id="sheet">
  <span class="eyebrow" id="sumEyebrow">Lesson done</span>
  <h2 id="sumSong"></h2>
  <p id="sumWhy" style="margin:.2rem 0 .8rem;color:var(--gold);font-weight:600"></p>
  <div class="kpis"><div><b id="kLearned"></b><span>parts of the song learned</span></div><div><b id="kNew"></b><span>new today</span></div><div><b id="kScore"></b><span>full run</span></div>
    <div><b id="kTime"></b><span>minutes</span></div><div><b id="kMist"></b><span>mistakes</span></div><div><b id="kFixed"></b><span>parts practiced again</span></div></div>
  <ul id="sumList"></ul>
  <div class="actions"><button class="btn gold" id="sumReplay">▶ Watch the replay</button><a class="btn ghost" id="sumMidi" data-save="lesson.mid" style="text-decoration:none" download>Download MIDI</a><button class="btn ghost" id="sumClose">Close</button></div>
</div></div>
<div class="modal" id="codeBox"><div class="sheet">
  <h2>Enter the screen code</h2>
  <p style="color:var(--muted);line-height:1.6;margin:.5rem 0 1rem">Only screens with the code can start lessons, play the keyboard or change its volume. It is asked once on each device and holds for 14 days, renewed every time the screen is used.<br>
  Get it in Telegram with <b>/pianocode</b>, or on the machine with <code>armonico code</code></p>
  <p style="color:var(--muted);line-height:1.6;margin:0 0 1rem;font-size:.9rem">This device keeps one pass in the browser: an end date and a signature, nothing about who is holding it. It stays on this device, is never sent anywhere else, and "Sign every screen out" in the settings ends it.</p>
  <input id="codeIn" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="000000"
    style="font:inherit;font-size:2rem;letter-spacing:.5rem;text-align:center;width:100%;padding:.6rem;border-radius:.9rem;border:2px solid var(--line);background:var(--surface2);color:var(--title)">
  <div class="actions" style="margin-top:1rem"><button class="btn gold" id="codeGo">Unlock</button><button class="btn ghost" id="codeCancel">Cancel</button></div>
</div></div>
<div class="modal" id="restoreBox" role="dialog" aria-modal="true"><div class="sheet">
  <h2>Restore from a backup</h2>
  <p class="setnote">Choose a backup file you downloaded here before. Only a backup made by Armonico is accepted. It goes back into the profile it was made from, which becomes the profile at the piano, and that profile is made again if it was deleted. Its progress, history and settings are replaced. Recordings and songs are added, and nothing already here is deleted.</p>
  <label class="setnote" for="restFile">Backup file (.zip)</label>
  <input type="file" id="restFile" accept=".zip,application/zip">
  <div id="restPick" style="display:none;margin-top:1rem">
    <p class="setnote" id="restWhat"></p>
    <div id="restSrcBox" style="display:none">
      <label class="setnote" for="restSrc">This backup holds several profiles. Take the data of</label>
      <select id="restSrc" style="width:100%;padding:.5rem;border-radius:.6rem;background:var(--surface2);color:var(--title);border:1px solid var(--line)"></select>
    </div>
    <p class="setnote" id="restInto" style="font-weight:700"></p>
    <input id="restPin" type="password" inputmode="numeric" maxlength="8" autocomplete="off" placeholder="PIN" style="display:none;width:100%;padding:.5rem;border-radius:.6rem;background:var(--surface2);color:var(--title);border:1px solid var(--line)">
    <p class="setnote" id="restSongs"></p>
  </div>
  <div class="actions" style="margin-top:1rem"><button class="btn gold" id="restGo" disabled>Restore</button><button class="btn ghost" id="restCancel">Cancel</button></div>
</div></div>
<div class="modal" id="profiles" role="dialog" aria-modal="true" aria-labelledby="profTitle"><div class="sheet">
  <h2 id="profTitle">Who is playing?</h2>
  <p class="setnote">Each profile has its own progress, recordings, pace and language. The songs are shared.</p>
  <div class="proflist" id="profList"></div>
  <div class="profpin" id="profPin" hidden>
    <label for="profPinIn"><span id="profPinWhy">PIN for</span> <b id="profPinName"></b></label>
    <div class="frow"><input id="profPinIn" type="password" inputmode="numeric" maxlength="8" autocomplete="off" placeholder="••••">
      <button class="btn gold" id="profPinGo">Continue</button><button class="btn ghost" id="profPinCancel">Cancel</button></div>
  </div>
  <details class="profform" id="profAdd"><summary>Add a profile</summary>
    <div class="frow"><input id="addName" maxlength="24" autocomplete="off" placeholder="Name" aria-label="Name">
      <input id="addPin" type="password" inputmode="numeric" maxlength="8" autocomplete="new-password" placeholder="PIN (optional)" aria-label="PIN (optional)">
      <button class="btn gold" id="addGo">Add</button></div>
  </details>
  <details class="profform" id="profEdit"><summary>Edit my profile</summary>
    <div class="frow"><input id="editName" maxlength="24" autocomplete="off" placeholder="Name" aria-label="Name">
      <input id="editNewPin" type="password" inputmode="numeric" maxlength="8" autocomplete="new-password" placeholder="New PIN (empty: keep it)" aria-label="New PIN (empty: keep it)"></div>
    <label class="chk"><input type="checkbox" id="editNoPin"> Remove the PIN</label>
    <div class="frow"><input id="editPin" type="password" inputmode="numeric" maxlength="8" autocomplete="off" placeholder="Current PIN" aria-label="Current PIN">
      <button class="btn gold" id="editGo">Save</button></div>
  </details>
  <div class="actions" style="margin-top:1rem"><button class="btn ghost" id="profClose">Close</button></div>
</div></div>
<div class="modal" id="settings" role="dialog" aria-modal="true" aria-labelledby="setTitle"><div class="sheet">
  <h2 id="setTitle">Settings</h2>
  <div class="setrows">
    <div class="setrow"><span>Language</span><div class="seg" id="setLang"><button data-v="en">English</button><button data-v="he">עברית</button></div></div>
    <div class="setrow"><span>Theme</span><div class="seg" id="setTheme"><button data-v="light">Light</button><button data-v="dark">Dark</button></div></div>
    <div class="setrow"><span>Color set<small>The colors of the screen. The key colors and the gold key stay as they are</small></span><div class="pals" id="setPalette">
      <button data-v="vesta" title="Vesta" style="--sw:#0b4cb0;--sw2:#16386a"></button><button data-v="ember" title="Ember" style="--sw:#a8391f;--sw2:#6b2a18"></button><button data-v="forest" title="Forest" style="--sw:#1a6b4a;--sw2:#1b5943"></button><button data-v="slate" title="Slate" style="--sw:#4a5568;--sw2:#39424f"></button><button data-v="plum" title="Plum" style="--sw:#6b3fa0;--sw2:#4a2a70"></button></div></div>
    <div class="setrow"><span>Text size</span><div class="seg" id="setSize"><button data-v="-">A−</button><b id="setSizeTxt">100%</b><button data-v="+">A+</button></div></div>
    <div class="setrow"><span>Sound on this computer<small>The notes also play on this device's speakers</small></span><button class="live" id="setSound"><i></i><span class="onoff"></span></button></div>
    <div class="setrow"><span>Sound<small>The voice of the sound on this device</small></span><select id="setVoice" class="setsel">
      <option value="grand">Grand piano</option><option value="epiano">Electric piano</option><option value="organ">Organ</option>
      <option value="musicbox">Music box</option><option value="synth">Soft synth</option></select></div>
    <div class="setrow"><span>Play with the computer's keys<small>Z to M and Q to P, the space bar is the pedal, ← and → move an octave</small></span><button class="live" id="setPcKeys"><i></i><span class="onoff"></span></button></div>
    <div class="setrow"><span>Live<small>Keys on the screen play the keyboard, and the keyboard lights them</small></span><button class="live" id="setLive"><i></i><span class="onoff"></span></button></div>
    <div class="setrow"><span>Profile<small>Progress, pace, language, colors and labels</small></span><button class="btn ghost" id="setProf"></button></div>
    <div class="setrow"><span>Keyboard</span><b id="setKbd"></b></div>
    <div class="setrow"><span>Ask for a code<small>A screen is asked once on each device, and holds for 14 days. Turning this off opens the piano to everyone on the network</small></span><button class="live" id="setCode"><i></i><span class="onoff"></span></button></div>
    <div class="setrow"><span>The code<small>Show it, or draw a new one. A new code does not sign the screens already in out</small></span><span><b id="setCodeVal">······</b> <button class="btn ghost" id="setCodeShow">Show</button> <button class="btn ghost" id="setCodeNew">New code</button></span></div>
    <div class="setrow"><span>Sign every screen out<small>Ends the pass on every device at once, this one aside. For a phone that was lost, or a code that was seen</small></span><button class="btn ghost" id="setSignOut">Sign out</button></div>
    <div class="setrow"><span>Status screen<small>The touch status screen, already signed in to the broker</small></span><a class="btn ghost" id="setStatus" href="/status" target="_blank" rel="noopener" style="text-decoration:none">Show status</a></div>
    <div class="setrow"><span>Edit the page<small>Turns on edit mode. Pick a part at the top of the page, then tap a block to rewrite it as HTML, in the language shown</small></span><button class="live" id="setEditMode"><i></i><span class="onoff"></span></button></div>
  </div>
  <div class="actions"><a class="btn ghost" href="/api/backup" data-save="piano-backup.zip" download style="text-decoration:none">Download a backup</a><button class="btn ghost" id="setRestore">Restore from a backup</button><button class="btn gold" id="setClose">Done</button></div>
</div></div>
<div id="edBar">
  <span class="edBarLbl">Editing</span>
  <button type="button" data-scope="content.">Content</button>
  <button type="button" data-scope="head.">Headings</button>
  <button type="button" data-scope="nav.">Menu</button>
  <button type="button" data-scope="foot.">Footer</button>
  <span class="edSpacer"></span>
  <button type="button" class="edDone" data-scope="">Done</button>
</div>
<div id="edModal"><div class="edBox">
  <div class="edTop"><b>Edit block</b><span class="edKey" id="edKey"></span><span class="edLang" id="edLang"></span></div>
  <div class="edPrevLbl">Preview</div>
  <div class="edPrev" id="edPrev"></div>
  <textarea id="edSrc" spellcheck="false" aria-label="HTML source"></textarea>
  <div class="edImgTools" id="edImgTools">
    <span class="edImgLbl">Align</span>
    <button type="button" data-al="right">Right</button>
    <button type="button" data-al="center">Center</button>
    <button type="button" data-al="left">Left</button>
    <span class="edImgLbl">Size</span>
    <button type="button" data-w="30">Small</button>
    <button type="button" data-w="60">Medium</button>
    <button type="button" data-w="100">Full</button>
  </div>
  <div class="edBtns">
    <button class="ghost" id="edImg" type="button">Add image</button>
    <input type="file" id="edFile" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
    <span class="edSpacer"></span>
    <button class="warn" id="edReset" type="button">Reset to default</button>
    <button class="ghost" id="edCancel" type="button">Cancel</button>
    <button class="save" id="edSave" type="button">Save</button>
  </div>
</div></div>
<div class="toast" id="toast"></div>

<script>
// a browser that kept the language or look of an earlier install of this address starts clean
try {
  if (localStorage.getItem('pianoInstall') !== '__INSTALL__') {
    Object.keys(localStorage).filter(k => k.indexOf('piano') === 0).forEach(k => localStorage.removeItem(k));
    localStorage.setItem('pianoInstall', '__INSTALL__');
  }
} catch (e) { /* no storage: nothing to clean */ }
// FIRST and LAST are filled in by the engine from the keyboard's measured range
const FIRST = /*FIRST*/36, LAST = /*LAST*/96;
const $ = id => document.getElementById(id);
const pc = m => ((m % 12) + 12) % 12;
const isBlack = m => [1, 3, 6, 8, 10].includes(pc(m));
// a black key takes the colour of the white on its left, as its number does (10# is right of 10)
const LETTER = ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B'];
const NAME = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const SOLFA = { C: 'Do', D: 'Re', E: 'Mi', F: 'Fa', G: 'Sol', A: 'La', B: 'Si' };
const colOf = m => 'var(--' + LETTER[pc(m)].toLowerCase() + ')';
let colorMode = (() => { try { return localStorage.getItem('pianoColors'); } catch (e) { return null; } })() || 'notes';
document.documentElement.dataset.colors = colorMode;
const darkText = m => colorMode === 'notes' && ['D', 'E'].includes(LETTER[pc(m)]);
const DEFAULT_LOOK = { theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light', palette: 'vesta', voice: 'grand', scale: 1, colors: 'notes', labels: 'num' };
let lookTouched = 0;     // when this device last changed the look: the piano's older answer does not undo it
const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } };
const nice = s => (s || '').replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
// messages from the engine name songs by their file name (ode_to_joy): shown as titles, except
// in a line with a command such as /lesson, where the file name is what has to be typed
const titles = t => /\\//.test(t) ? t : t.replace(/(^|[^\\w])([a-z0-9]+(?:_[a-z0-9]+)+)/gi, (m, pre, name) => pre + nice(name));

// ---------- language: English or Hebrew, one choice for the whole piano ----------
// Deliberately small: the English text is the key and HE holds its Hebrew. Text written in the
// page is translated where it stands; text built here goes through t(). In Hebrew the page
// mirrors, except the keyboard, which is a real keyboard and never flips.
const HE = {
  // header, hero, today
  'Play': 'נגינה', 'Songs': 'שירים', 'Progress': 'התקדמות', 'About': 'אודות', '‹ Back to the lessons': '› חזרה לשיעורים',
  'Vesta Core Labs': 'Vesta Core Labs', 'Where Armonico came from': 'איך Armonico התחיל', 'In daily use': 'בשימוש יומיומי',
  'Vesta Core Labs is my home lab. I build and document systems there the way they would run in production: virtualization, networking, security and home automation. I learn by building rather than by reading, and a project is written down once it works, because that is when the understanding settles. Each one is documented well enough that someone else, or a later version of me, can pick it up and see how it works and why it was built that way.':
    'Vesta Core Labs היא המעבדה הביתית שלי. אני בונה ומתעד בה מערכות כמו שהן היו רצות בסביבת ייצור: וירטואליזציה, רשתות, אבטחה ואוטומציה ביתית. אני לומד תוך כדי בנייה ולא מתוך קריאה, ופרויקט נכתב אחרי שהוא עובד, כי זה הרגע שבו ההבנה נקלטת. כל אחד מהם מתועד טוב מספיק כדי שמישהו אחר, או אני בעוד כמה זמן, יוכל להמשיך אותו ולראות איך הוא עובד ולמה הוא נבנה ככה.',
  'What it does': 'מה הוא עושה',
  'Armonico has two sides. It teaches: a song is cut into short parts, every white key carries a number, the screen lights the key to play next and names the finger for it, and each part comes back in later lessons until it holds. And it listens: the same keyboard is an input device for the house, so a short sequence of keys can set off a scene through MQTT, and the alarm in the morning is the piano itself playing, stopped by the lowest key. Both sides share one bridge and one set of commands, from the browser, from Telegram and from the terminal.':
    'ל-Armonico שני צדדים. הוא מלמד: השיר נחתך לחלקים קצרים, לכל קליד לבן יש מספר, המסך מאיר את הקליד הבא ואומר באיזו אצבע ללחוץ, וכל חלק חוזר בשיעורים הבאים עד שהוא נשאר. והוא מקשיב: אותה מקלדת היא אמצעי קלט לבית, כך שרצף קצר של קלידים מפעיל תרחיש דרך MQTT, והשעון המעורר בבוקר הוא הפסנתר עצמו מנגן, והקליד הנמוך עוצר אותו. שני הצדדים חולקים גשר אחד ואותן פקודות, מהדפדפן, מטלגרם ומהמסוף.',
  'It started as one automation, the piano playing a wake-up song every morning. My first songs I had learned from short videos, with the key numbers written on the white keys, repeating a part until my hands knew it without looking. That worked until I sat at a piano I could not write on, so the numbers moved to a screen. Over time I needed them less, and the automation grew into the lesson engine running here.':
    'הוא התחיל כאוטומציה אחת, הפסנתר מנגן שיר השכמה כל בוקר. את השירים הראשונים שלי למדתי מסרטונים קצרים, עם מספרי הקלידים כתובים על הקלידים הלבנים, וחזרתי על כל חלק עד שהידיים ידעו אותו בלי להסתכל. זה עבד עד שישבתי מול פסנתר שאי אפשר לכתוב עליו, אז המספרים עברו למסך. עם הזמן הצטרכתי אותם פחות, והאוטומציה גדלה למנוע השיעורים שרץ כאן.',
  'It has run on a real keyboard every day for more than half a year, with a real alarm every morning, and AI was among its tools. Whatever broke along the way is already fixed in what runs here.':
    'הוא רץ על מקלדת אמיתית כל יום במשך יותר מחצי שנה, עם שעון מעורר אמיתי כל בוקר, ו-AI היה בין הכלים. מה שנשבר בדרך כבר מתוקן במה שרץ כאן.',
  'Smaller text': 'טקסט קטן יותר', 'Bigger text': 'טקסט גדול יותר', 'Dark or light': 'מצב כהה או בהיר',
  'Checking…': 'בודק…', 'Keyboard connected': 'המקלדת מחוברת', 'Keyboard disconnected': 'המקלדת לא מחוברת',
  'All songs': 'כל השירים', 'Start the first lesson': 'להתחיל את השיעור הראשון',
  'Continue {song}': 'להמשיך את {song}', 'Start {song}': 'להתחיל את {song}',
  'Where you stand': 'איפה אתה עומד',
  'day streak': 'ימים ברצף', 'minutes this week': 'דקות השבוע', 'new parts this week': 'חלקים חדשים השבוע', 'last full run': 'נגינה מלאה אחרונה',
  'Today': 'היום', 'Your first lesson': 'השיעור הראשון שלך', 'Loading your songs…': 'טוען את השירים…',
  'Pace': 'קצב', 'Relaxed': 'רגוע', 'Steady': 'יציב', 'Fast': 'מהיר',
  '1 new part in each lesson, and a slower demo': 'חלק חדש אחד בכל שיעור, והדגמה איטית',
  '2 new parts in each lesson, and a medium demo': '2 חלקים חדשים בכל שיעור, והדגמה בינונית',
  '3 new parts in each lesson, and a faster demo': '3 חלקים חדשים בכל שיעור, והדגמה מהירה יותר',
  'Starts at {speed} by the pace, then follows you': 'מתחיל ב-{speed} לפי הקצב, ומשם מתאים את עצמו אליך',
  'Slows the demo down after two slips in a row, and speeds it up when a part comes out clean':
    'ההדגמה מאטה אחרי 2 טעויות ברצף, ומאיצה כשחלק יוצא נקי',
  'Tempo adapts to you': 'הטמפו מתאים את עצמו אליך', 'Demo speed': 'מהירות ההדגמה',
  // play
  'The keyboard on the screen follows the real one.': 'המקלדת במסך עוקבת אחרי המקלדת האמיתית.',
  'Play or pause (space)': 'ניגון או עצירה (רווח)', 'Position': 'מיקום', 'You': 'אתה', 'The lesson': 'השיעור', 'Close': 'סגירה',
  'Pedal': 'פדל', 'Stop lesson': 'לעצור את השיעור', 'Key colors': 'צבעי הקלידים', 'Colors': 'צבעים', 'Simple': 'פשוט',
  'Do Re Mi': 'דו רה מי', 'None': 'בלי', "Sustain pedal for the screen's keys": 'פדל המשך לקלידים שבמסך', 'Keyboard volume': 'עוצמת המקלדת',
  'Live': 'לייב', 'Live on': 'לייב פועל', 'Live off': 'לייב כבוי',
  'Computer sound': 'צליל במחשב', "Play the notes on this computer's speakers too": 'לנגן את התווים גם ברמקולים של המחשב',
  'Turn on Live to see and play the keys': 'צריך להפעיל לייב כדי לראות את הקלידים ולנגן', 'Play any key': 'אפשר לנגן על כל קליד',
  'You played ': 'ניגנת ', 'Lesson': 'שיעור',
  'Your turn, the key is lit': 'תורך, הקליד מואר', 'Your turn, from memory': 'תורך, מהזיכרון', 'Listen': 'הקשבה', 'Right!': 'נכון!',
  'Your turn, press': 'תורך', 'Listen, the keyboard plays': 'הקשבה, המקלדת מנגנת', 'Get ready': 'היכון',
  'Suggested finger: ': 'אצבע מומלצת: ', 'Note {i} is {key}': 'תו {i} הוא {key}',
  // songs
  'Your songs': 'השירים שלך', 'The ring shows how much of each song you have learned.': 'הטבעת מראה כמה מכל שיר כבר למדת.',
  'Add a song': 'הוספת שיר', '⬆ Upload a MIDI file': '⬆ העלאת קובץ MIDI',
  'A MIDI file (.mid) from this computer or phone. It can also be dropped here.': 'קובץ MIDI מהמחשב או מהטלפון. אפשר גם לגרור אותו לכאן.',
  'Search free archives, for example: minuet': 'חיפוש במאגרים חופשיים, למשל: minuet', 'Search free archives': 'חיפוש במאגרים חופשיים',
  'Search': 'חיפוש',
  'Searched in: Mutopia Project · Wikimedia Commons. Only archives whose files may be kept. The same search as /getmidi in Telegram.':
    'החיפוש הוא ב-Mutopia Project וב-Wikimedia Commons, רק מאגרים שמותר לשמור את הקבצים שלהם. זה אותו חיפוש כמו ‎/getmidi בטלגרם.',
  '{n} part': 'חלק אחד', '{n} parts': '{n} חלקים', '{n} part not steady yet': 'חלק אחד עוד לא יציב', '{n} parts not steady yet': '{n} חלקים עוד לא יציבים',
  '{n} new part': 'חלק חדש אחד', '{n} new parts': '{n} חלקים חדשים', '{n} short part': 'חלק קצר אחד', '{n} short parts': '{n} חלקים קצרים',
  'about {n} minute': 'בערך דקה', 'about {n} minutes': 'בערך {n} דקות',
  'The whole song is learned': 'כל השיר נלמד', '{n} of {t} parts learned': 'נלמדו {n} מתוך {t} חלקים', '{n} of {t} parts': '{n} מתוך {t} חלקים',
  'Not started · ': 'עוד לא התחיל · ', 'Learned': 'נלמד', 'Review {when}': 'חזרה {when}', 'Practiced {ago}': 'תורגל {ago}', 'Tap to start': 'לחיצה להתחלה',
  'Start lesson': 'התחלת שיעור', 'Review now': 'חזרה עכשיו', 'Continue': 'המשך', 'Practice': 'תרגול',
  ' · {a} settling, {b} steady': ' · {a} מתייצבים, {b} יציבים', ' · last full run {s}%': ' · נגינה מלאה אחרונה {s}%',
  'Start this song over': 'להתחיל את השיר מחדש',
  'By {author}': 'מאת {author}', 'Source and license of the file': 'המקור והרישיון של הקובץ',
  'Start {song} over? Its learned parts, review schedule and lessons in the history are cleared. Recordings stay.':
    'להתחיל את {song} מחדש? החלקים שנלמדו, לוח החזרות והשיעורים ברשימת ההתקדמות יימחקו. ההקלטות נשארות.',
  '{song} starts over': '{song} מתחיל מחדש', 'Not during a lesson': 'לא באמצע שיעור', 'Starting {song}…': 'מתחיל את {song}…',
  'not started': 'עוד לא התחיל', 'today': 'היום', 'yesterday': 'אתמול', '{n} days ago': 'לפני {n} ימים',
  'now': 'עכשיו', 'in {n} min': 'בעוד {n} דק׳', 'in {n} h': 'בעוד {n} שע׳', 'tomorrow': 'מחר', 'in {n} days': 'בעוד {n} ימים',
  'practice on {n} part that is not steady yet': 'תרגול של חלק אחד שעוד לא יציב',
  'practice on {n} parts that are not steady yet': 'תרגול של {n} חלקים שעוד לא יציבים',
  '{n} part is not steady yet, and the run at the start is what settles it': 'חלק אחד עוד לא יציב, והנגינה שבהתחלה היא שמייצבת אותו',
  '{n} parts are not steady yet, and the run at the start is what settles them': '{n} חלקים עוד לא יציבים, והנגינה שבהתחלה היא שמייצבת אותם',
  'a full run on your own and then with the band': 'נגינה מלאה לבד ואז עם הלהקה', 'a full run on your own': 'נגינה מלאה לבד',
  'the same run again at the end': 'אותה נגינה שוב בסוף',
  '{name} is too big for a MIDI file': '{name} גדול מדי בשביל קובץ MIDI', 'The keyboard machine did not answer': 'המחשב של המקלדת לא עונה',
  'Added {name}: {n} parts to learn': '{name} נוסף: {n} חלקים ללמידה', 'The file was not added': 'הקובץ לא נוסף',
  'Searching…': 'מחפש…', 'The search did not answer': 'החיפוש לא ענה', 'Nothing found': 'לא נמצא כלום',
  '. Is "{w}" spelled right?': '. האם "{w}" כתוב נכון?', 'Add': 'הוספה',
  '{n} results': '{n} תוצאות', 'page {p} of {n}': 'עמוד {p} מתוך {n}',
  // progress
  'Recent lessons': 'שיעורים אחרונים', 'Recordings': 'הקלטות', 'Your lessons will show up here.': 'השיעורים שלך יופיעו כאן.',
  'No recordings yet. Every lesson is recorded from its first key.': 'עדיין אין הקלטות. כל שיעור מוקלט מהקליד הראשון.',
  'Download the MIDI file': 'הורדת קובץ ה-MIDI', 'Replay on the screen and the keyboard': 'ניגון חוזר במסך ובמקלדת', 'Delete this recording': 'מחיקת ההקלטה',
  'Delete the recording of {song} from {date}?': 'למחוק את ההקלטה של {song} מ-{date}?', 'Recording deleted': 'ההקלטה נמחקה',
  '{n} min': '{n} דק׳', 'stopped': 'נעצר', 'Yesterday': 'אתמול',
  // how a lesson works
  'How a lesson works': 'איך שיעור עובד',
  'A lesson opens with a full run of everything learned so far, from the first note: once on your own, then the same again with the band. That first run is what decides which parts are steady.':
    'שיעור נפתח בנגינה מלאה של כל מה שנלמד עד עכשיו, מהתו הראשון: פעם אחת לבד, ואז שוב עם הלהקה. הנגינה הראשונה הזאת היא שקובעת אילו חלקים יציבים.',
  'Then come the new parts, each one first with the key numbers, the colors and a demo, then with only the key lit, and last from memory. A part that comes out from memory is joined to the part before it, and the lesson ends with the same two runs again, now with today’s part in them.':
    'אחריה מגיעים החלקים החדשים, כל אחד קודם עם מספרי הקלידים, הצבעים והדגמה, אחר כך רק עם הקליד המואר, ולבסוף מהזיכרון. חלק שיוצא מהזיכרון מתחבר לחלק שלפניו, והשיעור נגמר באותן שתי נגינות שוב, הפעם כולל החלק של היום.',
  'More about the method': 'עוד על השיטה', '›': '‹',
  'The method in detail': 'השיטה בפירוט',
  "The lessons teach the melody of a song, cut into short parts where the music breathes. A few new parts in each lesson, at the pace you choose, and another lesson can start right after. Each part comes back until it holds. A mode for the full piano part, with the left hand's accompaniment, is planned.":
    'השיעורים מלמדים את המנגינה של השיר, מחולקת לחלקים קצרים במקומות שבהם המוזיקה נושמת. כמה חלקים חדשים בכל שיעור, בקצב שבוחרים, ואפשר להתחיל שיעור נוסף מיד אחריו. כל חלק חוזר עד שהוא מתייצב. מתוכנן גם מצב לפסנתר המלא, עם הליווי של יד שמאל.',
  'Everything learned so far, in order, from the first note of the song. First on your own, then the same again with the band. The run on your own is the one that is read: the band does not lead, but chords under the melody are themselves a reminder of what comes next. This run opens the lesson on purpose, when the parts were last played a day or a week ago and nothing has warmed them up.':
    'כל מה שנלמד עד עכשיו, לפי הסדר, מהתו הראשון של השיר. קודם לבד, ואז שוב עם הלהקה. הנגינה לבד היא זו שנקראת: הלהקה לא מובילה, אבל האקורדים מתחת למנגינה הם בעצמם תזכורת למה שבא אחר כך. הנגינה הזאת פותחת את השיעור בכוונה, כשהחלקים נוגנו לאחרונה לפני יום או שבוע ושום דבר עוד לא חימם אותם.',
  'The help comes off in 3 steps: numbers, colors and a demo, then only the key lit, then from memory. A wrong key gets one more try before the answer shows, because finding it yourself is what makes it stick. Each new part is then played together with the part before it, from memory, so the song grows as one line.':
    'העזרה יורדת ב-3 שלבים: מספרים, צבעים והדגמה, אחר כך רק הקליד מואר, ואז מהזיכרון. אחרי קליד שגוי יש עוד ניסיון אחד לפני שהתשובה מוצגת, כי מה שמקבע את הזיכרון זה למצוא אותה בעצמך. כל חלק חדש מנוגן אחר כך יחד עם החלק שלפניו, מהזיכרון, כך שהשיר גדל כקו אחד.',
  'The run that measures': 'הנגינה שמודדת',
  'The help comes off in 3 steps: numbers, colors and a demo, then only the key lit, then from memory. A wrong key gets one more try before the answer shows, because finding it yourself is what makes it stick.':
    'העזרה יורדת ב-3 שלבים: מספרים, צבעים והדגמה, אחר כך רק הקליד מואר, ואז מהזיכרון. אחרי קליד שגוי יש עוד ניסיון אחד לפני שהתשובה מופיעה, כי כשמוצאים לבד זה נקלט.',
  'New parts, and connecting them': 'חלקים חדשים, והחיבור ביניהם', 'Each new part is played together with the part before it, from memory.': 'כל חלק חדש מנוגן יחד עם החלק שלפניו, מהזיכרון.',
  'The same run again, at the end': 'אותה נגינה שוב, בסוף', 'When a part counts as steady': 'מתי חלק נחשב יציב',
  'The whole song so far with today’s part in it, on your own and then with the band, scored on the right notes, the rhythm and the hesitations. It comes minutes after practising those same notes, so it says how the lesson went rather than what will still be there tomorrow, and it does not move a part’s count. Comparing it with the run at the start is what shows the day’s gain.':
    'כל השיר עד כה כולל החלק של היום, לבד ואז עם הלהקה, עם ציון על התווים הנכונים, על הקצב ועל ההיסוסים. היא מגיעה דקות אחרי שתרגלת בדיוק את התווים האלה, ולכן היא אומרת איך השיעור עבר ולא מה יישאר מחר, והיא לא מזיזה את המונה של אף חלק. ההשוואה בינה לבין הנגינה שבהתחלה היא שמראה את מה שהרווחת היום.',
  'A melody is remembered forward, from its beginning, not from the middle, so there is no separate test of a part on its own. The runs are the test: a part played clean in the run at the start counts clean, a part that slipped there starts counting from 0. A part counts as steady after 3 clean opening runs in a row. Whatever slipped in either run is practiced again right after it, with the numbers back on, up to two parts per run. The beginning of a song is played in every run and settles first, and the newest parts get the most work, which is also the order in which a song is really learned.':
    'מנגינה נזכרת קדימה, מההתחלה שלה, לא מהאמצע, ולכן אין מבחן נפרד לחלק בפני עצמו. הנגינות הן המבחן: חלק שיצא נקי בנגינה שבהתחלה נספר נקי, וחלק שנפלת בו שם מתאפס. חלק נחשב יציב אחרי 3 נגינות פתיחה נקיות ברצף. מה שנפל באחת הנגינות מתורגל מיד אחריה, עם המספרים חזרה על המסך, עד שני חלקים לכל נגינה. ההתחלה של שיר מנוגנת בכל נגינה ומתייצבת ראשונה, והחלקים החדשים מקבלים הכי הרבה עבודה, וזה גם הסדר שבו שיר באמת נלמד.',
  'Why colors, when a real piano has none?': 'למה צבעים, אם בפסנתר אמיתי אין?',
  'They are training wheels. Every C has the same color, so while a part is new you find its keys at a glance and your attention stays on the music. A teacher in a lesson does the same by pointing at the keys. The rounds from memory take the colors and numbers away together, so what stays with you is where the key is on the piano, not its color. Learners who only ever practiced with colors tend to lean on them, and these rounds are what prevent that.':
    'הם גלגלי עזר. לכל דו יש אותו צבע, אז כשחלק עוד חדש מוצאים את הקלידים שלו במבט אחד, והקשב נשאר על המוזיקה. מורה בשיעור עושה אותו דבר כשהוא מצביע על הקלידים. בסבבים מהזיכרון הצבעים והמספרים יורדים יחד, כך שמה שנשאר הוא איפה הקליד נמצא על הפסנתר ולא הצבע שלו. מי שמתרגל רק עם צבעים נוטה להישען עליהם, והסבבים האלה מונעים את זה.',
  // footer
  'Piano lessons on a MIDI keyboard, and smart home control from the same keys.': 'שיעורי פסנתר על מקלדת MIDI, ושליטה בבית החכם מאותם קלידים.',
  'A song is cut into short parts, each part is learned until it comes out from memory, and it comes back in later lessons until it stays.':
    'השיר נחתך לחלקים קצרים, כל חלק נלמד עד שהוא יוצא מהזיכרון, וחוזר בשיעורים הבאים עד שהוא נשאר.',
  'Project': 'הפרויקט', 'Documentation': 'תיעוד', 'Installation': 'התקנה', 'Help': 'עזרה', 'Questions and pitfalls': 'שאלות ותקלות נפוצות',
  'Report a problem': 'דיווח על בעיה', 'Download a backup': 'הורדת גיבוי',
  'Version': 'גרסה', 'Check for updates': 'בדיקת עדכונים',
  '· last update failed: {m}': '· העדכון האחרון נכשל: {m}', ' · Install {v}': ' · התקנת {v}',
  'Install version {v}? The keyboard is unavailable for a minute or two, and settings, songs and progress stay.':
    'להתקין את גרסה {v}? המקלדת לא תהיה זמינה לדקה או שתיים, וההגדרות, השירים וההתקדמות נשמרים.',
  'Updating to {v}…': 'מעדכן לגרסה {v}…', 'Not now': 'לא עכשיו', '· this is the newest version': '· זו הגרסה העדכנית',
  'Downloading {x}': 'מוריד את {x}', 'Installing {x}': 'מתקין את {x}', 'No valid version to install': 'אין גרסה תקינה להתקנה',
  'The download of {x} failed': 'ההורדה של {x} נכשלה', 'The download was damaged': 'ההורדה פגומה',
  'The release has no installer': 'בגרסה הזאת אין מתקין', 'The installer stopped. Details: {x}': 'המתקין נעצר. פרטים: {x}',
  // end of lesson, replay, the code
  'Lesson done': 'השיעור הסתיים',
  'Restore from a backup': 'שחזור מגיבוי', 'Backup file (.zip)': 'קובץ גיבוי (.zip)', 'It will be restored into {p}, which becomes the profile at the piano.': 'השחזור ייעשה לפרופיל {p}, והוא יהפוך לפרופיל שליד הפסנתר.', 'That profile was deleted: it will be created again.': 'הפרופיל הזה נמחק: הוא ייווצר מחדש.', 'PIN of {p}': 'קוד PIN של {p}', 'Restored into {p}': 'שוחזר לפרופיל {p}', 'The progress, history and settings of {p} are replaced, and {p} becomes the profile at the piano. Continue?': 'ההתקדמות, ההיסטוריה וההגדרות של {p} יוחלפו, ו-{p} יהפוך לפרופיל שליד הפסנתר. להמשיך?', 'This backup was made by a newer version, or is not an Armonico backup': 'הגיבוי נוצר בגרסה חדשה יותר, או שאינו גיבוי של Armonico', 'This is not a backup this version can restore': 'זה לא גיבוי שהגרסה הזאת יכולה לשחזר', 'The backup holds a damaged file': 'בגיבוי יש קובץ פגום', 'There is room for 12 profiles': 'יש מקום ל-12 פרופילים', 'A backup of {p}{d}': 'גיבוי של {p}{d}', 'This backup holds several profiles. Take the data of': 'הגיבוי הזה מכיל כמה פרופילים. לקחת את הנתונים של',
  'Restore': 'שחזור', 'The backup was restored': 'הגיבוי שוחזר', 'The restore failed': 'השחזור נכשל', 'The download failed': 'ההורדה נכשלה',
  'This is not a backup file': 'זה לא קובץ גיבוי', 'This is not an Armonico backup': 'זה לא גיבוי של Armonico', 'The backup is too large': 'הגיבוי גדול מדי', 'Choose the backup file again': 'יש לבחור שוב את קובץ הגיבוי',
  'Choose a backup file you downloaded here before. Only a backup made by Armonico is accepted. It goes back into the profile it was made from, which becomes the profile at the piano, and that profile is made again if it was deleted. Its progress, history and settings are replaced. Recordings and songs are added, and nothing already here is deleted.': 'בחר קובץ גיבוי שהורדת מכאן בעבר. מתקבל רק גיבוי שנוצר ב-Armonico. הוא חוזר לפרופיל שממנו נוצר, והפרופיל הזה הופך לפרופיל שליד הפסנתר ונוצר מחדש אם נמחק. ההתקדמות, ההיסטוריה וההגדרות שלו מוחלפות. הקלטות ושירים מתווספים, ושום דבר שכבר קיים לא נמחק.',
  'The progress, history and settings of {p} are replaced. Continue?': 'ההתקדמות, ההיסטוריה וההגדרות של {p} יוחלפו. להמשיך?',
  '{n} song in the backup is added if it is not here yet': 'שיר {n} מהגיבוי יתווסף אם עדיין אין אותו כאן', '{n} songs in the backup are added if they are not here yet': '{n} שירים מהגיבוי יתווספו אם עדיין אין אותם כאן', 'The keyboard was disconnected or switched off in the middle of the lesson. Your progress is saved.': 'המקלדת התנתקה או כובתה באמצע השיעור. ההתקדמות נשמרה.', 'You learned the whole song': 'למדת את כל השיר', 'Lesson stopped': 'השיעור נעצר',
  'parts of the song learned': 'חלקים מהשיר נלמדו', 'new today': 'חדשים היום', 'full run': 'נגינה מלאה', 'minutes': 'דקות',
  'mistakes': 'טעויות', 'parts practiced again': 'חלקים שתורגלו שוב', '▶ Watch the replay': '▶ צפייה בהקלטה', 'Download MIDI': 'הורדת MIDI',
  'On your own at the start: {n} part, {c} clean': 'בהתחלה לבד: חלק אחד, {c} נקי',
  'On your own at the start: {n} parts, {c} clean': 'בהתחלה לבד: {n} חלקים, {c} נקיים',
  'On your own at the end: {n} part, {c} clean': 'בסוף לבד: חלק אחד, {c} נקי',
  'On your own at the end: {n} parts, {c} clean': 'בסוף לבד: {n} חלקים, {c} נקיים',
  'Full run: notes {a}%': 'נגינה מלאה: תווים {a}%', 'Full run with the band: notes {a}%': 'נגינה מלאה עם הלהקה: תווים {a}%',
  ' · rhythm {r}%': ' · קצב {r}%', ' · {n} hesitation': ' · היסוס אחד', ' · {n} hesitations': ' · {n} היסוסים',
  ' · {n} wrong key': ' · קליד שגוי אחד', ' · {n} wrong keys': ' · {n} קלידים שגויים',
  'Parts: {a} settling, {b} steady': 'חלקים: {a} מתייצבים, {b} יציבים', ' · next review {when}': ' · החזרה הבאה {when}',
  '{p}: {n} mistake': '{p}: טעות אחת', '{p}: {n} mistakes': '{p}: {n} טעויות', 'No mistakes.': 'בלי טעויות.',
  'The recording could not be opened': 'אי אפשר לפתוח את ההקלטה', '🙋 You play': '🙋 אתה מנגן', '🎹 The lesson plays': '🎹 השיעור מנגן',
  '🦶 Pedal down': '🦶 הפדל לחוץ', 'Pedal up': 'הפדל משוחרר',
  'Too many wrong codes. Wait a minute and try again': 'יותר מדי קודים שגויים. כדאי לחכות דקה ולנסות שוב',
  'The keyboard is busy: a lesson, a song or the alarm is playing': 'המקלדת תפוסה: שיעור, שיר או השעון המעורר מנגנים עכשיו',
  'This screen is unlocked': 'המסך פתוח', 'Too many wrong codes. Wait a minute': 'יותר מדי קודים שגויים. כדאי לחכות דקה', 'Wrong code': 'קוד שגוי',
  'This screen': 'המסך הזה', 'Enter the screen code': 'קוד המסך',
  'A new code was drawn': 'הוגרל קוד חדש',
  'Draw a new code? The old one stops working': 'להגריל קוד חדש? הקוד הישן מפסיק לעבוד',
  'Every other screen was signed out': 'כל שאר המסכים נותקו',
  'Sign every screen out? Every other device will be asked for the code again': 'לנתק את כל המסכים? כל מכשיר אחר יידרש לקוד מחדש',
  'Turn the code off? Every device on the network will be able to use the piano': 'לכבות את הקוד? כל מכשיר ברשת יוכל להשתמש בפסנתר',
  'Ask for a code': 'לבקש קוד',
  'A screen is asked once on each device, and holds for 14 days. Turning this off opens the piano to everyone on the network':
    'המסך נדרש לקוד פעם אחת בכל מכשיר, והוא תקף ל-14 יום. כיבוי פותח את הפסנתר לכל מי שברשת',
  'The code': 'הקוד',
  'Show it, or draw a new one. A new code does not sign the screens already in out':
    'להציג אותו, או להגריל חדש. קוד חדש לא מנתק את המסכים שכבר נכנסו',
  'Show': 'הצג', 'New code': 'קוד חדש',
  'Sign every screen out': 'לנתק את כל המסכים',
  'Ends the pass on every device at once, this one aside. For a phone that was lost, or a code that was seen':
    'מסיים את האישור בכל המכשירים בבת אחת, חוץ מהמכשיר הזה. לטלפון שאבד, או לקוד שנראה',
  'Sign out': 'נתק',
  'This device keeps one pass in the browser: an end date and a signature, nothing about who is holding it. It stays on this device, is never sent anywhere else, and "Sign every screen out" in the settings ends it.':
    'המכשיר הזה שומר בדפדפן אישור אחד: תאריך תפוגה וחתימה, בלי שום דבר על מי מחזיק בו. הוא נשאר במכשיר, לא נשלח לשום מקום, ו"לנתק את כל המסכים" בהגדרות מסיים אותו.',
  'Only screens with the code can start lessons, play the keyboard or change its volume. It is asked once on each device and holds for 14 days, renewed every time the screen is used.':
    'רק מסך עם הקוד יכול להתחיל שיעורים, לנגן במקלדת או לשנות את העוצמה שלה. הקוד נדרש פעם אחת בכל מכשיר ותקף ל-14 יום, שמתחדשים בכל שימוש במסך.',
  'Get it in Telegram with': 'אפשר לקבל אותו בטלגרם עם', ', or on the machine with': ', או במחשב עצמו עם', 'Unlock': 'פתיחה', 'Cancel': 'ביטול',
  // profiles
  'Who is playing': 'מי מנגן', 'Who is playing?': 'מי מנגן?', 'Main': 'ראשי',
  'Each profile has its own progress, recordings, pace and language. The songs are shared.': 'לכל פרופיל התקדמות, הקלטות, קצב ושפה משלו. השירים משותפים לכולם.',
  'Playing now': 'ליד הפסנתר עכשיו', 'Delete this profile': 'מחיקת הפרופיל', 'PIN for': 'הקוד האישי של', 'To delete, the PIN of': 'למחיקה, הקוד האישי של',
  'Add a profile': 'הוספת פרופיל', 'Name': 'שם', 'PIN (optional)': 'קוד אישי (לא חובה)',
  'Edit my profile': 'עריכת הפרופיל שלי', 'New PIN (empty: keep it)': 'קוד אישי חדש (ריק: בלי שינוי)', 'Remove the PIN': 'להסיר את הקוד האישי',
  'Current PIN': 'הקוד האישי הנוכחי', 'Save': 'שמירה',
  'Delete {name}? Its progress and recordings are deleted too.': 'למחוק את {name}? גם ההתקדמות וההקלטות שלו יימחקו.',
  'Wrong PIN': 'קוד אישי שגוי', 'That name is already taken': 'השם הזה כבר תפוס', 'There is room for 12 profiles': 'יש מקום ל-12 פרופילים',
  'Not while a lesson or a replay is playing': 'לא בזמן שיעור או הקלטה שמתנגנת', 'A name is needed, and a PIN is 4 to 8 digits': 'צריך שם, וקוד אישי הוא 4 עד 8 ספרות',
  'That did not work': 'זה לא הצליח', 'Hi, {name}': 'היי, {name}', 'Profile added': 'הפרופיל נוסף', 'Saved': 'נשמר',
  // credits on the about page
  'Piano sound': 'צליל הפסנתר',
  'The grand piano on this screen is the Salamander Grand Piano by Alexander Holm, a Yamaha C5 recorded note by note, under Creative Commons Attribution 3.0. The files are the subset prepared by the Tone.js project.':
    'פסנתר הכנף במסך הזה הוא Salamander Grand Piano של אלכסנדר הולם, פסנתר Yamaha C5 שהוקלט תו אחרי תו, ברישיון Creative Commons Attribution 3.0. הקבצים הם המבחר שהכין פרויקט Tone.js.',
  'Credits': 'קרדיטים', 'Computer keys': 'מקשי המחשב', 'Method': 'השיטה', 'Font': 'גופן', 'Libraries': 'ספריות',
  'Free archives whose files may be kept. The source and license of each song are on its card.': 'מאגרים חופשיים שמותר לשמור את הקבצים שלהם. המקור והרישיון של כל שיר מופיעים על הכרטיס שלו.',
  "Playing with the computer's keys follows Virtual Piano, where I learned a little before I had a keyboard.": 'הרעיון לנגן במקשי המחשב בא מהאתר Virtual Piano, שבו למדתי קצת לפני שהייתה לי מקלדת.',
  'Each part comes back until it holds, an idea borrowed from flashcard apps such as Anki.': 'כל חלק חוזר עד שהוא נקלט, רעיון שנלקח מאפליקציות כרטיסיות כמו Anki.',
  'Heebo, by the Heebo Project Authors, under the SIL Open Font License 1.1. Included with the project, so the screen needs no internet.': 'הגופן Heebo של יוצרי פרויקט Heebo, ברישיון SIL Open Font License 1.1. הגופן כלול בפרויקט, כך שהמסך עובד גם בלי אינטרנט.',
  'mido for MIDI files, and Eclipse Paho for MQTT.': 'ספריית mido לקובצי MIDI, וספריית Eclipse Paho לחיבור MQTT.',
  // computer keys and settings
  'The computer key that plays each note': 'איזה מקש במחשב מנגן כל תו',
  'Settings': 'הגדרות', 'Language': 'שפה', 'Theme': 'ערכת צבעים', 'Light': 'בהירה', 'Dark': 'כהה', 'Text size': 'גודל טקסט',
  'Sound on this computer': 'צליל במכשיר הזה', "The notes also play on this device's speakers": 'התווים מתנגנים גם ברמקולים של המכשיר',
  "Play with the computer's keys": 'נגינה במקשי המחשב', 'Z to M and Q to P, the space bar is the pedal, ← and → move an octave': 'שורת Z עד M ושורת Q עד P, רווח הוא הפדל, והחצים מזיזים אוקטבה',
  'Keys on the screen play the keyboard, and the keyboard lights them': 'הקלידים במסך מנגנים במקלדת, והמקלדת מאירה אותם במסך',
  'Keyboard': 'מקלדת', 'Profile': 'פרופיל', 'Progress, pace, language, colors and labels': 'התקדמות, קצב, שפה, צבעים וסימון הקלידים', '{n} keys, from {a} to {b}': '{n} קלידים, מ-{a} עד {b}', 'On': 'פועל', 'Off': 'כבוי', 'Done': 'סיום',
  'Computer keys: Z to M and Q to P play, the space bar is the pedal, ← and → move an octave. Q is key': 'מקשי המחשב מנגנים: שורת Z עד M ושורת Q עד P. מקש הרווח הוא הפדל, והחצים ימינה ושמאלה מזיזים אוקטבה. Q הוא הקליד',
  'While this screen is open, keys on the keyboard trigger no shortcuts or automations. The stop key still works.': 'כל עוד המסך הזה פתוח, המקלדת לא מפעילה קיצורי דרך ואוטומציות. מקש העצירה ממשיך לעבוד.',
  'Sound': 'צליל', 'The voice of the sound on this device': 'הצליל שמתנגן במכשיר הזה',
  'Grand piano': 'פסנתר כנף', 'Electric piano': 'פסנתר חשמלי', 'Organ': 'אורגן', 'Music box': 'תיבת נגינה', 'Soft synth': 'סינתיסייזר רך',
  'Status screen': 'מסך סטטוס', 'The touch status screen, already signed in to the broker': 'מסך המגע של הסטטוס, כבר מחובר לברוקר', 'Show status': 'הצגת סטטוס',
  'Edit the page': 'עריכת העמוד', 'Turns on edit mode. Pick a part at the top of the page, then tap a block to rewrite it as HTML, in the language shown': 'מפעיל מצב עריכה. בוחרים חלק למעלה בעמוד, ואז לוחצים על בלוק כדי לכתוב אותו מחדש כ-HTML, בשפה שמוצגת',
  'Your progress': 'ההתקדמות שלך', 'Every lesson you played, and its recording.': 'כל שיעור שניגנת, וההקלטה שלו.',
  'About the project': 'אודות הפרויקט',
  'System': 'מערכת', 'Uploaded': 'הועלה', 'Delete this song': 'מחיקת השיר',
  'Delete {song}? The file and its progress are removed.': 'למחוק את {song}? הקובץ וההתקדמות שלו יימחקו.',
  '{song} deleted': '{song} נמחק',
  // edit mode (a private tool, shown in both languages all the same)
  'Edit': 'עריכה', 'Content': 'תוכן', 'Headings': 'כותרות', 'Menu': 'תפריט', 'Footer': 'פוטר', 'Done': 'סיום',
  'Edit block': 'עריכת בלוק', 'Preview': 'תצוגה מקדימה', 'Replace image': 'החלפת תמונה', 'Add image': 'הוספת תמונה',
  'Reset to default': 'איפוס לברירת מחדל', 'Tap a block to edit it': 'לחיצה על בלוק פותחת אותו לעריכה',
  'Uploading…': 'מעלה…', 'The upload failed': 'ההעלאה נכשלה', 'Editing': 'עריכה',
  'Page {n}': 'עמוד {n}',
  'Color set': 'ערכת צבעים',
  'The colors of the screen. The key colors and the gold key stay as they are':
    'צבעי המסך. צבעי הקלידים והקליד המוזהב נשארים כמו שהם',
  'Vesta': 'וסטה', 'Ember': 'גחלת', 'Forest': 'יער', 'Slate': 'צפחה', 'Plum': 'שזיף',
  'Align': 'יישור', 'Size': 'גודל', 'Right': 'ימין', 'Center': 'מרכז', 'Left': 'שמאל',
  'Small': 'קטן', 'Medium': 'בינוני', 'Full': 'מלא',
};
let LANG = store('pianoLang') === 'he' ? 'he' : 'en';   // English until this device picks a language itself
let VER_TEXT = '__VERSION__';        // filled in by the server: right on the first visit, before any request
function paintVersion() { const v = document.getElementById('ver'); if (v && VER_TEXT) v.textContent = VER_TEXT; }
const OV = __OVERRIDES__;   // wording edited on this screen, kept in the data folder, replacing the text below
const t = (s, v) => {
  const o0 = OV[LANG][s] !== undefined ? OV[LANG][s] : (LANG === 'he' && HE[s] !== undefined ? HE[s] : s);
  let o = o0;
  if (v) for (const k in v) o = o.split('{' + k + '}').join(v[k]);
  return o;
};
const tn = (n, one, many, v) => t(n === 1 ? one : many, Object.assign({ n }, v || {}));
const loc = () => LANG === 'he' ? 'he-IL' : undefined;
// "a, b and c": in Hebrew the "and" is a letter joined to the next word, with a hyphen before a digit
function joinAnd(bits) {
  if (bits.length < 2) return bits[0] || '';
  const last = bits[bits.length - 1];
  return bits.slice(0, -1).join(', ') + (LANG === 'he' ? ' ו' + (/^\\d/.test(last) ? '-' : '') : ' and ') + last;
}
// the text written in the page itself: each text node and label keeps its English, and shows it in the language
const SKIP = new Set(['SCRIPT', 'STYLE', 'svg', 'CODE']);
// Each [data-tkey] is one editable block. Its built-in HTML is kept in _def, and an edit for the
// current language (OV[LANG][key]) replaces the whole block as free HTML. A block with no edit for
// the current language falls back to its built-in wording, translated the usual way below.
function initBlocks() {
  document.querySelectorAll('[data-tkey]').forEach(el => { if (el._def === undefined) el._def = el.innerHTML; });
}
function applyOverrideBlocks() {
  initBlocks();
  document.querySelectorAll('[data-tkey]').forEach(el => {
    const ov = OV[LANG] ? OV[LANG][el.dataset.tkey] : undefined;
    if (ov !== undefined) { el.innerHTML = ov; el.dataset.ovOn = '1'; }
    else if (el.dataset.ovOn) { el.innerHTML = el._def; delete el.dataset.ovOn; }   // back to the built-in text
  });
}
function translatePage() {
  applyOverrideBlocks();
  const walk = el => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        if (n._en === undefined) n._en = n.nodeValue;
        const core = n._en.trim();
        if (core) {
          const val = t(core);
          if (val.indexOf('<') !== -1) {                 // an edit that carries formatting: shown as HTML
            let h = n._holder;
            if (!h) { h = document.createElement('span'); h.className = 'ovHtml'; h.dataset.key = core; n.parentNode.insertBefore(h, n); n._holder = h; n.nodeValue = ''; }
            h.innerHTML = val;
            h.style.whiteSpace = val.indexOf('\\n') !== -1 ? 'pre-wrap' : '';
          } else {
            if (n._holder) { n._holder.remove(); n._holder = null; }
            n.nodeValue = n._en.replace(core, val);
            if (n.parentNode && n.parentNode.nodeType === 1) n.parentNode.style.whiteSpace = val.indexOf('\\n') !== -1 ? 'pre-wrap' : '';
          }
        }
      } else if (n.nodeType === 1 && !SKIP.has(n.nodeName) && n.id !== 'kb' && n.id !== 'edPrev' && n.id !== 'edSrc') {
        if (n.dataset && n.dataset.ovOn) continue;       // an edited block: its HTML is authored, leave it be
        for (const a of ['title', 'placeholder', 'aria-label']) {
          if (!n.hasAttribute(a)) continue;
          if (n['_en_' + a] === undefined) n['_en_' + a] = n.getAttribute(a);
          n.setAttribute(a, t(n['_en_' + a]));
        }
        walk(n);
      }
    }
  };
  walk(document.body);
  paintVersion();       // the footer line is rewritten with the text: the version goes back into it
}
// each language with the flag of its country and its own name, so it is found in any language
const FLAG = {
  en: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 30" preserveAspectRatio="none"><clipPath id="s"><path d="M0,0v30h60V0z"/></clipPath><clipPath id="t"><path d="M30,15h30v15zv15H0zH0V0zV0h30z"/></clipPath><g clip-path="url(#s)"><path d="M0,0v30h60V0z" fill="#012169"/><path d="M0,0L60,30M60,0L0,30" stroke="#fff" stroke-width="6"/><path d="M0,0L60,30M60,0L0,30" clip-path="url(#t)" stroke="#C8102E" stroke-width="4"/><path d="M30,0v30M0,15h60" stroke="#fff" stroke-width="10"/><path d="M30,0v30M0,15h60" stroke="#C8102E" stroke-width="6"/></g></svg>'),
  he: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 16" preserveAspectRatio="none"><rect width="22" height="16" fill="#fff"/><rect y="1.5" width="22" height="2.4" fill="#0038b8"/><rect y="12.1" width="22" height="2.4" fill="#0038b8"/><path d="M11 5 13.6 9.5H8.4Z M11 11 8.4 6.5H13.6Z" fill="none" stroke="#0038b8" stroke-width=".75"/></svg>'),
};
const LANG_NAME = { en: 'English', he: 'עברית' };
const flagImg = l => { const i = document.createElement('img'); i.className = 'flag'; i.src = FLAG[l]; i.alt = ''; return i; };
async function setLang(l) {
  if (l === LANG) return;
  LANG = l; store('pianoLang', LANG); applyLang();
  prevMsg = ''; paint(state);
  await post('/settings', { lang: LANG }).catch(() => {});    // saved first: the lesson messages and Telegram follow
  loadOverview(); showUpdate(false);
}
document.querySelectorAll('.langPick').forEach(box => {
  const cur = document.createElement('button'); cur.className = 'langCur'; cur.setAttribute('aria-haspopup', 'listbox');
  const list = document.createElement('ul'); list.className = 'langList'; list.setAttribute('role', 'listbox');
  for (const l of ['en', 'he']) {
    const li = document.createElement('li'); li.dataset.l = l; li.lang = l; li.tabIndex = 0; li.setAttribute('role', 'option');
    li.append(flagImg(l), LANG_NAME[l]);
    li.onclick = () => { box.classList.remove('open'); setLang(l); };
    li.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); li.click(); } };
    list.appendChild(li);
  }
  cur.onclick = e => { e.stopPropagation(); document.querySelectorAll('.langPick.open').forEach(o => o !== box && o.classList.remove('open')); box.classList.toggle('open'); };
  box.append(cur, list);
});
document.addEventListener('click', e => { if (!e.target.closest('.langPick')) document.querySelectorAll('.langPick.open').forEach(o => o.classList.remove('open')); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.langPick.open').forEach(o => o.classList.remove('open')); });
function applyLang() {
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === 'he' ? 'rtl' : 'ltr';
  document.querySelectorAll('.langPick').forEach(box => {
    const cur = box.querySelector('.langCur');
    cur.replaceChildren(flagImg(LANG), Object.assign(document.createElement('span'), { textContent: '▾' }));
    cur.title = 'Language · שפה';
    box.querySelectorAll('li').forEach(li => { li.classList.toggle('on', li.dataset.l === LANG); li.setAttribute('aria-selected', li.dataset.l === LANG); });
  });
  translatePage();
}
applyLang();


// ---------- which device: phone, tablet or desktop; ?device=phone forces one ----------
function detectDevice() {
  const forced = new URLSearchParams(location.search).get('device');
  if (['phone', 'tablet', 'desktop'].includes(forced)) return forced;
  const ua = navigator.userAgent || '';
  const touch = navigator.maxTouchPoints > 1;
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && touch)) return 'tablet';   // iPadOS says it is a Mac
  if (/iPhone|iPod/.test(ua)) return 'phone';
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? 'phone' : 'tablet';
  if (navigator.userAgentData && navigator.userAgentData.mobile) return 'phone';
  if (/Tablet|Silk|Kindle|PlayBook/.test(ua)) return 'tablet';
  const touchOnly = matchMedia('(pointer: coarse)').matches && !matchMedia('(any-pointer: fine)').matches;
  if (touchOnly) return Math.min(screen.width, screen.height) < 600 ? 'phone' : 'tablet';
  return 'desktop';
}
const DEVICE = detectDevice();
document.documentElement.dataset.device = DEVICE;

// ---------- size: everything is in rem, and the root size follows the window ----------
// The same idea as the MFA screen: a scale from the real size of the window, width weighted
// more than height, so a 55" TV, a laptop at 75% zoom and a phone all get readable text.
// A- and A+ add a personal factor on top, remembered on this device.
let userScale = parseFloat(store('pianoScale')) || 1, SCALE = 1;
function applyScale() {
  const auto = 0.7 * (innerWidth / 1440) + 0.3 * (innerHeight / 900);
  SCALE = Math.min(2.6, Math.max(DEVICE === 'phone' ? 0.95 : 1, auto)) * userScale;
  document.documentElement.style.fontSize = (16 * SCALE).toFixed(2) + 'px';
}
applyScale();
function setScale(v, save) {
  userScale = v; store('pianoScale', userScale); applyScale(); layout();
  if (save) { lookTouched = Date.now(); post('/settings', { scale: v }).catch(() => {}); }
}
$('smaller').onclick = () => setScale(Math.max(0.6, +(userScale - 0.1).toFixed(1)), true);
$('bigger').onclick = () => setScale(Math.min(2, +(userScale + 0.1).toFixed(1)), true);

// ---------- dark or light: the system's choice first, then the button's ----------
let theme = store('pianoTheme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  $('theme').textContent = theme === 'dark' ? '☀' : '☾';
}
applyTheme();
function setTheme(v, save) { theme = v; store('pianoTheme', theme); applyTheme(); if (save) { lookTouched = Date.now(); post('/settings', { theme: v }).catch(() => {}); } }
$('theme').onclick = () => setTheme(theme === 'dark' ? 'light' : 'dark', true);

// ---------- the colour set: five ready-made sets, kept on this device like the theme ----------
const PALETTES = ['vesta', 'ember', 'forest', 'slate', 'plum'];
let palette = PALETTES.indexOf(store('pianoPalette')) !== -1 ? store('pianoPalette') : 'vesta';
function applyPalette() {
  document.documentElement.dataset.palette = palette;
  const box = $('setPalette');
  if (box) box.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === palette));
}
applyPalette();
$('setPalette').onclick = e => { const b = e.target.closest('button');
  if (!b || b.dataset.v === palette) return;
  palette = b.dataset.v; store('pianoPalette', palette); applyPalette(); lookTouched = Date.now(); post('/settings', { palette: palette }).catch(() => {}); };

// ---------- the keyboard ----------
const kb = $('kb'), kbwrap = $('kbwrap'), keyEl = {}, whites = [];
for (let m = FIRST; m <= LAST; m++) if (!isBlack(m)) whites.push(m);
const WHITES = whites.length, RATIO = 5.4, MAX_KEY = 56, SCROLL_KEY = 42;
let scrollMode = false, keyPx = 40, labelMode = store('pianoLabels') || 'num';
whites.forEach((m, k) => {
  const w = document.createElement('div');
  w.className = 'w'; w.dataset.m = m; w.dataset.n = k + 1;
  w.style.left = (k * 100 / WHITES) + '%'; w.style.width = (100 / WHITES) + '%';
  w.style.setProperty('--col', colOf(m));
  const d = document.createElement('span');
  d.className = 'dot' + (darkText(m) ? ' dark' : ''); d.style.setProperty('--col', colOf(m));
  w.appendChild(d); kb.appendChild(w); keyEl[m] = w;
  if (isBlack(m + 1) && m + 1 <= LAST) {
    const b = document.createElement('div');
    b.className = 'b'; b.dataset.m = m + 1; b.style.setProperty('--col', colOf(m + 1));
    b.style.left = 'calc(' + ((k + 1) * 100 / WHITES) + '% - ' + (100 / WHITES * 0.3) + '%)';
    b.style.width = (100 / WHITES * 0.6) + '%';
    kb.appendChild(b); keyEl[m + 1] = b;
  }
});
const PC_ROWS = {
  KeyZ: 0, KeyS: 1, KeyX: 2, KeyD: 3, KeyC: 4, KeyV: 5, KeyG: 6, KeyB: 7, KeyH: 8, KeyN: 9, KeyJ: 10, KeyM: 11,
  Comma: 12, KeyL: 13, Period: 14, Semicolon: 15, Slash: 16,
  KeyQ: 12, Digit2: 13, KeyW: 14, Digit3: 15, KeyE: 16, KeyR: 17, Digit5: 18, KeyT: 19, Digit6: 20, KeyY: 21,
  Digit7: 22, KeyU: 23, KeyI: 24, Digit9: 25, KeyO: 26, Digit0: 27, KeyP: 28, BracketLeft: 29, BracketRight: 31,
};
let pcKeys = store('pianoPcKeys') !== 'off';
const pcBaseMin = Math.ceil(FIRST / 12) * 12, pcBaseMax = Math.max(pcBaseMin, Math.floor((LAST - 23) / 12) * 12);
let pcBase = Math.min(pcBaseMax, Math.max(pcBaseMin, 48));     // Q is middle C when the keyboard has it
// which computer key plays a note now; where two keys play the same note, the upper row's is shown
const PC_SIGN = { Comma: ',', Period: '.', Semicolon: ';', Slash: '/', BracketLeft: '[', BracketRight: ']' };
function pcLetter(m) {
  for (const [code, off] of Object.entries(PC_ROWS).reverse())
    if (pcBase + off === m) return PC_SIGN[code] || code.replace(/^(Key|Digit)/, '');
  return '';
}
// the label of a key in the chosen system: the key number (10, 10#), the letter, Do Re Mi, or the computer key
function keyLabel(m) {
  if (labelMode === 'pc') return pcLetter(m);
  // scientific pitch notation, as teachers and keyboard makers write it: middle C (MIDI 60) is C4
  if (labelMode === 'abc') return NAME[pc(m)] + (Math.floor(m / 12) - 1);
  if (labelMode === 'do') return SOLFA[LETTER[pc(m)]] + (isBlack(m) ? '#' : '');
  if (labelMode === 'none') return '';
  const el = keyEl[isBlack(m) ? m - 1 : m];
  return el ? el.dataset.n + (isBlack(m) ? '#' : '') : '';
}
function relabel() {
  kb.classList.toggle('labels-none', labelMode === 'none');
  kb.querySelectorAll('.w').forEach(w => {
    const n = +w.dataset.n, dot = w.firstChild;
    const room = keyPx >= (['num', 'pc'].includes(labelMode) ? 26 : 34) || n % 2 === 1;
    dot.classList.toggle('small', !room && labelMode !== 'none');
    dot.textContent = room ? keyLabel(+w.dataset.m) : '';
  });
  kb.querySelectorAll('.b').forEach(b => { b.textContent = labelMode === 'pc' ? pcLetter(+b.dataset.m) : ''; });
  document.querySelectorAll('#labels button').forEach(b => b.classList.toggle('on', b.dataset.l === labelMode));
}
function markColors() { document.querySelectorAll('#colors button').forEach(b => b.classList.toggle('on', b.dataset.c === colorMode)); }
markColors();
// the colours and the labels belong to the profile, so they follow it to every screen
function setColors(c, save) {
  colorMode = c; store('pianoColors', c); document.documentElement.dataset.colors = c; markColors();
  kb.querySelectorAll('.w').forEach(w => w.firstChild.classList.toggle('dark', darkText(+w.dataset.m)));
  paint(state);
  if (save) { lookTouched = Date.now(); post('/settings', { colors: c }).catch(() => {}); }
}
function setLabels(l, save) {
  labelMode = l; store('pianoLabels', l); relabel(); paint(state);
  if (save) { lookTouched = Date.now(); post('/settings', { labels: l }).catch(() => {}); }
}
$('colors').onclick = e => { const btn = e.target.closest('button'); const c = btn && btn.dataset.c; if (c) setColors(c, true); };
$('labels').onclick = e => { const btn = e.target.closest('button'); const l = btn && btn.dataset.l; if (l) setLabels(l, true); };
let lastFollowed = null;
function layout() {
  document.documentElement.dataset.orient = innerWidth > innerHeight ? 'landscape' : 'portrait';
  kb.style.width = ''; kbwrap.parentNode.style.width = '';
  const inner = kbwrap.clientWidth;
  scrollMode = DEVICE === 'phone' || inner / WHITES < 20;
  kbwrap.classList.toggle('scroll', scrollMode);
  const flat = DEVICE === 'phone' && innerWidth > innerHeight;
  keyPx = scrollMode ? (flat ? 34 : SCROLL_KEY) * Math.min(SCALE, 1.3) : Math.min(MAX_KEY * SCALE, inner / WHITES);
  kb.style.width = (WHITES * keyPx) + 'px';
  kb.style.height = Math.round(keyPx * (flat ? 4 : RATIO)) + 'px';
  kb.style.setProperty('--k', keyPx + 'px');
  kbwrap.parentNode.style.width = scrollMode ? '' : 'fit-content';   // the frame hugs the keys
  lastFollowed = null;
  relabel();
}
function follow(el) {           // keep the lit key in the middle of a scrolling keyboard
  if (!scrollMode || !el || el === lastFollowed) return;
  lastFollowed = el;
  kbwrap.scrollTo({ left: Math.max(0, el.offsetLeft + el.offsetWidth / 2 - kbwrap.clientWidth / 2), behavior: 'smooth' });
}
layout();
addEventListener('resize', () => { applyScale(); layout(); });

// ---------- live mode: keys from the keyboard light up, keys on the screen play ----------
let live = store('pianoLive') !== 'off';
let state = { mode: 'idle', live: [], notice: [0, ''] }, lastLiveId = null, lastNotice = null, lastSummary = null;
$('live').onclick = () => { live = !live; store('pianoLive', live ? 'on' : 'off'); paint(state); };
// ---------- sound on this device: 5 voices, one of them a recorded grand piano ----------
// The grand piano is the Salamander Grand Piano (Alexander Holm, CC BY 3.0): 30 recorded notes,
// every third semitone, each one stretched to its neighbours. The other four are built here.
// A key held on the screen or on the computer sounds until it is let go; the pedal keeps it
// ringing after that, as on a real piano. A note with no release (a demo, the real keyboard's
// keys) is let go after a moment, or when the pedal comes up.
const VOICES = ['grand', 'epiano', 'organ', 'musicbox', 'synth'];
const VOICE_NAME = { grand: 'Grand piano', epiano: 'Electric piano', organ: 'Organ', musicbox: 'Music box', synth: 'Soft synth' };
let soundOn = store('pianoSound') === 'on', audio = null, master = null;
let voice = VOICES.includes(store('pianoVoice')) ? store('pianoVoice') : 'grand';
const SAMPLE_NAME = { 0: 'C', 3: 'Ds', 6: 'Fs', 9: 'A' };
const SAMPLES = []; for (let m = 21; m <= 108; m += 3) SAMPLES.push(m);   // A0, C1, D#1 … C8
const buffers = {}; let samplesLoading = null;
function wake() {
  if (!audio) {
    audio = new (window.AudioContext || window.webkitAudioContext)();
    const squeeze = audio.createDynamicsCompressor();   // many notes at once never clip
    squeeze.threshold.value = -12; squeeze.ratio.value = 4;
    master = audio.createGain(); master.connect(squeeze); squeeze.connect(audio.destination);
  }
  if (audio.state === 'suspended') audio.resume();
  if (voice === 'grand') loadSamples();
}
function loadSamples() {
  if (samplesLoading || !audio) return;
  samplesLoading = Promise.all(SAMPLES.map(async m => {
    const r = await fetch('/sounds/piano/' + SAMPLE_NAME[pc(m)] + (Math.floor(m / 12) - 1) + '.mp3');
    if (!r.ok) throw new Error(r.status);
    buffers[m] = await audio.decodeAudioData(await r.arrayBuffer());
  })).catch(() => { samplesLoading = null; });
}
const hz = m => 440 * Math.pow(2, (m - 69) / 12);
// each voice returns the gain to fade out and how long its release takes
function build(m, vel, now) {
  const out = audio.createGain(); out.connect(master);
  const v = vel * (+$('vol').value / 100), f = hz(m);
  const osc = (type, freq, g, dest) => { const o = audio.createOscillator(), og = audio.createGain();
    o.type = type; o.frequency.value = freq; og.gain.value = g; o.connect(og); og.connect(dest || out); o.start(now); return o; };
  let nodes = [], release = 0.25;
  const near = SAMPLES.reduce((a, b) => Math.abs(b - m) < Math.abs(a - m) ? b : a);
  if (voice === 'grand' && buffers[near]) {
    const s = audio.createBufferSource(); s.buffer = buffers[near];
    s.playbackRate.value = Math.pow(2, (m - near) / 12);
    out.gain.value = v * 1.1; s.connect(out); s.start(now); nodes = [s]; release = 0.3;
  } else if (voice === 'epiano') {                       // two-operator FM, the tine sound of a Rhodes
    const mod = audio.createOscillator(), idx = audio.createGain(), car = audio.createOscillator();
    mod.frequency.value = f; car.frequency.value = f;
    idx.gain.setValueAtTime(f * 2.2, now); idx.gain.exponentialRampToValueAtTime(f * 0.25, now + 1.2);
    mod.connect(idx); idx.connect(car.frequency); car.connect(out); mod.start(now); car.start(now);
    nodes = [mod, car, osc('sine', f * 14, 0.04 * v)];
    out.gain.setValueAtTime(0.0001, now); out.gain.exponentialRampToValueAtTime(v * 0.45, now + 0.005);
    out.gain.exponentialRampToValueAtTime(v * 0.18, now + 1.5); out.gain.exponentialRampToValueAtTime(0.0001, now + 6);
    release = 0.35;
  } else if (voice === 'organ') {                        // drawbars 16', 8', 4', 2 2/3', 2'
    nodes = [[0.5, 0.45], [1, 1], [2, 0.6], [3, 0.3], [4, 0.22]].map(([mul, g]) => osc('sine', f * mul, g));
    out.gain.setValueAtTime(0.0001, now); out.gain.exponentialRampToValueAtTime(v * 0.16, now + 0.012);
    release = 0.08;
  } else if (voice === 'musicbox') {                     // a plucked steel tooth, two octaves up in character
    nodes = [osc('sine', f * 2, 1), osc('sine', f * 5.4, 0.18), osc('triangle', f * 2, 0.12)];
    out.gain.setValueAtTime(0.0001, now); out.gain.exponentialRampToValueAtTime(v * 0.3, now + 0.003);
    out.gain.exponentialRampToValueAtTime(0.0001, now + 1.6);
    release = 0.6;
  } else {                                               // the first voice of this screen: soft and round
    const tip = audio.createBiquadFilter(); tip.type = 'lowpass'; tip.frequency.value = Math.min(9000, f * 6); tip.connect(out);
    nodes = [[1, 'triangle', 1], [2, 'sine', 0.35], [3, 'sine', 0.12]].map(([mul, type, g]) => osc(type, f * mul, g, tip));
    out.gain.setValueAtTime(0.0001, now); out.gain.exponentialRampToValueAtTime(v * 0.28, now + 0.006);
    out.gain.exponentialRampToValueAtTime(v * 0.1, now + 0.25); out.gain.exponentialRampToValueAtTime(0.0001, now + 1.1 + (96 - Math.min(m, 96)) / 60);
  }
  return { out, nodes, release };
}
const sounding = new Map();       // note -> { out, nodes, release, held, timer }
let pedalDown = false;
function letGo(m) {
  const n = sounding.get(m); if (!n) return;
  sounding.delete(m); clearTimeout(n.timer);
  const now = audio.currentTime;
  n.out.gain.cancelScheduledValues(now);
  n.out.gain.setValueAtTime(Math.max(0.0001, n.out.gain.value), now);
  n.out.gain.exponentialRampToValueAtTime(0.0001, now + n.release);
  n.nodes.forEach(o => { try { o.stop(now + n.release + 0.05); } catch (e) { /* already stopped */ } });
}
// held: a release will come (screen, computer keys, a replay); otherwise it is let go after a moment
function noteOn(m, vel, held) {
  if (!soundOn || !audio) return;
  if (sounding.has(m)) letGo(m);                  // struck again: the old one stops, as the damper would
  const n = build(m, vel || 0.8, audio.currentTime);
  n.held = !!held;
  n.timer = setTimeout(() => noteOff(m), held ? 8000 : 700);   // held ones have a safety limit too
  sounding.set(m, n);
}
function noteOff(m) {
  const n = sounding.get(m); if (!n) return;
  if (pedalDown) { n.sustained = true; clearTimeout(n.timer); return; }
  letGo(m);
}
function setPedal(down) {
  if (down === pedalDown) return;
  pedalDown = down;
  if (!down) [...sounding.entries()].forEach(([m, n]) => { if (n.sustained || !n.held) letGo(m); });
}
// the screen's pedal, the keyboard's own pedal, or a replay's: any of them holds the notes
function paintPedal() { setPedal(!!(pedal || state.pedal || (replay && replay.pedal >= 64))); }
function tone(m, velocity, held) { noteOn(m, velocity, held); }
function paintSound() {
  $('soundBtn').classList.toggle('on', soundOn);
  $('soundBtn').title = t("Play the notes on this computer's speakers too") + ' · ' + t(VOICE_NAME[voice]);
}
function setVoice(v, quiet) {
  voice = v; store('pianoVoice', v);
  if (quiet) { paintSound(); return; }
  lookTouched = Date.now(); post('/settings', { voice: v }).catch(() => {});
  [...sounding.keys()].forEach(letGo);
  if (!soundOn) { soundOn = true; store('pianoSound', 'on'); }
  wake(); paintSound();
  const demo = () => [60, 64, 67].forEach((m, i) => setTimeout(() => noteOn(m, 0.7), i * 130));
  if (v === 'grand' && samplesLoading) samplesLoading.then(demo); else demo();
}
paintSound();
$('soundBtn').onclick = () => {
  soundOn = !soundOn; store('pianoSound', soundOn ? 'on' : 'off');
  if (soundOn) wake(); else [...sounding.keys()].forEach(letGo);
  paintSound();
};
// a browser starts sound only after a click or a key on the page: the first one wakes it
['pointerdown', 'keydown'].forEach(ev => addEventListener(ev, () => { if (soundOn) wake(); }, { capture: true }));
function flash(m, cls, held) {
  tone(m, undefined, held);
  const el = keyEl[m]; if (!el) return;
  el.classList.remove('flash', 'wrong', 'them'); void el.offsetWidth;
  el.classList.add('lit', 'flash'); if (cls) el.classList.add(cls);
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('lit', 'flash', 'wrong', 'them'), 380);
  follow(el);
}
function heard(m, held) {
  flash(m, null, held);
  $('heard').innerHTML = '';
  const s = document.createElement('span'); s.className = 'dot' + (darkText(m) ? ' dark' : '');
  s.style.cssText = '--col:' + colOf(m) + ';width:1.9rem;height:1.9rem;font-size:.85rem';
  s.textContent = keyLabel(m) || '♪';
  $('heard').append(t('You played '), s); heardPrompt = false;
}
const down = new Map();
let heardPrompt = true;
let pendingAction = null, lastSearch = '';
function rawPost(path, body) {
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
}
// every read goes through here too: the pass travels in the cookie, and a screen that has
// none is asked for the code instead of quietly drawing an empty page
async function getJSON(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { openCode(); return null; }
  if (!r.ok) return null;
  return r.json();
}
// every action goes through here: asks for the code once, and says why the keyboard refused
async function post(path, body) {
  const r = await rawPost(path, body);
  if (r.status === 401) { pendingAction = () => post(path, body); openCode(); }
  else if (r.status === 429) toast(t('Too many wrong codes. Wait a minute and try again'), 'warn');
  else if (r.status === 409 && !/stop/.test(path)) toast(t('The keyboard is busy: a lesson, a song or the alarm is playing'), 'warn');
  return r;
}
async function refreshCode() {
  const o = await getJSON('/api/code', { cache: 'no-store' });
  if (!o) return;
  $('setCode').classList.toggle('on', !!o.on);
  $('setCodeVal').textContent = '······';
}
$('setCode').onclick = async () => {
  const on = $('setCode').classList.contains('on');
  if (on && !confirm(t('Turn the code off? Every device on the network will be able to use the piano'))) return;
  const r = await post(on ? '/code/off' : '/code/on');
  if (r.ok) { const j = await r.json(); $('setCode').classList.toggle('on', !!j.on); if (j.code) { $('setCodeVal').textContent = j.code; } }
};
$('setCodeShow').onclick = async () => {
  const o = await getJSON('/api/code?show=1', { cache: 'no-store' });
  if (o && o.code) $('setCodeVal').textContent = o.code;
};
$('setCodeNew').onclick = async () => {
  if (!confirm(t('Draw a new code? The old one stops working'))) return;
  const r = await post('/code/new');
  if (r.ok) { const j = await r.json(); $('setCodeVal').textContent = j.code; toast(t('A new code was drawn'), 'good'); }
};
$('setSignOut').onclick = async () => {
  if (!confirm(t('Sign every screen out? Every other device will be asked for the code again'))) return;
  const r = await post('/code/signout');
  if (r.ok) toast(t('Every other screen was signed out'), 'good');
};
function openCode() {
  const box = $('codeBox');
  if (box.classList.contains('show')) return;    // the loop asks ten times a second: ask once
  box.classList.add('show'); $('codeIn').value = '';
  setTimeout(() => $('codeIn').focus(), 50);
}
async function tryCode() {
  const code = $('codeIn').value.replace(/\\D/g, '');
  const r = await rawPost('/code', { code: code });
  if (r.ok) {
    $('codeBox').classList.remove('show'); toast(t('This screen is unlocked'), 'good');
    const a = pendingAction; pendingAction = null; if (a) a();
    loadOverview(); tick(); showUpdate(false);
  } else toast(t(r.status === 429 ? 'Too many wrong codes. Wait a minute' : 'Wrong code'), 'warn');
}
$('codeGo').onclick = tryCode;
$('codeIn').addEventListener('keydown', e => { if (e.key === 'Enter') tryCode(); });
$('codeCancel').onclick = () => { $('codeBox').classList.remove('show'); pendingAction = null; };

// A file is fetched by the page and saved from memory. A plain link to an http:// address makes
// Chrome call the download insecure and block it, a file saved from the page itself is not.
async function saveAs(url, fallback) {
  try {
    const r = await fetch(url, { cache: 'no-store' });
    if (r.status === 401) { pendingAction = () => saveAs(url, fallback); openCode(); return; }
    if (!r.ok) throw new Error(r.status);
    const cd = r.headers.get('Content-Disposition') || '';
    const u8 = /filename\\*=UTF-8''([^;]+)/i.exec(cd), m = /filename="([^"]+)"/.exec(cd);
    let name = m ? m[1] : fallback;
    try { if (u8) name = decodeURIComponent(u8[1]); } catch (e) { /* the plain name stays */ }
    const u = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 30000);
  } catch (e) { toast(t('The download failed'), 'warn'); }
}
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('a[data-save]');
  if (!a || !a.getAttribute('href')) return;
  e.preventDefault(); saveAs(a.getAttribute('href'), a.dataset.save);
});

// restoring: the backup is read and checked first; it goes back into the profile it was made from
let restoreInfo = null;
function openRestore() {
  restoreInfo = null; $('restFile').value = ''; $('restPick').style.display = 'none'; $('restGo').disabled = true;
  $('restPin').value = '';
  $('restoreBox').classList.add('show');
}
function restoreTarget() { return restoreInfo && restoreInfo.profiles.find(p => p.id === $('restSrc').value); }
function paintRestore() {
  const p = restoreTarget();
  if (!p) return;
  const name = p.name || t('Main');
  $('restInto').textContent = t('It will be restored into {p}, which becomes the profile at the piano.', { p: name })
    + (p.exists ? '' : ' ' + t('That profile was deleted: it will be created again.'));
  $('restPin').style.display = p.locked ? '' : 'none';
  $('restPin').placeholder = t('PIN of {p}', { p: name });
}
$('setRestore').onclick = openRestore;
$('restCancel').onclick = () => $('restoreBox').classList.remove('show');
$('restSrc').onchange = paintRestore;
$('restFile').onchange = async () => {
  const f = $('restFile').files[0];
  if (!f) return;
  const r = await fetch('/backup/inspect', { method: 'POST', headers: { 'X-Filename': 'backup.zip' }, body: f });
  if (r.status === 401) { openCode(); return; }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.profiles) { restoreInfo = null; $('restPick').style.display = 'none'; $('restGo').disabled = true; toast(t(j.error || 'This is not a backup file'), 'warn'); return; }
  restoreInfo = j;
  $('restSrc').innerHTML = '';
  j.profiles.forEach(p => { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name || t('Main'); $('restSrc').appendChild(o); });
  $('restSrcBox').style.display = j.profiles.length > 1 ? '' : 'none';
  $('restWhat').textContent = j.profiles.length === 1
    ? t('A backup of {p}{d}', { p: j.profiles[0].name || t('Main'), d: j.date ? ', ' + j.date : '' }) : '';
  $('restSongs').textContent = j.songs ? tn(j.songs, '{n} song in the backup is added if it is not here yet', '{n} songs in the backup are added if they are not here yet') : '';
  paintRestore();
  $('restPick').style.display = ''; $('restGo').disabled = !j.profiles.length;
};
$('restGo').onclick = async () => {
  const p = restoreTarget();
  if (!p) return;
  const name = p.name || t('Main');
  if (!confirm(t('The progress, history and settings of {p} are replaced, and {p} becomes the profile at the piano. Continue?', { p: name }))) return;
  const r = await post('/backup/restore', { source: p.id, pin: $('restPin').value, songs: true });
  const j = await r.json().catch(() => ({}));
  if (r.ok && j.ok) {
    $('restoreBox').classList.remove('show'); toast(t('Restored into {p}', { p: j.name || t('Main') }), 'good');
    if (j.profiles) paintProfiles(j.profiles);
    lookTouched = 0; loadOverview(); tick();
  } else if (r.status === 403) toast(t('Wrong PIN'), 'warn');
  else if (r.status !== 409) toast(t(j.error || 'The restore failed'), 'warn');
};

// the screen's sustain pedal, and the keyboard's own pedal shown on the same button
// The sound on this device follows it at once; the keyboard follows when the piano accepts it.
let pedal = false, pedalBySpace = false;
function setScreenPedal(down, bySpace) {
  pedal = down; pedalBySpace = !!(down && bySpace);
  $('pedalBtn').classList.toggle('on', pedal); paintPedal();
  post('/pedal', { down }).catch(() => {});
}
$('pedalBtn').onclick = () => setScreenPedal(!pedal);
// master volume: sent a moment after the slider stops, remembered by the engine
let volTimer = null, volTouched = 0;
$('vol').addEventListener('input', () => {
  $('volTxt').textContent = $('vol').value + '%'; volTouched = Date.now();
  clearTimeout(volTimer); volTimer = setTimeout(() => post('/volume', { value: +$('vol').value }), 150);
});
kb.addEventListener('pointerdown', e => {
  if (!(live && state.mode === 'idle' && !replay)) return;
  const el = e.target.closest('.w, .b'); if (!el) return;
  const m = +el.dataset.m; down.set(e.pointerId, m);
  post('/key', { note: m, down: true }).catch(() => {});
  heard(m, true);
});
const release = e => {
  if (!down.has(e.pointerId)) return;
  const m = down.get(e.pointerId);
  post('/key', { note: m, down: false }).catch(() => {}); down.delete(e.pointerId); noteOff(m);
};
['pointerup', 'pointercancel', 'pointerleave'].forEach(t => kb.addEventListener(t, release));

// ---------- the computer's own keys play the screen's keys, the way Virtual Piano does ----------
// Two rows, each an octave with the black keys on the row above it. e.code is the key's place,
// not its letter, so it works the same with a Hebrew layout. ← and → move both rows an octave.
const pcDown = new Map();
function pcRelease(code) {
  if (!pcDown.has(code)) return;
  const m = pcDown.get(code);
  post('/key', { note: m, down: false }).catch(() => {});
  pcDown.delete(code); noteOff(m);
}
addEventListener('keydown', e => {
  if (!pcKeys || e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.querySelector('.modal.show') || e.target.closest && e.target.closest('input, textarea, select, [contenteditable]')) return;
  if (!(live && state.mode === 'idle' && !replay)) return;
  if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') {
    e.preventDefault();
    const b = Math.min(pcBaseMax, Math.max(pcBaseMin, pcBase + (e.code === 'ArrowLeft' ? -12 : 12)));
    if (b !== pcBase) { [...pcDown.keys()].forEach(pcRelease); pcBase = b; paintPcHint(); if (labelMode === 'pc') relabel(); }
    return;
  }
  if (e.code === 'Space') {                                 // the space bar is the sustain pedal while held
    e.preventDefault();
    if (!e.repeat && !pedal) setScreenPedal(true, true);
    return;
  }
  if (!(e.code in PC_ROWS)) return;
  e.preventDefault();
  if (e.repeat || pcDown.has(e.code)) return;
  const m = pcBase + PC_ROWS[e.code];
  if (!keyEl[m]) return;                                    // off the keyboard's range
  pcDown.set(e.code, m);
  post('/key', { note: m, down: true }).catch(() => {});
  heard(m, true);
});
addEventListener('keyup', e => { if (e.code === 'Space' && pedal && pedalBySpace) setScreenPedal(false); });
addEventListener('keyup', e => pcRelease(e.code));
addEventListener('blur', () => [...pcDown.keys()].forEach(pcRelease));   // a key held while the window lost focus
function paintPcHint() {
  $('pcHint').hidden = !pcKeys || DEVICE !== 'desktop';
  const q = pcBase + 12, el = keyEl[q];
  $('pcOct').textContent = 'C' + (Math.floor(q / 12) - 1) + (el ? ' · ' + el.dataset.n : '');
}
paintPcHint();

// ---------- settings: the screen's own choices in one place (the lesson's pace stays on the Today card) ----------
function paintSettings() {
  const on = (id, v) => { const el = $(id); if (!el) return; el.classList.toggle('on', v); el.querySelector('.onoff').textContent = t(v ? 'On' : 'Off'); };
  $('setLang').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === LANG));
  $('setTheme').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === theme));
  applyPalette();
  $('setSizeTxt').textContent = Math.round(userScale * 100) + '%';
  on('setSound', soundOn); on('setPcKeys', pcKeys); on('setLive', live); on('setEditMode', editOn);
  $('setVoice').value = voice;
  [...$('setVoice').options].forEach(o => { o.textContent = t(VOICE_NAME[o.value]); });
  $('setProf').textContent = profName(profiles.list.find(p => p.id === profiles.active) || {}) + ' ›';
  $('setKbd').textContent = t('{n} keys, from {a} to {b}', { n: LAST - FIRST + 1, a: NAME[pc(FIRST)] + (Math.floor(FIRST / 12) - 1), b: NAME[pc(LAST)] + (Math.floor(LAST / 12) - 1) });
}
function openSettings() {
  paintSettings(); refreshCode(); $('settings').classList.add('show');
  // open at the top: focusing the Close button at the bottom used to scroll the sheet down to it
  const sheet = $('settings').querySelector('.sheet'); sheet.scrollTop = 0;
  setTimeout(() => { $('setClose').focus({ preventScroll: true }); sheet.scrollTop = 0; }, 50);
}
function closeSettings() { $('settings').classList.remove('show'); $('setBtn').focus({ preventScroll: true }); }
$('setBtn').onclick = openSettings;
$('setProf').onclick = () => { $('settings').classList.remove('show'); openProfiles(); };
$('setClose').onclick = closeSettings;
$('settings').addEventListener('click', e => { if (e.target === $('settings')) closeSettings(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && $('settings').classList.contains('show')) closeSettings(); });
$('setLang').onclick = async e => { const v = e.target.dataset.v; if (v) { await setLang(v); paintSettings(); } };
$('setTheme').onclick = e => { const v = e.target.dataset.v; if (v && v !== theme) { $('theme').click(); paintSettings(); } };
$('setSize').onclick = e => { const v = e.target.dataset.v; if (v) { $(v === '+' ? 'bigger' : 'smaller').click(); paintSettings(); } };
$('setSound').onclick = () => { $('soundBtn').click(); paintSettings(); };
$('setVoice').onchange = () => { setVoice($('setVoice').value); paintSettings(); };
$('setLive').onclick = () => { $('live').click(); paintSettings(); };

// ---------- edit mode: whole blocks edited as free HTML, in the language on screen ----------
// A block is any element with a data-tkey. Its edit for the current language replaces the whole
// block, as free HTML, kept in the data folder (OV[LANG][key]); the code file is never touched.
// The keys are separate per element, so the nav item and the page heading never share one.
let editOn = false, edScope = '', edTarget = null;
const EDIT_ALLOWED = __EDITMODE__;
const edBar = $('edBar'), edModal = $('edModal'), edToggle = $('edToggleBtn');
if (!EDIT_ALLOWED) {                      // the tools are not on the screen unless they were asked for
  edToggle.remove();
  const row = $('setEditMode') && $('setEditMode').closest('.setrow');
  if (row) row.remove();
}

function edMatches(el) { return !!edScope && !!el.dataset.tkey && el.dataset.tkey.slice(0, edScope.length) === edScope; }
function edPaintPicks() {
  document.querySelectorAll('[data-tkey]').forEach(el => el.classList.toggle('edPick', editOn && edMatches(el)));
}
function setEditScope(scope) {
  edScope = scope || '';
  editOn = !!edScope;
  document.body.classList.toggle('edMode', editOn);
  edBar.querySelectorAll('button[data-scope]').forEach(b => b.classList.toggle('on', editOn && b.dataset.scope === edScope && b.dataset.scope !== ''));
  if (!editOn) closeEditor();
  edPaintPicks();
  paintSettings();
}
edToggle.onclick = () => setEditScope(editOn ? '' : 'content.');
edBar.querySelectorAll('button[data-scope]').forEach(b => { b.onclick = () => setEditScope(b.dataset.scope); });

// click a highlighted block to open its editor; stop links inside it from navigating while editing
document.addEventListener('click', e => {
  if (!editOn || edModal.contains(e.target) || edBar.contains(e.target) || edToggle.contains(e.target)) return;
  const el = e.target.closest('[data-tkey]');
  if (el && edMatches(el)) { e.preventDefault(); openEditor(el); }
});

function curHtml(el) {
  const ov = OV[LANG] ? OV[LANG][el.dataset.tkey] : undefined;
  return ov !== undefined ? ov : el.innerHTML;         // the edit for this language, or what is shown now
}
function openEditor(el) {
  edTarget = el;
  $('edKey').textContent = el.dataset.tkey;
  $('edLang').textContent = (LANG === 'he' ? 'עברית' : 'English');
  const html = curHtml(el);
  $('edSrc').value = html;
  $('edPrev').innerHTML = html;
  syncImgTools();
  edModal.classList.add('show');
  $('edSrc').focus();
}
function closeEditor() { edModal.classList.remove('show'); edTarget = null; }

// image controls appear only when the block holds a picture
function syncImgTools() {
  const has = $('edSrc').value.toLowerCase().includes('<img');
  $('edImgTools').classList.toggle('show', has);
  $('edImg').textContent = t(has ? 'Replace image' : 'Add image');
}
function withFirstImg(fn) {
  const ta = $('edSrc'), box = document.createElement('div');
  box.innerHTML = ta.value;
  const img = box.querySelector('img');
  if (!img) return false;
  fn(img);
  ta.value = box.innerHTML; $('edPrev').innerHTML = ta.value; syncImgTools();
  return true;
}
function imgAlign(img, al) {                    // absolute left / centre / right, not affected by text direction
  img.style.display = 'block'; img.style.height = 'auto';
  if (al === 'center') { img.style.marginLeft = 'auto'; img.style.marginRight = 'auto'; }
  else if (al === 'left') { img.style.marginLeft = '0'; img.style.marginRight = 'auto'; }
  else { img.style.marginLeft = 'auto'; img.style.marginRight = '0'; }
}
$('edImgTools').querySelectorAll('button[data-al]').forEach(b => { b.onclick = () => withFirstImg(img => imgAlign(img, b.dataset.al)); });
$('edImgTools').querySelectorAll('button[data-w]').forEach(b => { b.onclick = () => withFirstImg(img => { img.style.width = b.dataset.w + '%'; img.style.height = 'auto'; }); });

$('edSrc').addEventListener('input', () => { $('edPrev').innerHTML = $('edSrc').value; syncImgTools(); });

async function edSaveValue(value) {
  if (!edTarget) return;
  const key = edTarget.dataset.tkey;
  if (value.trim() === (edTarget._def || '').trim()) value = '';   // same as the built-in text: back to default
  const r = await post('/text-edit', { lang: LANG, key, value });
  if (!r.ok) { let why = ''; try { why = (await r.json()).why; } catch (e) {} toast(why || t('That did not work'), 'warn'); return; }
  if (!value) { if (OV[LANG]) delete OV[LANG][key]; } else { (OV[LANG] = OV[LANG] || {})[key] = value; }
  translatePage();                       // re-render with the change, in the language shown
  if (key.slice(0, 5) === 'foot.') {     // the footer holds the live year and version: refresh them
    const yr = $('year'); if (yr) yr.textContent = new Date().getFullYear();
    if (window.showUpdate) showUpdate(false);
  }
  edPaintPicks();
  toast(t('Saved'), 'good');
  closeEditor();
}
$('edSave').onclick = () => edSaveValue($('edSrc').value);
$('edReset').onclick = () => edSaveValue('');
$('edCancel').onclick = closeEditor;
edModal.addEventListener('click', e => { if (e.target === edModal) closeEditor(); });

// add a picture, or replace the one already in the block; the file goes to the piano's data folder
$('edImg').onclick = () => $('edFile').click();
$('edFile').onchange = async () => {
  const f = $('edFile').files[0]; $('edFile').value = '';
  if (!f) return;
  toast(t('Uploading…'));
  let url;
  try {
    const buf = await f.arrayBuffer();
    const r = await fetch('/media-upload', { method: 'POST',
      headers: { 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' },
      body: buf });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.why || '');
    url = j.url;
  } catch (e) { toast((e && e.message) ? e.message : t('The upload failed'), 'warn'); return; }
  const replaced = withFirstImg(img => { img.setAttribute('src', url); });   // keep its size and position
  if (!replaced) {
    const ta = $('edSrc');
    const tag = '<img src="' + url + '" alt="" style="display:block;height:auto;width:60%;margin-left:auto;margin-right:auto">';
    const a = ta.selectionStart, b = ta.selectionEnd;
    ta.value = ta.value.slice(0, a) + tag + ta.value.slice(b);
    ta.selectionStart = ta.selectionEnd = a + tag.length;
    $('edPrev').innerHTML = ta.value; ta.focus();
  }
  syncImgTools();
};

document.addEventListener('keydown', e => {
  if (!edModal.classList.contains('show')) return;
  if (e.key === 'Escape') { e.preventDefault(); closeEditor(); }
  else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); edSaveValue($('edSrc').value); }
});

// the Settings toggle still turns edit mode on, now straight into content editing
if ($('setEditMode')) $('setEditMode').onclick = () => { setEditScope(editOn ? '' : 'content.'); };

// ---------- profiles: who is at the piano now. The piano keeps it, so every screen agrees ----------
let profiles = { active: 'main', list: [] }, pinFor = null;
const profName = p => p.name || t('Main');
function avatar(el, p) {
  const n = profName(p);
  let h = 0; for (const c of n) h = (h * 31 + c.codePointAt(0)) % 360;
  el.textContent = [...n.trim()][0] ? [...n.trim()][0].toUpperCase() : '♪';
  el.style.setProperty('--avc', 'hsl(' + h + ' 55% 42%)');
}
function paintProfiles(pr) {
  if (pr) profiles = pr;
  const me = profiles.list.find(p => p.id === profiles.active) || { id: 'main', name: '' };
  avatar($('whoAv'), me); $('whoName').textContent = profName(me);
  const box = $('profList'); box.replaceChildren();
  for (const p of profiles.list) {
    const b = document.createElement('div'); b.className = 'prof' + (p.id === profiles.active ? ' on' : '');
    b.tabIndex = 0; b.setAttribute('role', 'button');
    const av = document.createElement('span'); av.className = 'av'; avatar(av, p);
    const nm = document.createElement('b'); nm.textContent = profName(p) + (p.locked ? ' 🔒' : '');
    const sm = document.createElement('small'); sm.textContent = p.id === profiles.active ? t('Playing now') : '';
    b.append(av, nm, sm);
    if (p.id !== 'main' && p.id !== profiles.active) {
      const d = document.createElement('button'); d.className = 'del'; d.textContent = '✕'; d.title = t('Delete this profile');
      d.onclick = e => { e.stopPropagation(); askDelete(p); };
      b.append(d);
    }
    b.onclick = () => { if (p.id !== profiles.active) p.locked ? askPin(p, 'switch') : profAct('switch', { id: p.id }); };
    b.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); b.click(); } };
    box.append(b);
  }
  $('editName').value = me.name || '';
  $('editPin').hidden = !me.locked;
  $('editNoPin').parentNode.hidden = !me.locked;
}
function askPin(p, kind) {
  pinFor = { p, kind };
  $('profPinWhy').textContent = t(kind === 'delete' ? 'To delete, the PIN of' : 'PIN for');
  $('profPinName').textContent = profName(p);
  $('profPin').hidden = false; $('profPinIn').value = ''; setTimeout(() => $('profPinIn').focus(), 30);
}
function askDelete(p) {
  if (!confirm(t('Delete {name}? Its progress and recordings are deleted too.', { name: profName(p) }))) return;
  if (p.locked) askPin(p, 'delete'); else profAct('delete', { id: p.id });
}
// its own post: a 409 here can mean a taken name, not only a busy keyboard
async function profAct(kind, body) {
  let r;
  try { r = await rawPost('/profiles/' + kind, body); } catch (e) { return false; }
  if (r.status === 401) { pendingAction = () => profAct(kind, body); openCode(); return false; }
  if (r.status === 429) { toast(t('Too many wrong codes. Wait a minute and try again'), 'warn'); return false; }
  let j = {}; try { j = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const why = j.why === 'wrong_pin' ? 'Wrong PIN' : j.why === 'taken' ? 'That name is already taken'
      : j.why === 'full' ? 'There is room for 12 profiles' : r.status === 409 ? 'Not while a lesson or a replay is playing'
      : r.status === 400 ? 'A name is needed, and a PIN is 4 to 8 digits' : 'That did not work';
    toast(t(why), 'warn'); return false;
  }
  $('profPin').hidden = true; pinFor = null;
  paintProfiles(j.profiles);
  if (kind === 'switch') {
    lookTouched = 0;                 // the new profile's look is applied now, in this same window
    const me = profiles.list.find(p => p.id === profiles.active);
    closeProfiles(); toast(t('Hi, {name}', { name: profName(me) }), 'good');
  }
  loadOverview();
  return true;
}
function openProfiles() { $('profPin').hidden = true; paintProfiles(); $('profiles').classList.add('show'); }
function closeProfiles() { $('profiles').classList.remove('show'); }
$('whoBtn').onclick = openProfiles;
$('profClose').onclick = closeProfiles;
$('profiles').addEventListener('click', e => { if (e.target === $('profiles')) closeProfiles(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && $('profiles').classList.contains('show')) closeProfiles(); });
$('profPinGo').onclick = () => { if (pinFor) profAct(pinFor.kind, { id: pinFor.p.id, pin: $('profPinIn').value }); };
$('profPinIn').addEventListener('keydown', e => { if (e.key === 'Enter') $('profPinGo').click(); });
$('profPinCancel').onclick = () => { $('profPin').hidden = true; pinFor = null; };
$('addGo').onclick = async () => {
  if (await profAct('add', { name: $('addName').value, pin: $('addPin').value })) {
    $('addName').value = ''; $('addPin').value = ''; $('profAdd').open = false; toast(t('Profile added'), 'good');
  }
};
$('editGo').onclick = async () => {
  const body = { name: $('editName').value, pin: $('editPin').value };
  if ($('editNoPin').checked) body.new_pin = '';
  else if ($('editNewPin').value) body.new_pin = $('editNewPin').value;
  if (await profAct('edit', body)) {
    $('editPin').value = ''; $('editNewPin').value = ''; $('editNoPin').checked = false; $('profEdit').open = false; toast(t('Saved'), 'good');
  }
};

$('setPcKeys').onclick = () => {
  pcKeys = !pcKeys; store('pianoPcKeys', pcKeys ? 'on' : 'off');
  if (!pcKeys) [...pcDown.keys()].forEach(pcRelease);
  paintPcHint(); paintSettings();
};

// ---------- painting the lesson ----------
function setSticker(text, m, pulse) {
  const s = $('sticker'); s.textContent = text;
  s.className = 'sticker' + (m == null ? ' neutral' : '') + (m != null && darkText(m) ? ' dark' : '') + (pulse ? ' pulse' : '');
  s.style.setProperty('--col', m == null ? '#2b4468' : colOf(m));
}
function bead(text, m, cls, small) {
  const c = document.createElement('div');
  c.className = 'bead' + (m == null ? ' blank' : (darkText(m) ? ' dark' : '')) + (cls || '');
  if (m != null) c.style.setProperty('--col', colOf(m));
  c.textContent = text;
  if (small) { const sm = document.createElement('small'); sm.textContent = small; c.appendChild(sm); }
  return c;
}
function clearKeys() { Object.values(keyEl).forEach(e => { if (!e.classList.contains('flash')) e.classList.remove('lit', 'target'); }); }
function burst() {
  const box = $('now'), wrap = document.createElement('div'); wrap.className = 'burst';
  ['var(--gold)', 'var(--sky)', 'var(--gold)', 'var(--sky)', 'var(--gold)', 'var(--sky)'].forEach((n, i) => {
    const d = document.createElement('i'), a = i / 6 * Math.PI * 2 + 0.5, r = 70;
    d.style.background = n; d.style.setProperty('--x', Math.cos(a) * r + 'px'); d.style.setProperty('--y', Math.sin(a) * r + 'px');
    wrap.appendChild(d);
  });
  box.appendChild(wrap); setTimeout(() => wrap.remove(), 600);
}
function toast(msg, kind) {
  const t = $('toast'); t.textContent = msg; t.className = 'toast show' + (kind ? ' ' + kind : '');
  clearTimeout(t._t); t._t = setTimeout(() => t.className = 'toast', 2200);
}
let prevMode = 'idle', prevMsg = '', lastDemo = { idx: -1, title: '' };
function paintLesson(s) {
  $('stageTitle').textContent = nice(s.song) || t('Lesson');
  $('pieceTitle').textContent = s.title || '';
  const waiting = s.mode === 'wait', playing = s.mode === 'demo', ok = s.mode === 'ok';
  const idx = waiting ? s.expect : s.demo, keysOnly = s.show === 'keys', blind = s.show === false || keysOnly, n = (s.seq || []).length;
  kb.classList.toggle('blind', blind); kb.classList.toggle('keysonly', keysOnly);
  const seq = $('seq'); seq.innerHTML = '';
  clearKeys();
  if (blind) {
    // no numbers and no colours: they come off together, so the colour does not become the crutch
    for (let i = 0; i < n; i++) seq.appendChild(bead(waiting && i < idx ? '●' : '○', null, waiting && i === idx ? ' cur' : (waiting && i < idx ? ' done' : '')));
    $('what').textContent = t(waiting ? (keysOnly ? 'Your turn, the key is lit' : 'Your turn, from memory') : playing ? 'Listen' : ok ? 'Right!' : '');
    setSticker(playing ? '🎧' : ok ? '✓' : (waiting && idx >= 0 && n ? (idx + 1) + '/' + n : '…'), null);
    $('noteTag').textContent = ''; $('hand').textContent = '';
    const km = keysOnly && waiting && idx >= 0 ? s.midi[idx] : null;
    if (km != null && keyEl[km]) { keyEl[km].classList.add('lit', 'target'); follow(keyEl[km]); }
  } else {
    // every note of the run is drawn: a window with a "+7" at its edge hides where you are
    for (let i = 0; i < n; i++) seq.appendChild(bead(labelMode === 'num' ? s.seq[i] : (keyLabel(s.midi[i]) || '•'), s.midi[i],
      i === idx ? ' cur' : (waiting && i < idx ? ' done' : ''), s.fing && s.fing[i]));
    const m = (idx >= 0 && s.midi[idx] !== undefined) ? s.midi[idx] : null;
    $('what').textContent = t(waiting ? 'Your turn, press' : playing ? 'Listen, the keyboard plays' : ok ? 'Right!' : 'Get ready');
    if (ok) setSticker('✓', null); else if (m != null) setSticker(labelMode === 'num' ? s.seq[idx] : (keyLabel(m) || '•'), m, waiting); else setSticker('…', null);
    $('noteTag').textContent = m != null && labelMode === 'num' ? NAME[pc(m)] + (Math.floor(m / 12) - 1) : '';
    const f = (s.fing && idx >= 0 && s.fing[idx]) || '';
    $('hand').textContent = f ? t('Suggested finger: ') + f : '';
    if (m != null && keyEl[m]) { keyEl[m].classList.add('lit'); if (waiting) keyEl[m].classList.add('target'); follow(keyEl[m]); }
  }
  const curBead = seq.querySelector('.bead.cur');
  if (curBead && seq.scrollHeight > seq.clientHeight + 4) {
    const top = curBead.offsetTop - seq.offsetTop, bot = top + curBead.offsetHeight;
    if (top < seq.scrollTop || bot > seq.scrollTop + seq.clientHeight)
      seq.scrollTop = Math.max(0, top - seq.clientHeight / 2 + curBead.offsetHeight / 2);
  }
  if (ok && prevMode !== 'ok' && !replay) burst();
  // the demo on this computer's speakers too: each new note the lesson plays
  const demoKey = playing && idx >= 0 && s.midi ? s.midi[idx] : null;
  if (demoKey != null && (idx !== lastDemo.idx || s.title !== lastDemo.title) && !replay) tone(demoKey);
  lastDemo = { idx: playing ? idx : -1, title: s.title };
  $('fill').style.width = (s.total ? Math.round(100 * s.pos / s.total) : 0) + '%';
  // "Note 7 is 10": the engine names keys by number; say it in the labels chosen on the screen
  const said = (s.msg || '').replace(/(?:Note|תו) (\\d+) (?:is|הוא) (\\S+)/, (all, i, lab) => {
    const m = s.midi && s.midi[+i - 1];
    return m == null || labelMode === 'num' ? all : t('Note {i} is {key}', { i, key: keyLabel(m) || NAME[pc(m)] });
  });
  if (s.msg && s.msg !== prevMsg) toast(said, /^(Right|נכון)/.test(s.msg) ? 'good' : /^(Stuck|Not that one|Note |אין לחיצה|לא זה|תו )/.test(s.msg) ? 'warn' : '');
  prevMsg = s.msg || '';
}
function paint(s) {
  const pill = $('pill');
  pill.className = 'pill' + (s.piano === true ? ' on' : s.piano === false ? ' off' : '');
  pill.lastChild.textContent = t(s.piano === true ? 'Keyboard connected' : s.piano === false ? 'Keyboard disconnected' : 'Checking…');
  const lesson = s.mode !== 'idle' || !!replay;
  if (lesson && !document.body.classList.contains('focus')) scrollTo({ top: 0 });
  document.body.classList.toggle('focus', lesson);
  $('stopBtn').style.display = replay ? 'none' : '';
  if (lesson) paintLesson(replay ? replay.screen : s);
  else {
    if (prevMode !== 'idle') { clearKeys(); kb.classList.remove('blind', 'keysonly'); loadOverview(); }
    $('stageTitle').textContent = t('Play');
    $('live').classList.toggle('on', live);
    $('liveTxt').textContent = t(live ? 'Live on' : 'Live off');
    kb.classList.toggle('live', live);
    if (!live) { $('heard').textContent = t('Turn on Live to see and play the keys'); heardPrompt = true; }
    else if (heardPrompt) $('heard').textContent = t('Play any key');
  }
  prevMode = replay ? 'replay' : s.mode;
}

// ---------- end of lesson ----------
function confetti(box) {
  const w = document.createElement('div'); w.className = 'confetti';
  for (let i = 0; i < 24; i++) {
    const c = document.createElement('i');
    c.style.left = Math.random() * 100 + '%'; c.style.background = ['var(--gold)', 'var(--sky)', 'var(--blue)', '#ffffff'][i % 4];
    c.style.animationDelay = Math.random() * .3 + 's'; c.style.animationDuration = 1.1 + Math.random() * .4 + 's';
    w.appendChild(c);
  }
  box.appendChild(w); setTimeout(() => w.remove(), 1900);
}
function showSummary(sm) {
  $('sumSong').textContent = nice(sm.song);
  $('sumEyebrow').textContent = t(sm.complete ? 'You learned the whole song' : sm.stopped ? 'Lesson stopped' : 'Lesson done')
    + (profiles.list.length > 1 ? ' · ' + profName(profiles.list.find(p => p.id === profiles.active) || {}) : '');
  $('kLearned').textContent = sm.learned + ' / ' + sm.total;
  $('kNew').textContent = sm.new_today == null ? sm.new : sm.new_today;
  // with the band off no score is given, and the full run's notes are what the number is
  const runPct = sm.score != null ? sm.score : sm.run ? sm.run.accuracy : null;
  $('kScore').textContent = runPct == null ? '–' : runPct + '%';
  $('kTime').textContent = Math.max(1, Math.round(sm.secs / 60));
  const mist = Object.entries(sm.mistakes || {});
  $('sumWhy') && ($('sumWhy').textContent = sm.stopped === 'unplugged' ? t('The keyboard was disconnected or switched off in the middle of the lesson. Your progress is saved.') : '');
  $('kMist').textContent = mist.reduce((a, e) => a + e[1], 0);
  $('kFixed').textContent = (sm.fixed || []).length;
  const ul = $('sumList'); ul.innerHTML = '';
  const line = t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); };
  if (sm.run_before) line(tn(sm.run_before.parts, 'On your own at the start: {n} part, {c} clean', 'On your own at the start: {n} parts, {c} clean', { c: sm.run_before.clean }));
  if (sm.run_after) line(tn(sm.run_after.parts, 'On your own at the end: {n} part, {c} clean', 'On your own at the end: {n} parts, {c} clean', { c: sm.run_after.clean }));
  const r = sm.run;
  if (r) line(t(sm.band ? 'Full run with the band: notes {a}%' : 'Full run: notes {a}%', { a: r.accuracy })
    + (r.rhythm != null ? t(' · rhythm {r}%', { r: r.rhythm }) : '')
    + (r.hesitations != null ? tn(r.hesitations, ' · {n} hesitation', ' · {n} hesitations') : '')
    + tn(r.wrong, ' · {n} wrong key', ' · {n} wrong keys'));
  if (sm.states) line(t('Parts: {a} settling, {b} steady', { a: sm.states.settling, b: sm.states.steady }));
  // the part's emoji comes off; letters of any language stay
  mist.sort((a, b) => b[1] - a[1]).slice(0, 5).forEach(([p, c]) => line(tn(c, '{p}: {n} mistake', '{p}: {n} mistakes', { p: p.replace(/^[^\\p{L}\\p{N}]+/u, '') })));
  if (!mist.length) line(t('No mistakes.'));
  $('sumReplay').style.display = sm.recording ? '' : 'none';
  $('sumMidi').style.display = sm.recording ? '' : 'none';
  $('sumReplay').onclick = () => { closeSummary(); startReplay(sm.recording); };
  $('sumMidi').href = '/recordings/' + sm.recording + '.mid';
  $('summary').classList.add('show');
  if (sm.complete) confetti($('sheet'));
}
function closeSummary() { $('summary').classList.remove('show'); post('/summary/close').catch(() => {}); }
$('sumClose').onclick = closeSummary;

// ---------- replay: the recorded lesson on the screen and on the keyboard, like a video ----------
let replay = null;
const fmt = t => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0');
async function startReplay(id) {
  let data;
  try { data = await getJSON('/api/recording/' + id); if (!data) return; }
  catch (e) { return toast(t('The recording could not be opened'), 'warn'); }
  const len = Math.max(0.1, data.length || 0.1);
  replay = { id, data, len, pos: 0, playing: false, i: 0, t0: 0, screen: { mode: 'idle', seq: [], midi: [] }, pedal: 0 };
  document.body.classList.add('replaying');
  $('replayName').textContent = nice(data.song) + ' · ' + new Date(data.start * 1000).toLocaleString(loc());
  $('seek').max = len.toFixed(1);
  // two lanes over the whole recording: where you played, and where the lesson played
  const lanes = $('lanes'); lanes.innerHTML = '';
  const mark = (t, cls) => { const i = document.createElement('i'); i.className = cls; i.style.left = (t / len * 100) + '%'; i.style.width = Math.max(0.35, 0.6 / len * 100) + '%'; lanes.appendChild(i); };
  data.events.forEach(e => { if (e[3] && e[1] === 'k') mark(e[0], 'you'); else if (e[3] && e[1] === 'd') mark(e[0], 'them'); });
  scrollTo({ top: 0, behavior: 'smooth' });
  playFrom(0);
}
function screenAt(t) {          // the lesson screen as it was at t, and the pedal
  let sc = { mode: 'idle', seq: [], midi: [] }, pedal = 0, i = 0;
  const ev = replay.data.events;
  for (; i < ev.length && ev[i][0] < t; i++) { if (ev[i][1] === 'u') sc = ev[i][2]; else if (ev[i][1] === 'p') pedal = ev[i][2]; }
  return { sc, pedal, i };
}
function playFrom(t) {
  const at = screenAt(t);
  Object.assign(replay, { pos: t, i: at.i, screen: at.sc, pedal: at.pedal, playing: true, t0: performance.now() - t * 1000 });
  clearKeys();
  post('/replay', { id: replay.id, at: t }).then(r => { if (r.status !== 204 && replay) pauseReplay(); }).catch(() => {});
  $('replayPlay').textContent = '❚❚';
  paint(state); drawReplay();
  requestAnimationFrame(stepReplay);
}
function pauseReplay() {
  if (!replay || !replay.playing) return;
  replay.playing = false;
  if (audio) [...sounding.keys()].forEach(letGo);
  post('/replay/stop').catch(() => {});
  $('replayPlay').textContent = '▶';
}
function drawReplay() {
  const r = replay;
  $('seek').value = r.pos.toFixed(1);
  $('replayHead').style.left = (r.pos / r.len * 100) + '%';
  $('replayTime').textContent = fmt(r.pos) + ' / ' + fmt(r.len);
  const you = r.screen.mode === 'wait', them = r.screen.mode === 'demo';
  $('who').className = 'who' + (you ? ' you' : them ? ' them' : '');
  $('who').textContent = you ? t('🙋 You play') : them ? t('🎹 The lesson plays') : '…';
  $('pedal').classList.toggle('down', r.pedal >= 64);
  $('pedal').textContent = t(r.pedal >= 64 ? '🦶 Pedal down' : 'Pedal up');
}
function stepReplay() {
  if (!replay || !replay.playing) return;
  const t = (performance.now() - replay.t0) / 1000, ev = replay.data.events;
  while (replay.i < ev.length && ev[replay.i][0] <= t) {
    const e = ev[replay.i++];
    if (e[1] === 'u') { replay.screen = e[2]; paint(state); }
    else if (e[1] === 'p') { replay.pedal = e[2]; paintPedal(); }
    else if (!e[3] && (e[1] === 'd' || e[1] === 'k')) noteOff(e[2]);   // the key let go
    else if (e[1] === 'd') flash(e[2], 'them', true);
    else if (e[1] === 'k') {
      // a key you pressed: red when it was not the key the lesson waited for
      const sc = replay.screen, want = sc.mode === 'wait' && sc.midi ? sc.midi[sc.expect] : undefined;
      flash(e[2], want !== undefined && want !== e[2] ? 'wrong' : null, true);
    }
  }
  replay.pos = Math.min(t, replay.len);
  drawReplay();
  if (replay.i >= ev.length && t >= replay.len) { pauseReplay(); replay.pos = replay.len; drawReplay(); return; }
  requestAnimationFrame(stepReplay);
}
function stopReplay() {
  if (!replay) return;
  pauseReplay();
  replay = null;
  document.body.classList.remove('replaying'); clearKeys(); paint(state);
}
// dragging the slider: the screen follows the finger; letting go plays from there
let wasPlaying = false;
$('seek').addEventListener('input', () => {
  if (!replay) return;
  if (replay.playing) { wasPlaying = true; pauseReplay(); }
  const t = +$('seek').value, at = screenAt(t);
  Object.assign(replay, { pos: t, screen: at.sc, pedal: at.pedal, i: at.i });
  clearKeys(); paint(state); drawReplay();
});
$('seek').addEventListener('change', () => { if (replay && wasPlaying) { wasPlaying = false; playFrom(+$('seek').value); } });
$('replayPlay').onclick = () => { if (!replay) return; if (replay.playing) pauseReplay(); else playFrom(replay.pos >= replay.len - 0.2 ? 0 : replay.pos); };
$('replayStop').onclick = stopReplay;
addEventListener('keydown', e => {   // like a video player: space, and arrows for 5 seconds
  if (!replay || e.target.tagName === 'INPUT' && e.target.type !== 'range') return;
  if (e.code === 'Space') { e.preventDefault(); $('replayPlay').click(); }
  else if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') {
    e.preventDefault();
    const t = Math.min(replay.len, Math.max(0, replay.pos + (e.code === 'ArrowLeft' ? -5 : 5)));
    if (replay.playing) playFrom(t); else { $('seek').value = t; $('seek').dispatchEvent(new Event('input')); }
  }
});
$('stopBtn').onclick = () => post('/lessonstop').catch(() => {});

// ---------- songs, stats and recordings ----------
function ago(ts) {
  if (!ts) return t('not started');
  const d = Math.floor((Date.now() / 1000 - ts) / 86400);
  return d <= 0 ? t('today') : d === 1 ? t('yesterday') : t('{n} days ago', { n: d });
}
function ring(el, pct) { el.style.setProperty('--p', pct); }
function todayPlan(s) {
  const bits = [];
  if (s.learned) bits.push(t(s.band ? 'a full run on your own and then with the band' : 'a full run on your own'));
  if (s.new_next) bits.push(tn(s.new_next, '{n} new part', '{n} new parts'));
  if (s.learned || s.new_next) bits.push(t('the same run again at the end'));
  if (s.due) bits.push(tn(s.due, 'practice on {n} part that is not steady yet', 'practice on {n} parts that are not steady yet'));
  return joinAnd(bits);
}
let settings = null;
function paintPace() {
  if (!settings) return;
  document.querySelectorAll('#paceSeg button').forEach(b => b.classList.toggle('on', b.dataset.p === settings.pace));
  $('autoTempo').checked = settings.auto_tempo;
  $('speedBox').style.display = settings.auto_tempo ? 'none' : '';
  $('speed').value = settings.speed; $('speedTxt').textContent = settings.speed + '%';
  // with the tempo adapting, the pace sets the start and the slider is out of the picture
  const start = paceSpeed[settings.pace];
  $('speedAuto').style.display = settings.auto_tempo && start ? '' : 'none';
  if (start) $('speedAuto').innerHTML = t('Starts at {speed} by the pace, then follows you', { speed: '<b>' + start + '%</b>' });
}
let paceSpeed = {};
async function saveSettings(ch) {
  Object.assign(settings, ch); paintPace();
  const r = await post('/settings', ch);
  if (r.status === 204) loadOverview();
}
$('paceSeg').onclick = e => {
  const btn = e.target.closest('button'); const p = btn && btn.dataset.p;
  if (!p) return;
  const ch = { pace: p };
  if (paceSpeed[p]) ch.speed = paceSpeed[p];   // the fixed speed moves to the pace's start too, the slider can fine-tune it
  saveSettings(ch);
};
$('autoTempo').onchange = e => saveSettings({ auto_tempo: e.target.checked });
$('speed').oninput = e => { $('speedTxt').textContent = e.target.value + '%'; };
$('speed').onchange = e => saveSettings({ speed: +e.target.value });
function startLesson(song) {
  post('/lesson', { song }).then(r => { if (r.status === 204) toast(t('Starting {song}…', { song: nice(song) })); });
}
async function loadOverview() {
  let o;
  try { o = await getJSON('/api/overview', { cache: 'no-store' }); } catch (e) { return; }
  if (!o) return;
  const st = o.stats;
  if (o.pace_speed) paceSpeed = o.pace_speed;
  if (o.settings) { settings = o.settings; paintPace(); }
  if (o.profiles) paintProfiles(o.profiles);
  // The look follows the profile to every device, in this same window. A profile that never chose one
  // gets the default (the blue set, the grand piano, normal size) and keeps it: it is saved for it now.
  const lookFresh = Date.now() - lookTouched > 8000;
  if (o.settings && lookFresh) {
    const look = {}, miss = {};
    for (const k in DEFAULT_LOOK) { look[k] = o.settings[k] || DEFAULT_LOOK[k]; if (!o.settings[k]) miss[k] = look[k]; }
    if (look.colors !== colorMode) setColors(look.colors);
    if (look.labels !== labelMode) setLabels(look.labels);
    if (look.theme !== theme) setTheme(look.theme);
    if (look.palette !== palette) { palette = look.palette; store('pianoPalette', palette); applyPalette(); }
    if (look.voice !== voice) setVoice(look.voice, true);
    if (look.scale !== userScale) setScale(look.scale, false);
    if (Object.keys(miss).length) post('/settings', miss).catch(() => {});
  }
  // the language lives on the piano: a screen that chose one follows it, and Telegram agrees
  // (a screen that never chose a language stays in English, whatever was saved on the piano)
  if (o.settings && o.settings.lang && o.settings.lang !== LANG && store('pianoLang')) { LANG = o.settings.lang; store('pianoLang', LANG); applyLang(); prevMsg = ''; paint(state); showUpdate(false); }
  $('sStreak').textContent = st.streak; $('sMin').textContent = st.week_minutes; $('sPieces').textContent = st.week_pieces;
  $('sScore').textContent = st.last_score == null ? '–' : st.last_score + '%';
  const songs = o.songs.filter(s => s.total > 0);
  // today's song: the one with the most parts due for review; else a song in progress,
  // the most recent first; else the first song not started yet
  const due = songs.filter(s => s.due > 0).sort((a, b) => b.due - a.due || (b.last || 0) - (a.last || 0))[0];
  const going = songs.filter(s => s.last && s.learned < s.total).sort((a, b) => b.last - a.last)[0];
  const cur = due || going || songs.find(s => !s.last && s.total) || songs[0];
  if (cur) {
    const pct = cur.total ? Math.round(100 * cur.learned / cur.total) : 0;
    $('todaySong').textContent = nice(cur.name); $('todayPct').textContent = pct + '%'; ring($('todayRing'), pct);
    $('todayHead').textContent = t(cur.last ? 'Today' : 'Your first lesson');
    // one short line: where the song stands, what today holds, and how long lessons of it usually take
    const bits = [cur.learned >= cur.total ? t('The whole song is learned')
      : cur.learned ? t('{n} of {t} parts learned', { n: cur.learned, t: cur.total }) : tn(cur.total, '{n} short part', '{n} short parts')];
    if (cur.due) bits.push(tn(cur.due, '{n} part not steady yet', '{n} parts not steady yet'));
    if (cur.new_next) bits.push(tn(cur.new_next, '{n} new part', '{n} new parts'));
    const secs = (o.lesson_secs || {})[cur.name];
    if (secs) bits.push(tn(Math.max(1, Math.round(secs / 60)), 'about {n} minute', 'about {n} minutes'));
    $('todayText').textContent = bits.join(' · ');
    $('todayText').title = todayPlan(cur);
    $('todayGo').textContent = t(cur.learned ? 'Continue {song}' : 'Start {song}', { song: nice(cur.name) });
    $('todayGo').onclick = () => startLesson(cur.name);
  }
  const list = $('songList'); list.innerHTML = '';
  songs.forEach(s => {
    const pct = s.total ? Math.round(100 * s.learned / s.total) : 0;
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = '<button class="go"><span class="ring"><span><b></b></span></span><span><span class="name"></span><span class="meta"></span></span></button>';
    c.querySelector('.ring b').textContent = pct + '%';
    ring(c.querySelector('.ring'), pct);
    c.querySelector('.name').textContent = nice(s.name) + (s.band ? ' 🎻' : '');
    const meta = c.querySelector('.meta');
    const line1 = pct === 100 ? null : s.learned ? t('{n} of {t} parts', { n: s.learned, t: s.total }) : (s.last ? '' : t('Not started · ')) + tn(s.total, '{n} part', '{n} parts');
    if (pct === 100) { const d = document.createElement('span'); d.className = 'done'; d.textContent = t('Learned'); meta.append(d); }
    else meta.append(line1);
    meta.append(document.createElement('br'));
    if (s.due) { const d = document.createElement('span'); d.className = 'due'; d.textContent = tn(s.due, '{n} part not steady yet', '{n} parts not steady yet'); meta.append(d); }
    else meta.append(s.last ? t('Practiced {ago}', { ago: ago(s.last) }) : t('Tap to start'));
    const tag = document.createElement('span'); tag.className = 'tag' + (s.system ? '' : ' up');   // where the song came from
    tag.textContent = t(s.system ? 'System' : 'Uploaded');
    meta.append(document.createElement('br'), tag);
    const go = c.querySelector('.go');
    go.title = t(!s.learned ? 'Start lesson' : s.due ? 'Review now' : s.new_next ? 'Continue' : 'Practice')
      + (s.states.settling || s.states.steady ? t(' · {a} settling, {b} steady', { a: s.states.settling, b: s.states.steady }) : '')
      + (s.score != null ? t(' · last full run {s}%', { s: s.score }) : '');
    go.onclick = () => startLesson(s.name);
    // where a downloaded song came from, and under which licence: the licence asks for it
    if (s.credit && s.credit.source) {
      const page = /^https?:[/][/]/.test(s.credit.page || '') ? s.credit.page : '';
      const cr = document.createElement(page ? 'a' : 'span'); cr.className = 'credit';
      if (page) { cr.href = page; cr.target = '_blank'; cr.rel = 'noopener'; }
      cr.textContent = s.credit.source + (s.credit.license ? ' · ' + s.credit.license : '');
      const author = (s.credit.author || '').replace(/<[^>]*>/g, '').trim();
      cr.title = (author ? t('By {author}', { author }) + ' · ' : '') + t('Source and license of the file');
      c.appendChild(cr);
    }
    if (s.learned || s.last) {
      const rs = document.createElement('button'); rs.className = 'reset'; rs.textContent = '↺';
      rs.title = t('Start this song over'); rs.onclick = async () => {
        if (!confirm(t('Start {song} over? Its learned parts, review schedule and lessons in the history are cleared. Recordings stay.', { song: nice(s.name) }))) return;
        const r = await post('/progress/reset', { song: s.name });
        if (r.status === 204) { toast(t('{song} starts over', { song: nice(s.name) })); loadOverview(); }
        else if (r.status === 409) toast(t('Not during a lesson'), 'warn');
      };
      c.appendChild(rs);
    }
    if (!s.system) {                               // only a song you added can be removed
      c.classList.add('hasDel');
      const del = document.createElement('button'); del.className = 'del'; del.textContent = '🗑';
      del.title = t('Delete this song'); del.onclick = async () => {
        if (!confirm(t('Delete {song}? The file and its progress are removed.', { song: nice(s.name) }))) return;
        const r = await post('/songs/delete', { name: s.name });
        if (r.ok) { toast(t('{song} deleted', { song: nice(s.name) })); loadOverview(); }
        else if (r.status === 409) toast(t('Not during a lesson'), 'warn');
      };
      c.appendChild(del);
    }
    list.appendChild(c);
  });
  const hist = $('histList'); hist.innerHTML = '';
  const recs = $('recList'); recs.innerHTML = '';
  if (!o.recordings.length) { recs.innerHTML = '<div class="empty"></div>'; recs.firstChild.textContent = t('No recordings yet. Every lesson is recorded from its first key.'); }
  pages(recs, o.recordings, 'rec', r => {
    const it = document.createElement('div'); it.className = 'item';
    it.innerHTML = '<div class="grow"><b></b><small></small></div><a class="midi" data-save="lesson.mid" download>MIDI</a><button class="play">▶</button><button class="del">🗑</button>';
    it.querySelector('.midi').title = t('Download the MIDI file'); it.querySelector('.play').title = t('Replay on the screen and the keyboard');
    it.querySelector('.del').title = t('Delete this recording');
    it.querySelector('b').textContent = nice(r.song);
    it.querySelector('small').textContent = dayTime(r.start) + ' · ' + fmt(r.length);
    it.querySelector('.play').onclick = () => startReplay(r.id);
    it.querySelector('a').href = '/recordings/' + r.id + '.mid';
    it.querySelector('.del').onclick = async () => {
      if (!confirm(t('Delete the recording of {song} from {date}?', { song: nice(r.song), date: new Date(r.start * 1000).toLocaleString(loc()) }))) return;
      const res = await post('/recordings/delete', { id: r.id });
      if (res.status === 204) { toast(t('Recording deleted')); loadOverview(); }
    };
    recs.appendChild(it);
  });
  const lessons = o.history || [];
  if (!lessons.length) { hist.innerHTML = '<div class="empty"></div>'; hist.firstChild.textContent = t('Your lessons will show up here.'); }
  lessons.forEach(h => {
    const it = document.createElement('div'); it.className = 'item';
    it.innerHTML = '<div class="grow"><b></b><small></small></div>';
    it.querySelector('b').textContent = nice(h.song);
    const bits = [cap(ago(h.start)), t('{n} min', { n: Math.max(1, Math.round((h.secs || 0) / 60)) })];
    if (h.score != null) bits.push(h.score + '%');
    if (h.stopped) bits.push(t('stopped'));
    it.querySelector('small').textContent = bits.join(' · ');
    hist.appendChild(it);
  });
}
// ---------- a long list is read in numbered pages, not by scrolling ----------
const LIST_PAGE = 8;
const pageNo = {};                                   // the page each list is left on, while the screen is open
function pages(box, items, key, draw) {
  if (!items.length) return;                         // an empty list keeps the line that says so
  const last = Math.ceil(items.length / LIST_PAGE);
  const show = () => {
    const p = pageNo[key] = Math.min(Math.max(pageNo[key] || 1, 1), last);
    box.innerHTML = '';
    items.slice((p - 1) * LIST_PAGE, p * LIST_PAGE).forEach(draw);
    if (last < 2) return;                            // one page needs no numbers
    const nav = document.createElement('div'); nav.className = 'pager';
    const btn = (label, to, cur) => {
      const b = document.createElement('button');
      b.textContent = label;
      if (cur) b.className = 'on';
      if (to == null) b.disabled = true;
      else { b.title = t('Page {n}', { n: to }); b.onclick = () => { pageNo[key] = to; show(); }; }
      nav.appendChild(b);
    };
    btn(LANG === 'he' ? '\u203a' : '\u2039', p > 1 ? p - 1 : null);
    let shown = 0;                                   // the first, the last, and the ones around the page being read
    for (let i = 1; i <= last; i++) {
      if (i !== 1 && i !== last && Math.abs(i - p) > 1) continue;
      if (shown && i > shown + 1) {
        const g = document.createElement('span'); g.className = 'gap'; g.textContent = '\u2026'; nav.appendChild(g);
      }
      btn(String(i), i === p ? null : i, i === p);
      shown = i;
    }
    btn(LANG === 'he' ? '\u2039' : '\u203a', p < last ? p + 1 : null);
    box.appendChild(nav);
  };
  show();
}
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;
// "Today 18:40", "Yesterday 21:05", else the weekday within a week, else the date
function dayTime(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString(loc() || [], { hour: '2-digit', minute: '2-digit' });
  const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  const day = days === 0 ? t('Today') : days === 1 ? t('Yesterday') : days < 7 ? d.toLocaleDateString(loc() || [], { weekday: 'long' }) : d.toLocaleDateString(loc());
  return day + ' ' + hm;
}

// ---------- adding songs: a file from this device, or a search in the online archive ----------
async function uploadFiles(files) {
  for (const f of files) {
    if (f.size > 4 * 1024 * 1024) { toast(t('{name} is too big for a MIDI file', { name: f.name }), 'warn'); continue; }
    const r = await fetch('/songs/upload', { method: 'POST', headers: { 'X-Filename': encodeURIComponent(f.name) },
                                             body: await f.arrayBuffer() }).catch(() => null);
    showAdded(r);
  }
  loadOverview();
}
async function showAdded(r) {
  if (!r) return toast(t('The keyboard machine did not answer'), 'warn');
  const j = await r.json().catch(() => ({}));
  if (j.ok) toast(t('Added {name}: {n} parts to learn', { name: nice(j.name), n: j.parts }), 'good');
  else toast((j.why || t('The file was not added')) + (j.name ? ' (' + j.name + ')' : ''), 'warn');
}
$('upload').onchange = e => { uploadFiles([...e.target.files]); e.target.value = ''; };
const drop = $('drop');
['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
async function search() {
  const q = $('q').value.trim(); if (!q) return;
  const box = $('qRes'); box.innerHTML = '<div class="empty"></div>'; box.firstChild.textContent = t('Searching…');
  let j;
  try { j = await getJSON('/api/search?q=' + encodeURIComponent(q)); if (!j) return; lastSearch = j.search || ''; }
  catch (e) { box.innerHTML = '<div class="empty"></div>'; box.firstChild.textContent = t('The search did not answer'); return; }
  box.innerHTML = '';
  if (j.error || !j.results.length) {
    box.innerHTML = '<div class="empty"></div>';
    box.firstChild.textContent = j.error || (t('Nothing found') + (j.missing && j.missing.length ? t('. Is "{w}" spelled right?', { w: j.missing.join(', ') }) : ''));
    return;
  }
  hits = j.results; added = new Set(); showHits(0);
}
// the results, 10 to a page, with page numbers under them like any search site
const PER_PAGE = 10;
let hits = [], added = new Set();
function showHits(page) {
  const box = $('qRes'); box.innerHTML = '';
  const pages = Math.ceil(hits.length / PER_PAGE);
  const head = document.createElement('div'); head.className = 'hitcount';
  head.textContent = t('{n} results', { n: hits.length }) + (pages > 1 ? ' · ' + t('page {p} of {n}', { p: page + 1, n: pages }) : '');
  box.appendChild(head);
  hits.slice(page * PER_PAGE, (page + 1) * PER_PAGE).forEach(h => {
    const row = document.createElement('div'); row.className = 'hit';
    row.innerHTML = '<span><b></b><small></small></span><button class="btn gold"></button>';
    row.querySelector('b').textContent = h.name;
    row.querySelector('small').textContent = h.source + (h.license ? ' · ' + h.license : '');
    const btn = row.querySelector('button');
    btn.textContent = added.has(h.id) ? '✓' : t('Add'); btn.disabled = added.has(h.id);
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = '…';
      const r = await post('/songs/fetch', { id: h.id, search: lastSearch });
      if (r.status !== 401) { await showAdded(r); btn.textContent = '✓'; added.add(h.id); loadOverview(); }
      else { btn.disabled = false; btn.textContent = t('Add'); }
    };
    box.appendChild(row);
  });
  if (pages < 2) return;
  const nav = document.createElement('div'); nav.className = 'pager';
  const btn = (label, p, on) => {
    const b = document.createElement('button'); b.textContent = label; b.disabled = p < 0 || p >= pages;
    if (on) b.className = 'on';
    b.onclick = () => { showHits(p); const top = $('drop').getBoundingClientRect().top; if (top < 80) scrollBy({ top: top - 80, behavior: 'smooth' }); };
    nav.appendChild(b);
  };
  btn(LANG === 'he' ? '›' : '‹', page - 1);
  for (let p = 0; p < pages; p++) btn(String(p + 1), p, p === page);
  btn(LANG === 'he' ? '‹' : '›', page + 1);
  box.appendChild(nav);
}
$('qGo').onclick = search;
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });

// ---------- version, updates and backup (footer) ----------
// the updater writes its state in English: the known sentences are shown in the language
function updMsg(m) {
  for (const k of ['Downloading {x}', 'Installing {x}', 'The download of {x} failed', 'The installer stopped. Details: {x}',
                   'No valid version to install', 'The download was damaged', 'The release has no installer']) {
    const re = new RegExp('^' + k.replace(/[.]/g, '[.]').replace('{x}', '(.+)') + '$'), got = (m || '').match(re);
    if (got) return t(k, { x: got[1] || '' });
  }
  return m;
}
async function showUpdate(force) {
  let u; try { u = await getJSON('/api/update' + (force ? '?force' : ''), { cache: 'no-store' }); } catch (e) { return; }
  if (!u) return;
  { const p = String(u.version).split('.');   // the footer shows 1.0, not 1.0.0: a trailing zero patch is dropped
    VER_TEXT = (p.length === 3 && p[2] === '0') ? p[0] + '.' + p[1] : u.version; paintVersion(); }
  const st = $('updState'); if (!st) return;                 // the footer version line may have been rewritten in edit mode
  st.textContent = '';
  if (u.status && u.status.state === 'running') { st.textContent = '· ' + updMsg(u.status.message) + '…'; setTimeout(() => showUpdate(false), 4000); return; }
  if (u.status && u.status.state === 'failed') st.textContent = t('· last update failed: {m}', { m: updMsg(u.status.message) });
  if (u.available) {
    const b = document.createElement('a'); b.href = '#'; b.textContent = t(' · Install {v}', { v: u.latest });
    b.onclick = async ev => { ev.preventDefault();
      if (!confirm(t('Install version {v}? The keyboard is unavailable for a minute or two, and settings, songs and progress stay.', { v: u.latest }))) return;
      const r = await post('/update', {}); const j = await r.json().catch(() => ({}));
      if (r.status === 202) { toast(t('Updating to {v}…', { v: j.version })); setTimeout(() => showUpdate(false), 4000); } else toast(j.why || t('Not now'), 'warn'); };
    st.appendChild(b);
  } else if (force) st.textContent = u.error ? '· ' + u.error : t('· this is the newest version');
}
{ const uc = $('updCheck'); if (uc) uc.onclick = ev => { ev.preventDefault(); showUpdate(true); }; }
showUpdate(false);

// ---------- about is a page of its own: #about shows it, any other link goes back ----------
// /about is its own address; the links inside the page change it without reloading
function route() {
  const about = location.pathname === '/about';
  if (about !== document.body.classList.contains('about')) scrollTo({ top: 0 });
  document.body.classList.toggle('about', about);
  navLinks.forEach(a => { if (about) a.classList.toggle('on', a.getAttribute('href') === '/about'); });
  const target = !about && location.hash && document.querySelector(location.hash);
  if (target) target.scrollIntoView();
  if (!about) requestAnimationFrame(markSection);    // back from about: the menu marks by what is on screen
}
addEventListener('popstate', route);
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="/"]');
  if (!a || a.hasAttribute('download') || e.ctrlKey || e.metaKey || e.shiftKey) return;
  const url = new URL(a.href);
  if (url.pathname !== '/' && url.pathname !== '/about') return;
  e.preventDefault();
  if (url.pathname + url.hash !== location.pathname + location.hash) history.pushState(null, '', url.pathname + url.hash);
  route();
  if (url.pathname === '/' && !url.hash) scrollTo({ top: 0, behavior: 'smooth' });
});

// ---------- the header: the section on screen is marked in the menu ----------
{ const yr = $('year'); if (yr) yr.textContent = new Date().getFullYear(); }
const navLinks = [...document.querySelectorAll('#nav a')];
// the part holding most of the screen is the marked one. the banner and the head of the page hold screen
// too, and while one of them leads, nothing in the menu is marked
const spied = navLinks.map(a => { const h = a.getAttribute('href');
  return { a, el: h.startsWith('/#') ? document.querySelector(h.slice(1)) : null }; }).filter(s => s.el);
const plain = ['.arm-banner', '.band'].map(q => document.querySelector(q)).filter(Boolean);
const onScreen = el => { const r = el.getBoundingClientRect();     // the height it holds under the header
  return Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 72)); };
function markSection() {
  if (document.body.classList.contains('about')) return;           // the about page marks its own entry
  if (document.documentElement.dataset.atop === 'yes') {           // the head of the page belongs to no part
    navLinks.forEach(a => a.classList.remove('on')); return; }
  let lead = null, most = 0;
  spied.forEach(s => { const h = onScreen(s.el); if (h > most) { most = h; lead = s.a; } });
  plain.forEach(el => { if (onScreen(el) > most) { most = onScreen(el); lead = null; } });
  navLinks.forEach(a => a.classList.toggle('on', a === lead));
}
{ let waiting = false;
  const again = () => { if (waiting) return; waiting = true;
    requestAnimationFrame(() => { waiting = false; markSection(); }); };
  addEventListener('scroll', again, { passive: true });
  addEventListener('resize', again);
  markSection(); }
// the head of the page: while the banner is on screen the menu leaves the name to it
{ const bn = document.querySelector('.arm-banner');
  if (bn) new IntersectionObserver(([e]) => { document.documentElement.dataset.atop = e.isIntersecting ? 'yes' : 'no'; },
    { rootMargin: '-72px 0px 0px 0px' }).observe(bn);   // rootMargin takes px, not rem: 72px clears the 4rem header
  else document.documentElement.dataset.atop = 'no'; }
route();

// ---------- the loop ----------
async function tick() {
  try {
    const s = await getJSON(document.visibilityState === 'visible' ? '/state?seen' : '/state', { cache: 'no-store' });
    if (!s) return;
    const ev = s.live || [];
    if (lastLiveId === null) lastLiveId = ev.length ? ev[ev.length - 1][0] : 0;   // no replay of old presses
    const fresh = ev.filter(e => e[0] > lastLiveId);
    if (fresh.length) lastLiveId = fresh[fresh.length - 1][0];
    state = s;
    if (live && s.mode === 'idle' && !replay) fresh.forEach(e => heard(e[1]));
    if (s.volume != null && Date.now() - volTouched > 2000) { $('vol').value = s.volume; $('volTxt').textContent = s.volume + '%'; }
    $('pedalBtn').classList.toggle('feet', !!s.pedal);
    paintPedal();
    if (lastNotice === null) lastNotice = s.notice[0];
    else if (s.notice[0] !== lastNotice) { lastNotice = s.notice[0]; if (s.mode === 'idle' && !/^🏁/.test(s.notice[1])) toast(titles(s.notice[1].split('\\n')[0])); }
    if (s.summary && (!lastSummary || s.summary.id !== lastSummary)) { lastSummary = s.summary.id; showSummary(s.summary); }
    paint(s);
  } catch (e) { /* the engine restarted, the next tick catches up */ }
}
loadOverview();
setInterval(tick, 100);
setInterval(() => { if (state.mode === 'idle') loadOverview(); }, 30000);
tick();
</script>
</body>
</html>"""


if __name__ == "__main__":
    Engine().run()
