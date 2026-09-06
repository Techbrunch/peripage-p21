// PeriPage P21 — Web Bluetooth driver + image pipeline.
// Protocol reverse-engineered from PeriPage 6.10.11; see ../docs/.
'use strict';

const SVC   = 0x0000ff00;
const CH_TX = 0x0000ff02;   // write / writeNoResponse
const CH_RX = 0x0000ff01;   // data notify
const CH_FC = 0x0000ff03;   // flow-control notify, emits 01 01
const CHUNK = 180;          // device reports maxWriteNoResp 237; stay under it

// Flow control. BLE writeWithoutResponse has NO backpressure: the browser will
// happily push a full image in ~0.2 s, which is ~20x faster than the print engine
// consumes it, and the printer silently drops the overflow (symptom: only the top
// of the image prints). Classic RFCOMM never showed this because link-level flow
// control paced delivery to the engine (~4 s for the same 18 KB).
//
// Two brakes, belt and braces:
//   1. FF03 credits -- the printer notifies 01 01 as it takes data. Keep at most
//      CREDIT_WINDOW packets in flight.
//   2. A rate cap, in case credits are absent or mean something else on a variant.
//      ~6 KB/s matches what RFCOMM achieved and prints reliably.
const CREDIT_WINDOW = 6;
const TARGET_BPS    = 6000;
const CREDIT_TIMEOUT = 400;   // ms; fall through to the rate cap if none arrive

// Status frames (from the y0.c implementations in y6.f$a)
const STATUS = {
  'ff01': ['out of paper',   true ], 'ff02': ['cover open',     true ],
  'ff03': ['over-heat',      true ], 'ff04': ['low battery',    false],
  'ff05': ['cover closed',   false], 'ff06': ['low consumable', false],
  'fd01': ['printer: stop',  true ], 'fd02': ['printer: resume',false],
  'fd03': ['printer busy',   false], 'fd04': ['printer idle',   false],
};

export class P21 {
  constructor() {
    this.dev = null; this.tx = null;
    this.rx = [];            // accumulated data-notify bytes
    this.events = [];        // decoded status frames
    this.onstatus = () => {};
    this.onlog = () => {};
    this.width = 384;        // PPG_P21_ = 384 dots; PPG_P21+_ = 576
    this.feed = 96;
  }

  get connected() { return !!(this.dev && this.dev.gatt.connected); }

