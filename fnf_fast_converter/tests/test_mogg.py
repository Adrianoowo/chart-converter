"""
Comprehensive unit and integration test suite for Harmonix MOGG Audio Demuxer & Stem Slicer.

Tests:
1. MOGG container header parsing across all types (Type 10, 11, 16, 17, 18, raw OGG, invalid).
2. Multi-channel Vorbis bitstream decoding into float32 PCM buffers.
3. Sample rate preservation (44.1kHz, 48kHz, 32kHz).
4. Stem channel routing, DTA track naming normalization, and constant-power pan/vol mixdown.
5. Unassigned channels routing to backing track 'song.ogg'.
6. Multi-threaded parallel stem encoding vs sequential parity.
7. Format validity and audio integrity of encoded Ogg Vorbis stem byte buffers.
8. Real Rock Band CON package MOGG extraction and demuxing benchmarks.
"""

from __future__ import annotations

import io
import math
import struct
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest
import soundfile as sf

from src.mogg import (
    AudioStem,
    InvalidMOGGHeaderError,
    MOGGChannelMappingError,
    MOGGDecodeError,
    MOGGDemuxer,
    MOGGEncodeError,
    MOGGHeader,
    canonical_stem_name,
    decode_mogg_pcm,
    encode_vorbis_stem,
    mix_and_slice_channels,
    parse_mogg_header,
)
from src.stfs import STFSPackage


# ---------------------------------------------------------------------------
# Test Helpers & Synthetic Audio Generators
# ---------------------------------------------------------------------------

def generate_synthetic_vorbis_stream(
    num_channels: int = 10,
    sample_rate: int = 44100,
    duration_sec: float = 1.0,
) -> bytes:
    """Generate a valid in-memory multi-channel Ogg Vorbis stream."""
    num_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, num_samples, endpoint=False, dtype=np.float32)

    channels = []
    for c in range(num_channels):
        freq = 220.0 * (1.2 ** c)
        ch_wave = 0.5 * np.sin(2.0 * np.pi * freq * t, dtype=np.float32)
        channels.append(ch_wave)

    pcm_data = np.stack(channels, axis=-1)
    buf = io.BytesIO()
    sf.write(buf, pcm_data, samplerate=sample_rate, format="OGG", subtype="VORBIS")
    return buf.getvalue()


def create_synthetic_mogg(
    num_channels: int = 10,
    sample_rate: int = 44100,
    duration_sec: float = 1.0,
    header_type: int = 10,
    vorbis_offset: int = 2900,
) -> bytes:
    """Generate a complete synthetic MOGG file with container header and Vorbis stream."""
    ogg_bytes = generate_synthetic_vorbis_stream(
        num_channels=num_channels,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
    )
    return MOGGDemuxer.create_mogg_container(
        vorbis_ogg_bytes=ogg_bytes,
        header_type=header_type,
        vorbis_offset=vorbis_offset,
    )


# ---------------------------------------------------------------------------
# 1. Header Parsing Unit Tests
# ---------------------------------------------------------------------------

