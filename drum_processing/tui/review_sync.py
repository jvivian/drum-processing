"""Stage 4 — interactive sync review with an A/B preview clip.

The preview writes a 10 s MP4 at the loudest moment of the overlap with the audio
hard-panned: camera left, studio right. In sync it sounds like one drummer; out of
sync it flams. Fastest possible verification.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import questionary
import soundfile as sf
from rich.console import Console
from rich.table import Table

from ..config import Config
from ..manifest import Manifest, SyncResult
from ..media import run
from ..stages.reference import CORRELATION_FS, REF_REL
from ..stages.scratch import scratch_path

PREVIEW_S = 10.0


def _loudest_overlap_start(scratch: np.ndarray, fs: float, res: SyncResult,
                           clip_dur: float, ref_dur: float) -> float:
    """Clip time of the loudest PREVIEW_S window that also lands inside the audio."""
    win = int(PREVIEW_S * fs)
    if scratch.size <= win:
        return 0.0
    # Overlap in clip time: 0 <= offset + t <= ref_dur, and window fits in the clip.
    t_lo = max(0.0, -res.offset_s)
    t_hi = min(clip_dur - PREVIEW_S, ref_dur - PREVIEW_S - res.offset_s)
    if t_hi <= t_lo:
        return max(0.0, min(clip_dur - PREVIEW_S, t_lo))
    step = max(1, win // 2)
    best_t, best_rms = t_lo, -1.0
    t = t_lo
    while t <= t_hi:
        i = int(t * fs)
        seg = scratch[i:i + win]
        rms = float(np.sqrt(np.mean(seg**2))) if seg.size else 0.0
        if rms > best_rms:
            best_rms, best_t = rms, t
        t += step / fs
    return best_t


def build_preview(session_dir: Path, manifest: Manifest, res: SyncResult) -> Path:
    session_dir = Path(session_dir)
    clip = manifest.clip(res.clip)
    assert clip is not None
    ref_path = session_dir / REF_REL
    scratch = sf.read(str(scratch_path(session_dir, clip)), dtype="float32")[0]
    ref_info = sf.info(str(ref_path))
    cam_t = _loudest_overlap_start(scratch, CORRELATION_FS, res, clip.duration_s,
                                   ref_info.frames / ref_info.samplerate)
    std_t = max(0.0, res.offset_s + cam_t)

    out = session_dir / "work" / f"preview_{Path(clip.name).stem}.mp4"
    args = [
        "-ss", f"{cam_t}", "-i", str(session_dir / clip.src_path),
        "-ss", f"{std_t}", "-i", str(ref_path),
        "-t", f"{PREVIEW_S}",
        "-filter_complex",
        "[0:a]aresample=48000,pan=mono|c0=c0[l];"
        "[1:a]aresample=48000,pan=mono|c0=c0[r];"
        "[l][r]join=inputs=2:channel_layout=stereo[a];"
        "[0:v:0]scale=640:-2,hue=s=0[v]",
        "-map", "[v]", "-map", "[a]", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", str(out),
    ]
    run(args)
    return out


def _table(console: Console, manifest: Manifest) -> None:
    t = Table(title="Sync review", header_style="bold cyan")
    for col in ("#", "clip", "offset", "drift", "confidence", "status"):
        t.add_column(col)
    for i, r in enumerate(manifest.sync):
        mark = {"ok": "[green]✓[/]", "low_confidence": "[yellow]? review[/]",
                "no_match": "[red]✗ no match[/]", "skip": "[dim]skip[/]",
                "pending": "…"}[r.status]
        t.add_row(str(i), r.clip,
                  f"{r.offset_s:.3f}s" if r.status != "no_match" else "—",
                  f"{r.drift_ppm:+.0f}ppm" if r.status == "ok" else "—",
                  f"{r.confidence:.0f}", mark)
    console.print(t)


def review(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    while True:
        _table(console, manifest)
        action = questionary.select(
            "Sync review",
            choices=["accept all", "preview a clip", "nudge a clip",
                     "skip a clip", "quit"],
        ).ask()
        if action in (None, "accept all"):
            break
        if action == "quit":
            raise SystemExit(0)

        idx = questionary.select(
            "Which clip?",
            choices=[questionary.Choice(f"{i}: {r.clip}", value=i)
                     for i, r in enumerate(manifest.sync)],
        ).ask()
        if idx is None:
            continue
        res = manifest.sync[idx]

        if action == "preview a clip":
            with console.status("Building A/B preview…"):
                path = build_preview(session_dir, manifest, res)
            console.print(f"[green]Preview:[/] {path}")
            console.print("  [dim]camera=L, studio=R — a flam means out of sync[/]")
        elif action == "nudge a clip":
            ms = questionary.text("Offset nudge in ms (+ = clip later in audio):",
                                  default="0").ask()
            try:
                res.offset_s += float(ms) / 1000.0
                res.status = "ok"
                manifest.save(session_dir)
                console.print(f"[green]Nudged[/] {res.clip} → offset {res.offset_s:.3f}s")
            except (TypeError, ValueError):
                console.print("[red]Not a number.[/]")
        elif action == "skip a clip":
            res.status = "skip"
            manifest.save(session_dir)

    manifest.save(session_dir)
