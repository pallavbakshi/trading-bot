import json
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

from src.loader import load_csv


PATTERN_COLORS = {
    "double_top": "#e74c3c",
    "double_bottom": "#2ecc71",
    "head_and_shoulders": "#e74c3c",
    "inverse_head_and_shoulders": "#2ecc71",
    "triple_top": "#c0392b",
    "triple_bottom": "#27ae60",
    "ascending_triangle": "#2ecc71",
    "descending_triangle": "#e74c3c",
    "symmetrical_triangle": "#f39c12",
    "rising_wedge": "#e74c3c",
    "falling_wedge": "#2ecc71",
    "ascending_channel": "#2ecc71",
    "descending_channel": "#e74c3c",
    "horizontal_channel": "#f39c12",
    "broadening_formation": "#9b59b6",
    "bull_flag": "#2ecc71",
    "bear_flag": "#e74c3c",
    "bull_pennant": "#2ecc71",
    "bear_pennant": "#e74c3c",
}

DIRECTION_COLORS = {
    "bullish": "#2ecc71",
    "bearish": "#e74c3c",
    "neutral": "#f39c12",
}


def _in_date_range(date_str, date_from_str, date_to_str):
    """Check if a date string falls within the displayed range."""
    if date_from_str and date_str < date_from_str:
        return False
    if date_to_str and date_str > date_to_str:
        return False
    return True


def _pattern_in_range(pat, date_from_str, date_to_str):
    """Check if a geometric pattern overlaps with the displayed date range."""
    start = pat.get("start_date") or pat.get("pivots", [{}])[0].get("date", "")
    end = pat.get("end_date") or pat.get("pivots", [{}])[-1].get("date", "")
    # Pattern overlaps if it doesn't end before range starts or start after range ends
    if date_from_str and end < date_from_str:
        return False
    if date_to_str and start > date_to_str:
        return False
    return True


