# MQTT

Everything the system does goes through MQTT. Home Assistant, the Telegram bot, the `armonico` command and any other tool talk to it by publishing and subscribing to the topics below, all of which start with `piano/`.

## Broker

Any MQTT 3.1.1 broker works. A broker is needed even without Home Assistant, because the bridge and the lesson engine talk through it.

- **No broker at all:** the installer offers to install Mosquitto on the machine (option 2). See [installation.md](installation.md#choosing-a-setup).
- **Home Assistant:** the Mosquitto broker app. See [home-assistant.md](home-assistant.md#the-broker-in-home-assistant).
- **By hand, on any Debian or Ubuntu machine:**

```bash
sudo apt install mosquitto
sudo touch /etc/mosquitto/passwd
sudo mosquitto_passwd /etc/mosquitto/passwd piano
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 0700 /etc/mosquitto/passwd
printf 'listener 1883\nallow_anonymous false\npassword_file /etc/mosquitto/passwd\n' \
  | sudo tee /etc/mosquitto/conf.d/piano.conf
sudo systemctl restart mosquitto
```

`mosquitto_passwd` asks for the password twice, and it reads every pasted line after it as an answer. It has to run as a command of its own, with the lines after it pasted only once it has finished.

A dedicated user for the piano keeps its password separate from everything else on the broker. Home Assistant gets a user of its own, added to the same file without `-c` (which would erase the existing users):

```bash
sudo mosquitto_passwd /etc/mosquitto/passwd ha
sudo systemctl restart mosquitto
```

The file must be owned by `mosquitto` and readable by nobody else. With Mosquitto 2.0.18, a file owned by root makes the broker fail at start ("Unable to open pwfile"), and a file others can read brings a warning that future versions will refuse it. `mosquitto_passwd` itself warns that the owner is not root. That warning is expected and changes nothing.

## Commands (published to the piano)

| Topic | Payload | Effect |
|---|---|---|
| `piano/play` | `song` or `song\|who` | Plays a MIDI file from the songs folder once. `who` is shown as the source of the song, for example `ode_to_joy\|Morning routine` |
| `piano/alarm/set` | `song` or `song\|who` | Plays the song in a loop until the stop key is pressed or `piano/stop` arrives |
| `piano/stop` | a reason | Stops the alarm or the song. The reason is published in `piano/alarm/stopped_by` when it stopped the alarm, and in `piano/play/stopped_by` when it stopped a song (the alarm's song too) |
| `piano/pause` | `pause`, `resume` or `toggle` | Pauses a song, or goes on from the same place (the file is cut at the second it reached). Only a song: the alarm is never paused, and a stop works on a paused song as on a playing one |
| `piano/log/control` | `start` or `stop` | Mirrors the bridge's log to `piano/log` |
| `piano/shortcut/cmd` | `action\|chat_id\|arguments` | Shortcut and song commands, sent by the Telegram automation and the `armonico` command. Actions: `list`, `add`, `del`, `learn`, `cancel`, `songs`, `play`, `reset` (the full reset of the connection, as `/pianoreset`, only with the admin secret, see below) |
| `piano/game/cmd` | `action\|chat_id\|arguments` | Lesson, profile and screen code commands. Actions: `lesson`, `lessons`, `lessonstop`, `getmidi`, `profile`, `code`, `textexport` (the edited wording as JSON, for `armonico text export`) |

### The commands that prove who is asking

`code` hands out the screen code, and `profile` changes whose progress is being recorded.
On `piano/shortcut/cmd`, `reset` starts the full reset, which restarts every service. Each of
them is refused unless its
arguments begin with `secret:<value>`, where the value is `MQTT_ADMIN_SECRET` from
`/etc/armonico/config.env`. A refused command is written to the log and does nothing.

    piano/game/cmd       code|cli-1234|secret:<value> on
    piano/shortcut/cmd   reset|1234567|secret:<value>

`sudo armonico reset` runs the reset itself, as root, and sends nothing over MQTT.

`armonico` reads the secret from the settings file by itself, so nothing changes in a
terminal. A Home Assistant automation that sends one of the four has to carry it. The package's
Telegram automation does: it reads `armonico_admin_secret` from `secrets.yaml`, and when
the installer sets Home Assistant up it writes the value in itself.

This exists because a broker cannot be relied on to say who may publish what. Where the
installer put the broker in place, topic rules do the job and this is a second lock on
the same door. Where the broker belongs to someone else, it is the only lock there is,
and in Home Assistant's Mosquitto add-on that is the whole of the story: in every
version of series 7 the add-on does not enforce topic rules at all, because the plugin
it authenticates with answers the permission question before the broker's own rules are
ever reached. Nothing warns about this and nothing fails, which is what makes it worth
writing down.

### Who may reach which topic

When the installer put Mosquitto here, it wrote `/etc/mosquitto/armonico.acl` with two
logins that have nothing in common but the machine they run on:

    user piano
    topic readwrite piano/#

    user piano-rec
    topic read #

`piano` acts, and only under `piano/`. `piano-rec`, which the recorder uses, watches
the whole broker and writes nothing, so a recorder login that got out can listen and
command nothing. Port 1883 listens on this machine only, unless the install was told
another machine needs it. Port 9001, the websockets the status page uses, answers on the
network, so the page opens from other devices; it is plain `ws://`.

On a broker the installer did not put in place, none of this is written by anything
here. The same two rules can be added by whoever owns that broker.

A song name is the file name without `.mid`. Case does not matter.

## Events and state (published by the piano)

Topics marked *retained* keep their last value on the broker, so a subscriber sees it right away.

| Topic | Retained | Values |
|---|---|---|
| `piano/status` | yes | `online`, `offline`, `starting`, `reconnecting`, `resetting`, `disconnected`, `timeout`, `stuck`. `offline` is also the bridge's last will. `stuck` means several startups in a row failed to find the keyboard, so it likely needs a physical power-cycle |
| `piano/alert` | no | A human-readable message when the keyboard is stuck (asking for a power-cycle) and again when it comes back. Not retained, so a notification automation does not resend the last one on reload. For a Telegram message, for example |
| `piano/port` | yes | The keyboard's ALSA client number, published again when the keyboard comes back on another one |
| `piano/range` | yes | The lowest and highest key from the settings (`KEY_LOWEST`, `KEY_HIGHEST`), for example `36 96`. The status page draws its keyboard from it |
| `piano/notes` | no | Every key pressed, as a MIDI note number |
| `piano/pedal` | no | `down`, `hold` (held for 2 seconds), `up` |
| `piano/shortcut` | no | The name of a shortcut that was just played |
| `piano/shortcut/reply` | no | `chat_id\|text`, the answers to both command topics. A numeric chat ID is a Telegram chat, and one that starts with `cli-` is the `armonico` command in a terminal, which Home Assistant ignores |
| `piano/play/status` | yes | `playing` or `idle` |
| `piano/play/paused` | yes | `on` while a song is paused, otherwise `off`. `piano/play/status` stays `playing` while paused |
| `piano/play/current` | yes | The song playing now, or `unknown` |
| `piano/play/source` | yes | Who started it: `Telegram`, `Alarm clock`, a name given in the command, or `MQTT` |
| `piano/play/duration` | yes | Length of the song, for example `2 minutes 5 seconds`. Cleared when the song or the alarm ends |
| `piano/play/last_finished` | yes | The last song that ended |
| `piano/play/stopped_by` | yes | `Finished`, `Stop key (C2)`, `Telegram`, `Playback error`, `Keyboard disconnected` or the reason sent to `piano/stop` |
| `piano/alarm/status` | yes | `on`, `off`, or `failed` when an alarm could not start (see `piano/alarm/error`) |
| `piano/alarm/stopped_by` | yes | What stopped the alarm: `Stop key (C2)`, `Playback error`, `Keyboard disconnected`, `Restart` (the bridge went down while it rang, by a restart, a reboot or a power cut; it is not rung again) or the reason sent to `piano/stop` |
| `piano/alarm/error` | yes | Why the last alarm did not start: `Song not found: <name>` or `Keyboard not connected`. Cleared when an alarm starts. The alarm blueprint stops trying on a song that is not there, and goes on trying while the keyboard is off |
| `piano/alarm/last_song` | yes | The last song the alarm played |
| `piano/disconnect/reason`, `source`, `uptime`, `activity`, `time` | yes | Details of the last disconnect |
| `piano/reconnect/time` | yes | When the keyboard came back after the last disconnect, for example `22:34:29` |
| `piano/reconnect/downtime` | yes | How long it was away, counted from the first disconnect report, for example `27 seconds` |
| `piano/log` | no | Log lines, while the live log is on |
| `piano/game/status` | yes | `idle` or `lesson` |
| `piano/game/song` | yes | The song of the current lesson |
| `piano/game/score` | yes | Accuracy of the last full run with the band, in percent |
| `piano/game/piano` | yes | `connected` or `disconnected`, as seen by the lesson engine |
| `piano/game/event` | yes | `connected` or `disconnected`, as seen by the bridge. The lesson engine stops a lesson the moment it arrives, and ignores the retained value at start |
| `piano/game/note` | no | Not published at present. Meant for the progress of a demo (`3/8 15`), but the lesson engine's demos never send it. The status screen shows it when something else publishes it |
| `piano/lang` | yes | `en` or `he`, the language chosen on the lesson screen. Home Assistant answers Telegram in it |
| `piano/profile` | yes | The name of the active profile, `main` for the first one |
| `piano/practice` | yes | Today's practice of the active profile, as JSON: `{"lessons_today": 1, "minutes_today": 9, "due": 3, "streak": 4, "song": "fur_elise"}`. Published after every lesson and every 10 minutes |

## Stopping without the broker

The bridge also stops the alarm or the song when a file named `stop-request` appears in the data
folder (`/var/lib/armonico/stop-request`), so something on the same machine can stop the alarm
while the broker is away. The watchdog looks for it every second, deletes it, and stops as
`piano/stop` does. The first line of the file is the reason (`Armonico Pi stop button`, for
example), published in the same topics as the reason of `piano/stop` when the broker answers. A
file older than 30 seconds was left over from before and is deleted without stopping anything.

On the Armonico Pi the stop key or button writes this file even while the device has no broker
connection, when the bridge's own flags on the device show the alarm or a song playing (or cannot be
read). A request that finds nothing playing is only logged.

## Stopping by itself

The alarm and a song also stop with no key pressed when something they depend on goes away while
they play, on any Linux install:

| What ended | What stops the playback |
|---|---|
| The broker, unanswered for 10 seconds | The bridge's watchdog. While something plays it reads the retained `piano/status` every 2 seconds, and nothing at other times |
| The lesson engine (a crash, a kill or a stop) | The unit's `ExecStopPost` (`piano_bridge.sh --stop-if-playing`) writes the stop request above, only when the alarm or a song plays |
| The bridge's watchdog | The watchdog stops the playback on its way out, unless the bridge ended it on purpose |
| The bridge itself | systemd ends the player with the rest of the unit, and the unit's `ExecStopPost` (`piano_bridge.sh --panic`) sends all notes off and the pedal up on the keyboard |
| Armonico Pi: the keys' service | Its unit's `ExecStopPost`, as for the lesson engine |
| Armonico Pi: the screen program | The keys' service, which sees the screen's process gone without the word it leaves when it ends on purpose (a change of screens) |

Every stop goes through the usual stop path: the player ends, all notes off and the pedal up go to the
keyboard, and `piano/play/stopped_by` or `piano/alarm/stopped_by` holds the reason (`Broker lost`,
`Lesson engine stopped`, `Screen stopped` and so on) once the broker answers.

## The status page and the broker

The status page (`/status` on the lesson screen) talks to the broker from the browser, over
MQTT on websockets, so the broker needs a websocket listener: port 9001 on the broker the
installer put in place, or 1884 in Home Assistant's Mosquitto app. A broker without one shows
"Disconnected" on the page, with a line saying that no websocket listener answered.

The page is handed the bridge's own `piano` login (`/api/status-login`), which reads and writes
every `piano/` topic, so it can also send stop and the live log switch. There is no read-only
status login. Any device that has the screen code can read the login from the page, as
[SECURITY.md](../SECURITY.md) says. With Home Assistant's broker that login is the Home
Assistant user the setup made for the piano, which reads and writes every topic and can also
sign in to Home Assistant.

## The Armonico Pi device

Only on the device (`install.sh --device`, see [hardware.md](hardware.md#the-armonico-pi-device)). Its buttons are not notes, so they travel here instead of on the keyboard.

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `piano/device/state` | out | yes | `online`, or `offline` when the keys service stops |
| `piano/device/button` | out | no | `menu`, `down`, `up` or `ok`, and `menu:hold` and the like after 0.8 seconds |
| `piano/device/octave` | out | yes | The octave of the keys, 4 when the first key is middle C |
| `piano/device/pedal` | out | yes | `on` or `off`, the sustain switch of the settings |
| `piano/device/octave/set` | in | no | `1` to `7`, or `+1` / `-1` |
| `piano/device/pedal/set` | in | no | `on` or `off` |
| `piano/device/button/up` | out | no | The name of a button let go, `menu`, `down`, `up` or `ok` |
| `piano/device/power` | out | no | `press`: the power button was pressed, and the screen asks before it shuts down. Held 4 seconds, the device shuts down without the screen |
| `piano/device/voice` | out | yes | The voice of the keys and the touches, `0` to `15`, a General MIDI family (`0` is the grand piano) |
| `piano/device/keyboards` | out | yes | The USB MIDI keyboards connected now, as a JSON list of their ALSA names: `["Keystation 49"]` |
| `piano/device/test/key` | out | no | `0` to `12`, the key pressed while the hardware test is open (`0` is C, `12` the high C) |
| `piano/device/key` | out | no | `0` to `12`, the key pressed and sounded: the screen flashes that key, since in a lesson key `0` and the high C (`12`) sound the same note |
| `piano/device/test/usb` | out | no | A note played on a USB keyboard while the hardware test is open, as JSON: `{"name": "Keystation 49", "note": 60}`. It plays nothing then |
| `piano/device/expect` | in | no | The note a lesson waits for, `21` to `108`: its key sounds at the song's own height. `-1` in a lesson that waits for none yet (a demo, the count-in, a pause), and empty when the lesson is over. Sent again every 3 seconds during a lesson; one not sent again for 10 seconds counts as the end of the lesson |
| `piano/device/note` | in | no | `on 64` or `off 64`: a key touched on the screen, sounded and sent on like one of the keys |
| `piano/device/rec` | in | no | `on` or `off`: starts or stops the screen recording, as `armonico screen-rec on` and `off` do |
| `piano/device/voice/set` | in | no | `0` to `15`: the voice of the keys and the touches, kept in the settings, with a short note of it |
| `piano/device/test` | in | yes | `on` or `off`: the hardware test is open. `quiet`: the full check's guided steps, the same, and the keys play nothing and the power button held does not shut down |
| `piano/device/keys/set` | in | yes | `on` or `off`: the keys play or are silent; the stop key still stops the alarm and songs |
| `piano/device/keyboard/set` | in | yes | JSON `{"mode": "internal", "name": "Keystation 49", "out": "both"}`: `mode` is what plays (`internal` for the 13 keys, `external` for a USB keyboard, `both`), `name` the USB keyboard by its ALSA name, and `out` where the sound comes out while a USB keyboard is chosen (`device`, `keyboard` or `both`) |

## Quick tests

```bash
# watch everything
mosquitto_sub -h <broker> -u <user> -P <password> -t 'piano/#' -v

# play a song, with a source
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/play -m 'ode_to_joy|Test'

# stop it
mosquitto_pub -h <broker> -u <user> -P <password> -t piano/stop -m 'Test'
```

## Recording every message

`sudo armonico record on` writes every message of every topic to `/var/lib/armonico/mqtt/`, one file per day. Details in [commands.md](commands.md#recording-mqtt-armonico-record).
