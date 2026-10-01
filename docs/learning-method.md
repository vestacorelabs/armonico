# The learning method

The lesson engine teaches a song the way many people who play by ear already practice: a short part of the song with the key numbers in front of them, then from memory, then with the numbers again, then from memory, until it stays. That works well for short parts and breaks down on long ones, because too much new material in one sitting does not stick. The engine keeps what works and fixes what breaks: it cuts every song into short parts, adds only a few new parts in each lesson, and brings each part back until it sticks. There is no clock: the player decides when the next lesson starts, even right after the last one.

The lessons teach the melody of a song.

## Key numbers

Every white key has a number, 1 being the leftmost white key of the keyboard. A black key is written as the white key on its left plus `#`, so `11#` is the black key right of white key 11. On a 61-key keyboard middle C is key 15. The lesson screen draws the whole keyboard with its numbers, and lights the key to play next.

Numbers were chosen over note names on purpose: they need no music reading, and they map straight onto the keyboard. Stickers with numbers on the white keys help a lot in the first weeks.

The browser lesson can choose numbers (`1`, `15#`), note names (`C4`, `C#4`), solfège (`Do`, `Do#`), computer-key labels, or no labels. The Pi's own piano display supports only numbers, note names, solfège, or no labels; computer-key labels are browser-only because the Pi's buttons are piano keys, not computer keys. If the profile is set to computer-key labels, the Pi falls back to numbers. In the numbered round it shows the selected piano labels and note colors; in **Keys lit** it hides labels and colors and lights only the next key; **From memory** removes those hints entirely. Key numbers always count from the first white key of the full configured keyboard range, not from the left edge of whichever octave the Pi is showing.

## A lesson

A session starts from the lesson screen at `http://<machine>:8099`, from `/lesson ode_to_joy` in Telegram, or from `armonico lesson start ode_to_joy` on the machine. The song is split where the music breathes, mostly into parts of 4 to 8 notes: a part ends at a rest once it has 4 notes, a part longer than 8 is cut again at its own longest rest, and a tail of 1 to 3 notes joins the part before it as long as the two stay within 10 notes. So a part can have up to 10 notes, and a tail that cannot join stays shorter than 4. A song with no timing at all is cut every 4 notes, its last few joining the part before them. A session has four stages, and the first and last of them are the same run of the song:

1. **The run that measures.** Everything learned so far, in one go, in the order of the song and from its first note. First on your own, then the same again with the band. The run on your own is the one that is measured: the band does not lead the player, but chords under the melody are themselves a reminder of what comes next, so a part is measured where nothing is helping it. This run opens the lesson on purpose, when those parts were last played a day or a week ago and nothing has warmed them up. Up to two parts that slipped are practiced right after it, with the numbers back on. A song with no parts learned yet skips this stage.
2. **New parts.** As many as the chosen pace allows today (see below), each through three help steps:
   - **Numbers:** the keyboard plays the part first, after a count-in, with the numbers, colors and suggested fingers on the screen. Then it is the player's turn.
   - **Keys lit:** no numbers, no colors and no demo. Only the key to play next is lit.
   - **From memory:** nothing on the screen but the position in the part.
   - A slip from memory goes back one step, to the keys lit. A slip with the keys lit goes back to the numbers. The part is done when a round from memory comes out clean.
   - The three steps are the default. In the lesson screen's settings (⚙), "Rounds for a new part" keeps only some of them, for example numbers and memory without the keys lit. A slip then goes back to the step before it among those kept, and the part is done when the last of them comes out clean. The same settings hold "Count-in clicks", the clicks before each round (4 by default, 0 for none), and "Pause after the opening run", the seconds between the run at the start and the first new part (1 by default). They belong to the profile, like the pace.
3. **Connecting.** Each new part is played together with the one before it, from memory, so the song grows as one line and not as separate fragments.
4. **The same run again.** The whole song so far with today's part in it, on your own and then with the band. It comes minutes after practicing those same notes, so it says how the lesson went rather than what will still be there tomorrow, and it does not move a part's count. Up to two parts that slipped in it are practiced right after. Comparing the two runs is what shows the day's gain: *at the start 4 of 7 parts clean, at the end 7 of 8*.

