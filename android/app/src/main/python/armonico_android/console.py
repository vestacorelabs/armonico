"""console.py - the armonico command inside the app.

On Linux, `armonico <command>` sends the piano commands over MQTT the way Telegram does and
prints the answers addressed to the terminal, and runs the system commands itself. Here the same
commands are typed in the app's command screen. The piano commands go through the bridge exactly
as a Telegram command does, with the chat "app-console", and the answers come back to the screen.
The Telegram names (/pianoplay and the rest) work too.

restart and logs belong to the app (the service and the log screen) and are handled there.
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.request

from . import lessons

CHAT = "app-console"
REPO = "vestacorelabs/armonico"

HELP_EN = """Piano
  status                          keyboard, player, alarm and the last shortcut
  check                           every check, its result and what to do, saved as a report
  play <song>                     play a song from the songs folder
  songs [filter]                  list the songs
  stop                            stop the alarm or the song
  pause                           pause the song, or go on
  shortcut list                   list the shortcuts
  shortcut learn <name>           play it on the keyboard, saved after 5 seconds of silence
  shortcut add <name> <notes...>  save one from MIDI note numbers, middle C is 60
  shortcut del <name>             delete one
  shortcut cancel                 stop learning
  lesson list                     songs and how much of each is learned
  lesson start <song> [reset]     start today's lesson, reset starts the song over
  lesson stop                     stop the lesson and save
  getmidi <name> | <number>       search free MIDI archives, then download a result
  profile [name]                  list the profiles, or switch to one
  code [show | on | off | new]    the lesson screen's code: show it, turn asking on or off, or draw a new one
  text export                     the wording edited on the lesson screen, as the file a release carries

System
  update                          look for a newer release
  restart                         restart the bridge
  logs                            the bridge's log
  record on | off                 record every MQTT message, of every topic, to a file per day
  record [follow [filter]]        the recorder's state and files, or today's last lines
  unstick                         bring a connected but silent keyboard back
  reset                           full reset: unstick, the keyboard opened again, the broker again
  version                         the installed version"""

HELP_HE = """פסנתר
  status                          המקלדת, הנגן, השעון המעורר והקיצור האחרון
  check                           כל הבדיקות, התוצאה ומה לעשות, נשמר כדוח
  play <song>                     ניגון שיר מתיקיית השירים
  songs [filter]                  רשימת השירים
  stop                            עצירת השעון המעורר או השיר
  pause                           השהיית השיר או המשך
  shortcut list                   רשימת הקיצורים
  shortcut learn <name>           לנגן אותו במקלדת, נשמר אחרי 5 שניות של שקט
  shortcut add <name> <notes...>  שמירה ממספרי תווי MIDI, דו אמצעי הוא 60
  shortcut del <name>             מחיקה
  shortcut cancel                 ביטול הלמידה
  lesson list                     השירים וכמה נלמד מכל אחד
  lesson start <song> [reset]     השיעור של היום, reset מתחיל את השיר מחדש
  lesson stop                     עצירת השיעור ושמירה
  getmidi <name> | <number>       חיפוש בארכיוני MIDI חופשיים, ואז הורדת תוצאה
  profile [name]                  רשימת הפרופילים או החלפה
  code [show | on | off | new]    הקוד של מסך השיעורים: הצגה, הפעלה או כיבוי, או קוד חדש
  text export                     הנוסח שנערך במסך השיעורים, כקובץ שגרסה נושאת

