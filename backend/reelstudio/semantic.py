"""Optional AudioSep language-guided target separation adapter.

The API app can run without PyTorch. Heavy model setup stays in a dedicated
Python environment, outside the source tree. No network access occurs at import
or inference time.
"""

from __future__ import annotations

import json
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _paths() -> tuple[Path, Path, Path]:
    state = Path(os.environ.get("REEL_STUDIO_AUDIOSEP_HOME", Path.home() / ".codex/work/reel-studio/models/audiosep"))
    repo = Path(os.environ.get("AUDIOSEP_REPO", state / "source"))
    checkpoint = Path(os.environ.get("AUDIOSEP_CHECKPOINT", state / "pytorch_model.bin"))
    python = Path(os.environ.get("AUDIOSEP_PYTHON", state / "venv/bin/python"))
    return repo, checkpoint, python


def _ready_marker() -> Path:
    state = Path(os.environ.get("REEL_STUDIO_AUDIOSEP_HOME", Path.home() / ".codex/work/reel-studio/models/audiosep"))
    return state / "ready.json"


def available() -> bool:
    """Return true only when source, checkpoint, and dedicated Python exist."""
    repo, checkpoint, python = _paths()
    files_exist = (
        repo.is_dir()
        and (repo / "config/audiosep_base.yaml").is_file()
        and checkpoint.is_file()
        and checkpoint.stat().st_size == 957_134_817
        and python.is_file()
        and _ready_marker().is_file()
    )
    if not files_exist:
        return False
    try:
        marker = json.loads(_ready_marker().read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker.get("worker_loaded") is True
        and marker.get("checkpoint_bytes") == checkpoint.stat().st_size
        and marker.get("checkpoint_path") == str(checkpoint.resolve())
        and marker.get("repository_commit") == "944583f18b84589dc965de3ad77525c945334252"
    )


