import numpy as np
import pandas as pd


def detect_head_and_shoulders(df: pd.DataFrame, pivots: pd.DataFrame,
                              shoulder_tolerance_pct: float = 3.0,
                              min_pattern_bars: int = 20,
                              max_pattern_bars: int = 200) -> list[dict]:
    """Detect head and shoulders patterns.

    Structure: peak(left shoulder) - higher peak(head) - peak(right shoulder)
    with two troughs forming the neckline. Bearish reversal.
    """
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(peaks) < 3:
        return []

    patterns = []

    for i in range(len(peaks) - 2):
        ls = peaks.iloc[i]      # left shoulder
        head = peaks.iloc[i + 1]  # head
        rs = peaks.iloc[i + 2]  # right shoulder

        total_bars = rs["bar_index"] - ls["bar_index"]
        if total_bars < min_pattern_bars or total_bars > max_pattern_bars:
            continue

        # Head must be higher than both shoulders
        if head["price"] <= ls["price"] or head["price"] <= rs["price"]:
            continue

        # Shoulders should be at approximately the same level
        shoulder_diff_pct = abs(ls["price"] - rs["price"]) / ls["price"] * 100
        if shoulder_diff_pct > shoulder_tolerance_pct:
            continue

        # Find troughs between LS-Head and Head-RS for neckline
        t1_candidates = troughs[
            (troughs["bar_index"] > ls["bar_index"]) &
            (troughs["bar_index"] < head["bar_index"])
        ]
        t2_candidates = troughs[
            (troughs["bar_index"] > head["bar_index"]) &
            (troughs["bar_index"] < rs["bar_index"])
        ]

        if t1_candidates.empty or t2_candidates.empty:
            continue

        t1 = t1_candidates.loc[t1_candidates["price"].idxmin()]
        t2 = t2_candidates.loc[t2_candidates["price"].idxmin()]

        # Neckline from the two troughs
        neckline_at_rs = t1["price"] + (t2["price"] - t1["price"]) * \
            (rs["bar_index"] - t1["bar_index"]) / (t2["bar_index"] - t1["bar_index"])

        pattern_height = head["price"] - (t1["price"] + t2["price"]) / 2
        target = neckline_at_rs - pattern_height

        # Check confirmation
        after_rs = df.iloc[rs["bar_index"]:]
        neckline_avg = (t1["price"] + t2["price"]) / 2
        confirmed = bool((after_rs["Close"] < neckline_avg).any())

        # Confidence scoring
        symmetry = 1 - (shoulder_diff_pct / shoulder_tolerance_pct)
        head_prominence = (head["price"] - max(ls["price"], rs["price"])) / head["price"] * 100
        neckline_flatness = 1 - abs(t1["price"] - t2["price"]) / max(t1["price"], t2["price"])
        confidence = round(min(1.0, symmetry * 0.3 + min(head_prominence / 10, 0.4) + neckline_flatness * 0.3), 2)

        patterns.append({
            "pattern": "head_and_shoulders",
            "direction": "bearish",
            "start_date": str(ls["date"]),
            "end_date": str(rs["date"]),
            "pivots": [
                {"date": str(ls["date"]), "price": round(ls["price"], 4), "role": "left_shoulder"},
                {"date": str(t1["date"]), "price": round(t1["price"], 4), "role": "neckline_left"},
                {"date": str(head["date"]), "price": round(head["price"], 4), "role": "head"},
                {"date": str(t2["date"]), "price": round(t2["price"], 4), "role": "neckline_right"},
                {"date": str(rs["date"]), "price": round(rs["price"], 4), "role": "right_shoulder"},
            ],
            "neckline": round(float(neckline_avg), 4),
            "target": round(float(target), 4),
            "confirmed": confirmed,
            "confidence": confidence,
        })

    return patterns


def detect_inverse_head_and_shoulders(df: pd.DataFrame, pivots: pd.DataFrame,
                                      shoulder_tolerance_pct: float = 3.0,
                                      min_pattern_bars: int = 20,
                                      max_pattern_bars: int = 200) -> list[dict]:
    """Detect inverse head and shoulders. Bullish reversal.

    Structure: trough(left shoulder) - lower trough(head) - trough(right shoulder)
    """
    peaks = pivots[pivots["type"] == "peak"].reset_index(drop=True)
    troughs = pivots[pivots["type"] == "trough"].reset_index(drop=True)

    if len(troughs) < 3:
        return []

    patterns = []

    for i in range(len(troughs) - 2):
        ls = troughs.iloc[i]
        head = troughs.iloc[i + 1]
        rs = troughs.iloc[i + 2]

        total_bars = rs["bar_index"] - ls["bar_index"]
        if total_bars < min_pattern_bars or total_bars > max_pattern_bars:
            continue

        # Head must be lower than both shoulders
        if head["price"] >= ls["price"] or head["price"] >= rs["price"]:
            continue

        shoulder_diff_pct = abs(ls["price"] - rs["price"]) / ls["price"] * 100
        if shoulder_diff_pct > shoulder_tolerance_pct:
            continue

        p1_candidates = peaks[
            (peaks["bar_index"] > ls["bar_index"]) &
            (peaks["bar_index"] < head["bar_index"])
        ]
        p2_candidates = peaks[
            (peaks["bar_index"] > head["bar_index"]) &
            (peaks["bar_index"] < rs["bar_index"])
        ]

        if p1_candidates.empty or p2_candidates.empty:
            continue

        p1 = p1_candidates.loc[p1_candidates["price"].idxmax()]
        p2 = p2_candidates.loc[p2_candidates["price"].idxmax()]

        neckline_avg = (p1["price"] + p2["price"]) / 2
        pattern_height = neckline_avg - head["price"]
        target = neckline_avg + pattern_height

        after_rs = df.iloc[rs["bar_index"]:]
        confirmed = bool((after_rs["Close"] > neckline_avg).any())

        symmetry = 1 - (shoulder_diff_pct / shoulder_tolerance_pct)
        head_prominence = (min(ls["price"], rs["price"]) - head["price"]) / head["price"] * 100
        neckline_flatness = 1 - abs(p1["price"] - p2["price"]) / max(p1["price"], p2["price"])
        confidence = round(min(1.0, symmetry * 0.3 + min(head_prominence / 10, 0.4) + neckline_flatness * 0.3), 2)

        patterns.append({
            "pattern": "inverse_head_and_shoulders",
            "direction": "bullish",
            "start_date": str(ls["date"]),
            "end_date": str(rs["date"]),
            "pivots": [
                {"date": str(ls["date"]), "price": round(ls["price"], 4), "role": "left_shoulder"},
                {"date": str(p1["date"]), "price": round(p1["price"], 4), "role": "neckline_left"},
                {"date": str(head["date"]), "price": round(head["price"], 4), "role": "head"},
                {"date": str(p2["date"]), "price": round(p2["price"], 4), "role": "neckline_right"},
                {"date": str(rs["date"]), "price": round(rs["price"], 4), "role": "right_shoulder"},
            ],
            "neckline": round(float(neckline_avg), 4),
            "target": round(float(target), 4),
            "confirmed": confirmed,
            "confidence": confidence,
        })

    return patterns
