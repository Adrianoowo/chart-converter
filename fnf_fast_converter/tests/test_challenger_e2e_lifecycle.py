"""
test_challenger_e2e_lifecycle.py - Empirical Challenger Verification Harness

Rigorous, empirical tests for:
1. Headless FastConverterApp lifecycle with simulated button clicks, events, and UI interactions.
2. Detailed Clone Hero chart validation: song.ini (icon = fnf), notes.mid, audio stems, album.png.
3. Real dataset end-to-end conversion validation.
4. Standalone executable integrity: dist/FNF_Fast_Converter/FNF_Fast_Converter.exe and bundled DLLs.
5. High-stress concurrency, queue scale (1000 items), and SafeStreamWriter threading.
"""

import configparser
import io
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import numpy as np
import pytest
import soundfile as sf

from fnf_fast_converter.src.gui import SafeStreamWriter, setup_safe_streams
from fnf_fast_converter.src.gui_app import FastConverterApp
from fnf_fast_converter.src.gui_config import AppConfig, ConfigManager
from fnf_fast_converter.src.gui_queue import QueueItem, QueueItemState, QueueModel
from fnf_fast_converter.src.gui_scanner import scan_directory_for_con_files, peek_con_metadata
from fnf_fast_converter.src.gui_stats import LiveStatsTracker
from fnf_fast_converter.src.gui_worker import (
    ConversionWorkerPool,
    WorkerEvent,
    WorkerEventType,
)
from fnf_fast_converter.src.pipeline import convert_con_to_song_folder, is_valid_converted_song
from tests.e2e.harness_helpers import (
    build_complete_test_con,
    build_synthetic_dta,
    build_synthetic_midi,
    build_synthetic_mogg,
    build_synthetic_png_xbox,
    build_synthetic_stfs,
)


def get_real_dataset_dir() -> Optional[Path]:
    dl = Path(r"C:\Users\adema\Downloads")
    if not dl.exists():
        return None
    matches = [p for p in dl.iterdir() if "Dansla116" in p.name or "FNFestivaltoRB" in p.name]
    return matches[0] if matches else None


# ============================================================================
# 1. HEADLESS FastConverterApp LIFECYCLE & BUTTON CLICKS HARNESS
# ============================================================================

