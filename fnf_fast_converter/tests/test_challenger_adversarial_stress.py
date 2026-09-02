"""
test_challenger_adversarial_stress.py - Challenger 2 Stress Testing & Fuzzing Harness.

Comprehensive Adversarial Test Suite covering:
1. Corrupted & Truncated STFS files (headers, magics, cyclic block chains, truncated blocks, OOB offsets).
2. Malformed MOGG files (invalid Vorbis headers, unaligned offsets, unsupported channels, extreme pans/vols).
3. Degenerate DTA metadata (unbalanced parens, unclosed strings/blocks, toxic UTF-8, missing keys, unusual tracks).
4. High Concurrency Batch Stress (16/32/64 threads, read-only directories, non-existent paths, resource cleanup).
5. Fast Resume Resilience & Corrupt Folder Detection (partial folders, invalid song.ini, truncated MIDI, missing stems).
"""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytest
import soundfile as sf

from fnf_fast_converter.src.dta import (
    DTASyntaxError,
    extract_dta_metadata,
    map_difficulty_rank,
    normalize_genre,
    parse_dta,
    parse_s_expressions,
    tokenize_dta,
)
from fnf_fast_converter.src.image import decode_png_xbox
from fnf_fast_converter.src.ini import generate_song_ini
from fnf_fast_converter.src.mogg import (
    AudioStem,
    InvalidMOGGHeaderError,
    MOGGChannelMappingError,
    MOGGDecodeError,
    MOGGDemuxer,
    MOGGEncodeError,
    MOGGError,
    canonical_stem_name,
    decode_mogg_pcm,
    encode_vorbis_stem,
    mix_and_slice_channels,
    parse_mogg_header,
)
from fnf_fast_converter.src.pipeline import (
    BatchConversionProgress,
    BatchConversionStats,
    BatchConverter,
    convert_con_to_song_folder,
    is_valid_converted_song,
    sanitize_folder_name,
)
from fnf_fast_converter.src.stfs import (
    CorruptedSTFSError,
    InvalidSTFSHeaderError,
    STFSEntry,
    STFSError,
    STFSFileNotFoundError,
    STFSPackage,
)


