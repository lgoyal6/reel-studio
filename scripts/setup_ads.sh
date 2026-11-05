#!/bin/sh
set -eu
runtime="${HOME}/.codex/work/reel-studio/models/glissade-0.79.0"
mkdir -p "$runtime"
cd "$runtime"
if [ ! -f package.json ]; then npm init -y >/dev/null; fi
npm install --no-audit --no-fund @glissade/cli@0.79.0
mkdir -p "$runtime/fonts"
curl -fLsS 'https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsans/IBMPlexSans%5Bwdth%2Cwght%5D.ttf' -o "$runtime/fonts/IBMPlexSans.ttf"
curl -fLsS 'https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsans/OFL.txt' -o "$runtime/fonts/OFL.txt"
smoke="$(mktemp -d)"
scene="$runtime/.readiness-smoke.ts"
trap 'rm -rf "$smoke"; rm -f "$scene"' EXIT
cat > "$scene" <<EOF
import { timeline } from "@glissade/core";
import { createScene, Text, type SceneModule } from "@glissade/scene";
const font = "$runtime/fonts/IBMPlexSans.ttf";
const mod: SceneModule = {
  createScene: () => createScene({ size: { w: 320, h: 180 }, children: [
    new Text({ id: "smoke", text: "Reel Studio", position: [160, 90], fontFamily: "BrandSans", fontSize: 28, fill: "#FFFFFF", align: "center" })
  ] }),
  timeline: timeline(() => {}, { duration: 1, fps: 30, assets: { brandSans: { kind: "font", url: font, faces: [{ url: font, weight: 400 }, { url: font, weight: 700 }] } } })
};
export default mod;
EOF
"$runtime/node_modules/.bin/gs" render "$scene" --out "$smoke/readiness.mp4" --fps 30 --workers 1
ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$smoke/readiness.mp4" | grep -q video
printf 'Glissade 0.79.0 smoke render passed\n' > "$runtime/.ads-render-verified"
printf 'Glissade ad renderer installed at %s\n' "$runtime"
