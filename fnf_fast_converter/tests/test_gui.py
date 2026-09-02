"""
test_gui.py - Comprehensive Unit, Integration & Headless Lifecycle Tests for FNF Fast Converter Desktop GUI.

Covers Tiers 1-4:
- Tier 1: Pure Python Models & Feature Units (QueueModel, Scanner, Config, LiveStatsTracker, SafeStreamWriter)
- Tier 2: Boundaries, Edge Cases & Error Handling (0-byte, corrupt STFS, 1000 items stress, cancellation)
- Tier 3: Concurrency, Dynamic Updates & Cross-Feature Ingestion
- Tier 4: Headless CustomTkinter Lifecycle, Event Draining & End-to-End Synthetic Conversion to song.ini (icon = fnf)
"""

import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from fnf_fast_converter.src.gui import SafeStreamWriter, setup_safe_streams
from fnf_fast_converter.src.gui_app import FastConverterApp
from fnf_fast_converter.src.gui_config import AppConfig, ConfigManager
from fnf_fast_converter.src.gui_dnd import _decode_dropped_path, hook_drop_target
from fnf_fast_converter.src.gui_queue import (
    QueueItem,
    QueueItemState,
    QueueModel,
    format_byte_size,
)
from fnf_fast_converter.src.gui_scanner import (
    EXCLUDED_EXTENSIONS,
    is_con_file,
    peek_con_metadata,
    scan_directory_for_con_files,
)
from fnf_fast_converter.src.gui_stats import (
    LiveStatsTracker,
    format_duration,
    format_throughput,
)
from fnf_fast_converter.src.gui_views import (
    AdvancedSettingsCard,
    GlobalProgressView,
    HeaderView,
    LogConsoleView,
    QueueRowView,
    QueueTableView,
    QueueToolbarView,
    SettingsToolbarView,
)
from fnf_fast_converter.src.gui_worker import (
    ConversionWorkerPool,
    WorkerEvent,
    WorkerEventType,
)
from tests.e2e.harness_helpers import (
    build_complete_test_con,
    build_synthetic_dta,
    build_synthetic_midi,
    build_synthetic_mogg,
    build_synthetic_png_xbox,
    build_synthetic_stfs,
)

# ============================================================================
# TIER 1: FEATURE COVERAGE (UNIT & MODEL ISOLATION)
# ============================================================================

