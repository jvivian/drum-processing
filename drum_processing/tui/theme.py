"""The one place colour, glyphs and status vocabulary are defined.

Two rules keep the output looking authored rather than accidental:

1. ``build_console`` sets ``highlight=False``. rich's ReprHighlighter otherwise
   paints every ``name=`` yellow and every number cyan in printed strings, while
   table cells (which bypass it) stay plain — so the same value gets two
   different treatments on one screen.
2. ``STYLES`` overrides rich's *own* default style names alongside ours. rich
   ships a neon default theme (hot-pink progress bars, magenta percentages,
   bright-green rules); overriding the names here retints everything at once,
   including renderables we never touch directly.

Palette notes: values must be literal colours — a Theme value cannot name another
Theme entry (rich raises ``StyleSyntaxError``). The timeline ramp is ordered by
*luminance*, not hue, so the bar still reads correctly in greyscale, under the
common colour-vision deficiencies, and on a 16-colour terminal where the hues
collapse. ``tests/test_theme.py`` asserts that ordering.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

PALETTE: dict[str, str] = {
    # text ramp
    "ink_strong": "#E6E9EE",
    "ink": "#C3C8D0",
    "ink_dim": "#8A9099",
    "ink_faint": "#6C7079",
    "rule": "#3A3F46",
    # semantic state (Okabe-Ito derived, desaturated)
    "accent": "#4FA3C7",
    "ok": "#5FA886",
    "warn": "#C8963E",
    "error": "#C2654A",
    # timeline layers, in ascending luminance
    "void": "#2E3238",
    "film_b": "#474C54",
    "film_a": "#5A5F68",
    "axis": "#565B63",
    "take": "#5A7FA6",
    "active": "#D7A64A",
    "done": "#7FC3A3",
}

#: Timeline layers that must stay luminance-ordered for the bar to survive
#: greyscale / CVD / 16-colour rendering.
LUMINANCE_RAMP = ("void", "film_b", "film_a", "take", "active", "done")

STYLES: dict[str, str] = {
    # ---- text ----------------------------------------------------------
    "body": PALETTE["ink"],
    "muted": PALETTE["ink_dim"],
    "faint": PALETTE["ink_faint"],
    "strong": f"bold {PALETTE['ink_strong']}",
    "heading": f"bold {PALETTE['ink_strong']}",
    "label": PALETTE["ink_dim"],
    "value": PALETTE["ink"],
    "metric": f"bold {PALETTE['ink']}",
    "unit": PALETTE["ink_faint"],
    "path": PALETTE["accent"],
    # ---- state ---------------------------------------------------------
    "ok": PALETTE["ok"],
    "warn": PALETTE["warn"],
    "error": f"bold {PALETTE['error']}",
    "skip": PALETTE["ink_faint"],
    "pending": PALETTE["ink_faint"],
    "provenance": PALETTE["ink_dim"],  # `-converted` is metadata, not a failure
    "star": PALETTE["warn"],
    # ---- chrome --------------------------------------------------------
    "rule": PALETTE["rule"],
    "stage.name": f"bold {PALETTE['ink_strong']}",
    "stage.count": PALETTE["ink_faint"],
    "stage.done": PALETTE["ok"],
    "stage.failed": f"bold {PALETTE['error']}",
    "section": f"bold {PALETTE['ink_dim']}",
    # ---- rich's own defaults (the real noise source) --------------------
    "table.header": f"bold {PALETTE['ink_dim']}",
    "table.title": f"bold {PALETTE['ink_strong']}",  # drops the default italic
    "table.caption": PALETTE["ink_faint"],
    "table.border": PALETTE["rule"],
    "bar.back": PALETTE["void"],
    "bar.complete": PALETTE["take"],
    "bar.finished": PALETTE["done"],
    "bar.pulse": PALETTE["take"],
    "progress.description": PALETTE["ink"],
    "progress.percentage": PALETTE["ink_dim"],
    "progress.remaining": PALETTE["ink_dim"],
    "progress.elapsed": PALETTE["ink_faint"],
    "progress.data.speed": PALETTE["ink_dim"],
    "status.spinner": PALETTE["accent"],
    "rule.line": PALETTE["rule"],
    "rule.text": PALETTE["ink_dim"],
    "prompt": PALETTE["ink"],
    "prompt.choices": PALETTE["accent"],
    "prompt.default": PALETTE["ink_dim"],
    "prompt.invalid": PALETTE["error"],
    # belt-and-braces alongside highlight=False
    "repr.number": PALETTE["ink"],
    "repr.str": PALETTE["ink"],
    "repr.path": PALETTE["accent"],
    "repr.filename": PALETTE["accent"],
    "repr.none": PALETTE["ink_faint"],
    # ---- timeline layers ------------------------------------------------
    "tl.void": PALETTE["void"],
    "tl.film.a": PALETTE["film_a"],
    "tl.film.b": PALETTE["film_b"],
    "tl.take": PALETTE["take"],
    "tl.active": PALETTE["active"],
    "tl.done": PALETTE["done"],
    "tl.axis": PALETTE["axis"],
    "tl.tick": PALETTE["ink_faint"],
    "tl.marker": PALETTE["ink_dim"],
    "tl.boundary": PALETTE["void"],
}

THEME = Theme(STYLES, inherit=True)

# Glyphs deliberately avoid U+2605 BLACK STAR and U+26A0 WARNING SIGN: the first is
# East-Asian *Ambiguous* width and the second has emoji-presentation ambiguity.
# rich's cell_len assumes width 1 for both, so a double-wide render silently
# misaligns every column below them. Everything here is unambiguous-width.
GLYPHS: dict[str, str] = {
    "ok": "✓",
    "warn": "!",
    "error": "✗",
    "skip": "–",
    "pending": "·",
    "star": "*",
    "step": "›",
    "stage": "▎",
    "tick": "┬",
    "boundary": "▏",
    "cell": "▄",
    "full": "█",
    "shade_mid": "▒",
    "shade_lo": "░",
    "dot": "·",
    "dash": "─",
    "sep": "·",
    "ellipsis": "…",
    "range": "–",
}

ASCII_GLYPHS: dict[str, str] = {
    "ok": "ok",
    "warn": "!",
    "error": "x",
    "skip": "-",
    "pending": ".",
    "star": "*",
    "step": ">",
    "stage": "|",
    "tick": "+",
    "boundary": "|",
    "cell": "=",
    "full": "#",
    "shade_mid": "-",
    "shade_lo": ".",
    "dot": ".",
    "dash": "-",
    "sep": "-",
    "ellipsis": "...",
    "range": "-",
}

#: status -> (glyph key, style name, short label). Keyed by exactly the values of
#: ``manifest.SyncStatus``; ``tests/test_theme.py`` locks that correspondence so a
#: new status can't silently go unhandled the way ``skip``/``pending`` used to.
STATUS: dict[str, tuple[str, str, str]] = {
    "ok": ("ok", "ok", "ok"),
    "low_confidence": ("warn", "warn", "review"),
    "no_match": ("error", "error", "no match"),
    "skip": ("skip", "skip", "skipped"),
    "pending": ("pending", "pending", "pending"),
}


def glyphs(console: Console) -> dict[str, str]:
    """Unicode glyphs, or the ASCII fallback when the terminal can't be trusted."""
    if console.options.legacy_windows:
        return ASCII_GLYPHS
    encoding = (getattr(console.file, "encoding", None) or "utf-8").lower()
    if "utf" not in encoding:
        return ASCII_GLYPHS
    return GLYPHS