מערכת
  update                          בדיקה אם יש גרסה חדשה
  restart                         הפעלה מחדש של הגשר
  logs                            הלוג של הגשר
  record on | off                 הקלטת כל הודעות ה-MQTT, מכל נושא, לקובץ ליום
  record [follow [filter]]        מצב ההקלטה והקבצים, או השורות האחרונות של היום
  unstick                         החזרת מקלדת מחוברת ששותקת
  reset                           איפוס מלא: שחרור, פתיחה מחדש של המקלדת, ושוב לברוקר
  version                         הגרסה המותקנת"""

# The Telegram names, as the terminal reads them (cli/armonico.py, TG)
TG = {"/pianostatus": "status", "/pianoplay": "play", "/pianosongs": "songs", "/pianostop": "stop",
      "/pianoreset": "reset", "/seqlist": "shortcut list", "/seqlearn": "shortcut learn",
      "/seqadd": "shortcut add", "/seqdel": "shortcut del", "/seqcancel": "shortcut cancel",
      "/lesson": "lesson start", "/lessons": "lesson list", "/lessonstop": "lesson stop",
      "/getmidi": "getmidi", "/pianoprofile": "profile", "/pianocode": "code"}

# Answers speak of Telegram commands; on this screen they read as the commands typed here
OUT = sorted(TG.items(), key=lambda kv: -len(kv[0]))


def screen_text(text):
    for tg, cmd in OUT:
        text = re.sub(re.escape(tg) + r"\b", cmd, text)
    return text


def version(b):
    try:
        with open(os.path.join(b.cfg.get("engine_dir", ""), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "dev"


def shown(v):
    return re.sub(r"^(\d+\.\d+)\.0$", r"\1", v)


def run(b, line):
    """One typed line. Runs on its own thread: some commands wait (a check, an update, unstick)."""
    line = line.strip()
    if not line:
        return
    b.console_add("in", line)
    threading.Thread(target=_run_as_app, args=(b, line), daemon=True, name="armonico-console").start()


def _run_as_app(b, line):
    b.for_chat(CHAT)                 # this thread answers in the app's language
    _run(b, line)


def _run(b, line):
    try:
        _dispatch(b, line)
    except Exception as e:
        b.console_add("out", f"⚠️ {type(e).__name__}: {e}")


def _dispatch(b, line):
    word, _, rest = line.partition(" ")
    rest = rest.strip()
    if word.startswith("/"):
        cmd = word.split("@", 1)[0].lower()
        if cmd in ("/showlog", "/stoplog"):
            b.events.put(("live_log", cmd == "/showlog"))
            return out(b, b.L("🟢 Live log on", "🟢 הלוג החי פועל") if cmd == "/showlog" else b.L("🔴 Live log off", "🔴 הלוג החי כבוי"))
        if cmd not in TG:
            return out(b, b.L(f"Unknown command: {word}. See: help", f"פקודה לא מוכרת: {word}. ראה: help"))
        word, _, more = TG[cmd].partition(" ")
        rest = f"{more} {rest}".strip()
    # as in cli/armonico.py: the lesson screen's own editing tools on and off
    if word in ("EditMode10", "EditMode10Off"):
        return game_cmd(b, "editmode", f"secret:{b.admin_secret} {'on' if word == 'EditMode10' else 'off'}")
    if word in ("PlexFun10", "PlexFun10Off"):                 # the TV card (Plex), the same way
        return game_cmd(b, "plexfun", f"secret:{b.admin_secret} {'on' if word == 'PlexFun10' else 'off'}")
    word = word.lower()
    a = rest.split()
    sub = a[0].lower() if a else ""
    tail = " ".join(a[1:])
    he = b.lang == "he"

    if word in ("help", "?"):
        return out(b, HELP_HE if he else HELP_EN)
    if word == "version":
        return out(b, f"Armonico {shown(version(b))}")
    if word == "status":
        return out(b, b.status_text())
    if word == "check":
        return out(b, check(b))
    if word == "play":
        if not rest:
            return usage(b, "play <song>")
        return shortcut_cmd(b, "play", rest)
    if word == "songs":
        return shortcut_cmd(b, "songs", rest)
    if word == "stop":
        b.events.put(("stop", "Armonico app"))
        return out(b, b.L("⏹️ Stop sent", "⏹️ נשלחה עצירה"))
    if word == "pause":
        b.events.put(("mqtt", "piano/pause", "toggle"))
        return out(b, b.L("⏯️ Sent", "⏯️ נשלח"))
    if word == "shortcut":
        sub = sub or "list"
        if sub == "list" or sub == "cancel":
            return shortcut_cmd(b, sub, "")
        if sub == "add":
            if len(a) < 3:
                return usage(b, b.L("shortcut add <name> <note> <note> ...   (MIDI note numbers, middle C is 60)",
                                       "shortcut add <name> <note> <note> ...   (מספרי תווי MIDI, דו אמצעי הוא 60)"))
            return shortcut_cmd(b, "add", tail)
        if sub in ("del", "delete", "rm"):
            if len(a) < 2:
                return usage(b, "shortcut del <name>")
            return shortcut_cmd(b, "del", tail)
        if sub == "learn":
            if len(a) < 2:
                return usage(b, "shortcut learn <name>")
            return shortcut_cmd(b, "learn", tail)
        return out(b, b.L(f"Unknown: shortcut {sub}. See: help", f"לא מוכר: shortcut {sub}. ראה: help"))
    if word == "lesson":
        sub = sub or "list"
        if sub == "list":
            return game_cmd(b, "lessons", "")
        if sub == "start":
            if len(a) < 2:
                return usage(b, "lesson start <song> [reset]")
            return game_cmd(b, "lesson", tail)
        if sub == "stop":
            return game_cmd(b, "lessonstop", "")
        return out(b, b.L(f"Unknown: lesson {sub}. See: help", f"לא מוכר: lesson {sub}. ראה: help"))
    if word == "getmidi":
        if not rest:
            return usage(b, b.L("getmidi <name>, then getmidi <number> to download a result",
                                   "getmidi <name>, ואז getmidi <number> כדי להוריד תוצאה"))
        return game_cmd(b, "getmidi", rest)
    if word == "profile":
        return game_cmd(b, "profile", f"secret:{b.admin_secret} {rest}".strip())
    if word == "code":
        if sub in ("", "show"):
            return game_cmd(b, "code", f"secret:{b.admin_secret}")
        if sub in ("on", "off", "new"):
            return game_cmd(b, "code", f"secret:{b.admin_secret} {sub}")
        return usage(b, "code [show | on | off | new]")
    if word == "text":
        if sub != "export":
            return usage(b, "text export")
        return game_cmd(b, "textexport", "")
    if word == "unstick":
        b.unstick(CHAT)
        return
    if word == "reset":
        return shortcut_cmd(b, "reset", f"secret:{b.admin_secret}")
    if word == "record":
        return record(b, a)
    if word == "update":
        return out(b, update(b))
    return out(b, b.L(f"Unknown command: {word}. See: help", f"פקודה לא מוכרת: {word}. ראה: help"))


def out(b, text):
    b.console_add("out", text)


def usage(b, text):
    out(b, b.L(f"Usage: {text}", f"שימוש: {text}"))


def shortcut_cmd(b, action, args):
    b.events.put(("mqtt", "piano/shortcut/cmd", f"{action}|{CHAT}|{args}"))


def game_cmd(b, action, args):
    if not lessons.running():
        b.handle_game_cmd(f"{action}|{CHAT}|")
        return
    b.pub("piano/game/cmd", f"{action}|{CHAT}|{args}")


# ---------------------------------------------------------------------------- record
def record(b, a):
    sub = a[0].lower() if a else "status"
    r = b.recorder
    if sub in ("on", "off"):
        r.set(sub == "on")
        b.log("⏺️ MQTT recorder on" if sub == "on" else "⏹️ MQTT recorder off")
        b._status()
        sub = "status"
    if sub == "status":
        s = r.summary()
        # Hebrew lines keep a path, a file name and "1.2 MB" each in one left-to-right piece (LRI ... PDI)
        ltr = lambda t: f"\u2066{t}\u2069"
        lines = [b.L(f"Recorder: {'on' if s['on'] else 'off'}", f"הקלטה: {'פועלת' if s['on'] else 'כבויה'}"),
                 b.L(f"Folder: {s['folder']}", f"תיקייה: {ltr(s['folder'])}")]
        if s["days"]:
            lines.append(b.L(f"Files: {s['days']} days, {s['mb']} MB, today {s['today']}",
                             f"קבצים: {s['days']} ימים, {ltr(str(s['mb']) + ' MB')}, היום {ltr(s['today'])}"))
        lines.append(b.L(f"Kept: {s['keep']} days", f"נשמר: {s['keep']} ימים"))
        return out(b, "\n".join(lines))
    if sub == "follow":
        got = r.tail(20, " ".join(a[1:]))
        return out(b, "\n".join(got) if got else b.L("Nothing recorded today", "לא הוקלט כלום היום"))
    out(b, b.L(f"Unknown: record {sub}. See: help", f"לא מוכר: record {sub}. ראה: help"))


# ---------------------------------------------------------------------------- update
def newer(a, b):
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v)[:4]] or [0]
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    return pa + [0] * (n - len(pa)) > pb + [0] * (n - len(pb))


def update(b):
    """The release the Linux updater would install (setup/update.sh asks the same address), and
    its APK when the release carries one. Installing is Android's: the link opens the download."""
    mine = version(b)
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "armonico-android"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.loads(r.read())
    except Exception as e:
        return b.L(f"❌ The releases could not be read: {e}", f"❌ לא הצלחתי לקרוא את הגרסאות: {e}")
    tag = str(rel.get("tag_name", "")).lstrip("v")
    if not tag:
        return b.L("❌ No release found", "❌ לא נמצאה גרסה")
    if not newer(tag, mine):
        return b.L(f"✅ Armonico {shown(mine)} is the newest version", f"✅ Armonico {shown(mine)} היא הגרסה החדשה ביותר")
    apk = next((x.get("browser_download_url") for x in rel.get("assets", [])
                if str(x.get("name", "")).endswith(".apk")), None)
    link = apk or rel.get("html_url") or f"https://github.com/{REPO}/releases/latest"
    if apk:
        return b.L(f"⬆️ Version {shown(tag)} is out (this is {shown(mine)}). Download and install:\n{link}",
                   f"⬆️ יצאה גרסה {shown(tag)} (כאן {shown(mine)}). להורדה ולהתקנה:\n{link}")
    return b.L(f"⬆️ Version {shown(tag)} is out (this is {shown(mine)}), but its APK is not attached yet:\n{link}",
               f"⬆️ יצאה גרסה {shown(tag)} (כאן {shown(mine)}), אבל ה-APK שלה עוד לא צורף:\n{link}")


