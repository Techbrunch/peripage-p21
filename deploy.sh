#!/usr/bin/env bash
# Manual deploy. Normally unnecessary: pushing to main is built and deployed by
# Workers Builds. Use this to publish without a push, or to test a deploy locally.
set -euo pipefail
cd "$(dirname "$0")"

./build.sh
[ "${1:-}" = "--build-only" ] && exit 0

npx wrangler deploy          # assets dir and Worker name come from wrangler.jsonc
