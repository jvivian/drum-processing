"""The per-take render loop: aggregate ETA bookkeeping, bar retirement, failure
isolation. Driven with a fake renderer, so none of this needs ffmpeg."""

import io

import pytest
from rich.console import Console

from drum_processing.config import Config
from drum_processing.manifest import Clip, Manifest, SyncResult, Take
from drum_processing.stages import render as render_stage
from drum_processing.tui import chrome, theme


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=100, theme=theme.THEME,
                   highlight=False, force_terminal=True, color_system="truecolor")


@pytest.fixture
def manifest():
    m = Manifest(session_dir=".")
    m.clips.append(Clip(
        name="DJI_20260915_0001_D.MP4", src_path="DJI_20260915_0001_D.MP4",
        duration_s=600.0, video_codec="hevc", width=1920, height=1080,
        fps=59.94, avg_fps=59.94, is_vfr=False,
    ))
    m.sync.append(SyncResult(clip="DJI_20260915_0001_D.MP4", status="ok"))
    for i, (start, end) in enumerate([(10.0, 40.0), (60.0, 150.0), (200.0, 260.0)], 1):
        m.takes.append(Take(index=i, clip="DJI_20260915_0001_D.MP4",
                            start_s=start, end_s=end))
    return m


def _run(console, manifest, tmp_path, fake, config=None):
    """Drive the real path: production renders inside a pane, not a scrolling log."""
    config = config or Config()
    config.output.sidecars = False
    with chrome.stage(console, "render", stages=["render"], index=1, total=1,
                      pane=True) as st:
        return render_stage._render_takes(tmp_path, manifest, config, st,
                                          ref_dur=600.0, render=fake)


def _progress_bars(renderable):
    """Every Progress in a renderable tree."""
    from rich.console import Group
    from rich.progress import Progress

    found = []
    stack = [renderable]
    while stack:
        node = stack.pop()
        if isinstance(node, Progress):
            found.append(node)
        elif isinstance(node, Group):
            stack.extend(node.renderables)
    return found


def _reporting_fake(fractions=(0.0, 0.5, 1.0), fail_on=(), observer=None):
    """A render_segment stand-in that reports progress and can fail on demand."""
    def fake(session_dir, clip, sync, config, start_s, end_s, out_path,
             on_progress=None):
        if observer is not None:
            observer()
        index = int(out_path.stem.split("_")[0])
        if index in fail_on:
            raise RuntimeError(f"encode failed for {index}")
        duration = end_s - start_s
        for f in fractions:
            on_progress(duration * f)
        out_path.write_bytes(b"x" * 1000)
    return fake


def test_aggregate_total_is_the_sum_of_live_takes(console, manifest, tmp_path):
    manifest.takes[1].dropped = True
    outcome = _run(console, manifest, tmp_path, _reporting_fake())
    assert outcome.rendered == frozenset({1, 3})
    assert outcome.media_s == pytest.approx(30.0 + 60.0)


def test_aggregate_lands_exactly_on_total(console, manifest, tmp_path):
    outcome = _run(console, manifest, tmp_path, _reporting_fake())
    assert outcome.media_s == pytest.approx(sum(t.duration_s for t in manifest.takes))


def test_overrunning_progress_is_clamped(console, manifest, tmp_path):
    """ffmpeg's out_time can exceed -t; the aggregate must not sail past 100%."""
    fake = _reporting_fake(fractions=(0.5, 1.5))
    outcome = _run(console, manifest, tmp_path, fake)
    assert outcome.media_s == pytest.approx(sum(t.duration_s for t in manifest.takes))


def test_finished_item_bars_are_retired(console, manifest, tmp_path):
    """The live block must stay a fixed height instead of growing one bar per take.

    The old loop added a task per take and never removed one, so a long session's
    live region eventually outgrew the terminal. Sampled mid-render: never more
    than one per-item bar, and never more than one aggregate bar.
    """
    item_counts, bar_heights, frames = [], [], []
    config = Config()
    config.output.sidecars = False

    with chrome.stage(console, "render", stages=["render"], index=1, total=1,
                      pane=True) as st:
        def observe():
            # `_extra` is the live region now: the timeline plus the bars.
            bars = _progress_bars(st._extra)
            # One aggregate task plus the take currently encoding — never a
            # backlog of finished bars.
            item_counts.append(sum(len(p.tasks) for p in bars))
            bar_heights.append(chrome.measure(console, st._extra))
            frames.append(st.height())

        render_stage._render_takes(tmp_path, manifest, config, st, ref_dur=600.0,
                                   render=_reporting_fake(observer=observe))

    assert item_counts == [2, 2, 2]
    assert max(bar_heights) - min(bar_heights) <= 1  # fixed height, not growing
    # The per-take log grows, but the pane as a whole must never overrun its
    # budget — that is what the fit() clamp is for.
    assert max(frames) <= chrome.pane_budget(console)


def test_one_failed_take_does_not_abort_the_rest(console, manifest, tmp_path):
    outcome = _run(console, manifest, tmp_path, _reporting_fake(fail_on={2}))
    assert outcome.rendered == frozenset({1, 3})
    assert [i for i, _ in outcome.failed] == [2]
    assert "encode failed for 2" in outcome.failed[0][1]


def test_bytes_written_is_tallied(console, manifest, tmp_path):
    outcome = _run(console, manifest, tmp_path, _reporting_fake())
    assert outcome.bytes_written == 3000


def test_each_take_leaves_a_log_line(console, manifest, tmp_path):
    """Finished bars are retired; the record of what rendered is the log body."""
    config = Config()
    config.output.sidecars = False
    with chrome.stage(console, "render", stages=["render"], index=1, total=1,
                      pane=True) as st:
        render_stage._render_takes(tmp_path, manifest, config, st, ref_dur=600.0,
                                   render=_reporting_fake())
        body = "\n".join(t.plain for t in st._body)
    for take in manifest.takes:
        assert render_stage.take_label(take) in body


@pytest.mark.parametrize("height", [10, 18, 24, 60])
@pytest.mark.parametrize("ntakes", [3, 40])
def test_pane_never_overruns_its_budget(manifest, tmp_path, height, ntakes):
    """The property that keeps every width/height defect from coming back."""
    from drum_processing.manifest import Take

    manifest.takes = [Take(index=i, clip=manifest.clips[0].name,
                           start_s=i * 20.0, end_s=i * 20.0 + 15)
                      for i in range(1, ntakes + 1)]
    console = Console(file=io.StringIO(), width=100, height=height,
                      theme=theme.THEME, highlight=False, force_terminal=True,
                      color_system="truecolor")
    seen = []
    config = Config()
    config.output.sidecars = False
    with chrome.stage(console, "render", stages=["render"], index=1, total=1,
                      pane=True) as st:
        def observe():
            seen.append(st.height())

        render_stage._render_takes(tmp_path, manifest, config, st, ref_dur=1200.0,
                                   render=_reporting_fake(observer=observe))
    assert seen and max(seen) <= chrome.pane_budget(console)


def test_take_label_format(manifest):
    assert render_stage.take_label(manifest.takes[1]) == "002_t1m00s"
