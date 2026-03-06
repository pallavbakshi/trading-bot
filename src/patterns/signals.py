import numpy as np
import pandas as pd
import talib


def detect_ma_crossovers(df: pd.DataFrame) -> list[dict]:
    """Detect golden cross and death cross (50/200 SMA crossovers)."""
    close = df["Close"].values.astype(np.float64)
    dates = df.index

    sma50 = talib.SMA(close, timeperiod=50)
    sma200 = talib.SMA(close, timeperiod=200)

    patterns = []

    for i in range(201, len(close)):
        if np.isnan(sma50[i]) or np.isnan(sma200[i]) or np.isnan(sma50[i-1]) or np.isnan(sma200[i-1]):
            continue

        prev_diff = sma50[i-1] - sma200[i-1]
        curr_diff = sma50[i] - sma200[i]

        if prev_diff <= 0 and curr_diff > 0:
            patterns.append({
                "pattern": "golden_cross",
                "direction": "bullish",
                "date": str(dates[i]),
                "sma50": round(float(sma50[i]), 4),
                "sma200": round(float(sma200[i]), 4),
                "confidence": 0.7,
            })
        elif prev_diff >= 0 and curr_diff < 0:
            patterns.append({
                "pattern": "death_cross",
                "direction": "bearish",
                "date": str(dates[i]),
                "sma50": round(float(sma50[i]), 4),
                "sma200": round(float(sma200[i]), 4),
                "confidence": 0.7,
            })

    return patterns


def detect_bollinger_squeeze(df: pd.DataFrame, period: int = 20,
                             squeeze_threshold_pct: float = 4.0,
                             lookback: int = 120) -> list[dict]:
    """Detect Bollinger Band squeezes (low volatility preceding breakout).

    A squeeze occurs when bandwidth (upper - lower) / middle drops to
    its lowest level in the lookback period.
    """
    close = df["Close"].values.astype(np.float64)
    dates = df.index

    upper, middle, lower = talib.BBANDS(close, timeperiod=period)

    patterns = []

    for i in range(lookback, len(close)):
        if np.isnan(upper[i]) or np.isnan(lower[i]) or np.isnan(middle[i]):
            continue
        if middle[i] == 0:
            continue

        bandwidth = (upper[i] - lower[i]) / middle[i] * 100

        # Check if this is the tightest squeeze in the lookback window
        window_bandwidths = []
        for j in range(i - lookback, i):
            if not np.isnan(upper[j]) and not np.isnan(lower[j]) and middle[j] != 0:
                window_bandwidths.append((upper[j] - lower[j]) / middle[j] * 100)

        if not window_bandwidths:
            continue

        min_bw = min(window_bandwidths)

        if bandwidth <= min_bw and bandwidth < squeeze_threshold_pct:
            # Avoid consecutive squeeze signals
            if patterns and patterns[-1].get("_bar_index", 0) >= i - 5:
                continue

            patterns.append({
                "pattern": "bollinger_squeeze",
                "direction": "neutral",
                "date": str(dates[i]),
                "bandwidth": round(bandwidth, 4),
                "upper": round(float(upper[i]), 4),
                "lower": round(float(lower[i]), 4),
                "confidence": round(min(1.0, (squeeze_threshold_pct - bandwidth) / squeeze_threshold_pct), 2),
                "_bar_index": i,
            })

    # Clean internal field
    for p in patterns:
        del p["_bar_index"]

    return patterns


def detect_volume_climax(df: pd.DataFrame, vol_multiplier: float = 3.0,
                         lookback: int = 50) -> list[dict]:
    """Detect volume climax events (extreme volume + reversal bar).

    A volume climax is a bar with unusually high volume that often marks
    exhaustion and precedes a reversal.
    """
    close = df["Close"].values
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    volumes = df["Volume"].values.astype(np.float64)
    dates = df.index

    patterns = []

    for i in range(lookback, len(df)):
        avg_vol = np.mean(volumes[i - lookback:i])
        if avg_vol == 0:
            continue

        vol_ratio = volumes[i] / avg_vol

        if vol_ratio < vol_multiplier:
            continue

        bar_range = highs[i] - lows[i]
        if bar_range == 0:
            continue

        body = abs(close[i] - opens[i])
        body_ratio = body / bar_range

        # Wide range bar with high volume
        is_up = close[i] > opens[i]

        # Check for reversal: next bar goes opposite direction
        if i + 1 >= len(df):
            continue

        next_reversal = (is_up and close[i + 1] < opens[i + 1]) or \
                        (not is_up and close[i + 1] > opens[i + 1])

        if not next_reversal:
            continue

        direction = "bearish" if is_up else "bullish"

        patterns.append({
            "pattern": "volume_climax",
            "direction": direction,
            "date": str(dates[i]),
            "volume_ratio": round(vol_ratio, 2),
            "close": round(close[i], 4),
            "body_ratio": round(body_ratio, 2),
            "confidence": round(min(1.0, vol_ratio / 6 * 0.5 + body_ratio * 0.5), 2),
        })

    return patterns
