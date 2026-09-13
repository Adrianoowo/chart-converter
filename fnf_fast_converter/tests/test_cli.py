"""
Unit and Integration Tests for Converter CLI (`src/cli.py`).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fnf_fast_converter.src.cli import (
    format_bytes,
    format_time,
    main,
    render_progress_bar,
    run_benchmark,
)
from fnf_fast_converter.tests.e2e.harness_helpers import build_complete_test_con


class TestCLIHelpers:
    """Tests for CLI formatting and rendering helpers."""

    def test_format_time(self):
        """Verify time formatting across sub-minute, minutes, and hours."""
        assert format_time(0) == "00:00"
        assert format_time(45) == "00:45"
        assert format_time(90) == "01:30"
        assert format_time(3665) == "01:01:05"
        assert format_time(-10) == "00:00"

    def test_format_bytes(self):
        """Verify byte formatting across B, KB, MB, and GB."""
        assert format_bytes(512) == "512 B"
        assert format_bytes(2048) == "2.0 KB"
        assert format_bytes(10 * 1024 * 1024) == "10.0 MB"
        assert format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"

    def test_render_progress_bar(self):
        """Verify progress bar generation at various percentages."""
        bar_0 = render_progress_bar(0.0, width=10)
        assert bar_0 == "[░░░░░░░░░░]"

        bar_50 = render_progress_bar(50.0, width=10)
        assert bar_50 == "[█████░░░░░]"

        bar_100 = render_progress_bar(100.0, width=10)
        assert bar_100 == "[██████████]"


class TestCLIExecution:
    """Tests for CLI main entry point and flag handling."""

    def test_cli_help(self, capsys):
        """Verify --help outputs usage information and exits with code 0."""
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "fnf_fast_converter" in captured.out
        assert "--input" in captured.out
        assert "--output" in captured.out

    def test_cli_version(self, capsys):
        """Verify --version outputs version and exits with code 0."""
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "1.1.0" in captured.out

    def test_cli_nonexistent_input_returns_code_1(self, capsys):
        """Verify non-existent input path returns exit code 1."""
        code = main(["-i", "non_existent_directory_xyz", "-o", "out"])
        assert code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_cli_dry_run_flag(self, tmp_path, capsys):
        """Verify --dry-run prints plan without creating destination directory."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        con = build_complete_test_con(title="Dry Run Song")
        (in_dir / "test.con").write_bytes(con)

        out_dir = tmp_path / "out_dry"
        code = main(["-i", str(in_dir), "-o", str(out_dir), "--dry-run", "--quiet"])
        assert code == 0
        assert not out_dir.exists() or len(list(out_dir.iterdir())) == 0

        captured = capsys.readouterr()
        assert "CONVERSION SUMMARY REPORT" in captured.out
        assert "Total Packages Scanned:  1" in captured.out

    def test_cli_end_to_end_conversion(self, tmp_path, capsys):
        """Verify CLI converts multiple CON packages from input directory."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()

        for i in range(3):
            con = build_complete_test_con(
                song_id=f"cli_song_{i}",
                title=f"CLI Song {i}",
                artist="CLI Artist",
            )
            (in_dir / f"song_{i}.con").write_bytes(con)

        out_dir = tmp_path / "out_cli"
        code = main([
            "--input", str(in_dir),
            "--output", str(out_dir),
            "--threads", "4",
            "--charter", "Dansla116",
        ])
        assert code == 0

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == 3

        captured = capsys.readouterr()
        assert "Successfully Converted:  3" in captured.out

    def test_cli_force_and_fast_resume(self, tmp_path, capsys):
        """Verify CLI skips existing songs on second pass and re-converts with --force."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        con = build_complete_test_con(title="Resume Song", artist="Resume Artist")
        (in_dir / "test.con").write_bytes(con)

        out_dir = tmp_path / "out_resume"

        # Pass 1: Initial conversion
        code1 = main(["-i", str(in_dir), "-o", str(out_dir), "-q"])
        assert code1 == 0

        # Pass 2: Fast resume
        code2 = main(["-i", str(in_dir), "-o", str(out_dir), "-q"])
        assert code2 == 0
        captured2 = capsys.readouterr()
        assert "Skipped (Up to date):    1" in captured2.out
        assert "Successfully Converted:  0" in captured2.out

        # Pass 3: Force overwrite
        code3 = main(["-i", str(in_dir), "-o", str(out_dir), "-f", "-q"])
        assert code3 == 0
        captured3 = capsys.readouterr()
        assert "Successfully Converted:  1" in captured3.out
        assert "Skipped (Up to date):    0" in captured3.out

    def test_cli_benchmark_mode(self, tmp_path, capsys):
        """Verify --benchmark mode runs benchmarks and prints comparison table."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        con = build_complete_test_con(title="Bench Song", artist="Bench Artist")
        (in_dir / "bench.con").write_bytes(con)

        out_dir = tmp_path / "out_bench"
        code = main(["-i", str(in_dir), "-o", str(out_dir), "--benchmark", "--threads", "4"])
        assert code == 0

        captured = capsys.readouterr()
        assert "Performance Benchmark Suite" in captured.out
        assert "Speedup over Onyx Baseline:" in captured.out
        assert "Fast Resume Check Latency:" in captured.out
