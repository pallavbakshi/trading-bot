import numpy as np
import pandas as pd


def detect_double_tops(df: pd.DataFrame, pivots: pd.DataFrame,
                       tolerance_pct: float = 2.0,
                       min_bars_between: int = 10,
                       max_bars_between: int = 150) -> list[dict]:
    """Detect double top patterns.

    A double top: two peaks at approximately the same level with a trough between.
    Bearish reversal pattern.
    """
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(peaks) < 2:
        return []

    patterns = []

    for i in range(len(peaks) - 1):
        p1 = peaks.iloc[i]
        for j in range(i + 1, len(peaks)):
            p2 = peaks.iloc[j]

            bars_between = p2["bar_index"] - p1["bar_index"]
            if bars_between < min_bars_between or bars_between > max_bars_between:
                continue

            # Check peaks are at similar level
            price_diff_pct = abs(p1["price"] - p2["price"]) / p1["price"] * 100
            if price_diff_pct > tolerance_pct:
                continue

            # Find the lowest trough between the two peaks
            between_troughs = troughs[
                (troughs["bar_index"] > p1["bar_index"]) &
                (troughs["bar_index"] < p2["bar_index"])
            ]
            if between_troughs.empty:
                continue

            neckline_row = between_troughs.loc[between_troughs["price"].idxmin()]
            neckline = neckline_row["price"]

            # Pattern height for target calculation
            pattern_height = ((p1["price"] + p2["price"]) / 2) - neckline
            target = neckline - pattern_height

            # Check if price broke below neckline after second peak
            after_p2 = df.iloc[p2["bar_index"]:]
            confirmed = bool((after_p2["Close"] < neckline).any())

            # Confidence based on symmetry and depth
            depth_pct = pattern_height / p1["price"] * 100
            symmetry = 1 - (price_diff_pct / tolerance_pct)
            confidence = round(min(1.0, symmetry * 0.5 + min(depth_pct / 10, 0.5)), 2)

            patterns.append({
                "pattern": "double_top",
                "direction": "bearish",
                "start_date": str(p1["date"]),
                "end_date": str(p2["date"]),
                "pivots": [
                    {"date": str(p1["date"]), "price": round(p1["price"], 4), "role": "peak_1"},
                    {"date": str(neckline_row["date"]), "price": round(neckline, 4), "role": "neckline"},
                    {"date": str(p2["date"]), "price": round(p2["price"], 4), "role": "peak_2"},
                ],
                "neckline": round(neckline, 4),
                "target": round(target, 4),
                "confirmed": confirmed,
                "confidence": confidence,
            })

    return patterns


def detect_double_bottoms(df: pd.DataFrame, pivots: pd.DataFrame,
                          tolerance_pct: float = 2.0,
                          min_bars_between: int = 10,
                          max_bars_between: int = 150) -> list[dict]:
    """Detect double bottom patterns.

    A double bottom: two troughs at approximately the same level with a peak between.
    Bullish reversal pattern.
    """
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(troughs) < 2:
        return []

    patterns = []

    for i in range(len(troughs) - 1):
        t1 = troughs.iloc[i]
        for j in range(i + 1, len(troughs)):
            t2 = troughs.iloc[j]

            bars_between = t2["bar_index"] - t1["bar_index"]
            if bars_between < min_bars_between or bars_between > max_bars_between:
                continue

            price_diff_pct = abs(t1["price"] - t2["price"]) / t1["price"] * 100
            if price_diff_pct > tolerance_pct:
                continue

            between_peaks = peaks[
                (peaks["bar_index"] > t1["bar_index"]) &
                (peaks["bar_index"] < t2["bar_index"])
            ]
            if between_peaks.empty:
                continue

            neckline_row = between_peaks.loc[between_peaks["price"].idxmax()]
            neckline = neckline_row["price"]

            pattern_height = neckline - ((t1["price"] + t2["price"]) / 2)
            target = neckline + pattern_height

            after_t2 = df.iloc[t2["bar_index"]:]
            confirmed = bool((after_t2["Close"] > neckline).any())

            depth_pct = pattern_height / neckline * 100
            symmetry = 1 - (price_diff_pct / tolerance_pct)
            confidence = round(min(1.0, symmetry * 0.5 + min(depth_pct / 10, 0.5)), 2)

            patterns.append({
                "pattern": "double_bottom",
                "direction": "bullish",
                "start_date": str(t1["date"]),
                "end_date": str(t2["date"]),
                "pivots": [
                    {"date": str(t1["date"]), "price": round(t1["price"], 4), "role": "trough_1"},
                    {"date": str(neckline_row["date"]), "price": round(neckline, 4), "role": "neckline"},
                    {"date": str(t2["date"]), "price": round(t2["price"], 4), "role": "trough_2"},
                ],
                "neckline": round(neckline, 4),
                "target": round(target, 4),
                "confirmed": confirmed,
                "confidence": confidence,
            })

    return patterns
