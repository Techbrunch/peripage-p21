# Reference clients

| File | Platform | Status |
|---|---|---|
| `p21_iobt.py` | **macOS** | **Use this.** Drives IOBluetooth RFCOMM directly; self-sufficient. Verified printing on `PPG_P21_F530`. |
| `peripage_p21.py` | Linux | `AF_BLUETOOTH`/`BTPROTO_RFCOMM` socket. Untested (no Linux host available). |
| `p21_macos.py` | macOS | pyserial over `/dev/cu.*`. **Only works while another process holds the RFCOMM link up** — see the warning in its docstring. |

## macOS quickstart

```bash
python3 -m venv .venv
./.venv/bin/pip install pyobjc-framework-IOBluetooth pillow
brew install blueutil

# 1. Grant your terminal app Bluetooth access:
#    System Settings > Privacy & Security > Bluetooth
#    (without it every Bluetooth call dies with SIGABRT / exit 134)

# 2. Find and pair. SSP numeric comparison FAILS on this printer (no screen),
#    so use legacy PIN pairing:
blueutil --inquiry 15
blueutil --pair xx-xx-xx-xx-f5-30 0000

# 3. Print
./.venv/bin/python -c "
import sys; sys.path.insert(0,'ref')
from p21_iobt import P21
d = P21('xx-xx-xx-xx-f5-30', channel_id=1, name='PPG_P21_F530')
d.open()
print(d.info().decode())
d.print_text('hello from a reversed protocol')
d.print_image('photo.png', mode='photo')     # dithered
d.close()
"
```

## Text vs images

`print_text()` uses a hard threshold. `print_image(path, mode=...)` picks the
preprocessing:

| mode | pipeline | use for |
|---|---|---|
| `"photo"` (default) | auto-exposure + Sierra-3 error diffusion, threshold 127 | photographs, anything with midtones |
| `"sketch"` | adaptive threshold at `mean - stddev + 10` | **logos, line art, documents** — usually the best choice |
| `"text"` | resize + threshold 190 | already-black-on-white bitmaps |

**Preview before you burn paper.** `prepare_image()` is standalone and returns the
exact bytes the printer will get, so you can render them to a PNG first:

```python
from p21_iobt import prepare_image
from PIL import Image

data, w, h, info = prepare_image("photo.png", 384, "photo")
print(info)          # {'mode': 'photo', 'mean': 138.7, 'gamma_f': 1.5}

img = Image.new("1", (w, h), 1); px = img.load(); bpr = (w + 7) // 8
for y in range(h):
    for x in range(w):
        if data[y*bpr + (x >> 3)] >> (7 - (x & 7)) & 1:
            px[x, y] = 0
img.save("preview.png")
```

A bad dither is only obvious once it is on paper, so this is worth doing.

## Other commands

```python
d.set_paper_type(P21.PAPER_CONTINUOUS)   # 10 FF 10 03 n   (1..4)
d.set_paper_length(dots)                 # 10 FF 12 hi lo
d.form_feed()                            # 1D 0C
d.mileage()                              # 10 FF A0 01  -> mm, None on a P21
d.print_copies(data, w, h, copies=3)     # client-side loop, no protocol support
d.wifi_configure(ssid, pw)               # 10 FF 31 02  (Wi-Fi models only)
d.reset()                                # 10 FF 03
```

`recharge_mileage()` writes a non-volatile consumable counter with no documented
undo, so it is guarded behind an explicit flag and was never run against hardware.
The `B1..B6` toggles are exposed via `toggle()` but their semantics are unknown —
only `B3` is labelled in the app (设置激活, "set activation").

## Gotchas that cost real time

1. **The `/dev/cu.*` tty silently discards writes** unless the RFCOMM link is already
   up. Check `blueutil --is-connected <addr>` returns `1`.
2. **PyObjC segfaults** in `extract_count` unless the data callback declares an
   explicit ObjC signature — see `Delegate.rfcommChannelData_data_length_`.
3. **Chunk to the negotiated MTU** (126 here), not the APK's flat 1024; `writeSync`
   rejects larger buffers.
4. Pairing needs PIN `0000`, not SSP.
5. **Never pace with a non-zero `CFRunLoopRunInMode` timeout.** It blocks for the
   whole tick when nothing is pending, inflating 1 ms pacing to ~145 ms. The printer
   then **drops the link mid-job** (it allows roughly 8 s between start- and
   end-of-job). Use a `0.0` timeout. This cost an entire debugging cycle: text jobs
   survived it, images did not.
6. The printer **auto-sleeps** after a few minutes. An open returning
   `0xe00002d6` (timeout) usually just means "press the power button" — pairing is
   retained. A `0xe00002bc` on an awake, paired device means a half-open session;
   the client retries the open to clear it.
