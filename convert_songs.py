import os
import sys
import shutil
import subprocess
import tempfile
import re
import threading
import queue
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

ONYX_EXE = str(Path(__file__).resolve().parent / "onyx-command-line-20251011-windows-x64" / "onyx.exe")
SOURCE_DIR = Path(r"C:\Users\adema\Downloads\Dansla116⁄FNFestivaltoRB")
OUTPUT_DIR = Path(r"P:\Charts\Fortnite Festival")
TEMP_STAGING_DIR = Path(tempfile.gettempdir()) / "fnf_staging"

def is_valid_converted(song_dir: Path) -> bool:
    if not song_dir.is_dir():
        return False
    ini_file = song_dir / "song.ini"
    mid_file = song_dir / "notes.mid"
    if not (ini_file.exists() and mid_file.exists()):
        return False
    ogg_files = list(song_dir.glob("*.ogg"))
    return len(ogg_files) > 0

def convert_song_to_staging(song_path: Path, staging_root: Path, output_root: Path, skip_existing: bool = True) -> tuple[bool, str, Path | None, Path | None]:
    song_name = song_path.name
    final_song_dir = output_root / song_name
    
    # 0. Skip if already properly converted on destination drive
    if skip_existing and is_valid_converted(final_song_dir):
        # Ensure icon=fnf
        ini_path = final_song_dir / "song.ini"
        try:
            ini_content = ini_path.read_text(encoding="utf-8", errors="replace")
            if "icon" not in ini_content:
                if "[song]" in ini_content:
                    ini_content = ini_content.replace("[song]\n", "[song]\nicon = fnf\n", 1).replace("[song]\r\n", "[song]\r\nicon = fnf\r\n", 1)
                else:
                    ini_content += "\nicon = fnf\n"
                ini_path.write_text(ini_content, encoding="utf-8")
        except Exception:
            pass
        return True, f"[SKIPPED] Already converted: {song_name}", None, None

    # Staging path on fast NVMe C:
    song_staging_dir = staging_root / f"staged_{os.getpid()}_{song_name[:30]}"
    if song_staging_dir.exists():
        shutil.rmtree(song_staging_dir, ignore_errors=True)
    song_staging_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir) / "proj"
        
        # 1. Onyx Import (100% on NVMe C:)
        cmd_import = [ONYX_EXE, "import", str(song_path), "--to", str(proj_dir)]
        res = subprocess.run(cmd_import, capture_output=True, text=True)
        if res.returncode != 0:
            shutil.rmtree(song_staging_dir, ignore_errors=True)
            return False, f"[ERROR] Import failed for {song_name}: {res.stderr or res.stdout}", None, None
            
        yml_path = proj_dir / "song.yml"
        if not yml_path.exists():
            shutil.rmtree(song_staging_dir, ignore_errors=True)
            return False, f"[ERROR] song.yml not found for {song_name}", None, None
            
        # 2. Modify song.yml to target 'ps'
        content = yml_path.read_text(encoding="utf-8")
        if "targets:" in content:
            content = re.sub(r'targets:.*', 'targets:\n  ps:\n    game: ps\n', content, flags=re.DOTALL)
        else:
            content += "\ntargets:\n  ps:\n    game: ps\n"
            
        yml_path.write_text(content, encoding="utf-8")
        
        # 3. Onyx Build directly to NVMe staging directory
        cmd_build = [ONYX_EXE, "build", str(yml_path), "--target", "ps", "--to", str(song_staging_dir)]
        res_build = subprocess.run(cmd_build, capture_output=True, text=True)
        if res_build.returncode != 0:
            shutil.rmtree(song_staging_dir, ignore_errors=True)
            return False, f"[ERROR] Build failed for {song_name}: {res_build.stderr or res_build.stdout}", None, None
            
        # 4. Update song.ini to include icon=fnf
        ini_path = song_staging_dir / "song.ini"
        if ini_path.exists():
            ini_content = ini_path.read_text(encoding="utf-8", errors="replace")
            if "icon" not in ini_content:
                if "[song]" in ini_content:
                    ini_content = ini_content.replace("[song]\n", "[song]\nicon = fnf\n", 1).replace("[song]\r\n", "[song]\r\nicon = fnf\r\n", 1)
                else:
                    ini_content += "\nicon = fnf\n"
            else:
                ini_content = re.sub(r'icon\s*=.*', 'icon = fnf', ini_content)
                
            ini_path.write_text(ini_content, encoding="utf-8")
        else:
            shutil.rmtree(song_staging_dir, ignore_errors=True)
            return False, f"[WARNING] song.ini missing in build output for {song_name}", None, None

    # Return staged folder ready for sequential transfer to P:
    return True, f"[STAGED] {song_name}", song_staging_dir, final_song_dir

