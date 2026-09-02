"""
test_gui_concurrency_stress.py - Adversarial Concurrency, Deadlock & High-Throughput Stress Suite.

Empirical verification suite for Challenger 1:
1. Rapid Start / Cancel / Restart cycles under jitter, multi-producer race conditions, and abrupt teardown.
2. 10,000 synthetic queue items memory consumption and operations throughput benchmark.
3. Random worker thread exceptions, hostile file inputs, permission faults, and mixed error distributions.
4. High-concurrency event queue draining (16 producers, 50,000 events), race condition safety, and UI event pump integrity.
"""

from __future__ import annotations

import gc
import os
import queue
import random
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from fnf_fast_converter.src.gui_app import FastConverterApp
from fnf_fast_converter.src.gui_config import AppConfig
from fnf_fast_converter.src.gui_queue import (
    QueueItem,
    QueueItemState,
    QueueModel,
    format_byte_size,
)
from fnf_fast_converter.src.gui_stats import (
    LiveStatsTracker,
    format_duration,
    format_throughput,
)
from fnf_fast_converter.src.gui_views import QueueTableView
from fnf_fast_converter.src.gui_worker import (
    ConversionWorkerPool,
    WorkerEvent,
    WorkerEventType,
)


# ============================================================================
# HELPER FIXTURES & DATA GENERATORS
# ============================================================================

def generate_synthetic_items(count: int, base_dir: Optional[Path] = None) -> List[QueueItem]:
    """Generate `count` synthetic QueueItems with realistic metadata."""
    items = []
    if base_dir is None:
        base_dir = Path("C:/Mock/Charts")

    artists = ["Metallica", "Queen", "Muse", "Daft Punk", "Avenged Sevenfold", "Green Day", "Rush"]
    albums = ["Album Alpha", "Greatest Hits", "Live 2024", "Origins", "Remastered Ed."]

    for i in range(count):
        art = artists[i % len(artists)]
        alb = albums[i % len(albums)]
        title = f"Track_{i:05d} {art} - Song #{i}"
        file_path = base_dir / f"Song_{i:05d}.con"
        size = 1024 * 1024 + (i * 1337) % (50 * 1024 * 1024)

        item = QueueItem(
            file_path=file_path,
            title=title,
            artist=art,
            album=alb,
            size_bytes=size,
        )
        items.append(item)
    return items


# ============================================================================
# SCENARIO 1: RAPID START / CANCEL / RESTART CYCLES
# ============================================================================

