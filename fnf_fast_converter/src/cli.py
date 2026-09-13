"""
cli.py - Command-Line Interface and High-Speed Batch Converter Runner.

Provides the CLI interface for fnf_fast_converter:
- Multi-threaded scanning and parallel conversion.
- Rich live progress reporting with songs/sec throughput, ETA, and elapsed time.
- Sub-millisecond fast resume skipping already converted songs.
- Standalone benchmark mode comparing against Onyx baseline.
- Force overwrite, dry-run, quiet, and verbose diagnostic modes.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    from .pipeline import (
        BatchConversionProgress,
        BatchConversionStats,
        BatchConverter,
        convert_con_to_song_folder,
        is_valid_converted_song,
        sanitize_folder_name,
    )
except (ImportError, ValueError):
    pkg_dir = Path(__file__).resolve().parent.parent
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    from src.pipeline import (
        BatchConversionProgress,
        BatchConversionStats,
        BatchConverter,
        convert_con_to_song_folder,
        is_valid_converted_song,
        sanitize_folder_name,
    )

__version__ = "1.1.0"

# Reference baseline for speed comparisons (Onyx CLI average per song: 15.323s)
ONYX_BASELINE_SECONDS = 15.323


def format_time(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if seconds < 0:
        seconds = 0
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_bytes(bytes_count: int) -> str:
    """Format byte counts into human-readable strings (KB, MB, GB)."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def render_progress_bar(percent: float, width: int = 24) -> str:
    """Render an ASCII progress bar."""
    clamped = max(0.0, min(100.0, percent))
    filled_len = int(width * clamped / 100.0)
    bar = "█" * filled_len + "░" * (width - filled_len)
    return f"[{bar}]"


