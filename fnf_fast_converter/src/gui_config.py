"""
gui_config.py - Persistent Application Configuration Manager for FNF Fast Converter GUI.

Handles loading, saving, and migrating user preferences to/from JSON in APPDATA
with safe local directory fallbacks.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class AppConfig:
    """Dataclass holding all GUI and conversion preferences."""

    output_dir: str = ""
    overwrite: bool = False
    worker_threads: int = 4
    stem_threads: int = 6
    charter: str = "Dansla116"
    dark_theme: str = "Dark"
    auto_scroll_logs: bool = True
    window_width: int = 1100
    window_height: int = 750

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppConfig:
        """Construct AppConfig from dictionary with safe defaults for missing keys."""
        if not isinstance(data, dict):
            return cls()
        
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        
        # Type coercions
        if "worker_threads" in filtered:
            try:
                filtered["worker_threads"] = max(1, min(64, int(filtered["worker_threads"])))
            except (ValueError, TypeError):
                filtered["worker_threads"] = 4

        if "stem_threads" in filtered:
            try:
                filtered["stem_threads"] = max(1, min(32, int(filtered["stem_threads"])))
            except (ValueError, TypeError):
                filtered["stem_threads"] = 6

        if "overwrite" in filtered:
            filtered["overwrite"] = bool(filtered["overwrite"])

        if "output_dir" in filtered:
            filtered["output_dir"] = str(filtered["output_dir"] or "")

        if "charter" in filtered:
            filtered["charter"] = str(filtered["charter"] or "Dansla116")

        if "dark_theme" in filtered:
            theme_val = str(filtered["dark_theme"]).strip().capitalize()
            if theme_val not in ("Dark", "Light", "System"):
                theme_val = "Dark"
            filtered["dark_theme"] = theme_val

        return cls(**filtered)


class ConfigManager:
    """Manages reading and writing application configuration to disk."""

    APP_NAME = "FNF_Fast_Converter"
    CONFIG_FILENAME = "config.json"

    def __init__(self, custom_path: Optional[Path | str] = None):
        self.custom_path = Path(custom_path) if custom_path else None

    def get_config_path(self) -> Path:
        """
        Determine the configuration JSON file path.
        Priority:
        1. Custom path (if explicitly provided)
        2. %APPDATA%/FNF_Fast_Converter/config.json (Windows)
        3. ~/.config/FNF_Fast_Converter/config.json (POSIX/Fallback)
        4. Local ./config.json (Fallback)
        """
        if self.custom_path:
            return self.custom_path

        # 1. Check APPDATA on Windows
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / self.APP_NAME / self.CONFIG_FILENAME

        # 2. Check user home config directory
        try:
            home = Path.home()
            return home / ".config" / self.APP_NAME / self.CONFIG_FILENAME
        except Exception:
            pass

        # 3. Fallback to local directory
        return Path(__file__).resolve().parent.parent / self.CONFIG_FILENAME

    def load(self) -> AppConfig:
        """
        Load configuration from disk. If the file does not exist or is corrupt,
        returns default AppConfig.
        """
        config_path = self.get_config_path()
        if not config_path.is_file():
            return AppConfig()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppConfig.from_dict(data)
        except Exception:
            # Corrupted JSON or IO error -> Return default config
            return AppConfig()

    def save(self, config: AppConfig) -> bool:
        """
        Save configuration to disk. Creates parent directories if needed.
        Returns True on success, False on failure.
        """
        config_path = self.get_config_path()
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = config_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
            
            # Atomic replace
            if config_path.exists():
                config_path.unlink()
            temp_file.replace(config_path)
            return True
        except Exception:
            # Fallback to direct write if replace fails
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
                return True
            except Exception:
                return False
