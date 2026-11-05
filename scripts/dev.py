#!/usr/bin/env python3
"""Launch the local workstation after its frontend is built."""
import argparse
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8787)
parser.add_argument("--reload", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
if not (root / "frontend/dist/index.html").is_file():
    raise SystemExit("Build the frontend first: cd frontend && npm ci && npm run build")
command = ["uv", "run", "uvicorn", "reelstudio.app:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(args.port)]
if args.reload:
    command.append("--reload")
subprocess.run(command, cwd=root, env=os.environ.copy(), check=True)
