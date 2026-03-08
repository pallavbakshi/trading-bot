import "./styles.css";
import * as d3 from "d3";
import { initChat } from "./chat";
import type {
  Bar,
  TickerResult,
  TickerData,
  LayerKey,
  Layer,
} from "./types";

// ── Defaults (edit here to change initial state) ──────────────────────
const DEFAULTS = {
  interval: "daily" as "daily" | "weekly" | "monthly",
  dateMode: "calendar" as "calendar" | "trading",
  showVolProfile: false,
  showSMA: false,
  showRSI: false,
  showAVWAP: false,
  layers: {
    sr: false,
    geometric: false,
    crosses: false,
    bb_squeeze: false,
    vol_climax: false,
    divergences: false,
    gaps: false,
  },
  geoEnabled: false,    // false = all off, true = all on
  candleEnabled: false,  // false = all off, true = all on
};

// ── State ──────────────────────────────────────────────────────────────
let bars: Bar[] = [];
let result: TickerResult | null = null;
let sliderIndex = 0;
interface TickerInfo { ticker: string; exchange: string; }
let tickers: TickerInfo[] = [];
let currentTicker = "";
let isDark = false;

const layers: Layer[] = [
  { key: "sr",             label: "S/R",            active: DEFAULTS.layers.sr },
  { key: "geometric",      label: "Geometric",       active: DEFAULTS.layers.geometric },
  { key: "crosses",        label: "GC / DC",         active: DEFAULTS.layers.crosses },
  { key: "bb_squeeze",     label: "BB Squeeze",      active: DEFAULTS.layers.bb_squeeze },
  { key: "vol_climax",     label: "Vol Climax",      active: DEFAULTS.layers.vol_climax },
  { key: "divergences",    label: "Diverg.",         active: DEFAULTS.layers.divergences },
  { key: "gaps",           label: "Gaps",            active: DEFAULTS.layers.gaps },
  { key: "llm_levels",     label: "LLM Key Levels",  active: false },
];

// ── Dimensions ─────────────────────────────────────────────────────────
const margin = { top: 20, right: 60, bottom: 20, left: 60 };
const volHeight = 80;
const volGap = 8;
const rsiHeight = 60;
const rsiGap = 8;
let W = 0;
let H = 0;
let priceH = 0;

// ── Scales ─────────────────────────────────────────────────────────────
let xScale: d3.ScaleBand<string>;
let yPrice: d3.ScaleLinear<number, number>;
let yVol: d3.ScaleLinear<number, number>;

// ── View state ─────────────────────────────────────────────────────────
let visibleRange: [number, number] = [0, 0];
const MIN_VISIBLE_BARS = 30;
let dateMode = DEFAULTS.dateMode;
let showVolProfile = DEFAULTS.showVolProfile;
let showSMA = DEFAULTS.showSMA;
let showRSI = DEFAULTS.showRSI;
let showAVWAP = DEFAULTS.showAVWAP;
let llmLevels: { resistance: number[]; support: number[] } | null = null;
let llmLevelsKey: string | null = null;      // key of currently loaded levels
let _llmCheckedKey: string | null = null;    // last key we already queried (no cache found)
let _llmPendingKey: string | null = null;    // key for which a cache-check has been armed
let _llmCacheTimer: ReturnType<typeof setTimeout> | null = null;
let timeframe = DEFAULTS.interval;
let activeTfDays: number | null = null; // tracks lookback selection
let activeLfDays: number | null = null; // tracks lookforward selection
let tickerData: import("./types").TickerData | null = null;

// ── Pre-computed indicator arrays (from server) ──────────────────────────
let sma50: number[] = [];
let sma200: number[] = [];
let rsiValues: number[] = [];

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
let gVolProfile: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gSMA: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gAVWAP: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gLLMLevels: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gRSI: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
let gYRSIAxis: d3.Selection<SVGGElement, unknown, HTMLElement, unknown>;
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
async function fetchTickers(): Promise<TickerInfo[]> {
  const resp = await fetch("/api/tickers");
  const data = await resp.json();
  // Handle both old (string[]) and new ({ticker,exchange}[]) formats
  if (data.length > 0 && typeof data[0] === "string") {
    return data.map((t: string) => ({ ticker: t, exchange: "US" }));
  }
  return data;
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
    .attr("value", (d) => d.ticker).text((d) => d.ticker);
  select.on("change", async function () { await loadTicker(this.value); });

  renderLayerPanel();
  initNowDrag();

  // Chat drawer (init early so it's always visible)
  initChat(() => {
    const tf = timeframe === "daily" ? "D" : timeframe === "weekly" ? "W" : "M";
    const active: string[] = [];
    if (showSMA) active.push("SMA50/200");
    if (showRSI) active.push("RSI14");
    if (showAVWAP) active.push("AVWAP");
    if (showVolProfile) active.push("Volume Profile");
    return `${tf}${active.length ? " | " + active.join(", ") : ""}`;
  }, () => {
    // Generate CSV from visible bars + indicators (no dates to avoid ticker identification)
    const [vStart, vEnd] = visibleRange;
    const header = ["day", "open", "high", "low", "close", "volume"];
    if (showSMA) header.push("sma50", "sma200");
    if (showRSI) header.push("rsi");
    const rows = [header.join(",")];
    for (let i = vStart; i <= vEnd && i < bars.length; i++) {
      const b = bars[i];
      const dayLabel = dateMode === "trading"
        ? String(i - sliderIndex)
        : b.date;
      const cols: (string | number)[] = [dayLabel, b.open, b.high, b.low, b.close, b.volume];
      if (showSMA) {
        cols.push(isNaN(sma50[i]) ? "" : sma50[i]);
        cols.push(isNaN(sma200[i]) ? "" : sma200[i]);
      }
      if (showRSI) {
        cols.push(isNaN(rsiValues[i]) ? "" : rsiValues[i]);
      }
      rows.push(cols.join(","));
    }
    // Append volume profile summary if active
    if (showVolProfile) {
      const sliderDate = bars[sliderIndex]?.date ?? "";
      const histBars = bars.slice(vStart, vEnd + 1).filter(b => b.date <= sliderDate);
      const vp = computeVolumeProfileStats(histBars);
      if (vp) {
        rows.push("");
        rows.push(`# Volume Profile: POC=${vp.poc} VAH=${vp.vah} VAL=${vp.val}`);
      }
    }
    return rows.join("\n");
  });

  await loadTicker(tickers[0].ticker);

  initSim();

  window.addEventListener("resize", () => {
    if (bars.length > 0) { setupSVG(); draw(); }
  });

  // CLI sidecar: poll for commands
  startCommandPoll();
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

async function loadTicker(ticker: string, force = false) {
  if (!force && ticker === currentTicker && bars.length > 0) return;
  currentTicker = ticker;
  d3.select("#ticker-select").property("value", ticker);

  // Show loading spinner
  const chartContainer = document.getElementById("chart-container")!;
  const overlay = document.createElement("div");
  overlay.id = "loading-overlay";
  overlay.innerHTML = `<div class="loading-spinner"></div>`;
  chartContainer.appendChild(overlay);

  tickerData = await fetchTickerData(ticker);
  const tf = tickerData[timeframe];
  bars = tf.bars;
  result = tf.result;
  sma50 = tf.sma50.map(v => v ?? NaN);
  sma200 = tf.sma200.map(v => v ?? NaN);
  rsiValues = tf.rsi.map(v => v ?? NaN);

  renderLayerPanel();
  renderHistoryPanel();
  precompute();

  visibleRange = [0, bars.length - 1];
  sliderIndex = Math.floor(bars.length * 0.75);

  const zoomSlider = document.getElementById("zoom-slider") as HTMLInputElement;
  zoomSlider.value = "0";
  document.getElementById("zoom-label")!.textContent = "100%";
  (document.getElementById("nav-slider") as HTMLInputElement).value = "1000";
  activeTfDays = null;
  activeLfDays = null;
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".lf-btn").forEach(b => b.classList.remove("active"));

  // Set date picker bounds
  if (bars.length > 0) {
    const minDate = bars[0].date;
    const maxDate = bars[bars.length - 1].date;
    for (const id of ["goto-date", "vdr-start", "vdr-end"]) {
      const el = document.getElementById(id) as HTMLInputElement;
      el.min = minDate;
      el.max = maxDate;
    }
    (document.getElementById("vdr-start") as HTMLInputElement).value = "";
    (document.getElementById("vdr-end") as HTMLInputElement).value = "";
  }

  setupSVG();
  draw();
  positionNowHandle();

  // Remove loading spinner
  document.getElementById("loading-overlay")?.remove();
}

