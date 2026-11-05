from pathlib import Path

import numpy as np
import soundfile as sf

from reelstudio import engine


def tone(path, frequency, seconds=4):
    t = np.arange(48000 * seconds) / 48000
    y = .1 * np.sin(2*np.pi*frequency*t)
    sf.write(path, np.column_stack([y, y]), 48000)
    return path


def test_compound_replacement_uses_only_selected_audio_interval(tmp_path):
    dry = tone(tmp_path / "dry.wav", 440)
    music = tone(tmp_path / "replacement.wav", 880)
    result = engine.render(dry, dry, [{"type": "replace_audio", "start": 1, "end": 3, "music_start": 0, "gain_db": 0}], tmp_path / "replace", auxiliary=music)
    y, rate = sf.read(result["audio_path"])
    assert len(y) == 4 * rate
    def peak(start):
        piece = y[int(start*rate):int((start+.5)*rate), 0]
        return np.fft.rfftfreq(len(piece), 1/rate)[np.argmax(np.abs(np.fft.rfft(piece)))]
    assert abs(peak(.25)-440)<4 and abs(peak(1.5)-880)<4 and abs(peak(3.25)-440)<4


def test_compound_timeline_operations_have_distinct_work_files(tmp_path):
    dry = tone(tmp_path / "dry.wav", 440)
    music = tone(tmp_path / "music.wav", 880)
    result = engine.render(dry, dry, [
        {"type": "align_music", "anchor": 0, "mode": "start", "gain_db": -12},
        {"type": "speed", "start": 1, "end": 2, "factor": .5},
        {"type": "gain", "start": 0, "end": 5, "gain_db": -3},
        {"type": "trim", "start": .5, "end": 4.5},
    ], tmp_path / "compound", auxiliary=music)
    assert abs(result["duration"]-4)<.01
    for path in result["layers"].values():
        assert path and abs(engine.probe(Path(path))["duration"]-4)<.01


def test_realign_replaces_previous_bed_without_doubling(tmp_path):
    dry = tone(tmp_path / "dry.wav", 440)
    music = tone(tmp_path / "music.wav", 880)
    operation = {"type": "align_music", "anchor": 0, "mode": "start", "gain_db": -12}
    first = engine.render(dry, dry, [operation], tmp_path / "first", auxiliary=music)
    second = engine.render(Path(first["audio_path"]), Path(first["audio_path"]), [operation], tmp_path / "second", auxiliary=music, input_layers=first["layers"])
    a, _ = sf.read(first["audio_path"])
    b, _ = sf.read(second["audio_path"])
    assert np.max(np.abs(a-b)) < .0001
    assert any("Replaced" in note for note in second["notes"])
