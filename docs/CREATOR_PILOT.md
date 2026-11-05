# Creator pilot

Use the local app at `http://127.0.0.1:8787`, or an invite-gated hosted instance after its model
capabilities and persistent storage have been verified. A container smoke test is not a hosted
model test. No external creator session has been completed yet.

## First session

Use headphones and a 10 to 30 second clip whose audio you know. Keep an unedited copy.

1. Upload a clip. Play the original and identify one sound you want to change.
2. Ask for one change, such as "isolate the drums" or "clean up the speech". Listen to the same
   passage with Original and Edited selected. Check whether the wanted sound survived.
3. Select a short range and ask to slow it to 0.75x. Check the visible event and its sound together.
4. Upload a music file. Choose a video anchor and align a beat or estimated drop. Preview and adjust
   the anchor if the estimated musical point is wrong. The chosen music passage begins near that
   anchor; this is not automatic song-structure composition.
5. Undo, then return to the edited revision. Export MP4 and play it outside the editor.

## Record the outcome

For each task, record completion, time spent waiting, number of retries, whether help was needed,
and whether the exported result was usable. For cleanup and separation, record both unwanted sound
remaining and damage to wanted sound. Keep a failure example as well as a successful example.
Use a creator's recording only with their permission; invite access alone is not permission to
publish their media or contact them.

The pilot passes when a creator can independently import, make a useful edit, compare, undo, and
export a playable result. Collect direct listening feedback before claiming quality improvements.

## Current technical evidence

- Real public online clips cover noisy outdoor speech, crowd ambience, live vocals, percussion,
  action footage, archival speech, and storm footage.
- The outdoor interview's transcript describes a sewing machine in the background. It is not a
  validated wind-removal example.
- On that one clip, official DNSMOS P.835 predicted overall scores of 2.07 for the original,
  2.21 for spectral cleanup, and 2.98 for DeepFilterNet speech cleanup. These are model predictions,
  not listener ratings. ASR changed 2 of 49 original-transcript tokens after spectral cleanup and
  4 of 49 after speech cleanup. The original ASR transcript is not a ground-truth transcript.
- AudioSep estimates the requested target and a residual. It can leave bleed, omit wanted audio,
  and collapse the target's stereo image. Render completion does not establish separation quality.

Sources and original licenses are retained with the private field-evaluation manifests. Sample
rights must be checked separately before bundling media into a public deployment.
