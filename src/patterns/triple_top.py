import numpy as np
import pandas as pd


def detect_triple_tops(df: pd.DataFrame, pivots: pd.DataFrame,
                       tolerance_pct: float = 2.0,
                       min_bars_between: int = 10,
                       max_pattern_bars: int = 250) -> list[dict]:
    """Detect triple top patterns.

    Three peaks at approximately the same level with two troughs between.
    Bearish reversal — stronger signal than a double top.
    """
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(peaks) < 3:
        return []

    patterns = []

    for i in range(len(peaks) - 2):
        p1 = peaks.iloc[i]
        p2 = peaks.iloc[i + 1]
        p3 = peaks.iloc[i + 2]

        total_bars = p3["bar_index"] - p1["bar_index"]
        if total_bars > max_pattern_bars:
            continue
        if p2["bar_index"] - p1["bar_index"] < min_bars_between:
            continue
        if p3["bar_index"] - p2["bar_index"] < min_bars_between:
            continue

        avg_peak = (p1["price"] + p2["price"] + p3["price"]) / 3

        # All three peaks must be within tolerance of their average
        for p in [p1, p2, p3]:
            if abs(p["price"] - avg_peak) / avg_peak * 100 > tolerance_pct:
                break
        else:
            # Find troughs between peaks for neckline
            t1_candidates = troughs[
                (troughs["bar_index"] > p1["bar_index"]) &
                (troughs["bar_index"] < p2["bar_index"])
            ]
            t2_candidates = troughs[
                (troughs["bar_index"] > p2["bar_index"]) &
                (troughs["bar_index"] < p3["bar_index"])
            ]

            if t1_candidates.empty or t2_candidates.empty:
                continue

            t1 = t1_candidates.loc[t1_candidates["price"].idxmin()]
            t2 = t2_candidates.loc[t2_candidates["price"].idxmin()]

            neckline = min(t1["price"], t2["price"])
            pattern_height = avg_peak - neckline
            target = neckline - pattern_height

            after_p3 = df.iloc[p3["bar_index"]:]
            confirmed = bool((after_p3["Close"] < neckline).any())

            # Higher confidence than double top due to 3 touches
            max_dev = max(abs(p["price"] - avg_peak) / avg_peak * 100
                         for p in [p1, p2, p3])
            symmetry = 1 - (max_dev / tolerance_pct)
            depth_pct = pattern_height / avg_peak * 100
            confidence = round(min(1.0, symmetry * 0.4 + min(depth_pct / 10, 0.4) + 0.2), 2)

            patterns.append({
                "pattern": "triple_top",
                "direction": "bearish",
                "start_date": str(p1["date"]),
                "end_date": str(p3["date"]),
                "pivots": [
                    {"date": str(p1["date"]), "price": round(p1["price"], 4), "role": "peak_1"},
                    {"date": str(t1["date"]), "price": round(t1["price"], 4), "role": "trough_1"},
                    {"date": str(p2["date"]), "price": round(p2["price"], 4), "role": "peak_2"},
                    {"date": str(t2["date"]), "price": round(t2["price"], 4), "role": "trough_2"},
                    {"date": str(p3["date"]), "price": round(p3["price"], 4), "role": "peak_3"},
                ],
                "neckline": round(neckline, 4),
                "target": round(target, 4),
                "confirmed": confirmed,
                "confidence": confidence,
            })

    return patterns


def detect_triple_bottoms(df: pd.DataFrame, pivots: pd.DataFrame,
                          tolerance_pct: float = 2.0,
                          min_bars_between: int = 10,
                          max_pattern_bars: int = 250) -> list[dict]:
    """Detect triple bottom patterns. Bullish reversal."""
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(troughs) < 3:
        return []

    patterns = []

    for i in range(len(troughs) - 2):
        t1 = troughs.iloc[i]
        t2 = troughs.iloc[i + 1]
        t3 = troughs.iloc[i + 2]

        total_bars = t3["bar_index"] - t1["bar_index"]
        if total_bars > max_pattern_bars:
            continue
        if t2["bar_index"] - t1["bar_index"] < min_bars_between:
            continue
        if t3["bar_index"] - t2["bar_index"] < min_bars_between:
            continue

        avg_trough = (t1["price"] + t2["price"] + t3["price"]) / 3

        for t in [t1, t2, t3]:
            if abs(t["price"] - avg_trough) / avg_trough * 100 > tolerance_pct:
                break
        else:
            p1_candidates = peaks[
                (peaks["bar_index"] > t1["bar_index"]) &
                (peaks["bar_index"] < t2["bar_index"])
            ]
            p2_candidates = peaks[
                (peaks["bar_index"] > t2["bar_index"]) &
                (peaks["bar_index"] < t3["bar_index"])
            ]

            if p1_candidates.empty or p2_candidates.empty:
                continue

            p1 = p1_candidates.loc[p1_candidates["price"].idxmax()]
            p2 = p2_candidates.loc[p2_candidates["price"].idxmax()]

            neckline = max(p1["price"], p2["price"])
            pattern_height = neckline - avg_trough
            target = neckline + pattern_height

            after_t3 = df.iloc[t3["bar_index"]:]
            confirmed = bool((after_t3["Close"] > neckline).any())

            max_dev = max(abs(t["price"] - avg_trough) / avg_trough * 100
                         for t in [t1, t2, t3])
            symmetry = 1 - (max_dev / tolerance_pct)
            depth_pct = pattern_height / neckline * 100
            confidence = round(min(1.0, symmetry * 0.4 + min(depth_pct / 10, 0.4) + 0.2), 2)

            patterns.append({
                "pattern": "triple_bottom",
                "direction": "bullish",
                "start_date": str(t1["date"]),
                "end_date": str(t3["date"]),
                "pivots": [
                    {"date": str(t1["date"]), "price": round(t1["price"], 4), "role": "trough_1"},
                    {"date": str(p1["date"]), "price": round(p1["price"], 4), "role": "peak_1"},
                    {"date": str(t2["date"]), "price": round(t2["price"], 4), "role": "trough_2"},
                    {"date": str(p2["date"]), "price": round(p2["price"], 4), "role": "peak_2"},
                    {"date": str(t3["date"]), "price": round(t3["price"], 4), "role": "trough_3"},
                ],
                "neckline": round(neckline, 4),
                "target": round(target, 4),
                "confirmed": confirmed,
                "confidence": confidence,
            })

    return patterns
