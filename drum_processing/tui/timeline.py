"""Timeline visualization: a grey bar for the whole studio audio, filled with each
clip's aligned coverage, then the takes within, and (live) each take as it renders.

Reused by ``status``/after-``takes`` (static) and by the render stage (live fill).
"""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..manifest import Manifest
from ..stages.render import _overlap

# Cell styles, in ascending precedence (later paints win).
C_AUDIO = "grey30"        # studio audio with no camera coverage
C_CLIP_A = "blue"         # clip-covered (alternating shades per clip)
C_CLIP_B = "dodger_blue2"
C_TAKE = "green"          # a detected take, not yet rendered
C_RENDERED = "bright_green"
C_ACTIVE = "yellow"       # currently rendering


def _synced_clips(manifest: Manifest):
    for clip in manifest.clips:
        s = manifest.sync_for(clip.name)
        if s and s.status in ("ok", "low_confidence"):
            yield clip, s


def _clip_span(manifest: Manifest, clip, sync, ref_dur: float) -> tuple[float, float]:
    b = sync.drift_ppm / 1e6
    ci, co = _overlap(sync, clip, ref_dur)
    return sync.offset_s + (1 + b) * ci, sync.offset_s + (1 + b) * co


def _take_span(manifest: Manifest, take) -> tuple[float, float]:
    s = manifest.sync_for(take.clip)
    b = s.drift_ppm / 1e6
    return s.offset_s + (1 + b) * take.start_s, s.offset_s + (1 + b) * take.end_s


def _paint(styles: list[str], lo_frac: float, hi_frac: float, style: str) -> None:
    n = len(styles)
    i0 = max(0, int(lo_frac * n))
    i1 = min(n, max(i0 + 1, int(round(hi_frac * n))))
    for i in range(i0, i1):
        styles[i] = style


def _fmt(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def master_bar(manifest: Manifest, ref_dur: float, width: int,
               rendered: frozenset[int] = frozenset(), active: int | None = None) -> Text:
    n = max(10, min(width - 2, 100))
    styles = [C_AUDIO] * n
    for i, (clip, sync) in enumerate(_synced_clips(manifest)):
        lo, hi = _clip_span(manifest, clip, sync, ref_dur)
        _paint(styles, lo / ref_dur, hi / ref_dur, C_CLIP_A if i % 2 == 0 else C_CLIP_B)
    for take in manifest.takes:
        if take.dropped:
            continue
        lo, hi = _take_span(manifest, take)
        style = (C_ACTIVE if take.index == active
                 else C_RENDERED if take.index in rendered else C_TAKE)
        _paint(styles, lo / ref_dur, hi / ref_dur, style)

    bar = Text()
    for st in styles:
        bar.append("█", style=st)
    return bar


def _legend() -> Text:
    t = Text()
    for label, style in [("filmed", C_CLIP_A), ("take", C_TAKE),
                         ("rendering", C_ACTIVE), ("done", C_RENDERED), ("no video", C_AUDIO)]:
        t.append("█ ", style=style)
        t.append(f"{label}   ", style="dim")
    return t


def master_panel(manifest: Manifest, ref_dur: float, width: int,
                 rendered: frozenset[int] = frozenset(), active: int | None = None) -> Group:
    title = Text(f"Session timeline  0:00 … {_fmt(ref_dur)}", style="bold")
    return Group(title, master_bar(manifest, ref_dur, width, rendered, active), _legend())


def breakdown(manifest: Manifest, ref_dur: float) -> Table:
    """Per-clip take list: which takes come from which clip and how long they are."""
    table = Table(title="Takes by clip", header_style="bold cyan", show_edge=True)
    for col in ("clip", "#", "window (audio)", "dur"):
        table.add_column(col)
    last_clip = None
    for take in manifest.takes:
        if take.dropped:
            continue
        lo, hi = _take_span(manifest, take)
        clip_label = take.clip.split("_")[-2] if "_" in take.clip else take.clip
        shown = "" if clip_label == last_clip else f"…{clip_label}"
        last_clip = clip_label
        table.add_row(shown, f"{take.index:03d}", f"{_fmt(lo)}–{_fmt(hi)}",
                      f"{_fmt(take.duration_s)}")
    return table


def show_timeline(console, manifest: Manifest, ref_dur: float) -> None:
    """Static view for `status` / after take detection."""
    if not manifest.takes:
        return
    console.print(master_panel(manifest, ref_dur, console.width))
    console.print(breakdown(manifest, ref_dur))
