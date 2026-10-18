"""
Chartink-style Web Application for Delta Exchange 4H CHoCH Alerts
=================================================================
Provides a web dashboard where users can see historical and live
signals: what instrument triggered, when, at what price, and chart history.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

from delta_client import DeltaExchangeClient
from structure_detector import MarketStructureDetector
import database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

# Load configuration
config_path = os.path.join(BASE_DIR, "config.json")
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = json.load(f)
else:
    config = {
        "api_key": os.environ.get("DELTA_API_KEY", ""),
        "base_url": os.environ.get("DELTA_BASE_URL", "https://api.india.delta.exchange"),
        "timeframe": "4h",
        "swing_length": 3,
        "macro_lookback_days": 10
    }

client = DeltaExchangeClient(api_key=config.get("api_key", ""), base_url=config.get("base_url", "https://api.india.delta.exchange"))
detector = MarketStructureDetector(
    swing_length=config.get("swing_length", 3),
    macro_lookback_days=config.get("macro_lookback_days", 10),
)

# Global scanner state
scanner_state = {
    "is_scanning": False,
    "last_scan_time": None,
    "last_scan_count": 0,
    "last_new_alerts": 0,
    "next_close_time": None,
    "next_close_seconds": 0,
}


def get_seconds_until_next_4h_close() -> tuple[int, datetime]:
    """Calculates seconds until next 4H candle close in IST (05:30 AM, 09:30 AM, 01:30 PM, 05:30 PM, 09:30 PM, 01:30 AM IST)."""
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


def run_full_scan():
    """Scans all perpetual contracts, saves any new signals into SQLite."""
    global scanner_state
    if scanner_state["is_scanning"]:
        return

    scanner_state["is_scanning"] = True
    now_str = datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p IST")

    try:
        instruments = client.fetch_instruments(contract_types=["perpetual_futures"])
        max_inst = config.get("max_instruments", 250)
        instruments = instruments[:max_inst]

        new_count = 0
        scanned_count = 0

        for inst in instruments:
            sym = inst["symbol"]
            scanned_count += 1
            try:
                ohlc = client.fetch_candles_paginated(sym, resolution="4h", num_candles=500)
                if ohlc.empty or len(ohlc) < 70:
                    continue

                res = detector.analyze(ohlc)
                curr_price = float(ohlc["close"].iloc[-1])

                for sig in res["signals"]:
                    choch = sig.choch_event
                    bos = sig.bos_event
                    is_new = database.save_signal(
                        symbol=sym,
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
                        current_price=curr_price,
                        bars_gap=sig.bars_between,
                        timeframe="4h",
                    )
                    if is_new:
                        new_count += 1

                time.sleep(0.08)
            except Exception:
                continue

        scanner_state["last_scan_time"] = now_str
        scanner_state["last_scan_count"] = scanned_count
        scanner_state["last_new_alerts"] = new_count

    finally:
        scanner_state["is_scanning"] = False


def background_scheduler():
    """Continuously waits for 4H candle close, triggers scan, and repeats."""
    while True:
        secs_left, next_close = get_seconds_until_next_4h_close()
        scanner_state["next_close_time"] = next_close.strftime("%I:%M %p IST")
        scanner_state["next_close_seconds"] = secs_left

        time.sleep(secs_left)
        run_full_scan()


# ─────────────────────────────────────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/signals")
def get_signals():
    symbol = request.args.get("symbol", "").strip()
    sig_type = request.args.get("type", "ALL").strip()
    limit = int(request.args.get("limit", 200))
    offset = int(request.args.get("offset", 0))

    signals = database.get_history(
        limit=limit,
        offset=offset,
        symbol_filter=symbol if symbol else None,
        type_filter=sig_type if sig_type != "ALL" else None,
    )
    return jsonify({"signals": signals})


@app.route("/api/stats")
def get_stats():
    stats = database.get_stats()
    secs_left, next_close = get_seconds_until_next_4h_close()
    return jsonify({
        **stats,
        "is_scanning": scanner_state["is_scanning"],
        "last_scan_time": scanner_state["last_scan_time"],
        "next_close_time": next_close.strftime("%I:%M %p IST"),
        "next_close_seconds": secs_left,
    })


@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    if scanner_state["is_scanning"]:
        return jsonify({"status": "already_scanning", "message": "Scan already in progress."})

    t = threading.Thread(target=run_full_scan, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Live scan started across all Delta Exchange perpetuals."})


@app.route("/api/candles/<symbol>")
def get_candles(symbol):
    """Returns 4H OHLC candle data for the interactive chart modal."""
    try:
        ohlc = client.fetch_candles_paginated(symbol.upper(), resolution="4h", num_candles=400)
        if ohlc.empty:
            return jsonify({"candles": []})

        candles = []
        for _, row in ohlc.iterrows():
            candles.append({
                "time": int(pd.to_datetime(row["time"]).timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
        return jsonify({"symbol": symbol.upper(), "candles": candles})
    except Exception as e:
        return jsonify({"error": str(e), "candles": []}), 500


# Start scheduler thread (only for local continuous background scanning, skip on Vercel serverless)
if not os.environ.get("VERCEL"):
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()

if __name__ == "__main__":
    import pandas as pd
    app.run(host="0.0.0.0", port=5001, debug=False)
