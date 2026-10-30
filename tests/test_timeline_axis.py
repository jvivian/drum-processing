"""Time axis tick selection, markers, and narrow-width degradation."""

import pytest

from drum_processing.manifest import Clip, Manifest, SyncResult, Take
from drum_processing.tui import timeline as tl


def _model(ref_dur: float, n: int) -> tl.TimelineModel:
    m = Manifest(session_dir=".")
    m.clips.append(Clip(name="DJI_20260915_0001_D.MP4", src_path="x",
                        duration_s=ref_dur, video_codec="hevc", width=1920,
                        height=1080, fps=50.0, avg_fps=50.0, is_vfr=False))
    m.sync.append(SyncResult(clip="DJI_20260915_0001_D.MP4", status="ok"))
    return tl.build_model(m, ref_dur, n)


def _step_for(ref_dur: float, n: int) -> int | None:
    """Re-derive the chosen tick interval from the rendered label row."""
    rows = tl.axis_rows(_model(ref_dur, n))
    if not rows:
        return None
    labels = [lbl for lbl in rows[1].plain.split(" ") if lbl]
    if len(labels) < 2:
        return None

    def secs(text: str) -> int:
        parts = [int(p) for p in text.split(":")]
        return parts[0] * 60 + parts[1] if len(parts) == 2 else \
            parts[0] * 3600 + parts[1] * 60 + parts[2]

    return secs(labels[1]) - secs(labels[0])


@pytest.mark.parametrize("ref_dur,n,expected", [
    (3600, 78, 600),
    (3600, 40, 900),
    (3600, 32, 1800),   # narrowest width that still earns a labelled axis
    (600, 78, 60),
])
def test_tick_step_scales_with_available_width(ref_dur, n, expected):
    assert _step_for(ref_dur, n) == expected


def test_axis_rows_are_exactly_bar_width():
    model = _model(2722.0, 78)
    tick_row, label_row = tl.axis_rows(model)
    assert len(tick_row.plain) == len(label_row.plain) == model.n


def test_labels_never_run_together():
    """Two abutting labels read as one wrong number."""
    _, label_row = tl.axis_rows(_model(2722.0, 78))
    for chunk in label_row.plain.split("  "):
        assert ":" not in chunk.strip() or chunk.strip().count(":") <= 2


def test_end_label_is_always_present():
    _, label_row = tl.axis_rows(_model(2722.0, 78))
    assert label_row.plain.rstrip().endswith("45:22")


def test_no_axis_when_too_narrow():
    assert tl.axis_rows(_model(2722.0, 20)) == []


def test_marker_row_places_take_indices():
    m = Manifest(session_dir=".")
    m.clips.append(Clip(name="DJI_20260915_0001_D.MP4", src_path="x",
                        duration_s=600.0, video_codec="hevc", width=1920,
                        height=1080, fps=50.0, avg_fps=50.0, is_vfr=False))
    m.sync.append(SyncResult(clip="DJI_20260915_0001_D.MP4", status="ok"))
    m.takes.append(Take(index=7, clip="DJI_20260915_0001_D.MP4",
                        start_s=100.0, end_s=300.0))
    model = tl.build_model(m, 600.0, 60)
    row = tl.marker_row(model)
    assert "7" in row.plain
    assert len(row.plain) == model.n


def test_bar_rows_are_one_cell_per_column():
    """Half-block and glyph modes must not inflate the bar's width."""
    model = _model(600.0, 50)
    for mode in (tl.BarMode.HALF, tl.BarMode.GLYPH):
        rows = tl.bar_rows(model, mode=mode)
        assert len(rows) == 1
        assert len(rows[0].plain) == model.n
    stacked = tl.bar_rows(model, mode=tl.BarMode.STACK)
    assert len(stacked) == 2
    assert all(len(r.plain) == model.n for r in stacked)


def test_clip_short_keeps_the_family_prefix():
    """The old form printed a bare `0012`, matching neither the column header nor
    the clip name in any other table."""
    assert tl.clip_short("DJI_20261011152550_0012_D.MP4") == "DJI…0012"


def test_a_take_past_the_end_of_the_reference_does_not_crash():
    """A late sync offset can place a take's audio beyond the master mix. The
    cell index has to stay inside the bar or the marker row indexes off the end."""
    m = Manifest(session_dir=".")
    m.clips.append(Clip(name="DJI_20260915_0001_D.MP4", src_path="x",
                        duration_s=600.0, video_codec="hevc", width=1920,
                        height=1080, fps=50.0, avg_fps=50.0, is_vfr=False))
    m.sync.append(SyncResult(clip="DJI_20260915_0001_D.MP4", offset_s=500.0,
                             status="ok"))
    m.takes.append(Take(index=1, clip="DJI_20260915_0001_D.MP4",
                        start_s=550.0, end_s=600.0))  # audio 1050–1100 > ref 600
    model = tl.build_model(m, 600.0, 60)
    assert max(model.starts.values()) < model.n
    assert len(tl.marker_row(model).plain) == model.n
    assert len(tl.bar_rows(model)[0].plain) == model.n
