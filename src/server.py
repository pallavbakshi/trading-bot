"""Simple HTTP API server that serves ticker data and pattern results.

Endpoints:
  GET /api/tickers         — list of available tickers
  GET /api/ticker/<TICKER> — { daily: {...}, weekly: {...}, monthly: {...} }

Each timeframe contains: bars, result (patterns/signals), sma50, sma200, rsi.
All pre-computed and cached on first request.
"""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import numpy as np
import pandas as pd
import talib

from src.loader import load_csv
from src.scanner import scan_ticker

# Cache scanned results so we don't re-scan on every request
_cache: dict = {}


def _find_csv(ticker: str, data_dirs: list[str]) -> Path | None:
    for d in data_dirs:
        p = Path(d) / f"{ticker.lower()}.csv"
        if p.exists():
            return p
        p = Path(d) / f"{ticker.lower().replace('&', '_')}.csv"
        if p.exists():
            return p
    return None


def _list_tickers(data_dirs: list[str]) -> list[str]:
    tickers = []
    for d in data_dirs:
        dp = Path(d)
        if not dp.exists():
            continue
        for f in sorted(dp.glob("*.csv")):
            tickers.append(f.stem.upper())
    return sorted(set(tickers))


def _aggregate_df(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Aggregate daily OHLCV DataFrame to weekly or monthly."""
    # Ensure DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)

    if freq == "weekly":
        # Group by Monday of each week
        monday = df.index - pd.to_timedelta(df.index.weekday, unit="D")
        grouper = monday
    else:
        grouper = df.index.to_period("M")

    rows = []
    indices = []
    for _, group in df.groupby(grouper):
        rows.append({
            "Open": group["Open"].iloc[0],
            "High": group["High"].max(),
            "Low": group["Low"].min(),
            "Close": group["Close"].iloc[-1],
            "Volume": group["Volume"].sum(),
        })
        indices.append(group.index[-1])  # last trading day in period

    result = pd.DataFrame(rows, index=pd.DatetimeIndex(indices))
    return result


def _df_to_bars(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to list of bar dicts."""
    bars = []
    for date, row in df.iterrows():
        bars.append({
            "date": str(date)[:10],
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"]),
        })
    return bars


def _compute_indicators(df: pd.DataFrame) -> dict:
    """Compute SMA50, SMA200, RSI14 for a DataFrame."""
    close = df["Close"].values.astype(np.float64)
    sma50 = talib.SMA(close, timeperiod=50)
    sma200 = talib.SMA(close, timeperiod=200)
    rsi = talib.RSI(close, timeperiod=14)

    def to_list(arr, decimals=4):
        return [round(float(v), decimals) if not np.isnan(v) else None for v in arr]

    return {
        "sma50": to_list(sma50),
        "sma200": to_list(sma200),
        "rsi": to_list(rsi, 2),
    }


def _empty_result(ticker: str, df: pd.DataFrame) -> dict:
    """Return an empty scan result for when scanning fails."""
    dates = [str(d)[:10] for d in df.index]
    return {
        "ticker": ticker,
        "bars": len(df),
        "date_range": [dates[0], dates[-1]] if dates else ["", ""],
        "candlestick_patterns": [],
        "geometric_patterns": [],
        "gaps": [],
        "island_reversals": [],
        "divergences": [],
        "signals": [],
        "support_resistance": [],
        "rolling_sr": [],
        "density_sr": [],
    }


def _build_timeframe(ticker: str, df: pd.DataFrame, result: dict | None = None) -> dict:
    """Build complete timeframe data: bars + scan result + indicators."""
    if result is None:
        try:
            result = scan_ticker(ticker, df)
        except Exception as e:
            print(f"  Warning: scan failed for {ticker}: {e}")
            result = _empty_result(ticker, df)

    return {
        "bars": _df_to_bars(df),
        "result": result,
        **_compute_indicators(df),
    }


def _get_ticker_data(ticker: str, data_dirs: list[str], results_map: dict) -> dict | None:
    if ticker in _cache:
        return _cache[ticker]

    csv_path = _find_csv(ticker, data_dirs)
    if csv_path is None:
        return None

    df = load_csv(csv_path)

    # Daily: use pre-computed result if available
    daily_result = results_map.get(ticker.upper())
    if daily_result is None:
        print(f"  Scanning {ticker} (daily)...")

    # Aggregate to weekly/monthly
    df_w = _aggregate_df(df, "weekly")
    df_m = _aggregate_df(df, "monthly")
    print(f"  Building {ticker}: D={len(df)} W={len(df_w)} M={len(df_m)} bars")

    data = {
        "daily": _build_timeframe(ticker, df, daily_result),
        "weekly": _build_timeframe(ticker, df_w),
        "monthly": _build_timeframe(ticker, df_m),
    }
    _cache[ticker] = data
    return data


class Handler(BaseHTTPRequestHandler):
    data_dirs: list[str] = []
    results_map: dict = {}

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/api/tickers":
            tickers = _list_tickers(self.data_dirs)
            self._json(tickers)

        elif path.startswith("/api/ticker/"):
            ticker = path.split("/")[-1].upper()
            data = _get_ticker_data(ticker, self.data_dirs, self.results_map)
            if data is None:
                self.send_error(404, f"Ticker {ticker} not found")
                return
            self._json(data)

        else:
            self.send_error(404)

    def _json(self, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quieter logging
        if args and "404" in str(args[0]):
            return
        print(f"  {args[0]}" if args else "")


def run_server(data_dirs: list[str] = None, results_paths: list[str] = None,
               host: str = "127.0.0.1", port: int = 8000):
    if data_dirs is None:
        data_dirs = ["data"]
    if results_paths is None:
        results_paths = []

    # Load pre-computed results
    results_map = {}
    for rp in results_paths:
        p = Path(rp)
        if p.exists():
            with open(p) as f:
                for r in json.load(f):
                    results_map[r["ticker"].upper()] = r
            print(f"Loaded {len(results_map)} pre-computed results from {rp}")

    Handler.data_dirs = data_dirs
    Handler.results_map = results_map

    import socket
    server = HTTPServer((host, port), Handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    server.server_bind()
    server.server_activate()
    print(f"\nAPI server running at http://{host}:{port}")
    print(f"Data dirs: {data_dirs}")
    print(f"Tickers: {len(_list_tickers(data_dirs))}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    data_dirs = ["data"]
    results_paths = ["results/patterns.json"]

    # Parse args: --data-dir <dir> --results <path>
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--data-dir":
            data_dirs.append(args[i + 1])
            i += 2
        elif args[i] == "--results":
            results_paths.append(args[i + 1])
            i += 2
        elif args[i] == "--port":
            i += 2  # handled below
        else:
            i += 1

    port = 8000
    if "--port" in args:
        port = int(args[args.index("--port") + 1])

    run_server(data_dirs=data_dirs, results_paths=results_paths, port=port)
