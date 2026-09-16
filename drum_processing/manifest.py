"""The session manifest — ``drumprep.json``, the single source of truth.

Every stage reads the manifest, does its work, and writes results back. Nothing
recomputes what a prior stage already recorded, so any stage is independently
re-runnable and the whole pipeline is resumable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MANIFEST_NAME = "drumprep.json"
SchemaVersion = 1


class Clip(BaseModel):
    """A DJI video clip discovered during ingest."""

    name: str  # basename
    src_path: str  # path relative to session dir (under src/)
    duration_s: float
    video_codec: str
    width: int
    height: int
    fps: float  # nominal (r_frame_rate)
    avg_fps: float  # avg_frame_rate; differs from fps => VFR
    is_vfr: bool
    rotation: int = 0
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    is_converted: bool = False  # a VLC/-converted derivative, not an original


class Stem(BaseModel):
    """A studio stem WAV rendered from REAPER."""

    name: str
    src_path: str
    role: str | None = None  # STL / STR / AD2 / etc. (matched by suffix)
    duration_s: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""


SyncStatus = Literal["pending", "ok", "low_confidence", "no_match", "skip"]


class SyncResult(BaseModel):
    """Where a clip sits on the studio-audio timeline (stage 3)."""

    clip: str  # clip name this result belongs to
    offset_s: float = 0.0  # audio time at which the clip's t=0 lands
    drift_ppm: float = 0.0  # clock-drift slope; correct via setpts=PTS/(1+b)
    confidence: float = 0.0  # peak / median of the correlation function
    residual_ms: float = 0.0  # drift-fit residual (QC)
    status: SyncStatus = "pending"


class Take(BaseModel):
    """A detected take (stage 6)."""

    index: int
    clip: str  # which clip this take is cut from
    start_s: float  # start within the clip (pre-roll applied)
    end_s: float  # end within the clip (post-roll applied)
    starred: bool = False
    measured_lufs: float | None = None
    dropped: bool = False

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class MixResult(BaseModel):
    master_path: str = ""  # relative path to work/master_mix.wav
    measured_lufs: float | None = None
    measured_true_peak: float | None = None


class Manifest(BaseModel):
    schema_version: int = SchemaVersion
    session_dir: str
    stages_done: list[str] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    stems: list[Stem] = Field(default_factory=list)
    sync: list[SyncResult] = Field(default_factory=list)
    mix: MixResult = Field(default_factory=MixResult)
    takes: list[Take] = Field(default_factory=list)

    # ---- persistence ---------------------------------------------------

    @classmethod
    def path_for(cls, session_dir: Path) -> Path:
        return Path(session_dir) / MANIFEST_NAME

    @classmethod
    def load(cls, session_dir: Path) -> Manifest:
        p = cls.path_for(session_dir)
        if p.is_file():
            return cls.model_validate_json(p.read_text())
        return cls(session_dir=str(session_dir))

    def save(self, session_dir: Path) -> None:
        """Atomic write: dump to a temp file, then replace."""
        p = self.path_for(session_dir)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=2))
        os.replace(tmp, p)

    # ---- convenience ---------------------------------------------------

    def mark_done(self, stage: str) -> None:
        if stage not in self.stages_done:
            self.stages_done.append(stage)

    def sync_for(self, clip_name: str) -> SyncResult | None:
        return next((s for s in self.sync if s.clip == clip_name), None)

    def clip(self, name: str) -> Clip | None:
        return next((c for c in self.clips if c.name == name), None)
