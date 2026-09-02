"""
Xbox 360 STFS (CON / LIVE / PIRS) In-Memory Package Parser.

Provides high-speed, zero-intermediate-disk parsing of Xbox 360 STFS packages,
supporting multi-level interleaved hash table block translation, directory
tree reconstruction, and in-memory file extraction.
"""

from __future__ import annotations

import io
import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple, Union


class STFSError(Exception):
    """Base exception for STFS parsing and extraction errors."""
    pass


class InvalidSTFSHeaderError(STFSError):
    """Raised when the package magic or header structure is invalid."""
    pass


class STFSFileNotFoundError(STFSError, KeyError):
    """Raised when a requested file does not exist in the STFS package."""
    pass


class CorruptedSTFSError(STFSError):
    """Raised when directory table or block chain structures are corrupted."""
    pass


@dataclass(frozen=True)
class STFSEntry:
    """Represents a directory or file entry inside an STFS package."""
    name: str
    full_path: str
    is_dir: bool
    is_contiguous: bool
    allocated_blocks: int
    block_count: int
    starting_block: int
    parent_index: int
    file_size: int
    creation_time: int
    modified_time: int
    entry_index: int


@dataclass(frozen=True)
class STFSVolumeDescriptor:
    """Represents the 36-byte Volume Descriptor located at offset 0x0379."""
    descriptor_size: int
    reserved: int
    block_separation: int
    file_table_block_count: int
    file_table_block_num: int
    top_hash: bytes
    total_allocated_blocks: int
    total_unallocated_blocks: int


@dataclass(frozen=True)
class STFSHeader:
    """Represents STFS package header metadata."""
    magic: bytes
    header_size: int
    base_data_offset: int
    content_type: int
    metadata_version: int
    content_size: int
    media_id: int
    version: int
    base_version: int
    title_id: int
    platform: int
    executable_type: int
    disc_num: int
    disc_in_set: int
    save_game_id: int
    console_id: bytes
    profile_id: int
    volume_descriptor: STFSVolumeDescriptor
    file_count: int
    combined_file_size: int
    display_names: Dict[str, str]
    descriptions: Dict[str, str]
    publisher: str
    title_name: str
    flags: int
    thumbnail_size: int
    title_thumbnail_size: int
    thumbnail_offset: int