  async connect() {
    this.dev = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SVC] }, { namePrefix: 'PPG_' }, { namePrefix: 'PeriPage' }],
      optionalServices: [SVC],
    });
    this.dev.addEventListener('gattserverdisconnected', () => this.onstatus('disconnected', true));
    const server = await this.dev.gatt.connect();
    const svc    = await server.getPrimaryService(SVC);
    this.tx      = await svc.getCharacteristic(CH_TX);

    const data = await svc.getCharacteristic(CH_RX);
    await data.startNotifications();
    data.addEventListener('characteristicvaluechanged', e =>
      this._ondata(new Uint8Array(e.target.value.buffer)));

    try {                                   // flow-control channel is advisory
      const fc = await svc.getCharacteristic(CH_FC);
      await fc.startNotifications();
      fc.addEventListener('characteristicvaluechanged', () => { this._credits++; });
      this._hasFlowControl = true;
    } catch { /* absent on some models */ }

    // PPG_P21+_ is the 576-dot variant
    const n = this.dev.name || '';
    this.width = n.startsWith('PPG_P21+') ? 576 : 384;
    this.feed  = n.startsWith('PPG_P21+') ? 108 : 96;
    return n;
  }

  disconnect() { if (this.connected) this.dev.gatt.disconnect(); }

  _credits = 0;
  _sent = 0;
  _lastWrite = 0;

  _ondata(bytes) {
    // Pull 2-byte status frames out; ASCII payloads never contain >= 0xFC.
    const out = [];
    for (let i = 0; i < bytes.length; i++) {
      const b = bytes[i];
      if (b >= 0xfc && i + 1 < bytes.length) {
        const key = b.toString(16).padStart(2, '0') + bytes[i + 1].toString(16).padStart(2, '0');
        if (STATUS[key]) {
          const [name, fault] = STATUS[key];
          this.events.push({ name, fault });
          this.onstatus(name, fault);
          i++; continue;
        }
        if (b === 0xfe) { this.events.push({ name: 'paper error', fault: true }); this.onstatus('paper error', true); i++; continue; }
      }
      out.push(b);
    }
    this.rx.push(...out);
  }

  async _raw(part) {
    if (this.tx.writeValueWithoutResponse) await this.tx.writeValueWithoutResponse(part);
    else await this.tx.writeValue(part);
  }

  /**
   * @param {Uint8Array} buf
   * @param {boolean} paced  true for bulk raster data, false for short commands
   */
  async write(buf, paced = false) {
    const minGap = (CHUNK / TARGET_BPS) * 1000;   // ms per chunk at the rate cap
    for (let i = 0; i < buf.length; i += CHUNK) {
      const part = buf.subarray(i, Math.min(i + CHUNK, buf.length));
      if (paced) {
        const t0 = performance.now();
        // 1. credit window
        while (this._sent - this._credits >= CREDIT_WINDOW) {
          if (performance.now() - t0 > CREDIT_TIMEOUT) break;
          await new Promise(r => setTimeout(r, 4));
        }
        // 2. rate cap
        const since = performance.now() - this._lastWrite;
        if (since < minGap) await new Promise(r => setTimeout(r, minGap - since));
      }
      await this._raw(part);
      this._sent++;
      this._lastWrite = performance.now();
    }
  }

  async ask(cmd, wait = 1200) {
    this.rx.length = 0;
    await this.write(Uint8Array.from(cmd));
    await new Promise(r => setTimeout(r, wait));
    return Uint8Array.from(this.rx);
  }

  async info()    { return new TextDecoder().decode(await this.ask([0x10,0xff,0x70])); }
  async model()   { return new TextDecoder().decode(await this.ask([0x10,0xff,0x20,0xf0])); }
  async version() { return new TextDecoder().decode(await this.ask([0x10,0xff,0x20,0xf1])); }
  async battery() { const r = await this.ask([0x10,0xff,0x50,0xf1]); return r.length >= 2 ? r[1] : null; }

  async setDensity(level) {          // UI level 0..2; the P21 doubles it -> 0/2/4
    await this.write(new Uint8Array(12));
    const r = await this.ask([0x10, 0xff, 0x10, 0x00, Math.min(level, 2) * 2], 1200);
    return new TextDecoder().decode(r).includes('OK');
  }

  async feedDots(n) { await this.write(Uint8Array.from([0x1b, 0x4a, n & 0xff])); }

  /**
   * One print job. `data` is packed 1bpp, MSB-first.
   * End-of-job is sent on every path: an unterminated job WEDGES the printer
   * (it keeps accepting connections but stops executing, power-cycle only).
   */
  async printRaster(data, w, h) {
    const bpr = (w + 7) >> 3;
    const hdr = Uint8Array.from([0x1d,0x76,0x30,0x00, bpr & 255, bpr >> 8, h & 255, h >> 8]);
    const body = new Uint8Array(hdr.length + data.length);
    body.set(hdr); body.set(data, hdr.length);

    this._sent = 0; this._credits = 0; this._lastWrite = 0;
    await this.write(Uint8Array.from([0x10,0xff,0xfe,0x01]));   // start job
    try {
      await this.write(new Uint8Array(12));
      await this.write(body, true);                             // paced: bulk raster
      await this.write(Uint8Array.from([0x1b,0x4a,this.feed]));
    } finally {
      try { await this.write(Uint8Array.from([0x10,0xff,0xfe,0x45])); } catch {}
    }
  }
}

/* ------------------------------------------------------------------ imaging */

