"""telegram.py - the Telegram bot, read by the app itself, for a phone with no Home Assistant.

The same commands and the same answers as the Telegram automation in home-assistant/piano.yaml.
The bot answers one chat only: the one that pressed Start while the app was waiting for it
("Connect my chat" in the settings). Everyone else is ignored.

A command from here reaches the bridge as if it came over MQTT, with the chat written as
app-<chat id>. The bridge answers such a chat through this bot and never publishes the answer,
the way it treats cli- chats of the armonico command, so a Home Assistant bot on the same broker
never sees it.

One token is read from one place only: Telegram hands each update to one reader. A bot that Home
Assistant already reads needs a token of its own here.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.telegram.org"
PREFIX = "app-"

COMMANDS = [
    ("pianostatus", "Keyboard, player and alarm status", "מצב המקלדת, הנגן וההתראה"),
    ("pianoplay", "Play a song: /pianoplay ode_to_joy", "נגן שיר: /pianoplay ode_to_joy"),
    ("pianosongs", "List the songs, optionally filtered", "רשימת השירים, אפשר לסנן"),
    ("pianostop", "Stop the alarm or the song", "עצור את ההתראה או השיר"),
    ("pianoreset", "Full reset: unstick, the keyboard opened again, the broker again", "איפוס מלא: שחרור, פתיחה מחדש של המקלדת, ושוב לברוקר"),
    ("seqlist", "List the shortcuts", "רשימת הקיצורים"),
    ("seqlearn", "Learn a shortcut by playing it: /seqlearn name", "למד קיצור בנגינה: /seqlearn name"),
    ("seqadd", "Add a shortcut by numbers: /seqadd name 60 62 64", "הוסף קיצור במספרים: /seqadd name 60 62 64"),
    ("seqdel", "Delete a shortcut: /seqdel name", "מחק קיצור: /seqdel name"),
    ("seqcancel", "Cancel learning", "בטל למידה"),
    ("lesson", "Today's lesson: /lesson ode_to_joy", "השיעור של היום: /lesson ode_to_joy"),
    ("lessons", "Songs and progress", "שירים והתקדמות"),
    ("lessonstop", "Stop the lesson and save", "עצור את השיעור ושמור"),
    ("getmidi", "Search and download a song: /getmidi bella ciao", "חפש והורד שיר: /getmidi bella ciao"),
    ("pianoprofile", "Profiles, or switch: /pianoprofile Noa", "פרופילים או החלפה: /pianoprofile Noa"),
    ("pianocode", "The lesson screen code", "קוד מסך השיעורים"),
    ("showlog", "Live log in this chat", "לוג חי בצ'אט הזה"),
    ("stoplog", "Stop the live log", "עצור את הלוג החי"),
]
LESSON_COMMANDS = ("/lesson", "/lessons", "/lessonstop", "/getmidi", "/pianoprofile", "/pianocode")


class TgError(Exception):
    pass


def call(token, method, data=None, timeout=40):
    req = urllib.request.Request(f"{API}/bot{token}/{method}", json.dumps(data or {}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode())
        except ValueError:
            raise TgError(f"Telegram answered {e.code}") from None
        out.setdefault("error_code", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TgError(f"No answer from Telegram: {getattr(e, 'reason', e)}") from None
    if not out.get("ok"):
        err = TgError(out.get("description") or "Telegram refused")
        err.code = out.get("error_code")
        raise err
    return out.get("result")


def upload(token, method, fields, name, filename, data, timeout=40):
    """A multipart request, for a file: the bot's picture."""
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
             "Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{API}/bot{token}/{method}", body,
                                 {"Content-Type": f"multipart/form-data; boundary={boundary}"})
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


