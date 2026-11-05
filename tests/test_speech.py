from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from reelstudio import speech


FIXTURE_DIR = Path.home() / ".codex/work/reel-studio/verification/speech"
NOISY_FIXTURE = FIXTURE_DIR / "voice-and-wind.wav"
CLEAN_FIXTURE = FIXTURE_DIR / "clean.wav"


def test_readiness_requires_the_verified_model_and_smoke_test(tmp_path: Path, monkeypatch):
    home = tmp_path / "deepfilter"
    python = home / "venv/bin/python"
    model = home / "DeepFilterNet3"
    (model / "checkpoints").mkdir(parents=True)
    python.parent.mkdir(parents=True)
    python.touch()
    (model / "checkpoints/model_120.ckpt.best").write_bytes(b"model")
    (model / "config.ini").write_text("model config")
    monkeypatch.setenv("REEL_STUDIO_DEEPFILTER_HOME", str(home))

    assert speech.available() is False
    marker = {
        "worker_loaded": True,
        "repository_release": speech.RELEASE,
        "checkpoint_sha256": speech.MODEL_SHA256,
        "python_path": str(python.resolve()),
        "model_path": str(model.resolve()),
        "model_bytes": speech.MODEL_BYTES,
    }
    (home / "ready.json").write_text(json.dumps(marker))
    assert speech.available() is True
    marker["worker_loaded"] = False
    (home / "ready.json").write_text(json.dumps(marker))
    assert speech.available() is False


def test_unavailable_engine_is_actionable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REEL_STUDIO_DEEPFILTER_HOME", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="setup_speech.py"):
        speech.enhance(tmp_path / "input.wav", tmp_path / "out")


def _si_sdr_db(estimate: np.ndarray, reference: np.ndarray) -> float:
    estimate = estimate.reshape(-1).astype(np.float64)
    reference = reference.reshape(-1).astype(np.float64)
    scale = float(estimate @ reference) / float(reference @ reference)
    target = scale * reference
    residual = estimate - target
    return 10 * math.log10(float(target @ target) / max(float(residual @ residual), 1e-15))


@pytest.mark.skipif(
    not speech.available() or not NOISY_FIXTURE.is_file() or not CLEAN_FIXTURE.is_file(),
    reason="Requires the optional verified DeepFilterNet3 model and private synthetic speech fixtures.",
)
def test_deepfilternet3_improves_synthetic_speech_snr_and_preserves_timing(tmp_path: Path):
    output = speech.enhance(NOISY_FIXTURE, tmp_path)
    enhanced, enhanced_rate = sf.read(output, dtype="float32", always_2d=True)
    noisy, noisy_rate = sf.read(NOISY_FIXTURE, dtype="float32", always_2d=True)
    clean, clean_rate = sf.read(CLEAN_FIXTURE, dtype="float32", always_2d=True)
    assert enhanced_rate == 48_000
    assert enhanced.shape[1] == 2
    assert np.isfinite(enhanced).all()
    assert np.max(np.abs(enhanced)) <= 0.9991

    assert noisy_rate == clean_rate == 22_050
    noisy_48k = resample_poly(noisy, up=320, down=147, axis=0)
    clean_48k = resample_poly(clean, up=320, down=147, axis=0)
    frames = min(len(enhanced), len(noisy_48k), len(clean_48k))
    assert abs(len(enhanced) / enhanced_rate - len(noisy) / noisy_rate) <= 1 / enhanced_rate
    enhanced, noisy_48k, clean_48k = enhanced[:frames], noisy_48k[:frames], clean_48k[:frames]

    noisy_sisdr = _si_sdr_db(noisy_48k, clean_48k)
    enhanced_sisdr = _si_sdr_db(enhanced, clean_48k)
    assert enhanced_sisdr - noisy_sisdr >= 2.0