// if.b.y(): mean grey -> exposure factor, applied as out = in ** (1/f)
export function gammaFactor(mean) {
  if (mean >= 180) {
    if (mean < 190) return 0.9;  if (mean < 200) return 0.8;
    if (mean < 210) return 0.7;  if (mean < 220) return 0.6;
    if (mean < 230) return 0.5;  if (mean < 240) return 0.4;
    return mean < 250 ? 0.3 : 0.2;
  }
  if (mean < 120) return 1.8;  if (mean < 130) return 1.7;
  if (mean < 140) return 1.5;  if (mean < 150) return 1.4;
  if (mean < 160) return 1.3;  return mean < 170 ? 1.2 : 1.0;
}

// if.b.a(): Sierra-3, divisor 32.        *  5  3
//                              2  4  5  4  2
//                                 2  3  2
const SIERRA = [[1,0,5],[2,0,3],[-2,1,2],[-1,1,4],[0,1,5],[1,1,4],[2,1,2],[-1,2,2],[0,2,3],[1,2,2]];

export function toGray(imgData) {
  const { width: w, height: h, data: d } = imgData;
  const g = new Float32Array(w * h);
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    const a = d[i+3] / 255;
    // composite onto white, matching the app's white canvas
    const r = d[i]*a + 255*(1-a), gg = d[i+1]*a + 255*(1-a), b = d[i+2]*a + 255*(1-a);
    g[p] = Math.round(0.299*r + 0.587*gg + 0.114*b);   // 8-bit, as cvtColor gives
  }
  return g;
}

export function pack(bits, w, h) {
  const bpr = (w + 7) >> 3, out = new Uint8Array(bpr * h);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++)
      if (bits[y*w + x]) out[y*bpr + (x >> 3)] |= 1 << (7 - (x & 7));
  return out;
}

export function convert(imgData, mode) {
  const w = imgData.width, h = imgData.height;
  const g = toGray(imgData);
  const bits = new Uint8Array(w * h);
  let info = {};

  if (mode === 'text') {
    for (let i = 0; i < g.length; i++) bits[i] = g[i] < 190 ? 1 : 0;
    info = { mode: 'text', threshold: 190 };
  } else if (mode === 'sketch') {
    let sum = 0; for (const v of g) sum += v;
    const mean = sum / g.length;
    let va = 0; for (const v of g) va += (v-mean)*(v-mean);
    const sd = Math.sqrt(va / g.length);
    const thr = mean > sd ? mean - sd + 10 : mean;
    for (let i = 0; i < g.length; i++) bits[i] = g[i] <= thr ? 1 : 0;
    info = { mode: 'sketch', mean: mean.toFixed(1), stddev: sd.toFixed(1), threshold: thr.toFixed(1) };
  } else {
    let sum = 0; for (const v of g) sum += v;
    const mean = sum / g.length, f = gammaFactor(mean), inv = 1 / f;
    // OpenCV's convertTo() saturate-casts back to CV_8U after Core.pow, so the
    // gamma result is QUANTISED TO INTEGERS before dithering. Keeping floats here
    // makes the error diffusion drift and produces a different bitmap.
    const buf = new Float32Array(g.length);
    for (let i = 0; i < g.length; i++)
      buf[i] = Math.min(255, Math.max(0, Math.round(Math.pow(g[i]/255, inv) * 255)));
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y*w + x, v = buf[i];
        let err;
        if (v > 127) { err = v - 255; } else { err = v; bits[i] = 1; }
        if (!err) continue;
        for (const [dx, dy, wt] of SIERRA) {
          const nx = x + dx, ny = y + dy;
          if (nx >= 0 && nx < w && ny < h) buf[ny*w + nx] += Math.trunc(err * wt / 32);
        }
      }
    }
    info = { mode: 'photo', mean: mean.toFixed(1), gamma_f: f };
  }
  return { data: pack(bits, w, h), bits, w, h, info };
}
