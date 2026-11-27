# Practice

Play your part along with the song and record yourself. Your waveform runs under the original part's, and you can listen to your last take along with the song.

## How to use

1. Press the target button in the row of the part you play (before S and M). Mute that part with M if you want to replace it.
2. A row **You** appears right under it. Its buttons, on the right of its left side: record and the latency.
3. Measure the latency once (see below).
4. Press record, then ▶, and play. Pausing, or pressing record again, ends the take. While record is on, your last take is not heard and not drawn: the lane shows only what you record now.
5. Your last take plays along with the song like any part; M on the **You** row mutes it.
6. Once there is a take, the button in the top right corner of its waveform saves two FLAC files to `Music/Exports` in the Audio Stems folder: the song with your take as you hear it now, and only what you recorded (full volume, no EQ, in time with the song). The folder opens with the file selected.

Choose the audio input in Settings → Plugins → Practice.

## Measuring the latency

The latency is the time from a sound in the headphones to its coming back through your input. Measured once, it puts what you play where you played it.

1. Plug in as you play: the instrument (or microphone) into the input you chose, wired headphones into the output. Wireless headphones cannot be measured.
2. Hold one earcup against the pickup of your instrument (or against the microphone), the volume as you play.
3. Press the stopwatch button, then **Start**, and keep still. Clicks play one at a time; when the last ones agree, it says **Stable** and keeps the result for this input and output.

Measure again after changing the input, the output or the headphones. The result is kept for this input and output by their names; for a pair never measured, the last result is used.

To correct it, use **Adjust by hand** under the result: −1 / +1 ms or type the value. Each change is saved at once and moves your last take with it.

## Good to know

- The input is taken without echo cancelling, noise suppression or automatic gain.
- At 100 % speed your take lines up with the song most exactly. At other speeds the song is stretched, which blurs note starts by 10–20 ms.
- Takes are kept as WAV files, up to 100 MB per project; the oldest go first. They are deleted with the project or the plugin.

## Permissions

- **Records from your audio input.**
