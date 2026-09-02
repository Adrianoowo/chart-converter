"""
gui_worker.py - Non-Blocking Multithreaded Conversion Engine & UI Event Dispatcher.

Executes batch song conversions across a ThreadPoolExecutor in the background while
posting real-time events to a thread-safe UI message queue.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .gui_config import AppConfig
from .gui_queue import QueueItem, QueueItemState
from .pipeline import convert_con_to_song_folder

logger = logging.getLogger(__name__)


class WorkerEventType(str, Enum):
    """Event types passed from conversion worker threads to the UI thread."""

    ITEM_STARTED = "item_started"
    ITEM_COMPLETED = "item_completed"
    ITEM_SKIPPED = "item_skipped"
    ITEM_ERROR = "item_error"
    BATCH_FINISHED = "batch_finished"
    LOG_MESSAGE = "log_message"


@dataclass
class WorkerEvent:
    """Carries event payload from background worker pool to the UI polling loop."""

    event_type: WorkerEventType
    item_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


class ConversionWorkerPool:
    """
    Manages background thread execution of CON song conversions without blocking the UI.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._is_running = False
        self._orchestrator_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

    def is_running(self) -> bool:
        """Return True if background conversions are currently running."""
        with self._lock:
            return self._is_running

    def start_conversion(
        self,
        items: Sequence[QueueItem],
        config: AppConfig,
        event_queue: queue.Queue[WorkerEvent],
    ) -> bool:
        """
        Start converting the given list of QueueItems asynchronously.
        Returns True if started, False if already running or items list is empty.
        """
        with self._lock:
            if self._is_running:
                logger.warning("Conversion worker pool is already running.")
                return False

            if not items:
                logger.info("No items provided to conversion worker pool.")
                return False

            self._cancel_event.clear()
            self._is_running = True

            # Snapshot items to convert
            items_to_process = list(items)

            self._orchestrator_thread = threading.Thread(
                target=self._run_batch,
                args=(items_to_process, config, event_queue),
                name="ConversionOrchestratorThread",
                daemon=True,
            )
            self._orchestrator_thread.start()
            return True

    def cancel_conversion(self) -> None:
        """Signal all worker threads to cancel and abort processing."""
        with self._lock:
            if self._is_running:
                self._cancel_event.set()

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown worker pool and cancel any in-flight conversions."""
        self.cancel_conversion()
        with self._lock:
            if self._executor:
                self._executor.shutdown(wait=wait, cancel_futures=True)
                self._executor = None

    def _convert_single_item(
        self,
        item: QueueItem,
        config: AppConfig,
        event_queue: queue.Queue[WorkerEvent],
    ) -> None:
        """Worker task converting a single song file."""
        if self._cancel_event.is_set():
            return

        # 1. Notify ITEM_STARTED
        event_queue.put(
            WorkerEvent(
                event_type=WorkerEventType.ITEM_STARTED,
                item_id=item.id,
                data={"title": item.title, "file_path": str(item.file_path)},
            )
        )

        event_queue.put(
            WorkerEvent(
                event_type=WorkerEventType.LOG_MESSAGE,
                item_id=item.id,
                data={
                    "message": f"Starting conversion: {item.display_title} ({item.file_path.name})",
                    "level": "INFO",
                },
            )
        )

        t0 = time.perf_counter()
        try:
            success, msg = convert_con_to_song_folder(
                con_data_or_path=item.file_path,
                output_dir=config.output_dir or "./output",
                charter=config.charter or "Dansla116",
                overwrite=config.overwrite,
                audio_threads=config.stem_threads,
            )
            elapsed = max(0.001, time.perf_counter() - t0)

            if success:
                if "[SKIPPED]" in msg:
                    event_queue.put(
                        WorkerEvent(
                            event_type=WorkerEventType.ITEM_SKIPPED,
                            item_id=item.id,
                            data={"elapsed_time": elapsed, "message": msg},
                        )
                    )
                    event_queue.put(
                        WorkerEvent(
                            event_type=WorkerEventType.LOG_MESSAGE,
                            item_id=item.id,
                            data={"message": msg, "level": "WARNING"},
                        )
                    )
                else:
                    event_queue.put(
                        WorkerEvent(
                            event_type=WorkerEventType.ITEM_COMPLETED,
                            item_id=item.id,
                            data={"elapsed_time": elapsed, "message": msg},
                        )
                    )
                    event_queue.put(
                        WorkerEvent(
                            event_type=WorkerEventType.LOG_MESSAGE,
                            item_id=item.id,
                            data={"message": f"{msg} ({elapsed:.2f}s)", "level": "SUCCESS"},
                        )
                    )
            else:
                event_queue.put(
                    WorkerEvent(
                        event_type=WorkerEventType.ITEM_ERROR,
                        item_id=item.id,
                        data={"elapsed_time": elapsed, "error": msg},
                    )
                )
                event_queue.put(
                    WorkerEvent(
                        event_type=WorkerEventType.LOG_MESSAGE,
                        item_id=item.id,
                        data={"message": f"FAILED {item.display_title}: {msg}", "level": "ERROR"},
                    )
                )
        except Exception as exc:
            elapsed = max(0.001, time.perf_counter() - t0)
            err_msg = f"[ERROR] Unexpected exception: {exc}"
            event_queue.put(
                WorkerEvent(
                    event_type=WorkerEventType.ITEM_ERROR,
                    item_id=item.id,
                    data={"elapsed_time": elapsed, "error": err_msg},
                )
            )
            event_queue.put(
                WorkerEvent(
                    event_type=WorkerEventType.LOG_MESSAGE,
                    item_id=item.id,
                    data={"message": f"CRITICAL {item.display_title}: {err_msg}", "level": "ERROR"},
                )
            )

    def _run_batch(
        self,
        items: List[QueueItem],
        config: AppConfig,
        event_queue: queue.Queue[WorkerEvent],
    ) -> None:
        """Batch orchestrator thread body."""
        num_workers = max(1, config.worker_threads)
        futures: List[Future[None]] = []

        try:
            with ThreadPoolExecutor(
                max_workers=num_workers, thread_name_prefix="CONWorker"
            ) as executor:
                with self._lock:
                    self._executor = executor

                for item in items:
                    if self._cancel_event.is_set():
                        break
                    fut = executor.submit(
                        self._convert_single_item, item, config, event_queue
                    )
                    futures.append(fut)

                # Wait for all submitted futures
                for fut in as_completed(futures):
                    if self._cancel_event.is_set():
                        # Cancel remaining pending futures
                        for f in futures:
                            f.cancel()
                        break
        except Exception as exc:
            logger.error(f"Worker pool batch execution error: {exc}", exc_info=True)
        finally:
            with self._lock:
                self._is_running = False
                self._executor = None

            event_queue.put(
                WorkerEvent(
                    event_type=WorkerEventType.BATCH_FINISHED,
                    data={"cancelled": self._cancel_event.is_set()},
                )
            )
