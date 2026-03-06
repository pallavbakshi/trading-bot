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


def _normalize_slope(slope, avg_price):
    if avg_price == 0:
        return 0
    return slope / avg_price * 100


def detect_wedges(df: pd.DataFrame, pivots: pd.DataFrame,
                  min_touches: int = 4,
                  min_pattern_bars: int = 15,
                  max_pattern_bars: int = 200,
                  min_slope: float = 0.02) -> list[dict]:
    """Detect rising and falling wedge patterns.

    - Rising wedge: both trendlines slope UP but converge (bearish)
    - Falling wedge: both trendlines slope DOWN but converge (bullish)

    Key difference from triangles: both lines slope in the same direction.
    """
    peaks = pivots[pivots["type"] == "peak"].to_dict("records")
    troughs = pivots[pivots["type"] == "trough"].to_dict("records")

    if len(peaks) < 2 or len(troughs) < 2:
        return []

    patterns = []

    for start_peak_idx in range(len(peaks) - 1):
        for end_peak_idx in range(start_peak_idx + 1, min(start_peak_idx + 6, len(peaks))):
            window_peaks = peaks[start_peak_idx:end_peak_idx + 1]

            bar_start = window_peaks[0]["bar_index"]
            bar_end = window_peaks[-1]["bar_index"]
            span = bar_end - bar_start

            if span < min_pattern_bars or span > max_pattern_bars:
                continue

            window_troughs = [t for t in troughs if bar_start <= t["bar_index"] <= bar_end]

            total_touches = len(window_peaks) + len(window_troughs)
            if total_touches < min_touches or len(window_troughs) < 2:
                continue

            upper_slope, upper_intercept, upper_r2 = _fit_trendline(window_peaks)
            lower_slope, lower_intercept, lower_r2 = _fit_trendline(window_troughs)

            if upper_slope is None or lower_slope is None:
                continue

            if upper_r2 < 0.6 or lower_r2 < 0.6:
                continue

            avg_price = (np.mean([p["price"] for p in window_peaks]) +
                         np.mean([t["price"] for t in window_troughs])) / 2

            norm_upper = _normalize_slope(upper_slope, avg_price)
            norm_lower = _normalize_slope(lower_slope, avg_price)

            # Both slopes must be in the same direction and significant
            both_up = norm_upper > min_slope and norm_lower > min_slope
            both_down = norm_upper < -min_slope and norm_lower < -min_slope

            if not (both_up or both_down):
                continue

            # Must be converging (trendlines getting closer)
            width_start = (upper_slope * bar_start + upper_intercept) - \
                          (lower_slope * bar_start + lower_intercept)
            width_end = (upper_slope * bar_end + upper_intercept) - \
                        (lower_slope * bar_end + lower_intercept)

            if width_end >= width_start:
                continue  # Not converging

            if both_up:
                wedge_type = "rising_wedge"
                direction = "bearish"
                breakout_level = lower_slope * bar_end + lower_intercept
            else:
                wedge_type = "falling_wedge"
                direction = "bullish"
                breakout_level = upper_slope * bar_end + upper_intercept

            pattern_height = abs(width_start)
            target = breakout_level - pattern_height if both_up else breakout_level + pattern_height

            confidence = round(min(1.0,
                (upper_r2 + lower_r2) / 2 * 0.5 +
                min(total_touches / 8, 0.3) +
                0.2
            ), 2)

            all_pivots = sorted(window_peaks + window_troughs, key=lambda p: p["bar_index"])

            patterns.append({
                "pattern": wedge_type,
                "direction": direction,
                "start_date": str(all_pivots[0]["date"]),
                "end_date": str(all_pivots[-1]["date"]),
                "pivots": [
                    {"date": str(p["date"]), "price": round(p["price"], 4),
                     "role": p["type"]} for p in all_pivots
                ],
                "upper_slope": round(norm_upper, 4),
                "lower_slope": round(norm_lower, 4),
                "target": round(target, 4),
                "confirmed": False,
                "confidence": confidence,
            })

    return patterns
