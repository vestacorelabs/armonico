# Configuration: every setting and where it lives

The piano machine knows one thing about the outside world: an MQTT broker. The bridge and the lesson engine publish and subscribe there, and nowhere else. Home Assistant and the Telegram bot are separate programs that connect to the same broker. That is why the setup screen asks only for the broker and the keyboard, and everything about Home Assistant and Telegram is set up inside Home Assistant.

## The map

| Place | What it holds | Changed with |
|---|---|---|
| `/etc/armonico/config.env` on the piano machine | The broker, the keyboard, the ports, the first language, the screen code | The setup screen (`sudo armonico reconfigure`), or by hand and `sudo armonico restart` |
| `/var/lib/armonico/` on the piano machine | What the lesson screen changes: language, pace, profiles, progress, recordings, shortcuts, songs | The lesson screen, Telegram, the `armonico` command, or by hand for `shortcuts.conf` |
| The browser, on each device | Theme, color set, text size, sound on that device, the computer's keys, live mode | The lesson screen's ⚙ settings |
| Home Assistant | The Telegram bot, the automations that answer it, alarm times, reminders, what each shortcut does | Home Assistant's interface and the files from `home-assistant/` |
| Telegram, at @BotFather | The bot's name and the command list shown while typing | BotFather |
| The broker | Users and passwords | Mosquitto's own files, or Home Assistant's users |
| The top of `install.sh` | The technical name, the repository, the ports | Editing `install.sh`, for a fork or a rename |

## 1. `config.env`: the machine

Written by the installer and loaded by the services through systemd (`EnvironmentFile=`). Root and the `armonico` group may read it (`640`), which is what lets the `armonico` command run without `sudo`. Values are in single quotes, so a password with `$` or `"` is safe, and a single quote itself cannot be stored.

| Key | Set by | Default | Read by | Meaning |
|---|---|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | Setup screen, step 1 | none, `1883` | both services | The broker |
| `MQTT_USER`, `MQTT_PASS` | Setup screen, step 1 | empty | both services | The broker login. Empty for a broker without logins |
| `MQTT_LOCAL` | Installer | `no` | the setup | `yes` when the installer set up Mosquitto on the machine (option 2). The setup then fills the broker in by itself and does not ask for it |
| `MIDI_DEVICE` | Setup screen, step 2 | none | both services | The keyboard's ALSA client name, as `aconnect -i` shows it |
| `KEY_LOWEST`, `KEY_HIGHEST` | Setup screen, step 2 (measured) | `36`, `96` | lesson engine | The keyboard's range as MIDI notes. Middle C is 60 |
| `STOP_KEY` | Setup screen, step 2 (the lowest key) | `36` | bridge | The key that stops the alarm and anything playing |
| `UI_LANG` | The terminal setup (it asks), `en` from the setup screen | `en` | lesson engine, bridge | `en` or `he`, the language of the Telegram answers until the lesson screen changes it. A lesson screen always starts in English on a device that has not chosen a language; the choice is kept in that browser and saved here. After the first change on the lesson screen, `settings.json` decides |
| `LESSON_HOSTS` | Setup screen, step 3 | empty | lesson engine | Extra host names the lesson screen answers to, comma separated, for a reverse proxy or a local DNS name |
| `DATA_DIR`, `SONGS_DIR` | Installer | `/var/lib/armonico`, `…/songs` | both services | Data and songs folders. The services may write only inside `/var/lib/armonico` (`ReadWritePaths` in the unit), so a folder elsewhere also needs `sudo systemctl edit armonico-lessons` and the same for the bridge |
| `LESSON_UI_PORT` | Installer | `8099` | lesson engine | Port of the lesson screen |
| `LESSON_BIND` | By hand only | empty (every address) | lesson engine | The address the lesson screen listens on. Empty means every network interface, which is what phones and other devices on the network need. `127.0.0.1` makes it reachable from this machine only; a fixed address of this machine limits it to one network card, and stops working when that address changes |
| `LESSON_SHOW_KEY`, `LESSON_EXIT_KEY` | By hand only | first and third black key from the bottom | lesson engine | The keys that show the numbers again and end a lesson, as MIDI notes |
| `SCREEN_CODE` | The terminal setup (the setup screen always sets `on`) | `on` | lesson engine | The first answer only. From then on the switch lives in the screen's settings and in `armonico code on` / `off`, which write `/var/lib/armonico/code_enabled`; that file decides. With it on, a device enters a 6-digit code once and keeps a signed pass for 14 days, renewed on every use. Reads are behind it too, so the history and the backup are not open. The code is shown by `armonico code`, `/pianocode` in Telegram, and at the end of the install. After 5 wrong codes in a minute, an address waits a minute. "Sign every screen out" in the settings replaces the signing key and ends every pass at once |
| `MQTT_ADMIN_SECRET` | Written by the install | drawn at install | lesson engine, `armonico` | The proof carried by the three commands that hand out the screen code, open the editing tools, or change the active profile. Without it those three are refused, whoever published them. It exists because the broker cannot be relied on to say who may publish what, and Home Assistant's Mosquitto add-on does not enforce topic rules at all in series 7 |
| `MQTT_REC_USER`, `MQTT_REC_PASS` | Written by the install, local broker only | drawn at install | MQTT recorder | The recorder's own login: it may read every topic and write to none. Empty on a broker this project did not install, where the recorder falls back to the piano's login |
| `UPDATE_REQUIRE_CHECKSUM` | By hand | `yes` | update script | A release is checked against the `SHA256SUMS` published with it before anything in it runs. `no` installs a release that published no checksum, which means running code from the internet as root without checking what it is |
| `MQTT_RECORD_DAYS` | By hand only | `14` | MQTT recorder | Days the recorder keeps its files, when it is on (`sudo armonico record on`) |

