# drum-processing/drum_processing/__init__.py
"""Automate drum clip preprocessing"""

__version__ = "0.1.0"
__author__ = "John Vivian"
__email__ = "jtvivian@gmail.com"

from .core import hello_world

__all__ = ["hello_world"]


# drum-processing/drum_processing/core.py
"""Core functionality for drum_processing."""


def hello_world() -> str:
    """Return a hello world message.
    
    Returns:
        str: A greeting message.
    """
    return "Hello from drum_processing!"


def add_numbers(a: float, b: float) -> float:
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
        
    Returns:
        The sum of a and b
    """
    return a + b


# drum-processing/drum_processing/cli.py
"""Command line interface for drum_processing."""

import argparse
from .core import hello_world


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Automate drum clip preprocessing")
    parser.add_argument(
        "--greeting", 
        action="store_true", 
        help="Show greeting message"
    )
    
    args = parser.parse_args()
    
    if args.greeting:
        print(hello_world())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


# drum-processing/tests/conftest.py
"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_data():
    """Sample data for testing."""
    return {"test": "data"}


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


@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
    (10.5, 2.5, 13.0)
])
def test_add_numbers_parametrized(a, b, expected):
    """Test add_numbers with multiple parameter combinations."""
    assert add_numbers(a, b) == expected