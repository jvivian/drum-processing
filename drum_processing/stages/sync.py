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
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..gcc_phat import fit_drift, gcc_phat
from ..manifest import Manifest, SyncResult
from ..onset import onset_envelope
from ..tui import chrome, theme
from ..tui.timeline import clip_short
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


def _result_detail(res: SyncResult) -> Text:
    """``offset 20.383s   drift -192 ppm   resid 2 ms   conf 48.8``

    Labels dim, figures bright: the eye should land on the numbers, and the units
    should not compete with them. Widths are fixed so the columns line up across
    clips without needing a table.
    """
    text = Text()
    for label, value, unit in (
        ("offset", f"{res.offset_s:9.3f}", "s"),
        ("drift", f"{res.drift_ppm:+5.0f}", " ppm"),
        ("resid", f"{res.residual_ms:3.0f}", " ms"),
        ("conf", f"{res.confidence:5.1f}", ""),
    ):
        text.append(f"{label} ", style="label")
        text.append(value, style="metric")
        if unit:
            text.append(unit, style="unit")
        text.append("   ")
    return text


def _checklist(st, clips, results: dict) -> Table:
    """Every clip up front, each resolving in place as it is aligned.

    Only worth drawing in a pane: in scrollback a pending row would still be
    there, stale, after the clip finished — so the log keeps one line per
    completed clip instead.
    """
    # No header: a status glyph, a clip name and its numbers need no labelling,
    # and empty header cells read as a mistake.
    t = chrome.table(chrome.c_text(""), chrome.c_name(""), chrome.c_name(""),
                     show_header=False)
    for clip in clips:
        res = results.get(clip.name)
        if res is None:
            t.add_row(Text(theme.GLYPHS["pending"], style="pending"),
                      Text(clip_short(clip.name), style="faint"),
                      Text("waiting", style="faint"))
        else:
            t.add_row(theme.status_text(st.console, res.status),
                      Text(clip_short(clip.name), style="value"),
                      _result_detail(res))
    return t


def run(session_dir: Path, manifest: Manifest, config: Config, st) -> None:
    session_dir = Path(session_dir)
    hop, nfft = config.sync.env_hop, config.sync.env_nfft

    with st.step("building correlation reference…"):
        ref_path = ensure_reference(session_dir, manifest)
        ref_env, efs = onset_envelope(_load_mono(ref_path), CORRELATION_FS, hop, nfft)

    results: list[SyncResult] = []
    done: dict[str, SyncResult] = {}
    if st.pane:
        st.set_body(_checklist(st, manifest.clips, done))
    for clip in manifest.clips:
        with st.step(f"syncing {clip.name}"):
            scratch = ensure_scratch(session_dir, clip)
            clip_env, _ = onset_envelope(_load_mono(scratch), CORRELATION_FS, hop, nfft)
            res = sync_clip(clip_env, ref_env, efs, clip.duration_s, clip.name, config)
        results.append(res)
        done[clip.name] = res
        if st.pane:
            st.set_body(_checklist(st, manifest.clips, done))
        else:
            st.item(res.status, f"{clip_short(clip.name)}  ", _result_detail(res))

    manifest.sync = results
    manifest.mark_done("sync")
    manifest.save(session_dir)
