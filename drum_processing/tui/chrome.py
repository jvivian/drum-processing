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
    return Constrain(padded, GUTTER + content_width(console))


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


# ---- stages -------------------------------------------------------------

@dataclass
class Stage:
    """A pipeline stage's slice of the screen.

    Stages receive one of these instead of a Console so that all output lands in
    the same gutter, the live region has a single owner, and the elapsed time and
    summary line come out in a consistent place.

    When ``collapse`` is set the body is buffered into a transient live region and
    erased on exit, leaving only the header and summary — for stages whose output
    is pure progress chatter nobody needs in scrollback.
    """

    name: str
    console: Console
    index: int | None = None
    total: int | None = None
    collapse: bool = False
    t0: float = field(default_factory=time.monotonic)
    _summary: Text | None = field(default=None, repr=False)
    _live: Live | None = field(default=None, repr=False)
    _buffer: list[RenderableType] = field(default_factory=list, repr=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    @property
    def glyphs(self) -> dict[str, str]:
        return theme.glyphs(self.console)

    # -- body output ------------------------------------------------------

    def _emit(self, renderable: RenderableType) -> None:
        """Buffer into the collapsing region, else print above it / to scrollback."""
        if self.collapse and self._live is not None:
            self._buffer.append(indent(renderable, self.console))
            self._live.update(Group(*self._buffer[-_COLLAPSE_LINES:]))
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
        if self.collapse and self._live is not None:
            self._emit(renderable)
            return
        section(self.console, title, renderable)

    # -- the live region --------------------------------------------------

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """Transient progress for work with no measurable percentage.

        Replaces ``console.status``. Inside a collapsing stage, or off a terminal,
        it degrades to one line — a second ``Live`` would raise, and a piped log
        should still record what happened.
        """
        if self._live is not None or not self.console.is_terminal:
            self._emit(Text(f"{self.glyphs['step']} {label}", style="muted"))
            yield
            return
        with self.console.status(Text(label, style="muted"), spinner="dots"):
            yield

    def progress(self, *columns: ProgressColumn, speed_window: float = 30.0) -> Progress:
        from .progress import make_progress

        return make_progress(self.console, *columns, speed_window=speed_window)

    @contextmanager
    def live(self, build: Callable[[], RenderableType],
             transient: bool = True) -> Iterator[Live]:
        """Borrow the stage's live region. The caller re-renders via ``build``.

        Transient by default: the region is scaffolding for work in progress, and
        a non-transient one leaves its last frame behind in scrollback — a stale
        bar reading "100% eta 00:00" and a second copy of whatever it was
        painting. The permanent record is the log lines above it and the summary
        below; anything worth keeping gets reprinted deliberately.
        """
        if self._live is not None:  # already inside the collapsing region
            self._live.update(build())
            yield self._live
            return
        with Live(build(), console=self.console, refresh_per_second=8,
                  transient=transient) as live:
            self._live = live
            try:
                yield live
            finally:
                self._live = None
        _mark_printed(self.console)

    # -- summary ----------------------------------------------------------

    def summary(self, text: RenderableType | str) -> None:
        self._summary = text if isinstance(text, Text) else Text(str(text), style="value")


#: How many buffered body lines a collapsing stage keeps on screen.
_COLLAPSE_LINES = 6


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
          total: int | None = None, collapse: bool = False) -> Iterator[Stage]:
    """Run a pipeline stage inside a header / body / summary frame."""
    # A transient region can't be collapsed into scrollback that isn't a screen.
    collapse = collapse and console.is_terminal
    blank(console)
    console.print(_header(console, name, index, total))
    _mark_printed(console)
    blank(console)

    st = Stage(name=name, console=console, index=index, total=total, collapse=collapse)
    try:
        if collapse:
            with Live(Text(""), console=console, refresh_per_second=8,
                      transient=True) as live:
                st._live = live
                yield st
                st._live = None
        else:
            yield st
    except BaseException:
        blank(console)
        console.print(_summary_line(console, name, Text("failed", style="stage.failed"),
                                    st.elapsed, ok=False))
        _mark_printed(console)
        blank(console)
        raise
    body = st._summary if st._summary is not None else Text("done", style="muted")
    blank(console)
    console.print(_summary_line(console, name, body, st.elapsed, ok=True))
    _mark_printed(console)
    blank(console)


def summary_card(console: Console, *, title: str,
                 rows: Sequence[tuple[str, RenderableType | str]],
                 footer: RenderableType | None = None) -> None:
    """The closing frame: label/value pairs plus an optional renderable below."""
    blank(console)
    console.print(Text(title, style="heading"))
    _mark_printed(console)
    blank(console)
    grid = table(c_text("", style="label"), c_name(""), show_header=False)
    for label, value in rows:
        grid.add_row(Text(label, style="label"),
                     value if isinstance(value, Text) else Text(str(value)))
    write(console, Group(grid, *( [Text(""), footer] if footer is not None else [])))
    blank(console)
