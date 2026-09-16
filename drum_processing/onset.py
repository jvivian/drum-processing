"""Onset-strength (spectral-flux) envelopes for cross-device audio alignment.

Raw-waveform GCC-PHAT fails between a room-across camera mic and close-mic'd /
sampled studio stems — they share no fine waveform structure (verified: confidence
pinned at the noise floor). But the *onset envelope* — when drum hits happen — is
common to both, so correlating spectral-flux envelopes locks reliably.

Computed in chunks so a 60-minute reference never materializes a full STFT matrix.
"""

from __future__ import annotations

import numpy as np

DEFAULT_HOP = 80  # 16000/80 = 200 Hz envelope (5 ms bins)
DEFAULT_NFFT = 1024


def onset_envelope(
    x: np.ndarray, sr: int, hop: int = DEFAULT_HOP, nfft: int = DEFAULT_NFFT,
    chunk_frames: int = 40000,
) -> tuple[np.ndarray, float]:
    """Half-wave-rectified spectral flux, mean-removed. Returns (envelope, env_fs)."""
    x = np.ascontiguousarray(x, dtype=np.float32)
    if x.size < nfft:
        return np.zeros(0, np.float32), sr / hop
    window = np.hanning(nfft).astype(np.float32)
    sw = np.lib.stride_tricks.sliding_window_view(x, nfft)  # (N-nfft+1, nfft) view
    n_frames = 1 + (x.size - nfft) // hop

    env = np.empty(n_frames, dtype=np.float32)
    prev_mag: np.ndarray | None = None
    fi = 0
    while fi < n_frames:
        cf = min(chunk_frames, n_frames - fi)
        frames = sw[fi * hop: fi * hop + cf * hop: hop] * window  # (cf, nfft) copy
        mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
        pad = prev_mag[None, :] if prev_mag is not None else mag[:1]
        diff = mag - np.concatenate([pad, mag[:-1]], axis=0)
        env[fi: fi + cf] = np.maximum(0.0, diff).sum(axis=1)
        prev_mag = mag[-1]
        fi += cf

    env -= env.mean()
    return env, sr / hop
