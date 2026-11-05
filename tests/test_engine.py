from __future__ import annotations

import array
import json
import subprocess
from pathlib import Path

import pytest

from reelstudio import engine


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture
def clip(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "synthetic.mp4"
    run("-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=8",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source))
    imported = engine.ingest(source, tmp_path / "import")
    return {"source": source, "media": Path(imported["video_path"]), "audio": Path(imported["audio_path"]), "root": tmp_path}


def test_probe_ingest_and_analysis(clip: dict[str, Path]) -> None:
    metadata = engine.probe(clip["media"])
    assert metadata["has_video"] is True
    assert metadata["width"] == 320 and metadata["height"] == 240
    assert 7.9 <= metadata["duration"] <= 8.1
    stats = engine.analyze(clip["audio"])
    assert 7.9 <= stats["duration"] <= 8.1
    assert 32 <= len(stats["peaks"]) <= 320
    assert stats["peak_db"] < 0


def test_speed_edit_keeps_video_and_audio_in_sync(clip: dict[str, Path]) -> None:
    result = engine.render(clip["media"], clip["audio"], [
        {"type": "speed", "start": 2, "end": 4, "factor": .5, "preserve_pitch": True},
    ], clip["root"] / "speed")
    audio_duration = engine.probe(Path(result["audio_path"]))["duration"]
    video_info = engine.probe(Path(result["video_path"]))
    assert 9.9 <= audio_duration <= 10.15
    assert abs(audio_duration - video_info["duration"]) < .12
    assert result["mapping"][0]["operation"]["factor"] == .5


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ({"type": "trim", "start": 2, "end": 5}, 3),
        ({"type": "cut", "start": 2, "end": 5}, 5),
        ({"type": "loop", "start": 2, "end": 3, "count": 3}, 10),
        ({"type": "reorder", "ranges": [{"start": 4, "end": 8}, {"start": 0, "end": 4}]}, 8),
    ],
)
def test_range_edits_keep_audio_and_video_lengths_matched(
    clip: dict[str, Path], op: dict, expected: int
) -> None:
    result = engine.render(clip["media"], clip["audio"], [op], clip["root"] / op["type"])
    audio_duration = engine.probe(Path(result["audio_path"]))["duration"]
    video_duration = engine.probe(Path(result["video_path"]))["duration"]
    assert abs(audio_duration - expected) < .1
    assert abs(audio_duration - video_duration) < .12


def test_gain_is_limited_to_selected_range(clip: dict[str, Path]) -> None:
    result = engine.render(clip["media"], clip["audio"], [
        {"type": "gain", "start": 2, "end": 6, "gain_db": -12},
    ], clip["root"] / "gain")
    audio = Path(result["audio_path"])
    regions: list[float] = []
    pcm, channels, _ = engine._pcm(audio)
    for start, end in ((.2, 1.5), (3, 5)):
        first, last = int(start * engine.SR) * channels, int(end * engine.SR) * channels
        frames = array.array("f", pcm[first:last])
        regions.append((sum(x*x for x in frames) / len(frames)) ** .5)
    ratio_db = 20 * __import__("math").log10(regions[1] / regions[0])
    assert -13 <= ratio_db <= -11


def test_clean_emits_nonempty_audio_and_describes_scope(clip: dict[str, Path]) -> None:
    result = engine.render(clip["media"], clip["audio"], [
        {"type": "clean", "strength": .5},
    ], clip["root"] / "clean")
    assert 7.9 <= result["duration"] <= 8.1
    assert result["analysis"]["rms_db"] > -100
    assert any("does not selectively remove a named sound" in note for note in result["notes"])


def test_music_alignment_uses_exact_start_anchor(clip: dict[str, Path]) -> None:
    silence = clip["root"] / "silence.wav"
    music = clip["root"] / "music.wav"
    run("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
        "-af", "volume=0", "-c:a", "pcm_s16le", str(silence))
    run("-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
        "-c:a", "pcm_s16le", str(music))
    result = engine.render(clip["media"], silence, [
        {"type": "align_music", "anchor": 1, "music_start": 0, "mode": "start", "gain_db": -6},
    ], clip["root"] / "aligned", auxiliary=music)
    assert abs(result["duration"] - 8) < .02
    pcm, channels, _ = engine._pcm(Path(result["audio_path"]))
    def rms(start: float, end: float) -> float:
        values = pcm[int(start * engine.SR) * channels:int(end * engine.SR) * channels]
        return (sum(x*x for x in values) / max(1, len(values))) ** .5
    assert rms(.2, .8) < 0.00001
    assert rms(1.1, 1.8) > .02
    assert any("crossfades to fill the clip" in note for note in result["notes"])


