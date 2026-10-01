# Installation

## Requirements

| Item | Details |
|---|---|
| System | Debian 11 or newer, Ubuntu 22.04 or newer, or Raspberry Pi OS (Bullseye or newer, 32 or 64 bit), with or without a desktop. systemd must be running |
| CPU | Any. 64-bit PCs (amd64) and 64-bit ARM (arm64) are the main targets, and 32-bit ARM (armhf) works too |
| Memory | 512 MB or more. The services use about 100 MB together |
| Storage | 300 MB free during the install. About 100 MB stay in use, more when a Python package had to be compiled (see below), plus the songs |
| Keyboard | Any class-compliant USB MIDI keyboard with 25 to 88 keys. See [hardware.md](hardware.md) |
| MQTT broker | Required, also without Home Assistant, because the two services talk through it. The installer can install one on the machine, or use an existing one such as the Mosquitto broker app in Home Assistant. See [Choosing a setup](#choosing-a-setup) |
| Network | Internet access during the install, for the system and Python packages |

Home Assistant and the Telegram bot are optional. [Without Home Assistant](#without-home-assistant) lists what works without them.

### Machines

| Machine | Status |
|---|---|
| Virtual machine (Proxmox, VirtualBox, VMware) with USB passthrough, 2 GB RAM | Tested, including an Ubuntu Server 26.04 guest |
| Mini PC or any x86 computer | Supported |
| Raspberry Pi 4 | Tested |
| Raspberry Pi 5 | Supported |
| Raspberry Pi 3 and Zero 2 W | Supported. The install takes longer |
| Raspberry Pi 1, 2 and Zero W (armv6 and armv7, 512 MB) | Expected to work with 32-bit Raspberry Pi OS, not tested |
| Termux on Android | Not supported. Android gives apps no access to ALSA MIDI devices and has no systemd |
| Windows, macOS | Not supported directly. A Linux virtual machine with the keyboard passed through works |

### The CPU and the Python packages

The code is bash and Python, the same on every CPU, and apt picks the right system packages by itself. The one part with compiled code is `python-rtmidi`, the MIDI library of the lesson engine. The installer checks the CPU and the Python version:

| CPU | Python | What happens |
|---|---|---|
| amd64, arm64 | 3.8 to 3.12 | A ready-made package comes from PyPI |
| armv7l, armv6l (32-bit Raspberry Pi OS) | any | A ready-made package comes from piwheels.org |
| any other, or Python 3.13 and newer (Debian 13, Ubuntu 26.04) | | It is compiled on the machine: about 10 seconds on a PC, a few minutes on a Raspberry Pi 3. The compiler and headers are installed for this, about 250 MB |

On Raspberry Pi OS Lite, with no desktop, everything runs over SSH. The setup screen opens in a browser on any other device in the network, or the setup runs in the terminal with `--cli`.

## Choosing a setup

The installer asks one question about the broker. The answer depends on what is already there:

| Situation | Answer in the installer | Then |
|---|---|---|
| Home Assistant OS or Supervised, with an access token | The "Set Home Assistant up now?" question comes first. Answer yes | The installer installs the Mosquitto app, makes the piano's user, and sets everything up in Home Assistant, with nothing to type. Details in [home-assistant.md](home-assistant.md#the-short-way-one-token) |
| Home Assistant with the Mosquitto broker app, no token | 1, an existing broker | The broker address is the Home Assistant machine, and the user is a Home Assistant user made for the piano. Details in [home-assistant.md](home-assistant.md#the-broker-in-home-assistant) |
| Another broker on the network | 1, an existing broker | Its address, port, user and password |
| No broker and no Home Assistant | 2, install one here | Nothing to fill in. Mosquitto is installed on the machine with a random password, and the setup goes on to the keyboard |

A broker installed by option 2 answers port 1883 on the machine itself only, and port 9001 (websockets, for the [status screen](screen.md)) on the network. Both require a login. To let Home Assistant on another machine use this broker, add a `listener 1883` line without an address to `/etc/mosquitto/conf.d/armonico.conf` and restart Mosquitto, or use Home Assistant's own broker (option 1) instead. Its user is `piano`, and the password is in the settings file:

```bash
sudo grep MQTT_PASS /etc/armonico/config.env
```

A second user for Home Assistant keeps the two passwords apart:

```bash
sudo mosquitto_passwd /etc/mosquitto/armonico.passwd ha
sudo systemctl restart mosquitto
```

`mosquitto_passwd` then warns that the owner of the file is not root. The warning is expected, and the file stays owned by `mosquitto`, because the broker cannot open a password file owned by root.

When a broker already listens on port 1883 of the machine, option 2 installs nothing, and the setup uses that broker at `127.0.0.1`.

## Without Home Assistant

| Works | How |
|---|---|
| Lessons, songs, recordings | The lesson screen at `http://<machine>:8099`: lessons, MIDI uploads, the song search, replays. A device enters the screen code once and is remembered for 14 days |
| Playing a song, the other terminal commands | `armonico play ode_to_joy` on the machine, or `mosquitto_pub ... -t piano/play -m ode_to_joy` from any computer on the network |
| Alarm clock | A line in root's crontab (`sudo crontab -e`), shown below |
| Shortcuts | Recognised and published to `piano/shortcut`. Any MQTT tool, such as Node-RED, can act on them |

| Needs Home Assistant | Why |
|---|---|
| Telegram commands | The bot runs as Home Assistant automations |
| Shortcuts that run automations | The actions live in Home Assistant |
| The alarm blueprint with retries | It is a Home Assistant blueprint |

An alarm at 07:00 on weekdays, with the login read from the settings file rather than written into the crontab:

```
0 7 * * 1-5  armonico play ode_to_joy
```

`armonico` reads the settings itself, so no password is typed on a command line where
`ps` would show it to every other user on the machine. Where `mosquitto_pub` is used
directly, its own configuration file carries the login instead of the arguments:

```
# ~/.config/mosquitto_pub, mode 0600
-u piano
-P the-password
```

The time follows the machine's clock and time zone (`timedatectl`).

## Install

```bash
git clone https://github.com/vestacorelabs/armonico.git
cd armonico
sudo ./install.sh
```

### The steps

1. **Checks.** The system is Debian based, systemd is running, and every file of the repository is in place. Nothing changes before these checks pass.
2. **Keyboard check.** Only `alsa-utils` and `python3` are installed at first, and then the installer looks for a keyboard. When it finds one, it offers to play classical music on it while the rest installs. Any key stops the music, which shows that both directions of the USB link work. The pieces are listed in [setup/music/SOURCES.md](../setup/music/SOURCES.md).
3. **System packages.** `mosquitto-clients`, `python3-venv` and a few small tools. The installer notes which packages were not there before, so `--purge` can later remove exactly those.
4. **Service user.** A system user named `armonico`, with no login and no password, in the `audio` group. The services never run as root, and neither does the setup screen: the installer starts it as this user. The user who ran `sudo` joins the `armonico`, `adm` and `systemd-journal` groups, which allows the `armonico` command and its logs without `sudo` from the next login. In the current terminal, `newgrp armonico` makes the group active at once. Commands that change the system (`restart`, `update`, `reconfigure`, `uninstall`, `purge`, `integrations`) ask `sudo` for the password by themselves, so `sudo` need not be typed in front of them.
5. **Program files and the Python environment.** The code goes to `/opt/armonico`, and a Python virtual environment is built there with `mido`, `python-rtmidi` and `paho-mqtt` 2. The virtual environment exists because not every distribution packages `paho-mqtt` 2. `zeroconf` is added after them, in a call of its own: it is used only to find Home Assistant on the network, and a failure there is a warning, not a stopped install. The `armonico` command is installed as `/usr/local/bin/armonico`.
6. **Settings.** On a first install only. First the time zone (below), then the search for Home Assistant (below), then the broker (see [Choosing a setup](#choosing-a-setup)), and then the one-time setup screen in the browser (below).
7. **Services.** `armonico-bridge` and `armonico-lessons` start and are enabled at boot. The MQTT recorder, `armonico-recorder`, is installed too, but stays off until `sudo armonico record on`.

### The time zone

Lessons, the practice streak and the alarm clock count days on the machine's clock. With a wrong time zone every day starts at the wrong hour: the streak rolls over at the wrong moment, and an alarm from cron rings at another time. Raspberry Pi OS is often left on UTC. The installer shows the current zone and time. Enter keeps it, and a name such as `Asia/Jerusalem` changes it through `timedatectl`, which is the system's own setting and stays after a restart. It can be changed later with `sudo timedatectl set-timezone Asia/Jerusalem`.

### Finding Home Assistant

Home Assistant publishes itself on the network under the mDNS service name `_home-assistant._tcp.local.`, and its answer carries the version, the location name and the internal address. The installer listens for a few seconds, and a Home Assistant in a container on the same machine is found by port 8123 of localhost instead. What comes back becomes the suggested broker address in the terminal and in the setup screen, and port 1883 on that address is opened once to see whether the Mosquitto broker app answers there.

The app itself publishes nothing over mDNS, and its logins are Home Assistant user accounts that cannot be read from outside, so a user name and a password are asked for in every case. A suggested address can be replaced, and an install where nothing was found asks exactly the questions it always asked. mDNS stays inside one network, so a Home Assistant on another VLAN or behind an access point that blocks multicast between clients is not seen.

When nothing is found (mDNS does not cross to another VLAN or subnet, and some routers block it), the question "Set Home Assistant up now?" still takes the address by hand, and the setup goes on exactly as if it had been found. If Home Assistant is on another VLAN or subnet, the router has to route between the two networks, and its firewall has to let this machine reach Home Assistant on port 8123 (the web page and its API) and port 1883 (the broker), with replies allowed back. A switch or access point with client isolation also blocks it, and has to allow the two devices to talk.

### The ports

The setup screen uses 8098 and the lesson screen 8099, both over HTTP, which is TCP. A
machine that already runs other things may well have one of them. Before anything opens a
port, the installer asks the question the server itself will ask: it tries to bind the
port on every address and sees whether the system allows it. That catches the case a
simpler check misses, where another service holds the port on one address only and answers
nothing on localhost while still blocking a listener.

When a port is taken, the installer says so, names what holds it where the system can tell,
and offers the first free port from ten above. Another number can be typed instead. The
lesson screen's port is kept in `LESSON_UI_PORT` in `config.env`, so it survives a
reconfigure and an update, and the address printed at the end is the one actually listening.

Neither port belongs on the internet. A port forward to 8099 on a router hands the keyboard,
the practice history and the backup to whoever finds it.

### The setup screen

The terminal shows an address and a 6-digit code:

```
  Open in a browser on any device in the same network:

      http://192.168.1.20:8098

  One-time code:  482913
```

After the code, the screen has three steps:

1. **MQTT broker.** Address, port, user name and password. The connection is tested before the next step opens.
2. **Keyboard.** The connected MIDI keyboards are listed. After one is picked, its lowest key and then its highest key are pressed. That confirms the right device and measures the keyboard, so any size works. The lowest key becomes the stop key, which stops the alarm and anything else that is playing.
3. **Save.** An optional field takes extra names for the lesson screen (`LESSON_HOSTS`), for a reverse proxy such as Caddy or a name in the local DNS. The lesson screen always asks for its code; turning that off is `sudo armonico code off`. The setup screen starts in English, and the language button changes it.

When the broker was installed on this machine, no Home Assistant steps follow. When Home Assistant is used, the card at the top of step 1 shows whether its broker answers, checked again each time the page asks. Home Assistant and Telegram are not part of this screen. They connect to the same broker. Home Assistant is set up by the installer, or by the steps this screen shows; Telegram by `armonico telegram`. After saving, the screen shows what comes next, with links to the guides.

The button at the top switches the setup page itself between English and Hebrew. It does not choose the piano's language: the lesson screen and the Telegram answers start in English, and the flag menu on the lesson screen changes them from then on.

Once saved, the installer goes on in the terminal and the page can be closed. An unused setup screen stops after 15 minutes. Five wrong codes in a row lock it for a minute.

### Setup in the terminal

On a machine with no browser nearby, the same steps run as questions in the terminal:

```bash
sudo ./install.sh --cli
```

## Files on the machine

| Path | Contents | Owner and permissions |
|---|---|---|
| `/opt/armonico/` | The code, the Python virtual environment, and a copy of `install.sh` for `armonico reconfigure`, `uninstall` and `purge` | root, read-only for the services |
| `/opt/armonico/VERSION`, `/opt/armonico/REPO` | The installed version, and the GitHub repository updates come from | root |
| `/usr/local/bin/armonico` | The `armonico` command | root |
| `/etc/armonico/config.env` | Settings, including the MQTT password | root, group `armonico`, `640` |
| `/etc/armonico/secrets.env` | The Home Assistant and Telegram tokens, only when you answered yes to keeping them for updates | root, `600` |
| `/etc/armonico/source_dir` | The folder the project was unpacked in, so `uninstall` and `purge` can remove it | root, `600` |
| `/etc/armonico/installed-packages` | The system packages the installer added, for `--purge` | root, `600` |
| `/var/lib/armonico/shortcuts.conf` | Shortcuts | service user |
| `/var/lib/armonico/lessons.json`, `history.json` | Lesson progress and history of the first profile | service user |
| `/var/lib/armonico/settings.json`, `volume`, `last_chat` | Lesson screen settings: the language, and the first profile's pace, colors and labels | service user |
| `/var/lib/armonico/profiles.json` | The profiles, the active one, and each PIN as a salted hash | service user, `600` |
| `/var/lib/armonico/profiles/<id>/` | Every profile except the first: its own progress, history, settings and recordings | service user |
| `/var/lib/armonico/songs/` | MIDI files, and `<song>.credit.json` next to a downloaded one, with its source and license | service user |
| `/var/lib/armonico/recordings/` | Lesson recordings, the last 30 | service user |
| `/var/lib/armonico/mqtt/` | The MQTT recorder's files, one per day, when it is on | service user, readable by the group |
| `/run/armonico/` | Flags shared by the two services, cleared at boot | service user |

## Settings

`/etc/armonico/config.env` can be edited by hand, with every value in single quotes. After a change, the services are restarted with `sudo armonico restart`. Every setting, including the ones kept in Home Assistant, the lesson screen and the browser, is listed in [configuration.md](configuration.md).

Running the setup again keeps the current values as defaults, and keeps keys added by hand:

```bash
sudo armonico reconfigure
```

## Songs

Songs are plain MIDI files in `/var/lib/armonico/songs/`. A song's name is its file name without `.mid`, and case does not matter. The installer adds six classical pieces to start with. More can be uploaded on the lesson screen, downloaded with `/getmidi` or `armonico getmidi`, or copied into the folder:

```bash
sudo install -m 640 -o armonico -g armonico my_song.mid /var/lib/armonico/songs/
```

## Update

**From the lesson screen.** The footer shows the version and "Check for updates". When GitHub has a newer release, "Install" starts the update. The screen writes a request, and a root service (`armonico-update.path` and `armonico-update.service`) downloads that release from GitHub over HTTPS and runs its installer. The screen itself has no root rights and can name only a version number, and the download address is built from the repository in `/opt/armonico/REPO`. Progress and errors go to `/var/lib/armonico/update.log`.

**From the terminal.** The same update, with its progress shown:

```bash
sudo armonico update           # the newest release
sudo armonico update 1.0.1     # a given release
```

**From a copy of the repository:**

```bash
cd armonico
git pull
sudo ./install.sh
```

An update keeps the settings, shortcuts, progress, recordings and songs. Only the code and the services are replaced.

**Home Assistant and Telegram in an update.** The installer asks, once, whether to keep the Home Assistant token (and the Telegram bot token, when the bot was set up) for later updates. With yes, they are stored in `/etc/armonico/secrets.env`, readable by root only, and every update refreshes the sensors, helpers, automations and blueprints in Home Assistant and the bot's description, commands and picture in Telegram. The piano's automations are replaced by the update; one that you changed in Home Assistant is first saved as a file in `/etc/armonico/ha-backup/`, so the change can be put back by hand. With no, nothing is stored, and an update leaves those parts as they are. Refresh them whenever you like with `sudo armonico integrations`: it uses the kept tokens, or asks for new ones. `sudo armonico forget-tokens` deletes the kept tokens (delete the token in Home Assistant's profile page too).

## Backup and restore

"Download a backup" in the footer of the lesson screen (or in its settings) downloads one zip of the profile that is signed in: its progress, history, settings and recordings, and the songs, which every profile shares. The file is named after the profile, for example `armonico-backup-Noa-2026-10-02.zip`. The other profiles and the piano's own settings are not in it. The MQTT password is not in it, since it lives in `/etc/armonico/config.env`.

"Restore from a backup" in the screen's settings takes such a zip. Only a backup made by Armonico is accepted: a zip with any file the program never writes, a file that cannot be read back, or a backup from a newer version is refused, and nothing of it is kept. A backup goes back into the profile it was made from (not into whoever is signed in), and that profile becomes the profile at the piano. A profile that was deleted is created again, with the same name. A profile with a PIN asks for it first, like switching to it. Progress, history and settings are replaced; recordings and songs are added, and nothing already here is deleted. (A backup from an older version may hold several profiles; then the screen asks which of them to take.) It does not run while a lesson is playing.

## Uninstall

```bash
sudo armonico uninstall   # removes everything: program, settings, progress, songs, recordings, and the folder the project was unpacked in
sudo armonico purge       # removes everything, after listing it and asking for DELETE
```

`sudo ./install.sh --uninstall` and `--purge` do the same from a copy of the repository. What `--purge` removes, and what it leaves on purpose, is in [commands.md](commands.md#full-removal---purge).

## Troubleshooting

| Symptom | Check |
|---|---|
| A service does not start | `armonico status`, then `armonico logs` or `journalctl -u armonico-bridge -n 50` |
| "No MIDI keyboard found" | `aconnect -i` must list the keyboard. On a virtual machine, the USB device has to be passed through. When `/dev/snd/seq` is missing: `sudo modprobe snd-seq` |
| The keyboard is connected but silent | `sudo armonico unstick` resets it without unplugging, and shows whether data reaches it |
| Disconnects under load or with a long cable | See [hardware.md](hardware.md) |
| Nothing reaches Home Assistant | `armonico record follow piano/` (after `sudo armonico record on`), or `mosquitto_sub -h <broker> -u <user> -P <password> -t 'piano/#' -v`, shows whether the bridge publishes |
| A live log | `armonico logs -f`, or `/showlog` in Telegram |
