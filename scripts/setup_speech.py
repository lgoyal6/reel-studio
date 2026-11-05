"""Set up optional DeepFilterNet3 speech enhancement.

The pinned Python environment and 8 MB official model are stored outside the
repository. This requires no GPU, account, paid API, or network during edits.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from reelstudio import speech  # noqa: E402


HOME = Path(os.environ.get("REEL_STUDIO_DEEPFILTER_HOME", Path.home() / ".codex/work/reel-studio/models/deepfilter"))
VENV = HOME / "venv"
PYTHON = VENV / "bin/python"
MODEL = HOME / "DeepFilterNet3"
MODEL_ARCHIVE = HOME / "DeepFilterNet3.zip"
PACKAGES = [
    "DeepFilterNet==0.5.6",
    "torch==2.6.0",
    "torchaudio==2.6.0",
    "soundfile==0.12.1",
]


def run(args: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_model() -> None:
    if (
        MODEL_ARCHIVE.is_file()
        and MODEL_ARCHIVE.stat().st_size == speech.MODEL_BYTES
        and sha256(MODEL_ARCHIVE) == speech.MODEL_SHA256
    ):
        return
    HOME.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_ARCHIVE.with_suffix(".download")
    digest, total = hashlib.sha256(), 0
    request = urllib.request.Request(speech.MODEL_URL, headers={"User-Agent": "Reel-Studio"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
        while block := response.read(1024 * 1024):
            output.write(block)
            digest.update(block)
            total += len(block)
            print(f"Downloaded DeepFilterNet3 model: {total / speech.MODEL_BYTES:.0%}", flush=True)
    if total != speech.MODEL_BYTES or digest.hexdigest() != speech.MODEL_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("The DeepFilterNet3 model size or SHA-256 did not match the pinned release.")
    temporary.replace(MODEL_ARCHIVE)


def extract_model() -> None:
    checkpoint = MODEL / "checkpoints/model_120.ckpt.best"
    config_path = MODEL / "config.ini"
    if checkpoint.is_file() and config_path.is_file():
        return
    if MODEL.exists():
        shutil.rmtree(MODEL)
    with zipfile.ZipFile(MODEL_ARCHIVE) as archive:
        names = archive.namelist()
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise RuntimeError("The pinned DeepFilterNet3 model contains an unsafe archive path.")
        expected = {
            "DeepFilterNet3/config.ini",
            "DeepFilterNet3/checkpoints/model_120.ckpt.best",
        }
        if not expected.issubset(names):
            raise RuntimeError("The pinned DeepFilterNet3 model is missing its config or checkpoint.")
        archive.extractall(HOME)


def _write_smoke_wav(path: Path) -> int:
    rate, frames, channels = 48_000, 8_192, 2
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        payload = bytearray()
        for i in range(frames):
            speech_like = int(3500 * __import__("math").sin(2 * __import__("math").pi * 440 * i / rate))
            noise = ((i * 1103515245 + 12345) >> 16) & 1023
            sample = max(-32768, min(32767, speech_like + noise - 512))
            payload.extend(struct.pack("<hh", sample, sample))
        wav.writeframes(payload)
    return frames


def smoke_test() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="reel-studio-deepfilter-") as directory:
        work = Path(directory)
        source, output = work / "smoke.wav", work / "enhanced.wav"
        expected_frames = _write_smoke_wav(source)
        request = {"input": str(source), "output": str(output), "model": str(MODEL)}
        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        worker = subprocess.run(
            [str(PYTHON), str(ROOT / "backend/reelstudio/speech.py"), "--deepfilter-worker"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=work,
            env=env,
            timeout=180,
        )
        if worker.returncode:
            raise RuntimeError(f"DeepFilterNet3 smoke inference failed:\n{worker.stderr[-2500:]}")
        result = json.loads(worker.stdout.splitlines()[-1])
        if not output.is_file() or result["output_frames_48k"] != expected_frames:
            raise RuntimeError("DeepFilterNet3 smoke inference changed the frame count.")
        if result["output_channels"] != 2 or not 0 <= result["peak"] <= 0.9991:
            raise RuntimeError("DeepFilterNet3 smoke inference returned invalid output.")
        metadata = speech._probe(output)
        if metadata["sample_rate"] != 48_000 or metadata["channels"] != 2:
            raise RuntimeError(f"Unexpected smoke test output metadata: {metadata}")
        return {"passed": True, **result, **metadata}


def main() -> int:
    if shutil.which("uv") is None:
        raise SystemExit("Install uv, then run this setup script again.")
    HOME.mkdir(parents=True, exist_ok=True)
    run(["uv", "python", "install", "3.11"])
    if not PYTHON.is_file():
        run(["uv", "venv", "--python", "3.11", str(VENV)], cwd=HOME)
    print(f"Installing pinned packages in {VENV}", flush=True)
    run(["uv", "pip", "install", "--python", str(PYTHON), *PACKAGES])
    download_model()
    extract_model()
    smoke = smoke_test()
    marker = {
        "worker_loaded": True,
        "repository": speech.REPOSITORY,
        "repository_release": speech.RELEASE,
        "model_url": speech.MODEL_URL,
        "model_bytes": speech.MODEL_BYTES,
        "checkpoint_sha256": speech.MODEL_SHA256,
        "python_path": str(PYTHON.resolve()),
        "python_version": subprocess.check_output([str(PYTHON), "--version"], text=True).strip(),
        "platform": platform.platform(),
        "model_path": str(MODEL.resolve()),
        "packages": PACKAGES,
        "smoke_test": smoke,
    }
    (HOME / "ready.json").write_text(json.dumps(marker, indent=2))
    print(json.dumps(marker, indent=2))
    print(f"DeepFilterNet3 is ready. Runtime and model are stored under {HOME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
