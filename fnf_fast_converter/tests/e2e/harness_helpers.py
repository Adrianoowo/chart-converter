"""
High-Speed Native Rock Band CON to Clone Hero Converter
E2E Test Harness Helpers, Synthetic Data Generators & Reference Oracles
"""

import io
import math
import os
import re
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import soundfile as sf
from PIL import Image


# ============================================================================
# 1. SYNTHETIC STFS (CON) ARCHIVE BUILDER
# ============================================================================

def build_synthetic_stfs(
    files: Dict[str, bytes],
    magic: bytes = b'CON ',
    volume_descriptor_override: Optional[bytes] = None,
) -> bytes:
    """
    Constructs a fully valid in-memory Xbox 360 STFS CON archive containing the
    provided hierarchy of files.
    """
    if len(magic) != 4:
        raise ValueError(f"Magic must be 4 bytes, got {magic!r}")

    # STFS Header layout: 0xB000 bytes base offset
    hdr_size = 0xAD0E
    base_offset = (hdr_size + 0xFFF) & ~0xFFF  # 0xB000 (45056 bytes)

    # 1. Build directory tree entries
    all_paths = sorted(files.keys())
    dir_set = set()
    for p in all_paths:
        parts = p.strip("/").split("/")
        for i in range(1, len(parts)):
            dir_set.add("/".join(parts[:i]))

    sorted_dirs = sorted(list(dir_set))
    dir_indices: Dict[str, int] = {}
    entries: List[Dict[str, Any]] = []

    # Add directories first
    for d in sorted_dirs:
        parts = d.split("/")
        name = parts[-1]
        parent_path = "/".join(parts[:-1])
        parent_idx = dir_indices[parent_path] if parent_path in dir_indices else 0xFFFF
        idx = len(entries)
        dir_indices[d] = idx
        entries.append({
            "name": name,
            "is_dir": True,
            "parent_idx": parent_idx,
            "data": b"",
            "size": 0,
            "start_block": 0,
            "block_count": 0,
        })

    # Add files
    for p in all_paths:
        data = files[p]
        parts = p.strip("/").split("/")
        name = parts[-1]
        parent_path = "/".join(parts[:-1])
        parent_idx = dir_indices[parent_path] if parent_path in dir_indices else 0xFFFF
        entries.append({
            "name": name,
            "is_dir": False,
            "parent_idx": parent_idx,
            "data": data,
            "size": len(data),
            "start_block": 0,
            "block_count": (len(data) + 4095) // 4096 if len(data) > 0 else 0,
        })

    # Directory table bytes
    dir_table_bytes = bytearray()
    for e in entries:
        chunk = bytearray(64)
        name_bytes = e["name"].encode("ascii", errors="replace")[:40]
        chunk[0:len(name_bytes)] = name_bytes
        name_len = len(name_bytes) & 0x3F
        flags = (0x80 if e["is_dir"] else 0x00) | name_len
        chunk[0x28] = flags
        chunk[0x32:0x34] = struct.pack(">H", e["parent_idx"])
        chunk[0x34:0x38] = struct.pack(">I", e["size"])
        dir_table_bytes.extend(chunk)

    # Pad directory table to 4KB
    dir_block_count = (len(dir_table_bytes) + 4095) // 4096
    if dir_block_count == 0:
        dir_block_count = 1
    dir_table_bytes.extend(b'\x00' * (dir_block_count * 4096 - len(dir_table_bytes)))

    # Allocate logical blocks:
    current_block = dir_block_count
    file_block_allocations: Dict[int, List[int]] = {}
    for idx, e in enumerate(entries):
        if e["is_dir"] or e["size"] == 0:
            e["start_block"] = 0xFFFFFF if e["size"] == 0 and not e["is_dir"] else 0
            file_block_allocations[idx] = []
            continue
        
        needed = (e["size"] + 4095) // 4096
        e["start_block"] = current_block
        e["block_count"] = needed
        allocated = list(range(current_block, current_block + needed))
        file_block_allocations[idx] = allocated
        current_block += needed

    total_allocated_blocks = current_block

    # Update directory entries with start blocks and block counts
    for idx, e in enumerate(entries):
        entry_offset = idx * 64
        sb = e["start_block"]
        bc = e["block_count"]
        dir_table_bytes[entry_offset + 0x29] = bc & 0xFF
        dir_table_bytes[entry_offset + 0x2A] = (bc >> 8) & 0xFF
        dir_table_bytes[entry_offset + 0x2B] = (bc >> 16) & 0xFF
        dir_table_bytes[entry_offset + 0x2C] = bc & 0xFF
        dir_table_bytes[entry_offset + 0x2D] = (bc >> 8) & 0xFF
        dir_table_bytes[entry_offset + 0x2E] = (bc >> 16) & 0xFF
        dir_table_bytes[entry_offset + 0x2F] = sb & 0xFF
        dir_table_bytes[entry_offset + 0x30] = (sb >> 8) & 0xFF
        dir_table_bytes[entry_offset + 0x31] = (sb >> 16) & 0xFF

    # Map logical blocks to data payloads
    logical_blocks: Dict[int, bytes] = {}
    for b in range(dir_block_count):
        logical_blocks[b] = bytes(dir_table_bytes[b * 4096 : (b + 1) * 4096])

    for idx, e in enumerate(entries):
        if e["is_dir"] or e["size"] == 0:
            continue
        data = e["data"]
        alloc = file_block_allocations[idx]
        for i, b_idx in enumerate(alloc):
            chunk = data[i * 4096 : (i + 1) * 4096]
            if len(chunk) < 4096:
                chunk = chunk + b'\x00' * (4096 - len(chunk))
            logical_blocks[b_idx] = chunk

    # Block chain pointers
    next_pointers: Dict[int, int] = {}
    for b in range(dir_block_count - 1):
        next_pointers[b] = b + 1
    if dir_block_count > 0:
        next_pointers[dir_block_count - 1] = 0x00FFFFFF

    for idx, alloc in file_block_allocations.items():
        for i in range(len(alloc) - 1):
            next_pointers[alloc[i]] = alloc[i + 1]
        if len(alloc) > 0:
            next_pointers[alloc[-1]] = 0x00FFFFFF

    def get_phys_block(b_idx: int) -> int:
        num_l0 = (b_idx // 170) + 1
        num_l1 = (b_idx // 28900) + 1 if b_idx >= 170 else 0
        num_l2 = (b_idx // 4913000) + 1 if b_idx >= 28900 else 0
        return b_idx + num_l0 + num_l1 + num_l2

    def get_table_phys_block(t_idx: int) -> int:
        num_l0 = t_idx
        num_l1 = (t_idx // 170) + 1 if t_idx >= 1 else 0
        num_l2 = (t_idx // 28900) + 1 if t_idx >= 1 else 0
        return (t_idx * 170) + num_l0 + num_l1 + num_l2

    max_phys_block = 0
    for b_idx in range(total_allocated_blocks):
        pb = get_phys_block(b_idx)
        if pb > max_phys_block:
            max_phys_block = pb

    num_tables = (total_allocated_blocks + 169) // 170
    for t_idx in range(num_tables):
        tpb = get_table_phys_block(t_idx)
        if tpb > max_phys_block:
            max_phys_block = tpb

    total_file_bytes = base_offset + (max_phys_block + 1) * 4096
    out_buf = bytearray(total_file_bytes)

    # 1. Write Header
    out_buf[0:4] = magic
    out_buf[0x340:0x344] = struct.pack(">I", hdr_size)
    out_buf[0x344:0x348] = struct.pack(">I", 0x00000002)

    if volume_descriptor_override:
        out_buf[0x379 : 0x379 + len(volume_descriptor_override)] = volume_descriptor_override
    else:
        vol_desc = bytearray(0x24)
        vol_desc[0] = 0x24
        vol_desc[1] = 0x00
        vol_desc[2] = 0x01
        vol_desc[3:5] = struct.pack(">H", dir_block_count)
        vol_desc[5:8] = b'\x00\x00\x00'
        vol_desc[28:32] = struct.pack(">I", total_allocated_blocks)
        out_buf[0x379:0x379 + 0x24] = vol_desc

    # 2. Write Level 0 Hash Tables
    for t_idx in range(num_tables):
        tpb = get_table_phys_block(t_idx)
        t_off = base_offset + tpb * 4096
        table_bytes = bytearray(4096)
        
        start_b = t_idx * 170
        end_b = min(start_b + 170, total_allocated_blocks)
        for b_idx in range(start_b, end_b):
            entry_offset = (b_idx % 170) * 24
            next_b = next_pointers.get(b_idx, 0x00FFFFFF)
            table_bytes[entry_offset + 20] = 0x80
            table_bytes[entry_offset + 21] = (next_b >> 16) & 0xFF
            table_bytes[entry_offset + 22] = (next_b >> 8) & 0xFF
            table_bytes[entry_offset + 23] = next_b & 0xFF
        out_buf[t_off : t_off + 4096] = table_bytes

    # 3. Write Data Blocks
    for b_idx, block_data in logical_blocks.items():
        pb = get_phys_block(b_idx)
        p_off = base_offset + pb * 4096
        out_buf[p_off : p_off + 4096] = block_data

    return bytes(out_buf)


# ============================================================================
# 2. SYNTHETIC MOGG AUDIO BUILDER
# ============================================================================

def build_synthetic_mogg(
    channels: int = 10,
    sample_rate: int = 44100,
    duration_seconds: float = 0.5,
    mogg_type: int = 10,
    ogg_offset: int = 2900,
) -> bytes:
    """
    Generates a valid Harmonix MOGG container with specified number of channels
    and valid Vorbis bitstream at specified offset.
    """
    num_samples = int(sample_rate * duration_seconds)
    if num_samples < 100:
        num_samples = 100
    t = np.linspace(0, duration_seconds, num_samples, endpoint=False, dtype=np.float32)
    
    pcm_data = np.zeros((num_samples, channels), dtype=np.float32)
    for c in range(channels):
        freq = 220.0 + c * 55.0
        pcm_data[:, c] = 0.2 * np.sin(2.0 * np.pi * freq * t)

    bio = io.BytesIO()
    sf.write(bio, pcm_data, sample_rate, format='OGG', subtype='VORBIS')
    ogg_bytes = bio.getvalue()

    mogg_header = bytearray(ogg_offset)
    mogg_header[0:4] = struct.pack("<I", mogg_type)
    mogg_header[4:8] = struct.pack("<I", ogg_offset)
    
    return bytes(mogg_header) + ogg_bytes


# ============================================================================
# 3. SYNTHETIC MILO DXT1 IMAGE BUILDER
# ============================================================================

def build_synthetic_png_xbox(
    width: int = 256,
    height: int = 256,
    color_rgb: Tuple[int, int, int] = (255, 0, 0),
) -> bytes:
    """
    Generates a valid 32-byte Milo texture header + 16-bit byte-swapped DXT1
    texture payload.
    """
    header = bytearray(32)
    header[0:2] = struct.pack("<H", 1)
    header[2:4] = struct.pack("<H", 4)
    header[4:8] = struct.pack("<I", 8)
    header[8:10] = struct.pack("<H", width)
    header[10:12] = struct.pack("<H", height)
    header[12:16] = struct.pack("<I", 1)

    r, g, b = color_rgb
    r5 = (r * 31) // 255
    g6 = (g * 63) // 255
    b5 = (b * 31) // 255
    rgb565 = (r5 << 11) | (g6 << 5) | b5

    block = bytearray(8)
    block[0:2] = struct.pack("<H", rgb565)
    block[2:4] = struct.pack("<H", rgb565)
    block[4:8] = struct.pack("<I", 0x00000000)

    num_blocks = (width // 4) * (height // 4)
    raw_payload = bytearray(block * num_blocks)

    for i in range(0, len(raw_payload) - 1, 2):
        raw_payload[i], raw_payload[i + 1] = raw_payload[i + 1], raw_payload[i]

    return bytes(header) + bytes(raw_payload)


# ============================================================================
# 4. SYNTHETIC MIDI CHART BUILDER
# ============================================================================

def build_synthetic_midi(
    track_names: Optional[List[str]] = None,
    ticks_per_beat: int = 480,
) -> bytes:
    """
    Generates a valid Type-1 MIDI file with standard Rock Band tracks.
    """
    if track_names is None:
        track_names = ["PART DRUMS", "PART GUITAR", "PART BASS", "PART VOCALS", "BEAT", "EVENTS"]

    tracks_data: List[bytes] = []

    bio_tempo = io.BytesIO()
    bio_tempo.write(b'\x00\xFF\x51\x03\x07\xA1\x20')
    bio_tempo.write(b'\x00\xFF\x58\x04\x04\x02\x18\x08')
    bio_tempo.write(b'\x00\xFF\x2F\x00')
    tracks_data.append(bio_tempo.getvalue())

    for t_name in track_names:
        bio = io.BytesIO()
        name_bytes = t_name.encode("ascii")
        bio.write(b'\x00\xFF\x03' + bytes([len(name_bytes)]) + name_bytes)
        bio.write(b'\x00\x90\x60\x64')
        bio.write(b'\x83\x60\x80\x60\x00')
        bio.write(b'\x00\xFF\x2F\x00')
        tracks_data.append(bio.getvalue())

    header = struct.pack(">4sIHHH", b"MThd", 6, 1, len(tracks_data), ticks_per_beat)

    out = bytearray(header)
    for td in tracks_data:
        chunk_hdr = struct.pack(">4sI", b"MTrk", len(td))
        out.extend(chunk_hdr)
        out.extend(td)

    return bytes(out)


# ============================================================================
# 5. SYNTHETIC DTA METADATA BUILDER
# ============================================================================

def build_synthetic_dta(
    song_id: str = "testsong",
    name: str = "Test Song",
    artist: str = "Test Artist",
    album_name: str = "Test Album",
    year_released: int = 2024,
    charter: str = "Dansla116",
    genre: str = "rock",
    song_length: int = 120000,
    preview: Tuple[int, int] = (10000, 40000),
    ranks: Optional[Dict[str, int]] = None,
    tracks: Optional[Dict[str, List[int]]] = None,
    pans: Optional[List[float]] = None,
    vols: Optional[List[float]] = None,
    vocal_parts: int = 1,
    album_track_number: int = 1,
    extra_dta_blocks: str = "",
    use_bom: bool = False,
) -> bytes:
    """
    Synthesizes standard Harmonix LISP songs.dta content.
    """
    if ranks is None:
        ranks = {"guitar": 200, "bass": 180, "drum": 220, "vocals": 190, "band": 210}
    if tracks is None:
        tracks = {
            "drum": [0, 1],
            "bass": [2, 3],
            "guitar": [4, 5],
            "vocals": [6, 7],
        }
    if pans is None:
        pans = [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0]
    if vols is None:
        vols = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    ranks_str = " ".join([f"({k} {v})" for k, v in ranks.items()])
    
    tracks_inner = []
    for k, chs in tracks.items():
        ch_str = " ".join(str(c) for c in chs)
        tracks_inner.append(f"({k} ({ch_str}))")
    tracks_str = " ".join(tracks_inner)

    pans_str = " ".join(f"{p:.1f}" for p in pans)
    vols_str = " ".join(f"{v:.1f}" for v in vols)

    content = f"""({song_id}
   (name "{name.replace('"', '\\"')}")
   (artist "{artist.replace('"', '\\"')}")
   (album_name "{album_name.replace('"', '\\"')}")
   (year_released {year_released})
   (author "{charter.replace('"', '\\"')}")
   (genre {genre})
   (song_length {song_length})
   (preview {preview[0]} {preview[1]})
   (album_track_number {album_track_number})
   (vocal_parts {vocal_parts})
   (song
      (name "songs/{song_id}/{song_id}")
      (tracks ({tracks_str}))
      (pans ({pans_str}))
      (vols ({vols_str}))
      (cores (-1 -1 -1 -1 1 1 -1 -1 -1 -1))
   )
   (rank ({ranks_str}))
   (format 4)
   {extra_dta_blocks}
)
"""
    raw = content.encode("utf-8")
    if use_bom:
        raw = b'\xEF\xBB\xBF' + raw
    return raw


# ============================================================================
# 6. COMPLETE SYNTHETIC CON PACKAGE GENERATOR
# ============================================================================

def build_complete_test_con(
    song_id: str = "buddyhollyfnf",
    title: str = "Buddy Holly",
    artist: str = "Weezer",
    album: str = "Weezer (The Blue Album)",
    year: int = 1994,
    genre: str = "alternative",
    ranks: Optional[Dict[str, int]] = None,
    audio_channels: int = 10,
    include_cover: bool = True,
    use_bom: bool = False,
    extra_dta: str = "",
) -> bytes:
    """
    Creates a full in-memory CON package containing songs.dta, .mid, .mogg, and .png_xbox.
    """
    dta_bytes = build_synthetic_dta(
        song_id=song_id,
        name=title,
        artist=artist,
        album_name=album,
        year_released=year,
        genre=genre,
        ranks=ranks,
        use_bom=use_bom,
        extra_dta_blocks=extra_dta,
    )
    mid_bytes = build_synthetic_midi()
    mogg_bytes = build_synthetic_mogg(channels=audio_channels, duration_seconds=0.4)
    
    files_map = {
        "songs/songs.dta": dta_bytes,
        f"songs/{song_id}/{song_id}.mid": mid_bytes,
        f"songs/{song_id}/{song_id}.mogg": mogg_bytes,
    }

    if include_cover:
        img_bytes = build_synthetic_png_xbox(256, 256, (0, 128, 255))
        files_map[f"songs/{song_id}/gen/{song_id}_keep.png_xbox"] = img_bytes

    return build_synthetic_stfs(files_map)


# ============================================================================
# 7. REFERENCE ORACLES & PARSERS
# ============================================================================

DIFFICULTY_CUTOFFS = {
    "guitar": [139, 176, 221, 267, 333, 409],
    "bass": [135, 181, 228, 293, 364, 436],
    "drum": [124, 151, 178, 242, 345, 448],
    "drums": [124, 151, 178, 242, 345, 448],
    "vocals": [132, 175, 218, 279, 353, 427],
    "band": [163, 215, 243, 267, 292, 345],
    "keys": [153, 211, 269, 327, 385, 443],
}

GENRE_MAP = {
    "alternative": "Alternative",
    "blues": "Blues",
    "classicrock": "Classic Rock",
    "classic_rock": "Classic Rock",
    "classical": "Classical",
    "country": "Country",
    "emo": "Emo",
    "glam": "Glam",
    "grunge": "Grunge",
    "hiphoprap": "Hip-Hop/Rap",
    "hiphop": "Hip-Hop/Rap",
    "rap": "Hip-Hop/Rap",
    "urban": "Hip-Hop/Rap",
    "indierock": "Indie Rock",
    "indie_rock": "Indie Rock",
    "indie": "Indie Rock",
    "jrock": "J-Rock",
    "j_rock": "J-Rock",
    "jazz": "Jazz",
    "latin": "Latin",
    "metal": "Metal",
    "new_wave": "New Wave",
    "newwave": "New Wave",
    "novelty": "Novelty",
    "numetal": "Nu-Metal",
    "nu_metal": "Nu-Metal",
    "poprock": "Pop-Rock",
    "pop_rock": "Pop-Rock",
    "popdanceelectronic": "Pop/Dance/Electronic",
    "dance": "Pop/Dance/Electronic",
    "electronic": "Pop/Dance/Electronic",
    "prog": "Prog",
    "punk": "Punk",
    "rbsoulfunk": "R&B/Soul/Funk",
    "rb": "R&B/Soul/Funk",
    "soul": "R&B/Soul/Funk",
    "funk": "R&B/Soul/Funk",
    "reggaeska": "Reggae/Ska",
    "reggae": "Reggae/Ska",
    "ska": "Reggae/Ska",
    "rock": "Rock",
    "southernrock": "Southern Rock",
    "southern_rock": "Southern Rock",
}


def calculate_expected_diff_tier(rank_val: Optional[int], instrument: str) -> int:
    """Calculates ground-truth Clone Hero 0-6 difficulty tier from RB rank (0-500)."""
    if rank_val is None or rank_val <= 0:
        return -1
    if rank_val == 1:
        return 0
    cutoffs = DIFFICULTY_CUTOFFS.get(instrument.lower())
    if not cutoffs:
        return 0
    for tier, cutoff in enumerate(cutoffs):
        if rank_val < cutoff:
            return tier
    return 6


class ReferenceSTFSPackage:
    """Reference STFS CON archive parser."""
    def __init__(self, data: bytes):
        self.data = data
        self.magic = data[:4]
        if self.magic not in (b'CON ', b'LIVE', b'PIRS'):
            raise ValueError(f"Invalid STFS magic: {self.magic}")
        
        self.hdr_size = struct.unpack('>I', data[0x340:0x344])[0]
        self.base_offset = (self.hdr_size + 0xFFF) & ~0xFFF
        
        vol_desc = data[0x379:0x379+0x24]
        self.ft_block_count = struct.unpack('>H', vol_desc[3:5])[0]
        self.ft_block_num = (vol_desc[5] << 16) | (vol_desc[6] << 8) | vol_desc[7]
        self.total_alloc = struct.unpack('>I', vol_desc[28:32])[0]
        
        self.files: Dict[str, bytes] = {}
        self._parse_directory()

    @classmethod
    def from_bytes(cls, data: Union[bytes, bytearray, memoryview]) -> "ReferenceSTFSPackage":
        return cls(bytes(data))

    @classmethod
    def from_file(cls, file_path: Union[str, Path]) -> "ReferenceSTFSPackage":
        return cls(Path(file_path).read_bytes())

    def _get_physical_offset(self, block_idx: int) -> int:
        num_l0 = (block_idx // 170) + 1
        num_l1 = (block_idx // (170 * 170)) + 1 if block_idx >= 170 else 0
        num_l2 = (block_idx // (170 * 170 * 170)) + 1 if block_idx >= (170 * 170) else 0
        phys_block = block_idx + num_l0 + num_l1 + num_l2
        return self.base_offset + (phys_block * 4096)

    def _get_next_block(self, block_idx: int) -> int:
        table_idx = block_idx // 170
        entry_idx = block_idx % 170
        
        num_l0 = table_idx
        num_l1 = (table_idx // 170) + 1 if table_idx >= 1 else 0
        num_l2 = (table_idx // (170 * 170)) + 1 if table_idx >= 1 else 0
        table_phys_block = (table_idx * 170) + num_l0 + num_l1 + num_l2
        
        table_offset = self.base_offset + (table_phys_block * 4096)
        entry_offset = table_offset + (entry_idx * 24)
        if entry_offset + 24 > len(self.data):
            return 0x00FFFFFF
        b0 = self.data[entry_offset + 21]
        b1 = self.data[entry_offset + 22]
        b2 = self.data[entry_offset + 23]
        return (b0 << 16) | (b1 << 8) | b2

    def _parse_directory(self):
        dir_data = bytearray()
        curr_block = self.ft_block_num
        visited = set()
        while curr_block != 0x00FFFFFF and curr_block < self.total_alloc and curr_block not in visited:
            visited.add(curr_block)
            off = self._get_physical_offset(curr_block)
            if off + 4096 > len(self.data):
                break
            dir_data.extend(self.data[off:off+4096])
            curr_block = self._get_next_block(curr_block)
            
        entries = []
        for i in range(0, len(dir_data), 64):
            chunk = dir_data[i:i+64]
            if len(chunk) < 64 or chunk[0] == 0:
                continue
            name_len_flag = chunk[0x28]
            name_len = name_len_flag & 0x3F
            is_dir = bool(name_len_flag & 0x80)
            name = chunk[:name_len].decode('ascii', errors='replace')
            first_block = chunk[0x2F] | (chunk[0x30] << 8) | (chunk[0x31] << 16)
            parent_idx = struct.unpack('>H', chunk[0x32:0x34])[0]
            file_size = struct.unpack('>I', chunk[0x34:0x38])[0]
            
            entries.append({
                'name': name,
                'is_dir': is_dir,
                'first_block': first_block,
                'parent_idx': parent_idx,
                'file_size': file_size,
            })
            
        for e in entries:
            path_parts = [e['name']]
            p = e['parent_idx']
            p_visited = set()
            while p != 0xFFFF and p < len(entries) and p not in p_visited:
                p_visited.add(p)
                path_parts.append(entries[p]['name'])
                p = entries[p]['parent_idx']
            e['full_path'] = '/'.join(reversed(path_parts))
            
        for e in entries:
            if e['is_dir']:
                continue
            if e['file_size'] == 0:
                self.files[e['full_path']] = b""
                continue
            fbytes = bytearray()
            cur_blk = e['first_block']
            bytes_left = e['file_size']
            b_visited = set()
            while cur_blk != 0x00FFFFFF and bytes_left > 0 and cur_blk not in b_visited:
                b_visited.add(cur_blk)
                off = self._get_physical_offset(cur_blk)
                chunk_sz = min(4096, bytes_left)
                if off + chunk_sz > len(self.data):
                    break
                fbytes.extend(self.data[off:off+chunk_sz])
                bytes_left -= chunk_sz
                cur_blk = self._get_next_block(cur_blk)
            self.files[e['full_path']] = bytes(fbytes)

    def list_files(self) -> List[str]:
        return list(self.files.keys())

    def get_file_bytes(self, path: str) -> bytes:
        norm = path.replace("\\", "/").lstrip("/")
        for k, v in self.files.items():
            if k.replace("\\", "/").lstrip("/") == norm:
                return v
        raise FileNotFoundError(f"File '{path}' not found in STFS package")

    def extract_all_memory(self) -> Dict[str, bytes]:
        return dict(self.files)


def reference_parse_dta(dta_content: Union[str, bytes]) -> Dict[str, Any]:
    """Reference DTA S-expression parser."""
    if isinstance(dta_content, bytes):
        if dta_content.startswith(b'\xef\xbb\xbf'):
            dta_content = dta_content[3:]
        try:
            text = dta_content.decode('utf-8')
        except UnicodeDecodeError:
            text = dta_content.decode('latin-1')
    else:
        text = dta_content
        if text.startswith('\ufeff') or text.startswith('\xef\xbb\xbf'):
            text = text.lstrip('\ufeff\xef\xbb\xbf')

    # Strip inline comments
    clean_lines = []
    for line in text.splitlines():
        in_str = False
        res = []
        for ch in line:
            if ch == '"':
                in_str = not in_str
            if ch == ';' and not in_str:
                break
            res.append(ch)
        clean_lines.append("".join(res))
    text = "\n".join(clean_lines)

    # Tokenizer
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in '()':
            tokens.append(c)
            i += 1
            continue
        if c == '{':
            depth = 1
            i += 1
            expr = []
            while i < n and depth > 0:
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                if depth > 0:
                    expr.append(text[i])
                i += 1
            tokens.append("{" + "".join(expr) + "}")
            continue
        if c == '"':
            i += 1
            str_val = []
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n:
                    i += 1
                    str_val.append(text[i])
                else:
                    str_val.append(text[i])
                i += 1
            if i < n:
                i += 1
            tokens.append("".join(str_val))
            continue
        tok = []
        while i < n and not text[i].isspace() and text[i] not in '(){}':
            tok.append(text[i])
            i += 1
        val = "".join(tok)
        tokens.append(val)

    def parse_sexpr(tok_iter):
        res = []
        for tok in tok_iter:
            if tok == '(':
                res.append(parse_sexpr(tok_iter))
            elif tok == ')':
                return res
            else:
                res.append(tok)
        return res

    ast = parse_sexpr(iter(tokens))
    if not ast:
        return {}
    
    root = ast[0] if isinstance(ast[0], list) else ast
    song_dict: Dict[str, Any] = {}

    # Reconstruct top-level key-values from AST
    if isinstance(root, list):
        for item in root:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
                key = item[0].lower()
                val = item[1:] if len(item) > 2 else item[1]
                if key != "song":  # Keep top-level keys
                    song_dict[key] = val

    # If song block exists, extract its tracks/pans/vols separately
    def find_block(node, block_name):
        if not isinstance(node, list):
            return None
        for item in node:
            if isinstance(item, list) and len(item) > 0 and item[0] == block_name:
                return item
            res = find_block(item, block_name)
            if res is not None:
                return res
        return None

    song_block = find_block(root, "song")
    if song_block and isinstance(song_block, list):
        for item in song_block:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
                k = item[0].lower()
                if k in ("tracks", "pans", "vols", "cores"):
                    song_dict[k] = item[1:] if len(item) > 2 else item[1]

    # Process ranks
    ranks_dict = {}
    if "rank" in song_dict:
        r_node = song_dict["rank"]
        if isinstance(r_node, list):
            for item in r_node:
                if isinstance(item, list) and len(item) == 2:
                    k, v = item
                    try:
                        ranks_dict[str(k).lower()] = int(v)
                    except ValueError:
                        pass
    song_dict["ranks"] = ranks_dict

    # Process tracks
    tracks_dict = {}
    if "tracks" in song_dict:
        t_node = song_dict["tracks"]
        if isinstance(t_node, list):
            for item in t_node:
                if isinstance(item, list) and len(item) >= 2:
                    k = str(item[0]).lower()
                    ch_list = item[1] if isinstance(item[1], list) else item[1:]
                    tracks_dict[k] = [int(x) for x in ch_list if str(x).isdigit()]
    song_dict["track_mapping"] = tracks_dict

    return song_dict


def reference_generate_song_ini(dta_data: Dict[str, Any], charter: str = "Dansla116") -> str:
    """Generates standard Clone Hero song.ini with mandatory icon = fnf."""
    title = dta_data.get("name", "Unknown Title")
    artist = dta_data.get("artist", "Unknown Artist")
    album = dta_data.get("album_name", "")
    author = dta_data.get("author", charter)
    year = dta_data.get("year_released", 0)
    genre_sym = str(dta_data.get("genre", "rock")).lower()
    genre = GENRE_MAP.get(genre_sym, genre_sym.title())
    song_length = dta_data.get("song_length", 0)

    preview = dta_data.get("preview", [0, 0])
    p_start = preview[0] if isinstance(preview, list) and len(preview) > 0 else 0
    p_end = preview[1] if isinstance(preview, list) and len(preview) > 1 else 0

    ranks = dta_data.get("ranks", {})
    diff_band = calculate_expected_diff_tier(ranks.get("band"), "band")
    diff_guitar = calculate_expected_diff_tier(ranks.get("guitar"), "guitar")
    diff_bass = calculate_expected_diff_tier(ranks.get("bass"), "bass")
    diff_drums = calculate_expected_diff_tier(ranks.get("drum") or ranks.get("drums"), "drums")
    diff_vocals = calculate_expected_diff_tier(ranks.get("vocals"), "vocals")
    diff_keys = calculate_expected_diff_tier(ranks.get("keys"), "keys")
    diff_keys_real = calculate_expected_diff_tier(ranks.get("real_keys") or ranks.get("keys"), "keys")

    vocal_parts = int(dta_data.get("vocal_parts", 1) or 1)
    diff_vocals_harm = diff_vocals if vocal_parts > 1 else -1

    track_num = dta_data.get("album_track_number", "")

    lines = [
        "[song]",
        "icon = fnf",
        f"name = {title}",
        f"artist = {artist}",
        f"album = {album}",
        f"charter = {author}",
        f"frets = {author}",
        f"year = {year}",
        f"genre = {genre}",
        "pro_drums = True",
        f"song_length = {song_length}",
        f"preview_start_time = {p_start}",
        f"preview_end_time = {p_end}",
        f"diff_band = {diff_band}",
        f"diff_guitar = {diff_guitar}",
        "diff_guitarghl = -1",
        f"diff_bass = {diff_bass}",
        "diff_bassghl = -1",
        f"diff_drums = {diff_drums}",
        f"diff_drums_real = {diff_drums}",
        f"diff_keys = {diff_keys}",
        f"diff_keys_real = {diff_keys_real}",
        f"diff_vocals = {diff_vocals}",
        f"diff_vocals_harm = {diff_vocals_harm}",
        "diff_dance = -1",
        "diff_bass_real = -1",
        "diff_guitar_real = -1",
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
    if track_num != "":
        lines.append(f"track = {track_num}")
        lines.append(f"album_track = {track_num}")
    lines.append("sysex_slider = False")
    lines.append("sysex_open_bass = False")
    lines.append("")

    return "\r\n".join(lines)


def reference_decode_png_xbox(png_xbox_data: bytes) -> bytes:
    """Decodes Milo 32-byte header + 16-bit byte-swapped DXT1 texture into PNG bytes."""
    if len(png_xbox_data) < 32:
        raise ValueError("Invalid png_xbox data: too short")
    
    width = 256
    height = 256
    if len(png_xbox_data) >= 12:
        # Milo headers store width/height in Big-Endian at 0x08 with pitch flags
        w_be, h_be = struct.unpack(">HH", png_xbox_data[8:12])
        w_clean = w_be & 0x07FF
        h_clean = h_be & 0x07FF
        if w_clean in (64, 128, 256, 512, 1024) and h_clean in (64, 128, 256, 512, 1024):
            width, height = w_clean, h_clean
        else:
            w_le, h_le = struct.unpack("<HH", png_xbox_data[8:12])
            if w_le in (64, 128, 256, 512) and h_le in (64, 128, 256, 512):
                width, height = w_le, h_le

    payload = png_xbox_data[32 : 32 + (width * height // 2)]
    swapped = bytearray(payload)
    for i in range(0, len(swapped) - 1, 2):
        swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]

    img = Image.new("RGB", (width, height))
    w_blocks = width // 4
    h_blocks = height // 4

    for by in range(h_blocks):
        for bx in range(w_blocks):
            b_off = (by * w_blocks + bx) * 8
            block = swapped[b_off : b_off + 8]
            if len(block) < 8:
                continue

            c0, c1 = struct.unpack("<HH", block[:4])
            r0 = ((c0 >> 11) & 0x1F) * 255 // 31
            g0 = ((c0 >> 5) & 0x3F) * 255 // 63
            b0 = (c0 & 0x1F) * 255 // 31

            r1 = ((c1 >> 11) & 0x1F) * 255 // 31
            g1 = ((c1 >> 5) & 0x3F) * 255 // 63
            b1 = (c1 & 0x1F) * 255 // 31

            if c0 > c1:
                colors = [
                    (r0, g0, b0),
                    (r1, g1, b1),
                    ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                    ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3),
                ]
            else:
                colors = [
                    (r0, g0, b0),
                    (r1, g1, b1),
                    ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2),
                    (0, 0, 0),
                ]

            lookup = struct.unpack("<I", block[4:8])[0]
            for py in range(4):
                for px in range(4):
                    shift = (py * 4 + px) * 2
                    color_idx = (lookup >> shift) & 0x3
                    img.putpixel((bx * 4 + px, by * 4 + py), colors[color_idx])

    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


class ReferenceMOGGDemuxer:
    """Reference MOGG Vorbis demuxer."""
    @classmethod
    def demux_to_stems(
        cls,
        mogg_data: bytes,
        channel_mapping: Dict[str, List[int]],
        pans: Optional[List[float]] = None,
        vols: Optional[List[float]] = None,
        num_threads: int = 4,
    ) -> Dict[str, bytes]:
        if len(mogg_data) < 8:
            raise ValueError("MOGG data too short")
        mogg_type, ogg_offset = struct.unpack("<II", mogg_data[:8])
        if mogg_type != 10:
            raise ValueError(f"Unsupported MOGG type: {mogg_type}")
        
        ogg_bytes = mogg_data[ogg_offset:]
        bio = io.BytesIO(ogg_bytes)
        pcm_data, sr = sf.read(bio)
        
        if pcm_data.ndim == 1:
            pcm_data = pcm_data[:, np.newaxis]
        total_channels = pcm_data.shape[1]

        stem_target_map = {
            "drum": "drums.ogg",
            "drums": "drums.ogg",
            "bass": "rhythm.ogg",
            "guitar": "guitar.ogg",
            "vocals": "vocals.ogg",
            "keys": "keys.ogg",
            "crowd": "crowd.ogg",
        }

        used_channels = set()
        result_stems: Dict[str, bytes] = {}

        for track_key, ch_indices in channel_mapping.items():
            valid_ch = [c for c in ch_indices if c < total_channels]
            if not valid_ch:
                continue
            for c in valid_ch:
                used_channels.add(c)
            stem_filename = stem_target_map.get(track_key.lower(), f"{track_key.lower()}.ogg")
            stem_pcm = np.ascontiguousarray(np.clip(pcm_data[:, valid_ch], -1.0, 1.0), dtype=np.float32)
            
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tmp_stem_path = tf.name
            try:
                with sf.SoundFile(tmp_stem_path, mode="w", samplerate=sr, channels=len(valid_ch), format="OGG", subtype="VORBIS") as f:
                    chunk_sz = 65536
                    for i in range(0, len(stem_pcm), chunk_sz):
                        f.write(stem_pcm[i : i + chunk_sz])
                result_stems[stem_filename] = Path(tmp_stem_path).read_bytes()
            finally:
                Path(tmp_stem_path).unlink(missing_ok=True)

        # Unassigned channels -> song.ogg
        unassigned = [c for c in range(total_channels) if c not in used_channels]
        if unassigned:
            backing_pcm = np.ascontiguousarray(np.clip(pcm_data[:, unassigned], -1.0, 1.0), dtype=np.float32)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tmp_stem_path = tf.name
            try:
                with sf.SoundFile(tmp_stem_path, mode="w", samplerate=sr, channels=len(unassigned), format="OGG", subtype="VORBIS") as f:
                    chunk_sz = 65536
                    for i in range(0, len(backing_pcm), chunk_sz):
                        f.write(backing_pcm[i : i + chunk_sz])
                result_stems["song.ogg"] = Path(tmp_stem_path).read_bytes()
            finally:
                Path(tmp_stem_path).unlink(missing_ok=True)
        elif "song.ogg" not in result_stems and total_channels > 0:
            backing_pcm = np.ascontiguousarray(np.clip(pcm_data, -1.0, 1.0), dtype=np.float32)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tmp_stem_path = tf.name
            try:
                with sf.SoundFile(tmp_stem_path, mode="w", samplerate=sr, channels=total_channels, format="OGG", subtype="VORBIS") as f:
                    chunk_sz = 65536
                    for i in range(0, len(backing_pcm), chunk_sz):
                        f.write(backing_pcm[i : i + chunk_sz])
                result_stems["song.ogg"] = Path(tmp_stem_path).read_bytes()
            finally:
                Path(tmp_stem_path).unlink(missing_ok=True)

        return result_stems


def reference_convert_con_to_song_folder(
    con_data: Union[bytes, Path],
    output_dir: Path,
    overwrite: bool = False,
) -> Tuple[bool, str]:
    """Reference end-to-end converter pipeline."""
    if isinstance(con_data, (str, Path)):
        data = Path(con_data).read_bytes()
    else:
        data = bytes(con_data)

    archive = ReferenceSTFSPackage.from_bytes(data)
    files = archive.extract_all_memory()

    # 1. Parse DTA
    dta_path = None
    for k in files:
        if k.endswith("songs.dta"):
            dta_path = k
            break
    if not dta_path:
        return False, "songs.dta not found in CON package"

    dta_dict = reference_parse_dta(files[dta_path])
    song_id = dta_dict.get("song_id", "song")
    title = dta_dict.get("name", song_id)
    artist = dta_dict.get("artist", "Unknown")

    # Sanitize folder name
    clean_folder_name = f"{artist} - {title}".replace("/", "_").replace("\\", "_").replace(":", "_").replace("*", "_").replace("?", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")
    target_folder = output_dir / clean_folder_name
    if target_folder.exists() and not overwrite:
        return True, f"[SKIPPED] {clean_folder_name}"

    target_folder.mkdir(parents=True, exist_ok=True)

    # 2. Write song.ini
    ini_content = reference_generate_song_ini(dta_dict)
    (target_folder / "song.ini").write_text(ini_content, encoding="utf-8")

    # 3. Write notes.mid
    mid_key = None
    for k in files:
        if k.endswith(".mid"):
            mid_key = k
            break
    if mid_key:
        (target_folder / "notes.mid").write_bytes(files[mid_key])

    # 4. Write album.png
    img_key = None
    for k in files:
        if k.endswith(".png_xbox") or k.endswith(".png_xbo") or k.endswith(".bmp_xbox") or "_keep." in k:
            img_key = k
            break
    if img_key:
        try:
            png_bytes = reference_decode_png_xbox(files[img_key])
            (target_folder / "album.png").write_bytes(png_bytes)
        except Exception:
            pass

    # 5. Demux MOGG
    mogg_key = None
    for k in files:
        if k.endswith(".mogg"):
            mogg_key = k
            break
    if mogg_key:
        channel_map = dta_dict.get("track_mapping", {})
        stems = ReferenceMOGGDemuxer.demux_to_stems(files[mogg_key], channel_map)
        for s_name, s_bytes in stems.items():
            (target_folder / s_name).write_bytes(s_bytes)

    return True, f"[CONVERTED] {clean_folder_name}"


# ============================================================================
# 8. DYNAMIC RESOLVER & REFERENCE VALIDATORS
# ============================================================================

def get_stfs_parser_class():
    """Tries importing target implementation, falls back to reference oracle."""
    try:
        from fnf_fast_converter.src.stfs import STFSPackage
        return STFSPackage
    except (ImportError, ModuleNotFoundError, AttributeError):
        return ReferenceSTFSPackage


def get_dta_parser():
    try:
        from fnf_fast_converter.src.dta import parse_dta
        return parse_dta
    except (ImportError, ModuleNotFoundError, AttributeError):
        return reference_parse_dta


def get_song_ini_generator():
    try:
        from fnf_fast_converter.src.ini import generate_song_ini
        return generate_song_ini
    except (ImportError, ModuleNotFoundError, AttributeError):
        return reference_generate_song_ini


def get_image_decoder():
    try:
        from fnf_fast_converter.src.image import decode_png_xbox
        return decode_png_xbox
    except (ImportError, ModuleNotFoundError, AttributeError):
        return reference_decode_png_xbox


def get_mogg_demuxer_class():
    try:
        from fnf_fast_converter.src.mogg import MOGGDemuxer
        return MOGGDemuxer
    except (ImportError, ModuleNotFoundError, AttributeError):
        return ReferenceMOGGDemuxer


def get_converter_pipeline():
    try:
        from fnf_fast_converter.src.pipeline import convert_con_to_song_folder
        return convert_con_to_song_folder
    except (ImportError, ModuleNotFoundError, AttributeError):
        return reference_convert_con_to_song_folder


def validate_song_ini_content(ini_text: str) -> Dict[str, Any]:
    """
    Strictly verifies Clone Hero song.ini compliance:
    - [song] header exists
    - icon = fnf appears immediately in [song]
    - Returns dictionary of parsed key-value properties
    """
    lines = [l.strip() for l in ini_text.splitlines() if l.strip()]
    if not lines:
        raise AssertionError("song.ini is completely empty")
    
    if lines[0] != "[song]":
        raise AssertionError(f"First non-empty line of song.ini must be '[song]', found '{lines[0]}'")
    
    found_icon_fnf = False
    for line in lines[1:5]:
        if re.match(r"^icon\s*=\s*fnf$", line, re.IGNORECASE):
            found_icon_fnf = True
            break
    if not found_icon_fnf:
        raise AssertionError(f"song.ini must specify 'icon = fnf' directly under [song], lines: {lines[:5]}")

    props: Dict[str, str] = {}
    for l in lines[1:]:
        if "=" in l:
            k, v = l.split("=", 1)
            props[k.strip().lower()] = v.strip()

    return props


def validate_converted_chart_folder(
    folder_path: Path,
    expected_title: Optional[str] = None,
    expected_artist: Optional[str] = None,
    require_stems: bool = True,
    require_album_png: bool = True,
) -> Dict[str, Any]:
    """
    Performs complete verification on an output Clone Hero chart directory.
    """
    if not folder_path.is_dir():
        raise AssertionError(f"Target chart folder does not exist: {folder_path}")

    # 1. Check song.ini
    ini_path = folder_path / "song.ini"
    if not ini_path.is_file():
        raise AssertionError(f"song.ini missing in {folder_path}")
    
    ini_text = ini_path.read_text(encoding="utf-8", errors="replace")
    props = validate_song_ini_content(ini_text)

    if expected_title and props.get("name") != expected_title:
        raise AssertionError(f"Expected title '{expected_title}', got '{props.get('name')}'")
    if expected_artist and props.get("artist") != expected_artist:
        raise AssertionError(f"Expected artist '{expected_artist}', got '{props.get('artist')}'")

    # 2. Check notes.mid
    mid_path = folder_path / "notes.mid"
    if not mid_path.is_file():
        raise AssertionError(f"notes.mid missing in {folder_path}")
    mid_bytes = mid_path.read_bytes()
    if len(mid_bytes) < 14 or mid_bytes[:4] != b"MThd":
        raise AssertionError(f"notes.mid is corrupted or invalid MIDI header: {mid_bytes[:4]!r}")

    # 3. Check album.png
    if require_album_png:
        png_path = folder_path / "album.png"
        if not png_path.is_file():
            raise AssertionError(f"album.png missing in {folder_path}")
        with Image.open(png_path) as img:
            if img.size != (256, 256) and img.size != (512, 512) and img.size != (64, 64):
                raise AssertionError(f"Unexpected album.png dimensions: {img.size}")

    # 4. Check audio stems
    ogg_files = list(folder_path.glob("*.ogg"))
    if require_stems and len(ogg_files) == 0:
        raise AssertionError(f"No .ogg stems found in {folder_path}")

    stem_info = {}
    for ogg in ogg_files:
        try:
            data, sr = sf.read(str(ogg))
            stem_info[ogg.name] = {
                "sample_rate": sr,
                "channels": 1 if data.ndim == 1 else data.shape[1],
                "samples": len(data),
                "duration": len(data) / sr,
            }
        except Exception as e:
            raise AssertionError(f"Failed reading stem {ogg.name}: {e}")

    return {
        "folder": str(folder_path),
        "props": props,
        "midi_size": len(mid_bytes),
        "stems": stem_info,
    }