def is_monochrome(console: Console) -> bool:
    """Whether colour can carry meaning here.

    Both halves matter: ``NO_COLOR=1`` sets ``no_color`` but leaves ``color_system``
    at "256", while a pipe sets ``color_system`` to None without touching
    ``no_color``. "standard" (16 colours) counts as monochrome because the palette's
    mid-tones all collapse onto the same ANSI index.
    """
    return bool(console.no_color) or console.color_system in (None, "standard")


def status_text(console: Console, status: str, *, label: bool = False) -> Text:
    """``✓`` or ``✓ ok``. An unrecognised status renders rather than raising."""
    entry = STATUS.get(status)
    if entry is None:
        return Text(status, style="warn")
    glyph_key, style, short = entry
    text = Text(glyphs(console)[glyph_key], style=style)
    if label:
        text.append(f" {short}", style=style)
    return text


def build_console(*, stderr: bool = False, display: Any | None = None) -> Console:
    """The project's Console. Every print site goes through one of these.

    ``display`` is an optional object carrying a ``width`` override (the future
    ``[display]`` config section); anything without that attribute is ignored.
    """
    return Console(
        theme=THEME,
        highlight=False,
        emoji=False,  # don't let `:foo:` in a filename become a pictograph
        soft_wrap=False,
        stderr=stderr,
        width=getattr(display, "width", None) or None,
    )


def questionary_style(console: Console):
    """questionary's defaults clash with ours (its `answer` is bright orange).

    prompt_toolkit does its own colour-depth detection and ignores ``NO_COLOR``,
    so in monochrome mode we hand back an empty style rather than letting it
    reintroduce colour we just suppressed.
    """
    import questionary

    if is_monochrome(console):
        return questionary.Style([])
    return questionary.Style([
        ("qmark", f"fg:{PALETTE['accent']}"),
        ("question", f"fg:{PALETTE['ink_strong']} bold"),
        ("answer", f"fg:{PALETTE['accent']}"),
        ("pointer", f"fg:{PALETTE['accent']} bold"),
        ("highlighted", f"fg:{PALETTE['ink_strong']} bold"),
        ("selected", f"fg:{PALETTE['ok']}"),
        ("separator", f"fg:{PALETTE['rule']}"),
        ("instruction", f"fg:{PALETTE['ink_faint']}"),
        ("text", f"fg:{PALETTE['ink']}"),
        ("disabled", f"fg:{PALETTE['ink_faint']} italic"),
    ])