function switchTimeframe(tf: "daily" | "weekly" | "monthly") {
  if (tf === timeframe) return;

  // Show spinner immediately, then yield so the browser paints it before the heavy redraw.
  const chartContainer = document.getElementById("chart-container")!;
  if (!document.getElementById("loading-overlay")) {
    const overlay = document.createElement("div");
    overlay.id = "loading-overlay";
    overlay.innerHTML = `<div class="loading-spinner"></div>`;
    chartContainer.appendChild(overlay);
  }

  setTimeout(() => {
    // Remember current NOW date
    const currentDate = bars[sliderIndex]?.date ?? "";

    timeframe = tf;
    const data = tickerData![tf];
    bars = data.bars;
    result = data.result;
    sma50 = data.sma50.map(v => v ?? NaN);
    sma200 = data.sma200.map(v => v ?? NaN);
    rsiValues = data.rsi.map(v => v ?? NaN);
    precompute();

    // Find closest bar to previous NOW date
    let bestIdx = 0;
    for (let i = 0; i < bars.length; i++) {
      if (bars[i].date <= currentDate) bestIdx = i;
    }

    sliderIndex = bestIdx;

    // Update button active states
    document.querySelectorAll(".interval-btn").forEach(btn => {
      btn.classList.toggle("active", (btn as HTMLElement).dataset.interval === tf);
    });

    // Re-apply active timeframe window, or reset to full zoom
    if (activeTfDays !== null) {
      showTimeframe(activeTfDays);
    } else if (activeLfDays !== null) {
      showLookforward(activeLfDays);
    } else {
      visibleRange = [0, bars.length - 1];
      const zoomSlider = document.getElementById("zoom-slider") as HTMLInputElement;
      zoomSlider.value = "0";
      document.getElementById("zoom-label")!.textContent = "100%";
      (document.getElementById("nav-slider") as HTMLInputElement).value = "1000";
      draw();
    }
    positionNowHandle();

    document.getElementById("loading-overlay")?.remove();
  }, 0);
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
const ALL_GEO_TYPES = [
  "double_top", "double_bottom",
  "head_and_shoulders", "inverse_head_and_shoulders",
  "triple_top", "triple_bottom",
  "ascending_triangle", "descending_triangle", "symmetrical_triangle",
  "rising_wedge", "falling_wedge",
  "ascending_channel", "descending_channel", "horizontal_channel",
  "broadening_formation",
  "bull_flag", "bear_flag", "bull_pennant", "bear_pennant",
];
const candleEnabled = new Set<string>(DEFAULTS.candleEnabled ? ALL_CANDLE_TYPES : []);
const geoEnabled = new Set<string>(DEFAULTS.geoEnabled ? ALL_GEO_TYPES : []);

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
  for (const gt of ALL_GEO_TYPES) {
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

  // ── LLM Key Levels ──
  rows.push(
    { icon: `<svg width="14" height="14"><line x1="0" y1="7" x2="14" y2="7" stroke="#ef4444" stroke-width="1.5" stroke-dasharray="4,2"/><line x1="0" y1="10" x2="14" y2="10" stroke="#22c55e" stroke-width="1.5" stroke-dasharray="4,2"/></svg>`,
      label: "LLM Key Levels", layerKey: "llm_levels",
      countFn: () => (llmLevels?.resistance.length ?? 0) + (llmLevels?.support.length ?? 0) },
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
  const baseH = Math.max(500, Math.min(window.innerHeight - 200, 700));
  priceH = baseH - margin.top - margin.bottom - volHeight - volGap;
  const rsiExtra = rsiHeight + rsiGap; // always reserve space
  H = baseH + rsiExtra;

  d3.select("#chart-container").selectAll("svg").remove();

  svg = d3.select("#chart-container").append("svg")
    .attr("width", W).attr("height", H);

  const chartW = W - margin.left - margin.right;
  svg.append("defs").append("clipPath").attr("id", "clip-price")
    .append("rect").attr("width", chartW).attr("height", priceH);
  svg.select("defs").append("clipPath").attr("id", "clip-vol")
    .append("rect").attr("width", chartW).attr("height", volHeight);
  svg.select("defs").append("clipPath").attr("id", "clip-rsi")
    .append("rect").attr("width", chartW).attr("height", rsiHeight);

  gPrice = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gVolProfile = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gSMA = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gAVWAP = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gLLMLevels = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gOverlay = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`)
    .attr("clip-path", "url(#clip-price)");
  gVol = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top + priceH + volGap})`)
    .attr("clip-path", "url(#clip-vol)");

  const rsiTop = margin.top + priceH + volGap + volHeight + rsiGap;
  gRSI = svg.append("g")
    .attr("transform", `translate(${margin.left},${rsiTop})`)
    .attr("clip-path", "url(#clip-rsi)");

  const xAxisY = margin.top + priceH + volGap + volHeight + rsiExtra;
  gXAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${margin.left},${xAxisY})`);
  gYAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${W - margin.right},${margin.top})`);
  gYVolAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${W - margin.right},${margin.top + priceH + volGap})`);
  gYRSIAxis = svg.append("g").attr("class", "axis")
    .attr("transform", `translate(${W - margin.right},${rsiTop})`);
  gCrosshair = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`);

  svg.append("rect").attr("class", "mouse-rect")
    .attr("x", margin.left).attr("y", margin.top)
    .attr("width", chartW)
    .attr("height", priceH + volGap + volHeight + rsiExtra)
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
function currentLLMKey(): string {
  const [v0, v1] = visibleRange;
  return [currentTicker, bars[sliderIndex]?.date ?? "", bars[v0]?.date ?? "", bars[v1]?.date ?? "", timeframe].join("|");
}

function scheduleCheckLLMCache() {
  if (_llmCacheTimer) clearTimeout(_llmCacheTimer);
  _llmCacheTimer = setTimeout(async () => {
    const key = currentLLMKey();
    if (llmLevelsKey === key) return;   // already loaded
    if (_llmCheckedKey === key) return; // already queried — no cache exists for this key
    const [v0, v1] = visibleRange;
    const params = new URLSearchParams({
      ticker:    currentTicker,
      date:      bars[sliderIndex]?.date ?? "",
      vdr_start: bars[v0]?.date ?? "",
      vdr_end:   bars[v1]?.date ?? "",
      interval:  timeframe,
    });
    try {
      const r = await fetch(`/api/keylevels/check?${params}`);
      const data = await r.json();
      if (data !== null && currentLLMKey() === key) {
        // Cache auto-load: don't override the user's eye state
        await executeCommand({ action: "key_levels", resistance: data.resistance, support: data.support, activate: false });
      } else {
        _llmCheckedKey = key;  // no cache — don't re-query until key changes
      }
    } catch (e) {
      console.error("keylevels cache check failed:", e);
    }
  }, 400);
}

function draw() {
  if (bars.length === 0) return;

  // Clear LLM levels if the chart state they were loaded for has changed,
  // then schedule a cache lookup for the new state (once per unique key, not every draw).
  const _curLLMKey = currentLLMKey();
  if (llmLevelsKey !== _curLLMKey) {
    if (llmLevels) {
      llmLevels = null;
      llmLevelsKey = null;
      // Don't touch the eye state — drawLLMLevels() won't render when llmLevels is null,
      // and we want to restore to the user's preferred state when cache reloads.
    }
    if (_llmPendingKey !== _curLLMKey) {
      _llmPendingKey = _curLLMKey;
      scheduleCheckLLMCache();
    }
  }

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
      `<span class="now-date">Current Day</span>` +
      `<span class="ohlc-item"><span class="ohlc-label">O</span> <span class="${cls}">${nowBar.open.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">H</span> <span class="${cls}">${nowBar.high.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">L</span> <span class="${cls}">${nowBar.low.toFixed(2)}</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">C</span> <span class="${cls}">${nowBar.close.toFixed(2)}</span> <span class="${cls}">(${sign}${chg}%)</span></span>` +
      `<span class="ohlc-item"><span class="ohlc-label">Vol</span> <span style="color:var(--text)">${d3.format(",")(nowBar.volume)}</span></span>`;
    (document.getElementById("goto-date") as HTMLInputElement).value = sliderDate.slice(0, 10);
  }

  // Sync VDR with visible window
  const [vr0, vr1] = visibleRange;
  if (bars[vr0] && bars[vr1]) {
    (document.getElementById("vdr-start") as HTMLInputElement).value = bars[vr0].date;
    (document.getElementById("vdr-end") as HTMLInputElement).value = bars[vr1].date;
  }

  // Legend — only show entries for active overlays
  const legendItems: string[] = [];
  if (showSMA) {
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:2px;background:#f59e0b"></span>SMA50</span>`);
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:2px;background:#a855f7"></span>SMA200</span>`);
  }
  if (showAVWAP) {
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:2px;background:#ef4444;border-top:1px dashed #ef4444"></span>AVWAP↓</span>`);
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:2px;background:#22c55e;border-top:1px dashed #22c55e"></span>AVWAP↑</span>`);
  }
  if (showVolProfile) {
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:8px;background:#6366f1;opacity:0.35;border-radius:1px"></span>POC</span>`);
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:8px;background:#818cf8;opacity:0.25;border-radius:1px"></span>Value Area</span>`);
  }
  if (showRSI) {
    legendItems.push(`<span class="flex items-center gap-1"><span style="display:inline-block;width:16px;height:2px;background:#6366f1"></span>RSI(14)</span>`);
  }
  document.getElementById("chart-legend")!.innerHTML = legendItems.join("");

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
    .tickFormat((d: string) => {
      if (dateMode === "trading") {
        const barGlobalIdx = barIdxByDate.get(d) ?? 0;
        const delta = barGlobalIdx - sliderIndex;
        return delta === 0 ? "0" : delta > 0 ? `+${delta}` : String(delta);
      }
      const [y, m, day] = d.split("-");
      return `${day}/${m}/${y.slice(2)}`;
    });
  (gXAxis as any).call(xAxis);
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
      const isLookforward = d.date > sliderDate;
      const base = up ? upColor : downColor;
      return isLookforward ? base.replace(")", ",0.25)").replace("rgb", "rgba") : base;
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
    document.getElementById("days-lookback")!.textContent = `(${daysBehind} trading days)`;
    document.getElementById("days-lookforward")!.textContent = `(${daysForward} trading days)`;
  } else {
    const firstDate = new Date(visible[0].date);
    const nowDate = new Date(sliderDate);
    const lastDate = new Date(visible[visible.length - 1].date);
    document.getElementById("days-lookback")!.textContent = `(${fmtDateDiff(firstDate, nowDate)})`;
    document.getElementById("days-lookforward")!.textContent = `(${fmtDateDiff(nowDate, lastDate)})`;
  }

  drawVolumeProfile(visible, sliderDate);
  drawSMA(visible);
  drawAVWAP(visible, sliderDate);
  drawLLMLevels();
  drawOverlays(visible, sliderDate);
  drawRSI(visible, sliderDate);
  positionNowHandle();
  updateLayerPanel(sliderDate);
  postState();
}

