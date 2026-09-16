"""drumprep command-line interface.

One manifest per session directory; each subcommand runs one stage over it.
``drumprep run <dir>`` walks the whole pipeline. ``--yes`` skips interactive reviews.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console

from .config import Config
from .manifest import Manifest
from .stages import ingest as ingest_stage
from .stages import report as report_stage

app = typer.Typer(
    help="Turn a raw drum session into synced, mixed, grayscale take clips.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _load(session_dir: Path) -> tuple[Manifest, Config]:
    session_dir = session_dir.expanduser().resolve()
    if not session_dir.is_dir():
        console.print(f"[red]Not a directory:[/] {session_dir}")
        raise typer.Exit(1)
    return Manifest.load(session_dir), Config.load(session_dir)


DirArg = typer.Argument(..., help="Session directory containing clips + stems.")


@app.command()
def ingest(session_dir: Path = DirArg) -> None:
    """Discover + probe media, build the manifest (stage 0)."""
    session_dir = session_dir.expanduser().resolve()
    config = Config.load(session_dir)
    console.rule("[bold]Ingest")
    manifest, warnings = ingest_stage.ingest(session_dir, config)
    report_stage.render(manifest, console, warnings)


@app.command()
def status(session_dir: Path = DirArg) -> None:
    """Show the current manifest as tables."""
    manifest, _ = _load(session_dir)
    done = ", ".join(manifest.stages_done) or "none"
    console.rule(f"[bold]{session_dir.name}[/]  ·  stages: {done}")
    report_stage.render(manifest, console)


@app.command()
def clean(session_dir: Path = DirArg) -> None:
    """Delete the disposable ``work/`` intermediates (sources are never touched)."""
    session_dir = session_dir.expanduser().resolve()
    work = session_dir / "work"
    if work.is_dir():
        shutil.rmtree(work)
        console.print(f"[green]Removed[/] {work}")
    else:
        console.print("[dim]Nothing to clean.[/]")


@app.command()
def sync(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the review screen."),
) -> None:
    """Align each clip to the studio audio via GCC-PHAT (stages 1–4)."""
    from .stages import sync as sync_stage
    from .tui import review_sync

    manifest, config = _load(session_dir)
    console.rule("[bold]Sync")
    sync_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_sync.review(session_dir, manifest, config, console)
    report_stage.render(manifest, console)


@app.command()
def mix(session_dir: Path = DirArg) -> None:
    """Build the mixed, normalized master bed (stage 5)."""
    from .stages import mix as mix_stage

    manifest, config = _load(session_dir)
    console.rule("[bold]Mix")
    mix_stage.run(session_dir, manifest, config, console)
    report_stage.render(manifest, console)


@app.command()
def takes(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the review screen."),
) -> None:
    """Detect take boundaries on the mix (stages 6 + 8)."""
    from .stages import takes as takes_stage
    from .tui import review_takes

    manifest, config = _load(session_dir)
    console.rule("[bold]Takes")
    takes_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_takes.review(session_dir, manifest, config, console)
    report_stage.render(manifest, console)


@app.command()
def render(
    session_dir: Path = DirArg,
    full: bool = typer.Option(False, "--full", help="Render whole clips, not per-take."),
) -> None:
    """Render grayscale, synced, muxed take clips (stage 9)."""
    from .stages import render as render_stage

    manifest, config = _load(session_dir)
    console.rule("[bold]Render")
    render_stage.run(session_dir, manifest, config, console, full_clips=full)
    report_stage.render(manifest, console)


@app.command()
def run(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive reviews."),
) -> None:
    """Walk the whole pipeline: ingest → sync → mix → takes → render → report."""
    session_dir = session_dir.expanduser().resolve()
    config = Config.load(session_dir)
    from .stages import mix as mix_stage
    from .stages import render as render_stage
    from .stages import sync as sync_stage
    from .stages import takes as takes_stage
    from .tui import review_sync, review_takes

    console.rule("[bold]Ingest")
    manifest, warnings = ingest_stage.ingest(session_dir, config)
    report_stage.render(manifest, console, warnings)

    console.rule("[bold]Sync")
    sync_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_sync.review(session_dir, manifest, config, console)

    console.rule("[bold]Mix")
    mix_stage.run(session_dir, manifest, config, console)

    console.rule("[bold]Takes")
    takes_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_takes.review(session_dir, manifest, config, console)

    console.rule("[bold]Render")
    render_stage.run(session_dir, manifest, config, console)

    console.rule("[bold]Done")
    report_stage.render(manifest, console)
    console.print(f"\n[green]Takes written to[/] {session_dir / 'out'}")


if __name__ == "__main__":
    app()
