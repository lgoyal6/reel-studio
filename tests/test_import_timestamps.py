from pathlib import Path

import pytest

from reelstudio import engine


def test_damaged_theora_seek_timestamps_are_rejected_before_video_is_lost(monkeypatch):
    # Observed on the downloaded live-performance excerpt, before normalization
    # silently dropped its entire video stream.
    monkeypatch.setattr(engine, "_ffprobe", lambda _: {"format": {"duration": "15.005533"}, "streams": [
        {"codec_type": "video", "start_time": "9617286884262114", "duration": "-9617286884262098"},
        {"codec_type": "audio", "start_time": "0", "duration": "15.005533", "sample_rate": "44100", "channels": 2},
    ]})
    with pytest.raises(engine.EngineError, match="timestamps are damaged"):
        engine.probe(Path("downloaded-excerpt.ogv"))


def test_import_preserves_video_tail_when_audio_ends_early(tmp_path):
    from reelstudio import engine

    source = tmp_path / "short-audio.mp4"
    engine._run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ])
    original = engine.ingest(source, tmp_path / "original")
    assert abs(original["duration"] - 3) < .05
    result = engine.render(Path(original["video_path"]), Path(original["audio_path"]),
                           [{"type": "gain", "gain_db": -3}], tmp_path / "edited")
    assert abs(engine._video_duration(Path(result["video_path"])) - 3) < .08
    pixel = engine._run(["ffmpeg", "-v", "error", "-ss", "2.8", "-i", result["video_path"],
                         "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]).stdout
    assert len(pixel) == 3 and pixel[2] > 200 and pixel[0] < 30


def test_import_keeps_delayed_audio_at_its_video_timestamp(tmp_path):
    import numpy as np
    import soundfile as sf
    from reelstudio import engine

    source = tmp_path / "delayed-audio.mp4"
    engine._run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=25:d=3",
        "-itsoffset", "0.7", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac", str(source),
    ])
    result = engine.ingest(source, tmp_path / "original")
    samples, rate = sf.read(result["audio_path"])
    assert len(samples) / rate == pytest.approx(3, abs=.05)
    assert np.max(np.abs(samples[:int(.5 * rate)])) < .001
    assert np.max(np.abs(samples[int(.9 * rate):int(1.2 * rate)])) > .05

