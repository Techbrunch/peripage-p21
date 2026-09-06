# 2. Architecture of the printing stack

## 2.1 Layers

```
   com.ileadtek.peripage.mvp.ui.*          Activities (TextPrintActivity, ImagePrintActivity, ...)
                 |
   y6.b  (singleton  y6.b.h5())            app-level device manager  [extends y6.f]
   y6.f  (5701 lines)                      device model registry + print orchestration
                 |
   y6.a  (2771 lines)                      protocol session          [extends y0.d]
                 |
   y0.d  ->  y0.h  (501 lines)             *** PeriPage command builder ***
                 |
   y0.e  ("BluetoothPortPrint")            Bluetooth Classic RFCOMM socket + RX thread
```

There is a **second, unrelated SDK** in package `gb.*` (also SPP). It is selected only when

```java
// y6.f
public boolean F0(String str) { return str.startsWith("PeriPage_A3_"); }
```

So `gb.*` is the **A3** SDK. **P21 does not use it** — P21 goes through `y0.h`.

`com.clj.fastble` (BLE/GATT) is referenced only from discovery UI
(`DeviceActivity`, `DiscoveryFragment`, `CupSearchDeviceDialog`) — it is used for
*scanning* and for a few BLE-only accessories, not for the P21 data path.

> This describes **the app**, not the hardware. The P21 itself is dual-mode and
> exposes a perfectly usable BLE GATT service (`FF00`) that the app never touches —
> see §5.13. Everything below concerns the SPP path the app actually uses.

## 2.2 Transport: Bluetooth Classic SPP (RFCOMM)

`y0.e` is the socket layer (log tag `BluetoothPortPrint`):

```java
BluetoothDevice dev = adapter.getRemoteDevice(mac);
BluetoothSocket s = dev.createRfcommSocketToServiceRecord(
        UUID.fromString("00001101-0000-1000-8000-00805F9B34FB"));   // standard SPP
s.connect();
// fallback if that throws:
s = (BluetoothSocket) dev.getClass()
        .getMethod("createRfcommSocket", int.class).invoke(dev, 1); // channel 1
s.connect();
```

* Adapter must report `STATE_ON` (12); it polls every 200 ms up to the timeout.
* Default connect timeout `y0.h.f51257c = 3000` ms.
* On success it grabs `getOutputStream()` / `getInputStream()` and starts one RX thread.

### Write path — `y0.h.G(byte[])`

Every command and every raster payload goes through one function:

```java
public boolean G(byte[] buf) {
    if (!port.connected) return false;
    int remaining = buf.length;
    while (true) {
        Thread.sleep(1);                       // 1 ms pacing between chunks
        if (remaining <= 1024)
            return port.write(buf, buf.length - remaining, remaining);
        if (!port.write(buf, buf.length - remaining, 1024)) return false;
        remaining -= 1024;
    }
}
```

**=> Writes are split into 1024-byte chunks with a 1 ms sleep between them.**
That is the only flow control on the TX side. Keep the pacing, but see §3.5 — on
stacks that do not fragment for you, chunk to the negotiated RFCOMM MTU (126 bytes
on the test unit) rather than 1024.

### Read path — `y0.e` RX thread (`y0.g`)

The RX thread polls `InputStream.available()` every 100 ms (`y0.e.n(timeout)`),
then hands buffers to `y0.e.l(byte[])`, which **only interprets 2-byte replies**:

| Reply (2 bytes) | Handler msg | Meaning |
|---|---|---|
| `FF 01` | 1 | status / notification |
| `FF 02` | 2 | " |
| `FF 03` | 3 | " |
| `FF 04` | 4 | " |
| `FF 05` | 5 | " |
| `FF 06` | 18 | " |
| `FE xx` | 6 | " |
| `FD 03` | 16 | " |
| `FD 04` | 17 | " |
| `FD xx` | 7 | " |
| `FC xx` | 8 | " |
| anything else | 0 | raw payload passed up |

Framing rules used by the RX thread (`y0.g.run()`):

* If a buffer is longer than 2 bytes, has even length and is not length 3, it is
  **split into 2-byte pairs**, each dispatched 10 ms apart.
* Length-3 buffers are special-cased when the first or last byte is `0xAA`
  (`AA` is dispatched alone, the other two as a pair).
* When flag `f51242p` is set (set by `y0.h.h()`, the `10 FF 31 03` query),
  the reader instead accumulates an **ASCII-bracket frame `[` … `]`**
  (`0x5B` … `0x5D`) and posts it as message 9. This is the
  string-response mode used for device info/Wi-Fi scan results.
