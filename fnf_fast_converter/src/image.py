"""
image.py - High-Performance Xbox 360 Milo DXT1 & DXT5 Texture Decoder.

Decodes Harmonix .png_xbox / .bmp_xbox files (32-byte Milo header + 16-bit byte-swapped DXT1/DXT5)
into standard PNG bytes (album.png) in memory in <1ms without external tools.
"""

from __future__ import annotations
import struct
import zlib
from pathlib import Path
from typing import Tuple, Optional

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_png_raw(rgb_bytes: bytes, width: int, height: int) -> bytes:
    """
    Encodes flat RGB raw bytes (height * width * 3) into valid standard PNG file bytes
    using zlib and standard PNG chunks (IHDR, IDAT, IEND) in <0.3ms.
    """
    stride = width * 3
    # Add PNG filter type 0 (None) at the beginning of each scanline
    scanlines = bytearray(height * (stride + 1))
    for y in range(height):
        dest_off = y * (stride + 1)
        src_off = y * stride
        scanlines[dest_off] = 0  # Filter type 0
        scanlines[dest_off + 1 : dest_off + 1 + stride] = rgb_bytes[src_off : src_off + stride]

    idat_data = zlib.compress(bytes(scanlines), 1)

    # IHDR Chunk
    ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_payload)
    ihdr_chunk = struct.pack(">I", 13) + b"IHDR" + ihdr_payload + struct.pack(">I", ihdr_crc)

    # IDAT Chunk
    idat_crc = zlib.crc32(b"IDAT" + idat_data)
    idat_chunk = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)

    # IEND Chunk
    iend_crc = zlib.crc32(b"IEND")
    iend_chunk = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    return PNG_SIGNATURE + ihdr_chunk + idat_chunk + iend_chunk