function postState() {
  const sliderDate = bars[sliderIndex]?.date ?? "";
  const [v0, v1] = visibleRange;
  const state = {
    ticker: currentTicker,
    interval: timeframe,
    date: sliderDate,
    vdr: [bars[v0]?.date ?? "", bars[v1]?.date ?? ""],
    visible_bars: v1 - v0 + 1,
    total_bars: bars.length,
    lookback: activeTfDays,
    lookforward: activeLfDays,
    trading_days: dateMode === "trading",
    volume_profile: showVolProfile,
    sma: showSMA,
    rsi: showRSI,
    avwap: showAVWAP,
  };
  fetch("/api/state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  }).catch(() => {});
}

// ── Overlays ───────────────────────────────────────────────────────────
// ── Volume Profile ──────────────────────────────────────────────────────
function computeVolumeProfileStats(histBars: Bar[]): { poc: number; vah: number; val: number } | null {
  if (histBars.length < 10) return null;
  const prices = histBars.flatMap(b => [b.low, b.high]);
  const pLow = Math.min(...prices);
  const pHigh = Math.max(...prices);
  const NUM_BINS = 120;
  const binSize = (pHigh - pLow) / NUM_BINS;
  if (binSize <= 0) return null;

  const bins = new Float64Array(NUM_BINS);
  for (const bar of histBars) {
    const lo = Math.max(0, Math.min(NUM_BINS - 1, Math.floor((bar.low - pLow) / binSize)));
    const hi = Math.max(0, Math.min(NUM_BINS - 1, Math.floor((bar.high - pLow) / binSize)));
    const span = hi - lo + 1;
    const volPerBin = bar.volume / span;
    for (let b = lo; b <= hi; b++) bins[b] += volPerBin;
  }

  let pocBin = 0, maxVol = 0, totalVol = 0;
  for (let i = 0; i < NUM_BINS; i++) {
    totalVol += bins[i];
    if (bins[i] > maxVol) { maxVol = bins[i]; pocBin = i; }
  }
  if (maxVol === 0) return null;

  const vaTarget = totalVol * 0.70;
  let vaVol = bins[pocBin], vaLow = pocBin, vaHigh = pocBin;
  while (vaVol < vaTarget && (vaLow > 0 || vaHigh < NUM_BINS - 1)) {
    const belowVol = vaLow > 0 ? bins[vaLow - 1] : 0;
    const aboveVol = vaHigh < NUM_BINS - 1 ? bins[vaHigh + 1] : 0;
    if (belowVol >= aboveVol && vaLow > 0) { vaLow--; vaVol += bins[vaLow]; }
    else if (vaHigh < NUM_BINS - 1) { vaHigh++; vaVol += bins[vaHigh]; }
    else { vaLow--; vaVol += bins[vaLow]; }
  }

  return {
    poc: +(pLow + (pocBin + 0.5) * binSize).toFixed(2),
    vah: +(pLow + (vaHigh + 1) * binSize).toFixed(2),
    val: +(pLow + vaLow * binSize).toFixed(2),
  };
}

function drawVolumeProfile(visible: Bar[], sliderDate: string) {
  gVolProfile.selectAll("*").remove();
  if (!showVolProfile || visible.length === 0) return;

  // Only use lookback bars (up to and including NOW)
  const histBars = visible.filter(b => b.date <= sliderDate);
  if (histBars.length < 10) return;

  const chartW = W - margin.left - margin.right;
  const pLow = yPrice.domain()[0];
  const pHigh = yPrice.domain()[1];
  const NUM_BINS = 120;
  const binSize = (pHigh - pLow) / NUM_BINS;
  if (binSize <= 0) return;

  // Build separate up/down histograms for delta coloring
  const binsUp = new Float64Array(NUM_BINS);
  const binsDown = new Float64Array(NUM_BINS);
  for (const bar of histBars) {
    const lo = Math.max(0, Math.min(NUM_BINS - 1, Math.floor((bar.low - pLow) / binSize)));
    const hi = Math.max(0, Math.min(NUM_BINS - 1, Math.floor((bar.high - pLow) / binSize)));
    const span = hi - lo + 1;
    const volPerBin = bar.volume / span;
    const isUp = bar.close >= bar.open;
    for (let b = lo; b <= hi; b++) {
      if (isUp) binsUp[b] += volPerBin;
      else binsDown[b] += volPerBin;
    }
  }

  // Total volume per bin
  const bins = new Float64Array(NUM_BINS);
  for (let i = 0; i < NUM_BINS; i++) bins[i] = binsUp[i] + binsDown[i];

  // Find POC (max volume bin) and compute Value Area (70%)
  let pocBin = 0;
  let maxVol = 0;
  let totalVol = 0;
  for (let i = 0; i < NUM_BINS; i++) {
    totalVol += bins[i];
    if (bins[i] > maxVol) { maxVol = bins[i]; pocBin = i; }
  }
  if (maxVol === 0) return;

  // Value Area: expand from POC until 70% of volume
  const vaTarget = totalVol * 0.70;
  let vaVol = bins[pocBin];
  let vaLow = pocBin;
  let vaHigh = pocBin;
  while (vaVol < vaTarget && (vaLow > 0 || vaHigh < NUM_BINS - 1)) {
    const belowVol = vaLow > 0 ? bins[vaLow - 1] : 0;
    const aboveVol = vaHigh < NUM_BINS - 1 ? bins[vaHigh + 1] : 0;
    if (belowVol >= aboveVol && vaLow > 0) {
      vaLow--;
      vaVol += bins[vaLow];
    } else if (vaHigh < NUM_BINS - 1) {
      vaHigh++;
      vaVol += bins[vaHigh];
    } else {
      vaLow--;
      vaVol += bins[vaLow];
    }
  }

  // Max bar width = 35% of chart width, anchored to right edge
  const maxBarW = chartW * 0.35;
  const xVP = (vol: number) => (vol / maxVol) * maxBarW;
  const barH = Math.max(1, priceH / NUM_BINS - 0.5);

  // Value Area shading (full width)
  const vaLowPrice = pLow + vaLow * binSize;
  const vaHighPrice = pLow + (vaHigh + 1) * binSize;
  gVolProfile.append("rect")
    .attr("x", 0)
    .attr("y", yPrice(vaHighPrice))
    .attr("width", chartW)
    .attr("height", yPrice(vaLowPrice) - yPrice(vaHighPrice))
    .attr("fill", "#6366f1")
    .attr("opacity", 0.04);

  // Histogram bars — right-anchored, split into up (green) / down (red)
  for (let i = 0; i < NUM_BINS; i++) {
    if (bins[i] === 0) continue;
    const y = yPrice(pLow + (i + 1) * binSize);
    const totalW = xVP(bins[i]);
    const upW = bins[i] > 0 ? (binsUp[i] / bins[i]) * totalW : 0;
    const downW = totalW - upW;
    const isPOC = i === pocBin;
    const inVA = i >= vaLow && i <= vaHigh;
    const opac = isPOC ? 0.45 : inVA ? 0.28 : 0.18;

    // Down (red) portion — leftmost within the bar
    if (downW > 0) {
      gVolProfile.append("rect")
        .attr("x", chartW - totalW)
        .attr("y", y)
        .attr("width", downW)
        .attr("height", barH)
        .attr("fill", C("--candle-down"))
        .attr("opacity", opac)
        .attr("rx", 1);
    }
    // Up (green) portion — rightmost within the bar
    if (upW > 0) {
      gVolProfile.append("rect")
        .attr("x", chartW - upW)
        .attr("y", y)
        .attr("width", upW)
        .attr("height", barH)
        .attr("fill", C("--candle-up"))
        .attr("opacity", opac)
        .attr("rx", 1);
    }
  }

  // POC line across full chart
  const pocPrice = pLow + (pocBin + 0.5) * binSize;
  gVolProfile.append("line")
    .attr("x1", 0).attr("x2", chartW)
    .attr("y1", yPrice(pocPrice)).attr("y2", yPrice(pocPrice))
    .attr("stroke", "#6366f1")
    .attr("stroke-width", 1)
    .attr("stroke-dasharray", "6,3")
    .attr("opacity", 0.5);

  // POC label
  gVolProfile.append("text")
    .attr("x", 4)
    .attr("y", yPrice(pocPrice) - 3)
    .attr("text-anchor", "start")
    .attr("font-size", "9px")
    .attr("fill", "#6366f1")
    .attr("opacity", 0.7)
    .text(`POC ${pocPrice.toFixed(2)}`);

  // VAH label
  gVolProfile.append("text")
    .attr("x", 4)
    .attr("y", yPrice(vaHighPrice) - 3)
    .attr("text-anchor", "start")
    .attr("font-size", "9px")
    .attr("fill", "#818cf8")
    .attr("opacity", 0.6)
    .text(`VAH ${vaHighPrice.toFixed(2)}`);

  // VAL label
  gVolProfile.append("text")
    .attr("x", 4)
    .attr("y", yPrice(vaLowPrice) + 10)
    .attr("text-anchor", "start")
    .attr("font-size", "9px")
    .attr("fill", "#818cf8")
    .attr("opacity", 0.6)
    .text(`VAL ${vaLowPrice.toFixed(2)}`);
}

// ── SMA Lines (50 & 200) ────────────────────────────────────────────────
function drawSMA(visible: Bar[]) {
  gSMA.selectAll("*").remove();
  if (!showSMA || visible.length === 0) return;

  const candleW = xScale.bandwidth();
  const line = d3.line<[string, number]>()
    .defined(d => !isNaN(d[1]))
    .x(d => (xScale(d[0]) ?? 0) + candleW / 2)
    .y(d => yPrice(d[1]));

  const data50: [string, number][] = [];
  const data200: [string, number][] = [];
  for (const bar of visible) {
    const idx = barIdxByDate.get(bar.date);
    if (idx !== undefined) {
      data50.push([bar.date, sma50[idx]]);
      data200.push([bar.date, sma200[idx]]);
    }
  }

  // SMA50 — orange
  gSMA.append("path")
    .datum(data50).attr("fill", "none")
    .attr("stroke", "#f59e0b").attr("stroke-width", 1.5)
    .attr("opacity", 0.8).attr("d", line);

  // SMA200 — purple
  gSMA.append("path")
    .datum(data200).attr("fill", "none")
    .attr("stroke", "#a855f7").attr("stroke-width", 1.5)
    .attr("opacity", 0.8).attr("d", line);

  // End labels
  const last50 = data50.filter(d => !isNaN(d[1]));
  const last200 = data200.filter(d => !isNaN(d[1]));
  if (last50.length > 0) {
    const [date, val] = last50[last50.length - 1];
    gSMA.append("text")
      .attr("x", (xScale(date) ?? 0) + candleW / 2 + 4)
      .attr("y", yPrice(val) + 3)
      .attr("font-size", "9px").attr("fill", "#f59e0b").attr("opacity", 0.9)
      .text(`SMA50`);
  }
  if (last200.length > 0) {
    const [date, val] = last200[last200.length - 1];
    gSMA.append("text")
      .attr("x", (xScale(date) ?? 0) + candleW / 2 + 4)
      .attr("y", yPrice(val) + 3)
      .attr("font-size", "9px").attr("fill", "#a855f7").attr("opacity", 0.9)
      .text(`SMA200`);
  }
}

// ── Anchored VWAP ──────────────────────────────────────────────────────
function drawAVWAP(visible: Bar[], sliderDate: string) {
  gAVWAP.selectAll("*").remove();
  if (!showAVWAP || visible.length === 0) return;

  // Find anchor points: highest high and lowest low in lookback portion
  const sliderPos = visible.findIndex(b => b.date > sliderDate);
  const histEnd = sliderPos === -1 ? visible.length : sliderPos;
  if (histEnd < 5) return;

  let highIdx = 0, lowIdx = 0;
  for (let i = 0; i < histEnd; i++) {
    if (visible[i].high > visible[highIdx].high) highIdx = i;
    if (visible[i].low < visible[lowIdx].low) lowIdx = i;
  }

  const candleW = xScale.bandwidth();
  const line = d3.line<[string, number]>()
    .x(d => (xScale(d[0]) ?? 0) + candleW / 2)
    .y(d => yPrice(d[1]));

  // Compute VWAP from anchor index onward
  const computeVwap = (anchorIdx: number): [string, number][] => {
    const points: [string, number][] = [];
    let cumTPV = 0, cumV = 0;
    for (let i = anchorIdx; i < visible.length; i++) {
      const bar = visible[i];
      const tp = (bar.high + bar.low + bar.close) / 3;
      cumTPV += tp * bar.volume;
      cumV += bar.volume;
      if (cumV > 0) points.push([bar.date, cumTPV / cumV]);
    }
    return points;
  };

  // AVWAP from swing high (resistance) — red dashed
  const vwapHigh = computeVwap(highIdx);
  if (vwapHigh.length > 1) {
    gAVWAP.append("path")
      .datum(vwapHigh).attr("fill", "none")
      .attr("stroke", "#ef4444").attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "4,2").attr("opacity", 0.7)
      .attr("d", line);
    const last = vwapHigh[vwapHigh.length - 1];
    gAVWAP.append("text")
      .attr("x", (xScale(last[0]) ?? 0) + candleW / 2 + 4)
      .attr("y", yPrice(last[1]) + 3)
      .attr("font-size", "9px").attr("fill", "#ef4444").attr("opacity", 0.8)
      .text("AVWAP↓");
  }

  // AVWAP from swing low (support) — green dashed
  const vwapLow = computeVwap(lowIdx);
  if (vwapLow.length > 1) {
    gAVWAP.append("path")
      .datum(vwapLow).attr("fill", "none")
      .attr("stroke", "#22c55e").attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "4,2").attr("opacity", 0.7)
      .attr("d", line);
    const last = vwapLow[vwapLow.length - 1];
    gAVWAP.append("text")
      .attr("x", (xScale(last[0]) ?? 0) + candleW / 2 + 4)
      .attr("y", yPrice(last[1]) + 3)
      .attr("font-size", "9px").attr("fill", "#22c55e").attr("opacity", 0.8)
      .text("AVWAP↑");
  }
}

