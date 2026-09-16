"""rich progress bars driven by ffmpeg's ``-progress`` output.

``media.run(..., on_progress=cb)`` calls back with seconds-processed; we translate
that into a task bar advanced against the probed duration.
"""

from __future__ import annotations

from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)


@contextmanager
def stage_progress(console: Console, description: str):
    """Context manager yielding a helper that runs one ffmpeg job with a bar.

    Usage::

        with stage_progress(console, "Rendering") as track:
            track("clip 001", total_s, lambda cb: media.run(args, total_s=total_s,
                                                             on_progress=cb))
    """
    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    def track(label: str, total_s: float, runner) -> None:
        task = progress.add_task(label, total=max(total_s, 0.001))
        runner(lambda done_s: progress.update(task, completed=min(done_s, total_s)))
        progress.update(task, completed=max(total_s, 0.001))

    with progress:
        yield track