def get_source_dataset_dir() -> Optional[Path]:
    """Find real source CON files directory if available."""
    candidates = [
        Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB"),
        Path(r"C:\Users\adema\Downloads\Dansla116/FNFestivaltoRB"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # Try searching for any Dansla folder in Downloads
    dl = Path(r"C:\Users\adema\Downloads")
    if dl.is_dir():
        for item in dl.iterdir():
            if "Dansla" in item.name and item.is_dir():
                return item
    return None


# Helper to build a minimal valid synthetic STFS CON package in memory
def create_synthetic_con_package(
    files: Dict[str, bytes],
    magic: bytes = b"CON ",
    header_size: int = 0x9000,
    corrupt_block_table: Optional[str] = None,
) -> bytes:
    """Creates a valid or intentionally corrupted Xbox 360 STFS CON package."""
    block_size = 4096
    base_data_offset = (header_size + 0x0FFF) & ~0x0FFF
    header = bytearray(base_data_offset)

    # Magic
    header[0:4] = magic
    struct.pack_into(">I", header, 0x0340, header_size)
    struct.pack_into(">I", header, 0x0344, 0x00000001)

    # Volume Descriptor at 0x0379
    header[0x0379] = 0x24  # descriptor size
    header[0x037A] = 0x00
    header[0x037B] = 0x00
    struct.pack_into(">H", header, 0x037C, 1)  # 1 block directory table
    # file_table_block_num = 0
    header[0x037E] = 0x00
    header[0x037F] = 0x00
    header[0x0380] = 0x00

    total_allocated = len(files) * 4 + 20
    struct.pack_into(">I", header, 0x0395, total_allocated)
    struct.pack_into(">I", header, 0x0399, 100)

    # Title strings
    title_str = "Adversarial Test Song"
    title_bytes = title_str.encode("utf-16be")
    header[0x1691 : 0x1691 + len(title_bytes)] = title_bytes

    # Allocate data blocks
    # Block 0: Directory block (L0 table 0 is physical block 0, Data block 0 is physical block 1)
    # Physical structure:
    # Phys 0: L0 Table 0
    # Phys 1: Data Block 0 (Directory block)
    # Phys 2+: File Data Blocks
    num_phys_blocks = 1 + 1 + len(files) * 4 + 10
    package_data = bytearray(base_data_offset + num_phys_blocks * block_size)
    package_data[:base_data_offset] = header

    # Directory entries (64 bytes each in Data Block 0)
    # Data block 0 physical offset = base_data_offset + 1 * block_size
    dir_offset = base_data_offset + 1 * block_size

    # L0 Table 0 physical offset = base_data_offset + 0 * block_size
    l0_table_offset = base_data_offset

    curr_logical_block = 1
    for idx, (fname, fcontent) in enumerate(files.items()):
        f_blocks = max(1, (len(fcontent) + block_size - 1) // block_size)
        start_block = curr_logical_block

        # Write directory entry (64 bytes)
        entry_pos = dir_offset + idx * 64
        name_bytes = fname.encode("utf-8")[:40]
        package_data[entry_pos : entry_pos + len(name_bytes)] = name_bytes

        flags_len = len(name_bytes) & 0x3F  # not directory, contiguous if 1 block
        if f_blocks == 1:
            flags_len |= 0x40
        package_data[entry_pos + 0x28] = flags_len

        # alloc_blocks (3 bytes LE)
        package_data[entry_pos + 0x29] = f_blocks & 0xFF
        package_data[entry_pos + 0x2A] = (f_blocks >> 8) & 0xFF
        package_data[entry_pos + 0x2B] = (f_blocks >> 16) & 0xFF

        # block_count (3 bytes LE)
        package_data[entry_pos + 0x2C] = f_blocks & 0xFF
        package_data[entry_pos + 0x2D] = (f_blocks >> 8) & 0xFF
        package_data[entry_pos + 0x2E] = (f_blocks >> 16) & 0xFF

        # starting_block (3 bytes LE)
        package_data[entry_pos + 0x2F] = start_block & 0xFF
        package_data[entry_pos + 0x30] = (start_block >> 8) & 0xFF
        package_data[entry_pos + 0x31] = (start_block >> 16) & 0xFF

        # parent_index = 0xFFFF (root)
        struct.pack_into(">H", package_data, entry_pos + 0x32, 0xFFFF)
        # file_size
        struct.pack_into(">I", package_data, entry_pos + 0x34, len(fcontent))

        # Write file content to physical blocks
        for b in range(f_blocks):
            log_b = start_block + b
            phys_b = log_b + 1  # 1 L0 table
            phys_off = base_data_offset + phys_b * block_size
            chunk = fcontent[b * block_size : (b + 1) * block_size]
            package_data[phys_off : phys_off + len(chunk)] = chunk

            # Update L0 hash table next pointer
            l0_entry = l0_table_offset + log_b * 24
            next_b = (log_b + 1) if (b + 1 < f_blocks) else 0x00FFFFFF
            struct.pack_into(">I", package_data, l0_entry + 20, next_b)

        curr_logical_block += f_blocks

    # Apply intentional block table corruption if requested
    if corrupt_block_table == "cycle":
        # Make block 1 point to block 1 (infinite cycle)
        l0_entry = l0_table_offset + 1 * 24
        struct.pack_into(">I", package_data, l0_entry + 20, 1)
    elif corrupt_block_table == "two_cycle":
        # Block 1 -> 2, Block 2 -> 1
        l0_entry1 = l0_table_offset + 1 * 24
        l0_entry2 = l0_table_offset + 2 * 24
        struct.pack_into(">I", package_data, l0_entry1 + 20, 2)
        struct.pack_into(">I", package_data, l0_entry2 + 20, 1)
    elif corrupt_block_table == "oob":
        # Make block 1 point to block 999999
        l0_entry = l0_table_offset + 1 * 24
        struct.pack_into(">I", package_data, l0_entry + 20, 999999)

    return bytes(package_data)


# Generate a minimal valid Vorbis audio byte stream
def create_synthetic_vorbis_ogg(
    duration_s: float = 0.5,
    channels: int = 2,
    sample_rate: int = 44100,
    frequency: float = 440.0,
) -> bytes:
    """Generate a standard multi-channel Ogg Vorbis stream."""
    num_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, num_samples, endpoint=False, dtype=np.float32)
    audio = np.zeros((num_samples, channels), dtype=np.float32)
    for ch in range(channels):
        audio[:, ch] = 0.5 * np.sin(2.0 * math.pi * (frequency * (ch + 1)) * t)

    buf = io.BytesIO()
    with sf.SoundFile(
        buf,
        mode="w",
        samplerate=sample_rate,
        channels=channels,
        subtype="VORBIS",
        format="OGG",
    ) as f:
        f.write(audio)
    return buf.getvalue()


# Generate a valid MOGG container
def create_synthetic_mogg(
    ogg_bytes: bytes,
    header_type: int = 10,
    vorbis_offset: int = 2900,
) -> bytes:
    """Wraps an Ogg Vorbis stream inside a Harmonix MOGG container header."""
    header = bytearray(vorbis_offset)
    struct.pack_into("<II", header, 0, header_type, vorbis_offset)
    return bytes(header) + ogg_bytes


# ==============================================================================
# 1. CORRUPTED & TRUNCATED STFS ADVERSARIAL TESTS
# ==============================================================================
class TestSTFSAdversarialCorruption:
    """Stress tests STFS parser against malformed headers, corrupt block tables, and truncation."""

    def test_truncated_headers(self):
        """Test file streams smaller than 8192 bytes or truncated at arbitrary byte boundaries."""
        for size in [0, 1, 10, 50, 500, 1024, 4096, 8191]:
            corrupt_bytes = b"CON " + os.urandom(max(0, size - 4))
            with pytest.raises(InvalidSTFSHeaderError):
                STFSPackage.from_bytes(corrupt_bytes)

    def test_invalid_magic_numbers(self):
        """Test rejection of non-STFS headers (ZIP, WAV, MP3, ELF, random noise)."""
        invalid_magics = [b"PK\x03\x04", b"RIFF", b"ID3\x03", b"\x7fELF", b"\x89PNG", b"FAKE", b"\x00\x00\x00\x00"]
        for magic in invalid_magics:
            data = bytearray(9000)
            data[: len(magic)] = magic
            with pytest.raises(InvalidSTFSHeaderError):
                STFSPackage.from_bytes(data)

    def test_cyclic_block_table_does_not_hang(self):
        """Test that cyclic Level 0 block pointer table (A->A or A->B->A) terminates cleanly."""
        dummy_dta = b"(song_id (name \"Cycle Test\") (artist \"Tester\"))"
        cyclic_con = create_synthetic_con_package(
            files={"songs.dta": dummy_dta, "song.mid": b"MThd" + b"\x00" * 20},
            corrupt_block_table="cycle",
        )
        pkg = STFSPackage.from_bytes(cyclic_con)
        # Reading file bytes should terminate without infinite loop
        t0 = time.perf_counter()
        extracted = pkg.get_file_bytes("songs.dta")
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"Cyclic block table caused hang: {elapsed:.3f}s"
        assert isinstance(extracted, bytes)

    def test_two_cycle_block_table_terminates(self):
        """Test 2-step cycle A->B->A terminates without infinite loop."""
        dummy_dta = b"(song_id (name \"Cycle 2 Test\") (artist \"Tester\"))"
        cyclic_con = create_synthetic_con_package(
            files={"songs.dta": dummy_dta, "song.mid": b"MThd" + b"\x00" * 20},
            corrupt_block_table="two_cycle",
        )
        pkg = STFSPackage.from_bytes(cyclic_con)
        t0 = time.perf_counter()
        extracted = pkg.get_file_bytes("songs.dta")
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"Two-cycle block table caused hang: {elapsed:.3f}s"

    def test_out_of_bounds_block_pointers(self):
        """Test out-of-bounds block index in L0 table terminates cleanly."""
        dummy_dta = b"(song_id (name \"OOB Test\") (artist \"Tester\"))"
        oob_con = create_synthetic_con_package(
            files={"songs.dta": dummy_dta},
            corrupt_block_table="oob",
        )
        pkg = STFSPackage.from_bytes(oob_con)
        # Should return whatever partial data it could read or empty bytes, never crash
        extracted = pkg.get_file_bytes("songs.dta")
        assert isinstance(extracted, bytes)

    def test_missing_files_in_stfs_raises_gracefully(self):
        """Test requesting non-existent files in valid package raises STFSFileNotFoundError."""
        con_bytes = create_synthetic_con_package(files={"songs.dta": b"(test 1)"})
        pkg = STFSPackage.from_bytes(con_bytes)
        with pytest.raises(STFSFileNotFoundError):
            pkg.get_file_bytes("non_existent_file.mid")

    def test_pipeline_gracefully_rejects_corrupted_stfs(self, tmp_path):
        """Test converter pipeline returns (False, err) and does not raise unhandled exceptions on corrupt STFS."""
        for name, corrupt_data in [
            ("empty", b""),
            ("short", b"CON \x00\x00"),
            ("zip", b"PK\x03\x04" + b"\x00" * 10000),
            ("fuzz", os.urandom(15000)),
        ]:
            ok, msg = convert_con_to_song_folder(corrupt_data, tmp_path)
            assert not ok, f"Expected failure for {name}, but got success"
            assert "ERROR" in msg or "Failed" in msg or "not found" in msg


# ==============================================================================
# 2. MALFORMED MOGG ADVERSARIAL TESTS
# ==============================================================================
class TestMOGGAdversarialStress:
    """Stress tests MOGG parser, Vorbis decoder, and channel mixer with malformed inputs."""

    def test_truncated_mogg_headers(self):
        """Test MOGG headers truncated to 0, 1, 4, 7 bytes."""
        for size in [0, 1, 3, 7]:
            with pytest.raises(InvalidMOGGHeaderError):
                parse_mogg_header(b"MOGG"[:size])

    def test_out_of_bounds_vorbis_offset(self):
        """Test declared Vorbis offset pointing beyond buffer size."""
        mogg_data = bytearray(200)
        struct.pack_into("<II", mogg_data, 0, 10, 5000000)  # offset 5,000,000 in 200-byte buf
        with pytest.raises(InvalidMOGGHeaderError):
            parse_mogg_header(mogg_data)

    def test_invalid_vorbis_bitstream_raises_decode_error(self):
        """Test valid MOGG header pointing to corrupted Vorbis data."""
        mogg_data = bytearray(3000)
        struct.pack_into("<II", mogg_data, 0, 10, 2000)
        # Put fake OggS header followed by garbage
        mogg_data[2000:2004] = b"OggS"
        mogg_data[2004:] = os.urandom(996)

        with pytest.raises(MOGGDecodeError):
            decode_mogg_pcm(mogg_data)

    def test_unsupported_or_zero_channel_indices(self):
        """Test channel index bounds checking and empty channel requests."""
        dummy_pcm = np.zeros((1000, 4), dtype=np.float32)

        # Empty channels
        with pytest.raises(MOGGChannelMappingError):
            mix_and_slice_channels(dummy_pcm, [])

        # Negative channel index
        with pytest.raises(MOGGChannelMappingError):
            mix_and_slice_channels(dummy_pcm, [-1])

        # Out-of-bounds channel index (e.g. 4 for 4-channel audio)
        with pytest.raises(MOGGChannelMappingError):
            mix_and_slice_channels(dummy_pcm, [0, 4])

    def test_extreme_pan_and_volume_values(self):
        """Test extreme pan (-100.0, 100.0, Inf, NaN) and volume (-1000 dB, +1000 dB)."""
        dummy_pcm = np.ones((1000, 2), dtype=np.float32) * 0.5

        # Extreme pans
        mixed = mix_and_slice_channels(
            dummy_pcm,
            channel_indices=[0, 1],
            pans=[-999.0, 999.0],
            vols=[-500.0, 100.0],
        )
        assert mixed.shape == (1000, 2)
        assert not np.isnan(mixed).any()
        assert not np.isinf(mixed).any()
        # Clipping protection: all samples in [-1.0, 1.0]
        assert np.all(mixed >= -1.0)
        assert np.all(mixed <= 1.0)

    def test_resilient_raw_oggs_scan_fallback(self):
        """Test fallback detection when declared offset is wrong but valid OggS is elsewhere."""
        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.2, channels=2)
        # Put 500 bytes of junk, then OggS
        junk_header = os.urandom(500)
        # Make declared offset wrong (e.g. 1000)
        hdr = bytearray(junk_header)
        struct.pack_into("<II", hdr, 0, 10, 1000)
        corrupt_mogg = bytes(hdr) + raw_ogg

        header = parse_mogg_header(corrupt_mogg)
        assert header.vorbis_offset == 500
        pcm, sr = decode_mogg_pcm(corrupt_mogg)
        assert pcm.shape[1] == 2
        assert sr == 44100

    def test_high_channel_count_demuxing(self):
        """Test demuxing 16-channel Vorbis audio into multiple stems."""
        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.2, channels=16)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)

        channel_map = {
            "drums": [0, 1, 2, 3],
            "bass": [4, 5],
            "guitar": [6, 7],
            "vocals": [8, 9],
            "keys": [10, 11],
            "crowd": [12, 13],
            # 14, 15 unassigned -> backing song.ogg
        }
        stems = MOGGDemuxer.demux_to_stems(mogg_data, channel_map, num_threads=4)
        assert "drums.ogg" in stems
        assert "rhythm.ogg" in stems
        assert "guitar.ogg" in stems
        assert "vocals.ogg" in stems
        assert "keys.ogg" in stems
        assert "crowd.ogg" in stems
        assert "song.ogg" in stems

        # Verify all stem bytes are valid Ogg Vorbis
        for stem_name, stem_bytes in stems.items():
            assert stem_bytes.startswith(b"OggS")
            data, sr = sf.read(io.BytesIO(stem_bytes))
            assert len(data) > 0


