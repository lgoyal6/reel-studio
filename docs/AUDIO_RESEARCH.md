# Reel Studio audio research and engineering plan

This note compares real source separation, cleanup, time processing, and alignment work that can fit Reel Studio. Papers report results on their own test sets. They do not guarantee that an uploaded Reel will sound clean after editing. The app should say which model ran, keep its target and residual tracks, and let the creator listen before exporting.

## Model selection by request

| User request | First engine to try | Why | Limits to show |
| --- | --- | --- | --- |
| Isolate drums, bass, or vocals in music | Meta Demucs `htdemucs` | Music-specific four-stem separation; MIT code; already exercised on a real Reel in this project | Its `other` stem groups everything else. Bleed and musical artifacts happen. It cannot pick one laugh or one person from a video. |
| Isolate a described sound, such as laughter or traffic | AudioSep, with SAM Audio as the future target if access and deployment terms fit | Text-conditioned target separation; AudioSep's official repository supports 32 kHz, text prompts, and chunk inference | Older, large model. Its 957 MB public checkpoint requires the AudioSep code and CLAP text encoder to run. The public checkpoint includes learned query encoder parameters; the official Space uses larger separate separation and CLAP checkpoints. Quality depends on how the source is described and overlaps with other sounds. |
| Remove noise from speech | DeepFilterNet, with RNNoise as a low latency alternative | Both target speech enhancement. DeepFilterNet uses full-band 48 kHz deep filtering; RNNoise is a small recurrent DSP and neural system | Speech denoisers can damage music, laughter, ambience, and transients. Apply to a speech stem where possible, never silently to every track. |
| Slow down a selected section | Rubber Band when available, otherwise a phase vocoder implementation | These change duration while attempting to preserve pitch | Listen for smearing on transients and cymbals. Keep the original and allow respeeding. |
| Fit a song to footage | Beat and onset detection, then explicit anchor alignment | Detect candidate beats and edit points; let the creator choose which beat and video event correspond | A beat detector cannot infer the creative meaning of a drop. Show the proposed anchor and let the creator change it. |

## Prior art and primary sources

### Open-domain, text-conditioned separation

**AudioSep, "Separate Anything You Describe"** uses a text encoder and a conditioned frequency-domain ResUNet. It proposes open-domain language-queried sound separation and evaluates sound events, instruments, and speech enhancement. The paper reports SDR improvement on its evaluation sets, including 7.74 dB AudioSet, 9.14 dB VGGSound, 10.51 dB MUSIC, and 9.21 dB SSNR on Voicebank Demand. These dataset numbers are evidence that the approach is viable, not an expected score for Reels.

