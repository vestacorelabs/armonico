# Home Assistant

## The short way: one token

On a first install, in a terminal, the installer offers to set Home Assistant up: **"Set Home Assistant up now? [Y/n]"**. It needs one access token from an administrator of Home Assistant:

1. In Home Assistant, open your profile (your name, bottom left), the **Security** tab, and scroll to **Long-lived access tokens**.
2. **Create token**, with a name such as `armonico`, and copy it. It is shown once.
3. Paste it into the installer. It is not shown while you paste: the installer says `Token received: 183 characters` and the first and last four, so you know it arrived. In PuTTY, paste with a right click or Shift+Insert, once.
4. The address it asks for is the one you open in the browser, with its port when it has one: `http://192.168.1.10:8123`, or `http://192.168.1.10` when Home Assistant answers on port 80. A bare address is tried on 8123 and then on 80.

Then, over Home Assistant's own API and in this order:

| Step | What it does | What you see |
|---|---|---|
| The token | Checks it and reads the version | `Connected to Home Assistant 2026.9.4` |
| The broker | Installs and starts the **Mosquitto broker** app, and opens its websockets port 1884 for the status screen. Home Assistant OS and Supervised only | `The Mosquitto broker app is running` |
| The login | Clears the Supervisor's login cache, makes a Home Assistant user `armonicopiano` (not an administrator) with a random password, and checks that the broker accepts it with a real MQTT login | `Attempt 1 of 6 (user armonicopiano): the broker accepts it` |
| The integration | Adds the **MQTT** integration to Home Assistant, pointed at the app with that login | `The MQTT integration is set up` |
| After the setup screen | The 3 helpers, the 5 automations, the 3 blueprints, and the 28 sensors under one device named **Piano**, with the entity ids the automations use | `28 sensors under one device named Piano ...` |

No file is copied into Home Assistant, `configuration.yaml` is not touched and nothing restarts. The installer never changes a user that already exists: an administrator called `piano` stays as it is, and the piano gets its own `armonicopiano`.

The whole step takes one to five minutes, and every line appears as it finishes.

