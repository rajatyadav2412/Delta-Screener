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
import concurrent.futures
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
    "last_scan_utc": None,
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


def check_and_auto_scan():
    """
    Checks if a 4H candle has closed since the last scan.
    If so, executes run_full_scan() so all clients receive updated instruments automatically.
    """
    global scanner_state
    if scanner_state["is_scanning"]:
        return

    now_utc = datetime.now(timezone.utc)
    running_start_hour = (now_utc.hour // 4) * 4
    last_closed_candle_time = now_utc.replace(hour=running_start_hour, minute=0, second=0, microsecond=0)

    # 10-second buffer after close to let exchange finalize candles
    if (now_utc - last_closed_candle_time).total_seconds() >= 10:
        last_scan_utc = scanner_state.get("last_scan_utc")
        if last_scan_utc is None or last_scan_utc < last_closed_candle_time:
            run_full_scan()


def run_full_scan():
    """Scans all perpetual contracts concurrently, saves any new signals into SQLite."""
    global scanner_state
    if scanner_state["is_scanning"]:
        return

    scanner_state["is_scanning"] = True

    try:
        instruments = client.fetch_instruments(contract_types=["perpetual_futures"])
        max_inst = config.get("max_instruments", 250)
        instruments = instruments[:max_inst]

        new_count = 0
        scanned_count = len(instruments)

        def scan_single_instrument(inst):
            sym = inst["symbol"]
            try:
                ohlc = client.fetch_candles_paginated(sym, resolution="4h", num_candles=150)
                if ohlc.empty or len(ohlc) < 60:
                    return None

                sig = detector.has_recent_signal(ohlc, lookback_bars=2)
                if sig is not None:
                    curr_price = float(ohlc["close"].iloc[-1])
                    return sym, sig, curr_price
                return None
            except Exception:
                return None

        # Run concurrent scan with 35 workers (~3 seconds total across 237 instruments)
        with concurrent.futures.ThreadPoolExecutor(max_workers=35) as executor:
            recent_alerts = [r for r in executor.map(scan_single_instrument, instruments) if r is not None]

        signals_to_save = []
        for sym, sig, curr_price in recent_alerts:
            choch = sig.choch_event
            bos = sig.bos_event
            signals_to_save.append({
                "symbol": sym,
                "signal_type": "BULLISH_CHOCH" if choch.direction == 1 else "BEARISH_CHOCH",
                "direction": choch.direction,
                "candle_time": str(choch.time),
                "broken_pivot_label": choch.broken_pivot.label,
                "broken_wick_level": choch.broken_pivot.candle_high if choch.direction == 1 else choch.broken_pivot.candle_low,
                "macro_anchor_label": choch.macro_anchor.label if choch.macro_anchor else "",
                "macro_anchor_price": choch.macro_anchor.price if choch.macro_anchor else 0.0,
                "bos_type": f"BOS {bos.broken_pivot.label}",
                "bos_time": str(bos.time),
                "price_at_signal": choch.candle_close,
                "current_price": curr_price,
                "bars_gap": sig.bars_between,
                "timeframe": "4h",
            })

        new_count = database.save_signals_batch(signals_to_save)


        now_ist = datetime.now(IST).strftime("%d %b, %I:%M:%S %p IST")
        scanner_state["last_scan_time"] = now_ist
        scanner_state["last_scan_utc"] = datetime.now(timezone.utc)
        scanner_state["last_scan_count"] = scanned_count
        scanner_state["last_new_alerts"] = new_count
        database.set_metadata("last_scan_time", now_ist)

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


@app.route("/sw.js")
def service_worker():
    return app.send_static_file("sw.js"), 200, {"Content-Type": "application/javascript"}


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json"), 200, {"Content-Type": "application/json"}


@app.route("/api/signals")
def get_signals():
    check_and_auto_scan()
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
    check_and_auto_scan()
    stats = database.get_stats()
    secs_left, next_close = get_seconds_until_next_4h_close()
    return jsonify({
        **stats,
        "is_scanning": scanner_state["is_scanning"],
        "last_scan_time": scanner_state["last_scan_time"] or stats.get("last_scan_time"),
        "next_close_time": next_close.strftime("%I:%M %p IST"),
        "next_close_seconds": secs_left,
    })


@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    if scanner_state["is_scanning"]:
        return jsonify({
            "status": "already_scanning",
            "message": "Scan already in progress.",
            "last_scan_time": scanner_state["last_scan_time"] or database.get_stats().get("last_scan_time"),
            "signals": database.get_history(limit=200),
        })

    # Execute concurrent scan across all Delta perpetuals (~3.5 seconds)
    run_full_scan()
    now_time = scanner_state["last_scan_time"] or database.get_stats().get("last_scan_time")
    fresh_signals = database.get_history(limit=200)
    return jsonify({
        "status": "completed",
        "message": "Live scan completed across all Delta Exchange perpetuals.",
        "last_scan_time": now_time,
        "scanned_count": scanner_state["last_scan_count"],
        "new_alerts": scanner_state["last_new_alerts"],
        "signals": fresh_signals,
    })


_candle_cache = {}


@app.route("/api/candles/<symbol>")
def get_candles(symbol):
    """Returns 4H OHLC candle data for the interactive chart modal with in-memory caching."""
    sym = symbol.upper()
    now_ts = time.time()

    # Serve from cache if fresh (valid for 90 seconds)
    if sym in _candle_cache:
        exp, cached_data = _candle_cache[sym]
        if now_ts < exp:
            return jsonify(cached_data)

    try:
        ohlc = client.fetch_candles_paginated(sym, resolution="4h", num_candles=300)
        if ohlc.empty:
            return jsonify({"symbol": sym, "candles": []})

        candles = []
        for row in ohlc.to_dict(orient="records"):
            t = row.get("time")
            t_sec = int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
            candles.append({
                "time": t_sec,
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
            })

        resp_data = {"symbol": sym, "candles": candles}
        _candle_cache[sym] = (now_ts + 90, resp_data)
        return jsonify(resp_data)
    except Exception as e:
        return jsonify({"error": str(e), "candles": []}), 500


# Start scheduler thread (only for local continuous background scanning, skip on Vercel serverless)
if not os.environ.get("VERCEL"):
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()

if __name__ == "__main__":
    import pandas as pd
    app.run(host="0.0.0.0", port=5001, debug=False)
