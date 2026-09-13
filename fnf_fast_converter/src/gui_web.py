"""
gui_web.py - Ultra-Fast, Lightweight Web-Based UI for FNF Fast Converter.

Zero-dependency local HTTP server using Python's built-in standard library.
Serves a simple, clean, fast HTML/CSS/JS page that eliminates desktop GUI lag,
avoids canvas rendering overhead, and operates seamlessly across all Python versions.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .gui_config import AppConfig, ConfigManager
from .gui_queue import QueueItem, QueueItemState, QueueModel
from .gui_scanner import peek_con_metadata, scan_directory_for_con_files
from .gui_stats import LiveStatsTracker
from .gui_worker import ConversionWorkerPool, WorkerEvent, WorkerEventType
from .repair import repair_chart_library

logger = logging.getLogger("fnf_fast_converter.web")

WEB_DIR = Path(__file__).resolve().parent / "web"


def _find_free_port(start_port: int = 8765) -> int:
    """Find an available localhost TCP port starting from start_port."""
    port = start_port
    while port < start_port + 100:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            port += 1
    return start_port


def _native_pick_directory(title: str = "Select Folder", initial_dir: Optional[str] = None) -> str:
    """Open native Windows directory chooser dialog without displaying Tk root."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title=title, initialdir=initial_dir or None)
        root.destroy()
        return folder or ""
    except Exception as exc:
        logger.warning(f"Native folder picker failed: {exc}")
        return ""


