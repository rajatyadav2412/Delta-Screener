"""
Delta Exchange BOS → Clean CHoCH Alert System
================================================
Length: 3 candles left / 3 candles right (leftLen=3, rightLen=3)
Macro Rule: Only alert when HH > 10-Day High (Bearish CHoCH)
            or LL < 10-Day Low (Bullish CHoCH).

Synchronized to 4H Candle Close:
  - 4H candles close at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.
  - The scanner waits for the exact candle close (+15s buffer), then scans
    and alerts you immediately.

Usage:
    python3 scanner.py                     # Runs on every 4H candle close
    python3 scanner.py --now               # Run an immediate scan right now
    python3 scanner.py --now --lookback 2  # Check the last 2 closed 4H candles
"""

import json
import time
import argparse
import subprocess
import sys
import os
import concurrent.futures
from datetime import datetime, timezone, timedelta

from colorama import init, Fore, Style
from tabulate import tabulate

from delta_client import DeltaExchangeClient
from structure_detector import MarketStructureDetector, CHoCHAfterBOS
import database

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Initialize colorama for colored terminal output
init(autoreset=True)

# Track already-notified signals so we don't duplicate
_notified_signals: set[str] = set()


def load_config(config_path: str = "config.json") -> dict:
    """Load configuration from JSON file."""
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"{Fore.YELLOW}[WARN] Config file '{config_path}' not found. Using defaults.{Style.RESET_ALL}")
        return {}
    except json.JSONDecodeError as e:
        print(f"{Fore.RED}[ERROR] Invalid config JSON: {e}{Style.RESET_ALL}")
        sys.exit(1)


def send_notification(title: str, message: str, sound: bool = True):
    """
    Send a macOS desktop notification.
    Falls back to terminal bell if osascript fails.
    """
    try:
        sound_str = 'sound name "Glass"' if sound else ""
        clean_msg = message.replace('"', "'")
        clean_title = title.replace('"', "'")
        script = f'display notification "{clean_msg}" with title "{clean_title}" {sound_str}'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
    except Exception:
        print("\a")  # Terminal bell fallback


def print_banner():
    """Print the scanner banner."""
    banner = f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   {Fore.WHITE}Delta Exchange — Clean CHoCH after BOS Alert System{Fore.CYAN}           ║
