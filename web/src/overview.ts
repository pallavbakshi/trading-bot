/**
 * Mini overview bar — full price history sparkline with draggable viewport.
 * Lives OUTSIDE #snapshot-region so it never appears in chart screenshots.
 */

let canvas: HTMLCanvasElement;
let ctx: CanvasRenderingContext2D;
let allBars: { close: number; date: string }[] = [];
let onRangeChange: (start: number, end: number) => void = () => {};

type DragMode = "window" | "left" | "right" | null;
let dragMode: DragMode = null;
let dragStartX = 0;
let dragStartRange: [number, number] = [0, 0];
let _vis: [number, number] = [0, 0];

const HANDLE_PX = 10; // px grab zone at each edge
const MIN_BARS  = 10; // minimum window size in bars

export function initOverview(
  container: HTMLElement,
  onChange: (start: number, end: number) => void
) {
  onRangeChange = onChange;

  canvas = document.createElement("canvas");
  canvas.id = "overview-canvas";
  container.appendChild(canvas);

  canvas.addEventListener("mousedown", onMouseDown);
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
  canvas.addEventListener("mousemove", updateCursor);
}

export function updateOverview(
  bars: { close: number; date: string }[],
  visibleStart: number,
  visibleEnd: number
) {
  allBars = bars;
  render(visibleStart, visibleEnd);
}

export function setOverviewVisible(s: number, e: number) {
  _vis = [s, e];
}

// ── Rendering ──────────────────────────────────────────────────────────────

function render(visibleStart: number, visibleEnd: number) {
  if (!canvas || allBars.length === 0) return;

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const W = rect.width;
  const H = rect.height;
  if (W === 0 || H === 0) return;

  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  ctx = canvas.getContext("2d")!;
  ctx.scale(dpr, dpr);

  const cs = getComputedStyle(document.documentElement);
  const bgColor    = cs.getPropertyValue("--color-bg-panel").trim()  || "#161b22";
  const lineColor  = cs.getPropertyValue("--color-accent").trim()    || "#58a6ff";
  const textColor  = cs.getPropertyValue("--color-text-muted").trim()|| "#8b949e";

  const fillColor  = lineColor + "22";
  const viewFill   = lineColor + "2e";
  const viewBorder = lineColor;
  const handleFill = lineColor + "cc";

  ctx.fillStyle = bgColor;
  ctx.fillRect(0, 0, W, H);

  const n = allBars.length;
  // safe min/max for large arrays
  let minC = Infinity, maxC = -Infinity;
  for (const b of allBars) {
    if (b.close < minC) minC = b.close;
    if (b.close > maxC) maxC = b.close;
  }
  const range = maxC - minC || 1;

  const padT = 4, padB = 12;
  const chartH = H - padT - padB;
  const iToX = (i: number) => (i / (n - 1)) * W;
  const cToY = (c: number) => padT + chartH - ((c - minC) / range) * chartH;

  // Sparkline fill
  ctx.beginPath();
  ctx.moveTo(iToX(0), cToY(allBars[0].close));
  for (let i = 1; i < n; i++) ctx.lineTo(iToX(i), cToY(allBars[i].close));
  ctx.lineTo(iToX(n - 1), H);
  ctx.lineTo(iToX(0), H);
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();

  // Sparkline line
  ctx.beginPath();
  ctx.moveTo(iToX(0), cToY(allBars[0].close));
  for (let i = 1; i < n; i++) ctx.lineTo(iToX(i), cToY(allBars[i].close));
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1;
  ctx.stroke();

  // Viewport window fill
  const x0 = iToX(visibleStart);
  const x1 = iToX(visibleEnd);
  const ww  = Math.max(x1 - x0, 2);

  ctx.fillStyle = viewFill;
  ctx.fillRect(x0, 0, ww, H);

  // Viewport border (top + bottom only — sides are the handles)
  ctx.strokeStyle = viewBorder;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0, 0); ctx.lineTo(x1, 0);       // top
  ctx.moveTo(x0, H - padB); ctx.lineTo(x1, H - padB); // above labels
  ctx.stroke();

  // Left handle — bright vertical bar
  ctx.fillStyle = handleFill;
  ctx.fillRect(x0, 0, 3, H - padB);

  // Right handle
  ctx.fillRect(x1 - 3, 0, 3, H - padB);

  // Year labels — exact first bar of each year
  ctx.fillStyle = textColor;
  ctx.font = `9px monospace`;
  ctx.textAlign = "center";
  const MIN_LABEL_GAP = 28;
  let lastLabelX = -MIN_LABEL_GAP;
  let lastYear = "";
  for (let i = 0; i < n; i++) {
    const yr = allBars[i].date.slice(0, 4);
    if (yr !== lastYear) {
      lastYear = yr;
      const x = iToX(i);
      if (x - lastLabelX >= MIN_LABEL_GAP) {
        ctx.fillText(yr, x, H - 2);
        lastLabelX = x;
      }
    }
  }
}

