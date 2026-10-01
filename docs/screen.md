# Touch status screen

`screen/piano-status.html` is a page for a touch display on a Raspberry Pi next to the keyboard. It reads the size and orientation of the browser window and fits itself, so 3.5, 5, 7 and 10 inch screens (DSI, HDMI or anything else), landscape or portrait, all work. If it looks too small or too large, add `&scale=1.2` (or `0.8`) to the address. It shows what the piano is doing and has big buttons to stop it.

The home screen is a grid of big tiles, in the style of KlipperScreen. Tap a tile to open it, "Home" to come back.

| Tile | Shows |
|---|---|
| Status | Player, alarm, lesson, last shortcut |
| Playing | The song now playing, and buttons to stop the song or the lesson |
| Practice | Streak, minutes and lessons today, songs due, last accuracy, profile |
| Keys | A live keyboard, the last notes and a graph of key activity |
| System | Last disconnect, broker connection, connection history, the live log |
| Theme | Cycles night, day and clay. The browser remembers the choice, and the first time it follows the device's light or dark setting. `?theme=light`, `dark` or `warm` in the address sets it |

The top bar always shows the connection, the song playing and the alarm. "Stop all" at the bottom is on every screen.

## Language

The screen starts in English, whatever the language of the browser. The button in the top bar, which always names the other language (**עברית** or **English**), switches every text on it, the direction of the page and the swipe between screens. The choice is kept on that device. `?lang=he` or `?lang=en` in the address sets it and keeps it too, which is the way for a display that has no one to press the button.

## Open it from any device

The lesson engine serves the page at `http://<piano machine>:8099/status`, behind the same screen code as the lesson screen, so a phone, a tablet or another computer opens it with a browser. Nothing has to run on those devices, and no display server (X11 or Wayland) is needed on the piano machine. The page gets the broker address and login from the lesson engine, once the device is signed in. Nothing about the broker is typed or put in the address, and the login is not kept on the device.

From the lesson screen, Settings has a "Show status" button. It opens `/status` with the broker login filled in by the lesson engine, so the address stays clean and nothing is typed. It works on any device that is signed in to the lesson screen (the screen's own code applies as everywhere else). Old links that carry `?user=`, `?pass=` or `?broker=` still open the page, but those values are ignored and removed from the address.

The status screen runs in the browser of another device, so the installer's broker answers websockets on port 9001 on the network (the page picks the port itself, nothing to type). Port 1883 stays on the machine. Every login still needs its password and is kept to its own topics.

## Broker

The page talks MQTT over websockets, on port 9001 of the broker. The installer's local broker listens for them there, on the same addresses as port 1883 and under the same logins and rules. When the lesson engine uses a broker on another machine, the page connects to that one and tries port 1884 first, which is the websockets port of Home Assistant's Mosquitto app, and then 9001. The installer opens 1884 on that app by itself when it sets Home Assistant up with a token; by hand it is under Configuration, Network in the app.

## A display on the Pi itself

Only a screen wired to the Pi needs a display server. Something has to draw the page on that screen, and on Raspberry Pi OS that is X11 (or Wayland) with Chromium on top. It is the same reason KlipperScreen needs X11. A Pi without a screen, or a screen you reach from another device, needs none of it.

## Install on the Pi

1. Raspberry Pi OS with the desktop, display connected, touch working.
2. Start Chromium in kiosk mode at login, `~/.config/autostart/armonico-screen.desktop`. On the piano machine itself the address is `localhost`; on another Pi use the piano machine's address:

```
[Desktop Entry]
Type=Application
Name=Armonico Screen
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --overscroll-history-navigation=0 "http://localhost:8099/status"
```

3. The first time, enter the screen code on the page. The pass is kept in the browser profile for 14 days and renewed on every visit, so the screen stays signed in.
4. Turn screen blanking off (`raspi-config`, Display options).
5. If the Pi is not the piano machine, let the Pi reach ports 8099 and 9001 and keep them closed to the rest of the network.

Opened from a file (`file://`) instead of the lesson engine, the page runs a demo with made-up data, which is how to check the look.
