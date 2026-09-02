"""
Tier 3: Cross-Feature Combination E2E Tests
Exercises pairwise matrices across:
- Audio configurations (2ch, 4ch, 6ch, 8ch, 10ch)
- Metadata complexity (minimal, rich unicode, multi-vocal, full difficulty ranks)
- Runtime execution modes (single worker, multi-threaded batch, overwrite vs resume)
"""

import io
import os
import tempfile
from pathlib import Path
import pytest
import soundfile as sf

from fnf_fast_converter.tests.e2e.harness_helpers import (
    build_complete_test_con,
    build_synthetic_stfs,
    build_synthetic_mogg,
    build_synthetic_dta,
    build_synthetic_midi,
    build_synthetic_png_xbox,
    validate_converted_chart_folder,
    validate_song_ini_content,
    get_converter_pipeline,
)

AUDIO_CONFIGS = [
    ("2ch_master", 2, {}, ["song.ogg"]),
    ("4ch_drums_song", 4, {"drum": [0, 1]}, ["drums.ogg", "song.ogg"]),
    ("6ch_drums_guitar_song", 6, {"drum": [0, 1], "guitar": [2, 3]}, ["drums.ogg", "guitar.ogg", "song.ogg"]),
    ("8ch_drums_bass_guitar_song", 8, {"drum": [0, 1], "bass": [2, 3], "guitar": [4, 5]}, ["drums.ogg", "rhythm.ogg", "guitar.ogg", "song.ogg"]),
    ("10ch_full_band", 10, {"drum": [0, 1], "bass": [2, 3], "guitar": [4, 5], "vocals": [6, 7]}, ["drums.ogg", "rhythm.ogg", "guitar.ogg", "vocals.ogg", "song.ogg"]),
]

METADATA_CONFIGS = [
    ("minimal", {"name": "Simple", "artist": "Band"}, {}),
    ("full_diffs", {"name": "Full Diffs", "artist": "Artist"}, {"guitar": 350, "bass": 280, "drum": 400, "vocals": 250, "band": 320}),
    ("unicode_rich", {"name": "Café ☕ & Música", "artist": "Björk / Sigur Rós"}, {"guitar": 200, "bass": 150}),
    ("multivocal", {"name": "Harmony Trio", "artist": "Choir", "vocal_parts": 3}, {"vocals": 300, "band": 250}),
]


class TestCrossFeatureCombinations:
    @pytest.mark.parametrize("audio_id,ch_count,track_map,expected_stems", AUDIO_CONFIGS)
    @pytest.mark.parametrize("meta_id,meta_props,ranks", METADATA_CONFIGS)
    def test_pairwise_audio_and_metadata_combination(
        self, tmp_path, audio_id, ch_count, track_map, expected_stems, meta_id, meta_props, ranks
    ):
        """Verify full conversion across all audio configurations x metadata variations."""
        song_id = f"s_{audio_id[:4]}_{meta_id[:4]}"
        title = meta_props.get("name", "Test Title")
        artist = meta_props.get("artist", "Test Artist")
        vocal_parts = meta_props.get("vocal_parts", 1)

        dta_bytes = build_synthetic_dta(
            song_id=song_id,
            name=title,
            artist=artist,
            vocal_parts=vocal_parts,
            ranks=ranks,
            tracks=track_map if track_map else {"song": [0, 1]},
        )
        mid_bytes = build_synthetic_midi()
        mogg_bytes = build_synthetic_mogg(channels=ch_count, duration_seconds=0.25)
        img_bytes = build_synthetic_png_xbox(256, 256, (50, 100, 150))

        files = {
            "songs/songs.dta": dta_bytes,
            f"songs/{song_id}/{song_id}.mid": mid_bytes,
            f"songs/{song_id}/{song_id}.mogg": mogg_bytes,
            f"songs/{song_id}/gen/{song_id}_keep.png_xbox": img_bytes,
        }
        con_bytes = build_synthetic_stfs(files)

        out_dir = tmp_path / f"out_{audio_id}_{meta_id}"
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir)
        assert ok, f"Conversion failed: {msg}"

        chart_folders = list(out_dir.glob("* - *"))
        assert len(chart_folders) == 1, f"Expected 1 folder in {out_dir}"
        chart_dir = chart_folders[0]

        result = validate_converted_chart_folder(chart_dir, expected_title=title, expected_artist=artist)
        
        # Verify all expected stem files exist and are valid
        for stem in expected_stems:
            assert (chart_dir / stem).is_file(), f"Missing expected stem {stem} in {chart_dir}"
            assert stem in result["stems"]

        # Verify song.ini properties
        props = result["props"]
        assert props.get("icon") == "fnf"
        if vocal_parts > 1:
            assert props.get("diff_vocals_harm") != "-1"

    def test_batch_concurrency_and_resume_combination(self, tmp_path):
        """Verify multiple CON conversions with simulated concurrent batch and fast resume."""
        con_list = []
        for i in range(5):
            con = build_complete_test_con(
                song_id=f"batch_song_{i}",
                title=f"Batch Song {i}",
                artist=f"Batch Artist {i}",
                audio_channels=6,
            )
            con_list.append((f"batch_song_{i}.con", con))

        out_dir = tmp_path / "batch_out"
        converter = get_converter_pipeline()

        # First pass: Convert all 5 songs
        for filename, con_data in con_list:
            ok, msg = converter(con_data, out_dir, overwrite=False)
            assert ok
            assert "CONVERTED" in msg

        # Second pass: Fast resume should skip all 5
        for filename, con_data in con_list:
            ok, msg = converter(con_data, out_dir, overwrite=False)
            assert ok
            assert "SKIPPED" in msg

        # Third pass: Force overwrite should re-convert all 5
        for filename, con_data in con_list:
            ok, msg = converter(con_data, out_dir, overwrite=True)
            assert ok
            assert "CONVERTED" in msg

        # Verify total output folders
        chart_dirs = list(out_dir.glob("* - *"))
        assert len(chart_dirs) == 5
        for cdir in chart_dirs:
            validate_converted_chart_folder(cdir)
