"""
repair.py - Core Chart Library Maintenance & Repair Utilities.

Provides automated cleaning and repair for Clone Hero / YARG libraries:
1. Eliminates white dot artifacts from broken Milo DXT1 album.png images.
2. Ensures all song.ini files have 'icon = fnf' so YARG places them under
   'FNF' instead of 'Unknown Sources'.
3. Detects and reports duplicate songs or conflicting folders.
"""

from __future__ import annotations
import os
import time
from pathlib import Path
from collections import defaultdict
from typing import Callable, Optional, Dict, Any, List

from .image import repair_album_file
from .ini import ensure_ini_icon


def repair_chart_library(
    target_dir: str | Path,
    fix_art: bool = True,
    fix_icons: bool = True,
    icon_tag: str = "fnf",
    dry_run: bool = False,
    progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Scans target chart directory, repairing album images and song.ini icons.
    Returns a comprehensive stats dictionary.
    """
    root = Path(target_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    song_folders: List[Path] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            if (item / "song.ini").exists() or (item / "notes.mid").exists():
                song_folders.append(item)

    total_songs = len(song_folders)
    stats: Dict[str, Any] = {
        "target_dir": str(root),
        "total_songs": total_songs,
        "images_checked": 0,
        "images_repaired": 0,
        "pixels_fixed": 0,
        "inis_checked": 0,
        "inis_updated": 0,
        "duplicates": [],
        "errors": [],
    }

    songs_by_meta = defaultdict(list)
    t0 = time.perf_counter()

    for idx, song_folder in enumerate(song_folders, 1):
        # 1. Check and repair song.ini icon
        ini_file = song_folder / "song.ini"
        song_name = ""
        song_artist = ""
        if ini_file.is_file():
            stats["inis_checked"] += 1
            try:
                content = ini_file.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip().lower()
                        if k == "name":
                            song_name = v.strip()
                        elif k == "artist":
                            song_artist = v.strip()

                if fix_icons:
                    if dry_run:
                        if "icon" not in content.lower():
                            stats["inis_updated"] += 1
                    else:
                        updated, _ = ensure_ini_icon(ini_file, icon=icon_tag)
                        if updated:
                            stats["inis_updated"] += 1
            except Exception as exc:
                stats["errors"].append(f"{song_folder.name} song.ini error: {exc}")

        # Track metadata for duplicate detection
        if song_name or song_artist:
            meta_key = (song_artist.lower().strip(), song_name.lower().strip())
            songs_by_meta[meta_key].append(song_folder.name)

        # 2. Check and repair album.png
        album_file = song_folder / "album.png"
        if fix_art and album_file.is_file():
            stats["images_checked"] += 1
            try:
                if dry_run:
                    from PIL import Image
                    import numpy as np
                    from .image import clean_white_dots_array
                    with Image.open(album_file) as im:
                        arr = np.array(im.convert("RGB"))
                        _, fixed = clean_white_dots_array(arr)
                        if fixed > 0:
                            stats["images_repaired"] += 1
                            stats["pixels_fixed"] += fixed
                else:
                    repaired, fixed = repair_album_file(album_file)
                    if repaired:
                        stats["images_repaired"] += 1
                        stats["pixels_fixed"] += fixed
            except Exception as exc:
                stats["errors"].append(f"{song_folder.name} album.png error: {exc}")

        if progress_callback:
            progress_callback(idx, total_songs, song_folder.name, stats)

    # 3. Analyze duplicate collisions
    for (artist, title), folders in songs_by_meta.items():
        if len(folders) > 1 and (artist or title):
            stats["duplicates"].append({
                "artist": artist,
                "title": title,
                "folders": folders,
            })

    stats["elapsed_seconds"] = time.perf_counter() - t0
    return stats
