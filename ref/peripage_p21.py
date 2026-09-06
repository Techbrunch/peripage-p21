"""
Minimal PeriPage P21 client, reconstructed from PeriPage 6.10.11 (com.ileadtek.peripage).

Transport: Bluetooth Classic RFCOMM / SPP, UUID 00001101-0000-1000-8000-00805F9B34FB.
Text is NOT sent as text -- it is rasterised locally and sent as a GS v 0 bit image.

See docs/02-protocol.md and docs/03-p21-flow.md for provenance.
"""
import socket
import time
import zlib

# NOTE: socket.AF_BLUETOOTH / BTPROTO_RFCOMM is Linux-only. On macOS or Windows,
# swap the transport in PeriPage.__init__ for PyBluez, a serial port bound to the
# device's SPP profile, or any other RFCOMM stack -- everything else is unchanged.

# ---------------------------------------------------------------- device table
#   y6.f device table: f51376q = print width in dots, f51377r = trailing feed
P21_PLAIN = dict(width=384, feed=96,  compressed=False)   # PPG_P21_*   (58 mm)
P21_PLUS  = dict(width=576, feed=108, compressed=True)    # PPG_P21+_*  (80 mm)


def profile_for(name: str) -> dict:
    """y6.f.p3(): a P21 is any device whose BT name starts with 'PPG_P21'."""
    if not name.startswith("PPG_P21"):
        raise ValueError(f"not a P21: {name!r}")
    return dict(P21_PLUS if name.startswith("PPG_P21+") else P21_PLAIN)


# ---------------------------------------------------------------- raster
def pack_1bpp(rows) -> bytes:
    """
    y0.h.w(): MSB-first, 1 = black.
    `rows` is a list of rows, each a list of (r, g, b) tuples, all the same length.
    A pixel is black when it is not pure white and mean(r,g,b) < 190.
    """
    height = len(rows)
    width = len(rows[0]) if height else 0
    bpr = (width + 7) // 8
    out = bytearray(bpr * height)
    for y, row in enumerate(rows):
        base = y * bpr
        for x, px in enumerate(row):
            r, g, b = px[:3]
            if (r, g, b) == (255, 255, 255):
                continue                      # the app skips pure white first
            if (r + g + b) // 3 < 190:
                out[base + (x >> 3)] |= 1 << (7 - (x & 7))
    return bytes(out)


def raster_uncompressed(data: bytes, width: int, height: int) -> bytes:
    """y0.h.F(): GS v 0. Width in BYTES and height are LITTLE-endian."""
    bpr = (width + 7) // 8
    return bytes([0x1D, 0x76, 0x30, 0x00,
                  bpr % 256, bpr // 256,
                  height % 256, height // 256]) + data


def raster_compressed(data: bytes, width: int, height: int) -> bytes:
    """
    y0.h.z(): '1F 00' header, then the zlib stream with its 2-byte header removed
    (i.e. raw DEFLATE + adler32). Width in BYTES / height here are BIG-endian.
    """
    bpr = (width + 7) // 8
    z = zlib.compress(data)          # libCode.so = zlib compress(), default level
    body = z[2:]                     # drop the 78 9C header, keep the adler32 tail
    n = len(body)
    return bytes([0x1F, 0x00,
                  bpr >> 8, bpr & 0xFF,
                  height >> 8, height & 0xFF,
                  (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + body


# ---------------------------------------------------------------- client
class PeriPageP21:
    def __init__(self, mac: str, name: str = "PPG_P21_0000", channel: int = 1):
        self.mac, self.name = mac, name
        self.profile = profile_for(name)
        self.sock = socket.socket(socket.AF_BLUETOOTH,
                                  socket.SOCK_STREAM,
                                  socket.BTPROTO_RFCOMM)
        self.sock.connect((mac, channel))

    # -- y0.h.G(): 1024-byte chunks, 1 ms apart -----------------------------
    def _write(self, data: bytes) -> None:
        for i in range(0, len(data), 1024):
            self.sock.send(data[i:i + 1024])
            time.sleep(0.001)

    def _read(self, timeout: float = 2.0) -> bytes:
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(1024)
        except socket.timeout:
            return b""

    # -- queries (y0.h) -----------------------------------------------------
    def device_info(self) -> str:
        """10 FF 70 -> 'a|b|MAC|VERSION|SN|BATTERY|...'"""
        self._write(bytes([0x10, 0xFF, 0x70]))
        return self._read().decode("utf-8", "replace")

    def firmware_version(self) -> str:
        self._write(bytes([0x10, 0xFF, 0x20, 0xF1]))
        return self._read().decode("utf-8", "replace")

    def serial_number(self) -> str:
        self._write(bytes([0x10, 0xFF, 0x20, 0xF2]))
        return self._read().decode("utf-8", "replace")

    def battery(self):
        self._write(bytes([0x10, 0xFF, 0x50, 0xF1]))
        r = self._read()
        return r[1] if len(r) == 2 else None

    # -- density (y6.a.i1) --------------------------------------------------
    def set_density(self, level: int = 1) -> bool:
        """level is the UI level 0/1/2; the P21 doubles it (y6.f n-runnable)."""
        self._write(bytes(12))                                   # L(): 12 NUL
        self._write(bytes([0x10, 0xFF, 0x10, 0x00, level * 2]))  # Q(n)
        return self._read().startswith(b"OK")

    # -- print (y6.a.u1) ----------------------------------------------------
    def print_raster(self, data: bytes, width: int, height: int) -> None:
        p = self.profile
        self._write(bytes([0x10, 0xFF, 0xFE, 0x01]))       # o() start job
        if p["compressed"]:                                 # P21+ only
            self._write(bytes([0x1F, 0xB2, 0x10]))          # N()
        self._write(bytes([0x10, 0xFF, 0x80, 0x01]))       # a1() -> m()
        self._write(bytes(12))                              # L()
        self._write(raster_compressed(data, width, height) if p["compressed"]
                    else raster_uncompressed(data, width, height))
        self._write(bytes([0x1B, 0x4A, p["feed"]]))        # E(): ESC J feed
        self._write(bytes([0x10, 0xFF, 0xFE, 0x45]))       # R() end job

    def print_text(self, text: str, font_path: str, size: int = 24,
                   density: int = 1) -> None:
        """Rasterise text the way the app does, then send it."""
        from PIL import Image, ImageDraw, ImageFont
        w = self.profile["width"]
        font = ImageFont.truetype(font_path, size)
        lines = text.split("\n")
        lh = size + 6
        img = Image.new("RGB", (w, max(lh * len(lines), 1)), "white")
        ImageDraw.Draw(img).text((0, 0), text, fill="black", font=font, spacing=6)
        px = img.load()
        rows = [[px[x, y] for x in range(img.width)] for y in range(img.height)]
        self.set_density(density)
        self.print_raster(pack_1bpp(rows), img.width, img.height)

    def close(self) -> None:
        self.sock.close()