# ---------------------------------------------------------------------------- check
OK, NOTE, PROBLEM = "✔", "!", "✘"


def check_rows(b):
    """What `armonico status` and `armonico check` look at, as far as a phone has it."""
    rows = []
    he = b.lang == "he"
    L = b.L
    rows.append((L("Bridge", "גשר"), OK, L("running", "פועל")))

    if b.mode == "local":
        br = b.broker
        if br is None:
            rows.append((L("Broker", "ברוקר"), PROBLEM, L(f"the built-in broker did not start: {b.broker_error}",
                                                          f"הברוקר המובנה לא עלה: {b.broker_error}")))
        else:
            rows.append((L("Broker", "ברוקר"), OK, L(f"built-in, port {br.port} of this phone, logins piano and piano-rec",
                                                     f"מובנה, פורט {br.port} של הטלפון, משתמשים piano ו-piano-rec")))
            if br.ws_server is not None:
                rows.append((L("Websockets", "Websockets"), OK, L(f"port {br.ws_port} on the network, for the status screen",
                                                                  f"פורט {br.ws_port} ברשת, בשביל מסך הסטטוס")))
            else:
                rows.append((L("Websockets", "Websockets"), NOTE, L(f"port {br.ws_port} did not open: {br.ws_error}. The status screen cannot connect",
                                                                    f"פורט {br.ws_port} לא נפתח: {br.ws_error}. מסך הסטטוס לא יתחבר")))
    host = "127.0.0.1" if b.mode == "local" else b.cfg.get("host", "")
    if b.broker_up:
        rows.append((L("MQTT", "MQTT"), OK, L(f"connected to {host}", f"מחובר ל-{host}")))
    else:
        rows.append((L("MQTT", "MQTT"), PROBLEM, L(f"not connected to {host or 'no address'}: {b.broker_error or 'trying'}",
                                                   f"לא מחובר ל-{host or 'אין כתובת'}: {b.broker_error or 'מנסה'}")))

    if b.connected:
        rows.append((L("Keyboard", "מקלדת"), OK, b._device_name() or L("connected", "מחוברת")))
    else:
        rows.append((L("Keyboard", "מקלדת"), PROBLEM, L("not connected: plug it in with a USB OTG cable",
                                                        "לא מחוברת: לחבר בכבל USB OTG")))

    port = int(b.cfg.get("lesson_port", 8099))
    if not lessons.running():
        why = lessons.error()
        rows.append((L("Lesson screen", "מסך השיעורים"), PROBLEM if why else NOTE,
                     why or L("starting", "עולה")))
    else:
        try:
            code = lessons.screen_code()
            head = {"X-Screen-Code": code} if code else {}
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/overview", headers=head), timeout=4) as r:
                songs = json.loads(r.read()).get("songs", [])
            rows.append((L("Lesson screen", "מסך השיעורים"), OK, L(f"answering on port {port}, {len(songs)} songs",
                                                                   f"עונה בפורט {port}, {len(songs)} שירים")))
            for x in songs:
                if not x.get("total"):
                    rows.append((L("Not a lesson", "לא שיעור"), NOTE,
                                 f"{x.get('name')}: {x.get('problem') or L('cannot be read', 'לא נקרא')}"))
        except Exception as e:
            rows.append((L("Lesson screen", "מסך השיעורים"), PROBLEM, L(f"no answer ({e})", f"אין תשובה ({e})")))

    n = len(b.song_names())
    rows.append((L("Songs", "שירים"), OK if n else NOTE, L(f"{n} in the songs folder", f"{n} בתיקיית השירים")))

    if b.bot is None:
        rows.append(("Telegram", NOTE, L("no bot token: Telegram commands come only through Home Assistant",
                                         "אין טוקן לבוט: פקודות טלגרם מגיעות רק דרך Home Assistant")))
    else:
        i = b.bot.info()
        if i.get("error"):
            rows.append(("Telegram", PROBLEM, i["error"]))
        elif not i.get("owner"):
            rows.append(("Telegram", NOTE, L("the bot runs, no chat is connected yet", "הבוט פועל, עוד לא חובר צ'אט")))
        else:
            who = f"@{i['username']}, " if i.get("username") else ""
            rows.append(("Telegram", OK, L(f"{who}one chat connected", f"{who}צ'אט אחד מחובר")))

    s = b.recorder.summary()
    rows.append((L("Recorder", "הקלטה"), OK, L(f"on, {s['days']} days kept" if s["on"] else "off",
                                              f"פועלת, {s['days']} ימים שמורים" if s["on"] else "כבויה")))
    try:
        free = shutil.disk_usage(b.data_dir).free / 1e9
        rows.append((L("Disk", "אחסון"), OK if free > 1 else NOTE,
                     L(f"{free:.1f} GB free" + ("" if free > 1 else ", running low"),
                       f"\u2066{free:.1f} GB\u2069 פנויים" + ("" if free > 1 else ", נגמר המקום"))))
    except OSError:
        pass
    return rows


def check(b):
    rows = check_rows(b)
    n = {k: sum(1 for r in rows if r[1] == k) for k in (OK, NOTE, PROBLEM)}
    lines = [b.L(f"Full check  Armonico {shown(version(b))}", f"בדיקה מלאה  Armonico {shown(version(b))}"), ""]
    lines += [f"{mark} {label}: {text}" for label, mark, text in rows]
    lines += ["", b.L(f"{n[PROBLEM]} failed, {n[NOTE]} warnings, {n[OK]} passed",
                      f"{n[PROBLEM]} נכשלו, {n[NOTE]} אזהרות, {n[OK]} עברו")]
    report = "\n".join(lines)
    try:
        folder = os.path.join(b.data_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("check-%Y%m%d-%H%M%S.txt"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        report += "\n" + b.L(f"Report: {path}", f"דוח: \u2066{path}\u2069")
    except OSError as e:
        report += "\n" + b.L(f"! The report was not saved: {e}", f"! הדוח לא נשמר: {e}")
    return report
