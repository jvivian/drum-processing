"""Stage 3 — sync: align each clip to the studio audio via onset-envelope correlation.

Per clip:
  1. Compute the spectral-flux onset envelope of the camera scratch track.
  2. Correlate short windows spread across the clip against the reference envelope.
  3. Take the *median* window offset (robust to windows that fall outside the audio
     or land in silence), keep the inliers, and linear-fit them for clock drift.

Camera↔studio share onset timing but not waveform shape, so we align on envelopes,
not raw audio (see onset.py). The studio audio is never resampled; drift is corrected
at render time by retiming the video.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from rich.console import Console

from ..config import Config
from ..gcc_phat import fit_drift, gcc_phat
from ..manifest import Manifest, SyncResult
from ..onset import onset_envelope
from .reference import CORRELATION_FS, ensure_reference
from .scratch import ensure_scratch


def _load_mono(path: Path) -> np.ndarray:
    data, _ = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def _best_cluster(times: np.ndarray, offs: np.ndarray, confs: np.ndarray,
                  tol: float) -> np.ndarray:
    """Boolean mask of the highest-confidence-mass cluster of window offsets.

    Robust where the median is not: a spurious offset can attract as many windows
    as the true one (e.g. audio paused mid-clip), so we pick the cluster carrying
    the most *confidence*, not the most members or the middle value.
    """
    best_mask = np.zeros(offs.size, dtype=bool)
    best_score = -1.0
    for center in offs:
        mask = np.abs(offs - center) <= tol
        score = float(confs[mask].sum())
        if score > best_score:
            best_score, best_mask = score, mask
    return best_mask


def sync_clip(clip_env: np.ndarray, ref_env: np.ndarray, efs: float,
              clip_dur: float, clip_name: str, cfg: Config) -> SyncResult:
    """Robustly locate ``clip_env`` within ``ref_env`` and estimate drift."""
    sc = cfg.sync
    ref_dur = ref_env.size / efs
    win = int(sc.window_s * efs)
    times: list[float] = []
    offsets: list[float] = []
    confs: list[float] = []

    t = 0.0
    while t + sc.window_s <= clip_dur:
        seg = clip_env[int(t * efs): int(t * efs) + win]
        if seg.size >= win:
            est = gcc_phat(seg, ref_env, efs)
            off = est.lag_s - t  # offset(t) = ref position - clip time
            # Keep only windows that are confident AND physically possible: a valid
            # clip offset must place the clip's overlap inside the audio.
            if est.confidence >= sc.min_confidence and -clip_dur <= off <= ref_dur:
                times.append(t)
                offsets.append(off)
                confs.append(est.confidence)
        t += sc.window_hop_s

    if len(offsets) < sc.min_inliers:
        return SyncResult(
            clip=clip_name,
            confidence=float(np.max(confs)) if confs else 0.0,
            status="no_match",
        )

    time_arr, off_arr, conf_arr = np.array(times), np.array(offsets), np.array(confs)
    inlier = _best_cluster(time_arr, off_arr, conf_arr, sc.inlier_tol_s)

    if inlier.sum() < sc.min_inliers:
        return SyncResult(clip=clip_name, offset_s=float(np.median(off_arr[inlier])),
                          confidence=float(conf_arr[inlier].max()), status="low_confidence")

    fit = fit_drift(time_arr[inlier], off_arr[inlier])
    return SyncResult(
        clip=clip_name, offset_s=fit.offset_s, drift_ppm=fit.drift_ppm,
        confidence=float(np.median(conf_arr[inlier])), residual_ms=fit.residual_ms,
        status="ok",
    )


def run(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    hop, nfft = config.sync.env_hop, config.sync.env_nfft

    with console.status("[bold]Building correlation reference…"):
        ref_path = ensure_reference(session_dir, manifest)
        ref_env, efs = onset_envelope(_load_mono(ref_path), CORRELATION_FS, hop, nfft)

    results: list[SyncResult] = []
    for clip in manifest.clips:
        with console.status(f"[bold]Syncing[/] {clip.name}"):
            scratch = ensure_scratch(session_dir, clip)
            clip_env, _ = onset_envelope(_load_mono(scratch), CORRELATION_FS, hop, nfft)
            res = sync_clip(clip_env, ref_env, efs, clip.duration_s, clip.name, config)
        results.append(res)
        icon = {"ok": "[green]✓[/]", "low_confidence": "[yellow]? review[/]",
                "no_match": "[red]✗ no match[/]"}[res.status]
        console.print(
            f"  {clip.name}: offset={res.offset_s:8.3f}s  "
            f"drift={res.drift_ppm:+6.0f}ppm  resid={res.residual_ms:4.0f}ms  "
            f"conf={res.confidence:6.1f}  {icon}"
        )

    manifest.sync = results
    manifest.mark_done("sync")
    manifest.save(session_dir)