║   {Fore.YELLOW}Pivots: 3 Bars | Macro Filter: 10-Day High / Low Rule{Fore.CYAN}          ║
║   {Fore.YELLOW}Synchronized to 4H Candle Closes (UTC){Fore.CYAN}                        ║
║                                                                  ║
║   {Fore.GREEN}Bearish CHoCH: HH > 10D High -> Close below HL Low Wick{Fore.CYAN}        ║
║   {Fore.GREEN}Bullish CHoCH: LL < 10D Low  -> Close above LH High Wick{Fore.CYAN}       ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
"""
    print(banner)


def get_seconds_until_next_4h_close() -> tuple[int, datetime]:
    """
    Calculates seconds remaining until the next 4H candle closes in IST.
    4H candles close at: 05:30 AM, 09:30 AM, 01:30 PM, 05:30 PM, 09:30 PM, 01:30 AM IST.
    Adds a 15-second buffer to ensure the exchange has finalized the bar.
    """
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour

    next_hour = ((current_hour // 4) + 1) * 4
    if next_hour >= 24:
        next_close = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_close = now_utc.replace(hour=next_hour, minute=0, second=0, microsecond=0)

    target_time = next_close + timedelta(seconds=15)
    seconds_left = max(1, int((target_time - now_utc).total_seconds()))

    return seconds_left, next_close.astimezone(IST)


def make_signal_key(symbol: str, signal: CHoCHAfterBOS) -> str:
    """Create a unique key for a signal to avoid duplicate notifications."""
    choch = signal.choch_event
    return f"{symbol}|{choch.direction}|{choch.bar_index}|{choch.level}|{choch.time}"


def format_ist_str(t) -> str:
    """Format any timestamp into clean IST format."""
    if t is None or t == "N/A":
        return "N/A"
    try:
        import pandas as pd
        dt = pd.to_datetime(t)
        if dt.tz is None:
            dt = dt.tz_localize("UTC").tz_convert(IST)
        else:
            dt = dt.tz_convert(IST)
        return dt.strftime("%d-%b %I:%M %p")
    except Exception:
        return str(t)[:16]


def format_signal_row(symbol: str, signal: CHoCHAfterBOS, current_price: float) -> list:
    """Format a signal into a clean table row in IST."""
    bos = signal.bos_event
    choch = signal.choch_event

    bos_label = bos.broken_pivot.label if bos.broken_pivot else "SW"
    choch_label = choch.broken_pivot.label if choch.broken_pivot else "SW"

    bos_dir = f"{Fore.GREEN}BULL ↑{Style.RESET_ALL}" if bos.direction == 1 else f"{Fore.RED}BEAR ↓{Style.RESET_ALL}"
    choch_dir = f"{Fore.GREEN}BULL ↑{Style.RESET_ALL}" if choch.direction == 1 else f"{Fore.RED}BEAR ↓{Style.RESET_ALL}"

    clean_str = f"{Fore.GREEN}✓ CLEAN{Style.RESET_ALL}" if choch.is_clean else f"{Fore.YELLOW}✗ WICK{Style.RESET_ALL}"

    bos_time = format_ist_str(bos.time)
    choch_time = format_ist_str(choch.time)

    sw = choch.broken_pivot
    if sw:
        if choch.direction == -1:
            wick_info = f"HL Low Wick: {sw.candle_low:.4f}"
        else:
            wick_info = f"LH High Wick: {sw.candle_high:.4f}"
    else:
        wick_info = "N/A"

    anchor = choch.macro_anchor
    if anchor:
        if anchor.label == "HH":
            anchor_info = f"HH > 10D High ({anchor.price:.4f})"
        else:
            anchor_info = f"LL < 10D Low ({anchor.price:.4f})"
    else:
        anchor_info = "N/A"

    return [
        f"{Fore.WHITE}{symbol}{Style.RESET_ALL}",
        f"BOS {bos_dir} ({bos_label})",
        bos_time,
        f"{Fore.YELLOW}{anchor_info}{Style.RESET_ALL}",
        f"CHoCH {choch_dir} ({choch_label})",
        wick_info,
        choch_time,
        clean_str,
        f"{current_price:.4f}",
    ]


def scan_instruments(
    client: DeltaExchangeClient,
    detector: MarketStructureDetector,
    config: dict,
) -> list:
    """
    Scan instruments for clean CHoCH-after-BOS setups with the 10-day filter.
    """
    timeframe = "4h"  # Strictly locked to 4H timeframe only
    contract_types = config.get("contract_types", ["perpetual_futures"])
    max_instruments = config.get("max_instruments", 250)
    lookback_bars = config.get("lookback_bars", 2)

    # Step 1: Fetch active instruments
    print(f"\n{Fore.CYAN}[1/3] Fetching live perpetuals from Delta Exchange...{Style.RESET_ALL}")
    instruments = client.fetch_instruments(contract_types=contract_types)

    if not instruments:
        print(f"{Fore.RED}  No instruments returned. Check network or API key.{Style.RESET_ALL}")
        return []

    instruments = instruments[:max_instruments]
    print(f"  Scanning {Fore.WHITE}{len(instruments)}{Style.RESET_ALL} perpetual contracts on {timeframe.upper()}...")

    # Step 2: Analyze each instrument concurrently
    print(f"\n{Fore.CYAN}[2/3] Analyzing structure (length=3) with 10-day High/Low rule (concurrent)...{Style.RESET_ALL}")

    def scan_single(inst):
        symbol = inst.get("symbol", "UNKNOWN")
        try:
            ohlc = client.fetch_candles_paginated(
                symbol=symbol,
                resolution=timeframe,
                num_candles=150,
            )
            if ohlc.empty or len(ohlc) < 60:
                return None

            signal = detector.has_recent_signal(ohlc, lookback_bars=lookback_bars, require_closed=True)
            if signal is not None:
                signal.symbol = symbol
                current_price = float(ohlc["close"].iloc[-1])
                return (symbol, signal, current_price)
            return None
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=35) as executor:
        alerts = [r for r in executor.map(scan_single, instruments) if r is not None]

    print(f"  Completed scan: {Fore.WHITE}{len(instruments)}{Style.RESET_ALL} scanned across 35 workers")
    return alerts


def display_and_notify(alerts: list, scan_time: str):
    """Print results table and trigger desktop notifications for new setups."""
    global _notified_signals

    if not alerts:
        print(f"\n{Fore.YELLOW}  No clean CHoCH-after-BOS setups detected on the closed 4H candle.{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}(A setup requires: Length=3, HH > 10D High or LL < 10D Low, and full wick break.){Style.RESET_ALL}")
        return

    print(f"\n{Fore.CYAN}[3/3] {Fore.GREEN}🚨 VALIDATED CLEAN CHoCH SETUPS FOUND (10D Filter)!{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Evaluation Time: {scan_time}{Style.RESET_ALL}\n")

    headers = [
        "Instrument",
        "BOS Break",
        "BOS Bar Time",
        "10-Day Macro Anchor",
        "CHoCH Reversal",
        "Broken Wick Level",
        "CHoCH Bar Time",
        "Clean Break",
        "Current Price",
    ]

    rows = []
    new_alerts = []

    for symbol, signal, current_price in alerts:
        rows.append(format_signal_row(symbol, signal, current_price))

        choch = signal.choch_event
        bos = signal.bos_event
        database.save_signal(
            symbol=symbol,
            signal_type="BULLISH_CHOCH" if choch.direction == 1 else "BEARISH_CHOCH",
            direction=choch.direction,
            candle_time=str(choch.time),
            broken_pivot_label=choch.broken_pivot.label,
            broken_wick_level=choch.broken_pivot.candle_high if choch.direction == 1 else choch.broken_pivot.candle_low,
            macro_anchor_label=choch.macro_anchor.label if choch.macro_anchor else "",
            macro_anchor_price=choch.macro_anchor.price if choch.macro_anchor else 0.0,
            bos_type=f"BOS {bos.broken_pivot.label}",
            bos_time=str(bos.time),
            price_at_signal=choch.candle_close,
            current_price=current_price,
            bars_gap=signal.bars_between,
            timeframe="4h",
        )

        key = make_signal_key(symbol, signal)
        if key not in _notified_signals:
            _notified_signals.add(key)
            new_alerts.append((symbol, signal, current_price))

    print(tabulate(rows, headers=headers, tablefmt="rounded_grid"))

    # Summary
    bull = sum(1 for _, s, _ in alerts if s.choch_event.direction == 1)
    bear = sum(1 for _, s, _ in alerts if s.choch_event.direction == -1)

    print(f"\n  {Fore.WHITE}Summary:{Style.RESET_ALL}")
    print(f"    {Fore.GREEN}Bullish Reversals (LL < 10D Low -> Broke LH): {bull}{Style.RESET_ALL}")
    print(f"    {Fore.RED}Bearish Reversals (HH > 10D High -> Broke HL): {bear}{Style.RESET_ALL}")

    # Send desktop alerts
    if new_alerts:
        print(f"\n  {Fore.GREEN}📱 Firing {len(new_alerts)} desktop notification(s)...{Style.RESET_ALL}")
        for symbol, signal, price in new_alerts:
            sw = signal.choch_event.broken_pivot
            sw_name = sw.label if sw else "Swing"
            anchor = signal.choch_event.macro_anchor
            anchor_text = f"after {anchor.label} ({anchor.price:.4f})" if anchor else ""

            if signal.choch_event.direction == 1:
                title = f"🟢 BULLISH CHoCH: {symbol}"
                body = f"LL made 10D Low -> Clean close above {sw_name} wick ({sw.candle_high:.4f}). Price: {price}"
            else:
                title = f"🔴 BEARISH CHoCH: {symbol}"
                body = f"HH made 10D High -> Clean close below {sw_name} wick ({sw.candle_low:.4f}). Price: {price}"

            send_notification(title, body)
            time.sleep(0.4)


def run_scanner(config: dict, run_once: bool = False):
    """Main execution loop synchronized to 4H candle closes."""
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "https://api.india.delta.exchange")

    if not api_key:
        print(f"{Fore.RED}[ERROR] API key missing in config.json!{Style.RESET_ALL}")
        sys.exit(1)

    client = DeltaExchangeClient(api_key=api_key, base_url=base_url)
    detector = MarketStructureDetector(
        swing_length=config.get("swing_length", 3),
        macro_lookback_days=config.get("macro_lookback_days", 10),
    )

    scan_num = 0

    while True:
        scan_num += 1
        now_str = datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p IST")

        print(f"\n{'='*70}")
        print(f"{Fore.CYAN}  Scan #{scan_num} | Triggered at {now_str}{Style.RESET_ALL}")
        print(f"{'='*70}")

        try:
            alerts = scan_instruments(client, detector, config)
            display_and_notify(alerts, now_str)
            database.set_metadata("last_scan_time", datetime.now(IST).strftime("%d %b, %I:%M:%S %p IST"))
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}  Scanner interrupted by user.{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"\n{Fore.RED}  [ERROR] Scan failure: {e}{Style.RESET_ALL}")

        if run_once:
            print(f"\n{Fore.CYAN}  Scan completed (run once mode).{Style.RESET_ALL}")
            break

        secs_left, next_close = get_seconds_until_next_4h_close()
        close_str = next_close.strftime("%I:%M %p IST")
        hours_left = secs_left // 3600
        mins_left = (secs_left % 3600) // 60

        print(f"\n{Fore.WHITE}  🕒 Next 4H Candle Close: {Fore.YELLOW}{close_str}{Fore.WHITE} "
              f"(in {hours_left}h {mins_left}m). Sleeping until close... (Ctrl+C to stop){Style.RESET_ALL}")

        try:
            time.sleep(secs_left)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}  Scanner stopped.{Style.RESET_ALL}")
            break


def main():
    parser = argparse.ArgumentParser(description="Delta Exchange 4H Clean CHoCH Alert Scanner")
    parser.add_argument("--now", action="store_true", help="Run a single scan right now and exit")
    parser.add_argument("--once", action="store_true", help="Alias for --now")
    parser.add_argument("--lookback", type=int, default=2, help="Bars back to check for the CHoCH break (default: 2)")
    parser.add_argument("--swing-length", type=int, default=3, help="Override swing length (default: 3)")
    parser.add_argument("--max-instruments", type=int, help="Max instruments to scan (default: 250)")

    args = parser.parse_args()
    config = load_config("config.json")

    if args.swing_length:
        config["swing_length"] = args.swing_length
    if args.max_instruments:
        config["max_instruments"] = args.max_instruments
    config["lookback_bars"] = args.lookback

    print_banner()
    print(f"  Configuration:")
    print(f"    Timeframe:        {Fore.CYAN}4h{Style.RESET_ALL}")
    print(f"    Pivot Length:     {Fore.CYAN}{config.get('swing_length', 3)}{Style.RESET_ALL} (3 bars left / 3 bars right)")
    print(f"    Macro Filter:     {Fore.CYAN}{config.get('macro_lookback_days', 10)}-Day High / Low Rule{Style.RESET_ALL}")
    print(f"    Lookback Bars:    {Fore.CYAN}{config.get('lookback_bars', 2)}{Style.RESET_ALL} (evaluates recently closed 4H candles)")
    print(f"    Max Instruments:  {Fore.CYAN}{config.get('max_instruments', 250)}{Style.RESET_ALL}")
    print(f"    Schedule:         {Fore.YELLOW}Triggers immediately after every 4H candle close{Style.RESET_ALL}")

    run_scanner(config, run_once=(args.now or args.once))


if __name__ == "__main__":
    main()
