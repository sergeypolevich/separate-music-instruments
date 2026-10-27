# Audio Stems

**English** · [Русский](README.ru.md) · [Deutsch](README.de.md)

A practice tool for musicians: split a recording into vocals, drums, bass, guitar, piano and other, and play
along with the tracks in sync — locally, on your own computer. Open your own audio file or drag it onto the
window; you can also find a song on YouTube.

## What it does

- **Opens your own audio file** — MP3, WAV, FLAC, M4A, OGG and others — with a button or dragged onto the
  window. You can also find a song on YouTube by name or paste a YouTube link.
- **Separates it into tracks.** A fast model gives four (vocals, drums, bass and everything else) in about
  a minute per minute of music; «improve quality» re-separates a song into six — vocals, drums, bass, guitar,
  piano and everything else — with a much better, slower model.
- **Plays all tracks in sync**, with a volume fader, solo and mute for each, and a waveform you click to seek.
- **For practising**:
  - a metronome on the song's own beats;
  - a four-beat count-in before playing;
  - speed from 25 % to 150 % without changing the pitch;
  - the silence at the start is skipped.
- **Keeps your projects**: songs and tracks stay on your disk, and the settings show how much space they take,
  with buttons to free it.

Everything runs on your computer; your music never leaves it. The interface is in Russian.

## Requirements

- **Windows** 10 or 11 (64-bit), or **macOS** on Apple Silicon (M1 or newer) with Chrome or Safari 16.4+.
  Intel Macs are not supported yet. Phones and tablets are not supported.
- About 3 GB of free disk space for the program and its models, plus about 20 MB per minute of music.
- Internet for the first start and for downloading songs.

## Download and start

1. On this page click **Code → Download ZIP** and unpack the archive anywhere, for example into your user folder.
2. Start it:
   - **Windows**: double-click `Start.bat`.
   - **macOS**: double-click `Start.command`. The first time macOS says *“Apple could not verify "Start.command"
     is free of malware”*. This does not mean malware was found: the app is not signed with a paid Apple developer
     certificate, and macOS shows this for every unsigned file downloaded from the internet. To allow it once:
     click **Done**, open **System Settings → Privacy & Security**, scroll down to *“Start.command was blocked”*,
     click **Open Anyway** and confirm with your password. (Before macOS 15: right-click `Start.command` →
     **Open** → **Open**.) Afterwards a double-click is enough.
3. The app opens in your browser at `http://127.0.0.1:8000`. A console window shows what it is doing;
   close that window to stop the app.

**The first start takes 5–15 minutes**: it installs Python if needed (without an administrator password) and
downloads the components (about 1.5 GB). Each separation model downloads its weights the first time it is used
(the fast one about 160 MB, the best one about 650 MB, the beat detector about 80 MB). Later starts take a few
seconds.

## Updating

**Settings → Обновления → Проверить** shows what is new in later versions. **Обновить до …** downloads the newest
one (when no song is being separated); close the Audio Stems window and start the app again, and the update is
installed before it opens. Your songs, stems and settings stay as they are.

## Where your files are

Songs, tracks, the project database and logs are kept in the `Audio Stems` folder in your user folder, separate
from the program. The tracks are ordinary FLAC files: `Audio Stems/Music/Stems/<song>/vocals.flac` and
so on, so you can use them in any other program.

## What it connects to

Everything runs on your computer; the app sends nothing about you or your music anywhere and has no analytics.
It connects only to:

- **YouTube** (youtube.com and its media servers): when you search for a song or paste a link;
- **PyPI** (pypi.org) and **python.org** or **GitHub** (uv, on macOS): on the first start, to install Python and
  the libraries;
- **Hugging Face** (huggingface.co), **GitHub** (the releases that audio-separator uses) and
  **cloud.cp.jku.at**: once, to download a separation model or the beat model the first time you use it;
- **GitHub** (api.github.com, codeload.github.com): to check for a new version of Audio Stems, and to download it
  when you choose to install it.

## Please note

Use it only for music you have the right to download and process. The app does not bypass any access restrictions.

Audio Stems is a free hobby project, provided as is, without any warranty (see [LICENSE](LICENSE)). You are
responsible for what you download, separate and share.

How the two models were chosen (listening tests, methodology, a summary report): [separate-music-instruments-research](https://github.com/sergeypolevich/separate-music-instruments-research).

The code is free under the [MIT License](LICENSE). The separation models, Python packages and ffmpeg it
downloads have their own licenses, listed in
[THIRD_PARTY.md](THIRD_PARTY.md).

---

For developers: setup by hand, tests, the API and worker, backups and maintenance are described in
[app/README.md](app/README.md).
