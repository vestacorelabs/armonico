# Hardware

## Keyboards

Any **class-compliant USB MIDI** keyboard works, meaning one that Linux, Windows and macOS recognize without a driver. Almost every home digital piano and MIDI controller from the last 15 years is class-compliant. A quick check after plugging it in:

```bash
aconnect -i      # the keyboard appears as a client, for example 20: 'Digital Keyboard'
amidi -l         # and as a raw MIDI device, for example hw:1,0,0
```

The setup measures the keyboard from its lowest and highest keys, so models with 25, 37, 49, 61, 76 and 88 keys all work. Playback and the alarm need a keyboard with its own speakers, such as a digital piano or an arranger keyboard. A controller with no sound of its own still works for shortcuts and lessons: the lesson screen can play the demos in the browser, and it shows every note either way.

### Keyboards with only a 5-pin MIDI port

Older keyboards have a round 5-pin DIN MIDI port instead of USB. There are two ways to connect one:

- **A USB MIDI interface**: a small cable with a USB plug on one end and two DIN plugs on the other. Cheap ones often drop or garble messages, and interfaces from established music brands are the reliable choice.
- **A Raspberry Pi as a USB MIDI gadget.** A Pi Zero, Zero 2 W or Pi 4 can present itself to the host computer as a USB MIDI device, with the keyboard connected to the Pi through a MIDI interface. The host then sees the Pi as the keyboard. On the Pi:

  ```bash
  echo "dtoverlay=dwc2" | sudo tee -a /boot/firmware/config.txt    # /boot/config.txt before Bookworm
  echo -e "dwc2\ng_midi" | sudo tee -a /etc/modules
  sudo reboot
  ```

  After the reboot, the Pi's USB data port connects to the host. On a Pi Zero that is the port marked USB, not PWR. On a Pi 4 it is the USB-C port, which then also powers the Pi, so the host's port has to supply enough current. The host lists a new MIDI client, usually named `f_midi`, and that is the name to pick in the setup.

  The Pi also has to pass the notes between the keyboard and `f_midi`, in both directions. With the names that `aconnect -l` shows on the Pi:

  ```bash
  aconnect '<keyboard>' f_midi
  aconnect f_midi '<keyboard>'
  ```

  These connections are lost at every reboot, so they belong in a small systemd service or in `/etc/rc.local` on the Pi.

## Cables and distance

USB 2.0 is specified for passive cables of up to **5 meters**. A longer passive cable, or a chain of extensions, loses the signal, and the keyboard disconnects at random, often in the middle of a song, when the most data flows.

| Distance | Solution |
|---|---|
| Up to 5 m | A normal USB cable of good quality |
| 5 to 15 m | An **active** USB extension cable (a repeater cable), with a small amplifier in the plug. Some need a power adapter at the far end |
| Over 15 m | A chain of active cables, a powered USB hub at the far end, or a small computer next to the keyboard, such as a Raspberry Pi, running Armonico and reaching the rest over the network |

## Power and USB autosuspend

Linux can suspend idle USB devices to save power, and some keyboards do not wake up properly afterwards. When the keyboard disappears after a quiet period, autosuspend can be turned off for it:

```bash
lsusb                              # the keyboard's ID, for example 0499:1054
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0499", ATTR{idProduct}=="1054", ATTR{power/autosuspend}="-1"' \
  | sudo tee /etc/udev/rules.d/50-piano-autosuspend.rules
sudo udevadm control --reload
```

The rule applies from the next time the keyboard is plugged in.

A keyboard that draws its power over USB, or a Raspberry Pi with a weak power supply, can also drop out under load. A powered USB hub between them solves both.

## Virtual machines

Armonico runs well in a virtual machine, for example on Proxmox, with the keyboard passed through as a USB device. Passing it by vendor and product ID rather than by port keeps it working when it is plugged into another port. Minimal images sometimes lack the ALSA sequencer module, and the installer loads it (`snd-seq`) and keeps it loaded at boot.

## The Armonico Pi device

Armonico Pi is a small keyboard of its own: a Raspberry Pi 4 in a printed case with 13 keys (8 white and 5 black, from C up to the high C, one octave above it), 4 buttons and the BIGTREETECH Pi TFT50 screen on DSI. Its sound goes out on the headphone jack (AUX), a USB sound device or Bluetooth. It is powered over PoE through a PoE HAT, or over USB-C without one. It runs the whole of Armonico, so lessons, songs and shortcuts work on it as on any keyboard.