// ── LLM Key Levels ─────────────────────────────────────────────────────
function drawLLMLevels() {
  gLLMLevels.selectAll("*").remove();
  if (!llmLevels || !yPrice) return;

  const chartW = W - margin.left - margin.right;

  const drawLevel = (price: number, color: string) => {
    const y = yPrice(price);
    gLLMLevels.append("line")
      .attr("x1", 0).attr("x2", chartW)
      .attr("y1", y).attr("y2", y)
      .attr("stroke", color).attr("stroke-width", 1)
      .attr("stroke-dasharray", "4,3").attr("opacity", 0.7);
    gLLMLevels.append("text")
      .attr("x", chartW - 4).attr("y", y - 2)
      .attr("font-size", "9px").attr("fill", color).attr("opacity", 0.85)
      .attr("text-anchor", "end")
      .text(price.toFixed(2));
  };

  if (isLayerActive("llm_levels")) {
    (llmLevels.resistance || []).forEach(p => drawLevel(p, "#ef4444"));
    (llmLevels.support    || []).forEach(p => drawLevel(p, "#22c55e"));
  }
}

// ── RSI Panel ──────────────────────────────────────────────────────────
function drawRSI(visible: Bar[], sliderDate: string) {
  gRSI.selectAll("*").remove();
  gYRSIAxis.selectAll("*").remove();
  if (!showRSI || visible.length === 0) return;

  const chartW = W - margin.left - margin.right;
  const textDim = C("--text-dim");
  const axisColor = C("--border");
  const gridColor = C("--grid");

  const yRSI = d3.scaleLinear().domain([0, 100]).range([rsiHeight, 0]);

  // Overbought / oversold shading
  gRSI.append("rect")
    .attr("x", 0).attr("y", yRSI(100))
    .attr("width", chartW).attr("height", yRSI(70) - yRSI(100))
    .attr("fill", C("--candle-down")).attr("opacity", 0.06);
  gRSI.append("rect")
    .attr("x", 0).attr("y", yRSI(30))
    .attr("width", chartW).attr("height", yRSI(0) - yRSI(30))
    .attr("fill", C("--candle-up")).attr("opacity", 0.06);

  // Reference lines at 30, 50, 70
  [30, 50, 70].forEach(level => {
    gRSI.append("line")
      .attr("x1", 0).attr("x2", chartW)
      .attr("y1", yRSI(level)).attr("y2", yRSI(level))
      .attr("stroke", gridColor)
      .attr("stroke-dasharray", level === 50 ? "4,4" : "2,4")
      .attr("opacity", level === 50 ? 0.6 : 0.4);
  });

  // RSI line
  const candleW = xScale.bandwidth();
  const rsiLine = d3.line<[string, number]>()
    .defined(d => !isNaN(d[1]))
    .x(d => (xScale(d[0]) ?? 0) + candleW / 2)
    .y(d => yRSI(d[1]));

  const data: [string, number][] = [];
  for (const bar of visible) {
    const idx = barIdxByDate.get(bar.date);
    if (idx !== undefined) data.push([bar.date, rsiValues[idx]]);
  }

  const histData = data.filter(d => d[0] <= sliderDate);
  const futureData = data.filter(d => d[0] > sliderDate);

  if (histData.length > 0) {
    gRSI.append("path")
      .datum(histData).attr("fill", "none")
      .attr("stroke", "#6366f1").attr("stroke-width", 1.5)
      .attr("d", rsiLine);
  }
  if (futureData.length > 0) {
    const connected = histData.length > 0
      ? [histData[histData.length - 1], ...futureData] : futureData;
    gRSI.append("path")
      .datum(connected).attr("fill", "none")
      .attr("stroke", "#6366f1").attr("stroke-width", 1)
      .attr("opacity", 0.3).attr("d", rsiLine);
  }

  // RSI label
  gRSI.append("text")
    .attr("x", 4).attr("y", 10)
    .attr("font-size", "9px").attr("fill", "#6366f1").attr("opacity", 0.7)
    .text("RSI(14)");

  // RSI Y-axis
  const rsiAxis = d3.axisRight(yRSI).tickValues([30, 50, 70]).tickFormat(d3.format("d"));
  gYRSIAxis.call(rsiAxis);
  gYRSIAxis.selectAll("text").attr("fill", textDim);
  gYRSIAxis.selectAll("line, path").attr("stroke", axisColor);
}

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
  // Collect active patterns for this date (only in lookback)
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
  activeTfDays = days;
  activeLfDays = null;
  const total = bars.length;
  // Convert daily trading days to bar count for current interval
  let count: number;
  if (timeframe === "weekly") count = Math.round(days / 5);
  else if (timeframe === "monthly") count = Math.round(days / 21);
  else count = days;
  count = Math.min(count, total);
  const end = sliderIndex;
  const start = Math.max(0, end - count + 1);
  visibleRange = [start, end];

  // Sync zoom slider
  const zoomVal = Math.round(((total - (end - start + 1)) / (total - MIN_VISIBLE_BARS)) * 100);
  (document.getElementById("zoom-slider") as HTMLInputElement).value = String(Math.max(0, Math.min(100, zoomVal)));
  document.getElementById("zoom-label")!.textContent = `${Math.round(((end - start + 1) / total) * 100)}%`;

  // Highlight active button
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".lf-btn").forEach(b => b.classList.remove("active"));
  document.querySelector(`.tf-btn[data-days="${days}"]`)?.classList.add("active");

  syncNavSlider();
  draw();
}

