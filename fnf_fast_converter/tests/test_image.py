"""
Unit tests for Xbox 360 Milo DXT1 image decoder and PNG encoder.
"""

import io
import struct
import time
import pytest
from pathlib import Path
from PIL import Image
import numpy as np

from fnf_fast_converter.src.image import (
    decode_png_xbox,
    make_png_raw,
    PNG_SIGNATURE,
    clean_white_dots_array,
    clean_white_dot_artifacts,
    repair_album_file,
)


def create_synthetic_dxt1_xbox(
    c0_rgb: tuple[int, int, int],
    c1_rgb: tuple[int, int, int],
    indices_4x4: list[list[int]],
    width: int = 256,
    height: int = 256,
) -> bytes:
    """Helper to synthesize a valid 32-byte header + DXT1 Xbox 360 payload."""
    # Convert RGB888 to RGB565
    r0 = (c0_rgb[0] * 31 // 255) & 0x1F
    g0 = (c0_rgb[1] * 63 // 255) & 0x3F
    b0 = (c0_rgb[2] * 31 // 255) & 0x1F
    c0_565 = (r0 << 11) | (g0 << 5) | b0

    r1 = (c1_rgb[0] * 31 // 255) & 0x1F
    g1 = (c1_rgb[1] * 63 // 255) & 0x3F
    b1 = (c1_rgb[2] * 31 // 255) & 0x1F
    c1_565 = (r1 << 11) | (g1 << 5) | b1

    lookup = 0
    for py in range(4):
        for px in range(4):
            idx = indices_4x4[py][px] & 0x3
            shift = (py * 4 + px) * 2
            lookup |= (idx << shift)

    # Little-endian block
    block_le = struct.pack("<HHI", c0_565, c1_565, lookup)

    # Big-endian byte swap for Xbox 360
    block_be = bytearray(block_le)
    block_be[0::2], block_be[1::2] = block_be[1::2], block_be[0::2]

    num_blocks = (width * height) // 16
    payload = bytes(block_be) * num_blocks

    # 32-byte header (version=1, format=4, width, height/pitch=384)
    header = bytearray(32)
    header[0] = 1
    header[1] = 4
    struct.pack_into(">HH", header, 8, width, 384)

    return bytes(header) + payload


class TestImageDecoder:
    def test_decode_synthetic_solid_colors(self):
        # Solid Red block (indices all 0 -> color c0)
        solid_red = (255, 0, 0)
        black = (0, 0, 0)
        idx_grid = [[0] * 4 for _ in range(4)]

        raw_xbox = create_synthetic_dxt1_xbox(solid_red, black, idx_grid, 256, 256)
        png_bytes = decode_png_xbox(raw_xbox, 256, 256)

        assert png_bytes.startswith(PNG_SIGNATURE)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.size == (256, 256)
        assert img.mode == "RGB"

        arr = np.array(img)
        # Check pixel color is close to red (accounting for 5-bit RGB565 quant)
        assert arr[0, 0, 0] >= 240
        assert arr[0, 0, 1] <= 10
        assert arr[0, 0, 2] <= 10

    def test_decode_color_interpolation_c0_gt_c1(self):
        # c0 > c1: 4-color opaque mode
        c0 = (255, 255, 255)  # White
        c1 = (0, 0, 0)        # Black
        # Grid using all 4 palette slots
        idx_grid = [
            [0, 1, 2, 3],
            [0, 1, 2, 3],
            [0, 1, 2, 3],
            [0, 1, 2, 3],
        ]

        raw_xbox = create_synthetic_dxt1_xbox(c0, c1, idx_grid, 256, 256)
        png_bytes = decode_png_xbox(raw_xbox, 256, 256)
        img = Image.open(io.BytesIO(png_bytes))
        arr = np.array(img)

        # Slot 0: ~255 white
        assert arr[0, 0, 0] >= 250
        # Slot 1: ~0 black
        assert arr[0, 1, 0] <= 5
        # Slot 2: 2/3 c0 + 1/3 c1 (~170)
        assert 160 <= arr[0, 2, 0] <= 180
        # Slot 3: 1/3 c0 + 2/3 c1 (~85)
        assert 75 <= arr[0, 3, 0] <= 95

    def test_raw_png_passthrough(self):
        # If input is already PNG bytes, return directly
        sample_png = make_png_raw(np.zeros((64, 64, 3), dtype=np.uint8).tobytes(), 64, 64)
        result = decode_png_xbox(sample_png)
        assert result == sample_png

    def test_invalid_and_truncated_inputs(self):
        with pytest.raises(ValueError, match="Empty"):
            decode_png_xbox(b"")

        with pytest.raises(ValueError, match="Insufficient data"):
            decode_png_xbox(b"\x01\x04" + b"\x00" * 10)

    def test_png_chunk_structure(self):
        img_arr = np.zeros((128, 128, 3), dtype=np.uint8)
        img_arr[:, :, 1] = 200  # Green
        png_bytes = make_png_raw(img_arr.tobytes(), 128, 128)

        # Validate signature
        assert png_bytes[:8] == PNG_SIGNATURE
        # Validate IHDR
        assert png_bytes[12:16] == b"IHDR"
        w, h, depth, color_type = struct.unpack(">IIBB", png_bytes[16:26])
        assert w == 128
        assert h == 128
        assert depth == 8
        assert color_type == 2  # RGB
        # Validate IEND at end
        assert png_bytes[-12:-8] == struct.pack(">I", 0)
        assert png_bytes[-8:-4] == b"IEND"

        # Verify Pillow loads it flawlessly
        pil_img = Image.open(io.BytesIO(png_bytes))
        assert pil_img.size == (128, 128)

    def test_real_chart_buddy_holly(self):
        ref_path = Path(r"P:\Charts\Fortnite Festival\Weezer - Buddy Holly\album.png")
        con_path = Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB\Weezer - Buddy Holly")

        if not (ref_path.exists() and con_path.exists()):
            pytest.skip("Reference files not found on current test machine.")

        # Extract png_xbox block from CON
        con_data = con_path.read_bytes()
        # Offset 0xc36000 found in inspection
        offset = con_data.find(bytes([1, 4, 8, 0, 0, 0, 4, 0, 1, 0, 1, 128]))
        assert offset != -1

        raw_png_xbox = con_data[offset : offset + 32800]
        decoded_png = decode_png_xbox(raw_png_xbox, 256, 256)

        decoded_img = Image.open(io.BytesIO(decoded_png)).convert("RGB")
        ref_img = Image.open(ref_path).convert("RGB")

        diff = np.abs(np.array(decoded_img, dtype=float) - np.array(ref_img, dtype=float))
        mean_diff = np.mean(diff)
        max_diff = np.max(diff)

        print(f"Buddy Holly Mean pixel diff: {mean_diff:.4f}, Max diff: {max_diff:.1f}")
        assert mean_diff < 1.0  # Perfect sub-1.0 RGB quantization tolerance
        assert max_diff <= 2.0

    def test_performance(self):
        idx_grid = [[0, 1, 2, 3], [3, 2, 1, 0], [1, 2, 3, 0], [2, 3, 0, 1]]
        raw_xbox = create_synthetic_dxt1_xbox((200, 100, 50), (10, 20, 30), idx_grid, 256, 256)

        t0 = time.perf_counter()
        iters = 50
        for _ in range(iters):
            decode_png_xbox(raw_xbox, 256, 256)
        t1 = time.perf_counter()

        avg_ms = (t1 - t0) / iters * 1000
        print(f"Average DXT1 image decode time: {avg_ms:.3f} ms")
        assert avg_ms < 5.0  # Must be well under 5ms

    def test_white_dot_cleaning_isolated_pixels(self):
        # Background: dark magenta (100, 20, 80)
        arr = np.full((64, 64, 3), [100, 20, 80], dtype=np.uint8)
        # Add 5 isolated white dots
        arr[10, 10] = [255, 255, 255]
        arr[20, 30] = [255, 255, 255]
        arr[40, 50] = [255, 255, 255]
        arr[5, 5] = [255, 255, 255]
        arr[5, 6] = [255, 255, 255]  # 2-pixel isolated pair

        cleaned, fixed = clean_white_dots_array(arr)
        assert fixed == 5
        assert np.all(cleaned[10, 10] == [100, 20, 80])
        assert np.all(cleaned[20, 30] == [100, 20, 80])
        assert np.all(cleaned[40, 50] == [100, 20, 80])
        assert np.all(cleaned[5, 5] == [100, 20, 80])
        assert np.all(cleaned[5, 6] == [100, 20, 80])

    def test_white_dot_cleaning_preserves_white_text_and_lines(self):
        arr = np.full((64, 64, 3), [30, 30, 30], dtype=np.uint8)
        # 3x3 white block (letter stroke)
        arr[10:13, 10:13] = [255, 255, 255]
        # Vertical 1-pixel white line (stem)
        arr[20:30, 20] = [255, 255, 255]

        cleaned, fixed = clean_white_dots_array(arr)
        # Zero pixels modified because they are continuous text/lines
        assert fixed == 0
        assert np.all(cleaned[11, 11] == [255, 255, 255])
        assert np.all(cleaned[25, 20] == [255, 255, 255])

    def test_repair_album_file_on_disk(self, tmp_path):
        # Create a test image on disk with white dots
        test_img = Image.new("RGB", (64, 64), color=(60, 120, 180))
        test_arr = np.array(test_img)
        test_arr[15, 15] = [255, 255, 255]
        file_path = tmp_path / "album.png"
        Image.fromarray(test_arr).save(file_path, format="PNG")

        repaired, count = repair_album_file(file_path)
        assert repaired is True
        assert count == 1

        # Check repaired image
        with Image.open(file_path) as reloaded:
            arr_after = np.array(reloaded)
            assert np.all(arr_after[15, 15] == [60, 120, 180])

        # Second run should report 0 fixed (idempotent)
        repaired2, count2 = repair_album_file(file_path)
        assert repaired2 is False
        assert count2 == 0

    def test_decode_synthetic_dxt5(self):
        # Green and Blue block
        green = (0, 255, 0)
        blue = (0, 0, 255)
        idx_grid = [
            [0, 1, 2, 3],
            [3, 2, 1, 0],
            [0, 1, 2, 3],
            [3, 2, 1, 0],
        ]
        raw_dxt5 = create_synthetic_dxt5_xbox(green, blue, idx_grid, 64, 64)
        png_bytes = decode_png_xbox(raw_dxt5, 64, 64)

        assert png_bytes.startswith(PNG_SIGNATURE)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.size == (64, 64)
        assert img.mode == "RGB"

        arr = np.array(img)
        # Pixel (0, 0) is slot 0 -> Green
        assert arr[0, 0, 1] >= 240
        assert arr[0, 0, 2] <= 15
        # Pixel (0, 1) is slot 1 -> Blue
        assert arr[0, 1, 2] >= 240
        assert arr[0, 1, 1] <= 15

    def test_dxt5_python_fallback_matches_numpy(self):
        color0 = (220, 140, 80)
        color1 = (30, 70, 190)
        idx_grid = [
            [0, 2, 1, 3],
            [1, 3, 0, 2],
            [2, 0, 3, 1],
            [3, 1, 2, 0],
        ]
        raw_dxt5 = create_synthetic_dxt5_xbox(color0, color1, idx_grid, 32, 32)
        payload = bytearray(raw_dxt5[32 : 32 + (32 * 32)])
        payload[0::2], payload[1::2] = payload[1::2], payload[0::2]

        from fnf_fast_converter.src.image import decode_dxt5_numpy, decode_dxt5_python
        res_numpy = decode_dxt5_numpy(payload, 32, 32)
        res_python = decode_dxt5_python(payload, 32, 32)

        assert res_numpy == res_python

    def test_is_corrupted_album_art_detection(self):
        from fnf_fast_converter.src.image import is_corrupted_album_art
        # 1. Clean uniform image
        clean_img = Image.new("RGB", (64, 64), (120, 150, 180))
        buf = io.BytesIO()
        clean_img.save(buf, format="PNG")
        assert is_corrupted_album_art(buf.getvalue()) is False

        # 2. Naturally white image (like Big Poppa - Ready to Die)
        white_img = Image.new("RGB", (64, 64), (255, 255, 255))
        buf_w = io.BytesIO()
        white_img.save(buf_w, format="PNG")
        assert is_corrupted_album_art(buf_w.getvalue()) is False

        # 3. Corrupted image with alternating white checkerboard blocks
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        # Set every alternating 4x4 block to pure white
        for by in range(16):
            for bx in range(16):
                if (bx + by) % 2 == 0:
                    arr[by*4:(by+1)*4, bx*4:(bx+1)*4] = 255
        corrupted_img = Image.fromarray(arr)
        buf_c = io.BytesIO()
        corrupted_img.save(buf_c, format="PNG")
        assert is_corrupted_album_art(buf_c.getvalue()) is True


def create_synthetic_dxt5_xbox(
    c0_rgb: tuple[int, int, int],
    c1_rgb: tuple[int, int, int],
    indices_4x4: list[list[int]],
    width: int = 64,
    height: int = 64,
) -> bytes:
    """Helper to synthesize a valid 32-byte header + DXT5 Xbox 360 payload."""
    r0 = (c0_rgb[0] * 31 // 255) & 0x1F
    g0 = (c0_rgb[1] * 63 // 255) & 0x3F
    b0 = (c0_rgb[2] * 31 // 255) & 0x1F
    c0_565 = (r0 << 11) | (g0 << 5) | b0

    r1 = (c1_rgb[0] * 31 // 255) & 0x1F
    g1 = (c1_rgb[1] * 63 // 255) & 0x3F
    b1 = (c1_rgb[2] * 31 // 255) & 0x1F
    c1_565 = (r1 << 11) | (g1 << 5) | b1

    lookup = 0
    for py in range(4):
        for px in range(4):
            idx = indices_4x4[py][px] & 0x3
            shift = (py * 4 + px) * 2
            lookup |= (idx << shift)

    # 16-byte block: 8 bytes alpha (0xFF opaque) + 8 bytes color
    alpha_block = b"\xff\xff\x00\x00\x00\x00\x00\x00"
    color_block = struct.pack("<HHI", c0_565, c1_565, lookup)
    block_le = alpha_block + color_block

    # Big-endian byte swap for Xbox 360
    block_be = bytearray(block_le)
    block_be[0::2], block_be[1::2] = block_be[1::2], block_be[0::2]

    num_blocks = (width * height) // 16
    payload = bytes(block_be) * num_blocks

    # 32-byte header (version=1, bpp=24, enc=5, width, height)
    header = bytearray(32)
    header[0] = 1
    header[1] = 8
    header[2] = 24  # DXT5 bpp
    header[6] = 5   # DXT5 enc
    struct.pack_into(">HH", header, 8, width, height)

    return bytes(header) + payload


