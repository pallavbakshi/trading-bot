import numpy as np
import pandas as pd


def detect_gaps(df: pd.DataFrame, min_gap_pct: float = 1.0) -> list[dict]:
    """Detect price gaps: gap up, gap down, and island reversals.

    A gap occurs when the current bar's range doesn't overlap with the previous bar.
    - Gap up: current Low > previous High
    - Gap down: current High < previous Low
    - Island reversal: gap in one direction followed by gap in the opposite direction
    """
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    volumes = df["Volume"].values
    dates = df.index

    gaps = []

    for i in range(1, len(df)):
        prev_high = highs[i - 1]
        prev_low = lows[i - 1]
        curr_high = highs[i]
        curr_low = lows[i]
        mid_prev = (prev_high + prev_low) / 2

        if curr_low > prev_high:
            gap_size = curr_low - prev_high
            gap_pct = gap_size / mid_prev * 100
            if gap_pct >= min_gap_pct:
                gaps.append({
                    "pattern": "gap_up",
                    "direction": "bullish",
                    "date": str(dates[i]),
                    "gap_low": round(prev_high, 4),
                    "gap_high": round(curr_low, 4),
                    "gap_pct": round(gap_pct, 2),
                    "filled": bool(np.any(lows[i:] <= prev_high)),
                    "bar_index": i,
                })
        elif curr_high < prev_low:
            gap_size = prev_low - curr_high
            gap_pct = gap_size / mid_prev * 100
            if gap_pct >= min_gap_pct:
                gaps.append({
                    "pattern": "gap_down",
                    "direction": "bearish",
                    "date": str(dates[i]),
                    "gap_low": round(curr_high, 4),
                    "gap_high": round(prev_low, 4),
                    "gap_pct": round(gap_pct, 2),
                    "filled": bool(np.any(highs[i:] >= prev_low)),
                    "bar_index": i,
                })

    # Detect island reversals: gap up followed by gap down (or vice versa)
    islands = []
    for i in range(len(gaps) - 1):
        g1 = gaps[i]
        g2 = gaps[i + 1]

        bars_between = g2["bar_index"] - g1["bar_index"]
        if bars_between > 30:
            continue

        if g1["pattern"] == "gap_up" and g2["pattern"] == "gap_down":
            islands.append({
                "pattern": "island_reversal_top",
                "direction": "bearish",
                "start_date": g1["date"],
                "end_date": g2["date"],
                "gap_up": g1,
                "gap_down": g2,
                "bars_on_island": bars_between,
                "confidence": round(min(1.0, (g1["gap_pct"] + g2["gap_pct"]) / 10), 2),
            })
        elif g1["pattern"] == "gap_down" and g2["pattern"] == "gap_up":
            islands.append({
                "pattern": "island_reversal_bottom",
                "direction": "bullish",
                "start_date": g1["date"],
                "end_date": g2["date"],
                "gap_down": g1,
                "gap_up": g2,
                "bars_on_island": bars_between,
                "confidence": round(min(1.0, (g1["gap_pct"] + g2["gap_pct"]) / 10), 2),
            })

    # Clean bar_index from gap output
    for g in gaps:
        del g["bar_index"]

    return gaps, islands
