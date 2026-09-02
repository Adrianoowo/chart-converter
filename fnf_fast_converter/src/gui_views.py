"""
gui_views.py - Modern Dark Onyx CustomTkinter UI Components.

Provides high-performance CustomTkinter view components:
- Header bar with title, subtitle, and branding
- Settings toolbar & expandable advanced settings card
- Queue toolbar with Add/Remove/Clear/Retry and Start/Cancel actions
- Chunked lazy-rendered queue table with row cards, animated status badges, and context menus
- Global progress section with throughput & ETA metrics
- Collapsible live log console drawer with color-coded autoscrolling output
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

import customtkinter as ctk

from .gui_config import AppConfig
from .gui_queue import QueueItem, QueueItemState, format_byte_size
from .gui_stats import format_duration, format_throughput

# Spinner frames for active "Converting" state badge
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Onyx Dark Palette
COLOR_BG = "#121214"
COLOR_CARD = "#1c1c1f"
COLOR_CARD_HOVER = "#232328"
COLOR_CARD_SELECTED = "#282830"
COLOR_BORDER = "#2e2e33"
COLOR_TEXT_MAIN = "#f4f4f5"
COLOR_TEXT_MUTED = "#a1a1aa"
COLOR_ACCENT = "#6366f1"
COLOR_ACCENT_HOVER = "#4f46e5"
COLOR_SUCCESS = "#16a34a"
COLOR_WARNING = "#d97706"
COLOR_ERROR = "#dc2626"
COLOR_INFO = "#0284c7"
COLOR_PENDING = "#3f3f46"


class HeaderView(ctk.CTkFrame):
    """Header bar with app title, subtitle, and branding badge."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)

        # Left title group
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="w", padx=16, pady=10)

        main_title = ctk.CTkLabel(
            title_frame,
            text="FNF FAST CONVERTER",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLOR_TEXT_MAIN,
        )
        main_title.pack(side="left", padx=(0, 10))

        badge = ctk.CTkLabel(
            title_frame,
            text="ROCK BAND CON ➔ CLONE HERO",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLOR_ACCENT,
            text_color="#ffffff",
            corner_radius=4,
            padx=8,
            pady=2,
        )
        badge.pack(side="left")

        # Subtitle
        subtitle = ctk.CTkLabel(
            self,
            text="High-speed in-memory STFS parser & multi-threaded MOGG stem demuxer",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MUTED,
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))


