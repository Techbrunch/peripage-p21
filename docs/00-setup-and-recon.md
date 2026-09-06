# PeriPage P21 — Reverse Engineering Notes

## 0. Target & Tooling

| Item | Value |
|---|---|
| Sample | `PeriPage_6.10.11_APKPure.xapk` (178,700,258 bytes) |
| SHA-256 | `a842d10c92dfd05bfe4100ff55538d4d5647c7741573a9174e8d70c9f3753495` |
| Source | <https://apkpure.com/peripage/com.ileadtek.peripage/download/6.10.11> |
| Package | `com.ileadtek.peripage` |
| Version | 6.10.11 (versionCode 323) |
| minSdk / targetSdk | 26 / 36 |
| Goal | Protocol used to **connect** to and **send text to print** on a PeriPage **P21** |

The `.xapk` is not in this repository -- it is PeriPage's, not ours to
redistribute. Download it from the link above and check it before unpacking:

```bash
shasum -a 256 PeriPage_6.10.11_APKPure.xapk
# a842d10c92dfd05bfe4100ff55538d4d5647c7741573a9174e8d70c9f3753495
```

### Tools

```bash
brew install jadx          # 1.5.6
unzip -o PeriPage_*.xapk -d xapk
unzip -o xapk/com.ileadtek.peripage.apk 'classes*.dex' -d dex
jadx -j 8 --no-res --no-debug-info --escape-unicode -d out \
     dex/classes.dex dex/classes2.dex dex/classes3.dex dex/classes4.dex
```

### XAPK layout

```
com.ileadtek.peripage.apk   83 MB   base split (classes.dex .. classes9.dex)
config.arm64_v8a.apk        95 MB   native libs
manifest.json / icon.png
```

## 1. First-pass recon

### It is a hybrid app

`assets/flutter_assets/` + `lib/arm64-v8a/libflutter.so` + `libapp.so` (19.5 MB) show a
Flutter layer bolted onto a much older native-Android app. **The printer protocol is
*not* in Dart** — it lives in the Java/Kotlin DEX, which is good news for us.

Relevant native libs are all unrelated to printing (EasyAR, OpenCV, pdfium, zxing,
mupdf, ffmpeg…), i.e. camera/scan/document features.

### Where the printing code lives

Grepping the DEX string tables for class-path prefixes:

| Package | Role |
|---|---|
| `com.clj.fastble` | third-party **FastBle** library — BLE (GATT) transport |
| `com.ileadtek.peripage.*` | app UI / MVP layer (mostly `databinding`, activities, presenters) |
| `com.peripage.a3` | A3-series specific SDK |
| `y0.*` | **transport layer** (obfuscated) — Bluetooth Classic SPP + BLE sockets |
| `y6.*` | **device / print manager** (obfuscated) — `y6.f` (5701 lines) is the core |
| `v4.*` | device discovery / model registry helpers |

Obfuscation is name-only (ProGuard-style); control flow and all string/byte literals
are intact.

### The P21 anchor points

`grep -rl 'P21' out/sources` → only three files: `y6/f.java`, `y6/a.java`, `v4/d.java`.

Advertised Bluetooth names the app matches for this family:

```
PPG_P21_       PPG_P21_HD     PPG_P21_UD     PPG_P21_UHD     PPG_P21+_
```

`y6.f` groups P21 with the **A2 family** (`PeriPage_A2`, `PPG_A2`, `PPG_A2Neo`,
`PPG_P22`, `PPG_P20`), which is the strongest hint that P21 speaks the A2 dialect of
the protocol.

### Transport candidates

UUID literals recovered from the whole decompiled tree:

```
00001101-0000-1000-8000-00805F9B34FB   <- Bluetooth Classic SPP (RFCOMM)  [y0/e.java, gb/e.java]
20799a27-fa80-4b36-b2db-0f8141f24180
629a824d-c717-4ba5-bc0f-3f3968554d01
cf61947c-a8fe-4fa3-aa7c-fbeb7f291352
...
```

So there are **two transports**; next step is to determine which one P21 uses and
what framing sits on top.

## Reproducing the analysis

`tools/eval_device_predicates.py` mechanically evaluates the app's own device-family
predicate methods (`y6.f.p3(String)`, `m1()`, `j1()`, …) against a given Bluetooth
name, so the model-dependent branches in the print path can be resolved without
reading dozens of boolean chains by hand:

```bash
Y6F=<jadx-out>/sources/y6/f.java \
  python3 tools/eval_device_predicates.py 'PPG_P21_1234' m1 j1 p3 z0 l1 L0 j4
```
