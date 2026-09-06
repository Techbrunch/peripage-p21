# 5. Validation against real hardware

Device: **`PPG_P21_F530`**, `xx:xx:xx:xx:f5:30`, firmware `V4.04_SD`,
SN `P21XXXXXXXXXXXX`. Host: macOS 26 (Darwin 25.6.0), Apple BCM_4388 controller.

The MAC's first four octets and the serial number are masked throughout. The MAC's
last two octets are left in — the advertised name ends in them, so they are public
regardless — and the masked SN keeps its real width, so the response layouts below
still line up.

## 5.1 Getting a link on macOS

macOS has no `AF_BLUETOOTH`, so the Linux-style RFCOMM socket in
`ref/peripage_p21.py` does not apply. Three obstacles, in order:

**1. TCC.** The Bluetooth API is gated per-application. `blueutil` and PyObjC both
die with `SIGABRT` (exit 134) until the *host terminal application* is granted
System Settings > Privacy & Security > Bluetooth. This is not the Claude Code
sandbox — it fails identically with the sandbox disabled.

**2. Pairing.** The printer advertises SSP with DisplayYesNo capability, so macOS
tries numeric comparison — which a printer with no screen cannot complete:

```
$ blueutil --pair xx-xx-xx-xx-f5-30
Does "PPG_P21_F530" (xx-xx-xx-xx-f5-30) display number 278716 (yes/no)?
Failed to pair ... with error 0x1f (Unspecified Error)
```

Legacy PIN pairing works:

```
$ blueutil --pair xx-xx-xx-xx-f5-30 0000        # PIN 0000
```

Without pairing, SDP still answers (so you can enumerate services), but
`openRFCOMMChannelSync` returns `kIOReturnError` (`0xe00002bc`) and `isOpen()`
stays false, and any write returns `kIOReturnNotOpen` (`0xe00002cd`).

**3. Transport.** Once paired, macOS creates an SPP tty:

```
/dev/cu.PPG_P21_F530
/dev/tty.PPG_P21_F530
```

> **Trap — this tty does NOT establish the RFCOMM link.** Opening it succeeds,
> `is_open` is true, and writes return success, but they go nowhere: the device stays
> `not connected` and nothing is ever transmitted. Measured directly:
>
> ```
> before open : 0            # blueutil --is-connected
>   t+1s open=True connected=0
>   ... through t+8s ...
> writing info query ... rx: (none)
> ```
>
> This silently swallowed an entire print job during testing and looked exactly like
> "the printer ignored my commands". Always confirm
> `blueutil --is-connected <addr>` returns `1` before trusting a write.

**The working path on macOS is IOBluetooth** (`ref/p21_iobt.py`):
`openRFCOMMChannelSync_withChannelID_delegate_` -> `status=0x0`, `isOpen=True`,
`writeSync_length_ -> 0x0`, and replies arrive on the delegate.

One PyObjC gotcha: the data callback **must** carry an explicit ObjC signature, or
PyObjC cannot size the incoming buffer and segfaults in `extract_count`:

```python
def rfcommChannelData_data_length_(self, ch, data, length):
    if data and length:
        self.buf.extend(bytes(data[:length]))

rfcommChannelData_data_length_ = objc.selector(
    rfcommChannelData_data_length_, signature=b"v@:@n^vQ")   # void,self,_cmd,id,in void*,size_t
```

Writes must be chunked to the **negotiated RFCOMM MTU (126 here)**, not the app's flat
1024 — `writeSync` rejects anything larger. The 1 ms inter-chunk pacing from
`y0.h.G()` still applies.

## 5.2 Confirmed on the wire

SDP confirms **SPP on RFCOMM channel 1** — the exact fallback the APK hardcodes
(`createRfcommSocket(1)` in `y0.e.e`). Negotiated RFCOMM MTU was 126 over the
IOBluetooth channel and 672 at the L2CAP layer.

Queries, all replying as decompiled:

| Command | Reply from `PPG_P21_F530` |
|---|---|
| `10 FF 70` | `PPG_P21_F530\|XX:XX:XX:XX:F5:30\|XX:XX:XX:XX:F5:30\|V4.04_SD\|P21XXXXXXXXXXXX\|89` |
| `10 FF 20 F0` | `P21` |
| `10 FF 20 F1` | `V4.04_SD` |
| `10 FF 20 F2` | `P21XXXXXXXXXXXX` |
| `10 FF 50 F1` | 2 bytes, `[1]` = 90 (percent) |
| `10 FF 10 00 02` | `OK` — density **does** ack (see below) |

