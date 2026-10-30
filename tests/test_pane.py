"""Pane primitives: height measurement, truncation, and the stage stepper."""

import io

import pytest
from rich.console import Console, Group
from rich.text import Text

from drum_processing.tui import chrome, theme

STAGES = ["ingest", "sync", "mix", "takes", "render"]


def _console(width: int = 100, height: int = 24) -> Console:
    return Console(file=io.StringIO(), width=width, height=height,
                   theme=theme.THEME, highlight=False, force_terminal=True,
                   color_system="truecolor")


def _rows(n: int) -> Group:
    return Group(*[Text(f"row {i:02d}") for i in range(n)])


# ---- measure / fit ------------------------------------------------------

def test_measure_counts_true_height_not_terminal_height():
    """The console is 24 rows; a 40-row renderable must still measure 40."""
    console = _console(height=24)
    assert chrome.measure(console, _rows(40)) == 40


def test_fit_passes_through_when_it_already_fits():
    console = _console()
    body = _rows(5)
    assert chrome.fit(console, body, 10) is body


@pytest.mark.parametrize("budget", [3, 8, 12, 20])
def test_fit_clamps_to_exactly_the_budget(budget):
    console = _console()
    fitted = chrome.fit(console, _rows(40), budget)
    assert chrome.measure(console, fitted) == budget


def test_fit_head_keeps_the_top():
    """A table must keep its header, so head-mode drops the tail."""
    console = _console()
    probe = _console()
    probe.print(chrome.fit(console, _rows(40), 10))
    out = probe.file.getvalue()
    assert "row 00" in out and "row 39" not in out


def test_fit_tail_keeps_the_newest():
    """A growing log must keep its tail. Head-mode here would pin the render
    pane to takes 1-7 for the rest of the run."""
    console = _console()
    probe = _console()
    probe.print(chrome.fit(console, _rows(40), 10, keep="tail"))
    out = probe.file.getvalue()
    assert "row 39" in out and "row 00" not in out
    assert "earlier" in out


@pytest.mark.parametrize("keep", ["head", "tail"])
def test_fit_clamps_in_both_directions(keep):
    console = _console()
    assert chrome.measure(console, chrome.fit(console, _rows(40), 9, keep=keep)) == 9


@pytest.mark.parametrize("width", [20, 40, 42, 100, 102, 400])
def test_pane_width_is_the_painted_width(width):
    """Measuring at console.width overcounts: the gutter takes two columns, and a
    frozen line wider than the terminal wraps as a row nobody counted."""
    from rich.text import Text as T

    console = _console(width=width)
    w = chrome.pane_width(console)
    assert chrome.measure(console, T("x" * w)) == 1
    assert chrome.measure(console, T("x" * (w + 1))) == 2


def test_fit_reports_how_much_it_dropped():
    console = _console()
    probe = _console()
    probe.print(chrome.fit(console, _rows(40), 10))
    assert "+31 more" in probe.file.getvalue()


def test_fit_survives_a_degenerate_budget():
    console = _console()
    for budget in (0, 1, 2):
        fitted = chrome.fit(console, _rows(40), budget)
        assert chrome.measure(console, fitted) <= 2


def test_fit_preserves_a_table_header():
    console = _console()
    table = chrome.table(chrome.c_name("clip"), chrome.c_num("#"))
    for i in range(30):
        table.add_row(f"DJI…{i:04d}", str(i))
    probe = _console()
    probe.print(chrome.fit(console, table, 8))
    out = probe.file.getvalue()
    assert "clip" in out and "+" in out


# ---- budget -------------------------------------------------------------

@pytest.mark.parametrize("height,expected", [(40, 37), (24, 21), (12, 9), (8, 6)])
def test_pane_budget_tracks_height_and_floors(height, expected):
    assert chrome.pane_budget(_console(height=height)) == expected


# ---- stepper ------------------------------------------------------------

def test_stepper_marks_prior_stages_done():
    console = _console()
    assert chrome.stepper(console, STAGES, 3).plain.count(theme.GLYPHS["ok"]) == 3


def test_stepper_lists_every_stage_in_order():
    plain = chrome.stepper(_console(), STAGES, 2).plain
    positions = [plain.index(name) for name in STAGES]
    assert positions == sorted(positions)


@pytest.mark.parametrize("width", [60, 100, 200])
def test_stepper_fits_the_content_width(width):
    from rich.cells import cell_len

    console = _console(width=width)
    assert cell_len(chrome.stepper(console, STAGES, 0).plain) <= chrome.content_width(console)


def test_stepper_emphasises_exactly_one_stage():
    console = _console()
    styles = [str(span.style) for span in chrome.stepper(console, STAGES, 2).spans]
    assert styles.count("stage.name") == 1


# ---- which end of a clamped body survives -------------------------------

def _plain(console, renderable) -> str:
    lines = console.render_lines(renderable, console.options.update(height=None),
                                 pad=False)
    return "\n".join("".join(seg.text for seg in line) for line in lines)


def _stage(console, **kw):
    return chrome.stage(console, "takes", stages=STAGES, index=4, total=5,
                        pane=True, **kw)


def test_a_log_body_keeps_its_newest_rows():
    """Head-keeping here would pin the render pane to the first few takes and
    show those for the rest of the run."""
    console = _console(height=14)
    with _stage(console) as st:
        for i in range(40):
            st.item("ok", f"take-{i:03d}")
        assert st._keep == "tail"
        body = _plain(console, st.render())
    assert "take-039" in body and "take-000" not in body


def test_a_titled_block_keeps_its_header():
    """`block` appends, but what it appends is a table — tail-keeping would
    scroll the header off."""
    console = _console(height=14)
    table = chrome.table(chrome.c_name("source clip"), chrome.c_num("#"))
    for i in range(40):
        table.add_row(f"DJI…{i:04d}", str(i))
    with _stage(console) as st:
        st.line("detecting on MIDI stem: x.wav")
        st.block("takes by clip", table)
        assert st._keep == "head"
        body = _plain(console, st.render())
    assert "takes by clip" in body and "source clip" in body


@pytest.mark.parametrize("height", [10, 14, 24, 60])
def test_a_pane_never_exceeds_its_budget(height):
    console = _console(height=height)
    with _stage(console) as st:
        for i in range(60):
            st.item("ok", f"take-{i:03d}")
            assert st.height() <= chrome.pane_budget(console)