The screen is the 5" BIGTREETECH Pi TFT50 on the Pi's DSI port. The printed case, the touch and the layout of every screen are built and tested for it, so the device is built with that screen. The device draws and plays on it only: an HDMI screen and HDMI sound are off. The HDMI parts of the code are kept as comments marked "HDMI off (DSI only)".

```bash
sudo ./install.sh --device
```

On Raspberry Pi OS Lite this installs everything the normal install does, and then the parts below. The setup does not ask for a keyboard or measure one: it is always `Armonico Pi` with 13 keys. The machine is remembered as a device (`/etc/armonico/device`), so updates and `--reconfigure` keep all of this, and `--uninstall` and `--purge` take it away again: snd-virmidi, the sound settings, the power button line, `fsck.repair=yes` on the kernel command line when the install added it, I2C back on if it was on before, and with `--purge` the device's packages too.

| Part | What it is |
|---|---|
| `armonico-device` | `device/armonico_pi.py`: reads the keys and the buttons on GPIO, plays the grand piano and the other 15 General MIDI voices and the drums from samples, and shows up in ALSA as the keyboard `Armonico Pi`. The setup lists it like a USB keyboard |
| `armonico-screen` | greetd, which logs the screen user in and runs `device/session.sh`: `device/screen.py` straight on KMS, on the DSI screen |
| `snd-virmidi` | A virtual MIDI device, `hw:7,0`, that only goes out: the lesson engine plays on it (`AMIDI_DEVICE` in `config.env`) and the device sounds it |
| PipeWire | The ALSA default: PipeWire, run as the service user's own, mixes every program into AUX, a USB sound device, a Bluetooth one or any of them together (`device/pipewire`, see [Sound outputs](#sound-outputs)). The Pi's HDMI sound cards are left off. The installer writes no `/etc/asound.conf`; one written by someone else is kept, with a warning, since a `pcm.!default` in it skips PipeWire |
| `config.txt` | `dtoverlay=gpio-shutdown,gpio_pin=3` for the power button, and I2C off. One restart applies it |

The piano samples are the Salamander Grand recordings without loss (FLAC, `device/samples`), rendered once to one file per key, each moved by a semitone at most; the lesson screen's MP3s are used only for a note whose FLAC is missing. The other voices and the drums come from `device/samples/gm` (FluidR3_GM), and a note of theirs is made when first played.

### Wiring

Every switch has one leg on its own GPIO pin and one on a shared ground chain, with the pull-up inside the Pi, so there are no resistors. Pin numbers are the physical pins of the 40-pin header.

| Part | Pins | GPIO |
|---|---|---|
| White keys C, D, E, F, G, A, B, high C | 7, 11, 12, 13, 15, 16, 18, 33 | 4, 17, 18, 27, 22, 23, 24, 13 |
| Black keys C#, D#, F#, G#, A# | 19, 21, 22, 23, 24 | 10, 9, 25, 11, 8 |
| Buttons ☰, ▼, ▲, ✓ | 26, 29, 31, 32 | 7, 5, 6, 12 |
| Ground chain | 39 | GND |
| Power button | 5 and 6 | 3 and GND |

Another wiring is set in `config.env` with `DEVICE_KEY_PINS` (13 GPIO numbers, C to the high C; the default is `4,10,17,9,18,27,25,22,11,23,8,24,13`), and `DEVICE_BUTTON_PINS` (`menu:7,down:5,up:6,ok:12`).

### Using it

| Control | Rest, free play | Songs, settings | Lesson |
|---|---|---|---|
| Keys | Play, in the octave on the screen | Play | The lesson. The keys stay put: key 1 is always C, the high C key is number 1 too, the key of the next note sounds at the song's own height, an arrow marks a note above or below (none for the C that the high C key plays), and the corner shows its octave |
| ☰ | Next screen, held: back to rest | Next screen | Held: leave the lesson |
| ▲ ▼ | Octave | Move, or the volume | |
| ✓ | Songs | Start a lesson, held: play the song. In settings: change the item | |

