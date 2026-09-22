"""Stage 9 — render: one ffmpeg pass per segment.

Trim the DJI original, drift-correct the video (never the audio), convert to
grayscale, and mux the studio master mix — camera audio is simply never mapped.
Renders whole overlapping clips (``full_clips=True``) or per-take segments (M4).
"""

from __future__ import annotations

from pathlib import Path

import soundfile as sf
from rich.console import Console

from ..config import Config
from ..manifest import Clip, Manifest, SyncResult
from ..media import has_nvenc
from ..media import run as ff_run
from .mix import MASTER_REL
from .reference import CORRELATION_FS  # noqa: F401  (kept for parity/imports)


def _grayscale(bw_mode: str) -> str:
    if bw_mode == "rec709_luma":
        w = "0.2126:0.7152:0.0722"
        return f"colorchannelmixer={w}:0:{w}:0:{w}:0"
    return "hue=s=0"  # chroma_zero (default) and fallback


def _encoder_args(config: Config) -> list[str]:
    if has_nvenc():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq",
                str(config.output.nvenc_cq), "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf",
            str(config.output.libx264_crf), "-pix_fmt", "yuv420p"]


def render_segment(session_dir: Path, clip: Clip, sync: SyncResult, config: Config,
                   clip_in: float, clip_out: float, out_path: Path,
                   on_progress=None) -> None:
    """Render clip time [clip_in, clip_out] with the aligned master mix as audio."""
    session_dir = Path(session_dir)
    master = session_dir / MASTER_REL
    b = sync.drift_ppm / 1e6
    # audio time for clip-time t is: a + (1+b)*t  (see sync.py derivation)
    audio_in = sync.offset_s + (1.0 + b) * clip_in
    out_dur = (clip_out - clip_in) * (1.0 + b)

    fps = config.output.fps or clip.fps
    gray = _grayscale(config.output.bw_mode)
    vfilter = f"[0:v:0]setpts=(PTS-STARTPTS)*{1.0 + b:.9f},{gray},fps={fps}[v]"

    args = [
        "-ss", f"{clip_in}", "-to", f"{clip_out}", "-i", str(session_dir / clip.src_path),
        "-ss", f"{max(0.0, audio_in)}", "-i", str(master),
        "-filter_complex", vfilter,
        "-map", "[v]", "-map", "1:a",
        "-t", f"{out_dur}",
        *_encoder_args(config),
        "-c:a", "aac", "-b:a", config.output.audio_bitrate,
        "-movflags", "+faststart",
        str(out_path),
    ]
    ff_run(args, total_s=out_dur, on_progress=on_progress)


def _overlap(sync: SyncResult, clip: Clip, ref_dur: float) -> tuple[float, float]:
    """The portion of a clip (in clip time) that overlaps the studio audio."""
    b = sync.drift_ppm / 1e6
    clip_in = max(0.0, -sync.offset_s / (1.0 + b))
    clip_out = min(clip.duration_s, (ref_dur - sync.offset_s) / (1.0 + b))
    return clip_in, clip_out


def run(session_dir: Path, manifest: Manifest, config: Config, console: Console,
        full_clips: bool = False) -> None:
    session_dir = Path(session_dir)
    master = session_dir / MASTER_REL
    if not master.is_file():
        raise RuntimeError("No master mix — run the mix stage first.")
    ref_dur = sf.info(str(master)).frames / sf.info(str(master)).samplerate

    from ..tui.progress import stage_progress

    if full_clips or not manifest.takes:
        _render_full_clips(session_dir, manifest, config, console, ref_dur, stage_progress)
    else:
        _render_takes(session_dir, manifest, config, console, ref_dur)

    manifest.mark_done("render")
    manifest.save(session_dir)


def _render_full_clips(session_dir, manifest, config, console, ref_dur, stage_progress):
    out_dir = session_dir / "out"
    out_dir.mkdir(exist_ok=True)
    synced = [(c, manifest.sync_for(c.name)) for c in manifest.clips]
    synced = [(c, s) for c, s in synced if s and s.status in ("ok", "low_confidence")]
    if not synced:
        console.print("[yellow]No synced clips to render.[/]")
        return
    with stage_progress(console, "Rendering clips") as track:
        for clip, sync in synced:
            clip_in, clip_out = _overlap(sync, clip, ref_dur)
            if clip_out - clip_in < 1.0:
                continue
            out_path = out_dir / f"{Path(clip.name).stem}_bw.mp4"
            track(clip.name, (clip_out - clip_in),
                  lambda cb, c=clip, s=sync, ci=clip_in, co=clip_out, o=out_path:
                  render_segment(session_dir, c, s, config, ci, co, o, on_progress=cb))


def _render_takes(session_dir, manifest, config, console, ref_dur):
    """Render each take, filling its segment on the session timeline live as it finishes."""
    from rich.console import Group
    from rich.live import Live

    from ..tui import timeline as tl
    from ..tui.progress import make_progress

    out_dir = session_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    takes = [t for t in manifest.takes if not t.dropped]

    console.print(tl.breakdown(manifest, ref_dur))
    progress = make_progress(console)
    rendered: set[int] = set()

    def view(active: int | None) -> Group:
        panel = tl.master_panel(manifest, ref_dur, console.width, frozenset(rendered), active)
        return Group(panel, progress)

    with Live(view(None), console=console, refresh_per_second=8) as live:
        for take in takes:
            clip = manifest.clip(take.clip)
            sync = manifest.sync_for(take.clip)
            if not clip or not sync:
                continue
            name = f"{take.index:03d}_t{int(take.start_s)//60}m{int(take.start_s)%60:02d}s"
            out_path = out_dir / f"{name}.mp4"
            task = progress.add_task(name, total=max(take.duration_s, 0.001))
            live.update(view(active=take.index))  # paint this take yellow

            def cb(done, _task=task, _dur=take.duration_s):
                progress.update(_task, completed=min(done, _dur))

            render_segment(session_dir, clip, sync, config, take.start_s, take.end_s,
                           out_path, on_progress=cb)
            progress.update(task, completed=take.duration_s)
            rendered.add(take.index)
            live.update(view(active=None))  # flip it to done (bright green)

            if config.output.sidecars:
                _write_sidecars(session_dir, manifest, take, out_dir / f"{name}_stems")


def _write_sidecars(session_dir: Path, manifest: Manifest, take, stem_dir: Path) -> None:
    """Per-take stem WAVs over the take's audio window, for a real re-edit later."""
    sync = manifest.sync_for(take.clip)
    if not sync:
        return
    b = sync.drift_ppm / 1e6
    audio_in = sync.offset_s + (1.0 + b) * take.start_s
    dur = (take.end_s - take.start_s) * (1.0 + b)
    stem_dir.mkdir(parents=True, exist_ok=True)
    for stem in manifest.stems:
        out = stem_dir / f"{stem.role or Path(stem.name).stem}.wav"
        ff_run(["-ss", f"{max(0.0, audio_in)}", "-t", f"{dur}",
                "-i", str(session_dir / stem.src_path), "-c", "copy", str(out)])
