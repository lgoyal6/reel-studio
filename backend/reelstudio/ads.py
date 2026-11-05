"""Deterministic local cinematic ad rendering using Glissade (Apache-2.0)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[str, float], None]
RUNTIME = Path.home() / ".codex/work/reel-studio/models/glissade-0.79.0"


def available() -> bool:
    return ((RUNTIME / "node_modules/.bin/gs").is_file()
            and (RUNTIME / "fonts/IBMPlexSans.ttf").is_file()
            and (RUNTIME / ".ads-render-verified").is_file()
            and shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None)


def _run(args: list[str], timeout: int = 1800, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required local ad-rendering tool is missing: {args[0]}. Run scripts/setup_ads.sh.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Ad rendering timed out. Try a shorter source clip.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or b"").decode("utf-8", "replace")[-1800:]
        raise RuntimeError(f"Ad rendering failed: {message or exc}") from exc


def _probe(path: Path) -> dict[str, Any]:
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)], 60)
    return json.loads(result.stdout)


def _quote(value: str) -> str:
    # JavaScript string literal encoding. User strings are always data, never source code.
    return json.dumps(value, ensure_ascii=True)


def _font_size(text: str, base: int, width: int, lines: int = 1) -> int:
    # Conservative glyph-width estimate; Glissade wraps at `width`, this keeps
    # long/unusual Unicode strings readable and inside the safe title area.
    columns = sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else .58 for ch in text)
    return max(18, min(base, int(width * lines / max(1.0, columns * .72))))


def _check_op(op: dict[str, Any]) -> dict[str, Any]:
    allowed = {"type", "headline", "tagline", "brand", "cta", "duration", "aspect", "accent"}
    if set(op) - allowed or op.get("type") != "cinematic_ad":
        raise ValueError("Expected a cinematic_ad operation with only headline, tagline, brand, cta, duration, aspect, and accent fields.")
    headline, brand, cta, tagline = (op.get(k, "") for k in ("headline", "brand", "cta", "tagline"))
    if not isinstance(headline, str) or not 1 <= len(headline) <= 90: raise ValueError("headline must contain 1 to 90 characters.")
    if not isinstance(brand, str) or not 1 <= len(brand) <= 40: raise ValueError("brand must contain 1 to 40 characters.")
    if not isinstance(cta, str) or not 1 <= len(cta) <= 50: raise ValueError("cta must contain 1 to 50 characters.")
    if not isinstance(tagline, str) or len(tagline) > 140: raise ValueError("tagline must contain at most 140 characters.")
    duration, aspect, accent = op.get("duration", 15), op.get("aspect", "portrait"), op.get("accent", "coral")
    if duration not in (15, 30): raise ValueError("duration must be 15 or 30 seconds.")
    if aspect not in ("portrait", "landscape"): raise ValueError("aspect must be portrait or landscape.")
    colors = {"coral": "#FF6B5E", "teal": "#42D6C5", "blue": "#6E9BFF"}
    if accent not in colors: raise ValueError("accent must be coral, teal, or blue.")
    return {"headline": headline, "tagline": tagline, "brand": brand, "cta": cta, "duration": duration, "aspect": aspect, "color": colors[accent]}


def _scene_source(media_path: str, font_path: str, config: dict[str, Any], width: int, height: int) -> str:
    d = config["duration"]
    opening, closing = d * .2, d * .2
    headline = _quote(config["headline"])
    tagline = _quote(config["tagline"])
    brand = _quote(config["brand"])
    cta = _quote(config["cta"])
    color = config["color"]
    safe_w = round(width * .82)
    headline_size = _font_size(config["headline"], 82 if height > width else 72, safe_w, 2)
    tagline_size = _font_size(config["tagline"], 30, safe_w, 2)
    brand_size = _font_size(config["brand"], 80 if height > width else 70, safe_w)
    cta_size = _font_size(config["cta"], 30, safe_w)
    # The input video has already been fit to the selected canvas. Keep typography
    # in safe margins and use concise separate scenes over the real moving footage.
    return f'''import {{ timeline }} from "@glissade/core";
import {{ createScene, Rect, Text, Video, type SceneModule }} from "@glissade/scene";
const W={width}, H={height}, D={d}, O={opening}, C={closing};
const mod: SceneModule = {{
 createScene: () => createScene({{ size:{{w:W,h:H}}, children:[
  new Video({{id:"source",assetId:"source",position:[W/2,H/2],scale:[W/480,H/270],width:480,height:270,clipDuration:D,sourceFps:30}}),
  new Rect({{id:"shade",width:W,height:H,position:[W/2,H/2],fill:"#071019",opacity:0.72}}),
  new Text({{id:"headline",text:{headline},position:[W/2,H*0.44],width:{safe_w},fontFamily:"BrandSans",fontSize:{headline_size},fontWeight:700,align:"center",fill:"#FFFFFF",opacity:0}}),
  new Text({{id:"tagline",text:{tagline},position:[W/2,H*0.56],width:{safe_w},fontFamily:"BrandSans",fontSize:{tagline_size},align:"center",fill:"#E7EDF4",opacity:0}}),
  new Rect({{id:"brand-mark",width:W,height:H,position:[W/2,H/2],fill:"#071019",opacity:0}}),
  new Text({{id:"brand",text:{brand},position:[W/2,H*0.46],width:{safe_w},fontFamily:"BrandSans",fontSize:{brand_size},fontWeight:700,align:"center",fill:{_quote(color)},opacity:0}}),
  new Text({{id:"cta",text:{cta},position:[W/2,H*0.56],width:{safe_w},fontFamily:"BrandSans",fontSize:{cta_size},fontWeight:600,align:"center",fill:"#FFFFFF",opacity:0}})
 ]}}),
 timeline: timeline(t => {{
  t.to("shade/opacity",0,{{duration:.5,at:O-.5,from:.72}})
   .to("headline/opacity",1,{{duration:.45,at:0,from:0}}).to("headline/position.y",H*.43,{{duration:.55,at:0,from:H*.48}})
   .to("tagline/opacity",1,{{duration:.4,at:.35,from:0}})
   .to("headline/opacity",0,{{duration:.4,at:O-.4,from:1}}).to("tagline/opacity",0,{{duration:.4,at:O-.4,from:1}})
   .to("brand-mark/opacity",.92,{{duration:.65,at:D-C,from:0}})
   .to("brand/opacity",1,{{duration:.5,at:D-C+.35,from:0}}).to("brand/position.y",H*.46,{{duration:.55,at:D-C+.35,from:H*.51}})
   .to("cta/opacity",1,{{duration:.45,at:D-C+.7,from:0}});
 }},{{duration:D,fps:30,assets:{{source:{{kind:"video",url:{_quote(media_path)}}},brandSans:{{kind:"font",url:{_quote(font_path)},faces:[{{url:{_quote(font_path)},weight:400}},{{url:{_quote(font_path)},weight:700}}]}}}}}})
}};
export default mod;
'''


def render_ad(input_media: Path, input_audio: Path, op: dict, out_dir: Path, progress: Progress | None = None) -> dict:
    if not available(): raise RuntimeError("Local cinematic ad renderer is unavailable. Run scripts/setup_ads.sh.")
    media, audio = Path(input_media), Path(input_audio)
    if not media.is_file() or not audio.is_file(): raise ValueError("The uploaded source video and current audio must both exist.")
    config = _check_op(op)
    info = _probe(media)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video: raise ValueError("Cinematic ads need an uploaded video. Choose a video with a picture track.")
    duration = config["duration"]
    video_duration = float(video.get("duration") or info.get("format", {}).get("duration") or 0)
    if video_duration <= 0: raise ValueError("Could not read the uploaded video's duration.")
    width, height = (720,1280) if config["aspect"] == "portrait" else (1280,720)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fitted, looped_video, raw = out_dir / "source-fit.mp4", out_dir / "source-loop.mp4", out_dir / "ad-video-silent.mp4"
    audio_out, final = out_dir / "ad-audio.wav", out_dir / "cinematic-ad.mp4"
    if progress: progress("Preparing source footage and ad soundtrack", .08)
    # Fit (never crop) source frames to the ad canvas, then use a subtle center push.
    loop_video = video_duration < duration - .02
    video_cycle = video_duration if loop_video else duration
    fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111923,setsar=1,fps=30,zoompan=z='1+0.035*on/{30*video_cycle}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps=30,trim=duration={video_cycle},setpts=PTS-STARTPTS"
    if loop_video:
        seam = min(.25, video_cycle * .1)
        fit += f",fade=t=in:st=0:d={seam},fade=t=out:st={video_cycle-seam}:d={seam}"
    _run(["ffmpeg","-y","-v","error","-i",str(media),"-vf",fit,"-an","-t",str(video_cycle),"-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(fitted)])
    video_for_scene = fitted
    if loop_video:
        _run(["ffmpeg","-y","-v","error","-stream_loop","-1","-i",str(fitted),"-t",str(duration),"-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(looped_video)])
        video_for_scene = looped_video
    # Repeat a short audio source at its original cycle length. Fade to/from silence
    # on the same fixed seam as the video so the timeline never loses half a second.
    audio_info = _probe(audio)
    audio_stream = next((s for s in audio_info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio_stream: raise ValueError("The current audio input has no audio stream.")
    source_audio_duration = float(audio_stream.get("duration") or audio_info.get("format", {}).get("duration") or 0)
    if source_audio_duration <= 0: raise ValueError("Could not read the current audio duration.")
    loop_audio = source_audio_duration < duration - .02
    audio_cycle = source_audio_duration if loop_audio else duration
    audio_filter = "aresample=48000,aformat=channel_layouts=stereo"
    if loop_audio:
        seam = min(.25, audio_cycle * .1)
        audio_filter += f",afade=t=in:st=0:d={seam},afade=t=out:st={audio_cycle-seam}:d={seam}"
    loop_source = out_dir / "audio-loop.wav"
    _run(["ffmpeg","-y","-v","error","-i",str(audio),"-map","0:a:0","-af",audio_filter,"-t",str(audio_cycle),"-ar","48000","-ac","2","-c:a","pcm_s16le",str(loop_source)])
    _run(["ffmpeg","-y","-v","error","-stream_loop","-1","-i",str(loop_source),"-t",str(duration),"-af",f"afade=t=out:st={duration-.65}:d=0.65","-ar","48000","-ac","2","-c:a","pcm_s16le",str(audio_out)])
    if progress: progress("Composing animated typography with Glissade", .28)
    env = dict(os.environ, DISABLE_TELEMETRY="true", CI="true")
    # Keep the module beside its private node_modules so Node resolves the pinned renderer.
    with tempfile.NamedTemporaryFile("w", suffix=".ts", dir=RUNTIME, encoding="utf-8", delete=False) as f:
        scene = Path(f.name)
        f.write(_scene_source(str(video_for_scene.resolve()), str((RUNTIME / "fonts/IBMPlexSans.ttf").resolve()), config, width, height))
    try:
        _run([str(RUNTIME / "node_modules/.bin/gs"),"render",str(scene),"--out",str(raw),"--fps","30","--workers","1"], timeout=duration * 90, env=env)
    finally:
        scene.unlink(missing_ok=True)
    if progress: progress("Muxing synchronized audio and finishing the ad", .88)
    _run(["ffmpeg","-y","-v","error","-i",str(raw),"-i",str(audio_out),"-map","0:v:0","-map","1:a:0","-t",str(duration),"-c:v","copy","-c:a","aac","-b:a","192k","-movflags","+faststart",str(final)])
    probe = _probe(final)
    result_video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
    result_audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    if not result_video or not result_audio or abs(float(probe["format"]["duration"]) - duration) > .15:
        raise RuntimeError("The rendered ad failed its duration or audio/video validation. Try a different source clip.")
    if progress: progress("Cinematic ad ready", 1)
    (RUNTIME / ".ads-render-verified").write_text("Glissade 0.79.0 output decoded with finite audio and video.\n", encoding="utf-8")
    loop_note = "Short picture and audio sources repeat at their original durations with matching fixed-length fade seams; no overlap shortens either cycle. If their source durations differ, their independent repeat boundaries can drift."
    return {"audio_path":str(audio_out),"video_path":str(final),"duration":duration,"tracks":[],"notes":[f"Glissade composes an opening title, a clear footage interval, and a branded CTA close with fades. The uploaded footage receives a subtle 3.5% push-in and is fit without cropping. {loop_note} Audio fades out at the final ad boundary."],"mapping":[],"layers":{"dry":str(audio_out),"music":None}}
