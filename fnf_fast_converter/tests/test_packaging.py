"""
Test suite for FNF Fast Converter standalone packaging and distribution scripts.
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path
import pytest

# Ensure build_standalone can be imported
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

import build_standalone


class TestPackagingPipeline:
    def test_get_project_paths(self):
        paths = build_standalone.get_project_paths()
        assert "script_dir" in paths
        assert "repo_root" in paths
        assert "pkg_root" in paths
        assert "src_dir" in paths
        assert "dist_root" in paths
        assert "output_dir" in paths
        assert paths["output_dir"].name == "FNF_Fast_Converter"

    def test_check_prerequisites(self):
        # Should execute without raising RuntimeError since all packages are installed
        build_standalone.check_prerequisites()

    def test_build_pyinstaller_command_windowed(self):
        paths = build_standalone.get_project_paths()
        cmd = build_standalone.build_pyinstaller_command(paths, windowed=True)
        
        assert any("--name=FNF_Fast_Converter" in arg for arg in cmd)
        assert "--onedir" in cmd
        assert "--windowed" in cmd
        assert "--collect-all" in cmd
        assert "customtkinter" in cmd
        assert "soundfile" in cmd
        assert "PIL" in cmd
        assert "windnd" in cmd
        assert any("fnf_fast_converter" in arg for arg in cmd)

    def test_build_pyinstaller_command_console(self):
        paths = build_standalone.get_project_paths()
        cmd = build_standalone.build_pyinstaller_command(paths, windowed=False)
        
        assert "--console" in cmd
        assert "--windowed" not in cmd

    def test_copy_distribution_assets_and_launcher(self, tmp_path):
        out_dir = tmp_path / "FNF_Fast_Converter"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a mock README in repo_root
        mock_paths = {
            "repo_root": tmp_path,
            "pkg_root": tmp_path,
            "output_dir": out_dir,
        }
        (tmp_path / "README.md").write_text("# Test FNF Fast Converter\n", encoding="utf-8")
        
        build_standalone.copy_distribution_assets(mock_paths)
        
        assert (out_dir / "README.md").is_file()
        assert (out_dir / "README.txt").is_file()
        assert (out_dir / "Run_Converter.bat").is_file()
        
        launcher_content = (out_dir / "Run_Converter.bat").read_text(encoding="utf-8")
        assert "FNF_Fast_Converter.exe" in launcher_content

    def test_verify_distribution_success(self, tmp_path):
        out_dir = tmp_path / "FNF_Fast_Converter"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Create mock executable and files
        exe = out_dir / "FNF_Fast_Converter.exe"
        exe.write_bytes(b"MZ_MOCK_EXE_DATA")
        
        ctk_file = out_dir / "_internal" / "customtkinter" / "theme.json"
        ctk_file.parent.mkdir(parents=True, exist_ok=True)
        ctk_file.write_text("{}", encoding="utf-8")
        
        sf_file = out_dir / "_internal" / "soundfile.py"
        sf_file.write_text("# mock", encoding="utf-8")
        
        mock_paths = {
            "output_dir": out_dir,
        }
        
        # Should succeed without exception
        build_standalone.verify_distribution(mock_paths)

    def test_verify_distribution_missing_exe_raises(self, tmp_path):
        out_dir = tmp_path / "FNF_Fast_Converter"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        mock_paths = {
            "output_dir": out_dir,
        }
        
        with pytest.raises(FileNotFoundError):
            build_standalone.verify_distribution(mock_paths)

    def test_verify_distribution_zero_byte_exe_raises(self, tmp_path):
        out_dir = tmp_path / "FNF_Fast_Converter"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "FNF_Fast_Converter.exe").write_bytes(b"")
        
        mock_paths = {
            "output_dir": out_dir,
        }
        
        with pytest.raises(ValueError, match="0 bytes"):
            build_standalone.verify_distribution(mock_paths)


class TestBatchLaunchers:
    def test_run_gui_bat_exists(self):
        root_bat = _repo_root / "Run_GUI.bat"
        pkg_bat = _pkg_root / "Run_GUI.bat"
        assert root_bat.is_file(), "Run_GUI.bat must exist at workspace root"
        assert pkg_bat.is_file(), "Run_GUI.bat must exist in fnf_fast_converter"
        
        content = root_bat.read_text(encoding="utf-8")
        assert "customtkinter" in content
        assert "soundfile" in content
        assert "numpy" in content
        assert "PIL" in content
        assert "windnd" in content
        assert "python.exe" in content or "PYTHON_EXE" in content

    def test_build_standalone_bat_exists(self):
        root_bat = _repo_root / "build_standalone.bat"
        pkg_bat = _pkg_root / "build_standalone.bat"
        assert root_bat.is_file(), "build_standalone.bat must exist at workspace root"
        assert pkg_bat.is_file(), "build_standalone.bat must exist in fnf_fast_converter"
        
        content = root_bat.read_text(encoding="utf-8")
        assert "build_standalone.py" in content
        assert "PyInstaller" in content or "pyinstaller" in content
