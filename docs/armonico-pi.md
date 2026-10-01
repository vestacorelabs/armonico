# Armonico Pi

Armonico Pi is a small keyboard of its own: a Raspberry Pi 4 in a printed case with 13 keys (8 white and 5 black, from C up to the high C, one octave above it), 4 buttons and the BIGTREETECH Pi TFT50 screen on DSI. Its sound goes out on the headphone jack, with a USB sound device or Bluetooth beside it. It is powered over PoE through a PoE HAT, or over USB-C without one. It runs the whole of Armonico, so lessons, songs and shortcuts work on it as on any keyboard.

The screen is the 5" BIGTREETECH Pi TFT50 on the Pi's DSI port. The printed case, the touch and the layout of every screen are built and tested for it, so the device is built with that screen. The device draws and plays on it only: an HDMI screen and HDMI sound are off. The HDMI parts of the code are kept as comments marked "HDMI off (DSI only)".

## What it needs

| Part | Details |
|---|---|
| Computer | Raspberry Pi 4 |
| Screen | BIGTREETECH Pi TFT50, 5", on the DSI port |
| Keys and buttons | 13 key switches (8 white, 5 black) and 4 buttons, wired as in [Wiring](#wiring), plus a power button on pins 5 and 6 |
| Case | Printed |
| Power | PoE through a PoE HAT, or USB-C |
| Sound | Speakers or headphones on the headphone jack, a USB sound device, or Bluetooth |
| Storage | A microSD card with Raspberry Pi OS Lite |
| Network | A network cable or Wi-Fi. Needed for the install; after it the device also works offline on the Local broker (see [Away from the broker](#away-from-the-broker)) |

## Wiring

Every switch has one leg on its own GPIO pin and one on a shared ground chain, with the pull-up inside the Pi, so there are no resistors. Pin numbers are the physical pins of the 40-pin header. GPIO 4 (pin 7) stays free: a HAT such as the Argon one uses it for its own power button. The key C is on GPIO 15 (pin 10), the serial port's receive pin, so the serial port stays off; the install turns `enable_uart=1` off if it is there.

| Part | Pins | GPIO |
|---|---|---|
| White keys C, D, E, F, G, A, B, high C | 10, 11, 12, 13, 15, 16, 18, 33 | 15, 17, 18, 27, 22, 23, 24, 13 |
| Black keys C#, D#, F#, G#, A# | 19, 21, 22, 23, 24 | 10, 9, 25, 11, 8 |
| Buttons ☰, ▼, ▲, ✓ | 26, 29, 31, 32 | 7, 5, 6, 12 |
| Ground chain | 39 | GND |
| Power button | 5 and 6 | 3 and GND |

Another wiring is set in `config.env` with `DEVICE_KEY_PINS` (13 GPIO numbers, C to the high C; the default is `15,10,17,9,18,27,25,22,11,23,8,24,13`), and `DEVICE_BUTTON_PINS` (`menu:7,down:5,up:6,ok:12`).

## Install

1. Put Raspberry Pi OS Lite on the microSD card, for example with Raspberry Pi Imager, with SSH turned on and a user of your own.
2. Start the Pi with the network cable plugged in, and connect to it over SSH.
3. Get Armonico and run the installer with `--device`, then restart:

```bash
git clone https://github.com/vestacorelabs/armonico.git
cd armonico
sudo ./install.sh --device
sudo reboot
```

The release zip from the [Releases](https://github.com/vestacorelabs/armonico/releases) page works the same way: unpack it and run the same two commands in its folder.

On Raspberry Pi OS Lite this installs everything the normal install does, and then the parts below. The setup does not ask for a keyboard or measure one: it is always `Armonico Pi` with 13 keys. The machine is remembered as a device (`/etc/armonico/device`), so updates and `--reconfigure` keep all of this, and `--uninstall` and `--purge` take it away again: snd-virmidi, the sound settings, the power button line, `fsck.repair=yes` on the kernel command line when the install added it, I2C back on if it was on before, and with `--purge` the device's packages too.

| Part | What it is |
|---|---|
| `armonico-device` | `device/armonico_pi.py`: reads the keys and the buttons on GPIO, plays the grand piano and the other 15 General MIDI voices and the drums from samples, and shows up in ALSA as the keyboard `Armonico Pi`. The setup lists it like a USB keyboard |
| `armonico-screen` | greetd, which logs the screen user in and runs `device/session.sh`: `device/screen.py` straight on KMS, on the DSI screen |
| `snd-virmidi` | A virtual MIDI device, `hw:7,0`, that only goes out: the lesson engine plays on it (`AMIDI_DEVICE` in `config.env`) and the device sounds it |
| PipeWire | The ALSA default: PipeWire, run as the service user's own, mixes every program into the headphone jack, a USB sound device, a Bluetooth one or any of them together (`device/pipewire`, see [Sound outputs](#sound-outputs)). The Pi's HDMI sound cards are left off. The installer writes no `/etc/asound.conf`; one written by someone else is kept, with a warning, since a `pcm.!default` in it skips PipeWire |
| `config.txt` | `dtoverlay=gpio-shutdown,gpio_pin=3` for the power button, and I2C off. One restart applies it |

The piano samples are the Salamander Grand recordings without loss (FLAC, `device/samples`), rendered once to one file per key, each moved by a semitone at most; the lesson screen's MP3s are used only for a note whose FLAC is missing. The other voices and the drums come from `device/samples/gm` (FluidR3_GM), and a note of theirs is made when first played.

## The first start

After the restart the setup opens on the device's own screen, and is done by touch:

1. **Language.** English or Hebrew.
2. **Wi-Fi.** Only when no network cable has an address. It can be skipped.
3. **Place.** The city, for the time zone, the sky and the weather.
4. **Time.**
5. **Sound.** Sound on, or No sound.
6. **Connection.** Local broker, the broker on the Pi, installed and tested right there. Or External broker, one that already exists on the home network. Its keys (Home Assistant's access token is about 180 characters) are entered from a computer or a phone over SSH, as in a regular install: the step shows the address and the user to log in with (PuTTY, a phone SSH app, or `ssh user@address`) and the command to run there, `armonico connect`. It finds Home Assistant (more than one found: you choose), asks for the token, makes the Mosquitto app and the piano's login from it, adds the sensors and automations, then sets up the Telegram bot, each step as the installer asks it. At the end the device switches to that broker and the screen goes on by itself, on the next step. When the SSH server is off, the step offers to turn it on, after asking. **Enter on this screen** keeps the old way: the broker's address and login, then Home Assistant and Telegram, typed or pasted on the device. The same SSH window opens from Settings › Network and links › Broker and services › Connect over SSH.
7. **Look.**
8. **Ready.**

Everything chosen here can be changed later in Settings, and the setup can be opened again from System. When the setup ends, the opening tour shows how the device is used (see [Using it](#using-it)). The device itself is the keyboard: the setup does not ask for one and does not measure one.

## Using it

| Control | Rest, free play | Songs, settings | Lesson |
|---|---|---|---|
| Keys | Play, in the octave on the screen | Play | The lesson. The keys stay put for the whole lesson: key 1 is always C and the high C key is number 1 too. Each song is placed on the 13 keys first (see below), and a note that is still past them sounds at its own height on the key of its name, with an arrow in the corner |
| ☰ | Next screen, held: back to rest | Next screen | Held: leave the lesson |
| ▲ ▼ | Octave | Move, or the volume | |
| ✓ | Songs | Start a lesson, held: play the song. In settings: change the item | |

The screen is a touch screen, and a bar of 4 touch buttons along its bottom does what the 4 buttons do, so the device works before any button is wired. One touch on a song starts its lesson, and one touch on a setting changes it. What cannot be taken back asks first, with Yes and Cancel: switching the broker, which restarts every service, and leaving a lesson, by touch or with ☰ held. ✓ or a touch on Yes goes on, any other button or 12 seconds without an answer cancels. Over the idle video the bar stays hidden, so the subtitles stay clear, and a touch brings it up for 5 seconds. During a lesson the bar holds one button that leaves the lesson.

**Lessons on the device.** Before a lesson the whole melody moves to the height where the most of its notes land on the 13 keys; its steps stay the same, only the height changes. A part that still goes past the keys moves an octave as a whole, before it is ever played, so what you hear is what the keys play and you never change octave yourself. In a tune wider than an octave, such as a minor song from the 5 below to the 3 above, the few notes still outside move an octave onto the key of their name, and they are played there too: every note sounds where its key is. Parts are learned by ear, in five rounds from the easiest: **watch and listen** (the part plays twice while its keys light up one after the other, with their numbers, and you play it with each key lit in gold), **the key leads** (the part is played once, then the next key lights in gold and waits for you), **note by note** (one note, then two, three, up to the whole part), **by ear** (the part is played twice, to its end even if a key is touched, and you play it back, its first note in gold) and **from memory** (no demo, no help). With **Gradual, by itself** on, where a part starts follows the **Level** in the same settings (see [Levels](learning-method.md#levels)): a beginner's new part from watch and listen, an intermediate one from the key leads, an advanced one straight by ear (note by note only from 5 notes up); a part you knew before that slipped starts a round or two later; two rounds by ear that do not come out right go back to note by note. After a wrong key it says higher or lower and plays the note again after the one before it; a second miss says it again and lights the key in gold; a third plays the part again from the start. Each round and each of these helps can be turned off in Settings › Playing › Lessons, for each profile; one round at least stays on. A line over the keys shows the part's rounds and the one you are in. **Before it fades**, also there, plays a part or two of other songs that are fading at the end of a lesson. The songs list shows each song's level. Then the part is played from memory, and parts join into larger ones as before.

**The game.** ☰ goes from Free play to the game. The notes of a song fall down the lane over their key and land on the key itself, on their time in the song. A note that landed waits there, lit, and the song waits with it until the key is played, so nothing is ever missed. A key played up to 0.4 seconds before its note lands plays it too. A wrong key flashes red, breaks the streak and lowers the accuracy, and the song goes on. Each note says how close it was to its landing: perfect, good or nice. The song is a song you started that is fading, at random among them; a song not started is never played in the game, and with no song started yet it is the easiest one. Any key starts, ✓ or any key after the end plays another song. The device's keys, the keys on the screen and a USB keyboard all play it.

**A USB MIDI keyboard** is chosen in Settings › Playing › Keyboard. **Plays through** says where the sound comes out while it plays: **Automatic** (the default) is the keyboard's own sound when it takes MIDI in, else the device, so every note is heard in one place; **Device**, **USB keyboard**, or **Device and keyboard**, which plays everything twice, on the device and on the keyboard.

The **Bluetooth** quick check looks without changing anything: whether `bluetooth.service` runs, the radio's switch, whether the adapter answers (root's `bt-status`, asked only to look, never turns it on), the devices saved and the one connected (a connected one with no sound output, such as a watch, is a warning), the dropouts of a connected one, and Bluetooth gone with the Wi-Fi chip. A result that needs the Bluetooth page links to it.

The stop button is chosen in the device's settings, or in the settings of the lesson screen, where the row shows only on the device. It can be any of the 4 buttons or any of the 13 keys (✓ by default), never the power button. While the alarm rings or a song plays, it stops them and does nothing else: a key chosen for it does not sound then. The rest of the time it is an ordinary button or key.

The sustain pedal is a switch in the settings.

After the setup finishes for the first time, the character chosen in the settings goes over the device in 7 short steps: it reaches out with a pen and loops around one part at a time while the rest of the screen is dimmed, with one line in a bubble. The steps go through the screens: the 4 buttons and ☰ on the rest screen, ▶ in Songs, the lit key of a lesson, the keys of free play, the card in Settings where the character is changed, and the character itself, which ends the tour when touched. ✓ or a touch on the bubble goes on, ☰ goes a step back. This first tour has no Skip. It waits while an alarm, a song or a lesson plays and goes on from the same step afterwards. The first time Songs, Free play and a lesson open, a one-line tip shows in a corner for 8 seconds, once each. Settings › System › Opening tour shows the tour and the tips again, and that tour can be skipped.

The hardware test (one of the ways into Settings › System › Device test, also linked from the full check's summary), checks everything after the soldering in one go: each of the 13 keys and the 4 buttons lights up with its GPIO number when pressed, every touch leaves a dot where the panel felt it, two touch buttons play a tone on the left or the right speaker only, and a gold frame shows whether the whole picture is on the panel. The power button is the one part it cannot show, since a press shuts the Pi down, so it is tested last. ☰ held or Done leaves it. `armonico status` checks the rest: the services, the broker, the samples, the sound card and the jack's level, the lesson output, the keys on the sequencer, the display, `config.txt`, the power supply, and every song that cannot be a lesson, with the reason.

The ghost run, on by default and switched in the settings, races the previous full run of the same song: during a full run a ring marks the key the last run had reached at that moment, and at the end the screen says how many keys ahead or behind it this run finished. The run kept is the last one that reached the end, one for the run on your own and one for the run with the band, in `ghosts.json` of the profile. Starting a song over (`/lesson <song> reset`) forgets it.

The device publishes its buttons and its octave on MQTT (`piano/device/button`, `piano/device/octave`), see [mqtt.md](mqtt.md).

## Full check

**Settings › System › Device test** is one window for every check, 6 ways in: automatic and by hand, automatic only, by hand only, the hardware test, the deep check, and a quick check of one area (sound, touch, keys and buttons, network, Bluetooth, screen, services); the same report is saved by the first three and the quick check. The CPU temperature shows in a corner of every screen of the window, read every 5 seconds: green up to 60°C, gold to 70°C, red above. No screen of a check moves on by itself: each waits for ✓, Next or Finish. The full check below is its automatic and guided parts. It changes nothing on the device: no alarm, no backup, no message, no setting and no fix, unless Fix is touched in the summary for a check the diagnosis already knows how to fix. The only file it writes is its report.

First 21 automatic checks, about a minute, each row filled as its area ends, a few at a time in the background: the services, the broker and the bridge's status kept on it (`piano/status` online, not a stale offline), the lesson screen and its song list, the network and Wi-Fi (NetworkManager, the adapter, the radio, the signal), the internet services the device uses (GitHub for updates, Open-Meteo, the NASA feed, each with its response time, only with a network), the time and the time zone against the city, power (under-voltage and slowdown now and since boot, from `vcgencmd get_throttled`), the temperature, memory and swap and an Armonico process using too much of it, the SD card (free space, not read-only, no read or write errors in the kernel log), the sound and its volume, the samples (the piano, the 15 other voices and the drums), MIDI and a USB keyboard, the GPIO pins (all inputs, none read as pressed; with the automatic checks alone, the keys and buttons are marked "checked by hand", not as a warning), the display, its fonts and `config.txt`, the settings, progress, profiles and learner files as valid JSON (a damaged one by its name), errors in the log of each Armonico service in the last 24 hours, kept line by line, the last backup and the backups folder, the alarm song and the local stop path the bridge reads, whether a Telegram bot token is kept (the root action `telegram-kept` answers true or false, never the token), and a newer version. What cannot be read without root (the system log and the kernel log, unless the screen's user is in the `systemd-journal` group) is marked as not checked, never as failed.

The list of the automatic checks stays on the screen when they end, until Next (to the steps) or Finish (to the summary). Then 9 guided steps, each skippable with ☰: the 4 corner circles in order (a touch that lands on another circle shows a turned panel), the 13 piano keys, the high C named as such (a key not pressed within 30 seconds counts as not responding), the 4 buttons under the screen, the power button (the shutdown question shows, and its Yes shuts nothing down), a tone on the right speaker only and on the left only, a short piano phrase at the current volume, one key of a USB keyboard when one is connected, and the brightness at 100, 75, 50 and 25%. A step that ends by itself (every circle, key or button done, or its 30 seconds over, or the power button's question answered) keeps its result on the screen, in green or red, until Next. During the steps the keys are only named (`piano/device/test` is `quiet`: they play nothing, send nothing on, and the power button held 4 seconds does not shut down), and leaving the screen at any point turns test mode off and brings the brightness back; the volume is never changed.

The summary lists the failures first, then the warnings, each with one line of what to do, a Fix where the diagnosis can fix it, a link to the hardware test for the keys and buttons, and Details for the rest, then how many passed. A touch on any card opens its details: what was found, what to do, and the lines behind it, the kernel log's read and write errors and a service's log errors as the system wrote them (left to right), or every service the device runs with its state, how long it has been up, its memory and its restarts. **All results ›** on the passed card lists every check, the passed ones too, each opening its details. **Send via Telegram**, shown only while a bot token is kept, asks root to send a short summary in a fixed form (the counts and the failed and warning lines, addresses taken out) to the device's chats with the kept token, at most once every 10 seconds. The report is a text file in English, `/var/lib/armonico/device/checks/YYYY-MM-DD-HHMM.txt` (the last 10 are kept), with the version, the date, every check with its result and detail and the lines behind it, and the guided steps' answers. `armonico check` runs the automatic part in a terminal and saves the same report. The first start after an update offers the check in one line at the top of the rest screen, once for each version; a touch opens it.

The same window has a **Deep check** (also `armonico check --deep`), and a boot after a power loss opens a recovery screen first. Both are described in [power-loss-and-deep-check.md](power-loss-and-deep-check.md).

## Settings

The settings open in 8 groups: Look and screen, Sound, Playing, Place and time, Network and links (with the broker, Home Assistant and Telegram behind it), Backup and restore, System, and About. The sections below cover the parts with the most behind them.

## Sound outputs

**Settings › Sound › Output** lists the USB sound devices plugged in (headphones, a USB sound card, a USB speaker) and the Bluetooth ones connected, each by its own name. The headphone jack (AUX) is not in the list and has no setting: it is always on. Plugging a cable into it or not is your choice. It is the base of the sound, and every other output, USB or Bluetooth, plays beside it. Each device is switched on or off, and **No sound** silences them all. A set of outputs is a combine sink made with `pactl load-module module-combine-sink` and made again at every start. A device that is plugged in or connects becomes an output at once. When a chosen device goes, the sound stays on the rest, and the screen says only that it disconnected: nothing moves, the headphone jack plays all along. A USB device keeps its own level (WirePlumber's), while the Pi's own outputs are set to full and the device's volume sets what is heard. No stream gets back a volume, a mute or an output from an earlier time (`node.stream.restore-props` and `node.stream.restore-target` are off in `device/pipewire/80-armonico-wireplumber.conf`). The setup's sound step has Sound on and No sound.

## Bluetooth sound

**Settings › Sound › Output › Bluetooth** scans for headphones and speakers, pairs one (the device offers itself to the Pi as a device with no screen and no keyboard, which is what headphones and speakers expect), trusts it so it can connect by itself when it is switched on, and connects it. A connected device is one more output in the list, and plays beside the headphone jack and together with any USB device. A speaker that does not connect by itself is connected again from the screen while it is in the sound choice, with a wait that grows from 20 seconds to 2 minutes. A saved device that shows as connected but has no sound output in PipeWire (it connected before PipeWire was up, or PipeWire restarted under it) is asked for its sound profile without dropping the link: every 30 seconds the screen looks for one, asks at once in the first 5 minutes after boot and later after 2 silent looks in a row, at most 3 times until the device has an output or disconnects. If it is still silent after that, the screen turns the Bluetooth radio off and on again, at most twice an hour, and says so. A device left on its headset profile, or with its profile off, is set back to its best A2DP profile. A chosen device that dropped and came back between two looks is a new node in PipeWire, and the mix is made again for it. Sound goes out as A2DP only: a Bluetooth device is never moved to its mono headset profile when a recording starts (`bluetooth.autoswitch-to-headset-profile = false`), and its output stays open instead of being suspended when idle (`session.suspend-timeout-seconds = 0`), both in `device/pipewire/80-armonico-wireplumber.conf`. The Pi 4's one radio serves Wi-Fi and Bluetooth on 2.4 GHz, so the device on the network cable is the quiet case; the full check's Sound area counts PipeWire's dropouts on a connected Bluetooth output and reads the log of bluetoothd and PipeWire, and says what helps (the cable instead of Wi-Fi, getting closer, a USB Bluetooth adapter with `dtoverlay=disable-bt`). The installer adds bluez and `libspa-0.2-bluetooth`, puts the service user in the `bluetooth` group (undone by `--uninstall`) and turns the adapter on.

## Wi-Fi

**Settings › Network and links › Wi-Fi** lists the networks around: one row for each name (the strongest of its access points), its signal in 4 bars, a lock on a secured one and a ✓ on the one in use, and a row for a hidden network, whose name is typed. The list is scanned again every 15 seconds while the page is open, and not at all while it is closed. The password is typed on the touch keyboard, which has a symbols layer (the `!#$` key) and a Show button; an open network or a saved one connects without it. The connection is given 20 seconds; once up, the screen shows the network, its IPv4 address and the lesson screen's address, and a wrong password brings the keyboard back with what was typed. **Saved networks** lists the networks NetworkManager keeps, marks the one in use, and **Forget** asks before it deletes one. The setup has a Wi-Fi step right after the language, only when no network cable has an address; it can be skipped, since the device works offline on the Local broker.

Wi-Fi goes through NetworkManager (`nmcli`), which Raspberry Pi OS uses since Bookworm, by way of the root unit (`device/root_actions.py`: `wifi-scan`, `wifi-connect`, `wifi-saved`, `wifi-forget`, `wifi-status`), which checks every value before `nmcli` sees it. The password never appears on a command line: a new connection is added without it, and `nmcli connection up ... passwd-file` reads it from a file only root can read, removed right after. NetworkManager keeps the password with the connection; Armonico stores none. Without NetworkManager the page says Wi-Fi is not available on that system.

**The chips themselves.** The Wi-Fi and Bluetooth switches turn the radio off and leave the chip powered with its driver loaded. The last two cards of **Settings › Network and links**, **Wi-Fi chip** and **Bluetooth chip**, turn the chip itself off, or on again, from the next start: the root action `radio-chip` writes the Raspberry Pi overlays `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` into one block of its own at the end of `/boot/firmware/config.txt`, marked `# >>> Armonico: radio chips`, and removes the block when both are on again; nothing else in the file is touched. The two halves of the chip are separate for the switches, but not always for the overlays: `disable-wifi` makes the firmware pull the chip's WL_ON line low, and on boards where Bluetooth's BT_ON follows that line, Bluetooth goes off with Wi-Fi ([raspberrypi/firmware#1611](https://github.com/raspberrypi/firmware/issues/1611)). The confirmation says so, and with the Wi-Fi chip off the full check's network area looks for the Bluetooth adapter and reports it when it is gone. To keep Wi-Fi quiet without touching Bluetooth, the Wi-Fi switch is enough. On a board with Bluetooth of its own (every Raspberry Pi since the 3, the 400 and 500, the Zero W and Zero 2 W), the Bluetooth page never says there is no adapter: it says the chip is off in `config.txt` (by Armonico or by a line written by hand), that it went off with the Wi-Fi chip, or that it is not answering. For the first two, ✓ turns the chip on from the page, after a question, and it works after a restart. A line written by hand is not deleted: it is made a comment, with a note that it was turned on from Armonico's settings. Bluetooth off also disables `hciuart.service` when it was enabled, and enables it again with the chip. Each change asks first, says that it takes effect after a restart, and offers the restart. Turning the Wi-Fi chip off while the device is connected through Wi-Fi says that it will have no network after the restart until a network cable is connected. A chip turned off by a line written by hand in `config.txt` is left as it is, and the screen says so.

## Connection modes: Local broker / External broker

**Local broker** uses the MQTT broker installed on the Pi. Armonico's bridge, lesson engine and device communicate through that broker on the same machine; internet access and another computer are not required for this local connection.

**External broker** points those services at an MQTT broker that already exists on the home network, for example the broker used by Home Assistant. The lesson engine, its screen on port 8099, the profiles and the progress still live on this Pi: this switch changes the MQTT connection, not the computer running Armonico. Messages are visible to other clients on the same broker. Profiles and progress are not shared between installations, and two Armonico installations on one broker use the same `piano/...` topics and answer each other's commands, so one broker serves one installation.

The external broker is set up once, with a guided command that asks for the address, port, username and hidden password, then tests whether the broker accepts the login:

```bash
sudo armonico device-mode setup-external
```

If the test succeeds, the external connection is saved and activated immediately; the services restart. The previous profile is saved automatically too, so switching back later does not require entering MQTT details again.

On the device, open **Settings › Network and links › Broker and services** to switch between **Local broker** and **External broker**. A tap sends a local request; the device-mode service applies the saved broker profile and restarts the bridge, lesson engine and device screen (and the recorder, if enabled). The Pi does not move to another computer, and you do not have to enter the address or password again. If an external broker has not been configured yet, the screen tells you the one-time setup command.

For a Home Assistant broker, use the Home Assistant machine's local IP/host and the MQTT user/password configured for that broker. The setup command checks that the supplied login is accepted before it changes the active mode. This is a one-time connection setup; it is not required each time you toggle back and forth.

The CLI equivalents are:

```bash
sudo armonico device-mode local-broker
sudo armonico device-mode external-broker
sudo armonico device-mode
```

### Away from the broker

On the External broker the bridge and the lesson engine talk only through that broker, so when it cannot be reached (the device away from home, or Home Assistant restarting) the keys still sound and the stop button still stops the alarm and a song, but lessons, songs started from the device and the alarm clock wait for it. An alarm whose minute comes while the broker is away is not lost: the screen says it cannot ring, and it rings as soon as the broker answers, up to 59 minutes late. To take the device out of the house, switch to **Local broker**; once the local broker has been set up, the switch needs no network. The device never switches by itself: after a minute with no connection to the external broker it offers the local one, with Yes and Cancel, once until the connection comes back, and only when a local broker was set up before. After a switch made from that offer it looks for the broker at home every 5 minutes, and when it answers it offers the way back, once. **Settings › Network and links › Without a network** shows what works on the broker in use.

## Rest screen

The rest screen has exactly two choices in Settings:

- **Armonico video**: the bundled `device/media/armonico-japanese.mp4` (1280x720, 30 frames a second, scaled to the screen by the same pipeline); audio and video play together in one GStreamer pipeline. Each repeat pauses for one second before restarting.
- **Animation**: the built-in Armonico house-and-sky animation, without video playback.

On the animation the character is there to play with. A touch on it moves it, a different move each time, and a moment later it says a short line in a bubble, put together from parts: about the time, the streak, the last song, a pause, or itself. A line is not said twice until the screen restarts. Its face follows what it says: smug, angry when touched too often, offended, surprised after a pause, proud, sleepy late at night. A few quick touches make it laugh, and the fifth spins it round. Now and then it plays 3 notes and waits for them on the keys; touching it while it waits plays them again. A touch on a star lights it and rings a note, higher for a higher star.

After the minutes set in **Settings › Look and screen › Screen sleep** (1, 5 or 15, or never) without a touch, a key or a button, the rest screen goes to sleep: black, with a medium clock, one line under it with the next alarm (when the device's own alarm rings within 24 hours) and the weather now in the chosen city, the Moon in its real phase, a few stars and the character asleep on a hill. Everything is dim, about a quarter of full white, and the whole picture drifts by a few pixels over minutes so nothing stays on the same pixels. The weather comes from Open-Meteo (free, no key), asked at most every 30 minutes and only with a network; without one the weather is left out. The sleep screen is drawn twice a second to keep the Pi's load low. The first touch, key or button only wakes the screen. With night mode and Screen at night set to off, the backlight is off while the screen sleeps, as before.

**Settings › Look and screen › Night mode** is one setting that lowers the screen's brightness and the volume together. The alarm always rings at 100%, at night too, and the volume before it comes back when it stops. Its first row is a switch: one touch turns night mode on or off. **Automatic** cycles through Sunset and sunrise (by the chosen city), Chosen hours (a row **Hours** with the two times as pills, each touch moves a time 30 minutes later, and a span that crosses midnight works) and Manual (it stays on until the switch is turned off). **Dimming** has 3 steps: 75% brightness with the volume at 70% at most, 50% with 40%, and 25% with 20%. The line over the rows says whether it is on and when. The screen is never brighter than the day's Brightness, a volume already under the cap is left alone, and the day volume comes back when night mode ends or its dimming is lightened. While the switch is off nothing is lowered and the rows under it are dimmed and cannot be touched.

The video stops while a lesson, alarm or song is active, when a key is played, or when the screen leaves rest. It starts again when the device returns to rest. After a key was played it waits for a touch or a button, and comes back by itself when a lesson, alarm or song ends. There is no YouTube downloader or general file browser in the Device UI.

## Screen recording

The screen recording (`armonico screen-rec on`) records the screen to a file, for guides and videos: the picture, the device's own sound and a USB microphone, so a recording can carry a narration. The screen program hands each frame it shows to ffmpeg through a pipe, at most 30 a second, so the file holds exactly what was drawn; reading the picture back from the graphics memory (`kmsgrab`) was tried first and came out choppy, since every frame was copied out of uncached video memory on the CPU the screen needs. The frames wait in a queue of 2 for a writer thread: when ffmpeg falls behind, a frame is dropped and the screen never waits. Each frame is stamped with the clock when it was drawn, and the video is made a steady 30 frames a second, a frame repeated where the screen drew fewer (the sleep screen draws 2), so time never jumps. x264 (ultrafast, crf 18) into an `.mkv`, which stays readable if the recording is cut off; ffmpeg runs at a lower priority (nice 10) than the screen.

The sound is the device's own (the monitor of PipeWire's default output, so the keys, the songs and the video as clean as they were played) and the microphone, all on one clock. With a microphone the file has 3 sound tracks: first both mixed, the default track, which any player plays (most play the first track only); then the device's sound alone and the microphone alone, to be moved and set louder or softer in an editor. The microphone is the first USB input PipeWire has; a USB headset whose card was left on an output-only profile is moved to its best profile with an input, and the microphone is unmuted and set to full level. The message at the start names the microphone, or says none was found. A USB headset plays the device's sound only when it is chosen as an output (see above), so plugging one in for its microphone leaves the speakers as they are.

`armonico screen-rec off` stops it. The file is closed, then moved to a USB stick when one is plugged in (root mounts it, copies into `Armonico recordings/`, writes it to the stick for good, checks the size, and only then removes it from the SD card); with no stick it stays in `/var/lib/armonico/device/recordings/`, and `sudo armonico screen-rec export` moves the ones left there later. ffmpeg's own output is in `last.log` in that folder. From a terminal: `armonico screen-rec on|off` (sent to the screen over MQTT, `piano/device/rec`), `sudo armonico screen-rec export|list`. The root action is `rec-export` (`device/root_actions.py`, the work in `device/screen_rec.py`); it opens the recordings folder and the stick's folder without following a link, reads only regular files and writes only new ones.

## Backup and restore

**Settings › Backup and restore** saves the whole device in one file, `armonico-backup-YYYY-MM-DD-HHMM.armonico`. It is a normal zip, which 7-Zip opens, or which opens after renaming it to `.zip`. It holds the settings from `/etc/armonico` and the data folder: profiles, progress, history, recordings, songs, the device's settings and the screen code. The broker passwords are in it, so keep it somewhere safe.

A backup goes to a USB stick, the device's SD card, a shared folder on the network (Windows or a NAS), a Linux machine over SSH with a password, or an FTP server. A restore takes the file from the same places, or from a direct http or https link. A USB stick the Pi cannot read can be formatted from the same screen, as exFAT (read by the Pi and by Windows) or ext4 (the Pi and Linux only); both erase the stick.

A restore checks the file first. Then it keeps the device as it is now as a `before-restore` file, writes the backup in, restarts the services and checks that they came up. If they did not, it goes back to the `before-restore` file by itself.

## Update

**Settings › System › Update** shows when a newer release exists, with what is new, and updates after a confirmation. The device restarts at the end. From a terminal, `sudo armonico update` does the same. The first start after an update offers the full check, once for each version.

An update keeps the settings, progress, recordings and songs, and the device stays a device: the `--device` choice is remembered.

## After a power loss

A boot after a power loss opens a recovery screen first. It checks the file system, the settings, the learning data and the songs, restores a broken file from its last good copy, and starts the services again. An alarm that was ringing does not ring again. Details in [power-loss-and-deep-check.md](power-loss-and-deep-check.md).

## Full reset

**Settings › System › Full reset** deletes everything Armonico put on the device and installs it again, as on a first install. After two questions, `device/full_reset.sh` runs as a unit of its own, and its own screen (`device/reset_screen.py`) shows the steps:

1. **Getting ready.** A copy of the installer, the fonts and the logo go to `/var/lib/armonico-reset`, outside the program that is about to be deleted.
2. **Packages and version.** The network is checked first: apt's package lists from every source, and PyPI. Without a network the reset uses the offline kit the installer keeps in `/var/lib/armonico-offline` (the `.deb` files of the install and the Python wheels; `install.sh --purge` removes it, the full reset keeps it) and goes on, with no newer release. Without a network and without a kit the reset ends here, nothing is deleted, and the device's own screen comes back. The release on GitHub is taken only when it is newer than the version on the device, compared as the updates compare it (`v1.0.0` and `1.0.0.0` are the same version as `1.0`, and the same version is never taken), is checked against its own line in `SHA256SUMS`, and carries the device's files (`device/` with every file its installer requires there; a release without them is never taken); otherwise, or when GitHub does not answer, the copy on the device is installed again. Every package in `/etc/armonico/installed-packages` is downloaded into apt's cache.
3. **Deleting everything.** `install.sh --purge --yes`: the program, the data and the backups on the device, the settings, the service user, the system packages the installer added (Mosquitto with its settings and data among them) and the system users they made, and the folder the project was unpacked in. Then the pip cache in `/root/.cache/pip`, the system journal, and what the screen changed in the system itself: every paired Bluetooth device (BlueZ keeps them in `/var/lib/bluetooth`, which the Bluetooth page reads), the radios switched off (on again, and nothing kept off in `/var/lib/systemd/rfkill`), the radio chips turned off (Armonico's block in `config.txt` goes, and `hciuart` is enabled again, from the next start), and the USB sticks mounted under `/media/armonico`.
4. **Installing again.** `install.sh --device`, from the network or, with none, from the offline kit (`ARMONICO_OFFLINE=yes`: dpkg installs the kit's packages, pip reads only the kit's wheels). If the install stops, it tries again, 3 times in all: from the kit when there is one, else after waiting for the network (up to 10 minutes).
5. **Starting again.** Every saved Wi-Fi network is forgotten (last, since the install needs the network; a network cable keeps working), the reset's folder and its log are deleted, the device restarts, and the setup opens, with its Wi-Fi step.

**A power cut during the reset.** The reset writes a unit of its own (`armonico-recovery.service`) and a copy of its script to `/var/lib/armonico-recovery`, outside everything it deletes, and notes the step it reached. At the next boot the unit starts the copy, which shows the reset's screen and goes on from that step (a cut before anything was deleted drops the reset). The unit and the copy are deleted when the reset ends well.

If the reset stops after something was deleted, its screen stays on with the reason, the log `/var/lib/armonico-reset/reset.log` and the command for the install by hand: `cd /var/lib/armonico-reset/src && sudo ./install.sh --device`.

What stays: packages that were on the device before the first install, or that something installed later depends on, and `curl` and `ca-certificates` (see [commands.md](commands.md#full-removal---purge)); the zip file the project came in, if it was kept outside the folder it was unpacked in; and the time zone, which the setup sets again.

## Uninstall

```bash
sudo ./install.sh --uninstall   # or: sudo armonico uninstall
sudo ./install.sh --purge       # or: sudo armonico purge
```

Both remove what `--device` added as well: snd-virmidi, the sound settings, the power button line, `fsck.repair=yes` when the install added it, and I2C back on if it was on before. `--purge` removes the device's packages too. What `--purge` removes and leaves is in [commands.md](commands.md#full-removal---purge).

## Troubleshooting

| Symptom | Check |
|---|---|
| A key or a button does nothing | Settings › System › Device test, the hardware test: each key and button lights up with its GPIO number. One that does not light up is a wiring problem |
| No sound | Settings › Sound › Output, then the full check's Sound area. The headphone jack always plays while a cable is in it |
| A Bluetooth speaker is silent or drops out | See [Bluetooth sound](#bluetooth-sound). The network cable instead of Wi-Fi helps, since the Pi 4 has one radio for both |
| The touch lands in the wrong place | The full check's first guided step, the 4 corner circles |
| A lesson says Higher or Lower and the key you then press is still wrong | A second miss says the way again and lights the key in gold (Settings › Playing › Lessons can turn either off). The lesson's log names each wrong key: `Note 3: not that one` |
| Something else | Settings › System › Device test, or `armonico check` in a terminal. The report is in `/var/lib/armonico/device/checks/` |