When the MIDI file has other instruments besides the melody, they play along as a band. A file with the melody alone, like the six included pieces, has no band, so there is one run at each point instead of two. The band follows the player: it waits on a hesitation, speeds up and slows down with the player's pace, and plays as loud as the keys are pressed. It never starts on its own and comes in with the first key of a run. Whatever the file plays before its melody track begins (a verse, an introduction) is left out, and only the instrument settings are kept.

Three small additions sit around these stages without changing them:

- **A warm-up of other songs.** Before the first run, up to 3 parts of *other* songs the player learned are played once each, a few seconds per part: the ones memory is about to let go of, still remembered now but under 90% within half a day. Each is played from memory. Only when a note is forgotten are the keys lit, and then the numbers. A wrong key found again is a slip of the finger, graded as in the run that opens the lesson (below), and goes to the drills of that step. Parts of the song of the lesson are never in the warm-up, because the run that opens the lesson has to find them cold. A warm-up moves a part's memory, not its count of clean showings.
- **A tricky step, alone.** When the step between two notes keeps going wrong, the part that holds it starts with a short drill of just that step: the note before, the note itself and the one after it, with the numbers and then once from memory. The drill is named by the two notes of the step, for example "Part 2: practicing the move between note 3 and note 4". At most 3 such drills in a lesson.
- **A good place to stop.** When the mistakes of the last rounds clearly rise above those at the start of the lesson, the screen says once, right after a part that went well: *Good place to stop: end on this one*. It is only a suggestion. The lesson goes on until the player stops it.

When the song is fully learned and steady, a session is the two runs and whatever slips in them. Progress is saved after every part, so a stopped lesson loses nothing. A run that was stopped in the middle only judges the parts it actually reached.

### One more try before the hint

A wrong key does not show the answer at once. The player gets one more try, because finding the key from memory is itself what makes it stick. Only a second wrong key, or being stuck, shows the expected key, and only that one.

Help is never withheld altogether, at any stage. In a run there is no extra try: after a few seconds of silence the next note is named by itself. Sitting stuck in front of a note that will not come teaches nothing, so the note is always named in the end. A note that had to be named counts as a note that was not remembered, the same as a wrong key, so its part is practiced again and its count starts over.

### When a part comes back

There are no waiting times and no daily limit, and there is no separate test of a part on its own. A melody is remembered forward, from its beginning, so a part is measured where it is really played: inside the full run, in order, from the first note of the song.

- Every part keeps a count of clean showings in a row, and it counts as **steady** at **3**. The lesson a part was learned in gives it the first, so a new part needs two more, each one a clean **run that opens a lesson**.
- A part that slipped in an opening run is practiced again and its count starts over from 0, so from there it takes three clean opening runs.
- The run at the end of the lesson is measured and shown, but does not change any count. A part played right after practicing it says little about tomorrow, and counting it would settle parts that have not really settled.

This also settles a song in the order it is really learned. The beginning is played in every single run and steadies first, the middle follows, and the newest parts, which are played least, get the most practice.

The screen shows each part as **settling** while its count is under 3, and as **steady** once it reaches it. Parts from different songs are never mixed.

### Pace, tempo and the score

