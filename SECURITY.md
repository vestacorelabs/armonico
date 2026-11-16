# Security

## Reporting a problem

Report a security problem privately, not as a public issue.

Use GitHub's private reporting on this repository: **Security → Report a vulnerability**.
That opens a channel only the maintainer can see.

A first answer usually comes within a week. A confirmed problem is fixed, released and
credited to whoever reported it, unless they ask to stay unnamed.

Nothing here is a bug bounty. There is no payment, and there is no legal threat either:
a report made in good faith, without reaching into anyone else's installation, is welcome.

## What this project is

Armonico runs on one machine on a home network, next to a MIDI keyboard. It is not
built to face the internet, and nothing here should be read as a claim that it is.
A port forward to it from a router is outside what this design defends against.

## Known limits

These are choices of the design, not oversights.

- **The screen is plain HTTP.** The pages, the code and the pass cookie cross the home
  network unencrypted. Someone who can listen on that network can see them. A machine on a
  home network has no name a certificate can be issued for, and a self-signed certificate
  teaches people to click through warnings. For anything wider, a reverse proxy with TLS
  goes in front (see `LESSON_HOSTS`).
- **The status page gets the broker login.** A signed-in screen is handed the broker
  address and the `piano` login (`/api/status-login`) so it can talk to the broker. That
  login can read and write the `piano/` topics (so the page can also stop a song), and any
  device that has the screen code can read it. The login is kept in memory only, never in the address and never on the device.
  With a Home Assistant broker the login is a Home Assistant user the setup made for the piano
  (not an administrator), and the Mosquitto add-on lets it read and write every topic. With it,
  someone can also sign in to Home Assistant itself as that user and use the entities there.
- **Turning the screen code off** opens everything to every device on the network (see
  below).
- **The admin secret travels in MQTT commands** that need it, so someone who can read the
  `piano/` topics while such a command is sent can reuse it. The recorder does not write it.
- **The screen code is sent in the answer** to the command that asks for it
  (`piano/shortcut/reply`), so whoever can read that topic sees it. The recorder writes it
  as `******`.
- **The status page's websockets port (9001) is open to the network** on a broker this
  project installed, so the status page works from other devices. It is plain `ws://`: the
  `piano` login crosses the home network unencrypted, like the screen itself.
- **Home Assistant holds the admin secret** so that `/pianocode` and `/pianoprofile` work
  from Telegram. It sits in `secrets.yaml`, or in the automation itself when the installer
  set Home Assistant up, and it shows in that automation's traces. Anyone who administers
  Home Assistant can read it.
- **Topic rules** cannot be enforced on a broker this project did not install, and in the
  Home Assistant add-on they cannot be enforced at all today.
- **The recording of MQTT traffic**, when it is turned on, writes everything that crosses
  the broker to files on this machine. It is off unless it is turned on.

## What it defends

**The lesson screen.** Every read and every action needs a pass. A device enters the
six-digit code once, keeps a signed pass for 14 days, and that pass is renewed on every
visit. The code is shown by `armonico code`, by `/pianocode` in Telegram, and at the end
of the install. "Sign every screen out" in the settings replaces the key the passes are
signed with, which ends every pass that was ever handed out.

Asking for the code can be turned off. Turning it off means every device on the network
can start lessons, play the keyboard, read the practice history, download a full backup
and ask for a system update. The setting says so where it is turned off.

**The commands that arrive over MQTT.** The commands that hand out the screen code or
change whose progress is being recorded answer only to a
request carrying the secret written in `/etc/armonico/config.env`. This is deliberate and
not belt-and-suspenders: the broker cannot be relied on to say who may publish what. Home
Assistant's Mosquitto add-on, in every version of series 7, does not enforce topic rules
at all, because the plugin it authenticates with answers the permission question before
the broker's own rules are ever consulted.

**The broker, when this project installed it.** Two logins with separate jobs. `piano`
reads and writes only under `piano/`. `piano-rec`, which the recorder uses, reads
everything and writes nothing. Port 1883 stays on the machine unless the install was told
another machine needs it. Port 9001, the status page's websockets, answers on the network. On a broker this project did not install, none of this applies,
and the rules there belong to whoever owns it.

**The setup screen.** The installer runs it as the `armonico` user and not as root, even
though the installer itself needs root. Nothing the screen does asks for more: the keyboard
comes from the `audio` group, the broker test is network only, and its answer goes into a
folder of its own that belongs to that user. The installer reads that answer and writes
`config.env` itself, so the settings file has one author. The screen is reachable only by
address or host name, asks for a six-digit code, locks a device out after five wrong answers
(each device on its own), and closes after fifteen minutes without use or once it has saved.

**Updates.** A release is checked against the `SHA256SUMS` published with it before
anything in it runs, and a release with no published checksum is not installed. Both come
from the same GitHub release, so the check catches a broken or changed download on the way,
not a release that was replaced on GitHub itself. The update
and the full reset run as root, and never write through a link in the data folder, which
belongs to the service user.

**The Telegram bot's token on the Armonico Pi.** After the device's own Telegram setup works,
the token goes to `/etc/armonico/secrets.env`, which only root reads, the same file the
installer keeps tokens in. It is never written to the screen's settings and never handed back to
the screen: for the test message the screen asks root, and root sends one fixed text to the chats
the setup found, at most once every 10 seconds. Settings › Telegram deletes it, and so does
`sudo armonico forget-tokens`. The full check asks root only whether a token is saved (true or
false), and its summary goes out the same way: the screen sends counts and short lines, root
checks them, takes addresses and anything shaped like a token out, and sends them in a fixed form.

**Edited wording.** Wording saved for the screen (`text_overrides.json`) may hold HTML, because
it lays out the page. What is saved keeps structure and styling and loses anything that can
execute: script and style blocks, event handlers, and addresses that are really code.
