# 4. The P21 specifically

## 4.1 Model identification

The dedicated predicate is `y6.f.p3()`:

```java
public boolean p3(String str) { return str.startsWith("PPG_P21"); }
```

Two hardware variants share that prefix and they **differ on the wire**:

| Advertised name | `f51375p` (type) | Print width `f51376q` | Trailing feed `f51377r` | Raster format |
|---|---|---|---|---|
| `PPG_P21_xxxx`, `PPG_P21_HD/UD/UHD_xxxx` | 0 | **384 dots** (58 mm) | **96** (= 12×8) | **uncompressed `GS v 0`** |
| `PPG_P21+_xxxx` | 2 | **576 dots** (80 mm) | 108 | **zlib-compressed `1F 00`** |

Derivation (`y6.f`, device table at ~line 1996 and ~2015):

```java
} else if (name.equalsIgnoreCase("PeriPage_A2") || name.startsWith("PPG_A2+")
        || name.startsWith("PPG_A2")  || name.startsWith("PPG_A2Neo")
        || name.startsWith("PPG_P21_") || E2(name)) {
    this.f51375p = 0;
    this.f51376q = 384.0f;
    if (!E2(name)) i16 = (name.startsWith("PPG_P21_") ? 12 : 9) * 8;   // 96
    this.f51377r = i16;
}
...
} else if (... || name.startsWith("PPG_P21+_")) {
    this.f51375p = 2;  this.f51376q = 576.0f;  this.f51377r = 108;
}
```

So the plain P21 is treated as a member of the **A2 family**, and the P21+ as a member
of the **A2+ / H2+ / PC20+ family**.

Firmware gate seen at `y6.f:4593` — some behaviour is enabled only when the version
number parses to `> 125` on a `PPG_P21*`.

## 4.2 Connect

1. **Discovery**: classic `BluetoothAdapter.startDiscovery()` (`y6.f:3738`). The P21
   advertises as `PPG_P21_xxxx`. No BLE/GATT involved.
2. `Thread.sleep(500)` before opening the socket (`y6.f`, connect runnable).
3. RFCOMM connect to `00001101-0000-1000-8000-00805F9B34FB`, with the
   `createRfcommSocket(1)` reflection fallback (`y0.e.e`).
4. On success the app runs a **device-info chain**. Because `PPG_P21_*` starts with
   `PPG_`, `y6.f.l1()` is true, so `y6.f.P()` picks `J0()`:

```
  ->  10 FF 20 EE <rand:1..255>          get UserKey
      <- "<userkey>"                      (ASCII)
  ->  (200 ms later)  10 FF 70            get all device info
      <- "a|b|MAC|VERSION|SN|BATTERY|..." (pipe-separated, needs >= 5 '|')
```

The device-info string is split on `|` and read positionally
(`y6.f$a.v(String)`): index **2 = MAC**, **3 = firmware version**, **4 = SN**,
**5 = battery %**.

### The UserKey is *not* printer-side authentication

`10 FF 20 EE <rand>` returns a key that the app forwards to PeriPage's **backend**:

```java
// CommonPresenter.deviceVerify()
sign = MD5(String.format("%s_%s_%s_Ald@2023", sn, userKey, randNum));
POST { deviceSn, deviceKey, randNum, sign, source:1 }
```

That is an anti-counterfeit / genuine-device check performed by the server. **The
printer will accept and print data without it** — nothing in `y0.h` gates the print
commands on the key. A third-party client can skip steps 4 entirely and go straight
to printing.

## 4.3 Printing text

**The P21 has no text mode in this app.** There is exactly one command carrying
free-form text bytes (`10 FF 31 02`, `y0.h.p`) and it is Wi-Fi provisioning
(`y6.a.W0(ssid, pwd)`). Every visible glyph is rasterised on the phone.

The text pipeline (`TextPrintPreviewActivity`):

```
EditText content laid out into a View
  -> g0.S(view)          : View.draw() onto a white ARGB_8888 Canvas
  -> y6.c.r0(bitmap)     : since z0()==false -> if.b.u(bmp, q0())
       -> Imgproc.resize(bmp, width = 384, height = 384 / aspect)   [OpenCV]
       -> back to ARGB_8888
  -> y6.f.X4(isLast, bitmap)
  -> y6.a.t1(...) -> y6.a.u1(...)
```

No dithering on this path — just a bilinear resize, then the fixed threshold in
`y0.h.w()` (`mean(R,G,B) < 190` => black).

