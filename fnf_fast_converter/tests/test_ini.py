"""
Unit tests for Clone Hero song.ini synthesizer.
"""

import pytest
from pathlib import Path
from fnf_fast_converter.src.dta import parse_dta
from fnf_fast_converter.src.ini import generate_song_ini

SAMPLE_BUDDY_HOLLY_DTA = """
(buddyhollyfnf
   (name "Buddy Holly")
   (artist "Weezer")
   (song
      (name songs/buddyhollyfnf/buddyhollyfnf)
      (tracks
         (
            (drum (0 1))
            (bass (2 3))
            (guitar (4 5))
            (vocals (6 7))
         )
      )
      (pans (-1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0))
      (vols (0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0))
      (vocal_parts 1)
   )
   (preview 83313 111920)
   (rank
      (band 215)
      (guitar 139)
      (drum 124)
      (bass 135)
      (vocals 218)
   )
   (song_length 164390)
   (year_released 1994)
   (album_name "Weezer (The Blue Album)")
   (author "Harmonix, Rhythm Authors")
   (album_track_number 4)
   (genre alternative)
)
"""

SAMPLE_MOVE_DTA = """
(moveadapted
   (name "Move (Adapted)")
   (artist "1K Phew, Lecrae")
   (album_name "No Church In A While")
   (song
      (name songs/moveadapted/moveadapted)
      (tracks
         (
            (drum (0 1))
            (bass (2 3))
            (guitar (4 5))
            (vocals (6 7))
         )
      )
      (vocal_parts 1)
   )
   (song_length 142153)
   (preview 10476 40475)
   (rank
      (band 1)
      (guitar 1)
      (drum 175)
      (bass 1)
      (vocals 1)
   )
   (genre hiphoprap)
   (year_released 2021)
   (author "Harmonix, Rhythm Authors")
)
"""


class TestSongIniSynthesizer:
    def test_strict_icon_placement(self):
        ini_str = generate_song_ini(SAMPLE_BUDDY_HOLLY_DTA)
        lines = [line.strip() for line in ini_str.strip().split("\n")]
        assert lines[0] == "[song]", "Line 1 must be [song]"
        assert lines[1] == "icon = fnf", "Line 2 must strictly be icon = fnf"

    def test_buddy_holly_exact_match(self):
        meta = parse_dta(SAMPLE_BUDDY_HOLLY_DTA)
        ini_str = generate_song_ini(meta)

        lines_dict = {}
        for line in ini_str.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                lines_dict[k.strip()] = v.strip()

        assert lines_dict["icon"] == "fnf"
        assert lines_dict["name"] == "Buddy Holly"
        assert lines_dict["artist"] == "Weezer"
        assert lines_dict["album"] == "Weezer (The Blue Album)"
        assert lines_dict["charter"] == "Harmonix, Rhythm Authors"
        assert lines_dict["frets"] == "Harmonix, Rhythm Authors"
        assert lines_dict["year"] == "1994"
        assert lines_dict["genre"] == "Alternative"
        assert lines_dict["pro_drums"] == "True"
        assert lines_dict["song_length"] == "164390"
        assert lines_dict["preview_start_time"] == "83313"
        assert lines_dict["preview_end_time"] == "111920"
        assert lines_dict["diff_band"] == "2"
        assert lines_dict["diff_guitar"] == "1"
        assert lines_dict["diff_guitarghl"] == "-1"
        assert lines_dict["diff_bass"] == "1"
        assert lines_dict["diff_bassghl"] == "-1"
        assert lines_dict["diff_drums"] == "1"
        assert lines_dict["diff_drums_real"] == "1"
        assert lines_dict["diff_keys"] == "-1"
        assert lines_dict["diff_keys_real"] == "-1"
        assert lines_dict["diff_vocals"] == "3"
        assert lines_dict["diff_vocals_harm"] == "-1"
        assert lines_dict["diff_dance"] == "-1"
        assert lines_dict["track"] == "4"
        assert lines_dict["album_track"] == "4"
        assert lines_dict["star_power_note"] == "116"
        assert lines_dict["multiplier_note"] == "116"
        assert lines_dict["sysex_slider"] == "False"
        assert lines_dict["sysex_open_bass"] == "False"

    def test_move_ini_no_track_number(self):
        ini_str = generate_song_ini(SAMPLE_MOVE_DTA)
        lines_dict = {}
        for line in ini_str.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                lines_dict[k.strip()] = v.strip()

        assert lines_dict["name"] == "Move (Adapted)"
        assert lines_dict["artist"] == "1K Phew, Lecrae"
        assert lines_dict["album"] == "No Church In A While"
        assert lines_dict["genre"] == "Hip-Hop/Rap"
        assert lines_dict["diff_drums"] == "2"
        assert lines_dict["diff_drums_real"] == "2"
        assert lines_dict["diff_guitar"] == "0"
        assert lines_dict["diff_band"] == "0"
        # Since track number was not present, track keys must be absent
        assert "track" not in lines_dict
        assert "album_track" not in lines_dict

    def test_charter_override_fallback(self):
        dta = '(minimal (name "Song"))'
        ini_str = generate_song_ini(dta, charter="CustomCharter")
        assert "charter = CustomCharter" in ini_str
        assert "frets = CustomCharter" in ini_str

    def test_compare_with_reference_files_if_available(self):
        ref_path = Path(r"P:\Charts\Fortnite Festival\Weezer - Buddy Holly\song.ini")
        if not ref_path.exists():
            pytest.skip("Reference song.ini not available on current environment")

        ref_lines = [l.strip() for l in ref_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
        gen_lines = [l.strip() for l in generate_song_ini(SAMPLE_BUDDY_HOLLY_DTA).strip().split("\n") if l.strip()]

        # Both should have icon = fnf at index 1
        assert ref_lines[1] == "icon = fnf"
        assert gen_lines[1] == "icon = fnf"

        # Check line counts match
        assert len(ref_lines) == len(gen_lines)
        for ref_l, gen_l in zip(ref_lines, gen_lines):
            assert ref_l == gen_l
