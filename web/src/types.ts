export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Pivot {
  date: string;
  price: number;
  role: string;
}

export interface GeometricPattern {
  pattern: string;
  direction: string;
  start_date: string;
  end_date: string;
  pivots: Pivot[];
  neckline?: number;
  target?: number;
  confirmed?: boolean;
  confidence: number;
  pivot_order: number;
}

export interface CandlestickPattern {
  date: string;
  pattern: string;
  direction: string;
  strength: number;
}

export interface SRZone {
  level: number;
  type: "support" | "resistance";
  start_date: string;
  end_date: string;
  touches: number;
  broken: boolean;
  broken_date: string | null;
}

export interface Signal {
  pattern: string;
  direction: string;
  date: string;
  sma50?: number;
  sma200?: number;
  close?: number;
  bandwidth?: number;
  upper?: number;
  lower?: number;
  volume_ratio?: number;
  body_ratio?: number;
  confidence?: number;
}

export interface Divergence {
  pattern: string;
  direction: string;
  start_date: string;
  end_date: string;
  price_1: number;
  price_2: number;
  indicator_1: number;
  indicator_2: number;
  confidence: number;
}

export interface Gap {
  pattern: string;
  date: string;
  gap_low: number;
  gap_high: number;
  gap_pct: number;
}

export interface TickerResult {
  ticker: string;
  bars: number;
  date_range: [string, string];
  candlestick_patterns: CandlestickPattern[];
  geometric_patterns: GeometricPattern[];
  gaps: Gap[];
  island_reversals: Gap[];
  divergences: Divergence[];
  signals: Signal[];
  support_resistance: SRZone[];
  rolling_sr: SRZone[];
  density_sr: SRZone[];
}

export interface TimeframeData {
  bars: Bar[];
  result: TickerResult;
  sma50: (number | null)[];
  sma200: (number | null)[];
  rsi: (number | null)[];
}

export interface TickerData {
  daily: TimeframeData;
  weekly: TimeframeData;
  monthly: TimeframeData;
}

export type LayerKey = "sr" | "geometric" | "crosses" | "bb_squeeze" | "vol_climax" | "divergences" | "gaps" | "llm_levels";

export interface Layer {
  key: LayerKey;
  label: string;
  active: boolean;
}
