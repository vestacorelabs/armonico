# Telegram bot

The Telegram bot is the piano's remote control: status, songs, shortcuts, lessons and a live log. It runs through the Telegram bot integration in Home Assistant, so the piano machine itself needs no Telegram setup. The same commands also exist on the machine as the [`armonico` command](commands.md#the-armonico-command).

## The short way: `armonico telegram`

The installer offers it at the end of the Home Assistant step, and `armonico telegram` runs it any time. You make the bot yourself in Telegram, then it carries on from the token. **It asks before every step**, and for a step you decline, or one that fails, it prints the manual way, the same as sections 1 to 4 below.

| Step | What it does | If you decline it |
|---|---|---|
| 1. The bot | Tells you what to send to @BotFather, reads the token with no echo, checks it with Telegram | [Section 1](#1-creating-the-bot) |
| 2. The profile | Sets the description, the short description and the 17 commands, in English and in Hebrew, and the picture (the Armonico logo) | [Section 4](#4-the-bots-profile-by-hand) |
| 3. Your chat | You press Start on the bot, it finds your chat number and asks which chats may use the piano | [Section 2](#2-finding-the-chat-id) |
| 4. Home Assistant | Adds the Telegram bot integration (Polling) and the allowed chats, over the same token as the installer | [Section 3](#3-connecting-it-to-home-assistant) |
| 5. A test message | Sends one message to each allowed chat | Write `/pianostatus` to the bot |

The bot's token goes only to Telegram and to Home Assistant, and is not written to a file unless you chose, at install, to keep tokens for updates. Then it is stored in `/etc/armonico/secrets.env` (root only) and updates refresh the bot's description, commands and picture; `sudo armonico integrations` does the same on demand. Setting the bot's picture through Telegram's API is new and not every bot allows it: when it is refused, the picture is the one manual step (`/setuserpic` in BotFather, with `setup/bot-photo.jpg`). Bots cannot write first: the test message reaches only a chat that pressed Start.

## 1. Creating the bot

1. `/newbot` in a chat with **@BotFather** in Telegram starts it.
2. BotFather asks for a display name, and for a user name that ends in `bot`.
3. It answers with a **token**. The token gives full control of the bot, so it stays private.

## 2. Finding the chat ID

1. The new bot needs one message from the chat first.
2. `https://api.telegram.org/bot<token>/getUpdates`, opened in a browser, then lists that message.
3. The number in `"chat":{"id":...}` is the chat ID. A group's ID starts with a minus sign, which is part of it.

This step comes before the bot is connected to Home Assistant. Once Home Assistant polls the bot, it takes every message first, and `getUpdates` answers with an empty list or a `409 Conflict` error. Home Assistant also logs the chat ID of any message from a chat that is not allowed, which is another way to find it.

## 3. Connecting it to Home Assistant

Settings, Devices & services, Add integration, **Telegram bot**:

- Platform: **Polling** (works without exposing Home Assistant to the internet).
- API key: the token from BotFather.
- Allowed chat IDs: the chat ID from step 2. Messages from any other chat are ignored, and that is what keeps strangers away from the piano.

On older Home Assistant versions the integration is set up in `configuration.yaml` instead:

```yaml
telegram_bot:
  - platform: polling
    api_key: !secret telegram_token
    allowed_chat_ids:
      - 123456789
```

The automations in the [Home Assistant package](home-assistant.md) react to the commands and send the answers back.

## 4. The bot's profile, by hand

All in a chat with @BotFather: send the command, choose the bot, then send the text.

| Command | Send |
|---|---|
| `/setdescription` | `Your piano on Telegram. Check the keyboard, play a song, run a lesson, manage shortcuts and follow the log, from anywhere.` |
| `/setabouttext` | `Armonico: your piano on Telegram` |
| `/setuserpic` | The picture `setup/bot-photo.jpg` (512 x 512, copy it from the machine to the phone or computer) |
| `/setcommands` | The block below, as one message. |

With the commands registered, Telegram suggests them while typing. The block:

```
pianostatus - Keyboard, player and alarm status
pianoplay - Play a song: /pianoplay ode_to_joy
pianosongs - List the songs, optionally filtered
pianostop - Stop the alarm or the song
seqlist - List the shortcuts
seqlearn - Learn a shortcut by playing it: /seqlearn name
seqadd - Add a shortcut by numbers: /seqadd name 60 62 64
seqdel - Delete a shortcut: /seqdel name
seqcancel - Cancel learning
lesson - Today's lesson: /lesson ode_to_joy
lessons - Songs and progress
lessonstop - Stop the lesson and save
getmidi - Search and download a song: /getmidi bella ciao
pianoprofile - Profiles, or switch: /pianoprofile Noa
pianocode - The lesson screen code
showlog - Live log in this chat
stoplog - Stop the live log
```

## Commands

| Command | What it does |
|---|---|
| `/pianostatus` | Connection, what is playing and who started it, alarm, last shortcut |
| `/pianoplay <song>` | Plays a song from the songs folder. Refused while the alarm, another song or a lesson is running |
| `/pianosongs [filter]` | Lists the songs, or the ones whose name contains the filter |
| `/pianostop` | Stops the alarm or the song |
| `/seqlearn <name>` | Learning mode: play the sequence on the keyboard, it is saved after 5 seconds of silence |
| `/seqadd <name> <notes>` | Saves a shortcut from MIDI note numbers |
| `/seqdel <name>` | Deletes a shortcut |
| `/seqlist` | Lists the shortcuts |
| `/seqcancel` | Cancels learning mode |
| `/lesson <song> [reset]` | Starts today's lesson. `reset` starts the song over |
| `/lessons` | Songs, and how much of each is learned |
| `/lessonstop` | Stops the lesson and saves the progress |
| `/getmidi <name>` | Searches free MIDI archives, see [sources.md](sources.md). `/getmidi <number>` downloads a result into the songs folder, with its credit |
| `/pianoprofile [name]` | Lists the profiles, or switches to one. A profile with a PIN is switched on the lesson screen only, so the PIN never goes into a chat |
| `/pianocode` | The code a lesson screen asks for once on each device |
| `/showlog`, `/stoplog` | Turns the live log in this chat on and off |

## Language

The command menu and the bot's description are shown by Telegram in each person's app language, English when the bot has nothing for it. Telegram has no way to follow the piano's own language setting: `armonico telegram` registers English and Hebrew, and each person sees the one that matches their Telegram app.

The answers come in English until the language is changed on the lesson screen (the flag menu), and in the language chosen there after that, English or Hebrew. Hebrew lines start with a right-to-left mark and end with their emoji, and runs of key numbers keep their left-to-right order. The `/pianostatus` answer and the live log switches come from Home Assistant, which reads the language from the `Piano Language` sensor (`piano/lang`). Command names stay in English.

## Shortcuts

A shortcut is a short melody that triggers an automation, much like a keyboard shortcut. A sequence of 1 to 3 keys is recognized after 2 seconds of silence, so single keys and short pairs do not fire in the middle of playing. A longer sequence is recognized the moment its last note is played, even inside a longer piece.

While a lesson screen is open and in view on any device, or a lesson runs, the keys trigger no shortcuts and `/seqlearn` refuses to start, so playing on the screen never sets off an automation. The stop key keeps working. Shortcuts come back about 15 seconds after the last screen is closed or hidden.

Each shortcut publishes its name to `piano/shortcut`. The **Piano shortcut** blueprint turns a shortcut into any Home Assistant action.