### Changing it

| Change | How |
|---|---|
| Broker, keyboard, language, host names, screen code | `sudo armonico reconfigure` opens the setup screen again with the current values filled in. `sudo armonico reconfigure --cli` asks the same questions in the terminal |
| Any key | Editing the file, then `sudo armonico restart` |

The setup rewrites the file on save. Keys it does not ask about, such as `LESSON_SHOW_KEY` or `MQTT_RECORD_DAYS`, are copied back as they were. An update (`sudo armonico update`, "Install" on the lesson screen, or `sudo ./install.sh` on an installed machine) never touches the file.

## 2. `/var/lib/armonico/`: what the lesson screen changes

Owned by the service user. "Download a backup" on the lesson screen puts the signed-in profile (its progress, history, settings and recordings) and the songs in one zip.

| File | Holds | Changed by |
|---|---|---|
| `settings.json` | The piano's language, and the first profile's pace, tempo, key colors and labels. The bridge reads the language here too | Lesson screen: the flag menu, the Today card, the toolbar above the keyboard |
| `profiles.json` | Every profile, the one at the piano now, and each PIN as a salted hash (`600`) | Lesson screen: the round button in the header; Telegram: `/pianoprofile` |
| `profiles/<id>/` | Everything of one profile except the first: `lessons.json`, `history.json`, `settings.json`, `recordings/` | Lessons played in that profile |
| `lessons.json`, `history.json`, `recordings/` | The first profile's progress, lesson history and the last 30 recordings | Lessons |
| `volume` | The master volume set on the screen | The volume slider |
| `shortcuts.conf` | Shortcuts, one per line: the keys as MIDI notes, then `\|` and the name, as in `60 62 64\|good_night` | Telegram (`/seqlearn`, `/seqadd`, `/seqdel`), `armonico shortcut`, or by hand, since the bridge reloads the file when it changes |
| `songs/` | MIDI files, and `<song>.credit.json` next to a downloaded one | Uploads and the archive search on the screen, `/getmidi`, or copying files in |
| `last_chat` | The Telegram chat a lesson started from the screen reports to | The last `/lesson` from Telegram |
| `screen_code` | The code of the lesson screen. Not in the backup | Made by the install, or on first use |
| `screen_secret` | The key the screens' passes are signed with. Replacing it ends every pass. Not in the backup | Made on first use |
| `code_enabled` | Whether screens are asked for the code, once the setting or the terminal has said so | Written when the switch is used |
| `text_defaults.json` (in `/opt/armonico/lessons/`) | The wording a release carries, so a fresh install looks the way the project was written. Written by `armonico text export` and committed; never edited on a running machine, since an update replaces it | Whatever the release shipped |
| `text_overrides.json` | Wording edited on this screen. It wins over what the release shipped, line by line, so an update never overwrites it | Written by the screen’s editing tools |
| `mqtt/` | The MQTT recorder's files, one per day, when it is on. Not in the backup | `sudo armonico record on` |