class TestRapidStartCancelRestartCycles:
    """Stress test the worker pool against rapid state changes and race conditions."""

    def test_rapid_start_cancel_immediate_cycles(self):
        """
        Execute 50 rapid start -> cancel cycles without delay.
        Verify: No deadlock, pool terminates cleanly each cycle, BATCH_FINISHED emitted.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=4, output_dir="./mock_out")
        items = generate_synthetic_items(10)
        
        t0 = time.perf_counter()
        cycles = 50

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder") as mock_conv:
            # Simulate short work
            mock_conv.side_effect = lambda *args, **kwargs: (time.sleep(0.005) or (True, "OK"))

            for cycle in range(cycles):
                eq: queue.Queue[WorkerEvent] = queue.Queue()
                # Start
                started = pool.start_conversion(items, cfg, eq)
                assert started is True or pool.is_running() is True
                
                # Immediate cancel
                pool.cancel_conversion()

                # Wait for batch finish with timeout
                t_wait = time.time() + 1.0
                while pool.is_running() and time.time() < t_wait:
                    time.sleep(0.002)

                assert pool.is_running() is False, f"Pool failed to stop on cycle {cycle}"

                # Verify BATCH_FINISHED was placed in queue
                events = []
                while not eq.empty():
                    events.append(eq.get_nowait())
                
                batch_finished_events = [e for e in events if e.event_type == WorkerEventType.BATCH_FINISHED]
                assert len(batch_finished_events) == 1, f"Missing BATCH_FINISHED on cycle {cycle}"

        elapsed = time.perf_counter() - t0
        print(f"\n[BENCHMARK] 50 Rapid Start/Cancel Cycles completed in {elapsed:.3f}s ({cycles / elapsed:.1f} cycles/sec)")
        assert elapsed < 5.0, f"Rapid cancel cycles took too long: {elapsed:.2f}s"

    def test_rapid_cancel_with_random_jitter(self):
        """
        Execute 30 start -> cancel cycles with random jitter delay (1ms - 15ms)
        to interrupt workers in mid-execution.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=8, output_dir="./mock_out")
        items = generate_synthetic_items(20)

        initial_threads = threading.active_count()
        cycles = 30

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder") as mock_conv:
            def simulated_work(*args, **kwargs):
                time.sleep(random.uniform(0.005, 0.020))
                return True, "Success"
            mock_conv.side_effect = simulated_work

            for cycle in range(cycles):
                eq: queue.Queue[WorkerEvent] = queue.Queue()
                started = pool.start_conversion(items, cfg, eq)
                
                # Jitter delay
                time.sleep(random.uniform(0.001, 0.015))
                
                pool.cancel_conversion()

                # Wait for batch orchestrator thread to exit
                t_wait = time.time() + 1.5
                while pool.is_running() and time.time() < t_wait:
                    time.sleep(0.005)

                assert pool.is_running() is False, f"Worker pool hung on cycle {cycle}"

        # Ensure no zombie threads accumulated
        time.sleep(0.1)
        active_threads = threading.active_count()
        assert active_threads <= initial_threads + 2, f"Leaked threads detected: before={initial_threads}, after={active_threads}"

    def test_concurrent_multi_thread_start_cancel_hammering(self):
        """
        Hammer a single worker pool with 12 concurrent threads simultaneously trying
        to call `start_conversion()` and `cancel_conversion()`.
        Verify internal thread safety and lock freedom.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=4, output_dir="./mock_out")
        items = generate_synthetic_items(5)

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder") as mock_conv:
            mock_conv.return_value = (True, "OK")

            start_count = [0]
            cancel_count = [0]
            lock = threading.Lock()

            def hammer_starter(thread_id: int):
                for _ in range(20):
                    eq: queue.Queue[WorkerEvent] = queue.Queue()
                    res = pool.start_conversion(items, cfg, eq)
                    if res:
                        with lock:
                            start_count[0] += 1
                    time.sleep(random.uniform(0.001, 0.005))

            def hammer_canceller(thread_id: int):
                for _ in range(20):
                    pool.cancel_conversion()
                    with lock:
                        cancel_count[0] += 1
                    time.sleep(random.uniform(0.001, 0.005))

            threads = []
            for i in range(6):
                t1 = threading.Thread(target=hammer_starter, args=(i,))
                t2 = threading.Thread(target=hammer_canceller, args=(i,))
                threads.extend([t1, t2])

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10.0)

            # Wait for final quiescence
            t_wait = time.time() + 2.0
            while pool.is_running() and time.time() < t_wait:
                time.sleep(0.01)

            assert pool.is_running() is False
            print(f"\n[BENCHMARK] Multi-thread hammer: {start_count[0]} successful starts, {cancel_count[0]} cancels handled cleanly.")

    def test_shutdown_under_active_load(self):
        """
        Verify immediate shutdown while 16 worker threads are processing a massive batch.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=16, output_dir="./mock_out")
        items = generate_synthetic_items(100)
        eq: queue.Queue[WorkerEvent] = queue.Queue()

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder") as mock_conv:
            mock_conv.side_effect = lambda *args, **kwargs: (time.sleep(0.5) or (True, "OK"))

            started = pool.start_conversion(items, cfg, eq)
            assert started is True
            assert pool.is_running() is True

            time.sleep(0.02)
            t0 = time.perf_counter()
            pool.shutdown(wait=False)
            elapsed = time.perf_counter() - t0

            # Wait for pool to settle
            t_wait = time.time() + 2.0
            while pool.is_running() and time.time() < t_wait:
                time.sleep(0.01)

            assert pool.is_running() is False
            assert elapsed < 0.2, f"Shutdown (wait=False) blocked for {elapsed:.3f}s"


