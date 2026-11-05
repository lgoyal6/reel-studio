# Reel Studio

A local conversational audio editor for Reels and other short videos. Upload a clip, describe an
edit, compare the result, and export it. Muse plans typed edits; specialized local models and
FFmpeg render them. Originals and successful revisions remain available for undo and branching.

The application is a standalone web workstation. It does not inject controls into Instagram.

## Hackathon contribution

Built for the Meta Global AI Developer Hackathon by Laksh Goyal. The hackathon work includes
the React editing workstation, FastAPI project and job service, Muse edit planner and validation,
audio worker integrations, synchronized timeline operations, reversible revision history,
preview/export workflow, and cinematic ad compositor integration. The audio models, FFmpeg,
and Glissade are existing upstream projects, not models or rendering engines trained or authored
for this submission. Their roles and source links are documented below and in the research inventory.

The proposed product direction is a conversational audio-editing step within Reels or Edits.
The submitted implementation runs independently and exports files for manual import.

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22+, and FFmpeg/FFprobe on PATH.
The optional general-sound worker also uses Python 3.11. Models run on CPU by default.

From this directory:

```sh
uv sync --python 3.12
cd frontend
npm ci
npm run build
cd ..
python3 scripts/setup_models.py
export REEL_DEMUCS_PYTHON="$HOME/.codex/work/reel-studio/models/demucs/bin/python"
python3 scripts/dev.py
```

