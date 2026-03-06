"""Interactive backtesting chart viewer with Dash.

A date slider divides the chart into:
- Left of slider (history): price data + all detected patterns
- Right of slider (future): price data only, no patterns

This eliminates lookahead bias — at any point in time you only see
patterns that were detectable from historical data available at that date.
"""

import json
from pathlib import Path

import dash
from dash import dcc, html, Input, Output, State
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


def load_ticker_data(ticker, data_dir="data"):
    csv_path = Path(data_dir) / f"{ticker.lower()}.csv"
    if not csv_path.exists():
        csv_path = Path(data_dir) / f"{ticker.lower().replace('&', '_')}.csv"
    return load_csv(csv_path)


def load_results(results_path="results/patterns.json"):
    with open(results_path) as f:
        return json.load(f)


def get_ticker_result(all_results, ticker):
    for r in all_results:
        if r["ticker"].upper() == ticker.upper():
            return r
    return None


def build_figure(df, ticker_result, slider_date, show_layers):
    """Build the plotly figure with patterns filtered by slider_date."""
    dates = [str(d) for d in df.index]
    slider_str = str(slider_date)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.8, 0.2],
    )

    # Split price data into history (left) and future (right)
    hist_mask = df.index <= pd.to_datetime(slider_date).date()
    future_mask = ~hist_mask

    hist_df = df[hist_mask]
    future_df = df[future_mask]
    hist_dates = [str(d) for d in hist_df.index]
    future_dates = [str(d) for d in future_df.index]

    # History candlesticks (full color)
    if not hist_df.empty:
        fig.add_trace(
            go.Candlestick(
                x=hist_dates,
                open=hist_df["Open"], high=hist_df["High"],
                low=hist_df["Low"], close=hist_df["Close"],
                name="History",
                increasing_line_color="#2ecc71",
                decreasing_line_color="#e74c3c",
            ),
            row=1, col=1,
        )

    # Future candlesticks (dimmed)
    if not future_df.empty:
        fig.add_trace(
            go.Candlestick(
                x=future_dates,
                open=future_df["Open"], high=future_df["High"],
                low=future_df["Low"], close=future_df["Close"],
                name="Future",
                increasing_line_color="rgba(46,204,113,0.3)",
                decreasing_line_color="rgba(231,76,60,0.3)",
            ),
            row=1, col=1,
        )

    # Volume bars — history vs future
    if not hist_df.empty:
        h_colors = ["#2ecc71" if c >= o else "#e74c3c"
                     for c, o in zip(hist_df["Close"], hist_df["Open"])]
        fig.add_trace(
            go.Bar(x=hist_dates, y=hist_df["Volume"], name="Volume",
                   marker_color=h_colors, opacity=0.5, showlegend=False),
            row=2, col=1,
        )
    if not future_df.empty:
        f_colors = ["rgba(46,204,113,0.3)" if c >= o else "rgba(231,76,60,0.3)"
                     for c, o in zip(future_df["Close"], future_df["Open"])]
        fig.add_trace(
            go.Bar(x=future_dates, y=future_df["Volume"],
                   marker_color=f_colors, opacity=0.3, showlegend=False),
            row=2, col=1,
        )

    # Vertical line at slider position
    fig.add_vline(
        x=slider_str, line_dash="dash", line_color="#ffffff",
        line_width=2, opacity=0.7,
        annotation_text="NOW", annotation_position="top",
        row=1, col=1,
    )
    fig.add_vline(
        x=slider_str, line_dash="dash", line_color="#ffffff",
        line_width=1, opacity=0.4,
        row=2, col=1,
    )

    if not ticker_result:
        _apply_layout(fig, "Pattern Analysis")
        return fig

    # Price range for S/R filtering
    price_min = df["Low"].min()
    price_max = df["High"].max()
    price_margin = (price_max - price_min) * 0.1

    # --- Patterns: only show if end_date <= slider_date ---

    # Rolling S/R
    if "sr" in show_layers and ticker_result.get("rolling_sr"):
        added_support, added_resist = False, False
        for seg in ticker_result["rolling_sr"]:
            # Only show S/R zones that were established before the slider
            if seg["start_date"] > slider_str:
                continue
            # Clip end_date to slider
            end = min(seg["end_date"], slider_str)
            level = seg["level"]
            if level < price_min - price_margin or level > price_max + price_margin:
                continue

            is_support = seg["type"] == "support"
            color = "#3498db" if is_support else "#e67e22"
            opacity = min(0.8, 0.3 + seg["touches"] * 0.1)
            width = min(3, 1 + seg["touches"] * 0.5)

            # Mark as broken only if broken before slider date
            was_broken = seg.get("broken") and seg.get("broken_date", "") <= slider_str
            dash = "dash" if was_broken else "dot"

            show_legend = (is_support and not added_support) or \
                          (not is_support and not added_resist)
            if is_support:
                added_support = True
            else:
                added_resist = True

            fig.add_trace(
                go.Scatter(
                    x=[seg["start_date"], end],
                    y=[level, level],
                    mode="lines",
                    line=dict(color=color, width=width, dash=dash),
                    opacity=opacity,
                    name=f"{'Support' if is_support else 'Resistance'}",
                    legendgroup=seg["type"],
                    showlegend=show_legend,
                    text=[f"{seg['type']} {level:.2f} ({seg['touches']}x)"] * 2,
                    hovertemplate="%{text}<extra></extra>",
                ),
                row=1, col=1,
            )

    # Geometric patterns
    if "geometric" in show_layers and ticker_result.get("geometric_patterns"):
        patterns = [p for p in ticker_result["geometric_patterns"]
                    if p.get("end_date", "") <= slider_str]
        patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
        patterns = patterns[:50]

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
                    name=name,
                    text=[f"{r}: {p:.2f}" for r, p in zip(roles, px_prices)],
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                    legendgroup=pat["pattern"],
                    showlegend=show_legend,
                ),
                row=1, col=1,
            )

    # Candlestick patterns
    if "candlestick" in show_layers and ticker_result.get("candlestick_patterns"):
        hist_dates_set = set(hist_dates)
        bull_dates, bull_prices, bull_texts = [], [], []
        bear_dates, bear_prices, bear_texts = [], [], []

        for p in ticker_result["candlestick_patterns"]:
            if p["date"] > slider_str or p["date"] not in hist_dates_set:
                continue
            idx = hist_dates.index(p["date"])
            if p["direction"] == "bullish":
                bull_dates.append(p["date"])
                bull_prices.append(hist_df["Low"].iloc[idx] * 0.98)
                bull_texts.append(p["pattern"])
            else:
                bear_dates.append(p["date"])
                bear_prices.append(hist_df["High"].iloc[idx] * 1.02)
                bear_texts.append(p["pattern"])

        if bull_dates:
            fig.add_trace(
                go.Scatter(
                    x=bull_dates, y=bull_prices, mode="markers",
                    marker=dict(symbol="triangle-up", size=6, color="#2ecc71", opacity=0.6),
                    name="Bullish Candle", text=bull_texts,
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                    visible="legendonly",
                ),
                row=1, col=1,
            )
        if bear_dates:
            fig.add_trace(
                go.Scatter(
                    x=bear_dates, y=bear_prices, mode="markers",
                    marker=dict(symbol="triangle-down", size=6, color="#e74c3c", opacity=0.6),
                    name="Bearish Candle", text=bear_texts,
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                    visible="legendonly",
                ),
                row=1, col=1,
            )

    # Technical signals (golden/death cross, bollinger squeeze, volume climax)
    if "signals" in show_layers and ticker_result.get("signals"):
        gc_dates, gc_prices, gc_texts = [], [], []
        dc_dates, dc_prices, dc_texts = [], [], []
        bs_dates, bs_prices, bs_texts = [], [], []
        vc_dates, vc_prices, vc_texts = [], [], []

        for sig in ticker_result["signals"]:
            if sig.get("date", "") > slider_str:
                continue
            if sig["pattern"] == "golden_cross":
                gc_dates.append(sig["date"])
                gc_prices.append(sig.get("sma200", sig.get("close", 0)))
                gc_texts.append(f"Golden Cross: SMA50={sig.get('sma50', 0):.2f}")
            elif sig["pattern"] == "death_cross":
                dc_dates.append(sig["date"])
                dc_prices.append(sig.get("sma200", sig.get("close", 0)))
                dc_texts.append(f"Death Cross: SMA50={sig.get('sma50', 0):.2f}")
            elif sig["pattern"] == "bollinger_squeeze":
                bs_dates.append(sig["date"])
                bs_prices.append(sig.get("upper", sig.get("close", 0)))
                bs_texts.append(f"BB Squeeze: BW={sig.get('bandwidth', 0):.2f}%")
            elif sig["pattern"] == "volume_climax":
                vc_dates.append(sig["date"])
                vc_prices.append(sig.get("close", 0))
                vc_texts.append(f"Vol Climax: {sig.get('volume_ratio', 0):.1f}x avg")

        if gc_dates:
            fig.add_trace(
                go.Scatter(
                    x=gc_dates, y=gc_prices, mode="markers",
                    marker=dict(symbol="star", size=14, color="#FFD700",
                               line=dict(width=2, color="white")),
                    name="Golden Cross", text=gc_texts,
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
                    name="Death Cross", text=dc_texts,
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                ),
                row=1, col=1,
            )
        if bs_dates:
            fig.add_trace(
                go.Scatter(
                    x=bs_dates, y=bs_prices, mode="markers",
                    marker=dict(symbol="hourglass", size=10, color="#9b59b6"),
                    name="BB Squeeze", text=bs_texts,
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                    visible="legendonly",
                ),
                row=1, col=1,
            )
        if vc_dates:
            fig.add_trace(
                go.Scatter(
                    x=vc_dates, y=vc_prices, mode="markers",
                    marker=dict(symbol="diamond-tall", size=10, color="#e67e22"),
                    name="Volume Climax", text=vc_texts,
                    hovertemplate="%{text}<br>%{x}<extra></extra>",
                    visible="legendonly",
                ),
                row=1, col=1,
            )

    # Divergences
    if "divergences" in show_layers and ticker_result.get("divergences"):
        added_div = False
        for div in ticker_result["divergences"]:
            if div.get("end_date", div.get("date", "")) > slider_str:
                continue
            color = DIRECTION_COLORS.get(div["direction"], "#95a5a6")
            fig.add_trace(
                go.Scatter(
                    x=[div["start_date"], div["end_date"]],
                    y=[div["price_1"], div["price_2"]],
                    mode="lines+markers",
                    line=dict(color=color, width=2, dash="dot"),
                    marker=dict(size=8, color=color),
                    name="Divergence",
                    legendgroup="divergences",
                    showlegend=not added_div,
                    visible="legendonly",
                ),
                row=1, col=1,
            )
            added_div = True

    # Gaps
    if "gaps" in show_layers and ticker_result.get("gaps"):
        for gap in ticker_result["gaps"]:
            if gap["date"] > slider_str:
                continue
            color = "#2ecc71" if gap["pattern"] == "gap_up" else "#e74c3c"
            fig.add_shape(
                type="rect",
                x0=gap["date"], x1=gap["date"],
                y0=gap["gap_low"], y1=gap["gap_high"],
                fillcolor=color, opacity=0.15,
                line=dict(width=0),
                row=1, col=1,
            )

    _apply_layout(fig, ticker_result.get("ticker", ""))
    return fig


