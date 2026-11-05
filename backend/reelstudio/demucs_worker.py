"""Pinned Demucs worker with float stems and no per-stem gain normalization.

The upstream CLI rescales each estimate separately before saving. Floating-point
working stems preserve their relative gains, with headroom handled at final mix.
This worker runs in the dedicated Demucs environment, not the web environment.
"""
from pathlib import Path
import sys

import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model
from demucs.separate import load_track


def separate(source: Path, output: Path):
    model = get_model("htdemucs").cpu().eval()
    wave = load_track(source, model.audio_channels, model.samplerate)
    reference = wave.mean(0)
    center, scale = reference.mean(), reference.std()
    if scale < 1e-8:
        sources = torch.zeros(len(model.sources), *wave.shape)
    else:
        normalized = (wave - center) / scale
        with torch.inference_mode():
            sources = apply_model(model, normalized[None], device="cpu", shifts=0,
                                  split=True, overlap=.1, progress=True)[0]
        sources = sources * scale + center
    output.mkdir(parents=True, exist_ok=True)
    for name, estimate in zip(model.sources, sources):
        if not torch.isfinite(estimate).all():
            raise RuntimeError("Demucs returned non-finite audio")
        sf.write(output / f"{name}.wav", estimate.T.cpu().numpy(), model.samplerate, subtype="FLOAT")


if __name__ == "__main__":
    separate(Path(sys.argv[1]), Path(sys.argv[2]))
