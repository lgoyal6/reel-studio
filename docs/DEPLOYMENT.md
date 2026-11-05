# Deployment preparation

## Recommended target: Hugging Face Docker Space

Reel Studio has one serialized audio render worker, CPU model runtimes, FFmpeg, and project media
that must persist. A Docker Space is a closer fit than a static frontend host. Its free CPU Basic
hardware has 2 vCPU and 16 GB RAM. Hugging Face currently requires a PRO subscription (or Team or
Enterprise) to run a Docker Space on CPU Basic. CPU Basic sleeps after 48 hours without activity.
Check the current plan and Space settings before creating it.

The browser account was checked on November 4, 2026: it has no PRO subscription and shows $0 in
credits. The subscription screen offers PRO for $9/month. The CLI remains signed out. No Space or
subscription has been created by this preparation.

Official references:

- [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker)
- [Spaces hardware and pricing](https://huggingface.co/docs/hub/spaces-gpus)
- [Space storage](https://huggingface.co/docs/hub/main/en/spaces-storage)
- [Manage Spaces](https://huggingface.co/docs/huggingface_hub/guides/manage-spaces)

## What's in this image

The root `Dockerfile` builds the React frontend, installs the pinned Python application dependencies,
and includes FFmpeg and its shared audio libraries. It starts FastAPI on the `PORT` environment
variable, defaulting to Hugging Face's `7860`. The image does not contain user uploads, the local
database, model checkpoints, Muse credentials, or the Drumeo demo clip.

For a Docker Space, create a separate Space repository from this source and set this metadata at the
top of its `README.md`:

```yaml
---
title: Reel Studio
sdk: docker
app_port: 7860
---
```

Configure a Storage Bucket mounted at `/data` before accepting uploads. Without a bucket, Space
filesystem contents are ephemeral across restarts. The application stores SQLite state, imported
media, revisions, and exports below `/data/reel-studio`.

Local smoke run:

```sh
docker build -t reel-studio:local .
docker run --rm -p 127.0.0.1:8787:7860 \
  -e REEL_DATA_DIR=/data/reel-studio \
  --mount source=reel-studio-data,target=/data \
  reel-studio:local
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). The local bind keeps the port off public
interfaces. Do not reuse or mount the existing private workstation data when configuring a hosted
Space. Start with a separate, empty storage bucket.

## Hosted access and model boundaries

The app's baseline architecture is single-user. Do not expose uploads or project media publicly
until the hosted access-control and per-owner project scoping changes are present and verified.
Keep the Space private while preparing it. A public Space can make its app reachable without login
even if its source repository is private.

For the access-gated demo mode, set these values as Space secrets, never in the README, source
repository, or image:

- `REEL_PUBLIC_ORIGIN`: the exact HTTPS origin used to open the hosted app.
- `REEL_DEMO_ACCESS_CODE`: a private invite code with at least 12 characters. Share it only with
  intended reviewers.
- `REEL_SESSION_SECRET`: a randomly generated secret with at least 32 characters for signing
  session cookies.

Use the app's access gate and verify that a second browser without the invite cannot see projects,
jobs, or media. A pass through the access gate only grants the user their own projects; it does not
make Space storage multi-tenant by itself.

The image supplies deterministic editing, spectral cleanup, FFmpeg, and the labelled simple command
parser. It intentionally does not bundle HT Demucs, AudioSep, DeepFilterNet weights, or the Muse
CLI. Install model workers into persistent `/data` only after confirming Space disk/storage limits
and measuring CPU latency. The local Muse CLI's login state is not portable into this container and
must never be copied into an image or volume. A hosted Muse integration needs an approved server-side
API and secret configuration. Until then, the app must report the rules planner in its capabilities.

Do not put model weights, API tokens, OAuth state, `.env` files, sample videos, or user projects in
the Space repository or build context. The `.dockerignore` excludes common local artifacts.

## Verification status

Build the image locally before creating a Space. Verify the home page, `/api/capabilities`, media
upload, a deterministic edit, and persistence across a container restart using disposable test
media. Then verify the hosted access gate and Storage Bucket behavior with a new empty bucket. A
successful image build does not verify model inference, account billing, or public access isolation.

## Temporary creator demo

A Cloudflare Quick Tunnel can forward a separate local pilot process to an HTTPS URL without a
Cloudflare account. See [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
Use a fresh `REEL_DATA_DIR` and set the exact tunnel origin, invite code, and session secret before
starting the app. Never point the tunnel at the existing private workstation on port 8787.

This shares compute running on the Mac. The URL changes when the tunnel restarts and stops working
when the Mac sleeps or either process exits. It is a temporary demo, not persistent cloud hosting.
The pilot can reuse installed local model runtimes without copying their files or Muse credentials.
Keep uploads small for the proxy and CPU worker. Confirm unauthenticated rejection, separate browser
workspaces, real model inference, and exported media through the HTTPS origin before sharing it.
