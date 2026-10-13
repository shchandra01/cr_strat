"""Self-contained HTML tearsheet for a backtest.

One file with no external dependencies: headline figures, charts drawn in the
browser from data embedded in the page (with a hover readout of every series),
and the tables a researcher reads next: statistics for the unhedged and
rates-hedged series, P&L attribution, monthly returns, drawdowns, exposures
and the best and worst holding episodes. The CSVs written next to it hold the
full data behind every chart and table.
"""

from __future__ import annotations

import html
import json
import math

import numpy as np
import pandas as pd

from cr_strat import analytics
from cr_strat.backtest import COMPONENTS, BacktestResult

_STYLE = """
.viz-root { color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
  --series-5: #e87ba4; --series-6: #008300; --pos: #2a78d6; --neg: #e34948; --mid: #f0efec; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .viz-root { color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  --series-5: #d55181; --series-6: #008300; --pos: #3987e5; --neg: #e66767; --mid: #383835; } }
:root[data-theme="dark"] .viz-root { color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  --series-5: #d55181; --series-6: #008300; --pos: #3987e5; --neg: #e66767; --mid: #383835; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); }
.viz-root { font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--text-primary);
  background: var(--page); padding: 24px 16px 48px; max-width: 1180px; margin: 0 auto; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; }
h2 { font-size: 16px; font-weight: 600; margin: 32px 0 10px; }
.sub { color: var(--text-secondary); margin: 0 0 4px; }
.banner { border: 1px solid var(--border); border-left: 4px solid var(--series-4); background: var(--surface-1);
  padding: 10px 14px; border-radius: 6px; margin: 14px 0; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 18px 0; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.tile .label { color: var(--text-secondary); font-size: 12px; }
.tile .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 10px 0; }
.card h3 { font-size: 14px; font-weight: 600; margin: 0 0 2px; }
.card .note { color: var(--text-secondary); font-size: 12px; margin: 0 0 8px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 10px; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--text-secondary); margin: 4px 0 6px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { display: inline-block; width: 14px; height: 2px; border-radius: 1px; }
.chart { position: relative; }
.chart svg { display: block; width: 100%; overflow: visible; }
.chart svg:focus { outline: 2px solid var(--series-1); outline-offset: 2px; }
.tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 9px; font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); display: none;
  white-space: nowrap; z-index: 2; }
.tip .d { color: var(--text-secondary); margin-bottom: 3px; }
.tip .r { display: flex; align-items: center; gap: 6px; }
.tip .r b { font-weight: 600; min-width: 64px; text-align: right; font-variant-numeric: tabular-nums; }
.tip .r i { display: inline-block; width: 12px; height: 2px; }
.tip .r span { color: var(--text-secondary); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
td { font-variant-numeric: tabular-nums; }
.foot { color: var(--muted); font-size: 12px; margin-top: 28px; }
"""

