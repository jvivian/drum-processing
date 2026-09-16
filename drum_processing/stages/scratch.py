"""Stage 2 — scratch: extract each clip's camera audio to mono 16 kHz for correlation."""

from __future__ import annotations

from pathlib import Path

from ..manifest import Clip, Manifest
from ..media import decode_mono
from .reference import CORRELATION_FS


def scratch_path(session_dir: Path, clip: Clip) -> Path:
    return Path(session_dir) / "work" / "scratch" / f"{Path(clip.name).stem}.wav"


def ensure_scratch(session_dir: Path, clip: Clip) -> Path:
    """Decode ``clip``'s camera audio to a mono 16 kHz scratch WAV if missing."""
    session_dir = Path(session_dir)
    dst = scratch_path(session_dir, clip)
    if dst.is_file():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    decode_mono(session_dir / clip.src_path, dst, sample_rate=CORRELATION_FS)
    return dst


def ensure_all(session_dir: Path, manifest: Manifest) -> None:
    for clip in manifest.clips:
        ensure_scratch(session_dir, clip)