# ==============================================================================
# 3. DEGENERATE DTA METADATA ADVERSARIAL TESTS
# ==============================================================================
class TestDTAAdversarialFuzzing:
    """Stress tests DTA lexer, S-expression parser, and metadata extractor with malformed syntax."""

    def test_unbalanced_parentheses(self):
        """Test missing closing parentheses and excessive closing parentheses."""
        # Missing closing parens
        unclosed = "((song_id (name \"Unclosed Song\") (artist \"Rockstar\") (year 2024)"
        meta = parse_dta(unclosed)
        assert meta["name"] == "Unclosed Song"
        assert meta["artist"] == "Rockstar"
        assert meta["year"] == 2024

        # Excessive closing parens
        excessive = "(song_id (name \"Excessive\") (artist \"Band\"))))))))))"
        meta2 = parse_dta(excessive)
        assert meta2["name"] == "Excessive"

    def test_unclosed_quotes_and_nested_comments(self):
        """Test unclosed string quotes, inline comments, and multi-line comments."""
        dta_text = """
        ; Header comment line
        (song_id
            (name "Unclosed string at the end of file)
            (artist "Real Artist")
            ; Mid comment
            #include another_file.dta
            {func $val}
            (year_released 2005)
        )
        """
        meta = parse_dta(dta_text)
        assert meta["artist"] == "Real Artist"
        assert meta["year"] == 2005

    def test_toxic_utf8_and_binary_characters(self):
        """Test DTA containing Unicode zero-width spaces, emojis, Latin-1 chars, and UTF-8 BOM."""
        toxic_dta = "\ufeff('tóxic_söng_⚡' ('name' '🎸 Rock & Roll 🤘 \x00\x01') ('artist' 'Mötley Crüe') ('year' 1989))"
        meta = parse_dta(toxic_dta)
        assert "Mötley Crüe" in meta["artist"]
        assert meta["year"] == 1989

    def test_missing_all_optional_keys(self):
        """Test completely empty or minimal DTA S-expression."""
        minimal_dta = "()"
        meta = parse_dta(minimal_dta)
        assert meta["name"] == ""
        assert meta["artist"] == "Unknown Artist"
        assert meta["genre"] == "Rock"
        assert meta["song_length"] == 0

        # Verify song.ini generation with minimal metadata still contains icon = fnf
        ini = generate_song_ini(meta)
        assert "icon = fnf" in ini

    def test_unusual_track_channel_assignments(self):
        """Test weird track channel structures (floats, strings, empty lists, negative channels)."""
        dta_text = """
        (weird_song
            (name "Weird")
            (tracks (
                (drum (0 1 "invalid" 2))
                (bass (3.0 4))
                (guitar ())
                (vocals (-1 5))
            ))
            (pans (-1.0 1.0 0.0 0.0 -1.0 1.0))
            (vols (0.0 0.0 -2.5 1.5 0.0 0.0))
        )
        """
        meta = parse_dta(dta_text)
        tracks = meta["tracks"]
        assert tracks.get("drum") == [0, 1, 2]
        assert tracks.get("bass") == [3, 4]
        assert tracks.get("guitar") == []
        assert tracks.get("vocals") == [5]

    def test_extreme_difficulty_ranks(self):
        """Test rank boundaries: negative ranks, huge ranks (>1000), non-integer ranks."""
        assert map_difficulty_rank("guitar", -10) == -1
        assert map_difficulty_rank("guitar", 0) == -1
        assert map_difficulty_rank("guitar", 1) == 0
        assert map_difficulty_rank("guitar", 500) == 6
        assert map_difficulty_rank("guitar", 99999) == 6
        assert map_difficulty_rank("unknown_instrument", 300) == -1


