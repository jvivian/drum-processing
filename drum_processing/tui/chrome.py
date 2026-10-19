"""Layout primitives: one content width, one gutter, one table style, one header.

The pipeline prints sequentially and scrolls — this is not a full-screen app. What
makes it read as designed rather than accumulated is that every block shares a left
edge, a right edge, and a blank-line rhythm. That's what lives here.

``stage()`` also owns the single live display. rich permits one ``Live`` at a time
and ``console.status`` *is* a ``Live``, so if stages kept opening their own we'd
deadlock the moment a stage wanted a spinner inside a collapsing section. Stages
borrow the region via ``Stage.step`` / ``Stage.progress`` / ``Stage.live`` instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import rich.box as box
from rich.console import Console, Group, RenderableType
from rich.constrain import Constrain
from rich.live import Live
from rich.padding import Padding
from rich.progress import Progress, ProgressColumn
from rich.segment import Segment, Segments
from rich.table import Column, Table
from rich.text import Text

from . import theme

MAX_CONTENT = 100
GUTTER = 2


def content_width(console: Console) -> int:
    """The width every block is laid out against."""
    return max(40, min(console.width - GUTTER, MAX_CONTENT))


def indent(renderable: RenderableType, console: Console | None = None) -> RenderableType:
    """The one gutter in the program.

    Constrained to the content width so a wide terminal gets a readable measure
    instead of a block stretched to the window edge.
    """
    padded = Padding(renderable, (0, 0, 0, GUTTER))
    if console is None:
        return padded
    return Constrain(padded, _pane_frame_width(console))


# ---- vertical rhythm ----------------------------------------------------

_last_was_blank: dict[int, bool] = {}


def blank(console: Console) -> None:
    """Exactly one blank line — never two, never zero."""
    if not _last_was_blank.get(id(console), True):
        console.print()
    _last_was_blank[id(console)] = True


def _mark_printed(console: Console) -> None:
    _last_was_blank[id(console)] = False


def write(console: Console, renderable: RenderableType, *, gutter: bool = True) -> None:
    console.print(indent(renderable, console) if gutter else renderable)
    _mark_printed(console)


def section(console: Console, title: str, body: RenderableType) -> None:
    """A labelled block: left-aligned caption, indented body."""
    blank(console)
    write(console, Text(title, style="section"), gutter=True)
    write(console, body)


# ---- tables -------------------------------------------------------------

def table(*columns: str | Column, title: str | None = None,
          show_header: bool = True, expand: bool = False,
          width: int | None = None) -> Table:
    """The only Table configuration in the project.

    SIMPLE_HEAD gives a single header underline with no frame and no verticals.
    ``box=None`` would drop the underline entirely and leave headers floating;
    the rich default (HEAVY_HEAD) draws a full frame, which is more border than
    data for the three-row tables this tool prints.
    """
    return Table(
        *columns,
        title=title,
        box=box.SIMPLE_HEAD,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1, 0, 0),
        show_header=show_header,
        header_style="table.header",
        title_style="table.title",
        title_justify="left",
        border_style="table.border",
        expand=expand,
        width=width,
        highlight=False,
    )


# Narrow terminals have to lose something, and these factories decide what.
#
# rich only shrinks columns it considers wrapable (`no_wrap=False`); if every
# column is no_wrap it flat-reduces all of them and ignores min_width, which is
# how "10:00" became "10:…" and "original" became "origin". So exactly one column
# per table — the long name, via ``c_name`` — stays wrapable and absorbs the
# shrink. A truncated filename is recoverable; a truncated figure is a lie.

def c_name(header: str, *, style: str = "value") -> Column:
    """The one flexible column in a table: the long, truncatable name.

    A filename has no spaces to wrap at, so ``overflow="ellipsis"`` is what
    actually happens when it is squeezed.
    """
    return Column(header, justify="left", style=style, no_wrap=False,
                  overflow="ellipsis", min_width=8)


def c_text(header: str, *, style: str = "value", wrap: bool = False) -> Column:
    """A short, fixed-vocabulary label. Never shrinks."""
    return Column(header, justify="left", style=style, no_wrap=not wrap,
                  overflow="fold" if wrap else "ellipsis")


def c_num(header: str, *, style: str = "value") -> Column:
    """Numeric column. Right-aligned, because that is what makes figures scannable."""
    return Column(header, justify="right", style=style, no_wrap=True,
                  overflow="ignore")


def c_time(header: str, *, style: str = "value") -> Column:
    return Column(header, justify="right", style=style, no_wrap=True,
                  overflow="ignore")


def c_flag(header: str, *, style: str = "value") -> Column:
    return Column(header, justify="center", style=style, no_wrap=True,
                  overflow="ignore")


# ---- formatting ---------------------------------------------------------

def human_dur(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def dir_size(path: Path) -> tuple[int, int]:
    """(bytes, file count) over a directory tree; (0, 0) if it isn't there."""
    total = count = 0
    if not Path(path).is_dir():
        return 0, 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            count += 1
    return total, count


