"""drumprep command-line interface.

One manifest per session directory; each subcommand runs one stage over it.
``drumprep run <dir>`` walks the whole pipeline. ``--yes`` skips interactive reviews;
``--from/--to`` run a sub-range (e.g. ``--from takes`` to re-detect + re-render while
reusing the existing sync/mix intermediates).
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
    context_settings={"show_default": True},  # show option defaults in --help
)
console = Console()

STAGES = ["ingest", "sync", "mix", "takes", "render"]


def _load(session_dir: Path) -> tuple[Manifest, Config]:
    session_dir = session_dir.expanduser().resolve()
    if not session_dir.is_dir():
        console.print(f"[red]Not a directory:[/] {session_dir}")
        raise typer.Exit(1)
    return Manifest.load(session_dir), Config.load(session_dir)


def _clean_work(session_dir: Path) -> None:
    work = session_dir / "work"
    if work.is_dir():
        shutil.rmtree(work)
        console.print(f"[green]Removed[/] {work}")
    else:
        console.print("[dim]Nothing to clean.[/]")


def _ref_dur(session_dir: Path) -> float | None:
    """Studio-audio duration from the master mix, or None if not built yet."""
    import soundfile as sf

    master = session_dir / "work" / "master_mix.wav"
    if not master.is_file():
        return None
    info = sf.info(str(master))
    return info.frames / info.samplerate


def _apply_overrides(config: Config, *, max_silence: float | None = None,
                     min_take: float | None = None, bw_mode: str | None = None,
                     no_sidecars: bool = False, lufs: float | None = None) -> None:
    """Mutate a loaded Config with CLI overrides (None => leave the toml/default)."""
    if max_silence is not None:
        config.takes.max_silence_s = max_silence
    if min_take is not None:
        config.takes.min_take_s = min_take
    if bw_mode is not None:
        config.output.bw_mode = bw_mode
    if no_sidecars:
        config.output.sidecars = False
    if lufs is not None:
        config.mix.target_lufs = lufs


def _show_timeline(session_dir: Path, manifest: Manifest) -> None:
    """Static breakdown + bar — used by `status` (the bar itself is a render-time view)."""
    ref_dur = _ref_dur(session_dir)
    if ref_dur and manifest.takes:
        from .tui import timeline as tl

        tl.show_timeline(console, manifest, ref_dur)


def _show_breakdown(manifest: Manifest) -> None:
    """Just the takes-by-clip table (no timeline bar) — shown after take detection."""
    if manifest.takes:
        from .tui import timeline as tl

        console.print(tl.breakdown(manifest))


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
    """Show the current manifest as tables + the session timeline."""
    manifest, _ = _load(session_dir)
    session_dir = session_dir.expanduser().resolve()
    done = ", ".join(manifest.stages_done) or "none"
    console.rule(f"[bold]{session_dir.name}[/]  ·  stages: {done}")
    report_stage.render(manifest, console)
    _show_timeline(session_dir, manifest)


@app.command()
def clean(session_dir: Path = DirArg) -> None:
    """Delete the disposable ``work/`` intermediates (sources are never touched)."""
    _clean_work(session_dir.expanduser().resolve())


@app.command()
def sync(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the review screen."),
) -> None:
    """Align each clip to the studio audio via onset-envelope GCC-PHAT (stages 1–4)."""
    from .stages import sync as sync_stage
    from .tui import review_sync

    manifest, config = _load(session_dir)
    console.rule("[bold]Sync")
    sync_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_sync.review(session_dir, manifest, config, console)
    report_stage.render(manifest, console)


@app.command()
def mix(
    session_dir: Path = DirArg,
    lufs: float = typer.Option(None, "--lufs", help="Override integrated LUFS target."),
) -> None:
    """Build the mixed, normalized master bed (stage 5)."""
    from .stages import mix as mix_stage

    manifest, config = _load(session_dir)
    _apply_overrides(config, lufs=lufs)
    console.rule("[bold]Mix")
    mix_stage.run(session_dir, manifest, config, console)
    report_stage.render(manifest, console)


@app.command()
def takes(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the review screen."),
    max_silence: float = typer.Option(None, "--max-silence",
                                      help="Gap (s) that ends a take."),
    min_take: float = typer.Option(None, "--min-take", help="Reject takes shorter (s)."),
) -> None:
    """Detect take boundaries on the MIDI stem (stages 6 + 8)."""
    from .stages import takes as takes_stage
    from .tui import review_takes

    manifest, config = _load(session_dir)
    _apply_overrides(config, max_silence=max_silence, min_take=min_take)
    console.rule("[bold]Takes")
    takes_stage.run(session_dir, manifest, config, console)
    if not yes:
        review_takes.review(session_dir, manifest, config, console)
    _show_breakdown(manifest)


@app.command()
def render(
    session_dir: Path = DirArg,
    full: bool = typer.Option(False, "--full", help="Render whole clips, not per-take."),
    bw_mode: str = typer.Option(None, "--bw-mode",
                                help="chroma_zero | rec709_luma."),
    no_sidecars: bool = typer.Option(False, "--no-sidecars",
                                     help="Don't write per-take stem WAVs."),
) -> None:
    """Render grayscale, synced, muxed take clips (stage 9)."""
    from .stages import render as render_stage

    manifest, config = _load(session_dir)
    _apply_overrides(config, bw_mode=bw_mode, no_sidecars=no_sidecars)
    console.rule("[bold]Render")
    render_stage.run(session_dir, manifest, config, console, full_clips=full)


@app.command()
def run(
    session_dir: Path = DirArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive reviews."),
    from_stage: str = typer.Option("ingest", "--from", help=f"Start stage {STAGES}."),
    to_stage: str = typer.Option("render", "--to", help="End stage (inclusive)."),
    rm: bool = typer.Option(False, "--rm", help="Delete work/ after a successful run."),
    max_silence: float = typer.Option(None, "--max-silence", help="Take-split gap (s)."),
    min_take: float = typer.Option(None, "--min-take", help="Reject takes shorter (s)."),
    bw_mode: str = typer.Option(None, "--bw-mode", help="chroma_zero | rec709_luma."),
    no_sidecars: bool = typer.Option(False, "--no-sidecars", help="No stem sidecars."),
    lufs: float = typer.Option(None, "--lufs", help="Override integrated LUFS target."),
) -> None:
    """Walk the pipeline (or a --from/--to sub-range): ingest → sync → mix → takes → render."""
    session_dir = session_dir.expanduser().resolve()
    for name, val in (("--from", from_stage), ("--to", to_stage)):
        if val not in STAGES:
            console.print(f"[red]{name} must be one of {STAGES}[/]")
            raise typer.Exit(1)
    i0, i1 = STAGES.index(from_stage), STAGES.index(to_stage)
    if i0 > i1:
        console.print("[red]--from must not come after --to[/]")
        raise typer.Exit(1)
    active = set(STAGES[i0:i1 + 1])

    config = Config.load(session_dir)
    _apply_overrides(config, max_silence=max_silence, min_take=min_take,
                     bw_mode=bw_mode, no_sidecars=no_sidecars, lufs=lufs)

    from .stages import mix as mix_stage
    from .stages import render as render_stage
    from .stages import sync as sync_stage
    from .stages import takes as takes_stage
    from .tui import review_sync, review_takes

    if "ingest" in active:
        console.rule("[bold]Ingest")
        manifest, warnings = ingest_stage.ingest(session_dir, config)
        report_stage.render(manifest, console, warnings)
    else:
        manifest = Manifest.load(session_dir)
        if not manifest.clips:
            console.print("[red]No manifest yet — run ingest first.[/]")
            raise typer.Exit(1)

    if "sync" in active:
        console.rule("[bold]Sync")
        sync_stage.run(session_dir, manifest, config, console)
        if not yes:
            review_sync.review(session_dir, manifest, config, console)

    if "mix" in active:
        console.rule("[bold]Mix")
        mix_stage.run(session_dir, manifest, config, console)

    if "takes" in active:
        console.rule("[bold]Takes")
        takes_stage.run(session_dir, manifest, config, console)
        if not yes:
            review_takes.review(session_dir, manifest, config, console)
        _show_breakdown(manifest)

    if "render" in active:
        console.rule("[bold]Render")
        render_stage.run(session_dir, manifest, config, console)

    console.rule("[bold]Done")
    console.print(f"[green]Output in[/] {session_dir / 'out'}")
    if rm:
        _clean_work(session_dir)


if __name__ == "__main__":
    app()
