"""CLI stage-range validation and config overrides."""

from typer.testing import CliRunner

from drum_processing.cli import STAGES, _apply_overrides, app
from drum_processing.config import Config

runner = CliRunner()


def test_stages_order():
    assert STAGES == ["ingest", "sync", "mix", "takes", "render"]


def test_invalid_from_stage(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path), "--from", "bogus"])
    assert result.exit_code == 1


def test_from_after_to_rejected(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path), "--from", "render", "--to", "sync"])
    assert result.exit_code == 1


def test_apply_overrides():
    cfg = Config()
    _apply_overrides(cfg, max_silence=12.0, min_take=15.0, bw_mode="rec709_luma",
                     no_sidecars=True, lufs=-16.0)
    assert cfg.takes.max_silence_s == 12.0
    assert cfg.takes.min_take_s == 15.0
    assert cfg.output.bw_mode == "rec709_luma"
    assert cfg.output.sidecars is False
    assert cfg.mix.target_lufs == -16.0


def test_apply_overrides_noop_keeps_defaults():
    cfg = Config()
    _apply_overrides(cfg)  # all None/False
    assert cfg.takes.max_silence_s == 20.0
    assert cfg.output.sidecars is True
