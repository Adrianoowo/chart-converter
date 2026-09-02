"""
gui.py - Main Desktop GUI Launch Entry Point & SafeStreamWriter Stream Wrapper.

Provides the CLI/GUI launcher, handles --noconsole windowed execution without crashing,
and redirects standard output to the embedded real-time log drawer.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from .gui_app import FastConverterApp
from .gui_config import AppConfig, ConfigManager

logger = logging.getLogger("fnf_fast_converter.gui")


class SafeStreamWriter(io.TextIOBase):
    """
    Thread-safe stream wrapper that protects against None stdout/stderr in
    Windows --noconsole execution and redirects log streams to a callback.
    """

    def __init__(
        self,
        original_stream: Optional[Any],
        log_callback: Optional[Callable[[str, str], None]] = None,
        level: str = "INFO",
    ) -> None:
        self._original = original_stream
        self._callback = log_callback
        self._level = level
        self._buffer = ""

    def write(self, s: str) -> int:
        if not s:
            return 0

        # Write to original underlying console stream if it exists
        if self._original is not None:
            try:
                self._original.write(s)
                self._original.flush()
            except Exception:
                pass

        # Buffer lines for UI callback
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line_clean = line.strip("\r\n")
            if line_clean and self._callback is not None:
                try:
                    self._callback(line_clean, self._level)
                except Exception:
                    pass

        return len(s)

    def flush(self) -> None:
        if self._buffer and self._callback is not None:
            line_clean = self._buffer.strip("\r\n")
            if line_clean:
                try:
                    self._callback(line_clean, self._level)
                except Exception:
                    pass
            self._buffer = ""

        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        if self._original is not None and hasattr(self._original, "isatty"):
            try:
                return self._original.isatty()
            except Exception:
                return False
        return False

    def fileno(self) -> int:
        if self._original is not None and hasattr(self._original, "fileno"):
            try:
                return self._original.fileno()
            except Exception:
                raise io.UnsupportedOperation("fileno not supported")
        raise io.UnsupportedOperation("fileno not supported")


def setup_safe_streams(app: Optional[FastConverterApp] = None) -> None:
    """Redirect sys.stdout and sys.stderr through SafeStreamWriter."""
    cb: Optional[Callable[[str, str], None]] = None
    if app is not None and hasattr(app, "log_console"):
        cb = app.log_console.append_log

    if not isinstance(sys.stdout, SafeStreamWriter):
        sys.stdout = SafeStreamWriter(sys.stdout, log_callback=cb, level="INFO")

    if not isinstance(sys.stderr, SafeStreamWriter):
        sys.stderr = SafeStreamWriter(sys.stderr, log_callback=cb, level="ERROR")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main launch entry point for desktop GUI."""
    parser = argparse.ArgumentParser(
        description="FNF Fast CON to Clone Hero Converter — Desktop GUI"
    )
    parser.add_argument(
        "-i", "--input", help="Optional initial CON file or directory to queue on startup"
    )
    parser.add_argument(
        "-o", "--output", help="Optional default destination directory for song charts"
    )
    parser.add_argument(
        "-t", "--threads", type=int, help="Number of worker song conversion threads"
    )
    parser.add_argument(
        "-s", "--stems", type=int, help="Number of audio stem worker threads"
    )
    parser.add_argument(
        "-c", "--charter", type=str, help="Default charter name to embed in song.ini"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing chart directories"
    )
    parser.add_argument(
        "--theme", choices=["Dark", "Light", "System"], help="Appearance theme mode"
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Initialize app
    app = FastConverterApp()

    # Apply any command-line overrides
    if args.output:
        app.settings_toolbar.set_output_dir(str(Path(args.output).resolve()))
    if args.threads:
        app.config.worker_threads = max(1, args.threads)
        app.advanced_card.slider_workers.set(app.config.worker_threads)
        app.advanced_card.lbl_workers_val.configure(text=str(app.config.worker_threads))
    if args.stems:
        app.config.stem_threads = max(1, args.stems)
        app.advanced_card.slider_stems.set(app.config.stem_threads)
        app.advanced_card.lbl_stems_val.configure(text=str(app.config.stem_threads))
    if args.charter:
        app.config.charter = args.charter
        app.advanced_card.entry_charter.delete(0, "end")
        app.advanced_card.entry_charter.insert(0, args.charter)
    if args.overwrite:
        app.config.overwrite = True
        app.settings_toolbar.var_overwrite.set(True)
    if args.theme:
        app.advanced_card._on_theme_changed(args.theme)

    # Attach safe logging stream
    setup_safe_streams(app)

    # Handle initial input if provided
    if args.input:
        in_path = Path(args.input).resolve()
        app.handle_dropped_paths([in_path])

    # Run mainloop
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
