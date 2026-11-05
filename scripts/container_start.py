#!/usr/bin/env python3
"""Start Reel Studio in a container after validating its persistent data path."""

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    data_dir = Path(os.environ.get("REEL_DATA_DIR", "/data/reel-studio")).expanduser()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"REEL_DATA_DIR is not writable: {data_dir}: {exc}", file=sys.stderr)
        return 1

    port = os.environ.get("PORT", "7860")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        print("PORT must be an integer from 1 to 65535", file=sys.stderr)
        return 2

    command = [sys.executable, "-m", "uvicorn", "reelstudio.app:app", "--app-dir", "backend",
               "--host", "0.0.0.0", "--port", port]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
