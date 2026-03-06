import numpy as np
import pandas as pd


def _fit_trendline(points):
    if len(points) < 2:
        return None, None, 0
    x = np.array([p["bar_index"] for p in points], dtype=float)
    y = np.array([p["price"] for p in points], dtype=float)
    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs
    y_pred = np.polyval(coeffs, x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    return slope, intercept, r_squared


def detect_flags(df: pd.DataFrame, pivots: pd.DataFrame,
                 pole_min_pct: float = 5.0,
                 pole_max_bars: int = 30,
                 flag_min_bars: int = 5,
                 flag_max_bars: int = 50,
                 parallel_tolerance: float = 0.05) -> list[dict]:
    """Detect bull and bear flag patterns.

    A flag is a sharp move (pole) followed by a small parallel channel
    that slopes against the pole direction.
    - Bull flag: sharp up move → small downward-sloping channel
    - Bear flag: sharp down move → small upward-sloping channel
    """
    peaks = pivots[pivots["type"] == "peak"].to_dict("records")
    troughs = pivots[pivots["type"] == "trough"].to_dict("records")
    all_pivots = pivots.to_dict("records")

    if len(all_pivots) < 4:
        return []

    patterns = []
    closes = df["Close"].values

    for i in range(len(all_pivots) - 3):
        # Look for a pole: a strong move between two consecutive pivots
        p_start = all_pivots[i]
        p_end = all_pivots[i + 1]

        pole_bars = p_end["bar_index"] - p_start["bar_index"]
        if pole_bars < 3 or pole_bars > pole_max_bars:
            continue

        pole_move_pct = (p_end["price"] - p_start["price"]) / p_start["price"] * 100

        if abs(pole_move_pct) < pole_min_pct:
            continue

        is_bull_pole = pole_move_pct > 0

        # Now look for a flag (consolidation) after the pole
        flag_start_bar = p_end["bar_index"]
        flag_peaks = [p for p in peaks
                      if flag_start_bar <= p["bar_index"] <= flag_start_bar + flag_max_bars]
        flag_troughs = [t for t in troughs
                        if flag_start_bar <= t["bar_index"] <= flag_start_bar + flag_max_bars]

        if len(flag_peaks) < 2 or len(flag_troughs) < 2:
            continue

        # Fit trendlines to the flag portion
        upper_slope, upper_int, upper_r2 = _fit_trendline(flag_peaks)
        lower_slope, lower_int, lower_r2 = _fit_trendline(flag_troughs)

        if upper_slope is None or lower_slope is None:
            continue
        if upper_r2 < 0.5 or lower_r2 < 0.5:
            continue

        avg_price = (np.mean([p["price"] for p in flag_peaks]) +
                     np.mean([t["price"] for t in flag_troughs])) / 2

        norm_upper = upper_slope / avg_price * 100 if avg_price else 0
        norm_lower = lower_slope / avg_price * 100 if avg_price else 0

        # Lines should be roughly parallel
        slope_diff = abs(norm_upper - norm_lower)
        if slope_diff > parallel_tolerance:
            continue

        avg_flag_slope = (norm_upper + norm_lower) / 2

        # Flag should slope against the pole
        if is_bull_pole and avg_flag_slope >= 0:
            continue
        if not is_bull_pole and avg_flag_slope <= 0:
            continue

        # Flag consolidation should be much smaller than the pole
        flag_range = max(p["price"] for p in flag_peaks) - min(t["price"] for t in flag_troughs)
        pole_range = abs(p_end["price"] - p_start["price"])
        if flag_range > pole_range * 0.5:
            continue

        flag_end_bar = max(p["bar_index"] for p in flag_peaks + flag_troughs)
        flag_bars = flag_end_bar - flag_start_bar
        if flag_bars < flag_min_bars:
            continue

        flag_type = "bull_flag" if is_bull_pole else "bear_flag"
        direction = "bullish" if is_bull_pole else "bearish"

        # Target: pole height projected from breakout
        if is_bull_pole:
            breakout = upper_slope * flag_end_bar + upper_int
            target = breakout + pole_range
        else:
            breakout = lower_slope * flag_end_bar + lower_int
            target = breakout - pole_range

        total_touches = len(flag_peaks) + len(flag_troughs)
        confidence = round(min(1.0,
            (upper_r2 + lower_r2) / 2 * 0.4 +
            min(total_touches / 8, 0.3) +
            min(abs(pole_move_pct) / 20, 0.3)
        ), 2)

        all_flag_pivots = sorted(flag_peaks + flag_troughs, key=lambda p: p["bar_index"])

        patterns.append({
            "pattern": flag_type,
            "direction": direction,
            "start_date": str(p_start["date"]),
            "end_date": str(all_flag_pivots[-1]["date"]),
            "pole_start": {"date": str(p_start["date"]), "price": round(p_start["price"], 4)},
            "pole_end": {"date": str(p_end["date"]), "price": round(p_end["price"], 4)},
            "pole_pct": round(pole_move_pct, 2),
            "pivots": [
                {"date": str(p["date"]), "price": round(p["price"], 4),
                 "role": p["type"]} for p in all_flag_pivots
            ],
            "target": round(target, 4),
            "confirmed": False,
            "confidence": confidence,
        })

    return patterns


def detect_pennants(df: pd.DataFrame, pivots: pd.DataFrame,
                    pole_min_pct: float = 5.0,
                    pole_max_bars: int = 30,
                    pennant_min_bars: int = 5,
                    pennant_max_bars: int = 50) -> list[dict]:
    """Detect bull and bear pennants.

    A pennant is a sharp move (pole) followed by a small symmetrical triangle.
    - Bull pennant: sharp up → converging trendlines
    - Bear pennant: sharp down → converging trendlines
    """
    peaks = pivots[pivots["type"] == "peak"].to_dict("records")
    troughs = pivots[pivots["type"] == "trough"].to_dict("records")
    all_pivots_list = pivots.to_dict("records")

    if len(all_pivots_list) < 4:
        return []

    patterns = []

    for i in range(len(all_pivots_list) - 3):
        p_start = all_pivots_list[i]
        p_end = all_pivots_list[i + 1]

        pole_bars = p_end["bar_index"] - p_start["bar_index"]
        if pole_bars < 3 or pole_bars > pole_max_bars:
            continue

        pole_move_pct = (p_end["price"] - p_start["price"]) / p_start["price"] * 100
        if abs(pole_move_pct) < pole_min_pct:
            continue

        is_bull_pole = pole_move_pct > 0
        flag_start_bar = p_end["bar_index"]

        pennant_peaks = [p for p in peaks
                         if flag_start_bar <= p["bar_index"] <= flag_start_bar + pennant_max_bars]
        pennant_troughs = [t for t in troughs
                           if flag_start_bar <= t["bar_index"] <= flag_start_bar + pennant_max_bars]

        if len(pennant_peaks) < 2 or len(pennant_troughs) < 2:
            continue

        upper_slope, upper_int, upper_r2 = _fit_trendline(pennant_peaks)
        lower_slope, lower_int, lower_r2 = _fit_trendline(pennant_troughs)

        if upper_slope is None or lower_slope is None:
            continue
        if upper_r2 < 0.5 or lower_r2 < 0.5:
            continue

        avg_price = (np.mean([p["price"] for p in pennant_peaks]) +
                     np.mean([t["price"] for t in pennant_troughs])) / 2
        norm_upper = upper_slope / avg_price * 100 if avg_price else 0
        norm_lower = lower_slope / avg_price * 100 if avg_price else 0

        # Pennant: upper slopes DOWN, lower slopes UP (converging)
        if norm_upper >= 0 or norm_lower <= 0:
            continue

        # Consolidation should be small relative to pole
        pennant_range = max(p["price"] for p in pennant_peaks) - min(t["price"] for t in pennant_troughs)
        pole_range = abs(p_end["price"] - p_start["price"])
        if pennant_range > pole_range * 0.5:
            continue

        pennant_end_bar = max(p["bar_index"] for p in pennant_peaks + pennant_troughs)
        pennant_bars = pennant_end_bar - flag_start_bar
        if pennant_bars < pennant_min_bars:
            continue

        pennant_type = "bull_pennant" if is_bull_pole else "bear_pennant"
        direction = "bullish" if is_bull_pole else "bearish"

        if is_bull_pole:
            breakout = upper_slope * pennant_end_bar + upper_int
            target = breakout + pole_range
        else:
            breakout = lower_slope * pennant_end_bar + lower_int
            target = breakout - pole_range

        total_touches = len(pennant_peaks) + len(pennant_troughs)
        confidence = round(min(1.0,
            (upper_r2 + lower_r2) / 2 * 0.4 +
            min(total_touches / 8, 0.3) +
            min(abs(pole_move_pct) / 20, 0.3)
        ), 2)

        all_pennant_pivots = sorted(pennant_peaks + pennant_troughs, key=lambda p: p["bar_index"])

        patterns.append({
            "pattern": pennant_type,
            "direction": direction,
            "start_date": str(p_start["date"]),
            "end_date": str(all_pennant_pivots[-1]["date"]),
            "pole_start": {"date": str(p_start["date"]), "price": round(p_start["price"], 4)},
            "pole_end": {"date": str(p_end["date"]), "price": round(p_end["price"], 4)},
            "pole_pct": round(pole_move_pct, 2),
            "pivots": [
                {"date": str(p["date"]), "price": round(p["price"], 4),
                 "role": p["type"]} for p in all_pennant_pivots
            ],
            "target": round(target, 4),
            "confirmed": False,
            "confidence": confidence,
        })

    return patterns