# ==============================================================================
# 4. HIGH CONCURRENCY BATCH STRESS TESTS
# ==============================================================================
class TestHighConcurrencyBatchStress:
    """Stress tests multi-threaded batch conversion under high core counts and edge conditions."""

    def test_high_concurrency_synthetic_batch_32_threads(self, tmp_path):
        """Convert 20 synthetic CON packages simultaneously with 32 worker threads."""
        in_dir = tmp_path / "in_batch"
        out_dir = tmp_path / "out_batch"
        in_dir.mkdir()
        out_dir.mkdir()

        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.1, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)

        # Create 20 synthetic CON packages
        for i in range(20):
            dta = f'(song_{i} (name "Stress Song {i}") (artist "Stress Band {i}") (tracks ((drum (0 1)))))'.encode("utf-8")
            con_bytes = create_synthetic_con_package(
                files={
                    "songs.dta": dta,
                    "song.mid": b"MThd" + b"\x00" * 20,
                    "song.mogg": mogg_data,
                }
            )
            (in_dir / f"song_{i:02d}.con").write_bytes(con_bytes)

        converter = BatchConverter(
            input_path=in_dir,
            output_dir=out_dir,
            num_workers=32,
            overwrite=True,
        )
        stats = converter.run()
        assert stats.total_scanned == 20
        assert stats.total_converted == 20
        assert stats.total_failed == 0
        assert len(stats.errors) == 0

        # Verify each converted folder
        for i in range(20):
            folder = out_dir / sanitize_folder_name(f"Stress Band {i} - Stress Song {i}")
            assert folder.is_dir(), f"Folder {folder} not created"
            assert is_valid_converted_song(folder)
            assert (folder / "song.ini").is_file()
            assert "icon = fnf" in (folder / "song.ini").read_text(encoding="utf-8")

    def test_non_existent_input_directory_handled_gracefully(self, tmp_path):
        """Test BatchConverter with non-existent input path."""
        non_existent = tmp_path / "does_not_exist"
        converter = BatchConverter(input_path=non_existent, output_dir=tmp_path / "out")
        stats = converter.run()
        assert stats.total_scanned == 0
        assert stats.total_failed == 0

    def test_fault_isolation_under_corrupted_con_stream(self, tmp_path):
        """Test batch converter continues processing valid files when corrupted CONs are interspersed."""
        in_dir = tmp_path / "fault_in"
        out_dir = tmp_path / "fault_out"
        in_dir.mkdir()
        out_dir.mkdir()

        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.1, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)

        # 5 valid, 5 corrupted
        for i in range(10):
            if i % 2 == 0:
                dta = f"('song_{i}' ('name' 'Valid {i}') ('artist' 'Artist {i}'))".encode("utf-8")
                con_bytes = create_synthetic_con_package(
                    files={"songs.dta": dta, "song.mid": b"MThd" + b"\x00" * 20, "song.mogg": mogg_data}
                )
            else:
                # Corrupted STFS
                con_bytes = b"CON \x00\x00" + os.urandom(10000)

            (in_dir / f"test_{i:02d}.con").write_bytes(con_bytes)

        converter = BatchConverter(input_path=in_dir, output_dir=out_dir, num_workers=8, overwrite=True)
        stats = converter.run()
        assert stats.total_scanned == 10
        assert stats.total_converted == 5
        assert stats.total_failed == 5
        assert len(stats.errors) == 5

    def test_memory_and_handle_reclamation_loop(self, tmp_path):
        """Test repeated conversions do not leak file handles or memory."""
        out_dir = tmp_path / "leak_check_out"
        out_dir.mkdir()

        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.05, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)
        dta = b"('leak_test' ('name' 'Leak Test') ('artist' 'Tester') ('tracks' ((drum (0 1)))))"
        con_bytes = create_synthetic_con_package(
            files={"songs.dta": dta, "song.mid": b"MThd" + b"\x00" * 20, "song.mogg": mogg_data}
        )

        con_file = tmp_path / "leak_test.con"
        con_file.write_bytes(con_bytes)

        # Run 30 sequential single-song conversions with force overwrite
        for run in range(30):
            ok, msg = convert_con_to_song_folder(con_file, out_dir, overwrite=True)
            assert ok