class STFSPackage:
    """
    High-performance in-memory Xbox 360 STFS (CON / LIVE / PIRS) Archive Reader.

    Supports zero-copy slicing, multi-level interleaved block translation,
    and instantaneous in-memory file extraction.
    """

    VALID_MAGICS = (b"CON ", b"LIVE", b"PIRS")
    BLOCK_SIZE = 4096  # 4KB block size
    BLOCKS_PER_L0 = 170
    BLOCKS_PER_L1 = 170 * 170  # 28,900 blocks
    BLOCKS_PER_L2 = 170 * 170 * 170  # 4,913,000 blocks

    def __init__(self, data: Union[bytes, bytearray, memoryview]) -> None:
        """
        Initialize an STFSPackage from an in-memory buffer.

        Args:
            data: Bytes-like object containing the complete STFS package.
        """
        if len(data) < 0x2000:
            raise InvalidSTFSHeaderError(
                f"Data size ({len(data)} bytes) is smaller than minimum STFS header (8192 bytes)."
            )

        self._raw_data: Union[bytes, bytearray, memoryview] = data
        self._data: memoryview = memoryview(data)
        self._mmap: Optional[mmap.mmap] = None
        self._file_handle: Optional[BinaryIO] = None
        self._header: STFSHeader = self._parse_header()
        self._entries: List[STFSEntry] = []
        self._entries_by_path: Dict[str, STFSEntry] = {}
        self._entries_by_lower_path: Dict[str, STFSEntry] = {}
        self._parse_directory()

    def __enter__(self) -> STFSPackage:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Release underlying mmap and file handles if open."""
        if hasattr(self, "_data") and self._data is not None:
            try:
                self._data.release()
            except Exception:
                pass
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    @classmethod
    def from_bytes(cls, data: Union[bytes, bytearray, memoryview]) -> STFSPackage:
        """
        Create an STFSPackage instance from raw in-memory bytes or memoryview.

        Args:
            data: Raw STFS package bytes.

        Returns:
            STFSPackage instance.
        """
        return cls(data)

    @classmethod
    def from_file(cls, file_path: Union[str, Path], use_mmap: bool = True) -> STFSPackage:
        """
        Create an STFSPackage instance directly from a file on disk.

        If use_mmap is True (default), memory-maps the file for zero-copy read
        performance without copying the whole file into Python memory.

        Args:
            file_path: Path to the CON / LIVE / PIRS package file.
            use_mmap: Whether to use mmap for file reading.

        Returns:
            STFSPackage instance.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"STFS file not found: {path}")

        file_size = path.stat().st_size
        if file_size < 0x2000:
            raise InvalidSTFSHeaderError(
                f"File size ({file_size} bytes) is smaller than minimum STFS header (8192 bytes)."
            )

        if use_mmap and file_size > 0:
            f = open(path, "rb")
            try:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                pkg = cls(mm)
                pkg._mmap = mm
                pkg._file_handle = f
                return pkg
            except Exception:
                f.close()
                raise
        else:
            with open(path, "rb") as f:
                data = f.read()
                return cls(data)

    @property
    def header(self) -> STFSHeader:
        """Return package header metadata."""
        return self._header

    @property
    def volume_descriptor(self) -> STFSVolumeDescriptor:
        """Return STFS Volume Descriptor."""
        return self._header.volume_descriptor

    @property
    def entries(self) -> List[STFSEntry]:
        """Return all directory and file entries."""
        return list(self._entries)

    def _parse_header(self) -> STFSHeader:
        """Parse the STFS header and Volume Descriptor."""
        magic = bytes(self._data[0x00:0x04])
        if magic not in self.VALID_MAGICS:
            raise InvalidSTFSHeaderError(
                f"Invalid STFS magic {magic!r}; expected one of {self.VALID_MAGICS!r}"
            )

        # Header size & base data offset calculation
        header_size = struct.unpack_from(">I", self._data, 0x0340)[0]
        base_data_offset = (header_size + 0x0FFF) & ~0x0FFF

        content_type = struct.unpack_from(">I", self._data, 0x0344)[0]
        metadata_version = struct.unpack_from(">I", self._data, 0x0348)[0]
        content_size = struct.unpack_from(">Q", self._data, 0x034C)[0]
        media_id = struct.unpack_from(">I", self._data, 0x0354)[0]
        version = struct.unpack_from(">I", self._data, 0x0358)[0]
        base_version = struct.unpack_from(">I", self._data, 0x035C)[0]
        title_id = struct.unpack_from(">I", self._data, 0x0360)[0]

        platform_meta = bytes(self._data[0x0364:0x0368])
        platform = platform_meta[0]
        executable_type = platform_meta[1]
        disc_num = platform_meta[2]
        disc_in_set = platform_meta[3]

        save_game_id = struct.unpack_from(">I", self._data, 0x0368)[0]
        console_id = bytes(self._data[0x036C:0x0371])
        profile_id = struct.unpack_from(">Q", self._data, 0x0371)[0]

        # Parse Volume Descriptor at 0x0379 (36 bytes)
        vd_bytes = self._data[0x0379:0x039D]
        descriptor_size = vd_bytes[0]
        reserved = vd_bytes[1]
        block_separation = vd_bytes[2]
        file_table_block_count = struct.unpack_from(">H", vd_bytes, 3)[0]
        file_table_block_num = (vd_bytes[5] << 16) | (vd_bytes[6] << 8) | vd_bytes[7]
        top_hash = bytes(vd_bytes[8:28])
        total_allocated_blocks = struct.unpack_from(">I", vd_bytes, 28)[0]
        total_unallocated_blocks = struct.unpack_from(">I", vd_bytes, 32)[0]

        vol_desc = STFSVolumeDescriptor(
            descriptor_size=descriptor_size,
            reserved=reserved,
            block_separation=block_separation,
            file_table_block_count=file_table_block_count,
            file_table_block_num=file_table_block_num,
            top_hash=top_hash,
            total_allocated_blocks=total_allocated_blocks,
            total_unallocated_blocks=total_unallocated_blocks,
        )

        file_count = struct.unpack_from(">I", self._data, 0x03FD)[0] if len(self._data) >= 0x0401 else 0
        combined_file_size = struct.unpack_from(">Q", self._data, 0x0401)[0] if len(self._data) >= 0x0409 else 0

        # Parse Title and Publisher strings (UTF-16BE)
        publisher = ""
        title_name = ""
        if len(self._data) >= 0x1711:
            try:
                pub_bytes = bytes(self._data[0x1611:0x1691])
                publisher = pub_bytes.decode("utf-16be", errors="ignore").rstrip("\x00")
            except Exception:
                pass
            try:
                title_bytes = bytes(self._data[0x1691:0x1711])
                title_name = title_bytes.decode("utf-16be", errors="ignore").rstrip("\x00")
            except Exception:
                pass

        flags = self._data[0x1711] if len(self._data) > 0x1711 else 0
        thumbnail_size = struct.unpack_from(">I", self._data, 0x1712)[0] if len(self._data) >= 0x1716 else 0
        title_thumbnail_size = struct.unpack_from(">I", self._data, 0x1716)[0] if len(self._data) >= 0x171A else 0
        thumbnail_offset = 0x171A

        return STFSHeader(
            magic=magic,
            header_size=header_size,
            base_data_offset=base_data_offset,
            content_type=content_type,
            metadata_version=metadata_version,
            content_size=content_size,
            media_id=media_id,
            version=version,
            base_version=base_version,
            title_id=title_id,
            platform=platform,
            executable_type=executable_type,
            disc_num=disc_num,
            disc_in_set=disc_in_set,
            save_game_id=save_game_id,
            console_id=console_id,
            profile_id=profile_id,
            volume_descriptor=vol_desc,
            file_count=file_count,
            combined_file_size=combined_file_size,
            display_names={},
            descriptions={},
            publisher=publisher,
            title_name=title_name,
            flags=flags,
            thumbnail_size=thumbnail_size,
            title_thumbnail_size=title_thumbnail_size,
            thumbnail_offset=thumbnail_offset,
        )

    def get_physical_block_index(self, block_index: int) -> int:
        """
        Calculate the physical 4KB block index in the package stream for a given logical data block.

        Accounts for interleaved Level 0, Level 1, and Level 2 hash tables:
        - Level 0: 1 table block every 170 data blocks.
        - Level 1: 1 table block every 28,900 data blocks (inserted before table index >= 1).
        - Level 2: 1 table block every 4,913,000 data blocks (inserted before table index >= 170).

        Args:
            block_index: Logical data block index (0-based).

        Returns:
            Physical 4KB block index starting from base_data_offset.
        """
        num_l0 = (block_index // self.BLOCKS_PER_L0) + 1
        num_l1 = ((block_index // self.BLOCKS_PER_L1) + 1) if block_index >= self.BLOCKS_PER_L0 else 0
        num_l2 = ((block_index // self.BLOCKS_PER_L2) + 1) if block_index >= self.BLOCKS_PER_L1 else 0
        return block_index + num_l0 + num_l1 + num_l2

    def get_physical_offset(self, block_index: int) -> int:
        """
        Calculate the absolute byte offset in the package for a given logical data block.

        Args:
            block_index: Logical data block index (0-based).

        Returns:
            Absolute physical byte offset.
        """
        phys_block = self.get_physical_block_index(block_index)
        return self._header.base_data_offset + (phys_block * self.BLOCK_SIZE)

    def get_next_block(self, block_index: int) -> int:
        """
        Read the next logical block pointer from the Level 0 Hash Table for a given logical block.

        Args:
            block_index: Current logical data block index.

        Returns:
            Next logical block index, or 0x00FFFFFF if EOF.
        """
        table_idx = block_index // self.BLOCKS_PER_L0
        entry_idx = block_index % self.BLOCKS_PER_L0

        num_l0 = table_idx
        num_l1 = ((table_idx // self.BLOCKS_PER_L0) + 1) if table_idx >= 1 else 0
        num_l2 = ((table_idx // self.BLOCKS_PER_L1) + 1) if table_idx >= self.BLOCKS_PER_L0 else 0

        table_phys_block = (table_idx * self.BLOCKS_PER_L0) + num_l0 + num_l1 + num_l2
        table_offset = self._header.base_data_offset + (table_phys_block * self.BLOCK_SIZE)
        entry_offset = table_offset + (entry_idx * 24)

        if entry_offset + 24 > len(self._data):
            return 0x00FFFFFF

        next_val = struct.unpack_from(">I", self._data, entry_offset + 20)[0]
        return next_val & 0x00FFFFFF

    def _parse_directory(self) -> None:
        """Read and parse the 64-byte directory table entries across the directory block chain."""
        vd = self._header.volume_descriptor
        curr_block = vd.file_table_block_num
        max_blocks = max(vd.total_allocated_blocks, 1)

        # Traverse directory block chain
        dir_data = bytearray()
        visited = set()
        while curr_block != 0x00FFFFFF and curr_block < max_blocks and curr_block not in visited:
            visited.add(curr_block)
            phys_offset = self.get_physical_offset(curr_block)
            if phys_offset + self.BLOCK_SIZE > len(self._data):
                # Read whatever remains if truncated
                chunk_len = max(0, len(self._data) - phys_offset)
                if chunk_len > 0:
                    dir_data.extend(self._data[phys_offset:phys_offset + chunk_len])
                break
            dir_data.extend(self._data[phys_offset:phys_offset + self.BLOCK_SIZE])
            curr_block = self.get_next_block(curr_block)

        # Parse 64-byte directory entry records
        raw_entries: List[dict] = []
        entry_idx = 0
        for i in range(0, len(dir_data) - 63, 64):
            chunk = dir_data[i:i + 64]
            if chunk[0] == 0:
                continue

            flags_and_len = chunk[0x28]
            name_len = flags_and_len & 0x3F
            if name_len == 0 or name_len > 40:
                continue

            is_dir = bool(flags_and_len & 0x80)
            is_contiguous = bool(flags_and_len & 0x40)

            name_bytes = bytes(chunk[:name_len])
            try:
                name = name_bytes.decode("utf-8")
            except UnicodeDecodeError:
                name = name_bytes.decode("latin1", errors="replace")

            alloc_blocks = chunk[0x29] | (chunk[0x2A] << 8) | (chunk[0x2B] << 16)
            block_count = chunk[0x2C] | (chunk[0x2D] << 8) | (chunk[0x2E] << 16)
            starting_block = chunk[0x2F] | (chunk[0x30] << 8) | (chunk[0x31] << 16)
            parent_index = struct.unpack_from(">H", chunk, 0x32)[0]
            file_size = struct.unpack_from(">I", chunk, 0x34)[0]
            creation_time = struct.unpack_from(">I", chunk, 0x38)[0]
            modified_time = struct.unpack_from(">I", chunk, 0x3C)[0]

            raw_entries.append({
                "entry_index": entry_idx,
                "name": name,
                "is_dir": is_dir,
                "is_contiguous": is_contiguous,
                "allocated_blocks": alloc_blocks,
                "block_count": block_count,
                "starting_block": starting_block,
                "parent_index": parent_index,
                "file_size": file_size,
                "creation_time": creation_time,
                "modified_time": modified_time,
            })
            entry_idx += 1

        # Reconstruct hierarchical paths
        for e in raw_entries:
            path_parts = [e["name"]]
            p_idx = e["parent_index"]
            visited_parents = set()
            while p_idx != 0xFFFF and 0 <= p_idx < len(raw_entries) and p_idx not in visited_parents:
                visited_parents.add(p_idx)
                path_parts.append(raw_entries[p_idx]["name"])
                p_idx = raw_entries[p_idx]["parent_index"]

            full_path = "/".join(reversed(path_parts))

            entry_obj = STFSEntry(
                name=e["name"],
                full_path=full_path,
                is_dir=e["is_dir"],
                is_contiguous=e["is_contiguous"],
                allocated_blocks=e["allocated_blocks"],
                block_count=e["block_count"],
                starting_block=e["starting_block"],
                parent_index=e["parent_index"],
                file_size=e["file_size"],
                creation_time=e["creation_time"],
                modified_time=e["modified_time"],
                entry_index=e["entry_index"],
            )

            self._entries.append(entry_obj)
            self._entries_by_path[full_path] = entry_obj
            self._entries_by_lower_path[full_path.lower()] = entry_obj

            # Also register normalized without leading slashes and with alternative lookups
            norm_path = full_path.lstrip("/")
            self._entries_by_path[norm_path] = entry_obj
            self._entries_by_lower_path[norm_path.lower()] = entry_obj

    def list_files(self) -> List[str]:
        """
        Return a sorted list of all file paths (excluding directories) in the package.

        Returns:
            List of full relative file path strings.
        """
        return sorted([e.full_path for e in self._entries if not e.is_dir])

    def list_all(self) -> List[STFSEntry]:
        """
        Return all parsed STFSEntry objects (including directories and files).

        Returns:
            List of STFSEntry objects.
        """
        return list(self._entries)

    def find_entry(self, path: str) -> Optional[STFSEntry]:
        """
        Find an STFSEntry by exact path, normalized path, or case-insensitive path.

        Args:
            path: Target file or directory path.

        Returns:
            STFSEntry if found, else None.
        """
        # Exact match
        if path in self._entries_by_path:
            return self._entries_by_path[path]

        norm = path.replace("\\", "/").strip("/")
        if norm in self._entries_by_path:
            return self._entries_by_path[norm]

        lower_norm = norm.lower()
        if lower_norm in self._entries_by_lower_path:
            return self._entries_by_lower_path[lower_norm]

        # Check if basename matches uniquely
        matching = [e for e in self._entries if not e.is_dir and (e.name.lower() == lower_norm or e.full_path.lower().endswith("/" + lower_norm))]
        if len(matching) == 1:
            return matching[0]

        return None

    def contains(self, path: str) -> bool:
        """Check if a file or directory exists in the package."""
        return self.find_entry(path) is not None

    def __contains__(self, path: str) -> bool:
        """Support 'path in package' operator."""
        return self.contains(path)

    def get_file_entry(self, path: str) -> STFSEntry:
        """
        Get the STFSEntry for a file path, raising STFSFileNotFoundError if missing or is a directory.

        Args:
            path: Relative path of the file.

        Returns:
            STFSEntry for the file.
        """
        entry = self.find_entry(path)
        if entry is None:
            raise STFSFileNotFoundError(f"File '{path}' not found in STFS package.")
        if entry.is_dir:
            raise STFSFileNotFoundError(f"Path '{path}' is a directory, not a file.")
        return entry

    def get_file_bytes(self, path: str) -> bytes:
        """
        Extract the complete contents of a file directly to in-memory bytes.

        Args:
            path: Relative path or filename.

        Returns:
            File content as raw bytes.
        """
        entry = self.get_file_entry(path)
        file_size = entry.file_size
        if file_size == 0:
            return b""

        # Fast path for contiguous single-block or multi-block files within single continuous physical block
        if entry.is_contiguous and entry.block_count > 0:
            # Check if block span crosses any L0 hash table boundary (every 170 blocks)
            start_l0 = entry.starting_block // self.BLOCKS_PER_L0
            end_l0 = (entry.starting_block + entry.block_count - 1) // self.BLOCKS_PER_L0
            if start_l0 == end_l0:
                # Completely contiguous in physical memory! Direct zero-copy slice!
                phys_off = self.get_physical_offset(entry.starting_block)
                if phys_off + file_size <= len(self._data):
                    return bytes(self._data[phys_off:phys_off + file_size])

        # General extraction path (contiguous crossing boundaries or chained non-contiguous)
        out_buf = bytearray()
        bytes_left = file_size
        cur_block = entry.starting_block
        max_blocks = max(self._header.volume_descriptor.total_allocated_blocks, entry.block_count + 10)
        blocks_read = 0

        while cur_block != 0x00FFFFFF and bytes_left > 0 and blocks_read < max_blocks:
            phys_off = self.get_physical_offset(cur_block)
            if phys_off >= len(self._data):
                break

            take_bytes = min(self.BLOCK_SIZE, bytes_left)
            available = len(self._data) - phys_off
            if available < take_bytes:
                take_bytes = available

            out_buf.extend(self._data[phys_off:phys_off + take_bytes])
            bytes_left -= take_bytes
            blocks_read += 1

            if entry.is_contiguous:
                cur_block += 1
            else:
                cur_block = self.get_next_block(cur_block)

        return bytes(out_buf)

    def get_file_memoryview(self, path: str) -> memoryview:
        """
        Attempt zero-copy extraction returning a memoryview slice if the file is physically contiguous,
        or a memoryview of newly extracted bytes if segmented.

        Args:
            path: Relative path or filename.

        Returns:
            memoryview slice of the file.
        """
        entry = self.get_file_entry(path)
        file_size = entry.file_size
        if file_size == 0:
            return memoryview(b"")

        if entry.is_contiguous and entry.block_count > 0:
            start_l0 = entry.starting_block // self.BLOCKS_PER_L0
            end_l0 = (entry.starting_block + entry.block_count - 1) // self.BLOCKS_PER_L0
            if start_l0 == end_l0:
                phys_off = self.get_physical_offset(entry.starting_block)
                if phys_off + file_size <= len(self._data):
                    return self._data[phys_off:phys_off + file_size]

        return memoryview(self.get_file_bytes(path))

    def extract_all_memory(self) -> Dict[str, bytes]:
        """
        Extract all files in the package directly into a dictionary mapping path -> bytes.

        Returns:
            Dictionary of {full_path: file_bytes}.
        """
        extracted = {}
        for entry in self._entries:
            if not entry.is_dir:
                extracted[entry.full_path] = self.get_file_bytes(entry.full_path)
        return extracted

    def get_thumbnail(self) -> Optional[bytes]:
        """
        Extract the embedded package thumbnail image (PNG/JPG) from header metadata if available.

        Returns:
            Thumbnail image bytes, or None if not present.
        """
        hdr = self._header
        if hdr.thumbnail_size > 0 and hdr.thumbnail_offset + hdr.thumbnail_size <= len(self._data):
            return bytes(self._data[hdr.thumbnail_offset:hdr.thumbnail_offset + hdr.thumbnail_size])
        return None

    def find_by_extension(self, ext: str) -> List[str]:
        """
        Find all file paths matching a specific extension (e.g. '.dta', '.mid', '.mogg', '.png_xbox').

        Args:
            ext: Extension with or without leading dot.

        Returns:
            List of matching full file paths.
        """
        clean_ext = ("." + ext.lstrip(".")).lower()
        return [e.full_path for e in self._entries if not e.is_dir and e.name.lower().endswith(clean_ext)]