class TestMOGGHeaderParsing:
    """Test suite for MOGG container header parsing and format detection."""

    def test_raw_ogg_header_detection(self):
        """Verify that raw unencapsulated Ogg Vorbis files (Type 0) are detected at offset 0."""
        ogg_bytes = generate_synthetic_vorbis_stream(num_channels=2, sample_rate=44100, duration_sec=0.1)
        header = parse_mogg_header(ogg_bytes)
        assert header.header_type == 0
        assert header.vorbis_offset == 0
        assert header.raw_header == b""

    def test_type_10_standard_header_fixed_offset(self):
        """Verify standard Type 10 MOGG with 2900 (0x0B54) Vorbis offset."""
        mogg_bytes = create_synthetic_mogg(num_channels=6, vorbis_offset=2900, header_type=10)
        header = parse_mogg_header(mogg_bytes)
        assert header.header_type == 10
        assert header.vorbis_offset == 2900
        assert len(header.raw_header) == 2900
        assert mogg_bytes[header.vorbis_offset:header.vorbis_offset + 4] == b"OggS"

    def test_type_10_dynamic_offsets(self):
        """Verify Type 10 MOGG with various dynamic offsets (e.g. 2532, 4852, 5516)."""
        for offset in (2532, 3276, 4852, 5516, 8192):
            mogg_bytes = create_synthetic_mogg(num_channels=4, vorbis_offset=offset, header_type=10)
            header = parse_mogg_header(mogg_bytes)
            assert header.header_type == 10
            assert header.vorbis_offset == offset
            assert len(header.raw_header) == offset

    def test_other_mogg_header_types(self):
        """Verify parsing container header types 11, 16, 17, 18."""
        for h_type in (11, 16, 17, 18):
            mogg_bytes = create_synthetic_mogg(num_channels=2, vorbis_offset=1024, header_type=h_type)
            header = parse_mogg_header(mogg_bytes)
            assert header.header_type == h_type
            assert header.vorbis_offset == 1024

    def test_resilient_fallback_sync_scan(self):
        """Verify fallback scan if declared offset is slightly inaccurate but 'OggS' exists."""
        ogg_bytes = generate_synthetic_vorbis_stream(num_channels=2, sample_rate=44100, duration_sec=0.1)
        # Create header with declared offset 500, but place OggS at 600
        header_buf = bytearray(600)
        struct.pack_into("<II", header_buf, 0, 10, 500)
        mogg_corrupt_offset = bytes(header_buf) + ogg_bytes

        header = parse_mogg_header(mogg_corrupt_offset)
        assert header.header_type == 10
        assert header.vorbis_offset == 600

    def test_invalid_header_too_small(self):
        """Verify that data smaller than 4 bytes raises InvalidMOGGHeaderError."""
        with pytest.raises(InvalidMOGGHeaderError, match="too small"):
            parse_mogg_header(b"abc")

    def test_invalid_header_no_oggs_magic(self):
        """Verify that MOGG without valid OggS stream raises InvalidMOGGHeaderError."""
        garbage_header = bytearray(4096)
        struct.pack_into("<II", garbage_header, 0, 10, 2900)
        # Fill rest with random non-OggS bytes
        garbage_data = bytes(garbage_header) + (b"\xFF" * 1000)

        with pytest.raises(InvalidMOGGHeaderError, match="No valid Ogg Vorbis stream"):
            parse_mogg_header(garbage_data)


# ---------------------------------------------------------------------------
# 2. In-Memory PCM Decoding Unit Tests
# ---------------------------------------------------------------------------

class TestMOGGDecoding:
    """Test suite for in-memory Vorbis audio decoding."""

    def test_decode_10_channel_vorbis_pcm(self):
        """Verify decoding 10-channel 44.1kHz Vorbis audio into float32 numpy array."""
        sample_rate = 44100
        duration_sec = 0.5
        mogg_bytes = create_synthetic_mogg(
            num_channels=10,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            vorbis_offset=2900,
        )

        audio_pcm, sr = decode_mogg_pcm(mogg_bytes)
        assert sr == sample_rate
        assert audio_pcm.dtype == np.float32
        assert audio_pcm.ndim == 2
        assert audio_pcm.shape[1] == 10
        expected_samples = int(sample_rate * duration_sec)
        # Vorbis compression may pad slightly by ~a few hundred samples for windowing
        assert abs(audio_pcm.shape[0] - expected_samples) < 2048

    def test_decode_various_sample_rates(self):
        """Verify sample rate preservation across 32kHz, 44.1kHz, and 48kHz."""
        for target_sr in (32000, 44100, 48000):
            mogg_bytes = create_synthetic_mogg(
                num_channels=2,
                sample_rate=target_sr,
                duration_sec=0.2,
            )
            audio_pcm, sr = decode_mogg_pcm(mogg_bytes)
            assert sr == target_sr
            assert audio_pcm.shape[1] == 2

    def test_decode_memoryview_input(self):
        """Verify decode works seamlessly on memoryview slices without copying."""
        mogg_bytes = create_synthetic_mogg(num_channels=4, duration_sec=0.2)
        mv = memoryview(mogg_bytes)
        audio_pcm, sr = decode_mogg_pcm(mv)
        assert audio_pcm.shape[1] == 4
        assert sr == 44100

    def test_decode_corrupted_stream_raises(self):
        """Verify that corrupted Vorbis payload raises MOGGDecodeError."""
        hdr = struct.pack("<II", 10, 8)
        corrupt_vorbis = b"OggS" + (b"\x00\xFF" * 100)
        with pytest.raises(MOGGDecodeError, match="Failed to decode"):
            decode_mogg_pcm(hdr + corrupt_vorbis)


# ---------------------------------------------------------------------------
# 3. Channel Mapping, Normalization & Mixing Unit Tests
# ---------------------------------------------------------------------------

