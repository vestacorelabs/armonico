# Armonico for Android

The Android app turns a phone into the machine next to the keyboard. The keyboard plugs into the phone over USB, and the app runs the bridge, the lesson engine and the lesson screen, with the same MQTT topics as the Linux install. Home Assistant, Telegram and the `armonico` commands work with it as they do with Linux. It is still in testing. The app is at version 1.0.1, a step ahead of the project's 1.0, for the shortcuts on the phone and the alarm clock.

How the app is put together and how it is built is in [android/README.md](../android/README.md).

## What it needs

| Item | Details |
|---|---|
| Phone | Android 7.0 or later, with USB host (OTG). Most phones have it |
| Keyboard | Any class-compliant USB MIDI keyboard, as for Linux. See [hardware.md](hardware.md) |
| Cable | USB-C (or micro-USB) on the phone's side to the keyboard's plug, or an OTG adapter |
| Power | The phone has to stay powered while it is the bridge. A port in host mode gives power to the keyboard instead of taking it, so charging at the same time needs a USB-C OTG adapter or hub with PD pass-through, and a phone that charges while it is the USB host. Not every phone does |
| Network | Wi-Fi, for the lesson screen on other devices, Home Assistant and Telegram. The phone alone with the keyboard works without it |

## Install

1. On the phone, open the [Releases](https://github.com/vestacorelabs/armonico/releases) page and download `armonico-android-1.0.apk` (the number is the version).
2. Open the file. Android asks once to allow installs from the browser or the file manager it was opened from; allow it, then install.
3. Open Armonico. The first start explains the permissions the app uses and asks for each one in turn: notifications (the bridge runs as a service with a notification, which has two buttons: **Stop playback**, and **Turn the bridge off**, which ends the service and takes the notification away; the bridge starts again from the app, with the phone, or at the time of an alarm set in the app), running in the background (so an alarm or a key never waits for the phone to wake up), display over other apps (so a shortcut can open an app or a link, see [Shortcuts on the phone](#shortcuts-on-the-phone)), and Do Not Disturb access. On Xiaomi, Redmi and Poco phones their own pop-up switch comes last. Each opens a page of Android's settings; after allowing it, come back to Armonico and it goes on to the next. Settings › **Permissions** lists all of them with their state, and **Allow** opens the one still missing; while one is missing the card stays at the top of the settings. **Later** skips the walk, and the card is there for any of them.

The app is signed with the project's key. A newer APK from the same page installs over it and keeps its settings and songs.

## The first start

1. **Plug in the keyboard.** The bridge finds it by itself while it runs, with no question from Android. The bridge starts with the app, with the phone (Settings › General), and at the time of an alarm set in the app.
2. **Choose the broker mode** in Settings, at the top (see [Two ways to run](#two-ways-to-run)), and **Save and connect**.
3. **Measure the keyboard.** Settings › Keyboard › Range › **Measure**, then press the lowest key and the highest key. Settings › Keyboard › Stop key › **Press it on the piano** sets the key that stops the alarm and a song. Device picks another keyboard when more than one is plugged in.
4. **Check the Home tab.** Under CONNECTIONS the keyboard, the broker, Telegram and the bridge each show whether they are working.

**Start with the phone** (Settings › General) is on by default: the bridge starts when the phone starts.

## The tabs

| Tab | What is on it |
|---|---|
| Home | What is playing now and a button to stop it, the bridge's switch, the lesson screen with its address and the screen code, the connections and their state |
| Songs | The songs on the phone, with a search. **Add MIDI files** picks files from the phone. The file name is the name used in Telegram and in the alarm. A long press deletes a song |
| Settings | The broker mode, Telegram, the keyboard (device, stop key, range), the alarm clock, the shortcuts and what they do on the phone, the permissions, General (start with the phone, the language of the Telegram replies), the tools, and Advanced (the admin secret). **Save** at the bottom keeps the settings; a shortcut's action is kept as soon as it is chosen |
| Terminal | The command screen with the `armonico` commands, and the log of what the bridge did |

## Two ways to run

| Mode | When to choose it | What happens |
|---|---|---|
| Broker on the phone | No Home Assistant and no broker on the network | The app runs a small MQTT broker of its own. The bridge, the lessons and the recorder talk through it on the phone, and the status screen reaches it on the network over websockets on port 9001. The Telegram bot is read by the app |
| Broker on the network | A broker already exists, Home Assistant's or another | The bridge connects to that broker, as on Linux. Fill in its address, port, user and password |

Home Assistant connects only through **Broker on the network**. The details of the built-in broker are in [android/README.md](../android/README.md#two-ways-to-run).

## Home Assistant

1. Choose **Broker on the network**, with the address of Home Assistant's broker (for example the Mosquitto broker app) and the MQTT user and password set for it.
2. Put the piano's package and blueprints into Home Assistant by hand, as in [home-assistant.md](home-assistant.md#installing-the-package). The installer's token step is for Linux only.
3. Settings › Advanced › **Admin secret**: the same value as `armonico_admin_secret` in Home Assistant's `secrets.yaml`. Without it `/pianoreset` from Home Assistant does not work. A phone that replaces a Linux machine can take that machine's value.

## Telegram

The bot can be read by Home Assistant or by the app, never by both.

- **Through Home Assistant:** leave **Token from BotFather** empty. Home Assistant reads the bot, as with Linux ([telegram.md](telegram.md)).
- **By the app:** paste the token from BotFather and save. Then **Connect my chat**, and send `/start` to the bot within 2 minutes. The bot answers that chat only. If Home Assistant already reads that bot, make a new bot for the app.

The bot runs while the bridge runs.

## The alarm clock

Settings › **Alarm clock** rings a song on the piano at a time set in the app, with no Home Assistant, so it works in both broker modes. **Add an alarm** asks for the time, the days of the week (none means once: it turns itself off after ringing) and the song. A switch beside each alarm turns it on or off, a touch changes its time, days or song, and a long press deletes it. The card shows the next alarm, and Android shows it in the status bar too.

The alarm is handed to Android as an alarm clock (AlarmManager `setAlarmClock`), the kind clock apps use: it wakes the phone at the exact minute, also in Doze, and starts the bridge if it is off. A bridge that has just started gives the keyboard up to 15 seconds to be found. Then the song rings exactly as an alarm from Home Assistant does: again and again, on the piano, until the stop key, **Stop** in the notification or on the Home tab, or `/pianostop` stops it. Its status goes out on MQTT as always (`piano/alarm/status`, with the source `Armonico app alarm clock`), and a restart in the middle does not ring it again. Without the keyboard it is reported as failed, with the reason.

From Android 13 the permission for exact alarms comes with the install. On Android 12, Settings › Permissions has a row for "Alarms and reminders". After a restart of the phone, an update of the app, or a change of the clock or the time zone, the next alarm is set again by itself.

An alarm from Home Assistant (the Piano alarm clock blueprint, through **Broker on the network**) still rings as before; the two can be used together.

## The watch app

A small app for a Wear OS watch (Wear OS 3 or later, such as a Samsung Galaxy Watch4 or newer) that works with the phone app. It talks to the phone over the Bluetooth link the two already have, through Google Play services' Wearable Data Layer, so the watch needs no network settings of its own. Both apps carry the same package name and are signed with the same key, which that link requires; a test build of the phone app works with a test build of the watch app.

| On the watch | What it does |
|---|---|
| Home | The logo, whether the bridge runs and the keyboard is connected, what plays with round Pause and Stop buttons, then a list scrolled with the bezel: the alarm clock with the next alarm, songs, shortcuts, the bridge, the phone, settings |
| Stop, Pause, Resume | The same as on the phone, for a song or the alarm |
| Turn the bridge on / off | Starts or stops the bridge on the phone (starting it from the watch needs Background running allowed on the phone) |
| Songs | Plays a song from the phone on the piano |
| Shortcuts | Runs a shortcut as if it was played: it goes out on MQTT and does its action on the phone |
| Alarm clock | The phone app's alarms: on or off, a new one, its time, days and song. Tap the hours or the minutes and turn the bezel to change them. A change made on the watch is saved in the phone app, and the other way round |
| Open on the phone | Opens the phone app on the phone |
| Settings | Whether the watch shows a ringing alarm, vibrates, and sounds; the snooze length (5, 10 or 15 minutes); what the watch's buttons do while it rings; the shortcut on the tile |

When an alarm rings on the piano, from the app's alarm clock or from Home Assistant, the watch shows it full screen and vibrates (and sounds, when chosen) until it stops: **Stop** stops it on the piano, **Snooze** stops it and rings again after the chosen minutes, **Quiet the watch** only stops the watch. The alarm itself always rings on the piano.

While it rings, the watch's physical buttons (and Back) do what Settings › Buttons while ringing says: snooze, stop, or quiet the watch. On a Galaxy Watch with a programmable Quick button, that button can be set in the watch's own settings (Advanced features › Customize buttons) to open Armonico.

### The tile and the Complication

The **tile** is a screen beside the watch face (swipe from the watch face; add it with a long press on the tiles): the logo, the keyboard's state, the next alarm in large type (or the song that plays), three round buttons with the same icons (turn the bridge on or off, pause or play, stop), and one shortcut chosen in the watch app's settings. A tap elsewhere on it opens the watch app.

The **Complication** is a small item placed on the watch face itself (long press the watch face › Customize › Complications › Armonico): it shows the time of the next alarm, or ⏰ while it rings, or ♪ while a song plays. A tap opens the watch app. Both are redrawn whenever the phone sends a new state.

### Installing the watch app

A watch installs an app that is not in the store over ADB, from a computer or from the phone:

1. On the watch: Settings › About watch › Software › tap **Software version** 5 times. Then Settings › Developer options: **ADB debugging** and **Wireless debugging** on. Wireless debugging shows the watch's address and port, and **Pair new device** a pairing code and port.
2. From a computer with ADB (the Android platform tools, or `adb` in WSL), on the same Wi-Fi:

```bash
adb pair <watch address>:<pairing port>      # then type the pairing code
adb connect <watch address>:<port>
adb install armonico-watch.apk
```

   From the phone instead: an ADB app on the phone, such as Bugjaeger or Wear Installer, pairs with the watch the same way and installs the APK file.
3. Turn ADB debugging off on the watch afterwards. An update installs the same way, over the app, keeping its settings.

## Shortcuts on the phone

A piano shortcut can also do something on the phone itself, with no other app needed. Home Assistant still gets the shortcut as before. Settings › **Shortcuts** lists the shortcuts saved on the phone; **Choose** sets what each one does:

| Action | What it does | Needs |
|---|---|---|
| Open an app | Opens the app chosen from the list | Display over other apps |
| Open a link | Opens an address: a web page, `tel:` for a call, `geo:` for a map | Display over other apps |
| Start an activity (intent) | An intent to an activity, with the fields of App Inventor's Activity Starter: Action, Package, Class, Data (URI), Type (MIME) and Extras, one `key=value` a line | Display over other apps |
| Send a broadcast (intent) | A broadcast intent with an Action, an optional Package and Extras, and the extra `shortcut` with the shortcut's name. For an automation app, if one is ever used | Nothing |
| Flashlight on or off | Turns the flashlight on, and off at the next press | Nothing |
| Volume up, down, mute | The media volume | Nothing |
| Play or pause, next, previous | The music app that is playing | Nothing |
| Do Not Disturb on or off | Turns it on, and off at the next press | Do Not Disturb access |

**Display over other apps.** Android does not let an app open a screen while it is in the background, and the bridge always is. This permission is the exception the user grants, and it is asked for at the first start, and in Settings › Permissions. On Xiaomi, Redmi and Poco phones a second switch of their own, "Display pop-up windows while running in the background", is needed too; the Permissions card has a row that opens it.

**Test** in the form of an intent runs it at once, to check the fields before saving. What each shortcut did on the phone shows in the Terminal tab, with the reason when it did not run.

**Teach a shortcut**, in the same card, asks for a name and then listens: play the keys, and the shortcut is saved after 5 seconds of silence, after which the choice of what it does on the phone opens. A long press on a shortcut deletes it. How the bridge recognizes a shortcut (1.5 seconds of silence for 1 to 3 keys, a pause before a longer one) is in [telegram.md](telegram.md#shortcuts). The command screen (`shortcut learn <name>`) and Telegram (`/seqlearn`) work as before.

## Lessons

The Home tab shows the lesson screen's address and the **Screen code**. **Open the lesson screen** opens it in a window of the app. The address also opens in a browser on any device on the same network, and the code is entered once on each device. While a lesson runs, the keys belong to it: songs wait and shortcuts do not run.

The touch status screen is at `/status` of the same address ([screen.md](screen.md)).

## The tools

Settings › Tools runs what the `armonico` command does on Linux, and the result shows on the command screen in the Terminal tab:

| Tool | What it does |
|---|---|
| Full check | Checks the bridge, the broker, MQTT, the keyboard, the lesson screen, Telegram, the recorder and free space, and saves a report |
| Unstick | Brings back a keyboard that went silent, without unplugging it |
| Full reset | Unstick, the keyboard opened again, the broker and the recorder again |
| Check for an update | Looks for a newer release, and links to its APK |
| MQTT recording | Every broker message, to one file a day, kept 14 days |

The command screen takes the same commands as `armonico` on Linux, and the Telegram ones. `help` lists them. Its answers, like the app's own dialogs, are in the app's language (the EN / עב button), while Telegram's answers follow Settings › General; the bridge's log stays in English, as on Linux. The full list is in [android/README.md](../android/README.md#the-armonico-command).

## Security

The lesson screen (port 8099) and the status screen's websockets (port 9001) answer on whatever Wi-Fi the phone is on, unencrypted, protected by the screen code and the broker logins. On a public or shared network (a hotel, a café, a hotspot others use), everyone on it can reach them: turn the bridge off there, or keep the screen code on. More in [SECURITY.md](../SECURITY.md).

## Update

Settings › Tools › **Check for an update**, or `update` on the command screen, finds the newest release and links to its APK. Downloading it and opening it installs over the app and keeps the settings and songs.

## Uninstall

Like any app: a long press on its icon, then Uninstall. Android deletes its settings, songs and progress with it.

## Troubleshooting

| Symptom | Check |
|---|---|
| The keyboard shows Not connected | The cable or adapter has to be OTG. Unplug it and plug it in again |
| The bridge stops when the screen is off | Home › Background running › Allow. Some phones also have a battery setting of their own for each app |
| The alarm did not ring | The phone powered and the keyboard plugged in. Settings › Permissions: Background running allowed, and on Android 12 Alarms and reminders. The Terminal tab's log says why an alarm failed |
| The phone does not charge with the keyboard plugged in | A hub or adapter with PD pass-through, on a phone that supports charging as USB host |
| A shortcut does not open its app | Settings › Permissions: Display over other apps allowed, and on Xiaomi, Redmi and Poco the pop-up switch too. The Terminal tab says why |
| The files app opens when the keyboard is plugged in through a USB hub | The hub has a card reader, which the phone sees as a USB drive even with no card in it, and opens the files app for it; Armonico does not. A hub without a card reader, or an OTG cable straight to the keyboard, avoids it |
| The phone's sound comes out of the keyboard | A keyboard that is also a USB audio interface (the PSR-E363 is one) becomes the phone's speaker, and Android sends all its sound there by itself; the app does not choose this. To keep the sound on the phone: Developer options › Media › Disable USB audio routing. It applies to every USB audio device |
| The bot does not answer | Only one program may read a bot. If Home Assistant reads it, empty the token in the app, or use another bot |
| The watch shows Waiting for the phone app | The phone app installed and opened once, the watch connected to the phone in Galaxy Wearable, and both apps from the same kind of build (both test, or both release) |
| Something else | Settings › Tools › Full check, then the Terminal tab |
