#!/usr/bin/env python3
"""
repair_library.py - Chart Library Maintenance and Repair Tool

Repairs Clone Hero / YARG chart libraries:
1. Eliminates white dot noise artifacts from broken Milo DXT1 album.png files.
2. Ensures all song.ini files contain 'icon = fnf' so YARG places them under
   'FNF' instead of 'Unknown Sources'.
3. Scans and reports duplicate songs or conflicting folders.
"""

from __future__ import annotations
import os
import sys
import argparse
import time
from pathlib import Path
from collections import defaultdict

# Add project root to sys.path so package imports work
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fnf_fast_converter.src.repair import repair_chart_library


def main():
    parser = argparse.ArgumentParser(
        description="Repair album art white dots, ensure icon = fnf in song.ini, and check duplicates."
    )
    default_dir = r"D:\Charts\Fortnite Festival"
    if not os.path.exists(default_dir):
        default_dir = str(PROJECT_ROOT)

    default_source = r"C:\Users\adema\Downloads\FNFestivaltoRB-main"
    if not os.path.exists(default_source):
        default_source = None

    parser.add_argument(
        "-d", "--dir",
        default=default_dir,
        help=f"Path to chart library folder (default: {default_dir})",
    )
    parser.add_argument(
        "-s", "--source-dir",
        default=default_source,
        help=f"Optional path to source CON packages to restore broken DXT5 covers (default: {default_source})",
    )
    parser.add_argument(
        "--icon",
        default="fnf",
        help="Icon tag to set in song.ini (default: 'fnf')",
    )
    parser.add_argument(
        "--no-art",
        action="store_true",
        help="Skip album art white dot repair",
    )
    parser.add_argument(
        "--no-icons",
        action="store_true",
        help="Skip song.ini icon repair",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without modifying any files on disk",
    )

    args = parser.parse_args()

    target_path = Path(args.dir)
    if not target_path.exists():
        print(f"[ERROR] Directory does not exist: {target_path}")
        sys.exit(1)

    print("=" * 65)
    print("  CHART LIBRARY REPAIR & MAINTENANCE UTILITY")
    print("=" * 65)
    print(f"Target Directory:    {target_path}")
    print(f"Source CON Directory: {args.source_dir or '(None)'}")
    print(f"Repair Album Art:    {not args.no_art}")
    print(f"Repair song.ini:     {not args.no_icons} (icon = {args.icon})")
    print(f"Dry Run Mode:        {args.dry_run}")
    print("-" * 65)

    def print_progress(current, total, name, current_stats):
        if current % 25 == 0 or current == total:
            pct = (current / total) * 100 if total else 100
            print(
                f"\r[{pct:5.1f}%] ({current}/{total}) "
                f"Art restored: {current_stats['images_restored_from_con']} | "
                f"Art cleaned: {current_stats['images_repaired']} | "
                f"INIs: {current_stats['inis_updated']} - {name[:24]:24s}",
                end="",
                flush=True,
            )

    stats = repair_chart_library(
        target_path,
        source_dir=args.source_dir,
        fix_art=not args.no_art,
        fix_icons=not args.no_icons,
        icon_tag=args.icon,
        dry_run=args.dry_run,
        progress_callback=print_progress,
    )

    print("\n" + "=" * 65)
    print("  REPAIR SUMMARY RESULTS")
    print("=" * 65)
    print(f"  • Total Songs Scanned:      {stats['total_songs']}")
    print(f"  • song.ini Checked:         {stats['inis_checked']}")
    print(f"  • song.ini Updated:         {stats['inis_updated']} (now with icon = {args.icon})")
    print(f"  • Album Images Checked:     {stats['images_checked']}")
    print(f"  • Album Images Repaired:    {stats['images_repaired']}")
    print(f"  • Restored from CONs:       {stats['images_restored_from_con']}")
    print(f"  • Noise Pixels Eliminated:  {stats['pixels_fixed']}")
    print(f"  • Elapsed Time:             {stats['elapsed_seconds']:.2f} s")

    if stats["duplicates"]:
        print("\n  DUPLICATE SONGS DETECTED:")
        for dup in stats["duplicates"]:
            print(f"    - '{dup['artist']} - {dup['title']}':")
            for f in dup["folders"]:
                print(f"        folder: {f}")
    else:
        print("\n  • No duplicate song collisions found.")

    if stats["errors"]:
        print(f"\n  Warnings / Errors ({len(stats['errors'])}):")
        for err in stats["errors"][:5]:
            print(f"    - {err}")
    print("=" * 65)


if __name__ == "__main__":
    main()