# ---- height budget ------------------------------------------------------

#: Rows a pane leaves clear so its final frame cannot scroll the screen. rich's
#: last frame ends in a newline, so one row of slack is the true floor; this
#: keeps a second in hand.
PANE_RESERVED = 3
#: Below this many rows a pane is not worth drawing at all.
MIN_PANE = 6


def pane_width(console: Console) -> int:
    """The width pane contents are *painted* at.

    Two columns go to the gutter, and one more is left bare on the right. That
    last column is not cosmetic: a row filled to exactly ``console.width`` makes
    the terminal auto-wrap onto a second screen row, while rich still counts it
    as one. Inside a live region that miscount is fatal — the teardown erases
    fewer rows than were drawn and the pane is left on screen.

    Measuring at ``console.width`` also overcounts what fits, and lets ``fit``
    freeze a line too wide to ever re-wrap.
    """
    return min(content_width(console), max(1, console.width - GUTTER - 1))


def _pane_frame_width(console: Console) -> int:
    """Total width of an indented pane frame, gutter included."""
    return GUTTER + pane_width(console)


def measure(console: Console, renderable: RenderableType,
            width: int | None = None) -> int:
    """How many terminal lines ``renderable`` occupies when painted in a pane.

    ``height=None`` is what makes this the *true* height rather than the height
    clipped to the current terminal.
    """
    options = console.options.update(width=width or pane_width(console), height=None)
    return len(console.render_lines(renderable, options, pad=False))


def pane_budget(console: Console) -> int:
    """Rows a stage's pane may occupy. Read fresh so a mid-run resize adapts."""
    return max(MIN_PANE, console.height - PANE_RESERVED)


def fit(console: Console, renderable: RenderableType, budget: int, *,
        keep: str = "head", width: int | None = None) -> RenderableType:
    """Clamp a renderable to ``budget`` lines.

    We do this rather than leaning on rich's ``vertical_overflow`` because rich
    crops the *bottom* — which is exactly where a pane pins its progress bars.

    ``keep`` decides which end survives, and the two have opposite right answers:
    a table must keep its **head** or it loses the header, while a growing log
    must keep its **tail** or it freezes on the first few entries and shows those
    for the rest of the run.
    """
    w = width or pane_width(console)
    lines = console.render_lines(renderable, console.options.update(width=w, height=None),
                                 pad=False)
    if len(lines) <= budget:
        return renderable
    room = max(1, budget - 1)
    kept = lines[:room] if keep == "head" else lines[-room:]
    segments: list[Segment] = []
    for line in kept:
        segments.extend(line)
        segments.append(Segment.line())
    hidden = len(lines) - len(kept)
    marker = Text(f"+{hidden} more" if keep == "head" else f"+{hidden} earlier",
                  style="faint")
    frozen = Segments(segments)
    return Group(frozen, marker) if keep == "head" else Group(marker, frozen)


# ---- the stepper --------------------------------------------------------

def stepper(console: Console, stages: Sequence[str], current: int) -> Text:
    """``✓ ingest ── ✓ sync ── mix ── takes ── render``

    Where the run is, in one line. Completed stages keep their name but hand the
    emphasis forward; the current one is the only thing on the strip at full
    weight. At its longest this is 48 columns, so it needs no narrow-width
    variant — it fits wherever a table does.
    """
    g = theme.glyphs(console)
    text = Text()
    for i, name in enumerate(stages):
        if i:
            text.append(f" {g['dash'] * 2} ", style="rule")
        if i < current:
            text.append(f"{g['ok']} ", style="ok")
            text.append(name, style="muted")
        elif i == current:
            text.append(name, style="stage.name")
        else:
            text.append(name, style="faint")
    return text


# ---- stages -------------------------------------------------------------

@dataclass
class Ledger:
    """What each stage did, kept for the closing card.

    Panes are transient, so the run's narrative would otherwise vanish with the
    last frame. This is the thread that survives it.
    """

    entries: list[tuple[str, Text, float, bool]] = field(default_factory=list)

    def record(self, name: str, summary: Text, elapsed: float, ok: bool = True) -> None:
        self.entries.append((name, summary, elapsed, ok))


