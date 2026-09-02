"""
gui_app.py - Main CustomTkinter Application Controller for FNF Fast Converter.

Orchestrates UI components, queue state model, background worker pool,
configuration persistence, drag-and-drop ingestion, and 60 FPS event dispatching.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional, Sequence, Union

import customtkinter as ctk

from .gui_config import AppConfig, ConfigManager
from .gui_dnd import hook_drop_target
from .gui_queue import QueueItem, QueueItemState, QueueModel
from .gui_scanner import peek_con_metadata, scan_directory_for_con_files
from .gui_stats import LiveStatsTracker
from .gui_views import (
    COLOR_BG,
    AdvancedSettingsCard,
    GlobalProgressView,
    HeaderView,
    LogConsoleView,
    QueueTableView,
    QueueToolbarView,
    SettingsToolbarView,
)
from .gui_worker import ConversionWorkerPool, WorkerEvent, WorkerEventType

logger = logging.getLogger(__name__)


def reveal_in_file_manager(path: Path) -> None:
    """Open native file manager and highlight or navigate to the target path."""
    try:
        resolved = path.resolve()
        if sys.platform == "win32":
            if resolved.is_file():
                subprocess.run(["explorer", f"/select,{resolved}"], check=False)
            elif resolved.is_dir():
                os.startfile(str(resolved))
        elif sys.platform == "darwin":
            if resolved.is_file():
                subprocess.run(["open", "-R", str(resolved)], check=False)
            else:
                subprocess.run(["open", str(resolved)], check=False)
        else:
            target_dir = resolved.parent if resolved.is_file() else resolved
            subprocess.run(["xdg-open", str(target_dir)], check=False)
    except Exception as exc:
        logger.warning(f"Failed to reveal path {path}: {exc}")


class FastConverterApp(ctk.CTk):
    """
    Main desktop application window for the Fast Rock Band CON to Clone Hero Converter.
    """

    def __init__(
        self,
        custom_config_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        # 1. State Models & Core Services
        self.config_manager = ConfigManager(custom_config_path)
        self.config = self.config_manager.load()
        self.queue_model = QueueModel()
        self.stats_tracker = LiveStatsTracker(window_size=20)
        self.worker_pool = ConversionWorkerPool()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()

        # 2. Window Setup
        self.title("FNF Fast CON Converter — Rock Band to Clone Hero")
        ctk.set_appearance_mode(self.config.dark_theme or "Dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=COLOR_BG)

        width = max(960, self.config.window_width or 1100)
        height = max(650, self.config.window_height or 750)
        self.geometry(f"{width}x{height}")
        self.minsize(960, 650)

        # 3. Layout Grid
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)  # Queue Table is expandable

        # 4. Build View Components
        self._build_views()

        # 5. Native Drag-and-Drop Hook
        self._setup_dnd()

        # 6. Window Close Interceptor
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        # 7. Global Keyboard Shortcuts
        self.bind("<Control-a>", lambda e: self._on_select_all())
        self.bind("<Control-A>", lambda e: self._on_select_all())

        # 8. Start 60 FPS Event Polling Loop
        self._anim_counter = 0
        self._poll_interval_ms = 20  # ~50-60 Hz UI update
        self.after(self._poll_interval_ms, self._poll_worker_events)

    def _build_views(self) -> None:
        """Construct and layout all UI sections."""
        # Row 0: Header Bar
        self.header_view = HeaderView(self)
        self.header_view.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))

        # Row 1: Settings Toolbar
        self.settings_toolbar = SettingsToolbarView(
            self,
            config=self.config,
            on_browse_output=self._on_browse_output,
            on_toggle_advanced=self._on_toggle_advanced,
            on_config_changed=self._on_config_changed,
        )
        self.settings_toolbar.grid(row=1, column=0, sticky="ew", padx=16, pady=4)

        # Row 2: Expandable Advanced Settings (hidden initially on its own row)
        self.advanced_card = AdvancedSettingsCard(
            self,
            config=self.config,
            on_config_changed=self._on_config_changed,
            on_collapse=self._on_toggle_advanced,
        )
        self._advanced_visible = False

        # Row 3: Queue Toolbar (Add / Select All / Remove / Clear / Start)
        self.queue_toolbar = QueueToolbarView(
            self,
            on_add_files=self._on_add_files,
            on_add_folder=self._on_add_folder,
            on_remove_selected=self._on_remove_selected,
            on_clear_all=self._on_clear_all,
            on_retry_failed=self._on_retry_failed,
            on_start_conversion=self._on_start_conversion,
            on_cancel_conversion=self._on_cancel_conversion,
            on_select_all=self._on_select_all,
        )
        self.queue_toolbar.grid(row=3, column=0, sticky="ew", padx=16, pady=6)

        # Row 4: Queue Table View
        self.queue_table = QueueTableView(
            self,
            on_delete_item=self._on_delete_queue_item,
            on_select_item=self._on_select_queue_item,
            on_context_action=self._on_context_action,
        )
        self.queue_table.grid(row=4, column=0, sticky="nsew", padx=16, pady=4)

        # Row 5: Global Progress & Live Metrics
        self.progress_view = GlobalProgressView(self)
        self.progress_view.grid(row=5, column=0, sticky="ew", padx=16, pady=6)

        # Row 6: Collapsible Log Console Drawer
        self.log_console = LogConsoleView(self)
        self.log_console.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 12))

        # Initial queue counts
        self._update_queue_counts()

    def _setup_dnd(self) -> None:
        """Initialize native Windows drag-and-drop hook with safe fallback."""
        hook_drop_target(self, callback=self.handle_dropped_paths)

    def handle_dropped_paths(self, paths: Sequence[Union[str, Path]]) -> None:
        """Process files and folders dropped directly onto the application window."""
        con_files: List[Path] = []
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                discovered = scan_directory_for_con_files(path_obj, recursive=True)
                con_files.extend(discovered)
            elif path_obj.is_file():
                con_files.append(path_obj)

        if con_files:
            new_items = self.queue_model.add_files(
                con_files, metadata_peek_fn=peek_con_metadata
            )
            if new_items:
                self.queue_table.add_items_lazy(new_items)
                self._update_queue_counts()
                self.log_console.append_log(
                    f"Added {len(new_items)} songs to conversion queue from drag-and-drop.",
                    level="INFO",
                )

    # -------------------------------------------------------------------------
    # Actions & View Callbacks
    # -------------------------------------------------------------------------

    def _on_browse_output(self) -> None:
        """Open directory chooser for destination chart folder."""
        initial = self.config.output_dir or str(Path.home())
        chosen = filedialog.askdirectory(
            title="Select Output Directory for Converted Charts",
            initialdir=initial,
        )
        if chosen:
            self.settings_toolbar.set_output_dir(chosen)

    def _on_toggle_advanced(self) -> None:
        """Show or hide advanced settings card."""
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_card.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
            self.settings_toolbar.btn_advanced.configure(text="⚙ Advanced ▴")
        else:
            self.advanced_card.grid_forget()
            self.settings_toolbar.btn_advanced.configure(text="⚙ Advanced ▾")

    def _on_config_changed(self) -> None:
        """Save updated settings to disk."""
        self.config_manager.save(self.config)

    def _on_select_all(self) -> None:
        """Toggle select all items in queue."""
        self.queue_table.toggle_select_all()
        self._update_queue_counts()

    def _on_deselect_all(self) -> None:
        """Deselect all items in queue."""
        self.queue_table.deselect_all()
        self._update_queue_counts()

    def _update_queue_counts(self) -> None:
        """Update toolbar counter badge and progress view total count."""
        total = self.queue_model.counts()["total"]
        selected = len(self.queue_table.selected_ids)
        self.queue_toolbar.update_counter(total_count=total, selected_count=selected)
        self.progress_view.set_total_items(total)

    def _on_add_files(self) -> None:
        """Open multi-file picker for CON packages."""
        chosen = filedialog.askopenfilenames(
            title="Select Rock Band CON Package Files",
            filetypes=[
                ("Rock Band CON Files / All Files", "*.*"),
            ],
        )
        if chosen:
            new_items = self.queue_model.add_files(
                chosen, metadata_peek_fn=peek_con_metadata
            )
            if new_items:
                self.queue_table.add_items_lazy(new_items)
                self._update_queue_counts()
                self.log_console.append_log(
                    f"Added {len(new_items)} file(s) to conversion queue.",
                    level="INFO",
                )

    def _on_add_folder(self) -> None:
        """Open directory picker to recursively scan for CON archives."""
        chosen = filedialog.askdirectory(
            title="Select Folder to Recursively Scan for CON Files"
        )
        if chosen:
            self.log_console.append_log(
                f"Scanning directory '{chosen}' for CON packages...",
                level="INFO",
            )
            discovered = scan_directory_for_con_files(chosen, recursive=True)
            if discovered:
                new_items = self.queue_model.add_files(
                    discovered, metadata_peek_fn=peek_con_metadata
                )
                self.queue_table.add_items_lazy(new_items)
                self._update_queue_counts()
                self.log_console.append_log(
                    f"Discovered and queued {len(new_items)} CON package(s).",
                    level="INFO",
                )
            else:
                self.log_console.append_log(
                    f"No CON files found in '{chosen}'.",
                    level="WARNING",
                )
                messagebox.showinfo(
                    "No CON Files Found",
                    f"No valid Rock Band CON files were found in:\n{chosen}",
                )

    def _on_delete_queue_item(self, item_id: str) -> None:
        """Remove item from queue model."""
        self.queue_model.remove_item(item_id)
        self._update_queue_counts()

    def _on_select_queue_item(self, item_id: str, selected: bool) -> None:
        """Track selected item in queue table."""
        self._update_queue_counts()

    def _on_remove_selected(self) -> None:
        """Remove all checked rows."""
        selected_ids = list(self.queue_table.selected_ids)
        if not selected_ids:
            return

        self.queue_model.remove_selected(selected_ids)
        self.queue_table.remove_items(selected_ids)
        self._update_queue_counts()
        self.log_console.append_log(
            f"Removed {len(selected_ids)} item(s) from queue.",
            level="INFO",
        )

    def _on_clear_all(self) -> None:
        """Clear entire queue."""
        if self.worker_pool.is_running():
            messagebox.showwarning(
                "Conversion in Progress",
                "Cannot clear queue while conversion is actively running. Cancel first.",
            )
            return

        self.queue_model.clear()
        self.queue_table.clear()
        self.stats_tracker.reset()
        self.progress_view.reset()
        self._update_queue_counts()
        self.log_console.append_log("Queue cleared.", level="INFO")

    def _on_retry_failed(self) -> None:
        """Reset all failed and skipped items back to pending."""
        retried = self.queue_model.retry_failed()
        if retried:
            for item in retried:
                self.queue_table.update_row_state(item.id, QueueItemState.PENDING)
            self._update_queue_counts()
            self.log_console.append_log(
                f"Reset {len(retried)} failed/skipped item(s) back to Pending.",
                level="INFO",
            )

    def _on_context_action(self, item_id: str, action: str) -> None:
        """Handle right-click context menu commands."""
        item = self.queue_model.get_item(item_id)
        if not item:
            return

        if action == "reveal":
            reveal_in_file_manager(item.file_path)
        elif action == "open_output":
            if item.output_folder and Path(item.output_folder).is_dir():
                reveal_in_file_manager(Path(item.output_folder))
            elif self.config.output_dir and Path(self.config.output_dir).is_dir():
                reveal_in_file_manager(Path(self.config.output_dir))
            else:
                reveal_in_file_manager(Path.cwd())
        elif action == "copy_path":
            self.clipboard_clear()
            self.clipboard_append(str(item.file_path.resolve()))
            self.log_console.append_log(
                f"Copied path to clipboard: {item.file_path}",
                level="INFO",
            )
        elif action == "select_all":
            self._on_select_all()
        elif action == "deselect_all":
            self._on_deselect_all()
        elif action == "view_error":
            if item.state == QueueItemState.ERROR:
                msg = item.error_message or "Unknown error."
                messagebox.showerror(
                    f"Error Details — {item.display_title}",
                    f"File: {item.file_path}\n\nError:\n{msg}",
                )
            else:
                messagebox.showinfo(
                    "Item Status",
                    f"Song: {item.display_title}\nStatus: {item.state.value.capitalize()}",
                )

    def _on_start_conversion(self) -> None:
        """Initiate asynchronous batch conversion run."""
        output_dir = self.settings_toolbar.get_output_dir()
        if not output_dir:
            messagebox.showwarning(
                "Missing Output Directory",
                "Please select an Output Directory for converted charts before starting.",
            )
            return

        # Ensure output directory exists or can be created
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror(
                "Invalid Output Directory",
                f"Cannot create destination directory:\n{output_dir}\n\nError: {exc}",
            )
            return

        # Get pending items
        pending_items = self.queue_model.get_pending_items()
        if not pending_items:
            # If all are done/error, prompt or suggest retry
            counts = self.queue_model.counts()
            if counts["total"] == 0:
                messagebox.showinfo(
                    "Empty Queue",
                    "Please add Rock Band CON files to the queue before converting.",
                )
            elif counts["error"] > 0 or counts["skipped"] > 0:
                messagebox.showinfo(
                    "No Pending Items",
                    "All items in queue are already processed. Click '🔄 Retry Failed' to convert again.",
                )
            else:
                messagebox.showinfo(
                    "Conversion Completed",
                    "All songs in the queue have already been converted.",
                )
            return

        # Start stats tracking and update UI
        self.stats_tracker.start(len(pending_items))
        self.queue_toolbar.set_converting_state(True)
        self.log_console.append_log(
            f"=== Starting Batch Conversion ({len(pending_items)} songs) ===",
            level="INFO",
        )

        # Launch worker pool
        self.worker_pool.start_conversion(
            items=pending_items,
            config=self.config,
            event_queue=self.event_queue,
        )

    def _on_cancel_conversion(self) -> None:
        """Signal worker pool cancellation."""
        self.log_console.append_log(
            "Cancelling active conversion run...",
            level="WARNING",
        )
        self.worker_pool.cancel_conversion()

    # -------------------------------------------------------------------------
    # 60 FPS Event Polling Loop
    # -------------------------------------------------------------------------

    def _poll_worker_events(self) -> None:
        """Drain background worker event queue and update UI elements."""
        try:
            while True:
                try:
                    event: WorkerEvent = self.event_queue.get_nowait()
                except queue.Empty:
                    break

                self._process_single_worker_event(event)
        except Exception as exc:
            logger.error(f"Error in UI event polling loop: {exc}", exc_info=True)

        # Periodic spinner animation & metrics refresh
        self._anim_counter += 1
        if self._anim_counter % 5 == 0:
            self.queue_table.animate_spinners()

        if self.worker_pool.is_running():
            metrics = self.stats_tracker.get_metrics()
            self.progress_view.update_metrics(metrics)

        # Re-arm timer
        self.after(self._poll_interval_ms, self._poll_worker_events)

    def _process_single_worker_event(self, event: WorkerEvent) -> None:
        """Dispatch a single worker event to UI state and components."""
        etype = event.event_type
        iid = event.item_id
        data = event.data or {}

        if etype == WorkerEventType.ITEM_STARTED and iid:
            self.queue_model.set_item_state(iid, QueueItemState.CONVERTING)
            self.queue_table.update_row_state(iid, QueueItemState.CONVERTING)
            self.stats_tracker.record_item_started(iid)

        elif etype == WorkerEventType.ITEM_COMPLETED and iid:
            elapsed = data.get("elapsed_time", 0.0)
            msg = data.get("message", "")
            self.queue_model.set_item_state(
                iid, QueueItemState.DONE, elapsed_time_sec=elapsed
            )
            self.queue_table.update_row_state(
                iid, QueueItemState.DONE, elapsed_sec=elapsed
            )
            self.stats_tracker.record_item_finished(
                iid, QueueItemState.DONE, elapsed_time=elapsed
            )

        elif etype == WorkerEventType.ITEM_SKIPPED and iid:
            elapsed = data.get("elapsed_time", 0.0)
            msg = data.get("message", "")
            self.queue_model.set_item_state(
                iid, QueueItemState.SKIPPED, elapsed_time_sec=elapsed
            )
            self.queue_table.update_row_state(
                iid, QueueItemState.SKIPPED, elapsed_sec=elapsed
            )
            self.stats_tracker.record_item_finished(
                iid, QueueItemState.SKIPPED, elapsed_time=elapsed
            )

        elif etype == WorkerEventType.ITEM_ERROR and iid:
            elapsed = data.get("elapsed_time", 0.0)
            err = data.get("error", "Unknown error")
            self.queue_model.set_item_state(
                iid, QueueItemState.ERROR, error_message=err, elapsed_time_sec=elapsed
            )
            self.queue_table.update_row_state(
                iid, QueueItemState.ERROR, elapsed_sec=elapsed, error_msg=err
            )
            self.stats_tracker.record_item_finished(
                iid, QueueItemState.ERROR, elapsed_time=elapsed
            )

        elif etype == WorkerEventType.BATCH_FINISHED:
            cancelled = data.get("cancelled", False)
            self.stats_tracker.finish_batch()
            self.queue_toolbar.set_converting_state(False)
            metrics = self.stats_tracker.get_metrics()
            self.progress_view.update_metrics(metrics)

            if cancelled:
                self.log_console.append_log(
                    "=== Batch Conversion Cancelled by User ===",
                    level="WARNING",
                )
            else:
                self.log_console.append_log(
                    f"=== Batch Conversion Finished: {metrics['done']} Converted, {metrics['skipped']} Skipped, {metrics['error']} Failed in {metrics['elapsed_str']} ===",
                    level="SUCCESS" if metrics["error"] == 0 else "WARNING",
                )

        elif etype == WorkerEventType.LOG_MESSAGE:
            msg = data.get("message", "")
            lvl = data.get("level", "INFO")
            self.log_console.append_log(msg, level=lvl)

    def _on_close_window(self) -> None:
        """Handle window destruction safely."""
        # Save window dimensions
        try:
            self.config.window_width = self.winfo_width()
            self.config.window_height = self.winfo_height()
            self.config_manager.save(self.config)
        except Exception:
            pass

        # Terminate worker pool cleanly
        self.worker_pool.shutdown(wait=False)
        self.destroy()
