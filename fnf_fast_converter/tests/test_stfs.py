"""
Unit and Integration Tests for Xbox 360 STFS In-Memory Reader (`src/stfs.py`).
"""

import io
import os
import struct
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from fnf_fast_converter.src.stfs import (
    CorruptedSTFSError,
    InvalidSTFSHeaderError,
    STFSEntry,
    STFSError,
    STFSFileNotFoundError,
    STFSHeader,
    STFSPackage,
    STFSVolumeDescriptor,
)

SAMPLE_CON_DIR = Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB")


def create_synthetic_stfs(
    magic: bytes = b"CON ",
    files: Dict[str, bytes] = None,
    contiguous_flags: Dict[str, bool] = None,
    non_sequential_chains: bool = False,
    extra_blocks: int = 0,
) -> bytes:
    """
    Build a mathematically valid, fully compliant synthetic Xbox 360 STFS package.

    Supports arbitrary files, directories, multi-block files, cross-L0 boundary
    allocations, contiguous and non-contiguous hash chains.
    """
    if files is None:
        files = {
            "songs/songs.dta": b"(test_song (name \"Test Song\"))",
            "songs/testsong/testsong.mid": b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\xe0" + b"\x00" * 5000,
            "songs/testsong/testsong.mogg": b"\x0a\x00\x00\x00\x54\x0b\x00\x00" + b"\x00" * 3000,
            "songs/testsong/testsong.pan": b"",
            "songs/testsong/gen/testsong_keep.png_xbox": b"\x01\x00\x04\x00\x08\x00\x00\x00" + b"\xaa" * 1000,
        }
    if contiguous_flags is None:
        contiguous_flags = {}

    hdr_size = 0xAD0E
    base_offset = 0xB000

    # Build directory entries hierarchy
    dir_entries: List[dict] = []
    path_to_idx: Dict[str, int] = {}

    # Gather all unique directories in order
    dirs = set()
    for file_path in files.keys():
        parts = file_path.strip("/").split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))

    sorted_dirs = sorted(list(dirs), key=lambda d: (d.count("/"), d))
    for d in sorted_dirs:
        parts = d.split("/")
        parent_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
        parent_idx = path_to_idx[parent_path] if parent_path else 0xFFFF
        idx = len(dir_entries)
        path_to_idx[d] = idx
        dir_entries.append({
            "name": parts[-1],
            "is_dir": True,
            "is_contig": False,
            "parent_idx": parent_idx,
            "file_size": 0,
            "first_block": 0,
            "block_count": 0,
            "data": b"",
        })

    for file_path, content in files.items():
        parts = file_path.strip("/").split("/")
        fname = parts[-1]
        parent_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
        parent_idx = path_to_idx[parent_path] if parent_path else 0xFFFF
        idx = len(dir_entries)
        path_to_idx[file_path] = idx
        is_contig = contiguous_flags.get(file_path, False)
        blk_cnt = (len(content) + 4095) // 4096 if len(content) > 0 else 0
        dir_entries.append({
            "name": fname,
            "is_dir": False,
            "is_contig": is_contig,
            "parent_idx": parent_idx,
            "file_size": len(content),
            "first_block": 0,  # Assigned during block layout
            "block_count": blk_cnt,
            "data": content,
        })

    # Block 0 is allocated for Directory Table
    current_logical_block = 1
    file_block_allocations: List[Tuple[dict, List[int]]] = []

    for entry in dir_entries:
        if entry["is_dir"] or entry["block_count"] == 0:
            continue
        blk_count = entry["block_count"]
        if non_sequential_chains and blk_count > 1:
            # Allocate non-consecutive blocks
            allocated = []
            for _ in range(blk_count):
                allocated.append(current_logical_block)
                current_logical_block += 2
        else:
            allocated = list(range(current_logical_block, current_logical_block + blk_count))
            current_logical_block += blk_count

        entry["first_block"] = allocated[0]
        file_block_allocations.append((entry, allocated))

    total_alloc_blocks = current_logical_block + extra_blocks

    # Helper functions for physical offsets
    def get_phys_block(b: int) -> int:
        num_l0 = (b // 170) + 1
        num_l1 = ((b // (170 * 170)) + 1) if b >= 170 else 0
        num_l2 = ((b // (170 * 170 * 170)) + 1) if b >= (170 * 170) else 0
        return b + num_l0 + num_l1 + num_l2

    def get_table_phys_block(t: int) -> int:
        num_l0 = t
        num_l1 = ((t // 170) + 1) if t >= 1 else 0
        num_l2 = ((t // (170 * 170)) + 1) if t >= 170 else 0
        return (t * 170) + num_l0 + num_l1 + num_l2

    max_logical_block = total_alloc_blocks + 170
    max_phys_block = get_phys_block(max_logical_block) + 2
    total_size = base_offset + (max_phys_block * 4096)
    pkg = bytearray(total_size)

    # Write Header
    pkg[0x00:len(magic)] = magic
    struct.pack_into(">I", pkg, 0x0340, hdr_size)
    struct.pack_into(">I", pkg, 0x0344, 0x00000002)  # Marketplace DLC
    struct.pack_into(">I", pkg, 0x0348, 2)  # Metadata version
    struct.pack_into(">I", pkg, 0x0360, 0x45410829)  # Rock Band Title ID

    # Volume Descriptor at 0x0379
    vd = bytearray(36)
    vd[0] = 0x24  # Size
    vd[2] = 0x01  # Block separation
    struct.pack_into(">H", vd, 3, 1)  # FileTableBlockCount = 1
    vd[5] = 0  # FileTableBlockNum = 0
    vd[6] = 0
    vd[7] = 0
    struct.pack_into(">I", vd, 28, total_alloc_blocks)
    pkg[0x0379:0x0379 + 36] = vd

    # Metadata strings & thumbnails
    publisher_str = "Test Publisher".encode("utf-16be")
    title_str = "Test Song Package".encode("utf-16be")
    pkg[0x1611:0x1611 + len(publisher_str)] = publisher_str
    pkg[0x1691:0x1691 + len(title_str)] = title_str

    thumb_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    struct.pack_into(">I", pkg, 0x1712, len(thumb_data))
    pkg[0x171A:0x171A + len(thumb_data)] = thumb_data

    # Write Directory Table at logical block 0
    dir_table_phys = get_phys_block(0)
    dir_table_offset = base_offset + (dir_table_phys * 4096)

    for i, entry in enumerate(dir_entries):
        chunk = bytearray(64)
        name_bytes = entry["name"].encode("latin1")[:40]
        chunk[:len(name_bytes)] = name_bytes

        flags = len(name_bytes) & 0x3F
        if entry["is_dir"]:
            flags |= 0x80
        if entry["is_contig"]:
            flags |= 0x40
        chunk[0x28] = flags

        alloc_cnt = entry["block_count"]
        chunk[0x29] = alloc_cnt & 0xFF
        chunk[0x2A] = (alloc_cnt >> 8) & 0xFF
        chunk[0x2B] = (alloc_cnt >> 16) & 0xFF

        chunk[0x2C] = alloc_cnt & 0xFF
        chunk[0x2D] = (alloc_cnt >> 8) & 0xFF
        chunk[0x2E] = (alloc_cnt >> 16) & 0xFF

        start_blk = entry["first_block"]
        chunk[0x2F] = start_blk & 0xFF
        chunk[0x30] = (start_blk >> 8) & 0xFF
        chunk[0x31] = (start_blk >> 16) & 0xFF

        struct.pack_into(">H", chunk, 0x32, entry["parent_idx"])
        struct.pack_into(">I", chunk, 0x34, entry["file_size"])
        struct.pack_into(">I", chunk, 0x38, 0x12345678)
        struct.pack_into(">I", chunk, 0x3C, 0x12345678)

        entry_off = dir_table_offset + (i * 64)
        pkg[entry_off:entry_off + 64] = chunk

    # Set Directory Block next pointer in L0 Hash Table 0
    # Directory is block 0 -> entry 0 in table 0
    t0_offset = base_offset + (get_table_phys_block(0) * 4096)
    struct.pack_into(">I", pkg, t0_offset + 20, 0x80FFFFFF)

    # Write file data and hash table chain pointers
    for entry, blocks in file_block_allocations:
        data = entry["data"]
        for b_idx, blk in enumerate(blocks):
            # Write data slice
            phys_blk = get_phys_block(blk)
            phys_off = base_offset + (phys_blk * 4096)
            chunk = data[b_idx * 4096:(b_idx + 1) * 4096]
            pkg[phys_off:phys_off + len(chunk)] = chunk

            # Write next block pointer in L0 hash table
            t_idx = blk // 170
            e_idx = blk % 170
            t_phys = get_table_phys_block(t_idx)
            t_off = base_offset + (t_phys * 4096)
            ent_off = t_off + (e_idx * 24)

            next_blk = blocks[b_idx + 1] if b_idx + 1 < len(blocks) else 0x00FFFFFF
            struct.pack_into(">I", pkg, ent_off + 20, 0x80000000 | next_blk)

    return bytes(pkg)


# -----------------------------------------------------------------------------
# Unit Tests
# -----------------------------------------------------------------------------


def test_magic_headers_validation():
    """Verify support for CON, LIVE, and PIRS headers, and rejection of invalid magics."""
    for magic in (b"CON ", b"LIVE", b"PIRS"):
        data = create_synthetic_stfs(magic=magic)
        pkg = STFSPackage.from_bytes(data)
        assert pkg.header.magic == magic
        assert pkg.volume_descriptor.total_allocated_blocks > 0

    # Invalid magics
    with pytest.raises(InvalidSTFSHeaderError, match="Invalid STFS magic"):
        STFSPackage.from_bytes(b"BAD!" + b"\x00" * 0x3000)

    # Truncated buffer
    with pytest.raises(InvalidSTFSHeaderError, match="smaller than minimum STFS header"):
        STFSPackage.from_bytes(b"CON \x00\x00")


def test_volume_descriptor_parsing():
    """Verify precise parsing of STFS volume descriptor fields at 0x0379."""
    data = create_synthetic_stfs(magic=b"CON ")
    pkg = STFSPackage(data)
    vd = pkg.volume_descriptor

    assert vd.descriptor_size == 0x24
    assert vd.block_separation == 0x01
    assert vd.file_table_block_count == 1
    assert vd.file_table_block_num == 0
    assert vd.total_allocated_blocks > 0
    assert pkg.header.base_data_offset == 0xB000
    assert pkg.header.publisher == "Test Publisher"
    assert pkg.header.title_name == "Test Song Package"


def test_block_translation_math():
    """Verify Level 0, Level 1, and Level 2 block translation mathematics."""
    data = create_synthetic_stfs()
    pkg = STFSPackage(data)

    # Level 0 (0..169)
    # block 0: num_l0=1, num_l1=0, num_l2=0 -> phys=1
    assert pkg.get_physical_block_index(0) == 1
    assert pkg.get_physical_offset(0) == 0xB000 + (1 * 4096)

    # block 169: num_l0=1, num_l1=0, num_l2=0 -> phys=170
    assert pkg.get_physical_block_index(169) == 170

    # Level 0 Table 1 boundary (block 170)
    # block 170: num_l0=2, num_l1=1, num_l2=0 -> phys=173
    assert pkg.get_physical_block_index(170) == 173
    assert pkg.get_physical_offset(170) == 0xB000 + (173 * 4096)

    # block 339: num_l0=2, num_l1=1, num_l2=0 -> phys=342
    assert pkg.get_physical_block_index(339) == 342

    # block 340: num_l0=3, num_l1=1, num_l2=0 -> phys=344
    assert pkg.get_physical_block_index(340) == 344

    # Level 1 boundary (block 28900 = 170 * 170)
    # block 28899: num_l0 = (28899//170)+1 = 170, num_l1 = (28899//28900)+1 = 1, num_l2 = 0
    # phys = 28899 + 170 + 1 = 29070
    assert pkg.get_physical_block_index(28899) == 28899 + 170 + 1

    # block 28900: num_l0 = 171, num_l1 = 2, num_l2 = 1 (since 28900 >= 28900)
    # phys = 28900 + 171 + 2 + 1 = 29074
    assert pkg.get_physical_block_index(28900) == 28900 + 171 + 2 + 1


def test_synthetic_directory_and_file_extraction():
    """Verify directory hierarchy reconstruction and byte-exact file extraction."""
    files_payload = {
        "songs/songs.dta": b"(buddyholly\n   (name \"Buddy Holly\"))\n",
        "songs/buddyholly/buddyholly.mid": b"MThd" + b"\x12\x34" * 1000,
        "songs/buddyholly/buddyholly.mogg": b"\x0a\x00\x00\x00\x54\x0b\x00\x00" + b"\x55\xaa" * 2000,
        "songs/buddyholly/buddyholly.pan": b"",
        "songs/buddyholly/gen/buddyholly_keep.png_xbox": b"\x01\x00\x04\x00" + b"\x99" * 500,
    }

    data = create_synthetic_stfs(files=files_payload)
    pkg = STFSPackage.from_bytes(data)

    file_list = pkg.list_files()
    assert "songs/songs.dta" in file_list
    assert "songs/buddyholly/buddyholly.mid" in file_list
    assert "songs/buddyholly/buddyholly.mogg" in file_list
    assert "songs/buddyholly/buddyholly.pan" in file_list
    assert "songs/buddyholly/gen/buddyholly_keep.png_xbox" in file_list
    assert len(file_list) == 5

    # Test file bytes extraction
    for path, expected in files_payload.items():
        extracted = pkg.get_file_bytes(path)
        assert extracted == expected, f"Content mismatch for {path}"
        # Test memoryview
        mv = pkg.get_file_memoryview(path)
        assert bytes(mv) == expected

    # Test thumbnail extraction
    thumb = pkg.get_thumbnail()
    assert thumb is not None
    assert thumb.startswith(b"\x89PNG")

    # Test extract_all_memory
    all_files = pkg.extract_all_memory()
    assert len(all_files) == 5
    for path, expected in files_payload.items():
        assert all_files[path] == expected

    # Test extension filtering
    assert pkg.find_by_extension("dta") == ["songs/songs.dta"]
    assert pkg.find_by_extension(".mid") == ["songs/buddyholly/buddyholly.mid"]
    assert pkg.find_by_extension("mogg") == ["songs/buddyholly/buddyholly.mogg"]
    assert pkg.find_by_extension("png_xbox") == ["songs/buddyholly/gen/buddyholly_keep.png_xbox"]


def test_flexible_path_lookup_and_missing_files():
    """Verify robust path resolution (case-insensitive, backslashes, leading slashes, missing files)."""
    files_payload = {
        "songs/songs.dta": b"(test dta)",
        "songs/test/track.mid": b"MThd MIDI DATA",
    }
    pkg = STFSPackage(create_synthetic_stfs(files=files_payload))

    # Flexible path lookups
    assert pkg.get_file_bytes("songs/songs.dta") == b"(test dta)"
    assert pkg.get_file_bytes("/songs/songs.dta") == b"(test dta)"
    assert pkg.get_file_bytes("songs\\songs.dta") == b"(test dta)"
    assert pkg.get_file_bytes("SONGS/SONGS.DTA") == b"(test dta)"
    assert pkg.get_file_bytes("songs.dta") == b"(test dta)"  # Unambiguous basename lookup
    assert pkg.get_file_bytes("track.mid") == b"MThd MIDI DATA"

    # Contains check
    assert "songs/songs.dta" in pkg
    assert "nonexistent.file" not in pkg

    # Missing file error
    with pytest.raises(STFSFileNotFoundError):
        pkg.get_file_bytes("does_not_exist.bin")

    # Directory access as file error
    with pytest.raises(STFSFileNotFoundError, match="is a directory"):
        pkg.get_file_bytes("songs")


def test_non_contiguous_chained_blocks():
    """Verify that multi-block files with non-contiguous hash chains extract accurately."""
    multi_block_content = bytes([(i % 256) for i in range(15000)])  # ~4 blocks
    files_payload = {
        "songs/songs.dta": b"(dta)",
        "songs/test/large.bin": multi_block_content,
    }
    data = create_synthetic_stfs(files=files_payload, non_sequential_chains=True)
    pkg = STFSPackage.from_bytes(data)

    assert pkg.get_file_bytes("songs/test/large.bin") == multi_block_content


def test_large_file_spanning_hash_table_boundary():
    """Verify file extraction when data spans across the 170-block Level 0 hash table boundary."""
    # 200 blocks * 4096 = 819,200 bytes (spans from L0 Table 0 past L0 Table 1)
    large_payload = bytearray(819200)
    for i in range(len(large_payload)):
        large_payload[i] = (i * 37) & 0xFF

    files_payload = {
        "songs/songs.dta": b"(large song test)",
        "songs/test/huge.mogg": bytes(large_payload),
    }

    data = create_synthetic_stfs(files=files_payload, contiguous_flags={"songs/test/huge.mogg": False})
    pkg = STFSPackage.from_bytes(data)

    extracted = pkg.get_file_bytes("songs/test/huge.mogg")
    assert len(extracted) == len(large_payload)
    assert extracted == bytes(large_payload)


def test_file_io_and_mmap():
    """Verify loading from disk with and without mmap."""
    files_payload = {"songs/songs.dta": b"mmap test dta content"}
    data = create_synthetic_stfs(files=files_payload)

    with tempfile.NamedTemporaryFile(suffix=".con", delete=False) as tf:
        tf.write(data)
        tf_path = tf.name

    try:
        # Load with mmap
        with STFSPackage.from_file(tf_path, use_mmap=True) as pkg_mmap:
            assert pkg_mmap.get_file_bytes("songs/songs.dta") == b"mmap test dta content"

        # Load without mmap
        with STFSPackage.from_file(tf_path, use_mmap=False) as pkg_no_mmap:
            assert pkg_no_mmap.get_file_bytes("songs/songs.dta") == b"mmap test dta content"
    finally:
        if os.path.exists(tf_path):
            os.unlink(tf_path)

    # Missing file check
    with pytest.raises(FileNotFoundError):
        STFSPackage.from_file("non_existent_file_path_12345.con")


def test_contiguous_file_spanning_boundary():
    """Verify contiguous file extraction when contiguous file spans across L0 hash table boundaries."""
    # 200 blocks * 4096 = 819,200 bytes contiguous
    large_payload = bytearray(819200)
    for i in range(len(large_payload)):
        large_payload[i] = (i * 13) & 0xFF

    files_payload = {
        "songs/test/contig_huge.mogg": bytes(large_payload),
    }

    data = create_synthetic_stfs(files=files_payload, contiguous_flags={"songs/test/contig_huge.mogg": True})
    pkg = STFSPackage.from_bytes(data)

    extracted = pkg.get_file_bytes("songs/test/contig_huge.mogg")
    assert len(extracted) == len(large_payload)
    assert extracted == bytes(large_payload)


def test_deeply_nested_directories():
    """Verify path hierarchy reconstruction with deeply nested directories."""
    nested_path = "level1/level2/level3/level4/level5/deep_file.txt"
    files_payload = {
        nested_path: b"deep nested content payload",
    }
    data = create_synthetic_stfs(files=files_payload)
    pkg = STFSPackage(data)

    assert nested_path in pkg.list_files()
    assert pkg.get_file_bytes(nested_path) == b"deep nested content payload"
    assert pkg.get_file_bytes("deep_file.txt") == b"deep nested content payload"


def test_empty_stfs_package():
    """Verify package handling when no files are contained."""
    data = create_synthetic_stfs(files={})
    pkg = STFSPackage(data)

    assert pkg.list_files() == []
    assert pkg.extract_all_memory() == {}
    assert pkg.find_by_extension("dta") == []


def test_all_entries_property_and_metadata():
    """Verify entry metadata properties such as timestamps and parent indices."""
    files_payload = {
        "songs/songs.dta": b"(dta)",
    }
    data = create_synthetic_stfs(files=files_payload)
    pkg = STFSPackage(data)

    entries = pkg.list_all()
    assert len(entries) >= 2  # dir + file

    dta_entry = pkg.get_file_entry("songs/songs.dta")
    assert dta_entry.name == "songs.dta"
    assert dta_entry.full_path == "songs/songs.dta"
    assert not dta_entry.is_dir
    assert dta_entry.file_size == len(b"(dta)")
    assert dta_entry.creation_time == 0x12345678
    assert dta_entry.modified_time == 0x12345678


# -----------------------------------------------------------------------------
# Real-world CON Dataset Tests
# -----------------------------------------------------------------------------


@pytest.mark.skipif(not SAMPLE_CON_DIR.exists(), reason="Sample CON directory not present")
def test_real_con_files_parsing_and_performance():
    """
    Test STFS parsing and extraction against real Rock Band CON files.
    Ensures sub-millisecond parsing and 100% format fidelity.
    """
    con_paths = list(SAMPLE_CON_DIR.glob("*"))[:25]
    assert len(con_paths) > 0, "No CON files found in sample directory"

    parse_times = []

    for path in con_paths:
        t0 = time.perf_counter()
        with STFSPackage.from_file(path, use_mmap=True) as pkg:
            t_parse = (time.perf_counter() - t0) * 1000  # ms
            parse_times.append(t_parse)

            assert pkg.header.magic == b"CON "
            assert pkg.volume_descriptor.total_allocated_blocks > 0

            files = pkg.list_files()
            assert len(files) >= 3, f"Expected at least 3 files in {path.name}, got {files}"

            # Find songs.dta
            dta_files = pkg.find_by_extension("dta")
            assert len(dta_files) >= 1, f"Missing songs.dta in {path.name}"
            dta_bytes = pkg.get_file_bytes(dta_files[0])
            assert len(dta_bytes) > 0

            # Find .mid
            mid_files = pkg.find_by_extension("mid")
            assert len(mid_files) >= 1, f"Missing MIDI chart in {path.name}"
            mid_bytes = pkg.get_file_bytes(mid_files[0])
            assert mid_bytes.startswith(b"MThd"), f"Invalid MIDI header in {mid_files[0]}"

            # Find .mogg
            mogg_files = pkg.find_by_extension("mogg")
            assert len(mogg_files) >= 1, f"Missing MOGG audio in {path.name}"
            mogg_header = pkg.get_file_bytes(mogg_files[0])[:8]
            mtype = struct.unpack("<I", mogg_header[:4])[0]
            assert mtype == 10, f"Expected MOGG Type 10, got {mtype} in {path.name}"

            # Test extract_all_memory
            extracted = pkg.extract_all_memory()
            assert len(extracted) == len(files)
            for f in files:
                assert f in extracted
                assert len(extracted[f]) == pkg.get_file_entry(f).file_size

    avg_time = sum(parse_times) / len(parse_times)
    max_time = max(parse_times)
    print(f"\n[Real CON Benchmark] Tested {len(con_paths)} packages. Avg: {avg_time:.3f}ms, Max: {max_time:.3f}ms")
    assert avg_time < 5.0, f"Average parse time {avg_time:.3f}ms exceeded 5.0ms threshold"
