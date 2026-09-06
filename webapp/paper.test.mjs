import { P21, PAPER } from './app.js';

const run = async (kind, dots, label) => {
  const p = new P21();
  const seen = [];
  p._raw = async b => seen.push(Buffer.from(b).toString('hex'));
  await p.configurePaper(kind, dots);
  await p.printRaster(new Uint8Array(48 * 4), 384, 4, { paper: kind });
  console.log(`\n--- ${label}`);
  for (const h of seen) console.log('   ', h.length > 40 ? h.slice(0, 40) + `… (${h.length/2}B)` : h);
  return seen;
};

const lab = await run(PAPER.label, 320, 'die-cut, 40 mm pitch (320 dots)');
const con = await run(PAPER.continuous, 0, 'continuous roll');

const has = (a, hex) => a.some(x => x === hex);
const checks = [
  ['label: set paper type 02',      has(lab, '10ff100302')],
  ['label: pitch 320 = 0x0140 BE',  has(lab, '10ff12' + '0140')],
  ['label: GS FF form feed',        has(lab, '1d0c')],
  ['label: no fixed ESC J feed',    !lab.some(x => x.startsWith('1b4a'))],
  ['label: end-of-job present',     has(lab, '10fffe45')],
  ['cont: set paper type 01',       has(con, '10ff100301')],
  ['cont: no pitch command',        !con.some(x => x.startsWith('10ff12'))],
  ['cont: ESC J feed 96 = 0x60',    has(con, '1b4a60')],
  ['cont: no form feed',            !has(con, '1d0c')],
  ['cont: end-of-job present',      has(con, '10fffe45')],
];
console.log();
let bad = 0;
for (const [name, ok] of checks) { if (!ok) bad++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}`); }
process.exit(bad ? 1 : 0);
