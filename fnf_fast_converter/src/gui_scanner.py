"""
gui_scanner.py - High-Speed CON Directory Traversal & Fast DTA Metadata Preview.

Provides recursive CON file discovery and sub-millisecond in-memory metadata peeking
via STFSPackage and songs.dta AST parser without decoding audio stems.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Union

from .dta import parse_dta
from .pipeline import STFS_MAGICS
from .stfs import STFSError, STFSPackage

# Obvious non-CON extensions to skip during directory scanning for peak throughput
EXCLUDED_EXTENSIONS: Set[str] = {
    ".ogg",
    ".mp3",
    ".wav",
    ".flac",
    ".mid",
    ".midi",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".ini",
    ".txt",
    ".log",
    ".json",
    ".py",
    ".pyc",
    ".pyd",
    ".exe",
    ".dll",
    ".so",
    ".bat",
    ".cmd",
    ".sh",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
}


def is_con_file(file_path: Union[str, Path]) -> bool:
    """
    Rapidly check whether a file is a valid Xbox 360 STFS CON/LIVE/PIRS package
    by testing its 4-byte header magic without parsing the full structure.
    """
    try:
        p = Path(file_path)
        if not p.is_file():
            return False

        ext = p.suffix.lower()
        if ext in EXCLUDED_EXTENSIONS:
            return False

        # Check minimal STFS header size (0x1000 = 4096 bytes)
        stat = p.stat()
        if stat.st_size < 0x1000:
            return False

        with open(p, "rb") as f:
            magic = f.read(4)
            return magic in STFS_MAGICS
    except Exception:
        return False


def scan_directory_for_con_files(
    directory: Union[str, Path], recursive: bool = True
) -> List[Path]:
    """
    Scan a directory for Xbox 360 Rock Band CON packages.
    
    Args:
        directory: Root directory to scan.
        recursive: Whether to traverse subdirectories recursively.

    Returns:
        Sorted list of Path objects for discovered CON files.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    con_files: List[Path] = []

    try:
        if recursive:
            for root, dirs, files in os.walk(dir_path):
                # Skip hidden / system directories
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
                for file_name in files:
                    file_path = Path(root) / file_name
                    if is_con_file(file_path):
                        con_files.append(file_path)
        else:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.is_file() and not entry.name.startswith("."):
                        file_path = Path(entry.path)
                        if is_con_file(file_path):
                            con_files.append(file_path)
    except Exception:
        pass

    return sorted(con_files)


def peek_con_metadata(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Fast in-memory metadata peek (<1ms).
    Parses the STFS directory table and songs.dta to extract title, artist, album,
    year, and charter without decoding MOGG audio or Milos images.

    Returns dictionary with keys:
        - title: str
        - artist: str
        - album: str
        - year: int
        - charter: str
        - song_id: str
        - size_bytes: int
    """
    p = Path(file_path)
    size_bytes = 0
    try:
        size_bytes = p.stat().st_size
    except Exception:
        pass

    fallback_result = {
        "title": p.stem or p.name,
        "artist": "Unknown Artist",
        "album": "",
        "year": 0,
        "charter": "Dansla116",
        "song_id": p.stem or "song",
        "size_bytes": size_bytes,
    }

    try:
        with STFSPackage.from_file(p, use_mmap=True) as package:
            files = package.list_files()
            dta_entry: Optional[str] = None
            for f in files:
                f_lower = f.lower()
                if f_lower.endswith(".dta") or "songs.dta" in f_lower:
                    dta_entry = f
                    break

            if not dta_entry:
                return fallback_result

            dta_bytes = package.get_file_bytes(dta_entry)
            dta_meta = parse_dta(dta_bytes)

            song_id = str(dta_meta.get("id") or dta_meta.get("song_id") or p.stem or "song")
            title = str(dta_meta.get("name") or song_id)
            artist = str(dta_meta.get("artist") or "Unknown Artist")
            album = str(dta_meta.get("album_name") or dta_meta.get("album") or "")
            year = int(dta_meta.get("year_released") or dta_meta.get("year") or 0)
            charter = str(dta_meta.get("charter") or "Dansla116")

            return {
                "title": title,
                "artist": artist,
                "album": album,
                "year": year,
                "charter": charter,
                "song_id": song_id,
                "size_bytes": size_bytes,
            }
    except Exception:
        return fallback_result
