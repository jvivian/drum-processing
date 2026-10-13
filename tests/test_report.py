"""Manifest tables: no leaked markup, right-aligned figures, honest colour."""

import io

import pytest
from rich.console import Console

from drum_processing.config import Config
from drum_processing.manifest import Clip, Manifest, MixResult, Stem, SyncResult, Take
from drum_processing.stages import report
from drum_processing.tui import theme


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=100, theme=theme.THEME, highlight=False)


@pytest.fixture
def manifest():
    m = Manifest(session_dir=".")
    m.clips.append(Clip(
        name="DJI_20261011152550_0012_D.MP4", src_path="x", duration_s=598.0,
        video_codec="hevc", width=1920, height=1080, fps=100.0, avg_fps=100.0,
        is_vfr=False, audio_codec="aac", audio_sample_rate=48000, is_converted=True,
    ))
    m.stems.append(Stem(name="2026-10-11-STL.wav", src_path="x", role="STL",
                        duration_s=2722.0, sample_rate=48000, channels=2,
                        codec="pcm_s24le"))
    m.sync.append(SyncResult(clip="DJI_20261011152550_0012_D.MP4", offset_s=20.383,
                             drift_ppm=-192.0, residual_ms=2.0, confidence=48.8,
                             status="ok"))
    m.takes.append(Take(index=1, clip="DJI_20261011152550_0012_D.MP4",
                        start_s=88.0, end_s=256.0, measured_lufs=-14.2))
    m.mix = MixResult(master_path="work/master_mix.wav", measured_lufs=-14.5,
                      measured_true_peak=-0.9)
    return m


def test_render_leaks_no_markup(console, manifest):
    report.render(manifest, console)
    out = console.file.getvalue()
    assert "[/" not in out and "[bold" not in out


def test_numeric_columns_are_right_aligned(manifest):
    for column in report.clips_table(manifest).columns:
        if column.header in ("fps", "dur"):
            assert column.justify == "right"
    for column in report.takes_table(manifest).columns:
        if column.header in ("#", "start", "end", "dur", "LUFS"):
            assert column.justify == "right"


def test_converted_is_provenance_not_an_error(manifest):
    """A converted clip is a fact about where the file came from. Red is the most
    expensive signal available and should not be spent on metadata."""
    column = next(c for c in report.clips_table(manifest).columns
                  if c.header == "source")
    assert column.style == "provenance"
    assert "converted" in str(column._cells[0])


def test_sections_filter(console, manifest):
    report.render(manifest, console, sections=("clips",))
    out = console.file.getvalue()
    assert "clips" in out
    assert "stems" not in out and "takes" not in out


def test_meaningless_decimals_are_dropped(manifest):
    assert report._fps(100.0) == "100"
    assert report._fps(59.94) == "59.94"


def test_vfr_and_rate_flags_still_surface(console, manifest):
    manifest.clips[0].is_vfr = True
    manifest.stems[0].sample_rate = 44100
    report.render(manifest, console, sections=("clips", "stems"))
    out = console.file.getvalue()
    assert "VFR" in out and "≠48k" in out


def test_summaries_are_one_line(console, manifest):
    config = Config()
    for text in (report.ingest_summary(manifest), report.sync_summary(manifest),
                 report.mix_summary(manifest, config), report.takes_summary(manifest)):
        assert "\n" not in text.plain


def test_sync_summary_uses_the_shared_status_labels(manifest):
    manifest.sync.append(SyncResult(clip="other", status="skip"))
    assert "skipped" in report.sync_summary(manifest).plain


def test_closing_degrades_without_a_mix(console, manifest, tmp_path):
    """`--to sync` has no master mix, so there is no timeline to draw."""
    report.closing(console, tmp_path, manifest, elapsed_s=12.0,
                   stages=["ingest", "sync"], ref_dur=None)
    out = console.file.getvalue()
    assert "done" in out and "elapsed" in out
