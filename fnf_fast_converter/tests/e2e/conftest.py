"""
Pytest configuration and shared fixtures for E2E Test Suite.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import shutil
import tempfile
import pytest

from fnf_fast_converter.tests.e2e.harness_helpers import (
    build_complete_test_con,
    build_synthetic_stfs,
    build_synthetic_mogg,
    build_synthetic_dta,
    build_synthetic_midi,
    build_synthetic_png_xbox,
)

# Real CON sample dataset path
REAL_CON_DIR = Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB")
REFERENCE_CHART_DIR = Path(r"P:\Charts\Fortnite Festival")


@pytest.fixture(scope="session")
def real_con_dir() -> Path:
    return REAL_CON_DIR


@pytest.fixture(scope="session")
def reference_chart_dir() -> Path:
    return REFERENCE_CHART_DIR


@pytest.fixture
def temp_output_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def synthetic_con_file(temp_output_dir) -> Path:
    con_bytes = build_complete_test_con(
        song_id="testbuddy",
        title="Buddy Holly",
        artist="Weezer",
        album="Weezer (The Blue Album)",
        year=1994,
        genre="alternative",
    )
    con_path = temp_output_dir / "test_song.con"
    con_path.write_bytes(con_bytes)
    return con_path
