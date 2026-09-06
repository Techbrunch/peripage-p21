# PeriPage P21 — protocol reverse-engineering

Reversing `PeriPage_6.10.11_APKPure.xapk` (`com.ileadtek.peripage`, versionCode 323)
to recover how the app connects to and prints on a PeriPage P21 — then reimplementing
it, validated against real hardware (`PPG_P21_F530`, firmware `V4.04_SD`).

| Document | Contents |
|---|---|
| [00-setup-and-recon.md](00-setup-and-recon.md) | tooling, unpacking, first-pass recon, where the code lives |
| [01-architecture.md](01-architecture.md) | layer map, SPP transport, write/read paths |
| [02-protocol.md](02-protocol.md) | command reference, status frames, raster + image encodings |
| [03-p21-flow.md](03-p21-flow.md) | P21 model table, connect handshake, exact print byte sequence |
| [04-hardware-validation.md](04-hardware-validation.md) | **everything measured on the real printer**, and the traps |

| Code | Status |
|---|---|
| [`../webapp/`](https://peripage-p21.meeseeks.workers.dev/app/) | **web app over BLE — the recommended client.** Live at [peripage-p21.meeseeks.workers.dev/app/](https://peripage-p21.meeseeks.workers.dev/app/). Replaces the mobile app |
| [`../ref/p21_iobt.py`](../ref/p21_iobt.py) | macOS Python client over Classic SPP. Hardware-verified |
| [`../ref/p21_macos.py`](../ref/p21_macos.py) | pyserial variant. Only works if something else holds the RFCOMM link — see §5.1 |
| [`../ref/peripage_p21.py`](../ref/peripage_p21.py) | Linux `AF_BLUETOOTH` variant. **Untested** |
| [`../tools/eval_device_predicates.py`](../tools/eval_device_predicates.py) | evaluates the APK's device predicates against a Bluetooth name |

## Executive summary

**The P21 is dual-mode, and BLE is the better transport.**

| | Classic SPP | BLE GATT |
|---|---|---|
| how | RFCOMM ch 1, UUID `00001101-…` | service `FF00`, write `FF02`, notify `FF01` (data) / `FF03` (flow control) |
| max write | 126 B | 237 B |
| pairing | required; SSP fails, PIN `0000` only | none |
| 18 KB image | ~4 s (RFCOMM paces it) | ~3 s (must be paced by the client) |
| used by the Android app | yes, exclusively | **no — it never touches this service** |

The byte protocol is **identical** on both. The app only ever uses SPP; the BLE
service is what makes a browser client possible.

**Discovery.** A P21 advertises as `PPG_P21_xxxx` on both transports. The app's own
test is literally `name.startsWith("PPG_P21")`. `PPG_P21_` is 384 dots wide (58 mm);
`PPG_P21+_` is 576 dots and uses a zlib-compressed raster.

**Text and images are both rasters.** There is no text mode. The only command
carrying free-form text (`10 FF 31 02`) is Wi-Fi provisioning. Text is thresholded at
190; images get auto-exposure plus **Sierra-3 error diffusion**; line art gets an
adaptive threshold. See §3.7.

**The print sequence**, verified minimal:

```
00 ×12
10 FF 10 00 <0..4>                        density — the ONLY command that acks
   <- "OK"
10 FF FE 01                               start job
1D 76 30 00 30 00 <hLo> <hHi> <raster>    GS v 0, 48 bytes/row, LITTLE-endian
1B 4A 60                                  feed 96 dots = 12 mm @ 203 dpi
10 FF FE 45                               end job
   <- 4F 4B AA
```

## Three firmware behaviours you cannot see in the APK

All found only by running against hardware, all documented in
[04-hardware-validation.md](04-hardware-validation.md):

1. **An unterminated job wedges the printer** (§5.8). If `10 FF FE 01` is sent and
   `10 FF FE 45` never arrives, it keeps accepting connections but executes nothing.
   Only a power cycle clears it. **Always send end-of-job in a `finally`.**
2. **A job that starves gets dropped** (§5.8). More than roughly 8 s between start-
   and end-of-job and the printer closes the channel.
3. **BLE has no backpressure** (§5.14). Push a raster at full speed and the input
   buffer overflows; the tail is silently discarded and the job still reports
   success. Pace bulk writes.

Together these bound the transfer rate on both sides: too fast truncates, too slow
disconnects.

## Client support

The web app needs **Web Bluetooth**, which constrains where it runs:

| Platform | Works |
|---|---|
| Chrome / Edge on desktop (macOS, Windows, Linux) | yes |
| Chrome / Edge / Samsung Internet on **Android** | yes — needs Bluetooth on and the *Nearby devices* permission |
| Safari, and **all** browsers on iOS/iPadOS | **no** — WebKit does not implement Web Bluetooth |
| Firefox (any platform) | no |

iOS has no first-party route; only a third-party bridging browser such as Bluefy.

Web Bluetooth also requires a **secure context**: `https://` or `localhost`. Serving
the page over plain HTTP from another machine on the LAN leaves `navigator.bluetooth`
undefined, so phone testing needs real HTTPS (a static host, or a tunnel such as
`cloudflared tunnel --url http://localhost:8765`).

## Open items

* **`PPG_P21+` compressed path** — the 576-dot, zlib `1F 00` raster with big-endian
  dimensions. Implemented and round-trips, but never run against hardware; needs the
  80 mm variant.
* **Mobile layout** — the page collapses to one column below 820 px, but has not been
  seen on a real phone.
* **Hosting** — nothing deployed yet; the app is served locally only.
* **`10 FF 10 B1..B6` toggles** — semantics unknown. Only `B3` is labelled in the app
  (设置激活, "set activation"). Exposed via `toggle()` but never fired at hardware.
* **`recharge_mileage()`** — implemented but deliberately never run: it writes a
  non-volatile consumable counter with no documented undo.
