"""Stage 6 — takes: detect take boundaries on the studio mix with auditok.

auditok's ``max_silence`` gives hysteresis (a bar rest is not a take boundary, a
water break is), which silencedetect can't. Detection runs on the clean studio mix;
regions (in audio time) are mapped back to the clip they were filmed on.
"""

from __future__ import annotations

from pathlib import Path

import auditok
import soundfile as sf
from rich.console import Console

from ..config import Config
from ..manifest import Manifest, Take
from ..media import run as ff_run
from .mix import MASTER_REL
from .render import _overlap

DETECT_REL = "work/mix_detect.wav"


def _detection_track(session_dir: Path) -> Path:
    """auditok needs 8/16-bit PCM; make a mono 16 kHz 16-bit copy of the master."""
    detect = session_dir / DETECT_REL
    if not detect.is_file():
        ff_run(["-i", str(session_dir / MASTER_REL), "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(detect)])
    return detect


def run(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    master = session_dir / MASTER_REL
    if not master.is_file():
        raise RuntimeError("No master mix — run the mix stage first.")
    ref_dur = sf.info(str(master)).frames / sf.info(str(master)).samplerate
    tc = config.takes

    with console.status("[bold]Detecting takes…"):
        detect = _detection_track(session_dir)
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
