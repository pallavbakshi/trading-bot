import numpy as np
import pandas as pd
from src.pivots import find_pivots


def find_sr_zones(df: pd.DataFrame, order: int = 10, tolerance_pct: float = 1.5,
                  min_touches: int = 3) -> list[dict]:
    """Find support and resistance zones by clustering pivot prices (static, full history)."""
    pivots = find_pivots(df, order=order)
    if pivots.empty:
        return []

    peaks = pivots[pivots["type"] == "peak"]["price"].values
    troughs = pivots[pivots["type"] == "trough"]["price"].values
    peak_dates = pivots[pivots["type"] == "peak"]["date"].values
    trough_dates = pivots[pivots["type"] == "trough"]["date"].values

    zones = []
    zones += _cluster_levels(peaks, peak_dates, tolerance_pct, min_touches, "resistance")
    zones += _cluster_levels(troughs, trough_dates, tolerance_pct, min_touches, "support")
    zones.sort(key=lambda z: z["level"])
    return zones


def find_rolling_sr(df: pd.DataFrame, order: int = 5, lookback: int = 250,
                    step: int = 5, tolerance_pct: float = 1.5,
                    min_touches: int = 2) -> list[dict]:
    """Compute rolling support/resistance levels that evolve over time.

    At each step, looks back `lookback` bars, finds pivot clusters, and
    tracks zones that can be broken and **re-established** when price
    returns to that level and bounces again.

    A zone is:
      - Created when 2+ pivots cluster near a level
      - Broken when price closes decisively through it
      - Re-established (new segment) if new pivots form at the same level
        after a break — this is the key behavior for "broken support becomes
        resistance" and vice-versa.

    No lookahead: at bar i we only use data from bars 0..i.
    """
    n = len(df)
    if n < lookback:
        return []

    dates = df.index
    closes = df["Close"].values

    # Pre-compute all pivots once (pivots at bar i use order bars after i,
    # so the last `order` bars won't have pivots — that's fine, no lookahead
    # because argrelextrema with order=10 needs 10 bars on each side which
    # are all in the past relative to bar i+order)
    all_pivots = find_pivots(df, order=order)
    if all_pivots.empty:
        return []

    pivot_bars = all_pivots["bar_index"].values
    pivot_prices = all_pivots["price"].values
    pivot_types = all_pivots["type"].values
    pivot_dates = all_pivots["date"].values

    # Active zones keyed by (type, quantized_level)
    active: dict[tuple, dict] = {}
    output: list[dict] = []

    # Cooldown: after a zone is broken, don't re-establish at the same
    # level for this many bars (prevents flicker at the boundary)
    cooldown: dict[tuple, int] = {}
    COOLDOWN_BARS = 20

    def _level_key(level: float) -> int:
        """Quantize a price level so nearby prices map to the same key."""
        # Use log-based bucketing so each bucket spans tolerance_pct% of price
        import math
        return round(math.log(level) / (tolerance_pct / 100))

    def _levels_close(a: float, b: float) -> bool:
        return abs(a - b) / max(a, b) * 100 <= tolerance_pct

    for i in range(max(lookback, order * 2), n, step):
        current_date = dates[i]
        current_close = closes[i]

        # --- 1. Check for broken zones FIRST (before adding new ones) ---
        keys_to_break = []
        for key, zone in active.items():
            level = zone["level"]
            broken = False
            if zone["type"] == "resistance" and current_close > level * (1 + tolerance_pct / 200):
                broken = True
            elif zone["type"] == "support" and current_close < level * (1 - tolerance_pct / 200):
                broken = True

            if broken:
                zone["broken"] = True
                zone["broken_date"] = str(current_date)
                zone["end_date"] = str(current_date)
                output.append(zone)
                keys_to_break.append(key)
                cooldown[key] = i + COOLDOWN_BARS

        for key in keys_to_break:
            active.pop(key, None)

        # --- 2. Find pivots in the lookback window (only confirmed pivots) ---
        # A pivot at bar_index j is confirmed and visible at bar i if j <= i - order
        # (we need `order` bars after j to confirm it, all of which are <= i)
        mask = (pivot_bars >= i - lookback) & (pivot_bars <= i - order)
        w_prices = pivot_prices[mask]
        w_types = pivot_types[mask]
        w_dates = pivot_dates[mask]

        if len(w_prices) == 0:
            continue

        # --- 3. Cluster pivots into S/R levels ---
        peak_mask = w_types == "peak"
        trough_mask = w_types == "trough"

        found_keys = set()

        for zone_type, p_mask in [("resistance", peak_mask), ("support", trough_mask)]:
            clusters = _cluster_levels(
                w_prices[p_mask], w_dates[p_mask],
                tolerance_pct, min_touches, zone_type
            )
            for cluster in clusters:
                level = cluster["level"]
                lk = _level_key(level)
                key = (zone_type, lk)
                found_keys.add(key)

                # Skip if in cooldown
                if key in cooldown and i < cooldown[key]:
                    continue

                if key in active:
                    # Update existing zone
                    z = active[key]
                    z["end_date"] = str(current_date)
                    z["touches"] = max(z["touches"], cluster["touches"])
                    # Refine level as weighted average
                    z["level"] = round((z["level"] + level) / 2, 4)
                else:
                    # Only create if level is not already blown through
                    # (resistance must be above price, support below)
                    if zone_type == "resistance" and level < current_close * (1 - tolerance_pct / 200):
                        continue
                    if zone_type == "support" and level > current_close * (1 + tolerance_pct / 200):
                        continue

                    active[key] = {
                        "level": level,
                        "type": zone_type,
                        "start_date": str(current_date),
                        "end_date": str(current_date),
                        "touches": cluster["touches"],
                        "broken": False,
                        "broken_date": None,
                    }

        # --- 4. Expire zones not seen in the window for a while ---
        # If a zone hasn't been refreshed and price has moved far from it,
        # it's no longer relevant
        keys_to_expire = []
        for key, zone in active.items():
            if key in found_keys:
                continue
            level = zone["level"]
            distance_pct = abs(current_close - level) / level * 100
            if distance_pct > tolerance_pct * 10:
                # Price is very far from this level — expire it
                zone["end_date"] = str(current_date)
                output.append(zone)
                keys_to_expire.append(key)

        for key in keys_to_expire:
            active.pop(key, None)

    # Flush remaining active zones
    for zone in active.values():
        output.append(zone)

    output.sort(key=lambda z: z["start_date"])
    return output