**The device-info field layout is confirmed** exactly as derived from
`y6.f$a.v(String)`:

```
0: PPG_P21_F530        name
1: XX:XX:XX:XX:F5:30   (MAC, repeated)
2: XX:XX:XX:XX:F5:30   <- setMacAddress()
3: V4.04_SD            <- setVersion()   (split on '-', so "V4.04_SD")
4: P21XXXXXXXXXXXX     <- setSn()
5: 89                  <- setBattery()
```

Note firmware `V4.04_SD` parses to `404` under the app's
`replace(".","").replace("V","")` rule, which is `> 125` — so this unit passes the
`y6.f:4593` firmware gate.

Model reply is `P21` (bare), while the *Bluetooth* name is `PPG_P21_F530`. The app
keys **every** behavioural decision off the Bluetooth name, never the model string.

## 5.3 Reproducing

```bash
python3 -m venv .venv && ./.venv/bin/pip install pyserial pillow
blueutil --inquiry 15                       # find PPG_P21_xxxx
blueutil --pair <addr> 0000                 # PIN pairing
./.venv/bin/python ref/p21_macos.py --port /dev/cu.PPG_P21_F530 --info
```


## 5.4 The print path on hardware

Sent over a confirmed-live IOBluetooth channel, 384x192 bitmap, 9216 raster bytes:

```
GS v 0 header: 1d 76 30 00 30 00 c0 00     # 48 bytes/row, height 192 (0x00c0), LE
set_density   -> b'OK'
full job sent in 4.24 s
post-job rx   -> 4f 4b aa                  # "OK" + 0xAA
```

**Paper output confirmed** — the text printed correctly at 384 dots wide, upright,
legible, with the expected trailing feed. The reconstructed sequence in
[03-p21-flow.md](03-p21-flow.md) is correct end to end.

Two confirmations of the static analysis:

* **Density acks.** `10 FF 10 00 <n>` replies `OK`, exactly as `y6.a.i1` expects
  (case 24 checks for `0x4F 0x4B` and aborts the job on a 2 s timeout). An earlier
  run showed no ack — that was the dead-tty problem above, not the command.
* **The trailing `0xAA`.** The end-of-job reply is `4F 4B AA`. That explains the
  otherwise-bizarre special case in `y0.g.run()`, which splits a 3-byte buffer when
  the first or last byte is `0xAA`, dispatching the `AA` on its own and the remaining
  pair as a status frame.

Timing: 9216 bytes at MTU 126 with 1 ms pacing took 4.24 s, i.e. the pacing dominates
(~74 chunks). The app's 1024-byte chunking is an Android-socket convenience; the real
constraint is the RFCOMM MTU.


## 5.5 Command acceptance probing

Swept on `PPG_P21_F530` / `V4.04_SD` over a live channel.

### Density `10 FF 10 00 <n>` — acks everything

Every value `0x00`-`0xFF` returned `OK`:

```
0 1 2 3 4 5 6 7 8 9 a b c d e f 10 14 18 20 30 40 50 60 7f 80 c0 ff   -> all "OK"
```

**The ack is not validation.** The firmware accepts the byte unconditionally and
replies `OK` regardless, so a client cannot probe the useful range programmatically —
it only tells you the command was received. The app never sends more than
`uiLevel(0..2) * 2` = `0/2/4`, so anything above 4 is outside what PeriPage ships.

### Back-off commands never ack

```
10 FF 80 01   (enable)            -> (no reply)
10 FF 80 00   (disable)           -> (no reply)
10 FF 81 <n>  (distance, n=0..255)-> (no reply)
```

All fire-and-forget, which matches the app: `y0.h.Z()/a0()/y()` call `G()` and never
wait, unlike the density path in `y6.a.i1` which arms a 2 s timeout for `OK`.

This is a useful asymmetry for a re-implementation: **only `10 FF 10 00 <n>` is
synchronous.** Everything in the print sequence proper (`10 FF FE 01`, `10 FF 80 01`,
`1D 76 30 ...`, `1B 4A n`, `10 FF FE 45`) is fire-and-forget, with a single
`4F 4B AA` arriving after end-of-job.


## 5.6 Printed results: density range and back-off

### Density: the useful range is 0-4

Nine bands printed at `D = 0, 2, 4, 8, 16, 32, 64, 128, 255`, each carrying a 50%
checkerboard, a 25% dither, 1 px hairlines and a solid block:

