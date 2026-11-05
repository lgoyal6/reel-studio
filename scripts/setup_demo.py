#!/usr/bin/env python3
"""Prepare a 12-second public Drumeo sample without storing media in the repository."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, help="Use an existing 12-second clip instead of downloading")
parser.add_argument("--data-dir", type=Path, default=Path.home() / ".codex/work/reel-studio/data")
args = parser.parse_args()
cache = args.data_dir.expanduser() / "cache"
cache.mkdir(parents=True, exist_ok=True)
if args.source:
    shutil.copyfile(args.source, cache / "demo.mp4")
else:
    with tempfile.TemporaryDirectory(prefix="reel-demo-") as temp:
        source = Path(temp) / "source.mp4"
        subprocess.run(["yt-dlp", "--no-playlist", "-f", "best[ext=mp4]/best", "-o", str(source),
                        "https://www.instagram.com/reel/C-LQOCqvX-I/"], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "30", "-i", str(source), "-t", "12",
                        "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(cache / "demo.mp4")], check=True)
(cache / "provenance.json").write_text(json.dumps({"source": "https://www.instagram.com/reel/C-LQOCqvX-I/",
    "uploader": "Drumeo", "source_range_seconds": [30, 42], "purpose": "Private editing demonstration"}, indent=2))
print(f"Sample ready: {cache / 'demo.mp4'}")
