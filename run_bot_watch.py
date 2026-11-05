import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
WATCHED_ROOT_FILES = {
    ".env",
    ".env.example",
    "I_PAY_User_Guide.pdf",
    "knowledge_base.md",
    "requirements.txt",
}
POLL_INTERVAL_SECONDS = 0.5
CHANGE_DEBOUNCE_SECONDS = 0.7
SHUTDOWN_TIMEOUT_SECONDS = 10
FileState = Tuple[int, int]


def watched_files(root: Path = PROJECT_ROOT) -> Dict[str, FileState]:
    files = {}
    candidates = [*root.glob("*.py")]
    candidates.extend(root / name for name in WATCHED_ROOT_FILES)
    custom_images = root / "custom_images"
    if custom_images.is_dir():
        candidates.extend(custom_images.iterdir())
    for path in candidates:
        if path.is_file():
            stat = path.stat()
            files[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return files


def start_bot() -> subprocess.Popen:
    bot_path = PROJECT_ROOT / "bot.py"
    print("Starting iPay bot...", flush=True)
    return subprocess.Popen([sys.executable, str(bot_path)], cwd=PROJECT_ROOT)


def stop_bot(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run() -> None:
    previous_files = watched_files()
    process = start_bot()
    try:
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            if process is not None and process.poll() is not None:
                print(
                    f"Bot exited with status {process.returncode}; "
                    "waiting for a project change before restarting.",
                    flush=True,
                )
                process = None
            current_files = watched_files()
            if current_files == previous_files:
                continue

            time.sleep(CHANGE_DEBOUNCE_SECONDS)
            current_files = watched_files()
            if current_files == previous_files:
                continue

            changed = sorted(
                name
                for name in set(previous_files) | set(current_files)
                if previous_files.get(name) != current_files.get(name)
            )
            print(
                "Detected project changes: "
                + ", ".join(changed)
                + ". Restarting the bot.",
                flush=True,
            )
            stop_bot(process)
            previous_files = current_files
            process = start_bot()
    except KeyboardInterrupt:
        print("Stopping bot watcher...", flush=True)
    finally:
        stop_bot(process)


if __name__ == "__main__":
    run()
