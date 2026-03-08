"""Automated backtesting script.

Runs the full analysis workflow for a ticker across multiple dates:
1. Navigate chart to date with lookback window
2. Snapshot plain chart
3. Enable Volume Profile + RSI, snapshot again
4. Send plain chart to LLM for analysis
5. Follow-up with overlay chart + CSV data
6. Extract structured trade parameters (JSON)
7. Check actual price data for trade result

Chart captures run sequentially (one browser). LLM calls run concurrently
via asyncio — while waiting for OpenRouter on date N, we capture date N+1.

Requires: server running (python -m src.server) + browser open to chart.

Usage:
  tb backtest NVDA -s 2025-01-01 -e 2025-01-07
  tb backtest NVDA -s 2024-06-01 -e 2024-12-31 --every 20
  tb backtest NVDA --every 5 --lookback 1Y --model anthropic/claude-sonnet-4.6
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from src.loader import load_csv
from src.openrouter import Chat, MODELS

log = logging.getLogger("backtest")

API = "http://127.0.0.1:8000"

WEEKLY_PROMPT = (
    "Analyse this weekly chart (1-year lookback with Volume Profile). "
    "Identify: 1) Key resistance zones 2) Key support zones "
    "3) Overall trend direction and strength "
    "4) Any significant patterns (bases, breakouts, breakdowns, consolidations). "
    "Do NOT suggest a specific trade yet — focus on the broader market structure."
)

DAILY_PROMPT = (
    "Here is the daily chart ({lookback} lookback with Volume Profile) for the same stock. "
    "Based on the weekly structure above and what you see on the daily, take a specific position: "
    "direction (long or short), stop loss, take profit, and max trading days. "
    "\n\nIMPORTANT: You are viewing the chart at market close (end of day). The trade "
    "executes at the MARKET OPEN of the next trading day (T+1) — no entry price needed. "
    "Set your stop loss and take profit as absolute price levels based on chart structure "
    "(support/resistance zones, ATR, key levels). Do NOT set them relative to an unknown entry."
)

CONTEXT_PROMPT = (
    "Here is additional quantitative context:\n\n"
    "{stats}\n\n"
    "[Visible bar data CSV]\n{csv}\n\n"
    "Review this data and confirm or update your position. "
    "State your FINAL position clearly with all parameters — even if unchanged: "
    "direction, stop loss, take profit, max_days, confidence (low/medium/high), "
    "and a one-sentence rationale."
)

JSON_PROMPT = (
    'Based on the analyst\'s final position statement above, extract a structured JSON '
    'with exactly these fields: '
    '{{"ticker": "{ticker}", "date": "{date}", "direction": "long|short", '
    '"stop_loss": <price>, "take_profit": <price>, '
    '"max_days": <int>, "risk_reward": <float>, "confidence": "low|medium|high", '
    '"key_levels": {{"resistance": [<prices>], "support": [<prices>]}}, '
    '"rationale": "<1 sentence>"}} '
    '— trade enters at T+1 market open; stop_loss and take_profit are absolute price levels. '
    'Return ONLY the JSON, no markdown fences, no explanation.'
)

LOOKBACK_DAYS = {"3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504}
MAX_RETRIES = 2
DEFAULT_CONCURRENCY = 5  # max parallel OpenRouter analysis tasks


def _compute_stats(df, analysis_date: str, lookback: str) -> str:
    """Compute quantitative stats for the lookback window ending at analysis_date."""
    dates = [str(d) for d in df.index]
    idx = None
    for i, d in enumerate(dates):
        if d >= analysis_date:
            idx = i
            break
    if idx is None:
        return ""

    lb_days = LOOKBACK_DAYS.get(lookback, 126)
    start_idx = max(0, idx - lb_days + 1)
    window = df.iloc[start_idx:idx + 1]

    start_price = float(window.iloc[0]["Close"])
    end_price = float(window.iloc[-1]["Close"])
    pct_change = (end_price - start_price) / start_price * 100

    # ATR-14
    true_ranges = []
    for i in range(max(1, idx - 13), idx + 1):
        tr = max(
            float(df.iloc[i]["High"]) - float(df.iloc[i]["Low"]),
            abs(float(df.iloc[i]["High"]) - float(df.iloc[i - 1]["Close"])),
            abs(float(df.iloc[i]["Low"]) - float(df.iloc[i - 1]["Close"])),
        )
        true_ranges.append(tr)
    atr = sum(true_ranges) / len(true_ranges) if true_ranges else 0

    # MA distances
    ma50 = float(df.iloc[max(0, idx - 49):idx + 1]["Close"].mean())
    ma200 = float(df.iloc[max(0, idx - 199):idx + 1]["Close"].mean())
    dist50 = (end_price - ma50) / ma50 * 100
    dist200 = (end_price - ma200) / ma200 * 100

    return (
        f"- {lookback} period return: ${start_price:.2f} → ${end_price:.2f} "
        f"({pct_change:+.1f}%)\n"
        f"- 14-day ATR: ${atr:.2f}\n"
        f"- Price vs 50-day MA: {dist50:+.1f}%\n"
        f"- Price vs 200-day MA: {dist200:+.1f}%"
    )


# ── Server helpers ──────────────────────────────────────────────────────

def _send_cmd(cmd: dict):
    log.debug("send_cmd: %s", cmd)
    data = json.dumps(cmd).encode()
    req = urllib.request.Request(
        f"{API}/api/command", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    log.debug("send_cmd response: %s", result)
    return result


def _get_state() -> dict:
    with urllib.request.urlopen(f"{API}/api/state") as resp:
        state = json.loads(resp.read()) or {}
    log.debug("get_state: ticker=%s date=%s lookback=%s td=%s vp=%s rsi=%s",
              state.get("ticker"), state.get("date"), state.get("lookback"),
              state.get("trading_days"), state.get("volume_profile"), state.get("rsi"))
    return state


def _wait_for_state(key: str, expected, timeout: float = 5.0) -> bool:
    log.debug("wait_for_state: %s == %s (timeout=%.1f)", key, expected, timeout)
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = _get_state()
        if state.get(key) == expected:
            log.debug("wait_for_state: %s matched in %.1fs", key, time.time() - t0)
            return True
        time.sleep(0.2)
    log.debug("wait_for_state: %s TIMED OUT (got %s)", key, _get_state().get(key))
    return False


def _wait_for_date(target: str, timeout: float = 8.0) -> bool:
    log.debug("wait_for_date: target=%s (timeout=%.1f)", target, timeout)
    target_dt = datetime.strptime(target, "%Y-%m-%d")
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = _get_state()
        state_date = state.get("date", "")
        if state_date:
            try:
                state_dt = datetime.strptime(state_date[:10], "%Y-%m-%d")
                if abs((state_dt - target_dt).days) <= 3:
                    log.debug("wait_for_date: matched %s in %.1fs", state_date, time.time() - t0)
                    return True
            except ValueError:
                pass
        time.sleep(0.3)
    log.debug("wait_for_date: TIMED OUT (chart date=%s)", _get_state().get("date"))
    return False


def _snapshot(save_dir: str, prefix: str, timeout: float = 15.0) -> dict | None:
    log.debug("snapshot: dir=%s prefix=%s", save_dir, prefix)
    _send_cmd({"action": "snapshot", "save_dir": save_dir, "prefix": prefix})
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{API}/api/snapshot/result") as resp:
                result = json.loads(resp.read())
                if result and result.get("ok"):
                    log.debug("snapshot: OK in %.1fs — %s", time.time() - t0, result.get("png"))
                    return result
                elif result and not result.get("ok"):
                    log.debug("snapshot: FAILED — %s", result)
                    return None
        except Exception:
            pass
        time.sleep(0.3)
    log.debug("snapshot: TIMED OUT after %.1fs", timeout)
    return None


def _toggle(key: str, value: str):
    log.debug("toggle: %s → %s", key, value)
    _send_cmd({"action": "toggle", "key": key, "value": value})


# ── Trade evaluation ────────────────────────────────────────────────────

def _check_trade(df, analysis_date: str, entry: float, stop_loss: float,
                 take_profit: float, max_days: int, direction: str,
                 market_open: bool = False) -> dict:
    """Check trade outcome against actual price data.

    T+1 (next trading day after analysis_date) is the entry day.
    market_open=False (default): entry is a limit order — fills only if
        T+1 low <= entry <= T+1 high.
    market_open=True: always enter at T+1 open price, ignore entry price.
    SL and TP are absolute price levels, live from T+1 onward.
    max_days counts from entry (T+1 = day 1).
    """
    dates = [str(d) for d in df.index]
    try:
        idx = dates.index(analysis_date)
    except ValueError:
        for i, d in enumerate(dates):
            if d >= analysis_date:
                idx = i
                break
        else:
            return {"result": {"outcome": "no_data", "error": f"Date {analysis_date} not found"}, "daily_data": []}

    if idx + 1 >= len(df):
        return {"result": {"outcome": "no_data", "error": "No T+1 data available"}, "daily_data": []}

    is_short = direction.lower() == "short"

    # T+1: entry day
    entry_row = df.iloc[idx + 1]
    entry_date = dates[idx + 1]
    entry_day = {
        "day": 1, "date": entry_date,
        "open": round(float(entry_row["Open"]), 2),
        "high": round(float(entry_row["High"]), 2),
        "low": round(float(entry_row["Low"]), 2),
        "close": round(float(entry_row["Close"]), 2),
        "volume": int(entry_row["Volume"]),
    }

    if market_open:
        # Always fill at T+1 open — unless open has already gapped through SL or TP
        entry = round(float(entry_row["Open"]), 2)
        if is_short:
            gap_sl = entry >= stop_loss   # gapped up past stop loss
            gap_tp = entry <= take_profit  # gapped down past take profit
        else:
            gap_sl = entry <= stop_loss   # gapped down past stop loss
            gap_tp = entry >= take_profit  # gapped up past take profit
        if gap_sl or gap_tp:
            reason = "gap_through_sl" if gap_sl else "gap_through_tp"
            return {
                "result": {
                    "outcome": "no_entry",
                    "reason": reason,
                    "entry_date": entry_date,
                    "t1_open": entry,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pnl": 0,
                },
                "daily_data": [entry_day],
            }
    elif not (float(entry_row["Low"]) <= entry <= float(entry_row["High"])):
        return {
            "result": {
                "outcome": "no_entry",
                "entry_date": entry_date,
                "day_range": [entry_day["low"], entry_day["high"]],
                "requested_entry": entry,
                "pnl": 0,
            },
            "daily_data": [entry_day],
        }

    # Entered — SL/TP are live from T+1 onward (including entry day)
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
            result = {"outcome": "ambiguous", "exit_day": i, "exit_date": d,
                      "note": "Both SL and TP breached on same candle"}
            break
    else:
        if days:
            exit_price = days[-1]["close"]
            pnl = (entry - exit_price) if is_short else (exit_price - entry)
            result = {"outcome": "max_days", "exit_day": len(days),
                      "exit_date": days[-1]["date"],
                      "exit_price": exit_price, "pnl": round(pnl, 2)}
        else:
            result = {"outcome": "no_data", "error": "No forward data available"}

    # ── Metrics ──
    if days:
        highs = [d["high"] for d in days]
        lows = [d["low"] for d in days]
        if is_short:
            mae = round(max(0.0, max(highs) - entry), 2)
            mfe = round(max(0.0, entry - min(lows)), 2)
        else:
            mae = round(max(0.0, entry - min(lows)), 2)
            mfe = round(max(0.0, max(highs) - entry), 2)
    else:
        mae = mfe = 0.0

    # price_at_max_days: close at T+max_days from analysis_date
    price_at_max_days = None
    pm_idx = idx + max_days
    if pm_idx < len(df):
        price_at_max_days = round(float(df.iloc[pm_idx]["Close"]), 2)

    # direction_correct: did TP level get reached within 2×max_days?
    direction_correct = False
    look_end = min(max_days * 2, len(df) - idx - 1)
    for i in range(1, look_end + 1):
        fwd = df.iloc[idx + i]
        if is_short and float(fwd["Low"]) <= take_profit:
            direction_correct = True
            break
        elif not is_short and float(fwd["High"]) >= take_profit:
            direction_correct = True
            break

    result["mae"] = mae
    result["mfe"] = mfe
    result["direction_correct"] = direction_correct
    result["price_at_max_days"] = price_at_max_days
    result["actual_entry"] = entry
    return {"result": result, "daily_data": days}


# ── Reprocess existing results ──────────────────────────────────────────

def reprocess_results(ticker: str, output_dir: Path, df,
                      start: str | None = None, end: str | None = None,
                      market_open: bool = False) -> list[dict]:
    """Re-evaluate existing trade.json files, updating actual outcome + new metrics."""
    ticker_dir = output_dir / ticker
    if not ticker_dir.exists():
        print(f"No results found at {ticker_dir}")
        return []

    trades = []
    for date_dir in sorted(d for d in ticker_dir.iterdir() if d.is_dir()):
        date = date_dir.name
        if start and date < start:
            continue
        if end and date > end:
            continue
        trade_file = date_dir / "trade.json"
        if not trade_file.exists():
            continue
        try:
            trade = json.loads(trade_file.read_text())
        except (json.JSONDecodeError, KeyError):
            print(f"  [{date}] WARN: bad JSON, skipping")
            continue

        if "stop_loss" in trade and "take_profit" in trade:
            actual = _check_trade(
                df, date,
                entry=float(trade.get("entry", 0)),
                stop_loss=float(trade["stop_loss"]),
                take_profit=float(trade["take_profit"]),
                max_days=int(trade["max_days"]),
                direction=trade.get("direction", "long"),
                market_open=market_open,
            )
            if market_open:
                trade["actual_market_open"] = actual
            else:
                trade["actual"] = actual
            trade_file.write_text(json.dumps(trade, indent=2))
            # Always expose under "actual" in-memory so _print_report works for both modes
            trade["actual"] = actual
            res = actual.get("result", {})
            outcome = res.get("outcome", "?")
            if outcome == "no_entry":
                print(f"  [{date}] no_entry")
            else:
                mae = res.get("mae", 0) or 0
                mfe = res.get("mfe", 0) or 0
                dc = "Y" if res.get("direction_correct") else "N"
                print(f"  [{date}] {outcome:<12} mae={mae:>6.2f}  mfe={mfe:>6.2f}  dir={dc}")

        trades.append(trade)

    return trades


# ── State verification ──────────────────────────────────────────────────

def _verify_state(date: str, lookback: str, expect_overlays: dict | None = None) -> str | None:
    log.debug("verify_state: date=%s lookback=%s overlays=%s", date, lookback, expect_overlays)
    state = _get_state()
    actual_date = state.get("date", "")
    if not actual_date:
        return "no date in state"
    target_dt = datetime.strptime(date, "%Y-%m-%d")
    actual_dt = datetime.strptime(actual_date[:10], "%Y-%m-%d")
    if abs((actual_dt - target_dt).days) > 3:
        return f"date mismatch: chart={actual_date}, target={date}"
    expected_lb = LOOKBACK_DAYS.get(lookback)
    if state.get("lookback") != expected_lb:
        return f"lookback mismatch: chart={state.get('lookback')}, expected={expected_lb}"
    if not state.get("trading_days"):
        return "trading_days is off"
    if expect_overlays:
        for key, expected in expect_overlays.items():
            if state.get(key) != expected:
                return f"{key} mismatch: chart={state.get(key)}, expected={expected}"
    return None


def _reset_chart(ticker: str) -> None:
    """Send a full reset command: reloads ticker data and restores all defaults."""
    log.debug("reset_chart: ticker=%s", ticker)
    _send_cmd({"action": "reset", "ticker": ticker})
    _wait_for_state("ticker", ticker.upper(), timeout=15)
    time.sleep(2)  # allow full reload + render


def _setup_chart(ticker: str, date: str, lookback: str) -> str | None:
    log.debug("setup_chart: ticker=%s date=%s lookback=%s", ticker, date, lookback)
    state = _get_state()
    if state.get("ticker") != ticker.upper():
        _send_cmd({"action": "ticker", "value": ticker})
        if not _wait_for_state("ticker", ticker.upper(), timeout=15):
            if _get_state().get("ticker") != ticker.upper():
                return "ticker did not change"
        time.sleep(1)

    _toggle("trading-days", "on")
    _toggle("vol-profile", "off")
    _toggle("rsi", "off")
    _toggle("sma", "off")
    _toggle("avwap", "off")
    _wait_for_state("trading_days", True, timeout=5)
    _wait_for_state("volume_profile", False, timeout=5)
    _wait_for_state("rsi", False, timeout=5)

    _send_cmd({"action": "gtd", "value": date})
    if not _wait_for_date(date):
        return f"gtd {date} did not take effect"

    _send_cmd({"action": "lookback", "value": lookback})
    if not _wait_for_state("lookback", LOOKBACK_DAYS.get(lookback)):
        return f"lookback {lookback} did not take effect"

    time.sleep(0.5)
    return _verify_state(date, lookback, {"volume_profile": False, "rsi": False})


def _enable_overlays(date: str, lookback: str) -> str | None:
    _toggle("vol-profile", "on")
    _toggle("rsi", "on")
    _wait_for_state("volume_profile", True, timeout=3)
    _wait_for_state("rsi", True, timeout=3)
    time.sleep(0.3)
    return _verify_state(date, lookback, {"volume_profile": True, "rsi": True})


# ── Phase 1: Capture (sequential — needs the browser) ──────────────────

def capture_date(ticker: str, date: str, lookback: str,
                 date_dir: Path) -> dict | None:
    """Navigate chart, take plain + overlay snapshots.

    Returns dict with file paths, or None on failure (triggers abort).
    """
    log.debug("capture_date START: %s @ %s", ticker, date)
    t0 = time.time()
    date_dir.mkdir(parents=True, exist_ok=True)
    save_dir = str(date_dir.resolve())

    # 1. Setup chart (with retries + escalating delays + full reset on last attempt)
    SETUP_DELAYS = [1, 2, 5]  # seconds to wait before attempts 2, 3, 4
    print(f"  [capture] Setting up chart...")
    for attempt in range(1, len(SETUP_DELAYS) + 2):
        if attempt > len(SETUP_DELAYS):
            # Nuclear option: full chart reset before final attempt
            print(f"        Full chart reset (ticker={ticker})...")
            _reset_chart(ticker)
        err = _setup_chart(ticker, date, lookback)
        if err is None:
            break
        print(f"        Attempt {attempt} failed: {err}")
        if attempt > len(SETUP_DELAYS):
            print(f"  ABORT: could not set up chart after {attempt} attempts")
            return None
        delay = SETUP_DELAYS[attempt - 1]
        print(f"        Waiting {delay}s before retry...")
        time.sleep(delay)

    state = _get_state()
    print(f"        Date: {state.get('date')}  Lookback: {state.get('lookback')}  "
          f"Bars: {state.get('visible_bars')}")

    # 2. Daily snapshot (VP + RSI on)
    print(f"  [capture] Daily+VP+RSI snapshot...")
    daily_png = None
    daily_csv = None
    _toggle("vol-profile", "on")
    _toggle("rsi", "on")
    _wait_for_state("volume_profile", True, timeout=3)
    _wait_for_state("rsi", True, timeout=3)
    time.sleep(1.0)
    for attempt in range(1, MAX_RETRIES + 2):
        err = _verify_state(date, lookback, {"volume_profile": True, "rsi": True})
        if err:
            print(f"        Attempt {attempt} state check failed: {err}")
            if attempt > MAX_RETRIES:
                print(f"  ABORT: state wrong before daily snapshot")
                return None
            _setup_chart(ticker, date, lookback)
            _toggle("vol-profile", "on")
            _toggle("rsi", "on")
            _wait_for_state("volume_profile", True, timeout=3)
            _wait_for_state("rsi", True, timeout=3)
            time.sleep(1.0)
            continue
        result = _snapshot(save_dir, "daily")
        if result:
            daily_png = result["png"]
            daily_csv = result["csv"]
            break
        print(f"        Attempt {attempt} snapshot timed out")
        if attempt > MAX_RETRIES:
            print(f"  ABORT: daily snapshot failed after {MAX_RETRIES + 1} attempts")
            return None
        time.sleep(1)
    print(f"        {daily_png}")

    # 3. Weekly snapshot — RSI off, VP stays on, switch interval + lookback
    print(f"  [capture] Weekly (W, 1Y, VP) snapshot...")
    weekly_png = None
    _toggle("rsi", "off")
    _send_cmd({"action": "interval", "value": "W"})
    _wait_for_state("interval", "weekly", timeout=3)
    _send_cmd({"action": "lookback", "value": "1Y"})
    _wait_for_state("lookback", LOOKBACK_DAYS["1Y"], timeout=3)
    time.sleep(0.3)
    for attempt in range(1, MAX_RETRIES + 2):
        result = _snapshot(save_dir, "weekly")
        if result:
            weekly_png = result["png"]
            break
        print(f"        Attempt {attempt} weekly snapshot timed out")
        if attempt > MAX_RETRIES:
            print(f"  WARN: weekly snapshot failed — continuing without it")
            break
        time.sleep(1)
    if weekly_png:
        print(f"        {weekly_png}")

    # Restore: VP off, RSI off, daily interval, original lookback
    _toggle("vol-profile", "off")
    _toggle("rsi", "off")
    _send_cmd({"action": "interval", "value": "D"})
    _wait_for_state("interval", "daily", timeout=5)
    _send_cmd({"action": "lookback", "value": lookback})
    _wait_for_state("lookback", LOOKBACK_DAYS.get(lookback), timeout=5)
    # Wait for chart to fully settle before next date's setup reads state
    _wait_for_state("ticker", ticker.upper(), timeout=5)
    time.sleep(0.5)

    log.debug("capture_date DONE: %s in %.1fs", date, time.time() - t0)
    return {
        "daily_png": daily_png,
        "daily_csv": daily_csv,
        "weekly_png": weekly_png,
    }


# ── Phase 2: Analyze (can run concurrently — pure I/O) ─────────────────

def analyze_date(ticker: str, date: str, lookback: str, model: str,
                 captures: dict, date_dir: Path, df) -> dict | None:
    """Send snapshots to LLM, get trade params, check actual result.

    This is blocking I/O (OpenRouter HTTP calls). Runs in a background
    thread via asyncio.to_thread() so the event loop stays free for
    other work (like capturing the next date).
    """
    t0 = time.time()
    daily_png = captures["daily_png"]
    daily_csv = captures["daily_csv"]
    weekly_png = captures.get("weekly_png")
    chat_id = f"{ticker.lower()}-{date}"
    log.debug("analyze_date START: %s chat_id=%s model=%s", date, chat_id, model)

    chat = Chat(model=model, chat_id=chat_id)

    # 1. Weekly chart → market structure (no position yet)
    print(f"  [{date} analyze] Weekly chart → market structure...")
    log.debug("[%s] LLM call 1/4: weekly chart", date)
    reply1 = chat.send(text=WEEKLY_PROMPT, image_path=weekly_png, model=model)
    log.debug("[%s] LLM call 1/4 done: %d chars", date, len(reply1))
    print(f"  [{date} analyze] Got weekly analysis ({len(reply1)} chars)")

    # 2. Daily chart → take a position
    print(f"  [{date} analyze] Daily chart → position...")
    log.debug("[%s] LLM call 2/4: daily chart", date)
    reply2 = chat.send(
        text=DAILY_PROMPT.format(lookback=lookback),
        image_path=daily_png,
        model=model,
    )
    log.debug("[%s] LLM call 2/4 done: %d chars", date, len(reply2))
    print(f"  [{date} analyze] Got position ({len(reply2)} chars)")

    # 3. Text-only: stats + CSV → confirm/update, state final position
    print(f"  [{date} analyze] Context + CSV → final position...")
    stats_text = _compute_stats(df, date, lookback)
    csv_text = Path(daily_csv).read_text()
    log.debug("[%s] LLM call 3/4: context + CSV (%d chars)", date, len(csv_text))
    reply3 = chat.send(
        text=CONTEXT_PROMPT.format(stats=stats_text, csv=csv_text),
        model=model,
    )
    log.debug("[%s] LLM call 3/4 done: %d chars", date, len(reply3))
    print(f"  [{date} analyze] Got final position ({len(reply3)} chars)")

    # 4. Fresh flash chat with ONLY step 3's output → JSON extraction
    print(f"  [{date} analyze] Extracting trade parameters...")
    json_chat = Chat(model=MODELS[0], chat_id=f"{chat_id}-json")
    json_prompt = (f"Analyst's final position:\n\n{reply3}\n\n"
                   + JSON_PROMPT.format(ticker=ticker.upper(), date=date))
    log.debug("[%s] LLM call 4/4: JSON extraction", date)
    reply4 = json_chat.send(text=json_prompt, model=MODELS[0])
    log.debug("[%s] LLM call 4/4 done: %s", date, reply4[:200])

    try:
        text = reply4.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        trade = json.loads(text)
    except json.JSONDecodeError as e:
        log.debug("[%s] JSON parse error: %s — raw: %s", date, e, reply4[:300])
        print(f"  [{date} analyze] WARN: Failed to parse JSON")
        trade = {"error": "parse_failed", "raw": reply4}

    # 7. Check actual result (always market-on-open)
    if "stop_loss" in trade and "take_profit" in trade:
        actual = _check_trade(
            df, date,
            entry=0,  # overridden by market_open=True
            stop_loss=float(trade["stop_loss"]),
            take_profit=float(trade["take_profit"]),
            max_days=int(trade["max_days"]),
            direction=trade["direction"],
            market_open=True,
        )
        trade["actual"] = actual
        outcome = actual.get("result", {})
        if outcome.get("outcome") == "no_entry":
            print(f"  [{date} analyze] NO ENTRY — {outcome.get('reason')} "
                  f"(T+1 open: {outcome.get('t1_open')})")
        else:
            pnl = outcome.get("pnl", "?")
            print(f"  [{date} analyze] {outcome.get('outcome')} "
                  f"day {outcome.get('exit_day')} — P&L: ${pnl}")
    else:
        print(f"  [{date} analyze] Skipped (no trade params)")

    # Save artifacts
    trade["chat_id"] = chat_id
    trade["model"] = model
    trade["lookback"] = lookback
    (date_dir / "trade.json").write_text(json.dumps(trade, indent=2))

    chat_src = Path(f".cache/chats/{chat_id}.json")
    if chat_src.exists():
        shutil.copy2(chat_src, date_dir / "chat.json")
    chat_json_src = Path(f".cache/chats/{chat_id}-json.json")
    if chat_json_src.exists():
        shutil.copy2(chat_json_src, date_dir / "chat-json.json")

    log.debug("analyze_date DONE: %s in %.1fs", date, time.time() - t0)
    return trade


# ── Pipeline: capture sequentially, analyze concurrently ────────────────

async def run_pipeline(ticker: str, dates: list[str], lookback: str,
                       model: str, output_dir: Path, df,
                       concurrency: int = DEFAULT_CONCURRENCY) -> list[dict]:
    """Main async pipeline.

    - capture_date() runs on the main thread (sequential, needs browser)
    - analyze_date() runs via asyncio.to_thread() (concurrent, pure I/O)
    - Semaphore limits max parallel OpenRouter calls to `concurrency`

    Timeline (concurrency=2):
      capture(d1) → spawn analyze(d1)
      capture(d2) → spawn analyze(d2)   ← d1 analysis running
      capture(d3) → spawn analyze(d3)   ← blocks until d1 or d2 finishes (semaphore)
      ...
      await all analysis tasks
    """
    sem = asyncio.Semaphore(concurrency)
    log.debug("pipeline: %d dates, model=%s, concurrency=%d", len(dates), model, concurrency)
    tasks: list[tuple[str, asyncio.Task]] = []

    async def _throttled_analyze(sem, *args):
        async with sem:
            log.debug("semaphore acquired for %s (%d/%d slots used)",
                      args[1], concurrency - sem._value, concurrency)
            return await asyncio.to_thread(analyze_date, *args)

    for date in dates:
        print(f"\n{'=' * 60}")
        print(f"  {ticker} @ {date}")
        print(f"{'=' * 60}")

        date_dir = output_dir / date

        # ── Cache check 1: skip capture if all screenshots already exist ──
        cached_captures = None
        daily_png_cached  = date_dir / "daily.png"
        daily_csv_cached  = date_dir / "daily.csv"
        weekly_png_cached = date_dir / "weekly.png"
        if daily_png_cached.exists() and daily_csv_cached.exists():
            cached_captures = {
                "daily_png":  str(daily_png_cached),
                "daily_csv":  str(daily_csv_cached),
                "weekly_png": str(weekly_png_cached) if weekly_png_cached.exists() else None,
            }
            print(f"  [capture] Using cached screenshots")

        # ── Cache check 2: skip analysis if trade.json already exists ──
        trade_file = date_dir / "trade.json"
        if trade_file.exists():
            print(f"  [analyze] Using cached trade.json")
            try:
                trade = json.loads(trade_file.read_text())
                async def _cached(t=trade):
                    return t
                tasks.append((date, asyncio.create_task(_cached())))
            except Exception:
                pass
            continue

        if cached_captures:
            captures = cached_captures
        else:
            captures = capture_date(ticker, date, lookback, date_dir)

        if captures is None:
            print(f"\n  ABORTED at {date} — stopping capture pipeline.")
            break

        in_flight = sum(1 for _, t in tasks if not t.done())
        log.debug("pipeline: spawning analyze task for %s (%d in-flight)", date, in_flight)
        task = asyncio.create_task(
            _throttled_analyze(
                sem, ticker, date, lookback, model,
                captures, date_dir, df,
            )
        )
        tasks.append((date, task))

        # Yield control so completed tasks can log and semaphore can release
        await asyncio.sleep(0)

    # Wait for all in-flight analyses to complete
    if tasks:
        pending = [t for _, t in tasks if not t.done()]
        if pending:
            print(f"\n  Waiting for {len(pending)} analysis tasks to finish...")
        await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)

    # Collect results in date order
    trades = []
    for date, task in tasks:
        try:
            trade = task.result()
            if trade:
                trades.append(trade)
                print(f"  [{date}] Done")
        except Exception as e:
            print(f"  [{date}] Analysis failed: {e}")

    return trades


# ── Report ──────────────────────────────────────────────────────────────

def _print_report(trades: list[dict], ticker: str, model: str,
                  lookback: str, capital: float, per_trade: float,
                  output_dir: Path):
    if not trades:
        print("\nNo trades to report.")
        return

    W = 72
    print(f"\n{'=' * W}")
    print(f"  BACKTEST REPORT: {ticker}")
    print(f"{'=' * W}")
    print(f"  Model:        {model}")
    print(f"  Lookback:     {lookback}")
    print(f"  Trades:       {len(trades)}")
    print(f"  Capital:      ${capital:,.2f}")
    print(f"  Per trade:    ${per_trade:,.2f}")

    print(f"\n  {'Date':<12} {'Dir':>5} {'Entry':>8} {'Exit':>8} "
          f"{'Day':>5} {'D':>2} {'Outcome':<12} {'$/shr':>7} {'Shares':>6} {'Trade$':>8} {'Balance':>12}")
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
    outcomes = {"take_profit": 0, "stop_loss": 0, "max_days": 0, "ambiguous": 0, "no_entry": 0}

    for t in trades:
        actual = t.get("actual", {}).get("result", {})
        outcome = actual.get("outcome", "unknown")
        pnl_per_share = actual.get("pnl", 0)
        date = t.get("date", "?")
        direction = t.get("direction", "?")
        entry = actual.get("actual_entry") or t.get("entry", 0)
        exit_price = actual.get("exit_price", entry)

        if outcome in outcomes:
            outcomes[outcome] += 1

        if outcome == "no_entry":
            shares = 0
            trade_pnl = 0
        else:
            if entry > 0:
                shares = int(per_trade / entry)
                if shares < 1:
                    shares = 1
            else:
                shares = 0
            trade_pnl = round(pnl_per_share * shares, 2)

        balance += trade_pnl
        total_pnl_dollar += trade_pnl

        if outcome == "no_entry":
            pass  # don't count in win/loss
        elif pnl_per_share > 0:
            wins += 1
            win_pnls.append(trade_pnl)
            biggest_win = max(biggest_win, trade_pnl)
        elif pnl_per_share < 0:
            losses += 1
            loss_pnls.append(trade_pnl)
            biggest_loss = min(biggest_loss, trade_pnl)
        else:
            flat += 1

        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_drawdown = max(max_drawdown, dd)

        exit_day = actual.get("exit_day")
        day_label = f"T+{exit_day}" if exit_day else "—"
        dc = actual.get("direction_correct")
        dc_str = "-" if outcome == "no_entry" else ("Y" if dc else "N")

        sign = "+" if trade_pnl >= 0 else ""
        print(f"  {date:<12} {direction:>5} {entry:>8.2f} {exit_price:>8.2f} "
              f"{day_label:>5} {dc_str:>2} {outcome:<12} {pnl_per_share:>+7.2f} {shares:>6} "
              f"{sign}{trade_pnl:>7.2f} {balance:>12,.2f}")

    print(f"\n  {'─' * (W - 4)}")
    print(f"  RESULTS")
    print(f"  {'─' * (W - 4)}")

    entered = len(trades) - outcomes["no_entry"]
    win_rate = wins / entered * 100 if entered else 0
    print(f"  Win rate:     {win_rate:.0f}%  ({wins}W / {losses}L"
          + (f" / {flat}F" if flat else "") + f" of {entered} entered)")
    if outcomes["no_entry"]:
        print(f"  No entry:     {outcomes['no_entry']} (T+1 didn't reach entry price)")
    print()
    print(f"  By outcome:   TP={outcomes['take_profit']}  "
          f"SL={outcomes['stop_loss']}  "
          f"MaxDays={outcomes['max_days']}"
          + (f"  Ambiguous={outcomes['ambiguous']}" if outcomes['ambiguous'] else "")
          + (f"  NoEntry={outcomes['no_entry']}" if outcomes['no_entry'] else ""))
    print()

    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    print(f"  Avg win:      ${avg_win:+,.2f}")
    print(f"  Avg loss:     ${avg_loss:+,.2f}")
    print(f"  Biggest win:  ${biggest_win:+,.2f}")
    print(f"  Biggest loss: ${biggest_loss:+,.2f}")
    if avg_loss != 0 and entered > 0:
        expectancy = avg_win * (wins / entered) + avg_loss * (losses / entered)
        print(f"  Expectancy:   ${expectancy:+,.2f} per trade")
    print()

    # Direction / MAE / MFE analysis
    mae_list, mfe_list, sl_dist_list, tp_dist_list = [], [], [], []
    whipsawed = 0
    dir_correct_count = 0
    for t in trades:
        res = t.get("actual", {}).get("result", {})
        out = res.get("outcome")
        if out in ("no_entry", "no_data", None):
            continue
        ep = res.get("actual_entry") or float(t.get("entry", 0) or 0)
        sl = float(t.get("stop_loss", 0) or 0)
        tp = float(t.get("take_profit", 0) or 0)
        mae_list.append(res.get("mae") or 0)
        mfe_list.append(res.get("mfe") or 0)
        if ep > 0:
            sl_dist_list.append(abs(ep - sl))
            tp_dist_list.append(abs(tp - ep))
        dc = res.get("direction_correct", False)
        if dc:
            dir_correct_count += 1
        if dc and out == "stop_loss":
            whipsawed += 1

    if entered:
        dir_pct = dir_correct_count / entered * 100
        avg_mae = sum(mae_list) / len(mae_list) if mae_list else 0
        avg_mfe = sum(mfe_list) / len(mfe_list) if mfe_list else 0
        avg_sl_dist = sum(sl_dist_list) / len(sl_dist_list) if sl_dist_list else 0
        avg_tp_dist = sum(tp_dist_list) / len(tp_dist_list) if tp_dist_list else 0
        print(f"  Dir correct:  {dir_correct_count}/{entered} ({dir_pct:.0f}%)")
        print(f"  Whipsawed:    {whipsawed} (right direction, SL hit)")
        print(f"  Avg MAE:      ${avg_mae:.2f}  vs avg SL dist ${avg_sl_dist:.2f}")
        print(f"  Avg MFE:      ${avg_mfe:.2f}  vs avg TP dist ${avg_tp_dist:.2f}")
        print()
    else:
        dir_correct_count = whipsawed = 0
        dir_pct = avg_mae = avg_mfe = avg_sl_dist = avg_tp_dist = 0.0

    ret = (balance - capital) / capital * 100
    print(f"  Starting:     ${capital:>12,.2f}")
    print(f"  Ending:       ${balance:>12,.2f}")
    print(f"  Net P&L:      ${total_pnl_dollar:>+12,.2f}")
    print(f"  Return:       {ret:>+11.2f}%")
    print(f"  Max drawdown: {max_drawdown:>11.2f}%")

    # Concurrent positions / capital utilisation
    trading_days: set[str] = set()
    for t in trades:
        actual = t.get("actual", {})
        for day in actual.get("daily_data", []):
            trading_days.add(day["date"])
        res = actual.get("result", {})
        if res.get("entry_date"):
            trading_days.add(res["entry_date"])

    if trading_days:
        open_intervals = []
        for t in trades:
            res = t.get("actual", {}).get("result", {})
            if res.get("outcome") in ("no_entry", "no_data", None, "unknown"):
                continue
            ed, xd = res.get("entry_date", ""), res.get("exit_date", "")
            if ed and xd:
                open_intervals.append((ed, xd))

        daily_counts = {}
        for d in sorted(trading_days):
            n = sum(1 for ed, xd in open_intervals if ed <= d <= xd)
            if n > 0:
                daily_counts[d] = n

        if daily_counts:
            avg_conc = sum(daily_counts.values()) / len(daily_counts)
            max_conc = max(daily_counts.values())
            avg_deployed = avg_conc * per_trade
            max_deployed = max_conc * per_trade
            ret_on_deployed = (total_pnl_dollar / avg_deployed * 100) if avg_deployed else 0
            print()
            print(f"  Avg concurrent positions: {avg_conc:.1f}  (max {max_conc})")
            print(f"  Avg deployed capital:     ${avg_deployed:>10,.2f}  (max ${max_deployed:,.2f})")
            print(f"  Return on deployed:       {ret_on_deployed:>+10.2f}%")

    print(f"{'=' * W}")

    summary = {
        "ticker": ticker,
        "model": model,
        "lookback": lookback,
        "capital": capital,
        "per_trade": per_trade,
        "total_trades": len(trades),
        "entered": entered,
        "no_entry": outcomes["no_entry"],
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
        "direction_correct": dir_correct_count,
        "direction_correct_pct": round(dir_pct, 1) if entered else 0,
        "whipsawed": whipsawed,
        "avg_mae": round(avg_mae, 2) if entered else 0,
        "avg_mfe": round(avg_mfe, 2) if entered else 0,
        "avg_sl_dist": round(avg_sl_dist, 2) if entered else 0,
        "avg_tp_dist": round(avg_tp_dist, 2) if entered else 0,
        "trades": trades,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n  Summary saved to {summary_path}")


# ── Main ────────────────────────────────────────────────────────────────

def _find_csv_path(ticker: str) -> Path | None:
    for d in ["data/nse", "data/sp"]:
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
    p.add_argument("--model", default=MODELS[0],
                   help=f"Model (default: {MODELS[0]})")
    p.add_argument("--output", default="results/backtest",
                   help="Output root directory")
    p.add_argument("--capital", type=float, default=100000,
                   help="Starting capital (default: 100000)")
    p.add_argument("--per-trade", type=float, default=100,
                   help="Dollar amount allocated per trade (default: 100)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help=f"Max parallel OpenRouter calls (default: {DEFAULT_CONCURRENCY})")
    args = p.parse_args()

    # Setup debug logging: DEBUG=1 in env enables verbose output
    level = logging.DEBUG if os.environ.get("DEBUG") == "1" else logging.WARNING
    logging.basicConfig(
        level=level,
        format="  [DEBUG %(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

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

    # Run pipeline
    output_dir = Path(args.output) / ticker
    output_dir.mkdir(parents=True, exist_ok=True)

    trades = asyncio.run(
        run_pipeline(ticker, dates, args.lookback, args.model, output_dir, df,
                     concurrency=args.concurrency)
    )

    # Report
    _print_report(trades, ticker, args.model, args.lookback,
                  args.capital, args.per_trade, output_dir)


def report_main():
    """Re-evaluate existing results and print report (no LLM, no browser needed)."""
    p = argparse.ArgumentParser(
        prog="report",
        description="Re-evaluate existing backtest results and print report",
    )
    p.add_argument("ticker", help="Ticker symbol (e.g. NVDA, AAPL)")
    p.add_argument("--start", "-s", help="Start date filter (YYYY-MM-DD)")
    p.add_argument("--end", "-e", help="End date filter (YYYY-MM-DD)")
    p.add_argument("--output", default="results/backtest", help="Output root directory")
    p.add_argument("--capital", type=float, default=100000)
    p.add_argument("--per-trade", type=float, default=100)
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="  [DEBUG %(asctime)s] %(message)s", datefmt="%H:%M:%S")

    ticker = args.ticker.upper()
    csv_path = _find_csv_path(ticker)
    if csv_path is None:
        print(f"No CSV found for {ticker}", file=sys.stderr)
        sys.exit(1)
    df = load_csv(csv_path)

    output_dir = Path(args.output)
    print(f"Reprocessing {ticker} results from {output_dir / ticker}...")
    trades = reprocess_results(ticker, output_dir, df, args.start, args.end)

    if not trades:
        print("No trades found.")
        return

    model = next((t.get("model") for t in trades if t.get("model")), "unknown")
    lookback = next((t.get("lookback") for t in trades if t.get("lookback")), "6M")
    _print_report(trades, ticker, model, lookback, args.capital, args.per_trade,
                  output_dir / ticker)


if __name__ == "__main__":
    import pandas as pd
    main()