function showLookforward(days: number) {
  activeLfDays = days;
  activeTfDays = null;
  const total = bars.length;
  let count: number;
  if (timeframe === "weekly") count = Math.round(days / 5);
  else if (timeframe === "monthly") count = Math.round(days / 21);
  else count = days;
  // Start from current NOW position, show lookforward
  const start = sliderIndex;
  const end = Math.min(total - 1, start + count - 1);
  visibleRange = [start, end];

  // Sync zoom slider
  const zoomVal = Math.round(((total - (end - start + 1)) / (total - MIN_VISIBLE_BARS)) * 100);
  (document.getElementById("zoom-slider") as HTMLInputElement).value = String(Math.max(0, Math.min(100, zoomVal)));
  document.getElementById("zoom-label")!.textContent = `${Math.round(((end - start + 1) / total) * 100)}%`;

  // Highlight active button
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".lf-btn").forEach(b => b.classList.remove("active"));
  document.querySelector(`.lf-btn[data-days="${days}"]`)?.classList.add("active");

  syncNavSlider();
  draw();
}

// ── Trading days toggle ──────────────────────────────────────────────────
document.getElementById("td-toggle")!.addEventListener("change", (e) => {
  dateMode = (e.target as HTMLInputElement).checked ? "trading" : "calendar";
  draw();
});

// ── Volume Profile toggle ────────────────────────────────────────────────
document.getElementById("vp-toggle")!.addEventListener("change", (e) => {
  showVolProfile = (e.target as HTMLInputElement).checked;
  draw();
});

// ── SMA toggle ──────────────────────────────────────────────────────────
document.getElementById("sma-toggle")!.addEventListener("change", (e) => {
  showSMA = (e.target as HTMLInputElement).checked;
  draw();
});

// ── RSI toggle ──────────────────────────────────────────────────────────
document.getElementById("rsi-toggle")!.addEventListener("change", (e) => {
  showRSI = (e.target as HTMLInputElement).checked;
  draw();
});

// ── AVWAP toggle ────────────────────────────────────────────────────────
document.getElementById("avwap-toggle")!.addEventListener("change", (e) => {
  showAVWAP = (e.target as HTMLInputElement).checked;
  draw();
});


document.querySelectorAll(".tf-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const days = parseInt((btn as HTMLElement).dataset.days!);
    showTimeframe(days);
  });
});

document.querySelectorAll(".lf-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const days = parseInt((btn as HTMLElement).dataset.days!);
    showLookforward(days);
  });
});

