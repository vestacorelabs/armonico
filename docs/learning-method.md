# The learning method

The lesson engine teaches a song the way many people who play by ear already practice: a short part of the song with the key numbers in front of them, then from memory, then with the numbers again, then from memory, until it stays. That works well for short parts and breaks down on long ones, because too much new material in one sitting does not stick. The engine keeps what works and fixes what breaks: it cuts every song into short parts, adds only a few new parts in each lesson, and brings each part back until it holds. There is no clock: the player decides when the next lesson starts, even right after the last one.

The lessons teach the melody of a song. A mode for the full piano part, with the left hand's accompaniment, is planned as a separate feature.

## Key numbers

Every white key has a number, 1 being the leftmost white key of the keyboard. A black key is written as the white key on its left plus `#`, so `11#` is the black key right of white key 11. On a 61-key keyboard middle C is key 15. The lesson screen draws the whole keyboard with its numbers, and lights the key to play next.

Numbers were chosen over note names on purpose: they need no music reading, and they map straight onto the keyboard. Stickers with numbers on the white keys help a lot in the first weeks.

## A lesson

A session starts from the lesson screen at `http://<machine>:8099`, from `/lesson ode_to_joy` in Telegram, or from `armonico lesson start ode_to_joy` on the machine. The song is split where the music breathes, into parts of 4 to 8 notes. A session has four stages, and the first and last of them are the same run of the song:

1. **The run that measures.** Everything learned so far, in one go, in the order of the song and from its first note. First on your own, then the same again with the band. The run on your own is the one that is read: the band does not lead the player, but chords under the melody are themselves a reminder of what comes next, so a part is measured where nothing is helping it. This run opens the lesson on purpose, when those parts were last played a day or a week ago and nothing has warmed them up. Up to two parts that slipped are practiced right after it, with the numbers back on. A song with no parts learned yet skips this stage.
2. **New parts.** As many as the chosen pace allows today (see below), each through three help steps:
   - **Numbers:** the keyboard plays the part first, after a count-in, with the numbers, colors and suggested fingers on the screen. Then it is the player's turn.
   - **Keys lit:** no numbers, no colors and no demo. Only the key to play next is lit.
   - **From memory:** nothing on the screen but the position in the part.
   - A slip from memory goes back one step, to the keys lit. A slip with the keys lit goes back to the numbers. The part is done when a round from memory comes out clean.
   - The three steps are the default. In the lesson screen's settings (⚙), "Rounds for a new part" keeps only some of them, for example numbers and memory without the keys lit. A slip then goes back to the step before it among those kept, and the part is done when the last of them comes out clean. The same settings hold "Count-in clicks", the clicks before each round (4 by default, 0 for none), and "Pause after the opening run", the seconds between the run at the start and the first new part (1 by default). They belong to the profile, like the pace.
3. **Connecting.** Each new part is played together with the one before it, from memory, so the song grows as one line and not as separate fragments.
4. **The same run again.** The whole song so far with today's part in it, on your own and then with the band. It comes minutes after practising those same notes, so it says how the lesson went rather than what will still be there tomorrow, and it does not move a part's count. Up to two parts that slipped in it are practiced right after. Comparing the two runs is what shows the day's gain: *at the start 4 of 7 parts clean, at the end 7 of 8*.

When the MIDI file has other instruments besides the melody, they play along as a band. A file with the melody alone, like the six included pieces, has no band, so there is one run at each point instead of two. The band follows the player: it waits on a hesitation, speeds up and slows down with the player's pace, and plays at a volume that matches how hard the keys are pressed. The band follows the player and never starts on its own: it comes in with the first key of a run. Whatever the file plays before its melody track begins (a verse, an introduction) is left out, and only the instrument settings are kept.

When the song is fully learned and steady, a session is the two runs and whatever slips in them. Progress is saved after every part, so a stopped lesson loses nothing. A run that was stopped in the middle only judges the parts it actually reached.

### One more try before the hint

A wrong key does not show the answer at once. The player gets one more try, because finding the key from memory is itself what makes it stick. Only a second wrong key, or being stuck, shows the expected key, and only that one.

Help is never withheld altogether, at any stage. In a run there is no extra try: the show key names the next note at any moment, and after a few seconds of silence it is named by itself. Sitting stuck in front of a note that will not come teaches nothing, so the note is always named in the end. A note that had to be named counts as a note that was not remembered, the same as a wrong key, so its part is practiced again and its count starts over.

### When a part comes back

There are no waiting times and no daily limit, and there is no separate test of a part on its own. A melody is remembered forward, from its beginning, so a part is measured where it is really played: inside the full run, in order, from the first note of the song.

- Every part keeps a count of clean showings in a row, and it counts as **steady** at **3**. The lesson a part was learned in gives it the first, so a new part needs two more, each one a clean **run that opens a lesson**.
- A part that slipped in an opening run is practiced again and its count starts over from 0, so from there it takes three clean opening runs.
- The run at the end of the lesson is measured and shown, but does not change any count. A part played right after practising it says little about tomorrow, and counting it would settle parts that have not really settled.

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

## Hands and fingers

For every note the engine calculates which hand and which finger plays it, keeping hand movement to a minimum. Each hand rests on five white keys, one finger per key, the same way fingers rest on the home row of a computer keyboard. The screen shows the finger under every number (`R3` is the right hand's middle finger), and the lesson's first message says where to place both hands.

## Keys during a lesson

| Key | Action |
|---|---|
| The first black key from the bottom | Show the numbers again, and in a run name the note it is waiting for |
| The third black key from the bottom | Leave the lesson |
| The stop key (the lowest key) | Still stops the alarm, which always wins over a lesson |

On a keyboard that starts on C, these are C# and F# of the lowest octave, keys that songs almost never use. Both can be changed in `config.env` (`LESSON_SHOW_KEY`, `LESSON_EXIT_KEY`, see [configuration.md](configuration.md)). Shortcuts are paused while a lesson runs, so practicing never triggers the house by accident.

## Levels

The method stays the same at every level. What changes is the material:

| Level | Material | How to use it |
|---|---|---|
| Beginner | Short, familiar tunes: the included Ode to Joy and Minuet in G, or any saved shortcut | Stickers with numbers on the keys, and every part through the full cycle. The finger numbers on the screen matter more than speed |
| Intermediate | Full songs downloaded with `/getmidi`, with their accompaniment | The run with the band becomes the main goal, and a score above 90% is a fair mark of a learned song |
| Advanced | Longer and faster pieces | `/lesson <song> reset` to learn a known song again from memory only, which shows which parts were really learned and which were only recognized |

## Songs

Any MIDI file in the songs folder can be a lesson. The engine picks the melody track by itself (the track with the highest notes), moves the song to the key with the fewest black keys that fits the keyboard, and keeps the other tracks as the band. A shortcut with the same name as a MIDI file takes its notes from the shortcut and its rhythm and band from the file.

`/getmidi <name>` searches free archives (the Mutopia Project and Wikimedia Commons, see [sources.md](sources.md)), and `/getmidi <number>` downloads one of the results into the songs folder, with its credit. The lesson screen has the same search. `/lessons` lists every song and how far each one got. In the terminal these are `armonico getmidi` and `armonico lesson list`.
