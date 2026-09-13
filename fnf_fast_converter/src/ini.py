"""
ini.py - Clone Hero song.ini Synthesizer.

Generates strictly compliant Clone Hero song.ini files with mandatory 'icon = fnf'
placed immediately beneath the '[song]' section header.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, Union, Optional
from .dta import parse_dta, extract_dta_metadata


def generate_song_ini(
    dta_data: Union[Dict[str, Any], str, bytes],
    charter: str = "Harmonix, Rhythm Authors",
) -> str:
    """
    Synthesizes a Clone Hero compliant song.ini string from DTA metadata.
    
    CRITICAL: 'icon = fnf' is strictly placed directly below '[song]'.
    """
    if isinstance(dta_data, (str, bytes, bytearray, memoryview)):
        meta = parse_dta(dta_data)
    elif isinstance(dta_data, dict):
        meta = dta_data
    else:
        raise TypeError(f"Invalid dta_data type: {type(dta_data)}")

    # Extract fields with safe defaults
    name = meta.get("name", "")
    artist = meta.get("artist", "")
    album = meta.get("album", "")
    
    # Charter / frets
    charter_val = meta.get("charter") or charter or "Harmonix, Rhythm Authors"
    frets_val = charter_val

    year = meta.get("year", 0)
    genre = meta.get("genre", "Rock")
    song_length = meta.get("song_length", 0)
    preview_start = meta.get("preview_start_time", meta.get("preview", (0, 0))[0] if isinstance(meta.get("preview"), (list, tuple)) else 0)
    preview_end = meta.get("preview_end_time", meta.get("preview", (0, 0))[1] if isinstance(meta.get("preview"), (list, tuple)) and len(meta.get("preview", [])) > 1 else 0)

    # Difficulty tiers
    diff_tiers = meta.get("diff_tiers", {})

    diff_band = diff_tiers.get("band", -1)
    diff_guitar = diff_tiers.get("guitar", -1)
    diff_bass = diff_tiers.get("bass", -1)
    diff_drums = diff_tiers.get("drums", -1)
    diff_drums_real = diff_tiers.get("drums_real", diff_drums)
    diff_keys = diff_tiers.get("keys", -1)
    diff_keys_real = diff_tiers.get("keys_real", -1)
    diff_vocals = diff_tiers.get("vocals", -1)
    diff_vocals_harm = diff_tiers.get("vocals_harm", -1)
    diff_guitar_real = diff_tiers.get("guitar_real", -1)
    diff_bass_real = diff_tiers.get("bass_real", -1)

    # Build INI lines
    lines = [
        "[song]",
        "icon = fnf",
        f"name = {name}",
        f"artist = {artist}",
        f"album = {album}",
        f"charter = {charter_val}",
        f"frets = {frets_val}",
        f"year = {year}",
        f"genre = {genre}",
        "pro_drums = True",
        f"song_length = {song_length}",
        f"preview_start_time = {preview_start}",
        f"preview_end_time = {preview_end}",
        f"diff_band = {diff_band}",
        f"diff_guitar = {diff_guitar}",
        "diff_guitarghl = -1",
        f"diff_bass = {diff_bass}",
        "diff_bassghl = -1",
        f"diff_drums = {diff_drums}",
        f"diff_drums_real = {diff_drums_real}",
        f"diff_keys = {diff_keys}",
        f"diff_keys_real = {diff_keys_real}",
        f"diff_vocals = {diff_vocals}",
        f"diff_vocals_harm = {diff_vocals_harm}",
        "diff_dance = -1",
        f"diff_bass_real = {diff_bass_real}",
        f"diff_guitar_real = {diff_guitar_real}",
        "diff_guitar_coop = -1",
        "diff_rhythm = -1",
        "diff_drums_real_ps = -1",
        "diff_keys_real_ps = -1",
        "diff_guitar_pad = -1",
        "diff_bass_pad = -1",
        "diff_drums_pad = -1",
        "diff_vocals_pad = -1",
        "diff_keys_pad = -1",
        "star_power_note = 116",
        "multiplier_note = 116",
    ]

    # Optional track number
    track_num = meta.get("album_track_number", meta.get("track"))
    if track_num is not None:
        lines.append(f"track = {track_num}")
        lines.append(f"album_track = {track_num}")

    lines.append("sysex_slider = False")
    lines.append("sysex_open_bass = False")
    lines.append("")  # Trailing newline

    return "\n".join(lines)


def ensure_ini_icon(ini_target: str | Path, icon: str = "fnf") -> tuple[bool, str]:
    """
    Safely ensures an INI file or string has `icon = {icon}` directly under `[song]`.
    
    If `ini_target` is a file path, updates the file on disk if changes are needed.
    Returns (updated_bool, new_or_current_content).
    """
    is_path = False
    path: Optional[Path] = None
    if isinstance(ini_target, Path):
        is_path = True
        path = ini_target
    elif isinstance(ini_target, str) and (len(ini_target) < 1024 and ("\n" not in ini_target) and Path(ini_target).is_file()):
        is_path = True
        path = Path(ini_target)

    if is_path and path is not None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return False, str(exc)
    else:
        content = str(ini_target)

    nl = "\r\n" if "\r\n" in content else "\n"

    # 1. Check if an icon line already exists
    icon_pattern = re.compile(r"^[ \t]*icon[ \t]*=.*$", re.MULTILINE | re.IGNORECASE)
    match = icon_pattern.search(content)

    if match:
        existing_line = match.group(0)
        curr_val = existing_line.split("=", 1)[1].strip().lower()
        if curr_val == icon.lower():
            return False, content
        new_content = icon_pattern.sub(f"icon = {icon}", content, count=1)
    else:
        # 2. Insert icon directly below [song]
        song_pattern = re.compile(r"^[ \t]*\[song\][ \t]*$", re.MULTILINE | re.IGNORECASE)
        match_song = song_pattern.search(content)
        if match_song:
            end = match_song.end()
            new_content = content[:end] + nl + f"icon = {icon}" + content[end:]
        else:
            new_content = f"[song]{nl}icon = {icon}{nl}" + content

    if is_path and path is not None:
        path.write_text(new_content, encoding="utf-8")

    return True, new_content

