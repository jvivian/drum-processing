"""Stage 10 — report: render the manifest as tables + QC warnings.

Pure view. Everything here goes through ``chrome`` for layout and ``theme`` for
colour, so there is no styling decision in this file — only which facts to show.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..manifest import Manifest
from ..tui import chrome, theme
from ..tui.timeline import clip_short

ALL_SECTIONS = ("clips", "stems", "sync", "takes")


def _fmt_hms(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    h, rem = divmod(int(abs(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}" if h else f"{sign}{m}:{s:02d}"


def _fps(value: float) -> str:
    """Drop a meaningless ``.00`` — 100 fps is not 100.00 fps."""
    return f"{value:.0f}" if abs(value - round(value)) < 0.005 else f"{value:.2f}"


def clips_table(manifest: Manifest) -> Table:
    # Only the filename is a `c_name`, so a narrow terminal truncates that
    # (recoverable) instead of turning "original" into "origin" or "10:00" into
    # "10:…" — see the column factories in chrome.
    t = chrome.table(
        chrome.c_name("clip"), chrome.c_text("codec"), chrome.c_text("res"),
        chrome.c_num("fps"), chrome.c_time("dur"), chrome.c_text("audio"),
        chrome.c_text("source", style="provenance"),
    )
    for c in manifest.clips:
        fps = Text(_fps(c.fps), style="value")
        if c.is_vfr:
            fps.append(" VFR", style="warn")
        rate = f" {c.audio_sample_rate}" if c.audio_sample_rate else ""
        t.add_row(c.name, c.video_codec, f"{c.width}x{c.height}", fps,
                  _fmt_hms(c.duration_s), f"{c.audio_codec or '-'}{rate}",
                  # Provenance, not a verdict: a converted clip is a fact about
                  # where the file came from, not an error to flag in red.
                  "converted" if c.is_converted else "original")
    return t


def stems_table(manifest: Manifest) -> Table:
    t = chrome.table(
        chrome.c_name("stem"), chrome.c_text("role"), chrome.c_text("codec"),
        chrome.c_num("rate"), chrome.c_num("ch"), chrome.c_time("dur"),
    )
    for s in manifest.stems:
        rate = Text(str(s.sample_rate), style="value")
        if s.sample_rate not in (0, 48000):
            rate.append(" ≠48k", style="warn")
        t.add_row(s.name, s.role or "?", s.codec, rate, str(s.channels),
                  _fmt_hms(s.duration_s))
    return t


def sync_table(manifest: Manifest, console: Console) -> Table:
    t = chrome.table(
        chrome.c_name("clip"), chrome.c_time("offset"), chrome.c_num("drift"),
        chrome.c_num("resid"), chrome.c_num("conf"), chrome.c_text("status"),
    )
    for r in manifest.sync:
        known = r.status not in ("no_match", "pending")
        t.add_row(
            clip_short(r.clip),
            _fmt_hms(r.offset_s) if known else "—",
            f"{r.drift_ppm:+.0f} ppm" if r.status == "ok" else "—",
            f"{r.residual_ms:.0f} ms" if r.status == "ok" else "—",
            f"{r.confidence:.1f}",
            theme.status_text(console, r.status, label=True),
        )
    return t


def takes_table(manifest: Manifest) -> Table:
    g = theme.GLYPHS
    t = chrome.table(
        chrome.c_num("#"), chrome.c_name("clip"), chrome.c_time("start"),
        chrome.c_time("end"), chrome.c_time("dur"),
        chrome.c_flag(g["star"], style="star"), chrome.c_num("LUFS"),
    )
    for tk in manifest.takes:
        if tk.dropped:
            continue
        t.add_row(f"{tk.index:03d}", clip_short(tk.clip), _fmt_hms(tk.start_s),
                  _fmt_hms(tk.end_s), _fmt_hms(tk.duration_s),
                  g["star"] if tk.starred else "",
                  f"{tk.measured_lufs:.1f}" if tk.measured_lufs is not None else "—")
    return t


def render(manifest: Manifest, console: Console, warnings: list[str] | None = None,
           sections: tuple[str, ...] = ALL_SECTIONS) -> None:
    """Print the requested manifest sections.

    ``sections`` keeps a full run from printing the same four tables after every
    stage — each stage shows only what it changed.
    """
    if "clips" in sections and manifest.clips:
        chrome.section(console, "clips", clips_table(manifest))
    if "stems" in sections and manifest.stems:
        chrome.section(console, "stems", stems_table(manifest))
    if "sync" in sections and manifest.sync:
        chrome.section(console, "sync", sync_table(manifest, console))
    if "takes" in sections and manifest.takes:
        chrome.section(console, "takes", takes_table(manifest))

    for w in warnings or []:
        chrome.write(console, Text.assemble((f"{theme.GLYPHS['warn']} ", "warn"),
                                            (w, "value")))


def ingest_body(manifest: Manifest, warnings: list[str] | None = None):
    """Everything ingest found, as one renderable for a pane."""
    from rich.console import Group

    parts = []
    if manifest.clips:
        parts += [Text("clips", style="section"), clips_table(manifest)]
    if manifest.stems:
        if parts:
            parts.append(Text(""))
        parts += [Text("stems", style="section"), stems_table(manifest)]
    for w in warnings or []:
        parts.append(Text.assemble((f"{theme.GLYPHS['warn']} ", "warn"), (w, "value")))
    return Group(*parts)


def probe_progress(done: int, total: int, name: str) -> Text:
    """``probing 3/6  DJI_…0025_D.MP4`` — ffprobe gives no sub-file progress,
    so the honest unit is files."""
    text = Text("probing ", style="label")
    text.append(f"{done}/{total}", style="metric")
    text.append(f"  {name}", style="muted")
    return text


# ---- one-line stage summaries -------------------------------------------

def _counts(text: list[tuple[str, str]]) -> Text:
    """``3 clips · 3 stems`` — the dot separator is the only joiner we use."""
    out = Text()
    for i, (value, label) in enumerate(text):
        if i:
            out.append(f"  {theme.GLYPHS['sep']}  ", style="faint")
        out.append(value, style="metric")
        out.append(f" {label}", style="muted")
    return out


def ingest_summary(manifest: Manifest, warnings: list[str] | None = None) -> Text:
    rows = [(str(len(manifest.clips)), "clips"), (str(len(manifest.stems)), "stems")]
    if warnings:
        rows.append((str(len(warnings)), "warnings"))
    return _counts(rows)


def sync_summary(manifest: Manifest) -> Text:
    tally: dict[str, int] = {}
    for r in manifest.sync:
        tally[r.status] = tally.get(r.status, 0) + 1
    # Ordered by severity (the STATUS declaration order), not alphabetically, so
    # "2 ok · 1 review" reads as a verdict rather than a sorted list.
    rows = [(str(tally[status]), theme.STATUS[status][2])
            for status in theme.STATUS if status in tally]
    rows += [(str(count), status) for status, count in tally.items()
             if status not in theme.STATUS]
    return _counts(rows) if rows else Text("nothing to sync", style="muted")


def mix_summary(manifest: Manifest, config) -> Text:
    mix = manifest.mix
    if mix.measured_lufs is None:
        return Text("no mix", style="muted")
    out = Text()
    out.append(f"{mix.measured_lufs:.1f}", style="metric")
    out.append(" LUFS", style="unit")
    if mix.measured_true_peak is not None:
        out.append(f"  {theme.GLYPHS['sep']}  ", style="faint")
        out.append(f"{mix.measured_true_peak:.2f}", style="metric")
        out.append(" dBFS peak", style="unit")
    out.append(f"   target {config.mix.target_lufs} / {config.mix.target_true_peak}",
               style="faint")
    return out


def takes_summary(manifest: Manifest) -> Text:
    live = [t for t in manifest.takes if not t.dropped]
    total = sum(t.duration_s for t in live)
    clips = len({t.clip for t in live})
    return _counts([(str(len(live)), "takes"), (str(clips), "clips"),
                    (_fmt_hms(total), "total")])


def render_summary(outcome) -> Text:
    """``12 rendered · 1 failed · 42.1 MB``."""
    if outcome is None:
        return Text("nothing rendered", style="muted")
    rows = [(str(len(outcome.rendered)), "rendered")]
    if outcome.failed:
        rows.append((str(len(outcome.failed)), "failed"))
    rows.append((chrome.human_bytes(outcome.bytes_written), ""))
    return _counts(rows)


def closing(console: Console, session_dir: Path, manifest: Manifest, *,
            elapsed_s: float, stages: list[str],
            ref_dur: float | None = None, outcome=None,
            ledger: chrome.Ledger | None = None,
            warnings: list[str] | None = None) -> None:
    """The final frame. The end of a run is what it is remembered by.

    With panes the middle of the run is erased as it goes, so the ledger is the
    only surviving account of what each stage did — it leads the card for that
    reason, above the run-level totals.
    """
    from ..tui import timeline as tl

    out_dir = Path(session_dir) / "out"
    size, files = chrome.dir_size(out_dir)
    live = [t for t in manifest.takes if not t.dropped]

    rows: list[tuple[str, Text | str]] = []
    if ledger is None or not ledger.entries:
        rows.append(("stages", "  ".join(stages)))
    if live:
        rows.append(("takes", _counts([(str(len(live)), "kept"),
                                       (_fmt_hms(sum(t.duration_s for t in live)),
                                        "runtime")])))
    if outcome is not None and outcome.failed:
        rows.append(("failed", Text(
            ", ".join(f"{i:03d}" for i, _ in outcome.failed), style="error")))
    if files:
        rows.append(("output", _counts([(str(files), "files"),
                                        (chrome.human_bytes(size), "")])))
    rows.append(("elapsed", chrome.human_dur(elapsed_s)))
    rows.append(("where", Text(str(out_dir), style="path")))

    footer = None
    if ref_dur and live:
        rendered = frozenset(outcome.rendered) if outcome else frozenset()
        footer = tl.master_panel(manifest, ref_dur, console.width,
                                 rendered, console=console, show_markers=False)
    for w in warnings or []:
        rows.append(("", Text.assemble((f"{theme.GLYPHS['warn']} ", "warn"), (w, "warn"))))
    header = chrome.ledger_table(console, ledger) if ledger and ledger.entries else None
    chrome.summary_card(console, title="done", rows=rows, footer=footer, header=header)
