"""Automated backtesting script.

Runs the full analysis workflow for a ticker across multiple dates:
1. Navigate chart to date with lookback window
2. Snapshot plain chart
3. Enable Volume Profile + RSI, snapshot again
4. Send plain chart to LLM for analysis
5. Follow-up with overlay chart + CSV data
6. Extract structured trade parameters (JSON)
7. Check actual price data for trade result

Requires: server running (python -m src.server) + browser open to chart.

Usage:
  tb backtest NVDA -s 2025-01-01 -e 2025-01-07
  tb backtest NVDA -s 2024-06-01 -e 2024-12-31 --every 20
  tb backtest NVDA --every 5 --lookback 1Y --model anthropic/claude-sonnet-4.6
"""

import argparse
import json
import shutil
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from src.loader import load_csv
from src.openrouter import Chat, MODELS

API = "http://127.0.0.1:8000"

ANALYSIS_PROMPT = (
    "Looking at this chart ({lookback} lookback, trading days mode, NOW is at day 0 "
    "which is the rightmost visible candle before lookforward), identify: "
    "1) Major resistance levels "
    "2) Major support levels "
    "3) Suggest a position (long or short) with specific entry price, stop loss, "
    "take profit, and maximum number of trading days for the play "
    "(if stop loss or take profit not hit, we exit at day N regardless)."
)

FOLLOWUP_PROMPT = (
    "Here is the same chart but now with Volume Profile and RSI overlays turned on, "
    "plus the CSV bar data. Does this additional information change your analysis?"
)

JSON_PROMPT = (
    'Based on your analysis, give me a structured JSON output with exactly these fields: '
    '{{"ticker": "{ticker}", "date": "{date}", "direction": "long|short", '
    '"entry": <price>, "stop_loss": <price>, "take_profit": <price>, '
    '"max_days": <int>, "risk_reward": <float>, "confidence": "low|medium|high", '
    '"key_levels": {{"resistance": [<prices>], "support": [<prices>]}}, '
    '"rationale": "<1 sentence>"}} — Return ONLY the JSON, no markdown fences, no explanation.'
)