def _apply_layout(fig, ticker):
    fig.update_layout(
        title=f"{ticker} — Backtesting Viewer",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=700,
        margin=dict(l=50, r=50, t=80, b=50),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=10),
        ),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)


def create_app(ticker="NVDA", data_dir="data", results_path="results/patterns.json"):
    """Create and return the Dash app."""

    df = load_ticker_data(ticker, data_dir)
    all_results = load_results(results_path)
    ticker_result = get_ticker_result(all_results, ticker)

    # Get list of available tickers from results
    available_tickers = [r["ticker"] for r in all_results]

    # Date range
    all_dates = [str(d) for d in df.index]
    min_date = all_dates[0]
    max_date = all_dates[-1]

    # Create marks for the slider (every ~10% of range)
    n = len(all_dates)
    mark_indices = list(range(0, n, max(1, n // 10))) + [n - 1]
    marks = {i: {"label": all_dates[i][:10], "style": {"fontSize": "10px"}}
             for i in mark_indices}

    app = dash.Dash(__name__)

    app.layout = html.Div(style={"backgroundColor": "#1a1a2e", "minHeight": "100vh", "padding": "20px"}, children=[
        html.Div(style={"maxWidth": "1400px", "margin": "0 auto"}, children=[
            # Header
            html.H1("Pattern Backtesting Viewer",
                     style={"color": "#ecf0f1", "fontFamily": "monospace", "marginBottom": "5px"}),
            html.P("Slide the date to see what patterns were visible historically vs what price did next",
                    style={"color": "#7f8c8d", "fontFamily": "monospace", "marginBottom": "20px"}),

            # Controls row
            html.Div(style={"display": "flex", "gap": "20px", "marginBottom": "15px",
                           "alignItems": "center", "flexWrap": "wrap"}, children=[
                # Ticker dropdown
                html.Div(children=[
                    html.Label("Ticker", style={"color": "#bdc3c7", "fontSize": "12px"}),
                    dcc.Dropdown(
                        id="ticker-dropdown",
                        options=[{"label": t, "value": t} for t in available_tickers],
                        value=ticker.upper(),
                        style={"width": "150px"},
                        clearable=False,
                    ),
                ]),

                # Layer toggles
                html.Div(children=[
                    html.Label("Layers", style={"color": "#bdc3c7", "fontSize": "12px"}),
                    dcc.Checklist(
                        id="layer-checklist",
                        options=[
                            {"label": " S/R Zones", "value": "sr"},
                            {"label": " Geometric", "value": "geometric"},
                            {"label": " Candlestick", "value": "candlestick"},
                            {"label": " Signals", "value": "signals"},
                            {"label": " Divergences", "value": "divergences"},
                            {"label": " Gaps", "value": "gaps"},
                        ],
                        value=["sr", "geometric", "signals"],
                        inline=True,
                        style={"color": "#ecf0f1", "fontSize": "13px"},
                        inputStyle={"marginRight": "4px", "marginLeft": "12px"},
                    ),
                ]),
            ]),

            # Date slider
            html.Div(style={"marginBottom": "10px", "padding": "0 10px"}, children=[
                html.Div(style={"display": "flex", "justifyContent": "space-between",
                               "alignItems": "center", "marginBottom": "5px"}, children=[
                    html.Label("History ←", style={"color": "#2ecc71", "fontSize": "12px",
                                                    "fontFamily": "monospace"}),
                    html.Span(id="slider-date-label",
                              style={"color": "#ecf0f1", "fontSize": "14px",
                                    "fontWeight": "bold", "fontFamily": "monospace"}),
                    html.Label("→ Future", style={"color": "#e74c3c", "fontSize": "12px",
                                                   "fontFamily": "monospace"}),
                ]),
                dcc.Slider(
                    id="date-slider",
                    min=0, max=n - 1,
                    value=int(n * 0.75),  # start at 75% through the data
                    marks=marks,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="mouseup",
                ),
            ]),

            # Chart
            dcc.Loading(
                dcc.Graph(id="main-chart", style={"height": "700px"}),
                type="circle",
                color="#3498db",
            ),

            # Pattern summary below chart
            html.Div(id="pattern-summary",
                     style={"color": "#bdc3c7", "fontFamily": "monospace",
                            "fontSize": "13px", "marginTop": "10px",
                            "padding": "10px", "backgroundColor": "#16213e",
                            "borderRadius": "5px"}),
        ]),

        # Store the data index mapping
        dcc.Store(id="date-index-store", data=all_dates),
    ])

    # State to cache loaded ticker data
    app._cache = {
        "ticker": ticker.upper(),
        "df": df,
        "ticker_result": ticker_result,
        "all_results": all_results,
        "data_dir": data_dir,
    }

    @app.callback(
        Output("main-chart", "figure"),
        Output("slider-date-label", "children"),
        Output("pattern-summary", "children"),
        Output("date-slider", "max"),
        Output("date-slider", "marks"),
        Output("date-slider", "value"),
        Output("date-index-store", "data"),
        Input("date-slider", "value"),
        Input("ticker-dropdown", "value"),
        Input("layer-checklist", "value"),
        State("date-index-store", "data"),
    )
    def update_chart(slider_idx, selected_ticker, show_layers, stored_dates):
        cache = app._cache
        ctx = dash.callback_context
        triggered = ctx.triggered[0]["prop_id"] if ctx.triggered else ""

        # Reload data if ticker changed
        if selected_ticker != cache["ticker"]:
            new_df = load_ticker_data(selected_ticker, cache["data_dir"])
            new_result = get_ticker_result(cache["all_results"], selected_ticker)
            cache["ticker"] = selected_ticker
            cache["df"] = new_df
            cache["ticker_result"] = new_result
            stored_dates = [str(d) for d in new_df.index]
            # Reset slider to 75% for new ticker
            slider_idx = int(len(stored_dates) * 0.75)

        cur_df = cache["df"]
        cur_result = cache["ticker_result"]
        stored_dates = [str(d) for d in cur_df.index]

        n = len(stored_dates)
        slider_idx = max(0, min(slider_idx, n - 1))
        slider_date = stored_dates[slider_idx]

        # Build slider marks
        mark_indices = list(range(0, n, max(1, n // 10))) + [n - 1]
        new_marks = {i: {"label": stored_dates[i][:10],
                         "style": {"fontSize": "10px", "color": "#7f8c8d"}}
                     for i in mark_indices}

        fig = build_figure(cur_df, cur_result, slider_date, show_layers)

        # Pattern summary
        summary_parts = []
        if cur_result:
            if "geometric" in show_layers:
                geo_before = [p for p in cur_result.get("geometric_patterns", [])
                              if p.get("end_date", "") <= slider_date]
                summary_parts.append(f"Geometric: {len(geo_before)}")
            if "sr" in show_layers:
                sr_active = [s for s in cur_result.get("rolling_sr", [])
                             if s["start_date"] <= slider_date
                             and s["end_date"] >= slider_date
                             and not (s.get("broken") and s.get("broken_date", "") <= slider_date)]
                summary_parts.append(f"Active S/R: {len(sr_active)}")
            if "signals" in show_layers:
                sigs_before = [s for s in cur_result.get("signals", [])
                               if s.get("date", "") <= slider_date]
                summary_parts.append(f"Signals: {len(sigs_before)}")
            if "candlestick" in show_layers:
                cdl_before = [p for p in cur_result.get("candlestick_patterns", [])
                              if p.get("date", "") <= slider_date]
                summary_parts.append(f"Candlestick: {len(cdl_before)}")
            if "divergences" in show_layers:
                div_before = [d for d in cur_result.get("divergences", [])
                              if d.get("end_date", d.get("date", "")) <= slider_date]
                summary_parts.append(f"Divergences: {len(div_before)}")

        summary_text = f"Date: {slider_date}  |  " + "  |  ".join(summary_parts) if summary_parts else f"Date: {slider_date}"

        return fig, slider_date[:10], summary_text, n - 1, new_marks, slider_idx, stored_dates

    return app


if __name__ == "__main__":
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "data"
    results_path = sys.argv[3] if len(sys.argv) > 3 else "results/patterns.json"

    app = create_app(ticker=ticker, data_dir=data_dir, results_path=results_path)
    print(f"\nStarting backtesting viewer for {ticker}")
    print(f"Open http://127.0.0.1:8050 in your browser\n")
    app.run(debug=True, port=8050)
