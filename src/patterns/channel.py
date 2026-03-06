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


def detect_channels(df: pd.DataFrame, pivots: pd.DataFrame,
                    min_touches: int = 4,
                    min_pattern_bars: int = 20,
                    max_pattern_bars: int = 300,
                    parallel_tolerance: float = 0.03) -> list[dict]:
    """Detect horizontal and trending channels (rectangles).

    A channel has two roughly parallel trendlines.
    - Horizontal channel (rectangle): both lines ~flat
    - Ascending channel: both lines slope up, roughly parallel
    - Descending channel: both lines slope down, roughly parallel
    """
    peaks = pivots[pivots["type"] == "peak"].to_dict("records")
    troughs = pivots[pivots["type"] == "trough"].to_dict("records")

    if len(peaks) < 2 or len(troughs) < 2:
        return []

    patterns = []

    for start_peak_idx in range(len(peaks) - 1):
        for end_peak_idx in range(start_peak_idx + 1, min(start_peak_idx + 8, len(peaks))):
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

            if upper_r2 < 0.7 or lower_r2 < 0.7:
                continue

            avg_price = (np.mean([p["price"] for p in window_peaks]) +
                         np.mean([t["price"] for t in window_troughs])) / 2

            norm_upper = _normalize_slope(upper_slope, avg_price)
            norm_lower = _normalize_slope(lower_slope, avg_price)

            # Lines must be roughly parallel
            slope_diff = abs(norm_upper - norm_lower)
            if slope_diff > parallel_tolerance:
                continue

            # Classify
            avg_slope = (norm_upper + norm_lower) / 2
            if abs(avg_slope) < 0.02:
                channel_type = "horizontal_channel"
                direction = "neutral"
            elif avg_slope > 0:
                channel_type = "ascending_channel"
                direction = "bullish"
            else:
                channel_type = "descending_channel"
                direction = "bearish"

            resistance = upper_slope * bar_end + upper_intercept
            support = lower_slope * bar_end + lower_intercept
            channel_width = resistance - support

            confidence = round(min(1.0,
                (upper_r2 + lower_r2) / 2 * 0.4 +
                min(total_touches / 10, 0.3) +
                (1 - slope_diff / parallel_tolerance) * 0.3
            ), 2)

            all_pivots = sorted(window_peaks + window_troughs, key=lambda p: p["bar_index"])

            patterns.append({
                "pattern": channel_type,
                "direction": direction,
                "start_date": str(all_pivots[0]["date"]),
                "end_date": str(all_pivots[-1]["date"]),
                "pivots": [
                    {"date": str(p["date"]), "price": round(p["price"], 4),
                     "role": p["type"]} for p in all_pivots
                ],
                "resistance": round(resistance, 4),
                "support": round(support, 4),
                "channel_width": round(channel_width, 4),
                "confirmed": False,
                "confidence": confidence,
            })

    return patterns