LOOKBACK_DAYS = {"3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504}


# ── Server helpers ──────────────────────────────────────────────────────

def _send_cmd(cmd: dict):
    data = json.dumps(cmd).encode()
    req = urllib.request.Request(
        f"{API}/api/command", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _get_state() -> dict:
    with urllib.request.urlopen(f"{API}/api/state") as resp:
        return json.loads(resp.read()) or {}


def _wait_for_state(key: str, expected, timeout: float = 5.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = _get_state()
        if state.get(key) == expected:
            return True
        time.sleep(0.2)
    return False


def _wait_for_date(target: str, timeout: float = 8.0) -> bool:
    """Wait until chart state date is near the target date (within 3 calendar days)."""
    target_dt = datetime.strptime(target, "%Y-%m-%d")
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = _get_state()
        state_date = state.get("date", "")
        if state_date:
            try:
                state_dt = datetime.strptime(state_date[:10], "%Y-%m-%d")
                if abs((state_dt - target_dt).days) <= 3:
                    return True
            except ValueError:
                pass
        time.sleep(0.3)
    return False


def _snapshot(save_dir: str, prefix: str, timeout: float = 15.0) -> dict | None:
    _send_cmd({"action": "snapshot", "save_dir": save_dir, "prefix": prefix})
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{API}/api/snapshot/result") as resp:
                result = json.loads(resp.read())
                if result and result.get("ok"):
                    return result
                elif result and not result.get("ok"):
                    return None
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _toggle(key: str, value: str):
    _send_cmd({"action": "toggle", "key": key, "value": value})


# ── Trade evaluation ────────────────────────────────────────────────────

def _check_trade(df, entry_date: str, entry: float, stop_loss: float,
                 take_profit: float, max_days: int, direction: str) -> dict:
    dates = [str(d) for d in df.index]
    try:
        idx = dates.index(entry_date)
    except ValueError:
        for i, d in enumerate(dates):
            if d >= entry_date:
                idx = i
                break
        else:
            return {"result": {"outcome": "no_data", "error": f"Date {entry_date} not found"}, "daily_data": []}

    is_short = direction.lower() == "short"
    days = []
    result = {}

    for i in range(1, min(max_days + 1, len(df) - idx)):
        row = df.iloc[idx + i]
        d = dates[idx + i]
        day_data = {
            "day": i, "date": d,
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
        }
        days.append(day_data)

        if is_short:
            sl_hit = row["High"] >= stop_loss
            tp_hit = row["Low"] <= take_profit
        else:
            sl_hit = row["Low"] <= stop_loss
            tp_hit = row["High"] >= take_profit

        if sl_hit and not tp_hit:
            pnl = (entry - stop_loss) if is_short else (stop_loss - entry)
            result = {"outcome": "stop_loss", "exit_day": i, "exit_date": d,
                      "exit_price": stop_loss, "pnl": round(pnl, 2)}
            break
        elif tp_hit and not sl_hit:
            pnl = (entry - take_profit) if is_short else (take_profit - entry)
            result = {"outcome": "take_profit", "exit_day": i, "exit_date": d,
                      "exit_price": take_profit, "pnl": round(pnl, 2)}
            break
        elif sl_hit and tp_hit:
            # Both hit same candle — ambiguous without intraday data
            result = {"outcome": "ambiguous", "exit_day": i, "exit_date": d,
                      "note": "Both SL and TP breached on same candle"}
            break
    else:
        # Max days reached
        if days:
            exit_price = days[-1]["close"]
            pnl = (entry - exit_price) if is_short else (exit_price - entry)
            result = {"outcome": "max_days", "exit_day": len(days),
                      "exit_date": days[-1]["date"],
                      "exit_price": exit_price, "pnl": round(pnl, 2)}
        else:
            result = {"outcome": "no_data", "error": "No forward data available"}

    return {"result": result, "daily_data": days}


MAX_RETRIES = 2


def _verify_state(date: str, lookback: str, expect_overlays: dict | None = None) -> str | None:
    """Check chart state matches expectations. Returns error string or None if OK."""
    state = _get_state()
    # Date check
    actual_date = state.get("date", "")
    if not actual_date:
        return "no date in state"
    target_dt = datetime.strptime(date, "%Y-%m-%d")
    actual_dt = datetime.strptime(actual_date[:10], "%Y-%m-%d")
    if abs((actual_dt - target_dt).days) > 3:
        return f"date mismatch: chart={actual_date}, target={date}"
    # Lookback check
    expected_lb = LOOKBACK_DAYS.get(lookback)
    if state.get("lookback") != expected_lb:
        return f"lookback mismatch: chart={state.get('lookback')}, expected={expected_lb}"
    # Trading days must be on
    if not state.get("trading_days"):
        return "trading_days is off"
    # Overlay checks
    if expect_overlays:
        for key, expected in expect_overlays.items():
            if state.get(key) != expected:
                return f"{key} mismatch: chart={state.get(key)}, expected={expected}"
    return None


def _setup_chart(ticker: str, date: str, lookback: str) -> str | None:
    """Navigate chart to ticker/date/lookback with overlays off.

    Returns error string on failure, None on success.
    """
    state = _get_state()
    if state.get("ticker") != ticker.upper():
        _send_cmd({"action": "ticker", "value": ticker})
        if not _wait_for_state("ticker", ticker.upper(), timeout=10):
            return "ticker did not change"
        time.sleep(1)  # let data load settle

    _toggle("trading-days", "on")
    _toggle("vol-profile", "off")
    _toggle("rsi", "off")
    _toggle("sma", "off")
    _toggle("avwap", "off")
    _wait_for_state("trading_days", True, timeout=3)

    _send_cmd({"action": "gtd", "value": date})
    if not _wait_for_date(date):
        return f"gtd {date} did not take effect"

    _send_cmd({"action": "lookback", "value": lookback})
    if not _wait_for_state("lookback", LOOKBACK_DAYS.get(lookback)):
        return f"lookback {lookback} did not take effect"

    time.sleep(0.3)
    return _verify_state(date, lookback,
                         {"volume_profile": False, "rsi": False})


def _enable_overlays(date: str, lookback: str) -> str | None:
    """Turn on VP + RSI and verify. Returns error string or None."""
    _toggle("vol-profile", "on")
    _toggle("rsi", "on")
    _wait_for_state("volume_profile", True, timeout=3)
    _wait_for_state("rsi", True, timeout=3)
    time.sleep(0.3)
    return _verify_state(date, lookback,
                         {"volume_profile": True, "rsi": True})


# ── Single date backtest ────────────────────────────────────────────────

def backtest_date(ticker: str, date: str, lookback: str, model: str,
                  date_dir: Path, df) -> dict | None:
    date_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  {ticker} @ {date}")
    print(f"{'=' * 60}")

    # 1. Navigate chart (with retries)
    print("  [1/7] Setting up chart...")
    for attempt in range(1, MAX_RETRIES + 2):
        err = _setup_chart(ticker, date, lookback)
        if err is None:
            break
        print(f"        Attempt {attempt} failed: {err}")
        if attempt > MAX_RETRIES:
            print(f"  ABORT: could not set up chart after {MAX_RETRIES + 1} attempts")
            return None
        time.sleep(1)

    state = _get_state()
    print(f"        Date: {state.get('date')}  Lookback: {state.get('lookback')}  "
          f"Bars: {state.get('visible_bars')}")

    # 2. Plain snapshot
    print("  [2/7] Plain snapshot...")
    save_dir = str(date_dir.resolve())
    plain_png = None
    for attempt in range(1, MAX_RETRIES + 2):
        err = _verify_state(date, lookback, {"volume_profile": False, "rsi": False})
        if err:
            print(f"        Attempt {attempt} state check failed: {err}")
            if attempt > MAX_RETRIES:
                print(f"  ABORT: state wrong before plain snapshot")
                return None
            _setup_chart(ticker, date, lookback)
            continue
        result = _snapshot(save_dir, "plain")
        if result:
            plain_png = result["png"]
            break
        print(f"        Attempt {attempt} snapshot timed out")
        if attempt > MAX_RETRIES:
            print(f"  ABORT: plain snapshot failed after {MAX_RETRIES + 1} attempts")
            return None
        time.sleep(1)
    print(f"        {plain_png}")

    # 3. Overlay snapshot (VP + RSI)
    print("  [3/7] VP+RSI snapshot...")
    overlay_png = None
    overlay_csv = None
    for attempt in range(1, MAX_RETRIES + 2):
        err = _enable_overlays(date, lookback)
        if err:
            print(f"        Attempt {attempt} overlay state failed: {err}")
            if attempt > MAX_RETRIES:
                print(f"  ABORT: could not enable overlays")
                return None
            time.sleep(1)
            continue
        result = _snapshot(save_dir, "overlay")
        if result:
            overlay_png = result["png"]
            overlay_csv = result["csv"]
            break
        print(f"        Attempt {attempt} snapshot timed out")
        if attempt > MAX_RETRIES:
            print(f"  ABORT: overlay snapshot failed after {MAX_RETRIES + 1} attempts")
            return None
        time.sleep(1)
    print(f"        {overlay_png}")

    # Restore overlays off
    _toggle("vol-profile", "off")
    _toggle("rsi", "off")

    # 4. Send plain chart to LLM
    chat_id = f"{ticker.lower()}-{date}"
    print(f"  [4/7] Analyzing plain chart... (chat: {chat_id})")
    chat = Chat(model=model, chat_id=chat_id)
    prompt = ANALYSIS_PROMPT.format(lookback=lookback)
    reply1 = chat.send(text=prompt, image_path=plain_png, model=model)
    print(f"        {reply1[:100]}...")

    # 5. Follow-up with overlay + CSV
    print(f"  [5/7] Follow-up with VP+RSI+CSV...")
    csv_text = Path(overlay_csv).read_text()
    reply2 = chat.send(
        text=FOLLOWUP_PROMPT,
        image_path=overlay_png,
        attachment_text=csv_text,
        model=model,
    )
    print(f"        {reply2[:100]}...")

    # 6. Fork and get structured JSON
    print(f"  [6/7] Extracting trade parameters...")
    json_chat = chat.fork(f"{chat_id}-json", model=model)
    json_prompt = JSON_PROMPT.format(ticker=ticker.upper(), date=date)
    reply3 = json_chat.send(text=json_prompt, model=model)

    try:
        text = reply3.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        trade = json.loads(text)
    except json.JSONDecodeError:
        print(f"  WARN: Failed to parse JSON, saving raw response")
        trade = {"error": "parse_failed", "raw": reply3}

    # 7. Check actual result
    if "entry" in trade:
        print(f"  [7/7] Checking actual result...")
        actual = _check_trade(
            df, date,
            entry=trade["entry"],
            stop_loss=trade["stop_loss"],
            take_profit=trade["take_profit"],
            max_days=trade["max_days"],
            direction=trade["direction"],
        )
        trade["actual"] = actual
        outcome = actual.get("result", {})
        pnl = outcome.get("pnl", "?")
        print(f"        {outcome.get('outcome')} on day {outcome.get('exit_day')} — P&L: ${pnl}")
    else:
        print(f"  [7/7] Skipped (no trade params)")

    # Save artifacts
    trade["chat_id"] = chat_id
    trade["model"] = model
    trade["lookback"] = lookback
    (date_dir / "trade.json").write_text(json.dumps(trade, indent=2))

    # Copy chat history into the date folder
    chat_src = Path(f".cache/chats/{chat_id}.json")
    if chat_src.exists():
        shutil.copy2(chat_src, date_dir / "chat.json")
    chat_json_src = Path(f".cache/chats/{chat_id}-json.json")
    if chat_json_src.exists():
        shutil.copy2(chat_json_src, date_dir / "chat-json.json")

    print(f"  Done: {date_dir}/")
    return trade


# ── Report ──────────────────────────────────────────────────────────────

def _print_report(trades: list[dict], ticker: str, model: str,
                  lookback: str, capital: float, per_trade: float,
                  output_dir: Path):
    if not trades:
        print("\nNo trades to report.")
        return

    W = 64
    print(f"\n{'=' * W}")
    print(f"  BACKTEST REPORT: {ticker}")
    print(f"{'=' * W}")
    print(f"  Model:        {model}")
    print(f"  Lookback:     {lookback}")
    print(f"  Trades:       {len(trades)}")
    print(f"  Capital:      ${capital:,.2f}")
    print(f"  Per trade:    ${per_trade:,.2f}")

    # Trade log with portfolio simulation
    print(f"\n  {'Date':<12} {'Dir':>5} {'Entry':>8} {'Exit':>8} "
          f"{'Outcome':<12} {'$/shr':>7} {'Shares':>6} {'Trade$':>8} {'Balance':>12}")
    print(f"  {'-' * (W - 4)}")

    balance = capital
    wins = 0
    losses = 0
    flat = 0
    total_pnl_dollar = 0.0
    biggest_win = 0.0
    biggest_loss = 0.0
    win_pnls = []
    loss_pnls = []
    peak = capital
    max_drawdown = 0.0
    outcomes = {"take_profit": 0, "stop_loss": 0, "max_days": 0, "ambiguous": 0}

    for t in trades:
        actual = t.get("actual", {}).get("result", {})
        outcome = actual.get("outcome", "unknown")
        pnl_per_share = actual.get("pnl", 0)
        date = t.get("date", "?")
        direction = t.get("direction", "?")
        entry = t.get("entry", 0)
        exit_price = actual.get("exit_price", entry)

        # Position sizing: allocate $per_trade to this trade
        if entry > 0:
            shares = int(per_trade / entry)
            if shares < 1:
                shares = 1
        else:
            shares = 0
        trade_pnl = round(pnl_per_share * shares, 2)
        balance += trade_pnl
        total_pnl_dollar += trade_pnl

        # Track stats
        if outcome in outcomes:
            outcomes[outcome] += 1
        if pnl_per_share > 0:
            wins += 1
            win_pnls.append(trade_pnl)
            biggest_win = max(biggest_win, trade_pnl)
        elif pnl_per_share < 0:
            losses += 1
            loss_pnls.append(trade_pnl)
            biggest_loss = min(biggest_loss, trade_pnl)
        else:
            flat += 1

        # Drawdown
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_drawdown = max(max_drawdown, dd)

        # Print row
        sign = "+" if trade_pnl >= 0 else ""
        print(f"  {date:<12} {direction:>5} {entry:>8.2f} {exit_price:>8.2f} "
              f"{outcome:<12} {pnl_per_share:>+7.2f} {shares:>6} "
              f"{sign}{trade_pnl:>7.2f} {balance:>12,.2f}")

    # Summary stats
    print(f"\n  {'─' * (W - 4)}")
    print(f"  RESULTS")
    print(f"  {'─' * (W - 4)}")

    win_rate = wins / len(trades) * 100 if trades else 0
    print(f"  Win rate:     {win_rate:.0f}%  ({wins}W / {losses}L"
          + (f" / {flat}F" if flat else "") + f" of {len(trades)})")
    print()
    print(f"  By outcome:   TP={outcomes['take_profit']}  "
          f"SL={outcomes['stop_loss']}  "
          f"MaxDays={outcomes['max_days']}"
          + (f"  Ambiguous={outcomes['ambiguous']}" if outcomes['ambiguous'] else ""))
    print()

    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    print(f"  Avg win:      ${avg_win:+,.2f}")
    print(f"  Avg loss:     ${avg_loss:+,.2f}")
    print(f"  Biggest win:  ${biggest_win:+,.2f}")
    print(f"  Biggest loss: ${biggest_loss:+,.2f}")
    if avg_loss != 0:
        expectancy = avg_win * (wins / len(trades)) + avg_loss * (losses / len(trades))
        print(f"  Expectancy:   ${expectancy:+,.2f} per trade")
    print()

    ret = (balance - capital) / capital * 100
    print(f"  Starting:     ${capital:>12,.2f}")
    print(f"  Ending:       ${balance:>12,.2f}")
    print(f"  Net P&L:      ${total_pnl_dollar:>+12,.2f}")
    print(f"  Return:       {ret:>+11.2f}%")
    print(f"  Max drawdown: {max_drawdown:>11.2f}%")
    print(f"{'=' * W}")

    # Save summary JSON
    summary = {
        "ticker": ticker,
        "model": model,
        "lookback": lookback,
        "capital": capital,
        "per_trade": per_trade,
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": round(win_rate, 1),
        "outcomes": outcomes,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "biggest_win": round(biggest_win, 2),
        "biggest_loss": round(biggest_loss, 2),
        "starting_capital": capital,
        "ending_capital": round(balance, 2),
        "net_pnl": round(total_pnl_dollar, 2),
        "return_pct": round(ret, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "trades": trades,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n  Summary saved to {summary_path}")




def _find_csv_path(ticker: str) -> Path | None:
    for d in ["data", "data/nse"]:
        p = Path(d) / f"{ticker.lower()}.csv"
        if p.exists():
            return p
        p = Path(d) / f"{ticker.lower().replace('&', '_')}.csv"
        if p.exists():
            return p
    return None


def main():
    p = argparse.ArgumentParser(
        prog="backtest",
        description="Automated backtesting across multiple dates",
    )
    p.add_argument("ticker", help="Ticker symbol (e.g. NVDA, AAPL)")
    p.add_argument("--start", "-s", help="Start date (default: first date in data)")
    p.add_argument("--end", "-e", help="End date (default: last date in data)")
    p.add_argument("--every", type=int, metavar="N",
                   help="Test every Nth trading day (default: every day)")
    p.add_argument("--lookback", default="6M",
                   choices=["3M", "6M", "9M", "1Y", "2Y"])
    p.add_argument("--model", default=MODELS[1],
                   help=f"Model (default: {MODELS[1]})")
    p.add_argument("--output", default="results/backtest",
                   help="Output root directory")
    p.add_argument("--capital", type=float, default=100000,
                   help="Starting capital (default: 100000)")
    p.add_argument("--per-trade", type=float, default=100,
                   help="Dollar amount allocated per trade (default: 100)")
    args = p.parse_args()

    ticker = args.ticker.upper()

    # Load price data
    csv_path = _find_csv_path(ticker)
    if csv_path is None:
        print(f"No CSV found for {ticker}", file=sys.stderr)
        sys.exit(1)
    df = load_csv(csv_path)
    dates_all = [str(d) for d in df.index]
    print(f"Loaded {ticker}: {len(df)} bars ({dates_all[0]} to {dates_all[-1]})")

    # Build date list from range — only actual trading days from data
    start = args.start or dates_all[0]
    end = args.end or dates_all[-1]
    # Filter to range, leave room for forward data (at least 20 bars)
    max_idx = len(dates_all) - 21
    in_range = [d for i, d in enumerate(dates_all)
                if d >= start and d <= end and i <= max_idx]
    every = args.every or 1
    dates = in_range[::every]

    if not dates:
        print(f"No trading days found in {start} to {end}", file=sys.stderr)
        sys.exit(1)

    print(f"Selected {len(dates)} trading days"
          + (f" (every {every})" if every > 1 else "")
          + f" from {dates[0]} to {dates[-1]}")

    # Verify server is running
    try:
        _get_state()
    except Exception:
        print("Cannot reach server at http://127.0.0.1:8000", file=sys.stderr)
        print("Start it with: uv run python -m src.server", file=sys.stderr)
        sys.exit(1)

    # Run backtests
    output_dir = Path(args.output) / ticker
    output_dir.mkdir(parents=True, exist_ok=True)

    trades = []
    for date in dates:
        date_dir = output_dir / date
        trade = backtest_date(ticker, date, args.lookback, args.model,
                              date_dir, df)
        if trade:
            trades.append(trade)
        else:
            print(f"\n  ABORTED at {date} — stopping backtest run.")
            break

    # ── Report ───────────────────────────────────────────────────────
    _print_report(trades, ticker, args.model, args.lookback,
                  args.capital, args.per_trade, output_dir)


if __name__ == "__main__":
    import pandas as pd
    main()