def plot_ticker(ticker: str, data_dir: str = "data", results_path: str = "results/patterns.json",
                date_from: str = None, date_to: str = None,
                show_geometric: bool = True, show_candlestick: bool = True,
                show_sr: bool = True, show_gaps: bool = False,
                show_divergences: bool = False, show_signals: bool = True,
                min_confidence: float = 0.0,
                max_patterns: int = 50,
                output_html: str = None) -> go.Figure:
    """Plot interactive candlestick chart with pattern overlays."""
    # Load price data
    csv_path = Path(data_dir) / f"{ticker.lower()}.csv"
    if not csv_path.exists():
        csv_path = Path(data_dir) / f"{ticker.lower().replace('&', '_')}.csv"
    df = load_csv(csv_path)

    # Apply date filters
    if date_from:
        df = df[df.index >= pd.to_datetime(date_from).date()]
    if date_to:
        df = df[df.index <= pd.to_datetime(date_to).date()]

    if df.empty:
        print(f"No data for {ticker} in the given date range")
        return None

    dates = [str(d) for d in df.index]
    date_from_str = dates[0]
    date_to_str = dates[-1]

    # Load pattern results
    with open(results_path) as f:
        all_results = json.load(f)

    ticker_result = None
    for r in all_results:
        if r["ticker"] == ticker.upper() or r["ticker"] == ticker.upper().replace("&", "_"):
            ticker_result = r
            break

    # Create figure with volume subplot
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.8, 0.2],
    )

    # Candlestick chart
    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="Price",
            increasing_line_color="#2ecc71",
            decreasing_line_color="#e74c3c",
        ),
        row=1, col=1,
    )

    # Volume bars
    colors = ["#2ecc71" if c >= o else "#e74c3c" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(x=dates, y=df["Volume"], name="Volume", marker_color=colors, opacity=0.5),
        row=2, col=1,
    )

    # Price range for filtering S/R levels
    price_min = df["Low"].min()
    price_max = df["High"].max()
    price_margin = (price_max - price_min) * 0.1

    if ticker_result:
        # Rolling Support/Resistance — time-bounded segments
        if show_sr and ticker_result.get("rolling_sr"):
            sr_segments = [s for s in ticker_result["rolling_sr"]
                          if _pattern_in_range(
                              {"start_date": s["start_date"], "end_date": s["end_date"]},
                              date_from_str, date_to_str)
                          and price_min - price_margin <= s["level"] <= price_max + price_margin]

            # Deduplicate very close levels
            added_support, added_resist = False, False
            for seg in sr_segments:
                level = seg["level"]
                is_support = seg["type"] == "support"
                color = "#3498db" if is_support else "#e67e22"
                opacity = min(0.8, 0.3 + seg["touches"] * 0.1)
                width = min(3, 1 + seg["touches"] * 0.5)

                show_legend = (is_support and not added_support) or \
                              (not is_support and not added_resist)
                if is_support:
                    added_support = True
                else:
                    added_resist = True

                broken_marker = " (broken)" if seg.get("broken") else ""
                dash = "dot" if not seg.get("broken") else "dash"

                fig.add_trace(
                    go.Scatter(
                        x=[seg["start_date"], seg["end_date"]],
                        y=[level, level],
                        mode="lines",
                        line=dict(color=color, width=width, dash=dash),
                        opacity=opacity,
                        name=f"{'Support' if is_support else 'Resistance'}",
                        legendgroup=seg["type"],
                        showlegend=show_legend,
                        text=[f"{seg['type']} {level:.2f} ({seg['touches']}x){broken_marker}"] * 2,
                        hovertemplate="%{text}<extra></extra>",
                    ),
                    row=1, col=1,
                )

        # Fallback to static S/R if no rolling data
        elif show_sr and ticker_result.get("support_resistance"):
            for zone in ticker_result["support_resistance"]:
                level = zone["level"]
                if level < price_min - price_margin or level > price_max + price_margin:
                    continue
                color = "#3498db" if zone["type"] == "support" else "#e67e22"
                fig.add_hline(
                    y=level, line_dash="dot", line_color=color, opacity=0.5,
                    annotation_text=f"{zone['type']} ({zone['touches']}x) {level:.2f}",
                    annotation_position="right",
                    row=1, col=1,
                )

        # Geometric patterns — filter to displayed date range
        if show_geometric and ticker_result.get("geometric_patterns"):
            patterns = [p for p in ticker_result["geometric_patterns"]
                       if p.get("confidence", 0) >= min_confidence
                       and _pattern_in_range(p, date_from_str, date_to_str)]
            patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
            patterns = patterns[:max_patterns]

            seen_names = set()
            for pat in patterns:
                color = PATTERN_COLORS.get(pat["pattern"], "#95a5a6")
                pivots = pat.get("pivots", [])
                if not pivots:
                    continue

                px_dates = [p["date"] for p in pivots]
                px_prices = [p["price"] for p in pivots]
                roles = [p.get("role", "") for p in pivots]

                name = pat["pattern"].replace("_", " ").title()
                show_legend = name not in seen_names
                seen_names.add(name)

                fig.add_trace(
                    go.Scatter(
                        x=px_dates, y=px_prices,
                        mode="lines+markers",
                        line=dict(color=color, width=2, dash="dash"),
                        marker=dict(size=8, color=color, symbol="diamond"),
                        name=f"{name} ({pat.get('confidence', '?')})",
                        text=[f"{r}: {p:.2f}" for r, p in zip(roles, px_prices)],
                        hovertemplate="%{text}<br>%{x}<extra></extra>",
                        legendgroup=pat["pattern"],
                        showlegend=show_legend,
                    ),
                    row=1, col=1,
                )

                if "neckline" in pat:
                    neckline = pat["neckline"]
                    if price_min - price_margin <= neckline <= price_max + price_margin:
                        fig.add_hline(
                            y=neckline, line_dash="dash", line_color=color,
                            opacity=0.4, row=1, col=1,
                        )

        # Candlestick pattern markers — only for dates in visible range
        if show_candlestick and ticker_result.get("candlestick_patterns"):
            dates_set = set(dates)

            bull_dates, bull_prices, bull_texts = [], [], []
            bear_dates, bear_prices, bear_texts = [], [], []

            for p in ticker_result["candlestick_patterns"]:
                if p["date"] not in dates_set:
                    continue
                idx = dates.index(p["date"])
                if p["direction"] == "bullish":
                    bull_dates.append(p["date"])
                    bull_prices.append(df["Low"].iloc[idx] * 0.98)
                    bull_texts.append(p["pattern"])
                else:
                    bear_dates.append(p["date"])
                    bear_prices.append(df["High"].iloc[idx] * 1.02)
                    bear_texts.append(p["pattern"])

            if bull_dates:
                fig.add_trace(
                    go.Scatter(
                        x=bull_dates, y=bull_prices,
                        mode="markers",
                        marker=dict(symbol="triangle-up", size=6, color="#2ecc71", opacity=0.6),
                        name="Bullish Candle",
                        text=bull_texts,
                        hovertemplate="%{text}<br>%{x}<extra></extra>",
                        visible="legendonly",
                    ),
                    row=1, col=1,
                )

            if bear_dates:
                fig.add_trace(
                    go.Scatter(
                        x=bear_dates, y=bear_prices,
                        mode="markers",
                        marker=dict(symbol="triangle-down", size=6, color="#e74c3c", opacity=0.6),
                        name="Bearish Candle",
                        text=bear_texts,
                        hovertemplate="%{text}<br>%{x}<extra></extra>",
                        visible="legendonly",
                    ),
                    row=1, col=1,
                )

        # Gap markers — filter to date range
        if show_gaps and ticker_result.get("gaps"):
            for gap in ticker_result["gaps"]:
                if not _in_date_range(gap["date"], date_from_str, date_to_str):
                    continue
                color = "#2ecc71" if gap["pattern"] == "gap_up" else "#e74c3c"
                fig.add_shape(
                    type="rect",
                    x0=gap["date"], x1=gap["date"],
                    y0=gap["gap_low"], y1=gap["gap_high"],
                    fillcolor=color, opacity=0.2,
                    line=dict(width=0),
                    row=1, col=1,
                )

        # Technical signals — batch golden/death cross into single traces
        if show_signals and ticker_result.get("signals"):
            gc_dates, gc_prices, gc_texts = [], [], []
            dc_dates, dc_prices, dc_texts = [], [], []

            for sig in ticker_result["signals"]:
                if not _in_date_range(sig.get("date", ""), date_from_str, date_to_str):
                    continue
                if sig["pattern"] == "golden_cross":
                    gc_dates.append(sig["date"])
                    gc_prices.append(sig.get("sma200", sig.get("close", 0)))
                    gc_texts.append(f"Golden Cross: SMA50={sig.get('sma50', 0):.2f}")
                elif sig["pattern"] == "death_cross":
                    dc_dates.append(sig["date"])
                    dc_prices.append(sig.get("sma200", sig.get("close", 0)))
                    dc_texts.append(f"Death Cross: SMA50={sig.get('sma50', 0):.2f}")

            if gc_dates:
                fig.add_trace(
                    go.Scatter(
                        x=gc_dates, y=gc_prices, mode="markers",
                        marker=dict(symbol="star", size=14, color="#FFD700",
                                   line=dict(width=2, color="white")),
                        name="Golden Cross",
                        text=gc_texts,
                        hovertemplate="%{text}<br>%{x}<extra></extra>",
                    ),
                    row=1, col=1,
                )

            if dc_dates:
                fig.add_trace(
                    go.Scatter(
                        x=dc_dates, y=dc_prices, mode="markers",
                        marker=dict(symbol="x", size=14, color="#4a0080",
                                   line=dict(width=2, color="white")),
                        name="Death Cross",
                        text=dc_texts,
                        hovertemplate="%{text}<br>%{x}<extra></extra>",
                    ),
                    row=1, col=1,
                )

        # Divergence markers — filter to date range
        if show_divergences and ticker_result.get("divergences"):
            for div in ticker_result["divergences"]:
                if div.get("confidence", 0) < min_confidence:
                    continue
                if not _in_date_range(div.get("start_date", ""), date_from_str, date_to_str):
                    continue
                color = DIRECTION_COLORS.get(div["direction"], "#95a5a6")
                fig.add_trace(
                    go.Scatter(
                        x=[div["start_date"], div["end_date"]],
                        y=[div["price_1"], div["price_2"]],
                        mode="lines+markers",
                        line=dict(color=color, width=2, dash="dot"),
                        marker=dict(size=8, color=color),
                        name=div["pattern"].replace("_", " ").title(),
                        legendgroup="divergences",
                        showlegend=False,
                        visible="legendonly",
                    ),
                    row=1, col=1,
                )

    # Layout
    fig.update_layout(
        title=f"{ticker.upper()} — Pattern Analysis",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=800,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10),
        ),
        hovermode="x unified",
    )

    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)

    if output_html:
        output_path = Path(output_html)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(str(output_path), include_plotlyjs=True)
        print(f"Chart saved to: {output_path}")

    return fig


