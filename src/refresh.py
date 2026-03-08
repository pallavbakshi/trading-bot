"""Refresh ticker CSV data using the tv CLI tool.

Downloads data from the last known date to today, overwrites the last
row (which may have been a partial trading day), and merges cleanly.

Usage:
    uv run python -m src.refresh RELIANCE          # auto-detect NSE
    uv run python -m src.refresh NSE:RELIANCE      # explicit
    uv run python -m src.refresh MSFT              # auto-detect SP
    uv run python -m src.refresh --all             # all tickers
    uv run python -m src.refresh --all --exchange nse
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DATA_DIRS = {
    "nse": Path("data/nse"),
    "sp":  Path("data/sp"),
}
CACHE_DIR = Path(".cache/ticker_data")

# ── tv helpers ────────────────────────────────────────────────────────────────

def _run_tv(args: list[str], timeout: int = 60) -> dict:
    """Run a tv command with --json --quiet. Returns parsed JSON. Raises on error."""
    cmd = ["tv", "--json", "--quiet"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"tv {args[0]} timed out after {timeout}s")

    if r.returncode == 2:
        raise RuntimeError(
            "tv bridge not running — open Chrome and run: tv auth"
        )
    if r.returncode != 0:
        err = r.stderr.strip() or r.stdout.strip()
        raise RuntimeError(f"tv {' '.join(args)} failed: {err}")

    return json.loads(r.stdout) if r.stdout.strip() else {}


def check_bridge():
    """Raise if the tv bridge is not running."""
    _run_tv(["status"])


# ── CSV helpers ───────────────────────────────────────────────────────────────

def find_ticker(ticker_input: str) -> tuple[Path, str]:
    """Resolve a ticker name to (csv_path, tv_symbol).

    Accepts:
      RELIANCE          → auto-detect from data/nse/ or data/sp/
      NSE:RELIANCE      → explicit NSE
      MSFT              → auto-detect from data/sp/
    """
    if ":" in ticker_input:
        prefix, symbol = ticker_input.upper().split(":", 1)
        dir_key = "nse" if prefix == "NSE" else "sp"
        filename = f"{symbol.lower().replace('&', '_')}.csv"
        csv_path = DATA_DIRS[dir_key] / filename
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")
        tv_symbol = f"{prefix}:{symbol}" if prefix == "NSE" else symbol
        return csv_path, tv_symbol

    symbol = ticker_input.upper()
    nse_file = DATA_DIRS["nse"] / f"{symbol.lower().replace('&', '_')}.csv"
    sp_file  = DATA_DIRS["sp"]  / f"{symbol.lower()}.csv"

    in_nse = nse_file.exists()
    in_sp  = sp_file.exists()

    if in_nse and in_sp:
        raise ValueError(
            f"{symbol} found in both data/nse/ and data/sp/ — "
            f"specify NSE:{symbol} or SP:{symbol}"
        )
    if in_nse:
        return nse_file, f"NSE:{symbol}"
    if in_sp:
        return sp_file, symbol
    raise FileNotFoundError(
        f"No CSV found for {symbol} in data/nse/ or data/sp/"
    )


def last_date(csv_path: Path) -> date:
    df = pd.read_csv(csv_path)
    return datetime.fromtimestamp(int(df["time"].iloc[-1])).date()


# ── Core refresh logic ────────────────────────────────────────────────────────

def _download(tv_symbol: str, from_date: date, to_date: date, out: Path):
    """Set chart state and download OHLCV CSV for the date range."""
    from_str = from_date.strftime("%Y-%m-%d")
    to_str   = to_date.strftime("%Y-%m-%d")

    # 1. Set symbol (exit 0 = success, we trust it)
    print(f"    tv symbol {tv_symbol}")
    _run_tv(["symbol", tv_symbol])
    print(f"    symbol set")

    # 3. Force daily timeframe
    print(f"    tv tf D")
    _run_tv(["tf", "D"])

    # 4. Set date range
    print(f"    tv goto {from_str} --to {to_str}")
    result = _run_tv(["goto", from_str, "--to", to_str], timeout=20)
    # Verify the range was accepted
    confirmed_from = result.get("date", "")
    confirmed_to   = result.get("endDate", "")
    if confirmed_from and confirmed_from != from_str:
        raise RuntimeError(
            f"Date mismatch: requested {from_str}, got {confirmed_from}"
        )
    print(f"    range confirmed: {confirmed_from} → {confirmed_to}")

    # 5. Download
    print(f"    tv download → {out}")
    dl = _run_tv(["download", "-o", str(out)], timeout=60)

    # 6. Verify file
    if not out.exists():
        raise RuntimeError(f"Download reported success but file missing: {out}")
    size = out.stat().st_size
    if size == 0:
        raise RuntimeError("Downloaded file is empty")
    print(f"    downloaded {size:,} bytes")

    # 7. Verify the downloaded CSV has data and correct columns
    new_df = pd.read_csv(out)
    if new_df.empty:
        raise RuntimeError("Downloaded CSV has no rows")
    expected_cols = {"time", "open", "high", "low", "close", "Volume"}
    actual_cols   = set(new_df.columns)
    if not expected_cols.issubset(actual_cols):
        raise RuntimeError(
            f"Unexpected columns in download: {actual_cols}"
        )
    dl_first = datetime.fromtimestamp(int(new_df["time"].iloc[0])).date()
    dl_last  = datetime.fromtimestamp(int(new_df["time"].iloc[-1])).date()
    print(f"    data spans {dl_first} → {dl_last} ({len(new_df)} rows)")

    if dl_last < from_date:
        raise RuntimeError(
            f"Downloaded data ends at {dl_last}, expected up to {to_date}. "
            "Market may be closed or data not yet available."
        )


def _merge(csv_path: Path, new_path: Path):
    """Merge downloaded data into existing CSV.

    - Drops the last row of existing (partial/live day) then appends new rows.
    - Deduplicates by timestamp (new data wins on conflict).
    - Sorts by time.
    - Verifies no historical rows were lost.
    """
    existing = pd.read_csv(csv_path)
    new      = pd.read_csv(new_path)

    if list(existing.columns) != list(new.columns):
        raise ValueError(
            f"Column mismatch — existing: {list(existing.columns)}, "
            f"downloaded: {list(new.columns)}"
        )

    original_rows = len(existing)
    original_last_row = existing.iloc[-1]
    original_last = datetime.fromtimestamp(int(original_last_row["time"])).date()

    # Trim the last row of existing (it gets replaced by fresh download)
    trimmed = existing.iloc[:-1].copy()

    combined = pd.concat([trimmed, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"], keep="last")
    combined = combined.sort_values("time").reset_index(drop=True)

    final_rows = len(combined)
    final_last = datetime.fromtimestamp(int(combined["time"].iloc[-1])).date()

    # Safety check: we must not have fewer rows than original - 1
    min_expected = original_rows - 1
    if final_rows < min_expected:
        raise ValueError(
            f"Merge would reduce rows from {original_rows} to {final_rows} "
            f"(minimum expected: {min_expected}). Aborting to protect data."
        )

    added = final_rows - (original_rows - 1)
    combined.to_csv(csv_path, index=False)
    print(f"    merged: {original_rows} → {final_rows} rows "
          f"(+{added} new bars, last date: {final_last})")

    # Show before/after diff for the replaced last row
    new_last_row = combined[combined["time"] == original_last_row["time"]]
    if not new_last_row.empty:
        old = original_last_row
        new = new_last_row.iloc[0]
        diffs = [
            col for col in ["open", "high", "low", "close", "Volume"]
            if round(float(old[col]), 4) != round(float(new[col]), 4)
        ]
        if diffs:
            print(f"    last row ({original_last}) changed:")
            for col in diffs:
                print(f"      {col}: {old[col]} → {new[col]}")
        else:
            print(f"    last row ({original_last}): no change in OHLCV")


def _clear_cache(ticker: str):
    cache_file = CACHE_DIR / f"{ticker.upper()}.json.gz"
    if cache_file.exists():
        cache_file.unlink()
        print(f"    cache cleared: {cache_file.name}")


# ── Public API ────────────────────────────────────────────────────────────────

def refresh_ticker(ticker_input: str, today: date) -> bool:
    """Refresh one ticker. Returns True on success."""
    sep = "─" * 52
    try:
        csv_path, tv_symbol = find_ticker(ticker_input)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n{sep}\n✗ {ticker_input}: {e}")
        return False

    ticker = csv_path.stem.upper()
    ld = last_date(csv_path)

    if ld >= today:
        print(f"✓ {ticker}: already up to date (last: {ld})")
        return True

    print(f"\n{sep}")
    print(f"Refreshing {ticker}  [{tv_symbol}]")
    print(f"  existing : {len(pd.read_csv(csv_path))} rows, last date: {ld}")
    print(f"  fetching : {ld} → {today}")

    with tempfile.NamedTemporaryFile(
        suffix=f"_{ticker}_refresh.csv", delete=False
    ) as f:
        tmp = Path(f.name)

    try:
        _download(tv_symbol, ld, today, tmp)
        _merge(csv_path, tmp)
        _clear_cache(ticker)
        print(f"✓ {ticker}: done")
        return True
    except Exception as e:
        print(f"✗ {ticker}: {e}")
        return False
    finally:
        tmp.unlink(missing_ok=True)


def all_ticker_inputs(exchange: str | None = None) -> list[str]:
    """Return list of ticker_input strings for all CSVs in the given exchange dirs."""
    results = []
    dirs = []
    if exchange in (None, "nse"):
        dirs.append(("nse", "NSE:"))
    if exchange in (None, "sp"):
        dirs.append(("sp", ""))

    for dir_key, prefix in dirs:
        d = DATA_DIRS[dir_key]
        if not d.exists():
            continue
        for csv in sorted(d.glob("*.csv")):
            symbol = csv.stem.upper()
            results.append(f"{prefix}{symbol}")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refresh ticker CSV data using the tv CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python -m src.refresh RELIANCE
  uv run python -m src.refresh NSE:RELIANCE
  uv run python -m src.refresh MSFT
  uv run python -m src.refresh --all
  uv run python -m src.refresh --all --exchange nse
        """,
    )
    parser.add_argument("ticker", nargs="?", help="Ticker to refresh")
    parser.add_argument("--all", action="store_true", help="Refresh all tickers")
    parser.add_argument(
        "--exchange", choices=["nse", "sp"],
        help="With --all: limit to one exchange"
    )
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.print_help()
        sys.exit(1)

    # Allow "make refresh TICKER=nse" or "make refresh TICKER=sp" as shorthand
    if args.ticker and args.ticker.lower() in ("nse", "sp"):
        args.exchange = args.ticker.lower()
        args.ticker = None
        args.all = True

    print("Checking tv bridge...")
    try:
        check_bridge()
        print("  ✓ bridge running\n")
    except RuntimeError as e:
        print(f"  ✗ {e}")
        sys.exit(2)

    today = date.today()

    if args.all:
        inputs = all_ticker_inputs(args.exchange)
        label = f"({args.exchange.upper()})" if args.exchange else "(all)"
        print(f"Refreshing {len(inputs)} tickers {label}...\n")
        ok = fail = skipped = 0
        for t in inputs:
            success = refresh_ticker(t, today)
            if success:
                ld = last_date(find_ticker(t)[0])
                if ld >= today:
                    skipped += 1
                else:
                    ok += 1
            else:
                fail += 1
        print(f"\n{'─'*52}")
        print(f"Done: {ok} updated, {skipped} already current, {fail} failed")
    else:
        success = refresh_ticker(args.ticker, today)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