class _PaneLive:
    """A stand-in for ``Live`` that composes into the pane instead of owning it.

    Lets a stage body keep writing ``live.update(view())`` whether it has the
    screen to itself or is sharing a pane with a stepper and a log.
    """

    def __init__(self, stage: Stage) -> None:
        self._stage = stage

    def update(self, renderable: RenderableType, **_: object) -> None:
        self._stage.attach(renderable)

    def refresh(self) -> None:
        self._stage.refresh()

    @property
    def renderable(self) -> RenderableType:
        return self._stage.render()


@dataclass
class Stage:
    """A pipeline stage's slice of the screen.

    Two presentations, one API. In **log mode** every method prints to scrollback
    as it always has. In **pane mode** the stage owns one transient ``Live``
    holding a stepper, a body and optionally a pinned renderable (progress bars,
    the timeline); the whole thing is erased when the stage ends, and the
    ``Ledger`` carries the summary forward to the closing card.

    Pane mode is what makes a stage affordable: its body costs nothing once the
    stage is over, so a step can show whatever makes it legible.
    """

    name: str
    console: Console
    index: int | None = None
    total: int | None = None
    stages: list[str] | None = None
    pane: bool = False
    t0: float = field(default_factory=time.monotonic)
    _summary: Text | None = field(default=None, repr=False)
    _live: Live | None = field(default=None, repr=False)
    _body: list[RenderableType] = field(default_factory=list, repr=False)
    _extra: RenderableType | None = field(default=None, repr=False)
    #: Which end of the body survives truncation. A replaced body is a table and
    #: needs its header; an appended one is a log and needs its newest rows.
    _keep: str = field(default="head", repr=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    @property
    def glyphs(self) -> dict[str, str]:
        return theme.glyphs(self.console)

    # -- the pane ---------------------------------------------------------

    def _head(self) -> RenderableType:
        """The strip above the body: the run's stepper, or this stage's header."""
        if self.stages:
            return stepper(self.console, self.stages, (self.index - 1) if self.index else 0)
        return _header(self.console, self.name, self.index, self.total)

    def render(self) -> RenderableType:
        """The pane: head strip, body, pinned extras — clamped to the budget.

        Budgeted in priority order. What is pinned below is the live part (bars,
        the timeline), so it is the *least* droppable thing and gets its rows
        first; the body takes what is left. The composed frame is then clamped
        again so no caller can overrun the pane silently.
        """
        budget = pane_budget(self.console)
        head = self._head()
        parts: list[RenderableType] = [head, Text("")]
        used = measure(self.console, head) + 1

        extra = self._extra
        if extra is not None:
            extra = fit(self.console, extra, max(1, budget - used))
            used += measure(self.console, extra)

        if self._body:
            body = fit(self.console, Group(*self._body),
                       max(1, budget - used), keep=self._keep)
            parts.append(body)
        if extra is not None:
            parts.append(extra)

        return indent(fit(self.console, Group(*parts), budget), self.console)

    def height(self) -> int:
        """Rows the composed pane occupies on screen.

        Measured at the full console width, because ``render`` already wrapped
        the contents in the gutter — measuring *that* at the content width would
        charge for the gutter twice and overcount.
        """
        return measure(self.console, self.render(), width=self.console.width)

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.render())

    def attach(self, renderable: RenderableType | None) -> None:
        """Pin a renderable below the body — progress bars, the live timeline."""
        if not self.pane:
            return
        self._extra = renderable
        self.refresh()

    def set_body(self, renderable: RenderableType) -> None:
        """Replace the body outright, for content that is rebuilt each tick.

        No-op in log mode: a body that redraws has no meaning in scrollback, so
        stages that use this emit per-item lines there instead.
        """
        if not self.pane:
            return
        self._body = [renderable]
        self._keep = "head"
        self.refresh()

    # -- body output ------------------------------------------------------

    def _emit(self, renderable: RenderableType, keep: str = "tail") -> None:
        """Append to the pane body.

        ``keep`` records which end matters if the body later has to be clamped:
        a running log wants its newest rows, a table wants its header. The last
        thing written wins, which is what a stage that logs a line and then
        shows a table actually wants.
        """
        if self.pane:
            self._body.append(renderable)
            self._keep = keep
            self.refresh()
            return
        write(self.console, renderable)

    def line(self, renderable: RenderableType | str) -> None:
        self._emit(renderable if not isinstance(renderable, str)
                   else Text(renderable, style="value"))

    def item(self, status: str, label: str, detail: RenderableType | str = "") -> None:
        """One result row: ``✓ DJI_…0012   offset 20.383s  drift -192 ppm``."""
        text = theme.status_text(self.console, status)
        text.append(f" {label}", style="value")
        if detail:
            text.append("  ")
            text.append(detail if isinstance(detail, Text) else Text(str(detail)))
        self._emit(text)

    def warn(self, message: str) -> None:
        text = Text(f"{self.glyphs['warn']} ", style="warn")
        text.append(message, style="value")
        self._emit(text)

    def block(self, title: str, renderable: RenderableType) -> None:
        """A titled block — structured content, so it keeps its head when clamped."""
        if self.pane:
            self._emit(Group(Text(title, style="section"), renderable), keep="head")
            return
        section(self.console, title, renderable)

    # -- the live region --------------------------------------------------

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """Work with no measurable percentage.

        In a pane it becomes a checklist row that ticks itself off, so three
        sequential steps read as progress rather than three identical spinners.
        Off a terminal it is one printed line — a piped log should still record
        what happened. A second ``Live`` is never opened; rich allows one.
        """
        g = self.glyphs
        if self.pane:
            from rich.spinner import Spinner

            slot = len(self._body)
            # A real Spinner animates purely on re-render, so the pane's own
            # refresh drives it — a step with no measurable progress still moves.
            self._body.append(Spinner("dots", Text(label, style="muted"),
                                      style="status.spinner"))
            self._keep = "tail"
            self.refresh()
            try:
                yield
            except BaseException:
                self._body[slot] = Text.assemble((f"{g['error']} ", "error"),
                                                 (label, "muted"))
                self.refresh()
                raise
            self._body[slot] = Text.assemble((f"{g['ok']} ", "ok"), (label, "muted"))
            self.refresh()
            return
        if self._live is not None or not self.console.is_terminal:
            self._emit(Text(f"{g['step']} {label}", style="muted"))
            yield
            return
        with self.console.status(Text(label, style="muted"), spinner="dots"):
            yield

    @contextmanager
    def bar(self, label: str, total_s: float) -> Iterator[Callable[[float], None] | None]:
        """A real progress bar pinned under the pane for one job's duration.

        Yields an advance callback, or ``None`` when there is nothing to pin it
        to or no duration to measure against — callers pass it straight to
        ``media.run(on_progress=...)``, which treats ``None`` as "no progress".
        """
        if total_s <= 0:
            yield None
            return
        progress = self.progress()
        task = progress.add_task(label, total=total_s)
        advance = lambda done_s: progress.update(task, completed=min(done_s, total_s))  # noqa: E731
        if self.pane:
            self.attach(progress)
            try:
                yield advance
            finally:
                self.attach(None)
            return
        # No pane to pin it to: run the bar as its own transient region. Under
        # rich 15 a nested Live does not raise, it silently renders nothing —
        # so this branch must never be taken while a pane is open.
        with Live(progress, console=self.console, refresh_per_second=8,
                  transient=True) as live:
            self._live = live
            try:
                yield advance
            finally:
                self._live = None

    def progress(self, *columns: ProgressColumn, speed_window: float = 30.0) -> Progress:
        from .progress import make_progress

        return make_progress(self.console, *columns, speed_window=speed_window)

    @contextmanager
    def live(self, build: Callable[[], RenderableType],
             transient: bool = True) -> Iterator[Live | _PaneLive]:
        """Borrow a live region. The caller re-renders via ``build``.

        In a pane the region already exists, so this returns a proxy that pins
        the caller's renderable below the body rather than replacing the pane.

        Standalone it is transient by default: the region is scaffolding, and a
        non-transient one leaves its last frame in scrollback — a stale bar
        reading "100% eta 00:00" and a second copy of whatever it was painting.
        Anything worth keeping gets reprinted deliberately.
        """
        if self.pane:
            proxy = _PaneLive(self)
            self.attach(build())
            try:
                yield proxy
            finally:
                self.attach(None)
            return
        with Live(build(), console=self.console, refresh_per_second=8,
                  transient=transient) as live:
            self._live = live
            try:
                yield live
            finally:
                self._live = None
        if not transient:
            _mark_printed(self.console)

    # -- summary ----------------------------------------------------------

    def summary(self, text: RenderableType | str) -> None:
        self._summary = text if isinstance(text, Text) else Text(str(text), style="value")