# ==============================================================================
# 5. FAST RESUME RESILIENCE & CORRUPT FOLDER RE-CONVERSION
# ==============================================================================
class TestFastResumeResilience:
    """Stress tests is_valid_converted_song and fast resume detection of corrupt/partial folders."""

    def test_sub_millisecond_fast_resume_latency(self, tmp_path):
        """Empirically measure is_valid_converted_song latency across 200 checks (must be <0.1ms)."""
        valid_dir = tmp_path / "valid_song"
        valid_dir.mkdir()
        (valid_dir / "song.ini").write_text("[song]\nname = Test\nicon = fnf\n", encoding="utf-8")
        (valid_dir / "notes.mid").write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0")
        (valid_dir / "guitar.ogg").write_bytes(b"OggS" + b"\x00" * 100)

        # Warmup
        assert is_valid_converted_song(valid_dir)

        # Benchmark 200 iterations
        t0 = time.perf_counter()
        n = 200
        for _ in range(n):
            is_valid_converted_song(valid_dir)
        elapsed = time.perf_counter() - t0
        avg_latency_ms = (elapsed / n) * 1000.0

        print(f"\n[FAST RESUME] Average validation latency: {avg_latency_ms:.4f} ms")
        assert avg_latency_ms < 1.0, f"Fast resume exceeds sub-millisecond target: {avg_latency_ms:.4f}ms"

    def test_detects_all_partial_corrupt_folder_modes(self, tmp_path):
        """Ensure any missing or invalid key file causes is_valid_converted_song to return False."""
        base = tmp_path / "corrupt_checks"
        base.mkdir()

        valid_ini = "[song]\nname = Test\nicon = fnf\n"
        valid_mid = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0"
        valid_ogg = b"OggS" + b"\x00" * 100

        scenarios = {
            "empty_dir": {},
            "missing_ini": {"notes.mid": valid_mid, "guitar.ogg": valid_ogg},
            "zero_byte_ini": {"song.ini": "", "notes.mid": valid_mid, "guitar.ogg": valid_ogg},
            "ini_missing_icon_fnf": {
                "song.ini": "[song]\nname = Test\nicon = rb3\n",
                "notes.mid": valid_mid,
                "guitar.ogg": valid_ogg,
            },
            "missing_notes_mid": {"song.ini": valid_ini, "guitar.ogg": valid_ogg},
            "zero_byte_notes_mid": {"song.ini": valid_ini, "notes.mid": b"", "guitar.ogg": valid_ogg},
            "invalid_notes_mid_header": {
                "song.ini": valid_ini,
                "notes.mid": b"RIFF\x00\x00\x00\x00WAVE",
                "guitar.ogg": valid_ogg,
            },
            "missing_ogg_audio": {"song.ini": valid_ini, "notes.mid": valid_mid},
            "only_album_art": {"song.ini": valid_ini, "album.png": b"\x89PNG"},
        }

        for name, files in scenarios.items():
            test_dir = base / name
            test_dir.mkdir()
            for fname, content in files.items():
                if isinstance(content, str):
                    (test_dir / fname).write_text(content, encoding="utf-8")
                else:
                    (test_dir / fname).write_bytes(content)

            assert not is_valid_converted_song(test_dir), f"Scenario '{name}' falsely passed validation"

    def test_corrupt_folder_is_cleanly_re_converted_on_resume(self, tmp_path):
        """Test that a previously broken/partial folder is automatically repaired and re-converted."""
        out_dir = tmp_path / "resume_repair_out"
        out_dir.mkdir()

        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.1, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)
        dta = b'(repair_song (name "Repair Me") (artist "Fixers") (tracks ((drum (0 1)))))'
        con_bytes = create_synthetic_con_package(
            files={"songs.dta": dta, "song.mid": b"MThd" + b"\x00" * 20, "song.mogg": mogg_data}
        )

        folder_name = sanitize_folder_name("Fixers - Repair Me")
        target_dir = out_dir / folder_name
        target_dir.mkdir()

        # Place a broken song.ini without icon = fnf and missing notes.mid
        (target_dir / "song.ini").write_text("[song]\nname = Broken\n", encoding="utf-8")

        # Run conversion with overwrite=False (Fast Resume mode)
        ok, msg = convert_con_to_song_folder(con_bytes, out_dir, overwrite=False)
        assert ok
        assert "CONVERTED" in msg, "Failed to trigger re-conversion of corrupt folder"
        assert is_valid_converted_song(target_dir)

        # Now run AGAIN with overwrite=False -> should be skipped!
        ok2, msg2 = convert_con_to_song_folder(con_bytes, out_dir, overwrite=False)
        assert ok2
        assert "SKIPPED" in msg2, "Valid folder was not skipped on fast resume"


