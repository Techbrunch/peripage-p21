#!/usr/bin/env bash
# Manual deploy to Cloudflare Pages. Normally unnecessary: pushes to main are built
# and deployed by Cloudflare's Git integration. Use this to publish without a push.
set -euo pipefail
cd "$(dirname "$0")"

./build.sh
[ "${1:-}" = "--build-only" ] && exit 0

npx wrangler pages deploy site --project-name peripage-p21 --branch main