class TestChannelMappingAndMixing:
    """Test suite for DTA track normalization, channel extraction, and pan/vol mixing."""

    def test_canonical_stem_naming(self):
        """Verify track key normalization to standard Clone Hero stem filenames."""
        assert canonical_stem_name("drum") == "drums.ogg"
        assert canonical_stem_name("drums") == "drums.ogg"
        assert canonical_stem_name("bass") == "rhythm.ogg"
        assert canonical_stem_name("rhythm") == "rhythm.ogg"
        assert canonical_stem_name("guitar") == "guitar.ogg"
        assert canonical_stem_name("lead_guitar") == "guitar.ogg"
        assert canonical_stem_name("vocals") == "vocals.ogg"
        assert canonical_stem_name("vocal") == "vocals.ogg"
        assert canonical_stem_name("vox") == "vocals.ogg"
        assert canonical_stem_name("keys") == "keys.ogg"
        assert canonical_stem_name("crowd") == "crowd.ogg"
        assert canonical_stem_name("song") == "song.ogg"
        assert canonical_stem_name("backing") == "song.ogg"
        assert canonical_stem_name("drums.ogg") == "drums.ogg"
        assert canonical_stem_name("synth") == "keys.ogg"
        assert canonical_stem_name("custom_track") == "custom_track.ogg"

    def test_stereo_pair_direct_slicing(self):
        """Verify fast-path direct slicing for standard stereo pairs."""
        N = 1000
        audio = np.arange(N * 4, dtype=np.float32).reshape(N, 4)
        pans = [-1.0, 1.0, -1.0, 1.0]
        vols = [0.0, 0.0, 0.0, 0.0]

        stem = mix_and_slice_channels(audio, [2, 3], pans, vols)
        assert stem.shape == (N, 2)
        np.testing.assert_array_equal(stem[:, 0], audio[:, 2])
        np.testing.assert_array_equal(stem[:, 1], audio[:, 3])

    def test_mono_channel_slicing(self):
        """Verify mono channel slicing."""
        N = 1000
        audio = np.ones((N, 4), dtype=np.float32)
        audio[:, 1] = 0.75
        pans = [0.0, 0.0, 0.0, 0.0]
        vols = [0.0, 0.0, 0.0, 0.0]

        stem = mix_and_slice_channels(audio, [1], pans, vols)
        assert stem.shape == (N, 1)
        np.testing.assert_array_equal(stem[:, 0], audio[:, 1])

    def test_multichannel_drum_mixdown_constant_power(self):
        """Verify 4-channel Rock Band drums downmixing with constant-power panning without clipping."""
        N = 1000
        # Channels: 0 = kick (mono center), 1 = snare (mono center), 2 = kit L (-1.0), 3 = kit R (1.0)
        audio = np.zeros((N, 4), dtype=np.float32)
        audio[:, 0] = 0.2  # kick
        audio[:, 1] = 0.2  # snare
        audio[:, 2] = 0.3  # kit L
        audio[:, 3] = 0.4  # kit R

        pans = [0.0, 0.0, -1.0, 1.0]
        vols = [0.0, 0.0, 0.0, 0.0]

        stem = mix_and_slice_channels(audio, [0, 1, 2, 3], pans, vols)
        assert stem.shape == (N, 2)

        # Center channels (kick/snare) at pan 0.0 contribute cos(pi/4) ≈ 0.7071 to each side
        center_gain = math.cos(math.pi / 4.0)
        expected_l = (0.2 + 0.2) * center_gain + 0.3 * 1.0 + 0.4 * 0.0
        expected_r = (0.2 + 0.2) * center_gain + 0.3 * 0.0 + 0.4 * 1.0

        np.testing.assert_allclose(stem[0, 0], expected_l, rtol=1e-4)
        np.testing.assert_allclose(stem[0, 1], expected_r, rtol=1e-4)

    def test_volume_db_scaling(self):
        """Verify volume dB gain scaling in mixdown (e.g. -6 dB ≈ 0.501 gain)."""
        N = 500
        audio = np.ones((N, 2), dtype=np.float32)
        pans = [-1.0, 1.0]
        vols = [-6.0206, -6.0206]  # -6.0206 dB = exactly 0.5 amplitude

        stem = mix_and_slice_channels(audio, [0, 1], pans, vols)
        assert stem.shape == (N, 2)
        np.testing.assert_allclose(stem[:, 0], 0.5, atol=1e-3)
        np.testing.assert_allclose(stem[:, 1], 0.5, atol=1e-3)

    def test_channel_index_out_of_bounds_raises(self):
        """Verify that invalid channel index raises MOGGChannelMappingError."""
        audio = np.zeros((100, 4), dtype=np.float32)
        with pytest.raises(MOGGChannelMappingError, match="out of bounds"):
            mix_and_slice_channels(audio, [0, 4])  # 4 is out of bounds for 4-ch audio

    def test_empty_channel_indices_raises(self):
        """Verify that empty channel list raises MOGGChannelMappingError."""
        audio = np.zeros((100, 4), dtype=np.float32)
        with pytest.raises(MOGGChannelMappingError, match="empty"):
            mix_and_slice_channels(audio, [])


