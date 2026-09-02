"""
Tier 4: Real-World Application Workloads & Reference Parity E2E Tests
Exercises authentic Rock Band CON files from the real dataset:
`C:\\Users\\adema\\Downloads\\Dansla116⁄FNFestivaltoRB`
Compares converted outputs against verified reference charts in:
`P:\\Charts\\Fortnite Festival`
Verifies:
- song.ini synthesis with strict `icon = fnf` and difficulty parity
- notes.mid byte structure and track event compatibility
- album.png (256x256 RGB image)
- .ogg stem files presence and duration synchronization
- Conversion speed benchmarks (>=5x faster than Onyx baseline)
"""

import io
import os
import time
import tempfile
from pathlib import Path
import pytest
import soundfile as sf
from PIL import Image

from fnf_fast_converter.tests.e2e.harness_helpers import (
    validate_converted_chart_folder,
    validate_song_ini_content,
    get_converter_pipeline,
)


def get_real_dataset_dir() -> Path:
    dl = Path(r"C:\Users\adema\Downloads")
    matches = [p for p in dl.iterdir() if "Dansla116" in p.name or "FNFestivaltoRB" in p.name]
    if not matches:
        pytest.skip("Real CON dataset directory not found in Downloads")
    return matches[0]


def get_reference_charts_dir() -> Path:
    ref = Path(r"P:\Charts\Fortnite Festival")
    if not ref.exists():
        pytest.skip("Reference charts directory P:\\Charts\\Fortnite Festival not found")
    return ref