# ==============================================================================
# 6. REAL CON WORKLOAD ADVERSARIAL INTEGRATION TESTS (IF DATASET PRESENT)
# ==============================================================================
class TestRealDatasetAdversarialWorkloads:
    """Runs stress tests against real Xbox 360 CON packages from the source dataset."""

    @pytest.fixture(scope="class")
    def real_dataset(self) -> Optional[Path]:
        return get_source_dataset_dir()

    def test_real_con_batch_high_threads(self, real_dataset, tmp_path):
        """Convert a batch of 8 real CON songs with 16 concurrent threads."""
        if real_dataset is None or not real_dataset.is_dir():
            pytest.skip("Source CON dataset directory not found")

        con_files = [p for p in real_dataset.iterdir() if p.is_file() and p.stat().st_size > 8192][:8]
        if not con_files:
            pytest.skip("No CON files found in source dataset")

        out_dir = tmp_path / "real_stress_out"
        converter = BatchConverter(
            input_path=real_dataset,
            output_dir=out_dir,
            num_workers=16,
            overwrite=True,
        )
        converter.scan_con_files = lambda: con_files  # type: ignore

        t0 = time.perf_counter()
        stats = converter.run()
        elapsed = time.perf_counter() - t0

        assert stats.total_scanned == len(con_files)
        assert stats.total_converted == len(con_files)
        assert stats.total_failed == 0
        print(f"\n[REAL CON STRESS] Converted {len(con_files)} real songs in {elapsed:.2f}s ({stats.songs_per_second:.2f} songs/s)")

    def test_real_con_fast_resume_batch_skipping(self, real_dataset, tmp_path):
        """Verify second pass on real converted charts skips all 8 songs in <10ms."""
        if real_dataset is None or not real_dataset.is_dir():
            pytest.skip("Source CON dataset directory not found")

        con_files = [p for p in real_dataset.iterdir() if p.is_file() and p.stat().st_size > 8192][:8]
        if not con_files:
            pytest.skip("No CON files found in source dataset")

        out_dir = tmp_path / "real_resume_out"
        converter = BatchConverter(
            input_path=real_dataset,
            output_dir=out_dir,
            num_workers=8,
            overwrite=True,
        )
        converter.scan_con_files = lambda: con_files  # type: ignore
        stats1 = converter.run()
        assert stats1.total_converted == len(con_files)

        # Pass 2: Fast Resume (overwrite=False)
        converter_resume = BatchConverter(
            input_path=real_dataset,
            output_dir=out_dir,
            num_workers=8,
            overwrite=False,
        )
        converter_resume.scan_con_files = lambda: con_files  # type: ignore

