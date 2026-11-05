from pathlib import Path
import json
import shutil
import subprocess

import pytest
import numpy as np
import soundfile as sf

from reelstudio import ads


def _tool(args):
    subprocess.run(args, check=True, capture_output=True)


def _inputs(tmp_path: Path):
    video, audio = tmp_path / "source.mp4", tmp_path / "current.wav"
    _tool(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=15", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
    _tool(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "pcm_s16le", str(audio)])
    return video, audio


def _op(**kwargs):
    return {"type": "cinematic_ad", "headline": "Make a moment", "brand": "Northstar", "cta": "Explore now", **kwargs}


def test_operation_validation_and_text_is_data():
    escaped = ads._check_op(_op(headline='Say "hello";\nprocess.exit(1)'))
    source = ads._scene_source("/tmp/source.mp4", "/tmp/font.ttf", escaped, 720, 1280)
    assert json.dumps(escaped["headline"], ensure_ascii=True) in source
    assert "process.exit(1)" in source  # content is quoted, never executable source
    with pytest.raises(ValueError, match="only"):
        ads._check_op(_op(extra="unexpected"))
    with pytest.raises(ValueError, match="duration"):
        ads._check_op(_op(duration=10))


def test_missing_video_is_actionable(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required")
    audio = tmp_path / "current.wav"
    _tool(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", str(audio)])
    monkeypatch.setattr(ads, "available", lambda: True)
    with pytest.raises(ValueError, match="video"):
        ads.render_ad(audio, audio, _op(), tmp_path / "out")


def test_unavailable_runtime_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(ads, "available", lambda: False)
    with pytest.raises(RuntimeError, match="setup_ads"):
        ads.render_ad(tmp_path / "missing.mp4", tmp_path / "missing.wav", _op(), tmp_path / "out")


@pytest.mark.skipif(not ads.available(), reason="Install and smoke-test pinned Glissade runtime")
def test_repeated_video_flash_and_audio_click_stay_aligned(tmp_path):
    video, audio = tmp_path / "flash.mp4", tmp_path / "click.wav"
    _tool(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=30:duration=3",
           "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='lt(t,0.22)'", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
    _tool(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
           "-af", "volume=0:enable='gte(t,0.22)'", "-c:a", "pcm_s16le", str(audio)])
    result = ads.render_ad(video, audio, _op(), tmp_path / "sync")
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", result["video_path"], "-vf", "crop=2:2:640:360",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
    video_levels = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 4).mean(axis=1)
    samples, rate = sf.read(result["audio_path"])
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    audio_levels = np.sqrt(np.mean(samples[:len(samples) // 960 * 960].reshape(-1, 960) ** 2, axis=1))
    offsets = []
    for seam in (3, 6, 9):
        first = round(seam * 30)
        video_window = video_levels[first:first + 8]
        audio_first = round(seam * rate / 960)
        audio_window = audio_levels[audio_first:audio_first + 8]
        assert video_window.max() > 10
        assert audio_window.max() > .03
        video_peak = (first + int(video_window.argmax())) / 30
        audio_peak = (audio_first + int(audio_window.argmax())) * .02
        offset = video_peak - audio_peak
        assert abs(offset) <= .12
        offsets.append(offset)
    assert max(offsets) - min(offsets) <= .02  # no cumulative slip over repeated cycles


@pytest.mark.skipif(not ads.available(), reason="Install and smoke-test pinned Glissade runtime")
@pytest.mark.parametrize("aspect,size", [("landscape", (1280, 720)), ("portrait", (720, 1280))])
def test_rendered_ad_has_finite_audio_and_video(tmp_path, aspect, size):
    video, audio = _inputs(tmp_path)
    result = ads.render_ad(video, audio, _op(duration=15, aspect=aspect), tmp_path / "render")
    output = ads._probe(Path(result["video_path"]))
    streams = output["streams"]
    assert {s["codec_type"] for s in streams} == {"video", "audio"}
    assert abs(float(output["format"]["duration"]) - 15) < .15
    video_stream = next(s for s in streams if s["codec_type"] == "video")
    assert (video_stream["width"], video_stream["height"]) == size
    assert float(next(s["duration"] for s in streams if s["codec_type"] == "audio")) >= 14.9
    samples, _ = sf.read(result["audio_path"])
    assert samples.shape[0] >= 14.9 * 48_000 and np.isfinite(samples).all()
    _tool(["ffmpeg", "-v", "error", "-i", result["video_path"], "-f", "null", "-"])
    assert result["layers"] == {"dry": result["audio_path"], "music": None}