class TestHeadlessAppLifecycleHarness:
    """Empirically validates FastConverterApp lifecycle with simulated UI clicks and events."""

    @classmethod
    def setup_class(cls):
        """Create persistent headless FastConverterApp to avoid Tk/Tcl uninit issues."""
        cls._tmp_dir = tempfile.mkdtemp()
        cfg_file = Path(cls._tmp_dir) / "test_config.json"
        cls.app = FastConverterApp(custom_config_path=cfg_file)
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def teardown_class(cls):
        try:
            if cls.app.worker_pool and cls.app.worker_pool.is_running():
                cls.app.worker_pool.shutdown(wait=False)
            cls.app.destroy()
        except Exception:
            pass
        try:
            shutil.rmtree(cls._tmp_dir, ignore_errors=True)
        except Exception:
            pass

    def setup_method(self):
        """Reset queue and UI state before each test."""
        self.app.queue_model.clear()
        self.app.queue_table.clear()
        self.app.stats_tracker.reset()
        self.app.progress_view.reset()
        self.app.update()

    def test_headless_full_conversion_workflow_with_button_clicks(self, tmp_path):
        """Simulate end-to-end user workflow: add CON files, click Start button, observe event loop, verify completion."""
        out_dir = tmp_path / "output_charts"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.app.settings_toolbar.set_output_dir(str(out_dir))
        self.app.config.overwrite = True
        self.app.settings_toolbar.var_overwrite.set(True)

        # 1. Create 3 synthetic CON packages
        con_files = []
        for i in range(3):
            con_bytes = build_complete_test_con(
                song_id=f"song_{i}",
                title=f"Challenger Song {i}",
                artist=f"Challenger Artist {i}",
                album=f"Challenger Album {i}",
                audio_channels=2,
            )
            con_file = tmp_path / f"song_{i}.con"
            con_file.write_bytes(con_bytes)
            con_files.append(con_file)

        # 2. Add files via handle_dropped_paths
        self.app.handle_dropped_paths(con_files)
        self.app.update_idletasks()
        self.app.update()

        items = self.app.queue_model.get_items()
        assert len(items) == 3
        assert all(it.state == QueueItemState.PENDING for it in items)

        # 3. Simulate clicking the "Start Conversion" button
        self.app._on_start_conversion()
        assert self.app.worker_pool.is_running()

        # 4. Drain event loop until worker finishes
        timeout = time.time() + 15.0
        while self.app.worker_pool.is_running() and time.time() < timeout:
            self.app._poll_worker_events()
            self.app.update_idletasks()
            self.app.update()
            time.sleep(0.05)

        # Final drain
        self.app._poll_worker_events()
        self.app.update_idletasks()
        self.app.update()

        assert not self.app.worker_pool.is_running()

        # 5. Verify all items completed
        items = self.app.queue_model.get_items()
        for it in items:
            assert it.state == QueueItemState.DONE, f"Item {it.title} state was {it.state}, error={it.error_message}"

        # 6. Verify progress view
        assert "3 / 3" in self.app.progress_view.lbl_percent.cget("text")
        assert "3 Done" in self.app.progress_view.lbl_counts.cget("text")

        # 7. Verify log console drawer received messages
        logs = self.app.log_console.textbox.get("1.0", "end")
        assert len(logs.strip()) > 10

    def test_headless_real_con_file_conversion(self, tmp_path):
        """If real CON dataset is present, test converting authentic song through GUI."""
        ds = get_real_dataset_dir()
        if not ds:
            pytest.skip("Real CON dataset not found")

        buddy_holly = ds / "Weezer - Buddy Holly"
        if not buddy_holly.is_file():
            pytest.skip("Weezer - Buddy Holly CON not found")

        out_dir = tmp_path / "real_charts_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.app.settings_toolbar.set_output_dir(str(out_dir))
        self.app.config.overwrite = True

        self.app.handle_dropped_paths([buddy_holly])
        self.app.update_idletasks()
        self.app.update()

        assert len(self.app.queue_model) == 1
        item = self.app.queue_model.get_items()[0]
        assert "Buddy Holly" in item.title or "Weezer" in item.title or "Buddy Holly" in item.display_subtitle

        self.app._on_start_conversion()
        timeout = time.time() + 30.0
        while self.app.worker_pool.is_running() and time.time() < timeout:
            self.app._poll_worker_events()
            self.app.update_idletasks()
            self.app.update()
            time.sleep(0.05)
        self.app._poll_worker_events()

        assert not self.app.worker_pool.is_running()
        item = self.app.queue_model.get_items()[0]
        assert item.state == QueueItemState.DONE
        
        # Verify song.ini with icon = fnf in real converted song folder
        out_song = out_dir / "Weezer - Buddy Holly"
        assert out_song.is_dir()
        ini_p = out_song / "song.ini"
        assert ini_p.is_file()
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(str(ini_p), encoding="utf-8")
        assert cfg.get("song", "icon", fallback="") == "fnf"

    def test_headless_pause_cancel_workflow(self, tmp_path):
        """Simulate clicking Start then Cancel during active conversion."""
        out_dir = tmp_path / "cancel_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.app.settings_toolbar.set_output_dir(str(out_dir))

        con_files = []
        for i in range(5):
            con_bytes = build_complete_test_con(
                song_id=f"cancel_{i}",
                title=f"Cancel Track {i}",
                artist="Cancel Band",
                audio_channels=2,
            )
            con_file = tmp_path / f"cancel_song_{i}.con"
            con_file.write_bytes(con_bytes)
            con_files.append(con_file)

        self.app.handle_dropped_paths(con_files)
        self.app.update_idletasks()
        self.app.update()

        # Start conversion
        self.app._on_start_conversion()
        assert self.app.worker_pool.is_running()

        # Simulate clicking Cancel button
        self.app._on_cancel_conversion()
        
        # Drain events
        for _ in range(20):
            self.app._poll_worker_events()
            self.app.update_idletasks()
            self.app.update()
            time.sleep(0.05)

        assert not self.app.worker_pool.is_running()

    def test_headless_retry_failed_and_clear_all_buttons(self, tmp_path):
        """Test simulated Clear All and Retry Failed button actions."""
        out_dir = tmp_path / "retry_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.app.settings_toolbar.set_output_dir(str(out_dir))

        # Add a mix of valid and corrupted files
        valid_con = tmp_path / "valid.con"
        valid_con.write_bytes(build_complete_test_con("validsong", "Valid Song", "Valid Artist"))
        
        corrupt_con = tmp_path / "corrupt.con"
        corrupt_con.write_bytes(b"CON NOT VALID STFS DATA TRUNCATED 0000000000")

        self.app.handle_dropped_paths([valid_con, corrupt_con])
        self.app.update_idletasks()

        # Start conversion
        self.app._on_start_conversion()
        timeout = time.time() + 15.0
        while self.app.worker_pool.is_running() and time.time() < timeout:
            self.app._poll_worker_events()
            self.app.update_idletasks()
            time.sleep(0.05)
        self.app._poll_worker_events()

        items = self.app.queue_model.get_items()
        assert len(items) == 2
        states = {it.title: it.state for it in items}
        assert states["Valid Song"] == QueueItemState.DONE
        assert states["corrupt"] == QueueItemState.ERROR

        # Test retry failed
        self.app._on_retry_failed()
        items = self.app.queue_model.get_items()
        states = {it.title: it.state for it in items}
        assert states["Valid Song"] == QueueItemState.DONE
        assert states["corrupt"] == QueueItemState.PENDING

        # Test clear all
        self.app._on_clear_all()
        assert len(self.app.queue_model.get_items()) == 0


