"""Simple HTTP API server that serves ticker data and pattern results.

Endpoints:
  GET /api/tickers         — list of available tickers
  GET /api/ticker/<TICKER> — { bars: [...], result: {...} }
"""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

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


def _get_ticker_data(ticker: str, data_dirs: list[str], results_map: dict) -> dict | None:
    if ticker in _cache:
        return _cache[ticker]

    csv_path = _find_csv(ticker, data_dirs)
    if csv_path is None:
        return None

    df = load_csv(csv_path)
    bars = []
    for date, row in df.iterrows():
        bars.append({
            "date": str(date),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"]),
        })

    # Get scan results — from pre-computed file or scan live
    result = results_map.get(ticker.upper())
    if result is None:
        print(f"  Scanning {ticker} live...")
        result = scan_ticker(ticker, df)

    data = {"bars": bars, "result": result}
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
