"""Stage 6 — takes: detect take boundaries with auditok.

Detection runs on the **Addictive Drums (MIDI) stem** when present: it only carries
signal when John is actually playing, so idle stretches (where the room mics still
hear backing music) don't read as active. Falls back to the summed mix for pure
acoustic sessions. auditok's ``max_silence`` gives hysteresis — a musical rest is not
a take boundary, a real stop is. Regions (studio time) map back to the filmed clip.
"""

from __future__ import annotations

from pathlib import Path

import auditok
import soundfile as sf
from rich.console import Console

from ..config import Config
from ..manifest import Manifest, Stem, Take
from ..media import run as ff_run
from .mix import MASTER_REL
from .render import _overlap

DETECT_REL = "work/takes_detect.wav"


def _detect_stem(manifest: Manifest, match: str | None) -> Stem | None:
    """The stem whose name/role matches (case-insensitive), e.g. Addictive Drums."""
    if not match:
        return None
    m = match.lower()
    return next(
        (s for s in manifest.stems
         if m in s.name.lower() or (s.role and m in s.role.lower())),
        None,
    )


def _detection_track(session_dir: Path, manifest: Manifest, config: Config,
                     console: Console) -> Path:
    """auditok needs 8/16-bit PCM. Decode the trigger source — the MIDI stem if we
    have one, else the summed mix — to a mono 16 kHz 16-bit WAV."""
    detect = session_dir / DETECT_REL
    stem = _detect_stem(manifest, config.takes.detect_stem_match)
    if stem is not None:
        source = session_dir / stem.src_path
        console.print(f"  [dim]detecting on MIDI stem:[/] {stem.name}")
    else:
        source = session_dir / MASTER_REL
        console.print("  [dim]no MIDI stem found — detecting on the summed mix[/]")
    # Always regenerate (fast, one-stem decode) so it can't go stale when the source
    # stem or config changes.
    ff_run(["-i", str(source), "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(detect)])
    return detect


def run(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    master = session_dir / MASTER_REL
    if not master.is_file():
        raise RuntimeError("No master mix — run the mix stage first.")
    ref_dur = sf.info(str(master)).frames / sf.info(str(master)).samplerate
    tc = config.takes

    detect = _detection_track(session_dir, manifest, config, console)
    with console.status("[bold]Detecting takes…"):
        regions = list(auditok.split(
            str(detect), min_dur=tc.min_take_s, max_dur=1e9,
            max_silence=tc.max_silence_s, energy_threshold=tc.energy_threshold,
        ))

    # Precompute each synced clip's audio-time coverage window.
    coverage = []
    for clip in manifest.clips:
        s = manifest.sync_for(clip.name)
        if not s or s.status not in ("ok", "low_confidence"):
            continue
        b = s.drift_ppm / 1e6
        ci, co = _overlap(s, clip, ref_dur)
        a_lo = s.offset_s + (1 + b) * ci
        a_hi = s.offset_s + (1 + b) * co
        coverage.append((clip, s, ci, co, a_lo, a_hi, b))

    takes: list[Take] = []
    idx = 1
    for region in regions:
        a_start, a_end = float(region.start), float(region.end)
        mid = 0.5 * (a_start + a_end)
        hit = next((cov for cov in coverage if cov[4] <= mid <= cov[5]), None)
        if hit is None:
            continue  # audio with no camera coverage — nothing to render
        clip, s, ci, co, _, _, b = hit
        start_clip = max(ci, (a_start - s.offset_s) / (1 + b) - tc.pre_roll_s)
        end_clip = min(co, (a_end - s.offset_s) / (1 + b) + tc.post_roll_s)
        if end_clip - start_clip < tc.min_take_s:
            continue
        takes.append(Take(index=idx, clip=clip.name, start_s=start_clip, end_s=end_clip))
        idx += 1

    manifest.takes = takes
    manifest.mark_done("takes")
    manifest.save(session_dir)
    console.print(f"  detected [bold]{len(takes)}[/] takes across {len(coverage)} synced clips")