The screen is a touch screen, and a bar of 4 touch buttons along its bottom does what the 4 buttons do, so the device works before any button is wired. One touch on a song starts its lesson, and one touch on a setting changes it. What cannot be taken back asks first, with Yes and Cancel: switching the broker, which restarts every service, and leaving a lesson, by touch or with ☰ held. ✓ or a touch on Yes goes on, any other button or 12 seconds without an answer cancels. Over the idle video the bar stays hidden, so the subtitles stay clear, and a touch brings it up for 5 seconds. During a lesson the bar holds one button that leaves the lesson.

The stop button is chosen in the device's settings, or in the settings of the lesson screen, where the row shows only on the device. It can be any of the 4 buttons or any of the 13 keys (✓ by default), never the power button. While the alarm rings or a song plays, it stops them and does nothing else: a key chosen for it does not sound then. The rest of the time it is an ordinary button or key.

The sustain pedal is a switch in the settings.

After the setup finishes for the first time, the character chosen in the settings shows the rest screen in 8 short steps (the 4 buttons, the song card, how a lesson goes in stages, playing on the keys and the touch screen, the long presses and the octave, ☰ and the settings, and a word on the mix of fields: the characters and the sky are design only, not part of the learning), each part outlined while the rest of the screen is dimmed: ✓ or a touch on the bubble goes on, ☰ skips. It waits while an alarm, a song or a lesson plays and goes on from the same step afterwards. The first time Songs, Free play and a lesson open, a one-line tip shows in a corner for 8 seconds, once each. Settings › System › Opening tour shows the tour and the tips again.

The hardware test (the guided steps of Settings › System › Device test, and the Hardware test screen they link to), checks everything after the soldering in one go: each of the 13 keys and the 4 buttons lights up with its GPIO number when pressed, every touch leaves a dot where the panel felt it, two touch buttons play a tone on the left or the right speaker only, and a gold frame shows whether the whole picture is on the panel. The power button is the one part it cannot show, since a press shuts the Pi down, so it is tested last. ☰ held or Done leaves it. `armonico status` checks the rest: the services, the broker, the samples, the sound card and the jack's level, the lesson output, the keys on the sequencer, the display, `config.txt`, the power supply, and every song that cannot be a lesson, with the reason.

The ghost run, on by default and switched in the settings, races the previous full run of the same song: during a full run a ring marks the key the last run had reached at that moment, and at the end the screen says how many keys ahead or behind it this run finished. The run kept is the last one that reached the end, one for the run on your own and one for the run with the band, in `ghosts.json` of the profile. Starting a song over (`/lesson <song> reset`) forgets it.

The device publishes its buttons and its octave on MQTT (`piano/device/button`, `piano/device/octave`), see [mqtt.md](mqtt.md).

### Full check

**Settings › System › Device test** is one window for the diagnosis, the full check and the hardware test: automatic and by hand, automatic only, by hand only, or a quick check of one area; the same report is saved in each. The full check below is its automatic and guided parts. It changes nothing on the device: no alarm, no backup, no message, no setting and no fix, unless Fix is touched in the summary for a check the diagnosis already knows how to fix. The only file it writes is its report.

First 21 automatic checks, about a minute, each row filled as its area ends, a few at a time in the background: the services, the broker and the bridge's status kept on it (`piano/status` online, not a stale offline), the lesson screen and its song list, the network and Wi-Fi (NetworkManager, the adapter, the radio, the signal), the internet services the device uses (GitHub for updates, Open-Meteo, the NASA feed, each with its response time, only with a network), the time and the time zone against the city, power (under-voltage and slowdown now and since boot, from `vcgencmd get_throttled`), the temperature, memory and swap and an Armonico process using too much of it, the SD card (free space, not read-only, no read or write errors in the kernel log), the sound and its volume, the samples (the piano, the 15 other voices and the drums), MIDI and a USB keyboard, the GPIO pins (all inputs, none read as pressed), the display, its fonts and `config.txt`, the settings, progress, profiles and learner files as valid JSON (a damaged one by its name), errors in the log of each Armonico service in the last 24 hours with the last one, the last backup and the backups folder, the alarm song and the local stop path the bridge reads, whether a Telegram bot token is kept (the root action `telegram-kept` answers true or false, never the token), and a newer version. What cannot be read without root (the system log and the kernel log, unless the screen's user is in the `systemd-journal` group) is marked as not checked, never as failed.

