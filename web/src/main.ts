import "./styles.css";
import * as d3 from "d3";
import type {
  Bar,
  TickerResult,
  TickerData,
  LayerKey,
  Layer,
} from "./types";

// ── State ──────────────────────────────────────────────────────────────
let bars: Bar[] = [];
let result: TickerResult | null = null;
let sliderIndex = 0;
let tickers: string[] = [];
let currentTicker = "";
let isDark = false;

const layers: Layer[] = [
  { key: "sr",          label: "S/R",        active: true },
  { key: "geometric",   label: "Geometric",  active: true },
  { key: "crosses",     label: "GC / DC",    active: true },
  { key: "bb_squeeze",  label: "BB Squeeze", active: false },
  { key: "vol_climax",  label: "Vol Climax", active: false },
  { key: "divergences", label: "Diverg.",    active: false },
  { key: "gaps",        label: "Gaps",       active: false },
];

// ── Dimensions ─────────────────────────────────────────────────────────
const margin = { top: 20, right: 60, bottom: 20, left: 60 };
const volHeight = 80;
const volGap = 8;
let W = 0;
let H = 0;
let priceH = 0;

// ── Scales ─────────────────────────────────────────────────────────────
let xScale: d3.ScaleBand<string>;
let yPrice: d3.ScaleLinear<number, number>;
let yVol: d3.ScaleLinear<number, number>;

// ── Zoom state ─────────────────────────────────────────────────────────
let visibleRange: [number, number] = [0, 0];
const MIN_VISIBLE_BARS = 30;
let dateMode: "calendar" | "trading" = "calendar";

// ── Pre-computed indices (rebuilt on ticker load) ────────────────────────
let barByDate: Map<string, Bar> = new Map();
let barIdxByDate: Map<string, number> = new Map();
let candleByDate: Map<string, import("./types").CandlestickPattern[]> = new Map();
let signalsByDate: Map<string, import("./types").Signal[]> = new Map();
let gapsByDate: Map<string, import("./types").Gap[]> = new Map();
let divByDate: Map<string, import("./types").Divergence[]> = new Map(); // date → divs active on that date
let geoByEndDate: import("./types").GeometricPattern[] = []; // sorted by end_date
let srMerged: import("./types").SRZone[] = []; // deduplicated S/R zones, sorted by start_date
let panelCounts: number[][] = []; // panelCounts[rowIdx][barIdx] = cumulative count

// ── DOM refs ───────────────────────────────────────────────────────────
let svg: d3.Selection<SVGSVGElement, unknown, HTMLElement, unknown>;
let gPrice: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gVol: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gOverlay: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gXAxis: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gYAxis: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gYVolAxis: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gCrosshair: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;

// ── Utilities ─────────────────────────────────────────────────────────
function fmtDateDiff(a: Date, b: Date): string {
  let years = b.getFullYear() - a.getFullYear();
  let months = b.getMonth() - a.getMonth();
  let days = b.getDate() - a.getDate();
  if (days < 0) {
    months--;
    const prev = new Date(b.getFullYear(), b.getMonth(), 0).getDate();
    days += prev;
  }
  if (months < 0) { years--; months += 12; }
  const parts: string[] = [];
  if (years > 0) parts.push(`${years}y`);
  if (months > 0) parts.push(`${months}m`);
  if (days > 0 || parts.length === 0) parts.push(`${days}d`);
  return parts.join(" ");
}

// ── Theme-aware colors ─────────────────────────────────────────────────
function C(varName: string): string {
  // Tailwind v4 @theme maps to --color-* variables
  const mapped = varName.replace(/^--/, "--color-");
  return getComputedStyle(document.body).getPropertyValue(mapped).trim();
}

const PATTERN_COLORS: Record<string, string> = {
  double_top: "#f85149", double_bottom: "#3fb950",
  head_and_shoulders: "#f85149", inverse_head_and_shoulders: "#3fb950",
  triple_top: "#da3633", triple_bottom: "#2ea043",
  ascending_triangle: "#3fb950", descending_triangle: "#f85149",
  symmetrical_triangle: "#d29922",
  rising_wedge: "#f85149", falling_wedge: "#3fb950",
  ascending_channel: "#3fb950", descending_channel: "#f85149",
  horizontal_channel: "#d29922",
  broadening_formation: "#bc8cff",
  bull_flag: "#3fb950", bear_flag: "#f85149",
  bull_pennant: "#3fb950", bear_pennant: "#f85149",
};

// ── Data loading ───────────────────────────────────────────────────────
async function fetchTickers(): Promise<string[]> {
  const resp = await fetch("/api/tickers");
  return resp.json();
}

async function fetchTickerData(ticker: string): Promise<TickerData> {
  const resp = await fetch(`/api/ticker/${ticker}`);
  return resp.json();
}

// ── Init ───────────────────────────────────────────────────────────────
async function init() {
  tickers = await fetchTickers();
  if (tickers.length === 0) return;

  const select = d3.select<HTMLSelectElement, unknown>("#ticker-select");
  select.selectAll("option").data(tickers).join("option")
    .attr("value", (d) => d).text((d) => d);
  select.on("change", async function () { await loadTicker(this.value); });

  renderLayerPanel();
  initNowDrag();

  await loadTicker(tickers[0]);

  window.addEventListener("resize", () => {
    if (bars.length > 0) { setupSVG(); draw(); }
  });
}

function precompute() {
  if (!result || bars.length === 0) return;

  // Bar index by date
  barByDate = new Map(bars.map(b => [b.date, b]));
  barIdxByDate = new Map(bars.map((b, i) => [b.date, i]));

  // Candlestick patterns grouped by date
  candleByDate = new Map();
  for (const p of result.candlestick_patterns ?? []) {
    let arr = candleByDate.get(p.date);
    if (!arr) { arr = []; candleByDate.set(p.date, arr); }
    arr.push(p);
  }

  // Signals grouped by date
  signalsByDate = new Map();
  for (const s of result.signals ?? []) {
    let arr = signalsByDate.get(s.date);
    if (!arr) { arr = []; signalsByDate.set(s.date, arr); }
    arr.push(s);
  }

  // Gaps grouped by date
  gapsByDate = new Map();
  for (const g of result.gaps ?? []) {
    let arr = gapsByDate.get(g.date);
    if (!arr) { arr = []; gapsByDate.set(g.date, arr); }
    arr.push(g);
  }

  // Geometric patterns sorted by end_date
  geoByEndDate = [...(result.geometric_patterns ?? [])].sort((a, b) => a.end_date.localeCompare(b.end_date));

  // S/R: merge rolling_sr + density_sr, dedup nearby levels, keep highest touches
  srMerged = [];
  const allSR = [
    ...(result.rolling_sr ?? []),
    ...(result.density_sr ?? []),
  ].sort((a, b) => b.touches - a.touches);
  for (const z of allSR) {
    const existing = srMerged.find(e =>
      e.type === z.type && Math.abs(e.level - z.level) / z.level < 0.015);
    if (!existing) {
      srMerged.push({ ...z });
    }
  }
  srMerged.sort((a, b) => a.start_date.localeCompare(b.start_date));

  // Divergences: expand into per-date map for each date in their range
  divByDate = new Map();
  for (const d of result.divergences ?? []) {
    // Just store start and end; we'll check range during lookup
    for (const date of [d.start_date, d.end_date]) {
      let arr = divByDate.get(date);
      if (!arr) { arr = []; divByDate.set(date, arr); }
      // Avoid duplicates
      if (!arr.includes(d)) arr.push(d);
    }
  }

  // Pre-compute cumulative counts for each panel row
  panelCounts = [];
  for (let r = 0; r < panelRows.length; r++) {
    const counts = new Array(bars.length);
    let running = 0;
    const fn = panelRows[r].countFn;
    for (let i = 0; i < bars.length; i++) {
      running = fn(bars[i].date);
      counts[i] = running;
    }
    panelCounts.push(counts);
  }
}

