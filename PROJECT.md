# Project: CustomTkinter Desktop GUI & Standalone Distribution for Fast CON to Clone Hero Converter

## Architecture
- **Layer 1: Core Engine Integration (`fnf_fast_converter.src`)**: Uses `pipeline.convert_con_to_song_folder`, `pipeline.is_valid_converted_song`, `stfs.STFSPackage`, `dta.parse_dta`.
- **Layer 2: Data Models & Persistence (`gui_config.py`, `gui_queue.py`, `gui_scanner.py`)**: Pure Python models for Queue items, State transitions, Recursive folder traversal, and JSON config persistence.
- **Layer 3: Asynchronous Worker Engine (`gui_worker.py`, `gui_stats.py`)**: `ThreadPoolExecutor` worker pool, thread-safe `queue.Queue` communication, live throughput & ETA calculator, safe stream redirection (`SafeStreamWriter`).
- **Layer 4: Modern CustomTkinter UI (`gui_views.py`, `gui_app.py`, `gui.py`)**: Dark Onyx-inspired GUI, chunked batch row rendering, animated status badges, Windows drag-and-drop (`windnd` with graceful fallback), settings toolbar, context menus, and collapsible log console.
- **Layer 5: Packaging & Distribution (`Run_GUI.bat`, `build_standalone.py`, `build_standalone.bat`)**: One-click source runner and PyInstaller `--onedir` standalone folder distribution.

## Code Layout
```
fnf_fast_converter/
├── src/
│   ├── __init__.py
│   ├── cli.py
│   ├── pipeline.py
│   ├── stfs.py
│   ├── mogg.py
│   ├── dta.py
│   ├── audio.py
│   ├── image.py
│   ├── ini.py
│   ├── gui_config.py      # Config persistence (JSON in AppData / local)
│   ├── gui_queue.py       # Queue item data structures, state machine
│   ├── gui_scanner.py     # Recursive CON scanner & fast DTA metadata preview
│   ├── gui_dnd.py         # Windows Drag-and-drop hook with safe fallback
│   ├── gui_stats.py       # Live metrics, throughput (songs/s), ETA tracker
│   ├── gui_worker.py      # Background ThreadPoolExecutor & UI event queue
│   ├── gui_views.py       # CustomTkinter widgets: queue table, badges, drawers
│   ├── gui_app.py         # Main CustomTkinter Application Controller
│   └── gui.py             # CLI/GUI entry point & SafeStreamWriter wrapper
├── tests/
│   ├── test_gui.py        # Comprehensive unit & integration GUI test suite (227 tests)
│   ├── test_gui_concurrency_stress.py # Adversarial stress & concurrency tests (18 tests)
│   ├── test_challenger_e2e_lifecycle.py # Headless app lifecycle tests (14 tests)
│   ├── test_packaging.py  # Standalone packaging & launcher tests (10 tests)
│   ├── e2e/               # Tier 1-4 existing converter tests (84 tests)
│   └── ...                # Core converter unit & stress tests (128 tests)
├── build_standalone.py    # PyInstaller packaging automation script
├── build_standalone.bat   # Windows batch script for one-click standalone compilation
├── Run_GUI.bat            # One-click Windows source launcher with dependency check
└── run_gui.py             # Direct execution entry point
```

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| F1 | Modern CustomTkinter GUI Window | High-speed, dark-themed CustomTkinter UI window with Onyx styling | M1 | ORIGINAL_REQUEST §R1 | DONE |
| F2 | File Queue Table | File queue table with File/Title, Size, Status badge, and row Actions | M1 | ORIGINAL_REQUEST §R1 | DONE |
| F3 | File & Folder Ingestion | "Add Files..." picker and "Add Folder..." recursive directory scanner | M1 | ORIGINAL_REQUEST §R1 | DONE |
| F4 | Windows Drag-and-Drop | Native `windnd` drag-and-drop ingestion with resilient fallback | M1 | ORIGINAL_REQUEST §R1 | DONE |
| F5 | Queue Controls | Remove Selected, Clear All, and Retry Failed queue actions | M1 | ORIGINAL_REQUEST §R1 | DONE |
| F6 | Non-blocking Async Conversion | Background worker thread pool with thread-safe UI message queue (60 FPS) | M2 | ORIGINAL_REQUEST §R2 | DONE |
| F7 | Row Status Indicators | Live status badges: Pending, Converting (animated), Done, Skipped, Error | M2 | ORIGINAL_REQUEST §R2 | DONE |
| F8 | Global Progress & Live Stats | % complete, processed/total, throughput (songs/s), elapsed timer, ETA | M2 | ORIGINAL_REQUEST §R2 | DONE |
| F9 | Live Log Console Drawer | Collapsible drawer with real-time pipeline output, warnings, and errors | M2 | ORIGINAL_REQUEST §R2 | DONE |
| F10 | Inline Settings Controls | Output Directory selector with Browse button and Overwrite Existing toggle | M3 | ORIGINAL_REQUEST §R3 | DONE |
| F11 | Expandable Advanced Settings | Worker Threads count, Charter name, and Audio Stem threads controls | M3 | ORIGINAL_REQUEST §R3 | DONE |
| F12 | Queue Row Context Menu | "Reveal in Explorer", "Open Output Folder", "Remove from Queue" | M3 | ORIGINAL_REQUEST §R3 | DONE |
| F13 | Configuration Persistence | Save/restore user preferences and directories in local JSON config | M3 | ORIGINAL_REQUEST §R3 | DONE |
| F14 | One-Click Source Launcher | `Run_GUI.bat` launcher with auto Python & dependency verification | M4 | ORIGINAL_REQUEST §R4 | DONE |
| F15 | Standalone PyInstaller Build | `build_standalone.py`/`build_standalone.bat` generating portable folder distribution | M4 | ORIGINAL_REQUEST §R4 | DONE |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | E2E Testing Suite Track | Comprehensive headless test suite `test_gui.py` (Tiers 1-4) covering all 15 features | none | DONE |
| M1 | Core GUI Architecture & Queue Table | `gui_queue.py`, `gui_scanner.py`, `gui_dnd.py`, `gui_views.py` (F1-F5) | none | DONE |
| M2 | Async Engine, Status Badges & Stats | `gui_worker.py`, `gui_stats.py`, log drawer & live metrics (F6-F9) | M1 | DONE |
| M3 | Settings, Context Menu & Config Persistence | `gui_config.py`, settings toolbar, context menus, JSON persistence (F10-F13) | M1 | DONE |
| M4 | Standalone Packaging & Launchers | `Run_GUI.bat`, `build_standalone.py`, `build_standalone.bat` (F14-F15) | M1, M2, M3 | DONE |
| MF | Final Verification & Coverage Hardening | 100% E2E test suite pass + Adversarial stress testing (Tier 5) + Forensic Audit | M0-M4 | DONE |
