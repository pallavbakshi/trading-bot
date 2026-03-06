import numpy as np
import pandas as pd


def _fit_trendline(points):
    """Fit a line through pivot points. Returns (slope, intercept, r_squared)."""
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
    """Convert slope to percentage per bar for comparability across price levels."""
    if avg_price == 0:
        return 0
    return slope / avg_price * 100


def detect_triangles(df: pd.DataFrame, pivots: pd.DataFrame,
                     min_touches: int = 4,
                     min_pattern_bars: int = 15,
                     max_pattern_bars: int = 200,
                     flat_slope_threshold: float = 0.02,
                     convergence_threshold: float = 0.5) -> list[dict]:
    """Detect ascending, descending, and symmetrical triangles.

    Uses a sliding window over pivots to find converging trendlines.

    - Ascending: flat upper trendline, rising lower trendline
    - Descending: falling upper trendline, flat lower trendline
    - Symmetrical: both trendlines converge (upper falling, lower rising)
    """
    peaks = pivots[pivots["type"] == "peak"].to_dict("records")
    troughs = pivots[pivots["type"] == "trough"].to_dict("records")

    if len(peaks) < 2 or len(troughs) < 2:
        return []

    patterns = []

    # Sliding window: try different spans of pivots
    for start_peak_idx in range(len(peaks) - 1):
        for end_peak_idx in range(start_peak_idx + 1, min(start_peak_idx + 6, len(peaks))):
            window_peaks = peaks[start_peak_idx:end_peak_idx + 1]

            bar_start = window_peaks[0]["bar_index"]
            bar_end = window_peaks[-1]["bar_index"]
            span = bar_end - bar_start

            if span < min_pattern_bars or span > max_pattern_bars:
                continue

            # Find troughs within the same bar range
            window_troughs = [t for t in troughs if bar_start <= t["bar_index"] <= bar_end]

            total_touches = len(window_peaks) + len(window_troughs)
            if total_touches < min_touches or len(window_troughs) < 2:
                continue

            upper_slope, upper_intercept, upper_r2 = _fit_trendline(window_peaks)
            lower_slope, lower_intercept, lower_r2 = _fit_trendline(window_troughs)

            if upper_slope is None or lower_slope is None:
                continue

            # Both trendlines should fit reasonably well
            if upper_r2 < 0.6 or lower_r2 < 0.6:
                continue

            avg_price = (np.mean([p["price"] for p in window_peaks]) +
                         np.mean([t["price"] for t in window_troughs])) / 2

            norm_upper = _normalize_slope(upper_slope, avg_price)
            norm_lower = _normalize_slope(lower_slope, avg_price)

            # Must be converging (upper slope < lower slope)
            if norm_upper >= norm_lower:
                continue

            # Classify triangle type
            upper_flat = abs(norm_upper) < flat_slope_threshold
            lower_flat = abs(norm_lower) < flat_slope_threshold

            if upper_flat and norm_lower > 0:
                tri_type = "ascending_triangle"
                direction = "bullish"
            elif lower_flat and norm_upper < 0:
                tri_type = "descending_triangle"
                direction = "bearish"
            elif norm_upper < 0 and norm_lower > 0:
                tri_type = "symmetrical_triangle"
                direction = "neutral"
            else:
                continue

            # Calculate apex (where trendlines converge)
            if abs(upper_slope - lower_slope) > 1e-10:
                apex_bar = (lower_intercept - upper_intercept) / (upper_slope - lower_slope)
            else:
                apex_bar = bar_end + span

            # Breakout level at the end of the pattern
            breakout_upper = upper_slope * bar_end + upper_intercept
            breakout_lower = lower_slope * bar_end + lower_intercept

            pattern_height = breakout_upper - breakout_lower
            if direction == "bullish":
                target = breakout_upper + pattern_height
            elif direction == "bearish":
                target = breakout_lower - pattern_height
            else:
                target = None

            confidence = round(min(1.0,
                (upper_r2 + lower_r2) / 2 * 0.5 +
                min(total_touches / 8, 0.3) +
                0.2
            ), 2)

            all_pivots = sorted(window_peaks + window_troughs, key=lambda p: p["bar_index"])

            patterns.append({
                "pattern": tri_type,
                "direction": direction,
                "start_date": str(all_pivots[0]["date"]),
                "end_date": str(all_pivots[-1]["date"]),
                "pivots": [
                    {"date": str(p["date"]), "price": round(p["price"], 4),
                     "role": p["type"]} for p in all_pivots
                ],
                "upper_slope": round(norm_upper, 4),
                "lower_slope": round(norm_lower, 4),
                "target": round(target, 4) if target else None,
                "confirmed": False,
                "confidence": confidence,
            })

    return patterns