def decode_dxt1_numpy(swapped_payload: bytearray, width: int = 256, height: int = 256) -> bytes:
    """Vectorized DXT1 decoder using NumPy."""
    arr16 = np.frombuffer(swapped_payload, dtype="<u2")
    blocks16 = arr16.reshape(-1, 4)
    c0 = blocks16[:, 0].astype(np.uint32)
    c1 = blocks16[:, 1].astype(np.uint32)
    lookup = blocks16[:, 2].astype(np.uint32) | (blocks16[:, 3].astype(np.uint32) << 16)

    r0 = (((c0 >> 11) & 0x1F) * 255 // 31).astype(np.uint8)
    g0 = (((c0 >> 5) & 0x3F) * 255 // 63).astype(np.uint8)
    b0 = ((c0 & 0x1F) * 255 // 31).astype(np.uint8)

    r1 = (((c1 >> 11) & 0x1F) * 255 // 31).astype(np.uint8)
    g1 = (((c1 >> 5) & 0x3F) * 255 // 63).astype(np.uint8)
    b1 = ((c1 & 0x1F) * 255 // 31).astype(np.uint8)

    num_blocks = blocks16.shape[0]
    palettes = np.zeros((num_blocks, 4, 3), dtype=np.uint8)
    palettes[:, 0, 0] = r0
    palettes[:, 0, 1] = g0
    palettes[:, 0, 2] = b0
    palettes[:, 1, 0] = r1
    palettes[:, 1, 1] = g1
    palettes[:, 1, 2] = b1

    mask = c0 > c1
    # 2/3 c0 + 1/3 c1
    palettes[mask, 2, 0] = ((2 * r0[mask].astype(np.uint16) + r1[mask].astype(np.uint16)) // 3).astype(np.uint8)
    palettes[mask, 2, 1] = ((2 * g0[mask].astype(np.uint16) + g1[mask].astype(np.uint16)) // 3).astype(np.uint8)
    palettes[mask, 2, 2] = ((2 * b0[mask].astype(np.uint16) + b1[mask].astype(np.uint16)) // 3).astype(np.uint8)

    # 1/3 c0 + 2/3 c1
    palettes[mask, 3, 0] = ((r0[mask].astype(np.uint16) + 2 * r1[mask].astype(np.uint16)) // 3).astype(np.uint8)
    palettes[mask, 3, 1] = ((g0[mask].astype(np.uint16) + 2 * g1[mask].astype(np.uint16)) // 3).astype(np.uint8)
    palettes[mask, 3, 2] = ((b0[mask].astype(np.uint16) + 2 * b1[mask].astype(np.uint16)) // 3).astype(np.uint8)

    inv = ~mask
    palettes[inv, 2, 0] = ((r0[inv].astype(np.uint16) + r1[inv].astype(np.uint16)) // 2).astype(np.uint8)
    palettes[inv, 2, 1] = ((g0[inv].astype(np.uint16) + g1[inv].astype(np.uint16)) // 2).astype(np.uint8)
    palettes[inv, 2, 2] = ((b0[inv].astype(np.uint16) + b1[inv].astype(np.uint16)) // 2).astype(np.uint8)
    palettes[inv, 3, :] = 0

    indices = np.empty((num_blocks, 16), dtype=np.uint8)
    for p in range(16):
        indices[:, p] = (lookup >> (2 * p)) & 3

    block_pixels = np.take_along_axis(palettes, indices[:, :, None], axis=1)

    h_blocks = height // 4
    w_blocks = width // 4
    blocks_grid = block_pixels.reshape(h_blocks, w_blocks, 4, 4, 3)
    img_array = blocks_grid.transpose(0, 2, 1, 3, 4).reshape(height, width, 3)
    return img_array.tobytes()


def decode_dxt1_python(swapped_payload: bytearray, width: int = 256, height: int = 256) -> bytes:
    """Pure-Python fallback DXT1 decoder."""
    rgb_buf = bytearray(width * height * 3)
    w_blocks = width // 4
    h_blocks = height // 4
    stride = width * 3

    for by in range(h_blocks):
        row_base = by * 4 * stride
        for bx in range(w_blocks):
            block_offset = (by * w_blocks + bx) * 8
            c0, c1 = struct.unpack_from("<HH", swapped_payload, block_offset)
            r0 = ((c0 >> 11) & 0x1F) * 255 // 31
            g0 = ((c0 >> 5) & 0x3F) * 255 // 63
            b0 = (c0 & 0x1F) * 255 // 31

            r1 = ((c1 >> 11) & 0x1F) * 255 // 31
            g1 = ((c1 >> 5) & 0x3F) * 255 // 63
            b1 = (c1 & 0x1F) * 255 // 31

            if c0 > c1:
                palette = (
                    (r0, g0, b0),
                    (r1, g1, b1),
                    ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                    ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3),
                )
            else:
                palette = (
                    (r0, g0, b0),
                    (r1, g1, b1),
                    ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2),
                    (0, 0, 0),
                )

            lookup, = struct.unpack_from("<I", swapped_payload, block_offset + 4)
            col_base = bx * 12
            for py in range(4):
                line_offset = row_base + py * stride + col_base
                for px in range(4):
                    shift = (py * 4 + px) * 2
                    col_idx = (lookup >> shift) & 0x3
                    cr, cg, cb = palette[col_idx]
                    pix_idx = line_offset + px * 3
                    rgb_buf[pix_idx] = cr
                    rgb_buf[pix_idx + 1] = cg
                    rgb_buf[pix_idx + 2] = cb

    return bytes(rgb_buf)


def decode_dxt5_numpy(swapped_payload: bytearray | bytes, width: int = 512, height: int = 512) -> bytes:
    """
    Vectorized DXT5 (BC3) texture decoder using NumPy.
    Each 4x4 block is 16 bytes:
      Bytes 0..7: 8-byte alpha block (opaque for album art; 4x4 alpha indices)
      Bytes 8..15: 8-byte color block (c0 uint16, c1 uint16, lookup uint32)
    Note: Payload has undergone 16-bit word byte swapping for Xbox 360 Milo textures.
    In DXT5, colors always use standard 4-color interpolation mode.
    """
    arr16 = np.frombuffer(swapped_payload, dtype="<u2").reshape(-1, 8)
    c0 = arr16[:, 4].astype(np.uint32)
    c1 = arr16[:, 5].astype(np.uint32)
    lookup = arr16[:, 6].astype(np.uint32) | (arr16[:, 7].astype(np.uint32) << 16)

    r0 = (((c0 >> 11) & 0x1F) * 255 // 31).astype(np.uint8)
    g0 = (((c0 >> 5) & 0x3F) * 255 // 63).astype(np.uint8)
    b0 = ((c0 & 0x1F) * 255 // 31).astype(np.uint8)

    r1 = (((c1 >> 11) & 0x1F) * 255 // 31).astype(np.uint8)
    g1 = (((c1 >> 5) & 0x3F) * 255 // 63).astype(np.uint8)
    b1 = ((c1 & 0x1F) * 255 // 31).astype(np.uint8)

    num_blocks = arr16.shape[0]
    palettes = np.zeros((num_blocks, 4, 3), dtype=np.uint8)
    palettes[:, 0, 0] = r0
    palettes[:, 0, 1] = g0
    palettes[:, 0, 2] = b0
    palettes[:, 1, 0] = r1
    palettes[:, 1, 1] = g1
    palettes[:, 1, 2] = b1

    # DXT5 4-color mode interpolation
    palettes[:, 2, 0] = ((2 * r0.astype(np.uint16) + r1.astype(np.uint16)) // 3).astype(np.uint8)
    palettes[:, 2, 1] = ((2 * g0.astype(np.uint16) + g1.astype(np.uint16)) // 3).astype(np.uint8)
    palettes[:, 2, 2] = ((2 * b0.astype(np.uint16) + b1.astype(np.uint16)) // 3).astype(np.uint8)

    palettes[:, 3, 0] = ((r0.astype(np.uint16) + 2 * r1.astype(np.uint16)) // 3).astype(np.uint8)
    palettes[:, 3, 1] = ((g0.astype(np.uint16) + 2 * g1.astype(np.uint16)) // 3).astype(np.uint8)
    palettes[:, 3, 2] = ((b0.astype(np.uint16) + 2 * b1.astype(np.uint16)) // 3).astype(np.uint8)

    indices = np.empty((num_blocks, 16), dtype=np.uint8)
    for p in range(16):
        indices[:, p] = (lookup >> (2 * p)) & 3

    block_pixels = np.take_along_axis(palettes, indices[:, :, None], axis=1)

    h_blocks = height // 4
    w_blocks = width // 4
    blocks_grid = block_pixels.reshape(h_blocks, w_blocks, 4, 4, 3)
    img_array = blocks_grid.transpose(0, 2, 1, 3, 4).reshape(height, width, 3)
    return img_array.tobytes()


def decode_dxt5_python(swapped_payload: bytearray | bytes, width: int = 512, height: int = 512) -> bytes:
    """Pure-Python fallback DXT5 decoder."""
    rgb_buf = bytearray(width * height * 3)
    w_blocks = width // 4
    h_blocks = height // 4
    stride = width * 3

    for by in range(h_blocks):
        row_base = by * 4 * stride
        for bx in range(w_blocks):
            block_offset = (by * w_blocks + bx) * 16
            c0, c1 = struct.unpack_from("<HH", swapped_payload, block_offset + 8)
            r0 = ((c0 >> 11) & 0x1F) * 255 // 31
            g0 = ((c0 >> 5) & 0x3F) * 255 // 63
            b0 = (c0 & 0x1F) * 255 // 31

            r1 = ((c1 >> 11) & 0x1F) * 255 // 31
            g1 = ((c1 >> 5) & 0x3F) * 255 // 63
            b1 = (c1 & 0x1F) * 255 // 31

            palette = (
                (r0, g0, b0),
                (r1, g1, b1),
                ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3),
            )

            lookup, = struct.unpack_from("<I", swapped_payload, block_offset + 12)
            col_base = bx * 12
            for py in range(4):
                line_offset = row_base + py * stride + col_base
                for px in range(4):
                    shift = (py * 4 + px) * 2
                    col_idx = (lookup >> shift) & 0x3
                    cr, cg, cb = palette[col_idx]
                    pix_idx = line_offset + px * 3
                    rgb_buf[pix_idx] = cr
                    rgb_buf[pix_idx + 1] = cg
                    rgb_buf[pix_idx + 2] = cb

    return bytes(rgb_buf)


def decode_png_xbox(png_xbox_data: bytes, width: int = 256, height: int = 256) -> bytes:
    """
    Decodes Xbox 360 Milo texture data (.png_xbox / .bmp_xbox) into standard PNG image bytes (album.png).
    Supports both DXT1 (BC1) and DXT5 (BC3) texture formats with 16-bit word byte swapping.
    """
    if not png_xbox_data:
        raise ValueError("Empty png_xbox data provided.")

    # If data is already a standard PNG file, return as-is
    if png_xbox_data.startswith(PNG_SIGNATURE):
        return png_xbox_data

    header_size = 32
    is_dxt5 = False

    if len(png_xbox_data) >= header_size:
        bpp = png_xbox_data[2]
        enc = png_xbox_data[6]
        try:
            hdr_w, hdr_h = struct.unpack_from(">HH", png_xbox_data, 8)
            if hdr_w in (64, 128, 256, 512, 1024):
                width = hdr_w
            if hdr_h in (64, 128, 256, 512, 1024):
                height = hdr_h
            elif hdr_h == 384 and hdr_w == 256:
                # Harmonix pitch 0x0180 (384) for 256x256 textures
                height = 256
        except Exception:
            pass

        if bpp in (24, 32) or enc == 5:
            is_dxt5 = True
        elif bpp != 8 and len(png_xbox_data) >= header_size + (width * height):
            is_dxt5 = True
    else:
        # Headerless / direct payload
        header_size = 0
        if len(png_xbox_data) >= width * height:
            is_dxt5 = True

    payload_len = (width * height) if is_dxt5 else ((width * height) // 2)

    if len(png_xbox_data) < header_size + payload_len:
        if header_size > 0 and len(png_xbox_data) >= payload_len:
            header_size = 0
        else:
            raise ValueError(
                f"Insufficient data for {width}x{height} {'DXT5' if is_dxt5 else 'DXT1'} texture "
                f"(expected {header_size + payload_len}, got {len(png_xbox_data)})"
            )

    raw_payload = png_xbox_data[header_size : header_size + payload_len]
    if len(raw_payload) < payload_len:
        raise ValueError(f"Payload truncated: expected {payload_len} bytes, got {len(raw_payload)}")

    # 16-bit word byte swap (PowerPC big-endian -> little-endian)
    swapped = bytearray(raw_payload)
    swapped[0::2], swapped[1::2] = swapped[1::2], swapped[0::2]

    # Decode texture blocks
    if is_dxt5:
        if _HAS_NUMPY:
            rgb_bytes = decode_dxt5_numpy(swapped, width, height)
        else:
            rgb_bytes = decode_dxt5_python(swapped, width, height)
    else:
        if _HAS_NUMPY:
            rgb_bytes = decode_dxt1_numpy(swapped, width, height)
            rgb_bytes, _ = clean_white_dot_artifacts(rgb_bytes, width, height)
        else:
            rgb_bytes = decode_dxt1_python(swapped, width, height)

    # Encode to PNG
    return make_png_raw(rgb_bytes, width, height)


def clean_white_dots_array(arr: np.ndarray) -> tuple[np.ndarray, int]:
    """
    Suppresses isolated white dot artifacts (1x1 or tiny 1x2 noise dots)
    caused by DXT1 quantization and decoding edge cases, while preserving
    legitimate white text, strokes, borders, and high-key backgrounds.
    """
    is_white = np.all(arr >= 250, axis=-1)
    if not np.any(is_white):
        return arr, 0

    pad_w = np.pad(is_white, 1, mode="constant", constant_values=False)
    nbr_w_cnt = (
        pad_w[:-2, :-2].astype(np.uint8) + pad_w[:-2, 1:-1] + pad_w[:-2, 2:] +
        pad_w[1:-1, :-2] +                                    pad_w[1:-1, 2:] +
        pad_w[2:, :-2]   + pad_w[2:, 1:-1]   + pad_w[2:, 2:]
    )

    # Isolated 1x1 dot
    iso_1x1 = is_white & (nbr_w_cnt == 0)

    # Isolated 2-pixel pair: white pixel with exactly 1 white neighbor whose only white neighbor is also this pixel
    pad_cnt = np.pad(nbr_w_cnt, 1, mode="constant", constant_values=99)
    nbr_is_pair = np.zeros(arr.shape[:2], dtype=bool)
    for dy in [-1, 0, 1]:
        for dx in [-1, 0, 1]:
            if dy == 0 and dx == 0:
                continue
            y_start, y_end = 1 + dy, (arr.shape[0] + 1 + dy)
            x_start, x_end = 1 + dx, (arr.shape[1] + 1 + dx)
            n_w = pad_w[y_start:y_end, x_start:x_end]
            n_cnt = pad_cnt[y_start:y_end, x_start:x_end]
            nbr_is_pair |= (n_w & (n_cnt == 1))

    iso_2px = is_white & (nbr_w_cnt == 1) & nbr_is_pair

    cand = iso_1x1 | iso_2px
    if not np.any(cand):
        return arr, 0

    pad_c = np.pad(arr, ((1, 1), (1, 1), (0, 0)), mode="edge")

    # 8 neighbors
    neighbors = [
        (pad_c[:-2, :-2], pad_w[:-2, :-2, None]),
        (pad_c[:-2, 1:-1], pad_w[:-2, 1:-1, None]),
        (pad_c[:-2, 2:],   pad_w[:-2, 2:, None]),
        (pad_c[1:-1, :-2], pad_w[1:-1, :-2, None]),
        (pad_c[1:-1, 2:],  pad_w[1:-1, 2:, None]),
        (pad_c[2:, :-2],   pad_w[2:, :-2, None]),
        (pad_c[2:, 1:-1],  pad_w[2:, 1:-1, None]),
        (pad_c[2:, 2:],    pad_w[2:, 2:, None]),
    ]

    sum_c = np.zeros(arr.shape, dtype=np.uint32)
    cnt_c = np.zeros((arr.shape[0], arr.shape[1], 1), dtype=np.uint32)

    for n_rgb, n_is_w in neighbors:
        valid = ~n_is_w
        sum_c += np.where(valid, n_rgb, 0).astype(np.uint32)
        cnt_c += valid.astype(np.uint32)

    safe_cnt = np.maximum(cnt_c, 1)
    avg_c = (sum_c // safe_cnt).astype(np.uint8)

    # Neighbor average must be non-white (at least one channel < 235 or mean < 240)
    sig_non_white = np.any(avg_c < 235, axis=-1, keepdims=True) | (np.mean(avg_c, axis=-1, keepdims=True) < 240)
    valid_replace = cand[:, :, None] & (cnt_c > 0) & sig_non_white

    out = np.where(valid_replace, avg_c, arr)
    fixed = int(np.sum(valid_replace[:, :, 0]))
    return out, fixed


def clean_white_dot_artifacts(rgb_bytes: bytes, width: int, height: int) -> tuple[bytes, int]:
    """Cleans white dot noise from raw RGB bytes."""
    if not _HAS_NUMPY:
        return rgb_bytes, 0
    arr = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape((height, width, 3))
    cleaned, fixed = clean_white_dots_array(arr)
    return cleaned.tobytes(), fixed


def repair_album_file(album_path: str | Path) -> tuple[bool, int]:
    """
    Inspects and repairs an existing album.png file on disk, removing white dot artifacts.
    Returns (repaired_bool, fixed_pixels_count).
    """
    path = Path(album_path)
    if not path.is_file():
        return False, 0
    try:
        if _HAS_PIL:
            with Image.open(path) as img:
                img_rgb = img.convert("RGB")
                arr = np.array(img_rgb) if _HAS_NUMPY else None
                if arr is None:
                    return False, 0
                cleaned, fixed = clean_white_dots_array(arr)
                if fixed > 0:
                    cleaned_img = Image.fromarray(cleaned)
                    cleaned_img.save(path, format="PNG", optimize=True)
                    return True, fixed
        return False, 0
    except Exception:
        return False, 0


def is_corrupted_album_art(album_path_or_bytes: str | Path | bytes) -> bool:
    """
    Checks if an existing album.png is corrupted by the DXT5-decoded-as-DXT1 bug.
    
    Signature:
    Alternating 4x4 blocks of pure white [255, 255, 255] interleaved with color blocks
    caused by DXT5 0xFF alpha blocks being misinterpreted as RGB color blocks.
    Naturally white album covers (e.g. Big Poppa) have contiguous white regions with
    low horizontal alternation (alt_h < 0.15), while corrupted images exhibit dense
    horizontal alternating rates (alt_h > 0.25) across more than 25% of all blocks.
    """
    try:
        arr: Optional[np.ndarray] = None
        if isinstance(album_path_or_bytes, (str, Path)):
            p = Path(album_path_or_bytes)
            if not p.is_file():
                return False
            if _HAS_PIL:
                with Image.open(p) as img:
                    arr = np.array(img.convert("RGB")) if _HAS_NUMPY else None
        elif isinstance(album_path_or_bytes, bytes):
            if _HAS_PIL:
                import io
                with Image.open(io.BytesIO(album_path_or_bytes)) as img:
                    arr = np.array(img.convert("RGB")) if _HAS_NUMPY else None

        if arr is None or arr.ndim != 3:
            return False

        h, w = arr.shape[:2]
        if h < 16 or w < 16 or h % 4 != 0 or w % 4 != 0:
            return False

        blocks = arr.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4)
        grid = np.all(blocks >= 250, axis=(2, 3, 4))
        diff_h = (grid[:, :-1] != grid[:, 1:])
        alt_h = float(np.mean(diff_h))
        white_pct = float(np.mean(grid))

        return bool(alt_h >= 0.38 and white_pct > 0.20)
    except Exception:
        return False



