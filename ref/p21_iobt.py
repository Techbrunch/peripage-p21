#!/usr/bin/env python3
"""
PeriPage P21 client for macOS via IOBluetooth RFCOMM (no pairing required).

macOS has no AF_BLUETOOTH; this drives IOBluetoothRFCOMMChannel through PyObjC.
Requires the host terminal app to have Bluetooth permission
(System Settings > Privacy & Security > Bluetooth).
"""
import struct
import sys
import time
import zlib

import objc
from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode
from Foundation import NSObject
import IOBluetooth

SPP_UUID16 = 0x1101

# ---------------------------------------------------------------- status frames
# Meanings from the y0.c implementations in y6.f$a. Value is (name, is_fault).
# ASCII replies never contain bytes >= 0xFC, so a leading 0xFC..0xFF is an
# unambiguous marker for a status frame inside the RX stream.
STATUS_FRAMES = {
    (0xFF, 0x01): ("out_of_paper",   True),
    (0xFF, 0x02): ("cover_open",     True),
    (0xFF, 0x03): ("over_heat",      True),
    (0xFF, 0x04): ("low_battery",    False),
    (0xFF, 0x05): ("cover_closed",   False),
    (0xFF, 0x06): ("low_consumable", False),
    (0xFD, 0x01): ("host_stop",      True),    # printer asks host to abort
    (0xFD, 0x02): ("host_resume",    False),
    (0xFD, 0x03): ("printer_busy",   False),
    (0xFD, 0x04): ("printer_idle",   False),
}


class PrinterFault(IOError):
    """Raised when the printer reports a fault frame during a job."""


def split_status(buf: bytes):
    """
    Pull status frames out of an RX buffer.

    Returns (payload_bytes, [(name, is_fault, raw2), ...]). Unrecognised frames
    beginning FE/FC are reported as paper_error / wifi_status respectively, matching
    y0.e.l()'s catch-all branches.
    """
    out, events, i = bytearray(), [], 0
    while i < len(buf):
        b = buf[i]
        if b >= 0xFC and i + 1 < len(buf):
            pair = (b, buf[i + 1])
            if pair in STATUS_FRAMES:
                name, fault = STATUS_FRAMES[pair]
                events.append((name, fault, bytes(pair)))
                i += 2
                continue
            if b == 0xFE:
                events.append(("paper_error", True, bytes(pair)))
                i += 2
                continue
            if b == 0xFC:
                events.append(("wifi_status", False, bytes(pair)))
                i += 2
                continue
        out.append(b)
        i += 1
    return bytes(out), events


def pump(seconds: float = 0.0, until=None) -> None:
    """
    Run the CF run loop so IOBluetooth delegate callbacks can fire.

    Uses a ZERO timeout so each call drains pending sources and returns
    immediately. Passing a real timeout here (e.g. 0.05) makes CFRunLoopRunInMode
    block for the whole tick whenever nothing is pending, which turns the 1 ms
    inter-chunk pacing into ~50 ms and starves the printer mid-job until it drops
    the RFCOMM link.
    """
    end = time.time() + seconds
    while True:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.0, True)
        if until is not None and until():
            return
        if time.time() >= end:
            return
        time.sleep(0.0005)


class Delegate(NSObject):
    def init(self):
        self = objc.super(Delegate, self).init()
        self.buf = bytearray()
        self.open_status = None
        self.closed = False
        return self

    def rfcommChannelData_data_length_(self, ch, data, length):
        if data and length:
            self.buf.extend(bytes(data[:length]))

    # void, self, _cmd, id, in-void*, size_t.  Without this explicit signature
    # PyObjC cannot size the incoming buffer and segfaults in extract_count.
    rfcommChannelData_data_length_ = objc.selector(
        rfcommChannelData_data_length_, signature=b"v@:@n^vQ")

    def rfcommChannelOpenComplete_status_(self, ch, status):
        self.open_status = status

    def rfcommChannelClosed_(self, ch):
        self.closed = True

    def rfcommChannelWriteComplete_refcon_status_(self, ch, refcon, status):
        pass

    # unused but part of the informal protocol
    def rfcommChannelControlSignalsChanged_(self, ch): pass
    def rfcommChannelFlowControlChanged_(self, ch): pass
    def rfcommChannelQueueSpaceAvailable_(self, ch): pass


