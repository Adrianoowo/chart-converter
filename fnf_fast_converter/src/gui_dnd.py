"""
gui_dnd.py - Resilient Windows Drag-and-Drop Hook with Unicode Decoding & Safe Fallbacks.

Provides native Windows shell drag-and-drop file ingestion into CustomTkinter widgets
with graceful fallback when running in non-Windows or headless test environments.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, List, Sequence, Union

logger = logging.getLogger(__name__)


def _decode_dropped_path(raw: Union[str, bytes]) -> Path:
    """Decode raw dropped item from windnd into a valid Path."""
    if isinstance(raw, Path):
        return raw

    if isinstance(raw, bytes):
        for enc in ("utf-8", "mbcs", sys.getfilesystemencoding(), "latin-1"):
            try:
                decoded = raw.decode(enc)
                if decoded:
                    return Path(decoded.strip("\x00 \t\r\n"))
            except (UnicodeDecodeError, Exception):
                continue
        # Fallback with replace
        return Path(raw.decode("utf-8", errors="replace").strip("\x00 \t\r\n"))

    return Path(str(raw).strip("\x00 \t\r\n"))


def hook_drop_target(
    widget: Any,
    callback: Callable[[List[Path]], None],
    expand_directories: bool = False,
) -> bool:
    """
    Attach native Windows shell drag-and-drop handler to a Tkinter / CustomTkinter widget.

    Args:
        widget: Tk or CustomTkinter widget (e.g. root window or CTkFrame).
        callback: Function taking a list of resolved `Path` objects.
        expand_directories: If True, recursively scans dropped folders for CON files.

    Returns:
        bool: True if drag-and-drop was successfully hooked, False if unavailable.
    """
    if sys.platform != "win32":
        logger.info("Native windnd is only available on Windows platform.")
        return False

    try:
        import windnd
    except ImportError:
        logger.warning("windnd package not installed. Drag-and-drop disabled.")
        return False
    except Exception as exc:
        logger.warning(f"Failed to import windnd: {exc}")
        return False

    def _on_drop_files(raw_files: Sequence[Union[str, bytes]]) -> None:
        try:
            paths: List[Path] = []
            for raw in raw_files:
                p = _decode_dropped_path(raw)
                if p.exists():
                    paths.append(p)

            if paths:
                callback(paths)
        except Exception as exc:
            logger.error(f"Error handling dropped files: {exc}", exc_info=True)

    try:
        # If widget is a CustomTkinter widget, it might have an underlying Tk widget or canvas
        target = widget
        if hasattr(widget, "winfo_id"):
            target = widget
        elif hasattr(widget, "_canvas"):
            target = widget._canvas

        windnd.hook_dropfiles(target, func=_on_drop_files)
        return True
    except Exception as exc:
        logger.warning(f"Failed to hook drag-and-drop on widget: {exc}")
        return False