// ── Interval buttons (D/W/M) ────────────────────────────────────────────
document.querySelectorAll(".interval-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tf = (btn as HTMLElement).dataset.interval as "daily" | "weekly" | "monthly";
    switchTimeframe(tf);
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

// ── CLI sidecar command poll ──────────────────────────────────────────────
function startCommandPoll() {
  setInterval(async () => {
    try {
      const resp = await fetch("/api/commands/poll");
      const cmds = await resp.json();
      for (const cmd of cmds) await executeCommand(cmd);
    } catch { /* server not ready */ }
  }, 200);
}

async function executeCommand(cmd: { action: string; [key: string]: unknown }) {
  switch (cmd.action) {
    case "ticker":
      await loadTicker(String(cmd.value).toUpperCase());
      break;

    case "gtd":
      goToDate(String(cmd.value));
      break;

    case "lookback": {
      const presetMap: Record<string, number> = { "3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504 };
      const days = presetMap[String(cmd.value).toUpperCase()];
      if (days) showTimeframe(days);
      break;
    }

    case "lookforward": {
      const presetMap: Record<string, number> = { "3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504 };
      const days = presetMap[String(cmd.value).toUpperCase()];
      if (days) showLookforward(days);
      break;
    }

    case "vdr": {
      const s = String(cmd.start);
      const e = String(cmd.end);
      // Reuse the VDR inputs so applyVDR handles nearest-date logic
      (document.getElementById("vdr-start") as HTMLInputElement).value = s;
      (document.getElementById("vdr-end") as HTMLInputElement).value = e;
      applyVDR();
      break;
    }

    case "interval": {
      const v = String(cmd.value).toUpperCase();
      const map: Record<string, "daily" | "weekly" | "monthly"> = { D: "daily", W: "weekly", M: "monthly" };
      const tf = map[v];
      if (tf) {
        switchTimeframe(tf);
        document.querySelectorAll(".interval-btn").forEach(b => b.classList.toggle("active", b.getAttribute("data-interval") === tf));
      }
      break;
    }

    case "toggle": {
      const key = String(cmd.key);
      const on = cmd.value === true || cmd.value === "on";
      if (key === "trading-days") {
        dateMode = on ? "trading" : "calendar";
        (document.getElementById("td-toggle") as HTMLInputElement).checked = on;
      } else if (key === "vol-profile") {
        showVolProfile = on;
        (document.getElementById("vp-toggle") as HTMLInputElement).checked = on;
      } else if (key === "sma") {
        showSMA = on;
        (document.getElementById("sma-toggle") as HTMLInputElement).checked = on;
      } else if (key === "rsi") {
        showRSI = on;
        (document.getElementById("rsi-toggle") as HTMLInputElement).checked = on;
      } else if (key === "avwap") {
        showAVWAP = on;
        (document.getElementById("avwap-toggle") as HTMLInputElement).checked = on;
      } else if (key === "llm-levels") {
        const ll = layers.find(l => l.key === "llm_levels");
        if (ll) ll.active = on;
        renderLayerPanel();
      }
      draw();
      break;
    }

    case "key_levels": {
      llmLevels = {
        resistance: Array.isArray(cmd.resistance) ? cmd.resistance.map(Number) : [],
        support: Array.isArray(cmd.support) ? cmd.support.map(Number) : [],
      };
      llmLevelsKey = currentLLMKey();
      _llmCheckedKey = null;
      // activate=false means cache auto-load — preserve user's eye state
      if (cmd.activate !== false) {
        layers.find(l => l.key === "llm_levels")!.active = true;
      }
      draw();
      break;
    }

    case "layer": {
      const key = String(cmd.key);
      const on = cmd.value === true || cmd.value === "on";
      const layer = layers.find(l => l.key === key);
      if (layer) {
        layer.active = on;
        renderLayerPanel();
        draw();
      }
      break;
    }

    case "snapshot":
      takeSnapshot(cmd.save_dir ? String(cmd.save_dir) : null, cmd.prefix ? String(cmd.prefix) : undefined);
      break;

    case "zoom": {
      const count = Number(cmd.value);
      if (count > 0) zoomCenteredOnNow(count);
      break;
    }

    case "reset": {
      const ticker = cmd.ticker ? String(cmd.ticker).toUpperCase() : currentTicker;
      // Reset all state variables to defaults
      timeframe = DEFAULTS.interval;
      dateMode = DEFAULTS.dateMode;
      showVolProfile = DEFAULTS.showVolProfile;
      showSMA = DEFAULTS.showSMA;
      showRSI = DEFAULTS.showRSI;
      showAVWAP = DEFAULTS.showAVWAP;
      activeTfDays = null;
      activeLfDays = null;
      // Sync UI toggles
      (document.getElementById("td-toggle") as HTMLInputElement).checked = false;
      (document.getElementById("vp-toggle") as HTMLInputElement).checked = false;
      (document.getElementById("sma-toggle") as HTMLInputElement).checked = false;
      (document.getElementById("rsi-toggle") as HTMLInputElement).checked = false;
      (document.getElementById("avwap-toggle") as HTMLInputElement).checked = false;
      llmLevels = null;
      llmLevelsKey = null;
      _llmCheckedKey = null;
      _llmPendingKey = null;
      layers.find(l => l.key === "llm_levels")!.active = false;
      // Reload ticker data fresh (force=true bypasses same-ticker guard)
      await loadTicker(ticker, true);
      break;
    }

    default:
      console.warn("Unknown CLI command:", cmd);
  }
}

// ── Ticker search modal (Cmd+K) ──────────────────────────────────────────
function openTickerSearch() {
  // Don't open if already open
  if (document.getElementById("ticker-modal")) return;

  const overlay = document.createElement("div");
  overlay.id = "ticker-modal";
  overlay.innerHTML = `
    <div class="ticker-modal-backdrop"></div>
    <div class="ticker-modal-content">
      <div class="ticker-modal-header">
        <input id="ticker-search-input" type="text" placeholder="Search ticker..." autocomplete="off" spellcheck="false" />
        <kbd>ESC</kbd>
      </div>
      <div id="ticker-search-results" class="ticker-modal-results"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  const input = overlay.querySelector("#ticker-search-input") as HTMLInputElement;
  const resultsEl = overlay.querySelector("#ticker-search-results") as HTMLElement;
  let selectedIdx = 0;

  function renderResults(query: string) {
    let q = query.toUpperCase().trim();
    let exchangeFilter: string | null = null;

    // Support "NYSE:XXX", "NASDAQ:XXX", "NSE:XXX" prefix search
    const colonIdx = q.indexOf(":");
    if (colonIdx > 0) {
      const prefix = q.slice(0, colonIdx);
      const symbol = q.slice(colonIdx + 1).trim();
      if (prefix === "NYSE" || prefix === "NASDAQ") {
        exchangeFilter = "US";
      } else if (prefix === "NSE") {
        exchangeFilter = "NSE";
      }
      q = symbol;
    }

    let filtered = tickers.filter(t => {
      if (exchangeFilter && t.exchange !== exchangeFilter) return false;
      return !q || t.ticker.includes(q);
    });
    // Sort: exact prefix matches first
    filtered.sort((a, b) => {
      const aStart = a.ticker.startsWith(q) ? 0 : 1;
      const bStart = b.ticker.startsWith(q) ? 0 : 1;
      return aStart - bStart || a.ticker.localeCompare(b.ticker);
    });
    selectedIdx = 0;

    const flag = (ex: string) => ex === "NSE" ? "\uD83C\uDDEE\uD83C\uDDF3" : "\uD83C\uDDFA\uD83C\uDDF8";

    resultsEl.innerHTML = filtered.slice(0, 50).map((t, i) =>
      `<div class="ticker-modal-item${i === 0 ? " selected" : ""}${t.ticker === currentTicker ? " current" : ""}" data-ticker="${t.ticker}">
        <span class="ticker-modal-symbol">${t.ticker}</span>
        <span class="ticker-modal-exchange">${flag(t.exchange)} ${t.exchange}</span>
      </div>`
    ).join("");

    if (filtered.length === 0) {
      resultsEl.innerHTML = `<div class="ticker-modal-empty">No tickers found</div>`;
    }
  }

  function updateSelection() {
    const items = resultsEl.querySelectorAll(".ticker-modal-item");
    items.forEach((el, i) => el.classList.toggle("selected", i === selectedIdx));
    items[selectedIdx]?.scrollIntoView({ block: "nearest" });
  }

  async function selectTicker() {
    const items = resultsEl.querySelectorAll(".ticker-modal-item");
    const item = items[selectedIdx] as HTMLElement | undefined;
    if (!item) return;
    const ticker = item.dataset.ticker!;
    close();
    if (ticker !== currentTicker) {
      await loadTicker(ticker);
    }
  }

  function close() {
    overlay.remove();
  }

  input.addEventListener("input", () => renderResults(input.value));

  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    const items = resultsEl.querySelectorAll(".ticker-modal-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedIdx = Math.min(selectedIdx + 1, items.length - 1);
      updateSelection();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedIdx = Math.max(selectedIdx - 1, 0);
      updateSelection();
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectTicker();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });

  resultsEl.addEventListener("click", (e) => {
    const item = (e.target as HTMLElement).closest(".ticker-modal-item") as HTMLElement | null;
    if (!item) return;
    selectedIdx = Array.from(resultsEl.children).indexOf(item);
    selectTicker();
  });

  overlay.querySelector(".ticker-modal-backdrop")!.addEventListener("click", close);

  renderResults("");
  input.focus();
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  // Cmd+K : open ticker search modal
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    openTickerSearch();
    return;
  }

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
    // Auto-pan window to keep NOW visible
    const visibleCount = visibleRange[1] - visibleRange[0] + 1;
    if (sliderIndex < visibleRange[0]) {
      const start = Math.max(0, sliderIndex);
      visibleRange = [start, start + visibleCount - 1];
      syncNavSlider();
    } else if (sliderIndex > visibleRange[1]) {
      const end = Math.min(bars.length - 1, sliderIndex);
      visibleRange = [end - visibleCount + 1, end];
      syncNavSlider();
    }
    draw();
  }
});

// ── GoToDate ─────────────────────────────────────────────────────────────
/** Find bar index for a date. Exact match first, else next trading day on or after target. */
function findBarIdx(target: string): number | undefined {
  let idx = barIdxByDate.get(target);
  if (idx !== undefined) return idx;
  const t = new Date(target).getTime();
  for (let i = 0; i < bars.length; i++) {
    if (new Date(bars[i].date).getTime() >= t) return i;
  }
  // Target is after all bars — return last bar
  return bars.length > 0 ? bars.length - 1 : undefined;
}

function goToDate(target: string) {
  const idx = findBarIdx(target);
  if (idx === undefined) return;
  sliderIndex = idx;
  const vc = visibleRange[1] - visibleRange[0] + 1;
  if (sliderIndex < visibleRange[0] || sliderIndex > visibleRange[1]) {
    const half = Math.floor(vc / 2);
    let s = Math.max(0, sliderIndex - half);
    let e = s + vc - 1;
    if (e >= bars.length) { e = bars.length - 1; s = e - vc + 1; }
    visibleRange = [Math.max(0, s), e];
  }
  syncNavSlider();
  draw();
  positionNowHandle();
}

document.getElementById("goto-date")!.addEventListener("change", (e) => {
  const target = (e.target as HTMLInputElement).value;
  if (target) goToDate(target);
});

// ── Visible Date Range (VDR) ─────────────────────────────────────────────
function applyVDR() {
  const startVal = (document.getElementById("vdr-start") as HTMLInputElement).value;
  const endVal = (document.getElementById("vdr-end") as HTMLInputElement).value;
  if (!startVal || !endVal) return;

  // Find bar indices: next trading day on or after target
  let si = findBarIdx(startVal);
  let ei = findBarIdx(endVal);
  if (si === undefined) si = 0;
  if (ei === undefined) ei = bars.length - 1;

  if (si > ei) [si, ei] = [ei, si];
  visibleRange = [si, ei];
  activeTfDays = null;
  activeLfDays = null;
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".lf-btn").forEach(b => b.classList.remove("active"));

  // Sync zoom slider
  const total = bars.length;
  const zoomVal = Math.round(((total - (ei - si + 1)) / (total - MIN_VISIBLE_BARS)) * 100);
  (document.getElementById("zoom-slider") as HTMLInputElement).value = String(Math.max(0, Math.min(100, zoomVal)));
  document.getElementById("zoom-label")!.textContent = `${Math.round(((ei - si + 1) / total) * 100)}%`;

  syncNavSlider();
  draw();
}

document.getElementById("vdr-start")!.addEventListener("change", applyVDR);
document.getElementById("vdr-end")!.addEventListener("change", applyVDR);

// ── Snapshot (PNG + CSV) ─────────────────────────────────────────────────
function buildSnapshotCsv(): string {
  const [vi0, vi1] = visibleRange;
  const visible = bars.slice(vi0, vi1 + 1);
  const sliderDate = bars[sliderIndex]?.date ?? "";

  const csvRows: string[] = [];
  const hdr = ["date", "open", "high", "low", "close", "volume"];
  if (showSMA) hdr.push("sma50", "sma200");
  if (showRSI) hdr.push("rsi");
  hdr.push("candlestick_patterns", "geometric_patterns", "signals", "divergences", "gaps", "support_levels", "resistance_levels");
  csvRows.push(hdr.join(","));

  for (let idx = vi0; idx <= vi1; idx++) {
    const bar = bars[idx];
    const d = bar.date;
    const inLookback = d <= sliderDate;

    const dayLabel = dateMode === "trading"
      ? String(idx - sliderIndex)
      : d;

    let candles = "";
    let geos = "";
    let sigs = "";
    let divs = "";
    let gapsStr = "";

    if (inLookback && result) {
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
      if (gp) gapsStr = gp.map(g => `${g.pattern}(${g.gap_pct.toFixed(1)}%)`).join("; ");
    }

    const supports: number[] = [];
    const resistances: number[] = [];
    if (inLookback) {
      for (const z of srMerged) {
        if (z.start_date <= d && (z.end_date >= d || (!z.broken))) {
          if (z.broken && z.broken_date && z.broken_date < d) continue;
          if (z.type === "support") supports.push(z.level);
          else resistances.push(z.level);
        }
      }
    }

    const esc = (s: string) => `"${s.replace(/"/g, '""')}"`;
    const cols: string[] = [
      dayLabel, bar.open.toFixed(2), bar.high.toFixed(2), bar.low.toFixed(2), bar.close.toFixed(2), String(bar.volume),
    ];
    if (showSMA) {
      cols.push(isNaN(sma50[idx]) ? "" : String(sma50[idx]));
      cols.push(isNaN(sma200[idx]) ? "" : String(sma200[idx]));
    }
    if (showRSI) {
      cols.push(isNaN(rsiValues[idx]) ? "" : String(rsiValues[idx]));
    }
    cols.push(esc(candles), esc(geos), esc(sigs), esc(divs), esc(gapsStr),
      esc(supports.map(l => l.toFixed(2)).join("; ")),
      esc(resistances.map(l => l.toFixed(2)).join("; ")),
    );
    csvRows.push(cols.join(","));
  }

  // Volume profile summary
  if (showVolProfile) {
    const histBars = visible.filter(b => b.date <= sliderDate);
    const vp = computeVolumeProfileStats(histBars);
    if (vp) {
      csvRows.push("");
      csvRows.push(`# Volume Profile: POC=${vp.poc} VAH=${vp.vah} VAL=${vp.val}`);
    }
  }

  return csvRows.join("\n");
}

async function takeSnapshot(saveDir: string | null = null, customPrefix?: string) {
  const sliderDate = bars[sliderIndex]?.date ?? "";
  const prefix = customPrefix || `${currentTicker}_${sliderDate.slice(0, 10)}`;

  const { default: html2canvas } = await import("html2canvas");
  const el = document.getElementById("snapshot-region")!;
  const canvas = await html2canvas(el, { backgroundColor: "#ffffff", scale: 2 });
  const csv = buildSnapshotCsv();

  if (saveDir) {
    // CLI-triggered: POST data back to server for disk save
    const pngDataUrl = canvas.toDataURL("image/png");
    await fetch("/api/snapshot/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ save_dir: saveDir, prefix, png: pngDataUrl, csv }),
    });
  } else {
    // Browser-triggered: download via blob
    canvas.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${prefix}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    }, "image/png");

    const csvBlob = new Blob([csv], { type: "text/csv" });
    const csvA = document.createElement("a");
    csvA.href = URL.createObjectURL(csvBlob);
    csvA.download = `${prefix}.csv`;
    csvA.click();
    URL.revokeObjectURL(csvA.href);
  }
}

