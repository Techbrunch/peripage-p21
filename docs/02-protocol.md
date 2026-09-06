# 3. The PeriPage protocol (as spoken to a P21)

Everything below is byte-exact from `y0.h` (command builder) and `y6.a` / `y6.f`
(sequencing). All multi-byte integers are noted individually — the protocol is
**not** consistently big- or little-endian.

## 3.1 Command families

Three prefixes appear on the wire:

| Prefix | Family | Used by P21 |
|---|---|---|
| `10 FF …` | PeriPage vendor commands | **yes** |
| `1B` / `1D` / `1F` | ESC/POS-ish subset | **yes** |
| `59 …` (`'Y'`) | newer "Y" command family (voice models, mileage) | no |

## 3.2 Vendor command reference (`10 FF …`)

Source: `y0.h`, plus the `y6.f.G4(int,int)` dispatcher.

### Session / job control

| Bytes | `y0.h` | Meaning |
|---|---|---|
| `10 FF FE 01` | `X()` | **Start print job** |
| `10 FF FE 45` | `b0()` | **End print job** |
| `10 FF FE 01` + `1F B2 10` | `X()`+`W()` | start job on A8/A9/A40/P40 families (**not** P21) |
| `10 FF FD 01` | `Y()` | (job-related, second variant) |
| `10 FF 03` | `R()` | reset / abort |
| `00 ×12` | `P()` | 12 NUL bytes — link "wake"/flush before a job |

### Print parameters

| Bytes | `y0.h` | Meaning |
|---|---|---|
| `10 FF 10 00 <n>` | `S(n)` | **Set print density / "thickness"** |
| `10 FF 10 03 <n>` | `E(n)` | Select paper type |
| `10 FF 12 <hi> <lo>` | `N(n)` | Set paper length (big-endian 16-bit) |
| `10 FF 80 01` | `Z()` | Enable paper back-off — **no-op on the P21** (§5.6) |
| `10 FF 80 00` | `a0()` | Disable paper back-off — **no-op on the P21** |
| `10 FF 81 <n>` | `y(n)` / `l(n,dpi)` | Retract paper by `n` (回退) on models that support it — `y6.f.G4(3)` sends `10 FF 81 05` behind the UI's retract button. Clamped to 120 on 300 dpi models, else 80. **Measured: no effect on a P21** (§5.6). |
| `10 FF 10 B1 <0\|1>` | `G4(12)` | toggle (model-specific) |
| `10 FF 10 B2 <0\|1>` | `G4(13)` | toggle |
| `10 FF 10 B3 01` | `G4(14)` | |
| `10 FF 10 B4 01/02` | `G4(15/16)` | |
| `10 FF 10 B6 01/02` | `G4(17/18)` | |

### Queries (device answers with an ASCII/UTF-8 string unless noted)

| Bytes | `y0.h` | `y6.a` caller | Returns |
|---|---|---|---|
| `10 FF 70` | `e()` | `G0()` "get all device info" | `a\|b\|MAC\|VERSION\|SN\|BATTERY\|…` (pipe-separated, ≥5 `\|`) |
| `10 FF 20 F0` | `f()` | `H0()` "get printer model" | model string |
| `10 FF 20 F1` | `c()` | `N0()` "get firmware version" | e.g. `V1.26_xxx` / `…OK` |
| `10 FF 20 F2` | `d()` | `K0()` "get SN" | serial number |
| `10 FF 30 10` | `b()` | `E0()` "get BT firmware version" | version string |
| `10 FF 30 12` | `a()` | `D0()` "get BT MAC" | 6 raw bytes |
| `10 FF 50 F1` | `e0()` | `B0()` "get battery" | 2 bytes, `[1]` = percent |
| `10 FF 40` | `V()` | `I0()` "query printer status" | status bytes |
| `10 FF 20 EE <rand>` | `j(b)` | `J0()` "get UserKey" | UserKey string (see §3.6) |
| `10 FF 50 F2` | — | `T0()` PB40 label height | 2 bytes, big-endian height |
| `10 FF A0 01` | `d0()` / `G4(0)` | query mileage | |
| `10 FF A0 00 <u32be>` | `Q(n)` | top-up mileage | |
| `10 FF 31 01` | `g()` | Wi-Fi: start/scan | |
| `10 FF 31 03` | `h()` | Wi-Fi: list APs — switches RX into `[`…`]` string mode | |
| `10 FF 30 12` | `a()` | | |

