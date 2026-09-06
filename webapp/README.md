# PeriPage P21 — web app

Replaces the Android app with a single page. No server, no native code: the browser
talks to the printer directly over **Bluetooth LE**.

```bash
cd webapp && python3 -m http.server 8765 --bind 127.0.0.1
# then open http://localhost:8765/ in Chrome or Edge
```

## Where it runs

| Platform | Works |
|---|---|
| Chrome / Edge on desktop (macOS, Windows, Linux) | yes |
| Chrome / Edge / Samsung Internet on **Android** | yes |
| Safari on iOS/iPadOS | **no** |
| Chrome, Firefox, Edge on **iOS/iPadOS** | **no** — every iOS browser is WebKit underneath, so none has Web Bluetooth |
| Firefox, Safari on desktop | no |

On iOS the only route is a third-party bridging browser such as Bluefy. Installing
Chrome on an iPhone does **not** help.

On Android, Chrome needs Bluetooth enabled and the *Nearby devices* permission
granted, or `requestDevice()` throws.

### Secure context

Web Bluetooth requires `https://` or `localhost`. Two consequences:

* opening `index.html` as a `file://` URL does not work;
* pointing a phone at `http://<your-mac-ip>:8765` does **not** work either — that is
  a plain-HTTP origin, so `navigator.bluetooth` is undefined.

