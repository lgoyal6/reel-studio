from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import numpy as np
import pytest
import soundfile as sf

from reelstudio import semantic


@pytest.fixture
def cached_separator(tmp_path, monkeypatch):
    checkpoint = tmp_path / "model.bin"
    checkpoint.write_bytes(b"model-one")
    marker = tmp_path / "ready.json"
    marker.write_text('{"model": 1}')
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_paths", lambda: (tmp_path, checkpoint, tmp_path / "python"))
    monkeypatch.setattr(semantic, "_ready_marker", lambda: marker)
    calls = []

    def worker(audio, target, out):
        calls.append(target)
        time.sleep(0.02)
        out.mkdir(parents=True)
        samples, rate = sf.read(audio, always_2d=True)
        sf.write(out / "semantic-target.wav", samples * 0.25, rate, subtype="FLOAT")
        sf.write(out / "semantic-residual.wav", samples * 0.75, rate, subtype="FLOAT")

    monkeypatch.setattr(semantic, "_separate_uncached", worker)
    audio = tmp_path / "input.wav"
    sf.write(audio, np.ones((100, 2)) * 0.5, 48000, subtype="FLOAT")
    return audio, checkpoint, calls


def test_cache_reuses_both_stems_and_invalidates_changed_inputs(tmp_path, cached_separator):
    audio, checkpoint, calls = cached_separator
    cache = tmp_path / "cache"
    first = semantic.separate(audio, "People talking", tmp_path / "first", cache_dir=cache)
    second = semantic.separate(audio, " people TALKING ", tmp_path / "second", cache_dir=cache)
    assert len(calls) == 1
    assert first["target_path"].read_bytes() == second["target_path"].read_bytes()
    assert first["residual_path"].read_bytes() == second["residual_path"].read_bytes()
    semantic.separate(audio, "drums", tmp_path / "different-target", cache_dir=cache)
    sf.write(audio, np.zeros((100, 2)), 48000, subtype="FLOAT")
    semantic.separate(audio, "drums", tmp_path / "different-input", cache_dir=cache)
    checkpoint.write_bytes(b"model-two-new")
    semantic.separate(audio, "drums", tmp_path / "different-model", cache_dir=cache)
    assert len(calls) == 4


def test_simultaneous_requests_do_not_duplicate_inference(tmp_path, cached_separator):
    audio, _, calls = cached_separator

    def request(index):
        return semantic.separate(audio, "voices", tmp_path / str(index), cache_dir=tmp_path / "cache")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(request, range(2)))
    assert len(calls) == 1
    for result in outputs:
        assert sf.info(result["target_path"]).frames == 100
        assert sf.info(result["residual_path"]).frames == 100


def test_failed_inference_leaves_no_completed_cache(tmp_path, cached_separator, monkeypatch):
    audio, _, _ = cached_separator

    def failure(audio, target, out):
        out.mkdir()
        (out / "semantic-target.wav").write_bytes(b"partial")
        raise RuntimeError("interrupted")

    monkeypatch.setattr(semantic, "_separate_uncached", failure)
    with pytest.raises(RuntimeError, match="interrupted"):
        semantic.separate(audio, "voices", tmp_path / "out", cache_dir=tmp_path / "cache")
    assert not any(path.is_dir() for path in (tmp_path / "cache").iterdir())
