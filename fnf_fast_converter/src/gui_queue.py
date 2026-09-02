"""
gui_queue.py - Thread-Safe Queue Item Data Models & State Machine.

Provides QueueItem data structure, QueueItemState enumeration, formatting helpers,
and thread-safe QueueModel for desktop queue operations.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


class QueueItemState(str, Enum):
    """Lifecycle states of a queue item."""

    PENDING = "pending"
    CONVERTING = "converting"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"
    REMOVED = "removed"


def format_byte_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (e.g. 14.2 MB, 850 KB)."""
    if size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class QueueItem:
    """Represents a single song package in the conversion queue."""

    file_path: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    artist: str = ""
    album: str = ""
    size_bytes: int = 0
    state: QueueItemState = QueueItemState.PENDING
    error_message: str = ""
    elapsed_time_sec: float = 0.0
    output_folder: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path)

        if not self.title:
            self.title = self.file_path.stem or self.file_path.name

        if not self.artist:
            self.artist = "Unknown Artist"

        if self.size_bytes <= 0:
            try:
                if self.file_path.is_file():
                    self.size_bytes = self.file_path.stat().st_size
            except Exception:
                self.size_bytes = 0

    @property
    def formatted_size(self) -> str:
        """Formatted human readable file size."""
        return format_byte_size(self.size_bytes)

    @property
    def display_title(self) -> str:
        """Display title for row cards."""
        return self.title if self.title else self.file_path.name

    @property
    def display_subtitle(self) -> str:
        """Display artist - album subtitle for row cards."""
        parts = []
        if self.artist and self.artist != "Unknown Artist":
            parts.append(self.artist)
        if self.album:
            parts.append(self.album)
        if not parts:
            return self.file_path.name
        return " — ".join(parts)


class QueueModel:
    """
    Thread-safe collection managing queue items.
    Supports deduplication, state transitions, filtered queries, and batch retries.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: List[QueueItem] = []
        self._items_by_id: Dict[str, QueueItem] = {}
        self._paths_seen: Dict[str, str] = {}  # resolved path string -> item_id

    def add_item(self, item: QueueItem) -> bool:
        """
        Add a single QueueItem to the queue.
        Returns True if added, False if duplicate file_path already exists.
        """
        with self._lock:
            resolved = str(item.file_path.resolve())
            if resolved in self._paths_seen:
                existing_id = self._paths_seen[resolved]
                existing = self._items_by_id.get(existing_id)
                if existing and existing.state != QueueItemState.REMOVED:
                    return False

            self._items.append(item)
            self._items_by_id[item.id] = item
            self._paths_seen[resolved] = item.id
            return True

    def add_files(
        self,
        file_paths: Sequence[Union[str, Path]],
        metadata_peek_fn: Optional[Callable[[Path], Dict[str, Any]]] = None,
    ) -> List[QueueItem]:
        """
        Batch add files to the queue, invoking optional fast metadata peek.
        Returns list of newly added items (ignoring duplicates).
        """
        added: List[QueueItem] = []
        with self._lock:
            for p in file_paths:
                path_obj = Path(p)
                resolved = str(path_obj.resolve())
                if resolved in self._paths_seen:
                    existing_id = self._paths_seen[resolved]
                    existing = self._items_by_id.get(existing_id)
                    if existing and existing.state != QueueItemState.REMOVED:
                        continue

                title = ""
                artist = ""
                album = ""
                size_bytes = 0

                if metadata_peek_fn:
                    try:
                        meta = metadata_peek_fn(path_obj)
                        title = meta.get("title", "")
                        artist = meta.get("artist", "")
                        album = meta.get("album", "")
                        size_bytes = meta.get("size_bytes", 0)
                    except Exception:
                        pass

                item = QueueItem(
                    file_path=path_obj,
                    title=title,
                    artist=artist,
                    album=album,
                    size_bytes=size_bytes,
                )
                if self.add_item(item):
                    added.append(item)

        return added

    def remove_item(self, item_id: str) -> Optional[QueueItem]:
        """Remove an item from the queue by ID."""
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item:
                item.state = QueueItemState.REMOVED
                self._items = [x for x in self._items if x.id != item_id]
                self._items_by_id.pop(item_id, None)
                resolved = str(item.file_path.resolve())
                self._paths_seen.pop(resolved, None)
                return item
            return None

    def remove_selected(self, item_ids: Sequence[str]) -> List[QueueItem]:
        """Remove multiple items by their IDs."""
        removed: List[QueueItem] = []
        with self._lock:
            for iid in item_ids:
                res = self.remove_item(iid)
                if res is not None:
                    removed.append(res)
        return removed

    def clear(self) -> None:
        """Clear all items from the queue."""
        with self._lock:
            self._items.clear()
            self._items_by_id.clear()
            self._paths_seen.clear()

    def retry_failed(self) -> List[QueueItem]:
        """
        Reset all items in ERROR or SKIPPED state back to PENDING.
        Returns list of modified items.
        """
        retried: List[QueueItem] = []
        with self._lock:
            for item in self._items:
                if item.state in (QueueItemState.ERROR, QueueItemState.SKIPPED):
                    item.state = QueueItemState.PENDING
                    item.error_message = ""
                    item.elapsed_time_sec = 0.0
                    retried.append(item)
        return retried

    def get_item(self, item_id: str) -> Optional[QueueItem]:
        """Get an item by its ID."""
        with self._lock:
            return self._items_by_id.get(item_id)

    def get_items(self) -> List[QueueItem]:
        """Return a snapshot copy of all active queue items."""
        with self._lock:
            return list(self._items)

    def get_pending_items(self) -> List[QueueItem]:
        """Return a list of items currently in PENDING state."""
        with self._lock:
            return [x for x in self._items if x.state == QueueItemState.PENDING]

    def set_item_state(
        self,
        item_id: str,
        state: QueueItemState,
        error_message: str = "",
        elapsed_time_sec: float = 0.0,
        output_folder: str = "",
    ) -> None:
        """Update an item's state, error message, elapsed time, and output folder."""
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item:
                item.state = state
                if error_message:
                    item.error_message = error_message
                if elapsed_time_sec > 0:
                    item.elapsed_time_sec = elapsed_time_sec
                if output_folder:
                    item.output_folder = output_folder

    def counts(self) -> Dict[str, int]:
        """Return dictionary of item counts by state."""
        with self._lock:
            counts = {
                "total": len(self._items),
                "pending": 0,
                "converting": 0,
                "done": 0,
                "skipped": 0,
                "error": 0,
            }
            for item in self._items:
                if item.state == QueueItemState.PENDING:
                    counts["pending"] += 1
                elif item.state == QueueItemState.CONVERTING:
                    counts["converting"] += 1
                elif item.state == QueueItemState.DONE:
                    counts["done"] += 1
                elif item.state == QueueItemState.SKIPPED:
                    counts["skipped"] += 1
                elif item.state == QueueItemState.ERROR:
                    counts["error"] += 1
            return counts

    def is_empty(self) -> bool:
        """Return True if queue contains no active items."""
        with self._lock:
            return len(self._items) == 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