def _cluster_levels(prices, dates, tolerance_pct, min_touches, zone_type):
    """Group nearby price levels into zones."""
    if len(prices) == 0:
        return []

    sorted_indices = np.argsort(prices)
    prices = prices[sorted_indices]
    dates = dates[sorted_indices]
    zones = []
    used = set()

    for i in range(len(prices)):
        if i in used:
            continue

        cluster_prices = [prices[i]]
        cluster_dates = [dates[i]]
        used.add(i)

        for j in range(i + 1, len(prices)):
            if j in used:
                continue
            if abs(prices[j] - np.mean(cluster_prices)) / np.mean(cluster_prices) * 100 <= tolerance_pct:
                cluster_prices.append(prices[j])
                cluster_dates.append(dates[j])
                used.add(j)

        if len(cluster_prices) >= min_touches:
            zones.append({
                "level": round(float(np.mean(cluster_prices)), 4),
                "type": zone_type,
                "touches": len(cluster_prices),
                "first_date": str(min(cluster_dates)),
                "last_date": str(max(cluster_dates)),
                "strength": len(cluster_prices),
            })

    return zones


def find_density_sr(df: pd.DataFrame, lookback: int = 250, step: int = 5,
                    num_bins: int = 50, min_density_pct: float = 15.0,
                    tolerance_pct: float = 1.5) -> list[dict]:
    """Find S/R zones using price density (time-at-price).

    For each step, builds a histogram of how many bars in the lookback window
    had their high-low range overlap each price level. Peaks in this histogram
    are levels where price spent significant time — natural S/R.

    A zone is support if current price is above it, resistance if below.
    Zones are broken when price closes decisively through them.

    No lookahead: at bar i we only use bars 0..i.
    """
    import math
    from scipy.signal import find_peaks

    n = len(df)
    if n < lookback:
        return []

    dates = df.index
    highs = df["High"].values.astype(np.float64)
    lows = df["Low"].values.astype(np.float64)
    closes = df["Close"].values.astype(np.float64)

    def _lk(level: float) -> int:
        return round(math.log(level) / (tolerance_pct / 100))

    active: dict[tuple, dict] = {}
    output: list[dict] = []
    cooldown: dict[tuple, int] = {}
    COOLDOWN_BARS = 20

    for i in range(lookback, n, step):
        current_date = dates[i]
        current_close = closes[i]

        # --- 1. Break check ---
        keys_to_break = []
        for key, zone in active.items():
            level = zone["level"]
            if zone["type"] == "resistance" and current_close > level * (1 + tolerance_pct / 200):
                zone["broken"] = True
                zone["broken_date"] = str(current_date)
                zone["end_date"] = str(current_date)
                output.append(zone)
                keys_to_break.append(key)
                cooldown[key] = i + COOLDOWN_BARS
            elif zone["type"] == "support" and current_close < level * (1 - tolerance_pct / 200):
                zone["broken"] = True
                zone["broken_date"] = str(current_date)
                zone["end_date"] = str(current_date)
                output.append(zone)
                keys_to_break.append(key)
                cooldown[key] = i + COOLDOWN_BARS
        for key in keys_to_break:
            active.pop(key, None)

        # --- 2. Build price density histogram (vectorized) ---
        w_highs = highs[i - lookback:i]
        w_lows = lows[i - lookback:i]
        price_min = float(w_lows.min())
        price_max = float(w_highs.max())
        if price_max <= price_min:
            continue

        bin_size = (price_max - price_min) / num_bins
        # Vectorized bin computation
        lo_bins = np.clip(((w_lows - price_min) / bin_size).astype(np.int32), 0, num_bins - 1)
        hi_bins = np.clip(((w_highs - price_min) / bin_size).astype(np.int32), 0, num_bins - 1)

        # Use diff trick: for each bar, +1 at lo_bin, -1 at hi_bin+1, then cumsum
        marks = np.zeros(num_bins + 1, dtype=np.int32)
        np.add.at(marks, lo_bins, 1)
        np.add.at(marks, hi_bins + 1, -1)
        density = np.cumsum(marks[:num_bins])

        # --- 3. Find peaks in density histogram using scipy ---
        min_count = int(lookback * min_density_pct / 100)
        # distance=3 prevents adjacent near-duplicate peaks, prominence filters noise
        peak_indices, properties = find_peaks(
            density, height=min_count, distance=3, prominence=min_count * 0.3
        )

        found_keys = set()
        for b in peak_indices:
            level = price_min + (b + 0.5) * bin_size
            touches = int(density[b])
            zone_type = "support" if current_close > level else "resistance"
            lk = _lk(level)
            key = (zone_type, lk)
            found_keys.add(key)

            if key in cooldown and i < cooldown[key]:
                continue

            if key in active:
                z = active[key]
                z["end_date"] = str(current_date)
                z["touches"] = max(z["touches"], touches)
                z["level"] = round((z["level"] + level) / 2, 4)
            else:
                if zone_type == "resistance" and level < current_close * (1 - tolerance_pct / 200):
                    continue
                if zone_type == "support" and level > current_close * (1 + tolerance_pct / 200):
                    continue

                active[key] = {
                    "level": round(level, 4),
                    "type": zone_type,
                    "start_date": str(current_date),
                    "end_date": str(current_date),
                    "touches": touches,
                    "broken": False,
                    "broken_date": None,
                    "method": "density",
                }

        # --- 4. Expire distant zones ---
        keys_to_expire = []
        for key, zone in active.items():
            if key in found_keys:
                continue
            distance_pct = abs(current_close - zone["level"]) / zone["level"] * 100
            if distance_pct > tolerance_pct * 10:
                zone["end_date"] = str(current_date)
                output.append(zone)
                keys_to_expire.append(key)
        for key in keys_to_expire:
            active.pop(key, None)

    for zone in active.values():
        output.append(zone)

    output.sort(key=lambda z: z["start_date"])
    return output