# ============================================================================
# 2. DETAILED CLONE HERO CHART DIRECTORY VALIDATION (icon = fnf, audio, midi)
# ============================================================================

class TestCloneHeroChartDirectoryIntegrity:
    """Empirically inspects generated Clone Hero chart directories for strict format compliance."""

    @pytest.mark.parametrize(
        "audio_channels,ranks,expected_stems",
        [
            (2, {"guitar": 3}, ["guitar.ogg", "song.ogg"]),
            (4, {"drums": 4, "guitar": 3}, ["drums.ogg", "guitar.ogg"]),
            (6, {"drums": 4, "bass": 2, "guitar": 3}, ["drums.ogg", "rhythm.ogg", "guitar.ogg"]),
            (8, {"drums": 4, "bass": 2, "guitar": 3, "vocals": 5}, ["drums.ogg", "rhythm.ogg", "guitar.ogg", "vocals.ogg"]),
            (10, {"drums": 4, "bass": 2, "guitar": 3, "vocals": 5, "keys": 2}, ["drums.ogg", "rhythm.ogg", "guitar.ogg", "vocals.ogg"]),
        ],
    )
    def test_synthetic_chart_full_structure_and_stems(self, tmp_path, audio_channels, ranks, expected_stems):
        """Verify converted song folder has song.ini with icon=fnf, valid notes.mid, and valid .ogg audio stems."""
        con_bytes = build_complete_test_con(
            song_id=f"chart_integrity_{audio_channels}",
            title="Chart Integrity Song",
            artist="Clone Hero Artists",
            album="Integrity Album",
            year=2026,
            genre="rock",
            ranks=ranks,
            audio_channels=audio_channels,
            include_cover=True,
        )
        con_path = tmp_path / f"integrity_{audio_channels}ch.con"
        con_path.write_bytes(con_bytes)
        out_root = tmp_path / "charts"

        success, msg = convert_con_to_song_folder(
            con_path,
            out_root,
            overwrite=True,
            charter="EmpiricalChallenger",
        )
        assert success, f"Conversion failed: {msg}"
        
        song_dir = out_root / "Clone Hero Artists - Chart Integrity Song"
        assert song_dir.is_dir(), f"Expected song dir not found: {song_dir}"

        # 1. Verify song.ini exists and has [song] section with icon = fnf
        ini_file = song_dir / "song.ini"
        assert ini_file.is_file(), "song.ini missing from output folder"
        
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(str(ini_file), encoding="utf-8")
        assert "song" in cfg.sections() or "Song" in cfg.sections()
        sec = "song" if "song" in cfg.sections() else "Song"
        
        assert cfg.get(sec, "icon", fallback="") == "fnf", "song.ini MUST contain icon = fnf"
        assert cfg.get(sec, "name", fallback="") == "Chart Integrity Song"
        assert cfg.get(sec, "artist", fallback="") == "Clone Hero Artists"
        assert cfg.get(sec, "album", fallback="") == "Integrity Album"
        assert cfg.get(sec, "year", fallback="") == "2026"
        assert cfg.get(sec, "genre", fallback="") == "Rock"

        # 2. Verify notes.mid is valid MIDI
        mid_file = song_dir / "notes.mid"
        assert mid_file.is_file(), "notes.mid missing from output folder"
        mid_data = mid_file.read_bytes()
        assert len(mid_data) >= 14, "notes.mid is too small"
        assert mid_data[:4] == b"MThd", "notes.mid header missing 'MThd' magic"

        # 3. Verify audio stems exist and are readable Ogg Vorbis
        for stem_name in expected_stems:
            stem_path = song_dir / stem_name
            if stem_path.is_file():
                data, sr = sf.read(str(stem_path))
                assert len(data) > 0
                assert sr > 0

        # 4. Verify album.png
        album_png = song_dir / "album.png"
        assert album_png.is_file()
        assert album_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

        # 5. Verify is_valid_converted_song returns True
        assert is_valid_converted_song(song_dir)


