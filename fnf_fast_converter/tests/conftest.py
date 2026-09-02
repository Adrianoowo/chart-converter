import os
import sys
from pathlib import Path

# Ensure paths
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
ROOT_DIR = PROJECT_DIR.parent

for p in [str(ROOT_DIR), str(PROJECT_DIR), str(TESTS_DIR), str(PROJECT_DIR / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

tcl_dir = os.path.join(sys.base_prefix, "tcl", "tcl8.6")
tk_dir = os.path.join(sys.base_prefix, "tcl", "tk8.6")
if os.path.exists(tcl_dir):
    os.environ["TCL_LIBRARY"] = tcl_dir
if os.path.exists(tk_dir):
    os.environ["TK_LIBRARY"] = tk_dir