* `D=0` is **not blank** — it prints, just faint. There is no "off" value.
* Output **saturates at `D=4`**. `D=8` through `D=255` are indistinguishable from
  `D=4`; the firmware clamps internally.

So the effective scale is **0..4**, and the app's mapping covers it exactly:

```java
// y6.f, the `n` runnable -- p3() is "name.startsWith(\"PPG_P21\")"
if (... || n3() || ...) this.f51437a = i10 * 2;    // n3() -> p3() -> true for P21
```

UI level `0/1/2` -> wire value `0/2/4` = faint / mid / saturated. The doubling is not
an arbitrary quirk, it is calibrated to the firmware's real range. Sending anything
above 4 is pointless but harmless.

### Back-off does nothing on a P21

Two jobs at a fixed 96-dot feed with back-off disabled, then two with it enabled:

```
10 FF 80 00  ->  job, job     gap = 1.6 cm
10 FF 80 01  ->  job, job     gap = 1.6 cm     (identical)
```

**`10 FF 80 01` has no observable effect on this model.** It reaches the P21 only
because `y6.a.a1()` ("setOffBack") gates on `j1()`, the broad
A2/H2/P20/P21/P22 family test — not on anything P21-specific. Note the app also never
sends a back-off *distance* (`10 FF 81 <n>`) for a P21: every sub-branch inside
`a1()` (`K0`, `n4`, `N0`, `q4`, `E2`, `k3`, `C1`) evaluates false, so the feature is
switched on and then never configured. On this hardware it is dead code.

**It can be omitted from the print sequence.**

### ...and neither does the retract command

`10 FF 80 01` only sets a feature flag, so a fair test also needs the *action*,
`10 FF 81 <n>` — which `y6.f.G4(3)` sends as `10 FF 81 05` behind the app's 回退
(retract) button. Tested with two marker pairs printed at a fixed 96-dot feed:

```
A , 10 FF 81 60 (retract 96 dots = 12 mm) , B     gap = unchanged
C , (nothing between)                     , D     gap = unchanged
```

Identical. **The entire paper back-off family is inert on the P21:**

| Command | Intent | On a P21 |
|---|---|---|
| `10 FF 80 01` | enable back-off | no effect |
| `10 FF 80 00` | disable back-off | no effect |
| `10 FF 81 <n>` | retract by `n` | no effect |

Consistent with the hardware: the P21 appears to have no reverse paper feed at all.
The commands reach it only because `y6.a.a1()` gates on `j1()`, the broad
A2/H2/P20/P21/P22 family test. All three are accepted silently (no ack, no error),
so a client gets no signal that they did nothing — which is why this needed a
printed control rather than a reply check.

### Dot pitch, derived

The gap is band height + feed = 34 px + 96 = 130 dot-lines, measured at 16 mm:

```
130 dots / 16 mm = 8.1 dots/mm  ->  203 dpi (8 dots/mm), the thermal standard
384 dots / 8 = 48 mm printable width on 58 mm stock
```

This also confirms the trailing `1B 4A 60` (96 dots) is a **12 mm** feed, about the
distance from print head to tear bar.


## 5.7 Verified-minimal sequence, confirmed equivalent

The trimmed sequence — `10 FF 80 01` removed entirely — was printed back-to-back
against the app-faithful one at the same density:

```
00 x12
10 FF 10 00 02            <- "OK"
10 FF FE 01
1D 76 30 00 30 00 72 00   <- 384 x 114 bitmap, 5472 raster bytes
1B 4A 60
10 FF FE 45               <- 4F 4B AA
```

**Output is identical in quality to the full sequence.** Dropping the back-off
command changes nothing observable on a P21, so the minimal form above is the
recommended implementation. `ref/p21_iobt.py` sends it by default and exposes
`print_raster(..., backoff=True)` for byte-faithful replay of the app.

This is the complete, hardware-verified path from a Bluetooth address to printed
text on a PeriPage P21.


## 5.8 The printer drops the link if a job starves

A real firmware behaviour, found while implementing image printing.

Sending an 18 KB raster (384x384) failed part-way with `kIOReturnUnderrun`, then
`kIOReturnNotOpen`. Instrumented, the picture was unambiguous:

```
   2520/18440  t=  2.9s stalls=0 open=True
   5040/18440  t=  5.9s stalls=0 open=True
  HARD FAIL at 6552 after 400 tries, st=0xe00002cd, isOpen=False
```

`stalls=0` up to the failure rules out flow-control back-pressure: the printer was
accepting every write, then **closed the RFCOMM channel outright**.

