"""Stage 1 — reference: sum the studio stems into a mono 16 kHz correlation bed.

The stems are sample-locked (rendered over identical REAPER bounds), so a plain
sum reconstructs the studio timeline. Downsampling to 16 kHz keeps GCC-PHAT cheap;
drums have ample energy below 8 kHz.
"""

from __future__ import annotations

from pathlib import Path

from ..manifest import Manifest
from ..media import run

CORRELATION_FS = 16000
REF_REL = "work/ref_mix.wav"


def ensure_reference(session_dir: Path, manifest: Manifest) -> Path:
    """Build ``work/ref_mix.wav`` if missing; return its path."""
    session_dir = Path(session_dir)
    ref_path = session_dir / REF_REL
    if ref_path.is_file():
        return ref_path
    if not manifest.stems:
        raise RuntimeError("No stems to build a correlation reference from.")

    n = len(manifest.stems)
    args: list[str] = []
    for stem in manifest.stems:
        args += ["-i", str(session_dir / stem.src_path)]
    if n > 1:
        # amix auto-connects all unlabeled inputs; normalize=0 keeps the raw sum.
        args += ["-filter_complex", f"amix=inputs={n}:normalize=0[a]", "-map", "[a]"]
    args += ["-ac", "1", "-ar", str(CORRELATION_FS), str(ref_path)]
    run(args)
    return ref_path