async function loadTicker(ticker: string) {
  currentTicker = ticker;
  d3.select("#ticker-select").property("value", ticker);

  const data = await fetchTickerData(ticker);
  bars = data.bars;
  result = data.result;

  renderLayerPanel();
  precompute();

  visibleRange = [0, bars.length - 1];
  sliderIndex = Math.floor(bars.length * 0.75);

  const zoomSlider = document.getElementById("zoom-slider") as HTMLInputElement;
  zoomSlider.value = "0";
  document.getElementById("zoom-label")!.textContent = "100%";
  (document.getElementById("nav-slider") as HTMLInputElement).value = "1000";

  setupSVG();
  draw();
  positionNowHandle();
}

function applyZoom() {
  const zoomVal = parseInt((document.getElementById("zoom-slider") as HTMLInputElement).value);
  const total = bars.length;
  const visibleCount = Math.round(total - (zoomVal / 100) * (total - MIN_VISIBLE_BARS));

  // Keep current center
  const currentCenter = Math.floor((visibleRange[0] + visibleRange[1]) / 2);
  const half = Math.floor(visibleCount / 2);
  let start = currentCenter - half;
  let end = start + visibleCount - 1;
  if (start < 0) { end -= start; start = 0; }
  if (end >= total) { start -= (end - total + 1); end = total - 1; }
  start = Math.max(0, start);

  visibleRange = [start, end];
  document.getElementById("zoom-label")!.textContent = `${Math.round((visibleCount / total) * 100)}%`;
  draw();
}

/** Pan the visible window by a number of bars (positive = right, negative = left) */
function panWindow(delta: number) {
  const total = bars.length;
  const visibleCount = visibleRange[1] - visibleRange[0] + 1;
  let start = visibleRange[0] + delta;
  start = Math.max(0, Math.min(total - visibleCount, start));
  visibleRange = [start, start + visibleCount - 1];
  syncNavSlider();
  draw();
}

function syncNavSlider() {
  const total = bars.length;
  const visibleCount = visibleRange[1] - visibleRange[0] + 1;
  const maxStart = total - visibleCount;
  const navVal = maxStart > 0 ? Math.round((visibleRange[0] / maxStart) * 1000) : 0;
  (document.getElementById("nav-slider") as HTMLInputElement).value = String(Math.max(0, Math.min(1000, navVal)));
}

/** Zoom to a specific visible count, centering on the NOW date. Syncs zoom slider. */
function zoomCenteredOnNow(newVisibleCount: number) {
  const total = bars.length;
  const clamped = Math.max(MIN_VISIBLE_BARS, Math.min(total, newVisibleCount));

  // Center the window on sliderIndex
  const half = Math.floor(clamped / 2);
  let start = sliderIndex - half;
  let end = start + clamped - 1;

  // Clamp to bounds
  if (start < 0) { end -= start; start = 0; }
  if (end >= total) { start -= (end - total + 1); end = total - 1; }
  start = Math.max(0, start);

  visibleRange = [start, end];

  // Sync the zoom slider
  const zoomVal = Math.round(((total - clamped) / (total - MIN_VISIBLE_BARS)) * 100);
  const zoomSlider = document.getElementById("zoom-slider") as HTMLInputElement;
  zoomSlider.value = String(Math.max(0, Math.min(100, zoomVal)));

  document.getElementById("zoom-label")!.textContent = `${Math.round((clamped / total) * 100)}%`;
  syncNavSlider();
  draw();
}

// ── Layer panel (table with icon, name, count, eye toggle) ─────────────

// Track which individual candlestick pattern types are enabled
// Start with the most useful reversal/strong patterns on, noisy ones off
// Direction classification for icons
const CANDLE_BULLISH = [
  "HAMMER", "INVERTEDHAMMER", "MORNINGSTAR", "MORNINGDOJISTAR",
  "3WHITESOLDIERS", "PIERCING", "LADDERBOTTOM",
  "TAKURI", "MATCHINGLOW", "HOMINGPIGEON", "UNIQUE3RIVER",
  "STICKSANDWICH",
];
const CANDLE_BEARISH = [
  "SHOOTINGSTAR", "HANGINGMAN", "EVENINGSTAR", "EVENINGDOJISTAR",
  "3BLACKCROWS", "DARKCLOUDCOVER",
  "ADVANCEBLOCK", "UPSIDEGAP2CROWS", "2CROWS", "STALLEDPATTERN",
];
// All others (ENGULFING, HARAMI, HARAMICROSS, 3INSIDE, 3OUTSIDE, DOJISTAR, COUNTERATTACK,
// DOJI variants, HIKKAKE, BELTHOLD, MARUBOZU, etc.) can be either direction → "both"

const CANDLE_REVERSAL = [
  "ENGULFING", "HAMMER", "SHOOTINGSTAR", "HANGINGMAN", "INVERTEDHAMMER",
  "MORNINGSTAR", "EVENINGSTAR", "MORNINGDOJISTAR", "EVENINGDOJISTAR",
  "3WHITESOLDIERS", "3BLACKCROWS", "PIERCING", "DARKCLOUDCOVER",
  "HARAMI", "HARAMICROSS", "3INSIDE", "3OUTSIDE",
  "DOJISTAR", "COUNTERATTACK", "LADDERBOTTOM",
];
const CANDLE_CONTINUATION = [
  "DOJI", "LONGLEGGEDDOJI", "DRAGONFLYDOJI", "GRAVESTONEDOJI", "RICKSHAWMAN",
  "SPINNINGTOP", "HIGHWAVE", "LONGLINE", "SHORTLINE", "MARUBOZU",
  "CLOSINGMARUBOZU", "BELTHOLD", "HIKKAKE", "HIKKAKEMOD",
  "TAKURI", "MATCHINGLOW", "HOMINGPIGEON", "THRUSTING",
  "XSIDEGAP3METHODS", "ADVANCEBLOCK", "SEPARATINGLINES",
  "ONNECK", "INNECK", "GAPSIDESIDEWHITE", "TASUKIGAP",
  "STALLEDPATTERN", "UNIQUE3RIVER", "UPSIDEGAP2CROWS",
  "STICKSANDWICH", "3LINESTRIKE", "2CROWS", "RISEFALL3METHODS", "TRISTAR",
];
const ALL_CANDLE_TYPES = [...CANDLE_REVERSAL, ...CANDLE_CONTINUATION];
const candleEnabled = new Set<string>(CANDLE_REVERSAL); // start with reversal on

// Track which individual geometric pattern types are enabled
const geoEnabled = new Set<string>([
  "double_top", "double_bottom",
  "head_and_shoulders", "inverse_head_and_shoulders",
  "triple_top", "triple_bottom",
  "ascending_triangle", "descending_triangle", "symmetrical_triangle",
  "rising_wedge", "falling_wedge",
  "ascending_channel", "descending_channel", "horizontal_channel",
  "broadening_formation",
  "bull_flag", "bear_flag", "bull_pennant", "bear_pennant",
]);

function geoIcon(color: string): string {
  return `<svg width="14" height="14"><circle cx="7" cy="7" r="4" fill="${color}" stroke="#0d1117" stroke-width="1"/></svg>`;
}

