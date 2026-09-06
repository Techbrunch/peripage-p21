# Where things stand

Full write-up in [docs/README.md](docs/README.md). This is just the pick-up-tomorrow note.

## Restart the web app

```bash
cd webapp && python3 -m http.server 8765 --bind 127.0.0.1
# http://localhost:8765/  in Chrome or Edge
```

## Deploy

Docs and web app ship as one **Worker with static assets**, `peripage-p21` — not a
Pages project. Pushing to `main` builds and deploys automatically (Workers Builds).

* docs — <https://peripage-p21.meeseeks.workers.dev/>
* web app — <https://peripage-p21.meeseeks.workers.dev/app/>  ← **the HTTPS origin the phone needs**

```bash
./build.sh                 # docs -> site/, webapp -> site/app/, sources -> site/ref/
./deploy.sh                # build, then `wrangler deploy` -- publish without a push
```

`wrangler.jsonc` is what makes this work: it declares the assets dir as `./site`.
Without it Workers Builds auto-detects, picks `webapp/`, and serves the app at `/`
with no docs at all. The build command lives in the dashboard, not in the repo
(Worker → Settings → Build): `python3 -m pip install -r requirements.txt && ./build.sh`.

## Docs site

`docs/` is a [Zensical](https://zensical.org/) project — config in `zensical.toml`,
`docs/README.md` is the landing page (Zensical treats `README.md` as the index).

```bash
./.venv/bin/zensical serve -o        # live preview on localhost:8000
./.venv/bin/zensical build --strict  # -> site/ , fails on any warning
```

Install if the venv is rebuilt: `./.venv/bin/pip install zensical`.
`site/` and `.cache/` are gitignored. `site_url` is deliberately unset — set it in
`zensical.toml` before publishing, or canonical links and the sitemap will be wrong.

## Python client

Python client (Classic SPP) needs the venv:

```bash
./.venv/bin/python -c "
import sys; sys.path.insert(0,'ref')
from p21_iobt import P21
d = P21('xx-xx-xx-xx-f5-30', channel_id=1, name='PPG_P21_F530'); d.open()
print(d.info().decode()); d.close()"
```

## Done and verified on hardware

Protocol fully reversed and reimplemented. Text and image printing both work, over
Classic SPP (Python) and BLE (web app). The web app is a working replacement for the
mobile app: connect, device info, text with font controls and word wrap, image with three dither
modes and live preview, density, copies, feed, decoded status.

## Next up

1. **Check the layout on a real phone.** It collapses to one column below 820 px but
   has never been rendered on one. Now that it is on HTTPS at
   <https://peripage-p21.meeseeks.workers.dev/app/> this is just: open it on an Android phone.
   Android only — no iOS browser implements Web Bluetooth.
2. Optional: inline `app.js` into `index.html` for a single self-contained file.

## Don't forget

* Always send end-of-job (`10 FF FE 45`). An unterminated job **wedges the printer** —
  power cycle only. Both clients already do this in a `finally`.
* Bulk BLE writes must be paced (`CREDIT_WINDOW` / `TARGET_BPS` at the top of
  `webapp/app.js`). Too fast silently truncates the image; too slow trips the ~8 s
  job timeout.
* The printer auto-sleeps after a few minutes; pairing survives it.
* It stops advertising while a browser tab holds the BLE connection.