Open [127.0.0.1:8787](http://127.0.0.1:8787). The default server binds to loopback. For an invited
pilot, use a separate data directory and the browser workspace access gate described in
[deployment preparation](docs/DEPLOYMENT.md). It is a pilot gate, not a production account system
or a durable cloud job service.

Music stems can also run through a pinned `uv` worker on first request if no dedicated Demucs
interpreter is configured. The explicit setup above installs dependencies and downloads weights
before editing. No cloud audio inference or paid provider is required for the installed workers.

## Enable Muse conversation

Use an installed, signed-in Muse CLI:

```sh
export REEL_MUSE_BIN="$(command -v muse)"
python3 scripts/dev.py
```

If Muse lives elsewhere, set `REEL_MUSE_BIN` to its executable. The app uses your existing Muse
authorization and model credits. It never reads or displays credential files. A planning call has
shell, writes, web tools, reminders, and delegation disabled. Model output passes through a fixed
operation schema with local numeric/range validation before processing.

Without Muse, direct controls still work. A labelled command parser handles a few simple requests,
such as "isolate drums". It is not presented as a general language model. Configured Muse failures
remain visible rather than silently falling back to a different model.

Examples:

- "Keep drums at their original level and lower vocals, bass and other by 12 dB. Then slow seconds
  4 through 8 to 0.75x without changing pitch."
- "Remove the vocals."
- "Cut seconds 2 through 4, then add a half-second fade at the end."
- "Move this selected section to the beginning."
- "Line up the first detected beat after music time 2 seconds with video time 4 seconds."

## General sound selection

AudioSep is an optional local, text-guided separator for targets beyond vocals/drums/bass/other.
Its isolated worker needs a public checkpoint of approximately 960 MB, plus Python packages and
tokenizer/config assets. The setup verifies the pinned checkpoint and model state before marking
the worker ready:

```sh
python3 scripts/setup_semantic.py
```

The script prints environment variables for the runtime it installed. Set those in the shell used
to launch the app if you choose a non-default location. Inference runs offline after setup.
The model estimates a mono target and duplicates it across the working channels; target stereo
imaging is lost. It can retain unwanted sounds or suppress wanted sounds. Target plus residual
reconstruction is an engineering check, not evidence of perceptual separation quality.

Meta SAM Audio is researched as an alternative, not silently substituted or claimed to be running.
Its official weights/access and deployment conditions are separate from this installed AudioSep
worker. See the [research inventory](docs/AUDIO_RESEARCH.md) for papers, repos, and license caveats.

## Speech denoising

```sh
python3 scripts/setup_speech.py
```

This optional worker uses DeepFilterNet3 from the official v0.5.6 release. It downloads an approximately 8 MB model plus an isolated Python runtime and packages, then verifies real inference. Use **Clean speech** in More tools, or ask Muse for speech-focused denoising. It processes channels independently and preserves timing. It is trained for speech; desired music, laughter and ambience may be suppressed. The broad **Clean up audio** action remains spectral cleanup.

## Workstation features

- Video/audio import and audio extraction, actual waveforms, beat estimates, numeric time selection.
- Musical stem isolation/removal and four-stem gain mixing with Meta HT Demucs.
- Optional text-guided target and residual estimates with AudioSep.
- Spectral noise reduction, optional DeepFilterNet3 speech denoising, selected-range gain, loudness normalization and fades.
- Pitch-preserving speed changes, independent pitch shifts, trim, cut, loop, reorder and audio replacement.
- Uploaded music alignment by exact source point, estimated beat or estimated energy rise.
  Music retains its lead-in when feasible and fills the clip with short loop crossfades.
- Added-music ducking using a Demucs vocal estimate as the sidechain, not perfect speech detection.
- A/B preview, persistent history, undo/redo, and failed-edit preservation of the current revision.
- MP4 and WAV export; separated layers can be downloaded individually or in a ZIP.
- Ad studio: 15 or 30 second portrait/landscape cuts from the current footage and audio, with
  animated titles, a camera push, and a brand/call-to-action close.

Edits execute sequentially. Times for a later operation refer to the output of the preceding one.
Video follows timing edits. Unchanged video bitstreams are copied during audio-only edits, avoiding
an unnecessary new video encode. Working audio stays lossless between revisions.

Stem downloads identify their separation stage. Later full-mix processing can differ from those
individual files; read the revision notes. The current rendered WAV/MP4 is the authoritative preview. Added music is stored separately, so realigning it replaces the bed and ducking modifies that bed without adding another copy. Timing edits transform both layers together. Full-mix separation, cleaning or normalization can flatten layers; the revision notes say when that happens.
Beat/drop detection and sound selection are estimates, with numeric controls for correction.

## Cinematic ad cuts

Install the optional local compositor:

```sh
sh scripts/setup_ads.sh
```

The setup pins [Glissade](https://github.com/tyevco/glissade) 0.79.0, an Apache-2.0 TypeScript
motion-graphics engine with a headless Skia renderer. It downloads IBM Plex Sans under the OFL,
retains its license, and runs a real smoke render before enabling the feature. The runtime and font
live outside this repository. Glissade is an experimental dependency; the version is pinned and
checked locally rather than treated as a mature hosted service.

Open a video, select **Ad studio**, and enter a headline, optional supporting line, brand, and
call to action. Choose 9:16 or 16:9, 15 or 30 seconds, and an accent. **Create cinematic cut** saves
a new revision that can be compared, undone, edited further, and exported through the normal flow.
It composes existing footage. It does not generate new scenes or require video-generation credits.
Short source material repeats to fill the selected length; inspect the rendered loop and revision
notes before exporting. Audio-only projects cannot create a video ad.

Muse can also plan an ad when the renderer is installed and the request supplies the headline,
brand, and call to action. Missing copy prompts a question instead of inventing a product claim.

[OpenScreen](https://github.com/getopenscreen/openscreen) is a useful MIT-licensed tool for polished
product recordings. [Revideo](https://github.com/midrender/revideo) is an MIT alternative compositor.
[Cua Launchpad](https://github.com/trycua/launchpad) supplies launch-video templates, but its
Remotion dependency has separate license terms from the template repository.

## Sample clip

The demonstration uses seconds 30-42 of the [public Drumeo / Chad Smith Reel](https://www.instagram.com/reel/C-LQOCqvX-I/).
Media is not included in this source tree. To prepare the sample locally:

```sh
python3 scripts/setup_demo.py
```

This needs `yt-dlp` on PATH. If a sample file is already available, use
`python3 scripts/setup_demo.py --source /absolute/path/to/clip.mp4`. Uploading your own file is the
primary import path and does not require Instagram login or public-link downloading.

## Storage and limits

Project data defaults to `~/.codex/work/reel-studio/data`, including SQLite documents, originals,
lossless revisions, model output caches and exports. Override with `REEL_DATA_DIR`. All revisions
are retained until you remove the corresponding local project data. Media and credentials should
never be committed. Uploaded filenames are display names, not trusted paths.

Input limits are 250 MB and 300 seconds per file. A rendered revision is limited to 20 minutes.
Heavy processing is serialized. The interface reports queued/running/failed jobs and preserves the
previous playable revision on failure. Restarting the server marks unfinished jobs interrupted.

## Verify

```sh
uv run pytest -q
cd frontend
npm run build
```

Tests use real FFmpeg processing of known synthetic fixtures. They cover import/export through HTTP,
failure preservation, state recovery, rejected plans, range operations, pitch/frequency behavior,
noise reduction against a clean reference, and a synchronized flash/tone after retiming. Model
inference and browser checks are separate integration evidence; unit tests do not establish general
separation quality. Paper benchmark numbers are not measurements of this app.

Read [DESIGN.md](DESIGN.md) for the elaborate architecture, build sequence, acceptance criteria,
optimization experiments, quality gates and deployment boundaries.

## License

Reel Studio's original application code is available under the [MIT License](LICENSE).
Third-party libraries, models, checkpoints, and media retain their own licenses and terms;
see the research inventory for the model and rendering dependencies.
