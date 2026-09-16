# drumprep

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)

One CLI call to convert DJI clips + REAPER stems into a folder of
upload-ready, synced, mixed, normalized, black-and-white take clips



## Pipeline

```
ingest   discover + ffprobe every clip and stem → drumprep.json
sync     onset-envelope GCC-PHAT: per-clip offset + clock-drift + confidence   [review]
mix      pan/gain the stems → sum → two-pass EBU R128 to −14 LUFS / −1 dBTP
takes    auditok activity detection on the mix → take boundaries               [review]
render   one ffmpeg pass per take: trim, drift-correct, grayscale, mux studio audio
```

## Install

```bash
conda create -n drumprep -c conda-forge python=3.12 ffmpeg numpy scipy
conda activate drumprep
pip install -e .
```

`ffmpeg` must be the conda-forge build (NVENC-enabled). NVENC is detected at runtime and
falls back to `libx264 -crf 18` automatically (e.g. under WSL2 without GPU passthrough).

## Usage

```bash
drumprep run   /path/to/session       # walk the whole pipeline
drumprep run   /path/to/session --yes # unattended (no review screens)

# or run one stage at a time (each reads/writes the same manifest):
drumprep ingest /path/to/session
drumprep sync   /path/to/session
drumprep mix    /path/to/session
drumprep takes  /path/to/session
drumprep render /path/to/session
drumprep status /path/to/session      # show the manifest
drumprep clean  /path/to/session      # delete disposable work/ intermediates
```

A session directory just needs the DJI originals (`*.MP4`, HEVC preferred over any
`-converted` copies) and the REAPER stem WAVs (`*-STL.wav`, `*-STR.wav`, `*Addictive
Drums*.wav`). Sources are never modified; output lands in `out/takes/`.

## Configuration

Drop an optional `drumprep.toml` in the session dir to override defaults (mix balance,
loudness targets, take-detection thresholds, sync gates, output encoding):

```toml
[mix]
target_lufs = -14.0
target_true_peak = -1.0

[[mix.stem]]
match = "STL"
pan = -0.5

[[mix.stem]]
match = "Addictive Drums"
gain_db = -2.5      # relative balance only — absolute level comes from normalization

[takes]
max_silence_s = 4.0
min_take_s = 20.0
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check drum_processing/ tests/
```

## License

MIT — see [LICENSE](LICENSE). © John Vivian.