_SCRIPT = r"""
const fmt = {
  pct: v => (v * 100).toFixed(2) + '%',
  idx: v => v.toFixed(1),
  cr: v => (v < 0 ? '−' : '') + '₹' + Math.abs(v / 1e7).toFixed(2) + ' cr',
  num: v => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)),
  int: v => v.toFixed(0),
};
const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function niceTicks(lo, hi, n) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}
function lineChart(root, spec) {
  const legend = root.querySelector('.legend');
  if (spec.series.length > 1) {
    spec.series.forEach(s => {
      const item = document.createElement('span'), key = document.createElement('i');
      key.style.background = `var(--series-${s.slot})`;
      item.appendChild(key); item.appendChild(document.createTextNode(s.name)); legend.appendChild(item);
    });
  }
  const box = root.querySelector('.chart'), tip = box.querySelector('.tip');
  const x = spec.x.map(d => new Date(d + 'T00:00:00Z').getTime());
  const draw = () => {
    box.querySelectorAll('svg').forEach(s => s.remove());
    const W = Math.max(box.clientWidth, 280), H = spec.height || 240;
    const m = {l: 58, r: 14, t: 10, b: 26};
    const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H, tabindex: 0, role: 'img',
                           'aria-label': spec.title}, null);
    box.insertBefore(svg, tip);
    const vals = spec.series.flatMap(s => s.values.filter(v => v !== null));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (spec.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const yt = niceTicks(lo, hi, 4);
    lo = Math.min(lo, yt[0]); hi = Math.max(hi, yt[yt.length - 1]);
    const Y = v => m.t + (hi - v) / Math.max(hi - lo, 1e-12) * (H - m.t - m.b);
    const lastIdx = s => s.values.length - 1 - [...s.values].reverse().findIndex(v => v !== null);
    // Direct end labels only when they cannot collide; the legend carries identity otherwise.
    const endYs = spec.series.map(s => Y(s.values[lastIdx(s)])).sort((a, b) => a - b);
    const labelled = spec.series.length <= 3 && endYs.every((v, i) => i === 0 || v - endYs[i - 1] >= 14);
    if (labelled) m.r = 72;
    const X = t => m.l + (t - x[0]) / Math.max(x[x.length - 1] - x[0], 1) * (W - m.l - m.r);
    yt.forEach(v => {
      el('line', {x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: v === 0 ? 'var(--axis)' : 'var(--grid)',
                  'stroke-width': 1}, svg);
      const t = el('text', {x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)'}, svg);
      t.textContent = fmt[spec.format](v);
    });
    const y0 = new Date(x[0]).getUTCFullYear(), y1 = new Date(x[x.length - 1]).getUTCFullYear();
    const every = Math.max(1, Math.ceil((y1 - y0 + 1) / Math.max(1, Math.floor((W - m.l - m.r) / 70))));
    for (let yr = y0 + 1; yr <= y1; yr += every) {
      const t = Date.UTC(yr, 0, 1), px = X(t);
      el('line', {x1: px, x2: px, y1: H - m.b, y2: H - m.b + 4, stroke: 'var(--axis)'}, svg);
      const lab = el('text', {x: px, y: H - 8, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)'}, svg);
      lab.textContent = yr;
    }
    el('line', {x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: 'var(--axis)'}, svg);
    const ends = [];
    spec.series.forEach(s => {
      let d = '', pen = false;
      s.values.forEach((v, i) => {
        if (v === null) { pen = false; return; }
        d += (pen ? 'L' : 'M') + X(x[i]).toFixed(1) + ',' + Y(v).toFixed(1); pen = true;
      });
      el('path', {d, fill: 'none', stroke: `var(--series-${s.slot})`, 'stroke-width': 2,
                  'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
      const last = lastIdx(s);
      ends.push({s, y: Y(s.values[last]), x: X(x[last]), v: s.values[last]});
    });
    if (labelled) {
      ends.forEach(e => {
        el('circle', {cx: e.x, cy: e.y, r: 4, fill: `var(--series-${e.s.slot})`, stroke: 'var(--surface-1)',
                      'stroke-width': 2}, svg);
        const t = el('text', {x: e.x + 8, y: e.y + 4, 'font-size': 11, fill: 'var(--text-secondary)'}, svg);
        t.textContent = fmt[spec.format](e.v);
      });
    }
    const hair = el('line', {y1: m.t, y2: H - m.b, stroke: 'var(--axis)', 'stroke-width': 1, visibility: 'hidden'}, svg);
    const dots = spec.series.map(s => el('circle', {r: 4, fill: `var(--series-${s.slot})`, stroke: 'var(--surface-1)',
                                                    'stroke-width': 2, visibility: 'hidden'}, svg));
    const hit = el('rect', {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent'}, svg);
    let cur = x.length - 1;
    const show = (i, px) => {
      cur = i;
      const cx = X(x[i]);
      hair.setAttribute('x1', cx); hair.setAttribute('x2', cx); hair.setAttribute('visibility', 'visible');
      tip.replaceChildren();
      const d = document.createElement('div'); d.className = 'd'; d.textContent = spec.x[i]; tip.appendChild(d);
      spec.series.forEach((s, k) => {
        const v = s.values[i];
        if (v === null) { dots[k].setAttribute('visibility', 'hidden'); return; }
        dots[k].setAttribute('cx', cx); dots[k].setAttribute('cy', Y(v)); dots[k].setAttribute('visibility', 'visible');
        const r = document.createElement('div'); r.className = 'r';
        const key = document.createElement('i'); key.style.background = `var(--series-${s.slot})`;
        const b = document.createElement('b'); b.textContent = fmt[spec.format](v);
        const n = document.createElement('span'); n.textContent = s.name;
        r.append(key, b, n); tip.appendChild(r);
      });
      tip.style.display = 'block';
      const left = (px === undefined ? cx : px) / W * box.clientWidth;
      tip.style.left = Math.min(left + 12, box.clientWidth - tip.offsetWidth - 4) + 'px';
      tip.style.top = '8px';
    };
    const hide = () => { tip.style.display = 'none'; hair.setAttribute('visibility', 'hidden');
                         dots.forEach(d => d.setAttribute('visibility', 'hidden')); };
    hit.addEventListener('pointermove', ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) / r.width * W;
      const t = x[0] + (px - m.l) / (W - m.l - m.r) * (x[x.length - 1] - x[0]);
      let lo2 = 0, hi2 = x.length - 1;
      while (hi2 - lo2 > 1) { const mid = (lo2 + hi2) >> 1; if (x[mid] < t) lo2 = mid; else hi2 = mid; }
      show(Math.abs(x[lo2] - t) <= Math.abs(x[hi2] - t) ? lo2 : hi2, px);
    });
    hit.addEventListener('pointerleave', hide);
    svg.addEventListener('focus', () => show(cur));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowLeft') { show(Math.max(cur - 1, 0)); ev.preventDefault(); }
      if (ev.key === 'ArrowRight') { show(Math.min(cur + 1, x.length - 1)); ev.preventDefault(); }
    });
  };
  draw();
  new ResizeObserver(draw).observe(box);
}
document.querySelectorAll('[data-chart]').forEach(root => {
  lineChart(root, JSON.parse(document.getElementById(root.dataset.chart).textContent));
});
"""