def test_drop_alignment_uses_an_estimated_loudness_rise(clip: dict[str, Path]) -> None:
    import math
    import wave

    sr = 48_000
    music = clip["root"] / "rising-music.wav"
    values = array.array("h")
    for i in range(sr * 3):
        amplitude = .02 if i < sr else .5
        values.append(int(32767 * amplitude * math.sin(2 * math.pi * 440 * i / sr)))
    with wave.open(str(music), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(values.tobytes())
    assert .8 <= engine._energy_rise(music, .2) <= 1.3


def _write_wave(path: Path, data: array.array, channels: int = 1) -> Path:
    import wave

    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(engine.SR)
        out.writeframes(data.tobytes())
    return path


def _layer_rms(path: Path, start: float, end: float) -> float:
    pcm, channels, _ = engine._pcm(path)
    values = pcm[int(start * engine.SR) * channels:int(end * engine.SR) * channels]
    return (sum(x * x for x in values) / max(1, len(values))) ** .5


def test_duck_replaces_existing_music_layer_without_adding_it_twice(tmp_path: Path, monkeypatch) -> None:
    import math

    sr = engine.SR
    dry_data, voice_data, music_data = array.array("h"), array.array("h"), array.array("h")
    for i in range(sr * 4):
        t = i / sr
        voice = .22 * math.sin(2 * math.pi * 440 * t) if t < .5 else 0
        dry_data.append(int(32767 * voice))
        voice_data.append(int(32767 * voice))
        music_data.append(int(32767 * .18 * math.sin(2 * math.pi * 880 * t)))
    dry = _write_wave(tmp_path / "dry.wav", dry_data)
    voice = _write_wave(tmp_path / "voice.wav", voice_data)
    music = _write_wave(tmp_path / "music.wav", music_data)
    aligned = engine.render(dry, dry, [
        {"type": "align_music", "anchor": 0, "music_start": 0, "mode": "start", "gain_db": -6},
    ], tmp_path / "aligned", auxiliary=music)
    baseline_tail = _layer_rms(Path(aligned["audio_path"]), 3, 3.5)

    silent = _write_wave(tmp_path / "silent.wav", array.array("h", [0] * sr * 4))
    monkeypatch.setattr(engine, "_stem_files", lambda audio, cache, progress: {
        "vocals": voice, "drums": silent, "bass": silent, "other": silent,
    })
    ducked = engine.render(Path(aligned["audio_path"]), Path(aligned["audio_path"]), [
        {"type": "duck", "amount_db": -12},
    ], tmp_path / "ducked", auxiliary=music, input_layers=aligned["layers"])
    duck_tail = _layer_rms(Path(ducked["audio_path"]), 3, 3.5)
    assert abs(duck_tail / baseline_tail - 1) < .12
    assert Path(ducked["layers"]["dry"]).is_file()
    assert Path(ducked["layers"]["music"]).is_file()


def test_speed_transforms_both_persistent_layers_with_matching_duration(tmp_path: Path) -> None:
    import math

    sr = engine.SR
    source = array.array("h", (int(32767 * .15 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(sr * 4)))
    audio = _write_wave(tmp_path / "source.wav", source)
    music = _write_wave(tmp_path / "music.wav", source)
    aligned = engine.render(audio, audio, [
        {"type": "align_music", "anchor": 0, "music_start": 0, "mode": "start", "gain_db": -6},
    ], tmp_path / "aligned", auxiliary=music)
    revised = engine.render(Path(aligned["audio_path"]), Path(aligned["audio_path"]), [
        {"type": "speed", "start": 1, "end": 2, "factor": .5},
    ], tmp_path / "slowed", input_layers=aligned["layers"])
    assert abs(engine.probe(Path(revised["audio_path"]))["duration"] - 5) < .03
    assert abs(engine.probe(Path(revised["layers"]["dry"]))["duration"] - 5) < .03
    assert abs(engine.probe(Path(revised["layers"]["music"]))["duration"] - 5) < .03


def test_requested_unavailable_sound_target_is_explained(clip: dict[str, Path], monkeypatch) -> None:
    from reelstudio import semantic
    monkeypatch.setattr(semantic, "available", lambda: False)
    with pytest.raises(engine.EngineError, match="not configured|cannot reliably isolate"):
        engine.render(clip["media"], clip["audio"], [
            {"type": "isolate", "target": "the audience laughing"},
        ], clip["root"] / "unsupported")