The trigger is elapsed time inside a job, not payload size. The transfer was running
at ~145 ms per 126-byte chunk (~870 B/s) because of a client bug (see below), so
6.5 KB took ~8 s and the firmware gave up.

With the pacing fixed, the identical 18,440-byte payload completes cleanly:

```
   2520/18440  t= 0.0s stalls=0 open=True
  ...
sent 18440/18440 in 1.7s, stalls=0, isOpen=True
```

**1.7 s vs ~21 s** for the same bytes -- a 12x speedup, `stalls=0` throughout, so the
printer never applied back-pressure at any point. It only ever objected to slowness.

**Implication for any re-implementation:** once `10 FF FE 01` has been sent, the
raster must be delivered promptly. Do not insert long waits mid-job, and do not
block on a reply that will not come — every command between start-of-job and
end-of-job is fire-and-forget (§5.5). Budget roughly **under ~8 s** between
start-of-job and end-of-job.

### The client bug that exposed it

`CFRunLoopRunInMode(mode, tick, True)` blocks for the **whole** `tick` when no input
source is pending. Pacing chunks with `pump(0.001)` implemented as a 0.05 s tick
therefore slept 50 ms+ per chunk instead of 1 ms:

| payload | chunks @ MTU 126 | at ~145 ms/chunk | result |
|---|---|---|---|
| text, 5.5 KB | 44 | ~6 s | printed (just inside the limit) |
| image, 18 KB | 147 | ~21 s | link dropped at ~6.5 KB |

The fix is a zero timeout, which drains pending sources and returns immediately:

```python
CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.0, True)   # not 0.05
```

The tell was visible earlier and missed: a 5 KB text job reporting **7.31 s** to send
is absurd for RFCOMM, and that was the same defect.


## 5.9 Two more operational quirks

### The printer sleeps, and pairing survives it

`PPG_P21_F530` auto-powers-off after a few minutes idle. Symptoms, in order of how
misleading they are:

```
blueutil --inquiry 8          -> device absent          (asleep)
openRFCOMMChannelSync         -> 0xe00002d6 kIOReturnTimeout
blueutil --paired             -> still listed, paired   (pairing is retained)
```

Press the power button to wake it; **no re-pairing is needed**. Worth checking first
whenever an open times out, because the pairing state looks perfectly healthy.

### A half-open session makes the first open fail

After an aborted transfer the printer can leave its RFCOMM session half-open, and the
next `openRFCOMMChannelSync` returns `0xe00002bc` (`kIOReturnError`) on a device that
is paired *and* awake — indistinguishable from the "not paired" failure.

`ref/p21_iobt.py` retries the open up to 6 times, closing the channel and calling
`closeConnection()` between attempts, which clears it:

```python
for attempt in range(6):
    res = dev.openRFCOMMChannelSync_withChannelID_delegate_(None, cid, delegate)
    status, ch = res
    if status == 0 and ch is not None and ch.isOpen():
        break
    if ch is not None: ch.closeChannel()
    dev.closeConnection()
    pump(1.5)
```

### Writes retry on transient underrun

`writeSync_length_` can return `0xe00002e7` (`kIOReturnUnderrun`) when the transmit
queue starves. The client retries with a run-loop pump between attempts. Note this is
distinct from the link-drop in §5.8: underrun is recoverable, `kIOReturnNotOpen` with
`isOpen() == False` is not.

## 5.10 Image printing, confirmed

The photo pipeline (§3.7 in [02-protocol.md](02-protocol.md)) was implemented and run
against the hardware using the app's own launcher icon as the source:

```
source 192x192 -> 384x384, mean grey 138.7 -> gamma f=1.5 (exponent 0.67)
Sierra-3 dither -> 18,432 raster bytes, 34.6% black
sent -> 4F 4B AA
```

Same source through the **text** path (threshold 190) packs to 80.6% black — the
mid-grey background crushes to solid. That contrast is the whole reason the app keeps
two preprocessing paths behind one wire format.


## 5.11 Status frames, and what the P21 actually reports

### `10 FF 40` is not a pollable status register

Queried on an idle, healthy printer over a confirmed-live channel:

```
10 FF 40  ->  (no reply at all)
```

Not `OK`, not a status byte — nothing. Despite `y0.h.V()` being named like a status
read and `y6.a.I0()` arming a 5 s timeout for it, **the P21 never answers it**. Status
on this model is push-only, delivered asynchronously as the `FF xx` / `FD xx` frames
in §3.3. A client must not block waiting for a reply to `10 FF 40`.