> **Images take a different route.** A photo pushed through this text path comes out
> as a black blob, because a hard threshold has no midtones. The app sends images via
> `y6.c.s0/u0` -> `if.b.B`, which adds auto-exposure and Sierra-3 error diffusion
> before the same `GS v 0` command. See §3.7 of
> [02-protocol.md](02-protocol.md).

### The exact byte sequence for a `PPG_P21_` print

Pressing "Print" first sets density (`y6.f.U4` -> `y6.a.i1`), then prints.

```
--- set density -------------------------------------------------
00 00 00 00 00 00 00 00 00 00 00 00      L()  : 12 NUL
10 FF 10 00 <density>                    Q(n) : density = uiLevel * 2
                                                 (P21 doubles it: p3()==true)
                                                 uiLevel 0/1/2 -> 0/2/4
   <- "OK"    the ONLY synchronous command in the whole path;
              the app arms a 2 s timeout and aborts the job on silence

--- print job ---------------------------------------------------
10 FF FE 01                              o()  : start job
10 FF 80 01                              m()  : enable paper back-off  [NO-OP, see below]
00 x12                                   L()  : 12 NUL
1D 76 30 00 30 00 <hLo> <hHi>            F()  : GS v 0, bytesPerRow = 384/8 = 48 = 0x30
<48 * height raster bytes>                      width/height little-endian
1B 4A 60                                 E(96): feed 96 dot-lines = 12 mm @ 203 dpi
10 FF FE 45                              R()  : end job
   <- 4F 4B AA   "OK" + the 0xAA marker that y0.g.run() special-cases
```

Everything after the density command is **fire-and-forget** — verified on hardware,
none of it acks.

> **`10 FF 80 01` is a no-op on the P21** (measured: identical inter-job gaps with it
> enabled and disabled). It reaches this model only via `y6.a.a1()`'s broad
> A2/H2/P20/P21/P22 family test `j1()`, and the app never follows it with a back-off
> *distance*, so the feature is enabled and never configured. Safe to omit.

### Verified-minimal sequence

Dropping the no-op, this is the whole thing:

```
00 x12
10 FF 10 00 <0..4>        <- "OK"
10 FF FE 01
1D 76 30 00 30 00 <hLo> <hHi> <raster>
1B 4A 60
10 FF FE 45               <- 4F 4B AA
```

### Density scale (measured)

| wire value | result |
|---|---|
| `0` | prints, faint (**not** blank) |
| `2` | mid |
| `4` | saturated |
| `5`-`255` | accepted, acks `OK`, identical to `4` |

The app's `uiLevel * 2` mapping therefore spans the firmware's entire usable range.

Notes on the branches that are *not* taken for a plain P21, verified by evaluating
every guard predicate against the name `PPG_P21_1234`:

| Guard | Value | Effect |
|---|---|---|
| `m1()` "new device type" | false | no `1F B2 10`, no compressed raster |
| `name.startsWith("PPG_P21+")` | false | uses `B()` (uncompressed) not `C()` |
| `j1()` (A2/H2/P20/P21/P22 family) | **true** | `a1()` runs -> `10 FF 80 01` is sent |
| `G1()` | false | so `m()` inside `a1()` is reached |
| `K0()/n4()/N0()/q4()/E2()/k3()/C1()` | false | no `10 FF 81 <n>` back-off distance |
| `L0()`, `j4()` | false | `E(feed)` is used, not `E(16)` / `I()` |
| `p3()` "is P21" | **true** | density is **doubled** |
| `z0()` | false | bitmap prep is plain resize, no `x0()` A4 width |

For a **`PPG_P21+_`** the two differences are:

```
10 FF FE 01
1F B2 10                                 N() : because name startsWith("PPG_P21+")
10 FF 80 01
00 x12
1F 00 <bprHi> <bprLo> <hHi> <hLo> <len32be>   compressed raster header (BIG-endian)
<raw DEFLATE stream + adler32>
1B 4A 6C                                 feed 108
10 FF FE 45
```
with `bytesPerRow = 576/8 = 72` and `len = zlibOutput.length - 2`.

## 4.4 Progress / completion

`y6.a` registers a `s4.e0` completion callback and drives multi-copy printing by
re-running the job. Actual "printing finished" detection is by **elapsed-time
estimate** (`y6.f.p0(w, h, bool)` computes an expected duration and a UI timer counts
it down), plus the async 2-byte status frames listed in §3.3 — the firmware does not
send a reliable end-of-job ACK.
