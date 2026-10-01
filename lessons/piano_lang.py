"""
piano_lang.py - Hebrew for the lesson engine: the lesson screen's messages and the Telegram replies.

Deliberately small: the English text is the key, and HE holds its Hebrew. A text with no
entry stays in English, so a missing line never breaks anything. The Hebrew is written as
Hebrew is spoken, not word for word.

Telegram shows each line in the direction of its first letter, so a Hebrew line gets a
right-to-left mark at its start; the leading emoji moves to the end of the line, where a
right-to-left reader ends; and runs of key numbers (17 16 15#) get left-to-right marks so
they keep their order.
"""
import re

LANGS = ("en", "he")

HE = {
    # ---------- the lesson screen, during a lesson ----------
    "Count-in": "ספירה",
    "Not that one": "לא זה",
    "Not that one. Try again": "לא זה. עוד ניסיון",
    "Stuck. Note {i} is {key}": "אין לחיצה. תו {i} הוא {key}",
    "Not that one. Note {i} is {key}": "לא זה. תו {i} הוא {key}",
    "Note {i} is {key}": "תו {i} הוא {key}",
    "The full run is done. Well done": "הנגינה המלאה הסתיימה. כל הכבוד",
    "Right": "נכון",
    "From memory": "מהזיכרון",
    "Right, from memory": "נכון, מהזיכרון",
    "A little slower: {speed}%": "קצת יותר לאט: {speed}%",
    "{title} · with numbers": "{title} · עם מספרים",
    "{title} · without numbers": "{title} · בלי מספרים",
    "{title} · keys lit": "{title} · קלידים מוארים",
    "▶ The full runs": "▶ הנגינות המלאות",
    "🔁 Part {k}": "🔁 חלק {k}",
    "🔹 Part {k} of {n}": "🔹 חלק {k} מתוך {n}",
    "🔗 Parts {a} and {b}": "🔗 חלקים {a} ו-{b}",
    "🎻 Everything in one go, with the band": "🎻 הכל ברצף אחד, עם הלהקה",
    "▶ Everything learned so far, on your own": "▶ כל מה שנלמד עד עכשיו, לבד",
    "▶ Everything, with today's part, on your own": "▶ הכל, כולל החלק של היום, לבד",
    "🎻 The same with the band": "🎻 אותו דבר עם הלהקה",
    "On your own: {clean} of {n} parts clean": "לבד: {clean} חלקים נקיים מתוך {n}",
    "{clean} of {n} parts clean. The same again, with the band": "{clean} חלקים נקיים מתוך {n}. עכשיו אותו דבר, עם הלהקה",
    "At the start, on your own: {clean} of {n} parts clean": "בהתחלה, לבד: {clean} חלקים נקיים מתוך {n}",
    "At the end, on your own: {clean} of {n} parts clean": "בסוף, לבד: {clean} חלקים נקיים מתוך {n}",
    "▶ Everything learned so far, in one go": "▶ כל מה שנלמד עד עכשיו, ברצף אחד",
    "Right {f}": "ימין {f}",
    "Left {f}": "שמאל {f}",
    " (stretch)": " (מתיחה)",

    # ---------- songs added from the screen ----------
    "The file could not be read: {e}": "אי אפשר לקרוא את הקובץ: {e}",
    "No melody was found in it": "לא נמצאה בו מנגינה",
    "This is not a MIDI file": "זה לא קובץ MIDI",
    "The file is empty": "הקובץ ריק",
    "The image is larger than {n} MB": "התמונה גדולה מ-{n} MB",
    "Only PNG, JPEG, GIF or WebP images can be used": "אפשר להשתמש רק בתמונות PNG, JPEG, GIF או WebP",
    "The file was not saved: {e}": "הקובץ לא נשמר: {e}",
    "The download failed: {e}": "ההורדה נכשלה: {e}",
    "No archive answered: {e}": "אף מאגר לא ענה: {e}",
    "This is already the newest version": "זו כבר הגרסה העדכנית",
    "no release published yet": "עוד לא פורסמה גרסה",
    "GitHub did not answer: {e}": "GitHub לא ענה: {e}",

    # ---------- Telegram ----------
    "⏹ Stopping the lesson...": "⏹ עוצר את השיעור...",
    "ℹ️ No lesson is running": "ℹ️ אין שיעור פעיל כרגע",
    # ---------- profiles ----------
    "Main": "ראשי",
    "👥 Profiles:": "👥 פרופילים:",
    "To switch: /pianoprofile <name>": "להחלפה: /pianoprofile <שם>",
    "❓ No profile named {name}. The list: /pianoprofile": "❓ אין פרופיל בשם {name}. הרשימה: /pianoprofile",
    "ℹ️ {name} is already at the piano": "ℹ️ {name} כבר ליד הפסנתר",
    "ℹ️ Not while a lesson or a replay is playing": "ℹ️ לא בזמן שיעור או הקלטה שמתנגנת",
    "🔒 {name} has a PIN: switch on the lesson screen": "🔒 ל-{name} יש קוד אישי: מחליפים במסך השיעורים",
    "👋 {name} is at the piano now": "👋 עכשיו ליד הפסנתר: {name}",
    "🔑 Lesson screen code: {code}\nEnter it once on each screen, it is remembered there":
        "🔑 קוד מסך השיעורים: {code}\nמזינים אותו פעם אחת בכל מסך, והוא נשמר שם",
    "🔓 The lesson screen has no code. Turn it on with: code on":
        "🔓 למסך השיעורים אין קוד. כדי להדליק: code on",
    "🔓 The screens are no longer asked for a code. Everyone on the network can use the piano":
        "🔓 המסכים כבר לא נדרשים לקוד. כל מי שברשת יכול להשתמש בפסנתר",
    "🔒 The screens are asked for a code again: {code}":
        "🔒 המסכים נדרשים לקוד שוב: {code}",
    "🔑 A new code was drawn: {code}\nThe screens already signed in stay in":
        "🔑 הוגרל קוד חדש: {code}\nהמסכים שכבר נכנסו נשארים בפנים",
    "❌ {action} did not go through. The log has the details":
        "❌ {action} לא עבר. הפרטים ביומן",
    "No archive answered": "אף מאגר לא ענה",
    "🚫 That command needs the admin secret from the settings file":
        "🚫 הפקודה הזו דורשת את סוד הניהול מקובץ ההגדרות",
    "ℹ️ A lesson is already running. To stop it: /lessonstop": "ℹ️ כבר יש שיעור פעיל. כדי לעצור אותו: /lessonstop",
    "❌ A name is needed. Example: /getmidi bella ciao": "❌ צריך שם של שיר. לדוגמה: /getmidi bella ciao",
    "❌ No result with that number. Search first: /getmidi <name>":
        "❌ אין תוצאה עם המספר הזה. קודם מחפשים: /getmidi ושם השיר",
    "⬇️ Downloaded: {name}\nNo melody could be found in it": "⬇️ ירד: {name}\nלא נמצאה בו מנגינה",
    "⬇️ Downloaded: {name}\nFrom {source}, {license}\n{notes} notes, {parts} phrases\nOpening: {opening}\nTo start: /lesson {stem}":
        "⬇️ ירד: {name}\nמקור: {source}, {license}\n{notes} תווים, {parts} חלקים\nפתיחה: {opening}\nכדי להתחיל: /lesson {stem}",
    "\n⚠️ The word {words} is in no name in the archives. A typo?":
        "\n⚠️ המילה {words} לא מופיעה באף שם במאגרים. אולי יש שגיאת כתיב?",
    "\n⚠️ Not answering right now: {names}": "\n⚠️ לא עונים כרגע: {names}",
    "❌ Nothing found for: {q}{typo}": "❌ לא נמצא כלום עבור: {q}{typo}",
    "🔎 Results for {q}:": "🔎 תוצאות עבור {q}:",
    "\nTo download: /getmidi <number>": "\nלהורדה: /getmidi ומספר התוצאה",
    "❌ A song name is needed. Example: /lesson ode_to_joy": "❌ צריך שם של שיר. לדוגמה: /lesson ode_to_joy",
    "⏰ The alarm is playing, the lesson did not start": "⏰ השעון המעורר מנגן, השיעור לא התחיל",
    "❌ No song named: {name}": "❌ אין שיר בשם: {name}",
    "🔄 {song} starts over": "🔄 {song} מתחיל מחדש",
    "🔌 The keyboard is not connected, the lesson did not start": "🔌 המקלדת לא מחוברת, השיעור לא התחיל",
    "🔌 The keyboard is on the bus, but this program cannot open its raw MIDI port, the lesson did not start. amidi -l must list it for the user that runs the lessons":
        "🔌 המקלדת על הפס, אבל התוכנה לא יכולה לפתוח את פורט ה-MIDI הגולמי שלה, והשיעור לא התחיל. הפקודה amidi -l חייבת להציג אותה למשתמש שמריץ את השיעורים",
    "{n} part is not steady yet, and the run at the start is what settles it": "חלק אחד עוד לא יציב, והנגינה שבהתחלה היא שמייצבת אותו",
    "{n} parts are not steady yet, and the run at the start is what settles them": "{n} חלקים עוד לא יציבים, והנגינה שבהתחלה היא שמייצבת אותם",
    "everything learned so far, on your own and then with the band": "כל מה שנלמד עד עכשיו, לבד ואז עם הלהקה",
    "everything learned so far, on your own": "כל מה שנלמד עד עכשיו, לבד",
    "all of it again at the end, with today's part in it": "הכל שוב בסוף, כולל החלק של היום",
    "{n} new part": "חלק חדש אחד",
    "{n} new parts": "{n} חלקים חדשים",
    " and ": " ו-",
    "\n\n✋ Starting position:\n• Left: {l}-{l4} (thumb on {lt})\n• Right: {r}-{r4} (thumb on {rt})":
        "\n\n✋ תנוחת פתיחה:\n• יד שמאל: {l}-{l4} (האגודל על {lt})\n• יד ימין: {r}-{r4} (האגודל על {rt})",
    "🎹 Piano lesson: {song}\n📚 Learned: {learned} of the song's {total} parts\n\nToday:\n{today}{where}\n\n⌨️ Keys:\n{show} = show the numbers again\n{exit} = leave the lesson":
        "🎹 שיעור פסנתר: {song}\n📚 נלמדו {learned} מתוך {total} החלקים של השיר\n\nהיום:\n{today}{where}\n\n⌨️ קלידים:\n{show} = להציג שוב את המספרים\n{exit} = לצאת מהשיעור",
    "🔹 Part {k} of {n}: {keys}": "🔹 חלק {k} מתוך {n}: {keys}",
    "\nSuggested fingers: {fingers}": "\nאצבעות מומלצות: {fingers}",
    "⏰ The alarm took over, the lesson stopped": "⏰ השעון המעורר נכנס, השיעור נעצר",
    "⏹ The lesson stopped": "⏹ השיעור נעצר",
    "⏹ Left the lesson": "⏹ יציאה מהשיעור",
    "⌛ Nothing was played, the lesson closed": "⌛ לא נוגן כלום, השיעור נסגר",
    "🔌 The keyboard disconnected, the lesson stopped. Progress is saved": "🔌 המקלדת התנתקה והשיעור נעצר. ההתקדמות נשמרה",
    "❌ The keyboard disconnected in the middle: {e}": "❌ המקלדת התנתקה באמצע: {e}",
    "🏁 {song}: {learned} of the song's {total} parts learned": "🏁 {song}: נלמדו {learned} מתוך {total} החלקים של השיר",
    "New parts in this lesson: {n}": "חלקים חדשים בשיעור הזה: {n}",
    "Slipped and practiced again: parts {parts}": "היו טעויות ותורגלו שוב: חלקים {parts}",
    "In one go: notes {score}%": "ברצף אחד: תווים {score}%",
    "In one go with the band: notes {score}%": "ברצף אחד עם הלהקה: תווים {score}%",
    ", rhythm {rhythm}%": ", קצב {rhythm}%",
    "🎉 The whole song is learned. Every lesson from now on plays it through and works on the weak parts":
        "🎉 כל השיר נלמד. מעכשיו כל שיעור מנגן אותו מההתחלה עד הסוף ומתרגל את החלקים החלשים",
    "The next lesson can start right away, with the next parts": "אפשר להתחיל את השיעור הבא מיד, עם החלקים הבאים",
    "• {name}: {learned} of {total} parts learned": "• {name}: נלמדו {learned} מתוך {total} חלקים",
    "🎹 {n} songs": "🎹 {n} שירים",
    "\nIn progress:": "\nבתהליך:",
    "\nNot started yet:": "\nעוד לא התחילו:",
    "\nTo start: /lesson <name>": "\nכדי להתחיל: /lesson ושם השיר",
    "❌ {action} failed: {e}": "❌ {action} נכשל: {e}",
}

