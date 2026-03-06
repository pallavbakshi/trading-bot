import numpy as np
import pandas as pd
import talib
from src.pivots import find_pivots


def detect_rsi_divergence(df: pd.DataFrame, rsi_period: int = 14,
                          pivot_order: int = 5, lookback: int = 100) -> list[dict]:
    """Detect bullish and bearish RSI divergence.

    - Bullish divergence: price makes lower low, RSI makes higher low
    - Bearish divergence: price makes higher high, RSI makes lower high
    """
    close = df["Close"].values.astype(np.float64)
    rsi = talib.RSI(close, timeperiod=rsi_period)

    rsi_series = pd.DataFrame({"High": rsi, "Low": rsi}, index=df.index)
    # Replace NaN with 50 for early bars where RSI isn't calculated
    rsi_series = rsi_series.fillna(50)

    price_pivots = find_pivots(df, order=pivot_order)
    rsi_pivots = find_pivots(rsi_series, order=pivot_order)

    if price_pivots.empty or rsi_pivots.empty:
        return []

    patterns = []
    dates = df.index

    # Bearish divergence: higher highs in price, lower highs in RSI
    price_peaks = price_pivots[price_pivots["type"] == "peak"].reset_index(drop=True)
    for i in range(len(price_peaks) - 1):
        p1 = price_peaks.iloc[i]
        p2 = price_peaks.iloc[i + 1]

        if p2["bar_index"] - p1["bar_index"] > lookback:
            continue

        # Price made higher high
        if p2["price"] <= p1["price"]:
            continue

        # RSI at those same bar positions
        idx1 = p1["bar_index"]
        idx2 = p2["bar_index"]
        if idx1 >= len(rsi) or idx2 >= len(rsi):
            continue

        rsi1 = rsi[idx1]
        rsi2 = rsi[idx2]

        if np.isnan(rsi1) or np.isnan(rsi2):
            continue

        # RSI made lower high
        if rsi2 < rsi1:
            patterns.append({
                "pattern": "bearish_rsi_divergence",
                "direction": "bearish",
                "start_date": str(p1["date"]),
                "end_date": str(p2["date"]),
                "price_1": round(p1["price"], 4),
                "price_2": round(p2["price"], 4),
                "rsi_1": round(float(rsi1), 2),
                "rsi_2": round(float(rsi2), 2),
                "confidence": round(min(1.0,
                    abs(rsi1 - rsi2) / 20 * 0.5 +
                    (1 if rsi1 > 70 else 0.3) * 0.5
                ), 2),
            })

    # Bullish divergence: lower lows in price, higher lows in RSI
    price_troughs = price_pivots[price_pivots["type"] == "trough"].reset_index(drop=True)
    for i in range(len(price_troughs) - 1):
        t1 = price_troughs.iloc[i]
        t2 = price_troughs.iloc[i + 1]

        if t2["bar_index"] - t1["bar_index"] > lookback:
            continue

        if t2["price"] >= t1["price"]:
            continue

        idx1 = t1["bar_index"]
        idx2 = t2["bar_index"]
        if idx1 >= len(rsi) or idx2 >= len(rsi):
            continue

        rsi1 = rsi[idx1]
        rsi2 = rsi[idx2]

        if np.isnan(rsi1) or np.isnan(rsi2):
            continue

        if rsi2 > rsi1:
            patterns.append({
                "pattern": "bullish_rsi_divergence",
                "direction": "bullish",
                "start_date": str(t1["date"]),
                "end_date": str(t2["date"]),
                "price_1": round(t1["price"], 4),
                "price_2": round(t2["price"], 4),
                "rsi_1": round(float(rsi1), 2),
                "rsi_2": round(float(rsi2), 2),
                "confidence": round(min(1.0,
                    abs(rsi1 - rsi2) / 20 * 0.5 +
                    (1 if rsi1 < 30 else 0.3) * 0.5
                ), 2),
            })

    return patterns


def detect_macd_divergence(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                           signal: int = 9, pivot_order: int = 5,
                           lookback: int = 100) -> list[dict]:
    """Detect bullish and bearish MACD divergence."""
    close = df["Close"].values.astype(np.float64)
    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=fast,
                                               slowperiod=slow, signalperiod=signal)

    macd_series = pd.DataFrame({"High": macd_hist, "Low": macd_hist}, index=df.index)
    macd_series = macd_series.fillna(0)

    price_pivots = find_pivots(df, order=pivot_order)

    if price_pivots.empty:
        return []

    patterns = []
    dates = df.index

    # Bearish: higher price highs, lower MACD histogram highs
    price_peaks = price_pivots[price_pivots["type"] == "peak"].reset_index(drop=True)
    for i in range(len(price_peaks) - 1):
        p1 = price_peaks.iloc[i]
        p2 = price_peaks.iloc[i + 1]

        if p2["bar_index"] - p1["bar_index"] > lookback:
            continue
        if p2["price"] <= p1["price"]:
            continue

        idx1, idx2 = p1["bar_index"], p2["bar_index"]
        if idx1 >= len(macd_hist) or idx2 >= len(macd_hist):
            continue

        m1 = macd_hist[idx1]
        m2 = macd_hist[idx2]
        if np.isnan(m1) or np.isnan(m2):
            continue

        if m2 < m1:
            patterns.append({
                "pattern": "bearish_macd_divergence",
                "direction": "bearish",
                "start_date": str(p1["date"]),
                "end_date": str(p2["date"]),
                "price_1": round(p1["price"], 4),
                "price_2": round(p2["price"], 4),
                "macd_hist_1": round(float(m1), 4),
                "macd_hist_2": round(float(m2), 4),
                "confidence": round(min(1.0, abs(m1 - m2) / max(abs(m1), 0.001) * 0.5 + 0.3), 2),
            })

    # Bullish: lower price lows, higher MACD histogram lows
    price_troughs = price_pivots[price_pivots["type"] == "trough"].reset_index(drop=True)
    for i in range(len(price_troughs) - 1):
        t1 = price_troughs.iloc[i]
        t2 = price_troughs.iloc[i + 1]

        if t2["bar_index"] - t1["bar_index"] > lookback:
            continue
        if t2["price"] >= t1["price"]:
            continue

        idx1, idx2 = t1["bar_index"], t2["bar_index"]
        if idx1 >= len(macd_hist) or idx2 >= len(macd_hist):
            continue

        m1 = macd_hist[idx1]
        m2 = macd_hist[idx2]
        if np.isnan(m1) or np.isnan(m2):
            continue

        if m2 > m1:
            patterns.append({
                "pattern": "bullish_macd_divergence",
                "direction": "bullish",
                "start_date": str(t1["date"]),
                "end_date": str(t2["date"]),
                "price_1": round(t1["price"], 4),
                "price_2": round(t2["price"], 4),
                "macd_hist_1": round(float(m1), 4),
                "macd_hist_2": round(float(m2), 4),
                "confidence": round(min(1.0, abs(m1 - m2) / max(abs(m1), 0.001) * 0.5 + 0.3), 2),
            })

    return patterns
