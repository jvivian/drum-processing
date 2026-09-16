"""Tests for the GCC-PHAT alignment core (pure, no I/O)."""

import numpy as np
import pytest

from drum_processing.gcc_phat import MAX_ABS_DRIFT_PPM, fit_drift, gcc_phat


def _rng():
    return np.random.default_rng(1234)


def _bandlimited_noise(n, rng):
    x = rng.standard_normal(n).astype(np.float32)
    # crude low-pass so it resembles drum-band energy
    k = np.ones(32, dtype=np.float32) / 32
    return np.convolve(x, k, mode="same")


def test_recovers_known_offset():
    """A clip cut from a reference at position O should report lag ≈ +O seconds."""
    fs = 16000
    ref = _bandlimited_noise(fs * 60, _rng())  # 60 s reference
    offset_samples = fs * 12  # clip starts 12 s into the reference
    clip = ref[offset_samples: offset_samples + fs * 20].copy()  # 20 s clip

    est = gcc_phat(clip, ref, fs)
    assert est.lag_s == pytest.approx(12.0, abs=0.01)
    assert est.confidence > 20


def test_offset_with_noise_and_bandlimit():
    """Robust to a noisy, band-limited camera mic (0 dB-ish SNR)."""
    fs = 16000
    rng = _rng()
    ref = _bandlimited_noise(fs * 40, rng)
    offset_samples = fs * 7
    clip = ref[offset_samples: offset_samples + fs * 15].copy()
    clip += rng.standard_normal(clip.size).astype(np.float32) * clip.std()

    est = gcc_phat(clip, ref, fs)
    assert est.lag_s == pytest.approx(7.0, abs=0.02)


def test_drift_fit_slope():
    times = np.array([0.0, 100.0, 200.0, 300.0])
    # 50 ppm drift: offset grows 50e-6 s per second
    offsets = 3.0 + 50e-6 * times
    fit = fit_drift(times, offsets)
    assert fit.ok
    assert fit.offset_s == pytest.approx(3.0, abs=1e-3)
    assert fit.drift_ppm == pytest.approx(50.0, abs=1.0)


def test_drift_fit_rejects_implausible_slope():
    times = np.array([0.0, 1.0, 2.0])
    offsets = np.array([0.0, 0.5, 1.0])  # 500,000 ppm — absurd
    fit = fit_drift(times, offsets)
    assert not fit.ok
    assert fit.drift_ppm == 0.0
    assert abs(fit.drift_ppm) <= MAX_ABS_DRIFT_PPM
