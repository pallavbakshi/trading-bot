import json
import time
from pathlib import Path

from src.loader import load_all
from src.pivots import find_pivots
from src.candlestick import scan_candlestick_patterns
from src.patterns.support_resistance import find_sr_zones, find_rolling_sr, find_density_sr
from src.patterns.double_top import detect_double_tops, detect_double_bottoms
from src.patterns.head_shoulders import detect_head_and_shoulders, detect_inverse_head_and_shoulders
from src.patterns.triangle import detect_triangles
from src.patterns.wedge import detect_wedges
from src.patterns.channel import detect_channels
from src.patterns.flag import detect_flags, detect_pennants
from src.patterns.triple_top import detect_triple_tops, detect_triple_bottoms
from src.patterns.broadening import detect_broadening
from src.patterns.gap import detect_gaps
from src.patterns.divergence import detect_rsi_divergence, detect_macd_divergence
from src.patterns.signals import detect_ma_crossovers, detect_bollinger_squeeze, detect_volume_climax


PIVOT_ORDERS = [5, 10, 20]


def scan_ticker(ticker: str, df, pivot_orders=None):
    """Run all pattern detectors on a single ticker."""
    if pivot_orders is None:
        pivot_orders = PIVOT_ORDERS

    result = {
        "ticker": ticker,
        "bars": len(df),
        "date_range": [str(df.index[0]), str(df.index[-1])],
        "candlestick_patterns": [],
        "geometric_patterns": [],
        "gaps": [],
        "island_reversals": [],
        "divergences": [],
        "signals": [],
        "support_resistance": [],
    }

    # Layer 1: Candlestick patterns (TA-Lib)
    result["candlestick_patterns"] = scan_candlestick_patterns(df)

    # Layer 2: Geometric patterns at multiple pivot scales
    for order in pivot_orders:
        pivots = find_pivots(df, order=order)
        if pivots.empty:
            continue

        tag = {"pivot_order": order}

        for pattern in detect_double_tops(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_double_bottoms(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_head_and_shoulders(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_inverse_head_and_shoulders(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_triangles(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_wedges(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_channels(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_triple_tops(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_triple_bottoms(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_flags(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_pennants(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

        for pattern in detect_broadening(df, pivots):
            result["geometric_patterns"].append({**pattern, **tag})

    # Layer 3: Gap analysis
    gaps, islands = detect_gaps(df)
    result["gaps"] = gaps
    result["island_reversals"] = islands

    # Layer 4: Divergences
    result["divergences"] = detect_rsi_divergence(df) + detect_macd_divergence(df)

    # Layer 5: Technical signals
    result["signals"] = (
        detect_ma_crossovers(df) +
        detect_bollinger_squeeze(df) +
        detect_volume_climax(df)
    )

    # Support/Resistance zones (static + rolling)
    result["support_resistance"] = find_sr_zones(df, order=10)
    result["rolling_sr"] = find_rolling_sr(df, order=5, lookback=250, step=5)
    result["density_sr"] = find_density_sr(df, lookback=250, step=5)

    return result


def scan_all(data_dir: str = "data", output_dir: str = "results"):
    """Scan all tickers and write results."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    print("Loading data...")
    tickers = load_all(data_dir)
    print(f"Loaded {len(tickers)} tickers\n")

    all_results = []
    totals = {
        "candlestick": 0, "geometric": 0, "gaps": 0,
        "islands": 0, "divergences": 0, "signals": 0,
    }

    for i, (ticker, df) in enumerate(tickers.items(), 1):
        t0 = time.time()
        result = scan_ticker(ticker, df)
        elapsed = time.time() - t0

        n_candle = len(result["candlestick_patterns"])
        n_geo = len(result["geometric_patterns"])
        n_gaps = len(result["gaps"])
        n_islands = len(result["island_reversals"])
        n_div = len(result["divergences"])
        n_sig = len(result["signals"])
        n_sr = len(result["support_resistance"])

        totals["candlestick"] += n_candle
        totals["geometric"] += n_geo
        totals["gaps"] += n_gaps
        totals["islands"] += n_islands
        totals["divergences"] += n_div
        totals["signals"] += n_sig

        print(f"[{i:3d}/{len(tickers)}] {ticker:6s}  "
              f"cdl={n_candle:4d}  geo={n_geo:4d}  gap={n_gaps:3d}  "
              f"div={n_div:3d}  sig={n_sig:3d}  s/r={n_sr:2d}  "
              f"({elapsed:.2f}s)")

        all_results.append(result)

    # Write combined results
    results_file = output_path / "patterns.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"Scan complete!")
    print(f"  Tickers scanned:      {len(tickers)}")
    print(f"  Candlestick patterns: {totals['candlestick']}")
    print(f"  Geometric patterns:   {totals['geometric']}")
    print(f"  Gaps:                 {totals['gaps']}")
    print(f"  Island reversals:     {totals['islands']}")
    print(f"  Divergences:          {totals['divergences']}")
    print(f"  Technical signals:    {totals['signals']}")
    print(f"  Results written to:   {results_file}")


if __name__ == "__main__":
    scan_all()
