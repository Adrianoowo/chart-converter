"""
Harmonix MOGG Container Demuxer & Multi-Threaded Audio Stem Slicer.

Provides high-speed, 100% in-memory parsing of Harmonix MOGG audio containers,
multi-channel Vorbis audio decoding, DTA channel routing and pan/vol mixing,
and concurrent multi-threaded stem re-encoding into standard Clone Hero / Phase Shift
Ogg Vorbis stem files (drums.ogg, guitar.ogg, rhythm.ogg, vocals.ogg, song.ogg).
"""

from __future__ import annotations

import io
import math
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import soundfile as sf


class MOGGError(Exception):
    """Base exception for all MOGG parsing, decoding, and demuxing errors."""
    pass


class InvalidMOGGHeaderError(MOGGError):
    """Raised when MOGG container header is malformed or Vorbis stream is missing."""
    pass


class MOGGDecodeError(MOGGError):
    """Raised when in-memory Vorbis audio decoding fails."""
    pass


class MOGGEncodeError(MOGGError):
    """Raised when Vorbis stem encoding fails."""
    pass


class MOGGChannelMappingError(MOGGError):
    """Raised when channel indices are invalid or out of bounds."""
    pass


@dataclass(frozen=True)
class MOGGHeader:
    """
    Parsed Harmonix MOGG container header metadata.

    Attributes:
        header_type: MOGG format type (10 = standard unencrypted, 11 = encrypted RB1/2,
                     16 = custom type 16, 17 = custom RB3/Blitz, 18 = encrypted custom,
                     0 = raw unencapsulated Ogg Vorbis).
        vorbis_offset: Byte offset where the standard Ogg Vorbis bitstream (magic 'OggS') begins.
        raw_header: Bytes of the container header preceding the Vorbis stream.
    """
    header_type: int
    vorbis_offset: int
    raw_header: bytes


@dataclass(frozen=True)
class AudioStem:
    """
    Represents an isolated, encoded audio stem.

    Attributes:
        filename: Target stem filename (e.g. 'drums.ogg', 'guitar.ogg', 'song.ogg').
        channels: Source channel indices mapped to this stem.
        data: Encoded Ogg Vorbis byte buffer.
        sample_rate: Audio sample rate in Hz (e.g. 44100).
        num_channels: Number of channels in this stem (1 for mono, 2 for stereo).
        num_samples: Number of audio frames.
        duration_seconds: Duration of the audio in seconds.
    """
    filename: str
    channels: List[int]
    data: bytes
    sample_rate: int
    num_channels: int
    num_samples: int
    duration_seconds: float


# Mapping from standard DTA track keys to canonical stem filenames
TRACK_NAME_TO_STEM: Dict[str, str] = {
    "drum": "drums.ogg",
    "drums": "drums.ogg",
    "bass": "rhythm.ogg",
    "rhythm": "rhythm.ogg",
    "guitar": "guitar.ogg",
    "lead_guitar": "guitar.ogg",
    "guitar_coop": "guitar.ogg",
    "vocals": "vocals.ogg",
    "vocal": "vocals.ogg",
    "vox": "vocals.ogg",
    "singers": "vocals.ogg",
    "keys": "keys.ogg",
    "keyboard": "keys.ogg",
    "synth": "keys.ogg",
    "crowd": "crowd.ogg",
    "song": "song.ogg",
    "backing": "song.ogg",
    "band": "song.ogg",
}


def canonical_stem_name(track_name: str) -> str:
    """
    Normalize an instrument track name or key to its canonical Clone Hero stem filename.

    Args:
        track_name: Raw track key (e.g. 'drum', 'bass', 'guitar', 'vocals', 'song', 'drums.ogg').

    Returns:
        Canonical stem filename (e.g. 'drums.ogg', 'rhythm.ogg', 'guitar.ogg', 'vocals.ogg', 'song.ogg').
    """
    cleaned = track_name.strip().lower()
    if cleaned in TRACK_NAME_TO_STEM:
        return TRACK_NAME_TO_STEM[cleaned]

    # If it already has an audio extension, keep it normalized
    if cleaned.endswith((".ogg", ".wav", ".opus", ".mp3", ".flac")):
        base = cleaned.rsplit(".", 1)[0]
        if base in TRACK_NAME_TO_STEM:
            return TRACK_NAME_TO_STEM[base]
        return cleaned

    return f"{cleaned}.ogg"