def separate(audio: Path, target: str, out_dir: Path, *, cache_dir: Path | None = None) -> dict[str, Path]:
    """Reuse both stems only for identical input, prompt, and model identity."""
    if cache_dir is None:
        return _separate_uncached(audio, target, out_dir)
    if not available():
        return _separate_uncached(audio, target, out_dir)
    normalized = target.strip().lower()
    if not normalized or len(target) > 180:
        raise ValueError("Describe the sound to isolate in 1 to 180 characters.")
    _, checkpoint, _ = _paths()
    identity = {
        "target": normalized,
        "model": _ready_marker().read_text(),
        "checkpoint": [str(checkpoint.resolve()), checkpoint.stat().st_size, checkpoint.stat().st_mtime_ns],
        "worker": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode())
    with audio.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = cache_dir / digest.hexdigest()
    # flock also serializes separate worker processes, not just this interpreter.
    with (cache_dir / f"{digest.hexdigest()}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        names = {"target_path": "semantic-target.wav", "residual_path": "semantic-residual.wav"}
        if not all((entry / name).is_file() and (entry / name).stat().st_size > 44 for name in names.values()):
            shutil.rmtree(entry, ignore_errors=True)
            with tempfile.TemporaryDirectory(dir=cache_dir, prefix="pending-") as temp:
                pending = Path(temp) / "stems"
                _separate_uncached(audio, normalized, pending)
                os.replace(pending, entry)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in names.values():
            shutil.copyfile(entry / name, out_dir / name)
        return {key: out_dir / name for key, name in names.items()}


def _separate_uncached(audio: Path, target: str, out_dir: Path) -> dict[str, Path]:
    """Separate a free-text source using AudioSep; return target and residual WAVs.

    AudioSep's model accepts mono 32 kHz input. To preserve compatibility with
    the editor, the output target is resampled and duplicated to stereo 48 kHz;
    the residual is computed against the original stereo input. This cannot
    preserve the original stereo image for the target and is surfaced in notes.
    """
    if not available():
        state = Path(os.environ.get("REEL_STUDIO_AUDIOSEP_HOME", Path.home() / ".codex/work/reel-studio/models/audiosep"))
        raise RuntimeError(
            "Text-guided sound separation is not installed. Run "
            "`python scripts/setup_semantic.py` to set up AudioSep "
            f"(model files are stored outside the project at {state})."
        )
    if not audio.is_file():
        raise FileNotFoundError(f"Audio input does not exist: {audio}")
    if not target.strip() or len(target) > 180:
        raise ValueError("Describe the sound to isolate in 1 to 180 characters.")

    repo, checkpoint, python = _paths()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reel-audiosep-") as temp_dir:
        temp = Path(temp_dir)
        target_32k = temp / "target-32k.wav"
        request = {
            "repo": str(repo),
            "checkpoint": str(checkpoint),
            "input": str(audio.resolve()),
            "target": target.strip(),
            "output": str(target_32k),
        }
        env = os.environ.copy()
        env["TOKENIZERS_PARALLELISM"] = "false"
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_HOME"] = str(Path(os.environ.get("REEL_STUDIO_AUDIOSEP_HOME", Path.home() / ".codex/work/reel-studio/models/audiosep")) / "hf")
        script = Path(__file__).resolve()
        result = subprocess.run(
            [str(python), str(script), "--audiosep-worker"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=repo,
            env=env,
            timeout=max(900, int(os.environ.get("AUDIOSEP_TIMEOUT_SECONDS", "900"))),
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                "AudioSep could not separate this audio. Check the optional model "
                f"environment and try a shorter or clearer prompt. Details: {detail[-1800:]}"
            )
        try:
            import numpy as np
            import librosa
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("AudioSep output conversion needs numpy, librosa, and soundfile in the app environment.") from exc

        source, source_rate = sf.read(str(audio), dtype="float32", always_2d=True)
        separated, sep_rate = sf.read(str(target_32k), dtype="float32", always_2d=True)
        if len(separated) == 0 or sep_rate != 32000:
            raise RuntimeError("AudioSep returned an empty or unexpected 32 kHz target stem.")
        mono_target = separated.mean(axis=1)
        target_resampled = librosa.resample(mono_target, orig_sr=sep_rate, target_sr=source_rate)
        frames = len(source)
        if len(target_resampled) < frames:
            target_resampled = np.pad(target_resampled, (0, frames - len(target_resampled)))
        else:
            target_resampled = target_resampled[:frames]
        target_stereo = np.repeat(target_resampled[:, None], source.shape[1], axis=1)
        residual_stereo = source - target_stereo
        target_path = out_dir / "semantic-target.wav"
        residual_path = out_dir / "semantic-residual.wav"
        sf.write(str(target_path), target_stereo, source_rate, subtype="FLOAT")
        sf.write(str(residual_path), residual_stereo, source_rate, subtype="FLOAT")

    return {"target_path": target_path, "residual_path": residual_path}


def _run_worker() -> int:
    """Load the published AudioSep architecture and safely apply its weights."""
    payload: dict[str, Any] = json.load(sys.stdin)
    repo = Path(payload["repo"])
    checkpoint = Path(payload["checkpoint"])
    backend_root = Path(__file__).resolve().parents[1]
    app_paths = {str(backend_root), str(backend_root / "reelstudio")}
    sys.path[:] = [str(repo)] + [entry for entry in sys.path if str(Path(entry).resolve()) not in app_paths]

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from transformers import RobertaConfig, RobertaModel

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    # AudioSep's public model card checkpoint stores the learned CLAP query
    # encoder as well as its separation network. Build the Roberta architecture
    # without separately downloading its base weights, then load the checkpoint
    # state dict in one verified, weights-only pass.
    _original_from_pretrained = RobertaModel.from_pretrained.__func__

    def _model_from_pretrained(cls: type, model_name: str, *args: Any, **kwargs: Any):
        if model_name == "roberta-base":
            config = RobertaConfig.from_pretrained(model_name)
            return cls(config)
        return _original_from_pretrained(cls, model_name, *args, **kwargs)

    RobertaModel.from_pretrained = classmethod(_model_from_pretrained)
    from models.clap_encoder import CLAP_Encoder

    # AudioSep's Lightning class evaluates CLAP_Encoder() as a class default at
    # import time. That default expects a separate multi-gigabyte training
    # checkpoint. The published compact model already contains all query
    # encoder parameters, so construct the matching architecture uninitialized
    # and load the compact checkpoint below.
    original_encoder_init = CLAP_Encoder.__init__

    def _init_encoder_without_duplicate_checkpoint(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["pretrained_path"] = ""
        original_encoder_init(self, *args, **kwargs)

    CLAP_Encoder.__init__ = _init_encoder_without_duplicate_checkpoint
    from models.audiosep import AudioSep, get_model_class
    from utils import parse_yaml

    config_path = repo / "config/audiosep_base.yaml"
    configs = parse_yaml(str(config_path))
    ss_model_class = get_model_class(configs["model"]["model_type"])
    ss_model = ss_model_class(
        input_channels=configs["model"]["input_channels"],
        output_channels=configs["model"]["output_channels"],
        condition_size=configs["model"]["condition_size"],
    )
    query_encoder = CLAP_Encoder(pretrained_path="")
    model = AudioSep(ss_model=ss_model, query_encoder=query_encoder).eval()

    state = torch.load(checkpoint, map_location="cpu", weights_only=True, mmap=True)
    required = {"ss_model", "query_encoder"}
    present = {key.split(".", 1)[0] for key in state}
    if not required.issubset(present):
        raise RuntimeError(f"AudioSep checkpoint is missing learned components: {sorted(required - present)}")
    # Transformers 5 stopped persisting this deterministic RoBERTa position
    # buffer. It is recomputed from the same config at runtime, so it is not a
    # learned checkpoint tensor.
    state.pop("query_encoder.model.text_branch.embeddings.position_ids", None)
    model.load_state_dict(state, strict=True)

    if payload.get("verify_only"):
        print("AudioSep architecture and learned checkpoint loaded successfully.")
        return 0

    audio, _ = librosa.load(payload["input"], sr=32000, mono=True)
    if len(audio) < 16000:
        raise ValueError("AudioSep needs at least 0.5 seconds of audio.")
    # Upstream's trained model accepts 5-second windows. Use 0.5 second overlap
    # and a Hann crossfade to avoid seams when separating longer clips.
    chunk_samples = 160_000
    overlap_samples = 16_000
    separation: list[np.ndarray] = []
    positions = list(range(0, max(1, len(audio) - overlap_samples), chunk_samples - overlap_samples))
    with torch.inference_mode():
        for start in positions:
            chunk = audio[start : start + chunk_samples]
            if len(chunk) < chunk_samples:
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
            text = [payload["target"].strip().lower()]
            condition = model.query_encoder.get_query_embed(modality="text", text=text)
            output = model.ss_model({
                "mixture": torch.as_tensor(chunk, dtype=torch.float32)[None, None, :],
                "condition": condition,
            })["waveform"].squeeze().cpu().numpy()
            separation.append(output[: min(chunk_samples, len(audio) - start)])

    assembled = np.zeros(len(audio), dtype=np.float32)
    weights = np.zeros(len(audio), dtype=np.float32)
    for index, (start, separated) in enumerate(zip(positions, separation)):
        length = min(len(separated), len(audio) - start)
        window = np.ones(length, dtype=np.float32)
        ramp = min(overlap_samples, length // 2)
        if index and ramp:
            window[:ramp] = np.linspace(0, 1, ramp, dtype=np.float32)
        if index + 1 < len(separation) and ramp:
            window[-ramp:] = np.minimum(window[-ramp:], np.linspace(1, 0, ramp, dtype=np.float32))
        assembled[start : start + length] += separated[:length] * window
        weights[start : start + length] += window
    assembled /= np.maximum(weights, 1e-6)
    sf.write(payload["output"], assembled, 32000, subtype="PCM_16")
    return 0


if __name__ == "__main__" and "--audiosep-worker" in sys.argv:
    raise SystemExit(_run_worker())