Then 9 guided steps, each skippable with ☰: the 4 corner circles in order (a touch that lands on another circle shows a turned panel), the 13 keys, the high C named as such (a key not pressed within 30 seconds counts as not responding), the 4 buttons, the power button (the shutdown question shows, and its Yes shuts nothing down), a tone on the right speaker only and on the left only, a short piano phrase at the current volume, one key of a USB keyboard when one is connected, and the brightness at 100, 75, 50 and 25%. During the steps the keys are only named (`piano/device/test` is `quiet`: they play nothing, send nothing on, and the power button held 4 seconds does not shut down), and leaving the screen at any point turns test mode off and brings the brightness back; the volume is never changed.

The summary lists the failures first, then the warnings, each with one line of what to do, a Fix where the diagnosis can fix it and a link to the hardware test for the keys and buttons, then how many passed. **Send via Telegram**, shown only while a bot token is kept, asks root to send a short summary in a fixed form (the counts and the failed and warning lines, addresses taken out) to the device's chats with the kept token, at most once every 10 seconds. The report is a text file in English, `/var/lib/armonico/device/checks/YYYY-MM-DD-HHMM.txt` (the last 10 are kept), with the version, the date, every check with its result and detail, and the guided steps' answers. `armonico check` runs the automatic part in a terminal and saves the same report. The first start after an update offers the check in one line at the top of the rest screen, once for each version; a touch opens it.

The same window has a **Deep check** (also `armonico check --deep`), and a boot after a power loss opens a recovery screen first. Both are described in [power-loss-and-deep-check.md](power-loss-and-deep-check.md).

### Sound outputs

**Settings › Sound › Output** lists the USB sound devices plugged in (headphones, a USB sound card, a USB speaker) and the Bluetooth ones connected, each by its own name. AUX (the headphone jack, or speakers on it) is not in the list: it always plays, and pulling its cable out is how it goes quiet. Each device is switched on or off and plays beside AUX, and **No sound** silences them all. A set of outputs is a combine sink made with `pactl load-module module-combine-sink` and made again at every start. A device that is plugged in or connects becomes an output at once, beside AUX. When a chosen device goes, the sound stays on the rest, and the screen says where the sound is now. A USB device keeps its own level (WirePlumber's), while the Pi's own outputs are set to full and the device's volume sets what is heard. No stream gets back a volume, a mute or an output from an earlier time (`node.stream.restore-props` and `node.stream.restore-target` are off in `device/pipewire/80-armonico-wireplumber.conf`). The setup's sound step has Sound on and No sound.

### Bluetooth sound

**Settings › Sound › Output › Bluetooth** scans for headphones and speakers, pairs one (the device offers itself to the Pi as a device with no screen and no keyboard, which is what headphones and speakers expect), trusts it so it can connect by itself when it is switched on, and connects it. A connected device is one more output in the list, and plays beside AUX and together with any USB device. A speaker that does not connect by itself is connected again from the screen while it is in the sound choice, with a wait that grows from 20 seconds to 2 minutes. A saved device that shows as connected but has no sound output in PipeWire (it connected before PipeWire was up, or PipeWire restarted under it) is asked for its sound profile without dropping the link: every 30 seconds the screen looks for one, asks at once in the first 5 minutes after boot and later after 2 silent looks in a row, at most 3 times until the device has an output or disconnects. If it is still silent after that, the screen turns the Bluetooth radio off and on again, at most twice an hour, and says so. A device left on its headset profile, or with its profile off, is set back to its best A2DP profile. A chosen device that dropped and came back between two looks is a new node in PipeWire, and the mix is made again for it. Sound goes out as A2DP only: a Bluetooth device is never moved to its mono headset profile when a recording starts (`bluetooth.autoswitch-to-headset-profile = false`), and its output stays open instead of being suspended when idle (`session.suspend-timeout-seconds = 0`), both in `device/pipewire/80-armonico-wireplumber.conf`. The Pi 4's one radio serves Wi-Fi and Bluetooth on 2.4 GHz, so the device on the network cable is the quiet case; the full check's Sound area counts PipeWire's dropouts on a connected Bluetooth output and reads the log of bluetoothd and PipeWire, and says what helps (the cable instead of Wi-Fi, getting closer, a USB Bluetooth adapter with `dtoverlay=disable-bt`). The installer adds bluez and `libspa-0.2-bluetooth`, puts the service user in the `bluetooth` group (undone by `--uninstall`) and turns the adapter on.

### Screen recording

The screen recording (`armonico screen-rec on`) records the screen to a file, for guides and videos: the picture, the device's own sound and a USB microphone, so a recording can carry a narration. The screen program hands each frame it shows to ffmpeg through a pipe, at most 30 a second, so the file holds exactly what was drawn; reading the picture back from the graphics memory (`kmsgrab`) was tried first and came out choppy, since every frame was copied out of uncached video memory on the CPU the screen needs. The frames wait in a queue of 2 for a writer thread: when ffmpeg falls behind, a frame is dropped and the screen never waits. Each frame is stamped with the clock when it was drawn, and the video is made a steady 30 frames a second, a frame repeated where the screen drew fewer (the sleep screen draws 2), so time never jumps. x264 (ultrafast, crf 18) into an `.mkv`, which stays readable if the recording is cut off; ffmpeg runs at a lower priority (nice 10) than the screen.

The sound is the device's own (the monitor of PipeWire's default output, so the keys, the songs and the video as clean as they were played) and the microphone, all on one clock. With a microphone the file has 3 sound tracks: first both mixed, the default track, which any player plays (most play the first track only); then the device's sound alone and the microphone alone, to be moved and set louder or softer in an editor. The microphone is the first USB input PipeWire has; a USB headset whose card was left on an output-only profile is moved to its best profile with an input, and the microphone is unmuted and set to full level. The message at the start names the microphone, or says none was found. A USB headset plays the device's sound only when it is chosen as an output (see above), so plugging one in for its microphone leaves the speakers as they are.

