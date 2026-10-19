"""The session timeline: the whole studio audio as one bar, with each clip's
aligned camera coverage, the takes within it, and (live) each take as it renders.

Two layers, not one. Coverage ("which clip was filming") and take state ("detected
/ rendering / done") are tracked separately and composed at draw time, so painting
a take no longer erases which clip it came from. In the default HALF mode both
layers share a row: ``▄`` renders its lower half in the foreground colour (take
state) and its upper half in the background colour (coverage).

Reused by ``status``/after-``takes`` (static) and by the render stage (live fill).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from rich.console import Console, Group, RenderableType
from rich.style import Style
from rich.text import Text

from ..manifest import Manifest
from . import chrome, theme

# Layer values are theme *style names*, never hex — that keeps the model
# renderable-agnostic and the tests readable.
S_VOID, S_FILM_A, S_FILM_B = "tl.void", "tl.film.a", "tl.film.b"
S_TAKE, S_ACTIVE, S_DONE = "tl.take", "tl.active", "tl.done"

#: Candidate axis tick intervals, seconds.
TICK_STEPS = (15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)

#: Below this many cells there is no room for a labelled axis.
MIN_AXIS_CELLS = 32
#: Below this the bar carries too little resolution to be worth drawing.
MIN_BAR_CELLS = 16


class BarMode(StrEnum):
    HALF = "half"    # two layers in one row, via fg/bg of ▄
    STACK = "stack"  # two rows of █ — fallback for fonts with half-block seams
    GLYPH = "glyph"  # one row, distinct characters, no colour


@dataclass(frozen=True)
class TimelineModel:
    """Per-cell state of the session, independent of how it gets drawn."""

    n: int
    ref_dur: float
    cover: list[str]          # S_VOID / S_FILM_A / S_FILM_B
    take: list[str | None]    # S_TAKE / S_ACTIVE / S_DONE, or None
    owner: list[int | None]   # take index per cell, for boundaries and markers
    starts: dict[int, int] = field(default_factory=dict)


# ---- geometry -----------------------------------------------------------

def _synced_clips(manifest: Manifest):
    for clip in manifest.clips:
        s = manifest.sync_for(clip.name)
        if s and s.is_usable:
            yield clip, s


def _clip_span(manifest: Manifest, clip, sync, ref_dur: float) -> tuple[float, float]:
    ci, co = sync.overlap(clip, ref_dur)
    return sync.audio_time(ci), sync.audio_time(co)


def _take_span(manifest: Manifest, take) -> tuple[float, float]:
    s = manifest.sync_for(take.clip)
    return s.audio_time(take.start_s), s.audio_time(take.end_s)


def _cells(lo_frac: float, hi_frac: float, n: int) -> tuple[int, int]:
    """Half-open cell range [i0, i1) for a fractional span (always ≥ 1 cell wide).

    ``i0`` is clamped inside the bar. A take can genuinely start past the end of
    the reference — a late sync offset puts its audio beyond the master mix — and
    an unclamped index there is an out-of-range cell that crashes the marker row.
    """
    i0 = min(max(0, int(lo_frac * n)), max(0, n - 1))
    i1 = min(n, max(i0 + 1, int(round(hi_frac * n))))
    return i0, i1


def _paint(styles: list, lo_frac: float, hi_frac: float, style) -> None:
    i0, i1 = _cells(lo_frac, hi_frac, len(styles))
    for i in range(i0, i1):
        styles[i] = style


def _fmt(s: float) -> str:
    s = max(0.0, s)
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def clip_short(name: str) -> str:
    """``DJI_20261011152550_0012_D.MP4`` -> ``DJI_…0012``.

    Keeps the family prefix so the column still reads as a clip name; the bare
    4-digit index it used to print matched neither the header nor the Clips table.
    """
    stem = name.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) >= 3:
        return f"{parts[0]}{theme.GLYPHS['ellipsis']}{parts[-2]}"
    return stem


# ---- model --------------------------------------------------------------

def build_model(manifest: Manifest, ref_dur: float, n: int, *,
                rendered: frozenset[int] = frozenset(),
                active: int | None = None) -> TimelineModel:
    cover: list[str] = [S_VOID] * n
    take: list[str | None] = [None] * n
    owner: list[int | None] = [None] * n
    starts: dict[int, int] = {}

    for i, (clip, sync) in enumerate(_synced_clips(manifest)):
        lo, hi = _clip_span(manifest, clip, sync, ref_dur)
        _paint(cover, lo / ref_dur, hi / ref_dur, S_FILM_A if i % 2 == 0 else S_FILM_B)

    for t in manifest.takes:
        if t.dropped:
            continue
        lo, hi = _take_span(manifest, t)
        state = (S_ACTIVE if t.index == active
                 else S_DONE if t.index in rendered else S_TAKE)
        i0, i1 = _cells(lo / ref_dur, hi / ref_dur, n)
        starts[t.index] = i0
        for i in range(i0, i1):
            take[i] = state
            owner[i] = t.index

    return TimelineModel(n=n, ref_dur=ref_dur, cover=cover, take=take,
                         owner=owner, starts=starts)


# ---- rendering ----------------------------------------------------------

#: GLYPH mode: one character per state, so the bar survives with no colour at all.
_GLYPH_MAP = {
    S_VOID: "dot", S_FILM_A: "shade_lo", S_FILM_B: "shade_mid",
    S_TAKE: "dash", S_ACTIVE: "step", S_DONE: "full",
}


def _resolve(console: Console | None, name: str) -> str:
    """Theme style name -> a colour rich can compose with."""
    if console is not None:
        try:
            colour = console.get_style(name).color
            if colour is not None:
                return colour.name
        except Exception:  # pragma: no cover - unknown style, fall through
            pass
    return theme.PALETTE[name.split(".", 1)[1].replace(".", "_")]


def _pick_mode(console: Console | None, mode: BarMode | None) -> BarMode:
    if mode is not None:
        return mode
    if console is not None and theme.is_monochrome(console):
        return BarMode.GLYPH
    return BarMode.HALF


def bar_rows(model: TimelineModel, *, console: Console | None = None,
             mode: BarMode | None = None) -> list[Text]:
    """The bar itself: one row in HALF/GLYPH mode, two in STACK."""
    mode = _pick_mode(console, mode)
    g = theme.glyphs(console) if console is not None else theme.GLYPHS

    if mode is BarMode.GLYPH:
        row = Text()
        for i in range(model.n):
            state = model.take[i] or model.cover[i]
            row.append(g[_GLYPH_MAP[state]])
        return [row]

    if mode is BarMode.STACK:
        top, bottom = Text(), Text()
        for i in range(model.n):
            top.append(g["full"], style=model.cover[i])
            bottom.append(g["full"], style=model.take[i] or model.cover[i])
        return [top, bottom]

    # HALF: compose fg (take layer) over bg (coverage layer) per cell. A theme
    # lookup of "a on b" raises, so the Style has to be built by hand.
    cache: dict[tuple[str | None, str], Style] = {}

    def style_for(take_name: str | None, cover_name: str) -> Style:
        key = (take_name, cover_name)
        if key not in cache:
            cache[key] = Style(color=_resolve(console, take_name or cover_name),
                               bgcolor=_resolve(console, cover_name))
        return cache[key]

    row = Text()
    for i in range(model.n):
        # Seam between two adjacent takes: a dark sliver drawn *inside* the take
        # colour. The old blank space punched a hole that read as missing data.
        boundary = (i and model.owner[i] is not None
                    and model.owner[i - 1] is not None
                    and model.owner[i] != model.owner[i - 1])
        if boundary:
            row.append(g["boundary"],
                       style=Style(color=_resolve(console, S_VOID),
                                   bgcolor=_resolve(console, model.take[i] or model.cover[i])))
        else:
            row.append(g["cell"], style=style_for(model.take[i], model.cover[i]))
    return [row]


def marker_row(model: TimelineModel, *, console: Console | None = None) -> Text | None:
    """Take indices above the bar, where each one fits without colliding."""
    if not model.starts:
        return None
    g = theme.glyphs(console) if console is not None else theme.GLYPHS
    slots = [" "] * model.n
    taken = [False] * model.n
    for index in sorted(model.starts):
        col = model.starts[index]
        label = str(index)
        if col + len(label) <= model.n and not any(taken[col:col + len(label)]):
            for k, ch in enumerate(label):
                slots[col + k] = ch
                taken[col + k] = True
        elif not taken[col]:
            slots[col] = g["dot"]
            taken[col] = True
    return Text("".join(slots), style="tl.marker")


def axis_rows(model: TimelineModel, *, console: Console | None = None) -> list[Text]:
    """A tick row and a label row, or [] when there isn't room to label them."""
    if model.n < MIN_AXIS_CELLS or model.ref_dur <= 0:
        return []
    g = theme.glyphs(console) if console is not None else theme.GLYPHS

    label_w = len(_fmt(model.ref_dur))
    pitch = label_w + 2
    step = next((s for s in TICK_STEPS if (model.ref_dur / s) * pitch <= model.n), None)
    if step is None:
        return []

    ticks = [" "] * model.n
    labels = [" "] * model.n

    def place(col: int, text: str) -> bool:
        col = max(0, min(col, model.n - len(text)))
        if any(labels[c] != " " for c in range(max(0, col - 1),
                                               min(model.n, col + len(text) + 1))):
            return False
        for k, ch in enumerate(text):
            labels[col + k] = ch
        return True

    # The end label is the one that must always be readable, so it is placed first
    # and any regular tick that would collide with it is dropped.
    end_text = _fmt(model.ref_dur)
    place(model.n - len(end_text), end_text)
    ticks[model.n - 1] = g["tick"]

    t = 0.0
    while t <= model.ref_dur:
        col = round(t / model.ref_dur * (model.n - 1))
        if place(col, _fmt(t)):
            ticks[col] = g["tick"]
        t += step

    return [Text("".join(ticks), style="tl.tick"),
            Text("".join(labels), style="faint")]