def _esc(v) -> str:
    return html.escape(str(v))


def _fmt(v, kind: str = "num") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)) or v is pd.NaT:
        return "–"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if not isinstance(v, (int, float, np.integer, np.floating)):
        return _esc(v)
    if kind == "pct":
        return f"{v * 100:.2f}%".replace("-", "−")
    if kind == "year":
        return f"{v:.0f}"
    if kind == "cr":
        return f"{'−' if v < 0 else ''}₹{abs(v) / 1e7:,.2f} cr"
    if kind == "int":
        return f"{v:,.0f}"
    return (f"{v:,.2f}" if abs(v) < 1e5 else f"{v:,.0f}").replace("-", "−")


_INT_METRICS = {
    "n_obs", "longest_drawdown_days", "n_fills", "n_orders", "orders_expired", "n_episodes", "cost_inr",
}
_PCT_METRICS = {
    "total_return", "cagr", "ann_vol", "ann_excess_return", "max_drawdown", "var_95_daily", "cvar_95_daily",
    "hit_rate_daily", "hit_rate_monthly", "best_month", "worst_month", "prob_sharpe_gt_0", "tracking_error",
    "alpha_ann", "avg_gross", "turnover_annual", "order_fill_rate", "avg_stale_share", "episode_win_rate",
    "episode_excess_win_rate", "avg_episode_return", "avg_episode_excess_return",
}


