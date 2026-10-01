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
| `piano/stop` | a reason | Stops the alarm or the song. The reason is published in `piano/play/stopped_by` |
| `piano/log/control` | `start` or `stop` | Mirrors the bridge's log to `piano/log` |
| `piano/shortcut/cmd` | `action\|chat_id\|arguments` | Shortcut and song commands, sent by the Telegram automation and the `armonico` command. Actions: `list`, `add`, `del`, `learn`, `cancel`, `songs`, `play` |
| `piano/game/cmd` | `action\|chat_id\|arguments` | Lesson, profile and screen code commands. Actions: `lesson`, `lessons`, `lessonstop`, `getmidi`, `profile`, `code`, `editmode` |

### The three commands that prove who is asking

`code` hands out the screen code, `editmode` opens the screen's editing tools, and
`profile` changes whose progress is being recorded. Each of the three is refused unless
its arguments begin with `secret:<value>`, where the value is `MQTT_ADMIN_SECRET` from
`/etc/armonico/config.env`. A refused command is written to the log and does nothing.

    piano/game/cmd   code|cli-1234|secret:<value> on

`armonico` reads the secret from the settings file by itself, so nothing changes in a
terminal. A Home Assistant automation that sends one of the three has to carry it.

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
command nothing. The broker listens on this machine only, unless the install was told
another machine needs it.

On a broker the installer did not put in place, none of this is written by anything
here. The same two rules can be added by whoever owns that broker.

A song name is the file name without `.mid`. Case does not matter.

## Events and state (published by the piano)

Topics marked *retained* keep their last value on the broker, so a subscriber sees it right away.

| Topic | Retained | Values |
|---|---|---|
| `piano/status` | yes | `online`, `offline`, `starting`, `reconnecting`, `resetting`, `disconnected`, `timeout`, `stuck`. `offline` is also the bridge's last will. `stuck` means several startups in a row failed to find the keyboard, so it likely needs a physical power-cycle |
| `piano/alert` | no | A human-readable message when the keyboard is stuck (asking for a power-cycle) and again when it comes back. Not retained, so a notification automation does not resend the last one on reload. For a Telegram message, for example |
| `piano/port` | yes | The keyboard's ALSA client number |
| `piano/notes` | no | Every key pressed, as a MIDI note number |
| `piano/pedal` | no | `down`, `hold` (held for 2 seconds), `up` |
| `piano/shortcut` | no | The name of a shortcut that was just played |
| `piano/shortcut/reply` | no | `chat_id\|text`, the answers to both command topics. A numeric chat ID is a Telegram chat, and one that starts with `cli-` is the `armonico` command in a terminal, which Home Assistant ignores |
| `piano/play/status` | yes | `playing` or `idle` |
| `piano/play/current` | yes | The song playing now, or `unknown` |
| `piano/play/source` | yes | Who started it: `Telegram`, `Alarm clock`, a name given in the command, or `MQTT` |
| `piano/play/duration` | no | Length of the song, for example `2 minutes 5 seconds` |
| `piano/play/last_finished` | yes | The last song that ended |
| `piano/play/stopped_by` | yes | `Finished`, `Stop key (C2)`, `Telegram`, `Playback error`, `Keyboard disconnected` or the reason sent to `piano/stop` |
| `piano/alarm/status` | yes | `on` or `off` |
| `piano/alarm/stopped_by` | yes | What stopped the alarm |
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
| `piano/game/note` | no | Progress of a demo, `3/8 15` |
| `piano/lang` | yes | `en` or `he`, the language chosen on the lesson screen. Home Assistant answers Telegram in it |
| `piano/profile` | yes | The name of the profile at the piano now, `main` for the first one |
| `piano/practice` | yes | Today's practice of the active profile, as JSON: `{"lessons_today": 1, "minutes_today": 9, "due": 3, "streak": 4, "song": "fur_elise"}`. Published after every lesson and every 10 minutes |

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