# ============================================================================
# 3. STANDALONE DISTRIBUTION EXECUTABLE INTEGRITY HARNESS
# ============================================================================

class TestStandaloneExecutableDistribution:
    """Empirically inspects dist/FNF_Fast_Converter/FNF_Fast_Converter.exe for standalone execution integrity."""

    @pytest.fixture
    def exe_path(self):
        root = Path(__file__).resolve().parent.parent.parent
        # Look in dist/FNF_Fast_Converter/FNF_Fast_Converter.exe
        exe = root / "dist" / "FNF_Fast_Converter" / "FNF_Fast_Converter.exe"
        if not exe.is_file():
            exe = root / "fnf_fast_converter" / "dist" / "FNF_Fast_Converter" / "FNF_Fast_Converter.exe"
        return exe

    def test_executable_file_exists_and_has_pe_headers(self, exe_path):
        """Verify the binary exists, is larger than 1MB, and has valid PE 'MZ' magic header."""
        assert exe_path.is_file(), f"Executable not found at: {exe_path}"
        size_bytes = exe_path.stat().st_size
        assert size_bytes > 1_000_000, f"Executable size {size_bytes} bytes is suspiciously small (<1MB)"
        
        header = exe_path.read_bytes()[:2]
        assert header == b"MZ", "Executable must start with PE DOS MZ header"

    def test_internal_dependencies_folder_structure(self, exe_path):
        """Verify _internal contains all runtime dependencies."""
        internal_dir = exe_path.parent / "_internal"
        assert internal_dir.is_dir(), "_internal directory missing from distribution"

        # Critical libraries
        expected_items = [
            "customtkinter",
            "PIL",
            "numpy",
            "tcl86t.dll",
            "tk86t.dll",
            "python312.dll",
            "VCRUNTIME140.dll",
        ]
        existing_names = [p.name for p in internal_dir.iterdir()]
        for item in expected_items:
            assert item in existing_names, f"Expected runtime dependency '{item}' not found in _internal"

    def test_executable_subcommand_execution_clean_exit(self, exe_path):
        """Run the compiled executable with --help and verify exit code 0 without crash/exception."""
        proc = subprocess.run(
            [str(exe_path), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, f"Executable exited with error code {proc.returncode}: {proc.stderr}"


# ============================================================================
# 4. HIGH-CONCURRENCY & STRESS VALIDATION
# ============================================================================

class TestConcurrencyAndStressHarness:
    """Stress tests queue capacity, SafeStreamWriter threading, and stats calculations."""

    def test_1000_queue_items_fast_ingestion_and_state_filtering(self, tmp_path):
        """Stress test QueueModel with 1000 items in memory."""
        model = QueueModel()
        items = []
        for i in range(1000):
            p = tmp_path / f"track_{i:04d}.con"
            items.append(QueueItem(file_path=p, title=f"Track {i}", size_bytes=i * 1024))

        t0 = time.perf_counter()
        for item in items:
            model.add_item(item)
        elapsed = time.perf_counter() - t0

        assert len(model) == 1000
        assert elapsed < 0.1, f"Adding 1000 items took too long: {elapsed:.4f}s"

        # Mark 500 as DONE, 300 as ERROR, 200 as SKIPPED
        for i, item in enumerate(model.get_items()):
            if i < 500:
                model.set_item_state(item.id, QueueItemState.DONE)
            elif i < 800:
                model.set_item_state(item.id, QueueItemState.ERROR, error_message="Synthetic error")
            else:
                model.set_item_state(item.id, QueueItemState.SKIPPED)

        counts = model.counts()
        assert counts["total"] == 1000
        assert counts["done"] == 500
        assert counts["error"] == 300
        assert counts["skipped"] == 200
        assert counts["pending"] == 0

        # Retry failed resets error and skipped (500 items)
        retried = model.retry_failed()
        assert len(retried) == 500
        assert model.counts()["pending"] == 500
        assert model.counts()["done"] == 500

    def test_safestreamwriter_multi_threaded_hammer(self):
        """Hammer SafeStreamWriter with 20 concurrent threads writing simultaneously."""
        received_logs = []
        lock = threading.Lock()

        def log_cb(msg: str, lvl: str):
            with lock:
                received_logs.append((msg, lvl))

        writer = SafeStreamWriter(original_stream=None, log_callback=log_cb, level="INFO")
        
        def hammer_worker(tid: int):
            for i in range(100):
                writer.write(f"[Thread {tid}] Log message {i}\n")

        threads = [threading.Thread(target=hammer_worker, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        writer.flush()
        assert len(received_logs) == 2000