// ── Interaction ─────────────────────────────────────────────────────────────

function iToX(i: number) {
  const W = canvas.getBoundingClientRect().width;
  return (i / (allBars.length - 1)) * W;
}

function xToIndex(x: number): number {
  const W = canvas.getBoundingClientRect().width;
  const n = allBars.length;
  return Math.round(Math.max(0, Math.min(n - 1, (x / W) * (n - 1))));
}

function hitTest(x: number): DragMode {
  if (!canvas || allBars.length === 0) return null;
  const x0 = iToX(_vis[0]);
  const x1 = iToX(_vis[1]);
  if (Math.abs(x - x0) <= HANDLE_PX) return "left";
  if (Math.abs(x - x1) <= HANDLE_PX) return "right";
  if (x > x0 && x < x1)             return "window";
  return null;
}

function onMouseDown(e: MouseEvent) {
  const x    = e.offsetX;
  const mode = hitTest(x);

  if (!mode) {
    // Click outside window → jump, keeping window size
    const idx  = xToIndex(x);
    const half = Math.round((_vis[1] - _vis[0]) / 2);
    const n    = allBars.length;
    const s    = Math.max(0, Math.min(n - 1 - (_vis[1] - _vis[0]), idx - half));
    onRangeChange(s, s + (_vis[1] - _vis[0]));
    return;
  }

  dragMode       = mode;
  dragStartX     = e.clientX;
  dragStartRange = [_vis[0], _vis[1]];
  canvas.style.cursor = mode === "window" ? "grabbing" : "ew-resize";
  e.preventDefault();
}

function onMouseMove(e: MouseEvent) {
  if (!dragMode) return;
  const W  = canvas.getBoundingClientRect().width;
  const n  = allBars.length;
  const dx = e.clientX - dragStartX;
  const di = Math.round((dx / W) * (n - 1));

  let [s, end] = dragStartRange;

  if (dragMode === "window") {
    const len = end - s;
    s   = Math.max(0, Math.min(n - 1 - len, s + di));
    end = s + len;
  } else if (dragMode === "left") {
    s   = Math.max(0, Math.min(end - MIN_BARS, s + di));
  } else if (dragMode === "right") {
    end = Math.max(s + MIN_BARS, Math.min(n - 1, end + di));
  }

  onRangeChange(s, end);
}

function onMouseUp() {
  if (dragMode) {
    dragMode = null;
    updateCursorFromVis();
  }
}

function updateCursorFromVis() {
  // reset to the appropriate non-active cursor
  canvas.style.cursor = "crosshair";
}

function updateCursor(e: MouseEvent) {
  if (dragMode) return; // don't flicker during drag
  const mode = hitTest(e.offsetX);
  canvas.style.cursor =
    mode === "window"               ? "grab"      :
    mode === "left" || mode === "right" ? "ew-resize" :
    "crosshair";
}
