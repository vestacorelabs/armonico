# Armonico for Android

The bridge on an Android phone. The phone takes the place of the Linux machine: the keyboard is
plugged into it over USB, and it talks to the same MQTT broker with the same topics
([docs/mqtt.md](../docs/mqtt.md)). Home Assistant, the Telegram automation and the `armonico`
command work with it unchanged.

What it does: the alarm, songs played on the keyboard, pause, the stop key, the sustain pedal,
shortcuts (`/seqadd`, `/seqlearn`, `/seqdel`), the song list, the keyboard's disconnect and
reconnect reports, the live log and the guarded `/pianoreset`.

The lesson engine runs on the phone too: `lessons/piano_game.py`, unchanged, with its screen on
port 8099 of the phone's address and in a window of the app, and the touch status screen
(`screen/piano-status.html`) at `/status` of the same address. It reaches the keyboard through the
bridge (`lessons.py` gives it mido ports that the bridge feeds and sends from), and shares the
same flags with the bridge as on Linux, so keys trigger no shortcut during a lesson and the alarm
stops a lesson.

## Two ways to run

The switch at the top of the settings chooses one.

| Mode | What it is for | What runs on the phone |
|---|---|---|
| Broker on the phone | No Home Assistant and no broker on the network | The bridge and a small MQTT broker (`broker.py`) set up as `install.sh` sets up Mosquitto: port 1883 for the phone only (127.0.0.1), websockets on port 9001 for the status screen on the network, and the two logins with their rules. The Telegram bot works directly |
| Broker on the network | A broker already exists, Home Assistant's or another | The bridge, connected to that broker, as on Linux |

In both modes the app can read a Telegram bot itself (`telegram.py`), with the same commands
and answers as the automation in `home-assistant/piano.yaml`. The bot answers one chat: the one
that sends `/start` within 2 minutes of "Connect my chat". Only one program can read a bot at a
time, so if Home Assistant already reads it, use a different bot here.

The built-in broker speaks MQTT 3.1.1 with logins, `+` and `#`, retained messages kept on disk,
last wills, QoS 0 to 2 from publishers (delivered at QoS 1 at most), keepalive and websockets
(subprotocol `mqtt`). It has two logins, as the Linux install: `piano` may read and write
`piano/#` only, `piano-rec` reads every topic and writes none. A message outside a login's rules
is acknowledged and dropped, as Mosquitto does. It has no persistent sessions, no MQTT 5 and no TLS.

The lesson screen (8099) and the status screen's websockets (9001) answer on whatever Wi-Fi the
phone is on, unencrypted, protected by the screen code and the broker logins. On a public or
shared network (a hotel, a café, a hotspot others use), everyone on it can reach them: stop the
service there, or keep the screen code on.

## The armonico command

The log tab has a command screen with the commands of `armonico` on Linux (`cli/armonico.py`),
typed as there: `status`, `check`, `play`, `songs`, `stop`, `pause`, `shortcut`, `lesson`,
`getmidi`, `profile`, `code`, `text export`, `update`, `restart`, `logs`, `record`, `unstick`,
`reset` and `version`. `help` lists them. The Telegram names (`/pianoplay` and the rest) work too.
The piano commands travel as a Telegram command does, to the chat `app-console`, and their answers
come back to the screen and never to the broker.

| On Linux | Here |
|---|---|
| `armonico check` | The checks a phone has: the bridge, the broker and its websockets, MQTT, the keyboard, the lesson screen and the songs it cannot read, Telegram, the recorder and free space. Saved as a report in `reports/` of the data folder |
| `armonico record on/off/follow` | `recorder.py`, the same file per day as `bridge/mqtt_recorder.py` in `mqtt/` of the data folder, the admin secret masked, 14 days kept. Also a switch in Settings › Tools |
| `armonico unstick` | The same bytes as `bridge/piano_unstick.sh`: a lone F7, GM System On, 16 channels reset, a test note |
| `armonico reset` | As `bridge/piano_reset.sh`: unstick, the keyboard opened again (the USB reset there), unstick again, the broker and the recorder again |
| `armonico update` | The newest release on GitHub, the address `setup/update.sh` asks. When a release is published, the workflow attaches the APK to it, and the answer links to it |
| `armonico restart`, `logs` | The bridge restarted, the log view |

The system commands that belong to a Linux machine (`reconfigure`, `uninstall`, `purge`,
`telegram`, `integrations`, `forget-tokens`, `device-mode`, `screen-rec`) are the app's settings,
Android's uninstall, or the Armonico Pi's.

