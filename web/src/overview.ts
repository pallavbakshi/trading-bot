/**
 * Mini overview bar — full price history sparkline with draggable viewport.
 * Lives OUTSIDE #snapshot-region so it never appears in chart screenshots.
 */

let canvas: HTMLCanvasElement;
let ctx: CanvasRenderingContext2D;
let allBars: { close: number; date: string }[] = [];
let onRangeChange: (start: number, end: number) => void = () => {};

// Drag state
type DragMode = "window" | "left" | "right" | null;
let dragMode: DragMode = null;
let dragStartX = 0;
let dragStartRange: [number, number] = [0, 0];

const HANDLE_PX = 6; // pixels at each edge that act as resize handle

export function initOverview(
  container: HTMLElement,
  onChange: (start: number, end: number) => void
) {
  onRangeChange = onChange;

  canvas = document.createElement("canvas");
  canvas.id = "overview-canvas";
  container.appendChild(canvas);

  // Pointer events
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

function render(visibleStart: number, visibleEnd: number) {
  if (!canvas || allBars.length === 0) return;

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const W = rect.width;
  const H = rect.height;

  if (W === 0 || H === 0) return;

  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx = canvas.getContext("2d")!;
  ctx.scale(dpr, dpr);

  const style = getComputedStyle(document.documentElement);
  const bgColor      = style.getPropertyValue("--color-bg-panel").trim() || "#161b22";
  const lineColor    = style.getPropertyValue("--color-accent").trim()   || "#58a6ff";
  const fillColor    = lineColor + "22";
  const viewColor    = lineColor + "33";
  const viewBorder   = lineColor;
  const textColor    = style.getPropertyValue("--color-text-muted").trim() || "#8b949e";
  const borderColor  = style.getPropertyValue("--color-border").trim()    || "#30363d";

  // Background
  ctx.fillStyle = bgColor;
  ctx.fillRect(0, 0, W, H);

  const n = allBars.length;
  const closes = allBars.map(b => b.close);
  const minC = Math.min(...closes);
  const maxC = Math.max(...closes);
  const range = maxC - minC || 1;

  const padT = 4, padB = 12;
  const chartH = H - padT - padB;

  // Map index → x pixel
  const iToX = (i: number) => (i / (n - 1)) * W;
  const cToY = (c: number) => padT + chartH - ((c - minC) / range) * chartH;

  // Draw sparkline area
  ctx.beginPath();
  ctx.moveTo(iToX(0), cToY(closes[0]));
  for (let i = 1; i < n; i++) {
    ctx.lineTo(iToX(i), cToY(closes[i]));
  }
  // Fill down to bottom
  ctx.lineTo(iToX(n - 1), H);
  ctx.lineTo(iToX(0), H);
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();

  // Draw sparkline line
  ctx.beginPath();
  ctx.moveTo(iToX(0), cToY(closes[0]));
  for (let i = 1; i < n; i++) {
    ctx.lineTo(iToX(i), cToY(closes[i]));
  }
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1;
  ctx.stroke();

  // Viewport window
  const x0 = iToX(visibleStart);
  const x1 = iToX(visibleEnd);

  ctx.fillStyle = viewColor;
  ctx.fillRect(x0, 0, x1 - x0, H);

  ctx.strokeStyle = viewBorder;
  ctx.lineWidth = 1.5;
  ctx.strokeRect(x0, 0, x1 - x0, H);

  // Year labels — one per year, placed at the exact first bar of each year
  ctx.fillStyle = textColor;
  ctx.font = `9px monospace`;
  ctx.textAlign = "center";
  const MIN_LABEL_GAP = 28; // px — skip label if previous one was too close
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

function hitTest(x: number, visibleStart: number, visibleEnd: number): DragMode {
  if (!canvas || allBars.length === 0) return null;
  const W = canvas.getBoundingClientRect().width;
  const n = allBars.length;
  const x0 = (visibleStart / (n - 1)) * W;
  const x1 = (visibleEnd   / (n - 1)) * W;

  if (Math.abs(x - x0) <= HANDLE_PX) return "left";
  if (Math.abs(x - x1) <= HANDLE_PX) return "right";
  if (x >= x0 && x <= x1) return "window";
  return null;
}

function xToIndex(x: number): number {
  const W = canvas.getBoundingClientRect().width;
  const n = allBars.length;
  return Math.round(Math.max(0, Math.min(n - 1, (x / W) * (n - 1))));
}

// We store visible range here for drag calculations
let _vis: [number, number] = [0, 0];
export function setOverviewVisible(s: number, e: number) { _vis = [s, e]; }

function onMouseDown(e: MouseEvent) {
  const x = e.offsetX;
  const mode = hitTest(x, _vis[0], _vis[1]);
  if (!mode) {
    // Click outside window → jump to center on that position
    const idx = xToIndex(x);
    const half = Math.round((_vis[1] - _vis[0]) / 2);
    const n = allBars.length;
    const start = Math.max(0, Math.min(n - 1 - (_vis[1] - _vis[0]), idx - half));
    const end   = start + (_vis[1] - _vis[0]);
    onRangeChange(start, Math.min(n - 1, end));
    return;
  }
  dragMode = mode;
  dragStartX = e.clientX;
  dragStartRange = [_vis[0], _vis[1]];
  e.preventDefault();
}

function onMouseMove(e: MouseEvent) {
  if (!dragMode) return;
  const W = canvas.getBoundingClientRect().width;
  const n = allBars.length;
  const dx = e.clientX - dragStartX;
  const di = Math.round((dx / W) * (n - 1));

  let [s, end] = dragStartRange;

  if (dragMode === "window") {
    const len = end - s;
    s   = Math.max(0, Math.min(n - 1 - len, s + di));
    end = s + len;
  } else if (dragMode === "left") {
    s = Math.max(0, Math.min(end - 10, s + di));
  } else if (dragMode === "right") {
    end = Math.max(s + 10, Math.min(n - 1, end + di));
  }

  onRangeChange(s, end);
}

function onMouseUp() {
  dragMode = null;
}

function updateCursor(e: MouseEvent) {
  const mode = hitTest(e.offsetX, _vis[0], _vis[1]);
  canvas.style.cursor =
    mode === "window" ? "grab" :
    mode === "left" || mode === "right" ? "ew-resize" :
    "crosshair";
}
