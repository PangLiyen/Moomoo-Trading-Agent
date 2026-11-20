import fs from 'node:fs';

const SYM = process.argv[2];
const FILE = process.argv[3] || `bars_US_${SYM}.json`;
const raw = JSON.parse(fs.readFileSync(new URL(FILE, import.meta.url), 'utf8'));

const fmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});
function et(tk) {
  const p = Object.fromEntries(fmt.formatToParts(new Date(tk)).map((x) => [x.type, x.value]));
  const d = `${p.year}-${p.month}-${p.day}`;
  const hh = p.hour === '24' ? '00' : p.hour;
  return { d, t: `${hh}:${p.minute}`, mins: +hh * 60 + +p.minute };
}

const norm = raw.map((b) => {
  const tk = b.time_key ?? b.k;
  const e = et(tk);
  return {
    tk, d: e.d, t: e.t, mins: e.mins,
    o: +(b.open ?? b.o), h: +(b.high ?? b.h), l: +(b.low ?? b.l), c: +(b.close ?? b.c),
    v: +(b.volume ?? b.v ?? 0),
  };
}).sort((a, b) => a.tk - b.tk);

// dedupe
const seen = new Set();
const bars = [];
for (const b of norm) { if (seen.has(b.tk)) continue; seen.add(b.tk); bars.push(b); }

// keep 09:35..15:55 only (drop the 16:00 closing print and anything off-grid)
const kept = bars.filter((b) => b.mins >= 575 && b.mins <= 955);

const WIN_FROM = '2026-11-09', WIN_TO = '2026-11-19';

// TR
for (let i = 0; i < kept.length; i++) {
  const b = kept[i], p = kept[i - 1];
  b.prevC = p ? p.c : null;
  b.tr = p ? Math.max(b.h - b.l, Math.abs(b.h - p.c), Math.abs(b.l - p.c)) : null;
}
// Wilder ATR20 seeded with mean of TR[1..20], emitted from index 20
const ATR_P = 20;
let atr = null, seedSum = 0, seedN = 0;
for (let i = 1; i < kept.length; i++) {
  const b = kept[i];
  if (i <= ATR_P) {
    seedSum += b.tr; seedN++;
    if (seedN === ATR_P) { atr = seedSum / ATR_P; b.atr20 = atr; }
  } else {
    atr = (atr * (ATR_P - 1) + b.tr) / ATR_P;
    b.atr20 = atr;
  }
}
// SMA20
for (let i = 0; i < kept.length; i++) {
  if (i < 19) continue;
  let s = 0; for (let j = i - 19; j <= i; j++) s += kept[j].c;
  kept[i].sma20 = s / 20;
}

const win = kept.filter((b) => b.d >= WIN_FROM && b.d <= WIN_TO && b.atr20 != null && b.sma20 != null);
const perDate = {}, qual = [], countDate = {};
for (const b of win) perDate[b.d] = (perDate[b.d] || 0) + 1;

for (const b of win) {
  const thr = b.sma20 - 1.5 * b.atr20;
  const a = b.c < thr, c2 = b.prevC != null && b.c > b.prevC;
  const c3 = b.mins >= 600 && b.mins < 900;
  const inWinTime = c3; // same-day time gate
  if (a && c2 && c3) {
    qual.push({ d: b.d, t: b.t, c: +b.c.toFixed(4), prev: +b.prevC.toFixed(4), sma: +b.sma20.toFixed(4), atr: +b.atr20.toFixed(4), thr: +thr.toFixed(4), gap: +(thr - b.c).toFixed(4) });
    countDate[b.d] = (countDate[b.d] || 0) + 1;
  }
  b._q = a && c2 && (b.mins >= 600 && b.mins <= 900); // alt incl 15:00
}
const qualAlt = win.filter((b) => b._q).length;

const atrs = win.map((b) => b.atr20).sort((x, y) => x - y);
const med = (a) => a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2;

