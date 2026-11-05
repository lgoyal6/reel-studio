"""Reference fixtures measure behavior, rather than just mirroring filter choices."""
from pathlib import Path
import subprocess

import numpy as np
import soundfile as sf

from reelstudio import engine


def test_cleanup_improves_known_stationary_noise_fixture(tmp_path):
    sr = 48000
    t = np.arange(sr * 4) / sr
    clean = .2 * np.sin(2 * np.pi * 440 * t)
    noisy = clean + np.random.default_rng(7).normal(0, .02, len(t))
    source = tmp_path / "noisy.wav"
    sf.write(source, np.column_stack([noisy, noisy]), sr)
    result = engine.render(source, source, [{"type": "clean", "strength": .65}], tmp_path / "clean")
    processed, out_sr = sf.read(result["audio_path"])
    assert out_sr == sr and len(processed) == len(noisy)
    region = slice(sr, len(t) - sr)
    snr_before = 10 * np.log10(np.mean(clean[region] ** 2) / np.mean((noisy[region] - clean[region]) ** 2))
    snr_after = 10 * np.log10(np.mean(clean[region] ** 2) / np.mean((processed[region, 0] - clean[region]) ** 2))
    assert snr_after > snr_before + 3, (snr_before, snr_after)
    assert np.max(np.abs(processed)) < 1


def test_speed_preserves_flash_click_alignment_not_just_duration(tmp_path):
    sr = 48000
    signal = np.zeros(sr * 4)
    # A short identifiable tone burst at 1 second, paired with a visible flash.
    t = np.arange(960) / sr
    signal[sr:sr + len(t)] = .6 * np.sin(2 * np.pi * 1000 * t) * np.hanning(len(t))
    audio = tmp_path / "click.wav"
    sf.write(audio, np.column_stack([signal, signal]), sr)
    video = tmp_path / "flash.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x160:r=25:d=4",
                    "-i", str(audio), "-vf", "drawbox=color=white:t=fill:enable='between(t,0.96,1.04)'",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    imported = engine.ingest(video, tmp_path / "import")
    result = engine.render(Path(imported["video_path"]), audio,
                           [{"type": "speed", "start": 0, "end": 2, "factor": .5}], tmp_path / "slowed")
    processed, _ = sf.read(result["audio_path"])
    energy = np.mean(processed[:len(processed) // 480 * 480, 0].reshape(-1, 480) ** 2, axis=1)
    click_time = float(np.argmax(energy)) * .01
    assert abs(click_time - 2) < .08
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2", "-i", result["video_path"],
                            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    assert np.frombuffer(frame, dtype=np.uint8).mean() > 220


def test_pitch_shift_changes_frequency_without_changing_duration(tmp_path):
    sr = 48000
    t = np.arange(sr * 3) / sr
    signal = .2 * np.sin(2 * np.pi * 440 * t)
    audio = tmp_path / "tone.wav"
    sf.write(audio, np.column_stack([signal, signal]), sr)
    result = engine.render(audio, audio, [{"type": "pitch", "semitones": 12, "start": 0, "end": 3}], tmp_path / "pitch")
    processed, _ = sf.read(result["audio_path"])
    assert abs(len(processed) / sr - 3) < .03
    middle = processed[sr:2*sr, 0]
    frequencies = np.fft.rfftfreq(len(middle), d=1/sr)
    dominant = frequencies[np.argmax(np.abs(np.fft.rfft(middle)))]
    assert abs(dominant - 880) < 5


def test_export_retains_complete_audio_when_video_ends_between_frames(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x160:r=24000/1001:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    imported = engine.ingest(source, tmp_path / "import")
    result = engine.render(Path(imported["video_path"]), Path(imported["audio_path"]),
                           [{"type": "speed", "start": 1, "end": 2, "factor": .75}], tmp_path / "render")
    meta = engine._ffprobe(Path(result["video_path"]))
    audio = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    assert abs(float(audio["duration"])-result["duration"]) < 1/48000 + .003
