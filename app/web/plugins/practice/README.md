# Practice

Play your part along with the song and record yourself. Your waveform runs under the original part's, and after each take you see how many notes were on time.

## How to use

1. Press the target button in the row of the part you play (before S and M). Mute that part with M if you want to replace it.
2. A row **You** appears right under it. Its buttons, on the right of its left side: record, listen to your last take, and the latency.
3. Measure the latency once (see below).
4. Press record, then ▶, and play. Pausing, or pressing record again, ends the take.
5. The score window opens: how many notes were on time, early, late or missed.
6. The headphones button plays your last take along with the song.

Choose the audio input in Settings → Plugins → Practice.

## Measuring the latency

The latency is the time from a sound in the headphones to its coming back through your input. Measured once, it puts what you play where you played it.

1. Plug in as you play: the instrument (or microphone) into the input you chose, wired headphones into the output. Wireless headphones cannot be measured.
2. Hold one earcup against the pickup of your instrument (or against the microphone), the volume as you play.
3. Press the stopwatch button, then **Start**, and keep still. Clicks play one at a time; when the last ones agree, it says **Stable** and keeps the result for this input and output.

Measure again after changing the input, the output or the headphones.

## The score

- Each note of the original part is matched with the nearest note you played, within 80 ms. **On time** is within the tolerance, **Early** and **Late** beyond it, **Missed** means nothing was played near it.
- The histogram shows how far the notes were. If the song's tempo is known, it is labelled in note values too (a beat being a quarter): at 120 BPM, 31 ms is a 1/64 note.
- **On time within** sets the tolerance, 5 to 40 ms, right in the score window or in Settings. The default ±15 ms: about 20 ms apart is the least at which the ear tells which of two notes came first, so notes within ±15 ms sound together with the original. A tighter value trains finer timing; a looser one suits a first go.
- The last 20 scores are kept with the project; the window shows the earlier ones.

## Good to know

- The input is taken without echo cancelling, noise suppression or automatic gain.
- At 100 % speed the score is the most exact. At other speeds the song is stretched, which blurs note starts by 10–20 ms.
- Takes are kept as WAV files, up to 100 MB per project; the oldest go first. They are deleted with the project or the plugin.

## Permissions

- **Records from your audio input.**
- **Runs an analysis on this computer:** the song's beats, for the note values.
