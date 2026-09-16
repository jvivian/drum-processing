"""Stage 8 — interactive take review: drop, merge, rename, star before rendering."""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.console import Console
from rich.table import Table

from ..config import Config
from ..manifest import Manifest


def _fmt(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def _table(console: Console, manifest: Manifest) -> None:
    t = Table(title="Take review", header_style="bold cyan")
    for col in ("#", "clip", "start", "end", "dur", "★"):
        t.add_column(col)
    for tk in manifest.takes:
        if tk.dropped:
            continue
        short = tk.clip.split("_")[-2] if "_" in tk.clip else tk.clip
        t.add_row(f"{tk.index:03d}", short, _fmt(tk.start_s), _fmt(tk.end_s),
                  _fmt(tk.duration_s), "★" if tk.starred else "")
    console.print(t)


def review(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    while True:
        _table(console, manifest)
        action = questionary.select(
            "Take review",
            choices=["render all", "drop a take", "toggle ★", "merge two takes", "quit"],
        ).ask()
        if action in (None, "render all"):
            break
        if action == "quit":
            raise SystemExit(0)

        live = [tk for tk in manifest.takes if not tk.dropped]
        if action == "merge two takes":
            picks = questionary.checkbox(
                "Pick exactly two adjacent takes to merge",
                choices=[questionary.Choice(f"{tk.index:03d} {_fmt(tk.start_s)}-{_fmt(tk.end_s)}",
                                            value=tk.index) for tk in live],
            ).ask() or []
            chosen = [tk for tk in live if tk.index in picks]
            if len(chosen) == 2 and chosen[0].clip == chosen[1].clip:
                a, b = sorted(chosen, key=lambda x: x.start_s)
                a.end_s = b.end_s
                b.dropped = True
                manifest.save(session_dir)
            else:
                console.print("[red]Pick two takes from the same clip.[/]")
            continue

        idx = questionary.select(
            "Which take?",
            choices=[questionary.Choice(f"{tk.index:03d} {_fmt(tk.start_s)}-{_fmt(tk.end_s)}",
                                        value=tk.index) for tk in live],
        ).ask()
        if idx is None:
            continue
        take = next(tk for tk in manifest.takes if tk.index == idx)
        if action == "drop a take":
            take.dropped = True
        elif action == "toggle ★":
            take.starred = not take.starred
        manifest.save(session_dir)

    manifest.save(session_dir)