def _worker(args):
    song_path, staging_root, output_root, skip_existing = args
    return convert_song_to_staging(song_path, staging_root, output_root, skip_existing)

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    
    songs = [p for p in SOURCE_DIR.iterdir() if p.is_file()]
    total_songs = len(songs)
    print(f"Found {total_songs} songs in source folder.", flush=True)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--single":
        test_song = songs[0]
        for s in songs:
            if "Buddy Holly" in s.name:
                test_song = s
                break
        print(f"Testing conversion of 1 song: {test_song.name}", flush=True)
        ok, msg, staged_dir, final_dir = convert_song_to_staging(test_song, TEMP_STAGING_DIR, OUTPUT_DIR, skip_existing=False)
        if ok and staged_dir:
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            shutil.move(str(staged_dir), str(final_dir))
            print(f"[SUCCESS] Converted & transferred: {test_song.name}", flush=True)
        else:
            print(msg, flush=True)
        return

    cpu_count = os.cpu_count() or 4
    max_workers = min(16, cpu_count)
    print(f"Starting NVMe parallel conversion ({max_workers} workers) + Sequential HDD Writer...", flush=True)
    tasks = [(s, TEMP_STAGING_DIR, OUTPUT_DIR, True) for s in songs]
    
    success_count = 0
    skipped_count = 0
    failed = []
    
    # Threaded sequential writer for smooth HDD writing without head thrashing
    transfer_queue = queue.Queue()
    stop_event = threading.Event()
    
    def writer_thread():
        while not (stop_event.is_set() and transfer_queue.empty()):
            try:
                item = transfer_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            staged_dir, final_dir, s_name = item
            try:
                if final_dir.exists():
                    shutil.rmtree(final_dir, ignore_errors=True)
                shutil.move(str(staged_dir), str(final_dir))
            except Exception as e:
                print(f"[TRANSFER ERROR] {s_name}: {e}", flush=True)
            finally:
                transfer_queue.task_done()
                
    writer = threading.Thread(target=writer_thread, daemon=True)
    writer.start()
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, t): t[0].name for t in tasks}
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            done += 1
            song_name = futures[future]
            try:
                ok, msg, staged_dir, final_dir = future.result()
                if ok:
                    if staged_dir and final_dir:
                        transfer_queue.put((staged_dir, final_dir, song_name))
                        success_count += 1
                        print(f"[{done}/{total}] [CONVERTED] {song_name}", flush=True)
                    else:
                        skipped_count += 1
                        print(f"[{done}/{total}] {msg}", flush=True)
                else:
                    failed.append((song_name, msg))
                    print(f"[{done}/{total}] {msg}", flush=True)
            except Exception as e:
                failed.append((song_name, str(e)))
                print(f"[{done}/{total}] [EXCEPTION] {song_name}: {e}", flush=True)
                
    # Wait for remaining sequential transfers to finish
    transfer_queue.join()
    stop_event.set()
    writer.join()
    
    # Clean staging directory
    shutil.rmtree(TEMP_STAGING_DIR, ignore_errors=True)
                
    print("\n==========================================", flush=True)
    print(f"Summary: {total_songs} Total Songs", flush=True)
    print(f"  - Newly Converted: {success_count}", flush=True)
    print(f"  - Skipped (Already Done): {skipped_count}", flush=True)
    print(f"  - Failed: {len(failed)}", flush=True)
    if failed:
        print("\nFailed Songs:", flush=True)
        for name, err in failed:
            print(f"  - {name}: {err}", flush=True)
    print("==========================================", flush=True)

if __name__ == "__main__":
    main()