document.getElementById("snapshot-btn")!.addEventListener("click", () => takeSnapshot());

// ── Key Levels (LLM S/R analysis) ──────────────────────────────────────
async function requestKeyLevels() {
  const btn = document.getElementById("keylevels-btn") as HTMLButtonElement;

  const sliderDate = bars[sliderIndex]?.date ?? "";
  const [v0, v1] = visibleRange;
  const vdrStart = bars[v0]?.date ?? "";
  const vdrEnd   = bars[v1]?.date ?? "";

  btn.disabled = true;
  btn.textContent = "Analysing…";
  _llmCheckedKey = null;  // force fresh poll even if we already checked this key

  try {
    const { default: html2canvas } = await import("html2canvas");
    const el = document.getElementById("snapshot-region")!;
    const canvas = await html2canvas(el, { backgroundColor: "#ffffff", scale: 2 });
    const png = canvas.toDataURL("image/png");
    const csv = buildSnapshotCsv();

    const resp = await fetch("/api/keylevels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        png, csv,
        ticker: currentTicker,
        date: sliderDate,
        vdr_start: vdrStart,
        vdr_end: vdrEnd,
        interval: timeframe,
        trading_days: dateMode === "trading",
        t_lookback: sliderIndex - v0,
        t_lookforward: v1 - sliderIndex,
        force: true,  // always re-run LLM, overwrite cache
      }),
    });
    const init = await resp.json();
    if (!init.ok) { console.error("keylevels start failed:", init); return; }

    // Poll for result (covers both cache-hit and pending cases)
    let result: any = null;
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1000));
      const r = await fetch("/api/keylevels/result");
      const data = await r.json();
      if (data) { result = data; break; }
    }

    if (!result?.ok) { console.error("keylevels failed:", result); return; }

    await executeCommand({
      action: "key_levels",
      resistance: result.resistance,
      support: result.support,
    });
  } finally {
    btn.textContent = "📍 Key Levels";
    btn.disabled = false;
  }
}

document.getElementById("keylevels-btn")!.addEventListener("click", requestKeyLevels);

// ── Trade Simulator ──────────────────────────────────────────────────────
interface SimParams {
  ticker: string;
  simDate: string;
  simBarIdx: number;
  interval: "daily" | "weekly" | "monthly";
  entryType: "market" | "limit";
  entryPrice?: number;
  maxDaysToEnter?: number;
  tp?: number;
  sl?: number;
  direction: "long" | "short";
  maxExitDays?: number;
}

interface SimResult {
  params: SimParams;
  runAt: string;
  entryTriggered: boolean;
  entryDate?: string;
  actualEntryPrice?: number;
  entryBarIdx?: number;
  exited: boolean;
  exitDate?: string;
  exitPrice?: number;
  exitReason?: "tp" | "sl" | "sl_tp_same_bar" | "max_days" | "end_of_data";
  pnlPct?: number;
  pnlAbs?: number;
  durationBars?: number;
  warnings: string[];
}

let _lastSimResult: SimResult | null = null;

function runSimulation(p: SimParams): SimResult {
  const res: SimResult = {
    params: p, runAt: new Date().toISOString(),
    entryTriggered: false, exited: false, warnings: [],
  };

  // ── Find entry ──
  let entryBarIdx = -1;
  let actualEntryPrice = 0;

  if (p.entryType === "market") {
    const i = p.simBarIdx + 1;
    if (i >= bars.length) {
      res.warnings.push("No bar after T+0 — end of available data.");
      return res;
    }
    entryBarIdx = i;
    actualEntryPrice = bars[i].open;
  } else {
    // Limit: search T+0 through T+0+(maxDaysToEnter-1) inclusive
    const end = Math.min(bars.length - 1, p.simBarIdx + (p.maxDaysToEnter! - 1));
    for (let i = p.simBarIdx; i <= end; i++) {
      const b = bars[i];
      if (p.direction === "long" && b.low <= p.entryPrice!) {
        entryBarIdx = i; actualEntryPrice = p.entryPrice!; break;
      }
      if (p.direction === "short" && b.high >= p.entryPrice!) {
        entryBarIdx = i; actualEntryPrice = p.entryPrice!; break;
      }
    }
    if (entryBarIdx === -1) return res; // entry never triggered
  }

  res.entryTriggered = true;
  res.entryBarIdx = entryBarIdx;
  res.actualEntryPrice = actualEntryPrice;
  res.entryDate = bars[entryBarIdx].date;

  // ── Simulate forward ──
  const exitDeadline = p.maxExitDays !== undefined
    ? Math.min(bars.length - 1, p.simBarIdx + p.maxExitDays)
    : bars.length - 1;

  for (let i = entryBarIdx + 1; i <= exitDeadline; i++) {
    const b = bars[i];
    let tpHit = false, slHit = false;
    if (p.direction === "long") {
      if (p.sl !== undefined && b.low  <= p.sl) slHit = true;
      if (p.tp !== undefined && b.high >= p.tp) tpHit = true;
    } else {
      if (p.sl !== undefined && b.high >= p.sl) slHit = true;
      if (p.tp !== undefined && b.low  <= p.tp) tpHit = true;
    }

    if (slHit && tpHit) {
      res.exited = true; (res as any).exitBarIdx = i; res.exitDate = b.date;
      res.exitPrice = p.sl!; res.exitReason = "sl_tp_same_bar";
      res.warnings.push("Both SL and TP within this bar's range — assumed SL filled (worst case).");
      break;
    } else if (slHit) {
      res.exited = true; (res as any).exitBarIdx = i; res.exitDate = b.date;
      res.exitPrice = p.sl!; res.exitReason = "sl"; break;
    } else if (tpHit) {
      res.exited = true; (res as any).exitBarIdx = i; res.exitDate = b.date;
      res.exitPrice = p.tp!; res.exitReason = "tp"; break;
    }

    // Last bar in window
    if (i === exitDeadline) {
      res.exited = true; (res as any).exitBarIdx = i; res.exitDate = b.date;
      res.exitPrice = b.close;
      res.exitReason = i === bars.length - 1 ? "end_of_data" : "max_days";
      if (res.exitReason === "end_of_data")
        res.warnings.push("Position held to end of available data.");
    }
  }

  if (!res.exited && entryBarIdx === bars.length - 1)
    res.warnings.push("Entered on last bar — no forward bars to simulate.");

  // ── P&L ──
  if (res.exited && res.exitPrice !== undefined) {
    const diff = p.direction === "long"
      ? res.exitPrice - actualEntryPrice
      : actualEntryPrice - res.exitPrice;
    res.pnlAbs = diff;
    res.pnlPct = (diff / actualEntryPrice) * 100;
    res.durationBars = (res as any).exitBarIdx - entryBarIdx;
  }

  return res;
}

function validateAndBuildParams(): { error?: string; params?: SimParams } {
  const entryType = (document.querySelector('input[name="sim-entry-type"]:checked') as HTMLInputElement).value as "market" | "limit";
  const entryPriceVal   = (document.getElementById("sim-entry-price")    as HTMLInputElement).value;
  const maxEntryDaysVal = (document.getElementById("sim-max-entry-days") as HTMLInputElement).value;
  const tpVal           = (document.getElementById("sim-tp")             as HTMLInputElement).value;
  const slVal           = (document.getElementById("sim-sl")             as HTMLInputElement).value;
  const maxExitDaysVal  = (document.getElementById("sim-max-exit-days")  as HTMLInputElement).value;

  const entryPrice    = entryPriceVal   ? parseFloat(entryPriceVal)   : undefined;
  const maxDaysToEnter = maxEntryDaysVal ? parseInt(maxEntryDaysVal)   : undefined;
  const tp            = tpVal           ? parseFloat(tpVal)           : undefined;
  const sl            = slVal           ? parseFloat(slVal)           : undefined;
  const maxExitDays   = maxExitDaysVal  ? parseInt(maxExitDaysVal)    : undefined;

  if (entryType === "limit") {
    if (!entryPrice || isNaN(entryPrice) || entryPrice <= 0)
      return { error: "Please enter a valid entry price." };
    if (!maxDaysToEnter || isNaN(maxDaysToEnter) || maxDaysToEnter < 1)
      return { error: "Please set max days to enter (minimum 1)." };
  }
  if (maxExitDays !== undefined && (isNaN(maxExitDays) || maxExitDays < 1))
    return { error: "Max days to hold must be at least 1." };

  const refPrice = entryType === "limit" ? entryPrice! : bars[sliderIndex].close;

  // Auto-detect direction
  let direction: "long" | "short" | null = null;
  if (tp !== undefined && sl !== undefined) {
    if      (tp > refPrice && sl < refPrice) direction = "long";
    else if (tp < refPrice && sl > refPrice) direction = "short";
    else return { error: "TP and SL don't define a clear direction — check your values." };
  } else if (tp !== undefined) {
    direction = tp > refPrice ? "long" : "short";
  } else if (sl !== undefined) {
    direction = sl < refPrice ? "long" : "short";
  } else {
    const dirEl = document.querySelector('input[name="sim-dir"]:checked') as HTMLInputElement | null;
    if (!dirEl) return { error: "Please set TP / SL, or select a direction (Long / Short)." };
    direction = dirEl.value as "long" | "short";
  }

  // Cross-validate TP/SL vs detected direction
  if (tp !== undefined) {
    if (direction === "long"  && tp <= refPrice) return { error: "Long trade: Take Profit must be above entry price." };
    if (direction === "short" && tp >= refPrice) return { error: "Short trade: Take Profit must be below entry price." };
  }
  if (sl !== undefined) {
    if (direction === "long"  && sl >= refPrice) return { error: "Long trade: Stop Loss must be below entry price." };
    if (direction === "short" && sl <= refPrice) return { error: "Short trade: Stop Loss must be above entry price." };
  }

  return {
    params: {
      ticker: currentTicker, simDate: bars[sliderIndex].date,
      simBarIdx: sliderIndex, interval: timeframe,
      entryType, entryPrice, maxDaysToEnter,
      tp, sl, direction, maxExitDays,
    },
  };
}

