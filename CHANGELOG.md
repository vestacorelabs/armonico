# Changelog

## 1.0

The first release: the bridge, the lesson engine and its screen, Home Assistant and Telegram, the Armonico Pi device and the Android app.

### Highlights

**New**
- An Android app: the bridge, the lessons and the alarm on a phone
- Wi-Fi and Bluetooth switches in Settings › Network and links
- Alarm clock icon and the next ring time in the top bar
- The full path above each Settings title
- One Night mode: sunset to sunrise, chosen hours or off, with a strength
- Bluetooth headphones and speakers as a sound output
- Wi-Fi from the screen, and a full check of the device
- One Device test window, with 4 ways to check the device
- Interface size from 80% to 150% (Settings › Look and screen)
- Screen recording with the device's sound
- A new start-up screen: the logo, what is being done, a bar that fills, then a fade
- The network page shows the connection, signal, address and gateway
- Profiles on the device: add, rename, change the PIN, delete (a backup first)
- The opening tour has 8 steps: how a lesson goes, long presses and the octave
- Hide the screen buttons (Settings › Look and screen); a touch shows them for 5 s
- A QR code opens the lesson screen: end of setup, after Wi-Fi, in Network and links
- Hebrew letters on the on-screen keyboard, wherever you type
- Settings in a clearer order, and one Profiles card instead of two
- Playback stops by itself when a service or the broker fails during a song or the alarm
- The Wi-Fi and Bluetooth chips can be turned off completely, from the next restart
- 13 keys, C to C: a high C key on GPIO 13
- After a power loss the device checks and repairs itself, then opens as usual
- A deep check of the device with a 7-day history, also as `armonico check --deep`
- Search songs online from the Songs screen
- A phone-style number keypad for PINs and ports
- Settings fit the screen's size, from 3.5" to 10", with scrolling and a position bar
- Profiles: a page for each one, to switch to it, rename it, change its PIN or delete it
- Songs from USB is on the Songs screen, beside Search online
- A sound device that connects becomes an output at once, beside AUX
- AUX always plays and is not a choice in Settings › Sound › Output
- Saving a screen recording shows how far it is
- The Output page shows each Bluetooth device's codec, or that it is in call mode
- Delete any song from the Songs screen; a built-in song stays deleted after updates

**Improved**
- Bluetooth sound: a longer buffer, and Wi-Fi rests while it plays on a wired device
- The device runs on its own 5" DSI screen only: an HDMI screen and HDMI sound are off
- The rest video is the 1280x720 original, not a 800x450 copy
- With the buttons hidden, an empty "No song played yet" line is hidden too
- The USB keyboard types, and left Shift + left Alt changes the language
- A larger mouse pointer, in the usual Linux look
- The mouse wheel moves one row for each notch
- A Bluetooth output chosen alone plays (it was silent without AUX or HDMI beside it)
- The screen recording can record a chosen output
- A recording keeps Bluetooth headphones in full quality (their microphone is not used)
- A profile is switched to from its own page, with the PIN asked first
- Each check opens with its own title and lines
- Pause and resume from the songs list
- No click when the rest video closes
- The keys play without crackle on fast notes
- Piano samples take a third of the memory
- A drag scrolls and never presses; a short touch presses
- Hebrew text fields write right to left
- Long texts shrink to fit, then take a shorter form, and only then end with …
- Pages that do not change redraw 8 times a second, not 30
- The lesson screen's keyboard shows the device's 13 keys, the high C its own key
- After a power loss, settings and learning data are repaired already at boot
- Bluetooth devices are trusted before pairing, so they reconnect after a restart
- A device disconnected by hand stays disconnected, also after a restart
- Wording in Hebrew and English, across the screens and the docs
- Screen recording is no longer a card in Settings; `armonico screen-rec` runs it
- Settings have at most 8 cards on a page on the 5" screen
- Search online finds pieces in movements (Vivaldi's seasons) and Hebrew words
- On 7" and 10" at 140% and more, the cards are in two columns
- A screen recording saves faster: the microphone is cleaned beside the picture
- Hebrew status lines are written as nouns (חיפוש…, שמירה…)

**Fixed**
- Bluetooth no longer goes down a minute after boot when a headset was connected
- The rest video keeps its sound after it loops or opens again
- Night mode lowers the sound by a share of the day volume, and never to silence
- The alarm always rings at 100%, also in night mode
- An alarm cut by a restart or a power cut is not rung again
- The day volume comes back after the power was cut during night mode
- No sound stream starts again muted or at 0 from an earlier time
- A connected but silent Bluetooth device gets its sound back by itself
- The full check says when Bluetooth went off with the Wi-Fi chip
- The Bluetooth page no longer says "No Bluetooth adapter" for a moment
- On a Pi, the Bluetooth page says why there is no Bluetooth instead of no adapter
- The Bluetooth page keeps its place when the list changes, and says when the chip is stuck
- A song stops when a lesson starts or another screen opens
- The active profile can be deleted: the device moves to the main profile first
- A profile delete waits up to a minute and says why when it does not go through
- Clearer wording across Settings, profiles, songs and recording
- The tests skip cleanly when pygame, numpy, fontTools or pyluach are missing
- The power button is read from its own key, not from the HDMI CEC device
- A recording keeps its full length when a sound source drops out
- A Bluetooth tap during a refresh waits its turn instead of hanging
- Backups have no size limit; free space is checked before writing
- A restore updates the broker's logins and tests them
- The screen stops retrying when no display is connected at boot
- Free play names its notes in one way, not a mix of numbers, letters and Do Re Mi
- Bluetooth no longer hangs: shorter waits, a Cancel button and a real disconnect
- A song starts at once, and stops when the songs list is left
- Bluetooth opened from the quick check returns to the quick check
- Bluetooth scan finds devices again, and says how many it heard
- Bluetooth lists the devices with no name in one folded row
- Bluetooth pairing answers a PIN or a confirmation, and gives up cleanly after 25 s
- Bluetooth asks again when the adapter answers late, and wakes it when it dropped
- Bluetooth no longer takes a speaker for something else
- The full reset installs again: its copy of the installer lacked CHANGELOG.md
- The sleep screen moves smoothly (15 frames a second, a finer breath)
- The corner rings of the touch test no longer lie on the bottom buttons
- Text in the settings cards, and the title lines, stay inside at 150%
- The Home Assistant token stays after a restart
- Recording with a USB headset keeps the sound on the headphones
- Skills show only what was practiced
- The hardware test ends after the 13 keys and 4 buttons, with no knob to wait for
- Cards no longer hide under the bottom buttons on long Settings pages
- No more squares for ◂ ▸ and for names in other alphabets
- A Bluetooth devices card shows the connected device, not only an arrow
- A pairing is kept when the first connection after it is slow, instead of being removed
- A removed Bluetooth device is not called back; tries for one away stop after 12
- Wi-Fi comes back at once when the network cable is pulled while Bluetooth plays
- The uninstall also removes the file system repair setting it added
- The deep check reads the system log only when allowed, and counts the screen program
- The high C key is 8 in the numbers, on every screen and in the browser
- Bluetooth that connected at boot with no sound is connected again by itself
- A song with a few bytes after its last track is no longer moved aside as damaged
- A download is never kept cut short, or with no melody to learn
- Wikimedia Commons is asked once more when it answers "too many requests"