def legend(console: Console | None = None, *, mode: BarMode | None = None) -> Text:
    mode = _pick_mode(console, mode)
    g = theme.glyphs(console) if console is not None else theme.GLYPHS
    out = Text()
    pairs = [("filmed", S_FILM_A), ("take", S_TAKE), ("rendering", S_ACTIVE),
             ("done", S_DONE), ("no video", S_VOID)]
    for label, state in pairs:
        if mode is BarMode.GLYPH:
            out.append(g[_GLYPH_MAP[state]] + " ", style="faint")
        else:
            out.append(g["full"] + " ", style=state)
        out.append(f"{label}   ", style="faint")
    return out


def bar_cells(console: Console | None, width: int | None = None) -> int:
    """How many cells the bar gets — aligned to the content gutter, not the edge."""
    if width is None and console is not None:
        width = chrome.pane_width(console)
    return max(MIN_BAR_CELLS, min(int(width or 78), 240))


def master_bar(manifest: Manifest, ref_dur: float, width: int,
               rendered: frozenset[int] = frozenset(), active: int | None = None,
               *, console: Console | None = None,
               mode: BarMode | None = None) -> Text:
    """The bar on its own (first row only in STACK mode)."""
    n = bar_cells(console, width if console is None else None)
    model = build_model(manifest, ref_dur, n, rendered=rendered, active=active)
    return bar_rows(model, console=console, mode=mode)[0]