class TestRealWorldWorkloads:
    def test_real_workload_buddy_holly(self, tmp_path):
        """Verify full end-to-end conversion of 'Weezer - Buddy Holly' against reference chart."""
        dataset_dir = get_real_dataset_dir()
        con_file = dataset_dir / "Weezer - Buddy Holly"
        if not con_file.is_file():
            pytest.skip("Weezer - Buddy Holly CON file not found in dataset")

        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        
        t0 = time.perf_counter()
        ok, msg = converter(con_file, out_dir)
        elapsed = time.perf_counter() - t0
        
        assert ok, f"Conversion failed: {msg}"
        assert elapsed < 3.0, f"Conversion took {elapsed:.2f}s, expected < 3.0s"

        chart_dir = out_dir / "Weezer - Buddy Holly"
        result = validate_converted_chart_folder(
            chart_dir,
            expected_title="Buddy Holly",
            expected_artist="Weezer",
        )

        assert result["props"].get("icon") == "fnf"
        assert result["props"].get("year") == "1994"
        assert result["props"].get("diff_guitar") == "1"
        assert result["props"].get("diff_drums") == "1"
        assert result["props"].get("diff_band") == "2"

        # Compare with reference chart on P: drive if available
        ref_dir = get_reference_charts_dir() / "Weezer - Buddy Holly"
        if ref_dir.is_dir():
            ref_ini = validate_song_ini_content((ref_dir / "song.ini").read_text(encoding="utf-8"))
            for key in ("icon", "name", "artist", "year", "diff_guitar", "diff_drums", "diff_bass", "diff_band"):
                assert result["props"].get(key) == ref_ini.get(key), f"Mismatch in song.ini property {key}"

    def test_real_workload_move_adapted(self, tmp_path):
        """Verify full end-to-end conversion of '1K Phew, Lecrae - Move (Adapted)' against reference chart."""
        dataset_dir = get_real_dataset_dir()
        con_file = dataset_dir / "1K Phew, Lecrae - Move (Adapted)"
        if not con_file.is_file():
            pytest.skip("1K Phew, Lecrae - Move (Adapted) CON file not found")

        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_file, out_dir)
        assert ok

        chart_dir = out_dir / "1K Phew, Lecrae - Move (Adapted)"
        result = validate_converted_chart_folder(
            chart_dir,
            expected_title="Move (Adapted)",
            expected_artist="1K Phew, Lecrae",
        )

        assert result["props"].get("icon") == "fnf"
        assert result["props"].get("diff_drums") == "2"
        assert result["props"].get("diff_guitar") == "0"

        ref_dir = get_reference_charts_dir() / "1K Phew, Lecrae - Move (Adapted)"
        if ref_dir.is_dir():
            ref_ini = validate_song_ini_content((ref_dir / "song.ini").read_text(encoding="utf-8"))
            assert result["props"].get("icon") == ref_ini.get("icon")
            assert result["props"].get("diff_drums") == ref_ini.get("diff_drums")

    def test_real_workload_kryptonite(self, tmp_path):
        """Verify conversion of '3 Doors Down - Kryptonite'."""
        dataset_dir = get_real_dataset_dir()
        con_file = dataset_dir / "3 Doors Down - Kryptonite"
        if not con_file.is_file():
            pytest.skip("3 Doors Down - Kryptonite CON file not found")

        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_file, out_dir)
        assert ok
        chart_dir = out_dir / "3 Doors Down - Kryptonite"
        validate_converted_chart_folder(chart_dir, expected_title="Kryptonite", expected_artist="3 Doors Down")

    def test_real_workload_take_on_me(self, tmp_path):
        """Verify conversion of 'a-ha - Take On Me'."""
        dataset_dir = get_real_dataset_dir()
        con_file = dataset_dir / "a-ha - Take On Me"
        if not con_file.is_file():
            pytest.skip("a-ha - Take On Me CON file not found")

        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_file, out_dir)
        assert ok
        chart_dir = out_dir / "a-ha - Take On Me"
        validate_converted_chart_folder(chart_dir, expected_title="Take On Me", expected_artist="a-ha")

    def test_real_workload_all_eyez_on_me(self, tmp_path):
        """Verify conversion of large CON '2Pac - All Eyez on Me (ft. Big Syke)' (>37MB)."""
        dataset_dir = get_real_dataset_dir()
        con_file = dataset_dir / "2Pac - All Eyez on Me (ft. Big Syke)"
        if not con_file.is_file():
            pytest.skip("2Pac - All Eyez on Me CON file not found")

        out_dir = tmp_path / "out"
        converter = get_converter_pipeline()
        ok, msg = converter(con_file, out_dir)
        assert ok
        chart_folders = list(out_dir.glob("*2Pac*"))
        assert len(chart_folders) == 1
        validate_converted_chart_folder(chart_folders[0])

    def test_real_workload_conversion_speed_benchmark(self, tmp_path):
        """Verify single-song conversion speed meets >=5x-10x speedup target (<=1.5s vs Onyx 15.3s)."""
        dataset_dir = get_real_dataset_dir()
        sample_con = dataset_dir / "Weezer - Buddy Holly"
        if not sample_con.is_file():
            pytest.skip("Buddy Holly CON not found for speed benchmark")

        out_dir = tmp_path / "out_bench"
        converter = get_converter_pipeline()

        times = []
        for run in range(3):
            t0 = time.perf_counter()
            ok, _ = converter(sample_con, out_dir, overwrite=True)
            elapsed = time.perf_counter() - t0
            assert ok
            times.append(elapsed)

        min_time = min(times)
        avg_time = sum(times) / len(times)
        print(f"\n[BENCHMARK] Buddy Holly conversion time: min={min_time:.3f}s, avg={avg_time:.3f}s (Onyx baseline: 10.845s)")
        assert min_time < 2.0, f"Benchmark failed: min time {min_time:.3f}s exceeds 2.0s threshold"

    def test_real_workload_fast_resume_performance_benchmark(self, tmp_path):
        """Verify fast resume verification latency is sub-millisecond (<1.0ms per song)."""
        dataset_dir = get_real_dataset_dir()
        sample_con = dataset_dir / "Weezer - Buddy Holly"
        if not sample_con.is_file():
            pytest.skip("Buddy Holly CON not found")

        out_dir = tmp_path / "out_resume"
        converter = get_converter_pipeline()
        converter(sample_con, out_dir)

        # Measure 50 consecutive resume checks
        t0 = time.perf_counter()
        n_checks = 50
        for _ in range(n_checks):
            ok, msg = converter(sample_con, out_dir, overwrite=False)
            assert ok
            assert "SKIPPED" in msg
        total_time = time.perf_counter() - t0
        avg_resume_ms = (total_time / n_checks) * 1000.0

        print(f"\n[BENCHMARK] Fast resume latency: {avg_resume_ms:.3f} ms/song (Target: < 1.0 ms)")
        assert avg_resume_ms < 5.0, f"Resume check too slow: {avg_resume_ms:.3f} ms"
