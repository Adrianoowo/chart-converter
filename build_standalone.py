#!/usr/bin/env python3
"""
FNF Fast Converter - Standalone PyInstaller Packaging Automation Script

Automates the compilation and packaging of FNF Fast Converter into a portable
standalone Windows directory distribution: dist/FNF_Fast_Converter/FNF_Fast_Converter.exe
"""

import os
import sys
import shutil
import subprocess
import argparse
import time
from pathlib import Path


def get_project_paths():
    """Determine repository and package roots based on script location."""
    script_dir = Path(__file__).resolve().parent
    
    if (script_dir / "fnf_fast_converter").is_dir():
        repo_root = script_dir
        pkg_root = script_dir / "fnf_fast_converter"
    elif (script_dir.parent / "fnf_fast_converter").is_dir():
        repo_root = script_dir.parent
        pkg_root = script_dir
    else:
        repo_root = script_dir
        pkg_root = script_dir
        
    return {
        "script_dir": script_dir,
        "repo_root": repo_root,
        "pkg_root": pkg_root,
        "src_dir": pkg_root / "src" if (pkg_root / "src").is_dir() else repo_root / "fnf_fast_converter" / "src",
        "dist_root": repo_root / "dist",
        "build_root": repo_root / "build",
        "output_dir": repo_root / "dist" / "FNF_Fast_Converter",
    }


def check_prerequisites():
    """Verify that required build tools and libraries are installed."""
    print("=" * 70)
    print(" 1. Verifying Build Prerequisites")
    print("=" * 70)
    
    required_modules = [
        ("PyInstaller", "pyinstaller"),
        ("customtkinter", "customtkinter"),
        ("soundfile", "soundfile"),
        ("PIL", "pillow"),
        ("numpy", "numpy"),
        ("windnd", "windnd"),
    ]
    
    missing = []
    for mod_import, mod_pkg in required_modules:
        try:
            mod = __import__(mod_import)
            ver = getattr(mod, "__version__", "installed")
            print(f"  [OK] {mod_import:16s} ({ver})")
        except ImportError:
            print(f"  [MISSING] {mod_import:12s} -> requires '{mod_pkg}'")
            missing.append(mod_pkg)
            
    if missing:
        print("\n[INFO] Installing missing build dependencies via pip...")
        cmd = [sys.executable, "-m", "pip", "install", *missing]
        res = subprocess.run(cmd)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to install dependencies: {missing}")
        print("  [OK] Missing dependencies installed successfully.")
        
    print()


def clean_previous_builds(paths):
    """Remove previous dist/build directories and spec files if requested."""
    print("=" * 70)
    print(" 2. Cleaning Previous Build Artifacts")
    print("=" * 70)
    
    dist_target = paths["output_dir"]
    build_target = paths["build_root"]
    spec_files = list(paths["repo_root"].glob("*.spec")) + list(paths["pkg_root"].glob("*.spec"))
    
    if dist_target.exists():
        print(f"  Removing existing distribution: {dist_target}")
        shutil.rmtree(dist_target, ignore_errors=True)
        
    if build_target.exists():
        print(f"  Removing build cache: {build_target}")
        shutil.rmtree(build_target, ignore_errors=True)
        
    for spec in spec_files:
        try:
            print(f"  Removing old spec file: {spec.name}")
            spec.unlink()
        except OSError:
            pass
            
    print("  [OK] Clean complete.\n")


