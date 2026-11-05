"""Set up optional AudioSep weights and its isolated Python runtime.

This downloads approximately 960 MB of public weights and installs a second
Python environment. The main Reel Studio environment stays lightweight.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "backend/reelstudio/semantic.py"
STATE = Path(os.environ.get("REEL_STUDIO_AUDIOSEP_HOME", Path.home() / ".codex/work/reel-studio/models/audiosep"))
SOURCE = Path(os.environ.get("AUDIOSEP_REPO", STATE / "source"))
VENV = STATE / "venv"
PYTHON = VENV / "bin/python"
WEIGHTS = Path(os.environ.get("AUDIOSEP_CHECKPOINT", STATE / "pytorch_model.bin"))
REPO_COMMIT = "944583f18b84589dc965de3ad77525c945334252"
WEIGHTS_SHA256 = "37f1691fb067e2575f1ad1cfbfe44b7b3da18e52f33fcb2b0937b72952f11ba1"
WEIGHTS_SIZE = 957_134_817


def run(args: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_weights() -> None:
    if WEIGHTS.is_file() and WEIGHTS.stat().st_size == WEIGHTS_SIZE and sha256(WEIGHTS) == WEIGHTS_SHA256:
        return
    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    temporary = WEIGHTS.with_suffix(".download")
    url = "https://huggingface.co/nielsr/audiosep-demo/resolve/main/pytorch_model.bin?download=true"
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
        while True:
            block = response.read(8 * 1024 * 1024)
            if not block:
                break
            out.write(block)
            digest.update(block)
            total += len(block)
            print(f"Downloaded {total / WEIGHTS_SIZE:.0%} of the AudioSep checkpoint", flush=True)
    if total != WEIGHTS_SIZE or digest.hexdigest() != WEIGHTS_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("AudioSep checkpoint size or SHA-256 did not match the public model file.")
    temporary.replace(WEIGHTS)


def main() -> int:
    if shutil.which("uv") is None:
        raise SystemExit("Install uv first, then run this setup script again.")
    STATE.mkdir(parents=True, exist_ok=True)
    if not SOURCE.is_dir():
        run(["git", "clone", "--filter=blob:none", "https://github.com/Audio-AGI/AudioSep.git", str(SOURCE)])
    run(["git", "fetch", "--depth", "1", "origin", REPO_COMMIT], cwd=SOURCE)
    run(["git", "checkout", "--detach", REPO_COMMIT], cwd=SOURCE)
    if not (SOURCE / "config/audiosep_base.yaml").is_file():
        raise RuntimeError("The cloned AudioSep source does not contain its published config.")

    run(["uv", "python", "install", "3.11"])
    if not PYTHON.is_file():
        run(["uv", "venv", "--python", "3.11", str(VENV)])
    run([
        "uv", "pip", "install", "--python", str(PYTHON),
        "torch==2.6.0", "torchaudio==2.6.0", "numpy==1.26.4", "librosa==0.11.0",
        "soundfile==0.14.0", "transformers==5.22.1", "pytorch-lightning==2.6.6",
        "lightning==2.6.6", "huggingface-hub==2.4.0", "torchlibrosa==0.1.0",
        "timm==1.0.31", "PyYAML==6.0.3", "h5py==3.16.0", "pandas==3.0.6",
        "braceexpand==0.1.7", "webdataset==1.0.2", "wget==3.2", "ftfy==6.3.1",
    ])
    download_weights()

    env = os.environ.copy()
    env["HF_HOME"] = str(STATE / "hf")
    env["HF_HUB_OFFLINE"] = "0"
    env["TRANSFORMERS_OFFLINE"] = "0"
    env["TOKENIZERS_PARALLELISM"] = "false"
    env["REEL_STUDIO_AUDIOSEP_HOME"] = str(STATE)
    env["AUDIOSEP_REPO"] = str(SOURCE)
    env["AUDIOSEP_CHECKPOINT"] = str(WEIGHTS)
    env["AUDIOSEP_PYTHON"] = str(PYTHON)

    # CLAP_Encoder and its text tokenizer are constructed by the worker. The
    # latter needs only the small tokenizer/config assets; its trained tensor
    # weights are loaded from the verified AudioSep checkpoint above.
    worker_request = json.dumps({
        "repo": str(SOURCE),
        "checkpoint": str(WEIGHTS),
        "verify_only": True,
    })
    verified = subprocess.run(
        [str(PYTHON), str(WORKER), "--audiosep-worker"],
        input=worker_request,
        text=True,
        cwd=SOURCE,
        env=env,
        timeout=900,
        capture_output=True,
    )
    if verified.returncode:
        raise RuntimeError(f"AudioSep model readiness check failed:\n{verified.stderr[-4000:]}")
    print(verified.stdout.strip())

    marker = {
        "repository": "https://github.com/Audio-AGI/AudioSep",
        "repository_commit": REPO_COMMIT,
        "checkpoint": "https://huggingface.co/nielsr/audiosep-demo",
        "checkpoint_sha256": WEIGHTS_SHA256,
        "checkpoint_bytes": WEIGHTS_SIZE,
        "checkpoint_path": str(WEIGHTS.resolve()),
        "worker_loaded": True,
    }
    (STATE / "ready.json").write_text(json.dumps(marker, indent=2))
    print("AudioSep setup is ready. To enable it in this shell, set:")
    print(f"  REEL_STUDIO_AUDIOSEP_HOME={STATE}")
    print(f"  AUDIOSEP_REPO={SOURCE}")
    print(f"  AUDIOSEP_CHECKPOINT={WEIGHTS}")
    print(f"  AUDIOSEP_PYTHON={PYTHON}")
    print("The editor will run inference offline and only when a free-text sound target is requested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
