# drumprep

[![CI](https://github.com/jvivian/drum-processing/actions/workflows/ci.yml/badge.svg)](https://github.com/jvivian/drum-processing/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)

One CLI call to convert DJI clips + REAPER stems into a folder of
upload-ready, synced, mixed, normalized, black-and-white take clips



## Pipeline

```
ingest   discover + ffprobe every clip and stem → drumprep.json
sync     onset-envelope GCC-PHAT: per-clip offset + clock-drift + confidence   [review]
mix      pan/gain the stems → sum → two-pass EBU R128 to −14 LUFS / −1 dBTP
takes    auditok activity detection on the MIDI stem → take boundaries         [review]
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

`run` also takes `--from/--to` to walk a sub-range (`--from takes` re-detects and
re-renders while reusing the existing sync and mix) and `--rm` to drop `work/` afterwards.

A session directory just needs the DJI originals (`*.MP4`, HEVC preferred over any
`-converted` copies) and the REAPER stem WAVs (`*-STL.wav`, `*-STR.wav`, `*Addictive
Drums*.wav`). Sources are never modified; output lands in `out/`.

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
max_silence_s = 20.0
min_take_s = 20.0
```

## Methods

### Aligning camera clips to the studio audio

Every clip has to be placed on the REAPER timeline before anything can be cut. The
camera's built-in mic and the studio stems capture the same drums, so cross-correlation
is the obvious tool — but the obvious version of it does not work here.

**Why raw GCC-PHAT fails.** GCC-PHAT cross-correlates two signals with phase-transform
weighting (`R /= |R|`), which whitens the cross-spectrum and makes the result largely
insensitive to frequency-response differences. That is normally exactly right for a tinny
action-cam mic versus close-mic'd studio tracks. In practice it sat at the noise floor:
camera audio against the summed stems scored a confidence (correlation peak ÷ median) of
about **13**, while a positive control — a 60 s slice of the reference correlated against
the reference itself — scored **1528**. The two recordings share no fine waveform
structure: the stems are close-mic'd and partly sample-triggered, the camera hears a room.

**What works: onset envelopes.** What the recordings *do* share is *when* the hits happen.
So drumprep correlates **onset-strength envelopes** rather than waveforms:

1. Decode both sides to mono 16 kHz.
2. Compute a half-wave-rectified **spectral flux** envelope — per STFT frame, the sum of
   positive changes in magnitude across bins — at an 80-sample hop, i.e. a **200 Hz
   envelope (5 ms bins)**. For a 60-minute reference this runs in chunks so a full STFT
   magnitude matrix is never materialized.
3. Run GCC-PHAT on the envelopes, with parabolic interpolation around the peak for
   sub-bin precision.

On real sessions this lifts confidence from ~13 to **30–56** on true matches, while
unrelated audio stays below the gate.

**Robust offset + drift fit.** A single global correlation isn't enough, because a clip
can contain stretches that aren't in the audio at all (the camera rolls while REAPER is
stopped). So for each clip drumprep correlates **60 s windows every 30 s** against the
full reference envelope and keeps only windows that are both

- confident (peak/median ≥ 27), and
- **physically possible** — the implied offset must satisfy `-clip_duration ≤ offset ≤ audio_duration`

It then picks the **cluster carrying the most total confidence**, not the median. This
matters: on one real clip the true offset (+1625 s) and a spurious one (−3618 s) each
attracted about half the windows, so a median landed on the wrong one while
confidence-mass clustering landed on the right one. The winning cluster's inliers are
least-squares fit to

```
offset(t) = a + b·t
```

where `a` is the clip's start offset and `b` the clock-rate error. Fits beyond ±200 ppm
are rejected as failures and fall back to offset-only, and clips with fewer than three
agreeing windows are reported as **no match** rather than force-fit — a clip recorded on a
different day is correctly refused.

Measured on real sessions: fit residuals of **0–2 ms**, and a consistent **≈ −180 ppm**
drift across every clip from the same camera (one crystal, one error).

### Clock drift, and why the video is retimed instead of the audio

−180 ppm is about 0.65 s per hour — some 160 ms across a 15-minute take, already past the
ITU-R BT.1359 lip-sync threshold and very visible on drums, where the stick hit and the
transient have to coincide. Since `offset(t) = a + b·t`, the studio time matching camera
time `t` is

```
audio_time(t) = a + (1 + b)·t
```

The correction is applied to the **video**, never the audio: the render scales
presentation timestamps by `(1 + b)` (`setpts=(PTS-STARTPTS)*(1+b)`) while the studio mix
plays untouched from `a + (1+b)·clip_in`. At these ratios that duplicates or drops a
handful of frames across an hour — imperceptible — and the good signal is never resampled.

### Why clips are matched by correlation, not timestamps

DJI filenames encode a wall-clock start time, which looks like a free shortcut. It isn't:
the REAPER render is a **compacted** timeline. On one session the audio advanced only
~20 s across a 16-minute gap in the camera's wall clock, because recording was stopped and
restarted between takes. Filename timestamps therefore can't locate a clip in the render,
and every clip is placed by correlation alone.

The stems need no alignment of their own — rendered over identical project bounds they are
sample-locked, so they're simply summed at t = 0.

### Finding takes on the MIDI stem

Take boundaries come from activity detection (`auditok`), but *which signal* it runs on
decides everything. The room mics hear the backing music all session, so on the summed mix
a long idle stretch still looks "active" and takes come out far too long. The **Addictive
Drums stem only carries signal when the kit is actually struck**, making it a clean "am I
playing?" gate; drumprep detects on it and falls back to the mix only when no such stem
exists. On one session this took the same audio from 7 takes to 17.

The split threshold is the interesting parameter. Backing tracks contain drumless sections
up to ~20 s and real breaks run longer, so `max_silence_s` defaults to **20 s** — long
enough that a drumless bridge keeps a take whole, short enough that putting the sticks down
starts a new one. `auditok` is used rather than ffmpeg's `silencedetect` precisely because
`max_silence` gives that hysteresis; `silencedetect` is sample-instantaneous and fragments
a pause the moment one sample crosses the threshold. A pre-roll and a 3 s post-roll are
added back so the room mics' cymbal decay isn't clipped after the last MIDI note.

Detected regions live on the audio timeline and are mapped back through each clip's offset
and drift to whichever clip was filming at the time.

### Loudness: normalize the sum, never the stems

Per-stem gain in the config is **relative balance only**. Absolute level comes from a
single EBU R128 pass over the *summed* mix, targeting −14 LUFS / −1 dBTP.

Normalizing stems individually is the tempting mistake, and it doesn't merely get the
number wrong — it destroys the mix: forcing a quiet room mic up to the same loudness as a
close kit mic is a remix, not a normalization, and the sum lands several dB hot anyway. The
pass is also deliberately **two-pass** — one-pass `loudnorm` is a *dynamic* normalizer whose
time-varying gain audibly pumps on drum transients, while two-pass measures first and then
applies a single linear gain. `pyloudnorm` only measures and reports the result; ffmpeg
does the render, so there's a true-peak limiter in the path.

Stems are resampled to 48 kHz to match the video, removing a resample at every join.

### Render

One ffmpeg invocation per take, decoding and encoding exactly once:

- **The source is the HEVC original**, not a `-converted` copy — one lossy generation
  instead of two.
- **Streams are mapped explicitly** (`0:v:0` plus the master mix). DJI files carry extra
  data streams and a rotated MJPEG thumbnail that a bare `0:v` would happily pick up, and
  the camera's own audio is simply never mapped — so "mute the camera track" stops being a
  step.
- Grayscale is `hue=s=0` (zeroing chroma in YUV, essentially free); a `rec709_luma`
  weighting is also available, since the two give visibly different greys on a coloured kit.
- Output is forced to **CFR** — DJI clips are sometimes VFR, which combined with `setpts`
  retiming would reintroduce drift.
- **NVENC is probed at runtime**, not merely looked up: under WSL2 ffmpeg lists
  `h264_nvenc` but fails to initialize it, so drumprep runs a tiny null-encode and falls
  back to `libx264 -crf 18` when that fails.
- Per-take stem WAVs are written beside each MP4, so any take can be reopened in an editor
  with everything already aligned at 00:00.

### Manifest-driven stages

Every stage is a pure function over one `drumprep.json` in the session directory: read the
manifest, do the work, write the results back. Nothing recomputes what an earlier stage
recorded, so any stage re-runs independently, the pipeline is resumable, and sources are
only ever read.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check drum_processing/ tests/
```

## License

MIT — see [LICENSE](LICENSE). © John Vivian.