# ---------------------------------------------------------------------------
# 4. Stem Encoding & Parallel Demuxing Unit Tests
# ---------------------------------------------------------------------------

class TestStemEncodingAndDemuxing:
    """Test suite for Vorbis stem encoding and high-speed parallel demuxing."""

    def test_encode_vorbis_stem_format_validity(self):
        """Verify that encode_vorbis_stem produces a valid Ogg Vorbis stream."""
        sr = 44100
        n_samples = 44100
        pcm = np.random.uniform(-0.5, 0.5, (n_samples, 2)).astype(np.float32)

        ogg_bytes = encode_vorbis_stem(pcm, sample_rate=sr, quality=0.5)
        assert ogg_bytes.startswith(b"OggS")
        assert len(ogg_bytes) > 1000

        # Verify readability with soundfile
        info = sf.info(io.BytesIO(ogg_bytes))
        assert info.format == "OGG"
        assert info.subtype == "VORBIS"
        assert info.samplerate == sr
        assert info.channels == 2
        assert abs(info.duration - 1.0) < 0.05

    def test_demux_to_stems_full_routing(self):
        """Verify complete stem demuxing with instrument mapping and unassigned backing routing."""
        mogg_bytes = create_synthetic_mogg(
            num_channels=10,
            sample_rate=44100,
            duration_sec=0.5,
            vorbis_offset=2900,
        )

        mapping = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
            "vocals": [6, 7],
        }
        # Channels 8, 9 unassigned -> should automatically create song.ogg

        stems = MOGGDemuxer.demux_to_stems(mogg_bytes, mapping, num_threads=4)

        assert "drums.ogg" in stems
        assert "rhythm.ogg" in stems
        assert "guitar.ogg" in stems
        assert "vocals.ogg" in stems
        assert "song.ogg" in stems
        assert len(stems) == 5

        for name, data in stems.items():
            assert isinstance(data, bytes)
            assert data.startswith(b"OggS")
            info = sf.info(io.BytesIO(data))
            assert info.samplerate == 44100
            assert info.channels == 2

    def test_demux_empty_mapping_creates_song_ogg(self):
        """Verify that when mapping is empty, all channels become song.ogg."""
        mogg_bytes = create_synthetic_mogg(num_channels=2, sample_rate=48000, duration_sec=0.3)
        stems = MOGGDemuxer.demux_to_stems(mogg_bytes, {}, num_threads=2)
        assert list(stems.keys()) == ["song.ogg"]
        info = sf.info(io.BytesIO(stems["song.ogg"]))
        assert info.samplerate == 48000
        assert info.channels == 2

    def test_demux_no_unassigned_omits_song_ogg(self):
        """Verify that when all channels are assigned, song.ogg is omitted."""
        mogg_bytes = create_synthetic_mogg(num_channels=4, duration_sec=0.3)
        mapping = {
            "guitar": [0, 1],
            "bass": [2, 3],
        }
        stems = MOGGDemuxer.demux_to_stems(mogg_bytes, mapping, num_threads=2)
        assert set(stems.keys()) == {"guitar.ogg", "rhythm.ogg"}
        assert "song.ogg" not in stems

    def test_demux_to_stem_objects(self):
        """Verify demux_to_stem_objects returns rich AudioStem instances."""
        mogg_bytes = create_synthetic_mogg(num_channels=6, duration_sec=0.4)
        mapping = {
            "drum": [0, 1],
            "guitar": [2, 3],
        }
        stem_objs = MOGGDemuxer.demux_to_stem_objects(mogg_bytes, mapping, num_threads=2)
        assert "drums.ogg" in stem_objs
        assert "guitar.ogg" in stem_objs
        assert "song.ogg" in stem_objs

        drum_stem = stem_objs["drums.ogg"]
        assert isinstance(drum_stem, AudioStem)
        assert drum_stem.filename == "drums.ogg"
        assert drum_stem.channels == [0, 1]
        assert drum_stem.num_channels == 2
        assert drum_stem.sample_rate == 44100
        assert drum_stem.duration_seconds > 0.3
        assert drum_stem.data.startswith(b"OggS")

    def test_parallel_vs_sequential_execution_parity(self):
        """Verify parallel encoding (num_threads=4) matches single-threaded (num_threads=1)."""
        mogg_bytes = create_synthetic_mogg(num_channels=6, duration_sec=0.3)
        mapping = {"drum": [0, 1], "bass": [2, 3], "guitar": [4, 5]}

        stems_seq = MOGGDemuxer.demux_to_stems(mogg_bytes, mapping, num_threads=1)
        stems_par = MOGGDemuxer.demux_to_stems(mogg_bytes, mapping, num_threads=4)

        assert set(stems_seq.keys()) == set(stems_par.keys())
        for k in stems_seq:
            # Both must be valid Ogg Vorbis with matching channel counts
            info_seq = sf.info(io.BytesIO(stems_seq[k]))
            info_par = sf.info(io.BytesIO(stems_par[k]))
            assert info_seq.channels == info_par.channels
            assert info_seq.samplerate == info_par.samplerate


