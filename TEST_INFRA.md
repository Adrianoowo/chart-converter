# E2E Test Infra: CustomTkinter Desktop GUI for Fast CON to Clone Hero Converter

## Test Philosophy
- Opaque-box, requirement-driven, and headless testable.
- Methodology: Category-Partition + Boundary Value Analysis + Concurrency & Deadlock Stress + Real-World Headless UI Lifecycle Testing.
- Guarantees 100% test execution in CI/headless environments using `root.withdraw()` and pure Python models.

## Feature Inventory & Test Mapping
| # | Feature | Source | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Cross-Feature) | Tier 4 (Workloads) |
|---|---------|--------|:----------------:|:-----------------:|:----------------------:|:------------------:|
| F1 | Modern CTk Window | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| F2 | File Queue Table | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| F3 | File & Folder Ingestion | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| F4 | Windows Drag & Drop | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| F5 | Queue Controls | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| F6 | Non-blocking Async Worker | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| F7 | Row Status Indicators | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| F8 | Global Progress & Stats | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| F9 | Live Log Console Drawer | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| F10 | Inline Settings Controls | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |
| F11 | Advanced Settings Card | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |
| F12 | Queue Context Menu | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |
| F13 | Config JSON Persistence | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |
| F14 | One-Click Source Launcher | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ | ✓ |
| F15 | PyInstaller Packaging | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ | ✓ |

## Test Suites in `fnf_fast_converter/tests/test_gui.py`
1. **Tier 1: Feature Coverage (Unit & Model Isolation)**
   - Queue model addition, removal, clearing, state transitions
   - Fast scanner recursive directory traversal & DTA metadata extraction
   - Config JSON serialization & default fallbacks
   - Live stats calculation (songs/sec, ETA, elapsed time)
   - SafeStreamWriter redirection & log ring buffer
2. **Tier 2: Boundary & Corner Cases**
   - Empty queue operations, zero-byte / corrupt CON files in queue
   - Giant queues (1,000+ items) memory & iteration performance
   - Non-existent directories, permission errors, disk full simulation
   - Corrupt JSON config files & missing AppData directories
   - Fast cancel mid-conversion & worker pool teardown without orphan threads
3. **Tier 3: Cross-Feature Combinations & Concurrency**
   - Queue ingestion during active background conversion
   - Dynamic settings update (thread count, charter, overwrite) across conversions
   - Retry failed items while retaining completed items
   - Drag-and-drop ingestion with mixed valid/invalid files
4. **Tier 4: Headless CustomTkinter Lifecycle & End-to-End Workflows**
   - Headless `CTk` window creation with `root.withdraw()`, widget hierarchy validation
   - Simulated button events (Add Files, Start Conversion, Cancel, Clear All)
   - Event loop draining (`root.update()`) verifying no exceptions under windowed/headless modes
   - Complete end-to-end synthetic CON conversion through GUI worker pool to output folder with `song.ini` (`icon = fnf`) verification

## Coverage Thresholds
- Tier 1: >= 75 test cases
- Tier 2: >= 75 test cases
- Tier 3: >= 15 test cases
- Tier 4: >= 10 end-to-end workload cases
- All existing 212 core converter tests preserved at 100% pass rate.