- **Pace:** Relaxed (1 new part in each lesson, demo from 50%), Steady (2, from 60%) or Fast (3, from 75%). Chosen on the lesson screen.
- **Tempo adapts to you:** on by default. The demo starts at the pace's speed. Two slips in a row slow it down a step, and a part played clean at the first try speeds up a step next time. Each part remembers how far it moved from the pace's speed, so changing the pace moves every part with it, and the full run plays at the middle one. The speed slider is hidden while the tempo adapts.
- **Fixed speed:** with the adapting tempo off, the slider sets one demo speed for everything. Choosing a pace moves the slider to that pace's speed, and the slider can fine-tune it from there.
- **The full run is scored** in four numbers: right notes, rhythm (the gaps between notes compared with the song, after taking out the player's overall tempo, so playing the whole song slower is fine), hesitations (a gap more than twice as long as it should be) and wrong keys.

### Why a few parts in each lesson

A few new parts at a time keeps each lesson short and focused. There is no daily cap: more lessons in a row bring more parts, and the full run at the end of each one brings back everything learned so far. New motor memory is still consolidated during sleep, so parts learned in a long sitting tend to need a few more lessons before the opening run finds them steady.

### Why colors, when a real piano has none

The lesson screen gives every note its own color, the same in every octave, so every C is the same red. Colors and numbers are training wheels: while a part is new, they make each key easy to find at a glance, so the attention stays on the music, much as a teacher points at the keys in a first lesson. A real piano has neither, so the rounds from memory take both away together. What remains is where the key is on the piano, not its color. Studies of color-coded notation found that learners who only ever practiced with colors came to rely on them, and the rounds without colors are what prevents that. The screen also offers a Simple mode with a single color for every key.

## What the lessons learn about the player

A few small models, from before the time of AI, follow the player from lesson to lesson (`lessons/piano_learner.py`). Each is a few lines of arithmetic that runs on a Raspberry Pi, and none changes its mind after one lesson: every piece of evidence moves it a little, so one bad day does not undo a good week.

| Model | What it keeps | What it decides |
|---|---|---|
| Memory, for every part | A stability, the days until the chance of remembering the part falls to 90%, and a difficulty from 1 to 10. Each showing from memory, in the run at the start of a lesson or in a warm-up, gets a grade from 1 to 4 for memory: again (a note had to be named or was skipped, or the part needed the keys lit or the numbers), hard (a hesitation, or two wrong keys or more found again), good (clean, or a single wrong key found again), easy (clean and every gap even). A single wrong key found again is a finger that slipped, not a note forgotten: it goes to the drills of that step instead (Steps, below). Good grows the stability, more the longer the part went unplayed; hard grows it a little, easy a lot, again shrinks it. A showing within half a day of the first showing of that day moves it by a bounded factor only, and all of that day's showings together keep it between half and a little above what the first one left, so one slip in a second lesson the same day does not erase weeks. The day is counted from its first showing, so lessons 8 to 11 hours apart still grow the part every other time instead of never. A new part starts more stable when it was learned at the first try, less when it needed rounds again. The curves are those of FSRS-4.5 ([open-spaced-repetition](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm), MIT license) with its published default weights; the same-day rule, the start and the grades are Armonico's own | Which slipped parts are practiced first: the ones memory holds least firmly. How many new parts a lesson brings: one fewer than the pace while two or more parts are fading, one more when every part is fresh and stable for 3 days or more. Which parts of other songs the warm-up takes, and which songs show parts gone too far for a warm-up, worth a lesson of their own |
| The player's own forgetting curve | Every graded showing, kept as a short history. Once there are 100 of them (same-day ones do not count), the two weights that set how much a part grows and how much it falls are fitted to the player: the curve is replayed over the older showings for a small grid of values around the weights in use, with a pull back toward the defaults that weakens as the history grows, and the fall weight moves only after 15 forgotten showings. The latest fifth of the showings, never used to fit, judges the result | A curve that follows this player's memory rather than the average, kept only when it predicts the latest showings at least 1% better than both the curve in use and the defaults. It is tried at most once a day, each weight moves at most 2 small steps, and each try records how well the chances matched what happened (log-loss and the gap by days since the last showing) |
| Skills | For black keys, leaps, long parts, rhythm, playing from memory and fluency, the chance that the skill is already the player's own ([Bayesian Knowledge Tracing](https://en.wikipedia.org/wiki/Bayesian_knowledge_tracing)). Every part needs some of them, read from its own notes, and moves them once a lesson, on its first attempt: the same part tried again minutes later is not new evidence. A skill unused drifts back toward the start, halfway in 90 days, counted from the lesson that last used it; a skill the last lesson used does not drift. Fluency moves with hesitations: a right note after a pause more than twice as long as the player's usual gap in that part is known, but not fluent yet. The pause into a part's first note is not counted, as that is where a part is called up from memory, and with fewer than 4 gaps a hesitation also has to last a second | A part whose skills are still weak starts as a harder part in the memory model. The weakest skill shows in the lesson summary and on the progress page |
| Pace, for every song | The speed the player holds in the full runs: the middle of the speeds between each two right keys in a row, so a stop on a wrong key is not slow playing, smoothed with a trend that fades a little each run (Holt's method, damped) | The speed the runs of the next lesson start at: where the player is heading, without overshooting it. A part's own speed goes up a step after two practices in a row clean at the first try, and down a step after two failed rounds or more |
| Steps between two notes | Every wrong key or named note counts against the step into it, by its place in the song and by its two notes and the interval between them, so the same step in another song is recognized too. An error weighs half as much a week later, and a drill that went right takes half of it away | Which parts start with a drill of their tricky step alone: one whose step has gone wrong about twice lately. When several parts are practiced after a run, their tricky steps are drilled one of each in turn first, then the parts: steps mixed are kept longer than steps repeated in a block |
| Fatigue, within a lesson | Whether each round of the lesson had a mistake | The suggestion to stop on a success, when the last third of the rounds (8 at least) went wrong at least half the time and clearly more than the first third: a rise that chance alone gives less than 2% of the time (a one-sided Fisher test), looked at every 3 rounds. A steady lesson, good or hard, rarely calls itself tired |

The parts' count of clean showings, and when a part counts as steady, stay as described above. The grades move only the memory model, never the count. The models only choose the order of the practice, the number of new parts, the starting speed, the warm-up, the drills of tricky steps and the suggestion to stop. What they learned is kept in the profile's `learner.json` and in its progress, and goes with the backup.

## Hands and fingers

For every note the engine calculates which hand and which finger plays it, keeping hand movement to a minimum. Each hand rests on five white keys, one finger per key, the same way fingers rest on the home row of a computer keyboard. The screen shows the finger under every number (`R3` is the right hand's middle finger), and the lesson's first message says where to place both hands.

## Keys during a lesson

Every key of the keyboard belongs to the song: none is held back for controlling the lesson. A lesson ends with the stop button on the screen, `/lessonstop` in Telegram or `armonico lesson stop`. The stop key (the lowest key) still stops the alarm, which always wins over a lesson. Shortcuts are paused while a lesson runs, so practicing never triggers the house by accident.

## Levels

The method stays the same at every level. What changes is the material:

| Level | Material | How to use it |
|---|---|---|
| Beginner | Short, familiar tunes: the included Ode to Joy and Minuet in G, or any saved shortcut | Stickers with numbers on the keys, and every part through the full cycle. The finger numbers on the screen matter more than speed |
| Intermediate | Full songs downloaded with `/getmidi`, with their accompaniment | The run with the band becomes the main goal, and a score above 90% is a fair mark of a learned song |
| Advanced | Longer and faster pieces | `/lesson <song> reset` to learn a known song again from memory only, which shows which parts were really learned and which were only recognized |

## Songs

Any MIDI file in the songs folder can be a lesson. The engine picks the melody track by itself (the track with the highest notes), moves the song to the key with the fewest black keys that fits the keyboard, and keeps the other tracks as the band. When the tune and its accompaniment share one track, as both hands do in most solo piano files, the tune is told apart note by note: of notes struck together the highest is kept, a lower note struck under a tune note that is still held is the other hand, and a single note an octave or more below the tune that the next notes leave again is a bass note in a gap of the tune. A melody still wider than the keyboard is not refused: each part keeps its own notes and moves as a whole by an octave to fit, so the tune itself does not change inside a part. A shortcut with the same name as a MIDI file takes its notes from the shortcut and its rhythm and band from the file.

`/getmidi <name>` searches free archives (the Mutopia Project and Wikimedia Commons, see [sources.md](sources.md)), and `/getmidi <number>` downloads one of the results into the songs folder, with its credit. The lesson screen has the same search. `/lessons` lists every song and how far each one got. In the terminal these are `armonico getmidi` and `armonico lesson list`.