function prettyName(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

interface PanelRow {
  icon: string;
  label: string;
  layerKey?: LayerKey;
  geoKey?: string;
  candleKey?: string;
  countFn: (d: string) => number;
}

function buildPanelRows(): PanelRow[] {
  const rows: PanelRow[] = [
    // ── S/R ──
    { icon: `<svg width="14" height="14"><line x1="0" y1="7" x2="14" y2="7" stroke="#58a6ff" stroke-width="2"/></svg>`,
      label: "Support (active)", layerKey: "sr",
      countFn: (d) => result?.rolling_sr.filter(z => z.type === "support" && z.start_date <= d && !(z.broken && z.broken_date && z.broken_date <= d)).length ?? 0 },
    { icon: `<svg width="14" height="14"><line x1="0" y1="7" x2="14" y2="7" stroke="#e3b341" stroke-width="2"/></svg>`,
      label: "Resistance (active)", layerKey: "sr",
      countFn: (d) => result?.rolling_sr.filter(z => z.type === "resistance" && z.start_date <= d && !(z.broken && z.broken_date && z.broken_date <= d)).length ?? 0 },
  ];

  // ── Geometric: one row per pattern type ──
  const geoTypes = [
    "double_top", "double_bottom",
    "head_and_shoulders", "inverse_head_and_shoulders",
    "triple_top", "triple_bottom",
    "ascending_triangle", "descending_triangle", "symmetrical_triangle",
    "rising_wedge", "falling_wedge",
    "ascending_channel", "descending_channel", "horizontal_channel",
    "broadening_formation",
    "bull_flag", "bear_flag", "bull_pennant", "bear_pennant",
  ];
  for (const gt of geoTypes) {
    const color = PATTERN_COLORS[gt] ?? "#8b949e";
    rows.push({
      icon: geoIcon(color),
      label: prettyName(gt),
      geoKey: gt,
      countFn: (d) => result?.geometric_patterns.filter(p => p.pattern === gt && p.end_date <= d).length ?? 0,
    });
  }

  // ── Signals ──
  rows.push(
    { icon: `<svg width="14" height="14"><polygon points="7,1 8.8,5.2 13.4,5.5 9.9,8.5 11,13 7,10.5 3,13 4.1,8.5 0.6,5.5 5.2,5.2" fill="#e3b341"/></svg>`,
      label: "Golden Cross", layerKey: "crosses",
      countFn: (d) => result?.signals.filter(s => s.pattern === "golden_cross" && s.date <= d).length ?? 0 },
    { icon: `<svg width="14" height="14"><line x1="3" y1="3" x2="11" y2="11" stroke="#da3633" stroke-width="2.5"/><line x1="11" y1="3" x2="3" y2="11" stroke="#da3633" stroke-width="2.5"/></svg>`,
      label: "Death Cross", layerKey: "crosses",
      countFn: (d) => result?.signals.filter(s => s.pattern === "death_cross" && s.date <= d).length ?? 0 },
    { icon: `<svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="#bc8cff" stroke-width="2"/></svg>`,
      label: "BB Squeeze", layerKey: "bb_squeeze",
      countFn: (d) => result?.signals.filter(s => s.pattern === "bollinger_squeeze" && s.date <= d).length ?? 0 },
    { icon: `<svg width="14" height="14"><rect x="3" y="3" width="8" height="8" fill="#e67e22" transform="rotate(45,7,7)"/></svg>`,
      label: "Volume Climax", layerKey: "vol_climax",
      countFn: (d) => result?.signals.filter(s => s.pattern === "volume_climax" && s.date <= d).length ?? 0 },
  );

  // ── Candlestick: one row per pattern type ──
  for (const ct of ALL_CANDLE_TYPES) {
    const dir = CANDLE_BULLISH.includes(ct) ? "bull" : CANDLE_BEARISH.includes(ct) ? "bear" : "both";
    let icon: string;
    if (dir === "bull") {
      icon = `<svg width="14" height="14"><polygon points="7,3 2,11 12,11" fill="#3fb950" opacity="0.8"/></svg>`;
    } else if (dir === "bear") {
      icon = `<svg width="14" height="14"><polygon points="7,11 2,3 12,3" fill="#f85149" opacity="0.8"/></svg>`;
    } else {
      icon = `<svg width="14" height="14"><polygon points="7,2 12,7 7,12 2,7" fill="#e3b341" opacity="0.7"/></svg>`;
    }
    rows.push({
      icon,
      label: prettyName(ct.toLowerCase()),
      candleKey: ct,
      countFn: (d) => result?.candlestick_patterns.filter(p => p.pattern === ct && p.date <= d).length ?? 0,
    });
  }

  // ── Divergences ──
  rows.push(
    { icon: `<svg width="14" height="14"><line x1="0" y1="7" x2="14" y2="7" stroke="#3fb950" stroke-width="2" stroke-dasharray="3,2"/></svg>`,
      label: "Bullish divergence", layerKey: "divergences",
      countFn: (d) => result?.divergences.filter(x => x.direction === "bullish" && x.end_date <= d).length ?? 0 },
    { icon: `<svg width="14" height="14"><line x1="0" y1="7" x2="14" y2="7" stroke="#f85149" stroke-width="2" stroke-dasharray="3,2"/></svg>`,
      label: "Bearish divergence", layerKey: "divergences",
      countFn: (d) => result?.divergences.filter(x => x.direction === "bearish" && x.end_date <= d).length ?? 0 },
  );

  // ── Gaps ──
  rows.push(
    { icon: `<svg width="14" height="14"><rect x="2" y="4" width="10" height="6" fill="#3fb950" opacity="0.3" rx="1"/></svg>`,
      label: "Gaps", layerKey: "gaps",
      countFn: (d) => result?.gaps.filter(g => g.date <= d).length ?? 0 },
  );

  return rows;
}

let panelRows: PanelRow[] = [];

function renderLayerPanel() {
  panelRows = buildPanelRows();
  const el = document.getElementById("layer-panel")!;

  const headerRow = `<tr><th class="lp-toggle"><button class="eye-btn col-toggle" title="Toggle column">&#x1F441;</button></th><th class="lp-icon"></th><th>Signal</th><th style="text-align:right">#</th></tr>`;

  function rowHtml(row: PanelRow, i: number): string {
    let off = false;
    let toggleAttr = "";
    if (row.layerKey) {
      const layer = layers.find(l => l.key === row.layerKey);
      off = layer ? !layer.active : false;
      toggleAttr = `data-layer="${row.layerKey}"`;
    } else if (row.geoKey) {
      off = !geoEnabled.has(row.geoKey);
      toggleAttr = `data-geo="${row.geoKey}"`;
    } else if (row.candleKey) {
      off = !candleEnabled.has(row.candleKey);
      toggleAttr = `data-candle="${row.candleKey}"`;
    }

    return `<tr class="${off ? "layer-off" : ""}" data-row="${i}" ${toggleAttr}>
      <td class="lp-toggle"><button class="eye-btn ${off ? "off" : ""}" ${toggleAttr} title="Toggle">&#x1F441;</button></td>
      <td class="lp-icon">${row.icon}</td>
      <td class="lp-name">${row.label}</td>
      <td class="lp-count" data-count="${i}">—</td>
    </tr>`;
  }

  // Split into two columns
  const mid = Math.ceil(panelRows.length / 2);
  const leftRows = panelRows.slice(0, mid).map((r, i) => rowHtml(r, i)).join("");
  const rightRows = panelRows.slice(mid).map((r, i) => rowHtml(r, i + mid)).join("");

  el.innerHTML =
    `<table>${headerRow}${leftRows}</table>` +
    `<table>${headerRow}${rightRows}</table>`;

  // Column-level toggle (eye in header)
  el.querySelectorAll(".col-toggle").forEach((btn) => {
    let colOn = true;
    btn.addEventListener("click", () => {
      colOn = !colOn;
      btn.classList.toggle("off", !colOn);
      const table = btn.closest("table")!;
      table.querySelectorAll("tr[data-row]").forEach(tr => {
        const rowEl = tr as HTMLElement;
        const rowBtn = rowEl.querySelector(".eye-btn:not(.col-toggle)") as HTMLElement;
        const layerKey = rowBtn?.dataset.layer as LayerKey | undefined;
        const geoKey = rowBtn?.dataset.geo;
        const candleKey = rowBtn?.dataset.candle;

        if (layerKey) {
          const layer = layers.find(l => l.key === layerKey)!;
          layer.active = colOn;
          // Update all rows sharing this layerKey across both tables
          el.querySelectorAll(`tr[data-layer="${layerKey}"]`).forEach(shared => {
            shared.classList.toggle("layer-off", !colOn);
            shared.querySelector(".eye-btn")!.classList.toggle("off", !colOn);
          });
        } else if (geoKey) {
          colOn ? geoEnabled.add(geoKey) : geoEnabled.delete(geoKey);
        } else if (candleKey) {
          colOn ? candleEnabled.add(candleKey) : candleEnabled.delete(candleKey);
        }

        rowEl.classList.toggle("layer-off", !colOn);
        if (rowBtn) rowBtn.classList.toggle("off", !colOn);
      });
      draw();
    });
  });

  // Row-level click handlers
  el.querySelectorAll(".eye-btn:not(.col-toggle)").forEach((btn) => {
    btn.addEventListener("click", () => {
      const btnEl = btn as HTMLElement;
      const layerKey = btnEl.dataset.layer as LayerKey | undefined;
      const geoKey = btnEl.dataset.geo;
      const candleKey = btnEl.dataset.candle;

      if (layerKey) {
        const layer = layers.find(l => l.key === layerKey)!;
        layer.active = !layer.active;
        el.querySelectorAll(`tr[data-layer="${layerKey}"]`).forEach(tr => {
          tr.classList.toggle("layer-off", !layer.active);
          tr.querySelector(".eye-btn")!.classList.toggle("off", !layer.active);
        });
      } else if (geoKey) {
        geoEnabled.has(geoKey) ? geoEnabled.delete(geoKey) : geoEnabled.add(geoKey);
        const tr = btnEl.closest("tr")!;
        tr.classList.toggle("layer-off", !geoEnabled.has(geoKey));
        btnEl.classList.toggle("off", !geoEnabled.has(geoKey));
      } else if (candleKey) {
        candleEnabled.has(candleKey) ? candleEnabled.delete(candleKey) : candleEnabled.add(candleKey);
        const tr = btnEl.closest("tr")!;
        tr.classList.toggle("layer-off", !candleEnabled.has(candleKey));
        btnEl.classList.toggle("off", !candleEnabled.has(candleKey));
      }

      draw();
    });
  });
}

function updateLayerPanel(_sliderDate: string) {
  const idx = sliderIndex;
  panelRows.forEach((_row, i) => {
    const cell = document.querySelector(`[data-count="${i}"]`);
    if (cell) {
      cell.textContent = panelCounts[i] ? String(panelCounts[i][idx] ?? 0) : "—";
    }
  });
}

function isLayerActive(key: LayerKey): boolean {
  return layers.find((l) => l.key === key)?.active ?? false;
}

function isGeoEnabled(patternName: string): boolean {
  return geoEnabled.has(patternName);
}

// ── SVG setup ──────────────────────────────────────────────────────────
function setupSVG() {
  const container = document.getElementById("chart-container")!;
  W = container.clientWidth;
  H = Math.max(500, Math.min(window.innerHeight - 200, 700));
  priceH = H - margin.top - margin.bottom - volHeight - volGap;

  d3.select("#chart-container").select("svg").remove();

  svg = d3.select("#chart-container").append("svg")
    .attr("width", W).attr("height", H);

  svg.append("defs").append("clipPath").attr("id", "clip-price")
    .append("rect").attr("width", W - margin.left - margin.right).attr("height", priceH);
  svg.select("defs").append("clipPath").attr("id", "clip-vol")
    .append("rect").attr("width", W - margin.left - margin.right).attr("height", volHeight);

  gPrice = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gOverlay = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gVol = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top + priceH + volGap})`)
    .attr("clip-path", "url(#clip-vol)");
  gXAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${margin.left},${margin.top + priceH + volGap + volHeight})`);
  gYAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${W - margin.right},${margin.top})`);
  gYVolAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${W - margin.right},${margin.top + priceH + volGap})`);
  gCrosshair = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`);

  svg.append("rect").attr("class", "mouse-rect")
    .attr("x", margin.left).attr("y", margin.top)
    .attr("width", W - margin.left - margin.right)
    .attr("height", priceH + volGap + volHeight)
    .attr("fill", "transparent")
    .on("mousemove", onMouseMove)
    .on("mouseleave", onMouseLeave);

  // Scroll wheel zoom — centered on NOW date
  svg.on("wheel", (event: WheelEvent) => {
    event.preventDefault();
    const currentVisible = visibleRange[1] - visibleRange[0] + 1;
    const factor = event.deltaY > 0 ? 1.15 : 0.87; // scroll down = zoom out, up = zoom in
    const newCount = Math.round(currentVisible * factor);
    zoomCenteredOnNow(newCount);
  }, { passive: false } as any);
}

// ── Draw ───────────────────────────────────────────────────────────────
function draw() {
  if (bars.length === 0) return;

  const [vi0, vi1] = visibleRange;
  const visible = bars.slice(vi0, vi1 + 1);
  const sliderDate = bars[sliderIndex]?.date ?? "";
  const chartW = W - margin.left - margin.right;

  const upColor = C("--candle-up");
  const downColor = C("--candle-down");
  const gridColor = C("--grid");
  const axisColor = C("--border");
  const textDim = C("--text-dim");
  const accentColor = C("--accent");
  const bgColor = C("--chart-bg");

  // OHLC stats for NOW date
  const nowBar = bars[sliderIndex];
  if (nowBar) {
    const up = nowBar.close >= nowBar.open;
    const cls = up ? "ohlc-up" : "ohlc-down";
    const chg = ((nowBar.close - nowBar.open) / nowBar.open * 100).toFixed(2);
    const sign = up ? "+" : "";
    document.getElementById("now-ohlc")!.innerHTML =
      `<span class="now-date">${sliderDate.slice(0, 10)}</span>` +
      `<span class="ohlc-item"><span class="ohlc-label">O</span> <span class="${cls}">${nowBar.open.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">H</span> <span class="${cls}">${nowBar.high.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">L</span> <span class="${cls}">${nowBar.low.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">C</span> <span class="${cls}">${nowBar.close.toFixed(2)}</span> <span class="${cls}">(${sign}${chg}%)</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">Vol</span> <span style="color:var(--text)">${d3.format(",")(nowBar.volume)}</span></span>`;
  }

  xScale = d3.scaleBand<string>()
    .domain(visible.map((b) => b.date))
    .range([0, chartW])
    .padding(0.3);

  const pLow = d3.min(visible, (b) => b.low)! * 0.995;
  const pHigh = d3.max(visible, (b) => b.high)! * 1.005;
  yPrice = d3.scaleLinear().domain([pLow, pHigh]).range([priceH, 0]);

  const vMax = d3.max(visible, (b) => b.volume)! * 1.1;
  yVol = d3.scaleLinear().domain([0, vMax]).range([volHeight, 0]);

  // Axes
  const nowIdx = sliderIndex - vi0; // index of NOW within visible array
  const xAxis = d3.axisBottom(xScale)
    .tickValues(xScale.domain().filter((_, i) => i % Math.max(1, Math.floor(visible.length / 20)) === 0))
    .tickFormat((d, i, nodes) => {
      if (dateMode === "trading") {
        const barGlobalIdx = barIdxByDate.get(d) ?? 0;
        const delta = barGlobalIdx - sliderIndex;
        return delta === 0 ? "0" : delta > 0 ? `+${delta}` : String(delta);
      }
      const [y, m, day] = d.split("-");
      return `${day}/${m}/${y.slice(2)}`;
    });
  gXAxis.call(xAxis);
  gXAxis.selectAll("text").attr("fill", textDim);
  gXAxis.selectAll("line, path").attr("stroke", axisColor);

  gYAxis.call(d3.axisRight(yPrice).ticks(8).tickFormat(d3.format(",.2f")));
  gYAxis.selectAll("text").attr("fill", textDim);
  gYAxis.selectAll("line, path").attr("stroke", axisColor);

  gYVolAxis.call(d3.axisRight(yVol).ticks(3).tickFormat((d) => {
    const n = d as number;
    return n >= 1e6 ? `${(n / 1e6).toFixed(0)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}K` : String(n);
  }));
  gYVolAxis.selectAll("text").attr("fill", textDim);
  gYVolAxis.selectAll("line, path").attr("stroke", axisColor);

  // Grid
  gPrice.selectAll(".grid").remove();
  gPrice.append("g").attr("class", "grid").selectAll("line")
    .data(yPrice.ticks(8)).join("line")
    .attr("x1", 0).attr("x2", chartW)
    .attr("y1", (d) => yPrice(d)).attr("y2", (d) => yPrice(d))
    .attr("stroke", gridColor).attr("opacity", 0.4);

  // Candlesticks
  const candleW = xScale.bandwidth();
  gPrice.selectAll(".candle").remove();
  const candles = gPrice.selectAll(".candle").data(visible).join("g").attr("class", "candle");

  candles.append("line")
    .attr("x1", (d) => (xScale(d.date) ?? 0) + candleW / 2)
    .attr("x2", (d) => (xScale(d.date) ?? 0) + candleW / 2)
    .attr("y1", (d) => yPrice(d.high))
    .attr("y2", (d) => yPrice(d.low))
    .attr("stroke", (d) => {
      const up = d.close >= d.open;
      const future = d.date > sliderDate;
      const base = up ? upColor : downColor;
      return future ? base.replace(")", ",0.25)").replace("rgb", "rgba") : base;
    })
    .attr("stroke-width", 1)
    .attr("opacity", (d) => d.date > sliderDate ? 0.35 : 1);

  candles.append("rect")
    .attr("x", (d) => xScale(d.date) ?? 0)
    .attr("y", (d) => yPrice(Math.max(d.open, d.close)))
    .attr("width", candleW)
    .attr("height", (d) => Math.max(1, Math.abs(yPrice(d.open) - yPrice(d.close))))
    .attr("fill", (d) => d.close >= d.open ? upColor : downColor)
    .attr("stroke", (d) => d.close >= d.open ? upColor : downColor)
    .attr("opacity", (d) => d.date > sliderDate ? 0.25 : 1);

  // Volume
  gVol.selectAll(".vol-bar").remove();
  gVol.selectAll(".vol-bar").data(visible).join("rect")
    .attr("class", "vol-bar")
    .attr("x", (d) => xScale(d.date) ?? 0)
    .attr("y", (d) => yVol(d.volume))
    .attr("width", candleW)
    .attr("height", (d) => volHeight - yVol(d.volume))
    .attr("fill", (d) => d.close >= d.open ? upColor : downColor)
    .attr("opacity", (d) => d.date > sliderDate ? 0.1 : 0.4);

  // Day count labels
  if (dateMode === "trading") {
    const daysBehind = sliderIndex - vi0;
    const daysForward = vi1 - sliderIndex;
    document.getElementById("days-behind")!.textContent = `(${daysBehind} trading days)`;
    document.getElementById("days-forward")!.textContent = `(${daysForward} trading days)`;
  } else {
    const firstDate = new Date(visible[0].date);
    const nowDate = new Date(sliderDate);
    const lastDate = new Date(visible[visible.length - 1].date);
    document.getElementById("days-behind")!.textContent = `(${fmtDateDiff(firstDate, nowDate)})`;
    document.getElementById("days-forward")!.textContent = `(${fmtDateDiff(nowDate, lastDate)})`;
  }

  drawOverlays(visible, sliderDate);
  positionNowHandle();
  updateLayerPanel(sliderDate);
}

// ── Overlays ───────────────────────────────────────────────────────────
function drawOverlays(visible: Bar[], sliderDate: string) {
  gOverlay.selectAll("*").remove();
  if (!result) return;

  const visibleDates = new Set(visible.map((b) => b.date));
  const candleW = xScale.bandwidth();
  const xPos = (date: string) => (xScale(date) ?? 0) + candleW / 2;

  // S/R — uses pre-merged srMerged
  // Broken zones linger for 60 bars after break date (dashed, fading)
  const SR_LINGER_BARS = 60;
  if (isLayerActive("sr")) {
    for (const z of srMerged) {
      if (z.start_date > sliderDate) continue;
      const wasBroken = z.broken && z.broken_date && z.broken_date <= sliderDate;

      let drawEnd: string;
      let lingerFade = 1;
      if (wasBroken) {
        const breakIdx = barIdxByDate.get(z.broken_date!);
        if (breakIdx === undefined) continue;
        const lingerEndIdx = Math.min(bars.length - 1, breakIdx + SR_LINGER_BARS);
        const lingerEndDate = bars[lingerEndIdx].date;
        if (lingerEndDate < visible[0].date) continue;
        drawEnd = lingerEndDate < sliderDate ? lingerEndDate : sliderDate;
        const elapsed = Math.max(0, sliderIndex - breakIdx);
        if (elapsed > SR_LINGER_BARS) continue;
        lingerFade = Math.max(0.15, 1 - elapsed / SR_LINGER_BARS);
      } else {
        drawEnd = sliderDate;
      }

      if (drawEnd < visible[0].date || z.start_date > visible[visible.length - 1].date) continue;
      const level = z.level;
      if (level < yPrice.domain()[0] || level > yPrice.domain()[1]) continue;

      const x1 = xPos(z.start_date < visible[0].date ? visible[0].date : z.start_date);
      const x2 = xPos(drawEnd > visible[visible.length - 1].date ? visible[visible.length - 1].date : drawEnd);
      const isSupport = z.type === "support";
      const baseOpacity = Math.min(0.8, 0.25 + z.touches * 0.1);

      gOverlay.append("line")
        .attr("x1", x1).attr("x2", x2)
        .attr("y1", yPrice(level)).attr("y2", yPrice(level))
        .attr("stroke", isSupport ? "#58a6ff" : "#e3b341")
        .attr("stroke-width", Math.min(3, 0.8 + z.touches * 0.4))
        .attr("stroke-dasharray", wasBroken ? "4,3" : "none")
        .attr("opacity", baseOpacity * lingerFade);
    }
  }

  // Geometric — uses pre-sorted geoByEndDate
  if (isLayerActive("geometric")) {
    const pats: typeof geoByEndDate = [];
    for (const p of geoByEndDate) {
      if (p.end_date > sliderDate) break; // sorted by end_date, so all remaining are after
      if (p.confidence >= 0.5 && isGeoEnabled(p.pattern)) pats.push(p);
    }
    pats.sort((a, b) => b.confidence - a.confidence);
    if (pats.length > 40) pats.length = 40;

    for (const pat of pats) {
      const pivots = pat.pivots.filter((p) => visibleDates.has(p.date));
      if (pivots.length < 2) continue;
      const color = PATTERN_COLORS[pat.pattern] ?? "#8b949e";
      const bg = C("--chart-bg");

      const line = d3.line<{ date: string; price: number }>()
        .x((d) => xPos(d.date)).y((d) => yPrice(d.price));

      gOverlay.append("path").datum(pivots).attr("d", line)
        .attr("stroke", color).attr("stroke-width", 2).attr("stroke-dasharray", "5,3")
        .attr("fill", "none").attr("opacity", 0.7);

      gOverlay.selectAll(null).data(pivots).join("circle")
        .attr("cx", (d) => xPos(d.date)).attr("cy", (d) => yPrice(d.price))
        .attr("r", 4).attr("fill", color).attr("stroke", bg).attr("stroke-width", 1.5)
        .append("title")
        .text((d) => `${pat.pattern.replace(/_/g, " ")} — ${d.role}: ${d.price.toFixed(2)} (conf: ${pat.confidence})`);
    }
  }

  // Candlestick patterns — uses candleByDate index
  if (candleEnabled.size > 0) {
    for (const date of visibleDates) {
      if (date > sliderDate) continue;
      const pats = candleByDate.get(date);
      if (!pats) continue;
      for (const p of pats) {
        if (!candleEnabled.has(p.pattern)) continue;
        const bar = barByDate.get(p.date);
        if (!bar) continue;
        const isBull = p.direction === "bullish";
        const cx = xPos(p.date);
        const cy = yPrice(isBull ? bar.low * 0.99 : bar.high * 1.01);

        gOverlay.append("polygon")
          .attr("points", isBull
            ? `${cx},${cy - 6} ${cx - 4},${cy + 2} ${cx + 4},${cy + 2}`
            : `${cx},${cy + 6} ${cx - 4},${cy - 2} ${cx + 4},${cy - 2}`)
          .attr("fill", isBull ? C("--candle-up") : C("--candle-down"))
          .attr("opacity", 0.6)
          .append("title").text(prettyName(p.pattern.toLowerCase()));
      }
    }
  }

  // Signals (GC/DC, BB Squeeze, Vol Climax) — uses signalsByDate index
  const drawCrosses = isLayerActive("crosses");
  const drawBB = isLayerActive("bb_squeeze");
  const drawVol = isLayerActive("vol_climax");
  if (drawCrosses || drawBB || drawVol) {
    for (const date of visibleDates) {
      if (date > sliderDate) continue;
      const sigs = signalsByDate.get(date);
      if (!sigs) continue;
      for (const sig of sigs) {
        const cx = xPos(sig.date);
        if (drawCrosses && sig.pattern === "golden_cross") {
          const cy = yPrice(sig.sma200 ?? sig.close ?? 0);
          drawStar(gOverlay, cx, cy, 8, "#e3b341", `Golden Cross ${sig.date}\nSMA50: ${sig.sma50?.toFixed(2)}  SMA200: ${sig.sma200?.toFixed(2)}`);
        } else if (drawCrosses && sig.pattern === "death_cross") {
          const cy = yPrice(sig.sma200 ?? sig.close ?? 0);
          drawX(gOverlay, cx, cy, 6, "#da3633", `Death Cross ${sig.date}\nSMA50: ${sig.sma50?.toFixed(2)}  SMA200: ${sig.sma200?.toFixed(2)}`);
        } else if (drawBB && sig.pattern === "bollinger_squeeze") {
          const cy = yPrice(sig.upper ?? sig.close ?? 0);
          gOverlay.append("circle")
            .attr("cx", cx).attr("cy", cy).attr("r", 5)
            .attr("fill", "none").attr("stroke", "#bc8cff").attr("stroke-width", 2)
            .attr("opacity", 0.7)
            .append("title").text(`BB Squeeze ${sig.date}\nBandwidth: ${sig.bandwidth?.toFixed(2)}%`);
        } else if (drawVol && sig.pattern === "volume_climax") {
          const cy = yPrice(sig.close ?? 0);
          gOverlay.append("rect")
            .attr("x", cx - 4).attr("y", cy - 4).attr("width", 8).attr("height", 8)
            .attr("fill", sig.direction === "bullish" ? "#3fb950" : "#e67e22")
            .attr("transform", `rotate(45,${cx},${cy})`)
            .attr("opacity", 0.7)
            .append("title").text(`Volume Climax ${sig.date}\n${sig.volume_ratio?.toFixed(1)}x avg volume, ${sig.direction}`);
        }
      }
    }
  }

  // Divergences
  if (isLayerActive("divergences") && result.divergences) {
    for (const div of result.divergences) {
      if (div.end_date > sliderDate) continue;
      if (!visibleDates.has(div.start_date) && !visibleDates.has(div.end_date)) continue;
      const color = div.direction === "bullish" ? "#3fb950" : "#f85149";

      gOverlay.append("line")
        .attr("x1", xPos(div.start_date)).attr("y1", yPrice(div.price_1))
        .attr("x2", xPos(div.end_date)).attr("y2", yPrice(div.price_2))
        .attr("stroke", color).attr("stroke-width", 2)
        .attr("stroke-dasharray", "3,3").attr("opacity", 0.6)
        .append("title").text(`${div.pattern.replace(/_/g, " ")} — ${div.direction}\nConf: ${div.confidence}`);
    }
  }

  // Gaps — uses gapsByDate index
  if (isLayerActive("gaps")) {
    for (const date of visibleDates) {
      if (date > sliderDate) continue;
      const gaps = gapsByDate.get(date);
      if (!gaps) continue;
      for (const gap of gaps) {
        gOverlay.append("rect")
          .attr("x", xScale(gap.date) ?? 0)
          .attr("y", yPrice(gap.gap_high))
          .attr("width", candleW)
          .attr("height", Math.abs(yPrice(gap.gap_low) - yPrice(gap.gap_high)))
          .attr("fill", gap.pattern === "gap_up" ? "#3fb950" : "#f85149")
          .attr("opacity", 0.12);
      }
    }
  }
}

// ── Signal markers ─────────────────────────────────────────────────────
function drawStar(
  g: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>,
  cx: number, cy: number, r: number, color: string, title: string,
) {
  const pts: string[] = [];
  for (let i = 0; i < 10; i++) {
    const angle = (Math.PI / 5) * i - Math.PI / 2;
    const radius = i % 2 === 0 ? r : r * 0.4;
    pts.push(`${cx + Math.cos(angle) * radius},${cy + Math.sin(angle) * radius}`);
  }
  g.append("polygon").attr("points", pts.join(" "))
    .attr("fill", color).attr("stroke", C("--chart-bg")).attr("stroke-width", 1.5)
    .attr("opacity", 0.9)
    .append("title").text(title);
}

function drawX(
  g: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>,
  cx: number, cy: number, r: number, color: string, title: string,
) {
  const group = g.append("g");
  group.append("line").attr("x1", cx - r).attr("y1", cy - r).attr("x2", cx + r).attr("y2", cy + r)
    .attr("stroke", color).attr("stroke-width", 3);
  group.append("line").attr("x1", cx + r).attr("y1", cy - r).attr("x2", cx - r).attr("y2", cy + r)
    .attr("stroke", color).attr("stroke-width", 3);
  group.append("title").text(title);
}

// ── Crosshair / Tooltip ────────────────────────────────────────────────
function onMouseMove(event: MouseEvent) {
  const [mx] = d3.pointer(event, svg.node());
  const relX = mx - margin.left;
  const chartW = W - margin.left - margin.right;
  if (relX < 0 || relX > chartW) { onMouseLeave(); return; }

  const domain = xScale.domain();
  const step = xScale.step();
  const idx = Math.min(domain.length - 1, Math.max(0, Math.floor(relX / step)));
  const date = domain[idx];
  const bar = bars.find((b) => b.date === date);
  if (!bar) return;

  const cx = (xScale(date) ?? 0) + xScale.bandwidth() / 2;
  const crossColor = C("--crosshair");

  gCrosshair.selectAll("*").remove();
  // Vertical crosshair line
  gCrosshair.append("line")
    .attr("x1", cx).attr("x2", cx)
    .attr("y1", 0).attr("y2", priceH + volGap + volHeight)
    .attr("stroke", crossColor).attr("stroke-width", 0.5).attr("stroke-dasharray", "3,3");

  // Date label on x-axis
  let dateLabel: string;
  if (dateMode === "trading") {
    const delta = (barIdxByDate.get(bar.date) ?? 0) - sliderIndex;
    dateLabel = delta === 0 ? "0" : delta > 0 ? `+${delta}` : String(delta);
  } else {
    const [_y, _m, _d] = bar.date.split("-");
    dateLabel = `${_d}/${_m}/${_y.slice(2)}`;
  }
  gCrosshair.append("rect")
    .attr("x", cx - 32).attr("y", priceH + volGap + volHeight + 2)
    .attr("width", 64).attr("height", 16).attr("rx", 3)
    .attr("fill", "#555");
  gCrosshair.append("text")
    .attr("x", cx).attr("y", priceH + volGap + volHeight + 13)
    .attr("text-anchor", "middle").attr("font-size", "10px").attr("fill", "#fff")
    .text(dateLabel);

  // Horizontal crosshair line + price label
  const my = d3.pointer(event, svg.node())[1] - margin.top;
  if (my >= 0 && my <= priceH) {
    const hoverPrice = yPrice.invert(my);
    gCrosshair.append("line")
      .attr("x1", 0).attr("x2", chartW)
      .attr("y1", my).attr("y2", my)
      .attr("stroke", crossColor).attr("stroke-width", 0.5).attr("stroke-dasharray", "3,3");
    // Price label on y-axis
    gCrosshair.append("rect")
      .attr("x", chartW + 2).attr("y", my - 8)
      .attr("width", 56).attr("height", 16).attr("rx", 3)
      .attr("fill", "#555");
    gCrosshair.append("text")
      .attr("x", chartW + 30).attr("y", my + 4)
      .attr("text-anchor", "middle").attr("font-size", "10px").attr("fill", "#fff")
      .text(d3.format(",.2f")(hoverPrice));
  }

  const tooltip = document.getElementById("tooltip")!;
  const up = bar.close >= bar.open;
  const change = ((bar.close - bar.open) / bar.open * 100).toFixed(2);
  const cls = up ? "price-up" : "price-down";
  const sign = up ? "+" : "";
  // Collect active patterns for this date (only in history)
  let patternsHtml = "";
  const sliderDate = bars[sliderIndex]?.date ?? "";
  if (result && bar.date <= sliderDate) {
    const items: string[] = [];
    const bullTriangle = `<svg width="10" height="10" style="flex-shrink:0"><polygon points="5,1 1,9 9,9" fill="#3fb950"/></svg>`;
    const bearTriangle = `<svg width="10" height="10" style="flex-shrink:0"><polygon points="5,9 1,1 9,1" fill="#f85149"/></svg>`;
    const diamond = `<svg width="10" height="10" style="flex-shrink:0"><polygon points="5,1 9,5 5,9 1,5" fill="#e3b341"/></svg>`;

    function candleIcon(pattern: string, direction: string): string {
      if (CANDLE_BULLISH.includes(pattern)) return bullTriangle;
      if (CANDLE_BEARISH.includes(pattern)) return bearTriangle;
      return direction === "bullish" ? bullTriangle : bearTriangle;
    }

    // Candlestick patterns — from index
    if (candleEnabled.size > 0) {
      const pats = candleByDate.get(bar.date);
      if (pats) {
        for (const p of pats) {
          if (!candleEnabled.has(p.pattern)) continue;
          const c = p.direction === "bullish" ? "price-up" : "price-down";
          items.push(`${candleIcon(p.pattern, p.direction)} <span class="${c}">${prettyName(p.pattern.toLowerCase())}</span>`);
        }
      }
    }

    // Geometric patterns active on this date — from pre-sorted index, deduplicate
    {
      const seenGeo = new Set<string>();
      for (const p of geoByEndDate) {
        if (p.end_date > sliderDate) break;
        if (!geoEnabled.has(p.pattern)) continue;
        if (p.start_date <= bar.date && p.end_date >= bar.date && !seenGeo.has(p.pattern)) {
          seenGeo.add(p.pattern);
          const color = PATTERN_COLORS[p.pattern] ?? "#8b949e";
          const icon = `<svg width="10" height="10" style="flex-shrink:0"><circle cx="5" cy="5" r="4" fill="${color}"/></svg>`;
          const c = p.direction === "bullish" ? "price-up" : "price-down";
          items.push(`${icon} <span class="${c}">${prettyName(p.pattern)}</span>`);
        }
      }
    }

    // Signals — from index
    {
      const sigs = signalsByDate.get(bar.date);
      if (sigs) {
        for (const s of sigs) {
          if (s.pattern === "golden_cross" && isLayerActive("crosses"))
            items.push(`<svg width="10" height="10" style="flex-shrink:0"><polygon points="5,0 6.2,3.6 10,3.8 7,6 7.8,10 5,7.8 2.2,10 3,6 0,3.8 3.8,3.6" fill="#e3b341"/></svg> <span class="price-up">Golden Cross</span>`);
          if (s.pattern === "death_cross" && isLayerActive("crosses"))
            items.push(`<svg width="10" height="10" style="flex-shrink:0"><line x1="2" y1="2" x2="8" y2="8" stroke="#da3633" stroke-width="2"/><line x1="8" y1="2" x2="2" y2="8" stroke="#da3633" stroke-width="2"/></svg> <span class="price-down">Death Cross</span>`);
          if (s.pattern === "bollinger_squeeze" && isLayerActive("bb_squeeze"))
            items.push(`<svg width="10" height="10" style="flex-shrink:0"><circle cx="5" cy="5" r="4" fill="none" stroke="#bc8cff" stroke-width="1.5"/></svg> BB Squeeze`);
          if (s.pattern === "volume_climax" && isLayerActive("vol_climax"))
            items.push(`<svg width="10" height="10" style="flex-shrink:0"><rect x="2" y="2" width="6" height="6" fill="#e67e22" transform="rotate(45,5,5)"/></svg> Vol Climax`);
        }
      }
    }

    // Divergences active on this date — deduplicate by pattern+direction
    if (isLayerActive("divergences") && result.divergences) {
      const seenDiv = new Set<string>();
      for (const d of result.divergences) {
        const key = `${d.pattern}_${d.direction}`;
        if (d.start_date <= bar.date && d.end_date >= bar.date && !seenDiv.has(key)) {
          seenDiv.add(key);
          const c = d.direction === "bullish" ? "price-up" : "price-down";
          const color = d.direction === "bullish" ? "#3fb950" : "#f85149";
          items.push(`<svg width="10" height="10" style="flex-shrink:0"><line x1="0" y1="5" x2="10" y2="5" stroke="${color}" stroke-width="1.5" stroke-dasharray="2,1"/></svg> <span class="${c}">${prettyName(d.pattern)}</span>`);
        }
      }
    }

    if (items.length > 0) {
      const rows = items.map(i => `<div style="display:flex;align-items:center;gap:4px;margin-top:2px">${i}</div>`).join("");
      patternsHtml = `<div style="border-top:1px solid var(--border);margin-top:4px;padding-top:4px;font-size:10px;color:var(--text-muted)">${rows}</div>`;
    }
  }

  tooltip.innerHTML = `
    <div class="date">${bar.date}</div>
    <div>O: <span class="${cls}">${bar.open.toFixed(2)}</span></div>
    <div>H: <span class="${cls}">${bar.high.toFixed(2)}</span></div>
    <div>L: <span class="${cls}">${bar.low.toFixed(2)}</span></div>
    <div>C: <span class="${cls}">${bar.close.toFixed(2)}</span> (${sign}${change}%)</div>
    <div>Vol: ${d3.format(",")(bar.volume)}</div>
    ${patternsHtml}
  `;
  tooltip.style.display = "block";
  const ttW = tooltip.offsetWidth;
  tooltip.style.left = `${mx + 20 + ttW > W ? mx - ttW - 10 : mx + 20}px`;
  tooltip.style.top = `${Math.max(10, d3.pointer(event, svg.node())[1] - 40)}px`;
}

function onMouseLeave() {
  gCrosshair.selectAll("*").remove();
  document.getElementById("tooltip")!.style.display = "none";
}

// ── Now handle positioning ──────────────────────────────────────────────
function positionNowHandle() {
  const handle = document.getElementById("now-handle")!;
  const grab = document.getElementById("now-grab")!;

  if (!xScale || bars.length === 0) {
    handle.style.display = "none";
    grab.style.display = "none";
    return;
  }

  const sliderDate = bars[sliderIndex]?.date ?? "";
  const [vi0, vi1] = visibleRange;
  const visible = bars.slice(vi0, vi1 + 1);

  if (!visible.length || sliderDate < visible[0].date || sliderDate > visible[visible.length - 1].date) {
    handle.style.display = "none";
    grab.style.display = "none";
    return;
  }

  const candleW = xScale.bandwidth();
  // x position relative to chart-container (includes the 1px border + margin.left)
  const px = margin.left + 1 + (xScale(sliderDate) ?? 0) + candleW / 2;

  handle.style.display = "block";
  handle.style.left = `${px}px`;
  handle.style.height = `${H + 18}px`; // +18 for the top offset

  grab.style.display = "block";
  grab.style.left = `${px}px`;
}

// ── Now handle drag ────────────────────────────────────────────────────
function initNowDrag() {
  const grab = document.getElementById("now-grab")!;
  const thumb = document.querySelector("#now-handle .now-thumb") as HTMLElement;
  let dragging = false;

  function onPointerDown(e: PointerEvent) {
    dragging = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    updateSliderFromX(e.clientX);
  }

  function onPointerUp() {
    dragging = false;
  }

  // Both the grab zone and thumb are draggable
  for (const el of [grab, thumb]) {
    el.addEventListener("pointerdown", onPointerDown);
  }
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);

  // Click anywhere on the chart to move NOW there
  document.getElementById("chart-container")!.addEventListener("click", (e) => {
    // Don't trigger on drag release or thumb click
    if ((e.target as HTMLElement).closest("#now-handle, #now-grab")) return;
    updateSliderFromX(e.clientX);
  });
}

function updateSliderFromX(clientX: number) {
  const container = document.getElementById("chart-container")!;
  const rect = container.getBoundingClientRect();
  // Convert clientX to plot area x
  const plotX = clientX - rect.left - 1 - margin.left; // 1px border
  const chartW = W - margin.left - margin.right;

  if (plotX < 0 || plotX > chartW || !xScale) return;

  // Find nearest bar index from plotX
  const domain = xScale.domain();
  const step = xScale.step();
  const domainIdx = Math.min(domain.length - 1, Math.max(0, Math.floor(plotX / step)));
  const date = domain[domainIdx];

  // Find the global bar index for this date
  const barIdx = bars.findIndex((b) => b.date === date);
  if (barIdx >= 0) {
    sliderIndex = barIdx;
    draw();
  }
}

// ── Zoom slider ─────────────────────────────────────────────────────────
document.getElementById("zoom-slider")!.addEventListener("input", () => applyZoom());

// ── Nav slider ──────────────────────────────────────────────────────────
document.getElementById("nav-slider")!.addEventListener("input", () => {
  const navVal = parseInt((document.getElementById("nav-slider") as HTMLInputElement).value);
  const total = bars.length;
  const visibleCount = visibleRange[1] - visibleRange[0] + 1;
  const maxStart = total - visibleCount;
  const start = Math.round((navVal / 1000) * maxStart);
  visibleRange = [Math.max(0, start), Math.min(total - 1, start + visibleCount - 1)];
  draw();
});

// ── Timeframe buttons ────────────────────────────────────────────────────
function showTimeframe(days: number) {
  const total = bars.length;
  const count = Math.min(days, total);
  const end = sliderIndex;
  const start = Math.max(0, end - count + 1);
  visibleRange = [start, end];

  // Sync zoom slider
  const zoomVal = Math.round(((total - (end - start + 1)) / (total - MIN_VISIBLE_BARS)) * 100);
  (document.getElementById("zoom-slider") as HTMLInputElement).value = String(Math.max(0, Math.min(100, zoomVal)));
  document.getElementById("zoom-label")!.textContent = `${Math.round(((end - start + 1) / total) * 100)}%`;

  // Highlight active button
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
  document.querySelector(`.tf-btn[data-days="${days}"]`)?.classList.add("active");

  syncNavSlider();
  draw();
}

// ── Trading days toggle ──────────────────────────────────────────────────
document.getElementById("td-toggle")!.addEventListener("change", (e) => {
  dateMode = (e.target as HTMLInputElement).checked ? "trading" : "calendar";
  draw();
});

document.querySelectorAll(".tf-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const days = parseInt((btn as HTMLElement).dataset.days!);
    showTimeframe(days);
  });
});

// ── Nav buttons ─────────────────────────────────────────────────────────
document.getElementById("nav-left")!.addEventListener("click", () => {
  const visibleCount = visibleRange[1] - visibleRange[0] + 1;
  panWindow(-Math.max(1, Math.floor(visibleCount * 0.1)));
});
document.getElementById("nav-right")!.addEventListener("click", () => {
  const visibleCount = visibleRange[1] - visibleRange[0] + 1;
  panWindow(Math.max(1, Math.floor(visibleCount * 0.1)));
});

// ── Keyboard shortcuts ─────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  // Cmd+= / Cmd+- : zoom centered on NOW
  if ((e.metaKey || e.ctrlKey) && (e.key === "=" || e.key === "+" || e.key === "-")) {
    e.preventDefault();
    const currentVisible = visibleRange[1] - visibleRange[0] + 1;
    const factor = (e.key === "-") ? 1.2 : 0.8;
    zoomCenteredOnNow(Math.round(currentVisible * factor));
    return;
  }

  // Cmd+] / Cmd+[ : pan window right/left
  if ((e.metaKey || e.ctrlKey) && (e.key === "]" || e.key === "[")) {
    e.preventDefault();
    const visibleCount = visibleRange[1] - visibleRange[0] + 1;
    const step = Math.max(1, Math.floor(visibleCount * 0.1));
    panWindow(e.key === "]" ? step : -step);
    return;
  }

  // Arrow keys: move NOW bar
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    let step = 1;
    if (e.metaKey || e.ctrlKey) {
      // Cmd+Arrow: jump to start/end
      sliderIndex = e.key === "ArrowLeft" ? 0 : bars.length - 1;
    } else {
      if (e.altKey) step = 5;       // Alt+Arrow: 5 days
      if (e.shiftKey) step = 20;    // Shift+Arrow: 20 days
      const delta = e.key === "ArrowLeft" ? -step : step;
      sliderIndex = Math.max(0, Math.min(bars.length - 1, sliderIndex + delta));
    }
    draw();
  }
});

// ── Snapshot (PNG + CSV) ─────────────────────────────────────────────────
document.getElementById("snapshot-btn")!.addEventListener("click", () => {
  const [vi0, vi1] = visibleRange;
  const visible = bars.slice(vi0, vi1 + 1);
  const sliderDate = bars[sliderIndex]?.date ?? "";
  const ticker = currentTicker;
  const prefix = `${ticker}_${sliderDate.slice(0, 10)}`;

  // --- PNG: capture chart container directly ---
  import("html2canvas").then(({ default: html2canvas }) => {
    const el = document.getElementById("chart-container")!;
    html2canvas(el, { backgroundColor: "#ffffff", scale: 2 }).then((canvas) => {
      canvas.toBlob((blob) => {
        if (!blob) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `${prefix}.png`;
        a.click();
        URL.revokeObjectURL(a.href);
      }, "image/png");
    });
  });

  // --- CSV: visible bars + active patterns ---
  const csvRows: string[] = [];
  csvRows.push("date,open,high,low,close,volume,candlestick_patterns,geometric_patterns,signals,divergences,gaps,support_levels,resistance_levels");

  for (const bar of visible) {
    const d = bar.date;
    const inHistory = d <= sliderDate;

    let candles = "";
    let geos = "";
    let sigs = "";
    let divs = "";
    let gaps = "";

    if (inHistory && result) {
      const cp = candleByDate.get(d);
      if (cp) candles = cp.map(p => `${p.pattern}(${p.direction})`).join("; ");

      const geoActive: string[] = [];
      for (const g of geoByEndDate) {
        if (g.end_date > sliderDate) break;
        if (g.start_date <= d && g.end_date >= d) geoActive.push(`${g.pattern}(${g.direction})`);
      }
      geos = geoActive.join("; ");

      const sp = signalsByDate.get(d);
      if (sp) sigs = sp.map(s => s.pattern).join("; ");

      const dp = divByDate.get(d);
      if (dp) divs = dp.map(v => `${v.pattern}(${v.direction})`).join("; ");

      const gp = gapsByDate.get(d);
      if (gp) gaps = gp.map(g => `${g.pattern}(${g.gap_pct.toFixed(1)}%)`).join("; ");
    }

    // Active S/R levels at this date
    const supports: number[] = [];
    const resistances: number[] = [];
    if (inHistory) {
      for (const z of srMerged) {
        if (z.start_date <= d && (z.end_date >= d || (!z.broken))) {
          if (z.broken && z.broken_date && z.broken_date < d) continue;
          if (z.type === "support") supports.push(z.level);
          else resistances.push(z.level);
        }
      }
    }

    const esc = (s: string) => `"${s.replace(/"/g, '""')}"`;
    csvRows.push([
      d, bar.open.toFixed(2), bar.high.toFixed(2), bar.low.toFixed(2), bar.close.toFixed(2), bar.volume,
      esc(candles), esc(geos), esc(sigs), esc(divs), esc(gaps),
      esc(supports.map(l => l.toFixed(2)).join("; ")),
      esc(resistances.map(l => l.toFixed(2)).join("; ")),
    ].join(","));
  }

  const csvBlob = new Blob([csvRows.join("\n")], { type: "text/csv" });
  const csvA = document.createElement("a");
  csvA.href = URL.createObjectURL(csvBlob);
  csvA.download = `${prefix}.csv`;
  csvA.click();
  URL.revokeObjectURL(csvA.href);
});

// ── Start ──────────────────────────────────────────────────────────────
init();
