"""drumprep command-line interface.

One manifest per session directory; each subcommand runs one stage over it.
``drumprep run <dir>`` walks the whole pipeline. ``--yes`` skips interactive reviews;
``--from/--to`` run a sub-range (e.g. ``--from takes`` to re-detect + re-render while
reusing the existing sync/mix intermediates).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import typer
from rich.text import Text

from .config import Config
from .manifest import Manifest
from .stages import ingest as ingest_stage
from .stages import report as report_stage
from .tui import chrome, theme

app = typer.Typer(
    help="Turn a raw drum session into synced, mixed, grayscale take clips.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"show_default": True},  # show option defaults in --help
)
console = theme.build_console()
err = theme.build_console(stderr=True)

STAGES = ["ingest", "sync", "mix", "takes", "render"]


def _fail(message: str) -> typer.Exit:
    err.print(Text(message, style="error"))
    return typer.Exit(1)


def _load(session_dir: Path) -> tuple[Manifest, Config]:
    session_dir = session_dir.expanduser().resolve()
    if not session_dir.is_dir():
        raise _fail(f"Not a directory: {session_dir}")
    return Manifest.load(session_dir), Config.load(session_dir)


def _clean_work(session_dir: Path) -> None:
    work = session_dir / "work"
    if work.is_dir():
        shutil.rmtree(work)
        chrome.write(console, Text.assemble(("removed  ", "label"), (str(work), "path")))
    else:
        chrome.write(console, Text("Nothing to clean.", style="muted"))


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
                     stems: bool | None = None, lufs: float | None = None) -> None:
    """Mutate a loaded Config with CLI overrides (None => leave the toml/default)."""
    if max_silence is not None:
        config.takes.max_silence_s = max_silence
    if min_take is not None:
        config.takes.min_take_s = min_take
    if bw_mode is not None:
        config.output.bw_mode = bw_mode
    if stems is not None:
        config.output.sidecars = stems
    if lufs is not None:
        config.mix.target_lufs = lufs


def _show_timeline(session_dir: Path, manifest: Manifest) -> None:
    """Static bar + breakdown — used by `status`."""
    ref_dur = _ref_dur(session_dir)
    if ref_dur and manifest.takes:
        from .tui import timeline as tl

        tl.show_timeline(console, manifest, ref_dur)


DirArg = typer.Argument(..., help="Session directory containing clips + stems.")
StemsOpt = typer.Option(None, "--stems/--no-stems",
                        help="Also write per-take stem WAVs beside each MP4 (for remixing).")


@app.command()
def ingest(session_dir: Path = DirArg) -> None:
    """Discover + probe media, build the manifest (stage 0)."""
    session_dir = session_dir.expanduser().resolve()
    config = Config.load(session_dir)
    with chrome.stage(console, "ingest") as st:
        manifest, warnings = ingest_stage.ingest(session_dir, config)
        report_stage.render(manifest, console, warnings, sections=("clips", "stems"))
        st.summary(report_stage.ingest_summary(manifest, warnings))


@app.command()
def status(session_dir: Path = DirArg) -> None:
    """Show the current manifest as tables + the session timeline."""
    manifest, _ = _load(session_dir)
    session_dir = session_dir.expanduser().resolve()
    done = "  ".join(manifest.stages_done) or "none"
    chrome.blank(console)
    console.print(Text.assemble((session_dir.name, "heading"),
                                ("    stages: ", "label"), (done, "value")))
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
    with chrome.stage(console, "sync") as st:
        sync_stage.run(session_dir, manifest, config, st)
        st.summary(report_stage.sync_summary(manifest))
    if not yes:
        review_sync.review(session_dir, manifest, config, console)
        report_stage.render(manifest, console, sections=("sync",))


@app.command()
def mix(
    session_dir: Path = DirArg,
    lufs: float = typer.Option(None, "--lufs", help="Override integrated LUFS target."),
) -> None:
    """Build the mixed, normalized master bed (stage 5)."""
    from .stages import mix as mix_stage

    manifest, config = _load(session_dir)
    _apply_overrides(config, lufs=lufs)
    with chrome.stage(console, "mix", collapse=True) as st:
        mix_stage.run(session_dir, manifest, config, st)
        st.summary(report_stage.mix_summary(manifest, config))


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
    from .tui import timeline as tl

    manifest, config = _load(session_dir)
    _apply_overrides(config, max_silence=max_silence, min_take=min_take)
    with chrome.stage(console, "takes") as st:
        takes_stage.run(session_dir, manifest, config, st)
        st.block("takes by clip", tl.breakdown(manifest))
        st.summary(report_stage.takes_summary(manifest))
    if not yes:
        review_takes.review(session_dir, manifest, config, console)


@app.command()
def render(
    session_dir: Path = DirArg,
    full: bool = typer.Option(False, "--full", help="Render whole clips, not per-take."),
    bw_mode: str = typer.Option(None, "--bw-mode",
                                help="chroma_zero | rec709_luma."),
    stems: bool | None = StemsOpt,
) -> None:
    """Render grayscale, synced, muxed take clips (stage 9)."""
    from .stages import render as render_stage

    session_dir = session_dir.expanduser().resolve()
    manifest, config = _load(session_dir)
    _apply_overrides(config, bw_mode=bw_mode, stems=stems)
    t0 = time.monotonic()
    with chrome.stage(console, "render") as st:
        outcome = render_stage.run(session_dir, manifest, config, st, full_clips=full)
        st.summary(report_stage.render_summary(outcome))
    # The live timeline is transient, so the closing card is where the finished
    # one gets printed — same as `run`.
    report_stage.closing(console, session_dir, manifest,
                         elapsed_s=time.monotonic() - t0, stages=["render"],
                         ref_dur=_ref_dur(session_dir), outcome=outcome)


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
    stems: bool | None = StemsOpt,
    lufs: float = typer.Option(None, "--lufs", help="Override integrated LUFS target."),
) -> None:
    """Walk the pipeline (or a --from/--to sub-range): ingest → sync → mix → takes → render."""
    session_dir = session_dir.expanduser().resolve()
    for name, val in (("--from", from_stage), ("--to", to_stage)):
        if val not in STAGES:
            raise _fail(f"{name} must be one of {STAGES}")
    i0, i1 = STAGES.index(from_stage), STAGES.index(to_stage)
    if i0 > i1:
        raise _fail("--from must not come after --to")
    active = STAGES[i0:i1 + 1]
    total = len(active)
    t_run = time.monotonic()

    config = Config.load(session_dir)
    _apply_overrides(config, max_silence=max_silence, min_take=min_take,
                     bw_mode=bw_mode, stems=stems, lufs=lufs)

    from .stages import mix as mix_stage
    from .stages import render as render_stage
    from .stages import sync as sync_stage
    from .stages import takes as takes_stage
    from .tui import review_sync, review_takes
    from .tui import timeline as tl

    step = iter(range(1, total + 1))
    outcome = None

    if "ingest" in active:
        with chrome.stage(console, "ingest", index=next(step), total=total) as st:
            manifest, warnings = ingest_stage.ingest(session_dir, config)
            report_stage.render(manifest, console, warnings, sections=("clips", "stems"))
            st.summary(report_stage.ingest_summary(manifest, warnings))
    else:
        manifest = Manifest.load(session_dir)
        if not manifest.clips:
            raise _fail("No manifest yet — run ingest first.")

    if "sync" in active:
        with chrome.stage(console, "sync", index=next(step), total=total) as st:
            sync_stage.run(session_dir, manifest, config, st)
            st.summary(report_stage.sync_summary(manifest))
        # Reviews sit outside the stage frame: prompt_toolkit draws its own screen
        # and will not cooperate with a live region.
        if not yes:
            review_sync.review(session_dir, manifest, config, console)

    if "mix" in active:
        with chrome.stage(console, "mix", index=next(step), total=total,
                          collapse=True) as st:
            mix_stage.run(session_dir, manifest, config, st)
            st.summary(report_stage.mix_summary(manifest, config))

    if "takes" in active:
        with chrome.stage(console, "takes", index=next(step), total=total) as st:
            takes_stage.run(session_dir, manifest, config, st)
            st.block("takes by clip", tl.breakdown(manifest))
            st.summary(report_stage.takes_summary(manifest))
        if not yes:
            review_takes.review(session_dir, manifest, config, console)

    if "render" in active:
        with chrome.stage(console, "render", index=next(step), total=total) as st:
            outcome = render_stage.run(session_dir, manifest, config, st)
            st.summary(report_stage.render_summary(outcome))

    report_stage.closing(console, session_dir, manifest,
                         elapsed_s=time.monotonic() - t_run, stages=active,
                         ref_dur=_ref_dur(session_dir), outcome=outcome)
    if rm:
        _clean_work(session_dir)


if __name__ == "__main__":
    app()
