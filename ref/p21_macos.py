#!/usr/bin/env python3
"""
PeriPage P21 client for macOS.

!!! DO NOT USE THIS FIRST -- prefer p21_iobt.py. !!!

macOS exposes a paired SPP device as /dev/cu.<name>, but **opening that tty does not
establish the RFCOMM link**. The open succeeds, is_open is True and every write
returns success, yet the device stays "not connected" and the bytes are discarded.
A whole print job disappeared this way during testing.

This module only works while something else already holds the RFCOMM link up
(e.g. p21_iobt.py). Verify with:  blueutil --is-connected <addr>   -> must be 1.

Use ref/p21_iobt.py, which drives IOBluetooth directly and is self-sufficient.

Usage:
    python3 p21_macos.py --list
    python3 p21_macos.py --port /dev/cu.PPG-P21-xxxx --info
    python3 p21_macos.py --port /dev/cu.PPG-P21-xxxx --text "hello"
    python3 p21_macos.py --port /dev/cu.PPG-P21-xxxx --selftest   # no printing
"""
import argparse
import glob
import sys
import time
import zlib

import serial

P21_PLAIN = dict(width=384, feed=96,  compressed=False)   # PPG_P21_*   58 mm
P21_PLUS  = dict(width=576, feed=108, compressed=True)    # PPG_P21+_*  80 mm


def find_ports():
    return [p for p in glob.glob("/dev/cu.*")
            if "Bluetooth-Incoming" not in p and "debug-console" not in p]


def guess_profile(port: str) -> dict:
    # macOS builds the node name from the BT name, replacing '_' with '-'.
    return dict(P21_PLUS if "P21+" in port else P21_PLAIN)


def pack_1bpp(img) -> bytes:
    """y0.h.w(): MSB-first, 1 = black, black when not pure white and mean(rgb) < 190."""
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


class P21:
    def __init__(self, port, profile=None, timeout=2.0):
        self.profile = profile or guess_profile(port)
        self.ser = serial.Serial(port, baudrate=115200, timeout=timeout)
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def write(self, data: bytes):
        """y0.h.G(): 1024-byte chunks, 1 ms apart."""
        for i in range(0, len(data), 1024):
            self.ser.write(data[i:i + 1024])
            self.ser.flush()
            time.sleep(0.001)

    def ask(self, cmd: bytes, wait=1.5) -> bytes:
        self.ser.reset_input_buffer()
        self.write(cmd)
        time.sleep(wait)
        n = self.ser.in_waiting
        return self.ser.read(n) if n else b""

    # -- queries ---------------------------------------------------------
    def info(self):        return self.ask(bytes([0x10, 0xFF, 0x70]))
    def version(self):     return self.ask(bytes([0x10, 0xFF, 0x20, 0xF1]))
    def serial_no(self):   return self.ask(bytes([0x10, 0xFF, 0x20, 0xF2]))
    def model(self):       return self.ask(bytes([0x10, 0xFF, 0x20, 0xF0]))
    def battery(self):
        r = self.ask(bytes([0x10, 0xFF, 0x50, 0xF1]))
        return r[1] if len(r) == 2 else None

    # -- print -----------------------------------------------------------
    def set_density(self, level=1):
        self.write(bytes(12))
        r = self.ask(bytes([0x10, 0xFF, 0x10, 0x00, level * 2]))   # P21 doubles it
        return r

    def print_raster(self, data, width, height):
        p = self.profile
        self.write(bytes([0x10, 0xFF, 0xFE, 0x01]))
        if p["compressed"]:
            self.write(bytes([0x1F, 0xB2, 0x10]))
        self.write(bytes([0x10, 0xFF, 0x80, 0x01]))
        self.write(bytes(12))
        self.write(raster_compressed(data, width, height) if p["compressed"]
                   else raster_uncompressed(data, width, height))
        self.write(bytes([0x1B, 0x4A, p["feed"]]))
        self.write(bytes([0x10, 0xFF, 0xFE, 0x45]))

    def print_text(self, text, size=24, density=1):
        from PIL import Image, ImageDraw, ImageFont
        w = self.profile["width"]
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", size)
        except OSError:
            font = ImageFont.load_default()
        tmp = ImageDraw.Draw(Image.new("RGB", (w, 10)))
        box = tmp.multiline_textbbox((0, 0), text, font=font, spacing=6)
        h = max(box[3] + 8, 24)
        img = Image.new("RGB", (w, h), "white")
        ImageDraw.Draw(img).multiline_text((4, 2), text, fill="black",
                                           font=font, spacing=6)
        self.set_density(density)
        self.print_raster(pack_1bpp(img), img.width, img.height)
        return img.size

    def close(self):
        self.ser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--text")
    ap.add_argument("--size", type=int, default=24)
    ap.add_argument("--density", type=int, default=1)
    a = ap.parse_args()

    if a.list or not a.port:
        ports = find_ports()
        print("candidate serial ports:")
        for p in ports:
            print("   ", p)
        if not ports:
            print("    (none -- pair the printer first)")
        if not a.port:
            return 0

    d = P21(a.port)
    print(f"opened {a.port}  profile={d.profile}")

    if a.selftest or a.info:
        for label, fn in (("info", d.info), ("model", d.model),
                          ("version", d.version), ("serial", d.serial_no)):
            r = fn()
            print(f"  {label:8}: {r.hex(' ') if r else '(no reply)':40} "
                  f"{r.decode('utf-8', 'replace')!r}" if r else f"  {label:8}: (no reply)")
        print(f"  battery : {d.battery()}")

    if a.text:
        size = d.print_text(a.text, size=a.size, density=a.density)
        print(f"  sent raster {size[0]}x{size[1]}")

    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
