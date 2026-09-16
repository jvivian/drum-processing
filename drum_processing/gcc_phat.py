"""GCC-PHAT cross-correlation for aligning camera scratch audio to studio stems.

Phase-transform weighting (``R /= |R|``) whitens the cross-spectrum, making the
result structurally insensitive to the frequency-response gap between a tinny
across-the-room action-cam mic and close-mic'd studio stems — exactly our case.

Pure numpy/scipy, no I/O, so it unit-tests on synthetic signals.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_ABS_DRIFT_PPM = 200.0  # reject fits beyond this as failures; fall back to offset-only


@dataclass
class OffsetEstimate:
    lag_s: float  # position of `sig` within `ref`: sig[0] aligns to ref at +lag_s seconds
    confidence: float  # peak / median of the correlation magnitude


def gcc_phat(sig: np.ndarray, ref: np.ndarray, fs: float,
             max_lag_s: float | None = None) -> OffsetEstimate:
    """Estimate the lag of ``sig`` relative to ``ref`` (both mono at ``fs`` Hz).

    Returns a sub-sample lag (via parabolic interpolation of the correlation peak)
    and a confidence = peak / median of the correlation magnitude.
    """
    n = sig.shape[0] + ref.shape[0]
    nfft = 1 << (int(n - 1).bit_length())  # next power of two >= n

    SIG = np.fft.rfft(sig, n=nfft)
    REF = np.fft.rfft(ref, n=nfft)
    R = SIG * np.conj(REF)
    denom = np.abs(R)
    denom[denom < 1e-12] = 1e-12
    R /= denom  # PHAT weighting

    cc = np.fft.irfft(R, n=nfft)
    # Reorder so index 0 is zero lag, negative lags in the second half.
    max_shift = nfft // 2
    cc = np.concatenate((cc[-max_shift:], cc[: max_shift + 1]))
    mag = np.abs(cc)

    if max_lag_s is not None:
        max_shift_samp = min(max_shift, int(round(max_lag_s * fs)))
        center = max_shift
        window = np.zeros_like(mag)
        lo = center - max_shift_samp
        hi = center + max_shift_samp + 1
        window[lo:hi] = mag[lo:hi]
        mag_search = window
    else:
        mag_search = mag

    peak = int(np.argmax(mag_search))
    # Parabolic interpolation for sub-sample precision.
    delta = _parabolic_offset(mag, peak)
    lag_samples = (peak - max_shift) + delta
    # Negate so +lag_s means "sig begins lag_s seconds into ref" (offset convention).
    lag_s = -lag_samples / fs

    median = float(np.median(mag[mag > 0])) if np.any(mag > 0) else 1e-12
    confidence = float(mag[peak] / median) if median else 0.0
    return OffsetEstimate(lag_s=lag_s, confidence=confidence)


def _parabolic_offset(y: np.ndarray, i: int) -> float:
    """Sub-sample peak offset from a 3-point parabola around index ``i``."""
    if i <= 0 or i >= len(y) - 1:
        return 0.0
    a, b, c = y[i - 1], y[i], y[i + 1]
    denom = a - 2 * b + c
    if abs(denom) < 1e-12:
        return 0.0
    return 0.5 * (a - c) / denom


@dataclass
class DriftFit:
    offset_s: float  # intercept a in offset(t) = a + b*t
    drift_ppm: float  # slope b, in parts-per-million
    residual_ms: float  # RMS fit residual, a QC number
    ok: bool  # False => |drift| implausible, fall back to offset-only


def fit_drift(window_times_s: np.ndarray, offsets_s: np.ndarray) -> DriftFit:
    """Linear-fit per-window offsets to recover clock drift.

    ``offset(t) = a + b*t``; ``b`` is the rate error. Rejects |b| > 200 ppm as a
    fit failure (consumer crystals rarely exceed this) and falls back to a
    constant offset = the median.
    """
    t = np.asarray(window_times_s, dtype=float)
    o = np.asarray(offsets_s, dtype=float)
    if t.size < 2:
        return DriftFit(offset_s=float(o[0]) if o.size else 0.0,
                        drift_ppm=0.0, residual_ms=0.0, ok=True)

    b, a = np.polyfit(t, o, 1)  # slope, intercept
    resid = o - (a + b * t)
    residual_ms = float(np.sqrt(np.mean(resid**2)) * 1000.0)
    drift_ppm = float(b * 1e6)

    if abs(drift_ppm) > MAX_ABS_DRIFT_PPM:
        return DriftFit(offset_s=float(np.median(o)), drift_ppm=0.0,
                        residual_ms=residual_ms, ok=False)
    return DriftFit(offset_s=float(a), drift_ppm=drift_ppm,
                    residual_ms=residual_ms, ok=True)
