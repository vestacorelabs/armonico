#!/usr/bin/env python3
"""telegram_setup.py - the optional Telegram bot, set up step by step, with a question before each step.

You make the bot yourself in Telegram (BotFather, one minute). From its token this does the rest:

  1  checks the token
  2  gives the bot a description, a short description, the command list and a picture
  3  finds your chat, by you pressing Start on the bot
  4  connects the bot to Home Assistant, with your chat as the one allowed to use it
  5  sends a test message

Every step asks first. A step you decline, or one that fails, is not skipped silently: it prints
exactly what to do by hand instead. The token is read without echo, kept in memory, and goes only
to Telegram and to Home Assistant. It is never written to a file.

  telegram_setup.py            from the environment: HA_URL, HA_TOKEN (optional, for step 4)
  telegram_setup.py --refresh  only the bot's profile (steps 1 and 2), with no questions, for an update:
                               the token comes from TELEGRAM_TOKEN
  SECRETS_FILE=<path>          when set, the token and the allowed chats are kept there for updates
                               (a file only root reads; the person agreed to it in the installer)
  TELEGRAM_API=<url>           another address for the Telegram API (tests only)
"""
import getpass
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")
PHOTO = HERE / "bot-photo.jpg"

NAME_HINT = "Armonico Piano"
DESCRIPTION = ("Your piano on Telegram. Check the keyboard, play a song, run a lesson, "
               "manage shortcuts and follow the log, from anywhere.")
SHORT = "Armonico: your piano on Telegram"
DESCRIPTION_HE = "הפסנתר שלך בטלגרם. מצב המקלדת, ניגון שיר, שיעור, קיצורים ולוג חי, מכל מקום."
SHORT_HE = "Armonico: הפסנתר שלך בטלגרם"

COMMANDS = [
    ("pianostatus", "Keyboard, player and alarm status"),
    ("pianoplay", "Play a song: /pianoplay ode_to_joy"),
    ("pianosongs", "List the songs, optionally filtered"),
    ("pianostop", "Stop the alarm or the song"),
    ("pianoreset", "Full reset: unstick, USB reset, restart everything"),
    ("seqlist", "List the shortcuts"),
    ("seqlearn", "Learn a shortcut by playing it: /seqlearn name"),
    ("seqadd", "Add a shortcut by numbers: /seqadd name 60 62 64"),
    ("seqdel", "Delete a shortcut: /seqdel name"),
    ("seqcancel", "Cancel learning"),
    ("lesson", "Today's lesson: /lesson ode_to_joy"),
    ("lessons", "Songs and progress"),
    ("lessonstop", "Stop the lesson and save"),
    ("getmidi", "Search and download a song: /getmidi bella ciao"),
    ("pianoprofile", "Profiles, or switch: /pianoprofile Noa"),
    ("pianocode", "The lesson screen code"),
    ("showlog", "Live log in this chat"),
    ("stoplog", "Stop the live log"),
]
COMMANDS_HE = {
    "pianostatus": "מצב המקלדת, הנגן וההתראה", "pianoplay": "נגן שיר: /pianoplay ode_to_joy",
    "pianosongs": "רשימת השירים, אפשר לסנן", "pianostop": "עצור את ההתראה או השיר",
    "pianoreset": "איפוס מלא: שחרור, איפוס USB והפעלה מחדש של הכול",
    "seqlist": "רשימת הקיצורים", "seqlearn": "למד קיצור בנגינה: /seqlearn name",
    "seqadd": "הוסף קיצור במספרים: /seqadd name 60 62 64", "seqdel": "מחק קיצור: /seqdel name",
    "seqcancel": "בטל למידה", "lesson": "השיעור של היום: /lesson ode_to_joy",
    "lessons": "שירים והתקדמות", "lessonstop": "עצור את השיעור ושמור",
    "getmidi": "חפש והורד שיר: /getmidi bella ciao", "pianoprofile": "פרופילים או החלפה: /pianoprofile Noa",
    "pianocode": "קוד מסך השיעורים", "showlog": "לוג חי בצ'אט הזה", "stoplog": "עצור את הלוג החי",
}


class TgError(Exception):
    pass


