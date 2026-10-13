"""Stage 9 — render: one ffmpeg pass per segment.

Trim the DJI original, drift-correct the video (never the audio), convert to
grayscale, and mux the studio master mix — camera audio is simply never mapped.
Renders whole overlapping clips (``full_clips=True``) or per-take segments (M4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from ..config import Config
from ..manifest import Clip, Manifest, SyncResult, Take
from ..media import has_nvenc
from ..media import run as ff_run
from .mix import MASTER_REL
from .reference import CORRELATION_FS  # noqa: F401  (kept for parity/imports)


@dataclass(frozen=True)
class RenderOutcome:
    """What a render pass produced — feeds the stage summary and closing card."""

    rendered: frozenset[int] = frozenset()
    failed: tuple[tuple[int, str], ...] = ()
    media_s: float = 0.0
    bytes_written: int = 0
    elapsed_s: float = 0.0


def take_label(take: Take) -> str:
    """``007_t23m39s`` — the output basename and the progress-bar label."""
    return f"{take.index:03d}_t{int(take.start_s) // 60}m{int(take.start_s) % 60:02d}s"


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
    audio_in = sync.audio_time(clip_in)
    out_dur = (clip_out - clip_in) * sync.rate

    fps = config.output.fps or clip.fps
    gray = _grayscale(config.output.bw_mode)
    vfilter = f"[0:v:0]setpts=(PTS-STARTPTS)*{sync.rate:.9f},{gray},fps={fps}[v]"

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


def run(session_dir: Path, manifest: Manifest, config: Config, st,
        full_clips: bool = False) -> RenderOutcome:
    session_dir = Path(session_dir)
    master = session_dir / MASTER_REL
    if not master.is_file():
        raise RuntimeError("No master mix — run the mix stage first.")
    ref_dur = sf.info(str(master)).frames / sf.info(str(master)).samplerate

    if full_clips or not manifest.takes:
        outcome = _render_full_clips(session_dir, manifest, config, st, ref_dur)
    else:
        outcome = _render_takes(session_dir, manifest, config, st, ref_dur)

    manifest.mark_done("render")
    manifest.save(session_dir)
    return outcome


def _render_full_clips(session_dir, manifest, config, st, ref_dur) -> RenderOutcome:
    from ..tui.progress import stage_progress

    out_dir = session_dir / "out"
    out_dir.mkdir(exist_ok=True)
    synced = [(c, manifest.sync_for(c.name)) for c in manifest.clips]
    synced = [(c, s) for c, s in synced if s and s.is_usable]
    if not synced:
        st.warn("No synced clips to render.")
        return RenderOutcome()

    t0 = time.monotonic()
    written = 0
    with stage_progress(st.console) as track:
        for clip, sync in synced:
            clip_in, clip_out = sync.overlap(clip, ref_dur)
            if clip_out - clip_in < 1.0:
                continue
            out_path = out_dir / f"{Path(clip.name).stem}_bw.mp4"
            track(clip.name, (clip_out - clip_in),
                  lambda cb, c=clip, s=sync, ci=clip_in, co=clip_out, o=out_path:
                  render_segment(session_dir, c, s, config, ci, co, o, on_progress=cb))
            if out_path.is_file():
                written += out_path.stat().st_size
    return RenderOutcome(bytes_written=written, elapsed_s=time.monotonic() - t0)


def _render_takes(session_dir, manifest, config, st, ref_dur, *,
                  render=None) -> RenderOutcome:
    """Render each take, filling its segment on the session timeline as it goes.

    The aggregate task is what makes the ETA real: its total is the sum of take
    durations and its units are output-media seconds, so rich's ``task.speed``
    becomes the encode realtime factor and ``TimeRemainingColumn`` does the rest.
    Per-take bars are *removed* once finished and replaced by a permanent log
    line, so the live region stays a fixed height instead of growing past the
    bottom of the terminal.

    ``render`` is injectable so the loop can be driven without ffmpeg in tests.
    """
    from rich.console import Group
    from rich.text import Text

    from ..tui import progress as prog
    from ..tui import timeline as tl

    render = render or render_segment
    out_dir = session_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    takes = [t for t in manifest.takes if not t.dropped]
    total_media = sum(t.duration_s for t in takes) or 0.001

    console = st.console
    # Two Progress objects: the aggregate needs a long speed window for a stable
    # ETA, the current take a short one so its bar stays responsive.
    total_p = prog.make_progress(console, *prog.columns_total(console), speed_window=600.0)
    item_p = prog.make_progress(console, *prog.columns_item(console), speed_window=30.0)
    agg = total_p.add_task("", total=total_media, headline=f"takes 0/{len(takes)}")

    rendered: set[int] = set()
    failed: list[tuple[int, str]] = []
    done_media = 0.0
    written = 0
    active: int | None = None
    t0 = time.monotonic()
    compact = console.height < 20

    def view() -> Group:
        panel = tl.master_panel(manifest, ref_dur, console.width, frozenset(rendered),
                               active, console=console, show_markers=not compact)
        return Group(panel, Text(""), total_p, item_p)

    with st.live(view) as live:
        for k, take in enumerate(takes, 1):
            clip = manifest.clip(take.clip)
            sync = manifest.sync_for(take.clip)
            if not clip or not sync:
                continue
            name = take_label(take)
            out_path = out_dir / f"{name}.mp4"
            active = take.index
            total_p.update(agg, headline=f"takes {k}/{len(takes)}")
            item = item_p.add_task(name, total=max(take.duration_s, 0.001))
            live.update(view())

            def cb(done_s, _task=item, _dur=take.duration_s, _base=done_media):
                # ffmpeg's out_time can overrun -t; clamp so the aggregate lands
                # exactly on its total.
                clamped = min(done_s, _dur)
                item_p.update(_task, completed=clamped)
                total_p.update(agg, completed=_base + clamped)

            try:
                render(session_dir, clip, sync, config, take.start_s, take.end_s,
                       out_path, on_progress=cb)
            except Exception as exc:  # one bad take must not abort the run
                failed.append((take.index, str(exc)))
                item_p.remove_task(item)
                active = None
                live.update(view())
                st.item("error", name, Text(str(exc), style="error"))
                continue

            done_media += take.duration_s
            total_p.update(agg, completed=done_media)
            item_p.remove_task(item)
            rendered.add(take.index)
            active = None
            live.update(view())

            size = out_path.stat().st_size if out_path.is_file() else 0
            written += size
            st.item("ok", name, _take_detail(take.duration_s, size))

            if config.output.sidecars:
                _write_sidecars(session_dir, manifest, take, out_dir / f"{name}_stems")

    return RenderOutcome(rendered=frozenset(rendered), failed=tuple(failed),
                         media_s=done_media, bytes_written=written,
                         elapsed_s=time.monotonic() - t0)


def _take_detail(duration_s: float, size: int):
    from rich.text import Text

    from ..tui import chrome

    text = Text()
    text.append(f"{int(duration_s) // 60}:{int(duration_s) % 60:02d}", style="value")
    if size:
        text.append(f"  {chrome.human_bytes(size)}", style="unit")
    return text


def _write_sidecars(session_dir: Path, manifest: Manifest, take, stem_dir: Path) -> None:
    """Per-take stem WAVs over the take's audio window, for a real re-edit later."""
    sync = manifest.sync_for(take.clip)
    if not sync:
        return
    audio_in = sync.audio_time(take.start_s)
    dur = take.duration_s * sync.rate
    stem_dir.mkdir(parents=True, exist_ok=True)
    for stem in manifest.stems:
        out = stem_dir / f"{stem.role or Path(stem.name).stem}.wav"
        ff_run(["-ss", f"{max(0.0, audio_in)}", "-t", f"{dur}",
                "-i", str(session_dir / stem.src_path), "-c", "copy", str(out)])
