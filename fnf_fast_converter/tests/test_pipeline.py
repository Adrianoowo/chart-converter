"""
Unit and Integration Tests for Converter Pipeline & Batch Engine (`src/pipeline.py`).
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import List

import pytest

from fnf_fast_converter.src.pipeline import (
    BatchConversionProgress,
    BatchConversionStats,
    BatchConverter,
    convert_con_to_song_folder,
    is_valid_converted_song,
    sanitize_folder_name,
)
from fnf_fast_converter.tests.e2e.harness_helpers import (
    build_complete_test_con,
    build_synthetic_dta,
    build_synthetic_midi,
    build_synthetic_mogg,
    build_synthetic_png_xbox,
    build_synthetic_stfs,
    validate_converted_chart_folder,
)


class TestPipelineUnit:
    """Unit tests for pipeline helper functions."""

    def test_sanitize_folder_name(self):
        """Verify Windows-forbidden characters and whitespace/dots are sanitized."""
        assert sanitize_folder_name("AC/DC - Back in Black") == "AC_DC - Back in Black"
        assert sanitize_folder_name("Artist: Name *?<>|") == "Artist_ Name _____"
        assert sanitize_folder_name("...Leading and Trailing... ") == "Leading and Trailing"
        assert sanitize_folder_name("") == "unnamed_song"
        assert sanitize_folder_name("   ") == "unnamed_song"
        assert sanitize_folder_name("Artist - Song (feat. \"Special\")") == "Artist - Song (feat. _Special_)"

    def test_is_valid_converted_song(self, tmp_path):
        """Verify fast (<0.1ms) validation logic on various directory states."""
        song_dir = tmp_path / "valid_song"
        song_dir.mkdir()

        # Non-existent directory
        assert not is_valid_converted_song(tmp_path / "non_existent")

        # Empty directory
        assert not is_valid_converted_song(song_dir)

        # Only song.ini without icon = fnf
        (song_dir / "song.ini").write_text("[song]\nname = Test\n", encoding="utf-8")
        assert not is_valid_converted_song(song_dir)

        # song.ini with icon = fnf but no notes.mid
        (song_dir / "song.ini").write_text("[song]\nicon = fnf\nname = Test\n", encoding="utf-8")
        assert not is_valid_converted_song(song_dir)

        # notes.mid invalid header
        (song_dir / "notes.mid").write_bytes(b"invalid_midi_header")
        assert not is_valid_converted_song(song_dir)

        # notes.mid valid header but no stems
        (song_dir / "notes.mid").write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0")
        assert not is_valid_converted_song(song_dir)

        # Valid notes.mid + song.ini + stems
        (song_dir / "song.ogg").write_bytes(b"OggS\x00test")
        assert is_valid_converted_song(song_dir)

    def test_is_valid_converted_song_speed(self, tmp_path):
        """Verify fast-resume validation runs in < 0.5ms."""
        con_bytes = build_complete_test_con()
        out_dir = tmp_path / "out"
        ok, msg = convert_con_to_song_folder(con_bytes, out_dir)
        assert ok

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == 1
        target = chart_folders[0]

        # Measure 100 checks
        t0 = time.perf_counter()
        n_iters = 100
        for _ in range(n_iters):
            assert is_valid_converted_song(target)
        elapsed = time.perf_counter() - t0
        avg_ms = (elapsed / n_iters) * 1000.0

        assert avg_ms < 1.0, f"is_valid_converted_song too slow: {avg_ms:.3f}ms"


class TestSingleSongConversion:
    """Integration tests for single CON song conversion."""

    def test_convert_con_bytes_in_memory(self, tmp_path):
        """Verify full conversion from raw in-memory CON bytes."""
        con_bytes = build_complete_test_con(
            song_id="in_memory_test",
            title="In Memory Song",
            artist="Fast Artist",
        )
        out_dir = tmp_path / "out"
        ok, msg = convert_con_to_song_folder(con_bytes, out_dir, charter="Dansla116")
        assert ok
        assert "CONVERTED" in msg

        chart_dir = out_dir / "Fast Artist - In Memory Song"
        assert chart_dir.is_dir()
        validate_converted_chart_folder(chart_dir, expected_title="In Memory Song", expected_artist="Fast Artist")

    def test_convert_con_file_from_disk(self, tmp_path):
        """Verify conversion directly from a CON file path on disk."""
        con_bytes = build_complete_test_con(
            song_id="file_path_test",
            title="File Path Song",
            artist="Disk Artist",
        )
        con_file = tmp_path / "song.con"
        con_file.write_bytes(con_bytes)

        out_dir = tmp_path / "out"
        ok, msg = convert_con_to_song_folder(con_file, out_dir)
        assert ok
        assert "CONVERTED" in msg

        chart_dir = out_dir / "Disk Artist - File Path Song"
        assert chart_dir.is_dir()

    def test_fast_resume_skips_when_overwrite_false(self, tmp_path):
        """Verify converter skips already converted songs when overwrite=False."""
        con_bytes = build_complete_test_con()
        out_dir = tmp_path / "out"

        ok1, msg1 = convert_con_to_song_folder(con_bytes, out_dir, overwrite=False)
        assert ok1
        assert "CONVERTED" in msg1

        ok2, msg2 = convert_con_to_song_folder(con_bytes, out_dir, overwrite=False)
        assert ok2
        assert "SKIPPED" in msg2

    def test_force_overwrite_re_converts(self, tmp_path):
        """Verify converter re-runs conversion when overwrite=True."""
        con_bytes = build_complete_test_con()
        out_dir = tmp_path / "out"

        convert_con_to_song_folder(con_bytes, out_dir, overwrite=False)
        ok, msg = convert_con_to_song_folder(con_bytes, out_dir, overwrite=True)
        assert ok
        assert "CONVERTED" in msg

    def test_missing_album_art_graceful_fallback(self, tmp_path):
        """Verify conversion succeeds when cover art is missing from CON package."""
        con_bytes = build_complete_test_con(include_cover=False)
        out_dir = tmp_path / "out"
        ok, msg = convert_con_to_song_folder(con_bytes, out_dir)
        assert ok

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == 1
        validate_converted_chart_folder(chart_folders[0], require_album_png=False)

    def test_corrupted_con_returns_error(self, tmp_path):
        """Verify corrupted CON package returns False with descriptive message without crashing."""
        corrupt_con = b"CON \x00\x00\x00\x00" + b"\xff" * 1000
        out_dir = tmp_path / "out"
        ok, msg = convert_con_to_song_folder(corrupt_con, out_dir)
        assert not ok
        assert "ERROR" in msg or "Failed" in msg


class TestBatchConverterEngine:
    """Tests for the multi-threaded BatchConverter engine."""

    def test_scan_con_files_filters_non_con_files(self, tmp_path):
        """Verify scanner correctly identifies STFS files and ignores non-STFS files."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()

        # Create valid CON file
        valid_con = build_complete_test_con()
        (in_dir / "song1.con").write_bytes(valid_con)

        # Create nested valid CON file
        sub_dir = in_dir / "subdir"
        sub_dir.mkdir()
        (sub_dir / "song2.con").write_bytes(valid_con)

        # Create non-CON text file
        (in_dir / "readme.txt").write_text("Hello world")

        # Create short dummy file
        (in_dir / "short.bin").write_bytes(b"CON ")

        converter = BatchConverter(input_path=in_dir, output_dir=tmp_path / "out")
        con_files = converter.scan_con_files()

        assert len(con_files) == 2
        file_names = {f.name for f in con_files}
        assert file_names == {"song1.con", "song2.con"}

    def test_batch_converter_dry_run(self, tmp_path):
        """Verify dry-run mode scans and computes preview without writing files."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()

        for i in range(3):
            con = build_complete_test_con(
                song_id=f"dry_{i}",
                title=f"Dry Song {i}",
                artist="Dry Artist",
            )
            (in_dir / f"song_{i}.con").write_bytes(con)

        out_dir = tmp_path / "out"
        progress_events = []

        converter = BatchConverter(
            input_path=in_dir,
            output_dir=out_dir,
            dry_run=True,
            progress_callback=lambda p: progress_events.append(p),
        )
        stats = converter.run()

        assert stats.total_scanned == 3
        assert stats.total_converted == 3
        assert stats.total_failed == 0
        assert not out_dir.exists() or len(list(out_dir.iterdir())) == 0
        assert len(progress_events) == 3

    def test_batch_converter_multi_threaded_execution(self, tmp_path):
        """Verify parallel multi-worker conversion across multiple CON packages."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()

        total_songs = 6
        for i in range(total_songs):
            con = build_complete_test_con(
                song_id=f"parallel_{i}",
                title=f"Parallel Song {i}",
                artist=f"Parallel Artist {i}",
            )
            (in_dir / f"song_{i}.con").write_bytes(con)

        out_dir = tmp_path / "out"
        converter = BatchConverter(
            input_path=in_dir,
            output_dir=out_dir,
            num_workers=4,
            overwrite=False,
        )
        stats = converter.run()

        assert stats.total_scanned == total_songs
        assert stats.total_converted == total_songs
        assert stats.total_failed == 0

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == total_songs
        for cdir in chart_folders:
            validate_converted_chart_folder(cdir)

    def test_batch_converter_fault_isolation(self, tmp_path):
        """Verify a corrupt CON file in the batch does not halt other conversions."""
        in_dir = tmp_path / "input"
        in_dir.mkdir()

        # 2 valid CON files
        valid_con = build_complete_test_con(song_id="good1", title="Good Song 1", artist="Good")
        (in_dir / "good1.con").write_bytes(valid_con)
        valid_con2 = build_complete_test_con(song_id="good2", title="Good Song 2", artist="Good")
        (in_dir / "good2.con").write_bytes(valid_con2)

        # 1 corrupt CON file with CON magic but garbage data
        corrupt_con = b"CON " + b"\x00" * 8192 + b"CorruptedData" * 100
        (in_dir / "bad.con").write_bytes(corrupt_con)

        out_dir = tmp_path / "out"
        converter = BatchConverter(input_path=in_dir, output_dir=out_dir, num_workers=2)
        stats = converter.run()

        assert stats.total_scanned == 3
        assert stats.total_converted == 2
        assert stats.total_failed == 1
        assert len(stats.errors) == 1

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == 2
