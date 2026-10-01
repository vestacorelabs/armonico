# Terminal commands

Every command Armonico has in a terminal. They all run on the machine the keyboard is connected to, except the MQTT ones, which work from any machine that can reach the broker. The Telegram commands are in [telegram.md](telegram.md).

## The `armonico` command

Installed as `/usr/local/bin/armonico`. `armonico help` shows the logo and every command.

The piano commands travel over MQTT the same way the Telegram commands do, and the answers come back to the terminal instead of a chat. They need the MQTT login from `/etc/armonico/config.env`, which root and members of the `armonico` group may read. The installer adds the user who ran it with `sudo` to that group, from the next login. Another user is added with `sudo usermod -aG armonico <user>`. Anyone in the group can also read the MQTT password.

| Command | What it does |
|---|---|
| `armonico status` | Services, keyboard, player, alarm, lesson and today's practice |
| `armonico check` | The full check's automatic part, as Settings › System › Device test runs it on the Armonico Pi, without the guided steps: every check with its result and, for a warning or a failure, what to do. Saves the same report in `/var/lib/armonico/device/checks/` and exits with 1 when something failed. With `sudo` it also reads the system and kernel logs and whether a Telegram token is kept |
| `armonico check --deep` | The deep check, on any install: each service over its whole uptime and over 30 seconds live, the boot stages, the system, storage, audio dropouts and the logs of the last 24 hours, with the history the sampler keeps for 7 days. Saves the report as text and JSON in `/var/lib/armonico/checks/` (the last 10 are kept) and exits with 1 when a problem was found. [Details](power-loss-and-deep-check.md#deep-check) |
| `armonico play <song>`, `armonico songs [filter]`, `armonico stop` | Like `/pianoplay`, `/pianosongs` and `/pianostop`. A song started here shows `Terminal` as its source |
| `armonico shortcut list`, `learn <name>`, `add <name> <notes>`, `del <name>`, `cancel` | Like the `/seq...` commands. `learn` waits for the result, and Ctrl+C cancels the learning |
| `armonico lesson list`, `start <song> [reset]`, `stop` | Like `/lessons`, `/lesson` and `/lessonstop` |
| `armonico getmidi <name>`, `armonico getmidi <number>` | Like `/getmidi` |
| `armonico profile [name]` | Like `/pianoprofile` |
| `armonico code` | Like `/pianocode`: the code a lesson screen asks for |
| `armonico code on` / `off` | Whether a screen is asked for the code at all. Off means every device on the network can use the lesson screen, read the practice history and download a backup |
| `armonico text export` | The wording edited on this screen, in the form a release carries: `armonico text export > ~/armonico/lessons/text_defaults.json`. Commit that file and the next release looks the way this screen looks |
| `armonico integrations` | Refreshes the Home Assistant parts and the Telegram bot's profile after an update, with the tokens kept at install, or with new ones it asks for |
| `armonico forget-tokens` | Deletes the tokens kept for those updates |
| `armonico code new` | Draw a new code. Screens already signed in stay in; to end those, use "Sign every screen out" in the screen's settings |

The system commands need `sudo`:

| Command | What it does |
|---|---|
| `sudo armonico update [version]` | Installs the newest release, or the one given, and shows the progress. The same as "Install" on the lesson screen |
| `sudo armonico restart` | Restarts the bridge and the lesson engine, the recorder when it is on, and on the Armonico Pi the keys and the screen (`armonico-device`, `armonico-screen`) |
| `armonico logs [lessons] [-f]` | The last 50 lines of the bridge's log, or the lesson engine's. `-f` follows it. Without `sudo` for members of the `adm` or `systemd-journal` group |
| `sudo armonico unstick` | Brings a connected but silent keyboard back, see `piano_unstick.sh` below |
| `sudo armonico reset` | The full reset, like `/pianoreset` in Telegram: stops the services, runs unstick, switches the keyboard's USB device off and on, restarts the MQTT broker (when the installer made it), then starts the bridge and the lesson engine and checks they run. Runs `/opt/armonico/bridge/piano_reset.sh`, the log is `reset.log` in the data folder |
| `sudo armonico reconfigure [--cli]` | Runs the setup again |
| `armonico telegram` | Sets the Telegram bot up: description, commands, picture, your chat, Home Assistant. A question before each step, and the manual way for any step you decline |
| `sudo armonico uninstall`, `sudo armonico purge [--yes]` | The same as `install.sh --uninstall` and `--purge`. A copy of the installer is kept in `/opt/armonico/` for this, so no copy of the repository is needed. `uninstall` already takes the settings, the songs, the progress and the retained messages on the broker with it, so what `--purge` adds afterwards is the system packages and Mosquitto, from a copy of the project (and the retained messages, when the broker did not answer during `uninstall`) |
| `sudo armonico record on`, `sudo armonico record off` | Records every MQTT message on the broker, of every topic, see below |
| `sudo armonico device-mode [status]` | Show the current connection and saved profiles. |
| `sudo armonico device-mode setup-external` | Guided one-time setup for an existing MQTT broker: asks for connection details, hides the password, tests the login, saves it and switches to it (the services restart). |
| `sudo armonico device-mode local-broker` / `external-broker` | Switch to the broker on this Pi or the saved broker on the home network. Saves the current profile and restarts Armonico services. See [hardware.md](hardware.md). |
| `armonico screen-rec on`, `off` | Armonico Pi: records the screen to a file, with the device's sound and a USB microphone on tracks of their own; `off` closes the file and moves it to a USB stick when one is plugged in. The same as Settings › System › Screen recording. See [hardware.md](hardware.md#screen-recording) |
| `sudo armonico screen-rec export`, `list` | Moves the recordings left on the SD card to a USB stick; lists them |
| `armonico record` | Whether the recorder is on, its folder, how many days and how much space |
| `armonico record follow [filter]` | Today's file live, like `tail -f`. With a filter, only the lines that contain it, for example `armonico record follow piano/game` |
| `armonico version` | The installed version |

### Recording MQTT: `armonico record`

The recorder is an optional third service, `armonico-recorder`, installed with the others and off until `sudo armonico record on`. It subscribes to `#`, so it records every topic on the broker, not only `piano/`: Home Assistant, the bridge, the lesson engine and anything else that uses the same broker. It runs on the piano machine and writes there, one file per day:

```
/var/lib/armonico/mqtt/2026-09-29.log
```

One line per message, in the order the broker passed them on:

```
2026-09-29 07:00:00.112  -  piano/alarm/set  wake_up|Alarm clock
2026-09-29 07:00:00.140  -  piano/play/status  playing
2026-09-29 07:01:12.903  -  piano/alarm/stopped_by  Stop key (C2)
2026-09-29 07:05:41.018  R  piano/status  online
```

`R` marks a retained message that the broker handed over when the recorder connected, a dash a live one. Line breaks inside a payload are written as `\n`, so every message stays on one line and `grep` finds it. Binary payloads are written in hex, and a payload over 16 KB is cut with a note of its full size. Lines that start with `#` are the recorder's own: connected, disconnected, stopped.

A `secret:<value>` in a payload (the admin secret that guarded commands carry) is written as `secret:***`.

Files older than 14 days are deleted. `MQTT_RECORD_DAYS` in `config.env` changes that. The files are readable by the `armonico` group, so `armonico record follow` needs no `sudo`. They hold everything that passes through the broker, including Telegram chat IDs and the text of the answers. `--purge` removes them with the rest of the data.


## Installer

Run from the cloned repository folder.

| Command | What it does |
|---|---|
| `sudo ./install.sh` | Installs, or updates an installed machine. Settings are made in a one-time setup screen in the browser. An update keeps the settings, shortcuts, progress, songs and recordings |
| `sudo ./install.sh --cli` | The same, with the settings asked in the terminal instead of the browser |
| `sudo ./install.sh --device` | Installs on the Armonico Pi: the keys, the sound and the screens, with the setup on the device's own screen. Details: [hardware.md](hardware.md#the-armonico-pi-device) |
| `sudo ./install.sh --integrations` | Refreshes the Home Assistant parts and the Telegram bot's profile, with the values already on the machine (as `sudo armonico integrations`) |
| `sudo ./install.sh --reconfigure` | Runs the setup again with the current values as defaults. Keys added to `config.env` by hand are kept as written, and so are `DATA_DIR` and `SONGS_DIR`. Nothing else changes. A setup left before it was saved leaves the settings as they were and starts the services that ran before. Every setting and its place: [configuration.md](configuration.md) |
| `sudo ./install.sh --reconfigure --cli` | The same, in the terminal |
| `sudo ./install.sh --uninstall` | Removes the program, the services, the settings, the songs, the recordings, the progress, the shortcuts, the service user, and last of all the project folder it was run from. Nothing of it is left on the machine |
| `sudo ./install.sh --purge` | Removes everything. Details below |
| `sudo ./install.sh --purge --yes` | The same without the question, for scripts |
| `./install.sh --help` | Prints the options |

### Full removal: `--purge`

`--purge` removes the program and all of its data, from the machine and from the broker:

| Removed | Where |
|---|---|
| Lesson recordings (`.json` and `.mid`) | `/var/lib/armonico/recordings/` |
| All songs, including the ones downloaded with `/getmidi` or uploaded from the lesson screen | `/var/lib/armonico/songs/` |
| Lesson progress, lesson history, profiles, screen settings, volume, shortcuts, the MQTT recorder's files | `/var/lib/armonico/` |
| Settings and the MQTT password | `/etc/armonico/` |
| The program, its Python environment and the `armonico` command | `/opt/armonico/`, `/usr/local/bin/armonico` |
| The services (bridge, lessons, recorder, update), including overrides made with `systemctl edit` | `/etc/systemd/system/armonico-*` |
| Flags shared by the services | `/run/armonico/` |
| Loading of the ALSA sequencer at boot | `/etc/modules-load.d/armonico.conf` |
| The service user and its group | `armonico` |
| Retained `piano/` messages (also by `--uninstall`), so Home Assistant stops showing the old state, and the sensors the installer announced to it (`homeassistant/sensor/piano_*`), so the device **Piano** goes from Home Assistant by itself | the MQTT broker |
| The broker, when the installer installed it (option 2): its settings, users, data and the Mosquitto package. When other settings also use that Mosquitto, or another package installed since needs it (apt's dry run decides), only the piano's settings go and Mosquitto stays, with its service, settings and data; the confirmation says so | `/etc/mosquitto/conf.d/armonico.conf`, `/etc/mosquitto/armonico.passwd` |
| System packages the installer added, with the dependencies they brought. A package that was on the machine before the first install stays, and so does one that something installed later needs. `curl` and `ca-certificates` always stay | the list in `/etc/armonico/installed-packages` |
| The folder the project was unpacked in, with whatever else was put in it, also when `purge` runs through the `armonico` command (the folder is recorded at install). It stays only when it is a system folder or a home folder, or a git checkout with changes that were never committed | the cloned or unpacked folder |
| The changes the installer noted in `/etc/armonico/system-changes` (also by `--uninstall`): the user taken out of the groups it was added to, the pip cache when the installer made it, and on the Armonico Pi the user units enabled for everyone disabled again, greetd enabled again when it was, and the saved ALSA levels removed when the device made them | the groups, `/root/.cache/pip`, `/etc/systemd/user/`, `/var/lib/alsa/asound.state` |
| The system users and groups the removed packages declared in `sysusers.d` (such as `polkitd`, which Debian keeps after a purge), unless something still runs as them. Base groups such as `audio` or `video` always stay | `/etc/passwd`, `/etc/group` |
| Links in `/etc/systemd/user` left by user units whose packages were removed (`pipewire.socket` and the like, on the Armonico Pi) | `/etc/systemd/user/` |
| Data and songs folders moved elsewhere in `config.env` | wherever `DATA_DIR` and `SONGS_DIR` point. A system folder such as `/home` or `/var` is never removed, even when `config.env` points there |

Before anything is removed, the installer lists each folder with its size and number of files, counts the recordings and songs that are about to go, and prints a backup command. The backup holds the MQTT password, so the command makes it readable by root only:

```
==> About to remove Armonico and ALL of its data
  ✘ /var/lib/armonico  (92K, 20 files)
  ✘ /etc/armonico  (settings and the MQTT password)
  ...
  This deletes 3 lesson recordings, 7 songs (including every uploaded one),
  the lesson progress and history, the shortcuts and the settings.
  It cannot be undone.
  To keep a copy first:
    (umask 077; sudo tar czf ~/armonico-backup.tar.gz /var/lib/armonico /etc/armonico)

  Type DELETE to remove everything, anything else cancels:
```

Only the word `DELETE` continues. Any other answer, or running without a terminal and without `--yes`, removes nothing.

Left in place on purpose:

- `curl` and `ca-certificates`, and system packages that were there before the first install or that another program now depends on. Before removing a package, the installer asks apt for a dry run and keeps any package whose removal would take something outside its list with it.
- The project folder when it also holds other files or has local changes. The installer says why it was kept.
- The services' lines in the system journal. journald cannot delete the lines of a single service, and they age out with the rest of the journal.
- Everything outside this machine: the Home Assistant package and blueprints, the Telegram bot and its automations.

`--purge` also works after `--uninstall`, from a copy of the repository. `--uninstall` clears the retained messages itself; when the broker did not answer then, it keeps the broker's address and login in `/etc/armonico/broker.env` (root only), and `--purge` clears them with `mosquitto_sub` and `mosquitto_pub`, since the Python environment is already gone, and deletes the file. Either way the MQTT password goes through a private file, never on the command line.

## Services

`armonico status`, `sudo armonico restart` and `armonico logs` cover the everyday cases. The same with systemd directly:

| Command | What it does |
|---|---|
| `systemctl status armonico-bridge armonico-lessons` | State of both services |
| `sudo systemctl restart armonico-bridge armonico-lessons` | Restart, for example after editing `config.env` |
| `sudo systemctl stop armonico-bridge armonico-lessons` | Stop until the next boot or start |
| `sudo systemctl disable --now armonico-bridge armonico-lessons` | Stop and do not start at boot |
| `journalctl -fu armonico-bridge` | Live log of the bridge |
| `journalctl -u armonico-lessons -n 50` | Last 50 lines of the lesson engine |

## Tools

| Command | What it does |
|---|---|
| `sudo /opt/armonico/setup/update.sh` | Installs the newest release from GitHub and keeps everything else, without showing progress. `sudo armonico update` and "Install" on the lesson screen run the same |
| `sudo timedatectl set-timezone Europe/London` | The machine's time zone, which lessons and the alarm follow (`timedatectl list-timezones` lists them) |
| `sudo armonico unstick` | Brings a connected but silent keyboard back without unplugging it, and shows what was stuck. It runs `/opt/armonico/bridge/piano_unstick.sh` |
| `sudo armonico unstick "<keyboard name>"` | The same for a keyboard other than the one in the settings |
| `sudo /opt/armonico/bridge/piano_reset.sh` | The full reset, see `armonico reset`. From Telegram the bridge only writes `reset-request` in the data folder, and the root unit `armonico-reset.service`, started by `armonico-reset.path`, runs it |
| `sudo sh -c 'set -a; . /etc/armonico/config.env; exec /opt/armonico/venv/bin/python /opt/armonico/lessons/piano_numbers.py song.mid'` | Converts a MIDI file into white-key numbers for this keyboard, split into phrases. Also `--track N` for a specific track and `--json out.json` to save the result |
| `sudo sh -c 'set -a; . /etc/armonico/config.env; exec /opt/armonico/venv/bin/python /opt/armonico/lessons/piano_hands.py 17 17 18 19'` | Hand positions and fingers for a melody given as white-key numbers. `11#` is the black key right of white key 11 |

The `sh -c` wrapper loads the keyboard's range from the settings. Without it, both tools assume a 61-key keyboard.

## MQTT

The full topic list is in [mqtt.md](mqtt.md).

```bash
mosquitto_sub -h <broker> -u <user> -P <password> -t 'piano/#' -v                 # watch everything
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/play -m ode_to_joy       # play a song
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/alarm/set -m ode_to_joy  # alarm, until the stop key
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/stop -m Test             # stop
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/log/control -m start     # log to piano/log
```