When it is done, **delete the token** in the same profile page. By default the installer keeps it in memory only, and sends it only to the address you gave. If you tell it to keep the token for updates, it is written to `/etc/armonico/secrets.env` (root only) and every update refreshes the package and the blueprints; see [installation.md](installation.md#update). Do not delete it in Home Assistant while it is kept. On a local network that address is usually plain `http`, so it travels the way your own Home Assistant login does.

Declining the token opens a page at the end of the setup screen that walks through Home Assistant step by step, with a blue **Open Home Assistant** button for each step.

### When it does not go through

| What the installer says | What it means | What to do |
|---|---|---|
| `Cannot reach Home Assistant at ...: Connection refused` | Nothing answers at that address and port | Use the address exactly as it opens in the browser, with its port |
| `Home Assistant refused the token` | The token was copied in part, or deleted | Make a new one and paste it once |
| `This Home Assistant has no apps (it is Container or Core)` | There is no Mosquitto app to install | Use a broker of your own, or let the installer put Mosquitto on the piano machine (option 2). The sensors, automations and blueprints can still be added, see below |
| `Attempt N of 6 ... the broker says Not authorized` | The Supervisor keeps the last password that worked for a user name, and answers a login with a new password for the same name "no" before it asks Home Assistant. The broker remembers that refusal for 5 minutes. The installer clears that cache before every attempt and tries up to 6 times with a new password each | Nothing, it normally passes on the first or the second attempt. After the fourth attempt a numbered name is used (`armonicopiano2`), and after the sixth the app's own login option |
| `Adding the MQTT integration, attempt N of 3: ... 400 ...` | Home Assistant wanted a setting the installer did not send | Add the integration by hand (below) and send the text of the message |
| `The MQTT integration still has to be accepted` | The integration was not added | Settings, Devices & services, **MQTT** card, Submit. Or Add integration, MQTT, with the broker, port 1883, and the login from `sudo grep MQTT_ /etc/armonico/config.env` |
| `... automations were saved, but only N are loaded` | `configuration.yaml` does not load `automations.yaml` | Add the line `automation: !include automations.yaml` and reload |
| `... sensors announced, N have the expected entity id` | The sensors were still being registered | Run the Home Assistant part again (below) a minute later |

### Running the Home Assistant part again, or on its own

The token step runs on a first install. Everything it does for the sensors, helpers, automations and blueprints can be run later, for any Home Assistant that already has the MQTT integration on the same broker as the piano, Container and Core included:

```bash
sudo env HA_URL=http://192.168.1.10:8123 HA_TOKEN='the token' \
  MQTT_HOST=<broker> MQTT_PORT=1883 MQTT_USER=<user> MQTT_PASS='<password>' \
  /opt/armonico/venv/bin/python /opt/armonico/setup/ha_setup.py package
```

The broker values are in `/etc/armonico/config.env`. Running it again replaces the automations and blueprints, keeps the helpers, and announces the sensors again. Do not combine it with the YAML package below: the sensors and the automations would exist twice.

### Taking it out again

`sudo ./install.sh --uninstall` and `--purge` print this list at the end, and do not touch Home Assistant, which would need a token at that moment and would delete what may have been edited since. `--uninstall` and `--purge` both clear the retained messages the piano left on the broker, and with them the sensors it announced, so the device **Piano** goes from Home Assistant by itself. The rest, in Home Assistant: the automations named `Piano: ...`, the three helpers `piano_alarm_stop`, `piano_log_chat_id` and `piano_last_played`, the blueprints under `piano/`, the user `armonicopiano` (Settings, People, Users) and, when nothing else uses it, the Mosquitto app. The sensors go away with the device **Piano**: Settings, Devices & services, MQTT, the device, Delete. Then `sudo ./install.sh --uninstall` on the piano machine.

The rest of this page is the manual way, and what is behind the short one.

The `home-assistant/` folder holds everything Home Assistant needs:

| File | Contents |
|---|---|
| `piano.yaml` | 28 MQTT sensors under one "Piano" device, 3 helpers, and 5 automations: the Telegram commands, their answers, keyboard alerts, the end of the alarm and the last played time |
| `blueprint_alarm.yaml` | Alarm clock: a song at a set time, repeated until stopped |
| `blueprint_shortcut.yaml` | Any action when a shortcut is played |
| `blueprint_reminder.yaml` | A practice reminder on days with no lesson yet |

## Requirements

- The **MQTT** integration, connected to the same broker as the piano.
- The **Telegram bot** integration, for the Telegram commands only. `armonico telegram` adds it, with your chat, after you make the bot in BotFather. See [telegram.md](telegram.md).

## The broker in Home Assistant

The piano and Home Assistant have to use the same broker. With Home Assistant OS or Supervised, the broker is the **Mosquitto broker** app (called an add-on before Home Assistant 2026.2):

1. Settings, Apps: **Mosquitto broker**, installed from the app store and started.
2. Settings, Devices & services: Home Assistant discovers the broker and offers the **MQTT** integration, which is accepted.
3. A user for the piano: Settings, People, Users, a new user of its own, such as `pianobroker`, with no administrator rights and not the name of a person. The Users tab appears only with Advanced mode on in the user profile. The Mosquitto broker app accepts every Home Assistant user and no anonymous logins, and the names `homeassistant` and `addons` are reserved for its own use.
4. On the piano machine: `sudo ./install.sh`, answer 1 (an existing broker), and in the setup the address of the Home Assistant machine, port 1883, and the user from step 3.

The address is usually filled in already. Before it asks anything the installer listens for Home Assistant on the network over mDNS, and a Home Assistant in a container on the same machine is found on port 8123 of localhost, so the address, the version and the location name come from Home Assistant itself. It also opens port 1883 on that address to see whether the Mosquitto broker app answers there, and says which of the two it found. The user name and the password are asked for in every case: the app signs in with Home Assistant user accounts, and none of them can be read from outside Home Assistant. A suggested address can be replaced, and an install that found nothing asks exactly the questions it always asked. Details and the cases where nothing is found are in [installation.md](installation.md#finding-home-assistant).

Home Assistant Container and Core have no apps. There, the broker is a separate Mosquitto, for example installed by the piano's installer (option 2), and the MQTT integration is pointed at it: Settings, Devices & services, Add integration, MQTT, with the piano machine's address and a user of that broker.

There has to be one broker only. With a second broker on the piano machine while Home Assistant uses its own, the two never see each other's messages.

## Installing the package

### Getting the files onto the Home Assistant machine

The files live in the repository on the piano machine, and Home Assistant reads them from its own configuration folder, `/config`. Which way they travel depends on the install:

| Home Assistant | The configuration folder | Ways in |
|---|---|---|
| OS or Supervised | `/config` inside Home Assistant | The **Samba share** app (the `config` share, over the network), the **File editor** or **Studio Code Server** apps (paste the file), or `scp` into `/config` when the **Terminal & SSH** app is installed |
| Container or Core | The folder mounted as `/config` (for example `~/ha/config` on the host) | `scp`, or a plain copy on the machine itself |

From the piano machine, with the Samba share mounted or the SSH app running, one file is one command:

```bash
scp home-assistant/piano.yaml <ha-machine>:/config/packages/
scp home-assistant/blueprint_*.yaml <ha-machine>:/config/blueprints/automation/piano/
```

### The steps

1. Packages enabled in `configuration.yaml`, if they are not yet:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. `packages/piano.yaml` copied into the `packages` folder next to `configuration.yaml`, which is created if it is missing.
3. The admin secret in `secrets.yaml`, next to `configuration.yaml`. `/pianocode` and `/pianoprofile` are guarded commands, and the lesson engine answers them only with this secret. The value is `MQTT_ADMIN_SECRET` on the piano machine (`sudo grep MQTT_ADMIN_SECRET /etc/armonico/config.env`, without the quotes):

   ```yaml
   armonico_admin_secret: <value>
   ```

   Without this line the configuration check reports `Secret armonico_admin_secret not defined` and the package does not load.
4. Settings, Tools, YAML, **Check configuration** in the configuration validation section, and then a restart of Home Assistant.

The sensors appear under a device named **Piano** (Settings, Devices & services, MQTT). Every sensor has a unique ID, so its name, icon and area can be changed from the interface.

A sensor with no value yet is normal: most topics are retained, so their last value arrives the moment Home Assistant connects, but a topic the piano has not published since the broker started stays empty until it does. What actually reaches the broker is visible without any of the package: Settings, Devices & services, the **MQTT** integration, **Configure**, and `piano/#` in **Listen to a topic**.

### Moving from helpers made in the interface

The package defines its helpers in YAML: `input_boolean.piano_alarm_stop`, `input_text.piano_log_chat_id` and `input_datetime.piano_last_played`. Helpers with the same IDs that were created earlier in the interface have to be deleted first (Settings, Devices & services, Helpers). Otherwise Home Assistant creates copies with `_2` at the end and the automations keep using the old ones.

## Adding it to a Home Assistant that is already in use

The package only adds. Everything in it is named `piano_…`, it changes no other entity or automation, and its automations react only to Telegram commands and to `piano/` topics. Answers meant for the `armonico` command in a terminal (a chat ID that starts with `cli-`) are left alone. What it adds:

| Kind | Items |
|---|---|
| MQTT sensors | 28, under a device named **Piano** (`sensor.piano_status`, `sensor.piano_alert`, `sensor.piano_language`, `sensor.piano_profile`, `sensor.piano_parts_due` and the rest) |
| Helpers | `input_boolean.piano_alarm_stop`, `input_text.piano_log_chat_id`, `input_datetime.piano_last_played` |
| Automations | `piano_telegram_commands`, `piano_telegram_forward`, `piano_alert_forward`, `piano_alarm_stopped`, `piano_last_played` |

What can go wrong, and how it shows:

| Situation | Symptom | Prevention |
|---|---|---|
| An older copy of the package stays next to the new one | Every Telegram command is answered twice | Replace the old file, never add a second one |
| Helpers with the same IDs were made in the interface | Copies named `…_2`, automations using the old ones | Delete them first (see above) |
| Packages are not enabled | The configuration check reports `piano` keys as invalid | The `homeassistant: packages:` lines above |
| The admin secret is not in `secrets.yaml` | The configuration check reports `Secret armonico_admin_secret not defined` | The `armonico_admin_secret:` line above |
| The installer's setup and this manual package are both used | Every sensor and automation exists twice | Use one of the two: delete the installer's automations and the device **Piano** before the package goes in |

The order that keeps the current setup safe:

1. A backup: Settings, System, Backups, **Create backup**.
2. The new `piano.yaml` over the old one, in the same place. When an older version is there, `diff old/piano.yaml new/piano.yaml` shows exactly what changes.
3. Settings, Tools, YAML, **Check configuration**. Only a green result goes on to a restart.
4. Restart, then check: Settings, Devices & services, MQTT, the **Piano** device lists its sensors, and `/pianostatus` answers once.
5. To undo: the old file back (or the file removed), check, restart. The backup from step 1 restores everything else.

### Trying it without the main Home Assistant

Watching the topics shows everything the sensors would show, with no Home Assistant at all:

```bash
mosquitto_sub -h <broker> -u <user> -P <password> -t 'piano/#' -v
```

A throwaway Home Assistant on another machine, such as the piano's Raspberry Pi, can load the package against the same broker. Without the Telegram bot integration it never answers the bot, so the main Home Assistant keeps the bot to itself. With Docker:

```bash
docker run -d --name ha-test -v ~/ha-test:/config -e TZ=Asia/Jerusalem --network=host \
  ghcr.io/home-assistant/home-assistant:stable
```

The package there needs the same `armonico_admin_secret` line in `~/ha-test/secrets.yaml`. It opens on port 8123 of that machine, needs a few hundred MB of memory, and goes away with `docker rm -f ha-test && rm -rf ~/ha-test`. Blueprint automations such as the alarm clock belong in one Home Assistant only: in both, the alarm would be sent twice.

## Installing the blueprints

The installer's token step puts them there by itself. By hand, the `blueprint_*.yaml` files from `home-assistant/` go into `config/blueprints/automation/piano/`, followed by a reload of the automations (Settings, Tools, YAML, the reloading section, **Automations**). They then appear under Settings, Automations & scenes, Blueprints.

### Piano alarm clock

Inputs: time, days, song, and minutes between retries. At the set time the song plays in a loop until the stop key is pressed, `/pianostop` is sent, or anything else publishes to `piano/stop`. When the keyboard is off or unplugged at that moment, or is lost while the alarm plays, the alarm is sent again every few minutes (only while it is not playing) until it plays and somebody stops it. The package's automation **Piano: alarm stopped** ends that loop only when a person stopped the alarm (the stop key, `/pianostop`, `piano/stop`); a restart of the bridge or a keyboard that vanished does not end it. If Home Assistant was set up before this version, run `sudo armonico integrations` (or update with kept tokens) to replace the automation and the blueprint.

One automation per alarm time. All of them share the `input_boolean.piano_alarm_stop` helper, which the package switches on whenever the alarm stops.

### Piano shortcut

Inputs: the shortcut's name and the actions to run. For example, a shortcut named `good_night` that turns off the lights:

```yaml
use_blueprint:
  path: piano/blueprint_shortcut.yaml
  input:
    shortcut: good_night
    actions:
      - action: light.turn_off
        target:
          area_id: bedroom
```

### Piano practice reminder

Inputs: time, days, whether to stay quiet when no part is due, and the action that sends the message. At the set time it checks `sensor.piano_lessons_today`: with a lesson already done that day, nothing is sent. The message follows the language of the lesson screen and names the parts due, the song with the most of them, and the streak. It reaches the action as `{{ message }}`:

```yaml
use_blueprint:
  path: piano/blueprint_reminder.yaml
  input:
    remind_time: "19:00:00"
    only_when_due: true
    notify:
      - action: telegram_bot.send_message
        data:
          message: "{{ message }}"
```

The numbers come from `piano/practice`, which the lesson engine publishes after every lesson and every 10 minutes, so a part that comes due during the day is counted.

## Playing a song from any automation

```yaml
- action: mqtt.publish
  data:
    topic: piano/play
    payload: "ode_to_joy|{{ this.attributes.friendly_name }}"
```

The part after `|` is shown by `/pianostatus` as "Started by", so the status always names the automation that started the song.

## The database

`sensor.piano_log` changes with every log line while the live log is on. Keeping it out of the history database saves space:

```yaml
recorder:
  exclude:
    entities:
      - sensor.piano_log
```