### An interrupted job WEDGES the printer

The most consequential firmware behaviour found in this session.

If `10 FF FE 01` (start job) is sent and `10 FF FE 45` (end job) never arrives —
because the raster write failed part-way — the printer enters a state where it still
accepts RFCOMM connections but **executes nothing**:

```
openRFCOMMChannelSync -> 0x0, isOpen() == True
10 FF 40   -> (none)
10 FF 70   -> (none)      <- otherwise 100% reliable
1B 4A 80   -> (none)      <- and NO paper movement
```

* It is **not** the transport: the channel is genuinely open.
* It survives closing the paper cover, disconnecting, and reconnecting.
* **Only a power cycle clears it.** Confirmed: after power-cycling, `10 FF 70`
  answered normally on the first attempt.

I initially attributed this to the paper cover being open, since that is when it was
first noticed. That was wrong — closing the cover did not restore it. The correlation
that holds is with the preceding job dying mid-raster on `kIOReturnUnderrun`.

> Not deliberately reproduced. The evidence is a single occurrence plus a clean
> recovery, so "unterminated job wedges the firmware" is the best-supported
> explanation, not a proven one.

**Mitigation — send end-of-job on every path.** `ref/p21_iobt.py` now wraps the job
body so `10 FF FE 45` is emitted even when the raster write throws:

```python
self.write(bytes([0x10, 0xFF, 0xFE, 0x01]))      # start job
try:
    ...raster, feed...
finally:
    try:
        self.write(bytes([0x10, 0xFF, 0xFE, 0x45]))   # end job, always
    except Exception:
        pass
```

Any re-implementation should do the same. The cost of getting it wrong is a printer
that needs physical intervention.

### Diagnosing a silent printer

`10 FF 70` is the canary — it replies on a healthy P21 every time. If it goes silent:

| also true | likely cause | fix |
|---|---|---|
| device absent from `blueutil --inquiry` | auto-sleep | press power button |
| channel opened, `isOpen()` true, no paper movement | wedged job (above) | power cycle |
| using `/dev/cu.*`, `blueutil --is-connected` = 0 | phantom tty (§5.1) | use IOBluetooth |

All three look identical from the host: writes "succeed", nothing comes back.



## 5.12 Remaining features ported

Everything left in the app was implemented in `ref/p21_iobt.py`. What the P21
actually supports, tested where testing was safe:

| Feature | Command | On `PPG_P21_F530` |
|---|---|---|
| paper type | `10 FF 10 03 <1..4>` | accepted silently, printer stays healthy; only CONTINUOUS is meaningful on a roll printer |
| paper length | `10 FF 12 <hi> <lo>` | ported, no effect expected on continuous roll |
| form feed | `1D 0C` | accepted, no reply |
| mileage query | `10 FF A0 01` | **no reply** — the P21 has no consumable odometer (that is a P10/P11 label-printer feature) |
| mileage recharge | `10 FF 0A 00 <u32be>` | **deliberately not tested** — writes a non-volatile counter with no documented undo; guarded behind an explicit flag |
| Wi-Fi scan / config | `10 FF 31 01/02/03` | **no reply** — the P21 is Bluetooth-only; ported for Wi-Fi models |
| `B1..B6` toggles | `10 FF 10 <B?> <v>` | **not exercised** — semantics unknown (only `B3` is labelled, 设置激活) |
| `0x59` "Y" family | `59 ...` | ported, not applicable to the P21 |
| multi-copy | (no protocol support) | works — re-run the job per copy; 2 copies in 2.3 s |
| sketch dither | (host-side) | works, see below |

Paper-type values, from `y6.f`'s dispatcher:

```
1  连续卷筒纸      continuous roll  (P21 default)
2  不干胶缝隙纸    die-cut label with gaps
3  分页纸          perforated / paged
4  纹身纸          tattoo transfer
```

### Multi-copy is a client-side loop

There is **no copies field anywhere in the protocol**. The app implements it by
re-running the entire job per copy (`y6.a.t1` registers a completion callback that
calls `u1` again). `print_copies()` does the same but waits for the end-of-job
`...AA` between copies, so the print engine is not still busy when the next
start-of-job lands — that was what provoked back-pressure in earlier testing.

### Sketch mode (`if.b.T`) is the best mode for line art

Threshold at `mean - stddev + 10` (when `mean > stddev`), from
`Core.meanStdDev` + `Imgproc.threshold(THRESH_BINARY)`.

