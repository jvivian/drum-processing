"""Stage 8 — interactive take review: drop, merge, rename, star before rendering."""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..manifest import Manifest
from . import chrome, theme
from .timeline import clip_short

STAR = theme.GLYPHS["star"]
TOGGLE_STAR = f"toggle {STAR}"


def _fmt(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def build_table(console: Console, manifest: Manifest) -> Table:
    """The review table. Returns rather than prints, so it can be tested."""
    t = chrome.table(
        chrome.c_num("#"), chrome.c_text("clip"), chrome.c_time("start"),
        chrome.c_time("end"), chrome.c_time("dur"),
        chrome.c_flag(STAR, style="star"),
    )
    for tk in manifest.takes:
        if tk.dropped:
            continue
        t.add_row(f"{tk.index:03d}", clip_short(tk.clip), _fmt(tk.start_s),
                  _fmt(tk.end_s), _fmt(tk.duration_s), STAR if tk.starred else "")
    return t


def review(session_dir: Path, manifest: Manifest, config: Config, console: Console) -> None:
    session_dir = Path(session_dir)
    style = theme.questionary_style(console)
    qmark = theme.glyphs(console)["step"]
    while True:
        chrome.section(console, "take review", build_table(console, manifest))
        chrome.blank(console)
        action = questionary.select(
            "Take review",
            choices=["render all", "drop a take", TOGGLE_STAR, "merge two takes", "quit"],
            style=style, qmark=qmark,
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
                style=style, qmark=qmark,
            ).ask() or []
            chosen = [tk for tk in live if tk.index in picks]
            if len(chosen) == 2 and chosen[0].clip == chosen[1].clip:
                a, b = sorted(chosen, key=lambda x: x.start_s)
                a.end_s = b.end_s
                b.dropped = True
                manifest.save(session_dir)
            else:
                chrome.write(console, Text("Pick two takes from the same clip.",
                                           style="error"))
            continue

        idx = questionary.select(
            "Which take?",
            choices=[questionary.Choice(f"{tk.index:03d} {_fmt(tk.start_s)}-{_fmt(tk.end_s)}",
                                        value=tk.index) for tk in live],
            style=style, qmark=qmark,
        ).ask()
        if idx is None:
            continue
        take = next(tk for tk in manifest.takes if tk.index == idx)
        if action == "drop a take":
            take.dropped = True
        elif action == TOGGLE_STAR:
            take.starred = not take.starred
        manifest.save(session_dir)

    manifest.save(session_dir)