def build_pyinstaller_command(paths, windowed=True):
    """Construct PyInstaller argument list with all necessary hooks and assets."""
    entry_point = paths["repo_root"] / "run_gui.py"
    if not entry_point.is_file():
        entry_point = paths["pkg_root"] / "run_gui.py"
    if not entry_point.is_file():
        entry_point = paths["src_dir"] / "gui.py"
        
    if not entry_point.is_file():
        raise FileNotFoundError(f"Cannot find GUI entry point script. Looked for run_gui.py and {paths['src_dir'] / 'gui.py'}")
        
    print(f"  Using Entry Point: {entry_point}")
    
    # Internal hidden imports to ensure all converter components are bundled
    hidden_imports = [
        "windnd",
        "customtkinter",
        "soundfile",
        "PIL",
        "PIL.Image",
        "PIL.PngImagePlugin",
        "numpy",
        "fnf_fast_converter",
        "fnf_fast_converter.src",
        "fnf_fast_converter.src.gui",
        "fnf_fast_converter.src.gui_app",
        "fnf_fast_converter.src.gui_views",
        "fnf_fast_converter.src.gui_worker",
        "fnf_fast_converter.src.gui_stats",
        "fnf_fast_converter.src.gui_dnd",
        "fnf_fast_converter.src.gui_scanner",
        "fnf_fast_converter.src.gui_queue",
        "fnf_fast_converter.src.gui_config",
        "fnf_fast_converter.src.pipeline",
        "fnf_fast_converter.src.stfs",
        "fnf_fast_converter.src.mogg",
        "fnf_fast_converter.src.dta",
        "fnf_fast_converter.src.image",
        "fnf_fast_converter.src.ini",
        "fnf_fast_converter.src.cli",
        "fnf_fast_converter.src.gui_web",
        "fnf_fast_converter.src.repair",
    ]
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=FNF_Fast_Converter",
        "--onedir",
        "--windowed" if windowed else "--console",
        "--clean",
        "--noconfirm",
        "--distpath", str(paths["dist_root"]),
        "--workpath", str(paths["build_root"]),
        "--specpath", str(paths["repo_root"]),
        "--paths", str(paths["repo_root"]),
        "--paths", str(paths["pkg_root"]),
        "--paths", str(paths["src_dir"]),
        "--collect-all", "customtkinter",
        "--collect-all", "soundfile",
        "--collect-all", "PIL",
    ]

    web_src = paths["src_dir"] / "web"
    if web_src.is_dir():
        cmd.extend(["--add-data", f"{web_src}{os.pathsep}fnf_fast_converter/src/web"])
        cmd.extend(["--add-data", f"{web_src}{os.pathsep}web"])
    
    for h in hidden_imports:
        cmd.extend(["--hidden-import", h])
        
    cmd.append(str(entry_point))
    return cmd


def execute_build(cmd):
    """Run PyInstaller build command and stream output."""
    print("=" * 70)
    print(" 3. Compiling Portable Standalone Package with PyInstaller")
    print("=" * 70)
    print(f"Command:\n  {' '.join(cmd)}\n")
    
    start_time = time.time()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1
    )
    
    for line in iter(process.stdout.readline, ""):
        print(f"  [PyInstaller] {line.rstrip()}")
        
    process.stdout.close()
    ret = process.wait()
    duration = time.time() - start_time
    
    if ret != 0:
        raise RuntimeError(f"PyInstaller build failed with exit code {ret}")
        
    print(f"\n  [OK] PyInstaller compilation completed in {duration:.2f} seconds.\n")