def master_panel(manifest: Manifest, ref_dur: float, width: int,
                 rendered: frozenset[int] = frozenset(), active: int | None = None,
                 *, console: Console | None = None, show_axis: bool = True,
                 show_markers: bool = True,
                 mode: BarMode | None = None) -> Group:
    """Markers, bar, axis and legend as one block."""
    n = bar_cells(console, width if console is None else None)
    model = build_model(manifest, ref_dur, n, rendered=rendered, active=active)

    parts: list[RenderableType] = []
    if show_markers:
        markers = marker_row(model, console=console)
        if markers is not None:
            parts.append(markers)
    parts.extend(bar_rows(model, console=console, mode=mode))
    axis = axis_rows(model, console=console) if show_axis else []
    if axis:
        parts.extend(axis)
    else:
        parts.append(Text(f"0:00 {theme.GLYPHS['ellipsis']} {_fmt(ref_dur)}",
                          style="faint"))
    parts.append(legend(console, mode=mode))
    return Group(*parts)


def breakdown(manifest: Manifest, ref_dur: float | None = None):
    """Per-clip take list: which takes come from which clip and how long they are."""
    table = chrome.table(
        chrome.c_name("source clip"),
        chrome.c_num("#"),
        chrome.c_time("window (audio)"),
        chrome.c_time("dur"),
    )
    last_clip = None
    for take in manifest.takes:
        if take.dropped:
            continue
        lo, hi = _take_span(manifest, take)
        label = clip_short(take.clip)
        # Continuation rows are dimmed rather than blanked: an empty cell inside a
        # grouped column reads as missing data.
        cell = Text(label, style="value" if label != last_clip else "faint")
        last_clip = label
        table.add_row(cell, f"{take.index:03d}",
                      f"{_fmt(lo)}{theme.GLYPHS['range']}{_fmt(hi)}",
                      _fmt(take.duration_s))
    return table


def show_timeline(console: Console, manifest: Manifest, ref_dur: float) -> None:
    """Static view for `status` / after take detection."""
    if not manifest.takes:
        return
    if chrome.content_width(console) >= MIN_BAR_CELLS:
        chrome.section(console, f"session timeline  0:00 … {_fmt(ref_dur)}",
                       master_panel(manifest, ref_dur, console.width, console=console))
    chrome.section(console, "takes by clip", breakdown(manifest, ref_dur))
