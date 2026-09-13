"""
test_web_gui.py - Comprehensive Unit & Integration Tests for Web UI and HTTP Server.
Supports both unittest and pytest without external test dependencies.
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from fnf_fast_converter.src.gui_config import AppConfig, ConfigManager
from fnf_fast_converter.src.gui_queue import QueueItem, QueueItemState, QueueModel
from fnf_fast_converter.src.gui_web import (
    WebAppController,
    create_web_handler,
    _find_free_port,
    WEB_DIR,
)


class TestWebAppController(unittest.TestCase):
    """Tests for WebAppController state management."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_controller_initialization(self):
        cfg_path = self.temp_dir / "config.json"
        ctrl = WebAppController(custom_config_path=cfg_path)
        try:
            self.assertIsNotNone(ctrl.config)
            self.assertEqual(len(ctrl.queue_model), 0)
            self.assertIsNotNone(ctrl.stats_tracker)
            self.assertFalse(ctrl.worker_pool.is_running())
        finally:
            ctrl.stop()

    def test_controller_add_files_and_remove(self):
        f1 = self.temp_dir / "song1.con"
        f2 = self.temp_dir / "song2.con"
        f1.touch()
        f2.touch()

        ctrl = WebAppController(custom_config_path=self.temp_dir / "config.json")
        try:
            added = ctrl.add_files([f1, f2])
            self.assertEqual(added, 2)
            self.assertEqual(len(ctrl.queue_model), 2)
            items_dict = ctrl.get_queue_items_dict()
            self.assertEqual(len(items_dict), 2)
            self.assertTrue(all(it["selected"] for it in items_dict))

            # Remove first item
            first_id = items_dict[0]["id"]
            ctrl.remove_items([first_id])
            self.assertEqual(len(ctrl.queue_model), 1)
            self.assertNotEqual(ctrl.queue_model.get_items()[0].id, first_id)

            # Clear queue
            ctrl.clear_queue()
            self.assertEqual(len(ctrl.queue_model), 0)
        finally:
            ctrl.stop()

    def test_controller_logging_buffer(self):
        ctrl = WebAppController(custom_config_path=self.temp_dir / "config.json")
        try:
            ctrl.add_log("Test message 1", "info")
            ctrl.add_log("Test warning", "warning")
            logs = ctrl.pop_logs()
            self.assertEqual(len(logs), 2)
            self.assertEqual(logs[0]["message"], "Test message 1")
            self.assertEqual(logs[1]["level"], "warning")
            # Second pop should be empty
            self.assertEqual(len(ctrl.pop_logs()), 0)
        finally:
            ctrl.stop()


class TestWebServerEndpoints(unittest.TestCase):
    """Integration tests for HTTP server JSON API endpoints."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        cfg_path = self.temp_dir / "config.json"
        self.ctrl = WebAppController(custom_config_path=cfg_path)
        self.port = _find_free_port(9876)
        handler_cls = create_web_handler(self.ctrl)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)

        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.ctrl.stop()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_index_html(self):
        req = urllib.request.Request(f"{self.base_url}/")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            content = resp.read().decode("utf-8")
            self.assertIn("FNF Fast Converter", content)
            self.assertIn('table id="queueTable"', content)

    def test_api_init(self):
        req = urllib.request.Request(f"{self.base_url}/api/init")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("config", data)
            self.assertIn("queue", data)
            self.assertIsInstance(data["queue"], list)

    def test_api_config_update(self):
        payload = json.dumps({
            "output_dir": "C:/TestOutput",
            "overwrite": True,
            "worker_threads": 8,
            "charter": "CustomCharter",
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertEqual(self.ctrl.config.output_dir, "C:/TestOutput")
            self.assertTrue(self.ctrl.config.overwrite)
            self.assertEqual(self.ctrl.config.worker_threads, 8)
            self.assertEqual(self.ctrl.config.charter, "CustomCharter")

    def test_api_queue_add_remove_clear(self):
        f1 = self.temp_dir / "song1.con"
        f2 = self.temp_dir / "song2.con"
        f1.touch()
        f2.touch()

        # Add files
        payload = json.dumps({"files": [str(f1), str(f2)]}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/queue/add_files",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertEqual(data["added_count"], 2)
            self.assertEqual(len(data["queue"]), 2)

        # Check status
        req = urllib.request.Request(f"{self.base_url}/api/status")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertFalse(data["is_converting"])
            self.assertEqual(len(data["queue"]), 2)

        # Remove 1 file
        item_id = data["queue"][0]["id"]
        payload = json.dumps({"ids": [item_id]}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/queue/remove",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(len(data["queue"]), 1)

        # Clear queue
        req = urllib.request.Request(f"{self.base_url}/api/queue/clear", data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(len(data["queue"]), 0)

    def test_api_convert_start_and_cancel(self):
        f1 = self.temp_dir / "song1.con"
        f1.touch()

        payload = json.dumps({"files": [str(f1)]}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/queue/add_files",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            item_id = data["queue"][0]["id"]

        # Configure output directory
        self.ctrl.config.output_dir = str(self.temp_dir / "output")

        # Start conversion
        payload = json.dumps({"item_ids": [item_id]}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/convert/start",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])

        # Cancel conversion
        req = urllib.request.Request(
            f"{self.base_url}/api/convert/cancel",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])

    def test_api_library_repair(self):
        # Create a mock song folder with missing icon in song.ini
        mock_chart_dir = self.temp_dir / "charts"
        mock_song = mock_chart_dir / "Artist - Song"
        mock_song.mkdir(parents=True, exist_ok=True)

        ini_path = mock_song / "song.ini"
        ini_path.write_text("[song]\nname = Song\nartist = Artist\n", encoding="utf-8")

        # Create an album image with white dot noise
        from PIL import Image
        import numpy as np
        img_arr = np.full((32, 32, 3), 80, dtype=np.uint8)
        img_arr[10, 10] = [255, 255, 255] # isolated dot
        Image.fromarray(img_arr).save(mock_song / "album.png")

        payload = json.dumps({
            "target_dir": str(mock_chart_dir),
            "fix_art": True,
            "fix_icons": True,
            "icon": "fnf",
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/library/repair",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertEqual(data["stats"]["total_songs"], 1)
            self.assertEqual(data["stats"]["inis_updated"], 1)
            self.assertEqual(data["stats"]["images_repaired"], 1)
            self.assertEqual(data["stats"]["pixels_fixed"], 1)

        # Verify on-disk changes
        ini_text = ini_path.read_text(encoding="utf-8")
        self.assertIn("icon = fnf", ini_text)
        with Image.open(mock_song / "album.png") as reloaded:
            arr_after = np.array(reloaded)
            self.assertTrue(np.all(arr_after[10, 10] == [80, 80, 80]))


if __name__ == "__main__":
    unittest.main()
