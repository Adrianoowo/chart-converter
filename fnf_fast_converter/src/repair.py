"""
repair.py - Core Chart Library Maintenance & Repair Utilities.

Provides automated cleaning and repair for Clone Hero / YARG libraries:
1. Detects and restores authentic album art for corrupted DXT5 textures or missing images from source CON packages.
2. Eliminates isolated white dot noise artifacts from broken Milo DXT1 album.png images.
3. Ensures all song.ini files have 'icon = fnf' so YARG places them under
   'FNF' instead of 'Unknown Sources'.
4. Detects and reports duplicate songs or conflicting folders.
"""

from __future__ import annotations
import os
import re
import time
from pathlib import Path
from collections import defaultdict
from typing import Callable, Optional, Dict, Any, List

from .image import repair_album_file, is_corrupted_album_art, decode_png_xbox
from .ini import ensure_ini_icon
from .stfs import STFSPackage
from .dta import parse_dta
from .pipeline import sanitize_folder_name


def build_con_index(source_dir: str | Path) -> Dict[str, Path]:
    """
    Builds a fast multi-key lookup index of CON package files in source_dir.
    Keys include:
      - lowercase filename
      - sanitized folder name
      - punctuation-stripped filename
      - normalized "artist__title" and sanitized "artist - title" from songs.dta
    """
    src = Path(source_dir)
    if not src.is_dir():
        return {}

    index: Dict[str, Path] = {}
    for p in src.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue

        p_name_l = p.name.lower()
        index[p_name_l] = p
        index[sanitize_folder_name(p.name).lower()] = p
        index[re.sub(r"[^a-z0-9]", "", p_name_l)] = p

        # Parse songs.dta from CON package to index exact artist/title
        try:
            with STFSPackage.from_file(p) as pkg:
                for f in pkg.list_files():
                    if f.lower().endswith("songs.dta"):
                        dta = parse_dta(pkg.get_file_bytes(f))
                        artist = dta.get("artist") or ""
                        name = dta.get("name") or ""
                        if artist or name:
                            pair_key = f"{re.sub(r'[^a-z0-9]', '', artist.lower())}__{re.sub(r'[^a-z0-9]', '', name.lower())}"
                            index[pair_key] = p
                            folder_key = sanitize_folder_name(f"{artist} - {name}").lower()
                            index[folder_key] = p
                        break
        except Exception:
            pass

    return index


def extract_cover_from_con(con_path: Path | str) -> Optional[bytes]:
    """
    Extracts and decodes the Milo .png_xbox / .bmp_xbox texture from a CON file
    into valid PNG bytes using decode_png_xbox.
    """
    try:
        with STFSPackage.from_file(con_path) as pkg:
            for f in pkg.list_files():
                f_l = f.lower()
                if f_l.endswith(".png_xbox") or f_l.endswith(".png_xbo") or f_l.endswith(".bmp_xbox") or "_keep." in f_l:
                    raw = pkg.get_file_bytes(f)
                    return decode_png_xbox(raw)
            # Fallback to package thumbnail
            thumb = pkg.get_thumbnail()
            if thumb and len(thumb) > 8 and (thumb.startswith(b"\x89PNG\r\n\x1a\n") or thumb.startswith(b"\xff\xd8")):
                return thumb
    except Exception:
        pass
    return None


def repair_chart_library(
    target_dir: str | Path,
    source_dir: Optional[str | Path] = None,
    fix_art: bool = True,
    fix_icons: bool = True,
    icon_tag: str = "fnf",
    dry_run: bool = False,
    progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Scans target chart directory, repairing album images and song.ini icons.
    Optionally restores corrupted DXT5 album art directly from source CON packages.
    Returns a comprehensive stats dictionary.
    """
    root = Path(target_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    # Auto-detect default CON download folder if not explicitly provided
    if source_dir is None:
        default_con = Path(r"C:\Users\adema\Downloads\FNFestivaltoRB-main")
        if default_con.is_dir():
            source_dir = default_con

    con_index: Dict[str, Path] = {}
    if source_dir and Path(source_dir).is_dir():
        con_index = build_con_index(source_dir)

    song_folders: List[Path] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            if (item / "song.ini").exists() or (item / "notes.mid").exists():
                song_folders.append(item)

    total_songs = len(song_folders)
    stats: Dict[str, Any] = {
        "target_dir": str(root),
        "source_dir": str(source_dir) if source_dir else None,
        "total_songs": total_songs,
        "images_checked": 0,
        "images_repaired": 0,
        "images_restored_from_con": 0,
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
        if fix_art:
            needs_con_restore = False
            if album_file.is_file():
                stats["images_checked"] += 1
                try:
                    if is_corrupted_album_art(album_file):
                        needs_con_restore = True
                    else:
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
            else:
                needs_con_restore = True

            # Attempt restoration from CON package if corrupted or missing
            if needs_con_restore and con_index:
                k1 = song_folder.name.lower()
                k2 = sanitize_folder_name(song_folder.name).lower()
                k3 = re.sub(r"[^a-z0-9]", "", k1)
                pair_k = (
                    f"{re.sub(r'[^a-z0-9]', '', song_artist.lower())}__{re.sub(r'[^a-z0-9]', '', song_name.lower())}"
                    if (song_artist or song_name)
                    else ""
                )

                con_match = (
                    con_index.get(k1)
                    or con_index.get(k2)
                    or con_index.get(k3)
                    or (con_index.get(pair_k) if pair_k else None)
                )
                if con_match:
                    try:
                        png_bytes = extract_cover_from_con(con_match)
                        if png_bytes:
                            if not dry_run:
                                album_file.write_bytes(png_bytes)
                            stats["images_restored_from_con"] += 1
                            stats["images_repaired"] += 1
                    except Exception as exc:
                        stats["errors"].append(f"{song_folder.name} CON restore error: {exc}")

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