def tg(token, method, data=None, files=None, timeout=40):
    """One Telegram Bot API call. Returns the result, or raises TgError with Telegram's own words."""
    url = f"{API}/bot{token}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, path in files.items():
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{Path(path).name}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n').encode() + Path(path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(data or {}).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode())
        except ValueError:
            raise TgError(f"Telegram answered {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TgError(f"No answer from Telegram: {getattr(e, 'reason', e)}") from None
    if not out.get("ok"):
        raise TgError(out.get("description") or "Telegram refused")
    return out.get("result")


# ---------------------------------------------------------------- talking to the person

def say(text=""):
    print(text, flush=True)


QUIET = False       # --refresh: no questions, every step is taken


def ask(question, default=True):
    """A yes/no question. Enter takes the default. Anything but a clear no counts as the default."""
    if QUIET:
        return True
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {question} {hint} ").strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def manual(title, lines):
    say(f"\n  Do it by hand: {title}")
    for line in lines:
        say(f"    {line}")
    say()


# ---------------------------------------------------------------- the steps

def step_token():
    say("\n  Step 1 of 5: the bot")
    say("  Make the bot yourself, it takes a minute:")
    say("    1. In Telegram, search for @BotFather (the one with the blue check) and open it.")
    say("    2. Send:  /newbot")
    say("    3. It asks for a name (anything, for example Armonico Piano), then a username that")
    say("       ends in 'bot' (for example my_piano_bot).")
    say("    4. It answers with a token that looks like 123456789:AAH... Copy it.")
    say()
    for attempt in range(3):
        try:
            token = getpass.getpass("  Paste the token (nothing is shown while you paste, Enter to finish, empty to skip): ").strip()
        except EOFError:
            return None, None
        if not token:
            return None, None
        try:
            me = tg(token, "getMe")
        except TgError as e:
            say(f"  Telegram does not accept that token: {e}.")
            say("  Copy it again from BotFather, the whole line, with the colon in the middle.")
            continue
        say(f"  OK   The bot is @{me.get('username')} ({me.get('first_name')})")
        return token, me
    return None, None


def profile_steps(token, me):
    say("\n  Step 2 of 5: the bot's profile")
    say("  Through Telegram's API this step sets the description people see before they press Start,")
    say("  the short description, the list of commands (Telegram suggests them while typing),")
    say("  in English and in Hebrew, and the picture (the Armonico logo).")
    if not ask("Set the bot's profile now?"):
        manual("the bot's profile, in a chat with @BotFather", [
            "/setdescription   choose the bot, then paste:",
            f"    {DESCRIPTION}",
            "/setabouttext     choose the bot, then paste:",
            f"    {SHORT}",
            "/setcommands      choose the bot, then paste the whole block below as one message:",
            *[f"    {n} - {d}" for n, d in COMMANDS],
            "/setuserpic       choose the bot, then send the picture:",
            f"    {PHOTO}   (on the machine; copy it to your phone or computer)",
        ])
        return
    todo = [
        ("description", lambda: (tg(token, "setMyDescription", {"description": DESCRIPTION}),
                                 tg(token, "setMyDescription", {"description": DESCRIPTION_HE, "language_code": "he"}))),
        ("short description", lambda: (tg(token, "setMyShortDescription", {"short_description": SHORT}),
                                       tg(token, "setMyShortDescription", {"short_description": SHORT_HE, "language_code": "he"}))),
        ("commands", lambda: (tg(token, "setMyCommands", {"commands": [{"command": n, "description": d} for n, d in COMMANDS]}),
                              tg(token, "setMyCommands", {"commands": [{"command": n, "description": COMMANDS_HE[n]} for n, _ in COMMANDS],
                                                         "language_code": "he"}))),
    ]
    for name, fn in todo:
        try:
            fn()
            say(f"  OK   {name[0].upper()}{name[1:]}: set")
        except TgError as e:
            say(f"  WARN {name[0].upper()}{name[1:]}: not set ({e})")
            if name == "description":
                manual("the description", ["In @BotFather: /setdescription, choose the bot, paste:", f"  {DESCRIPTION}"])
            elif name == "short description":
                manual("the short description", ["In @BotFather: /setabouttext, choose the bot, paste:", f"  {SHORT}"])
            else:
                manual("the commands", ["In @BotFather: /setcommands, choose the bot, paste:",
                                        *[f"  {n} - {d}" for n, d in COMMANDS]])
    if not PHOTO.exists():
        say(f"  WARN The picture file is missing ({PHOTO})")
        return
    # Setting a bot's picture through the API is new in Telegram, and not every bot or version allows it.
    try:
        tg(token, "setMyProfilePhoto", {"photo": json.dumps({"type": "static", "photo": "attach://pic"})}, files={"pic": PHOTO})
        say("  OK   The picture is set")
    except TgError as e:
        say(f"  WARN The picture was not set through the API: {e}")
        manual("the picture", ["In @BotFather: /setuserpic, choose the bot, send the picture file:",
                               f"  {PHOTO}", "  (copy it from the machine to your phone or computer first)"])


def find_chats(token, me):
    say("\n  Step 3 of 5: your chat")
    say("  Home Assistant lets only the chats you name use the bot, so this step needs your chat's number.")
    say("  It is found when you write to the bot:")
    say(f"    Open  https://t.me/{me.get('username')}  and press Start (or send any message).")
    if not ask("Look for your chat now?"):
        manual("your chat's number", [
            f"1. Open https://t.me/{me.get('username')} and press Start.",
            "2. In a browser open:  https://api.telegram.org/bot<TOKEN>/getUpdates   (put the token in place of <TOKEN>)",
            '3. Find  "chat":{"id": 123456789 ...  The number after "id" is the chat number.',
            "   A group's number is negative, for example -1001234567890.",
            "4. Do this before Home Assistant is connected to the bot: once it is, it takes the messages itself.",
        ])
        return []
    try:                                       # a webhook would keep getUpdates empty
        tg(token, "deleteWebhook")
    except TgError:
        pass
    found = {}
    deadline = time.time() + 120
    offset = None
    say("  Waiting for your message (up to 2 minutes, Ctrl+C to stop waiting)...")
    try:
        while time.time() < deadline and not found:
            data = {"timeout": 10, "allowed_updates": ["message"]}
            if offset:
                data["offset"] = offset
            for u in tg(token, "getUpdates", data, timeout=30) or []:
                offset = u["update_id"] + 1
                chat = (u.get("message") or {}).get("chat") or {}
                if chat.get("id") is not None:
                    found[chat["id"]] = chat.get("title") or chat.get("first_name") or str(chat["id"])
    except (TgError, KeyboardInterrupt) as e:
        if isinstance(e, TgError):
            say(f"  WARN {e}")
    if not found:
        say("  No message arrived.")
        manual("your chat's number", [f"Press Start at https://t.me/{me.get('username')}, then open",
                                      "https://api.telegram.org/bot<TOKEN>/getUpdates and read the number after \"chat\":{\"id\":",
                                      "Then run: armonico telegram"])
        return []
    chosen = []
    for cid, name in found.items():
        if ask(f"Allow the chat '{name}' ({cid}) to use the piano?"):
            chosen.append(cid)
    extra = input("  Another chat number to allow (a family member's, a group's), Enter for none: ").strip() if sys.stdin.isatty() else ""
    for part in extra.replace(",", " ").split():
        try:
            chosen.append(int(part))
        except ValueError:
            say(f"  WARN '{part}' is not a number, ignored")
    return chosen


def ha_manual(token, chats):
    ids = ", ".join(str(c) for c in chats) if chats else "<your chat number>"
    manual("connect the bot to Home Assistant", [
        "1. Home Assistant: Settings, Devices & services, Add integration, search 'Telegram bot'.",
        "2. Platform: Polling (it needs no address from outside).",
        "3. API key: the bot's token from BotFather.",
        "4. Submit. Then open the new 'Telegram bot' entry, Add allowed chat ID, and enter each chat number:",
        f"   {ids}",
        "5. Without an allowed chat ID the bot answers nobody, and the piano automations stay quiet.",
    ])


def step_home_assistant(token, chats):
    say("\n  Step 4 of 5: connect the bot to Home Assistant")
    url, ha_token = os.environ.get("HA_URL", ""), os.environ.get("HA_TOKEN", "")
    if not chats:
        say("  No chat to allow yet, so Home Assistant would answer nobody.")
        ha_manual(token, chats)
        return False
    if not ask("Add the bot to Home Assistant, with your chat as the allowed one?"):
        ha_manual(token, chats)
        return False
    import ha_setup
    if not url:
        url = input("  Home Assistant address: ").strip()
    if not ha_token:
        say("  This needs the same kind of token as the installer: Home Assistant, your profile,")
        say("  Security, Long-lived access tokens, Create token.")
        try:
            ha_token = getpass.getpass("  Home Assistant token (nothing is shown): ").strip()
        except EOFError:
            ha_token = ""
    if not (url and ha_token):
        ha_manual(token, chats)
        return False
    try:
        ha = None
        error = None
        for candidate in ha_setup.candidates(url):
            ha = ha_setup.HA(candidate, ha_token)
            try:
                ha.rest("GET", "/api/config")
                break
            except ha_setup.HAError as e:
                error, ha = e, None
        if ha is None:
            raise error
        report = lambda kind, text: say(f"  {'OK  ' if kind == 'ok' else 'WARN'} {text}")
        done, info = ha_setup.telegram_connect(ha, token, chats, endpoint=os.environ.get("TELEGRAM_API_HA"), report=report)
    except ha_setup.HAError as e:
        done, info = False, str(e)
    if done:
        return True
    say(f"  WARN Home Assistant did not take it: {info}")
    ha_manual(token, chats)
    return False


def step_test(token, chats, connected):
    say("\n  Step 5 of 5: a test message")
    if not chats:
        return
    if not ask("Send a test message to the allowed chat(s) now?"):
        say("  Skipped. Try it yourself: write /pianostatus to the bot.")
        return
    text = "Armonico is connected. Try /pianostatus"
    if not connected:
        text = "Hello from Armonico. The bot works, Home Assistant is not connected to it yet."
    for cid in chats:
        try:
            tg(token, "sendMessage", {"chat_id": cid, "text": text})
            say(f"  OK   Sent to {cid}")
        except TgError as e:
            say(f"  WARN Not sent to {cid}: {e}")
            say("       The person has to press Start on the bot first. Bots cannot write first.")


def keep_secrets(**values):
    """Keeps the Telegram token and chats where an update can read them, when the installer was told to."""
    path = os.environ.get("SECRETS_FILE", "")
    if not path:
        return
    lines = []
    try:
        lines = [l for l in Path(path).read_text().splitlines() if l.split("=", 1)[0] not in values]
    except OSError:
        pass
    for k, v in values.items():
        if v and "'" not in str(v):
            lines.append(f"{k}='{v}'")
    tmp = Path(path + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    say(f"  OK   The bot token is kept for updates ({path})")


def refresh():
    """For an update: the bot's description, commands and picture, from the kept token."""
    global QUIET
    QUIET = True
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        say("  WARN No Telegram token to refresh the bot with")
        return 1
    try:
        me = tg(token, "getMe")
    except TgError as e:
        say(f"  WARN Telegram does not accept the kept token: {e}. Run: sudo armonico integrations")
        return 1
    say(f"  OK   The bot is @{me.get('username')}")
    profile_steps(token, me)
    return 0


def main():
    if "--refresh" in sys.argv[1:]:
        return refresh()
    if not sys.stdin.isatty():
        say("  The Telegram setup asks questions: run it in a terminal (armonico telegram).")
        return 2
    say("\n  Telegram (optional)")
    say("  With Telegram you control the piano from your phone: status, play a song, lessons, the log.")
    say("  It needs Home Assistant, which reads the bot's messages and answers them.")
    if not ask("Set Telegram up now?", default=False):
        say("  Skipped. Whenever you want it:  armonico telegram    (guide: docs/telegram.md)")
        return 0
    token, me = step_token()
    if not token:
        manual("making the bot", ["Follow docs/telegram.md, section 1, then run:  armonico telegram"])
        return 0
    profile_steps(token, me)
    chats = find_chats(token, me)
    connected = step_home_assistant(token, chats)
    keep_secrets(TELEGRAM_TOKEN=token, TELEGRAM_CHATS=",".join(str(c) for c in chats or []))
    step_test(token, chats, connected)
    say("\n  Telegram is " + ("ready." if connected else "set up as far as you allowed. What is left is in the lines marked 'Do it by hand' above."))
    if connected:
        say(f"  Open https://t.me/{me.get('username')} and send /pianostatus.")
    token = None
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n  Stopped.")
        sys.exit(130)