For phone testing you need real HTTPS. It is deployed at **[peripage-p21.meeseeks.workers.dev/app/](https://peripage-p21.meeseeks.workers.dev/app/)**
(Cloudflare Pages, `../deploy.sh`) — open that on an Android phone and it just works.
For an unpublished change, `cloudflared tunnel --url http://localhost:8765`.

## Why this is possible at all

Web Bluetooth speaks **only BLE/GATT** — it cannot open Bluetooth Classic RFCOMM,
which is what the Android app uses (SPP, channel 1). That would normally rule out a
web app entirely.

The P21 turns out to be **dual-mode**. Alongside Classic SPP it advertises a BLE
GATT service, and the same byte protocol works over it unchanged:

| | |
|---|---|
| advertised service | `FEE7` |
| primary service | `FF00` |
| `FF02` | write / writeWithoutResponse — commands go here (max 237 B per write) |
| `FF01` | notify — **data**: `P21`, `4F 4B`, `00 64`, `AA` … |
| `FF03` | notify — **flow control**, emits `01 01` credits as the printer drains |

Measured ~78 KB/s, so an 18 KB full-width image transfers in ~0.2 s — far inside the
~8 s job timeout that makes the RFCOMM path fragile. **BLE is the better transport
for this printer**, which the Android app does not use.

## Features

* connect / disconnect, live device info (model, firmware, SN, battery)
* **Text**: font, size, weight, alignment, live preview
  * *Wrap long lines* (on by default) reflows at the paper width, breaking a word
    wider than the line; unchecked, long lines run off the right edge and are
    silently clipped by the printer
* **Image editor**: the print frame is fixed and the image moves under it —
  drag to pan, scroll or slider to zoom, rotate in 90° steps, Fit / Fill / Reset.
  Everything outside the frame is dimmed, so you can see what you are cropping
  away, and what is inside the frame is byte-for-byte what gets rasterised.
  * *Print height* sets the frame height in mm on continuous roll; on die-cut
    stock the frame **is** the label, so the height is taken from the label
    pitch and the slider is disabled
  * panning is deliberately unclamped — you can push the image off-centre and
    leave white space, which is what you want for placing a logo on a label
* **Image**: drag-and-drop, brightness/contrast, invert, six conversion modes
  * `sketch`   — adaptive threshold (mean − σ), best for logos and line art
  * `photo`    — auto-exposure + Sierra-3 dither, for photographs
  * `floyd`    — Floyd–Steinberg ÷16, fine neutral grain
  * `jarvis`   — Jarvis–Judice–Ninke ÷48, holds fine detail, softer wider grain
  * `atkinson` — Atkinson ÷8, diffuses only 6/8 of the error **on purpose**, so
    highlights and shadows stay clean instead of muddying; high contrast
  * `text`     — hard threshold at 190

  All the diffusion modes share one loop over a kernel table (`KERNELS` in
  `app.js`); they differ only in kernel and divisor. Invert is applied to the
  final 1-bit result, so the dither pattern is still computed on the real tones.
* density 0–2 (wire 0/2/4), copies, manual feed
* **Paper**: continuous roll or **die-cut labels**
  * label pitch in mm, converted at 8 dots/mm (203 dpi, measured — see
    `../docs/04-hardware-validation.md` §5.6)
  * the preview is padded or clipped to exactly one label, and says how much
    overflowed
  * on labels the job ends with `1D 0C` (GS FF, advance to the next gap mark)
    instead of the fixed `1B 4A 96` feed, which would drift out of registration
    within a few labels
* decoded status frames in the log (out of paper, cover open, over-heat, …)

### Die-cut labels — what is and isn't verified

The byte sequences are checked against the reverse-engineered protocol
(`../docs/02-protocol.md` §3.2) by a unit test over a stubbed characteristic:

```
10 FF 10 03 02        paper type = 不干胶缝隙纸, die-cut with gaps
10 FF 12 01 40        pitch 320 dot-lines = 40 mm, BIG-endian (the raster
                      header GS v 0 is little-endian; this command is not)
...raster...
1D 0C                 GS FF, advance to the next gap mark
10 FF FE 45           end of job
```

Continuous mode is unchanged and still ends with `1B 4A 60`. Run the test with
`node paper.test.mjs`.

**This path has not been run against real hardware** — no die-cut stock to hand.
What is known from the APK is that the firmware answers `FE` (decoded here as
"paper error") on a paper-type/black-mark mismatch, so a wrong pitch or plain
roll loaded in label mode should surface in the log rather than fail silently.
The paper type is sticky in firmware, which is why continuous is now sent
explicitly too: a printer left in label mode hunts for gaps that plain roll
stock does not have.

## Correctness

`app.js` is a direct port of the pipeline in `../ref/p21_iobt.py`, which was validated
against real hardware. The two implementations produce **byte-identical output** —
verified by hashing the packed bitmap for a deterministic gradient through both:

```
photo   04a9df3cf7ccbb9f     sketch  b9e4b97167877ea2     text  34967c2d943f7f96
```

`sketch`, `photo` and `text` are pinned by `dither.test.mjs` (`node dither.test.mjs`),
which hashes each mode's packed bitmap for a deterministic fixture. The kernel-table
refactor that added the three new modes was done against those pins: all three
hardware-validated modes are byte-identical before and after. The test also checks
the new modes are mutually distinct, produce non-degenerate coverage (a wrong
divisor collapses to all-black or all-white), and that invert flips every bit and
nothing else.

One subtlety that broke this at first: OpenCV's `convertTo()` saturate-casts the
gamma result back to 8-bit *before* dithering, so the JS had to round to integers
too. Keeping floats made the error diffusion drift into a visibly different bitmap.

## Flow control

BLE `writeWithoutResponse` has **no backpressure**. Pushing a raster at full speed
overruns the printer's input buffer and the tail is silently dropped — the symptom is
an image that prints only partway down, with no error anywhere.

Bulk writes are therefore paced two ways: a credit window on the `FF03` notifications
(the printer's own "I took that" signal), and a ~6 KB/s rate cap matching what RFCOMM
achieved. Both live at the top of `app.js`. If a print still truncates, lower
`TARGET_BPS`; if jobs time out, raise it — the safe window is bounded by buffer
overflow above and the ~8 s job-starvation timeout below.

## Safety

`printRaster()` sends end-of-job in a `finally` block. An unterminated job **wedges
the printer** — it keeps accepting connections but stops executing, and only a power
cycle clears it. See `../docs/04-hardware-validation.md` §5.8.