`armonico screen-rec off` stops it. The file is closed, then moved to a USB stick when one is plugged in (root mounts it, copies into `Armonico recordings/`, writes it to the stick for good, checks the size, and only then removes it from the SD card); with no stick it stays in `/var/lib/armonico/device/recordings/`, and `sudo armonico screen-rec export` moves the ones left there later. ffmpeg's own output is in `last.log` in that folder. From a terminal: `armonico screen-rec on|off` (sent to the screen over MQTT, `piano/device/rec`), `sudo armonico screen-rec export|list`. The root action is `rec-export` (`device/root_actions.py`, the work in `device/screen_rec.py`); it opens the recordings folder and the stick's folder without following a link, reads only regular files and writes only new ones.

### Wi-Fi

**Settings › Network and links › Wi-Fi** lists the networks around: one row for each name (the strongest of its access points), its signal in 4 bars, a lock on a secured one and a ✓ on the one in use, and a row for a hidden network, whose name is typed. The list is scanned again every 15 seconds while the page is open, and not at all while it is closed. The password is typed on the touch keyboard, which has a symbols layer (the `!#$` key) and a Show button; an open network or a saved one connects without it. The connection is given 20 seconds; once up, the screen shows the network, its IPv4 address and the lesson screen's address, and a wrong password brings the keyboard back with what was typed. **Saved networks** lists the networks NetworkManager keeps, marks the one in use, and **Forget** asks before it deletes one. The setup has a Wi-Fi step right after the language, only when no network cable has an address; it can be skipped, since the device works offline.

Wi-Fi goes through NetworkManager (`nmcli`), which Raspberry Pi OS uses since Bookworm, by way of the root unit (`device/root_actions.py`: `wifi-scan`, `wifi-connect`, `wifi-saved`, `wifi-forget`, `wifi-status`), which checks every value before `nmcli` sees it. The password never appears on a command line: a new connection is added without it, and `nmcli connection up ... passwd-file` reads it from a file only root can read, removed right after. NetworkManager keeps the password with the connection; Armonico stores none. Without NetworkManager the page says Wi-Fi is not available on that system.

