#!/bin/sh
# Push the public demo to a Hugging Face Space (Docker SDK). Needs `hf auth login` with a write token.
#   sh deploy/hf_push.sh <hf-user>/trialproof-studio
# Set the model in the Space settings as secrets: TP_LLM_BASE_URL, TP_LLM_MODEL, TP_LLM_API_KEY.
set -eu
SPACE=${1:?usage: hf_push.sh <user>/<space>}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
B=$(mktemp -d)
cd "$ROOT"
git ls-files -z | xargs -0 -I{} rsync -R "{}" "$B/"   # tracked files only: no data, no var/, no secrets
cp deploy/hf_README.md "$B/README.md"
hf repo create "$SPACE" --repo-type space --space_sdk docker --exist-ok >/dev/null
hf upload "$SPACE" "$B" . --repo-type space --commit-message "deploy $(git rev-parse --short HEAD)"
rm -rf "$B"
echo "https://huggingface.co/spaces/$SPACE"
