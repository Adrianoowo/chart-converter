"""
Unit tests for library repair utility (repair.py).
"""

import io
from pathlib import Path
from PIL import Image
import numpy as np
import pytest

from fnf_fast_converter.src.repair import repair_chart_library, build_con_index


def test_repair_chart_library_ini_and_image(tmp_path):
    # 1. Setup mock song folder
    song_dir = tmp_path / "Artist - Song One"
    song_dir.mkdir(parents=True, exist_ok=True)

    # song.ini without icon
    ini_path = song_dir / "song.ini"
    ini_path.write_text("[song]\nname = Song One\nartist = Artist\n", encoding="utf-8")

    # notes.mid mock
    (song_dir / "notes.mid").write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0")

    # album.png with a white dot
    test_img = Image.new("RGB", (64, 64), color=(50, 100, 150))
    test_arr = np.array(test_img)
    test_arr[10, 10] = [255, 255, 255]
    Image.fromarray(test_arr).save(song_dir / "album.png", format="PNG")

    # Run repair
    stats = repair_chart_library(tmp_path, fix_art=True, fix_icons=True, icon_tag="fnf")

    assert stats["total_songs"] == 1
    assert stats["inis_updated"] == 1
    assert stats["images_repaired"] == 1
    assert stats["pixels_fixed"] == 1

    # Verify song.ini updated
    updated_ini = ini_path.read_text(encoding="utf-8")
    assert "icon = fnf" in updated_ini

    # Verify album.png repaired
    with Image.open(song_dir / "album.png") as reloaded:
        arr_after = np.array(reloaded)
        assert np.all(arr_after[10, 10] == [50, 100, 150])


def test_repair_detects_duplicates(tmp_path):
    # Two folders with same artist and name
    s1 = tmp_path / "Song_Folder_1"
    s1.mkdir()
    (s1 / "song.ini").write_text("[song]\nname = Same Song\nartist = Same Artist\n", encoding="utf-8")

    s2 = tmp_path / "Song_Folder_2"
    s2.mkdir()
    (s2 / "song.ini").write_text("[song]\nname = Same Song\nartist = Same Artist\n", encoding="utf-8")

    stats = repair_chart_library(tmp_path, fix_art=False, fix_icons=False)
    assert len(stats["duplicates"]) == 1
    assert stats["duplicates"][0]["title"] == "same song"
    assert stats["duplicates"][0]["artist"] == "same artist"
    assert len(stats["duplicates"][0]["folders"]) == 2
