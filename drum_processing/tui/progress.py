"""Progress bars driven by ffmpeg's ``-progress`` output.

``media.run(..., on_progress=cb)`` calls back with **output media seconds**, which
is what makes the aggregate ETA fall out for free: give the overall task a total of
``sum(take durations)`` and rich's own ``task.speed`` becomes the realtime encode
factor (media-seconds per wall-second). Since renders are strictly serial,
``TimeRemainingColumn`` then computes exactly "remaining take-seconds ÷ observed
speed" with no arithmetic of ours. The one rule: never re-send ``total`` after
``add_task`` — rich resets the task and wipes the speed history, blanking the ETA.
"""

from __future__ import annotations

from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.text import Text


class SpeedColumn(ProgressColumn):
    """``×3.2`` — media seconds encoded per wall second. Blank until known."""

    def render(self, task: Task) -> Text:
        speed = task.speed
        if not speed:
            return Text("", style="progress.data.speed")
        return Text(f"×{speed:.1f}", style="progress.data.speed")


class ElapsedColumn(ProgressColumn):
    """``12:04`` — elapsed, in the same m:ss shape as the ETA beside it.

    rich's TimeElapsedColumn renders ``0:00:00`` while TimeRemainingColumn(compact)
    renders ``--:--``; two time formats side by side on one line is exactly the
    kind of detail that reads as unconsidered.
    """

    def render(self, task: Task) -> Text:
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return Text("--:--", style="progress.elapsed")
        minutes, seconds = divmod(int(elapsed), 60)
        hours, minutes = divmod(minutes, 60)
        text = (f"{hours}:{minutes:02d}:{seconds:02d}" if hours
                else f"{minutes:02d}:{seconds:02d}")
        return Text(text, style="progress.elapsed")


def columns_item(console: Console) -> tuple[ProgressColumn, ...]:
    """The bar for the item being worked on right now."""
    return (
        TextColumn("{task.description}", style="progress.description"),
        BarColumn(bar_width=None, style="bar.back",
                  complete_style="bar.complete", finished_style="bar.finished"),
        TaskProgressColumn(),
        SpeedColumn(),
    )


def columns_total(console: Console) -> tuple[ProgressColumn, ...]:
    """The aggregate bar — the one that answers "when will this finish?"."""
    return (
        TextColumn("{task.fields[headline]}", style="strong"),
        BarColumn(bar_width=None, style="bar.back",
                  complete_style="bar.complete", finished_style="bar.finished"),
        TaskProgressColumn(),
        ElapsedColumn(),
        TextColumn("eta", style="faint"),
        TimeRemainingColumn(compact=True),
    )


def make_progress(console: Console, *columns: ProgressColumn,
                  speed_window: float = 30.0) -> Progress:
    """A Progress in the project's style.

    ``disable`` off a terminal keeps piped logs from filling with bar frames.
    A long ``speed_window`` is what makes a serial multi-minute render's ETA
    stop jittering; rich's 30 s default is tuned for fast, uniform tasks.
    """
    return Progress(
        *(columns or columns_item(console)),
        console=console,
        speed_estimate_period=speed_window,
        disable=not console.is_terminal,
    )


@contextmanager
def stage_progress(console: Console):
    """Context manager yielding a helper that runs one ffmpeg job with a bar.

    Usage::

        with stage_progress(console) as track:
            track("clip 001", total_s, lambda cb: media.run(args, total_s=total_s,
                                                             on_progress=cb))
    """
    progress = make_progress(console)

    def track(label: str, total_s: float, runner) -> None:
        task = progress.add_task(label, total=max(total_s, 0.001))
        runner(lambda done_s: progress.update(task, completed=min(done_s, total_s)))
        progress.update(task, completed=max(total_s, 0.001))

    with progress:
        yield track