def copy_distribution_assets(paths):
    """Copy auxiliary documentation, launchers, and README files into dist folder."""
    print("=" * 70)
    print(" 4. Assembling Standalone Distribution Bundle")
    print("=" * 70)
    
    out_dir = paths["output_dir"]
    if not out_dir.is_dir():
        raise RuntimeError(f"Output directory does not exist: {out_dir}")
        
    # Copy README.md if present
    readme_src = paths["repo_root"] / "README.md"
    if not readme_src.is_file():
        readme_src = paths["pkg_root"] / "README.md"
        
    if readme_src.is_file():
        shutil.copy2(readme_src, out_dir / "README.md")
        shutil.copy2(readme_src, out_dir / "README.txt")
        print(f"  [+] Copied README to {out_dir / 'README.txt'}")

    # Copy Web UI assets to ensure presence
    web_src = paths["src_dir"] / "web"
    if web_src.is_dir():
        for target_sub in [
            out_dir / "web",
            out_dir / "_internal" / "web",
            out_dir / "_internal" / "fnf_fast_converter" / "src" / "web",
        ]:
            try:
                target_sub.parent.mkdir(parents=True, exist_ok=True)
                if target_sub.exists():
                    shutil.rmtree(target_sub)
                shutil.copytree(web_src, target_sub)
            except Exception:
                pass
        print(f"  [+] Bundled Web UI assets into standalone distribution")

    # Copy repair_library.py if present
    repair_src = paths["repo_root"] / "repair_library.py"
    if repair_src.is_file():
        shutil.copy2(repair_src, out_dir / "repair_library.py")
        print(f"  [+] Copied repair tool: {repair_src.name}")

    # Create convenience launchers inside standalone folder
    launcher_content = (
        "@echo off\r\n"
        "title FNF Fast Converter\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0FNF_Fast_Converter.exe\" %*\r\n"
        "exit /b 0\r\n"
    )
    launcher_path = out_dir / "Run_Converter.bat"
    launcher_path.write_text(launcher_content, encoding="utf-8")
    print(f"  [+] Generated standalone launcher: {launcher_path.name}")

    repair_launcher = (
        "@echo off\r\n"
        "title FNF Fast Converter - Library Repair\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0FNF_Fast_Converter.exe\" %*\r\n"
        "exit /b 0\r\n"
    )
    (out_dir / "Run_Repair.bat").write_text(repair_launcher, encoding="utf-8")
    print(f"  [+] Generated standalone repair launcher: Run_Repair.bat")

    # Generate release ZIP archive
    zip_base = paths["dist_root"] / "FNF_Fast_Converter-v1.1.2-windows-x64"
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=str(paths["dist_root"]), base_dir="FNF_Fast_Converter")
    print(f"  [+] Created distribution ZIP: {Path(zip_path).name} ({os.path.getsize(zip_path) / (1024*1024):.2f} MB)")
    print("  [OK] Bundle assembly complete.\n")


def verify_distribution(paths):
    """Validate that the output distribution is complete, valid, and functional."""
    print("=" * 70)
    print(" 5. Verifying Standalone Executable & Asset Integrity")
    print("=" * 70)
    
    out_dir = paths["output_dir"]
    exe_path = out_dir / "FNF_Fast_Converter.exe"
    
    if not exe_path.is_file():
        raise FileNotFoundError(f"Verification Failed: {exe_path} was not created!")
        
    exe_size_bytes = exe_path.stat().st_size
    if exe_size_bytes == 0:
        raise ValueError("Verification Failed: Executable is 0 bytes!")
        
    # Calculate total folder size and file count
    total_size = 0
    file_count = 0
    has_customtkinter_data = False
    has_soundfile_data = False
    
    for item in out_dir.rglob("*"):
        if item.is_file():
            file_count += 1
            total_size += item.stat().st_size
            rel_lower = str(item.relative_to(out_dir)).lower()
            if "customtkinter" in rel_lower:
                has_customtkinter_data = True
            if "soundfile" in rel_lower or "sndfile" in rel_lower:
                has_soundfile_data = True
                
    total_mb = total_size / (1024 * 1024)
    exe_mb = exe_size_bytes / (1024 * 1024)
    
    print(f"  Distribution Path : {out_dir}")
    print(f"  Main Executable   : {exe_path.name} ({exe_mb:.2f} MB)")
    print(f"  Total Files       : {file_count}")
    print(f"  Total Bundle Size : {total_mb:.2f} MB")
    print(f"  CustomTkinter Res : {'Found' if has_customtkinter_data else 'Not detected (may be internal)'}")
    print(f"  SoundFile DLLs    : {'Found' if has_soundfile_data else 'Not detected (may be internal)'}")
    print()
    print("=" * 70)
    print(" [SUCCESS] Standalone Distribution Packaging Complete!")
    print("=" * 70)
    print(f" Location: {out_dir}")
    print(f" Executable: {exe_path}")
    print(" Ready for portable distribution on any Windows machine.")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Build Standalone FNF Fast Converter Executable")
    parser.add_argument("--console", action="store_true", help="Build with console window visible (for debugging)")
    parser.add_argument("--no-clean", action="store_true", help="Skip cleaning previous build artifacts")
    args = parser.parse_args()
    
    paths = get_project_paths()
    
    try:
        check_prerequisites()
        if not args.no_clean:
            clean_previous_builds(paths)
        cmd = build_pyinstaller_command(paths, windowed=not args.console)
        execute_build(cmd)
        copy_distribution_assets(paths)
        verify_distribution(paths)
        return 0
    except Exception as exc:
        print(f"\n[FATAL ERROR] Build process failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