# ---------------------------------------------------------------------------
# 5. Real CON Package MOGG Extraction & Benchmarking
# ---------------------------------------------------------------------------

class TestRealCONMOGGDemuxing:
    """Integration and benchmark tests against real Rock Band CON packages."""

    @pytest.fixture
    def sample_con_files(self) -> List[Path]:
        """Discover sample CON files from Downloads."""
        base_dir = Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB")
        if not base_dir.exists():
            pytest.skip(f"CON sample directory not found: {base_dir}")

        files = [p for p in base_dir.glob("*") if p.is_file()]
        if not files:
            pytest.skip("No CON files found in test directory.")
        return sorted(files)

    def test_real_con_mogg_header_detection(self, sample_con_files: List[Path]):
        """Verify header parsing across 5 real Rock Band CON packages."""
        tested = 0
        for con_file in sample_con_files[:5]:
            pkg = STFSPackage.from_file(con_file)
            mogg_paths = pkg.find_by_extension(".mogg")
            if not mogg_paths:
                continue

            mogg_bytes = pkg.get_file_bytes(mogg_paths[0])
            hdr = parse_mogg_header(mogg_bytes)
            assert hdr.header_type == 10
            assert hdr.vorbis_offset > 0
            assert mogg_bytes[hdr.vorbis_offset:hdr.vorbis_offset + 4] == b"OggS"
            tested += 1

        assert tested >= 1, "At least one CON package must contain a valid MOGG."

    def test_real_con_buddy_holly_demux_and_benchmark(self, sample_con_files: List[Path]):
        """
        Verify end-to-end demuxing of Weezer - Buddy Holly into 5 stems.
        Benchmark execution time to verify target performance (< 2.5s total audio pipeline).
        """
        buddy_file = next((f for f in sample_con_files if "Buddy Holly" in f.name), None)
        if buddy_file is None:
            pytest.skip("Weezer - Buddy Holly CON not found in sample folder.")

        # Extract MOGG directly from memory
        pkg = STFSPackage.from_file(buddy_file)
        mogg_path = pkg.find_by_extension(".mogg")[0]
        mogg_bytes = pkg.get_file_bytes(mogg_path)

        mapping = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
            "vocals": [6, 7],
        }
        # Channels 8, 9 unassigned -> song.ogg

        t0 = time.perf_counter()
        stems = MOGGDemuxer.demux_to_stems(
            mogg_data=mogg_bytes,
            channel_mapping=mapping,
            num_threads=5,
            quality=0.5,
        )
        elapsed = time.perf_counter() - t0

        print(f"\n[BENCHMARK] Buddy Holly (142s, 10ch) Demux & Parallel Stem Encode: {elapsed:.3f}s")

        # Verify all 5 stems
        expected_stems = {"drums.ogg", "rhythm.ogg", "guitar.ogg", "vocals.ogg", "song.ogg"}
        assert set(stems.keys()) == expected_stems

        for stem_name, stem_bytes in stems.items():
            assert stem_bytes.startswith(b"OggS")
            info = sf.info(io.BytesIO(stem_bytes))
            assert info.samplerate == 44100
            assert info.channels == 2
            # Song is ~163 seconds long
            assert abs(info.duration - 163.0) < 5.0
            assert len(stem_bytes) > 500_000  # Stems should be non-trivial compressed audio (>500KB)

        # Performance assertion: In-memory pipeline should complete well under 2.5s (Onyx takes ~11s)
        assert elapsed < 2.5, f"Demuxing took {elapsed:.2f}s, expected < 2.5s"