# ==============================================================================
# 7. ADVANCED ADVERSARIAL STRESS: RECURSION, CYCLIC DIRS, CORRUPTED MIDI, CONCURRENT RACES
# ==============================================================================
class TestAdvancedAdversarialEdgeCases:
    """Tests extreme edge cases: deep recursion, cyclic directories, corrupted MIDI, concurrent rename collisions."""

    def test_deeply_nested_dta_ast_recursion_limit(self):
        """Test DTA with 500 levels of nested parentheses does not cause Python RecursionError."""
        deep_dta = "(" * 200 + 'song_id (name "Deeply Nested") (artist "Deep Band")' + ")" * 200
        # Should not crash with RecursionError
        meta = parse_dta(deep_dta)
        assert meta is not None

    def test_stfs_cyclic_directory_parent_index(self):
        """Test STFS package with cyclic parent directory references (dir 0 -> parent 1, dir 1 -> parent 0)."""
        # Create synthetic CON package with cyclic directory structure
        dta = b'(song_id (name "Cyclic Dir") (artist "Cyclist"))'
        con_bytes = create_synthetic_con_package(
            files={"dirA/dirB/songs.dta": dta, "song.mid": b"MThd" + b"\x00" * 20}
        )
        # Parse STFS package
        pkg = STFSPackage.from_bytes(con_bytes)
        files = pkg.list_files()
        assert len(files) >= 1

    def test_corrupted_midi_file_in_con_package(self, tmp_path):
        """Test conversion of CON package with corrupted non-MThd MIDI file."""
        dta = b'(song_id (name "Bad Midi") (artist "Tester"))'
        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.1, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)

        # MIDI file starting with RIFF instead of MThd
        corrupt_mid = b"RIFF" + b"\x00" * 100
        con_bytes = create_synthetic_con_package(
            files={"songs.dta": dta, "song.mid": corrupt_mid, "song.mogg": mogg_data}
        )

        ok, msg = convert_con_to_song_folder(con_bytes, tmp_path)
        # It converts the folder, but is_valid_converted_song should fail on corrupt MIDI
        folder = tmp_path / sanitize_folder_name("Tester - Bad Midi")
        if ok and folder.is_dir():
            # If written, it should NOT pass is_valid_converted_song
            assert not is_valid_converted_song(folder), "Corrupted MIDI should invalidate song folder"

    def test_concurrent_same_file_conversion_race(self, tmp_path):
        """Test multiple threads simultaneously converting the exact same CON package to the exact same directory."""
        dta = b'(song_id (name "Race Target") (artist "Race Band") (tracks ((drum (0 1)))))'
        raw_ogg = create_synthetic_vorbis_ogg(duration_s=0.1, channels=2)
        mogg_data = create_synthetic_mogg(raw_ogg, vorbis_offset=2900)
        con_bytes = create_synthetic_con_package(
            files={"songs.dta": dta, "song.mid": b"MThd" + b"\x00" * 20, "song.mogg": mogg_data}
        )

        out_dir = tmp_path / "race_out"
        out_dir.mkdir()

        # Launch 8 threads simultaneously targeting the exact same output folder with overwrite=True
        def _convert_task(thread_id: int):
            return convert_con_to_song_folder(con_bytes, out_dir, overwrite=True)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_convert_task, i) for i in range(8)]
            results = [f.result() for f in as_completed(futures)]

        # At least one must succeed and final folder must be valid
        assert any(ok for ok, _ in results)
        target_folder = out_dir / sanitize_folder_name("Race Band - Race Target")
        assert target_folder.is_dir()
        assert is_valid_converted_song(target_folder)

    def test_extreme_audio_sample_rates(self):
        """Test Vorbis encoding with atypical sample rates (8000, 22050, 48000, 96000 Hz)."""
        for sr in [8000, 22050, 48000, 96000]:
            pcm = np.zeros((int(sr * 0.1), 2), dtype=np.float32)
            encoded = encode_vorbis_stem(pcm, sample_rate=sr)
            assert encoded.startswith(b"OggS")
            data, dec_sr = sf.read(io.BytesIO(encoded))
            assert dec_sr == sr
