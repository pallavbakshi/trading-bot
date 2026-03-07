"""CLI sidecar for controlling the trading chart web app.

Usage:
  tb ticker AAPL
  tb gtd 2024-01-15
  tb lookback 6M
  tb lookforward 3M
  tb vdr -s 2024-01-01 -e 2024-06-01
  tb interval D
  tb toggle sma on
  tb toggle trading-days off
  tb layer sr on
  tb snapshot --dir ./out
  tb zoom 120
  tb chat "What patterns do you see?" --image snap.png
  tb chat "analyze" --image snap.png --attachment data.csv
  tb chat "follow up" --continue my-session
  tb chat "try another model" --continue my-session --model openai/gpt-5.4
  tb chat "fork it" --continue my-session --fork new-branch
  tb chat --id my-session "first question"
  tb state
  tb chats
  tb chatlog <chat_id>
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


API = "http://127.0.0.1:8000/api/command"


def send(cmd: dict):
    data = json.dumps(cmd).encode()
    req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"✓ {cmd['action']}", end="")
                for k, v in cmd.items():
                    if k != "action":
                        print(f" {k}={v}", end="")
                print()
    except Exception as e:
        print(f"✗ Failed: {e}", file=sys.stderr)
        sys.exit(1)


def _get_state() -> dict:
    """Fetch current chart state from server."""
    url = "http://127.0.0.1:8000/api/state"
    try:
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read()) or {}
    except Exception:
        return {}


def _wait_for_state(key: str, expected, timeout: float = 5.0) -> bool:
    """Poll until chart state[key] matches expected value."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = _get_state()
        if state.get(key) == expected:
            return True
        time.sleep(0.2)
    return False


def _poll_snapshot(timeout: float = 10.0):
    """Poll server until snapshot files are saved."""
    url = "http://127.0.0.1:8000/api/snapshot/result"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url) as resp:
                result = json.loads(resp.read())
                if result and result.get("ok"):
                    print(f"  PNG: {result['png']}")
                    print(f"  CSV: {result['csv']}")
                    return result
                elif result and not result.get("ok"):
                    print(f"Snapshot failed: {result.get('error')}", file=sys.stderr)
                    return result
        except Exception:
            pass
        time.sleep(0.3)
    print("Snapshot timed out", file=sys.stderr)
    return None