P21_PLAIN = dict(width=384, feed=96,  compressed=False)   # PPG_P21_*   58 mm
P21_PLUS  = dict(width=576, feed=108, compressed=True)    # PPG_P21+_*  80 mm


def raster_uncompressed(data, width, height):
    bpr = (width + 7) // 8
    return bytes([0x1D, 0x76, 0x30, 0x00,
                  bpr % 256, bpr // 256,
                  height % 256, height // 256]) + data


def raster_compressed(data, width, height):
    bpr = (width + 7) // 8
    body = zlib.compress(data)[2:]        # drop 78 9C, keep deflate + adler32
    n = len(body)
    return bytes([0x1F, 0x00,
                  bpr >> 8, bpr & 0xFF,
                  height >> 8, height & 0xFF,
                  (n >> 24) & 0xFF, (n >> 16) & 0xFF,
                  (n >> 8) & 0xFF, n & 0xFF]) + body


# ---------------------------------------------------------------- image pipeline
# Reconstructed from if.b.B() -> if.b.y() -> if.b.a() in the APK.
# The TEXT path (if.b.u) is just a resize + the 190 threshold in y0.h.w().
# The IMAGE path adds auto-exposure and Sierra-3 dithering before packing.

def auto_gamma_factor(mean: float) -> float:
    """if.b.y(): mean grey level -> exposure factor f. Applied as out = in ** (1/f)."""
    if mean >= 180.0:
        if mean < 190.0: return 0.9
        if mean < 200.0: return 0.8
        if mean < 210.0: return 0.7
        if mean < 220.0: return 0.6
        if mean < 230.0: return 0.5
        if mean < 240.0: return 0.4
        return 0.3 if mean < 250.0 else 0.2
    if mean < 120.0: return 1.8
    if mean < 130.0: return 1.7
    if mean < 140.0: return 1.5
    if mean < 150.0: return 1.4
    if mean < 160.0: return 1.3
    return 1.2 if mean < 170.0 else 1.0


def auto_expose(img):
    """Grayscale + the app's gamma curve. `img` is a PIL image; returns mode 'L'."""
    g = img.convert("L")
    mean = sum(g.getdata()) / (g.width * g.height)
    f = auto_gamma_factor(mean)
    inv = 1.0 / f
    lut = [min(255, max(0, int(((i / 255.0) ** inv) * 255.0 + 0.5))) for i in range(256)]
    return g.point(lut), mean, f


# Sierra-3 ("Sierra filter"), divisor 32 -- exactly if.b.a():
#            *   5   3
#    2   4   5   4   2
#        2   3   2
_SIERRA3 = (
    (1, 0, 5), (2, 0, 3),
    (-2, 1, 2), (-1, 1, 4), (0, 1, 5), (1, 1, 4), (2, 1, 2),
    (-1, 2, 2), (0, 2, 3), (1, 2, 2),
)


def dither_sierra3(gray) -> bytes:
    """
    if.b.a(): threshold 127, Sierra-3 error diffusion, MSB-first 1bpp packing.

    Java truncates integer division toward zero, so the error terms use int(x/32)
    rather than Python's floor //, which differs for negative error.
    """
    w, h = gray.size
    buf = list(gray.getdata())          # ints 0..255
    bpr = (w + 7) // 8
    out = bytearray(bpr * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            v = buf[i]
            if v > 127:
                err = v - 255           # -> white, nothing to set
            else:
                err = v                 # -> black
                out[y * bpr + (x >> 3)] |= 1 << (7 - (x & 7))
            if not err:
                continue
            for dx, dy, wgt in _SIERRA3:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and ny < h:
                    buf[ny * w + nx] += int(err * wgt / 32)
    return bytes(out)


def prepare_image(path_or_img, width: int = 384, mode: str = "photo"):
    """
    Scale to the printer width and convert to 1bpp.

      mode="photo"   auto-exposure + Sierra-3 dither  (if.b.B, the image path)
      mode="text"    plain resize + threshold 190     (if.b.u, the text path)
      mode="sketch"  adaptive threshold               (if.b.T, the line-art path)

    Returns (packed_bytes, width, height, info_dict).
    """
    from PIL import Image
    img = Image.open(path_or_img) if isinstance(path_or_img, (str, bytes)) else path_or_img
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    h = max(1, round(img.height * width / img.width))
    img = img.resize((width, h), Image.LANCZOS)

    if mode == "text":
        g = img.convert("L")
        bpr = (width + 7) // 8
        out = bytearray(bpr * h)
        px = g.load()
        for y in range(h):
            for x in range(width):
                if px[x, y] < 190:
                    out[y * bpr + (x >> 3)] |= 1 << (7 - (x & 7))
        return bytes(out), width, h, {"mode": "text"}

    if mode == "sketch":
        # if.b.T(): threshold at mean - stddev + 10 (when mean > stddev), via
        # Core.meanStdDev + Imgproc.threshold(THRESH_BINARY). Good for line art.
        g = img.convert("L")
        px_all = list(g.getdata())
        n = len(px_all)
        mean = sum(px_all) / n
        var = sum((v - mean) ** 2 for v in px_all) / n
        sd = var ** 0.5
        thr = (mean - sd + 10.0) if mean > sd else mean
        bpr = (width + 7) // 8
        out = bytearray(bpr * h)
        for y in range(h):
            base = y * bpr
            row = y * width
            for x in range(width):
                if px_all[row + x] <= thr:        # THRESH_BINARY: <= thresh -> black
                    out[base + (x >> 3)] |= 1 << (7 - (x & 7))
        return bytes(out), width, h, {"mode": "sketch", "mean": round(mean, 1),
                                      "stddev": round(sd, 1), "threshold": round(thr, 1)}

    gray, mean, f = auto_expose(img)
    data = dither_sierra3(gray)
    return data, width, h, {"mode": "photo", "mean": round(mean, 1), "gamma_f": f}


def pack_1bpp(img) -> bytes:
    """y0.h.w(): MSB-first, 1 = black; black when not pure white and mean(rgb) < 190."""
    w, h = img.size
    px = img.load()
    bpr = (w + 7) // 8
    out = bytearray(bpr * h)
    for y in range(h):
        base = y * bpr
        for x in range(w):
            r, g, b = px[x, y][:3]
            if (r, g, b) == (255, 255, 255):
                continue
            if (r + g + b) // 3 < 190:
                out[base + (x >> 3)] |= 1 << (7 - (x & 7))
    return bytes(out)


class P21:
    def __init__(self, address: str, channel_id: int | None = None, name: str = ""):
        self.address = address
        self.dev = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(address)
        if self.dev is None:
            raise RuntimeError(f"no device for {address}")
        self.name = name or (self.dev.name() or "")
        self.profile = dict(P21_PLUS if "P21+" in self.name else P21_PLAIN)
        self.delegate = Delegate.alloc().init()
        self.channel = None
        self.channel_id = channel_id
        self.events = []          # [(name, is_fault, raw2), ...]

    # -- connection ------------------------------------------------------
    def discover_channel(self) -> int | None:
        """SDP query for the Serial Port service's RFCOMM channel."""
        err = self.dev.performSDPQuery_(None)
        pump(4.0)
        uuid = IOBluetooth.IOBluetoothSDPUUID.uuid16_(SPP_UUID16)
        rec = self.dev.getServiceRecordForUUID_(uuid)
        if rec is None:
            return None
        res = rec.getRFCOMMChannelID_(None)
        # returns (IOReturn, channelID)
        if isinstance(res, tuple):
            status, cid = res[0], res[1]
            return int(cid) if status == 0 else None
        return None

    def open(self, timeout: float = 12.0) -> None:
        cid = self.channel_id or self.discover_channel() or 1
        self.channel_id = cid
        # A half-open session on the printer's side (e.g. after an aborted
        # transfer) makes the first open fail with kIOReturnError even though the
        # device is paired and awake. Retrying clears it.
        status, ch = None, None
        for attempt in range(6):
            res = self.dev.openRFCOMMChannelSync_withChannelID_delegate_(
                None, cid, self.delegate)
            status, ch = (res[0], res[1]) if isinstance(res, tuple) else (res, None)
            if status == 0 and ch is not None and ch.isOpen():
                break
            if ch is not None:
                try:
                    ch.closeChannel()
                except Exception:
                    pass
            self.dev.closeConnection()
            pump(1.5)
        if status != 0 or ch is None or not ch.isOpen():
            # 0xe00002bc (kIOReturnError) here almost always means "not paired":
            #   blueutil --pair <addr> 0000
            raise RuntimeError(
                f"channel open failed: 0x{status & 0xffffffff:x} "
                f"(paired? try: blueutil --pair {self.address} 0000)")
        self.channel = ch
        pump(0.3)

    # -- io --------------------------------------------------------------
    def write(self, data: bytes) -> None:
        """y0.h.G(): 1024-byte chunks, 1 ms apart (capped to the channel MTU)."""
        mtu = self.channel.getMTU() or 1024
        chunk = min(1024, mtu)   # writeSync rejects > MTU
        for i in range(0, len(data), chunk):
            part = data[i:i + chunk]
            # On payloads of a few KB the transmit queue starves and writeSync
            # returns kIOReturnUnderrun (0xe00002e7). Pump the run loop to let the
            # stack drain, then retry -- this is normal back-pressure, not an error.
            deadline = time.time() + 20.0
            delay = 0.005
            while True:
                st = self.channel.writeSync_length_(part, len(part))
                if st == 0:
                    break
                if not self.channel.isOpen():
                    raise IOError(
                        f"link dropped mid-job at byte {i}/{len(data)} "
                        f"(0x{st & 0xffffffff:x}); see docs 5.8 -- the printer "
                        f"closes the channel if a job starves")
                if time.time() > deadline:
                    break
                pump(delay)                      # let the print engine drain
                delay = min(delay * 1.5, 0.25)   # back off, printer is slower than us
            if st != 0:
                raise IOError(f"writeSync failed: 0x{st & 0xffffffff:x} "
                              f"at byte {i}/{len(data)} after 20 s of back-pressure")
            pump(0.001)   # y0.h.G() pacing

    def ask(self, cmd: bytes, wait: float = 1.5) -> bytes:
        self.delegate.buf.clear()
        self.write(cmd)
        pump(wait)
        payload, events = split_status(bytes(self.delegate.buf))
        self.events.extend(events)
        return payload

    def drain_status(self):
        """Pop any status frames received since the last call."""
        payload, events = split_status(bytes(self.delegate.buf))
        self.delegate.buf.clear()
        self.delegate.buf.extend(payload)
        self.events.extend(events)
        ev, self.events = self.events, []
        return ev

    def faults(self):
        """Status frames seen so far that indicate a fault condition."""
        return [e for e in self.events if e[1]]

    # -- queries ---------------------------------------------------------
    def info(self):      return self.ask(bytes([0x10, 0xFF, 0x70]))
    def model(self):     return self.ask(bytes([0x10, 0xFF, 0x20, 0xF0]))
    def version(self):   return self.ask(bytes([0x10, 0xFF, 0x20, 0xF1]))
    def serial_no(self): return self.ask(bytes([0x10, 0xFF, 0x20, 0xF2]))
    def status(self):    return self.ask(bytes([0x10, 0xFF, 0x40]))

    def battery(self):
        r = self.ask(bytes([0x10, 0xFF, 0x50, 0xF1]))
        return r[1] if len(r) >= 2 else None

    def userkey(self, rand: int = 0x42):
        return self.ask(bytes([0x10, 0xFF, 0x20, 0xEE, rand]))

    # -- printing --------------------------------------------------------
    def set_density(self, level: int = 1) -> bytes:
        """
        level is the app's UI level 0/1/2; the P21 doubles it (y6.f, p3()==true),
        giving wire values 0/2/4.

        Measured on V4.04_SD: the usable wire range is 0..4 -- 0 prints faint (not
        blank) and output saturates at 4. Values above 4 still ack `OK` but look
        identical to 4, so the ack is receipt, not validation.

        This is the only synchronous command in the print path; the app arms a 2 s
        timeout and aborts the job if `OK` does not arrive.
        """
        self.write(bytes(12))
        return self.ask(bytes([0x10, 0xFF, 0x10, 0x00, min(level, 2) * 2]))

    def print_raster(self, data: bytes, width: int, height: int,
                     backoff: bool = False, check: bool = True) -> None:
        """
        Everything here is fire-and-forget; only a single `4F 4B AA` arrives after
        end-of-job.

        `backoff` sends the app's `10 FF 80 01`. Measured to be a **no-op** on the
        P21 (identical inter-job gaps with it on and off) -- the app only sends it
        because y6.a.a1() gates on the broad A2-family test j1(). Off by default;
        set True to mirror the app byte-for-byte.
        """
        p = self.profile
        self.drain_status()
        faults = [e for e in self.events if e[1]]
        if faults and check:
            raise PrinterFault("printer reports " +
                               ", ".join(f"{n} ({r.hex(' ')})" for n, _, r in faults))
        # Once start-of-job is sent the printer is mid-job. If the raster write
        # throws (back-pressure, dropped link) and end-of-job never arrives, the
        # firmware WEDGES: it keeps accepting RFCOMM connections but stops
        # processing commands entirely, and only a power cycle clears it.
        # So end-of-job must go out on every path.
        self.write(bytes([0x10, 0xFF, 0xFE, 0x01]))          # start job
        try:
            if p["compressed"]:
                self.write(bytes([0x1F, 0xB2, 0x10]))
            if backoff:
                self.write(bytes([0x10, 0xFF, 0x80, 0x01]))
            self.write(bytes(12))
            self.write(raster_compressed(data, width, height) if p["compressed"]
                       else raster_uncompressed(data, width, height))
            self.write(bytes([0x1B, 0x4A, p["feed"]]))       # ESC J feed (96 = 12 mm)
        finally:
            try:
                self.write(bytes([0x10, 0xFF, 0xFE, 0x45]))  # end job, always
            except Exception:
                pass                                          # link already gone

    def print_text(self, text: str, size: int = 28, density: int = 1):
        from PIL import Image, ImageDraw, ImageFont
        w = self.profile["width"]
        font = None
        for path in ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                     "/System/Library/Fonts/Helvetica.ttc",
                     "/System/Library/Fonts/SFNS.ttf"):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        probe = ImageDraw.Draw(Image.new("RGB", (w, 10)))
        box = probe.multiline_textbbox((4, 2), text, font=font, spacing=6)
        img = Image.new("RGB", (w, max(box[3] + 10, 32)), "white")
        ImageDraw.Draw(img).multiline_text((4, 2), text, fill="black",
                                           font=font, spacing=6)
        self.set_density(density)
        self.print_raster(pack_1bpp(img), img.width, img.height)
        return img.size

    def retract(self, n: int = 5) -> None:
        """
        `10 FF 81 <n>` -- retract paper by n. y6.f.G4(3) sends `10 FF 81 05` behind
        the app's retract button.

        MEASURED: a NO-OP on the P21, exactly like `10 FF 80 01/00`. The whole paper
        back-off family is inert on this model (it has no reverse feed); the commands
        reach it only via the broad A2-family branch j1(). Accepted silently, so
        there is no reply that would tell you it did nothing. Kept for other models.
        """
        self.write(bytes([0x10, 0xFF, 0x81, n & 0xFF]))

    def feed(self, n: int) -> None:
        """`1B 4A <n>` -- ESC J, feed n dot-lines (8 dots/mm)."""
        self.write(bytes([0x1B, 0x4A, n & 0xFF]))

    def print_image(self, path, mode: str = "photo", density: int = 1):
        """Print an image file. mode is "photo" (dithered) or "text" (thresholded)."""
        data, w, h, info = prepare_image(path, self.profile["width"], mode)
        self.set_density(density)
        self.print_raster(data, w, h)
        return w, h, info


    # ---------------------------------------------------------------- paper
    # `10 FF 10 03 <n>` (y0.h.E / y6.a.U0). Values from y6.f's paper-type
    # dispatcher; the P21 is a roll printer so CONTINUOUS is the only one that
    # really applies, but the command is accepted on any model.
    PAPER_CONTINUOUS = 1   # 连续卷筒纸  roll
    PAPER_GAP_LABEL  = 2   # 不干胶缝隙纸 die-cut label with gaps
    PAPER_PAGED      = 3   # 分页纸      perforated / paged
    PAPER_TATTOO     = 4   # 纹身纸      tattoo transfer paper

    def set_paper_type(self, kind: int = PAPER_CONTINUOUS) -> None:
        """`10 FF 10 03 <n>`. Fire-and-forget."""
        self.write(bytes([0x10, 0xFF, 0x10, 0x03, kind & 0xFF]))

    def set_paper_length(self, dots: int) -> None:
        """`10 FF 12 <hi> <lo>` -- paper/label length in dot-lines, BIG-endian."""
        self.write(bytes([0x10, 0xFF, 0x12, (dots >> 8) & 0xFF, dots & 0xFF]))

    def form_feed(self) -> None:
        """`1D 0C` -- GS FF, advance to the next label mark. Only meaningful on
        gap/black-mark paper; on continuous roll it behaves like a short feed."""
        self.write(bytes([0x1D, 0x0C]))

    # ---------------------------------------------------------------- mileage
    def mileage(self):
        """
        `10 FF A0 01` -> consumable odometer in **mm** (u32 big-endian).

        y6.a case 10: if the reply starts with "OK" the value is at bytes 4..7,
        otherwise at 0..3.
        """
        r = self.ask(bytes([0x10, 0xFF, 0xA0, 0x01]), wait=2.0)
        if len(r) >= 8 and r[0] == 0x4F and r[1] == 0x4B:
            v = r[4:8]
        elif len(r) >= 4:
            v = r[0:4]
        else:
            return None
        return (v[0] << 24) | (v[1] << 16) | (v[2] << 8) | v[3]

    def recharge_mileage(self, mm: int, i_understand_this_writes_nvram: bool = False):
        """
        `10 FF 0A 00 <u32 big-endian>` -- writes the consumable odometer.
        Replies "OK" on success (y6.a case 11).

        NOT a read-only call: it mutates a counter the vendor uses for consumable
        tracking, and there is no documented way to undo it. Guarded deliberately;
        never exercised against hardware here.
        """
        if not i_understand_this_writes_nvram:
            raise RuntimeError(
                "recharge_mileage() writes the printer's consumable counter and is "
                "not reversible. Pass i_understand_this_writes_nvram=True to proceed.")
        b = [(mm >> 24) & 0xFF, (mm >> 16) & 0xFF, (mm >> 8) & 0xFF, mm & 0xFF]
        return self.ask(bytes([0x10, 0xFF, 0x0A, 0x00] + b), wait=2.0)

    # ---------------------------------------------------------------- Wi-Fi
    # Present on Wi-Fi capable models (A40/P40 etc). A P21 is Bluetooth-only, so
    # these are ported for completeness and untested.
    def wifi_start(self) -> None:
        """`10 FF 31 01`"""
        self.write(bytes([0x10, 0xFF, 0x31, 0x01]))

    def wifi_scan(self, wait: float = 6.0) -> bytes:
        """
        `10 FF 31 03` -- ask for the AP list. The reply is a bracketed ASCII frame
        `[ ... ]` rather than 2-byte status frames (y0.e sets f51242p and the RX
        thread accumulates until `]`).
        """
        return self.ask(bytes([0x10, 0xFF, 0x31, 0x03]), wait=wait)

    def wifi_configure(self, ssid: str, password: str, mode: int = 0) -> bytes:
        """
        `10 FF 31 02` + TLV, from y0.h.p():

            10 FF 31 02  <len16 lo> 00
            01 <ssidLen> 00  <ssid utf-8>
            02 <pwdLen>  00  <password utf-8>
            03 01 00 <mode>

        where len16 = ssidLen + 6 + pwdLen + 4. Replies "OK" (y6.a case 1).
        This is the ONLY command in the protocol that carries free-form text --
        it is Wi-Fi provisioning, not text printing.
        """
        a = ssid.encode("utf-8")
        b = password.encode("utf-8")
        total = len(a) + 6 + len(b) + 4
        buf = bytearray([0x10, 0xFF, 0x31, 0x02, total & 0xFF, (total >> 8) & 0xFF])
        buf += bytes([1, len(a), 0]) + a
        buf += bytes([2, len(b), 0]) + b
        buf += bytes([3, 1, 0, mode & 0xFF])
        return self.ask(bytes(buf), wait=3.0)

    # ---------------------------------------------------------------- misc
    def reset(self) -> None:
        """`10 FF 03` (y0.h.R)."""
        self.write(bytes([0x10, 0xFF, 0x03]))

    def toggle(self, sub: int, value: int) -> None:
        """
        `10 FF 10 <sub> <value>` for sub in B1..B6 (y6.f.G4 items 12-18).
        Only B3 is labelled in the app (设置激活, "set activation"); the rest are
        unlabelled and model-specific. Semantics unknown -- exposed for
        experimentation, not used by any code path here.
        """
        if sub not in (0xB1, 0xB2, 0xB3, 0xB4, 0xB6):
            raise ValueError("sub must be one of 0xB1,0xB2,0xB3,0xB4,0xB6")
        self.write(bytes([0x10, 0xFF, 0x10, sub, value & 0xFF]))

    # ---- 0x59 "Y" family: voice models and label stock. Not used by the P21. ----
    def y_query_voice_mode(self) -> bytes:
        return self.ask(bytes([0x59, 0x2F, 0x17]), wait=2.0)

    def y_set_voice_mode(self, n: int) -> None:
        self.write(bytes([0x59, 0x2F, 0x20, 1 if n == 1 else 2]))

    def y_set_paper(self, kind: str) -> None:
        """continuous | gap | blackmark  ->  59 1F 80 01 {10,20,30}"""
        m = {"continuous": 0x10, "gap": 0x20, "blackmark": 0x30}
        self.write(bytes([0x59, 0x1F, 0x80, 0x01, m[kind]]))

    def y_set_paper_size(self, n: int) -> None:
        self.write(bytes([0x59, 0x2F, 0xF2, n & 0xFF]))

    # ---------------------------------------------------------------- copies
    def print_copies(self, data: bytes, width: int, height: int, copies: int = 1,
                     settle: float = 2.5) -> int:
        """
        Print the same raster `copies` times.

        The app does this by re-running the whole job per copy (y6.a.t1 registers a
        completion callback that calls u1 again) -- there is no "copies" field in
        the protocol. We wait for the end-of-job reply between copies so the print
        engine is not still busy when the next start-of-job arrives, which is what
        provoked back-pressure during testing.
        """
        done = 0
        for _ in range(max(1, copies)):
            self.delegate.buf.clear()
            self.print_raster(data, width, height)
            pump(settle, until=lambda: b"\xaa" in bytes(self.delegate.buf))
            self.drain_status()
            done += 1
        return done

    def close(self):
        if self.channel is not None:
            self.channel.closeChannel()
            pump(0.5)
        self.dev.closeConnection()


def find_p21(timeout: int = 12):
    """Inquiry scan; returns (address, name) of the first PPG_P21* found."""
    inq = IOBluetooth.IOBluetoothDeviceInquiry.inquiryWithDelegate_(None)
    inq.setInquiryLength_(timeout)
    inq.setUpdateNewDeviceNames_(True)
    inq.start()
    pump(timeout + 2)
    inq.stop()
    for d in (inq.foundDevices() or []):
        n = d.name() or ""
        if n.startswith("PPG_P21"):
            return d.addressString(), n
    return None, None
