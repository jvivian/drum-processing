# drum-processing/tests/test_drum_processing.py
"""Tests for drum_processing."""

import pytest

from drum_processing.core import hello_world, add_numbers


def test_hello_world():
    """Test the hello_world function."""
    result = hello_world()
    assert isinstance(result, str)
    assert "drum_processing" in result


def test_add_numbers():
    """Test the add_numbers function."""
    result = add_numbers(2, 3)
    assert result == 5

    result = add_numbers(2.5, 3.7)
    assert result == 6.2


@pytest.mark.parametrize(
    "a,b,expected", [(1, 2, 3), (0, 0, 0), (-1, 1, 0), (10.5, 2.5, 13.0)]
)
def test_add_numbers_parametrized(a, b, expected):
    """Test add_numbers with multiple parameter combinations."""
    assert add_numbers(a, b) == expected