def _downsample(df: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    if len(df) <= max_points:
        return df
    step = math.ceil(len(df) / max_points)
    return pd.concat([df.iloc[::step], df.iloc[[-1]]]).drop_duplicates()


def _chart(cid: str, title: str, note: str, x, series, fmt: str, *, zero: bool = False, height: int = 240) -> str:
    spec = {"title": title, "x": [d.strftime("%Y-%m-%d") for d in x], "format": fmt, "zero": zero,
            "height": height, "series": [
                {"name": name, "slot": slot,
                 "values": [None if not np.isfinite(v) else round(float(v), 6) for v in values]}
                for name, slot, values in series]}
    payload = json.dumps(spec).replace("</", "<\\/")
    return (f'<div class="card" data-chart="{cid}"><h3>{_esc(title)}</h3><p class="note">{_esc(note)}</p>'
            f'<div class="legend"></div><div class="chart"><div class="tip"></div></div>'
            f'<script type="application/json" id="{cid}">{payload}</script></div>')


def _table(df: pd.DataFrame, formats: dict[str, str] | None = None, *, heat: list[str] | None = None) -> str:
    formats = formats or {}
    scale = 0.0
    if heat:
        vals = df[heat].to_numpy(dtype=float)
        scale = float(np.nanmax(np.abs(vals))) if np.isfinite(vals).any() else 0.0
    head = "".join(f"<th>{_esc(c)}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            style = ""
            if heat and c in heat and scale > 0 and isinstance(v, (float, int)) and math.isfinite(v):
                share = min(abs(v) / scale, 1.0) * 45
                tone = "--pos" if v >= 0 else "--neg"
                style = f' style="background: color-mix(in srgb, var({tone}) {share:.0f}%, var(--surface-1))"'
            cells.append(f"<td{style}>{_fmt(v, formats.get(c, 'num'))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def tearsheet(result: BacktestResult, benchmark: BacktestResult | None = None, *, episodes: pd.DataFrame | None = None,
              summary: pd.DataFrame | None = None, title: str | None = None, synthetic: bool = False) -> str:
    nav = result.nav.copy()
    eps = analytics.episodes(result.positions) if episodes is None else episodes
    summary = analytics.summary(result, benchmark) if summary is None else summary
    s = summary.set_index("metric")
    get = lambda k, col="total": s[col].get(k) if k in s.index else None
    title = title or f"{result.strategy.replace('_', ' ').capitalize()} backtest"
    nv = nav.set_index("date")
    start, end = nv.index[0], nv.index[-1]

    tiles = [
        ("CAGR", _fmt(get("cagr"), "pct")), ("Sharpe", _fmt(get("sharpe"))),
        ("Max drawdown", _fmt(get("max_drawdown"), "pct")),
        ("Hedged excess return", _fmt(get("ann_excess_return", "hedged"), "pct")),
        ("Hedged Sharpe", _fmt(get("sharpe", "hedged"))),
        ("Avg positions", _fmt(get("avg_positions"))), ("Turnover / yr", _fmt(get("turnover_annual"), "pct")),
        ("Costs / yr", f"{_fmt(get('cost_bps_annual'))} bps"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="label">{_esc(k)}</div><div class="value">{v}</div></div>'
                         for k, v in tiles)

    d = _downsample(nav)
    idx_series = [("Total return", 1, (d["nav"] / nav["nav"].iloc[0] * 100).to_numpy()),
                  ("Rates-hedged", 2, (d["nav_hedged"] / nav["nav_hedged"].iloc[0] * 100).to_numpy())]
    if benchmark is not None:
        b = benchmark.nav.set_index("date")["nav"].reindex(d["date"])
        idx_series.append((f"Benchmark: {benchmark.strategy.replace('_', ' ')}", 3,
                           (b / benchmark.nav["nav"].iloc[0] * 100).to_numpy()))
    charts = [_chart("c_nav", "Growth of 100", "Total return and the rates-hedged series (cash plus credit excess "
                     "return), indexed to 100 at the start.", d["date"], idx_series, "idx", height=280)]
    dd_total = analytics.drawdown(nv["nav"]).reindex(d["date"]).to_numpy()
    dd_hedged = analytics.drawdown(nv["nav_hedged"]).reindex(d["date"]).to_numpy()
    charts.append(_chart("c_dd", "Drawdown", "Fall from the running peak.", d["date"],
                         [("Total return", 1, dd_total), ("Rates-hedged", 2, dd_hedged)], "pct", zero=True))
    cum = analytics.cumulative_attribution(nav).set_index("date").reindex(d["date"])
    names = {"income": "Income", "roll": "Roll", "rates": "Rates", "spread": "Spread", "execution": "Execution",
             "cash": "Cash interest"}
    charts.append(_chart("c_attr", "Cumulative P&L by source",
                         "Income is coupon accrual; roll is pull-to-par at constant yield; rates and spread split "
                         "the yield move; execution is costs against the mark.", d["date"],
                         [(names[c], k + 1, cum[f"pnl_{c}"].to_numpy()) for k, c in
                          enumerate([*COMPONENTS, "cash"])], "cr", zero=True, height=280))
    small = [("c_gross", "Invested share of NAV", d["gross"].to_numpy(), "pct"),
             ("c_npos", "Positions held", d["n_positions"].to_numpy(dtype=float), "int"),
             ("c_dur", "Modified duration (years)", d["mod_duration"].to_numpy(), "num"),
             ("c_spr", "Average spread (bps)", d["spread_bps"].to_numpy(), "num")]
    small_html = "".join(_chart(cid, t, "Value-weighted across holdings." if cid in ("c_dur", "c_spr") else "",
                                d["date"], [(t, 1, v)], f, zero=cid in ("c_gross", "c_npos"), height=180)
                         for cid, t, v, f in small)

    stats = summary.copy()
    perf = stats[stats["hedged"].notna() | stats["metric"].isin(["start", "end", "years", "n_obs"])]
    trade = stats[stats["hedged"].isna() & ~stats["metric"].isin(perf["metric"])]

    def metric_rows(df, cols):
        out = []
        for _, r in df.iterrows():
            kind = "pct" if r["metric"] in _PCT_METRICS else "int" if r["metric"] in _INT_METRICS else "num"
            out.append("<tr><td>" + _esc(r["metric"].replace("_", " ")) + "</td>"
                       + "".join(f"<td>{_fmt(r[c], kind)}</td>" for c in cols) + "</tr>")
        return "".join(out)

    perf_html = (f'<div class="scroll"><table><thead><tr><th>Statistic</th><th>Total return</th>'
                 f'<th>Rates-hedged</th></tr></thead><tbody>{metric_rows(perf, ["total", "hedged"])}</tbody>'
                 '</table></div>')
    trade_html = (f'<div class="scroll"><table><thead><tr><th>Trading</th><th>Value</th></tr></thead>'
                  f'<tbody>{metric_rows(trade, ["total"])}</tbody></table></div>')
    attr = analytics.attribution(nav)
    attr_html = _table(attr.rename(columns={"component": "Component", "pnl_inr": "P&L", "share_of_total": "Share",
                                            "bps_per_year": "bps of NAV / yr"}),
                       {"P&L": "cr", "Share": "pct"})
    monthly = analytics.monthly_returns(nv["nav_hedged"])
    month_cols = [c for c in monthly.columns if c != "calendar_year"]
    monthly_html = _table(monthly.rename(columns={"calendar_year": "Year", "year": "Full year"}),
                          {"Year": "year", **{c: "pct" for c in [*month_cols, "Full year"]}},
                          heat=[c for c in month_cols if c != "year"])
    dds = analytics.drawdowns(nv["nav"], top=5).rename(columns={
        "peak": "Peak", "trough": "Trough", "recovery": "Recovered", "depth": "Depth",
        "days_to_trough": "Days to trough", "days_underwater": "Days under"})
    dd_html = _table(dds, {"Depth": "pct", "Days to trough": "int", "Days under": "int"})
    exp = analytics.exposures(result.positions, "rating_bucket")
    exp_html = ""
    if len(exp):
        avg = exp.drop(columns="date").mean().sort_values(ascending=False)
        exp_html = _table(pd.DataFrame({"Rating bucket": avg.index, "Average weight": avg.to_numpy()}),
                          {"Average weight": "pct"})
    ep_html = ""
    if len(eps):
        cols = {"isin": "ISIN", "issuer_id": "Issuer", "entry_date": "Entry", "last_date": "Last day",
                "status": "Status", "holding_days": "Days", "entry_spread_bps": "Entry spread (bps)",
                "exit_spread_bps": "Exit spread (bps)", "pnl": "P&L", "excess_pnl": "Excess P&L",
                "excess_return_on_peak_value": "Excess return"}
        best = eps.sort_values("excess_pnl", ascending=False).head(8)[list(cols)].rename(columns=cols)
        worst = eps.sort_values("excess_pnl").head(8)[list(cols)].rename(columns=cols)
        f = {"P&L": "cr", "Excess P&L": "cr", "Excess return": "pct", "Days": "int"}
        ep_html = (f"<h3>Best by excess P&amp;L</h3>{_table(best, f)}"
                   f"<h3 style='margin-top:14px'>Worst by excess P&amp;L</h3>{_table(worst, f)}")
    params = "; ".join(f"{k} = {v}" for k, v in result.params.items())
    banner = ('<div class="banner"><b>Synthetic data.</b> Every input behind this report was simulated by '
              '<code>cr_strat.synthetic</code> with planted effects. It shows what the engine produces, not how '
              'any strategy performs in the Indian market.</div>') if synthetic else ""
    notes = "".join(f"<li>{_esc(n)}</li>" for n in result.notes)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title><style>{_STYLE}</style></head>
<body><main class="viz-root">
<h1>{_esc(title)}</h1>
<p class="sub">{start:%d %b %Y} – {end:%d %b %Y} · starting capital {_fmt(result.config.initial_capital_inr, 'cr')} ·
rebalance {_esc(result.params.get('rebalance'))} · orders live {result.execution.lag_days} day(s) after the signal ·
{'fills only on days with a reported trade' if result.execution.trade_days_only else 'fills on any day at the mark'}</p>
{banner}
<div class="tiles">{tiles_html}</div>
{''.join(charts[:2])}
{charts[2]}
<h2>Portfolio</h2><div class="grid2">{small_html}</div>
<h2>Statistics</h2><div class="grid2"><div class="card">{perf_html}</div><div class="card">{trade_html}</div></div>
<h2>P&amp;L attribution</h2><div class="card">{attr_html}</div>
<h2>Monthly returns, rates-hedged</h2><div class="card">{monthly_html}</div>
<div class="grid2"><div><h2>Largest drawdowns</h2><div class="card">{dd_html}</div></div>
<div><h2>Average exposure by rating</h2>
<div class="card">{exp_html or '<p class="note">No holdings.</p>'}</div></div></div>
<h2>Holding episodes</h2><div class="card">{ep_html or '<p class="note">No positions were taken.</p>'}</div>
<p class="foot">Strategy parameters: {_esc(params)}</p>
{f'<ul class="foot">{notes}</ul>' if notes else ''}
</main><script>{_SCRIPT}</script></body></html>"""
