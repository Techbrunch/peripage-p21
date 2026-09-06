// Regression + sanity tests for the image pipeline.
//
// The `photo`, `sketch` and `text` modes are hardware-validated ports of the
// Android app (webapp/README.md records their hashes), so this pins their exact
// output: any refactor that changes a single byte is a bug, not an improvement.
import { createHash } from 'node:crypto';
import { convert } from './app.js';

// A deterministic image with gradients, flats and hard edges -- enough structure
// that a wrong kernel or divisor cannot hash the same by luck.
function fixture(w = 96, h = 64) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      let v = (x * 255 / (w - 1)) * 0.6 + (y * 255 / (h - 1)) * 0.4;   // diagonal ramp
      if (x > w * 0.7 && y < h * 0.3) v = 20;                          // dark block
      if (x < w * 0.2 && y > h * 0.7) v = 240;                         // light block
      if ((x + y) % 17 === 0) v = 128;                                 // texture
      data[i] = data[i+1] = data[i+2] = Math.round(v);
      data[i+3] = 255;
    }
  }
  return { width: w, height: h, data };
}

const img = fixture();
const hash = mode => {
  const r = convert(img, mode);
  return { mode, hash: createHash('sha256').update(r.data).digest('hex').slice(0, 16),
           black: (100 * r.bits.reduce((a, b) => a + b, 0) / (r.w * r.h)).toFixed(1) + '%' };
};

// Pinned before the kernel-table refactor. These three are ports of the Android
// app, validated on hardware; if one moves, the port has been broken.
const PINNED = {
  photo:  '33e00a3bca41aea9',
  sketch: 'a42bed747d43d8fc',
  text:   '68a9b736c9893cd4',
};

let bad = 0;
const say = (ok, msg) => { if (!ok) bad++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

console.log('\nregression -- hardware-validated modes must not move');
for (const [m, want] of Object.entries(PINNED)) {
  const got = hash(m).hash;
  say(got === want, `${m.padEnd(8)} ${got}${got === want ? '' : ` (expected ${want})`}`);
}

console.log('\nnew modes -- must produce distinct, non-degenerate output');
const seen = new Map(Object.entries(PINNED).map(([m, h]) => [h, m]));
for (const m of ['floyd', 'jarvis', 'atkinson']) {
  const r = hash(m), pct = parseFloat(r.black);
  say(!seen.has(r.hash), `${m.padEnd(8)} ${r.hash} distinct from ${seen.get(r.hash) || 'all others'}`);
  seen.set(r.hash, m);
  // A broken kernel or divisor typically collapses to all-black or all-white.
  say(pct > 15 && pct < 60, `${m.padEnd(8)} black=${r.black} is in a sane range`);
}

console.log('\ninvert');
for (const m of ['photo', 'atkinson']) {
  const a = convert(img, m), b = convert(img, m, { invert: true });
  const flipped = a.bits.every((v, i) => v === (b.bits[i] ^ 1));
  say(flipped, `${m.padEnd(8)} every bit flipped, and only the bits`);
  say(convert(img, m).bits.every((v, i) => v === a.bits[i]), `${m.padEnd(8)} invert:false unchanged`);
}

console.log();
process.exit(bad ? 1 : 0);