def _header(console: Console, name: str, index: int | None, total: int | None) -> Text:
    """``▎ sync ──────────────────────────────────────────────────  2/5``

    Hand-built rather than ``console.rule``: a rule centres its label and has
    nowhere to put the right-hand counter.
    """
    g = theme.glyphs(console)
    width = content_width(console)
    counter = f"{index}/{total}" if index and total else ""
    text = Text()
    text.append(f"{g['stage']} ", style="rule")
    text.append(name, style="stage.name")
    fill = width - 2 - len(name) - (len(counter) + 2 if counter else 0)
    if fill > 0:
        text.append(" " + g["dash"] * (fill - 1), style="rule")
    if counter:
        text.append(f"  {counter}", style="stage.count")
    return text


def _summary_line(console: Console, name: str, body: Text, elapsed: float,
                  *, ok: bool) -> Table:
    """Status + summary on the left, elapsed right-aligned at the content edge.

    A two-column expanding table rather than manual padding, so the elapsed times
    land on the same column for every stage no matter how long the body is. That
    shared right edge is most of what reads as rhythm.
    """
    g = theme.glyphs(console)
    left = Text(f"{g['ok'] if ok else g['error']} ", style="stage.done" if ok else "stage.failed")
    left.append(name, style="strong")
    left.append("   ")
    left.append(body)
    grid = table(c_name(""), c_time(""), show_header=False,
                 expand=True, width=content_width(console))
    grid.add_row(left, Text(human_dur(elapsed), style="faint"))
    return grid