def parse_mogg_header(mogg_data: Union[bytes, bytearray, memoryview]) -> MOGGHeader:
    """
    Parse a Harmonix MOGG container header and locate the Vorbis bitstream offset.

    Supports:
    - Type 10 (Standard unencrypted MOGG, e.g. offset 2900 / 0x0B54 or dynamic offset).
    - Type 11 / 16 / 17 / 18 container headers.
    - Raw unencapsulated Ogg Vorbis streams (starting with 'OggS' at offset 0).

    Args:
        mogg_data: Raw MOGG file bytes, bytearray, or memoryview.

    Returns:
        MOGGHeader with format type and Vorbis byte offset.

    Raises:
        InvalidMOGGHeaderError: If header is malformed or no Vorbis stream is found.
    """
    data_len = len(mogg_data)
    if data_len < 4:
        raise InvalidMOGGHeaderError(f"MOGG data too small ({data_len} bytes); minimum 4 bytes required.")

    # Check for raw unencapsulated Ogg Vorbis bitstream (magic 'OggS')
    if mogg_data[:4] == b"OggS":
        return MOGGHeader(header_type=0, vorbis_offset=0, raw_header=b"")

    if data_len < 8:
        raise InvalidMOGGHeaderError(
            f"MOGG header too small ({data_len} bytes) to contain 8-byte type/offset header."
        )

    # Read 32-bit little-endian Type and Vorbis Stream Offset
    header_type, vorbis_offset = struct.unpack_from("<II", mogg_data, 0)

    # Validate vorbis_offset pointing to 'OggS'
    if 0 <= vorbis_offset <= data_len - 4:
        if mogg_data[vorbis_offset:vorbis_offset + 4] == b"OggS":
            raw_header = bytes(mogg_data[:vorbis_offset])
            return MOGGHeader(
                header_type=header_type,
                vorbis_offset=vorbis_offset,
                raw_header=raw_header,
            )

    # Resilient fallback: scan for 'OggS' magic within first 64KB (or entire data)
    scan_limit = min(65536, data_len)
    data_bytes = bytes(mogg_data[:scan_limit])
    ogg_pos = data_bytes.find(b"OggS")
    if ogg_pos != -1:
        raw_header = bytes(mogg_data[:ogg_pos])
        return MOGGHeader(
            header_type=header_type,
            vorbis_offset=ogg_pos,
            raw_header=raw_header,
        )

    raise InvalidMOGGHeaderError(
        f"No valid Ogg Vorbis stream (magic 'OggS') found in MOGG container "
        f"(Header Type: {header_type}, Declared Offset: {vorbis_offset}, Total Size: {data_len} bytes)."
    )


def decode_mogg_pcm(
    mogg_data: Union[bytes, bytearray, memoryview],
    vorbis_offset: Optional[int] = None,
) -> Tuple[np.ndarray, int]:
    """
    Decode multi-channel Vorbis bitstream from in-memory MOGG data into planar/interleaved PCM.

    Args:
        mogg_data: Raw MOGG file bytes, bytearray, or memoryview.
        vorbis_offset: Optional explicit byte offset where Vorbis stream starts.
                       If None, auto-detected via parse_mogg_header.

    Returns:
        Tuple of (audio_pcm, sample_rate):
        - audio_pcm: 2D numpy.ndarray of float32 samples with shape (num_samples, num_channels).
        - sample_rate: Audio sampling rate in Hz (typically 44100).

    Raises:
        InvalidMOGGHeaderError: If MOGG header is invalid.
        MOGGDecodeError: If Vorbis bitstream cannot be decoded.
    """
    if vorbis_offset is None:
        header = parse_mogg_header(mogg_data)
        vorbis_offset = header.vorbis_offset

    if vorbis_offset < 0 or vorbis_offset >= len(mogg_data):
        raise InvalidMOGGHeaderError(
            f"Vorbis offset {vorbis_offset} is outside buffer range [0, {len(mogg_data)})."
        )

    vorbis_bytes = bytes(mogg_data[vorbis_offset:])
    if not vorbis_bytes.startswith(b"OggS"):
        # Double check if OggS is present
        ogg_idx = vorbis_bytes.find(b"OggS")
        if ogg_idx != -1:
            vorbis_bytes = vorbis_bytes[ogg_idx:]
        else:
            raise InvalidMOGGHeaderError("Vorbis slice does not contain 'OggS' sync pattern.")

    try:
        buf = io.BytesIO(vorbis_bytes)
        audio_data, sample_rate = sf.read(buf, dtype="float32", always_2d=True)
    except Exception as exc:
        raise MOGGDecodeError(f"Failed to decode in-memory Vorbis audio stream: {exc}") from exc

    return audio_data, int(sample_rate)