const moves = [];
for (let i = 1; i < kept.length; i++) if (kept[i].d >= WIN_FROM && kept[i].d <= WIN_TO && kept[i].prevC != null) moves.push({ v: Math.abs(kept[i].c - kept[i].prevC), cross: kept[i].d !== kept[i - 1].d });
const intra = moves.filter((m) => !m.cross);
const mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;

const last = kept[kept.length - 1];
const latest = win[win.length - 1];

// outliers
const rng = win.map((b) => b.h - b.l).sort((a, b) => a - b);
const medR = med(rng);
const out = win.filter((b) => (b.h - b.l) > 10 * medR || b.h > Math.max(b.o, b.c) * 1.01 || b.l < Math.min(b.o, b.c) * 0.99);

// outlier-excluded recompute
let ex = null;
if (out.length) {
  const set = new Set(out.map((b) => b.tk));
  const k2 = kept.filter((b) => !set.has(b.tk)).map((b) => ({ ...b }));
  for (let i = 0; i < k2.length; i++) { const b = k2[i], p = k2[i - 1]; b.prevC = p ? p.c : null; b.tr = p ? Math.max(b.h - b.l, Math.abs(b.h - p.c), Math.abs(b.l - p.c)) : null; }
  let a2 = null, s2 = 0, n2 = 0;
  for (let i = 1; i < k2.length; i++) { const b = k2[i]; if (i <= 20) { s2 += b.tr; n2++; if (n2 === 20) { a2 = s2 / 20; b.atr20 = a2; } } else { a2 = (a2 * 19 + b.tr) / 20; b.atr20 = a2; } }
  for (let i = 0; i < k2.length; i++) { if (i < 19) continue; let s = 0; for (let j = i - 19; j <= i; j++) s += k2[j].c; k2[i].sma20 = s / 20; }
  const w2 = k2.filter((b) => b.d >= WIN_FROM && b.d <= WIN_TO && b.atr20 != null && b.sma20 != null);
  const q2 = w2.filter((b) => b.c < b.sma20 - 1.5 * b.atr20 && b.prevC != null && b.c > b.prevC && b.mins >= 600 && b.mins < 900);
  const a2s = w2.map((b) => b.atr20).sort((x, y) => x - y);
  ex = { atr_now: +w2[w2.length - 1].atr20.toFixed(4), atr_min: +a2s[0].toFixed(4), atr_med: +med(a2s).toFixed(4), atr_max: +a2s[a2s.length - 1].toFixed(4), qual: q2.length };
}

const out2 = {
  symbol: `US.${SYM}`,
  bars_total_kept: kept.length,
  first_bar: { d: kept[0].d, t: kept[0].t, c: kept[0].c },
  last_bar: { d: last.d, t: last.t, c: last.c },
  window: [WIN_FROM, WIN_TO],
  bars_per_date: perDate,
  atr_defined_bars: atrs.length,
  current: { close: +last.c.toFixed(4), sma20: +latest.sma20.toFixed(4), atr20: +latest.atr20.toFixed(4), pct_atr_of_price: +(100 * latest.atr20 / last.c).toFixed(4) },
  atr20_stats: { min: +atrs[0].toFixed(4), median: +med(atrs).toFixed(4), max: +atrs[atrs.length - 1].toFixed(4) },
  avg_move_with_gap: { usd: +mean(moves.map((m) => m.v)).toFixed(4), pct: +(100 * mean(moves.map((m) => m.v)) / last.c).toFixed(4), n: moves.length },
  avg_move_intraday: { usd: +mean(intra.map((m) => m.v)).toFixed(4), pct: +(100 * mean(intra.map((m) => m.v)) / last.c).toFixed(4), n: intra.length },
  qual_count: qual.length,
  qual_count_incl_1500: qualAlt,
  qual_per_date: countDate,
  qual_bars: qual,
  outliers: out.map((b) => ({ d: b.d, t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v, range: +(b.h - b.l).toFixed(4), x_median: +((b.h - b.l) / medR).toFixed(2) })),
  median_range: +medR.toFixed(4),
  outlier_excluded: ex,
};
console.log(JSON.stringify(out2, null, 1));