- [Paper, arXiv:2308.05037](https://arxiv.org/abs/2308.05037)
- [Official Audio-AGI/AudioSep code](https://github.com/Audio-AGI/AudioSep), MIT license
- [Official inference README](https://github.com/Audio-AGI/AudioSep#inference)
- [Public `nielsr/audiosep-demo` checkpoint](https://huggingface.co/nielsr/audiosep-demo), Hugging Face model card declares Apache-2.0; its `pytorch_model.bin` is 957,134,817 bytes
- [Official AudioSep Space checkpoint tree](https://huggingface.co/spaces/Audio-AGI/AudioSep/tree/main/checkpoint), lists `audiosep_base_4M_steps.ckpt` at 1,264,844,076 bytes and `music_speech_audioset_epoch_15_esc_89.98.pt` at 2,352,471,003 bytes

The public compact checkpoint does not make the model a lightweight plug-in. The code constructs a CLAP/Roberta query encoder, resamples to 32 kHz, and expects a working PyTorch, torchaudio, transformers, and torchlibrosa stack. Its 957 MB checkpoint includes learned `ss_model` and `query_encoder` weights, so it avoids the larger CLAP checkpoint used by the official Space. The official example is CUDA-oriented but includes a CPU fallback. Before choosing this for the everyday path, benchmark cold model load, resident memory, separation latency per input minute, stereo-to-mono impact, and prompt-specific artifact rate on the same source set as Demucs.

For a separated target `T` and source mixture `X`, use a model-provided residual when available. Otherwise derive residual `R = X - T` at a matching sample rate, channel layout, and sample alignment. Retain the exact pre-edit source so the user can restore it. Mono separation cannot exactly reconstruct a stereo image; label the target and residual accordingly and do not claim they null against the input.

### Meta SAM Audio

SAM Audio is the strongest product match found for free-form targeting. Meta describes text prompts, visual prompts from a masked video object, and time-span prompts. The inference API returns both target and residual stems. Its paper also describes optional span prediction and candidate reranking with CLAP, SAM Audio Judge, or ImageBind.

- [SAM Audio paper, arXiv:2512.18099](https://arxiv.org/abs/2512.18099)
- [Meta SAM Audio announcement](https://ai.meta.com/blog/sam-audio/)
- [Official `facebookresearch/sam-audio` repository](https://github.com/facebookresearch/sam-audio)
- [SAM Audio model collection](https://huggingface.co/collections/facebook/sam-audio)

The official repository says Python 3.11+ and recommends a CUDA GPU. It says users must request access to model checkpoints, then authenticate to download them. The implementation and checkpoint are subject to Meta's SAM License, which needs review before redistribution or a hosted product. A public Hugging Face Space tried in the preceding demo did not run inference: it rejected the requested GPU duration before processing. Do not advertise this as a working local feature until an actual checkpoint run, model memory/latency measurements, and deployment terms are confirmed.

For a strong later feature, visual prompting could let a creator tap a visible object during a time span and request its sound. This requires tracking a chosen object through video, mapping a time-varying visual mask to the audio model, and making the selected source editable over time. The present repository or demo evidence does not show that complete Reel Studio path has been built.

### Music stems

**Hybrid Demucs v4** uses waveform and spectrogram paths with cross-domain attention. The original model supports four groups: drums, bass, vocals, and other. The published README reports 9.0 SDR on MUSDB HQ for the single `htdemucs` model and 9.2 SDR for a tuned sparse-attention setting. The source, pretrained model package, and model usage are MIT-licensed. Treat these as historical benchmark figures, not user-facing quality promises. The project is no longer actively developed.

- [Demucs repository and model notes](https://github.com/facebookresearch/demucs)
- [Demucs paper, arXiv:2111.03600](https://arxiv.org/abs/2211.08553)

**Band-split RoPE Transformer (BS-RoFormer)** is a more specialized music separation architecture. It splits the spectrum into sub-bands and uses transformer attention over frequency and time. Use a well-licensed, fixed-target checkpoint for a future vocal or instrument quality option, after verifying the individual checkpoint's license and dataset constraints. The popular lucidrains repository is a research implementation, not proof that an arbitrary checkpoint is redistribution-ready.

- [BS-RoFormer paper, arXiv:2309.02612](https://arxiv.org/abs/2309.02612)
- [lucidrains implementation](https://github.com/lucidrains/BS-RoFormer)

### Speech cleanup

**DeepFilterNet** predicts perceptually weighted spectral gains and applies short, learned complex filters to improve periodic speech components. The paper targets low-complexity, full-band speech enhancement. Its upstream repository offers a 48 kHz DeepFilterNet3 model and real-time implementations. This is appropriate for wind or room noise under speech when validated on that material, not for removing wind while preserving a nearby laugh or an animal bark.

- [DeepFilterNet paper, arXiv:2110.05588](https://arxiv.org/abs/2110.05588)
- [DeepFilterNet2 paper, arXiv:2205.05474](https://arxiv.org/abs/2205.05474)
- [Official Rikorose/DeepFilterNet repository](https://github.com/Rikorose/DeepFilterNet)
- [DeepFilterNet model examples](https://rikorose.github.io/DeepFilterNet-Samples/)

**RNNoise** combines recurrent neural speech-noise estimates with DSP. It was designed for low-latency real-time full-band speech enhancement and has a tiny runtime footprint relative to a large general separator. It is useful as an optional fast voice-cleanup pass. Its model was trained for speech and noise; applying it to a music mix can suppress desired components.

- [RNNoise paper, arXiv:1709.08243](https://arxiv.org/abs/1709.08243)
- [Xiph RNNoise repository](https://github.com/xiph/rnnoise)

### Timing and synchronization

Use source-time coordinates for each edit, then maintain an explicit mapping from source time to rendered time. A speed edit must apply the same transform to video and audio. Each operation should be reversible by replaying the immutable original plus the chosen operation list, rather than repeatedly transcoding the prior MP4.

For music fit:

1. Detect beats and onset strength on the candidate song and display a snap grid.
2. Let the creator point to a video event, such as the first visible jump, and select a musical beat or a detected high-energy onset.
3. Compute the offset and optional rate match. Preview it before applying.
4. Preserve the chosen mapping across edits. If speed changes, recompute the visible alignment and ask the user to confirm only when an anchor moves materially.

Dynamic time warping can align two recordings of the same musical passage with chroma features. It does not by itself match unrelated music to arbitrary visual events. A drop detector is likewise a candidate generator, not a semantic understanding of the edit.

- [librosa beat tracking](https://librosa.org/doc/latest/generated/librosa.beat.beat_track.html)
- [librosa onset strength](https://librosa.org/doc/latest/generated/librosa.onset.onset_strength.html)
- [librosa time stretch](https://librosa.org/doc/latest/generated/librosa.effects.time_stretch.html)
- [librosa music synchronization with DTW](https://librosa.org/doc/latest/auto_examples/plot_music_sync.html)
- [Rubber Band Library](https://breakfastquay.com/rubberband/)

## Recommended end-to-end design

1. **Keep an immutable media asset.** Probe and normalize the source once. Store source duration, sample rate, channels, video time base, and hashes. Keep audio processing on 48 kHz working media and adapt to model rates in a named adapter.
2. **Plan edits, validate, then execute.** Muse or another planner returns a JSON operation plan. Validate each operation against a Pydantic schema and media duration. The planner has no shell, filesystem, or network tool access. Never execute generated FFmpeg filter text.
3. **Use task-specific engines.** Send musical stem requests to Demucs. Send voice cleanup to DeepFilterNet or RNNoise only on the selected voice audio. Send named general-sound requests to a text separator only when installed and measured. Offer spectral denoise as a lower-confidence cleanup action with its own preview. Keep the chosen engine in the revision metadata.
4. **Use source-time edits and render from source.** Represent trim, split, gain, pan, fade, stem, pitch, time stretch, loop, duck, and music anchors as typed operations over an edit decision list. Materialize a preview from the source plus that list. Keep per-revision assets to make undo fast.
5. **Prioritize a low-friction local demo.** Lazy load heavyweight weights on first use, show download and initialization status, cache model instances and stems by source/model/query hash, and serialize inference until resource limits are known. Chunk long media with overlap and crossfades where the model supports it. Do not start multiple full model sessions on a laptop.
6. **Evaluate by target and content.** Build a small opt-in test set covering music, speech with wind, laughter near speech, traffic, pets, reverberation, and overlapping sources. Record target precision, residual quality, leakage, speech intelligibility, stereo image loss, runtime, peak memory, and whether a creator prefers the processed result. Keep before/after listening in the acceptance gate. Use objective SDR/SI-SDR only when clean references exist. Consider SAM Audio Judge only for SAM outputs and verify its model license and bias before automating decisions.
7. **Be transparent at playback and export.** Show the active engine, prompt, sample-rate conversion, and any unsupported request. Name stems as estimates. Keep A/B audition, restore-original, mute/solo, undo, and a dry export path. Never imply a sound was removed perfectly or that the stems recombine losslessly.

## What is currently verified in this project

- A real Drumeo Reel was processed with Meta Demucs to isolate the drums.
- A Muse-generated plan lowered the residual music, slowed the middle section with pitch preservation, and mapped the same time change to the video.
- The public SAM Audio web demo failed before inference because its requested GPU duration exceeded its allowance.
- AudioSep now runs locally with the hash-verified compact checkpoint and pinned worker. It strictly loads all learned state, without the redundant 2.35 GB CLAP checkpoint. A real 12-second Drumeo extraction using `drums playing` returned same-length target/residual; their sum reconstructed the input to approximately 3e-8 maximum sample error. This checks accounting, not perceptual quality.
- On one controlled synthetic speech plus colored/gusting-noise fixture, AudioSep's `a person speaking` target improved SI-SDR from 7.51 to 12.57 dB. This is one known-reference fixture, not evidence for every real wind/laughter/overlapping-voice request.
- DeepFilterNet3 is installed in an isolated local worker, with the official v0.5.6 model archive hash verified and a real smoke inference passed. The code is dual MIT/Apache-2.0. The compact archive is about 8 MB. On that same controlled fixture, it improved SI-SDR from 7.51 to 12.59 dB, preserved duration, and returned stereo 48 kHz float audio without clipping. Each channel is enhanced separately; this does not establish stereo perceptual coherence.
- FFmpeg spectral cleanup improved SNR from approximately 17 to 28 dB on a separate stationary-noise-plus-tone fixture. This is not a speech quality benchmark.
- Tests additionally check gain, pitch, duration and visible-flash/audio-burst synchronization. All app and model measurements must be refreshed after implementation changes.

## Product caveat

Most components in this plan are existing open models or standard media operations. The potential product contribution is their integration into Instagram-style creator editing: a typed conversational edit plan, video-aware selection, reversible multi-track edits, in-context preview, and accurate handoff to Reels. A hackathon pitch should claim the demonstrated workflow and frame visual-aware event editing as the next research direction, not claim a new separation model.

## Integrated speech enhancement source

The implemented speech cleanup mode uses [DeepFilterNet3](https://github.com/Rikorose/DeepFilterNet/tree/v0.5.6), not AudioSep or a spectral-filter substitute. Its [2023 paper](https://arxiv.org/abs/2305.08227) describes perceptually motivated real-time speech enhancement. Published real-time factors belong to the paper's tested hardware, not this app. Setup pins the model archive SHA-256 and verifies actual inference before enabling the feature. Model packages remain outside the repository; edits run offline. Noise-cleaning strength blends the enhanced estimate with dry audio, and final mix headroom is protected.

Model download/setup scripts and the research inventory are included for reproducibility. General-purpose sound selection and speech-only denoising have distinct UI controls and planner operations.
