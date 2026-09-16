"""Stage 10 — report: render the manifest as rich tables + QC warnings."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..manifest import Manifest


def _fmt_hms(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    h, rem = divmod(int(abs(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}" if h else f"{sign}{m}:{s:02d}"


def render(manifest: Manifest, console: Console, warnings: list[str] | None = None) -> None:
    clips = Table(title="Clips", header_style="bold cyan", expand=False)
    for col in ("clip", "codec", "res", "fps", "dur", "audio", "source"):
        clips.add_column(col)
    for c in manifest.clips:
        vfr = " [yellow]VFR[/]" if c.is_vfr else ""
        src = "[red]-converted[/]" if c.is_converted else "[green]original[/]"
        clips.add_row(
            c.name,
            c.video_codec,
            f"{c.width}x{c.height}",
            f"{c.fps:.2f}{vfr}",
            _fmt_hms(c.duration_s),
            f"{c.audio_codec or '-'} {c.audio_sample_rate or ''}",
            src,
        )
    console.print(clips)

    if manifest.stems:
        stems = Table(title="Stems", header_style="bold cyan")
        for col in ("stem", "role", "codec", "rate", "ch", "dur"):
            stems.add_column(col)
        for s in manifest.stems:
            flag = " [yellow]≠48k[/]" if s.sample_rate not in (0, 48000) else ""
            stems.add_row(s.name, s.role or "?", s.codec, f"{s.sample_rate}{flag}",
                          str(s.channels), _fmt_hms(s.duration_s))
        console.print(stems)

    if manifest.sync:
        sync = Table(title="Sync", header_style="bold cyan")
        for col in ("clip", "offset", "drift", "confidence", "status"):
            sync.add_column(col)
        for r in manifest.sync:
            mark = {"ok": "[green]✓[/]", "low_confidence": "[yellow]?[/]",
                    "no_match": "[red]✗[/]", "skip": "[dim]skip[/]",
                    "pending": "[dim]…[/]"}.get(r.status, r.status)
            sync.add_row(
                r.clip,
                _fmt_hms(r.offset_s) if r.status not in ("no_match", "pending") else "—",
                f"{r.drift_ppm:+.0f} ppm" if r.status == "ok" else "—",
                f"{r.confidence:.0f}",
                mark,
            )
        console.print(sync)

    if manifest.takes:
        takes = Table(title="Takes", header_style="bold cyan")
        for col in ("#", "clip", "start", "end", "dur", "★", "LUFS"):
            takes.add_column(col)
        for t in manifest.takes:
            if t.dropped:
                continue
            takes.add_row(
                f"{t.index:03d}", t.clip, _fmt_hms(t.start_s), _fmt_hms(t.end_s),
                _fmt_hms(t.duration_s), "★" if t.starred else "",
                f"{t.measured_lufs:.1f}" if t.measured_lufs is not None else "—",
            )
        console.print(takes)

    for w in warnings or []:
        console.print(f"[yellow]⚠[/]  {w}")