### Wi-Fi provisioning — `10 FF 31 02`

The only command that carries free text (`y0.h.p`, called from `y6.a.W0(ssid, pwd)`).
**This is not a text-printing command.**

```
10 FF 31 02  <len16lo> <len16hi=0>
01 <ssidLen> 00  <ssid bytes (UTF-8)>
02 <pwdLen>  00  <password bytes (UTF-8)>
03 01 00 <mode>
```
where `len16lo = ssidLen + 6 + pwdLen + 4`.

### ESC/POS subset

| Bytes | `y0.h` | Meaning |
|---|---|---|
| `1B 4A <n>` | `k(n)` | **ESC J** — feed `n` dot-lines |
| `1D 0C` | `T()` | **GS FF** — form feed / to next label mark |
| `1D 76 30 00 <wLo> <wHi> <hLo> <hHi>` + data | `F()` | **GS v 0** — raster bit image (uncompressed) |
| `1D 76 31 00 <wLo> <wHi> <hLo> <hHi>` + data | `r()` | GS v 1 variant |
| `1F 00 …` + data | `z()` | **compressed raster** (see §3.4) |
| `1F B2 10` | `W()` | start-of-job extra for A8/A9/A40 |

## 3.3 Responses

The RX path (`y0.e.l`) decodes **2-byte** status frames; anything else is passed up
raw as the payload of the current query. Meanings recovered from the `y0.c`
implementations in `y6.f$a`:

| Frame | `y0.c` | Meaning |
|---|---|---|
| `FF 01` | `d()` | **out of paper** (`onOutPaper`) |
| `FF 02` | `b()` | **cover open** (`onOpenCover`) |
| `FF 03` | `e()` | **over-heat** (`onOverHeat`) |
| `FF 04` | `a()` | **low battery** (`onLowVal`; app forces battery to 9%) |
| `FF 05` | `l()` | **cover closed** (`onCloseCover`) |
| `FF 06` | `i()` | **low mileage / consumable** (`onPrinterLowMileageAuto`) |
| `FD 01` | `u()` | **host: stop sending** (终止命令, abort) |
| `FD 02` | `u()` | **host: resume sending** (继续开始命令) |
| `FD 03` | `g()` | **printer busy** |
| `FD 04` | `t()` | **printer idle** |
| `FE xx` | `n()` | **paper error** — wrong paper type / black-mark mismatch |
| `FC xx` | `r()` | **Wi-Fi link status** |
| `[ ... ]` | `w()` | Wi-Fi scan results (bracketed string mode) |

Common ASCII replies: `OK` (`4F 4B`) for accept, `ER` (`45 52`) for error.

> **`FD 01` / `FD 02` are software flow control.** The printer can tell the host to
> abort or resume mid-transfer. A robust client should watch for these during a large
> raster; see §5.8 for the related hard timeout. Nothing in `ref/p21_iobt.py`
> currently acts on them — it only logs whatever arrives.

## 3.4 Raster encoding

### Monochrome packing — `y0.h.w(bitmap, w, h)`

```
bytesPerRow = ceil(width / 8)
for each row y:
    for each pixel x:
        if pixel != 0xFFFFFFFF (not pure white)
           and (R + G + B) / 3 < 190:
               set bit (7 - (x mod 8)) of out[y*bytesPerRow + x/8]
```
=> MSB-first, 1 = black, threshold **190** on the mean of R,G,B. Pure-white pixels are
skipped outright before the threshold test.

### Uncompressed — `y0.h.F(bitmap, mode)`  ← **what a plain `PPG_P21_` uses**

```
1D 76 30 <mode> <bytesPerRow & 0xFF> <bytesPerRow >> 8> <height & 0xFF> <height >> 8>
<raster bytes>
```
`mode` is clamped to 0..3, and is always 0 on the P21 print path.
Width and height are **little-endian**.

### Compressed — `y0.h.z(bitmap, mode)`  ← **what a `PPG_P21+_` uses**

```java
byte[] z = Code.code(raster);          // native, libCode.so
byte[] hdr = { 0x1F, 0x00,
               bytesPerRow >> 8, bytesPerRow & 0xFF,   // BIG-endian here!
               height      >> 8, height      & 0xFF,
               (len >> 24), (len >> 16), (len >> 8), len };   // len = z.length - 2
out = hdr ++ z[2 .. end]
```

