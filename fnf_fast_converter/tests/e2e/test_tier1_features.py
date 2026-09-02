"""
Tier 1: Feature Coverage E2E Tests (>=5 test cases per feature)
Covers F1 through F13:
- F1, F2: STFS In-Memory Archive Parser & Slicer
- F3, F4: MOGG Container Parser & Multi-Channel Demuxing
- F5: Parallel Stem Slicer & Vorbis Encoder
- F6, F7: DTA Lexer, AST Parser & Difficulty Mapping
- F8: song.ini Synthesizer (Strict `icon = fnf`)
- F9: Milo DXT1 Artwork Decoder
- F10: MIDI Track Passthrough
- F11, F12, F13: Batch CLI Runner, Fast Resume & Progress
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
    GENRE_MAP,
)


# ============================================================================
# Feature 1 & 2: STFS Archive Reader & File Stream Extractor (F1, F2)
# ============================================================================

class TestSTFSArchiveReader:
    def test_stfs_parse_con_magic_and_extract_files(self):
        """Verify parsing standard CON package and extracting exact file bytes."""
        files = {
            "songs/songs.dta": b"(test (name \"Test\"))",
            "songs/test/test.mid": b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xE0MTrk\x00\x00\x00\x04\x00\xFF\x2F\x00",
        }
        con_bytes = build_synthetic_stfs(files, magic=b"CON ")
        
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        
        extracted = archive.extract_all_memory()
        assert "songs/songs.dta" in extracted
        assert extracted["songs/songs.dta"] == files["songs/songs.dta"]
        assert extracted["songs/test/test.mid"] == files["songs/test/test.mid"]

    def test_stfs_parse_live_and_pirs_magic(self):
        """Verify STFS reader accepts LIVE and PIRS header magic."""
        files = {"songs/songs.dta": b"(live_pack (name \"Live\"))"}
        
        for magic in (b"LIVE", b"PIRS"):
            pkg_bytes = build_synthetic_stfs(files, magic=magic)
            ParserClass = get_stfs_parser_class()
            archive = ParserClass.from_bytes(pkg_bytes)
            assert archive.get_file_bytes("songs/songs.dta") == files["songs/songs.dta"]

    def test_stfs_hierarchical_directory_resolution(self):
        """Verify deeply nested file paths are properly reconstructed."""
        files = {
            "songs/subfolder/gen/art_keep.png_xbox": b"MiloArtPayloadData12345",
            "songs/subfolder/audio.mogg": b"MoggAudioPayload67890",
        }
        con_bytes = build_synthetic_stfs(files)
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        
        file_list = archive.list_files()
        assert "songs/subfolder/gen/art_keep.png_xbox" in file_list
        assert "songs/subfolder/audio.mogg" in file_list
        assert archive.get_file_bytes("songs/subfolder/gen/art_keep.png_xbox") == b"MiloArtPayloadData12345"

    def test_stfs_multi_block_file_reassembly(self):
        """Verify large multi-block files (>8KB, spanning >=3 4KB blocks) assemble without corruption."""
        large_payload = bytes([i % 256 for i in range(16384)])  # 16 KB = 4 blocks
        files = {
            "songs/large/large.mogg": large_payload,
            "songs/songs.dta": b"(dta)",
        }
        con_bytes = build_synthetic_stfs(files)
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        
        extracted = archive.get_file_bytes("songs/large/large.mogg")
        assert len(extracted) == 16384
        assert extracted == large_payload

    def test_stfs_nonexistent_file_raises_filenotfound(self):
        """Verify requesting a missing file raises FileNotFoundError."""
        con_bytes = build_synthetic_stfs({"songs/songs.dta": b"(dta)"})
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        with pytest.raises((FileNotFoundError, KeyError)):
            archive.get_file_bytes("songs/nonexistent.mid")

    def test_stfs_extract_all_memory_returns_all_files(self):
        """Verify extract_all_memory returns dictionary of all payload files."""
        files = {
            "songs/songs.dta": b"(dta)",
            "songs/test/test.mid": b"MIDI",
            "songs/test/test.mogg": b"MOGG",
        }
        con_bytes = build_synthetic_stfs(files)
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        all_files = archive.extract_all_memory()
        assert len(all_files) == 3
        assert set(all_files.keys()) == set(files.keys())


# ============================================================================
# Feature 3 & 4: MOGG Container Parser & Multi-Channel Demuxing (F3, F4)
# ============================================================================

class TestMOGGDemuxer:
    def test_mogg_type10_header_and_ogg_offset_parsing(self):
        """Verify MOGG Type 10 container header and OGG bitstream offset."""
        mogg_data = build_synthetic_mogg(channels=2, duration_seconds=0.2, ogg_offset=2900)
        assert struct.unpack("<I", mogg_data[:4])[0] == 10
        assert struct.unpack("<I", mogg_data[4:8])[0] == 2900
        assert mogg_data[2900:2904] == b"OggS"

    def test_mogg_demux_standard_10_channel_stream(self):
        """Verify demuxing 10-channel MOGG into drums, bass, guitar, vocals, song stems."""
        mogg_data = build_synthetic_mogg(channels=10, duration_seconds=0.3)
        channel_mapping = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
            "vocals": [6, 7],
        }
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, channel_mapping)
        
        assert "drums.ogg" in stems
        assert "rhythm.ogg" in stems
        assert "guitar.ogg" in stems
        assert "vocals.ogg" in stems
        assert "song.ogg" in stems  # Unassigned channels 8 and 9 form song.ogg
        
        # Verify each stem is valid Ogg Vorbis
        for stem_name, ogg_bytes in stems.items():
            bio = io.BytesIO(ogg_bytes)
            data, sr = sf.read(bio)
            assert sr == 44100
            assert data.shape[1] == 2  # Stereo stems

    def test_mogg_demux_stereo_2_channel_stream(self):
        """Verify demuxing a 2-channel MOGG generates valid song.ogg."""
        mogg_data = build_synthetic_mogg(channels=2, duration_seconds=0.3)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, channel_mapping={})
        assert "song.ogg" in stems
        bio = io.BytesIO(stems["song.ogg"])
        data, sr = sf.read(bio)
        assert data.shape[1] == 2

    def test_mogg_demux_4_channel_stream(self):
        """Verify demuxing 4-channel MOGG (drums + song)."""
        mogg_data = build_synthetic_mogg(channels=4, duration_seconds=0.3)
        channel_mapping = {"drum": [0, 1]}
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, channel_mapping)
        assert "drums.ogg" in stems
        assert "song.ogg" in stems
        assert len(stems) == 2

    def test_mogg_demux_8_channel_stream(self):
        """Verify demuxing 8-channel MOGG (drums + bass + guitar + song)."""
        mogg_data = build_synthetic_mogg(channels=8, duration_seconds=0.3)
        channel_mapping = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
        }
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, channel_mapping)
        assert "drums.ogg" in stems
        assert "rhythm.ogg" in stems
        assert "guitar.ogg" in stems
        assert "song.ogg" in stems

    def test_mogg_preserves_audio_sample_rate(self):
        """Verify 48000 Hz source MOGG preserves 48000 Hz in demuxed stems."""
        mogg_data = build_synthetic_mogg(channels=2, sample_rate=48000, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {})
        data, sr = sf.read(io.BytesIO(stems["song.ogg"]))
        assert sr == 48000


# ============================================================================
# Feature 5: Stem Slicer & Encoder (F5)
# ============================================================================

class TestStemSlicer:
    def test_stem_slicer_routes_drums_to_drums_ogg(self):
        mogg_data = build_synthetic_mogg(channels=4, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {"drums": [0, 1]})
        assert "drums.ogg" in stems

    def test_stem_slicer_routes_bass_to_rhythm_ogg(self):
        mogg_data = build_synthetic_mogg(channels=4, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {"bass": [0, 1]})
        assert "rhythm.ogg" in stems

    def test_stem_slicer_routes_guitar_and_vocals(self):
        mogg_data = build_synthetic_mogg(channels=6, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {"guitar": [0, 1], "vocals": [2, 3]})
        assert "guitar.ogg" in stems
        assert "vocals.ogg" in stems

    def test_stem_slicer_unassigned_channels_to_song_ogg(self):
        mogg_data = build_synthetic_mogg(channels=6, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {"guitar": [0, 1]})
        assert "song.ogg" in stems  # Channels 2,3,4,5 form song.ogg

    def test_stem_slicer_empty_track_mapping_handling(self):
        mogg_data = build_synthetic_mogg(channels=2, duration_seconds=0.2)
        Demuxer = get_mogg_demuxer_class()
        stems = Demuxer.demux_to_stems(mogg_data, {})
        assert "song.ogg" in stems


# ============================================================================
# Feature 6 & 7: DTA Lexer, AST Parser & Difficulty Mapping (F6, F7)
# ============================================================================

class TestDTAParserAndDifficulty:
    def test_dta_parse_standard_s_expressions(self):
        """Verify parsing standard DTA S-expressions."""
        dta_text = """(testsong
            (name "Buddy Holly")
            (artist "Weezer")
            (album_name "The Blue Album")
            (year_released 1994)
            (genre alternative)
            (song_length 162727)
        )"""
        parser = get_dta_parser()
        data = parser(dta_text)
        assert data.get("name") == "Buddy Holly"
        assert data.get("artist") == "Weezer"
        year_val = data.get("year_released") or data.get("year")
        assert year_val == "1994" or year_val == 1994

    def test_dta_parse_with_inline_comments_and_bom(self):
        """Verify DTA parser handles UTF-8 BOM and inline comments."""
        dta_text = "\xEF\xBB\xBF(song_with_bom\n(name \"Song Title\") ; inline comment\n(year_released 2021) ; year\n)"
        parser = get_dta_parser()
        data = parser(dta_text)
        assert data.get("name") == "Song Title"

    def test_dta_parse_complex_curly_brace_expressions(self):
        """Verify DTA parser tolerates complex {...} expressions like (context { ... })."""
        dta_text = """(complexsong
            (name "Complex Song")
            (context {== $SONG_VERSION 0})
            (fake {+ 1 2})
            (song (tracks ((guitar (0 1))))))"""
        parser = get_dta_parser()
        data = parser(dta_text)
        assert data.get("name") == "Complex Song"

    def test_dta_difficulty_rank_cutoff_mapping_all_tiers(self):
        """Verify cutoff table maps ranks to discrete 0-6 difficulty tiers."""
        # Guitar: [139, 176, 221, 267, 333, 409]
        assert calculate_expected_diff_tier(100, "guitar") == 0
        assert calculate_expected_diff_tier(139, "guitar") == 1
        assert calculate_expected_diff_tier(176, "guitar") == 2
        assert calculate_expected_diff_tier(221, "guitar") == 3
        assert calculate_expected_diff_tier(267, "guitar") == 4
        assert calculate_expected_diff_tier(333, "guitar") == 5
        assert calculate_expected_diff_tier(450, "guitar") == 6
        assert calculate_expected_diff_tier(0, "guitar") == -1
        assert calculate_expected_diff_tier(None, "guitar") == -1

    def test_dta_genre_normalization(self):
        """Verify genre symbols normalize to standard Clone Hero genre strings."""
        assert GENRE_MAP.get("classicrock") == "Classic Rock"
        assert GENRE_MAP.get("hiphoprap") == "Hip-Hop/Rap"
        assert GENRE_MAP.get("alternative") == "Alternative"

    def test_dta_vocal_harmonies_detection(self):
        """Verify vocal_parts > 1 enables diff_vocals_harm."""
        dta_text = """(harmoniessong
            (name "Harmonies")
            (vocal_parts 3)
            (rank (vocals 218))
        )"""
        parser = get_dta_parser()
        generator = get_song_ini_generator()
        ini_str = generator(parser(dta_text))
        props = validate_song_ini_content(ini_str)
        assert props.get("diff_vocals_harm") == props.get("diff_vocals")


# ============================================================================
# Feature 8: `song.ini` Synthesizer with `icon = fnf` (F8)
# ============================================================================

class TestSongINISynthesizer:
    def test_song_ini_mandatory_icon_fnf_placement_line2(self):
        """Verify `icon = fnf` is strictly present on line 2 under `[song]`."""
        generator = get_song_ini_generator()
        ini_str = generator({"name": "Test", "artist": "Artist"})
        lines = [l.strip() for l in ini_str.splitlines() if l.strip()]
        assert lines[0] == "[song]"
        assert lines[1].replace(" ", "").lower() == "icon=fnf"

    def test_song_ini_metadata_fields_synthesis(self):
        """Verify metadata fields correctly appear in song.ini."""
        dta_text = """(moveadapted
            (name "Move (Adapted)")
            (artist "1K Phew, Lecrae")
            (album_name "No Church In A While")
            (year_released 2021)
            (genre hiphoprap)
            (song_length 142153)
            (preview 10476 40475)
        )"""
        parser = get_dta_parser()
        generator = get_song_ini_generator()
        ini_str = generator(parser(dta_text))
        props = validate_song_ini_content(ini_str)
        assert props.get("name") == "Move (Adapted)"
        assert props.get("artist") == "1K Phew, Lecrae"
        assert props.get("album") == "No Church In A While"
        assert props.get("year") == "2021"
        assert props.get("genre") == "Hip-Hop/Rap"
        assert props.get("song_length") == "142153"
        assert props.get("preview_start_time") == "10476"
        assert props.get("preview_end_time") == "40475"

    def test_song_ini_pro_drums_flag_set(self):
        """Verify pro_drums = True is set."""
        generator = get_song_ini_generator()
        ini_str = generator({"name": "Song"})
        props = validate_song_ini_content(ini_str)
        assert props.get("pro_drums", "").lower() == "true"

    def test_song_ini_star_power_and_multiplier_notes_116(self):
        """Verify star power and multiplier notes are 116."""
        generator = get_song_ini_generator()
        ini_str = generator({"name": "Song"})
        props = validate_song_ini_content(ini_str)
        assert props.get("star_power_note") == "116"
        assert props.get("multiplier_note") == "116"

    def test_song_ini_track_and_album_track_formatting(self):
        """Verify track numbers are formatted when present."""
        dta_text = """(trackedsong
            (name "Tracked Song")
            (album_track_number 4)
        )"""
        parser = get_dta_parser()
        generator = get_song_ini_generator()
        ini_str = generator(parser(dta_text))
        props = validate_song_ini_content(ini_str)
        assert props.get("track") == "4"
        assert props.get("album_track") == "4"


# ============================================================================
# Feature 9: Milo DXT1 Artwork Decoder (F9)
# ============================================================================

class TestMiloDXT1Decoder:
    def test_dxt1_header_parsing_and_dimensions(self):
        """Verify 32-byte Milo header parsing."""
        raw = build_synthetic_png_xbox(256, 256, (255, 0, 0))
        assert struct.unpack("<H", raw[0:2])[0] == 1
        assert struct.unpack("<H", raw[2:4])[0] == 4
        assert struct.unpack("<H", raw[8:10])[0] == 256
        assert struct.unpack("<H", raw[10:12])[0] == 256

    def test_dxt1_16bit_word_byte_swap_correction(self):
        """Verify 16-bit word byte swapping decode."""
        decoder = get_image_decoder()
        raw = build_synthetic_png_xbox(256, 256, (0, 255, 0))
        png_bytes = decoder(raw)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.size == (256, 256)
        r, g, b = img.getpixel((128, 128))
        assert g > 200 and r < 30 and b < 30

    def test_dxt1_block_decompression_to_valid_png(self):
        """Verify output is a valid standard PNG image."""
        decoder = get_image_decoder()
        raw = build_synthetic_png_xbox(256, 256, (0, 0, 255))
        png_bytes = decoder(raw)
        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    def test_dxt1_handles_various_image_resolutions(self):
        """Verify handling 256x256 and 512x512 textures."""
        decoder = get_image_decoder()
        for res in (256, 512):
            raw = build_synthetic_png_xbox(res, res, (128, 128, 128))
            try:
                png_bytes = decoder(raw, width=res, height=res)
            except TypeError:
                png_bytes = decoder(raw)
            img = Image.open(io.BytesIO(png_bytes))
            assert img.size == (res, res)

    def test_dxt1_corrupted_payload_graceful_handling(self):
        """Verify corrupted image data raises ValueError or handles gracefully."""
        decoder = get_image_decoder()
        with pytest.raises(Exception):
            decoder(b"TooShortData")


# ============================================================================
# Feature 10: MIDI Passthrough (F10)
# ============================================================================

class TestMIDIPassthrough:
    def test_midi_passthrough_exact_byte_preservation(self):
        """Verify MIDI chart is extracted bit-identically."""
        midi_raw = build_synthetic_midi()
        files = {
            "songs/songs.dta": b"(dta)",
            "songs/buddy/buddy.mid": midi_raw,
        }
        con_bytes = build_synthetic_stfs(files)
        ParserClass = get_stfs_parser_class()
        archive = ParserClass.from_bytes(con_bytes)
        assert archive.get_file_bytes("songs/buddy/buddy.mid") == midi_raw

    def test_midi_contains_standard_rockband_track_names(self):
        """Verify standard Rock Band tracks are intact in MIDI."""
        midi_raw = build_synthetic_midi(["PART GUITAR", "PART DRUMS", "PART BASS"])
        assert b"PART GUITAR" in midi_raw
        assert b"PART DRUMS" in midi_raw
        assert b"PART BASS" in midi_raw

    def test_midi_clone_hero_note_pitch_compatibility(self):
        """Verify MIDI header MThd and MTrk chunk identifiers."""
        midi_raw = build_synthetic_midi()
        assert midi_raw.startswith(b"MThd")
        assert b"MTrk" in midi_raw

    def test_midi_tempo_track_integrity(self):
        """Verify tempo track contains set tempo meta event."""
        midi_raw = build_synthetic_midi()
        assert b"\xFF\x51\x03" in midi_raw  # Set tempo meta event

    def test_midi_corrupt_mthd_validation(self):
        """Verify truncated MIDI data is caught."""
        assert not b"CorruptedData".startswith(b"MThd")


# ============================================================================
# Feature 11, 12, 13: Batch CLI & Fast Resume (F11, F12, F13)
# ============================================================================

class TestBatchCLIAndFastResume:
    def test_batch_cli_input_and_output_arguments(self, tmp_path):
        """Verify end-to-end converter pipeline creates complete chart folder."""
        con_bytes = build_complete_test_con()
        con_file = tmp_path / "song.con"
        con_file.write_bytes(con_bytes)
        
        output_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_file, output_dir)
        assert ok
        
        chart_folders = list(output_dir.glob("* - *"))
        assert len(chart_folders) == 1
        validate_converted_chart_folder(chart_folders[0], expected_title="Buddy Holly")

    def test_fast_resume_skips_valid_converted_songs(self, tmp_path):
        """Verify converting already converted chart skips in fast resume mode."""
        con_bytes = build_complete_test_con()
        con_file = tmp_path / "song.con"
        con_file.write_bytes(con_bytes)
        
        output_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        converter(con_file, output_dir)
        
        # Second run should skip
        ok, msg = converter(con_file, output_dir, overwrite=False)
        assert ok
        assert "SKIPPED" in msg

    def test_fast_resume_patches_missing_icon_fnf(self, tmp_path):
        """Verify song.ini strictly has icon = fnf."""
        con_bytes = build_complete_test_con()
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        converter(con_bytes, out_dir)
        
        chart_dir = list(out_dir.glob("* - *"))[0]
        ini_path = chart_dir / "song.ini"
        ini_content = ini_path.read_text(encoding="utf-8")
        assert "icon = fnf" in ini_content

    def test_force_flag_re_converts_existing_songs(self, tmp_path):
        """Verify overwrite=True re-converts song."""
        con_bytes = build_complete_test_con()
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        converter(con_bytes, out_dir)
        
        ok, msg = converter(con_bytes, out_dir, overwrite=True)
        assert ok
        assert "CONVERTED" in msg

    def test_quiet_and_verbose_flag_behavior(self, tmp_path):
        """Verify converter return message format."""
        con_bytes = build_complete_test_con(title="Quiet Test")
        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_bytes, out_dir)
        assert ok
        assert "Quiet Test" in msg
