"""Optional DeepFilterNet3 speech enhancement adapter.

Model/runtime stay outside the source tree. Importing this module never imports
torch and model inference runs with Hugging Face and Transformers offline.
DeepFilterNet enhances each input channel independently, preserving stereo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY = "https://github.com/Rikorose/DeepFilterNet"
RELEASE = "v0.5.6"
MODEL_URL = f"{REPOSITORY}/raw/refs/tags/{RELEASE}/models/DeepFilterNet3.zip"
MODEL_BYTES = 7_986_207
MODEL_SHA256 = "49c52edc8947ae1f9bf50d81530beaf3a2c3245aeaf34b6f31ff535cd22284d2"
PYTHON_PACKAGES = {"DeepFilterNet": "0.5.6", "torch": "2.6.0", "torchaudio": "2.6.0"}


def _home() -> Path:
    return Path(os.environ.get("REEL_STUDIO_DEEPFILTER_HOME", Path.home() / ".codex/work/reel-studio/models/deepfilter"))


def _paths() -> tuple[Path, Path, Path]:
    home = _home()
    python = home / "venv" / "bin" / "python"
    model = home / "DeepFilterNet3"
    marker = home / "ready.json"
    return python, model, marker


def available() -> bool:
    """Return true only after DeepFilterNet3 weights and inference were verified."""
    python, model, marker_path = _paths()
    checkpoint = model / "checkpoints" / "model_120.ckpt.best"
    config_path = model / "config.ini"
    if not (python.is_file() and checkpoint.is_file() and config_path.is_file() and marker_path.is_file()):
        return False
    try:
        marker = json.loads(marker_path.read_text())
        return (
            marker.get("worker_loaded") is True
            and marker.get("repository_release") == RELEASE
            and marker.get("checkpoint_sha256") == MODEL_SHA256
            and marker.get("python_path") == str(python.resolve())
            and marker.get("model_path") == str(model.resolve())
            and marker.get("model_bytes") == MODEL_BYTES
        )
    except (OSError, json.JSONDecodeError):
        return False


def enhance(input_path: Path, out_dir: Path) -> Path:
    """Run local speech enhancement and return a stereo 48 kHz WAV.

    The model is trained for speech denoising. It can alter speech and is not a
    music, laughter, or arbitrary sound selector. Model inference is offline.
    """
    if not available():
        raise RuntimeError(
            "Speech enhancement is not installed. Run python scripts/setup_speech.py "
            f"(the optional model is stored outside the project at {_home()})."
        )
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"Audio input does not exist: {source}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / "speech-enhanced.wav"
    python, model, _ = _paths()
    request = {"input": str(source.resolve()), "output": str(destination.resolve()), "model": str(model.resolve())}
    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    env["REEL_STUDIO_DEEPFILTER_HOME"] = str(_home().resolve())
    try:
        result = subprocess.run(
            [str(python), str(Path(__file__).resolve()), "--deepfilter-worker"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=out_dir,
            env=env,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("DeepFilterNet3 speech enhancement exceeded 30 minutes.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"DeepFilterNet3 could not enhance this audio. Details: {detail[-1800:]}")
    if not destination.is_file() or destination.stat().st_size < 128:
        raise RuntimeError("DeepFilterNet3 did not return an audio file.")

    meta = _probe(destination)
    if meta["sample_rate"] != 48_000 or meta["channels"] != 2:
        destination.unlink(missing_ok=True)
        raise RuntimeError("DeepFilterNet3 returned audio with an unexpected sample rate or channel count.")
    return destination


def _probe(path: Path) -> dict[str, int | float]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels,duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise RuntimeError("DeepFilterNet3 output contains no audio stream.")
    return {
        "sample_rate": int(streams[0]["sample_rate"]),
        "channels": int(streams[0]["channels"]),
        "duration": float(streams[0].get("duration", 0)),
    }


def _worker(request: dict[str, Any]) -> dict[str, Any]:
    """Heavy inference entry point. Runs only inside the dedicated environment."""
    import numpy as np
    import soundfile as sf
    import torch
    from df.enhance import enhance as enhance_audio, init_df
    from df.io import load_audio

    source = Path(request["input"])
    output = Path(request["output"])
    model_path = Path(request["model"])
    model, df_state, _ = init_df(
        model_base_dir=str(model_path),
        post_filter=False,
        log_level="ERROR",
        log_file=None,
        config_allow_defaults=True,
    )
    audio, metadata = load_audio(str(source), sr=48_000, verbose=False)
    if audio.ndim != 2 or audio.shape[0] not in (1, 2):
        raise ValueError("Speech enhancement accepts mono or stereo audio.")
    original_frames = audio.shape[-1]
    with torch.inference_mode():
        output_audio = enhance_audio(model, df_state, audio, pad=True).detach().cpu()
    samples = output_audio.numpy()
    if samples.ndim != 2 or samples.shape[-1] != original_frames:
        raise RuntimeError("DeepFilterNet3 changed the audio channel or frame count.")
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.999:
        samples *= np.float32(0.999 / peak)
    if samples.shape[0] == 1:
        samples = np.repeat(samples, 2, axis=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), samples.T, 48_000, subtype="FLOAT")
    return {
        "input_frames_48k": int(original_frames),
        "output_frames_48k": int(samples.shape[-1]),
        "input_channels": int(metadata.num_channels),
        "output_channels": int(samples.shape[0]),
        "peak": float(np.max(np.abs(samples))),
        "model": "DeepFilterNet3",
    }


def _main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "--deepfilter-worker":
        raise SystemExit(2)
    try:
        response = _worker(json.load(sys.stdin))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


if __name__ == "__main__" and "--deepfilter-worker" in sys.argv:
    raise SystemExit(_main())