### What belongs to a profile, and what to the piano

| Per profile | For the whole piano |
|---|---|
| Progress, history, recordings | Songs |
| Pace, auto tempo, demo speed | Shortcuts |
| Language (it comes back when switching to the profile) | Volume |
| Key colors and labels (the toolbar above the keyboard) | The broker and the keyboard (`config.env`) |

## 3. The browser: each device on its own

Kept in the browser's local storage, so a phone and a laptop can differ. None of it reaches the piano.

| Setting | Where on the screen |
|---|---|
| Dark or light, the color set and the voice below follow the profile: the choice is saved on the piano for the active profile and appears on every device that opens it | |
| Dark or light | ☾ / ☀ in the header, or ⚙ |
| The color set: Vesta, Ember, Forest, Slate or Plum. It moves the colors of the screen, and leaves the banner, the key colors and the gold key as they are | ⚙ |
| Text size | A− and A+ in the header, or ⚙ |
| Sound on this device | "Computer sound" above the keyboard, or ⚙ |
| The voice of that sound: grand piano (recorded), electric piano, organ, music box, soft synth | ⚙ |
| Playing with the computer's keys | ⚙ |
| Live mode | "Live" above the keyboard, or ⚙ |

## 4. Home Assistant

Nothing here is needed for the lesson screen, playback over MQTT or an alarm from cron ([without Home Assistant](installation.md#without-home-assistant)). With it:

| Item | Where | What is filled in |
|---|---|---|
| MQTT integration | Settings, Devices & services | The same broker as in `config.env`, with a user of its own |
| Sensors, helpers, automations, blueprints | Added by the installer from an access token (see [home-assistant.md](home-assistant.md#the-short-way-one-token)), or by hand as the package `config/packages/piano.yaml` from `home-assistant/piano.yaml` | Nothing. It brings 28 sensors under a device named Piano, 3 helpers and 5 automations, all named `piano_…`, and 3 blueprints under `piano/` |
| Telegram bot integration | Settings, Devices & services | The token from BotFather and the allowed chat IDs. The token lives only here, never on the piano machine. See [telegram.md](telegram.md) |
| Live log chat | The helper `input_text.piano_log_chat_id` | Filled by `/showlog` in a chat, cleared by `/stoplog` |
| Alarm clock | An automation from the **Piano alarm clock** blueprint | Time, days, song, minutes between retries |
| Shortcut actions | An automation from the **Piano shortcut** blueprint, one per shortcut | The shortcut's name and the actions |
| Practice reminder | An automation from the **Piano practice reminder** blueprint | Time, days, whether to wait for parts due, and the action that sends `{{ message }}` |

The full steps, and how to add all this to a Home Assistant that is already in use without breaking it, are in [home-assistant.md](home-assistant.md).

## 5. Telegram

The bot's token lives in Home Assistant. It is also kept in `/etc/armonico/secrets.env` (root only) when you chose, at install, to keep tokens for updates; see [installation.md](installation.md#update).

| Item | Where |
|---|---|
| Bot name, user name, token | @BotFather, `/newbot` |
| The list of commands shown while typing | @BotFather, `/setcommands`, with the list from [telegram.md](telegram.md#4-the-bots-profile-by-hand) |
| Who may use the bot | "Allowed chat IDs" in Home Assistant's Telegram bot integration |
| The language of the answers | The lesson screen's language (`piano/lang`) |

## 6. The broker

| Setup | Users live in |
|---|---|
| Mosquitto installed by the installer (option 2) | `/etc/mosquitto/armonico.passwd`, user `piano`; the password is `MQTT_PASS` in `config.env` |
| Home Assistant's Mosquitto broker app | Home Assistant's own users (Settings, People, Users) |
| Any other broker | Its own configuration |

## 7. `install.sh`: names and ports

The first lines of `install.sh` hold the values a fork or a rename changes: `APP` (the technical name, used for the folders, the services, the user and the command), `APP_TITLE`, `REPO` (where updates come from), `SETUP_PORT` (8098) and `LESSON_UI_PORT` (8099). This documentation uses the default name, `armonico`, so with another `APP` every `armonico` in a path, a service or a command becomes that name.