class SettingsToolbarView(ctk.CTkFrame):
    """Settings toolbar for output directory, overwrite toggle, and advanced settings expander."""

    def __init__(
        self,
        parent: Any,
        config: AppConfig,
        on_browse_output: Callable[[], None],
        on_toggle_advanced: Callable[[], None],
        on_config_changed: Callable[[], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self.config = config
        self.on_browse_output = on_browse_output
        self.on_toggle_advanced = on_toggle_advanced
        self.on_config_changed = on_config_changed
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)

        # Output Dir Label
        lbl_out = ctk.CTkLabel(
            self,
            text="Output Directory:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_out.grid(row=0, column=0, padx=(16, 8), pady=10, sticky="w")

        # Output Dir Entry
        self.entry_out = ctk.CTkEntry(
            self,
            placeholder_text="Select Clone Hero songs output directory...",
            font=ctk.CTkFont(size=12),
            fg_color="#121214",
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MAIN,
        )
        if self.config.output_dir:
            self.entry_out.insert(0, self.config.output_dir)
        self.entry_out.grid(row=0, column=1, padx=4, pady=10, sticky="ew")
        self.entry_out.bind("<FocusOut>", lambda e: self._on_entry_changed())
        self.entry_out.bind("<Return>", lambda e: self._on_entry_changed())

        # Browse Button
        self.btn_browse = ctk.CTkButton(
            self,
            text="Browse...",
            width=90,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            command=self.on_browse_output,
        )
        self.btn_browse.grid(row=0, column=2, padx=(4, 12), pady=10)

        # Overwrite Switch
        self.var_overwrite = ctk.BooleanVar(value=self.config.overwrite)
        self.switch_overwrite = ctk.CTkSwitch(
            self,
            text="Overwrite Existing",
            font=ctk.CTkFont(size=12),
            variable=self.var_overwrite,
            command=self._on_switch_overwrite,
            progress_color=COLOR_ACCENT,
        )
        self.switch_overwrite.grid(row=0, column=3, padx=(8, 12), pady=10)

        # Advanced Settings Toggle Button
        self.btn_advanced = ctk.CTkButton(
            self,
            text="⚙ Advanced ▾",
            width=110,
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_toggle_advanced,
        )
        self.btn_advanced.grid(row=0, column=4, padx=(4, 16), pady=10)

    def set_output_dir(self, path_str: str) -> None:
        self.entry_out.delete(0, "end")
        self.entry_out.insert(0, path_str)
        self.config.output_dir = path_str
        self.on_config_changed()

    def get_output_dir(self) -> str:
        return self.entry_out.get().strip()

    def _on_entry_changed(self) -> None:
        self.config.output_dir = self.get_output_dir()
        self.on_config_changed()

    def _on_switch_overwrite(self) -> None:
        self.config.overwrite = self.var_overwrite.get()
        self.on_config_changed()


class AdvancedSettingsCard(ctk.CTkFrame):
    """Expandable card containing worker threads, stem threads, and charter settings."""

    def __init__(
        self,
        parent: Any,
        config: AppConfig,
        on_config_changed: Callable[[], None],
        on_collapse: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            fg_color="#18181b",
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self.config = config
        self.on_config_changed = on_config_changed
        self.on_collapse = on_collapse
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure((1, 3, 5), weight=1)

        # Header bar with title and collapse button
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, columnspan=6, sticky="ew", padx=16, pady=(10, 4))
        header_frame.columnconfigure(0, weight=1)

        lbl_header = ctk.CTkLabel(
            header_frame,
            text="⚙ Advanced Configuration",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_header.grid(row=0, column=0, sticky="w")

        if self.on_collapse:
            btn_close = ctk.CTkButton(
                header_frame,
                text="▲ Collapse",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#27272a",
                hover_color="#3f3f46",
                text_color=COLOR_TEXT_MAIN,
                height=26,
                width=90,
                command=self.on_collapse,
            )
            btn_close.grid(row=0, column=1, sticky="e")

        # 1. Worker Threads
        lbl_workers = ctk.CTkLabel(
            self,
            text="Worker Threads (Songs):",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_workers.grid(row=1, column=0, padx=(16, 6), pady=(8, 12), sticky="w")

        self.slider_workers = ctk.CTkSlider(
            self,
            from_=1,
            to=16,
            number_of_steps=15,
            command=self._on_workers_changed,
            progress_color=COLOR_ACCENT,
        )
        self.slider_workers.set(self.config.worker_threads)
        self.slider_workers.grid(row=1, column=1, padx=4, pady=(8, 12), sticky="ew")

        self.lbl_workers_val = ctk.CTkLabel(
            self,
            text=str(self.config.worker_threads),
            font=ctk.CTkFont(size=12, weight="bold"),
            width=28,
            text_color=COLOR_TEXT_MAIN,
        )
        self.lbl_workers_val.grid(row=1, column=2, padx=(2, 16), pady=(8, 12))

        # 2. Audio Stem Threads
        lbl_stems = ctk.CTkLabel(
            self,
            text="Audio Stem Threads:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_stems.grid(row=1, column=3, padx=(8, 6), pady=(8, 12), sticky="w")

        self.slider_stems = ctk.CTkSlider(
            self,
            from_=1,
            to=8,
            number_of_steps=7,
            command=self._on_stems_changed,
            progress_color=COLOR_ACCENT,
        )
        self.slider_stems.set(self.config.stem_threads)
        self.slider_stems.grid(row=1, column=4, padx=4, pady=(8, 12), sticky="ew")

        self.lbl_stems_val = ctk.CTkLabel(
            self,
            text=str(self.config.stem_threads),
            font=ctk.CTkFont(size=12, weight="bold"),
            width=28,
            text_color=COLOR_TEXT_MAIN,
        )
        self.lbl_stems_val.grid(row=1, column=5, padx=(2, 16), pady=(8, 12))

        # 3. Charter Name
        lbl_charter = ctk.CTkLabel(
            self,
            text="Default Charter:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_charter.grid(row=2, column=0, padx=(16, 6), pady=(0, 12), sticky="w")

        self.entry_charter = ctk.CTkEntry(
            self,
            font=ctk.CTkFont(size=12),
            fg_color="#121214",
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MAIN,
            width=140,
        )
        self.entry_charter.insert(0, self.config.charter)
        self.entry_charter.grid(row=2, column=1, padx=4, pady=(0, 12), sticky="w")
        self.entry_charter.bind("<FocusOut>", lambda e: self._on_charter_changed())
        self.entry_charter.bind("<Return>", lambda e: self._on_charter_changed())

        # 4. Theme Selector
        lbl_theme = ctk.CTkLabel(
            self,
            text="Theme Mode:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        lbl_theme.grid(row=2, column=3, padx=(8, 6), pady=(0, 12), sticky="w")

        self.opt_theme = ctk.CTkOptionMenu(
            self,
            values=["Dark", "Light", "System"],
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_HOVER,
            command=self._on_theme_changed,
            width=120,
        )
        self.opt_theme.set(self.config.dark_theme)
        self.opt_theme.grid(row=2, column=4, padx=4, pady=(0, 12), sticky="w")

    def _on_workers_changed(self, val: float) -> None:
        int_val = int(round(val))
        self.lbl_workers_val.configure(text=str(int_val))
        self.config.worker_threads = int_val
        self.on_config_changed()

    def _on_stems_changed(self, val: float) -> None:
        int_val = int(round(val))
        self.lbl_stems_val.configure(text=str(int_val))
        self.config.stem_threads = int_val
        self.on_config_changed()

    def _on_charter_changed(self) -> None:
        self.config.charter = self.entry_charter.get().strip() or "Dansla116"
        self.on_config_changed()

    def _on_theme_changed(self, choice: str) -> None:
        self.config.dark_theme = choice
        ctk.set_appearance_mode(choice)
        self.on_config_changed()


class QueueToolbarView(ctk.CTkFrame):
    """Toolbar with queue manipulation buttons and primary Start/Cancel action."""

    def __init__(
        self,
        parent: Any,
        on_add_files: Callable[[], None],
        on_add_folder: Callable[[], None],
        on_remove_selected: Callable[[], None],
        on_clear_all: Callable[[], None],
        on_retry_failed: Callable[[], None],
        on_start_conversion: Callable[[], None],
        on_cancel_conversion: Callable[[], None],
        on_select_all: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.on_add_files = on_add_files
        self.on_add_folder = on_add_folder
        self.on_remove_selected = on_remove_selected
        self.on_clear_all = on_clear_all
        self.on_retry_failed = on_retry_failed
        self.on_start_conversion = on_start_conversion
        self.on_cancel_conversion = on_cancel_conversion
        self.on_select_all = on_select_all
        self._is_converting = False
        self._build_ui()

    def _build_ui(self) -> None:
        # Left Actions
        self.btn_add_files = ctk.CTkButton(
            self,
            text="➕ Add Files...",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_add_files,
            width=110,
        )
        self.btn_add_files.pack(side="left", padx=(0, 4))

        self.btn_add_folder = ctk.CTkButton(
            self,
            text="📁 Add Folder...",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_add_folder,
            width=115,
        )
        self.btn_add_folder.pack(side="left", padx=4)

        self.btn_select_all = ctk.CTkButton(
            self,
            text="☑ Select All",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self._on_select_all_clicked,
            width=105,
        )
        self.btn_select_all.pack(side="left", padx=4)

        self.btn_remove_sel = ctk.CTkButton(
            self,
            text="🗑 Remove Selected",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_remove_selected,
            width=135,
        )
        self.btn_remove_sel.pack(side="left", padx=4)

        self.btn_retry_failed = ctk.CTkButton(
            self,
            text="🔄 Retry Failed",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_retry_failed,
            width=105,
        )
        self.btn_retry_failed.pack(side="left", padx=4)

        self.btn_clear_all = ctk.CTkButton(
            self,
            text="Clear All",
            font=ctk.CTkFont(size=12),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self.on_clear_all,
            width=80,
        )
        self.btn_clear_all.pack(side="left", padx=4)

        # Right Action: Start / Cancel Conversion Button
        self.btn_main_action = ctk.CTkButton(
            self,
            text="🚀 Start Conversion",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLOR_SUCCESS,
            hover_color="#15803d",
            command=self._on_main_action_clicked,
            width=170,
            height=34,
        )
        self.btn_main_action.pack(side="right")

        # Total Files Counter Badge Frame
        self.frame_counter = ctk.CTkFrame(
            self,
            fg_color="#18181b",
            corner_radius=6,
            border_width=1,
            border_color=COLOR_BORDER,
            height=34,
        )
        self.frame_counter.pack(side="right", padx=(0, 10))

        self.lbl_counter = ctk.CTkLabel(
            self.frame_counter,
            text="Total: 0 files",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_MUTED,
            padx=12,
            pady=4,
        )
        self.lbl_counter.pack(expand=True)

    def _on_select_all_clicked(self) -> None:
        if self.on_select_all:
            self.on_select_all()

    def update_counter(self, total_count: int, selected_count: int = 0) -> None:
        """Update total and selected files counter display."""
        if total_count == 0:
            self.lbl_counter.configure(
                text="Total: 0 files",
                text_color=COLOR_TEXT_MUTED,
            )
            self.btn_select_all.configure(text="☑ Select All")
        elif selected_count > 0:
            self.lbl_counter.configure(
                text=f"Total: {total_count} files ({selected_count} selected)",
                text_color=COLOR_TEXT_MAIN,
            )
            if selected_count >= total_count:
                self.btn_select_all.configure(text="☐ Deselect All")
            else:
                self.btn_select_all.configure(text="☑ Select All")
        else:
            self.lbl_counter.configure(
                text=f"Total: {total_count} files",
                text_color=COLOR_TEXT_MAIN,
            )
            self.btn_select_all.configure(text="☑ Select All")

    def _on_main_action_clicked(self) -> None:
        if self._is_converting:
            self.on_cancel_conversion()
        else:
            self.on_start_conversion()

    def set_converting_state(self, converting: bool) -> None:
        """Update toolbar button states based on active conversion status."""
        self._is_converting = converting
        if converting:
            self.btn_main_action.configure(
                text="⛔ Cancel Conversion",
                fg_color=COLOR_ERROR,
                hover_color="#b91c1c",
            )
            self.btn_add_files.configure(state="disabled")
            self.btn_add_folder.configure(state="disabled")
            self.btn_select_all.configure(state="disabled")
            self.btn_clear_all.configure(state="disabled")
            self.btn_retry_failed.configure(state="disabled")
            self.btn_remove_sel.configure(state="disabled")
        else:
            self.btn_main_action.configure(
                text="🚀 Start Conversion",
                fg_color=COLOR_SUCCESS,
                hover_color="#15803d",
            )
            self.btn_add_files.configure(state="normal")
            self.btn_add_folder.configure(state="normal")
            self.btn_select_all.configure(state="normal")
            self.btn_clear_all.configure(state="normal")
            self.btn_retry_failed.configure(state="normal")
            self.btn_remove_sel.configure(state="normal")


class QueueRowView(ctk.CTkFrame):
    """A single row card representing a queue item."""

    def __init__(
        self,
        parent: Any,
        item: QueueItem,
        on_delete: Callable[[str], None],
        on_select_toggle: Callable[[str, bool], None],
        on_context_action: Callable[[str, str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=6,
            border_width=1,
            border_color=COLOR_BORDER,
            height=46,
            **kwargs,
        )
        self.item = item
        self.on_delete = on_delete
        self.on_select_toggle = on_select_toggle
        self.on_context_action = on_context_action
        self._spinner_idx = 0
        self._selected = False
        self.menu: Optional[tk.Menu] = None
        self._build_ui()
        self._bind_context_menu()

    def _build_ui(self) -> None:
        self.columnconfigure(2, weight=1)

        # Checkbox
        self.var_sel = ctk.BooleanVar(value=False)
        self.chk = ctk.CTkCheckBox(
            self,
            text="",
            width=20,
            checkbox_width=18,
            checkbox_height=18,
            variable=self.var_sel,
            command=self._on_check_toggle,
            fg_color=COLOR_ACCENT,
        )
        self.chk.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")

        # Titles Frame
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=1, columnspan=2, padx=6, pady=4, sticky="w")

        self.lbl_title = ctk.CTkLabel(
            title_frame,
            text=self.item.display_title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_MAIN,
            anchor="w",
        )
        self.lbl_title.pack(side="top", anchor="w")

        self.lbl_sub = ctk.CTkLabel(
            title_frame,
            text=self.item.display_subtitle,
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_sub.pack(side="top", anchor="w")

        # Size badge
        self.lbl_size = ctk.CTkLabel(
            self,
            text=self.item.formatted_size,
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_MUTED,
            width=65,
        )
        self.lbl_size.grid(row=0, column=3, padx=8, pady=6)

        # Status badge
        self.badge_status = ctk.CTkButton(
            self,
            text="Pending",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLOR_PENDING,
            hover=False,
            height=24,
            corner_radius=12,
            width=95,
            command=self._on_badge_clicked,
        )
        self.badge_status.grid(row=0, column=4, padx=8, pady=6)

        # Delete Button
        self.btn_del = ctk.CTkButton(
            self,
            text="✕",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="transparent",
            hover_color="#ef4444",
            text_color=COLOR_TEXT_MUTED,
            width=24,
            height=24,
            corner_radius=4,
            command=lambda: self.on_delete(self.item.id),
        )
        self.btn_del.grid(row=0, column=5, padx=(4, 10), pady=6)

        # Initial badge state
        self.update_state(self.item.state, self.item.elapsed_time_sec, self.item.error_message)

    def _on_check_toggle(self) -> None:
        self._selected = self.var_sel.get()
        self.configure(
            fg_color=COLOR_CARD_SELECTED if self._selected else COLOR_CARD
        )
        self.on_select_toggle(self.item.id, self._selected)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.var_sel.set(selected)
        self.configure(
            fg_color=COLOR_CARD_SELECTED if selected else COLOR_CARD
        )

    def update_state(
        self,
        state: QueueItemState,
        elapsed_sec: float = 0.0,
        error_msg: str = "",
    ) -> None:
        """Update row status badge appearance and text."""
        self.item.state = state
        if elapsed_sec > 0:
            self.item.elapsed_time_sec = elapsed_sec
        if error_msg:
            self.item.error_message = error_msg

        if state == QueueItemState.PENDING:
            self.badge_status.configure(
                text="Pending",
                fg_color=COLOR_PENDING,
                text_color=COLOR_TEXT_MAIN,
                hover=False,
            )
        elif state == QueueItemState.CONVERTING:
            spinner = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
            self.badge_status.configure(
                text=f"Converting {spinner}",
                fg_color=COLOR_INFO,
                text_color="#ffffff",
                hover=False,
            )
        elif state == QueueItemState.DONE:
            time_txt = f"{self.item.elapsed_time_sec:.1f}s" if self.item.elapsed_time_sec > 0 else ""
            txt = f"Done {time_txt}".strip()
            self.badge_status.configure(
                text=txt,
                fg_color=COLOR_SUCCESS,
                text_color="#ffffff",
                hover=False,
            )
        elif state == QueueItemState.SKIPPED:
            self.badge_status.configure(
                text="Skipped",
                fg_color=COLOR_WARNING,
                text_color="#ffffff",
                hover=False,
            )
        elif state == QueueItemState.ERROR:
            self.badge_status.configure(
                text="Error ⚠",
                fg_color=COLOR_ERROR,
                text_color="#ffffff",
                hover=True,
            )

    def advance_spinner(self) -> None:
        """Advance animated spinner frame for converting rows."""
        if self.item.state == QueueItemState.CONVERTING:
            self._spinner_idx += 1
            spinner = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
            self.badge_status.configure(text=f"Converting {spinner}")

    def _on_badge_clicked(self) -> None:
        if self.item.state == QueueItemState.ERROR:
            msg = self.item.error_message or "An unknown conversion error occurred."
            messagebox.showerror(
                f"Conversion Error — {self.item.display_title}",
                f"File: {self.item.file_path}\n\nDetails:\n{msg}",
            )

    def _bind_context_menu(self) -> None:
        """Bind right-click context menu event handlers."""
        self.bind("<Button-3>", self._show_context_menu)
        self.lbl_title.bind("<Button-3>", self._show_context_menu)
        self.lbl_sub.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event: Any) -> None:
        """Lazily instantiate tk.Menu on right-click to avoid Win32 OS HMENU exhaustion."""
        if self.menu is not None:
            try:
                self.menu.destroy()
            except Exception:
                pass
            self.menu = None

        self.menu = tk.Menu(self, tearoff=0, bg="#1f1f23", fg="#ffffff", activebackground=COLOR_ACCENT)
        self.menu.add_command(
            label="🔍 Reveal in File Explorer",
            command=lambda: self.on_context_action(self.item.id, "reveal"),
        )
        self.menu.add_command(
            label="📁 Open Output Folder",
            command=lambda: self.on_context_action(self.item.id, "open_output"),
        )
        self.menu.add_command(
            label="📋 Copy File Path",
            command=lambda: self.on_context_action(self.item.id, "copy_path"),
        )
        self.menu.add_separator()
        self.menu.add_command(
            label="☑ Select All Songs (Ctrl+A)",
            command=lambda: self.on_context_action(self.item.id, "select_all"),
        )
        self.menu.add_command(
            label="☐ Deselect All Songs",
            command=lambda: self.on_context_action(self.item.id, "deselect_all"),
        )
        self.menu.add_separator()
        self.menu.add_command(
            label="⚠ View Error Details",
            command=lambda: self.on_context_action(self.item.id, "view_error"),
        )
        self.menu.add_command(
            label="🗑 Remove from Queue",
            command=lambda: self.on_delete(self.item.id),
        )

        try:
            x = getattr(event, "x_root", 0)
            y = getattr(event, "y_root", 0)
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def destroy(self) -> None:
        """Destroy row card and clean up context menu resource."""
        if self.menu is not None:
            try:
                self.menu.destroy()
            except Exception:
                pass
            self.menu = None
        super().destroy()


class QueueTableView(ctk.CTkFrame):
    """
    Scrollable Queue Table supporting chunked lazy rendering for 1,000+ items
    without freezing the GUI.
    """

    def __init__(
        self,
        parent: Any,
        on_delete_item: Callable[[str], None],
        on_select_item: Callable[[str, bool], None],
        on_context_action: Callable[[str, str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_BG,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self.on_delete_item = on_delete_item
        self.on_select_item = on_select_item
        self.on_context_action = on_context_action

        self.row_views: Dict[str, QueueRowView] = {}
        self.selected_ids: Set[str] = set()
        self._lazy_queue: List[QueueItem] = []
        self._lazy_job: Optional[str] = None

        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            label_text="",
        )
        self.scroll_frame.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.scroll_frame.columnconfigure(0, weight=1)

        # Empty queue placeholder
        self.lbl_empty = ctk.CTkLabel(
            self.scroll_frame,
            text="📂 Drag and drop Rock Band CON files or folders here\nor click '➕ Add Files...' / '📁 Add Folder...'",
            font=ctk.CTkFont(size=14),
            text_color=COLOR_TEXT_MUTED,
            justify="center",
        )
        self.lbl_empty.pack(expand=True, pady=100)

    def add_items_lazy(
        self,
        items: Sequence[QueueItem],
        chunk_size: int = 25,
        chunk_delay_ms: int = 5,
    ) -> None:
        """Add items to table in chunked asynchronous batches for silky smooth rendering."""
        if not items:
            return

        if self.lbl_empty.winfo_ismapped():
            self.lbl_empty.pack_forget()

        self._lazy_queue.extend(items)
        if self._lazy_job is None:
            self._process_lazy_chunk(chunk_size, chunk_delay_ms)

    def _process_lazy_chunk(self, chunk_size: int, delay_ms: int) -> None:
        if not self._lazy_queue:
            self._lazy_job = None
            return

        chunk = self._lazy_queue[:chunk_size]
        self._lazy_queue = self._lazy_queue[chunk_size:]

        for item in chunk:
            if item.id not in self.row_views:
                row = QueueRowView(
                    self.scroll_frame,
                    item=item,
                    on_delete=self._handle_delete_row,
                    on_select_toggle=self._handle_select_toggle,
                    on_context_action=self.on_context_action,
                )
                if item.id in self.selected_ids:
                    row.set_selected(True)
                row.pack(fill="x", padx=4, pady=2)
                self.row_views[item.id] = row

        if self._lazy_queue:
            self._lazy_job = self.after(
                delay_ms, lambda: self._process_lazy_chunk(chunk_size, delay_ms)
            )
        else:
            self._lazy_job = None

    def select_all(self) -> None:
        """Select all items currently in table and pending in lazy queue."""
        for item_id, row in self.row_views.items():
            row.set_selected(True)
            self.selected_ids.add(item_id)
        for it in self._lazy_queue:
            self.selected_ids.add(it.id)
        self.on_select_item("", True)

    def deselect_all(self) -> None:
        """Deselect all items currently in table and pending in lazy queue."""
        for item_id, row in self.row_views.items():
            row.set_selected(False)
        self.selected_ids.clear()
        self.on_select_item("", False)

    def toggle_select_all(self) -> bool:
        """
        Toggle selection for all items in queue.
        Returns True if all selected, False if all deselected.
        """
        total = len(self.row_views) + len(self._lazy_queue)
        if total > 0 and len(self.selected_ids) >= total:
            self.deselect_all()
            return False
        else:
            self.select_all()
            return True

    def _handle_delete_row(self, item_id: str) -> None:
        self.remove_item(item_id)
        self.on_delete_item(item_id)

    def _handle_select_toggle(self, item_id: str, selected: bool) -> None:
        if selected:
            self.selected_ids.add(item_id)
        else:
            self.selected_ids.discard(item_id)
        self.on_select_item(item_id, selected)

    def remove_item(self, item_id: str) -> None:
        """Remove a single row from view."""
        self.selected_ids.discard(item_id)
        row = self.row_views.pop(item_id, None)
        if row is not None:
            row.destroy()

        if not self.row_views and not self._lazy_queue:
            self.lbl_empty.pack(expand=True, pady=100)

    def remove_items(self, item_ids: Sequence[str]) -> None:
        """Remove multiple rows from view."""
        for iid in item_ids:
            self.remove_item(iid)

    def clear(self) -> None:
        """Clear all rows from view."""
        if self._lazy_job is not None:
            self.after_cancel(self._lazy_job)
            self._lazy_job = None
        self._lazy_queue.clear()
        self.selected_ids.clear()

        for row in self.row_views.values():
            row.destroy()
        self.row_views.clear()

        self.lbl_empty.pack(expand=True, pady=100)

    def update_row_state(
        self,
        item_id: str,
        state: QueueItemState,
        elapsed_sec: float = 0.0,
        error_msg: str = "",
    ) -> None:
        """Update row status badge."""
        row = self.row_views.get(item_id)
        if row is not None:
            row.update_state(state, elapsed_sec, error_msg)

    def animate_spinners(self) -> None:
        """Advance spinner frames on all converting rows."""
        for row in self.row_views.values():
            if row.item.state == QueueItemState.CONVERTING:
                row.advance_spinner()


class GlobalProgressView(ctk.CTkFrame):
    """Global conversion progress bar with live metrics, throughput, and ETA."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)

        # Progress Bar
        self.progress_bar = ctk.CTkProgressBar(
            self,
            height=14,
            corner_radius=7,
            progress_color=COLOR_ACCENT,
            fg_color="#121214",
        )
        self.progress_bar.set(0.0)
        self.progress_bar.grid(
            row=0, column=0, columnspan=5, sticky="ew", padx=16, pady=(12, 8)
        )

        # Metrics Labels
        self.lbl_percent = ctk.CTkLabel(
            self,
            text="0% (0 / 0 songs)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_MAIN,
        )
        self.lbl_percent.grid(row=1, column=0, sticky="w", padx=(16, 8), pady=(0, 10))

        self.lbl_counts = ctk.CTkLabel(
            self,
            text="0 Done · 0 Skipped · 0 Failed",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MUTED,
        )
        self.lbl_counts.grid(row=1, column=1, sticky="w", padx=8, pady=(0, 10))

        self.lbl_speed = ctk.CTkLabel(
            self,
            text="⚡ 0.0 songs/s",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        self.lbl_speed.grid(row=1, column=2, sticky="e", padx=8, pady=(0, 10))

        self.lbl_eta = ctk.CTkLabel(
            self,
            text="⏱ ETA: --:--",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MAIN,
        )
        self.lbl_eta.grid(row=1, column=3, sticky="e", padx=8, pady=(0, 10))

        self.lbl_elapsed = ctk.CTkLabel(
            self,
            text="⌛ 00:00",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MUTED,
        )
        self.lbl_elapsed.grid(row=1, column=4, sticky="e", padx=(8, 16), pady=(0, 10))

    def update_metrics(self, metrics: Dict[str, Any]) -> None:
        """Update progress bar and metrics labels."""
        self.progress_bar.set(metrics.get("progress_fraction", 0.0))

        pct = metrics.get("percent", 0.0)
        proc = metrics.get("processed", 0)
        tot = metrics.get("total", 0)
        self.lbl_percent.configure(text=f"{pct:.0f}% ({proc} / {tot} songs)")

        done = metrics.get("done", 0)
        skipped = metrics.get("skipped", 0)
        error = metrics.get("error", 0)
        self.lbl_counts.configure(text=f"{done} Done · {skipped} Skipped · {error} Failed")

        self.lbl_speed.configure(text=f"⚡ {metrics.get('throughput_str', '0.0 songs/s')}")
        self.lbl_eta.configure(text=f"⏱ ETA: {metrics.get('eta_str', '--:--')}")
        self.lbl_elapsed.configure(text=f"⌛ {metrics.get('elapsed_str', '00:00')}")

    def set_total_items(self, total: int) -> None:
        """Update queued songs count before/after conversion."""
        if self.progress_bar.get() == 0.0:
            self.lbl_percent.configure(text=f"0% (0 / {total} songs)")

    def reset(self) -> None:
        """Reset progress view to zero."""
        self.progress_bar.set(0.0)
        self.lbl_percent.configure(text="0% (0 / 0 songs)")
        self.lbl_counts.configure(text="0 Done · 0 Skipped · 0 Failed")
        self.lbl_speed.configure(text="⚡ 0.0 songs/s")
        self.lbl_eta.configure(text="⏱ ETA: --:--")
        self.lbl_elapsed.configure(text="⌛ 00:00")


class LogConsoleView(ctk.CTkFrame):
    """Collapsible live log console drawer with color-coded message tags."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        self._is_collapsed = True
        self._auto_scroll = True
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)

        # Header Bar
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=6)

        self.btn_toggle = ctk.CTkButton(
            header,
            text="▶ Show Live Log Console",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent",
            hover_color="#27272a",
            text_color=COLOR_TEXT_MAIN,
            anchor="w",
            command=self.toggle_collapse,
            height=26,
        )
        self.btn_toggle.pack(side="left")

        # Right Action Buttons
        self.btn_clear = ctk.CTkButton(
            header,
            text="Clear",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#3f3f46",
            width=55,
            height=24,
            command=self.clear_logs,
        )
        self.btn_clear.pack(side="right", padx=(4, 0))

        self.btn_copy = ctk.CTkButton(
            header,
            text="Copy All",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#3f3f46",
            width=65,
            height=24,
            command=self.copy_all,
        )
        self.btn_copy.pack(side="right", padx=4)

        # Body Container
        self.body_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.textbox = ctk.CTkTextbox(
            self.body_frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#101012",
            border_color=COLOR_BORDER,
            border_width=1,
            text_color=COLOR_TEXT_MAIN,
            height=140,
            wrap="word",
        )
        self.textbox.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Color tags
        self.textbox.tag_config("INFO", foreground="#a1a1aa")
        self.textbox.tag_config("SUCCESS", foreground="#4ade80")
        self.textbox.tag_config("WARNING", foreground="#fbbf24")
        self.textbox.tag_config("ERROR", foreground="#f87171")

    def toggle_collapse(self) -> None:
        """Expand or collapse the live log drawer."""
        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            self.body_frame.pack_forget()
            self.btn_toggle.configure(text="▶ Show Live Log Console")
        else:
            self.body_frame.pack(fill="both", expand=True)
            self.btn_toggle.configure(text="▼ Hide Live Log Console")

    def append_log(self, message: str, level: str = "INFO") -> None:
        """Append a log message to the console with color-coded level tag."""
        msg = message.strip()
        if not msg:
            return

        level_tag = level.upper()
        if level_tag not in ("INFO", "SUCCESS", "WARNING", "ERROR"):
            level_tag = "INFO"

        self.textbox.insert("end", f"{msg}\n", level_tag)
        if self._auto_scroll:
            self.textbox.see("end")

    def clear_logs(self) -> None:
        """Clear all messages from log console."""
        self.textbox.delete("1.0", "end")

    def copy_all(self) -> None:
        """Copy entire log buffer to clipboard."""
        content = self.textbox.get("1.0", "end-1c")
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
