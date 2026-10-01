# Sorry for asking

The questions people think about Armonico and do not always ask. The answers are honest, including the ones that do not flatter the project. The practical questions, about installing and setting up, are in [Questions and pitfalls](faq.md).

The idea comes from the Israeli TV show of the same name ("סליחה על השאלה", Kan 11), where people answer the questions the public wanted to ask them and never dared to.

### Is this a real piano?

No. Armonico is software around a USB MIDI keyboard, and the keyboard is the instrument. Armonico Pi, the printed device, has 13 tact switches for keys and plays recordings of a real grand piano (the Salamander Grand, CC BY 3.0) on two small speakers. It is good for practicing a melody, and it is not a piano for playing a concert.

### Why only 13 keys on Armonico Pi?

One octave and a high C is what fits in a case that is still portable. In a lesson the keys stay put, so the numbers on them never jump: the C above is played on the same key as the C below (or on the high C key, which is number 1 too) and sounds at the song's own height, an arrow over the key says it is above or below, and a line on the screen shows the tune going up and down. So a song wider than one octave can still be learned. What the device cannot do is two hands far apart, and the lessons do not teach that anyway.

### Why do the keys click?

They are ordinary 6×6 mm tact switches, the cheapest and easiest part to find and solder. They click, and pressing harder does not play louder. Quieter switches exist, and a lesson does not need them.

### Was it built with AI?

Yes. The code, the documentation and the 3D model were written with AI tools, and every change was tested by hand on real hardware. It has been in daily use in the Vesta Core Labs home lab for more than six months, with a real alarm every morning. AI writes code fast and makes mistakes with confidence, which is exactly why every change is tested on a real keyboard before it is published.

### Who is responsible if something breaks?

Nobody. Armonico is a personal project under the MIT license, given as it is, with no warranty of any kind. Bugs are welcome in [Issues](https://github.com/vestacorelabs/armonico/issues), and they are fixed when there is time.

### Is it a product or a company?

Neither. Vesta Core Labs is the name of a home lab, not a business. Nothing is sold, there is no account to make, and nothing is collected.

### Do I have to read notes?

No. A part is first played with key numbers on the screen, then with only the next key lit, and then from memory. The method is explained in [The learning method](learning-method.md).

### Then is it not just copying numbers?

Only in the first round. The numbers are removed on purpose, and a part counts as learned only when it comes out from memory, at the start of a later lesson, before anything warmed it up. A piano teacher would add learning by ear, and that is planned.

### Do I need Home Assistant, a server, or the internet?

No. Home Assistant and Telegram are optional. Armonico Pi works on its own with no network at all. The internet is needed only to search the free archives for new songs.

### Why does the song come out different from the file?

A lesson teaches the melody alone. When both hands are on one track of the file, the tune is told apart from the accompaniment note by note, and a tune wider than the keyboard is moved part by part by an octave to fit. Both are guesses that a person could make differently, and a file with the melody on its own track always gives the cleanest lesson.

### Does it listen to me or record me?

It records the keys pressed in a lesson, so the lesson can be watched again and the ghost run can race the last one. The recordings stay on the machine, in the profile's folder, and a lesson screen on the network asks for a code before it shows them. Lessons never use a microphone.