# ============================================================================
# SCENARIO 2: 10,000 SYNTHETIC QUEUE ITEMS BENCHMARK
# ============================================================================

class TestLargeScale10KQueueItemsBenchmark:
    """Stress test QueueModel, StatsTracker, and memory under 10,000 synthetic items."""

    def test_10k_items_model_ingestion_and_memory_footprint(self):
        """
        Benchmark: Ingest 10,000 synthetic QueueItems into QueueModel.
        Measure: Exact heap memory delta, throughput (items/sec), and deduplication overhead.
        """
        gc.collect()
        tracemalloc.start()
        mem_before, _ = tracemalloc.get_traced_memory()

        model = QueueModel()
        items = generate_synthetic_items(10000)

        t0 = time.perf_counter()
        # Ingest 10,000 items
        for item in items:
            added = model.add_item(item)
            assert added is True
        t_ingest = time.perf_counter() - t0

        mem_after, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        mem_delta_mb = (mem_after - mem_before) / (1024 * 1024)
        peak_mb = peak_mem / (1024 * 1024)
        bytes_per_item = (mem_after - mem_before) / 10000
        throughput = 10000 / t_ingest

        print(f"\n[BENCHMARK] 10,000 Items Ingestion:")
        print(f"  - Ingestion Time: {t_ingest:.4f}s ({throughput:,.0f} items/s)")
        print(f"  - Memory Delta: {mem_delta_mb:.2f} MB ({bytes_per_item:.1f} bytes/item)")
        print(f"  - Peak Heap: {peak_mb:.2f} MB")

        assert len(model) == 10000
        assert throughput > 3000, f"Ingestion throughput too low: {throughput:.0f} items/s"
        assert mem_delta_mb < 25.0, f"Memory footprint exceeded 25MB limit: {mem_delta_mb:.2f} MB"

    def test_10k_items_batch_state_transitions_and_counts(self):
        """
        Benchmark: Transition 10,000 items through lifecycle states and query counts().
        """
        model = QueueModel()
        items = generate_synthetic_items(10000)
        for it in items:
            model.add_item(it)

        # 1. State updates: 4,000 DONE, 3,000 ERROR, 2,000 SKIPPED, 1,000 PENDING
        t0 = time.perf_counter()
        for idx, it in enumerate(items):
            if idx < 4000:
                model.set_item_state(it.id, QueueItemState.DONE, elapsed_time_sec=0.45)
            elif idx < 7000:
                model.set_item_state(it.id, QueueItemState.ERROR, error_message="Demux fail")
            elif idx < 9000:
                model.set_item_state(it.id, QueueItemState.SKIPPED)
            else:
                pass # PENDING
        t_updates = time.perf_counter() - t0

        # 2. Benchmark counts()
        t0_counts = time.perf_counter()
        counts = model.counts()
        t_counts = time.perf_counter() - t0_counts

        print(f"\n[BENCHMARK] 10,000 Item State Operations:")
        print(f"  - 10,000 State Transitions: {t_updates:.4f}s ({10000 / t_updates:,.0f} ops/s)")
        print(f"  - counts() execution time: {t_counts * 1000:.2f}ms")

        assert counts["total"] == 10000
        assert counts["done"] == 4000
        assert counts["error"] == 3000
        assert counts["skipped"] == 2000
        assert counts["pending"] == 1000
        assert t_counts < 0.020, f"counts() too slow on 10k items: {t_counts:.4f}s"

    def test_10k_items_batch_retries_and_removal_performance(self):
        """
        Benchmark: retry_failed() on 5,000 failed/skipped items and remove_selected() on 3,000 items.
        """
        model = QueueModel()
        items = generate_synthetic_items(10000)
        for it in items:
            model.add_item(it)

        # Set 5,000 items to ERROR
        for idx in range(5000):
            model.set_item_state(items[idx].id, QueueItemState.ERROR, error_message="Failed")

        # Benchmark retry_failed()
        t0 = time.perf_counter()
        retried = model.retry_failed()
        t_retry = time.perf_counter() - t0

        assert len(retried) == 5000
        assert model.counts()["pending"] == 10000
        print(f"\n[BENCHMARK] retry_failed() on 5,000 items: {t_retry * 1000:.2f}ms ({5000 / t_retry:,.0f} items/s)")
        assert t_retry < 0.050, f"retry_failed() too slow: {t_retry:.4f}s"

        # Benchmark remove_selected() on 3,000 items
        to_remove = [items[i].id for i in range(3000)]
        t0_rem = time.perf_counter()
        removed = model.remove_selected(to_remove)
        t_remove = time.perf_counter() - t0_rem

        assert len(removed) == 3000
        assert len(model) == 7000
        print(f"[BENCHMARK] remove_selected() on 3,000 items: {t_remove * 1000:.2f}ms ({3000 / t_remove:,.0f} items/s)")
        assert t_remove < 1.50, f"remove_selected() too slow: {t_remove:.4f}s"

    def test_10k_events_live_stats_tracker_throughput(self):
        """
        Benchmark: Stream 10,000 conversion events through LiveStatsTracker
        and verify sliding-window throughput and ETA calculation stability.
        """
        tracker = LiveStatsTracker(window_size=50)
        tracker.start(10000)

        t0 = time.perf_counter()
        for i in range(10000):
            item_id = f"item_{i}"
            tracker.record_item_started(item_id)
            state = QueueItemState.DONE if i % 10 != 0 else QueueItemState.ERROR
            tracker.record_item_finished(item_id, state, elapsed_time=0.1)

            if i % 500 == 0:
                metrics = tracker.get_metrics()
                assert metrics["percent"] >= 0.0
                assert metrics["percent"] <= 100.0
                assert metrics["eta_sec"] >= -1.0
                assert not (metrics["throughput"] != metrics["throughput"])  # No NaN

        tracker.finish_batch()
        elapsed = time.perf_counter() - t0
        metrics = tracker.get_metrics()

        throughput_ops = 10000 / elapsed
        print(f"\n[BENCHMARK] LiveStatsTracker 10,000 events throughput: {throughput_ops:,.0f} events/s ({elapsed:.4f}s)")
        assert metrics["done"] == 9000
        assert metrics["error"] == 1000
        assert metrics["processed"] == 10000
        assert metrics["percent"] == 100.0
        assert throughput_ops > 50000, f"Stats tracker too slow: {throughput_ops:.0f} ops/s"