def run_benchmark(input_path: Path, output_dir: Path, charter: str = "Dansla116", num_workers: int = 4) -> int:
    """
    Execute benchmark suite measuring single-song latency, fast-resume latency,
    and batch conversion throughput vs the Onyx baseline.
    """
    print("=" * 72)
    print(f"  fnf_fast_converter v{__version__} — Performance Benchmark Suite")
    print("=" * 72)
    print(f"Input source:       {input_path}")
    print(f"Benchmark output:   {output_dir}")
    print(f"Logical CPU cores:  {os.cpu_count() or 'N/A'}")
    print(f"Worker threads:     {num_workers}")
    print("-" * 72)

    converter = BatchConverter(
        input_path=input_path,
        output_dir=output_dir,
        num_workers=num_workers,
        charter=charter,
        overwrite=True,
    )
    con_files = converter.scan_con_files()

    if not con_files:
        print("[ERROR] No valid CON package files found in input path for benchmark.")
        return 1

    sample_con = con_files[0]
    sample_size = sample_con.stat().st_size
    print(f"\nFound {len(con_files)} CON packages.")
    print(f"Primary Benchmark Target: '{sample_con.name}' ({format_bytes(sample_size)})")

    # 1. Single-Song Conversion Benchmark (3 iterations)
    print("\n[Phase 1/3] Measuring Single-Song Conversion Latency (3 iterations)...")
    single_times: List[float] = []
    for run in range(3):
        t0 = time.perf_counter()
        ok, msg = convert_con_to_song_folder(
            con_data_or_path=sample_con,
            output_dir=output_dir,
            charter=charter,
            overwrite=True,
            audio_threads=6,
        )
        elapsed = time.perf_counter() - t0
        if not ok:
            print(f"  Iteration {run + 1}: FAILED ({msg})")
        else:
            single_times.append(elapsed)
            print(f"  Iteration {run + 1}: {elapsed:.3f}s")

    if not single_times:
        print("[ERROR] Single song conversion benchmark failed.")
        return 1

    min_single = min(single_times)
    avg_single = sum(single_times) / len(single_times)
    speedup = ONYX_BASELINE_SECONDS / avg_single if avg_single > 0 else 0.0

    # 2. Fast Resume Latency Benchmark (50 iterations)
    print("\n[Phase 2/3] Measuring Fast Resume Latency (50 iterations)...")
    t0 = time.perf_counter()
    n_resume = 50
    for _ in range(n_resume):
        convert_con_to_song_folder(
            con_data_or_path=sample_con,
            output_dir=output_dir,
            charter=charter,
            overwrite=False,
        )
    resume_total = time.perf_counter() - t0
    avg_resume_ms = (resume_total / n_resume) * 1000.0
    print(f"  Average resume check latency: {avg_resume_ms:.3f} ms / song (<0.1ms per song target)")

    # 3. Batch Throughput Benchmark (Up to 10 sample songs)
    batch_sample_count = min(len(con_files), 10)
    batch_subset = con_files[:batch_sample_count]
    print(f"\n[Phase 3/3] Measuring Batch Conversion Throughput ({batch_sample_count} songs)...")

    batch_output = output_dir / "batch_bench"
    batch_conv = BatchConverter(
        input_path=input_path,
        output_dir=batch_output,
        num_workers=num_workers,
        charter=charter,
        overwrite=True,
    )
    # Override scan to subset
    batch_conv.scan_con_files = lambda: batch_subset  # type: ignore

    batch_stats = batch_conv.run()

    print("\n" + "=" * 72)
    print("  BENCHMARK SUMMARY RESULTS & ONYX COMPARISON")
    print("=" * 72)
    print(f"  • Single-Song Conversion Time (min):   {min_single:.3f} s")
    print(f"  • Single-Song Conversion Time (avg):   {avg_single:.3f} s")
    print(f"  • Onyx CLI Baseline Average:           {ONYX_BASELINE_SECONDS:.3f} s")
    print(f"  • Speedup over Onyx Baseline:          {speedup:.1f}x FASTER")
    print(f"  • Fast Resume Check Latency:           {avg_resume_ms:.3f} ms")
    print(f"  • Batch Throughput ({batch_sample_count} songs):           {batch_stats.songs_per_second:.2f} songs/sec ({batch_stats.mb_per_second:.1f} MB/sec)")
    print(f"  • Batch Total Duration:                {batch_stats.elapsed_seconds:.2f} s")
    print("=" * 72)

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="fnf_fast_converter",
        description="High-Speed Native Rock Band CON to Clone Hero Converter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  fnf_fast_converter -i C:\\CON_Files -o C:\\CloneHero_Songs
  fnf_fast_converter -i song.con -o C:\\CloneHero_Songs --force
  fnf_fast_converter -i C:\\CON_Files -o C:\\CloneHero_Songs --threads 16
  fnf_fast_converter -i C:\\CON_Files -o C:\\CloneHero_Songs --benchmark
        """,
    )

    parser.add_argument(
        "-i",
        "--input",
        dest="input",
        type=str,
        default=".",
        help="Path to input CON package file or directory of CON packages (default: current directory).",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output",
        type=str,
        default="./charts",
        help="Destination directory for converted Clone Hero song folders (default: './charts').",
    )
    parser.add_argument(
        "-t",
        "-j",
        "--threads",
        dest="threads",
        type=int,
        default=os.cpu_count() or 4,
        help=f"Number of parallel worker threads (default: logical CPU count, {os.cpu_count() or 4}).",
    )
    parser.add_argument(
        "-f",
        "--force",
        dest="force",
        action="store_true",
        default=False,
        help="Force overwrite of existing converted charts (disables fast resume).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Scan and report what would be converted without modifying files on disk.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Suppress live progress bars and print only final summary.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Enable detailed per-file logging.",
    )
    parser.add_argument(
        "--charter",
        dest="charter",
        type=str,
        default="Dansla116",
        help="Default charter name to embed in song.ini (default: 'Dansla116').",
    )
    parser.add_argument(
        "--quality",
        dest="quality",
        type=float,
        default=0.5,
        help="Vorbis audio compression quality between 0.0 and 1.0 (default: 0.5 ~160kbps).",
    )
    parser.add_argument(
        "--no-recursive",
        dest="no_recursive",
        action="store_true",
        default=False,
        help="Disable recursive scanning of input subdirectories.",
    )
    parser.add_argument(
        "--benchmark",
        dest="benchmark",
        action="store_true",
        default=False,
        help="Run benchmark suite measuring conversion latency and throughput vs Onyx.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit.",
    )

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"[ERROR] Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    # Benchmark mode handler
    if args.benchmark:
        return run_benchmark(
            input_path=input_path,
            output_dir=output_dir,
            charter=args.charter,
            num_workers=args.threads,
        )

    if not args.quiet:
        print("=" * 72)
        print(f"  fnf_fast_converter v{__version__} — Rock Band CON to Clone Hero")
        print("=" * 72)
        print(f"Input:        {input_path.resolve()}")
        print(f"Output:       {output_dir.resolve()}")
        print(f"Workers:      {args.threads}")
        print(f"Overwrite:    {args.force}")
        print(f"Dry Run:      {args.dry_run}")
        print(f"Vorbis Q:     {args.quality:.2f}")
        print("-" * 72)

    is_tty = sys.stdout.isatty() and not args.quiet

    def progress_callback(prog: BatchConversionProgress) -> None:
        if args.quiet:
            return

        bar = render_progress_bar(prog.percent_complete, width=20)
        sps_str = f"{prog.throughput_sps:.1f} songs/s" if prog.throughput_sps > 0 else "-- songs/s"
        elapsed_str = format_time(prog.elapsed_time)
        eta_str = format_time(prog.eta_seconds)

        line = (
            f"\r{bar} {prog.percent_complete:5.1f}% ({prog.processed}/{prog.total}) "
            f"| C: {prog.converted} S: {prog.skipped} F: {prog.failed} "
            f"| {sps_str} | Elapsed: {elapsed_str} | ETA: {eta_str}"
        )

        if is_tty:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            # Periodic output when stdout is not a TTY (every 10 items or on completion)
            if prog.processed % 10 == 0 or prog.processed == prog.total:
                print(line.strip())

    converter = BatchConverter(
        input_path=input_path,
        output_dir=output_dir,
        num_workers=args.threads,
        overwrite=args.force,
        charter=args.charter,
        quality=args.quality,
        dry_run=args.dry_run,
        recursive=not args.no_recursive,
        progress_callback=progress_callback,
    )

    t_start = time.perf_counter()
    stats = converter.run()
    t_end = time.perf_counter()

    if is_tty and not args.quiet:
        sys.stdout.write("\n")
        sys.stdout.flush()

    # Final summary report
    print("\n" + "=" * 72)
    print("  CONVERSION SUMMARY REPORT")
    print("=" * 72)
    print(f"  • Total Packages Scanned:  {stats.total_scanned}")
    print(f"  • Successfully Converted:  {stats.total_converted}")
    print(f"  • Skipped (Up to date):    {stats.total_skipped}")
    print(f"  • Failed / Corrupted:      {stats.total_failed}")
    print(f"  • Total Data Processed:    {format_bytes(stats.total_bytes)}")
    print(f"  • Total Elapsed Time:      {stats.elapsed_seconds:.2f} s")
    if stats.elapsed_seconds > 0:
        print(f"  • Average Throughput:      {stats.songs_per_second:.2f} songs/sec ({stats.mb_per_second:.1f} MB/sec)")
    print("=" * 72)

    if stats.errors:
        print(f"\n[WARNING] Encountered {len(stats.errors)} errors during conversion:")
        for fname, err in stats.errors[:10]:
            print(f"  - {fname}: {err}")
        if len(stats.errors) > 10:
            print(f"  ... and {len(stats.errors) - 10} more errors.")

    return 0 if stats.total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