RLM, LRM = "\u200f", "\u200e"
_HEBREW = re.compile(r"[\u0590-\u05ff]")
_LEAD = re.compile(r"^((?:[^\w\s•\-(<]\ufe0f?)+)\s+(.*)$")     # an emoji or symbol at the start, then text
_KEYS = re.compile(r"(?<![\w#])\d+#?(?: \d+#?)+(?![\w#])")      # 17 16 15# : two numbers or more
_CMD = re.compile(r"(?<!\S)/(?=[a-z])")                         # /lesson, /getmidi


def translate(lang, text, **kw):
    """The text in the language, with the values filled in."""
    if lang == "he":
        text = HE.get(text, text)
    return text.format(**kw) if kw else text


def plural(lang, n, one, many, **kw):
    return translate(lang, one if n == 1 else many, n=n, **kw)


def telegram(text):
    """A Hebrew reply laid out for Telegram, line by line. Lines with no Hebrew are left alone."""
    out = []
    for line in text.split("\n"):
        if not _HEBREW.search(line):
            out.append(line)
            continue
        if "/" not in line:
            line = line.replace("_", " ")      # song file names read as titles, but a command keeps them
        m = _LEAD.match(line)
        if m:
            line = f"{m.group(2)} {m.group(1)}"
        line = _KEYS.sub(lambda k: LRM + (" " + LRM).join(k.group(0).split(" ")), line)
        line = _CMD.sub(LRM + "/", line)
        out.append(RLM + line)
    return "\n".join(out)


if __name__ == "__main__":        # the bridge (a shell script) lays out its Hebrew replies with this
    import sys
    sys.stdout.write(telegram(sys.stdin.read().rstrip("\n")))