`libCode.so` is statically linked zlib; `Java_com_example_sdk_Code_code` calls
`compressBound()` then `compress()` — a standard zlib stream. The Java side then
**drops the first two bytes** (the `78 9C` zlib header) and sends the rest, so the wire
format is **raw DEFLATE followed by the 4-byte Adler-32 trailer**. The declared length
also excludes those two header bytes.

Note the width/height fields are **big-endian** here but **little-endian** in `GS v 0`.

## 3.5 Chunking

`y0.h.G(byte[])` splits every write into **1024-byte chunks with `Thread.sleep(1)`
between them**. There is no ACK per chunk.

In practice the 1024 figure is an Android-socket convenience — Android fragments to
the RFCOMM MTU underneath. On a stack that does not (macOS `writeSync_length_`
rejects anything over the MTU), **chunk to the negotiated MTU instead**; 126 bytes
was negotiated with `PPG_P21_F530`. Keep the 1 ms pacing: it dominates transfer time
(9216 raster bytes took 4.2 s at MTU 126).

## 3.6 Synchronous vs fire-and-forget

Verified on hardware — this matters for a re-implementation:

| Command | Behaviour |
|---|---|
| `10 FF 10 00 <n>` (density) | **acks `OK`** — the app arms a 2 s timeout and aborts the job on silence |
| `10 FF 70`, `10 FF 20 F0/F1/F2`, `10 FF 50 F1` | reply with their payload |
| `10 FF FE 45` (end job) | replies `4F 4B AA` |
| everything else in the print path | **no reply at all** |
| `10 FF 80 xx`, `10 FF 81 <n>` | no reply, and no effect on a P21 |


## 3.7 Image pipeline (photo mode)

Text and images take **different** preprocessing paths in the app. Both end at the
same `GS v 0` command; what differs is how pixels become 1 bpp.

| | class path | preprocessing |
|---|---|---|
| text | `y6.c.r0` -> `if.b.u` | resize only; the `<190` threshold in `y0.h.w()` does the conversion |
| image | `y6.c.s0/u0` -> `if.b.B` -> `if.b.y` + `if.b.a` | resize, **auto-exposure**, **Sierra-3 dither** |
| "sketch" | `y6.c.A0` -> `if.b.T` | resize, adaptive threshold from image min/max |

### Auto-exposure — `if.b.y()` + `if.b.i()`

Grayscale, take the mean, look up a factor `f`, then apply `out = (in/255)^(1/f)*255`
(`if.b.i` does `convertTo(1/255)`, `Core.pow(1/f)`, `convertTo(*255)`):

| mean grey | `f` | exponent `1/f` | effect |
|---|---|---|---|
| < 120 | 1.8 | 0.56 | brighten hard |
| < 130 | 1.7 | 0.59 | |
| < 140 | 1.5 | 0.67 | |
| < 150 | 1.4 | 0.71 | |
| < 160 | 1.3 | 0.77 | |
| < 170 | 1.2 | 0.83 | |
| 170-180 | 1.0 | 1.00 | identity |
| < 190 | 0.9 | 1.11 | darken |
| < 200 | 0.8 | 1.25 | |
| < 210 | 0.7 | 1.43 | |
| < 220 | 0.6 | 1.67 | |
| < 230 | 0.5 | 2.00 | |
| < 240 | 0.4 | 2.50 | |
| < 250 | 0.3 | 3.33 | |
| >= 250 | 0.2 | 5.00 | darken hard |

Thermal paper has no midtones, so this pulls the histogram toward the middle before
dithering, otherwise bright photos dither to near-blank.

### Sierra-3 dither — `if.b.a()`

Threshold **127** (not the 190 used for text). Error diffused with divisor 32:

```
              *    5    3
    2    4    5    4    2
         2    3    2
```

That is the standard Sierra-3 ("Sierra filter") kernel. Note Java truncates integer
division toward zero, so terms are `int(err * w / 32)`, not a floor -- this differs
for negative error, which occurs on every white pixel (`err = value - 255`).

Output is pure 0 or 255, so the later `<190` test in `y0.h.w()` is a no-op for
images; it only packs bits.

### Why it matters

Same 192x192 source icon scaled to 384 wide:

```
photo mode (dither)     34.6% black    recognisable, tonal
text mode (threshold)   80.6% black    mid-grey background crushed to solid
```