def plot_pattern_detail(ticker: str, pattern_index: int = 0,
                        data_dir: str = "data",
                        results_path: str = "results/patterns.json",
                        context_bars: int = 50,
                        output_html: str = None) -> go.Figure:
    """Plot a zoomed-in view of a specific geometric pattern."""
    with open(results_path) as f:
        all_results = json.load(f)

    ticker_result = None
    for r in all_results:
        if r["ticker"] == ticker.upper():
            ticker_result = r
            break

    if not ticker_result or not ticker_result.get("geometric_patterns"):
        print(f"No geometric patterns found for {ticker}")
        return None

    patterns = sorted(ticker_result["geometric_patterns"],
                     key=lambda p: p.get("confidence", 0), reverse=True)

    if pattern_index >= len(patterns):
        print(f"Pattern index {pattern_index} out of range (max {len(patterns) - 1})")
        return None

    pat = patterns[pattern_index]

    start = pat.get("start_date", pat.get("pivots", [{}])[0].get("date"))
    end = pat.get("end_date", pat.get("pivots", [{}])[-1].get("date"))

    return plot_ticker(
        ticker, data_dir=data_dir, results_path=results_path,
        date_from=str(pd.to_datetime(start) - pd.Timedelta(days=context_bars * 2)),
        date_to=str(pd.to_datetime(end) + pd.Timedelta(days=context_bars * 2)),
        show_geometric=True, show_candlestick=False,
        show_sr=False, show_signals=True,
        min_confidence=pat.get("confidence", 0) - 0.01,
        max_patterns=5,
        output_html=output_html,
    )


if __name__ == "__main__":
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "data"
    results_path = sys.argv[3] if len(sys.argv) > 3 else "results/patterns.json"

    plot_ticker(
        ticker, data_dir=data_dir, results_path=results_path,
        min_confidence=0.7, max_patterns=20,
        output_html=f"results/charts/{ticker.lower()}_full.html",
    )

    plot_pattern_detail(
        ticker, pattern_index=0,
        data_dir=data_dir, results_path=results_path,
        output_html=f"results/charts/{ticker.lower()}_pattern_0.html",
    )