function renderSimResults(r: SimResult) {
  const el = document.getElementById("sim-results-content")!;
  const p = r.params;
  const dirClass = p.direction === "long" ? "color:#1a7f37" : "color:#cf222e";
  const dirLabel = p.direction === "long" ? "LONG ▲" : "SHORT ▼";
  const lines: string[] = [];

  lines.push(`<div class="font-semibold mb-2"><span style="${dirClass}">${dirLabel}</span> &nbsp;${p.ticker} &middot; ${p.simDate} &middot; ${p.interval}</div>`);

  if (!r.entryTriggered) {
    const window = p.entryType === "limit"
      ? `within ${p.maxDaysToEnter} day(s) from T+0`
      : "no bar after T+0";
    lines.push(`<div style="color:#cf222e;font-weight:600">Entry not triggered (${window})</div>`);
  } else {
    const entryLabel = p.entryType === "market" ? "T+1 Open" : "Limit";
    lines.push(`<div>&#128229; <span class="text-text-muted">Entry</span> &nbsp;${r.entryDate} @ <strong>${r.actualEntryPrice?.toFixed(2)}</strong> <span class="text-text-dim text-xs">(${entryLabel})</span></div>`);

    if (!r.exited) {
      lines.push(`<div class="text-text-muted italic">No exit triggered.</div>`);
    } else {
      const exitIcon: Record<string, string> = {
        tp: "&#9989;", sl: "&#10060;", sl_tp_same_bar: "&#9888;&#65039;", max_days: "&#9203;", end_of_data: "&#128202;",
      };
      const exitName: Record<string, string> = {
        tp: "Take Profit", sl: "Stop Loss", sl_tp_same_bar: "SL (same-bar conflict)",
        max_days: "Max days reached", end_of_data: "End of data",
      };
      const exitColor = r.exitReason === "tp" ? "#1a7f37" : r.exitReason === "sl" || r.exitReason === "sl_tp_same_bar" ? "#cf222e" : "#656d76";
      lines.push(`<div>&#128228; <span class="text-text-muted">Exit</span> &nbsp;${r.exitDate} @ <strong>${r.exitPrice?.toFixed(2)}</strong> &nbsp;<span style="color:${exitColor};font-size:0.75rem">${exitIcon[r.exitReason!]} ${exitName[r.exitReason!]}</span></div>`);

      const pnlPos = (r.pnlPct ?? 0) >= 0;
      const pnlColor = pnlPos ? "#1a7f37" : "#cf222e";
      const pnlSign = pnlPos ? "+" : "";
      lines.push(`<div style="color:${pnlColor};font-weight:600;margin-top:4px">P&amp;L: ${pnlSign}${r.pnlPct?.toFixed(2)}%&ensp;(${pnlSign}${r.pnlAbs?.toFixed(2)})</div>`);
      lines.push(`<div class="text-text-muted text-xs">Duration: ${r.durationBars} bar${r.durationBars !== 1 ? "s" : ""}</div>`);
    }

    for (const w of r.warnings)
      lines.push(`<div style="color:#b45309;font-size:0.75rem;margin-top:4px">&#9888; ${w}</div>`);
  }

  el.innerHTML = lines.join("");
  document.getElementById("sim-results")!.classList.remove("hidden");
}

// ── History storage ───────────────────────────────────────────────────────
function _historyKey(ticker: string) { return `tradeHistory_${ticker}`; }

function loadTradeHistory(ticker: string): { result: SimResult; id: string }[] {
  try { return JSON.parse(localStorage.getItem(_historyKey(ticker)) ?? "[]"); }
  catch { return []; }
}

function addToHistory(r: SimResult) {
  const records = loadTradeHistory(r.params.ticker);
  records.push({ result: r, id: String(Date.now()) });
  localStorage.setItem(_historyKey(r.params.ticker), JSON.stringify(records.slice(-50)));
}

function renderHistoryPanel() {
  const list  = document.getElementById("history-list")!;
  const empty = document.getElementById("history-empty")!;
  const records = loadTradeHistory(currentTicker);

  if (records.length === 0) {
    list.innerHTML = ""; empty.classList.remove("hidden"); return;
  }
  empty.classList.add("hidden");

  const exitShort: Record<string, string> = {
    tp: "TP", sl: "SL", sl_tp_same_bar: "SL*", max_days: "MaxDays", end_of_data: "EndData",
  };

  list.innerHTML = records.slice().reverse().map(({ result: r }) => {
    const p = r.params;
    const dirColor = p.direction === "long" ? "#1a7f37" : "#cf222e";
    const dirLabel = p.direction === "long" ? "LONG" : "SHORT";

    const pnlHtml = r.pnlPct !== undefined
      ? `<span style="color:${r.pnlPct >= 0 ? "#1a7f37" : "#cf222e"};font-weight:600">${r.pnlPct >= 0 ? "+" : ""}${r.pnlPct.toFixed(2)}%</span>`
      : `<span class="text-text-muted">–</span>`;

    const entryHtml = r.entryTriggered
      ? `${r.entryDate} @ ${r.actualEntryPrice?.toFixed(2)}`
      : `<span style="color:#cf222e">Not triggered</span>`;

    const exitHtml = r.exited
      ? `${r.exitDate} @ ${r.exitPrice?.toFixed(2)} [${exitShort[r.exitReason!] ?? r.exitReason}]`
      : "–";

    const runDate = new Date(r.runAt).toLocaleDateString();

    return `<div>
      <div class="flex items-center gap-3 flex-wrap">
        <span class="text-text-dim">${runDate}</span>
        <span style="color:${dirColor};font-weight:600;width:2.5rem">${dirLabel}</span>
        <span class="text-text-muted">${p.interval}</span>
        <span>T+0: ${p.simDate}</span>
        <span>${pnlHtml}</span>
      </div>
      <div class="text-text-muted mt-0.5">Entry: ${entryHtml} &nbsp;|&nbsp; Exit: ${exitHtml}</div>
    </div>`;
  }).join("");
}

function _simUpdateDirectionVisibility() {
  const hasTp = !!(document.getElementById("sim-tp") as HTMLInputElement).value;
  const hasSl = !!(document.getElementById("sim-sl") as HTMLInputElement).value;
  document.getElementById("sim-direction-row")!.classList.toggle("hidden", hasTp || hasSl);
}

function openSimModal() {
  // Reset form
  (document.querySelector('input[name="sim-entry-type"][value="market"]') as HTMLInputElement).checked = true;
  document.getElementById("sim-limit-fields")!.style.display = "none";
  (["sim-entry-price", "sim-max-entry-days", "sim-tp", "sim-sl", "sim-max-exit-days"] as const)
    .forEach(id => { (document.getElementById(id) as HTMLInputElement).value = ""; });
  document.getElementById("sim-direction-row")!.classList.add("hidden");
  document.getElementById("sim-error")!.classList.add("hidden");
  document.getElementById("sim-results")!.classList.add("hidden");
  _lastSimResult = null;

  // Show current bar reference
  const b = bars[sliderIndex];
  document.getElementById("sim-ref")!.textContent =
    `T+0: ${b.date}  ·  Close ${b.close.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}  ·  ${timeframe}`;

  document.getElementById("sim-modal")!.style.display = "flex";
}

function initSim() {
  const modal = document.getElementById("sim-modal")!;
  const close = () => { modal.style.display = "none"; };

  document.getElementById("sim-btn")!.addEventListener("click", openSimModal);
  document.getElementById("sim-close")!.addEventListener("click", close);
  document.getElementById("sim-backdrop")!.addEventListener("click", close);

  // Entry type toggle → show/hide limit fields
  document.querySelectorAll('input[name="sim-entry-type"]').forEach(r => {
    r.addEventListener("change", () => {
      document.getElementById("sim-limit-fields")!.style.display =
        (r as HTMLInputElement).value === "limit" ? "flex" : "none";
    });
  });

  // Auto-show direction row when both TP and SL are empty
  document.getElementById("sim-tp")!.addEventListener("input", _simUpdateDirectionVisibility);
  document.getElementById("sim-sl")!.addEventListener("input", _simUpdateDirectionVisibility);

  // Run
  document.getElementById("sim-run-btn")!.addEventListener("click", () => {
    const errEl = document.getElementById("sim-error")!;
    const { error, params } = validateAndBuildParams();
    if (error) { errEl.textContent = error; errEl.classList.remove("hidden"); return; }
    errEl.classList.add("hidden");
    _lastSimResult = runSimulation(params!);
    renderSimResults(_lastSimResult);
  });

  // Save to history
  document.getElementById("sim-save-btn")!.addEventListener("click", () => {
    if (!_lastSimResult) return;
    addToHistory(_lastSimResult);
    renderHistoryPanel();
    close();
  });

  // History panel toggle (collapse/expand)
  document.getElementById("history-header")!.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).id === "history-clear-btn") return;
    const panel   = document.getElementById("history-panel")!;
    const chevron = document.getElementById("history-chevron")!;
    const hiding  = !panel.classList.contains("hidden");
    panel.classList.toggle("hidden", hiding);
    chevron.textContent = hiding ? "▼" : "▲";
  });

  // Clear history
  document.getElementById("history-clear-btn")!.addEventListener("click", (e) => {
    e.stopPropagation();
    if (confirm(`Clear backtesting history for ${currentTicker}?`)) {
      localStorage.removeItem(_historyKey(currentTicker));
      renderHistoryPanel();
    }
  });

  renderHistoryPanel();
}

// ── Start ──────────────────────────────────────────────────────────────
init();
