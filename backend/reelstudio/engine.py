"""CPU-first, file-backed audio/video rendering primitives for Reel Studio.

All user-controlled times are seconds in the current revision. Media is rendered
to a fresh revision after every operation, so failed jobs never corrupt the source.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

SR = 48_000
LIMITER = "alimiter=limit=0.98:level=false:latency=true"
Progress = Callable[[str, float], None]


class EngineError(RuntimeError):
    """An actionable input, model, or renderer error."""


def _run(args: list[str], *, timeout: int = 1800, input_data: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=input_data, check=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise EngineError(f"Required media tool is missing: {args[0]}. Install FFmpeg and make it available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"Media operation timed out after {timeout} seconds. Try a shorter clip.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or b"").decode("utf-8", "replace")[-2000:]
        raise EngineError(f"Media operation failed: {details.strip() or exc}") from exc


def _ffprobe(path: Path) -> dict[str, Any]:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,duration,start_time,sample_rate,channels",
        "-of", "json", str(path),
    ], timeout=60)
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EngineError(f"Could not inspect media file: {path.name}") from exc


def probe(path: Path) -> dict[str, Any]:
    """Return duration, dimensions, and whether a video stream is present."""
    info = _ffprobe(Path(path))
    streams = info.get("streams", [])
    fmt_duration = float(info.get("format", {}).get("duration") or 0)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio:
        raise EngineError("This file has no audio track. Choose a video or audio file with sound.")
    if video:
        video_start = float(video.get("start_time") or 0)
        audio_start = float(audio.get("start_time") or 0)
        if abs(video_start - audio_start) > max(60, fmt_duration) * 1000 or float(video.get("duration") or 0) < -1:
            raise EngineError("This video's timestamps are damaged. Re-export it as an MP4 before importing so its picture and audio stay synchronized.")
    duration = max(fmt_duration, float(audio.get("duration") or 0), float((video or {}).get("duration") or 0))
    if duration <= 0:
        raise EngineError("Could not read the clip duration.")
    return {
        "duration": duration,
        "width": int(video.get("width") or 0) if video else 0,
        "height": int(video.get("height") or 0) if video else 0,
        "has_video": video is not None,
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
    }


def _has_video(path: Path) -> bool:
    return any(s.get("codec_type") == "video" for s in _ffprobe(Path(path)).get("streams", []))


def _video_duration(path: Path) -> float:
    info = _ffprobe(Path(path))
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not stream:
        return 0.0
    return max(float(stream.get("duration") or 0), float(info.get("format", {}).get("duration") or 0))


def _pcm(path: Path) -> tuple[array.array, int, int]:
    """Decode a WAV into interleaved float32 using stdlib array and FFmpeg."""
    meta = _ffprobe(path)
    audio = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        raise EngineError("The audio track is missing.")
    channels = max(1, int(audio.get("channels") or 1))
    decoded = _run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels), "-ar", str(SR), "-"], timeout=300)
    values = array.array("f")
    values.frombytes(decoded.stdout)
    return values, channels, SR


def analyze(path: Path) -> dict[str, Any]:
    """Measure duration, a 160-bin peak envelope, RMS/peak dB, and beat times."""
    values, channels, _ = _pcm(Path(path))
    frames = len(values) // channels
    if frames == 0:
        raise EngineError("The audio file is empty.")
    # Downmix only for measurement. Keep the actual render stereo.
    mono = array.array("f", (sum(values[i:i + channels]) / channels for i in range(0, frames * channels, channels)))
    bins = min(320, max(32, int(frames / SR * 16)))
    peaks: list[float] = []
    peak = 0.0
    energy = 0.0
    for value in values:
        magnitude = abs(value)
        peak = max(peak, magnitude)
        energy += value * value
    block = max(1, math.ceil(frames / bins))
    for i in range(0, frames, block):
        low, high = i * channels, min(len(values), (i + block) * channels)
        peaks.append(max((abs(v) for v in values[low:high]), default=0.0))
    rms = math.sqrt(energy / len(values))
    bpm: float | None = None
    beats: list[float] = []
    try:
        import numpy as np  # type: ignore

        # Lightweight onset autocorrelation. Keep analysis responsive on 5-minute
        # clips and avoid librosa's first-call JIT cost on every edited revision.
        hop, frame = 512, 2048
        y = np.frombuffer(mono, dtype=np.float32)
        if len(y) >= frame:
            windows = np.lib.stride_tricks.sliding_window_view(y, frame)[::hop]
            energy_curve = np.log1p(np.sqrt(np.mean(windows * windows, axis=1)) * 100)
            onset = np.maximum(0, np.diff(energy_curve, prepend=energy_curve[0]))
            onset -= onset.mean()
            rate = SR / hop
            candidates = range(max(1, int(rate * 60 / 240)), min(len(onset) // 2, int(rate * 60 / 35)) + 1)
            correlations = [(float(np.dot(onset[lag:], onset[:-lag])), lag) for lag in candidates]
            if correlations:
                best, lag = max(correlations)
                baseline = max(1e-12, float(np.dot(onset, onset)))
                if best / baseline >= .06:
                    bpm = 60 * rate / lag
                    # Pick the strongest onset phase, then lay out the beat grid.
                    phase = max(range(lag), key=lambda p: float(onset[p::lag].sum()))
                    beats = [float(t / rate) for t in range(phase, len(onset), lag) if t / rate < frames / SR]
    except (ImportError, ValueError, RuntimeError):
        # Waveform and level analysis still work when optional NumPy is unavailable.
        pass
    return {
        "duration": frames / SR,
        "peaks": peaks,
        "beats": beats,
        "bpm": bpm,
        "peak_db": 20 * math.log10(max(peak, 1e-9)),
        "rms_db": 20 * math.log10(max(rms, 1e-9)),
    }


def _energy_rise(path: Path, search_start: float) -> float:
    """Return the strongest local loudness rise after a selected music offset."""
    import numpy as np  # type: ignore

    values, channels, _ = _pcm(path)
    signal = np.frombuffer(values, dtype=np.float32).reshape(-1, channels).mean(axis=1)
    hop = int(SR * .1)
    usable = signal[:len(signal) // hop * hop].reshape(-1, hop)
    level = np.sqrt(np.mean(usable * usable, axis=1) + 1e-12)
    start = max(1, int(search_start / .1))
    if len(level) < start + 3:
        return search_start
    best_time, best_change = search_start, -float("inf")
    for i in range(start, len(level) - 2):
        prior = max(1e-6, float(level[max(0, i - 10):i].mean()))
        after = max(1e-6, float(level[i:i + 3].mean()))
        change = 20 * math.log10(after / prior)
        if change > best_change:
            best_time, best_change = i * .1, change
    return best_time


def ingest(source: Path, work: Path) -> dict[str, Any]:
    """Copy an uploaded source and create aligned H.264 video/48 kHz PCM audio."""
    source, work = Path(source), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    info = probe(source)
    if info["duration"] > 300:
        raise EngineError("Clips can be up to five minutes. Trim the source and re-import it.")
    shutil.copyfile(source, work / "original-upload.bin")
    original = work / ("source.mp4" if info["has_video"] else "source-audio.m4a")
    wav = work / "original.wav"
    if info["has_video"]:
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-af", "aresample=async=1:first_pts=0,apad", "-shortest", "-c:a", "aac", "-ar", str(SR), "-ac", "2", "-movflags", "+faststart", str(original)], timeout=1800)
        if not _has_video(original):
            raise EngineError("The video could not be decoded. Re-export this clip as an MP4 and try again.")
    else:
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", str(original)], timeout=600)
    audio_timing = ["-af", "aresample=async=1:first_pts=0,apad", "-t", str(_video_duration(original))] if info["has_video"] else []
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", *audio_timing, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)], timeout=600)
    return {
        "audio_path": str(wav),
        "video_path": str(original) if info["has_video"] else None,
        "duration": _duration(wav),
        "analysis": analyze(wav),
        "tracks": [],
        "notes": [],
        "mapping": [],
        "layers": {"dry": str(wav), "music": None},
    }


def _validate(op: dict[str, Any], duration: float) -> dict[str, Any]:
    kind = op.get("type")
    allowed = {"gain", "clean", "isolate", "remove", "stem_mix", "speed", "pitch", "trim", "cut", "loop", "reorder", "fade", "normalize", "align_music", "replace_audio", "duck", "cinematic_ad"}
    if kind not in allowed:
        raise EngineError(f"Unsupported edit: {kind!r}. Try gain, clean, isolate, remove, speed, pitch, trim, cut, loop, fade, normalize, align music, or duck.")
    result = dict(op)
    if kind == "cinematic_ad":
        from .models import CinematicAd
        return CinematicAd.model_validate(result).model_dump()
    start = float(result.get("start", 0))
    end = float(result.get("end", duration))
    if kind in {"speed", "pitch", "gain", "trim", "cut", "loop", "replace_audio"}:
        if not (0 <= start < end <= duration + .01):
            raise EngineError(f"The {kind} section must fall within the current clip (0 to {duration:.2f} seconds).")
    if kind == "speed" and not .25 <= float(result.get("factor", 1)) <= 4:
        raise EngineError("Speed must be between 0.25× and 4×.")
    if kind == "pitch" and not -12 <= float(result.get("semitones", 0)) <= 12:
        raise EngineError("Pitch changes must be between -12 and +12 semitones.")
    if kind in {"isolate", "remove", "duck"} and (not isinstance(result.get("target", "vocals"), str) or not result.get("target", "vocals").strip()):
        raise EngineError("Describe the sound you want to isolate or remove.")
    if kind == "clean":
        result["strength"] = max(0, min(1, float(result.get("strength", .6))))
    if kind == "gain":
        result["gain_db"] = max(-60, min(18, float(result.get("gain_db", 0))))
    if kind == "loop" and not 2 <= int(result.get("count", 2)) <= 8:
        raise EngineError("Repeat a section 2 to 8 times.")
    if kind == "stem_mix":
        gains = result.get("gains")
        if not isinstance(gains, dict) or not gains or any(k not in {"vocals", "drums", "bass", "other"} for k in gains):
            raise EngineError("Choose stem volume levels for vocals, drums, bass, or other.")
        result["gains"] = {k: max(-60, min(18, float(v))) for k, v in gains.items()}
    if kind == "reorder":
        ranges = result.get("ranges")
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 16:
            raise EngineError("Choose 1 to 16 clip sections to place in order.")
        checked = []
        for section in ranges:
            start, end = float(section["start"]), float(section["end"])
            if start < 0 or end <= start or end > duration + .01:
                raise EngineError(f"Each reordered section must fall within the clip (0 to {duration:.2f} seconds).")
            checked.append({"start": start, "end": min(duration, end)})
        result["ranges"] = checked
    if kind == "align_music" and float(result.get("anchor", 0)) > duration:
        raise EngineError(f"The music alignment point must be within the clip (0 to {duration:.2f} seconds).")
    return result


def _stem_files(audio: Path, cache: Path, progress: Progress | None) -> dict[str, Path]:
    """Run Meta Demucs once per content hash and reuse its separated stems."""
    digest_obj = hashlib.sha256()
    digest_obj.update(b"htdemucs:cpu:shifts0:overlap.1:float32:noclip:v1")
    with audio.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest_obj.update(block)
    digest = digest_obj.hexdigest()
    folder = cache / digest
    stems_dir = folder / "htdemucs" / audio.stem
    expected = {name: stems_dir / f"{name}.wav" for name in ("vocals", "drums", "bass", "other")}
    if all(p.exists() for p in expected.values()):
        return expected
    folder.mkdir(parents=True, exist_ok=True)
    prepared = folder / "input.wav"
    if not prepared.exists():
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(prepared)], timeout=300)
    # Files written under Demucs follow the input filename stem.
    stems_dir = folder / "htdemucs" / prepared.stem
    expected = {name: stems_dir / f"{name}.wav" for name in ("vocals", "drums", "bass", "other")}
    if not all(p.exists() for p in expected.values()):
        if progress:
            progress("Separating audio into stems with Meta Demucs", .2)
        py = os.environ.get("REEL_DEMUCS_PYTHON")
        if py:
            command = [py]
        elif shutil.which("uv"):
            command = ["uv", "run", "--with", "demucs==4.0.1", "--with", "numpy<2", "--with", "soundfile", "--with", "torch==2.5.1", "--with", "torchaudio==2.5.1", "--python", "3.12", "python"]
        else:
            raise EngineError("Stem separation needs Meta Demucs. Install it or set REEL_DEMUCS_PYTHON to its Python interpreter.")
        _run(command + [str(Path(__file__).with_name("demucs_worker.py")), str(prepared), str(stems_dir)], timeout=7200)
    missing = [p.name for p in expected.values() if not p.exists()]
    if missing:
        raise EngineError("Demucs did not return every stem. Try a shorter clip or check the model installation.")
    return expected


def _select_tracks(stems: dict[str, Path], target: str, remove: bool, out: Path) -> Path:
    names = [n for n in stems if n == target] if not remove else [n for n in stems if n != target]
    args: list[str] = []
    labels: list[str] = []
    for i, name in enumerate(names):
        args += ["-i", str(stems[name])]
        labels.append(f"[{i}:a]")
    graph = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0,{LIMITER}[mix]"
    _run(["ffmpeg", "-v", "error", "-y"] + args + ["-filter_complex", graph, "-map", "[mix]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], timeout=300)
    return out


def _copy_track(source: Path, directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    shutil.copy2(source, destination)
    return destination


def _combine_layers(dry: Path, music: Path | None, output: Path) -> Path:
    """Combine the persistent source and music layers at their shared duration."""
    if music is None:
        shutil.copy2(dry, output)
        return output
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(dry), "-i", str(music),
          "-filter_complex", f"[0:a][1:a]amix=inputs=2:duration=first:normalize=0,{LIMITER}[mix]",
          "-map", "[mix]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(output)], timeout=1200)
    return output


def _make_music_bed(auxiliary: Path | None, duration: float, op: dict[str, Any], output: Path,
                    notes: list[str]) -> Path:
    """Build a clip-length music layer; replaces the previous bed, never stacks it."""
    if auxiliary is None:
        raise EngineError("Add a music file before aligning it to the clip.")
    auxiliary = Path(auxiliary)
    if not auxiliary.exists():
        raise EngineError("Add a music file before aligning it to the clip.")
    anchor = float(op["anchor"])
    music_start = float(op.get("music_start", 0))
    mode = op.get("mode", "beat")
    info = probe(auxiliary)
    if music_start < 0 or music_start >= info["duration"]:
        raise EngineError(f"Music search start must be before the end of the track (0 to {info['duration']:.2f} seconds).")
    music_offset = music_start
    if mode == "beat":
        candidates = [b for b in analyze(auxiliary)["beats"] if b >= music_start]
        if candidates:
            music_offset = candidates[0]
        else:
            notes.append("No beat estimate was found after the chosen music start; aligned that source point directly.")
    elif mode == "drop":
        music_offset = _energy_rise(auxiliary, music_start)
        notes.append("Drop alignment uses the largest estimated short-term energy rise after the selected music search point; it may not match a musical drop.")
    lead_in = music_offset - music_start
    delay_seconds = max(0, anchor - lead_in)
    source_start = music_start if lead_in <= anchor else music_offset - anchor
    # Materialize the bed in samples: an exact anchor survives loop seams and
    # sample-rate conversion without filter-graph timestamp propagation.
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    samples, rate = sf.read(auxiliary, dtype="float32", always_2d=True)
    if rate != SR:
        divisor = math.gcd(rate, SR)
        samples = resample_poly(samples, SR // divisor, rate // divisor, axis=0)
    if samples.shape[1] == 1:
        samples = np.repeat(samples, 2, axis=1)
    elif samples.shape[1] != 2:
        raise EngineError("Music must be mono or stereo. Re-import the track first.")
    segment = samples[int(round(source_start * SR)):] * 10 ** (float(op.get("gain_db", -6)) / 20)
    delay_frames = int(round(delay_seconds * SR))
    total_frames = int(round(duration * SR))
    needed_frames = total_frames - delay_frames
    if needed_frames <= 0:
        raise EngineError("Choose a music anchor before the end of the clip.")
    seam_frames = min(int(.05 * SR), len(segment) // 4, needed_frames // 4)
    if len(segment) < int(.02 * SR) or seam_frames < int(.005 * SR):
        raise EngineError("There is too little music after that search point to align and loop. Choose an earlier music start.")
    step = len(segment) - seam_frames
    repeats = max(1, math.ceil(max(0, needed_frames - seam_frames) / step))
    if repeats > 256:
        raise EngineError("That music section needs more than 256 repeats. Choose a longer section.")
    bed = np.zeros((total_frames, 2), dtype=np.float32)
    cursor = delay_frames
    ramp = np.linspace(0, 1, seam_frames, endpoint=False, dtype=np.float32)[:, None]
    for index in range(repeats):
        length = min(len(segment), total_frames - cursor)
        if index:
            overlap = min(seam_frames, length)
            bed[cursor:cursor+overlap] *= 1 - ramp[:overlap]
            bed[cursor:cursor+overlap] += segment[:overlap] * ramp[:overlap]
            bed[cursor+overlap:cursor+length] = segment[overlap:length]
        else:
            bed[cursor:cursor+length] = segment[:length]
        cursor += step
    sf.write(output, bed, SR, subtype="FLOAT")
    seam = seam_frames / SR
    notes.append(f"Aligned the estimated {mode} point from music at {music_offset:.3f}s to clip time {anchor:.3f}s; looped remaining music with {seam * 1000:.0f}ms crossfades to fill the clip.")
    return output


def _duck_music_bed(dry: Path, music: Path, op: dict[str, Any], root: Path, cache: Path,
                    progress: Progress | None, notes: list[str]) -> Path:
    """Compress the persistent music bed under vocals from the unchanged dry layer."""
    stems = _stem_files(dry, cache, progress)
    target = "vocals"
    vocal = stems[target]
    amount = max(0, min(36, abs(float(op.get("amount_db", -12)))))
    ratio, threshold = 1 + amount / 3, 0.08 * 10 ** (-amount / 40)
    out = root / f"ducked-bed-{len(list(root.glob('ducked-bed-*.wav'))):03d}.wav"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(music), "-i", str(vocal), "-filter_complex",
          f"[0:a][1:a]sidechaincompress=threshold={threshold:.7f}:ratio={ratio:.4f}:attack=15:release=250:makeup=1,{LIMITER}[out]",
          "-map", "[out]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], timeout=900)
    notes.append(f"Ducked the music bed under the separated {target} stem from the dry clip using side-chain compression. Requested attenuation {amount:g} dB controls compressor threshold and ratio; instantaneous reduction varies with the signal.")
    return out


def _duration(path: Path) -> float:
    return probe(path)["duration"]


def _write_audio(input_audio: Path, filtergraph: str, output: Path, inputs: list[Path] | None = None, timeout: int = 1800) -> None:
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(input_audio)]
    for path in inputs or []:
        args += ["-i", str(path)]
    args += ["-filter_complex", filtergraph + f";[out]{LIMITER}[limited]", "-map", "[limited]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(output)]
    _run(args, timeout=timeout)


def _apply_audio_op(audio: Path, op: dict[str, Any], root: Path, cache: Path, track_dir: Path,
                    auxiliary: Path | None, notes: list[str], progress: Progress | None,
                    tracks: list[dict[str, Any]]) -> Path:
    kind = op["type"]
    duration = _duration(audio)
    out = root / f"audio-{len(list(root.glob('audio-*.wav'))):03d}.wav"
    if kind == "gain":
        gain = float(op["gain_db"])
        start, end = float(op.get("start", 0)), float(op.get("end", duration))
        fg = f"[0:a]volume={gain}dB:enable='between(t,{start},{end})'[out]"
        _write_audio(audio, fg, out)
    elif kind == "clean":
        strength = float(op["strength"])
        if strength == 0:
            shutil.copyfile(audio, out)
            return out
        if op.get("mode") == "speech":
            from . import speech
            if not speech.available():
                raise EngineError("Speech-focused cleanup needs the optional DeepFilterNet3 model. Spectral cleanup is available.")
            enhanced = speech.enhance(audio, root / f"speech-{len(notes)}")
            _write_audio(audio, f"[0:a]volume={1-strength}[dry];[1:a]volume={strength}[voice];[dry][voice]amix=inputs=2:normalize=0[out]",
                         out, [enhanced])
            notes.append("Speech-focused cleanup used DeepFilterNet3 at 48 kHz. It is trained for speech denoising and may suppress music or wanted non-speech sounds. Channels are enhanced independently.")
            return out
        # afftdn is a real spectral noise suppressor. Strength controls its noise floor reduction.
        noise_reduction = 4 + strength * 18
        _write_audio(audio, f"[0:a]afftdn=nr={noise_reduction:.2f}:nf=-35:tn=1:tr=1[out]", out)
        notes.append("Applied FFmpeg spectral noise reduction. This can reduce steady background noise; it does not selectively remove a named sound.")
    elif kind in {"isolate", "remove"}:
        target = op.get("target", "vocals")
        if target in {"vocals", "drums", "bass", "other"}:
            stems = _stem_files(audio, cache, progress)
            _select_tracks(stems, target, kind == "remove", out)
            notes.append(f"Stem processing: Meta Demucs htdemucs, target={target}, operation={kind}. Separation can leave bleed or artifacts.")
            track_path = _copy_track(stems[target], track_dir, f"{target}-stem-{len(tracks):02d}.wav")
            tracks.append({"name": f"{target} stem (separated)", "path": str(track_path), "kind": "source_stem"})
        else:
            try:
                from . import semantic
                if not semantic.available():
                    raise EngineError("Text-guided sound isolation is not configured on this install. Semantic separation requires the optional AudioSep/CLAP model weights and inference packages.")
                if progress:
                    progress(f"Finding '{target}' in the audio", .2)
                separated = semantic.separate(audio, target, root / f"semantic-{len(notes)}", cache_dir=cache / "semantic")
                target_path = Path(separated["target_path"])
                residual_path = Path(separated["residual_path"])
                selected = target_path if kind == "isolate" else residual_path
                if not selected.exists():
                    raise EngineError("Text-guided separator returned a missing audio stem.")
                _run(["ffmpeg", "-v", "error", "-y", "-i", str(selected), "-af", LIMITER, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], timeout=600)
                track_path = _copy_track(target_path, track_dir, f"semantic-target-{len(tracks):02d}.wav")
                residual_copy = _copy_track(residual_path, track_dir, f"semantic-residual-{len(tracks):02d}.wav")
                tracks.append({"name": f"{target} stem (text guided)", "path": str(track_path), "kind": "semantic_stem"})
                tracks.append({"name": "Remaining sounds (text guided)", "path": str(residual_copy), "kind": "semantic_residual"})
                notes.append(f"Text-guided separation was requested for '{target}'. This is an estimated target and residual and can contain bleed or artifacts.")
                notes.append("AudioSep estimates a mono target duplicated across channels; it does not preserve the target's stereo image.")
            except ImportError as exc:
                raise EngineError("Text-guided sound isolation is not configured on this install. Add the optional AudioSep/CLAP model adapter and weights.") from exc
            except EngineError:
                raise
            except Exception as exc:
                raise EngineError(f"Text-guided sound isolation failed: {exc}") from exc
    elif kind == "stem_mix":
        stems = _stem_files(audio, cache, progress)
        mix_track_dir = track_dir / f"mix-{len(tracks):02d}"
        mix_track_dir.mkdir(parents=True, exist_ok=True)
        gains: dict[str, float] = op["gains"]
        args: list[str] = []
        labels: list[str] = []
        graph: list[str] = []
        for i, (name, path) in enumerate(stems.items()):
            args += ["-i", str(path)]
            label = f"s{i}"
            labels.append(f"[{label}]")
            gain = gains.get(name, 0.0)
            graph.append(f"[{i}:a]volume={gain}dB[{label}]")
            track_file = mix_track_dir / f"{name}.wav"
            _run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-af", f"volume={gain}dB,{LIMITER}", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(track_file)], timeout=600)
            tracks.append({"name": f"{name} at {gain:+g} dB", "path": str(track_file), "kind": "stem_mix_source"})
        graph.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[out]")
        _run(["ffmpeg", "-v", "error", "-y"] + args + ["-filter_complex", ";".join(graph) + f";[out]{LIMITER}[limited]", "-map", "[limited]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], timeout=1200)
        notes.append("Mixed the four Meta Demucs music stems at the requested gains. Individual stem files were exported at this edit; later edits apply to the full mix.")
    elif kind == "duck":
        stems = _stem_files(audio, cache, progress)
        target = op.get("target", "vocals")
        vocal = stems[target]
        music = Path(auxiliary) if auxiliary and Path(auxiliary).exists() else root / "duck-music.wav"
        if music.name == "duck-music.wav":
            _select_tracks(stems, target, True, music)
        amount = max(0, min(36, abs(float(op.get("amount_db", -12)))))
        ratio, threshold = 1 + amount / 3, 0.08 * 10 ** (-amount / 40)
        # Duck the uploaded soundtrack under speech detected from the current clip.
        # Keep the clip's original mix and add the controlled music over it.
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(music), "-i", str(audio), "-i", str(vocal), "-filter_complex",
              f"[0:a]apad[m];[m][2:a]sidechaincompress=threshold={threshold:.7f}:ratio={ratio:.4f}:attack=15:release=250:makeup=1[ducked];[1:a][ducked]amix=inputs=2:duration=first:normalize=0,{LIMITER}[out]",
              "-map", "[out]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], timeout=600)
        notes.append(f"Ducked music under the separated {target} stem using side-chain compression. Requested attenuation {amount:g} dB controls compressor threshold and ratio; instantaneous reduction varies with the signal.")
    elif kind == "normalize":
        target = float(op.get("target_lufs", -16))
        target = max(-30, min(-5, target))
        scan = _run(["ffmpeg", "-v", "info", "-i", str(audio), "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], timeout=600)
        text = (scan.stderr or b"").decode("utf-8", "replace")
        # Use two-pass linear loudness normalization with measured input values.
        try:
            start = text.rindex("{", 0, text.rindex("}") + 1)
            stats = json.loads(text[start:text.rindex("}") + 1])
            filt = (f"loudnorm=I={target}:TP=-1.5:LRA=11:linear=true:print_format=summary:" 
                    f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:" 
                    f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:" 
                    f"offset={stats['target_offset']}")
        except (ValueError, KeyError, json.JSONDecodeError):
            filt = f"loudnorm=I={target}:TP=-1.5:LRA=11"
            notes.append("Loudness scan did not return all two-pass measurements; applied FFmpeg single-pass loudness normalization.")
        _write_audio(audio, f"[0:a]{filt}[out]", out)
    elif kind == "replace_audio":
        if auxiliary is None:
            raise EngineError("Upload replacement audio first.")
        start, end = float(op["start"]), float(op["end"])
        music_start = float(op.get("music_start", 0))
        if music_start + end - start > _duration(auxiliary) + .001:
            raise EngineError("The uploaded replacement is shorter than this selection. Choose a shorter selection or earlier audio offset.")
        fg = (f"[0:a]asplit=2[a][b];[a]atrim=0:{start},asetpts=PTS-STARTPTS[pre];"
              f"[1:a]atrim=start={music_start}:end={music_start+end-start},asetpts=PTS-STARTPTS,volume={float(op.get('gain_db',0))}dB[mid];"
              f"[b]atrim=start={end},asetpts=PTS-STARTPTS[post];[pre][mid][post]concat=n=3:v=0:a=1[out]")
        _write_audio(audio, fg, out, [auxiliary])
        notes.append(f"Replaced audio from {start:g}s to {end:g}s; video and clip duration are unchanged.")
    elif kind == "align_music":
        if auxiliary is None or not Path(auxiliary).exists():
            raise EngineError("Add a music file before aligning it to the clip.")
        anchor = float(op["anchor"])
        music_start = float(op.get("music_start", 0))
        mode = op.get("mode", "beat")
        music_offset = music_start
        music_info = probe(Path(auxiliary))
        if music_start >= music_info["duration"]:
            raise EngineError(f"Music search start must be before the end of the track (0 to {music_info['duration']:.2f} seconds).")
        if mode == "beat":
            beat_data = analyze(Path(auxiliary))["beats"]
            candidates = [b for b in beat_data if b >= music_start]
            if candidates:
                music_offset = candidates[0]
            else:
                notes.append("No beat estimate was found after the chosen music start; aligned that source point directly.")
        elif mode == "drop":
            music_offset = _energy_rise(Path(auxiliary), music_start)
            notes.append("Drop alignment uses the largest estimated short-term energy rise after the selected music search point; it may not match a musical drop.")
        # Keep the music lead-in when it fits before the selected beat; otherwise
        # trim only the part that would need to begin before time zero.
        lead_in = music_offset - music_start
        delay_seconds = max(0, anchor - lead_in)
        source_start = music_start if lead_in <= anchor else music_offset - anchor
        delay = max(0, int(round(delay_seconds * 1000)))
        gain = float(op.get("gain_db", -6))
        # Place the estimated point at the requested anchor, retain music lead-in
        # where it fits, and loop the remainder with short crossfades.
        needed = max(.01, duration - delay_seconds)
        segment_duration = music_info["duration"] - source_start
        seam = min(.05, segment_duration / 4, needed / 4)
        if segment_duration <= .02 or seam < .005:
            raise EngineError("There is too little music after that search point to align and loop. Choose an earlier music start.")
        repeats = max(1, math.ceil(max(0, needed - seam) / max(.01, segment_duration - seam)))
        if repeats > 256:
            raise EngineError("That short music section would need more than 256 repeats. Choose a longer music section.")
        if repeats == 1:
            music_graph, final_label = f"[1:a]atrim=start={source_start},asetpts=PTS-STARTPTS,volume={gain}dB[m0]", "m0"
        else:
            pieces = [f"[1:a]asplit={repeats}" + "".join(f"[src{i}]" for i in range(repeats))]
            pieces.extend(f"[src{i}]atrim=start={source_start},asetpts=PTS-STARTPTS,volume={gain}dB[m{i}]" for i in range(repeats))
            previous = "m0"
            for i in range(1, repeats):
                destination = f"x{i}"
                pieces.append(f"[{previous}][m{i}]acrossfade=d={seam:.6f}:c1=tri:c2=tri[{destination}]")
                previous = destination
            music_graph, final_label = ";".join(pieces), previous
        fg = (music_graph + f";[{final_label}]atrim=duration={needed},apad,atrim=duration={needed},"
              f"adelay={delay}|{delay}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0,{LIMITER}[out]")
        _write_audio(audio, fg, out, [Path(auxiliary)])
        notes.append(f"Aligned the estimated {mode} point from music at {music_offset:.3f}s to clip time {anchor:.3f}s; looped remaining music with {seam * 1000:.0f}ms crossfades to fill the clip.")
    elif kind in {"speed", "pitch"}:
        start, end = float(op["start"]), float(op["end"])
        if kind == "speed":
            factor = float(op["factor"])
            chain = _atempo(factor)
            notes.append(f"Changed speed to {factor:g}x on {start:.2f}-{end:.2f}s; audio pitch preserved={bool(op.get('preserve_pitch', True))}.")
        else:
            ratio = 2 ** (float(op["semitones"]) / 12)
            chain = f"asetrate={SR}*{ratio:.9f},aresample={SR}," + _atempo(1 / ratio)
            notes.append(f"Shifted pitch by {float(op['semitones']):g} semitones on {start:.2f}-{end:.2f}s while preserving section duration.")
        fg = (f"[0:a]asplit=3[a0][a1][a2];[a0]atrim=0:{start},asetpts=PTS-STARTPTS[p];"
              f"[a1]atrim={start}:{end},asetpts=PTS-STARTPTS,{chain},apad,atrim=duration={(end-start)/(factor if kind == 'speed' else 1)}[m];"
              f"[a2]atrim={end}:{duration},asetpts=PTS-STARTPTS[q];[p][m][q]concat=n=3:v=0:a=1[out]")
        _write_audio(audio, fg, out)
    elif kind in {"trim", "cut", "loop"}:
        start = float(op.get("start", 0))
        end = float(op.get("end", duration))
        if kind == "trim":
            fg = f"[0:a]atrim={start}:{end},asetpts=PTS-STARTPTS[out]"
        elif kind == "cut":
            if start == 0:
                fg = f"[0:a]atrim={end}:{duration},asetpts=PTS-STARTPTS[out]"
            elif end >= duration:
                fg = f"[0:a]atrim=0:{start},asetpts=PTS-STARTPTS[out]"
            else:
                fg = (f"[0:a]asplit=2[a][b];[a]atrim=0:{start},asetpts=PTS-STARTPTS[x];"
                      f"[b]atrim={end}:{duration},asetpts=PTS-STARTPTS[y];[x][y]concat=n=2:v=0:a=1[out]")
        else:
            n = int(op.get("count", 2))
            before = f"[0:a]atrim=0:{start},asetpts=PTS-STARTPTS[pre];" if start else ""
            after = f"[0:a]atrim={end}:{duration},asetpts=PTS-STARTPTS[post];" if end < duration else ""
            body_labels = []
            filter_inputs = "[0:a]asplit=" + str(n + (1 if start else 0) + (1 if end < duration else 0)) + "[" + "][".join(f"s{i}" for i in range(n + (1 if start else 0) + (1 if end < duration else 0))) + "];"
            # For a repeated selection, the source stream is trimmed independently on every split.
            idx = 0
            if start:
                filter_inputs += f"[s{idx}]atrim=0:{start},asetpts=PTS-STARTPTS[pre];"; idx += 1
            for j in range(n):
                filter_inputs += f"[s{idx}]atrim={start}:{end},asetpts=PTS-STARTPTS[r{j}];"
                body_labels.append(f"[r{j}]"); idx += 1
            if end < duration:
                filter_inputs += f"[s{idx}]atrim={end}:{duration},asetpts=PTS-STARTPTS[post];"
            pieces = (["[pre]"] if start else []) + body_labels + (["[post]"] if end < duration else [])
            fg = filter_inputs + "".join(pieces) + f"concat=n={len(pieces)}:v=0:a=1[out]"
        _write_audio(audio, fg, out)
    elif kind == "reorder":
        ranges = op["ranges"]
        labels = []
        for i, section in enumerate(ranges):
            labels.append(f"[r{i}]")
        if len(ranges) == 1:
            _write_audio(audio, f"[0:a]atrim={ranges[0]['start']}:{ranges[0]['end']},asetpts=PTS-STARTPTS[out]", out)
        else:
            # The split filter must appear once; trim each requested range in user order.
            graph = [f"[0:a]asplit={len(ranges)}" + "".join(f"[src{j}]" for j in range(len(ranges))) ]
            graph += [f"[src{i}]atrim={section['start']}:{section['end']},asetpts=PTS-STARTPTS[r{i}]" for i, section in enumerate(ranges)]
            graph += ["".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"]
            _write_audio(audio, ";".join(graph), out)
    elif kind == "fade":
        fi, fo = max(0, float(op.get("in_seconds", 0))), max(0, float(op.get("out_seconds", 0)))
        fg = f"[0:a]afade=t=in:st=0:d={fi},afade=t=out:st={max(0, duration-fo)}:d={fo}[out]"
        _write_audio(audio, fg, out)
    else:
        raise EngineError(f"The {kind} edit is unavailable.")
    return out


def _atempo(factor: float) -> str:
    """Build supported chained atempo filters while preserving pitch."""
    parts: list[float] = []
    remaining = factor
    while remaining > 2:
        parts.append(2.0); remaining /= 2
    while remaining < .5:
        parts.append(.5); remaining /= .5
    parts.append(remaining)
    return ",".join(f"atempo={p:.9f}" for p in parts)


def _apply_video_op(media: Path, op: dict[str, Any], audio_duration: float, root: Path) -> Path:
    if not _has_video(media):
        return media
    kind = op["type"]
    duration = _video_duration(media)
    out = root / f"video-{len(list(root.glob('video-*.mp4'))):03d}.mp4"
    if kind in {"speed", "trim", "cut", "loop", "reorder"}:
        inputs = ["ffmpeg", "-v", "error", "-y", "-i", str(media)]
        if kind == "speed":
            start, end, factor = float(op["start"]), float(op["end"]), float(op["factor"])
            fg = (f"[0:v]split=3[v0][v1][v2];[v0]trim=0:{start},setpts=PTS-STARTPTS[p];"
                  f"[v1]trim={start}:{end},setpts=(PTS-STARTPTS)/{factor}[m];"
                  f"[v2]trim={end}:{duration},setpts=PTS-STARTPTS[q];[p][m][q]concat=n=3:v=1:a=0[outv]")
        elif kind == "trim":
            start, end = float(op.get("start", 0)), float(op.get("end", audio_duration))
            fg = f"[0:v]trim={start}:{end},setpts=PTS-STARTPTS[outv]"
        elif kind == "cut":
            start, end = float(op["start"]), float(op["end"])
            if start == 0:
                fg = f"[0:v]trim={end}:{duration},setpts=PTS-STARTPTS[outv]"
            elif end >= duration:
                fg = f"[0:v]trim=0:{start},setpts=PTS-STARTPTS[outv]"
            else:
                fg = (f"[0:v]split=2[a][b];[a]trim=0:{start},setpts=PTS-STARTPTS[x];"
                      f"[b]trim={end}:{duration},setpts=PTS-STARTPTS[y];[x][y]concat=n=2:v=1:a=0[outv]")
        else:
            if kind == "reorder":
                ranges = op["ranges"]
                n = len(ranges)
                if n == 1:
                    fg = f"[0:v]trim={ranges[0]['start']}:{ranges[0]['end']},setpts=PTS-STARTPTS[outv]"
                else:
                    fg = f"[0:v]split={n}" + "".join(f"[v{i}]" for i in range(n)) + ";"
                    for i, section in enumerate(ranges):
                        fg += f"[v{i}]trim={section['start']}:{section['end']},setpts=PTS-STARTPTS[r{i}];"
                    fg += "".join(f"[r{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
            else:
                start, end, count = float(op.get("start", 0)), float(op.get("end", audio_duration)), int(op.get("count", 2))
                n = count + (1 if start else 0) + (1 if end < duration else 0)
                labels = [f"v{i}" for i in range(n)]
                fg = f"[0:v]split={n}" + "".join(f"[{x}]" for x in labels) + ";"
                idx, pieces = 0, []
                if start:
                    fg += f"[v{idx}]trim=0:{start},setpts=PTS-STARTPTS[pre];"; pieces.append("[pre]"); idx += 1
                for j in range(count):
                    fg += f"[v{idx}]trim={start}:{end},setpts=PTS-STARTPTS[r{j}];"; pieces.append(f"[r{j}]"); idx += 1
                if end < duration:
                    fg += f"[v{idx}]trim={end}:{duration},setpts=PTS-STARTPTS[post];"; pieces.append("[post]")
                fg += "".join(pieces) + f"concat=n={len(pieces)}:v=1:a=0[outv]"
        _run(inputs + ["-filter_complex", fg, "-map", "[outv]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], timeout=1800)
        return out
    # Audio-only changes reuse the existing video bitstream without another encode.
    return media


def render(input_media: Path, input_audio: Path, operations: list[dict[str, Any]], out_dir: Path,
           auxiliary: Path | None = None, progress: Progress | None = None,
           input_layers: dict[str, Path | str | None] | None = None) -> dict[str, Any]:
    """Apply validated source-time operations sequentially, keeping each A/V revision aligned."""
    input_media, input_audio, out_dir = Path(input_media), Path(input_audio), Path(out_dir)
    if not input_audio.exists() or not input_media.exists():
        raise EngineError("The original clip or audio track is missing. Re-import the clip and retry.")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="render-", dir=out_dir))
    cache = out_dir.parent.parent / ".stem-cache"
    cache.mkdir(exist_ok=True)
    current_audio, current_media = input_audio, input_media
    dry = Path(input_layers["dry"]) if input_layers and input_layers.get("dry") else input_audio
    music_layer = Path(input_layers["music"]) if input_layers and input_layers.get("music") else None
    if not dry.is_file() or (music_layer is not None and not music_layer.is_file()):
        raise EngineError("A saved audio layer is missing. Re-import the clip or restore its previous revision.")
    notes: list[str] = []
    mapping: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    try:
        for i, raw_op in enumerate(operations):
            before_duration = _duration(current_audio)
            op = _validate(raw_op, before_duration)
            if progress:
                progress(f"Applying {op['type']} edit", (i + .1) / max(1, len(operations)))
            kind = op["type"]
            if kind == "cinematic_ad":
                if not _has_video(current_media):
                    raise EngineError("Ad studio needs a video clip. Open a video before creating a cinematic cut.")
                from .ads import render_ad
                ad = render_ad(current_media, current_audio, op, work / f"ad-{i}", progress)
                dry, music_layer = Path(ad["audio_path"]), None
                new_audio, new_media = dry, Path(ad["video_path"])
                notes.extend(ad.get("notes", []))
                if input_layers and input_layers.get("music"):
                    notes.append("The cinematic cut uses the current combined audio; the added music layer is flattened into its soundtrack.")
            elif kind == "align_music":
                if music_layer is not None:
                    notes.append("Replaced the previous music bed with the newly aligned track.")
                music_layer = _make_music_bed(Path(auxiliary) if auxiliary else None, before_duration,
                                              op, work / "music-bed.wav", notes)
                new_audio = _combine_layers(dry, music_layer, work / f"mix-{i:03d}.wav")
            elif kind == "duck":
                if music_layer is None:
                    if auxiliary is None:
                        raise EngineError("Upload or align music before ducking it under the clip's vocal stem.")
                    seed = {"type": "align_music", "anchor": 0, "music_start": 0, "mode": "start", "gain_db": -6}
                    music_layer = _make_music_bed(Path(auxiliary), before_duration, seed, work / "music-bed.wav", notes)
                music_layer = _duck_music_bed(dry, music_layer, op, work, cache, progress, notes)
                new_audio = _combine_layers(dry, music_layer, work / f"mix-{i:03d}.wav")
            elif kind in {"gain", "speed", "pitch", "trim", "cut", "loop", "reorder", "fade"}:
                dry = _apply_audio_op(dry, op, work, cache, out_dir / "tracks", None, notes, progress, tracks)
                if music_layer is not None:
                    music_layer = _apply_audio_op(music_layer, op, work, cache, out_dir / "tracks", None, notes, progress, tracks)
                new_audio = _combine_layers(dry, music_layer, work / f"mix-{i:03d}.wav")
            elif kind == "stem_mix":
                dry = _apply_audio_op(dry, op, work, cache, out_dir / "tracks", None, notes, progress, tracks)
                new_audio = _combine_layers(dry, music_layer, work / f"mix-{i:03d}.wav")
            else:
                if music_layer is not None:
                    notes.append(f"The {kind} operation processed the current mix and flattened the separate music layer; later ducking will use this rendered mix as its dry source.")
                    music_layer = None
                dry = _apply_audio_op(current_audio, op, work, cache, out_dir / "tracks",
                                      Path(auxiliary) if auxiliary else None, notes, progress, tracks)
                new_audio = dry
            if _duration(new_audio) > 1200:
                raise EngineError("This edit would make the clip longer than 20 minutes. Shorten the repeated section.")
            if kind != "cinematic_ad":
                new_media = _apply_video_op(current_media, op, _duration(new_audio), work)
            mapping.append({"operation": op, "before_duration": before_duration, "after_duration": _duration(new_audio)})
            current_audio, current_media = new_audio, new_media
        rendered_audio = out_dir / "rendered.wav"
        shutil.copy2(current_audio, rendered_audio)
        layers_dir = out_dir / "layers"
        layers_dir.mkdir(parents=True, exist_ok=True)
        persisted_dry = _copy_track(dry, layers_dir, "dry.wav")
        persisted_music = _copy_track(music_layer, layers_dir, "music.wav") if music_layer is not None else None
        if persisted_music is not None:
            tracks.append({"name": "music bed", "path": str(persisted_music), "kind": "music_layer"})
        has_video = _has_video(current_media)
        preview: Path | None = None
        if has_video:
            preview = out_dir / "preview.mp4"
            _run(["ffmpeg", "-v", "error", "-y", "-i", str(current_media), "-i", str(rendered_audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(preview)], timeout=1800)
        result = {
            "audio_path": str(rendered_audio),
            "video_path": str(preview) if preview else None,
            "duration": _duration(rendered_audio),
            "analysis": analyze(rendered_audio),
            "tracks": tracks,
            "notes": list(dict.fromkeys(notes)),
            "mapping": mapping,
            "layers": {"dry": str(persisted_dry), "music": str(persisted_music) if persisted_music else None},
        }
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)

