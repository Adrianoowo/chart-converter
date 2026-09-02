#!/usr/bin/env python3
"""
High-Speed Native Rock Band CON to Clone Hero Converter
Master E2E Test Suite Automated Runner (`run_e2e_tests.py`)

Usage:
  python run_e2e_tests.py [--tier 1,2,3,4] [--fast] [--verbose]
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

TEST_DIR = Path(__file__).resolve().parent

TIER_MAP = {
    1: ("Tier 1: Feature Coverage (STFS, MOGG, DTA, song.ini, DXT1, MIDI, CLI)", TEST_DIR / "test_tier1_features.py"),
    2: ("Tier 2: Boundary, Corner & Adversarial Cases", TEST_DIR / "test_tier2_boundaries.py"),
    3: ("Tier 3: Cross-Feature Pairwise Combinations", TEST_DIR / "test_tier3_combinations.py"),
    4: ("Tier 4: Real-World Workloads & Reference Parity", TEST_DIR / "test_tier4_real_workloads.py"),
}


class TierReporter:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = 0

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            if report.passed:
                self.passed += 1
            elif report.failed:
                self.failed += 1
            elif report.skipped:
                self.skipped += 1
        elif report.when in ("setup", "teardown") and report.failed:
            self.errors += 1


def run_tier(tier_num: int, tier_name: str, test_file: Path, verbose: bool = False) -> dict:
    print(f"\n================================================================================")
    print(f"▶ EXECUTING {tier_name.upper()}")
    print(f"  Source: {test_file.name}")
    print(f"================================================================================")

    reporter = TierReporter()
    args = [str(test_file), "-q"]
    if verbose:
        args.append("-v")
    else:
        args.append("--tb=short")

    t0 = time.perf_counter()
    exit_code = pytest.main(args, plugins=[reporter])
    duration = time.perf_counter() - t0

    status = "PASSED" if exit_code == 0 else "FAILED"
    print(f"  Result: [{status}] — {reporter.passed} passed, {reporter.failed} failed, {reporter.skipped} skipped in {duration:.2f}s")
    
    return {
        "tier": tier_num,
        "name": tier_name,
        "status": status,
        "exit_code": exit_code,
        "passed": reporter.passed,
        "failed": reporter.failed,
        "skipped": reporter.skipped,
        "duration": duration,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Master E2E Test Suite Runner for fnf_fast_converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tier", "-t",
        type=str,
        default="1,2,3,4",
        help="Comma-separated tier numbers to execute (e.g. 1,2 or 1,2,3,4). Default: all",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose pytest test-by-test logging",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skips Tier 4 real CON conversion stress benchmarks",
    )

    args = parser.parse_args()

    selected_tiers = []
    for part in args.tier.split(","):
        part = part.strip()
        if part.isdigit() and int(part) in TIER_MAP:
            t_num = int(part)
            if args.fast and t_num == 4:
                continue
            selected_tiers.append(t_num)

    selected_tiers = sorted(list(set(selected_tiers)))
    if not selected_tiers:
        print("[ERROR] No valid test tiers selected.")
        return 1

    print("\n╔══════════════════════════════════════════════════════════════════════════════╗")
    print("║        HIGH-SPEED ROCK BAND CON TO CLONE HERO CONVERTER E2E TEST SUITE       ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝")
    print(f"Execution Target: {TEST_DIR}")
    print(f"Selected Tiers: {selected_tiers}")
    print(f"Fast Mode: {args.fast}")

    suite_t0 = time.perf_counter()
    tier_results = []

    for t_num in selected_tiers:
        t_name, t_file = TIER_MAP[t_num]
        res = run_tier(t_num, t_name, t_file, verbose=args.verbose)
        tier_results.append(res)

    suite_duration = time.perf_counter() - suite_t0

    # Diagnostics Summary Table
    print("\n\n" + "═" * 80)
    print("                          E2E TEST EXECUTION SUMMARY                            ")
    print("═" * 80)
    print(f"{'Tier':<8} | {'Description':<42} | {'Result':<8} | {'Passed':<6} | {'Time':<6}")
    print("─" * 80)

    total_passed = 0
    total_failed = 0
    total_skipped = 0
    all_passed = True

    for r in tier_results:
        total_passed += r["passed"]
        total_failed += r["failed"]
        total_skipped += r["skipped"]
        if r["status"] != "PASSED":
            all_passed = False
        
        tier_label = f"Tier {r['tier']}"
        desc = r["name"].split(":")[1].strip() if ":" in r["name"] else r["name"]
        desc_short = desc[:40]
        status_color = "PASS" if r["status"] == "PASSED" else "FAIL"
        print(f"{tier_label:<8} | {desc_short:<42} | {status_color:<8} | {r['passed']:<6} | {r['duration']:.2f}s")

    print("═" * 80)
    print(f"TOTAL: {total_passed} Passed, {total_failed} Failed, {total_skipped} Skipped across {len(tier_results)} Tiers in {suite_duration:.2f}s")
    
    if all_passed and total_failed == 0:
        print("\n🏆 VERDICT: ALL E2E TEST SUITES PASSED (100% SUCCESS)\n")
        return 0
    else:
        print("\n❌ VERDICT: E2E TEST SUITE FAILURES DETECTED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
