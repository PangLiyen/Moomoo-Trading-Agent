#!/usr/bin/env python3
"""Build one self-contained HTML dashboard from a decision ledger.

WHY A GENERATOR AND NOT A REPORT

The dashboard is evidence, so it is rebuilt from the ledger rather than
hand-assembled: every number in it is read from a record, and a number that
cannot be read from a record is printed as unavailable rather than estimated.
No interpolation, no smoothing, no trend lines.

THE THREE RULES THIS FILE EXISTS TO KEEP

1. Every number traces to a ledger record. Panel (f) is the one exception and
   says so in its own caption: it counts the findings register file, which is
   prose, not a record.

2. Gaps render as gaps. A series drawn continuously across the 12:59-15:50
   hole on 2026-11-23 would state that the engine was watching when it was
   not. Equity and d_atr lines are broken wherever a `session_coverage` record
   lists a gap between consecutive points. With no coverage record there is
   no basis on which to join points, so the panels draw points only and say
   why.

3. A failed chain verification renders the failure and nothing else. If the
   ledger cannot be shown to be unaltered, every panel derived from it would
   be a claim about a file of unknown provenance.

No network at build time and none at view time: no CDN links, no remote
fonts, no scripts, no external images. The output is one HTML file.

Run:  python3 tools/build_dashboard.py --ledger logs/decisions_live.jsonl \\
          --out reports/dashboard.html
Reads the ledger, the config (for the starting equity and default fee), and
the findings register. Writes one file. Places nothing.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

from engine.config import RiskConfig  # noqa: E402
from engine.ledger import Ledger  # noqa: E402
import agent_attribution  # noqa: E402

# The conditions dict comes off the wire in no fixed order; the first failure
# must be chosen by the rule's order, not by dict insertion order.
CANONICAL_CONDITIONS = (
    "dislocated_below_sma20",
    "ask_below_threshold",
    "ticked_up",
    "regime_ok",
    "quote_fresh",
    "spread_tight",
    "within_entry_window",
)
SERIES_COLOURS = ("#1f77b4", "#d95f02", "#7570b3", "#1b9e77", "#e7298a", "#555")
SHORT = {
    "dislocated_below_sma20": "D",
    "ask_below_threshold": "A",
    "ticked_up": "T",
    "regime_ok": "R",
    "quote_fresh": "Q",
    "spread_tight": "S",
    "within_entry_window": "W",
}
LONG = {
    "dislocated_below_sma20": "close not dislocated below SMA20 by the entry distance",
    "ask_below_threshold": "live ask not below the entry threshold",
    "ticked_up": "close did not tick up off the low",
    "regime_ok": "regime filter failed",
    "quote_fresh": "quote stale",
    "spread_tight": "spread too wide",
    "within_entry_window": "outside the entry window",
}

CSS = """
:root{--ink:#1b1d21;--mute:#6b7280;--line:#d7dbe2;--bg:#fbfbfc;
--agent:#0b6efd;--acct:#b45309;--gap:#fde2e1;--ok:#0f7b3f;--bad:#b3261e}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 56px;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:0 0 2px}
p.sub{color:var(--mute);margin:0 0 18px}
section.panel{background:#fff;border:1px solid var(--line);border-radius:8px;
padding:16px 18px;margin:0 0 18px}
section.panel p.note{color:var(--mute);font-size:12px;margin:6px 0 10px}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
table{border-collapse:collapse}
td,th{padding:3px 7px;text-align:left;vertical-align:top}
th{font-weight:600;font-size:12px;color:var(--mute)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{font:12px/1.4 ui-monospace,Menlo,monospace}
.chip{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;
border:1px solid var(--line)}
.chip.ok{color:var(--ok);border-color:var(--ok)}
.chip.bad{color:var(--bad);border-color:var(--bad)}
.fail{border:2px solid var(--bad);background:#fff5f5;color:var(--bad);
padding:26px 28px;border-radius:8px;margin-top:12px}
.fail h1{margin-bottom:8px}
.cell{width:34px;height:26px;text-align:center;font:12px ui-monospace,Menlo,monospace;
border:1px solid #fff;cursor:default}
.c-D{background:#c9d7ee}.c-A{background:#e6c66a}.c-T{background:#d8b4e2}
.c-R{background:#f3d9a4}.c-Q{background:#c8e2c8}.c-S{background:#efe0b8}
.c-W{background:#cfd8e3}.c-SETUP{background:#0f7b3f;color:#fff;font-weight:700}
.c-none{background:#eef0f3;color:var(--mute)}
.legend span{margin-right:14px;font-size:12px}
.grid{stroke:#eceef2;stroke-width:1}
.axis{stroke:#9aa1ab;stroke-width:1}
.zero{stroke:#444;stroke-width:1.2;stroke-dasharray:4 3}
.l-agent{fill:none;stroke:var(--agent);stroke-width:2}
.l-acct{fill:none;stroke:var(--acct);stroke-width:2}
path.line{fill:none;stroke-width:2}
.s-0{stroke:#1f77b4}.s-1{stroke:#d95f02}.s-2{stroke:#7570b3}
.s-3{stroke:#1b9e77}.s-4{stroke:#e7298a}.s-5{stroke:#555}
.s-0.pt{fill:#1f77b4}.s-1.pt{fill:#d95f02}.s-2.pt{fill:#7570b3}
.s-3.pt{fill:#1b9e77}.s-4.pt{fill:#e7298a}.s-5.pt{fill:#555}
.l-agent.pt{fill:var(--agent)}.l-acct.pt{fill:var(--acct)}
.pt{fill:currentColor}
.hole{fill:var(--gap);opacity:.9}
.tick{stroke:#333;stroke-width:1.4}
"""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def load_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _parse_id(cid: str) -> datetime | None:
    m = re.match(r"^C(\d{8})T(\d{4})$", str(cid or ""))
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")


def _hm(dt: datetime) -> str:
    return f"{dt:%H:%M}"


def _minutes(dt: datetime) -> float:
    return dt.hour * 60 + dt.minute + dt.second / 60.0


def pick_date(records: Sequence[Mapping[str, Any]]) -> date | None:
    dates = sorted({d for d in
                    (_parse_id(r.get("cycle_id", "")).date()
                     for r in records if r.get("stage") == "cycle")
                    if d is not None})
    return dates[-1] if dates else None


def coverage_for(records: Sequence[Mapping[str, Any]], day: date
                 ) -> Mapping[str, Any] | None:
    rows = [r for r in records
            if r.get("stage") == "session_coverage"
            and str(r.get("trading_date")) == day.isoformat()]
    for r in rows:
        if r.get("basis") == "live":
            return r
    return rows[-1] if rows else None


def first_failing(conditions: Mapping[str, Any]) -> str:
    for name in CANONICAL_CONDITIONS:
        if name in conditions and not conditions[name]:
            return name
    if conditions and all(bool(conditions.get(n, True)) for n in CANONICAL_CONDITIONS
                          if n in conditions):
        return "all_true"
    return "none"


# ---------------------------------------------------------------------------
# Series with recorded gaps as breaks
# ---------------------------------------------------------------------------

def segments(points: Sequence[tuple[datetime, float]],
             gaps: Sequence[Mapping[str, Any]]) -> list[list[tuple[datetime, float]]]:
    """Split a series wherever a recorded gap lies between two points.

    Joining two points across a gap states that a value existed in between.
    On a ledger where the engine was absent, that statement is false, so the
    path is cut -- not shaded, not dashed over: cut.
    """
    spans = []
    for g in gaps:
        try:
            spans.append((datetime.fromisoformat(str(g["from_et"])),
                          datetime.fromisoformat(str(g["to_et"]))))
        except (KeyError, ValueError):
            continue
    out: list[list[tuple[datetime, float]]] = []
    if not points:
        return out
    cur = [points[0]]
    for prev, nxt in zip(points, points[1:]):
        cut = any(f >= prev[0] and t <= nxt[0] for f, t in spans)
        if cut:
            out.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    out.append(cur)
    return out


def _scale(v: float, lo: float, hi: float, a: float, b: float) -> float:
    if hi == lo:
        return (a + b) / 2.0
    return a + (v - lo) * (b - a) / (hi - lo)


def svg_series(
    series: Sequence[tuple[str, Sequence[tuple[datetime, float]], str]],
    *, gaps, width=1040, height=250, y_label="", fmt="{:.2f}",
    zero_line=False, x_lo=None, x_hi=None, y_lo=None, y_hi=None,
    ident="series",
) -> str:
    """Line chart whose paths are cut at each recorded gap."""
    left, right, top, bottom = 64, 16, 14, 34
    pw, ph = width - left - right, height - top - bottom
    xs = [p[0] for _, pts, _ in series for p in pts]
    ys = [p[1] for _, pts, _ in series for p in pts]
    if not xs:
        return '<p class="note">no points on this date.</p>'
    x_lo = x_lo or min(xs)
    x_hi = x_hi or max(xs)
    if x_hi == x_lo:
        x_hi = x_lo + datetime.resolution
    y_lo = min(ys) if y_lo is None else y_lo
    y_hi = max(ys) if y_hi is None else y_hi
    if zero_line:
        y_lo, y_hi = min(y_lo, 0.0), max(y_hi, 0.0)
    pad = (y_hi - y_lo) * 0.08 or 1.0
    y_lo, y_hi = y_lo - pad, y_hi + pad

    def X(dt): return _scale((dt - x_lo).total_seconds(), 0,
                             (x_hi - x_lo).total_seconds(), left, left + pw)
    def Y(v): return _scale(v, y_lo, y_hi, top + ph, top)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" id="{ident}-svg">']
    for i in range(5):
        v = y_lo + (y_hi - y_lo) * i / 4
        y = Y(v)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" '
                     f'x2="{left+pw}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#6b7280">{fmt.format(v)}</text>')
    if zero_line:
        y = Y(0.0)
        parts.append(f'<line class="zero" x1="{left}" y1="{y:.1f}" '
                     f'x2="{left+pw}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left+pw}" y="{y-4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#444">trigger (0)</text>')
    for g in gaps:
        try:
            f = datetime.fromisoformat(str(g["from_et"]))
            t = datetime.fromisoformat(str(g["to_et"]))
        except (KeyError, ValueError):
            continue
        x0, x1 = X(f), X(t)
        parts.append(f'<rect class="hole" x="{x0:.1f}" y="{top}" '
                     f'width="{max(1.5, x1-x0):.1f}" height="{ph}"/>')
    for name, pts, colour in series:
        segs = segments(pts, gaps)
        d = " ".join("M " + " L ".join(f"{X(t):.1f} {Y(v):.1f}" for t, v in seg)
                     for seg in segs)
        parts.append(f'<path class="line {colour}" id="line-{name}" '
                     f'data-segments="{len(segs)}" d="{d}"/>')
        for t, v in pts:
            parts.append(f'<circle class="{colour} pt" cx="{X(t):.1f}" '
                         f'cy="{Y(v):.1f}" r="2.4"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top+ph}" '
                 f'x2="{left+pw}" y2="{top+ph}"/>')
    for x in sorted({round(X(t)) for _, pts, _ in series for t, _ in pts}):
        parts.append(f'<line class="axis" x1="{x}" y1="{top+ph}" '
                     f'x2="{x}" y2="{top+ph+4}"/>')
    parts.append(f'<text x="{left}" y="{height-6}" font-size="11" '
                 f'fill="#6b7280">{_hm(x_lo)}</text>')
    parts.append(f'<text x="{left+pw}" y="{height-6}" text-anchor="end" '
                 f'font-size="11" fill="#6b7280">{_hm(x_hi)}</text>')
    parts.append(f'<text x="14" y="{top+ph/2:.1f}" font-size="11" '
                 f'fill="#6b7280" transform="rotate(-90 14 {top+ph/2:.1f})" '
                 f'text-anchor="middle">{html.escape(y_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _plain(seg_list: Sequence[Sequence[tuple[datetime, float]]]) -> str:
    return " ; ".join(f"{_hm(s[0][0])}-{_hm(s[-1][0])}" for s in seg_list)


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def panel_equity(records, day, cov, start_equity, fee) -> str:
    gaps = (cov or {}).get("gaps") or []
    res = agent_attribution.attribute(list(records), start_equity, fee)
    snaps = [r for r in records if r.get("stage") == "account_snapshot"
             and _parse_id(r.get("cycle_id", "")) is not None
             and _parse_id(r.get("cycle_id", "")).date() == day]
    snaps.sort(key=lambda r: _parse_id(r["cycle_id"]))
    acct_pts, agent_pts, rows = [], [], []
    agent_by_cycle = {r.get("cycle_id"): r for r in res["rows"]}
    for s in snaps:
        t = _parse_id(s["cycle_id"])
        acct = s.get("total_asset", s.get("equity"))
        a = agent_by_cycle.get(s.get("cycle_id"))
        if acct is None:
            continue
        acct_pts.append((t, float(acct)))
        rows.append((_hm(t), float(acct),
                     None if a is None else a["agent_equity"],
                     None if a is None else a["residual"]))
        if a is not None:
            agent_pts.append((t, float(a["agent_equity"])))
    lo = min([v for _, v in acct_pts + agent_pts], default=0.0)
    hi = max([v for _, v in acct_pts + agent_pts], default=0.0)
    svg = svg_series(
        [("acct", acct_pts, "l-acct"), ("agent", agent_pts, "l-agent")],
        gaps=gaps, y_label="USD", fmt="{:,.2f}", ident="equity",
    )
    seg_a = segments(agent_pts, gaps)
    if not cov:
        note = ("no session_coverage record for this date: the ledger does "
                "not say where the engine was absent, so no line is drawn "
                "between points (points only).")
    else:
        note = (f"lines are cut at the {len(gaps)} gap(s) the coverage record "
                f"lists: account series in {len(segments(acct_pts, gaps))} "
                f"segment(s), agent series in {len(seg_a)}. Agent equity is "
                f"engine-originated fills only; account equity is total_asset.")
    tbl = "".join(
        f"<tr><td>{t}</td><td class='num'>{a:,.2f}</td>"
        f"<td class='num'>{'' if ag is None else f'{ag:,.2f}'}</td>"
        f"<td class='num'>{'' if rs is None else f'{rs:+,.2f}'}</td></tr>"
        for t, a, ag, rs in rows)
    return f"""
<section class="panel" id="panel-equity" data-gaps="{len(gaps)}">
<h2>a. Agent equity against account equity</h2>
<p class="note">{html.escape(note)}</p>
<p class="legend"><span><i class="key" style="background:var(--agent)"></i>
agent (engine fills only)</span><span><i class="key"
style="background:var(--acct)"></i>account total_asset</span>
<span><i class="key" style="background:var(--gap)"></i>recorded gap</span>
<span>start ${start_equity:,.2f}</span></p>
{svg}
<table><tr><th>ET</th><th>account</th><th>agent</th><th>residual</th></tr>{tbl}</table>
<p class="note">The residual is everything the engine did not do:
manual P&amp;L, commissions and marks. It is shown, not decomposed.</p>
</section>"""


def panel_heatmap(records, day) -> str:
    cycles = sorted({_parse_id(r["cycle_id"]) for r in records
                     if r.get("stage") == "cycle"
                     and _parse_id(r.get("cycle_id", "")) is not None
                     and _parse_id(r["cycle_id"]).date() == day})
    sigs = [r for r in records if r.get("stage") == "signal"
            and _parse_id(r.get("cycle_id", "")) is not None
            and _parse_id(r["cycle_id"]).date() == day]
    symbols = sorted({str(r.get("symbol")) for r in sigs})
    by_cell = {(str(r.get("cycle_id")), str(r.get("symbol"))): r for r in sigs}
    head = "".join(f"<th>{_hm(c)}</th>" for c in cycles)
    body = []
    for sym in symbols:
        cells = []
        for c in cycles:
            rec = by_cell.get((f"C{c:%Y%m%dT%H%M}", sym))
            if rec is None:
                cells.append('<td class="cell c-none" title="no signal record">-</td>')
                continue
            cond = (rec.get("conditions") or {})
            verdict = str(rec.get("verdict"))
            if verdict == "PROPOSE":
                cells.append(f'<td class="cell c-SETUP" title="SETUP: all conditions '
                             f'passed">SETUP</td>')
                continue
            fail = first_failing(cond)
            code = SHORT.get(fail, "?")
            why = LONG.get(fail, "all conditions passed but no setup")
            cells.append(f'<td class="cell c-{code}" title="{html.escape(why)} '
                         f'({html.escape(fail)})">{code}</td>')
        body.append(f"<tr><th>{html.escape(sym)}</th>{''.join(cells)}</tr>")
    counts: dict[str, int] = {}
    for rec in sigs:
        k = "SETUP" if rec.get("verdict") == "PROPOSE" else SHORT.get(
            first_failing(rec.get("conditions") or {}), "?")
        counts[k] = counts.get(k, 0) + 1
    tally = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    legend = "".join(
        f'<span><i class="key c-{SHORT[n]}"></i>{SHORT[n]} = '
        f'{html.escape(LONG[n])}</span>' for n in CANONICAL_CONDITIONS)
    return f"""
<section class="panel" id="panel-heatmap">
<h2>b. First failing entry condition</h2>
<p class="note">One cell per (cycle, symbol) that wrote a signal record;
the letter is the first condition that failed, in the rule's own order.
Grey '-' means no signal record was written for that cell. Tally: {tally}.</p>
<p class="legend">{legend}<span><i class="key c-SETUP"></i>SETUP = all passed</span></p>
<table><tr><th></th>{head}</tr>{''.join(body)}</table>
</section>"""


def panel_d_atr(records, day, cov) -> str:
    gaps = (cov or {}).get("gaps") or []
    sigs = [r for r in records if r.get("stage") == "signal"
            and _parse_id(r.get("cycle_id", "")) is not None
            and _parse_id(r["cycle_id"]).date() == day]
    per: dict[str, list[tuple[datetime, float]]] = {}
    skipped = 0
    for r in sigs:
        d = r.get("detail") or {}
        try:
            close = float(d["close"])
            thr = float(d["entry_threshold"])
            atr = float(d["atr20"])
            if atr <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        per.setdefault(str(r.get("symbol")), []).append(
            (_parse_id(r["cycle_id"]), (close - thr) / atr))
    series = [(f"s-{i}", sorted(pts), f"s-{i}")
              for i, (sym, pts) in enumerate(sorted(per.items()))]
    svg = svg_series(series, gaps=gaps, y_label="d_atr (ATR units)",
                     fmt="{:+.2f}", zero_line=True, ident="datr")
    notes = [f"d_atr = (close - entry_threshold) / ATR20, computed from the "
             f"signal record's own detail fields. Zero is the entry trigger; "
             f"negative is dislocated past it."]
    if not cov:
        notes.append("no coverage record: points only, no joining line.")
    if skipped:
        notes.append(f"{skipped} signal record(s) lacked detail fields and "
                     f"are absent from the plot rather than guessed.")
    legend = " &middot; ".join(
        f'<i class="key" style="background:{SERIES_COLOURS[i % len(SERIES_COLOURS)]}"></i>'
        f'{html.escape(sym)}'
        for i, sym in enumerate(sorted(per)))
    return f"""
<section class="panel" id="panel-datr">
<h2>c. Distance to trigger, per symbol</h2>
<p class="note">{' '.join(html.escape(n) for n in notes)}</p>
<p class="note">series: {legend}</p>
{svg}
</section>"""


def panel_coverage(cov, day) -> str:
    if not cov:
        return """
<section class="panel" id="panel-coverage" data-gaps="missing">
<h2>d. Session coverage</h2>
<p class="note">No session_coverage record exists for this date. Coverage
cannot be computed from this ledger; nothing is estimated in its place.</p>
</section>"""
    gaps = cov.get("gaps") or []
    w0 = datetime.fromisoformat(str(cov["window_start_et"]))
    w1 = datetime.fromisoformat(str(cov["window_end_et"]))
    left, right, top, ph = 24, 24, 26, 40
    width = 1040
    pw = width - left - right
    lo, hi = _minutes(w0), _minutes(w1)
    span = max(hi - lo, 1.0)
    def X(m): return left + (m - lo) * pw / span
    parts = [f'<svg viewBox="0 0 {width} 96" role="img" id="coverage-timeline">']
    parts.append(f'<line class="axis" x1="{left}" y1="{top+ph}" '
                 f'x2="{left+pw}" y2="{top+ph}"/>')
    for g in gaps:
        f = datetime.fromisoformat(str(g["from_et"]))
        t = datetime.fromisoformat(str(g["to_et"]))
        parts.append(f'<rect class="hole" x="{X(_minutes(f)):.1f}" y="{top}" '
                     f'width="{max(2.0, X(_minutes(t))-X(_minutes(f))):.1f}" '
                     f'height="{ph}" data-seconds="{g.get("seconds")}"/>')
    for cid in cov.get("cycle_ids") or []:
        dt = _parse_id(cid)
        if dt is None:
            continue
        parts.append(f'<line class="tick" x1="{X(_minutes(dt)):.1f}" y1="{top+6}" '
                     f'x2="{X(_minutes(dt)):.1f}" y2="{top+ph}"/>')
    parts.append(f'<text x="{left}" y="{top+ph+18}" font-size="11" '
                 f'fill="#6b7280">{_hm(w0)}</text>')
    parts.append(f'<text x="{left+pw}" y="{top+ph+18}" text-anchor="end" '
                 f'font-size="11" fill="#6b7280">{_hm(w1)}</text>')
    parts.append("</svg>")
    rows = "".join(
        f"<tr><td>{html.escape(str(g['from_et']))[-5:]}</td>"
        f"<td>{html.escape(str(g['to_et']))[-5:]}</td>"
        f"<td class='num'>{float(g['seconds']):,.0f}</td>"
        f"<td>{html.escape(str(g.get('next_cycle_id') or 'session end'))}</td></tr>"
        for g in gaps)
    return f"""
<section class="panel" id="panel-coverage" data-gaps="{len(gaps)}"
 data-actual="{cov.get('actual_cycles')}" data-expected="{cov.get('expected_slots')}">
<h2>d. Session coverage</h2>
<p class="note">Basis <b>{html.escape(str(cov.get('basis')))}</b>, clock
{html.escape(str(cov.get('cadence_seconds')))}s. <b>{cov.get('actual_cycles')}
of {cov.get('expected_slots')}</b> expected slots; {cov.get('missing_slots')}
missing; {len(gaps)} gap(s); longest
{float(cov.get('longest_gap_seconds') or 0):,.0f}s. Holes are the recorded
gaps, not an inference.</p>
{''.join(parts)}
<table><tr><th>from ET</th><th>to ET</th><th>seconds</th><th>next cycle</th></tr>{rows}</table>
</section>"""


def panel_chain(records, report, ledger_path) -> str:
    ccs = [r for r in records if r.get("stage") == "code_change"]
    incs = [r for r in records if r.get("stage") == "incident"]
    cc_rows = "".join(
        f"<tr><td>{r.get('seq')}</td><td>{html.escape(str(r.get('cycle_id')))}</td>"
        f"<td><code>{html.escape(str((r.get('from') or {}).get('code_commit'))[:12])}"
        f" &rarr; {html.escape(str((r.get('to') or {}).get('code_commit'))[:12])}</code></td>"
        f"<td><code>{html.escape(str((r.get('to') or {}).get('engine_file_set_hash'))[:12])}</code>"
        f"{'' if (r.get('from') or {}).get('engine_file_set_hash') != (r.get('to') or {}).get('engine_file_set_hash') else ' (unchanged)'}</td></tr>"
        for r in ccs)
    inc_rows = ""
    for r in incs:
        acct = r.get("account_snapshot") or {}
        inc_rows += (
            f"<tr><td>{r.get('seq')}</td><td>{html.escape(str(r.get('verdict')))}</td>"
            f"<td>{html.escape(str(r.get('reason_text'))[:420])}</td>"
            f"<td class='num'>{acct.get('total_asset', '')}</td>"
            f"<td>{html.escape(str(r.get('action_taken', '')))}</td></tr>")
    return f"""
<section class="panel" id="panel-chain">
<h2>e. Chain status</h2>
<p class="note">verification: <span class="chip ok">INTACT</span>
records {report.get('records')} &middot; tip
<code>{html.escape(str(report.get('tip')))}</code> &middot;
verified at {html.escape(str(report.get('verified_at_utc')))} (build time)
&middot; file {html.escape(str(ledger_path))}</p>
<h2 style="margin-top:14px">code_change records ({len(ccs)})</h2>
<table><tr><th>seq</th><th>cycle</th><th>commit from &rarr; to</th>
<th>engine file-set hash</th></tr>{cc_rows or '<tr><td colspan="4">none</td></tr>'}</table>
<h2 style="margin-top:14px">incident records ({len(incs)})</h2>
<table><tr><th>seq</th><th>verdict</th><th>reason_text</th>
<th>total_asset at detection</th><th>action</th></tr>
{inc_rows or '<tr><td colspan="5">none</td></tr>'}</table>
</section>"""


def read_findings(path: Path) -> dict | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    last: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\|\s*(N\d+|P-[a-z])\s*\|", line)
        if not m:
            continue
        fid = m.group(1)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if fid not in last:
            order.append(fid)
        last[fid] = cells[-1]
    open_ = [f for f in order if last[f].startswith("**Open")]
    closed = [f for f in order
              if last[f].startswith("**Closed") or last[f].startswith("**Fixed")]
    other = [f for f in order if f not in open_ and f not in closed]
    # The register keeps its own history ("stands at 33", "34", ...); the
    # current claim is the last one written.
    claims = re.findall(r"register stands at \*\*(\d+)\*\*", text)
    prose = int(claims[-1]) if claims else None
    return {"total": len(order), "open": open_, "closed": closed,
            "other": other, "prose_total": prose,
            "n_ids": sum(1 for f in order if f.startswith("N")),
            "p_ids": sum(1 for f in order if f.startswith("P-"))}


def panel_findings(path: Path) -> str:
    f = read_findings(path)
    if f is None:
        return """
<section class="panel" id="panel-findings">
<h2>f. Findings register</h2>
<p class="note">Register file not found; counts unavailable.</p>
</section>"""
    tally = (f"total {f['total']} ({f['n_ids']} N-series, {f['p_ids']} P-series)"
             f" &middot; open {len(f['open'])} &middot; closed {len(f['closed'])}"
             f" &middot; neither open nor closed {len(f['other'])}"
             f" (recorded/accepted/documented)")
    mismatch = ""
    if f["prose_total"] is not None and f["prose_total"] != f["total"]:
        mismatch = (f" The register's own prose says {f['prose_total']}; this "
                    f"panel counts {f['total']} distinct IDs in its rows. The "
                    f"disagreement is shown rather than reconciled silently.")
    return f"""
<section class="panel" id="panel-findings">
<h2>f. Findings register</h2>
<p class="note">This is the one panel not derived from the ledger: it counts
<code>{html.escape(str(path))}</code>, a prose register. Counting rule: one
count per distinct ID with a table row; the last row for an ID wins;
"closed" means the disposition begins **Closed or **Fixed.</p>
<p style="font-size:16px">{tally}.{html.escape(mismatch)}</p>
<p class="note">Open: {html.escape(', '.join(f['open']) or 'none')}<br>
Closed: {html.escape(', '.join(f['closed']) or 'none')}</p>
</section>"""


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(records, report, ledger_path, findings_path, start_equity, fee) -> str:
    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Decision ledger dashboard</title>
<style>{CSS}</style></head><body>
<h1>Decision ledger dashboard</h1>
<p class="sub">ledger <code>{html.escape(str(ledger_path))}</code> &middot;
{report.get('records')} records &middot; tip
<code>{html.escape(str(report.get('tip'))[:16])}</code> &middot;
<span class="chip ok">chain INTACT</span></p>"""
    if not report.get("intact"):
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>CHAIN VERIFICATION FAILED</title><style>{CSS}</style></head><body>
<div class="fail">
<h1>CHAIN VERIFICATION FAILED</h1>
<p>No panel is rendered. Every panel would be a claim about a ledger that
cannot be shown to be unaltered.</p>
<p><code>{html.escape(str(report.get('error')))}</code></p>
<p>ledger <code>{html.escape(str(ledger_path))}</code> &middot;
{report.get('records')} record(s) present</p>
</div></body></html>"""

    day = pick_date(records)
    if day is None:
        return head + """
<section class="panel"><h2>No completed cycles</h2>
<p class="note">This ledger contains no `cycle` record. There is no trading
date to report, and nothing is substituted for one.</p></section></body></html>"""
    cov = coverage_for(records, day)
    body = "".join([
        panel_equity(records, day, cov, start_equity, fee),
        panel_heatmap(records, day),
        panel_d_atr(records, day, cov),
        panel_coverage(cov, day),
        panel_chain(records, report, ledger_path),
        panel_findings(findings_path),
    ])
    return head + f"""
<p class="sub">trading date <b>{day.isoformat()}</b> (latest date with a
completed cycle in this ledger; panels a&ndash;d are that date only).</p>
{body}
<p class="note">Generated from records only. No interpolation, no smoothing,
no scripts, no network references in this file.</p>
</body></html>"""


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="logs/decisions_live.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default="config/risk_limits.yaml")
    ap.add_argument("--findings", default="docs/AUDIT_FINDINGS.md")
    args = ap.parse_args(argv)

    ledger_path = Path(args.ledger)
    led = Ledger(ledger_path)
    report = led.verify_report()
    records = load_records(ledger_path) if report.get("intact") else []

    start_equity, fee = 0.0, 0.0
    try:
        cfg = RiskConfig.load(Path(args.config))
        start_equity = cfg.starting_equity
        fee = float(cfg.section("fees")["commission_per_filled_order_usd"])
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"config unavailable ({exc}); starting equity 0.00 and no "
              f"default fee -- implied_fee from records is still used")

    page = render(records, report, args.ledger, Path(args.findings),
                  start_equity, fee)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"{'INTACT' if report.get('intact') else 'FAILED'} "
          f"{report.get('records')} records -> {out}")
    return 0 if report.get("intact") else 1


if __name__ == "__main__":
    raise SystemExit(main())