def setup_texts(engine_dir):
    """DESCRIPTION, SHORT, DESCRIPTION_HE and SHORT_HE of setup/telegram_setup.py, read as text and
    never run: that file is the Linux setup's, with its own questions."""
    import ast
    out = {}
    try:
        with open(os.path.join(engine_dir, "setup", "telegram_setup.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
                if key in ("DESCRIPTION", "SHORT", "DESCRIPTION_HE", "SHORT_HE"):
                    out[key] = ast.literal_eval(node.value)
    except Exception:
        pass
    return out


def nice(name):
    import re
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    return s.replace("_", " ").strip()


class Bot:
    def __init__(self, bridge, token, state_file):
        self.bridge = bridge
        self.token = token
        self.state_file = state_file
        self.owner = None
        self.username = ""
        self.pairing_until = 0.0
        self.offset = None
        self.running = False
        self.last_error = ""
        self.connected = False
        self._load()

    def _profile_key(self):
        # a new profile (the description and the picture were added) is sent once more to a bot set up before
        return self.token[-6:] + ":2"

    # ---------------------------------------------------------------- state kept on disk
    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                s = json.load(f)
            if s.get("token_tail") == self.token[-6:]:
                self.owner = s.get("owner")
                self.username = s.get("username", "")
                self._profile_done = s.get("profile") == self._profile_key()
            else:
                self._profile_done = False
        except Exception:
            self._profile_done = False

    def _save(self):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"token_tail": self.token[-6:], "owner": self.owner, "username": self.username,
                           "profile": self._profile_key() if self._profile_done else ""}, f)
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    # ---------------------------------------------------------------- life cycle
    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True, name="armonico-telegram").start()

    def stop(self):
        self.running = False

    def pair(self, seconds=120):
        """The next /start within the time becomes the one chat the bot answers."""
        self.pairing_until = time.time() + seconds
        self.bridge.log(f"🔗 Telegram: waiting {seconds} seconds for /start")

    def info(self):
        return {"on": True, "connected": self.connected, "username": self.username,
                "paired": self.owner is not None, "owner": self.owner, "pairing": time.time() < self.pairing_until,
                "error": self.last_error}

    def _run(self):
        while self.running:
            try:
                me = call(self.token, "getMe", timeout=20)
                self.username = me.get("username", "")
                break
            except TgError as e:
                self._fail(str(e))
                if getattr(e, "code", None) == 401:     # a wrong token does not get better by waiting
                    return
                time.sleep(15)
        self.connected = True
        self.last_error = ""
        self.bridge.log(f"✅ Telegram bot @{self.username} running")
        if not self._profile_done:
            self._profile()
        self._save()
        conflict_said = False
        while self.running:
            try:
                data = {"timeout": 30, "allowed_updates": ["message"]}
                if self.offset is not None:
                    data["offset"] = self.offset
                updates = call(self.token, "getUpdates", data, timeout=45)
                self.connected = True
                conflict_said = False
                for u in updates:
                    self.offset = u["update_id"] + 1
                    try:
                        self._update(u)
                    except Exception as e:
                        self.bridge.log(f"⚠️ Telegram: {type(e).__name__}: {e}")
            except TgError as e:
                if getattr(e, "code", None) == 409:
                    # another reader of the same token (Home Assistant, most likely)
                    if not conflict_said:
                        self._fail("Another program reads this bot. One token is read from one place only")
                        conflict_said = True
                    time.sleep(30)
                else:
                    self._fail(str(e))
                    time.sleep(10)

    def _fail(self, why):
        self.connected = False
        if why != self.last_error:
            self.last_error = why
            self.bridge.log(f"⚠️ Telegram: {why}")
        self.bridge._status()

    def _profile(self):
        """What setup/telegram_setup.py gives the bot on Linux: the command list Telegram suggests while
        typing, the description, the short description (both in English and in Hebrew) and the picture.
        The texts and the picture are read from that file in the engine's folder, so they exist once."""
        try:
            call(self.token, "setMyCommands", {"commands": [{"command": c, "description": en} for c, en, _ in COMMANDS]})
            call(self.token, "setMyCommands", {"commands": [{"command": c, "description": he} for c, _, he in COMMANDS],
                                               "language_code": "he"})
        except TgError as e:
            self.bridge.log(f"⚠️ Telegram: the command list was not set: {e}")
            return
        texts = setup_texts(self.bridge.cfg.get("engine_dir", ""))
        try:
            if texts.get("DESCRIPTION"):
                call(self.token, "setMyDescription", {"description": texts["DESCRIPTION"]})
                call(self.token, "setMyDescription", {"description": texts.get("DESCRIPTION_HE", ""), "language_code": "he"})
            if texts.get("SHORT"):
                call(self.token, "setMyShortDescription", {"short_description": texts["SHORT"]})
                call(self.token, "setMyShortDescription", {"short_description": texts.get("SHORT_HE", ""), "language_code": "he"})
        except TgError as e:
            self.bridge.log(f"⚠️ Telegram: the description was not set: {e}")
        photo = os.path.join(self.bridge.cfg.get("engine_dir", ""), "setup", "bot-photo.jpg")
        if os.path.exists(photo):
            # new in Telegram's API, and not every bot allows it: then BotFather's /setuserpic, as on Linux
            try:
                with open(photo, "rb") as f:
                    upload(self.token, "setMyProfilePhoto",
                           {"photo": json.dumps({"type": "static", "photo": "attach://pic"})}, "pic", "bot-photo.jpg", f.read())
            except TgError as e:
                self.bridge.log(f"⚠️ Telegram: the picture was not set ({e}). BotFather's /setuserpic sets it by hand")
        self._profile_done = True

    # ---------------------------------------------------------------- sending
    def send(self, chat, text):
        for i in range(0, max(len(text), 1), 4000):         # Telegram takes up to 4096 characters
            try:
                call(self.token, "sendMessage", {"chat_id": int(chat), "text": text[i:i + 4000]}, timeout=20)
            except (TgError, ValueError) as e:
                self.bridge.log(f"⚠️ Telegram: a message was not sent: {e}")
                return

    def send_async(self, chat, text):
        threading.Thread(target=self.send, args=(chat, text), daemon=True).start()

    # ---------------------------------------------------------------- receiving
    def _update(self, u):
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat is None or not text.startswith("/"):
            return
        word, _, args = text.partition(" ")
        cmd = word.split("@", 1)[0].lower()
        args = args.strip()
        b = self.bridge
        if cmd == "/start" and time.time() < self.pairing_until:
            self.owner = chat
            self.pairing_until = 0
            self._save()
            b.log(f"🔗 Telegram: chat {chat} connected")
            b.reply(PREFIX + str(chat), b.L("✅ This chat is connected to Armonico. The commands: /pianostatus",
                                            "✅ הצ'אט הזה מחובר ל-Armonico. הפקודות: /pianostatus"))
            b._status()
            return
        if self.owner is None or chat != self.owner:
            return                                          # the bot answers its one chat only
        app_chat = PREFIX + str(chat)
        if cmd == "/start":
            b.reply(app_chat, self._help())
        elif cmd == "/pianostatus":
            b.reply(app_chat, b.status_text())
        elif cmd == "/pianostop":
            b.events.put(("stop", "Telegram"))
            b.reply(app_chat, b.L("⏹️ Stop sent", "⏹️ נשלחה עצירה"))
        elif cmd in ("/pianoplay", "/pianosongs"):
            b.events.put(("mqtt", "piano/shortcut/cmd", f"{'play' if cmd == '/pianoplay' else 'songs'}|{app_chat}|{args}"))
        elif cmd == "/pianoreset":
            b.events.put(("mqtt", "piano/shortcut/cmd", f"reset|{app_chat}|secret:{b.admin_secret}"))
        elif cmd in ("/seqlist", "/seqadd", "/seqdel", "/seqlearn", "/seqcancel"):
            b.events.put(("mqtt", "piano/shortcut/cmd", f"{cmd[4:]}|{app_chat}|{args}"))
        elif cmd == "/showlog":
            b.events.put(("live_log", True))
            b.reply(app_chat, b.L("🟢 Live log on", "🟢 הלוג החי פועל"))
        elif cmd == "/stoplog":
            b.events.put(("live_log", False))
            b.reply(app_chat, b.L("🔴 Live log off", "🔴 הלוג החי כבוי"))
        elif cmd in LESSON_COMMANDS:
            # the lesson engine takes these on the broker and answers there; the bridge passes the answer on
            from . import lessons
            if not lessons.running():
                b.handle_game_cmd(f"x|{app_chat}|")
            elif cmd == "/pianoprofile":
                b.pub("piano/game/cmd", f"profile|{app_chat}|secret:{b.admin_secret} {args}")
            elif cmd == "/pianocode":
                b.pub("piano/game/cmd", f"code|{app_chat}|secret:{b.admin_secret}")
            else:
                b.pub("piano/game/cmd", f"{cmd[1:]}|{app_chat}|{args}")
        else:
            b.reply(app_chat, self._help())

    def _help(self):
        he = self.bridge.lang == "he"
        lines = ["🎹 הפקודות:" if he else "🎹 Commands:"]
        lines += [f"/{c} · {h if he else en}" for c, en, h in COMMANDS]
        return "\n".join(lines)