Same 384x384 source through all three paths:

| mode | black | result |
|---|---|---|
| `photo` | 34.6% | dithered; background becomes 50% hatch texture |
| `sketch` | 15.4% | clean line art, background correctly resolves to white (threshold 86.2) |
| `text` | 80.6% | background crushed to solid black |

For logos, line art and documents `sketch` beats `photo` — it produces solid shapes
on clean white instead of dither texture, and uses a third of the ink.


## 5.13 The P21 is dual-mode: it also speaks BLE

Discovered while assessing whether a Web Bluetooth app was possible. Web Bluetooth
supports **only** BLE/GATT and cannot open Classic RFCOMM, so the answer hinged on
whether the printer offers anything besides SPP. It does.

A CoreBluetooth scan finds it advertising as a BLE peripheral:

```
PPG_P21_F530   rssi=-49   advertised services: ['FEE7']
```

GATT database (one service):

| UUID | properties | role |
|---|---|---|
| `FF00` | service | |
| `FF01` | notify | **data** — protocol replies arrive here |
| `FF02` | write, writeWithoutResponse | **commands** — max 237 B per write |
| `FF03` | notify | **flow control** — emits `01 01` as the printer consumes data |

**The byte protocol is identical over BLE.** Verified against the same commands used
over SPP:

```
10 FF 20 F0  -> FF03: 01 01 | FF01: 'P21'
10 FF 50 F1  -> FF03: 01 01 | FF01: 00 64          (battery 100)
10 FF 10 00 02 -> FF03: 01 01 | FF01: 'OK'
full print job -> ... FF03: 01 01 x29 ... FF01: aa
```

Note the split: `FF03` carries `01 01` acknowledgements (29 of them during one job —
consistent with credit-based flow control), while `FF01` carries the actual protocol
payload. A naive reader that merges both channels sees a spurious `01 01` prefix on
every reply.

### BLE is the better transport here

| | Classic SPP | BLE GATT |
|---|---|---|
| max write | 126 B (negotiated RFCOMM MTU) | 237 B |
| 18 KB image | ~1.7 s | **~0.2 s** |
| pairing | required, and SSP fails (PIN 0000 only) | **none needed** |
| macOS access | IOBluetooth or a phantom tty | plain Web Bluetooth |
| job-starvation risk (§5.8) | real — text jobs nearly tripped it | negligible |

The Android app uses Classic SPP exclusively for the P21 and never touches this BLE
service. Given the margin against the ~8 s job timeout, **BLE is the transport a new
client should prefer.**


## 5.14 BLE has no backpressure — bulk data must be paced

Symptom: printing a full-width image from the web app produced only the **top half**.

Cause: `writeValueWithoutResponse` (and CoreBluetooth's equivalent) gives the
application no flow control. The browser pushed an 18 KB raster in ~0.2 s. The print
engine consumes far slower, its input buffer filled, and the remainder was **silently
discarded** — no error, no status frame, nothing on `FF01`.

Classic SPP never showed this because RFCOMM applies link-level flow control: the
same 18 KB took ~4 s over RFCOMM precisely because the stack was throttling to the
engine. Moving to BLE removed that brake without removing the need for it.

There is a window to hit, bounded on both sides:

```
too fast  ->  input buffer overflows, tail of the image is dropped   (this bug)
too slow  ->  job starves, printer closes the channel (~8 s, §5.8)
```

### Two brakes

`webapp/app.js` paces bulk raster writes only (short commands are unaffected):

1. **`FF03` credits.** The printer notifies `01 01` as it takes data — 29 such
   notifications were observed against 32 written packets during one job, i.e.
   roughly one per packet. At most `CREDIT_WINDOW = 6` packets are kept in flight.
2. **A rate cap of ~6 KB/s**, matching what RFCOMM achieved, as a fallback in case
   credits are absent or mean something different on another variant.

18 KB then takes ~3.1 s: comfortably slower than the buffer fills, comfortably faster
than the starvation timeout.

### Diagnostic note

A truncated print is **not** reported by the printer. No fault frame, no `ER`, and
the job still ends with a normal `4F 4B AA`. The only signal is the paper. Any
BLE client for this device needs pacing designed in from the start — it cannot be
detected and retried after the fact.

### One central at a time

The P21 stops advertising while a central is connected. If a scan cannot find it but
it is powered on, an existing connection (a browser tab holding it) is the usual
reason — disconnect there before connecting from elsewhere.
