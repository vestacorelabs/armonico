# Questions and pitfalls

Common questions, and the mistakes that break a setup.

## Before installing

**Is Home Assistant required?**
No. The lesson screen, the `armonico` command, playback over MQTT and an alarm from cron all work without it. Telegram and shortcuts that run automations need it. See [Without Home Assistant](installation.md#without-home-assistant).

**Is a broker required without Home Assistant?**
Yes. The bridge and the lesson engine talk to each other through MQTT. When there is no broker, the installer sets one up (option 2).

**Can a Home Assistant install be put on the same Raspberry Pi just for this?**
It works, but Home Assistant is a whole platform and uses far more memory than the piano. For the piano alone, option 2 and the lesson screen are enough.

**Does it work with an acoustic piano, or without any keyboard?**
Not yet. It needs a USB MIDI keyboard, or a 5-pin MIDI keyboard through a USB MIDI interface. See [hardware.md](hardware.md).

**Windows, macOS, Android?**
Windows and macOS only through a Linux virtual machine with the keyboard passed through. On Android, Termux has no access to MIDI devices, but a full Debian container with systemd on a rooted phone works, with limits. See [hardware.md](hardware.md#linux-on-android).

**Does it teach both hands?**
It teaches the melody of a song, with one hand. When the file has more parts, they play along as accompaniment.

## The broker

**The setup filled in an address by itself. Where did it come from?**
Home Assistant publishes itself on the network under the mDNS name `_home-assistant._tcp.local.`, and the setup listens for that answer for a few seconds before it asks anything. The address, the version and the location name come from that answer, and the setup then opens port 1883 on the same address to see whether the Mosquitto broker app answers there. A filled-in address is a suggestion and can be replaced.

**Home Assistant is running, and the setup did not find it.**
mDNS does not cross between networks, so a Home Assistant on another VLAN or subnet is never seen, and some access points block multicast between wireless clients. The optional `zeroconf` package may also have failed to install, which the installer reports as a warning. The address can always be typed by hand, and nothing else about the install changes.

**Why does the setup not fill in the user name and the password as well?**
The Mosquitto broker app signs in with Home Assistant user accounts, and there is no way to read one from outside Home Assistant. Only the address and the port can be found.

**Home Assistant already has the Mosquitto broker app. Should option 2 be used as well?**
No. Two brokers never see each other's messages, and nothing reaches Home Assistant. Answer 1 and use the Home Assistant machine's address. See [home-assistant.md](home-assistant.md#the-broker-in-home-assistant).

**The login is refused by the Mosquitto broker app.**
The app accepts Home Assistant users only (Settings, People, Users) and no anonymous logins. The names `homeassistant` and `addons` are reserved and cannot be used.

**The Users tab is missing in Home Assistant.**
It appears after Advanced mode is turned on in the user profile.

**`mosquitto_passwd` said "Passwords do not match" although they did.**
Several lines were pasted at once, and the lines after the command were read as the password. The command has to run on its own, and the rest is pasted after it finishes.

**`mosquitto_passwd` warns that the file owner is not root. Should it be changed?**
No. With Mosquitto 2.0.18 the broker cannot open a password file owned by root and fails at start. The file stays `mosquitto:mosquitto`, mode `0700`.

**A new user was added and all the old ones stopped working.**
`mosquitto_passwd -c` creates the file from scratch. It is used only for the first user.

**Where is the password of the broker installed by option 2?**
`sudo grep MQTT_PASS /etc/armonico/config.env`

**The installer did not install a broker although option 2 was chosen.**
Something already listens on port 1883 of the machine, usually another Mosquitto or a Home Assistant on the same machine. The setup uses it at `127.0.0.1` with one of its users.

## Setup and network

**Something else on this machine already uses port 8098 or 8099.**
The install checks both before anything listens on them. When one is taken it says what has it, offers the first free port from ten above, and takes another number instead if one is typed. The lesson screen's port is kept in `LESSON_UI_PORT` in `config.env` and survives a reconfigure.

**The setup screen or the lesson screen does not open from another device.**
The ports are 8098 (setup, 15 minutes only) and 8099 (lessons). A firewall on the machine, a guest or IoT network that isolates devices, or router rules between networks can block them. Where a firewall is the cause, open the port to the home network only, not to everything: `sudo ufw allow from 192.168.0.0/16 to any port 8099 proto tcp`. Neither port belongs on the internet, and a port forward to one of them on the router hands the keyboard and the practice history to anyone who finds it.

**Who can use the lesson screen?**
By default, only a device that entered the code. The install turns this on, and each device enters a 6-digit code once, then keeps a signed pass for 14 days that is renewed every time the screen is used. The code is shown by `armonico code`, by `/pianocode` in Telegram, and at the end of the install. Both reads and actions need the pass, so the practice history and the backup are behind it too, not only the buttons.

Asking for the code can be turned off, in the screen's settings or with `armonico code off`; the setup screen does not offer it. With it off, anyone who can reach port 8099 can start a lesson, play on the keyboard, upload songs, read the practice history, download a full backup and ask for a system update. The screen is meant for a home network, not for the internet, and a port forward to it on the router opens all of that to everyone.

If a code was seen, or a device with a pass was lost, "Sign every screen out" in the settings ends every pass at once, on every device except the one that pressed it. The screen answers only to its own address and host name, so a web page elsewhere cannot use a browser at home to reach it. A reverse proxy with a name of its own needs that name in `LESSON_HOSTS` (for example `LESSON_HOSTS='piano.home.lan'`).

**The keyboard was not found during setup.**
It has to be connected and switched on, and `aconnect -i` must list it. On a virtual machine, the USB device has to be passed through to it. When `/dev/snd/seq` is missing: `sudo modprobe snd-seq`.

**Several MIDI devices are listed.**
The keyboard is picked by its name, and pressing its lowest and highest keys confirms the choice. The name is saved in `MIDI_DEVICE`. For a different keyboard later: `sudo armonico reconfigure`.

**The old bridge from a manual install still runs.**
Both answer the same topics, so every song plays twice and every Telegram reply comes twice. The old service has to be stopped and disabled before the new one uses the same broker.

## Home Assistant

### Chrome says "insecure download blocked"

The screen is plain HTTP on the home network, and Chrome warns about any download from an address that is not HTTPS, even a file the piano made for you. The file is safe: choose Keep in the download list. The warning goes away only with HTTPS, for example through a reverse proxy such as Caddy that holds a certificate for a name, with that name added to `LESSON_HOSTS`.

### The status screen says there is no broker

The status screen connects from the browser of the device, to port 9001 of the broker. A broker the installer made answers that port on the network. For a Home Assistant broker, turn on the websockets listener of its app (port 1884); the page tries 9001 and then 1884 by itself.

### What does the access token do, and is it safe?

It lets the installer set Home Assistant up over its own API: the Mosquitto app, a user for the piano, the sensors, the helpers, the automations and the blueprints. It is read with no echo and sent only to the address you typed. By default it is kept in memory only. If you answer yes to keeping tokens for updates, it is written to `/etc/armonico/secrets.env`, readable by root only, so updates can refresh Home Assistant by themselves; `sudo armonico forget-tokens` deletes it. It is as powerful as the administrator who made it, so delete it in the profile page when the installer is done, unless you keep it for updates. The steps are in [home-assistant.md](home-assistant.md#the-short-way-one-token).

### The installer says `Not authorized` for the piano's login, although Home Assistant accepts it.

That is the Supervisor's login cache, which answers a changed password for a known name with "no" and checks with Home Assistant afterwards. The installer clears it before every attempt and retries. See the table in [home-assistant.md](home-assistant.md#when-it-does-not-go-through).

### Why is the lesson screen in English although I use Hebrew?

A lesson screen starts in English on every device that has not chosen a language, and a fresh install forgets a language a browser remembered from an earlier install on the same address, so the code screen and the footer are the same everywhere. The flag menu at the top changes it, the choice is kept in that browser, and the Telegram answers follow it.

## Everyday use

**A song from the internet has no band.**
Only a file with more instruments than the melody has one. The six included pieces are the melody alone.

**The keyboard stays quiet after a song or plays too softly.**
Some MIDI files leave the volume low when they end. `sudo armonico unstick` resets it without unplugging.

**The lesson says the keyboard is on the bus but its raw MIDI port cannot be opened, or the computer's keys make no sound.**
Songs, the alarm and shortcuts go through the ALSA sequencer, which is why they can work while this does not. Lessons and the keys of the screen write to the keyboard's raw MIDI device, `/dev/snd/midiC*D*`. If `aconnect -i` lists the keyboard but `sudo -u armonico amidi -l` lists nothing, the device node is missing or the `audio` group cannot open it. This happens in containers that were not handed the node, and on systems without the usual udev rule. The log of the lessons (`armonico logs lessons`) says which one it is.

**An alarm and a lesson at the same time.**
The alarm always wins and the lesson stops.

**Disconnects with a long cable.**
A passive USB extension longer than about 5 m loses the keyboard, and an active extension fixes it. See [hardware.md](hardware.md).

**How much space do recordings take?**
The last 30 recordings are kept, and older ones are deleted by themselves. An uploaded MIDI file can be up to 4 MB. The MQTT recorder, when it is on, keeps 14 days by default.

**Can several people learn on the same keyboard?**
Yes, with profiles: the round button next to the keyboard status on the lesson screen. Each profile has its own progress, history, recordings, pace and language. The songs, shortcuts and volume belong to the piano. The profile chosen is the one at the piano for every screen and for Telegram, so `/lesson` counts toward it too. Switching is refused while a lesson or a replay is playing.

**What does a profile's PIN protect?**
It stops others from switching into that profile, or deleting it, and playing over its progress. It is 4 to 8 digits, stored only as a salted hash in `profiles.json`, and 5 wrong tries from one address wait a minute. It is not a lock on the lesson screen itself: anyone on the home network who opens the screen sees the profile that is at the piano. The first profile's files stay where they always were, so updating from a version without profiles moves nothing; every other profile lives in `profiles/<id>/` in the data folder, and the backup includes them all.

## Updating, moving, removing

**After a system upgrade (for example to a newer Debian) the lessons service does not start.**
The Python environment was built for the old Python. Running `sudo ./install.sh` again from a copy of the repository rebuilds it and keeps everything else.

**The technical name in `install.sh` was changed after installing.**
The new name installs a second copy next to the old one. The old copy has to go first, while the old name is still set (`sudo ./install.sh --purge`), and only then is the name changed and the installer run again.

**Moving to another machine.**
The data is all in `/var/lib/armonico/`: songs, progress, recordings, shortcuts. The order is to install on the new machine, stop the services there (`sudo systemctl stop armonico-bridge armonico-lessons`), copy the folder over with owner `armonico`, and start them again. "Download a backup" on the lesson screen gives one profile and the songs as one zip; copying the folder moves all profiles.

**Removing everything.**
`sudo armonico purge`, which lists everything and asks for the word DELETE first. Details in [commands.md](commands.md#full-removal---purge).