def main():
    p = argparse.ArgumentParser(prog="chart", description="Control the trading chart from the CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # ticker
    t = sub.add_parser("ticker", help="Change ticker")
    t.add_argument("symbol", help="Ticker symbol (e.g. AAPL, RELIANCE)")

    # gtd (go to date)
    g = sub.add_parser("gtd", help="Go to date (move NOW line)")
    g.add_argument("value", help="Date in YYYY-MM-DD format")

    # lookback
    lb = sub.add_parser("lookback", help="Lookback from NOW")
    lb.add_argument("preset", choices=["3M", "6M", "9M", "1Y", "2Y", "3m", "6m", "9m", "1y", "2y"],
                     help="Lookback preset")

    # lookforward
    lf = sub.add_parser("lookforward", help="Lookforward from NOW")
    lf.add_argument("preset", choices=["3M", "6M", "9M", "1Y", "2Y", "3m", "6m", "9m", "1y", "2y"],
                     help="Lookforward preset")

    # vdr (visible date range)
    vdr = sub.add_parser("vdr", help="Set visible date range")
    vdr.add_argument("--start", "-s", required=True, help="Start date (YYYY-MM-DD)")
    vdr.add_argument("--end", "-e", required=True, help="End date (YYYY-MM-DD)")

    # interval
    i = sub.add_parser("interval", help="Set interval (D/W/M)")
    i.add_argument("value", choices=["D", "W", "M", "d", "w", "m"], help="D=daily, W=weekly, M=monthly")

    # toggle
    tg = sub.add_parser("toggle", help="Toggle chart overlays on/off")
    tg.add_argument("key", choices=["trading-days", "vol-profile", "sma", "rsi", "avwap"],
                     help="Feature to toggle")
    tg.add_argument("state", choices=["on", "off"], help="on or off")

    # layer
    ly = sub.add_parser("layer", help="Toggle signal layers on/off")
    ly.add_argument("key", choices=["sr", "geometric", "crosses", "bb_squeeze", "vol_climax", "divergences", "gaps"],
                     help="Layer key")
    ly.add_argument("state", choices=["on", "off"], help="on or off")

    # snapshot
    sn = sub.add_parser("snapshot", help="Take PNG + CSV snapshot")
    sn.add_argument("--dir", default=".", help="Directory to save files (default: current dir)")

    # zoom
    z = sub.add_parser("zoom", help="Set number of visible bars")
    z.add_argument("bars", type=int, help="Number of bars to show")

    # chat
    ch = sub.add_parser("chat", help="Send a message to OpenRouter")
    ch.add_argument("text", nargs="?", default="", help="Message text")
    ch.add_argument("--image", help="Path to image file (PNG/JPG)")
    ch.add_argument("--attachment", help="Path to file to attach as text (CSV, TXT, etc.)")
    ch.add_argument("--model", default=None, help="Model to use (can change per turn)")
    ch.add_argument("--id", dest="chat_id", help="Assign a custom chat ID")
    ch.add_argument("--continue", dest="continue_id", metavar="CHAT_ID",
                     help="Continue an existing chat")
    ch.add_argument("--fork", dest="fork_id", metavar="NEW_ID",
                     help="Fork from --continue chat into a new chat ID")

    # chats (list)
    sub.add_parser("chats", help="List saved chats")

    # state
    sub.add_parser("state", help="Show current chart state")

    # chatlog
    cl = sub.add_parser("chatlog", help="Print chat history")
    cl.add_argument("chat_id", help="Chat ID to display")

    # backtest
    bt = sub.add_parser("backtest", help="Run automated backtest across dates")
    bt.add_argument("ticker", help="Ticker symbol (e.g. NVDA, AAPL)")
    bt.add_argument("--start", "-s", help="Start date (default: first in data)")
    bt.add_argument("--end", "-e", help="End date (default: last in data)")
    bt.add_argument("--every", type=int, metavar="N",
                     help="Every Nth trading day (default: every day)")
    bt.add_argument("--lookback", default="6M",
                     choices=["3M", "6M", "9M", "1Y", "2Y"])
    bt.add_argument("--model", default=None,
                     help="Model (default: google/gemini-3.1-pro-preview)")
    bt.add_argument("--output", default="results/backtest",
                     help="Output root directory")
    bt.add_argument("--capital", type=float, default=100000,
                     help="Starting capital (default: 100000)")
    bt.add_argument("--per-trade", type=float, default=100,
                     help="Dollars allocated per trade (default: 100)")

    args = p.parse_args()
    cmd_name = args.command

    if cmd_name == "ticker":
        send({"action": "ticker", "value": args.symbol})
        _wait_for_state("ticker", args.symbol.upper())
    elif cmd_name == "gtd":
        send({"action": "gtd", "value": args.value})
        _wait_for_state("date", args.value)
    elif cmd_name == "lookback":
        preset = args.preset.upper()
        day_map = {"3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504}
        send({"action": "lookback", "value": preset})
        _wait_for_state("lookback", day_map.get(preset))
    elif cmd_name == "lookforward":
        preset = args.preset.upper()
        day_map = {"3M": 63, "6M": 126, "9M": 189, "1Y": 252, "2Y": 504}
        send({"action": "lookforward", "value": preset})
        _wait_for_state("lookforward", day_map.get(preset))
    elif cmd_name == "vdr":
        send({"action": "vdr", "start": args.start, "end": args.end})
    elif cmd_name == "interval":
        send({"action": "interval", "value": args.value.upper()})
    elif cmd_name == "toggle":
        send({"action": "toggle", "key": args.key, "value": args.state})
    elif cmd_name == "layer":
        send({"action": "layer", "key": args.key, "value": args.state})
    elif cmd_name == "snapshot":
        save_dir = str(Path(args.dir).resolve())
        send({"action": "snapshot", "save_dir": save_dir})
        _poll_snapshot()
    elif cmd_name == "zoom":
        send({"action": "zoom", "value": args.bars})

    elif cmd_name == "chat":
        from src.openrouter import Chat, MODELS

        if args.continue_id:
            chat = Chat.load(args.continue_id)
            if args.fork_id:
                chat = chat.fork(args.fork_id, model=args.model)
                print(f"Forked {args.continue_id} -> {args.fork_id}")
        else:
            if args.fork_id:
                print("--fork requires --continue", file=sys.stderr)
                sys.exit(1)
            model = args.model or MODELS[0]
            chat = Chat(model=model, chat_id=args.chat_id)

        if not args.text and not args.image and not args.attachment:
            print("Provide text, --image, or --attachment", file=sys.stderr)
            sys.exit(1)

        # Read attachment file as text
        attachment_text = None
        if args.attachment:
            attachment_text = Path(args.attachment).read_text()

        use_model = args.model or chat.model
        print(f"Chat {chat.chat_id} ({use_model})")
        reply = chat.send(
            text=args.text,
            image_path=args.image,
            attachment_text=attachment_text,
            model=args.model,
        )
        print(f"\n{reply}")

    elif cmd_name == "chats":
        from src.openrouter import Chat
        chats = Chat.list_chats()
        if not chats:
            print("No saved chats.")
        else:
            for c in chats:
                print(f"  {c['chat_id']}  {c['model']:<45} {c['message_count']:>3} msgs  {c['created_at']}")

    elif cmd_name == "state":
        url = "http://127.0.0.1:8000/api/state"
        with urllib.request.urlopen(url) as resp:
            state = json.loads(resp.read())
        if not state:
            print("No state available (is the chart open?)")
        else:
            print(f"  Ticker:       {state.get('ticker')}")
            print(f"  Interval:     {state.get('interval')}")
            print(f"  Date (NOW):   {state.get('date')}")
            vdr = state.get('vdr', ['', ''])
            print(f"  VDR:          {vdr[0]} to {vdr[1]}")
            print(f"  Visible bars: {state.get('visible_bars')} / {state.get('total_bars')}")
            lb = state.get('lookback')
            lf = state.get('lookforward')
            if lb: print(f"  Lookback:     {lb} days")
            if lf: print(f"  Lookforward:  {lf} days")
            print(f"  Trading days: {'on' if state.get('trading_days') else 'off'}")
            print(f"  Vol profile:  {'on' if state.get('volume_profile') else 'off'}")
            print(f"  SMA 50/200:   {'on' if state.get('sma') else 'off'}")
            print(f"  RSI:          {'on' if state.get('rsi') else 'off'}")
            print(f"  AVWAP:        {'on' if state.get('avwap') else 'off'}")

    elif cmd_name == "chatlog":
        from src.openrouter import Chat
        chat = Chat.load(args.chat_id)
        chat.print_history()

    elif cmd_name == "backtest":
        from src.backtest import main as backtest_main
        # Forward args to backtest module by rebuilding sys.argv
        argv = ["backtest", args.ticker]
        if args.every:
            argv.extend(["--every", str(args.every)])
        if args.start:
            argv.extend(["--start", args.start])
        if args.end:
            argv.extend(["--end", args.end])
        if args.lookback:
            argv.extend(["--lookback", args.lookback])
        if args.model:
            argv.extend(["--model", args.model])
        if args.output != "results/backtest":
            argv.extend(["--output", args.output])
        if args.capital != 100000:
            argv.extend(["--capital", str(args.capital)])
        if args.per_trade != 100:
            argv.extend(["--per-trade", str(args.per_trade)])
        sys.argv = argv
        backtest_main()


if __name__ == "__main__":
    main()
