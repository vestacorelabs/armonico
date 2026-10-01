# device/ui: the screens of the Armonico Pi

Every screen is drawn on one `Canvas` at the display's own resolution, on an 800x480 plan, in
English or Hebrew (mirrored right to left, except what is physical: the buttons, the keys, the sky
and the character). `screen.py` starts it (`start_ui`) and keeps the lesson engine, the rest video,
MQTT and the lesson requests; when this package cannot load, the older screens in `screen.py` run.

| File | What it does |
| --- | --- |
| `canvas.py` | The drawing surface: shapes, text (Hebrew through python-bidi), the plan on the real screen. |
| `sky.py` | The real sky over the city, the classic look, the palettes, the week's events, the Hebrew date, the city search, NASA's picture of the day. |
| `characters.py` | The figure on the rest screen (house, keys, grand, metro), its eyes, touches and the owl. |
| `sleep.py` | The sleep screen: the clock, the next alarm and the weather now (Open-Meteo), the Moon in its phase, the figure asleep, all dim and drawn twice a second. |
| `app.py` | `App`: the loop, input (touch, mouse, USB keyboard, the 4 buttons over MQTT), sleep, night mode, the background threads (sky, address, diagnosis, update check), the display size. |
| `widgets.py` | The bar of 4 buttons, header, cards, the 13 keys, the toast, the confirm box, the job screen, and `Job` (steps that wait, run, end). |
| `views_main.py` | Rest, lesson, summary, songs, free play, hardware test, the network screen, the two-displays chooser. |
| `views_settings.py` | The settings in 12 groups, the edit screen (touch keyboard, USB keyboard, phone paste), the city search, the job screen, and their actions (broker, Home Assistant, Telegram, sound output, profile). |
| `views_setup.py` | The setup wizard on the device: language, place, time, sound, connection, broker / Home Assistant / Telegram, look, done. |
| `views_wifi.py` | Wi-Fi: the networks around, the password, the connection and the saved networks (Settings › Network and links › Wi-Fi), and the setup's Wi-Fi step, through the root unit's wifi-* actions. |
| `views_check.py` | The full check (`device/fullcheck.py`): the automatic checks, the 9 guided steps, the summary, the report and its Telegram summary, and the offer after an update. |
| `views_more.py` | Backup and restore (through `device/backup.py`), the full diagnosis and its fixes (`device/diag.py`), the update, About and the credits. |
| `tour.py` | The opening tour after the first setup (the character, a speech bubble, the part explained outlined) and the one-time tips in Songs, Free play and the first lesson. |
| `texts.py` | Every word on the screens, English and Hebrew, with the About pages, credits, network lists and job steps. |
| `system.py` | What the screen asks of the machine: root requests (`device/root_actions.py` through `armonico-device-request.path`), the broker login test, `setup/find_ha.py`, `setup/ha_setup.py`, the Telegram helpers. |
| `phone.py` | The small page on port 8097, open only while a token field is, for pasting a token from a phone with a 4-digit code. |
| `data/` | The star catalog and the built-in cities. |

Each view is a method `view_<name>(t)`: it draws itself and declares its bar with `self.bar(...)`, so
a touch on the bar and a press of the real button do the same thing. Touch areas are added with
`self.hit(...)` in layout units and stored as they show (mirrored in Hebrew).