## How it is built

| Part | Where | What it does |
|---|---|---|
| The bridge | `app/src/main/python/armonico_android/bridge.py` | The logic of `bridge/piano_bridge.sh` in Python: MQTT, shortcuts, the alarm, songs, disconnects |
| The broker | `app/src/main/python/armonico_android/broker.py` | The MQTT broker of "Broker on the phone" |
| The bot | `app/src/main/python/armonico_android/telegram.py` | The Telegram bot read by the app |
| The commands | `app/src/main/python/armonico_android/console.py` | The `armonico` command of the command screen |
| The recorder | `app/src/main/python/armonico_android/recorder.py` | `bridge/mqtt_recorder.py` inside the app |
| The lessons | `app/src/main/python/armonico_android/lessons.py` | Runs `lessons/piano_game.py` with the bridge as its keyboard. The engine's files are copied into the app at build time and unpacked on the phone |
| The lesson window | `LessonsActivity.kt` | The lesson screen in the app, let in with the screen code |
| The keyboard | `MidiLink.kt` | Opens the USB keyboard through Android's MIDI service and passes raw bytes both ways |
| The service | `BridgeService.kt` | Keeps the bridge running in the background, with a notification that has a stop button |
| The screen | `MainActivity.kt`, `Ui.kt` | Home, songs, settings, the commands and the log, in the lesson screen's design language (colors, Heebo, buttons, pills, switch, cards) |
| Start-up | `BootReceiver.kt`, `UsbAttachActivity.kt` | Starts the bridge when the phone starts and when a keyboard is plugged in |

Python runs inside the app through [Chaquopy](https://chaquo.com/chaquopy/) (MIT license), with
the same `paho-mqtt` and `mido` versions as `requirements.txt`. The songs in `setup/music/` and
`lessons/piano_lang.py` (the Hebrew layout of the Telegram replies) are copied in at build time,
so each exists once in the repository.

The service is a foreground service of type `connectedDevice`, the type Android gives an app that
works with a USB device. It holds a partial wake lock, so a key or an alarm time never waits for
the phone to wake up.

## Building

The APK is built by GitHub Actions (`.github/workflows/android.yml`) on every push that changes
the app, and by hand from the Actions tab. The APK is in the run's artifacts.

Locally, with JDK 17, Python 3.13 and the Android SDK:

```bash
cd android
./gradlew assembleRelease
```

Pictures of every tab, in both themes and both languages, are drawn on the computer with no
phone (Robolectric and Roborazzi) into `app/build/shots/`:

```bash
./gradlew testReleaseUnitTest
```

### Signing

An update installs over the app, keeping its settings, only when every build is signed with the
same key. The workflow signs with the key in four repository secrets
(Settings › Secrets and variables › Actions):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_B64` | The keystore file in base64 (`base64 -w0 armonico-release.keystore`) |
| `ANDROID_KEYSTORE_PASSWORD` | The keystore's password |
| `ANDROID_KEY_ALIAS` | The key's alias |
| `ANDROID_KEY_PASSWORD` | The key's password |

Without them the APK is signed with a debug key that changes on every run, and the workflow
warns about it.

## Settings

| Setting | Linux equivalent | Default |
|---|---|---|
| Mode | `MQTT_LOCAL` | broker on the phone |
| Broker address, port, user, password | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` | none, `1883` |
| Telegram token | the bot in Home Assistant | empty |
| Keyboard | `MIDI_DEVICE` | the first USB keyboard found |
| Stop key, lowest key, highest key | `STOP_KEY`, `KEY_LOWEST`, `KEY_HIGHEST` | `36`, `36`, `96` |
| Language of the Telegram replies | `UI_LANG` | English |
| Admin secret | `MQTT_ADMIN_SECRET` | drawn on first start |
| Start with the phone | the systemd unit being enabled | on |

The admin secret has to match `armonico_admin_secret` in Home Assistant's `secrets.yaml` for
`/pianoreset` to work. A phone that replaces a Linux machine can take that machine's value.

## Requirements

Android 7.0 or later, with USB host (OTG). The phone has to stay powered while it is the bridge.
A port in host mode gives power to the keyboard instead of taking it, so charging at the same
time needs a USB-C OTG adapter or hub with PD pass-through, and a phone that charges while it is
the USB host. Not every phone does.
