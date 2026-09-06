#!/usr/bin/env bash
# Build the published tree into site/: the Zensical docs, with the web app nested
# at /app/ and the reference sources alongside.
#
# Runs both locally and in Cloudflare's build image, so it must not assume .venv:
# locally zensical lives in .venv/bin, in CI it is on PATH from requirements.txt.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x .venv/bin/zensical ]; then
  ZENSICAL=.venv/bin/zensical
else
  ZENSICAL=zensical
fi

"$ZENSICAL" build --strict

# The web app is plain static files. Nesting it under the docs origin is what gives
# it HTTPS, which Web Bluetooth requires -- navigator.bluetooth is undefined on a
# plain-HTTP origin. README.md stays out: it is source docs, not part of the app.
rm -rf site/app
mkdir -p site/app
cp webapp/index.html webapp/app.js site/app/

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

# `wrangler pages deploy site` drops its account cache (account id + account name,
# which is an email) into site/.wrangler. Pages skips dot-directories on upload so
# it has never been served, but it has no business in a publish tree either.
rm -rf site/.wrangler

echo "built site/ ($(find site -type f | wc -l | tr -d ' ') files)"
