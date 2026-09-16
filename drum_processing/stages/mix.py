"""Stage 5 — mix: pan/gain the stems, sum, resample to 48 kHz, two-pass EBU normalize.

Two things the manual recipe conflated, separated here:
  * relative balance (pan + a small gain offset) — a creative choice, in config;
  * absolute level — set once by normalizing the SUM (never the individual stems)
    to −14 LUFS / −1 dBTP via ffmpeg-normalize's two-pass linear loudnorm.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from rich.console import Console

from ..config import Config
from ..manifest import Manifest
from ..media import FFmpegError
from ..media import run as ff_run

RAW_REL = "work/mix_raw.wav"
MASTER_REL = "work/master_mix.wav"


def _pan_filter(pan: float, gain_db: float, label_in: str, label_out: str) -> str:
    """A stereo balance + gain applied to one (stereo) stem.

    Linear-balance pan law: at pan −1 the right channel is fully attenuated, etc.
    (Pan-law calibration against Resolve is a one-time task — see the plan.)
    """
    g = 10 ** (gain_db / 20.0)
    left = g * (1.0 - max(0.0, pan))
    right = g * (1.0 + min(0.0, pan))
    return f"[{label_in}]pan=stereo|c0={left:.5f}*c0|c1={right:.5f}*c1[{label_out}]"


def _build_raw_mix(session_dir: Path, manifest: Manifest, config: Config) -> Path:
    """Sum the panned stems into a float WAV at the mix sample rate (headroom kept)."""
    raw = session_dir / RAW_REL
    args: list[str] = []
    filters: list[str] = []
    mix_inputs: list[str] = []

    for i, stem in enumerate(manifest.stems):
        recipe = next(
            (s for s in config.mix.stems if s.match.lower() in stem.name.lower()), None
        )
        pan = recipe.pan if recipe else 0.0
        gain = recipe.gain_db if recipe else 0.0
        args += ["-i", str(session_dir / stem.src_path)]
        filters.append(_pan_filter(pan, gain, f"{i}:a", f"a{i}"))
        mix_inputs.append(f"[a{i}]")

    n = len(mix_inputs)
    filters.append(f"{''.join(mix_inputs)}amix=inputs={n}:normalize=0[mix]")
    args += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]",
        "-ar", str(config.mix.sample_rate),
        "-c:a", "pcm_f32le",  # float: never clip before the loudness pass
        str(raw),
    ]
    ff_run(args)
    return raw


def _two_pass_normalize(raw: Path, master: Path, config: Config) -> None:
    """Two-pass EBU R128 via ffmpeg-normalize (single linear gain — no pumping)."""
    exe = shutil.which("ffmpeg-normalize")
    if not exe:
        raise FFmpegError("ffmpeg-normalize not found on PATH (pip install ffmpeg-normalize).")
    cmd = [
        exe, str(raw), "-o", str(master), "-f",
        "-nt", "ebu",
        "-t", str(config.mix.target_lufs),
        "-tp", str(config.mix.target_true_peak),
        "--keep-loudness-range-target",  # don't compress dynamics as a side effect
        "-ar", str(config.mix.sample_rate),
        "-c:a", "pcm_s24le",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg-normalize failed:\n{proc.stderr[-2000:]}")


def _measure(master: Path) -> tuple[float, float]:
    """Integrated LUFS (pyloudnorm) + sample-peak dBFS for the QC report."""
    data, rate = sf.read(str(master))
    meter = pyln.Meter(rate)
    lufs = float(meter.integrated_loudness(data))
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    peak_dbfs = 20 * np.log10(peak) if peak > 0 else -np.inf
    return lufs, peak_dbfs


def run(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    if not manifest.stems:
        raise RuntimeError("No stems to mix.")

    master = session_dir / MASTER_REL
    with console.status("[bold]Summing panned stems…"):
        raw = _build_raw_mix(session_dir, manifest, config)
    with console.status("[bold]Two-pass EBU R128 normalize…"):
        _two_pass_normalize(raw, master, config)
    with console.status("[bold]Measuring loudness…"):
        lufs, peak = _measure(master)

    manifest.mix.master_path = MASTER_REL
    manifest.mix.measured_lufs = lufs
    manifest.mix.measured_true_peak = peak
    manifest.mark_done("mix")
    manifest.save(session_dir)
    console.print(
        f"  master_mix: [green]{lufs:.1f} LUFS[/]  peak {peak:.2f} dBFS  "
        f"(target {config.mix.target_lufs} / {config.mix.target_true_peak})"
    )