class TestQueueModel:
    """Unit tests for QueueItem and thread-safe QueueModel collection."""

    @pytest.mark.parametrize(
        "size_bytes,expected",
        [
            (-100, "0 B"),
            (-1, "0 B"),
            (0, "0 B"),
            (1, "1 B"),
            (500, "500 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (2048, "2.0 KB"),
            (1048576, "1.0 MB"),
            (15728640, "15.0 MB"),
            (26214400, "25.0 MB"),
            (1073741824, "1.00 GB"),
            (3221225472, "3.00 GB"),
            (5368709120, "5.00 GB"),
        ],
    )
    def test_format_byte_size_units(self, size_bytes, expected):
        assert format_byte_size(size_bytes) == expected

    def test_queue_item_defaults(self, tmp_path):
        f = tmp_path / "song1.con"
        f.write_bytes(b"12345678")
        item = QueueItem(file_path=f)
        assert item.title == "song1"
        assert item.artist == "Unknown Artist"
        assert item.album == ""
        assert item.size_bytes == 8
        assert item.state == QueueItemState.PENDING
        assert item.formatted_size == "8 B"
        assert item.display_title == "song1"
        assert item.display_subtitle == "song1.con"

    def test_queue_item_string_path_conversion(self, tmp_path):
        f = tmp_path / "string_path.con"
        f.touch()
        item = QueueItem(file_path=str(f))
        assert isinstance(item.file_path, Path)
        assert item.title == "string_path"

    def test_queue_item_custom_metadata(self, tmp_path):
        f = tmp_path / "custom_chart"
        item = QueueItem(
            file_path=f,
            title="Everlong",
            artist="Foo Fighters",
            album="The Colour and the Shape",
            size_bytes=15000000,
        )
        assert item.title == "Everlong"
        assert item.artist == "Foo Fighters"
        assert item.album == "The Colour and the Shape"
        assert item.display_subtitle == "Foo Fighters — The Colour and the Shape"
        assert "MB" in item.formatted_size

    @pytest.mark.parametrize(
        "artist,album,expected_sub",
        [
            ("Queen", "", "Queen"),
            ("Unknown Artist", "A Night at the Opera", "A Night at the Opera"),
            ("Queen", "A Night at the Opera", "Queen — A Night at the Opera"),
            ("Unknown Artist", "", "test_track.con"),
            ("", "", "test_track.con"),
        ],
    )
    def test_queue_item_display_subtitle_variations(self, tmp_path, artist, album, expected_sub):
        f = tmp_path / "test_track.con"
        item = QueueItem(file_path=f, artist=artist, album=album)
        assert item.display_subtitle == expected_sub

    def test_queue_item_state_enum_values(self):
        assert QueueItemState.PENDING.value == "pending"
        assert QueueItemState.CONVERTING.value == "converting"
        assert QueueItemState.DONE.value == "done"
        assert QueueItemState.SKIPPED.value == "skipped"
        assert QueueItemState.ERROR.value == "error"
        assert QueueItemState.REMOVED.value == "removed"

    def test_queue_model_init_empty(self):
        model = QueueModel()
        assert len(model) == 0
        assert model.is_empty()
        assert model.get_items() == []
        assert model.get_pending_items() == []

    def test_queue_model_add_single_item(self, tmp_path):
        model = QueueModel()
        f1 = tmp_path / "test1.con"
        f1.touch()
        item1 = QueueItem(file_path=f1)
        assert model.add_item(item1) is True
        assert len(model) == 1
        assert not model.is_empty()

        item1_dup = QueueItem(file_path=f1)
        assert model.add_item(item1_dup) is False
        assert len(model) == 1

    def test_queue_model_add_files_with_peek(self, tmp_path):
        model = QueueModel()
        f1 = tmp_path / "track1.con"
        f2 = tmp_path / "track2.con"
        f1.touch()
        f2.touch()

        def mock_peek(path: Path):
            return {
                "title": f"Song {path.stem}",
                "artist": "Test Artist",
                "album": "Test Album",
                "size_bytes": 1024,
            }

        added = model.add_files([f1, f2], metadata_peek_fn=mock_peek)
        assert len(added) == 2
        assert len(model) == 2
        assert added[0].title == "Song track1"
        assert added[0].artist == "Test Artist"
        assert added[0].album == "Test Album"

        added_again = model.add_files([f1, f2], metadata_peek_fn=mock_peek)
        assert len(added_again) == 0
        assert len(model) == 2

    def test_queue_model_remove_and_re_add(self, tmp_path):
        model = QueueModel()
        f1 = tmp_path / "song_a.con"
        f2 = tmp_path / "song_b.con"
        f1.touch()
        f2.touch()
        added = model.add_files([f1, f2])

        id_a = added[0].id
        removed = model.remove_item(id_a)
        assert removed is not None
        assert removed.id == id_a
        assert removed.state == QueueItemState.REMOVED
        assert len(model) == 1
        assert model.get_item(id_a) is None

        assert model.remove_item("non_existent_id") is None

        new_added = model.add_files([f1])
        assert len(new_added) == 1
        assert len(model) == 2

    def test_queue_model_remove_selected(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"s_{i}.con" for i in range(5)]
        for f in files:
            f.touch()
        items = model.add_files(files)
        ids_to_remove = [items[0].id, items[2].id]
        removed = model.remove_selected(ids_to_remove)
        assert len(removed) == 2
        assert len(model) == 3

    def test_queue_model_clear(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"c_{i}.con" for i in range(3)]
        for f in files:
            f.touch()
        model.add_files(files)
        assert len(model) == 3
        model.clear()
        assert len(model) == 0
        assert model.is_empty()

    def test_queue_model_state_transitions_and_counts(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"st_{i}.con" for i in range(4)]
        for f in files:
            f.touch()
        items = model.add_files(files)

        counts = model.counts()
        assert counts == {"total": 4, "pending": 4, "converting": 0, "done": 0, "skipped": 0, "error": 0}

        model.set_item_state(items[0].id, QueueItemState.CONVERTING)
        assert model.counts()["converting"] == 1

        model.set_item_state(items[0].id, QueueItemState.DONE, elapsed_time_sec=1.45, output_folder="/out/song0")
        item0 = model.get_item(items[0].id)
        assert item0.state == QueueItemState.DONE
        assert item0.elapsed_time_sec == 1.45
        assert item0.output_folder == "/out/song0"

        model.set_item_state(items[1].id, QueueItemState.SKIPPED, elapsed_time_sec=0.01)
        model.set_item_state(items[2].id, QueueItemState.ERROR, error_message="STFS corrupt", elapsed_time_sec=0.1)

        counts2 = model.counts()
        assert counts2["done"] == 1
        assert counts2["skipped"] == 1
        assert counts2["error"] == 1
        assert counts2["pending"] == 1
        assert len(model.get_pending_items()) == 1

    def test_queue_model_retry_failed(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"rf_{i}.con" for i in range(3)]
        for f in files:
            f.touch()
        items = model.add_files(files)
        model.set_item_state(items[0].id, QueueItemState.DONE)
        model.set_item_state(items[1].id, QueueItemState.ERROR, error_message="Failed")
        model.set_item_state(items[2].id, QueueItemState.SKIPPED)

        retried = model.retry_failed()
        assert len(retried) == 2
        assert items[1].state == QueueItemState.PENDING
        assert items[1].error_message == ""
        assert items[2].state == QueueItemState.PENDING
        assert items[0].state == QueueItemState.DONE

    def test_queue_model_thread_safety(self, tmp_path):
        model = QueueModel()
        num_threads = 8
        items_per_thread = 25

        def worker(thread_idx: int):
            for i in range(items_per_thread):
                p = tmp_path / f"t_{thread_idx}_{i}.con"
                p.touch()
                item = QueueItem(file_path=p)
                model.add_item(item)
                model.set_item_state(item.id, QueueItemState.CONVERTING)
                model.set_item_state(item.id, QueueItemState.DONE, elapsed_time_sec=0.01)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(model) == num_threads * items_per_thread
        assert model.counts()["done"] == num_threads * items_per_thread

class TestScanner:
    """Unit tests for recursive directory scanning and fast metadata peeking."""

    @pytest.mark.parametrize("magic", [b"CON ", b"LIVE", b"PIRS"])
    def test_is_con_file_stfs_magics(self, tmp_path, magic):
        f = tmp_path / f"test_{magic.decode().strip()}.pkg"
        data = bytearray(4096)
        data[0:4] = magic
        f.write_bytes(data)
        assert is_con_file(f) is True

    @pytest.mark.parametrize(
        "ext",
        [
            ".ogg", ".mp3", ".wav", ".flac", ".mid", ".midi",
            ".png", ".jpg", ".jpeg", ".bmp", ".ini", ".txt",
            ".log", ".json", ".py", ".exe", ".dll", ".zip",
            ".tar", ".gz",
        ],
    )
    def test_is_con_file_excluded_extensions(self, tmp_path, ext):
        f = tmp_path / f"song{ext}"
        f.write_bytes(b"CON " + b"\x00" * 5000)
        assert is_con_file(f) is False

    def test_is_con_file_short_file(self, tmp_path):
        f = tmp_path / "short.con"
        f.write_bytes(b"CON short")
        assert is_con_file(f) is False

    def test_is_con_file_non_existent(self, tmp_path):
        assert is_con_file(tmp_path / "ghost.con") is False

    def test_is_con_file_directory(self, tmp_path):
        d = tmp_path / "a_directory"
        d.mkdir()
        assert is_con_file(d) is False

    def test_is_con_file_random_bytes(self, tmp_path):
        f = tmp_path / "random.dat"
        f.write_bytes(b"\xDE\xAD\xBE\xEF" * 1024)
        assert is_con_file(f) is False

    def test_scan_directory_recursive_and_non_recursive(self, tmp_path):
        sub1 = tmp_path / "sub1"
        sub2 = sub1 / "sub2"
        sub2.mkdir(parents=True)

        con_data = bytearray(4096)
        con_data[0:4] = b"CON "

        (tmp_path / "root.con").write_bytes(con_data)
        (sub1 / "level1.con").write_bytes(con_data)
        (sub2 / "level2.con").write_bytes(con_data)
        (sub2 / "ignored.png").write_bytes(con_data)

        res_rec = scan_directory_for_con_files(tmp_path, recursive=True)
        assert len(res_rec) == 3
        assert any(p.name == "root.con" for p in res_rec)
        assert any(p.name == "level1.con" for p in res_rec)
        assert any(p.name == "level2.con" for p in res_rec)

        res_non = scan_directory_for_con_files(tmp_path, recursive=False)
        assert len(res_non) == 1
        assert res_non[0].name == "root.con"

    def test_scan_directory_empty_or_missing(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert scan_directory_for_con_files(empty_dir) == []
        assert scan_directory_for_con_files(tmp_path / "not_exist") == []

    def test_scan_directory_skips_hidden_and_pycache(self, tmp_path):
        hidden = tmp_path / ".hidden_folder"
        pycache = tmp_path / "__pycache__"
        hidden.mkdir()
        pycache.mkdir()

        con_data = bytearray(4096)
        con_data[0:4] = b"CON "
        (hidden / "hidden.con").write_bytes(con_data)
        (pycache / "cached.con").write_bytes(con_data)

        assert scan_directory_for_con_files(tmp_path, recursive=True) == []

    def test_peek_con_metadata_complete_package(self, tmp_path):
        raw_con = build_complete_test_con(
            song_id="rockbandtest",
            title="Test Track Name",
            artist="Superstar Artist",
            album="Greatest Hits",
            year=2024,
        )
        con_path = tmp_path / "track.con"
        con_path.write_bytes(raw_con)

        meta = peek_con_metadata(con_path)
        assert meta["title"] == "Test Track Name"
        assert meta["artist"] == "Superstar Artist"
        assert meta["album"] == "Greatest Hits"
        assert meta["year"] == 2024
        assert meta["song_id"] == "rockbandtest"
        assert meta["size_bytes"] == len(raw_con)

    def test_peek_con_metadata_corrupt_or_invalid_fallback(self, tmp_path):
        corrupt = tmp_path / "corrupt.con"
        corrupt.write_bytes(b"JUNK_DATA_NOT_STFS_HEADER_1234567890")
        meta = peek_con_metadata(corrupt)
        assert meta["title"] == "corrupt"
        assert meta["artist"] == "Unknown Artist"
        assert meta["size_bytes"] == len(b"JUNK_DATA_NOT_STFS_HEADER_1234567890")

    def test_peek_con_metadata_zero_byte_file(self, tmp_path):
        zero = tmp_path / "zero.con"
        zero.touch()
        meta = peek_con_metadata(zero)
        assert meta["title"] == "zero"
        assert meta["size_bytes"] == 0

    def test_peek_con_metadata_missing_dta_fallback(self, tmp_path):
        raw_con = build_synthetic_stfs({"other/file.txt": b"hello"})
        f = tmp_path / "no_dta.con"
        f.write_bytes(raw_con)
        meta = peek_con_metadata(f)
        assert meta["title"] == "no_dta"
        assert meta["artist"] == "Unknown Artist"

class TestConfig:
    """Unit tests for AppConfig dataclass and ConfigManager JSON persistence."""

    def test_app_config_defaults(self):
        cfg = AppConfig()
        assert cfg.output_dir == ""
        assert cfg.overwrite is False
        assert cfg.worker_threads == 4
        assert cfg.stem_threads == 6
        assert cfg.charter == "Dansla116"
        assert cfg.dark_theme == "Dark"
        assert cfg.auto_scroll_logs is True
        assert cfg.window_width == 1100
        assert cfg.window_height == 750

    def test_app_config_to_from_dict_and_clamping(self):
        data = {
            "output_dir": "P:/Charts",
            "overwrite": True,
            "worker_threads": 999,
            "stem_threads": -5,
            "charter": "CustomCharter",
            "dark_theme": "light",
        }
        cfg = AppConfig.from_dict(data)
        assert cfg.output_dir == "P:/Charts"
        assert cfg.overwrite is True
        assert cfg.worker_threads == 64
        assert cfg.stem_threads == 1
        assert cfg.charter == "CustomCharter"
        assert cfg.dark_theme == "Light"

        d = cfg.to_dict()
        assert d["output_dir"] == "P:/Charts"
        assert d["worker_threads"] == 64

    @pytest.mark.parametrize(
        "theme_input,expected_theme",
        [
            ("dark", "Dark"),
            ("DARK", "Dark"),
            ("light", "Light"),
            ("LIGHT", "Light"),
            ("system", "System"),
            ("invalid_theme", "Dark"),
            ("", "Dark"),
        ],
    )
    def test_app_config_theme_normalization(self, theme_input, expected_theme):
        cfg = AppConfig.from_dict({"dark_theme": theme_input})
        assert cfg.dark_theme == expected_theme

    @pytest.mark.parametrize(
        "bad_input",
        [
            None,
            "invalid_string",
            12345,
            [],
            {"worker_threads": "not_an_int"},
            {"stem_threads": "bad_stem"},
        ],
    )
    def test_app_config_from_dict_invalid_types(self, bad_input):
        cfg = AppConfig.from_dict(bad_input)
        assert isinstance(cfg.worker_threads, int)
        assert isinstance(cfg.stem_threads, int)

    def test_config_manager_save_and_load(self, tmp_path):
        config_file = tmp_path / "subfolder" / "app_config.json"
        mgr = ConfigManager(custom_path=config_file)

        cfg = mgr.load()
        assert cfg.charter == "Dansla116"

        cfg.output_dir = "/target/songs"
        cfg.overwrite = True
        cfg.worker_threads = 8
        cfg.charter = "Hero"
        assert mgr.save(cfg) is True
        assert config_file.is_file()

        mgr2 = ConfigManager(custom_path=config_file)
        loaded = mgr2.load()
        assert loaded.output_dir == "/target/songs"
        assert loaded.overwrite is True
        assert loaded.worker_threads == 8
        assert loaded.charter == "Hero"

    def test_config_manager_corrupted_json_recovery(self, tmp_path):
        config_file = tmp_path / "corrupt_config.json"
        config_file.write_text("{INVALID JSON SYNTAX::: 1234", encoding="utf-8")
        mgr = ConfigManager(custom_path=config_file)
        loaded = mgr.load()
        assert loaded.worker_threads == 4

    def test_config_manager_get_config_path_precedence(self, tmp_path):
        custom = tmp_path / "c.json"
        mgr = ConfigManager(custom_path=custom)
        assert mgr.get_config_path() == custom

        mgr_default = ConfigManager()
        path = mgr_default.get_config_path()
        assert "config.json" in str(path)


class TestStatsTracker:
    """Unit tests for LiveStatsTracker, ETA estimation and time formatters."""

    @pytest.mark.parametrize(
        "secs,expected",
        [
            (-100, "--:--"),
            (-1, "--:--"),
            (float("nan"), "--:--"),
            (0, "00:00"),
            (5, "00:05"),
            (59, "00:59"),
            (60, "01:00"),
            (125, "02:05"),
            (3599, "59:59"),
            (3600, "01:00:00"),
            (3665, "01:01:05"),
            (7322, "02:02:02"),
            (86400, "24:00:00"),
            (90061, "25:01:01"),
        ],
    )
    def test_format_duration(self, secs, expected):
        assert format_duration(secs) == expected

    @pytest.mark.parametrize(
        "tps,expected",
        [
            (0.0, "0.0 songs/s"),
            (-5.0, "0.0 songs/s"),
            (0.01, "0.01 songs/s"),
            (0.456, "0.46 songs/s"),
            (2.345, "2.35 songs/s"),
            (9.99, "9.99 songs/s"),
            (10.0, "10.0 songs/s"),
            (14.89, "14.9 songs/s"),
            (100.5, "100.5 songs/s"),
            (250.0, "250.0 songs/s"),
        ],
    )
    def test_format_throughput(self, tps, expected):
        assert format_throughput(tps) == expected

    def test_stats_tracker_workflow(self):
        tracker = LiveStatsTracker(window_size=10)
        assert tracker.get_metrics()["total"] == 0

        tracker.start(total_items=10)
        m0 = tracker.get_metrics()
        assert m0["total"] == 10
        assert m0["processed"] == 0
        assert m0["percent"] == 0.0
        assert m0["is_running"] is True

        tracker.record_item_started("item_1")
        tracker.record_item_finished("item_1", QueueItemState.DONE, elapsed_time=0.1)
        tracker.record_item_started("item_2")
        tracker.record_item_finished("item_2", QueueItemState.SKIPPED, elapsed_time=0.05)
        tracker.record_item_started("item_3")
        tracker.record_item_finished("item_3", QueueItemState.ERROR, elapsed_time=0.08)

        m1 = tracker.get_metrics()
        assert m1["processed"] == 3
        assert m1["done"] == 1
        assert m1["skipped"] == 1
        assert m1["error"] == 1
        assert m1["remaining"] == 7
        assert m1["percent"] == 30.0

        tracker.finish_batch()
        m2 = tracker.get_metrics()
        assert m2["is_running"] is False

        tracker.reset()
        m3 = tracker.get_metrics()
        assert m3["total"] == 0
        assert m3["processed"] == 0


class TestSafeStreamWriter:
    """Unit tests for SafeStreamWriter and stdout/stderr stream capture."""

    def test_safe_stream_writer_buffering_and_callbacks(self):
        logged_lines = []

        def mock_callback(msg: str, lvl: str):
            logged_lines.append((msg, lvl))

        stream = SafeStreamWriter(original_stream=None, log_callback=mock_callback, level="INFO")
        assert stream.write("Hello ") == 6
        assert len(logged_lines) == 0

        assert stream.write("World!\nSecond Line\n") == 19
        assert len(logged_lines) == 2
        assert logged_lines[0] == ("Hello World!", "INFO")
        assert logged_lines[1] == ("Second Line", "INFO")

        stream.write("Trailing")
        assert len(logged_lines) == 2
        stream.flush()
        assert len(logged_lines) == 3
        assert logged_lines[2] == ("Trailing", "INFO")

    def test_safe_stream_writer_methods_safety(self):
        stream = SafeStreamWriter(original_stream=None)
        assert stream.isatty() is False
        with pytest.raises(io.UnsupportedOperation):
            stream.fileno()

    def test_safe_stream_writer_with_real_underlying_stream(self):
        buf = io.StringIO()
        stream = SafeStreamWriter(original_stream=buf)
        stream.write("Testing output\n")
        assert buf.getvalue() == "Testing output\n"

    def test_setup_safe_streams_redirection(self):
        orig_out = sys.stdout
        orig_err = sys.stderr
        try:
            setup_safe_streams()
            assert isinstance(sys.stdout, SafeStreamWriter)
            assert isinstance(sys.stderr, SafeStreamWriter)
        finally:
            sys.stdout = orig_out
            sys.stderr = orig_err

# ============================================================================
# TIER 2: BOUNDARIES, CORNER CASES & ERROR HANDLING
# ============================================================================

class TestGuiBoundariesAndErrors:
    """Stress, corruption, cancellation, and permission boundary tests."""

    def test_empty_queue_worker_pool_start(self):
        pool = ConversionWorkerPool()
        ev_q = queue.Queue()
        cfg = AppConfig()
        assert pool.start_conversion([], cfg, ev_q) is False
        assert pool.is_running() is False

    @pytest.mark.parametrize(
        "bad_payload",
        [
            b"",
            b"JUNK",
            b"CON " + b"\x00" * 100,
            b"LIVE" + b"\xFF" * 500,
            b"PIRS" + b"\x55" * 1000,
            b"\x00" * 4096,
            b"CON \x00\x00\x00\x00" + b"\xFF" * 4096,
            b"PK\x03\x04" + b"\x00" * 4096,
            b"RIFF" + b"\x00" * 4096,
            b"OggS" + b"\x00" * 4096,
        ],
    )
    def test_corrupted_stfs_file_in_worker_pool(self, tmp_path, bad_payload):
        bad_con = tmp_path / f"corrupt_{len(bad_payload)}.con"
        bad_con.write_bytes(bad_payload)
        item = QueueItem(file_path=bad_con, title="Corrupt Chart")

        pool = ConversionWorkerPool()
        ev_q = queue.Queue()
        cfg = AppConfig(output_dir=str(tmp_path / "output"))

        assert pool.start_conversion([item], cfg, ev_q) is True

        events = []
        timeout = time.time() + 5.0
        while time.time() < timeout:
            try:
                ev = ev_q.get(timeout=0.2)
                events.append(ev)
                if ev.event_type == WorkerEventType.BATCH_FINISHED:
                    break
            except queue.Empty:
                pass

        error_events = [e for e in events if e.event_type == WorkerEventType.ITEM_ERROR]
        assert len(error_events) >= 1
        assert pool.is_running() is False

    def test_zero_byte_file_in_worker_pool(self, tmp_path):
        zero_con = tmp_path / "zero_file.con"
        zero_con.touch()
        item = QueueItem(file_path=zero_con, title="Zero Byte Chart")

        pool = ConversionWorkerPool()
        ev_q = queue.Queue()
        cfg = AppConfig(output_dir=str(tmp_path / "output_zero"))

        assert pool.start_conversion([item], cfg, ev_q) is True

        events = []
        timeout = time.time() + 5.0
        while time.time() < timeout:
            try:
                ev = ev_q.get(timeout=0.2)
                events.append(ev)
                if ev.event_type == WorkerEventType.BATCH_FINISHED:
                    break
            except queue.Empty:
                pass

        error_events = [e for e in events if e.event_type == WorkerEventType.ITEM_ERROR]
        assert len(error_events) >= 1
        assert pool.is_running() is False

    def test_high_volume_1000_items_queue_model_performance(self, tmp_path):
        model = QueueModel()
        t0 = time.perf_counter()
        items = []
        for i in range(1000):
            p = tmp_path / f"perf_song_{i:04d}.con"
            items.append(QueueItem(file_path=p, title=f"Song {i}", size_bytes=1024 * i))

        for it in items:
            model.add_item(it)

        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0
        assert len(model) == 1000
        assert model.counts()["total"] == 1000

        snapshot = model.get_items()
        assert len(snapshot) == 1000

        ids_to_remove = [it.id for it in items[:500]]
        removed = model.remove_selected(ids_to_remove)
        assert len(removed) == 500
        assert len(model) == 500

    def test_cancellation_mid_batch_clean_teardown(self, tmp_path):
        raw_con = build_complete_test_con(song_id="cancelsong", title="Cancel Song")
        items = []
        for i in range(50):
            p = tmp_path / f"cancel_{i}.con"
            p.write_bytes(raw_con)
            items.append(QueueItem(file_path=p, title=f"Song {i}"))

        pool = ConversionWorkerPool()
        ev_q = queue.Queue()
        cfg = AppConfig(output_dir=str(tmp_path / "out_cancel"), worker_threads=1)

        pool.start_conversion(items, cfg, ev_q)
        pool.cancel_conversion()

        batch_finished_ev = None
        timeout = time.time() + 10.0
        while time.time() < timeout:
            try:
                ev = ev_q.get(timeout=0.2)
                if ev.event_type == WorkerEventType.BATCH_FINISHED:
                    batch_finished_ev = ev
                    break
            except queue.Empty:
                pass

        assert batch_finished_ev is not None
        assert batch_finished_ev.data.get("cancelled") is True
        assert pool.is_running() is False

    def test_worker_pool_shutdown(self):
        pool = ConversionWorkerPool()
        pool.shutdown(wait=False)
        assert pool.is_running() is False

    @pytest.mark.parametrize(
        "raw_input",
        [
            "C:/songs/track1.con",
            b"C:/songs/track2.con",
            b"C:/songs/track3.con\x00",
            Path("C:/songs/track4.con"),
            "C:\\songs\\track5.con",
            b"C:\\songs\\track6.con\r\n",
            "  C:/songs/track7.con  ",
            b"  C:/songs/track8.con  ",
        ],
    )
    def test_dnd_decode_path_encodings(self, raw_input):
        res = _decode_dropped_path(raw_input)
        assert isinstance(res, Path)
        assert "track" in str(res)

    def test_dnd_hook_target_safe_fallback(self):
        dummy_widget = MagicMock()
        with patch.dict("sys.modules", {"windnd": None}):
            res = hook_drop_target(dummy_widget, lambda paths: None)
            assert isinstance(res, bool)

    @pytest.mark.parametrize(
        "corrupt_content",
        [
            "",
            "[]",
            "[1, 2, 3]",
            "12345",
            "true",
            "false",
            "null",
            "{{{invalid",
            "{\"output_dir\": 99999}",
            "{\"worker_threads\": -50}",
            "{\"worker_threads\": 9999}",
            "{\"stem_threads\": -10}",
            "{\"stem_threads\": 100}",
            "{\"dark_theme\": null}",
            "{\"overwrite\": \"not_a_bool\"}",
        ],
    )
    def test_config_manager_corrupt_payload_permutations(self, tmp_path, corrupt_content):
        cfg_file = tmp_path / "corrupt_test.json"
        cfg_file.write_text(corrupt_content, encoding="utf-8")
        mgr = ConfigManager(custom_path=cfg_file)
        loaded = mgr.load()
        assert isinstance(loaded, AppConfig)
        assert loaded.charter == "Dansla116"

    @pytest.mark.parametrize(
        "extreme_size,expected_str",
        [
            (-1000, "0 B"),
            (-1, "0 B"),
            (0, "0 B"),
            (1, "1 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (2048, "2.0 KB"),
            (1048576, "1.0 MB"),
            (15728640, "15.0 MB"),
            (1073741824, "1.00 GB"),
            (5368709120, "5.00 GB"),
            (1099511627776, "1024.00 GB"),
            (999999999999, "931.32 GB"),
            (100, "100 B"),
            (999, "999 B"),
        ],
    )
    def test_queue_item_extreme_file_sizes(self, tmp_path, extreme_size, expected_str):
        f = tmp_path / "extreme.con"
        item = QueueItem(file_path=f, size_bytes=extreme_size)
        assert item.formatted_size == expected_str

    @pytest.mark.parametrize(
        "rate_val,expected_rate_str",
        [
            (-100.0, "0.0 songs/s"),
            (-1.0, "0.0 songs/s"),
            (0.0, "0.0 songs/s"),
            (0.0001, "0.00 songs/s"),
            (0.01, "0.01 songs/s"),
            (0.5, "0.50 songs/s"),
            (1.0, "1.00 songs/s"),
            (9.99, "9.99 songs/s"),
            (10.0, "10.0 songs/s"),
            (14.89, "14.9 songs/s"),
            (99.9, "99.9 songs/s"),
            (100.0, "100.0 songs/s"),
            (500.0, "500.0 songs/s"),
            (10000.0, "10000.0 songs/s"),
            (float("nan"), "0.0 songs/s"),
        ],
    )
    def test_stats_tracker_extreme_rates(self, rate_val, expected_rate_str):
        res = format_throughput(rate_val)
        assert res == expected_rate_str

    @pytest.mark.parametrize(
        "junk_len",
        [0, 1, 2, 3, 4, 10, 50, 100, 512, 1024, 2048, 4095, 4096, 5000, 8192],
    )
    def test_scanner_corrupt_files_permutations(self, tmp_path, junk_len):
        f = tmp_path / f"junk_{junk_len}.pkg"
        f.write_bytes(b"\xFF" * junk_len)
        assert is_con_file(f) is False
        meta = peek_con_metadata(f)
        assert meta["title"] == f"junk_{junk_len}"
        assert meta["size_bytes"] == junk_len

# ============================================================================
# TIER 3: CONCURRENCY & CROSS-FEATURE INTERACTIONS
# ============================================================================

class TestGuiConcurrencyAndInteractions:
    """Cross-feature concurrency, settings changes, and retry state workflows."""

    def test_queue_additions_during_active_worker_run(self, tmp_path):
        model = QueueModel()
        raw_con = build_complete_test_con(song_id="conc_song", title="Concurrent Song")
        
        initial_files = [tmp_path / f"init_{i}.con" for i in range(4)]
        for f in initial_files:
            f.write_bytes(raw_con)
        init_items = model.add_files(initial_files)

        pool = ConversionWorkerPool()
        ev_q = queue.Queue()
        cfg = AppConfig(output_dir=str(tmp_path / "out_concurrent"), worker_threads=2)

        pool.start_conversion(init_items, cfg, ev_q)

        new_files = [tmp_path / f"new_{i}.con" for i in range(4)]
        for f in new_files:
            f.write_bytes(raw_con)
        new_items = model.add_files(new_files)
        assert len(new_items) == 4
        assert len(model) == 8

        timeout = time.time() + 10.0
        while time.time() < timeout:
            try:
                ev = ev_q.get(timeout=0.2)
                if ev.event_type == WorkerEventType.BATCH_FINISHED:
                    break
            except queue.Empty:
                pass

        pool.shutdown(wait=True)

    def test_dynamic_settings_persistence_across_runs(self, tmp_path):
        config_path = tmp_path / "settings_test.json"
        mgr = ConfigManager(custom_path=config_path)

        cfg1 = mgr.load()
        cfg1.charter = "CharterAlpha"
        cfg1.overwrite = False
        mgr.save(cfg1)

        cfg2 = mgr.load()
        assert cfg2.charter == "CharterAlpha"
        cfg2.charter = "CharterBeta"
        cfg2.overwrite = True
        mgr.save(cfg2)

        cfg3 = mgr.load()
        assert cfg3.charter == "CharterBeta"
        assert cfg3.overwrite is True

    def test_multithreaded_safe_stream_writer_concurrency(self):
        logged = []
        lock = threading.Lock()

        def cb(msg, lvl):
            with lock:
                logged.append((msg, lvl))

        writer = SafeStreamWriter(original_stream=None, log_callback=cb, level="INFO")

        def worker(t_idx):
            for i in range(50):
                writer.write(f"Thread {t_idx} Msg {i}\n")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(logged) == 200

    def test_retry_failed_preserves_done_items_across_runs(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"item_{i}.con" for i in range(4)]
        for f in files:
            f.touch()
        items = model.add_files(files)

        model.set_item_state(items[0].id, QueueItemState.DONE, elapsed_time_sec=1.2)
        model.set_item_state(items[1].id, QueueItemState.ERROR, error_message="Failed")
        model.set_item_state(items[2].id, QueueItemState.DONE, elapsed_time_sec=0.8)
        model.set_item_state(items[3].id, QueueItemState.SKIPPED)

        retried = model.retry_failed()
        assert len(retried) == 2
        pending = model.get_pending_items()
        assert len(pending) == 2
        assert items[0].state == QueueItemState.DONE
        assert items[2].state == QueueItemState.DONE

    @pytest.mark.parametrize(
        "states_seq",
        [
            [QueueItemState.CONVERTING, QueueItemState.DONE],
            [QueueItemState.CONVERTING, QueueItemState.SKIPPED],
            [QueueItemState.CONVERTING, QueueItemState.ERROR],
            [QueueItemState.CONVERTING, QueueItemState.REMOVED],
            [QueueItemState.PENDING, QueueItemState.REMOVED],
            [QueueItemState.ERROR, QueueItemState.PENDING, QueueItemState.DONE],
            [QueueItemState.SKIPPED, QueueItemState.PENDING, QueueItemState.DONE],
            [QueueItemState.CONVERTING, QueueItemState.ERROR, QueueItemState.PENDING],
        ],
    )
    def test_queue_item_state_transitions_matrix(self, tmp_path, states_seq):
        f = tmp_path / "matrix.con"
        f.touch()
        item = QueueItem(file_path=f)
        for st in states_seq:
            item.state = st
        assert item.state == states_seq[-1]

    def test_concurrent_queue_removals_and_queries(self, tmp_path):
        model = QueueModel()
        files = [tmp_path / f"c_rem_{i}.con" for i in range(100)]
        for f in files:
            f.touch()
        items = model.add_files(files)

        def remover():
            for it in items[:50]:
                model.remove_item(it.id)

        def reader():
            for _ in range(50):
                _ = model.get_items()
                _ = model.counts()

        t1 = threading.Thread(target=remover)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(model) == 50

    def test_multithreaded_config_manager_updates(self, tmp_path):
        cfg_file = tmp_path / "thread_config.json"
        mgr = ConfigManager(custom_path=cfg_file)
        mgr.save(AppConfig())

        def updater(idx: int):
            for i in range(10):
                cfg = mgr.load()
                cfg.charter = f"ThreadCharter_{idx}_{i}"
                mgr.save(cfg)

        threads = [threading.Thread(target=updater, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_cfg = mgr.load()
        assert "ThreadCharter_" in final_cfg.charter

    def test_concurrent_stats_tracker_recordings(self):
        tracker = LiveStatsTracker(window_size=20)
        tracker.start(total_items=100)

        def recorder(idx: int):
            for i in range(25):
                item_id = f"item_{idx}_{i}"
                tracker.record_item_started(item_id)
                tracker.record_item_finished(item_id, QueueItemState.DONE, elapsed_time=0.01)

        threads = [threading.Thread(target=recorder, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        m = tracker.get_metrics()
        assert m["processed"] == 100
        assert m["done"] == 100
        assert m["percent"] == 100.0

    def test_simulated_drag_and_drop_mixed_files_and_folders(self, tmp_path):
        # Folder with CON files
        con_dir = tmp_path / "AlbumFolder"
        con_dir.mkdir()
        con1 = con_dir / "song1.con"
        con1.write_bytes(bytearray(4096))
        con1.write_bytes(b"CON " + b"\x00" * 4092)

        # Standalone CON file
        con2 = tmp_path / "standalone.con"
        con2.write_bytes(b"LIVE" + b"\x00" * 4092)

        # Non-CON file
        txt = tmp_path / "notes.txt"
        txt.write_text("just text", encoding="utf-8")

        # Resolve paths
        all_resolved = []
        for raw in [str(con_dir), str(con2), str(txt)]:
            p = _decode_dropped_path(raw)
            if p.is_dir():
                all_resolved.extend(scan_directory_for_con_files(p, recursive=True))
            elif is_con_file(p):
                all_resolved.append(p)

        assert len(all_resolved) == 2
        assert any(p.name == "song1.con" for p in all_resolved)
        assert any(p.name == "standalone.con" for p in all_resolved)

# ============================================================================
# TIER 4: HEADLESS CUSTOMTKINTER LIFECYCLE & END-TO-END WORKFLOWS
# ============================================================================

class TestHeadlessCustomTkinterLifecycle:
    """Tests the full CustomTkinter widget hierarchy and complete simulated conversion workflow."""

    @classmethod
    def setup_class(cls):
        """Create single persistent headless FastConverterApp for class to avoid Tcl uninit error."""
        cls._tmp_dir = tempfile.mkdtemp()
        cfg_file = Path(cls._tmp_dir) / "class_config.json"
        cls.app = FastConverterApp(custom_config_path=cfg_file)
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def teardown_class(cls):
        try:
            cls.app.worker_pool.shutdown(wait=False)
            cls.app.destroy()
        except Exception:
            pass
        try:
            shutil.rmtree(cls._tmp_dir, ignore_errors=True)
        except Exception:
            pass

    def setup_method(self):
        """Reset queue and state before each test method."""
        self.app.queue_model.clear()
        self.app.queue_table.clear()
        self.app.stats_tracker.reset()
        self.app.progress_view.reset()
        self.app.update()

    def test_headless_app_widget_hierarchy(self):
        """Verify all 6 main view components are instantiated and wired."""
        assert isinstance(self.app.header_view, HeaderView)
        assert isinstance(self.app.settings_toolbar, SettingsToolbarView)
        assert isinstance(self.app.advanced_card, AdvancedSettingsCard)
        assert isinstance(self.app.queue_toolbar, QueueToolbarView)
        assert isinstance(self.app.queue_table, QueueTableView)
        assert isinstance(self.app.progress_view, GlobalProgressView)
        assert isinstance(self.app.log_console, LogConsoleView)
        self.app.update()

    def test_headless_app_toggle_advanced_settings(self):
        """Verify expandable advanced settings toggle card."""
        assert self.app._advanced_visible is False
        self.app._on_toggle_advanced()
        assert self.app._advanced_visible is True
        self.app.update()
        self.app._on_toggle_advanced()
        assert self.app._advanced_visible is False
        self.app.update()

    def test_headless_app_queue_toolbar_actions(self, tmp_path):
        """Verify queue addition, selection removal, and clear all via app controller."""
        f1 = tmp_path / "song1.con"
        f2 = tmp_path / "song2.con"
        f1.touch()
        f2.touch()

        added = self.app.queue_model.add_files([f1, f2])
        self.app.queue_table.add_items_lazy(added)
        self.app.update()

        assert len(self.app.queue_model) == 2
        assert len(self.app.queue_table.row_views) == 2

        self.app._on_clear_all()
        self.app.update()

        assert len(self.app.queue_model) == 0
        assert len(self.app.queue_table.row_views) == 0

    def test_headless_app_handle_dropped_paths(self, tmp_path):
        raw_con = build_complete_test_con(song_id="dropsong", title="Dropped Song")
        con_file = tmp_path / "DropTest.con"
        con_file.write_bytes(raw_con)

        self.app.handle_dropped_paths([con_file])
        self.app.update()

        assert len(self.app.queue_model) == 1
        assert self.app.queue_model.get_items()[0].title == "Dropped Song"

    def test_headless_app_log_console_append(self):
        self.app.log_console.append_log("Test log entry 123", level="INFO")
        self.app.update()
        txt = self.app.log_console.textbox.get("1.0", "end")
        assert "Test log entry 123" in txt

    def test_headless_app_progress_metrics_update(self):
        metrics = {
            "total": 5,
            "processed": 2,
            "remaining": 3,
            "done": 2,
            "skipped": 0,
            "error": 0,
            "percent": 40.0,
            "progress_fraction": 0.4,
            "throughput": 1.5,
            "throughput_str": "1.5 songs/s",
            "elapsed_sec": 10.0,
            "elapsed_str": "00:10",
            "eta_sec": 15.0,
            "eta_str": "00:15",
            "is_running": True,
        }
        self.app.progress_view.update_metrics(metrics)
        self.app.update()
        assert "2 / 5 songs" in self.app.progress_view.lbl_percent.cget("text")
        assert "40%" in self.app.progress_view.lbl_percent.cget("text")

    def test_headless_app_context_action_copy_path(self, tmp_path):
        f = tmp_path / "test_copy.con"
        f.touch()
        added = self.app.queue_model.add_files([f])
        self.app._on_context_action(added[0].id, "copy_path")
        self.app.update()

    def test_headless_app_settings_toolbar_callbacks(self, tmp_path):
        out = str(tmp_path / "custom_out")
        self.app.settings_toolbar.set_output_dir(out)
        assert self.app.settings_toolbar.get_output_dir() == out
        assert self.app.config.output_dir == out

    def test_end_to_end_simulated_conversion_producing_clone_hero_chart(self, tmp_path):
        """
        Full End-to-End Test:
        1. Ingest synthetic CON package into queue
        2. Set output directory
        3. Trigger conversion
        4. Poll UI event queue until batch completes
        5. Verify output chart folder, song.ini (with icon = fnf), notes.mid, and stem files.
        """
        raw_con = build_complete_test_con(
            song_id="buddyholly_gui",
            title="Buddy Holly (GUI Test)",
            artist="Weezer",
            album="Blue Album",
            year=1994,
        )
        con_path = tmp_path / "Buddy_Holly.con"
        con_path.write_bytes(raw_con)

        out_dir = tmp_path / "CloneHeroSongs"
        out_dir.mkdir()
        self.app.settings_toolbar.set_output_dir(str(out_dir))

        added = self.app.queue_model.add_files([con_path], metadata_peek_fn=peek_con_metadata)
        self.app.queue_table.add_items_lazy(added)
        self.app.update()
        assert len(added) == 1

        self.app._on_start_conversion()
        assert self.app.worker_pool.is_running() is True

        t_deadline = time.time() + 15.0
        while time.time() < t_deadline and self.app.worker_pool.is_running():
            self.app.update()
            time.sleep(0.05)

        self.app.update()
        assert self.app.worker_pool.is_running() is False

        item = self.app.queue_model.get_item(added[0].id)
        assert item is not None
        assert item.state == QueueItemState.DONE
        assert item.elapsed_time_sec > 0

        expected_chart_dir = out_dir / "Weezer - Buddy Holly (GUI Test)"
        assert expected_chart_dir.is_dir()

        song_ini = expected_chart_dir / "song.ini"
        notes_mid = expected_chart_dir / "notes.mid"
        assert song_ini.is_file()
        assert notes_mid.is_file()

    def test_headless_app_log_console_clear_and_toggle(self):
        self.app.log_console.append_log("Log line 1", level="INFO")
        self.app.log_console.append_log("Log line 2", level="ERROR")
        self.app.update()
        assert "Log line 1" in self.app.log_console.textbox.get("1.0", "end")

        self.app.log_console.clear_logs()
        self.app.update()
        assert self.app.log_console.textbox.get("1.0", "end").strip() == ""

        # Toggle collapse
        init_collapsed = self.app.log_console._is_collapsed
        self.app.log_console.toggle_collapse()
        assert self.app.log_console._is_collapsed != init_collapsed
        self.app.log_console.toggle_collapse()
        assert self.app.log_console._is_collapsed == init_collapsed

    def test_headless_app_advanced_card_controls(self):
        self.app.advanced_card.slider_workers.set(8)
        self.app.advanced_card._on_workers_changed(8)
        assert self.app.config.worker_threads == 8

        self.app.advanced_card.slider_stems.set(6)
        self.app.advanced_card._on_stems_changed(6)
        assert self.app.config.stem_threads == 6

        self.app.advanced_card.entry_charter.delete(0, "end")
        self.app.advanced_card.entry_charter.insert(0, "GuitarMaster")
        self.app.advanced_card._on_charter_changed()
        assert self.app.config.charter == "GuitarMaster"

    def test_headless_app_theme_mode_switching(self):
        self.app.advanced_card._on_theme_changed("Light")
        self.app.update()
        assert self.app.config.dark_theme == "Light"

        self.app.advanced_card._on_theme_changed("Dark")
        self.app.update()
        assert self.app.config.dark_theme == "Dark"

    def test_end_to_end_conversion_skipped_when_already_exists(self, tmp_path):
        raw_con = build_complete_test_con(
            song_id="exists_test",
            title="Already Exists Song",
            artist="Existing Artist",
        )
        con_path = tmp_path / "Exists_Test.con"
        con_path.write_bytes(raw_con)

        out_dir = tmp_path / "CloneHeroSongsExisting"
        out_dir.mkdir()
        # Pre-create valid converted chart files so is_valid_converted_song returns True
        chart_dir = out_dir / "Existing Artist - Already Exists Song"
        chart_dir.mkdir(parents=True)
        (chart_dir / "song.ini").write_text("[song]\nname = Already Exists Song\nicon = fnf\n", encoding="utf-8")
        (chart_dir / "notes.mid").write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")
        (chart_dir / "song.ogg").write_bytes(b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 100)

        self.app.settings_toolbar.set_output_dir(str(out_dir))
        self.app.settings_toolbar.var_overwrite.set(False)
        self.app.settings_toolbar._on_switch_overwrite()

        added = self.app.queue_model.add_files([con_path], metadata_peek_fn=peek_con_metadata)
        self.app.queue_table.add_items_lazy(added)
        self.app.update()

        self.app._on_start_conversion()
        t_deadline = time.time() + 15.0
        while time.time() < t_deadline and self.app.worker_pool.is_running():
            self.app.update()
            time.sleep(0.05)

        self.app.update()
        item = self.app.queue_model.get_item(added[0].id)
        assert item is not None
        assert item.state == QueueItemState.SKIPPED

    def test_headless_app_total_files_counter_updates(self, tmp_path):
        """Verify total files counter badge updates dynamically on additions, selections, deletions."""
        self.app._on_clear_all()
        assert "Total: 0 files" in self.app.queue_toolbar.lbl_counter.cget("text")

        f1 = tmp_path / "song1.con"
        f2 = tmp_path / "song2.con"
        f1.touch()
        f2.touch()

        added = self.app.queue_model.add_files([f1, f2])
        self.app.queue_table.add_items_lazy(added)
        self.app._update_queue_counts()
        self.app.update()

        assert "Total: 2 files" in self.app.queue_toolbar.lbl_counter.cget("text")
        assert "0 / 2 songs" in self.app.progress_view.lbl_percent.cget("text")

        # Select 1 item
        self.app.queue_table.row_views[added[0].id].set_selected(True)
        self.app.queue_table._handle_select_toggle(added[0].id, True)
        self.app.update()

        assert "Total: 2 files (1 selected)" in self.app.queue_toolbar.lbl_counter.cget("text")

        # Clear all
        self.app._on_clear_all()
        self.app.update()
        assert "Total: 0 files" in self.app.queue_toolbar.lbl_counter.cget("text")

    def test_headless_app_select_all_toggle_and_shortcuts(self, tmp_path):
        """Verify select all button, toggle behavior, and context menu actions."""
        f1 = tmp_path / "s1.con"
        f2 = tmp_path / "s2.con"
        f1.touch()
        f2.touch()

        added = self.app.queue_model.add_files([f1, f2])
        self.app.queue_table.add_items_lazy(added)
        self.app._update_queue_counts()
        self.app.update()

        assert len(self.app.queue_table.selected_ids) == 0
        assert self.app.queue_toolbar.btn_select_all.cget("text") == "☑ Select All"

        # Trigger Select All
        self.app._on_select_all()
        self.app.update()

        assert len(self.app.queue_table.selected_ids) == 2
        assert self.app.queue_toolbar.btn_select_all.cget("text") == "☐ Deselect All"
        assert "Total: 2 files (2 selected)" in self.app.queue_toolbar.lbl_counter.cget("text")

        # Trigger Deselect All
        self.app._on_select_all()
        self.app.update()

        assert len(self.app.queue_table.selected_ids) == 0
        assert self.app.queue_toolbar.btn_select_all.cget("text") == "☑ Select All"

        # Explicit deselect
        self.app._on_select_all()
        assert len(self.app.queue_table.selected_ids) == 2
        self.app._on_deselect_all()
        assert len(self.app.queue_table.selected_ids) == 0

    def test_headless_app_advanced_settings_collapse_and_row_layout(self):
        """Verify advanced settings card expands on dedicated row 2 and collapses cleanly."""
        assert self.app._advanced_visible is False

        # Open
        self.app._on_toggle_advanced()
        assert self.app._advanced_visible is True
        info = self.app.advanced_card.grid_info()
        assert info["row"] == 2
        assert "▴" in self.app.settings_toolbar.btn_advanced.cget("text")

        # Collapse via on_collapse callback on card itself
        assert self.app.advanced_card.on_collapse is not None
        self.app.advanced_card.on_collapse()
        assert self.app._advanced_visible is False
        assert "▾" in self.app.settings_toolbar.btn_advanced.cget("text")
        assert not self.app.advanced_card.winfo_ismapped()



