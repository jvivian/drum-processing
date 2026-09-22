"""Take-detection source selection: MIDI stem when present, else fall back to mix."""

from drum_processing.manifest import Manifest, Stem
from drum_processing.stages.takes import _detect_stem


def _manifest_with(*stems: tuple[str, str | None]) -> Manifest:
    m = Manifest(session_dir=".")
    for name, role in stems:
        m.stems.append(Stem(name=name, src_path=name, role=role))
    return m


def test_finds_addictive_drums_by_role():
    m = _manifest_with(("2026-09-15-STL.wav", "STL"),
                       ("2026-09-15-Addictive Drums 2.wav", "Addictive Drums"))
    stem = _detect_stem(m, "Addictive Drums")
    assert stem is not None and stem.role == "Addictive Drums"


def test_matches_by_filename_when_role_missing():
    m = _manifest_with(("2026-09-15-Addictive Drums 2.wav", None))
    assert _detect_stem(m, "addictive drums") is not None  # case-insensitive


def test_returns_none_when_absent_or_disabled():
    m = _manifest_with(("STL.wav", "STL"), ("STR.wav", "STR"))
    assert _detect_stem(m, "Addictive Drums") is None  # pure-acoustic → fall back to mix
    assert _detect_stem(m, None) is None               # detection disabled
