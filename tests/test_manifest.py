"""Manifest and config round-trip / persistence tests."""

from pathlib import Path

from drum_processing.config import Config
from drum_processing.manifest import Clip, Manifest, SyncResult, Take


def test_manifest_roundtrip(tmp_path: Path):
    m = Manifest(session_dir=str(tmp_path))
    m.clips.append(Clip(
        name="DJI_0001.MP4", src_path="DJI_0001.MP4", duration_s=100.0,
        video_codec="hevc", width=1920, height=1080, fps=59.94, avg_fps=59.94,
        is_vfr=False, audio_codec="aac", audio_sample_rate=48000,
    ))
    m.sync.append(SyncResult(clip="DJI_0001.MP4", offset_s=12.5, drift_ppm=-40.0,
                             confidence=42.0, status="ok"))
    m.takes.append(Take(index=1, clip="DJI_0001.MP4", start_s=1.0, end_s=30.0, starred=True))
    m.mark_done("ingest")
    m.save(tmp_path)

    loaded = Manifest.load(tmp_path)
    assert loaded.clips[0].video_codec == "hevc"
    assert loaded.sync_for("DJI_0001.MP4").offset_s == 12.5
    assert loaded.clip("DJI_0001.MP4").width == 1920
    assert loaded.takes[0].starred is True
    assert loaded.takes[0].duration_s == 29.0
    assert "ingest" in loaded.stages_done


def test_mark_done_is_idempotent(tmp_path: Path):
    m = Manifest(session_dir=str(tmp_path))
    m.mark_done("sync")
    m.mark_done("sync")
    assert m.stages_done.count("sync") == 1


def test_config_defaults_and_load(tmp_path: Path):
    cfg = Config.load(tmp_path)  # no toml => defaults
    assert cfg.mix.target_lufs == -14.0
    assert {s.match for s in cfg.mix.stems} == {"STL", "STR", "Addictive Drums"}

    (tmp_path / "drumprep.toml").write_text(
        "[mix]\ntarget_lufs = -16.0\n[takes]\nmin_take_s = 30.0\n"
    )
    cfg2 = Config.load(tmp_path)
    assert cfg2.mix.target_lufs == -16.0
    assert cfg2.takes.min_take_s == 30.0
