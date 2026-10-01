# device/ui: the screens of the Armonico Pi

Every screen is drawn on one `Canvas` at the display's own resolution, on an 800x480 plan, in
English or Hebrew (mirrored right to left, except what is physical: the buttons, the keys, the sky
and the character). `screen.py` starts it (`start_ui`) and keeps the lesson engine, the rest video,
MQTT and the lesson requests; when this package cannot load, the older screens in `screen.py` run.

| File | What it does |
| --- | --- |
| `canvas.py` | The drawing surface: shapes, text (Hebrew through python-bidi, a number kept beside its Latin unit: "58 MB", not "MB 58"), the plan on the real screen. |
| `sky.py` | The real sky over the city, the classic look, the palettes, the week's events, the Hebrew date, the city search, NASA's picture of the day. |
| `characters.py` | The figure on the rest screen (house, keys, grand; the metronome is kept only as comments), its eyes, touches and the owl. |
| `sleep.py` | The sleep screen: the clock, the next alarm and the weather now (Open-Meteo), the Moon in its phase, the figure asleep, all dim and drawn twice a second. |
| `app.py` | `App`: the loop, input (touch, mouse, USB keyboard, the 4 buttons over MQTT), sleep, night mode, the background threads (sky, address, diagnosis, update check), the display size. |
| `widgets.py` | The bar of 4 buttons, header (with the CPU temperature on the test screens), cards, the 13 keys, the toast, the confirm box, the job screen, and `Job` (steps that wait, run, end). |
| `views_main.py` | Rest, lesson (with the line of the part's rounds by ear), summary, songs, free play, hardware test, the network screen, the two-displays chooser. |
| `views_settings.py` | The settings in 12 groups, the edit screen (touch keyboard, USB keyboard, phone paste), the city search, the job screen, and their actions (broker, Home Assistant, Telegram, sound output, profile). |
| `views_setup.py` | The setup wizard on the device: language, place, time, sound, connection, broker / Home Assistant / Telegram, look, done. |
| `views_wifi.py` | Wi-Fi: the networks around, the password, the connection and the saved networks (Settings › Network and links › Wi-Fi), and the setup's Wi-Fi step, through the root unit's wifi-* actions. |
| `views_check.py` | The full check (`device/fullcheck.py`): the automatic checks, the 9 guided steps (a step that ends by itself waits for Next), the summary, the details of each result with the lines behind it, all results, the report and its Telegram summary, and the offer after an update. |
| `views_deep.py` | The deep check (`device/deepcheck.py`): the opening, the 30 seconds of measuring, and the result as a list scrolled by the buttons or a drag, with a note beside each number. |
| `views_more.py` | Backup and restore (through `device/backup.py`), the full diagnosis and its fixes (`device/diag.py`), the update, About and the credits. |
| `tour.py` | The opening tour after the first setup (7 steps through the screens, the character with a pen looping around each part, one line in a bubble) and the one-time tips in Songs, Free play and the first lesson. |
| `talk.py` | What the character on the rest screen says when touched (lines put together from parts, never twice in a run), the echo game of 3 notes, and the stars that ring when touched. |
| `texts.py` | Every word on the screens, English and Hebrew, with the About pages, credits, network lists and job steps. |
| `system.py` | What the screen asks of the machine: root requests (`device/root_actions.py` through `armonico-device-request.path`), the broker login test, `setup/find_ha.py`, `setup/ha_setup.py`, the Telegram helpers. |
| `phone.py` | The small page on port 8097, open only while a token field is, for pasting a token from a phone with a 4-digit code. |
| `data/` | The star catalog and the built-in cities. |

Each view is a method `view_<name>(t)`: it draws itself and declares its bar with `self.bar(...)`, so
a touch on the bar and a press of the real button do the same thing. Touch areas are added with
`self.hit(...)` in layout units and stored as they show (mirrored in Hebrew).