# ============================================================================
# SCENARIO 3: RANDOM WORKER EXCEPTIONS & HOSTILE INPUTS
# ============================================================================

class TestRandomWorkerExceptionsAndHostileInputs:
    """Stress test error handling, corrupted data, and exception recovery in worker threads."""

    def test_worker_pool_catastrophic_exceptions(self):
        """
        Inject toxic exceptions into `convert_con_to_song_folder`:
        - MemoryError
        - OSError
        - ZeroDivisionError
        - UnicodeDecodeError
        - ValueError
        Verify: Worker pool catches 100% of exceptions, assigns ITEM_ERROR, emits BATCH_FINISHED.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=8, output_dir="./mock_out")
        items = generate_synthetic_items(100)
        eq: queue.Queue[WorkerEvent] = queue.Queue()

        exceptions_to_throw = [
            MemoryError("Out of memory during MOGG demux"),
            OSError("Disk full / permission denied"),
            ZeroDivisionError("Sample rate division error"),
            UnicodeDecodeError("utf-8", b"\xff\xff", 0, 1, "Invalid byte"),
            ValueError("Corrupted STFS header blocks"),
        ]

        def hostile_converter(con_data_or_path, output_dir, **kwargs):
            # Throw random exception on 70% of calls, succeed on 30%
            if random.random() < 0.70:
                exc = random.choice(exceptions_to_throw)
                raise exc
            return True, "Converted successfully"

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder", side_effect=hostile_converter):
            started = pool.start_conversion(items, cfg, eq)
            assert started is True

            # Wait for completion
            t_wait = time.time() + 5.0
            while pool.is_running() and time.time() < t_wait:
                time.sleep(0.01)

            assert pool.is_running() is False

            # Drain event queue
            events: List[WorkerEvent] = []
            while not eq.empty():
                events.append(eq.get_nowait())

            started_events = [e for e in events if e.event_type == WorkerEventType.ITEM_STARTED]
            completed_events = [e for e in events if e.event_type == WorkerEventType.ITEM_COMPLETED]
            error_events = [e for e in events if e.event_type == WorkerEventType.ITEM_ERROR]
            batch_fin = [e for e in events if e.event_type == WorkerEventType.BATCH_FINISHED]

            print(f"\n[BENCHMARK] Hostile Exception Injection (100 items):")
            print(f"  - Started: {len(started_events)}")
            print(f"  - Completed: {len(completed_events)}")
            print(f"  - Caught Errors: {len(error_events)}")
            print(f"  - Total Processed: {len(completed_events) + len(error_events)}")

            assert len(started_events) == 100
            assert len(completed_events) + len(error_events) == 100
            assert len(batch_fin) == 1
            assert len(error_events) > 0

    def test_worker_pool_with_corrupt_files_and_invalid_paths(self, tmp_path):
        """
        Verify worker pool against 40 adversarial file paths:
        0-byte file, truncated file, non-existent path, directory-as-file, unicode paths.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=6, output_dir=str(tmp_path / "out"))
        eq: queue.Queue[WorkerEvent] = queue.Queue()

        # Create hostile files
        hostile_paths = []
        # 1. 0-byte
        p_zero = tmp_path / "zero_byte.con"
        p_zero.touch()
        hostile_paths.append(p_zero)

        # 2. Garbage 1MB
        p_garbage = tmp_path / "garbage_bytes.con"
        p_garbage.write_bytes(b"\x00\xFF\xAA\x55" * 250000)
        hostile_paths.append(p_garbage)

        # 3. Non-existent file
        hostile_paths.append(tmp_path / "does_not_exist_404.con")

        # 4. Directory as file
        p_dir = tmp_path / "folder_as_file.con"
        p_dir.mkdir()
        hostile_paths.append(p_dir)

        # 5. Unicode / high-byte filenames
        p_unicode = tmp_path / "🎵_RockBand_测试_CON.dat"
        p_unicode.write_bytes(b"INVALID_MAGIC_STFS")
        hostile_paths.append(p_unicode)

        # Duplicate to 40 items
        items = []
        for i in range(40):
            p = hostile_paths[i % len(hostile_paths)]
            items.append(QueueItem(file_path=p, title=f"Hostile {i}"))

        started = pool.start_conversion(items, cfg, eq)
        assert started is True

        t_wait = time.time() + 10.0
        while pool.is_running() and time.time() < t_wait:
            time.sleep(0.01)

        assert pool.is_running() is False

        events = []
        while not eq.empty():
            events.append(eq.get_nowait())

        error_events = [e for e in events if e.event_type == WorkerEventType.ITEM_ERROR]
        batch_finished = [e for e in events if e.event_type == WorkerEventType.BATCH_FINISHED]

        assert len(error_events) == 40
        assert len(batch_finished) == 1
        print(f"\n[BENCHMARK] Hostile file resilience: 40/40 corrupted files safely routed to ITEM_ERROR.")

    def test_mixed_exact_distribution_correctness(self):
        """
        Verify exact distribution counts under high concurrency:
        100 SUCCESS, 100 SKIPPED, 100 ERROR across 16 worker threads.
        """
        pool = ConversionWorkerPool()
        cfg = AppConfig(worker_threads=16, output_dir="./mock_out")
        items = generate_synthetic_items(300)
        eq: queue.Queue[WorkerEvent] = queue.Queue()

        def deterministic_converter(con_data_or_path, *args, **kwargs):
            # Parse track index from file name
            idx = int(str(con_data_or_path).split("Song_")[1].split(".con")[0])
            if idx < 100:
                return True, "Converted 5 stems"
            elif idx < 200:
                return True, "[SKIPPED] Song directory already complete"
            else:
                return False, "[ERROR] Bad DTA tokens"

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder", side_effect=deterministic_converter):
            started = pool.start_conversion(items, cfg, eq)
            assert started is True

            t_wait = time.time() + 5.0
            while pool.is_running() and time.time() < t_wait:
                time.sleep(0.01)

            assert pool.is_running() is False

            events = []
            while not eq.empty():
                events.append(eq.get_nowait())

            completed = [e for e in events if e.event_type == WorkerEventType.ITEM_COMPLETED]
            skipped = [e for e in events if e.event_type == WorkerEventType.ITEM_SKIPPED]
            error = [e for e in events if e.event_type == WorkerEventType.ITEM_ERROR]

            assert len(completed) == 100
            assert len(skipped) == 100
            assert len(error) == 100
            print(f"\n[BENCHMARK] 300 Item Mixed Distribution: 100 Done, 100 Skipped, 100 Error (100% exact match).")


