import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


def find_pivots(df: pd.DataFrame, order: int = 5) -> pd.DataFrame:
    """Find local peaks and troughs in price data.

    Args:
        df: OHLCV DataFrame with High and Low columns.
        order: How many bars on each side to confirm a pivot.
              order=5 means a high must be higher than 5 bars before and after.

    Returns:
        DataFrame with columns: date, price, type ('peak' or 'trough'), bar_index
    """
    highs = df["High"].values
    lows = df["Low"].values
    dates = df.index

    peak_indices = argrelextrema(highs, np.greater_equal, order=order)[0]
    trough_indices = argrelextrema(lows, np.less_equal, order=order)[0]

    pivots = []
    for idx in peak_indices:
        pivots.append({
            "date": dates[idx],
            "price": highs[idx],
            "type": "peak",
            "bar_index": int(idx),
        })
    for idx in trough_indices:
        pivots.append({
            "date": dates[idx],
            "price": lows[idx],
            "type": "trough",
            "bar_index": int(idx),
        })

    result = pd.DataFrame(pivots)
    if not result.empty:
        result = result.sort_values("bar_index").reset_index(drop=True)
    return result


def find_pivots_multi(df: pd.DataFrame, orders: list[int] = None) -> dict[int, pd.DataFrame]:
    """Find pivots at multiple scales."""
    if orders is None:
        orders = [5, 10, 20]
    return {order: find_pivots(df, order=order) for order in orders}


def zigzag(df: pd.DataFrame, pct_threshold: float = 5.0) -> pd.DataFrame:
    """Zigzag indicator: alternate between peaks and troughs with minimum % move.

    Args:
        df: OHLCV DataFrame.
        pct_threshold: Minimum percentage move to register a new pivot.

    Returns:
        DataFrame with alternating peak/trough pivots.
    """
    highs = df["High"].values
    lows = df["Low"].values
    dates = df.index
    n = len(df)

    pivots = []
    last_type = None
    last_price = highs[0]
    last_idx = 0

    for i in range(1, n):
        if last_type is None or last_type == "trough":
            if highs[i] >= last_price:
                last_price = highs[i]
                last_idx = i
            pct_drop = (last_price - lows[i]) / last_price * 100
            if pct_drop >= pct_threshold:
                pivots.append({
                    "date": dates[last_idx],
                    "price": last_price,
                    "type": "peak",
                    "bar_index": int(last_idx),
                })
                last_type = "peak"
                last_price = lows[i]
                last_idx = i
        else:
            if lows[i] <= last_price:
                last_price = lows[i]
                last_idx = i
            pct_rise = (highs[i] - last_price) / last_price * 100
            if pct_rise >= pct_threshold:
                pivots.append({
                    "date": dates[last_idx],
                    "price": last_price,
                    "type": "trough",
                    "bar_index": int(last_idx),
                })
                last_type = "trough"
                last_price = highs[i]
                last_idx = i

    # Add the final pivot
    if last_type == "peak" or last_type is None:
        pivots.append({
            "date": dates[last_idx],
            "price": last_price,
            "type": "trough" if last_type == "peak" else "peak",
            "bar_index": int(last_idx),
        })
    else:
        pivots.append({
            "date": dates[last_idx],
            "price": last_price,
            "type": "peak",
            "bar_index": int(last_idx),
        })

    return pd.DataFrame(pivots)
