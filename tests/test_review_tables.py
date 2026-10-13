"""Review screens share the project's status vocabulary and table style."""

import io

import pytest
from rich.console import Console

from drum_processing.manifest import Clip, Manifest, SyncResult, Take
from drum_processing.tui import review_sync, review_takes, theme


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=100, theme=theme.THEME, highlight=False)


@pytest.fixture
def manifest():
    m = Manifest(session_dir=".")
    m.clips.append(Clip(name="DJI_20261011152550_0012_D.MP4", src_path="x",
                        duration_s=598.0, video_codec="hevc", width=1920,
                        height=1080, fps=100.0, avg_fps=100.0, is_vfr=False))
    m.takes.append(Take(index=1, clip="DJI_20261011152550_0012_D.MP4",
                        start_s=88.0, end_s=256.0, starred=True))
    m.takes.append(Take(index=2, clip="DJI_20261011152550_0012_D.MP4",
                        start_s=300.0, end_s=400.0, dropped=True))
    return m


@pytest.mark.parametrize("status", sorted(theme.STATUS))
def test_sync_review_handles_every_status(console, manifest, status):
    """`sync` and the review screen kept separate status maps, and the sync one
    was missing `skip`/`pending` — so skipping a clip in review then re-running
    sync raised KeyError."""
    manifest.sync.append(SyncResult(clip="DJI_20261011152550_0012_D.MP4",
                                    status=status))
    table = review_sync.build_table(console, manifest)
    assert table.row_count == 1


def test_take_review_excludes_dropped(console, manifest):
    assert review_takes.build_table(console, manifest).row_count == 1


def test_review_tables_use_the_shared_style(console, manifest):
    import rich.box as box

    manifest.sync.append(SyncResult(clip="DJI_20261011152550_0012_D.MP4", status="ok"))
    for table in (review_sync.build_table(console, manifest),
                  review_takes.build_table(console, manifest)):
        assert table.box is box.SIMPLE_HEAD
        assert table.show_edge is False


def test_star_glyph_is_single_width():
    """The star doubles as a menu choice label, so the two must stay in step."""
    from rich.cells import cell_len

    assert cell_len(review_takes.STAR) == 1
    assert review_takes.STAR in review_takes.TOGGLE_STAR
