#!/usr/bin/env python3
"""Install the pinned optional Demucs worker outside the repository."""
import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--directory", type=Path, default=Path.home() / ".codex/work/reel-studio/models/demucs")
args = parser.parse_args()
root = args.directory.expanduser().resolve()
if not (root / "bin/python").is_file():
    subprocess.run(["uv", "venv", "--python", "3.12", str(root)], check=True)
subprocess.run(["uv", "pip", "install", "--python", str(root / "bin/python"),
                "demucs==4.0.1", "numpy<2", "torch==2.5.1", "torchaudio==2.5.1", "soundfile>=0.13,<1"], check=True)
subprocess.run([str(root / "bin/python"), "-c", "from demucs.pretrained import get_model; get_model('htdemucs'); print('HT Demucs weights loaded')"], check=True)
print(f"Set REEL_DEMUCS_PYTHON={root / 'bin/python'} when launching the app.")
