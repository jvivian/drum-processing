"""Stage 0 — ingest: discover media in the session dir, probe it, build the manifest.

Operates *in place*: the session directory already holds the DJI clips and stem WAVs
(they can be 10 GB apiece, so we never copy). We only ever read the sources and write
under ``work/`` and ``out/``. Paths in the manifest are relative to the session dir.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Config
from ..manifest import Clip, Manifest, Stem
from ..media import probe

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}
AUDIO_EXTS = {".wav", ".flac", ".mp3", ".aif", ".aiff"}
CONVERTED_RE = re.compile(r"-converted", re.IGNORECASE)
# DJI clip id, e.g. DJI_20260913150350_0054 — used to pair originals with -converted.
DJI_ID_RE = re.compile(r"(DJI_\d+_\d+)", re.IGNORECASE)


def _clip_key(path: Path) -> str:
    """A key that pairs an original with its -converted derivative."""
    m = DJI_ID_RE.search(path.name)
    if m:
        return m.group(1).upper()
    return CONVERTED_RE.sub("", path.stem).lower()


def _assign_role(name: str, config: Config) -> str | None:
    lname = name.lower()
    for stem in config.mix.stems:
        if stem.match.lower() in lname:
            return stem.match
    m = re.search(r"-(ST[LR]|AD2|OH|ROOM)\b", name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def ingest(session_dir: Path, config: Config) -> tuple[Manifest, list[str]]:
    """Build (or rebuild) the manifest for ``session_dir``. Returns the manifest and
    a list of human-readable warnings for the report."""
    session_dir = Path(session_dir)
    warnings: list[str] = []

    (session_dir / "work" / "scratch").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(parents=True, exist_ok=True)

    files = [p for p in session_dir.iterdir() if p.is_file()]
    stems_dir = session_dir / "stems"
    if stems_dir.is_dir():
        files += [p for p in stems_dir.iterdir() if p.is_file()]

    video_files = [p for p in files if p.suffix.lower() in VIDEO_EXTS]
    audio_files = [p for p in files if p.suffix.lower() in AUDIO_EXTS]

    # Pair originals with -converted derivatives; prefer the original.
    by_key: dict[str, list[Path]] = {}
    for p in video_files:
        by_key.setdefault(_clip_key(p), []).append(p)

    clips: list[Clip] = []
    for _key, group in sorted(by_key.items()):
        originals = [p for p in group if not CONVERTED_RE.search(p.name)]
        chosen = min(originals or group, key=lambda p: p.name)
        is_converted = CONVERTED_RE.search(chosen.name) is not None
        if is_converted:
            warnings.append(
                f"{chosen.name}: only a -converted (likely downscaled) copy found; "
                "the HEVC original is the master — stop deleting originals."
            )
        pr = probe(chosen)
        if pr.is_vfr:
            warnings.append(
                f"{chosen.name}: variable frame rate detected — output is forced to CFR."
            )
        clips.append(
            Clip(
                name=chosen.name,
                src_path=str(chosen.relative_to(session_dir)),
                duration_s=pr.duration_s,
                video_codec=pr.video_codec,
                width=pr.width,
                height=pr.height,
                fps=pr.fps,
                avg_fps=pr.avg_fps,
                is_vfr=pr.is_vfr,
                rotation=pr.rotation,
                audio_codec=pr.audio_codec,
                audio_sample_rate=pr.audio_sample_rate,
                is_converted=is_converted,
            )
        )

    stems: list[Stem] = []
    for p in sorted(audio_files):
        pr = probe(p)
        role = _assign_role(p.name, config)
        if pr.audio_sample_rate and pr.audio_sample_rate != config.mix.sample_rate:
            warnings.append(
                f"{p.name}: {pr.audio_sample_rate} Hz — will resample to "
                f"{config.mix.sample_rate} Hz for the mix."
            )
        if p.suffix.lower() == ".mp3":
            warnings.append(
                f"{p.name}: lossy MP3 stem — render WAV from REAPER for full quality."
            )
        stems.append(
            Stem(
                name=p.name,
                src_path=str(p.relative_to(session_dir)),
                role=role,
                duration_s=pr.duration_s,
                sample_rate=pr.audio_sample_rate or 0,
                channels=pr.audio_channels or 0,
                codec=pr.audio_codec or "",
            )
        )

    if not clips:
        warnings.append("No video clips found in the session directory.")
    if not stems:
        warnings.append("No studio stems found — mix and take-detection need them.")

    manifest = Manifest(session_dir=str(session_dir), clips=clips, stems=stems)
    manifest.mark_done("ingest")
    manifest.save(session_dir)
    return manifest, warnings
