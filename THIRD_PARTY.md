# Third-party software and models

Audio Stems itself is MIT-licensed ([LICENSE](LICENSE)). This repository contains only its own code and one
vendored file. Everything else below is downloaded on the user's computer, from its official source, the first
time it is needed; none of it is redistributed here.

## In this repository

| Component | Author | License | File |
|---|---|---|---|
| Signalsmith Stretch Web 1.3.2 (playback speed) | Geraint Luff / Signalsmith Audio Ltd. | MIT | `app/web/vendor/signalsmith-stretch-1.3.2.mjs`, license text next to it |

## Models (downloaded on first use)

| Model | Used for | Author | Source | License |
|---|---|---|---|---|
| Demucs `hdemucs_mmi` | fast separation, 4 stems | Alexandre Défossez (Meta AI) | [huggingface.co/adefossez/Demucs-hdemucs_mmi](https://huggingface.co/adefossez/Demucs-hdemucs_mmi) | Demucs code: MIT; the model card names no license for the weights |
| Demucs `htdemucs_6s` | re-separating projects made before version 13 | Alexandre Défossez (Meta AI) | [huggingface.co/adefossez/HTDemucs-6s](https://huggingface.co/adefossez/HTDemucs-6s) | as above |
| BS-RoFormer SW | «Улучшить качество», 6 stems | jarredou | GitHub releases used by [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) | no license published |
| beat_this `final0` | beats for the metronome | CPJKU, Johannes Kepler University Linz | [github.com/CPJKU/beat_this](https://github.com/CPJKU/beat_this), weights from cloud.cp.jku.at | MIT (code and published weights) |

Some of these models were trained on MUSDB18 and other datasets whose terms allow research and non-commercial
use. Every weights file is checked against a pinned SHA-256 before it runs (`app/audio_stems/separator.py`,
`app/audio_stems/analysis.py`).

## Python packages (installed from PyPI by the launcher)

The exact versions and hashes are in `app/requirements-windows.lock` and `app/requirements-macos.lock`. The direct
dependencies are pytubefix, demucs, audio-separator, beat-this (MIT), fastapi, uvicorn, numpy, soundfile, librosa,
onnxruntime (BSD, MIT, ISC) and imageio-ffmpeg (BSD-2-Clause). Almost all other packages are under MIT, BSD,
Apache-2.0, ISC or PSF licenses. The exceptions:

| Package | License | Note |
|---|---|---|
| diffq-fixed | CC BY-NC 4.0 | non-commercial; a dependency of demucs and audio-separator |
| lameenc | LGPL-3.0-or-later | |
| soxr | LGPL-2.1-or-later | |
| certifi, tqdm | MPL-2.0 | |
| ffmpeg 7.1 inside imageio-ffmpeg | GPL-3.0 (built with `--enable-gpl --enable-version3`) | run as a separate program |
| sphn | Apache-2.0 | license file only, no metadata |

## Installed by the launchers

- Python 3.12 from [python.org](https://www.python.org/) (Windows), PSF License.
- [uv](https://github.com/astral-sh/uv) from its GitHub releases (macOS), MIT or Apache-2.0; it installs its own
  Python build.
