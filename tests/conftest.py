# drum-processing/tests/conftest.py
"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_data():
    """Sample data for testing."""
    return {"test": "data"}
