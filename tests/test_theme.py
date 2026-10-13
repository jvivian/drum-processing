"""Palette, style and status-vocabulary invariants."""

from typing import get_args

import pytest
from rich.style import Style

from drum_processing.manifest import SyncStatus
from drum_processing.tui import theme


def _luminance(hex_colour: str) -> float:
    """WCAG relative luminance."""
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


@pytest.mark.parametrize("name,value", sorted(theme.STYLES.items()))
def test_every_style_parses(name, value):
    assert Style.parse(value) is not None


def test_status_map_covers_every_sync_status():
    """The old triplicated status dicts omitted `skip` and `pending`, so a clip
    skipped in review crashed the next sync run with a KeyError."""
    assert set(theme.STATUS) == set(get_args(SyncStatus))


def test_status_text_tolerates_unknown_status():
    console = theme.build_console()
    assert theme.status_text(console, "banana").plain == "banana"


def test_timeline_ramp_is_luminance_ordered():
    """The bar must stay readable in greyscale, under CVD, and on terminals where
    the hues collapse — which only holds if the layers differ in brightness."""
    values = [_luminance(theme.PALETTE[name]) for name in theme.LUMINANCE_RAMP]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_ascii_glyphs_cover_every_unicode_glyph():
    assert set(theme.ASCII_GLYPHS) == set(theme.GLYPHS)


def test_glyphs_are_single_width():
    """rich's cell_len assumes width 1; an East-Asian Ambiguous glyph (★, ⚠) would
    silently misalign every column below it."""
    from rich.cells import cell_len

    for key, glyph in theme.GLYPHS.items():
        if key == "ellipsis":  # the only intentionally multi-char entry in ASCII
            continue
        assert cell_len(glyph) == 1, key


def test_no_color_is_detected_as_monochrome(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    console = theme.build_console()
    assert console.no_color is True
    assert theme.is_monochrome(console)


def test_piped_console_is_monochrome():
    import io

    from rich.console import Console

    assert theme.is_monochrome(Console(file=io.StringIO()))


def test_console_does_not_autohighlight():
    """highlight=True is what painted every number cyan and every `name=` yellow."""
    import io

    console = theme.build_console()
    console.file = io.StringIO()
    console._force_terminal = False
    console.print("offset=20.383s conf=48.8")
    assert "\x1b[" not in console.file.getvalue()
