"""Configuration models for drumprep.

Config is loaded from an optional ``drumprep.toml`` in the session directory (falling
back to baked-in defaults). Everything the user might tune per-session — the mix recipe,
take-detection thresholds, output encoding — lives here as validated pydantic models.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class StemMix(BaseModel):
    """One stem's placement in the mix. Gain is *relative* balance only — absolute
    level is set by normalization, so never encode gain-staging pulls here."""

    match: str  # regex/substring matched (case-insensitive) against the stem filename
    pan: float = Field(0.0, ge=-1.0, le=1.0)  # -1 hard L .. 0 center .. +1 hard R
    gain_db: float = 0.0


class MixConfig(BaseModel):
    target_lufs: float = -14.0  # YouTube integrated target (a ceiling, not a goal)
    target_true_peak: float = -1.0  # dBTP
    sample_rate: int = 48000  # match video; stems get resampled to this
    stems: list[StemMix] = Field(
        default_factory=lambda: [
            StemMix(match="STL", pan=-0.5, gain_db=0.0),
            StemMix(match="STR", pan=0.5, gain_db=0.0),
            StemMix(match="Addictive Drums", pan=0.0, gain_db=-2.5),
        ]
    )


class TakesConfig(BaseModel):
    # Detect on the MIDI/Addictive Drums stem — it only has signal when John is
    # actually playing, unlike the room mics that pick up backing music continuously.
    # Set to None (or a match with no stem) to fall back to the summed mix.
    detect_stem_match: str | None = "Addictive Drums"
    max_silence_s: float = 20.0  # gap longer than this ends a take (keeps 15-20s song
    #                              gaps whole; a real stop splits)
    min_take_s: float = 20.0  # reject shorter detections as false positives
    pre_roll_s: float = 1.5  # lead-in kept before the first hit
    post_roll_s: float = 3.0  # let the room-mic cymbal ring out past the last MIDI note
    energy_threshold: float = 50.0  # auditok energy floor; calibrate per source


class SyncConfig(BaseModel):
    # Alignment runs on onset envelopes, not raw audio (see onset.py).
    env_hop: int = 80  # 16000/80 = 200 Hz envelope
    env_nfft: int = 1024
    window_s: float = 60.0  # per-window correlation length for the drift fit
    window_hop_s: float = 30.0
    min_confidence: float = 27.0  # per-window peak/median gate (no-match floor)
    min_inliers: int = 3  # windows agreeing on an offset before we trust a clip
    inlier_tol_s: float = 0.75  # a window is an inlier if within this of the cluster


class OutputConfig(BaseModel):
    bw_mode: str = "chroma_zero"  # "chroma_zero" | "rec709_luma" | "custom_mix"
    fps: float = 0.0  # 0 => use the source frame rate (forced CFR)
    nvenc_cq: int = 20  # h264_nvenc quality
    libx264_crf: int = 18  # fallback quality
    audio_bitrate: str = "320k"
    sidecars: bool = True  # also write per-take stem WAVs


class Config(BaseModel):
    mix: MixConfig = Field(default_factory=MixConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    takes: TakesConfig = Field(default_factory=TakesConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @classmethod
    def load(cls, session_dir: Path) -> Config:
        """Load ``<session_dir>/drumprep.toml`` if present, else return defaults."""
        toml_path = Path(session_dir) / "drumprep.toml"
        if toml_path.is_file():
            with toml_path.open("rb") as fh:
                return cls.model_validate(tomllib.load(fh))
        return cls()
