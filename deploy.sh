#!/usr/bin/env bash
# Build the docs site with the web app nested at /app/, then push to Cloudflare Pages.
# The web app is plain static files; Pages gives it the HTTPS origin that Web
# Bluetooth requires (navigator.bluetooth is undefined on a plain-HTTP origin).
set -euo pipefail
cd "$(dirname "$0")"

PROJECT=peripage-p21

./.venv/bin/zensical build --strict

rm -rf site/app
mkdir -p site/app
cp webapp/index.html webapp/app.js site/app/   # README.md stays out: it is source docs

# docs/README.md links the reference clients by path; ship them so those links
# resolve instead of 404ing, and force text/plain so they render in the browser
# rather than downloading.
mkdir -p site/ref site/tools
cp ref/*.py site/ref/
cp tools/*.py site/tools/
cat > site/_headers <<'HDR'
/ref/*
  Content-Type: text/plain; charset=utf-8
/tools/*
  Content-Type: text/plain; charset=utf-8
HDR

if [ "${1:-}" = "--build-only" ]; then
  echo "built site/ ($(find site -type f | wc -l | tr -d ' ') files); skipping deploy"
  exit 0
fi

npx wrangler pages deploy site --project-name "$PROJECT" --branch main
