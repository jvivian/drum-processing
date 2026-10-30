"""Layout primitives: width clamping, the table style, and the stage frame."""

import io

import pytest
import rich.box as box
from rich.cells import cell_len
from rich.console import Console

from drum_processing.tui import chrome


def _console(width: int = 100, terminal: bool = False) -> Console:
    from drum_processing.tui import theme

    return Console(file=io.StringIO(), width=width, theme=theme.THEME,
                   highlight=False, force_terminal=terminal or None,
                   color_system="truecolor" if terminal else None)


def _output(console: Console) -> str:
    return console.file.getvalue()


@pytest.mark.parametrize("width,expected", [(30, 40), (80, 78), (400, chrome.MAX_CONTENT)])
def test_content_width_clamps(width, expected):
    assert chrome.content_width(_console(width)) == expected


def test_table_uses_the_project_style():
    t = chrome.table(chrome.c_text("a"), chrome.c_num("b"))
    assert t.box is box.SIMPLE_HEAD
    assert t.show_edge is False
    assert t.pad_edge is False
    assert t.title_justify == "left"


def test_numeric_columns_are_right_aligned():
    assert chrome.c_num("fps").justify == "right"
    assert chrome.c_time("dur").justify == "right"
    assert chrome.c_text("clip").justify == "left"


def test_stage_emits_header_and_summary():
    console = _console()
    with chrome.stage(console, "sync", index=2, total=5) as st:
        st.summary("3 ok")
    out = _output(console)
    assert "sync" in out
    assert "2/5" in out
    assert "3 ok" in out


def test_stage_reports_failure_and_reraises():
    console = _console()
    with pytest.raises(RuntimeError):
        with chrome.stage(console, "mix"):
            raise RuntimeError("boom")
    assert "failed" in _output(console)


@pytest.mark.parametrize("width", [60, 100, 200, 400])
def test_stage_output_stays_inside_the_content_width(width):
    """Two things at once: no double-width glyph sneaks into the header or
    summary, and a wide terminal gets a readable measure rather than a block
    stretched to the window edge."""
    console = _console(width=width)
    with chrome.stage(console, "render", index=5, total=5) as st:
        st.item("ok", "007_t23m39s", "2:44")
        st.summary("12 rendered")
    budget = chrome.GUTTER + chrome.content_width(console)
    for line in _output(console).splitlines():
        assert cell_len(line.rstrip()) <= budget


def test_step_inside_a_pane_does_not_open_a_second_live():
    """rich allows one Live at a time and `console.status` IS a Live. Stages used
    to open their own, which would raise LiveError inside a pane — hence Stage
    owning the single region and `step` becoming a checklist row instead."""
    console = _console(terminal=True)
    ledger = chrome.Ledger()
    with chrome.stage(console, "mix", pane=True, ledger=ledger) as st:
        assert st.pane is True
        for label in ("summing stems", "normalising", "measuring"):
            with st.step(label):
                pass
        st.summary("-14.5 LUFS")
    # The pane is transient, so the summary survives only via the ledger.
    assert [e[0] for e in ledger.entries] == ["mix"]
    assert ledger.entries[0][1].plain == "-14.5 LUFS"


def test_stage_live_is_transient_by_default():
    """A non-transient Live leaves its last frame in scrollback, so the finished
    render printed its timeline twice — once as the stale final frame (complete
    with a "100% eta 00:00" bar) and once in the closing card."""
    from rich.text import Text

    console = _console(terminal=True)
    with chrome.stage(console, "render") as st:
        with st.live(lambda: Text("frame")) as live:
            assert live.transient is True


def test_live_inside_a_pane_composes_instead_of_taking_over():
    """A pane already owns the screen, so `st.live` pins the caller's renderable
    below the body rather than replacing the stepper and log above it."""
    from rich.text import Text

    console = _console(terminal=True)
    with chrome.stage(console, "render", stages=["render"], pane=True) as st:
        st.item("ok", "001_t0m17s")
        with st.live(lambda: Text("BARS")) as live:
            live.update(Text("BARS"))
            probe = _console(terminal=True)
            probe.print(st.render())
            frame = probe.file.getvalue()
        assert "001_t0m17s" in frame and "BARS" in frame
        st.summary("done")


def test_pane_is_disabled_off_a_terminal():
    """A transient pane can't be erased from scrollback that isn't a screen, so
    piped output falls all the way back to the scrolling log."""
    console = _console(terminal=False)
    with chrome.stage(console, "mix", pane=True) as st:
        assert st.pane is False
        st.line("summing stems")
    out = _output(console)
    assert "summing stems" in out and "mix" in out


def test_blank_never_doubles():
    console = _console()
    chrome.blank(console)
    chrome.blank(console)
    assert _output(console) == ""
    chrome.write(console, "x")
    chrome.blank(console)
    chrome.blank(console)
    assert _output(console).count("\n") == 2


@pytest.mark.parametrize("n,expected", [(0, "0 B"), (1536, "1.5 KB"), (5 * 1024**3, "5.0 GB")])
def test_human_bytes(n, expected):
    assert chrome.human_bytes(n) == expected


@pytest.mark.parametrize("s,expected", [(4.2, "4.2s"), (95, "1m 35s"), (3725, "1h 02m")])
def test_human_dur(s, expected):
    assert chrome.human_dur(s) == expected


def test_a_failed_stage_is_recorded_in_the_ledger():
    """A run that dies mid-pipeline has already erased its panes, so the ledger
    is the only account of the stages that did finish."""
    console = _console(terminal=True)
    ledger = chrome.Ledger()
    with chrome.stage(console, "ingest", pane=True, ledger=ledger) as st:
        st.summary("2 clips")
    with pytest.raises(RuntimeError):
        with chrome.stage(console, "sync", pane=True, ledger=ledger):
            raise RuntimeError("no stems")

    assert [(name, ok) for name, _, _, ok in ledger.entries] == [
        ("ingest", True), ("sync", False)]
    probe = _console(terminal=True)
    probe.print(chrome.ledger_table(probe, ledger))
    out = _output(probe)
    assert "ingest" in out and "sync" in out and "2 clips" in out


def test_the_failure_line_survives_the_pane():
    """The pane is erased on the way out; the failure must not go with it."""
    console = _console(terminal=True)
    with pytest.raises(RuntimeError):
        with chrome.stage(console, "mix", pane=True) as st:
            st.item("ok", "transient-body-line")
            raise RuntimeError("boom")
    assert "failed" in _output(console)
