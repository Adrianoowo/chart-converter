#!/usr/bin/env python3
"""
FNF Fast Converter - GUI Application Entry Point
"""
import sys
import os
from pathlib import Path

# Add project root and package directories to sys.path
_current_dir = Path(__file__).resolve().parent
_repo_root = _current_dir
if (_current_dir / "fnf_fast_converter").is_dir():
    _repo_root = _current_dir
    _pkg_dir = _current_dir / "fnf_fast_converter"
elif (_current_dir.parent / "fnf_fast_converter").is_dir():
    _repo_root = _current_dir.parent
    _pkg_dir = _current_dir
else:
    _pkg_dir = _current_dir

for p in [str(_repo_root), str(_pkg_dir), str(_pkg_dir / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

def main():
    try:
        from fnf_fast_converter.src.gui import main as gui_main
        return gui_main()
    except ImportError:
        try:
            from src.gui import main as gui_main
            return gui_main()
        except ImportError:
            import importlib
            gui_module = importlib.import_module("gui")
            if hasattr(gui_module, "main"):
                return gui_module.main()
            raise

if __name__ == "__main__":
    sys.exit(main() or 0)