def mix_and_slice_channels(
    audio_data: np.ndarray,
    channel_indices: Sequence[int],
    pans: Optional[Sequence[float]] = None,
    vols: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """
    Extract, balance, and mix specified channel indices into an isolated audio stem buffer.

    Handles:
    - Direct slicing for standard 2-channel stereo pairs and 1-channel mono tracks.
    - Constant-power stereo panning and volume dB gain scaling for multi-channel stems (>2 channels,
      e.g. 4-channel Rock Band drums with kick/snare/kit).
    - Clipping protection to prevent digital distortion.

    Args:
        audio_data: 2D numpy array of shape (num_samples, total_channels).
        channel_indices: Sequence of channel indices assigned to this stem.
        pans: Optional sequence of stereo pan values in [-1.0, 1.0] for all song channels.
        vols: Optional sequence of volume gain values in dB for all song channels.

    Returns:
        2D numpy array of shape (num_samples, stem_channels), where stem_channels is 1 or 2.

    Raises:
        MOGGChannelMappingError: If channel indices are empty or out of bounds.
    """
    if not channel_indices:
        raise MOGGChannelMappingError("Cannot mix empty channel indices.")

    total_channels = audio_data.shape[1]
    for ch in channel_indices:
        if ch < 0 or ch >= total_channels:
            raise MOGGChannelMappingError(
                f"Channel index {ch} out of bounds for {total_channels}-channel audio stream."
            )

    n_samples = audio_data.shape[0]
    num_ch = len(channel_indices)

    # Check if this is a standard 2-channel stereo pair without custom panning/volume modifications
    if num_ch == 2:
        c0, c1 = channel_indices[0], channel_indices[1]
        p0 = pans[c0] if pans is not None and c0 < len(pans) else -1.0
        p1 = pans[c1] if pans is not None and c1 < len(pans) else 1.0
        v0 = vols[c0] if vols is not None and c0 < len(vols) else 0.0
        v1 = vols[c1] if vols is not None and c1 < len(vols) else 0.0

        if math.isclose(p0, -1.0, abs_tol=1e-3) and math.isclose(p1, 1.0, abs_tol=1e-3) and \
           math.isclose(v0, 0.0, abs_tol=1e-3) and math.isclose(v1, 0.0, abs_tol=1e-3):
            # Fast-path: Direct slice without arithmetic
            return np.ascontiguousarray(audio_data[:, [c0, c1]], dtype=np.float32)

    # Check if this is a single mono channel with standard center pan and 0 dB vol
    if num_ch == 1:
        c0 = channel_indices[0]
        p0 = pans[c0] if pans is not None and c0 < len(pans) else 0.0
        v0 = vols[c0] if vols is not None and c0 < len(vols) else 0.0

        if math.isclose(p0, 0.0, abs_tol=1e-3) and math.isclose(v0, 0.0, abs_tol=1e-3):
            # Fast-path: Direct mono slice
            return np.ascontiguousarray(audio_data[:, [c0]], dtype=np.float32)

        if math.isclose(p0, 0.0, abs_tol=1e-3):
            # Mono with volume scale
            gain = 10.0 ** (v0 / 20.0)
            scaled = audio_data[:, [c0]] * np.float32(gain)
            return np.clip(scaled, -1.0, 1.0, out=scaled)

    # General pan and volume mixdown into stereo (2 channels)
    left_accum = np.zeros(n_samples, dtype=np.float32)
    right_accum = np.zeros(n_samples, dtype=np.float32)

    for ch in channel_indices:
        pan_val = float(pans[ch]) if pans is not None and ch < len(pans) else 0.0
        pan_val = max(-1.0, min(1.0, pan_val))
        vol_db = float(vols[ch]) if vols is not None and ch < len(vols) else 0.0
        gain = 10.0 ** (vol_db / 20.0)

        # Constant-power panning law:
        # theta in [0, pi/2] as pan goes from -1.0 (Left) to 1.0 (Right)
        theta = (pan_val + 1.0) * (math.pi / 4.0)
        gain_l = float(math.cos(theta) * gain)
        gain_r = float(math.sin(theta) * gain)

        src_ch = audio_data[:, ch]
        if not math.isclose(gain_l, 0.0, abs_tol=1e-6):
            left_accum += src_ch * np.float32(gain_l)
        if not math.isclose(gain_r, 0.0, abs_tol=1e-6):
            right_accum += src_ch * np.float32(gain_r)

    stereo_stem = np.stack([left_accum, right_accum], axis=-1)
    return np.clip(stereo_stem, -1.0, 1.0, out=stereo_stem)


def encode_vorbis_stem(
    stem_audio: np.ndarray,
    sample_rate: int,
    quality: float = 0.5,
    chunk_frames: int = 65536,
) -> bytes:
    """
    Encode an in-memory PCM audio array to a standard Ogg Vorbis byte buffer.

    Zero disk writes: compresses audio directly into an in-memory BytesIO buffer
    using chunked buffer streaming to eliminate CFFI stack recursion limits and optimize throughput.

    Args:
        stem_audio: 1D or 2D numpy array of audio samples (float32).
        sample_rate: Sampling rate in Hz (e.g. 44100).
        quality: Vorbis encoding quality in [0.0, 1.0] (default: 0.5 ~160 kbps).
        chunk_frames: Block size for chunked streaming encode (default: 65536 frames).

    Returns:
        Raw encoded Ogg Vorbis bytes.

    Raises:
        MOGGEncodeError: If Vorbis encoding fails.
    """
    buf = io.BytesIO()
    try:
        # Ensure 2D or 1D float32 contiguous array
        if not stem_audio.flags["C_CONTIGUOUS"] or stem_audio.dtype != np.float32:
            stem_audio = np.ascontiguousarray(stem_audio, dtype=np.float32)

        channels = 1 if stem_audio.ndim == 1 else stem_audio.shape[1]
        total_frames = stem_audio.shape[0]

        with sf.SoundFile(
            buf,
            mode="w",
            samplerate=sample_rate,
            channels=channels,
            subtype="VORBIS",
            format="OGG",
        ) as f:
            if total_frames <= chunk_frames:
                f.write(stem_audio)
            else:
                for start_idx in range(0, total_frames, chunk_frames):
                    end_idx = min(start_idx + chunk_frames, total_frames)
                    f.write(stem_audio[start_idx:end_idx])

        return buf.getvalue()
    except Exception as exc:
        raise MOGGEncodeError(f"Failed to encode audio stem to Ogg Vorbis: {exc}") from exc


class MOGGDemuxer:
    """
    High-performance Harmonix MOGG container demuxer and parallel audio stem slicer.

    Demuxes multi-channel audio directly from memory buffers, routes channels according
    to DTA track definitions, mixes multi-channel stems (drums, guitar, bass, vocals, song),
    and encodes all stems in parallel using multi-threaded thread pools with zero disk I/O.
    """

    @classmethod
    def parse_header(cls, mogg_data: Union[bytes, bytearray, memoryview]) -> MOGGHeader:
        """
        Parse MOGG container header and extract format type and Vorbis offset.

        Args:
            mogg_data: Raw MOGG bytes or memoryview.

        Returns:
            MOGGHeader instance.
        """
        return parse_mogg_header(mogg_data)

    @classmethod
    def get_vorbis_offset(cls, mogg_data: Union[bytes, bytearray, memoryview]) -> int:
        """
        Get the byte offset of the Vorbis bitstream inside the MOGG container.

        Args:
            mogg_data: Raw MOGG bytes or memoryview.

        Returns:
            Integer byte offset.
        """
        return parse_mogg_header(mogg_data).vorbis_offset

    @classmethod
    def decode_pcm(
        cls,
        mogg_data: Union[bytes, bytearray, memoryview],
        vorbis_offset: Optional[int] = None,
    ) -> Tuple[np.ndarray, int]:
        """
        Decode the multi-channel Vorbis bitstream from MOGG data into float32 PCM.

        Args:
            mogg_data: Raw MOGG bytes or memoryview.
            vorbis_offset: Optional explicit Vorbis stream offset.

        Returns:
            Tuple of (audio_pcm, sample_rate).
        """
        return decode_mogg_pcm(mogg_data, vorbis_offset)

    @classmethod
    def demux_to_stems(
        cls,
        mogg_data: Union[bytes, bytearray, memoryview],
        channel_mapping: Dict[str, Sequence[int]],
        pans: Optional[Sequence[float]] = None,
        vols: Optional[Sequence[float]] = None,
        num_threads: int = 4,
        quality: float = 0.5,
    ) -> Dict[str, bytes]:
        """
        Demux multi-channel MOGG audio into separate standard Ogg Vorbis stem byte buffers.

        Follows DTA channel assignments:
        - 'drum' / 'drums' -> 'drums.ogg'
        - 'bass' / 'rhythm' -> 'rhythm.ogg'
        - 'guitar' / 'lead_guitar' -> 'guitar.ogg'
        - 'vocals' / 'vocal' -> 'vocals.ogg'
        - 'keys' / 'keyboard' -> 'keys.ogg'
        - 'crowd' -> 'crowd.ogg'
        - Unassigned channels / backing -> 'song.ogg'

        All stems are encoded concurrently in memory across CPU threads with zero disk writes.

        Args:
            mogg_data: Raw MOGG container bytes or memoryview.
            channel_mapping: Dictionary mapping track names to channel index sequences
                             (e.g. {'drum': [0, 1], 'bass': [2, 3], 'guitar': [4, 5], 'vocals': [6, 7]}).
            pans: Optional list of stereo pan values (-1.0 to 1.0) for each channel.
            vols: Optional list of volume dB offsets for each channel.
            num_threads: Number of parallel worker threads for stem encoding (default: 4).
            quality: Vorbis compression quality (0.0 to 1.0, default: 0.5 ~160 kbps).

        Returns:
            Dictionary mapping stem filenames to encoded Ogg Vorbis bytes:
            {'drums.ogg': bytes, 'guitar.ogg': bytes, 'rhythm.ogg': bytes, 'vocals.ogg': bytes, 'song.ogg': bytes}
        """
        # Step 1: In-memory decode of multi-channel Vorbis stream
        audio_pcm, sample_rate = decode_mogg_pcm(mogg_data)
        total_channels = audio_pcm.shape[1]

        # Step 2: Resolve canonical stem channel assignments
        stem_channel_map: Dict[str, List[int]] = {}
        assigned_channels: set[int] = set()

        for track_name, channels in channel_mapping.items():
            if not channels:
                continue
            stem_name = canonical_stem_name(track_name)
            ch_list = [int(c) for c in channels]
            stem_channel_map[stem_name] = ch_list
            assigned_channels.update(ch_list)

        # Step 3: Route unassigned channels to backing track 'song.ogg'
        all_channels = set(range(total_channels))
        unassigned = sorted(all_channels - assigned_channels)

        if "song.ogg" not in stem_channel_map and unassigned:
            stem_channel_map["song.ogg"] = unassigned

        # If no stems were mapped at all, route all channels to song.ogg
        if not stem_channel_map:
            stem_channel_map["song.ogg"] = list(range(total_channels))

        # Step 4: Extract and mix PCM buffers for each stem
        stem_pcm_buffers: Dict[str, np.ndarray] = {}
        for stem_name, ch_indices in stem_channel_map.items():
            mixed_audio = mix_and_slice_channels(
                audio_data=audio_pcm,
                channel_indices=ch_indices,
                pans=pans,
                vols=vols,
            )
            stem_pcm_buffers[stem_name] = mixed_audio

        # Step 5: Parallel multi-threaded Vorbis encoding
        def _encode_worker(item: Tuple[str, np.ndarray]) -> Tuple[str, bytes]:
            name, pcm_arr = item
            encoded_bytes = encode_vorbis_stem(pcm_arr, sample_rate, quality=quality)
            return name, encoded_bytes

        worker_count = max(1, min(num_threads, len(stem_pcm_buffers)))
        results: Dict[str, bytes] = {}

        if worker_count > 1 and len(stem_pcm_buffers) > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                encoded_items = list(executor.map(_encode_worker, stem_pcm_buffers.items()))
                for stem_name, stem_bytes in encoded_items:
                    results[stem_name] = stem_bytes
        else:
            for item in stem_pcm_buffers.items():
                stem_name, stem_bytes = _encode_worker(item)
                results[stem_name] = stem_bytes

        return results

    @classmethod
    def demux_to_stem_objects(
        cls,
        mogg_data: Union[bytes, bytearray, memoryview],
        channel_mapping: Dict[str, Sequence[int]],
        pans: Optional[Sequence[float]] = None,
        vols: Optional[Sequence[float]] = None,
        num_threads: int = 4,
        quality: float = 0.5,
    ) -> Dict[str, AudioStem]:
        """
        Demux MOGG audio and return rich AudioStem metadata objects.

        Args:
            mogg_data: Raw MOGG bytes or memoryview.
            channel_mapping: Mapping of track names to channel indices.
            pans: Optional channel pans.
            vols: Optional channel volumes.
            num_threads: Thread pool worker count.
            quality: Vorbis quality.

        Returns:
            Dictionary of {stem_filename: AudioStem}.
        """
        audio_pcm, sample_rate = decode_mogg_pcm(mogg_data)
        total_channels = audio_pcm.shape[1]
        num_samples = audio_pcm.shape[0]
        duration_sec = num_samples / float(sample_rate) if sample_rate > 0 else 0.0

        encoded_stems = cls.demux_to_stems(
            mogg_data=mogg_data,
            channel_mapping=channel_mapping,
            pans=pans,
            vols=vols,
            num_threads=num_threads,
            quality=quality,
        )

        stem_objects: Dict[str, AudioStem] = {}
        for stem_name, stem_bytes in encoded_stems.items():
            # Determine channel count from decoded stem
            stem_info = sf.info(io.BytesIO(stem_bytes))
            ch_count = stem_info.channels

            # Find matching channels from mapping
            orig_channels: List[int] = []
            for trk, chs in channel_mapping.items():
                if canonical_stem_name(trk) == stem_name:
                    orig_channels = list(chs)
                    break
            if not orig_channels and stem_name == "song.ogg":
                assigned = {c for chs in channel_mapping.values() for c in chs}
                orig_channels = sorted(set(range(total_channels)) - assigned)

            stem_objects[stem_name] = AudioStem(
                filename=stem_name,
                channels=orig_channels,
                data=stem_bytes,
                sample_rate=sample_rate,
                num_channels=ch_count,
                num_samples=num_samples,
                duration_seconds=duration_sec,
            )

        return stem_objects

    @classmethod
    def create_mogg_container(
        cls,
        vorbis_ogg_bytes: bytes,
        header_type: int = 10,
        vorbis_offset: int = 2900,
    ) -> bytes:
        """
        Package raw Ogg Vorbis bytes into a valid Harmonix MOGG container format.

        Useful for test fixture generation, synthetic benchmarks, and reverse conversions.

        Args:
            vorbis_ogg_bytes: Standard Ogg Vorbis bitstream bytes starting with 'OggS'.
            header_type: MOGG header type (default: 10).
            vorbis_offset: Byte offset where Vorbis bitstream is placed (default: 2900).

        Returns:
            Complete binary MOGG file bytes.
        """
        if vorbis_offset < 8:
            raise ValueError(f"vorbis_offset ({vorbis_offset}) must be at least 8 bytes.")

        header_buf = bytearray(vorbis_offset)
        struct.pack_into("<II", header_buf, 0, header_type, vorbis_offset)
        return bytes(header_buf) + vorbis_ogg_bytes
