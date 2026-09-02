# FNF Fast Converter (High-Speed Rock Band CON to Clone Hero Converter)

High-performance native converter tool for Xbox 360 Rock Band CON packages to Clone Hero song folders, featuring an Onyx-inspired dark CustomTkinter desktop GUI, Windows drag-and-drop, multi-threaded audio stem extraction, and standalone distribution.

## Features
- **In-Memory STFS Parser**: Direct zero-copy reading of Rock Band CON archives.
- **High-Speed MOGG Audio Demuxer**: Multi-threaded extraction and mixing of Vorbis stems (`song.ogg`, `guitar.ogg`, `drums.ogg`, `vocals.ogg`, `rhythm.ogg`).
- **DTA Metadata & MIDI Extractor**: Accurate generation of `song.ini` with `icon = fnf`, track difficulties, preview times, and clean note charts (`notes.mid`).
- **Modern Dark Desktop GUI**: CustomTkinter interface with Onyx queue styling, live progress, throughput (songs/s), and real-time logs.
- **Drag-and-Drop Ingestion**: Drop CON files or folders directly into the conversion queue.
- **One-Click Execution & Portable Packaging**: Double-click `Run_GUI.bat` to launch from source or run `build_standalone.bat` to generate a standalone Windows executable.

## Quick Start (Running from Source)
1. Ensure Python 3.9+ is installed.
2. Double-click `Run_GUI.bat` to automatically verify dependencies and launch the GUI.

## Building Standalone Executable
Double-click `build_standalone.bat` (or run `python build_standalone.py`) to build the portable standalone distribution in `dist/FNF_Fast_Converter/`.