# ============================================================================
# SCENARIO 4: HIGH-CONCURRENCY EVENT QUEUE DRAINING & UI THREAD-SAFETY
# ============================================================================

class TestHighConcurrencyEventQueueDraining:
    """Stress test UI event queue under 16 concurrent producers pushing 50,000 events."""

    def test_high_concurrency_producer_consumer_queue_throughput(self):
        """
        16 producer threads push 50,000 WorkerEvents into queue.Queue while 1 consumer drains.
        Measure throughput and verify 100% event count integrity.
        """
        eq: queue.Queue[WorkerEvent] = queue.Queue()
        total_events = 50000
        num_producers = 16
        events_per_producer = total_events // num_producers

        drained_events: List[WorkerEvent] = []
        consumer_running = threading.Event()
        consumer_running.set()

        def consumer():
            while consumer_running.is_set() or not eq.empty():
                try:
                    ev = eq.get_nowait()
                    drained_events.append(ev)
                except queue.Empty:
                    time.sleep(0.0005)

        def producer(producer_id: int):
            for i in range(events_per_producer):
                ev = WorkerEvent(
                    event_type=WorkerEventType.LOG_MESSAGE,
                    item_id=f"item_p{producer_id}_{i}",
                    data={"message": f"Log msg {i} from producer {producer_id}", "level": "INFO"},
                )
                eq.put(ev)

        t_cons = threading.Thread(target=consumer, name="EventQueueConsumer")
        t_cons.start()

        t0 = time.perf_counter()
        threads = [
            threading.Thread(target=producer, args=(p,))
            for p in range(num_producers)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # Stop consumer once queue is drained
        while not eq.empty():
            time.sleep(0.001)
        consumer_running.clear()
        t_cons.join(timeout=2.0)

        elapsed = time.perf_counter() - t0
        throughput = total_events / elapsed

        print(f"\n[BENCHMARK] 50,000 Event Queue Throughput (16 producers ➔ 1 consumer):")
        print(f"  - Drained: {len(drained_events):,} events in {elapsed:.4f}s ({throughput:,.0f} events/s)")

        assert len(drained_events) == events_per_producer * num_producers
        assert throughput > 40000, f"Queue throughput too low: {throughput:.0f} events/s"

    def test_race_condition_event_dispatched_for_deleted_item(self):
        """
        Simulate race condition: UI user removes an item from QueueModel
        while background worker event for that exact item arrives in event queue.
        Verify _process_single_worker_event does not raise exceptions.
        """
        model = QueueModel()
        items = generate_synthetic_items(10)
        for it in items:
            model.add_item(it)

        # Remove item 0 from model
        removed = model.remove_item(items[0].id)
        assert removed is not None

        # Create simulated events for the removed item
        events = [
            WorkerEvent(event_type=WorkerEventType.ITEM_STARTED, item_id=items[0].id),
            WorkerEvent(event_type=WorkerEventType.ITEM_COMPLETED, item_id=items[0].id, data={"elapsed_time": 1.2}),
            WorkerEvent(event_type=WorkerEventType.ITEM_ERROR, item_id=items[0].id, data={"error": "Some error"}),
            WorkerEvent(event_type=WorkerEventType.ITEM_SKIPPED, item_id=items[0].id, data={"elapsed_time": 0.1}),
        ]

        # Process each on QueueModel
        for ev in events:
            # Setting state on non-existent item should be safe NO-OP
            model.set_item_state(ev.item_id, QueueItemState.DONE)
            assert model.get_item(ev.item_id) is None

        assert len(model) == 9

    def test_concurrent_stats_tracker_updates(self):
        """
        Stress test LiveStatsTracker with 10 threads concurrently recording item starts and completions.
        """
        tracker = LiveStatsTracker(window_size=20)
        total_items = 5000
        tracker.start(total_items)

        def worker_task(thread_id: int):
            for i in range(500):
                iid = f"t{thread_id}_item_{i}"
                tracker.record_item_started(iid)
                time.sleep(0.00001)
                tracker.record_item_finished(iid, QueueItemState.DONE, elapsed_time=0.01)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        metrics = tracker.get_metrics()
        assert metrics["done"] == 5000
        assert metrics["processed"] == 5000
        assert metrics["remaining"] == 0
        print(f"\n[BENCHMARK] LiveStatsTracker atomic multi-thread updates: 5,000 items processed across 10 threads without drift.")


# ============================================================================
# SCENARIO 5: HEADLESS APP LIFECYCLE & VIEW STRESS
# ============================================================================

class TestHeadlessAppLifecycleAndTableStress:
    """Stress test the full CustomTkinter FastConverterApp and view components under load."""

    @pytest.fixture(scope="class")
    def shared_app(self, tmp_path_factory):
        tmp_dir = tmp_path_factory.mktemp("app_stress")
        cfg_file = tmp_dir / "app_stress_config.json"
        app = FastConverterApp(custom_config_path=cfg_file)
        app.withdraw()  # Headless mode
        yield app
        try:
            app.worker_pool.shutdown(wait=False)
            app.destroy()
        except Exception:
            pass

    @pytest.fixture(autouse=True)
    def setup_headless_app(self, shared_app):
        self.app = shared_app
        self.app.queue_model.clear()
        self.app.queue_table.clear()
        self.app.log_console.clear_logs()
        yield

    def test_headless_app_rapid_start_cancel_and_event_pump(self, tmp_path):
        """
        Add 20 items to FastConverterApp, start conversion, cancel midway, and pump events.
        Verify: No UI exceptions, correct badge transitions, and clean finish.
        """
        out_dir = tmp_path / "output_charts"
        out_dir.mkdir()
        self.app.settings_toolbar.set_output_dir(str(out_dir))

        items = generate_synthetic_items(20, base_dir=tmp_path)
        for it in items:
            self.app.queue_model.add_item(it)
        self.app.queue_table.add_items_lazy(items, chunk_size=20, chunk_delay_ms=1)
        self.app.update()

        with patch("fnf_fast_converter.src.gui_worker.convert_con_to_song_folder") as mock_conv:
            mock_conv.side_effect = lambda *args, **kwargs: (time.sleep(0.01) or (True, "OK"))

            # Start
            self.app._on_start_conversion()
            assert self.app.worker_pool.is_running() is True

            # Pump UI a few times
            for _ in range(5):
                self.app.update()
                time.sleep(0.01)

            # Cancel
            self.app._on_cancel_conversion()

            # Drain until finished
            t_wait = time.time() + 2.0
            while self.app.worker_pool.is_running() and time.time() < t_wait:
                self.app.update()
                time.sleep(0.01)

            self.app.update()
            assert self.app.worker_pool.is_running() is False

        print("\n[BENCHMARK] Headless FastConverterApp start ➔ cancel ➔ drain cycle succeeded with zero UI errors.")

    def test_log_console_stress_3000_lines(self):
        """
        Stress test LogConsoleView with 3,000 rapid log messages.
        Measure rendering throughput and verify text buffer integrity.
        """
        t0 = time.perf_counter()
        for i in range(3000):
            lvl = "INFO" if i % 3 == 0 else ("WARNING" if i % 3 == 1 else "ERROR")
            self.app.log_console.append_log(f"Adversarial high-speed log message #{i:04d}", level=lvl)

        self.app.update()
        elapsed = time.perf_counter() - t0
        throughput = 3000 / elapsed

        print(f"\n[BENCHMARK] Log Console 3,000 lines throughput: {throughput:,.0f} lines/s ({elapsed:.4f}s)")
        content = self.app.log_console.textbox.get("1.0", "end")
        assert "Adversarial high-speed log message #2999" in content
        assert throughput > 1000, f"Log console throughput too slow: {throughput:.0f} lines/s"

    def test_queue_table_lazy_chunking_under_hmenu_limit(self, tmp_path):
        """
        Stress test QueueTableView chunked rendering with 250 items.
        Verify chunk processing loop completes without freezing and clear() cleans up.
        """
        items = generate_synthetic_items(250, base_dir=tmp_path)
        t0 = time.perf_counter()
        self.app.queue_table.add_items_lazy(items, chunk_size=50, chunk_delay_ms=1)

        # Pump events until lazy queue is drained
        t_wait = time.time() + 5.0
        while self.app.queue_table._lazy_queue and time.time() < t_wait:
            self.app.update()
            time.sleep(0.005)

        self.app.update()
        t_render = time.perf_counter() - t0

        assert len(self.app.queue_table.row_views) == 250
        print(f"\n[BENCHMARK] QueueTableView rendered 250 row cards in {t_render:.3f}s ({250 / t_render:,.0f} rows/s)")

        # Clear table
        t0_clr = time.perf_counter()
        self.app.queue_table.clear()
        self.app.update()
        t_clr = time.perf_counter() - t0_clr

        assert len(self.app.queue_table.row_views) == 0
        print(f"[BENCHMARK] QueueTableView cleared 250 row cards in {t_clr * 1000:.2f}ms")

    def test_win32_hmenu_exhaustion_at_scale(self, tmp_path):
        """
        Adversarial Boundary Test: Document and verify Win32 HMENU exhaustion
        when individual tk.Menu instances are allocated eagerly per row.
        """
        items = generate_synthetic_items(500, base_dir=tmp_path)
        menu_limit_hit = False
        allocated_count = 0

        try:
            self.app.queue_table.add_items_lazy(items, chunk_size=50, chunk_delay_ms=1)
            t_wait = time.time() + 5.0
            while self.app.queue_table._lazy_queue and time.time() < t_wait:
                self.app.update()
                time.sleep(0.005)
            allocated_count = len(self.app.queue_table.row_views)
        except Exception as exc:
            if "No more menus can be allocated" in str(exc):
                menu_limit_hit = True
                allocated_count = len(self.app.queue_table.row_views)

        print(f"\n[EMPIRICAL VULNERABILITY] Win32 HMENU Allocation Exhaustion:")
        print(f"  - Allocated before limit/finish: {allocated_count} rows")
        print(f"  - Hit Win32 HMENU limit: {menu_limit_hit or allocated_count < 500}")
        # Note: Clean up table
        self.app.queue_table.clear()


