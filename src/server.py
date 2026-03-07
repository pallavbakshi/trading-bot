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

# Cache serialized + gzipped JSON responses (in-memory + disk)
_json_cache: dict[str, bytes] = {}
_disk_cache_dir: Path | None = None
# Command queue for CLI sidecar
_command_queue: list[dict] = []
# Snapshot result (set by frontend, polled by CLI)
_snapshot_result: dict | None = None
# Chart state (posted by frontend on every draw)
_chart_state: dict = {}


def _find_csv(ticker: str, data_dirs: list[str]) -> Path | None:
    for d in data_dirs:
        p = Path(d) / f"{ticker.lower()}.csv"
        if p.exists():
            return p
        p = Path(d) / f"{ticker.lower().replace('&', '_')}.csv"
        if p.exists():
            return p
    return None


def _list_tickers(data_dirs: list[str]) -> list[dict]:
    seen = set()
    tickers = []
    for d in data_dirs:
        dp = Path(d)
        if not dp.exists():
            continue
        # Determine exchange from directory name
        dirname = dp.name.lower()
        if dirname == "nse":
            exchange = "NSE"
        else:
            exchange = "US"
        for f in sorted(dp.glob("*.csv")):
            t = f.stem.upper()
            if t not in seen:
                seen.add(t)
                tickers.append({"ticker": t, "exchange": exchange})
    tickers.sort(key=lambda x: x["ticker"])
    return tickers


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


def _get_ticker_data(ticker: str, data_dirs: list[str], results_map: dict) -> bool:
    """Build ticker data and cache it. Returns True if data exists."""
    if ticker in _json_cache:
        return True

    # Try disk cache first
    if _disk_cache_dir:
        disk_path = _disk_cache_dir / f"{ticker}.json.gz"
        if disk_path.exists():
            _json_cache[ticker] = disk_path.read_bytes()
            return True

    csv_path = _find_csv(ticker, data_dirs)
    if csv_path is None:
        return False

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

    # Serialize, gzip, cache in memory + disk
    import gzip as gz
    raw = json.dumps(data, default=str).encode()
    compressed = gz.compress(raw)
    _json_cache[ticker] = compressed

    if _disk_cache_dir:
        _disk_cache_dir.mkdir(parents=True, exist_ok=True)
        (_disk_cache_dir / f"{ticker}.json.gz").write_bytes(compressed)

    return True


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
            # Serve from pre-cached gzipped JSON if available
            if ticker in _json_cache:
                accept = self.headers.get("Accept-Encoding", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                if "gzip" in accept:
                    self.send_header("Content-Encoding", "gzip")
                    self.end_headers()
                    self.wfile.write(_json_cache[ticker])
                else:
                    import gzip as gz
                    self.end_headers()
                    self.wfile.write(gz.decompress(_json_cache[ticker]))
                return
            found = _get_ticker_data(ticker, self.data_dirs, self.results_map)
            if not found:
                self.send_error(404, f"Ticker {ticker} not found")
                return
            # Now it's in _json_cache, serve it
            accept = self.headers.get("Accept-Encoding", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            if "gzip" in accept:
                self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.write(_json_cache[ticker])
            else:
                import gzip as gz
                self.end_headers()
                self.wfile.write(gz.decompress(_json_cache[ticker]))

        elif path == "/api/commands/poll":
            cmds = list(_command_queue)
            _command_queue.clear()
            self._json(cmds)

        elif path == "/api/state":
            self._json(_chart_state)

        elif path == "/api/snapshot/result":
            global _snapshot_result
            if _snapshot_result:
                result = _snapshot_result
                _snapshot_result = None
                self._json(result)
            else:
                self._json(None)

        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/api/command":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                cmd = json.loads(body)
                _command_queue.append(cmd)
                self._json({"ok": True})
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")

        elif path == "/api/state":
            global _chart_state
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                _chart_state = json.loads(body)
                self._json({"ok": True})
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")

        elif path == "/api/snapshot/save":
            import base64
            global _snapshot_result
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                save_dir = Path(data.get("save_dir", "."))
                save_dir.mkdir(parents=True, exist_ok=True)
                prefix = data.get("prefix", "snapshot")
                # Save PNG
                png_path = save_dir / f"{prefix}.png"
                png_b64 = data["png"].split(",", 1)[-1]
                png_path.write_bytes(base64.b64decode(png_b64))
                # Save CSV
                csv_path = save_dir / f"{prefix}.csv"
                csv_path.write_text(data["csv"])
                _snapshot_result = {
                    "ok": True,
                    "png": str(png_path),
                    "csv": str(csv_path),
                }
                self._json({"ok": True})
            except Exception as e:
                _snapshot_result = {"ok": False, "error": str(e)}
                self.send_error(400, str(e))
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, obj):
        import gzip as gz
        body = json.dumps(obj, default=str).encode()
        accept = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept:
            body = gz.compress(body)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
        else:
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

    # Disk cache for pre-built ticker data
    global _disk_cache_dir
    _disk_cache_dir = Path(".cache/ticker_data")
    _disk_cache_dir.mkdir(parents=True, exist_ok=True)

    # Pre-warm cache in background thread
    tickers = _list_tickers(data_dirs)

    import threading
    import time

    def _warm_cache():
        t0 = time.time()
        # Load from disk first (instant)
        disk_hits = 0
        for t in tickers:
            tk = t["ticker"]
            disk_path = _disk_cache_dir / f"{tk}.json.gz"
            if disk_path.exists() and tk not in _json_cache:
                _json_cache[tk] = disk_path.read_bytes()
                disk_hits += 1
        if disk_hits:
            print(f"Loaded {disk_hits}/{len(tickers)} tickers from disk cache in {time.time()-t0:.1f}s")

        # Build any missing tickers
        missing = [t for t in tickers if t["ticker"] not in _json_cache]
        if missing:
            print(f"Building {len(missing)} uncached tickers...")
            for i, t in enumerate(missing):
                _get_ticker_data(t["ticker"], data_dirs, results_map)
                if (i + 1) % 10 == 0:
                    print(f"  Built {i + 1}/{len(missing)} tickers...")
            print(f"Cache complete: {len(missing)} tickers built in {time.time()-t0:.1f}s")
        else:
            print(f"All {len(tickers)} tickers cached (disk)")

    threading.Thread(target=_warm_cache, daemon=True).start()

    import socket
    server = HTTPServer((host, port), Handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    server.server_bind()
    server.server_activate()
    print(f"\nAPI server running at http://{host}:{port}")
    print(f"Data dirs: {data_dirs}")
    print(f"Tickers: {len(tickers)}\n")

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