def _native_pick_files(title: str = "Select Rock Band CON Files", initial_dir: Optional[str] = None) -> List[str]:
    """Open native Windows multi-file chooser dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        files = filedialog.askopenfilenames(
            title=title,
            initialdir=initial_dir or None,
            filetypes=[("Rock Band CON Files / All Files", "*.*")],
        )
        root.destroy()
        return list(files) if files else []
    except Exception as exc:
        logger.warning(f"Native file picker failed: {exc}")
        return []


class WebAppController:
    """Controller managing converter models, worker threads, and live stats for Web UI."""

    def __init__(self, custom_config_path: Optional[Union[str, Path]] = None) -> None:
        self.config_manager = ConfigManager(custom_config_path)
        self.config = self.config_manager.load()
        self.queue_model = QueueModel()
        self.stats_tracker = LiveStatsTracker(window_size=20)
        self.worker_pool = ConversionWorkerPool()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()
        self.logs_buffer: collections.deque[Dict[str, str]] = collections.deque(maxlen=300)
        self._lock = threading.RLock()

        # Selection state tracked for web UI
        self.selected_ids: set[str] = set()

        # Start worker event listener thread
        self._running = True
        self._event_thread = threading.Thread(
            target=self._drain_events, name="WebEventDrainThread", daemon=True
        )
        self._event_thread.start()

    def stop(self) -> None:
        self._running = False

    def add_log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs_buffer.append({"message": message, "level": level})

    def pop_logs(self) -> List[Dict[str, str]]:
        with self._lock:
            logs = list(self.logs_buffer)
            self.logs_buffer.clear()
            return logs

    def get_queue_items_dict(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for it in self.queue_model.get_items():
                result.append(
                    {
                        "id": it.id,
                        "file_path": str(it.file_path),
                        "filename": it.file_path.name,
                        "title": it.title,
                        "artist": it.artist,
                        "album": it.album,
                        "size_bytes": it.size_bytes,
                        "formatted_size": it.formatted_size,
                        "state": it.state.value if hasattr(it.state, "value") else str(it.state),
                        "elapsed_sec": it.elapsed_time_sec,
                        "error_message": it.error_message,
                        "selected": it.id in self.selected_ids,
                    }
                )
            return result

    def add_files(self, paths: Sequence[Union[str, Path]]) -> int:
        """Add files to queue with in-memory metadata peek."""
        new_items = self.queue_model.add_files(paths, metadata_peek_fn=peek_con_metadata)
        with self._lock:
            for it in new_items:
                self.selected_ids.add(it.id)
        return len(new_items)

    def add_folder(self, folder: Union[str, Path]) -> int:
        """Scan folder for CON packages and queue them."""
        discovered = scan_directory_for_con_files(folder, recursive=True)
        if not discovered:
            return 0
        return self.add_files(discovered)

    def remove_items(self, item_ids: Sequence[str]) -> None:
        with self._lock:
            self.queue_model.remove_selected(item_ids)
            for i in item_ids:
                self.selected_ids.discard(i)

    def clear_queue(self) -> None:
        with self._lock:
            self.queue_model.clear()
            self.selected_ids.clear()
        self.stats_tracker.reset()

    def start_conversion(self, item_ids: Optional[Sequence[str]] = None) -> bool:
        with self._lock:
            if self.worker_pool.is_running():
                return False

            all_items = self.queue_model.get_items()
            if item_ids:
                target_set = set(item_ids)
                items_to_run = [it for it in all_items if it.id in target_set]
            else:
                items_to_run = all_items

            if not items_to_run:
                return False

            self.stats_tracker.reset()
            self.stats_tracker.start(len(items_to_run))

            started = self.worker_pool.start_conversion(
                items=items_to_run,
                config=self.config,
                event_queue=self.event_queue,
            )
            return started

    def cancel_conversion(self) -> None:
        self.worker_pool.cancel_conversion()

    def _drain_events(self) -> None:
        """Consume worker events and dispatch to state models."""
        while self._running:
            try:
                event = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                if event.event_type == WorkerEventType.ITEM_STARTED:
                    if event.item_id:
                        self.queue_model.set_item_state(event.item_id, QueueItemState.CONVERTING)
                        self.stats_tracker.record_item_started(event.item_id)

                elif event.event_type == WorkerEventType.ITEM_COMPLETED:
                    if event.item_id:
                        elapsed = float(event.data.get("elapsed_time") or event.data.get("elapsed") or 0.0)
                        out_dir = str(event.data.get("output_dir", ""))
                        self.queue_model.set_item_state(
                            event.item_id,
                            QueueItemState.DONE,
                            elapsed_time_sec=elapsed,
                            output_folder=out_dir,
                        )
                        self.stats_tracker.record_item_finished(event.item_id, QueueItemState.DONE, elapsed)
                        item = self.queue_model.get_item(event.item_id)
                        song_title = event.data.get("title") or (item.display_title if item else event.item_id)
                        self.add_log(f"✔ Converted: {song_title} ({elapsed:.1f}s)", "info")

                elif event.event_type == WorkerEventType.ITEM_SKIPPED:
                    if event.item_id:
                        elapsed = float(event.data.get("elapsed_time") or event.data.get("elapsed") or 0.0)
                        self.queue_model.set_item_state(
                            event.item_id,
                            QueueItemState.SKIPPED,
                            elapsed_time_sec=elapsed,
                        )
                        self.stats_tracker.record_item_finished(event.item_id, QueueItemState.SKIPPED, elapsed)
                        item = self.queue_model.get_item(event.item_id)
                        song_title = event.data.get("title") or (item.display_title if item else event.item_id)
                        self.add_log(f"⚡ Skipped (already exists): {song_title}", "warning")

                elif event.event_type == WorkerEventType.ITEM_ERROR:
                    if event.item_id:
                        elapsed = float(event.data.get("elapsed_time") or event.data.get("elapsed") or 0.0)
                        err = str(event.data.get("error", "Unknown error"))
                        self.queue_model.set_item_state(
                            event.item_id, QueueItemState.ERROR, error_message=err, elapsed_time_sec=elapsed
                        )
                        self.stats_tracker.record_item_finished(event.item_id, QueueItemState.ERROR, elapsed)
                        item = self.queue_model.get_item(event.item_id)
                        song_title = event.data.get("title") or (item.display_title if item else event.item_id)
                        self.add_log(f"✖ Error converting {song_title}: {err}", "error")

                elif event.event_type == WorkerEventType.BATCH_FINISHED:
                    self.stats_tracker.finish_batch()
                    self.add_log("Batch conversion finished.", "info")

                elif event.event_type == WorkerEventType.LOG_MESSAGE:
                    msg = str(event.data.get("message", ""))
                    lvl = str(event.data.get("level", "info")).lower()
                    if msg:
                        self.add_log(msg, lvl)

            except Exception as exc:
                logger.error(f"Error handling worker event: {exc}")


def create_web_handler(controller: WebAppController):
    """Factory creating HTTP request handler closing over controller instance."""

    class FastConverterHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Suppress normal access log spam to keep console clean
            pass

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw)
            except Exception:
                return {}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path in ("/", "/index.html"):
                html_path = WEB_DIR / "index.html"
                if not html_path.is_file():
                    self.send_error(404, "index.html not found")
                    return
                html_bytes = html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
                return

            if path == "/api/init":
                metrics = controller.stats_tracker.get_metrics()
                metrics["elapsed"] = metrics["elapsed_str"]
                metrics["eta"] = metrics["eta_str"]
                metrics["speed"] = metrics["throughput_str"]
                self._send_json(
                    {
                        "config": controller.config.to_dict(),
                        "queue": controller.get_queue_items_dict(),
                        "is_converting": controller.worker_pool.is_running(),
                        "progress": metrics,
                    }
                )
                return

            if path == "/api/status":
                metrics = controller.stats_tracker.get_metrics()
                metrics["elapsed"] = metrics["elapsed_str"]
                metrics["eta"] = metrics["eta_str"]
                metrics["speed"] = metrics["throughput_str"]
                self._send_json(
                    {
                        "is_converting": controller.worker_pool.is_running(),
                        "progress": metrics,
                        "queue": controller.get_queue_items_dict(),
                        "logs": controller.pop_logs(),
                    }
                )
                return

            self.send_error(404, "Not Found")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            payload = self._read_json_body()

            if path == "/api/config":
                if payload:
                    controller.config = AppConfig.from_dict(payload)
                    controller.config_manager.save(controller.config)
                self._send_json({"success": True, "config": controller.config.to_dict()})
                return

            if path == "/api/browse/output":
                curr = payload.get("current", controller.config.output_dir)
                chosen = _native_pick_directory("Select Output Directory", initial_dir=curr)
                if chosen:
                    controller.config.output_dir = chosen
                    controller.config_manager.save(controller.config)
                self._send_json({"path": chosen or controller.config.output_dir})
                return

            if path == "/api/browse/files":
                chosen_files = _native_pick_files("Select Rock Band CON Files")
                self._send_json({"files": chosen_files})
                return

            if path == "/api/browse/folder":
                chosen_folder = _native_pick_directory("Select Folder to Scan for CON Files")
                self._send_json({"folder": chosen_folder})
                return

            if path == "/api/queue/add_files":
                files = payload.get("files", [])
                added = controller.add_files(files)
                self._send_json(
                    {
                        "success": True,
                        "added_count": added,
                        "queue": controller.get_queue_items_dict(),
                    }
                )
                return

            if path == "/api/queue/add_folder":
                folder = payload.get("folder", "")
                added = controller.add_folder(folder) if folder else 0
                self._send_json(
                    {
                        "success": True,
                        "added_count": added,
                        "queue": controller.get_queue_items_dict(),
                    }
                )
                return

            if path == "/api/queue/remove":
                ids = payload.get("ids", [])
                controller.remove_items(ids)
                self._send_json(
                    {
                        "success": True,
                        "queue": controller.get_queue_items_dict(),
                    }
                )
                return

            if path == "/api/queue/clear":
                controller.clear_queue()
                self._send_json(
                    {
                        "success": True,
                        "queue": [],
                    }
                )
                return

            if path == "/api/convert/start":
                item_ids = payload.get("item_ids")
                success = controller.start_conversion(item_ids)
                self._send_json({"success": success})
                return

            if path == "/api/convert/cancel":
                controller.cancel_conversion()
                self._send_json({"success": True})
                return

            if path == "/api/library/repair":
                target = payload.get("target_dir") or controller.config.output_dir or r"D:\Charts\Fortnite Festival"
                default_con = r"C:\Users\adema\Downloads\FNFestivaltoRB-main"
                source = payload.get("source_dir") or (default_con if os.path.isdir(default_con) else None)
                fix_art = payload.get("fix_art", True)
                fix_icons = payload.get("fix_icons", True)
                icon_tag = payload.get("icon", "fnf")

                controller.add_log(f"Starting library repair on '{target}' (source_dir='{source}', fix_art={fix_art}, fix_icons={fix_icons})...")
                try:
                    stats = repair_chart_library(
                        target,
                        source_dir=source,
                        fix_art=fix_art,
                        fix_icons=fix_icons,
                        icon_tag=icon_tag,
                    )
                    msg = (
                        f"Library repair complete: {stats['total_songs']} songs scanned, "
                        f"{stats['images_restored_from_con']} covers restored from CONs, "
                        f"{stats['images_repaired']} images repaired, "
                        f"{stats['inis_updated']} song.ini updated with icon={icon_tag}."
                    )
                    controller.add_log(msg)
                    self._send_json({"success": True, "stats": stats})
                except Exception as exc:
                    err_msg = f"Library repair error: {exc}"
                    controller.add_log(err_msg, "error")
                    self._send_json({"success": False, "error": str(exc)}, status=400)
                return

            self.send_error(404, "Not Found")

    return FastConverterHandler


def run_web_server(
    port: Optional[int] = None,
    open_browser: bool = True,
    custom_config_path: Optional[Union[str, Path]] = None,
) -> None:
    """Launch the Web UI HTTP server and open browser."""
    controller = WebAppController(custom_config_path=custom_config_path)
    chosen_port = port if port is not None else _find_free_port(8765)
    handler_cls = create_web_handler(controller)

    server = ThreadingHTTPServer(("127.0.0.1", chosen_port), handler_cls)
    url = f"http://127.0.0.1:{chosen_port}/"

    print("====================================================================")
    print("      FNF Fast Converter — Simple Web Interface")
    print("====================================================================")
    print(f"Running locally at: {url}")
    print("Press Ctrl+C to stop the server.")
    print("====================================================================")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        controller.stop()
        server.server_close()
        print("\nWeb server stopped.")


if __name__ == "__main__":
    run_web_server()