@contextmanager
def stage(console: Console, name: str, *, index: int | None = None,
          total: int | None = None, stages: Sequence[str] | None = None,
          pane: bool = False, ledger: Ledger | None = None) -> Iterator[Stage]:
    """Run a pipeline stage, either as a transient pane or as a scrolling block.

    Pane mode needs a screen to erase, so it is forced off whenever there isn't
    one — piped output and CI get the log exactly as before.
    """
    pane = pane and console.is_terminal
    st = Stage(name=name, console=console, index=index, total=total,
               stages=list(stages) if stages else None, pane=pane)
    if not pane:
        blank(console)
        console.print(_header(console, name, index, total))
        _mark_printed(console)
        blank(console)
    try:
        if pane:
            with Live(st.render(), console=console, refresh_per_second=8,
                      transient=True) as live:
                st._live = live
                yield st
                st._live = None
        else:
            yield st
    except BaseException:
        st._live = None
        failed = Text("failed", style="stage.failed")
        if ledger is not None:
            ledger.record(name, failed, st.elapsed, ok=False)
        # Always printed, in both modes: the pane is gone by now and a failure
        # is the one thing that must not vanish with it.
        blank(console)
        console.print(_summary_line(console, name, failed, st.elapsed, ok=False))
        _mark_printed(console)
        blank(console)
        raise
    body = st._summary if st._summary is not None else Text("done", style="muted")
    if ledger is not None:
        ledger.record(name, body, st.elapsed, ok=True)
    if not pane:
        blank(console)
        console.print(_summary_line(console, name, body, st.elapsed, ok=True))
        _mark_printed(console)
        blank(console)


def ledger_table(console: Console, ledger: Ledger) -> Table:
    """The stage ledger: what each step did and how long it took.

    In pane mode this is the only permanent trace of the run's middle, so it
    reuses each stage's own summary text verbatim.
    """
    grid = table(c_text("", style="label"), c_name(""), c_time(""),
                 show_header=False, expand=True, width=content_width(console))
    g = theme.glyphs(console)
    for name, summary, elapsed, ok in ledger.entries:
        mark = Text(f"{g['ok'] if ok else g['error']} ", style="ok" if ok else "error")
        mark.append(name, style="strong" if ok else "stage.failed")
        grid.add_row(mark, summary, Text(human_dur(elapsed), style="faint"))
    return grid


def summary_card(console: Console, *, title: str,
                 rows: Sequence[tuple[str, RenderableType | str]],
                 footer: RenderableType | None = None,
                 header: RenderableType | None = None) -> None:
    """The closing frame: an optional ledger, label/value pairs, a renderable."""
    blank(console)
    console.print(Text(title, style="heading"))
    _mark_printed(console)
    blank(console)
    grid = table(c_text("", style="label"), c_name(""), show_header=False)
    for label, value in rows:
        grid.add_row(Text(label, style="label"),
                     value if isinstance(value, Text) else Text(str(value)))
    parts: list[RenderableType] = []
    if header is not None:
        parts += [header, Text("")]
    parts.append(grid)
    if footer is not None:
        parts += [Text(""), footer]
    write(console, Group(*parts))
    blank(console)
