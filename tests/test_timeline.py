"""Timeline segment math + cell styling."""

from collections import Counter

from drum_processing.manifest import Clip, Manifest, SyncResult, Take
from drum_processing.tui import timeline as tl


def _manifest() -> Manifest:
    m = Manifest(session_dir=".")
    m.clips.append(Clip(
        name="DJI_20260915_0001_D.MP4", src_path="DJI_20260915_0001_D.MP4",
        duration_s=100.0, video_codec="hevc", width=1920, height=1080,
        fps=59.94, avg_fps=59.94, is_vfr=False,
    ))
    m.sync.append(SyncResult(clip="DJI_20260915_0001_D.MP4", offset_s=0.0,
                             drift_ppm=0.0, confidence=40.0, status="ok"))
    m.takes.append(Take(index=1, clip="DJI_20260915_0001_D.MP4", start_s=10.0, end_s=40.0))
    return m


def test_paint_bounds():
    styles = ["a"] * 10
    tl._paint(styles, 0.0, 0.5, "x")
    assert styles[:5] == ["x"] * 5 and styles[5:] == ["a"] * 5
    # a zero-width span still paints at least one cell
    tl._paint(styles, 0.9, 0.9, "z")
    assert "z" in styles


def test_take_and_clip_spans():
    m = _manifest()
    assert tl._take_span(m, m.takes[0]) == (10.0, 40.0)  # offset 0, no drift
    lo, hi = tl._clip_span(m, m.clips[0], m.sync[0], ref_dur=100.0)
    assert (lo, hi) == (0.0, 100.0)


def test_master_bar_styles():
    m = _manifest()
    bar = tl.master_bar(m, ref_dur=100.0, width=42)  # ~40 cells
    counts = Counter(str(sp.style) for sp in bar.spans)
    assert counts[tl.C_TAKE] > 0          # the take (10–40s)
    assert counts[tl.C_CLIP_A] > 0        # clip coverage outside the take
    assert tl.C_AUDIO not in counts        # clip covers the whole ref here


def test_master_bar_marks_rendered_and_active():
    m = _manifest()
    bar = tl.master_bar(m, 100.0, 42, rendered=frozenset({1}))
    assert tl.C_RENDERED in {str(sp.style) for sp in bar.spans}
    bar2 = tl.master_bar(m, 100.0, 42, active=1)
    assert tl.C_ACTIVE in {str(sp.style) for sp in bar2.spans}


def test_breakdown_lists_live_takes_only():
    m = _manifest()
    m.takes.append(Take(index=2, clip="DJI_20260915_0001_D.MP4",
                        start_s=50.0, end_s=60.0, dropped=True))
    table = tl.breakdown(m, 100.0)
    assert table.row_count == 1  # dropped take excluded
