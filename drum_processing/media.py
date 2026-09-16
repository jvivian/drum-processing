"""ffmpeg / ffprobe helpers: probing, running with progress, encoder detection.

All ffmpeg work goes through here so the stages stay declarative. We shell out to
the ``ffmpeg``/``ffprobe`` on PATH (the conda-forge build in the drumprep env).
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@dataclass
class ProbeResult:
    duration_s: float
    video_codec: str
    width: int
    height: int
    fps: float  # r_frame_rate (nominal)
    avg_fps: float  # avg_frame_rate
    rotation: int
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_bits: int | None

    @property
    def is_vfr(self) -> bool:
        # A meaningful gap between nominal and average frame rate implies VFR.
        if not self.fps or not self.avg_fps:
            return False
        return abs(self.fps - self.avg_fps) / self.fps > 0.001


def _rate(value: str) -> float:
    """Parse an ffprobe rate like '60000/1001' into a float."""
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, den = value.split("/")
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(value)


def probe(path: Path) -> ProbeResult:
    """ffprobe a media file. Selects the primary video stream (``v:0``) and skips
    cover-art / MJPEG-thumbnail streams that DJI files carry."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # First real (non-thumbnail) video stream. DJI files carry an mjpeg thumbnail;
    # its disposition has attached_pic=1, or it is simply an mjpeg codec.
    def is_thumbnail(s: dict) -> bool:
        return s.get("codec_name") == "mjpeg" or s.get("disposition", {}).get(
            "attached_pic", 0
        ) == 1

    vstreams = [
        s for s in streams if s.get("codec_type") == "video" and not is_thumbnail(s)
    ]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    v = vstreams[0] if vstreams else {}
    a = astreams[0] if astreams else None

    rotation = 0
    for tag_src in (v.get("side_data_list", []) or []):
        if "rotation" in tag_src:
            rotation = int(tag_src["rotation"])

    return ProbeResult(
        duration_s=float(fmt.get("duration", 0.0) or 0.0),
        video_codec=v.get("codec_name", ""),
        width=int(v.get("width", 0) or 0),
        height=int(v.get("height", 0) or 0),
        fps=_rate(v.get("r_frame_rate", "0/0")),
        avg_fps=_rate(v.get("avg_frame_rate", "0/0")),
        rotation=rotation,
        audio_codec=a.get("codec_name") if a else None,
        audio_sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        audio_channels=int(a["channels"]) if a and a.get("channels") else None,
        audio_bits=int(a["bits_per_raw_sample"])
        if a and a.get("bits_per_raw_sample")
        else None,
    )


@functools.lru_cache(maxsize=1)
def has_nvenc() -> bool:
    """True if the ffmpeg on PATH exposes h264_nvenc *and* it actually initializes.

    Listing the encoder is not enough — WSL2 without GPU passthrough lists it but
    fails at runtime, so we do a tiny null-encode probe.
    """
    listed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
    )
    if "h264_nvenc" not in listed.stdout:
        return False
    test = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
            "-c:v", "h264_nvenc", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    return test.returncode == 0


_TIME_RE = re.compile(r"out_time_ms=(\d+)")


def run(
    args: Sequence[str],
    *,
    total_s: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Run an ffmpeg command. If ``on_progress`` is given, ``-progress pipe:1`` is
    parsed and the callback receives seconds-processed as they arrive.

    Callers pass the ffmpeg args *without* the leading ``ffmpeg``.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if on_progress is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)

    if on_progress is None:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg failed:\n{' '.join(cmd)}\n{proc.stderr[-2000:]}")
        return

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _TIME_RE.search(line)
        if m:
            on_progress(int(m.group(1)) / 1_000_000.0)
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise FFmpegError(f"ffmpeg failed:\n{' '.join(cmd)}\n{err[-2000:]}")
    if on_progress and total_s:
        on_progress(total_s)  # ensure the bar completes


def decode_mono(src: Path, dst: Path, *, sample_rate: int, start: float = 0.0,
                duration: float | None = None) -> None:
    """Decode an input to mono PCM WAV at ``sample_rate`` — used for correlation
    scratch tracks and analysis feeds."""
    args: list[str] = []
    if start:
        args += ["-ss", f"{start}"]
    args += ["-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration}"]
    args += ["-ac", "1", "-ar", str(sample_rate), "-vn", str(dst)]
    run(args)
