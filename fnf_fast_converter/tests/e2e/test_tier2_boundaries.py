"""
Tier 2: Boundary, Corner Cases & Adversarial Verification E2E Tests
Covers edge conditions:
- Empty/missing metadata fields
- Non-standard audio channel counts (mono, 12-ch, missing instrument stems)
- Unicode & Windows-reserved character handling in titles & paths
- Corrupted STFS headers, invalid magic, truncated files
- Missing Milo album art
- Fast resume against corrupted existing charts
- Single-sample / edge audio lengths
"""

import io
import os
import struct
import tempfile
from pathlib import Path
import pytest
import soundfile as sf
from PIL import Image

from fnf_fast_converter.tests.e2e.harness_helpers import (
    build_synthetic_stfs,
    build_synthetic_mogg,
    build_synthetic_png_xbox,
    build_synthetic_midi,
    build_synthetic_dta,
    build_complete_test_con,
    calculate_expected_diff_tier,
    validate_song_ini_content,
    validate_converted_chart_folder,
    get_stfs_parser_class,
    get_dta_parser,
    get_song_ini_generator,
    get_image_decoder,
    get_mogg_demuxer_class,
    get_converter_pipeline,
)


class TestBoundaryAndCornerCases:
    def test_boundary_empty_dta_metadata_fields(self):
        """Verify DTA parser and song.ini generator handle empty/missing fields gracefully."""
        dta_text = "(emptysong)"
        parser = get_dta_parser()
        generator = get_song_ini_generator()
        
        parsed = parser(dta_text)
        ini_str = generator(parsed)
        
        props = validate_song_ini_content(ini_str)
        assert props.get("icon") == "fnf"
        assert props.get("diff_guitar") == "-1"
        assert props.get("diff_band") == "-1"

    def test_boundary_zero_length_file_in_stfs(self):
        """Verify STFS reader handles 0-byte files (like empty .pan overrides) without error."""
        files = {
            "songs/songs.dta": b"(test (name \"Test\"))",
            "songs/test/empty.pan": b"",
        }
        con_bytes = build_synthetic_stfs(files)
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        
        extracted = archive.extract_all_memory()
        assert "songs/test/empty.pan" in extracted
        assert len(extracted["songs/test/empty.pan"]) == 0

    def test_boundary_corrupted_stfs_magic(self):
        """Verify invalid STFS magic bytes raise an error."""
        files = {"songs/songs.dta": b"(dta)"}
        con_bytes = build_synthetic_stfs(files, magic=b"BAD!")
        ParserClass = get_stfs_parser_class()
        with pytest.raises(Exception):
            ParserClass.from_bytes(con_bytes)

    def test_boundary_truncated_stfs_header(self):
        """Verify severely truncated STFS byte stream fails cleanly."""
        truncated_bytes = b"CON \x00\x01\x02\x03"
        ParserClass = get_stfs_parser_class()
        with pytest.raises(Exception):
            ParserClass.from_bytes(truncated_bytes)

    def test_boundary_unicode_special_characters(self, tmp_path):
        """Verify UTF-8 characters (Japanese, Cyrillic, Accents, Emoji) in metadata and song.ini."""
        unicode_title = "アイドル (Idol) — [Special ★ Édition] 🎵"
        unicode_artist = "YOASOBI / 幾田りら"
        unicode_album = "THE BOOK 3 — Популярная музыка"

        dta_bytes = build_synthetic_dta(
            song_id="idol_yoasobi",
            name=unicode_title,
            artist=unicode_artist,
            album_name=unicode_album,
            charter="Charter ⚡",
            use_bom=True,
        )
        
        parser = get_dta_parser()
        parsed = parser(dta_bytes)
        generator = get_song_ini_generator()
        ini_str = generator(parsed)
        
        props = validate_song_ini_content(ini_str)
        assert props.get("name") == unicode_title
        assert props.get("artist") == unicode_artist
        assert props.get("album") == unicode_album
        assert props.get("charter") == "Charter ⚡"

    def test_boundary_windows_reserved_characters_sanitization(self, tmp_path):
        """Verify filenames with Windows reserved characters (: * ? \" < > | / \\) are sanitized."""
        tricky_title = 'Highway to Hell: Live? / "Rock" <Master> | Edition'
        tricky_artist = "AC/DC *Special*"
        
        con_bytes = build_complete_test_con(
            song_id="tricky_acdc",
            title=tricky_title,
            artist=tricky_artist,
        )
        
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir)
        assert ok
        
        created_dirs = list(out_dir.iterdir())
        assert len(created_dirs) == 1
        folder_name = created_dirs[0].name
        
        # Folder name must NOT contain reserved Windows characters
        for char in ':*?"<>|/\\':
            assert char not in folder_name
            
        # But song.ini should contain the exact un-sanitized title and artist
        ini_path = created_dirs[0] / "song.ini"
        props = validate_song_ini_content(ini_path.read_text(encoding="utf-8"))
        assert props.get("name") == tricky_title
        assert props.get("artist") == tricky_artist

    def test_boundary_unusual_channel_routing_mono(self):
        """Verify 1-channel mono audio stream demuxes into valid playable mono song.ogg."""
        mogg_data = build_synthetic_mogg(channels=1, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {})
        assert "song.ogg" in stems
        data, sr = sf.read(io.BytesIO(stems["song.ogg"]))
        assert sr == 44100

    def test_boundary_unusual_channel_routing_12_channels(self):
        """Verify 12-channel MOGG with extra crowd/backing channels demuxes cleanly."""
        mogg_data = build_synthetic_mogg(channels=12, duration_seconds=0.2)
        channel_mapping = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
            "vocals": [6, 7],
            "keys": [8, 9],
        }
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, channel_mapping)
        assert "drums.ogg" in stems
        assert "rhythm.ogg" in stems
        assert "guitar.ogg" in stems
        assert "vocals.ogg" in stems
        assert "keys.ogg" in stems
        assert "song.ogg" in stems  # Channels 10, 11 form song.ogg
        assert len(stems) == 6

    def test_boundary_missing_milo_album_art(self, tmp_path):
        """Verify converter succeeds when album art (.png_xbox) is missing from CON package."""
        con_bytes = build_complete_test_con(include_cover=False)
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir)
        assert ok
        
        chart_dir = list(out_dir.glob("* - *"))[0]
        # Should have song.ini, notes.mid, and stems even if album.png is absent
        validate_converted_chart_folder(chart_dir, require_album_png=False)

    def test_boundary_missing_drums_track(self, tmp_path):
        """Verify converting a song without drum channels routes remaining instruments cleanly."""
        dta_bytes = build_synthetic_dta(
            song_id="nodrums",
            name="Acoustic Song",
            artist="Solo Artist",
            tracks={"guitar": [0, 1], "vocals": [2, 3]},
            ranks={"guitar": 250, "vocals": 200},
        )
        mid_bytes = build_synthetic_midi(["PART GUITAR", "PART VOCALS", "BEAT"])
        mogg_bytes = build_synthetic_mogg(channels=4, duration_seconds=0.2)
        
        files = {
            "songs/songs.dta": dta_bytes,
            "songs/nodrums/nodrums.mid": mid_bytes,
            "songs/nodrums/nodrums.mogg": mogg_bytes,
        }
        con_bytes = build_synthetic_stfs(files)
        
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir)
        assert ok
        
        chart_dir = list(out_dir.glob("* - *"))[0]
        assert not (chart_dir / "drums.ogg").exists()
        assert (chart_dir / "guitar.ogg").exists()
        assert (chart_dir / "vocals.ogg").exists()

    def test_boundary_corrupted_mogg_header(self):
        """Verify invalid MOGG header raises exception rather than hanging."""
        corrupt_mogg = b"\x99\x99\x99\x99\x00\x00\x00\x00" + b"NotOggData"
        Demuxer = get_mogg_demuxer_class()
        with pytest.raises(Exception):
            Demuxer.demux_to_stems(corrupt_mogg, {})

    def test_boundary_single_sample_audio(self):
        """Verify demuxing extremely short audio duration (0.01s)."""
        mogg_data = build_synthetic_mogg(channels=2, duration_seconds=0.01)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {})
        assert "song.ogg" in stems
        data, sr = sf.read(io.BytesIO(stems["song.ogg"]))
        assert len(data) > 0

    def test_boundary_fast_resume_corrupt_existing_chart(self, tmp_path):
        """Verify incomplete/corrupted existing folder is re-converted when missing critical files."""
        out_dir = tmp_path / "out"
        fake_chart_dir = out_dir / "Weezer - Buddy Holly"
        fake_chart_dir.mkdir(parents=True, exist_ok=True)
        # Only write an empty song.ini missing icon=fnf and notes.mid
        (fake_chart_dir / "song.ini").write_text("[song]\nname=Broken\n", encoding="utf-8")
        
        con_bytes = build_complete_test_con()
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir, overwrite=True)
        assert ok
        
        # After conversion, notes.mid and icon=fnf must be present
        validate_converted_chart_folder(fake_chart_dir)