**The chips themselves.** The Wi-Fi and Bluetooth switches turn the radio off and leave the chip powered with its driver loaded. The last two cards of **Settings › Network and links**, **Wi-Fi chip** and **Bluetooth chip**, turn the chip itself off, or on again, from the next start: the root action `radio-chip` writes the Raspberry Pi overlays `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` into one block of its own at the end of `/boot/firmware/config.txt`, marked `# >>> Armonico: radio chips`, and removes the block when both are on again; nothing else in the file is touched. The two halves of the chip are separate for the switches, but not always for the overlays: `disable-wifi` makes the firmware pull the chip's WL_ON line low, and on boards where Bluetooth's BT_ON follows that line, Bluetooth goes off with Wi-Fi ([raspberrypi/firmware#1611](https://github.com/raspberrypi/firmware/issues/1611)). The confirmation says so, and with the Wi-Fi chip off the full check's network area looks for the Bluetooth adapter and reports it when it is gone. To keep Wi-Fi quiet without touching Bluetooth, the Wi-Fi switch is enough. On a board with Bluetooth of its own (every Raspberry Pi since the 3, the 400 and 500, the Zero W and Zero 2 W), the Bluetooth page never says there is no adapter: it says the chip is off in `config.txt` (by Armonico or by a line written by hand), that it went off with the Wi-Fi chip, or that it is not answering. For the first two, ✓ turns the chip on from the page, after a question, and it works after a restart. A line written by hand is not deleted: it is made a comment, with a note that it was turned on from Armonico's settings. Bluetooth off also disables `hciuart.service` when it was enabled, and enables it again with the chip. Each change asks first, says that it takes effect after a restart, and offers the restart. Turning the Wi-Fi chip off while the device is connected through Wi-Fi says that it will have no network after the restart until a network cable is connected. A chip turned off by a line written by hand in `config.txt` is left as it is, and the screen says so.

### Connection modes: Local broker / External broker

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

### Rest screen

The rest screen has exactly two choices in Settings:

- **Armonico video**: the bundled `device/media/armonico-japanese.mp4` (1280x720, 30 frames a second, scaled to the screen by the same pipeline); audio and video play together in one GStreamer pipeline. Each repeat pauses for one second before restarting.
- **Animation**: the built-in Armonico house-and-sky animation, without video playback.

After the minutes set in **Settings › Look and screen › Screen sleep** (1, 5 or 15, or never) without a touch, a key or a button, the rest screen goes to sleep: black, with a medium clock, one line under it with the next alarm (when the device's own alarm rings within 24 hours) and the weather now in the chosen city, the Moon in its real phase, a few stars and the character asleep on a hill. Everything is dim, about a quarter of full white, and the whole picture drifts by a few pixels over minutes so nothing stays on the same pixels. The weather comes from Open-Meteo (free, no key), asked at most every 30 minutes and only with a network; without one the weather is left out. The sleep screen is drawn twice a second to keep the Pi's load low. The first touch, key or button only wakes the screen. With night mode and Screen at night set to off, the backlight is off while the screen sleeps, as before.

**Settings › Look and screen › Night mode** is one setting that lowers the screen's brightness and the volume together. The alarm always rings at 100%, at night too, and the volume before it comes back when it stops. Its first row is a switch: one touch turns night mode on or off. **Automatic** cycles through Sunset and sunrise (by the chosen city), Chosen hours (a row **Hours** with the two times as pills, each touch moves a time 30 minutes later, and a span that crosses midnight works) and Manual (it stays on until the switch is turned off). **Dimming** has 3 steps: 75% brightness with the volume at 70% at most, 50% with 40%, and 25% with 20%. The line over the rows says whether it is on and when. The screen is never brighter than the day's Brightness, a volume already under the cap is left alone, and the day volume comes back when night mode ends or its dimming is lightened. While the switch is off nothing is lowered and the rows under it are dimmed and cannot be touched.

The video stops while a lesson, alarm or song is active, when a key is played, or when the screen leaves rest. It starts again when the device returns to rest. After a key was played it waits for a touch or a button, and comes back by itself when a lesson, alarm or song ends. There is no YouTube downloader or general file browser in the Device UI.

### Full reset

**Settings › System › Full reset** deletes everything Armonico put on the device and installs it again, as on a first install. After two questions, `device/full_reset.sh` runs as a unit of its own, and its own screen (`device/reset_screen.py`) shows the steps:

1. **Getting ready.** A copy of the installer, the fonts and the logo go to `/var/lib/armonico-reset`, outside the program that is about to be deleted.
2. **The latest version.** The network is checked first: apt's package lists from every source, and PyPI. Without a network the reset ends here, nothing is deleted, and the device's own screen comes back. The release on GitHub is taken only when it is newer than the version on the device, compared as the updates compare it (`v1.0.0` and `1.0.0.0` are the same version as `1.0`, and the same version is never taken), is checked against its own line in `SHA256SUMS`, and carries the device's files (`device/` with every file its installer requires there; a release without them is never taken); otherwise, or when GitHub does not answer, the copy on the device is installed again. Every package in `/etc/armonico/installed-packages` is downloaded into apt's cache.
3. **Deleting everything.** `install.sh --purge --yes`: the program, the data and the backups on the device, the settings, the service user, the system packages the installer added (Mosquitto with its settings and data among them) and the system users they made, and the folder the project was unpacked in. Then the pip cache in `/root/.cache/pip` and the system journal.
4. **Installing again.** `install.sh --device`, which takes the packages from apt's cache; only the Python packages need the network. If the install stops, the reset waits for the network (up to 10 minutes) and tries again, 3 times in all.
5. **Starting again.** The reset's folder and its log are deleted, the device restarts, and the setup opens.

If the reset stops after something was deleted, its screen stays on with the reason, the log `/var/lib/armonico-reset/reset.log` and the command for the install by hand: `cd /var/lib/armonico-reset/src && sudo ./install.sh --device`.

What stays: packages that were on the device before the first install, or that something installed later depends on, and `curl` and `ca-certificates` (see [commands.md](commands.md#full-removal---purge)); the zip file the project came in, if it was kept outside the folder it was unpacked in; and the time zone, which the setup sets again.

## The equipment it was tested on

Armonico 1.0 was tested in the Vesta Core Labs home lab on the equipment below. A model or version that is not listed can work as well: this is what was run, not a limit.

### Keyboard

| Item | Detail |
|---|---|
| Keyboard | Yamaha PSR-E363, 61 keys, class-compliant USB MIDI. USB ID `0499:1710`, listed by ALSA as `Digital Keyboard` |
| Connection | The keyboard's own USB cable and a 5 m active USB extension, about 6 to 7 m in total. A passive 5 m extension in the same place caused frequent disconnects |
| Power | Mains. USB autosuspend turned off for vendor `0499` |

### Machines

| Machine | System | Result |
|---|---|---|
| Virtual machine on Proxmox VE 9.2.21, with all updates, on a Dell PowerEdge R620. The keyboard is passed through by its USB ID | Debian 13, x86_64 | The main installation, in daily use |
| Virtual machine on the same Proxmox | Ubuntu Server 26.04, x86_64 | Works |
| Raspberry Pi 4 | Raspberry Pi OS 64-bit, both the desktop version and Lite | Works |
| Xiaomi Redmi Note 7, 4 GB RAM, rooted with Magisk, with a kernel built for it | A Debian 13 container with systemd under Droidspaces, arm64, on Android 10 | Works, with the limits below |

### Linux on Android

The installer refuses Termux, and that does not change: an app on stock Android has no access to the ALSA MIDI devices and no systemd. What worked is a full Debian container with systemd on a rooted phone, which needs all of these:

- Root, and a kernel that provides the ALSA sequencer and USB MIDI. On the test phone this was a kernel built for it.
- A container that runs systemd. Droidspaces was used, since every part of Armonico is a systemd service.
- The keyboard's sound devices (`/dev/snd`) passed into the container. On the test phone they were passed one by one over ADB, not with a blanket hardware access mode, which can take devices away from Android.
- A USB OTG cable. The PSR-E363 worked on a single plain cable, USB-C to USB-A, with no charger on the phone.

The limits that showed up:

- **Memory.** The phone's 4 GB is shared with Android and its apps, and the container was capped at 1800 MB. That is enough for Armonico, but Android competes for the same memory.
- **Android's own processes.** Android keeps running and managing its own processes around the container, and they compete with it for the CPU and memory. Playback and key detection lag at times because of it.
- **Start at boot.** On the test phone the container was started by hand, not at boot, so the alarm works only while it runs.

For daily use, a virtual machine, a mini PC or a Raspberry Pi is the steadier choice. The phone is a working option for those who already have one rooted.

### Home Assistant and Telegram

| Item | Detail |
|---|---|
| Home Assistant | Home Assistant OS in a virtual machine on the same Proxmox, version 2026.9.4 |
| Broker | The Mosquitto broker app 7.1.1 in Home Assistant, which does not enforce topic rules (see [SECURITY.md](../SECURITY.md)) |
| Telegram | The Telegram bot integration of Home Assistant |

### Lesson screen

| Device | System | Browser |
|---|---|---|
| Samsung Galaxy S25+ | Android 16 | Chrome |
| iPad 10.2" | iOS 26.5.2 | Chrome |
| Laptop | Windows 11 | Chrome |

Chrome is the browser the screen was tested in, and the recommended one. The screen only shows the lesson and sends commands, and no USB device goes through the browser, so other current browsers are expected to work too.
